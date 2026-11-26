#!/usr/bin/env python3
"""The title update's shaders, made from the player's own files (next update; builder-gui 2026-11-24).

A game with its title update runs the update's own replacements of nine common/shaders/fxl_final_final/*.fxc files
(update.rpf), whose programs the 1.0 pack does not hold, and its boot draw 2 uses the update's im.fxc small pair. Two
builder stages make what the runtime needs, from the player's files only:

    build_tu_shaders.py --repo REPO extract --update-rpf FILE --generated TREE --key-address ADDR --xmem TOOL
                        --params JSON --base-pack PACK --output DIR
        The listed .fxc files out of update.rpf (its table key recovered in memory from this run's code generation, as
        the shader-extract stage does for the discs), and a translation inventory (build_shader_pack.py
        validate_inventory's format) of every container whose runtime key is not in the base pack. The production
        translate stage (build_shader_pack.py translate) then writes the update's supplement pack.

    build_tu_shaders.py --repo REPO boot --params JSON --boot-config JSON --boot-shaders DIR --v10-inventory DIR
                        --tu-extract DIR --tu-image FILE --glslc GLSLC --spirv-validator SPIRV_VAL --output DIR
        The update's boot directory (14 files): the small pair translated from the update's im.fxc (checked against the
        live identities in the params), depth/vertex located in the update's decoded image, and the r8 pair and the four
        authored compute modules taken from this run's boot-shaders stage. A control translation of 1.0's small pair from
        this run's disc inventory must reproduce the boot-config modules first. The set is named
        boot-tu<n>-<first 12 hex of SHA-256 over 'name:sha256\\n' of the 14 sorted names>, the title-update lane's rule
        (make_tu27_request.py), and written to report.json.

Ported from the title-update lane's make_tu27_shader_inventory.py and build_tu27_boot_modules.py (left as they are). The
constants (file list, offsets, live identities) are in the params file the profile pins. The key is never written, hashed
or printed. Every output is game-derived and stays in the private work folder.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import struct
import sys

sys.dont_write_bytecode = True
BOOT_ROLES = ('depth/vertex', 'r8/vertex', 'r8/fragment', 'small/vertex', 'small/fragment', 'store', 'copy', 'load', 'depth/store')
BOOT_FILES = sorted([f'{r}/guest-code.bin' for r in BOOT_ROLES[:5]] + [f'{r}/dataflow.spv' for r in BOOT_ROLES])
FNV_PRIME = 1099511628211


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def write_json(path: Path, obj) -> None:
    with path.open('x', encoding='utf-8', newline='\n') as stream:
        json.dump(obj, stream, indent=2, sort_keys=True)
        stream.write('\n')


def load_params(path: Path) -> dict:
    p = json.loads(path.read_text(encoding='utf-8'))
    fxc = p.get('fxc')
    if p.get('schema') != 1 or not isinstance(fxc, list) or not fxc or len(set(fxc)) != len(fxc) or len(fxc) > 64:
        raise ValueError('title-update shader params refused (schema 1, a list of distinct .fxc paths)')
    for name in fxc:
        if not isinstance(name, str) or not name.endswith('.fxc') or name.startswith('/') or '..' in name.split('/'):
            raise ValueError(f'title-update shader path refused: {name!r}')
    boot = p.get('boot', {})
    if boot:
        if boot.get('source') not in fxc or set(boot.get('roles', {})) != {'small/vertex', 'small/fragment'}:
            raise ValueError('title-update boot params refused (source among the .fxc files; roles small/vertex and small/fragment)')
        for role, r in boot['roles'].items():
            if r.get('stage') != ('vertex' if role == 'small/vertex' else 'pixel') or not isinstance(r.get('offset'), int) \
                    or len(str(r.get('identity', ''))) != 16:
                raise ValueError(f'title-update boot role refused: {role}')
        if not isinstance(boot.get('identity_basis'), int) or not isinstance(boot.get('set_prefix'), str) \
                or not boot['set_prefix'].startswith('boot-tu'):
            raise ValueError('title-update boot identity basis or set prefix refused')
    return p


def renderer_identity(code: bytes, basis: int) -> int:
    """the renderer's draw identity (renderer_boundary_trace.cpp identity(): FNV-1a 64 over the bound bytes, from the
    renderer's own basis, which the params name; it is not the standard FNV offset)"""
    h = basis
    for x in code:
        h ^= x
        h = (h * FNV_PRIME) & 0xffffffffffffffff
    return h


def set_name(prefix: str, files: dict) -> str:
    """the title-update lane's name for a boot set (make_tu27_request.boot_module_records): its prefix and the first 12 hex
    of SHA-256 over 'name:sha256\\n' for the 14 files in sorted order"""
    if sorted(files) != BOOT_FILES:
        raise ValueError('boot set file names refused')
    return f'{prefix}-' + sha(''.join(f'{n}:{files[n]}\n' for n in BOOT_FILES).encode())[:12]


