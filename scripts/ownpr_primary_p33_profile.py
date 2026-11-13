"""Hash-guarded L22/PCLKT1/central-P33 profile from reversible native encoding."""
from functools import lru_cache
from pathlib import Path
from ownpr_image_storage import load_json,sha
from ownpr_clock_images import image_path,image_sha
from capture_runtime_tables import EXPECTED

@lru_cache(None)
def calibration():
    root=Path(__file__).resolve().parents[1]/'results/native_parent_fuse_trial_015'
    hashes={}
    for folder,tree,report,changes,bits in [('primary_encoding_001','vendor_primary_clock_tree.json','primary_hypothesis_audit.json',163,678),('primary_prefix_encoding','primary_prefix.json','primary_prefix_audit.json',0,2)]:
        p=root/folder;m=load_json(p/'manifest.json');a=load_json(root/report);c=load_json(p/'encoding_audit.json')
        if m['status']!='captured' or m['dll_sha256']!=EXPECTED or m['vendor_pnr_executed'] or m['open_chip_database_used']:raise ValueError('Unverified primary clock calibration')
        if a['status']!='clock_candidate_bits_matched' or not a['full_payload_restored'] or a['actual_changes']!=changes or a['predicted_changes']!=changes or a['required_bits']!=bits or a['missing'] or a['unexpected'] or a['value_mismatches']:raise ValueError('Primary clock calibration mismatch')
        if a['clock_tree_sha256']!=sha(root/tree) or a['native_capture_sha256']!=sha(p/'encoding_audit.json'):raise ValueError('Primary clock evidence changed')
        for filename,key in [('encoding_baseline.fs','baseline_sha256'),('encoding_clock.fs','clock_sha256'),('encoding_restored.fs','restored_sha256')]:
            if image_sha(image_path(p,filename))!=c[key]:raise ValueError('Primary clock image changed')
        hashes[report]=sha(root/report)
    return hashes

def edge(model,source,destination):
    fixed=(((-2,183,213),(-2,183,216)),((-2,183,216),(-2,-1,132)),((-1,-1,19),(-1,-1,300)),((-1,-1,300),(-2,-1,248)))
    if (source,destination) not in fixed and (source,destination) not in (((-2,183,192),(-2,183,213)),((-2,-1,132),(-1,-1,19))):return None
    calibration()
    if (source,destination)==((-2,183,192),(-2,183,213)):
        if model.device.grid[81][181]!=276:raise ValueError('Uncalibrated primary source site')
        table=model.table(276,48)
        if (5,26) in table:raise ValueError('Expected omitted default input')
        masks=[set(v) for (src,dst),v in table.items() if dst==26]
        if not masks or any(m!=masks[0] for m in masks):raise ValueError('Ambiguous primary source mask')
        return {(81,181,y,x):0 for y,x in masks[0]},'primary_pclkt1_zero_select'
    if (source,destination) in fixed:
        b=model.graph.by_coordinate[destination]
        if (source,destination)!=((-2,183,216),(-2,-1,132)) and model.indegree[b]!=1:raise ValueError('Changed fixed primary clock fanin')
        return {},'fixed_primary_p33_copy'
    choice=model.global_choice(38,132,19)
    if choice is None or choice[:2]!=(54,88):raise ValueError('Uncalibrated central P33 selector')
    r,c,values=choice
    return {(r,c,y,x):v for (y,x),v in values.items()},'central_p33_table38'
