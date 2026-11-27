"""Synthetic connected checks of the portable builder (the host helper, the front end, the engine, the tools they
call). No game data. PACKAGE_SRC redirects to a source mutant overlay (mutants_package.py)."""
from __future__ import annotations
import contextlib
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
import zipfile

REPO = Path(__file__).resolve().parents[3]
SRC = Path(os.environ.get('PACKAGE_SRC', REPO))
SCRATCH = REPO / 'local-analysis' / 'test-tmp'      # inside the checkout's private folder: the tools refuse any other place in it
sys.path.insert(0, str(SRC / 'tools/own_disc'))
import builder as b
import host_support as hs
import checkout_refusal
import disc_builder as db


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec); sys.modules[name] = mod; spec.loader.exec_module(mod)
    return mod


# Existing project fixtures are synthetic, not the owner's game.
os.environ['DISC_BUILDER'] = str(SRC / 'tools/own_disc/disc_builder.py')
fixture = load('package_fixtures', REPO / 'tools/own_disc/tests/test_disc_builder.py')
sys.path.insert(0, str(REPO / 'tools/iter'))
it = load('package_iterate', SRC / 'tools/iter/iterate.py')
rp = load('package_recipe', SRC / 'tools/fp_codegen/recipe_build.py')
gen = load('package_generate', SRC / 'tools/fp_codegen/generate.py')


