#!/usr/bin/env python3
"""The title update's fp-mode fix (audit COR-03 / COR-04) on this run's generated TU 27 code: the builder step of the TU27 lane's
tu_fp_mode_fix.py (local-analysis/title-update/carryover/online-a64/tools, sha256 3b8f5c01...; receipt
title-update/regen/tu27-q21-fp1/RECIPE.json), with the same rewrites and refusals.

    tu_fp_mode.py --generated CODEGEN_PPC --header ppc_fp_mode_fix.h --output NEW_DIR

  COR-03  chunk 343: the 5 `mffs` lines   ctx.fN.u64 = ctx.fpscr.loadFromHost();  ->  ... = ppc_fpscr_guest_rounding(ctx.fpscr);
  COR-04  chunks 341 (2) and 342 (8): the `fctid` lines
          ctx.fA.s64 = (ctx.fB.f64 > double(LLONG_MAX)) ? LLONG_MAX : simde_mm_cvtsd_si64(simde_mm_load_sd(&ctx.fB.f64));
          ->  ctx.fA.s64 = ppc_fctid_current_mode(ctx.fB.f64);
Each rewritten chunk gets #include "ppc_fp_mode_fix.h" as its second line (after ppc_recomp_shared.h). NEW_DIR receives only the
three rewritten chunks and the header: the compile takes every other chunk from the codegen output by its hash. Refuses, writing
nothing, unless it finds exactly the expected sites in those chunks and none in any other chunk (a different corpus is never
half-fixed). Bytes are read and written as they are (no newline translation), so every host writes the same files. Nothing of the
game is in this file. Exit 0 written, 1 refused.
"""
import argparse
import hashlib
import re
import sys
from pathlib import Path

HEAD = '#include "ppc_recomp_shared.h"\n'
INC = HEAD + '#include "ppc_fp_mode_fix.h" // fp_mode_fix.py (audit COR-03 / COR-04)\n'
MFFS = re.compile(r'^(\t// mffs (f\d+)\n\tctx\.\2\.u64 = )ctx\.fpscr\.loadFromHost\(\);$', re.M)
MFFS_DONE = re.compile(r'^\t// mffs (f\d+)\n\tctx\.\1\.u64 = ppc_fpscr_guest_rounding\(ctx\.fpscr\);$', re.M)
FCTID = re.compile(r'^(\t// fctid (f\d+),(f\d+)\n(?:\tctx\.fpscr\.disableFlushMode\(\);\n)?\tctx\.\2\.s64 = )'
                   r'\(ctx\.\3\.f64 > double\(LLONG_MAX\)\) \? LLONG_MAX : simde_mm_cvtsd_si64\(simde_mm_load_sd\(&ctx\.\3\.f64\)\);$', re.M)
FCTID_DONE = re.compile(r'^\t// fctid (f\d+),(f\d+)\n(?:\tctx\.fpscr\.disableFlushMode\(\);\n)?\tctx\.\1\.s64 = ppc_fctid_current_mode\(ctx\.\2\.f64\);$', re.M)
# (name, {chunk: sites}, open pattern, rewritten pattern, replacement, marker counted per line) as the lane's recipe applies them
PLAN = [('mffs', {343: 5}, MFFS, MFFS_DONE, r'\1ppc_fpscr_guest_rounding(ctx.fpscr);', 'loadFromHost'),
        ('fctid', {341: 2, 342: 8}, FCTID, FCTID_DONE, r'\1ppc_fctid_current_mode(ctx.\3.f64);', '// fctid ')]
HEADER = 'ppc_fp_mode_fix.h'


class Refused(Exception):
    pass


def chunk_number(path: Path):
    m = re.fullmatch(r'ppc_recomp\.(\d+)\.cpp', path.name)
    return int(m.group(1)) if m else None


def fix(generated: Path, plan=PLAN) -> dict:
    """{file name: new text} for the rewritten chunks. Raises Refused when the corpus is not the one the recipe was written for."""
    out = {}
    for name, chunks, todo, done, repl, marker in plan:
        for n, count in sorted(chunks.items()):
            fname = f'ppc_recomp.{n}.cpp'
            text = out.get(fname)
            if text is None:
                path = generated / fname
                if not path.is_file():
                    raise Refused(f'{fname} is not in the generated code')
                text = path.read_bytes().decode('utf-8')
            open_sites, done_sites = len(todo.findall(text)), len(done.findall(text))
            lines = sum(1 for line in text.split('\n') if marker in line)
            if open_sites != count or done_sites or lines != count:
                raise Refused(f'{name} chunk {n}: expected {count} open sites, found {open_sites} open / {done_sites} done / '
                              f'{lines} marker lines')
            text = todo.sub(repl, text)
            if not text.startswith(HEAD):
                raise Refused(f'chunk {n}: unexpected first line')
            if not text.startswith(INC):
                text = INC + text[len(HEAD):]
            out[fname] = text
    listed = {n for _, chunks, *_ in plan for n in chunks}
    for path in sorted(generated.glob('ppc_recomp.*.cpp')):
        n = chunk_number(path)
        if n is None or n in listed:
            continue
        text = path.read_bytes().decode('utf-8', errors='replace').replace('\r\n', '\n')   # (any line ending still counts)
        for name, _, todo, *_ in plan:
            if todo.search(text):
                raise Refused(f'{name}: an open site in {path.name}, a chunk the recipe does not list')
    return out


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
        files = fix(a.generated)
        a.output.mkdir(parents=True)
        for fname, text in sorted(files.items()):
            (a.output / fname).write_bytes(text.encode('utf-8'))
        (a.output / HEADER).write_bytes(a.header.read_bytes())
        for fname in sorted(files) + [HEADER]:
            print('TU_FP_MODE %s %s' % (hashlib.sha256((a.output / fname).read_bytes()).hexdigest()[:12], fname))
        print('TU_FP_MODE_DONE %d chunks rewritten' % len(files))
        return 0
    except (Refused, OSError, UnicodeDecodeError) as e:
        print(f'TU_FP_MODE_REFUSED {e}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
