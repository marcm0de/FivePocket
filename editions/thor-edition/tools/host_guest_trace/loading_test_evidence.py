"""Fail-closed evidence helpers for reviewed local test manifests, not gameplay."""
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import signal
import subprocess
import sys
import tempfile

from loading_workflow import digest, require_previous_terminal, validate_request

TIERS = {'focused', 'capture', 'regression'}
# Retain prior rendering regressions alongside new connected maintenance cases.
# This bounds orchestration size only; every step still has its own <=60s limit,
# explicit evidence, current build identity and fresh device checks.
# Retain the previous64 checks plus new packed-texture/raster connections.
# This limits orchestration only: per-step timeouts, receipts, device freshness
# and coverage requirements are unchanged, including the final step.
# Orchestration capacity only. Each additional step still requires the same
# identity, timeout, receipt, terminal-state and positive-result checks.
# Retain the previous95 checks plus the exposure-copy cases and the relocated
# audio regressions (changed FFmpeg include path) as separate steps.
# Then one host plan step per added PostFX band (ABI40, ABI41, resolves...).
MAX_TEST_STEPS = 128


def local_path(root, value, private=False):
    path = (root / value).absolute()
    if not path.is_relative_to(root / 'local-analysis' if private else root) or path != path.resolve():
        raise ValueError('explicit workspace path required (no traversal/symlink)')
    return path


def executable(value, root):
    path = (root / value) if '/' in value else shutil.which(value)
    if not path or not Path(path).is_file() or not os.access(path, os.X_OK):
        raise ValueError('executable missing: ' + value)
    return Path(path).resolve()


def validate_spec(spec, root, *, installable=False):
    """Validate the ENTIRE plan before executing its first step."""
    if spec.get('schema') != 2 or not isinstance(spec.get('scope'), str) or not spec['scope'].strip():
        raise ValueError('schema 2 and nonempty evidence scope required')
    steps = spec.get('steps')
    if not isinstance(steps, list) or not 3 <= len(steps) <= MAX_TEST_STEPS or any(not isinstance(s, dict) for s in steps):
        raise ValueError('bounded test list required')
    if not TIERS.issubset({s.get('tier') for s in steps}):
        raise ValueError('focused, capture and regression tiers required')
    extras = {local_path(root, p) for p in spec.get('additional_inputs', [])}
    names, remote_owners, device_seen = set(), {}, False
    device = spec.get('device')
    if device:
        adb = executable(device['adb'], root)
        if Path(device['adb']) != adb or adb.name != 'adb' or not re.fullmatch(r'[A-Za-z0-9_.:-]+', device['serial']):
            raise ValueError('canonical adb path and explicit serial required')
        request = local_path(root, device['previous_request'], private=True)
        validate_request(json.loads(request.read_text()), device['package'], device['serial'])
        extras.add(request)
    for step in steps:
        name, command = step.get('name'), step.get('command')
        if not isinstance(name, str) or not name.strip() or name in names:
            raise ValueError('unique nonempty step names required')
        names.add(name)
        if not isinstance(command, list) or not command or any(not isinstance(s, str) or not s for s in command):
            raise ValueError('explicit argument-list command required, never shell text')
        tool = executable(command[0], root)
        kind = step.get('kind', 'host')
        if step.get('tier') not in TIERS or kind not in ('host', 'build', 'device'):
            raise ValueError('unknown step tier/kind')
        if type(step.get('reusable', False)) is not bool:
            raise ValueError('reusable must be boolean')
        if step.get('reusable') and (kind != 'host' or step['tier'] == 'capture'):
            raise ValueError('only reviewed read-only host focused/regression checks can resume')
        timeout = step.get('timeout_seconds', 60)
        if type(timeout) not in (int, float) or not 1 <= timeout <= 60:
            raise ValueError('step timeout must be 1..60 seconds')
        expect = step.get('expect', [])
        if not isinstance(expect, list) or any(not isinstance(s, str) or not s for s in expect):
            raise ValueError('expect must contain nonempty output regexes')
        for pattern in expect:
            if re.compile(pattern).search(''):
                raise ValueError('expected evidence must not match empty output')
        if kind == 'build':
            if tool.name != 'cmake' or '--build' not in command or '--target' not in command:
                raise ValueError('build checks must name exact CMake targets')
            index = command.index('--target') + 1
            targets = []
            for target in command[index:]:
                if target.startswith('-'):
                    break
                targets.append(target)
            if not targets or any(t in ('all', 'clean') for t in targets):
                raise ValueError('specific test build targets required')
        elif not expect:
            raise ValueError('tests require explicit positive output evidence')
        if tool.name == 'ctest' and '--no-tests=error' not in command:
            raise ValueError('CTest must reject an empty test selection')
        if tool.name == 'adb' and kind != 'device':
            raise ValueError('ADB commands must declare device scope')
        if kind == 'device':
            device_seen = True
            if (not device or timeout <= 5 or len(command) < 5 or
                    command[:4] != [device['adb'], '-s', device['serial'], 'shell']):
                raise ValueError('device step must target declared serial with adb shell')
            artifacts, remotes = step.get('device_inputs', []), set()
            if not artifacts:
                raise ValueError('exact local/remote input mappings required')
            for item in artifacts:
                path, remote = local_path(root, item['local'], private=True), item['remote']
                if not re.fullmatch(r'/data/local/tmp/[A-Za-z0-9_./-]+', remote) or '..' in remote.split('/'):
                    raise ValueError('safe explicit private probe path required')
                if remote in remotes or (remote in remote_owners and remote_owners[remote] != path):
                    raise ValueError('ambiguous remote input mapping')
                remotes.add(remote)
                remote_owners[remote] = path
                extras.add(path)
            if command[4] not in remotes:
                raise ValueError('device executable must be hash-bound locally')
        elif device_seen:
            raise ValueError('host checks must precede device checks; order dependencies explicitly')
    for value in spec.get('tools', []):
        executable(value, root)
    from loading_test_contract import validate_coverage, validate_probe_builds
    coverage_paths, _ = validate_coverage(spec, root, installable=installable)
    extras.update(coverage_paths)
    if installable and not device_seen:
        raise ValueError('installation batch requires fresh device checks')
    extras.update(validate_probe_builds(spec, root, required=installable))
    if any(not p.is_file() for p in extras):
        raise ValueError('missing declared input')
    return sorted(extras)


