#!/usr/bin/env python3
"""Adds the builder's ui stage (build_ui_assets.py) to a format-2 profile: the stage, one output per launcher file with its
expected SHA-256, the bundle components (role ui-asset) and the stage's tools in bundle.portable_tools.

    ui_stage.py PROFILE_IN.json PROFILE_OUT.json --pins PINS.json

The stage goes last: every stage key holds the key of the stage before it, so a stage added at the end leaves the receipts of
an existing work folder valid and runs alone. Its only stage input is the codegen tree (the archive key is recovered from it).

PINS.json is private, like the profile: {"schema": "own-disc-ui-pins/1", "files": {"ui/<name>": "<sha256>", ...}}, one hash for
every file the stage writes (the names below); a missing, extra or malformed pin is refused. The hashes come from a run from
the discs (the stage's report.json lists them); the outputs are the same on every host. PROFILE_OUT must not exist.
"""
import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ui_fonts  # noqa: E402
import ui_pictures  # noqa: E402
import ui_music  # noqa: E402
import ui_sounds  # noqa: E402

STAGE_ID = 'ui-assets'
PINS_SCHEMA = 'own-disc-ui-pins/1'
FFMPEG_SOURCE = '${repo}/local-analysis/builder/xma-source/ffmpeg-15ece0882e8d.tar'
KEY_ADDRESS = '0x83bd4c98'
OWN = ['build_ui_assets.py', 'ui_fonts.py', 'ui_music.py', 'ui_pictures.py', 'ui_sounds.py', 'xma2_decode.py']
MUSIC = 'ui/music/launcher-music.wav'
PARTS = 'fonts,glyphs,art,sounds,music'          # every part: the stage pins every file (build_ui_assets.py's default leaves out music)
SUPPORT = ['disc_builder.py', 'host_support.py', 'checkout_refusal.py', 'build_shader_pack.py', 'shader_toolchain.py']


def files():
    """Every file the stage writes into the bundle, as its component name."""
    return sorted(['ui/' + f for f in ui_fonts.FACES.values()] + ['ui/glyph-%s.png' % p for p in ui_pictures.GLYPHS]
                  + ['ui/' + n for n in ui_pictures.ART] + ['ui/sounds/%s.wav' % n.lower() for n in ui_sounds.NAMES]
                  + ['ui/manifest.txt', MUSIC])


def output_key(name):
    return re.sub(r'[^a-z0-9]+', '_', name[len('ui/'):]).strip('_')


def add(profile: dict, pins: dict) -> dict:
    if pins.get('schema') != PINS_SCHEMA or not isinstance(pins.get('files'), dict):
        raise ValueError('the pins file is not ' + PINS_SCHEMA)
    want, got = set(files()), set(pins['files'])
    if want != got:
        raise ValueError('the pins do not cover the stage files: missing %s, extra %s' % (sorted(want - got), sorted(got - want)))
    bad = [n for n, h in pins['files'].items() if not (isinstance(h, str) and re.fullmatch(r'[0-9a-f]{64}', h))]
    if bad:
        raise ValueError('pins that are no SHA-256: ' + ', '.join(sorted(bad)))
    if any(s.get('id') == STAGE_ID for s in profile['stages']):
        raise ValueError('the profile has a %s stage already' % STAGE_ID)
    bundle = profile.get('bundle') or {}
    if bundle.get('format') != 2:
        raise ValueError('launcher files travel in format-2 bundles only')
    outputs = {'tree': STAGE_ID, 'report': STAGE_ID + '/report.json'}
    outputs.update({output_key(n): STAGE_ID + '/' + n for n in files()})
    profile['stages'].append({
        'id': STAGE_ID, 'adapter': 'command', 'heavy': False,
        'description': 'The launcher\'s fonts, controller glyphs, backdrop and tile pictures, the SELECT / BACK menu sounds and its '
                       'music (the score track %s), made from the play disc (archive key recovered in memory from this run '
                       'codegen); the same bytes on every host.' % ui_music.BANK.split('/')[-1],
        'tools': ['${python}'] + ['${repo}/tools/own_disc/' + f for f in OWN + SUPPORT] + ['${repo}/tools/rpf7_inventory',
                                                                                             '${hosttools}/xmem-lzx${exe}'],
        'inputs': {'generated': '${out:codegen:tree}', 'ffmpeg_source': FFMPEG_SOURCE},
        'outputs': outputs,
        'expect': {output_key(n): pins['files'][n] for n in files()},
        'clean': [STAGE_ID],
        'argv': ['${python}', '-B', '${repo}/tools/own_disc/build_ui_assets.py', '--play', '${disc:play}',
                 '--generated', '${out:codegen:tree}', '--xmem', '${hosttools}/xmem-lzx${exe}', '--ffmpeg-source', FFMPEG_SOURCE,
                 '--key-address', KEY_ADDRESS, '--parts', PARTS, '--output', '${work}/' + STAGE_ID]})
    bundle['components'] = bundle['components'] + [{'name': n, 'role': 'ui-asset', 'stage': STAGE_ID, 'output': output_key(n)}
                                                   for n in files()]
    tools = bundle.get('portable_tools', [])
    bundle['portable_tools'] = tools + ['${repo}/tools/own_disc/' + f for f in OWN if '${repo}/tools/own_disc/' + f not in tools]
    return profile


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('profile_in', type=Path)
    ap.add_argument('profile_out', type=Path)
    ap.add_argument('--pins', type=Path, required=True)
    a = ap.parse_args(argv)
    if a.profile_out.exists():
        print('%s exists already: use a new name' % a.profile_out, file=sys.stderr)
        return 1
    try:
        profile = add(json.loads(a.profile_in.read_text(encoding='utf-8')), json.loads(a.pins.read_text(encoding='utf-8')))
    except (ValueError, KeyError) as e:
        print('UI_STAGE_REFUSED %s' % e, file=sys.stderr)
        return 1
    a.profile_out.write_text(json.dumps(profile, indent=1) + '\n', encoding='utf-8', newline='\n')
    print('written %s: %d stages, %d bundle components (%d launcher files)' % (a.profile_out, len(profile['stages']),
          len(profile['bundle']['components']), len(files())))
    return 0


if __name__ == '__main__':
    sys.exit(main())
