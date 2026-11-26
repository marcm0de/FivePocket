#!/usr/bin/env python3
"""title_update.py: the player's own title-update package, read for the builder.

A title update is the file the player's console downloaded for the game (an Xbox 360 LIVE STFS package; for GTA V a
file named like tu00000009_00000000). It holds, per play disc, the XEX delta patch of that disc's executable
(discNNN/default.xexp) and a data archive (update.rpf). The builder reads both out of the player's own file; this
module is that reader. It holds no game data, no key and no list of supported updates: which files a build takes,
and their SHA-256s, are the profile's (disc_builder.py, games[].title_update).

The block, hash-tree and directory code is tools/host_guest_trace/stfs_inventory.py's verified reader, used as it is.
Only the acceptance of the package header is this module's own: that reader accepts marketplace content (PIRS,
content type 2, metadata version 1 or 2); a title update is a LIVE package of content type 0x000B0000 (title update)
with metadata version 0, 1 or 2. Everything else is the reader's rule (single-file read-only STFS, no free blocks,
every directory and chain consistent). Every data block read is checked against the package's SHA-1 hash tree.
Microsoft's signatures are NOT authenticated: what the build accepts is decided by the profile's SHA-256 pins and by
the patch step, which refuses a patch made for another executable.

  title_update.py list PACKAGE      the files inside (names and sizes; no content is printed)

For disc_builder.py --title-update: open_update(path) gives a read-only "disc" (kind, root, image, size(rel),
open(rel)) like the builder's FolderDisc and XisoDisc. A folder (the package already unpacked) is read by the
builder as a folder instead.
"""
from __future__ import annotations

import argparse
import os
import stat
import struct
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
for _folder in (HERE, HERE.parent / 'host_guest_trace'):
    if str(_folder) not in sys.path:
        sys.path.insert(0, str(_folder))
import stfs_inventory  # noqa: E402

LIVE, PIRS = b'LIVE', b'PIRS'
TITLE_UPDATE = 0x000B0000            # STFS content type of a title update
MARKETPLACE = 0x00000002             # STFS content type of downloadable content: a DLC pack
METADATA_VERSIONS = (0, 1, 2)


class NotAPackage(ValueError):
    """The file is not a package of the kind this reader accepts (the message says which check failed)."""


class NotATitleUpdate(NotAPackage):
    """The file is not a title-update package this reader accepts."""


class NotADlcPack(NotAPackage):
    """The file is not a DLC package this reader accepts."""


def _be32(data: bytes, offset: int) -> int:
    return struct.unpack_from('>I', data, offset)[0]


