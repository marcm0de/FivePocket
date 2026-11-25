"""Declared case provenance and current probe-build receipts, not a game oracle.

The reviewed manifest defines the expected identities; these checks cannot
authenticate a capture or prove an expected result independently. They prevent
accidentally substituting synthetic/other-variant evidence or stale binaries.
"""
import json
import re
from pathlib import Path

from loading_workflow import digest

# Bounded evidence inventory, not renderer admission or a test timeout. Retain
# earlier captured cases alongside new ones instead of dropping old coverage.
# Raised 128 -> 192 for the PostFX bloom chain's per-band captured cases.
MAX_COVERAGE_CASES = 192

def input_identities(values, root):
    from loading_test_evidence import local_path
    if not isinstance(values, dict) or not values:
        raise ValueError('case requires named input identities')
    paths = []
    for name, item in values.items():
        if not isinstance(name, str) or not name or not isinstance(item, dict):
            raise ValueError('invalid named case input')
        path = local_path(root, item['path'], private=True)
        if not re.fullmatch('[0-9a-f]{64}', item.get('sha256', '')) or digest(path) != item['sha256']:
            raise ValueError('case input identity changed: ' + name)
        paths.append(path)
    return paths


def validate_coverage(spec, root, *, installable=False):
    coverage = spec.get('coverage')
    if coverage is None:
        if installable:
            raise ValueError('installation requires an explicit case coverage contract')
        return [], dict(purpose='unspecified', complete=False)
    purpose = coverage.get('purpose')
    if purpose not in ('component', 'integration', 'diagnostic') or not coverage.get('rationale', '').strip():
        raise ValueError('explicit coverage purpose and rationale required')
    cases = coverage.get('cases')
    if not isinstance(cases, list) or not cases or len(cases) > MAX_COVERAGE_CASES:
        raise ValueError('bounded nonempty coverage cases required')
    expected, paths, covered = {}, [], set()
    for case in cases:
        name = case.get('id')
        if not isinstance(name, str) or not name or name in expected:
            raise ValueError('unique coverage case ids required')
        if not isinstance(case.get('check'), str) or not case['check'].strip():
            raise ValueError('case requires a named connected check')
        expected[name] = case
        if case.get('inputs'):
            paths.extend(input_identities(case['inputs'], root))
        elif not case.get('deferred_reason', '').strip():
            raise ValueError('missing capture identities require explicit deferred reason')
    for step in spec['steps']:
        seen = set()
        for claim in step.get('case_evidence', []):
            name = claim.get('id')
            if name not in expected or name in seen:
                raise ValueError('unknown or duplicate step case')
            seen.add(name)
            kind = claim.get('kind')
            if kind not in ('captured', 'synthetic'):
                raise ValueError('case evidence must declare captured or synthetic inputs')
            markers = claim.get('expect')
            if (not isinstance(markers,list) or not markers or
                    any(not isinstance(m,str) or not m or m not in step.get('expect',[]) for m in markers)):
                raise ValueError('each case needs explicit output markers enforced by its step')
            paths.extend(input_identities(claim['inputs'], root))
            # The named end-to-end check must occur in ONE step. Three unrelated
            # primitive tests cannot collectively stand in for a connected test.
            if claim.get('check') != expected[name]['check']:
                raise ValueError('case evidence must run the required connected check')
            if kind == 'captured':
                actual = {k:v['sha256'] for k,v in claim['inputs'].items()}
                wanted = {k:v['sha256'] for k,v in expected[name].get('inputs', {}).items()}
                if actual != wanted:
                    raise ValueError('captured case differs from expected game variant: ' + name)
                if step['tier'] != 'capture' or step.get('reusable'):
                    raise ValueError('captured cases require a fresh capture-tier step')
                covered.add(name)
    missing = sorted(set(expected) - covered)
    for name in missing:
        if not expected[name].get('deferred_reason', '').strip():
            raise ValueError('untested actual case requires deferred reason: ' + name)
    if purpose == 'integration' and missing:
        raise ValueError('synthetic or missing cases cannot establish actual sequence coverage')
    if installable and purpose == 'component':
        raise ValueError('component-only results cannot authorize a game installation')
    return paths, dict(purpose=purpose, complete=not missing, covered=sorted(covered), deferred=missing)


def validate_probe_builds(spec, root, *, required=False):
    """Bind each private executable to a build made from the current sources."""
    from loading_test_evidence import local_path
    from build_loading_probe import verify_probe_build
    bindings, paths = {}, []
    for item in spec.get('probe_builds', []):
        output = local_path(root, item['output'], private=True)
        receipt = local_path(root, item['receipt'], private=True)
        if output in bindings:
            raise ValueError('duplicate probe build binding')
        report = verify_probe_build(receipt, output, root)
        paths.extend([output, receipt, *[root / p for p in report['additional_inputs']]])
        bindings[output] = receipt
    if required:
        for step in spec['steps']:
            command = step['command']
            if step.get('kind') == 'device':
                remote = command[4]
                output = next(i['local'] for i in step['device_inputs'] if i['remote'] == remote)
                path = local_path(root, output, private=True)
            else:
                path = (root / command[0]).absolute()
                if not path.is_relative_to(root / 'local-analysis'):
                    continue
            if path not in bindings:
                raise ValueError('current-source build receipt required for probe: ' + str(path))
    return paths
