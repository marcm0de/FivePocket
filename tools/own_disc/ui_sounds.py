#!/usr/bin/env python3
"""The launcher's menu sounds, from the player's own disc (builder ui stage, 1.2.0).

Input: audio_rel.rpf!/config/sounds.dat54.rel (the sound tables) and sfx/audio_misc.rpf!/resident/frontend.awc (the resident
frontend wave bank), both as the disc's archives store them, decoded. Output: select.wav and back.wav, the sample-based sounds of
HUD_FRONTEND_DEFAULT_SOUNDSET that the pause menu plays, rendered as the game builds them: the sound set's entry -> Wrapper (6)
/ Multitrack (13) -> Simple (12) waves of the frontend bank, each with its header's Volume (centi-dB) and Pitch (cents) added down
the tree, the wave's own headroom, and its speaker mask (bit 0 left, bit 1 right). Header layout: CodeWalker RelFile.cs
RelSoundHeader (bit-gated fields; a flags byte 0xAA skips its group). The XMA2 waves are decoded by xma2_decode.py.

NAV_UP_DOWN, NAV_LEFT_RIGHT and ERROR are synthesized by the game (ModularSynth, type 18): nothing on the disc is a recording of
them, so they are not made here (the launcher stays silent for them). Output: 16-bit stereo RIFF WAVE at 48000 Hz.

Determinism: IEEE + - * / only, the decibel and cent factors from Decimal, sums written out in a fixed order. The same disc gives
the same bytes on every host. Made from the start-menu lane's ui_sounds.py (2026-11-17, our own code), with the decoder and the
factors made host-independent. Nothing of the game is in this file.
"""
import decimal
import struct

import xma2_decode

SOUNDSET = 'HUD_FRONTEND_DEFAULT_SOUNDSET'
NAMES = ['SELECT', 'BACK']          # the sample-based ones (the NAV and ERROR sounds are synthesized)
RATE = 48000
AWC_XMA2 = 5
_D = decimal.Context(prec=40, rounding=decimal.ROUND_HALF_EVEN)


class SoundError(ValueError):
    pass


def joaat(s):
    h = 0
    for c in s.lower().encode():
        h = (h + c) & 0xffffffff
        h = (h + (h << 10)) & 0xffffffff
        h ^= h >> 6
    h = (h + (h << 3)) & 0xffffffff
    h ^= h >> 11
    return (h + (h << 15)) & 0xffffffff


CONTAINER = joaat('resident/frontend')


def power(base, exponent_num, exponent_den):
    """base ** (num / den) as the double nearest a 40-digit Decimal result."""
    return float(_D.power(_D.create_decimal(base), _D.divide(exponent_num, exponent_den)))


# ---- the frontend wave bank (AWC, big-endian on the 360) ----
def awc_streams(b):
    if b[:4] != b'TADA':
        raise SoundError('not a big-endian AWC')
    _ver, flags, n, _ = struct.unpack('>HHII', b[4:16])
    p = 16 + (2 * n if flags & 1 else 0)
    infos = []
    for _ in range(n):
        raw, = struct.unpack('>I', b[p:p + 4])
        p += 4
        infos.append((raw & 0x1FFFFFFF, raw >> 29))
    out = {}
    for sid, cc in infos:
        ch = {}
        for _ in range(cc):
            v, = struct.unpack('>Q', b[p:p + 8])
            p += 8
            ch[v >> 56] = ((v >> 28) & 0x0FFFFFFF, v & 0x0FFFFFFF)
        out[sid] = ch
    return out


def decode_wave(awc, streams, wave, tables):
    """(samples, sample rate, headroom) of one bank wave."""
    ch = streams[wave]
    if 0xFA not in ch or 0x55 not in ch:
        raise SoundError('wave %08x: no format or data chunk' % wave)
    size, off = ch[0xFA]
    samples, _loop, rate, headroom, _lb, _le, _pe, _pb, codec = struct.unpack('>IiHhHHHBB', awc[off:off + 20])
    if codec != AWC_XMA2:
        raise SoundError('wave %08x: codec %d, not XMA2' % (wave, codec))
    dsize, doff = ch[0x55]
    return xma2_decode.decode(awc[doff:doff + dsize], samples, rate, tables), rate, headroom


# ---- the sound tables (RelFile type 54, big-endian) ----
FIELDS = [(0, 'I'), (1, 'H'), (2, 'h'), (3, 'H'), (4, 'h'), (5, 'H'), (6, 'H'), (7, 'H'), (8, 'h'), (9, 'H'), (10, 'i'), (11, 'i'),
          (12, 'H'), (13, 'H'), (14, 'H'), (15, 'I'), (16, 'H'), (17, 'H'), (18, 'H'), (19, 'H'), (20, 'I'), (21, 'h'), (22, 'B'),
          (23, 'B'), (24, 'B'), (25, 'I'), (26, 'I'), (27, 'H'), (28, 'H'), (29, 'H'), (30, 'H'), (31, 'H')]
VOLUME, PITCH, SPEAKERS = 2, 4, 23


