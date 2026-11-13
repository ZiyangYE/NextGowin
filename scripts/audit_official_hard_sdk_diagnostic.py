"""Bounded diagnostic only: source proof with normalized SDPB and verbatim DSP.

Does not enable these models in production admission or freeze any baseline.
Preserves the SDK DSP's unspecified initialization and the original-pair SAT.
"""
import json
from pathlib import Path
import shutil
import sys

import audit_official_source_equivalence as prover
from gowin_netlist_format import LIBRARY,EXPECTED
from ownpr_image_storage import sha
from ownpr_sdpb_formal import normalized_packed_reference
from split_primitive_models import units
from workspace_budget import reserve


def diagnostic_library():
    library,records=prover.formal_library()
    if sha(LIBRARY)!=EXPECTED:raise ValueError('SDK hash changed')
    sdk=LIBRARY.read_text(encoding='utf-8');row=units(sdk)['MULTALU27X18']
    dsp=sdk[row['start']:row['end']];needle='wire grstn = GSR.GSRO;'
    if dsp.count(needle)!=1:raise ValueError('DSP global-reset binding changed')
    dsp=dsp.replace(needle,"wire grstn = 1'b1;")
    ram,changes=normalized_packed_reference()
    records['SDPB']={'adapter':True,'experimental':True,
        'scope':'SDK body; inactive GSR; contiguous-write syntax normalization only',
        'normalizations':changes}
    records['MULTALU27X18']={'adapter':True,'experimental':True,
        'scope':'SDK body; inactive GSR; no added initial state'}
    return library+'\n'+ram+'\n'+dsp,records


def main():
    # Keep the established provenance and initial-state admission logic intact.
    args=sys.argv[1:]
    if '--out' not in args:raise ValueError('Use the source prover arguments, including --out')
    out=Path(args[args.index('--out')+1]).resolve()
    reserve(Path(__file__).resolve().parents[1],300_000_000)
    prepared=diagnostic_library()
    prover.formal_library=lambda:prepared
    failure=None
    try:prover.main()
    except Exception as e:failure=repr(e)
    finally:
        if out.is_dir():
            shutil.copyfile(__file__,out/'diagnostic_runner.py.txt')
            report={'production_admitted':False,'fair_baseline_frozen':False,
                    'scope':__doc__,'runner_sha256':sha(Path(__file__)),'error':failure}
            manifest=out/'manifest.json'
            if manifest.exists():
                record=json.loads(manifest.read_text());report['underlying_prover_status']=record['status']
                if record['status']!='proof_rejected_or_incomplete':
                    record['underlying_prover_status']=record['status']
                    record['status']='experimental_hard_sdk_diagnostic_only'
                    manifest.write_text(json.dumps(record,indent=2)+'\n')
            (out/'diagnostic_contract.json').write_text(json.dumps(report,indent=2)+'\n')
    if failure:raise SystemExit(failure)


if __name__=='__main__':main()
