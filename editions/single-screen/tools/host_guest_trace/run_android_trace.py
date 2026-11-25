#!/usr/bin/env python3
"""Cross-build the diagnostic runner and execute once in private ADB storage."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import uuid

import run_trace as host

REMOTE_DIRECTORY = re.compile(r"/data/local/tmp/recomp-trace\.[0-9a-f]{32}\Z")
CONTENT_CACHE_DIRECTORY = re.compile(r"/data/local/tmp/recomp-content-cache\.[A-Za-z0-9]{6,64}\Z")
CONTENT_CACHE_FILE = re.compile(r"/data/local/tmp/recomp-content-cache\.[A-Za-z0-9]{6,64}/[0-9a-f]{64}\.bin\Z")
REMOTE_FILES = ("host_guest_trace", "libhost_guest_trace.so", "supervisor", "decoded.image.bin", "title.xex", "file-metadata.bin",
                "installed-content.bin",
                *(f"installed-package-{i}.bin" for i in range(1, 17)),
                *(f"installed-file-{i}.bin" for i in range(32)),
                *(f"content-{i}.bin" for i in range(8)))
SUPERVISOR_OUTCOMES = {
    "exited", "launch-failure", "cpu-limit", "unix-signal", "wall-timeout", "protocol-limit"
}


def checked_command(command: list[str], timeout: int = 30) -> bytes:
    try:
        result = subprocess.run(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                stderr=subprocess.DEVNULL, timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise host.TraceError("Android diagnostic command could not complete") from exc
    if result.returncode != 0:
        raise host.TraceError("Android diagnostic command failed (diagnostics suppressed)")
    return result.stdout


def remote_command(adb: list[str], words: list[str], timeout: int = 30) -> bytes:
    return checked_command([*adb, "shell", shlex.join(words)], timeout)


def reported_emulator(adb: list[str]) -> bool | None:
    """Record the target's self-report, not an assertion of hardware identity."""
    value = remote_command(adb, ["getprop", "ro.kernel.qemu"])
    if len(value) > 16 or value.strip() not in {b"", b"0", b"1"}:
        raise host.TraceError("invalid Android emulator property")
    return {b"": None, b"0": False, b"1": True}[value.strip()]


def cleanup_commands(directory: str) -> list[list[str]]:
    if REMOTE_DIRECTORY.fullmatch(directory) is None:
        raise host.TraceError("invalid diagnostic temporary-directory target")
    return [["rm", "-f", "--", *(f"{directory}/{name}" for name in REMOTE_FILES)],
            ["rmdir", "--", directory]]


def digest(path: Path) -> str:
    """Hash a complete stable file, never a short read of an offloaded payload."""
    value = hashlib.sha256()
    with path.open("rb") as source:
        before=os.fstat(source.fileno());consumed=0
        while chunk := source.read(1024 * 1024):
            value.update(chunk);consumed+=len(chunk)
        after=os.fstat(source.fileno())
    def stamp(status):
        return (status.st_dev,status.st_ino,status.st_size,status.st_mtime_ns,status.st_ctime_ns)
    if consumed!=before.st_size or stamp(before)!=stamp(after):
        raise host.TraceError('Android payload changed or was not completely read while hashing')
    return value.hexdigest()


def validate_private_trace_library(path: Path) -> Path:
    source=host.require_regular_file(path,'private trace library')
    root=host.repository_root().resolve()
    if not host._is_relative_to(source,root/'local-analysis'):
        raise host.TraceError('private trace library must remain under ignored local-analysis')
    host.require_git_ignored(source,root,'private trace library')
    if not 64<=source.stat().st_size<=512*1024*1024:
        raise host.TraceError('private trace library size is outside its bound')
    with source.open('rb') as file:header=file.read(64)
    if (len(header)!=64 or header[:7]!=b'\x7fELF\x02\x01\x01' or
            header[16:20]!=b'\x03\x00\xb7\x00' or header[24:32]!=bytes(8)):
        raise host.TraceError('private trace library must be an ARM64 ELF shared object without an executable entry')
    return source


