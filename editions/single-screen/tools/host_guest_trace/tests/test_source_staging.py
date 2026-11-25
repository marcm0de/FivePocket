from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


@unittest.skipUnless(shutil.which("cmake"), "requires CMake")
class SourceStagingTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="recomp-stage-test-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.source = self.root / "source"
        self.source.mkdir()
        self.output = self.root / "staged"
        self.names = ["ppc_recomp.0.cpp", "ppc_recomp.1.cpp", "ppc_func_mapping.cpp",
                      "ppc_context.h", "ppc_config.h", "ppc_recomp_shared.h"]
        for name in self.names:
            (self.source / name).write_text(name)
        library = Path(__file__).resolve().parents[1] / "stage_ppc.cmake"
        self.script = self.root / "run.cmake"
        self.script.write_text(f'cmake_minimum_required(VERSION 3.22)\ninclude("{library}")\n'
                               'stage_private_ppc("${SOURCE}" "${OUTPUT}" "${COUNT}")\n')

    def run_stage(self, source=None, count=2):
        return subprocess.run(["cmake", f"-DSOURCE={source or self.source}",
            f"-DOUTPUT={self.output}", f"-DCOUNT={count}", "-P", str(self.script)],
            capture_output=True, text=True, timeout=10)

    def test_relocated_unchanged_corpus_preserves_timestamps(self):
        self.assertEqual(self.run_stage().returncode, 0)
        before = {name: (self.output / name).stat().st_mtime_ns for name in self.names}
        moved = self.root / "new-corpus"
        shutil.copytree(self.source, moved)
        self.assertEqual(self.run_stage(moved).returncode, 0)
        self.assertEqual(before, {name: (self.output / name).stat().st_mtime_ns for name in self.names})
        (moved / self.names[0]).write_text("changed chunk")
        self.assertEqual(self.run_stage(moved).returncode, 0)
        self.assertEqual((self.output / self.names[0]).read_text(), "changed chunk")
        self.assertEqual((self.source / self.names[0]).read_text(), self.names[0])
        self.assertTrue(all((self.output / name).stat().st_mtime_ns == before[name] for name in self.names[1:]))

    def test_rejects_bad_count_and_source_links_without_partial_writes(self):
        self.assertNotEqual(self.run_stage(count=3).returncode, 0)
        self.assertFalse(self.output.exists())
        target = self.source / self.names[-1]
        target.unlink()
        target.symlink_to(self.source / self.names[0])
        self.assertNotEqual(self.run_stage().returncode, 0)
        self.assertFalse(self.output.exists())

    def test_rejects_destination_links_and_preserves_unrelated_files(self):
        self.output.mkdir()
        unrelated = self.output / "keep.txt"
        unrelated.write_text("user content")
        target = self.output / self.names[0]
        target.symlink_to(unrelated)
        self.assertNotEqual(self.run_stage().returncode, 0)
        self.assertEqual(unrelated.read_text(), "user content")
        target.unlink()
        self.assertEqual(self.run_stage().returncode, 0)
        self.assertEqual(unrelated.read_text(), "user content")

    @unittest.skipUnless(shutil.which("ninja") and shutil.which("c++"), "requires Ninja and C++")
    def test_ninja_reuses_relocated_sources_and_rebuilds_changed_dependencies(self):
        for i in range(2):
            (self.source / self.names[i]).write_text(f'#include "ppc_context.h"\nint part{i}() {{ return VALUE; }}\n')
        (self.source / "ppc_context.h").write_text("#define VALUE 1\n")
        library = Path(__file__).resolve().parents[1] / "stage_ppc.cmake"
        project = self.root / "project"
        project.mkdir()
        (project / "CMakeLists.txt").write_text('cmake_minimum_required(VERSION 3.22)\nproject(StagingTest LANGUAGES CXX)\n'
            f'include("{library}")\nstage_private_ppc("${{SOURCE}}" "${{CMAKE_BINARY_DIR}}/staged" 2)\n'
            'add_library(parts STATIC ${STAGED_PPC_CHUNKS})\ntarget_include_directories(parts PRIVATE "${CMAKE_BINARY_DIR}/staged")\n')
        build = self.root / "build"

        def compile_corpus(source):
            subprocess.run(["cmake", "-S", str(project), "-B", str(build), "-G", "Ninja",
                f"-DSOURCE={source}"], capture_output=True, check=True, timeout=20)
            subprocess.run(["cmake", "--build", str(build)], capture_output=True, check=True, timeout=20)
            return {p.name: p.stat().st_mtime_ns for p in build.rglob("*.o")}

        before = compile_corpus(self.source)
        self.assertEqual(len(before), 2)
        moved = self.root / "new-corpus"
        shutil.copytree(self.source, moved)
        self.assertEqual(compile_corpus(moved), before)
        with (moved / self.names[0]).open("a") as stream:
            stream.write("int extra() { return 2; }\n")
        changed = compile_corpus(moved)
        self.assertNotEqual(changed[self.names[0] + ".o"], before[self.names[0] + ".o"])
        self.assertEqual(changed[self.names[1] + ".o"], before[self.names[1] + ".o"])
        (moved / "ppc_context.h").write_text("#define VALUE 3\n")
        headers = compile_corpus(moved)
        self.assertTrue(all(headers[name] != changed[name] for name in headers))


if __name__ == "__main__":
    unittest.main()
