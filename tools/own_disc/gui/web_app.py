"""FivePocket Builder: the window's local server. The window is web/index.html shown by the system's own browser
engine (Windows: Microsoft Edge in app mode, which every Windows 10/11 has; macOS: the default browser; Linux: Edge, Chrome
or Chromium in app mode when installed, else the default browser); this server
answers it on 127.0.0.1 only, with a random token, and holds the state. Every decision and every command is
builder_core.py's: the same checks and the same builder commands the guided command line runs.

  python web_app.py [--packages DIR]... [DISC_IMAGE]...      (started by "FivePocket Builder.exe")
  python web_app.py --state-json [--packages DIR]... [DISC]   (prints the window's state once; no server)

Nothing is sent anywhere: the server listens on the loopback address, and the only download is the builder's own fetch
step after the player accepted the compilers' licences. Links (Discord, X, GitHub) open in the player's browser only
when clicked.
"""
from __future__ import annotations

import ctypes
import http.client
import http.server
import json
import mimetypes
import os
import re
from pathlib import Path
import secrets
import shutil
import socketserver
import subprocess
import sys
import threading
import time
import urllib.parse
import webbrowser

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import builder_core as core     # noqa: E402

WEB = HERE / 'web'
GUI_ROOT = Path(os.environ.get('BUILDER_PACKAGE_ROOT') or HERE.parents[2])
LINKS = {'discord': 'https://discord.gg/mZvqCsxgxT', 'x': 'https://x.com/marcthescholar',
         'github': 'https://github.com/marcm0de/FivePocket'}
BACKDROP_TYPES = ('.jpg', '.jpeg', '.png', '.webp')
LOGO_NAMES = ('logo.svg', 'logo.png', 'logo.webp')
LOGO_EDITIONS = ('thor', 'ios', 'android', 'duo')        # the editions' words in logo file names (fold builds = android)


def human(n):
    if n is None:
        return '?'
    for unit, size in (('GiB', 1 << 30), ('MiB', 1 << 20), ('KiB', 1 << 10)):
        if n >= size:
            return f'{n / size:.2f} {unit} ({n:,} bytes)'
    return f'{n} bytes'


def fmt_time(seconds):
    seconds = int(seconds or 0)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f'{h} h {m:02d} min' if h else (f'{m} min {s:02d} s' if m else f'{s} s')


def short(label):
    return label.split(' (')[0] if label else ''


# ------------------------------------------------------------------------------------------------- native dialogs

def choose_path(title: str, folder: bool, image_filter: bool = True) -> str | None:
    """the system's own open dialog; None when the player cancels"""
    if os.name == 'nt':
        return _windows_dialog(title, folder, image_filter)
    if sys.platform == 'darwin':
        kind = 'folder' if folder else 'file'
        script = f'POSIX path of (choose {kind} with prompt "{title}")'
        r = subprocess.run(['/usr/bin/osascript', '-e', script], capture_output=True, text=True)
        return r.stdout.strip() or None
    if sys.platform.startswith('linux'):
        return _linux_dialog(title, folder, image_filter)
    return None


def _linux_dialog(title, folder, image_filter):
    """Linux: the desktop's own open dialog through zenity (GNOME and most desktops) or kdialog (KDE); None when the player
    cancels or neither is there (the disc images can then be named on the command line)."""
    if shutil.which('zenity'):
        argv = ['zenity', '--file-selection', f'--title={title}']
        if folder:
            argv.append('--directory')
        elif image_filter:
            argv += ['--file-filter=Xbox 360 disc image (*.iso) | *.iso *.ISO', '--file-filter=All files | *']
    elif shutil.which('kdialog'):
        start = str(Path.home())
        argv = ['kdialog', '--title', title] + (['--getexistingdirectory', start] if folder else
                                                ['--getopenfilename', start] + (['*.iso *.ISO|Xbox 360 disc image (*.iso)'] if image_filter else []))
    else:
        return None
    r = subprocess.run(argv, capture_output=True, text=True)
    path = (r.stdout or '').rstrip('\r\n')
    return path if r.returncode == 0 and path else None


