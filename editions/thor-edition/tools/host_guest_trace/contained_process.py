#!/usr/bin/env python3
"""Run one worker in a bounded POSIX process group."""

from __future__ import annotations

import dataclasses
import enum
import errno
import os
from pathlib import Path
import selectors
import signal
import subprocess
import sys
import time
from typing import Sequence


PROTOCOL_BYTE_LIMIT = 64 * 1024
OPEN_FILE_LIMIT = 32
LAUNCHER_FAILURE_EXIT = 125
ALLOWLISTED_ENVIRONMENT = {
    "__CF_USER_TEXT_ENCODING": "0x0:0x0",
    "LANG": "C",
    "LC_ALL": "C",
    "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
    "PYTHONDONTWRITEBYTECODE": "1",
}


class ContainedOutcomeKind(enum.Enum):
    EXITED = "exited"
    UNIX_SIGNAL = "unix-signal"
    CPU_LIMIT = "cpu-limit"
    WALL_TIMEOUT = "wall-timeout"
    PROTOCOL_LIMIT = "protocol-limit"
    LAUNCH_FAILURE = "launch-failure"


@dataclasses.dataclass(frozen=True)
class ContainedProcessLimits:
    wall_seconds: float
    cpu_seconds: int
    termination_grace_seconds: float = 0.25
    protocol_bytes: int = PROTOCOL_BYTE_LIMIT
    open_files: int = OPEN_FILE_LIMIT


@dataclasses.dataclass(frozen=True)
class ContainedProcessOutcome:
    kind: ContainedOutcomeKind
    returncode: int | None
    stdout: bytes
    elapsed_milliseconds: int
    signal_number: int | None = None
    signal_name: str | None = None
    term_sent: bool = False
    kill_sent: bool = False


def _signal_group(process_group: int, signal_number: int) -> bool:
    try:
        os.killpg(process_group, signal_number)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return False


