"""Read a bounded stopped binary graph; structural analysis is NOT execution.

No ADB, writes, hydration, command replay, guard relaxation or gameplay claims.
Optional protocol matching binds this sidecar to the request's stopped graph.
"""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import re
import struct
from stopped_pm4_artifacts import decode_packets

MAX_WORDS=256*1024
CHUNK_BYTES=65536


def bounded(path, maximum):
    if path.is_symlink() or not path.is_file() or path.stat().st_size>maximum:
        raise ValueError('missing, linked or excessive capture artifact: '+str(path))
    with path.open('rb') as stream:
        data=stream.read(maximum+1)
    if len(data)>maximum:raise ValueError('capture artifact grew beyond limit')
    return data


def load_capture(directory, protocol=None):
    directory=Path(directory)
    if directory.is_symlink() or not directory.is_dir():raise ValueError('plain capture directory required')
    raw=bounded(directory/'manifest.json',32768);m=json.loads(raw)
    if (type(m.get('version')) is not int or m['version'] not in (1,2) or m.get('writers_stopped') is not True or
            any(m.get(k) is not False for k in ('executed','guest_memory_changed','ring_advanced'))):
        raise ValueError('stopped-only provenance required')
    for k in ('read_word','write_word','total_words','total_packets'):
        if type(m.get(k)) is not int or not 0<=m[k]<=0xFFFFFFFF:raise ValueError('invalid integer '+k)
    if m['total_words']>MAX_WORDS or m['total_packets']>65536:raise ValueError('aggregate limit exceeded')
    nodes=m.get('nodes');dispatch=m.get('dispatch')
    if not isinstance(nodes,list) or not 1<=len(nodes)<=65 or not isinstance(dispatch,dict):
        raise ValueError('bounded node list and dispatch record required')
    decoded=[];hashes={'manifest.json':hashlib.sha256(raw).hexdigest()};total=0
    if m['version']==2:
        dispatch_raw=bounded(directory/'dispatch.json',32768)
        if json.loads(dispatch_raw)!=dispatch:raise ValueError('dispatch record differs from completion manifest')
        hashes['dispatch.json']=hashlib.sha256(dispatch_raw).hexdigest()
    for i,n in enumerate(nodes):
        if (n.get('node')!=i or type(n.get('node')) is not int or n.get('path')!=f'node-{i}.bin' or
                type(n.get('word_count')) is not int or type(n.get('packet_count')) is not int or
                not 0<=n['word_count']<=MAX_WORDS-total or not 0<=n['packet_count']<=65536):
            raise ValueError('invalid node extent/identity')
        size=n['word_count']*4
        if m['version']==2:
            if type(n.get('chunk_bytes')) is not int or n['chunk_bytes']!=CHUNK_BYTES:
                raise ValueError('fixed per-file capture limit required')
            parts=max(1,(size+CHUNK_BYTES-1)//CHUNK_BYTES);payloads=[]
            for part in range(parts):
                name=n['path'] if parts==1 else f'node-{i}-part-{part}.bin'
                expected=min(CHUNK_BYTES,size-part*CHUNK_BYTES)
                chunk=bounded(directory/name,expected)
                if len(chunk)!=expected:raise ValueError('node chunk byte count mismatch')
                payloads.append(chunk);hashes[name]=hashlib.sha256(chunk).hexdigest()
            data=b''.join(payloads)
        else:
            data=bounded(directory/n['path'],size)
            hashes[n['path']]=hashlib.sha256(data).hexdigest()
        if len(data)!=size:raise ValueError('node byte count mismatch')
        words,packets=decode_packets(data,max_capture_words=MAX_WORDS)
        if len(packets)!=n['packet_count']:raise ValueError('node packet count mismatch')
        decoded.append((words,packets));total+=len(words)
    if total!=m['total_words'] or sum(len(p) for _,p in decoded)!=m['total_packets']:
        raise ValueError('aggregate metadata mismatch')
    children={}
    for i,n in enumerate(nodes[1:],1):
        for key in ('parent','parent_offset','physical_address','depth'):
            if type(n.get(key)) is not int or not 0<=n[key]<=0xFFFFFFFF:raise ValueError('invalid node relationship')
        parent,offset=n['parent'],n['parent_offset'];key=parent,offset
        if not 0<=parent<i or key in children or not 1<=n['depth']<=8 or n['depth']!=(nodes[parent].get('depth',0)+1):
            raise ValueError('invalid parent or depth')
        if n['physical_address']&3 or not n['word_count'] or n['physical_address']+n['word_count']*4>0x20000000:
            raise ValueError('invalid physical extent')
        words,packets=decoded[parent];p=next((p for p in packets if p[0]==offset),None)
        if (p is None or (p[1],p[2],p[3],p[6]) not in ((3,2,63,0),(3,2,55,0)) or
                words[offset+1:offset+3]!=(n['physical_address'],n['word_count'])):
            raise ValueError('indirect pointer differs from captured child')
        children[key]=i
    for i,(_,packets) in enumerate(decoded):
        for p in packets:
            if p[1]==3 and p[3] in (55,63) and (i,p[0]) not in children:
                raise ValueError('missing indirect child')
    if protocol is not None:
        text=bounded(Path(protocol),4*1024*1024).decode()
        fields={}
        for line in text.splitlines():
            k,sep,v=line.partition('=')
            if k in ('gpu_submission','gpu_indirect_graph','graphics_ring_read_word','scalar_value','stop_reason'):
                if not sep or k in fields:raise ValueError('duplicate or malformed protocol field')
                fields[k]=v
        sub=[int(x) for x in fields['gpu_submission'].split(',')];graph=json.loads(fields['gpu_indirect_graph'])
        if (fields['stop_reason']!='unsupported-scalar-access' or
                int(fields['graphics_ring_read_word'])!=m['read_word'] or int(fields['scalar_value'])!=m['write_word'] or
                sub!=[5,0,0,0,m['read_word'],m['write_word'],nodes[0]['word_count'],nodes[0]['packet_count']] or
                graph[:8]!=[0,0,0,0,0,0,m['total_words'],m['total_packets']] or len(graph[8])!=len(nodes)-1):
            raise ValueError('sidecar differs from stopped protocol')
        for i,(n,g) in enumerate(zip(nodes[1:],graph[8]),1):
            expected=[n['parent'],n['parent_offset'],n['physical_address'],n['depth'],n['word_count'],n['packet_count']]
            if len(g)!=7 or g[:6]!=expected or g[6]!=decoded[i][1][:len(g[6])]:
                raise ValueError('sidecar node differs from stopped protocol')
        hashes['protocol']=hashlib.sha256(text.encode()).hexdigest()
    return m,decoded,children,hashes


def ordered_packets(decoded,children,node=0):
    for p in decoded[node][1]:
        child=children.get((node,p[0]))
        if child is not None:yield from ordered_packets(decoded,children,child)
        else:yield node,p,decoded[node][0][p[0]+1:p[0]+1+p[2]]


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('directory',type=Path);p.add_argument('--protocol',type=Path)
    a=p.parse_args();root=Path(__file__).resolve().parents[2]/'local-analysis'
    if not a.directory.resolve().is_relative_to(root) or (a.protocol and not a.protocol.resolve().is_relative_to(root)):
        p.error('private ignored local-analysis paths required')
    m,d,c,h=load_capture(a.directory,a.protocol)
    counts=Counter();predicates=0
    for _,packet,_ in ordered_packets(d,c):
        counts[f'command-{packet[3]:02x}' if packet[1]==3 else f'type-{packet[1]}']+=1;predicates+=packet[6]
    print(json.dumps(dict(structurally_valid=True,protocol_matched=a.protocol is not None,
        total_words=m['total_words'],total_packets=m['total_packets'],nodes=len(d),dispatch=m['dispatch'],
        packet_counts=dict(counts),predicated_packets=predicates,sha256=h,
        executed=False,semantics_verified=False,gameplay_verified=False),indent=2))


if __name__=='__main__':main()
