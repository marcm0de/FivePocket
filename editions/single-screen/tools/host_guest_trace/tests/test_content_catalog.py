from pathlib import Path
import struct
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from content_catalog import parse_catalog


def fixture():
    row = struct.pack('>II', 1, 2) + 'Synthetic'.encode('utf-16-be').ljust(256, b'\0') + b'ABC123'.ljust(44, b'\0')
    return b'GXCONT1\0' + struct.pack('>II', 0x12345678, 1) + row


class CatalogTests(unittest.TestCase):
    def test_valid_and_truncation(self):
        data = fixture()
        self.assertEqual(parse_catalog(data), (0x12345678, 1))
        for length in range(len(data)):
            with self.assertRaises(ValueError):
                parse_catalog(data[:length])
        for broken in (data + b'\0', data[:12] + struct.pack('>I', 17) + data[16:],
                       data[:8] + bytes(4) + data[12:]):
            with self.assertRaises(ValueError):
                parse_catalog(broken)

    def test_identity_utf16_and_padding(self):
        data = fixture()
        for offset, value in ((0, 0), (19, 2), (23, 3), (24, 0xDC), (24 + 30, 65), (280, ord('/')), (323, 1)):
            bad = bytearray(data); bad[offset] = value
            with self.assertRaises(ValueError):
                parse_catalog(bad)
        second = bytearray(data[16:]); second[264] = ord('a')
        with self.assertRaises(ValueError):
            parse_catalog(data[:12] + struct.pack('>I', 2) + data[16:] + second)
        # Valid paired surrogate is a display character, never an identity path.
        row = bytearray(data[16:]); row[8:264] = '\U0001F600'.encode('utf-16-be').ljust(256, b'\0')
        self.assertEqual(parse_catalog(data[:16] + row), (0x12345678, 1))


if __name__ == '__main__':
    unittest.main()
