from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import signal
import sys
import tempfile
import time
import unittest


MODULE_PATH = Path(__file__).resolve().parents[1] / "contained_process.py"
SPEC = importlib.util.spec_from_file_location("host_guest_contained_process", MODULE_PATH)
assert SPEC and SPEC.loader
contained = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = contained
SPEC.loader.exec_module(contained)

WORKER = Path(__file__).with_name("synthetic_worker.py")


def limits(
    *,
    wall: float = 3.0,
    cpu: int = 2,
) -> contained.ContainedProcessLimits:
    return contained.ContainedProcessLimits(
        wall_seconds=wall,
        cpu_seconds=cpu,
        termination_grace_seconds=0.1,
    )


class ContainedProcessTests(unittest.TestCase):
    def run_worker(
        self,
        mode: str,
        worker_limits: contained.ContainedProcessLimits | None = None,
    ) -> contained.ContainedProcessOutcome:
        with tempfile.TemporaryDirectory() as temporary:
            return contained.run_contained(
                [sys.executable, str(WORKER), mode],
                Path(temporary),
                worker_limits or limits(),
            )

    def test_clean_worker_and_strict_environment(self) -> None:
        clean = self.run_worker("clean")
        self.assertEqual(clean.kind, contained.ContainedOutcomeKind.EXITED)
        self.assertEqual(clean.returncode, 0)
        self.assertEqual(clean.stdout, b"synthetic-protocol\n")

        environment = self.run_worker("environment")
        self.assertEqual(environment.kind, contained.ContainedOutcomeKind.EXITED)
        keys = set(json.loads(environment.stdout))
        self.assertEqual(
            keys,
            set(contained.ALLOWLISTED_ENVIRONMENT) | {"TMPDIR"},
        )

    def test_unix_signal_is_structured(self) -> None:
        outcome = self.run_worker("signal")
        self.assertEqual(outcome.kind, contained.ContainedOutcomeKind.UNIX_SIGNAL)
        self.assertEqual(outcome.signal_number, signal.SIGSEGV)
        self.assertEqual(outcome.signal_name, "SIGSEGV")
        self.assertEqual(outcome.stdout, b"")

    def test_cpu_limit_is_distinct_from_wall_timeout(self) -> None:
        outcome = self.run_worker("spin", limits(wall=5.0, cpu=1))
        self.assertEqual(outcome.kind, contained.ContainedOutcomeKind.CPU_LIMIT)
        self.assertEqual(outcome.signal_number, signal.SIGXCPU)

    def test_wall_timeout_terminates_worker_group(self) -> None:
        outcome = self.run_worker("hang", limits(wall=0.2))
        self.assertEqual(outcome.kind, contained.ContainedOutcomeKind.WALL_TIMEOUT)
        self.assertTrue(outcome.term_sent)

    def test_wall_timeout_escalates_to_kill(self) -> None:
        # Includes two Python interpreter startups; leave room for concurrent
        # native compiles before the fixture installs its signal handler.
        outcome = self.run_worker("ignore-term", limits(wall=2.0))
        self.assertEqual(outcome.kind, contained.ContainedOutcomeKind.WALL_TIMEOUT)
        self.assertTrue(outcome.term_sent)
        self.assertTrue(outcome.kill_sent)

    def test_protocol_limit_terminates_worker_group(self) -> None:
        outcome = self.run_worker("flood")
        self.assertEqual(outcome.kind, contained.ContainedOutcomeKind.PROTOCOL_LIMIT)
        self.assertEqual(len(outcome.stdout), contained.PROTOCOL_BYTE_LIMIT)
        self.assertTrue(outcome.term_sent)

    def test_descendant_is_removed_with_process_group(self) -> None:
        # The assertion needs the descendant PID, so startup must fit before
        # the wall deadline. A 0.3s budget could expire before the fixture ran
        # under concurrent macOS/NDK builds, without testing a descendant at all.
        outcome = self.run_worker("descendant", limits(wall=2.0))
        self.assertEqual(outcome.kind, contained.ContainedOutcomeKind.WALL_TIMEOUT)
        self.assertTrue(outcome.stdout.strip().isdigit(), "fixture did not publish its child PID before timeout")
        descendant = int(outcome.stdout.strip())
        deadline = time.monotonic() + 2.0
        while True:
            try:
                os.kill(descendant, 0)
            except ProcessLookupError:
                break
            if time.monotonic() >= deadline:
                self.fail("contained descendant survived process-group cleanup")
            time.sleep(0.02)

    def test_missing_executable_is_launch_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            outcome = contained.run_contained(
                [str(Path(temporary) / "missing")],
                Path(temporary),
                limits(),
            )
        self.assertEqual(outcome.kind, contained.ContainedOutcomeKind.LAUNCH_FAILURE)
        self.assertIsNone(outcome.returncode)


if __name__ == "__main__":
    unittest.main()
