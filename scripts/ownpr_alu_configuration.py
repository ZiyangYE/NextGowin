"""Calibrated ALU mode-2 configuration fragments, never full-chain signoff."""
import hashlib,json
from pathlib import Path
from ownpr_device import ROOT,DLL_SHA
from ownpr_lut_configuration import LutInitChoices
from ownpr_cls_configuration import ClsChoices


class Mode2Configuration:
    def __init__(self,device):
        self.device=device;self.words={};self.sources={};self.luts=LutInitChoices(device);self.cls=ClsChoices(device)
        for suffix in ('004','006','007','008'):
            folder=ROOT/('results/native_alu_encoding_'+suffix);path=folder/'alu_encoding_probe.json';a=json.loads((folder/'alu_configuration_audit.json').read_text())
            digest=hashlib.sha256(path.read_bytes()).hexdigest()
            if (a['status']!='initial_mode2_profiles_verified' or a['capture_sha256']!=digest or a['dll_sha256']!=DLL_SHA
                    or a['source_fse_sha256']!=device.fse.sha256 or not a['full_payload_restored']):raise ValueError('Unverified arithmetic configuration calibration')
            for r in a['records']:
                key=tuple(r['profile'])
                if key in self.words and self.words[key]!=r['truth_word']:raise ValueError('Conflicting ALU truth calibration')
                self.words[key]=r['truth_word']
            self.sources[suffix]=digest

    def word(self,mode,profile):
        if mode!=2 or tuple(profile) not in self.words:raise ValueError('Uncalibrated ALU mode or constant-input profile')
        return self.words[tuple(profile)]

    @staticmethod
    def with_alu_modes(attributes,slots):
        if len(attributes)!=4 or any(len(a)!=19 for a in attributes):raise ValueError('Invalid combined CLS attributes')
        if len(set(slots))!=len(slots) or any(s not in range(6) for s in slots):raise ValueError('Invalid or duplicate ALU slot')
        result=[list(a) for a in attributes]
        for slot in slots:
            group=result[slot//2]
            if group[0] not in (0,9,10) or group[16+(slot&1)] not in (0,10):raise ValueError('Arithmetic mode conflicts with an unsupported resource mode')
            group[0]=10;group[16+(slot&1)]=10
        return result

    def requirements(self,row,column,slot,mode,profile,attributes):
        if self.device.grid[row][column]!=17 or slot not in range(6):raise ValueError('ALU configuration fragment not calibrated for this site')
        # Callers must provide the combined CLS attributes for all occupants.
        # Silently replacing FF or neighboring ALU control fields is forbidden.
        if len(attributes)!=19 or attributes[0]!=10 or attributes[16+(slot&1)]!=10:
            raise ValueError('Combined CLS attributes do not select this ALU')
        result=self.luts.requirements(row,column,slot,self.word(mode,profile))
        for bit,value in self.cls.requirements(row,column,slot//2,attributes).items():
            if bit in result and result[bit]!=value:raise ValueError('Arithmetic configuration conflicts with CLS fields')
            result[bit]=value
        return result
