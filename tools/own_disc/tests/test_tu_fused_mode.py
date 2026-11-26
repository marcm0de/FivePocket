"""tu_fused_mode.py (audit COR-05 on the TU code as a builder step) on synthetic chunks: no generated game code.

The real check is on the TU27 corpus (builder-gui, 2026-11-24): the step on this run's tu-codegen output turns chunk 256 (619b4535)
into tu27-q21-fx1's chunk 256 (c4ac51c3) byte for byte, and refuses fx1 itself (already fused).

    python3 -m unittest tools/own_disc/tests/test_tu_fused_mode.py
"""
import contextlib
import io
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import tu_fused_mode as m  # noqa: E402


def vmadd(d, a, c, b):
    return (f'\tsimde_mm_store_ps(ctx.v{d}.f32, simde_mm_add_ps(simde_mm_mul_ps(simde_mm_load_ps(ctx.v{a}.f32), '
            f'simde_mm_load_ps(ctx.v{c}.f32)), simde_mm_load_ps(ctx.v{b}.f32)));\n')


def chunk(kernel_sites=4, other_sites=1):
    return ('#include "ppc_recomp_shared.h"\n\n'
            'PPC_FUNC_IMPL(__imp__sub_831B5F98) {\n' + ''.join(vmadd(13, 9 + i, 0, 13) for i in range(kernel_sites)) + '}\n\n'
            'PPC_FUNC_IMPL(__imp__sub_831B6000) {\n' + ''.join(vmadd(1, 2, 3, 4) for _ in range(other_sites)) + '}\n')


class Fix(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.gen = self.root / 'ppc'
        self.gen.mkdir()
        (self.gen / 'ppc_recomp.7.cpp').write_bytes(b'#include "ppc_recomp_shared.h"\n\tctx.r3.u64 = 0;\n')
        self.header = self.root / 'ppc_fused_fix.h'
        self.header.write_bytes(b'#pragma once\n')

    def tearDown(self):
        self.tmp.cleanup()

    def run_step(self, out='out'):
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()) as err:
            code = m.main(['--generated', str(self.gen), '--header', str(self.header), '--output', str(self.root / out)])
        return code, err.getvalue()

    def test_only_the_kernels_four_statements_are_fused_and_the_include_follows_the_leading_includes(self):
        (self.gen / 'ppc_recomp.256.cpp').write_bytes(chunk().encode())
        self.assertEqual(self.run_step()[0], 0)
        out = sorted(p.name for p in (self.root / 'out').iterdir())
        self.assertEqual(out, ['ppc_fused_fix.h', 'ppc_recomp.256.cpp'], 'only the rewritten chunk and the header')
        text = (self.root / 'out/ppc_recomp.256.cpp').read_text()
        self.assertTrue(text.startswith('#include "ppc_recomp_shared.h"\n#include "ppc_fused_fix.h"\n\n'))
        kernel, other = text.split('PPC_FUNC_IMPL(__imp__sub_831B6000)')
        self.assertEqual(kernel.count('ppc_fused_vmaddfp(simde_mm_load_ps(ctx.v9.f32), simde_mm_load_ps(ctx.v0.f32), '
                                      'simde_mm_load_ps(ctx.v13.f32))'), 1)
        self.assertEqual(kernel.count('ppc_fused_vmaddfp('), 4)
        self.assertNotIn('ppc_fused_', other, 'another function\'s statement stays as the generator wrote it')

    def test_another_corpus_is_refused_and_nothing_is_written(self):
        for case, files in (('three sites', {'ppc_recomp.256.cpp': chunk(kernel_sites=3)}),
                            ('already fused', {'ppc_recomp.256.cpp': chunk().replace('simde_mm_add_ps(simde_mm_mul_ps(', 'ppc_fused_x(', 1)}),
                            ('in two chunks', {'ppc_recomp.256.cpp': chunk(), 'ppc_recomp.257.cpp': chunk()}),
                            ('in no chunk', {})):
            with self.subTest(case):
                for p in self.gen.glob('ppc_recomp.25*.cpp'):
                    p.unlink()
                for name, text in files.items():
                    (self.gen / name).write_text(text)
                code, err = self.run_step(out=case)
                self.assertEqual(code, 1)
                self.assertIn('TU_FUSED_REFUSED', err)
                self.assertFalse((self.root / case).exists())


if __name__ == '__main__':
    unittest.main()