class Package(unittest.TestCase):
    def setUp(self):
        SCRATCH.mkdir(parents=True, exist_ok=True)
        self.tmp = tempfile.TemporaryDirectory(prefix='synthetic-étrange space-', dir=SCRATCH)
        self.addCleanup(self.tmp.cleanup); self.root = Path(self.tmp.name)
        self.archive = self.root / 'ndk-fixture.zip'
        with zipfile.ZipFile(self.archive, 'w') as z:
            z.writestr('android-ndk-fixture/bin/compiler.txt', b'SYNTHETIC compiler, not executable')
        self.ndk = dict(url='https://dl.google.com/android/repository/android-ndk-fixture.zip',
                        sha256=db.sha256_file(self.archive), size=self.archive.stat().st_size,
                        directory='android-ndk-fixture', unpacked_bytes=4096,
                        license_url='https://developer.android.com/ndk/downloads')
        self.cache = self.root / 'cache'; self.release = {'ndk': self.ndk}

    def fetch(self):
        with contextlib.redirect_stdout(io.StringIO()):
            b.fetch(self.release, self.cache, True, self.archive)

    def test_01_dependency_receipt_roundtrip(self):
        header = self.root / 'naïve # cost$.h'; header.write_bytes(b'v1')
        dep = self.root / 'unit.d'
        escaped = str(header).replace(' ', '\\ ').replace('#', '\\#').replace('$', '$$')
        dep.write_text('obj.o: \\\n ' + escaped + '\n', encoding='utf-8')
        self.assertEqual(it._deps(dep, self.root), {str(header): hashlib.sha256(b'v1').hexdigest()})
        header.write_bytes(b'v2')
        self.assertNotEqual(it._deps(dep, self.root)[str(header)], hashlib.sha256(b'v1').hexdigest())

    def test_02_drive_dependency_names(self):
        self.assertEqual(hs.dep_paths('C:\\obj\\a.o: C:\\src\\name\\ file.h D:/inc/a.h\nC:/phony.h:\n'),
                         ['C:\\src\\name file.h', 'D:/inc/a.h'])
        with self.assertRaises(ValueError): hs.dep_paths('malformed depfile')

    def test_03_windows_priority_no_shell(self):
        with mock.patch.object(hs.sys, 'platform', 'win32'), mock.patch.object(hs.shutil, 'which', side_effect=AssertionError('must not find nice')):
            argv, kwargs = hs.low_priority(['C:\\sp ace\\clang++.exe', 'é.cpp'])
        self.assertEqual(argv, ['C:\\sp ace\\clang++.exe', 'é.cpp'])
        self.assertEqual(kwargs, {'creationflags': 0x4000})

    def test_04_copy_without_macos_command(self):
        src, dest = self.root / 'src', self.root / 'copy'; src.write_bytes(b'synthetic')
        with mock.patch.object(hs.sys, 'platform', 'win32'), mock.patch.object(hs.subprocess, 'run', side_effect=AssertionError('cp is unavailable')):
            it.clone_or_copy(src, dest)
        self.assertEqual(src.read_bytes(), dest.read_bytes())

    def test_05_compile_plan_producer_consumer(self):
        path = self.root / 'commands.json'
        path.write_text(json.dumps([dict(file='C:\\源 files\\ppc_recomp.3.cpp', directory=str(self.root),
                        arguments=['C:\\NDK tools\\clang++.exe', '-c', 'old.cpp', '-o', 'old.o', '-MF', 'old.d', '-MT', 'old.o', '-DKEEP=1'])]))
        with mock.patch.object(it, 'ROOT', self.root), mock.patch.object(it.subprocess, 'run', side_effect=AssertionError('no Ninja')):
            cmd, out = it.ppc_chunk_command({'build_cwd': '.', 'ppc_compile_commands': 'commands.json'}, 3,
                                           self.root / 'new src.cpp', self.root / 'new obj.o', self.root / 'new dep.d')
        self.assertEqual(cmd[0], 'C:\\NDK tools\\clang++.exe')
        self.assertEqual(cmd[cmd.index('-c')+1], str(self.root / 'new src.cpp'))
        self.assertIn('-DKEEP=1', cmd); self.assertEqual(cmd[-4:], it.STRICT_FLAGS)

    def test_06_recipe_subprocess_connection(self):
        tool = self.root / 'pass.py'
        tool.write_text('import sys\nfrom pathlib import Path\nPath(sys.argv[2]).write_bytes(Path(sys.argv[1]).read_bytes()+b"changed\\n")\n')
        pass_obj = object.__new__(rp.Pass); pass_obj.id = 'fixture'; pass_obj.tmp = self.root; pass_obj.tool = tool
        real_run = subprocess.run
        def checked_run(argv, **kwargs):
            self.assertNotEqual(Path(argv[0]).name, 'nice')
            return real_run(argv, **kwargs)
        with mock.patch.object(hs.shutil, 'which', return_value=None), mock.patch.object(rp.subprocess, 'run', side_effect=checked_run):
            self.assertEqual(pass_obj.run(['{in}', '{out}'], 'source é\n'), 'source é\nchanged\n')

    def test_07_generator_private_boundary(self):
        checkout = self.root / 'checkout'; (checkout / '.git').mkdir(parents=True)
        with self.assertRaisesRegex(ValueError, 'local-analysis'):
            hs.private_output(checkout / 'src/generated')
        hs.private_output(checkout / 'local-analysis/work')
        hs.private_output(self.root / 'standalone/work')
        with mock.patch.object(hs.os.path, 'relpath', side_effect=ValueError('different drive')):
            self.assertEqual(hs.relative_or_absolute(self.root / 'input', self.root / 'work'), str(self.root / 'input'))

    def test_08_fetch_requires_consent(self):
        with mock.patch.object(b.urllib.request, 'build_opener', side_effect=AssertionError('no network')):
            with self.assertRaisesRegex(b.Refusal, 'licence not accepted'):
                b.fetch(self.release, self.cache, False)
        self.assertFalse(self.cache.exists())

    def test_09_fetch_hash_before_extraction(self):
        self.ndk['sha256'] = '0' * 64
        with self.assertRaisesRegex(b.Refusal, 'hash/size mismatch'):
            b.fetch(self.release, self.cache, True, self.archive)
        self.assertFalse((self.cache / ('0' * 16)).exists())

    def test_10_fetch_to_compiler_identity(self):
        self.fetch(); root = b.existing_ndk(self.cache, self.ndk)
        self.assertEqual((root / 'bin/compiler.txt').read_bytes(), b'SYNTHETIC compiler, not executable')
        (root / 'bin/compiler.txt').write_bytes(b'changed')
        with self.assertRaisesRegex(b.Refusal, 'files changed'): b.existing_ndk(self.cache, self.ndk)

    def test_11_archive_name_guards(self):
        for name in ('../escape', 'C:/escape', 'a/../b', 'a\\b', 'a/NUL.txt', 'a/space ', 'a/trail.', '/abs', 'a//b'):
            with self.subTest(name=name), self.assertRaises(b.Refusal): b.safe_name(name)
        self.assertEqual(str(b.safe_name('répertoire/file name')), 'répertoire/file name')

    def test_12_zip_case_alias_refused(self):
        with zipfile.ZipFile(self.archive, 'w') as z:
            z.writestr('android-ndk-fixture/a', b'one'); z.writestr('android-ndk-fixture/A', b'two')
        self.ndk.update(size=self.archive.stat().st_size, sha256=db.sha256_file(self.archive))
        with self.assertRaisesRegex(b.Refusal, 'duplicate case-insensitive'):
            b.fetch(self.release, self.cache, True, self.archive)
        self.assertFalse((self.cache / self.ndk['sha256'][:16]).exists())

    def test_12b_fetch_refuses_early_past_the_windows_path_limit(self):
        # 2026-11-10 Zenbook: long paths off (the Windows default), the NDK's 176-character member under a 45-character cache
        # failed mid-unpack with a raw Errno 2. With long paths off, a path over 259 characters is refused before unpacking.
        def archive_reaching(depth):        # an archive whose one file lands exactly `depth` characters deep once installed
            base = len(str(self.cache.resolve() / ('0' * 16))) + 1
            name = 'android-ndk-fixture/' + 'd' * (depth - base - len('android-ndk-fixture/'))
            with zipfile.ZipFile(self.archive, 'w') as z: z.writestr(name, b'SYNTHETIC compiler')
            self.ndk.update(size=self.archive.stat().st_size, sha256=db.sha256_file(self.archive))
            target = self.cache / self.ndk['sha256'][:16]
            self.assertEqual(len(str(target.resolve() / name)), depth)
            return target
        with mock.patch.object(b, 'long_paths_enabled', return_value=False, create=True):
            target = archive_reaching(260)
            with self.assertRaisesRegex(b.Refusal, r'260 characters deep.*move the cache to a shorter folder'):
                b.fetch(self.release, self.cache, True, self.archive)
            self.assertFalse(target.exists())
            self.assertEqual([p for p in self.cache.rglob('*') if p.name != 'download.zip' and p.parent.parent == self.cache], [])
            target = archive_reaching(259)
            b.fetch(self.release, self.cache, True, self.archive); self.assertTrue(target.is_dir())
        with mock.patch.object(b, 'long_paths_enabled', return_value=True, create=True):
            target = archive_reaching(260)
            b.fetch(self.release, self.cache, True, self.archive); self.assertTrue(target.is_dir())

    def ended_pid(self):
        return int(subprocess.run([sys.executable, '-c', 'import os; print(os.getpid())'], capture_output=True, text=True).stdout)

    def fetch_with_lock(self, cache, content, age=0):
        """fetch into a new cache that holds a .fetch-lock with this content, written `age` seconds ago: (output, lock)"""
        self.cache = cache; cache.mkdir(parents=True, exist_ok=True)
        lock = cache / '.fetch-lock'; lock.write_text(content)
        if age:
            os.utime(lock, (lock.stat().st_mtime - age,) * 2)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            b.fetch(self.release, cache, True, self.archive)
        return out.getvalue(), lock

    def test_12c_a_stale_fetch_lock_goes_with_its_fetch_s_unfinished_staging(self):
        # 2026-11-24 (Discord #help-thor, 1.2.x Windows builder): an interrupted fetch left %LOCALAPPDATA%\own-disc-builder\.fetch-lock
        # and every later fetch refused, in the window and the console. A lock whose fetch has ended goes, with a note.
        gone = self.ended_pid()
        cache = self.root / 'cache-stale'; cache.mkdir()
        own, partial, done = (cache / n for n in ('f-0000000a', 'f-0000000b', 'f-0000000c'))
        for d in (own, partial, done):
            d.mkdir()
        shutil.copyfile(self.archive, own / 'download.zip')                 # the stale fetch's own folder (named in its lock)
        (partial / 'download.zip').write_bytes(b'cut short')                # another fetch's download, interrupted
        shutil.copyfile(self.archive, done / 'download.zip')                # a complete earlier download: evidence, kept
        node = b.platform.node()
        said, lock = self.fetch_with_lock(cache, json.dumps({'pid': gone, 'started': 1.0, 'host': node, 'staged': own.name}))
        self.assertIn(f'Removed a stale fetch lock (process {gone}, the fetch that wrote it, has ended): {lock}', said)
        self.assertEqual((own.exists(), partial.exists(), (done / 'download.zip').is_file(), lock.exists()), (False, False, True, False))
        b.existing_ndk(cache, self.ndk)
        # a 1.2.x lock (the bare process id): stale when that process has ended, or when its id now belongs to a program
        # started after the lock was written; an empty lock older than a minute names no live fetch
        for name, content, age in (('ended', str(gone), 0), ('reused', str(os.getpid()), 10 ** 6), ('empty', '', 120)):
            with self.subTest(name):
                said, lock = self.fetch_with_lock(self.root / f'cache-{name}', content, age)
                self.assertIn(f'Removed a stale fetch lock (', said)
                self.assertFalse(lock.exists())
                b.existing_ndk(self.cache, self.ndk)

    def test_12d_a_fetch_lock_that_may_still_be_owned_is_refused_with_the_file_to_delete(self):
        me, node = os.getpid(), b.platform.node()
        for name, content, age in (
                ('running fetch', json.dumps({'pid': me, 'started': b.process_start(me), 'host': node, 'staged': 'f-0000000d'}), 0),
                ('another computer', json.dumps({'pid': self.ended_pid(), 'started': 1.0, 'host': node + '-other', 'staged': None}), 0),
                ('1.2.x running fetch', str(me), 0),
                ('just created', '', 0)):
            with self.subTest(name):
                cache = self.root / f'cache-{name.replace(" ", "-")}'
                with self.assertRaisesRegex(b.Refusal, 'another fetch owns the cache.*delete this file, then run fetch again: '
                                            + re.escape(str(cache / '.fetch-lock'))):
                    self.fetch_with_lock(cache, content, age)
                self.assertTrue((cache / '.fetch-lock').exists())
                self.assertFalse((cache / self.ndk['sha256'][:16]).exists())

    def test_13_release_payload_hash(self):
        profile = self.root / 'profile.json'; profile.write_bytes(b'{}')
        release = dict(schema='builder-release/1', host=b.host_id(), profile='profile.json',
                       files=[dict(path='profile.json', sha256=db.sha256_file(profile))])
        path = self.root / 'release.json'; path.write_text(json.dumps(release))
        b.load_release(path); profile.write_bytes(b'{"changed":true}')
        with self.assertRaisesRegex(b.Refusal, 'missing or changed'): b.load_release(path)

    @contextlib.contextmanager
    def host(self, plat, build, machine):       # the OS (sys.platform), the interpreter's build (sysconfig), the OS's native CPU
        with mock.patch.object(b.sys, 'platform', plat), mock.patch('sysconfig.get_platform', return_value=build), \
             mock.patch.object(b.platform, 'machine', return_value=machine):
            yield

    def test_13b_release_host_is_the_interpreters_cpu(self):
        # 2026-11-10 Zenbook (Windows on ARM): the package's x64 Python reported the OS's ARM64 CPU, so the x64 release was refused.
        profile = self.root / 'profile.json'; profile.write_bytes(b'{}')
        path = self.root / 'release.json'
        pin = self.native_program(self.root)
        def release(host):
            files, extra = [dict(path='profile.json', sha256=db.sha256_file(profile))], {}
            if host == 'win32-arm64':           # a Windows on ARM release carries its native compiler programs (test_13e)
                files.append(dict(path=pin['path'], sha256=pin['sha256'])); extra['compiler_overlay'] = dict(files=[pin])
            path.write_text(json.dumps(dict(schema='builder-release/1', host=host, profile='profile.json', files=files, **extra)))
            return path
        on = self.host
        with on('win32', 'win-amd64', 'ARM64'):                     # x64 interpreter, emulated on an ARM64 OS
            b.load_release(release('win32-x86_64'))
            with self.assertRaisesRegex(b.Refusal, 'wrong or missing release for win32-x86_64'): b.load_release(release('win32-arm64'))
        with on('win32', 'win-arm64', 'ARM64'):                     # a native ARM64 interpreter: the match stays exact
            b.load_release(release('win32-arm64'))
            with self.assertRaisesRegex(b.Refusal, 'wrong or missing release for win32-arm64'): b.load_release(release('win32-x86_64'))
        with on('win32', 'win-amd64', 'AMD64'): self.assertEqual(b.host_id(), 'win32-x86_64')
        with on('darwin', 'macosx-11.0-arm64', 'arm64'): self.assertEqual(b.host_id(), 'darwin-arm64')
        with on('darwin', 'macosx-10.9-universal2', 'x86_64'): self.assertEqual(b.host_id(), 'darwin-x86_64')
        with on('linux', 'linux-aarch64', 'aarch64'): self.assertEqual(b.host_id(), 'linux-arm64')

    def test_13c_windows_on_arm_is_refused_at_check(self):
        # 2026-11-10: the x64 toolchain emulated on a Snapdragon X2 laptop folded one FFmpeg constant 1 ulp off, an hour into
        # the build. check now refuses such a PC before anything runs; an x64 PC passes this guard to the next checks.
        profile = self.root / 'profile.json'; profile.write_bytes(b'{}')
        release = self.root / 'release.json'
        release.write_text(json.dumps(dict(schema='builder-release/1', host='win32-x86_64', profile='profile.json',
                                           files=[dict(path='profile.json', sha256=db.sha256_file(profile))])))
        argv = ['--release', str(release), '--cache', str(self.cache), 'check', '--work', str(self.root / 'work')]
        def run(machine):
            with self.host('win32', 'win-amd64', machine), contextlib.redirect_stdout(io.StringIO()) as out:
                code = b.main(argv)
            return code, out.getvalue()
        code, out = run('ARM64')
        self.assertNotEqual(code, 0); self.assertIn("Windows on ARM can't build a bit-identical release", out)
        self.assertFalse((self.root / 'work').exists()); self.assertFalse(self.cache.exists())
        code, out = run('AMD64')
        self.assertNotIn('Windows on ARM', out); self.assertIn('80 characters', out)     # the next guard: this test's long folder

    def native_program(self, folder, name='native.exe', data=b'SYNTHETIC native compiler', replaces='bin/compiler.txt'):
        """A native compiler program in a package folder and its overlay pin (the Windows on ARM release carries the ARM64
        clang that replaces the NDK's x64 one)."""
        program = folder / name; program.parent.mkdir(parents=True, exist_ok=True); program.write_bytes(data)
        return dict(path=name, replaces=replaces, sha256=db.sha256_file(program), size=len(data))

    def test_13d_an_overlay_release_builds_with_its_native_compiler_programs(self):
        # 2026-11-11 Zenbook: the NDK publishes x64 compiler programs only, and emulated they fold floating point differently.
        # A release that lays native programs over the NDK hands the engine that folder: the declared program replaced,
        # every other file the verified NDK's, the NDK's own folder untouched, and no deeper (Windows' path limit).
        with zipfile.ZipFile(self.archive, 'w') as z:
            z.writestr('android-ndk-fixture/bin/compiler.txt', b'SYNTHETIC x64 compiler')
            z.writestr('android-ndk-fixture/bin/linker.txt', b'SYNTHETIC x64 linker')
        self.ndk.update(size=self.archive.stat().st_size, sha256=db.sha256_file(self.archive))
        (self.root / 'fixture').mkdir(); fx, release = self.make_release()
        pin = self.native_program(fx.side, 'native/compiler-arm64.txt')
        data = json.loads(release.read_text())
        data['files'].append(dict(path=pin['path'], sha256=pin['sha256'])); data['compiler_overlay'] = dict(files=[pin])
        release.write_text(json.dumps(data))
        base, handed = ['--release', str(release), '--cache', str(self.cache)], []
        build = base + ['build', '--work', str(fx.work), '--play-disc', str(fx.disc)]
        def engine(argv):
            handed.append(Path(os.environ['BUILDER_NDK_ROOT'])); return 0
        with mock.patch.object(b.db, 'main', side_effect=engine), contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(b.main(base + ['fetch', '--accept-ndk-license', '--archive', str(self.archive)]), 0, out.getvalue())
            self.assertEqual(b.main(build), 0, out.getvalue())
        ndk, = handed
        own = self.cache / self.ndk['sha256'][:16] / 'android-ndk-fixture'
        self.assertEqual((ndk / 'bin/compiler.txt').read_bytes(), b'SYNTHETIC native compiler')
        self.assertEqual((ndk / 'bin/linker.txt').read_bytes(), b'SYNTHETIC x64 linker')
        self.assertEqual((own / 'bin/compiler.txt').read_bytes(), b'SYNTHETIC x64 compiler')
        self.assertEqual(len(str(ndk)), len(str(own)))
        (ndk / 'bin/compiler.txt').write_bytes(b'SYNTHETIC x64 compiler')      # the NDK's program back in place of the native one
        with mock.patch.object(b.db, 'main', side_effect=engine), contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertNotEqual(b.main(build), 0)
        self.assertIn('compiler files changed', out.getvalue()); self.assertEqual(len(handed), 1)

    def test_13e_a_windows_on_arm_release_without_native_compiler_programs_is_refused(self):
        # A Windows on ARM package that would run the NDK's x64 compiler (emulated) is refused before anything is fetched or
        # built; the same package with native programs passes this guard to the next one.
        profile = self.root / 'profile.json'; profile.write_bytes(b'{}')
        pin = self.native_program(self.root)
        release = self.root / 'release.json'
        files = [dict(path='profile.json', sha256=db.sha256_file(profile)), dict(path=pin['path'], sha256=pin['sha256'])]
        def run(words, overlay):
            data = dict(schema='builder-release/1', host='win32-arm64', profile='profile.json', ndk=self.ndk, files=files)
            if overlay: data['compiler_overlay'] = dict(files=[pin])
            release.write_text(json.dumps(data))
            with self.host('win32', 'win-arm64', 'ARM64'), contextlib.redirect_stdout(io.StringIO()) as out:
                code = b.main(['--release', str(release), '--cache', str(self.cache)] + words)
            return code, out.getvalue()
        for words in (['fetch', '--accept-ndk-license', '--archive', str(self.archive)], ['check', '--work', str(self.root / 'work')]):
            with self.subTest(command=words[0]):
                code, out = run(words, overlay=False)
                self.assertNotEqual(code, 0); self.assertIn('carries no native compiler programs', out)
                self.assertFalse(self.cache.exists()); self.assertFalse((self.root / 'work').exists())
                code, out = run(words, overlay=True)
                self.assertNotIn('native compiler', out); self.assertIn('80 characters', out)   # the next guard: this test's long folder

    def test_13f_native_compiler_pins_fail_closed(self):
        profile = self.root / 'profile.json'; profile.write_bytes(b'{}')
        pin = self.native_program(self.root)
        listed = [dict(path='profile.json', sha256=db.sha256_file(profile)), dict(path=pin['path'], sha256=pin['sha256'])]
        release = self.root / 'release.json'
        def load(overlay, files=listed):
            release.write_text(json.dumps(dict(schema='builder-release/1', host=b.host_id(), profile='profile.json', ndk=self.ndk,
                                               files=files, compiler_overlay=overlay)))
            return b.load_release(release)
        for overlay, files, refusal in ((dict(files=[]), listed, 'native compiler overlay is not pinned'),
                                        (dict(files=[dict(pin, sha256='0' * 64)]), listed, 'not hash-bound in the release: native.exe'),
                                        (dict(files=[pin]), listed[:1], 'not hash-bound in the release: native.exe'),
                                        (dict(files=[dict(pin, replaces='../escape')]), listed, 'invalid package path'),
                                        (dict(files=[pin, dict(pin, replaces='BIN/Compiler.txt')]), listed, 'replaces BIN/Compiler.txt twice')):
            with self.subTest(refusal=refusal), self.assertRaisesRegex(b.Refusal, refusal):
                load(overlay, files)
        loaded, root = load(dict(files=[dict(pin, replaces='bin/other.txt')]))      # the release reads; the NDK has no such file
        self.fetch()
        with self.assertRaisesRegex(b.Refusal, 'replaces bin/other.txt, which the compiler archive does not have'):
            b.fetch_compiler_overlay(loaded, root, self.cache)

    def test_14_windows_path_refusal(self):
        with mock.patch.object(b.sys, 'platform', 'win32'):
            with self.assertRaisesRegex(b.Refusal, '80 characters'):
                b.check_paths(self.root / ('a' * 90), self.cache)

    def make_release(self, change=None):
        fx = fixture.Fixture(self.root / 'fixture')
        # Folder is created by the caller; copy-source is supplied to synthetic fixture only.
        for stage in fx.profile['stages']:
            if stage['adapter'] == 'command': stage['argv'][0] = '${python}'
        fx.stage('shaders')['source'] = '${repo}/pack.bin'
        if change:
            change(fx)
        fx.profile_path.write_text(json.dumps(fx.profile))
        shutil.copyfile(fx.profile_path, fx.side / 'profile.json')
        self.release.update(schema='builder-release/1', host=b.host_id(), profile='profile.json',
                            files=[dict(path=p.relative_to(fx.side).as_posix(), sha256=db.sha256_file(p)) for p in fx.side.rglob('*') if p.is_file()],
                            estimates=dict(min_free_bytes=1, build_time='synthetic fixture only'))
        release = fx.side / 'release.json'; release.write_text(json.dumps(self.release))
        return fx, release

    def test_15_frontend_build_resume_verify(self):
        self.fetch(); (self.root / 'fixture').mkdir(); fx, release = self.make_release()
        argv = ['--release', str(release), '--cache', str(self.cache), 'build', '--work', str(fx.work), '--play-disc', str(fx.disc)]
        with contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(b.main(argv), 0, out.getvalue())
            seal = db.verify_bundle(fx.bundle())['_seal']
            self.assertEqual(b.main(argv), 0, out.getvalue())
            self.assertEqual(fx.executed(), ['decode', 'codegen', 'link'])
            self.assertEqual(db.verify_bundle(fx.bundle())['_seal'], seal)
            self.assertEqual(b.main(['verify', str(fx.bundle())]), 0, out.getvalue())
        # Corrupt only a produced intermediate; producer rebuilds it, consumers
        # whose inputs still have identical content correctly reuse their receipts.
        (fx.work / 'decode/image.bin').write_bytes(b'corruption')
        with contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(b.main(argv), 0, out.getvalue())
        self.assertEqual(fx.executed(), ['decode', 'codegen', 'link', 'decode'])
        self.assertEqual(db.verify_bundle(fx.bundle())['_seal'], seal)

    def test_15b_frontend_passes_the_players_title_update_to_the_engine(self):
        # A release for the game with a title update: build forwards --title-update; without it the engine refuses the run.
        self.fetch(); (self.root / 'fixture').mkdir()
        update = self.root / 'update'; update.mkdir(); (update / 'default.xexp').write_bytes(b'patch bytes')
        def with_update(fx):
            game = fx.profile['games'][0]
            game['title_update'] = {'label': 'Fake update 2', 'executable_sha256': game['executable_sha256'],
                                    'executable_size': game['executable_size'],
                                    'files': [{'path': 'default.xexp', 'size': 11, 'sha256': db.sha256_file(update / 'default.xexp')}]}
        fx, release = self.make_release(with_update)
        argv = ['--release', str(release), '--cache', str(self.cache), 'build', '--work', str(fx.work), '--play-disc', str(fx.disc)]
        with contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(b.main(argv), db.EXIT_USAGE, out.getvalue())
            self.assertIn('give your own title-update file', out.getvalue())
            self.assertEqual(b.main(argv + ['--title-update', str(update)]), 0, out.getvalue())
            self.assertIn('Title update: Fake update 2', out.getvalue())

    def test_15c_frontend_passes_the_players_dlc_packs_to_the_engine(self):
        # A release whose game takes DLC: build forwards every --dlc (a folder, a single pack) and the bundle carries the packs.
        self.fetch(); (self.root / 'fixture').mkdir()
        mine = self.root / 'my dlc'; mine.mkdir(); single = self.root / 'one more'; single.mkdir()
        fixture.stfs_package(mine / 'ABC123', content_type=2, metadata_version=2)
        fixture.stfs_package(single / 'def456', content_type=2, metadata_version=2)
        def with_dlc(fx):
            fixture.BundleFormat2.second(None, fx)
            fx.profile['games'][0]['dlc'] = {'title_id': '12345678'}          # the synthetic packs' title
        fx, release = self.make_release(with_dlc)
        argv = ['--release', str(release), '--cache', str(self.cache), 'build', '--work', str(fx.work), '--play-disc', str(fx.disc),
                '--dlc', str(mine), '--dlc', str(single / 'def456')]
        with contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(b.main(argv), 0, out.getvalue())
        names = [c['name'] for c in db.verify_bundle(fx.bundle())['components'] if c['role'] == 'dlc-package']
        self.assertEqual(names, ['dlc/abc123', 'dlc/def456'])

    def test_16_case_ambiguity_refused(self):
        folder = db.FolderDisc(self.root)
        one, two = self.root / 'FILE', self.root / 'file'
        one.write_bytes(b'a'); two.write_bytes(b'b')
        # APFS here is case-insensitive. Exercise two directory entries with a
        # mocked listing instead of claiming the host is case-sensitive.
        with mock.patch.object(Path, 'iterdir', return_value=iter([one, two])):
            with self.assertRaisesRegex(db.BuilderError, 'ambiguous'): folder.size('file')

    def test_17_portable_context(self):
        ctx = db.Context({'_dir': self.root}, self.root / 'work')
        with mock.patch.dict(os.environ, {'BUILDER_PYTHON': 'C:\\Python é\\python.exe', 'BUILDER_NDK_ROOT': 'C:\\NDK'}), mock.patch.object(db.sys, 'platform', 'win32'):
            self.assertEqual(ctx.expand('${python}|${ndk}|tool${exe}'), 'C:\\Python é\\python.exe|C:\\NDK|tool.exe')
        with mock.patch.dict(os.environ, {'BUILDER_HOST_TOOLS': 'D:\\tools é'}), mock.patch.object(db.sys, 'platform', 'win32'), \
             mock.patch.object(hs.sys, 'platform', 'win32'):
            self.assertEqual(ctx.expand('${hosttools}/xex_image${exe}|${ndk_}'.replace('${ndk_}', '${ndkhost}')), 'D:\\tools é/xex_image.exe|windows-x86_64')
        with mock.patch.object(hs.sys, 'platform', 'darwin'), mock.patch.object(db.sys, 'platform', 'darwin'), mock.patch.dict(os.environ, {'BUILDER_HOST_TOOLS': '/t'}):
            self.assertEqual(ctx.expand('${hosttools}/x${exe} ${ndkhost}'), '/t/x darwin-x86_64')
        self.assertEqual([hs.ndk_host_tag(p) for p in ('darwin', 'win32', 'linux', 'linux2')], ['darwin-x86_64', 'windows-x86_64', 'linux-x86_64', 'linux-x86_64'])
        with self.assertRaisesRegex(ValueError, 'no NDK host'): hs.ndk_host_tag('sunos5')
        with mock.patch.dict(os.environ):
            os.environ.pop('BUILDER_HOST_TOOLS', None)
            with self.assertRaisesRegex(db.BuilderError, 'host tools missing'): ctx.expand('${hosttools}')

    def test_18_lf_recipe_source_contract(self):
        # Check actual Path call syntax, not a weak text search. Windows text-mode
        # writes must be explicit at every patched recipe producer.
        import ast
        for rel in ('recipe_build.py', 'leaf_locals.py', 'gpr_fastpath.py', 'stitch_split_functions.py', 'recipe/textpass.py'):
            tree = ast.parse((SRC / 'tools/fp_codegen' / rel).read_text())
            for node in ast.walk(tree):
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == 'write_text':
                    kwargs = {k.arg: ast.literal_eval(k.value) for k in node.keywords}
                    self.assertEqual(kwargs.get('newline'), '\n', (rel, node.lineno))
                    self.assertEqual(kwargs.get('encoding'), 'utf-8', (rel, node.lineno))

    def test_19_engine_identity_in_receipt(self):
        ctx = db.Context({'_dir': self.root}, self.root / 'work'); ctx.game = {'executable_sha256': '0' * 64}
        stage = dict(adapter='command', id='synthetic', tools=[], inputs={})
        parts = db.stage_key_parts(stage, ctx, 'start', {})
        self.assertEqual(parts['tools']['host-support'], db.sha256_file(Path(hs.__file__)))
        self.assertEqual(parts['tools']['builder-engine'], db.sha256_file(Path(db.__file__)))

    def test_20_missing_pins_fail_closed(self):
        self.release['ndk']['sha256'] = None
        with self.assertRaisesRegex(b.Refusal, 'not pinned'): b.fetch(self.release, self.cache, True, self.archive)
        self.assertFalse(self.cache.exists())

    def test_21_seal_scope_negative_evidence(self):
        # Current format binds host stage keys into the bundle. Equal component
        # bytes alone DO NOT imply equal seals. This records an unresolved B5
        # contract issue without removing any provenance or receipt protection.
        a, c = self.root / 'a', self.root / 'c'; a.mkdir(); c.mkdir()
        fa, fc = fixture.Fixture(a), fixture.Fixture(c)
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(fa.run()[0], 0); self.assertEqual(fc.run()[0], 0)
        ma, mc = db.verify_bundle(fa.bundle()), db.verify_bundle(fc.bundle())
        self.assertEqual([(x['name'], x['sha256']) for x in ma['components']], [(x['name'], x['sha256']) for x in mc['components']])
        self.assertNotEqual(ma['_seal'], mc['_seal'])

    def test_22_packaged_generator_no_git(self):
        input_path, context, switch = [self.root / x for x in ('synthetic-input', 'context.h', 'switch.toml')]
        for p in (input_path, context, switch): p.write_bytes(b'synthetic')
        out = self.root / 'generated'; exe = self.root / 'standin'; exe.write_bytes(b'synthetic executable identity')
        def outputs(ppc):
            for name in ('ppc_context.h', 'ppc_config.h'): (ppc / name).write_bytes(b'synthetic')
            return {'chunks': [], 'generated_bytes': 18}
        def run_logged(command, log):
            self.assertEqual(command[-1], str(context)); log.write_bytes(b'')
        with mock.patch.object(gen, '_tool_path', return_value=exe), mock.patch.object(gen, '_run_logged', side_effect=run_logged), \
             mock.patch.object(gen, 'collect_outputs', side_effect=outputs), \
             mock.patch.object(gen.subprocess, 'run', side_effect=AssertionError('Git/analyser must not run')):
            self.assertEqual(gen.main([str(input_path), str(out), '--context', str(context), '--switch-tables', str(switch),
                                       '--generator-revision', '1' * 40]), 0)
        data = json.loads((out / 'manifest.json').read_text())
        self.assertEqual(data['inputs']['generator_revision'], '1' * 40)
        self.assertEqual(data['inputs']['ppc_context_sha256'], db.sha256_file(context))

    def test_23_zipapp_build_and_verify(self):
        self.fetch(); (self.root / 'fixture').mkdir(); fx, release = self.make_release()
        # Rebuild zipapp from the selected overlay so mutants exercise the code
        # shipped inside the artifact, not a stale previously built archive.
        artifact = self.root / 'builder.pyz'
        with zipfile.ZipFile(artifact, 'w') as z:
            for name in ('builder.py', 'disc_builder.py', 'host_support.py', 'checkout_refusal.py'):
                z.write(SRC / 'tools/own_disc' / name, name)
            z.writestr('__main__.py', 'import builder\nraise SystemExit(builder.main())\n')
        result = subprocess.run([sys.executable, str(artifact), '--release', str(release), '--cache', str(self.cache),
                                 'build', '--work', str(fx.work), '--play-disc', str(fx.disc)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(db.verify_bundle(fx.bundle())['runtime_abi'], 'test-abi-1')

    def test_24_archive_links_materialized_safely(self):
        import stat
        with zipfile.ZipFile(self.archive, 'w') as z:
            z.writestr('android-ndk-fixture/bin/real', b'tool')
            link = zipfile.ZipInfo('android-ndk-fixture/bin/compiler')
            link.create_system = 3; link.external_attr = (stat.S_IFLNK | 0o777) << 16
            z.writestr(link, 'real')
        self.ndk.update(size=self.archive.stat().st_size, sha256=db.sha256_file(self.archive))
        self.fetch(); installed = b.existing_ndk(self.cache, self.ndk) / 'bin/compiler'
        self.assertFalse(installed.is_symlink()); self.assertEqual(installed.read_bytes(), b'tool')

    def test_25_recipe_helper_identity(self):
        with mock.patch.object(rp, 'TOOLS_ROOT', SRC):
            closure = rp.closure(SRC / 'tools/fp_codegen/recipe_build.py')
        self.assertIn(SRC / 'tools/own_disc/host_support.py', closure)

    def test_26_package_root_override(self):
        with mock.patch.dict(os.environ, {'BUILDER_PACKAGE_ROOT': str(self.root)}):
            self.assertEqual(hs.package_root(str(SRC / 'tools/iter/iterate.py')), self.root)
        self.assertEqual(hs.package_root(str(SRC / 'tools/iter/iterate.py')), SRC)

    def test_27_shader_records_root(self):
        pack = load('package_shader_pack', SRC / 'tools/iter/generic_shader_pack.py')
        out = self.root / 'pack.bin'
        with mock.patch.object(sys, 'argv', ['generic_shader_pack.py', '--root', str(self.root), '--corpus', 'empty-*', '--out', str(out)]):
            pack.main()
        self.assertEqual(pack.ROOT, self.root)
        self.assertEqual(out.read_bytes()[:4], b'GSP2')

    def test_28_nm_failure_not_empty_hook_set(self):
        mod = load('package_direct', SRC / 'tools/fp_codegen/direct_calls.py')
        (self.root / 'synthetic.o').write_bytes(b'synthetic')
        def process(argv, **kwargs):
            self.assertTrue(kwargs.get('check'), 'failed nm must never become an empty hook list')
            self.assertNotIn('/opt/homebrew', argv[0])
            raise subprocess.CalledProcessError(7, argv)
        with mock.patch.object(mod.subprocess, 'run', side_effect=process), self.assertRaises(subprocess.CalledProcessError):
            mod.hooked_from(self.root)

    def test_29_regenerator_without_checkout(self):
        mod = load('package_regen', SRC / 'tools/own_disc/regen_check.py')
        source, config, exe, context = [self.root / s for s in ('synthetic-input', 'config.toml', 'generator-standin', 'context.h')]
        for f in (source, exe, context): f.write_bytes(b'synthetic')
        config.write_text('file_path = "synthetic-input"\nout_directory_path = "./old"\n')
        work, report = self.root / 'regen-work', self.root / 'regen.json'
        def process(argv, **kwargs):
            self.assertEqual(argv[0], str(exe))
            (work / 'ppc/ppc_context.h').write_bytes(b'synthetic support')
            return subprocess.CompletedProcess(argv, 0)
        with mock.patch.object(mod.subprocess, 'run', side_effect=process), mock.patch.object(hs.shutil, 'which', return_value=None), \
             mock.patch.object(sys, 'argv', ['regen_check.py', '--generator', str(exe), '--context', str(context), '--config-from', str(config), '--work', str(work), '--report', str(report), '--keep']):
            self.assertEqual(mod.main(), 0)
        self.assertEqual(json.loads(report.read_text())['generated_files'], 1)

    def test_30_compile_cache_connected_header(self):
        base = self.root / 'base'; base.mkdir()
        attempts = self.root / 'local-analysis/attempts'; attempt = attempts / 'v1'
        srcdir = attempt / 'src'; srcdir.mkdir(parents=True)
        (srcdir / 'unit.cpp').write_bytes(b'synthetic source')
        header = self.root / 'header é space.h'; header.write_bytes(b'header one')
        plan = {'plans': [{'source': 'unit.cpp', 'spec': 'base/unit.json', 'output': 'base/unit.o'}]}
        (base / 'plan.json').write_text(json.dumps(plan))
        (base / 'unit.json').write_text(json.dumps({'command': ['synthetic-clang', '-c', 'unit.cpp', '-o', 'base/unit.o']}))
        (base / 'link-plan.json').write_text(json.dumps({'inputs': {}, 'command': ['synthetic-link', str(base / 'unit.o'), '-o', 'old.so']}))
        (attempt / 'attempt.json').write_text(json.dumps({'files': [{'name': 'unit.cpp', 'origin': 'original/unit.cpp'}]}))
        counts = {'compile': 0, 'link': 0}
        def process(argv, **kwargs):
            out = Path(argv[argv.index('-o')+1])
            if argv[0] == 'synthetic-clang':
                counts['compile'] += 1
                out.write_bytes(b'OBJECT:' + header.read_bytes())
                dep = Path(argv[argv.index('-MF')+1])
                escaped = str(header).replace(' ', '\\ ')
                dep.write_text('unit.o: ' + escaped + '\n', encoding='utf-8')
            else:
                counts['link'] += 1
                self.assertEqual(argv[0], 'synthetic-link')
                out.write_bytes(Path(argv[1]).read_bytes())
            return subprocess.CompletedProcess(argv, 0, stdout=b'')
        with mock.patch.object(it, 'ROOT', self.root), mock.patch.object(it, 'ATTEMPTS', attempts), \
             mock.patch.object(it, 'load_config', return_value={'base_build': 'base', 'build_cwd': '.'}), \
             mock.patch.object(it.subprocess, 'run', side_effect=process), mock.patch.object(it, 'clone_or_copy', side_effect=shutil.copy2):
            one = it.cmd_build('v1').read_bytes()
            self.assertEqual(it.cmd_build('v1').read_bytes(), one)
            self.assertEqual(counts['compile'], 1)
            header.write_bytes(b'header two changed')
            two = it.cmd_build('v1').read_bytes()
            self.assertNotEqual(one, two)
            self.assertEqual(counts, {'compile': 2, 'link': 3})

    def test_31_tree_identity_order(self):
        class CasefoldPath(type(Path())):
            def __lt__(self, other): return str(self).casefold() < str(other).casefold()
        # Mimic Windows Path ordering on this host, without claiming NTFS access.
        upper, lower = self.root / 'Z.txt', self.root / 'a.txt'
        upper.write_bytes(b'upper'); lower.write_bytes(b'lower')
        paths = [CasefoldPath(lower), CasefoldPath(upper)]
        expected = hashlib.sha256()
        for p in (upper, lower):
            expected.update(p.name.encode() + b'\0' + db.sha256_file(p).encode() + b'\n')
        with mock.patch.object(Path, 'rglob', return_value=iter(paths)):
            digest, count = db.sha256_tree(self.root)
        self.assertEqual((digest, count), (expected.hexdigest(), 2))

    def test_32_compile_concurrency_cap(self):
        with mock.patch.dict(os.environ):
            os.environ.pop('BUILDER_MAX_JOBS', None)
            self.assertEqual(it.build_workers(99, 10), 10)      # what the attempt asks for
            self.assertEqual(it.build_workers(2, 10), 2)        # never more than there are files
            self.assertEqual(it.build_workers(0, 0), 1)
            os.environ['BUILDER_MAX_JOBS'] = '4'                # a machine short of memory lowers it
            self.assertEqual(it.build_workers(99, 10), 4)
            self.assertEqual(it.build_workers(99, 2), 2)

    def test_33_legacy_stale_path_guard(self):
        cfg = {'build_cwd': '.'}
        output = '/tools/clang++ -I/legacy/Documents/tree/inc -c old.cpp -o old.o -MF old.d -MT old.o\n'
        result = subprocess.CompletedProcess(['ninja'], 0, stdout=output)
        with mock.patch.object(it, 'ROOT', self.root), mock.patch.object(it.subprocess, 'run', return_value=result):
            with self.assertRaisesRegex(SystemExit, 'unmapped Documents'):
                it.ppc_chunk_command(cfg, 1, self.root/'s', self.root/'o', self.root/'d')
            cfg['path_mappings'] = {'/legacy/Documents/tree': 'relocated tree'}
            argv, _ = it.ppc_chunk_command(cfg, 1, self.root/'s', self.root/'o', self.root/'d')
            self.assertIn('-I'+str(self.root/'relocated tree/inc'), argv)
        # without a mapping of its own the ninja route maps the one old root it was made for, as it always did
        output = f'/tools/clang++ "-I{it.DOCUMENTS_ROOT}/inc" -c old.cpp -o old.o -MF old.d -MT old.o\n'
        result = subprocess.CompletedProcess(['ninja'], 0, stdout=output)
        with mock.patch.object(it, 'ROOT', self.root), mock.patch.object(it.subprocess, 'run', return_value=result):
            argv, _ = it.ppc_chunk_command({'build_cwd': '.'}, 1, self.root/'s', self.root/'o', self.root/'d')
            self.assertIn('-I'+str(self.root/'inc'), argv)

    def test_34_priority_on_one_scale(self):
        # the system's own nice value here; on Windows the priority class, with "below normal" (what low_priority starts) as 10
        self.assertEqual(hs.effective_nice(), os.getpriority(os.PRIO_PROCESS, 0))
        self.assertEqual([hs.nice_of_priority_class(c) for c in (0x40, 0x4000, 0x20, 0x8000, 0x80, 0x100)], [19, 10, 0, -5, -10, -20])
        with self.assertRaisesRegex(ValueError, 'unknown process priority class'): hs.nice_of_priority_class(0x1234)
        with mock.patch.object(hs.sys, 'platform', 'win32'), mock.patch.object(hs, '_windows_priority_class', return_value=0x4000), \
             mock.patch.object(hs.os, 'getpriority', side_effect=AssertionError('no POSIX call on Windows')):
            self.assertEqual(hs.effective_nice(), 10)
            self.assertEqual(hs.low_priority(['tool'], 10), (['tool'], {'creationflags': 0x4000}))   # the class asked for is the class read back as 10

    def test_35_positioned_read_without_pread(self):
        path = self.root / 'data.bin'; path.write_bytes(bytes(range(256)) * 40)
        fd = os.open(path, os.O_RDONLY | getattr(os, 'O_BINARY', 0))
        try:
            for size, offset in ((0, 0), (16, 0), (300, 250), (4096, 9000), (500, 10100)):   # the last one runs past the end
                self.assertEqual(hs.pread_by_seek(fd, size, offset), os.pread(fd, size, offset), (size, offset))
            self.assertEqual(len(hs.pread_by_seek(fd, 500, 10100)), 140)
            # two threads reading at different places never get each other's bytes
            from concurrent.futures import ThreadPoolExecutor
            jobs = [(64, (i * 37) % 10000) for i in range(400)]
            with ThreadPoolExecutor(8) as pool:
                got = list(pool.map(lambda j: hs.pread_by_seek(fd, *j), jobs))
            self.assertEqual(got, [os.pread(fd, *j) for j in jobs])
        finally:
            os.close(fd)
        self.assertIs(hs.pread, os.pread)        # this system has its own; the fallback is for one that has none

    def test_36_release_at_the_package_root(self):
        # started by the launcher (it names the package root): the inventory there; without one: beside the front end
        with mock.patch.dict(os.environ, {'BUILDER_PACKAGE_ROOT': str(self.root)}):
            self.assertEqual(b.default_release(), self.root / 'release.json')
        environment = {k: v for k, v in os.environ.items() if k != 'BUILDER_PACKAGE_ROOT'}
        with mock.patch.dict(os.environ, environment, clear=True):
            self.assertEqual(b.default_release(), Path(b.__file__).with_name('release.json'))
        # and main() takes the package root's file when it is there: a release for another host is refused by name
        (self.root / 'release.json').write_text(json.dumps({'schema': 'builder-release/1', 'host': 'elsewhere-x'}))
        with mock.patch.dict(os.environ, {'BUILDER_PACKAGE_ROOT': str(self.root)}), contextlib.redirect_stdout(io.StringIO()) as said:
            self.assertEqual(b.main(['fetch']), 2)
        self.assertIn('wrong or missing release', said.getvalue())

    def test_37_check_passes_a_profile_whose_command_lines_name_the_discs(self):
        # the plan is checked before any disc is given: a stage's command line may still name one
        self.fetch(); (self.root / 'fixture').mkdir()
        fx = fixture.Fixture(self.root / 'fixture')
        for stage in fx.profile['stages']:
            if stage['adapter'] == 'command': stage['argv'][0] = '${python}'
        fx.stage('shaders')['source'] = '${repo}/pack.bin'
        fx.stage('codegen')['argv'] += ['--play', '${disc:play}', '--install', '${disc:install}']
        fx.profile_path.write_text(json.dumps(fx.profile))
        shutil.copyfile(fx.profile_path, fx.side / 'profile.json')
        self.release.update(schema='builder-release/1', host=b.host_id(), profile='profile.json',
                            files=[dict(path=p.relative_to(fx.side).as_posix(), sha256=db.sha256_file(p)) for p in fx.side.rglob('*') if p.is_file()],
                            estimates=dict(min_free_bytes=1, build_time='synthetic fixture only'))
        release = fx.side / 'release.json'; release.write_text(json.dumps(self.release))
        with contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(b.main(['--release', str(release), '--cache', str(self.cache), 'check', '--work', str(fx.work)]), 0, out.getvalue())
        self.assertIn('Payload, compiler and stage prerequisites verified', out.getvalue())
        self.assertFalse(fx.work.exists())                    # nothing ran, nothing was written
        # a disc the engine does not know is still a mistake of the profile
        fx.stage('codegen')['argv'] += ['${disc:third}']
        fx.profile_path.write_text(json.dumps(fx.profile)); shutil.copyfile(fx.profile_path, fx.side / 'profile.json')
        self.release['files'] = [dict(path=p.relative_to(fx.side).as_posix(), sha256=db.sha256_file(p)) for p in fx.side.rglob('*') if p.is_file() and p.name != 'release.json']
        release.write_text(json.dumps(self.release))
        with contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(b.main(['--release', str(release), '--cache', str(self.cache), 'check', '--work', str(fx.work)]), db.EXIT_USAGE, out.getvalue())
        self.assertIn('needs that disc on the command line', out.getvalue())

    def test_37b_check_plans_the_update_disc_for_a_title_update_build(self):
        # 1.3.0 reship (taken down 2026-11-26): the released builders refused every TU 27 build at check() - "stage update-patch-file:
        # needs the update disc (not given)" - because check planned only the play and install discs. A title-update build plans the
        # update disc too; a build without the title update still refuses a profile whose stages need it.
        self.fetch(); (self.root / 'fixture').mkdir()
        fx = fixture.Fixture(self.root / 'fixture')
        for stage in fx.profile['stages']:
            if stage['adapter'] == 'command': stage['argv'][0] = '${python}'
        fx.stage('shaders')['source'] = '${repo}/pack.bin'
        fx.profile['stages'].insert(1, {'id': 'update-patch-file', 'adapter': 'disc-file', 'disc': 'update', 'path': 'default.xexp',
                                        'outputs': {'patch': 'input/default.xexp'}})
        fx.profile_path.write_text(json.dumps(fx.profile))
        shutil.copyfile(fx.profile_path, fx.side / 'profile.json')
        shutil.copyfile(fx.profile_path, fx.side / 'profile-tu.json')
        self.release.update(schema='builder-release/1', host=b.host_id(), profile='profile.json',
                            title_update_profiles={'default': 'profile-tu.json'},
                            files=[dict(path=p.relative_to(fx.side).as_posix(), sha256=db.sha256_file(p)) for p in fx.side.rglob('*') if p.is_file()],
                            estimates=dict(min_free_bytes=1, build_time='synthetic fixture only'))
        with contextlib.redirect_stdout(io.StringIO()) as out:
            b.check(self.release, fx.side, fx.work.resolve(), self.cache.resolve(), None, title_update=True)
        self.assertIn('Payload, compiler and stage prerequisites verified', out.getvalue())
        with contextlib.redirect_stdout(io.StringIO()), self.assertRaises(b.Refusal) as refused:
            b.check(self.release, fx.side, fx.work.resolve(), self.cache.resolve(), None, title_update=False)
        self.assertIn('needs the update disc', str(refused.exception))

    def packaged(self, change=None):
        self.fetch(); (self.root / 'fixture').mkdir()
        fx = fixture.Fixture(self.root / 'fixture')
        for stage in fx.profile['stages']:
            if stage['adapter'] == 'command': stage['argv'][0] = '${python}'
        fx.stage('shaders')['source'] = '${repo}/pack.bin'
        if change: change(fx)
        fx.profile_path.write_text(json.dumps(fx.profile))
        shutil.copyfile(fx.profile_path, fx.side / 'profile.json')
        self.release.update(schema='builder-release/1', host=b.host_id(), profile='profile.json',
                            files=[dict(path=p.relative_to(fx.side).as_posix(), sha256=db.sha256_file(p)) for p in fx.side.rglob('*') if p.is_file()],
                            estimates=dict(min_free_bytes=3 << 30, build_time='synthetic fixture only'))
        release = fx.side / 'release.json'; release.write_text(json.dumps(self.release))
        return fx, release

    def test_38_a_build_without_a_work_folder_uses_the_package_s_own(self):
        fx, release = self.packaged()
        self.assertEqual(b.default_work(fx.side), fx.side / 'local-analysis' / 'builder' / 'work')
        with contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(b.main(['--release', str(release), '--cache', str(self.cache), 'build', '--play-disc', str(fx.disc)]), 0, out.getvalue())
        made = fx.side / 'local-analysis' / 'builder' / 'work' / 'out' / 'code-fixture-generic.bundle'
        self.assertTrue(made.is_file())
        self.assertFalse(fx.work.exists())
        with contextlib.redirect_stdout(io.StringIO()) as out:          # and the check alone looks at the same folder
            self.assertEqual(b.main(['--release', str(release), '--cache', str(self.cache), 'check']), 0, out.getvalue())
        with contextlib.redirect_stdout(io.StringIO()) as out:          # a folder that is named is the one used
            self.assertEqual(b.main(['--release', str(release), '--cache', str(self.cache), 'build', '--work', str(fx.work), '--play-disc', str(fx.disc)]), 0, out.getvalue())
        self.assertTrue(fx.bundle().is_file())

    def test_39_a_build_runs_its_long_stages_without_a_further_switch(self):
        fx, release = self.packaged(lambda fx: fx.stage('codegen').update(heavy=True))
        argv = ['--release', str(release), '--cache', str(self.cache), 'build', '--work', str(fx.work), '--play-disc', str(fx.disc)]
        with contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(b.main(argv), 0, out.getvalue())
        self.assertEqual(fx.executed(), ['decode', 'codegen', 'link'])
        with contextlib.redirect_stdout(io.StringIO()) as out:          # the old switch is still taken
            self.assertEqual(b.main(argv + ['--allow-heavy']), 0, out.getvalue())

    def test_40_started_without_a_word_it_says_what_to_do(self):
        fx, release = self.packaged()
        with mock.patch.dict(os.environ, {'BUILDER_PACKAGE_ROOT': str(fx.side)}), contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(b.main([]), 0)
        said = out.getvalue()
        for part in ('builder fetch --accept-ndk-license', 'builder build --play-disc', 'builder verify',
                     f'{self.ndk["size"] / 1e6:.0f} MB from {self.ndk["url"]}', self.ndk['license_url'], '3 GiB of free space',
                     str(Path('local-analysis') / 'builder' / 'work' / 'out')):
            self.assertIn(part, said)
        self.assertNotIn('--accept-dxc-license', said)                    # this release names no shader compiler to fetch
        self.assertFalse((fx.side / 'local-analysis').exists())          # it only speaks
        # without a readable inventory it still says what to do, with no numbers
        with mock.patch.dict(os.environ, {'BUILDER_PACKAGE_ROOT': str(self.root / 'nowhere')}), contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(b.main([]), 0)
        self.assertIn('builder build --play-disc', out.getvalue())
        self.assertIn('from its publisher', out.getvalue())
        # with a word it is the command line as before
        with contextlib.redirect_stdout(io.StringIO()) as out, contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            b.main(['no-such-step'])
        self.assertNotIn('builder fetch', out.getvalue())


    # The shader compiler the builder fetches from its publisher instead of carrying it (a synthetic archive here).
    DXC_FILES = (('bin/x64/dxc.exe', 'host-tools/dxc/bin/dxc-3.7.exe', b'SYNTHETIC shader compiler, not executable'),
                 ('bin/x64/dxcompiler.dll', 'host-tools/dxc/lib/dxcompiler.dll', b'SYNTHETIC compiler library'),
                 ('LICENSE-MIT.txt', 'host-tools/dxc/LICENSE-MIT.txt', b'SYNTHETIC terms'))

    def dxc(self, members=None, extra=()):
        """A package root, the archive (its members named with backslashes, as the publisher's are) and the pins."""
        root = self.root / 'package'; root.mkdir(exist_ok=True)
        archive = self.root / 'dxc-fixture.zip'
        with zipfile.ZipFile(archive, 'w') as z:
            for member, _, data in (self.DXC_FILES if members is None else members) + tuple(extra):
                z.writestr(zipfile.ZipInfo(member.replace('/', '\\')), data)
        self.release['dxc'] = dict(url='https://github.com/microsoft/DirectXShaderCompiler/releases/download/v0.0.fixture/dxc_fixture.zip',
                                   sha256=db.sha256_file(archive), size=archive.stat().st_size,
                                   license_url='https://github.com/microsoft/DirectXShaderCompiler/blob/v0.0.fixture/LICENSE.TXT',
                                   files=[dict(member=member, path=place, size=len(data), sha256=hashlib.sha256(data).hexdigest())
                                          for member, place, data in self.DXC_FILES])
        return root, archive

    def placed(self, root):
        return sorted(p.relative_to(root).as_posix() for p in root.rglob('*') if p.is_file())

    def test_41_shader_compiler_pins_fail_closed(self):
        root, archive = self.dxc()
        self.assertIs(b.dxc_spec(self.release), self.release['dxc'])
        self.assertIsNone(b.dxc_spec({'ndk': self.ndk}))                  # a release that names none: nothing to fetch, nothing to check
        with contextlib.redirect_stdout(io.StringIO()) as out:
            b.fetch_dxc({'ndk': self.ndk}, root, self.cache, False, archive); b.existing_dxc({'ndk': self.ndk}, root)
        self.assertEqual((out.getvalue(), self.placed(root), self.cache.exists()), ('', [], False))
        good = json.dumps(self.release['dxc'])
        def broken(change, message):
            d = json.loads(good); change(d)
            with mock.patch.object(b.urllib.request, 'build_opener', side_effect=AssertionError('no network')):
                for call in (lambda r: b.dxc_spec(r), lambda r: b.existing_dxc(r, root), lambda r: b.fetch_dxc(r, root, self.cache, True, archive)):
                    with self.assertRaisesRegex(b.Refusal, message): call({'ndk': self.ndk, 'dxc': d})
            self.assertEqual((self.placed(root), self.cache.exists()), ([], False))
        broken(lambda d: d.pop('sha256'), 'SHA-256/size not pinned')
        broken(lambda d: d.update(sha256='0' * 63), 'SHA-256/size not pinned')
        broken(lambda d: d.update(size=0), 'SHA-256/size not pinned')
        broken(lambda d: d.update(size=str(d['size'])), 'SHA-256/size not pinned')
        broken(lambda d: d.update(url='https://github.com/someone-else/DirectXShaderCompiler/releases/download/v1/dxc.zip'), 'not a pinned Microsoft release file')
        broken(lambda d: d.update(url=d['url'].replace('https://', 'http://')), 'not a pinned Microsoft release file')
        broken(lambda d: d.update(url=d['url'] + '?x=1'), 'not a pinned Microsoft release file')
        broken(lambda d: d.update(url='https://github.com/microsoft/DirectXShaderCompiler/releases/latest/download/dxc.zip'), 'not a pinned Microsoft release file')
        broken(lambda d: d.pop('license_url'), 'licence link missing')
        broken(lambda d: d.update(license_url='https://example.org/LICENSE'), 'licence link missing')
        broken(lambda d: d.update(files=[]), 'files not listed')
        broken(lambda d: d.pop('files'), 'files not listed')
        broken(lambda d: d['files'][1].pop('sha256'), 'file not pinned')
        broken(lambda d: d['files'][1].update(size=0), 'file not pinned')
        broken(lambda d: d['files'][0].update(path='../outside.exe'), 'invalid package path')
        broken(lambda d: d['files'][0].update(path='/absolute.exe'), 'invalid package path')
        broken(lambda d: d['files'][0].update(member='..\\x'), 'invalid package path')
        broken(lambda d: d['files'][1].update(path=d['files'][0]['path'].upper()), 'share a place')

    def test_42_shader_compiler_fetch_asks_consent_before_any_request(self):
        root, archive = self.dxc()
        with mock.patch.object(b.urllib.request, 'build_opener', side_effect=AssertionError('no network')), contextlib.redirect_stdout(io.StringIO()) as out:
            with self.assertRaisesRegex(b.Refusal, 'licence not accepted.*--accept-dxc-license'):
                b.fetch_dxc(self.release, root, self.cache, False)
            with self.assertRaisesRegex(b.Refusal, 'licence not accepted'):      # an archive on disk does not stand for the player's word either
                b.fetch_dxc(self.release, root, self.cache, False, archive)
        self.assertEqual((self.placed(root), self.cache.exists()), ([], False))
        for part in (self.release['dxc']['url'], str(self.release['dxc']['size']), self.release['dxc']['license_url']):
            self.assertIn(part, out.getvalue())                              # what is fetched and its terms are named first
        # too little room is said before any request too
        with mock.patch.object(b.shutil, 'disk_usage', return_value=mock.Mock(free=self.release['dxc']['size'] + sum(len(d) for _, _, d in self.DXC_FILES) - 1)), \
                mock.patch.object(b.urllib.request, 'build_opener', side_effect=AssertionError('no network')), contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(b.Refusal, 'not enough disk'):
                b.fetch_dxc(self.release, root, self.cache, True)
        self.assertEqual(self.placed(root), [])

    def test_43_shader_compiler_archive_checked_before_anything_is_placed(self):
        root, archive = self.dxc()
        pins = self.release['dxc']
        def refused(message, source=None):
            with contextlib.redirect_stdout(io.StringIO()), self.assertRaisesRegex(b.Refusal, message):
                b.fetch_dxc(self.release, root, self.cache, True, source or archive)
            self.assertEqual(self.placed(root), [])
        right = pins['sha256']; pins['sha256'] = '0' * 64
        refused('archive hash/size mismatch'); pins['sha256'] = right
        pins['size'] -= 1
        refused('archive exceeds pinned size'); pins['size'] += 2
        refused('archive hash/size mismatch'); pins['size'] -= 1
        # the archive is the pinned one, a file inside is not the pinned file: none of the files is placed
        last = pins['files'][2]
        was = dict(last); last['sha256'] = hashlib.sha256(b'another text').hexdigest()
        refused('LICENSE-MIT.txt is not the pinned file'); last.update(was)
        last['size'] += 1
        refused('archive lacks LICENSE-MIT.txt as pinned'); last.update(was)
        last['member'] = 'LICENSE-OTHER.txt'
        refused('archive lacks LICENSE-OTHER.txt as pinned'); last.update(was)
        with contextlib.redirect_stdout(io.StringIO()):
            b.fetch_dxc(self.release, root, self.cache, True, archive)      # and with the pins as they were it goes through
        self.assertEqual(len(self.placed(root)), 3)

    def test_44_shader_compiler_fetch_to_its_places(self):
        root, archive = self.dxc(extra=(('bin/arm64/dxc.exe', None, b'not asked for'),))
        with self.assertRaisesRegex(b.Refusal, 'shader compiler missing.*--accept-dxc-license'): b.existing_dxc(self.release, root)
        with contextlib.redirect_stdout(io.StringIO()) as out:
            b.fetch_dxc(self.release, root, self.cache, True, archive)
        self.assertEqual(self.placed(root), sorted(place for _, place, _ in self.DXC_FILES))      # the listed files and no other
        for _, place, data in self.DXC_FILES:
            self.assertEqual((root / place).read_bytes(), data)
        self.assertIn('3 of 3 files put below', out.getvalue())
        b.existing_dxc(self.release, root)
        stamps = [(root / place).stat().st_mtime_ns for _, place, _ in self.DXC_FILES]
        # a second fetch finds them, opens nothing and needs no further word
        with mock.patch.object(b.urllib.request, 'build_opener', side_effect=AssertionError('no network')), contextlib.redirect_stdout(io.StringIO()) as out:
            b.fetch_dxc(self.release, root, self.cache, False)
            b.fetch_dxc(self.release, root, self.cache, False, self.root / 'no-such-archive.zip')      # nor is an archive opened
        self.assertIn('already verified', out.getvalue())
        self.assertEqual([(root / place).stat().st_mtime_ns for _, place, _ in self.DXC_FILES], stamps)
        (root / self.DXC_FILES[1][1]).write_bytes(b'changed')
        with self.assertRaisesRegex(b.Refusal, 'shader compiler file changed: host-tools/dxc/lib/dxcompiler.dll'): b.existing_dxc(self.release, root)

    def test_45_shader_compiler_fetch_writes_over_nothing(self):
        root, archive = self.dxc()
        first, second, third = (root / place for _, place, _ in self.DXC_FILES)
        first.parent.mkdir(parents=True); first.write_bytes(self.DXC_FILES[0][2])       # one file is there already, the pinned one
        stamp = first.stat().st_mtime_ns
        with contextlib.redirect_stdout(io.StringIO()) as out:
            b.fetch_dxc(self.release, root, self.cache, True, archive)
        self.assertIn('2 of 3 files put below', out.getvalue())
        self.assertEqual(first.stat().st_mtime_ns, stamp)
        b.existing_dxc(self.release, root)
        # another file at a place: refused before any request, it stays as it is and nothing else is placed
        third.unlink(); second.write_bytes(b'the player\'s own file')
        with mock.patch.object(b.urllib.request, 'build_opener', side_effect=AssertionError('no network')), contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(b.Refusal, 'another file lies where the shader compiler goes: host-tools/dxc/lib/dxcompiler.dll; nothing is written over'):
                b.fetch_dxc(self.release, root, self.cache, True)
        self.assertEqual(second.read_bytes(), b'the player\'s own file'); self.assertFalse(third.exists())
        second.unlink(); second.mkdir()
        with contextlib.redirect_stdout(io.StringIO()), self.assertRaisesRegex(b.Refusal, 'something that is no file lies where the shader compiler goes'):
            b.fetch_dxc(self.release, root, self.cache, True, archive)
        self.assertFalse(third.exists())
        # a file that appears at a place while the archive is being fetched is not written over either
        second.rmdir()
        def opener(url, timeout):
            second.write_bytes(b'appeared meanwhile'); return archive.open('rb')
        with contextlib.redirect_stdout(io.StringIO()), self.assertRaisesRegex(b.Refusal, 'a file appeared where the shader compiler goes: host-tools/dxc/lib/dxcompiler.dll; nothing is written over'):
            b.fetch_dxc(self.release, root, self.cache, True, opener=opener)
        self.assertEqual(second.read_bytes(), b'appeared meanwhile')

    def test_46_shader_compiler_comes_from_its_publisher_only(self):
        root, archive = self.dxc()
        asked = []
        def opener(url, timeout):
            asked.append((url, timeout)); return archive.open('rb')
        with contextlib.redirect_stdout(io.StringIO()):
            b.fetch_dxc(self.release, root, self.cache, True, opener=opener)
        self.assertEqual(asked, [(self.release['dxc']['url'], 60)])
        self.assertEqual(len(self.placed(root)), 3)
        handler = b.PublisherOnlyRedirect()
        request = b.urllib.request.Request(self.release['dxc']['url'])
        for elsewhere in ('https://example.org/dxc.zip', 'http://objects.githubusercontent.com/x', 'https://github.com/someone-else/DirectXShaderCompiler/x',
                          'https://objects.githubusercontent.com.example.org/x'):
            with self.assertRaisesRegex(b.Refusal, 'left the publisher'): handler.redirect_request(request, None, 302, 'Found', {}, elsewhere)
        for own in ('https://objects.githubusercontent.com/github-production-release-asset/x', 'https://release-assets.githubusercontent.com/x',
                    'https://github.com/microsoft/DirectXShaderCompiler/releases/download/v0.0.fixture/dxc_fixture.zip'):
            self.assertEqual(handler.redirect_request(request, None, 302, 'Found', {}, own).full_url, own)

    def test_47_front_end_fetches_checks_and_names_the_shader_compiler(self):
        fx, release = self.packaged()
        root, archive = self.dxc()
        release.write_text(json.dumps(self.release))
        base = ['--release', str(release), '--cache', str(self.cache)]
        places = [fx.side / place for _, place, _ in self.DXC_FILES]
        with contextlib.redirect_stdout(io.StringIO()) as out:              # a build is refused while the pinned compiler is not there
            self.assertEqual(b.main(base + ['build', '--play-disc', str(fx.disc)]), 2)
        self.assertIn('REFUSED: shader compiler missing; read its terms and run builder fetch --accept-dxc-license', out.getvalue())
        self.assertFalse((fx.side / 'local-analysis').exists())
        with contextlib.redirect_stdout(io.StringIO()) as out:              # the compiler's switch alone does not stand for this one
            self.assertEqual(b.main(base + ['fetch', '--accept-ndk-license', '--archive', str(self.archive), '--dxc-archive', str(archive)]), 2)
        self.assertIn('--accept-dxc-license', out.getvalue()); self.assertFalse(any(p.exists() for p in places))
        with contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(b.main(base + ['fetch', '--accept-ndk-license', '--archive', str(self.archive), '--accept-dxc-license', '--dxc-archive', str(archive)]), 0, out.getvalue())
        self.assertEqual([p.read_bytes() for p in places], [data for _, _, data in self.DXC_FILES])      # below the package root, not below this test's other folder
        self.assertEqual(self.placed(root), [])
        with contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(b.main(base + ['build', '--play-disc', str(fx.disc)]), 0, out.getvalue())
        places[0].write_bytes(b'changed')
        with contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(b.main(base + ['check']), 2)
        self.assertIn('shader compiler file changed: host-tools/dxc/bin/dxc-3.7.exe', out.getvalue())
        # started without a word it names both fetches, both switches and both sets of terms
        with mock.patch.dict(os.environ, {'BUILDER_PACKAGE_ROOT': str(fx.side)}), contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(b.main([]), 0)
        pins = self.release['dxc']
        for part in ('builder fetch --accept-ndk-license --accept-dxc-license', 'each switch says you accept the one', self.ndk['license_url'],
                     f'DirectX Shader Compiler, {pins["size"] / 1e6:.0f} MB from {pins["url"]}', f'its terms: {pins["license_url"]}'):
            self.assertIn(part, out.getvalue())

    def make_dmg(self, build):
        """a synthetic macOS NDK disk image (hdiutil, offline): build(folder) fills AndroidNDK1.app/Contents/NDK"""
        src = self.root / 'dmg-src'; ndk = src / 'AndroidNDK1.app/Contents/NDK'; ndk.mkdir(parents=True)
        build(ndk)
        dmg = self.root / 'ndk-fixture.dmg'
        subprocess.run(['/usr/bin/hdiutil', 'create', '-quiet', '-fs', 'HFS+', '-format', 'UDZO', '-srcfolder', str(src),
                        '-volname', 'NDK', str(dmg)], check=True)
        self.archive = dmg
        self.ndk.update(url='https://dl.google.com/android/repository/android-ndk-fixture-darwin.dmg', size=dmg.stat().st_size,
                        sha256=db.sha256_file(dmg), image_root='AndroidNDK1.app/Contents/NDK')

    @unittest.skipUnless(sys.platform == 'darwin', 'disk images are read on macOS only')
    def test_48_macos_disk_image_installs_the_declared_folder_under_the_zip_rules(self):
        def build(ndk):
            (ndk / 'bin').mkdir(); (ndk / 'bin/clang').write_bytes(b'SYNTHETIC compiler'); (ndk / 'bin/clang').chmod(0o755)
            (ndk / 'bin/clang++').symlink_to('clang')
            (ndk / 'NOTICE').write_text('synthetic notice', encoding='utf-8', newline='\n')
        self.make_dmg(build)
        self.fetch(); root = b.existing_ndk(self.cache, self.ndk)
        self.assertEqual((root / 'bin/clang++').read_bytes(), b'SYNTHETIC compiler')    # the link, as a verified copy
        self.assertFalse((root / 'bin/clang++').is_symlink())
        self.assertTrue(os.access(root / 'bin/clang', os.X_OK))
        self.assertEqual((root / 'NOTICE').read_text(encoding='utf-8'), 'synthetic notice')
        self.assertFalse(any(p.name.endswith('.app') for p in (self.cache / self.ndk['sha256'][:16]).rglob('*')))
        mounted = subprocess.run(['/usr/bin/hdiutil', 'info'], capture_output=True, text=True).stdout
        self.assertNotIn(str(self.cache), mounted, 'the image was detached')

    @unittest.skipUnless(sys.platform == 'darwin', 'disk images are read on macOS only')
    def test_49_macos_disk_image_refusals(self):
        def escaping(ndk):
            (ndk / 'bin').mkdir(); (ndk / 'bin/clang').symlink_to('../../../../outside')
        self.make_dmg(escaping)
        with self.assertRaisesRegex(b.Refusal, 'link escapes its root|invalid package path'), contextlib.redirect_stdout(io.StringIO()):
            b.fetch(self.release, self.cache, True, self.archive)
        self.assertFalse((self.cache / self.ndk['sha256'][:16]).exists())
        self.ndk['image_root'] = 'AndroidNDK2.app/Contents/NDK'
        with self.assertRaisesRegex(b.Refusal, 'no NDK folder at the declared root'), contextlib.redirect_stdout(io.StringIO()):
            b.fetch(self.release, self.cache, True, self.archive)
        for root in ('', '../x', '/abs'):
            self.ndk['image_root'] = root
            with self.subTest(image_root=root), self.assertRaises(b.Refusal): b.ndk_spec(self.release)
        mounted = subprocess.run(['/usr/bin/hdiutil', 'info'], capture_output=True, text=True).stdout
        self.assertNotIn(str(self.cache), mounted, 'every image was detached')

    def test_50_an_ios_target_takes_its_own_profile_and_the_player_s_bundle_id(self):
        fx, release = self.packaged()
        ios = json.loads(fx.profile_path.read_text())
        ios['stages'][[s['id'] for s in ios['stages']].index('codegen')]['argv'] += ['--bundle-id', '${bundleid}']
        (fx.side / 'profile-ios.json').write_text(json.dumps(ios))
        self.release.update(profiles={'ios': 'profile-ios.json'},
                            files=[dict(path=p.relative_to(fx.side).as_posix(), sha256=db.sha256_file(p)) for p in fx.side.rglob('*')
                                   if p.is_file() and p.name != 'release.json'])
        release.write_text(json.dumps(self.release))
        base = ['--release', str(release), '--cache', str(self.cache)]
        with contextlib.redirect_stdout(io.StringIO()) as out:              # a malformed id: the profile's variable refuses it
            self.assertEqual(b.main(base + ['build', '--ios', '--bundle-id', 'no dots', '--work', str(fx.work), '--play-disc', str(fx.disc)]),
                             db.EXIT_USAGE, out.getvalue())
        self.assertIn('bundle id missing or malformed', out.getvalue())
        with contextlib.redirect_stdout(io.StringIO()) as out:              # the default target is unaffected
            self.assertEqual(b.main(base + ['check']), 0, out.getvalue())
        self.release['profile'] = 'profile-ios.json'                         # a release whose default is the iOS target
        release.write_text(json.dumps(self.release))
        with contextlib.redirect_stdout(io.StringIO()) as out:              # check plans it with a stand-in id, writes no settings
            self.assertEqual(b.main(base + ['check']), 0, out.getvalue())
        self.assertFalse((fx.side / 'local-analysis' / 'builder' / b.SETTINGS_NAME).exists())
        self.release['profile'] = 'profile.json'; release.write_text(json.dumps(self.release))
        with contextlib.redirect_stdout(io.StringIO()) as out:              # a target the release does not make
            self.assertEqual(b.main(base + ['build', '--duo', '--bundle-id', 'com.example.x', '--play-disc', str(fx.disc)]), 2, out.getvalue())
        self.assertIn('makes no duo app', out.getvalue())
        self.release['profiles'] = {'ios': 'not-listed.json'}               # a target profile outside the inventory
        release.write_text(json.dumps(self.release))
        with self.assertRaisesRegex(b.Refusal, 'target profile is not hash-bound'):
            b.load_release(release)

    def test_50b_the_players_app_icon_reaches_each_targets_build(self):
        # Owner 2026-11-22: build --app-icon names the logo the player picked. An iOS or Duo profile takes it as ${appicon} (its
        # app_icons choices, pinned by hash); a bundle names "<edition>-<colourway>" in its manifest. No pick: the profile's
        # default icon and no manifest key. The decode stage hashes its inputs, so its output shows which icon file it was given.
        icons = {c: f'icon {c}'.encode() for c in db.APP_ICON_COLOURWAYS}
        def single_screen(fx):
            (fx.side / 'edition.txt').write_text('fold\n')
            (fx.side / 'icons').mkdir()
            for c, data in icons.items():
                (fx.side / 'icons' / f'{c}.png').write_bytes(data)
        fx, release = self.packaged(single_screen)
        apple = json.loads(fx.profile_path.read_text())
        apple['app_icons'] = {'default': 'pink-blue-dusk', 'choices': {c: {'path': f'${{repo}}/icons/{c}.png', 'sha256': hashlib.sha256(d).hexdigest()}
                                                                       for c, d in icons.items()}}
        decode = apple['stages'][[s['id'] for s in apple['stages']].index('decode')]
        decode['inputs']['icon'] = '${appicon}'
        decode['argv'].append('${appicon}')
        (fx.side / 'profile-apple.json').write_text(json.dumps(apple))
        self.release.update(profiles={'ios': 'profile-apple.json', 'duo': 'profile-apple.json'},
                            files=[dict(path=p.relative_to(fx.side).as_posix(), sha256=db.sha256_file(p)) for p in fx.side.rglob('*')
                                   if p.is_file() and p.name != 'release.json'])
        release.write_text(json.dumps(self.release))
        base = ['--release', str(release), '--cache', str(self.cache), 'build', '--work', str(fx.work), '--play-disc', str(fx.disc)]

        def build(*extra):
            with contextlib.redirect_stdout(io.StringIO()) as out:
                code = b.main(base + list(extra))
            return code, out.getvalue()

        def decoded_with(icon):
            return hashlib.sha256(b'decode' + fixture.XEX + icon).hexdigest() + ' decode'

        for target in ('ios', 'duo'):
            for colourway in db.APP_ICON_COLOURWAYS:
                with self.subTest(target=target, colourway=colourway):
                    code, text = build('--' + target, '--bundle-id', 'com.example.x', '--app-icon', colourway)
                    self.assertEqual(code, 0, text)
                    self.assertEqual((fx.work / 'decode/image.bin').read_text(), decoded_with(icons[colourway]))
                    self.assertEqual(db.verify_bundle(fx.bundle())['app_icon'], f'{target}-{colourway}')
        code, text = build('--ios', '--bundle-id', 'com.example.x')                           # no pick: the profile's default
        self.assertEqual(code, 0, text)
        self.assertEqual((fx.work / 'decode/image.bin').read_text(), decoded_with(icons['pink-blue-dusk']))
        self.assertNotIn('app_icon', db.verify_bundle(fx.bundle()))
        code, text = build('--app-icon', 'peach-rose')                                         # the package's own edition
        self.assertEqual(code, 0, text)
        self.assertEqual(db.verify_bundle(fx.bundle())['app_icon'], 'android-peach-rose')
        code, text = build('--app-icon', 'peach-rose', '--edition', 'thor')                    # the window names the twin
        self.assertEqual(code, 0, text)
        self.assertEqual(db.verify_bundle(fx.bundle())['app_icon'], 'thor-peach-rose')
        code, text = build('--ios', '--bundle-id', 'com.example.x', '--app-icon', 'peach-rose', '--edition', 'thor')
        self.assertEqual(code, 2, text)
        self.assertIn('does not match the build target --ios', text)
        with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):     # a colourway the apps do not carry
            build('--app-icon', 'sunrise')

    def test_52_a_player_keeps_one_generated_bundle_id_per_target(self):
        one, two = self.root / 'one' / 'work', self.root / 'two' / 'work'
        first = b.bundle_id_for('ios', one)
        self.assertRegex(first, r'^com\.fivepocket\.[0-9a-f]{8}\.ios$')
        self.assertEqual(b.bundle_id_for('ios', one), first)                           # the same on every later build
        self.assertEqual(b.bundle_id_for('duo', one), first[:-len('ios')] + 'duo')     # one player key for both apps
        self.assertTrue((one.parent / b.SETTINGS_NAME).is_file())
        self.assertFalse(one.exists())                                                  # next to the work folder, not in it
        self.assertNotEqual(b.bundle_id_for('ios', two), first)                         # another settings folder: another id
        self.assertEqual(b.bundle_id_for('ios', one, 'com.example.mine'), 'com.example.mine')
        self.assertEqual(b.bundle_id_for('ios', one), first)                            # an override is not stored
        with contextlib.redirect_stdout(io.StringIO()) as out:
            b.say_bundle_id(first, 'ios')
        self.assertIn(first, out.getvalue()); self.assertIn('ALWAYS SIGN WITH THIS IDENTIFIER', out.getvalue())

    def test_53_a_release_whose_default_is_an_ios_target_explains_the_ios_steps(self):
        rel = self.root / 'release.json'
        rel.write_text(json.dumps({'profile': 'p.json', 'profiles': {'ios': 'p.json', 'duo': 'd.json'},
                                   'ndk': dict(self.ndk), 'estimates': {'min_free_bytes': 16 << 30}}))
        text = b.how_to(rel)
        self.assertIn('iPhone and iPad (duo, ios)', text)
        self.assertIn('./builder build --duo or --ios', text)
        self.assertIn('ALWAYS SIGN WITH THAT IDENTIFIER', text)
        self.assertNotIn('code bundle for the phone app', text)

    def test_54_an_android_target_builds_its_bundle_without_a_bundle_id(self):
        fx, release = self.packaged()
        shutil.copyfile(fx.side / 'profile.json', fx.side / 'profile-thor.json')
        self.release.update(profiles={'thor': 'profile-thor.json'},
                            files=[dict(path=p.relative_to(fx.side).as_posix(), sha256=db.sha256_file(p)) for p in fx.side.rglob('*')
                                   if p.is_file() and p.name != 'release.json'])
        release.write_text(json.dumps(self.release))
        with contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(b.main(['--release', str(release), '--cache', str(self.cache), 'build', '--thor', '--work', str(fx.work),
                                     '--play-disc', str(fx.disc)]), 0, out.getvalue())
        self.assertTrue(fx.bundle().is_file())
        self.assertNotIn('Bundle identifier', out.getvalue())
        self.assertFalse((fx.work.parent / b.SETTINGS_NAME).exists())
        rel = self.root / 'r.json'
        rel.write_text(json.dumps({'profile': 'p.json', 'profiles': {'ios': 'p.json', 'thor': 't.json', 'fold': 'f.json'},
                                   'ndk': dict(self.ndk), 'estimates': {'min_free_bytes': 16 << 30}}))
        text = b.how_to(rel)
        self.assertIn('./builder build --thor', text); self.assertIn('./builder build --fold', text); self.assertIn('./builder build --ios', text)

    def test_54b_a_build_with_the_players_title_update_uses_the_title_update_profile(self):
        # owner via lead 2026-11-24: one builder makes the 1.0 bundle, or the title update's when the player gives one
        fx, release = self.packaged()
        shutil.copyfile(fx.side / 'profile.json', fx.side / 'profile-tu.json')
        self.release.update(title_update_profiles={'default': 'profile-tu.json'},
                            files=[dict(path=p.relative_to(fx.side).as_posix(), sha256=db.sha256_file(p)) for p in fx.side.rglob('*')
                                   if p.is_file() and p.name != 'release.json'])
        release.write_text(json.dumps(self.release))
        loaded = b.load_release(release)[0]
        self.assertEqual((b.profile_of(loaded, None), b.profile_of(loaded, None, True)), (self.release['profile'], 'profile-tu.json'))
        loaded_without = dict(loaded, title_update_profiles={})
        self.assertEqual(b.profile_of(loaded_without, None, True), self.release['profile'], 'no title-update profile: the own one (test_15b)')
        update = fx.side.parent / 'tu00000009_00000000'
        update.write_bytes(b'package')
        for given, want in ((['--title-update', str(update)], 'profile-tu.json'), ([], self.release['profile'])):
            seen = []
            with self.subTest(given=bool(given)), mock.patch.object(b.db, 'main', side_effect=lambda argv: seen.append(argv) or 0), \
                    contextlib.redirect_stdout(io.StringIO()) as out:
                self.assertEqual(b.main(['--release', str(release), '--cache', str(self.cache), 'build', '--work', str(fx.work),
                                         '--play-disc', str(fx.disc)] + given), 0, out.getvalue())
                command = seen[0]
                self.assertEqual(Path(command[command.index('--profile') + 1]).name, want)
                self.assertEqual('--title-update' in command, bool(given))
        self.release['title_update_profiles'] = {'default': 'not-listed.json'}
        release.write_text(json.dumps(self.release))
        with self.assertRaisesRegex(b.Refusal, 'title-update profile is not hash-bound'):
            b.load_release(release)

    def test_55_an_ios_release_without_android_packs_names_the_windows_builder(self):
        fx, release = self.packaged()
        self.release.update(profiles={'ios': 'profile.json'})
        release.write_text(json.dumps(self.release))
        for target in ('--thor', '--fold'):
            with contextlib.redirect_stdout(io.StringIO()) as out:
                self.assertEqual(b.main(['--release', str(release), '--cache', str(self.cache), 'build', target, '--work', str(fx.work),
                                         '--play-disc', str(fx.disc)]), 2)
            self.assertEqual(out.getvalue(), b.ANDROID_ELSEWHERE + '\n')
        self.assertFalse(fx.bundle().exists())
        self.assertFalse((fx.work.parent / b.SETTINGS_NAME).exists())
        rel = self.root / 'r.json'
        rel.write_text(json.dumps({'profile': 'p.json', 'profiles': {'ios': 'p.json', 'duo': 'd.json'},
                                   'ndk': dict(self.ndk), 'estimates': {'min_free_bytes': 16 << 30}}))
        self.assertIn(b.ANDROID_ELSEWHERE, b.how_to(rel))

    def test_56_a_builder_inside_a_git_repository_says_where_why_and_what_to_do(self):
        # Discord, 2026-11-06: a player who ran the builder inside a git repository got "output is in a checkout: choose its
        # local-analysis directory ... use an intact release". The refusal now names the folder and the repository, says
        # why, says what a player does (the Releases download, outside any repository, no clone needed) and keeps the
        # developer's way; the "intact release" tail is not added to it. One wording for every place that refuses.
        fx, release = self.packaged()
        repo = self.root / 'some-clone'; (repo / '.git').mkdir(parents=True)
        cases = (('work folder', ['--cache', str(self.cache), 'build', '--work', str(repo / 'b'), '--play-disc', str(fx.disc)], repo / 'b'),
                 ('work folder', ['--cache', str(self.cache), 'check', '--work', str(repo / 'w')], repo / 'w'),
                 ('cache folder', ['--cache', str(repo / 'cache'), 'check', '--work', str(fx.work)], repo / 'cache'))
        for what, argv, place in cases:
            with self.subTest(argv[2:4]):
                with contextlib.redirect_stdout(io.StringIO()) as out:
                    self.assertEqual(b.main(['--release', str(release)] + argv), 2)
                text = out.getvalue()
                self.assertTrue(text.startswith(f'REFUSED: the {what} {place.resolve()} is inside a git repository (found at {repo.resolve()}).'), text)
                for needed in ("The builder only writes your game files outside git repositories, so they can't be committed by accident.",
                               'unzip the builder download from the Releases page into a normal folder outside any git repository',
                               os.path.join('Desktop', 'FivePocket-Builder'), "you don't need to clone the repository to build",
                               f'(Developers: inside a checkout, put the work folder under its local-analysis{os.sep} folder: {repo.resolve() / "local-analysis"})',
                               'run check again from the new folder'):
                    self.assertIn(needed, text)
                for gone in ('intact release', 'choose its local-analysis directory', 'output is in a checkout'):
                    self.assertNotIn(gone, text)
                self.assertFalse(place.exists())
        # the engine and the stage tools (disc_builder.check_private_path) refuse with the same words
        with self.assertRaises(db.BuilderError) as caught:
            db.check_private_path(repo / 'out', 'bundle directory')
        self.assertEqual(str(caught.exception), checkout_refusal.checkout_refusal_text('bundle directory', (repo / 'out').resolve(), repo.resolve()))
        self.assertEqual(caught.exception.code, db.EXIT_USAGE)
        # and inside the repository's local-analysis/ (the developer's way) nothing is refused
        checkout_refusal.private_output(repo / 'local-analysis' / 'work', 'work folder')
        db.check_private_path(repo / 'local-analysis' / 'out', 'bundle directory')

    def test_57_the_shipped_recipe_record_still_pins_this_tree_s_tools(self):
        # 2026-11-06: 9d8438b edited host_support.py, a helper of two recipe passes (direct_calls, vmx_all); every fresh build
        # then refused at the recipe stage ("tool helpers changed"), and no test saw it. The recipe record the shipped profile
        # names must accept this tree's pass tools and their whole import closures, by the recipe tool's own check.
        profile = REPO / 'local-analysis/builder/portable/profile-portable-1f-3.json'
        if not profile.is_file():
            self.skipTest('no private profile in this checkout')
        stage = next(s for s in json.loads(profile.read_text(encoding='utf-8'))['stages'] if s['id'] == 'recipe')
        record = REPO / stage['inputs']['recipe'].replace('${repo}/', '')
        recipe = json.loads(record.read_text(encoding='utf-8'))
        problems = []
        with mock.patch.object(rp, 'TOOLS_ROOT', SRC):
            for p in recipe['passes'] + recipe.get('base_passes', []):
                rp.check_tool(p['tool'], problems)
        self.assertEqual(problems, [], record.name)
        # and the refusal's wording lives outside every pass's closure
        with mock.patch.object(rp, 'TOOLS_ROOT', SRC):
            for p in recipe['passes'] + recipe.get('base_passes', []):
                self.assertNotIn(SRC / 'tools/own_disc/checkout_refusal.py', rp.closure(SRC / p['tool']['path']))

    def guided_run(self, release, answers, **kw):
        """runs the guided mode with typed answers; returns (exit code, what it printed, the prompts it showed)"""
        prompts, left = [], list(answers)
        def ask(prompt):
            prompts.append(prompt)
            if not left: raise EOFError
            return left.pop(0)
        with contextlib.redirect_stdout(io.StringIO()) as out:
            code = b.guided(release, ask=ask, cache=kw.pop('cache', self.cache), **kw)
        return code, out.getvalue(), prompts

    def test_58_a_double_click_guides_a_player_from_the_discs_to_a_verified_bundle(self):
        # Discord report 2026-11-07: a double click showed only "press Enter"; the player did not know what to do
        fx, release = self.packaged()
        install = self.root / 'install disc'; install.mkdir()
        inside_repo = REPO / 'tools' / 'not-a-private-folder'                # an output folder in a checkout is refused, then asked again
        code, said, prompts = self.guided_run(release, [f'"{fx.disc}"', str(install), str(inside_repo), '', '9', '3'])
        self.assertEqual(code, 0, said)
        bundle = fx.side / 'local-analysis' / 'builder' / 'work' / 'out' / 'code-fixture-generic.bundle'
        self.assertTrue(bundle.is_file(), said)
        self.assertEqual(sum('Pink/Blue Dusk' in p for p in prompts), 2)        # an answer that is no choice is asked again
        self.assertEqual(db.verify_bundle(bundle)['app_icon'], 'android-peach-rose')   # the pick, for this package's app
        self.assertIn('REFUSED', said.split('Step 1')[0])                   # the repo folder, before any step ran
        self.assertFalse(inside_repo.exists())
        steps = [line for line in said.splitlines() if line.startswith('=== Step')]
        self.assertEqual([s.split(': ', 1)[1].split(' (')[0].rstrip(' =') for s in steps],
                         ['Checking this computer', 'Building', 'Verifying the bundle'])   # compilers already fetched: no fetch step
        self.assertIn(f'Your package is at {bundle}', said)
        self.assertIn(f'{bundle.name}: verified.', said.split('Verifying the bundle')[1])   # the verify step itself ran
        self.assertEqual(sum('Disc 2 (Play)' in p for p in prompts), 1)
        self.assertNotIn('close this window', ''.join(prompts))            # after a double click the launcher waits, not this

    def test_59_a_disc_dropped_onto_the_program_fills_its_own_slot(self):
        fx, release = self.packaged()
        install = self.root / 'install disc'; install.mkdir()
        self.assertEqual(b.dropped_disc([str(install)]), install)
        iso = self.root / 'Disc2-Play.ISO'; iso.write_bytes(b'')
        self.assertEqual(b.dropped_disc([str(iso)]), iso)
        (self.root / 'check').mkdir()                                           # a folder named like a command, beside a disc
        cwd = os.getcwd(); os.chdir(self.root); self.addCleanup(os.chdir, cwd)
        for words in (['check'], ['Disc2-Play.ISO'], [str(self.root / 'missing.iso')], [str(iso), 'build'], [str(fx.profile_path)]):
            self.assertIsNone(b.dropped_disc(words), words)                   # commands and anything else stay the command line
        os.chdir(cwd)
        # the install disc dropped: only the play disc is asked for; the window waits at the end (the launcher does not)
        code, said, prompts = self.guided_run(release, [str(fx.disc), '', '', ''], dropped=install, wait_at_end=True)
        self.assertEqual(code, 0, said)
        self.assertIn(f'Disc 1 (Install): {install}', said)
        self.assertFalse(any('Disc 1 (Install)' in p for p in prompts), prompts)
        self.assertIn('Press Enter to close this window', prompts[-1])
        # the play disc typed where the install disc is asked for: taken as the play disc, then the install disc asked
        code, said, prompts = self.guided_run(release, [str(install), str(fx.disc), '', ''])
        self.assertEqual(code, 0, said)
        self.assertIn('That is Disc 1 (Install): taken as Disc 1 (Install)', said)
        games = fx.profile['games']
        self.assertEqual(b.disc_role(fx.disc, games), 'play'); self.assertEqual(b.disc_role(install, games), 'install')
        # an install disc with an executable of its own (the real Disc 1 has one, 2026-11-07 PC run) is still the install disc
        other = self.root / 'install with exe'; other.mkdir(); (other / 'default.xex').write_bytes(b'not the game executable')
        self.assertEqual(b.disc_role(other, games), 'install')
        code, said, prompts = self.guided_run(release, [str(fx.disc), str(other), '', ''])
        self.assertEqual(code, 0, said)
        self.assertNotIn('looks like', said)

    def test_60_a_player_who_declines_the_licences_fetches_and_builds_nothing(self):
        fx, release = self.packaged()
        install = self.root / 'install disc'; install.mkdir()
        empty = self.root / 'fresh cache'
        code, said, prompts = self.guided_run(release, [str(fx.disc), str(install), '', '', 'n'], cache=empty)
        self.assertEqual(code, 1, said)
        self.assertIn(self.ndk['license_url'], said)
        self.assertIn('(y/n)', prompts[-1])
        self.assertFalse(empty.exists())                                     # nothing fetched
        self.assertFalse((fx.side / 'local-analysis').exists())              # nothing built
        self.assertNotIn('=== Step', said)
        # yes: the fetch step runs first, with the switches a player would type (captured here: no download in a test)
        ran = []
        with mock.patch.object(b, 'main', side_effect=lambda argv: ran.append(argv) or 1):
            code, said, _ = self.guided_run(release, [str(fx.disc), str(install), '', '', 'y'], cache=empty)
        self.assertEqual(code, 1)
        self.assertIn('=== Step 1 of 4: Fetching the compilers ===', said)
        self.assertEqual(ran, [['--release', str(release), '--cache', str(empty), 'fetch', '--accept-ndk-license']])
        self.assertIn('Stopped at step 1', said)

    def test_62_unpacked_too_deep_on_windows_it_says_where_to_move_the_folder(self):
        # a player who unpacks into Downloads gets a build folder over Windows' 80 characters; the stages build only inside the
        # package (2026-11-07 PC run: a work folder outside it failed at codegen), so the folder itself must move
        fx, release = self.packaged()
        default = b.default_work(fx.side)
        with mock.patch.object(b, 'windows_path_too_long', lambda p: Path(p) == default):
            code, said, prompts = self.guided_run(release, [str(fx.disc)])
        self.assertEqual(code, 2, said)
        self.assertIn('C:\\FivePocket\\builder\\builder.exe', said)
        self.assertEqual(prompts, [])                                         # asked nothing: told before any question
        self.assertFalse(default.exists())                                    # nothing built
        self.assertNotIn('--work', said)

    def test_61_paths_pasted_or_dropped_onto_a_terminal_are_read_as_paths(self):
        self.assertEqual(b.clean_path('  "C:\\Games\\My Discs\\Disc2-Play.iso"  '), 'C:\\Games\\My Discs\\Disc2-Play.iso')
        self.assertEqual(b.clean_path("'/Users/p/My Discs/d.iso'"), '/Users/p/My Discs/d.iso')
        if sys.platform != 'win32':                                          # macOS Terminal escapes a dropped path's spaces
            self.assertEqual(b.clean_path('/Users/p/My\\ Discs/Disc\\ 2\\ \\(Play\\).iso '), '/Users/p/My Discs/Disc 2 (Play).iso')

    def test_51_sign_unpacks_signs_and_writes_a_new_ipa(self):
        app = self.root / 'Payload' / 'FivePocket.app'; app.mkdir(parents=True)
        (app / 'GamePort').write_bytes(b'\xcf\xfa\xed\xfe synthetic'); (app / 'GamePort').chmod(0o755)
        (app / 'Info.plist').write_bytes(b'plist')
        sys.path.insert(0, str(REPO / 'tools/own_disc'))
        import ipa_writer, ios_sign
        unsigned = self.root / 'u.ipa'
        with contextlib.redirect_stdout(io.StringIO()):
            ipa_writer.write_ipa(app, unsigned)
        seen = []

        def fake_sign(app_dir, identity, profile, entitlements):
            seen.append((app_dir.name, identity, os.access(app_dir / 'GamePort', os.X_OK)))
            (app_dir / '_CodeSignature').mkdir(); (app_dir / '_CodeSignature' / 'CodeResources').write_bytes(b'sig')
            return ['.']
        with mock.patch.object(ios_sign, 'sign', fake_sign), contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(b.main(['sign', '--ipa', str(unsigned), '--identity', '-', '--out', str(self.root / 's.ipa')]), 0, out.getvalue())
        self.assertEqual(seen, [('FivePocket.app', '-', True)])
        names = zipfile.ZipFile(self.root / 's.ipa').namelist()
        self.assertIn('Payload/FivePocket.app/_CodeSignature/CodeResources', names)
        self.assertIn('Payload/FivePocket.app/GamePort', zipfile.ZipFile(unsigned).namelist())
        with contextlib.redirect_stdout(io.StringIO()) as out:              # never over an existing file
            self.assertEqual(b.main(['sign', '--ipa', str(unsigned), '--identity', '-', '--out', str(self.root / 's.ipa')]), 2)
        bad = self.root / 'bad.ipa'
        with zipfile.ZipFile(bad, 'w') as z:
            z.writestr('../escape', b'x')
        with contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(b.main(['sign', '--ipa', str(bad), '--identity', '-', '--out', str(self.root / 'b.ipa')]), 2)
        self.assertIn('invalid package path', out.getvalue())


if __name__ == '__main__': unittest.main()
