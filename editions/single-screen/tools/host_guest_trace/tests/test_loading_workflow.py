from pathlib import Path
import io
import json
import sys
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import loading_workflow as w


class LoadingWorkflowTests(unittest.TestCase):
    def test_live_logger_resumes_after_client_exit_without_duplicates(self):
        # 2026-09-28: a logcat client that exits (logd dropping a slow reader) is restarted from the last delivered
        # timestamp; the replayed lines are skipped, a marker records the gap, close() stops the current client.
        import threading
        L = lambda ts, text: f'03-03 {ts}  7644  7712 I RecompRenderer: {text}\n'
        a, b, b2, c = L('14:31:52.287', 'RENDER_DIAG_KICK n=1'), L('14:31:52.300', 'RENDER_DIAG_KICK n=2'), \
            L('14:31:52.300', 'RENDER_DIAG_SUBMIT n=2'), L('14:31:53.001', 'RENDER_DIAG_KICK n=3')
        class Proc:
            def __init__(self, lines, block=False):
                self.stopped = threading.Event(); self.returncode = 0
                def gen():
                    yield from lines
                    if block: self.stopped.wait(5)
                self.stdout = gen()
            def wait(self, timeout=None): return 0
            def terminate(self): self.stopped.set()
        procs = [Proc([a, b]), Proc([a, b, b2, c]), Proc([], block=True)]
        calls = []
        def spawn(since):
            calls.append(since); return procs[len(calls) - 1]
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / 'live.log'
            cap = w.LiveLogCapture(['adb'], 123, out, spawn=spawn)
            for _ in range(100):
                if len(calls) >= 3: break
                import time; time.sleep(0.01)
            cap.close()
            text = out.read_text()
        self.assertEqual(calls, [None, '03-03 14:31:52.300', '03-03 14:31:53.001'])  # logcat -T 'MM-DD hh:mm:ss.mmm'
        self.assertEqual(text.count('RENDER_DIAG_KICK n=1'), 1)
        self.assertEqual(text.count('RENDER_DIAG_KICK n=2'), 1)
        self.assertEqual(text.count('RENDER_DIAG_SUBMIT n=2'), 1)  # new at the resume timestamp: kept
        self.assertEqual(text.count('RENDER_DIAG_KICK n=3'), 1)
        self.assertEqual(text.count('WORKFLOW_LOGGER_RESTARTED'), 2)
        self.assertEqual(cap.restarts, 2)

    def test_compact_log_keeps_all_failures_unknowns_and_input(self):
        output=io.StringIO();log=w.CompactRendererLog(output)
        for i in range(2500):log.consume(f'RENDER_API_TRANSLATED_DRAW completed=1 n={i}\n')
        important=['RENDER_API_TRANSLATED_DRAW completed=0\n','RENDER_API_CUBE_RECTANGLES completed=1\n',
                   'RecompNativeInput: B down\n','REJECT_MANIFEST\n','TRACE_RETURNED_PROTOCOL_SAVED\n']
        for line in important:log.consume(line)
        summary=log.finish()
        for line in important:self.assertIn(line,output.getvalue())
        self.assertIn('n=2499',output.getvalue());self.assertTrue(log.terminal.is_set())
        self.assertEqual(summary['routine_counts']['RENDER_API_TRANSLATED_DRAW'],2500)
        self.assertLess(summary['kept_lines'],20);self.assertEqual(log.rejection,'REJECT_MANIFEST')

    def test_launch_rejection_requires_private_trace_event_not_renderer_substring(self):
        prefix='09-21 20:20:07.592  4962  5123 I '
        non_rejections=[
            'RENDER_API_STENCIL_REJECT_RECTANGLE completed=1 passing=0\n',
            prefix+'RecompRenderer: RENDER_API_STENCIL_REJECT_RECTANGLE completed=1 passing=0\n',
            prefix+'RecompRenderer: REJECT_SOMETHING completed=0\n',
            prefix+'RecompPrivateTrace: diagnostic mentions REJECT_HASH\n',
            'diagnostic: REJECT_MANIFEST\n',
            'XREJECT_HASH\n',
        ]
        output=io.StringIO();log=w.CompactRendererLog(output)
        for line in non_rejections:
            log.consume(line)
            self.assertIsNone(log.rejection,line)
            self.assertIn(line,output.getvalue())
        self.assertFalse(log.terminal.is_set())
        for line in ('REJECT_MANIFEST\n',
                     prefix+'RecompPrivateTrace: REJECT_REQUEST_STORAGE pid=4962 uid=10231\n',
                     prefix+'RecompPrivateTrace: REJECT_IO_FileNotFoundException pid=4962\n',
                     prefix+'RecompPrivateTrace: REJECT_RUN_SETUP_-1 pid=4962\n'):
            with self.subTest(line=line):
                event=w.CompactRendererLog(io.StringIO());event.consume(line)
                self.assertEqual(event.rejection,line.strip())
                self.assertFalse(event.terminal.is_set())
        log.consume(prefix+'RecompPrivateTrace: TRACE_RETURNED_PROTOCOL_SAVED pid=4962\n')
        self.assertTrue(log.terminal.is_set())
        self.assertIsNone(log.rejection)

    def test_receipts_bind_compiler_headers_optional_set_and_object(self):
        with tempfile.TemporaryDirectory() as folder:
            r=Path(folder);compiler=r/'compiler';header=r/'header';obj=r/'object';receipt=r/'receipt'
            for p in (compiler,header,obj):p.write_text('initial')
            command=[str(compiler),'-O2','-c','source']
            original=w.build_fingerprint(command,[header]);w.write_receipt(receipt,original,obj)
            self.assertTrue(w.reusable_object(receipt,original,obj))
            for path in (compiler,header):
                path.write_text('changed');self.assertNotEqual(w.build_fingerprint(command,[header]),original);path.write_text('initial')
            optional=r/'new-pack';optional.write_text('new')
            self.assertNotEqual(w.build_fingerprint(command,[header],[optional]),original)
            self.assertNotEqual(w.build_fingerprint(command+['-DNEW'],[header]),original)
            obj.write_text('other');self.assertFalse(w.reusable_object(receipt,original,obj))
            receipt.write_text('{}');self.assertFalse(w.reusable_object(receipt,original,obj))

    def test_depfile_spaces_continuations_and_missing_input(self):
        with tempfile.TemporaryDirectory() as folder:
            p=Path(folder)/'object.d';p.write_text('object: a\\ b.h \\\n c.h\n')
            self.assertEqual(w.depfile_inputs(p,folder),[str((Path(folder)/'a b.h').resolve()),str((Path(folder)/'c.h').resolve())])
            with self.assertRaises(OSError):w.build_fingerprint(['/missing/compiler'],[p])
            p.write_text('not dependencies')
            with self.assertRaises(ValueError):w.depfile_inputs(p,folder)

    def test_previous_run_guard_never_stops_live_unknown_or_other_pid(self):
        record=dict(serial='serial',request_id='a'*32,pid='123',
            run_directory='/data/user/0/dev.test/files/private-trace/run-'+'a'*32)
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'request.json';path.write_text(json.dumps(record))
            for state in ('LIVE','', 'timeout', 'TERMINAL','MISSING'):
                responses=[SimpleNamespace(returncode=0,stdout=b'123\n'),SimpleNamespace(stdout=state.encode())]
                with patch.object(w.subprocess,'run',side_effect=responses) as call:
                    if state in ('TERMINAL','MISSING'):
                        self.assertEqual(w.require_previous_terminal(['adb'],'dev.test','serial',path)['authoritative_state'],state)
                    else:
                        with self.assertRaises(RuntimeError):w.require_previous_terminal(['adb'],'dev.test','serial',path)
                    self.assertFalse(any('force-stop' in str(c) for c in call.call_args_list))
                    command=call.call_args_list[-1].args[0]
                    self.assertTrue(command[-1].startswith("'if test -s "))
                    self.assertEqual(w.shlex.split(' '.join(command[2:]))[-1],w.status_script(record['run_directory'],123))
            with patch.object(w.subprocess,'run',return_value=SimpleNamespace(returncode=0,stdout=b'456\n')):
                with self.assertRaises(RuntimeError):w.require_previous_terminal(['adb'],'dev.test','serial',path)
            with self.assertRaises(ValueError):w.validate_request(record,'dev.test','another')
            with self.assertRaises(ValueError):w.status_script(record['run_directory']+'; bad',123)


if __name__=='__main__':unittest.main()
