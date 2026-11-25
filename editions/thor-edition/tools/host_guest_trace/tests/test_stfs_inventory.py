from pathlib import Path
import hashlib
import os
import struct
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from stfs_inventory import Package, BLOCK, END, data_offset, table_offset

TITLE = 0x12345678
BASE = 0xA000


def fixture(path, total=3, chain=(1, 2), *, name=b'test.bin', next_override=None, contiguous=False):
    header = bytearray(BASE)
    header[:4] = b'PIRS'
    struct.pack_into('>III', header, 0x340, 0x971A, 2, 2)
    struct.pack_into('>I', header, 0x360, TITLE)
    header[0x379:0x37C] = b'\x24\0\x01'
    struct.pack_into('<H', header, 0x37C, 1)
    struct.pack_into('>I', header, 0x395, total)
    header[0x411:0x419] = 'Test'.encode('utf-16-be')
    directory = bytearray(BLOCK)
    directory[:len(name)] = name
    directory[40] = len(name) | (64 if contiguous else 0)
    directory[41:44] = len(chain).to_bytes(3, 'little')
    directory[44:47] = len(chain).to_bytes(3, 'little')
    directory[47:50] = chain[0].to_bytes(3, 'little')
    struct.pack_into('>HI', directory, 50, 0xFFFF, len(chain) * BLOCK - 3)
    payload = {0: bytes(directory)}
    for i, number in enumerate(chain):
        payload.setdefault(number, bytes([i + 1]) * BLOCK)
    tables = {}
    for number, data in payload.items():
        off = table_offset(number, 0, BASE)
        table = tables.setdefault(off, bytearray(BLOCK))
        at = number % 170 * 24
        table[at:at + 20] = hashlib.sha1(data).digest()
        following = END if number == 0 else (chain[chain.index(number) + 1] if chain.index(number) + 1 < len(chain) else END)
        if next_override and number in next_override:
            following = next_override[number]
        struct.pack_into('>I', table, at + 20, 0x80000000 | following)
    root_level = 0 if total <= 170 else 1 if total <= 28900 else 2
    for level in range(1, root_level + 1):
        representatives = {table_offset(number, level - 1, BASE): number for number in payload}
        for child_offset, number in representatives.items():
            parent = tables.setdefault(table_offset(number, level, BASE), bytearray(BLOCK))
            at = number // (170 ** level) % 170 * 24
            parent[at:at + 20] = hashlib.sha1(tables[child_offset]).digest()
    header[0x381:0x395] = hashlib.sha1(tables[table_offset(0, root_level, BASE)]).digest()
    with path.open('wb') as stream:
        stream.truncate(data_offset(total - 1, BASE) + BLOCK)
        stream.write(header)
        for offset, data in tables.items():
            stream.seek(offset); stream.write(data)
        for number, data in payload.items():
            stream.seek(data_offset(number, BASE)); stream.write(data)


class StfsTests(unittest.TestCase):
    def test_layout_landmarks_include_both_upper_levels(self):
        self.assertEqual([data_offset(b, 0) // BLOCK for b in (0, 169, 170, 28899, 28900)],
                         [1, 170, 173, 29070, 29074])
        self.assertEqual([table_offset(b, 0, 0) // BLOCK for b in (0, 169, 170, 28899, 28900)],
                         [0, 0, 172, 28900, 29073])
        self.assertEqual(table_offset(28900, 1, 0) // BLOCK, 29072)
        self.assertEqual(table_offset(28900, 2, 0) // BLOCK, 29071)

    def test_root_levels_and_fragmented_crossings_extract_exact_bytes(self):
        for total, chain in ((3, (1, 2)), (172, (169, 170)), (28902, (169, 170, 28899, 28900))):
            with self.subTest(total=total), tempfile.TemporaryDirectory() as folder:
                path = Path(folder) / 'package'
                fixture(path, total, chain)
                with Package(path, TITLE) as package:
                    entry, = package.entries
                    self.assertEqual(entry.blocks, chain)
                    output = Path(folder) / 'result'
                    digest = package.extract(entry, output)
                    expected = b''.join(bytes([i + 1]) * BLOCK for i in range(len(chain)))[:-3]
                    self.assertEqual(output.read_bytes(), expected)
                    self.assertEqual(digest, hashlib.sha256(expected).hexdigest())
                    with self.assertRaises(FileExistsError):
                        package.extract(entry, output)

    def test_semantic_corruption_rejects_even_when_rehashed(self):
        for changes in ({'name': b'../oops'}, {'next_override': {1: 1}},
                        {'next_override': {1: END}}, {'next_override': {2: 1}},
                        {'next_override': {1: 3}}, {'chain': (0, 1)},
                        {'total': 172, 'chain': (1, 170), 'contiguous': True}):
            with self.subTest(changes=changes), tempfile.TemporaryDirectory() as folder:
                path = Path(folder) / 'package'; fixture(path, **changes)
                with self.assertRaises(ValueError):
                    Package(path, TITLE)

    def test_hash_table_and_directory_corruption_reject(self):
        for at in (BASE, data_offset(0, BASE), table_offset(28900, 1, BASE), table_offset(0, 2, BASE)):
            with self.subTest(at=at), tempfile.TemporaryDirectory() as folder:
                path = Path(folder) / 'package'; fixture(path, 28902, (1, 28900))
                with path.open('r+b') as stream:
                    stream.seek(at); old = stream.read(1); stream.seek(at); stream.write(bytes([old[0] ^ 1]))
                with self.assertRaises(ValueError):
                    Package(path, TITLE)

    def test_payload_corruption_only_claimed_verified_after_read(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'package'; fixture(path)
            with path.open('r+b') as stream:
                stream.seek(data_offset(2, BASE)); stream.write(b'X')
            with Package(path, TITLE) as package:
                with self.assertRaises(ValueError):
                    package.extract(package.entries[0], Path(folder) / 'failed-output')

    def test_header_modes_bounds_links_mutation_and_truncation(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'package'; fixture(path)
            with self.assertRaises(ValueError):
                Package(path, TITLE + 1)
            link = Path(folder) / 'link'; link.symlink_to(path)
            with self.assertRaises(OSError):
                Package(link, TITLE)
            for at, data in ((0, b'CON '), (0x37B, b'\0'), (0x3AC, b'\1'), (0x340, b'\xFF'*4),
                             (0x395, b'\xFF'*4), (0x379, b'\0'), (0x37C, b'\xFF'*2)):
                fixture(path)
                with path.open('r+b') as stream:
                    stream.seek(at); stream.write(data)
                with self.assertRaises(ValueError):
                    Package(path, TITLE)
            fixture(path)
            with Package(path, TITLE) as package:
                with path.open('r+b') as stream:
                    stream.truncate(path.stat().st_size - 1)
                with self.assertRaises(ValueError):
                    package.check_unchanged()
            with self.assertRaises(ValueError):
                Package(path, TITLE)


if __name__ == '__main__':
    unittest.main()
