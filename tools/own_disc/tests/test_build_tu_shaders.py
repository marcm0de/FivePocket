"""build_tu_shaders.py (the title update's shaders): synthetic archives only, no game or key file fixtures.

The boot subcommand is checked on the owner's files (its control translation needs the real boot sources); see the
builder-gui ledger entry of 2026-11-24 for that run.

    python3 -m unittest tools/own_disc/tests/test_build_tu_shaders.py
"""
import argparse
import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import test_build_shader_pack as sp  # noqa: E402  (its rpf(), container(), variant() and pack() archive builders)
import build_tu_shaders as tu  # noqa: E402


def key_stores(folder: Path, address: int) -> Path:
    """generated code whose immediate stores write 32 bytes at address (what the extract recovers the table key from)"""
    folder.mkdir()
    text = 'PPC_FUNC_IMPL(__imp__sub_10000) {\n'
    for i in range(32):
        text += f'// lis r4,{address >> 16}\n// li r3,{i}\n// stb r3,{(address & 0xffff) + i}(r4)\n'
    (folder / 'ppc_recomp.0.cpp').write_text(text + '}\n')
    return folder


def params(path: Path, fxc, **extra) -> Path:
    path.write_text(json.dumps(dict(schema=1, fxc=fxc, **extra)))
    return path


class Extract(unittest.TestCase):
    def run_extract(self, root: Path, listed, in_base):
        fxc = b'rgxe' + (7).to_bytes(4, 'little') + b'x' + sp.container()
        (root / 'update.rpf').write_bytes(sp.rpf('a.fxc', fxc))
        key = f"{sp.fnv1a64(sp.canonical_microcode(sp.parse_container(sp.container(), 'pixel')[0])):016x}"
        (root / 'base.bin').write_bytes(sp.pack(key=int(key, 16) if in_base else 1))
        args = argparse.Namespace(update_rpf=root / 'update.rpf', generated=key_stores(root / 'generated', 0x10000), key_address=0x10000,
                                  xmem=Path('/unused'), params=params(root / 'params.json', listed), base_pack=root / 'base.bin',
                                  output=root / 'out')
        with contextlib.redirect_stdout(io.StringIO()):
            return tu.extract(args), args, key

    def test_the_listed_file_becomes_an_inventory_the_translate_stage_accepts(self):
        with tempfile.TemporaryDirectory(dir=sp.SCRATCH) as temp:
            code, args, key = self.run_extract(Path(temp), ['a.fxc'], in_base=False)
            self.assertEqual(code, 0)
            inv, rows = sp.m.validate_inventory(args.output)
            self.assertEqual([(r['key'], r['origins']) for r in rows], [(key, [{'origin': 'update.rpf:a.fxc', 'offset': 9}])])
            self.assertEqual((args.output / rows[0]['file']).read_bytes(), sp.container())
            self.assertEqual([(f['path'], f['bytes']) for f in inv['fxc']], [('a.fxc', 89)])
            self.assertEqual((inv['toc_names'], inv['base_pack_skipped']), (2, 0))       # the fixture's TOC: root + a.fxc

    def test_a_container_already_in_the_base_pack_is_left_out(self):
        with tempfile.TemporaryDirectory(dir=sp.SCRATCH) as temp:
            with self.assertRaisesRegex(ValueError, 'already in the base pack'):
                self.run_extract(Path(temp), ['a.fxc'], in_base=True)

    def test_a_listed_file_the_archive_lacks_is_refused(self):
        with tempfile.TemporaryDirectory(dir=sp.SCRATCH) as temp:
            with self.assertRaisesRegex(ValueError, 'no single binary entry b.fxc'):
                self.run_extract(Path(temp), ['a.fxc', 'b.fxc'], in_base=False)
            self.assertFalse((Path(temp) / 'out').exists())       # refused before anything is written


class Params(unittest.TestCase):
    ROLES = {'small/vertex': {'offset': 1, 'stage': 'vertex', 'identity': '0' * 16},
             'small/fragment': {'offset': 2, 'stage': 'pixel', 'identity': '1' * 16}}
    BOOT = dict(source='x/im.fxc', v10_source='im.fxc', identity_basis=5, set_prefix='boot-tu27', roles=ROLES)

    def test_params_of_another_shape_are_refused(self):
        cases = {
            'schema': dict(schema=2, fxc=['a.fxc']),
            'no files': dict(schema=1, fxc=[]),
            'repeated file': dict(schema=1, fxc=['a.fxc', 'a.fxc']),
            'not an effect': dict(schema=1, fxc=['a.rpf']),
            'absolute path': dict(schema=1, fxc=['/a.fxc']),
            'parent path': dict(schema=1, fxc=['x/../a.fxc']),
            'boot source not extracted': dict(schema=1, fxc=['a.fxc'], boot=self.BOOT),
            'boot role missing': dict(schema=1, fxc=['x/im.fxc'], boot=dict(self.BOOT, roles={'small/vertex': self.ROLES['small/vertex']})),
            'boot stage swapped': dict(schema=1, fxc=['x/im.fxc'], boot=dict(self.BOOT, roles=dict(
                self.ROLES, **{'small/fragment': dict(self.ROLES['small/fragment'], stage='vertex')}))),
            'set prefix': dict(schema=1, fxc=['x/im.fxc'], boot=dict(self.BOOT, set_prefix='modules')),
        }
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'p.json'
            path.write_text(json.dumps(dict(schema=1, fxc=['x/im.fxc'], boot=self.BOOT)))
            self.assertEqual(tu.load_params(path)['boot']['set_prefix'], 'boot-tu27')
            for name, body in cases.items():
                with self.subTest(name):
                    path.write_text(json.dumps(body))
                    with self.assertRaises(ValueError):
                        tu.load_params(path)


class SetName(unittest.TestCase):
    def test_the_boot_set_is_named_by_the_title_update_lanes_rule(self):
        # make_tu27_request.boot_module_records: SHA-256 over 'name:sha256\n' of the 14 names in sorted order, first 12 hex.
        # The value below was computed with the lane's own expression (copied from make_tu27_request.py into a one-off
        # script, not this module) from these synthetic hashes; a change of order, separator or length breaks it.
        files = {n: f'{i:064x}' for i, n in enumerate(tu.BOOT_FILES)}
        self.assertEqual(tu.set_name('boot-tu27', files), 'boot-tu27-5a36bd926343')
        with self.assertRaises(ValueError):
            tu.set_name('boot-tu27', dict(files, **{'store/guest-code.bin': '0' * 64}))


if __name__ == '__main__':
    unittest.main()
