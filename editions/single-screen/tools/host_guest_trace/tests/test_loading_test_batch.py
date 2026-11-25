from pathlib import Path
import json
import sys
import os
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import run_loading_test_batch as batch


class LoadingTestBatchTests(unittest.TestCase):
    def test_zero_test_output_is_not_evidence(self):
        with tempfile.TemporaryDirectory() as folder:
            output=Path(folder)/'zero.log'
            output.write_text('Ran 0 tests in 0.000s\n\nOK\n')
            with self.assertRaises(ValueError):
                batch.validate_output(output, {'expect': ['OK']})

    def test_entire_plan_is_validated_before_execution(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder).resolve()
            spec=dict(schema=2,scope='fixture',steps=[dict(name=t,tier=t,
                command=[sys.executable,'-c','print("PASS")'],expect=['PASS'])
                for t in ('focused','capture','regression')])
            spec['steps'][-1]['command']='not an argument list'
            with self.assertRaises(ValueError):
                batch.validate_spec(spec,root)

    def test_real_batch_relative_paths_and_failure_stop(self):
        for fail in (False,True):
            with tempfile.TemporaryDirectory() as folder:
                root=Path(folder).resolve();private=root/'local-analysis';private.mkdir()
                (root/'AGENTS.md').write_text('test fixture instructions')
                (private/'library.so').write_bytes(b'fixture library')
                steps=[dict(name=t,tier=t,command=[sys.executable,'-c',
                    'import sys; sys.exit(7)' if fail and t=='capture' else 'print("fixture pass")'],expect=['fixture pass'])
                    for t in ('focused','capture','regression')]
                (private/'spec.json').write_text(json.dumps(dict(schema=2,scope='synthetic workflow test',steps=steps)))
                real=batch.subprocess.run
                def run(command,**kwargs):
                    return SimpleNamespace(returncode=0) if command[0]=='git' else real(command,**kwargs)
                previous=Path.cwd();os.chdir(root)
                try:
                    with patch.object(batch.subprocess,'run',side_effect=run):
                        result=batch.main(['--manifest','local-analysis/spec.json','--library','local-analysis/library.so',
                                           '--output','local-analysis/report.json'],root=root)
                finally:os.chdir(previous)
                report=json.loads((private/'report.json').read_text())
                self.assertEqual(result,1 if fail else 0)
                self.assertEqual(len(report['steps']),2 if fail else 3)
                self.assertEqual(report['result'],'failed' if fail else 'passed')

    def test_gate_binds_sources_library_outputs_and_required_tiers(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder).resolve();private=root/'local-analysis';private.mkdir()
            source=root/'AGENTS.md';source.write_text('instructions')
            library=private/'library.so';library.write_bytes(b'library fixture')
            output=private/'test.log';output.write_text('test output')
            spec=dict(schema=2,scope='synthetic identity fixture',steps=[dict(name=t,tier=t,
                command=[sys.executable,'-c','print("test output")'],expect=['test output'])
                for t in ('focused','capture','regression')])
            manifest=private/'spec.json';manifest.write_text(json.dumps(spec))
            report=dict(schema=2,result='passed',gameplay_verified=False,library_sha256=batch.digest(library),source_sha256=batch.snapshot(root,[manifest]),
                runtime=batch.runtime_snapshot(spec,root),manifest='local-analysis/spec.json',manifest_sha256=batch.digest(manifest),
                steps=[dict(plan=s,result='passed',returncode=0,output='local-analysis/test.log',output_sha256=batch.digest(output))
                       for s in spec['steps']])
            path=private/'report.json';path.write_text(json.dumps(report))
            self.assertEqual(batch.verify_report(path,library,root),report)
            for file in (source,library,output,manifest):
                before=file.read_bytes();file.write_bytes(b'changed')
                with self.assertRaises(ValueError):batch.verify_report(path,library,root)
                file.write_bytes(before)
            report['steps'].pop();path.write_text(json.dumps(report))
            with self.assertRaises(ValueError):batch.verify_report(path,library,root)
            report['result']='failed';path.write_text(json.dumps(report))
            with self.assertRaises(ValueError):batch.verify_report(path,library,root)


if __name__=='__main__':unittest.main()
