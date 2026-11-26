#!/usr/bin/env python3
"""The iOS / Duo app icon from one of the owner's colourway logos (next update, 2026-11-22).

    app_icon.py --logo LOGO.png --ground RRGGBB --out ICON.png
    app_icon.py --logo LOGO.png --sky TOP,MIDDLE,BOTTOM --out ICON.png     (RRGGBB each)

The Mac builder's window shows a logo per edition and backdrop (five-pocket-<edition>-<backdrop>.png, square RGBA with a
transparent surround). The app the builder makes carries the logo of the backdrop the player picked as its icon. An iOS icon
is opaque and 1024 x 1024 (ios_app.py compile_icon refuses anything else), so the logo is scaled to 1024 x 1024 whole (no
crop, no margin added: the art as drawn) and laid over the ground colour (the project's icon ground, 0B0D0C), or with --sky over
the colourway's sky (owner 2026-11-22, as the Android icons: a vertical gradient through three stops sampled from the logo's own V
fill, top -> middle -> bottom, e.g. Pink/Blue Dusk F9B4B4,D9A3B7,407299).

Arithmetic: integers only. The scale is an exact area average: output pixel x covers input [x * W / 1024, (x + 1) * W / 1024),
every input pixel weighted by the length it shares with that interval (units of 1/1024 input pixel), rows first, then columns.
Colour is averaged with alpha as weight (premultiplied), then the uncovered part takes the ground; each channel is rounded half
up. The PNG is written with stored deflate blocks (ui_pictures.png), so the same logo gives the same bytes on every host.
Input: 8-bit RGB or RGBA PNG, not interlaced. Exit 0: written; 2: refused.
"""
from __future__ import annotations

import argparse
import struct
import sys
import zlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ui_pictures import png  # noqa: E402

SIZE = 1024
GROUND = (0x0B, 0x0D, 0x0C)  # the project's icon ground (public-repo/icon-concepts/FivePocket-AppIcon-1024.png)


class Refused(Exception):
    pass


