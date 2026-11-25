"""Build reuse only: exact dependencies, no legacy blessing or device reuse."""
import copy
import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import build_loading_probe as build
from probe_reuse_plan import select_verified_prior, plan_probe_binding
from run_loading_test_batch import snapshot
from test_loading_test_contract import ProbeBuildTests


class DependencyReceiptTests(ProbeBuildTests):
    def requested(self):
        spec=copy.deepcopy(self.spec);spec['output']='local-analysis/next-output'
        spec['command'][spec['command'].index('-o')+1]=str(self.root/spec['output'])
        return spec

    def select(self,spec=None):
        return select_verified_prior(spec or self.requested(),self.private/'receipt.json',self.root)

    def save_spec(self):self.manifest.write_text(json.dumps(self.spec))

    def test_output_rename_retains_exact_old_paths_without_compilation_or_edits(self):
        self.assertEqual(self.run_build(),0)
        before={p:p.read_bytes() for p in (self.private/'receipt.json',self.private/'probe')}
        with patch.object(build,'run_command') as run:
            self.assertEqual(self.select(),dict(output='local-analysis/probe',receipt='local-analysis/receipt.json'))
            run.assert_not_called()
        self.assertTrue(all(p.read_bytes()==v for p,v in before.items()))
        self.assertFalse((self.root/'local-analysis/next-output').exists())

    def test_unrelated_source_reuses_build_but_invalidates_full_regression(self):
        other=self.root/'android/tests/unrelated.cpp';other.parent.mkdir(parents=True)
        other.write_text('// unrelated A\n');before=snapshot(self.root)
        self.assertEqual(self.run_build(),0);other.write_text('// unrelated B\n')
        self.assertNotEqual(before,snapshot(self.root));self.assertIsNotNone(self.select())
        with patch.object(build,'run_command') as run:
            self.assertEqual(self.run_build('reused','receipt'),0);run.assert_not_called()

    def test_nested_header_change_and_optional_appearance_refuse(self):
        nested=self.private/'nested.h';nested.write_text('#define NESTED 1\n')
        optional=self.private/'optional.h'
        self.header.write_text('#include "'+str(nested)+'"\n#if __has_include("'+str(optional)+'")\n#include "'+str(optional)+'"\n#endif\n')
        self.assertEqual(self.run_build(),0);self.assertIsNotNone(self.select())
        original=nested.read_text();nested.write_text('#define NESTED 2\n');self.assertIsNone(self.select())
        nested.write_text(original);optional.write_text('#define OPTIONAL 1\n');self.assertIsNone(self.select())
        optional.unlink();self.assertIsNotNone(self.select())

    def test_receipt_command_cannot_be_edited_to_claim_new_flags(self):
        self.assertEqual(self.run_build(),0);p=self.private/'receipt.json';r=json.loads(p.read_text())
        r['build']['command'].insert(1,'-DUNUSED_CHANGED_OPTION=1');p.write_text(json.dumps(r))
        with self.assertRaisesRegex(ValueError,'stale'):build.verify_probe_build(p,self.private/'probe',self.root)

    def test_changed_request_flags_and_input_declarations_refuse(self):
        self.assertEqual(self.run_build(),0);old=(self.private/'probe').read_bytes()
        requested=self.requested();requested['command'].insert(1,'-DNEW_MODE=1');self.assertIsNone(self.select(requested))
        requested=self.requested();requested['additional_inputs'].append('local-analysis/new-config.json');self.assertIsNone(self.select(requested))
        self.spec['command'].insert(1,'-DNEW_MODE=1');self.save_spec()
        self.assertEqual(self.run_build('changed','receipt'),1);self.assertEqual(old,(self.private/'probe').read_bytes())

    def test_private_pack_link_and_toolchain_changes_refuse(self):
        pack=self.private/'shader-pack.bin';pack.write_bytes(b'authored pack')
        toolchain=self.private/'link-configuration.txt';toolchain.write_text('authored toolchain input')
        source=self.private/'linked.cpp';source.write_text('int fixture_linked_global=1;\n')
        obj=self.private/'linked.o';subprocess.run([self.compiler,'-c',str(source),'-o',str(obj)],check=True)
        self.spec['command'].insert(1,str(obj));self.spec['additional_inputs'] += [str(pack.relative_to(self.root)),str(obj.relative_to(self.root))]
        self.spec['toolchain_inputs']=[str(toolchain)];self.save_spec();self.assertEqual(self.run_build(),0)
        for p in (pack,obj,toolchain):
            with self.subTest(path=p.name):
                old=p.read_bytes();p.write_bytes(old+b'changed')
                self.assertIsNone(self.select());p.write_bytes(old)

    def test_build_tool_implementation_and_compiler_identity_changes_refuse(self):
        self.assertEqual(self.run_build(),0);changed=build.build_tool_hashes()
        changed[str(Path(build.__file__).resolve())]='0'*64
        with patch.object(build,'build_tool_hashes',return_value=changed):self.assertIsNone(self.select())
        runtime=copy.deepcopy(build.runtime_snapshot(dict(steps=[dict(command=self.spec['command'])]),self.root))
        runtime['tools'][self.compiler]['sha256']='0'*64
        with patch.object(build,'runtime_snapshot',return_value=runtime):self.assertIsNone(self.select())

    def test_legacy_receipt_rejected_not_upgraded(self):
        self.assertEqual(self.run_build(),0);p=self.private/'receipt.json';r=json.loads(p.read_text())
        r['schema']=1;p.write_text(json.dumps(r));old=p.read_bytes()
        with self.assertRaisesRegex(ValueError,'rebuild legacy'):build.verify_probe_build(p,self.private/'probe',self.root)
        self.assertIsNone(self.select());self.assertEqual(self.run_build('legacy','receipt'),1)
        self.assertEqual(old,p.read_bytes())

    def test_missing_or_tampered_log_and_binary_refuse(self):
        self.assertEqual(self.run_build(),0);p=self.private/'receipt.log';old=p.read_bytes()
        p.write_bytes(old+b'changed');self.assertIsNone(self.select());p.write_bytes(old)
        binary=self.private/'probe';prior=binary.read_bytes();binary.write_bytes(prior+b'changed');self.assertIsNone(self.select())
        binary.write_bytes(prior);p.unlink();self.assertIsNone(self.select())

    def test_missing_or_malformed_receipt_refuses_without_creating_output(self):
        self.assertIsNone(self.select());p=self.private/'receipt.json';p.write_text('{}')
        self.assertIsNone(self.select());self.assertFalse((self.private/'probe').exists())

    def planned(self):
        return plan_probe_binding(self.requested(),self.private/'receipt.json',self.root,
            self.private/'next-output',self.private/'next-receipt.json')

    def test_packaging_retains_exact_valid_build_without_map_changes(self):
        self.assertEqual(self.run_build(),0);plan=self.planned()
        self.assertTrue(plan['reused_build']);self.assertIsNone(plan['build'])
        self.assertEqual(plan['binding'],dict(output='local-analysis/probe',receipt='local-analysis/receipt.json'))

    def test_packaging_changed_dependency_plans_real_new_build(self):
        self.assertEqual(self.run_build(),0);self.header.write_text('// changed real dependency\n')
        plan=self.planned();self.assertFalse(plan['reused_build'])
        self.assertEqual(plan['build']['additional_inputs'],self.spec['additional_inputs'])
        self.assertEqual(plan['binding'],dict(output='local-analysis/next-output',receipt='local-analysis/next-receipt.json'))
        self.assertFalse((self.private/'next-output').exists())
        build.build_context(plan['build'],self.root) # New build plan is independently valid.

    def test_packaging_never_overwrites_unknown_binary_or_receipt(self):
        (self.private/'next-output').write_bytes(b'unknown')
        with self.assertRaisesRegex(ValueError,'preserve existing'):self.planned()
        self.assertEqual((self.private/'next-output').read_bytes(),b'unknown')


if __name__=='__main__':unittest.main()