def test_environment():
    # A shell may increment SHLVL depending on whether Python is its last
    # command. Make incidental shell metadata identical for hashing AND tests.
    return dict(os.environ, _='loading-test-batch', SHLVL='0')


def runtime_snapshot(spec, root):
    commands = {s['command'][0] for s in spec['steps']} | set(spec.get('tools', [])) | {sys.executable}
    tools = {c: dict(path=str(executable(c, root)), sha256=digest(executable(c, root))) for c in sorted(commands)}
    # No credentials/env values in the report; all effective configuration is
    # still bound, including normalized metadata actually exported to tests.
    environment = test_environment()
    return dict(platform=platform.platform(), python=sys.version, tools=tools,
                environment_sha256=hashlib.sha256(json.dumps(environment, sort_keys=True).encode()).hexdigest())


def validate_output(path, step):
    if path.stat().st_size > 16 * 1024 * 1024:
        raise ValueError('test output exceeds 16 MiB evidence limit')
    output = path.read_text(errors='replace')
    if re.search(r'\bRan 0 tests?\b|No tests were found|Total Tests:\s*0\b', output):
        raise ValueError('empty test selection is not passing evidence')
    for pattern in step.get('expect', []):
        if not re.search(pattern, output, re.MULTILINE):
            raise ValueError('missing expected test evidence: ' + pattern)


def atomic_report(path, report):
    with tempfile.NamedTemporaryFile(mode='w', dir=path.parent, prefix=path.name + '.', delete=False) as stream:
        temporary = Path(stream.name)
        json.dump(report, stream, indent=2)
        stream.write('\n')
    temporary.replace(path)


@contextmanager
def batch_lock(root):
    # Cooperative batch-only lock, not a replacement for device state checks.
    with (root / 'local-analysis/.loading-test-batch.lock').open('a') as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise ValueError('another validation batch is active') from error
        try:
            yield
        finally:
            fcntl.flock(stream, fcntl.LOCK_UN)


def run_command(command, output, root, timeout):
    """Own a fresh process group, including descendants, for bounded cleanup."""
    with output.open('xb') as stream:
        process = subprocess.Popen(command, cwd=root, stdout=stream, stderr=subprocess.STDOUT,
                                   start_new_session=True, env=test_environment())
        try:
            return process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            return 124
        finally:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait(timeout=5)


def device_check(spec, step, root):
    device = spec['device']
    adb = [device['adb'], '-s', device['serial']]
    state = require_previous_terminal(adb, device['package'], device['serial'], root / device['previous_request'])
    properties = subprocess.run([*adb, 'shell',
        'getprop ro.build.fingerprint; getprop ro.product.cpu.abi; cat /proc/sys/kernel/random/boot_id'],
        capture_output=True, check=True, timeout=10).stdout.decode().splitlines()
    if len(properties) != 3 or not all(properties):
        raise ValueError('device build/ABI/boot identity unavailable')
    items = step['device_inputs']
    # Full captured scenes exceed Android's shell command-length limit. Keep
    # every declared file, but bound each command (never truncate coverage).
    observed = []
    for start in range(0, len(items), 64):
        observed.extend(subprocess.run([*adb, 'shell', 'sha256sum',
            *[i['remote'] for i in items[start:start + 64]]],
            capture_output=True, check=True, timeout=15).stdout.decode().splitlines())
    expected = {i['remote']: digest(root / i['local']) for i in items}
    parsed = {}
    for line in observed:
        sha, remote = line.split(maxsplit=1)
        if remote in parsed:
            raise ValueError('duplicate remote hash result')
        parsed[remote] = sha
    if parsed != expected:
        raise ValueError('remote probe/data differs from declared tested inputs')
    return dict(previous_request=state, fingerprint=properties[0], abi=properties[1],
                boot_id=properties[2], inputs_sha256=parsed)
