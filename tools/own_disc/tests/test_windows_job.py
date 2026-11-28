#!/usr/bin/env python3
"""Host tests for tools/own_disc/windows_job.py: Win32 Job Object struct layouts, API lifecycle,
fallback/error handling, non-Windows no-op behavior, and builder_core/disc_builder integration.

Runs on macOS, Linux, and Windows using a fake kernel32 test double (and also checks real Win32
Job Object orphan-termination when running on Windows).
"""
from __future__ import annotations

import ctypes
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

REPO = Path(__file__).resolve().parents[3]
OWN_DISC = REPO / 'tools' / 'own_disc'
if str(OWN_DISC) not in sys.path:
    sys.path.insert(0, str(OWN_DISC))
if str(OWN_DISC / 'gui') not in sys.path:
    sys.path.insert(0, str(OWN_DISC / 'gui'))

import windows_job as wj  # noqa: E402


class FakeKernel32:
    """In-memory test double for Win32 kernel32 Job Object functions."""

    def __init__(self, *, create_result=0x123456789ABC, set_info_ok=1, assign_ok=1,
                 terminate_ok=1, open_process_result=0x9000, current_process=0xFFFFFFFFFFFFFFFF,
                 last_error=5):
        self.create_result = create_result
        self.set_info_ok = set_info_ok
        self.assign_ok = assign_ok
        self.terminate_ok = terminate_ok
        self.open_process_result = open_process_result
        self.current_process = current_process
        self.last_error_code = last_error
        self.calls = []
        self.captured_limit_flags = None
        self.captured_info_size = None
        self.closed_handles = []

    def _get_last_error(self):
        return self.last_error_code

    def CreateJobObjectW(self, attrs, name):
        self.calls.append(('CreateJobObjectW', attrs, name))
        return self.create_result

    def SetInformationJobObject(self, handle, info_class, info_ptr, info_len):
        self.calls.append(('SetInformationJobObject', handle, info_class, info_len))
        self.captured_info_size = info_len
        info = ctypes.cast(info_ptr, ctypes.POINTER(wj.JOBOBJECT_EXTENDED_LIMIT_INFORMATION)).contents
        self.captured_limit_flags = info.BasicLimitInformation.LimitFlags
        return self.set_info_ok

    def AssignProcessToJobObject(self, job, proc):
        self.calls.append(('AssignProcessToJobObject', job, proc))
        return self.assign_ok

    def TerminateJobObject(self, job, exit_code):
        self.calls.append(('TerminateJobObject', job, exit_code))
        return self.terminate_ok

    def CloseHandle(self, handle):
        self.calls.append(('CloseHandle', handle))
        self.closed_handles.append(handle)
        return 1

    def GetCurrentProcess(self):
        self.calls.append(('GetCurrentProcess',))
        return self.current_process

    def OpenProcess(self, access, inherit, pid):
        self.calls.append(('OpenProcess', access, inherit, pid))
        return self.open_process_result


