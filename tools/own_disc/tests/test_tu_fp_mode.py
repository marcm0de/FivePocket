"""tu_fp_mode.py (the title update's fp-mode fix as a builder step) on synthetic chunks: no generated game code.

The real check is on the TU27 lane's corpus (builder-gui ledger, 2026-11-24): the step on tu27-q21-vx1 gives tu27-q21-fp1's three
chunks and header byte for byte.

    python3 -m unittest tools/own_disc/tests/test_tu_fp_mode.py
"""
import contextlib
import io
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import tu_fp_mode as m  # noqa: E402

HEAD = '#include "ppc_recomp_shared.h"\n'


def mffs(n):
    return f'\t// mffs f{n}\n\tctx.f{n}.u64 = ctx.fpscr.loadFromHost();\n'


def fctid(a, b, flush=False):
    return (f'\t// fctid f{a},f{b}\n' + ('\tctx.fpscr.disableFlushMode();\n' if flush else '') +
            f'\tctx.f{a}.s64 = (ctx.f{b}.f64 > double(LLONG_MAX)) ? LLONG_MAX : simde_mm_cvtsd_si64(simde_mm_load_sd(&ctx.f{b}.f64));\n')


def tree(root: Path, extra=None):
    chunks = {341: ''.join(fctid(i, i + 1, flush=i == 0) for i in range(2)),
              342: ''.join(fctid(i, i + 2) for i in range(8)),
              343: ''.join(mffs(i) for i in range(5)),
              7: '\tctx.r3.u64 = 0;\n'}
    chunks.update(extra or {})
    for n, body in chunks.items():
        (root / f'ppc_recomp.{n}.cpp').write_bytes((HEAD + body).replace('\n', '\r\n' if n == 7 else '\n').encode())
    return root


class FpMode(unittest.TestCase):
    def run_step(self, root: Path, out: Path):
        header = root.parent / 'ppc_fp_mode_fix.h'
        header.write_bytes(b'// fp mode helpers\n')
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            return m.main(['--generated', str(root), '--header', str(header), '--output', str(out)])

    def test_the_three_chunks_are_rewritten_and_written_with_the_header(self):
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, 'ppc').mkdir()
            gen = tree(Path(tmp, 'ppc'))
            self.assertEqual(self.run_step(gen, Path(tmp, 'out')), 0)
            out = Path(tmp, 'out')
            self.assertEqual(sorted(p.name for p in out.iterdir()),
                             ['ppc_fp_mode_fix.h', 'ppc_recomp.341.cpp', 'ppc_recomp.342.cpp', 'ppc_recomp.343.cpp'])
            for n, sites, new in ((341, 2, 'ppc_fctid_current_mode(ctx.f'), (342, 8, 'ppc_fctid_current_mode(ctx.f'),
                                  (343, 5, 'ppc_fpscr_guest_rounding(ctx.fpscr);')):
                text = (out / f'ppc_recomp.{n}.cpp').read_bytes().decode()
                self.assertTrue(text.startswith(m.INC), n)
                self.assertEqual(text.count(new), sites, n)
                self.assertNotIn('loadFromHost' if n == 343 else 'LLONG_MAX', text, n)
            self.assertIn('ctx.fpscr.disableFlushMode();\n\tctx.f0.s64 = ppc_fctid_current_mode(ctx.f1.f64);',
                          (out / 'ppc_recomp.341.cpp').read_text())

    def test_a_corpus_the_recipe_was_not_written_for_is_refused_and_nothing_is_written(self):
        cases = {'an open site in another chunk': {7: mffs(1)},
                 'a site fewer in a listed chunk': {343: ''.join(mffs(i) for i in range(4))},
                 'a site more in a listed chunk': {342: ''.join(fctid(i, i + 2) for i in range(9))},
                 'already rewritten': {343: ''.join(mffs(i) for i in range(5)).replace('ctx.fpscr.loadFromHost()',
                                                                                    'ppc_fpscr_guest_rounding(ctx.fpscr)')}}
        for label, extra in cases.items():
            with self.subTest(label), tempfile.TemporaryDirectory() as tmp:
                Path(tmp, 'ppc').mkdir()
                self.assertEqual(self.run_step(tree(Path(tmp, 'ppc'), extra), Path(tmp, 'out')), 1)
                self.assertFalse(Path(tmp, 'out').exists())


if __name__ == '__main__':
    unittest.main()
