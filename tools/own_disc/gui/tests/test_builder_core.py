"""Host tests of the GUI builder's logic layer (tools/own_disc/gui/builder_core.py). No game data: the discs, the
release and the pipeline are the synthetic fixtures of tools/own_disc/tests (a folder or XISO "disc", a profile whose
stages are a tiny logging script). The window (app.py) is not imported here: everything it decides lives in the core.

What is checked at the connections, not only per function:
- the commands the GUI runs are the commands the guided CLI runs (captured from builder.guided itself),
- a bundle built through the GUI's runner (a real builder process) is byte-identical to the CLI's for the same inputs,
- cancel kills the running process and resume continues from that step (the engine's receipts skip finished stages).
"""
from __future__ import annotations

import contextlib
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock
import zipfile

REPO = Path(__file__).resolve().parents[4]
OWN_DISC = REPO / 'tools' / 'own_disc'
SCRATCH = REPO / 'local-analysis' / 'test-tmp'          # the builder writes game-derived files only under local-analysis/
sys.path.insert(0, str(OWN_DISC))
sys.path.insert(0, str(OWN_DISC / 'gui'))
import builder as B
import disc_builder as db
import builder_core as core


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec); sys.modules[name] = mod; spec.loader.exec_module(mod)
    return mod


os.environ['DISC_BUILDER'] = str(OWN_DISC / 'disc_builder.py')
fixture = load('gui_core_fixtures', OWN_DISC / 'tests' / 'test_disc_builder.py')


class Synthetic(unittest.TestCase):
    """A synthetic builder package (release.json + payload + profile) and a fetched compiler cache."""

    def setUp(self):
        SCRATCH.mkdir(parents=True, exist_ok=True)
        self.tmp = tempfile.TemporaryDirectory(prefix='gui-core space-', dir=SCRATCH)
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.archive = self.root / 'ndk-fixture.zip'
        with zipfile.ZipFile(self.archive, 'w') as z:
            z.writestr('android-ndk-fixture/bin/compiler.txt', b'SYNTHETIC compiler, not executable')
        self.ndk = dict(url='https://dl.google.com/android/repository/android-ndk-fixture.zip',
                        sha256=db.sha256_file(self.archive), size=self.archive.stat().st_size,
                        directory='android-ndk-fixture', unpacked_bytes=4096,
                        license_url='https://developer.android.com/ndk/downloads')
        self.cache = self.root / 'cache'

    def fetch(self, release):
        with contextlib.redirect_stdout(io.StringIO()):
            B.fetch(release, self.cache, True, self.archive)

    def package(self, name='fixture', profile_id='fixture', profiles=None, host=None, change=None):
        """the fixture's side folder as a builder package; returns (fixture, release path). change(fx) edits the profile."""
        (self.root / name).mkdir()
        fx = fixture.Fixture(self.root / name)
        for stage in fx.profile['stages']:
            if stage['adapter'] == 'command':
                stage['argv'][0] = '${python}'
        fx.stage('shaders')['source'] = '${repo}/pack.bin'
        fx.profile['id'] = profile_id
        if change:
            change(fx)
        fx.profile_path.write_text(json.dumps(fx.profile))
        shutil.copyfile(fx.profile_path, fx.side / 'profile.json')
        release = {'ndk': self.ndk, 'schema': 'builder-release/1', 'host': host or B.host_id(), 'profile': 'profile.json',
                   'estimates': {'min_free_bytes': 1, 'build_time': 'synthetic fixture only'}}
        if profiles:
            release['profiles'] = profiles
        release['files'] = [dict(path=p.relative_to(fx.side).as_posix(), sha256=db.sha256_file(p))
                            for p in fx.side.rglob('*') if p.is_file()]
        path = fx.side / 'release.json'
        path.write_text(json.dumps(release))
        self.fetch(release)
        return fx, path

    def install_disc(self):
        d = self.root / 'install disc'
        d.mkdir(exist_ok=True)
        (d / 'readme.txt').write_bytes(b'synthetic install disc')
        return d