def stage_cached_content(adb: list[str], source: Path, destination: str, cache: str, *, link: bool = True) -> bool:
    """Verify a read-only cache entry; optionally link it into per-run storage.

    Returns whether an existing entry was reused. Never overwrites or removes
    an existing cache entry; a mismatch is an explicit error. Only our uniquely
    named partial upload is eligible for cleanup here.
    """
    if CONTENT_CACHE_DIRECTORY.fullmatch(cache) is None or not re.fullmatch(
            r"/data/local/tmp/recomp-trace\.[0-9a-f]{32}/(?:content-[0-7]|installed-file-(?:[0-9]|[12][0-9]|3[01]))\.bin", destination):
        raise host.TraceError("invalid bounded content-cache target")
    uid = remote_command(adb, ["id", "-u"]).strip().decode("ascii")
    if not uid.isdigit() or remote_command(adb, ["stat", "-c", "%F %a %u", cache]).strip() != f"directory 700 {uid}".encode():
        raise host.TraceError("content cache must be an owned non-link private directory")
    expected = digest(source)
    cached = f"{cache}/{expected}.bin"
    # Constant shell program, with a separately quoted single path argument.
    probe = 'if [ -L "$1" ]; then echo invalid; elif [ -f "$1" ]; then echo file; elif [ -e "$1" ]; then echo invalid; else echo missing; fi'
    state = remote_command(adb, ["sh", "-c", probe, "cache-probe", cached]).strip()
    if state not in {b"file", b"missing"}:
        raise host.TraceError("content cache entry is not a regular file")
    reused = state == b"file"
    if not reused:
        partial = cached + ".part-" + uuid.uuid4().hex
        try:
            checked_command([*adb, "push", str(source), partial], 180)
            remote_command(adb, ["chmod", "400", "--", partial])
            if remote_command(adb, ["sha256sum", partial], 120).decode().split()[0] != expected:
                raise host.TraceError("uploaded cache entry does not match source")
            remote_command(adb, ["mv", "-n", "--", partial, cached])
        finally:
            remote_command(adb, ["rm", "-f", "--", partial])
    expected_stat = f"regular file 400 {uid} {source.stat().st_size}".encode()
    if remote_command(adb, ["stat", "-c", "%F %a %u %s", cached]).strip() != expected_stat \
            or remote_command(adb, ["sha256sum", cached], 120).decode().split()[0] != expected:
        raise host.TraceError("existing content cache entry failed integrity/ownership validation")
    if link:
        remote_command(adb, ["ln", "--", cached, destination])
    return reused


def parse_supervisor(output: bytes,protocol_byte_limit: int=64*1024,*,swap_image_file: bool=False) -> tuple[dict[str, object], str]:
    if type(swap_image_file) is not bool:raise host.TraceError('invalid swap image file opt-in')
    if (type(protocol_byte_limit) is not int or not 64*1024<=protocol_byte_limit<=
        host.physical_capture.protocol_byte_limit(host.physical_capture.MAX_BYTE_BUDGET)):
        raise host.TraceError('invalid requested supervisor output bound')
    if len(output) > protocol_byte_limit + 512:
        raise host.TraceError("Android supervisor exceeded its protocol bound")
    try:
        text = output.decode("utf-8", errors="strict")
        header, protocol = text.split("WORKER_PROTOCOL\n", 1)
        if len(protocol.encode('utf-8')) > protocol_byte_limit:
            raise ValueError('worker protocol bound')
        lines = header.splitlines()
        if lines[0] != "ANDROID_TRACE_SUPERVISOR_V1" or len(lines) != 6+int(swap_image_file):
            raise ValueError("header")
        pairs = [line.split("=", 1) for line in lines[1:]]
        fields = dict(pairs)
        expected={"outcome", "exit_code", "signal", "elapsed_milliseconds", "limits_verified"}
        if swap_image_file:expected.add('swap_image_file_limit')
        if set(fields) != expected or (swap_image_file and fields['swap_image_file_limit']!='3686400'):
            raise ValueError("fields")
        if fields["outcome"] not in SUPERVISOR_OUTCOMES:
            raise ValueError("outcome")
        exit_code, signal_number, elapsed, verified = [int(fields[key]) for key in (
            "exit_code", "signal", "elapsed_milliseconds", "limits_verified")]
        if not -1 <= exit_code <= 255 or not 0 <= signal_number <= 64 or elapsed < 0 or verified not in (0, 1):
            raise ValueError("counters")
        if fields["outcome"] == "exited" and (exit_code < 0 or signal_number != 0 or verified != 1):
            raise ValueError("exit status")
        if fields["outcome"] in {"cpu-limit", "unix-signal"} and (exit_code != -1 or signal_number == 0):
            raise ValueError("signal status")
        if protocol and (fields["outcome"] != "exited" or exit_code != 0 or verified != 1):
            raise ValueError("untrusted worker output")
    except (UnicodeError, ValueError, IndexError) as exc:
        raise host.TraceError("invalid Android supervisor protocol") from exc
    return {"outcome": fields["outcome"], "returncode": exit_code,
            "signal_number": signal_number, "elapsed_milliseconds": elapsed,
            "limits_verified": bool(verified)}, protocol


