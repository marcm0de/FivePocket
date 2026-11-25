"""Local loading-test supervision and exact build receipts; no game emulation.

Unknown/failure logs are retained. A receipt proves only unchanged build inputs,
not correct behavior. No helper sends controller input or stops an app.
"""
from collections import Counter
import hashlib
import json
from pathlib import Path
import re
import shlex
import subprocess
import threading


def digest(path):
    path = Path(path)
    before = path.stat()
    if not path.is_file() or getattr(before, 'st_flags', 0) & 0x40000000:
        raise ValueError('build input unavailable locally; no cloud hydration')
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(block)
    after = path.stat()
    if (before.st_size, before.st_mtime_ns, before.st_ino) != (after.st_size, after.st_mtime_ns, after.st_ino):
        raise ValueError('build input changed while hashing')
    return h.hexdigest()


def depfile_inputs(path, cwd):
    return parse_depfile(Path(path).read_text(), cwd)


def parse_depfile(text, cwd):
    text = text.replace('\\\n', '')
    if ': ' not in text:
        raise ValueError('compiler dependency file missing target')
    paths = shlex.split(text.split(': ', 1)[1])
    if not paths:
        raise ValueError('empty compiler dependency file')
    return sorted({str((Path(cwd) / p).resolve()) for p in paths})


def build_fingerprint(command, dependencies, optional_headers=()):
    # Hash the compiler too, and include the set of optional #has_include
    # headers so adding a generated shader pack invalidates an old receipt.
    paths = sorted({str(Path(p).resolve()) for p in (*dependencies, *optional_headers, command[0], __file__)})
    hashes = {p: digest(p) for p in paths}
    return hashlib.sha256(json.dumps([command, hashes], sort_keys=True).encode()).hexdigest()


def reusable_object(receipt, fingerprint, object_path):
    try:
        r = json.loads(Path(receipt).read_text())
        return (r.get('schema') == 1 and r.get('input_fingerprint') == fingerprint
                and r.get('object_sha256') == digest(object_path))
    except (OSError, ValueError, TypeError):
        return False


def write_receipt(path, fingerprint, object_path):
    Path(path).write_text(json.dumps(dict(schema=1, input_fingerprint=fingerprint,
        object_sha256=digest(object_path)), sort_keys=True) + '\n')


ROUTINE = frozenset(('RENDER_API_TRANSLATED_DRAW', 'RENDER_INLINE_INPUT',
    'RENDER_API_LIVE_RESOLVE', 'RENDER_API_PRESENT_DRAIN', 'RENDER_API_LIVE_PRESENT',
    'RENDER_API_PRESENT_CALLBACK', 'RENDER_API_LIVE_CLEAR',
    'LIVE_DISPLAY_GPU_AUDIT_PASS_PRESENT_QUEUED'))
FAILURE = re.compile(r'UNAVAILABLE|UNSUPPORTED|REJECT_|EXCEPTION|FAIL|CRASH|\b(?:completed|state|captured|published)=0', re.I)
# PrivateTraceActivity.show emits launch refusals at the START of its message.
# Renderer events such as STENCIL_REJECT_RECTANGLE describe successful GPU work,
# not a refused launch. Keep those lines, but never abort observation for them.
# LiveLogCapture requests threadtime; bare messages are supported for fixtures.
LAUNCH_REJECTION = re.compile(
    r'^\s*(?:\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}\.\d+\s+'
    r'\d+\s+\d+\s+[VDIWEF]\s+RecompPrivateTrace\s*:\s*)?'
    r'REJECT_[A-Za-z0-9_-]+(?=\s|$)')


class CompactRendererLog:
    """Keep first/periodic/last routine events; every failure, input and unknown.

    This is a log-volume policy only: omitted routine lines are counted, not
    treated as proof a rendering operation was correct.
    """
    def __init__(self, stream, every=1000):
        if every < 1:
            raise ValueError('positive sampling interval required')
        self.stream, self.every = stream, every
        self.counts, self.last = Counter(), {}
        self.lines = self.kept = self.input_bytes = self.output_bytes = 0
        self.rejection = None
        self.terminal = threading.Event()

    def emit(self, line):
        self.stream.write(line)
        self.stream.flush()
        self.kept += 1
        self.output_bytes += len(line.encode())

    def consume(self, line):
        self.lines += 1
        self.input_bytes += len(line.encode())
        if 'TRACE_RETURNED_PROTOCOL_SAVED' in line:
            self.terminal.set()
        if LAUNCH_REJECTION.match(line):
            self.rejection = line.strip()
        match = re.search(r'\b(RENDER_[A-Z0-9_]+|LIVE_DISPLAY_GPU_AUDIT_PASS_PRESENT_QUEUED)\b', line)
        event = match[1] if match else None
        if event not in ROUTINE or FAILURE.search(line):
            self.emit(line)
            return
        self.counts[event] += 1
        self.last[event] = line
        if self.counts[event] <= 2 or self.counts[event] % self.every == 0:
            self.emit(line)

    def finish(self):
        for event, line in sorted(self.last.items()):
            self.emit('WORKFLOW_LAST_ROUTINE ' + line)
        result = dict(input_lines=self.lines, kept_lines=self.kept, input_bytes=self.input_bytes,
                      output_bytes=self.output_bytes, routine_counts=dict(self.counts))
        self.emit('WORKFLOW_LOG_SUMMARY ' + json.dumps(result, sort_keys=True) + '\n')
        return result


