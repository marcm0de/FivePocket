#!/usr/bin/env python3
# SPDX-License-Identifier: LGPL-2.1-or-later
#
# XMA2 decoder for mono streams: a Python transcription of FFmpeg's WMA Pro decoder.
# Transcribed from libavcodec/wmaprodec.c and libavcodec/wma.c of FFmpeg
# (https://github.com/xenia-project/FFmpeg at 15ece0882e8d5875051ff5b73c5a8326f7cee9f5):
#   wmaprodec.c  Copyright (c) 2007 Baptiste Coudurier, Benjamin Larsson, Ulion
#                Copyright (c) 2008 - 2011 Sascha Sommer, Benjamin Larsson
#   wma.c        Copyright (c) 2002-2007 The FFmpeg Project
# Transcription for the FivePocket builder, 2026.
#
# This file is free software; you can redistribute it and/or modify it under the terms of the GNU Lesser General Public
# License as published by the Free Software Foundation; either version 2.1 of the License, or (at your option) any later
# version. It is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied
# warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU Lesser General Public License for more
# details. A copy of the licence is COPYING.LGPLv2.1 in the same FFmpeg source archive (the builder package carries it).
"""XMA2 decoder for mono streams (the Xbox 360's WMA Pro packets), in Python, for the builder's ui stage (1.2.0).

This is a transcription of libavcodec/wmaprodec.c (with wma.c's run-level decoding) of the FFmpeg revision the
builder's xma-decoder stage pins (https://github.com/xenia-project/FFmpeg 15ece0882e8d5875051ff5b73c5a8326f7cee9f5).
That stage builds the decoder as a library for the phone, which the computer cannot run; the menu sounds the ui stage
writes are decoded on the computer, so the same decoder is written out here. FFmpeg is licensed under the GNU Lesser
General Public License version 2.1 or later, and so is this file (the header above). Its
Huffman, run and level tables are not copied here: they are read from libavcodec/wmaprodata.h in the same pinned source
archive (the xma-decoder stage's input), whose SHA-256 is checked first.

What it decodes: XMA2 packets of 2048 bytes as an AWC wave stores them (one stream, one channel), frames with their length
prefix, sub-frames of 512 / 256 / 128 samples, vector and run-level coded coefficients, the inverse MDCT and the sine
window, as FFmpeg does with the XMA2 decode flags 0x10d6. Stereo streams are refused.

Determinism (the ui stage pins its output): IEEE double + - * / only, sums with math.fsum (correctly rounded, the same in
every Python version), and the transcendental constants (the transform's cosines, the window's sines, 10^(x/20)) from
Decimal arithmetic at 40 digits, rounded once to double. So every host decodes the same samples. Doubles are used where
FFmpeg uses single precision; the samples differ from FFmpeg's by rounding only (about 1e-7).

    decode(packets: bytes, samples: int, sample_rate: int, tables: dict) -> list of floats in [-1, 1]
    decode_blocks([(packets, skip, count), ...], samples, sample_rate, tables) -> array('d')  (a streamed AWC's blocks)
    load_tables(ffmpeg_source_tar) -> dict
"""
import array
import decimal
import hashlib
import math
import re
import tarfile

FFMPEG_TABLES = 'libavcodec/wmaprodata.h'
FFMPEG_TABLES_SHA256 = '47817751e2d4c18c7abe5125cb6a45a325d3f6fe42e0fb14e51ed7ab5030a570'
TABLES = ('critical_freq', 'scale_huffcodes', 'scale_huffbits', 'scale_rl_huffcodes', 'scale_rl_huffbits', 'scale_rl_run',
          'scale_rl_level', 'coef0_huffcodes', 'coef0_huffbits', 'coef1_huffcodes', 'coef1_huffbits', 'coef0_run', 'coef0_level',
          'coef1_run', 'coef1_level', 'vec4_huffcodes', 'vec4_huffbits', 'vec2_huffcodes', 'vec2_huffbits', 'vec1_huffcodes',
          'vec1_huffbits', 'symbol_to_vec4', 'symbol_to_vec2')
PACKET = 2048
SAMPLES_PER_FRAME = 512
LOG2_FRAME_SIZE = 15            # av_log2(2048) + 4
MAX_NUM_SUBFRAMES = 4           # decode flags 0x10d6: (0xd6 & 0x38) >> 3 = 2
SUBFRAME_LEN_BITS = 2
MIN_SAMPLES_PER_SUBFRAME = SAMPLES_PER_FRAME // MAX_NUM_SUBFRAMES
NUM_BLOCK_SIZES = 3             # 512, 256, 128
BITS_PER_SAMPLE = 16
MAX_BANDS = 29


