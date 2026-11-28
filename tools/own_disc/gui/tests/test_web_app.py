"""Host tests of the window's server (tools/own_disc/gui/web_app.py) over builder_core: what the window is told, what its
clicks do, and that only the window (its token, the loopback address) is answered. Synthetic packages and discs (the
fixtures of test_builder_core.py); no game data, no browser."""
from __future__ import annotations

import http.client
import json
import shutil
from pathlib import Path
import sys
import threading
import time
import unittest
import unittest.mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_builder_core as t           # noqa: E402  the synthetic package fixtures
import web_app                          # noqa: E402


def wait(app, test, seconds=20):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if test(app.snapshot()):
            return app.snapshot()
        time.sleep(0.05)
    raise AssertionError('timed out; last state: ' + json.dumps(app.snapshot())[:1500])


class Window(t.Synthetic):
    def setUp(self):
        super().setUp()
        self.fx, self.release = self.package(profile_id='fivepocket-thor-edition-v852-3')
        self.install = self.install_disc()
        self.gui_root = self.root / 'gui root'
        self.gui_root.mkdir()
        self.picked = []
        self.answers = []
        cache = self.cache
        self.patch = unittest.mock.patch.object(web_app.core.B, 'default_cache', lambda: cache)
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def app(self, **kw):
        def choose(title, folder, *image_filter):
            self.picked.append(title)
            return self.answers.pop(0) if self.answers else None
        return web_app.AppState([str(self.fx.side)], choose=choose, gui_root=self.gui_root, backdrop_dirs=[self.root / 'bd'], **kw)

    def test_the_build_button_waits_for_both_discs_then_builds_and_shows_the_hashes(self):
        app = self.app()
        s = wait(app, lambda s: s['compilers'] != 'checking')
        self.assertFalse(s['primary']['enabled'])
        self.assertIn('Disc 2 (Play)', s['ready_problems'][0])
        self.assertEqual([e['short'] for e in s['editions'] if e['shown'] and e['available']], ['FivePocket Thor Edition'])
        self.assertEqual([e['short'] for e in s['editions'] if e['shown'] and not e['available']],
                         ['FivePocket single screen', 'FivePocket iOS', 'FivePocket Duo'])           # 1.2.1: listed, greyed
        self.assertEqual([e['short'] for e in s['editions'] if e['chosen']], ['FivePocket Thor Edition'])
        self.answers = [str(self.fx.disc)]
        app.act('choose_disc', {'slot': 'play'})
        self.answers = [str(self.install)]
        app.act('choose_disc', {'slot': 'install'})
        s = wait(app, lambda s: s['primary']['enabled'])
        self.assertTrue(s['discs']['play']['ok'] and s['discs']['install']['ok'])
        self.assertEqual(s['primary']['action'], 'start')
        app.act('start', {})
        s = wait(app, lambda s: not s['running'] and s['session'] and s['session']['finished'], 60)
        self.assertEqual([st['state'] for st in s['session']['steps']], ['done'] * 3)
        out = s['outputs'][0]
        self.assertEqual(len(out['sha256']), 64)
        self.assertTrue(out['verified'])
        self.assertTrue(any(': verified.' in line for line in app.log_since(0)['lines']))

    def test_too_little_free_space_holds_the_build_with_the_numbers(self):
        app = self.app()
        self.answers = [str(self.fx.disc), str(self.install)]
        app.act('choose_disc', {'slot': 'play'})
        app.act('choose_disc', {'slot': 'install'})
        wait(app, lambda s: s['primary']['enabled'])
        with unittest.mock.patch.object(web_app.shutil, 'disk_usage', lambda p: unittest.mock.Mock(free=0)):
            s = app.snapshot()
        self.assertFalse(s['primary']['enabled'])
        self.assertFalse(s['space']['enough'])
        self.assertIn('Not enough free space', s['ready_problems'][-1])

    def test_large_windows_cluster_size_holds_the_build_in_the_window(self):
        app = self.app()
        self.answers = [str(self.fx.disc), str(self.install)]
        app.act('choose_disc', {'slot': 'play'})
        app.act('choose_disc', {'slot': 'install'})
        wait(app, lambda s: s['primary']['enabled'])
        with unittest.mock.patch.object(web_app.core.B, 'windows_cluster_bytes', return_value=1024 * 1024):
            s = app.snapshot()
        self.assertFalse(s['primary']['enabled'])
        self.assertTrue(any('1024 KB allocation units' in p and 'NTFS drive' in p for p in s['ready_problems']), s['ready_problems'])

    def test_a_failed_step_is_reported_once_with_the_builder_s_own_words(self):
        app = self.app()
        self.answers = [str(self.fx.disc), str(self.install)]
        app.act('choose_disc', {'slot': 'play'})
        app.act('choose_disc', {'slot': 'install'})
        wait(app, lambda s: s['primary']['enabled'])
        (self.fx.side / 'step.py').rename(self.fx.side / 'step-gone.py')   # a payload file goes missing: the builder refuses
        app.act('start', {})
        s = wait(app, lambda s: not s['running'] and s['session'] and any(st['state'] == 'failed' for st in s['session']['steps']), 60)
        self.assertIsNotNone(s['failure'])
        self.assertIn('RESUME', s['failure']['text'])
        self.assertEqual(app.snapshot()['failure']['key'], s['failure']['key'])     # the same failure keeps its key
        self.assertEqual(s['primary']['action'], 'resume')

    def test_a_logo_file_beside_the_window_takes_the_logo_box(self):
        logos = self.root / 'web'
        logos.mkdir()
        app = self.app(logo_dir=logos)
        self.assertIsNone(app.snapshot()['logo'])                                   # the drawn lockup
        (logos / 'logo.png').write_bytes(b'png')
        self.assertEqual(app.snapshot()['logo'], 'logo.png')
        (logos / 'logo.svg').write_bytes(b'<svg/>')
        self.assertEqual(app.snapshot()['logo'], 'logo.svg')                        # a vector wins over a picture

    def test_logo_files_are_chosen_by_edition_and_backdrop(self):
        web = self.root / 'web'
        (web / 'logos').mkdir(parents=True)
        (self.root / 'bd').mkdir()
        for name in ('pink-blue-dusk.jpg', 'golden-sunset.jpg'):
            (self.root / 'bd' / name).write_bytes(b'x')
        for name in ('five-pocket-thor-pink-blue-dusk.png', 'five-pocket-thor-golden-sunset.png', 'five-pocket-ios.png',
                     'five-pocket-duo-unknown-backdrop.png', 'notes.txt', 'five-pocket-nothing.png'):
            (web / 'logos' / name).write_bytes(b'x')
        logos = self.app(logo_dir=web).snapshot()['logos']
        self.assertEqual(logos['thor'], {'pink-blue-dusk': 'logos/five-pocket-thor-pink-blue-dusk.png',
                                         'golden-sunset': 'logos/five-pocket-thor-golden-sunset.png'})
        self.assertEqual(logos['ios'], {'*': 'logos/five-pocket-ios.png'})                # every backdrop
        self.assertNotIn('duo', logos)                                                    # names no backdrop of ours

    def test_button_pictures_are_reported_by_word(self):
        web = self.root / 'web'
        web.mkdir()
        self.assertEqual(self.app(logo_dir=web).snapshot()['button_art'], {})            # the drawn button
        for name in ('button-build.png', 'button-blank.png', 'button-other.png'):
            (web / name).write_bytes(b'x')
        self.assertEqual(self.app(logo_dir=web).snapshot()['button_art'], {'build': 'button-build.png', 'blank': 'button-blank.png'})

    def test_a_disc_put_in_the_wrong_slot_moves_to_its_own(self):
        app = self.app()
        self.answers = [str(self.fx.disc)]
        app.act('choose_disc', {'slot': 'install'})
        s = wait(app, lambda s: s['discs']['play'] and not s['discs']['play']['checking'])
        self.assertTrue(s['discs']['play']['ok'])
        self.assertIsNone(s['discs']['install'])

    def test_backdrops_are_listed_by_name_and_the_choice_is_kept(self):
        (self.root / 'bd').mkdir()
        for name in ('la-dusk.jpg', 'la-sunset.jpg', 'notes.txt'):
            (self.root / 'bd' / name).write_bytes(b'x')
        app = self.app()
        s = app.snapshot()
        self.assertEqual([b['name'] for b in s['backdrops']], ['LA Dusk', 'LA Sunset'])
        self.assertEqual(s['backdrop'], 'la-dusk')
        app.act('set_backdrop', {'id': 'la-sunset'})
        self.assertEqual(self.app().snapshot()['backdrop'], 'la-sunset')         # a new window remembers it
        app.act('set_backdrop', {'id': '../../etc'})
        self.assertEqual(app.snapshot()['backdrop'], 'la-sunset')

    def test_the_app_icon_pick_starts_on_pink_blue_dusk_is_kept_and_goes_to_the_build(self):
        web = self.root / 'web'
        (web / 'logos').mkdir(parents=True)
        for name in ('five-pocket-thor-peach-rose.png', 'five-pocket-android-golden-sunset.png'):
            (web / 'logos' / name).write_bytes(b'x')
        app = self.app(logo_dir=web)
        s = app.snapshot()
        self.assertEqual(s['app_icons'], [{'id': 'pink-blue-dusk', 'name': 'Pink/Blue Dusk'}, {'id': 'golden-sunset', 'name': 'Golden Sunset'},
                                          {'id': 'peach-rose', 'name': 'Peach/Rose'}])
        self.assertEqual(s['app_icon'], 'pink-blue-dusk')
        self.assertEqual(s['logos']['thor'], {'peach-rose': 'logos/five-pocket-thor-peach-rose.png'})   # previews without backdrops
        app.act('set_app_icon', {'id': 'peach-rose'})
        app.act('set_app_icon', {'id': '../../etc'})
        self.assertEqual(app.snapshot()['app_icon'], 'peach-rose')
        again = self.app(logo_dir=web)
        self.assertEqual(again.snapshot()['app_icon'], 'peach-rose')                  # a new window remembers it
        again.discs = {'play': self.fx.disc, 'install': self.install}
        again.fetched = {str(self.release): True}
        builds = [s.words for s in again.plan() if s.kind == 'build']
        self.assertTrue(builds)
        self.assertTrue(all(w[-4:-1] == ['--app-icon', 'peach-rose', '--edition'] for w in builds), builds)

    def test_title_update_and_dlc_fields_show_for_an_edition_that_takes_them_and_reach_its_build(self):
        app = self.app()
        self.assertEqual([x['shown'] for x in app.snapshot()['extras']], [False, False])     # a 1.0 profile takes neither
        fx2, _ = self.package('updated', change=lambda fx: fx.profile['games'][0].update(
            title_update={'label': 'Fake update 2', 'executable_sha256': fx.profile['games'][0]['executable_sha256'],
                          'executable_size': 1, 'files': [{'path': 'default.xexp', 'size': 1, 'sha256': '0' * 64}]},
            dlc={'title_id': '12345678'}))
        app = web_app.AppState([str(fx2.side)], choose=lambda title, folder, *f: self.answers.pop(0), gui_root=self.gui_root,
                               backdrop_dirs=[self.root / 'bd'])
        extras = {x['slot']: x for x in app.snapshot()['extras']}
        self.assertEqual({k: (x['shown'], x['needed']) for k, x in extras.items()}, {'update': (True, True), 'dlc': (True, False)})
        packs = self.root / 'my dlc'
        packs.mkdir()
        t.fixture.stfs_package(packs / 'ABC123', content_type=2, metadata_version=2)
        self.answers += [str(packs / 'ABC123'), str(packs)]                     # a DLC pack picked as the update, then the packs
        app.act('choose_extra', {'slot': 'update'})
        app.act('choose_extra', {'slot': 'dlc', 'folder': True})
        extras = {x['slot']: x for x in app.snapshot()['extras']}
        self.assertIn('Not a title-update package', extras['update']['problems'][0])
        self.assertEqual(extras['dlc']['problems'], [])
        app.discs = {'play': fx2.disc, 'install': self.install}
        app.fetched = {str(fx2.side / 'release.json'): True}
        self.assertTrue(any(p.startswith('Title update: Not a title-update package') for p in app.ready_problems()), app.ready_problems())
        tu = self.root / 'tu00000009_00000000'
        t.fixture.stfs_package(tu)
        self.answers.append(str(tu))
        app.act('choose_extra', {'slot': 'update'})
        builds = [s.words for s in app.plan() if s.kind == 'build']
        self.assertEqual(builds[0][-4:], ['--title-update', str(tu), '--dlc', str(packs)])
        app.act('clear_extra', {'slot': 'dlc'})
        self.assertNotIn('--dlc', [s.words for s in app.plan() if s.kind == 'build'][0])

    def test_title_update_profiles_in_release_json_show_optional_extras_in_state(self):
        fx2, _ = self.package('dual')
        tu_prof = json.loads(json.dumps(fx2.profile))
        tu_prof['games'][0].update(
            title_update={'label': 'Fake update 27', 'executable_sha256': fx2.profile['games'][0]['executable_sha256'],
                          'executable_size': 1, 'files': [{'path': 'default.xexp', 'size': 1, 'sha256': '0' * 64}]},
            dlc={'title_id': '12345678'})
        tu_path = fx2.side / 'profile-tu.json'
        tu_path.write_text(json.dumps(tu_prof), encoding='utf-8')
        rel_path = fx2.side / 'release.json'
        rel = json.loads(rel_path.read_text(encoding='utf-8'))
        rel['files'].append({'path': 'profile-tu.json', 'sha256': t.core.B.db.sha256_file(tu_path)})
        rel['title_update_profiles'] = {'default': 'profile-tu.json'}
        rel_path.write_text(json.dumps(rel), encoding='utf-8')
        app = web_app.AppState([str(fx2.side)], choose=lambda title, folder, *f: self.answers.pop(0), gui_root=self.gui_root,
                               backdrop_dirs=[self.root / 'bd'])
        extras = {x['slot']: x for x in app.snapshot()['extras']}
        self.assertEqual({k: (x['shown'], x['needed']) for k, x in extras.items()},
                         {'update': (True, False), 'dlc': (True, False)})
        # without a title update chosen, a 1.0 build still plans cleanly
        app.discs = {'play': fx2.disc, 'install': self.install}
        app.fetched = {str(rel_path): True}
        self.assertEqual(app.ready_problems(), [])
        # picking DLC alone without a title update blocks build with a clear problem
        packs = self.root / 'my dlc2'
        packs.mkdir()
        t.fixture.stfs_package(packs / 'ABC123', content_type=2, metadata_version=2)
        self.answers.append(str(packs))
        app.act('choose_extra', {'slot': 'dlc', 'folder': True})
        self.assertTrue(any('title update' in p.lower() for p in app.ready_problems()), app.ready_problems())
        # adding the title update unblocks and passes both flags
        tu = self.root / 'tu00000027_00000000'
        t.fixture.stfs_package(tu)
        self.answers.append(str(tu))
        app.act('choose_extra', {'slot': 'update'})
        self.assertEqual(app.ready_problems(), [])
        builds = [s.words for s in app.plan() if s.kind == 'build']
        self.assertEqual(builds[0][-4:], ['--title-update', str(tu), '--dlc', str(packs)])

    def test_a_window_opens_on_its_own_packages_edition_beside_another_package(self):
        fold, _ = self.package('single', profile_id='fivepocket-v1944-3')
        thor, _ = self.package('thor2', profile_id='fivepocket-thor-edition-v852-3')
        parent = self.root / 'unpacked'                                            # two downloads unpacked side by side
        parent.mkdir()
        roots = {'fold': parent / 'FivePocket-Builder-Windows-Single-Screen', 'thor': parent / 'FivePocket-Builder-Windows-Thor-Edition'}
        shutil.move(str(fold.side), roots['fold'])
        shutil.move(str(thor.side), roots['thor'])
        for own, root in roots.items():
            with self.subTest(own):
                s = web_app.AppState([], gui_root=root, backdrop_dirs=[self.root / 'bd']).snapshot()
                self.assertEqual(sorted(e['key'] for e in s['editions'] if e['available']), ['fold', 'thor'])   # both build
                self.assertEqual([e['key'] for e in s['editions'] if e['chosen']], [own])

    def test_only_the_window_with_its_token_is_answered(self):
        app = self.app()
        token = 'secret-token'
        server = web_app.Server(('127.0.0.1', 0), web_app.make_handler(app, token))
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.shutdown)
        port = server.server_address[1]

        def get(path, headers=None, method='GET', body=None):
            c = http.client.HTTPConnection('127.0.0.1', port, timeout=10)
            c.request(method, path, body=body, headers=headers or {})
            r = c.getresponse()
            return r.status, r.read()
        self.assertEqual(get('/api/state')[0], 403)
        self.assertIsNone(app.last_seen)
        status, body = get('/api/state', {'X-Token': token})
        self.assertEqual(status, 200)
        self.assertIsNotNone(app.last_seen)                                           # a poll keeps the server alive
        self.assertIn('primary', json.loads(body))
        self.assertEqual(get('/api/action', {'X-Token': 'wrong'}, 'POST', b'{"name":"start"}')[0], 403)
        self.assertEqual(get('/index.html')[0], 200)                                  # the page itself needs no token
        self.assertEqual(get('/../web_app.py')[0], 404)
        self.assertEqual(get('/%2e%2e/web_app.py')[0], 404)
        self.assertEqual(server.server_address[0], '127.0.0.1')


    def test_the_server_ends_when_the_window_stops_polling_and_a_second_start_finds_the_first(self):
        self.assertFalse(web_app.window_gone(None, 0, web_app.GRACE - 1))             # the window may take a while to come
        self.assertTrue(web_app.window_gone(None, 0, web_app.GRACE + 1))              # it never came
        self.assertFalse(web_app.window_gone(100, 0, 100 + web_app.GONE - 1))         # it polls
        self.assertTrue(web_app.window_gone(100, 0, 100 + web_app.GONE + 1))          # it stopped polling: closed
        app = self.app()
        state_dir = self.gui_root / 'state'
        state_dir.mkdir()
        self.assertIsNone(web_app.running_instance(state_dir))                         # nothing recorded
        server = web_app.Server(('127.0.0.1', 0), web_app.make_handler(app, 'tok'))
        threading.Thread(target=server.serve_forever, daemon=True).start()
        port = server.server_address[1]
        (state_dir / web_app.WINDOW_FILE).write_text(json.dumps({'port': port, 'token': 'tok'}))
        self.assertEqual(web_app.running_instance(state_dir), f'http://127.0.0.1:{port}/index.html?t=tok')
        (state_dir / web_app.WINDOW_FILE).write_text(json.dumps({'port': port, 'token': 'not-its-token'}))
        self.assertIsNone(web_app.running_instance(state_dir))                         # another program on that port
        server.shutdown()
        server.server_close()
        (state_dir / web_app.WINDOW_FILE).write_text(json.dumps({'port': port, 'token': 'tok'}))
        self.assertIsNone(web_app.running_instance(state_dir))                         # it ended


if __name__ == '__main__':
    unittest.main()
