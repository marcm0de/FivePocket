#!/usr/bin/env python3
"""Apply one-shot POSIX limits, then replace this process with the worker."""

from __future__ import annotations

import argparse
import os
import resource
import sys
from typing import Sequence


LAUNCHER_FAILURE_EXIT = 125


def _set_limit(resource_name: int, soft: int, hard: int) -> None:
    _, current_hard = resource.getrlimit(resource_name)
    if current_hard != resource.RLIM_INFINITY:
        hard = min(hard, current_hard)
        soft = min(soft, hard)
    resource.setrlimit(resource_name, (soft, hard))


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--cpu-seconds", type=int, required=True)
    parser.add_argument("--open-files", type=int, required=True)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    try:
        args = parse_args(argv)
        command = list(args.command)
        if command and command[0] == "--":
            command.pop(0)
        if (
            args.cpu_seconds < 1
            or args.open_files < 3
            or not command
            or not os.path.isabs(command[0])
        ):
            return LAUNCHER_FAILURE_EXIT
        _set_limit(resource.RLIMIT_CORE, 0, 0)
        _set_limit(resource.RLIMIT_FSIZE, 0, 0)
        _set_limit(resource.RLIMIT_NOFILE, args.open_files, args.open_files)
        _set_limit(
            resource.RLIMIT_CPU,
            args.cpu_seconds,
            args.cpu_seconds + 1,
        )
        os.execve(command[0], command, os.environ.copy())
    except (OSError, ValueError):
        return LAUNCHER_FAILURE_EXIT
    return LAUNCHER_FAILURE_EXIT


if __name__ == "__main__":
    raise SystemExit(main())
