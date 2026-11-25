#!/usr/bin/env python3
"""Build and run a bounded private generated-code trace on macOS."""

from __future__ import annotations

import argparse
import dataclasses
import importlib.util
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys
from typing import Sequence


SHADER_CAPTURE_SPEC = importlib.util.spec_from_file_location(
    "host_stopped_shader_artifacts", Path(__file__).with_name("stopped_shader_artifacts.py"))
assert SHADER_CAPTURE_SPEC and SHADER_CAPTURE_SPEC.loader
shader_capture = importlib.util.module_from_spec(SHADER_CAPTURE_SPEC)
SHADER_CAPTURE_SPEC.loader.exec_module(shader_capture)

PM4_CAPTURE_SPEC = importlib.util.spec_from_file_location(
    "host_stopped_pm4_artifacts", Path(__file__).with_name("stopped_pm4_artifacts.py"))
assert PM4_CAPTURE_SPEC and PM4_CAPTURE_SPEC.loader
pm4_capture = importlib.util.module_from_spec(PM4_CAPTURE_SPEC)
PM4_CAPTURE_SPEC.loader.exec_module(pm4_capture)

PHYSICAL_CAPTURE_SPEC = importlib.util.spec_from_file_location(
    "host_stopped_physical_artifacts", Path(__file__).with_name("stopped_physical_artifacts.py"))
assert PHYSICAL_CAPTURE_SPEC and PHYSICAL_CAPTURE_SPEC.loader
physical_capture = importlib.util.module_from_spec(PHYSICAL_CAPTURE_SPEC)
PHYSICAL_CAPTURE_SPEC.loader.exec_module(physical_capture)

DEFERRED_SHADER_SPEC = importlib.util.spec_from_file_location(
    "host_deferred_shader_artifacts", Path(__file__).with_name("deferred_shader_artifacts.py"))
assert DEFERRED_SHADER_SPEC and DEFERRED_SHADER_SPEC.loader
deferred_shader_capture = importlib.util.module_from_spec(DEFERRED_SHADER_SPEC)
DEFERRED_SHADER_SPEC.loader.exec_module(deferred_shader_capture)

GUEST_SIZE = 1 << 32
PAGE_SIZE = 0x1000
MODULE_STRUCTURE_SIZE = 0x64
PCR_SIZE = 0x2D8
THREAD_RECORD_SIZE = 0x170
MAX_STATIC_TLS_BYTES = 1024 * 1024
MAX_TLS_SLOTS = 2048
MAXIMUM_XEX_HEADER_SIZE = 16 * 1024 * 1024
MAXIMUM_GUEST_FILE_READ_BYTES = 16 * 1024 * 1024
MAXIMUM_RAW_XEX_SIZE = 512 * 1024 * 1024
EXECUTABLE_MODULE_HANDLE_ORDINAL = 0x193
# Startup now legitimately exceeds the original diagnostic ceiling. This is
# still an explicit per-run bound; independent CPU/wall/output limits remain.
MAX_FUNCTION_ENTRIES = 10_000_000
MAX_FUNCTION_LABELS = 1_000
MAX_TRACE_SECONDS = 300
MAX_FUNCTION_MAPPINGS = 1_000_000
MAX_CPU_SECONDS = 300
CHUNK_PATTERN = re.compile(r"^ppc_recomp\.(\d+)\.cpp$")
PUBLIC_IMPORT_PATTERN = re.compile(r"^__imp__[A-Za-z0-9_]{1,120}$")
FUNCTION_LABEL_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")
CONFIG_DEFINE = re.compile(
    r"(?m)^\s*#define\s+(PPC_IMAGE_BASE|PPC_IMAGE_SIZE|PPC_CODE_BASE|PPC_CODE_SIZE)\s+"
    r"(0x[0-9A-Fa-f]+|[0-9]+)(?:ull|ULL|ul|UL|u|U|ll|LL)?\s*$"
)
MAPPING_DECLARATION = "PPCFuncMapping PPCFuncMappings[] = {"
MAPPING_ENTRY = re.compile(
    r"^\{\s*(0x[0-9A-Fa-f]+),\s*([A-Za-z_][A-Za-z0-9_]*)\s*\},$"
)
MAPPING_SENTINEL = re.compile(r"^\{\s*0,\s*nullptr\s*\}$")

XEX_INFO_PATH = Path(__file__).resolve().parents[1] / "xex_info/xex_info.py"
XEX_INFO_SPEC = importlib.util.spec_from_file_location(
    "host_guest_trace_xex_info", XEX_INFO_PATH
)
if XEX_INFO_SPEC is None or XEX_INFO_SPEC.loader is None:
    raise RuntimeError("cannot load the tracked XEX metadata parser")
xex_info = importlib.util.module_from_spec(XEX_INFO_SPEC)
sys.modules[XEX_INFO_SPEC.name] = xex_info
XEX_INFO_SPEC.loader.exec_module(xex_info)

FILE_METADATA_SPEC = importlib.util.spec_from_file_location(
    "host_guest_file_metadata", Path(__file__).with_name("file_metadata_catalog.py"))
assert FILE_METADATA_SPEC and FILE_METADATA_SPEC.loader
file_metadata = importlib.util.module_from_spec(FILE_METADATA_SPEC)
FILE_METADATA_SPEC.loader.exec_module(file_metadata)

CONTENT_CATALOG_SPEC = importlib.util.spec_from_file_location(
    "host_guest_content_catalog", Path(__file__).with_name("content_catalog.py"))
assert CONTENT_CATALOG_SPEC and CONTENT_CATALOG_SPEC.loader
content_catalog = importlib.util.module_from_spec(CONTENT_CATALOG_SPEC)
CONTENT_CATALOG_SPEC.loader.exec_module(content_catalog)

CONTAINED_PROCESS_PATH = Path(__file__).resolve().parent / "contained_process.py"
CONTAINED_PROCESS_SPEC = importlib.util.spec_from_file_location(
    "host_guest_trace_contained_process", CONTAINED_PROCESS_PATH
)
if CONTAINED_PROCESS_SPEC is None or CONTAINED_PROCESS_SPEC.loader is None:
    raise RuntimeError("cannot load the tracked contained-process runner")
contained_process = importlib.util.module_from_spec(CONTAINED_PROCESS_SPEC)
sys.modules[CONTAINED_PROCESS_SPEC.name] = contained_process
CONTAINED_PROCESS_SPEC.loader.exec_module(contained_process)


class TraceError(RuntimeError):
    """A validation or host-trace orchestration failure."""


@dataclasses.dataclass(frozen=True)
class GuestRange:
    name: str
    base: int
    size: int

    @property
    def end(self) -> int:
        return self.base + self.size


@dataclasses.dataclass(frozen=True)
class CommandOutcome:
    returncode: int | None
    stdout: str
    stderr: str
    failure_kind: str | None = None

    @property
    def succeeded(self) -> bool:
        return self.returncode == 0 and self.failure_kind is None


@dataclasses.dataclass(frozen=True)
class TraceProtocol:
    stop_reason: str
    completed_function_entries: int
    stop_import: str | None
    debug_message_count: int
    debug_message_bytes: int
    dropped_function_labels: int
    validated_mapping_count: int
    function_labels: tuple[str, ...]
    workers_created: int = 0
    workers_entered: int = 0
    context_switches: int = 0
    wait_operations: int = 0
    stop_exception: str | None = None
    priority_request: dict[str, int | bool] | None = None
    file_create_request: dict[str, int] | None = None
    file_io: dict[str, int] | None = None
    content_mount_rejection: int | None = None
    import_arguments: dict[str, int] | None = None
    scalar_access: dict[str, int] | None = None
    invalid_indirect_call: dict[str, int] | None = None
    graphics_initialized: bool | None = None
    graphics_ring: dict[str, int] | None = None
    native_display: dict[str, object] | None = None
    experimental_gpu_clock_500mhz_reads: int | None = None
    cpu_timebase: dict[str, object] | None = None
    stopped_gpu_submission: dict[str, object] | None = None
    stopped_gpu_indirect_graph: dict[str, object] | None = None
    native_initial_coherency: dict[str, object] | None = None
    experimental_native_initialization: dict[str, object] | None = None
    native_edram_training_policy: dict[str, object] | None = None
    native_memory_statistics: dict[str, object] | None = None
    native_kernel_version: dict[str, object] | None = None
    xex_image_protections: dict[str, object] | None = None
    disconnected_input: dict[str, object] | None = None
    experimental_native_audio: dict[str, object] | None = None
    native_audio_renderer: dict[str, object] | None = None
    native_event_wake_boost: dict[str, object] | None = None
    native_category1_gain: dict[str, object] | None = None
    critical_section_rejection: dict[str, object] | None = None
    native_critical_section_waits: dict[str, object] | None = None
    native_high_priority_tiers: dict[str, object] | None = None
    thread_delays: dict[str, object] | None = None
    experimental_idle_cache_pairs: dict[str, object] | None = None
    dispatcher_wait_objects: list[dict[str, object]] | None = None
    native_empty_cache_coherency: dict[str, object] | None = None
    native_state_batches: dict[str, object] | None = None
    native_bin_state: dict[str, object] | None = None
    native_ring_restart: dict[str, object] | None = None
    native_ring_warm_coherency: dict[str, object] | None = None
    native_warm_state_batches: dict[str, object] | None = None
    native_warm_empty_cache_coherency: dict[str, object] | None = None
    native_owned_color_batch: dict[str, object] | None = None
    native_no_system_overlay: dict[str, object] | None = None
    native_swap_command: dict[str, object] | None = None
    native_display_mode: dict[str, object] | None = None
    native_command_storage_coherency: dict[str, object] | None = None
    native_wrapped_swap: dict[str, object] | None = None
    native_scaler_timing: dict[str, object] | None = None
    native_scaler_command: dict[str, object] | None = None
    native_scaler_storage_coherency: dict[str, object] | None = None
    native_pending_submission: dict[str, object] | None = None
    native_scaler_wrapper_plan: dict[str, object] | None = None
    native_scaler_interrupt: dict[str, object] | None = None
    native_scaler_completion: dict[str, object] | None = None
    native_post_scaler_storage: dict[str, object] | None = None
    native_display_gamma: dict[str, object] | None = None
    native_post_display_storage: dict[str, object] | None = None
    native_post_output_storage: dict[str, object] | None = None
    native_post_output_color: dict[str, object] | None = None
    native_post_output_system_context: dict[str, object] | None = None
    native_post_output_swap: dict[str, object] | None = None
    native_post_output_swap_storage: dict[str, object] | None = None
    native_post_output_wrapped_swap: dict[str, object] | None = None
    native_post_swap_system_context: dict[str, object] | None = None
    stopped_post_swap_request: dict[str, object] | None = None
    native_deferred_swap_request: dict[str, object] | None = None
    native_deferred_swap_storage: dict[str, object] | None = None
    native_deferred_color_prefix: dict[str, object] | None = None
    stopped_deferred_swap_interrupt: dict[str, object] | None = None
    native_display_output: dict[str, object] | None = None
    stopped_native_swap_request: dict[str, object] | None = None
    stopped_native_swap_image: dict[str, object] | None = None
    stopped_native_swap_consumer: dict[str, object] | None = None
    stopped_gpu_work: dict[str, object] | None = None
    stopped_gpu_memory: dict[str, object] | None = None
    stopped_word_staging: dict[str, object] | None = None
    stopped_shader_proofs: dict[str, object] | None = None
    stopped_constant_initialization: dict[str, object] | None = None
    private_shader_capture: dict[str, object] | None = None
    private_pm4_capture: dict[str, object] | None = None
    private_physical_capture: dict[str, object] | None = None
    private_native_pending_capture: dict[str, object] | None = None
    private_deferred_shader_capture: dict[str, object] | None = None
    stopped_deferred_color_inspection: dict[str, object] | None = None
    stopped_deferred_memory_inspection: dict[str, object] | None = None
    stopped_deferred_color_gpu: dict[str, object] | None = None
    stopped_deferred_color_timeline: dict[str, object] | None = None
    stopped_deferred_color_pending_state: dict[str, object] | None = None
    stopped_deferred_color_write_guard: dict[str, object] | None = None
    stopped_ownership_queries: dict[str, object] | None = None
    title_termination_registrations: int | None = None


@dataclasses.dataclass(frozen=True)
class TitleEnvironmentInputs:
    raw_xex: Path
    raw_xex_size: int
    full_image: GuestRange
    header: GuestRange
    import_slot: int
    exported_variable: GuestRange
    module: GuestRange
    entry_point: int
    tls_info: dict[str, object] | None = None
    thread_object_type_slot: int | None = None
    xam_minimum_version: int | None = None
    title_id: int | None = None
    data_import_slots: tuple[int, ...] = ()
    hardware_info_slot: int | None = None
    video_device_slots: tuple[int, ...] = ()
    uptime_bundle_slot: int | None = None
    command_line_slot: int | None = None
    debug_monitor_slot: int | None = None
    cert_monitor_slot: int | None = None
    hsio_lock_slot: int | None = None
    kernel_version_slot: int | None = None
    kernel_minimum_version: int | None = None


@dataclasses.dataclass(frozen=True)
class PhysicalArenaInputs:
    memory: GuestRange
    physical_address_offset: int


@dataclasses.dataclass(frozen=True)
class ThreadEnvironmentInputs:
    pcr: GuestRange
    thread: GuestRange
    tls: GuestRange
    raw_data_address: int
    raw_data_size: int
    data_size: int
    slot_count: int


@dataclasses.dataclass(frozen=True)
class GeneratedLayout:
    image: GuestRange
    code: GuestRange


def repository_root() -> Path:
    return Path(__file__).resolve().parents[2]


def parse_integer(text: str) -> int:
    try:
        value = int(text, 0)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"invalid integer: {text}") from exc
    if value < 0:
        raise argparse.ArgumentTypeError("integer must not be negative")
    return value


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def require_private_path(path: Path, root: Path, label: str) -> Path:
    resolved = path.expanduser().resolve()
    private_root = (root / "local-analysis").resolve()
    if not _is_relative_to(resolved, private_root):
        raise TraceError(f"{label} must be below {private_root}")
    if ";" in str(resolved):
        raise TraceError(f"{label} contains an unsupported semicolon")
    return resolved