class Editions(Synthetic):
    def test_a_package_builds_its_own_edition_and_lists_the_others_with_where_they_are_made(self):
        # owner 1.2.1: each Android edition builds its own code from its own download; every window lists all four editions
        for name, profile_id, own, other in (('thor', 'fivepocket-thor-edition-v852-3', 'thor', 'fold'), ('fold', 'fivepocket-v1944-3', 'fold', 'thor')):
            with self.subTest(own):
                fx, release = self.package(name, profile_id=profile_id)
                found = core.discover_editions([fx.side])
                self.assertEqual([(e.key, e.kind, e.target) for e in found if e.available], [(own, 'bundle', None)])
                greyed = [e for e in found if e.key == other]
                self.assertEqual([(e.kind, e.available, e.root) for e in greyed], [('bundle', False, None)])
                self.assertIn('in its own download', greyed[0].reason)
                self.assertEqual(sorted(e.key for e in found), ['duo', 'fold', 'ios', 'thor'])
        fx, release = self.package('apple-check', profile_id='fivepocket-thor-edition-v852-3')
        found = core.discover_editions([fx.side])
        ios = [e for e in found if e.kind == 'ipa']
        self.assertTrue(ios and not any(e.available for e in ios), found)       # never offered as buildable here
        self.assertEqual(sorted(e.key for e in ios), ['duo', 'ios'])
        self.assertIn('Mac', ios[0].reason)

    def test_a_single_screen_profile_is_the_single_screen_edition(self):
        fx, release = self.package(profile_id='fivepocket-v718-3')
        ready = [e for e in core.discover_editions([fx.side]) if e.available]
        self.assertEqual([(e.key, e.target) for e in ready], [('fold', None)])

    def test_a_mac_package_offers_each_ios_target_with_its_build_switch(self):
        fx, release = self.package(profiles={'ios': 'profile.json', 'duo': 'profile.json'})
        ready = [e for e in core.discover_editions([fx.side]) if e.available]
        self.assertEqual(sorted((e.key, e.kind, e.target) for e in ready), [('duo', 'ipa', 'duo'), ('ios', 'ipa', 'ios')])

    def test_a_package_for_another_computer_is_listed_but_not_offered(self):
        fx, release = self.package(host='win32-x86_64' if B.host_id() != 'win32-x86_64' else 'darwin-arm64')
        found = [e for e in core.discover_editions([fx.side]) if e.release_path == release]
        self.assertTrue(found)
        self.assertFalse(any(e.available for e in found))
        self.assertIn('another computer', found[0].reason)

    def test_packages_beside_each_other_are_found_from_their_parent_folder(self):
        a, _ = self.package('thor', profile_id='fivepocket-thor-edition-v852-3')
        b, _ = self.package('fold', profile_id='fivepocket-v718-3')
        parent = self.root / 'unpacked'
        parent.mkdir()
        shutil.move(str(a.side), parent / 'FivePocket-Builder-Windows-Thor-Edition')
        shutil.move(str(b.side), parent / 'FivePocket-Builder-Windows-Single-Screen')
        ready = [e for e in core.discover_editions([parent]) if e.available]
        self.assertEqual(sorted(e.key for e in ready), ['fold', 'thor'])             # each package its own edition: both build
        self.assertFalse([e for e in core.discover_editions([parent]) if e.kind == 'bundle' and not e.available])


