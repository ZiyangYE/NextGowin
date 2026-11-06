"""Count local regular files without following junctions; reserve write space."""
import argparse,json,os
from pathlib import Path
HARD_LIMIT=40_000_000_000

def size(root):
    root=Path(root).resolve();total=0;stack=[root]
    while stack:
        for entry in os.scandir(stack.pop()):
            st=entry.stat(follow_symlinks=False)
            if getattr(st,'st_file_attributes',0)&0x400 or entry.is_symlink():continue
            if entry.is_dir(follow_symlinks=False):stack.append(entry.path)
            elif entry.is_file(follow_symlinks=False):total+=st.st_size
    return total

def reserve(root,additional_bytes):
    current=size(root)
    if additional_bytes<0 or current+additional_bytes>HARD_LIMIT:
        raise RuntimeError(f'Workspace budget exceeded: {current} + {additional_bytes} > {HARD_LIMIT} bytes')
    return dict(current_bytes=current,reserved_bytes=additional_bytes,hard_limit_bytes=HARD_LIMIT)

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--root',type=Path,default=Path(__file__).resolve().parents[1]);p.add_argument('--reserve',type=int,default=0);a=p.parse_args()
    print(json.dumps(reserve(a.root,a.reserve),indent=2))
