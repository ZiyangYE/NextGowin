"""Additional native-calibrated A-block output-register configuration types."""
from functools import lru_cache
from pathlib import Path
from ownpr_device import ROOT
from ownpr_image_storage import load_json,sha

EVIDENCE={245:('site_245_audit.json','db5eca0217acdd69e3561f50c7f165727d5a33b5a921d42401801e7c90a6852f'),63:('site_63_audit.json','aa88bfd4afc92ed9fb83cce8e6f63d5cbaaf6d1b8ad95483d00b08d5449692f1')}


@lru_cache(None)
def certificate(typ):
    if typ not in EVIDENCE:raise ValueError('No additional output I/O calibration')
    name,digest=EVIDENCE[typ];path=ROOT/'results/endpoint_class_cooptimization_trial_019'/name
    if sha(path)!=digest:raise ValueError('Additional output I/O audit changed')
    row=load_json(path)
    if row['status']!='extra_output_ioreg_static_sync_site_verified' or row['tile_type']!=typ or row['encoded_modes']!=8 or row['timing_vectors']!=21:raise ValueError('Incomplete additional I/O calibration')
    for raw,h in row['source_sha256'].items():
        file=Path(raw)
        if not file.is_relative_to(ROOT):
            parts=file.parts;index=next((i for i,p in enumerate(parts) if p.lower()=='results'),None)
            if index is None:raise ValueError('Unsupported relocated I/O evidence path')
            file=ROOT.joinpath(*parts[index:])
        if sha(file)!=h:raise ValueError('Additional I/O calibration dependency changed')
    return digest


def admit(typ,site,attributes):
    from ownpr_ioreg_configuration import output_attributes
    if site[2]!=0 or attributes!=output_attributes('DFFRE','one','zero'):raise ValueError('Additional I/O types admit only A-block static positive DFFRE')
    return certificate(typ)
