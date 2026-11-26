#!/usr/bin/env python3
"""Builder ui stage (1.2.0): the launcher's fonts, glyphs, pictures and menu sounds, made from the player's own play disc.

    build_ui_assets.py --play DISC --generated CODEGEN_PPC --xmem XMEM_TOOL --ffmpeg-source FFMPEG_TAR --output NEW_DIR
                       [--key-address 0x83bd4c98] [--parts fonts,glyphs,art,sounds[,music]]

Reads nine files out of the play disc's archives (the archive key is recovered in memory from this run's generated code,
as the shader-extract stage does; it is never written or printed):
  xbox360a.rpf!/data/cdimages/scaleform_platform_360.rpf!/font_lib_efigs_360.gfx   -> ui/chalet-london.ttf, chalet-comprime.ttf,
                                                                                       pricedown.ttf          (ui_fonts.py)
  xbox360b.rpf!/textures/gfxfontlib_360.xtd                                          -> ui/glyph-<prompt>.png  (ui_pictures.py)
  xbox360a.rpf!/data/cdimages/scaleform_frontend.rpf!/loadingscreen{1,0,6,5}.xtd     -> ui/menu-art.png, ui/tile-story.png,
                                                                                       tile-sandbox.png, tile-online.png
  audio_rel.rpf!/config/sounds.dat54.rel + sfx/audio_misc.rpf!/resident/frontend.awc -> ui/sounds/select.wav, back.wav
                                                                                       (ui_sounds.py, xma2_decode.py)
  audio_rel.rpf!/config/sounds.dat54.rel + sfx/audio_music.rpf!/interactive_music/td_greyhound.awc
                                                                                    -> ui/music/launcher-music.wav (ui_music.py)
and writes them under NEW_DIR/ui with NEW_DIR/ui/manifest.txt ("sha256 size name" for every file directly in ui/, the gate the
launcher's UiAssets checks) and NEW_DIR/report.json (origins, sizes, output hashes; no disc content). The app imports them as the
bundle's "ui-asset" components (BUNDLE-FORMAT.md) and reads them in place from files/game-install/bundle/ui.

Same disc, same bytes, on every host: integer arithmetic, IEEE + - * / (no libm, no FMA: Python evaluates one operation at a
time), Decimal for the few transcendental constants, and deflate stored blocks in the PNGs. The XMA2 sounds are decoded by
xma2_decode.py, a Python transcription of the FFmpeg decoder the xma-decoder stage builds for the game library (that stage's
libraries are for the phone and cannot run on the computer); its Huffman tables are read from the same pinned FFmpeg source
archive. Nothing of the game is in these files; nothing is uploaded. Exit 0 done, 1 refused or failed (the message says why).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
for _p in ('tools/own_disc', 'tools/rpf7_inventory', 'tools/host_guest_trace'):
    if str(ROOT / _p) not in sys.path:
        sys.path.insert(0, str(ROOT / _p))
import host_support  # noqa: E402
import ui_fonts  # noqa: E402
import ui_pictures  # noqa: E402

SOURCES = {
    'fonts': 'xbox360a.rpf!/data/cdimages/scaleform_platform_360.rpf!/font_lib_efigs_360.gfx',
    'glyphs': 'xbox360b.rpf!/textures/gfxfontlib_360.xtd',
    'loadingscreen0': 'xbox360a.rpf!/data/cdimages/scaleform_frontend.rpf!/loadingscreen0.xtd',
    'loadingscreen1': 'xbox360a.rpf!/data/cdimages/scaleform_frontend.rpf!/loadingscreen1.xtd',
    'loadingscreen5': 'xbox360a.rpf!/data/cdimages/scaleform_frontend.rpf!/loadingscreen5.xtd',
    'loadingscreen6': 'xbox360a.rpf!/data/cdimages/scaleform_frontend.rpf!/loadingscreen6.xtd',
    'sounds': 'audio_rel.rpf!/config/sounds.dat54.rel',
    'bank': 'sfx/audio_misc.rpf!/resident/frontend.awc',
    'music': 'sfx/audio_music.rpf!/interactive_music/td_greyhound.awc',
}
PARTS = {'fonts': ['fonts'], 'glyphs': ['glyphs'], 'art': ['loadingscreen0', 'loadingscreen1', 'loadingscreen5', 'loadingscreen6'],
         'sounds': ['sounds', 'bank'], 'music': ['sounds', 'music']}
RESOURCE_PREFIX = 16          # bytes before the XMem frames of a 360 resource payload


class Refusal(Exception):
    pass


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def read_disc_files(play: Path, generated: Path, xmem: Path, key_address: int, wanted: dict) -> dict:
    """{label: (catalog entry, payload)} for the wanted archive paths. Binary entries are decrypted and decompressed; resource
    entries come as stored (their XMem frames are decoded later with the resource's segment sizes). Only the archives on the
    wanted paths are opened."""
    from disc_builder import open_disc
    from build_shader_pack import View
    from rpf7_toc import parse_toc_header, _decrypt_tables
    from rpf7_catalog import parse_catalog_entries
    from rpf7_static_key_recover import recover_constant_stores, _MemoryKeyProvider
    from rpf7_extract import decode_binary_payload, decode_xmem_with_tool
    import rpf7_crypto

    secret, stores = recover_constant_stores(generated, target_address=key_address)
    del stores
    provider = _MemoryKeyProvider(secret)
    paths = {label: path.lower().split('!/') for label, path in wanted.items()}
    found = {}

    def decode(view, e):
        return decode_binary_payload(view.read(e.payload_offset, e.stored_size), encrypted=bool(e.encrypted),
                                     compressed=bool(e.compressed), logical_size=e.logical_size,
                                     key=secret if e.encrypted else None,
                                     xmem_decoder=lambda d, x, m: decode_xmem_with_tool(d, x, m, executable=xmem))

    def walk(view, trail):
        h = parse_toc_header(view.read(0, 16), view.size)
        eb, nb = view.read(16, h.entries_size), view.read(16 + h.entries_size, h.names_size)
        eb, nb, dec = _decrypt_tables(eb, nb, header=h, archive=Path('!/'.join(trail)), key_provider=provider,
                                      decryptor=rpf7_crypto.decrypt_aes256_ecb_prefix)
        for e in parse_catalog_entries(eb, nb, header=h, decrypted=dec):
            if e.kind == 'directory':
                continue
            here = trail + [e.path.lower()]
            for label, parts in paths.items():
                if parts == here:
                    if e.kind == 'resource':
                        if e.encrypted:
                            raise Refusal('%s: an encrypted resource (not expected on this disc)' % wanted[label])
                        found[label] = (e, view.read(e.payload_offset, e.stored_size))
                    else:
                        found[label] = (e, decode(view, e))
            if e.kind == 'binary' and e.path.lower().endswith('.rpf') and any(p[:len(here)] == here for p in paths.values()):
                if e.encrypted or e.compressed:
                    d = decode(view, e)
                    walk(View(len(d), lambda p, n, d=d: d[p:p + n]), here)
                else:
                    walk(view.sub(e.payload_offset, e.logical_size), here)

    disc = open_disc(play)
    for top in sorted({p[0] for p in paths.values()}):
        stream, size = disc.open(top)
        with stream:
            base = stream.tell()
            view = View(size, lambda off, n, s=stream, b=base: host_support.pread(s.fileno(), n, b + off))
            if view.read(0, 4) != b'RPF7':
                raise Refusal('%s on the play disc is not an RPF7 archive' % top)
            walk(view, [top])
    missing = sorted(wanted[label] for label in wanted if label not in found)
    if missing:
        raise Refusal('not on this play disc: ' + ', '.join(missing))
    return found


def resource_segments(entry, payload: bytes, xmem: Path, where: str):
    """(system segment, graphics segment) of a 360 resource: its payload after the 16-byte prefix, XMem-decoded to the sizes
    its flag words give."""
    from rsc7_inspect import resource_size
    from rpf7_extract import decode_xmem_with_tool
    if entry.system_flags is None or entry.graphics_flags is None:
        raise Refusal('%s: not a resource' % where)
    ss, gs = resource_size(entry.system_flags), resource_size(entry.graphics_flags)
    data = decode_xmem_with_tool(payload[RESOURCE_PREFIX:], ss + gs, ss + gs, executable=xmem)
    if len(data) != ss + gs:
        raise Refusal('%s: decoded %d bytes, want %d' % (where, len(data), ss + gs))
    return data[:ss], data[ss:]


def build(found: dict, xmem: Path, ffmpeg_source: Path | None, parts: list) -> dict:
    """{relative output name: bytes}."""
    out = {}
    if 'fonts' in parts:
        for name, data in ui_fonts.ttf_files(found['fonts'][1]).items():
            out['ui/' + name] = data
    if 'glyphs' in parts:
        sysm, gfx = resource_segments(*found['glyphs'], xmem, SOURCES['glyphs'])
        for prompt, tex in ui_pictures.GLYPHS.items():
            W, H, rgba = ui_pictures.texture(sysm, gfx, tex, SOURCES['glyphs'])
            out['ui/glyph-%s.png' % prompt] = ui_pictures.png(W, H, 4, rgba)
    if 'art' in parts:
        for name, screen in ui_pictures.ART.items():
            sysm, gfx = resource_segments(*found[screen], xmem, SOURCES[screen])
            W, H, rgba = ui_pictures.texture(sysm, gfx, ui_pictures.ART_TEXTURE, SOURCES[screen])
            w, h, rgb = ui_pictures.art(W, H, rgba)
            out['ui/' + name] = ui_pictures.png(w, h, 3, rgb)
    if 'sounds' in parts:
        import ui_sounds
        if ffmpeg_source is None:
            raise Refusal('the sounds need --ffmpeg-source (the pinned FFmpeg source archive of the xma-decoder stage)')
        for name, data in ui_sounds.render_sounds(found['sounds'][1], found['bank'][1], ffmpeg_source).items():
            out['ui/sounds/' + name] = data
    if 'music' in parts:
        import ui_music
        if ffmpeg_source is None:
            raise Refusal('the music needs --ffmpeg-source (the pinned FFmpeg source archive of the xma-decoder stage)')
        out['ui/music/launcher-music.wav'] = ui_music.render_music(found['sounds'][1], found['music'][1], ffmpeg_source)
    return out


def manifest(files: dict) -> bytes:
    lines = ['%s %d %s' % (sha(data), len(data), name[3:]) for name, data in sorted(files.items())
             if name.startswith('ui/') and '/' not in name[3:]]
    return ('\n'.join(lines) + '\n').encode('ascii')


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    for name in ('play', 'generated', 'xmem', 'output'):
        ap.add_argument('--' + name, type=Path, required=True)
    ap.add_argument('--ffmpeg-source', type=Path)
    ap.add_argument('--key-address', type=lambda s: int(s, 0), default=0x83bd4c98)
    # the default stays the 1.2.0 set: a profile made before the music (its stage argv names no parts) neither lists ui_music.py
    # among its tools nor pins the music; ui_stage.py names every part
    ap.add_argument('--parts', default='fonts,glyphs,art,sounds')
    a = ap.parse_args(argv)
    parts = [p for p in a.parts.split(',') if p]
    from disc_builder import BuilderError, check_private_path
    try:
        if not parts or any(p not in PARTS for p in parts):
            raise Refusal('--parts takes a comma list of ' + ', '.join(PARTS))
        out = a.output.absolute()
        check_private_path(out, 'ui stage output')
        if out.exists():
            raise Refusal('%s exists already: nothing is written over' % out)
        t0 = time.monotonic()
        wanted = {label: SOURCES[label] for p in parts for label in PARTS[p]}
        found = read_disc_files(a.play, a.generated, a.xmem, a.key_address, wanted)
        t1 = time.monotonic()
        files = build(found, a.xmem, a.ffmpeg_source, parts)
        if any(name.startswith('ui/') and '/' not in name[3:] for name in files):
            files['ui/manifest.txt'] = manifest(files)
        out.mkdir(parents=True)
        for name, data in sorted(files.items()):
            (out / name).parent.mkdir(parents=True, exist_ok=True)
            (out / name).write_bytes(data)
        report = dict(schema='own-disc-ui-assets/1', parts=parts,
                      sources={label: dict(origin='play:/' + path, bytes=found[label][0].logical_size)
                               for label, path in sorted(wanted.items())},
                      outputs={name: dict(bytes=len(data), sha256=sha(data)) for name, data in sorted(files.items())},
                      seconds=dict(read=round(t1 - t0, 2), make=round(time.monotonic() - t1, 2)))
        (out / 'report.json').write_text(json.dumps(report, indent=1, sort_keys=True) + '\n', encoding='utf-8', newline='\n')
        for name, data in sorted(files.items()):
            print('UI %s %d %s' % (sha(data)[:12], len(data), name))
        print('UI_DONE %d files (read %.1f s, make %.1f s)' % (len(files), t1 - t0, time.monotonic() - t1))
        return 0
    except (Refusal, BuilderError, ValueError, OSError) as e:
        print('UI_REFUSED %s' % e, file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