# ----------------------------------------------------------------------------------------------------------------- extract

def extract(args) -> int:
    from build_shader_pack import View, read_pack, record_name, validate_inventory
    from fxc_xenos_inspect import inspect_fxc_bytes, FxcLimits
    from generic_shader_pack import parse_container, canonical_microcode, fnv1a64
    from rpf7_catalog import parse_catalog_entries
    from rpf7_extract import decode_binary_payload, decode_xmem_with_tool
    from rpf7_static_key_recover import recover_constant_stores, _MemoryKeyProvider
    from rpf7_toc import parse_toc_header, _decrypt_tables
    import rpf7_crypto

    params = load_params(args.params)
    base = set(read_pack(args.base_pack.read_bytes()))
    # Never serialise the key or store records, hash them, or open a key file (as build_shader_pack.extract).
    secret, stores = recover_constant_stores(args.generated, target_address=args.key_address)
    provider = _MemoryKeyProvider(secret)
    del stores
    with args.update_rpf.open('rb') as stream:
        def read_at(offset, size):
            stream.seek(offset)
            return stream.read(size)
        view = View(args.update_rpf.stat().st_size, read_at)
        header = parse_toc_header(view.read(0, 16), view.size)
        eb = view.read(16, header.entries_size)
        nb = view.read(16 + header.entries_size, header.names_size)
        eb, nb, decrypted = _decrypt_tables(eb, nb, header=header, archive=args.update_rpf, key_provider=provider,
                                            decryptor=rpf7_crypto.decrypt_aes256_ecb_prefix)
        entries = parse_catalog_entries(eb, nb, header=header, decrypted=decrypted)
        by_path = {}
        for e in entries:
            if e.kind != 'directory':
                by_path.setdefault(e.path, []).append(e)
        effects = {}
        for name in params['fxc']:
            found = by_path.get(name, [])
            if len(found) != 1 or found[0].kind != 'binary':
                raise ValueError(f'update.rpf has no single binary entry {name} ({len(found)} found)')
            e = found[0]
            blob = view.read(e.payload_offset, e.stored_size)
            effects[name] = decode_binary_payload(
                blob, encrypted=bool(e.encrypted), compressed=bool(e.compressed), logical_size=e.logical_size,
                key=secret if e.encrypted else None,
                xmem_decoder=lambda data, expected, maximum: decode_xmem_with_tool(data, expected, maximum, executable=args.xmem))
    del secret, provider
    out = args.output
    out.mkdir(parents=True, exist_ok=False)
    (out / 'fxc').mkdir()
    (out / 'containers').mkdir()
    rows, skipped, fxc_records = {}, 0, []
    for name in params['fxc']:
        data = effects[name]
        path = out / 'fxc' / name.replace('/', '_')
        path.write_bytes(data)
        fxc_records.append(dict(path=name, file=record_name(path, out), bytes=len(data), sha256=sha(data)))
        inspection = inspect_fxc_bytes(data, limits=FxcLimits(max_file_size=64 << 20))
        if inspection.rejected_magic_candidates or not inspection.containers:
            raise ValueError(f'{name}: incomplete container staging refused')
        for c in inspection.containers:
            blob = data[c.offset:c.offset + c.size]
            raw, _, _, _ = parse_container(blob, c.stage)
            canon = canonical_microcode(raw)
            key = '%016x' % fnv1a64(canon)
            if key in base:
                skipped += 1
                continue
            h = sha(blob)
            origin = dict(origin='update.rpf:' + name, offset=c.offset)
            if h in rows:
                rows[h]['origins'].append(origin)
                continue
            file = out / 'containers' / f'{c.stage}-{h[:16]}.xenos'
            file.write_bytes(blob)
            rows[h] = dict(file=record_name(file, out), bytes=len(blob), sha256=h, stage=c.stage, key=key,
                           canonical_sha256=sha(canon), origins=[origin])
    if not rows:
        raise ValueError('every title-update container is already in the base pack: nothing to translate')
    write_json(out / 'inventory.json', dict(
        schema=1, source='disc', key_files_used=False, status='complete', unresolved=[],
        note='title-update shader replacements (update.rpf): every container whose runtime key is not in the base pack',
        base_pack_sha256=sha(args.base_pack.read_bytes()), base_pack_skipped=skipped, toc_names=len(entries),
        fxc=fxc_records, containers=sorted(rows.values(), key=lambda r: (r['stage'], r['key'], r['sha256']))))
    _, accepted = validate_inventory(out)
    print('TU_SHADERS_EXTRACT PASS fxc', len(fxc_records), 'toc_names', len(entries), 'containers', len(rows),
          'vertex', sum(r['stage'] == 'vertex' for r in rows.values()), 'pixel', sum(r['stage'] == 'pixel' for r in rows.values()),
          'skipped_in_base', skipped, 'accepted', len(accepted))
    return 0


