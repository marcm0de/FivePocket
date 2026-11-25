from pathlib import Path
import sys
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import loading_input_evidence as audit


def line(message, pid=123):
    return f'02-14 07:43:31.000 {pid} 456 I RecompNativeInput: {message}\n'


NEUTRAL = line('GUEST_SAMPLE packet=1 buttons=0000')
DOWN = line('SYNTHETIC KEYCODE_BUTTON_B down')
UP = line('SYNTHETIC KEYCODE_BUTTON_B up')
PRESSED = line('GUEST_SAMPLE packet=2 buttons=2000')
RELEASED = line('GUEST_SAMPLE packet=3 buttons=0000')


class LoadingInputEvidenceTests(unittest.TestCase):
    def test_short_pulse_delivery_is_not_guest_observation(self):
        result = audit.analyze_input(NEUTRAL + DOWN + UP + RELEASED, 123)
        self.assertFalse(result['attempts'][0]['guest_press_and_release_observed'])
        self.assertFalse(result['automatic_retry_allowed'])

    def test_held_press_does_not_imply_frame_or_physical_success(self):
        result = audit.analyze_input(NEUTRAL + DOWN + PRESSED + UP + RELEASED, 123)
        self.assertTrue(result['attempts'][0]['guest_press_and_release_observed'])
        for key in ('frame_transition_verified', 'physical_button_verified', 'installation_authority'):
            self.assertFalse(result[key])

    def test_separate_attempts_do_not_bless_failed_first_pulse(self):
        result = audit.analyze_input(NEUTRAL + DOWN + UP + RELEASED + DOWN + PRESSED + UP + RELEASED, 123)
        self.assertEqual([a['guest_press_and_release_observed'] for a in result['attempts']], [False, True])

    def test_wrong_pid_missing_release_and_unknown_prior_are_insufficient(self):
        self.assertEqual(audit.analyze_input(NEUTRAL + DOWN + PRESSED + UP + RELEASED, 124)['attempts'], [])
        for text in (DOWN + PRESSED + UP + RELEASED, NEUTRAL + DOWN + PRESSED + UP,
                     PRESSED + DOWN + PRESSED + UP + RELEASED,
                     NEUTRAL + DOWN + UP + PRESSED + RELEASED):
            self.assertFalse(audit.analyze_input(text, 123)['attempts'][0]['guest_press_and_release_observed'])

    def test_interfering_physical_or_other_synthetic_input_is_ambiguous(self):
        for event in ('PHYSICAL KEYCODE_BUTTON_B down', 'SYNTHETIC KEYCODE_BUTTON_A down'):
            result = audit.analyze_input(NEUTRAL + DOWN + line(event) + PRESSED + UP + RELEASED, 123)
            self.assertFalse(result['attempts'][0]['guest_press_and_release_observed'])

    def test_command_is_held_gamepad_b_on_explicit_display(self):
        self.assertEqual(audit.held_b_command(['adb', '-s', 'serial']),
                         ['adb', '-s', 'serial', 'shell', 'input', 'gamepad', '-d', '0',
                          'keyevent', '--longpress', '97'])
        for display in (-1, '0', True):
            with self.assertRaises(ValueError): audit.held_b_command(['adb'], display)


if __name__ == '__main__':
    unittest.main()
