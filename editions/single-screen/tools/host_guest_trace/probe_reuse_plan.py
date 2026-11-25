"""Read-only selection of an exact, current executable build for packaging.

This never reuses test results, upgrades receipts, builds or relocates binaries.
The caller must still run a fresh complete regression for the candidate library.
"""
import copy
import json
from pathlib import Path
from build_loading_probe import verify_probe_build
from loading_test_evidence import local_path


def without_output(spec):
    spec=copy.deepcopy(spec)
    command=spec.get('command',[])
    if command.count('-o')!=1 or command.index('-o')==len(command)-1:
        raise ValueError('one explicit compiler output required')
    spec['output']='<retained-output>'
    command[command.index('-o')+1]='<retained-output>'
    return spec


def select_verified_prior(requested, receipt, root):
    """Allow only an output-name difference; retain OLD paths and exact spec.

    Any dependency/tool/environment change or legacy/missing evidence requires
    a fresh build at a new output path. Never edit a receipt to claim validity.
    """
    root=Path(root).resolve()
    receipt=local_path(root,receipt,private=True)
    try:
        report=json.loads(receipt.read_text());prior=report['build']
        if without_output(prior)!=without_output(requested):return None
        output=local_path(root,prior['output'],private=True)
        verify_probe_build(receipt,output,root)
    except (OSError,ValueError,KeyError,TypeError):
        return None
    return dict(output=str(output.relative_to(root)),receipt=str(receipt.relative_to(root)))


def plan_probe_binding(requested, prior_receipt, root, new_output, new_receipt):
    """Return a retained binding or a new build spec; perform no writes/builds.

    Keep declared build inputs unchanged. Test manifests, maps and orchestration
    scripts are bound by the full regression report, not appended to every old
    compiler recipe merely because the package is being assembled again.
    """
    root=Path(root).resolve()
    retained=select_verified_prior(requested,prior_receipt,root)
    if retained:return dict(binding=retained,build=None,reused_build=True)
    output=local_path(root,new_output,private=True)
    receipt=local_path(root,new_receipt,private=True)
    if output==receipt or any(p.exists() for p in (output,receipt,receipt.with_suffix('.log'))):
        raise ValueError('fresh probe output/receipt/log paths required; preserve existing evidence')
    fresh=copy.deepcopy(requested);without_output(fresh) # Validate the command shape.
    fresh['output']=str(output.relative_to(root))
    fresh['command'][fresh['command'].index('-o')+1]=str(output)
    return dict(binding=dict(output=str(output.relative_to(root)),receipt=str(receipt.relative_to(root))),
                build=fresh,reused_build=False)
