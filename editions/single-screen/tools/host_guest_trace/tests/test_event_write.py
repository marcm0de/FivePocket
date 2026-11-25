from pathlib import Path
import sys
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from event_write import describe_event_write


class EventWriteTests(unittest.TestCase):
    def test_literal_done_event_is_not_a_measured_timestamp(self):
        event=describe_event_write(0x58,[3,0x4006,0x12345678])
        self.assertEqual(event['value_source'],'packet-literal')
        self.assertEqual(event['literal_write_candidate']['physical_address'],0x4004)
        self.assertEqual(event['literal_write_candidate']['big_endian_bytes_hex'],'12345678')
        self.assertEqual(event['reference_event_name'],'PS_DONE_TS')
        for flag in ('event_executed','guest_memory_written','runtime_supported_in_draw_batch',
                     'register_side_effect_executed','wall_clock_timestamp_verified'):
            self.assertFalse(event[flag])

    def test_counter_reserved_bits_and_other_byte_orders_are_not_literal_profile(self):
        for initiator in (2,6,0x43,0x80000003,0xFFFFFFFF):
            event=describe_event_write(0x58,[initiator,0x4002,42])
            self.assertFalse(event['exact_literal_ps_done_be_profile'])
            self.assertIsNone(event['literal_write_candidate'])
        event=describe_event_write(0x58,[0x80000003,0x4002,42])
        self.assertEqual(event['value_source'],'reference-internal-counter')
        self.assertFalse(event['counter_value_known'])
        for mode in (0,1,3):
            self.assertIsNone(describe_event_write(0x58,[3,0x4000|mode,42])['literal_write_candidate'])

    def test_physical_bounds_and_exact_flush_without_memory_payload(self):
        self.assertTrue(describe_event_write(0x58,[3,0x1FFFFFFE,0])['exact_literal_ps_done_be_profile'])
        for address in (0x20000002,0xFFFFFFFE):
            self.assertFalse(describe_event_write(0x58,[3,address,0])['canonical_physical_range'])
        flush=describe_event_write(0x46,[6])
        self.assertEqual(flush['reference_event_name'],'CACHE_FLUSH')
        self.assertTrue(flush['native_resource_flush_required'])
        for key in ('explicit_memory_destination','cache_flush_completed','no_memory_payload_proves_no_side_effect'):
            self.assertFalse(flush[key])
        self.assertFalse(describe_event_write(0x46,[0x46])['exact_cache_flush_request'])

    def test_bad_inputs_reject(self):
        for opcode,payload in ((0x58,[]),(0x46,[6,0]),(0x46,[True]),(True,[6]),
                (0x58,[3,-1,0]),(0x58,[3,0,1<<32]),(0x59,[3,0,0]),(0x46,'6')):
            with self.subTest(opcode=opcode,payload=payload),self.assertRaises(ValueError):
                describe_event_write(opcode,payload)


if __name__=='__main__':unittest.main()
