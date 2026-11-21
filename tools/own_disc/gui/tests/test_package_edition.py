"""1.2.0 (owner): each Windows builder download opens its window on its own edition and lists both Android editions (one
bundle) with iOS and Duo greyed. The Single-Screen and Thor Edition
downloads carry the same Thor-line profile (one bundle for both apps), so the edition cannot come from the profile's name;
stage_gui.py --edition writes it into the package (edition.txt, outside the builder's inventory) and the window reads it
(builder_core.discover_editions). Synthetic packages only.

    python3 -m unittest tools.own_disc.gui.tests.test_package_edition
"""
from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import unittest

OWN_DISC = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(OWN_DISC))
sys.path.insert(0, str(OWN_DISC / 'gui'))
import builder as B  # noqa: E402
import builder_core as core  # noqa: E402
import stage_gui  # noqa: E402


def package(folder: Path) -> Path:
    """a builder package as the release lane makes them: release.json + one Thor-line profile (no other files needed here)"""
    folder.mkdir(parents=True)
    app = lambda zip_name, member, label: {'url': f'https://github.com/o/FivePocket/releases/download/v1.2.0/{zip_name}',
                                          'page': 'https://github.com/o/FivePocket/releases/tag/v1.2.0', 'app': label,
                                          'sha256': '1' * 64, 'size': 1, 'member': member, 'member_sha256': '2' * 64, 'member_size': 1}
    (folder / 'release.json').write_text(json.dumps({'schema': 'builder-release/1', 'host': B.host_id(),
                                                     'profile': 'profile-thor-fivepocket-thor-v1922-3n-ui.json', 'apps': {
        'fold': app('FivePocket-1.2.0-android.zip', 'FivePocket-1.2.0-android/FivePocket-1.2.0.apk', 'FivePocket single screen'),
        'thor': app('FivePocket-Thor-Edition-1.2.0-android.zip', 'FivePocket-Thor-Edition-1.2.0-android/FivePocket-Thor-Edition-1.2.0.apk',
                    'FivePocket Thor Edition')}}))
    (folder / 'profile-thor-fivepocket-thor-v1922-3n-ui.json').write_text(json.dumps(
        {'id': 'gta5-disc-thor-fivepocket-thor-v1922-3-portable', 'stages': [{'id': 'extract-xex'}], 'games': [],
         'bundle': {'name': 'fivepocket-thor-edition-v1922.bundle'}}))
    return folder


def opens_as(folder: Path) -> str:
    """the edition the window selects first for this package alone (AppState.chosen: the available editions, in order)"""
    return next(e.key for e in core.discover_editions([folder]) if e.available)


