#!/usr/bin/env python3
"""The title update's game library compiled in a builder work folder (the TU compile rehearsal).

The TU27 lane builds its libraries with build_tu27.py, outside any work folder: each object's meta file (obj/<unit>.meta.json)
records its exact command and every file it read with its SHA-256. This tool compiles that recorded build again in a work folder,
every game-derived input taken from this run's stages, and links it. The library must have the attempt's code (elf_sections code
identity of every allocated section), and the work folder can then be frozen for a players' profile (freeze_build.py
--recorded). It links as the players' build does: no debug sections and a fixed build id (default: the attempt's own).

    tu_compile.py --repo DIR --work DIR --attempt local-analysis/.attempts/tu27-a132r --tu-code DIR [--tu-code DIR ...]
                  --kept-manifest FROZEN_1_0_MANIFEST --generated-headers DIR [--jobs N] [--portable-build-id HEX40]

The work folder holds this run's stage outputs: native-packs, code-headers and xma (the 1.0 stages: the TU library reads the same
pack headers as the 1.0 line) and compile/generated-headers (the 1.0 code generation's headers, for the kept units). --tu-code
names the TU code folders in order (the TU code generation, then tu_fp_mode.py's output over it): together they must give, file
for file, the code the attempt compiled. Written under compile/:
  tu-ppc/                          the TU code (APFS clones where the volume allows)
  attempts/<name>/src, origins/<attempt>/src   the attempt's sources and other attempts' files its commands name
  attempts/<name>/build/obj        objects with build_tu27.py-style meta files; build.json, link-command.json, the library
  tu-compile.json                  what was compared
Each unit's command is the recorded one with the attempt's folders moved into the work folder; a runtime unit also gets this
run's decoder-header, code-header and native-pack folders first on its include path, as compile_rehearsal.py puts them for 1.0
attempts. The runtime files the attempt linked from the base build (kept objects) are compiled with the 1.0 frozen manifest's
commands for them, moved into this work folder, and must give the kept objects byte for byte. A unit that reads a file of
local-analysis outside the work folder, other than the few the 1.0 profile supplies by name, is refused.
Exit 0: built and the code is the attempt's; 1: refused or failed.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import elf_sections  # noqa: E402
import freeze_build  # noqa: E402
import xma_extra_unit  # noqa: E402

DECODER_HEADERS = 'local-analysis/native-xma/ffmpeg'
XMA_ARCHIVES = ('libavcodec.a', 'libavutil.a')
# private files the 1.0 profile supplies by name (compile-link inputs private_*), which a unit may read where they are
SUPPLIED_BY_NAME = ('local-analysis/android-runtime-bridge-live-swap/api26/generated_guest_import_bridge.h',
                    'local-analysis/pgo/', 'local-analysis/attempts/v445/lean-include/guest_scalar_memory.h',
                    'local-analysis/layout/sym-v1.txt')


class Refused(Exception):
    pass


_SEEN: dict = {}


def sha(path) -> str:
    """SHA-256 of a file, remembered per path, size and modification time (units share most of their headers)."""
    st = os.stat(path)
    key = (str(path), st.st_size, st.st_mtime_ns)
    if key not in _SEEN:
        h = hashlib.sha256()
        with open(path, 'rb') as f:
            for block in iter(lambda: f.read(1 << 20), b''):
                h.update(block)
        _SEEN[key] = h.hexdigest()
    return _SEEN[key]


def clone(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    if subprocess.run(['cp', '-c', str(src), str(dst)], capture_output=True).returncode:
        shutil.copyfile(src, dst)


def aliases(root: Path, words) -> list[str]:
    """Other spellings of the repository root in the words (a link to it), longest first."""
    found = set()
    for w in words:
        m = re.search(r'(/[^ ]+?)/(local-analysis|tools|android)/', w)
        if m and m.group(1) != str(root) and Path(m.group(1)).resolve() == root.resolve():
            found.add(m.group(1))
    return sorted(found, key=len, reverse=True)


class Mover:
    """Moves the attempt's folders into the work folder, word by word."""

    def __init__(self, root: Path, work: Path, attempt: Path, tu_dir: str, spellings):
        self.root, self.work, self.attempt = root, work, attempt
        self.spellings = list(spellings)
        self.pairs = [(tu_dir, str(work / 'compile/tu-ppc')),
                      (str(attempt / 'src'), str(work / 'compile/attempts' / attempt.name / 'src')),
                      (str(attempt / 'build'), str(work / 'compile/attempts' / attempt.name / 'build')),
                      (str(attempt / attempt_script(attempt)), str(work / 'compile/attempts' / attempt.name / attempt_script(attempt)))]
        self.origins = {}

    def plain(self, word: str) -> str:
        for s in self.spellings:
            word = word.replace(s + '/', str(self.root) + '/')
        return word

    def __call__(self, word: str) -> str:
        word = self.plain(word)
        for old, new in self.pairs:
            word = word.replace(old + '/', new + '/') if (old + '/') in word else (word[:-len(old)] + new if word.endswith(old) else word)
        m = re.search(re.escape(str(self.root)) + r'/local-analysis/\.attempts/([^/]+)/src/', word)
        if m and m.group(1) != self.attempt.name:
            self.origins[m.group(1)] = self.root / 'local-analysis/.attempts' / m.group(1) / 'src'
            word = word.replace(m.group(0), str(self.work / 'compile/origins' / m.group(1) / 'src') + '/')
        return word