class LivePackage(stfs_inventory.Package):
    """stfs_inventory.Package with this module's acceptance of the header (MAGICS, CONTENT_TYPE); every other method is the
    reader's own. TitleUpdatePackage and DlcPackage are the two kinds the builder reads."""
    MAGICS = (LIVE,)
    CONTENT_TYPE = TITLE_UPDATE
    WHAT = 'a title update'
    ERROR = NotAPackage

    def __init__(self, path: Path):                     # noqa: super().__init__ is not called: its acceptance is PIRS only
        self.fd = -1
        self.cache = {}
        self.path = path
        bad = self.ERROR
        try:
            if not stfs_inventory.NO_FOLLOW and Path(path).is_symlink():
                raise bad('the package path is a link')
            self.fd = os.open(path, stfs_inventory.READ_FLAGS)
            self.initial = os.fstat(self.fd)
            self.size = self.initial.st_size
            if not stat.S_ISREG(self.initial.st_mode) or not 0x971A <= self.size <= stfs_inventory.MAX_PACKAGE:
                raise bad('not a package file (type or size)')
            header = self.read(0, 0x971A)
            if header[:4] not in self.MAGICS:
                raise bad(f'not a {" or ".join(m.decode() for m in self.MAGICS)} package')
            if _be32(header, 0x344) != self.CONTENT_TYPE:
                raise bad(f'a package, but not {self.WHAT} (content type)')
            if _be32(header, 0x348) not in METADATA_VERSIONS:
                raise bad('unsupported package metadata version')
            self.title_id = _be32(header, 0x360)
            header_size = _be32(header, 0x340)
            if not 0x971A <= header_size <= min(self.size, 1024 * 1024):
                raise bad('package header bounds rejected')
            self.base = (header_size + stfs_inventory.BLOCK - 1) & -stfs_inventory.BLOCK
            volume = header[0x379:0x39D]
            if volume[:3] != b'\x24\0\x01' or any(header[0x39D:0x3AD]):
                raise bad('only single-file read-only STFS version zero is supported')
            self.total = int.from_bytes(volume[28:32], 'big')
            free = int.from_bytes(volume[32:36], 'big')
            self.directory_count = int.from_bytes(volume[3:5], 'little')
            self.first_directory = int.from_bytes(volume[5:8], 'little')
            if not 1 <= self.total <= min(170 ** 3, self.size // stfs_inventory.BLOCK) or free != 0 \
                    or not 1 <= self.directory_count <= stfs_inventory.MAX_ENTRIES // 64 \
                    or stfs_inventory.data_offset(self.total - 1, self.base) + stfs_inventory.BLOCK > self.size:
                raise bad('package allocation bounds rejected')
            self.root_level = 0 if self.total <= 170 else 1 if self.total <= 28900 else 2
            self.root_hash = volume[8:28]
            self.claimed = bytearray(self.total)
            self.entries = self.directory()
            self.check_unchanged()
        except BaseException:
            self.close()
            raise


class TitleUpdatePackage(LivePackage):
    """a title update: a LIVE package of content type 0x000B0000"""
    ERROR = NotATitleUpdate


class DlcPackage(LivePackage):
    """a DLC pack: marketplace content (content type 2), LIVE or PIRS, as it comes off the console"""
    MAGICS = (LIVE, PIRS)
    CONTENT_TYPE = MARKETPLACE
    WHAT = 'a DLC pack'
    ERROR = NotADlcPack


def check_dlc(path: Path, title_id: int) -> dict:
    """a DLC pack read through: its header accepted, its title the game's, and every block of every file it holds checked
    against its hash tree. Returns {'files': count, 'bytes': data bytes}; NotADlcPack names the first problem."""
    files = data = 0
    with DlcPackage(path) as package:
        if package.title_id != title_id:
            raise NotADlcPack(f'a DLC pack of another game (title {package.title_id:08X}, not {title_id:08X})')
        for entry in package.entries:
            if entry.directory:
                continue
            reader = EntryReader(package, entry)
            while reader.read(1 << 20):
                pass
            files, data = files + 1, data + entry.size
    return {'files': files, 'bytes': data}


class EntryReader:
    """One file of the package as a read-only stream; every block is checked against the hash tree as it is read."""

    def __init__(self, package: LivePackage, entry):
        self.package, self.entry = package, entry
        self.position, self.buffer = 0, b''
        self.blocks = iter(entry.blocks)

    def read(self, size: int = -1) -> bytes:
        left = self.entry.size - self.position
        size = left if size is None or size < 0 else min(size, left)
        while len(self.buffer) < size:
            self.buffer += self.package.block(next(self.blocks))
        data, self.buffer = self.buffer[:size], self.buffer[size:]
        self.position += len(data)
        if self.position == self.entry.size:
            self.package.check_unchanged()
        return data

    def close(self):
        self.package.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


class UpdateDisc:
    """A title-update package as a read-only disc for disc_builder.py. Names are matched without regard to case."""

    kind = 'title-update package'

    def __init__(self, path: Path):
        self.image = self.root = path
        with TitleUpdatePackage(path) as package:
            self.title_id = package.title_id
            self.files = {e.path.lower(): e for e in package.entries if not e.directory}

    def _entry(self, rel: str):
        parts = [p for p in rel.replace('\\', '/').split('/') if p]
        if any(p in ('.', '..') for p in parts):
            raise ValueError(f'bad path inside the title update: {rel}')
        return self.files.get('/'.join(parts).lower())

    def size(self, rel: str) -> int | None:
        entry = self._entry(rel)
        return None if entry is None else entry.size

    def open(self, rel: str):
        entry = self._entry(rel)
        if entry is None:
            raise FileNotFoundError(f'{rel}: not in the title update')
        package = TitleUpdatePackage(self.image)
        try:
            same = next(e for e in package.entries if e.path.lower() == entry.path.lower())
        except StopIteration:
            package.close()
            raise ValueError('the title update changed while it was read') from None
        return EntryReader(package, same), same.size


def open_update(path: Path) -> UpdateDisc:
    return UpdateDisc(path)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    sub = ap.add_subparsers(dest='cmd', required=True)
    listing = sub.add_parser('list', help='the files inside a title-update package')
    listing.add_argument('package', type=Path)
    a = ap.parse_args(argv)
    try:
        disc = open_update(a.package)
    except (OSError, ValueError) as e:
        print(f'not a title update this builder reads: {e}')
        return 4
    print(f'title update for title {disc.title_id:08X}: {len(disc.files)} files')
    for entry in sorted(disc.files.values(), key=lambda e: e.path.lower()):
        print(f'  {entry.path:<40} {entry.size:>12} bytes')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
