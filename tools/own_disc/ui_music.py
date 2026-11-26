#!/usr/bin/env python3
"""The launcher's music, from the player's own disc (builder ui stage, next update): one layer of the score track "greyhound".

Input: audio_rel.rpf!/config/sounds.dat54.rel (the sound tables) and sfx/audio_music.rpf!/interactive_music/td_greyhound.awc (the
track's streamed wave bank), both as the disc's archives store them, decoded. Output: ui/music/launcher-music.wav.

The sound tables hold one StreamingSound (type 8) that plays the bank: its children are Multitracks (13) of two Simple sounds
(12) on the bank, the left and right halves of one stereo stem (Pan 307 and 53 degrees, speaker mask 0). In play the game's
interactive music sets each stem's level (its moods, in another table). The launcher plays ONE stem on its own (owner's choice,
1.3.0: "layer 6 only"): the 6th Multitrack in the StreamingSound's child order, plan[STEM] below, of the STEMS = 8 the tree
holds (any other count is refused: a different tree is not the one that choice was made on). The stem's two channels are scaled
by the Volume (centi-dB) of their headers down the tree and the wave's headroom, 10^((volume - headroom) / 2000), as ui_sounds.py
does for the menu sounds; a pitched stem is refused (the music is written at the bank's own rate, not resampled). The other
stems are not decoded.

The bank is a multichannel streamed AWC (big-endian; header flags bit 2): a format chunk (0x48: block count, block size, channel
count, then per channel id, samples, headroom, sample rate, codec in 16 bytes) and a data chunk (0x55) of blocks. Each block holds
per channel a record (a slot number, packet count, samples to skip, samples), then per packet the stream sample it starts at,
padding to 2048 bytes, and the 2048-byte XMA2 packets, channel after channel in record order. The slot number is not where a
channel's packets lie: in some blocks it runs one ahead of the packet counts (the disc's td_greyhound bank, blocks 1, 4, 5, 6,
12 and 15), while the packets and their entries follow the counts. Each channel's entries are checked against its packets'
frame counts, and its first entry plus its skip against the samples the blocks before keep, so a bank laid out otherwise is
refused. A block's first `skip` samples repeat the end of the block before; one decoder runs through all of a channel's blocks
(xma2_decode.decode_blocks), and the result is cut to the channel's sample count.

Output: 16-bit stereo RIFF WAVE at the bank's rate, the stem's left half to the left and its right half to the right, scaled as a
whole to peak at -1 dBFS (up as well as down: one stem alone is quieter than the mix), never clipped. A silent stem, or one whose
two sides are the same, is refused.

Determinism: IEEE + - * / only, the decibel factors from Decimal (ui_sounds.power), the sums in a fixed order. Decoding is the
slow part (about 8 microseconds a sample in Python, 3.2 million samples a channel): the two channels are decoded in up to
WORKERS processes and placed by side whatever order they finish in, so the same disc gives the same bytes on every host. Nothing of the game is in this file.
"""
import array
import concurrent.futures
import operator
import os
import struct
import sys

import xma2_decode
from ui_sounds import AWC_XMA2, SoundError, Sounds, joaat, power, wav

BANK = 'interactive_music/td_greyhound'
CONTAINER = joaat(BANK)
STREAMING, MULTITRACK, SIMPLE = 8, 13, 12
VOLUME, PITCH, PAN, SPEAKERS = 2, 4, 6, 23
LEFT, RIGHT = 307, 53                   # the Pan of a stereo stem's left and right halves, in degrees
AWC_MULTICHANNEL = 4
FORMAT_CHUNK, DATA_CHUNK = 0x48, 0x55
PACKET = xma2_decode.PACKET
FRAME = xma2_decode.SAMPLES_PER_FRAME
WORKERS = 4
STEM, STEMS = 5, 8                      # the layer played (0-based: the 6th) and the number of stems the tree must hold


def objects(b: bytes) -> dict:
    """{name hash: object} of a sounds.dat54 (big-endian RelFile): the data, the name table, then the index (hash, offset,
    length per object)."""
    if len(b) < 12 or struct.unpack_from('>I', b)[0] != 54:
        raise SoundError('not a sounds.dat54')
    dl, = struct.unpack_from('>I', b, 4)
    names = 8 + dl
    if names + 4 > len(b):
        raise SoundError('the sound tables end inside their data')
    index = names + 4 + struct.unpack_from('>I', b, names)[0]
    if index + 4 > len(b) or index + 4 + 12 * struct.unpack_from('>I', b, index)[0] > len(b):
        raise SoundError('the sound tables end inside their index')
    out = {}
    for i in range(struct.unpack_from('>I', b, index)[0]):
        h, o, n = struct.unpack_from('>III', b, index + 4 + 12 * i)
        if o + n > dl or not n:
            raise SoundError('sound %08x: outside the data' % h)
        out[h] = b[8 + o:8 + o + n]
    return out


