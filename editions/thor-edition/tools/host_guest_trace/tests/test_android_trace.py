from pathlib import Path
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import run_android_trace as android


class AndroidProtocolTests(unittest.TestCase):
    def test_private_trace_library_requires_ignored_arm64_shared_object(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);private=root/'local-analysis';private.mkdir()
            path=private/'library.so'
            header=bytearray(64);header[:7]=b'\x7fELF\x02\x01\x01';header[16:20]=b'\x03\x00\xb7\x00'
            path.write_bytes(header)
            with mock.patch.object(android.host,'repository_root',return_value=root), \
                    mock.patch.object(android.host,'require_git_ignored') as ignored:
                self.assertEqual(android.validate_private_trace_library(path),path.resolve())
                ignored.assert_called_once()
                for index in (*range(7),*range(16,20),*range(24,32)):
                    for bit in range(8):
                        altered=header.copy();altered[index]^=1<<bit;path.write_bytes(altered)
                        with self.subTest(byte=index,bit=bit),self.assertRaises(android.host.TraceError):
                            android.validate_private_trace_library(path)
                path.write_bytes(header[:63])
                with self.assertRaises(android.host.TraceError):android.validate_private_trace_library(path)
                with path.open('wb') as sparse:sparse.truncate(512*1024*1024+1)
                with self.assertRaises(android.host.TraceError):android.validate_private_trace_library(path)
                path.write_bytes(header)
                alias=private/'link.so';alias.symlink_to(path)
                with self.assertRaises(android.host.TraceError):android.validate_private_trace_library(alias)
                outside=root/'outside.so';outside.write_bytes(header)
                with self.assertRaises(android.host.TraceError):android.validate_private_trace_library(outside)

    def test_image_file_allowance_requires_exact_explicit_evidence(self):
        base=(b'ANDROID_TRACE_SUPERVISOR_V1\noutcome=exited\nexit_code=0\nsignal=0\n'
              b'elapsed_milliseconds=20\nlimits_verified=1\n')
        good=base+b'swap_image_file_limit=3686400\nWORKER_PROTOCOL\nfixture\n'
        result,protocol=android.parse_supervisor(good,swap_image_file=True)
        self.assertTrue(result['limits_verified']);self.assertEqual(protocol,'fixture\n')
        with self.assertRaises(android.host.TraceError):android.parse_supervisor(good)
        for field in (b'',b'swap_image_file_limit=0\n',b'swap_image_file_limit=3686401\n',
                      b'swap_image_file_limit=03686400\n',b'swap_image_file_limit=3686400\n'*2):
            with self.assertRaises(android.host.TraceError):
                android.parse_supervisor(base+field+b'WORKER_PROTOCOL\n',swap_image_file=True)
        for value in (1,None,'true'):
            with self.assertRaises(android.host.TraceError):android.parse_supervisor(good,swap_image_file=value)

    def test_payload_digest_rejects_short_reads_and_changes_before_cache_mutation(self):
        import hashlib
        with tempfile.TemporaryDirectory() as temporary:
            path=Path(temporary)/'source';original=Path.open
            data=b'complete payload\n'*(1024*100)
            path.write_bytes(data)
            self.assertEqual(android.digest(path),hashlib.sha256(data).hexdigest())
            class Incomplete:
                def __init__(self,file,mutate): self.file,self.mutate,self.first=file,mutate,True
                def __enter__(self): return self
                def __exit__(self,*args): self.file.close()
                def fileno(self): return self.file.fileno()
                def read(self,size):
                    if not self.mutate:
                        if not self.first:return b''
                        self.first=False;return self.file.read(3)
                    result=self.file.read(size)
                    if self.first:
                        self.first=False
                        with original(path,'ab') as changed: changed.write(b'changed')
                    return result
            for mutate in (False,True):
                path.write_bytes(data)
                def altered(p,*args,**kwargs): return Incomplete(original(p,*args,**kwargs),mutate)
                with mock.patch.object(Path,'open',altered),self.assertRaises(android.host.TraceError):android.digest(path)
            def partial(p,*args,**kwargs): return Incomplete(original(p,*args,**kwargs),False)
            with mock.patch.object(Path,'open',partial), \
                    mock.patch.object(android,'remote_command',side_effect=[b'2000\n',b'directory 700 2000\n']) as remote, \
                    mock.patch.object(android,'checked_command') as push,self.assertRaises(android.host.TraceError):
                android.stage_cached_content([],path,'/data/local/tmp/recomp-trace.'+'a'*32+'/content-0.bin',
                    '/data/local/tmp/recomp-content-cache.ABC123')
            push.assert_not_called();self.assertEqual(remote.call_count,2)
            path.write_bytes(b'')
            self.assertEqual(android.digest(path),hashlib.sha256(b'').hexdigest())

    def test_emulator_property_is_explicit_and_absence_is_unknown(self):
        for value,expected in ((b"1\n",True),(b"0\r\n",False),(b"\n",None),(b"",None)):
            with mock.patch.object(android,"remote_command",return_value=value) as remote:
                self.assertIs(android.reported_emulator(["adb","-s","explicit"]),expected)
                remote.assert_called_once_with(["adb","-s","explicit"],["getprop","ro.kernel.qemu"])
        for value in (b"true",b"2",b"1\n0",b"1"*17,b"\xff",b" "*17):
            with mock.patch.object(android,"remote_command",return_value=value),self.assertRaises(android.host.TraceError):
                android.reported_emulator([])

    def test_content_cache_reuses_only_verified_private_regular_entries(self):
        cache = "/data/local/tmp/recomp-content-cache.ABC123"
        destination = "/data/local/tmp/recomp-trace." + "a" * 32 + "/content-0.bin"
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "source"
            source.write_bytes(b"a")
            digest = android.digest(source)
            calls = []
            def remote(adb, words, timeout=30):
                calls.append(words)
                if words[:2] == ["id", "-u"]: return b"2000\n"
                if words[:3] == ["stat", "-c", "%F %a %u"]: return b"directory 700 2000\n"
                if words[:3] == ["stat", "-c", "%F %a %u %s"]: return b"regular file 400 2000 1\n"
                if words[0] == "sh": return b"file\n"
                if words[0] == "sha256sum": return f"{digest}  cached\n".encode()
                return b""
            with mock.patch.object(android, "remote_command", side_effect=remote), mock.patch.object(android, "checked_command") as push:
                self.assertTrue(android.stage_cached_content([], source, destination, cache))
                push.assert_not_called()
            self.assertIn(["ln", "--", f"{cache}/{digest}.bin", destination], calls)
            self.assertFalse(any(c[0] in {"rm", "chmod", "mv"} for c in calls))
            calls.clear()
            with mock.patch.object(android, "remote_command", side_effect=remote), mock.patch.object(android, "checked_command") as push:
                self.assertTrue(android.stage_cached_content([], source, destination, cache, link=False))
                push.assert_not_called()
            self.assertFalse(any(c[0] in {"ln", "rm", "chmod", "mv"} for c in calls))
            self.assertTrue(any(c[0] == "sha256sum" for c in calls))
            def corrupt(adb, words, timeout=30):
                return b"wrong  cached\n" if words[0] == "sha256sum" else remote(adb, words, timeout)
            with mock.patch.object(android, "remote_command", side_effect=corrupt), self.assertRaises(android.host.TraceError):
                android.stage_cached_content([], source, destination, cache)

    def test_content_cache_refuses_broad_targets_before_any_device_call(self):
        with mock.patch.object(android, "remote_command") as remote:
            for cache in ("/data/local/tmp", "/", "/data/local/tmp/recomp-content-cache.*", "/data/local/tmp/recomp-content-cache.ABC123/child"):
                with self.assertRaises(android.host.TraceError):
                    android.stage_cached_content([], Path("unused"), "/unsafe", cache)
            remote.assert_not_called()

    def test_direct_cache_mode_requires_explicit_private_cache(self):
        with self.assertRaisesRegex(android.host.TraceError,"requires an explicit cache"):
            android.run(["--ndk","unused","--adb","unused","--serial","explicit",
                         "--device-content-cache-direct"])

    def test_direct_cache_paths_can_override_only_explicit_payloads(self):
        host=android.host
        args=host.parse_args(["ppc","bridge","/image","--expected-chunk-count","2",
                             "--heap-base","0x10000000","--heap-size","0x10000",
                             "--stack-base","0x70000000","--stack-size","0x10000",
                             "--read-only-file","7","/input",
                             "--installed-file","1","2","/installed"])
        layout=host.GeneratedLayout(host.GuestRange("image",0x82000000,0x10000),
                                    host.GuestRange("code",0x82001000,0x1000))
        cached="/data/local/tmp/recomp-content-cache.ABC123/"+"a"*64+".bin"
        with mock.patch.object(host,"read_generated_layout",return_value=layout), \
             mock.patch.object(host,"validate_function_mapping_source",return_value=2):
            baseline=android.worker_arguments(args,Path("ppc"),"/private-test")
            direct=android.worker_arguments(args,Path("ppc"),"/private-test",
                {"content-0.bin":cached,"installed-file-0.bin":cached})
            self.assertEqual(direct,[cached if x in ("/private-test/content-0.bin",
                "/private-test/installed-file-0.bin") else x for x in baseline])
            for key in ("host_guest_trace","decoded.image.bin","file-metadata.bin","content-1.bin","installed-file-1.bin"):
                with self.assertRaises(host.TraceError):
                    android.worker_arguments(args,Path("ppc"),"/private-test",{key:cached})
            for path in ("/data/local/tmp/anything",cached+"/child",cached.replace("a"*64,"a"*63),cached.replace(".ABC123/",".ABC123/../")):
                with self.assertRaises(host.TraceError):
                    android.worker_arguments(args,Path("ppc"),"/private-test",{"content-0.bin":path})

    def test_worker_forwards_explicit_thread_pool_after_thread_metadata(self):
        host = android.host
        args = host.parse_args([
            "ppc", "bridge", "/image", "--expected-chunk-count", "2",
            "--heap-base", "0x10000000", "--heap-size", "0x10000",
            "--stack-base", "0x70000000", "--stack-size", "0x10000",
            "--title-xex", "/title.xex", "--title-header-base", "0x60000000",
            "--title-export-address", "0x62000000", "--title-module-base", "0x62001000",
            "--thread-pcr-base", "0x63000000", "--thread-record-base", "0x63001000",
            "--thread-tls-base", "0x63002000",
            "--thread-pool-base", "0x64000000", "--thread-pool-size", "0x02000000",
        ])
        region = host.GuestRange
        layout = host.GeneratedLayout(region("image", 0x82000000, 0x10000),
                                      region("code", 0x82001000, 0x1000))
        title = SimpleNamespace(raw_xex_size=64, full_image=layout.image,
            data_import_slots=(),
            thread_object_type_slot=0x82000804, xam_minimum_version=0x203F4A00,
            header=region("header", args.title_header_base, 64), import_slot=0x82000800,
            exported_variable=region("export", args.title_export_address, 4),
            module=region("module", args.title_module_base, 100), entry_point=0x82001000)
        thread = host.ThreadEnvironmentInputs(region("pcr", args.thread_pcr_base, 0x2D8),
            region("thread", args.thread_record_base, 0x170),
            region("tls", args.thread_tls_base, 272), 0x82000900, 12, 16, 64)
        with mock.patch.object(host, "read_generated_layout", return_value=layout), \
             mock.patch.object(host, "validate_function_mapping_source", return_value=2), \
             mock.patch.object(host, "validate_title_environment_inputs", return_value=title), \
             mock.patch.object(host, "validate_thread_environment_inputs", return_value=thread):
            result = android.worker_arguments(args, Path("ppc"), "/private-test")
            self.assertEqual(result[-3:], ["--thread-pool", str(0x64000000), str(0x02000000)])
            self.assertEqual(result[-11], "--thread-environment")
            args.xex_image_protections = True
            self.assertEqual(android.worker_arguments(args, Path("ppc"), "/private-test"),
                             result + ['--xex-image-protections'])
            args.disconnected_input = True
            self.assertEqual(android.worker_arguments(args, Path("ppc"), "/private-test"),
                             result+["--xex-image-protections","--disconnected-input"])
            args.experimental_native_audio = True
            self.assertEqual(android.worker_arguments(args, Path("ppc"), "/private-test"),
                             result+["--xex-image-protections","--disconnected-input","--experimental-native-audio"])
            args.experimental_native_audio = False
            args.disconnected_input = False
            args.xex_image_protections = False
            args.thread_pool_base = args.thread_pool_size = None
            self.assertEqual(android.worker_arguments(args, Path("ppc"), "/private-test"), result[:-3])
            args.thread_type_base = 0x62002000
            self.assertEqual(android.worker_arguments(args, Path("ppc"), "/private-test")[-3:],
                ["--thread-object-type", str(0x82000804), str(0x62002000)])
            args.thread_pool_base, args.thread_pool_size = 0x64000000, 0x02000000
            args.cooperative_threads, args.thread_quantum = True, 7
            self.assertEqual(android.worker_arguments(args, Path("ppc"), "/private-test")[-2:],
                ["--cooperative-threads", "7"])
            args.experimental_native_audio_renderer = True
            with self.assertRaises(host.TraceError):android.worker_arguments(args,Path("ppc"),"/private-test")
            args.experimental_native_audio = True
            self.assertEqual(android.worker_arguments(args,Path("ppc"),"/private-test")[-2:],
                ["--experimental-native-audio","--experimental-native-audio-renderer"])
            args.cooperative_threads = False
            with self.assertRaises(host.TraceError):android.worker_arguments(args,Path("ppc"),"/private-test")
            args.cooperative_threads = True
            args.experimental_native_audio_renderer = args.experimental_native_audio = False
            args.native_event_wake_boost = True
            self.assertEqual(android.worker_arguments(args,Path("ppc"),"/private-test")[-1],"--native-event-wake-boost")
            args.cooperative_threads = False
            with self.assertRaises(host.TraceError):android.worker_arguments(args,Path("ppc"),"/private-test")
            args.cooperative_threads = True
            args.native_event_wake_boost = False
            args.native_category1_unity_gain = True
            with self.assertRaises(host.TraceError):android.worker_arguments(args,Path("ppc"),"/private-test")
            args.experimental_native_audio_renderer = args.experimental_native_audio = True
            self.assertEqual(android.worker_arguments(args,Path("ppc"),"/private-test")[-1],"--native-category1-unity-gain")
            args.native_category1_unity_gain = args.experimental_native_audio_renderer = args.experimental_native_audio = False
            args.xam_module_base = 0x62003000
            args.virtual_video_mode = "720p60"
            self.assertEqual(android.worker_arguments(args, Path("ppc"), "/private-test")[-4:],
                ["--cooperative-threads", "7", "--virtual-video-mode", "720p60"])
            args.graphics_backend = "vulkan-headless"
            self.assertEqual(android.worker_arguments(args, Path("ppc"), "/private-test")[-6:],
                ["--cooperative-threads", "7", "--virtual-video-mode", "720p60",
                 "--graphics-backend", "vulkan-headless"])
            args.graphics_backend = None
            args.experimental_gpu_clock_500mhz = True
            with self.assertRaises(host.TraceError):
                android.worker_arguments(args, Path("ppc"), "/private-test")
            args.graphics_backend = "vulkan-headless"
            self.assertEqual(android.worker_arguments(args, Path("ppc"), "/private-test")[-3:],
                ["--graphics-backend", "vulkan-headless", "--experimental-gpu-clock-500mhz"])
            args.native_initial_coherency = True
            args.experimental_native_initialization = True
            self.assertEqual(android.worker_arguments(args, Path("ppc"), "/private-test")[-3:],
                ["--native-initial-coherency", "--experimental-native-initialization", "--experimental-gpu-clock-500mhz"])
            args.native_empty_cache_coherency = True
            self.assertEqual(android.worker_arguments(args, Path("ppc"), "/private-test")[-4:],
                ["--native-initial-coherency", "--experimental-native-initialization", "--native-empty-cache-coherency", "--experimental-gpu-clock-500mhz"])
            args.native_state_batches = True
            args.capture_stopped_shaders = args.capture_stopped_pm4 = True
            self.assertEqual(android.worker_arguments(args, Path("ppc"), "/private-test")[-7:],
                ["--native-initial-coherency", "--experimental-native-initialization", "--native-empty-cache-coherency",
                 "--native-state-batches", "--capture-stopped-shaders", "--capture-stopped-pm4", "--experimental-gpu-clock-500mhz"])
            args.capture_stopped_shaders = args.capture_stopped_pm4 = False
            args.capture_stopped_range = [[0x1000,12],[0x2000,16]]
            self.assertEqual(android.worker_arguments(args,Path('ppc'),'/private-test')[-6:],
                ['--capture-stopped-range','4096','12','--capture-stopped-range','8192','16'])
            args.inspect_stopped_ownership=[[0x1F2D8000,3768320]]
            self.assertEqual(android.worker_arguments(args,Path('ppc'),'/private-test')[-9:],
                ['--capture-stopped-range','4096','12','--capture-stopped-range','8192','16',
                 '--inspect-stopped-ownership',str(0x1F2D8000),'3768320'])
            args.inspect_stopped_ownership=[[0x1F2D8000,0x1000004]]
            with self.assertRaises(host.TraceError):android.worker_arguments(args,Path('ppc'),'/private-test')
            args.inspect_stopped_ownership=[]
            args.capture_stopped_byte_budget=65536
            args.capture_stopped_range=[[0x1000,65536]]
            self.assertEqual(android.worker_arguments(args,Path('ppc'),'/private-test')[-5:],
                ['--capture-stopped-byte-budget','65536','--capture-stopped-range','4096','65536'])
            args.capture_stopped_range=[]
            with self.assertRaises(host.TraceError):android.worker_arguments(args,Path('ppc'),'/private-test')
            args.capture_stopped_byte_budget=4096
            args.capture_stopped_range = [[0x1000,12],[0x1004,16]]
            with self.assertRaises(host.TraceError):android.worker_arguments(args,Path('ppc'),'/private-test')
            args.capture_stopped_range = []
            args.experimental_native_initialization = False
            with self.assertRaises(host.TraceError): android.worker_arguments(args, Path("ppc"), "/private-test")
            args.experimental_native_initialization = True
            args.native_empty_cache_coherency = False
            args.experimental_native_initialization = False
            with self.assertRaises(host.TraceError): android.worker_arguments(args, Path("ppc"), "/private-test")
            args.native_state_batches = False
            args.experimental_native_initialization = True
            args.native_initial_coherency = False
            with self.assertRaises(host.TraceError): android.worker_arguments(args, Path("ppc"), "/private-test")
            args.native_initial_coherency = True
            args.experimental_native_initialization = False
            self.assertEqual(android.worker_arguments(args, Path("ppc"), "/private-test")[-4:],
                ["--graphics-backend", "vulkan-headless", "--native-initial-coherency", "--experimental-gpu-clock-500mhz"])
            args.capture_stopped_shaders = True
            self.assertEqual(android.worker_arguments(args, Path("ppc"), "/private-test")[-5:],
                ["--graphics-backend", "vulkan-headless", "--native-initial-coherency",
                 "--capture-stopped-shaders", "--experimental-gpu-clock-500mhz"])
            args.capture_stopped_pm4 = True
            self.assertEqual(android.worker_arguments(args, Path("ppc"), "/private-test")[-3:],
                ["--capture-stopped-shaders", "--capture-stopped-pm4", "--experimental-gpu-clock-500mhz"])
            args.capture_stopped_pm4 = False
            args.capture_stopped_shaders = False
            args.cooperative_threads = False
            with self.assertRaises(host.TraceError): android.worker_arguments(args, Path("ppc"), "/private-test")
            args.cooperative_threads = True
            args.native_initial_coherency = False
            args.graphics_backend = None
            args.experimental_gpu_clock_500mhz = False
            args.capture_stopped_shaders = True
            with self.assertRaises(host.TraceError): android.worker_arguments(args, Path("ppc"), "/private-test")
            args.capture_stopped_shaders = False
            title.data_import_slots = (0x82000800, 0x82000804, 0x82000808)
            args.capture_stopped_pm4 = True
            with self.assertRaises(host.TraceError): android.worker_arguments(args, Path("ppc"), "/private-test")
            args.capture_stopped_pm4 = False
            self.assertEqual(android.worker_arguments(args, Path("ppc"), "/private-test")[-2:],
                ["--unbound-data-imports", str(0x82000808)])
            title.data_import_slots = ()
            title.hardware_info_slot = 0x82000808
            title.video_device_slots = (0x8200080C, 0x82000810)
            title.data_import_slots = (0x82000800, 0x82000804, 0x82000808, 0x8200080C, 0x82000810, 0x82000814)
            args.hardware_info_base, args.video_globals_base = 0x62004000, 0x62004100
            self.assertEqual(android.worker_arguments(args, Path("ppc"), "/private-test")[-9:],
                ["--unbound-data-imports", str(0x82000814),
                 "--hardware-info", str(0x82000808), str(0x62004000),
                 "--video-globals", str(0x8200080C), str(0x82000810), str(0x62004100)])
            args.hardware_info_base = args.video_globals_base = None
            title.uptime_bundle_slot = 0x82000814
            args.uptime_bundle_base = 0x62004200
            self.assertEqual(android.worker_arguments(args, Path("ppc"), "/private-test")[-3:],
                ["--uptime-bundle", str(0x82000814), str(0x62004200)])
            args.uptime_bundle_base = None
            title.command_line_slot = 0x82000818
            args.command_line_base = 0x62004300
            self.assertEqual(android.worker_arguments(args, Path("ppc"), "/private-test")[-3:],
                ["--command-line", str(0x82000818), str(0x62004300)])
            args.command_line_base = None
            title.debug_monitor_slot = 0x8200081C
            args.no_debug_monitor_base = 0x62004400
            title.data_import_slots = (0x8200081C,)
            configured = android.worker_arguments(args, Path("ppc"), "/private-test")
            self.assertEqual(configured[-3:],
                ["--no-debug-monitor", str(0x8200081C), str(0x62004400)])
            self.assertNotIn("--unbound-data-imports", configured)
            args.no_debug_monitor_base = None
            self.assertEqual(android.worker_arguments(args, Path("ppc"), "/private-test")[-2:],
                ["--unbound-data-imports", str(0x8200081C)])
            title.cert_monitor_slot = 0x82000820
            title.data_import_slots = (0x8200081C,0x82000820)
            args.no_debug_monitor_base, args.no_cert_monitor_base = 0x62004400, 0x62004500
            configured = android.worker_arguments(args, Path("ppc"), "/private-test")
            self.assertEqual(configured[-6:], ["--no-debug-monitor", str(0x8200081C), str(0x62004400),
                                              "--no-cert-monitor", str(0x82000820), str(0x62004500)])
            self.assertNotIn("--unbound-data-imports", configured)
            args.no_debug_monitor_base = args.no_cert_monitor_base = None
            title.hsio_lock_slot = 0x82000824
            title.data_import_slots = (0x82000824,)
            args.hsio_lock_base = 0x62004600
            configured = android.worker_arguments(args, Path("ppc"), "/private-test")
            self.assertEqual(configured[-3:], ["--hsio-lock",str(0x82000824),str(0x62004600)])
            self.assertNotIn("--unbound-data-imports",configured)
            args.hsio_lock_base = None
            title.kernel_version_slot = 0x82000828
            title.kernel_minimum_version = 0x203F4A00
            title.data_import_slots = (0x82000828,)
            args.native_kernel_version_base = 0x62004800
            configured = android.worker_arguments(args, Path('ppc'), '/private-test')
            self.assertEqual(configured[-4:], ['--native-kernel-version', str(0x82000828),
                str(0x62004800), str(0x203F4A00)])
            self.assertIn('--unbound-data-imports', configured)
            title.kernel_minimum_version = 0x103F4A00
            with self.assertRaises(host.TraceError):
                android.worker_arguments(args, Path('ppc'), '/private-test')
            title.kernel_minimum_version = 0x203F4A00
            args.native_kernel_version_base = None
            title.data_import_slots = ()
            args.virtual_video_mode = None
            self.assertEqual(android.worker_arguments(args, Path("ppc"), "/private-test")[-5:],
                ["--native-xam-module", str(0x62003000), str(0x203F4A00), "--cooperative-threads", "7"])
            args.file_metadata_catalog = Path("/private/catalog.bin")
            self.assertEqual(android.worker_arguments(args, Path("ppc"), "/private-test")[-7:],
                ["--file-metadata-catalog", "/private-test/file-metadata.bin",
                 "--native-xam-module", str(0x62003000), str(0x203F4A00), "--cooperative-threads", "7"])
            args.read_only_file = [["151", "/private/common.bin"]]
            self.assertEqual(android.worker_arguments(args, Path("ppc"), "/private-test")[-10:-5],
                ["--file-metadata-catalog", "/private-test/file-metadata.bin", "--read-only-file", "151", "/private-test/content-0.bin"])
            args.installed_content_catalog = Path('/private/installed-catalog')
            args.installed_package = [['1', '/private/package-metadata']]
            args.installed_file = [['1', '2', '/private/actual-content']]
            self.assertEqual(android.worker_arguments(args, Path('ppc'), '/private-test')[-19:-10],
                ['--installed-content-catalog', '/private-test/installed-content.bin',
                 '--installed-package', '1', '/private-test/installed-package-1.bin',
                 '--installed-file', '1', '2', '/private-test/installed-file-0.bin'])

    def test_cleanup_rejects_broad_and_injected_targets(self):
        for target in ("/data/local/tmp", "/", "/data/local/tmp/recomp-trace.*",
                       "/data/local/tmp/recomp-trace." + "a" * 32 + ";reboot"):
            with self.assertRaises(android.host.TraceError):
                android.cleanup_commands(target)
        target = "/data/local/tmp/recomp-trace." + "a" * 32
        commands = android.cleanup_commands(target)
        self.assertEqual(commands[1], ["rmdir", "--", target])
        self.assertTrue(all(value.startswith(target + "/") for value in commands[0][3:]))

    def test_rejects_failed_worker_payload_and_duplicate_fields(self):
        for header in (
            b"outcome=cpu-limit\nexit_code=-1\nsignal=24\nelapsed_milliseconds=5\nlimits_verified=1\n",
            b"outcome=exited\nexit_code=0\nsignal=0\nexit_code=0\nlimits_verified=1\n",
        ):
            with self.assertRaises(android.host.TraceError):
                android.parse_supervisor(b"ANDROID_TRACE_SUPERVISOR_V1\n" + header + b"WORKER_PROTOCOL\nprivate")

    def test_rejects_excessive_output(self):
        with self.assertRaises(android.host.TraceError):
            android.parse_supervisor(b"x" * (65536 + 513))

    def test_expanded_output_requires_explicit_bounded_allowance(self):
        payload = b"x" * 70000
        output = (b"ANDROID_TRACE_SUPERVISOR_V1\noutcome=exited\nexit_code=0\nsignal=0\n"
                  b"elapsed_milliseconds=20\nlimits_verified=1\nWORKER_PROTOCOL\n" + payload)
        with self.assertRaises(android.host.TraceError):
            android.parse_supervisor(output)
        result, protocol = android.parse_supervisor(output, 131072)
        self.assertEqual(result['outcome'], 'exited')
        self.assertEqual(protocol, payload.decode())
        with self.assertRaises(android.host.TraceError):
            android.parse_supervisor(output[:output.index(payload)] + b'x' * 65537)
        maximum = android.host.physical_capture.protocol_byte_limit(
            android.host.physical_capture.MAX_BYTE_BUDGET)
        for limit in (True, 65535, maximum + 1, -1, '131072'):
            with self.assertRaises(android.host.TraceError):
                android.parse_supervisor(output, limit)

    def test_rejects_inconsistent_exit_and_signal_fields(self):
        for fields in (b"outcome=exited\nexit_code=-1\nsignal=6\n",
                       b"outcome=cpu-limit\nexit_code=0\nsignal=0\n",
                       b"outcome=unix-signal\nexit_code=-1\nsignal=0\n"):
            with self.assertRaises(android.host.TraceError):
                android.parse_supervisor(b"ANDROID_TRACE_SUPERVISOR_V1\n" + fields
                    + b"elapsed_milliseconds=20\nlimits_verified=1\nWORKER_PROTOCOL\n")