def read_png(data: bytes):
    """(width, height, channels, rows) of an 8-bit RGB/RGBA non-interlaced PNG; rows are bytes per row, unfiltered"""
    if data[:8] != b'\x89PNG\r\n\x1a\n':
        raise Refused('not a PNG')
    pos, ihdr, idat = 8, None, bytearray()
    while pos < len(data):
        if pos + 8 > len(data):
            raise Refused('truncated PNG')
        n, kind = struct.unpack('>I4s', data[pos:pos + 8])
        body = data[pos + 8:pos + 8 + n]
        if len(body) != n or zlib.crc32(kind + body) & 0xFFFFFFFF != struct.unpack('>I', data[pos + 8 + n:pos + 12 + n])[0]:
            raise Refused(f'PNG chunk {kind!r} damaged')
        if kind == b'IHDR':
            ihdr = struct.unpack('>IIBBBBB', body)
        elif kind == b'IDAT':
            idat += body
        elif kind == b'IEND':
            break
        pos += 12 + n
    if ihdr is None:
        raise Refused('PNG without IHDR')
    w, h, depth, colour, _, _, interlace = ihdr
    if depth != 8 or colour not in (2, 6) or interlace != 0:
        raise Refused('only 8-bit RGB/RGBA PNGs without interlace are read')
    ch = 4 if colour == 6 else 3
    raw = zlib.decompress(bytes(idat))
    stride = w * ch
    if len(raw) != (stride + 1) * h:
        raise Refused('PNG image data has the wrong size')
    rows, prev = [], bytes(stride)
    for y in range(h):
        f, line = raw[y * (stride + 1)], bytearray(raw[y * (stride + 1) + 1:(y + 1) * (stride + 1)])
        if f == 1:
            for i in range(ch, stride):
                line[i] = (line[i] + line[i - ch]) & 255
        elif f == 2:
            for i in range(stride):
                line[i] = (line[i] + prev[i]) & 255
        elif f == 3:
            for i in range(stride):
                line[i] = (line[i] + ((line[i - ch] if i >= ch else 0) + prev[i]) // 2) & 255
        elif f == 4:
            for i in range(stride):
                a = line[i - ch] if i >= ch else 0
                b = prev[i]
                c = prev[i - ch] if i >= ch else 0
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                line[i] = (line[i] + (a if pa <= pb and pa <= pc else b if pb <= pc else c)) & 255
        elif f != 0:
            raise Refused(f'unknown PNG filter {f}')
        rows.append(bytes(line))
        prev = rows[-1]
    return w, h, ch, rows


def weights(src: int, dst: int):
    """for each output index: [(input index, weight)], weights in 1/dst input pixel, summing to src"""
    out = []
    for x in range(dst):
        lo, hi = x * src, (x + 1) * src          # the interval in units of 1/dst input pixel
        part, i = [], lo // dst
        while i * dst < hi:
            w = min(hi, (i + 1) * dst) - max(lo, i * dst)
            if w:
                part.append((i, w))
            i += 1
        out.append(part)
    return out


def sky_row(stops, y: int):
    """the sky's colour on row y: linear from stops[0] (y 0) to stops[1] (y (SIZE-1)/2) to stops[2] (y SIZE-1), rounded half up"""
    last = SIZE - 1
    num, a, b = (2 * y, stops[0], stops[1]) if 2 * y <= last else (2 * y - last, stops[1], stops[2])
    return tuple(a[k] + (2 * (b[k] - a[k]) * num + last) // (2 * last) for k in range(3))


def icon(w: int, h: int, ch: int, rows, ground=GROUND, sky=None) -> bytes:
    """the opaque SIZE x SIZE RGB pixels: area average with alpha weight, over the ground (or the sky's row colour)"""
    wx, wy = weights(w, SIZE), weights(h, SIZE)
    # rows first: per input row, per output column, the sums (a*r, a*g, a*b, a) weighted by the horizontal share
    horizontal = []
    for row in rows:
        acc = []
        for part in wx:
            sr = sg = sb = sa = 0
            for i, wt in part:
                o = i * ch
                a = row[o + 3] if ch == 4 else 255
                aw = a * wt
                sr += aw * row[o]; sg += aw * row[o + 1]; sb += aw * row[o + 2]; sa += aw
            acc.append((sr, sg, sb, sa))
        horizontal.append(acc)
    full = w * h * 255                            # total weight (src * src in 1/dst^2 units) times full alpha
    out = bytearray(SIZE * SIZE * 3)
    for y, part in enumerate(wy):
        if sky:
            ground = sky_row(sky, y)
        for x in range(SIZE):
            sr = sg = sb = sa = 0
            for j, wt in part:
                r, g, b, a = horizontal[j][x]
                sr += wt * r; sg += wt * g; sb += wt * b; sa += wt * a
            rest = full - sa                          # the share no logo pixel covers: the ground
            o = (y * SIZE + x) * 3
            for k, s in enumerate((sr, sg, sb)):
                out[o + k] = (2 * (s + rest * ground[k]) + full) // (2 * full)
    return bytes(out)


def make(logo: Path, ground, out: Path, sky=None) -> str:
    w, h, ch, rows = read_png(logo.read_bytes())
    if w != h or w < SIZE:
        raise Refused(f'{logo}: the logo must be square and at least {SIZE} px ({w} x {h})')
    data = png(SIZE, SIZE, 3, icon(w, h, ch, rows, ground, sky))
    if out.exists():
        raise Refused(f'{out} exists')
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name(out.name + '.part')
    tmp.write_bytes(data)
    tmp.replace(out)
    import hashlib
    return hashlib.sha256(data).hexdigest()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--logo', type=Path, required=True)
    ap.add_argument('--ground', default='0B0D0C')
    ap.add_argument('--sky')
    ap.add_argument('--out', type=Path, required=True)
    a = ap.parse_args(argv)
    try:
        colour = lambda s: tuple(int(s[i:i + 2], 16) for i in (0, 2, 4)) if len(s) == 6 else (_ for _ in ()).throw(Refused(f'{s!r} is not RRGGBB'))
        ground = colour(a.ground)
        sky = [colour(s) for s in a.sky.split(',')] if a.sky else None
        if sky is not None and len(sky) != 3:
            raise Refused('--sky is TOP,MIDDLE,BOTTOM')
        print(f'{make(a.logo, ground, a.out, sky)}  {a.out}')
        return 0
    except (Refused, ValueError, zlib.error, OSError) as error:
        print(f'refused: {error}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
