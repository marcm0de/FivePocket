"""Run an explicit local test batch and bind its evidence to source + library.

No install, game launch, input injection, automatic retries or gameplay claim.
Manifests are reviewed local command lists, never downloaded instructions.
"""
import argparse
import json
from pathlib import Path
import subprocess
import time
from loading_workflow import digest
from loading_test_evidence import (atomic_report, batch_lock, device_check,
    local_path, run_command, runtime_snapshot, validate_output, validate_spec)
import shutil
from loading_test_contract import validate_coverage


def source_files(root):
    paths=[root/'AGENTS.md']
    for folder in ('android/app/src/main', 'android/tests', 'tools/host_guest_trace',
                   'tools/rpf7_inventory', 'tools/gta5_codegen', 'tools/vulkan_probe'):
        paths.extend(p for p in (root/folder).rglob('*') if p.is_file() and
                     (p.suffix in ('.py','.c','.cc','.cpp','.cxx','.h','.hh','.hpp','.inl','.inc','.def',
                                   '.cmake','.kt','.java','.glsl','.comp','.vert','.frag','.S') or p.name=='CMakeLists.txt'))
    return sorted(set(paths))


def snapshot(root, additional=()):
    return {str(p.relative_to(root)):digest(p) for p in sorted(set(source_files(root)+list(additional)))}


def verify_report(path, library, root, *, require_installable=False):
    root = root.resolve()
    report = json.loads(Path(path).read_text())
    if (report.get('schema') != 2 or report.get('result') != 'passed' or
            report.get('gameplay_verified') is not False or report.get('library_sha256') != digest(library)):
        raise ValueError('schema-2 passing validation for this library required')
    manifest = local_path(root, report['manifest'], private=True)
    if digest(manifest) != report['manifest_sha256']:
        raise ValueError('validation manifest changed')
    spec = json.loads(manifest.read_text())
    installable = require_installable or report.get('installation_authority', False)
    extras = validate_spec(spec, root, installable=installable) + [manifest]
    _, coverage = validate_coverage(spec, root, installable=installable)
    if require_installable and report.get('installation_authority') is not True:
        raise ValueError('batch must pass --for-install preflight before installation')
    if 'coverage' in report and report['coverage'] != coverage:
        raise ValueError('case coverage changed after validation')
    if report['source_sha256'] != snapshot(root, extras) or report['runtime'] != runtime_snapshot(spec, root):
        raise ValueError('sources, tools or environment changed after validation')
    records = report.get('steps', [])
    if len(records) != len(spec['steps']):
        raise ValueError('not every planned step completed')
    for planned, record in zip(spec['steps'], records):
        if record.get('plan') != planned or record.get('returncode') != 0 or record.get('result') != 'passed':
            raise ValueError('step failed, changed, reordered or not executed')
        output = local_path(root, record['output'], private=True)
        if digest(output) != record['output_sha256']:
            raise ValueError('test output missing or changed')
        validate_output(output, planned)
        if planned.get('kind') == 'device' and not record.get('device'):
            raise ValueError('device test missing fresh device evidence')
        if installable and planned.get('kind') == 'device':
            observed = record['device']
            if ('reused_from' in record or any(not observed.get(k) for k in ('fingerprint','abi','boot_id')) or
                    observed.get('inputs_sha256') != {i['remote']:digest(root/i['local']) for i in planned['device_inputs']}):
                raise ValueError('installation needs complete fresh device identities and input evidence')
    return report


def resume_records(path, current, root):
    if path is None:
        return {}
    old = json.loads(path.read_text())
    for key in ('schema', 'manifest', 'manifest_sha256', 'library_sha256', 'source_sha256', 'runtime', 'installation_authority'):
        if old.get(key) != current.get(key):
            raise ValueError('resume refused: changed ' + key + '; run a fresh batch')
    reusable = {}
    for record in old.get('steps', []):
        plan = record['plan']
        if (record.get('result') != 'passed' or record.get('returncode') != 0 or
                not plan.get('reusable') or plan.get('kind', 'host') != 'host' or plan['tier'] == 'capture'):
            continue
        output = local_path(root, record['output'], private=True)
        if digest(output) != record['output_sha256']:
            raise ValueError('resume output changed')
        validate_output(output, plan)
        reusable[plan['name']] = record
    return reusable


