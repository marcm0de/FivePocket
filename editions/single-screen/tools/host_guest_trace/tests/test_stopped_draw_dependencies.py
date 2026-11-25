from copy import deepcopy
import hashlib
from pathlib import Path
import struct
import sys
import unittest
sys.path[:0]=[str(Path(__file__).resolve().parents[1]),
    str(Path(__file__).resolve().parents[2]/'rpf7_inventory/tests')]
from stopped_draw_dependencies import analyze_requests
from stopped_pm4_artifacts import decode_packets
from test_vertex_arithmetic_dataflow import simple,math_alu
from test_pixel_sample_dataflow import pixel


def physical(address,data):
    return ({'physical_address':address,'byte_count':len(data),'sha256':hashlib.sha256(data).hexdigest(),
             'path':'fixture.bin'},data)


def fixture(extra=(),suffix=(),external=False):
    words=[];shaders=[]
    for stage,code in enumerate((simple(),pixel([math_alu(op=1,dest=0,sources=(0,0,0),temps=(True,False,True))]))):
        size=len(code)//4;offset=len(words)
        if external:words.extend((0xC0012700,0x10000+stage*0x100+stage,size))
        else:words.extend((0xC0002B00|((size+1)<<16),stage,size,*struct.unpack('>'+str(size)+'I',code)))
        shaders.append(({'node':0,'offset':offset,'stage':stage,'immediate':not external,
            'physical_address':0x10000+stage*0x100 if external else 0,'instruction_words':size,
            'sha256':hashlib.sha256(code).hexdigest(),'path':f'shader-{stage}.bin'},code))
    words.extend((0x2180,0x10110303,0x2181,8,0x2208,4,
        0x14800,0x2003,0x10000042,  # slot0 vertex descriptor,64 bytes
        0x54000,1,2,3,4,5,6,  # six words; c1.zw deliberately unknown
        0xC0022F00,0x3000,0x400,4,
        *extra,0xC0012201,0,0x40086,*suffix))
    raw=struct.pack('>'+str(len(words))+'I',*words);data,packets=decode_packets(raw)
    return ([(0,p,data[p[0]+1:p[0]+p[2]+1]) for p in packets],shaders,
            [physical(0x1FF0,bytes(96)),physical(0x3000,struct.pack('>4I',9,8,7,6))])


