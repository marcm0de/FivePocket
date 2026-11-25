from __future__ import annotations

import contextlib
import importlib.util
import io
import itertools
import json
from pathlib import Path
import shutil
import stat
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


MODULE_PATH = Path(__file__).resolve().parents[1] / "run_trace.py"
SPEC = importlib.util.spec_from_file_location("host_guest_trace_run", MODULE_PATH)
assert SPEC and SPEC.loader
trace = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = trace
SPEC.loader.exec_module(trace)


def make_bridge_report(chunk_count: int, guarded_count: int = 1) -> dict[str, object]:
    return {
        "schema_version": 2,
        "guest_execution_connected": False,
        "runnable_guest_support": False,
        "inputs": {
            "expected_object_count": chunk_count + 1,
            "discovered_object_count": chunk_count + 1,
            "expected_xbox_import_count": guarded_count + 2,
            "validated_xbox_import_count": guarded_count + 2,
        },
        "unimplemented_import_stops": {
            "count": guarded_count,
            "mode": "guarded-trace",
            "stop_primitive": "recomp_guest_execution_unimplemented_import",
        },
        "symbol_partition_validation": {
            "succeeded": True,
            "final_unresolved_xbox_imports": 0,
            "guard_reference_validated": True,
            "implemented_expected": 2,
            "implemented_defined": 2,
            "unimplemented_stops_expected": guarded_count,
            "unimplemented_stops_defined": guarded_count,
        },
        "strict_link": {"succeeded": True},
    }


SYNTHETIC_IMAGE_BASE = 0x40000000
SYNTHETIC_IMAGE_SIZE = 0x2000
SYNTHETIC_CODE_BASE = SYNTHETIC_IMAGE_BASE + 0x100
SYNTHETIC_CODE_SIZE = 0x1000
SYNTHETIC_SECURITY_IMAGE_SIZE = 0x3000
SYNTHETIC_IMPORT_SLOT = SYNTHETIC_IMAGE_BASE + 0x100
SYNTHETIC_ENTRY_POINT = SYNTHETIC_IMAGE_BASE + 0x80


def synthetic_xex() -> bytes:
    data = bytearray(0x500)
    header_size = 0x340
    security_offset = 0x50
    imports_offset = 0x240
    optional_headers = (
        (0x00010100, SYNTHETIC_ENTRY_POINT),
        (0x00010201, SYNTHETIC_IMAGE_BASE),
        (0x000103FF, imports_offset),
        (0x00020104, 0x300),
    )
    struct.pack_into(
        ">4sIIIII",
        data,
        0,
        b"XEX2",
        0,
        header_size,
        0,
        security_offset,
        len(optional_headers),
    )
    for index, optional_header in enumerate(optional_headers):
        struct.pack_into(">II", data, 24 + index * 8, *optional_header)
    struct.pack_into(">I", data, security_offset + 0x04, SYNTHETIC_SECURITY_IMAGE_SIZE)
    struct.pack_into(">I", data, security_offset + 0x110, SYNTHETIC_IMAGE_BASE)

    string_table = b"xboxkrnl.exe\0\0\0\0"
    library_size = 48
    total_size = 12 + len(string_table) + library_size
    struct.pack_into(">III", data, imports_offset, total_size, len(string_table), 1)
    strings_end = imports_offset + 12 + len(string_table)
    data[imports_offset + 12:strings_end] = string_table
    struct.pack_into(
        ">I20sIIIHHII",
        data,
        strings_end,
        library_size,
        bytes(20),
        0x01020304,
        0x01000000,
        0x01000000,
        0,
        2,
        SYNTHETIC_IMPORT_SLOT,
        SYNTHETIC_IMPORT_SLOT + 4,
    )
    struct.pack_into(
        ">IIII", data, 0x300, 64, SYNTHETIC_IMAGE_BASE + 0x500, 16, 12
    )
    return bytes(data)


def make_synthetic_repo(temporary: str, chunk_count: int = 2) -> tuple[Path, Path, Path, Path]:
    root = Path(temporary) / "repo"
    root.mkdir()
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    (root / ".gitignore").write_text("local-analysis/\n", encoding="utf-8")

    ppc = root / "local-analysis/generated/ppc"
    ppc.mkdir(parents=True)
    (ppc / "ppc_config.h").write_text(
        f"#define PPC_IMAGE_BASE 0x{SYNTHETIC_IMAGE_BASE:X}ull\n"
        f"#define PPC_IMAGE_SIZE 0x{SYNTHETIC_IMAGE_SIZE:X}ull\n"
        f"#define PPC_CODE_BASE 0x{SYNTHETIC_CODE_BASE:X}ull\n"
        f"#define PPC_CODE_SIZE 0x{SYNTHETIC_CODE_SIZE:X}ull\n",
        encoding="utf-8",
    )
    (ppc / "ppc_context.h").write_text(
        "#define PPC_FUNC_PROLOGUE() PPC_RUNTIME_CHECKPOINT();\n"
        "#ifndef PPC_READ_TIMEBASE\n#define PPC_READ_TIMEBASE() __rdtsc()\n#endif\n",
        encoding="utf-8",
    )
    (ppc / "ppc_recomp_shared.h").write_text(
        "PPC_EXTERN_FUNC(_xstart);\n", encoding="utf-8"
    )
    (ppc / "ppc_func_mapping.cpp").write_text(
        "#include \"ppc_recomp_shared.h\"\n\n"
        "PPCFuncMapping PPCFuncMappings[] = {\n"
        f"    {{ 0x{SYNTHETIC_CODE_BASE:X}, synthetic_first }},\n"
        f"    {{ 0x{SYNTHETIC_CODE_BASE + 4:X}, synthetic_second }},\n"
        "    { 0, nullptr }\n"
        "};\n",
        encoding="utf-8",
    )
    for index in range(chunk_count):
        (ppc / f"ppc_recomp.{index}.cpp").write_text(
            "// synthetic generated chunk\n", encoding="utf-8"
        )

    bridge = root / "local-analysis/bridge"
    bridge.mkdir(parents=True)
    for name in ("generated_guest_import_bridge.cpp", "generated_guest_import_bridge.h"):
        (bridge / name).write_text("// synthetic bridge\n", encoding="utf-8")
    (bridge / "remaining_xbox_import_guarded_stops.cpp").write_text(
        'recomp_guest_execution_unimplemented_import("__imp__Synthetic");\n',
        encoding="utf-8",
    )
    (bridge / "runtime-bridge-report.json").write_text(
        json.dumps(make_bridge_report(chunk_count)), encoding="utf-8"
    )

    image = root / "local-analysis/decoded.image.bin"
    image_bytes = bytearray(SYNTHETIC_IMAGE_SIZE)
    struct.pack_into(">I", image_bytes, 0x100, 0x00000193)
    struct.pack_into(">I", image_bytes, 0x104, 0x0000001B)
    image.write_bytes(image_bytes)
    (root / "local-analysis/default.xex").write_bytes(synthetic_xex())
    output = root / "local-analysis/trace-output"
    return root, ppc, bridge, image


def valid_protocol(*, labels: tuple[str, ...] = ("_xstart",)) -> str:
    lines = [
        "HOST_GUEST_TRACE_V1",
        "stop_reason=unimplemented-import",
        "completed_function_entries=2",
        "debug_message_count=1",
        "debug_message_bytes=17",
        "dropped_function_labels=0",
        "validated_mapping_count=2",
        "stop_import=__imp__Synthetic",
    ]
    lines.extend(f"function_label={label}" for label in labels)
    return "\n".join(lines) + "\n"


def invalid_indirect_protocol() -> str:
    return valid_protocol().replace(
        "stop_reason=unimplemented-import", "stop_reason=invalid-indirect-target"
    ).replace("stop_import=__imp__Synthetic", "stop_import=none")


