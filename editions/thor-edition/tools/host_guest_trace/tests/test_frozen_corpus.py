from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from frozen_corpus import ppc_inputs, resident_digest, verify_frozen_ppc


class FrozenCorpusTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.build = Path(self.tmp.name).resolve()
        self.cache = self.build / 'optimized-ppc-strict'
        self.cache.mkdir()
        self.objects = [self.cache / f'ppc_recomp.{n}.cpp.o' for n in range(2)]
        for p in self.objects:
            p.write_bytes(b'preserved paired switch')
        output = self.build / 'surviving.so'
        output.write_bytes(b'known checkpoint')
        self.basis = dict(output=str(output), sha256=resident_digest(output),
                          command=['clang++', *map(str, self.objects), '-shared'],
                          inputs={str(p): resident_digest(p) for p in self.objects})

    def verify(self, **kwargs):
        return verify_frozen_ppc(self.basis, self.build, self.cache, 2, **kwargs)

    def test_surviving_checkpoint_needs_no_deleted_ancestor(self):
        self.assertFalse((self.build / 'obsolete-ancestor.so').exists())
        self.assertEqual(len(self.verify()), 2)

    def test_ninja_corpus_substitution_rejected_even_if_native_unchanged(self):
        old = self.build / 'CMakeFiles' / 'validated-ppc'
        old.mkdir(parents=True)
        alternate = old / self.objects[0].name
        alternate.write_bytes(self.objects[0].read_bytes())
        command = [str(alternate), str(self.objects[1])]
        with self.assertRaisesRegex(ValueError, 'substituted'):
            self.verify(command=command)

    def test_same_path_stale_object_rejected(self):
        self.objects[0].write_bytes(b'old indirect dispatch')
        with self.assertRaisesRegex(ValueError, 'hash mismatch'):
            self.verify()

    def test_missing_or_duplicate_chunk_rejected(self):
        for command in ([str(self.objects[0])],
                        [*map(str, self.objects), str(self.objects[0])]):
            with self.subTest(command=command), self.assertRaises(ValueError):
                self.verify(command=command)

    def test_wrong_or_missing_basis_library_rejected(self):
        output = Path(self.basis['output'])
        output.write_bytes(b'not checkpoint')
        with self.assertRaisesRegex(ValueError, 'basis library hash'):
            self.verify()
        output.unlink()
        with self.assertRaises(FileNotFoundError):
            self.verify()

    def test_explicit_rebuild_does_not_permit_path_substitution(self):
        self.objects[0].write_bytes(b'new code')
        self.assertEqual(len(self.verify(rebuilt=[0])), 2)
        with self.assertRaisesRegex(ValueError, 'invalid rebuilt'):
            self.verify(rebuilt=[2])

    def test_symlink_rejected(self):
        self.objects[0].unlink()
        self.objects[0].symlink_to(self.objects[1])
        with self.assertRaises(ValueError):
            self.verify()

    def test_response_file_cannot_hide_alternate_objects(self):
        with self.assertRaisesRegex(ValueError, 'response files'):
            ppc_inputs(['@objects.rsp'], self.build, self.cache, 2)


if __name__ == '__main__':
    unittest.main()