class PackageEdition(unittest.TestCase):
    def test_each_download_opens_on_its_own_edition(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = package(Path(tmp) / 'base')
            thor = Path(tmp) / 'FivePocket-Builder-Windows-Thor-Edition'
            single = Path(tmp) / 'FivePocket-Builder-Windows-Single-Screen'
            stage_gui.stage(base, thor, edition='thor')
            stage_gui.stage(base, single, edition='fold')
            self.assertEqual(opens_as(thor), 'thor')
            self.assertEqual(opens_as(single), 'fold')           # the window's ANDROID word and android logo (launcher.js)
            self.assertEqual(opens_as(base), 'thor', 'a package without the file keeps the profile-derived edition')
            for out, word in ((thor, 'thor'), (single, 'fold')):
                added = json.loads((out / 'gui-manifest.json').read_text())['added']
                self.assertIn('edition.txt', [a['path'] for a in added])
                self.assertEqual((out / 'edition.txt').read_bytes(), word.encode() + b'\n')

    def test_each_package_lists_both_android_editions_and_the_apple_ones_greyed(self):
        # owner 1.2.0: every Windows download offers Thor Edition AND single screen (one bundle), iOS and Duo greyed
        with tempfile.TemporaryDirectory() as tmp:
            base = package(Path(tmp) / 'base')
            for name, word in (('Android', 'fold'), ('Thor', 'thor')):
                out = Path(tmp) / name
                stage_gui.stage(base, out, edition=word)
                listed = {e.key: e for e in core.discover_editions([out])}
                self.assertEqual(sorted(listed), ['duo', 'fold', 'ios', 'thor'], name)
                self.assertTrue(listed['thor'].available and listed['fold'].available, name)
                for apple in ('ios', 'duo'):
                    self.assertFalse(listed[apple].available)
                    self.assertIn('macOS builder package', listed[apple].reason)
                self.assertEqual(opens_as(out), word, name)
                self.assertNotEqual(listed['thor'].ident, listed['fold'].ident)

    def test_both_android_editions_build_once_with_a_copy_each(self):
        with tempfile.TemporaryDirectory() as tmp:
            out_pkg = Path(tmp) / 'Android'
            stage_gui.stage(package(Path(tmp) / 'base'), out_pkg, edition='fold')
            discs = {'play': Path(tmp) / 'play.iso', 'install': Path(tmp) / 'install.iso'}
            for d in discs.values():
                d.write_bytes(b'x')
            both = [e for e in core.discover_editions([out_pkg]) if e.available]
            steps = core.make_plan(both, discs, Path(tmp) / 'out', Path(tmp) / 'cache', True,
                                   fetched={str(both[0].release_path): True})
            self.assertEqual([s.kind for s in steps], ['check', 'build', 'verify', 'app', 'build', 'verify', 'app'])
            outs = [s.words[s.words.index('--out') + 1] for s in steps if s.kind == 'build']
            self.assertEqual(outs, [str(Path(tmp) / 'out' / 'Single screen'), str(Path(tmp) / 'out' / 'Thor Edition')])
            one = core.make_plan(both[:1], discs, Path(tmp) / 'out', Path(tmp) / 'cache', True, fetched={str(both[0].release_path): True})
            self.assertEqual([s.words[s.words.index('--out') + 1] for s in one if s.kind == 'build'], [str(Path(tmp) / 'out')])

    def test_single_screen_output_is_named_for_its_app_and_thor_keeps_the_thor_name(self):
        # owner 1.2.0: "the builder is still building Thor APK for android" - the same bundle, named for the edition built
        with tempfile.TemporaryDirectory() as tmp:
            pkg = Path(tmp) / 'Android'
            stage_gui.stage(package(Path(tmp) / 'base'), pkg, edition='fold')
            discs = {'play': Path(tmp) / 'play.iso', 'install': Path(tmp) / 'install.iso'}
            for d in discs.values():
                d.write_bytes(b'x')
            both = [e for e in core.discover_editions([pkg]) if e.available]
            builds = {s.edition_label.split(' (')[0]: s for s in core.make_plan(both, discs, Path(tmp) / 'out', Path(tmp) / 'cache', True,
                                                                               fetched={str(both[0].release_path): True}) if s.kind == 'build'}
            self.assertEqual(builds['FivePocket single screen'].out_name, 'fivepocket-v1922.bundle')
            self.assertEqual(builds['FivePocket Thor Edition'].out_name, '')
            step = builds['FivePocket single screen']
            made = Path(tmp) / 'out' / 'Single screen' / 'fivepocket-thor-edition-v1922.bundle'      # what the engine writes
            made.parent.mkdir(parents=True)
            made.write_bytes(b'bundle bytes')
            said = []
            runner = type('R', (), {'_log': lambda self, line: said.append(line)})()
            result = core.Runner._named(runner, step, {'path': str(made), 'size': 12, 'seal': 'abc'})
            target = made.with_name('fivepocket-v1922.bundle')
            self.assertEqual((result['path'], result['seal'], target.read_bytes(), made.exists()), (str(target), 'abc', b'bundle bytes', False))
            self.assertEqual(said, [f'Your package (FivePocket single screen) is at {target}'])

    def test_each_tick_combination_puts_its_apk_beside_its_bundle(self):
        # owner 1.2.0: the output folder holds the edition's APK and its bundle; both ticked, all four files
        with tempfile.TemporaryDirectory() as tmp:
            pkg = Path(tmp) / 'Android'
            stage_gui.stage(package(Path(tmp) / 'base'), pkg, edition='fold')
            discs = {'play': Path(tmp) / 'play.iso', 'install': Path(tmp) / 'install.iso'}
            for d in discs.values():
                d.write_bytes(b'x')
            eds = {e.key: e for e in core.discover_editions([pkg]) if e.available}
            out = Path(tmp) / 'out'
            for label, keys, want in (
                    ('thor', ['thor'], {('thor', str(out)): ('FivePocket-Thor-Edition-1.2.0.apk', '')}),
                    ('single screen', ['fold'], {('fold', str(out)): ('FivePocket-1.2.0.apk', 'fivepocket-v1922.bundle')}),
                    ('both', ['fold', 'thor'], {('fold', str(out / 'Single screen')): ('FivePocket-1.2.0.apk', 'fivepocket-v1922.bundle'),
                                                ('thor', str(out / 'Thor Edition')): ('FivePocket-Thor-Edition-1.2.0.apk', '')})):
                with self.subTest(label):
                    steps = core.make_plan([eds[k] for k in keys], discs, out, Path(tmp) / 'cache', True,
                                           fetched={str(eds['thor'].release_path): True})
                    apps = {(s.words[s.words.index('--edition') + 1], s.words[s.words.index('--out') + 1]): s.out_name
                            for s in steps if s.kind == 'app'}
                    builds = {next(e.key for e in eds.values() if e.ident == s.edition): (s.words[s.words.index('--out') + 1], s.out_name)
                              for s in steps if s.kind == 'build'}
                    self.assertEqual(apps, {k: v[0] for k, v in want.items()})
                    self.assertEqual({k: v for k, v in builds.items()}, {k[0]: (k[1], v[1]) for k, v in want.items()})

    def test_an_app_not_fetched_leaves_the_bundle_and_says_what_to_download(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / 'out'
            out.mkdir()
            bundle = out / 'fivepocket-v1922.bundle'
            bundle.write_bytes(b'bundle')
            base = dict(root=tmp, release=str(Path(tmp) / 'release.json'), edition='r#fold', edition_label='FivePocket single screen (phones)',
                        edition_kind='bundle')
            build = core.Step('build:r#fold', 'build', 'Build', words=[], state='done', result={'path': str(bundle), 'size': 6, 'sha256': '0' * 64},
                              **base)
            app = core.Step('app:r#fold', 'app', 'Get the app', words=['app', '--edition', 'fold', '--out', str(out)],
                            out_name='FivePocket-1.2.0.apk', **base)
            session = core.Session(Path(tmp) / 'state', {'play': tmp, 'install': tmp}, {}, out, Path(tmp) / 'cache', [build, app])
            advice = 'could not download it (offline). The bundle is not affected. Download FivePocket-1.2.0-android.zip from https://x'
            script = f'print("APP NOT FETCHED: {advice}"); raise SystemExit(3)'
            log = []
            runner = core.Runner(session, events=lambda e: log.append(e.data.get('text', '')) if e.kind == 'line' else None,
                                 command=lambda step: [sys.executable, '-c', script])
            self.assertTrue(runner.run())
            outs = session.outputs()
            self.assertEqual([(o['kind'], Path(o['path']).name) for o in outs], [('bundle', 'fivepocket-v1922.bundle'), ('apk', 'FivePocket-1.2.0.apk')])
            self.assertIn('Download FivePocket-1.2.0-android.zip', outs[1]['missing'])
            script_ok = f'open(r"{out / "FivePocket-1.2.0.apk"}", "wb").write(b"apk"); print("Your app is there")'
            app.state = 'pending'
            runner = core.Runner(session, command=lambda step: [sys.executable, '-c', script_ok])
            self.assertTrue(runner.run())
            got = session.outputs()[1]
            self.assertEqual((got['kind'], got['size'], 'missing' in got), ('apk', 3, False))

    def test_an_unknown_edition_word_is_refused_by_the_stager_and_ignored_by_the_window(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = package(Path(tmp) / 'base')
            with self.assertRaises(stage_gui.StageError):
                stage_gui.stage(base, Path(tmp) / 'out', edition='ios')
            (base / 'edition.txt').write_text('ios\n')
            self.assertEqual(opens_as(base), 'thor')


if __name__ == '__main__':
    unittest.main()
