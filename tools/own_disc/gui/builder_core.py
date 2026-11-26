"""FivePocket Builder (window): the logic layer under the window (app.py). No toolkit is imported here.

It decides nothing the command-line builder does not: it finds the builder packages beside it, checks the player's two
disc images the way the build identifies them, and plans and runs the SAME builder commands the guided command line
runs (builder.py guided(): fetch when the compilers are missing, check, build, verify), each as its own builder process
started the way the package's launcher starts it (builder.exe on Windows, ./builder on macOS). So a bundle or app made
through the window is the command line's, byte for byte (tests/test_builder_core.py compares them).

On top of the command line it keeps a session file (the chosen discs, editions, output folder and each step's state)
so that a build that was cancelled, or a window that was closed, resumes at the step that did not finish; inside the
build step the engine's own receipts skip every stage that already finished.

Python 3.9 or newer (the Mac builder runs on the Python of Xcode, 3.9). No network use: the only download is the
builder's own fetch step, run only after the player accepted the compilers' licences on screen.
"""
from __future__ import annotations

import dataclasses
import datetime
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import signal
import subprocess
import sys
import threading
import time

HERE = Path(__file__).resolve().parent
OWN_DISC = HERE.parent
if str(OWN_DISC) not in sys.path:
    sys.path.insert(0, str(OWN_DISC))
import builder as B                     # noqa: E402  the front end the command line runs (read-only helpers only)
import disc_builder as db               # noqa: E402
import checkout_refusal                 # noqa: E402

SESSION_SCHEMA = 'fivepocket-gui-session/1'
SESSION_NAME = 'gui-session.json'
LOG_NAME = 'gui-build.log'
GIB = 1 << 30
SMALL_IMAGE = 1 * GIB                   # under this an image is surely trimmed or cut off: said, not refused
DISC_LABELS = {'play': 'Disc 2 (Play)', 'install': 'Disc 1 (Install)'}
# the player's own files beyond the discs (next update): their title update (build --title-update) and DLC packs (--dlc)
EXTRA_LABELS = {'update': 'Title update', 'dlc': 'DLC packs'}
EDITION_LABELS = {
    'thor': 'FivePocket Thor Edition (AYN Thor, two screens)',
    'fold': 'FivePocket single screen (Galaxy Fold and other phones)',
    'ios': 'FivePocket iOS (iPhone, iPad)',
    'duo': 'FivePocket Duo (iPad, two screens)',
}
EDITION_ORDER = ('thor', 'fold', 'ios', 'duo')
# 1.2.1 (owner): each Android edition builds its own code from its own download; a window lists every edition, the ones no package
# beside it makes greyed with where they are made (two downloads unpacked side by side build both)
ANDROID_ELSEWHERE = 'in its own download (the FivePocket builder of that edition)'
EDITION_DIRS = {'thor': 'Thor Edition', 'fold': 'Single screen', 'ios': 'iOS', 'duo': 'Duo'}
APPLE_ELSEWHERE = 'on a Mac, with the macOS builder'
APP_NOT_FETCHED = 3            # builder app's exit when the app could not be fetched or checked (the bundle stands)
VERIFY_TARGET = '{bundle}'              # in a verify step's words: the bundle its edition's build step wrote
STAGE_LINE = re.compile(r'^  ([a-z][a-z0-9-]{0,39})\s+(ran|skipped|FAILED|would run|NOT RUN|written)(?:\s|$)')
BUNDLE_ID_LINE = re.compile(r'^Bundle identifier of your (\w+) app: (\S+)')


class PlanError(Exception):
    """A plan the builder would refuse; the message says what to do."""


# ------------------------------------------------------------------------------------------------------- editions

@dataclasses.dataclass(frozen=True)
class Edition:
    key: str                         # thor | fold | ios | duo
    label: str
    kind: str                        # bundle (Android code bundle the app imports) | ipa (unsigned iOS app)
    root: Path | None                # the builder package (the folder holding release.json)
    release_path: Path | None
    target: str | None               # the build switch (--ios, --duo, ...) without dashes; None: the release's default
    available: bool
    reason: str = ''
    stages: tuple = ()               # the profile's stage ids, in order (for the progress view)
    update: str = ''                 # the player's title update: '' not taken, 'taken' (some game of the profile), 'needed' (every game)
    dlc: bool = False                # the profile takes the player's DLC packs

    @property
    def ident(self) -> str:
        return f'{self.release_path}#{self.target or self.key}'


