import copy,json,re,sys
from pathlib import Path
import pytest
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from remap_ownpr_logic import boundary
from ownpr_mux_macros import validate
from ownpr_device import Device
from ownpr_place import constraints


def read(p):return json.loads(Path(p).read_text())


def test_arithmetic_boundary_choices_keep_state_and_hard_resources():
    d=read(ROOT/'results/coopt_tmds_initial_001/input.json');before=copy.deepcopy(d)
    all_cells,_,net,a=boundary(d,'all');comparators,_,other,c=boundary(d,'comparators')
    assert d==before and a['soft_alu_count']==92 and c['soft_alu_count']==40
    for n in all_cells|comparators:assert d['modules']['top']['cells'][n]['type'] not in ('DFFRE','IBUF','OBUF','GSR')
    for folder in ('tmds_global_remap_all_002','tmds_global_remap_comparators_001','tmds_global_remap_wide_001'):
        r=read(ROOT/'results'/folder/'manifest.json');assert all(x['cec']['equivalent'] for x in r['proof'])


def test_initial_wide_packer_covers_all_muxes_without_macro_sharing():
    p=ROOT/'results/tmds_global_wide_place_001';d=read(p/'packed.json');placed=read(p/'placement.json');result=validate(d,placed,Device())
    assert result['capacity_verified'] and len(placed['mux_clusters'])==153
    assert len(result['macros'])==71
    assert all(not placed['clusters'][n]['ff'] for macro in result['macros'] for n,r in placed['clusters'].items() if (r['row'],r['column'])==(macro['row'],macro['column']))


def test_added_evaluation_pins_preserve_existing_constraints():
    p=ROOT/'results/vexriscv_evaluation_pins_001';r=read(p/'manifest.json');assert r['existing_pins_preserved']==282 and r['full_port_coverage']==284
    d=read(ROOT/'results/coopt_vexriscv_mapping_003/05_netlist_retry/final.json')['modules']['VexRiscv'];new=(p/'fixed.cst').read_text();old=(ROOT/'results/tangprimer138_suite_001/vexriscv/fixed.cst').read_text()
    assert new.startswith(old.rstrip())
    check=constraints(d,new,(ROOT/'results/tangprimer138_suite_001/vexriscv/clock200.sdc').read_text())
    assert check['pins']['dBusWishbone_ERR']=='A5' and check['pins']['iBusWishbone_ERR']=='C17'
    assert all('CFG' not in x['package_record'] for x in r['added'])