class Discs(Synthetic):
    def setUp(self):
        super().setUp()
        self.fx, self.release = self.package()
        self.games = self.fx.profile['games']

    def test_the_play_disc_is_recognised_by_its_executable_hash(self):
        r = core.check_disc(self.fx.disc, 'play', self.games)
        self.assertTrue(r.ok, r.problems)
        self.assertEqual(r.role, 'play')
        self.assertIn('Fake Game', r.recognised)
        self.assertIn(hashlib.sha256(fixture.XEX).hexdigest()[:12], r.recognised)

    def test_an_xiso_image_is_read_and_its_size_shown(self):
        iso = self.root / 'Disc 2.iso'
        fixture.make_xiso(iso, {'default.xex': fixture.XEX})
        r = core.check_disc(iso, 'play', self.games)
        self.assertTrue(r.ok, r.problems)
        self.assertEqual(r.size, iso.stat().st_size)
        self.assertTrue(any('smaller than a full disc image' in w for w in r.warnings), r.warnings)

    def test_a_file_that_is_no_xbox_disc_is_refused_with_the_reason(self):
        junk = self.root / 'holiday.iso'
        junk.write_bytes(b'\0' * 200000)
        r = core.check_disc(junk, 'play', self.games)
        self.assertFalse(r.ok)
        self.assertIn('no Xbox disc file system', ' '.join(r.problems))
        missing = core.check_disc(self.root / 'nope.iso', 'install', self.games)
        self.assertFalse(missing.ok)
        self.assertIn('Not found', ' '.join(missing.problems))

    def test_an_unsupported_game_version_names_what_was_found(self):
        other = self.root / 'other'
        other.mkdir()
        (other / 'default.xex').write_bytes(fixture.OTHER_XEX)
        r = core.check_disc(other, 'play', self.games)
        self.assertFalse(r.ok)
        self.assertIn('Unsupported game version', ' '.join(r.problems))

    def test_each_disc_in_the_wrong_slot_is_refused_and_placing_one_finds_its_slot(self):
        install = self.install_disc()
        self.assertFalse(core.check_disc(self.fx.disc, 'install', self.games).ok)
        self.assertIn('Disc 2 (Play)', ' '.join(core.check_disc(self.fx.disc, 'install', self.games).problems))
        self.assertFalse(core.check_disc(install, 'play', self.games).ok)
        self.assertEqual(core.place_disc({'play': None, 'install': None}, install, self.games), 'install')
        self.assertEqual(core.place_disc({'play': None, 'install': install}, self.fx.disc, self.games), 'play')

    def test_the_same_image_in_both_slots_is_refused(self):
        problems = core.check_pair({'play': self.fx.disc, 'install': self.fx.disc})
        self.assertTrue(problems)
        self.assertEqual(core.check_pair({'play': self.fx.disc, 'install': self.install_disc()}), [])