class StructLayout(unittest.TestCase):
    """Exact Win32 ABI sizes and offsets for JOBOBJECT_EXTENDED_LIMIT_INFORMATION (x64/ARM64 and x86)."""

    def test_win32_constants(self):
        self.assertEqual(wj.JOB_OBJECT_EXTENDED_LIMIT_INFORMATION_CLASS, 9)
        self.assertEqual(wj.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE, 0x00002000)
        self.assertEqual(wj.PROCESS_TERMINATE, 0x0001)
        self.assertEqual(wj.PROCESS_SET_QUOTA, 0x0100)

    def test_struct_sizes_and_offsets_match_winnt_h(self):
        ptr_size = ctypes.sizeof(ctypes.c_void_p)
        basic = wj.JOBOBJECT_BASIC_LIMIT_INFORMATION
        io = wj.IO_COUNTERS
        ext = wj.JOBOBJECT_EXTENDED_LIMIT_INFORMATION

        self.assertEqual(ctypes.sizeof(io), 48)
        if ptr_size == 8:
            # 64-bit Windows (win32-x86_64 and win32-arm64): exact Microsoft Win32 SDK <winnt.h> layout
            self.assertEqual(ctypes.sizeof(basic), 64)
            self.assertEqual(ctypes.sizeof(ext), 144)
            self.assertEqual(basic.PerProcessUserTimeLimit.offset, 0)
            self.assertEqual(basic.PerJobUserTimeLimit.offset, 8)
            self.assertEqual(basic.LimitFlags.offset, 16)
            self.assertEqual(basic.MinimumWorkingSetSize.offset, 24)
            self.assertEqual(basic.MaximumWorkingSetSize.offset, 32)
            self.assertEqual(basic.ActiveProcessLimit.offset, 40)
            self.assertEqual(basic.Affinity.offset, 48)
            self.assertEqual(basic.PriorityClass.offset, 56)
            self.assertEqual(basic.SchedulingClass.offset, 60)
            self.assertEqual(ext.BasicLimitInformation.offset, 0)
            self.assertEqual(ext.IoInfo.offset, 64)
            self.assertEqual(ext.ProcessMemoryLimit.offset, 112)
            self.assertEqual(ext.JobMemoryLimit.offset, 120)
            self.assertEqual(ext.PeakProcessMemoryUsed.offset, 128)
            self.assertEqual(ext.PeakJobMemoryUsed.offset, 136)
        elif ptr_size == 4:
            self.assertEqual(ctypes.sizeof(basic), 48)
            self.assertEqual(ctypes.sizeof(ext), 112)
            self.assertEqual(ext.IoInfo.offset, 48)
            self.assertEqual(ext.ProcessMemoryLimit.offset, 96)
        else:
            self.fail(f'unexpected pointer size {ptr_size}')

    def test_explicit_64bit_layout_independent_of_host_pointer_size(self):
        # Verify the 64-bit Win32 ABI explicitly using c_uint64 for SIZE_T/ULONG_PTR
        class Basic64(ctypes.Structure):
            _fields_ = [
                ('PerProcessUserTimeLimit', ctypes.c_int64),
                ('PerJobUserTimeLimit', ctypes.c_int64),
                ('LimitFlags', ctypes.c_uint32),
                ('MinimumWorkingSetSize', ctypes.c_uint64),
                ('MaximumWorkingSetSize', ctypes.c_uint64),
                ('ActiveProcessLimit', ctypes.c_uint32),
                ('Affinity', ctypes.c_uint64),
                ('PriorityClass', ctypes.c_uint32),
                ('SchedulingClass', ctypes.c_uint32),
            ]

        class Ext64(ctypes.Structure):
            _fields_ = [
                ('BasicLimitInformation', Basic64),
                ('IoInfo', wj.IO_COUNTERS),
                ('ProcessMemoryLimit', ctypes.c_uint64),
                ('JobMemoryLimit', ctypes.c_uint64),
                ('PeakProcessMemoryUsed', ctypes.c_uint64),
                ('PeakJobMemoryUsed', ctypes.c_uint64),
            ]

        self.assertEqual(ctypes.sizeof(Basic64), 64)
        self.assertEqual(ctypes.sizeof(Ext64), 144)
        self.assertEqual(Basic64.MinimumWorkingSetSize.offset, 24)
        self.assertEqual(Basic64.Affinity.offset, 48)
        self.assertEqual(Ext64.IoInfo.offset, 64)
        self.assertEqual(Ext64.PeakJobMemoryUsed.offset, 136)


