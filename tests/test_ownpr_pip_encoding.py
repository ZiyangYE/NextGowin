import sys,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from audit_native_pip_encoding import audit
from ownpr_device import Device
from ownpr_fuse_audit import SwitchChoices,audit as audit_routes


class NativePipEncodingTests(unittest.TestCase):
    def test_native_raw_writes_cover_own_route(self):
        r=audit(ROOT/'results/native_pip_encoding_001')
        self.assertEqual(r['status'],'verified_raw_pip_writes')
        self.assertEqual(r['connections'],22856)
        self.assertTrue(r['used_design_coverage_complete'])
        self.assertFalse(r['logical_node_mapping_verified'])

    def test_wrong_fuse_column_is_rejected(self):
        choices=SwitchChoices(Device());original=choices.bit
        def wrong(typ,index):
            row,col=original(typ,index);return row,col^1
        choices.bit=wrong
        self.assertEqual(audit(ROOT/'results/native_pip_encoding_001',choices)['status'],'failed')

    def test_implicit_zero_choice_constrains_mux_and_detects_alternative(self):
        device=Device();choices=SwitchChoices(device);typ=17
        rows=[r for r in device.implicit_connections()[typ]
              if r['source_name']=='Q0' and r['configuration_kind']=='implicit_zero']
        self.assertTrue(rows,'The native FF Q0 output must be routable')
        row=rows[0];src,dst=row['source_name'],row['destination_name']
        template=choices.template(typ);zero=template[src,dst]
        self.assertTrue(zero,'A zero-selection mux still constrains its selection bits')
        self.assertEqual(set(zero.values()),{0})
        alternatives=[s for s,d in template if d==dst and any(template[s,d].values())]
        self.assertTrue(alternatives)
        r,c=next((r,c) for r,line in enumerate(device.grid) for c,t in enumerate(line) if t==typ)
        routes={'nets':{'ff_output':{'pips':[[r,c,src,dst]]}}}
        self.assertEqual(audit_routes(routes,choices)['status'],'consistent')
        routes['nets']['incompatible_mux_selection']={'pips':[[r,c,alternatives[0],dst]]}
        self.assertEqual(audit_routes(routes,choices)['status'],'conflicting')

    def test_fixed_boundary_branch_has_no_mux_bits(self):
        device=Device();choices=SwitchChoices(device)
        # This boundary branch occurs in the retained full-I/O native clock path.
        self.assertEqual(choices.template(245)['EW10','W110'],{})
        rows=device.implicit_connections()
        self.assertEqual(set(rows),{t for line in device.grid for t in line})
        self.assertEqual(sum(len(v) for v in rows.values()),2997)

    def test_full_named_literal_and_implicit_encoding_calibration(self):
        r=audit(ROOT/'results/native_pip_encoding_010')
        self.assertEqual(r['status'],'verified_raw_pip_writes')
        self.assertEqual(r['connections'],624102)
        self.assertEqual(r['implicit_connections'],2997)
        self.assertEqual(r['mismatch_count'],0)
        self.assertFalse(r['full_bitstream_verified'])


if __name__=='__main__':unittest.main()