class HostGuestTraceTests(unittest.TestCase):
    def test_native_display_output(self):
        storage={'completed_batches':1,'read_word':58,'physical_base':0x7900000,'byte_count':0x700000}
        gamma={'completed_batches':1,'frontbuffer_physical':0x1f2d8000}
        ring={'ring_bytes':65536}
        parse=lambda raw:trace.parse_native_display_output(raw,storage,gamma,ring)
        good='1,58,61,126899392,2,1,527040580,2811253964,527040576,19'
        self.assertIsNone(parse(None));self.assertEqual(parse(','.join(['0']*10))['completed_batches'],0)
        value=parse(good);self.assertEqual(value['display_updates_queued'],1)
        self.assertEqual(value['generated_game_frames'],0);self.assertFalse(value['retail_link_semantics_verified'])
        for field in (0,1,2,4,5):
            for bit in range(32):
                bad=good.split(',');bad[field]=str(int(bad[field])^(1<<bit))
                with self.assertRaises(trace.TraceError):parse(','.join(bad))
        for field,value in ((3,1),(3,0x78ffffc),(3,0x8000000),(6,1),(6,0x20000000),(6,0x7900000),
                (6,0x1f2d8000),(8,527040580)):
            bad=good.split(',');bad[field]=str(value)
            with self.assertRaises(trace.TraceError):parse(','.join(bad))
        for raw in ('',good+',0','0'+good[1:],good.replace('2811253964','4294967296')):
            with self.assertRaises(trace.TraceError):parse(raw)
        for args in ((None,gamma,ring),(storage,None,ring),(storage,gamma,None)):
            with self.assertRaises(trace.TraceError):trace.parse_native_display_output(good,*args)

    def test_native_post_output_color(self):
        storage={'completed_batches':1,'read_word':64};ring={'ring_bytes':65536}
        parse=lambda raw:trace.parse_native_post_output_color(raw,storage,ring)
        good='1,64,67,4,2,3686400,3686400,4,1,1'
        self.assertIsNone(parse(None));self.assertEqual(parse(','.join(['0']*10))['completed_batches'],0)
        self.assertEqual(parse(good)['native_fences'],5)
        self.assertEqual(parse(good)['frames_presented'],0)
        for field in range(10):
            for bit in range(32):
                bad=good.split(',');bad[field]=str(int(bad[field])^(1<<bit))
                with self.assertRaises(trace.TraceError):parse(','.join(bad))
        for raw in ('',good+',0','0,0,0,0,0,0,0,0,0,1','01'+good[1:],'-1'+good[1:]):
            with self.assertRaises(trace.TraceError):parse(raw)
        for s,r in ((None,ring),(storage,None),({'completed_batches':0,'read_word':64},ring),(storage,{'ring_bytes':268})):
            with self.assertRaises(trace.TraceError):trace.parse_native_post_output_color(good,s,r)

    def test_native_post_output_system_context(self):
        color={'completed_batches':1,'read_word':67};original={'issued_contexts':1,'stack_pointer':0x640AF820}
        parse=lambda raw:trace.parse_native_post_output_system_context(raw,color,original)
        good='1,1678440480,1678440688,1678440596,67,1'
        self.assertIsNone(parse(None));self.assertEqual(parse('0,0,0,0,0,0')['issued_contexts'],0)
        self.assertEqual(parse(good)['read_word'],67);self.assertFalse(parse(good)['swap_completed'])
        for field in range(6):
            for bit in range(32):
                bad=good.split(',');bad[field]=str(int(bad[field])^(1<<bit))
                with self.assertRaises(trace.TraceError):parse(','.join(bad))
        for raw in ('',good+',0','0,0,0,0,0,1','01'+good[1:],'-1'+good[1:]):
            with self.assertRaises(trace.TraceError):parse(raw)
        for c,o in ((None,original),(color,None),({'completed_batches':0,'read_word':67},original),(color,{'issued_contexts':0,'stack_pointer':0x640AF820})):
            with self.assertRaises(trace.TraceError):trace.parse_native_post_output_system_context(good,c,o)

    def test_native_post_output_swap_ticket(self):
        color={'completed_batches':1,'read_word':67};context={'issued_contexts':1,'read_word':67}
        original={'present_queued':True};storage={'completed_batches':1,'physical_base':0x7900000,'byte_count':0x700000}
        ring={'ring_read_word':67}
        parse=lambda raw:trace.parse_native_post_output_swap(raw,color,context,original,storage,ring)
        good='1,1,0,127004552,67,67,67'
        self.assertIsNone(parse(None));self.assertEqual(parse('0,0,0,0,0,0,0')['issued_commands'],0)
        evidence=parse(good);self.assertEqual(evidence['ticket'],2);self.assertTrue(evidence['pending'])
        self.assertFalse(evidence['gpu_executed']);self.assertEqual(evidence['frames_presented'],0)
        for field in (0,1,2,4,5,6):
            for bit in range(32):
                bad=good.split(',');bad[field]=str(int(bad[field])^(1<<bit))
                with self.assertRaises(trace.TraceError):parse(','.join(bad))
        for command in (0,0x7900001,0x78FFFFC,0x8000000-252,0x8000000,0xFFFFFFFF):
            with self.assertRaises(trace.TraceError):parse(good.replace('127004552',str(command)))
        for raw in ('',good+',0','0,0,0,0,0,0,1','01'+good[1:],'-1'+good[1:]):
            with self.assertRaises(trace.TraceError):parse(raw)
        dependencies=[color,context,original,storage,ring]
        for index in range(len(dependencies)):
            changed=dependencies[:];changed[index]=None
            with self.assertRaises(trace.TraceError):trace.parse_native_post_output_swap(good,*changed)

    def test_native_post_output_swap_storage(self):
        color={'completed_batches':1,'read_word':67};context={'issued_contexts':1,'read_word':67}
        storage={'completed_batches':1,'physical_base':0x7900000,'byte_count':0x700000};ring={'ring_read_word':70}
        parse=lambda raw:trace.parse_native_post_output_swap_storage(raw,color,context,storage,ring)
        good='1,67,70,126877696,7340032,16777216'
        self.assertIsNone(parse(None));self.assertEqual(parse('0,0,0,0,0,0')['completed_batches'],0)
        self.assertEqual(parse(good)['native_fences'],1);self.assertFalse(parse(good)['ticket_rewritten'])
        for field in range(6):
            for bit in range(32):
                bad=good.split(',');bad[field]=str(int(bad[field])^(1<<bit))
                with self.assertRaises(trace.TraceError):parse(','.join(bad))
        for raw in ('',good+',0','0,0,0,0,0,1','01'+good[1:],'-1'+good[1:]):
            with self.assertRaises(trace.TraceError):parse(raw)
        dependencies=[color,context,storage,ring]
        for index in range(len(dependencies)):
            changed=dependencies[:];changed[index]=None
            with self.assertRaises(trace.TraceError):trace.parse_native_post_output_swap_storage(good,*changed)

    def test_native_post_output_wrapped_swap(self):
        storage={'completed_batches':1,'physical_base':0x7900000,'byte_count':0x700000}
        ring={'ring_read_word':73,'ring_bytes':65536,'ring_physical':0x1F670040,'ring_virtual':0xBF670040,
            'wb_physical':0x1F6A007C,'identifier_virtual':0xBF6A0048}
        irq={'acknowledged':True,'worker':0x64003000}
        good=[1,70,73,0x791EF60,0x7568000,0x7568000,0xA7568000,3768320,1280,720,6,1,1,1000,1010,
            0x64006000,1,1,1,0,1,1,1,0,1,0x1F6A0088,500,0x1F6A008C,505,0x1F6A0044,0xA791F130,0x1F6A0040,23]
        parse=lambda fields:trace.parse_native_post_output_wrapped_swap(','.join(map(str,fields)),storage,ring,irq)
        self.assertIsNone(trace.parse_native_post_output_wrapped_swap(None,None,None,None))
        self.assertEqual(parse([0]*33)['completed_batches'],0)
        result=parse(good);self.assertEqual(result['display_updates_queued'],1)
        self.assertFalse(result['vblank_completed']);self.assertFalse(result['title_screen_verified'])
        self.assertEqual(result['guest_event_words_published'],4)
        for field in (0,1,2,8,9,10,11,12,16,17,18,19,20,21,22,23,24,26,28):
            for bit in range(32):
                bad=good[:];bad[field]^=1<<bit
                with self.assertRaises(trace.TraceError):parse(bad)
        for field,values in {3:[0,0x7900001,0x8000000-512],4:[0,0x7568001,0x7569000],
                5:[0,0x7569000,0x7568001],6:[0,0xA7568001,0xFFFFF000],7:[0,3768319,0xFFFFFFFF],
                13:[1011,2**64],14:[999,1000+8589934592],15:[0,0x64003000,0x64006001],
                25:[0x1F6A0089,0x1F6A0090],27:[0x1F6A0088,0x1F6A0090],
                29:[0x791EF60,0x1F670040,0x1F6A007C,0x1F6A0048,0x7568000,0x1F6A0088,0x20000000]}.items():
            for value in values:
                bad=good[:];bad[field]=value
                with self.assertRaises(trace.TraceError):parse(bad)
        for raw in ('','bad',','.join(map(str,good))+',0',','.join(map(str,good))[1:],'01'+','.join(map(str,good))[1:]):
            with self.assertRaises(trace.TraceError):trace.parse_native_post_output_wrapped_swap(raw,storage,ring,irq)
        for deps in ((None,ring,irq),(storage,None,irq),(storage,ring,None),(storage,ring,dict(irq,acknowledged=False))):
            with self.assertRaises(trace.TraceError):trace.parse_native_post_output_wrapped_swap(','.join(map(str,good)),*deps)

    def test_stopped_post_swap_request(self):
        context={'issued_contexts':1,'read_word':73};ring={'ring_read_word':73,'ring_virtual':0xBF670040,'ring_physical':0x1F670040}
        fields=[1,0xA793B130,0x0793B130,0xA7900000,0x700000,4,0xA71D0000,6,0,1280,720,0,
            0x8A000002,0xA71D0006,0x0059E4FF,0x1414,0,0x200]
        parse=lambda raw:trace.parse_stopped_post_swap_request(raw,context,ring,True)
        self.assertIsNone(parse(None));self.assertFalse(parse('[0]')['captured'])
        result=parse(json.dumps(fields));self.assertEqual(result['frontbuffer_virtual'],0xA71D0000)
        self.assertTrue(result['metadata_only']);self.assertFalse(result['pixels_decoded'])
        self.assertFalse(result['next_image_published']);self.assertFalse(result['next_swap_completed'])
        # Raw image fields are deliberately not a format/ownership/pixel proof.
        unknown=fields[:];unknown[6:]=[0xDEADBEEF]*12
        self.assertEqual(parse(json.dumps(unknown))['width'],0xDEADBEEF)
        for raw in ('','bad','[true]','{}','[]','[0,0]',json.dumps(fields+[0])):
            with self.assertRaises(trace.TraceError):parse(raw)
        for index,value in ((0,2),(1,0),(2,fields[2]+4),(3,fields[1]+4),(4,0),(5,2),(6,-1),(17,2**32)):
            bad=fields[:];bad[index]=value
            with self.assertRaises(trace.TraceError):parse(json.dumps(bad))
        for ctx,rg,stop in ((None,ring,True),(context,None,True),(dict(context,read_word=70),ring,True),(context,ring,False)):
            with self.assertRaises(trace.TraceError):trace.parse_stopped_post_swap_request(json.dumps(fields),ctx,rg,stop)

    def test_native_deferred_swap_request(self):
        context=dict(issued_contexts=1,read_word=73)
        ring=dict(ring_read_word=73,ring_virtual=0xBF670040,ring_physical=0x1F670040)
        completion=dict(completed_batches=1,read_word=73,child_physical=0x0791EF60,image_physical=0x07568000)
        fields=[1,3,0xA793B130,0x0793B130,0xA7900000,0x700000,0x20000404,0xA71D0000,6,0,1280,720,0,
            0x8A000002,0xA71D0006,0x0059E4FF,0x1414,0,0x200,0x071D0000,0xA71D0000,3768320,0x404,1,73]
        parse=lambda raw:trace.parse_native_deferred_swap_request(raw,context,ring,completion,0x1F2D8000)
        self.assertIsNone(parse(None));self.assertFalse(parse('[0]')['issued'])
        result=parse(json.dumps(fields));self.assertTrue(result['issued']);self.assertTrue(result['pending'])
        self.assertEqual(result['ticket'],3);self.assertEqual(result['command_bytes_written'],256)
        self.assertEqual(result['image_owner_protection'],0x404)
        self.assertFalse(result['image_rendering_completed']);self.assertFalse(result['frame_presented'])
        for raw in ('','bad','[true]','{}','[]','[0,0]',json.dumps(fields+[0])):
            with self.assertRaises(trace.TraceError):parse(raw)
        for index in (0,1,8,9,10,11,13,14,15,16,17,18,19,20,23,24):
            for bit in range(32):
                bad=fields[:];bad[index]^=1<<bit
                with self.assertRaises(trace.TraceError):parse(json.dumps(bad))
        for index,value in ((2,fields[2]+4),(3,fields[3]-4),(4,fields[2]+4),(5,0),(6,2),(21,4),(21,0xFFFFFFFF),(22,0)):
            bad=fields[:];bad[index]=value
            with self.assertRaises(trace.TraceError):parse(json.dumps(bad))
        for old in (0x1F2D8000,0x07568000):
            bad=fields[:];bad[7]=bad[20]=old+0xA0000000;bad[14]=bad[7]|6;bad[19]=old
            with self.assertRaises(trace.TraceError):parse(json.dumps(bad))
        for ctx,rg,done in ((None,ring,completion),(context,None,completion),(context,ring,None),
                (dict(context,read_word=70),ring,completion),(context,ring,dict(completion,read_word=70)),
                (context,ring,dict(completion,child_physical=fields[3]-40))):
            with self.assertRaises(trace.TraceError):trace.parse_native_deferred_swap_request(json.dumps(fields),ctx,rg,done)

    def test_stopped_deferred_swap_interrupt(self):
        prefix=dict(completed=True,previous_word=76,pending_write_word=79,prefix_words=2778)
        request=dict(pending=True,image_physical=0x700000)
        fields=[1,76,79,2778,0x30000,0x836B4B98,0x700208,0x64000000,0x836B1768,50000000]+list(range(14))+list(range(32))+list(range(8))+list(range(6))
        parse=lambda value:trace.parse_stopped_deferred_swap_interrupt(json.dumps(value),prefix,request)
        result=parse(fields);self.assertTrue(result['captured']);self.assertFalse(result['queue_profile_admitted'])
        self.assertFalse(result['guest_writes']);self.assertEqual(result['queued_surfaces'],list(range(32)))
        self.assertEqual(result['pacing_record'],list(range(6)));self.assertFalse(parse([0,4])['captured'])
        for index,value in ((0,0),(1,73),(2,76),(3,2779),(4,1),(5,0),(6,0x800208),(7,0xFFFFF000),(8,1),(9,1<<64),(30,True),(69,-1)):
            bad=fields.copy();bad[index]=value
            with self.subTest(index=index,value=value),self.assertRaises(trace.TraceError):parse(bad)
        for bad in ([],fields+[0],fields[:-1],[0,0],[0,25],{},None):
            with self.assertRaises(trace.TraceError):parse(bad)
        for p,r in ((None,request),(dict(prefix,completed=False),request),(prefix,None),(prefix,dict(request,pending=False))):
            with self.assertRaises(trace.TraceError):trace.parse_stopped_deferred_swap_interrupt(json.dumps(fields),p,r)
        self.assertIsNone(trace.parse_stopped_deferred_swap_interrupt(None,None,None))

    def test_live_deferred_color_prefix(self):
        storage=dict(completed_batches=1,read_word=76)
        request=dict(issued=True,pending=True)
        ring=dict(ring_read_word=76,ring_bytes=4096)
        parse=lambda raw:trace.parse_native_deferred_color_prefix(raw,storage,request,ring)
        fields=[1,0,0,1,76,79,2778,12,3686400,4,1,1,76]
        raw=','.join(map(str,fields));result=parse(raw)
        self.assertTrue(result['copy_published']);self.assertTrue(result['prefix_state_committed'])
        self.assertFalse(result['surface_tail_completed']);self.assertFalse(result['frame_presented'])
        self.assertEqual(result['consumed_ring_words'],0)
        self.assertIsNone(parse(None))
        self.assertFalse(parse('0,2,0,0,0,0,0,0,0,0,0,0,76')['attempted'])
        self.assertTrue(parse('1,17,0,0,0,0,0,0,0,0,0,1,76')['repeat_rejected'])
        for index,value in ((0,0),(1,1),(2,1),(3,2),(4,73),(5,76),(5,80),(6,0),(6,3968),(7,0),(7,49),
                (8,0),(9,3),(10,0),(11,0),(12,79)):
            bad=fields.copy();bad[index]=value
            with self.subTest(index=index,value=value),self.assertRaises(trace.TraceError):parse(','.join(map(str,bad)))
        for raw in ('',raw+',0','0'+raw,raw+'\n','1,0,0,0,0,0,0,0,0,0,0,1,76'):
            with self.assertRaises(trace.TraceError):parse(raw)
        for backing,issued,current in ((None,request,ring),(storage,None,ring),(storage,request,None),
                (dict(completed_batches=0,read_word=76),request,ring),(storage,dict(issued=True,pending=False),ring)):
            with self.assertRaises(trace.TraceError):trace.parse_native_deferred_color_prefix(','.join(map(str,fields)),backing,issued,current)

    def test_native_deferred_swap_storage(self):
        storage=dict(completed_batches=1,physical_base=0x7900000,byte_count=0x700000)
        ring=dict(ring_read_word=76,ring_bytes=65536)
        fields=[1,73,76,0x7900000,0x700000,0x01000000]
        parse=lambda raw:trace.parse_native_deferred_swap_storage(raw,storage,ring)
        self.assertIsNone(parse(None));self.assertEqual(parse('0,0,0,0,0,0')['native_fences'],0)
        self.assertEqual(parse(','.join(map(str,fields)))['native_fences'],1)
        self.assertFalse(parse(','.join(map(str,fields)))['image_rendering_completed'])
        for index in range(6):
            for bit in range(32):
                bad=fields[:];bad[index]^=1<<bit
                with self.assertRaises(trace.TraceError):parse(','.join(map(str,bad)))
        for raw in ('','bad','0,0,0,0,0,1','2,73,76,126877696,7340032,16777216','1,73,76,126877696,7340032,4294967296'):
            with self.assertRaises(trace.TraceError):parse(raw)
        for backing,current in ((None,ring),(storage,None),(storage,dict(ring,ring_read_word=73))):
            with self.assertRaises(trace.TraceError):trace.parse_native_deferred_swap_storage(','.join(map(str,fields)),backing,current)

    def test_native_post_output_storage(self):
        output={'completed_batches':1,'read_word':61,'display_updates_queued':1}
        storage={'completed_batches':1,'physical_base':0x7900000,'byte_count':0x700000}
        ring={'ring_bytes':65536}
        parse=lambda raw:trace.parse_native_post_output_storage(raw,output,storage,ring)
        self.assertIsNone(parse(None));self.assertEqual(parse('0,0,0,0,0,0,0,0')['completed_batches'],0)
        good='1,61,61,64,126877696,7340032,16777216,1'
        self.assertEqual(parse(good)['profile'],'native-post-output-command-storage-fence-v1')
        for count in (1,2,256):
            record=f'{count},61,{61+(count-1)*3},{61+count*3},126877696,7340032,16777216,{count}'
            self.assertEqual(parse(record)['native_fences'],count)
        for field in range(8):
            for bit in range(32):
                bad=good.split(',');bad[field]=str(int(bad[field])^(1<<bit))
                with self.assertRaises(trace.TraceError):parse(','.join(bad))
        for raw in ('',good+',0','0,0,0,0,0,0,0,1','257,61,829,832,126877696,7340032,16777216,257'):
            with self.assertRaises(trace.TraceError):parse(raw)
        for args in ((None,storage,ring),(output,None,ring),(output,storage,None)):
            with self.assertRaises(trace.TraceError):trace.parse_native_post_output_storage(good,*args)
        output['display_updates_queued']=0
        with self.assertRaises(trace.TraceError):parse(good)

    def test_native_post_display_storage(self):
        gamma={'completed_batches':1,'read_word':55,'display_updates_queued':1}
        storage={'completed_batches':1,'physical_base':0x7900000,'byte_count':0x700000}
        ring={'ring_bytes':65536}
        parse=lambda raw:trace.parse_native_post_display_storage(raw,gamma,storage,ring)
        self.assertIsNone(parse(None));self.assertEqual(parse('0,0,0,0,0,0,0,0')['completed_batches'],0)
        good='1,55,55,58,126877696,7340032,16777216,1'
        self.assertEqual(parse(good)['profile'],'native-post-display-command-storage-fence-v1')
        for count in (1,2,256):
            record=f'{count},55,{55+(count-1)*3},{55+count*3},126877696,7340032,16777216,{count}'
            self.assertEqual(parse(record)['native_fences'],count)
        for field in range(8):
            for bit in range(32):
                bad=good.split(',');bad[field]=str(int(bad[field])^(1<<bit))
                with self.assertRaises(trace.TraceError):parse(','.join(bad))
        for raw in ('',good+',0','0,0,0,0,0,0,0,1','257,55,823,826,126877696,7340032,16777216,257'):
            with self.assertRaises(trace.TraceError):parse(raw)
        for args in ((None,storage,ring),(gamma,None,ring),(gamma,storage,None)):
            with self.assertRaises(trace.TraceError):trace.parse_native_post_display_storage(good,*args)
        gamma['display_updates_queued']=0
        with self.assertRaises(trace.TraceError):parse(good)

    def test_native_display_gamma_completion(self):
        storage={'completed_batches':1,'read_word':52,'physical_base':0x7900000,'byte_count':0x700000}
        ring={'ring_bytes':65536}
        parse=lambda raw:trace.parse_native_display_gamma(raw,storage,ring)
        self.assertIsNone(parse(None));self.assertEqual(parse(','.join(['0']*11))['display_updates_queued'],0)
        good='1,52,55,126890016,523075584,2,1,527040580,2811253880,527040576,17'
        value=parse(good);self.assertEqual(value['lookup_samples'],256)
        self.assertEqual(value['generated_game_frames'],0);self.assertFalse(value['retail_pending_bit_emulated'])
        for field in (0,1,2,5,6):
            for bit in range(32):
                bad=good.split(',');bad[field]=str(int(bad[field])^(1<<bit))
                with self.assertRaises(trace.TraceError):parse(','.join(bad))
        for field,value in ((3,1),(3,0x78ffffc),(3,0x8000000),(4,1),(4,0x20000000),(7,1),
                (7,0x20000000),(7,126890016),(7,523075584),(9,527040580)):
            bad=good.split(',');bad[field]=str(value)
            with self.assertRaises(trace.TraceError):parse(','.join(bad))
        for raw in ('',good+',0',good.replace('2811253880','4294967296'),'0'+good[1:]):
            with self.assertRaises(trace.TraceError):parse(raw)
        for args in ((None,ring),(storage,None)):
            with self.assertRaises(trace.TraceError):trace.parse_native_display_gamma(good,*args)

    def test_native_post_scaler_storage(self):
        completion={'configured':True,'read_word':49}
        storage={'completed_batches':1,'physical_base':0x7900000,'byte_count':0x700000}
        ring={'ring_bytes':65536}
        parse=lambda raw:trace.parse_native_post_scaler_storage(raw,completion,storage,ring)
        self.assertIsNone(parse(None))
        self.assertEqual(parse('0,0,0,0,0,0,0,0')['completed_batches'],0)
        good='1,49,49,52,126877696,7340032,16777216,1'
        self.assertEqual(parse(good)['native_fences'],1)
        self.assertFalse(parse(good)['guest_payload_uploaded'])
        self.assertFalse(parse(good)['guest_status_bits_written'])
        for count in (2,16,256):
            record=f'{count},49,{49+(count-1)*3},{49+count*3},126877696,7340032,16777216,{count}'
            self.assertEqual(parse(record)['consumed_ring_words'],count*3)
        for field in range(8):
            for bit in range(32):
                bad=good.split(',');bad[field]=str(int(bad[field])^(1<<bit))
                with self.assertRaises(trace.TraceError):parse(','.join(bad))
        for raw in ('',good+',0','0,0,0,0,0,0,0,1','257,49,817,820,126877696,7340032,16777216,257',
                    '1,49,49,52,4294967296,7340032,16777216,1'):
            with self.assertRaises(trace.TraceError):parse(raw)
        for args in ((None,storage,ring),(completion,None,ring),(completion,storage,None)):
            with self.assertRaises(trace.TraceError):trace.parse_native_post_scaler_storage(good,*args)
        completion['configured']=False
        with self.assertRaises(trace.TraceError):parse(good)

    def test_native_scaler_completion(self):
        interrupt={'acknowledged':True};storage={'completed_batches':1,'read_word':46};ring={'ring_bytes':65536}
        parse=lambda raw:trace.parse_native_scaler_completion(raw,interrupt,storage,ring)
        self.assertIsNone(parse(None));self.assertFalse(parse('0,0,0,0,0,0,0,0')['configured'])
        good='1,46,49,3,527040580,2811244496,527040576,15'
        value=parse(good);self.assertTrue(value['configured']);self.assertEqual(value['consumed_ring_words'],3)
        self.assertEqual(value['frames_presented'],0);self.assertFalse(value['retail_pending_bit_emulated'])
        for field,number in ((0,2),(1,43),(2,48),(3,2),(4,1),(4,0x20000000),(6,527040580)):
            bad=good.split(',');bad[field]=str(number)
            with self.assertRaises(trace.TraceError):parse(','.join(bad))
        for raw in ('',good+',0','0,0,0,1,0,0,0,0',good.replace('527040580','4294967296')):
            with self.assertRaises(trace.TraceError):parse(raw)
        for args in ((None,storage,ring),(interrupt,None,ring),(interrupt,storage,None)):
            with self.assertRaises(trace.TraceError):trace.parse_native_scaler_completion(good,*args)
        interrupt['acknowledged']=False
        with self.assertRaises(trace.TraceError):parse(good)

    def test_native_scaler_pacing_preflight(self):
        parse=lambda raw:trace.parse_native_scaler_pacing(raw,{'scope_attempted':True})
        self.assertIsNone(parse(None))
        self.assertFalse(parse('0,0,0,0,0,0')['observed'])
        value=parse('1,0,0,2200000000,520,50000000')
        self.assertEqual(value['optional_callback'],2200000000)
        self.assertFalse(value['callback_execution_proven'])
        self.assertEqual(parse('1,4294967295,4294967295,4294967295,4294967295,18446744073709551615')['count'],0xFFFFFFFF)
        for raw in ('','0,1,0,0,0,0','2,0,0,0,0,0','1,4294967296,0,0,0,0',
                    '1,0,0,0,0,18446744073709551616','01,0,0,0,0,0','1,0,0,0,0','1,0,0,0,0,0,0'):
            with self.assertRaises(trace.TraceError):parse(raw)
        for interrupt in (None,{'scope_attempted':False}):
            with self.assertRaises(trace.TraceError):trace.parse_native_scaler_pacing('1,0,0,0,0,0',interrupt)

    def test_native_scaler_interrupt_metadata(self):
        parse=lambda raw:trace.parse_native_scaler_interrupt(raw,{'issued_commands':1})
        self.assertIsNone(parse(None))
        self.assertFalse(parse('0,0,0,0,0,0,0,0,0,0,0,0')['acknowledged'])
        for reason in range(8,18):
            self.assertEqual(parse('0,0,0,0,0,0,0,0,0,0,0,'+str(reason))['rejection'],reason)
        good='1,1,1,1677733888,0,1,1,1,1,1,1,0'
        value=parse(good)
        self.assertTrue(value['acknowledged'])
        self.assertFalse(value['scaler_executed'])
        self.assertEqual(value['consumed_ring_words'],0)
        self.assertFalse(value['title_screen_verified'])
        for raw in ('1,0,0,1677733888,2,0,0,1,1,0,0,0',
                    '1,0,0,1677733888,0,0,0,0,0,0,0,4'):
            self.assertFalse(parse(raw)['acknowledged'])
        for field,value in ((0,0),(1,0),(2,0),(3,0),(3,1),(4,1),(5,0),(6,0),(7,0),(8,0),(9,0),(11,1),(11,8)):
            bad=good.split(',');bad[field]=str(value)
            with self.assertRaises(trace.TraceError):parse(','.join(bad))
        for raw in ('',good+',0','01'+good[1:],good.replace('1677733888','4294967296'),'0,0,0,0,0,0,0,0,0,0,0,18'):
            with self.assertRaises(trace.TraceError):parse(raw)
        with self.assertRaises(trace.TraceError):trace.parse_native_scaler_interrupt(good,None)

    def test_rejected_indirect_call_records_only_bounded_addresses(self):
        base = invalid_indirect_protocol()
        good = trace.parse_trace_protocol(base+'invalid_indirect_call=0,4294967295,1\n',1,10,2)
        self.assertEqual(good.invalid_indirect_call,{'target':0,'link_register':0xFFFFFFFF,'stack_pointer':1})
        for raw in ('-1,0,0','4294967296,0,0','00,0,0','0,0','0,0,0,0','x,0,0'):
            with self.assertRaises(trace.TraceError):
                trace.parse_trace_protocol(base+'invalid_indirect_call='+raw+'\n',1,10,2)
        with self.assertRaises(trace.TraceError):
            trace.parse_trace_protocol(valid_protocol()+'invalid_indirect_call=1,2,3\n',1,10,2)

    def test_timestamp_access_requires_explicit_bundle_range(self):
        base = invalid_indirect_protocol().replace('invalid-indirect-target', 'unsupported-scalar-access')
        fields = 'scalar_address=16384\nscalar_width=4\nscalar_value=0\nscalar_write=0\nscalar_region=4\n'
        self.assertEqual(trace.parse_trace_protocol(base + fields, 1, 10, 2,
            uptime_bundle_base=16384).scalar_access['region'], 4)
        for bundle in (None, 16388, 16360):
            with self.assertRaises(trace.TraceError):
                trace.parse_trace_protocol(base + fields, 1, 10, 2, uptime_bundle_base=bundle)

    def test_data_import_scalar_guard_requires_parser_confirmed_slot(self):
        base = invalid_indirect_protocol().replace('invalid-indirect-target', 'unsupported-scalar-access')
        fields = 'scalar_address=16384\nscalar_width=4\nscalar_value=0\nscalar_write=0\nscalar_region=3\n'
        self.assertEqual(trace.parse_trace_protocol(base + fields, 1, 10, 2,
            guarded_data_slots=(16384,)).scalar_access['region'], 3)
        for slots in ((), (16388,), (16380,)):
            with self.assertRaises(trace.TraceError):
                trace.parse_trace_protocol(base + fields, 1, 10, 2, guarded_data_slots=slots)

    def test_graphics_ring_protocol(self):
        fields = {"graphics_initialized": 1, "graphics_ring_physical": 0,
                  "graphics_ring_virtual": 0xA0000000, "graphics_ring_bytes": 65536,
                  "graphics_ring_read_word": 0, "graphics_wb_physical": 0x10000,
                  "graphics_wb_virtual": 0xA0010000, "graphics_wb_exponent": 6}
        def parse(data):
            return trace.parse_trace_protocol(valid_protocol() + ''.join(f'{k}={v}\n' for k, v in data.items()), 1, 10, 2)
        self.assertEqual(parse(fields).graphics_ring["ring_bytes"], 65536)
        self.assertEqual(parse(dict(fields, graphics_identifier_virtual=0xA0010008)).graphics_ring["identifier_virtual"], 0xA0010008)
        for invalid in (0, 1, 0xA0000000, 0xA0010000, 0x100000000):
            with self.assertRaises(trace.TraceError):
                parse(dict(fields, graphics_identifier_virtual=invalid))
        self.assertEqual(parse({k: v for k, v in fields.items() if not k.startswith('graphics_wb_')}).graphics_ring["ring_physical"], 0)
        for key in fields:
            with self.subTest(missing=key), self.assertRaises(trace.TraceError):
                parse({k: v for k, v in fields.items() if k != key})
        for key, value in [("graphics_initialized", 0), ("graphics_ring_bytes", 7),
                           ("graphics_ring_bytes", 24), ("graphics_ring_bytes", 0x2000000),
                           ("graphics_ring_read_word", 16384), ("graphics_ring_physical", 4),
                           ("graphics_ring_virtual", 0xFFFF0008), ("graphics_wb_exponent", 20),
                           ("graphics_wb_physical", 4), ("graphics_wb_virtual", 1),
                           ("graphics_wb_physical", 0x100000000), ("graphics_ring_bytes", '065536')]:
            with self.subTest(key=key, value=value), self.assertRaises(trace.TraceError):
                parse(dict(fields, **{key: value}))

    def test_experimental_gpu_clock_evidence(self):
        def parse(value, gpu=1):
            fields = f'experimental_gpu_clock_500mhz_reads={value}\n'
            if gpu is not None:
                fields += f'graphics_initialized={gpu}\n'
            return trace.parse_trace_protocol(valid_protocol() + fields, 1, 10, 2)
        self.assertIsNone(trace.parse_trace_protocol(valid_protocol(), 1, 10, 2).experimental_gpu_clock_500mhz_reads)
        for count in (0, 1, 0xFFFFFFFFFFFFFFFF):
            self.assertEqual(parse(count).experimental_gpu_clock_500mhz_reads, count)
        self.assertEqual(parse(0, 0).experimental_gpu_clock_500mhz_reads, 0)
        for value, gpu in ((1, 0), (0, None), ('01', 1), ('-1', 1), ('bad', 1), (1 << 64, 1)):
            with self.subTest(value=value, gpu=gpu), self.assertRaises(trace.TraceError):
                parse(value, gpu)

    def test_native_initial_coherency_is_explicit_and_fenced(self):
        base = valid_protocol() + 'graphics_initialized=1\nnative_display_dimensions=1280,720,1280,720\n'
        base += ('graphics_ring_physical=0\ngraphics_ring_virtual=2684354560\ngraphics_ring_bytes=65536\n'
                 'graphics_ring_read_word=22\ngraphics_wb_physical=65536\ngraphics_wb_virtual=2684420096\ngraphics_wb_exponent=7\n')
        def parse(raw='1,0,4096,16777216', prefix=base):
            return trace.parse_trace_protocol(prefix + 'native_initial_coherency=' + raw + '\n',1,10,2).native_initial_coherency
        result = parse()
        self.assertEqual(result['consumed_ring_words'],22)
        self.assertTrue(result['fence_waited'])
        self.assertFalse(result['range_uploaded'])
        self.assertFalse(parse('0,0,0,0')['completed'])
        for raw in ('1,0,0,16777216','1,1,4096,16777216','1,0,4096,33554432','1,536870912,4096,16777216',
                    '0,1,0,0','2,0,4096,16777216','01,0,4096,16777216','bad'):
            with self.assertRaises(trace.TraceError): parse(raw)
        for before, after in (('graphics_ring_read_word=22','graphics_ring_read_word=0'),
                              ('native_display_dimensions=1280,720,1280,720\n',''),
                              ('graphics_initialized=1','graphics_initialized=0')):
            with self.assertRaises(trace.TraceError): parse(prefix=base.replace(before,after))

    def test_native_memory_statistics_are_explicit_independent_arena_accounting(self):
        def parse(raw):
            return trace.parse_trace_protocol(valid_protocol() + 'native_memory_statistics=' + raw + '\n',
                                              1, 10, 2).native_memory_statistics
        state = parse('131072,40000,67108864,65536,91072,4,131071')
        self.assertEqual(state['committed_virtual_pages'], 4)
        self.assertEqual(state['reserved_virtual_bytes'], 65536)
        self.assertFalse(state['console_ram_equivalence_verified'])
        self.assertFalse(state['host_residency_measured'])
        self.assertEqual(parse('16,16,4096,0,0,0,31')['highest_physical_page'], 31)
        for bad in ('0,0,4096,0,0,0,0', '16,17,4096,0,0,0,15', '16,15,4096,0,0,0,15',
                    '16,16,4096,8192,0,0,15', '16,16,4096,0,0,1,15', '16,16,4097,0,0,0,15',
                    '16,16,4096,1,0,0,15', '16,16,0,0,0,0,15', '16,16,4096,0,0,0,14',
                    '16,16,4096,0,0,0,1048576', '16,16,4294967296,0,0,0,15',
                    '016,16,4096,0,0,0,15', '-1,16,4096,0,0,0,15', '16,16,4096,0,0,0',
                    '16,16,4096,0,0,0,15,0', 'bad'):
            with self.subTest(raw=bad), self.assertRaises(trace.TraceError):
                parse(bad)

    def test_xex_image_page_metadata_is_bounded_and_explicit(self):
        def parse(raw):
            return trace.parse_trace_protocol(valid_protocol() + 'xex_image_protections=' + raw + '\n',
                                              1, 10, 2).xex_image_protections
        image = parse('2181038080,31195136')
        self.assertEqual(image['page_bytes'], 65536)
        self.assertFalse(image['host_mprotect_enforced'])
        self.assertFalse(image['cryptographic_authenticity_verified'])
        for bad in ('2181038080,0', '2181038080,1', '2181038081,65536', '0,65536',
                    '2415919104,65536', '2147483648,268500992', '02181038080,65536',
                    '2181038080,65536,0', 'bad'):
            with self.subTest(raw=bad), self.assertRaises(trace.TraceError):
                parse(bad)

    def test_experimental_native_initialization_requires_complete_evidence(self):
        base = valid_protocol() + ('graphics_initialized=1\nnative_display_dimensions=1280,720,1280,720\n'
            'graphics_ring_physical=0\ngraphics_ring_virtual=2684354560\ngraphics_ring_bytes=65536\n'
            'graphics_ring_read_word=25\ngraphics_wb_physical=65536\ngraphics_wb_virtual=2684420096\ngraphics_wb_exponent=7\n'
            'native_initial_coherency=1,0,4096,16777216\n')
        def parse(raw='1,25,24,7,44', prefix=base):
            return trace.parse_trace_protocol(prefix+'experimental_native_initialization='+raw+'\n',1,10,2)
        result = parse()
        self.assertTrue(result.experimental_native_initialization['completed'])
        self.assertFalse(result.experimental_native_initialization['hardware_equivalence_verified'])
        self.assertEqual(result.native_initial_coherency['consumed_ring_words'],22)
        policy_base = base+'experimental_native_initialization=1,25,24,7,44\n'
        policy = trace.parse_trace_protocol(policy_base+'native_edram_training_policy=1,1\n',1,10,2)
        self.assertEqual(policy.native_edram_training_policy['no_guest_command_queries'],1)
        self.assertFalse(policy.native_edram_training_policy['xbox_hardware_training_performed'])
        for bad in ('0,1','1,2','-1,0','01,0','4294967296,0','1,1,1,1','0,0,1','1,0,1','bad'):
            with self.subTest(policy=bad), self.assertRaises(trace.TraceError):
                trace.parse_trace_protocol(policy_base+'native_edram_training_policy='+bad+'\n',1,10,2)
        with self.assertRaises(trace.TraceError):
            trace.parse_trace_protocol(base+'native_edram_training_policy=1,1\n',1,10,2)
        status = trace.parse_trace_protocol(policy_base+'native_edram_training_policy=1,1,2\n',1,10,2)
        self.assertEqual(status.native_edram_training_policy['status_queries'],2)
        legacy_extended=trace.parse_trace_protocol(policy_base+'native_edram_training_policy=1,1,2,0\n',1,10,2)
        self.assertEqual(legacy_extended.native_edram_training_policy['direct_no_command_queries'],0)
        cache_base=policy_base.replace('graphics_ring_read_word=25','graphics_ring_read_word=28')
        cache=trace.parse_trace_protocol(cache_base+'native_empty_cache_coherency=1,28,0,4096,16777216\n',1,10,2)
        self.assertEqual(cache.native_empty_cache_coherency['batches'],1)
        self.assertEqual(cache.experimental_native_initialization['read_word'],25)
        self.assertEqual(cache.native_initial_coherency['consumed_ring_words'],22)
        for bad in ('1,25,0,4096,16777216','2,28,0,4096,16777216','0,28,0,4096,16777216',
                    '1,28,1,4096,16777216','1,28,0,0,16777216','1,28,0,4096,0','1,28,536870912,4096,16777216',
                    '01,28,0,4096,16777216','bad'):
            with self.subTest(cache=bad), self.assertRaises(trace.TraceError):
                trace.parse_trace_protocol(cache_base+'native_empty_cache_coherency='+bad+'\n',1,10,2)
        for prefix in (base,valid_protocol()):
            with self.assertRaises(trace.TraceError):
                trace.parse_trace_protocol(prefix+'native_empty_cache_coherency=0,0,0,0,0\n',1,10,2)
        idle=trace.parse_trace_protocol(policy_base+'native_empty_cache_coherency=0,0,0,0,0\n',1,10,2)
        self.assertFalse(idle.native_empty_cache_coherency['fence_waited'])
        for raw in ('1,22,24,7,44','1,26,24,7,44','1,25,0,7,44','1,25,65,7,44',
                    '1,25,24,0,44','1,25,24,65,44','1,25,24,7,1025','0,25,24,7,44','bad','01,25,24,7,44'):
            with self.subTest(raw=raw), self.assertRaises(trace.TraceError): parse(raw)
        for before, after in (('native_initial_coherency=1,0,4096,16777216\n',''),
                             ('native_initial_coherency=1,0,4096,16777216','native_initial_coherency=0,0,0,0'),
                             ('graphics_initialized=1','graphics_initialized=0'),
                             ('native_display_dimensions=1280,720,1280,720\n','')):
            with self.assertRaises(trace.TraceError): parse(prefix=base.replace(before,after))
        self.assertFalse(parse('0,0,0,0,0',base.replace('read_word=25','read_word=22')).experimental_native_initialization['completed'])

    def test_experimental_idle_cache_pair_keeps_uncertainty_explicit(self):
        base = valid_protocol() + ('workers_created=1\nworkers_entered=1\ncontext_switches=2\nwait_operations=3\n'
            'graphics_initialized=1\nnative_display_dimensions=1280,720,1280,720\n'
            'graphics_ring_physical=0\ngraphics_ring_virtual=2684354560\ngraphics_ring_bytes=65536\n'
            'graphics_wb_physical=65536\ngraphics_wb_virtual=2684420096\ngraphics_wb_exponent=7\n'
            'native_initial_coherency=1,0,4096,16777216\nexperimental_native_initialization=1,25,24,7,44\n'
            'graphics_ring_read_word=28\nnative_empty_cache_coherency=0,0,0,0,0\nnative_state_batches=1,28,0,3,0\n')
        field='experimental_idle_cache_pairs=1\n'
        data=trace.parse_trace_protocol(base+field,1,10,2,cooperative=True).experimental_idle_cache_pairs
        self.assertEqual(data['completed_batches'],1)
        self.assertFalse(data['hardware_register_semantics_verified'])
        self.assertFalse(data['frame_presented'])
        for value in ('-1','01','1,0','2','18446744073709551616'):
            with self.assertRaises(trace.TraceError):
                trace.parse_trace_protocol(base+'experimental_idle_cache_pairs='+value+'\n',1,10,2,cooperative=True)
        for invalid in (base+field+field,base.replace('native_state_batches=1,28,0,3,0\n','')+field):
            with self.assertRaises(trace.TraceError):trace.parse_trace_protocol(invalid,1,10,2,cooperative=True)

    def test_retained_bin_state_does_not_invent_unknown_reset_words(self):
        base=valid_protocol()+('graphics_initialized=1\nnative_display_dimensions=1280,720,1280,720\n'
            'graphics_ring_physical=0\ngraphics_ring_virtual=2684354560\ngraphics_ring_bytes=65536\n'
            'graphics_wb_physical=65536\ngraphics_wb_virtual=2684420096\ngraphics_wb_exponent=7\n'
            'native_initial_coherency=1,0,4096,16777216\nexperimental_native_initialization=1,25,24,7,44\n'
            'graphics_ring_read_word=28\nnative_state_batches=1,28,0,4,0\n')
        def parse(raw,prefix=base):return trace.parse_trace_protocol(prefix+'native_bin_state='+raw+'\n',1,10,2).native_bin_state
        self.assertEqual(parse('1,2,3,4,15')['mask_words'],[1,2])
        self.assertEqual(parse('1,2,3,4,15')['select_words'],[3,4])
        self.assertEqual(parse('4294967295,0,0,0,1')['known_words'],1)
        unknown=parse('0,0,0,0,0')
        self.assertFalse(unknown['unknown_words_are_reset_values']);self.assertFalse(unknown['predicates_executed'])
        for raw in ('1,0,0,0,0','0,1,0,0,1','0,0,0,0,16','4294967296,0,0,0,1',
            '01,0,0,0,1','-1,0,0,0,1','0,0,0,0','0,0,0,0,0,0'):
            with self.assertRaises(trace.TraceError):parse(raw)
        for prefix in (valid_protocol(),base.replace('0,4,0\n','0,3,0\n'),
            base.replace('native_state_batches=1,28,0,4,0\n','')):
            with self.assertRaises(trace.TraceError):parse('1,2,3,4,15',prefix)

    def test_native_state_batch_interleaved_history(self):
        base = valid_protocol() + ('graphics_initialized=1\nnative_display_dimensions=1280,720,1280,720\n'
            'graphics_ring_physical=0\ngraphics_ring_virtual=2684354560\ngraphics_ring_bytes=65536\n'
            'graphics_wb_physical=65536\ngraphics_wb_virtual=2684420096\ngraphics_wb_exponent=7\n'
            'native_initial_coherency=1,0,4096,16777216\nexperimental_native_initialization=1,25,24,7,44\n')
        stopped_at_swap=False
        stopped_at_deferred=False
        def parse(cursor, cache, state, prefix=base,cooperative=None):
            if stopped_at_swap:
                prefix=prefix.replace('stop_reason=unimplemented-import','stop_reason=rejected-import').replace(
                    'stop_import=__imp__Synthetic','stop_import=__imp__VdSwap')
            if stopped_at_deferred:
                prefix=prefix.replace('stop_reason=unimplemented-import','stop_reason=unsupported-scalar-access').replace(
                    'stop_import=__imp__Synthetic','stop_import=none')
                prefix+=('scalar_address=2143815444\nscalar_width=4\nscalar_value=79\nscalar_write=1\nscalar_region=1\n'
                    'gpu_submission=5,0,0,0,76,79,3,1\ngpu_packet=0,3,2,63,0,0,0,0\n'
                    'gpu_indirect_graph=[7,0,0,4,512,83,0,0,[]]\n')
            return trace.parse_trace_protocol(prefix+f'graphics_ring_read_word={cursor}\n'
                f'native_empty_cache_coherency={cache}\nnative_state_batches={state}\n',1,10,2,cooperative=cooperative)
        for length in range(7):
            for history in itertools.product('CS',repeat=length):
                records=[]
                for kind in 'CS':
                    count=history.count(kind)
                    last=25+3*(max(i for i,v in enumerate(history) if v==kind)+1) if count else 0
                    records.append(f'{count},{last},0,4096,16777216' if count and kind=='C' else
                        f'{count},{last},{count*2},{count*5},{count}' if count else '0,0,0,0,0')
                result=parse(25+3*length,*records)
                self.assertEqual(result.native_state_batches['batches'],history.count('S'))
                self.assertFalse(result.native_state_batches['frame_presented'])
                self.assertEqual(result.native_initial_coherency['consumed_ring_words'],22)
        for bad in ('1,0,2,5,1','1,28,2,5,1','1,32,2,5,1','0,31,0,0,0','1,31,65,5,1',
                    '1,31,2,257,1','1,31,2,5,129','01,31,2,5,1','1,31,2,5,4294967296','bad'):
            with self.subTest(raw=bad), self.assertRaises(trace.TraceError):
                parse(31,'1,28,0,4096,16777216',bad)
        # Valid-looking totals cannot describe an impossible historical order.
        for cursor,cache,state in ((40,'1,37,0,4096,16777216','4,37,8,20,4'),
                                   (40,'1,28,0,4096,16777216','4,37,8,20,4'),
                                   (40,'4,28,0,4096,16777216','1,40,2,5,1')):
            with self.assertRaises(trace.TraceError): parse(cursor,cache,state)
        for prefix in (valid_protocol(),base.replace('experimental_native_initialization=1,25,24,7,44\n','')):
            with self.assertRaises(trace.TraceError): parse(31,'1,28,0,4096,16777216','1,31,2,5,1',prefix)
        restarted=parse(0,'1,28,0,4096,16777216','1,31,2,5,1',base+'native_ring_restart=1,31\n')
        self.assertEqual(restarted.graphics_ring['generation'],1)
        self.assertEqual(restarted.native_ring_restart['retired_read_word'],31)
        self.assertFalse(restarted.native_ring_restart['new_generation_commands_executed'])
        self.assertEqual(restarted.native_state_batches['ring_generation'],0)
        for raw in ('0,31','2,31','1,0','1,25','1,28','1,34','1,16384','1,4294967296','1,031','bad'):
            with self.subTest(restart=raw),self.assertRaises(trace.TraceError):
                parse(0,'1,28,0,4096,16777216','1,31,2,5,1',base+f'native_ring_restart={raw}\n')
        with self.assertRaises(trace.TraceError):
            parse(1,'1,28,0,4096,16777216','1,31,2,5,1',base+'native_ring_restart=1,31\n')
        with self.assertRaises(trace.TraceError):
            parse(0,'1,28,0,4096,16777216','0,0,0,0,0',base+'native_ring_restart=1,28\n')
        warm=base+'native_ring_restart=1,31\nnative_ring_warm_coherency=1,0,4096,16777216\n'
        warmed=parse(22,'1,28,0,4096,16777216','1,31,2,5,1',warm)
        self.assertTrue(warmed.native_ring_restart['new_generation_commands_executed'])
        self.assertEqual(warmed.native_ring_warm_coherency['ring_generation'],1)
        self.assertFalse(warmed.native_ring_warm_coherency['hardware_microcode_executed'])
        for bad in ('0,0,4096,16777216','1,1,4096,16777216','1,0,0,16777216','1,0,4096,0',
                    '1,536870912,4096,16777216','1,4294967296,4096,16777216','bad'):
            with self.subTest(warm=bad),self.assertRaises(trace.TraceError):
                parse(22,'1,28,0,4096,16777216','1,31,2,5,1',base+'native_ring_restart=1,31\nnative_ring_warm_coherency='+bad+'\n')
        for prefix,cursor in ((warm,0),(warm,25),(warm.replace('native_ring_restart=1,31\n',''),22)):
            with self.assertRaises(trace.TraceError): parse(cursor,'1,28,0,4096,16777216','1,31,2,5,1',prefix)
        warm_state=warm+'native_warm_state_batches=1,25,2,12,1\n'
        advanced=parse(25,'1,28,0,4096,16777216','1,31,2,5,1',warm_state)
        self.assertEqual(advanced.native_warm_state_batches['ring_generation'],1)
        self.assertEqual(advanced.native_state_batches['ring_generation'],0)
        self.assertEqual(advanced.native_warm_state_batches['guest_words_published'],2)
        # Protocol-only fixtures, not synthetic claims of GPU execution. A
        # separately reported color transaction may extend only the immediate
        # warm-history tail. Existing state/cache records keep their old counts.
        def color(raw,cursor=28,prefix=warm_state):
            return parse(cursor,'1,28,0,4096,16777216','1,31,2,5,1',prefix+
                'workers_created=1\nworkers_entered=1\ncontext_switches=2\nnative_owned_color_batch='+raw+'\n',cooperative=True)
        fields=[1,25,28,4,2,3686400,3686400,4,1]
        good=','.join(map(str,fields))
        result=color(good)
        self.assertEqual(result.native_owned_color_batch['completed_batches'],1)
        self.assertEqual(result.native_owned_color_batch['guest_copy_active_bytes'],3686400)
        self.assertFalse(result.native_owned_color_batch['frame_presented'])
        self.assertFalse(result.native_owned_color_batch['hardware_cache_semantics_verified'])
        self.assertEqual(result.native_warm_state_batches['read_word'],25)
        self.assertEqual(color('0,0,0,0,0,0,0,0,0',25).native_owned_color_batch['native_fences'],0)
        def context(raw):return color(good,prefix=warm_state+'native_no_system_overlay='+raw+'\n')
        system_fields=[1,0x12000,0x120D0,0x12074,28,1]
        system_good=','.join(map(str,system_fields))
        system=context(system_good).native_no_system_overlay
        self.assertEqual(system['issued_contexts'],1)
        self.assertFalse(system['gpu_identifier_written'])
        self.assertFalse(system['frame_presented'])
        self.assertFalse(system['retail_structure_verified'])
        self.assertEqual(context('0,0,0,0,0,0').native_no_system_overlay['issued_contexts'],0)
        def native_swap(raw,cursor=28,extra=''):
            return color(good,cursor,prefix=warm_state+'native_no_system_overlay='+system_good+
                '\nnative_swap_command='+raw+'\n'+extra).native_swap_command
        pending_fields=[1,1,0,0x20100,28,28]
        pending=native_swap(','.join(map(str,pending_fields)))
        def display_mode(raw):
            return color(good,prefix=warm_state+'native_no_system_overlay='+system_good+
                '\nnative_swap_command='+','.join(map(str,pending_fields))+'\nnative_display_mode='+raw+'\n').native_display_mode
        self.assertFalse(display_mode('0,0,0')['selected'])
        self.assertEqual(display_mode('1,1073741824,1')['guest_information_bytes_written'],88)
        self.assertFalse(display_mode('1,1073741824,0')['physical_display_changed'])
        for raw in ('','true,0,0','0,0,1','0,1073741824,0','1,1073741824,2','1,0,0'):
            with self.assertRaises(trace.TraceError):display_mode(raw)
        for index in range(3):
            for bit in range(32):
                bad=[1,0x40000000,1];bad[index]^=1<<bit
                if index==2 and bit==0:continue # Query not yet called is valid.
                with self.assertRaises(trace.TraceError):display_mode(','.join(map(str,bad)))
        with self.assertRaises(trace.TraceError):color(good,prefix=warm_state+'native_display_mode=0,0,0\n')
        self.assertTrue(pending['pending']);self.assertFalse(pending['present_queued'])
        self.assertEqual(pending['consumed_ring_words'],0)
        queued_fields=[1,0,1,0x20100,28,31]
        queued=native_swap(','.join(map(str,queued_fields)),31)
        self.assertTrue(queued['gpu_upload_readback_audited']);self.assertFalse(queued['vblank_completed'])
        self.assertEqual(queued['consumed_ring_words'],3)
        self.assertEqual(native_swap('0,0,0,0,0,0')['issued_commands'],0)
        # A separate command-storage fence advances the current cursor, while
        # the native ticket's issuance cursor remains the owned-color cursor.
        storage_fields=[1,28,31,0x20000,0x4000,0x01000000]
        def storage(raw,swap='1,1,0,131328,31,31,28',cursor=31,mode='1,1073741824,1',extra=''):
            return color(good,cursor,prefix=warm_state+'native_no_system_overlay='+system_good+
                '\nnative_swap_command='+swap+'\nnative_display_mode='+mode+
                '\nnative_command_storage_coherency='+raw+'\n'+extra)
        storage_good=','.join(map(str,storage_fields))
        ordered=storage(storage_good)
        self.assertEqual(ordered.native_command_storage_coherency['native_fences'],1)
        self.assertFalse(ordered.native_command_storage_coherency['guest_payload_uploaded'])
        self.assertFalse(ordered.native_command_storage_coherency['present_queued'])
        self.assertTrue(ordered.native_swap_command['pending'])
        self.assertEqual(ordered.native_swap_command['issued_read_word'],28)
        presented=storage(storage_good,'1,0,1,131328,31,34,28',34)
        self.assertTrue(presented.native_swap_command['present_queued'])
        self.assertEqual(presented.native_swap_command['consumed_ring_words'],3)
        wrapped_fields=[1,31,34,0x20100-40,4,1000,1200,0x10080,500,0x10084,600,0x10040,0x12345678,0x10044,13]
        def wrapped(raw,extra='experimental_gpu_clock_500mhz_reads=1\nexperimental_idle_cache_pairs=1\n',
                    swap='1,0,1,131328,31,34,28',cursor=34):
            return storage(storage_good,swap,cursor,extra='graphics_identifier_virtual=2684420104\n'+extra+'native_wrapped_swap='+raw+'\n')
        wrapped_good=','.join(map(str,wrapped_fields))
        result=wrapped(wrapped_good)
        timing_good='1,500,74,74250000,1650,750,60,2'
        def timing(raw):return wrapped(wrapped_good,'experimental_gpu_clock_500mhz_reads=1\nexperimental_idle_cache_pairs=1\n'+
            'native_scaler_timing='+raw+'\n').native_scaler_timing
        self.assertEqual(timing(timing_good)['mode_word_1c'],74)
        self.assertTrue(timing(timing_good)['field_semantics_hypothesis'])
        self.assertFalse(timing(timing_good)['host_gpu_frequency_measured'])
        def scaler(raw,with_timing=True,cursor=34,extra=''):
            return wrapped(wrapped_good,'experimental_gpu_clock_500mhz_reads=1\nexperimental_idle_cache_pairs=1\n'+
                ('native_scaler_timing='+timing_good+'\n' if with_timing else '')+
                'native_scaler_command='+raw+'\n'+extra,cursor=cursor).native_scaler_command
        scaler_good='1,34,64,1677725696'
        generated=scaler(scaler_good)
        self.assertTrue(generated['pending'])
        self.assertEqual(generated['guest_command_bytes_written'],256)
        self.assertEqual(generated['consumed_ring_words'],0)
        self.assertFalse(generated['gpu_work_completed'])
        self.assertFalse(scaler('0,0,0,0')['pending'])
        for field in range(3):
            for bit in range(32):
                bad=list(map(int,scaler_good.split(',')));bad[field]^=1<<bit
                with self.assertRaises(trace.TraceError):scaler(','.join(map(str,bad)))
        for raw in ('','1,34,64,0','1,34,64,1','1,34,64,4294967296','0,0,64,0',
                    '01,34,64,1677725696',scaler_good+',0'):
            with self.assertRaises(trace.TraceError):scaler(raw)
        with self.assertRaises(trace.TraceError):scaler(scaler_good,False)
        scaler_storage_good='1,34,37,131840,131072,16384,16777216'
        # Exercise protocol field collection too: standalone record parsers
        # cannot catch a newly emitted field being silently ignored.
        scaler_records=(
            'native_scaler_timing='+timing_good+'\n'+
            'native_scaler_command='+scaler_good+'\n'+
            'native_scaler_storage_coherency='+scaler_storage_good+'\n'+
            'native_scaler_interrupt=1,1,1,1677733888,0,1,1,1,1,1,1,0\n'+
            'native_scaler_pacing=1,60,0,2200000000,520,50000000\n'+
            'native_scaler_completion=1,37,40,3,65600,305419896,65604,15\n')
        def completed(records=scaler_records,cursor=40,pairs=1):
            return wrapped(wrapped_good,f'experimental_gpu_clock_500mhz_reads=1\nexperimental_idle_cache_pairs={pairs}\n'+records,cursor=cursor)
        completion=completed()
        self.assertTrue(completion.native_scaler_completion['configured'])
        self.assertEqual(completion.native_scaler_completion['read_word'],40)
        self.assertFalse(completion.native_scaler_command['pending'])
        self.assertTrue(completion.native_scaler_command['gpu_work_completed'])
        self.assertEqual(completion.native_scaler_interrupt['pacing_preflight']['refresh'],60)
        post_record='native_post_scaler_storage=2,40,43,46,131072,16384,16777216,2\n'
        post=completed(scaler_records+post_record,46)
        self.assertEqual(post.native_post_scaler_storage['completed_batches'],2)
        self.assertEqual(post.graphics_ring['ring_read_word'],46)
        self.assertEqual(post.native_scaler_completion['read_word'],40)
        self.assertFalse(post.native_post_scaler_storage['frames_presented'])
        gamma_record='native_display_gamma=1,46,49,131584,1048576,2,1,65600,305419896,65604,17\n'
        gamma=completed(scaler_records+post_record+gamma_record,49)
        self.assertEqual(gamma.native_display_gamma['display_updates_queued'],1)
        self.assertEqual(gamma.native_display_gamma['index_writes'],256)
        self.assertEqual(gamma.graphics_ring['ring_read_word'],49)
        display_record='native_post_display_storage=2,49,52,55,131072,16384,16777216,2\n'
        display=completed(scaler_records+post_record+gamma_record+display_record,55)
        self.assertEqual(display.native_post_display_storage['completed_batches'],2)
        self.assertEqual(display.native_display_gamma['read_word'],49)
        self.assertEqual(display.native_post_scaler_storage['read_word'],46)
        output_record='native_display_output=1,55,58,145664,2,1,65600,305419900,65604,19\n'
        before_output=scaler_records+post_record+gamma_record+display_record
        output=completed(before_output+output_record,58)
        self.assertEqual(output.native_display_output['display_updates_queued'],1)
        self.assertFalse(output.native_display_output['retail_link_semantics_verified'])
        self.assertEqual(output.native_post_display_storage['read_word'],55)
        self.assertEqual(output.native_display_gamma['read_word'],49)
        edram_prefix=before_output+output_record
        output_storage_record='native_post_output_storage=2,58,61,64,131072,16384,16777216,2\n'
        output_storage=completed(edram_prefix+output_storage_record,64)
        self.assertEqual(output_storage.native_post_output_storage['completed_batches'],2)
        self.assertEqual(output_storage.native_display_output['read_word'],58)
        self.assertEqual(output_storage.native_post_display_storage['read_word'],55)
        self.assertEqual(output_storage.native_display_gamma['read_word'],49)
        self.assertFalse(output_storage.native_post_output_storage['frames_presented'])
        color_record='native_post_output_color=1,64,67,4,2,3686400,3686400,4,1,1\n'
        color_prefix=edram_prefix+output_storage_record
        next_color=completed(color_prefix+color_record,67)
        self.assertEqual(next_color.native_post_output_color['native_fences'],5)
        self.assertEqual(next_color.native_post_output_storage['read_word'],64)
        self.assertFalse(next_color.native_post_output_color['scanout_transition_completed'])
        sp=next_color.native_no_system_overlay['stack_pointer']
        second_context_record=f'native_post_output_system_context=1,{sp},{sp+208},{sp+116},67,1\n'
        with_context=completed(color_prefix+color_record+second_context_record,67)
        self.assertEqual(with_context.native_post_output_system_context['read_word'],67)
        self.assertEqual(with_context.native_no_system_overlay,next_color.native_no_system_overlay)
        second_swap_record='native_post_output_swap=1,1,0,131456,67,67,67\n'
        with_swap=completed(color_prefix+color_record+second_context_record+second_swap_record,67)
        self.assertEqual(with_swap.native_post_output_swap['ticket'],2)
        self.assertEqual(with_swap.native_swap_command,with_context.native_swap_command)
        swap_storage_record='native_post_output_swap_storage=1,67,70,131072,16384,16777216\n'
        after_swap_storage=second_swap_record.replace(',67,67,67',',70,70,67')
        stored=completed(color_prefix+color_record+second_context_record+after_swap_storage+swap_storage_record,70)
        self.assertEqual(stored.native_post_output_swap_storage['native_fences'],1)
        self.assertEqual(stored.native_post_output_swap['issued_read_word'],67)
        self.assertEqual(stored.native_post_output_swap['read_word'],70)
        self.assertEqual(stored.native_swap_command,with_swap.native_swap_command)
        second_complete='native_post_output_swap=1,0,1,131456,70,73,67\n'
        second_fields=[1,70,73,131416,2097152,2097152,2686451712,3768320,1280,720,6,1,1,1000,1010,
            1677746176,1,1,1,0,1,1,1,0,1,65600,500,65604,505,65608,305419900,65612,23]
        second_wrapped='native_post_output_wrapped_swap='+','.join(map(str,second_fields))+'\n'
        before_second=color_prefix+color_record+second_context_record+swap_storage_record
        second=completed(before_second+second_complete+second_wrapped,73,2)
        self.assertEqual(second.native_post_output_wrapped_swap['native_fences'],6)
        self.assertEqual(second.native_post_output_swap['frames_presented'],1)
        self.assertEqual(second.native_post_output_swap['issued_read_word'],67)
        self.assertFalse(second.native_post_output_swap['pending'])
        self.assertFalse(second.native_post_output_wrapped_swap['vblank_completed'])
        self.assertEqual(second.native_swap_command,stored.native_swap_command)
        self.assertEqual(second.native_scaler_interrupt,stored.native_scaler_interrupt)
        self.assertEqual(second.native_post_output_swap_storage,stored.native_post_output_swap_storage)
        self.assertEqual(second.experimental_idle_cache_pairs['wrapped_swap_pairs'],2)
        third_context=f'native_post_swap_system_context=1,{sp},{sp+208},{sp+116},73,1\n'
        after_second=before_second+second_complete+second_wrapped
        third=completed(after_second+third_context,73,2)
        self.assertEqual(third.native_post_swap_system_context['read_word'],73)
        self.assertEqual(third.native_post_swap_system_context['profile'],'native-post-second-swap-no-system-overlay-v1')
        self.assertFalse(third.native_post_swap_system_context['swap_completed'])
        self.assertEqual(third.native_post_output_wrapped_swap,second.native_post_output_wrapped_swap)
        stopped_at_swap=True
        raw_request='stopped_post_swap_request='+json.dumps([1,0xA0020800,0x20800,0xA0020000,0x4000,0x20000004,
            0xA0700000,6,0,1280,720,0,0x8A000002,0xA0700006,0x0059E4FF,0x1414,0,0x200])+'\n'
        diagnostic=('stopped_physical_capture=1\nstopped_physical_requests=[[133120,4]]\n'
            'stopped_physical_blob=133120,4,00000000\n'
            'stopped_physical_ownership=[[131072,2684485632,16384,536870916,1]]\n'
            'stopped_ownership_status=1\nstopped_ownership_requests=[[7340032,3768320]]\n'
            'stopped_ownership_results=[[7340032,2691694592,3768320,1028,1]]\n')
        inspected=completed(after_second+third_context+raw_request+diagnostic,73,2)
        self.assertEqual(inspected.private_physical_capture['status'],1)
        self.assertEqual(inspected.stopped_ownership_queries['status'],1)
        self.assertEqual(inspected.private_physical_capture['stopped_swap_request'],inspected.stopped_post_swap_request)
        self.assertFalse(inspected.stopped_post_swap_request['next_swap_completed'])
        for request_text,capture_text in (('',diagnostic),(raw_request*2,diagnostic),
                (raw_request,diagnostic.replace('stopped_physical_capture=1','stopped_physical_capture=1\nstopped_physical_capture=1')),
                (raw_request,diagnostic.replace('stopped_physical_requests=[[133120,4]]','stopped_physical_requests=[[7340032,4]]')),
                (raw_request,diagnostic.replace('[[131072,2684485632,16384,536870916,1]]','[null]'))):
            with self.assertRaises(trace.TraceError):completed(after_second+third_context+request_text+capture_text,73,2)
        stopped_at_swap=False
        with self.assertRaises(trace.TraceError):completed(after_second+third_context+raw_request+diagnostic,73,2)
        deferred_fields=[1,3,0xA0020800,0x20800,0xA0020000,0x4000,0x20000004,0xA0700000,6,0,1280,720,0,
            0x8A000002,0xA0700006,0x0059E4FF,0x1414,0,0x200,0x700000,0xA0700000,3768320,0x404,1,73]
        deferred_record='native_deferred_swap_request='+json.dumps(deferred_fields)+'\n'
        deferred=completed(after_second+third_context+deferred_record,73,2)
        self.assertTrue(deferred.native_deferred_swap_request['pending'])
        self.assertFalse(deferred.native_deferred_swap_request['frame_presented'])
        self.assertEqual(deferred.native_post_output_wrapped_swap,third.native_post_output_wrapped_swap)
        deferred_fence='native_deferred_swap_storage=1,73,76,131072,16384,16777216\n'
        fenced=completed(after_second+third_context+deferred_record+deferred_fence,76,2)
        self.assertEqual(fenced.native_deferred_swap_storage['native_fences'],1)
        self.assertEqual(fenced.native_deferred_swap_request,deferred.native_deferred_swap_request)
        self.assertEqual(fenced.native_post_output_wrapped_swap,deferred.native_post_output_wrapped_swap)
        self.assertTrue(fenced.native_deferred_swap_request['pending'])
        stopped_at_deferred=True
        pending_record='native_pending_submission=1,304,131072,3000,512,76,79\n'
        pending_capture=('stopped_physical_capture=1\nstopped_physical_byte_budget=16384\n'
            'stopped_physical_requests=[[131072,12000]]\nstopped_physical_blob=131072,12000,'+'00'*12000+'\n'
            'stopped_physical_ownership=[[131072,2684485632,16384,536870916,1]]\n')
        deferred_prefix=after_second+third_context+deferred_record+deferred_fence
        live_prefix='native_deferred_color_prefix=1,0,0,1,76,79,2778,12,3686400,4,1,1,76\n'
        live=completed(deferred_prefix+live_prefix,76,2)
        self.assertTrue(live.native_deferred_color_prefix['completed'])
        self.assertTrue(live.native_deferred_swap_request['pending'])
        self.assertEqual(live.native_post_output_wrapped_swap,fenced.native_post_output_wrapped_swap)
        irq_record='stopped_deferred_swap_interrupt='+json.dumps([1,76,79,2778,0x30000,0x836B4B98,0x700208,0x64000000,0x836B1768,50000000]+[0]*60)+'\n'
        irq=completed(deferred_prefix+live_prefix+irq_record,76,2)
        self.assertTrue(irq.stopped_deferred_swap_interrupt['captured'])
        with self.assertRaises(trace.TraceError):completed(deferred_prefix+live_prefix+irq_record*2,76,2)
        with self.assertRaises(trace.TraceError):completed(deferred_prefix+irq_record,76,2)
        with self.assertRaises(trace.TraceError):completed(deferred_prefix+live_prefix*2,76,2)
        with self.assertRaises(trace.TraceError):completed(after_second+live_prefix,73,2)
        captured=completed(deferred_prefix+pending_record+pending_capture,76,2)
        self.assertEqual(captured.native_pending_submission['scope'],'stopped-pending-native-deferred-swap')
        self.assertFalse(captured.native_pending_submission['retail_graph_valid'])
        self.assertEqual(captured.private_physical_capture['byte_budget'],16384)
        self.assertEqual(captured.native_post_output_wrapped_swap,fenced.native_post_output_wrapped_swap)
        opaque_child=bytearray(12000)
        opaque_child[512*4:576*4]=struct.pack('>64I',*([0xFFFF5357,0x41500001,64,1,0,3,0x20800,0x700000,73,
            1280,720,3768320,3686400]+[0]*51))
        native_capture=('stopped_pm4_capture=0\nstopped_native_pending_capture=1\n'
            'stopped_native_pending_blob=304,12,'+struct.pack('>III',0xC0013F00,131072,3000).hex()+'\n'
            'stopped_native_pending_blob=131072,12000,'+opaque_child.hex()+'\n'
            'stopped_native_pending_ownership=[[0,2684354560,65536,536870916,1],[131072,2684485632,16384,536870916,1]]\n')
        auto=completed(deferred_prefix+pending_record+native_capture,76,2)
        self.assertEqual(auto.private_native_pending_capture['requests'],[[304,12],[131072,12000]])
        self.assertEqual(auto.private_pm4_capture['status'],0) # Never made into executable retail PM4.
        # Independent prefix-source evidence is separately labeled. Synthetic
        # opaque tail here is not a claim that the C++ surface plan executed.
        shader_child=opaque_child[:631*4]
        prefix_words=[0xC0042B00,0,3,0x11223344,0x55667788,0x99AABBCC,0xC0012700,0x40001,3]+[0xC0003600,0x30088]*4+[0x80000000]*485
        shader_child[:502*4]=struct.pack('>502I',*prefix_words)
        shader_native=native_capture.replace('131072,12000,','131072,2524,').replace(opaque_child.hex(),shader_child.hex()).replace(
            struct.pack('>III',0xC0013F00,131072,3000).hex(),struct.pack('>III',0xC0013F00,131072,631).hex())
        shader_pending=pending_record.replace(',3000,',',631,')
        shader_records=('stopped_shader_capture=0\nstopped_deferred_shader_capture=1\n'
            'stopped_deferred_shader_blob=1,0,0,112233445566778899aabbcc\n'
            'stopped_deferred_shader_blob=1,6,1,0,262144,'+'4a'*12+'\n')
        shader_protocol=deferred_prefix+shader_pending+shader_native+shader_records
        loaded=completed(shader_protocol,76,2)
        self.assertEqual(len(loaded.private_deferred_shader_capture['blobs']),2)
        self.assertEqual(loaded.private_deferred_shader_capture['prefix_words'],502)
        self.assertEqual(loaded.private_shader_capture['status'],0)
        self.assertEqual(loaded.private_pm4_capture['status'],0)
        color_record='stopped_deferred_color_inspection=[1,0,502,4,2,0,1474968,7340032,1,76]\n'
        color_checked=completed(shader_protocol+color_record,76,2)
        self.assertFalse(color_checked.stopped_deferred_color_inspection['gpu_executed'])
        self.assertEqual(color_checked.stopped_deferred_color_inspection['unchanged_read_word'],76)
        memory_record='stopped_deferred_memory_inspection=[1,8,7,76]\n'
        matched=completed(shader_protocol+color_record+memory_record,76,2)
        self.assertTrue(matched.stopped_deferred_memory_inspection['whole_submission_and_sources_matched'])
        self.assertFalse(matched.stopped_deferred_memory_inspection['live_allocation_leases_acquired'])
        gpu_record='stopped_deferred_color_gpu=[1,1,4,3686400,3686400,1,1,1,76]\n'
        diagnostic=completed(shader_protocol+color_record+memory_record+gpu_record,76,2)
        self.assertTrue(diagnostic.stopped_deferred_color_gpu['completed_profile'])
        self.assertFalse(diagnostic.stopped_deferred_color_gpu['frame_presented'])
        timeline_record='stopped_deferred_color_timeline=[1,1,4,2,1,1,1,0,76]\n'
        audited=completed(shader_protocol+color_record+memory_record+gpu_record+timeline_record,76,2)
        self.assertEqual(audited.stopped_deferred_color_timeline['invalidations'],2)
        self.assertFalse(audited.stopped_deferred_color_timeline['live_state_committed'])
        state_record='stopped_deferred_color_pending_state=[1,219,6,2,1,1,1,1,76]\n'
        state_protocol=shader_protocol+color_record+memory_record+gpu_record+timeline_record
        prepared=completed(state_protocol+state_record,76,2)
        self.assertEqual(prepared.stopped_deferred_color_pending_state['invalidation_delta'],2)
        guard_bytes=1474968+prepared.graphics_ring['ring_bytes']+4*(502+129)+2*3768320+60
        guard_record=f'stopped_deferred_color_write_guard=[1,{guard_bytes},20,76]\n'
        guarded=completed(state_protocol+state_record+guard_record,76,2)
        self.assertTrue(guarded.stopped_deferred_color_write_guard['all_before_images_verified'])
        for records in (state_protocol+guard_record*2,state_protocol+guard_record.replace(',20,',',19,'),
                state_protocol+guard_record.replace(',76]',',73]'),state_protocol.replace(gpu_record,'')+guard_record):
            with self.assertRaises(trace.TraceError):completed(records,76,2)
        for records in (state_protocol+state_record*2,state_protocol+state_record.replace(',76]',',73]'),
                state_protocol+state_record.replace('219,6,2','219,6,3'),state_protocol.replace(timeline_record,'')+state_record):
            with self.assertRaises(trace.TraceError):completed(records,76,2)
        for records in (shader_protocol+color_record+memory_record+timeline_record,
                shader_protocol+color_record+memory_record+gpu_record+timeline_record*2,
                shader_protocol+color_record+memory_record+gpu_record+timeline_record.replace(',76]',',73]'),
                shader_protocol+color_record+memory_record+gpu_record+timeline_record.replace('4,2,1,1,1,0','4,3,3,1,1,2')):
            with self.assertRaises(trace.TraceError):completed(records,76,2)
        for records in (shader_protocol+color_record+gpu_record,shader_protocol+color_record+memory_record+gpu_record*2,
                shader_protocol+color_record+memory_record+gpu_record.replace(',76]',',73]')):
            with self.assertRaises(trace.TraceError):completed(records,76,2)
        for records in (shader_protocol+memory_record,shader_protocol+color_record+memory_record*2,
                shader_protocol+color_record+memory_record.replace(',76]',',73]'),
                shader_protocol+color_record+memory_record.replace('[1,8,','[1,7,')):
            with self.assertRaises(trace.TraceError):completed(records,76,2)
        for records in (shader_protocol+color_record*2,shader_protocol+color_record.replace(',76]',',73]'),
                shader_protocol+color_record.replace('1474968','1474967'),deferred_prefix+color_record):
            with self.assertRaises(trace.TraceError):completed(records,76,2)
        for records in (shader_protocol.replace(shader_native,''),shader_protocol.replace('stopped_shader_capture=0\n',''),
                shader_protocol+'stopped_deferred_shader_capture=1\n',shader_protocol+shader_records.splitlines()[-1]+'\n',
                shader_protocol.replace('112233445566778899aabbcc','012233445566778899aabbcc',1),
                shader_protocol.replace('stopped_deferred_shader_capture=1\n','')):
            with self.assertRaises(trace.TraceError):completed(records,76,2)
        for records in (deferred_prefix+native_capture,deferred_prefix+pending_record+native_capture*2,
                deferred_prefix+pending_record+native_capture.replace('ffff5357','ffff5356'),
                deferred_prefix+pending_record+native_capture.replace('stopped_pm4_capture=0\n','')):
            with self.assertRaises(trace.TraceError):completed(records,76,2)
        for records in (deferred_prefix+pending_capture,deferred_prefix+pending_record*2+pending_capture,
                deferred_prefix+pending_record.replace(',512,',',511,')+pending_capture,
                deferred_prefix+pending_record+pending_capture.replace('[[131072,2684485632,16384,536870916,1]]','[null]'),
                deferred_prefix.replace(deferred_fence,'')+pending_record+pending_capture):
            with self.assertRaises(trace.TraceError):completed(records,76,2)
        stopped_at_deferred=False
        for records,cursor in ((after_second+third_context+deferred_record,76),
                (after_second+third_context+deferred_fence,76),
                (after_second+third_context+deferred_record+deferred_fence*2,76),
                (after_second+third_context+deferred_record+deferred_fence,73),
                (after_second+third_context+deferred_record+'native_deferred_swap_storage=bad\n',76)):
            with self.assertRaises(trace.TraceError):completed(records,cursor,2)
        for records in (after_second+deferred_record,after_second+third_context+deferred_record*2,
                after_second+third_context+'native_deferred_swap_request=bad\n'):
            with self.assertRaises(trace.TraceError):completed(records,73,2)
        with self.assertRaises(trace.TraceError):completed(after_second+third_context+'stopped_post_swap_request=bad\n',73,2)
        for records,cursor,pairs in ((after_second+third_context*2,73,2),(after_second+third_context.replace(',73,1',',70,1'),73,2),
                (after_second+'native_post_swap_system_context=bad\n',73,2),
                (before_second+after_swap_storage+third_context,70,1),(color_prefix+third_context,64,1)):
            with self.assertRaises(trace.TraceError):completed(records,cursor,pairs)
        for suffix,cursor,pairs in ((second_complete,73,2),(second_wrapped,73,2),(second_complete+second_wrapped,70,2),
                (after_swap_storage+second_wrapped,73,2),(second_complete+second_wrapped*2,73,2),
                (second_complete+second_wrapped,73,1),(second_complete+'native_post_output_wrapped_swap=bad\n',73,2),
                (second_complete+'native_post_output_wrapped_swap='+','.join(['0']*33)+'\n',73,2)):
            with self.assertRaises(trace.TraceError):completed(before_second+suffix,cursor,pairs)
        with self.assertRaises(trace.TraceError):completed(before_second.replace(swap_storage_record,'')+second_complete+second_wrapped,73,2)
        for suffix in (swap_storage_record,after_swap_storage,after_swap_storage+swap_storage_record*2,
                'native_post_output_swap=0,0,0,0,0,0,0\n'+swap_storage_record,second_swap_record+swap_storage_record):
            with self.assertRaises(trace.TraceError):completed(color_prefix+color_record+second_context_record+suffix,70)
        for records in (color_prefix+color_record+second_swap_record,
                color_prefix+color_record+second_context_record+second_swap_record*2,
                color_prefix+color_record+second_context_record+'native_post_output_swap=bad\n'):
            with self.assertRaises(trace.TraceError):completed(records,67)
        for records in (color_prefix+second_context_record,color_prefix+color_record+second_context_record*2,
                color_prefix+color_record+'native_post_output_system_context=bad\n'):
            with self.assertRaises(trace.TraceError):completed(records,67)
        for records,cursor in ((color_prefix+color_record+color_record,67),(color_prefix+color_record,64),
                (color_prefix,67),(edram_prefix+color_record,67),(color_prefix+'native_post_output_color=bad\n',64)):
            with self.assertRaises(trace.TraceError):completed(records,cursor)
        for records,cursor in ((edram_prefix+output_storage_record+output_storage_record,64),
                (edram_prefix+'native_post_output_storage=bad\n',58),(edram_prefix+output_storage_record,58),
                (edram_prefix,64),(before_output+output_storage_record,64)):
            with self.assertRaises(trace.TraceError):completed(records,cursor)
        for count in (1,2,256):
            edram=completed(edram_prefix+f'native_edram_training_policy=1,{count+1},1,{count}\n',58)
            self.assertEqual(edram.native_edram_training_policy['direct_no_command_queries'],count)
            self.assertEqual(edram.native_edram_training_policy['worker_checks'],1)
        for bad in ('0,1,0,1','1,2,1,0','1,3,1,1','1,1,1,1','1,258,1,257','1,2,1,1,0','1,2,1,-1'):
            with self.assertRaises(trace.TraceError):completed(edram_prefix+'native_edram_training_policy='+bad+'\n',58)
        with self.assertRaises(trace.TraceError):completed(before_output+'native_edram_training_policy=1,2,1,1\n',55)
        for records,cursor in ((before_output+output_record+output_record,58),
                (before_output+'native_display_output=bad\n',55),(before_output+output_record,55),
                (before_output,58),(scaler_records+post_record+gamma_record+output_record,58)):
            with self.assertRaises(trace.TraceError):completed(records,cursor)
        for records,cursor in ((scaler_records+post_record+gamma_record+display_record+display_record,55),
                (scaler_records+post_record+gamma_record+'native_post_display_storage=bad\n',49),
                (scaler_records+post_record+gamma_record+display_record,49),
                (scaler_records+post_record+gamma_record,55),(scaler_records+post_record+display_record,55)):
            with self.assertRaises(trace.TraceError):completed(records,cursor)
        for records,cursor in ((scaler_records+post_record+gamma_record+gamma_record,49),
                (scaler_records+post_record+'native_display_gamma=bad\n',46),
                (scaler_records+post_record+gamma_record,46),(scaler_records+post_record,49)):
            with self.assertRaises(trace.TraceError):completed(records,cursor)
        for records,cursor in ((scaler_records+post_record+post_record,46),
                (scaler_records+'native_post_scaler_storage=bad\n',40),
                (scaler_records+post_record,40),(scaler_records+post_record,43),(scaler_records,46)):
            with self.assertRaises(trace.TraceError):completed(records,cursor)
        for key in ('native_scaler_pacing','native_scaler_completion'):
            record=next(line for line in scaler_records.splitlines(True) if line.startswith(key+'='))
            for bad in (scaler_records+record,scaler_records.replace(record,key+'=bad\n')):
                with self.subTest(field=key),self.assertRaises(trace.TraceError):completed(bad)
        for cursor in (37,39,41):
            with self.assertRaises(trace.TraceError):completed(cursor=cursor)
        with self.assertRaises(trace.TraceError):completed(scaler_records.replace(
            'native_scaler_completion=1,37,40,3,65600,305419896,65604,15\n',''))
        scaler(scaler_good,cursor=37,extra='native_scaler_storage_coherency='+scaler_storage_good+'\n')
        scaler(scaler_good,extra='native_scaler_storage_coherency=0,0,0,0,0,0,0\n')
        with self.assertRaises(trace.TraceError):scaler(scaler_good,cursor=37)
        with self.assertRaises(trace.TraceError):scaler(scaler_good,extra='native_scaler_storage_coherency='+scaler_storage_good+'\n')
        for field in (0,1,2,4,5,6):
            for bit in range(32):
                bad=list(map(int,scaler_storage_good.split(',')));bad[field]^=1<<bit
                with self.assertRaises(trace.TraceError):
                    scaler(scaler_good,cursor=37,extra='native_scaler_storage_coherency='+','.join(map(str,bad))+'\n')
        for raw in ('','0,0,0,1,0,0,0','1,34,37,1,131072,16384,16777216',
                    scaler_storage_good+',0','1,34,37,4294967296,131072,16384,16777216'):
            with self.assertRaises(trace.TraceError):scaler(scaler_good,cursor=37,extra='native_scaler_storage_coherency='+raw+'\n')
        for field in range(7):
            for bit in range(32):
                bad=list(map(int,timing_good.split(',')));bad[field]^=1<<bit
                with self.assertRaises(trace.TraceError):timing(','.join(map(str,bad)))
        for raw in ('',timing_good+',0','01'+timing_good[1:],timing_good[:-1]+str(1<<64)):
            with self.assertRaises(trace.TraceError):timing(raw)
        self.assertEqual(result.native_wrapped_swap['guest_event_words_published'],4)
        self.assertEqual(result.native_wrapped_swap['events'][0]['source'],'native-host-fence-completion-500mhz')
        self.assertEqual(result.experimental_idle_cache_pairs['wrapped_swap_pairs'],1)
        self.assertFalse(result.native_wrapped_swap['gpu_hardware_timestamps'])
        self.assertFalse(result.native_wrapped_swap['title_screen_verified'])
        self.assertEqual(wrapped(','.join(['0']*15),swap='1,1,0,131328,31,31,28',cursor=31).
                         native_wrapped_swap['completed_batches'],0)
        for field in (0,1,2,3,4,8,10):
            for bit in range(32):
                bad=wrapped_fields.copy();bad[field]^=1<<bit
                with self.assertRaises(trace.TraceError):wrapped(','.join(map(str,bad)))
        for field,value in ((5,1201),(6,999),(6,1000+8589934592),(5,1<<64),(11,0x20100),
                            (11,0x10080),(11,0x20000000),(11,0x10041),(9,0x10088)):
            bad=wrapped_fields.copy();bad[field]=value
            with self.assertRaises(trace.TraceError):wrapped(','.join(map(str,bad)))
        for extra in ('','experimental_gpu_clock_500mhz_reads=1\n','experimental_idle_cache_pairs=1\n',
                      'experimental_gpu_clock_500mhz_reads=1\nexperimental_idle_cache_pairs=0\n'):
            with self.assertRaises(trace.TraceError):wrapped(wrapped_good,extra)
        with self.assertRaises(trace.TraceError):wrapped(wrapped_good,swap='1,1,0,131328,31,31,28',cursor=31)
        for raw in ('',wrapped_good+',0','true'+wrapped_good[1:],'01'+wrapped_good[1:]):
            with self.assertRaises(trace.TraceError):wrapped(raw)
        self.assertEqual(storage('0,0,0,0,0,0','1,1,0,131328,28,28,28',28).
                         native_command_storage_coherency['completed_batches'],0)
        for raw in ('','[]','1,28,31,131072,16384','0,0,0,0,0,1','1,28,31,131072,16384,50331648'):
            with self.assertRaises(trace.TraceError):storage(raw)
        for index in (0,1,2,5):
            for bit in range(32):
                bad=storage_fields.copy();bad[index]^=1<<bit
                with self.assertRaises(trace.TraceError):storage(','.join(map(str,bad)))
        for range_base,size in ((0x20001,0x4000),(0x21000,0x4000),(0x20000,0),(0x20000,0x4001),
                          (0x1FFFF000,0x2000),(0x20000000,0x4000)):
            bad=storage_fields.copy();bad[3:5]=[range_base,size]
            with self.assertRaises(trace.TraceError):storage(','.join(map(str,bad)))
        for swap,cursor,mode in (('1,1,0,131328,31,31',31,'1,1073741824,1'),
                                ('1,1,0,131328,31,31,31',31,'1,1073741824,1'),
                                ('1,1,0,131328,28,28,28',28,'1,1073741824,1'),
                                ('1,1,0,131328,31,31,28',31,'1,1073741824,0'),
                                ('1,1,0,131328,31,31,28',31,'0,0,0')):
            with self.assertRaises(trace.TraceError):storage(storage_good,swap,cursor,mode)
        for raw in ('','[]','true,0,0,0,0,0','0,0,0,0,0,1','1,0,0,131328,28,28',
                    '1,1,1,131328,28,31','1,1,0,0,28,28','1,1,0,536870912,28,28',
                    '1,1,0,131329,28,28','1,1,0,131328,29,29','1,0,1,131328,28,32'):
            with self.subTest(native_swap=raw),self.assertRaises(trace.TraceError):native_swap(raw)
        for record,cursor in ((pending_fields,31),(queued_fields,28)):
            with self.assertRaises(trace.TraceError):native_swap(','.join(map(str,record)),cursor)
        for index in (0,1,2,4,5):
            for bit in range(32):
                bad=pending_fields.copy();bad[index]^=1<<bit
                with self.subTest(swap_field=index,bit=bit),self.assertRaises(trace.TraceError):
                    native_swap(','.join(map(str,bad)))
        with self.assertRaises(trace.TraceError):native_swap('0,0,0,0,0,0',extra='stopped_native_swap_request=[0]\n')
        with self.assertRaises(trace.TraceError):
            color(good,prefix=warm_state+'native_swap_command=1,1,0,131328,28,28\n')
        for index in range(6):
            for bit in range(32):
                bad=system_fields.copy();bad[index]^=1<<bit
                with self.subTest(system_field=index,bit=bit),self.assertRaises(trace.TraceError):
                    context(','.join(map(str,bad)))
        for bad in ('bad',system_good+',0','01'+system_good[1:],'0,0,0,0,0,1',
                    '1,4294967280,192,100,28,1'):
            with self.assertRaises(trace.TraceError):context(bad)
        with self.assertRaises(trace.TraceError):
            color('0,0,0,0,0,0,0,0,0',25,prefix=warm_state+'native_no_system_overlay='+system_good+'\n')
        swap_fields=[1,0xA0020100,0x20100,0xA0020000,0x4000,4,0xA0100000,6,0,1280,720,0,*range(6)]
        def swap_capture(raw):
            return color(good,prefix=warm_state.replace('__imp__Synthetic','__imp__VdSwap')+
                'native_no_system_overlay='+system_good+'\nstopped_native_swap_request='+raw+'\n').stopped_native_swap_request
        swap=swap_capture(json.dumps(swap_fields))
        self.assertTrue(swap['captured']);self.assertFalse(swap['frame_presented'])
        self.assertFalse(swap['commands_written']);self.assertFalse(swap['pixels_decoded'])
        self.assertEqual(swap['fetch_words'],list(range(6)))
        self.assertFalse(swap_capture('[0]')['captured'])
        for raw in ('[]','{}','null','[true]','[1]','[0,0]',json.dumps(swap_fields+[0]),
                    json.dumps([True]+swap_fields[1:]),json.dumps(swap_fields[:-1]+[0x100000000])):
            with self.subTest(swap=raw),self.assertRaises(trace.TraceError):swap_capture(raw)
        for index,value in ((1,0),(2,1),(3,0xA0030000),(4,0),(5,2)):
            bad=swap_fields.copy();bad[index]=value
            with self.assertRaises(trace.TraceError):swap_capture(json.dumps(bad))
        with self.assertRaises(trace.TraceError):
            color(good,prefix=warm_state+'native_no_system_overlay='+system_good+
                '\nstopped_native_swap_request='+json.dumps(swap_fields)+'\n')
        image_swap=swap_fields[:12]+[0x8A000002,0xA0100006,0x0059E4FF,0x1414,0,0x200]
        image_fields=[1,0x100000,0x100000,0xA0100000,0x398000,4,3768320,3686400,921600,921600]
        def swap_image(raw,request=image_swap,mode='image'):
            parsed=color(good,prefix=warm_state.replace('__imp__Synthetic','__imp__VdSwap')+
                'native_no_system_overlay='+system_good+'\nstopped_native_swap_request='+json.dumps(request)+
                '\nstopped_native_swap_'+mode+'='+raw+'\n')
            return getattr(parsed,'stopped_native_swap_'+mode)
        image=swap_image(json.dumps(image_fields))
        self.assertTrue(image['captured']);self.assertTrue(image['pixels_decoded'])
        self.assertFalse(image['frame_presented']);self.assertFalse(image['guest_writes'])
        self.assertFalse(image['native_swap_completed']);self.assertEqual(image['pixel_format'],'RGBA8')
        self.assertEqual(image['rgba_bytes'],3686400)
        self.assertFalse(swap_image('[0]')['captured'])
        nonblack=image_fields.copy();nonblack[8]=0
        self.assertEqual(swap_image(json.dumps(nonblack))['black_pixels'],0)
        # Failed request is reportable, but cannot certify a decoded image.
        self.assertFalse(swap_image('[0]',[0])['captured'])
        with self.assertRaises(trace.TraceError):swap_image(json.dumps(image_fields),[0])
        for raw in ('[]','{}','null','[true]','[1]','[0,0]',json.dumps(image_fields+[0]),
                    json.dumps([True]+image_fields[1:]),json.dumps(image_fields[:-1]+[0x100000000])):
            with self.subTest(swap_image=raw),self.assertRaises(trace.TraceError):swap_image(raw)
        for index,value in ((1,0x101000),(2,0x101000),(3,0xA0200000),(4,0),(4,0x397000),
                            (4,0x20000000),(5,2),(6,3768319),(7,3686399),(8,921601),(9,0)):
            bad=image_fields.copy();bad[index]=value
            with self.subTest(image_field=index,value=value),self.assertRaises(trace.TraceError):swap_image(json.dumps(bad))
        for index in range(12,18):
            for bit in range(32):
                bad=image_swap.copy();bad[index]^=1<<bit
                with self.subTest(fetch=index,bit=bit),self.assertRaises(trace.TraceError):swap_image(json.dumps(image_fields),bad)
        for index in (6,7,8,9,10):
            bad=image_swap.copy();bad[index]^=1
            with self.assertRaises(trace.TraceError):swap_image(json.dumps(image_fields),bad)
        with self.assertRaises(trace.TraceError):
            color(good,prefix=warm_state+'stopped_native_swap_image='+json.dumps(image_fields)+'\n')
        # Callback acceptance is not a display/vblank or guest completion proof.
        consumer_fields=image_fields+[1]
        consumer=swap_image(json.dumps(consumer_fields),mode='consumer')
        self.assertTrue(consumer['callback_attempted']);self.assertTrue(consumer['consumer_returned_success'])
        self.assertTrue(consumer['pixels_decoded']);self.assertNotIn('artifact_name',consumer)
        self.assertFalse(consumer['frame_presented']);self.assertFalse(consumer['display_completion_verified'])
        self.assertFalse(consumer['native_swap_completed']);self.assertFalse(consumer['guest_writes'])
        self.assertFalse(consumer['published_copy_continuity_verified']) # Historical v2 has no such evidence.
        self.assertFalse(consumer['allocation_lock_held_during_callback']);self.assertFalse(consumer['future_guest_lease'])
        self.assertFalse(swap_image(json.dumps(image_fields+[0]),mode='consumer')['consumer_returned_success'])
        self.assertFalse(swap_image('[0]',mode='consumer')['callback_attempted'])
        for raw in ('[]','{}','[true]','[1]','[0,0]',json.dumps(image_fields),json.dumps(image_fields+[True]),
                    json.dumps(image_fields+[2]),json.dumps(consumer_fields+[0])):
            with self.assertRaises(trace.TraceError):swap_image(raw,mode='consumer')
        for index,value in ((1,0x101000),(2,0x101000),(3,0xA0200000),(4,0),(4,0x397000),
                            (4,0x20000000),(5,2),(6,3768319),(7,3686399),(8,921601),(9,0)):
            bad=consumer_fields.copy();bad[index]=value
            with self.assertRaises(trace.TraceError):swap_image(json.dumps(bad),mode='consumer')
        with self.assertRaises(trace.TraceError):swap_image(json.dumps(consumer_fields),[0],mode='consumer')
        with self.assertRaises(trace.TraceError):
            swap_image(json.dumps(image_fields)+'\nstopped_native_swap_consumer='+json.dumps(consumer_fields))
        continuity=[1,28,3768320,3686400]
        def continued(raw,consumer_raw=json.dumps(consumer_fields)):
            return swap_image(consumer_raw+'\nstopped_native_swap_continuity='+raw,mode='consumer')
        proven=continued(json.dumps(continuity))
        self.assertTrue(proven['published_copy_continuity_verified']);self.assertTrue(proven['allocation_lock_held_during_callback'])
        self.assertFalse(proven['future_guest_lease']);self.assertFalse(proven['native_swap_completed'])
        rejected_consumer=continued(json.dumps(continuity),json.dumps(image_fields+[0]))
        self.assertTrue(rejected_consumer['published_copy_continuity_verified'])
        self.assertFalse(rejected_consumer['consumer_returned_success'])
        self.assertFalse(continued('[0]','[0]')['published_copy_continuity_verified'])
        for raw in ('[0]','[]','null','{}','[true]','[1]','[0,0]',json.dumps(continuity+[0])):
            with self.assertRaises(trace.TraceError):continued(raw)
        for field in range(4):
            for bit in range(32):
                bad=continuity.copy();bad[field]^=1<<bit
                with self.assertRaises(trace.TraceError):continued(json.dumps(bad))
        for bad in ([True]+continuity[1:],continuity[:-1]+[True],continuity[:-1]+[-1]):
            with self.assertRaises(trace.TraceError):continued(json.dumps(bad))
        with self.assertRaises(trace.TraceError):continued(json.dumps(continuity),'[0]')
        with self.assertRaises(trace.TraceError):
            swap_image(json.dumps(image_fields)+'\nstopped_native_swap_continuity='+json.dumps(continuity))
        for index in range(9):
            for bit in range(32):
                bad=fields.copy();bad[index]^=1<<bit
                with self.subTest(color_field=index,bit=bit),self.assertRaises(trace.TraceError):
                    color(','.join(map(str,bad)))
        for raw in ('bad','01,25,28,4,2,3686400,3686400,4,1',good+',0',good+',',
                    '0,25,28,4,2,3686400,3686400,4,1','1,25,4294967296,4,2,3686400,3686400,4,1'):
            with self.subTest(color=raw),self.assertRaises(trace.TraceError):color(raw)
        for cursor in (25,27,29):
            with self.assertRaises(trace.TraceError):color(good,cursor)
        with self.assertRaises(trace.TraceError):color(good,prefix=warm)
        with self.assertRaises(trace.TraceError):color(good,prefix=warm_state+'native_owned_color_batch='+good+'\n')
        with self.assertRaises(trace.TraceError):color(good,prefix=warm_state.replace('native_ring_restart=1,31\n',''))
        with self.assertRaises(trace.TraceError):color(good,prefix=warm_state.replace('native_ring_warm_coherency=1,0,4096,16777216\n',''))
        for length in range(7):
            for history in itertools.product('CS', repeat=length):
                records = ''
                for kind in 'CS':
                    count = history.count(kind)
                    if not count:
                        continue
                    last = 22 + 3 * (max(i for i,v in enumerate(history) if v == kind) + 1)
                    records += (f'native_warm_empty_cache_coherency={count},{last},0,4096,16777216\n'
                        if kind == 'C' else f'native_warm_state_batches={count},{last},{2*count},{5*count},{count}\n')
                result = parse(22+3*length, '1,28,0,4096,16777216', '1,31,2,5,1', warm+records)
                self.assertEqual(result.graphics_ring['ring_read_word'],22+3*length)
                self.assertEqual(result.native_empty_cache_coherency['read_word'],28)
                if 'C' in history:
                    self.assertEqual(result.native_warm_empty_cache_coherency['ring_generation'],1)
                    self.assertEqual(result.native_warm_empty_cache_coherency['batches'],history.count('C'))
                    self.assertFalse(result.native_warm_empty_cache_coherency['range_uploaded'])
        for raw in ('0,28,0,4096,16777216','1,25,0,4096,16777216','2,28,0,4096,16777216',
                    '1,28,1,4096,16777216','1,28,0,0,16777216','1,28,0,4096,0',
                    '1,28,536870912,4096,16777216','1,28,0,4294967296,16777216','01,28,0,4096,16777216','bad'):
            with self.subTest(warm_cache=raw), self.assertRaises(trace.TraceError):
                parse(28,'1,28,0,4096,16777216','1,31,2,5,1',warm_state+'native_warm_empty_cache_coherency='+raw+'\n')
        # Two plausible per-type counters can still describe an impossible order.
        for state_raw,cache_raw in (('4,34,8,20,4','1,34,0,4096,16777216'),
                                   ('4,34,8,20,4','1,25,0,4096,16777216'),
                                   ('1,37,2,5,1','4,25,0,4096,16777216')):
            with self.assertRaises(trace.TraceError):
                parse(37,'1,28,0,4096,16777216','1,31,2,5,1',warm+
                    'native_warm_state_batches='+state_raw+'\nnative_warm_empty_cache_coherency='+cache_raw+'\n')
        with self.assertRaises(trace.TraceError):
            parse(25,'1,28,0,4096,16777216','1,31,2,5,1',base+'native_ring_restart=1,31\n'+
                'native_warm_empty_cache_coherency=1,25,0,4096,16777216\n')
        for raw in ('0,25,2,12,1','1,22,2,12,1','2,25,2,12,1','1,25,65,12,1',
                    '1,25,2,257,1','1,25,2,12,129','1,25,2,4294967296,1','bad'):
            with self.subTest(warm_state=raw),self.assertRaises(trace.TraceError):
                parse(25,'1,28,0,4096,16777216','1,31,2,5,1',warm+'native_warm_state_batches='+raw+'\n')
        with self.assertRaises(trace.TraceError):
            parse(25,'1,28,0,4096,16777216','1,31,2,5,1',warm_state.replace('native_ring_warm_coherency=1,0,4096,16777216\n',''))

    def test_stopped_gpu_submission_is_framed_not_executed(self):
        access = {'address': 0x7FC80714, 'width': 4, 'write': 1, 'region': 1, 'value': 19}
        ring = {'ring_bytes': 65536, 'ring_read_word': 0}
        packet = [0, 3, 18, 0x48, 0, 0, 0, 0]
        def parse(raw='5,0,0,0,0,19,19,1', packets=None, a=None, r=None):
            return trace.parse_stopped_gpu_submission(raw, [packet] if packets is None else packets,
                access if a is None else a, ring if r is None else r)
        result = parse()
        self.assertFalse(result['executed'])
        self.assertTrue(result['payloads_suppressed'])
        self.assertEqual(result['packet_count'], 1)
        for raw in ('0,0,0,0,0,19,19,1', '5,0,0,0,0,18,19,1', '5,0,0,0,0,19,18,1',
                    '5,1,0,0,0,19,19,1', '5,0,0,0,0,19,19,0', '05,0,0,0,0,19,19,1', 'bad'):
            with self.subTest(raw=raw), self.assertRaises(trace.TraceError): parse(raw)
        for index, value in ((0, 1), (1, 5), (2, 19), (3, 128), (4, 1), (5, 1), (6, 2), (7, 1)):
            bad = packet.copy(); bad[index] = value
            with self.subTest(index=index), self.assertRaises(trace.TraceError): parse(packets=[bad])
        for field, value in (('address', 0x7FC8112C), ('width', 8), ('write', 0), ('region', 0)):
            with self.subTest(field=field), self.assertRaises(trace.TraceError): parse(a=dict(access, **{field:value}))
        for status in (3, 4):
            raw = f'{status},{4 if status == 4 else 0},0,0,0,19,0,0'
            self.assertEqual(parse(raw, packets=[])['status'], status)
        self.assertEqual(parse('2,0,0,0,0,0,0,0', packets=[], a=dict(access, value=16384))['status'], 2)
        self.assertEqual(trace.parse_stopped_gpu_submission('1,0,0,0,0,0,0,0', [], access, None)['status'], 1)
        self.assertIsNone(trace.parse_stopped_gpu_submission(None, [], None, None))
        with self.assertRaises(trace.TraceError): trace.parse_stopped_gpu_submission(None, [packet], access, ring)

    def test_stopped_indirect_graph_retains_only_bounded_metadata(self):
        root = {'status': 5, 'word_count': 3, 'packet_count': 1, 'packets': [[0,3,2,63,0,0,0,0]]}
        filler = [0,2,0,0,0,0,0,0]
        good = [0,0,0,0,0,0,4,2,[[0,0,4096,1,1,1,[filler]]]]
        def parse(data=good, submission=root):
            return trace.parse_stopped_gpu_indirect_graph(json.dumps(data), submission)
        result = parse()
        self.assertFalse(result['executed'])
        self.assertTrue(result['writers_stopped'])
        self.assertEqual(result['nodes'][0]['physical_address'], 4096)
        for index, value in ((0,11), (1,1), (2,1), (3,1), (4,1), (5,1), (6,5), (7,3), (8,[])):
            bad = json.loads(json.dumps(good)); bad[index] = value
            with self.subTest(index=index), self.assertRaises(trace.TraceError): parse(bad)
        for index, value in ((0,1), (1,1), (2,1), (3,2), (4,0), (5,2), (6,[])):
            bad = json.loads(json.dumps(good)); bad[8][0][index] = value
            with self.subTest(node_index=index), self.assertRaises(trace.TraceError): parse(bad)
        for index, value in ((0,1), (1,5), (2,1), (3,128), (4,1), (5,1), (6,1), (7,1)):
            bad = json.loads(json.dumps(good)); bad[8][0][6][0][index] = value
            with self.subTest(packet_index=index), self.assertRaises(trace.TraceError): parse(bad)
        for value in ('bad', '[]', '[true,0,0,0,0,0,0,0,[]]', '[' * 15000, ' ' * 16001):
            with self.assertRaises(trace.TraceError): trace.parse_stopped_gpu_indirect_graph(value, root)
        for invalid in (None, dict(root, status=4), dict(root, packets=[[0,3,2,16,0,0,0,0]])):
            with self.assertRaises(trace.TraceError): parse(submission=invalid)
        for failure in range(1,11):
            result = parse([failure,1,3,4 if failure == 7 else 0,0,0,0,0,[]])
            self.assertEqual(result['failure'], failure)
        # Global cap applies across siblings, including nodes whose packets
        # are omitted after earlier nodes consume the budget.
        many = [[i,2,0,0,0,0,0,0] for i in range(64)]
        root2 = dict(root, word_count=6, packet_count=2, packets=[*root['packets'], [3,3,2,63,0,0,0,0]])
        result = parse([0,0,0,0,0,0,71,67,[[0,0,4096,1,64,64,many], [0,3,8192,1,1,1,[]]]], root2)
        self.assertEqual(result['nodes'][1]['packets'], [])
        self.assertIsNone(trace.parse_stopped_gpu_indirect_graph(None, None))

    def test_stopped_shader_and_draw_metadata_does_not_claim_execution(self):
        root = {'status':5,'word_count':8,'packet_count':2,'packets':[[0,3,5,43,0,0,0,0],[6,3,1,54,0,0,0,0]]}
        graph = {'failure':0,'nodes':[]}
        good = [0,0,0,0,0,[[0,0,0,3,1,0]],[[0,6,54,0,1,2,0,0,0,0,0]]]
        def parse(data=good,r=root,g=graph): return trace.parse_stopped_gpu_work(json.dumps(data),r,g)
        result = parse()
        self.assertFalse(result['executed']); self.assertFalse(result['index_payload_validated'])
        self.assertEqual(result['nonzero_index_requests'],0)
        active = json.loads(json.dumps(good)); active[6][0][3] = 3
        self.assertEqual(parse(active)['nonzero_index_requests'],1)
        for index,value in ((0,1),(1,1),(2,1),(3,1),(4,1)):
            bad = json.loads(json.dumps(good)); bad[index] = value
            with self.subTest(index=index),self.assertRaises(trace.TraceError): parse(bad)
        for index,value in ((0,1),(1,1),(2,2),(3,4),(4,0),(5,4)):
            bad = json.loads(json.dumps(good)); bad[5][0][index] = value
            with self.subTest(shader_index=index),self.assertRaises(trace.TraceError): parse(bad)
        for index,value in ((0,1),(1,7),(2,34),(3,65536),(4,64),(5,4),(6,4),(7,2),(8,2),(9,1),(10,1)):
            bad = json.loads(json.dumps(good)); bad[6][0][index] = value
            with self.subTest(draw_index=index),self.assertRaises(trace.TraceError): parse(bad)
        for index in (5,6):
            bad = json.loads(json.dumps(good)); bad[index] *= 2
            with self.assertRaises(trace.TraceError): parse(bad)
        for raw in ('bad','[]','[true,0,0,0,0,[],[]]','['*15000,' '*16001):
            with self.assertRaises(trace.TraceError): trace.parse_stopped_gpu_work(raw,root,graph)
        for g in (None,dict(graph,failure=1)):
            with self.assertRaises(trace.TraceError): parse(g=g)
        self.assertEqual(parse([2,0,12,0,34,[],[]])['failure'],12)
        self.assertIsNone(trace.parse_stopped_gpu_work(None,None,None))

    def test_ordered_constant_initialization_joins_bounded_evidence_without_execution(self):
        root = {'status':5, 'word_count':100}
        graph = {'failure':0, 'nodes':[]}
        work = {'error_kind':0, 'shaders':[[0,0,0,12,1,0],[0,16,1,6,1,0]],
                'draws':[[0,30,0x36,1,1,2,0,0,0,0,0]]}
        memory = {'failure':0,'draw_requests':1,'other_register_writes':8}
        proofs = {'accepted_count':2}
        good = [0,0,0,1,2,1,9,8,2,[[0x81,0x80010000,0x80010000],[0x82,0,0]],[[0,30,8,0]]]
        def parse(data=good, r=root, g=graph, w=work, m=memory, s={'planned':True}, p=proofs):
            return trace.parse_stopped_constant_initialization(json.dumps(data),r,g,w,m,s,p)
        result = parse()
        self.assertTrue(result['ordered_shader_and_masked_draw_subplan'])
        self.assertTrue(result['native_probe_linkage_validated'])
        for key in ('complete_batch_validated','hardware_register_side_effects_implemented','title_raster_state_replayed',
                    'guest_memory_written','gpu_executed'):
            self.assertFalse(result[key])
        for index, value in ((0,14),(0,1),(1,1),(2,1),(3,0),(3,2),(4,1),(4,9),(5,65537),(6,0),(6,1025),(7,7),(8,1),(8,10)):
            bad = json.loads(json.dumps(good)); bad[index] = value
            with self.subTest(index=index,value=value),self.assertRaises(trace.TraceError): parse(bad)
        for partial in ([[0x81,1,0],[0x82,0,0]],[[0x81,0,0xFFFFFFFF],[0x82,0,0]],
                        [[0x8000,0,0],[0x82,0,0]],[[0x82,0,0],[0x81,0,0]],[[0x81,0,0],[0x81,0,0]],[],[[True,0,0],[0x82,0,0]]):
            bad = json.loads(json.dumps(good)); bad[9] = partial
            with self.assertRaises(trace.TraceError): parse(bad)
        for records in ([],[[0,30,16,0]],[[0,30,8,16]],[[0,31,8,0]],[[1,30,8,0]],[[0,30,8]],[[0,30,True,0]]):
            bad = json.loads(json.dumps(good)); bad[10] = records
            with self.assertRaises(trace.TraceError): parse(bad)
        for change in ({'r':None},{'r':dict(root,status=4)},{'g':None},{'g':dict(graph,failure=1)},
                       {'w':None},{'w':dict(work,error_kind=1)},{'m':None},{'m':dict(memory,failure=1)},
                       {'s':None},{'p':None},{'p':{'accepted_count':1}}):
            with self.assertRaises(trace.TraceError): parse(**change)
        for index, value in ((2,0x22),(3,0),(4,2),(5,1),(6,1),(7,1),(8,1),(9,1),(10,1)):
            changed = json.loads(json.dumps(work)); changed['draws'][0][index] = value
            with self.assertRaises(trace.TraceError): parse(w=changed)
        for failure in range(1,14):
            result = parse([failure,0,1,0,0,0,0,0,0,[],[]],w=None,m=None,s=None,p=None)
            self.assertFalse(result['native_probe_linkage_validated'])
        for raw in ('bad','[]','['*15000,' '*16001):
            with self.assertRaises(trace.TraceError):
                trace.parse_stopped_constant_initialization(raw,root,graph,work,memory,{'planned':True},proofs)
        self.assertIsNone(trace.parse_stopped_constant_initialization(None,None,None,None,None,None,None))

    def test_native_shader_proofs_are_source_bound_without_execution_claims(self):
        work = {'error_kind':0, 'shaders':[[0,2,0,24,1,0],[1,8,1,9,1,0]]}
        good = [[0,2,0,0,0,6,5,2,0],[1,8,1,0,0,2,1,1,4]]
        def parse(data=good, source=work): return trace.parse_stopped_shader_proofs(json.dumps(data),source)
        result = parse()
        self.assertEqual(result['accepted_count'],2)
        for key in ('gpu_executed','linkage_validated','title_raster_state_replayed','hardware_float_edge_cases_validated'):
            self.assertFalse(result[key])
        for index, value in ((0,1),(1,3),(2,1),(3,14),(4,1),(5,0),(5,129),(6,0),(6,257),(7,0),(7,10),(8,257),(8,True)):
            changed = json.loads(json.dumps(good)); changed[0][index] = value
            with self.subTest(index=index,value=value), self.assertRaises(trace.TraceError): parse(changed)
        for data in ([],good[:1],good*2,good[::-1],{},[1],[[0]*8]):
            with self.assertRaises(trace.TraceError): parse(data)
        for raw in ('bad','['*15000,' '*16001):
            with self.assertRaises(trace.TraceError): trace.parse_stopped_shader_proofs(raw,work)
        for source in (None,dict(work,error_kind=1),dict(work,shaders=[[0,2,0,1026,1,0],work['shaders'][1]]),
                       dict(work,shaders=[[0,2,0,24,0,4096],work['shaders'][1]])):
            with self.assertRaises(trace.TraceError): parse(source=source)
        for failure in range(1,14):
            changed = [[0,2,0,failure,1,0,0,0,0],good[1]]
            self.assertEqual(parse(changed)['accepted_count'],1)
        self.assertIsNone(trace.parse_stopped_shader_proofs(None,None))

    def test_stopped_memory_requests_do_not_authorize_writes(self):
        root = {'status':5, 'word_count':8, 'packet_count':3, 'packets':[
            [0,0,1,0,0x578,0,0,0], [2,3,1,54,0,0,0,0], [4,3,3,88,0,0,0,0]]}
        graph = {'failure':0, 'nodes':[]}
        good = [0,0,0,0x20000,1,1,[[0,0,0,4096,7,0,1,0],[0,4,1,8192,9,1,1,0]]]
        def parse(data=good, r=root, g=graph): return trace.parse_stopped_gpu_memory(json.dumps(data),r,g)
        result = parse()
        self.assertFalse(result['executed']); self.assertFalse(result['complete_hardware_effects_validated'])
        self.assertTrue(result['identifier_alias_checked'])
        self.assertEqual(result['unmodeled_scratch_mask_bits'],0x20000)
        self.assertTrue(result['explicit_writes'][1]['owned_writable_word'])
        self.assertFalse(result['explicit_writes'][1]['written'])
        self.assertFalse(result['explicit_writes'][0]['byte_order_validated'])
        self.assertTrue(result['explicit_writes'][1]['byte_order_validated'])
        profiled = json.loads(json.dumps(good)); profiled[3] = 0
        for item in profiled[6]: item.append(1)
        profiled_result = parse(profiled)
        self.assertTrue(profiled_result['explicit_writes'][0]['byte_order_validated'])
        self.assertEqual(profiled_result['explicit_writes'][0]['byte_order_basis'], 'libxenon-xenos-driver-swap2-profile')
        self.assertFalse(profiled_result['explicit_writes'][0]['hardware_readback_measured'])
        staging = trace.parse_stopped_word_staging('1,1,2,8', profiled_result)
        self.assertEqual(staging['native_buffer_bytes'], 8)
        self.assertFalse(staging['complete_batch_validated'])
        self.assertFalse(staging['guest_memory_written'])
        self.assertTrue(staging['live_guest_revalidation_required'])
        for raw in ('2,0,2,8','1,1,1,4','1,1,2,9','-1,1,2,8','1,1,2,9999'):
            with self.assertRaises(trace.TraceError): trace.parse_stopped_word_staging(raw,profiled_result)
        with self.assertRaises(trace.TraceError): trace.parse_stopped_word_staging('1,1,2,8',result)
        with self.assertRaises(trace.TraceError): trace.parse_stopped_word_staging('1,1,2,8',None)
        self.assertIsNone(trace.parse_stopped_word_staging(None,None))
        for owner, value in ((0,2),(1,0)):
            bad = json.loads(json.dumps(profiled)); bad[6][owner][8] = value
            with self.assertRaises(trace.TraceError): parse(bad)
        for index, value in ((0,1),(1,1),(2,1),(3,1),(4,1048577),(5,65)):
            bad = json.loads(json.dumps(good)); bad[index] = value
            with self.assertRaises(trace.TraceError): parse(bad)
        for index, value in ((0,1),(1,1),(2,2),(3,4097),(3,0x20000000),(5,2),(6,2),(7,2)):
            bad = json.loads(json.dumps(good)); bad[6][0][index] = value
            with self.assertRaises(trace.TraceError): parse(bad)
        bad = json.loads(json.dumps(good)); bad[6][0][2] = 1
        with self.assertRaises(trace.TraceError): parse(bad)
        bad_root = json.loads(json.dumps(root)); bad_root['packets'][0][4] = 0x500
        with self.assertRaises(trace.TraceError): parse(r=bad_root)
        self.assertEqual(parse([4,0,0,0,0,0,[]])['failure'],4)
        self.assertIsNone(trace.parse_stopped_gpu_memory(None,None,None))
        with self.assertRaises(trace.TraceError): parse(g=None)

    def test_title_termination_count_is_bounded(self):
        for value in (0, 1, 2, 64):
            parsed = trace.parse_trace_protocol(valid_protocol() + f'title_termination_registrations={value}\n', 1, 10, 2)
            self.assertEqual(parsed.title_termination_registrations, value)
        for value in ('-1', '65', '99999999999999999', 'bad', '01'):
            with self.assertRaises(trace.TraceError):
                trace.parse_trace_protocol(valid_protocol() + f'title_termination_registrations={value}\n', 1, 10, 2)

    def test_capture_protocol_requires_validated_work_and_explicit_status(self):
        prefix = invalid_indirect_protocol().replace('invalid-indirect-target', 'unsupported-scalar-access')
        prefix += ('scalar_address=2143815444\nscalar_width=4\nscalar_value=6\nscalar_write=1\nscalar_region=1\n'
                   'graphics_initialized=1\ngraphics_ring_physical=0\ngraphics_ring_virtual=2684354560\n'
                   'graphics_ring_bytes=65536\ngraphics_ring_read_word=0\n'
                   'gpu_submission=5,0,0,0,0,6,6,1\ngpu_packet=0,3,5,43,0,0,0,0\n'
                   'gpu_indirect_graph=[0,0,0,0,0,0,6,1,[]]\n'
                   'gpu_stopped_work=[0,0,0,0,0,[[0,0,0,3,1,0]],[]]\n')
        blob = 'stopped_shader_blob=0,0,0,' + bytes(range(12)).hex() + '\n'
        suffix = 'stopped_shader_capture=1\n' + blob
        result = trace.parse_trace_protocol(prefix + suffix, 1, 10, 2)
        self.assertEqual(result.private_shader_capture['blobs'][0]['bytes'], bytes(range(12)))
        pm4_bytes = struct.pack('>III', 0xC0042B00, 0, 3) + bytes(range(12))
        pm4_suffix = 'stopped_pm4_capture=1\nstopped_pm4_blob=0,' + pm4_bytes.hex() + '\n'
        self.assertEqual(trace.parse_trace_protocol(prefix + suffix + pm4_suffix, 1, 10, 2)
            .private_pm4_capture['blobs'][0]['bytes'], pm4_bytes)
        for pm4_bad in (pm4_suffix + pm4_suffix, pm4_suffix.replace('stopped_pm4_capture=1\n', ''),
                        'stopped_pm4_capture=1\n'):
            with self.assertRaises(trace.TraceError): trace.parse_trace_protocol(prefix + pm4_bad, 1, 10, 2)
        self.assertNotIn('bytes', result.stopped_gpu_work)
        for data in (prefix + blob, prefix + suffix + blob, prefix + suffix + 'stopped_shader_capture=1\n',
                     prefix + 'stopped_shader_capture=1\n', valid_protocol() + suffix):
            with self.assertRaises(trace.TraceError): trace.parse_trace_protocol(data, 1, 10, 2)
        self.assertIsNone(trace.parse_trace_protocol(valid_protocol(), 1, 10, 2).private_shader_capture)

    def test_capture_protocol_external_shader_and_larger_opaque_predicated_batch(self):
        child = struct.pack('>IIII', 0xC0012700, 4097, 3, 0xC0003601) + struct.pack('>I',0x30888)
        child += struct.pack('>I',0x80000000) * (2784 - 5)
        _, packets = trace.pm4_capture.decode_packets(child)
        graph = [0,0,0,0,0,0,2787,len(packets)+1,[[0,0,8192,1,2784,len(packets),packets[:64]]]]
        prefix = invalid_indirect_protocol().replace('invalid-indirect-target','unsupported-scalar-access')
        prefix += ('scalar_address=2143815444\nscalar_width=4\nscalar_value=3\nscalar_write=1\nscalar_region=1\n'
            'graphics_initialized=1\ngraphics_ring_physical=0\ngraphics_ring_virtual=2684354560\n'
            'graphics_ring_bytes=65536\ngraphics_ring_read_word=0\n'
            'gpu_submission=5,0,0,0,0,3,3,1\ngpu_packet=0,3,2,63,0,0,0,0\n'
            'gpu_indirect_graph=' + json.dumps(graph) + '\n'
            'gpu_stopped_work=[0,0,0,0,0,[[1,0,1,3,0,4096]],[[1,3,54,3,8,2,0,0,0,0,1]]]\n')
        suffix = ('stopped_shader_capture=1\nstopped_shader_blob=1,0,1,0,4096,' + bytes(range(12)).hex() + '\n'
            'stopped_pm4_capture=1\nstopped_pm4_blob=0,' + struct.pack('>III',0xC0013F00,8192,2784).hex() + '\n'
            'stopped_pm4_blob=1,' + child.hex() + '\n')
        result = trace.parse_trace_protocol(prefix+suffix,1,10,2)
        self.assertEqual(result.private_pm4_capture['blobs'][1]['bytes'],child)
        self.assertEqual(result.private_shader_capture['blobs'][0]['physical_address'],4096)
        self.assertEqual(result.stopped_gpu_work['draws'][0][-1],1)
        physical = ('stopped_physical_capture=1\nstopped_physical_requests=[[4096,12]]\n'
            'stopped_physical_blob=4096,12,' + bytes(range(12)).hex() + '\n')
        parsed = trace.parse_trace_protocol(prefix+suffix+physical,1,10,2)
        self.assertEqual(parsed.private_physical_capture['requests'],[[4096,12]])
        self.assertEqual(parsed.private_physical_capture['blobs'][0]['bytes'],bytes(range(12)))
        for bad in (physical.replace('4096,12,','8192,12,'),physical+physical,
                    physical.replace('stopped_physical_capture=1\n','')):
            with self.assertRaises(trace.TraceError):trace.parse_trace_protocol(prefix+suffix+bad,1,10,2)
        with self.assertRaises(trace.TraceError):
            trace.parse_trace_protocol(prefix+suffix.replace('1,0,1,0,4096,','1,0,1,'),1,10,2)

    def test_graphics_initialization_protocol_is_explicit_and_boolean(self):
        self.assertIsNone(trace.parse_trace_protocol(valid_protocol(), 1, 10, 2).graphics_initialized)
        for value in (0, 1):
            self.assertEqual(trace.parse_trace_protocol(valid_protocol() + f'graphics_initialized={value}\n', 1, 10, 2).graphics_initialized, bool(value))
        for value in ('-1', '2', 'true', '01'):
            with self.assertRaises(trace.TraceError):
                trace.parse_trace_protocol(valid_protocol() + f'graphics_initialized={value}\n', 1, 10, 2)

    def test_critical_section_rejection_snapshot_matches_exact_stop(self):
        base=valid_protocol().replace('unimplemented-import','rejected-import').replace('__imp__Synthetic','__imp__RtlEnterCriticalSection')
        arguments={'stack_pointer':0x70000000,**{f'r{i}':0 for i in range(3,11)}}
        arguments['r3']=0xFEDCBA9800005000
        base+=''.join(f'import_{key}={value}\n' for key,value in arguments.items())
        state=[4,0x5000,2,1,1,1,1,0x01000000,0,0,0,0,1,1,0,1]
        def parse(row,prefix=base):
            return trace.parse_trace_protocol(prefix+'critical_section_rejection='+','.join(map(str,row))+'\n',1,10,2).critical_section_rejection
        result=parse(state);self.assertEqual(result['reason'],'unsupported-contention');self.assertEqual(result['registry_owner'],1)
        self.assertTrue(result['storage_matches']);self.assertFalse(result['guest_memory_modified'])
        mismatch=state.copy();mismatch[0]=3;mismatch[7]=0xAA000000;mismatch[15]=0
        self.assertEqual(parse(mismatch)['live_header_word'],0xAA000000)
        for index,value in ((0,0),(0,2),(1,0x5004),(2,1),(2,0),(3,0),(3,2),(6,0),(11,1),(13,3),(15,0)):
            row=state.copy();row[index]=value
            with self.subTest(index=index),self.assertRaises(trace.TraceError):parse(row)
        for row in (state[:-1],state+[0],['04']+state[1:],[1<<32]+state[1:]):
            with self.assertRaises(trace.TraceError):parse(row)
        with self.assertRaises(trace.TraceError):parse(state,valid_protocol())
        field='critical_section_rejection='+','.join(map(str,state))+'\n'
        with self.assertRaises(trace.TraceError):parse(state,base+field)

    def test_native_high_priority_tiers_requires_explicit_cooperative_evidence(self):
        base=valid_protocol()+'workers_created=1\nworkers_entered=1\ncontext_switches=1\n'
        field='native_high_priority_tiers=7\n'
        policy=trace.parse_trace_protocol(base+field,1,10,2,cooperative=True).native_high_priority_tiers
        self.assertEqual(policy['accepted_requests'],7);self.assertEqual(policy['slice_weight'],8)
        self.assertEqual(policy['increments'],[3,4])
        self.assertFalse(policy['console_priority_class_verified']);self.assertFalse(policy['host_os_priority_changed'])
        for raw in ('-1','01','1,0','18446744073709551616',''):
            with self.assertRaises(trace.TraceError):trace.parse_trace_protocol(base+'native_high_priority_tiers='+raw+'\n',1,10,2,cooperative=True)
        with self.assertRaises(trace.TraceError):trace.parse_trace_protocol(base+field*2,1,10,2,cooperative=True)
        with self.assertRaises(trace.TraceError):trace.parse_trace_protocol(valid_protocol()+field,1,10,2)

    def test_native_critical_section_waits_are_joined_and_explicit(self):
        base=valid_protocol()+'workers_created=1\nworkers_entered=1\ncontext_switches=1\n'
        field='native_critical_section_waits=5,3,2,0\n'
        waits=trace.parse_trace_protocol(base+field,1,10,2,cooperative=True).native_critical_section_waits
        self.assertEqual(waits['registered_waits'],5);self.assertEqual(waits['acquired_waits'],3)
        self.assertFalse(waits['console_scheduling_verified']);self.assertFalse(waits['guest_wait_lists_modified'])
        for raw in ('5,3,1,0','5,3,1,1','05,3,2,0','5,3,2','5,3,2,0,0','18446744073709551616,0,0,0'):
            with self.assertRaises(trace.TraceError):trace.parse_trace_protocol(base+'native_critical_section_waits='+raw+'\n',1,10,2,cooperative=True)
        with self.assertRaises(trace.TraceError):trace.parse_trace_protocol(valid_protocol()+field,1,10,2)

    def test_dispatcher_object_diagnostic_is_owned_bounded_and_stop_specific(self):
        base=valid_protocol().replace('unimplemented-import','rejected-import').replace('__imp__Synthetic','__imp__KeWaitForMultipleObjects')
        arguments={'stack_pointer':0x70000000,**{f'r{i}':0 for i in range(3,11)}}
        arguments.update(r3=2,r4=0x70000100)
        base+=''.join(f'import_{key}={value}\n' for key,value in arguments.items())
        rows=[[0x10000000,1,0x01000400,1,1],[0x2000,0,0,0,0]]
        def parse(data,prefix=base):
            return trace.parse_trace_protocol(prefix+'dispatcher_wait_objects='+json.dumps(data)+'\n',1,10,2).dispatcher_wait_objects
        evidence=parse(rows)
        self.assertTrue(evidence[0]['event_validated'])
        self.assertTrue(parse([[0x10000000,1,0x01000050,0,1],rows[1]])[0]['event_validated'])
        self.assertTrue(parse([[0x10000000,1,0x00088000,0,1],rows[1]])[0]['event_validated'])
        self.assertIsNone(evidence[1]['header']);self.assertIsNone(evidence[1]['signal'])
        self.assertFalse(evidence[1]['owned_header_read'])
        self.assertFalse(parse([[0x10000000,1,0x05000400,7,0],rows[1]])[0]['event_validated'])
        for data in ([],rows[:1],rows*33,{},[[1,2,3],rows[1]]):
            with self.assertRaises(trace.TraceError):parse(data)
        for index,value in ((0,0),(0,0xFFFFFFFF),(1,2),(1,False),(2,0x05000000),(3,2),(4,2)):
            changed=[row[:] for row in rows];changed[0][index]=value
            with self.assertRaises(trace.TraceError):parse(changed)
        for index in (2,3,4):
            changed=[row[:] for row in rows];changed[1][index]=1
            with self.assertRaises(trace.TraceError):parse(changed)
        for prefix in (valid_protocol(),base.replace('rejected-import','unimplemented-import'),
                       base.replace('KeWaitForMultipleObjects','KeWaitForSingleObject'),
                       base.replace('import_r3=2','import_r3=65'),base.replace('import_r4=1879048448','import_r4=0')):
            with self.assertRaises(trace.TraceError):parse(rows,prefix)
        field='dispatcher_wait_objects='+json.dumps(rows)+'\n'
        with self.assertRaises(trace.TraceError):trace.parse_trace_protocol(base+field*2,1,10,2)

    def test_scalar_access_protocol_checks_region_width_and_stop(self):
        base = invalid_indirect_protocol().replace('invalid-indirect-target', 'unsupported-scalar-access')
        fields = {'address': 0x7FC83214, 'width': 4, 'value': 7, 'write': 1, 'region': 1}
        def suffix(data):
            return ''.join(f'scalar_{key}={value}\n' for key, value in data.items())
        self.assertEqual(trace.parse_trace_protocol(base + suffix(fields), 1, 10, 2).scalar_access, fields)
        for key, value in (('address', 1 << 32), ('address', 0), ('width', 3), ('width', 0),
                           ('value', 1 << 32), ('write', 0), ('write', 2), ('region', 2), ('value', -1)):
            with self.assertRaises(trace.TraceError):
                trace.parse_trace_protocol(base + suffix({**fields, key: value}), 1, 10, 2)
        for bad in (base, base + 'scalar_address=1\n', valid_protocol() + suffix(fields),
                    base + suffix(fields) + 'scalar_value=7\n'):
            with self.assertRaises(trace.TraceError):
                trace.parse_trace_protocol(bad, 1, 10, 2)
        overflow = {**fields, 'address': 0xFFFFFFFF, 'width': 8, 'region': 2, 'value': (1 << 64) - 1}
        self.assertEqual(trace.parse_trace_protocol(base + suffix(overflow), 1, 10, 2).scalar_access, overflow)
        for address in (0x7FEA0000, 0x7FEA1804, 0x7FEAFFFF, 0x7FE9FFFE):
            xma = {**fields, 'address': address, 'region': 5}
            self.assertEqual(trace.parse_trace_protocol(base + suffix(xma), 1, 10, 2).scalar_access, xma)
        for address in (0x7FE9FFFC, 0x7FEB0000, 0x7FC83214):
            with self.assertRaises(trace.TraceError):
                trace.parse_trace_protocol(base + suffix({**fields, 'address': address, 'region': 5}), 1, 10, 2)

    def test_import_arguments_are_complete_private_unsigned_snapshots(self):
        fields = {"stack_pointer": 0x70001000, **{f"r{i}": (1 << 64) - i for i in range(3, 11)}}
        suffix = ''.join(f'import_{key}={value}\n' for key, value in fields.items())
        self.assertEqual(trace.parse_trace_protocol(valid_protocol() + suffix, 1, 10, 2).import_arguments, fields)
        self.assertIsNone(trace.parse_trace_protocol(valid_protocol(), 1, 10, 2).import_arguments)
        for value in ('-1', str(1 << 64), '0x1', 'private', '01'):
            with self.assertRaises(trace.TraceError):
                trace.parse_trace_protocol(valid_protocol() + suffix.replace('import_stack_pointer=1879052288',
                    f'import_stack_pointer={value}'), 1, 10, 2)
        for output in (valid_protocol() + 'import_r3=1\n', invalid_indirect_protocol() + suffix,
                       valid_protocol() + suffix + 'import_r3=1\n'):
            with self.assertRaises(trace.TraceError):
                trace.parse_trace_protocol(output, 1, 10, 2)

    def test_content_mount_rejection_mask_is_redacted_and_boundary_checked(self):
        base = valid_protocol().replace('unimplemented-import', 'rejected-import').replace('__imp__Synthetic', '__imp__XamContentCreateEx')
        self.assertEqual(trace.parse_trace_protocol(base + 'content_mount_rejection=513\n', 1, 10, 2).content_mount_rejection, 513)
        for value in ('0', '-1', '8192', 'private', '0x1'):
            with self.assertRaises(trace.TraceError):
                trace.parse_trace_protocol(base + f'content_mount_rejection={value}\n', 1, 10, 2)
        with self.assertRaises(trace.TraceError):
            trace.parse_trace_protocol(valid_protocol() + 'content_mount_rejection=1\n', 1, 10, 2)

    def test_native_indirect_resolver_with_synthetic_mapping(self) -> None:
        compiler = shutil.which("c++")
        if compiler is None:
            self.skipTest("host C++ compiler is unavailable")
        root = MODULE_PATH.parents[2]
        with tempfile.TemporaryDirectory() as temporary:
            executable = Path(temporary) / "indirect-resolver-test"
            compile_result = subprocess.run(
                [
                    compiler,
                    "-std=c++17",
                    "-Wall",
                    "-Wextra",
                    "-Wpedantic",
                    "-Werror",
                    "-I", str(MODULE_PATH.parent / "tests/synthetic_ppc"),
                    "-I", str(MODULE_PATH.parent),
                    "-I", str(root / "android/app/src/main/cpp"),
                    str(MODULE_PATH.parent / "indirect_call_resolver.cpp"),
                    str(root / "android/app/src/main/cpp/guest_execution_guard.cpp"),
                    str(MODULE_PATH.parent / "tests/indirect_call_resolver_test.cpp"),
                    "-o", str(executable),
                ],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(compile_result.returncode, 0, compile_result.stderr)
            run_result = subprocess.run(
                [str(executable)], check=False, capture_output=True, text=True
            )
            self.assertEqual(run_result.returncode, 0, run_result.stderr)

    def test_ranges_require_alignment_and_non_overlap(self) -> None:
        image = trace.GuestRange("image", 0x40000000, 0x2000)
        heap = trace.GuestRange("heap", 0x10000000, 0x4000)
        stack = trace.GuestRange("stack", 0x70000000, 0x10000)
        physical = trace.PhysicalArenaInputs(
            trace.GuestRange("physical_memory", 0x60000000, 0x10000),
            0,
        )
        trace.validate_ranges(image, heap, stack, physical=physical)
        with self.assertRaisesRegex(trace.TraceError, "overlap"):
            trace.validate_ranges(
                image,
                trace.GuestRange("heap", 0x40000000, 0x1000),
                stack,
            )
        with self.assertRaisesRegex(trace.TraceError, "aligned"):
            trace.validate_ranges(
                image,
                trace.GuestRange("heap", 0x10000001, 0x4000),
                stack,
            )
        with self.assertRaisesRegex(trace.TraceError, "overlap"):
            trace.validate_ranges(
                image,
                heap,
                stack,
                physical=trace.PhysicalArenaInputs(
                    trace.GuestRange("physical_memory", image.base, 0x1000),
                    0,
                ),
            )
        with self.assertRaisesRegex(trace.TraceError, "physical address range"):
            trace.validate_ranges(
                image,
                heap,
                stack,
                physical=trace.PhysicalArenaInputs(
                    trace.GuestRange("physical_memory", 0x60000000, 0x2000),
                    0xFFFFF000,
                ),
            )

    def test_private_inputs_require_exact_chunks_ignore_and_no_symlink(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root, ppc, bridge, _ = make_synthetic_repo(temporary)
            self.assertEqual(len(trace.discover_chunks(ppc, 2)), 2)
            self.assertEqual(
                trace.validate_guarded_bridge(bridge, 2)["schema_version"], 2
            )
            trace.validate_private_directory(ppc, root, "PPC")
            with self.assertRaisesRegex(trace.TraceError, "expected 3"):
                trace.discover_chunks(ppc, 3)

            linked = root / "local-analysis/linked-ppc"
            linked.symlink_to(ppc, target_is_directory=True)
            with self.assertRaisesRegex(trace.TraceError, "symlink"):
                trace.validate_private_directory(linked, root, "PPC")

            outside = root / "tracked"
            outside.mkdir()
            with self.assertRaisesRegex(trace.TraceError, "local-analysis"):
                trace.validate_private_directory(outside, root, "PPC")

    def test_function_mapping_requires_bounded_sorted_exact_code_entries(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            _, ppc, _, _ = make_synthetic_repo(temporary)
            layout = trace.read_generated_layout(ppc)
            self.assertEqual(trace.validate_function_mapping_source(ppc, layout.code), 2)

            mapping_path = ppc / "ppc_func_mapping.cpp"
            valid_source = mapping_path.read_text(encoding="utf-8")
            mapping_path.write_text(
                valid_source.replace(
                    f"0x{SYNTHETIC_CODE_BASE + 4:X}",
                    f"0x{SYNTHETIC_CODE_BASE:X}",
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(trace.TraceError, "sorted and unique"):
                trace.validate_function_mapping_source(ppc, layout.code)

            mapping_path.write_text(
                valid_source.replace("    { 0, nullptr }\n", ""), encoding="utf-8"
            )
            with self.assertRaisesRegex(trace.TraceError, "null sentinel"):
                trace.validate_function_mapping_source(ppc, layout.code)

    def test_bridge_report_and_source_counts_must_agree(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            _, _, bridge, _ = make_synthetic_repo(temporary)
            report_path = bridge / "runtime-bridge-report.json"
            report = json.loads(report_path.read_text(encoding="utf-8"))
            report["unimplemented_import_stops"]["count"] = 2
            report_path.write_text(json.dumps(report), encoding="utf-8")
            with self.assertRaisesRegex(trace.TraceError, "partition counts"):
                trace.validate_guarded_bridge(bridge, 2)

    def test_decoded_image_is_absolute_exact_and_not_a_symlink(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root, _, _, image = make_synthetic_repo(temporary)
            self.assertEqual(
                trace.validate_decoded_image(image, 0x2000, root), image.resolve()
            )
            with self.assertRaisesRegex(trace.TraceError, "absolute"):
                trace.validate_decoded_image(Path("decoded.image.bin"), 0x2000, root)
            with self.assertRaisesRegex(trace.TraceError, "expected"):
                trace.validate_decoded_image(image, 0x1000, root)
            linked = root / "local-analysis/linked.image.bin"
            linked.symlink_to(image)
            with self.assertRaisesRegex(trace.TraceError, "symlink"):
                trace.validate_decoded_image(linked, 0x2000, root)

    def test_title_environment_uses_schema_two_import_and_full_security_range(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root, _, _, image_path = make_synthetic_repo(temporary)
            image = trace.GuestRange(
                "image", SYNTHETIC_IMAGE_BASE, SYNTHETIC_IMAGE_SIZE
            )
            title = trace.validate_title_environment_inputs(
                root / "local-analysis/default.xex",
                image_path,
                image,
                0x50000000,
                0x50001000,
                0x50002000,
                root,
            )
            self.assertEqual(title.header.size, 0x340)
            self.assertEqual(title.full_image.size, SYNTHETIC_SECURITY_IMAGE_SIZE)
            self.assertEqual(title.import_slot, SYNTHETIC_IMPORT_SLOT)
            self.assertEqual(title.entry_point, SYNTHETIC_ENTRY_POINT)
            self.assertEqual(title.data_import_slots, (SYNTHETIC_IMPORT_SLOT, SYNTHETIC_IMPORT_SLOT + 4))
            self.assertIsNone(title.hardware_info_slot)
            self.assertEqual(title.video_device_slots, ())
            trace.validate_ranges(
                image,
                trace.GuestRange("heap", 0x10000000, 0x4000),
                trace.GuestRange("stack", 0x70000000, 0x10000),
                title,
            )

            overlapping_title = trace.dataclasses.replace(
                title,
                header=trace.GuestRange(
                    "xex_header",
                    SYNTHETIC_IMAGE_BASE + SYNTHETIC_IMAGE_SIZE,
                    0x1000,
                ),
            )
            with self.assertRaisesRegex(trace.TraceError, "overlap"):
                trace.validate_ranges(
                    image,
                    trace.GuestRange("heap", 0x10000000, 0x4000),
                    trace.GuestRange("stack", 0x70000000, 0x10000),
                    overlapping_title,
                )

    def test_title_environment_rejects_duplicate_or_wrong_module_import(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root, _, _, image_path = make_synthetic_repo(temporary)
            metadata = trace.xex_info.parse_xex2(
                synthetic_xex(), image_path.read_bytes()
            )
            data_import = metadata["import_libraries"]["data_imports"][0]
            metadata["import_libraries"]["data_imports"].append(dict(data_import))
            image = trace.GuestRange(
                "image", SYNTHETIC_IMAGE_BASE, SYNTHETIC_IMAGE_SIZE
            )
            with self.assertRaisesRegex(trace.TraceError, "exactly one"):
                trace.select_executable_module_handle_slot(metadata, image)
            metadata["import_libraries"]["data_imports"] = [
                {**data_import, "ordinal": 0x194}
            ]
            with self.assertRaisesRegex(trace.TraceError, "exactly one"):
                trace.select_executable_module_handle_slot(metadata, image)

    def test_thread_environment_bounds_template_and_all_scratch_ranges(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root, _, _, image_path = make_synthetic_repo(temporary)
            image = trace.GuestRange("image", SYNTHETIC_IMAGE_BASE, SYNTHETIC_IMAGE_SIZE)
            heap = trace.GuestRange("heap", 0x10000000, 0x4000)
            stack = trace.GuestRange("stack", 0x70000000, 0x10000)
            title = trace.validate_title_environment_inputs(
                root / "local-analysis/default.xex", image_path, image,
                0x50000000, 0x50001000, 0x50002000, root,
            )
            thread = trace.validate_thread_environment_inputs(
                title, image, 0x61000000, 0x61001000, 0x61002000,
            )
            self.assertEqual(thread.data_size, 16)
            self.assertEqual(thread.raw_data_size, 12)
            self.assertEqual(thread.slot_count, 64)
            self.assertEqual(thread.tls.size, 16 + 64 * 4)
            trace.validate_ranges(image, heap, stack, title, thread=thread)
            pool = trace.GuestRange("created_thread_arena", 0x64000000, 0x100000)
            trace.validate_ranges(image, heap, stack, title, thread=thread, thread_pool=pool)
            self.assertEqual(title.thread_object_type_slot, SYNTHETIC_IMPORT_SLOT + 4)
            identity = trace.GuestRange("thread_type_identity", 0x62002000, 4)
            trace.validate_ranges(image, heap, stack, title, thread=thread, thread_pool=pool, thread_type=identity)
            xam = trace.GuestRange("native_xam_module", 0x62003000, 0x64)
            trace.validate_ranges(image, heap, stack, title, thread=thread, thread_pool=pool, thread_type=identity, xam_module=xam)
            hardware = trace.GuestRange("hardware_info", 0x62004000, 16)
            trace.validate_ranges(image, heap, stack, title, thread=thread, thread_pool=pool,
                thread_type=identity, xam_module=xam, extra_scratch=(hardware,))
            for bad in (pool.base, heap.base, stack.base, thread.pcr.base, thread.tls.base,
                        title.header.base, image.base, identity.base, xam.base, 0, hardware.base + 1, 0xFFFFFFFC):
                with self.assertRaises(trace.TraceError):
                    trace.validate_ranges(image, heap, stack, title, thread=thread, thread_pool=pool,
                        thread_type=identity, xam_module=xam, extra_scratch=(trace.dataclasses.replace(hardware, base=bad),))
            with self.assertRaises(trace.TraceError):
                trace.validate_ranges(image, heap, stack, extra_scratch=(hardware, hardware))
            for bad in (pool.base, thread.pcr.base, title.header.base, image.base, identity.base, 0, xam.base + 1):
                with self.assertRaises(trace.TraceError):
                    trace.validate_ranges(image, heap, stack, title, thread=thread, thread_pool=pool,
                        thread_type=identity, xam_module=trace.dataclasses.replace(xam, base=bad))
            with self.assertRaises(trace.TraceError):
                trace.validate_ranges(image, heap, stack, xam_module=xam)
            for bad in (pool.base, thread.pcr.base, title.header.base, image.base, 0):
                with self.assertRaises(trace.TraceError):
                    trace.validate_ranges(image, heap, stack, title, thread=thread,
                        thread_pool=pool, thread_type=trace.dataclasses.replace(identity, base=bad))
            with self.assertRaises(trace.TraceError):
                trace.validate_ranges(image, heap, stack,
                    trace.dataclasses.replace(title, thread_object_type_slot=None), thread=thread,
                    thread_type=identity)
            with self.assertRaises(trace.TraceError):
                trace.validate_ranges(image, heap, stack, title, thread_pool=pool)
            for invalid_pool in (
                trace.dataclasses.replace(pool, size=0x6000),
                trace.dataclasses.replace(pool, size=0x10001000),
                trace.dataclasses.replace(pool, base=0),
                trace.dataclasses.replace(pool, base=0xFFF00000),
                trace.dataclasses.replace(pool, base=heap.base),
                trace.dataclasses.replace(pool, base=thread.pcr.base),
                trace.dataclasses.replace(pool, base=title.header.base),
                trace.dataclasses.replace(pool, base=image.base),
            ):
                with self.subTest(pool=invalid_pool), self.assertRaises(trace.TraceError):
                    trace.validate_ranges(image, heap, stack, title, thread=thread, thread_pool=invalid_pool)
            for address in (
                heap.base, stack.base, title.header.base, title.module.base,
                title.exported_variable.base,
                image.end,  # Full security-image zero/BSS tail is also reserved.
                thread.pcr.base,
            ):
                with self.subTest(address=address), self.assertRaisesRegex(
                    trace.TraceError, "overlap"
                ):
                    trace.validate_ranges(
                        image, heap, stack, title,
                        thread=trace.dataclasses.replace(
                            thread, tls=trace.GuestRange("thread_tls", address, thread.tls.size)
                        ),
                    )
            with self.assertRaisesRegex(trace.TraceError, "validated XEX TLS"):
                trace.validate_thread_environment_inputs(
                    trace.dataclasses.replace(title, tls_info=None), image,
                    0x61000000, 0x61001000, 0x61002000,
                )
            for field, value in (("data_size", 3), ("slot_count", 2049), ("raw_data_size", 17)):
                with self.subTest(field=field), self.assertRaises(trace.TraceError):
                    trace.validate_thread_environment_inputs(
                        trace.dataclasses.replace(title, tls_info={**title.tls_info, field: value}),
                        image, 0x61000000, 0x61001000, 0x61002000,
                    )

    def test_title_environment_bounds_raw_xex_before_parser_read(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root, _, _, image_path = make_synthetic_repo(temporary)
            image = trace.GuestRange(
                "image", SYNTHETIC_IMAGE_BASE, SYNTHETIC_IMAGE_SIZE
            )
            with mock.patch.object(trace, "MAXIMUM_RAW_XEX_SIZE", 0x100):
                with self.assertRaisesRegex(trace.TraceError, "bounded parser limit"):
                    trace.validate_title_environment_inputs(
                        root / "local-analysis/default.xex",
                        image_path,
                        image,
                        0x50000000,
                        0x50001000,
                        0x50002000,
                        root,
                    )
    def test_native_kernel_version_protocol(self):
        version = 'native_kernel_version=2181040512,1644185600,541018624\n'
        result = trace.parse_trace_protocol(valid_protocol() + version, 1, 10, 2)
        self.assertEqual(result.native_kernel_version['version'], [2,0,16202,0])
        self.assertFalse(result.native_kernel_version['complete_xbox_kernel'])
        for invalid in ('0,1644185600,541018624', '2181040513,1644185600,541018624',
                        '2181040512,4294967292,541018624', '2181040512,1644185600,0'):
            with self.assertRaises(trace.TraceError):
                trace.parse_trace_protocol(valid_protocol() + 'native_kernel_version=' + invalid + '\n', 1, 10, 2)

    def test_protocol_accepts_only_bounded_public_metadata(self) -> None:
        result = trace.parse_trace_protocol(valid_protocol(), 1, 10, 2)
        self.assertEqual(result.stop_import, "__imp__Synthetic")
        self.assertEqual(result.function_labels, ("_xstart",))
        rejected = trace.parse_trace_protocol(
            valid_protocol().replace("unimplemented-import", "rejected-import"), 1, 10, 2
        )
        self.assertEqual(rejected.stop_reason, "rejected-import")
        self.assertEqual(rejected.stop_import, "__imp__Synthetic")
        with self.assertRaisesRegex(trace.TraceError, "inconsistent"):
            trace.parse_trace_protocol(
                valid_protocol().replace("unimplemented-import", "rejected-import")
                .replace("stop_import=__imp__Synthetic", "stop_import=none"), 1, 10, 2
            )
        with self.assertRaisesRegex(trace.TraceError, "excessive"):
            trace.parse_trace_protocol(
                valid_protocol(labels=("first", "second")), 1, 10, 2
            )
        with self.assertRaisesRegex(trace.TraceError, "non-public"):
            trace.parse_trace_protocol(
                valid_protocol().replace("__imp__Synthetic", "private/path"), 1, 10, 2
            )
        with self.assertRaisesRegex(trace.TraceError, "exceeded"):
            trace.parse_trace_protocol(
                valid_protocol().replace("completed_function_entries=2", "completed_function_entries=11"),
                1,
                10,
                2,
            )
        with self.assertRaisesRegex(trace.TraceError, "mapping count"):
            trace.parse_trace_protocol(valid_protocol(), 1, 10, 3)
        indirect = trace.parse_trace_protocol(invalid_indirect_protocol(), 1, 10, 2)
        self.assertEqual(indirect.stop_reason, "invalid-indirect-target")
        self.assertIsNone(indirect.stop_import)

    def test_additional_kernel_data_slots_are_selected_from_xex_records(self):
        with tempfile.TemporaryDirectory() as temporary:
            root, _, _, image_path = make_synthetic_repo(temporary)
            raw = bytearray(synthetic_xex())
            library = 0x240 + 12 + 16
            struct.pack_into('>I', raw, 0x240, 12 + 16 + 64)
            struct.pack_into('>I', raw, library, 64)
            struct.pack_into('>H', raw, library + 38, 6)
            image_bytes = bytearray(image_path.read_bytes())
            for i, ordinal in enumerate((0x156, 0x1BE, 0x1BF, 0x59), 2):
                slot = SYNTHETIC_IMPORT_SLOT + i * 4
                struct.pack_into('>I', raw, library + 40 + i * 4, slot)
                struct.pack_into('>I', image_bytes, slot - SYNTHETIC_IMAGE_BASE, ordinal)
            raw_path = root / 'local-analysis/default.xex'
            raw_path.write_bytes(raw); image_path.write_bytes(image_bytes)
            def validate():
                return trace.validate_title_environment_inputs(raw_path, image_path,
                    trace.GuestRange('image', SYNTHETIC_IMAGE_BASE, SYNTHETIC_IMAGE_SIZE),
                    0x50000000, 0x50001000, 0x50002000, root)
            title = validate()
            self.assertEqual(title.hardware_info_slot, SYNTHETIC_IMPORT_SLOT + 8)
            self.assertEqual(title.video_device_slots, (SYNTHETIC_IMPORT_SLOT + 12, SYNTHETIC_IMPORT_SLOT + 16))
            self.assertEqual(title.debug_monitor_slot, SYNTHETIC_IMPORT_SLOT + 20)
            self.assertIsNone(title.cert_monitor_slot)
            self.assertEqual(trace.unbound_data_import_slots(title, True,
                (title.hardware_info_slot, *title.video_device_slots)), (title.debug_monitor_slot,))
            self.assertEqual(trace.unbound_data_import_slots(title, True,
                (title.hardware_info_slot, *title.video_device_slots, title.debug_monitor_slot)), ())
            struct.pack_into('>I', image_bytes, SYNTHETIC_IMPORT_SLOT + 16 - SYNTHETIC_IMAGE_BASE, 0x156)
            image_path.write_bytes(image_bytes)
            ambiguous = validate()
            self.assertIsNone(ambiguous.hardware_info_slot)
            self.assertEqual(ambiguous.video_device_slots, ())
            struct.pack_into('>I', image_bytes, SYNTHETIC_IMPORT_SLOT + 16 - SYNTHETIC_IMAGE_BASE, 0x59)
            image_path.write_bytes(image_bytes)
            self.assertIsNone(validate().debug_monitor_slot)
            struct.pack_into('>I', image_bytes, SYNTHETIC_IMPORT_SLOT + 20 - SYNTHETIC_IMAGE_BASE, 0x266)
            image_path.write_bytes(image_bytes)
            self.assertEqual(validate().cert_monitor_slot,SYNTHETIC_IMPORT_SLOT + 20)
            struct.pack_into('>I', image_bytes, SYNTHETIC_IMPORT_SLOT + 16 - SYNTHETIC_IMAGE_BASE, 0x266)
            image_path.write_bytes(image_bytes)
            self.assertIsNone(validate().cert_monitor_slot)
            struct.pack_into('>I',image_bytes,SYNTHETIC_IMPORT_SLOT+20-SYNTHETIC_IMAGE_BASE,0x1C1)
            image_path.write_bytes(image_bytes)
            self.assertEqual(validate().hsio_lock_slot,SYNTHETIC_IMPORT_SLOT+20)
            struct.pack_into('>I',image_bytes,SYNTHETIC_IMPORT_SLOT+16-SYNTHETIC_IMAGE_BASE,0x1C1)
            image_path.write_bytes(image_bytes)
            self.assertIsNone(validate().hsio_lock_slot)

    def test_rejects_silent_trap_noops(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            chunk = root / "ppc_recomp.0.cpp"
            (root / "ppc_context.h").write_text("PPCTrapCondition", encoding="utf-8")
            for source in ("// twi 31,r0,22\nreturn;\n", "// twi 31,r0,22\n",
                           "// tdlgei r3,0\n// comment\nPPC_RUNTIME_TRAP();\n"):
                chunk.write_text(source, encoding="utf-8")
                with self.assertRaises(trace.TraceError):
                    trace.validate_trap_hooks([chunk], root)
            chunk.write_text("// twi 31,r0,22\nPPC_RUNTIME_TRAP();\n"
                "// td 4,r3,r4\nif (PPCTrapCondition<uint64_t>(4,ctx.r3.u64,ctx.r4.u64)) PPC_RUNTIME_TRAP();\n", encoding="utf-8")
            self.assertEqual(trace.validate_trap_hooks([chunk], root), 2)
            (root / "ppc_context.h").write_text("old header", encoding="utf-8")
            with self.assertRaises(trace.TraceError):
                trace.validate_trap_hooks([chunk], root)

    def test_timebase_hook_validation_rejects_stale_or_partial_sources(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            chunk = root / "ppc_recomp.0.cpp"
            header = root / "ppc_context.h"
            header.write_text("#ifndef PPC_READ_TIMEBASE\n")
            for source in ("// mftb r3\nr3.u64 = __rdtsc();\n", "// mftbu r3\n",
                           "// mftb r3\nreturn;\n", "r3.u64 = PPC_READ_TIMEBASE();\n",
                           "// unrelated\nctx.r3.u64 = __rdtsc();\n"):
                chunk.write_text(source)
                with self.assertRaises(trace.TraceError):
                    trace.validate_timebase_hooks([chunk], root)
            chunk.write_text("// mftb r3\n\nctx.r3.u64 = uint64_t(PPC_READ_TIMEBASE());\n"
                "// mftbu r4\nr4.u64 = uint64_t(PPC_READ_TIMEBASE()) >> 32;\n")
            self.assertEqual(trace.validate_timebase_hooks([chunk], root), 2)
            header.write_text("old header")
            with self.assertRaises(trace.TraceError):
                trace.validate_timebase_hooks([chunk], root)

    def test_combined_runtime_hooks_scan_once_and_revalidate_changes(self) -> None:
        from collections import Counter
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / 'ppc_context.h').write_text('#ifndef PPC_READ_TIMEBASE\nPPCTrapCondition\n')
            chunks = [root / f'ppc_recomp.{i}.cpp' for i in range(6)]
            source = ('// twi 31,r0,22\n\nPPC_RUNTIME_TRAP();\n'
                      '// mftbu r4\n\nr4.u64 = uint64_t(PPC_READ_TIMEBASE()) >> 32;\n')
            for chunk in chunks: chunk.write_text(source)
            legacy = (trace.validate_trap_hooks(chunks, root), trace.validate_timebase_hooks(chunks, root))
            self.assertEqual(legacy, (6, 6))
            for workers in (1, 3, 4):
                reads = Counter()
                original = Path.open
                def counted(path, *args, **kwargs):
                    reads[path] += 1
                    return original(path, *args, **kwargs)
                with patch.object(Path, 'open', counted):
                    self.assertEqual(trace.validate_runtime_hooks(chunks, root, workers), legacy)
                self.assertTrue(all(reads[chunk] == 1 for chunk in chunks))
                self.assertEqual(reads[root / 'ppc_context.h'], 1)
            chunks[-1].write_text(source + '// trap\nreturn;\n')
            with self.assertRaises(trace.TraceError): trace.validate_runtime_hooks(chunks, root, 3)

    def test_combined_runtime_hooks_keep_chunk_boundaries_and_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            header = root / 'ppc_context.h'
            header.write_text('#ifndef PPC_READ_TIMEBASE\nPPCTrapCondition\n')
            chunks = [root / f'ppc_recomp.{i}.cpp' for i in range(2)]
            chunks[1].write_text('PPC_RUNTIME_TRAP();\n')
            for source in ('// trap\n', '// mftb r3\n', '// unrelated\u2028// trap\u2028return;\n',
                           '// unrelated\nctx.r3.u64 = __rdtsc();\n'):
                chunks[0].write_text(source)
                with self.assertRaises(trace.TraceError): trace.validate_runtime_hooks(chunks, root, 2)
            chunks[0].write_bytes(b'\xff')
            with self.assertRaises(trace.TraceError): trace.validate_runtime_hooks(chunks, root, 2)
            chunks[0].write_text('// trap\nPPC_RUNTIME_TRAP();\n')
            header.write_text('#ifndef PPC_READ_TIMEBASE\n')
            with self.assertRaises(trace.TraceError): trace.validate_runtime_hooks(chunks, root, 2)
            header.write_text('PPCTrapCondition\n')
            with self.assertRaises(trace.TraceError): trace.validate_runtime_hooks(chunks, root, 2)
            header.write_text('#ifndef PPC_READ_TIMEBASE\nPPCTrapCondition\n')
            for workers in (0, 5, True, '2'):
                with self.assertRaises(trace.TraceError): trace.validate_runtime_hooks(chunks, root, workers)
            with self.assertRaises(trace.TraceError): trace.validate_runtime_hooks([root / 'missing.cpp'], root, 2)

    def test_runtime_hook_scan_rejects_short_reads_and_concurrent_changes(self) -> None:
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);chunk=root/'ppc_recomp.0.cpp'
            (root/'ppc_context.h').write_text('#ifndef PPC_READ_TIMEBASE\nPPCTrapCondition\n')
            original=Path.open
            class AlteredRead:
                def __init__(self, file, short): self.file,self.short=file,short
                def __enter__(self): return self
                def __exit__(self,*args): self.file.close()
                def fileno(self): return self.file.fileno()
                def read(self): return b'#ifndef PPC_READ_TIMEBASE\n'
                def __iter__(self):
                    if self.short: return
                    for line in self.file:
                        yield line
                        with original(chunk,'ab') as changed: changed.write(b'// changed\n')
                        return
            for short in (True,False):
                chunk.write_text('// harmless complete chunk\n')
                def altered(path,*args,**kwargs):
                    file=original(path,*args,**kwargs)
                    return AlteredRead(file,short) if path==chunk else file
                with patch.object(Path,'open',altered),self.assertRaisesRegex(trace.TraceError,'not completely read'):
                    trace.validate_runtime_hooks([chunk],root,2)
            self.assertEqual(trace.validate_runtime_hooks([chunk],root,2),(0,0))
            def short_header(path,*args,**kwargs):
                file=original(path,*args,**kwargs)
                return AlteredRead(file,True) if path.name=='ppc_context.h' else file
            with patch.object(Path,'open',short_header),self.assertRaisesRegex(trace.TraceError,'header changed or was not completely read'):
                trace.validate_runtime_hooks([chunk],root,2)

    def test_cpu_timebase_evidence_requires_matching_rate_and_count(self) -> None:
        fields = "cpu_timebase_frequency=50000000\ncpu_timebase_reads=123\n"
        result = trace.parse_trace_protocol(valid_protocol() + fields, 1, 10, 2).cpu_timebase
        self.assertEqual(result["frequency_hz"], 50000000)
        self.assertEqual(result["reads"], 123)
        self.assertFalse(result["gpu_clock_semantics_validated"])
        self.assertIsNone(trace.parse_trace_protocol(valid_protocol(), 1, 10, 2).cpu_timebase)
        for bad in (fields.replace("50000000", "19200000"), fields.splitlines()[0] + "\n",
                    fields.splitlines()[1] + "\n", fields.replace("123", "-1"),
                    fields.replace("123", "18446744073709551616"), fields + fields):
            with self.assertRaises(trace.TraceError):
                trace.parse_trace_protocol(valid_protocol() + bad, 1, 10, 2)

    def test_file_io_counts_are_complete_and_bounded(self) -> None:
        base = valid_protocol()
        fields = "file_opens=2\nfile_reads=3\nfile_bytes=42\nfile_seeks=1\n"
        self.assertEqual(trace.parse_trace_protocol(base + fields, 1, 10, 2).file_io,
                         dict(opens=2, reads=3, bytes=42, seeks=1))
        large=fields.replace('bytes=42','bytes=50331648')
        self.assertEqual(trace.parse_trace_protocol(base+large,1,10,2).file_io['bytes'],3*16*1024*1024)
        for altered in (fields.replace("file_seeks=1\n", ""), fields + "file_bytes=1\n",
                        fields.replace("reads=3", "reads=0"), fields.replace("opens=2", "opens=0"),
                        fields.replace("bytes=42", "bytes=-1"), fields.replace("opens=2", "opens=4294967296"),
                        fields.replace("bytes=42", "bytes=50331649")):
            with self.assertRaises(trace.TraceError):
                trace.parse_trace_protocol(base + altered, 1, 10, 2)

    def test_disconnected_input_evidence_is_explicit_and_canonical(self) -> None:
        self.assertIsNone(trace.parse_trace_protocol(valid_protocol(), 1, 10, 2).disconnected_input)
        value=trace.parse_trace_protocol(valid_protocol()+"disconnected_input=1\n",1,10,2).disconnected_input
        self.assertEqual(value['connected_devices'],0)
        self.assertEqual(value['mode'],'explicit-no-connected-devices')
        for key in ('host_controller_input','synthetic_events','state_packet_writes'):
            self.assertIs(value[key],False)
        for raw in ('0','2','-1','01','true','1,0',''):
            with self.assertRaises(trace.TraceError):
                trace.parse_trace_protocol(valid_protocol()+f"disconnected_input={raw}\n",1,10,2)
        with self.assertRaises(trace.TraceError):
            trace.parse_trace_protocol(valid_protocol()+"disconnected_input=1\n"*2,1,10,2)

    def test_native_audio_stream_metadata_does_not_claim_playback(self) -> None:
        self.assertIsNone(trace.parse_trace_protocol(valid_protocol(),1,10,2).experimental_native_audio)
        field='experimental_native_audio=48000,2,4360,2180,1\n'
        value=trace.parse_trace_protocol(valid_protocol()+field,1,10,2).experimental_native_audio
        self.assertEqual(value['speaker_queries'],1)
        self.assertEqual(value['buffer_capacity_frames'],4360)
        self.assertEqual(value['reference_speaker_word'],0x10001)
        self.assertIs(value['stream_open'],True)
        self.assertEqual(value['submitted_frames'],0)
        for key in ('stream_started','guest_callbacks_implemented','audible_output_verified','xbox_speaker_bitfields_verified'):
            self.assertIs(value[key],False)
        for raw in ('','48000,2,4360,2180','48000,2,4360,2180,1,0','048000,2,4360,2180,1',
                    '44100,2,4360,2180,1','48000,6,4360,2180,1','48000,2,0,0,0',
                    '48000,2,100,101,0','48000,2,48001,256,0','48000,2,4360,0,1',
                    '48000,2,4360,2180,-1','48000,2,4360,2180,4294967296'):
            with self.assertRaises(trace.TraceError):
                trace.parse_trace_protocol(valid_protocol()+'experimental_native_audio='+raw+'\n',1,10,2)
        with self.assertRaises(trace.TraceError):
            trace.parse_trace_protocol(valid_protocol()+field*2,1,10,2)

    def test_native_category1_gain_does_not_claim_device_volume(self) -> None:
        base=valid_protocol()+'workers_created=1\nworkers_entered=1\ncontext_switches=1\n'
        renderer='experimental_native_audio=48000,2,4360,2180,1\nnative_audio_renderer=1677725696,1,0,0,0,0,0,0,0,1,0,0,0\n'
        field='native_category1_gain=1065353216,7\n'
        gain=trace.parse_trace_protocol(base+renderer+field,1,10,2,cooperative=True).native_category1_gain
        self.assertEqual(gain['gain'],1.0);self.assertEqual(gain['queries'],7);self.assertTrue(gain['title_consumes_gain'])
        for key in ('host_pcm_gain_reapplied','android_system_volume','xbox_settings_verified','category_meaning_verified','ducking_implemented','change_mask_implemented'):
            self.assertFalse(gain[key])
        for raw in ('0,7','1065353216','1065353216,-1','1065353216,07','1065353216,18446744073709551616','1065353216,1,0'):
            with self.assertRaises(trace.TraceError):trace.parse_trace_protocol(base+renderer+'native_category1_gain='+raw+'\n',1,10,2,cooperative=True)
        with self.assertRaises(trace.TraceError):trace.parse_trace_protocol(base+field,1,10,2,cooperative=True)
        with self.assertRaises(trace.TraceError):trace.parse_trace_protocol(base+renderer+field*2,1,10,2,cooperative=True)

    def test_native_event_boost_is_explicit_and_bounded(self) -> None:
        base=valid_protocol()+'workers_created=1\nworkers_entered=1\ncontext_switches=1\n'
        field='native_event_wake_boost=3,2,1\n'
        result=trace.parse_trace_protocol(base+field,1,10,2,cooperative=True).native_event_wake_boost
        self.assertEqual(result['threads_boosted'],2);self.assertEqual(result['threads_suppressed'],1)
        for key in ('base_priority_changed','host_os_priority_changed','console_scheduler_verified','signal_then_wait_irql_implemented'):
            self.assertFalse(result[key])
        for raw in ('','0,1,0','1,65,1','1,0,66','1,0','1,0,0,0','01,0,0','1,-1,0','18446744073709551616,0,0'):
            with self.assertRaises(trace.TraceError):
                trace.parse_trace_protocol(base+'native_event_wake_boost='+raw+'\n',1,10,2,cooperative=True)
        with self.assertRaises(trace.TraceError):trace.parse_trace_protocol(base+field*2,1,10,2,cooperative=True)
        with self.assertRaises(trace.TraceError):trace.parse_trace_protocol(valid_protocol()+field,1,10,2)

    def test_native_audio_renderer_has_separate_bounded_evidence(self) -> None:
        base=valid_protocol()+'workers_created=1\nworkers_entered=1\ncontext_switches=1\n'
        config='experimental_native_audio=48000,2,4360,2180,1\n'
        prefix='native_audio_renderer='
        # Active callback parked before producing PCM is not playback.
        waiting=[0x64001000,1,0,0,0,0,0,0,0,1,0,0,0]
        def parse(fields):
            return trace.parse_trace_protocol(base+config+prefix+','.join(map(str,fields))+'\n',1,10,2,cooperative=True)
        result=parse(waiting)
        self.assertEqual(result.native_audio_renderer['accepted_frames'],0)
        self.assertTrue(result.native_audio_renderer['callback_active'])
        self.assertFalse(result.native_audio_renderer['stream_started'])
        self.assertTrue(result.experimental_native_audio['guest_callbacks_implemented'])
        played=[0x64001000,24,24,24,6144,5888,0,0,1,0,0,6144,1]
        result=parse(played)
        self.assertEqual(result.native_audio_renderer['observed_service_read_frames'],6144)
        self.assertEqual(result.experimental_native_audio['submitted_frames'],6144)
        for key in ('synthetic_silence_inserted','audible_output_verified','multi_client_mixing',
                    'unregister_implemented','console_timing_verified'):
            self.assertFalse(result.native_audio_renderer[key])
        partial=[0x64001000,2,2,2,17,0,2,4,1,0,0,0,1]
        self.assertEqual(parse(partial).native_audio_renderer['pending_packets'],2)
        for index,bad in ((0,0),(0,0x100000000),(0,7),(1,25),(2,25),(3,25),(4,6145),
                          (5,6145),(6,1),(7,24*512+1),(8,0),(8,2),(9,1),(10,2),(11,5887),(12,2)):
            changed=played.copy();changed[index]=bad
            with self.subTest(index=index,bad=bad),self.assertRaises(trace.TraceError):parse(changed)
        for raw in ('',','.join(map(str,waiting))+',0','01,'+','.join(map(str,waiting[1:])),
                    ','.join(['18446744073709551616']*13)):
            with self.assertRaises(trace.TraceError):
                trace.parse_trace_protocol(base+config+prefix+raw+'\n',1,10,2,cooperative=True)
        field=prefix+','.join(map(str,waiting))+'\n'
        for malformed in (base+field,base+config+field*2):
            with self.assertRaises(trace.TraceError):trace.parse_trace_protocol(malformed,1,10,2,cooperative=True)
        with self.assertRaises(trace.TraceError):trace.parse_trace_protocol(valid_protocol()+config+field,1,10,2)

    def test_native_display_metadata_is_bounded_and_not_rendering(self) -> None:
        base = valid_protocol() + "graphics_initialized=1\n"
        field = "native_display_dimensions=1280,720,1280,720\n"
        display = trace.parse_trace_protocol(base + field, 1, 10, 2).native_display
        self.assertEqual(display["buffer_width"], 1280)
        self.assertIs(display["image_format_capability_checked"], True)
        for flag in ("scaler_executed", "image_created", "system_overlay_callbacks", "frame_presented"):
            self.assertIs(display[flag], False)
        for values in ("", "1,2,3", "0,720,1280,720", "4097,720,1280,720", "01280,720,1280,720",
                       "1280,720,1920,1080", "1280,720,1280,720,0", "x,720,1280,720", "-1,720,1280,720"):
            with self.assertRaises(trace.TraceError):
                trace.parse_trace_protocol(base + "native_display_dimensions=" + values + "\n", 1, 10, 2)
        for invalid in (valid_protocol() + field, base.replace("initialized=1", "initialized=0") + field, base + field + field):
            with self.assertRaises(trace.TraceError):
                trace.parse_trace_protocol(invalid, 1, 10, 2)

    def test_thread_delays_are_bounded_joined_and_not_fake_timeouts(self) -> None:
        base = valid_protocol() + 'workers_created=1\nworkers_entered=1\ncontext_switches=2\nwait_operations=3\n'
        field = 'thread_delays=3,2,1,1,20000,10000,0\n'
        result = trace.parse_trace_protocol(base + field, 1, 10, 2, cooperative=True).thread_delays
        self.assertEqual(result['completed'], 2)
        self.assertEqual(result['zero_yields'], 1)
        self.assertEqual(result['requested_100ns_ticks'], 20000)
        self.assertFalse(result['console_timing_verified'])
        self.assertTrue(result['zero_relinquishes_slice'])
        for raw in ('0,0,0,0,0,0,0', '1,1,0,1,0,0,0', '1,0,1,0,600000000,600000000,0'):
            trace.parse_trace_protocol(base + 'thread_delays=' + raw + '\n', 1, 10, 2, cooperative=True)
        for raw in ('', '1,1,0,0,1,1', '01,1,0,0,1,1,0', '-1,1,0,0,1,1,0',
                    '1,0,0,0,1,1,1', '1,0,0,0,1,1,0', '1,1,0,2,0,0,0',
                    '1,1,0,0,600000001,600000001,0', '2,2,0,0,1,1,0',
                    '2,2,0,0,3,1,0', '1,1,0,0,1,2,0', '1,1,0,1,0,1,0',
                    '1,1,0,0,0,0,0', '18446744073709551616,0,0,0,0,0,0'):
            with self.assertRaises(trace.TraceError):
                trace.parse_trace_protocol(base + 'thread_delays=' + raw + '\n', 1, 10, 2, cooperative=True)
        for value, coop in ((base + field + field, True), (valid_protocol() + field, False)):
            with self.assertRaises(trace.TraceError): trace.parse_trace_protocol(value, 1, 10, 2, cooperative=coop)

    def test_file_create_request_is_complete_bounded_and_stop_specific(self) -> None:
        base = valid_protocol().replace("unimplemented-import", "rejected-import").replace(
            "__imp__Synthetic", "__imp__NtCreateFile")
        fields = "file_create_access=2148532352\nfile_create_attributes=128\nfile_create_share=1\nfile_create_disposition=1\nfile_create_options=96\nfile_create_lookup=1\nfile_create_record=1\n"
        result = trace.parse_trace_protocol(base + fields, 1, 10, 2)
        self.assertEqual(result.file_create_request["options"], 96)
        for output in (valid_protocol() + fields, base + fields.replace("file_create_lookup=1\n", ""),
                       base + fields + "file_create_lookup=0\n",
                       base + fields.replace("lookup=1", "lookup=4"),
                       base + fields.replace("options=96", "options=-1"),
                       base + fields.replace("options=96", "options=4294967296"),
                       base + fields.replace("options=96", "options=private/path")):
            with self.assertRaises(trace.TraceError):
                trace.parse_trace_protocol(output, 1, 10, 2)

    def test_priority_request_is_complete_bounded_and_stop_specific(self) -> None:
        base = valid_protocol().replace("unimplemented-import", "rejected-import").replace(
            "__imp__Synthetic", "__imp__KeSetBasePriorityThread")
        fields = "priority_increment=-2\npriority_previous=0\npriority_initial=1\npriority_current=1\npriority_suspended=0\npriority_returned=0\n"
        result = trace.parse_trace_protocol(base + fields, 1, 10, 2)
        self.assertEqual(result.priority_request, dict(increment=-2, previous=0,
            initial=True, current=True, suspended=False, returned=False))
        for output in (base + fields.replace("priority_returned=0\n", ""),
                       valid_protocol() + fields, base + fields + "priority_initial=0\n",
                       base + fields.replace("increment=-2", "increment=-2147483649"),
                       base + fields.replace("increment=-2", "increment=2147483648"),
                       base + fields.replace("initial=1", "initial=2"),
                       base + fields.replace("previous=0", "previous=private/path")):
            with self.assertRaises(trace.TraceError):
                trace.parse_trace_protocol(output, 1, 10, 2)

    def test_cooperative_protocol_counters_are_complete_bounded_and_opt_in(self) -> None:
        base = valid_protocol()
        counters = "workers_created=1\nworkers_entered=1\ncontext_switches=2\n"
        result = trace.parse_trace_protocol(base + counters, 1, 10, 2, cooperative=True)
        self.assertEqual((result.workers_created, result.workers_entered, result.context_switches), (1, 1, 2))
        self.assertEqual(trace.parse_trace_protocol(base, 1, 10, 2, cooperative=False).workers_created, 0)
        for output, enabled in ((base, True), (base + counters, False),
                (base + "workers_created=1\n", True),
                (base + counters + "workers_created=1\n", True)):
            with self.subTest(output=output, enabled=enabled), self.assertRaises(trace.TraceError):
                trace.parse_trace_protocol(output, 1, 10, 2, cooperative=enabled)
        for source, target in (("workers_created=1", "workers_created=65"),
                ("workers_entered=1", "workers_entered=2"),
                ("workers_entered=1", "workers_entered=0"),
                ("workers_created=1", "workers_created=-1"),
                ("context_switches=2", "context_switches=5"),
                ("context_switches=2", "context_switches=-1"),
                ("context_switches=2", "context_switches=invalid")):
            with self.subTest(target=target), self.assertRaises(trace.TraceError):
                trace.parse_trace_protocol(base + counters.replace(source, target), 1, 10, 2, cooperative=True)
        extended = base + counters + "wait_operations=3\n"
        self.assertEqual(trace.parse_trace_protocol(extended, 1, 10, 2, cooperative=True).wait_operations, 3)
        for output in (base + "wait_operations=1\n", extended.replace("wait_operations=3", "wait_operations=-1"),
                       extended.replace("wait_operations=3", "wait_operations=11")):
            with self.assertRaises(trace.TraceError):
                trace.parse_trace_protocol(output, 1, 10, 2)
        stalled = extended.replace("unimplemented-import", "no-runnable-task").replace("stop_import=__imp__Synthetic", "stop_import=none")
        self.assertEqual(trace.parse_trace_protocol(stalled, 1, 10, 2, cooperative=True).stop_reason, "no-runnable-task")
        self.assertEqual(trace.parse_trace_protocol(stalled.replace("no-runnable-task", "guest-trap"),
            1, 10, 2, cooperative=True).stop_reason, "guest-trap")

    def test_validate_only_writes_redacted_report_without_host_build(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root, ppc, bridge, image = make_synthetic_repo(temporary)
            output = root / "local-analysis/output"
            arguments = [
                str(ppc),
                str(bridge),
                str(image),
                "--expected-chunk-count", "2",
                "--heap-base", "0x10000000",
                "--heap-size", "0x4000",
                "--stack-base", "0x70000000",
                "--stack-size", "0x10000",
                "--output-dir", str(output),
                "--validate-only",
            ]
            for extra in (["--cooperative-threads"], ["--thread-quantum", "0"],
                          ["--thread-quantum", "1000001"]):
                with self.subTest(extra=extra), self.assertRaises(trace.TraceError):
                    trace.run(arguments + extra)
            with mock.patch.object(trace, "repository_root", return_value=root):
                self.assertEqual(trace.run(arguments), 0)
            report_text = (output / "trace-report.json").read_text(encoding="utf-8")
            self.assertNotIn(str(root), report_text)
            report = json.loads(report_text)
            self.assertIs(report["build"]["attempted"], False)
            self.assertIs(report["safety"]["guest_text_suppressed"], True)
            self.assertIs(report["safety"]["fault_containment_claimed"], False)
            self.assertIs(report["physical_memory"]["enabled"], False)
            self.assertEqual(
                stat.S_IMODE((output / "trace-report.json").stat().st_mode), 0o600
            )
            self.assertEqual(stat.S_IMODE(output.stat().st_mode), 0o700)

    def test_extended_entry_budget_keeps_independent_containment_limits(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root, ppc, bridge, image = make_synthetic_repo(temporary)
            output = root / 'local-analysis/out'
            arguments = [str(ppc), str(bridge), str(image), '--expected-chunk-count', '2',
                         '--heap-base', '0x10000000', '--heap-size', '0x4000',
                         '--stack-base', '0x70000000', '--stack-size', '0x10000',
                         '--validate-only', '--output-dir', str(output)]
            with mock.patch.object(trace, 'repository_root', return_value=root):
                for budget in (1_000_001, 2_000_000, 10_000_000):
                    self.assertEqual(trace.run(arguments + ['--function-entry-budget', str(budget)]), 0)
                    report = json.loads((output / 'trace-report.json').read_text())
                    self.assertEqual(report['limits']['function_entries'], budget)
                for budget in (0, -1, 10_000_001, 0x100000000):
                    with self.subTest(budget=budget), self.assertRaises(trace.TraceError):
                        trace.run(arguments + ['--function-entry-budget', str(budget)])
                for option, value in (('--cpu-time-seconds', trace.MAX_CPU_SECONDS + 1),
                                      ('--trace-timeout-seconds', trace.MAX_TRACE_SECONDS + 1),
                                      ('--max-function-labels', trace.MAX_FUNCTION_LABELS + 1)):
                    with self.subTest(option=option), self.assertRaises(trace.TraceError):
                        trace.run(arguments + ['--function-entry-budget', '10000000', option, str(value)])
                with self.assertRaises(trace.TraceError):
                    trace.run(arguments + ['--xex-image-protections'])

    def test_explicit_content_binding_requires_private_matching_catalog_record(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root, ppc, bridge, image = make_synthetic_repo(temporary)
            tree = root / "local-analysis/content"
            tree.mkdir()
            content = tree / "a.bin"
            content.write_bytes(b"real synthetic content")
            catalog = root / "local-analysis/catalog.bin"
            catalog.write_bytes(trace.file_metadata.snapshot(tree, "game:"))
            args = [str(ppc), str(bridge), str(image), "--expected-chunk-count", "2",
                    "--heap-base", "0x10000000", "--heap-size", "0x4000", "--stack-base", "0x70000000",
                    "--stack-size", "0x10000", "--validate-only", "--output-dir", str(root / "local-analysis/out")]
            with mock.patch.object(trace, "repository_root", return_value=root):
                with self.assertRaises(trace.TraceError):
                    trace.run(args + ["--read-only-file", "2", str(content)])
                args += ["--file-metadata-catalog", str(catalog)]
                for binding in (["1", str(content)], ["3", str(content)], ["-1", str(content)],
                                ["2", "relative"], ["no-index", str(content)]):
                    with self.assertRaises(trace.TraceError):
                        trace.run(args + ["--read-only-file", *binding])
                good = ["--read-only-file", "2", str(content)]
                self.assertEqual(trace.run(args + good), 0)
                with self.assertRaises(trace.TraceError):
                    trace.run(args + good + good)
                content.write_bytes(b"changed size")
                with self.assertRaises(trace.TraceError):
                    trace.run(args + good)
                report = (root / "local-analysis/out/trace-report.json").read_text()
                self.assertNotIn(str(content), report)
                self.assertEqual(json.loads(report)["file_content"]["explicit_files_bound"], 1)

    def test_explicit_content_binding_accepts_sixteen_and_rejects_seventeen(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root, ppc, bridge, image = make_synthetic_repo(temporary)
            tree = root / "local-analysis/content"
            tree.mkdir()
            files = [tree / f"{i:02}.bin" for i in range(17)]
            for content in files:
                content.write_bytes(b"synthetic content")
            catalog = root / "local-analysis/catalog.bin"
            catalog.write_bytes(trace.file_metadata.snapshot(tree, "game:"))
            output = root / "local-analysis/out"
            args = [str(ppc), str(bridge), str(image), "--expected-chunk-count", "2",
                    "--heap-base", "0x10000000", "--heap-size", "0x4000", "--stack-base", "0x70000000",
                    "--stack-size", "0x10000", "--validate-only", "--output-dir", str(output),
                    "--file-metadata-catalog", str(catalog)]
            bindings = []
            for index, content in enumerate(files, 2):
                bindings += ["--read-only-file", str(index), str(content)]
            with mock.patch.object(trace, "repository_root", return_value=root):
                self.assertEqual(trace.run(args + bindings[:16 * 3]), 0)
                report = json.loads((output / "trace-report.json").read_text())
                self.assertEqual(report["file_content"]["explicit_files_bound"], 16)
                with self.assertRaisesRegex(trace.TraceError, "up to sixteen"):
                    trace.run(args + bindings)

    def test_complete_installed_package_inputs_and_redaction(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root, ppc, bridge, image = make_synthetic_repo(temporary)
            tree = root / "local-analysis/installed"; tree.mkdir()
            source = tree / "data.bin"; source.write_bytes(b'synthetic content')
            metadata = root / "local-analysis/package-metadata.bin"
            metadata.write_bytes(trace.file_metadata.snapshot(tree, "content:"))
            catalog = root / "local-analysis/installed-catalog.bin"
            row = struct.pack('>II', 1, 2) + bytes(256) + b'Synthetic'.ljust(44, b'\0')
            catalog.write_bytes(b'GXCONT1\0' + struct.pack('>II', 0x12345678, 1) + row)
            args = [str(ppc), str(bridge), str(image), '--expected-chunk-count', '2',
                    '--heap-base', '0x10000000', '--heap-size', '0x4000', '--stack-base', '0x70000000', '--stack-size', '0x10000',
                    '--title-xex', str(root / 'local-analysis/default.xex'), '--title-header-base', '0x50000000',
                    '--title-export-address', '0x50001000', '--title-module-base', '0x50002000',
                    '--installed-content-catalog', str(catalog), '--validate-only', '--output-dir', str(root / 'local-analysis/out')]
            original = trace.validate_title_environment_inputs
            def title(*args):
                return trace.dataclasses.replace(original(*args), title_id=0x12345678)
            package = ['--installed-package', '1', str(metadata)]
            good = ['--installed-file', '1', '2', str(source)]
            with mock.patch.object(trace, 'repository_root', return_value=root), \
                 mock.patch.object(trace, 'validate_title_environment_inputs', side_effect=title):
                self.assertEqual(trace.run(args + package + good), 0)
                for bad in (package, good, package + package + good, package + good + good,
                            package + ['--installed-file', '1', '1', str(source)],
                            ['--installed-package', '2', str(metadata)] + good,
                            package + ['--installed-file', '1', '2', 'relative']):
                    with self.assertRaises(trace.TraceError):
                        trace.run(args + bad)
                text = (root / 'local-analysis/out/trace-report.json').read_text()
                self.assertNotIn(str(source), text)
                self.assertEqual(json.loads(text)['installed_content']['preopened_files'], 1)
                source.write_bytes(b'changed')
                with self.assertRaises(trace.TraceError):
                    trace.run(args + package + good)
                catalog.write_bytes(b'GXCONT1\0' + struct.pack('>II', 0x87654321, 1) + row)
                with self.assertRaises(trace.TraceError):
                    trace.run(args)

    def test_validate_only_title_report_is_redacted(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root, ppc, bridge, image = make_synthetic_repo(temporary)
            raw_xex = root / "local-analysis/default.xex"
            output = root / "local-analysis/title-output"
            arguments = [
                str(ppc), str(bridge), str(image),
                "--expected-chunk-count", "2",
                "--heap-base", "0x10000000", "--heap-size", "0x4000",
                "--stack-base", "0x70000000", "--stack-size", "0x10000",
                "--physical-base", "0x60000000",
                "--physical-size", "0x10000",
                "--physical-address-offset", "0",
                "--title-xex", str(raw_xex),
                "--title-header-base", "0x50000000",
                "--title-export-address", "0x50001000",
                "--title-module-base", "0x50002000",
                "--output-dir", str(output), "--validate-only",
            ]
            with mock.patch.object(trace, "repository_root", return_value=root):
                self.assertEqual(trace.run(arguments), 0)
            report_text = (output / "trace-report.json").read_text(encoding="utf-8")
            self.assertNotIn(str(raw_xex), report_text)
            report = json.loads(report_text)
            self.assertIs(report["title_environment"]["enabled"], True)
            self.assertIs(report["physical_memory"]["enabled"], True)
            self.assertEqual(report["title_environment"]["staged_header_size"], 0x340)
            self.assertEqual(
                report["title_environment"]["security_image_size"],
                SYNTHETIC_SECURITY_IMAGE_SIZE,
            )
            self.assertEqual(report["title_environment"]["data_import"]["count"], 1)

    def test_mocked_native_run_keeps_counts_but_not_guest_text_or_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root, ppc, bridge, image = make_synthetic_repo(temporary)
            raw_xex = root / "local-analysis/default.xex"
            output = root / "local-analysis/output"
            guest_text = "synthetic guest text must not be retained"
            calls = 0
            trace_invocation: list[str] = []

            def fake_command(command: list[str], timeout_seconds: int) -> trace.CommandOutcome:
                nonlocal calls
                del timeout_seconds
                calls += 1
                if calls == 2:
                    executable = output / "build/host_guest_trace"
                    executable.parent.mkdir(parents=True, exist_ok=True)
                    executable.write_text("#!/bin/sh\n", encoding="utf-8")
                    executable.chmod(0o755)
                return trace.CommandOutcome(0, "", "")

            def fake_contained(
                command: list[str],
                cwd: Path,
                limits: object,
            ) -> object:
                del cwd, limits
                trace_invocation.extend(command)
                return trace.contained_process.ContainedProcessOutcome(
                    trace.contained_process.ContainedOutcomeKind.EXITED,
                    0,
                    (guest_text + "\n" + valid_protocol()).encode("utf-8"),
                    7,
                )

            arguments = [
                str(ppc), str(bridge), str(image),
                "--expected-chunk-count", "2",
                "--heap-base", "0x10000000", "--heap-size", "0x4000",
                "--stack-base", "0x70000000", "--stack-size", "0x10000",
                "--physical-base", "0x60000000",
                "--physical-size", "0x10000",
                "--physical-address-offset", "0",
                "--title-xex", str(raw_xex),
                "--title-header-base", "0x50000000",
                "--title-export-address", "0x50001000",
                "--title-module-base", "0x50002000",
                "--function-entry-budget", "10", "--max-function-labels", "1",
                "--thread-pcr-base", "0x61000000",
                "--thread-record-base", "0x61001000",
                "--thread-tls-base", "0x61002000",
                "--thread-pool-base", "0x64000000", "--thread-pool-size", "0x100000",
                "--thread-type-base", "0x62002000",
                "--virtual-video-mode", "720p60",
                "--output-dir", str(output),
            ]
            with (
                mock.patch.object(trace, "repository_root", return_value=root),
                mock.patch.object(trace.platform, "system", return_value="Darwin"),
                mock.patch.object(trace.shutil, "which", side_effect=lambda name: f"/usr/bin/{name}"),
                mock.patch.object(trace, "run_command", side_effect=fake_command),
                mock.patch.object(
                    trace.contained_process,
                    "run_contained",
                    side_effect=fake_contained,
                ),
            ):
                self.assertEqual(trace.run(arguments), 0)
            report_text = (output / "trace-report.json").read_text(encoding="utf-8")
            self.assertNotIn(guest_text, report_text)
            self.assertNotIn(str(root), report_text)
            self.assertEqual(len(trace_invocation), 39)
            self.assertEqual(trace_invocation[10], "2")
            self.assertEqual(trace_invocation[11], str(0x60000000))
            self.assertEqual(trace_invocation[12], str(0x10000))
            self.assertEqual(trace_invocation[13], "0")
            self.assertEqual(trace_invocation[14], str(raw_xex.resolve()))
            self.assertEqual(
                trace_invocation[16], str(SYNTHETIC_SECURITY_IMAGE_SIZE)
            )
            self.assertEqual(trace_invocation[19], str(SYNTHETIC_IMPORT_SLOT))
            self.assertEqual(trace_invocation[23:], [
                "--thread-environment", str(0x61000000), str(0x61001000), str(0x61002000),
                str(SYNTHETIC_IMAGE_BASE + 0x500), "12", "16", "64",
                "--thread-pool", str(0x64000000), str(0x100000),
                "--thread-object-type", str(SYNTHETIC_IMPORT_SLOT + 4), str(0x62002000),
                "--virtual-video-mode", "720p60",
            ])
            report = json.loads(report_text)
            self.assertEqual(report["schema_version"], 2)
            self.assertEqual(report["video_mode"], {"configuration": "720p60",
                "physical_monitor_queried": False, "rendering_implemented": False})
            self.assertIs(report["physical_memory"]["enabled"], True)
            self.assertEqual(report["physical_memory"]["guest_base"], "0x60000000")
            self.assertIs(report["thread_environment"]["enabled"], True)
            self.assertEqual(report["thread_environment"]["dynamic_tls_slots"], 64)
            self.assertEqual(report["thread_environment"]["copied_template_bytes"], 12)
            self.assertIs(report["created_threads"]["enabled"], True)
            self.assertEqual(report["created_threads"]["creation_flags"], [1])
            self.assertIs(report["created_threads"]["unsuspended_creation_enqueues_before_publication"], False)
            self.assertEqual(report["cooperative_threads"]["priority_saturated_values"], [-16, 16])
            self.assertEqual(report["cooperative_threads"]["priority_saturated_slice_weights"], [0.125, 8])
            self.assertEqual(report["dispatcher_events"]["supported_types"], [0, 1])
            self.assertIs(report["dispatcher_events"]["parked_allocation_pinning"], True)
            self.assertIs(report["dispatcher_events"]["nt_handle_aliasing"], False)
            self.assertIs(report["thread_object_type"]["enabled"], True)
            self.assertEqual(report["execution"]["outcome"], "guard-stop")
            self.assertEqual(report["execution"]["public_stop_import"], "__imp__Synthetic")
            self.assertEqual(report["execution"]["debug_messages"]["count"], 1)
            self.assertEqual(
                report["execution"]["function_trace"]["retention"],
                "most-recent",
            )
            self.assertEqual(report["execution"]["function_trace"]["labels"], ["_xstart"])

    def test_parent_signal_report_has_no_crash_time_guest_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root, ppc, bridge, image = make_synthetic_repo(temporary)
            output = root / "local-analysis/signal-output"
            calls = 0

            def fake_build(command: list[str], timeout_seconds: int) -> trace.CommandOutcome:
                nonlocal calls
                del command, timeout_seconds
                calls += 1
                if calls == 2:
                    executable = output / "build/host_guest_trace"
                    executable.parent.mkdir(parents=True, exist_ok=True)
                    executable.write_text("#!/bin/sh\n", encoding="utf-8")
                    executable.chmod(0o755)
                return trace.CommandOutcome(0, "", "")

            signal_outcome = trace.contained_process.ContainedProcessOutcome(
                trace.contained_process.ContainedOutcomeKind.UNIX_SIGNAL,
                -11,
                b"private partial protocol must not be retained",
                12,
                signal_number=11,
                signal_name="SIGSEGV",
            )
            arguments = [
                str(ppc), str(bridge), str(image),
                "--expected-chunk-count", "2",
                "--heap-base", "0x10000000", "--heap-size", "0x4000",
                "--stack-base", "0x70000000", "--stack-size", "0x10000",
                "--output-dir", str(output),
            ]
            with (
                mock.patch.object(trace, "repository_root", return_value=root),
                mock.patch.object(trace.platform, "system", return_value="Darwin"),
                mock.patch.object(trace.shutil, "which", return_value="/usr/bin/tool"),
                mock.patch.object(trace, "run_command", side_effect=fake_build),
                mock.patch.object(
                    trace.contained_process,
                    "run_contained",
                    return_value=signal_outcome,
                ),
                contextlib.redirect_stderr(io.StringIO()),
            ):
                self.assertEqual(trace.run(arguments), 1)
            report_text = (output / "trace-report.json").read_text(encoding="utf-8")
            self.assertNotIn("private partial protocol", report_text)
            report = json.loads(report_text)
            self.assertEqual(report["schema_version"], 2)
            self.assertEqual(report["execution"]["outcome"], "unix-signal")
            self.assertEqual(report["execution"]["signal_name"], "SIGSEGV")
            self.assertIsNone(report["execution"]["completed_function_entries"])
            self.assertIsNone(report["execution"]["public_stop_import"])

            invalid_outcome = trace.contained_process.ContainedProcessOutcome(
                trace.contained_process.ContainedOutcomeKind.EXITED,
                0,
                b"not a trace protocol",
                4,
            )
            with (
                mock.patch.object(trace, "repository_root", return_value=root),
                mock.patch.object(trace.platform, "system", return_value="Darwin"),
                mock.patch.object(trace.shutil, "which", return_value="/usr/bin/tool"),
                mock.patch.object(trace, "run_command", return_value=trace.CommandOutcome(0, "", "")),
                mock.patch.object(
                    trace.contained_process,
                    "run_contained",
                    return_value=invalid_outcome,
                ),
            ):
                self.assertEqual(trace.run(arguments), 1)
            invalid_report = json.loads(
                (output / "trace-report.json").read_text(encoding="utf-8")
            )
            self.assertEqual(invalid_report["execution"]["outcome"], "invalid-protocol")
            self.assertIsNone(invalid_report["execution"]["completed_function_entries"])
            self.assertIsNone(invalid_report["execution"]["public_stop_import"])

    def test_main_uses_distinct_configuration_exit(self) -> None:
        with mock.patch.object(trace, "run", side_effect=trace.TraceError("synthetic")):
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(trace.main(), 2)

    def test_title_arguments_are_all_or_none(self) -> None:
        with self.assertRaisesRegex(trace.TraceError, "must be provided together"):
            trace.run(
                [
                    "ppc", "bridge", "/decoded",
                    "--expected-chunk-count", "2",
                    "--heap-base", "0x1000", "--heap-size", "0x1000",
                    "--stack-base", "0x3000", "--stack-size", "0x1000",
                    "--title-xex", "/raw.xex",
                ]
            )
        with self.assertRaisesRegex(trace.TraceError, "must be provided together"):
            trace.run(
                [
                    "ppc", "bridge", "/decoded",
                    "--expected-chunk-count", "2",
                    "--heap-base", "0x1000", "--heap-size", "0x1000",
                    "--stack-base", "0x3000", "--stack-size", "0x1000",
                    "--physical-base", "0x5000",
                ]
            )
        with self.assertRaisesRegex(trace.TraceError, "require the title environment"):
            trace.run([
                "ppc", "bridge", "/decoded", "--expected-chunk-count", "2",
                "--heap-base", "0x1000", "--heap-size", "0x1000",
                "--stack-base", "0x3000", "--stack-size", "0x1000",
                "--thread-pcr-base", "0x6000", "--thread-record-base", "0x7000",
                "--thread-tls-base", "0x8000",
            ])
        for extra in (["--thread-pool-base", "0x64000000"],
                      ["--thread-pool-base", "0x64000000", "--thread-pool-size", "0x100000"]):
            with self.assertRaisesRegex(trace.TraceError, "thread-pool arguments require"):
                trace.run(["ppc", "bridge", "/decoded", "--expected-chunk-count", "2",
                    "--heap-base", "0x1000", "--heap-size", "0x1000",
                    "--stack-base", "0x3000", "--stack-size", "0x1000", *extra])


if __name__ == "__main__":
    unittest.main()