def main(argv=None, root=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for flag in ('manifest', 'library', 'output'):
        parser.add_argument('--' + flag, type=Path, required=True)
    parser.add_argument('--resume', type=Path)
    parser.add_argument('--check', action='store_true', help='validate plan/inputs without tests or ADB')
    parser.add_argument('--for-install', action='store_true', help='require declared actual-case coverage and current-source probe builds')
    args = parser.parse_args(argv)
    root = Path(root).resolve() if root else Path(__file__).resolve().parents[2]
    paths = [args.manifest, args.library, args.output] + ([args.resume] if args.resume else [])
    for path in paths:
        absolute = local_path(root, path, private=True)
        subprocess.run(['git', 'check-ignore', '--quiet', '--', str(absolute)], cwd=root, check=True)
    manifest, library, output = [local_path(root, p, private=True) for p in paths[:3]]
    spec = json.loads(manifest.read_text())
    extras = validate_spec(spec, root, installable=args.for_install) + [manifest]
    _, coverage = validate_coverage(spec, root, installable=args.for_install)
    if args.check:
        digest(library)
        snapshot(root, extras)
        runtime_snapshot(spec, root)
        print('PLAN_VALID', len(spec['steps']), 'steps; no tests/device commands executed', flush=True)
        return 0
    if output.exists() or any(output.with_name(output.stem + f'-{i:02}.log').exists() for i in range(len(spec['steps']))):
        raise ValueError('use a new report name; preserve existing evidence')
    with batch_lock(root):
        report = dict(schema=2, result='running', scope=spec['scope'], gameplay_verified=False,
            coverage=coverage, installation_authority=args.for_install,
            manifest=str(manifest.relative_to(root)), manifest_sha256=digest(manifest),
            library_sha256=digest(library), source_sha256=snapshot(root, extras),
            runtime=runtime_snapshot(spec, root), steps=[])
        resume = resume_records(local_path(root, args.resume, private=True) if args.resume else None, report, root)
        with output.open('x') as stream:
            json.dump(report, stream)
        started = time.monotonic()
        try:
            for index, step in enumerate(spec['steps']):
                log = output.with_name(output.stem + f'-{index:02}.log')
                record = dict(plan=step, result='running', returncode=None, output=str(log.relative_to(root)))
                report['steps'].append(record)
                atomic_report(output, report)
                before = time.monotonic()
                try:
                    previous = resume.get(step['name'])
                    if previous and previous['plan'] == step:
                        with log.open('xb') as target, (root / previous['output']).open('rb') as source:
                            shutil.copyfileobj(source, target)
                        record.update(returncode=0, reused_from=str(args.resume), prior_seconds=previous['seconds'])
                    else:
                        command, timeout = step['command'], step.get('timeout_seconds', 60)
                        if step.get('kind') == 'device':
                            record['device'] = device_check(spec, step, root)
                            # Killing adb doesn't stop a remote child. Bound the remote
                            # probe inside the host deadline; never kill the game.
                            command = command[:4] + ['timeout', '-s', 'KILL', str(timeout - 5)] + command[4:]
                        record['executed_command'] = command
                        record['returncode'] = run_command(command, log, root, timeout)
                    if record['returncode'] != 0:
                        raise ValueError('test process failed or timed out')
                    validate_output(log, step)
                    record['result'] = 'passed'
                except BaseException as error:
                    record['result'] = 'interrupted' if isinstance(error, KeyboardInterrupt) else 'failed'
                    record['error'] = str(error)
                    raise
                finally:
                    record['seconds'] = time.monotonic() - before
                    if log.exists():
                        record['output_sha256'] = digest(log)
                    atomic_report(output, report)
                    print(json.dumps({k: record.get(k) for k in ('result', 'returncode', 'seconds', 'reused_from')}
                                     | {'name': step['name']}), flush=True)
            report['result'] = 'passed' if (report['source_sha256'] == snapshot(root, extras)
                and report['library_sha256'] == digest(library) and report['runtime'] == runtime_snapshot(spec, root)) else 'inputs-changed'
        except (Exception, KeyboardInterrupt) as error:
            report['result'] = 'interrupted' if isinstance(error, KeyboardInterrupt) else 'failed'
            report['error'] = str(error)
        finally:
            report['seconds'] = time.monotonic() - started
            report['executed_steps'] = sum('reused_from' not in s and 'executed_command' in s for s in report['steps'])
            report['reused_steps'] = sum('reused_from' in s for s in report['steps'])
            atomic_report(output, report)
        if report['result'] == 'passed':
            try:
                verify_report(output, library, root)
            except (OSError, ValueError) as error:
                report.update(result='verification-failed', error=str(error))
                atomic_report(output, report)
        print('BATCH_REPORT', output, report['result'], flush=True)
        return 0 if report['result'] == 'passed' else 1


if __name__=='__main__':raise SystemExit(main())