def _windows_dialog(title, folder, image_filter):
    from ctypes import wintypes
    ctypes.windll.ole32.OleInitialize(None)          # this request's thread: the shell's dialogs need COM (STA) there
    owner = ctypes.windll.user32.GetForegroundWindow()   # the window the player just clicked in: the dialog stays over it
    if folder:
        class BROWSEINFOW(ctypes.Structure):
            _fields_ = [('hwndOwner', wintypes.HWND), ('pidlRoot', ctypes.c_void_p), ('pszDisplayName', wintypes.LPWSTR),
                        ('lpszTitle', wintypes.LPCWSTR), ('ulFlags', wintypes.UINT), ('lpfn', ctypes.c_void_p),
                        ('lParam', wintypes.LPARAM), ('iImage', ctypes.c_int)]
        name = ctypes.create_unicode_buffer(260)
        info = BROWSEINFOW(owner, None, name, title, 0x0001 | 0x0040, None, 0, 0)  # RETURNONLYFSDIRS | NEWDIALOGSTYLE
        pidl = ctypes.windll.shell32.SHBrowseForFolderW(ctypes.byref(info))
        if not pidl:
            return None
        path = ctypes.create_unicode_buffer(32768)
        ok = ctypes.windll.shell32.SHGetPathFromIDListW(ctypes.c_void_p(pidl), path)
        ctypes.windll.ole32.CoTaskMemFree(ctypes.c_void_p(pidl))
        return path.value if ok else None

    class OPENFILENAMEW(ctypes.Structure):
        _fields_ = [('lStructSize', wintypes.DWORD), ('hwndOwner', wintypes.HWND), ('hInstance', wintypes.HINSTANCE),
                    ('lpstrFilter', wintypes.LPCWSTR), ('lpstrCustomFilter', wintypes.LPWSTR), ('nMaxCustFilter', wintypes.DWORD),
                    ('nFilterIndex', wintypes.DWORD), ('lpstrFile', wintypes.LPWSTR), ('nMaxFile', wintypes.DWORD),
                    ('lpstrFileTitle', wintypes.LPWSTR), ('nMaxFileTitle', wintypes.DWORD), ('lpstrInitialDir', wintypes.LPCWSTR),
                    ('lpstrTitle', wintypes.LPCWSTR), ('Flags', wintypes.DWORD), ('nFileOffset', wintypes.WORD),
                    ('nFileExtension', wintypes.WORD), ('lpstrDefExt', wintypes.LPCWSTR), ('lCustData', wintypes.LPARAM),
                    ('lpfnHook', ctypes.c_void_p), ('lpTemplateName', wintypes.LPCWSTR), ('pvReserved', ctypes.c_void_p),
                    ('dwReserved', wintypes.DWORD), ('FlagsEx', wintypes.DWORD)]
    buffer = ctypes.create_unicode_buffer(32768)
    flt = 'Xbox 360 disc image (*.iso)\0*.iso\0All files (*.*)\0*.*\0\0' if image_filter else 'All files (*.*)\0*.*\0\0'
    ofn = OPENFILENAMEW()
    ofn.lStructSize = ctypes.sizeof(OPENFILENAMEW)
    ofn.hwndOwner = owner
    ofn.lpstrFilter = flt
    ofn.lpstrFile = ctypes.cast(buffer, wintypes.LPWSTR)
    ofn.nMaxFile = 32768
    ofn.lpstrTitle = title
    ofn.Flags = 0x00001000 | 0x00000800 | 0x00080000        # FILEMUSTEXIST | PATHMUSTEXIST | EXPLORER
    return buffer.value if ctypes.windll.comdlg32.GetOpenFileNameW(ctypes.byref(ofn)) else None


def open_path(path):
    path = Path(path)
    if not path.exists():
        return
    if os.name == 'nt':
        os.startfile(str(path))                              # noqa: S606  the player's own folder
    elif sys.platform == 'darwin':
        subprocess.Popen(['/usr/bin/open', str(path)])
    else:
        subprocess.Popen(['xdg-open', str(path)])


def copy_text(text):
    if os.name == 'nt':
        subprocess.run(['clip'], input=text.encode('utf-16-le'), check=False)
    elif sys.platform == 'darwin':
        subprocess.run(['/usr/bin/pbcopy'], input=text.encode(), check=False)
    elif sys.platform.startswith('linux'):
        argv = linux_clipboard()
        if argv:
            subprocess.run(argv, input=text.encode(), check=False)


def linux_clipboard():
    """Linux: the clipboard program of this session (Wayland: wl-copy; X11: xclip or xsel), or None"""
    choices = ([['wl-copy']] if os.environ.get('WAYLAND_DISPLAY') else []) + [['xclip', '-selection', 'clipboard'], ['xsel', '--clipboard', '--input']]
    return next((argv for argv in choices if shutil.which(argv[0])), None)


# -------------------------------------------------------------------------------------------------------- state

