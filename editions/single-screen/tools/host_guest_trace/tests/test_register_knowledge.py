import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from register_knowledge import RegisterKnowledge, WORD_MASK


class RegisterKnowledgeTests(unittest.TestCase):
    def test_unknown_bits_never_become_zero_reset_assumptions(self):
        state = RegisterKnowledge()
        with self.assertRaises(ValueError): state.require()
        self.assertEqual(state.require(0), 0)
        state = state.read_modify_write(WORD_MASK, 0x80010000)
        self.assertEqual(state.require(0x80010000), 0x80010000)
        self.assertEqual(state.known_mask, 0x80010000)
        with self.assertRaises(ValueError): state.require(1)
        self.assertFalse(state.metadata()['hardware_side_effects_validated'])
        self.assertEqual(state.read_modify_write(0, 123).require(), 123)
        self.assertEqual(RegisterKnowledge().read_modify_write(WORD_MASK, WORD_MASK).require(), WORD_MASK)

    def test_exhaustive_small_domain_matches_all_possible_initial_values(self):
        # Enumerate every known mask/value and AND/OR pair in four low bits.
        for known in range(16):
            for value in range(16):
                if value & ~known: continue
                before = RegisterKnowledge(value, known | (WORD_MASK ^ 15))
                possible = [n for n in range(16) if n & known == value]
                for and_mask in range(16):
                    for or_mask in range(16):
                        results = [(n & and_mask) | or_mask for n in possible]
                        ones, zeros = WORD_MASK, WORD_MASK
                        for result in results:
                            ones &= result
                            zeros &= WORD_MASK ^ result
                        after = before.read_modify_write(and_mask, or_mask)
                        self.assertEqual(after.known_mask, ones | zeros)
                        self.assertEqual(after.value, ones)

    def test_assignments_and_chained_masks_preserve_known_values(self):
        state = RegisterKnowledge.assigned(0xDEADBEEF)
        state = state.read_modify_write(0xFFFF00FF, 0x12340000)
        self.assertEqual(state.require(), (0xDEADBEEF & 0xFFFF00FF) | 0x12340000)
        self.assertTrue(state.metadata()['fully_known'])
        state = RegisterKnowledge().read_modify_write(0xFFFF0000, 0)
        self.assertEqual(state.require(0xFFFF), 0)
        self.assertEqual(state.read_modify_write(0xFFFF, 1).require(), 1)

    def test_rejects_invalid_words_and_inconsistent_unknown_value(self):
        for value in (-1, WORD_MASK + 1, True, 1.0, '0'):
            with self.assertRaises(ValueError): RegisterKnowledge.assigned(value)
            with self.assertRaises(ValueError): RegisterKnowledge().read_modify_write(value, 0)
            with self.assertRaises(ValueError): RegisterKnowledge().read_modify_write(0, value)
            with self.assertRaises(ValueError): RegisterKnowledge().require(value)
        with self.assertRaises(ValueError): RegisterKnowledge(1, 0)


if __name__ == '__main__': unittest.main()
