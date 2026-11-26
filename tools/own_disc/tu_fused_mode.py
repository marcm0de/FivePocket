#!/usr/bin/env python3
"""Audit fix COR-05 (fused multiply-add) on this run's generated TU 27 code: the builder step of the merge-next port
tu_fused_fix.py (local-analysis/audit-fixes-1116/merge-next, sha256 94eb1a27...; receipt title-update/regen/tu27-q21-fx1/RECIPE.json),
with the same rewrite and refusals (owner ship gate 2026-11-24: COR-05 on the TU corpus too).

    tu_fused_mode.py --generated CODEGEN_PPC --header ppc_fused_fix.h --output NEW_DIR

The audited mixing kernel (1.0 sub_83138190, already fused in the 1.0 recipe's chunk 253) is TU 27 sub_831B5F98. In the one chunk
that holds it, its four vmaddfp statements in the generator's spelling
    simde_mm_store_ps(ctx.vD.f32, simde_mm_add_ps(simde_mm_mul_ps(simde_mm_load_ps(ctx.vA.f32), simde_mm_load_ps(ctx.vC.f32)), simde_mm_load_ps(ctx.vB.f32)));
become  simde_mm_store_ps(ctx.vD.f32, ppc_fused_vmaddfp(simde_mm_load_ps(ctx.vA.f32), simde_mm_load_ps(ctx.vC.f32), simde_mm_load_ps(ctx.vB.f32)));
(rounded once: tools/own_disc/cor05/ppc_fused_fix.h), and the chunk gets #include "ppc_fused_fix.h" after its leading #include
lines. NEW_DIR receives only that chunk and the header: the compile takes every other chunk from the codegen output by its hash.
Refuses, writing nothing, unless the function is in exactly one chunk, its body holds exactly four such statements and no chunk is
already fused (a different corpus is never half-fixed). Bytes are read and written as they are (no newline translation), so every
host writes the same files. Nothing of the game is in this file. Exit 0 written, 1 refused.
"""
import argparse
import hashlib
import re
import sys
from pathlib import Path

FUNCTION = 'sub_831B5F98'
SITES = 4
HEADER = 'ppc_fused_fix.h'
INCLUDE = '#include "ppc_fused_fix.h"'
R = r'((?:ctx\.|l_)[vf]\d+)'
LP = r'simde_mm_load_ps\(' + R + r'\.f32\)'
VMADD = re.compile(r'^\tsimde_mm_store_ps\(' + R + r'\.f32, simde_mm_add_ps\(simde_mm_mul_ps\(' + LP + ', ' + LP + r'\), ' + LP + r'\)\);$')


def fused(m) -> str:
    return (f'\tsimde_mm_store_ps({m[1]}.f32, ppc_fused_vmaddfp(simde_mm_load_ps({m[2]}.f32), simde_mm_load_ps({m[3]}.f32), '
            f'simde_mm_load_ps({m[4]}.f32)));')


class Refused(Exception):
    pass


def fix(generated: Path) -> tuple:
    """(file name, new text) of the rewritten chunk. Raises Refused when the corpus is not the one the recipe was written for."""
    head = f'PPC_FUNC_IMPL(__imp__{FUNCTION}) {{'
    texts = {p.name: p.read_bytes().decode('utf-8') for p in sorted(generated.glob('ppc_recomp.*.cpp'))}
    already = sorted(n for n, t in texts.items() if 'ppc_fused_' in t)
    if already:
        raise Refused('already fused: ' + ', '.join(already))
    hits = [n for n, t in texts.items() if head in t]
    if len(hits) != 1:
        raise Refused(f'{FUNCTION} is in {len(hits)} chunks, not one')
    name = hits[0]
    text = texts[name]
    i = text.index(head)
    j = text.find('\nPPC_FUNC_IMPL(', i + 1)
    j = len(text) if j < 0 else j
    lines = text[i:j].split('\n')
    sites = 0
    for k, line in enumerate(lines):
        m = VMADD.match(line)
        if m:
            lines[k] = fused(m)
            sites += 1
    if sites != SITES:
        raise Refused(f'{FUNCTION}: {sites} vmaddfp statements in the generator\'s spelling, expected {SITES}')
    parts = (text[:i] + '\n'.join(lines) + text[j:]).split('\n')
    k = 0
    while k < len(parts) and (parts[k].startswith('#include') or not parts[k].strip()):
        k += 1
    includes = [x for x in range(k) if parts[x].startswith('#include')]
    if not includes:
        raise Refused(f'{name}: no leading #include line')
    parts.insert(includes[-1] + 1, INCLUDE)
    return name, '\n'.join(parts)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--generated', type=Path, required=True)
    ap.add_argument('--header', type=Path, required=True)
    ap.add_argument('--output', type=Path, required=True)
    a = ap.parse_args(argv)
    try:
        if a.output.exists():
            raise Refused(f'{a.output} exists already: nothing is written over')
        if a.header.name != HEADER or not a.header.is_file():
            raise Refused(f'--header must name the file {HEADER}')
        name, text = fix(a.generated)
        a.output.mkdir(parents=True)
        (a.output / name).write_bytes(text.encode('utf-8'))
        (a.output / HEADER).write_bytes(a.header.read_bytes())
        for fname in (name, HEADER):
            print('TU_FUSED %s %s' % (hashlib.sha256((a.output / fname).read_bytes()).hexdigest()[:12], fname))
        print(f'TU_FUSED_DONE {FUNCTION} in {name}: {SITES} statements fused')
        return 0
    except (Refused, OSError, UnicodeDecodeError) as e:
        print(f'TU_FUSED_REFUSED {e}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