class AppState:
    """everything the window shows; actions change it. Thread-safe: the server and the background work share it."""

    def __init__(self, packages=(), dropped=(), choose=choose_path, backdrop_dirs=None, gui_root=None, logo_dir=None):
        self.lock = threading.RLock()
        self.choose = choose
        root = Path(gui_root or GUI_ROOT)
        self.state_dir = core.state_dir_for(root)
        self.package_paths = [root, root.parent] + [Path(p) for p in packages]
        self.editions = core.discover_editions(self.package_paths)
        self.games = core.games_of([e for e in self.editions if e.available])
        self.chosen = {e.ident: e.available for e in self.editions}
        if sum(self.chosen.values()) > 1:          # 1.2.1: a window opens on its own package's edition, also beside another package
            own = [e for e in self.editions if e.available and e.root and Path(e.root).resolve() == root.resolve()]
            first = own[0] if own else next(e for e in self.editions if e.available)
            self.chosen = {k: k == first.ident for k in self.chosen}
        self.discs = {'play': None, 'install': None}
        self.reports = {'play': None, 'install': None}
        self.checking = set()
        self.hashes, self.hashing = {}, {}
        self.out = core.default_output(self.editions)
        self.out_custom = False
        self.fetched = {}
        self.accepted = False
        self.session = None
        self.runner = None
        self.stages = {}
        self.log = []                     # (step id, line)
        self.backdrop_dirs = backdrop_dirs if backdrop_dirs is not None else [WEB / 'backdrops']
        self.settings_file = self.state_dir / 'gui-settings.json'
        self.settings = self._read_settings()
        self.frameless = False
        self.logo_dir = Path(logo_dir) if logo_dir else WEB
        self.last_seen = None                 # the window's last poll (time.monotonic); None before the first
        self._load_session()
        for path in dropped:
            self.put_disc(Path(path))
        self.measure_compilers()

    # ---- persistence
    def _read_settings(self):
        try:
            return json.loads(self.settings_file.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            return {}

    def _write_settings(self):
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.settings_file.write_text(json.dumps(self.settings, indent=1) + '\n', encoding='utf-8')

    def _load_session(self):
        if not core.Session.exists(self.state_dir):
            return
        try:
            s = core.Session.load(self.state_dir)
        except (OSError, ValueError, TypeError, KeyError):
            return
        self.session = s
        for slot in ('play', 'install'):
            self.put_disc(s.discs[slot], slot, wait=True)
        self.out, self.out_custom = s.out, True
        idents = {st.edition for st in s.steps if st.edition}
        if idents:
            self.chosen = {k: k in idents for k in self.chosen}

    # ---- background work
    def _bg(self, work):
        threading.Thread(target=work, daemon=True).start()

    def put_disc(self, path, slot=None, wait=False):
        path = Path(path)

        def work():
            target = slot
            role = core.check_disc(path, 'play', self.games).role
            other = {'play': 'install', 'install': 'play'}
            with self.lock:
                if target is None:
                    target = core.place_disc(self.discs, path, self.games)
                elif role and role != target and self.discs[other[target]] is None:
                    target = role                          # the guided mode's rule: taken as the disc it is
                self.discs[target] = path
                self.checking.add(target)
            report = core.check_disc(path, target, self.games)
            with self.lock:
                if self.discs[target] == path:
                    self.reports[target] = report
                self.checking.discard(target)
        if wait:
            work()
        else:
            self._bg(work)

    def measure_compilers(self):
        for e in self.selected():
            key = str(e.release_path)
            if key in self.fetched:
                continue
            self.fetched[key] = None

            def work(e=e, key=key):
                value = core.compilers_fetched(e.release_path, core.B.default_cache())
                with self.lock:
                    self.fetched[key] = value
            self._bg(work)

    def hash_disc(self, slot):
        path = self.discs.get(slot)
        if not path or not path.is_file() or str(path) in self.hashes or str(path) in self.hashing:
            return
        key = str(path)
        self.hashing[key] = '0 %'

        def progress(done, total):
            self.hashing[key] = f'{int(done * 100 / max(1, total))} %'

        def work():
            digest = core.image_sha256(path, progress=progress)
            with self.lock:
                self.hashing.pop(key, None)
                if digest:
                    self.hashes[key] = digest
        self._bg(work)

    # ---- derived
    def selected(self):
        return [e for e in self.editions if e.available and self.chosen.get(e.ident)]

    def plan(self):
        if any(self.fetched.get(str(e.release_path)) is None for e in self.selected()):
            raise core.PlanError('Checking the compilers already fetched…')
        return core.make_plan(self.selected(), self.discs, self.out, core.B.default_cache(), self.accepted,
                              fetched={k: v for k, v in self.fetched.items() if v is not None})

    def ready_problems(self):
        problems = [f'Choose {core.DISC_LABELS[s]} in the sidebar' for s in ('play', 'install') if not self.discs[s]]
        if self.checking:
            problems.append('Checking your discs…')
        problems += [f'{core.DISC_LABELS[s]}: {self.reports[s].problems[0]}' for s in ('play', 'install')
                     if self.reports[s] and not self.reports[s].ok]
        space = self.space(self.selected())
        if self.selected() and not space['enough']:              # the check step would refuse: say it before
            problems.append(f'Not enough free space: the build needs {space["required_text"]} on the drive of the builder folder, '
                            f'which has {space["available_text"]} free')
        if not problems:
            try:
                self.plan()
            except core.PlanError as error:
                text = str(error)
                if 'compilers, fetched once' in text:
                    text = 'The compilers are fetched once first: read and accept their licences in SETTINGS.'
                problems.append(text)
        return problems

    def primary(self):
        if self.runner is not None:
            return {'label': 'Cancel', 'action': 'cancel', 'enabled': True}
        if self.session is not None and not self.session.finished and not self.session.resume_problems():
            return {'label': 'Resume', 'action': 'resume', 'enabled': True}
        return {'label': 'Build', 'action': 'start', 'enabled': not self.ready_problems()}

    def logo(self):
        """the player's or publisher's own logo file beside the window (web/logo.svg|png|webp), shown in the logo box
        instead of the drawn FivePocket lockup; None when there is none"""
        for name in LOGO_NAMES:
            if (self.logo_dir / name).is_file():
                return name
        return None

    def logos(self):
        """the player's own logo files in web/logos/, by edition and backdrop: a file whose name holds an edition word
        (thor, ios, android, duo) and, after it, a backdrop's name is shown for that edition on that backdrop; one with
        the edition word alone, on every backdrop. {edition: {backdrop id or '*': 'logos/<file>'}}"""
        found = {}
        folder = self.logo_dir / 'logos'
        if not folder.is_dir():
            return found
        backdrop_ids = {b['id'] for b in self.backdrops()}
        for p in sorted(folder.iterdir()):
            if not p.is_file() or p.suffix.lower() not in ('.svg', '.png', '.webp') or p.name.startswith('.'):
                continue
            words = p.stem.lower().split('-')
            edition = next((w for w in words if w in LOGO_EDITIONS), None)
            if edition is None:
                continue
            rest = '-'.join(words[words.index(edition) + 1:])
            key = rest if rest in backdrop_ids else ('*' if not rest else None)
            if key is not None:
                found.setdefault(edition, {})[key] = 'logos/' + urllib.parse.quote(p.name)
        return found

    def button_art(self):
        """the player's own button pictures beside the window (web/button-build|resume|cancel|blank.png|webp|svg): the
        one for the button's word is shown as it is; without it the blank one, with the word written over it"""
        found = {}
        for word in ('build', 'resume', 'cancel', 'blank'):
            for ext in ('.png', '.webp', '.svg'):
                if (self.logo_dir / f'button-{word}{ext}').is_file():
                    found[word] = f'button-{word}{ext}'
                    break
        return found

    def backdrops(self):
        found = []
        for d in self.backdrop_dirs:
            if Path(d).is_dir():
                for p in sorted(Path(d).iterdir()):
                    if p.suffix.lower() in BACKDROP_TYPES and not p.name.startswith('.'):
                        words = p.stem.replace('_', '-').split('-')
                        name = ' '.join(w.upper() if w.lower() == 'la' else w.capitalize() for w in words if w)
                        found.append({'id': p.stem, 'name': name,
                                      'url': f'/backdrops/{urllib.parse.quote(p.name)}', 'path': str(p)})
        return found

    def snapshot(self):
        with self.lock:
            shown_ipa = any(e.kind == 'ipa' and e.root for e in self.editions)
            discs = {}
            for slot in ('play', 'install'):
                p, r = self.discs[slot], self.reports[slot]
                if p is None:
                    discs[slot] = None
                    continue
                d = {'path': str(p), 'name': p.name, 'checking': slot in self.checking or r is None, 'is_file': p.is_file(),
                     'sha256': self.hashes.get(str(p)), 'hashing': self.hashing.get(str(p))}
                if r is not None and not d['checking']:
                    d.update(ok=r.ok, role=r.role, size=r.size, size_text=human(r.size) if r.size else '', kind=r.kind,
                             recognised=r.recognised, problems=list(r.problems), warnings=list(r.warnings))
                else:
                    d.update(ok=False, problems=[], warnings=[])
                discs[slot] = d
            sel = self.selected()
            states = [self.fetched.get(str(e.release_path)) for e in sel]
            compilers = 'checking' if None in states else ('missing' if False in states else 'ready')
            session, failure = None, None
            if self.session is not None:
                steps = []
                for st in self.session.steps:
                    label = st.title if st.kind == 'fetch' else f'{st.title}: {short(st.edition_label).replace("FivePocket ", "")}'
                    stages = []
                    if st.kind == 'build':
                        seen = [x for x in st.stages if self.stages.get((st.id, x))]
                        for x in st.stages:
                            status = self.stages.get((st.id, x))
                            if status is None and st.state == 'running' and len(seen) == st.stages.index(x):
                                status = 'running'                     # the first stage without a line yet is running
                            stages.append({'id': x, 'status': status})
                    steps.append({'label': label, 'state': st.state, 'time': fmt_time(st.seconds) if st.seconds else '',
                                  'stages': stages})
                build = next((st for st in self.session.steps if st.state == 'running' and st.kind == 'build'), None) or next(
                    (st for st in reversed(self.session.steps) if st.kind == 'build' and st.state != 'pending'), None)
                progress = None
                if build and build.stages:
                    done = list(build.stages) if build.state == 'done' else [
                        x for x in build.stages if self.stages.get((build.id, x)) in ('ran', 'skipped')]
                    current = next((x for x in build.stages if x not in done), None) if build.state == 'running' else None
                    progress = {'done': len(done), 'total': len(build.stages), 'current': current}
                session = {'steps': steps, 'finished': self.session.finished, 'resume_problems': self.session.resume_problems(),
                           'build_progress': progress}
                bad = next((st for st in self.session.steps if st.state == 'failed'), None)
                if bad is not None and self.runner is None:
                    failure = {'key': f'{bad.id}:{self.session.created}:{bad.exit_code}:{len(bad.last_lines)}',
                               'text': f'{bad.title} ({short(bad.edition_label) or "all editions"}) stopped'
                                       + (f' with exit code {bad.exit_code}' if bad.exit_code is not None else '') + '. The builder said:\n\n'
                                       + '\n'.join(bad.last_lines[-10:])
                                       + '\n\nCorrect the cause, then press RESUME: the build goes on from this step.'}
            outputs = []
            for o in (self.session.outputs() if self.session else []):
                outputs.append(dict(o, name=Path(o['path']).name, size_text=human(o.get('size')),
                                    edition=short(o['edition']).replace('FivePocket ', '') or 'Result'))
            bds = self.backdrops()
            chosen_bd = self.settings.get('backdrop') if any(b['id'] == self.settings.get('backdrop') for b in bds) else (bds[0]['id'] if bds else None)
            return {
                'labels': dict(core.DISC_LABELS),
                'discs': discs,
                'editions': [{'ident': e.ident, 'key': e.key, 'label': e.label, 'short': short(e.label), 'kind': e.kind,
                              'available': e.available, 'reason': e.reason, 'chosen': bool(self.chosen.get(e.ident)),
                              'root': str(e.root) if e.root else '', 'problems': core.edition_problems(e) if e.available else [],
                              'shown': e.available or (e.root is None and not shown_ipa),
                              'package': e.root.name if e.root else ''} for e in self.editions],
                'out': str(self.out), 'out_custom': self.out_custom, 'out_problems': core.check_output(self.out),
                'compilers': compilers, 'accepted': self.accepted,
                'licences': [dict(t, mb=round(t['size'] / 1e6)) for t in core.licence_terms([e for e in sel if self.fetched.get(str(e.release_path)) is False])],
                'packages': sorted({str(e.root) for e in self.editions if e.root}),
                'ready_problems': self.ready_problems(), 'primary': self.primary(), 'running': self.runner is not None,
                'session': session, 'outputs': outputs, 'log_file': str(self.state_dir / core.LOG_NAME),
                'backdrops': [{k: b[k] for k in ('id', 'name', 'url')} for b in bds], 'backdrop': chosen_bd,
                'frameless': self.frameless, 'failure': failure, 'space': self.space(sel),
                'version_lines': self.version_lines(), 'logo': self.logo(), 'logos': self.logos(), 'button_art': self.button_art(),
            }

    # ---- actions (names are the window's)
    def act(self, name, args):
        handler = getattr(self, 'do_' + name, None)
        if handler is None:
            raise ValueError(f'unknown action {name}')
        handler(**args)

    def do_choose_disc(self, slot, folder=False):
        if self.runner is not None or slot not in ('play', 'install'):
            return
        path = self.choose(f'{core.DISC_LABELS[slot]}: ' + ('the extracted disc folder' if folder else 'your disc image'), folder)
        if path:
            self.put_disc(Path(path), slot)

    def do_hash_disc(self, slot):
        self.hash_disc(slot)

    def do_toggle_edition(self, ident):
        with self.lock:
            e = next((x for x in self.editions if x.ident == ident), None)
            if e is None or not e.available or self.runner is not None:
                return
            self.chosen[ident] = not self.chosen.get(ident)
            if not self.out_custom:
                self.out = core.default_output(self.selected() or self.editions)
        self.measure_compilers()

    def do_choose_out(self):
        path = self.choose('Where the finished files go', True)
        if path:
            self.out, self.out_custom = Path(path), True

    def do_default_out(self):
        self.out, self.out_custom = core.default_output(self.selected() or self.editions), False

    def do_accept(self, value):
        self.accepted = bool(value)

    def do_set_backdrop(self, id):        # noqa: A002  the window's word
        if any(b['id'] == id for b in self.backdrops()):
            self.settings['backdrop'] = id
            self._write_settings()

    def do_add_package(self):
        path = self.choose('The folder of an unpacked FivePocket builder download', True)
        if not path:
            return
        with self.lock:
            self.package_paths.append(Path(path))
            known = set(self.chosen)
            self.editions = core.discover_editions(self.package_paths)
            for e in self.editions:
                if e.ident not in known:
                    self.chosen[e.ident] = e.available
            self.games = core.games_of([e for e in self.editions if e.available])
        self.measure_compilers()

    def do_start(self):
        with self.lock:
            if self.runner is not None:
                return
            steps = self.plan()
            if self.session is not None:
                self._keep_old_session()
            self.session = core.Session.new(self.state_dir, self.discs, self.out, core.B.default_cache(), steps)
        self._launch()

    def do_resume(self):
        with self.lock:
            if self.runner is not None or self.session is None or self.session.resume_problems():
                return
        self._launch()

    def do_cancel(self):
        if self.runner is not None:
            self._bg(self.runner.cancel)

    def do_start_over(self):
        with self.lock:
            if self.runner is not None:
                return
            if self.session is not None:
                self._keep_old_session()
            self.session = None

    def do_open(self, what, index=0):
        if what == 'log':
            open_path(self.state_dir)
        elif what == 'output' and self.session:
            outs = self.session.outputs()
            if 0 <= index < len(outs):
                open_path(Path(outs[index]['path']).parent)

    def do_open_link(self, key):
        if key in LINKS:
            webbrowser.open(LINKS[key])

    def do_copy_hashes(self):
        outs = self.session.outputs() if self.session else []
        copy_text('\n'.join(f'{o.get("sha256", "")}  {Path(o["path"]).name}' + (f'  seal {o["seal"]}' if o.get('seal') else '')
                            for o in outs))

    def do_window(self, what):
        pass                                                   # the host window (Edge app mode) has its own frame

    def _keep_old_session(self):
        old = self.state_dir / core.SESSION_NAME
        if old.is_file():                                      # kept, renamed: never deleted
            old.replace(old.with_name(f'gui-session-{time.strftime("%Y%m%d-%H%M%S")}.json'))

    def _launch(self):
        self.stages = {}
        self.log = []

        def events(e):
            with self.lock:
                if e.kind == 'stage':
                    self.stages[(e.step, e.data['stage'])] = e.data['status']
                elif e.kind == 'line':
                    self.log.append((e.step, e.data['text']))
                    if len(self.log) > 20000:
                        del self.log[:5000]
                elif e.kind == 'done':
                    self.runner = None
        self.runner = core.Runner(self.session, events=events)
        self._bg(self.runner.run)

    def log_since(self, since):
        with self.lock:
            reset = since > len(self.log)                  # a new build started: the window starts its log again
            since = 0 if reset else since
            return {'lines': [t for s, t in self.log[since:]], 'next': len(self.log), 'reset': reset}

    def space(self, editions):
        """what the build dialog shows: the space the chosen builds need (their releases' own estimate) and what the drive
        of their work folder has free"""
        need = 0
        for e in editions:
            try:
                need = max(need, int(json.loads(Path(e.release_path).read_text(encoding='utf-8')).get('estimates', {}).get('min_free_bytes') or 0))
            except (OSError, ValueError, TypeError):
                pass
        probe = Path(editions[0].root) if editions else Path(self.out)
        while not probe.exists() and probe.parent != probe:
            probe = probe.parent
        free = shutil.disk_usage(probe).free
        gb = lambda n: f'{n / 1e9:.2f} GB'      # noqa: E731  the launcher's own unit (GB REQUIRED / GB AVAILABLE)
        return {'required': need, 'available': free, 'required_text': gb(need), 'available_text': gb(free), 'enough': free >= need}

    HOST_NAMES = {'win32-x86_64': 'Windows x64', 'win32-arm64': 'Windows on ARM', 'darwin-arm64': 'macOS (Apple silicon)',
                  'linux-x86_64': 'Linux x64'}

    def version_lines(self):
        """the sidebar's lines beside the mark: the builder and its version, the edition(s) it makes, this computer; short
        enough for the sidebar (the package folder names are not shown: they ran past it)"""
        ready = [e for e in self.editions if e.root and e.available]
        versions = sorted({m.group(1) for e in ready for m in [re.search(r'-(\d+\.\d+\.\d+)', e.root.name)] if m})
        names = list(dict.fromkeys(short(e.label) for e in ready))
        host = core.B.host_id()
        return ([' '.join(['FivePocket Builder'] + versions[-1:])] + (names[:2] if names else ['no builder package found'])
                + [self.HOST_NAMES.get(host, host)])


# ------------------------------------------------------------------------------------------------------- server

def make_handler(app: AppState, token: str):
    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, code, body: bytes, kind='application/json'):
            self.send_response(code)
            self.send_header('Content-Type', kind)
            self.send_header('Cache-Control', 'no-store')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _authorised(self):
            return secrets.compare_digest(self.headers.get('X-Token', ''), token)

        def do_GET(self):
            url = urllib.parse.urlparse(self.path)
            if url.path == '/api/state':
                if not self._authorised():
                    return self._send(403, b'"forbidden"')
                app.last_seen = time.monotonic()
                return self._send(200, json.dumps(app.snapshot()).encode())
            if url.path == '/api/ping':                        # another start of the program asks whether this one lives
                return self._send(200 if self._authorised() else 403, b'"FivePocket Builder"')
            if url.path == '/api/log':
                if not self._authorised():
                    return self._send(403, b'"forbidden"')
                since = int(urllib.parse.parse_qs(url.query).get('since', ['0'])[0])
                return self._send(200, json.dumps(app.log_since(since)).encode())
            if url.path.startswith('/backdrops/'):
                name = urllib.parse.unquote(url.path[len('/backdrops/'):])
                for b in app.backdrops():
                    if Path(b['path']).name == name:
                        return self._send(200, Path(b['path']).read_bytes(), mimetypes.guess_type(name)[0] or 'image/jpeg')
                return self._send(404, b'"not found"')
            rel = url.path.lstrip('/') or 'index.html'
            target = (WEB / rel).resolve()
            if not target.is_relative_to(WEB.resolve()) or not target.is_file():
                return self._send(404, b'"not found"')
            kind = mimetypes.guess_type(target.name)[0] or 'application/octet-stream'
            if target.suffix == '.svg':
                kind = 'image/svg+xml'
            return self._send(200, target.read_bytes(), kind)

        def do_POST(self):
            if urllib.parse.urlparse(self.path).path != '/api/action' or not self._authorised():
                return self._send(403, b'"forbidden"')
            body = json.loads(self.rfile.read(int(self.headers.get('Content-Length', 0))) or b'{}')
            try:
                app.act(body.get('name', ''), body.get('args') or {})
            except core.PlanError as error:
                return self._send(409, json.dumps(str(error)).encode())
            except (ValueError, TypeError) as error:
                return self._send(400, json.dumps(str(error)).encode())
            return self._send(200, json.dumps(app.snapshot()).encode())
    return Handler