# -------------------------------------------------------------------------------------------------------------------- boot

def boot(args) -> int:
    import fxc_xenos_inspect as fx
    import generic_shader_pack as g
    import build_boot_shaders as bb
    from build_shader_pack import validate_inventory
    from generate_vertex_dataflow_probe import compile_compute

    params = load_params(args.params)
    bp = params.get('boot') or {}
    if not bp:
        raise ValueError('the params name no title-update boot set')
    config = json.loads(args.boot_config.read_text())
    cfg = {m['role']: m for m in config['modules']}
    authored = config['authored']
    shipped = json.loads((args.boot_shaders / 'report.json').read_text())
    if shipped.get('status') != 'complete':
        raise ValueError("this run's boot-shaders stage did not complete")

    def container(data: bytes, offset: int, stage: str, what: str) -> bytes:
        ins = fx.inspect_fxc_bytes(data, limits=fx.FxcLimits(max_file_size=64 << 20))
        found = [c for c in ins.containers if c.offset == offset]
        if len(found) != 1 or found[0].stage != stage:
            raise ValueError(f'{what}@{offset}: container refused')
        c = found[0]
        return g.parse_container(data[c.offset:c.offset + c.size], stage)[0]

    # 1.0's im.fxc: the disc effect of this run's inventory whose path is the boot source (fxl_final_final)
    inventory, _ = validate_inventory(args.v10_inventory)
    v10_files = {e['sha256']: e for e in inventory['effects'] if e['origin'].split('!/')[-1] == bp['v10_source']}
    if len(v10_files) != 1:
        raise ValueError(f"the disc inventory has no single 1.0 {bp['v10_source']} ({len(v10_files)} found)")
    v10_effect = next(iter(v10_files.values()))
    v10_im = (args.v10_inventory / v10_effect['file']).read_bytes()
    if sha(v10_im) != v10_effect['sha256']:
        raise ValueError('1.0 effect bytes differ from the inventory')
    tu_record = [f for f in json.loads((args.tu_extract / 'inventory.json').read_text())['fxc'] if f['path'] == bp['source']]
    if len(tu_record) != 1:
        raise ValueError('the title-update extract has no boot source file')
    tu_im = (args.tu_extract / tu_record[0]['file']).read_bytes()
    if sha(tu_im) != tu_record[0]['sha256']:
        raise ValueError('title-update boot source differs from its extract record')

    report = dict(schema=1, roles={}, inputs=dict(v10_im=sha(v10_im), tu_im=sha(tu_im), tu_image=sha(args.tu_image.read_bytes()),
                                                   boot_config=sha(args.boot_config.read_bytes()), params=sha(args.params.read_bytes())))
    sources = {}
    for role, r in sorted(bp['roles'].items()):
        item = cfg[role]
        v10 = container(v10_im, r['offset'], r['stage'], '1.0 ' + bp['v10_source'])
        tu = container(tu_im, r['offset'], r['stage'], 'update ' + bp['source'])
        if sha(g.canonical_microcode(v10)) != item['canonical_sha256']:
            raise ValueError(role + ': the 1.0 im.fxc is not the boot source')
        if item.get('bindings'):
            if [b['address'] for b in item['bindings']] != bb.vertex_slots(tu):
                raise ValueError(role + ': the update\'s fetch slots differ')
            v10 = bb.bind_vertex(v10, item['bindings'])
            tu = bb.bind_vertex(tu, item['bindings'])
        if sha(v10) != item['guest_sha256']:
            raise ValueError(role + ': 1.0 bound bytes differ from the boot-config guest code')
        if '%016x' % renderer_identity(tu, bp['identity_basis']) != r['identity']:
            raise ValueError(role + ': the update\'s bytes do not carry the live identity')
        sources[role] = (v10, tu, item)
        report['roles'][role] = dict(stage=r['stage'], offset=r['offset'], bytes=len(tu), tu_guest_sha256=sha(tu), v10_guest_sha256=sha(v10),
                                     tu_identity=r['identity'])
    # depth/vertex: the boot-shaders stage's guest code (1.0 decoded image), located once in the update's image
    dv = (args.boot_shaders / 'depth/vertex/guest-code.bin').read_bytes()
    if sha(dv) != cfg['depth/vertex']['guest_sha256']:
        raise ValueError("this run's depth/vertex guest code differs from boot-config")
    image = args.tu_image.read_bytes()
    hits, at = [], image.find(dv)
    while at >= 0 and len(hits) < 2:
        hits.append(at)
        at = image.find(dv, at + 1)
    if len(hits) != 1:
        raise ValueError('depth/vertex is not uniquely present in the update\'s image')
    report['roles']['depth/vertex'] = dict(source='tu-image', offset=hits[0], bytes=len(dv), guest_sha256=sha(dv))
    if args.check:
        print('TU_SHADERS_BOOT_CHECK PASS', json.dumps({r: report['roles'][r]['tu_identity'] for r in sorted(bp['roles'])}))
        return 0
    out = args.output
    out.mkdir(parents=True, exist_ok=False)
    os.chmod(out, 0o700)
    work = out / '_translations'
    for role, (v10, tu, item) in sorted(sources.items()):
        for tag, raw in (('v10', v10), ('tu', tu)):
            folder = work / tag / role
            folder.mkdir(parents=True)
            src, _ = bb.shader_source(role, raw, item['settings'])
            path = folder / ('dataflow.vert' if item['stage'] == 'vertex' else 'dataflow.frag')
            path.write_text(src, encoding='utf-8', newline='\n')
            blob = compile_compute(path, folder, args.glslc, args.spirv_validator)
            report['roles'][role][tag + '_module_sha256'] = sha(blob)
        rr = report['roles'][role]
        if rr['v10_module_sha256'] != item['module_sha256']:
            raise ValueError(role + ': the control translation does not reproduce the boot-config module')
        (out / role).mkdir(parents=True)
        (out / role / 'guest-code.bin').write_bytes(tu)
        (out / role / 'dataflow.spv').write_bytes((work / 'tu' / role / 'dataflow.spv').read_bytes())
        rr['tu_module_equals_v10'] = rr['tu_module_sha256'] == item['module_sha256']
    for role in ('depth/vertex', 'r8/vertex', 'r8/fragment'):
        (out / role).mkdir(parents=True)
        for name, want in (('guest-code.bin', cfg[role]['guest_sha256']), ('dataflow.spv', cfg[role]['module_sha256'])):
            b = (args.boot_shaders / role / name).read_bytes()
            if sha(b) != want:
                raise ValueError(f"{role}/{name}: this run's boot-shaders file differs from boot-config")
            (out / role / name).write_bytes(b)
        report['roles'].setdefault(role, {})['from_boot_shaders'] = True
    for role, want in sorted(authored.items()):
        (out / role).mkdir(parents=True, exist_ok=True)
        b = (args.boot_shaders / role / 'dataflow.spv').read_bytes()
        if sha(b) != want:
            raise ValueError(f"{role}: this run's authored module differs from boot-config")
        (out / role / 'dataflow.spv').write_bytes(b)
        report['roles'][role] = dict(from_boot_shaders=True, module_sha256=want)
    files = {}
    for name in BOOT_FILES:
        b = (out / name).read_bytes()
        files[name] = dict(bytes=len(b), sha256=sha(b))
    present = sorted(p.relative_to(out).as_posix() for p in out.rglob('*') if p.is_file() and not p.is_relative_to(work))
    if present != BOOT_FILES:
        raise ValueError(f'boot directory file set refused: {present}')
    report['files'] = files
    report['set'] = set_name(bp['set_prefix'], {n: m['sha256'] for n, m in files.items()})
    write_json(out / 'report.json', report)
    print('TU_SHADERS_BOOT PASS set', report['set'], 'files', len(files), json.dumps(
        {r: report['roles'][r]['tu_module_equals_v10'] for r in sorted(bp['roles'])}))
    return 0


