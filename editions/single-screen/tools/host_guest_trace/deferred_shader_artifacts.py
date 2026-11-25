"""Stopped shader sources in a framed prefix, NOT a valid full retail graph."""
import hashlib
import importlib.util
import json
from pathlib import Path


def _load(name):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(name + '.py'))
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


pm4 = _load('stopped_pm4_artifacts')
shaders = _load('stopped_shader_artifacts')


def parse_capture(status, records, native_capture, shader_capture_requested):
    if status is None:
        if records: raise ValueError('deferred shader blobs require capture status')
        return None
    if status not in ('0', '1', '2') or not shader_capture_requested or len(records) > 8:
        raise ValueError('invalid deferred shader capture framing or opt-in')
    if status != '1':
        if records: raise ValueError('unavailable deferred shader capture cannot publish blobs')
        return dict(status=int(status), blobs=[])
    if not native_capture or native_capture['status'] != 1:
        raise ValueError('deferred shaders require the complete owned native command capture')
    pending = native_capture['native_pending_submission']
    if pending['scope'] != 'stopped-pending-native-deferred-swap':
        raise ValueError('deferred shader scope requires its current pending request')
    child = native_capture['blobs'][1]['bytes']
    prefix_words = pending['child_words'] - 129
    if prefix_words <= 0 or pending['command_offset_words'] != prefix_words + 10:
        raise ValueError('native envelope must follow the complete prefix and ten-word tail preamble')
    prefix = child[:prefix_words * 4]
    words, packets = pm4.decode_packets(prefix)
    if len(packets) > 512: raise ValueError('excessive deferred prefix packet count')
    metadata, inline = [], []
    request = pending['deferred_request']
    image = request['frontbuffer_virtual'] - (request['command_virtual'] - request['command_physical'])
    for offset, kind, count, opcode, _, _, predicated, _ in packets:
        if kind != 3 or opcode not in (0x27, 0x2b): continue
        if predicated or count < 2: raise ValueError('conditional or incomplete shader load')
        source, start_size = words[offset + 1:offset + 3]
        immediate = opcode == 0x2b
        stage = source if immediate else source & 3
        size = start_size & 0xffff
        address = 0 if immediate else source & ~3
        if (stage > 1 or start_size >> 16 or not size or size % 3 or
                count != (size + 2 if immediate else 2) or
                address + size * 4 > 0x20000000 or
                not immediate and address < image + 3768320 and image < address + size * 4):
            raise ValueError('invalid deferred shader metadata or requested-image alias')
        metadata.append([1, offset, stage, size, int(immediate), address])
        inline.append(prefix[(offset + 3) * 4:(offset + count + 1) * 4] if immediate else None)
        if len(metadata) > 8: raise ValueError('excessive deferred shader loads')
    # Reuse only the bounded source-record validator. This locally constructed
    # metadata is NOT published as successful stopped full-graph inspection.
    result = shaders.parse_shader_capture('1', records, {'failure': 0, 'shaders': metadata})
    for blob, expected in zip(result['blobs'], inline):
        if expected is not None and blob['bytes'] != expected:
            raise ValueError('inline shader bytes differ from the current owned command prefix')
    result.update(prefix_words=prefix_words, prefix_packets=len(packets),
        prefix_sha256=hashlib.sha256(prefix).hexdigest(),
        child_sha256=hashlib.sha256(child).hexdigest())
    return result


def save_capture(output_directory, capture):
    report = shaders.save_shader_capture(output_directory, capture)
    if report is None: return None
    report.update(scope='stopped-deferred-prefix-shader-snapshot', retail_graph_valid=False,
        rendering_verified=False, ring_retired=False, requested_image_read=False)
    if capture['status'] == 1:
        for key in ('prefix_words', 'prefix_packets', 'prefix_sha256', 'child_sha256'):
            report[key] = capture[key]
    return report