class Plan(Synthetic):
    def setUp(self):
        super().setUp()
        self.fx, self.release = self.package()
        self.edition = [e for e in core.discover_editions([self.fx.side]) if e.available][0]
        self.discs = {'play': self.fx.disc, 'install': self.install_disc()}

    def guided_commands(self, answers, cache):
        """the builder.main argument lists the guided CLI runs for these typed answers (main captured, not run)"""
        ran = []
        left = list(answers)

        def ask(prompt):
            if not left:
                raise EOFError
            return left.pop(0)
        with mock.patch.object(B, 'main', side_effect=lambda argv: ran.append(argv) or 0), \
                contextlib.redirect_stdout(io.StringIO()):
            B.guided(self.release, ask=ask, cache=cache)
        return ran

    def test_the_gui_runs_the_commands_the_guided_cli_runs(self):
        out = B.default_work(self.fx.side) / 'out'
        guided = self.guided_commands([str(self.discs['play']), str(self.discs['install']), '', ''], self.cache)
        plan = core.make_plan([self.edition], self.discs, out, self.cache, licences_accepted=False, app_icon=core.APP_ICON_DEFAULT)
        gui = [core.builder_words(s, self.cache) for s in plan if s.kind != 'verify']
        self.assertEqual(gui, guided)
        self.assertEqual([s.kind for s in plan], ['check', 'build', 'verify'])

    def test_unfetched_compilers_need_the_licences_and_then_fetch_first_as_the_cli_does(self):
        empty = self.root / 'fresh cache'
        out = B.default_work(self.fx.side) / 'out'
        with self.assertRaises(core.PlanError) as refused:
            core.make_plan([self.edition], self.discs, out, empty, licences_accepted=False)
        self.assertIn(self.ndk['license_url'], str(refused.exception))
        guided = self.guided_commands([str(self.discs['play']), str(self.discs['install']), '', '', 'y'], empty)
        plan = core.make_plan([self.edition], self.discs, out, empty, licences_accepted=True, app_icon=core.APP_ICON_DEFAULT)
        self.assertEqual([core.builder_words(s, empty) for s in plan if s.kind != 'verify'], guided)
        self.assertEqual(plan[0].kind, 'fetch')
        self.assertEqual(core.licence_terms([self.edition])[0]['url'], self.ndk['license_url'])

    def test_an_output_folder_in_a_git_checkout_is_refused_before_anything_runs(self):
        problems = core.check_output(REPO / 'tools' / 'not-a-private-folder')
        self.assertTrue(problems and 'git repository' in problems[0], problems)
        self.assertEqual(core.check_output(self.root / 'my bundles'), [])
        self.assertFalse((REPO / 'tools' / 'not-a-private-folder').exists())

    def test_two_editions_check_build_and_verify_each(self):
        fx2, _ = self.package('second', profile_id='fivepocket-thor-edition-v852-3')
        second = [e for e in core.discover_editions([fx2.side]) if e.available][0]
        plan = core.make_plan([self.edition, second], self.discs, self.root / 'out', self.cache, licences_accepted=False)
        self.assertEqual([s.kind for s in plan], ['check', 'build', 'verify'] * 2)
        self.assertEqual({s.release for s in plan[:3]}, {str(self.release)})

    def test_the_app_icon_pick_goes_to_every_build_with_its_edition(self):
        fx2, _ = self.package('thor', profile_id='fivepocket-thor-edition-v852-3')         # 1.2.1: each edition its own download
        fx4, _ = self.package('fold', profile_id='fivepocket-v1944-3')
        fx3, _ = self.package('apple', profiles={'ios': 'profile.json', 'duo': 'profile.json'})
        editions = [e for e in core.discover_editions([fx2.side, fx4.side, fx3.side]) if e.available]
        self.assertEqual(sorted(e.key for e in editions), ['duo', 'fold', 'ios', 'thor'])
        self.assertEqual(core.APP_ICON_CHOICES[0], 'pink-blue-dusk')                          # the owner's default comes first
        for colourway in core.APP_ICON_CHOICES:
            with self.subTest(colourway=colourway):
                plan = core.make_plan(editions, self.discs, self.root / 'out', self.cache, licences_accepted=False, app_icon=colourway)
                builds = {s.edition: s.words for s in plan if s.kind == 'build'}
                self.assertEqual(len(builds), 4)
                for e in editions:
                    self.assertEqual(builds[e.ident][-4:], ['--app-icon', colourway, '--edition', e.key])
                self.assertFalse(any('--app-icon' in s.words for s in plan if s.kind != 'build'))
        plain = core.make_plan(editions, self.discs, self.root / 'out', self.cache, licences_accepted=False)
        self.assertFalse(any('--app-icon' in s.words for s in plain))
        with self.assertRaises(core.PlanError):
            core.make_plan(editions, self.discs, self.root / 'out', self.cache, licences_accepted=False, app_icon='sunrise')

    def test_the_players_title_update_and_dlc_go_to_the_editions_whose_profiles_take_them(self):
        def updated_game(fx):
            game = fx.profile['games'][0]
            game['title_update'] = {'label': 'Fake update 2', 'executable_sha256': game['executable_sha256'],
                                    'executable_size': game['executable_size'], 'files': [{'path': 'default.xexp', 'size': 1, 'sha256': '0' * 64}]}
            game['dlc'] = {'title_id': '12345678'}
        fx2, _ = self.package('updated', change=updated_game)
        updated = [e for e in core.discover_editions([fx2.side]) if e.available][0]
        self.assertEqual((updated.update, updated.dlc), ('needed', True))
        self.assertEqual((self.edition.update, self.edition.dlc), ('', False))                # the 1.0 profile takes neither
        update, dlc = self.root / 'tu00000009_00000000', self.root / 'my dlc'
        with self.assertRaises(core.PlanError) as refused:
            core.make_plan([updated], self.discs, self.root / 'out', self.cache, licences_accepted=False, extras={'dlc': dlc})
        self.assertIn('Choose your Title update in the sidebar', str(refused.exception))
        plan = core.make_plan([updated, self.edition], self.discs, self.root / 'out', self.cache, licences_accepted=False,
                              extras={'update': update, 'dlc': dlc})
        builds = {s.edition: s.words for s in plan if s.kind == 'build'}
        self.assertEqual(builds[updated.ident][-4:], ['--title-update', str(update), '--dlc', str(dlc)])
        self.assertFalse({'--title-update', '--dlc'} & set(builds[self.edition.ident]))

    def test_title_update_profiles_in_release_json_enables_optional_update_and_dlc(self):
        fx, release_path = self.package('dual-tu')
        tu_prof = json.loads((fx.side / 'profile.json').read_text())
        tu_prof['id'] = 'fixture-tu27'
        tu_prof['games'][0]['title_update'] = {'label': 'TU 27', 'executable_sha256': tu_prof['games'][0]['executable_sha256'],
                                               'xexp_member': 'disc002/default.xexp', 'xexp_sha256': 'a' * 64}
        tu_prof['games'][0]['dlc'] = {'title_id': '12345678'}
        tu_prof['stages'].append({'id': 'tu-only-stage', 'adapter': 'command', 'argv': ['${python}', '-c', 'pass']})
        (fx.side / 'profile-tu.json').write_text(json.dumps(tu_prof))
        rel = json.loads(release_path.read_text())
        rel['title_update_profiles'] = {'default': 'profile-tu.json'}
        rel['files'].append({'path': 'profile-tu.json', 'sha256': db.sha256_file(fx.side / 'profile-tu.json')})
        release_path.write_text(json.dumps(rel))

        ed = [e for e in core.discover_editions([fx.side]) if e.available][0]
        self.assertEqual((ed.update, ed.dlc), ('taken', True))
        self.assertIn('tu-only-stage', ed.tu_stages)
        self.assertTrue(ed.dlc_needs_update)

        update, dlc = self.root / 'tu00000009_00000000', self.root / 'my dlc'
        # 1.0 build with no extras still works and uses base stages
        plan_10 = core.make_plan([ed], self.discs, self.root / 'out10', self.cache, licences_accepted=False)
        b10 = next(s for s in plan_10 if s.kind == 'build')
        self.assertFalse({'--title-update', '--dlc'} & set(b10.words))
        self.assertNotIn('tu-only-stage', b10.stages)
        # DLC without Title Update refused cleanly when DLC is only in the TU profile
        with self.assertRaises(core.PlanError) as refused:
            core.make_plan([ed], self.discs, self.root / 'outdlc', self.cache, licences_accepted=False, extras={'dlc': dlc})
        self.assertIn('Choose your Title update in the sidebar', str(refused.exception))
        # TU + DLC passes both flags and shows TU stages
        plan_tu = core.make_plan([ed], self.discs, self.root / 'outtu', self.cache, licences_accepted=False,
                                 extras={'update': update, 'dlc': dlc})
        btu = next(s for s in plan_tu if s.kind == 'build')
        self.assertEqual(btu.words[-4:], ['--title-update', str(update), '--dlc', str(dlc)])
        self.assertIn('tu-only-stage', btu.stages)

    def test_the_window_says_early_when_a_title_update_or_dlc_pick_is_the_wrong_file(self):
        tu = self.root / 'tu00000009_00000000'
        fixture.stfs_package(tu)
        packs = self.root / 'my dlc'
        packs.mkdir()
        fixture.stfs_package(packs / 'ABC123', content_type=2, metadata_version=2)
        (packs / 'DLC-INDEX.txt').write_text('index')
        self.assertEqual(core.check_extra('update', tu), ('Title-update package, 1 files', []))
        self.assertEqual(core.check_extra('update', packs)[1], [])                           # an unpacked update folder
        what, problems = core.check_extra('update', packs / 'ABC123')                        # a DLC pack picked as the update
        self.assertIn('Not a title-update package', problems[0])
        self.assertTrue(core.check_extra('dlc', packs)[0].startswith('1 pack, '))
        self.assertIn('No DLC pack here', core.check_extra('dlc', packs / 'DLC-INDEX.txt')[1][0])
        empty = self.root / 'empty'
        empty.mkdir()
        self.assertIn('No DLC pack here', core.check_extra('dlc', empty)[1][0])


