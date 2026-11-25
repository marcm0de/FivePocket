#!/usr/bin/env python3
"""Mutant check for the host-neutral file access of stfs_inventory.py: copies of the tool with one rule changed;
test_stfs_inventory.py (beside this file) must fail on each. First a control. Exits 1 if the control fails, a mutant
survives or no longer applies. The tool itself is not touched."""
import os, shutil, subprocess, sys, tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
TOOL = HERE.parent / 'stfs_inventory.py'
MUTANTS = [
    ('the fallback read does not move to the place asked for', "        os.lseek(fd, offset, os.SEEK_SET)\n", ""),
    ('the fallback read takes one piece only', "        while left > 0:\n            chunk = os.read(fd, left)", "        while left > 0 and not chunks:\n            chunk = os.read(fd, min(left, 512))"),
    ('a link is opened on a host without the flag', "            if not NO_FOLLOW and Path(path).is_symlink():\n                raise ValueError('package path is a link')\n", ""),
    ('the package is read with the system call only', "        data = pread(self.fd, size, offset)\n", "        data = os.pread(self.fd, size, offset)\n"),
    ('the package is opened with fixed Unix flags', "            self.fd = os.open(path, READ_FLAGS)\n", "            self.fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)\n"),
    ('this system follows links', "READ_FLAGS = os.O_RDONLY | NO_FOLLOW | getattr(os, 'O_CLOEXEC', 0)", "READ_FLAGS = os.O_RDONLY | getattr(os, 'O_CLOEXEC', 0)"),
    ('this system leaves the descriptor to child processes', " | getattr(os, 'O_CLOEXEC', 0) | getattr(os, 'O_BINARY', 0)", " | getattr(os, 'O_BINARY', 0)"),
    ('an existing output is overwritten', "NEW_FILE_FLAGS = os.O_WRONLY | os.O_CREAT | os.O_EXCL | NO_FOLLOW", "NEW_FILE_FLAGS = os.O_WRONLY | os.O_CREAT | NO_FOLLOW"),
    ('the output is written with fixed Unix flags', "        fd = os.open(output, NEW_FILE_FLAGS, 0o600)\n", "        fd = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW, 0o600)\n"),
    ('the fallback is used on a system with its own', "pread = os.pread if hasattr(os, 'pread') else pread_by_seek", "pread = pread_by_seek"),
]


def run(folder):
    return subprocess.run([sys.executable, '-B', str(folder / 'tests/test_stfs_inventory.py')], capture_output=True, text=True)


def main() -> int:
    source = TOOL.read_text(encoding='utf-8')
    killed = 0
    with tempfile.TemporaryDirectory() as tmp:
        folder = Path(tmp)
        (folder / 'tests').mkdir()
        shutil.copy(HERE / 'test_stfs_inventory.py', folder / 'tests/test_stfs_inventory.py')   # it imports the tool from its parent folder
        copy = folder / 'stfs_inventory.py'
        copy.write_text(source, encoding='utf-8')
        if run(folder).returncode != 0:
            print('CONTROL FAILED: the tests do not pass on an unchanged copy'); return 1
        print('control: the tests pass on an unchanged copy of the tool', flush=True)
        for index, (what, old, new) in enumerate(MUTANTS, 1):
            if source.count(old) != 1:
                print(f'mutant {index:3d} DOES NOT APPLY ({source.count(old)} matches): {what}'); continue
            copy.write_text(source.replace(old, new), encoding='utf-8')
            if run(folder).returncode == 0: print(f'mutant {index:3d} SURVIVED: {what}')
            else: killed += 1; print(f'mutant {index:3d} killed: {what}')
    print(f'{killed} of {len(MUTANTS)} mutants killed')
    return 0 if killed == len(MUTANTS) else 1


if __name__ == '__main__':
    sys.exit(main())