def gnu_build_id(path: Path) -> str:
    """The GNU build-id note of an ELF64 little-endian file, as hex."""
    import struct
    d = Path(path).read_bytes()
    shoff, = struct.unpack_from('<Q', d, 0x28)
    es, n, _ = struct.unpack_from('<HHH', d, 0x3A)
    for i in range(n):
        _, typ, _, _, off, size = struct.unpack_from('<IIQQQQ', d, shoff + i * es)[:6]
        if typ != 7:                                    # SHT_NOTE
            continue
        j = off
        while j < off + size:
            nsz, dsz, nt = struct.unpack_from('<III', d, j)
            name, desc = d[j + 12:j + 12 + nsz], d[j + 12 + ((nsz + 3) & ~3):][:dsz]
            if nt == 3 and name.startswith(b'GNU'):
                return desc.hex()
            j += 12 + ((nsz + 3) & ~3) + ((dsz + 3) & ~3)
    raise Refused(f'{Path(path).name} has no GNU build id')


def allocated_differences(a: Path, b: Path) -> list[str]:
    """Names of the allocated (loaded) sections that differ between two libraries: the 1.0 rehearsal's criterion."""
    def table(path):
        return {s['name']: (s['type'], s['flags'], s['size'], s['align'], s['sha256']) for s in elf_sections.sections(path)
                if int(s['flags']) & 2}
    ta, tb = table(a), table(b)
    return sorted(n for n in set(ta) | set(tb) if ta.get(n) != tb.get(n))


def attempt_script(attempt: Path) -> str:
    spec = json.loads((attempt / 'attempt.json').read_text())
    return spec['version_script']


def shadow_words(root: Path, work: Path, command) -> list[str]:
    """This run's decoder-header, code-header and native-pack folders for the private include folders of a command, in the order
    compile_rehearsal.py gives them (decoder headers, then code headers, then packs, each group in the command's order)."""
    prefix = str(root / 'local-analysis') + '/'
    xma, code, packs = [], [], []
    for w in command:
        if not (w.startswith('-I') and w[2:].startswith(prefix)):
            continue
        rel = w[2:][len(prefix):]
        if 'local-analysis/' + rel == DECODER_HEADERS and (work / 'xma').is_dir():
            xma = ['-I' + str(work / 'xma')]
        for group, base in ((code, work / 'code-headers'), (packs, work / 'native-packs')):
            if (base / rel).is_dir() and '-I' + str(base / rel) not in group:
                group.append('-I' + str(base / rel))
    return xma + code + packs


def private_reads(root: Path, work: Path, deps) -> list[str]:
    la = str(root / 'local-analysis') + '/'
    out = []
    for p in deps:
        if p.startswith(la) and not p.startswith(str(work) + '/'):
            rel = p[len(str(root)) + 1:]
            if not any(rel == s or (s.endswith('/') and rel.startswith(s)) for s in SUPPLIED_BY_NAME):
                out.append(rel)
    return out


def reusable(command, obj: Path) -> dict | None:
    """The meta of an object an earlier run of this tool made with this very command, when the object and every file it read are
    unchanged (else None: the unit is compiled)."""
    meta_path = obj.with_name(obj.name[:-2] + '.meta.json')
    if not (meta_path.is_file() and obj.is_file()):
        return None
    meta = json.loads(meta_path.read_text())
    if meta.get('command') != command or sha(obj) != meta.get('object_sha256'):
        return None
    if not all(os.path.isfile(p) and sha(p) == h for p, h in meta['deps'].items()):
        return None
    return meta


