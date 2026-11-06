"""Frame CRC codec for the calibrated GW5AST-138 native header profile.

Native constructor RVA 0x5aa171 supplies polynomial 0x8005. Byte reflection,
little-endian CRC, six trailing FF bytes and seeds are cross-checked against
native captures. The first seed is calibrated for one exact header digest;
arbitrary headers, compression, encryption and full image semantics are NOT
supported by this module.
"""
import argparse,gzip,hashlib,json
from pathlib import Path
import numpy as np
from capture_runtime_tables import EXPECTED

HEADER_SHA256='18209683e46c9ba4a9d92c0045e647c5342f138798d990ccac9effe7189f152e'
FRAME_COUNT=21872
FRAME_BYTES=198
DATA_BYTES=190


def crc_table():
    table=[]
    for value in range(256):
        for _ in range(8):value=(value>>1)^0xa001 if value&1 else value>>1
        table.append(value)
    return np.array(table,dtype=np.uint16)


TABLE=crc_table()


def checksums(data,header_sha256):
    if header_sha256!=HEADER_SHA256:raise ValueError('Uncalibrated native bitstream header')
    if data.shape!=(FRAME_COUNT,DATA_BYTES) or data.dtype!=np.uint8:raise ValueError('Unexpected native frame data dimensions')
    state=np.full(FRAME_COUNT,0x8f01,dtype=np.uint16)
    # First-frame state is a calibrated property of the exact fixed header.
    # Other frames follow a zero CRC residue and six FF padding bytes.
    state[0]=0x8373
    for column in range(DATA_BYTES):state=(state>>8)^TABLE[(state^data[:,column])&255]
    return state


def encode_frames(data,header_sha256):
    """Return frame bytes only. Does not emit a complete FPGA bitstream."""
    crc=checksums(data,header_sha256);frames=np.full((FRAME_COUNT,FRAME_BYTES),255,dtype=np.uint8)
    frames[:,:DATA_BYTES]=data;frames[:,190]=crc&255;frames[:,191]=crc>>8
    return frames


def read_frames(path):
    from ownpr_image_storage import resolve
    path=resolve(path);opener=gzip.open if path.suffix=='.gz' else open
    with opener(path,'rt') as stream:lines=stream.read().splitlines()
    payload=[line for line in lines if line and not line.startswith('//')]
    if any(set(line)-{'0','1'} for line in payload):raise ValueError('Unknown native ASCII payload')
    positions=[i for i,line in enumerate(payload) if len(line)==FRAME_BYTES*8]
    if len(positions)!=FRAME_COUNT or positions!=list(range(positions[0],positions[0]+FRAME_COUNT)):
        raise ValueError('Unrecognized native frame sequence')
    header=hashlib.sha256(''.join(payload[:positions[0]]).encode()).hexdigest()
    bits=np.frombuffer(''.join(payload[i] for i in positions).encode(),dtype=np.uint8).reshape(FRAME_COUNT,FRAME_BYTES*8)-48
    return np.packbits(bits,axis=1),header


def audit(path):
    from ownpr_image_storage import sha as artifact_sha
    frames,header=read_frames(path);expected=encode_frames(frames[:,:DATA_BYTES].copy(),header)
    crc_bad=np.flatnonzero(np.any(frames[:,190:192]!=expected[:,190:192],axis=1)).tolist()
    tail_bad=np.flatnonzero(np.any(frames[:,192:]!=255,axis=1)).tolist()
    return dict(path=str(path),file_sha256=artifact_sha(path),
        header_sha256=header,frames=FRAME_COUNT,crc_mismatch_frames=crc_bad,tail_mismatch_frames=tail_bad,
        status='frame_integrity_verified' if not crc_bad and not tail_bad else 'failed',
        dll_sha256=EXPECTED,full_bitstream_verified=False,
        scope='Calibrated header and frame integrity only; footer/checksum commands, configuration semantics and hardware remain unverified.')


if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('files',nargs='+',type=Path);ap.add_argument('--out',required=True,type=Path);a=ap.parse_args()
    result=[audit(p) for p in a.files];a.out.write_text(json.dumps(result,indent=2))
    print(json.dumps(dict(files=len(result),frames=sum(r['frames'] for r in result),failed=sum(r['status']=='failed' for r in result)),indent=2))
    if any(r['status']=='failed' for r in result):raise SystemExit(1)
