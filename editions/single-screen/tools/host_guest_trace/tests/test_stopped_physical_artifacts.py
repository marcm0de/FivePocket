import json
from pathlib import Path
import stat
import struct
import sys
import tempfile
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import stopped_physical_artifacts as capture


class PhysicalCaptureTests(unittest.TestCase):
    def setUp(self):
        self.ranges=[[4096,12],[8192,16]]
        self.raw=json.dumps(self.ranges)
        self.records=['4096,12,'+bytes(range(12)).hex(),'8192,16,'+bytes(range(16)).hex()]

    def test_bounds_alignment_aggregate_overlap(self):
        for ranges in ([],[[0,0]],[[1,4]],[[0,3]],[[0,4100]],[[0x1FFFFFFC,8]],
                       [[True,4]],[[0,4],[0,4]],[[0,8],[4,4]],[[0,4],[4096,4096]],
                       [[i*4,4] for i in range(9)],[[0,4.0]],[[0,-4]],[[0x20000000,4]]):
            with self.assertRaises(ValueError):capture.validate_ranges(ranges)
        for ranges in (self.ranges,[[0,4096]],[[0x1FFFFFFC,4]],[[0,4],[4,4]]):
            capture.validate_ranges(ranges)

    def test_stopped_swap_request_is_not_a_submitted_graph_or_image_publication(self):
        request=dict(captured=True,metadata_only=True,pixels_decoded=False,commands_written=False,
            frame_presented=False,next_image_published=False,next_swap_completed=False,
            command_virtual=0xA793B130,command_physical=0x0793B130,allocation_base=0xA7900000,
            allocation_bytes=0x700000,protection=0x20000004,frontbuffer_virtual=0xA71D0000,format=6,
            color_space=0,width=1280,height=720,fetch_words=[0x8A000002,0xA71D0006,0x0059E4FF,0x1414,0,0x200])
        ranges=[[0x07937130,16384]];records=[f'{a},{n},'+bytes(n).hex() for a,n in ranges]
        result=capture.parse_capture('1',json.dumps(ranges),records,None,'16384',post_swap_request=request)
        self.assertEqual(result['stopped_swap_request'],request)
        command_owner=[0x07900000,0xA7900000,0x700000,0x20000004,1]
        self.assertEqual(capture.parse_ownership(json.dumps([command_owner]),result)[0]['byte_count'],0x700000)
        for row in (None,[0x07900000,0xB7900000,0x700000,0x20000004,1],
                [0x07900000,0xA7900000,0x710000,0x20000004,1],
                [0x07900000,0xA7900000,0x700000,0x40000004,1]):
            with self.assertRaises(ValueError):capture.parse_ownership(json.dumps([row]),result)
        with self.assertRaises(ValueError):capture.parse_ownership(None,result)
        with tempfile.TemporaryDirectory() as temp:
            saved=capture.save_capture(Path(temp),result)
            self.assertEqual(saved['stopped_swap_request'],request);self.assertFalse(saved['executed'])
        for bad in ([[0x078FFFFC,4]],[[0x0793B230,4]],[[0x0793B22C,8]],[[0x071D0000,4]],[[0x1F670040,4]]):
            raw=[f'{a},{n},'+bytes(n).hex() for a,n in bad]
            with self.assertRaises(ValueError):capture.parse_capture('1',json.dumps(bad),raw,None,post_swap_request=request)
        for missing in (None,dict(captured=False)):
            with self.assertRaises(ValueError):capture.parse_capture('1',json.dumps(ranges),records,None,'16384',post_swap_request=missing)
        with self.assertRaises(ValueError):capture.parse_capture('1',json.dumps(ranges),records,dict(failure=7),'16384',post_swap_request=request)
        for key in ('pixels_decoded','commands_written','frame_presented','next_image_published','next_swap_completed'):
            with self.assertRaises(ValueError):capture.post_swap_request_ranges(dict(request,**{key:True}))
        for key,value in [('command_virtual',0xA793B131),('command_physical',0x1000),('allocation_base',0xA793B134),
                ('allocation_bytes',4),('allocation_bytes',0x60000000),('allocation_bytes',True),('metadata_only',False)]:
            with self.assertRaises(ValueError):capture.post_swap_request_ranges(dict(request,**{key:value}))
        queries=[[0x071D0000,3768320],[0x07900000,0x700000]]
        owners=[[0x071D0000,0xA71D0000,3768320,0x20000004,1],[0x07900000,0xA7900000,0x700000,0x20000004,1]]
        parsed=capture.parse_ownership_queries('1',json.dumps(queries),json.dumps(owners),None,request)
        self.assertFalse(parsed['payload_read']);self.assertEqual(parsed['stopped_swap_request'],request)
        for row in ([0x071D0000,0xB71D0000,3768320,0x20000004,1],
                [0x071D0000,0xA71D0000,0x1000000,0x20000004,1]):
            with self.assertRaises(ValueError):capture.parse_ownership_queries('1',json.dumps(queries),json.dumps([row,owners[1]]),None,request)
        absent=capture.parse_ownership_queries('1',json.dumps(queries),'[null,null]',None,request)
        self.assertEqual(absent['owners'],[None,None]) # Metadata may report absence, never invent an owner.
        for bad in ([[0x071CFFFC,4]],[[0x071D0000,3768324]],[[0x1F670040,4]],[[0x07900000,0x700004]]):
            with self.assertRaises(ValueError):capture.parse_ownership_queries('1',json.dumps(bad),'[null]',None,request)
        for key,value in [('format',7),('width',0),('fetch_words',[0]*6),('frontbuffer_virtual',0xA71D0001)]:
            with self.assertRaises(ValueError):capture.parse_ownership_queries('1',json.dumps(queries),'[null,null]',None,dict(request,**{key:value}))
        self.assertEqual(capture.parse_capture('0',json.dumps(ranges),[],None,'16384')['status'],0)

    def test_native_pending_wrapper_is_separate_bounded_read_only_provenance(self):
        fields=[1,0x1000+40*4,0x2000,80,10,40,43]
        submission=dict(status=5,read_word=40,write_word=43,word_count=3,packet_count=1,packets=[[0,3,2,63,0,0,0,0]])
        graph=dict(failure=7,error_parent=0,error_offset=0,decode_failure=4,decode_opcode=0x53,decode_offset=10)
        ring=dict(ring_physical=0x1000,ring_bytes=0x10000,ring_read_word=40)
        swap=dict(pending=True,present_queued=False,read_word=40,command_physical=0x2028)
        coherency=dict(completed_batches=1,read_word=40,physical_base=0x2000,byte_count=0x4000)
        def parse(raw=None,**overrides):
            args=dict(submission=submission,graph=graph,ring=ring,swap=swap,coherency=coherency);args.update(overrides)
            return capture.parse_native_pending_submission(','.join(map(str,fields)) if raw is None else raw,**args)
        good=parse();self.assertTrue(good['captured']);self.assertFalse(good['retail_graph_valid'])
        self.assertFalse(good['executed']);self.assertFalse(good['guest_memory_changed'])
        self.assertFalse(parse('0,0,0,0,0,0,0')['captured'])
        for raw in ('','1','0,0,0,0,0,0,1','true,0,0,0,0,0,0','01,4256,8192,80,10,40,43'):
            with self.assertRaises(ValueError):parse(raw)
        for index in (0,1,2,4,5,6):
            for bit in range(32):
                bad=fields.copy();bad[index]^=1<<bit
                with self.assertRaises(ValueError):parse(','.join(map(str,bad)))
        for count in (0,63,64,73,1025,0xFFFFFFFF):
            bad=fields.copy();bad[3]=count
            with self.assertRaises(ValueError):parse(','.join(map(str,bad)))
        for key in ('submission','graph','ring','swap','coherency'):
            with self.assertRaises(ValueError):parse(**{key:None})
        for changes in (dict(failure=0),dict(error_parent=1),dict(error_offset=1),dict(decode_failure=6),
                        dict(decode_opcode=0),dict(decode_offset=9)):
            with self.assertRaises(ValueError):parse(graph=dict(graph,**changes))
        for changes in (dict(word_count=4),dict(packet_count=2),dict(packets=[[0,3,2,63,0,0,1,0]])):
            with self.assertRaises(ValueError):parse(submission=dict(submission,**changes))
        with self.assertRaises(ValueError):parse(swap=dict(swap,pending=False,present_queued=True))
        ranges=[[fields[1],12],[fields[2],320]]
        records=[f'{a},{n},'+bytes(n).hex() for a,n in ranges]
        with self.assertRaises(ValueError):capture.parse_capture('1',json.dumps(ranges),records,graph)
        result=capture.parse_capture('1',json.dumps(ranges),records,graph,native_pending=good)
        self.assertEqual(result['native_pending_submission'],good)
        for bad_ranges,budget in (([[fields[1]-4,12]],None),([[fields[2],324]],None),([[0x3000,4]],None),(ranges,'8192')):
            bad_records=[f'{a},{n},'+bytes(n).hex() for a,n in bad_ranges]
            with self.assertRaises(ValueError):capture.parse_capture('1',json.dumps(bad_ranges),bad_records,graph,budget,good)
        with tempfile.TemporaryDirectory() as temp:
            saved=capture.save_capture(Path(temp),result)
            self.assertFalse(saved['executed']);self.assertEqual(saved['native_pending_submission'],good)

    def test_deferred_submission_capture_is_owned_but_not_executable_pm4(self):
        request=dict(captured=True,metadata_only=True,pixels_decoded=False,commands_written=False,
            frame_presented=False,next_image_published=False,next_swap_completed=False,
            command_virtual=0xA793B130,command_physical=0x0793B130,allocation_base=0xA7900000,
            allocation_bytes=0x700000,protection=0x20000404)
        fields=[1,0x1F670040+76*4,0x079385A0,3000,2788,76,79]
        submission=dict(status=5,read_word=76,write_word=79,word_count=3,packet_count=1,packets=[[0,3,2,63,0,0,0,0]])
        graph=dict(failure=7,error_parent=0,error_offset=0,decode_failure=4,decode_opcode=0x53,decode_offset=2788)
        ring=dict(ring_physical=0x1F670040,ring_bytes=65536,ring_read_word=76)
        swap=dict(pending=False,present_queued=True,read_word=43,command_physical=0x07902C68)
        coherency=dict(completed_batches=1,read_word=40,physical_base=0x07900000,byte_count=0x700000)
        deferred=dict(issued=True,pending=True,ticket=3,issued_read_word=73,request=request,image_physical=0x071D0000)
        storage=dict(completed_batches=1,previous_word=73,read_word=76,physical_base=0x07900000,byte_count=0x700000)
        def parse(raw=None,**overrides):
            args=dict(submission=submission,graph=graph,ring=ring,swap=swap,coherency=coherency,
                deferred=deferred,deferred_storage=storage);args.update(overrides)
            return capture.parse_native_pending_submission(','.join(map(str,fields)) if raw is None else raw,**args)
        good=parse();self.assertEqual(good['scope'],'stopped-pending-native-deferred-swap')
        self.assertEqual(good['deferred_request'],request);self.assertFalse(good['retail_graph_valid'])
        for key in ('executed','guest_memory_changed'):self.assertFalse(good[key])
        for index in (0,1,2,4,5,6):
            for bit in range(32):
                bad=fields.copy();bad[index]^=1<<bit
                with self.assertRaises(ValueError):parse(','.join(map(str,bad)))
        for words in (0,63,64,2851,0x200001,0xFFFFFFFF):
            bad=fields.copy();bad[3]=words
            with self.assertRaises(ValueError):parse(','.join(map(str,bad)))
        for key in ('deferred','deferred_storage','submission','graph','ring','swap','coherency'):
            with self.assertRaises(ValueError):parse(**{key:None})
        for change in (dict(issued=False),dict(pending=False),dict(ticket=2),dict(issued_read_word=76)):
            with self.assertRaises(ValueError):parse(deferred=dict(deferred,**change))
        for change in (dict(read_word=73),dict(previous_word=70),dict(byte_count=0x4000),dict(completed_batches=2)):
            with self.assertRaises(ValueError):parse(deferred_storage=dict(storage,**change))
        ranges=[[fields[1],12],[fields[2],12000]]
        records=[f'{a},{n},'+bytes(n).hex() for a,n in ranges]
        result=capture.parse_capture('1',json.dumps(ranges),records,graph,'16384',good)
        self.assertEqual(result['native_pending_submission'],good)
        owners=[[0x1F670000,0xBF670000,0x11000,0x20000404,1],
            [0x07900000,0xA7900000,0x700000,0x20000404,1]]
        self.assertEqual(len(capture.parse_ownership(json.dumps(owners),result)),2)
        with self.assertRaises(ValueError):capture.parse_ownership(None,result)
        for row in (None,[0x07900000,0xB7900000,0x700000,0x20000404,1],
                [0x07900000,0xA7900000,0x710000,0x20000404,1]):
            with self.assertRaises(ValueError):capture.parse_ownership(json.dumps([owners[0],row]),result)
        for bad,budget in (([[fields[1]-4,12]],'16384'),([[fields[2],12004]],'16384'),
                ([[0x071D0000,4]],'16384'),(ranges,'32768')):
            blobs=[f'{a},{n},'+bytes(n).hex() for a,n in bad]
            with self.assertRaises(ValueError):capture.parse_capture('1',json.dumps(bad),blobs,graph,budget,good)
        with tempfile.TemporaryDirectory() as temp:
            saved=capture.save_capture(Path(temp),result)
            self.assertEqual(saved['native_pending_submission'],good);self.assertFalse(saved['executed'])
        envelope=struct.pack('>64I',*([0xFFFF5357,0x41500001,64,1,0,3,0x0793B130,0x071D0000,73,
            1280,720,3768320,3686400]+[0]*51))
        root=struct.pack('>III',0xC0013F00,fields[2],fields[3]);child=bytearray(12000)
        child[2788*4:(2788+64)*4]=envelope
        native_records=[f'{a},{len(data)},'+data.hex() for a,data in ((fields[1],root),(fields[2],child))]
        auto=lambda status='1',blobs=native_records,owner=json.dumps(owners),candidate=good,opt=True: capture.parse_native_pending_capture(
            status,blobs,owner,candidate,graph,deferred,opt)
        self.assertEqual(auto()['requests'],ranges)
        for status,blobs,owner,candidate,opt in ((None,native_records,json.dumps(owners),good,True),
                ('1',native_records,None,good,True),('1',native_records,json.dumps(owners),None,True),
                ('1',native_records,json.dumps(owners),good,False),('1',native_records*2,json.dumps(owners),good,True),
                ('0',native_records,json.dumps(owners),good,True),('2',native_records,json.dumps(owners),good,True)):
            with self.assertRaises(ValueError):auto(status,blobs,owner,candidate,opt)
        for index in range(2):
            at=0 if index==0 else 2788*4
            original=root if index==0 else child
            for offset in range(12 if index==0 else 256):
                bad=bytearray(original);bad[at+offset]^=1
                blobs=native_records.copy();blobs[index]=f'{ranges[index][0]},{len(bad)},'+bad.hex()
                with self.assertRaises(ValueError):auto(blobs=blobs)
        self.assertEqual(auto('0',[],'[]')['status'],0)
        self.assertIsNone(capture.parse_native_pending_capture(None,[],None,None,None,None,False))

    def test_native_scaler_wrapper_has_its_own_pending_provenance(self):
        fields=[1,0x1000+43*4,0x3000,140,69,43,46]
        submission=dict(status=5,read_word=43,write_word=46,word_count=3,packet_count=1,packets=[[0,3,2,63,0,0,0,0]])
        graph=dict(failure=7,error_parent=0,error_offset=0,decode_failure=4,decode_opcode=0x53,decode_offset=69)
        ring=dict(ring_physical=0x1000,ring_bytes=0x10000,ring_read_word=43)
        swap=dict(pending=False,present_queued=True,read_word=40,command_physical=0x2028)
        coherency=dict(completed_batches=1,read_word=37,physical_base=0x2000,byte_count=0x4000)
        scaler=dict(pending=True,issued_commands=1,previous_read_word=40)
        scaler_storage=dict(completed_batches=1,previous_word=40,read_word=43,command_physical=0x3114,
            physical_base=0x2000,byte_count=0x4000)
        def parse(raw=None,**overrides):
            args=dict(submission=submission,graph=graph,ring=ring,swap=swap,coherency=coherency,
                scaler=scaler,scaler_storage=scaler_storage);args.update(overrides)
            return capture.parse_native_pending_submission(','.join(map(str,fields)) if raw is None else raw,**args)
        good=parse();self.assertTrue(good['captured'])
        self.assertEqual(good['scope'],'stopped-pending-native-scaler-wrapper')
        self.assertFalse(good['executed']);self.assertFalse(good['retail_graph_valid'])
        for key in ('submission','graph','ring','swap','coherency','scaler','scaler_storage'):
            with self.assertRaises(ValueError):parse(**{key:None})
        for key in ('completed_batches','previous_word','read_word','command_physical','physical_base','byte_count'):
            with self.assertRaises(ValueError):parse(scaler_storage=dict(scaler_storage,**{key:scaler_storage[key]^1}))
        for key in ('pending','issued_commands','previous_read_word'):
            with self.assertRaises(ValueError):parse(scaler=dict(scaler,**{key:int(scaler[key])^1}))
        for index in (0,1,2,4,5,6):
            for bit in range(32):
                bad=fields.copy();bad[index]^=1<<bit
                with self.assertRaises(ValueError):parse(','.join(map(str,bad)))
        ranges=[[fields[1],12],[fields[2],140*4]]
        records=[f'{a},{n},'+bytes(n).hex() for a,n in ranges]
        result=capture.parse_capture('1',json.dumps(ranges),records,graph,native_pending=good)
        self.assertEqual(result['native_pending_submission'],good)
        with self.assertRaises(ValueError):
            capture.parse_capture('1',json.dumps([[fields[2]-4,4]]),[f'{fields[2]-4},4,00000000'],graph,native_pending=good)

    def test_scaler_plan_metadata_and_explicit_scratch_capture(self):
        pending=dict(captured=True,scope='stopped-pending-native-scaler-wrapper',root_physical=0x1000,
            child_physical=0x3000,child_words=145,command_offset_words=69)
        scaler=dict(pending=True)
        fields=[1,0x8000,0x82004000,0x1234,0x82008000,0x10002000,1,0x20033,1,0x8000]
        def parse(values=fields,p=pending,s=scaler):
            return capture.parse_scaler_wrapper_plan(','.join(map(str,values)),p,s)
        good=parse();self.assertTrue(good['captured']);self.assertTrue(good['retained_scratch_matches'])
        self.assertEqual(good['operation_count'],26);self.assertEqual(good['interrupt_cpu_mask'],4)
        self.assertFalse(good['callback_dispatched']);self.assertFalse(good['executed']);self.assertFalse(good['guest_memory_changed'])
        self.assertFalse(parse([0]*10,p=None)['captured'])
        self.assertIsNone(capture.parse_scaler_wrapper_plan(None,None,None))
        for index in (0,6,8):
            bad=fields.copy();bad[index]=2
            with self.assertRaises(ValueError):parse(bad)
        for bad in ([0]*9,[0]*11,[0]+fields[1:],fields[:2]+[False]+fields[3:],fields[:-1]+[2**32]):
            with self.assertRaises(ValueError):parse(bad)
        for p in (None,dict(pending,captured=False),dict(pending,child_words=144),dict(pending,command_offset_words=68),
                  dict(pending,scope='stopped-pending-native-wrapper')):
            with self.assertRaises(ValueError):parse(p=p)
        for s in (None,dict(pending=False)):
            with self.assertRaises(ValueError):parse(s=s)
        for index,value in ((1,0x1FFFFFF0),(1,0x3000),(1,0x3000+579),(1,0x8001),(2,0),(2,0x82004001),
                            (4,0),(4,0x82008001),(6,0),(8,0),(9,0x1FFFFFF0),(9,0x8001)):
            bad=fields.copy();bad[index]=value
            with self.assertRaises(ValueError):parse(bad)
        # Unknown/mismatched retained state is useful negative evidence, never
        # silently filled with the parser's expected configuration.
        for indices in ((6,7),(8,9),(4,5)):
            bad=fields.copy()
            for index in indices:bad[index]=0
            result=parse(bad);self.assertTrue(result['captured'])
            if indices!=(4,5):self.assertFalse(result['retained_scratch_matches'])
        for index,bit in ((7,0),(7,16),(9,2)):
            bad=fields.copy();bad[index]^=1<<bit;self.assertFalse(parse(bad)['retained_scratch_matches'])
        for mask in range(256):
            bad=fields.copy();bad[7]=0x20000|mask
            self.assertEqual(parse(bad)['retained_scratch_matches'],mask&0x33==0x33)
        ranges=[[0x8000,32]];records=['32768,32,'+'00'*32];graph=dict(failure=7)
        result=capture.parse_capture('1',json.dumps(ranges),records,graph,native_pending=pending,scaler_plan=good)
        self.assertEqual(result['native_scaler_wrapper_plan'],good)
        with self.assertRaises(ValueError):capture.parse_capture('1',json.dumps(ranges),records,graph,native_pending=pending)
        for a,n in ((0x7FFC,4),(0x801C,8),(0x8020,4),(0x9000,4)):
            with self.assertRaises(ValueError):capture.parse_capture('1',json.dumps([[a,n]]),[f'{a},{n},'+'00'*n],graph,
                native_pending=pending,scaler_plan=good)
        with tempfile.TemporaryDirectory() as temp:
            saved=capture.save_capture(Path(temp),result);self.assertFalse(saved['executed'])
            self.assertEqual(saved['native_scaler_wrapper_plan'],good)

    def test_complete_origin_match_and_atomic_failure(self):
        result=capture.parse_capture('1',self.raw,self.records,{'failure':0})
        self.assertEqual(result['blobs'][0]['bytes'],bytes(range(12)))
        capture.require_requested(result,self.ranges)
        capture.require_requested(None,[])
        for ranges in ([],self.ranges[::-1],[[4096,16],[8192,12]]):
            with self.assertRaises(ValueError):capture.require_requested(result,ranges)
        for records in (self.records[:1],self.records[::-1],self.records*5,
                        [self.records[0].upper(),self.records[1]],
                        [self.records[0]+'00',self.records[1]],
                        [self.records[0].replace('4096','04096'),self.records[1]]):
            with self.assertRaises(ValueError):capture.parse_capture('1',self.raw,records,{'failure':0})
        for graph in (None,{'failure':1}):
            with self.assertRaises(ValueError):capture.parse_capture('1',self.raw,self.records,graph)
        for status in ('0','2'):
            self.assertEqual(capture.parse_capture(status,self.raw,[],None)['blobs'],[])
            with self.assertRaises(ValueError):capture.parse_capture(status,self.raw,self.records,None)

    def test_explicit_status_requests_and_private_files(self):
        self.assertIsNone(capture.parse_capture(None,None,[],None))
        for status,raw,records in ((None,self.raw,[]),('1',None,[]),('3',self.raw,[]),
                                   ('1','not-json',[]),('1','[]',[]),(None,None,self.records)):
            with self.assertRaises(ValueError):capture.parse_capture(status,raw,records,{'failure':0})
        result=capture.parse_capture('1',self.raw,self.records,{'failure':0})
        with tempfile.TemporaryDirectory() as temp:
            reports=[capture.save_capture(Path(temp),result) for _ in range(2)]
            self.assertNotEqual(reports[0]['artifacts'][0]['path'],reports[1]['artifacts'][0]['path'])
            for r in reports:
                self.assertFalse(r['executed']);self.assertFalse(r['resource_uploaded'])
                self.assertFalse(r['gpu_execution_time_contents_verified'])
                self.assertNotIn(bytes(range(12)).hex(),json.dumps(r))
                for a in r['artifacts']:
                    p=Path(temp)/a['path']
                    self.assertEqual(stat.S_IMODE(p.stat().st_mode),0o600)
                    self.assertEqual(stat.S_IMODE(p.parent.stat().st_mode),0o700)
                    self.assertEqual(p.stat().st_size,a['byte_count'])

    def test_expanded_budget_is_explicit_bounded_and_echoed(self):
        ranges=[[4096,65536],[0x400000,4]];raw=json.dumps(ranges)
        records=['4096,65536,'+(bytes(range(256))*256).hex(),'4194304,4,01020304']
        with self.assertRaises(ValueError):capture.validate_ranges(ranges)
        capture.validate_ranges(ranges,65540)
        result=capture.parse_capture('1',raw,records,{'failure':0},'65540')
        self.assertEqual(result['blobs'][0]['bytes'],bytes(range(256))*256)
        capture.require_requested(result,ranges,65540)
        for budget in (4096,65544,capture.MAX_BYTE_BUDGET):
            with self.assertRaises(ValueError):capture.require_requested(result,ranges,budget)
        for text in (None,'65536','0','65541','2097156','065540','-4'):
            with self.assertRaises(ValueError):capture.parse_capture('1',raw,records,{'failure':0},text)
        for budget in (0,1,True,65540.0,capture.MAX_BYTE_BUDGET+4):
            with self.assertRaises(ValueError):capture.validate_ranges(ranges,budget)
        capture.validate_ranges([[0,capture.MAX_BYTE_BUDGET]],capture.MAX_BYTE_BUDGET)
        with self.assertRaises(ValueError):capture.validate_ranges([[0,capture.MAX_BYTE_BUDGET],[0x400000,4]],capture.MAX_BYTE_BUDGET)
        with self.assertRaises(ValueError):capture.parse_capture(None,None,[],None,'4096')
        with self.assertRaises(ValueError):capture.require_requested(None,[],65536)

    def test_ownership_metadata_is_bounded_and_not_future_write_authority(self):
        captured=capture.parse_capture('1',self.raw,self.records,{'failure':0})
        rows=[[4096,0xA0001000,4096,0x404,1],[8192,0xA0002000,4096,0x402,0]]
        data=capture.parse_ownership(json.dumps(rows),captured)
        self.assertTrue(data[0]['writable']);self.assertFalse(data[1]['writable'])
        self.assertFalse(data[0]['pinned_for_future_gpu_use']);self.assertFalse(data[0]['payload_read'])
        self.assertFalse(data[0]['guest_write_authorized'])
        self.assertEqual(capture.parse_ownership('[null,null]',captured),[None,None])
        self.assertIsNone(capture.parse_ownership(None,captured))
        for raw in ('[]','[null]','{}','bad',json.dumps([[4096,0xA0001000,8,4,1],rows[1]]),
            json.dumps([[4096,0xA0001000,4096,4,0],rows[1]]),json.dumps([[True,0,4096,4,1],rows[1]]),
            json.dumps([[0x20000000,0,4096,4,1],rows[1]]),json.dumps([[4096,0xFFFFFFFC,4096,4,1],rows[1]])):
            with self.assertRaises(ValueError):capture.parse_ownership(raw,captured)
        with self.assertRaises(ValueError):capture.parse_ownership('[]',None)
        failed=capture.parse_capture('2',self.raw,[],None)
        self.assertEqual(capture.parse_ownership('[]',failed),[])
        with self.assertRaises(ValueError):capture.parse_ownership('[null,null]',failed)

    def test_full_owner_lookup_includes_unsnapshotted_padding_but_never_a_lease(self):
        captured=capture.parse_capture('1',self.raw,self.records,{'failure':0})
        owners=capture.parse_ownership(json.dumps([[4096,0xA0001000,4096,4,1],[8192,0xA0002000,4096,2,0]]),captured)
        result=capture.covering_stopped_owner(owners,4096,4096,True)
        self.assertEqual(result['requested_virtual_address'],0xA0001000)
        self.assertTrue(result['ownership_contained_at_stop']);self.assertFalse(result['payload_contents_verified'])
        self.assertFalse(result['pinned_for_future_gpu_use']);self.assertFalse(result['guest_write_authorized'])
        self.assertIsNone(capture.covering_stopped_owner(owners,4096,4100))
        self.assertIsNone(capture.covering_stopped_owner(owners,8192,4,True))
        self.assertIsNotNone(capture.covering_stopped_owner(owners,8192,4096))
        self.assertIsNotNone(capture.covering_stopped_owner([owners[0],owners[0]],4096,4096))
        self.assertIsNone(capture.covering_stopped_owner([None],4096,4))
        bad=dict(owners[0],byte_count=8192)
        for records in ([owners[0],bad],[bad,owners[1]],[dict(owners[0],guest_write_authorized=True)]):
            with self.assertRaises(ValueError):capture.covering_stopped_owner(records,4096,4)
        with tempfile.TemporaryDirectory() as temp:
            captured['ownership']=owners
            saved=capture.save_capture(Path(temp),captured)
            self.assertEqual(saved['ownership'],owners)

    def test_explicit_ownership_queries_do_not_relax_payload_capture_limits(self):
        ranges=[[0x1F2D8000,3768320]]
        capture.validate_ownership_ranges(ranges)
        with self.assertRaises(ValueError):capture.validate_ranges(ranges,capture.MAX_BYTE_BUDGET)
        result=capture.parse_ownership_queries('1',json.dumps(ranges),json.dumps([[0x1F2D8000,0xBF2D8000,3768320,4,1]]),{'failure':0})
        self.assertFalse(result['payload_read']);self.assertTrue(result['owners'][0]['writable'])
        capture.require_ownership_requested(result,ranges);capture.require_ownership_requested(None,[])
        self.assertEqual(capture.parse_ownership_queries('1',json.dumps(ranges),'[null]',{'failure':0})['owners'],[None])
        self.assertEqual(capture.parse_ownership_queries('0',json.dumps(ranges),'[]',None)['owners'],[])
        for wrong in ([],[[0,4]],ranges*2):
            with self.assertRaises(ValueError):capture.require_ownership_requested(result,wrong)
        for status,requested,rows,graph in ((None,'[]',None,None),('2',json.dumps(ranges),'[]',None),
            ('1',json.dumps(ranges),'[]',{'failure':0}),('1',json.dumps(ranges),'[null]',None),
            ('0',json.dumps(ranges),'[null]',None),('1','bad','[]',None)):
            with self.assertRaises(ValueError):capture.parse_ownership_queries(status,requested,rows,graph)
        for invalid in ([],[[0,0]],[[0,0x1000004]],[[True,4]],[[1,4]],[[0x1FFFFFFC,8]],[[0,8],[4,4]],[[i*4,4] for i in range(9)]):
            with self.assertRaises(ValueError):capture.validate_ownership_ranges(invalid)

if __name__=='__main__':unittest.main()
