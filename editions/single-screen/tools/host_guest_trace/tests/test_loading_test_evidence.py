"""Real orchestration failures and mocked device checks; no game files needed."""
import copy
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import run_loading_test_batch as batch
import loading_test_evidence as evidence


class EvidenceTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.private = self.root / 'local-analysis'
        self.private.mkdir()
        (self.root / 'AGENTS.md').write_text('fixture')
        (self.private / 'library.so').write_bytes(b'library')
        self.spec = dict(schema=2, scope='synthetic tests', steps=[dict(name=t, tier=t,
            command=[sys.executable, '-c', 'print("FIXTURE_PASS")'], expect=['^FIXTURE_PASS$'],
            reusable=t != 'capture') for t in ('focused', 'capture', 'regression')])

    def save(self):
        (self.private / 'spec.json').write_text(json.dumps(self.spec))

    def run_batch(self, name='report', resume=None, check=False, for_install=False):
        args = ['--manifest', 'local-analysis/spec.json', '--library', 'local-analysis/library.so',
                '--output', 'local-analysis/' + name + '.json']
        if resume:
            args += ['--resume', 'local-analysis/' + resume + '.json']
        if check:
            args += ['--check']
        if for_install:
            args += ['--for-install']
        real = batch.subprocess.run
        def run(command, **kwargs):
            return SimpleNamespace(returncode=0) if command[0] == 'git' else real(command, **kwargs)
        with patch.object(batch.subprocess, 'run', side_effect=run):
            return batch.main(args, root=self.root)

    def report(self, name='report'):
        return json.loads((self.private / (name + '.json')).read_text())

    def test_invalid_late_step_prevents_earlier_execution(self):
        self.spec['steps'][-1]['command'] = 'not argv'
        self.save()
        with patch.object(batch, 'run_command') as run:
            with self.assertRaisesRegex(ValueError, 'argument-list'):
                self.run_batch()
            run.assert_not_called()
        self.assertFalse((self.private / 'report.json').exists())

    def test_zero_tests_not_a_successful_batch(self):
        self.spec['steps'][0].update(command=[sys.executable, '-c', 'print("Ran 0 tests in 0.000s\\nOK")'], expect=['OK'])
        self.save()
        self.assertEqual(self.run_batch(), 1)
        self.assertIn('empty test', self.report()['steps'][0]['error'])
        self.assertEqual(len(self.report()['steps']), 1)

    def test_dry_run_does_not_execute_tests(self):
        self.save()
        with patch.object(batch, 'run_command') as run, patch.object(batch, 'device_check') as device:
            self.assertEqual(self.run_batch(check=True), 0)
            run.assert_not_called()
            device.assert_not_called()
        self.assertFalse((self.private / 'report.json').exists())

    def test_install_preflight_requires_contract_before_executing_any_test(self):
        self.save()
        with patch.object(batch, 'run_command') as run, patch.object(batch, 'device_check') as device:
            with self.assertRaisesRegex(ValueError, 'coverage contract'):
                self.run_batch(check=True, for_install=True)
            run.assert_not_called(); device.assert_not_called()

    def test_install_preflight_refuses_hash_only_probe_without_current_build(self):
        self.device_fixture()
        self.spec['coverage'] = dict(purpose='diagnostic',rationale='fixture known unknown',cases=[
            dict(id='next',check='connected',deferred_reason='Fixture unavailable')])
        self.save()
        with patch.object(batch, 'run_command') as run, patch.object(batch, 'device_check') as device:
            with self.assertRaisesRegex(ValueError, 'build receipt required'):
                self.run_batch(check=True, for_install=True)
            run.assert_not_called(); device.assert_not_called()

    def test_resume_exact_host_checks_but_not_capture(self):
        self.save()
        self.assertEqual(self.run_batch(), 0)
        self.assertEqual(self.run_batch('resumed', 'report'), 0)
        report = self.report('resumed')
        self.assertEqual(report['reused_steps'], 2)
        self.assertEqual(report['executed_steps'], 1)
        self.assertNotIn('reused_from', report['steps'][1])
        (self.root / 'AGENTS.md').write_text('changed')
        with self.assertRaisesRegex(ValueError, 'resume refused'):
            self.run_batch('stale', 'report')

    def test_interrupt_checkpoint_resumes_completed_host_step(self):
        self.save()
        real = batch.run_command
        def run(command, output, root, timeout):
            if output.name.endswith('-01.log'):
                output.write_text('partial log')
                raise KeyboardInterrupt()
            return real(command, output, root, timeout)
        with patch.object(batch, 'run_command', side_effect=run):
            self.assertEqual(self.run_batch(), 1)
        self.assertEqual(self.report()['result'], 'interrupted')
        self.assertEqual(self.report()['steps'][1]['result'], 'interrupted')
        self.assertEqual(self.run_batch('resumed', 'report'), 0)
        self.assertEqual(self.report('resumed')['reused_steps'], 1)

    def test_resume_rejects_changed_log_tool_and_environment(self):
        self.save()
        self.assertEqual(self.run_batch(), 0)
        with patch.dict(os.environ, {'LOADING_TEST_CONFIGURATION': 'changed'}):
            with self.assertRaisesRegex(ValueError, 'runtime'):
                self.run_batch('environment', 'report')
        report = self.report()
        report['runtime']['tools']['fixture'] = 'changed tool'
        with self.assertRaisesRegex(ValueError, 'runtime'):
            batch.resume_records(self.private / 'report.json', report, self.root)
        (self.private / 'report-00.log').write_text('FIXTURE_PASS\nchanged')
        with self.assertRaisesRegex(ValueError, 'output changed'):
            self.run_batch('tampered', 'report')

    def test_shell_nesting_normalized_for_tests_and_independent_verifier(self):
        self.spec['steps'][0]['command'][-1] = ('import os; assert os.environ["SHLVL"]=="0"; '
            'assert os.environ["_"]=="loading-test-batch"; print("FIXTURE_PASS")')
        self.save()
        with patch.dict(os.environ, {'SHLVL': '1', '_': 'previous-command'}):
            self.assertEqual(self.run_batch(), 0)
        with patch.dict(os.environ, {'SHLVL': '2', '_': 'different-command'}):
            self.assertEqual(batch.verify_report(self.private / 'report.json',
                self.private / 'library.so', self.root)['result'], 'passed')
            self.assertEqual(self.run_batch('resumed', 'report'), 0)

    def test_collision_and_lock_prevent_execution(self):
        self.save()
        with evidence.batch_lock(self.root), patch.object(batch, 'run_command') as run:
            with self.assertRaisesRegex(ValueError, 'another validation'):
                self.run_batch()
            run.assert_not_called()
        log = self.private / 'report-02.log'
        log.write_text('preserve me')
        with self.assertRaisesRegex(ValueError, 'preserve'):
            self.run_batch()
        self.assertEqual(log.read_text(), 'preserve me')

    def test_reject_unbounded_ambiguous_or_missing_requirements(self):
        mutations = [lambda s: s.update(schema=1),
            lambda s: s['steps'][1].update(name='focused'),
            lambda s: s['steps'][1].update(expect=[]),
            lambda s: s['steps'][1].update(reusable=True),
            lambda s: s['steps'][1].update(timeout_seconds=61),
            lambda s: s['steps'][1].update(timeout_seconds=float('nan')),
            lambda s: s['steps'][1].update(command=['/missing/tool']),
            lambda s: s['steps'][1].update(expect=['['])]
        for mutate in mutations:
            spec = copy.deepcopy(self.spec)
            mutate(spec)
            with self.subTest(mutation=mutate), self.assertRaises((ValueError, re.error)):
                batch.validate_spec(spec, self.root)

    def test_empty_ctest_and_all_target_build_rejected(self):
        with patch.object(evidence, 'executable', side_effect=lambda c, _: Path('/tools/' + c)):
            for command, kind in [(['ctest', '-R', 'bad'], 'host'),
                                  (['cmake', '--build', 'build'], 'build'),
                                  (['cmake', '--build', 'build', '--target', 'all'], 'build')]:
                spec = copy.deepcopy(self.spec)
                spec['steps'][0].update(command=command, kind=kind, reusable=False)
                with self.assertRaises(ValueError):
                    batch.validate_spec(spec, self.root)

    def test_expanded_batch_stays_bounded_and_validates_last_step(self):
        template=copy.deepcopy(self.spec['steps'][2])
        for i in range(len(self.spec['steps']), evidence.MAX_TEST_STEPS):
            self.spec['steps'].append(dict(template,name=f'regression-{i}'))
        self.assertEqual(len(self.spec['steps']),128)
        batch.validate_spec(self.spec,self.root)
        self.spec['steps'].append(dict(template,name='one-too-many'))
        with self.assertRaisesRegex(ValueError,'bounded'):
            batch.validate_spec(self.spec,self.root)
        self.spec['steps'].pop()
        self.spec['steps'][-1]['command']='invalid late command'
        self.save()
        with patch.object(batch,'run_command') as run:
            with self.assertRaisesRegex(ValueError,'argument-list'):
                self.run_batch(check=True)
            run.assert_not_called()

    def test_timeout_kills_host_descendants(self):
        log = self.private / 'timeout.log'
        code = ('import subprocess,sys,time; p=subprocess.Popen([sys.executable,"-c","import time; time.sleep(30)"]); '
                'print(p.pid,flush=True); time.sleep(30)')
        self.assertEqual(evidence.run_command([sys.executable, '-c', code], log, self.root, 0.3), 124)
        child = log.read_text().strip()
        self.assertTrue(child.isdigit())
        status = subprocess.run(['ps', '-o', 'stat=', '-p', child], capture_output=True, text=True)
        self.assertTrue(not status.stdout.strip() or status.stdout.strip().startswith('Z'), status.stdout)

    def test_paths_reject_symlink_and_traversal(self):
        (self.private / 'alias').symlink_to(self.root / 'AGENTS.md')
        for path in ('local-analysis/alias', 'local-analysis/../AGENTS.md', '../outside'):
            with self.assertRaises(ValueError):
                evidence.local_path(self.root, path, private=True)

    def device_fixture(self):
        adb = self.private / 'adb'
        adb.write_text('#!/bin/sh\nexit 0\n')
        adb.chmod(0o700)
        request = self.private / 'request.json'
        request.write_text(json.dumps(dict(serial='thor', request_id='a' * 32, pid=123,
            run_directory='/data/user/0/dev.test/files/private-trace/run-' + 'a' * 32)))
        probe = self.private / 'probe'
        probe.write_bytes(b'probe bytes')
        self.spec['device'] = dict(adb=str(adb), serial='thor', package='dev.test',
                                   previous_request='local-analysis/request.json')
        step = self.spec['steps'][-1]
        step.update(kind='device', reusable=False, command=[str(adb), '-s', 'thor', 'shell', '/data/local/tmp/test/probe'],
                    device_inputs=[dict(local='local-analysis/probe', remote='/data/local/tmp/test/probe')])
        return step, probe

    def test_live_device_or_hash_mismatch_refuses_probe(self):
        step, probe = self.device_fixture()
        batch.validate_spec(self.spec, self.root)
        for sha in (evidence.digest(probe), '0' * 64):
            replies = [SimpleNamespace(stdout=b'fingerprint\narm64-v8a\nboot-id\n'),
                       SimpleNamespace(stdout=(sha + '  /data/local/tmp/test/probe\n').encode())]
            with patch.object(evidence, 'require_previous_terminal', return_value={'authoritative_state': 'TERMINAL'}), \
                    patch.object(evidence.subprocess, 'run', side_effect=replies):
                if sha.startswith('0'):
                    with self.assertRaisesRegex(ValueError, 'differs'):
                        evidence.device_check(self.spec, step, self.root)
                else:
                    self.assertEqual(evidence.device_check(self.spec, step, self.root)['abi'], 'arm64-v8a')
        with patch.object(evidence, 'require_previous_terminal', side_effect=RuntimeError('LIVE')), \
                patch.object(evidence.subprocess, 'run') as run:
            with self.assertRaisesRegex(RuntimeError, 'LIVE'):
                evidence.device_check(self.spec, step, self.root)
            run.assert_not_called()

    def test_device_scope_order_and_input_guards(self):
        self.device_fixture()
        for change in ({'reusable': True}, {'device_inputs': []}, {'kind': 'host'}, {'command': ['bad']}):
            spec = copy.deepcopy(self.spec)
            spec['steps'][-1].update(change)
            with self.assertRaises(ValueError):
                batch.validate_spec(spec, self.root)
        self.spec['steps'][1], self.spec['steps'][2] = self.spec['steps'][2], self.spec['steps'][1]
        with self.assertRaisesRegex(ValueError, 'precede device'):
            batch.validate_spec(self.spec, self.root)

    def test_large_device_manifest_hashes_every_input_in_bounded_groups(self):
        step, probe = self.device_fixture()
        step['device_inputs'] = [dict(local='local-analysis/probe', remote=f'/data/local/tmp/test/input-{i}') for i in range(130)]
        sha = evidence.digest(probe)
        for fault in ('none', 'missing-last', 'duplicate-last', 'wrong-last'):
            groups = []
            def run(command, **kwargs):
                if command[4] != 'sha256sum':
                    return SimpleNamespace(stdout=b'fingerprint\narm64-v8a\nboot-id\n')
                paths = command[5:];groups.append(paths)
                lines = [sha + '  ' + p for p in paths]
                if paths[-1].endswith('input-129'):
                    if fault == 'missing-last':lines.pop()
                    if fault == 'duplicate-last':lines.append(lines[-1])
                    if fault == 'wrong-last':lines[-1] = '0' * 64 + '  ' + paths[-1]
                return SimpleNamespace(stdout=('\n'.join(lines)+'\n').encode())
            with patch.object(evidence, 'require_previous_terminal', return_value={'authoritative_state':'TERMINAL'}), \
                    patch.object(evidence.subprocess, 'run', side_effect=run):
                if fault == 'none':
                    self.assertEqual(len(evidence.device_check(self.spec, step, self.root)['inputs_sha256']),130)
                else:
                    with self.assertRaises(ValueError):evidence.device_check(self.spec, step, self.root)
            self.assertEqual(list(map(len,groups)),[64,64,2])
            self.assertEqual([p for group in groups for p in group],[i['remote'] for i in step['device_inputs']])

    def test_device_is_fresh_on_resume_and_has_remote_timeout(self):
        self.device_fixture()
        self.save()
        def run(command, output, root, timeout):
            output.write_text('FIXTURE_PASS\n')
            return 0
        with patch.object(batch, 'run_command', side_effect=run) as run, \
                patch.object(batch, 'device_check', return_value={'abi': 'fixture'}) as device:
            self.assertEqual(self.run_batch(), 0)
            self.assertEqual(self.run_batch('resumed', 'report'), 0)
            self.assertEqual(device.call_count, 2)
            self.assertEqual(run.call_args.args[0][4:8], ['timeout', '-s', 'KILL', '55'])
        self.assertNotIn('reused_from', self.report('resumed')['steps'][-1])


if __name__ == '__main__':
    unittest.main()