def main(argv=None) -> int:
    os.umask(0o077)
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--repo', type=Path, required=True)
    sub = p.add_subparsers(dest='command', required=True)
    ep = sub.add_parser('extract')
    for name in ('update-rpf', 'generated', 'xmem', 'params', 'base-pack', 'output'):
        ep.add_argument('--' + name, type=Path, required=True)
    ep.add_argument('--key-address', type=lambda s: int(s, 0), required=True)
    bp = sub.add_parser('boot')
    for name in ('params', 'boot-config', 'boot-shaders', 'v10-inventory', 'tu-extract', 'tu-image', 'glslc', 'spirv-validator', 'output'):
        bp.add_argument('--' + name, type=Path, required=True)
    bp.add_argument('--check', action='store_true')
    args = p.parse_args(argv)
    for name, value in vars(args).items():
        if isinstance(value, Path):
            setattr(args, name, value.resolve())
    for folder in ('tools/own_disc', 'tools/iter', 'tools/rpf7_inventory', 'tools/vulkan_probe'):
        sys.path.insert(0, str(args.repo / folder))
    from disc_builder import check_private_path
    check_private_path(args.output, 'title-update shader output')
    return extract(args) if args.command == 'extract' else boot(args)


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (ValueError, OSError) as e:
        print('TU_SHADERS_REFUSED', e, file=sys.stderr)
        raise SystemExit(2)
