"""Explicit bounded allocation-owned stopped snapshots, never a resource upload."""
import hashlib
import json
import os
from pathlib import Path
import re
import struct
import tempfile

DEFAULT_BYTE_BUDGET=4096
MAX_BYTE_BUDGET=2*1024*1024
MAX_RECORD_CHARS=MAX_BYTE_BUDGET*2+64


def validate_budget(byte_budget):
    if type(byte_budget) is not int or not 4<=byte_budget<=MAX_BYTE_BUDGET or byte_budget&3:
        raise ValueError('physical capture byte budget must be aligned and within4..2097152')


def validate_ranges(ranges,byte_budget=DEFAULT_BYTE_BUDGET):
    validate_budget(byte_budget)
    if not isinstance(ranges, list) or not 1 <= len(ranges) <= 8:
        raise ValueError('physical capture requires one to eight explicit ranges')
    total = 0
    for i, r in enumerate(ranges):
        if (not isinstance(r, (list, tuple)) or len(r) != 2 or
                any(type(v) is not int for v in r)):
            raise ValueError('invalid physical capture range')
        a, n = r
        if a < 0 or n <= 0 or (a | n) & 3 or a+n > 0x20000000 or n > byte_budget-total:
            raise ValueError('physical capture range bounds exceeded')
        total += n
        if any(a < b+m and b < a+n for b,m in ranges[:i]):
            raise ValueError('physical capture ranges overlap')


def protocol_byte_limit(byte_budget=DEFAULT_BYTE_BUDGET):
    validate_budget(byte_budget)
    return 64*1024+max(0,byte_budget-DEFAULT_BYTE_BUDGET)*2