class JobLifecycle(unittest.TestCase):
    def test_non_windows_is_a_silent_noop(self):
        errors = []
        with mock.patch.object(wj.sys, 'platform', 'darwin'):
            job = wj.WindowsJob(on_error=errors.append)
            self.assertFalse(job.active)
            self.assertIsNone(job.handle)
            self.assertFalse(job.assign_self())
            self.assertFalse(job.assign_pid(1234))
            self.assertFalse(job.assign_process(mock.Mock(pid=1234, _handle=99)))
            self.assertFalse(job.terminate())
            job.close()
            self.assertIsNone(wj.attach_current_process(on_error=errors.append))
        self.assertEqual(errors, [])

    def test_create_assign_handle_terminate_and_idempotent_close(self):
        k32 = FakeKernel32()
        errors = []
        with wj.WindowsJob(kernel32=k32, on_error=errors.append, enabled=True) as job:
            self.assertTrue(job.active)
            self.assertEqual(k32.captured_limit_flags, wj.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE)
            # Breakaway bits must NOT be set
            self.assertEqual(k32.captured_limit_flags & 0x00000800, 0)  # JOB_OBJECT_LIMIT_BREAKAWAY_OK
            self.assertEqual(k32.captured_limit_flags & 0x00001000, 0)  # JOB_OBJECT_LIMIT_SILENT_BREAKAWAY_OK
            self.assertEqual(k32.captured_info_size, ctypes.sizeof(wj.JOBOBJECT_EXTENDED_LIMIT_INFORMATION))
            # Anonymous, non-inheritable handle (lpJobAttributes=None, lpName=None)
            self.assertEqual(k32.calls[0], ('CreateJobObjectW', None, None))
            # Prefers _handle on subprocess.Popen without calling OpenProcess
            proc = mock.Mock(_handle=0xCAFE, pid=777)
            self.assertTrue(job.assign_process(proc))
            self.assertIn(('AssignProcessToJobObject', k32.create_result, 0xCAFE), k32.calls)
            self.assertFalse(any(c[0] == 'OpenProcess' for c in k32.calls))
            self.assertTrue(job.terminate(1))
            self.assertIn(('TerminateJobObject', k32.create_result, 1), k32.calls)
        # Context exit closed handle once; a second close() is a safe no-op
        job.close()
        self.assertFalse(job.active)
        self.assertEqual(k32.closed_handles, [k32.create_result])
        self.assertEqual(errors, [])

    def test_assign_process_falls_back_to_open_process_and_closes_temp_handle(self):
        k32 = FakeKernel32(open_process_result=0xBEEF)
        errors = []
        with wj.WindowsJob(kernel32=k32, on_error=errors.append, enabled=True) as job:
            proc = mock.Mock(_handle=None, pid=4321)
            self.assertTrue(job.assign_process(proc))
        self.assertIn(('OpenProcess', wj.PROCESS_SET_QUOTA | wj.PROCESS_TERMINATE, False, 4321), k32.calls)
        self.assertIn(('AssignProcessToJobObject', k32.create_result, 0xBEEF), k32.calls)
        self.assertEqual(k32.closed_handles, [0xBEEF, k32.create_result])
        self.assertEqual(errors, [])

    def test_graceful_fallback_on_win32_failures(self):
        # 1. CreateJobObjectW returns NULL
        errors = []
        k32 = FakeKernel32(create_result=0, last_error=5)
        job = wj.WindowsJob(kernel32=k32, on_error=errors.append, enabled=True)
        self.assertFalse(job.active)
        self.assertEqual(job.last_error, 5)
        self.assertTrue(errors and 'CreateJobObjectW failed (error 5)' in errors[0], errors)

        # 2. SetInformationJobObject fails -> closes job handle without leaking
        errors = []
        k32 = FakeKernel32(set_info_ok=0, last_error=87)
        job = wj.WindowsJob(kernel32=k32, on_error=errors.append, enabled=True)
        self.assertFalse(job.active)
        self.assertEqual(k32.closed_handles, [k32.create_result])
        self.assertTrue(errors and 'SetInformationJobObject failed (error 87)' in errors[0], errors)

        # 3. AssignProcessToJobObject fails (e.g. Windows 7 / restricted nested job, ERROR_ACCESS_DENIED)
        errors = []
        k32 = FakeKernel32(assign_ok=0, last_error=5)
        with wj.WindowsJob(kernel32=k32, on_error=errors.append, enabled=True) as job:
            self.assertTrue(job.active)
            self.assertFalse(job.assign_pid(999))
            self.assertEqual(job.last_error, 5)
        self.assertEqual(k32.closed_handles, [k32.open_process_result, k32.create_result])
        self.assertTrue(errors and 'AssignProcessToJobObject failed (error 5)' in errors[0], errors)

        # 4. OpenProcess fails (process already exited)
        errors = []
        k32 = FakeKernel32(open_process_result=0, last_error=87)
        with wj.WindowsJob(kernel32=k32, on_error=errors.append, enabled=True) as job:
            self.assertFalse(job.assign_pid(999))
        self.assertTrue(errors and 'OpenProcess(999) failed (error 87)' in errors[0], errors)

    def test_attach_current_process_is_idempotent_and_cleans_up_on_failure(self):
        prev = wj._SELF_JOB
        wj._SELF_JOB = None
        try:
            # Failure closes handle and leaves _SELF_JOB unset
            errors = []
            k_fail = FakeKernel32(assign_ok=0, last_error=5)
            self.assertIsNone(wj.attach_current_process(kernel32=k_fail, on_error=errors.append, enabled=True))
            self.assertEqual(k_fail.closed_handles, [k_fail.create_result])
            self.assertIsNone(wj._SELF_JOB)

            # Success retains handle and second call returns the same job without creating another
            k_ok = FakeKernel32()
            j1 = wj.attach_current_process(kernel32=k_ok, on_error=errors.append, enabled=True)
            j2 = wj.attach_current_process(kernel32=k_ok, on_error=errors.append, enabled=True)
            self.assertIsNotNone(j1)
            self.assertIs(j1, j2)
            self.assertEqual(sum(1 for c in k_ok.calls if c[0] == 'CreateJobObjectW'), 1)
            self.assertIn(('AssignProcessToJobObject', k_ok.create_result, k_ok.current_process), k_ok.calls)
            self.assertEqual(k_ok.closed_handles, [])
        finally:
            wj._SELF_JOB = prev

    def test_run_in_job_assigns_and_closes_on_success_and_interrupt(self):
        k32 = FakeKernel32()

        class FakeProc:
            def __init__(self, fail_comm=False):
                self._handle = 0x5555
                self.pid = 888
                self.returncode = 0
                self.killed = False
                self.waited = False
                self._fail_comm = fail_comm

            def communicate(self):
                if self._fail_comm:
                    raise KeyboardInterrupt()
                return b'ok', None

            def kill(self):
                self.killed = True

            def wait(self):
                self.waited = True
                return -1

        res = wj.run_in_job(['cmd'], kernel32=k32, popen=lambda argv, **kw: FakeProc())
        self.assertEqual(res.returncode, 0)
        self.assertEqual(res.stdout, b'ok')
        self.assertIn(('AssignProcessToJobObject', k32.create_result, 0x5555), k32.calls)
        self.assertEqual(k32.closed_handles, [k32.create_result])

        # On KeyboardInterrupt, job is terminated and closed cleanly
        k32_int = FakeKernel32()
        proc_int = FakeProc(fail_comm=True)
        with self.assertRaises(KeyboardInterrupt):
            wj.run_in_job(['cmd'], kernel32=k32_int, popen=lambda argv, **kw: proc_int)
        self.assertIn(('TerminateJobObject', k32_int.create_result, 1), k32_int.calls)
        self.assertEqual(k32_int.closed_handles, [k32_int.create_result])
        self.assertTrue(proc_int.killed and proc_int.waited)

    def test_recipe_helper_isolation(self):
        # windows_job.py must never be imported by host_support.py (whose SHA-256 is pinned in shipped recipe records)
        host_src = (OWN_DISC / 'host_support.py').read_text(encoding='utf-8')
        self.assertNotIn('windows_job', host_src)

    def test_builder_launcher_c_keeps_job_object_and_mutants_intact(self):
        src = (OWN_DISC / 'windows' / 'builder_launcher.c').read_text(encoding='utf-8')
        for needed in ('CreateJobObjectW(NULL, NULL)', 'JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE',
                       'SetInformationJobObject', 'CREATE_SUSPENDED', 'AssignProcessToJobObject',
                       'ResumeThread'):
            self.assertIn(needed, src)
        self.assertNotIn('JOB_OBJECT_LIMIT_BREAKAWAY_OK', src)
        # Verify every existing mutant in mutants_launcher.py still matches exactly once
        sys.path.insert(0, str(OWN_DISC / 'windows'))
        import mutants_launcher  # noqa: E402
        for what, old, _ in mutants_launcher.MUTANTS:
            self.assertEqual(src.count(old), 1, f'mutant broken: {what}')


