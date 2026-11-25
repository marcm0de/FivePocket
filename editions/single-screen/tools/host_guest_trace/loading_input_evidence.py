"""Read-only input receipt audit. Never injects input or infers a frame transition.

Android delivery alone does not prove a polling guest observed a press. Keep
failed short pulses separate from later observed presses; do not retry either.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re

from loading_workflow import validate_request


RECORD = re.compile(r'^\d\d-\d\d (\d\d:\d\d:\d\d\.\d{3})\s+(\d+)\s+\d+\s+I RecompNativeInput: (.*)$')
SAMPLE = re.compile(r'GUEST_SAMPLE packet=(\d+) buttons=([0-9a-fA-F]{4})\b')
B_MASK = 0x2000


def held_b_command(adb, display=0):
    """A single held gamepad B, for a separately verified visible prompt only."""
    if not adb or type(display) is not int or display < 0:
        raise ValueError('validated ADB prefix and nonnegative display required')
    return [*adb, 'shell', 'input', 'gamepad', '-d', str(display),
            'keyevent', '--longpress', '97']


def analyze_input(text, pid):
    if not str(pid).isdigit() or int(pid) <= 0:
        raise ValueError('positive request PID required')
    attempts = []
    pending = None
    last_buttons = None
    for number, line in enumerate(text.splitlines(), 1):
        record = RECORD.fullmatch(line)
        if not record or record[2] != str(pid):
            continue
        stamp, _, message = record.groups()
        if message == 'SYNTHETIC KEYCODE_BUTTON_B down':
            pending = dict(down_line=number, down_device_time=stamp,
                           prior_guest_b_released=last_buttons is not None and not (last_buttons & B_MASK),
                           android_up_seen=False, guest_down_seen=False,
                           guest_release_seen=False, conflicting_input=False)
            attempts.append(pending)
        elif message == 'SYNTHETIC KEYCODE_BUTTON_B up':
            if pending:
                pending.update(android_up_seen=True, up_line=number, up_device_time=stamp)
        elif message.startswith(('PHYSICAL ', 'SYNTHETIC ')):
            if pending:
                pending['conflicting_input'] = True
        elif sample := SAMPLE.match(message):
            last_buttons = int(sample[2], 16)
            if pending:
                if not pending['android_up_seen'] and last_buttons & B_MASK:
                    pending.update(guest_down_seen=True, guest_down_line=number,
                                   guest_down_packet=int(sample[1]))
                elif pending['android_up_seen'] and not (last_buttons & B_MASK):
                    pending.update(guest_release_seen=True, guest_release_line=number,
                                   guest_release_packet=int(sample[1]))
                    pending = None
    for attempt in attempts:
        observed = all(attempt[k] for k in ('prior_guest_b_released', 'android_up_seen',
                                             'guest_down_seen', 'guest_release_seen'))
        attempt['guest_press_and_release_observed'] = observed and not attempt['conflicting_input']
        attempt['verdict'] = ('guest-press-and-release-observed' if attempt['guest_press_and_release_observed']
                              else 'insufficient-input-evidence')
    return dict(pid=str(pid), attempts=attempts,
                frame_transition_verified=False, physical_button_verified=False,
                installation_authority=False, automatic_retry_allowed=False)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--request', type=Path, required=True)
    parser.add_argument('--log', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    options = parser.parse_args()
    # A bounded snapshot may be taken while the single integrated logger runs.
    # It is evidence up to these bytes only, not an assertion of current state.
    with options.log.open('rb') as stream:
        raw = stream.read(128 * 1024 * 1024 + 1)
    if len(raw) > 128 * 1024 * 1024:
        raise ValueError('input audit exceeds bounded log size')
    request_raw = options.request.read_bytes()
    request = json.loads(request_raw)
    validate_request(request, 'dev.recompbootstrap.smoke', '311a883d')
    if request.get('native_input') is not True:
        raise ValueError('request does not use native input')
    result = analyze_input(raw.decode('utf-8', errors='strict'), request['pid'])
    result.update(request_id=request['request_id'], log_snapshot_bytes=len(raw),
                  log_snapshot_sha256=hashlib.sha256(raw).hexdigest(),
                  request_sha256=hashlib.sha256(request_raw).hexdigest())
    with options.output.open('x') as stream:
        json.dump(result, stream, indent=2)
        stream.write('\n')
    print(json.dumps(dict(attempts=len(result['attempts']),
        observed=sum(a['guest_press_and_release_observed'] for a in result['attempts']),
        frame_transition_verified=False, output=str(options.output))))


if __name__ == '__main__':
    main()