class FakeProcess:
    """stands for a builder process: prints its lines, then exits with code; 'hang' waits until killed"""

    def __init__(self, lines, code=0, hang=False):
        self.lines, self.returncode, self.hang = lines, None, hang
        self._code, self.killed = code, threading.Event()
        self.pid = 4242
        self.stdout = self._read()

    def _read(self):
        for line in self.lines:
            yield line + '\n'
        while self.hang and not self.killed.is_set():
            time.sleep(0.01)

    def wait(self, timeout=None):
        self.returncode = -9 if self.killed.is_set() else self._code
        return self.returncode

    def poll(self):
        return self.returncode


class Runner(Synthetic):
    def setUp(self):
        super().setUp()
        self.fx, self.release = self.package()
        self.edition = [e for e in core.discover_editions([self.fx.side]) if e.available][0]
        self.discs = {'play': self.fx.disc, 'install': self.install_disc()}
        self.state = self.root / 'gui state'
        self.out = self.root / 'out'

    def session(self):
        plan = core.make_plan([self.edition], self.discs, self.out, self.cache, licences_accepted=False)
        return core.Session.new(self.state, self.discs, self.out, self.cache, plan)

    def runner(self, session, script, events=None):
        """a runner whose processes come from script: {step kind: FakeProcess factory}; records what was started"""
        started = []

        def popen(argv, **kw):
            kind = next(k for k in ('fetch', 'check', 'build', 'verify') if k in argv)
            started.append((kind, argv))
            return script[kind]()
        r = core.Runner(session, events=events or (lambda e: None), popen=popen, kill=lambda p: p.killed.set())
        return r, started

    def last_run(self, bundle):
        work = B.default_work(self.fx.side)
        work.mkdir(parents=True, exist_ok=True)
        bundle.parent.mkdir(parents=True, exist_ok=True)
        bundle.write_bytes(b'synthetic bundle')
        (work / 'last-run.json').write_text(json.dumps({'exit_code': 0, 'bundle': {'path': str(bundle), 'seal': 'ab' * 32, 'size': 16}}))

    def test_steps_run_in_order_and_the_verify_step_checks_the_bundle_the_build_wrote(self):
        bundle = self.out / 'code.bundle'
        events = []
        s = self.session()
        r, started = self.runner(s, {'check': lambda: FakeProcess(['ok']),
                                     'build': lambda: (self.last_run(bundle), FakeProcess(['Stages:', '  extract          ran         (x)']))[1],
                                     'verify': lambda: FakeProcess(['code.bundle: verified.'])}, events.append)
        self.assertTrue(r.run())
        self.assertEqual([k for k, _ in started], ['check', 'build', 'verify'])
        self.assertEqual(started[2][1][-2:], ['verify', str(bundle)])
        self.assertEqual([st.state for st in s.steps], ['done'] * 3)
        out = s.outputs()
        self.assertEqual(out[0]['path'], str(bundle))
        self.assertEqual(out[0]['sha256'], hashlib.sha256(b'synthetic bundle').hexdigest())
        self.assertEqual(out[0]['seal'], 'ab' * 32)
        self.assertIn(('stage', 'extract', 'ran'), [(e.kind, e.data.get('stage'), e.data.get('status')) for e in events if e.kind == 'stage'])
        again = core.Session.load(self.state)                              # what was saved is what was done
        self.assertEqual([st.state for st in again.steps], ['done'] * 3)

    def test_a_failed_step_stops_the_plan_and_nothing_after_it_runs(self):
        s = self.session()
        r, started = self.runner(s, {'check': lambda: FakeProcess(['REFUSED: not enough free disk'], code=2),
                                     'build': lambda: self.fail('build ran after a failed check')})
        self.assertFalse(r.run())
        self.assertEqual([st.state for st in s.steps], ['failed', 'pending', 'pending'])
        self.assertEqual(s.steps[0].exit_code, 2)
        self.assertIn('not enough free disk', s.steps[0].last_lines[-1])

    def test_cancel_kills_the_running_build_and_resume_continues_from_it(self):
        s = self.session()
        holder = {}

        def hanging():
            holder['p'] = FakeProcess(['Stages:', '  extract          ran         (x)'], hang=True)
            return holder['p']
        r, started = self.runner(s, {'check': lambda: FakeProcess(['ok']), 'build': hanging})
        t = threading.Thread(target=r.run)
        t.start()
        for _ in range(500):
            if 'p' in holder:
                break
            time.sleep(0.01)
        r.cancel()
        t.join(10)
        self.assertFalse(t.is_alive())
        self.assertTrue(holder['p'].killed.is_set())
        self.assertEqual([st.state for st in s.steps], ['done', 'cancelled', 'pending'])
        resumed = core.Session.load(self.state)
        self.assertEqual(resumed.resume_problems(), [])
        bundle = self.out / 'code.bundle'
        r2, started2 = self.runner(resumed, {'build': lambda: (self.last_run(bundle), FakeProcess(['done']))[1],
                                             'verify': lambda: FakeProcess(['verified.'])})
        self.assertTrue(r2.run())
        self.assertEqual([k for k, _ in started2], ['build', 'verify'])         # the finished check is not run again

    def test_resume_is_refused_when_a_disc_changed_since(self):
        s = self.session()
        s.save()
        (self.discs['install'] / 'readme.txt').write_bytes(b'another disc entirely, longer')
        os.utime(self.discs['install'], ns=(1, 1))
        problems = core.Session.load(self.state).resume_problems()
        self.assertTrue(problems and 'Disc 1 (Install)' in problems[0], problems)

    def test_stage_lines_become_stage_states(self):
        lines = ['Stages:', '  extract          skipped     (receipt 1234)', '  decode           ran         (x; 3 s)',
                 '  codegen          FAILED      boom', '  bundle           written     /x/y.bundle (9 bytes)']
        self.assertEqual([core.parse_stage(l) for l in lines],
                         [None, ('extract', 'skipped'), ('decode', 'ran'), ('codegen', 'FAILED'), ('bundle', 'written')])