class Sounds:
    def __init__(self, b):
        self.b = b
        if len(b) < 8 or struct.unpack('>I', b[:4])[0] != 54:
            raise SoundError('not a sounds.dat54')
        self.dl, = struct.unpack('>I', b[4:8])

    def get(self, h):
        """The object of a name hash, from the index after the data (12-byte entries: hash, offset, length)."""
        b, k = self.b, 8 + self.dl
        key = struct.pack('>I', h)
        while True:
            k = b.find(key, k)
            if k < 0:
                return None
            if k + 12 <= len(b):
                _, o, n = struct.unpack('>III', b[k:k + 12])
                if 0 < n < 100000 and o + n <= self.dl:
                    return b[8 + o:8 + o + n]
            k += 1

    @staticmethod
    def header(obj):
        flags, = struct.unpack('>I', obj[1:5])
        p, h = 5, {}
        for bit, fmt in FIELDS:
            if (flags >> (8 * (bit // 8))) & 0xFF == 0xAA:
                continue
            if flags >> bit & 1:
                n = struct.calcsize(fmt)
                h[bit], = struct.unpack('>' + fmt, obj[p:p + n])
                p += n
        return h, p


def plan(sounds, h, vol=0, pitch=0, depth=0):
    """[(wave, volume centi-dB, pitch cents, speaker mask)] for a sound tree, or 'missing' / 'synth' / 'unsupported:...'."""
    obj = sounds.get(h)
    if obj is None or depth > 12:
        return 'missing'
    t = obj[0]
    hd, p = Sounds.header(obj)
    vol += hd.get(VOLUME, 0)
    pitch += hd.get(PITCH, 0)
    if t == 12:   # Simple: container, wave, slot
        c, w = struct.unpack('>II', obj[p:p + 8])
        if c != CONTAINER:
            return 'unsupported:container %08x' % c
        return [(w & 0x1FFFFFFF, vol, pitch, hd.get(SPEAKERS, 3))]
    if t == 6:    # Wrapper: its child
        child, = struct.unpack('>I', obj[p:p + 4])
        return plan(sounds, child, vol, pitch, depth + 1)
    if t == 13:   # Multitrack: a count byte, then the children, played together
        out = []
        for i in range(obj[p]):
            child, = struct.unpack('>I', obj[p + 1 + 4 * i:p + 5 + 4 * i])
            sub = plan(sounds, child, vol, pitch, depth + 1)
            if isinstance(sub, str):
                return sub
            out += sub
        return out
    return 'synth' if t == 18 else 'unsupported:%d' % t


def render(tracks, waves):
    """Mix the tracks into 16-bit stereo PCM at RATE: each wave resampled by its pitch (linear), scaled by its volume and
    headroom, sent to its speakers. A sum above -1 dBFS is scaled down as a whole (the layers' balance kept), never clipped."""
    parts = []
    for wave, vol, pitch, mask in tracks:
        pcm, rate, headroom = waves[wave]
        step = rate * power(2, pitch, 1200) / RATE
        gain = power(10, vol - headroom, 2000)
        n = int(len(pcm) / step)
        out = []
        for i in range(n):
            x = i * step
            j = int(x)
            f = x - j
            a = pcm[j]
            b = pcm[j + 1] if j + 1 < len(pcm) else 0.0
            out.append((a + (b - a) * f) * gain)
        parts.append((out, mask & 1, mask & 2))
    length = max(len(o) for o, _, _ in parts)
    left, right = [0.0] * length, [0.0] * length
    for o, to_left, to_right in parts:              # one track after another: a fixed order of additions
        for i, v in enumerate(o):
            if to_left:
                left[i] += v
            if to_right:
                right[i] += v
    peak = max(max(abs(v) for v in left), max(abs(v) for v in right)) or 1.0
    scale = min(1.0, power(10, -1, 20) / peak)
    frames = bytearray()
    for lv, rv in zip(left, right):
        frames += struct.pack('<hh', int(round(lv * scale * 32767)), int(round(rv * scale * 32767)))
    return bytes(frames)


def wav(frames, rate=RATE):
    """16-bit stereo PCM frames as a RIFF WAVE file."""
    fmt = struct.pack('<HHIIHH', 1, 2, rate, rate * 4, 4, 16)
    body = b'WAVE' + b'fmt ' + struct.pack('<I', len(fmt)) + fmt + b'data' + struct.pack('<I', len(frames)) + frames
    return b'RIFF' + struct.pack('<I', len(body)) + body


def render_sounds(sounds_dat54: bytes, frontend_awc: bytes, ffmpeg_source) -> dict:
    """{'select.wav': bytes, 'back.wav': bytes}. Refuses (SoundError / XmaError) when a sound is not the sample-based tree
    this expects, so a different disc never gives silent or wrong files."""
    tables = xma2_decode.load_tables(ffmpeg_source)
    sounds = Sounds(sounds_dat54)
    streams = awc_streams(frontend_awc)
    sset = sounds.get(joaat(SOUNDSET))
    if sset is None:
        raise SoundError('%s is not in the sound tables' % SOUNDSET)
    out, waves = {}, {}
    for name in NAMES:
        k = sset.find(struct.pack('>I', joaat(name)))
        if k < 0:
            raise SoundError('%s is not in %s' % (name, SOUNDSET))
        snd, = struct.unpack('>I', sset[k + 4:k + 8])
        tracks = plan(sounds, snd)
        if isinstance(tracks, str):
            raise SoundError('%s: %s' % (name, tracks))
        for w, _, _, _ in tracks:
            if w not in streams:
                raise SoundError('%s: wave %08x is not in the frontend bank' % (name, w))
            if w not in waves:
                waves[w] = decode_wave(frontend_awc, streams, w, tables)
        out[name.lower() + '.wav'] = wav(render(tracks, waves))
    return out
