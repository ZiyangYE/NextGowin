"""Output-register D/CE/async constraints and C2Q, calibrated from native data."""
from ownpr_image_storage import read_text as read_artifact_text
import json,re
from ownpr_device import ROOT,DLL_SHA
from ownpr_place import sha
from ownpr_cell_timing import NativeCellTiming
from extract_timing import ROW


class IoregTimingModel:
    def __init__(self):
        raw=NativeCellTiming().data;group=raw['groups'][raw['id_map'][2]];self.values={};self.preset_sha256=None;self.constant_sync_sha256=None
        # Hash-pinned dispatcher 0x1446fc0: normal external D (4/6), CE
        # (12/14), positive CLEAR recovery/removal (27/29), CLK->Q (24).
        for pin,ids in {'D':(6,4),'CE':(14,12),'CLEAR':(29,27),'Q':(24,24)}.items():
            for selector in range(3):
                for maximum in ((True,) if pin=='Q' else (True,False)):
                    self.values[pin,selector,maximum]=group['rows'][group['pin_ids'][ids[int(maximum)]]][4*selector:4*selector+4]
        folder=ROOT/'results/native_ioreg_attributes_004';meta=json.loads((folder/'manifest.json').read_text());file=folder/'ioreg_timing_queries.json'
        if meta['status']!='captured' or meta['dll_sha256']!=DLL_SHA or meta['vendor_placement_executed'] or meta['vendor_routing_executed'] or sha(file)!=meta['timing_sha256']:raise ValueError('Unverified isolated I/O-register timing capture')
        rows=json.loads(file.read_text())['registers'];seen=set()
        for row in rows:
            site=tuple(row['io_site'])
            if site in seen or row['site']!=[0,site[0],site[1],1]:raise ValueError('Duplicate/misplaced isolated I/O timing case')
            seen.add(site);self.check_row(row)
        if seen!={(110,135,0),(110,144,0),(110,144,1),(109,183,0),(103,183,1)}:raise ValueError('Incomplete I/O timing site coverage')
        self.sha256=meta['timing_sha256']
        ref=ROOT/'results/native_uart_ioreg_calibration_003';rm=json.loads((ref/'manifest.json').read_text());state=ref/'ioreg_state.json'
        if rm['dll_sha256']!=DLL_SHA or sha(state)!=rm['ioreg_state_sha256'] or not rm['vendor_placement_executed'] or not rm['vendor_routing_executed'] or rm['own_pnr_result']:raise ValueError('Unverified I/O timing report reference')
        names={r['instance']:next(p['pin'].split('.',1)[1] for p in r['pins'] if p['pin'].endswith(('.CLEAR','.PRESET'))) for r in json.loads(state.read_text())['registers_and_iologic'] if r['physical_header'][0]==0}
        if len(names)!=11:raise ValueError('Incomplete I/O reference register set')
        self.reference_report=self.report(read_artifact_text(ref/'probe.tr'),names)

    def ensure_preset_calibration(self):
        if self.preset_sha256:return
        folder=ROOT/'results/native_ioreg_preset_timing_003';meta=json.loads((folder/'manifest.json').read_text());file=folder/'ioreg_timing_queries.json'
        expected='32369c8b2a464c5e7ca8b00331a350a314a9782dc1eb4060241b7710f6df144f'
        if (meta['status']!='captured' or meta['dll_sha256']!=DLL_SHA or meta['timing_primitive']!='DFFPE' or meta['vendor_placement_executed'] or meta['vendor_routing_executed']
            or sha(file)!=expected or meta['timing_sha256']!=expected or sha(folder/'probe.vg')!=meta['source_sha256']):raise ValueError('Unverified DFFPE I/O calibration')
        from audit_ioreg_preset_timing import check
        check(json.loads(file.read_text()),self);self.preset_sha256=expected

    def check_row(self,row,control='CLEAR'):
        if not row['clock_positive']:raise ValueError('Uncalibrated I/O clock edge')
        if control not in ('CLEAR','PRESET','RESET'):raise ValueError('Uncalibrated I/O control')
        if control=='PRESET':self.ensure_preset_calibration()
        if control=='RESET':self.ensure_constant_sync_calibration()
        seen=set()
        for q in row['queries']:
            if q['pin'] not in ('D','CE',control,'Q'):raise ValueError('I/O timing capture has the wrong asynchronous control')
            key='CLEAR' if q['pin']=='PRESET' else q['pin'],q['selector'],q['maximum']
            if key in seen or key not in self.values or q['values']!=self.values[key]:raise ValueError('I/O-register dispatcher differs from independent raw library')
            seen.add(key)
        allowed={'D','CE','Q','CLEAR' if control=='PRESET' else control}
        if seen!={k for k in self.values if k[0] in allowed}:raise ValueError('Incomplete output-register timing vectors')

    def ensure_constant_sync_calibration(self):
        if self.constant_sync_sha256:return
        folder=ROOT/'results/native_ioreg_constant_controls_002';meta=json.loads((folder/'manifest.json').read_text());file=folder/'ioreg_timing_queries.json'
        if meta['status']!='captured' or meta['dll_sha256']!=DLL_SHA or meta['vendor_placement_executed'] or meta['vendor_routing_executed'] or sha(file)!=meta['timing_sha256'] or sha(folder/'probe.vg')!=meta['source_sha256']:raise ValueError('Unverified constant-control timing capture')
        raw=NativeCellTiming().data;group=raw['groups'][raw['id_map'][2]]
        # Native RESET pin type 0xe9 dispatches at 0x1447302 to 16/18
        # for positive-clock setup/hold. Confirmed by independent query rows.
        for selector in range(3):
            for maximum in (True,False):self.values['RESET',selector,maximum]=group['rows'][group['pin_ids'][16 if maximum else 18]][4*selector:4*selector+4]
        rows=[r for r in json.loads(file.read_text())['registers'] if r.get('primitive')=='DFFRE'];sites=set()
        expected={k for k in self.values if k[0] in ('D','CE','RESET','Q')}
        for row in rows:
            site=tuple(row['io_site']);seen=set()
            if site in sites or row['control_native_type']!=0xe9 or row['ce_mode']!='one' or row['control_mode']!='zero' or not row['clock_positive'] or row['site']!=[0,site[0],site[1],1]:raise ValueError('Uncalibrated constant-control case')
            sites.add(site)
            for q in row['queries']:
                key=q['pin'],q['selector'],q['maximum']
                if key in seen or key not in expected or q['values']!=self.values[key]:raise ValueError('Sync I/O timing differs from dispatcher table')
                seen.add(key)
            if seen!=expected:raise ValueError('Incomplete sync I/O queries')
        if sites!={(110,135,0),(110,144,0),(110,144,1),(109,183,0),(103,183,1)}:raise ValueError('Incomplete constant-control site coverage')
        encoding=ROOT/'results/native_ioreg_sync_constant_encoding_001';em=json.loads((encoding/'manifest.json').read_text());probe=encoding/'ioreg_encoding_probe.json';data=json.loads(probe.read_text())
        if em['status']!='captured' or em['dll_sha256']!=DLL_SHA or em['vendor_placement_executed'] or em['vendor_routing_executed'] or sha(probe)!=em['encoding_probe_sha256'] or data['cell_type']!='DFFRE' or not data['combined_constant_controls_checked']:raise ValueError('Missing constant sync encoding evidence')
        combo=[r for r in data['records'] if r['profile']==dict(ce='one',control='zero',clock='signal')]
        if len(combo)!=1 or not combo[0]['full_payload_restored'] or sha(encoding/combo[0]['variant'])!=combo[0]['variant_sha256']:raise ValueError('Constant sync encoding changed')
        self.constant_sync_sha256=dict(timing=meta['timing_sha256'],encoding=em['encoding_probe_sha256'])

    def check_capture(self,data,placed,mapping,controls=None):
        expected={mapping['instances'][n]:[0,r['site'][0],r['site'][1],1] for n,r in placed['ioreg_clusters'].items()};seen=set()
        for row in data['registers']:
            if row['instance'] in seen or row['site']!=expected.get(row['instance']):raise ValueError('I/O timing placement differs from own result')
            seen.add(row['instance']);self.check_row(row,(controls or {}).get(row['instance'],'CLEAR'))
        if seen!=set(expected):raise ValueError('Incomplete own I/O timing capture')
        return dict(registers=len(seen),query_vectors=21*len(seen),raw_library_matched=True,calibration_sha256=self.sha256,preset_calibration_sha256=self.preset_sha256)

    def report(self,text,names,static_controls=()):
        controls=dict(names) if isinstance(names,dict) else {n:'CLEAR' for n in names};names=set(names);kind=section=arrival=None;seen=set();checks=[]
        for line in text.splitlines():
            heading=re.match(r'^3\.(?:3|7)\.\d+ (Setup|Hold|Recovery|Removal) Analysis Report',line)
            if heading:kind=heading[1].lower();section=None
            if line.startswith('Data Arrival Path:'):section='arrival';arrival=None
            elif line.startswith('Data Required Path:'):section='required'
            elif line.startswith('Path Statistics:'):section=None
            row=ROW.match(line) if kind and section else None
            if not row:continue
            _,delay,typ,tr,_,_,endpoint=row.groups();instance,sep,pin=endpoint.rpartition('/')
            if section=='arrival':arrival=(endpoint,tr)
            maximum=kind in ('setup','recovery');selector=1 if maximum else 2
            if typ=='tC2Q' and instance in names:
                if pin!='Q' or tr not in ('RR','RF'):raise ValueError('Uncalibrated I/O clock-to-Q transition')
                query_pin='Q';values=self.values['Q',selector,True];predicted=values[(2 if maximum else 0)+int(tr[-1]=='F')]
            elif typ in ('tSu','tHld') and endpoint in names:
                instance=endpoint
                if not arrival or arrival[0].rpartition('/')[0]!=instance or arrival[1] not in ('RR','RF','FR','FF'):raise ValueError('I/O constraint lacks a data endpoint')
                query_pin=arrival[0].rpartition('/')[2]
                if query_pin not in ('D','CE',controls[instance]):raise ValueError('Unmodeled I/O-register constraint pin')
                model_pin='CLEAR' if query_pin=='PRESET' else query_pin
                values=self.values[model_pin,selector,maximum];predicted=values[(2 if maximum else 0)+int(arrival[1][-1]=='F')]*(-1 if maximum else 1)
            else:continue
            if abs(float(delay)-predicted)>.00051:raise ValueError('I/O register report differs from calibrated cell timing')
            seen.add((instance,kind,query_pin));checks.append(dict(instance=instance,analysis=kind,pin=query_pin,observed=float(delay),predicted=predicted))
        required={(n,k,p) for n in names for k,p in [('setup','Q'),('hold','Q'),('setup','D'),('hold','D')]}
        required.update((n,k,controls[n]) for n in names if n not in set(static_controls) for k in ('recovery','removal'))
        if not required<=seen:raise ValueError('Incomplete I/O-register report arc coverage')
        return dict(cell_or_constraint_rows=len(checks),matched=True,checks=checks)