def _read_json(path: Path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def _profile_facts(root: Path, rel: str):
    """(id, stage ids, games) of a profile inside a package; read as data, not validated (the check step does that)"""
    try:
        p = _read_json(B.inside(root, rel))
        return str(p.get('id', '')), tuple(s.get('id', '?') for s in p.get('stages', [])), list(p.get('games', []))
    except (OSError, ValueError, B.Refusal, AttributeError, TypeError):
        return '', (), []


def _key_of_profile(profile_id: str, rel: str) -> str:
    text = f'{profile_id} {rel}'.lower()
    return 'thor' if 'thor' in text else 'fold'


# Next update (owner 2026-11-22): the app icon the player picks in the window (the owner's logo in one of three colourways),
# the guided mode's choices; each build step passes it as builder build --app-icon with --edition
APP_ICON_DEFAULT, APP_ICON_CHOICES, APP_ICON_NAMES = B.APP_ICON_DEFAULT, B.APP_ICON_CHOICES, B.APP_ICON_NAMES

EDITION_FILE = 'edition.txt'   # one word at a package's root, written by stage_gui.py --edition: the edition its window opens as


def _package_edition(root: Path) -> str | None:
    """1.2.0: the edition a package says it is (thor | fold). The Single-Screen and Thor Edition downloads carry the same
    Thor-line profile (one bundle for both apps), so the profile's name alone made both open as the Thor Edition."""
    try:
        word = (root / EDITION_FILE).read_text(encoding='utf-8').strip()
    except OSError:
        return None
    return word if word in ('thor', 'fold') else None


def _package_dirs(paths) -> list:
    seen, found = set(), []
    for p in paths:
        p = Path(p)
        candidates = [p] if (p / 'release.json').is_file() else (sorted(c for c in p.iterdir() if c.is_dir()) if p.is_dir() else [])
        for c in candidates:
            if (c / 'release.json').is_file():
                key = os.path.normcase(str(c.resolve()))
                if key not in seen:
                    seen.add(key)
                    found.append(c)
    return found


def discover_editions(paths) -> list:
    """Every edition the builder packages at these places make (a package folder, or a folder of package folders),
    available or not (with the reason); plus an unavailable iOS entry when no package here makes the iOS app."""
    editions, preferred = [], {}
    here = B.host_id()
    for root in _package_dirs(paths):
        release_path = root / 'release.json'
        try:
            release = _read_json(release_path)
        except (OSError, ValueError) as error:
            editions.append(Edition('fold', f'Builder package in {root.name}', 'bundle', root, release_path, None, False,
                                    f'its release.json cannot be read ({error})'))
            continue
        host = release.get('host')
        reason = '' if host == here else f'this builder package is made for another computer ({host}); this one is {here}'
        if release.get('schema') != 'builder-release/1' or 'profile' not in release:
            reason = 'not a complete builder release (no schema or profile)'
        profiles = release.get('profiles') if isinstance(release.get('profiles'), dict) else {}
        default = release.get('profile', '')
        entries = []                                      # (key, target, profile rel)
        if any(t in B.APP_TARGETS for t in profiles):     # as the guided mode: a choice among the release's targets
            entries = [(t, t, rel) for t, rel in profiles.items()]
        else:
            pid, _, _ = _profile_facts(root, default) if default else ('', (), [])
            key = _key_of_profile(pid, default)
            entries.append((key, None, default))
            preferred[str(root)] = _package_edition(root) or key
            entries += [(t, t, rel) for t, rel in profiles.items() if rel != default]
        for key, target, rel in entries:
            _, stages, games = _profile_facts(root, rel)
            kind = 'ipa' if key in B.APP_TARGETS else 'bundle'
            tu = [isinstance(g, dict) and 'title_update' in g for g in games]
            editions.append(Edition(key, EDITION_LABELS.get(key, key), kind, root, release_path, target, not reason, reason, stages,
                                    'needed' if tu and all(tu) else ('taken' if any(tu) else ''),
                                    any(isinstance(g, dict) and bool(g.get('dlc')) for g in games)))
    for key in ('thor', 'fold'):                    # the Android editions: listed, greyed, where no package here makes them
        if not any(e.key == key and e.kind == 'bundle' for e in editions):
            editions.append(Edition(key, EDITION_LABELS[key], 'bundle', None, None, None, False, ANDROID_ELSEWHERE))
    for key in ('ios', 'duo'):                      # the Apple editions: listed, greyed, where no package here makes them
        if not any(e.key == key and e.kind == 'ipa' and e.available for e in editions):
            editions.append(Edition(key, EDITION_LABELS[key], 'ipa', None, None, None, False, APPLE_ELSEWHERE))
    editions.sort(key=lambda e: (not e.available, e.key != preferred.get(str(e.root)),     # a package's own edition first
                                 EDITION_ORDER.index(e.key) if e.key in EDITION_ORDER else 9, str(e.root)))
    return editions


def games_of(editions) -> list:
    """the supported games (play disc executables) of these editions' profiles"""
    games, seen = [], set()
    for e in editions:
        if e.root is None:
            continue
        release = _read_json(e.release_path)
        rel = B.profile_of(release, e.target) if e.target else release['profile']
        for g in _profile_facts(e.root, rel)[2]:
            key = (g.get('executable', 'default.xex'), g.get('executable_sha256'))
            if key not in seen:
                seen.add(key)
                games.append(g)
    return games


# ---------------------------------------------------------------------------------------------------------- discs

@dataclasses.dataclass
class DiscReport:
    path: Path
    slot: str
    ok: bool = False
    role: str | None = None          # play | install, as the build will take it
    size: int | None = None
    kind: str = ''                   # xiso image | extracted folder
    recognised: str = ''
    problems: list = dataclasses.field(default_factory=list)
    warnings: list = dataclasses.field(default_factory=list)


def check_disc(path, slot: str, games: list) -> DiscReport:
    """Checks one of the player's disc images for its slot (play or install) the way the build will read it: an Xbox
    disc file system, and for the play disc a game executable whose SHA-256 the profile supports."""
    path = Path(path)
    r = DiscReport(path, slot)
    if not (path.is_file() or path.is_dir()):
        r.problems.append(f'Not found: {path}')
        return r
    try:
        disc = db.open_disc(path)
    except (db.BuilderError, OSError) as error:
        r.problems.append(f'{path.name}: cannot be read as an Xbox 360 disc ({error})')
        return r
    if path.is_file():
        r.kind, r.size = 'disc image', path.stat().st_size
        if path.suffix.lower() != '.iso':
            r.warnings.append(f'{path.name} does not end in .iso; it is read as an Xbox disc image anyway')
        if r.size < SMALL_IMAGE:
            r.warnings.append(f'{r.size / GIB:.2f} GiB is smaller than a full disc image: a trimmed image works only if '
                              'no file was cut off')
    else:
        r.kind = 'extracted disc folder'
    game, why = None, ''
    try:
        game = db.identify({'games': games}, disc)
    except db.BuilderError as error:
        why = str(error)
    except (OSError, ValueError) as error:
        r.problems.append(f'{path.name}: reading it failed ({error})')
        return r
    r.role = 'play' if game else 'install'
    if game:
        r.recognised = f'{game.get("label", game.get("id", "?"))}; executable SHA-256 {game["executable_sha256"][:12]}…'
    elif slot == 'install':
        r.recognised = 'a readable Xbox 360 disc that is not the play disc'
    if slot == 'play' and not game:
        if re.search(r'disc_number 1\b', why):            # the install disc's own executable says which disc it is
            r.problems.append(f'This looks like {DISC_LABELS["install"]} (its executable says disc 1); choose {DISC_LABELS["play"]} here')
        else:
            r.problems.append(why if 'Unsupported game version' in why else
                              f'This is not {DISC_LABELS["play"]} of a supported game: {why}')
    elif slot == 'install' and game:
        r.problems.append(f'This is {DISC_LABELS["play"]}; choose {DISC_LABELS["install"]} here')
    r.ok = not r.problems
    return r


def place_disc(discs: dict, path, games: list) -> str:
    """the slot an image goes in when the player drops or picks it without saying which (the guided mode's rule): the
    play disc by its executable, otherwise the install slot; when that slot is taken, the free one"""
    role = check_disc(path, 'play', games).role or 'play'
    if discs.get(role) is not None and discs.get('install' if role == 'play' else 'play') is None and role == 'install':
        return 'install'
    return role


def check_extra(slot: str, path) -> tuple[str, list]:
    """the window's quick look at the player's title update or DLC: (what it is, problems). The build reads them through
    and checks them fully before any stage (disc_builder.py); this only says early when the wrong file was picked."""
    p = Path(path)
    if slot == 'update':
        if p.is_dir():
            return 'A folder of the title update\'s files', []
        try:
            import title_update
            with title_update.TitleUpdatePackage(p) as package:
                count = sum(1 for e in package.entries if not e.directory)
            return f'Title-update package, {count} files', []
        except (ValueError, OSError) as error:
            return '', [f'Not a title-update package: {error}']
    if p.is_dir():
        files = sorted(q for q in p.iterdir() if db.DLC_FILE_NAME.fullmatch(q.name) and q.is_file() and not q.is_symlink())
    else:
        files = [p] if p.is_file() and db.DLC_FILE_NAME.fullmatch(p.name) else []
    if not files:
        return '', ['No DLC pack here: a pack keeps the name the console gave it (letters and digits)']
    if len(files) > db.DLC_MAX:
        return '', [f'{len(files)} packs; the app takes at most {db.DLC_MAX}']
    total = sum(f.stat().st_size for f in files)
    return f'{len(files)} pack{"s" if len(files) > 1 else ""}, {total / 1e6:.0f} MB (each is read through when the build starts)', []


def check_pair(discs: dict) -> list:
    play, install = discs.get('play'), discs.get('install')
    if not play or not install:
        return [f'Choose {DISC_LABELS[s]}' for s in ('play', 'install') if not discs.get(s)]
    try:
        if os.path.samefile(play, install):
            return ['The same image is chosen for both discs: choose Disc 1 (Install) and Disc 2 (Play)']
    except OSError:
        pass
    return []


def image_sha256(path, cancelled=lambda: False, progress=lambda done, total: None) -> str | None:
    """SHA-256 of a whole image, for the player's own records (not compared with anything); None when cancelled"""
    path = Path(path)
    total, done, h = path.stat().st_size, 0, hashlib.sha256()
    with open(path, 'rb') as f:
        while True:
            if cancelled():
                return None
            block = f.read(8 << 20)
            if not block:
                break
            h.update(block)
            done += len(block)
            progress(done, total)
    return h.hexdigest()


# --------------------------------------------------------------------------------------------------------- output

def default_output(editions) -> Path:
    first = next((e for e in editions if e.root is not None), None)
    return B.default_work((first.root if first else HERE.parents[2]).resolve()) / 'out'


def check_output(out) -> list:
    out = Path(out)
    try:
        checkout_refusal.private_output(out.resolve(), 'output folder')
    except checkout_refusal.CheckoutRefusal as error:
        return [str(error)]
    probe = out
    while not probe.exists() and probe.parent != probe:
        probe = probe.parent
    if probe.exists() and not probe.is_dir():
        return [f'{probe} is a file, not a folder']
    if probe.exists() and not os.access(probe, os.W_OK):
        return [f'{probe} cannot be written to']
    return []


def edition_problems(edition) -> list:
    if not edition.available:
        return [edition.reason]
    work = B.default_work(edition.root.resolve())
    if B.windows_path_too_long(work):
        return [f'The builder folder lies too deep for the build on Windows (its build folder would be longer than '
                f'{B.WINDOWS_PATH_LIMIT} characters): {work}. Move the whole unpacked folder to C:\\FivePocket and start '
                'the builder again from there.']
    return []


# ----------------------------------------------------------------------------------------------------------- plan

@dataclasses.dataclass
class Step:
    id: str
    kind: str                        # fetch | check | build | verify
    title: str
    root: str
    release: str
    words: list
    edition: str = ''                # Edition.ident
    edition_label: str = ''
    edition_kind: str = ''
    stages: list = dataclasses.field(default_factory=list)
    state: str = 'pending'           # pending | running | done | failed | cancelled
    exit_code: int | None = None
    seconds: float | None = None
    result: dict | None = None       # a build step's output: path, size, seal or sha256, bundle_id
    out_name: str = ''               # 1.2.0: the file name this edition's output is given (single screen from a Thor-line profile)
    last_lines: list = dataclasses.field(default_factory=list)


def compilers_fetched(release_path, cache) -> bool:
    """the guided mode's test: the pinned NDK in the cache and the shader compiler in the package, hashes checked"""
    release = _read_json(release_path)
    root = Path(release_path).resolve().parent
    try:
        B.existing_ndk(Path(cache).resolve(), B.ndk_spec(release))
        B.existing_dxc(release, root)
        return True
    except B.Refusal:
        return False


def licence_terms(editions) -> list:
    """the compilers the fetch step downloads, each with its publisher's address, size and licence terms"""
    terms, seen = [], set()
    for e in editions:
        if e.release_path is None:
            continue
        release = _read_json(e.release_path)
        for name, spec in (('Android NDK', release.get('ndk')), ('DirectX Shader Compiler', release.get('dxc'))):
            if isinstance(spec, dict) and spec.get('url') not in seen:
                seen.add(spec.get('url'))
                terms.append({'name': name, 'url': spec.get('license_url', ''), 'download': spec.get('url', ''),
                              'size': spec.get('size', 0)})
    return terms


def make_plan(editions, discs: dict, out, cache, licences_accepted: bool, fetched=None, app_icon=None, extras=None) -> list:
    """The steps for these editions, as the guided command line runs them for each: fetch (only when that package's
    compilers are missing, and only after the licences were accepted), check, build, verify (code bundles; the iOS
    builder signs nothing and has no verify step). fetched: {release path: bool} when already measured. app_icon: the
    colourway the player picked (APP_ICON_CHOICES), passed to each build with its edition; None passes none. extras: the
    player's own title update and DLC ({'update': path, 'dlc': path}), passed to each edition whose profile takes them."""
    extras = {k: v for k, v in (extras or {}).items() if v}
    steps, packages = [], []
    problems = check_pair(discs) + check_output(out)
    for e in editions:
        problems += edition_problems(e)
    if not editions:
        problems.append('Choose at least one edition')
    if app_icon is not None and app_icon not in APP_ICON_CHOICES:
        problems.append(f'Unknown app icon {app_icon!r}')
    problems += [f'Choose your {EXTRA_LABELS["update"]} in the sidebar: {e.label} is built for the game with its title update'
                 for e in editions if e.update == 'needed' and 'update' not in extras]
    if problems:
        raise PlanError('; '.join(dict.fromkeys(problems)))
    fetched = dict(fetched or {})
    for e in editions:
        key = str(e.release_path)
        if key in packages:
            continue
        packages.append(key)
        if key not in fetched:
            fetched[key] = compilers_fetched(e.release_path, cache)
        if not fetched[key]:
            if not licences_accepted:
                terms = licence_terms([e])
                raise PlanError('the build needs these compilers, fetched once from their publishers: ' +
                                '; '.join(f'{t["name"]} ({t["size"] / 1e6:.0f} MB from {t["download"]}; terms: {t["url"]})' for t in terms) +
                                '. Read and accept their terms first')
            release = _read_json(e.release_path)
            words = ['fetch', '--accept-ndk-license'] + (['--accept-dxc-license'] if release.get('dxc') else [])
            steps.append(Step(f'fetch:{len(steps)}', 'fetch', 'Fetch the compilers', str(e.root), key, words))
    checked = set()
    several = sum(e.kind == 'bundle' for e in editions) > 1    # each Android edition's bundle and APK in its own folder
    for e in editions:
        target = ['--' + e.target] if e.target else []
        where = Path(out) / EDITION_DIRS.get(e.key, e.key) if several and e.kind == 'bundle' else Path(out)
        base = dict(root=str(e.root), release=str(e.release_path), edition=e.ident, edition_label=e.label, edition_kind=e.kind)
        if (str(e.release_path), e.target) not in checked:           # one check per package and target
            checked.add((str(e.release_path), e.target))
            steps.append(Step(f'check:{e.ident}', 'check', 'Check this computer', words=['check'] + target, **base))
        steps.append(Step(f'build:{e.ident}', 'build', 'Build', stages=list(e.stages),
                          words=['build'] + target + ['--play-disc', str(discs['play']), '--install-disc', str(discs['install']),
                                                     '--out', str(where)] + (['--app-icon', app_icon, '--edition', e.key] if app_icon else [])
                                + (['--title-update', str(extras['update'])] if e.update and 'update' in extras else [])
                                + (['--dlc', str(extras['dlc'])] if e.dlc and 'dlc' in extras else []),
                          **base))
        if e.kind == 'bundle':
            steps.append(Step(f'verify:{e.ident}', 'verify', 'Verify the bundle', words=['verify', VERIFY_TARGET], **base))
            app = app_pin(e)
            if app:                                     # 1.2.0: the edition's APK beside its bundle (builder app)
                steps.append(Step(f'app:{e.ident}', 'app', 'Get the app', out_name=PurePosixPath(app['member']).name,
                                  words=['app', '--edition', e.key, '--out', str(where)], **base))
    return steps


def app_pin(e) -> dict | None:
    """the release's pin of this edition's app download (builder.apps_spec), or None"""
    try:
        apps = _read_json(e.release_path).get('apps') or {}
    except (OSError, ValueError, TypeError):
        return None
    rec = apps.get(e.key) if isinstance(apps, dict) else None
    return rec if isinstance(rec, dict) and rec.get('member') else None


def builder_words(step: Step, cache) -> list:
    """the builder's arguments for a step: exactly what the guided mode passes to builder.main"""
    return ['--release', step.release, '--cache', str(cache)] + list(step.words)


def launcher_command(root) -> list:
    """how the package's launcher starts the front end: builder.exe (Windows), ./builder (macOS), else this checkout's
    builder.py with the package named in BUILDER_PACKAGE_ROOT (a development tree; the tests)"""
    root = Path(root)
    if os.name == 'nt' and (root / 'builder.exe').is_file():
        return [str(root / 'builder.exe')]
    if os.name != 'nt' and (root / 'builder').is_file() and (root / 'tools' / 'own_disc' / 'builder.py').is_file():
        return ['/bin/sh', str(root / 'builder')]
    return [sys.executable, '-B', str(OWN_DISC / 'builder.py')]


def launcher_env(root) -> dict:
    env = dict(os.environ)
    env['PYTHONUNBUFFERED'] = '1'                  # lines arrive as they are printed; changes nothing that is written
    if not ((os.name == 'nt' and (Path(root) / 'builder.exe').is_file()) or (os.name != 'nt' and (Path(root) / 'builder').is_file())):
        env['BUILDER_PACKAGE_ROOT'] = str(root)    # what the launcher would have set
        env.setdefault('PYTHONUTF8', '1')
    return env


def parse_stage(line: str):
    m = STAGE_LINE.match(line.rstrip('\r\n'))
    return (m.group(1), m.group(2)) if m else None


# -------------------------------------------------------------------------------------------------------- session

def _stamp(path) -> dict:
    st = Path(path).stat()
    return {'size': st.st_size, 'mtime_ns': st.st_mtime_ns}


class Session:
    """The window's record of one build: what was chosen and how far each step got. Written after every step."""

    def __init__(self, state_dir, discs, stamps, out, cache, steps, created=None):
        self.state_dir = Path(state_dir)
        self.discs = {k: Path(v) for k, v in discs.items()}
        self.stamps = stamps
        self.out = Path(out)
        self.cache = Path(cache)
        self.steps = steps
        self.created = created or datetime.datetime.now().isoformat(timespec='seconds')
        self._lock = threading.Lock()

    @classmethod
    def new(cls, state_dir, discs, out, cache, steps):
        s = cls(state_dir, discs, {k: _stamp(v) for k, v in discs.items()}, out, cache, steps)
        s.save()
        return s

    @classmethod
    def load(cls, state_dir):
        state_dir = Path(state_dir)
        data = _read_json(state_dir / SESSION_NAME)
        if data.get('schema') != SESSION_SCHEMA:
            raise ValueError('not a builder window session')
        steps = [Step(**s) for s in data['steps']]
        for s in steps:
            if s.state == 'running':                # the window closed while it ran: it did not finish
                s.state = 'cancelled'
        return cls(state_dir, data['discs'], data['stamps'], data['out'], data['cache'], steps, data.get('created'))

    @staticmethod
    def exists(state_dir) -> bool:
        return (Path(state_dir) / SESSION_NAME).is_file()

    def save(self):
        with self._lock:
            data = {'schema': SESSION_SCHEMA, 'created': self.created, 'discs': {k: str(v) for k, v in self.discs.items()},
                    'stamps': self.stamps, 'out': str(self.out), 'cache': str(self.cache),
                    'steps': [dataclasses.asdict(s) for s in self.steps]}
            self.state_dir.mkdir(parents=True, exist_ok=True)
            target = self.state_dir / SESSION_NAME
            tmp = target.with_name(target.name + '.part')
            tmp.write_text(json.dumps(data, indent=1) + '\n', encoding='utf-8')
            os.replace(tmp, target)

    @property
    def finished(self) -> bool:
        return all(s.state == 'done' for s in self.steps)

    def resume_problems(self) -> list:
        problems = []
        for slot, path in self.discs.items():
            try:
                if _stamp(path) != self.stamps.get(slot):
                    problems.append(f'{DISC_LABELS[slot]} changed since this build started ({path}); start a new build')
            except OSError:
                problems.append(f'{DISC_LABELS[slot]} is no longer at {path}')
        for s in self.steps:
            if not (Path(s.release).is_file()):
                problems.append(f'the builder package of this build is no longer at {s.root}')
                break
        return problems

    def outputs(self) -> list:
        """each finished build's file: path, size, SHA-256 (computed once), the bundle seal or the app's bundle id"""
        made = []
        for s in self.steps:
            if s.kind != 'build' or s.state != 'done' or not s.result:
                continue
            r = s.result
            p = Path(r['path'])
            if 'sha256' not in r and p.is_file():
                r['sha256'] = db.sha256_file(p)
                self.save()
            verify = next((v for v in self.steps if v.kind == 'verify' and v.edition == s.edition), None)
            made.append(dict(r, edition=s.edition_label, kind=s.edition_kind,
                             verified=None if verify is None else verify.state == 'done'))
            app = next((a for a in self.steps if a.kind == 'app' and a.edition == s.edition and a.state == 'done' and a.result), None)
            if app is not None:                         # the edition's APK, right after its bundle
                made.append(dict(app.result, edition=s.edition_label, kind='apk', verified=None))
        return made


# --------------------------------------------------------------------------------------------------------- runner

@dataclasses.dataclass
class Event:
    kind: str                        # step-start | line | stage | step-end | done
    step: str = ''
    data: dict = dataclasses.field(default_factory=dict)


def _kill_tree(process):
    """stops a builder process and every program it started"""
    if os.name == 'nt':
        subprocess.run([os.path.join(os.environ.get('SystemRoot', r'C:\Windows'), 'System32', 'taskkill.exe'),
                        '/PID', str(process.pid), '/T', '/F'], capture_output=True)
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        return
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass


def _start(argv, cwd, env):
    kw = dict(cwd=cwd, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
              text=True, encoding='utf-8', errors='replace', bufsize=1)
    if os.name == 'nt':
        kw['creationflags'] = subprocess.CREATE_NEW_PROCESS_GROUP | 0x08000000    # CREATE_NO_WINDOW
    else:
        kw['start_new_session'] = True
    return subprocess.Popen(argv, **kw)


class Runner:
    """Runs a session's unfinished steps in order, one builder process at a time; stops at the first failure.
    events(Event) is called from the runner's thread. cancel() may be called from any thread."""

    def __init__(self, session: Session, events=lambda e: None, popen=None, kill=None, command=None):
        self.session = session
        self.events = events
        self.popen = popen or (lambda argv, **kw: _start(argv, kw.get('cwd'), kw.get('env')))
        self.kill = kill or _kill_tree
        self.command = command
        self._cancel = threading.Event()
        self._process = None
        self._lock = threading.Lock()

    def cancel(self):
        self._cancel.set()
        with self._lock:
            p = self._process
        if p is not None:
            self.kill(p)

    @property
    def cancelled(self) -> bool:
        return self._cancel.is_set()

    def _resolve(self, step: Step) -> list:
        words = list(step.words)
        if VERIFY_TARGET in words:
            build = next((s for s in self.session.steps if s.kind == 'build' and s.edition == step.edition), None)
            if build is None or not build.result:
                raise PlanError('the build step wrote no bundle to verify')
            words[words.index(VERIFY_TARGET)] = build.result['path']
        return words

    def _log(self, text: str):
        try:
            with open(self.session.state_dir / LOG_NAME, 'a', encoding='utf-8') as f:
                f.write(text + '\n')
        except OSError:
            pass

    def run(self) -> bool:
        s = self.session
        for step in s.steps:
            if step.state == 'done':
                continue
            if self.cancelled:
                break
            if not self._run_step(step):
                break
        ok = s.finished
        if ok:                                          # the last line: each edition's files and what they are for
            for o in s.outputs():
                app = o['edition'].split(' (')[0]
                what = ('missing: ' + o['missing']) if o.get('missing') else o['path']
                self._log(f'{app}: ' + (f'the app to install, {what}' if o['kind'] == 'apk' else f'the bundle to import in the {app} app, {what}'))
        self.events(Event('done', data={'ok': ok, 'cancelled': self.cancelled}))
        return ok

    def _run_step(self, step: Step) -> bool:
        s = self.session
        try:
            words = self._resolve(step)
        except PlanError as error:
            step.state, step.last_lines = 'failed', [str(error)]
            s.save()
            self.events(Event('step-end', step.id, {'state': step.state, 'code': None}))
            return False
        argv = self.command(step) if self.command else launcher_command(step.root) + ['--release', step.release, '--cache', str(s.cache)] + words
        step.state, step.exit_code, step.last_lines = 'running', None, []
        s.save()
        started_wall, started = time.time(), time.monotonic()
        stamp = datetime.datetime.now().isoformat(timespec='seconds')
        self._log(f'\n=== {stamp} {step.title} ({step.edition_label or "all editions"}) ===\n$ ' + ' '.join(argv))
        self.events(Event('step-start', step.id, {'argv': argv}))
        try:
            p = self.popen(argv, cwd=step.root, env=launcher_env(step.root))
        except OSError as error:
            step.state, step.last_lines = 'failed', [f'could not start the builder: {error}']
            s.save()
            self.events(Event('step-end', step.id, {'state': step.state, 'code': None}))
            return False
        with self._lock:
            self._process = p
        if self.cancelled:                         # cancelled while it was being started
            self.kill(p)
        bundle_id = None
        for raw in p.stdout:
            line = raw.rstrip('\r\n')
            step.last_lines = (step.last_lines + [line])[-60:]
            self._log(line)
            self.events(Event('line', step.id, {'text': line}))
            stage = parse_stage(line)
            if stage:
                self.events(Event('stage', step.id, {'stage': stage[0], 'status': stage[1]}))
            m = BUNDLE_ID_LINE.match(line)
            if m:
                bundle_id = m.group(2)
        code = p.wait()
        if hasattr(p.stdout, 'close'):
            p.stdout.close()
        with self._lock:
            self._process = None
        step.exit_code, step.seconds = code, round(time.monotonic() - started, 1)
        if self.cancelled:
            step.state = 'cancelled'
        elif step.kind == 'app' and code == APP_NOT_FETCHED:
            step.state = 'done'                        # the bundle stands; the player is told which file to download
            advice = next((ln for ln in reversed(step.last_lines) if ln.startswith('APP NOT FETCHED')), 'The app was not fetched.')
            step.result = {'path': str(self._app_path(step)), 'missing': advice.replace('APP NOT FETCHED: ', '')}
        elif code == 0:
            step.state = 'done'
            if step.kind == 'app':
                made = self._app_path(step)
                step.result = {'path': str(made), 'size': made.stat().st_size, 'sha256': db.sha256_file(made)} if made.is_file() else \
                    {'path': str(made), 'missing': 'The builder ended without writing the app.'}
            if step.kind == 'build':
                step.result = self._build_result(step, started_wall, bundle_id)
                if step.result is not None and step.out_name:
                    step.result = self._named(step, step.result)
                if step.result is None:
                    step.state = 'failed'
                    step.last_lines.append('The build ended without naming its output (no last-run.json written by this run).')
        else:
            step.state = 'failed'
        s.save()
        self._log(f'=== {step.title}: {step.state} (exit {code}, {step.seconds} s) ===')
        self.events(Event('step-end', step.id, {'state': step.state, 'code': code}))
        return step.state == 'done'

    @staticmethod
    def _app_path(step: Step) -> Path:
        return Path(step.words[step.words.index('--out') + 1]) / step.out_name

    def _named(self, step: Step, result: dict) -> dict:
        """the edition's own name for its output (the same bytes, in the build's own output folder)"""
        made = Path(result['path'])
        target = made.with_name(step.out_name)
        if made != target and made.is_file():
            os.replace(made, target)
            self._log(f'Your package ({step.edition_label.split(" (")[0]}) is at {target}')
        return dict(result, path=str(target))

    @staticmethod
    def _build_result(step: Step, started_wall: float, bundle_id) -> dict | None:
        """the output the engine recorded in its work folder's last-run.json, when that file is from this run"""
        record = B.default_work(Path(step.root).resolve()) / 'last-run.json'
        try:
            if record.stat().st_mtime < started_wall - 2:
                return None
            report = _read_json(record)
        except (OSError, ValueError):
            return None
        out = report.get('bundle') or report.get('output')
        if not isinstance(out, dict) or 'path' not in out:
            return None
        result = {'path': out['path'], 'size': out.get('size')}
        if 'seal' in out:
            result['seal'] = out['seal']
        if 'sha256' in out:
            result['sha256'] = out['sha256']
        if bundle_id:
            result['bundle_id'] = bundle_id
        return result


def state_dir_for(gui_root) -> Path:
    """where the window keeps its session and log: the package's private folder (never inside a git checkout's tracked tree)"""
    return Path(gui_root) / 'local-analysis' / 'builder' / 'gui'
