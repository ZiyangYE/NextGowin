"""Calibrated GW5AST-138 uncompressed footer UserCode observation.

No image is rewritten. Identity outside this metadata word is checked exactly;
frame integrity, source semantics and hardware acceptance remain separate.
"""
import re
from ownpr_image_storage import read_text,sha
from audit_native_encoding_probe import payload


def locate(words):
    if len(words)<6:raise ValueError('Missing native footer')
    pos=len(words)-5
    expected=[(64,0x6800000000000000),(64,None),(64,0xffffffffffffffff),(32,0x08000000),(64,0xffffffffffffffff),(16,0xffff)]
    for word,(length,value) in zip(words[-6:],expected):
        if len(word)!=length or set(word)-{'0','1'} or value is not None and int(word,2)!=value:raise ValueError('Uncalibrated footer layout')
    if int(words[pos],2)>>32!=0x0a000000:raise ValueError('Uncalibrated UserCode command')
    return pos,int(words[pos],2)&0xffffffff


def observe(path):
    words=payload(path);pos,value=locate(words);text=read_text(path)
    def field(name):
        rows=re.findall(r'^//'+name+r':\s*(\S+)\s*$',text,re.M)
        if len(rows)!=1:raise ValueError('Missing/duplicate native '+name)
        try:return int(rows[0],0)
        except ValueError:raise ValueError('Unrecognized native '+name)
    checksum=field('CheckSum');declared=field('UserCode')
    if not 0<=checksum<=0xffff or declared!=value:raise ValueError('Native UserCode header/word mismatch')
    return dict(usercode=value,checksum=checksum,word_index=pos,word_hex=f'{int(words[pos],2):016x}',
        file_sha256=sha(path),default_checksum_matches=value==checksum,full_bitstream_verified=False)


def check_policy(path,config):
    rows=re.findall(r'^\s*set\s+user_code\s*=\s*(\S+)\s*$',read_text(config),re.M)
    if len(rows)>1:raise ValueError('Duplicate UserCode policy')
    result=observe(path)
    if rows:
        if not re.fullmatch(r'0[xX][0-9a-fA-F]{1,8}',rows[0]):raise ValueError('Uncalibrated explicit UserCode value')
        expected=int(rows[0],16);policy='explicit_hex'
    else:expected=result['checksum'];policy='native_default_checksum'
    if result['usercode']!=expected:raise ValueError('Encoded UserCode differs from device configuration')
    return dict(result,policy=policy,expected_usercode=expected,device_config_sha256=sha(config))


def compare_words(reference,candidate):
    a,av=locate(reference);b,bv=locate(candidate)
    if len(reference)!=len(candidate) or a!=b:raise ValueError('Payload shape changed')
    different=[i for i,(x,y) in enumerate(zip(reference,candidate)) if x!=y]
    if any(i!=a for i in different):raise ValueError('Change outside calibrated UserCode word')
    return dict(full_payload_identical=not different,configuration_and_other_commands_identical=True,
        usercode_only_difference=bool(different),reference_usercode=av,candidate_usercode=bv,word_index=a,
        timing_equivalence_proved=False,full_bitstream_verified=False)
