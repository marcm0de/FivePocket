#!/usr/bin/env python3
"""Check that a finished title-update 27 attempt build keeps the players' saves, before it becomes a recipe.

    tu_release_check.py BUILD_DIR        (an attempt's build folder: build.json, obj/<unit>.meta.json)

A TU 27 library for players must be compiled with -DRECOMP_TU27_RELEASE_SAVES=1 on every unit that reads the switch (otherwise
save_sandbox.h forces the TU 27 test sandbox and the build keeps no saves outside a tu27 test copy): the rule
freeze_build.release_saves_problems states, which freeze_build.py applies when it freezes a build. This tool applies it to
an attempt build as tools/iter/iterate.py or the TU lane's build_tu27.py leave it: each object's meta file holds its compile
command and the files the compiler read, with their SHA-256. A file that changed since the build is refused (the check
would read other bytes than the compiler did).

Exit 0: a TU 27 build that passes, or no TU 27 build (nothing to check; said so). Exit 1: refused, with the reasons.
"""
import argparse
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import freeze_build  # noqa: E402


def units(build: Path) -> list:
    receipt = json.loads((build / 'build.json').read_text(encoding='utf-8'))
    out = []
    for r in receipt['records']:
        obj = Path(r['object'])
        meta_path = obj.with_name(obj.name[:-2] + '.meta.json') if obj.name.endswith('.o') else None
        if meta_path is None or not meta_path.is_file():
            raise ValueError(f'{r["name"]}: no meta file beside {obj.name} (its command and dependencies are unknown)')
        meta = json.loads(meta_path.read_text(encoding='utf-8'))
        for path, digest in meta['deps'].items():
            if hashlib.sha256(Path(path).read_bytes()).hexdigest() != digest:
                raise ValueError(f'{r["name"]}: {path} changed since the build')
        out.append((r['name'], meta['command'], list(meta['deps'])))
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('build', type=Path)
    a = ap.parse_args(argv)
    try:
        found = units(a.build)
        problems = freeze_build.release_saves_problems(found, lambda path: Path(path).read_bytes())
    except (ValueError, OSError, KeyError) as e:
        print(f'TU27_RELEASE_CHECK_REFUSED {e}', file=sys.stderr)
        return 1
    tu = [u for u in found if freeze_build.defined(u[1]).get(freeze_build.TU27) == '27']
    if not tu:
        print(f'TU27_RELEASE_CHECK no TU 27 unit in {len(found)} units: nothing to check')
        return 0
    if problems:
        for p in problems:
            print(f'TU27_RELEASE_CHECK_REFUSED {p}', file=sys.stderr)
        return 1
    with_switch = [n for n, c, _ in tu if freeze_build.RELEASE_SAVES in freeze_build.defined(c)]
    print(f'TU27_RELEASE_CHECK OK: {len(tu)} TU 27 units; every reader of the switch is compiled with it '
          f'({", ".join(sorted(with_switch))})')
    return 0


if __name__ == '__main__':
    sys.exit(main())
