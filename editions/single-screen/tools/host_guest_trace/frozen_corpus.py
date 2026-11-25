"""Verify preserved PPC link inputs without needing an obsolete ancestor .so.

A surviving, hash-checked link receipt and its library are the checkpoint.
The entire PPC set must match it: native-only equality is not sufficient.
This does not authenticate a checkpoint's game semantics or authorize install.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import stat


def resident_digest(path):
    path = Path(path)
    info = path.lstat()
    if (not stat.S_ISREG(info.st_mode) or
            getattr(info, 'st_flags', 0) & 0x40000000):
        raise ValueError('resident regular file required: ' + str(path))
    result = hashlib.sha256()
    size = 0
    with path.open('rb') as stream:
        for data in iter(lambda: stream.read(1024 * 1024), b''):
            size += len(data)
            result.update(data)
    after = path.stat()
    if (size != info.st_size or
            (after.st_ino, after.st_size, after.st_mtime_ns) !=
            (info.st_ino, info.st_size, info.st_mtime_ns)):
        raise ValueError('input changed while reading: ' + str(path))
    return result.hexdigest()


def ppc_inputs(command, build, cache, count):
    build, cache = Path(build).resolve(), Path(cache).resolve()
    found = {}
    if any(word.startswith('@') for word in command):
        raise ValueError('expand response files before auditing link inputs')
    for word in command:
        match = re.fullmatch(r'ppc_recomp\.(\d+)\.cpp\.o', Path(word).name)
        if not match:
            continue
        path = Path(word)
        if not path.is_absolute():
            path = build / path
        index = int(match[1])
        if path.parent != cache or path.is_symlink():
            raise ValueError('PPC object substituted outside frozen cache: ' + word)
        if index in found:
            raise ValueError('duplicate PPC chunk: ' + str(index))
        found[index] = path
    if set(found) != set(range(count)):
        raise ValueError('complete exact PPC chunk set required')
    return found


def verify_frozen_ppc(basis, build, cache, count, *, command=None, rebuilt=()):
    """Check a surviving basis and current unchanged objects, fail closed.

    Rebuilt chunks are explicit exceptions for callers that separately validate
    fresh source/compiler/dependency receipts. They never permit path changes.
    """
    output = Path(basis['output'])
    if output.parent != Path(build).resolve():
        raise ValueError('basis output outside build directory')
    if resident_digest(output) != basis['sha256']:
        raise ValueError('basis library hash mismatch')
    expected = ppc_inputs(basis['command'], build, cache, count)
    actual = ppc_inputs(command if command is not None else basis['command'],
                        build, cache, count)
    if actual != expected:
        raise ValueError('candidate PPC paths differ from checkpoint')
    rebuilt = set(rebuilt)
    if not rebuilt.issubset(expected):
        raise ValueError('invalid rebuilt chunks')
    digests = {}
    for index, path in actual.items():
        digest = resident_digest(path)
        if index not in rebuilt and basis['inputs'].get(str(path)) != digest:
            raise ValueError('frozen PPC object hash mismatch: ' + str(path))
        digests[str(path)] = digest
    return digests


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--basis', type=Path, required=True)
    parser.add_argument('--candidate', type=Path, required=True)
    parser.add_argument('--build', type=Path, required=True)
    parser.add_argument('--cache', type=Path, required=True)
    parser.add_argument('--chunks', type=int, required=True)
    args = parser.parse_args()
    basis = json.loads(args.basis.read_text())
    candidate = json.loads(args.candidate.read_text())
    inputs = verify_frozen_ppc(basis, args.build, args.cache, args.chunks,
                               command=candidate['command'])
    if resident_digest(candidate['output']) != candidate['sha256']:
        raise ValueError('candidate library hash mismatch')
    for path, expected in candidate['inputs'].items():
        if resident_digest(path) != expected:
            raise ValueError('candidate link input changed: ' + path)
    if any(candidate['inputs'].get(p) != h for p, h in inputs.items()):
        raise ValueError('candidate receipt omits or changes frozen PPC inputs')
    print('FROZEN_CORPUS_LINK_PASS chunks=' + str(len(inputs)) +
          ' candidate_and_all_recorded_inputs=1')


if __name__ == '__main__':
    main()