def worker_arguments(args: argparse.Namespace, ppc: Path, remote: str,
                     cached_paths: dict[str, str] | None = None) -> list[str]:
    # Only explicitly bound payloads may refer to the separately verified cache.
    # Executables, image and catalogs must remain in fresh per-run storage.
    cached_paths = cached_paths or {}
    allowed = {*(f"installed-file-{slot}.bin" for slot in range(len(args.installed_file))),
               *(f"content-{slot}.bin" for slot in range(len(args.read_only_file)))}
    if any(name not in allowed or CONTENT_CACHE_FILE.fullmatch(path) is None
           for name, path in cached_paths.items()):
        raise host.TraceError("invalid direct content-cache binding")
    def content_path(name: str) -> str:
        return cached_paths.get(name, f"{remote}/{name}")

    image = host.read_generated_layout(ppc)
    mapping_count = host.validate_function_mapping_source(ppc, image.code)
    values = [image.image.base, image.image.size, args.heap_base, args.heap_size,
              args.stack_base, args.stack_size, args.function_entry_budget,
              args.max_function_labels, mapping_count]
    result = [f"{remote}/host_guest_trace", f"{remote}/decoded.image.bin", *map(str, values)]
    if args.physical_base is not None:
        result.extend(map(str, [args.physical_base, args.physical_size, args.physical_address_offset]))
    if args.title_xex is not None:
        title = host.validate_title_environment_inputs(
            args.title_xex, args.decoded_image, image.image, args.title_header_base,
            args.title_export_address, args.title_module_base, host.repository_root())
        result.extend([f"{remote}/title.xex", *map(str, [title.raw_xex_size,
            title.full_image.size, title.header.base, title.header.size, title.import_slot,
            title.exported_variable.base, title.module.base, title.entry_point])])
        if args.thread_pcr_base is not None:
            thread = host.validate_thread_environment_inputs(title, image.image,
                args.thread_pcr_base, args.thread_record_base, args.thread_tls_base)
            result.extend(["--thread-environment", *map(str, [thread.pcr.base,
                thread.thread.base, thread.tls.base, thread.raw_data_address,
                thread.raw_data_size, thread.data_size, thread.slot_count])])
    if args.thread_pool_base is not None:
        result.extend(["--thread-pool", str(args.thread_pool_base), str(args.thread_pool_size)])
    if args.thread_type_base is not None:
        result.extend(["--thread-object-type", str(title.thread_object_type_slot), str(args.thread_type_base)])
    if args.installed_content_catalog is not None:
        result.extend(["--installed-content-catalog", f"{remote}/installed-content.bin"])
    for index, _ in args.installed_package:
        result.extend(["--installed-package", str(int(index, 0)), f"{remote}/installed-package-{int(index, 0)}.bin"])
    for slot, (index, record, _) in enumerate(args.installed_file):
        result.extend(["--installed-file", str(int(index, 0)), str(int(record, 0)), content_path(f"installed-file-{slot}.bin")])
    if args.file_metadata_catalog is not None:
        result.extend(["--file-metadata-catalog", f"{remote}/file-metadata.bin"])
    for slot, (index, _) in enumerate(args.read_only_file):
        result.extend(["--read-only-file", str(int(index, 0)), content_path(f"content-{slot}.bin")])
    if args.xam_module_base is not None:
        if title.xam_minimum_version is None or title.xam_minimum_version >> 28 != 2:
            raise host.TraceError("native XAM module requires a parser-validated Xbox 2.x minimum version")
        result.extend(["--native-xam-module", str(args.xam_module_base), str(title.xam_minimum_version)])
    if args.cooperative_threads:
        result.extend(["--cooperative-threads", str(args.thread_quantum)])
    if args.virtual_video_mode is not None:
        result.extend(["--virtual-video-mode", args.virtual_video_mode])
    if args.title_xex is not None:
        slots = host.unbound_data_import_slots(title, args.thread_type_base is not None, host.extra_bound_data_slots(args, title))
        if slots: result.extend(["--unbound-data-imports", ",".join(map(str, slots))])
        if args.hardware_info_base is not None:
            result.extend(["--hardware-info", str(title.hardware_info_slot), str(args.hardware_info_base)])
        if args.video_globals_base is not None:
            result.extend(["--video-globals", *map(str, title.video_device_slots), str(args.video_globals_base)])
        if args.uptime_bundle_base is not None:
            result.extend(["--uptime-bundle", str(title.uptime_bundle_slot), str(args.uptime_bundle_base)])
        if args.command_line_base is not None:
            result.extend(["--command-line", str(title.command_line_slot), str(args.command_line_base)])
        if args.no_debug_monitor_base is not None:
            result.extend(["--no-debug-monitor", str(title.debug_monitor_slot), str(args.no_debug_monitor_base)])
        if args.no_cert_monitor_base is not None:
            result.extend(["--no-cert-monitor", str(title.cert_monitor_slot), str(args.no_cert_monitor_base)])
        if args.hsio_lock_base is not None:
            result.extend(["--hsio-lock", str(title.hsio_lock_slot), str(args.hsio_lock_base)])
    if args.graphics_backend is not None:
        result.extend(["--graphics-backend", args.graphics_backend])
    if args.native_initial_coherency:
        if not args.graphics_backend or not args.cooperative_threads:
            raise host.TraceError("native initial coherency requires serialized native graphics")
        result.append("--native-initial-coherency")
    if args.experimental_native_initialization:
        if not args.native_initial_coherency:
            raise host.TraceError("experimental native initialization requires native initial coherency")
        result.append("--experimental-native-initialization")
    if args.native_empty_cache_coherency:
        if not args.experimental_native_initialization:
            raise host.TraceError("native empty-cache coherency requires experimental native initialization")
        result.append("--native-empty-cache-coherency")
    if args.native_state_batches:
        if not args.experimental_native_initialization:
            raise host.TraceError("native state batches require experimental native initialization")
        result.append("--native-state-batches")
    if args.capture_stopped_shaders:
        if not args.graphics_backend: raise host.TraceError("stopped shader capture requires native graphics")
        result.append("--capture-stopped-shaders")
    if args.capture_stopped_pm4:
        if not args.graphics_backend: raise host.TraceError("stopped PM4 capture requires native graphics")
        result.append("--capture-stopped-pm4")
    if args.experimental_gpu_clock_500mhz:
        if args.graphics_backend is None:
            raise host.TraceError("experimental GPU clock requires native graphics")
        result.append("--experimental-gpu-clock-500mhz")
    if args.xex_image_protections:
        if args.title_xex is None:
            raise host.TraceError("image protection metadata requires the explicit title XEX")
        result.append("--xex-image-protections")
    if args.disconnected_input:
        result.append("--disconnected-input")
    if args.experimental_native_audio:
        result.append("--experimental-native-audio")
    if args.experimental_native_audio_renderer:
        if not args.experimental_native_audio or not args.cooperative_threads:
            raise host.TraceError("native audio renderer requires native audio and cooperative threads")
        result.append("--experimental-native-audio-renderer")
    if args.native_event_wake_boost:
        if not args.cooperative_threads:raise host.TraceError("native event wake boost requires cooperative threads")
        result.append("--native-event-wake-boost")
    if args.native_category1_unity_gain:
        if not args.experimental_native_audio_renderer:raise host.TraceError("native category1 gain requires native PCM renderer")
        result.append("--native-category1-unity-gain")
    if args.native_critical_section_waits:
        if not args.cooperative_threads:raise host.TraceError("native critical-section waits require cooperative threads")
        result.append("--native-critical-section-waits")
    if args.native_high_priority_tiers:
        if not args.cooperative_threads:raise host.TraceError("native +3/+4 priority requires cooperative threads")
        result.append("--native-high-priority-tiers")
    if args.experimental_idle_cache_pair:
        if not args.native_state_batches or not args.native_empty_cache_coherency:
            raise host.TraceError('experimental idle cache pair requires native state and empty-cache profiles')
        result.append('--experimental-idle-cache-pair')
    try: host.physical_capture.validate_budget(args.capture_stopped_byte_budget)
    except ValueError as exc: raise host.TraceError(str(exc)) from exc
    if not args.capture_stopped_range and args.capture_stopped_byte_budget!=host.physical_capture.DEFAULT_BYTE_BUDGET:
        raise host.TraceError('nondefault physical capture budget requires explicit ranges')
    if args.capture_stopped_range:
        try: host.physical_capture.validate_ranges(args.capture_stopped_range,args.capture_stopped_byte_budget)
        except ValueError as exc: raise host.TraceError(str(exc)) from exc
        if not args.graphics_backend: raise host.TraceError('physical capture requires native graphics')
        if args.capture_stopped_byte_budget!=host.physical_capture.DEFAULT_BYTE_BUDGET:
            result.extend(['--capture-stopped-byte-budget',str(args.capture_stopped_byte_budget)])
        for address, count in args.capture_stopped_range:
            result.extend(['--capture-stopped-range',str(address),str(count)])
    if args.inspect_stopped_ownership:
        if not args.graphics_backend:raise host.TraceError('ownership queries require native graphics')
        try:host.physical_capture.validate_ownership_ranges(args.inspect_stopped_ownership)
        except ValueError as exc:raise host.TraceError(str(exc)) from exc
        for address,count in args.inspect_stopped_ownership:
            result.extend(['--inspect-stopped-ownership',str(address),str(count)])
    result.extend(host.native_kernel_version_arguments(args, title if args.title_xex is not None else None))
    return result