class XmaError(ValueError):
    pass


# ---- the tables, from the pinned FFmpeg source ----
def parse_tables(text: str) -> dict:
    text = re.sub(r'/\*.*?\*/', ' ', text, flags=re.S)
    text = re.sub(r'//[^\n]*', ' ', text)
    out = {}
    for name, body in re.findall(r'static const \w+ (\w+)\[[^\]]*\]\s*=\s*\{(.*?)\};', text, flags=re.S):
        if name in TABLES:
            out[name] = [int(v, 0) for v in body.replace('\n', ' ').split(',') if v.strip()]
    missing = [n for n in TABLES if n not in out]
    if missing:
        raise XmaError('the FFmpeg tables lack ' + ', '.join(missing))
    return out


def load_tables(source_archive) -> dict:
    with tarfile.open(source_archive) as tar:
        member = tar.getmember(FFMPEG_TABLES)
        data = tar.extractfile(member).read()
    if hashlib.sha256(data).hexdigest() != FFMPEG_TABLES_SHA256:
        raise XmaError('%s in %s is not the pinned FFmpeg file' % (FFMPEG_TABLES, source_archive))
    return parse_tables(data.decode('ascii'))


class Vlc:
    """A prefix code from FFmpeg's (code, length) tables, read one bit at a time."""

    def __init__(self, codes, bits):
        self.table = {(b, c): sym for sym, (c, b) in enumerate(zip(codes, bits)) if b}
        self.max = max(bits)

    def read(self, br):
        code = 0
        for n in range(1, self.max + 1):
            code = (code << 1) | br.bit()
            sym = self.table.get((n, code))
            if sym is not None:
                return sym
        raise XmaError('invalid prefix code')


# ---- exact constants ----
_D = decimal.Context(prec=40, rounding=decimal.ROUND_HALF_EVEN)
_PI = None
_COS = {}


def _pi():
    global _PI
    if _PI is None:                       # Machin: pi = 16 atan(1/5) - 4 atan(1/239)
        def atan_inv(x):
            x = _D.create_decimal(x)
            total = term = _D.divide(1, x)
            x2, n, sign = _D.multiply(x, x), 1, -1
            while True:
                term = _D.divide(term, x2)
                n += 2
                delta = _D.divide(term, n)
                if delta == 0 or delta.adjusted() < -45:
                    return total
                total = _D.add(total, delta) if sign > 0 else _D.subtract(total, delta)
                sign = -sign
        _PI = _D.subtract(_D.multiply(16, atan_inv(5)), _D.multiply(4, atan_inv(239)))
    return _PI