class Server(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True


def edge_path():
    for base in (os.environ.get('ProgramFiles(x86)'), os.environ.get('ProgramFiles'), os.environ.get('LOCALAPPDATA')):
        if base:
            p = Path(base) / 'Microsoft' / 'Edge' / 'Application' / 'msedge.exe'
            if p.is_file():
                return p
    return shutil.which('msedge')


WINDOW_FILE = 'window.json'
GRACE = 90.0          # seconds the first window may take to show up
GONE = 25.0           # seconds without a poll after which the window is taken as closed (it polls every 0.7 s)


def window_gone(last_seen, started, now):
    """the window's lifetime rule: closed when it stopped polling, or never came"""
    if last_seen is None:
        return now - started > GRACE
    return now - last_seen > GONE


def running_instance(state_dir: Path):
    """the address of a window server this player already started, when it still answers; else None"""
    try:
        info = json.loads((state_dir / WINDOW_FILE).read_text(encoding='utf-8'))
        c = http.client.HTTPConnection('127.0.0.1', int(info['port']), timeout=2)
        c.request('GET', '/api/ping', headers={'X-Token': info['token']})
        if c.getresponse().status == 200:
            return f'http://127.0.0.1:{info["port"]}/index.html?t={info["token"]}'
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return None


LINUX_APP_BROWSERS = ('microsoft-edge', 'microsoft-edge-stable', 'google-chrome', 'google-chrome-stable', 'chromium', 'chromium-browser')


def linux_app_browser():
    """Linux: a Chromium-family browser that can show the window in app mode (as Edge does on Windows), or None"""
    return next((shutil.which(name) for name in LINUX_APP_BROWSERS if shutil.which(name)), None)


def show_window_linux(url, state_dir):
    """Linux: Edge, Chrome or Chromium in app mode with its own profile (as Edge on Windows) when one is installed; the default
    browser when none is, or when the one started ends at once with an error (a confined snap browser that may not use the
    profile folder, for example)."""
    browser = linux_app_browser()
    if browser:
        profile = state_dir / 'window-profile'
        profile.mkdir(parents=True, exist_ok=True)
        try:
            process = subprocess.Popen([str(browser), f'--app={url}', '--window-size=1288,760', f'--user-data-dir={profile}',
                                        '--no-first-run', '--no-default-browser-check', '--disable-extensions'])
            try:
                failed = process.wait(timeout=3) != 0
            except subprocess.TimeoutExpired:
                failed = False                                 # still running: the window is up
        except OSError:
            failed = True
        if not failed:
            return
    webbrowser.open(url)


def show_window(url, state_dir):
    """Edge in app mode (a window of its own, its own profile in the builder's private folder), else the browser"""
    if sys.platform.startswith('linux'):
        return show_window_linux(url, state_dir)
    edge = edge_path() if os.name == 'nt' else None
    if edge:
        profile = state_dir / 'window-profile'
        profile.mkdir(parents=True, exist_ok=True)
        subprocess.Popen([str(edge), f'--app={url}', '--window-size=1288,760', f'--user-data-dir={profile}',
                          '--no-first-run', '--no-default-browser-check', '--disable-extensions'])
    else:
        webbrowser.open(url)


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    packages, dropped, once = [], [], False
    while argv:
        a = argv.pop(0)
        if a == '--packages' and argv:
            packages.append(argv.pop(0))
        elif a == '--state-json':
            once = True
        elif os.path.exists(a):
            dropped.append(a)
    state_dir = core.state_dir_for(GUI_ROOT)
    if not once:
        existing = running_instance(state_dir)
        if existing:                                           # started twice: show the window that is already there
            show_window(existing, state_dir)
            return 0
    app = AppState(packages, dropped[:2])
    if once:
        time.sleep(0.5)
        print(json.dumps(app.snapshot(), indent=1))
        return 0
    token = secrets.token_urlsafe(24)
    server = Server(('127.0.0.1', 0), make_handler(app, token))
    port = server.server_address[1]
    url = f'http://127.0.0.1:{port}/index.html?t={token}'
    threading.Thread(target=server.serve_forever, daemon=True).start()
    app.state_dir.mkdir(parents=True, exist_ok=True)
    (app.state_dir / WINDOW_FILE).write_text(json.dumps({'port': port, 'token': token, 'pid': os.getpid()}), encoding='utf-8')
    show_window(url, app.state_dir)
    if os.name != 'nt':
        print(f'FivePocket Builder: {url}\nThis ends by itself when the window is closed (or press Ctrl+C).')
    started = time.monotonic()
    try:
        while not window_gone(app.last_seen, started, time.monotonic()):
            time.sleep(2)
    except KeyboardInterrupt:
        pass
    if app.runner is not None:                                 # the window closed: the build stops and resumes later
        app.runner.cancel()
    server.shutdown()
    try:
        (app.state_dir / WINDOW_FILE).unlink()
    except OSError:
        pass
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