def children(obj: bytes, at: int) -> list:
    """The child hashes after the count byte at `at` (a Multitrack's body starts with it, a StreamingSound's after its duration)."""
    n = obj[at]
    if at + 1 + 4 * n > len(obj):
        raise SoundError('a sound whose children end outside it')
    return list(struct.unpack_from('>%dI' % n, obj, at + 1))


def stems(objs: dict) -> list:
    """[((left wave, volume), (right wave, volume)), ...] of the StreamingSound that plays the bank, in its order."""
    def simple_on_bank(h):
        x = objs.get(h)
        return x is not None and x[0] == SIMPLE and struct.unpack_from('>I', x, Sounds.header(x)[1])[0] == CONTAINER

    tops = []
    for h, x in objs.items():
        if x[0] == STREAMING:
            hd, p = Sounds.header(x)
            kids = children(x, p + 4)
            if any(objs.get(k, b'\0')[0] == MULTITRACK and any(simple_on_bank(g) for g in
                                                                 children(objs[k], Sounds.header(objs[k])[1]))
                   for k in kids):
                tops.append((h, hd, kids))
    if len(tops) != 1:
        raise SoundError('%d streaming sounds play %s, want one' % (len(tops), BANK))
    top, hd, kids = tops[0]

    def plain(name, header, may_pan=False):
        if header.get(PITCH, 0):
            raise SoundError('%s: pitched by %d cents (the music is not resampled)' % (name, header[PITCH]))
        if not may_pan and (header.get(PAN, 0) or header.get(SPEAKERS, 0)):
            raise SoundError('%s: panned above its waves' % name)
        return header.get(VOLUME, 0)

    base = plain('sound %08x' % top, hd)
    out = []
    for k in kids:
        x = objs.get(k)
        if x is None or x[0] != MULTITRACK:
            raise SoundError('stem %08x: not a multitrack' % k)
        mh, mp = Sounds.header(x)
        vol = base + plain('stem %08x' % k, mh)
        sides = {}
        for g in children(x, mp):
            if not simple_on_bank(g):
                raise SoundError('stem %08x: %08x is not a wave of %s' % (k, g, BANK))
            sh, sp = Sounds.header(objs[g])
            v = vol + plain('wave sound %08x' % g, sh, may_pan=True)
            if sh.get(SPEAKERS, 0) or sh.get(PAN) not in (LEFT, RIGHT) or sh[PAN] in sides:
                raise SoundError('stem %08x: not one left (pan %d) and one right (pan %d) half' % (k, LEFT, RIGHT))
            sides[sh[PAN]] = (struct.unpack_from('>I', objs[g], sp + 4)[0] & 0x1FFFFFFF, v)
        if len(sides) != 2:
            raise SoundError('stem %08x: not one left (pan %d) and one right (pan %d) half' % (k, LEFT, RIGHT))
        out.append((sides[LEFT], sides[RIGHT]))
    return out


