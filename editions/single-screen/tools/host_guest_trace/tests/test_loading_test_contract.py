"""Synthetic fixtures deliberately model the wrong-shader and stale-probe bugs."""
import copy
import io
import json
import os
from contextlib import redirect_stdout
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import build_loading_probe as build
from loading_test_contract import MAX_COVERAGE_CASES, validate_coverage, validate_probe_builds
from loading_workflow import digest


class CoverageTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        (self.root/'local-analysis').mkdir()
        self.inputs = []
        for variant in (1,3):
            path = self.root / f'local-analysis/variant{variant}.bin'
            path.write_bytes(bytes([variant]))
            self.inputs.append({'pixel_shader':dict(path=str(path.relative_to(self.root)),sha256=digest(path))})
        self.spec = dict(coverage=dict(purpose='integration',rationale='fixture sequence',cases=[
            dict(id='mip1',check='draw-copy-clear',inputs=self.inputs[0]),
            dict(id='mip2',check='draw-copy-clear',inputs=self.inputs[1])]),steps=[
            dict(name='connected',tier='capture',expect=['^MIP1_CONNECTED_PASS$','^MIP2_CONNECTED_PASS$'],case_evidence=[
                dict(id='mip1',kind='captured',check='draw-copy-clear',inputs=self.inputs[0],expect=['^MIP1_CONNECTED_PASS$']),
                dict(id='mip2',kind='captured',check='draw-copy-clear',inputs=self.inputs[1],expect=['^MIP2_CONNECTED_PASS$'])])])

    def test_real_variant_sequence_requires_exact_input_identity(self):
        _, summary = validate_coverage(self.spec,self.root,installable=True)
        self.assertTrue(summary['complete'])
        # Retain every identity/marker check for an expanded but bounded suite.
        expanded=copy.deepcopy(self.spec)
        for i in range(2,MAX_COVERAGE_CASES):
            case=copy.deepcopy(expanded['coverage']['cases'][0]);case['id']=f'variant-{i}'
            claim=copy.deepcopy(expanded['steps'][0]['case_evidence'][0]);claim['id']=case['id']
            expanded['coverage']['cases'].append(case)
            expanded['steps'][0]['case_evidence'].append(claim)
        _, full=validate_coverage(expanded,self.root,installable=True)
        self.assertEqual(len(full['covered']),192)
        overflow=copy.deepcopy(expanded);overflow['coverage']['cases'].append(dict(id='overflow'))
        with self.assertRaisesRegex(ValueError,'bounded'):
            validate_coverage(overflow,self.root,installable=True)
        expanded['steps'][0]['case_evidence'][-1]['inputs']=self.inputs[1]
        with self.assertRaisesRegex(ValueError,'expected game variant'):
            validate_coverage(expanded,self.root,installable=True)
        self.spec['steps'][0]['case_evidence'][1]['inputs'] = self.inputs[0]
        with self.assertRaisesRegex(ValueError,'expected game variant'):
            validate_coverage(self.spec,self.root)

    def test_synthetic_cannot_satisfy_actual_coverage_even_with_same_bytes(self):
        self.spec['steps'][0]['case_evidence'][1]['kind'] = 'synthetic'
        self.spec['coverage']['cases'][1]['deferred_reason'] = 'Actual mip2 not replayed'
        with self.assertRaisesRegex(ValueError,'actual sequence'):
            validate_coverage(self.spec,self.root)
        self.spec['coverage']['purpose'] = 'component'
        _, summary = validate_coverage(self.spec,self.root)
        self.assertEqual(summary['deferred'],['mip2'])
        with self.assertRaisesRegex(ValueError,'component-only'):
            validate_coverage(self.spec,self.root,installable=True)

    def test_unknown_later_variant_requires_explicit_diagnostic_scope(self):
        self.spec['coverage']['cases'].append(dict(id='mip3',check='draw-copy-clear',
            deferred_reason='Variant5 not captured; no sequence proof'))
        self.spec['coverage']['purpose'] = 'diagnostic'
        _, summary = validate_coverage(self.spec,self.root,installable=True)
        self.assertFalse(summary['complete'])
        self.assertEqual(summary['deferred'],['mip3'])
        del self.spec['coverage']['cases'][-1]['deferred_reason']
        with self.assertRaisesRegex(ValueError,'deferred reason'):
            validate_coverage(self.spec,self.root)

    def test_separate_primitive_checks_or_reused_capture_cannot_claim_connection(self):
        for changes in ({'reusable':True}, {'tier':'focused'}):
            spec=copy.deepcopy(self.spec);spec['steps'][0].update(changes)
            with self.assertRaisesRegex(ValueError,'fresh capture-tier'):
                validate_coverage(spec,self.root)
        self.spec['steps'][0]['case_evidence'][0]['check'] = 'draw-only'
        with self.assertRaisesRegex(ValueError,'connected check'):
            validate_coverage(self.spec,self.root)

    def test_changed_capture_missing_contract_and_missing_receipt_reject(self):
        (self.root/'local-analysis/variant3.bin').write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError,'identity changed'):
            validate_coverage(self.spec,self.root)
        with self.assertRaisesRegex(ValueError,'coverage contract'):
            validate_coverage({},self.root,installable=True)
        with self.assertRaisesRegex(ValueError,'build receipt required'):
            validate_probe_builds(dict(steps=[dict(command=['local-analysis/probe'])]),self.root,required=True)

    def test_case_marker_cannot_be_only_a_general_exit_zero(self):
        self.spec['steps'][0]['expect'] = ['OK']
        with self.assertRaisesRegex(ValueError,'output markers'):
            validate_coverage(self.spec,self.root)


class ProbeBuildTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.private = self.root/'local-analysis';self.private.mkdir()
        (self.root/'AGENTS.md').write_text('fixture')
        self.source = self.private/'probe.cpp'
        self.source.write_text('int main() { return 0; }\n')
        self.header = self.root/'android/app/src/main/cpp/fixture.inc'
        self.header.parent.mkdir(parents=True);self.header.write_text('// tracked dependency fixture\n')
        self.source.write_text('#include "'+str(self.header)+'"\nint main() { return 0; }\n')
        self.compiler = shutil.which('clang++')
        if not self.compiler:
            self.skipTest('real local clang++ unavailable; compiler test not counted as executed')
        self.spec = dict(schema=1,output='local-analysis/probe',additional_inputs=['local-analysis/probe.cpp'],
            command=[self.compiler,'local-analysis/probe.cpp','-o','local-analysis/probe'])
        self.manifest=self.private/'build.json';self.manifest.write_text(json.dumps(self.spec))

    def run_build(self, receipt='receipt', reuse=None, check=False):
        args=['--manifest','local-analysis/build.json','--receipt',f'local-analysis/{receipt}.json']
        if reuse:args+=['--reuse',f'local-analysis/{reuse}.json']
        if check:args+=['--check']
        with redirect_stdout(io.StringIO()):
            return build.main(args,root=self.root)

    def test_actual_build_reuse_and_stale_sources_output_environment_refused(self):
        self.assertEqual(self.run_build(),0)
        report=build.verify_probe_build(self.private/'receipt.json',self.private/'probe',self.root)
        self.assertFalse(report['reused'])
        with patch.object(build,'run_command') as run:
            self.assertEqual(self.run_build('reused','receipt'),0)
            run.assert_not_called()
        self.assertTrue(build.verify_probe_build(self.private/'reused.json',self.private/'probe',self.root)['reused'])
        for path in (self.source,self.header,self.private/'probe'):
            old=path.read_bytes();path.write_bytes(old+b'\n')
            with self.assertRaises(ValueError):
                build.verify_probe_build(self.private/'receipt.json',self.private/'probe',self.root)
            path.write_bytes(old)
        with patch.dict(os.environ, {'LOADING_PROBE_MODE':'changed'}):
            with self.assertRaisesRegex(ValueError,'stale'):
                build.verify_probe_build(self.private/'receipt.json',self.private/'probe',self.root)
        spec=dict(probe_builds=[dict(output='local-analysis/probe',receipt='local-analysis/receipt.json')],
                  steps=[dict(command=['local-analysis/probe'])])
        self.assertTrue(validate_probe_builds(spec,self.root,required=True))

    def test_check_does_not_compile_and_unknown_existing_binary_not_blessed(self):
        with patch.object(build,'run_command') as run:
            self.assertEqual(self.run_build(check=True),0)
            run.assert_not_called()
        (self.private/'probe').write_bytes(b'unknown binary')
        self.assertEqual(self.run_build(),1)
        self.assertEqual((self.private/'probe').read_bytes(),b'unknown binary')
        self.assertFalse((self.private/'receipt.log').exists())

    def test_undeclared_private_translation_unit_rejects_before_build(self):
        self.spec['additional_inputs'] = ['AGENTS.md']
        with self.assertRaisesRegex(ValueError,'undeclared private'):
            build.build_context(self.spec,self.root)

    def test_new_optional_private_header_invalidates_receipt(self):
        self.source.write_text('#if __has_include("optional.h")\n#include "optional.h"\n#endif\nint main(){return 0;}\n')
        self.assertEqual(self.run_build(),0)
        report=build.verify_probe_build(self.private/'receipt.json',self.private/'probe',self.root)
        optional=self.private/'optional.h'
        self.assertNotIn(str(optional),report['compiler_dependencies_sha256'])
        optional.write_text('// newly discovered dependency, absent from public source glob\n')
        with self.assertRaisesRegex(ValueError,'stale'):
            build.verify_probe_build(self.private/'receipt.json',self.private/'probe',self.root)

    def test_dependency_scan_omits_link_only_flags_but_preserves_compile_warnings(self):
        dependencies=build.compiler_dependencies([self.compiler,'-Werror','-static-libstdc++',
            '-fuse-ld=lld','local-analysis/probe.cpp','-o','local-analysis/probe'],self.root)
        self.assertIn(str(self.source),dependencies)


if __name__=='__main__':unittest.main()