def parse_native_pending_submission(raw,submission,graph,ring,swap,coherency,scaler=None,scaler_storage=None,
        deferred=None,deferred_storage=None):
    if raw is None:return None
    if (not re.fullmatch(r'(?:0|[1-9][0-9]{0,9})(?:,(?:0|[1-9][0-9]{0,9})){6}',raw) or
        not swap or not coherency):raise ValueError('native pending diagnostic requires live-mode evidence')
    present,root,child,words,offset,read,write=map(int,raw.split(','))
    if (max(present,root,child,words,offset,read,write)>0xFFFFFFFF or present not in (0,1) or
        (not present and any((root,child,words,offset,read,write)))):
        raise ValueError('invalid native pending diagnostic fields')
    scaler_scope=bool(scaler and scaler_storage and scaler_storage['completed_batches'])
    deferred_scope=bool(deferred and deferred_storage and deferred_storage['completed_batches'])
    if deferred_scope:
        pending_read=deferred_storage['read_word'];command=deferred['request']['command_physical']
        valid_pending=(deferred['issued'] and deferred['pending'] and deferred['ticket']==3 and
            deferred['issued_read_word']==deferred_storage['previous_word'] and
            deferred_storage['completed_batches']==1 and pending_read==deferred_storage['previous_word']+3 and
            deferred_storage['physical_base']==coherency['physical_base'] and
            deferred_storage['byte_count']==coherency['byte_count'])
    elif scaler_scope:
        pending_read=scaler_storage['read_word'];command=scaler_storage['command_physical']
        valid_pending=(scaler['pending'] and scaler['issued_commands']==1 and scaler_storage['completed_batches']==1 and
            swap['present_queued'] and not swap['pending'] and scaler_storage['previous_word']==swap['read_word'] and
            scaler_storage['previous_word']==scaler['previous_read_word'] and pending_read==scaler_storage['previous_word']+3 and
            scaler_storage['physical_base']==coherency['physical_base'] and scaler_storage['byte_count']==coherency['byte_count'])
    else:
        pending_read=swap['read_word'];command=swap['command_physical']
        valid_pending=swap['pending'] and not swap['present_queued'] and pending_read==coherency['read_word']
    if present and (not submission or submission['status']!=5 or not graph or graph['failure']!=7 or
        graph['error_parent'] or graph['error_offset'] or graph['decode_failure']!=4 or graph['decode_opcode']!=0x53 or
        graph['decode_offset']!=offset or not ring or coherency['completed_batches']!=1 or
        not valid_pending or pending_read!=read or write!=read+3 or write>=ring['ring_bytes']//4 or
        ring['ring_read_word']!=read or root!=ring['ring_physical']+read*4 or
        submission['read_word']!=read or submission['write_word']!=write or
        submission['word_count']!=3 or submission['packet_count']!=1 or
        submission['packets']!=[[0,3,2,63,0,0,0,0]] or child&3 or not 64<=words<=(8*1024*1024//4 if deferred_scope else 1024) or
        offset+64>words or child+offset*4!=command or
        child+words*4>0x20000000 or child<coherency['physical_base'] or child+words*4>coherency['physical_base']+coherency['byte_count']):
        raise ValueError('inconsistent bounded native pending submission')
    result=dict(captured=bool(present),root_physical=root,child_physical=child,child_words=words,
        command_offset_words=offset,read_word=read,write_word=write,
        scope=('stopped-pending-native-deferred-swap' if deferred_scope else
            'stopped-pending-native-scaler-wrapper' if scaler_scope else 'stopped-pending-native-wrapper'),writers_stopped=True,
        retail_graph_valid=False,executed=False,guest_memory_changed=False)
    if deferred_scope and present:result['deferred_request']=deferred['request']
    return result


def parse_scaler_wrapper_plan(raw,pending,scaler):
    if raw is None:return None
    if not scaler or not re.fullmatch(r'(?:0|[1-9][0-9]{0,9})(?:,(?:0|[1-9][0-9]{0,9})){9}',raw):
        raise ValueError('scaler wrapper plan requires native producer evidence')
    fields=list(map(int,raw.split(',')))
    present,scratch,callback,context,routine,user_data,known_mask,mask,known_base,base=fields
    if (max(fields)>0xFFFFFFFF or any(v not in (0,1) for v in (present,known_mask,known_base)) or
        (not present and any(fields)) or (not known_mask and mask) or (not known_base and base)):
        raise ValueError('invalid scaler wrapper metadata fields')
    if present and (not pending or not pending['captured'] or pending['scope']!='stopped-pending-native-scaler-wrapper' or
        pending['child_words']!=145 or pending['command_offset_words']!=69 or not scaler['pending'] or
        scratch&3 or scratch+32>0x20000000 or not callback or callback&3 or routine&3 or
        (not routine and user_data) or (known_base and (base&3 or base+32>0x20000000)) or
        (scratch<pending['child_physical']+580 and pending['child_physical']<scratch+32)):
        raise ValueError('inconsistent scaler wrapper metadata provenance')
    return dict(captured=bool(present),scratch_physical=scratch,callback_address=callback,callback_context=context,
        registered_routine=routine,registered_user_data=user_data,retained_mask_known=bool(known_mask),retained_mask=mask,
        retained_base_known=bool(known_base),retained_base=base,
        retained_scratch_matches=bool(present and known_mask and known_base and (mask&~255)==0x20000 and (mask&0x33)==0x33 and base==scratch),
        operation_count=26 if present else 0,interrupt_cpu_mask=4 if present else 0,
        scope='stopped-native-scaler-wrapper-request-plan',writers_stopped=True,
        executed=False,callback_dispatched=False,guest_memory_changed=False,retail_scanout_verified=False)


def post_swap_request_ranges(request):
    """Bounds from an independently validated rejected VdSwap record; no lease.

    Image range, if layout-recognized, is eligible for metadata queries ONLY.
    Command bytes beyond the requested ticket and image payloads are excluded.
    """
    if not request or not request.get('captured'):return None
    if request.get('metadata_only') is not True or any(request.get(k) is not False for k in
            ('pixels_decoded','commands_written','frame_presented','next_image_published','next_swap_completed')):
        raise ValueError('read-only stopped request provenance required')
    fields=[request.get(k) for k in ('command_virtual','command_physical','allocation_base','allocation_bytes')]
    if any(type(v) is not int or not 0<=v<=0xFFFFFFFF for v in fields):
        raise ValueError('invalid command request bounds')
    command,physical,owner,size=fields
    base=physical-(command-owner)
    if ((command|physical|owner|size)&3 or size<256 or not owner<=command<=owner+size-256 or
            owner+size>0x100000000 or command<physical or base<0 or base+size>0x20000000):
        raise ValueError('invalid stopped request command owner')
    owners=[(base,size)]
    front=request.get('frontbuffer_virtual');image=None
    if (type(front) is int and 0<front<=0xFFFFFFFF and not front&4095 and
            request.get('format')==6 and request.get('color_space')==0 and
            request.get('width')==1280 and request.get('height')==720 and
            request.get('fetch_words')==[0x8A000002,front|6,0x0059E4FF,0x1414,0,0x200]):
        address=front-(command-physical);capacity=1280*736*4
        if address>=0 and not address&4095 and address+capacity<=0x20000000 and front+capacity<=0x100000000:
            if address<base+size and base<address+capacity:raise ValueError('requested image aliases command owner')
            image=(address,capacity);owners.append(image)
    return dict(payload=(base,physical+256-base),owners=owners)


def validate_post_swap_owners(owners,ranges,request,require_command_owner=False):
    scope=post_swap_request_ranges(request)
    if not scope:return
    base,size=scope['owners'][0];translation=request['command_virtual']-request['command_physical']
    for owner,(address,count) in zip(owners,ranges):
        if owner is None:
            if require_command_owner:raise ValueError('captured request commands require their exact owner')
            continue
        if owner['virtual_base']-owner['physical_base']!=translation:
            raise ValueError('stopped request owner uses a different virtual translation')
        if base<=address<address+count<=base+size:
            if (owner['physical_base'],owner['virtual_base'],owner['byte_count'],owner['protection'])!=(
                    base,request['allocation_base'],size,request['protection']):
                raise ValueError('stopped request command owner differs from request')
        elif owner['physical_base']<base+size and base<owner['physical_base']+owner['byte_count']:
            raise ValueError('stopped request image owner overlaps command owner')


def parse_capture(status, requested, records, graph,byte_budget_text=None,native_pending=None,scaler_plan=None,post_swap_request=None):
    if status is None:
        if requested is not None or records or byte_budget_text is not None: raise ValueError('physical capture requires explicit status')
        return None
    if byte_budget_text is not None and not re.fullmatch(r'[1-9][0-9]{0,6}',byte_budget_text):
        raise ValueError('invalid physical capture budget record')
    byte_budget=DEFAULT_BYTE_BUDGET if byte_budget_text is None else int(byte_budget_text)
    if status not in ('0','1','2') or requested is None or len(requested) > 512 or len(records) > 8:
        raise ValueError('invalid physical capture status or bounds')
    try: ranges = json.loads(requested)
    except (ValueError, RecursionError) as error: raise ValueError('invalid physical ranges JSON') from error
    validate_ranges(ranges,byte_budget)
    if status != '1':
        if records: raise ValueError('failed physical capture cannot publish a prefix')
        return {'status':int(status), 'requests':ranges, 'blobs':[],'byte_budget':byte_budget}
    native_scope=bool(native_pending and native_pending['captured'] and (not graph or graph['failure']))
    request_scope=post_swap_request_ranges(post_swap_request) if graph is None else None
    if request_scope:
        base,size=request_scope['payload']
        if any(not base<=a<a+n<=base+size for a,n in ranges):
            raise ValueError('stopped swap payload capture must stay inside its command prefix/request')
    native_ranges=[]
    if native_scope:
        native_ranges=[(native_pending['root_physical'],12),(native_pending['child_physical'],native_pending['child_words']*4)]
        if scaler_plan and scaler_plan['captured'] and native_pending['scope']=='stopped-pending-native-scaler-wrapper':
            native_ranges.append((scaler_plan['scratch_physical'],32))
    deferred_scope=bool(native_scope and native_pending['scope']=='stopped-pending-native-deferred-swap')
    if native_scope and (byte_budget>(16384 if deferred_scope else 4096) or any(not any(base<=a and a+n<=base+size for base,size in native_ranges)
            for a,n in ranges)):
        raise ValueError('native pending capture must stay inside its bounded root/child or validated scaler scratch')
    if (not native_scope and not request_scope and (graph is None or graph['failure'])) or len(records) != len(ranges):
        raise ValueError('physical capture requires a complete stopped graph and all ranges')
    blobs = []
    for raw, (a,n) in zip(records,ranges):
        if len(raw) > byte_budget*2+64 or not re.fullmatch(r'(?:0|[1-9][0-9]{0,9}),[1-9][0-9]{0,6},[0-9a-f]+',raw):
            raise ValueError('invalid private physical record')
        address, count, encoded = raw.split(',')
        if [int(address),int(count)] != [a,n] or len(encoded) != n*2:
            raise ValueError('physical bytes differ from requested extent')
        blobs.append({'physical_address':a, 'byte_count':n, 'bytes':bytes.fromhex(encoded)})
    result={'status':1, 'requests':ranges, 'blobs':blobs,'byte_budget':byte_budget}
    if native_scope:result['native_pending_submission']=native_pending
    if native_scope and scaler_plan and scaler_plan['captured']:result['native_scaler_wrapper_plan']=scaler_plan
    if request_scope:result['stopped_swap_request']=post_swap_request
    return result


def require_requested(capture, ranges,byte_budget=DEFAULT_BYTE_BUDGET):
    validate_budget(byte_budget)
    if ((capture is None) != (not ranges) or (capture is None and byte_budget!=DEFAULT_BYTE_BUDGET) or
        (capture is not None and (capture['requests'] != ranges or capture['byte_budget']!=byte_budget))):
        raise ValueError('physical capture differs from explicitly requested ranges')


def parse_native_pending_capture(status,records,owners,pending,graph,deferred,command_capture_requested):
    """Current owned root/child, separately labeled from the invalid PM4 graph."""
    if status is None:
        if records or owners is not None:raise ValueError('native command blobs require capture status')
        return None
    if status not in ('0','1','2') or not command_capture_requested or len(records)>2:
        raise ValueError('invalid native command capture framing')
    if status!='1':
        if records or owners!='[]':raise ValueError('failed native capture cannot publish a prefix')
        return dict(status=int(status),requests=[],blobs=[],byte_budget=16384)
    if (not pending or not pending['captured'] or pending['scope']!='stopped-pending-native-deferred-swap' or
            not deferred or not deferred['issued'] or not deferred['pending']):
        raise ValueError('owned deferred submission required for native capture')
    ranges=[[pending['root_physical'],12],[pending['child_physical'],pending['child_words']*4]]
    capture=parse_capture(status,json.dumps(ranges),records,graph,'16384',pending)
    capture['ownership']=parse_ownership(owners,capture)
    root,child=[blob['bytes'] for blob in capture['blobs']]
    if struct.unpack('>III',root)!=(0xC0013F00,pending['child_physical'],pending['child_words']):
        raise ValueError('captured native root differs from owned submission')
    request=deferred['request'];offset=pending['command_offset_words']*4
    expected=[0xFFFF5357,0x41500001,64,1,0,3,request['command_physical'],deferred['image_physical'],
        deferred['issued_read_word'],1280,720,3768320,3686400]+[0]*51
    if child[offset:offset+256]!=struct.pack('>64I',*expected):
        raise ValueError('captured native envelope differs from immutable ticket')
    return capture


def parse_ownership(raw,capture):
    pending=capture.get('native_pending_submission') if capture else None
    deferred_request=pending.get('deferred_request') if pending else None
    if raw is None:
        if capture and (capture.get('stopped_swap_request') or deferred_request):raise ValueError('stopped request capture requires owner metadata')
        return None
    if capture is None or len(raw)>2048:raise ValueError('bounded ownership requires a physical capture')
    try:rows=json.loads(raw)
    except (ValueError,RecursionError) as error:raise ValueError('invalid ownership JSON') from error
    if type(rows) is not list or len(rows)!=(len(capture['requests']) if capture['status']==1 else 0):
        raise ValueError('ownership rows must match complete capture requests')
    result=[]
    for row,(address,size) in zip(rows,capture['requests']):
        if row is None:result.append(None);continue
        if (type(row) is not list or len(row)!=5 or
            any(type(v) is not int or not 0<=v<=0xFFFFFFFF for v in row)):
            raise ValueError('five unsigned ownership fields required')
        physical,virtual,count,protection,writable=row
        if (not count or (physical|virtual|count)&3 or physical+count>0x20000000 or virtual+count>0x100000000 or
            not physical<=address<address+size<=physical+count or writable!=int(bool(protection&4))):
            raise ValueError('ownership extent/protection differs from captured range')
        result.append(dict(physical_base=physical,virtual_base=virtual,byte_count=count,protection=protection,
            writable=bool(writable),scope='stopped-allocation-metadata-only',payload_read=False,
            pinned_for_future_gpu_use=False,guest_write_authorized=False))
    if capture.get('stopped_swap_request'):
        validate_post_swap_owners(result,capture['requests'],capture['stopped_swap_request'],True)
    if deferred_request:
        validate_post_swap_owners(result,capture['requests'],deferred_request,True)
    return result


def covering_stopped_owner(owners,address,count,writable=False):
    """Find one consistent stopped allocation, NOT a lease or write permission.

    The requested range may extend beyond the small payload snapshot. This only
    establishes containment in its allocation metadata, not snapshot contents.
    """
    if (type(address) is not int or type(count) is not int or type(writable) is not bool or
        address<0 or not 0<count<=0x1000000 or (address|count)&3 or address+count>0x20000000 or
        type(owners) is not list or len(owners)>8):
        raise ValueError('bounded stopped ownership lookup required')
    unique={}
    for owner in owners:
        if owner is None:continue
        if (type(owner) is not dict or owner.get('scope')!='stopped-allocation-metadata-only' or
            any(owner.get(k) is not False for k in ('payload_read','pinned_for_future_gpu_use','guest_write_authorized'))):
            raise ValueError('metadata-only ownership record required')
        values=[owner.get(k) for k in ('physical_base','virtual_base','byte_count','protection')]
        if any(type(v) is not int or not 0<=v<=0xFFFFFFFF for v in values):raise ValueError('unsigned ownership fields required')
        base,virtual,size,protection=values
        if (not size or (base|virtual|size)&3 or base+size>0x20000000 or virtual+size>0x100000000 or
            type(owner.get('writable')) is not bool or owner['writable']!=bool(protection&4)):
            raise ValueError('invalid allocation ownership extent')
        record=tuple(values)
        if base in unique and unique[base]!=record:raise ValueError('contradictory ownership records')
        if any(base<b+n and b<base+size for b,_,n,_ in unique.values() if b!=base):
            raise ValueError('overlapping distinct allocation owners')
        unique[base]=record
    for base,virtual,size,protection in unique.values():
        if base<=address and address+count<=base+size and (not writable or protection&4):
            return dict(physical_base=base,virtual_base=virtual,byte_count=size,protection=protection,
                requested_virtual_address=virtual+address-base,requested_byte_count=count,
                ownership_contained_at_stop=True,payload_contents_verified=False,
                pinned_for_future_gpu_use=False,guest_write_authorized=False)
    return None


def validate_ownership_ranges(ranges):
    if type(ranges) is not list or not 1<=len(ranges)<=8:raise ValueError('one to eight ownership queries required')
    for i,pair in enumerate(ranges):
        if type(pair) not in (list,tuple) or len(pair)!=2 or any(type(v) is not int for v in pair):
            raise ValueError('unsigned ownership query pair required')
        a,n=pair
        if a<0 or not 0<n<=0x1000000 or (a|n)&3 or a+n>0x20000000:
            raise ValueError('bounded canonical ownership query required')
        if any(a<b+m and b<a+n for b,m in ranges[:i]):raise ValueError('ownership queries overlap')


def parse_ownership_queries(status,requested,results,graph,post_swap_request=None):
    if status is None:
        if requested is not None or results is not None:raise ValueError('ownership queries require explicit status')
        return None
    if status not in ('0','1') or requested is None or len(requested)>512 or results is None:
        raise ValueError('invalid ownership query framing')
    try:ranges=json.loads(requested)
    except (ValueError,RecursionError) as error:raise ValueError('invalid ownership request JSON') from error
    validate_ownership_ranges(ranges)
    request_scope=post_swap_request_ranges(post_swap_request) if graph is None else None
    if status=='1':
        if request_scope:
            if any(not any(base<=a<a+n<=base+size for base,size in request_scope['owners']) for a,n in ranges):
                raise ValueError('stopped request ownership query outside command/image scope')
        elif graph is None or graph['failure']:raise ValueError('stopped graph required for ownership evidence')
    owners=parse_ownership(results,{'status':int(status),'requests':ranges})
    if request_scope and status=='1':validate_post_swap_owners(owners,ranges,post_swap_request)
    result=dict(status=int(status),requests=ranges,owners=owners,scope='explicit-stopped-ownership-metadata',
        payload_read=False,guest_memory_changed=False,pinned_for_future_gpu_use=False)
    if request_scope and status=='1':result['stopped_swap_request']=post_swap_request
    return result


def require_ownership_requested(query,ranges):
    if ranges:validate_ownership_ranges(ranges)
    if (query is None)!=(not ranges) or (query is not None and query['requests']!=ranges):
        raise ValueError('ownership evidence differs from explicitly requested ranges')


def save_capture(output_directory, capture):
    if capture is None: return None
    report = {'status':capture['status'], 'requests':capture['requests'], 'artifacts':[],'byte_budget':capture['byte_budget'],
        'scope':'explicit-stopped-cpu-physical-snapshot', 'payloads_in_report':False,
        'gpu_execution_time_contents_verified':False, 'guest_memory_changed':False,
        'resource_uploaded':False, 'executed':False}
    if capture.get('ownership') is not None:report['ownership']=capture['ownership']
    if capture.get('native_pending_submission') is not None:
        report['native_pending_submission']=capture['native_pending_submission']
    if capture.get('native_scaler_wrapper_plan') is not None:
        report['native_scaler_wrapper_plan']=capture['native_scaler_wrapper_plan']
    if capture.get('stopped_swap_request') is not None:
        report['stopped_swap_request']=capture['stopped_swap_request']
    if capture['status'] != 1: return report
    directory = Path(tempfile.mkdtemp(prefix='stopped-physical-',dir=output_directory))
    for index,b in enumerate(capture['blobs']):
        path = directory / f'range-{index}.bin'
        fd = os.open(path,os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os,'O_NOFOLLOW',0),0o600)
        with os.fdopen(fd,'wb') as stream: stream.write(b['bytes'])
        report['artifacts'].append({'path':str(path.relative_to(output_directory)),
            'physical_address':b['physical_address'], 'byte_count':b['byte_count'],
            'sha256':hashlib.sha256(b['bytes']).hexdigest()})
    return report