THREADTIME = re.compile(r'^(\d\d-\d\d \d\d:\d\d:\d\d\.\d{3}) ')


class LiveLogCapture:
    # 2026-09-28: a logcat client that exits on its own (logd drops a reader that falls behind; observed on a loaded
    # Mac while a 96 MB-per-11-min log was streaming) is started again from the last timestamp it delivered (-T), at
    # most MAX_RESTARTS times; the replayed lines up to that timestamp are skipped and a WORKFLOW_LOGGER_RESTARTED line
    # records the gap. Only this host's diagnostic client is restarted, never the game.
    MAX_RESTARTS = 20

    def __init__(self, adb, pid, output, verbose=False, spawn=None):
        if not str(pid).isdigit():
            raise ValueError('logger requires the verified launched PID')
        self.output = Path(output)
        self.stream = self.output.open('x')
        self.log = CompactRendererLog(self.stream, every=1 if verbose else 1000)
        self.restarts = 0
        self._closing = threading.Event()
        self._lock = threading.Lock()  # the restart check + spawn vs close(): never leave a client running
        self._spawn = spawn or (lambda since: subprocess.Popen([*adb, 'logcat', '--pid', str(pid), '-v', 'threadtime',
            *(['-T', since] if since else []),
            'RecompRenderer:I', 'RecompPrivateTrace:I', 'RecompNativeInput:I', 'RecompSaves:I', '*:S'],  # 2026-09-27: + saves
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, errors='replace'))
        self.process = self._spawn(None)
        self.thread = threading.Thread(target=self._read, daemon=True)
        self.thread.start()

    def _read(self):
        last, at_last = None, set()      # newest timestamp delivered and the lines delivered at it
        resume, replayed = None, set()   # after a restart: skip lines older than `resume` and the ones already seen at it
        while True:
            for line in self.process.stdout:
                match = THREADTIME.match(line)
                ts = match[1] if match else None
                if resume is not None and ts is not None:
                    if ts < resume or (ts == resume and line in replayed):
                        continue
                    resume = None
                if ts is not None:
                    if ts != last:
                        last, at_last = ts, set()
                    at_last.add(line)
                self.log.consume(line)
            self.process.wait()
            with self._lock:
                if self._closing.is_set() or self.restarts >= self.MAX_RESTARTS:
                    return
                self.restarts += 1
                self.log.emit(f'WORKFLOW_LOGGER_RESTARTED n={self.restarts} after={last} rc={self.process.returncode}\n')
                resume, replayed = last, set(at_last)
                self.process = self._spawn(last)

    def close(self):
        # Stops ONLY this host's diagnostic logcat client, never the game.
        with self._lock:
            self._closing.set()
            self.process.terminate()
        self.process.wait(timeout=5)
        self.thread.join(timeout=5)
        if self.thread.is_alive():
            raise RuntimeError('diagnostic logger did not terminate')
        result = self.log.finish()
        self.stream.close()
        return result


def validate_request(record, package, serial):
    if (record.get('serial') != serial or not re.fullmatch('[0-9a-f]{32}', record.get('request_id', ''))
            or not re.fullmatch('[A-Za-z0-9_.]+', package)):
        raise ValueError('request identity mismatch')
    expected = '/data/user/0/' + package + '/files/private-trace/run-' + record['request_id']
    if record.get('run_directory') != expected or not str(record.get('pid', '')).isdigit():
        raise ValueError('request path/PID mismatch')
    return expected


def status_script(run, pid):
    if not re.fullmatch('/data/user/0/[A-Za-z0-9_.]+/files/private-trace/run-[0-9a-f]{32}', run) or not str(pid).isdigit():
        raise ValueError('unvalidated remote status target')
    # A single read-only ADB request. Protocol presence wins over retained UI
    # PID. Missing/failed observation is not itself permission to restart.
    return (f'if test -s {run}/trace-protocol.txt; then echo TERMINAL; '
            f'elif kill -0 {pid} 2>/dev/null; then echo LIVE; else echo MISSING; fi')


def require_previous_terminal(adb, package, serial, request_path):
    record = json.loads(Path(request_path).read_text())
    run = validate_request(record, package, serial)
    active = subprocess.run([*adb, 'shell', 'pidof', package + ':private_trace'],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False, timeout=10)
    if active.returncode not in (0, 1):
        raise RuntimeError('could not establish prior process state')
    if active.stdout.strip() and active.stdout.decode().strip() != str(record['pid']):
        raise RuntimeError('another private trace PID is active; do not replace it')
    # adb shell joins arguments into a remote command; preserve the complete
    # inner script as one sh -c argument, including its control operators.
    check = subprocess.run([*adb, 'shell', 'run-as', package, 'sh', '-c', shlex.quote(status_script(run, record['pid']))],
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True, timeout=10).stdout.decode().strip()
    if check not in ('TERMINAL', 'MISSING'):
        raise RuntimeError('previous request is live or status is unknown; no stop/restart permitted')
    return dict(request_id=record['request_id'], pid=record['pid'], authoritative_state=check)