def bank_layout(awc: bytes):
    """(channels {id: (samples, headroom, rate)}, blocks [[(packets, skip, count) per channel] per block], channel order)."""
    if awc[:4] != b'TADA':
        raise SoundError('not a big-endian AWC')
    flags, _version, n, _ = struct.unpack_from('>HHII', awc, 4)
    if not flags & AWC_MULTICHANNEL:
        raise SoundError('the bank is not a multichannel streamed AWC')
    p = 16 + (2 * n if flags & 1 else 0)
    counts = []
    for _ in range(n):
        counts.append(struct.unpack_from('>I', awc, p)[0] >> 29)
        p += 4
    chunks = {}
    for _ in range(sum(counts)):
        v, = struct.unpack_from('>Q', awc, p)
        p += 8
        chunks.setdefault(v >> 56, []).append((v & 0x0FFFFFFF, (v >> 28) & 0x0FFFFFFF))
    if len(chunks.get(FORMAT_CHUNK, [])) != 1 or len(chunks.get(DATA_CHUNK, [])) != 1:
        raise SoundError('the bank has no single format and data chunk')
    (fo, fs), (do, ds) = chunks[FORMAT_CHUNK][0], chunks[DATA_CHUNK][0]
    nblocks, block, nch = struct.unpack_from('>III', awc, fo)
    if fs < 12 + 16 * nch or fo + fs > len(awc) or do + ds > len(awc) or ds != nblocks * block or not nch:
        raise SoundError('the bank\'s format and data chunks do not fit it')
    order, channels = [], {}
    for c in range(nch):
        cid, samples, headroom, rate, codec = struct.unpack_from('>IIhHB', awc, fo + 12 + 16 * c)
        if codec != AWC_XMA2:
            raise SoundError('channel %08x: codec %d, not XMA2' % (cid, codec))
        order.append(cid)
        channels[cid] = (samples, headroom, rate)
    blocks, done = [], [0] * nch                        # per channel, the samples the blocks so far keep
    for k in range(nblocks):
        o = do + k * block
        recs = [struct.unpack_from('>iiii', awc, o + 16 * c) for c in range(nch)]
        total = sum(r[1] for r in recs)
        first = o + -(-(16 * nch + 4 * total) // PACKET) * PACKET
        if any(r[1] < 0 for r in recs) or first + total * PACKET > o + block:
            raise SoundError('block %d: the channels\' packets lie outside the block' % k)
        entries = struct.unpack_from('>%dI' % total, awc, o + 16 * nch)
        row, at = [], 0
        for c, (_slot, count, skip, samples) in enumerate(recs):
            packets = awc[first + at * PACKET:first + (at + count) * PACKET]
            starts = entries[at:at + count]
            if count and starts[0] + skip != done[c]:
                raise SoundError('block %d, channel %08x: starts at sample %d and skips %d, the blocks before end at %d'
                                 % (k, order[c], starts[0], skip, done[c]))
            if any(starts[i] + (packets[i * PACKET] >> 2) * FRAME != starts[i + 1] for i in range(count - 1)):
                raise SoundError('block %d, channel %08x: the packet entries disagree with the packets\' frame counts' % (k, order[c]))
            done[c] += samples - skip
            row.append((packets, skip, samples))
            at += count
        blocks.append(row)
    return channels, blocks, order


def _decode(job):
    blocks, samples, rate, tables = job
    return xma2_decode.decode_blocks([b for b in blocks if b[0] or b[2]], samples, rate, tables)


def render_music(sounds_dat54: bytes, bank_awc: bytes, ffmpeg_source, workers: int = WORKERS) -> bytes:
    """The launcher music as a RIFF WAVE file. Refuses (SoundError / XmaError) a tree, bank or mix that is not the one this
    expects, so a different disc never gives a silent or wrong file."""
    tables = xma2_decode.load_tables(ffmpeg_source)
    plan = stems(objects(sounds_dat54))
    if len(plan) != STEMS:
        raise SoundError('the sound tree holds %d stems, not the %d the launcher music was chosen from' % (len(plan), STEMS))
    plan = [plan[STEM]]
    channels, blocks, order = bank_layout(bank_awc)
    waves = [w for pair in plan for w, _ in pair]
    missing = [w for w in waves if w not in channels]
    if missing or len(set(waves)) != len(waves):
        raise SoundError('the stems\' waves are not the bank\'s channels: %s' % ', '.join('%08x' % w for w in missing or waves))
    if len({channels[w][::2] for w in waves}) != 1:
        raise SoundError('the stems\' channels differ in length or sample rate')
    samples, _, rate = channels[waves[0]]
    jobs = [([row[order.index(w)] for row in blocks], samples, rate, tables) for w in waves]
    left, right = array.array('d', bytes(8 * samples)), array.array('d', bytes(8 * samples))
    n = min(workers, os.cpu_count() or 1, len(jobs))
    pool = concurrent.futures.ProcessPoolExecutor(max_workers=n) if n > 1 else None
    try:
        decoded = pool.map(_decode, jobs) if pool else map(_decode, jobs)
        for i, pcm in enumerate(decoded):                # the stem's left half, then its right half
            wave, volume = plan[i // 2][i % 2]
            gain = power(10, volume - channels[wave][1], 2000)
            side = left if i % 2 == 0 else right
            side[:] = array.array('d', map(operator.add, side, map(gain.__mul__, pcm)))
            print('UI music: stem %d of %d, %s half (channel %08x) decoded' % (STEM + 1, STEMS, ('left', 'right')[i % 2], wave),
                  flush=True)
    finally:
        if pool:
            pool.shutdown(cancel_futures=True)
    peak = max(max(map(abs, left)), max(map(abs, right)))
    if not peak:
        raise SoundError('the music mix is silent')
    if left == right:
        raise SoundError('the music mix has the same left and right side: not the stereo stems this expects')
    scale = power(10, -1, 20) / peak                    # to -1 dBFS, up or down
    frames = array.array('h', bytes(4 * samples))
    frames[0::2] = array.array('h', (int(round(v * scale * 32767)) for v in left))
    frames[1::2] = array.array('h', (int(round(v * scale * 32767)) for v in right))
    if sys.byteorder != 'little':
        frames.byteswap()
    return wav(frames.tobytes(), rate)
