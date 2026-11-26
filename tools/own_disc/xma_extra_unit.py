#!/usr/bin/env python3
"""COR-02's sound-decoder unit as one extra compile of the game library (lead's decision 2026-11-24, "option A").

From v1971 / tu27-m04r on, the game library links the object of this project's changed libavcodec/wmaprodec.c (a flush callback
for the raw-frames XMA decoder; xma_decoder/cor02/README.txt) beside the XMA stage's unchanged libavcodec.a. A player's run cannot
take a ready-made object, so it compiles that file itself: in a copy of this run's XMA stage folder with the changed file laid
over the revision's, with the XMA plan's own command for that unit (build_xma_decoder.unit_command), its paths absolute and the
folder mapped away (-ffile-prefix-map=<folder>/=), so the object reads libavcodec/wmaprodec.c as the plan's own build does and
one recipe working folder can carry it. The object must be byte-equal to the one the library was made with.

    prepare(xma_folder, out_folder, clang) -> {'command', 'object', 'depfile', 'source'}  (lays the folder out; compiles nothing)

The XMA stage, its plans and its archives are not changed (option B, rejected, would have re-pinned every frozen recipe).
"""
from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from pathlib import Path

HERE = Path(__file__).resolve().parent
PATCHED = HERE / 'xma_decoder/cor02/libavcodec/wmaprodec.c'
PATCHED_SHA256 = 'fb2f1fd97ad3072bb2904b1ee46597f5de4f60e04f4f5ca63687b31d6eadfccd'   # COR-02's changed file (audit-fixes-1116/cor-02/ffmpeg/wmaprodec.c)
UNIT_SOURCE = 'libavcodec/wmaprodec.c'
OBJECT_SHA256 = {'plan.json': '08322eee756dcf9a3c8b690aa89ced593523f617d5e2d2724431938b7cfadf0c'}   # the Android plan's object
OBJECT_NAME = 'wmaprodec-android.o'       # the name the attempts link it under (link_extra)


class UnitError(ValueError):
    pass


def sha(path) -> str:
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for block in iter(lambda: f.read(1 << 20), b''):
            h.update(block)
    return h.hexdigest()


def clone_tree(src: Path, dst: Path) -> None:
    """dst = a copy of src (APFS clones where the volume allows, else a plain copy; Windows has no cp)."""
    try:
        cloned = subprocess.run(['cp', '-c', '-R', str(src), str(dst)], capture_output=True).returncode == 0
    except OSError:
        cloned = False
    if not cloned:
        if dst.exists():
            shutil.rmtree(dst)
        shutil.copytree(src, dst, symlinks=True)


def prepare(xma: Path, out: Path, clang: Path, plan_name: str = 'plan.json', lay_out: bool = True) -> dict:
    """Lay out out/xma (a copy of this run's XMA stage folder with the changed file over the revision's) and return the unit's
    command; out must not exist. The command writes out/wmaprodec-android.o and out/wmaprodec-android.d."""
    import build_xma_decoder
    if lay_out and out.exists():
        raise UnitError(f'{out} exists already')
    if sha(PATCHED) != PATCHED_SHA256:
        raise UnitError(f'{PATCHED} is not the changed file this unit was proven with')
    plan = json.loads((HERE / 'xma_decoder' / plan_name).read_text(encoding='utf-8'))
    units = [u for u in plan['units'] if u['source'] == UNIT_SOURCE]
    if len(units) != 1:
        raise UnitError(f'the XMA plan has no single unit {UNIT_SOURCE}')
    folder = out / 'xma'
    if lay_out:                       # (lay_out=False: the same command for a folder an earlier run laid out)
        out.mkdir(parents=True)
        clone_tree(Path(xma), folder)
        for stale in ('libavcodec/wmaprodec.o', 'libavcodec/wmaprodec.d'):
            (folder / stale).unlink(missing_ok=True)
        (folder / UNIT_SOURCE).unlink()
        shutil.copyfile(PATCHED, folder / UNIT_SOURCE)
    elif sha(folder / UNIT_SOURCE) != PATCHED_SHA256:
        raise UnitError(f'{folder / UNIT_SOURCE} is not the changed file')
    obj, dep = out / OBJECT_NAME, out / (OBJECT_NAME[:-2] + '.d')
    command = []
    for word in build_xma_decoder.unit_command(plan, units[0], Path(clang)):
        if word in ('-I.', '-I./'):
            word = '-I' + str(folder) + ('/' if word.endswith('/') else '')
        elif word.startswith('-I') and not Path(word[2:]).is_absolute():
            word = '-I' + str(folder / word[2:])
        command.append(word)
    command[command.index('-o') + 1] = str(obj)
    command[command.index('-MF') + 1] = str(dep)
    command[command.index('-MT') + 1] = str(obj)
    if command[-1] != UNIT_SOURCE:
        raise UnitError('the plan\'s unit command does not end with its source')
    command[-1] = str(folder / UNIT_SOURCE)
    command.insert(1, f'-ffile-prefix-map={folder}/=')
    return dict(command=command, object=str(obj), depfile=str(dep), source=str(folder / UNIT_SOURCE),
                want=OBJECT_SHA256[plan_name])