def require_git_ignored(path: Path, root: Path, label: str) -> None:
    relative = path.resolve().relative_to(root.resolve())
    try:
        completed = subprocess.run(
            ["git", "-C", str(root), "check-ignore", "--quiet", "--", relative.as_posix()],
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError as exc:
        raise TraceError(f"cannot verify Git-ignore status for {label}: {exc}") from exc
    if completed.returncode == 1:
        raise TraceError(f"{label} is not Git-ignored")
    if completed.returncode != 0:
        raise TraceError(f"git check-ignore failed for {label}")


def validate_private_directory(path: Path, root: Path, label: str) -> Path:
    if path.expanduser().absolute().is_symlink():
        raise TraceError(f"{label} is a symlink")
    resolved = require_private_path(path, root, label)
    if not resolved.is_dir():
        raise TraceError(f"{label} is missing")
    require_git_ignored(resolved, root, label)
    return resolved


def require_regular_file(path: Path, label: str) -> Path:
    resolved = path.expanduser().resolve()
    if path.expanduser().is_symlink() or not resolved.is_file():
        raise TraceError(f"{label} is missing, non-regular, or a symlink")
    return resolved


def validate_decoded_image(path: Path, expected_size: int, root: Path) -> Path:
    if not path.expanduser().is_absolute():
        raise TraceError("decoded image path must be absolute")
    resolved = require_regular_file(path, "decoded image")
    if _is_relative_to(resolved, root.resolve()):
        require_git_ignored(resolved, root, "decoded image")
    if resolved.stat().st_size != expected_size:
        raise TraceError(
            f"decoded image size is {resolved.stat().st_size}, expected {expected_size}"
        )
    return resolved


def _parse_report_address(value: object, label: str) -> int:
    if not isinstance(value, str) or re.fullmatch(r"0x[0-9A-F]{8}", value) is None:
        raise TraceError(f"XEX metadata has an invalid {label}")
    return int(value, 16)


def select_executable_module_handle_slot(
    metadata: object,
    image: GuestRange,
) -> tuple[int, int, int, int]:
    if not isinstance(metadata, dict) or metadata.get("schema_version") != 2:
        raise TraceError("XEX metadata parser did not return schema version 2")
    image_base = _parse_report_address(
        metadata.get("image_base_address"), "image base address"
    )
    entry_point = _parse_report_address(metadata.get("entry_point"), "entry point")
    security = metadata.get("security")
    imports = metadata.get("import_libraries")
    if not isinstance(security, dict) or not isinstance(imports, dict):
        raise TraceError("XEX metadata is missing security or import-library data")
    image_size = security.get("image_size")
    if (
        image_base != image.base
        or not isinstance(image_size, int)
        or isinstance(image_size, bool)
        or image.size > image_size
    ):
        raise TraceError("XEX image layout does not match generated PPC configuration")
    full_image = GuestRange("title_image", image_base, image_size)
    validate_range(full_image, alignment=1)
    if not full_image.base <= entry_point < full_image.end:
        raise TraceError("XEX entry point is outside the security image")
    if imports.get("decoded_records") is not True:
        raise TraceError("XEX import records were not decoded against the image")
    data_imports = imports.get("data_imports")
    if not isinstance(data_imports, list):
        raise TraceError("XEX metadata is missing decoded data imports")
    matches = [
        entry
        for entry in data_imports
        if isinstance(entry, dict)
        and entry.get("library") == "xboxkrnl.exe"
        and entry.get("ordinal") == EXECUTABLE_MODULE_HANDLE_ORDINAL
    ]
    if len(matches) != 1:
        raise TraceError(
            "XEX must contain exactly one xboxkrnl.exe data import at ordinal 0x193"
        )
    slot = _parse_report_address(matches[0].get("slot_address"), "data-import slot")
    if slot < image.base or slot + 4 > image.end:
        raise TraceError("XEX executable-module data-import slot is outside the image")
    header_size = metadata.get("header_size")
    if (
        not isinstance(header_size, int)
        or isinstance(header_size, bool)
        or not 24 <= header_size <= MAXIMUM_XEX_HEADER_SIZE
    ):
        raise TraceError("XEX declared header size is outside the supported bound")
    return slot, entry_point, header_size, image_size


def validate_title_environment_inputs(
    raw_xex_path: Path,
    decoded_image_path: Path,
    image: GuestRange,
    header_base: int,
    exported_variable_address: int,
    module_address: int,
    root: Path,
) -> TitleEnvironmentInputs:
    if not raw_xex_path.expanduser().is_absolute():
        raise TraceError("raw XEX path must be absolute")
    raw_xex = require_regular_file(raw_xex_path, "raw XEX")
    if _is_relative_to(raw_xex, root.resolve()):
        require_git_ignored(raw_xex, root, "raw XEX")
    try:
        raw_xex_size = raw_xex.stat().st_size
    except OSError as exc:
        raise TraceError(f"cannot inspect raw XEX size: {exc}") from exc
    if not 24 <= raw_xex_size <= MAXIMUM_RAW_XEX_SIZE:
        raise TraceError("raw XEX file size is outside the bounded parser limit")
    try:
        raw_bytes = raw_xex.read_bytes()
        decoded_bytes = decoded_image_path.read_bytes()
        metadata = xex_info.parse_xex2(raw_bytes, decoded_bytes)
    except (OSError, xex_info.XexFormatError) as exc:
        raise TraceError(f"raw XEX or decoded image failed bounded parser validation: {exc}") from exc
    slot, entry_point, header_size, security_image_size = select_executable_module_handle_slot(
        metadata, image
    )
    if len(raw_bytes) != raw_xex_size or metadata.get("file_size") != raw_xex_size:
        raise TraceError("XEX parser file-size result is inconsistent")
    header = GuestRange("xex_header", header_base, header_size)
    exported_variable = GuestRange(
        "module_handle_export", exported_variable_address, 4
    )
    module = GuestRange("title_module", module_address, MODULE_STRUCTURE_SIZE)
    for value in (header, exported_variable, module):
        validate_range(value, alignment=4)
        if value.base == 0:
            raise TraceError(f"{value.name} base must be nonzero")
    type_matches = [entry for entry in metadata["import_libraries"]["data_imports"]
                    if isinstance(entry, dict) and entry.get("library") == "xboxkrnl.exe"
                    and entry.get("ordinal") == 0x1B]
    type_slot = (_parse_report_address(type_matches[0].get("slot_address"), "thread type slot")
                 if len(type_matches) == 1 else None)
    if type_slot is not None and not image.base <= type_slot <= image.end - 4:
        raise TraceError("thread object type slot is outside the decoded image")
    data_slots = tuple(_parse_report_address(entry.get("slot_address"), "data import slot")
                       for entry in metadata["import_libraries"]["data_imports"])
    if len(data_slots) > 256 or len(set(data_slots)) != len(data_slots) or any(
            address & 3 or not image.base <= address <= image.end - 4 for address in data_slots):
        raise TraceError("invalid or excessive data import slots")
    def kernel_slot(ordinal):
        matched = [entry for entry in metadata["import_libraries"]["data_imports"]
                   if entry.get("library") == "xboxkrnl.exe" and entry.get("ordinal") == ordinal]
        return _parse_report_address(matched[0]["slot_address"], "kernel data slot") if len(matched) == 1 else None
    video_slots = (kernel_slot(0x1BE), kernel_slot(0x1BF))
    xam_libraries = [library for library in metadata["import_libraries"]["libraries"]
                     if library.get("name", "").lower() == "xam.xex"]
    xam_minimum_version = (_parse_report_address(xam_libraries[0]["minimum_version_raw"], "XAM minimum version")
                           if len(xam_libraries) == 1 else None)
    kernel_libraries = [library for library in metadata["import_libraries"]["libraries"]
                        if library.get("name", "").lower() == "xboxkrnl.exe"]
    kernel_minimum_version = (_parse_report_address(kernel_libraries[0]["minimum_version_raw"], "kernel minimum version")
                              if len(kernel_libraries) == 1 else None)
    return TitleEnvironmentInputs(
        raw_xex=raw_xex,
        raw_xex_size=raw_xex_size,
        full_image=GuestRange("title_image", image.base, security_image_size),
        header=header,
        import_slot=slot,
        exported_variable=exported_variable,
        module=module,
        entry_point=entry_point,
        tls_info=metadata.get("tls_info"),
        thread_object_type_slot=type_slot,
        xam_minimum_version=xam_minimum_version,
        title_id=(_parse_report_address("0x" + metadata["execution_info"]["title_id"], "title ID")
                  if metadata.get("execution_info") else None),
        data_import_slots=data_slots,
        hardware_info_slot=kernel_slot(0x156),
        uptime_bundle_slot=kernel_slot(0xAD),
        command_line_slot=kernel_slot(0x1AE),
        debug_monitor_slot=kernel_slot(0x59),
        cert_monitor_slot=kernel_slot(0x266),
        hsio_lock_slot=kernel_slot(0x1C1),
        kernel_version_slot=kernel_slot(0x158),
        kernel_minimum_version=kernel_minimum_version,
        video_device_slots=video_slots if None not in video_slots else (),
    )


def native_kernel_version_arguments(args: argparse.Namespace,
                                    title: TitleEnvironmentInputs | None) -> list[str]:
    storage = getattr(args, 'native_kernel_version_base', None)
    if storage is None:
        return []
    if (title is None or title.kernel_version_slot is None or
            title.kernel_minimum_version is None or title.kernel_minimum_version >> 28 != 2 or
            getattr(args, 'xam_module_base', None) is None):
        raise TraceError('kernel version requires native XAM and one parsed Xbox 2.x kernel DATA export/minimum version')
    validate_range(GuestRange('native_kernel_version', storage, 8), alignment=4)
    if not storage:
        raise TraceError('kernel version storage must be nonzero')
    # Keep this slot in the unresolved list: the native runner validates and
    # relocates it before removing exactly this entry from the scalar guard.
    return ['--native-kernel-version', str(title.kernel_version_slot), str(storage),
            str(title.kernel_minimum_version)]


def unbound_data_import_slots(title: TitleEnvironmentInputs, thread_type_bound: bool,
                              extra_bound: Sequence[int] = ()) -> tuple[int, ...]:
    bound = {title.import_slot, *extra_bound}
    if thread_type_bound:
        bound.add(title.thread_object_type_slot)
    return tuple(sorted(set(title.data_import_slots) - bound))


def extra_bound_data_slots(args: argparse.Namespace, title: TitleEnvironmentInputs) -> tuple[int, ...]:
    return (*((title.hardware_info_slot,) if args.hardware_info_base is not None else ()),
            *((title.uptime_bundle_slot,) if args.uptime_bundle_base is not None else ()),
            *((title.command_line_slot,) if args.command_line_base is not None else ()),
            *((title.debug_monitor_slot,) if args.no_debug_monitor_base is not None else ()),
            *((title.cert_monitor_slot,) if args.no_cert_monitor_base is not None else ()),
            *((title.hsio_lock_slot,) if args.hsio_lock_base is not None else ()),
            *(title.video_device_slots if args.video_globals_base is not None else ()))


def validate_thread_environment_inputs(
    title: TitleEnvironmentInputs,
    image: GuestRange,
    pcr_base: int,
    thread_base: int,
    tls_base: int,
) -> ThreadEnvironmentInputs:
    info = title.tls_info
    if not isinstance(info, dict):
        raise TraceError("thread staging requires validated XEX TLS metadata")
    counts = [info.get(key) for key in ("raw_data_size", "data_size", "slot_count")]
    if any(not isinstance(value, int) or isinstance(value, bool) for value in counts):
        raise TraceError("XEX TLS metadata has invalid sizes or slot count")
    raw_size, data_size, slot_count = counts
    if not (
        0 <= raw_size <= data_size <= MAX_STATIC_TLS_BYTES
        and data_size % 4 == 0
        and 1 <= slot_count <= MAX_TLS_SLOTS
    ):
        raise TraceError("XEX TLS metadata exceeds the supported storage bounds")
    raw_address = _parse_report_address(info.get("raw_data_address"), "TLS template address")
    if raw_size and not (image.base <= raw_address and raw_address + raw_size <= image.end):
        raise TraceError("XEX TLS template is outside the decoded image")
    return ThreadEnvironmentInputs(
        pcr=GuestRange("thread_pcr", pcr_base, PCR_SIZE),
        thread=GuestRange("thread_record", thread_base, THREAD_RECORD_SIZE),
        tls=GuestRange("thread_tls", tls_base, data_size + slot_count * 4),
        raw_data_address=raw_address,
        raw_data_size=raw_size,
        data_size=data_size,
        slot_count=slot_count,
    )


def read_generated_layout(ppc_dir: Path) -> GeneratedLayout:
    config = require_regular_file(ppc_dir / "ppc_config.h", "private ppc_config.h")
    try:
        matches = CONFIG_DEFINE.findall(config.read_text(encoding="utf-8"))
        values = {name: int(value, 0) for name, value in matches}
    except (OSError, UnicodeError) as exc:
        raise TraceError(f"cannot read private ppc_config.h: {exc}") from exc
    required = {"PPC_IMAGE_BASE", "PPC_IMAGE_SIZE", "PPC_CODE_BASE", "PPC_CODE_SIZE"}
    if len(matches) != 4 or set(values) != required:
        raise TraceError("private ppc_config.h must define one image and code base/size")
    image = GuestRange("image", values["PPC_IMAGE_BASE"], values["PPC_IMAGE_SIZE"])
    code = GuestRange("code", values["PPC_CODE_BASE"], values["PPC_CODE_SIZE"])
    validate_range(image, alignment=1)
    validate_range(code, alignment=4)
    if code.base < image.base or code.end > image.end:
        raise TraceError("configured PPC code range is outside the decoded image")
    return GeneratedLayout(image=image, code=code)


def read_image_layout(ppc_dir: Path) -> GuestRange:
    return read_generated_layout(ppc_dir).image


def validate_function_mapping_source(ppc_dir: Path, code: GuestRange) -> int:
    mapping_path = require_regular_file(
        ppc_dir / "ppc_func_mapping.cpp", "private ppc_func_mapping.cpp"
    )
    try:
        lines = mapping_path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as exc:
        raise TraceError(f"cannot read private function mapping: {exc}") from exc
    declarations = [
        index for index, line in enumerate(lines) if line.strip() == MAPPING_DECLARATION
    ]
    if len(declarations) != 1:
        raise TraceError("private function mapping must have one canonical declaration")
    declaration_index = declarations[0]
    closing_indices = [
        index
        for index in range(declaration_index + 1, len(lines))
        if lines[index].strip() == "};"
    ]
    if len(closing_indices) != 1:
        raise TraceError("private function mapping must have one canonical terminator")
    body = [
        line.strip()
        for line in lines[declaration_index + 1 : closing_indices[0]]
        if line.strip()
    ]
    if not body or MAPPING_SENTINEL.fullmatch(body[-1]) is None:
        raise TraceError("private function mapping lacks its final null sentinel")
    if any(MAPPING_SENTINEL.fullmatch(line) is not None for line in body[:-1]):
        raise TraceError("private function mapping contains an early null sentinel")
    entry_lines = body[:-1]
    if not 1 <= len(entry_lines) <= MAX_FUNCTION_MAPPINGS:
        raise TraceError("private function mapping count exceeds the validated cap")

    previous = -1
    for line in entry_lines:
        match = MAPPING_ENTRY.fullmatch(line)
        if match is None:
            raise TraceError("private function mapping contains a non-canonical entry")
        address = int(match.group(1), 16)
        if address < code.base or address >= code.end:
            raise TraceError("private function mapping address is outside configured code")
        if (address & 3) != 0:
            raise TraceError("private function mapping address is not 4-byte aligned")
        if address <= previous:
            raise TraceError("private function mapping addresses are not strictly sorted and unique")
        previous = address
    return len(entry_lines)


def discover_chunks(ppc_dir: Path, expected_count: int) -> list[Path]:
    numbered: list[tuple[int, Path]] = []
    for candidate in ppc_dir.iterdir():
        match = CHUNK_PATTERN.fullmatch(candidate.name)
        if not match:
            continue
        if candidate.is_symlink() or not candidate.is_file():
            raise TraceError("private PPC chunk is non-regular or a symlink")
        numbered.append((int(match.group(1)), candidate))
    numbered.sort(key=lambda item: item[0])
    indices = [index for index, _ in numbered]
    if len(indices) != expected_count:
        raise TraceError(f"found {len(indices)} PPC chunks, expected {expected_count}")
    if indices != list(range(expected_count)):
        raise TraceError("private PPC chunks must be contiguous and zero-based")

    for name in ("ppc_context.h", "ppc_recomp_shared.h", "ppc_func_mapping.cpp"):
        require_regular_file(ppc_dir / name, f"private {name}")
    try:
        context = (ppc_dir / "ppc_context.h").read_text(encoding="utf-8")
        shared = (ppc_dir / "ppc_recomp_shared.h").read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise TraceError(f"cannot read private generated headers: {exc}") from exc
    if "PPC_RUNTIME_CHECKPOINT();" not in context:
        raise TraceError("private PPC context lacks the instrumentable prologue hook")
    if re.search(r"(?m)^\s*PPC_EXTERN_FUNC\(_xstart\);\s*$", shared) is None:
        raise TraceError("private generated declarations do not contain _xstart")
    return [path for _, path in numbered]


def validate_guarded_bridge(bridge_dir: Path, expected_chunk_count: int) -> dict[str, object]:
    required = (
        "generated_guest_import_bridge.cpp",
        "generated_guest_import_bridge.h",
        "remaining_xbox_import_guarded_stops.cpp",
        "runtime-bridge-report.json",
    )
    for name in required:
        require_regular_file(bridge_dir / name, f"private bridge {name}")
    try:
        report = json.loads(
            (bridge_dir / "runtime-bridge-report.json").read_text(encoding="utf-8")
        )
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise TraceError(f"cannot read guarded bridge report: {exc}") from exc
    if not isinstance(report, dict) or report.get("schema_version") != 2:
        raise TraceError("guarded bridge report must use schema version 2")
    if report.get("guest_execution_connected") is not False:
        raise TraceError("guarded bridge report unexpectedly claims connected execution")
    if report.get("runnable_guest_support") is not False:
        raise TraceError("guarded bridge report must remain non-runnable")
    inputs = report.get("inputs")
    stops = report.get("unimplemented_import_stops")
    partition = report.get("symbol_partition_validation")
    strict_link = report.get("strict_link")
    if not all(isinstance(value, dict) for value in (inputs, stops, partition, strict_link)):
        raise TraceError("guarded bridge report is missing required sections")
    expected_object_count = expected_chunk_count + 1
    if inputs.get("expected_object_count") != expected_object_count:
        raise TraceError("guarded bridge expected-object count does not match chunks plus mapping")
    if inputs.get("discovered_object_count") != expected_object_count:
        raise TraceError("guarded bridge object count does not match chunks plus mapping")
    if stops.get("mode") != "guarded-trace":
        raise TraceError("bridge was not generated in explicit guarded-trace mode")
    if stops.get("stop_primitive") != "recomp_guest_execution_unimplemented_import":
        raise TraceError("guarded bridge uses an unexpected missing-import stop")
    if partition.get("succeeded") is not True or strict_link.get("succeeded") is not True:
        raise TraceError("guarded bridge did not pass partition and strict-link validation")
    if partition.get("final_unresolved_xbox_imports") != 0:
        raise TraceError("guarded bridge retains unresolved Xbox imports")
    if partition.get("guard_reference_validated") is not True:
        raise TraceError("guarded bridge did not validate its execution-guard reference")
    stop_count = stops.get("count")
    if not isinstance(stop_count, int) or isinstance(stop_count, bool) or stop_count < 1:
        raise TraceError("guarded bridge has no bounded unimplemented-import stops")
    implemented_count = partition.get("implemented_expected")
    expected_xbox_count = inputs.get("expected_xbox_import_count")
    validated_xbox_count = inputs.get("validated_xbox_import_count")
    if (
        not isinstance(implemented_count, int)
        or isinstance(implemented_count, bool)
        or implemented_count < 1
        or partition.get("implemented_defined") != implemented_count
        or partition.get("unimplemented_stops_expected") != stop_count
        or partition.get("unimplemented_stops_defined") != stop_count
        or expected_xbox_count != implemented_count + stop_count
        or validated_xbox_count != expected_xbox_count
    ):
        raise TraceError("guarded bridge import partition counts are inconsistent")
    try:
        stop_source = (bridge_dir / "remaining_xbox_import_guarded_stops.cpp").read_text(
            encoding="utf-8"
        )
    except (OSError, UnicodeError) as exc:
        raise TraceError(f"cannot read guarded stop source: {exc}") from exc
    call_count = stop_source.count("recomp_guest_execution_unimplemented_import(\"")
    if call_count != stop_count or "__builtin_trap();" in stop_source:
        raise TraceError("guarded stop source does not match its validated stop count")
    return report


def validate_range(value: GuestRange, alignment: int) -> None:
    if value.base < 0 or value.size <= 0 or value.end > GUEST_SIZE:
        raise TraceError(f"{value.name} range is outside the 32-bit guest address space")
    if value.base % alignment != 0 or value.size % alignment != 0:
        raise TraceError(f"{value.name} range must be aligned to {alignment} bytes")


def validate_non_overlapping_ranges(ranges: Sequence[GuestRange]) -> None:
    for index, left in enumerate(ranges):
        for right in ranges[index + 1 :]:
            if left.base < right.end and right.base < left.end:
                raise TraceError(f"{left.name} and {right.name} ranges overlap")


def validate_ranges(
    image: GuestRange,
    heap: GuestRange,
    stack: GuestRange,
    title: TitleEnvironmentInputs | None = None,
    physical: PhysicalArenaInputs | None = None,
    thread: ThreadEnvironmentInputs | None = None,
    thread_pool: GuestRange | None = None,
    thread_type: GuestRange | None = None,
    xam_module: GuestRange | None = None,
    extra_scratch: Sequence[GuestRange] = (),
) -> None:
    validate_range(image, 1)
    validate_range(heap, PAGE_SIZE)
    validate_range(stack, PAGE_SIZE)
    if heap.base == 0:
        raise TraceError("heap base must be nonzero")
    if stack.end > 0xFFFFFFFF or stack.end % 16 != 0:
        raise TraceError("stack top must be a representable 16-byte-aligned guest address")
    ranges: tuple[GuestRange, ...] = (image, heap, stack)
    if title is not None:
        ranges = (title.full_image, heap, stack)
        ranges += (title.header, title.exported_variable, title.module)
    if physical is not None:
        validate_range(physical.memory, PAGE_SIZE)
        if physical.memory.base == 0:
            raise TraceError("physical arena base must be nonzero")
        if (
            physical.physical_address_offset % PAGE_SIZE != 0
            or physical.physical_address_offset + physical.memory.size > GUEST_SIZE
        ):
            raise TraceError("physical address range is outside the aligned 32-bit domain")
        ranges += (physical.memory,)
    if thread is not None:
        for region in (thread.pcr, thread.thread, thread.tls):
            validate_range(region, 4)
            if region.base == 0:
                raise TraceError(f"{region.name} base must be nonzero")
        ranges += (thread.pcr, thread.thread, thread.tls)
    if thread_pool is not None:
        if title is None or thread is None:
            raise TraceError("thread pool requires title and initial-thread staging")
        validate_range(thread_pool, PAGE_SIZE)
        minimum = 0x6000 + ((thread.tls.size + PAGE_SIZE - 1) // PAGE_SIZE) * PAGE_SIZE
        if (thread_pool.base == 0 or not minimum <= thread_pool.size <= 256 * 1024 * 1024
                or thread_pool.end > 0xFFFFFFFF):
            raise TraceError("thread pool is outside the bounded representable arena")
        ranges += (thread_pool,)
    if thread_type is not None:
        if title is None or thread is None or title.thread_object_type_slot is None:
            raise TraceError("thread type requires initial-thread staging and exactly one type data import")
        validate_range(thread_type, 4)
        if thread_type.base == 0 or thread_type.size != 4:
            raise TraceError("thread type identity must occupy one nonzero aligned word")
        ranges += (thread_type,)
    if xam_module is not None:
        if title is None or thread is None:
            raise TraceError("native XAM module requires title and thread staging")
        validate_range(xam_module, 4)
        if xam_module.base == 0 or xam_module.size != 0x64:
            raise TraceError("native XAM record must occupy a nonzero aligned 0x64-byte range")
        ranges += (xam_module,)
    for region in extra_scratch:
        validate_range(region, 4)
        if not region.base: raise TraceError("data export storage must be nonzero")
    validate_non_overlapping_ranges((*ranges, *extra_scratch))


def _runtime_file_stamp(status):
    return (status.st_dev,status.st_ino,status.st_size,status.st_mtime_ns,status.st_ctime_ns)


def _read_runtime_header(ppc_dir: Path) -> str:
    with (ppc_dir / 'ppc_context.h').open('rb') as source:
        before=os.fstat(source.fileno());data=source.read();after=os.fstat(source.fileno())
    if len(data)!=before.st_size or _runtime_file_stamp(before)!=_runtime_file_stamp(after):
        raise TraceError('private PPC header changed or was not completely read during hook validation')
    return data.decode('utf-8')


def _scan_runtime_hooks(chunk: Path, traps: bool, timebase: bool) -> tuple[int, int]:
    """Stream a chunk once, keeping both checks' pending state file-local."""
    annotation = re.compile(r"^//\s+(?:tw[a-z]*|td[a-z]*|trap)\b")
    expression = re.compile(r"(?:ctx\.)?r[0-9]+\.u64 = uint64_t\(PPC_READ_TIMEBASE\(\)\)(?: >> 32)?;")
    trap_sites = timebase_sites = 0
    pending_trap = pending_timebase = False
    with chunk.open('rb') as source:
        before = os.fstat(source.fileno())
        consumed = 0
        def lines():
            nonlocal consumed
            for block in source:
                consumed += len(block)
                yield from block.decode('utf-8').splitlines()
        # Preserve str.splitlines() handling of non-ASCII line separators from
        # the previous scanners. Count actual bytes: offloaded local files have
        # been observed returning an empty read despite a nonzero stat size.
        for line in lines():
            stripped = line.strip()
            if traps:
                if pending_trap and stripped:
                    if (stripped != "PPC_RUNTIME_TRAP();" and not
                            (stripped.startswith("if (PPCTrapCondition<uint")
                             and stripped.endswith(") PPC_RUNTIME_TRAP();"))):
                        raise TraceError("generated trap annotation lacks its required runtime guard; regenerate with the trap patch")
                    pending_trap = False
                elif not pending_trap and annotation.match(stripped):
                    trap_sites += 1
                    pending_trap = True
            if timebase:
                if "__rdtsc" in stripped:
                    raise TraceError("generated code contains a raw host timebase read; regenerate")
                if not stripped:
                    continue
                if pending_timebase and not expression.fullmatch(stripped):
                    raise TraceError("generated timebase annotation lacks its runtime hook")
                pending_timebase = bool(re.match(r"//\s+mftb(?:l|u)?\b", stripped))
                if "PPC_READ_TIMEBASE" in stripped:
                    if not expression.fullmatch(stripped):
                        raise TraceError("noncanonical generated runtime timebase read")
                    timebase_sites += 1
        after = os.fstat(source.fileno())
        if consumed != before.st_size or _runtime_file_stamp(before) != _runtime_file_stamp(after):
            raise TraceError('private PPC chunk changed or was not completely read during hook validation')
    if pending_trap:
        raise TraceError("generated source ends at an unguarded trap annotation")
    if pending_timebase:
        raise TraceError("generated code ends at an unimplemented timebase read")
    return trap_sites, timebase_sites


def validate_trap_hooks(chunks: Sequence[Path], ppc_dir: Path) -> int:
    """Reject the old generator's silent fixed-point trap no-ops; not an ISA proof."""
    try:
        sites = sum(_scan_runtime_hooks(chunk, True, False)[0] for chunk in chunks)
        if sites and "PPCTrapCondition" not in _read_runtime_header(ppc_dir):
            raise TraceError("generated runtime header lacks trap-condition support")
    except (OSError, UnicodeError) as exc:
        raise TraceError("cannot inspect generated trap instrumentation") from exc
    return sites


def validate_timebase_hooks(chunks: Sequence[Path], ppc_dir: Path) -> int:
    """Reject stale raw-host timebase reads before advertising a fixed rate."""
    try:
        if "#ifndef PPC_READ_TIMEBASE" not in _read_runtime_header(ppc_dir):
            raise TraceError("generated context lacks the runtime timebase hook; regenerate")
        sites = sum(_scan_runtime_hooks(chunk, False, True)[1] for chunk in chunks)
    except (OSError, UnicodeError) as exc:
        raise TraceError("cannot inspect generated timebase hooks") from exc
    return sites


def validate_runtime_hooks(chunks: Sequence[Path], ppc_dir: Path, workers: int = 1) -> tuple[int, int]:
    """One complete fresh scan; bounded read concurrency, no cached acceptance.

    Results are collected in input order. No build/submission is allowed until
    every chunk and the required header pass both checks. Readers stream lines
    rather than retaining hundreds of private source texts in memory.
    """
    from concurrent.futures import ThreadPoolExecutor
    if type(workers) is not int or not 1 <= workers <= 4:
        raise TraceError("runtime hook readers must be between one and four")
    try:
        header = _read_runtime_header(ppc_dir)
        if "#ifndef PPC_READ_TIMEBASE" not in header:
            raise TraceError("generated context lacks the runtime timebase hook; regenerate")
        def scan(chunk):
            return _scan_runtime_hooks(chunk, True, True)
        with ThreadPoolExecutor(max_workers=workers) as pool:
            counts = list(pool.map(scan, chunks))
        trap_sites = sum(count[0] for count in counts)
        timebase_sites = sum(count[1] for count in counts)
        if trap_sites and "PPCTrapCondition" not in header:
            raise TraceError("generated runtime header lacks trap-condition support")
        return trap_sites, timebase_sites
    except (OSError, UnicodeError) as exc:
        raise TraceError("cannot inspect generated runtime instrumentation") from exc


def run_command(command: Sequence[str], timeout_seconds: int) -> CommandOutcome:
    try:
        completed = subprocess.run(
            list(command),
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired:
        return CommandOutcome(None, "", "", "timeout")
    except OSError as exc:
        return CommandOutcome(None, "", str(exc), "launch")
    return CommandOutcome(completed.returncode, completed.stdout, completed.stderr)


def parse_native_scaler_interrupt(raw, scaler):
    if raw is None:
        return None
    if not re.fullmatch(r'(?:0|[1-9][0-9]{0,9})(?:,(?:0|[1-9][0-9]{0,9})){11}',raw):
        raise TraceError('invalid native scaler interrupt evidence')
    fields=list(map(int,raw.split(',')))
    attempted,returned,balanced,worker,held,ack_pairs,pacing_pairs,prefix_attempted,prefix_ready,ack_attempted,acknowledged,rejection=fields
    if (max(fields)>0xFFFFFFFF or any(fields[i] not in (0,1) for i in (0,1,2,5,6,7,8,9,10)) or
            held not in (0,1,2) or rejection>17 or
            (attempted and (not scaler or not scaler['issued_commands'] or not worker or worker&3)) or
            (not attempted and any(fields[:11])) or (returned and not attempted) or
            (balanced and (not returned or held or ack_pairs!=1 or pacing_pairs!=1)) or
            (prefix_ready and not prefix_attempted) or (prefix_attempted and not attempted) or
            (ack_attempted and (not prefix_ready or not balanced)) or
            (acknowledged and (not ack_attempted or rejection))):
        raise TraceError('inconsistent native scaler interrupt evidence')
    return dict(profile='native-cpu2-command-interrupt-v1',scope_attempted=bool(attempted),
        callback_returned=bool(returned),locks_balanced=bool(balanced),worker=worker,held_locks=held,
        ack_lock_pairs=ack_pairs,pacing_lock_pairs=pacing_pairs,prefix_attempted=bool(prefix_attempted),
        prefix_ready=bool(prefix_ready),ack_attempted=bool(ack_attempted),acknowledged=bool(acknowledged),
        rejection=rejection,scaler_executed=False,consumed_ring_words=0,title_screen_verified=False)


def parse_native_scaler_pacing(raw, interrupt):
    if raw is None:
        return None
    if not re.fullmatch(r'(?:0|[1-9][0-9]{0,19})(?:,(?:0|[1-9][0-9]{0,19})){5}',raw):
        raise TraceError('invalid native scaler pacing preflight')
    valid,rate,count,callback,context,frequency=map(int,raw.split(','))
    if (not interrupt or valid not in (0,1) or max(rate,count,callback,context)>0xFFFFFFFF or frequency>0xFFFFFFFFFFFFFFFF or
            (valid and not interrupt['scope_attempted']) or (not valid and any((rate,count,callback,context,frequency)))):
        raise TraceError('inconsistent native scaler pacing preflight')
    return dict(observed=bool(valid),refresh=rate,count=count,optional_callback=callback,
        context=context,frequency=frequency,callback_execution_proven=False)


def parse_native_scaler_completion(raw,interrupt,storage,ring):
    if raw is None:
        return None
    if not re.fullmatch(r'(?:0|[1-9][0-9]{0,9})(?:,(?:0|[1-9][0-9]{0,9})){7}',raw):
        raise TraceError('invalid native scaler completion')
    fields=list(map(int,raw.split(',')))
    completed,previous,read,fences,address0,value0,address1,value1=fields
    if (max(fields)>0xFFFFFFFF or completed not in (0,1) or (not completed and any(fields[1:])) or
            (completed and (not interrupt or not interrupt['acknowledged'] or not storage or not storage['completed_batches'] or
                not ring or previous!=storage['read_word'] or read!=previous+3 or read>=ring['ring_bytes']//4 or fences!=3 or
                (address0|address1)&3 or max(address0,address1)+4>0x20000000 or address0==address1))):
        raise TraceError('inconsistent native scaler completion')
    return dict(profile='native-identity-scanout-v1',configured=bool(completed),previous_word=previous,read_word=read,
        native_fences=fences,event_words=[{'physical':address0,'value':value0},{'physical':address1,'value':value1}],
        consumed_ring_words=3*completed,retail_pending_bit_emulated=False,frames_presented=0,title_screen_verified=False)


def parse_native_post_scaler_storage(raw,completion,storage,ring):
    previous=completion['read_word'] if completion and completion['configured'] else None
    return parse_native_command_storage(raw,previous,storage,ring,'scaler')


def parse_native_post_display_storage(raw,gamma,storage,ring):
    previous=gamma['read_word'] if gamma and gamma['completed_batches']==1 and gamma['display_updates_queued']==1 else None
    return parse_native_command_storage(raw,previous,storage,ring,'display')


def parse_native_post_output_storage(raw,output,storage,ring):
    previous=output['read_word'] if output and output['completed_batches']==1 and output['display_updates_queued']==1 else None
    return parse_native_command_storage(raw,previous,storage,ring,'output')


def parse_native_post_output_color(raw,storage,ring):
    if raw is None:
        return None
    if not re.fullmatch(r'(?:0|[1-9][0-9]{0,9})(?:,(?:0|[1-9][0-9]{0,9})){9}',raw):
        raise TraceError('invalid post-output color evidence')
    fields=list(map(int,raw.split(',')))
    count,previous,read,draws,events,copy,edram,stages,leading,policy=fields
    if (max(fields)>0xFFFFFFFF or count not in (0,1) or (not count and any(fields[1:])) or
            (count and (not storage or not storage['completed_batches'] or not ring or
                previous!=storage['read_word'] or read!=previous+3 or read>=ring['ring_bytes']//4 or
                (draws,events,copy,edram,stages,leading,policy)!=(4,2,3686400,3686400,4,1,1)))):
        raise TraceError('inconsistent post-output color evidence')
    return dict(profile='native-post-output-owned-color-v1',completed_batches=count,previous_word=previous,read_word=read,
        draws=draws,event_words=events,copy_bytes=copy,edram_bytes=edram,stage_fences=stages,leading_fences=leading,
        native_fences=stages+leading,consumed_ring_words=count*3,frames_presented=0,
        original_display_receipts_preserved=bool(count),scanout_transition_completed=False,title_screen_verified=False,
        retail_cache_semantics_verified=False)


def parse_native_post_output_system_context(raw,color,original):
    if raw is None:
        return None
    if not re.fullmatch(r'(?:0|[1-9][0-9]{0,9})(?:,(?:0|[1-9][0-9]{0,9})){5}',raw):
        raise TraceError('invalid post-output system context')
    fields=list(map(int,raw.split(',')))
    count,sp,buffer,identifier,read,policy=fields
    if (max(fields)>0xFFFFFFFF or count not in (0,1) or (not count and any(fields[1:])) or
            (count and (not color or color['completed_batches']!=1 or not original or original['issued_contexts']!=1 or
                sp!=original['stack_pointer'] or not sp or sp&15 or sp+480>0x100000000 or
                buffer!=sp+208 or identifier!=sp+116 or read!=color['read_word'] or policy!=1))):
        raise TraceError('inconsistent post-output system context')
    return dict(profile='native-post-output-no-system-overlay-v1',issued_contexts=count,stack_pointer=sp,buffer=buffer,
        identifier_output=identifier,read_word=read,policy_version=policy,system_commands_scheduled=0,
        gpu_identifier_written=False,frame_presented=False,swap_completed=False,retail_structure_verified=False)


def parse_native_deferred_swap_storage(raw,storage,ring):
    if raw is None:return None
    if not re.fullmatch(r'(?:0|[1-9][0-9]{0,9})(?:,(?:0|[1-9][0-9]{0,9})){5}',raw):
        raise TraceError('invalid deferred-swap storage record')
    fields=list(map(int,raw.split(',')));count,previous,read,base,size,flags=fields
    if (max(fields)>0xFFFFFFFF or count not in (0,1) or not count and any(fields[1:]) or
            count and (not storage or not storage['completed_batches'] or not ring or read!=previous+3 or
                read!=ring['ring_read_word'] or read>=ring['ring_bytes']//4 or base!=storage['physical_base'] or
                size!=storage['byte_count'] or flags!=0x01000000)):
        raise TraceError('inconsistent deferred-swap storage record')
    return dict(profile='native-deferred-swap-command-storage-v1',completed_batches=count,previous_word=previous,read_word=read,
        physical_base=base,byte_count=size,cache_flags=flags,native_fences=count,consumed_ring_words=3*count,
        image_rendering_completed=False,frame_presented=False,guest_status_bits_written=False,retail_cache_semantics_verified=False)


def parse_native_deferred_color_prefix(raw,storage,request,ring):
    if raw is None:return None
    if not re.fullmatch(r'(?:0|[1-9][0-9]{0,9})(?:,(?:0|[1-9][0-9]{0,9})){12}',raw):
        raise TraceError('invalid live deferred color prefix record')
    fields=list(map(int,raw.split(',')))
    attempted,reason,detail,count,previous,pending,words,inputs,copied,fences,leading,repeated,read=fields
    if (max(fields)>0xFFFFFFFF or attempted not in (0,1) or count not in (0,1) or reason>18 or
            repeated!=attempted or not ring or read!=ring['ring_read_word'] or
            not count and (any(fields[4:11]) or attempted and not reason) or
            count and (not attempted or reason or detail or not storage or storage['completed_batches']!=1 or
                not request or not request['issued'] or not request['pending'] or previous!=storage['read_word'] or previous!=read or
                pending!=previous+3 or pending>=ring['ring_bytes']//4 or not 1<=words<=4096-129 or
                not 1<=inputs<=48 or (copied,fences,leading)!=(3686400,4,1))):
        raise TraceError('inconsistent live deferred color prefix record')
    return dict(profile='native-live-deferred-color-prefix-v1',attempted=bool(attempted),rejection=reason,detail=detail,
        completed=bool(count),previous_word=previous,pending_write_word=pending,prefix_words=words,input_ranges=inputs,
        copy_bytes=copied,native_stage_fences=fences,leading_fences=leading,repeat_rejected=bool(repeated),
        unchanged_read_word=read,copy_published=bool(count),prefix_state_committed=bool(count),
        surface_tail_completed=False,frame_presented=False,consumed_ring_words=0,retail_cache_semantics_verified=False)


def parse_stopped_deferred_swap_interrupt(raw,prefix,request):
    if raw is None:return None
    try:fields=json.loads(raw)
    except (ValueError,TypeError) as exc:raise TraceError('invalid deferred interrupt snapshot') from exc
    if (not isinstance(fields,list) or len(fields) not in (2,70) or
            any(type(v) is not int or not 0<=v<=(0xFFFFFFFFFFFFFFFF if i==9 else 0xFFFFFFFF) for i,v in enumerate(fields)) or
            not prefix or fields[0]!=int(len(fields)==70)):
        raise TraceError('inconsistent deferred interrupt snapshot')
    result=dict(profile='stopped-deferred-surface-irq-v1',captured=bool(fields[0]),guest_dispatched=False,
        gpu_executed=False,guest_writes=False,future_lease=False,queue_profile_admitted=False)
    if len(fields)==2:
        if fields[1]>24 or not fields[1] and prefix['completed']:raise TraceError('invalid deferred interrupt rejection')
        result['rejection']=fields[1];return result
    _,read,pending,words,scratch,callback,context,device,handler,frequency=fields[:10]
    if (not prefix['completed'] or not request or not request['pending'] or read!=prefix['previous_word'] or
            pending!=prefix['pending_write_word'] or words!=prefix['prefix_words'] or
            (scratch|callback|device|handler)&3 or not scratch or scratch+32>0x20000000 or
            not callback or not handler or not device or device+23792>0x100000000 or context!=request['image_physical']|0x208):
        raise TraceError('deferred interrupt snapshot lost its current submission')
    result.update(read_word=read,pending_write_word=pending,prefix_words=words,scratch_physical=scratch,
        callback=callback,callback_context=context,device=device,handler=handler,performance_frequency=frequency,
        device_values=fields[10:24],queued_surfaces=fields[24:56],scratch_words=fields[56:64],pacing_record=fields[64:70])
    return result


def parse_native_post_output_wrapped_swap(raw,storage,ring,old_irq,following_storage=None):
    if raw is None:return None
    if not re.fullmatch(r'(?:0|[1-9][0-9]{0,19})(?:,(?:0|[1-9][0-9]{0,19})){32}',raw):
        raise TraceError('invalid post-output wrapped-swap receipt')
    fields=list(map(int,raw.split(',')))
    (count,previous,read,child,image,owner,virtual,size,width,height,fences,idle,acked,before,after,
        worker,attempted,returned,balanced,held,ack_pairs,pacing_pairs,queued,rejection,phase,*events)=fields
    addresses=events[::2];words=events[1::2]
    if (max(fields)>0xFFFFFFFFFFFFFFFF or any(n>0xFFFFFFFF for i,n in enumerate(fields) if i not in (13,14)) or
            count not in (0,1) or (not count and any(fields[1:])) or
            (count and (not storage or not storage['completed_batches'] or not ring or not old_irq or
                not old_irq['acknowledged'] or worker==old_irq['worker'] or not worker or worker&3 or
                [attempted,returned,balanced,held,ack_pairs,pacing_pairs,queued,rejection,phase]!=[1,1,1,0,1,1,1,0,1] or
                read!=previous+3 or read!=(following_storage['previous_word'] if following_storage and following_storage['completed_batches'] else ring['ring_read_word']) or read>=ring['ring_bytes']//4 or
                child&3 or child<storage['physical_base'] or child+516>storage['physical_base']+storage['byte_count'] or
                child+516>0x20000000 or not owner or owner&4095 or image&4095 or not virtual or virtual&4095 or
                size<3768320 or image<owner or image+3768320>owner+size or owner+size>0x20000000 or
                virtual+size>0x100000000 or (width,height)!=(1280,720) or (fences,idle,acked)!=(6,1,1) or
                after<before or after-before>=8589934592 or words[:2]!=[(before//2)&0xFFFFFFFF,(after//2)&0xFFFFFFFF] or
                addresses[1]!=addresses[0]+4 or len(set(addresses))!=4 or
                not {'wb_physical','identifier_virtual','ring_virtual','ring_physical'}<=ring.keys() or
                any(a&3 or a+4>0x20000000 or a<child+516 and child<a+4 or a<owner+size and owner<a+4 or
                    a==ring['wb_physical'] or a+ring['ring_virtual']-ring['ring_physical']==ring['identifier_virtual'] or
                    a<ring['ring_physical']+ring['ring_bytes'] and ring['ring_physical']<a+4 for a in addresses)))):
        raise TraceError('inconsistent post-output wrapped-swap receipt')
    return dict(profile='native-post-output-ordered-swap-v1',completed_batches=count,previous_word=previous,read_word=read,
        child_physical=child,image_physical=image,image_owner_physical=owner,image_owner_virtual=virtual,image_owner_bytes=size,
        width=width,height=height,native_fences=fences,interrupt_idle_fences=idle,interrupt_acknowledged=bool(acked),
        before_completion_ns=before,after_completion_ns=after,worker=worker,locks_balanced=bool(balanced),
        queued_surface_validated=bool(queued),original_irq_receipt_preserved=True,
        events=[dict(physical_address=a,value=w,endian='little' if i<2 else 'big',
            source='native-host-fence-completion-500mhz' if i<2 else 'literal') for i,(a,w) in enumerate(zip(addresses,words))] if count else [],
        guest_event_words_published=count*4,consumed_ring_words=count*3,display_updates_queued=count,
        gpu_event_readback_audited=bool(count),gpu_hardware_timestamps=False,xbox_timing_verified=False,
        vblank_completed=False,title_screen_verified=False)


def parse_stopped_post_swap_request(raw,context,ring,is_swap_stop):
    if raw is None:return None
    try:fields=json.loads(raw)
    except (ValueError,TypeError) as exc:raise TraceError('invalid stopped post-swap request') from exc
    if (not isinstance(fields,list) or any(type(v) is not int or not 0<=v<=0xFFFFFFFF for v in fields) or
            fields!=[0] and (len(fields)!=18 or fields[0]!=1) or not is_swap_stop):
        raise TraceError('inconsistent stopped post-swap request')
    record=dict(captured=fields!=[0],metadata_only=True,pixels_decoded=False,commands_written=False,frame_presented=False,
        next_image_published=False,next_swap_completed=False)
    if fields!=[0]:
        _,command,physical,owner,size,protection,frontbuffer,fmt,color_space,width,height,identifier,*fetch=fields
        if (not context or context['issued_contexts']!=1 or not ring or context['read_word']!=ring['ring_read_word'] or
                (command|physical|owner|size)&3 or size<256 or owner>command or command+256>owner+size or
                owner+size>0x100000000 or physical+256>0x20000000 or
                physical<command-owner or physical-(command-owner)+size>0x20000000 or
                command-physical!=ring['ring_virtual']-ring['ring_physical'] or protection&255!=4):
            raise TraceError('invalid stopped post-swap command ownership')
        record.update(command_virtual=command,command_physical=physical,allocation_base=owner,allocation_bytes=size,protection=protection,
            frontbuffer_virtual=frontbuffer,format=fmt,color_space=color_space,width=width,height=height,identifier_word=identifier,fetch_words=fetch)
    return record


def parse_native_deferred_swap_request(raw,context,ring,completion,first_image=None,storage=None):
    if raw is None:return None
    try:fields=json.loads(raw)
    except (ValueError,TypeError) as exc:raise TraceError('invalid deferred native swap request') from exc
    if (type(fields) is not list or any(type(v) is not int or not 0<=v<=0xFFFFFFFF for v in fields) or
            fields!=[0] and (len(fields)!=25 or fields[:2]!=[1,3])):
        raise TraceError('invalid deferred native swap request fields')
    record=dict(issued=fields!=[0],pending=fields!=[0],ticket=3 if fields!=[0] else 0,
        command_bytes_written=256 if fields!=[0] else 0,pixels_decoded=False,image_rendering_completed=False,
        frame_presented=False,consumed_ring_words=0,vblank_completed=False,title_screen_verified=False)
    if fields==[0]:return record
    # Issuance is historical after its separately validated fresh fence; do not
    # mutate the real current cursor or rewrite the immutable ticket identity.
    request_ring=ring
    if storage and storage['completed_batches']:
        if not ring or storage['read_word']!=ring['ring_read_word']:raise TraceError('deferred storage cursor mismatch')
        request_ring=dict(ring,ring_read_word=storage['previous_word'])
    request=parse_stopped_post_swap_request(json.dumps([1,*fields[2:19]]),context,request_ring,True)
    physical,owner,size,protection,writable,read=fields[19:]
    translation=request['command_virtual']-request['command_physical']
    try:image=physical_capture.post_swap_request_ranges(request)
    except ValueError as exc:raise TraceError(str(exc)) from exc
    if (not completion or completion['completed_batches']!=1 or read!=completion['read_word'] or read!=context['read_word'] or
            request['command_physical']<completion['child_physical']+516 or not image or len(image['owners'])!=2 or
            physical!=image['owners'][1][0] or owner!=request['frontbuffer_virtual'] or owner-physical!=translation or
            (physical|owner)&4095 or size<3768320 or size&4095 or physical+size>0x20000000 or owner+size>0x100000000 or
            writable!=1 or not protection&4 or physical==completion['image_physical'] or physical==first_image or
            physical<image['owners'][0][0]+image['owners'][0][1] and image['owners'][0][0]<physical+size):
        raise TraceError('inconsistent deferred native swap ownership/history')
    record.update(request=request,image_physical=physical,image_owner_virtual=owner,image_owner_bytes=size,
        image_owner_protection=protection,issued_read_word=read,allocations_pinned=True)
    return record


def parse_native_post_output_swap_storage(raw,color,context,storage,ring,completion=None):
    if raw is None:return None
    if not re.fullmatch(r'(?:0|[1-9][0-9]{0,9})(?:,(?:0|[1-9][0-9]{0,9})){5}',raw):
        raise TraceError('invalid post-output swap storage')
    fields=list(map(int,raw.split(',')));count,previous,read,base,size,flags=fields
    if (max(fields)>0xFFFFFFFF or count not in (0,1) or (not count and any(fields[1:])) or
            (count and (not color or color['completed_batches']!=1 or not context or context['issued_contexts']!=1 or
                not storage or not storage['completed_batches'] or not ring or previous!=color['read_word'] or
                previous!=context['read_word'] or read!=previous+3 or
                read!=(completion['previous_word'] if completion and completion['completed_batches'] else ring['ring_read_word']) or
                base!=storage['physical_base'] or size!=storage['byte_count'] or flags!=0x01000000))):
        raise TraceError('inconsistent post-output swap storage')
    return dict(profile='native-post-output-swap-storage-v1',completed_batches=count,previous_word=previous,read_word=read,
        physical_base=base,byte_count=size,cache_flags=flags,native_fences=count,consumed_ring_words=count*3,
        ticket_rewritten=False,frames_presented=0,original_receipts_preserved=True,retail_cache_semantics_verified=False)


def parse_native_post_output_swap(raw,color,context,original,storage,ring,post_storage=None,completion=None,following_storage=None):
    if raw is None:return None
    if not re.fullmatch(r'(?:0|[1-9][0-9]{0,9})(?:,(?:0|[1-9][0-9]{0,9})){6}',raw):
        raise TraceError('invalid post-output swap issuance')
    fields=list(map(int,raw.split(',')))
    issued,pending,queued,command,previous,read,issued_read=fields
    completed_storage=post_storage and post_storage['completed_batches']
    completed=bool(completion and completion['completed_batches'])
    expected=post_storage['read_word'] if completed_storage else (color or {}).get('read_word')
    if (max(fields)>0xFFFFFFFF or issued not in (0,1) or (not issued and any(fields[1:])) or
            ((completed_storage or completed) and not issued) or
            (completed and (not completed_storage or completion['previous_word']!=previous or completion['child_physical']+40!=command)) or
            (issued and (pending!=int(not completed) or queued!=int(completed) or not color or color['completed_batches']!=1 or
                not context or context['issued_contexts']!=1 or not original or not original['present_queued'] or
                not storage or not storage['completed_batches'] or not ring or not command or command&3 or
                command<storage['physical_base'] or command+256>storage['physical_base']+storage['byte_count'] or
                command+256>0x20000000 or previous!=expected or read!=(completion['read_word'] if completed else expected) or issued_read!=color['read_word'] or
                issued_read!=context['read_word'] or read!=(following_storage['previous_word'] if following_storage and following_storage['completed_batches'] else ring['ring_read_word'])))):
        raise TraceError('inconsistent post-output swap issuance')
    return dict(profile='native-post-output-swap-ticket-v1',issued_commands=issued,pending=bool(pending),
        present_queued=bool(queued),command_physical=command,previous_word=previous,read_word=read,issued_read_word=issued_read,
        ticket=2 if issued else 0,command_bytes_written=issued*256,consumed_ring_words=3*int(completed),frames_presented=int(completed),
        original_swap_receipt_preserved=True,gpu_executed=completed,vblank_completed=False,title_screen_verified=False)


def parse_native_command_storage(raw,completion_read,storage,ring,phase):
    if raw is None:
        return None
    if not re.fullmatch(r'(?:0|[1-9][0-9]{0,9})(?:,(?:0|[1-9][0-9]{0,9})){7}',raw):
        raise TraceError(f'invalid post-{phase} storage evidence')
    fields=list(map(int,raw.split(',')))
    count,first,previous,read,base,size,flags,fences=fields
    if (max(fields)>0xFFFFFFFF or count>256 or (not count and any(fields[1:])) or
            (count and (completion_read is None or not storage or not storage['completed_batches'] or
                not ring or first!=completion_read or read!=first+count*3 or previous!=read-3 or
                read>=ring['ring_bytes']//4 or base!=storage['physical_base'] or size!=storage['byte_count'] or
                flags!=0x01000000 or fences!=count))):
        raise TraceError(f'inconsistent post-{phase} storage evidence')
    return dict(profile=f'native-post-{phase}-command-storage-fence-v1',completed_batches=count,first_word=first,
        previous_word=previous,read_word=read,physical_base=base,byte_count=size,cache_flags=flags,native_fences=fences,
        consumed_ring_words=count*3,guest_payload_uploaded=False,guest_status_bits_written=False,
        frames_presented=0,retail_cache_semantics_verified=False)


def parse_native_display_gamma(raw,storage,ring):
    if raw is None:
        return None
    if not re.fullmatch(r'(?:0|[1-9][0-9]{0,9})(?:,(?:0|[1-9][0-9]{0,9})){10}',raw):
        raise TraceError('invalid native display gamma evidence')
    fields=list(map(int,raw.split(',')))
    count,previous,read,child,frontbuffer,fences,queued,address0,value0,address1,value1=fields
    if (max(fields)>0xFFFFFFFF or count not in (0,1) or (not count and any(fields[1:])) or
            (count and (not storage or not storage['completed_batches'] or not ring or
                previous!=storage['read_word'] or read!=previous+3 or read>=ring['ring_bytes']//4 or
                child&3 or child<storage['physical_base'] or child+9356>storage['physical_base']+storage['byte_count'] or
                frontbuffer&3 or frontbuffer+3768320>0x20000000 or fences!=2 or queued!=1 or
                (address0|address1)&3 or max(address0,address1)+4>0x20000000 or address0==address1 or
                any(a<child+9356 and child<a+4 or a<frontbuffer+3768320 and frontbuffer<a+4 for a in (address0,address1))))):
        raise TraceError('inconsistent native display gamma evidence')
    return dict(profile='native-rgb8-display-gamma-v1',completed_batches=count,previous_word=previous,read_word=read,
        child_physical=child,frontbuffer_physical=frontbuffer,native_fences=fences,display_updates_queued=queued,
        lookup_samples=count*256,index_writes=count*256,event_words=[{'physical':address0,'value':value0},{'physical':address1,'value':value1}],
        consumed_ring_words=count*3,generated_game_frames=0,retail_pending_bit_emulated=False,
        retail_output_precision_verified=False,title_screen_verified=False)


def parse_native_display_output(raw,storage,gamma,ring):
    if raw is None:
        return None
    if not re.fullmatch(r'(?:0|[1-9][0-9]{0,9})(?:,(?:0|[1-9][0-9]{0,9})){9}',raw):
        raise TraceError('invalid native display output evidence')
    fields=list(map(int,raw.split(',')))
    count,previous,read,child,fences,queued,address0,value0,address1,value1=fields
    if (max(fields)>0xFFFFFFFF or count not in (0,1) or (not count and any(fields[1:])) or
            (count and (not storage or not storage['completed_batches'] or not gamma or not gamma['completed_batches'] or
                not ring or previous!=storage['read_word'] or read!=previous+3 or read>=ring['ring_bytes']//4 or
                child&3 or child<storage['physical_base'] or child+64>storage['physical_base']+storage['byte_count'] or
                fences!=2 or queued!=1 or (address0|address1)&3 or max(address0,address1)+4>0x20000000 or address0==address1 or
                any(a<storage['physical_base']+storage['byte_count'] and storage['physical_base']<a+4 or
                    a<gamma['frontbuffer_physical']+3768320 and gamma['frontbuffer_physical']<a+4 for a in (address0,address1))))):
        raise TraceError('inconsistent native display output evidence')
    return dict(profile='experimental-native-normal-display-output-v1',completed_batches=count,previous_word=previous,
        read_word=read,child_physical=child,native_fences=fences,display_updates_queued=queued,
        event_words=[{'physical':address0,'value':value0},{'physical':address1,'value':value1}],consumed_ring_words=count*3,
        register_index=0x1E4E,cleared_bits=count,generated_game_frames=0,retail_link_semantics_verified=False,
        force_data_supported=False,physical_vblank_completed=False,title_screen_verified=False)


def parse_trace_protocol(
    output: str,
    label_limit: int,
    budget: int,
    expected_mapping_count: int,
    cooperative: bool | None = None,
    guarded_data_slots: Sequence[int] = (),
    uptime_bundle_base: int | None = None,
) -> TraceProtocol:
    lines = output.splitlines()
    try:
        header_index = lines.index("HOST_GUEST_TRACE_V1")
    except ValueError as exc:
        raise TraceError("trace runner did not emit the protocol header") from exc
    values: dict[str, str] = {}
    labels: list[str] = []
    gpu_packets: list[list[int]] = []
    shader_blobs: list[str] = []
    pm4_blobs: list[str] = []
    physical_blobs: list[str] = []
    native_pending_blobs: list[str] = []
    deferred_shader_blobs: list[str] = []
    for line in lines[header_index + 1 :]:
        key, separator, value = line.partition("=")
        if not separator:
            continue
        if key == "stopped_deferred_shader_blob":
            if len(deferred_shader_blobs) >= 8 or len(value) > 8300:
                raise TraceError('excessive stopped deferred shader capture')
            deferred_shader_blobs.append(value)
        elif key == "stopped_native_pending_blob":
            if len(native_pending_blobs)>=2 or len(value)>16384*2+64:
                raise TraceError('excessive stopped native command capture')
            native_pending_blobs.append(value)
        elif key == "stopped_physical_blob":
            if len(physical_blobs) >= 8 or len(value) > physical_capture.MAX_RECORD_CHARS:
                raise TraceError("excessive stopped physical capture")
            physical_blobs.append(value)
        elif key == "stopped_pm4_blob":
            if len(pm4_blobs) >= 17 or len(value) > pm4_capture.MAX_RECORD_CHARS:
                raise TraceError("excessive stopped PM4 capture")
            pm4_blobs.append(value)
        elif key == "stopped_shader_blob":
            if len(shader_blobs) >= 8 or len(value) > 8300:
                raise TraceError("excessive stopped shader capture")
            shader_blobs.append(value)
        elif key == "gpu_packet":
            if len(gpu_packets) >= 64 or not re.fullmatch(r"(?:0|[1-9][0-9]{0,9})(?:,(?:0|[1-9][0-9]{0,9})){7}", value):
                raise TraceError("invalid or excessive stopped GPU packet metadata")
            gpu_packets.append([int(v) for v in value.split(',')])
        elif key == "function_label":
            if len(labels) >= label_limit or not FUNCTION_LABEL_PATTERN.fullmatch(value):
                raise TraceError("trace runner emitted an invalid or excessive function label")
            labels.append(value)
        elif key in {
            "stop_reason",
            "completed_function_entries",
            "stop_import",
            "stop_exception",
            "debug_message_count",
            "debug_message_bytes",
            "dropped_function_labels",
            "validated_mapping_count",
            "workers_created", "workers_entered", "context_switches",
            "wait_operations",
            "priority_increment", "priority_previous", "priority_initial",
            "priority_current", "priority_suspended", "priority_returned",
            "file_create_access", "file_create_attributes", "file_create_share",
            "file_create_disposition", "file_create_options", "file_create_lookup", "file_create_record",
            "content_mount_rejection",
            "file_opens", "file_reads", "file_bytes", "file_seeks",
            "native_kernel_version",
            "import_stack_pointer", *{f"import_r{i}" for i in range(3, 11)},
            "scalar_address", "scalar_width", "scalar_value", "scalar_write", "scalar_region",
            "invalid_indirect_call",
            "graphics_initialized",
            "experimental_gpu_clock_500mhz_reads",
            "cpu_timebase_frequency", "cpu_timebase_reads",
            "gpu_submission",
            "gpu_indirect_graph",
            "gpu_stopped_word_staging",
            "gpu_stopped_shader_proofs",
            "gpu_stopped_constant_initialization",
            "graphics_ring_physical", "graphics_ring_virtual", "graphics_ring_bytes", "graphics_ring_read_word",
            "graphics_wb_physical", "graphics_wb_virtual", "graphics_wb_exponent",
            "graphics_identifier_virtual",
            "title_termination_registrations",
            "native_display_dimensions",
            "native_initial_coherency",
            "experimental_native_initialization",
            "native_edram_training_policy",
            "native_memory_statistics",
            "xex_image_protections",
            "disconnected_input",
            "experimental_native_audio",
            "native_audio_renderer",
            "native_event_wake_boost",
            "native_category1_gain",
            "critical_section_rejection",
            "native_critical_section_waits",
            "native_high_priority_tiers",
            "thread_delays",
            "experimental_idle_cache_pairs",
            "dispatcher_wait_objects",
            "native_empty_cache_coherency",
            "native_state_batches",
            "native_bin_state",
            "native_ring_restart",
            "native_ring_warm_coherency",
            "native_warm_state_batches",
            "native_warm_empty_cache_coherency",
            "native_owned_color_batch",
            "native_no_system_overlay",
            "native_swap_command",
            "native_display_mode",
            "native_command_storage_coherency",
            "native_wrapped_swap",
            "native_scaler_timing",
            "native_scaler_command",
            "native_scaler_storage_coherency",
            "native_pending_submission",
            "native_scaler_wrapper_plan",
            "native_scaler_interrupt",
            "native_scaler_pacing",
            "native_scaler_completion",
            "native_post_scaler_storage",
            "native_display_gamma",
            "native_post_display_storage",
            "native_post_output_storage",
            "native_post_output_color",
            "native_post_output_system_context",
            "native_post_output_swap",
            "native_post_output_swap_storage",
            "native_post_output_wrapped_swap",
            "native_post_swap_system_context",
            "stopped_post_swap_request",
            "native_deferred_swap_request",
            "native_deferred_swap_storage",
            "native_deferred_color_prefix",
            "stopped_deferred_swap_interrupt",
            "native_display_output",
            "stopped_native_swap_request",
            "stopped_native_swap_image",
            "stopped_native_swap_consumer",
            "stopped_native_swap_continuity",
            "gpu_stopped_work",
            "gpu_stopped_memory",
            "stopped_shader_capture",
            "stopped_deferred_shader_capture",
            "stopped_deferred_color_inspection",
            "stopped_deferred_memory_inspection",
            "stopped_deferred_color_gpu",
            "stopped_deferred_color_timeline",
            "stopped_deferred_color_pending_state",
            "stopped_deferred_color_write_guard",
            "stopped_pm4_capture",
            "stopped_native_pending_capture",
            "stopped_native_pending_ownership",
            "stopped_physical_capture",
            "stopped_physical_requests",
            "stopped_physical_byte_budget",
            "stopped_physical_ownership",
            "stopped_ownership_status",
            "stopped_ownership_requests",
            "stopped_ownership_results",
        }:
            if key in values:
                raise TraceError(f"trace runner emitted duplicate {key}")
            values[key] = value
    required = {
        "stop_reason",
        "completed_function_entries",
        "stop_import",
        "debug_message_count",
        "debug_message_bytes",
        "dropped_function_labels",
        "validated_mapping_count",
    }
    cooperative_fields = {"workers_created", "workers_entered", "context_switches"}
    present = set(values) & cooperative_fields
    if not required <= set(values) or (present and present != cooperative_fields):
        raise TraceError("trace runner protocol is incomplete")
    if cooperative is not None and bool(present) != cooperative:
        raise TraceError("cooperative counters do not match the requested execution mode")
    if "wait_operations" in values and not present:
        raise TraceError("cooperative wait counter requires the cooperative counter group")
    allowed_reasons = {
        "completed",
        "invalid-entry",
        "invalid-budget",
        "nested-run",
        "function-entry-budget-exceeded",
        "unimplemented-import",
        "rejected-import",
        "invalid-indirect-target",
        "host-exception",
        "no-runnable-task",
        "guest-trap",
        "unsupported-scalar-access",
    }
    if values["stop_reason"] not in allowed_reasons:
        raise TraceError("trace runner emitted an unknown stop reason")
    stop_exception = values.get("stop_exception")
    if (stop_exception is not None) != (values["stop_reason"] == "host-exception"):
        raise TraceError("host-exception evidence does not match the stop reason")
    if stop_exception is not None and not re.fullmatch(r"[ -~]{1,192}", stop_exception):
        # The guard bounds and sanitises this; anything else means the protocol
        # was not produced by the guard that reported the stop.
        raise TraceError("trace runner emitted an unsanitised host-exception detail")
    invalid_indirect = None
    if 'invalid_indirect_call' in values:
        raw = values['invalid_indirect_call']
        if values['stop_reason'] != 'invalid-indirect-target' or not re.fullmatch(r'(?:0|[1-9][0-9]{0,9})(?:,(?:0|[1-9][0-9]{0,9})){2}',raw):
            raise TraceError('invalid rejected indirect-call evidence')
        fields = list(map(int,raw.split(',')))
        if max(fields) > 0xFFFFFFFF: raise TraceError('indirect-call address exceeds guest space')
        invalid_indirect = dict(zip(('target','link_register','stack_pointer'),fields))
    try:
        completed_entries = int(values["completed_function_entries"], 10)
        debug_count = int(values["debug_message_count"], 10)
        debug_bytes = int(values["debug_message_bytes"], 10)
        dropped_labels = int(values["dropped_function_labels"], 10)
        mapping_count = int(values["validated_mapping_count"], 10)
        workers_created = int(values.get("workers_created", "0"), 10)
        workers_entered = int(values.get("workers_entered", "0"), 10)
        context_switches = int(values.get("context_switches", "0"), 10)
        wait_operations = int(values.get("wait_operations", "0"), 10)
    except ValueError as exc:
        raise TraceError("trace runner emitted a non-numeric counter") from exc
    if min(completed_entries, debug_count, debug_bytes, dropped_labels, mapping_count) < 0:
        raise TraceError("trace runner emitted a negative counter")
    if completed_entries > budget:
        raise TraceError("trace runner exceeded the requested function-entry budget")
    if not (0 <= workers_entered <= workers_created <= 64
            and 0 <= wait_operations <= budget
            and 0 <= context_switches <= completed_entries + 2 * workers_created + 2 * wait_operations
            and (workers_entered != 0 or context_switches == 0)):
        raise TraceError("cooperative execution counters exceed their bounds")
    if mapping_count != expected_mapping_count:
        raise TraceError("trace runner mapping count does not match validated source")
    stop_import = None if values["stop_import"] == "none" else values["stop_import"]
    if stop_import is not None and not PUBLIC_IMPORT_PATTERN.fullmatch(stop_import):
        raise TraceError("trace runner emitted a non-public stop import")
    if (values["stop_reason"] in {"unimplemented-import", "rejected-import"}) != (stop_import is not None):
        raise TraceError("trace stop reason and public import are inconsistent")
    priority_fields = {"priority_increment", "priority_previous", "priority_initial",
                       "priority_current", "priority_suspended", "priority_returned"}
    priority_request = None
    if priority_fields & values.keys():
        if not priority_fields <= values.keys() or stop_import != "__imp__KeSetBasePriorityThread" \
                or values["stop_reason"] != "rejected-import":
            raise TraceError("priority request requires a complete group at the matching rejected import")
        priority_request = {}
        for key in priority_fields:
            value = values[key]
            if not re.fullmatch(r"-?(?:0|[1-9][0-9]{0,9})", value) or not -(1 << 31) <= int(value) < (1 << 31):
                raise TraceError("priority request is outside signed 32-bit bounds")
            numeric = key in {"priority_increment", "priority_previous"}
            if not numeric and value not in {"0", "1"}:
                raise TraceError("priority request state must be Boolean")
            priority_request[key.removeprefix("priority_")] = int(value) if numeric else bool(int(value))
    create_fields = {"file_create_access", "file_create_attributes", "file_create_share",
                     "file_create_disposition", "file_create_options", "file_create_lookup", "file_create_record"}
    file_create_request = None
    if create_fields & values.keys():
        if not create_fields <= values.keys() or stop_import != "__imp__NtCreateFile" or values["stop_reason"] != "rejected-import":
            raise TraceError("file-create request requires a complete group at its matching rejected import")
        file_create_request = {}
        for key in create_fields:
            value = values[key]
            if not re.fullmatch(r"(?:0|[1-9][0-9]{0,9})", value) or int(value) > 0xFFFFFFFF:
                raise TraceError("file-create metadata is outside unsigned 32-bit bounds")
            file_create_request[key.removeprefix("file_create_")] = int(value)
        if file_create_request["lookup"] > 3 or file_create_request["record"] > 16384 \
                or (file_create_request["lookup"] != 1 and file_create_request["record"] != 0):
            raise TraceError("file-create lookup state is invalid")
    io_fields = {"file_opens", "file_reads", "file_bytes", "file_seeks"}
    file_io = None
    if io_fields & values.keys():
        if not io_fields <= values.keys():
            raise TraceError("file I/O statistics require the complete group")
        if any(not re.fullmatch(r"(?:0|[1-9][0-9]{0,19})", values[k]) for k in io_fields):
            raise TraceError("file I/O statistics must be bounded unsigned integers")
        file_io = {k.removeprefix("file_"): int(values[k]) for k in io_fields}
        if max(file_io[k] for k in ("opens", "reads", "seeks")) > 0xFFFFFFFF \
                or file_io["bytes"] > file_io["reads"] * MAXIMUM_GUEST_FILE_READ_BYTES \
                or (not file_io["opens"] and (file_io["reads"] or file_io["seeks"])):
            raise TraceError("file I/O statistics exceed their bounds")
    content_mount_rejection = None
    if "content_mount_rejection" in values:
        value = values["content_mount_rejection"]
        if stop_import not in {"__imp__XamContentCreateEx", "__imp__XamContentClose"} or values["stop_reason"] != "rejected-import" \
                or not re.fullmatch(r"[1-9][0-9]{0,3}", value) or int(value) > 8191:
            raise TraceError("content mount rejection is not a bounded matching policy mask")
        content_mount_rejection = int(value)
    argument_fields = {"import_stack_pointer", *(f"import_r{i}" for i in range(3, 11))}
    import_arguments = None
    if argument_fields & values.keys():
        if not argument_fields <= values.keys() or stop_import is None:
            raise TraceError("import arguments require a complete group at an import stop")
        if any(not re.fullmatch(r"(?:0|[1-9][0-9]{0,19})", values[k]) or int(values[k]) >= 1 << 64
               for k in argument_fields):
            raise TraceError("import arguments must be unsigned 64-bit values")
        import_arguments = {k.removeprefix("import_"): int(values[k]) for k in sorted(argument_fields)}
    critical_rejection = None
    if 'critical_section_rejection' in values:
        raw=values['critical_section_rejection']
        if (values['stop_reason']!='rejected-import' or stop_import!='__imp__RtlEnterCriticalSection' or
            import_arguments is None or not re.fullmatch(r'(?:0|[1-9][0-9]{0,9})(?:,(?:0|[1-9][0-9]{0,9})){15}',raw)):
            raise TraceError('invalid critical-section rejection snapshot')
        fields=list(map(int,raw.split(',')))
        reason,address,token,registered,owner,recursion,observed,header,signal,forward,backward,lock,live_recursion,live_owner,available,matches=fields
        if (any(v>0xFFFFFFFF for v in fields) or reason not in (1,2,3,4,5,6) or
            address!=(import_arguments['r3']&0xFFFFFFFF) or
            any(v not in (0,1) for v in (registered,observed,available,matches)) or
            (not observed and any(fields[7:])) or (not registered and (owner or recursion or matches)) or
            (observed and (not address or address&3 or address+28>1<<32 or address<0x7FC90000 and address+28>0x7FC80000)) or
            (reason==1 and token!=0) or (reason!=1 and token==0) or
            (reason==2 and registered) or (reason in (3,4,5) and not registered) or
            (reason==3 and matches) or (reason in (4,5) and not matches) or
            (reason==4 and (not owner or token==owner)) or
            (reason==5 and recursion!=0x7FFFFFFF) or
            (matches and (not observed or owner!=live_owner or recursion!=live_recursion or
                           lock!=(recursion-1 if recursion else 0xFFFFFFFF)))):
            raise TraceError('inconsistent critical-section rejection snapshot')
        critical_rejection={'reason':{1:'unbound-thread',2:'not-canonically-initialized',3:'guest-storage-mismatch',
            4:'unsupported-contention',5:'recursion-limit',6:'native-wait-rejected'}[reason],'address':address,'current_thread':token,
            'registered':bool(registered),'registry_owner':owner if registered else None,
            'registry_recursion':recursion if registered else None,'live_words_read':bool(observed),
            'live_header_word':header if observed else None,'live_signal':signal if observed else None,
            'live_forward_link':forward if observed else None,'live_backward_link':backward if observed else None,
            'live_lock_count':lock if observed else None,'live_recursion':live_recursion if observed else None,
            'live_owner':live_owner if observed else None,'unregistered_range_available':bool(available),
            'storage_matches':bool(matches),'guest_memory_modified':False}
    dispatcher_wait_objects = None
    if 'dispatcher_wait_objects' in values:
        raw=values['dispatcher_wait_objects']
        if (len(raw)>4096 or values['stop_reason']!='rejected-import' or
                stop_import!='__imp__KeWaitForMultipleObjects' or import_arguments is None):
            raise TraceError('dispatcher objects require a bounded matching rejected wait')
        try: rows=json.loads(raw)
        except (ValueError,RecursionError) as exc: raise TraceError('invalid dispatcher object metadata') from exc
        count=import_arguments['r3']&0xFFFFFFFF
        array=import_arguments['r4']&0xFFFFFFFF
        if (not 1<=count<=64 or not array or array&3 or array+count*4>1<<32 or
                array<0x7FC90000 and array+count*4>0x7FC80000 or
                not isinstance(rows,list) or len(rows)!=count):
            raise TraceError('dispatcher object array does not match bounded arguments')
        dispatcher_wait_objects=[]
        for row in rows:
            if not isinstance(row,list) or len(row)!=5 or any(type(v) is not int or not 0<=v<=0xFFFFFFFF for v in row):
                raise TraceError('invalid dispatcher object record')
            address,observed,header,signal,valid=row
            if (observed not in (0,1) or valid not in (0,1) or not observed and (header or signal or valid) or
                    observed and (not address or address&3 or address+16>1<<32 or
                                  address<0x7FC90000 and address+16>0x7FC80000) or
                    valid and (header>>24>1 or signal>1)):
                raise TraceError('inconsistent dispatcher object evidence')
            dispatcher_wait_objects.append({'address':address,'owned_header_read':bool(observed),
                'header':header if observed else None,'signal':signal if observed else None,
                'event_validated':bool(valid)})
    scalar_fields = {"scalar_address", "scalar_width", "scalar_value", "scalar_write", "scalar_region"}
    scalar_access = None
    if scalar_fields & values.keys() or values["stop_reason"] == "unsupported-scalar-access":
        if not scalar_fields <= values.keys() or values["stop_reason"] != "unsupported-scalar-access":
            raise TraceError("scalar access requires a complete group at its matching stop")
        if any(not re.fullmatch(r"(?:0|[1-9][0-9]{0,19})", values[k]) or int(values[k]) >= 1 << 64
               for k in scalar_fields):
            raise TraceError("scalar access fields must be bounded unsigned integers")
        scalar_access = {k.removeprefix("scalar_"): int(values[k]) for k in scalar_fields}
        a = scalar_access
        if (a["address"] >= 1 << 32 or a["width"] not in (1, 2, 4, 8) or a["write"] not in (0, 1)
                or a["value"] >= 1 << (a["width"] * 8) or (not a["write"] and a["value"])):
            raise TraceError("scalar access metadata is outside its field bounds")
        end = a["address"] + a["width"]
        expected_region = 2 if end > 1 << 32 else (1 if a["address"] < 0x7FC90000 and end > 0x7FC80000 else 0)
        if not expected_region and a["address"] < 0x7FEB0000 and end > 0x7FEA0000:
            expected_region = 5
        if not expected_region and a["region"] == 3 and any(
                a["address"] < slot + 4 and end > slot for slot in guarded_data_slots):
            expected_region = 3
        if not expected_region and a["region"] == 4 and uptime_bundle_base is not None and (
                a["address"] < uptime_bundle_base + 24 and end > uptime_bundle_base):
            expected_region = 4
        if not expected_region or a["region"] != expected_region:
            raise TraceError("scalar access is not in the reported guarded region")
    graphics_initialized = None
    experimental_clock_reads = None
    termination_count = None
    if "title_termination_registrations" in values:
        value = values["title_termination_registrations"]
        if not re.fullmatch(r"(?:0|[1-9][0-9]?)", value) or int(value) > 64:
            raise TraceError("invalid title termination registration count")
        termination_count = int(value)
    if "graphics_initialized" in values:
        if values["graphics_initialized"] not in ("0", "1"):
            raise TraceError("graphics initialization state must be Boolean")
        graphics_initialized = values["graphics_initialized"] == "1"
    native_display = None
    if "native_display_dimensions" in values:
        dimensions = values["native_display_dimensions"].split(",")
        if graphics_initialized is not True or len(dimensions) != 4 or any(
                not re.fullmatch(r"[1-9][0-9]{0,3}", part) or int(part) > 4096 for part in dimensions):
            raise TraceError("invalid native display configuration dimensions")
        dimensions = [int(part) for part in dimensions]
        if dimensions[2:] != [1280, 720]:
            raise TraceError("native display configuration differs from the explicit software profile")
        native_display = dict(zip(("buffer_width", "buffer_height", "display_width", "display_height"), dimensions))
        native_display.update({"image_format_capability_checked": True, "scaler_executed": False,
            "configured_output_transfer": "srgb", "physical_gamma_measured": False,
            "image_created": False, "system_overlay_callbacks": False, "frame_presented": False})
    ring_keys = {"ring_physical", "ring_virtual", "ring_bytes", "ring_read_word"}
    if "experimental_gpu_clock_500mhz_reads" in values:
        raw = values["experimental_gpu_clock_500mhz_reads"]
        if graphics_initialized is None or not re.fullmatch(r"(?:0|[1-9][0-9]{0,19})", raw) or int(raw) > 0xFFFFFFFFFFFFFFFF:
            raise TraceError("invalid experimental GPU clock evidence")
        experimental_clock_reads = int(raw)
        if experimental_clock_reads and graphics_initialized is not True:
            raise TraceError("experimental GPU clock reads require initialized graphics")
    cpu_timebase = None
    if {"cpu_timebase_frequency", "cpu_timebase_reads"} & values.keys():
        reads = values.get("cpu_timebase_reads", "")
        if (values.get("cpu_timebase_frequency") != "50000000" or
                not re.fullmatch(r"(?:0|[1-9][0-9]{0,19})", reads) or int(reads) > 0xFFFFFFFFFFFFFFFF):
            raise TraceError("invalid CPU timebase evidence")
        cpu_timebase = {"frequency_hz": 50000000, "reads": int(reads),
            "source": "shared-process-monotonic-epoch", "host_raw_counter": False,
            "cycle_accurate": False, "gpu_clock_semantics_validated": False}
    wb_keys = {"wb_physical", "wb_virtual", "wb_exponent"}
    identifier_keys = {"identifier_virtual"}
    gpu_present = {key.removeprefix("graphics_") for key in values if key.startswith(("graphics_ring_", "graphics_wb_")) or key == "graphics_identifier_virtual"}
    graphics_ring = None
    if gpu_present:
        if graphics_initialized is not True or gpu_present not in (ring_keys, ring_keys | wb_keys, ring_keys | wb_keys | identifier_keys):
            raise TraceError("graphics ring configuration is incomplete or has no initialized backend")
        graphics_ring = {}
        for key in gpu_present:
            raw = values["graphics_" + key]
            if not re.fullmatch(r"(?:0|[1-9][0-9]{0,9})", raw) or int(raw) > 0xFFFFFFFF:
                raise TraceError("invalid graphics ring configuration integer")
            graphics_ring[key] = int(raw)
        g = graphics_ring
        size = g["ring_bytes"]
        if not 8 <= size <= 0x1000000 or size & (size - 1) or g["ring_read_word"] >= size // 4:
            raise TraceError("invalid graphics ring extent or read pointer")
        for key in ("ring_physical", "ring_virtual"):
            if g[key] & 7 or g[key] + size > 1 << 32:
                raise TraceError("invalid graphics ring address")
        if wb_keys <= gpu_present:
            if g["wb_exponent"] > 19 or any(not g[k] or g[k] & 3 for k in ("wb_physical", "wb_virtual")):
                raise TraceError("invalid graphics writeback address or exponent")
            if g["wb_physical"] < g["ring_physical"] + size and g["ring_physical"] < g["wb_physical"] + 4:
                raise TraceError("graphics writeback overlaps command ring")
        if identifier_keys <= gpu_present:
            address = g["identifier_virtual"]
            if not address or address & 3 or address == g["wb_virtual"] or (
                    address < g["ring_virtual"] + size and g["ring_virtual"] < address + 4):
                raise TraceError("invalid opaque graphics identifier address")
    submission = parse_stopped_gpu_submission(values.get("gpu_submission"), gpu_packets, scalar_access, graphics_ring)
    indirect_graph = parse_stopped_gpu_indirect_graph(values.get("gpu_indirect_graph"), submission)
    stopped_work = parse_stopped_gpu_work(values.get("gpu_stopped_work"), submission, indirect_graph)
    shader_proofs = parse_stopped_shader_proofs(values.get("gpu_stopped_shader_proofs"), stopped_work)
    stopped_memory = parse_stopped_gpu_memory(values.get("gpu_stopped_memory"), submission, indirect_graph)
    word_staging = parse_stopped_word_staging(values.get("gpu_stopped_word_staging"), stopped_memory)
    constant_initialization = parse_stopped_constant_initialization(values.get("gpu_stopped_constant_initialization"),
        submission, indirect_graph, stopped_work, stopped_memory, word_staging, shader_proofs)
    try:
        captured_shaders = shader_capture.parse_shader_capture(values.get('stopped_shader_capture'), shader_blobs, stopped_work)
    except ValueError as exc:
        raise TraceError("invalid bounded private shader capture") from exc
    try:
        captured_pm4 = pm4_capture.parse_pm4_capture(values.get('stopped_pm4_capture'), pm4_blobs, submission, indirect_graph)
    except ValueError as exc:
        raise TraceError("invalid bounded private PM4 capture") from exc
    initial_coherency = None
    native_init = None
    empty_cache = None
    state_batches = None
    ring_restart = None
    warm_coherency = None
    warm_state = None
    warm_cache = None
    owned_color = None
    def validate_batch_history(start, records):
        nonempty = [item for item in records if item and item['batches']]
        if not nonempty:
            return start
        latest = max(item['read_word'] for item in nonempty)
        total = sum(item['batches'] for item in nonempty)
        if latest != start + 3 * total:
            raise TraceError('batch history requires exact three-word progress')
        for item in nonempty:
            ordinal = (item['read_word'] - start) // 3
            if (item['read_word'] - start) % 3 or not item['batches'] <= ordinal <= total:
                raise TraceError('invalid historical batch cursor')
        if len(nonempty) == 2:
            older, newer = sorted(nonempty, key=lambda item: item['read_word'])
            if older['read_word'] == newer['read_word'] or (latest - older['read_word']) // 3 > newer['batches']:
                raise TraceError('inconsistent interleaved batch history')
        return latest
    if 'native_warm_empty_cache_coherency' in values:
        raw = values['native_warm_empty_cache_coherency']
        if not re.fullmatch(r'[1-9][0-9]{0,9}(?:,(?:0|[1-9][0-9]{0,9})){4}', raw):
            raise TraceError('invalid warm empty-cache evidence')
        count, read, address, size, flags = map(int, raw.split(','))
        if (max(count, read, address, size, flags) > 0xFFFFFFFF or not size or
                (address | size) & 0xFFF or address + size > 0x20000000 or
                flags not in (0x01000000, 0x03000000)):
            raise TraceError('invalid warm empty-cache range')
        warm_cache = {'batches':count, 'read_word':read, 'physical_base':address, 'byte_count':size,
            'cache_flags':flags, 'fence_waited':True, 'resident_guest_resources':0,
            'range_uploaded':False, 'ring_generation':1, 'frame_presented':False}
    if 'native_warm_state_batches' in values:
        raw=values['native_warm_state_batches']
        if not re.fullmatch(r'[1-9][0-9]{0,9}(?:,(?:0|[1-9][0-9]{0,9})){4}',raw):
            raise TraceError('invalid warm state batch evidence')
        count,read,events,writes,invalidations=map(int,raw.split(','))
        if (max(count,read,events,writes,invalidations)>0xFFFFFFFF or
                events>64*count or writes>256*count or invalidations>128*count):
            raise TraceError('inconsistent warm state batch progress')
        warm_state={'batches':count,'read_word':read,'guest_words_published':events,'state_writes':writes,
            'invalidations':invalidations,'ring_generation':1,'fence_waited':True,'gpu_readback_audited':True,
            'draws':0,'frame_presented':False}
    if 'native_ring_warm_coherency' in values:
        raw=values['native_ring_warm_coherency']
        if not re.fullmatch(r'1(?:,(?:0|[1-9][0-9]{0,9})){3}',raw):
            raise TraceError('invalid warm ring coherency evidence')
        _,address,size,flags=map(int,raw.split(','))
        if not size or (address|size)&0xFFF or address+size>0x20000000 or flags not in (0x01000000,0x03000000):
            raise TraceError('invalid warm ring cache range')
        warm_coherency={'ring_generation':1,'consumed_ring_words':22,'physical_base':address,'byte_count':size,
            'cache_flags':flags,'fence_waited':True,'frame_presented':False,'resident_guest_resources':0,
            'experimental_native_me_profile':True,'hardware_microcode_executed':False}
    if 'native_owned_color_batch' in values:
        raw=values['native_owned_color_batch']
        if not cooperative or not re.fullmatch(r'(?:0|[1-9][0-9]{0,9})(?:,(?:0|[1-9][0-9]{0,9})){8}',raw):
            raise TraceError('invalid native owned-color evidence')
        count,previous,read,draws,events,copy_bytes,edram_bytes,fences,policy=map(int,raw.split(','))
        if (max(count,previous,read,draws,events,copy_bytes,edram_bytes,fences,policy)>0xFFFFFFFF or
                count not in (0,1) or (not count and any((previous,read,draws,events,copy_bytes,edram_bytes,fences,policy))) or
                (count and (not warm_coherency or previous<22 or read!=previous+3 or
                    (draws,events,copy_bytes,edram_bytes,fences,policy)!=(4,2,3686400,3686400,4,1)))):
            raise TraceError('inconsistent native owned-color completion')
        owned_color={'completed_batches':count,'previous_word':previous,'read_word':read,'draws':draws,
            'guest_event_words_published':events,'guest_copy_active_bytes':copy_bytes,'retained_edram_known_bytes':edram_bytes,
            'native_fences':fences,'policy_version':policy,'profile':'experimental-conservative-owned-resources-v1',
            'ring_generation':1 if count else None,'gpu_readback_audited':bool(count),
            'hardware_cache_semantics_verified':False,'unknown_status_bits_fabricated':False,'frame_presented':False}
    system_context=None
    if 'native_no_system_overlay' in values:
        raw=values['native_no_system_overlay']
        if not cooperative or not re.fullmatch(r'(?:0|[1-9][0-9]{0,9})(?:,(?:0|[1-9][0-9]{0,9})){5}',raw):
            raise TraceError('invalid native no-system-overlay evidence')
        count,sp,buffer,identifier,read,policy=map(int,raw.split(','))
        if (max(count,sp,buffer,identifier,read,policy)>0xFFFFFFFF or count not in (0,1) or
                (not count and any((sp,buffer,identifier,read,policy))) or
                (count and (not sp or sp&15 or sp+480>0x100000000 or buffer!=sp+208 or identifier!=sp+116 or
                    policy!=1 or not owned_color or owned_color['completed_batches']!=1 or read!=owned_color['read_word']))):
            raise TraceError('inconsistent native no-system-overlay context')
        system_context={'issued_contexts':count,'stack_pointer':sp,'buffer':buffer,'identifier_output':identifier,
            'read_word':read,'policy_version':policy,'profile':'native-no-system-overlay-v1',
            'system_commands_scheduled':0,'gpu_identifier_written':False,'frame_presented':False,
            'retail_structure_verified':False}
    command_coherency=None
    if 'native_command_storage_coherency' in values:
        raw=values['native_command_storage_coherency']
        if not cooperative or not re.fullmatch(r'(?:0|[1-9][0-9]{0,9})(?:,(?:0|[1-9][0-9]{0,9})){5}',raw):
            raise TraceError('invalid native command-storage coherency evidence')
        count,previous,read,physical,size,flags=map(int,raw.split(','))
        if (max(count,previous,read,physical,size,flags)>0xFFFFFFFF or count not in (0,1) or
                (not count and any((previous,read,physical,size,flags))) or
                (count and (not owned_color or owned_color['completed_batches']!=1 or previous!=owned_color['read_word'] or
                    read!=previous+3 or (physical|size)&4095 or not size or physical+size>0x20000000 or flags!=0x01000000))):
            raise TraceError('inconsistent native command-storage coherency')
        command_coherency={'completed_batches':count,'previous_word':previous,'read_word':read,
            'physical_base':physical,'byte_count':size,'cache_flags':flags,'native_fences':count,
            'profile':'native-command-storage-full-fence-v1','consumed_ring_words':3*count,
            'guest_status_bits_written':False,'guest_payload_uploaded':False,'present_queued':False,
            'retail_cache_semantics_verified':False}
    native_swap=None
    if 'native_swap_command' in values:
        raw=values['native_swap_command']
        if not cooperative or not re.fullmatch(r'(?:0|[1-9][0-9]{0,9})(?:,(?:0|[1-9][0-9]{0,9})){5,6}',raw):
            raise TraceError('invalid native swap command evidence')
        fields=list(map(int,raw.split(',')))
        issued,pending,queued,command,previous,read=fields[:6]
        issued_read=fields[6] if len(fields)==7 else previous # Earlier v1 protocol had no intervening batch.
        expected_previous=command_coherency['read_word'] if command_coherency and command_coherency['completed_batches'] else (owned_color or {}).get('read_word')
        if (max(issued,pending,queued,command,previous,read,issued_read)>0xFFFFFFFF or
                issued not in (0,1) or pending not in (0,1) or queued not in (0,1) or
                not system_context or not owned_color or
                any(key.startswith('stopped_native_swap_') for key in values) or
                (not issued and any((pending,queued,command,previous,read,issued_read))) or
                (issued and (pending+queued!=1 or system_context['issued_contexts']!=1 or
                    owned_color['completed_batches']!=1 or not graphics_ring or not command or command&3 or
                    command+256>0x20000000 or issued_read!=owned_color['read_word'] or previous!=expected_previous or read!=previous+3*queued or
                    read>=graphics_ring['ring_bytes']//4))):
            raise TraceError('inconsistent native swap command transaction')
        native_swap={'issued_commands':issued,'pending':bool(pending),'present_queued':bool(queued),
            'command_physical':command,'previous_word':previous,'read_word':read,
            'issued_read_word':issued_read,
            'profile':'experimental-native-swap-command-v1','command_bytes_written':256*issued,
            'consumed_ring_words':3*queued,'gpu_upload_readback_audited':bool(queued),
            'allocation_lock_held_during_callback':bool(queued),'other_guest_writers_parked':bool(queued),
            'gpu_identifier_written':False,'vblank_completed':False,'title_screen_verified':False,
            'retail_packet_semantics_verified':False}
    scaler_timing=None
    if 'native_scaler_timing' in values:
        raw=values['native_scaler_timing']
        if (not cooperative or not native_swap or experimental_clock_reads is None or
                not re.fullmatch(r'1,500,74,74250000,1650,750,60,(?:0|[1-9][0-9]{0,19})',raw) or int(raw.split(',')[-1])>0xFFFFFFFFFFFFFFFF):
            raise TraceError('invalid experimental native scaler timing')
        scaler_timing={'profile':'experimental-native-scaler-bandwidth-720p60-v1','nominal_gpu_mhz':500,
            'mode_word_1c':74,'logical_pixel_clock_hz':74250000,'total_width':1650,'total_height':750,'logical_refresh_hz':60,
            'video_mode_queries':int(raw.split(',')[-1]),'mode_word_20':0,'whole_mhz_truncated':True,
            'field_semantics_hypothesis':True,'physical_display_changed':False,'xbox_timing_verified':False,
            'host_gpu_frequency_measured':False,'vblank_completed':False}
    display_mode=None
    if 'native_display_mode' in values:
        raw=values['native_display_mode']
        if not re.fullmatch(r'(?:0|[1-9][0-9]{0,9})(?:,(?:0|[1-9][0-9]{0,9})){2}',raw):
            raise TraceError('invalid native display-mode evidence')
        selected,flags,queries=map(int,raw.split(','))
        if (not native_swap or selected not in (0,1) or queries not in (0,1) or
                (not selected and (flags or queries)) or
                (selected and (flags!=0x40000000 or not native_swap['issued_commands'] or not native_display or
                    any(native_display[key]!=value for key,value in
                        (('buffer_width',1280),('buffer_height',720),('display_width',1280),('display_height',720)))))):
            raise TraceError('inconsistent native display-mode selection')
        display_mode={'selected':bool(selected),'flags':flags,'information_queries':queries,
            'logical_width':1280 if selected else 0,'logical_height':720 if selected else 0,
            'logical_refresh_hz':60 if selected else 0,'profile':'fixed-native-rgb8-720p60-v1',
            'guest_information_bytes_written':88*queries,'physical_display_changed':False,
            'vblank_completed':False,'scaler_executed':False}
    if command_coherency:
        if not native_swap or (command_coherency['completed_batches'] and
                (not native_swap['issued_commands'] or not display_mode or not display_mode['selected'] or
                    display_mode['information_queries']!=1 or not command_coherency['physical_base']<=native_swap['command_physical'] or
                    native_swap['command_physical']+256>command_coherency['physical_base']+command_coherency['byte_count'])):
            raise TraceError('command-storage coherency requires pending native command and display policy')
    wrapped_swap=None
    if 'native_wrapped_swap' in values:
        raw=values['native_wrapped_swap']
        if not cooperative or not re.fullmatch(r'(?:0|[1-9][0-9]{0,19})(?:,(?:0|[1-9][0-9]{0,19})){14}',raw):
            raise TraceError('invalid native wrapped-swap evidence')
        fields=list(map(int,raw.split(',')))
        count,previous,read,child,fences,before,after,*events=fields
        addresses=events[::2];words=events[1::2]
        if (max(fields)>0xFFFFFFFFFFFFFFFF or any(fields[i]>0xFFFFFFFF for i in (*range(5),*range(7,15))) or
                count not in (0,1) or (not count and any(fields)) or not native_swap or
                (count and (not native_swap['present_queued'] or not command_coherency or not command_coherency['completed_batches'] or
                    experimental_clock_reads is None or 'experimental_idle_cache_pairs' not in values or
                    previous!=native_swap['previous_word'] or read!=native_swap['read_word'] or
                    child+40!=native_swap['command_physical'] or child<command_coherency['physical_base'] or
                    child+360>command_coherency['physical_base']+command_coherency['byte_count'] or fences!=4 or
                    after<before or after-before>=8589934592 or words[:2]!=[(before//2)&0xFFFFFFFF,(after//2)&0xFFFFFFFF] or
                    addresses[1]!=addresses[0]+4 or len(set(addresses))!=4 or
                    any(a&3 or a+4>0x20000000 or a<child+360 and child<a+4 for a in addresses) or
                    not {'wb_physical','identifier_virtual'}<=graphics_ring.keys() or
                    any(a==graphics_ring['wb_physical'] or
                        a+graphics_ring['ring_virtual']-graphics_ring['ring_physical']==graphics_ring['identifier_virtual'] or
                        a<graphics_ring['ring_physical']+graphics_ring['ring_bytes'] and graphics_ring['ring_physical']<a+4 for a in addresses)))):
            raise TraceError('inconsistent native wrapped-swap transaction')
        wrapped_swap={'completed_batches':count,'previous_word':previous,'read_word':read,'child_physical':child,
            'native_fences':fences,'before_completion_ns':before,'after_completion_ns':after,
            'events':[{'physical_address':a,'value':w,'endian':'little' if i<2 else 'big',
                'source':'native-host-fence-completion-500mhz' if i<2 else 'literal'} for i,(a,w) in enumerate(zip(addresses,words))] if count else [],
            'profile':'native-ordered-swap-host-completion-clock-v1','guest_event_words_published':4*count,
            'idle_cache_pairs':count,'post_swap_invalidations':count,'present_queued':bool(count),
            'gpu_event_readback_audited':bool(count),'consumed_ring_words':3*count,
            'gpu_hardware_timestamps':False,'xbox_timing_verified':False,'vblank_completed':False,'title_screen_verified':False}
    scaler_command=None
    if 'native_scaler_command' in values:
        raw=values['native_scaler_command']
        if not scaler_timing or not re.fullmatch(r'(?:0|[1-9][0-9]{0,9})(?:,(?:0|[1-9][0-9]{0,9})){3}',raw):
            raise TraceError('invalid native scaler command evidence')
        fields=list(map(int,raw.split(',')))
        issued,previous,words,issuer=fields
        if (max(fields)>0xFFFFFFFF or issued not in (0,1) or (not issued and any(fields)) or
                (issued and (not wrapped_swap or wrapped_swap['completed_batches']!=1 or
                    previous!=wrapped_swap['read_word'] or not display_mode or
                    not display_mode['selected'] or display_mode['information_queries']!=1 or
                    words!=64 or not issuer or issuer&3))):
            raise TraceError('inconsistent native scaler command transaction')
        scaler_command={'profile':'experimental-native-identity-scaler-command-v1','issued_commands':issued,
            'previous_read_word':previous,'generated_dwords':words,'issuer_thread':issuer,'pending':bool(issued),
            'guest_command_bytes_written':words*4,'consumed_ring_words':0,'gpu_work_completed':False,
            'physical_display_changed':False,'retail_packet_semantics_verified':False,'title_screen_verified':False}
    scaler_storage=None
    if 'native_scaler_storage_coherency' in values:
        raw=values['native_scaler_storage_coherency']
        if not scaler_command or not re.fullmatch(r'(?:0|[1-9][0-9]{0,9})(?:,(?:0|[1-9][0-9]{0,9})){6}',raw):
            raise TraceError('invalid native scaler-storage evidence')
        fields=list(map(int,raw.split(',')))
        completed,previous,read,command,base,size,flags=fields
        if (max(fields)>0xFFFFFFFF or completed not in (0,1) or (not completed and any(fields)) or
                (completed and (not scaler_command['issued_commands'] or previous!=scaler_command['previous_read_word'] or
                    read!=previous+3 or read>=graphics_ring['ring_bytes']//4 or not command_coherency or
                    base!=command_coherency['physical_base'] or size!=command_coherency['byte_count'] or
                    not size or size>8*1024*1024 or (base|size)&4095 or base+size>0x20000000 or
                    command&3 or command<base or command+256>base+size or flags!=0x01000000))):
            raise TraceError('inconsistent native scaler-storage transaction')
        scaler_storage={'profile':'native-scaler-command-storage-fence-v1','completed_batches':completed,
            'previous_word':previous,'read_word':read,'command_physical':command,'physical_base':base,
            'byte_count':size,'cache_flags':flags,'native_fences':completed,'consumed_ring_words':completed*3,
            'command_owner_pinned':bool(completed),'unique_command_snapshot_verified':bool(completed),
            'guest_payload_uploaded':False,'scaler_executed':False,'present_queued':False,'retail_wait_bits_written':False}
    scaler_interrupt=parse_native_scaler_interrupt(values.get('native_scaler_interrupt'),scaler_command)
    pacing=parse_native_scaler_pacing(values.get('native_scaler_pacing'),scaler_interrupt)
    if scaler_interrupt is not None:
        scaler_interrupt['pacing_preflight']=pacing
    scaler_completion=parse_native_scaler_completion(values.get('native_scaler_completion'),scaler_interrupt,scaler_storage,graphics_ring)
    if scaler_completion and scaler_completion['configured']:
        scaler_command['pending']=False
        scaler_command['gpu_work_completed']=True
    post_scaler_storage=parse_native_post_scaler_storage(values.get('native_post_scaler_storage'),scaler_completion,scaler_storage,graphics_ring)
    display_gamma=parse_native_display_gamma(values.get('native_display_gamma'),post_scaler_storage,graphics_ring)
    post_display_storage=parse_native_post_display_storage(values.get('native_post_display_storage'),display_gamma,scaler_storage,graphics_ring)
    display_output=parse_native_display_output(values.get('native_display_output'),post_display_storage,display_gamma,graphics_ring)
    post_output_storage=parse_native_post_output_storage(values.get('native_post_output_storage'),display_output,scaler_storage,graphics_ring)
    post_output_color=parse_native_post_output_color(values.get('native_post_output_color'),post_output_storage,graphics_ring)
    post_output_context=parse_native_post_output_system_context(values.get('native_post_output_system_context'),post_output_color,system_context)
    deferred_storage=parse_native_deferred_swap_storage(values.get('native_deferred_swap_storage'),command_coherency,graphics_ring)
    post_output_wrapped_swap=parse_native_post_output_wrapped_swap(values.get('native_post_output_wrapped_swap'),command_coherency,graphics_ring,scaler_interrupt,deferred_storage)
    post_output_swap_storage=parse_native_post_output_swap_storage(values.get('native_post_output_swap_storage'),post_output_color,
        post_output_context,command_coherency,graphics_ring,post_output_wrapped_swap)
    post_output_swap=parse_native_post_output_swap(values.get('native_post_output_swap'),post_output_color,post_output_context,
        native_swap,command_coherency,graphics_ring,post_output_swap_storage,post_output_wrapped_swap,deferred_storage)
    if ((post_output_swap_storage and post_output_swap_storage['completed_batches']) or
            (post_output_wrapped_swap and post_output_wrapped_swap['completed_batches'])) and not post_output_swap:
        raise TraceError('post-output swap storage requires its issued ticket')
    post_swap_context=parse_native_post_output_system_context(values.get('native_post_swap_system_context'),post_output_wrapped_swap,post_output_context)
    if post_swap_context is not None:
        post_swap_context['profile']='native-post-second-swap-no-system-overlay-v1'
    post_swap_request=parse_stopped_post_swap_request(values.get('stopped_post_swap_request'),post_swap_context,graphics_ring,
        values['stop_reason']=='rejected-import' and stop_import=='__imp__VdSwap')
    deferred_swap=parse_native_deferred_swap_request(values.get('native_deferred_swap_request'),post_swap_context,graphics_ring,
        post_output_wrapped_swap,display_gamma['frontbuffer_physical'] if display_gamma else None,deferred_storage)
    deferred_color_prefix=parse_native_deferred_color_prefix(values.get('native_deferred_color_prefix'),deferred_storage,deferred_swap,graphics_ring)
    deferred_swap_interrupt=parse_stopped_deferred_swap_interrupt(values.get('stopped_deferred_swap_interrupt'),deferred_color_prefix,deferred_swap)
    if deferred_storage and deferred_storage['completed_batches'] and (not deferred_swap or not deferred_swap['issued'] or
            deferred_swap['issued_read_word']!=deferred_storage['previous_word']):
        raise TraceError('deferred command fence requires its still-pending issued request')
    if native_swap and native_swap['issued_commands']:
        latest=scaler_storage['read_word'] if scaler_storage and scaler_storage['completed_batches'] else native_swap['read_word']
        if scaler_completion and scaler_completion['configured']:
            latest=scaler_completion['read_word']
        if post_scaler_storage and post_scaler_storage['completed_batches']:
            latest=post_scaler_storage['read_word']
        if display_gamma and display_gamma['completed_batches']:
            latest=display_gamma['read_word']
        if post_display_storage and post_display_storage['completed_batches']:
            latest=post_display_storage['read_word']
        if display_output and display_output['completed_batches']:
            latest=display_output['read_word']
        if post_output_storage and post_output_storage['completed_batches']:
            latest=post_output_storage['read_word']
        if post_output_color and post_output_color['completed_batches']:
            latest=post_output_color['read_word']
        if post_output_swap_storage and post_output_swap_storage['completed_batches']:
            latest=post_output_swap_storage['read_word']
        if post_output_wrapped_swap and post_output_wrapped_swap['completed_batches']:
            latest=post_output_wrapped_swap['read_word']
        if deferred_storage and deferred_storage['completed_batches']:latest=deferred_storage['read_word']
        if graphics_ring['ring_read_word']!=latest:
            raise TraceError('native command completions disagree with current ring cursor')
    try:
        native_pending=physical_capture.parse_native_pending_submission(values.get('native_pending_submission'),
            submission,indirect_graph,graphics_ring,native_swap,command_coherency,scaler_command,scaler_storage,deferred_swap,deferred_storage)
        scaler_wrapper=physical_capture.parse_scaler_wrapper_plan(values.get('native_scaler_wrapper_plan'),native_pending,scaler_command)
        captured_native_pending=physical_capture.parse_native_pending_capture(values.get('stopped_native_pending_capture'),
            native_pending_blobs,values.get('stopped_native_pending_ownership'),native_pending,indirect_graph,deferred_swap,
            values.get('stopped_pm4_capture') is not None)
        captured_deferred_shaders=deferred_shader_capture.parse_capture(values.get('stopped_deferred_shader_capture'),
            deferred_shader_blobs,captured_native_pending,values.get('stopped_shader_capture') is not None)
        deferred_color_inspection=deferred_shader_capture.parse_color_inspection(values.get('stopped_deferred_color_inspection'),
            captured_deferred_shaders,deferred_swap,graphics_ring,captured_native_pending)
        deferred_memory_inspection=deferred_shader_capture.parse_memory_inspection(values.get('stopped_deferred_memory_inspection'),
            deferred_color_inspection,captured_deferred_shaders)
        deferred_gpu_inspection=deferred_shader_capture.parse_gpu_inspection(values.get('stopped_deferred_color_gpu'),deferred_memory_inspection)
        if deferred_gpu_inspection is not None and deferred_color_prefix is not None:
            raise TraceError('live prefix and stopped GPU diagnostic scopes cannot coexist')
        deferred_gpu_timeline=deferred_shader_capture.parse_gpu_timeline(values.get('stopped_deferred_color_timeline'),deferred_gpu_inspection)
        deferred_pending_state=deferred_shader_capture.parse_pending_state(values.get('stopped_deferred_color_pending_state'),deferred_gpu_timeline)
        deferred_write_guard=deferred_shader_capture.parse_write_guard(values.get('stopped_deferred_color_write_guard'),
            deferred_gpu_inspection,deferred_memory_inspection,deferred_color_inspection,graphics_ring)
        captured_physical = physical_capture.parse_capture(values.get('stopped_physical_capture'),
            values.get('stopped_physical_requests'), physical_blobs, indirect_graph,values.get('stopped_physical_byte_budget'),native_pending,scaler_wrapper,post_swap_request)
        ownership=physical_capture.parse_ownership(values.get('stopped_physical_ownership'),captured_physical)
        if ownership is not None:captured_physical['ownership']=ownership
        ownership_queries=physical_capture.parse_ownership_queries(values.get('stopped_ownership_status'),
            values.get('stopped_ownership_requests'),values.get('stopped_ownership_results'),indirect_graph,post_swap_request)
    except ValueError as exc:
        raise TraceError(str(exc)) from exc
    swap_request=None
    if 'stopped_native_swap_request' in values:
        try: fields=json.loads(values['stopped_native_swap_request'])
        except (ValueError,TypeError) as exc:raise TraceError('invalid stopped native swap metadata') from exc
        if (not isinstance(fields,list) or any(type(v) is not int or not 0<=v<=0xFFFFFFFF for v in fields) or
                fields!=[0] and (len(fields)!=18 or fields[0]!=1) or
                stop_import!='__imp__VdSwap' or values['stop_reason']!='unimplemented-import' or not system_context or
                system_context['issued_contexts']!=1):
            raise TraceError('inconsistent stopped native swap evidence')
        swap_request={'captured':fields!=[0],'frame_presented':False,'commands_written':False,'pixels_decoded':False}
        if fields!=[0]:
            _,command,physical,owner,size,protection,frontbuffer,fmt,color_space,width,height,identifier,*fetch=fields
            offset=graphics_ring['ring_virtual']-graphics_ring['ring_physical'] if graphics_ring else 0
            if (not graphics_ring or (command|physical|owner|size)&3 or size<256 or owner>command or command+256>owner+size or
                    owner+size>0x100000000 or physical+256>0x20000000 or command-physical!=offset or
                    protection&0xFF!=4):
                raise TraceError('invalid stopped swap command ownership metadata')
            swap_request.update(command_virtual=command,command_physical=physical,allocation_base=owner,
                allocation_bytes=size,protection=protection,frontbuffer_virtual=frontbuffer,format=fmt,
                color_space=color_space,width=width,height=height,identifier_word=identifier,fetch_words=fetch)
    swap_image=None;swap_consumer=None
    if 'stopped_native_swap_image' in values and 'stopped_native_swap_consumer' in values:
        raise TraceError('stopped file capture and consumer are mutually exclusive')
    for mode in ('image','consumer'):
        key='stopped_native_swap_'+mode
        if key not in values:continue
        try: fields=json.loads(values[key])
        except (ValueError,TypeError) as exc:raise TraceError('invalid stopped native swap image') from exc
        if (not isinstance(fields,list) or any(type(v) is not int or not 0<=v<=0xFFFFFFFF for v in fields) or
                fields!=[0] and (len(fields)!=(11 if mode=='consumer' else 10) or fields[0]!=1) or not swap_request):
            raise TraceError('inconsistent stopped native swap image evidence')
        record={'captured':fields!=[0],'frame_presented':False,'guest_writes':False,'native_swap_completed':False}
        if mode=='image':record['artifact_name']='native-swap.rgba' if fields!=[0] else None
        else:
            if fields!=[0] and fields[-1] not in (0,1):raise TraceError('invalid stopped consumer response')
            record.update(callback_attempted=fields!=[0],consumer_returned_success=fields!=[0] and fields[-1]==1,
                display_completion_verified=False,borrowed_pixels_synchronous=True,
                published_copy_continuity_verified=False,allocation_lock_held_during_callback=False,future_guest_lease=False)
        if fields!=[0]:
            _,physical,owner,virtual,size,protection,capacity,byte_count,black,opaque=fields[:10]
            frontbuffer=swap_request.get('frontbuffer_virtual',0)
            offset=graphics_ring['ring_virtual']-graphics_ring['ring_physical'] if graphics_ring else 0
            if (not swap_request['captured'] or not graphics_ring or
                    swap_request['fetch_words']!=[0x8A000002,frontbuffer|6,0x0059E4FF,0x1414,0,0x200] or
                    (swap_request['width'],swap_request['height'],swap_request['format'],swap_request['color_space'])!=(1280,720,6,0) or
                    (capacity,byte_count,opaque)!=(3768320,3686400,921600) or black>921600 or
                    not frontbuffer or (frontbuffer|physical|owner|virtual|size)&4095 or
                    not size or owner>physical or physical+capacity>owner+size or owner+size>0x20000000 or
                    virtual+size>0x100000000 or virtual+physical-owner!=frontbuffer or
                    frontbuffer-physical!=offset or protection&0xFF!=4):
                raise TraceError('invalid stopped native swap image layout or ownership')
            record.update(physical_address=physical,allocation_physical=owner,allocation_virtual=virtual,
                allocation_bytes=size,protection=protection,tiled_bytes=capacity,rgba_bytes=byte_count,
                black_pixels=black,opaque_pixels=opaque,width=1280,height=720,pixel_format='RGBA8',
                source_swizzle='zyx1',source_endian='none',pixels_decoded=True)
        if mode=='image':swap_image=record
        else:swap_consumer=record
    if 'stopped_native_swap_continuity' in values:
        try: fields=json.loads(values['stopped_native_swap_continuity'])
        except (ValueError,TypeError) as exc:raise TraceError('invalid swap copy continuity evidence') from exc
        if (not swap_consumer or not isinstance(fields,list) or any(type(v) is not int for v in fields) or
                fields!=[0] and (len(fields)!=4 or fields[0]!=1) or
                (fields!=[0])!=swap_consumer['captured']):
            raise TraceError('inconsistent swap copy continuity evidence')
        if fields!=[0]:
            if not graphics_ring or fields[1:]!=[graphics_ring['ring_read_word'],3768320,3686400]:
                raise TraceError('invalid swap copy continuity cursor or byte count')
            swap_consumer.update(published_copy_continuity_verified=True,allocation_lock_held_during_callback=True)
    if 'native_ring_restart' in values:
        raw=values['native_ring_restart']
        if not re.fullmatch(r'1,[1-9][0-9]{0,9}',raw):
            raise TraceError('invalid native ring restart evidence')
        retired_read=int(raw.split(',')[1])
        warm_read = validate_batch_history(22, (warm_state, warm_cache)) if warm_coherency else 0
        if owned_color and owned_color['completed_batches']:
            if owned_color['previous_word']!=warm_read:
                raise TraceError('owned-color batch must immediately follow warm batch history')
            warm_read=owned_color['read_word']
        if command_coherency and command_coherency['completed_batches']:
            if command_coherency['previous_word']!=warm_read:
                raise TraceError('command-storage coherency must follow owned-color history')
            warm_read=command_coherency['read_word']
        if native_swap and native_swap['present_queued']:
            if native_swap['previous_word']!=warm_read:
                raise TraceError('native swap must immediately follow owned-color history')
            warm_read=native_swap['read_word']
        if scaler_storage and scaler_storage['completed_batches']:
            if scaler_storage['previous_word']!=warm_read:
                raise TraceError('scaler-storage fence must immediately follow native swap history')
            warm_read=scaler_storage['read_word']
        if scaler_completion and scaler_completion['configured']:
            if scaler_completion['previous_word']!=warm_read:
                raise TraceError('scaler completion must immediately follow scaler-storage history')
            warm_read=scaler_completion['read_word']
        if post_scaler_storage and post_scaler_storage['completed_batches']:
            if post_scaler_storage['first_word']!=warm_read:
                raise TraceError('post-scaler storage must immediately follow scaler completion history')
            warm_read=post_scaler_storage['read_word']
        if display_gamma and display_gamma['completed_batches']:
            if display_gamma['previous_word']!=warm_read:
                raise TraceError('gamma must immediately follow post-scaler storage history')
            warm_read=display_gamma['read_word']
        if post_display_storage and post_display_storage['completed_batches']:
            if post_display_storage['first_word']!=warm_read:
                raise TraceError('post-display storage must immediately follow gamma history')
            warm_read=post_display_storage['read_word']
        if display_output and display_output['completed_batches']:
            if display_output['previous_word']!=warm_read:
                raise TraceError('display output must immediately follow post-display storage history')
            warm_read=display_output['read_word']
        if post_output_storage and post_output_storage['completed_batches']:
            if post_output_storage['first_word']!=warm_read:
                raise TraceError('post-output storage must immediately follow display output history')
            warm_read=post_output_storage['read_word']
        if post_output_color and post_output_color['completed_batches']:
            if post_output_color['previous_word']!=warm_read:
                raise TraceError('post-output color must immediately follow post-output storage history')
            warm_read=post_output_color['read_word']
        if post_output_swap_storage and post_output_swap_storage['completed_batches']:
            if post_output_swap_storage['previous_word']!=warm_read:
                raise TraceError('post-output swap storage must follow new color history')
            warm_read=post_output_swap_storage['read_word']
        if post_output_wrapped_swap and post_output_wrapped_swap['completed_batches']:
            if post_output_wrapped_swap['previous_word']!=warm_read:
                raise TraceError('post-output wrapped swap must follow fresh swap storage history')
            warm_read=post_output_wrapped_swap['read_word']
        if deferred_storage and deferred_storage['completed_batches']:
            if deferred_storage['previous_word']!=warm_read:raise TraceError('deferred command storage breaks warm history')
            warm_read=deferred_storage['read_word']
        if not graphics_ring or graphics_ring['ring_read_word']!=warm_read or not 25<retired_read<graphics_ring['ring_bytes']//4:
            raise TraceError('inconsistent native ring restart cursor')
        ring_restart={'generation':1,'retired_read_word':retired_read,'native_queue_drained':True,
            'guest_completion_written':False,'new_generation_commands_executed':bool(warm_coherency)}
        graphics_ring['generation']=1
    if warm_coherency and not ring_restart:
        raise TraceError('warm ring completion requires preceding retirement')
    if owned_color and owned_color['completed_batches'] and not ring_restart:
        raise TraceError('owned-color completion requires a warm ring')
    if (warm_state or warm_cache) and not warm_coherency:
        raise TraceError('warm batch completion requires new-generation initialization')
    completion_read=ring_restart['retired_read_word'] if ring_restart else (graphics_ring['ring_read_word'] if graphics_ring else None)
    if "native_state_batches" in values:
        raw=values['native_state_batches']
        if not re.fullmatch(r'(?:0|[1-9][0-9]{0,9})(?:,(?:0|[1-9][0-9]{0,9})){4}',raw):
            raise TraceError('invalid native state batch evidence')
        batches, state_read, event_words, state_writes, invalidations=map(int,raw.split(','))
        if (max(batches,state_read,event_words,state_writes,invalidations)>0xFFFFFFFF or
                (not batches and any((state_read,event_words,state_writes,invalidations))) or
                event_words>64*batches or state_writes>256*batches or invalidations>128*batches):
            raise TraceError('inconsistent native state batch evidence')
        state_batches={'batches':batches,'read_word':state_read,'guest_words_published':event_words,
            'state_writes':state_writes,'invalidations':invalidations,'fence_waited':bool(batches),
            'gpu_readback_audited':bool(batches),'draws':0,'frame_presented':False,
            'profile':'bounded-native-state-and-literal-events'}
    if "native_empty_cache_coherency" in values:
        raw=values['native_empty_cache_coherency']
        if not re.fullmatch(r'(?:0|[1-9][0-9]{0,9})(?:,(?:0|[1-9][0-9]{0,9})){4}',raw):
            raise TraceError('invalid native empty-cache coherency evidence')
        batches, cache_read, address, size, flags=map(int,raw.split(','))
        if (max(batches,cache_read,address,size,flags)>0xFFFFFFFF or
                (not batches and any((cache_read,address,size,flags))) or
                (batches and (not graphics_ring or graphics_initialized is not True or not native_display or
                    not size or (address|size)&0xFFF or
                    address+size>0x20000000 or flags not in (0x01000000,0x03000000)))):
            raise TraceError('inconsistent native empty-cache coherency state')
        empty_cache={'batches':batches,'read_word':cache_read,'physical_base':address,'byte_count':size,
            'cache_flags':flags,'fence_waited':bool(batches),'resident_guest_resources':0,
            'range_uploaded':False,'profile':'post-initialization-empty-guest-cache-only'}
    latest_post_read=max((item['read_word'] for item in (empty_cache,state_batches) if item),default=0)
    if "experimental_native_initialization" in values:
        raw = values['experimental_native_initialization']
        if not re.fullmatch(r"(?:0|[1-9][0-9]{0,9})(?:,(?:0|[1-9][0-9]{0,9})){4}", raw):
            raise TraceError("invalid experimental native initialization evidence")
        done, read_word, draws, words, registers = map(int, raw.split(','))
        if done not in (0,1) or (not done and any((read_word,draws,words,registers))):
            raise TraceError("inconsistent experimental native initialization state")
        if done and (graphics_initialized is not True or not native_display or not graphics_ring or
                'wb_physical' not in graphics_ring or read_word <= 22 or
                completion_read != (latest_post_read or read_word) or not 1 <= draws <= 64 or
                not 1 <= words <= 64 or not 1 <= registers <= 1024):
            raise TraceError("experimental native initialization requires completed bounded configuration")
        if 'native_initial_coherency' not in values or (done and not values['native_initial_coherency'].startswith('1,')):
            raise TraceError("experimental native initialization requires cold completion evidence")
        native_init = {'completed': bool(done), 'read_word': read_word, 'draws': draws, 'guest_words_published': words,
            'retained_registers': registers, 'fence_waited': bool(done), 'gpu_readback_audited': bool(done),
            'experimental_hardware_contract': True, 'hardware_equivalence_verified': False, 'frame_presented': False}
    post=[item for item in (empty_cache,state_batches) if item is not None]
    if post and not native_init:
        raise TraceError('post-initialization batches require native initialization evidence')
    if latest_post_read:
        if not native_init['completed']:
            raise TraceError('post-initialization batches require completed native initialization')
        validate_batch_history(native_init['read_word'], post)
    elif any(item['batches'] for item in post):
        raise TraceError('completed batch has no read cursor')
    if ring_restart:
        if not native_init or not native_init['completed'] or not state_batches or not state_batches['batches'] or latest_post_read!=completion_read:
            raise TraceError('ring retirement requires completed native state batches')
        for item in (native_init,empty_cache,state_batches):
            if item is not None: item['ring_generation']=0
    native_audio = None
    if 'experimental_native_audio' in values:
        raw = values['experimental_native_audio']
        if not re.fullmatch(r'(?:0|[1-9][0-9]{0,9})(?:,(?:0|[1-9][0-9]{0,9})){4}', raw):
            raise TraceError('invalid native audio stream evidence')
        rate, channels, capacity, burst, queries = map(int, raw.split(','))
        if rate != 48000 or channels != 2 or not 0 < burst <= capacity <= 48000 or queries > 0xFFFFFFFF:
            raise TraceError('unsupported native audio stream configuration')
        native_audio = {'profile': 'experimental-reference-speaker-word-with-aaudio-output',
            'sample_rate': rate, 'channels': channels, 'buffer_capacity_frames': capacity,
            'frames_per_burst': burst, 'speaker_queries': queries, 'reference_speaker_word': 0x10001,
            'stream_open': True, 'stream_started': False, 'submitted_frames': 0,
            'guest_callbacks_implemented': False, 'audible_output_verified': False,
            'xbox_speaker_bitfields_verified': False}
    audio_renderer = None
    if 'native_audio_renderer' in values:
        raw=values['native_audio_renderer']
        if not native_audio or not cooperative or not re.fullmatch(r'(?:0|[1-9][0-9]{0,19})(?:,(?:0|[1-9][0-9]{0,19})){12}',raw):
            raise TraceError('invalid native PCM renderer evidence')
        fields=list(map(int,raw.split(',')))
        worker,entered,completed,accepted,written,read,pending,clipped,start,active,failed,observed,started=fields
        queued=accepted*256-written
        if (any(value>0xFFFFFFFFFFFFFFFF for value in fields) or worker>0xFFFFFFFF or worker%4 or
            start not in (0,1) or active not in (0,1) or failed not in (0,1) or started not in (0,1) or
            entered!=completed+active or not completed<=accepted<=entered or accepted>0xFFFFFFFFFFFFFFFF//256 or
            not 0<=read<=observed<=written<=accepted*256 or pending>8 or clipped>accepted*512 or
            (pending==0 and queued!=0) or (pending>0 and not (pending-1)*256<queued<=pending*256) or
            (not worker and any(fields[1:])) or (start and not written) or
            (written and not start and not failed) or (started and not start)):
            raise TraceError('inconsistent native PCM renderer counters')
        audio_renderer={'profile':'experimental-single-client-native-stereo-no-lfe','worker_token':worker,
            'registered_clients':int(worker!=0),'callbacks_entered':entered,'callbacks_completed':completed,
            'accepted_packets':accepted,'accepted_frames':accepted*256,'native_written_frames':written,
            'last_pacing_service_read_frames':read,'observed_service_read_frames':observed,'pending_packets':pending,
            'clipped_samples':clipped,'start_requested':bool(start),'callback_active':bool(active),'failed':bool(failed),
            'stream_started':bool(started),'synthetic_silence_inserted':False,'audible_output_verified':False,
            'multi_client_mixing':False,'unregister_implemented':False,'console_timing_verified':False}
        native_audio.update(profile='experimental-reference-speaker-word-with-native-pcm-renderer',
            stream_started=bool(started),submitted_frames=accepted*256,guest_callbacks_implemented=True)
    bin_state = None
    if 'native_bin_state' in values:
        raw=values['native_bin_state']
        if (state_batches is None or graphics_initialized is not True or
                not re.fullmatch(r'(?:0|[1-9][0-9]{0,9})(?:,(?:0|[1-9][0-9]{0,9})){4}',raw)):
            raise TraceError('invalid retained native bin state')
        *words,known=map(int,raw.split(','))
        writes=sum(item['state_writes'] for item in (state_batches,warm_state) if item)
        if (max(words)>0xFFFFFFFF or known>15 or sum(bool(known&(1<<i)) for i in range(4))>writes or
                any(word and not known&(1<<i) for i,word in enumerate(words))):
            raise TraceError('inconsistent retained native bin state')
        bin_state={'mask_words':words[:2],'select_words':words[2:],'known_words':known,
            'word_order':'low-high','profile':'retained-command-written-bin-words',
            'unknown_words_are_reset_values':False,'predicates_executed':bool(owned_color and owned_color['completed_batches']),
            'retail_reset_state_verified':False}
    idle_cache_pairs = None
    if 'experimental_idle_cache_pairs' in values:
        raw = values['experimental_idle_cache_pairs']
        if (not cooperative or 'native_state_batches' not in values or
                not re.fullmatch(r'(?:0|[1-9][0-9]{0,19})', raw) or int(raw) > 0xFFFFFFFFFFFFFFFF):
            raise TraceError('invalid experimental idle cache-pair evidence')
        count = int(raw)
        state_count = sum(item['batches'] for item in (state_batches, warm_state) if item)
        state_write_count = sum(item['state_writes'] for item in (state_batches, warm_state) if item)
        wrapper_count=(wrapped_swap or {}).get('completed_batches',0)+(post_output_wrapped_swap or {}).get('completed_batches',0)
        if count < wrapper_count or count-wrapper_count > state_count or (count-wrapper_count) * 3 > state_write_count:
            raise TraceError('experimental idle cache pair exceeds completed state transactions')
        idle_cache_pairs = {'profile': ('unverified-idle-cache-pair-conservative-owned-cpu-residency' if wrapper_count else
                                      'unverified-idle-cache-pair-empty-native-residency'),
            'completed_batches': int(raw), 'exact_six_word_batch_only': not bool(wrapper_count),
            'wrapped_swap_pairs':wrapper_count,
            'resident_guest_resources': None if wrapper_count else 0,
            'persistent_gpu_guest_bindings':0,'cpu_edram_retained':bool(wrapper_count),
            'fresh_native_fence_per_batch': True,
            'guest_payload_uploaded': False, 'hardware_register_semantics_verified': False,
            'register_status_emulated': False, 'frame_presented': False}
    thread_delays = None
    if 'thread_delays' in values:
        raw = values['thread_delays']
        if not cooperative or not re.fullmatch(r'(?:0|[1-9][0-9]{0,19})(?:,(?:0|[1-9][0-9]{0,19})){6}', raw):
            raise TraceError('invalid native thread delay evidence')
        requests, completed, cancelled, zero, total, maximum, pending = map(int, raw.split(','))
        positive = requests - zero
        if (max(requests, completed, cancelled, zero, total, maximum, pending) > 0xFFFFFFFFFFFFFFFF or
                requests != completed + cancelled + pending or pending or zero > requests or maximum > 600000000 or
                total < maximum or total > positive * maximum or total < positive or
                (not positive and maximum) or (positive and not maximum)):
            raise TraceError('inconsistent or unjoined native thread delays')
        thread_delays = {'profile': 'serialized-native-nonalertable-relative-delay', 'requests': requests,
            'completed': completed, 'cancelled': cancelled, 'zero_yields': zero, 'pending': pending,
            'requested_100ns_ticks': total, 'maximum_requested_100ns_ticks': maximum,
            'maximum_supported_seconds': 60, 'clock': 'steady-monotonic', 'zero_relinquishes_slice': True,
            'apc_delivery': False, 'absolute_time_supported': False, 'console_timing_verified': False}
    high_priority_tiers = None
    if 'native_high_priority_tiers' in values:
        raw=values['native_high_priority_tiers']
        if not cooperative or not re.fullmatch(r'(?:0|[1-9][0-9]{0,19})',raw) or int(raw)>0xFFFFFFFFFFFFFFFF:
            raise TraceError('invalid native +3/+4 priority evidence')
        high_priority_tiers={'profile':'native-relative-plus3-plus4-capped-eight-times-slice','accepted_requests':int(raw),
            'increments':[3,4],'slice_weight':8,'maximum_slice_shift':3,'host_os_priority_changed':False,
            'console_priority_class_verified':False,'instruction_preemption':False}
    critical_waits = None
    if 'native_critical_section_waits' in values:
        raw=values['native_critical_section_waits']
        if not cooperative or not re.fullmatch(r'(?:0|[1-9][0-9]{0,19})(?:,(?:0|[1-9][0-9]{0,19})){3}',raw):
            raise TraceError('invalid native critical-section wait evidence')
        registered,acquired,cancelled,pending=map(int,raw.split(','))
        if max(registered,acquired,cancelled,pending)>0xFFFFFFFFFFFFFFFF or registered!=acquired+cancelled+pending or pending:
            raise TraceError('inconsistent or unjoined native critical-section waits')
        critical_waits={'profile':'serialized-native-fifo-recursive-lock','registered_waits':registered,
            'acquired_waits':acquired,'cancelled_waits':cancelled,'pending_waits':pending,
            'guest_wait_lists_modified':False,'lock_count_includes_waiters':False,
            'spin_emulated':False,'priority_boosts':False,'console_scheduling_verified':False}
    category_gain = None
    if 'native_category1_gain' in values:
        raw=values['native_category1_gain']
        if not audio_renderer or not re.fullmatch(r'1065353216,(?:0|[1-9][0-9]{0,19})',raw):
            raise TraceError('invalid native category1 unity-gain evidence')
        queries=int(raw.split(',')[1])
        if queries>0xFFFFFFFFFFFFFFFF:raise TraceError('native category1 query counter overflow')
        category_gain={'profile':'immutable-native-category1-unity','category':1,'gain':1.0,
            'gain_float_bits':0x3F800000,'queries':queries,'title_consumes_gain':True,
            'host_pcm_gain_reapplied':False,'android_system_volume':False,'xbox_settings_verified':False,
            'category_meaning_verified':False,'ducking_implemented':False,'change_mask_implemented':False}
    event_boost = None
    if 'native_event_wake_boost' in values:
        raw=values['native_event_wake_boost']
        if not cooperative or not re.fullmatch(r'(?:0|[1-9][0-9]{0,19})(?:,(?:0|[1-9][0-9]{0,19})){2}',raw):
            raise TraceError('invalid native event wake boost evidence')
        requests,boosted,suppressed=map(int,raw.split(','))
        if max(requests,boosted,suppressed)>0xFFFFFFFFFFFFFFFF or boosted+suppressed>requests*65:
            raise TraceError('inconsistent native event wake boost counters')
        event_boost={'profile':'native-one-boundary-weighted-event-increment1','signal_requests':requests,
            'threads_boosted':boosted,'threads_suppressed':suppressed,'maximum_increment':1,
            'base_priority_changed':False,'host_os_priority_changed':False,'console_scheduler_verified':False,
            'signal_then_wait_irql_implemented':False}
    disconnected_input = None
    if 'disconnected_input' in values:
        if values['disconnected_input'] != '1':
            raise TraceError('invalid disconnected input evidence')
        disconnected_input = {'mode': 'explicit-no-connected-devices', 'connected_devices': 0,
                              'host_controller_input': False, 'synthetic_events': False,
                              'state_packet_writes': False}
    image_protections = None
    if 'xex_image_protections' in values:
        raw = values['xex_image_protections']
        if not re.fullmatch(r'(?:0|[1-9][0-9]{0,9}),(?:0|[1-9][0-9]{0,9})', raw):
            raise TraceError('invalid XEX image page metadata')
        image_base, image_bytes = map(int, raw.split(','))
        if (image_base < 0x80000000 or not image_bytes or image_base + image_bytes > 0x90000000 or
                (image_base | image_bytes) & 0xFFFF):
            raise TraceError('XEX image page metadata requires a bounded 64-KiB title image')
        image_protections = {'base': image_base, 'bytes': image_bytes, 'page_bytes': 65536,
                             'profile': 'snapshotted-xex-security-page-read-write-metadata',
                             'source': 'complete-title-security-page-descriptors',
                             'host_mprotect_enforced': False, 'guest_cache_override': False,
                             'cryptographic_authenticity_verified': False}
    memory_statistics = None
    if "native_memory_statistics" in values:
        raw = values["native_memory_statistics"]
        if not re.fullmatch(r"(?:0|[1-9][0-9]{0,9})(?:,(?:0|[1-9][0-9]{0,9})){6}", raw):
            raise TraceError("invalid native memory statistics")
        total, available, virtual, reserved, physical, committed, highest = map(int, raw.split(','))
        if (not total or total > 0x100000 or available + physical != total or
                highest >= 0x100000 or highest + 1 < total or not virtual or
                virtual > 0xFFFFF000 or (virtual | reserved) & 0xFFF or
                committed * 4096 > reserved or reserved > virtual):
            raise TraceError("inconsistent native arena memory accounting")
        memory_statistics = {
            'profile': 'independent-native-physical-and-virtual-arenas',
            'scope': 'last-successful-guest-query', 'page_bytes': 4096,
            'total_physical_pages': total, 'available_physical_pages': available,
            'virtual_capacity_bytes': virtual, 'reserved_virtual_bytes': reserved,
            'allocated_physical_pages': physical, 'committed_virtual_pages': committed,
            'highest_physical_page': highest, 'console_ram_equivalence_verified': False,
            'host_residency_measured': False,
            'host_image_stack_kernel_cache_charged_to_arenas': False,
        }
    edram_policy = None
    if "native_edram_training_policy" in values:
        raw = values['native_edram_training_policy']
        if not re.fullmatch(r"(?:0|[1-9][0-9]{0,9})(?:,(?:0|[1-9][0-9]{0,9})){1,3}", raw):
            raise TraceError("invalid native EDRAM policy evidence")
        counts = list(map(int, raw.split(',')))
        workers, queries = counts[:2]
        statuses = counts[2] if len(counts) >= 3 else 0 # Older saved worker/query evidence.
        direct = counts[3] if len(counts) == 4 else 0
        if (not native_init or queries > workers+direct or max(counts) > 0xFFFFFFFF or
                (workers and not native_init['completed']) or (statuses and not queries) or direct>256 or (direct and direct>=queries) or
                (direct and (not display_output or not display_output['completed_batches']))):
            raise TraceError("native EDRAM policy requires completed startup and preceding worker checks")
        edram_policy = {'profile': 'driver-managed-no-xenos-link', 'worker_checks': workers,
            'no_guest_command_queries': queries, 'status_queries': statuses, 'direct_no_command_queries': direct, 'xbox_hardware_training_performed': False,
            'edram_rendering_implemented': False}
    if "native_initial_coherency" in values:
        raw = values['native_initial_coherency']
        if graphics_initialized is None or not re.fullmatch(r"(?:0|[1-9][0-9]{0,9})(?:,(?:0|[1-9][0-9]{0,9})){3}", raw):
            raise TraceError("invalid native initial coherency evidence")
        completed, address, size, flags = map(int, raw.split(','))
        if completed not in (0,1) or (not completed and any((address,size,flags))):
            raise TraceError("inconsistent native initial coherency state")
        if completed and (graphics_initialized is not True or not native_display or not graphics_ring or
                completion_read != (latest_post_read if latest_post_read else
                    (native_init['read_word'] if native_init and native_init['completed'] else 22)) or
                'wb_physical' not in graphics_ring or not size or
                (address | size) & 0xFFF or address + size > 0x20000000 or flags not in (0x01000000,0x03000000)):
            raise TraceError("native initial coherency requires completed bounded configuration")
        initial_coherency = {'completed': bool(completed), 'physical_base': address, 'byte_count': size,
            'cache_flags': flags, 'profile': 'one-cold-start-empty-resident-cache', 'fence_waited': bool(completed),
            'consumed_ring_words': 22 if completed else 0, 'resident_guest_resources': 0,
            'range_uploaded': False, 'draw_executed': False, 'general_cache_emulation': False}
    kernel_version = None
    if 'native_kernel_version' in values:
        raw = values['native_kernel_version']
        if not re.fullmatch(r'(?:0|[1-9][0-9]{0,9})(?:,(?:0|[1-9][0-9]{0,9})){2}', raw):
            raise TraceError('invalid native kernel version evidence')
        slot, storage, packed = map(int, raw.split(','))
        if (slot > 0xFFFFFFFC or not slot or not storage or storage > 0xFFFFFFF8 or
                (slot | storage) & 3 or packed >> 28 != 2):
            raise TraceError('invalid native kernel version range/profile')
        kernel_version = {'profile': 'native-declared-minimum-abi', 'slot': slot,
            'storage': storage, 'packed_version': packed,
            'version': [packed >> 28, (packed >> 24) & 15, (packed >> 8) & 0xFFFF, packed & 255],
            'complete_xbox_kernel': False}
    return TraceProtocol(
        stop_reason=values["stop_reason"],
        completed_function_entries=completed_entries,
        stop_import=stop_import,
        stop_exception=stop_exception,
        debug_message_count=debug_count,
        debug_message_bytes=debug_bytes,
        dropped_function_labels=dropped_labels,
        validated_mapping_count=mapping_count,
        function_labels=tuple(labels),
        workers_created=workers_created, workers_entered=workers_entered,
        context_switches=context_switches,
        wait_operations=wait_operations,
        priority_request=priority_request,
        file_create_request=file_create_request,
        file_io=file_io,
        content_mount_rejection=content_mount_rejection,
        import_arguments=import_arguments,
        scalar_access=scalar_access,
        graphics_initialized=graphics_initialized,
        graphics_ring=graphics_ring,
        native_display=native_display,
        experimental_gpu_clock_500mhz_reads=experimental_clock_reads,
        cpu_timebase=cpu_timebase,
        stopped_gpu_submission=submission,
        stopped_gpu_indirect_graph=indirect_graph,
        native_initial_coherency=initial_coherency,
        experimental_native_initialization=native_init,
        invalid_indirect_call=invalid_indirect,
        native_edram_training_policy=edram_policy,
        native_memory_statistics=memory_statistics,
        native_kernel_version=kernel_version,
        xex_image_protections=image_protections,
        disconnected_input=disconnected_input,
        experimental_native_audio=native_audio,
        native_audio_renderer=audio_renderer,
        native_event_wake_boost=event_boost,
        native_category1_gain=category_gain,
        critical_section_rejection=critical_rejection,
        native_critical_section_waits=critical_waits,
        native_high_priority_tiers=high_priority_tiers,
        thread_delays=thread_delays,
        experimental_idle_cache_pairs=idle_cache_pairs,
        dispatcher_wait_objects=dispatcher_wait_objects,
        native_empty_cache_coherency=empty_cache,
        native_state_batches=state_batches,
        native_bin_state=bin_state,
        native_ring_restart=ring_restart,
        native_ring_warm_coherency=warm_coherency,
        native_warm_state_batches=warm_state,
        native_warm_empty_cache_coherency=warm_cache,
        native_owned_color_batch=owned_color,
        native_no_system_overlay=system_context,
        native_swap_command=native_swap,
        native_display_mode=display_mode,
        native_command_storage_coherency=command_coherency,
        native_wrapped_swap=wrapped_swap,
        native_scaler_timing=scaler_timing,
        native_scaler_command=scaler_command,
        native_scaler_storage_coherency=scaler_storage,
        native_pending_submission=native_pending,
        native_scaler_wrapper_plan=scaler_wrapper,
        native_scaler_interrupt=scaler_interrupt,
        native_scaler_completion=scaler_completion,
        native_post_scaler_storage=post_scaler_storage,
        native_display_gamma=display_gamma,
        native_post_display_storage=post_display_storage,
        native_post_output_storage=post_output_storage,
        native_post_output_color=post_output_color,
        native_post_output_system_context=post_output_context,
        native_post_output_swap=post_output_swap,
        native_post_output_swap_storage=post_output_swap_storage,
        native_post_output_wrapped_swap=post_output_wrapped_swap,
        native_post_swap_system_context=post_swap_context,
        stopped_post_swap_request=post_swap_request,
        native_deferred_swap_request=deferred_swap,
        native_deferred_swap_storage=deferred_storage,
        native_deferred_color_prefix=deferred_color_prefix,
        stopped_deferred_swap_interrupt=deferred_swap_interrupt,
        native_display_output=display_output,
        stopped_native_swap_request=swap_request,
        stopped_native_swap_image=swap_image,
        stopped_native_swap_consumer=swap_consumer,
        stopped_gpu_work=stopped_work,
        stopped_gpu_memory=stopped_memory,
        stopped_word_staging=word_staging,
        stopped_shader_proofs=shader_proofs,
        stopped_constant_initialization=constant_initialization,
        private_shader_capture=captured_shaders,
        private_pm4_capture=captured_pm4,
        private_physical_capture=captured_physical,
        private_native_pending_capture=captured_native_pending,
        private_deferred_shader_capture=captured_deferred_shaders,
        stopped_deferred_color_inspection=deferred_color_inspection,
        stopped_deferred_memory_inspection=deferred_memory_inspection,
        stopped_deferred_color_gpu=deferred_gpu_inspection,
        stopped_deferred_color_timeline=deferred_gpu_timeline,
        stopped_deferred_color_pending_state=deferred_pending_state,
        stopped_deferred_color_write_guard=deferred_write_guard,
        stopped_ownership_queries=ownership_queries,
        title_termination_registrations=termination_count,
    )


def parse_stopped_gpu_submission(raw, packets, access, ring):
    if raw is None:
        if packets:
            raise TraceError("stopped GPU packets require submission metadata")
        return None
    if not re.fullmatch(r"(?:0|[1-9][0-9]{0,9})(?:,(?:0|[1-9][0-9]{0,9})){7}", raw):
        raise TraceError("invalid stopped GPU submission metadata")
    status, failure, offset, opcode, read, write, words, count = map(int, raw.split(','))
    if not access or (access['address'], access['width'], access['write'], access['region']) != (0x7FC80714, 4, 1, 1):
        raise TraceError("stopped GPU inspection requires a rejected CP_RB_WPTR store")
    if not 1 <= status <= 5 or words > 0x400000 or count > 65536 or len(packets) != min(count, 64):
        raise TraceError("invalid stopped GPU inspection bounds")
    if status == 1:
        if ring is not None or any((failure, offset, opcode, read, write, words, count)):
            raise TraceError("inconsistent unconfigured GPU inspection")
    else:
        if ring is None:
            raise TraceError("stopped GPU inspection requires ring ownership metadata")
        capacity = ring['ring_bytes'] // 4
        if status == 2:
            if access['value'] < capacity or any((failure, offset, opcode, read, write, words, count)):
                raise TraceError("inconsistent out-of-range write pointer")
        else:
            if read != ring['ring_read_word'] or write != access['value'] or write >= capacity:
                raise TraceError("stopped GPU inspection pointers do not match the rejected store")
            if status == 3 and any((failure, offset, opcode, words, count)):
                raise TraceError("unavailable ring snapshot cannot report decoded packets")
            if status == 4 and (not 1 <= failure <= 12 or offset > ((write-read) & (capacity-1)) or opcode > 127 or words or count):
                raise TraceError("inconsistent rejected ring decode")
            if status == 5:
                if any((failure, offset, opcode)) or words != ((write-read) & (capacity-1)) or not 0 <= count <= words or bool(count) != bool(words):
                    raise TraceError("inconsistent decoded ring extent")
    if status != 5 and (packets or words or count):
        raise TraceError("failed ring inspection cannot publish packets")
    cursor = 0
    for p in packets:
        at, kind, payload, op, first, second, predicated, repeat = p
        if at != cursor or kind > 4 or payload > 16384 or predicated > 1 or repeat > 1:
            raise TraceError("invalid stopped GPU packet framing")
        if (kind in (0, 3) and not payload) or (kind == 1 and payload != 2) or (kind in (2, 4) and payload):
            raise TraceError("invalid stopped GPU packet payload count")
        if op > 127 or (kind != 3 and (op or predicated)) or (kind != 0 and repeat):
            raise TraceError("invalid stopped GPU packet flags")
        if (kind == 0 and (first > 0x7FFF or second)) or (kind == 1 and (first > 0x7FF or second > 0x7FF)) or (kind not in (0, 1) and (first or second)):
            raise TraceError("invalid stopped GPU packet registers")
        cursor += 1 + payload
        if cursor > words:
            raise TraceError("stopped GPU packet exceeds snapshot")
    if count <= 64 and cursor != words:
        raise TraceError("stopped GPU packet metadata does not cover the snapshot")
    return {"status": status, "decode_failure": failure, "error_offset": offset, "error_opcode": opcode,
            "read_word": read, "write_word": write, "word_count": words, "packet_count": count,
            "packet_fields": ["offset", "kind", "payload_words", "opcode", "first_register", "second_register", "predicated", "repeat"],
            "packets": packets, "payloads_suppressed": True, "executed": False}


def parse_stopped_gpu_indirect_graph(raw, submission):
    if raw is None:
        return None
    if not submission or submission['status'] != 5 or len(raw) > 16000:
        raise TraceError("indirect graph requires bounded decoded stopped submission")
    try:
        data = json.loads(raw)
    except (ValueError, RecursionError) as exc:
        raise TraceError("invalid indirect graph metadata") from exc
    def integers(values, count):
        return isinstance(values, list) and len(values) == count and all(type(v) is int and 0 <= v <= 0xFFFFFFFF for v in values)
    if not isinstance(data, list) or len(data) != 9 or not integers(data[:8], 8) or not isinstance(data[8], list):
        raise TraceError("invalid indirect graph fields")
    failure, parent, at, decode, decode_at, opcode, words, count, nodes = data
    if failure > 10 or parent > 64 or at > 1048576 or decode_at > 1048576 or decode > 11 or opcode > 127:
        raise TraceError("invalid indirect graph failure bounds")
    if failure:
        if nodes or words or count or (failure != 7 and any((decode, decode_at, opcode))) or (failure == 7 and not decode):
            raise TraceError("failed indirect graph cannot publish partial data")
    elif any((parent, at, decode, decode_at, opcode)) or len(nodes) > 64 or words > 1048576 or count > 65536:
        raise TraceError("invalid indirect graph success bounds")
    result_nodes = []
    total_words, total_packets, remaining = submission['word_count'], submission['packet_count'], 64
    for index, node in enumerate(nodes, 1):
        if not isinstance(node, list) or len(node) != 7 or not integers(node[:6], 6) or not isinstance(node[6], list):
            raise TraceError("invalid indirect node fields")
        owner, offset, address, depth, nwords, npackets, packets = node
        if owner >= index or not 1 <= depth <= 8 or not 1 <= nwords <= 0xFFFFF or not 1 <= npackets <= nwords or address & 3 or address + nwords * 4 > 0x20000000:
            raise TraceError("invalid indirect node bounds")
        ancestor = result_nodes[owner - 1] if owner else submission
        expected_depth = ancestor['depth'] + 1 if owner else 1
        if depth != expected_depth or offset + 3 > ancestor['word_count']:
            raise TraceError("invalid indirect node parent")
        parent_packets = ancestor['packets']
        found = next((p for p in parent_packets if p[0] == offset), None)
        if found is not None and (found[1], found[2], found[3], found[6]) not in ((3, 2, 0x3F, 0), (3, 2, 0x37, 0)):
            raise TraceError("indirect node parent is not an unpredicated IB")
        if found is None and (len(parent_packets) == ancestor['packet_count'] or (parent_packets and offset < parent_packets[-1][0] + parent_packets[-1][2] + 1)):
            raise TraceError("indirect node parent does not match packet boundaries")
        if len(packets) != min(npackets, remaining) or any(not integers(p, 8) for p in packets):
            raise TraceError("invalid indirect packet retention")
        # Same strict framing as the root; retention is global across children.
        cursor = 0
        for p in packets:
            at_p, kind, payload, op, first, second, predicated, repeat = p
            if at_p != cursor or kind > 4 or payload > 16384 or op > 127 or predicated > 1 or repeat > 1:
                raise TraceError("invalid indirect packet framing")
            if ((kind in (0, 3) and not payload) or (kind == 1 and payload != 2) or (kind in (2, 4) and payload)
                or (kind != 3 and (op or predicated)) or (kind != 0 and repeat)
                or (kind == 0 and (first > 0x7FFF or second)) or (kind == 1 and (first > 0x7FF or second > 0x7FF))
                or (kind not in (0, 1) and (first or second))):
                raise TraceError("invalid indirect packet layout")
            cursor += payload + 1
            if cursor > nwords:
                raise TraceError("indirect packet exceeds buffer")
        if len(packets) == npackets and cursor != nwords:
            raise TraceError("indirect packets do not cover buffer")
        remaining -= len(packets)
        total_words += nwords; total_packets += npackets
        result_nodes.append({'id': index, 'parent': owner, 'parent_offset': offset, 'physical_address': address,
            'depth': depth, 'word_count': nwords, 'packet_count': npackets, 'packets': packets})
    if not failure and (total_words != words or total_packets != count):
        raise TraceError("indirect graph totals do not match buffers")
    return {'failure': failure, 'error_parent': parent, 'error_offset': at,
        'decode_failure': decode, 'decode_offset': decode_at, 'decode_opcode': opcode,
        'total_words': words, 'total_packets': count, 'nodes': result_nodes,
        'payloads_suppressed': True, 'executed': False, 'writers_stopped': True,
        'canonical_physical_addresses_only': True, 'packet_retention_limit': 64}


def parse_stopped_gpu_memory(raw, submission, graph):
    if raw is None: return None
    if not submission or submission['status'] != 5 or not graph or graph['failure'] or len(raw) > 8000:
        raise TraceError('memory request inspection requires a complete bounded graph')
    try: data = json.loads(raw)
    except (ValueError, RecursionError) as exc: raise TraceError('invalid memory request JSON') from exc
    def ints(values, n):
        return isinstance(values, list) and len(values) == n and all(type(v) is int and 0 <= v <= 0xFFFFFFFF for v in values)
    if not isinstance(data, list) or len(data) != 7 or not ints(data[:6], 6) or not isinstance(data[6], list):
        raise TraceError('invalid memory request fields')
    failure, node, offset, upper_mask, other_registers, draws, requests = data
    nodes = [submission, *graph['nodes']]
    if failure > 8 or node >= len(nodes) or offset > nodes[node]['word_count'] or upper_mask & 255 or other_registers > 1048576 or draws > 64 or len(requests) > 64:
        raise TraceError('memory inspection bounds exceeded')
    if (failure and (upper_mask or other_registers or draws or requests)) or (not failure and (node or offset)):
        raise TraceError('inconsistent memory inspection success/failure')
    result, previous_draws = [], 0
    for request in requests:
        if not ints(request, 8) and not ints(request, 9): raise TraceError('invalid explicit memory request')
        owner, at, kind, address, value, preceding, writable, aliases = request[:8]
        big_endian = request[8] if len(request) == 9 else int(kind == 1)
        if big_endian > 1 or (kind == 1 and not big_endian):
            raise TraceError('invalid memory request byte-order profile')
        if owner >= len(nodes) or at >= nodes[owner]['word_count'] or kind > 1 or address & 3 or address + 4 > 0x20000000 or not previous_draws <= preceding <= draws or writable > 1 or aliases > 1:
            raise TraceError('invalid explicit memory request bounds/order')
        previous_draws = preceding
        ancestor = nodes[owner]
        packet = next((p for p in ancestor['packets'] if p[0] == at), None)
        if packet is None:
            if len(ancestor['packets']) == ancestor['packet_count'] or (ancestor['packets'] and at < ancestor['packets'][-1][0] + ancestor['packets'][-1][2] + 1):
                raise TraceError('memory request is not at an inspected packet boundary')
        elif (kind == 1 and (packet[1],packet[2],packet[3],packet[6]) != (3,3,0x58,0)) or (kind == 0 and packet[1] not in (0,1)):
            raise TraceError('memory request differs from retained packet type')
        elif kind == 0:
            registers = (packet[4], packet[5]) if packet[1] == 1 else range(packet[4], packet[4] + (1 if packet[7] else packet[2]))
            if not any(0x578 <= register <= 0x57F for register in registers):
                raise TraceError('scratch request is outside scratch register range')
        result.append({'node': owner, 'offset': at, 'kind': 'scratch' if kind == 0 else 'literal-ps-done',
            'physical_address': address, 'value': value, 'prior_draw_requests': preceding,
            'owned_writable_word': bool(writable), 'aliases_commands_ring_writeback_or_identifier': bool(aliases),
            'bytes': 4, 'byte_order': 'big-endian' if big_endian else 'unverified-scratch-profile',
            'byte_order_validated': bool(big_endian),
            'byte_order_basis': 'literal-event-encoding' if kind else
                ('libxenon-xenos-driver-swap2-profile' if big_endian else 'unverified'),
            'hardware_readback_measured': False, 'written': False})
    return {'failure': failure, 'error_node': node, 'error_offset': offset,
        'unmodeled_scratch_mask_bits': upper_mask, 'other_register_writes': other_registers,
        'draw_requests': draws, 'explicit_writes': result, 'executed': False,
        'complete_hardware_effects_validated': False, 'identifier_alias_checked': True,
        'ownership_is_stopped_snapshot_not_write_authority': True}


def parse_stopped_word_staging(raw, memory):
    if raw is None: return None
    if (not memory or memory['failure'] or memory['unmodeled_scratch_mask_bits'] or
            not 1 <= memory['draw_requests'] <= 64 or
            not re.fullmatch(r'(?:0|[1-9][0-9]{0,2})(?:,(?:0|[1-9][0-9]{0,2})){3}', raw)):
        raise TraceError('invalid stopped native staging evidence')
    before, after, destinations, size = map(int, raw.split(','))
    writes = memory['explicit_writes']
    if (not writes or before + after != len(writes) or
            destinations != len({w['physical_address'] for w in writes}) or size != destinations * 4):
        raise TraceError('native staging counts differ from inspected requests')
    seen, previous = set(), 0
    for w in writes:
        prior = w['prior_draw_requests']
        key = (prior,w['physical_address'])
        if (not w['byte_order_validated'] or prior not in (0,memory['draw_requests']) or
                prior < previous or key in seen):
            raise TraceError('unvalidated, duplicated or intermediate native staging request')
        seen.add(key); previous = prior
    if before != sum(w['prior_draw_requests'] == 0 for w in writes):
        raise TraceError('native staging phases differ from inspected requests')
    return {'planned': True, 'before_graphics_updates': before, 'after_graphics_updates': after,
        'destinations': destinations, 'native_buffer_bytes': size,
        'native_commands_recorded': False, 'guest_memory_written': False,
        'complete_batch_validated': False, 'live_guest_revalidation_required': True}


def parse_stopped_constant_initialization(raw, submission, graph, work, memory, staging, proofs):
    if raw is None: return None
    if not submission or submission['status'] != 5 or not graph or graph['failure'] or len(raw) > 16000:
        raise TraceError('constant initialization inspection requires a bounded complete graph')
    try: data = json.loads(raw)
    except (ValueError, RecursionError) as exc: raise TraceError('invalid constant initialization JSON') from exc
    def ints(values, n):
        return isinstance(values,list) and len(values) == n and all(type(v) is int and 0 <= v <= 0xFFFFFFFF for v in values)
    if not isinstance(data,list) or len(data) != 11 or not ints(data[:9],9) or any(not isinstance(v,list) for v in data[9:]):
        raise TraceError('invalid constant initialization fields')
    failure, node, offset, draws, shaders, invalidations, registers, other_writes, incomplete, partial, records = data
    nodes = [submission, *graph['nodes']]
    if failure > 13 or node >= len(nodes) or offset > nodes[node]['word_count']:
        raise TraceError('invalid constant initialization error location')
    if failure:
        if any(data[3:9]) or partial or records: raise TraceError('failed initialization cannot publish a partial plan')
    else:
        if (node or offset or not work or work['error_kind'] or not memory or memory['failure'] or not staging or
                not proofs or proofs['accepted_count'] != shaders or shaders != len(work['shaders']) or not 1 <= shaders <= 8 or
                not 1 <= draws <= 64 or draws != memory['draw_requests'] or len(records) != draws or
                draws != len(work['draws']) or invalidations > 65536 or not 1 <= registers <= 1024 or
                incomplete > registers or other_writes != memory['other_register_writes']):
            raise TraceError('constant initialization does not match its source subplans')
        if len(partial) != min(incomplete,16) or any(not ints(r,3) or r[0] > 0x7FFF or
                r[2] == 0xFFFFFFFF or r[1] & ~r[2] for r in partial) or partial != sorted(partial) or len({r[0] for r in partial}) != len(partial):
            raise TraceError('invalid partial register knowledge')
        if any(not ints(r,4) or r[2] > 15 or r[3] > 15 for r in records):
            raise TraceError('invalid constant export/draw record')
        if (len({tuple(r[:2]) for r in records}) != draws or
                {tuple(r[:2]) for r in records} != {tuple(d[:2]) for d in work['draws']} or
                any(d[2:7] != [0x36,1,1,2,0] or any(d[7:]) for d in work['draws'])):
            raise TraceError('constant initialization draw sources differ from inspected point requests')
    return {'failure': failure, 'error_node': node, 'error_offset': offset, 'draw_count': draws, 'shader_loads': shaders,
        'invalidations_observed': invalidations, 'retained_register_count': registers,
        'other_register_writes_unimplemented': other_writes, 'incomplete_register_count': incomplete,
        'partial_register_fields': ['index','value_bits','known_mask'], 'partial_registers': partial,
        'partial_register_retention_limit': 16, 'draw_fields': ['node','offset','position_one_mask','parameter_one_mask'],
        'draws': records, 'ordered_shader_and_masked_draw_subplan': not bool(failure),
        'native_probe_linkage_validated': not bool(failure), 'complete_batch_validated': False,
        'hardware_register_side_effects_implemented': False, 'title_raster_state_replayed': False,
        'guest_memory_written': False, 'gpu_executed': False}


def parse_stopped_shader_proofs(raw, work):
    if raw is None: return None
    if not work or work['error_kind'] or len(raw) > 16000:
        raise TraceError('shader proofs require successful bounded stopped-work inspection')
    try: records = json.loads(raw)
    except (ValueError, RecursionError) as exc: raise TraceError('invalid native shader proof records') from exc
    if not isinstance(records, list) or len(records) != len(work['shaders']) or len(records) > 64:
        raise TraceError('native shader proofs must cover every inspected shader')
    for record, shader in zip(records, work['shaders']):
        if not isinstance(record,list) or len(record) != 9 or any(type(v) is not int or not 0 <= v <= 0xFFFFFFFF for v in record):
            raise TraceError('invalid native shader proof fields')
        node, offset, stage, failure, index, controls, executed, exports, inputs = record
        if record[:3] != shader[:3] or failure > 13 or index > 341:
            raise TraceError('native shader proof source/error mismatch')
        if failure:
            if any((controls,executed,exports,inputs)): raise TraceError('failed shader proof cannot publish partial results')
        elif (index or not shader[4] or shader[3]*4 > 4096 or not 1 <= controls <= 128 or
              not 1 <= executed <= 256 or not 1 <= exports <= (9 if stage == 0 else 4) or inputs > 256):
            raise TraceError('invalid successful native shader proof bounds')
    return {'analysis': 'restricted-copy-constant-exports',
        'fields': ['node','offset','stage','failure','error_index','control_count','executed_instruction_count','export_count','initial_component_count'],
        'records': records, 'accepted_count': sum(r[3] == 0 for r in records),
        'gpu_executed': False, 'linkage_validated': False, 'title_raster_state_replayed': False,
        'hardware_float_edge_cases_validated': False, 'raw_programs_suppressed': True}


def parse_stopped_gpu_work(raw, submission, graph):
    if raw is None:
        return None
    if not graph or graph['failure'] or not submission or submission['status'] != 5 or len(raw) > 16000:
        raise TraceError("stopped work requires a bounded complete indirect graph")
    try:
        data = json.loads(raw)
    except (ValueError, RecursionError) as exc:
        raise TraceError("invalid stopped work metadata") from exc
    def integers(values, length):
        return isinstance(values,list) and len(values) == length and all(type(v) is int and 0 <= v <= 0xFFFFFFFF for v in values)
    if not isinstance(data,list) or len(data) != 7 or not integers(data[:5],5) or any(not isinstance(a,list) for a in data[5:]):
        raise TraceError("invalid stopped work fields")
    kind, node, failure, at, opcode, shaders, draws = data
    if kind > 3 or node > len(graph['nodes']) or failure > 13 or at > 1048576 or opcode > 127 or len(shaders) > 64 or len(draws) > 64:
        raise TraceError("invalid stopped work bounds")
    if (kind == 0 and any((node,failure,at,opcode))) or (kind != 0 and (not failure or shaders or draws)):
        raise TraceError("failed stopped work cannot publish partial data")
    def packet_location(record, expected_opcode, minimum_payload):
        owner, offset = record[:2]
        if owner > len(graph['nodes']):
            raise TraceError("stopped work references unknown node")
        ancestor = graph['nodes'][owner-1] if owner else submission
        if offset + 1 + minimum_payload > ancestor['word_count']:
            raise TraceError("stopped work exceeds command buffer")
        packets = ancestor['packets']
        found = next((p for p in packets if p[0] == offset),None)
        if found is not None:
            if found[1] != 3 or found[3] != expected_opcode or found[2] < minimum_payload:
                raise TraceError("stopped work does not match decoded packet")
        elif len(packets) == ancestor['packet_count'] or (packets and offset < packets[-1][0] + packets[-1][2] + 1):
            raise TraceError("stopped work does not match retained packet boundaries")
        return found
    for s in shaders:
        if not integers(s,6): raise TraceError("invalid stopped shader fields")
        owner, offset, stage, count, immediate, physical = s
        if stage > 1 or not count or count > 65535 or count % 3 or immediate > 1 or physical & 3 or physical + count * 4 > 1 << 32 or (immediate and physical):
            raise TraceError("invalid stopped shader layout")
        packet = packet_location(s,0x2B if immediate else 0x27,count+2 if immediate else 2)
        if packet and (packet[6] or packet[2] != (count+2 if immediate else 2)):
            raise TraceError("stopped shader packet extent/predicate mismatch")
    for d in draws:
        if not integers(d,11): raise TraceError("invalid stopped draw fields")
        if d[2] not in (0x22,0x36) or d[3] > 65535 or d[4] > 63 or d[5] > 3 or d[6] > 3 or any(v > 1 for v in d[7:]):
            raise TraceError("invalid stopped draw initiator")
        packet = packet_location(d,d[2],2 if d[2] == 0x22 else 1)
        if (packet and packet[6] != d[10]) or (d[2] == 0x36 and d[9]):
            raise TraceError("stopped draw predicate/query mismatch")
    if len({tuple(s[:2]) for s in shaders}) != len(shaders) or len({tuple(d[:2]) for d in draws}) != len(draws):
        raise TraceError("duplicate stopped work packet")
    return {'error_kind': kind, 'error_node': node, 'failure': failure, 'error_offset': at, 'error_opcode': opcode,
        'shader_fields': ['node','offset','stage','instruction_words','immediate','physical_address'], 'shaders': shaders,
        'draw_fields': ['node','offset','opcode','index_count','primitive','source','major_mode','index32','not_eop','conditional_viz_query','predicated'],
        'draws': draws, 'nonzero_index_requests': sum(d[3] != 0 for d in draws), 'executed': False,
        'index_payload_validated': False, 'predicates_evaluated': False, 'shader_programs_suppressed': True}


def write_report(path: Path, report: dict[str, object]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(temporary, flags, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            os.fchmod(stream.fileno(), 0o600)
            stream.write(json.dumps(report, indent=2, sort_keys=True) + "\n")
        temporary.replace(path)
        path.chmod(0o600)
    except OSError as exc:
        raise TraceError(f"cannot write private trace report: {exc}") from exc


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build and run a guarded private generated-PPC trace on macOS."
    )
    parser.add_argument("generated_ppc_dir", type=Path)
    parser.add_argument("guarded_bridge_dir", type=Path)
    parser.add_argument("decoded_image", type=Path)
    parser.add_argument("--expected-chunk-count", type=int, required=True)
    parser.add_argument("--file-metadata-catalog", type=Path,
                        help="explicit private read-only metadata snapshot; not a file-content mount")
    parser.add_argument("--installed-content-catalog", type=Path,
                        help="explicit private title-content inventory; no mount or license grant")
    parser.add_argument("--installed-package", nargs=2, action="append", default=[], metavar=("PACKAGE_INDEX", "METADATA"),
                        help="bind a package's complete content: metadata snapshot")
    parser.add_argument("--installed-file", nargs=3, action="append", default=[], metavar=("PACKAGE_INDEX", "RECORD_INDEX", "PATH"),
                        help="explicit preopened package file; all regular files must be bound, maximum 32")
    parser.add_argument("--read-only-file", nargs=2, action="append", default=[], metavar=("RECORD_INDEX", "PATH"),
                        help="bind one catalog record to an explicit private regular content file; maximum sixteen")
    parser.add_argument("--heap-base", type=parse_integer, required=True)
    parser.add_argument("--heap-size", type=parse_integer, required=True)
    parser.add_argument("--stack-base", type=parse_integer, required=True)
    parser.add_argument("--stack-size", type=parse_integer, required=True)
    parser.add_argument("--physical-base", type=parse_integer)
    parser.add_argument("--physical-size", type=parse_integer)
    parser.add_argument("--physical-address-offset", type=parse_integer)
    parser.add_argument("--thread-pcr-base", type=parse_integer)
    parser.add_argument("--thread-record-base", type=parse_integer)
    parser.add_argument("--thread-tls-base", type=parse_integer)
    parser.add_argument("--thread-pool-base", type=parse_integer)
    parser.add_argument("--thread-pool-size", type=parse_integer)
    parser.add_argument("--thread-type-base", type=parse_integer)
    parser.add_argument("--xam-module-base", type=parse_integer)
    parser.add_argument("--virtual-video-mode", choices=["720p60"],
                        help="explicit software display configuration, not physical-monitor or rendering support")
    parser.add_argument("--graphics-backend", choices=["vulkan-headless"],
                        help="Android-only native device/queue initialization; no frame presentation yet")
    parser.add_argument("--experimental-gpu-clock-500mhz", action="store_true",
                        help="ISOLATED HYPOTHESIS: read 044B as a 500-MHz timer; NOT verified hardware semantics")
    parser.add_argument("--native-initial-coherency", action="store_true",
                        help="one exact cold-start cache invalidation with native fenced completion; no resident guest resources or draws")
    parser.add_argument("--native-empty-cache-coherency", action="store_true",
                        help="enable exact post-initialization cache batches only while the native guest-resource cache is empty")
    parser.add_argument("--native-state-batches", action="store_true",
                        help="enable bounded no-draw state batches and audited native literal-event completion")
    parser.add_argument("--experimental-native-initialization", action="store_true",
                        help="EXPERIMENTAL legacy-setup contract: real constant masked draws and audited guest completion; hardware equivalence remains unverified")
    parser.add_argument("--capture-stopped-shaders", action="store_true",
                        help="opt-in capture of at most eight inline shader programs / 4 KiB into ignored local-analysis; never uploads")
    parser.add_argument("--capture-stopped-pm4", action="store_true",
                        help="opt-in stopped command capture (17 PM4 buffers or separately labeled owned deferred root/child, 16 KiB); ignored private files, no execution")
    parser.add_argument("--hardware-info-base", type=parse_integer,
                        help="explicit 16-byte virtual six-CPU/storage hardware-info profile")
    parser.add_argument("--native-kernel-version-base", type=parse_integer,
                        help="eight-byte native kernel compatibility version using the title's declared minimum")
    parser.add_argument("--video-globals-base", type=parse_integer,
                        help="explicit eight-byte storage for two graphics pointer variables")
    parser.add_argument("--uptime-bundle-base", type=parse_integer,
                        help="explicit 24-byte timestamp data export; only live uptime word is supported")
    parser.add_argument("--command-line-base", type=parse_integer,
                        help="explicit 64-byte launch data containing quoted default.xex with no arguments")
    parser.add_argument("--cooperative-threads", action="store_true")
    parser.add_argument("--xex-image-protections", action="store_true",
                        help="snapshot read/write/cache metadata from the complete title XEX page table")
    parser.add_argument("--experimental-native-audio", action="store_true",
                        help="Android-only open AAudio stream and experimental reference speaker query; no playback")
    parser.add_argument("--experimental-native-audio-renderer", action="store_true",
                        help="explicit single-client callbacks and native stereo PCM; requires native audio and cooperative threads")
    parser.add_argument("--native-event-wake-boost", action="store_true",
                        help="explicit native increment1 event wake slice boost; no console scheduler/IRQL equivalence")
    parser.add_argument("--native-critical-section-waits", action="store_true",
                        help="Opt into cooperative native FIFO recursive critical-section waits")
    parser.add_argument("--native-high-priority-tiers", action="store_true",
                        help="Opt into native relative +3/+4 priorities, both capped at eight-times slices")
    parser.add_argument("--experimental-idle-cache-pair", action="store_true",
                        help="unverified exact idle/0E00/0E40 empty-cache hypothesis; requires native state and empty-cache profiles")
    parser.add_argument("--native-category1-unity-gain", action="store_true",
                        help="immutable native category1 gain of one; no Android system-volume or Xbox setting claim")
    parser.add_argument("--disconnected-input", action="store_true",
                        help="explicit headless input configuration: zero guest controllers, no state packets or synthetic events")
    parser.add_argument("--no-debug-monitor-base", type=parse_integer,
                        help="explicit four-byte null debug-monitor pointer variable; no Xbox remote debugger attached")
    parser.add_argument("--no-cert-monitor-base", type=parse_integer,
                        help="explicit four-byte null certification-monitor pointer variable; no Xbox certification monitor attached")
    parser.add_argument("--hsio-lock-base", type=parse_integer,
                        help="explicit 28-byte native calibration critical-section DATA object; not HSIO/EDRAM training")
    parser.add_argument("--thread-quantum", type=int, default=32)
    parser.add_argument(
        "--title-xex",
        type=Path,
        help="enable title-environment staging from this explicit absolute raw XEX",
    )
    parser.add_argument("--title-header-base", type=parse_integer)
    parser.add_argument("--title-export-address", type=parse_integer)
    parser.add_argument("--title-module-base", type=parse_integer)
    parser.add_argument("--function-entry-budget", type=int, default=10_000)
    parser.add_argument("--max-function-labels", type=int, default=0)
    parser.add_argument("--trace-timeout-seconds", type=int, default=30)
    parser.add_argument("--cpu-time-seconds", type=int, default=10)
    parser.add_argument("--build-timeout-seconds", type=int, default=1800)
    parser.add_argument("--jobs", type=int, default=min(4, os.cpu_count() or 1))
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--validate-only", action="store_true")
    parser.add_argument("--capture-stopped-range", action="append", nargs=2,
                        type=lambda text: int(text,0), default=[], metavar=('ADDRESS','BYTES'),
                        help="Snapshot explicit owned physical ranges after a stopped GPU submission; max8, default4KiB aggregate")
    parser.add_argument('--capture-stopped-byte-budget',type=lambda text:int(text,0),default=physical_capture.DEFAULT_BYTE_BUDGET,
                        help='Explicit aggregate capture cap; default4KiB, maximum2MiB; requires requested ranges')
    parser.add_argument('--inspect-stopped-ownership',action='append',nargs=2,type=lambda text:int(text,0),default=[],
                        metavar=('ADDRESS','BYTES'),help='Metadata only: max8 canonical ranges,16MiB each; no payload capture')
    return parser.parse_args(argv)


def run(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    if args.inspect_stopped_ownership:
        if not args.graphics_backend:raise TraceError('ownership queries require native graphics')
        try:physical_capture.validate_ownership_ranges(args.inspect_stopped_ownership)
        except ValueError as exc:raise TraceError(str(exc)) from exc
    try: physical_capture.validate_budget(args.capture_stopped_byte_budget)
    except ValueError as exc: raise TraceError(str(exc)) from exc
    if not args.capture_stopped_range and args.capture_stopped_byte_budget!=physical_capture.DEFAULT_BYTE_BUDGET:
        raise TraceError('nondefault physical capture budget requires explicit ranges')
    if args.capture_stopped_range:
        if not args.graphics_backend: raise TraceError('physical capture requires native graphics')
        try: physical_capture.validate_ranges(args.capture_stopped_range,args.capture_stopped_byte_budget)
        except ValueError as exc: raise TraceError(str(exc)) from exc
    if args.experimental_idle_cache_pair and (not args.native_state_batches or not args.native_empty_cache_coherency):
        raise TraceError('experimental idle cache pair requires native state batches and empty-cache coherency')
    if args.experimental_native_audio_renderer and (not args.experimental_native_audio or not args.cooperative_threads):
        raise TraceError("native audio renderer requires native audio and cooperative threads")
    if args.native_event_wake_boost and not args.cooperative_threads:
        raise TraceError("native event wake boost requires cooperative threads")
    if args.native_critical_section_waits and not args.cooperative_threads:
        raise TraceError("native critical-section waits require cooperative threads")
    if args.native_high_priority_tiers and not args.cooperative_threads:
        raise TraceError("native +3/+4 priority requires cooperative threads")
    if args.native_category1_unity_gain and not args.experimental_native_audio_renderer:
        raise TraceError("native category1 gain requires native PCM renderer")
    if args.xex_image_protections and args.title_xex is None:
        raise TraceError("image protection metadata requires the explicit title XEX")
    if args.experimental_gpu_clock_500mhz and not args.graphics_backend:
        raise TraceError("experimental GPU clock requires the explicit native graphics backend")
    if args.native_initial_coherency and (not args.graphics_backend or not args.cooperative_threads):
        raise TraceError("native initial coherency requires graphics and serialized cooperative execution")
    if args.experimental_native_initialization and not args.native_initial_coherency:
        raise TraceError("experimental native initialization requires native initial coherency")
    if args.native_empty_cache_coherency and not args.experimental_native_initialization:
        raise TraceError("native empty-cache coherency requires experimental native initialization")
    if args.native_state_batches and not args.experimental_native_initialization:
        raise TraceError("native state batches require experimental native initialization")
    if args.capture_stopped_shaders and not args.graphics_backend:
        raise TraceError("stopped shader capture requires native graphics")
    if args.capture_stopped_pm4 and not args.graphics_backend:
        raise TraceError("stopped PM4 capture requires native graphics")
    if args.graphics_backend and not args.validate_only:
        raise TraceError("use run_android_trace.py for the native Vulkan graphics backend")
    if args.xam_module_base is not None and not args.cooperative_threads:
        raise TraceError("native XAM module requires the explicit cooperative runtime")
    if not 1 <= args.thread_quantum <= 1000000:
        raise TraceError("thread quantum must be 1..1000000 function entries")
    if args.cooperative_threads and (args.thread_pool_base is None or args.thread_type_base is None):
        raise TraceError("cooperative threads require the explicit thread pool and type identity")
    if args.expected_chunk_count < 1:
        raise TraceError("--expected-chunk-count must be positive")
    if not 1 <= args.function_entry_budget <= MAX_FUNCTION_ENTRIES:
        raise TraceError(f"function-entry budget must be 1..{MAX_FUNCTION_ENTRIES}")
    if not 0 <= args.max_function_labels <= min(
        MAX_FUNCTION_LABELS, args.function_entry_budget
    ):
        raise TraceError("function-label limit is invalid or exceeds the entry budget")
    if not 1 <= args.trace_timeout_seconds <= MAX_TRACE_SECONDS:
        raise TraceError(f"trace timeout must be 1..{MAX_TRACE_SECONDS} seconds")
    if not 1 <= args.cpu_time_seconds <= MAX_CPU_SECONDS:
        raise TraceError(f"CPU-time limit must be 1..{MAX_CPU_SECONDS} seconds")
    if args.build_timeout_seconds < 1 or args.jobs < 1:
        raise TraceError("build timeout and jobs must be positive")
    title_arguments = (
        args.title_xex,
        args.title_header_base,
        args.title_export_address,
        args.title_module_base,
    )
    if any(value is not None for value in title_arguments) and not all(
        value is not None for value in title_arguments
    ):
        raise TraceError(
            "--title-xex, --title-header-base, --title-export-address, and "
            "--title-module-base must be provided together"
        )
    physical_arguments = (
        args.physical_base,
        args.physical_size,
        args.physical_address_offset,
    )
    if any(value is not None for value in physical_arguments) and not all(
        value is not None for value in physical_arguments
    ):
        raise TraceError(
            "--physical-base, --physical-size, and --physical-address-offset "
            "must be provided together"
        )
    thread_arguments = (args.thread_pcr_base, args.thread_record_base, args.thread_tls_base)
    if any(value is not None for value in thread_arguments) and (
        not all(value is not None for value in thread_arguments)
        or args.title_xex is None
    ):
        raise TraceError("all three --thread-*-base arguments require the title environment")
    pool_arguments = (args.thread_pool_base, args.thread_pool_size)
    if args.thread_type_base is not None and args.thread_pcr_base is None:
        raise TraceError("thread type identity requires initial-thread staging")
    if any(value is not None for value in pool_arguments) and (
        not all(value is not None for value in pool_arguments) or args.thread_pcr_base is None
    ):
        raise TraceError("both thread-pool arguments require initial-thread staging")

    root = repository_root().expanduser().resolve()
    installed_path = None
    installed_title = installed_count = 0
    if args.installed_content_catalog is not None:
        if not args.installed_content_catalog.is_absolute() or args.title_xex is None:
            raise TraceError("installed-content inventory requires an absolute path and title environment")
        installed_path = require_private_path(args.installed_content_catalog, root, "installed-content catalog")
        require_regular_file(installed_path, "installed-content catalog")
        require_git_ignored(installed_path, root, "installed-content catalog")
        try:
            with installed_path.open("rb") as stream:
                installed_title, installed_count = content_catalog.parse_catalog(stream.read(content_catalog.MAX_BYTES + 1))
        except (OSError, ValueError) as exc:
            raise TraceError("installed-content inventory validation failed") from exc
    installed_packages = {}
    installed_files = []
    if len(args.installed_package) > 16 or len(args.installed_file) > 32 \
            or ((args.installed_package or args.installed_file) and installed_path is None):
        raise TraceError("bounded installed packages require the title inventory")
    for index_text, path_text in args.installed_package:
        try:
            index = int(index_text, 0)
            path = Path(path_text)
            if not path.is_absolute() or not 1 <= index <= installed_count or index in installed_packages:
                raise ValueError("invalid or duplicate installed package")
            path = require_private_path(path, root, "installed package metadata")
            require_regular_file(path, "installed package metadata")
            require_git_ignored(path, root, "installed package metadata")
            with path.open("rb") as stream:
                entries = file_metadata.parse_catalog(stream.read(file_metadata.MAX_BYTES + 1))
            if len(entries) > 256 or sorted(entries)[0] != "content:\\":
                raise ValueError("installed package metadata namespace rejected")
            installed_packages[index] = (path, entries)
        except (ValueError, OSError) as exc:
            raise TraceError("installed package metadata validation failed") from exc
    seen_installed = set()
    for package_text, record_text, path_text in args.installed_file:
        try:
            package_index, record_index = int(package_text, 0), int(record_text, 0)
            if package_index not in installed_packages or (package_index, record_index) in seen_installed:
                raise ValueError("invalid or duplicate installed file binding")
            entries = installed_packages[package_index][1]
            if not 1 <= record_index <= len(entries):
                raise ValueError("installed file record outside snapshot")
            record = entries[sorted(entries)[record_index - 1]]
            path = Path(path_text)
            if not path.is_absolute():
                raise ValueError("installed file path is not absolute")
            path = require_private_path(path, root, "installed content file")
            require_regular_file(path, "installed content file")
            require_git_ignored(path, root, "installed content file")
            if record[6] != 1 or record[5] > 8 << 30 or path.stat().st_size != record[5]:
                raise ValueError("installed file size/type mismatch")
            seen_installed.add((package_index, record_index))
            installed_files.append((package_index, record_index, path, record[5]))
        except (ValueError, OSError) as exc:
            raise TraceError("installed file validation failed") from exc
    for index, (_, entries) in installed_packages.items():
        expected = {(index, i) for i, name in enumerate(sorted(entries), 1) if entries[name][6] == 1}
        if not expected or not expected.issubset(seen_installed):
            raise TraceError("installed package requires all regular files preopened")
    catalog_path = None
    catalog_records = 0
    catalog_entries = {}
    if args.file_metadata_catalog is not None:
        if not args.file_metadata_catalog.is_absolute():
            raise TraceError("file metadata catalog requires an absolute private path")
        catalog_path = require_private_path(args.file_metadata_catalog, root, "file metadata catalog")
        require_regular_file(catalog_path, "file metadata catalog")
        require_git_ignored(catalog_path, root, "file metadata catalog")
        try:
            with catalog_path.open("rb") as stream:
                catalog_entries = file_metadata.parse_catalog(stream.read(file_metadata.MAX_BYTES + 1))
                catalog_records = len(catalog_entries)
        except (OSError, ValueError) as exc:
            raise TraceError("file metadata catalog validation failed") from exc
    content_files = []
    if len(args.read_only_file) > 16 or (args.read_only_file and catalog_path is None):
        raise TraceError("up to sixteen explicit content files require a metadata catalog")
    seen_records = set()
    for index_text, path_text in args.read_only_file:
        try:
            index = int(index_text, 0)
        except ValueError as exc:
            raise TraceError("invalid content record index") from exc
        if index < 1 or index > catalog_records or index in seen_records:
            raise TraceError("content record is missing or duplicated")
        seen_records.add(index)
        content_path = Path(path_text)
        if not content_path.is_absolute():
            raise TraceError("explicit content path must be absolute")
        content_path = require_private_path(content_path, root, "read-only content")
        require_regular_file(content_path, "read-only content")
        require_git_ignored(content_path, root, "read-only content")
        record = catalog_entries[sorted(catalog_entries)[index - 1]]
        if record[6] != 1 or record[5] > 8 << 30 or content_path.stat().st_size != record[5]:
            raise TraceError("explicit content is not a matching regular-file record")
        content_files.append((index, content_path, record[5]))
    output_arg = args.output_dir or (root / "local-analysis/host-guest-trace-run")
    ppc_dir = validate_private_directory(args.generated_ppc_dir, root, "generated PPC directory")
    bridge_dir = validate_private_directory(
        args.guarded_bridge_dir, root, "guarded bridge directory"
    )
    output_dir = require_private_path(output_arg, root, "trace output directory")
    require_git_ignored(output_dir, root, "trace output directory")
    require_regular_file(Path(__file__).parent / "CMakeLists.txt", "trace CMake project")
    chunks = discover_chunks(ppc_dir, args.expected_chunk_count)
    trap_sites, timebase_sites = validate_runtime_hooks(chunks, ppc_dir, min(args.jobs, 4))
    generated_layout = read_generated_layout(ppc_dir)
    image_range = generated_layout.image
    mapping_count = validate_function_mapping_source(ppc_dir, generated_layout.code)
    heap_range = GuestRange("heap", args.heap_base, args.heap_size)
    stack_range = GuestRange("stack", args.stack_base, args.stack_size)
    physical: PhysicalArenaInputs | None = None
    if args.physical_base is not None:
        assert args.physical_size is not None
        assert args.physical_address_offset is not None
        physical = PhysicalArenaInputs(
            GuestRange("physical_memory", args.physical_base, args.physical_size),
            args.physical_address_offset,
        )
    validate_ranges(image_range, heap_range, stack_range, physical=physical)
    decoded_image = validate_decoded_image(args.decoded_image, image_range.size, root)
    title: TitleEnvironmentInputs | None = None
    thread: ThreadEnvironmentInputs | None = None
    if args.title_xex is not None:
        assert args.title_header_base is not None
        assert args.title_export_address is not None
        assert args.title_module_base is not None
        title = validate_title_environment_inputs(
            args.title_xex,
            decoded_image,
            image_range,
            args.title_header_base,
            args.title_export_address,
            args.title_module_base,
            root,
        )
        validate_ranges(
            image_range,
            heap_range,
            stack_range,
            title,
            physical,
        )
    if installed_path is not None and (title is None or title.title_id != installed_title):
        raise TraceError("installed-content inventory does not match the executable title")
    if args.thread_pcr_base is not None:
        assert title is not None
        assert args.thread_record_base is not None
        assert args.thread_tls_base is not None
        thread = validate_thread_environment_inputs(
            title,
            image_range,
            args.thread_pcr_base,
            args.thread_record_base,
            args.thread_tls_base,
        )
        validate_ranges(image_range, heap_range, stack_range, title, physical, thread)
    bridge_report = validate_guarded_bridge(bridge_dir, args.expected_chunk_count)

    thread_pool = None
    if args.thread_pool_base is not None:
        thread_pool = GuestRange("created_thread_arena", args.thread_pool_base, args.thread_pool_size)
        validate_ranges(image_range, heap_range, stack_range, title, physical, thread, thread_pool)
    thread_type = None
    if args.thread_type_base is not None:
        thread_type = GuestRange("thread_type_identity", args.thread_type_base, 4)
        validate_ranges(image_range, heap_range, stack_range, title, physical, thread, thread_pool, thread_type)
    xam_module = None
    if args.xam_module_base is not None:
        if title is None or title.xam_minimum_version is None or title.xam_minimum_version >> 28 != 2:
            raise TraceError("native XAM module requires one parser-validated Xbox 2.x minimum version")
        xam_module = GuestRange("native_xam_module", args.xam_module_base, 0x64)
        validate_ranges(image_range, heap_range, stack_range, title, physical, thread, thread_pool, thread_type, xam_module)

    extra_data_storage = []
    kernel_version_arguments = native_kernel_version_arguments(args, title)
    if kernel_version_arguments:
        extra_data_storage.append(GuestRange('native_kernel_version', args.native_kernel_version_base, 8))
    if args.hardware_info_base is not None:
        if title is None or title.hardware_info_slot is None or not args.cooperative_threads or installed_path is None:
            raise TraceError("hardware-info profile requires one parsed slot, cooperative CPUs and installed storage")
        extra_data_storage.append(GuestRange("hardware_info", args.hardware_info_base, 16))
    if args.video_globals_base is not None:
        if title is None or len(title.video_device_slots) != 2:
            raise TraceError("video globals require both parser-confirmed device variable slots")
        extra_data_storage.append(GuestRange("video_globals", args.video_globals_base, 8))
    if args.uptime_bundle_base is not None:
        if title is None or title.uptime_bundle_slot is None:
            raise TraceError("uptime bundle requires a unique parser-confirmed data import")
        extra_data_storage.append(GuestRange("uptime_bundle", args.uptime_bundle_base, 24))
    if args.command_line_base is not None:
        if title is None or title.command_line_slot is None:
            raise TraceError("launch command line requires a unique parser-confirmed data import")
        extra_data_storage.append(GuestRange("command_line", args.command_line_base, 64))
    if args.no_debug_monitor_base is not None:
        if title is None or title.debug_monitor_slot is None:
            raise TraceError("no-debug-monitor profile requires a unique parser-confirmed data import")
        extra_data_storage.append(GuestRange("no_debug_monitor", args.no_debug_monitor_base, 4))
    if args.no_cert_monitor_base is not None:
        if title is None or title.cert_monitor_slot is None:
            raise TraceError("no-cert-monitor profile requires a unique parser-confirmed data import")
        extra_data_storage.append(GuestRange("no_cert_monitor", args.no_cert_monitor_base, 4))
    if args.hsio_lock_base is not None:
        if title is None or title.hsio_lock_slot is None:
            raise TraceError("HSIO lock requires a unique parser-confirmed data import")
        extra_data_storage.append(GuestRange("hsio_lock", args.hsio_lock_base, 28))
    validate_ranges(image_range, heap_range, stack_range, title, physical, thread, thread_pool,
                    thread_type, xam_module, extra_data_storage)
    output_dir.mkdir(parents=True, exist_ok=True)
    output_dir.chmod(0o700)
    report_path = output_dir / "trace-report.json"
    report: dict[str, object] = {
        "schema_version": 2,
        "mode": "guarded-native-host-trace",
        "platform": platform.system().lower(),
        "video_mode": {"configuration": args.virtual_video_mode,
                       "physical_monitor_queried": False, "rendering_implemented": False},
        "graphics_backend": {"configuration": args.graphics_backend, "guest_firmware_executed": False,
                             "guest_commands_connected": args.native_initial_coherency, "frame_presentation": False,
                             "guest_command_profile": "experimental-cold-constant-initialization" if args.experimental_native_initialization else
                                 ("one-cold-start-empty-cache" if args.native_initial_coherency else None)},
        "experimental_gpu_clock": {"enabled": args.experimental_gpu_clock_500mhz,
            "hypothesis": "register-044B-session-nanoseconds-divided-by-2-modulo-2^32",
            "hardware_semantics_verified": False, "signals_completion": False},
        "data_import_guard": {"scalar_only": True, "slots_private": True,
            "unbound_slots": list(unbound_data_import_slots(title, thread_type is not None,
                extra_bound_data_slots(args, title))) if title else []},
        "kernel_data_profiles": {"hardware_info": args.hardware_info_base is not None,
            "video_device_variables": args.video_globals_base is not None,
            "uptime_bundle_base": args.uptime_bundle_base, "uptime_unit": "session-milliseconds-modulo-2^32",
            "uptime_updates": "on-scalar-read", "timestamp_other_fields_supported": False,
            "launch_command_line": "quoted-default.xex-no-arguments" if args.command_line_base is not None else None,
            "debug_monitor": "no-xbox-remote-debugger-attached" if args.no_debug_monitor_base is not None else None,
            "cert_monitor": "no-xbox-certification-monitor-attached" if args.no_cert_monitor_base is not None else None,
            "hsio_lock": "native-28-byte-critical-section-spin10000" if args.hsio_lock_base is not None else None,
            "actual_console_hardware": False, "unknown_hardware_bytes": "zero-reserved",
            "virtual_cpu_count": 6 if args.hardware_info_base is not None else None},
        "private_inputs": {
            "paths_redacted": True,
            "expected_chunk_count": args.expected_chunk_count,
            "validated_chunk_count": args.expected_chunk_count,
            "guarded_trap_annotations": trap_sites,
            "runtime_timebase_sites": timebase_sites,
            "decoded_image_size": image_range.size,
            "implemented_import_count": bridge_report["symbol_partition_validation"][
                "implemented_expected"
            ],
            "guarded_import_count": bridge_report["unimplemented_import_stops"]["count"],
        },
        "ranges": {
            item.name: {"base": f"0x{item.base:08X}", "size": f"0x{item.size:X}"}
            for item in (
                image_range,
                heap_range,
                stack_range,
                *((physical.memory,) if physical is not None else ()),
            )
        },
        "limits": {
            "function_entries": args.function_entry_budget,
            "function_labels": args.max_function_labels,
            "trace_seconds": args.trace_timeout_seconds,
            "cpu_seconds": args.cpu_time_seconds,
            "protocol_bytes": contained_process.PROTOCOL_BYTE_LIMIT,
            "open_files": contained_process.OPEN_FILE_LIMIT,
        },
        "safety": {
            "guest_text_suppressed": True,
            "jni_wired": False,
            "fault_containment_claimed": False,
            "fault_containment_scope": "worker-process-only",
            "in_process_signal_recovery": False,
            "guarded_missing_imports": True,
            "runnable_guest_support_claimed": False,
            "guest_process_isolated": True,
            "stderr_discarded": True,
            "rss_limit": None,
        },
        "indirect_calls": {
            "resolver": "exact-validated-host-mapping",
            "validated_mapping_count": mapping_count,
            "guest_addresses_redacted": True,
            "guest_pointer_table_written": False,
        },
        "containment": {
            "mode": "one-shot-process-group",
            "new_session": True,
            "stdin_null": True,
            "stderr_discarded": True,
            "environment_allowlisted": True,
            "working_directory_private": True,
            "termination_sequence": ["SIGTERM", "SIGKILL"],
            "rlimit_core_bytes": 0,
            "rlimit_file_bytes": 0,
        },
        "validation": {"succeeded": True},
        "build": {"attempted": False, "succeeded": None},
        "execution": {
            "attempted": False,
            "succeeded": None,
            "outcome": "not-attempted",
        },
    }
    if physical is None:
        report["physical_memory"] = {"enabled": False}
    else:
        report["physical_memory"] = {
            "enabled": True,
            "guest_base": f"0x{physical.memory.base:08X}",
            "size": f"0x{physical.memory.size:X}",
            "physical_address_offset": (
                f"0x{physical.physical_address_offset:08X}"
            ),
            "allocator": "deterministic-top-down",
        }
    if thread is None:
        report["thread_environment"] = {"enabled": False}
    else:
        report["thread_environment"] = {
            "enabled": True,
            "static_tls_bytes": thread.data_size,
            "copied_template_bytes": thread.raw_data_size,
            "dynamic_tls_slots": thread.slot_count,
            "guest_r13_bound": True,
            "template_content_emitted": False,
            "scheduler_implemented": False,
            "ranges": {
                item.name: {"base": f"0x{item.base:08X}", "size": f"0x{item.size:X}"}
                for item in (thread.pcr, thread.thread, thread.tls)
            },
        }
    report["created_threads"] = {"enabled": thread_pool is not None, "scheduler_implemented": False}
    report["thread_object_type"] = {"enabled": thread_type is not None,
                                   "complete_object_type_descriptor": False}
    report["cooperative_threads"] = {"enabled": args.cooperative_threads,
        "quantum": args.thread_quantum, "maximum_tasks": 65,
        "guest_execution_serialized": True, "full_xbox_scheduler": False,
        "virtual_cpu_affinity": args.cooperative_threads,
        "virtual_cpu_count": 6, "physical_host_affinity": False,
        "debug_thread_naming": args.cooperative_threads, "guest_thread_names_retained_in_report": False}
    report["cooperative_threads"].update({"infinite_nonalertable_waits": args.cooperative_threads,
        "maximum_wait_operations": args.function_entry_budget, "timed_waits": args.cooperative_threads,
        "relative_wait_cap_seconds": 60, "absolute_waits": False, "wait_clock": "host-monotonic"})
    report["cooperative_threads"].update({"priority_policy": "bounded-weighted-checkpoint-slices" if args.cooperative_threads else None,
        "priority_increment_range": [-2, 4 if args.native_high_priority_tiers else 2],
        "priority_slice_weights": [0.25, 0.5, 1, 2, 4]+([8,8] if args.native_high_priority_tiers else []),
        "priority_saturation_threshold": 16, "priority_saturated_values": [-16, 16],
        "priority_saturated_slice_weights": [0.125, 8],
        "priority_saturation_contract": "explicit-nt-compatible-subset",
        "minimum_slice_entries": 1, "strict_xbox_priority_order": False, "dynamic_priority_boosts": False})
    report["notifications"] = {"enabled": args.cooperative_threads, "maximum_listeners": 128,
        "queue_capacity": 64, "startup_events_injected": False, "host_event_source_wired": False}
    report["dispatcher_events"] = {"header_bytes": 16, "supported_types": [0, 1],
        "owned_writable_allocations_only": True, "nt_handle_aliasing": False,
        "wait_queues": "host-managed-no-guest-list-rewrite", "parked_allocation_pinning": True,
        "signal_boosts": False, "signal_then_wait_irql": False, "alertable_waits": False}
    report["network_policy"] = {"configuration_and_lifecycle_only": args.cooperative_threads,
        "sockets_opened": False, "external_network_traffic": False}
    report["file_metadata"] = {"enabled": catalog_path is not None, "records": catalog_records,
        "paths_redacted": True, "read_only_snapshot": True, "file_content_reads": False,
        "timestamps": "observed-host-file-metadata", "allocation_policy": "2048-byte-rounded-logical-size",
        "original_xdvdfs_metadata_emulated": False, "unmounted_namespace_returns_not_found": False}
    report["file_content"] = {"explicit_files_bound": len(content_files), "read_only": True,
        "bound_bytes": sum(item[2] for item in content_files), "maximum_read_bytes": MAXIMUM_GUEST_FILE_READ_BYTES,
        "maximum_source_chunk_bytes": 1048576, "transactional_read_staging": True,
        "guest_selected_host_paths": False, "file_paths_redacted": True,
        "async_reads": False, "writes": False}
    report["installed_content"] = {"enabled": installed_path is not None,
        "records": installed_count, "virtual_read_only_device": 1, "names_redacted": True,
        "preopened_packages": len(installed_packages), "preopened_files": len(installed_files),
        "preopened_bytes": sum(item[3] for item in installed_files),
        "capacity_policy": "sum-bound-logical-allocations", "writable_free_bytes": 0, "virtual_device_type": 1,
        "read_only_mount_support": bool(installed_packages), "license_grants": False, "asynchronous_enumeration": False}
    report["clock_policy"] = {"system_time": "host-utc-anchor-plus-monotonic-elapsed",
        "system_time_unit": "100ns-since-1601", "performance_frequency_hz": 50000000,
        "timebase_source": "shared-process-monotonic-epoch", "xenon_fixed_timebase_emulated": True,
        "cycle_accurate": False, "wall_clock_values_retained": False}
    report["native_xam_module"] = {"enabled": xam_module is not None,
        "arbitrary_xex_loading": False, "online_party_exports_available": False,
        "unknown_exports_guarded": True}
    if xam_module is not None:
        report["native_xam_module"].update({"compatibility_version": f"0x{title.xam_minimum_version:08X}",
            "version_source": "title-xam-import-minimum", "actual_xbox_firmware_running": False,
            "extended_network_startup_export_available": False})
    if thread_type is not None:
        report["thread_object_type"].update({"identity_address": f"0x{thread_type.base:08X}",
            "public_import": "ExThreadObjectType", "ordinal": "0x1B"})
    if thread_pool is not None:
        report["created_threads"].update({"guest_base": f"0x{thread_pool.base:08X}",
            "size": f"0x{thread_pool.size:X}", "maximum_threads": 64,
            "creation_flags": [0, 1] if args.cooperative_threads else [1],
            "unsuspended_creation_enqueues_before_publication": args.cooperative_threads})
    if title is None:
        report["title_environment"] = {"enabled": False}
    else:
        report["title_environment"] = {
            "enabled": True,
            "raw_xex_path_redacted": True,
            "raw_xex_size": title.raw_xex_size,
            "staged_header_size": title.header.size,
            "security_image_size": title.full_image.size,
            "data_import": {
                "library": "xboxkrnl.exe",
                "ordinal": f"0x{EXECUTABLE_MODULE_HANDLE_ORDINAL:X}",
                "count": 1,
            },
            "ranges": {
                item.name: {
                    "base": f"0x{item.base:08X}",
                    "size": f"0x{item.size:X}",
                }
                for item in (
                    title.full_image,
                    title.header,
                    title.exported_variable,
                    title.module,
                )
            },
        }
    if args.validate_only:
        write_report(report_path, report)
        print(f"Private host-trace inputs validated; redacted report: {report_path}")
        return 0
    if platform.system() != "Darwin":
        report["build"] = {
            "attempted": False,
            "succeeded": False,
            "failure_kind": "unsupported_platform",
        }
        write_report(report_path, report)
        raise TraceError("native host trace builds are currently supported only on macOS")
    cmake = shutil.which("cmake")
    ninja = shutil.which("ninja")
    if cmake is None or ninja is None:
        report["build"] = {
            "attempted": False,
            "succeeded": False,
            "failure_kind": "missing_build_tool",
        }
        write_report(report_path, report)
        raise TraceError("cmake and ninja are required for a native trace build")

    build_dir = output_dir / "build"
    configure = run_command(
        [
            cmake,
            "-S",
            str(Path(__file__).parent),
            "-B",
            str(build_dir),
            "-G",
            "Ninja",
            f"-DPPC_DIR={ppc_dir}",
            f"-DBRIDGE_DIR={bridge_dir}",
            f"-DREPOSITORY_ROOT={root}",
            f"-DEXPECTED_CHUNK_COUNT={args.expected_chunk_count}",
        ],
        args.build_timeout_seconds,
    )
    build = CommandOutcome(None, "", "", "configure_failed")
    if configure.succeeded:
        build = run_command(
            [
                cmake,
                "--build",
                str(build_dir),
                "--target",
                "host_guest_trace",
                "--parallel",
                str(args.jobs),
            ],
            args.build_timeout_seconds,
        )
    build_succeeded = configure.succeeded and build.succeeded
    report["build"] = {
        "attempted": True,
        "succeeded": build_succeeded,
        "configure_returncode": configure.returncode,
        "build_returncode": build.returncode,
        "failure_kind": configure.failure_kind or build.failure_kind,
        "diagnostic_line_count": len(
            (configure.stdout + configure.stderr + build.stdout + build.stderr).splitlines()
        ) if not build_succeeded else 0,
    }
    if not build_succeeded:
        write_report(report_path, report)
        print(f"Native host-trace build failed; redacted report: {report_path}", file=sys.stderr)
        return 1

    executable = build_dir / "host_guest_trace"
    if executable.is_symlink() or not executable.is_file() or not os.access(executable, os.X_OK):
        report["execution"] = {
            "attempted": False,
            "succeeded": False,
            "outcome": "launch-failure",
            "completed_function_entries": None,
            "public_stop_import": None,
        }
        write_report(report_path, report)
        return 1
    trace_command = [
        str(executable),
        str(decoded_image),
        str(image_range.base),
        str(image_range.size),
        str(heap_range.base),
        str(heap_range.size),
        str(stack_range.base),
        str(stack_range.size),
        str(args.function_entry_budget),
        str(args.max_function_labels),
        str(mapping_count),
    ]
    if physical is not None:
        trace_command.extend(
            [
                str(physical.memory.base),
                str(physical.memory.size),
                str(physical.physical_address_offset),
            ]
        )
    if title is not None:
        trace_command.extend(
            [
                str(title.raw_xex),
                str(title.raw_xex_size),
                str(title.full_image.size),
                str(title.header.base),
                str(title.header.size),
                str(title.import_slot),
                str(title.exported_variable.base),
                str(title.module.base),
                str(title.entry_point),
            ]
        )
    if thread is not None:
        trace_command.extend([
            "--thread-environment",
            str(thread.pcr.base), str(thread.thread.base), str(thread.tls.base),
            str(thread.raw_data_address), str(thread.raw_data_size),
            str(thread.data_size), str(thread.slot_count),
        ])
    if thread_pool is not None:
        trace_command.extend(["--thread-pool", str(thread_pool.base), str(thread_pool.size)])
    if thread_type is not None:
        trace_command.extend(["--thread-object-type", str(title.thread_object_type_slot), str(thread_type.base)])
    if installed_path is not None:
        trace_command.extend(["--installed-content-catalog", str(installed_path)])
    for index, (path, _) in installed_packages.items():
        trace_command.extend(["--installed-package", str(index), str(path)])
    for index, record, path, _ in installed_files:
        trace_command.extend(["--installed-file", str(index), str(record), str(path)])
    if catalog_path is not None:
        trace_command.extend(["--file-metadata-catalog", str(catalog_path)])
    for index, content_path, _ in content_files:
        trace_command.extend(["--read-only-file", str(index), str(content_path)])
    if xam_module is not None:
        trace_command.extend(["--native-xam-module", str(xam_module.base), str(title.xam_minimum_version)])
    if args.cooperative_threads:
        trace_command.extend(["--cooperative-threads", str(args.thread_quantum)])
    if args.virtual_video_mode is not None:
        trace_command.extend(["--virtual-video-mode", args.virtual_video_mode])
    if title is not None:
        slots = unbound_data_import_slots(title, thread_type is not None, extra_bound_data_slots(args, title))
        if slots: trace_command.extend(["--unbound-data-imports", ",".join(map(str, slots))])
        if args.hardware_info_base is not None:
            trace_command.extend(["--hardware-info", str(title.hardware_info_slot), str(args.hardware_info_base)])
        if args.video_globals_base is not None:
            trace_command.extend(["--video-globals", *map(str, title.video_device_slots), str(args.video_globals_base)])
        if args.uptime_bundle_base is not None:
            trace_command.extend(["--uptime-bundle", str(title.uptime_bundle_slot), str(args.uptime_bundle_base)])
        if args.command_line_base is not None:
            trace_command.extend(["--command-line", str(title.command_line_slot), str(args.command_line_base)])
        if args.no_debug_monitor_base is not None:
            trace_command.extend(["--no-debug-monitor", str(title.debug_monitor_slot), str(args.no_debug_monitor_base)])
        if args.no_cert_monitor_base is not None:
            trace_command.extend(["--no-cert-monitor", str(title.cert_monitor_slot), str(args.no_cert_monitor_base)])
        if args.hsio_lock_base is not None:
            trace_command.extend(["--hsio-lock", str(title.hsio_lock_slot), str(args.hsio_lock_base)])
    if args.xex_image_protections:
        trace_command.append('--xex-image-protections')
    if args.disconnected_input:
        trace_command.append('--disconnected-input')
    if args.experimental_native_audio:
        trace_command.append('--experimental-native-audio')
    if args.experimental_native_audio_renderer:
        trace_command.append('--experimental-native-audio-renderer')
    if args.native_event_wake_boost:
        trace_command.append('--native-event-wake-boost')
    if args.native_category1_unity_gain:
        trace_command.append('--native-category1-unity-gain')
    if args.native_critical_section_waits:
        trace_command.append('--native-critical-section-waits')
    if args.native_high_priority_tiers:
        trace_command.append('--native-high-priority-tiers')
    if args.experimental_idle_cache_pair:
        trace_command.append('--experimental-idle-cache-pair')
    if args.capture_stopped_byte_budget!=physical_capture.DEFAULT_BYTE_BUDGET:
        trace_command.extend(['--capture-stopped-byte-budget',str(args.capture_stopped_byte_budget)])
    for address, count in args.capture_stopped_range:
        trace_command.extend(['--capture-stopped-range',str(address),str(count)])
    for address,count in args.inspect_stopped_ownership:
        trace_command.extend(['--inspect-stopped-ownership',str(address),str(count)])
    trace_command.extend(kernel_version_arguments)
    process_result = contained_process.run_contained(
        trace_command,
        build_dir,
        contained_process.ContainedProcessLimits(
            wall_seconds=args.trace_timeout_seconds,
            cpu_seconds=args.cpu_time_seconds,
            protocol_bytes=physical_capture.protocol_byte_limit(args.capture_stopped_byte_budget),
        ),
    )
    if process_result.kind != contained_process.ContainedOutcomeKind.EXITED:
        report["execution"] = {
            "attempted": True,
            "succeeded": False,
            "outcome": process_result.kind.value,
            "returncode": process_result.returncode,
            "signal_number": process_result.signal_number,
            "signal_name": process_result.signal_name,
            "elapsed_milliseconds": process_result.elapsed_milliseconds,
            "term_sent": process_result.term_sent,
            "kill_sent": process_result.kill_sent,
            "completed_function_entries": None,
            "public_stop_import": None,
            "guest_output_retained": False,
        }
        write_report(report_path, report)
        print(
            f"Contained host trace ended with {process_result.kind.value}; "
            f"redacted report: {report_path}",
            file=sys.stderr,
        )
        return 1
    if process_result.returncode != 0:
        report["execution"] = {
            "attempted": True,
            "succeeded": False,
            "outcome": "invalid-protocol",
            "returncode": process_result.returncode,
            "elapsed_milliseconds": process_result.elapsed_milliseconds,
            "completed_function_entries": None,
            "public_stop_import": None,
            "guest_output_retained": False,
        }
        write_report(report_path, report)
        return 1
    try:
        protocol_output = process_result.stdout.decode("utf-8", errors="strict")
        protocol = parse_trace_protocol(
            protocol_output,
            args.max_function_labels,
            args.function_entry_budget,
            mapping_count,
            cooperative=args.cooperative_threads,
            guarded_data_slots=report["data_import_guard"]["unbound_slots"],
            uptime_bundle_base=args.uptime_bundle_base,
        )
        if (protocol.xex_image_protections is not None) != args.xex_image_protections:
            raise TraceError('XEX image protection evidence does not match the requested binding')
        if (protocol.disconnected_input is not None) != args.disconnected_input:
            raise TraceError('input configuration evidence does not match the requested binding')
        if (protocol.experimental_native_audio is not None) != args.experimental_native_audio:
            raise TraceError('native audio evidence does not match the requested binding')
        if (protocol.native_audio_renderer is not None) != args.experimental_native_audio_renderer:
            raise TraceError('native PCM renderer evidence does not match the requested binding')
        if (protocol.native_event_wake_boost is not None) != args.native_event_wake_boost:
            raise TraceError('native event wake boost evidence does not match the requested binding')
        if (protocol.native_category1_gain is not None) != args.native_category1_unity_gain:
            raise TraceError('native category1 gain evidence does not match the requested binding')
        if (protocol.native_critical_section_waits is not None) != args.native_critical_section_waits:
            raise TraceError('native critical-section wait evidence does not match the requested binding')
        if (protocol.native_high_priority_tiers is not None) != args.native_high_priority_tiers:
            raise TraceError('native +3/+4 priority evidence does not match the requested binding')
        if (protocol.experimental_idle_cache_pairs is not None) != args.experimental_idle_cache_pair:
            raise TraceError('experimental idle cache pair evidence does not match the requested binding')
        try: physical_capture.require_requested(protocol.private_physical_capture,args.capture_stopped_range,args.capture_stopped_byte_budget)
        except ValueError as exc: raise TraceError(str(exc)) from exc
        try:physical_capture.require_ownership_requested(protocol.stopped_ownership_queries,args.inspect_stopped_ownership)
        except ValueError as exc:raise TraceError(str(exc)) from exc
    except (UnicodeError, TraceError) as exc:
        report["execution"] = {
            "attempted": True,
            "succeeded": False,
            "outcome": "invalid-protocol",
            "returncode": process_result.returncode,
            "elapsed_milliseconds": process_result.elapsed_milliseconds,
            "diagnostic": str(exc),
            "completed_function_entries": None,
            "public_stop_import": None,
            "guest_output_retained": False,
        }
        write_report(report_path, report)
        return 1

    report["execution"] = {
        "attempted": True,
        "succeeded": True,
        "outcome": "clean-completion" if protocol.stop_reason == "completed" else "guard-stop",
        "structured_trace_observed": True,
        "returncode": process_result.returncode,
        "elapsed_milliseconds": process_result.elapsed_milliseconds,
        "stop_reason": protocol.stop_reason,
        "completed_function_entries": protocol.completed_function_entries,
        "public_stop_import": protocol.stop_import,
        "host_exception": protocol.stop_exception,
        "workers_created": protocol.workers_created,
        "workers_entered": protocol.workers_entered,
        "context_switches": protocol.context_switches,
        "wait_operations": protocol.wait_operations,
        "priority_request": protocol.priority_request,
        "file_create_request": protocol.file_create_request,
        "file_io": protocol.file_io,
        "content_mount_rejection": protocol.content_mount_rejection,
        "import_arguments": protocol.import_arguments,
        "scalar_access": protocol.scalar_access,
        "graphics_initialized": protocol.graphics_initialized,
        "graphics_ring": protocol.graphics_ring,
        "native_display": protocol.native_display,
        "experimental_gpu_clock_500mhz_reads": protocol.experimental_gpu_clock_500mhz_reads,
        "cpu_timebase": protocol.cpu_timebase,
        "stopped_gpu_submission": protocol.stopped_gpu_submission,
        "stopped_gpu_indirect_graph": protocol.stopped_gpu_indirect_graph,
        "native_initial_coherency": protocol.native_initial_coherency,
        "experimental_native_initialization": protocol.experimental_native_initialization,
        "invalid_indirect_call": protocol.invalid_indirect_call,
        "native_edram_training_policy": protocol.native_edram_training_policy,
        "native_memory_statistics": protocol.native_memory_statistics,
        "native_kernel_version": protocol.native_kernel_version,
        "xex_image_protections": protocol.xex_image_protections,
        "disconnected_input": protocol.disconnected_input,
        "experimental_native_audio": protocol.experimental_native_audio,
        "native_audio_renderer": protocol.native_audio_renderer,
        "native_event_wake_boost": protocol.native_event_wake_boost,
        "native_category1_gain": protocol.native_category1_gain,
        "critical_section_rejection": protocol.critical_section_rejection,
        "native_critical_section_waits": protocol.native_critical_section_waits,
        "native_high_priority_tiers": protocol.native_high_priority_tiers,
        "thread_delays": protocol.thread_delays,
        "experimental_idle_cache_pairs": protocol.experimental_idle_cache_pairs,
        "dispatcher_wait_objects": protocol.dispatcher_wait_objects,
        "native_empty_cache_coherency": protocol.native_empty_cache_coherency,
        "native_state_batches": protocol.native_state_batches,
        "native_bin_state": protocol.native_bin_state,
        "native_ring_restart": protocol.native_ring_restart,
        "native_ring_warm_coherency": protocol.native_ring_warm_coherency,
        "native_warm_state_batches": protocol.native_warm_state_batches,
        "native_warm_empty_cache_coherency": protocol.native_warm_empty_cache_coherency,
        "native_owned_color_batch": protocol.native_owned_color_batch,
        "native_no_system_overlay": protocol.native_no_system_overlay,
        "native_swap_command": protocol.native_swap_command,
        "native_display_mode": protocol.native_display_mode,
        "native_command_storage_coherency": protocol.native_command_storage_coherency,
        "native_wrapped_swap": protocol.native_wrapped_swap,
        "native_scaler_timing": protocol.native_scaler_timing,
        "native_scaler_command": protocol.native_scaler_command,
        "native_scaler_storage_coherency": protocol.native_scaler_storage_coherency,
        "native_pending_submission": protocol.native_pending_submission,
        "native_scaler_wrapper_plan": protocol.native_scaler_wrapper_plan,
        "native_scaler_interrupt": protocol.native_scaler_interrupt,
        "native_scaler_completion": protocol.native_scaler_completion,
        "native_post_scaler_storage": protocol.native_post_scaler_storage,
        "native_display_gamma": protocol.native_display_gamma,
        "native_post_display_storage": protocol.native_post_display_storage,
        "native_post_output_storage": protocol.native_post_output_storage,
        "native_post_output_color": protocol.native_post_output_color,
        "native_post_output_system_context": protocol.native_post_output_system_context,
        "native_post_output_swap": protocol.native_post_output_swap,
        "native_post_output_swap_storage": protocol.native_post_output_swap_storage,
        "native_post_output_wrapped_swap": protocol.native_post_output_wrapped_swap,
        "native_post_swap_system_context": protocol.native_post_swap_system_context,
        "stopped_post_swap_request": protocol.stopped_post_swap_request,
        "native_deferred_swap_request": protocol.native_deferred_swap_request,
        "native_deferred_swap_storage": protocol.native_deferred_swap_storage,
        "native_deferred_color_prefix": protocol.native_deferred_color_prefix,
        "stopped_deferred_swap_interrupt": protocol.stopped_deferred_swap_interrupt,
        "native_display_output": protocol.native_display_output,
        "stopped_native_swap_request": protocol.stopped_native_swap_request,
        "stopped_native_swap_image": protocol.stopped_native_swap_image,
        "stopped_native_swap_consumer": protocol.stopped_native_swap_consumer,
        "stopped_gpu_work": protocol.stopped_gpu_work,
        "stopped_gpu_memory": protocol.stopped_gpu_memory,
        "stopped_word_staging": protocol.stopped_word_staging,
        "stopped_shader_proofs": protocol.stopped_shader_proofs,
        "stopped_physical_capture": physical_capture.save_capture(report_path.parent,protocol.private_physical_capture),
        "stopped_native_pending_capture": physical_capture.save_capture(report_path.parent,protocol.private_native_pending_capture),
        "stopped_deferred_shader_capture": deferred_shader_capture.save_capture(report_path.parent,protocol.private_deferred_shader_capture),
        "stopped_deferred_color_inspection": protocol.stopped_deferred_color_inspection,
        "stopped_deferred_memory_inspection": protocol.stopped_deferred_memory_inspection,
        "stopped_deferred_color_gpu": protocol.stopped_deferred_color_gpu,
        "stopped_deferred_color_timeline": protocol.stopped_deferred_color_timeline,
        "stopped_deferred_color_pending_state": protocol.stopped_deferred_color_pending_state,
        "stopped_deferred_color_write_guard": protocol.stopped_deferred_color_write_guard,
        "stopped_ownership_queries": protocol.stopped_ownership_queries,
        "stopped_constant_initialization": protocol.stopped_constant_initialization,
        "title_termination_registrations": protocol.title_termination_registrations,
        "invalid_indirect_target": protocol.stop_reason == "invalid-indirect-target",
        "debug_messages": {
            "content_suppressed": True,
            "count": protocol.debug_message_count,
            "bytes": protocol.debug_message_bytes,
        },
        "function_trace": {
            "limit": args.max_function_labels,
            "captured": len(protocol.function_labels),
            "dropped": protocol.dropped_function_labels,
            "retention": "most-recent",
            "labels": list(protocol.function_labels),
        },
    }
    write_report(report_path, report)
    stop_suffix = f" at {protocol.stop_import}" if protocol.stop_import else ""
    if protocol.stop_exception:
        stop_suffix = f" ({protocol.stop_exception})"
    print(
        f"Guarded host trace stopped: {protocol.stop_reason}{stop_suffix}; "
        f"{protocol.completed_function_entries} function entries."
    )
    print(f"Guest debug text suppressed; redacted report: {report_path}")
    return 0


def main() -> int:
    try:
        return run()
    except TraceError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
