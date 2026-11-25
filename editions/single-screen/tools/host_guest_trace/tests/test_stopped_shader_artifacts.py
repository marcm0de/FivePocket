import hashlib
import json
from pathlib import Path
import stat
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import stopped_shader_artifacts as capture


class ShaderCaptureTests(unittest.TestCase):
    def setUp(self):
        # Synthetic complete instruction, not game code.
        self.code = bytes(range(12))
        self.record = '2,3,0,' + self.code.hex()
        self.work = {'failure': 0, 'shaders': [[2, 3, 0, 3, 1, 0]]}

    def test_unrequested_unavailable_and_empty(self):
        self.assertIsNone(capture.parse_shader_capture(None, [], None))
        for status in ('0', '2'):
            self.assertEqual(capture.parse_shader_capture(status, [], None),
                             {'status': int(status), 'blobs': []})
        self.assertEqual(capture.parse_shader_capture('1', [], {'failure': 0, 'shaders': []}),
                         {'status': 1, 'blobs': []})
        for status in (None, '0', '2', '3', '01', ''):
            with self.assertRaises(ValueError):
                capture.parse_shader_capture(status, [self.record], self.work)

    def test_requires_complete_validated_inline_metadata(self):
        result = capture.parse_shader_capture('1', [self.record], self.work)
        self.assertEqual(result['blobs'][0]['bytes'], self.code)
        for work in (None, {'failure': 1}, {'failure': 0, 'shaders': []},
                     {'failure': 0, 'shaders': [[2, 3, 0, 3, 0, 4096]]},
                     {'failure': 0, 'shaders': [[2, 3, 1, 3, 1, 0]]},
                     {'failure': 0, 'shaders': [[2, 3, 0, 6, 1, 0]]}):
            with self.assertRaises(ValueError):
                capture.parse_shader_capture('1', [self.record], work)
        for raw in ('02,3,0,' + self.code.hex(), self.record.upper(),
                    self.record[:-1], self.record + '\n', '2,3,0,',
                    '2,4,0,' + self.code.hex(), '../x,3,0,' + self.code.hex()):
            with self.assertRaises(ValueError):
                capture.parse_shader_capture('1', [raw], self.work)

    def test_instruction_and_aggregate_limits(self):
        for size in (4, 16, 4098, 4104):
            with self.assertRaises(ValueError):
                capture.parse_shader_capture('1', ['2,3,0,' + (b'a' * size).hex()],
                    {'failure': 0, 'shaders': [[2, 3, 0, size // 4, 1, 0]]})
        code = b'a' * 4080
        result = capture.parse_shader_capture('1', ['2,3,0,' + code.hex()],
            {'failure': 0, 'shaders': [[2, 3, 0, 1020, 1, 0]]})
        self.assertEqual(len(result['blobs'][0]['bytes']), 4080)
        with self.assertRaises(ValueError):
            capture.parse_shader_capture('1', [self.record] * 9,
                {'failure': 0, 'shaders': self.work['shaders'] * 9})
        with self.assertRaises(ValueError):
            capture.parse_shader_capture('1', ['2,3,0,' + (b'a' * 2052).hex()] * 2,
                {'failure': 0, 'shaders': [[2, 3, 0, 513, 1, 0]] * 2})

    def test_external_capture_requires_exact_physical_provenance(self):
        work = {'failure':0, 'shaders':[[2,3,0,3,0,4096]]}
        record = '2,3,0,0,4096,' + self.code.hex()
        result = capture.parse_shader_capture('1', [record], work)
        self.assertFalse(result['blobs'][0]['immediate'])
        self.assertEqual(result['blobs'][0]['physical_address'], 4096)
        self.assertEqual(result['blobs'][0]['bytes'], self.code)
        for raw in (self.record, record.replace('4096', '4097'), record.replace('4096', '8192'),
                    record.replace('4096', '04096'), record.replace('0,4096', '1,4096')):
            with self.assertRaises(ValueError): capture.parse_shader_capture('1', [raw], work)
        with self.assertRaises(ValueError): capture.parse_shader_capture('1', [record], self.work)
        for address in (4097, 0x1FFFFFF8, 0x20000000, 0xFFFFFFFC):
            with self.assertRaises(ValueError):
                capture.parse_shader_capture('1', ['2,3,0,0,' + str(address) + ',' + self.code.hex()],
                    {'failure':0, 'shaders':[[2,3,0,3,0,address]]})
        with tempfile.TemporaryDirectory() as temporary:
            report = capture.save_shader_capture(Path(temporary), result)
            self.assertFalse(report['gpu_execution_time_contents_verified'])
            self.assertFalse(report['executed']); self.assertFalse(report['predicates_evaluated'])
            self.assertEqual(report['scope'], 'stopped-cpu-memory-snapshot')
            self.assertEqual(report['artifacts'][0]['physical_address'], 4096)

    def test_private_artifacts_are_fresh_and_report_has_no_program_bytes(self):
        parsed = capture.parse_shader_capture('1', [self.record], self.work)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            reports = [capture.save_shader_capture(root, parsed) for _ in range(2)]
            paths = [root / r['artifacts'][0]['path'] for r in reports]
            self.assertNotEqual(*paths)
            for report, path in zip(reports, paths):
                self.assertEqual(path.read_bytes(), self.code)
                self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
                self.assertEqual(stat.S_IMODE(path.parent.stat().st_mode), 0o700)
                self.assertEqual(report['artifacts'][0]['sha256'], hashlib.sha256(self.code).hexdigest())
                self.assertFalse(report['programs_in_report'])
                self.assertNotIn(self.code.hex(), json.dumps(report))
            self.assertIsNone(capture.save_shader_capture(root, None))
            self.assertEqual(capture.save_shader_capture(root, {'status': 0, 'blobs': []})['artifacts'], [])


if __name__ == '__main__':
    unittest.main()
