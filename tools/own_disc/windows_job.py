"""Windows Job Object helper: ensures child build processes die when their parent exits or is killed.

On Windows, terminating a parent process (builder.exe, builder.py, or the GUI server) leaves descendants
(portable_compile.py, replay_build.py, clang++.exe, ld.lld.exe, XenonRecomp.exe) running as orphans unless they belong
to a Job Object configured with JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE.

Its own module, not host_support.py: host_support.py is pinned in shipped recipe records, so edits there invalidate
recipe stages. Nothing a recipe pass imports may import this module. Zero third-party dependencies (ctypes only).
"""
from __future__ import annotations

import ctypes
import subprocess
import sys
import threading

JOB_OBJECT_EXTENDED_LIMIT_INFORMATION_CLASS = 9   # JobObjectExtendedLimitInformation
JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
# Do NOT set JOB_OBJECT_LIMIT_BREAKAWAY_OK (0x0800) or JOB_OBJECT_LIMIT_SILENT_BREAKAWAY_OK (0x1000):
# descendants must stay inside the job so no tool can escape cleanup.

PROCESS_TERMINATE = 0x0001
PROCESS_SET_QUOTA = 0x0100


class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [
        ('PerProcessUserTimeLimit', ctypes.c_int64),
        ('PerJobUserTimeLimit', ctypes.c_int64),
        ('LimitFlags', ctypes.c_uint32),
        ('MinimumWorkingSetSize', ctypes.c_size_t),
        ('MaximumWorkingSetSize', ctypes.c_size_t),
        ('ActiveProcessLimit', ctypes.c_uint32),
        ('Affinity', ctypes.c_size_t),
        ('PriorityClass', ctypes.c_uint32),
        ('SchedulingClass', ctypes.c_uint32),
    ]


class IO_COUNTERS(ctypes.Structure):
    _fields_ = [
        ('ReadOperationCount', ctypes.c_uint64),
        ('WriteOperationCount', ctypes.c_uint64),
        ('OtherOperationCount', ctypes.c_uint64),
        ('ReadTransferCount', ctypes.c_uint64),
        ('WriteTransferCount', ctypes.c_uint64),
        ('OtherTransferCount', ctypes.c_uint64),
    ]


class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [
        ('BasicLimitInformation', JOBOBJECT_BASIC_LIMIT_INFORMATION),
        ('IoInfo', IO_COUNTERS),
        ('ProcessMemoryLimit', ctypes.c_size_t),
        ('JobMemoryLimit', ctypes.c_size_t),
        ('PeakProcessMemoryUsed', ctypes.c_size_t),
        ('PeakJobMemoryUsed', ctypes.c_size_t),
    ]


def _default_log(message: str) -> None:
    print(f'builder: warning: {message}', file=sys.stderr)


def _configure_kernel32(dll):
    """Configures explicit 64-bit-safe argtypes/restype on kernel32 (or a test double)."""
    specs = {
        'CreateJobObjectW': ([ctypes.c_void_p, ctypes.c_wchar_p], ctypes.c_void_p),
        'SetInformationJobObject': ([ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, ctypes.c_uint32], ctypes.c_int),
        'AssignProcessToJobObject': ([ctypes.c_void_p, ctypes.c_void_p], ctypes.c_int),
        'TerminateJobObject': ([ctypes.c_void_p, ctypes.c_uint32], ctypes.c_int),
        'CloseHandle': ([ctypes.c_void_p], ctypes.c_int),
        'GetCurrentProcess': ([], ctypes.c_void_p),
        'OpenProcess': ([ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32], ctypes.c_void_p),
    }
    for name, (argtypes, restype) in specs.items():
        fn = getattr(dll, name, None)
        if fn is not None and not getattr(fn, '_fivepocket_typed', False):
            try:
                fn.argtypes = argtypes
                fn.restype = restype
                fn._fivepocket_typed = True
            except (AttributeError, TypeError):
                pass
    return dll


def _get_kernel32(dll=None):
    if dll is not None:
        return _configure_kernel32(dll)
    return _configure_kernel32(ctypes.WinDLL('kernel32', use_last_error=True))


def _last_error(dll) -> int:
    get_err = getattr(dll, '_get_last_error', None)
    if callable(get_err):
        return int(get_err())
    return ctypes.get_last_error()