def compile_unit(job, cwd: Path, root: Path, work: Path, reuse: bool = False) -> dict:
    command, obj, dep = job['command'], Path(job['object']), Path(job['depfile'])
    obj.parent.mkdir(parents=True, exist_ok=True)
    meta = reusable(command, obj) if reuse else None
    if meta is not None:
        return dict(job, seconds=0.0, reused=True, object_sha256=meta['object_sha256'], private=private_reads(root, work, meta['deps']))
    t0 = time.monotonic()
    res = subprocess.run(command, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    secs = round(time.monotonic() - t0, 1)
    obj.with_name(obj.name[:-2] + '.log').write_bytes(res.stdout)
    if res.returncode:
        return dict(job, failed=True, seconds=secs)
    deps = {p: sha(p) for p in freeze_build.dependency_paths(dep, cwd)}
    for w in command:
        if w.startswith('-fprofile-use='):
            deps[str(Path(w.split('=', 1)[1]).resolve())] = sha(w.split('=', 1)[1])
    digest = sha(obj)
    obj.with_name(obj.name[:-2] + '.meta.json').write_text(json.dumps(dict(command=command, deps=deps, object_sha256=digest)))
    return dict(job, seconds=secs, object_sha256=digest, private=private_reads(root, work, deps))


def build(root: Path, work: Path, attempt: Path, tu_code, kept_manifest: Path, jobs: int, portable_build_id=None,
          generated_headers: Path | None = None, reuse: bool = False) -> dict:
    started = time.monotonic()
    receipt = json.loads((attempt / 'build/build.json').read_text())
    if receipt.get('title_update', {}).get('version') != 27:
        raise Refused('the attempt is not a title update 27 build')
    records = receipt['records']
    metas = {}
    for r in records:
        o = Path(r['object'])
        metas[r['name']] = json.loads(o.with_name(o.name[:-2] + '.meta.json').read_text())
    problems = freeze_build.release_saves_problems([(n, m['command'], list(m['deps'])) for n, m in metas.items()],
                                                   lambda p: Path(p).read_bytes())
    if problems:
        raise Refused('; '.join(problems))
    compile_dir = work / 'compile'
    if compile_dir.exists() and not reuse:
        raise Refused(f'{compile_dir} exists already: a TU compile writes a new one (--reuse keeps its unchanged objects)')
    tu_dir = receipt['tu_ppc_dir']
    # 1. the TU code: each file the attempt compiled from its corpus folder, found by SHA-256 in this run's TU code folders
    wanted = {}
    for m in metas.values():
        for p, h in m['deps'].items():
            if p.startswith(tu_dir + '/'):
                wanted[p[len(tu_dir) + 1:]] = h
    for r in records:
        if r['source'].startswith(tu_dir + '/'):
            wanted.setdefault(r['source'][len(tu_dir) + 1:], metas[r['name']]['deps'].get(r['source']) or sha(r['source']))
    have = {}
    for folder in tu_code:
        for p in sorted(Path(folder).iterdir()):
            if p.is_file():
                have[p.name] = p
    tu_ppc = compile_dir / 'tu-ppc'
    for rel, h in sorted(wanted.items()):
        src = have.get(rel)
        if src is None or sha(src) != h:
            raise Refused(f'the TU code of this run lacks {rel} as the attempt compiled it')
        clone(src, tu_ppc / rel)
    # the 1.0 code generation's headers, which the kept units' 1.0 commands read from compile/generated-headers
    if generated_headers is not None:
        for p in sorted(Path(generated_headers).iterdir()):
            if p.is_file():
                clone(p, compile_dir / 'generated-headers' / p.name)
    # 2. the attempt's sources and the other attempts' files its commands name
    words = [w for m in metas.values() for w in m['command']]
    move = Mover(root, work, attempt, tu_dir, aliases(root, words))
    for p in sorted((attempt / 'src').iterdir()):
        clone(p, compile_dir / 'attempts' / attempt.name / 'src' / p.name)
    clone(attempt / attempt_script(attempt), compile_dir / 'attempts' / attempt.name / attempt_script(attempt))
    out = compile_dir / 'attempts' / attempt.name / 'build'
    jobs_list = []
    for r in records:
        command = [move(w) for w in metas[r['name']]['command']]
        if r['kind'] != 'chunk':
            command[1:1] = shadow_words(root, work, command)
        obj = command[command.index('-o') + 1]
        dep = command[command.index('-MF') + 1]
        jobs_list.append(dict(name=r['name'], kind=r['kind'], command=command, object=obj, depfile=dep,
                              want=metas[r['name']]['object_sha256'], old=r['object'], replaces=r.get('replaces')))
    for name, src in move.origins.items():
        for p in sorted(src.iterdir()):
            clone(p, compile_dir / 'origins' / name / 'src' / p.name)
    # 3. the kept base objects, with the 1.0 frozen manifest's commands moved into this work folder
    link = [move(w) for w in json.loads((attempt / 'build/link-command.json').read_text())]
    compiled = {str(Path(j['old']).resolve()) for j in jobs_list}
    kept_words = [w for w in link if w.endswith('.o') and str(Path(w).resolve()) not in compiled and not w.startswith(str(work))]
    frozen = json.loads(kept_manifest.read_text())
    fwork = frozen['units'][0]['object'].split('/compile/')[0]
    by_name = {u['name']: u for u in frozen['units']}
    clang = Path(jobs_list[0]['command'][0]).with_name('clang')
    for w in kept_words:
        if Path(w).name == xma_extra_unit.OBJECT_NAME:
            # COR-02's sound-decoder object (lead 2026-11-24, option A): compiled here from the changed file with the XMA plan's
            # own command, and it must be the very object the attempt linked
            if sha(w) != xma_extra_unit.OBJECT_SHA256['plan.json']:
                raise Refused(f'{Path(w).name} is not the decoder object the builder can make (08322eee...)')
            unit = xma_extra_unit.prepare(work / 'xma', compile_dir / 'xma-cor02', clang,
                                          lay_out=not (reuse and (compile_dir / 'xma-cor02').is_dir()))
            jobs_list.append(dict(name=xma_extra_unit.OBJECT_NAME, kind='extra', command=unit['command'], object=unit['object'],
                                  depfile=unit['depfile'], want=unit['want'], old=w, replaces=w))
            continue
        name = re.sub(r'^\d+-', '', Path(w).name)[:-2]
        u = by_name.get(name)
        if u is None or u['kind'] != 'runtime':
            raise Refused(f'the kept object {Path(w).name} has no runtime unit in the 1.0 frozen manifest')
        command = [x.replace(fwork, str(work)) for x in u['command']]
        command = [move.plain(x) for x in command]
        obj = str(out / 'kept' / (name + '.o'))
        dep = str(out / 'kept' / (name + '.d'))
        command[command.index('-o') + 1] = obj
        command[command.index('-MF') + 1] = dep
        if '-MT' in command:
            command[command.index('-MT') + 1] = obj
        jobs_list.append(dict(name=name, kind='kept', command=command, object=obj, depfile=dep, want=sha(w), old=w, replaces=w))
    # 4. compile
    cwd = root / json.loads((root / 'local-analysis/.attempts/config.json').read_text())['build_cwd']
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, jobs)) as pool:
        done = list(pool.map(lambda j: compile_unit(j, cwd, root, work, reuse), jobs_list))
    failed = [d['name'] for d in done if d.get('failed')]
    if failed:
        raise Refused('compile failed: ' + ', '.join(failed[:8]))
    leaks = {d['name']: d['private'] for d in done if d['private']}
    if leaks:
        raise Refused('units read private files outside the work folder: ' + json.dumps(dict(list(leaks.items())[:4])))
    kept_diff = [d['name'] for d in done if d['kind'] in ('kept', 'extra') and d['object_sha256'] != d['want']]
    if kept_diff:
        raise Refused('kept or extra units that are not the objects the attempt linked: ' + ', '.join(kept_diff))
    # 5. link: this run's objects, archives and version script
    by_old = {str(Path(d['old']).resolve()): d['object'] for d in done}
    link = [by_old.get(str(Path(w).resolve()), w) if w.endswith('.o') else w for w in link]
    for name in XMA_ARCHIVES:
        old = [w for w in link if not w.startswith('-') and Path(w).name == name]
        made = work / 'xma' / name[:-2] / name
        if len(old) != 1 or not made.is_file() or sha(made) != sha(old[0]):
            raise Refused(f'this run\'s {name} is not the archive the attempt linked')
        link = [str(made) if w == old[0] else w for w in link]
    library = out / f'libhost_guest_trace-attempt-{attempt.name}.so'
    link[link.index('-o') + 1] = str(library)
    link = ['--dependency-file=' + str(out / 'link.d') if w.startswith('--dependency-file=') else w for w in link]
    # portable, as the 1.0 rehearsal: no debug sections and, unless named, the attempt's own build id, so the loaded sections
    # can equal the attempt's
    build_id = portable_build_id or gnu_build_id(Path(receipt['library']))
    link = [w for w in link if not w.startswith('-Wl,--build-id=')] + ['-Wl,--strip-debug', '-Wl,--build-id=0x' + build_id]
    outside = [w for w in link if ('/local-analysis/' in move.plain(w)) and str(work) not in w
               and not any(s in w for s in SUPPLIED_BY_NAME)]
    if outside:
        raise Refused('the link names private files outside the work folder: ' + ', '.join(outside[:4]))
    (out / 'link-command.json').write_text(json.dumps(link, indent=1) + '\n')
    res = subprocess.run(link, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    (out / 'link.log').write_bytes(res.stdout)
    if res.returncode:
        raise Refused(f'the link failed (exit {res.returncode}); see {out / "link.log"}')
    differ = allocated_differences(Path(receipt['library']), library)
    report = dict(schema='tu-compile/1', attempt=attempt.name, attempt_library_sha256=receipt['library_sha256'],
                  library=str(library), library_sha256=sha(library), build_id=build_id, allocated_sections_differing=differ,
                  same_code=not differ, units=len(done), kept=len([d for d in done if d['kind'] == 'kept']),
                  objects_byte_equal=sum(1 for d in done if d['object_sha256'] == d['want']),
                  origins=sorted(move.origins), cwd=str(cwd), reused=sum(1 for d in done if d.get('reused')),
                  seconds=round(time.monotonic() - started, 1))
    (out / 'build.json').write_text(json.dumps(dict(report, records=[{k: d[k] for k in ('name', 'kind', 'object', 'object_sha256', 'replaces', 'seconds')}
                                                                     | {'source': d['command'][d['command'].index('-c') + 1]} for d in done],
                                                    link_command=str(out / 'link-command.json'), title_update=receipt['title_update']),
                                               indent=1) + '\n')
    (compile_dir / 'tu-compile.json').write_text(json.dumps(report, indent=1) + '\n')
    if not report['same_code']:
        raise Refused('the library\'s loaded sections are not the attempt\'s: ' + ', '.join(differ))
    return report


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--repo', type=Path, required=True)
    ap.add_argument('--work', type=Path, required=True)
    ap.add_argument('--attempt', type=Path, required=True)
    ap.add_argument('--tu-code', type=Path, action='append', required=True)
    ap.add_argument('--kept-manifest', type=Path, required=True)
    ap.add_argument('--generated-headers', type=Path, help='the 1.0 generated headers the kept units read (compile/generated-headers)')
    ap.add_argument('--jobs', type=int, default=4)
    ap.add_argument('--portable-build-id', help='40 hex digits (default: the attempt library\'s own build id)')
    ap.add_argument('--reuse', action='store_true', help='an existing compile/ is kept: unchanged objects are not compiled again')
    a = ap.parse_args(argv)
    root = a.repo.resolve()
    work = a.work.resolve()
    try:
        if not work.is_relative_to((root / 'local-analysis/builder').resolve()):
            raise Refused('the work folder must lie in local-analysis/builder of the repository')
        if a.portable_build_id and not re.fullmatch(r'[0-9a-f]{40}', a.portable_build_id):
            raise Refused('the portable build id must be 40 lower-case hex digits')
        report = build(root, work, (root / a.attempt).resolve() if not a.attempt.is_absolute() else a.attempt, a.tu_code,
                       a.kept_manifest, a.jobs, a.portable_build_id, a.generated_headers, a.reuse)
    except (Refused, OSError, ValueError, KeyError) as e:
        print(f'TU_COMPILE_REFUSED {e}', file=sys.stderr)
        return 1
    print('TU_COMPILE_DONE %d units (%d kept, %d objects byte-equal), library %s, same code as %s: %s, %.0f s' % (
        report['units'], report['kept'], report['objects_byte_equal'], report['library_sha256'][:12], report['attempt'],
        report['same_code'], report['seconds']))
    return 0


if __name__ == '__main__':
    sys.exit(main())