@unittest.skipUnless(shutil.which("cc") and os.name == "posix", "requires POSIX C compiler")
class AndroidSupervisorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(prefix="android-supervisor-test-")
        cls.directory = Path(cls.temporary.name)
        cls.directory.chmod(0o700)
        root = Path(__file__).resolve().parents[1]
        for name, source in (("supervisor", root / "android_trace_supervisor.c"),
                             ("fixture", Path(__file__).with_name("android_supervisor_fixture.c"))):
            subprocess.run(["cc", "-std=c11", "-Wall", "-Wextra", "-Werror", "-O0",
                            str(source), "-o", str(cls.directory / name)], check=True, capture_output=True)
            (cls.directory / name).chmod(0o700)

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def run_fixture(self, mode, wall=3, protocol_limit=None,swap_image_file=False):
        command = [str(self.directory / "supervisor"), str(wall), "1"]
        if protocol_limit is not None:
            command.extend(['--protocol-bytes', str(protocol_limit)])
        if swap_image_file:command.append('--swap-image-file')
        command.extend([str(self.directory), str(self.directory / "fixture"), mode])
        completed = subprocess.run(command, capture_output=True, timeout=wall + 5)
        self.assertEqual(completed.returncode, 0)
        self.assertEqual(completed.stderr, b"")
        return android.parse_supervisor(completed.stdout,
            65536 if protocol_limit is None else protocol_limit,swap_image_file=swap_image_file)

    def test_image_file_default_zero_and_explicit_limit_enforced(self):
        path=self.directory/'synthetic-image.bin'
        for mode,opt_in,size,expected in (('image',False,0,'unix-signal'),
                ('image',True,3686400,'exited'),('image-overflow',True,3686400,'unix-signal')):
            try:
                result,protocol=self.run_fixture(mode,swap_image_file=opt_in)
                self.assertEqual(result['outcome'],expected)
                self.assertTrue(result['limits_verified']);self.assertEqual(path.stat().st_size,size)
                if expected=='unix-signal':
                    self.assertEqual(result['signal_number'],signal.SIGXFSZ);self.assertEqual(protocol,'')
                else:self.assertEqual(protocol,'synthetic-ok\n')
            finally:path.unlink(missing_ok=True)

    def test_large_protocol_requires_opt_in_and_remains_bounded(self):
        result, protocol = self.run_fixture('large')
        self.assertEqual(result['outcome'], 'protocol-limit')
        self.assertEqual(protocol, '')
        result, protocol = self.run_fixture('large', protocol_limit=131072)
        self.assertEqual(result['outcome'], 'exited')
        self.assertEqual(protocol, 'x' * 70000 + 'synthetic-ok\n')
        result, protocol = self.run_fixture('flood', protocol_limit=131072)
        self.assertEqual(result['outcome'], 'protocol-limit')
        self.assertEqual(protocol, '')

    def test_invalid_native_protocol_allowance_does_not_launch(self):
        maximum = android.host.physical_capture.protocol_byte_limit(
            android.host.physical_capture.MAX_BYTE_BUDGET)
        for limit in ('65535', str(maximum + 1), '-1', '131072x', '', '999999999999999999999999'):
            completed = subprocess.run([str(self.directory / 'supervisor'), '3', '1',
                '--protocol-bytes', limit, str(self.directory), str(self.directory / 'fixture'),
                'limits'], capture_output=True, timeout=5)
            self.assertEqual(completed.returncode, 125)
            self.assertEqual(completed.stdout, b'')

    def test_success_checks_child_limits(self):
        result, protocol = self.run_fixture("limits")
        self.assertEqual(result["outcome"], "exited")
        self.assertEqual(result["returncode"], 0)
        self.assertTrue(result["limits_verified"])
        self.assertEqual(protocol, "synthetic-ok\n")

    def test_signal_is_redacted(self):
        result, protocol = self.run_fixture("signal")
        self.assertEqual(result["outcome"], "unix-signal")
        self.assertEqual(result["signal_number"], signal.SIGABRT)
        self.assertEqual(protocol, "")

    def test_wall_timeout_is_on_supervisor(self):
        result, protocol = self.run_fixture("hang", wall=1)
        self.assertEqual(result["outcome"], "wall-timeout")
        self.assertEqual(protocol, "")

    def test_cpu_limit(self):
        result, protocol = self.run_fixture("cpu", wall=4)
        self.assertEqual(result["outcome"], "cpu-limit")
        self.assertEqual(protocol, "")

    def test_output_flood_is_bounded(self):
        result, protocol = self.run_fixture("flood")
        self.assertEqual(result["outcome"], "protocol-limit")
        self.assertEqual(protocol, "")

    def test_descendant_is_killed_after_parent_exits(self):
        result, protocol = self.run_fixture("descendant")
        self.assertEqual(result["returncode"], 0)
        child = int(protocol.strip())
        status = subprocess.run(["ps", "-o", "stat=", "-p", str(child)], capture_output=True).stdout.strip()
        self.assertTrue(not status or status.startswith(b"Z"), status)

    def test_supervisor_survives_hangup_until_its_wall_deadline(self):
        process = subprocess.Popen([str(self.directory / "supervisor"), "1", "1",
            str(self.directory), str(self.directory / "fixture"), "hang"], stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL)
        time.sleep(0.1)
        process.send_signal(signal.SIGHUP)
        stdout, _ = process.communicate(timeout=5)
        self.assertEqual(process.returncode, 0)
        result, protocol = android.parse_supervisor(stdout)
        self.assertEqual(result["outcome"], "wall-timeout")
        self.assertEqual(protocol, "")


if __name__ == "__main__":
    unittest.main()