def run(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, add_help=False)
    parser.add_argument("--ndk", type=Path, required=True)
    parser.add_argument("--adb", type=Path, required=True)
    parser.add_argument("--serial", required=True)
    parser.add_argument("--api", type=int, default=26)
    parser.add_argument('--allow-stopped-swap-image',action='store_true',
                        help='private diagnostic only: bound each output file to one 1280x720 RGBA image')
    parser.add_argument('--private-trace-library',type=Path,
                        help='explicit prebuilt local ARM64 embedding library; run through the same bounded supervisor')
    parser.add_argument("--device-content-cache", help="explicit existing private cache directory created for this task")
    parser.add_argument("--device-content-cache-direct", action="store_true",
                        help="preopen verified cache payloads directly when hard links are unavailable")
    device, trace_argv = parser.parse_known_args(argv)
    private_library=validate_private_trace_library(device.private_trace_library) if device.private_trace_library else None
    if not 26 <= device.api <= 35 or not re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", device.serial):
        raise host.TraceError("invalid Android API or explicit device serial")
    if device.device_content_cache is not None and CONTENT_CACHE_DIRECTORY.fullmatch(device.device_content_cache) is None:
        raise host.TraceError("device content cache must be an explicit bounded private cache directory")
    if device.device_content_cache_direct and device.device_content_cache is None:
        raise host.TraceError("direct content-cache access requires an explicit cache directory")
    if not any(item == "--output-dir" or item.startswith("--output-dir=") for item in trace_argv):
        trace_argv.extend(["--output-dir", str(host.repository_root() / "local-analysis/android-host-guest-trace")])
    args = host.parse_args(trace_argv)
    host.run([*trace_argv, "--validate-only"])
    output_dir = args.output_dir.resolve()
    report_path = output_dir / "trace-report.json"
    report = json.loads(report_path.read_text())
    report.update({"mode": "guarded-native-android-diagnostic", "platform": "android",
                   "device": {"serial_redacted": True, "abi": "arm64-v8a", "target_api": device.api}})
    # The linked Vulkan loader needs more descriptors than the CPU-only host.
    report["limits"]["open_files"] = 128
    report["containment"].update({"mode": "on-device-supervised-process-group",
        "remote_wall_timeout": True, "limits_checked_on_device": False,
        "temporary_storage": "private-adb-shell-directory", "temporary_files_removed": False})
    report["device_content_cache"] = {"enabled": device.device_content_cache is not None,
        "mode": "direct-preopened-read-only" if device.device_content_cache_direct else
                "hardlink" if device.device_content_cache is not None else "disabled",
        "directory_redacted": True, "content_retained_between_runs": device.device_content_cache is not None,
        "verified_hits": 0, "verified_uploads": 0, "permissions": "0400-files-in-0700-directory"}
    host.write_report(report_path, report)
    if args.validate_only:
        return 0
    ndk = device.ndk.expanduser().resolve()
    toolchain = host.require_regular_file(ndk / "build/cmake/android.toolchain.cmake", "NDK toolchain")
    compiler_candidates = list((ndk / "toolchains/llvm/prebuilt").glob(f"*/bin/aarch64-linux-android{device.api}-clang"))
    if len(compiler_candidates) != 1:
        raise host.TraceError("NDK Android compiler is missing or ambiguous")
    compiler = compiler_candidates[0]
    cmake = shutil.which("cmake")
    if cmake is None or shutil.which("ninja") is None:
        raise host.TraceError("CMake and Ninja are required")
    ppc = args.generated_ppc_dir.resolve()
    bridge = args.guarded_bridge_dir.resolve()
    build = output_dir / ("library-driver" if private_library else "build")
    commands = [[cmake, "-S", str(Path(__file__).parent), "-B", str(build), "-G", "Ninja",
        f"-DCMAKE_TOOLCHAIN_FILE={toolchain}", "-DANDROID_ABI=arm64-v8a",
        f"-DANDROID_PLATFORM=android-{device.api}", "-DANDROID_STL=c++_static",
        f"-DPPC_DIR={ppc}", f"-DBRIDGE_DIR={bridge}",
        f"-DREPOSITORY_ROOT={host.repository_root()}", f"-DEXPECTED_CHUNK_COUNT={args.expected_chunk_count}"],
        [cmake, "--build", str(build), "--target", "host_guest_trace", "--parallel", str(args.jobs)],
        [str(compiler), "-std=c11", "-Wall", "-Wextra", "-Werror", "-O2",
         str(Path(__file__).with_name("android_trace_supervisor.c")), "-o", str(build / "supervisor")]]
    if private_library:
        if build.is_symlink() or (build.exists() and not build.is_dir()):
            raise host.TraceError('private library driver output must be a real directory')
        if private_library in ((build/'host_guest_trace').resolve(),(build/'supervisor').resolve()):
            raise host.TraceError('private library input aliases a generated driver output')
        build.mkdir(parents=True,exist_ok=True)
        commands=[[
            str(compiler),'-std=c11','-Wall','-Wextra','-Werror','-O2',
            str(Path(__file__).with_name('private_trace_loader.c')),'-ldl','-o',str(build/'host_guest_trace')],commands[-1]]
    for command in commands:
        outcome = host.run_command(command, args.build_timeout_seconds)
        if not outcome.succeeded:
            report["build"] = {"attempted": True, "succeeded": False,
                "diagnostic_line_count": len((outcome.stdout + outcome.stderr).splitlines())}
            host.write_report(report_path, report)
            raise host.TraceError("Android diagnostic build failed; details suppressed")
    report["build"] = {"attempted": True, "succeeded": True, "private_executable_sha256": digest(build / "host_guest_trace")}
    if private_library:
        report['mode']='guarded-native-android-library-diagnostic'
        report['build'].update(private_library_sha256=digest(private_library),
            private_library_bytes=private_library.stat().st_size,
            private_library_built_in_this_run=False,private_library_abi='recomp_run_private_trace_v1')
    host.write_report(report_path, report)
    adb = [str(device.adb.expanduser().resolve()), "-s", device.serial]
    if remote_command(adb, ["uname", "-m"]).strip() != b"aarch64":
        raise host.TraceError("selected Android device is not aarch64")
    actual_api = int(remote_command(adb, ["getprop", "ro.build.version.sdk"]).strip())
    if actual_api < device.api:
        raise host.TraceError("selected device API is below the compile target")
    report["device"]["runtime_api"] = actual_api
    report["device"]["emulator_reported"] = reported_emulator(adb)
    report["device"]["emulator_detection"] = "ro.kernel.qemu-self-report-not-hardware-attestation"
    remote = "/data/local/tmp/recomp-trace." + uuid.uuid4().hex
    created = False
    try:
        remote_command(adb, ["mkdir", "-m", "700", "--", remote])
        created = True
        inputs = [(build / "host_guest_trace", "host_guest_trace", "700"),
                  (build / "supervisor", "supervisor", "700"),
                  (args.decoded_image, "decoded.image.bin", "600")]
        if private_library:inputs.append((private_library,'libhost_guest_trace.so','600'))
        if args.title_xex is not None:
            inputs.append((args.title_xex, "title.xex", "600"))
        if args.file_metadata_catalog is not None:
            inputs.append((args.file_metadata_catalog, "file-metadata.bin", "600"))
        if args.installed_content_catalog is not None:
            inputs.append((args.installed_content_catalog, "installed-content.bin", "600"))
        for index, path in args.installed_package:
            inputs.append((Path(path), f"installed-package-{int(index, 0)}.bin", "600"))
        for slot, (_, _, path) in enumerate(args.installed_file):
            inputs.append((Path(path), f"installed-file-{slot}.bin", "600"))
        for slot, (_, path) in enumerate(args.read_only_file):
            inputs.append((Path(path), f"content-{slot}.bin", "600"))
        cached_paths: dict[str, str] = {}
        for source, filename, mode in inputs:
            destination = f"{remote}/{filename}"
            if device.device_content_cache is not None and filename.startswith(("content-", "installed-file-")):
                reused = stage_cached_content(adb, source, destination, device.device_content_cache,
                                              link=not device.device_content_cache_direct)
                report["device_content_cache"]["verified_hits" if reused else "verified_uploads"] += 1
                if device.device_content_cache_direct:
                    destination = f"{device.device_content_cache}/{digest(source)}.bin"
                    cached_paths[filename] = destination
                mode = "400"
            else:
                checked_command([*adb, "push", str(source), destination], 180)
                remote_command(adb, ["chmod", mode, "--", destination])
            observed = remote_command(adb, ["sha256sum", destination]).decode().split()[0]
            if observed != digest(source):
                raise host.TraceError("Android staged-file digest mismatch")
            if filename=='libhost_guest_trace.so' and observed!=report['build']['private_library_sha256']:
                raise host.TraceError('private library changed after build provenance was recorded')
            if remote_command(adb, ["stat", "-c", "%a", destination]).strip() != mode.encode():
                raise host.TraceError("Android staged-file permissions mismatch")
        execution = {"attempted": True, "succeeded": False, "outcome": "launch-failure",
                     "completed_function_entries": None, "public_stop_import": None}
        report["execution"] = execution
        protocol_byte_limit=host.physical_capture.protocol_byte_limit(args.capture_stopped_byte_budget)
        report['device']['supervisor_protocol_byte_limit']=protocol_byte_limit
        command = ["setsid", "-w", f"{remote}/supervisor", str(args.trace_timeout_seconds), str(args.cpu_time_seconds)]
        if protocol_byte_limit!=64*1024:command.extend(['--protocol-bytes',str(protocol_byte_limit)])
        worker=worker_arguments(args,ppc,remote,cached_paths)
        if (worker[-1]=='--capture-native-swap-image')!=device.allow_stopped_swap_image:
            raise host.TraceError('swap image worker flag and explicit file allowance must agree')
        if device.allow_stopped_swap_image:
            command.append('--swap-image-file')
            report['containment']['rlimit_file_bytes']=3686400
            report['containment']['private_stopped_swap_image_requested']=True
        command.extend([remote,*worker])
        # The native supervisor's wall clock and process-group termination run on
        # the device even if the host ADB connection disappears.
        output = remote_command(adb, command, args.trace_timeout_seconds + 20)
        try:
            supervised, protocol = parse_supervisor(output,protocol_byte_limit,swap_image_file=device.allow_stopped_swap_image)
        except host.TraceError:
            execution["outcome"] = "invalid-protocol"
            raise
        execution.update(supervised)
        report["containment"]["limits_checked_on_device"] = supervised["limits_verified"]
        if supervised["outcome"] == "exited" and supervised["returncode"] == 0 and supervised["limits_verified"]:
            try:
                parsed = host.parse_trace_protocol(protocol, args.max_function_labels,
                    args.function_entry_budget, report["indirect_calls"]["validated_mapping_count"],
                    cooperative=args.cooperative_threads,
                    guarded_data_slots=report["data_import_guard"]["unbound_slots"],
                    uptime_bundle_base=args.uptime_bundle_base)
                if (parsed.graphics_initialized is not None) != (args.graphics_backend is not None):
                    raise host.TraceError("graphics backend evidence does not match the requested mode")
                if (parsed.xex_image_protections is not None) != args.xex_image_protections:
                    raise host.TraceError("XEX image protection evidence does not match the requested binding")
                if (parsed.disconnected_input is not None) != args.disconnected_input:
                    raise host.TraceError("input configuration evidence does not match the requested binding")
                if (parsed.experimental_native_audio is not None) != args.experimental_native_audio:
                    raise host.TraceError("native audio evidence does not match the requested binding")
                if (parsed.native_audio_renderer is not None) != args.experimental_native_audio_renderer:
                    raise host.TraceError("native PCM renderer evidence does not match the requested binding")
                if (parsed.native_event_wake_boost is not None) != args.native_event_wake_boost:
                    raise host.TraceError("native event wake boost evidence does not match the requested binding")
                if (parsed.native_category1_gain is not None) != args.native_category1_unity_gain:
                    raise host.TraceError("native category1 gain evidence does not match the requested binding")
                if (parsed.native_critical_section_waits is not None) != args.native_critical_section_waits:
                    raise host.TraceError("native critical-section wait evidence does not match the requested binding")
                if (parsed.native_high_priority_tiers is not None) != args.native_high_priority_tiers:
                    raise host.TraceError("native +3/+4 priority evidence does not match the requested binding")
                if (parsed.experimental_idle_cache_pairs is not None) != args.experimental_idle_cache_pair:
                    raise host.TraceError('experimental idle cache pair evidence does not match the requested binding')
                if (parsed.experimental_gpu_clock_500mhz_reads is not None) != args.experimental_gpu_clock_500mhz:
                    raise host.TraceError("experimental GPU clock evidence does not match the requested mode")
                if (parsed.native_initial_coherency is not None) != args.native_initial_coherency:
                    raise host.TraceError("native initial coherency evidence does not match the requested mode")
                if (parsed.experimental_native_initialization is not None) != args.experimental_native_initialization:
                    raise host.TraceError("experimental native initialization evidence does not match the requested mode")
                if (parsed.native_edram_training_policy is not None) != (args.experimental_native_initialization and args.hsio_lock_base is not None):
                    raise host.TraceError("native EDRAM policy evidence does not match the requested mode")
                if (parsed.native_empty_cache_coherency is not None) != args.native_empty_cache_coherency:
                    raise host.TraceError("native empty-cache coherency evidence does not match the requested mode")
                if (parsed.native_state_batches is not None) != args.native_state_batches:
                    raise host.TraceError("native state batch evidence does not match the requested mode")
                if (parsed.native_bin_state is not None) != args.native_state_batches:
                    raise host.TraceError("retained bin state evidence does not match the requested mode")
                if (parsed.private_shader_capture is not None) != args.capture_stopped_shaders:
                    raise host.TraceError("private shader capture does not match the requested mode")
                if (parsed.private_pm4_capture is not None) != args.capture_stopped_pm4:
                    raise host.TraceError("private PM4 capture does not match the requested mode")
                try: host.physical_capture.require_requested(parsed.private_physical_capture,args.capture_stopped_range,args.capture_stopped_byte_budget)
                except ValueError as exc: raise host.TraceError(str(exc)) from exc
                try:host.physical_capture.require_ownership_requested(parsed.stopped_ownership_queries,args.inspect_stopped_ownership)
                except ValueError as exc:raise host.TraceError(str(exc)) from exc
            except host.TraceError:
                execution["outcome"] = "invalid-protocol"
                raise
            execution.update({"succeeded": True, "outcome": "clean-completion" if parsed.stop_reason == "completed" else "guard-stop",
                "stop_reason": parsed.stop_reason, "completed_function_entries": parsed.completed_function_entries,
                "public_stop_import": parsed.stop_import,
                "host_exception": parsed.stop_exception,
                "workers_created": parsed.workers_created, "workers_entered": parsed.workers_entered,
            "context_switches": parsed.context_switches,
            "wait_operations": parsed.wait_operations,
                "priority_request": parsed.priority_request,
                "file_create_request": parsed.file_create_request,
                "file_io": parsed.file_io,
                "content_mount_rejection": parsed.content_mount_rejection,
                "import_arguments": parsed.import_arguments,
                "scalar_access": parsed.scalar_access,
                "graphics_initialized": parsed.graphics_initialized,
                "graphics_ring": parsed.graphics_ring,
                "native_display": parsed.native_display,
                "experimental_gpu_clock_500mhz_reads": parsed.experimental_gpu_clock_500mhz_reads,
                "cpu_timebase": parsed.cpu_timebase,
                "stopped_gpu_submission": parsed.stopped_gpu_submission,
                "stopped_gpu_indirect_graph": parsed.stopped_gpu_indirect_graph,
                "native_initial_coherency": parsed.native_initial_coherency,
                "experimental_native_initialization": parsed.experimental_native_initialization,
                "invalid_indirect_call": parsed.invalid_indirect_call,
                "native_edram_training_policy": parsed.native_edram_training_policy,
                "native_memory_statistics": parsed.native_memory_statistics,
                "xex_image_protections": parsed.xex_image_protections,
                "disconnected_input": parsed.disconnected_input,
                "experimental_native_audio": parsed.experimental_native_audio,
                "native_audio_renderer": parsed.native_audio_renderer,
                "native_event_wake_boost": parsed.native_event_wake_boost,
                "native_category1_gain": parsed.native_category1_gain,
                "critical_section_rejection": parsed.critical_section_rejection,
                "native_critical_section_waits": parsed.native_critical_section_waits,
                "native_high_priority_tiers": parsed.native_high_priority_tiers,
                "thread_delays": parsed.thread_delays,
                "experimental_idle_cache_pairs": parsed.experimental_idle_cache_pairs,
                "dispatcher_wait_objects": parsed.dispatcher_wait_objects,
                "native_empty_cache_coherency": parsed.native_empty_cache_coherency,
                "native_state_batches": parsed.native_state_batches,
                "native_bin_state": parsed.native_bin_state,
                "native_ring_restart": parsed.native_ring_restart,
                "native_ring_warm_coherency": parsed.native_ring_warm_coherency,
                "native_warm_state_batches": parsed.native_warm_state_batches,
                "native_warm_empty_cache_coherency": parsed.native_warm_empty_cache_coherency,
                "native_owned_color_batch": parsed.native_owned_color_batch,
                "native_no_system_overlay": parsed.native_no_system_overlay,
                "native_swap_command": parsed.native_swap_command,
                "native_display_mode": parsed.native_display_mode,
                "native_command_storage_coherency": parsed.native_command_storage_coherency,
                "native_wrapped_swap": parsed.native_wrapped_swap,
                "native_post_output_wrapped_swap": parsed.native_post_output_wrapped_swap,
                "native_post_swap_system_context": parsed.native_post_swap_system_context,
                "stopped_post_swap_request": parsed.stopped_post_swap_request,
                "native_scaler_timing": parsed.native_scaler_timing,
                "native_scaler_command": parsed.native_scaler_command,
                "native_scaler_storage_coherency": parsed.native_scaler_storage_coherency,
                "native_pending_submission": parsed.native_pending_submission,
                "stopped_native_swap_request": parsed.stopped_native_swap_request,
                "stopped_native_swap_image": parsed.stopped_native_swap_image,
                "stopped_native_swap_consumer": parsed.stopped_native_swap_consumer,
                "stopped_gpu_work": parsed.stopped_gpu_work,
                "stopped_gpu_memory": parsed.stopped_gpu_memory,
                "stopped_word_staging": parsed.stopped_word_staging,
                "stopped_shader_proofs": parsed.stopped_shader_proofs,
                "stopped_constant_initialization": parsed.stopped_constant_initialization,
                "stopped_shader_capture": host.shader_capture.save_shader_capture(report_path.parent, parsed.private_shader_capture),
                "stopped_pm4_capture": host.pm4_capture.save_pm4_capture(report_path.parent, parsed.private_pm4_capture),
                "stopped_physical_capture": host.physical_capture.save_capture(report_path.parent, parsed.private_physical_capture),
                "stopped_native_pending_capture": host.physical_capture.save_capture(report_path.parent, parsed.private_native_pending_capture),
                "stopped_deferred_shader_capture": host.deferred_shader_capture.save_capture(report_path.parent, parsed.private_deferred_shader_capture),
                "stopped_deferred_color_inspection": parsed.stopped_deferred_color_inspection,
                "stopped_deferred_memory_inspection": parsed.stopped_deferred_memory_inspection,
                "stopped_deferred_color_gpu": parsed.stopped_deferred_color_gpu,
                "stopped_deferred_color_timeline": parsed.stopped_deferred_color_timeline,
                "stopped_deferred_color_pending_state": parsed.stopped_deferred_color_pending_state,
                "stopped_deferred_color_write_guard": parsed.stopped_deferred_color_write_guard,
                "stopped_ownership_queries": parsed.stopped_ownership_queries,
                "title_termination_registrations": parsed.title_termination_registrations,
                "debug_messages": {"content_suppressed": True, "count": parsed.debug_message_count, "bytes": parsed.debug_message_bytes},
                "function_trace": {"limit": args.max_function_labels, "captured": len(parsed.function_labels),
                    "dropped": parsed.dropped_function_labels, "retention": "most-recent", "labels": list(parsed.function_labels)}})
        elif supervised["outcome"] == "exited":
            execution["outcome"] = "worker-failure"
    finally:
        if created:
            try:
                for command in cleanup_commands(remote):
                    remote_command(adb, command)
                report["containment"]["temporary_files_removed"] = True
            except host.TraceError:
                # Preserve the exact private directory for an explicit retry.
                report["containment"]["cleanup_retry_directory"] = remote
        host.write_report(report_path, report)
    execution = report["execution"]
    print(f"Android diagnostic: {execution['outcome']}; {execution.get('completed_function_entries')} function entries; "
          f"stop {execution.get('public_stop_import')}. Redacted report: {report_path}")
    return 0 if execution["succeeded"] and report["containment"]["temporary_files_removed"] else 1


if __name__ == "__main__":
    try:
        raise SystemExit(run())
    except (host.TraceError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        raise SystemExit(2)