def parse_color_inspection(raw, captured, deferred, ring, native_capture):
    if raw is None: return None
    if len(raw) > 256: raise ValueError('excessive stopped deferred color metadata')
    try: fields = json.loads(raw)
    except (ValueError, RecursionError) as exc: raise ValueError('invalid deferred color metadata') from exc
    if (not isinstance(fields, list) or len(fields) != 10 or
            any(type(v) is not int or not 0 <= v <= 0xffffffff for v in fields) or
            not ring or fields[9] != ring['ring_read_word'] or captured is None):
        raise ValueError('deferred color metadata requires current captured-source scope')
    status, rejection, words, draws, loads, constants, source_bytes, destination, zero, read = fields
    if status not in (0, 1, 2): raise ValueError('invalid deferred color inspection status')
    if status != 1:
        if any(fields[2:9]) or (rejection != 0 if status == 0 else not 1 <= rejection <= 6):
            raise ValueError('failed color inspection cannot publish partial success')
    elif (rejection or captured['status'] != 1 or not deferred or not deferred['pending'] or
            words != captured['prefix_words'] or draws != 4 or loads != len(captured['blobs']) or
            not 0 <= constants <= 32 or not 0 < source_bytes <= 2 * 1024 * 1024 or zero not in (0, 1) or
            destination != deferred['image_physical']):
        raise ValueError('deferred color inspection disagrees with pending sources or image')
    if status == 1:
        if not native_capture or native_capture['status'] != 1:
            raise ValueError('color inspection requires the same complete command capture')
        prefix = native_capture['blobs'][1]['bytes'][:words * 4]
        if hashlib.sha256(prefix).hexdigest() != captured['prefix_sha256']:
            raise ValueError('color inspection prefix differs from captured shader sources')
        decoded, packets = pm4.decode_packets(prefix)
        draw_count = constant_count = constant_bytes = 0
        for offset, kind, count, opcode, _, _, predicated, _ in packets:
            if kind != 3: continue
            if opcode in (0x22, 0x36): draw_count += 1
            if opcode == 0x2f:
                if predicated or count != 3: raise ValueError('invalid constant-load framing')
                address, start, size = decoded[offset + 1:offset + 4]
                if address & 3 or not size or start + size > 2048 or address + size * 4 > 0x20000000:
                    raise ValueError('invalid constant-load extent')
                constant_count += 1; constant_bytes += size * 4
        external_bytes = sum(len(blob['bytes']) for blob in captured['blobs'] if not blob['immediate'])
        # Fixed admitted source profile: depth84 + two color vertices144 each
        # + R8 planes921600/276480/276480 + copy24 + optional tiled4096.
        expected_bytes = 1474956 + (0 if zero else 4096) + external_bytes + constant_bytes
        if draw_count != draws or constant_count != constants or constant_bytes > 8192 or source_bytes != expected_bytes:
            raise ValueError('color source counts/bytes differ from current prefix requests')
    return dict(status=status, rejection_stage=rejection, prefix_words=words, draws=draws,
        shader_loads=loads, constant_loads=constants, source_bytes=source_bytes,
        destination_physical=destination, constant_zero_textures=zero, unchanged_read_word=read,
        scope='stopped-deferred-color-input-admission', gpu_executed=False, guest_writes=False,
        requested_image_read=False, frame_presented=False, ring_retired=False,
        live_allocation_leases_acquired=False, retail_cache_semantics_verified=False)


def parse_memory_inspection(raw, color, captured):
    if raw is None: return None
    if len(raw) > 128: raise ValueError('excessive deferred memory metadata')
    try: fields = json.loads(raw)
    except (ValueError, RecursionError) as exc: raise ValueError('invalid deferred memory metadata') from exc
    if (type(fields) is not list or len(fields) != 4 or
            any(type(v) is not int or not 0 <= v <= 0xffffffff for v in fields) or not color or
            fields[3] != color['unchanged_read_word'] or fields[0] not in (0, 1, 2)):
        raise ValueError('deferred memory inspection requires current color admission scope')
    status, inputs, writes, read = fields
    if status and color['status'] != 1: raise ValueError('memory inspection requires admitted current inputs')
    if status == 1:
        if not captured or captured['status'] != 1: raise ValueError('memory inspection requires current sources')
        expected = 8 - color['constant_zero_textures'] + color['constant_loads'] + sum(
            not blob['immediate'] for blob in captured['blobs'])
        if inputs != expected or writes != 7: raise ValueError('deferred memory range counts disagree')
    elif inputs or writes: raise ValueError('unavailable memory inspection cannot publish partial ranges')
    return dict(status=status, input_ranges=inputs, future_write_ranges=writes, unchanged_read_word=read,
        scope='stopped-deferred-memory-consistency', whole_submission_and_sources_matched=status == 1,
        image_pixels_read=False, gpu_executed=False, guest_writes=False, ring_retired=False,
        live_allocation_leases_acquired=False, interrupt_state_validated=False)


def parse_gpu_inspection(raw, memory):
    if raw is None: return None
    if len(raw)>192: raise ValueError('excessive deferred GPU metadata')
    try: fields=json.loads(raw)
    except (ValueError,RecursionError) as exc: raise ValueError('invalid deferred GPU metadata') from exc
    if (type(fields) is not list or len(fields)!=9 or
            any(type(v) is not int or not 0<=v<=0xffffffff for v in fields) or not memory or
            fields[8]!=memory['unchanged_read_word'] or fields[0] not in (0,1,2)):
        raise ValueError('deferred GPU diagnostic requires current memory inspection')
    status,leading,stages,edram,copy,same,counters,once,read=fields
    if status and memory['status']!=1: raise ValueError('deferred GPU diagnostic requires matched owned inputs')
    if status==1:
        if fields[1:8]!=[1,4,3686400,3686400,1,1,1]: raise ValueError('incomplete deferred GPU profile/audit')
    elif any(fields[1:5]) or any(v not in (0,1) for v in fields[5:8]) or status==0 and any(fields[5:8]):
        raise ValueError('failed deferred GPU diagnostic cannot publish a completed prefix')
    return dict(status=status,scope='stopped-offscreen-deferred-color',gpu_execution_attempted=status!=0,
        completed_profile=status==1,partial_gpu_work_possible=status==2,leading_fences=leading,stage_fences=stages,
        edram_bytes=edram,copy_bytes=copy,guest_sources_unchanged=bool(same),state_counters_unchanged=bool(counters),
        repeat_rejected=bool(once),unchanged_read_word=read,guest_writes=False,frame_presented=False,
        ring_retired=False,guest_event_words_published=0,old_completion_receipt_reused=False,
        live_allocation_leases_acquired=False,retail_cache_semantics_verified=False)