class DrawDependencyTests(unittest.TestCase):
    def analyze(self,*args,**kwargs):return analyze_requests(*fixture(*args,**kwargs))

    def test_unresolved_predicate_and_subrange_without_execution(self):
        result=self.analyze();draw=result['draws'][0]
        self.assertTrue(draw['predicate_requested']);self.assertIsNone(draw['predicate_result'])
        self.assertFalse(draw['binding_verified']);self.assertFalse(result['guest_commands_executed'])
        stages=draw['last_shader_load_candidates'];resource=stages['0']['resource_candidates'][0]
        self.assertEqual(resource['snapshot']['offset_bytes'],16)
        self.assertEqual(resource['snapshot']['byte_count'],64)
        self.assertFalse(resource['snapshot']['gpu_time_contents_verified'])
        self.assertEqual(resource['layout']['reserved_bits'],0x10000000)
        vc=stages['0']['constant_candidates']
        self.assertEqual([v['value_bits'] for v in vc[0]['words']],[1,2,3,4])
        self.assertEqual([v['fully_known'] for v in vc[1]['words']],[True,True,False,False])
        pc=stages['1']['constant_candidates'][0]['words']
        self.assertEqual([v['value_bits'] for v in pc],[9,8,7,6])
        self.assertEqual(pc[0]['source']['kind'],'stopped-constant-load-source')
        self.assertIsNone(draw['bin_predicate_reference']['reference_result'])

    def test_bin_candidates_obey_command_order_without_executing_draws(self):
        result=self.analyze(extra=(0xC0006000,0,0xC0006100,0),suffix=(
            0xC0006000,1,0xC0006200,1,0xC0003601,0x30088))
        first,second=result['draws']
        self.assertFalse(first['bin_predicate_reference']['reference_result'])
        self.assertTrue(second['bin_predicate_reference']['reference_result'])
        self.assertEqual(first['bin_predicate_reference']['words'],[0,0,None,None])
        for draw in (first,second):
            self.assertIsNone(draw['predicate_result']);self.assertFalse(draw['executed'])
        for extra in ((0xC0016000,1,2),(0xC0006001,0)):
            with self.assertRaises(ValueError):self.analyze(extra=extra)

    def test_missing_load_source_erases_old_literal_knowledge(self):
        packets,shaders,ranges=fixture(extra=(0xC0022F00,0x4000,0x400,4))
        r=analyze_requests(packets,shaders,ranges)['draws'][0]
        self.assertTrue(all(not v['fully_known'] for v in r['last_shader_load_candidates']['1']['constant_candidates'][0]['words']))

    def test_sequential_draw_snapshots_are_not_mutated(self):
        r=self.analyze(suffix=(0x2180,7,0xC0003600,0x30088))
        self.assertEqual([d['register_candidates'][str(0x2180)]['value_bits'] for d in r['draws']],[0x10110303,7])
        self.assertEqual([d['predicate_requested'] for d in r['draws']],[True,False])
        self.assertIn('draw-not-executed',[o['kind'] for o in r['draws'][1]['prefix_obligations']])

    def test_unknown_commands_and_conditional_writes_reject(self):
        for extra in ((0xC0007F00,0),(0xC0022F01,0x3000,0x400,4),(0xC0003B01,0),(0xC0001001,0)):
            with self.subTest(extra=extra),self.assertRaises(ValueError):self.analyze(extra=extra)

    def test_waits_invalidations_and_events_remain_obligations(self):
        r=self.analyze(extra=(0xC0003B00,0x300,0xC0043C00,3,0xA31,0,0x80000000,8),
            suffix=(0xC0004600,6,0xC0025800,3,0x4002,42))
        kinds=[o['kind'] for o in r['remaining_obligations']]
        for k in ('invalidation-not-executed','wait-not-evaluated','event-not-executed','timestamp-not-written'):
            self.assertIn(k,kinds)
        flush,write=r['remaining_obligations'][-2:]
        self.assertTrue(flush['event_reference']['native_resource_flush_required'])
        self.assertEqual(write['event_reference']['value_source'],'packet-literal')
        self.assertEqual(write['event_reference']['literal_write_candidate']['physical_address'],0x4000)
        self.assertEqual([o['kind'] for o in write['preceding_ordering_requests']],
            ['wait-not-evaluated','event-not-executed'])
        self.assertEqual(write['preceding_draw_requests'],[dict(node=0,offset=r['draws'][0]['offset'])])

    def test_external_metadata_is_checked_against_packet(self):
        self.analyze(external=True)
        for field,value in (('physical_address',0x4000),('stage',1),('immediate',True),('instruction_words',3)):
            p,s,r=fixture(external=True);s=deepcopy(s);s[0][0][field]=value
            with self.subTest(field=field),self.assertRaises(ValueError):analyze_requests(p,s,r)

    def test_coherency_wait_keeps_ordered_candidate_provenance(self):
        result=self.analyze(suffix=(0x200D,0,0xA31,0x10000,0x10A2F,0,0x1000,
            0xC0043C00,3,0xA31,0,0x80000000,8,0x200D,0x4000,0xC0003600,0x30088))
        wait=[x for x in result['remaining_obligations'] if x['kind']=='wait-not-evaluated'][0]
        self.assertEqual(wait['preceding_draw_requests'],[dict(node=0,offset=result['draws'][0]['offset'])])
        self.assertIsNotNone(wait['coherency_register_sources'][str(0x200D)])
        selected=wait['coherency_reference']['selected_destination_candidates'][-1]
        self.assertEqual(selected['base_candidate']['value_bits'],0)
        self.assertEqual(result['draws'][-1]['register_candidates'][str(0x200D)]['value_bits'],0x4000)
        self.assertFalse(wait['coherency_reference']['runtime_supported'])

    def test_snapshot_hash_duplicates_and_overlap_reject(self):
        p,s,r=fixture()
        altered=deepcopy(s);altered[0][0]['sha256']='0'*64
        for shaders,ranges in ((altered,r),(s+s[:1],r),(s,r+r[:1]),(s,r+[physical(0x2000,bytes(16))])):
            with self.assertRaises(ValueError):analyze_requests(p,shaders,ranges)
        altered=deepcopy(r);altered[0][0]['sha256']='0'*64
        with self.assertRaises(ValueError):analyze_requests(p,s,altered)

    def test_malformed_constant_loads_reject(self):
        for source,selector,size in ((0x3001,0x400,4),(0x3000,0x10400,4),(0x3000,2047,2),
                (0x1FFFFFFC,0,2),(0x3000,0,0),(0x3000,0,4096)):
            with self.subTest(source=source,selector=selector,size=size),self.assertRaises(ValueError):
                self.analyze(extra=(0xC0022F00,source,selector,size))

    def test_invalid_draw_fields_reject_and_no_partial_result(self):
        for word in (0,0x30008,0x30888,0x31088,0x10010088):
            with self.subTest(word=word),self.assertRaises(ValueError):self.analyze(suffix=(0xC0003600,word))
        with self.assertRaises(ValueError):self.analyze(suffix=(0xC0022200,0,0x30088,1))

    def test_packet_framing_bounds_and_repeated_register(self):
        r=self.analyze(extra=(0x29880,1,2,3))  # repeat bit, three writes to0x1880
        self.assertFalse(r['guest_commands_executed'])
        p,s,r=fixture()
        for packets in (p+p[:1],[(n,q[:-1],v) for n,q,v in p],[(n,q,v[:-1]) for n,q,v in p]):
            with self.assertRaises(ValueError):analyze_requests(packets,s,r)


if __name__=='__main__':unittest.main()
