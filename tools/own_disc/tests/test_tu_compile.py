"""tu_compile.py (the title update's library in a builder work folder): the word moves, the include order and the private-read rule,
on synthetic folders. The compile itself is proven on the TU27 lane's tu27-a132r (builder-gui ledger, 2026-11-24): every loaded
section of the work-folder library equals the attempt's.

    python3 -m unittest tools/own_disc/tests/test_tu_compile.py
"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import tu_compile as m  # noqa: E402


class Words(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name, 'repo').resolve()
        self.attempt = self.root / 'local-analysis/.attempts/tu27-a9'
        (self.attempt / 'src').mkdir(parents=True)
        (self.attempt / 'attempt.json').write_text(json.dumps({'version_script': 'private_trace.exports'}))
        self.work = self.root / 'local-analysis/builder/w'
        self.tu = str(self.root / 'local-analysis/title-update/regen/tu27/ppc')
        os.symlink(self.root, Path(self.tmp.name, 'alias'))
        self.alias = str(Path(self.tmp.name, 'alias'))

    def tearDown(self):
        self.tmp.cleanup()

    def test_the_attempts_folders_move_into_the_work_folder_in_either_spelling_of_the_root(self):
        words = ['-iquote', self.tu, '-c', f'{self.attempt}/src/a.cpp', '-o', f'{self.attempt}/build/obj/a.cpp.o',
                 f'-Wl,--version-script={self.attempt}/private_trace.exports',
                 '-include', f'{self.alias}/local-analysis/.attempts/tu27-a1/src/title_update.h', f'-I{self.alias}/tools/x']
        self.assertEqual(m.aliases(self.root, words), [self.alias])
        move = m.Mover(self.root, self.work, self.attempt, self.tu, m.aliases(self.root, words))
        moved = [move(w) for w in words]
        c = str(self.work / 'compile')
        self.assertEqual(moved, ['-iquote', c + '/tu-ppc', '-c', c + '/attempts/tu27-a9/src/a.cpp', '-o', c + '/attempts/tu27-a9/build/obj/a.cpp.o',
                                 f'-Wl,--version-script={c}/attempts/tu27-a9/private_trace.exports',
                                 '-include', c + '/origins/tu27-a1/src/title_update.h', f'-I{self.root}/tools/x'])
        self.assertEqual(move.origins, {'tu27-a1': self.root / 'local-analysis/.attempts/tu27-a1/src'})

    def test_this_runs_folders_go_first_in_the_order_the_1_0_rehearsal_gives_them(self):
        la = self.root / 'local-analysis'
        for rel in ('native-packs/p/api/shaders', 'native-packs/p/api/code', 'code-headers/p/api/code', 'xma'):
            (self.work / rel).mkdir(parents=True)
        command = ['clang++', f'-I{la}/native-xma/ffmpeg', f'-I{la}/p/api/shaders', f'-I{self.root}/tools/v', f'-I{la}/p/api/code',
                   f'-I{la}/p/api/none']
        self.assertEqual(m.shadow_words(self.root, self.work, command),
                         ['-I' + str(self.work / 'xma'), '-I' + str(self.work / 'code-headers/p/api/code'),
                          '-I' + str(self.work / 'native-packs/p/api/shaders'), '-I' + str(self.work / 'native-packs/p/api/code')])

    def test_a_private_file_read_outside_the_work_folder_is_named_unless_the_profile_supplies_it_by_name(self):
        la = str(self.root / 'local-analysis')
        deps = [f'{la}/builder/w/native-packs/a.h', f'{la}/pgo/round7.profdata', f'{la}/p/api/code/pack.h', f'{self.root}/tools/t.h',
                f'{la}/android-runtime-bridge-live-swap/api26/generated_guest_import_bridge.h']
        self.assertEqual(m.private_reads(self.root, self.work, deps), ['local-analysis/p/api/code/pack.h'])


if __name__ == '__main__':
    unittest.main()