def parse_gpu_timeline(raw, gpu):
    if raw is None: return None  # Historical D-309 did not emit this record.
    if len(raw)>192: raise ValueError('excessive deferred timeline metadata')
    try: fields=json.loads(raw)
    except (ValueError,RecursionError) as exc: raise ValueError('invalid deferred timeline metadata') from exc
    if (type(fields) is not list or len(fields)!=9 or
            any(type(v) is not int or not 0<=v<=0xffffffff for v in fields) or not gpu or
            fields[8]!=gpu['unchanged_read_word'] or fields[0] not in (0,1)):
        raise ValueError('deferred timeline requires current GPU diagnostic')
    status,leading,stages,invalidations,idles,depth,flush,events,read=fields
    if bool(status)!=(gpu['status']==1): raise ValueError('deferred timeline disagrees with GPU completion')
    if status:
        if fields[1:8]!=[1,4,2,1,1,1,0]: raise ValueError('incomplete or borrowed deferred prefix timeline')
    elif any(fields[1:8]): raise ValueError('unavailable timeline cannot publish partial completion')
    return dict(status=status,scope='stopped-deferred-prefix-audited-timeline',leading_fences=leading,
        audited_stages=stages,invalidations=invalidations,copy_idle_cleans=idles,depth_waits=depth,
        cache_flushes=flush,literal_color_events=events,unchanged_read_word=read,
        live_state_committed=False,ring_retired=False,retail_cache_semantics_verified=False)


def parse_pending_state(raw, timeline):
    if raw is None: return None
    if len(raw)>192: raise ValueError('excessive deferred pending-state metadata')
    try: fields=json.loads(raw)
    except (ValueError,RecursionError) as exc: raise ValueError('invalid deferred pending-state metadata') from exc
    if (type(fields) is not list or len(fields)!=9 or
            any(type(v) is not int or not 0<=v<=0xffffffff for v in fields) or not timeline or
            fields[8]!=timeline['unchanged_read_word'] or fields[0] not in (0,1)):
        raise ValueError('deferred pending state requires current prefix diagnostic')
    status,registers,unknown,invalidations,batches,colors,idles,bins,read=fields
    if status!=timeline['status']: raise ValueError('deferred pending state disagrees with current timeline')
    if status:
        if not 1<=registers<=0x8000 or fields[2:8]!=[6,2,1,1,1,1]: raise ValueError('incomplete deferred pending state')
    elif any(fields[1:8]): raise ValueError('unavailable state cannot publish partial candidates')
    return dict(status=status,scope='stopped-deferred-pending-state',registers=registers,
        unknown_completion_registers=unknown,invalidation_delta=invalidations,batch_delta=batches,color_batch_delta=colors,
        unrelated_idle_epoch_preserved=bool(idles),initial_bins_matched=bool(bins),unchanged_read_word=read,
        live_state_committed=False,ring_retired=False,retail_register_readback=False)


def parse_write_guard(raw, gpu, memory, color, ring):
    if raw is None: return None
    if len(raw)>128: raise ValueError('excessive deferred write-guard metadata')
    try: fields=json.loads(raw)
    except (ValueError,RecursionError) as exc: raise ValueError('invalid deferred write-guard metadata') from exc
    if (type(fields) is not list or len(fields)!=4 or
            any(type(v) is not int or not 0<=v<=0xffffffff for v in fields) or not gpu or not memory or not color or not ring or
            fields[3]!=gpu['unchanged_read_word'] or fields[0] not in (0,1,2)):
        raise ValueError('deferred write guard requires current diagnostic scope')
    status,byte_count,mappings,read=fields
    if status and (memory['status']!=1 or color['status']!=1): raise ValueError('write guard requires current inputs/mappings')
    if status==1:
        expected=color['source_bytes']+ring['ring_bytes']+4*(color['prefix_words']+129)+2*3768320+60
        if gpu['status']!=1 or byte_count!=expected or byte_count>32*1024*1024 or mappings!=memory['input_ranges']+12:
            raise ValueError('incomplete deferred before-image audit')
    elif byte_count or mappings: raise ValueError('unavailable guard cannot publish partial snapshots')
    return dict(status=status,scope='stopped-deferred-before-image-audit',snapshot_bytes=byte_count,mappings=mappings,
        unchanged_read_word=read,all_before_images_verified=status==1,guest_writes=False,frame_presented=False,
        ring_retired=False,live_allocation_leases_acquired=False,pixels_decoded=False)
