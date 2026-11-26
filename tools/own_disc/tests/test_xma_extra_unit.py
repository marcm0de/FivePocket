"""xma_extra_unit.py (COR-02's sound-decoder object as one extra compile unit; lead's option A, 2026-11-24) on a synthetic XMA folder.

The byte-equality itself is proven with the NDK on the owner's run (builder-gui ledger 2026-11-24: the plan's own command on the
changed file gives 08322eee, also from another folder with absolute paths and the prefix map); here: the command's shape that makes
that so, and the refusals.

    python3 -m unittest tools/own_disc/tests/test_xma_extra_unit.py
"""
import hashlib
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import xma_extra_unit as m  # noqa: E402


class Prepare(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        self.xma = self.root / 'xma'
        (self.xma / 'libavcodec').mkdir(parents=True)
        (self.xma / 'libavcodec/wmaprodec.c').write_text('/* the revision\'s file */\n')
        (self.xma / 'libavcodec/wmaprodec.o').write_bytes(b'stale object of the revision\'s file')
        (self.xma / 'config.h').write_text('#define X 1\n')

    def tearDown(self):
        self.tmp.cleanup()

    def test_the_plans_own_command_reads_the_changed_file_with_absolute_paths_and_the_folder_mapped_away(self):
        unit = m.prepare(self.xma, self.root / 'out', Path('/ndk/bin/clang'))
        folder = self.root / 'out/xma'
        command = unit['command']
        self.assertEqual(command[0], str(Path('/ndk/bin/clang')))
        self.assertEqual(command[1], f'-ffile-prefix-map={folder}/=')
        self.assertEqual(command[-1], str(folder / 'libavcodec/wmaprodec.c'))
        self.assertTrue(all(Path(w[2:]).is_absolute() for w in command if w.startswith('-I')), 'no include folder relative to a cwd')
        self.assertEqual((command[command.index('-o') + 1], unit['want']), (str(self.root / 'out' / m.OBJECT_NAME), m.OBJECT_SHA256['plan.json']))
        self.assertEqual(hashlib.sha256((folder / 'libavcodec/wmaprodec.c').read_bytes()).hexdigest(), m.PATCHED_SHA256)
        self.assertFalse((folder / 'libavcodec/wmaprodec.o').exists(), 'the stage\'s object of the revision\'s file is not left there')
        self.assertEqual((folder / 'config.h').read_text(), '#define X 1\n')
        self.assertEqual((self.xma / 'libavcodec/wmaprodec.c').read_text(), '/* the revision\'s file */\n', 'the stage folder is untouched')
        self.assertEqual(m.prepare(self.xma, self.root / 'out', Path('/ndk/bin/clang'), lay_out=False)['command'], command,
                         'the same command for a folder laid out before')

    def test_without_a_cp_program_the_folder_is_still_laid_out(self):
        # 2026-11-24 MSI suite: Windows has no cp; the clone attempt raised FileNotFoundError instead of falling back to a copy
        with mock.patch.object(m.subprocess, 'run', side_effect=FileNotFoundError(2, 'cp')):
            unit = m.prepare(self.xma, self.root / 'out', Path('/ndk/bin/clang'))
        self.assertEqual(hashlib.sha256(Path(unit['source']).read_bytes()).hexdigest(), m.PATCHED_SHA256)
        self.assertEqual((self.root / 'out/xma/config.h').read_text(), '#define X 1\n')

    def test_an_existing_folder_or_a_changed_file_that_is_not_cor_02s_is_refused(self):
        m.prepare(self.xma, self.root / 'out', Path('/ndk/bin/clang'))
        with self.assertRaisesRegex(m.UnitError, 'exists already'):
            m.prepare(self.xma, self.root / 'out', Path('/ndk/bin/clang'))
        (self.root / 'out/xma/libavcodec/wmaprodec.c').write_text('/* another change */\n')
        with self.assertRaisesRegex(m.UnitError, 'not the changed file'):
            m.prepare(self.xma, self.root / 'out', Path('/ndk/bin/clang'), lay_out=False)


if __name__ == '__main__':
    unittest.main()
