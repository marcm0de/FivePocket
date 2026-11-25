import copy
import hashlib
import json
from pathlib import Path
import struct
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import deferred_shader_artifacts as capture


class DeferredShaderTests(unittest.TestCase):
    # Synthetic framing only; the upstream native capture parser separately
    # validates ownership and the immutable envelope. No game payloads here.
    def setUp(self):
        self.words = [0xc0042b00, 0, 3, 0x11223344, 0x55667788, 0x99aabbcc,
                      0xc0012700, 0x40001, 3]
        self.inline = struct.pack('>III', *self.words[3:6])
        self.records = ['1,0,0,' + self.inline.hex(), '1,6,1,0,262144,' + '4a' * 12]
        self.native = self.frame(self.words)

    def frame(self, words):
        child = struct.pack('>' + 'I' * len(words), *words) + bytes(129 * 4)
        return dict(status=1, blobs=[dict(bytes=bytes(12)), dict(bytes=child)],
            native_pending_submission=dict(scope='stopped-pending-native-deferred-swap',
                child_words=len(words) + 129, command_offset_words=len(words) + 10,
                deferred_request=dict(frontbuffer_virtual=0xa0100000,
                    command_virtual=0xa0002000, command_physical=0x2000)))

    def test_complete_owned_sources_and_private_report(self):
        result = capture.parse_capture('1', self.records, self.native, True)
        self.assertEqual(result['prefix_words'], 9)
        self.assertEqual(result['prefix_packets'], 2)
        self.assertEqual(result['blobs'][0]['bytes'], self.inline)
        self.assertFalse(result['blobs'][1]['immediate'])
        with tempfile.TemporaryDirectory() as temp:
            report = capture.save_capture(Path(temp), result)
            self.assertEqual(report['scope'], 'stopped-deferred-prefix-shader-snapshot')
            for key in ('executed', 'retail_graph_valid', 'rendering_verified', 'ring_retired',
                        'requested_image_read', 'gpu_execution_time_contents_verified'):
                self.assertIs(report[key], False)
            self.assertEqual(report['child_sha256'], hashlib.sha256(self.native['blobs'][1]['bytes']).hexdigest())
            self.assertNotIn(self.inline.hex(), json.dumps(report))
            self.assertEqual((Path(temp) / report['artifacts'][0]['path']).read_bytes(), self.inline)

    def test_opt_in_status_bounds_and_complete_provenance(self):
        self.assertIsNone(capture.parse_capture(None, [], None, False))
        self.assertIsNone(capture.save_capture(None, None))
        for status in ('0', '2'):
            self.assertEqual(capture.parse_capture(status, [], None, True), dict(status=int(status), blobs=[]))
        for status, records, native, opted in [
                (None, self.records, self.native, True), ('1', self.records, self.native, False),
                ('1', self.records, None, True), ('1', self.records, dict(status=0), True),
                ('1', self.records[:1], self.native, True), ('1', self.records[::-1], self.native, True),
                ('1', self.records * 5, self.native, True), ('1', self.records * 2, self.native, True),
                *[(s, self.records, self.native, True) for s in ('0', '2', '3', '01', '')]]:
            with self.assertRaises(ValueError): capture.parse_capture(status, records, native, opted)
        for key, value in [('scope', 'retail-pm4'), ('child_words', 129), ('command_offset_words', 20)]:
            native = copy.deepcopy(self.native)
            native['native_pending_submission'][key] = value
            with self.assertRaises(ValueError): capture.parse_capture('1', self.records, native, True)

    def test_all_inline_bytes_bound_to_current_prefix(self):
        for index in range(12):
            for bit in range(8):
                code = bytearray(self.inline); code[index] ^= 1 << bit
                with self.assertRaises(ValueError):
                    capture.parse_capture('1', ['1,0,0,' + code.hex(), self.records[1]], self.native, True)
        for external in (self.records[1].replace('262144', '262148'),
                         self.records[1].replace('1,6,1,0,262144,', '1,6,1,'),
                         self.records[1].replace('1,6,1', '1,6,0')):
            with self.assertRaises(ValueError):
                capture.parse_capture('1', [self.records[0], external], self.native, True)

    def test_shader_metadata_and_requested_image_alias_refused(self):
        for index, value in [(0, 0xc0042b01), (1, 2), (2, 0x10003), (6, 0xc0012701),
                (7, 0x40002), (8, 4), (8, 0x10003), (7, 0x100001), (7, 0xffff9), (7, 0x1ffffffd)]:
            words = self.words.copy(); words[index] = value
            with self.assertRaises(ValueError): capture.parse_capture('1', self.records, self.frame(words), True)
        for words in ([0xc0012700], [0x80000000] * 513, [0xc0012700, 0x40001, 3] * 9):
            with self.assertRaises(ValueError): capture.parse_capture('1', [], self.frame(words), True)
        oversized = self.frame([0xc0012700, 0x40001, 1026])
        with self.assertRaises(ValueError):
            capture.parse_capture('1', ['1,0,1,0,262144,' + '4a' * 4104], oversized, True)

    def test_color_metadata_is_current_input_admission_not_execution(self):
        native = self.frame(self.words + [0xc0003600, 0x30088] * 4 + [0xc0022f00, 0x50000, 0, 16])
        sources = capture.parse_capture('1', self.records, native, True)
        deferred = dict(pending=True, image_physical=0x100000)
        ring = dict(ring_read_word=76)
        fields = [1, 0, 21, 4, 2, 1, 1475032, 0x100000, 1, 76]
        def parse(values=fields, source=sources, ticket=deferred, current=ring, command=native):
            return capture.parse_color_inspection(json.dumps(values), source, ticket, current, command)
        result = parse()
        self.assertEqual(result['source_bytes'], 1475032)
        for key in ('gpu_executed', 'guest_writes', 'requested_image_read', 'frame_presented',
                    'ring_retired', 'live_allocation_leases_acquired'):
            self.assertIs(result[key], False)
        self.assertIsNone(capture.parse_color_inspection(None, None, None, None, None))
        for status, rejection in [(0, 0), (2, 1), (2, 6)]:
            self.assertEqual(parse([status, rejection] + [0] * 7 + [76])['status'], status)
        for index, value in [(0, 3), (1, 1), (2, 20), (3, 3), (4, 1), (5, 0), (6, 1475031),
                             (7, 0x100004), (8, 0), (8, 2), (9, 73), (0, True)]:
            bad = fields.copy(); bad[index] = value
            with self.assertRaises(ValueError): parse(bad)
        for kwargs in [dict(source=None), dict(ticket=None), dict(ticket=dict(deferred, pending=False)),
                       dict(current=None), dict(command=None), dict(command=self.native)]:
            with self.assertRaises(ValueError): parse(**kwargs)
        for bad in [[0, 1] + [0] * 7 + [76], [2, 0] + [0] * 7 + [76], [2, 7] + [0] * 7 + [76],
                    [2, 1, 21] + [0] * 6 + [76], fields[:-1], fields + [1]]:
            with self.assertRaises(ValueError): parse(bad)
        for raw in ('bad', '[' * 400, '[true]'):
            with self.assertRaises(ValueError): capture.parse_color_inspection(raw, sources, deferred, ring, native)

    def test_memory_report_is_not_a_live_lease_or_execution(self):
        sources=capture.parse_capture('1',self.records,self.native,True)
        color=dict(status=1,unchanged_read_word=76,constant_zero_textures=1,constant_loads=0)
        result=capture.parse_memory_inspection('[1,8,7,76]',color,sources)
        self.assertTrue(result['whole_submission_and_sources_matched'])
        for key in ('image_pixels_read','gpu_executed','guest_writes','ring_retired',
                    'live_allocation_leases_acquired','interrupt_state_validated'):
            self.assertIs(result[key],False)
        self.assertIsNone(capture.parse_memory_inspection(None,None,None))
        for status in (0,2):
            self.assertEqual(capture.parse_memory_inspection(f'[{status},0,0,76]',color,sources)['status'],status)
        for raw in ('bad','[1,8,7,76,0]','[true,8,7,76]','[1,7,7,76]','[1,8,6,76]',
                    '[1,8,7,73]','[3,0,0,76]','[0,8,0,76]','[2,0,7,76]','['*200):
            with self.assertRaises(ValueError):capture.parse_memory_inspection(raw,color,sources)
        for admitted in (None,dict(color,status=0),dict(color,status=2)):
            with self.assertRaises(ValueError):capture.parse_memory_inspection('[1,8,7,76]',admitted,sources)
        with self.assertRaises(ValueError):capture.parse_memory_inspection('[1,8,7,76]',color,None)

    def test_write_guard_requires_complete_current_before_images(self):
        gpu=dict(status=1,unchanged_read_word=76);memory=dict(status=1,input_ranges=12)
        color=dict(status=1,source_bytes=1475548,prefix_words=2778);ring=dict(ring_bytes=65536)
        parents=(gpu,memory,color,ring);fields=[1,9089412,24,76]
        result=capture.parse_write_guard(json.dumps(fields),*parents)
        self.assertTrue(result['all_before_images_verified'])
        for key in ('guest_writes','frame_presented','ring_retired','live_allocation_leases_acquired','pixels_decoded'):
            self.assertFalse(result[key])
        self.assertIsNone(capture.parse_write_guard(None,None,None,None,None))
        for status in (0,2):
            self.assertEqual(capture.parse_write_guard(f'[{status},0,0,76]',*parents)['status'],status)
        for i in range(1,4):
            bad=fields.copy();bad[i]^=1
            with self.assertRaises(ValueError):capture.parse_write_guard(json.dumps(bad),*parents)
        for raw in ('bad','['*200,'[true,9089412,24,76]',json.dumps(fields+[0]),'[3,0,0,76]','[2,1,0,76]'):
            with self.assertRaises(ValueError):capture.parse_write_guard(raw,*parents)
        for i in range(4):
            bad=list(parents);bad[i]=None
            with self.assertRaises(ValueError):capture.parse_write_guard(json.dumps(fields),*bad)
        for i in range(3):
            bad=list(parents);bad[i]=dict(bad[i],status=0)
            with self.assertRaises(ValueError):capture.parse_write_guard(json.dumps(fields),*bad)

    def test_pending_state_cannot_claim_live_commit(self):
        timeline=dict(status=1,unchanged_read_word=76)
        fields=[1,219,6,2,1,1,1,1,76]
        result=capture.parse_pending_state(json.dumps(fields),timeline)
        self.assertEqual(result['unknown_completion_registers'],6)
        self.assertEqual(result['invalidation_delta'],2)
        self.assertFalse(result['live_state_committed'])
        self.assertIsNone(capture.parse_pending_state(None,None))
        self.assertEqual(capture.parse_pending_state('[0,0,0,0,0,0,0,0,76]',dict(timeline,status=0))['status'],0)
        for i in (0,2,3,4,5,6,7,8):
            bad=fields.copy();bad[i]^=1
            with self.assertRaises(ValueError):capture.parse_pending_state(json.dumps(bad),timeline)
        for count in (0,0x8001):
            bad=fields.copy();bad[1]=count
            with self.assertRaises(ValueError):capture.parse_pending_state(json.dumps(bad),timeline)
        for raw in ('bad','['*200,'[true,219,6,2,1,1,1,1,76]',json.dumps(fields+[0]),'[0,0,6,0,0,0,0,0,76]'):
            with self.assertRaises(ValueError):capture.parse_pending_state(raw,timeline)
        for parent in (None,dict(timeline,status=0)):
            with self.assertRaises(ValueError):capture.parse_pending_state(json.dumps(fields),parent)

    def test_prefix_timeline_is_separate_from_old_completion(self):
        gpu=dict(status=1,unchanged_read_word=76)
        fields=[1,1,4,2,1,1,1,0,76]
        result=capture.parse_gpu_timeline(json.dumps(fields),gpu)
        self.assertEqual(result['invalidations'],2)
        self.assertEqual(result['literal_color_events'],0)
        self.assertFalse(result['live_state_committed'])
        self.assertFalse(result['ring_retired'])
        self.assertIsNone(capture.parse_gpu_timeline(None,None))
        for status in (0,2):
            self.assertEqual(capture.parse_gpu_timeline('[0,0,0,0,0,0,0,0,76]',dict(gpu,status=status))['status'],0)
        for i in range(9):
            bad=fields.copy();bad[i]^=1
            with self.assertRaises(ValueError):capture.parse_gpu_timeline(json.dumps(bad),gpu)
        for raw in ('bad','['*200,'[true,1,4,2,1,1,1,0,76]',json.dumps(fields+[0]),
                    '[0,1,0,0,0,0,0,0,76]','[2,0,0,0,0,0,0,0,76]','[1,1,4,3,3,1,1,2,76]'):
            with self.assertRaises(ValueError):capture.parse_gpu_timeline(raw,gpu)
        for parent in (None,dict(gpu,status=0),dict(gpu,status=2)):
            with self.assertRaises(ValueError):capture.parse_gpu_timeline(json.dumps(fields),parent)

    def test_offscreen_gpu_report_cannot_retire_or_present(self):
        memory=dict(status=1,unchanged_read_word=76)
        fields=[1,1,4,3686400,3686400,1,1,1,76]
        result=capture.parse_gpu_inspection(json.dumps(fields),memory)
        self.assertTrue(result['completed_profile'])
        for key in ('guest_writes','frame_presented','ring_retired','old_completion_receipt_reused',
                    'live_allocation_leases_acquired','partial_gpu_work_possible'):
            self.assertIs(result[key],False)
        self.assertIsNone(capture.parse_gpu_inspection(None,None))
        failed=capture.parse_gpu_inspection('[2,0,0,0,0,1,1,0,76]',memory)
        self.assertTrue(failed['partial_gpu_work_possible']);self.assertFalse(failed['completed_profile'])
        self.assertEqual(capture.parse_gpu_inspection('[0,0,0,0,0,0,0,0,76]',memory)['status'],0)
        for i in range(1,9):
            bad=fields.copy();bad[i]^=1
            with self.assertRaises(ValueError):capture.parse_gpu_inspection(json.dumps(bad),memory)
        for raw in ('bad','['*256,'[true,1,4,3686400,3686400,1,1,1,76]',
                    '[3,0,0,0,0,0,0,0,76]','[2,1,4,3686400,3686400,1,1,1,76]',
                    '[0,0,0,0,0,1,0,0,76]','[2,0,0,0,0,2,1,0,76]',json.dumps(fields+[0])):
            with self.assertRaises(ValueError):capture.parse_gpu_inspection(raw,memory)
        for parent in (None,dict(memory,status=0),dict(memory,status=2)):
            with self.assertRaises(ValueError):capture.parse_gpu_inspection(json.dumps(fields),parent)


if __name__ == '__main__': unittest.main()
