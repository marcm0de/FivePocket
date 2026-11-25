"""Bounded opt-in private shader capture from a stopped diagnostic, never a renderer."""
import hashlib
import os
from pathlib import Path
import re
import tempfile


def parse_shader_capture(status, records, work):
    if status is None:
        if records: raise ValueError('shader blobs require explicit capture status')
        return None
    if status not in ('0','1','2') or len(records) > 8:
        raise ValueError('invalid shader capture bounds')
    if status != '1':
        if records: raise ValueError('unavailable capture cannot publish blobs')
        return {'status': int(status), 'blobs': []}
    if work is None or work['failure']:
        raise ValueError('shader capture requires successful stopped work inspection')
    if len(records) != len(work['shaders']):
        raise ValueError('shader capture is incomplete')
    blobs, total = [], 0
    for raw, shader in zip(records,work['shaders']):
        if len(raw) > 8300 or not re.fullmatch(r'(?:0|[1-9][0-9]{0,9}),(?:0|[1-9][0-9]{0,9}),[01],(?:0,(?:0|[1-9][0-9]{0,9}),)?[0-9a-f]+',raw):
            raise ValueError('invalid private shader record')
        parts = raw.split(',')
        node, offset, stage, encoded = *parts[:3], parts[-1]
        immediate, address = (1, 0) if len(parts) == 4 else (int(parts[3]), int(parts[4]))
        if ([int(node),int(offset),int(stage)] != shader[:3] or shader[4:] != [immediate,address]
                or len(encoded) != shader[3]*8 or address % 4 or address + shader[3]*4 > 0x20000000):
            raise ValueError('shader bytes do not match validated stopped-load metadata')
        code = bytes.fromhex(encoded); total += len(code)
        if not code or len(code) % 12 or total > 4096:
            raise ValueError('shader capture exceeds complete-instruction budget')
        blobs.append({'node':int(node),'offset':int(offset),'stage':int(stage),'bytes':code,
            'immediate':bool(immediate),'physical_address':address})
    return {'status':1,'blobs':blobs}


def save_shader_capture(output_directory, capture):
    if capture is None: return None
    status = capture['status']
    report = {'status':status, 'programs_in_report':False, 'artifacts':[],
        'scope':'stopped-cpu-memory-snapshot', 'gpu_execution_time_contents_verified':False,
        'executed':False, 'predicates_evaluated':False}
    if status != 1 or not capture['blobs']: return report
    # The orchestrator has already restricted output_directory to ignored
    # local-analysis. Create a fresh owned 0700 directory, never overwrite a
    # prior capture or follow a preexisting per-shader path/symlink.
    directory = Path(tempfile.mkdtemp(prefix='stopped-shaders-',dir=output_directory))
    for index, blob in enumerate(capture['blobs']):
        path = directory / f"shader-{index}-{'vs' if blob['stage'] == 0 else 'ps'}.bin"
        fd = os.open(path,os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os,'O_NOFOLLOW',0),0o600)
        with os.fdopen(fd,'wb') as stream:
            stream.write(blob['bytes'])
        report['artifacts'].append({'path':str(path.relative_to(output_directory)),
            'node':blob['node'],'offset':blob['offset'],'stage':blob['stage'],
            'immediate':blob['immediate'],'physical_address':blob['physical_address'],
            'instruction_words':len(blob['bytes'])//4,'sha256':hashlib.sha256(blob['bytes']).hexdigest()})
    return report
