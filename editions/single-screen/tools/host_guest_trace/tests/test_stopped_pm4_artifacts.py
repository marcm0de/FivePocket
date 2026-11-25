import copy
import hashlib
import json
from pathlib import Path
import stat
import struct
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import stopped_pm4_artifacts as capture


def packed(*words): return struct.pack('>' + 'I' * len(words), *words)


class Pm4CaptureTests(unittest.TestCase):
    def setUp(self):
        self.root = {'status': 5, 'word_count': 3, 'packet_count': 1,
                     'packets': [[0, 3, 2, 63, 0, 0, 0, 0]]}
        self.graph = {'failure': 0, 'total_words': 4, 'nodes': [
            {'parent': 0, 'parent_offset': 0, 'physical_address': 4096,
             'word_count': 1, 'packet_count': 1, 'packets': [[0, 2, 0, 0, 0, 0, 0, 0]]}]}
        self.records = ['0,' + packed(0xC0013F00, 4096, 1).hex(), '1,' + packed(0x80000000).hex()]

    def test_framing_matches_native_strict_zero_policy(self):
        _, packets = capture.decode_packets(packed(0, 7, 0x80000000, 0x40001001, 4, 5, 0xC0001001, 6, 0x8003, 8))
        self.assertEqual(packets, [[0,0,1,0,0,0,0,0], [2,2,0,0,0,0,0,0],
            [3,1,2,0,1,2,0,0], [6,3,1,16,0,0,1,0], [8,0,1,0,3,0,0,1]])
        for data in (b'a', packed(0), packed(0xC0011000, 1), b'a' * 16388):
            with self.assertRaises(ValueError): capture.decode_packets(data)

    def test_complete_capture_and_private_artifacts(self):
        result = capture.parse_pm4_capture('1', self.records, self.root, self.graph)
        self.assertEqual(result['blobs'][0]['bytes'], packed(0xC0013F00, 4096, 1))
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            reports = [capture.save_pm4_capture(root, result) for _ in range(2)]
            self.assertNotEqual(reports[0]['artifacts'][0]['path'], reports[1]['artifacts'][0]['path'])
            for report in reports:
                self.assertFalse(report['executed']); self.assertFalse(report['payloads_in_report'])
                for blob, entry in zip(result['blobs'], report['artifacts']):
                    path = root / entry['path']
                    self.assertEqual(path.read_bytes(), blob['bytes'])
                    self.assertEqual(entry['sha256'], hashlib.sha256(blob['bytes']).hexdigest())
                    self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
                    self.assertEqual(stat.S_IMODE(path.parent.stat().st_mode), 0o700)
                self.assertNotIn(result['blobs'][0]['bytes'].hex(), json.dumps(report))

    def test_unavailable_unrequested_and_partial_rejection(self):
        self.assertIsNone(capture.parse_pm4_capture(None, [], None, None))
        for status in ('0', '2'):
            self.assertEqual(capture.parse_pm4_capture(status, [], None, None)['status'], int(status))
        for status in (None, '0', '2', '3', '01'):
            with self.assertRaises(ValueError): capture.parse_pm4_capture(status, self.records, self.root, self.graph)
        for records in ([], self.records[:1], self.records * 9, self.records[::-1],
                        [self.records[0].upper(), self.records[1]],
                        [self.records[0] + '00', self.records[1]],
                        ['00,' + self.records[0].split(',')[1], self.records[1]]):
            with self.assertRaises(ValueError): capture.parse_pm4_capture('1', records, self.root, self.graph)

    def test_metadata_and_budget_mismatch(self):
        for key, value in (('failure', 1), ('total_words', 5), ('total_words', 4097)):
            with self.assertRaises(ValueError):
                capture.parse_pm4_capture('1', self.records, self.root, dict(self.graph, **{key: value}))
        for key, value in (('status', 0), ('word_count', 4), ('packet_count', 2)):
            with self.assertRaises(ValueError):
                capture.parse_pm4_capture('1', self.records, dict(self.root, **{key: value}), self.graph)
        root = copy.deepcopy(self.root); root['packets'][0][3] = 55
        with self.assertRaises(ValueError): capture.parse_pm4_capture('1', self.records, root, self.graph)
        graph = copy.deepcopy(self.graph); graph['nodes'][0]['packets'] = []
        self.assertEqual(capture.parse_pm4_capture('1', self.records, self.root, graph)['status'], 1)

    def test_pointer_cross_validation_and_ordered_child_expansion(self):
        result = capture.parse_pm4_capture('1', self.records, self.root, self.graph)
        self.assertEqual(list(capture.ordered_packets(result, self.graph)),
                         [(1, [0, 2, 0, 0, 0, 0, 0, 0], ())])
        for key, value in (('parent', 1), ('parent_offset', 1), ('physical_address', 8192)):
            graph = copy.deepcopy(self.graph); graph['nodes'][0][key] = value
            with self.assertRaises(ValueError): capture.parse_pm4_capture('1', self.records, self.root, graph)
        with self.assertRaises(ValueError):
            capture.parse_pm4_capture('1', self.records[:1], self.root,
                {'failure': 0, 'nodes': [], 'total_words': 3})

    def test_larger_predicated_batch_is_captured_without_execution(self):
        for words in (2784, 4093, 4094):
            # Opaque predicated draw plus padding; retain only64 metadata packets.
            child = packed(0xC0003601, 0x30888) + packed(0x80000000) * (words - 2)
            _, packets = capture.decode_packets(child)
            graph = copy.deepcopy(self.graph)
            graph['total_words'] = words + 3
            graph['nodes'][0].update(word_count=words, packet_count=len(packets), packets=packets[:64])
            records = ['0,' + packed(0xC0013F00,4096,words).hex(), '1,' + child.hex()]
            if words == 4094:
                with self.assertRaises(ValueError): capture.parse_pm4_capture('1',records,self.root,graph)
            else:
                result = capture.parse_pm4_capture('1',records,self.root,graph)
                ordered = list(capture.ordered_packets(result,graph))
                self.assertEqual(ordered[0][1][6], 1) # Predicate is not consumed.
                self.assertEqual(result['blobs'][1]['bytes'], child)


if __name__ == '__main__': unittest.main()
