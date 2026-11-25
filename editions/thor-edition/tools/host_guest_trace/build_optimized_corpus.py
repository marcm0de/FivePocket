#!/usr/bin/env python3
"""Compile the current private corpus in a separate, strict-FP optimized cache.

Does not regenerate game source, run CMake, replace diagnostic objects, install
anything, or execute the game. Existing Ninja compile commands supply the ABI.
All outputs, commands and provenance remain in the ignored private build tree.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
from pathlib import Path
import re
import shlex
import subprocess
import time

ROOT = Path(__file__).resolve().parents[2]
FLAGS = ['-O2', '-fno-strict-aliasing', '-ffp-model=strict', '-fwrapv']


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--build-dir', type=Path, required=True)
    parser.add_argument('--jobs', type=int, default=3, choices=range(1, 9))
    parser.add_argument('--chunks', help='Explicit comma-separated chunks; default rebuilds all')
    parser.add_argument('--link', action='store_true', help='Verify all cached objects and link a separate SO')
    parser.add_argument('--link-only', action='store_true', help='Verify/link cached objects without recompiling')
    args = parser.parse_args()
    build = args.build_dir.absolute()
    if build != build.resolve() or not build.is_relative_to(ROOT / 'local-analysis'):
        raise ValueError('Build must be an ignored private path without symlinks')
    subprocess.run(['git', 'check-ignore', '--quiet', str(build)], cwd=ROOT, check=True)
    sources = {int(p.name.split('.')[1]): p for p in (build / 'validated-ppc').glob('ppc_recomp.*.cpp')
               if re.fullmatch(r'ppc_recomp\.\d+\.cpp', p.name)}
    if not sources or set(sources) != set(range(len(sources))) or len(sources) > 1024:
        raise ValueError('Unexpected private corpus numbering')
    if args.link_only and (args.chunks is not None or not args.link):
        parser.error('--link-only requires --link and cannot be combined with --chunks')
    chosen = ([] if args.link_only else sorted(sources)) if args.chunks is None else sorted({int(x) for x in args.chunks.split(',')})
    if (not chosen and not args.link_only) or not set(chosen).issubset(sources):
        raise ValueError('Invalid selected chunks')
    cache = build / 'optimized-ppc-strict'
    cache.mkdir(exist_ok=True)
    if cache.is_symlink():
        raise ValueError('Cache must not be a symlink')

    def command(target: str) -> list[str]:
        result = subprocess.run(['ninja', '-t', 'commands', target], cwd=build,
                                capture_output=True, text=True, check=True)
        words = shlex.split(result.stdout.splitlines()[-1])
        if words[:2] == [':', '&&'] and words[-2:] == ['&&', ':']:
            words = words[2:-2]
        if any(w in {'&&', ';', '|', '||'} for w in words) or not words[0].endswith('/clang++'):
            raise ValueError('Unexpected compiler command')
        return words

    def compile_one(index: int) -> None:
        source = sources[index]
        before = digest(source)
        output = cache / f'ppc_recomp.{index}.cpp.o'
        dependency = output.with_suffix('.o.d')
        record_path = output.with_suffix('.o.json')
        words = command(f'CMakeFiles/host_guest_trace.dir/validated-ppc/{source.name}.o')
        if Path(words[words.index('-c') + 1]).resolve() != source.resolve():
            raise ValueError('Cached compiler source differs from selected source')
        for option, value in [('-o', output), ('-MF', dependency), ('-MT', output)]:
            words[words.index(option) + 1] = str(value)
        words.extend(FLAGS)
        started = time.monotonic()
        result = subprocess.run(words, cwd=build, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        output.with_suffix('.o.log').write_bytes(result.stdout)
        if result.returncode:
            raise RuntimeError(f'Chunk {index} failed; see {output.name}.log')
        if digest(source) != before:
            raise RuntimeError(f'Chunk {index} changed during compilation')
        dependencies = shlex.split(dependency.read_text().split(':', 1)[1].replace('\\\n', ' '))
        hashes = {str(Path(p).resolve() if Path(p).is_absolute() else (build / p).resolve()):
                  digest(Path(p) if Path(p).is_absolute() else build / p) for p in dependencies}
        record = dict(chunk=index, source=str(source), source_sha256=before,
                      object_sha256=digest(output), command=words, dependencies=hashes,
                      flags=FLAGS, seconds=round(time.monotonic() - started, 3))
        record_path.write_text(json.dumps(record, indent=2) + '\n')
        print(f'COMPILED {index} {record["seconds"]}s', flush=True)

    with concurrent.futures.ThreadPoolExecutor(max_workers=args.jobs) as pool:
        list(pool.map(compile_one, chosen))
    if not args.link:
        return
    verified = {}
    for index, source in sorted(sources.items()):
        output = cache / f'ppc_recomp.{index}.cpp.o'
        record = json.loads(output.with_suffix('.o.json').read_text())
        if record['flags'] != FLAGS or record['source_sha256'] != digest(source) \
                or record['object_sha256'] != digest(output):
            raise ValueError(f'Chunk {index} is stale')
        for path, expected in record['dependencies'].items():
            if path not in verified:
                verified[path] = digest(Path(path))
            if verified[path] != expected:
                raise ValueError(f'Chunk {index} has a stale dependency: {path}')
    words = command('libhost_guest_trace.so')
    replaced = set()
    for i, word in enumerate(words):
        match = re.fullmatch(r'CMakeFiles/host_guest_trace\.dir/validated-ppc/ppc_recomp\.(\d+)\.cpp\.o', word)
        if match:
            index = int(match[1]); replaced.add(index)
            words[i] = str(cache / f'ppc_recomp.{index}.cpp.o')
    if replaced != set(sources):
        raise ValueError('Link does not cover exactly the current corpus')
    output = build / 'libhost_guest_trace-optimized-corpus.so'
    words[words.index('-o') + 1] = str(output)
    subprocess.run(words, cwd=build, check=True)
    (cache / 'link.json').write_text(json.dumps(dict(command=words, output=str(output),
        sha256=digest(output), chunks=len(replaced), flags=FLAGS), indent=2) + '\n')
    print('LINKED', output.name, digest(output), flush=True)


if __name__ == '__main__':
    main()
