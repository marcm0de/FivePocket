from pathlib import Path
import struct
import sys
import unittest

sys.path[:0] = [str(Path(__file__).resolve().parents[1]),
    str(Path(__file__).resolve().parents[2] / 'rpf7_inventory/tests')]
import analyze_stopped_pm4 as analysis
from test_raw_shader_analysis import program, cf_alloc, cf_exec, alu


def packets(extra=()):
    vs = program([cf_alloc(2), cf_exec(address=2, opcode=1), cf_alloc(1), cf_exec(address=3)],
                 [alu(vector=0, zero=True), alu(dest=62, vector=8, scalar=8, zero=True)])
    ps = program([cf_alloc(), cf_exec()], [alu()])
    words = []
    for index, value in ((0x2180,0x1000000E), (0x2181,0), (0x2104,0), (0x2200,0),
                         (0x2208,4), (0x2293,0), (0x2280,0x80008)):
        words.extend((index, value))
    for stage, code in enumerate((vs, ps)):
        n = len(code) // 4
        words.extend((0xC0002B00 | ((n + 1) << 16), stage, n, *struct.unpack('>' + 'I' * n, code)))
    words.extend((*extra, 0xC0003600, 0x10081, 0xC0025800, 3, 0x1002, 7))
    data = struct.pack('>' + 'I' * len(words), *words)
    decoded_words, decoded = analysis.pm4.decode_packets(data)
    return [(0, p, decoded_words[p[0]+1:p[0]+p[2]+1]) for p in decoded]


class StoppedAnalysisTests(unittest.TestCase):
    def test_ordered_state_and_literal_event_do_not_execute(self):
        report, sources = analysis.analyze_packets(packets())
        self.assertFalse(report['guest_commands_executed'])
        self.assertEqual(len(sources), 2)
        self.assertTrue(report['draws'][0]['color_writes_disabled'])
        self.assertTrue(report['draws'][0]['depth_stencil_disabled'])
        self.assertFalse(report['events'][0]['written'])
        self.assertFalse(report['events'][0]['memory_ownership_validated'])
        self.assertEqual(report['events'][0]['physical_address'], 4096)
        self.assertEqual(report['events'][0]['prior_point_requests'], 1)

    def test_unknown_control_predication_and_missing_state_reject(self):
        for extra in ((0xC0007F00, 0), (0xC0001001, 0),
                      (0xC0022100, 0x80000000, 0, 0), (0x2181, 1)):
            with self.assertRaises(ValueError): analysis.analyze_packets(packets(extra))
        with self.assertRaises(ValueError): analysis.analyze_packets(packets()[1:])

    def test_write_masks_are_observed_not_assumed(self):
        report, _ = analysis.analyze_packets(packets((0x2104,15)))
        self.assertFalse(report['draws'][0]['color_writes_disabled'])
        # REG_RMW has a narrower 13-bit register index; do not alias high banks.
        with self.assertRaises(ValueError):
            analysis.analyze_packets(packets((0xC0022100,0x2104,0xFFFFFFFF,15)))

    def test_unknown_register_rmw_stays_unknown(self):
        report, _ = analysis.analyze_packets(packets((0xC0022100,0x81,0xFFFFFFFF,0)))
        self.assertEqual(len(report['read_modify_write_requests']), 1)
        self.assertNotIn(0x81, [entry[2] for entry in report['hardware_register_writes']])
        self.assertEqual(report['incomplete_register_knowledge']['129']['known_mask'], 0)
        report, _ = analysis.analyze_packets(packets((0xC0022100,0x81,0xFFFFFFFF,0x80010000)))
        state = report['incomplete_register_knowledge']['129']
        self.assertEqual(state['known_mask'], 0x80010000)
        self.assertEqual(state['value_bits'], 0x80010000)
        self.assertFalse(state['fully_known'])
        report, _ = analysis.analyze_packets(packets((0xC0022100,0x81,0xFFFFFFFF,0x80010000,
            0xC0022100,0x81,0,42)))
        self.assertNotIn('129', report['incomplete_register_knowledge'])


if __name__ == '__main__': unittest.main()
