"""Archive current sources and reconstruct only hash-exact earlier snapshots."""
import hashlib,json,sys,shutil
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from ownpr_image_storage import load_json,sha
from workspace_budget import reserve


def main():
    reserve(ROOT,4000000);r=ROOT/'results/heterogeneous_choice_joint_trial_008';out=r/'closeout_sources';out.mkdir(exist_ok=False)
    names=['ownpr_heterogeneous_timing.py','ownpr_hold_aware_pins.py','gw5a_arch_mapper.py','remap_ownpr_logic.py','ownpr_pack_wide_ff.py','ownpr_pack_shared_ff.py','pack_joint_register_boundaries.py','ownpr_ff_configuration.py','ownpr_carry_outputs.py']
    for n in names:shutil.copyfile(ROOT/'scripts'/n,out/n)
    shutil.copyfile(ROOT/'experiments/run_heterogeneous_mapping.py',out/'run_heterogeneous_mapping.py')
    meta=load_json(r/'tmds_bridge_pin_retry_006/manifest.json');text=(ROOT/'scripts/ownpr_heterogeneous_timing.py').read_text()
    text=text.replace('from ownpr_timing_pins import pins\n','')
    before="""        for p,b in pins(c,'output'):
            if b in drivers:raise ValueError('Multiple heterogeneous drivers')
            drivers[b]=n+'/'+p
            if c['type'] in ('GND','VCC'):constants.add(b)
        for p,b in pins(c,'input'):loads[b].append(n+'/'+p)
"""
    after="""        for p,bits in c['connections'].items():
            for b in bits:
                if c['port_directions'][p]=='output':
                    if b in drivers:raise ValueError('Multiple heterogeneous drivers')
                    drivers[b]=n+'/'+p
                    if c['type'] in ('GND','VCC'):constants.add(b)
                else:loads[b].append(n+'/'+p)
"""
    text=text.replace(before,after)
    begin=text.index('    def clock_token(');end=text.index('    for n,c in a.items():',begin);text=text[:begin]+text[end:]
    text=text.replace("if [clock_token(bit,drivers,cells) for bit in x]!=[clock_token(bit,old_drivers,old) for bit in y]:raise ValueError('Changed clock-driver identity')","if [drivers.get(bit) for bit in x]!=[old_drivers.get(bit) for bit in y] or any(drivers.get(bit) is None for bit in x):raise ValueError('Changed clock-driver identity')")
    text=text.replace("""                    for p,bit in pins(c,'input'):
                        point=n+'/'+p
                        if point in needed:wire(point,bit)
""","""                    for p,bits in c['connections'].items():
                        point=n+'/'+p
                        if point in needed and c['port_directions'][p]=='input':
                            if len(bits)!=1:raise ValueError('Vector hard input requires scalar native pin mapping')
                            wire(point,bits[0])
""")
    expected=meta['source_sha256'][str((ROOT/'scripts/ownpr_heterogeneous_timing.py').resolve())];matches=[b for b in (text.encode(),text.replace('\n','\r\n').encode()) if hashlib.sha256(b).hexdigest()==expected]
    row=dict(expected_sha256=expected,exact_snapshot_recovered=bool(matches),policy='No approximate snapshot is promoted. Current final sources are always separately archived.')
    if matches:
        target=out/'candidate006_ownpr_heterogeneous_timing.py';target.write_bytes(matches[0]);row.update(path=str(target),verified_sha256=sha(target))
    result=dict(status='sources_archived',candidate006_mapping_engine=row,current_sources_sha256={str(x.resolve()):sha(x) for x in out.iterdir()},old_failed_prototype_source_hashes_retained=True,all_earlier_prototype_versions_claimed_reproducible=False)
    (r/'source_archive_audit_013.json').write_text(json.dumps(result,indent=2));print(row)


if __name__=='__main__':main()