def _taylor(a, cosine):
    a2 = _D.multiply(a, a)
    term = _D.create_decimal(1) if cosine else a
    total, n = term, 0 if cosine else 1
    while True:
        term = _D.divide(_D.multiply(term, a2), (n + 1) * (n + 2))
        n += 2
        if term == 0 or term.adjusted() < -45:
            return total
        total = _D.subtract(total, term) if (n // 2) % 2 else _D.add(total, term)


def cos_q(m: int, u: int) -> float:
    """cos(pi * u / (4m)) as the double nearest the exact value."""
    key = (m, u)
    if key not in _COS:
        v = u % (8 * m)
        sign = 1
        if v > 4 * m:                       # cos(2pi - a) = cos(a)
            v = 8 * m - v
        if v > 2 * m:                       # cos(pi - a) = -cos(a)
            v, sign = 4 * m - v, -1
        a = _D.divide(_D.multiply(_pi(), v), 4 * m)
        if v <= m:
            x = _taylor(a, True)
        else:                               # cos(a) = sin(pi/2 - a)
            x = _taylor(_D.divide(_D.multiply(_pi(), 2 * m - v), 4 * m), False)
        _COS[key] = sign * float(x)
    return _COS[key]


_EXP10 = {}


def exp10_20(e: int) -> float:
    """10^(e/20) as a double (FFmpeg: ff_exp10(exp / 20.0))."""
    if e not in _EXP10:
        _EXP10[e] = float(_D.power(_D.create_decimal(10), _D.divide(e, 20)))
    return _EXP10[e]


# ---- bits ----
class Bits:
    """MSB-first reader over a bit string held in an int (value, length)."""

    def __init__(self, value: int, length: int):
        self.v, self.n, self.pos = value, length, 0

    @classmethod
    def of(cls, data: bytes):
        return cls(int.from_bytes(data, 'big'), len(data) * 8)

    def left(self):
        return self.n - self.pos

    def read(self, k):
        if k == 0:
            return 0
        if self.pos + k > self.n:
            raise XmaError('read past the end of the data')
        self.pos += k
        return (self.v >> (self.n - self.pos)) & ((1 << k) - 1)

    def bit(self):
        return self.read(1)

    def peek(self, k):
        return (self.v >> (self.n - self.pos - k)) & ((1 << k) - 1)

    def take(self, k):
        """(value, k): the next k bits, for the frame reservoir."""
        return self.read(k), k

    def sbits(self, k):
        x = self.read(k)
        return x - (1 << k) if x >> (k - 1) else x


def av_log2(x):
    return x.bit_length() - 1


# ---- the decoder ----
class Decoder:
    def __init__(self, t: dict, sample_rate: int):
        self.t = t
        self.sf_vlc = Vlc(t['scale_huffcodes'], t['scale_huffbits'])
        self.sf_rl_vlc = Vlc(t['scale_rl_huffcodes'], t['scale_rl_huffbits'])
        self.coef_vlc = [Vlc(t['coef0_huffcodes'], t['coef0_huffbits']), Vlc(t['coef1_huffcodes'], t['coef1_huffbits'])]
        self.vec4 = Vlc(t['vec4_huffcodes'], t['vec4_huffbits'])
        self.vec2 = Vlc(t['vec2_huffcodes'], t['vec2_huffbits'])
        self.vec1 = Vlc(t['vec1_huffcodes'], t['vec1_huffbits'])
        rate = 48000 if sample_rate > 44100 else 44100 if sample_rate > 32000 else 32000 if sample_rate > 24000 else 24000
        self.num_sfb, self.sfb_offsets = [], []
        for i in range(NUM_BLOCK_SIZES):
            sl = SAMPLES_PER_FRAME >> i
            offs, band = [0], 1
            for x in range(MAX_BANDS - 1):
                if offs[band - 1] >= sl:
                    break
                o = ((sl * 2 * t['critical_freq'][x]) // rate + 2) & ~3
                if o > offs[band - 1]:
                    offs.append(o)
                    band += 1
                if o >= sl:
                    break
            offs[band - 1] = sl
            del offs[band:]
            self.sfb_offsets.append(offs)
            self.num_sfb.append(band - 1)
        self.sf_offsets = [[[0] * MAX_BANDS for _ in range(NUM_BLOCK_SIZES)] for _ in range(NUM_BLOCK_SIZES)]
        for i in range(NUM_BLOCK_SIZES):
            for b in range(self.num_sfb[i]):
                offset = ((self.sfb_offsets[i][b] + self.sfb_offsets[i][b + 1] - 1) << i) >> 1
                for x in range(NUM_BLOCK_SIZES):
                    v = 0
                    while self.sfb_offsets[x][v + 1] << x < offset:
                        v += 1
                    self.sf_offsets[i][x][b] = v
        # channel state (one channel)
        self.out = [0.0] * (8192 + 4096)
        self.prev_block_len = SAMPLES_PER_FRAME
        self.saved_sf = [[0] * MAX_BANDS, [0] * MAX_BANDS]
        self.sf_idx = 0
        self.ch_table_idx = 0
        self.reuse_sf = 0
        self.sf_step = 1
        self.max_sf = 0
        self.scale_factors = self.saved_sf[1]
        self.quant_step = 0

    # -- one frame --
    def frame(self, br: Bits):
        """Decodes one frame from br (the frame alone, with its length prefix). Returns (512 samples, more frames)."""
        length = br.read(LOG2_FRAME_SIZE)
        subframes = self.tile_header(br)
        br.read(8)                                    # dynamic range compression gain (not applied, as FFmpeg)
        if br.bit():                                  # start / end skip (read, not applied, as FFmpeg)
            if br.bit():
                br.read(av_log2(SAMPLES_PER_FRAME * 2))
            if br.bit():
                br.read(av_log2(SAMPLES_PER_FRAME * 2))
        self.reuse_sf = 0
        offset = 0
        for n, sl in enumerate(subframes):
            self.subframe(br, offset, sl, n)
            offset += sl
        samples = self.out[:SAMPLES_PER_FRAME]
        half = SAMPLES_PER_FRAME >> 1
        self.out[:half] = self.out[SAMPLES_PER_FRAME:SAMPLES_PER_FRAME + half]
        if length != br.pos + 2:
            raise XmaError('frame length %d does not match the %d bits read' % (length, br.pos + 2))
        br.read(length - br.pos - 1)
        return samples, br.bit()

    def subframe_length(self, br, offset):
        if offset == SAMPLES_PER_FRAME - MIN_SAMPLES_PER_SUBFRAME:
            return MIN_SAMPLES_PER_SUBFRAME
        shift = 1 + br.read(SUBFRAME_LEN_BITS - 1) if br.bit() else 0     # max_subframe_len_bit (4 subframes)
        sl = SAMPLES_PER_FRAME >> shift
        if sl < MIN_SAMPLES_PER_SUBFRAME:
            raise XmaError('broken frame: sub-frame length %d' % sl)
        return sl

    def tile_header(self, br):
        br.bit()                                      # fixed channel layout (one channel: always "contains")
        lens, total = [], 0
        while total < SAMPLES_PER_FRAME:
            sl = self.subframe_length(br, total)
            lens.append(sl)
            total += sl
            if total > SAMPLES_PER_FRAME:
                raise XmaError('broken frame: channel longer than the frame')
        return lens

    # -- one sub-frame --
    def subframe(self, br, offset, sl, cur):
        table_idx = av_log2(SAMPLES_PER_FRAME // sl)
        num_bands = self.num_sfb[table_idx]
        sfb = self.sfb_offsets[table_idx]
        base = offset + (SAMPLES_PER_FRAME >> 1)      # coefficients / IMDCT output at out[base:base+sl]
        esc_len = av_log2(sl - 1) + 1
        if br.bit():                                  # extended header: fill bits
            fill = br.read(2)
            if not fill:
                fill = br.read(br.read(4)) + 1
            if br.pos + fill > br.n:
                raise XmaError('invalid number of fill bits')
            br.read(fill)
        if br.bit():
            raise XmaError('reserved bit set')
        transmit = br.bit()
        num_vec = sl
        if transmit:
            quant_step = 90 * BITS_PER_SAMPLE >> 4
            transmit_num_vec = br.bit()
            if transmit_num_vec:
                num_vec = br.read(av_log2((sl + 3) // 4) + 1) << 2
                if num_vec > sl:
                    raise XmaError('vector coefficient count %d too large' % num_vec)
            step = br.sbits(6)
            quant_step += step
            if step in (-32, 31):
                sign = (1 if step == 31 else 0) - 1
                quant = 0
                while br.pos + 5 < br.n:
                    step = br.read(5)
                    if step != 31:
                        break
                    quant += 31
                quant_step += ((quant + step) ^ sign) - sign
            self.quant_step = quant_step
            self.scale_factor_decode(br, table_idx, num_bands, cur)
        coeffs = [0.0] * sl
        if transmit and br.pos < br.n:
            self.coefficients(br, coeffs, sl, num_vec, transmit_num_vec, esc_len)
        if transmit:
            tmp = [0.0] * sl
            for b in range(num_bands):
                end = min(sfb[b + 1], sl)
                q = exp10_20(self.quant_step - (self.max_sf - self.scale_factors[b]) * self.sf_step)
                for k in range(sfb[b], end):
                    tmp[k] = coeffs[k] * q
            self.imdct_half(tmp, sl, base)
        else:
            self.out[base:base + sl] = coeffs
        self.window(base, sl)

    def scale_factor_decode(self, br, table_idx, num_bands, cur):
        sf = self.saved_sf[1 - self.sf_idx]
        self.scale_factors = sf
        if self.reuse_sf:
            offs = self.sf_offsets[table_idx][self.ch_table_idx]
            src = self.saved_sf[self.sf_idx]
            for b in range(num_bands):
                sf[b] = src[offs[b]]
        if not cur or br.bit():
            if not self.reuse_sf:
                self.sf_step = br.read(2) + 1
                val = 45 // self.sf_step
                for b in range(num_bands):
                    val += self.sf_vlc.read(br) - 60
                    sf[b] = val
            else:
                i = 0
                while i < num_bands:
                    idx = self.sf_rl_vlc.read(br)
                    if idx == 0:
                        code = br.read(14)
                        val, sign, skip = code >> 6, (code & 1) - 1, (code & 0x3f) >> 1
                    elif idx == 1:
                        break
                    else:
                        skip, val = self.t['scale_rl_run'][idx], self.t['scale_rl_level'][idx]
                        sign = br.bit() - 1
                    i += skip
                    if i >= num_bands:
                        raise XmaError('invalid scale factor coding')
                    sf[i] += (val ^ sign) - sign
                    i += 1
            self.sf_idx = 1 - self.sf_idx
            self.ch_table_idx = table_idx
            self.reuse_sf = 1
        self.max_sf = max(sf[:num_bands])

    def large_val(self, br):
        n = 8
        if br.bit():
            n += 8
            if br.bit():
                n += 8
                if br.bit():
                    n += 7
        return br.read(n)

    def coefficients(self, br, coeffs, sl, num_vec, transmit_num_vec, esc_len):
        t = self.t
        table = br.bit()
        vlc = self.coef_vlc[table]
        run, level = (t['coef1_run'], t['coef1_level']) if table else (t['coef0_run'], t['coef0_level'])
        rl_mode, cur, zeros = False, 0, 0
        while (transmit_num_vec or not rl_mode) and cur + 3 < num_vec:
            idx = self.vec4.read(br)
            if idx == len(t['vec4_huffbits']) - 1:
                vals = []
                for _ in range(2):
                    idx = self.vec2.read(br)
                    if idx == len(t['vec2_huffbits']) - 1:
                        for _ in range(2):
                            v = self.vec1.read(br)
                            if v == len(t['vec1_huffbits']) - 1:
                                v += self.large_val(br)
                            vals.append(v)
                    else:
                        s = t['symbol_to_vec2'][idx]
                        vals += [s >> 4, s & 0xF]
            else:
                s = t['symbol_to_vec4'][idx]
                vals = [s >> 12, (s >> 8) & 0xF, (s >> 4) & 0xF, s & 0xF]
            for v in vals:
                if v:
                    coeffs[cur] = float(v) if br.bit() else -float(v)
                    zeros = 0
                else:
                    coeffs[cur] = 0.0
                    zeros += 1
                    rl_mode = rl_mode or zeros > sl >> 8
                cur += 1
        if cur < sl:                                  # ff_wma_run_level_decode (version 1); its error result is ignored, as in FFmpeg
            mask = sl - 1
            offset = cur
            while offset < sl:
                code = vlc.read(br)
                if code > 1:
                    offset += run[code]
                    coeffs[offset & mask] = float(level[code]) if br.bit() else -float(level[code])
                elif code == 1:
                    break
                else:
                    lv = self.large_val(br)
                    if br.bit():
                        if br.bit():
                            if br.bit():
                                break                 # broken escape sequence
                            offset += br.read(esc_len) + 4
                        else:
                            offset += br.read(2) + 1
                    coeffs[offset & mask] = float(lv) if br.bit() else -float(lv)
                offset += 1

    def imdct_half(self, x, sl, base):
        """FFmpeg's imdct_half for N = 2 * sl with the decoder's scale: y[m] = -scale * sum_k x[k] cos(pi/sl (m + sl + 1/2)(k + 1/2)),
        scale = 1 / (sl / 2) / 2^15 (exact in binary)."""
        scale = 2.0 / sl / 32768.0
        nz = [(k, v) for k, v in enumerate(x) if v != 0.0]
        out = self.out
        for m in range(sl):
            a = 2 * m + 2 * sl + 1
            out[base + m] = -scale * math.fsum(v * cos_q(sl, a * (2 * k + 1)) for k, v in nz)

    def window(self, base, sl):
        winlen = self.prev_block_len
        start = base - (winlen >> 1)
        if sl < winlen:
            start += (winlen - sl) >> 1
            winlen = sl
        half = winlen >> 1
        out = self.out
        for k in range(half):
            wi = cos_q(winlen, 2 * winlen - (2 * k + 1))                       # sin((k + 1/2) pi / (2 winlen))
            wj = cos_q(winlen, 2 * winlen - (2 * (winlen - 1 - k) + 1))
            s0, s1 = out[start + k], out[start + winlen - 1 - k]
            out[start + k] = s0 * wj - s1 * wi
            out[start + winlen - 1 - k] = s0 * wi + s1 * wj
        self.prev_block_len = sl


class Stream:
    """One mono XMA2 stream fed in pieces of whole packets: the decoder state and a frame that continues in the next packet
    are kept between pieces, so the pieces decode as one stream."""

    def __init__(self, tables: dict, sample_rate: int):
        self.dec = Decoder(tables, sample_rate)
        self.reservoir = None                         # (value, bits) of a frame that continues in the next packet
        self.skip = 0

    def feed(self, packets: bytes) -> list:
        if len(packets) % PACKET or not packets:
            raise XmaError('the stream is not whole %d-byte packets' % PACKET)
        dec, out = self.dec, []
        for p in range(0, len(packets), PACKET):
            if self.skip:
                self.skip -= 1
                continue
            br = Bits.of(packets[p:p + PACKET])
            br.read(6)                                # frame count
            prev = br.read(LOG2_FRAME_SIZE)
            br.read(3)
            self.skip = br.read(8)
            done = False
            if prev > 0:
                if prev >= br.left():
                    prev, done = br.left(), True
                if self.reservoir is not None:
                    v, n = br.take(prev)
                    frame = Bits((self.reservoir[0] << n) | v, self.reservoir[1] + n)
                    s, more = dec.frame(frame)
                    out += s
                else:
                    br.read(prev)
            self.reservoir = None
            while not done:
                left = br.left()
                size = br.peek(LOG2_FRAME_SIZE) if left > LOG2_FRAME_SIZE else 0
                if not size or size > left:
                    break
                v, n = br.take(size)
                s, more = dec.frame(Bits(v, n))
                out += s
                if not more:
                    break
            if br.left() > 0:
                self.reservoir = br.take(br.left())
        return out

    def end(self) -> list:
        """The end of the stream: the last half frame of the transform, then silence for the other half."""
        return self.dec.out[:SAMPLES_PER_FRAME >> 1] + [0.0] * (SAMPLES_PER_FRAME >> 1)


def decode(packets: bytes, samples: int, sample_rate: int, tables: dict) -> list:
    """The first `samples` samples of a mono XMA2 stream (whole 2048-byte packets), as FFmpeg's decoder gives them."""
    if len(packets) % PACKET or not packets:
        raise XmaError('the stream is not whole %d-byte packets' % PACKET)
    stream = Stream(tables, sample_rate)
    out = stream.feed(packets)
    if len(out) < samples:
        out += stream.end()
    if len(out) < samples:
        raise XmaError('the stream holds %d samples, its header says %d' % (len(out), samples))
    return out[:samples]


def decode_blocks(blocks, samples: int, sample_rate: int, tables: dict) -> array.array:
    """The first `samples` samples of a mono XMA2 stream that a streamed AWC stores in blocks: blocks is a list of (packets,
    skip, count); a block keeps samples skip..count of what its packets decode to. A packet that holds the boundary between
    two blocks is stored in both: the first block decodes past its count (the rest is dropped) and the next one decodes the
    packet's frames before the boundary again, as its `skip` samples.

    One decoder runs through every block. A block whose skip is 0 continues the transform of the block before (a fresh decoder
    would fade its first half frame in from silence); a block that repeats a packet re-primes the transform within its skip.
    That holds only for a stream laid out as described, so anything else is refused: a block that starts with a repeated
    packet (it follows a block that decoded past its count) must skip at least one frame, a block that skips must follow one
    that decoded past its count, the first block skips nothing, every block decodes to at least its count, and the stream
    holds at least `samples`."""
    stream = Stream(tables, sample_rate)
    kept = array.array('d')
    extra = 0
    for i, (packets, skip, count) in enumerate(blocks):
        if not 0 <= skip <= count or (skip > 0) != (extra > 0) or 0 < skip < SAMPLES_PER_FRAME:
            raise XmaError('block %d: skips %d samples after a block that decoded %d past its count' % (i, skip, extra))
        out = stream.feed(packets)
        if i == len(blocks) - 1 and len(out) < count:
            out += stream.end()
        if len(out) < count:
            raise XmaError('block %d decodes to %d samples, its header says %d' % (i, len(out), count))
        extra = len(out) - count
        kept.extend(out[skip:count])
    if len(kept) < samples:
        raise XmaError('the stream holds %d samples, its header says %d' % (len(kept), samples))
    del kept[samples:]
    return kept
