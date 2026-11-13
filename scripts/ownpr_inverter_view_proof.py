"""Admit only a recorded restored control inverter becoming a native pin NOT.

Post-output native pin flags can retain the already-absorbed physical NOT
while the exported logical view contains that same explicit inverter. This
is a representation transition, not permission to discard arbitrary NOTs.
The caller must verify unchanged primitive modes and physical configuration.
"""
CONTROL_PINS={'CE','RESET','SET','CLEAR','PRESET'}


def compare_views(before,after):
    errors=[];proofs=[]
    if before['modes']!=after['modes']:errors.append('Changed LUT function or register mode')
    old,new=before['sinks'],after['sinks']
    if old.keys()!=new.keys():errors.append('Changed non-inverter sink membership')
    recorded=set(before['merged_inverter_addresses'])&set(after['merged_inverter_addresses'])
    for pin in old.keys()&new.keys():
        a,b=old[pin],new[pin]
        if (a['driver'],a['inverted'])==(b['driver'],b['inverted']):continue
        ids=a.get('inverter_chain_objects',[]);label=a.get('pin_label','').rsplit('.',1)[-1]
        eligible=(type(a['driver']) is int and a['driver']>0 and a['driver']==b['driver'] and a.get('instance_class')==b.get('instance_class')=='.?AVReg@NETLIST@@'
            and label in CONTROL_PINS and a.get('pin_label')==b.get('pin_label')
            and a.get('native_inversion_annotation') is True and b.get('native_inversion_annotation') is True
            and len(ids)==1 and ids[0] in recorded and a.get('inverter_chain_physical')==[False]
            and a.get('inverter_input_annotations')==[False]
            and not b.get('inverter_chain_objects') and b['inverted'] is True)
        if eligible:
            # Restored logical view: NOT(driver). Hardware-facing view:
            # inverted control pin(driver). The retained absorption annotation
            # is not counted twice in the former view.
            truth_before=[not bit for bit in (False,True)]
            truth_after=[bit^b['native_inversion_annotation'] for bit in (False,True)]
            if truth_before!=truth_after:raise AssertionError('Invalid NOT identity')
            proofs.append(dict(pin=pin,pin_label=label,driver=a['driver'],native_merged_inverter=ids[0],
                identity='explicit_NOT(x) == native_inverted_control_pin(x)',truth_table=[int(x) for x in truth_before]))
        else:errors.append(dict(pin=pin,before=a,after=b,reason='Unproved representation transition'))
    return dict(status='recorded_control_inverter_views_equivalent' if not errors else 'normalization_rejected',
        errors=errors,control_inverter_proofs=proofs,
        scope='Single unplaced inverter recorded as native-merged on a register control pin, unchanged root and absorption annotation; NOT truth identity only. Full RTL, physical and timing acceptance remain separate.')
