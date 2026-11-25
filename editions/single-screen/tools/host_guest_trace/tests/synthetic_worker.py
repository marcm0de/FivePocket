#!/usr/bin/env python3
"""Asset-free failure modes for contained-process tests."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time


def main() -> int:
    mode = sys.argv[1] if len(sys.argv) == 2 else ""
    if mode == "clean":
        os.write(1, b"synthetic-protocol\n")
        return 0
    if mode == "environment":
        os.write(1, json.dumps(sorted(os.environ)).encode("ascii") + b"\n")
        return 0
    if mode == "signal":
        os.kill(os.getpid(), signal.SIGSEGV)
    if mode == "spin":
        while True:
            pass
    if mode == "hang":
        while True:
            time.sleep(10)
    if mode == "ignore-term":
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        while True:
            time.sleep(10)
    if mode == "flood":
        block = b"x" * 8192
        while True:
            os.write(1, block)
    if mode == "descendant":
        child = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(60)"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        os.write(1, f"{child.pid}\n".encode("ascii"))
        while True:
            time.sleep(10)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
