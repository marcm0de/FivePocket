from copy import deepcopy
from itertools import product
from pathlib import Path
import sys
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from bin_predicate import BinState,retained_bins


class BinPredicateTests(unittest.TestCase):
    def test_unknown_zero_disjoint_and_known_overlap(self):
        self.assertIsNone(BinState().reference_result(True))
        self.assertTrue(BinState().reference_result(False))
        self.assertFalse(BinState((0,0,None,None)).reference_result(True))
        self.assertFalse(BinState((None,None,0,0)).reference_result(True))
        self.assertFalse(BinState((1,2,2,1)).reference_result(True))
        self.assertTrue(BinState((None,0x80000000,None,0x80000000)).reference_result(True))
        self.assertIsNone(BinState((1,0,None,None)).reference_result(True))

    def test_all_small_partial_states_match_all_completions(self):
        for words in product((None,0,1,2,3),repeat=4):
            outcomes={bool((a&c)|(b&d)) for a,b,c,d in product(*[(0,1,2,3) if w is None else (w,) for w in words])}
            want=outcomes.pop() if len(outcomes)==1 else None
            self.assertIs(BinState(words).reference_result(True),want)

    def test_assignments_immutable_and_guarded(self):
        initial=BinState();new=initial.assign(2,0xFFFFFFFF)
        self.assertEqual(initial.words,(None,)*4);self.assertEqual(new.words,(None,None,0xFFFFFFFF,None))
        for words in ((0,)*3,[0]*4,(False,0,0,0),(1.,0,0,0),(-1,0,0,0),(2**32,0,0,0)):
            with self.assertRaises(ValueError):BinState(words)
        for index,value in ((True,0),(4,0),(0,False),(0,-1),(0,2**32)):
            with self.assertRaises(ValueError):initial.assign(index,value)
        with self.assertRaises(ValueError):initial.reference_result(1)

    def test_retained_report_distinguishes_unknown_words(self):
        record=dict(mask_words=[1,0],select_words=[1,0],known_words=5,word_order='low-high',
            profile='retained-command-written-bin-words',unknown_words_are_reset_values=False,
            predicates_executed=False,retail_reset_state_verified=False)
        self.assertEqual(retained_bins(record).words,(1,None,1,None))
        self.assertTrue(retained_bins(record).reference_result(True))
        for key,value in (('known_words',16),('known_words',True),('mask_words',[1,1]),
            ('select_words',[True,0]),('word_order','high-low'),('predicates_executed',True),
            ('retail_reset_state_verified',True),('unknown_words_are_reset_values',True)):
            bad=deepcopy(record);bad[key]=value
            with self.assertRaises(ValueError):retained_bins(bad)
