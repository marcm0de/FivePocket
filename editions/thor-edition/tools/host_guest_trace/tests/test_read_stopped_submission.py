import copy
import json
from pathlib import Path
import struct
import sys
import tempfile
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import read_stopped_submission as reader
import stopped_pm4_artifacts as legacy


class StoppedSubmissionTests(unittest.TestCase):
    def fixture(self,folder,words=1):
        root=struct.pack('>III',0xC0013F00,0x10000,words)
        child=struct.pack('>I',0x80000000)*words
        (folder/'node-0.bin').write_bytes(root);(folder/'node-1.bin').write_bytes(child)
        m=dict(version=1,writers_stopped=True,executed=False,guest_memory_changed=False,ring_advanced=False,
            read_word=97,write_word=100,total_words=3+words,total_packets=1+words,dispatch={},nodes=[
                dict(node=0,path='node-0.bin',word_count=3,packet_count=1),
                dict(node=1,path='node-1.bin',word_count=words,packet_count=words,parent=0,parent_offset=0,
                    physical_address=0x10000,depth=1)])
        self.save(folder,m);return m
    def save(self,folder,m):(folder/'manifest.json').write_text(json.dumps(m))
    def chunked(self,folder,words):
        m=self.fixture(folder,words);m['version']=2
        (folder/'dispatch.json').write_text(json.dumps(m['dispatch']))
        for node in m['nodes']:
            node['chunk_bytes']=65536;path=folder/node['path'];data=path.read_bytes()
            if len(data)>65536:
                path.unlink()
                for part,start in enumerate(range(0,len(data),65536)):
                    (folder/f"node-{node['node']}-part-{part}.bin").write_bytes(data[start:start+65536])
        self.save(folder,m);return m
    def test_chunked_v2_exact_boundary_and_large_graph(self):
        for words in (1,16384,16385,22320,32768):
            with self.subTest(words=words),tempfile.TemporaryDirectory() as path:
                folder=Path(path);m=self.chunked(folder,words)
                actual,d,c,h=reader.load_capture(folder)
                self.assertEqual(actual,m);self.assertEqual(len(d[1][0]),words)
                self.assertEqual(len(list(reader.ordered_packets(d,c))),words)
                self.assertTrue(all(p.stat().st_size<=65536 for p in folder.iterdir()))
                self.assertIn('dispatch.json',h)
    def test_chunked_v2_missing_short_linked_and_wrong_chunk_contract(self):
        with tempfile.TemporaryDirectory() as path:
            folder=Path(path);m=self.chunked(folder,22320);part=folder/'node-1-part-1.bin';raw=part.read_bytes()
            part.unlink()
            with self.assertRaises(ValueError):reader.load_capture(folder)
            part.write_bytes(raw[:-4])
            with self.assertRaises(ValueError):reader.load_capture(folder)
            part.unlink();(folder/'linked-payload.bin').write_bytes(raw);part.symlink_to(folder/'linked-payload.bin')
            with self.assertRaises(ValueError):reader.load_capture(folder)
            part.unlink();part.write_bytes(raw)
            for value in (True,0,65532,65540,1048576):
                m['nodes'][1]['chunk_bytes']=value;self.save(folder,m)
                with self.assertRaises(ValueError):reader.load_capture(folder)
    def test_chunked_v2_requires_matching_dispatch_and_completion_manifest(self):
        with tempfile.TemporaryDirectory() as path:
            folder=Path(path);m=self.chunked(folder,1)
            (folder/'dispatch.json').write_text('{"accepted":true}')
            with self.assertRaises(ValueError):reader.load_capture(folder)
            (folder/'dispatch.json').write_text(json.dumps(m['dispatch']))
            (folder/'manifest.json').unlink()
            with self.assertRaises(ValueError):reader.load_capture(folder)
    def test_large_capture_preserves_legacy_protocol_limit(self):
        with tempfile.TemporaryDirectory() as path:
            folder=Path(path);m=self.fixture(folder,22320)
            actual,decoded,children,hashes=reader.load_capture(folder)
            self.assertEqual(actual,m);self.assertEqual(len(list(reader.ordered_packets(decoded,children))),22320)
            self.assertEqual(len(hashes),3)
            with self.assertRaises(ValueError):legacy.decode_packets((folder/'node-1.bin').read_bytes())
    def test_protocol_binds_exact_root_child_and_stop(self):
        with tempfile.TemporaryDirectory() as path:
            folder=Path(path);self.fixture(folder)
            graph=[0,0,0,0,0,0,4,2,[[0,0,65536,1,1,1,[[0,2,0,0,0,0,0,0]]]]]
            fields=dict(stop_reason='unsupported-scalar-access',graphics_ring_read_word='97',scalar_value='100',
                gpu_submission='5,0,0,0,97,100,3,1',gpu_indirect_graph=json.dumps(graph))
            protocol=folder/'protocol.txt'
            def save(values):protocol.write_text(''.join(k+'='+v+'\n' for k,v in values.items()))
            save(fields);reader.load_capture(folder,protocol)
            for key,value in [('scalar_value','101'),('graphics_ring_read_word','94'),('stop_reason','completed')]:
                bad=dict(fields);bad[key]=value;save(bad)
                with self.assertRaises(ValueError):reader.load_capture(folder,protocol)
            save(fields);protocol.write_text(protocol.read_text()+'scalar_value=100\n')
            with self.assertRaises(ValueError):reader.load_capture(folder,protocol)
    def test_graph_tampering_and_guest_execution_claims_rejected(self):
        with tempfile.TemporaryDirectory() as path:
            folder=Path(path);original=self.fixture(folder)
            changes=[lambda m:m.update(executed=True),lambda m:m.update(ring_advanced=True),
                lambda m:m.update(writers_stopped=False),lambda m:m.update(total_words=5),
                lambda m:m['nodes'][1].update(parent=1),lambda m:m['nodes'][1].update(depth=8),
                lambda m:m['nodes'][1].update(physical_address=0x11000),
                lambda m:m['nodes'][1].update(path='../node-1.bin'),lambda m:m['nodes'][1].update(word_count=True),
                lambda m:m['nodes'].pop(),lambda m:m['nodes'].append(copy.deepcopy(m['nodes'][1]))]
            for change in changes:
                m=copy.deepcopy(original);change(m);self.save(folder,m)
                with self.assertRaises(ValueError):reader.load_capture(folder)
            self.save(folder,original);(folder/'node-1.bin').write_bytes(b'wrong-size')
            with self.assertRaises(ValueError):reader.load_capture(folder)
    def test_linked_files_and_invalid_budgets_rejected(self):
        with tempfile.TemporaryDirectory() as path:
            folder=Path(path);self.fixture(folder);payload=folder/'node-1.bin';payload.rename(folder/'payload.bin')
            payload.symlink_to(folder/'payload.bin')
            with self.assertRaises(ValueError):reader.load_capture(folder)
            linked=folder/'linked';linked.symlink_to(folder,target_is_directory=True)
            with self.assertRaises(ValueError):reader.load_capture(linked)
        for bound in (True,0,256*1024+1,-1):
            with self.assertRaises(ValueError):legacy.decode_packets(b'',max_capture_words=bound)


if __name__=='__main__':unittest.main()