def _group_exists(process_group: int) -> bool:
    try:
        os.killpg(process_group, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def _terminate_group(
    process: subprocess.Popen[bytes],
    grace_seconds: float,
) -> tuple[bool, bool]:
    term_sent = _signal_group(process.pid, signal.SIGTERM)
    deadline = time.monotonic() + grace_seconds
    while _group_exists(process.pid) and time.monotonic() < deadline:
        time.sleep(0.01)
    kill_sent = _signal_group(process.pid, signal.SIGKILL)
    try:
        process.wait(timeout=max(0.1, grace_seconds))
    except subprocess.TimeoutExpired:
        kill_sent = _signal_group(process.pid, signal.SIGKILL) or kill_sent
        process.wait()
    return term_sent, kill_sent


def _read_available(
    descriptor: int,
    captured: bytearray,
    byte_limit: int,
) -> tuple[bool, bool]:
    reached_eof = False
    exceeded = False
    while True:
        remaining_capacity = byte_limit - len(captured)
        try:
            chunk = os.read(descriptor, min(8192, remaining_capacity + 1))
        except BlockingIOError:
            break
        except OSError as exc:
            if exc.errno == errno.EINTR:
                continue
            reached_eof = True
            break
        if not chunk:
            reached_eof = True
            break
        if len(chunk) > remaining_capacity:
            captured.extend(chunk[:remaining_capacity])
            exceeded = True
            break
        captured.extend(chunk)
    return reached_eof, exceeded


def _strict_environment(cwd: Path) -> dict[str, str]:
    return {**ALLOWLISTED_ENVIRONMENT, "TMPDIR": str(cwd)}


def run_contained(
    command: Sequence[str],
    cwd: Path,
    limits: ContainedProcessLimits,
) -> ContainedProcessOutcome:
    started = time.monotonic()
    resolved_cwd = cwd.expanduser().resolve()
    executable = Path(command[0]).expanduser() if command else Path()
    resolved_executable = executable.resolve()
    launcher = Path(__file__).with_name("contained_launcher.py").resolve()
    if (
        not command
        or not executable.is_absolute()
        or not resolved_executable.is_file()
        or not os.access(resolved_executable, os.X_OK)
        or not resolved_cwd.is_dir()
        or cwd.expanduser().absolute().is_symlink()
        or limits.wall_seconds <= 0
        or limits.cpu_seconds <= 0
        or limits.termination_grace_seconds < 0
        or limits.protocol_bytes != PROTOCOL_BYTE_LIMIT
        or limits.open_files != OPEN_FILE_LIMIT
        or not launcher.is_file()
    ):
        return ContainedProcessOutcome(
            ContainedOutcomeKind.LAUNCH_FAILURE,
            None,
            b"",
            int((time.monotonic() - started) * 1000),
        )

    launch_command = [
        sys.executable,
        str(launcher),
        "--cpu-seconds",
        str(limits.cpu_seconds),
        "--open-files",
        str(limits.open_files),
        "--",
        str(resolved_executable),
        *[str(value) for value in command[1:]],
    ]
    try:
        process = subprocess.Popen(
            launch_command,
            cwd=resolved_cwd,
            env=_strict_environment(resolved_cwd),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            close_fds=True,
            start_new_session=True,
        )
    except (OSError, ValueError):
        return ContainedProcessOutcome(
            ContainedOutcomeKind.LAUNCH_FAILURE,
            None,
            b"",
            int((time.monotonic() - started) * 1000),
        )

    assert process.stdout is not None
    descriptor = process.stdout.fileno()
    os.set_blocking(descriptor, False)
    selector = selectors.DefaultSelector()
    selector.register(descriptor, selectors.EVENT_READ)
    captured = bytearray()
    deadline = started + limits.wall_seconds
    forced_kind: ContainedOutcomeKind | None = None
    term_sent = False
    kill_sent = False
    reached_eof = False
    try:
        while True:
            reached_eof_now, exceeded = _read_available(
                descriptor, captured, limits.protocol_bytes
            )
            reached_eof = reached_eof or reached_eof_now
            if reached_eof_now:
                try:
                    selector.unregister(descriptor)
                except KeyError:
                    pass
            if exceeded:
                forced_kind = ContainedOutcomeKind.PROTOCOL_LIMIT
                term_sent, kill_sent = _terminate_group(
                    process, limits.termination_grace_seconds
                )
                break
            if process.poll() is not None and reached_eof:
                break
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                forced_kind = ContainedOutcomeKind.WALL_TIMEOUT
                term_sent, kill_sent = _terminate_group(
                    process, limits.termination_grace_seconds
                )
                break
            if reached_eof:
                time.sleep(min(remaining, 0.05))
            else:
                selector.select(min(remaining, 0.05))
    finally:
        selector.close()

    if process.poll() is None:
        process.wait()
    returncode = process.returncode
    # A worker can exit while leaving descendants in its group. Clean those up
    # without changing the direct worker's already-observed outcome.
    if forced_kind is None and _group_exists(process.pid):
        descendant_term, descendant_kill = _terminate_group(
            process, limits.termination_grace_seconds
        )
        term_sent = term_sent or descendant_term
        kill_sent = kill_sent or descendant_kill
    process.stdout.close()

    elapsed = int((time.monotonic() - started) * 1000)
    if forced_kind is not None:
        return ContainedProcessOutcome(
            forced_kind,
            returncode,
            bytes(captured),
            elapsed,
            term_sent=term_sent,
            kill_sent=kill_sent,
        )
    if returncode == LAUNCHER_FAILURE_EXIT:
        return ContainedProcessOutcome(
            ContainedOutcomeKind.LAUNCH_FAILURE, returncode, b"", elapsed
        )
    if returncode is not None and returncode < 0:
        signal_number = -returncode
        kind = (
            ContainedOutcomeKind.CPU_LIMIT
            if signal_number == signal.SIGXCPU
            else ContainedOutcomeKind.UNIX_SIGNAL
        )
        try:
            signal_name = signal.Signals(signal_number).name
        except ValueError:
            signal_name = "UNKNOWN"
        return ContainedProcessOutcome(
            kind,
            returncode,
            bytes(captured),
            elapsed,
            signal_number=signal_number,
            signal_name=signal_name,
        )
    return ContainedProcessOutcome(
        ContainedOutcomeKind.EXITED,
        returncode,
        bytes(captured),
        elapsed,
        term_sent=term_sent,
        kill_sent=kill_sent,
    )