class RealPipeline(Synthetic):
    """The runner starting real builder processes on the synthetic package (the CLI's own front end, as builder.exe
    starts it), compared with the CLI run in-process."""

    def test_the_gui_build_is_byte_identical_to_the_cli_build_and_cancel_resume_keeps_receipts(self):
        fx, release = self.package()
        install = self.install_disc()
        discs = {'play': fx.disc, 'install': install}
        out = B.default_work(fx.side) / 'out'
        argv = ['--release', str(release), '--cache', str(self.cache), 'build',
                '--play-disc', str(fx.disc), '--install-disc', str(install), '--out', str(out)]
        with contextlib.redirect_stdout(io.StringIO()) as said:
            self.assertEqual(B.main(argv), 0, said.getvalue())
        cli = fx.bundle() if fx.bundle().is_file() else next(out.glob('*.bundle'))
        cli_bytes = cli.read_bytes()
        shutil.rmtree(B.default_work(fx.side))                        # a fresh start: nothing reused from the CLI run
        executed_before = len(fx.executed())

        edition = [e for e in core.discover_editions([fx.side]) if e.available][0]
        plan = core.make_plan([edition], discs, out, self.cache, licences_accepted=False)
        session = core.Session.new(self.root / 'state', discs, out, self.cache, plan)
        lines = []
        runner = core.Runner(session, events=lambda e: lines.append(e.data.get('text', '')) if e.kind == 'line' else None)
        self.assertTrue(runner.run(), '\n'.join(lines))
        made = Path(session.outputs()[0]['path'])
        self.assertEqual(made.read_bytes(), cli_bytes)
        self.assertEqual(session.outputs()[0]['sha256'], hashlib.sha256(cli_bytes).hexdigest())
        self.assertTrue(any(': verified.' in l for l in lines), lines)
        self.assertEqual(fx.executed()[executed_before:], ['decode', 'codegen', 'link'])

        # a second GUI run of the same plan reuses every receipt (what resume relies on): no stage runs again
        session2 = core.Session.new(self.root / 'state2', discs, out, self.cache,
                                    core.make_plan([edition], discs, out, self.cache, licences_accepted=False))
        self.assertTrue(core.Runner(session2).run())
        self.assertEqual(fx.executed()[executed_before:], ['decode', 'codegen', 'link'])
        self.assertEqual(made.read_bytes(), cli_bytes)

    def test_the_windows_app_icon_reaches_each_android_bundle_as_the_command_line_s_does(self):
        # 1.2.1: the two Android downloads unpacked side by side, each edition built from its own package
        fx, rel_thor = self.package('thor', profile_id='fivepocket-thor-edition-v852-3')
        fx_fold, rel_fold = self.package('fold', profile_id='fivepocket-v1944-3')
        discs = {'play': fx.disc, 'install': self.install_disc()}
        out = B.default_work(fx.side) / 'out'
        twins = [e for e in core.discover_editions([fx.side, fx_fold.side]) if e.available]
        self.assertEqual(sorted(e.key for e in twins), ['fold', 'thor'])
        plan = core.make_plan(twins, discs, out, self.cache, licences_accepted=False, app_icon='golden-sunset')
        session = core.Session.new(self.root / 'state', discs, out, self.cache, plan)
        lines = []
        self.assertTrue(core.Runner(session, events=lambda e: lines.append(e.data.get('text', '')) if e.kind == 'line' else None).run(),
                        '\n'.join(lines))
        made = {o['edition'].split(' (')[0]: Path(o['path']) for o in session.outputs() if o['kind'] == 'bundle'}
        self.assertEqual(len(made), 2)
        for e in twins:
            bundle = next(p for label, p in made.items() if label == e.label.split(' (')[0])
            word = {'thor': 'thor', 'fold': 'android'}[e.key]
            self.assertEqual(db.verify_bundle(bundle)['app_icon'], f'{word}-golden-sunset')
            cli_out = self.root / f'cli-{e.key}'
            argv = ['--release', str({'thor': rel_thor, 'fold': rel_fold}[e.key]), '--cache', str(self.cache), 'build', '--play-disc', str(fx.disc),
                    '--install-disc', str(discs['install']), '--out', str(cli_out), '--app-icon', 'golden-sunset', '--edition', e.key]
            with contextlib.redirect_stdout(io.StringIO()) as said:
                self.assertEqual(B.main(argv), 0, said.getvalue())
            self.assertEqual(next(cli_out.glob('*.bundle')).read_bytes(), bundle.read_bytes())

    def test_the_windows_dlc_reaches_the_bundle_as_the_command_line_s_does(self):
        packs = self.root / 'my dlc'
        packs.mkdir()
        fixture.stfs_package(packs / 'ABC123', content_type=2, metadata_version=2)

        def takes_dlc(fx):
            fixture.BundleFormat2.second(None, fx)
            fx.profile['games'][0]['dlc'] = {'title_id': '12345678'}
        fx, release = self.package(change=takes_dlc)
        discs = {'play': fx.disc, 'install': self.install_disc()}
        out = B.default_work(fx.side) / 'out'
        edition = [e for e in core.discover_editions([fx.side]) if e.available][0]
        plan = core.make_plan([edition], discs, out, self.cache, licences_accepted=False, extras={'dlc': packs})
        session = core.Session.new(self.root / 'state', discs, out, self.cache, plan)
        lines = []
        self.assertTrue(core.Runner(session, events=lambda e: lines.append(e.data.get('text', '')) if e.kind == 'line' else None).run(),
                        '\n'.join(lines))
        made = Path(session.outputs()[0]['path'])
        self.assertEqual([c['name'] for c in db.verify_bundle(made)['components'] if c['role'] == 'dlc-package'], ['dlc/abc123'])
        cli_out = self.root / 'cli'
        argv = ['--release', str(release), '--cache', str(self.cache), 'build', '--play-disc', str(fx.disc),
                '--install-disc', str(discs['install']), '--out', str(cli_out), '--dlc', str(packs)]
        with contextlib.redirect_stdout(io.StringIO()) as said:
            self.assertEqual(B.main(argv), 0, said.getvalue())
        self.assertEqual(next(cli_out.glob('*.bundle')).read_bytes(), made.read_bytes())

    def test_cancel_stops_a_real_builder_process(self):
        fx, release = self.package()
        install = self.install_disc()
        discs = {'play': fx.disc, 'install': install}
        slow = fx.side / 'slow.py'                                      # a check that takes long: python sleeping
        edition = [e for e in core.discover_editions([fx.side]) if e.available][0]
        plan = core.make_plan([edition], discs, self.root / 'out', self.cache, licences_accepted=False)
        session = core.Session.new(self.root / 'state', discs, self.root / 'out', self.cache, plan)
        slow.write_text('import time\nprint("waiting", flush=True)\ntime.sleep(60)\n')
        runner = core.Runner(session, command=lambda step: [sys.executable, str(slow)])
        t = threading.Thread(target=runner.run)
        started = time.monotonic()
        t.start()
        time.sleep(1.0)
        runner.cancel()
        t.join(20)
        self.assertFalse(t.is_alive())
        self.assertLess(time.monotonic() - started, 20)
        self.assertEqual(session.steps[0].state, 'cancelled')


if __name__ == '__main__':
    unittest.main()
