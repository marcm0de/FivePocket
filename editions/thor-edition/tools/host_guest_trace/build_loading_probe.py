"""Build one reviewed local probe with conservative exact-input reuse.

No device access. Build identity uses fresh compiler-discovered dependencies;
the full regression report separately binds the complete public source tree.
Private source, shader packs, external link/toolchain inputs must be declared.
Missing receipts rebuild, never bless an existing executable after the fact.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import time
import subprocess

from loading_test_evidence import (atomic_report, batch_lock, executable, local_path,
                                   run_command, runtime_snapshot, test_environment)
from loading_workflow import digest, parse_depfile


def build_tool_hashes():
    """Changing receipt/dependency verification invalidates old build evidence."""
    import loading_test_evidence
    import loading_workflow
    paths=(Path(__file__),Path(loading_test_evidence.__file__),Path(loading_workflow.__file__))
    return {str(p.resolve()):digest(p) for p in paths}


def spec_hash(spec):
    return hashlib.sha256(json.dumps(spec,sort_keys=True,separators=(',',':')).encode()).hexdigest()


def build_snapshot(root, extras):
    """Declared non-compiler inputs; compiler dependencies are hashed below.

    Do not use this for a test report: run_loading_test_batch.snapshot retains
    its whole-tree scope. Unrelated edits invalidate tests, not every binary.
    """
    return {str(p.relative_to(root)):digest(p) for p in sorted({root/'AGENTS.md',*extras})}


def compiler_dependencies(command, root):
    """Fresh preprocessing per translation unit, including system/optional headers.

    Link inputs have separate hashes; -M never links or runs the probe. Reject
    response/side-output options instead of silently scanning different flags.
    """
    sources, flags, linked = [], [], []
    skip = False
    for arg in command[1:]:
        if skip:
            skip = False
            continue
        if arg == '-o':
            skip = True
        elif arg.startswith('@') or arg in ('-MF','-MT','-MQ','-MJ','-MD','-MMD','-M','-MM') or arg.startswith('-save-temps'):
            raise ValueError('probe command needs explicit sources and no compiler side-output flags')
        elif not arg.startswith('-') and Path(arg).suffix in ('.c','.cc','.cpp','.cxx'):
            sources.append(arg)
        elif not arg.startswith('-') and Path(arg).suffix in ('.o','.a','.so','.dylib'):
            linked.append((root/arg).resolve())
        elif arg in ('-c','-static-libstdc++','-static-libgcc','-shared','-static','-pie','-rdynamic') or arg.startswith(('-l','-L','-Wl,','-fuse-ld=')):
            if arg in ('-l','-L'):
                skip = True
        else:
            flags.append(arg)
    if not sources:
        raise ValueError('probe command needs explicit C/C++ translation units')
    paths = set(linked)
    for source in sources:
        result = subprocess.run([command[0],*flags,'-M','-MT','probe',source],cwd=root,
            capture_output=True,timeout=15,env=test_environment())
        if result.returncode or len(result.stdout)>16*1024*1024:
            raise ValueError('read-only compiler dependency scan failed: '+result.stderr.decode(errors='replace')[-1000:])
        dependencies = parse_depfile(result.stdout.decode(),root)
        if str((root/source).resolve()) not in dependencies:
            raise ValueError('compiler scan omitted translation unit')
        paths.update(map(Path,dependencies))
    return {str(p):digest(p) for p in sorted(paths)}


def build_context(spec, root):
    if spec.get('schema') != 1:
        raise ValueError('probe build schema 1 required')
    command = spec.get('command')
    if not isinstance(command, list) or not command or any(not isinstance(v,str) or not v for v in command):
        raise ValueError('probe build requires explicit command arguments')
    compiler = executable(command[0], root)
    if 'clang' not in compiler.name and compiler.name not in ('c++','g++','cc','gcc'):
        raise ValueError('explicit C/C++ compiler command required')
    output = local_path(root, spec['output'], private=True)
    if command.count('-o') != 1 or command.index('-o') == len(command)-1 or local_path(root, command[command.index('-o')+1], private=True) != output:
        raise ValueError('compiler output must match the declared private probe')
    extras = [local_path(root, p) for p in spec.get('additional_inputs', [])]
    if not extras or any(not p.is_file() for p in extras):
        raise ValueError('declare resident private sources/toolchain inputs')
    # Fail early on the common omission: a private translation unit, archive or
    # response file explicitly supplied to the compiler but not in its closure.
    for arg in command[1:]:
        path = root / arg.removeprefix('@')
        if path.is_file() and path != output and path.resolve().is_relative_to(root/'local-analysis'):
            if path.resolve() not in extras:
                raise ValueError('undeclared private compiler input: ' + arg)
    if output in extras:
        raise ValueError('probe output cannot also be a build input')
    environment = runtime_snapshot(dict(steps=[dict(command=command)], tools=spec.get('tools', [])), root)
    return dict(source_sha256=build_snapshot(root, extras), runtime=environment,
                build_tool_sha256=build_tool_hashes(), build_spec_sha256=spec_hash(spec),
                compiler_dependencies_sha256=compiler_dependencies(command,root),
                toolchain_inputs_sha256={str(Path(p).resolve()):digest(Path(p).resolve()) for p in spec.get('toolchain_inputs', [])},
                additional_inputs=[str(p.relative_to(root)) for p in extras])


def verify_probe_build(receipt, output, root):
    report = json.loads(Path(receipt).read_text())
    if report.get('schema') != 2 or report.get('result') != 'passed':
        raise ValueError('passing schema-2 probe build receipt required; rebuild legacy receipt')
    spec = report['build']
    if local_path(root, spec['output'], private=True) != output or report['output_sha256'] != digest(output):
        raise ValueError('probe output differs from its build receipt')
    current = build_context(spec, root)
    if any(report.get(key) != value for key,value in current.items()):
        raise ValueError('probe build is stale: sources, toolchain or environment changed')
    log = local_path(root, report['log'], private=True)
    if report['log_sha256'] != digest(log):
        raise ValueError('probe build log changed')
    return report


def main(argv=None, root=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', required=True, type=Path)
    parser.add_argument('--receipt', required=True, type=Path)
    parser.add_argument('--reuse', type=Path, help='reuse only an identical passing build; otherwise compile')
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args(argv)
    root = Path(root).resolve() if root else Path(__file__).resolve().parents[2]
    manifest = local_path(root, args.manifest, private=True)
    receipt = local_path(root, args.receipt, private=True)
    spec = json.loads(manifest.read_text())
    context = build_context(spec, root)
    output = local_path(root, spec['output'], private=True)
    if args.check:
        print('PROBE_BUILD_PLAN_VALID; no compilation/device command executed')
        return 0
    log = receipt.with_suffix('.log')
    if receipt.exists() or log.exists():
        raise ValueError('use a new probe receipt/log name; preserve previous evidence')
    with batch_lock(root):
        report = dict(schema=2, result='running', build=spec, **context,
                      log=str(log.relative_to(root)), reused=False)
        atomic_report(receipt, report)
        started = time.monotonic()
        try:
            previous = None
            if args.reuse:
                try:
                    previous = verify_probe_build(local_path(root,args.reuse,private=True),output,root)
                except (OSError, ValueError):
                    pass
            if previous and previous['build'] == spec:
                # Evidence reuse does not invoke or credit a device test.
                with log.open('x') as stream:
                    stream.write('PROBE_BUILD_REUSED ' + str(args.reuse) + '\n')
                report['reused'] = True
            else:
                # Never overwrite an unrelated or last-good probe in place.
                if output.exists():
                    raise ValueError('changed/missing receipt: select a new probe output path')
                if run_command(spec['command'], log, root, 60) != 0:
                    raise ValueError('probe compilation failed or timed out')
            if build_context(spec, root) != context:
                raise ValueError('inputs changed during probe compilation')
            report.update(result='passed', output_sha256=digest(output))
        except (Exception, KeyboardInterrupt) as error:
            report.update(result='failed', error=str(error))
        finally:
            report['seconds'] = time.monotonic()-started
            if log.exists():
                report['log_sha256'] = digest(log)
            atomic_report(receipt, report)
        print('PROBE_BUILD', report['result'], 'reused='+str(report['reused']), flush=True)
        return 0 if report['result']=='passed' else 1


if __name__ == '__main__':
    sys.exit(main())
