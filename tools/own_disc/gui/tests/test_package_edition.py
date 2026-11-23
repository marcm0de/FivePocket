"""1.2.1 (owner): each Windows builder download builds its own edition from its own profile (the single-screen download a
single-screen profile, the Thor Edition download the Thor-line one) and opens its window on it; stage_gui.py --edition also
writes the edition into the package (edition.txt, outside the builder's inventory). Two downloads unpacked side by side
build both editions, each bundle with its APK in its own folder. Synthetic packages only.

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


PROFILES = {   # edition: (profile file, profile id, bundle name), as the 1.2.1 downloads carry them
    'thor': ('profile-thor-fivepocket-thor-v1922-3n-ui.json', 'gta5-disc-thor-fivepocket-thor-v1922-3-portable', 'fivepocket-thor-edition-v1922.bundle'),
    'fold': ('profile-fivepocket-v1922-3n-ui.json', 'gta5-disc-fivepocket-v1922-3-portable', 'fivepocket-v1922.bundle')}
APPS = {'fold': ('FivePocket-1.2.0-android.zip', 'FivePocket-1.2.0-android/FivePocket-1.2.0.apk', 'FivePocket single screen'),
        'thor': ('FivePocket-Thor-Edition-1.2.0-android.zip', 'FivePocket-Thor-Edition-1.2.0-android/FivePocket-Thor-Edition-1.2.0.apk',
                 'FivePocket Thor Edition')}


def package(folder: Path, edition: str = 'thor') -> Path:
    """a builder package as the release lane makes them: release.json with its own edition's APK pin + its own profile"""
    folder.mkdir(parents=True)
    zip_name, member, label = APPS[edition]
    pin = {'url': f'https://github.com/o/FivePocket/releases/download/v1.2.0/{zip_name}',
           'page': 'https://github.com/o/FivePocket/releases/tag/v1.2.0', 'app': label,
           'sha256': '1' * 64, 'size': 1, 'member': member, 'member_sha256': '2' * 64, 'member_size': 1}
    rel, pid, bundle = PROFILES[edition]
    (folder / 'release.json').write_text(json.dumps({'schema': 'builder-release/1', 'host': B.host_id(), 'profile': rel,
                                                     'apps': {edition: pin}}))
    (folder / rel).write_text(json.dumps({'id': pid, 'stages': [{'id': 'extract-xex'}], 'games': [], 'bundle': {'name': bundle}}))
    return folder


def side_by_side(tmp: Path) -> Path:
    """the Thor Edition and single-screen downloads unpacked into one folder"""
    parent = tmp / 'downloads'
    for word, name in (('thor', 'FivePocket-Builder-Windows-Thor-Edition'), ('fold', 'FivePocket-Builder-Windows-Single-Screen')):
        stage_gui.stage(package(tmp / f'base-{word}', word), parent / name, edition=word)
    return parent


def discs_in(tmp: Path) -> dict:
    discs = {'play': tmp / 'play.iso', 'install': tmp / 'install.iso'}
    for d in discs.values():
        d.write_bytes(b'x')
    return discs


def opens_as(folder: Path) -> str:
    """the edition the window selects first for this package alone (AppState.chosen: the available editions, in order)"""
    return next(e.key for e in core.discover_editions([folder]) if e.available)


class PackageEdition(unittest.TestCase):
    def test_each_download_opens_on_its_own_edition(self):
        with tempfile.TemporaryDirectory() as tmp:
            thor = Path(tmp) / 'FivePocket-Builder-Windows-Thor-Edition'
            single = Path(tmp) / 'FivePocket-Builder-Windows-Single-Screen'
            stage_gui.stage(package(Path(tmp) / 'base-thor', 'thor'), thor, edition='thor')
            stage_gui.stage(package(Path(tmp) / 'base-fold', 'fold'), single, edition='fold')
            self.assertEqual(opens_as(thor), 'thor')
            self.assertEqual(opens_as(single), 'fold')           # the window's ANDROID word and android logo (launcher.js)
            for base, word in ((Path(tmp) / 'base-thor', 'thor'), (Path(tmp) / 'base-fold', 'fold')):
                self.assertEqual(opens_as(base), word, 'a package without the file keeps the profile-derived edition')
            for out, word in ((thor, 'thor'), (single, 'fold')):
                added = json.loads((out / 'gui-manifest.json').read_text())['added']
                self.assertIn('edition.txt', [a['path'] for a in added])
                self.assertEqual((out / 'edition.txt').read_bytes(), word.encode() + b'\n')

    def test_each_tick_combination_puts_its_apk_beside_its_bundle(self):
        # owner 1.2.1: two downloads side by side build both editions, each from its own package (one check each), each
        # bundle with its APK in its own folder; one edition alone writes both files into the output folder
        with tempfile.TemporaryDirectory() as tmp:
            discs = discs_in(Path(tmp))
            eds = {e.key: e for e in core.discover_editions([side_by_side(Path(tmp))]) if e.available}
            self.assertEqual(sorted(eds), ['fold', 'thor'])
            self.assertNotEqual(eds['thor'].release_path, eds['fold'].release_path)
            out = Path(tmp) / 'out'
            fetched = {str(e.release_path): True for e in eds.values()}
            for label, keys, want in (
                    ('thor', ['thor'], {('thor', str(out)): 'FivePocket-Thor-Edition-1.2.0.apk'}),
                    ('single screen', ['fold'], {('fold', str(out)): 'FivePocket-1.2.0.apk'}),
                    ('both', ['thor', 'fold'], {('thor', str(out / 'Thor Edition')): 'FivePocket-Thor-Edition-1.2.0.apk',
                                                ('fold', str(out / 'Single screen')): 'FivePocket-1.2.0.apk'})):
                with self.subTest(label):
                    steps = core.make_plan([eds[k] for k in keys], discs, out, Path(tmp) / 'cache', True, fetched=fetched)
                    self.assertEqual([s.kind for s in steps], ['check', 'build', 'verify', 'app'] * len(keys))
                    apps = {(s.words[s.words.index('--edition') + 1], s.words[s.words.index('--out') + 1]): s.out_name
                            for s in steps if s.kind == 'app'}
                    builds = {(next(e.key for e in eds.values() if e.ident == s.edition), s.words[s.words.index('--out') + 1]): s.root
                              for s in steps if s.kind == 'build'}
                    self.assertEqual(apps, want)
                    self.assertEqual(builds, {k: str(eds[k[0]].root) for k in want})     # each edition from its own package

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
            base = package(Path(tmp) / 'base', 'thor')
            with self.assertRaises(stage_gui.StageError):
                stage_gui.stage(base, Path(tmp) / 'out', edition='ios')
            (base / 'edition.txt').write_text('ios\n')
            self.assertEqual(opens_as(base), 'thor')


if __name__ == '__main__':
    unittest.main()