class WindowsJob:
    """Anonymous, non-inheritable Windows Job Object configured with JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE.

    No-op on non-Windows platforms. On Windows, any Win32 API failure (e.g. a restricted sandbox or pre-Windows-8
    nested-job policy) is logged once and leaves the job inactive without crashing the build.
    """

    def __init__(self, *, kernel32=None, on_error=None, enabled=None):
        self._lock = threading.Lock()
        self._on_error = on_error if on_error is not None else _default_log
        self._kernel32 = None
        self.handle = None
        self.last_error = 0
        active_platform = (sys.platform == 'win32') if enabled is None else bool(enabled)
        if not active_platform:
            return
        try:
            k32 = _get_kernel32(kernel32)
        except OSError as error:
            self._report(f'could not load kernel32 for job object ({error})')
            return
        self._kernel32 = k32
        # NULL security attributes -> bInheritHandle = FALSE so children never inherit the job handle.
        handle = k32.CreateJobObjectW(None, None)
        if not handle:
            self.last_error = _last_error(k32)
            self._report(f'CreateJobObjectW failed (error {self.last_error}); child processes will not be tracked in a job')
            return
        info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        ok = k32.SetInformationJobObject(
            handle,
            JOB_OBJECT_EXTENDED_LIMIT_INFORMATION_CLASS,
            ctypes.byref(info),
            ctypes.sizeof(info),
        )
        if not ok:
            self.last_error = _last_error(k32)
            k32.CloseHandle(handle)
            self._report(f'SetInformationJobObject failed (error {self.last_error}); child processes will not be tracked in a job')
            return
        self.handle = handle

    @property
    def active(self) -> bool:
        with self._lock:
            return self.handle is not None

    def _report(self, message: str) -> None:
        if self._on_error is not None:
            self._on_error(message)

    def assign_handle(self, process_handle: int) -> bool:
        """Assigns an open Win32 process handle (with PROCESS_SET_QUOTA | PROCESS_TERMINATE) to this job."""
        with self._lock:
            handle, k32 = self.handle, self._kernel32
        if not handle or not k32 or not process_handle:
            return False
        if not k32.AssignProcessToJobObject(handle, process_handle):
            self.last_error = _last_error(k32)
            self._report(f'AssignProcessToJobObject failed (error {self.last_error}); continuing without job isolation')
            return False
        return True

    def assign_pid(self, pid: int) -> bool:
        """Opens process pid with PROCESS_SET_QUOTA | PROCESS_TERMINATE and assigns it to this job."""
        with self._lock:
            handle, k32 = self.handle, self._kernel32
        if not handle or not k32 or pid <= 0:
            return False
        proc_handle = k32.OpenProcess(PROCESS_SET_QUOTA | PROCESS_TERMINATE, False, int(pid))
        if not proc_handle:
            self.last_error = _last_error(k32)
            self._report(f'OpenProcess({pid}) failed (error {self.last_error}); continuing without job isolation')
            return False
        try:
            return self.assign_handle(proc_handle)
        finally:
            k32.CloseHandle(proc_handle)

    def assign_process(self, process) -> bool:
        """Assigns a subprocess.Popen object to this job (uses its Win32 _handle directly when available)."""
        if not self.active or process is None:
            return False
        raw_handle = getattr(process, '_handle', None)
        if isinstance(raw_handle, int) and raw_handle:
            return self.assign_handle(raw_handle)
        pid = getattr(process, 'pid', None)
        if isinstance(pid, int) and pid > 0:
            return self.assign_pid(pid)
        return False

    def assign_self(self) -> bool:
        """Assigns the current process to this job so all future children inherit it automatically."""
        with self._lock:
            handle, k32 = self.handle, self._kernel32
        if not handle or not k32:
            return False
        return self.assign_handle(k32.GetCurrentProcess())

    def terminate(self, exit_code: int = 1) -> bool:
        """Terminates every process currently in this job with exit_code."""
        with self._lock:
            handle, k32 = self.handle, self._kernel32
        if not handle or not k32:
            return False
        if not k32.TerminateJobObject(handle, int(exit_code)):
            self.last_error = _last_error(k32)
            return False
        return True

    def close(self) -> None:
        """Closes the job handle (idempotent). With KILL_ON_JOB_CLOSE, terminates any remaining processes in the job."""
        with self._lock:
            handle, k32 = self.handle, self._kernel32
            self.handle = None
        if handle and k32:
            k32.CloseHandle(handle)

    def __enter__(self) -> 'WindowsJob':
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()


_SELF_LOCK = threading.Lock()
_SELF_JOB: WindowsJob | None = None


def attach_current_process(*, kernel32=None, on_error=None, enabled=None) -> WindowsJob | None:
    """Attaches the current process to a process-lifetime kill-on-close Job Object (idempotent).

    Intended for CLI entry points (builder.py __main__). The handle is intentionally kept open until OS process exit so
    that closing it never kills this process; when this process exits or is terminated, Windows closes the handle and
    terminates every descendant. Returns the active WindowsJob, or None on non-Windows / failure.
    """
    global _SELF_JOB
    active_platform = (sys.platform == 'win32') if enabled is None else bool(enabled)
    if not active_platform:
        return None
    with _SELF_LOCK:
        if _SELF_JOB is not None and _SELF_JOB.active:
            return _SELF_JOB
        job = WindowsJob(kernel32=kernel32, on_error=on_error, enabled=True)
        if not job.active:
            return None
        if not job.assign_self():
            job.close()
            return None
        _SELF_JOB = job
        return job


def run_in_job(argv, *, kernel32=None, on_error=None, popen=subprocess.Popen, **popen_kwargs) -> subprocess.CompletedProcess:
    """Runs a subprocess inside a scoped WindowsJob so any parent kill or stage abort sweeps all descendants.

    On non-Windows hosts (when kernel32 is not injected), delegates directly to subprocess.run.
    """
    if kernel32 is None and sys.platform != 'win32':
        return subprocess.run(argv, **popen_kwargs)
    with WindowsJob(kernel32=kernel32, on_error=on_error, enabled=True) as job:
        proc = popen(argv, **popen_kwargs)
        job.assign_process(proc)
        try:
            stdout, stderr = proc.communicate()
        except BaseException:
            job.terminate(1)
            job.close()
            try:
                proc.kill()
            except OSError:
                pass
            try:
                proc.wait()
            except OSError:
                pass
            raise
    return subprocess.CompletedProcess(argv, proc.returncode, stdout, stderr)
