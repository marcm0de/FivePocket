from pathlib import Path
import sys
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from coherency_wait import describe_coherency_wait,REGISTERS
from register_knowledge import RegisterKnowledge as Knowledge


class CoherencyWaitTests(unittest.TestCase):
    request=(3,0xA31,0,0x80000000,8)

    def candidates(self,**words):
        result={r:Knowledge() for r in REGISTERS}
        for r,v in words.items():result[int(r,16)]=Knowledge.assigned(v)
        return result

    def test_zero_size_destination_seven_does_not_satisfy_wait(self):
        result=describe_coherency_wait(self.request,self.candidates(A31=0x10000,A2F=0,A30=0x1000,**{'200D':0}))
        self.assertTrue(result['exact_observed_busy_wait_shape'])
        self.assertEqual(result['status_candidate']['value_bits'],0x10000)
        self.assertEqual(result['xenia_status_after_write_reference']['value_bits'],0x80010000)
        self.assertFalse(result['existing_empty_cache_range_profile_compatible'])
        self.assertFalse(result['zero_size_proves_no_work']);self.assertIsNone(result['wait_result'])
        self.assertFalse(result['runtime_supported']);self.assertFalse(result['guest_wait_executed'])
        selected=[x for x in result['selected_destination_candidates'] if x['enabled_candidate']]
        self.assertEqual([x['name'] for x in selected],['destination-7'])
        self.assertEqual(selected[0]['base_candidate']['value_bits'],0)

    def test_unknown_status_is_never_a_reset_or_completed_action(self):
        candidates=self.candidates();result=describe_coherency_wait(self.request,candidates)
        self.assertEqual(result['status_candidate']['known_mask'],0)
        self.assertEqual(result['xenia_status_after_write_reference']['known_mask'],0x80000000)
        self.assertTrue(all(x['enabled_candidate'] is None for x in result['selected_destination_candidates']))
        self.assertIsNone(result['existing_empty_cache_range_profile_compatible'])
        self.assertEqual(candidates[0xA31].known_mask,0)

    def test_range_shape_compatibility_is_not_backend_execution(self):
        result=describe_coherency_wait(self.request,self.candidates(A31=0x03000000,A2F=4096,A30=8192))
        self.assertTrue(result['existing_empty_cache_range_profile_compatible'])
        self.assertFalse(result['cache_action_completed']);self.assertIsNone(result['wait_result'])
        for size,base in ((0,0),(1,0),(4096,1),(8192,0x1FFFF000)):
            result=describe_coherency_wait(self.request,self.candidates(A31=0x03000000,A2F=size,A30=base))
            self.assertFalse(result['existing_empty_cache_range_profile_compatible'])

    def test_changed_requests_are_not_the_exact_observed_profile(self):
        for i in range(5):
            request=list(self.request);request[i]^=1
            self.assertFalse(describe_coherency_wait(request,self.candidates())['exact_observed_busy_wait_shape'])
        for bad in ((),self.request[:-1],(True,*self.request[1:]),(-1,*self.request[1:]),(1<<32,*self.request[1:])):
            with self.assertRaises(ValueError):describe_coherency_wait(bad,self.candidates())
        for bad in ({},{r:0 for r in REGISTERS}):
            with self.assertRaises(ValueError):describe_coherency_wait(self.request,bad)