@unittest.skipUnless(sys.platform == 'win32', 'real Win32 Job Object orphan kill test runs on Windows')
class RealWindowsOrphanKill(unittest.TestCase):
    """Spawns parent -> child -> grandchild sleeper on Windows, kills parent via TerminateProcess, verifies grandchild dies."""

    def test_killing_parent_with_self_job_kills_grandchild(self):
        with tempfile.TemporaryDirectory(prefix='job-test-') as tmp:
            pid_file = Path(tmp) / 'grandchild.pid'
            parent_script = Path(tmp) / 'parent.py'
            parent_script.write_text(
                f'import subprocess, sys, time\n'
                f'sys.path.insert(0, {str(OWN_DISC)!r})\n'
                f'import windows_job\n'
                f'job = windows_job.attach_current_process()\n'
                f'assert job is not None and job.active\n'
                f'grand = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])\n'
                f'open({str(pid_file)!r}, "w", encoding="utf-8", newline="\\n").write(str(grand.pid))\n'
                f'time.sleep(60)\n',
                encoding='utf-8', newline='\n',
            )
            parent = subprocess.Popen([sys.executable, '-B', str(parent_script)])
            deadline = time.monotonic() + 15
            while not pid_file.is_file() or not pid_file.read_text(encoding='utf-8').strip():
                self.assertLess(time.monotonic(), deadline, 'grandchild did not start')
                time.sleep(0.05)
            grand_pid = int(pid_file.read_text(encoding='utf-8').strip())
            parent.kill()
            parent.wait(timeout=10)
            # Grandchild must exit promptly because parent's kill-on-close Job Object closed
            import builder as B  # noqa: E402
            end = time.monotonic() + 10
            while B.process_alive(grand_pid) and time.monotonic() < end:
                time.sleep(0.1)
            self.assertFalse(B.process_alive(grand_pid), f'grandchild {grand_pid} survived parent kill!')


if __name__ == '__main__':
    unittest.main()
