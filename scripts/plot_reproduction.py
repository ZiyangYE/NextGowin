"""Standalone QoR figure; include only fully proven paired benchmarks."""
import argparse
import json
from pathlib import Path
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('manifest',type=Path)
    ap.add_argument('--out',type=Path,required=True)
    args=ap.parse_args()
    data=json.loads(args.manifest.read_text())
    methods=['acd','acd_remap']
    valid=[r for r in data['runs'] if all(r['configs'].get(m,{}).get('valid') for m in ['baseline']+methods)]
    excluded=[r['benchmark'] for r in data['runs'] if r not in valid]
    if not valid:
        raise ValueError('No proven pairs to plot')
    fig,axes=plt.subplots(2,1,figsize=(15,8),sharex=True)
    x=np.arange(len(valid));width=.36
    colors=['#377eb8','#e69f00']
    for axis,metric,label in zip(axes,['depth','luts'],['Logic depth / baseline','LUT count / baseline']):
        for i,method in enumerate(methods):
            ratios=[r['configs'][method]['summary'][metric]/r['configs']['baseline']['summary'][metric] for r in valid]
            axis.bar(x+(i-.5)*width,ratios,width,color=colors[i],label=method.replace('_',' + '))
        axis.axhline(1,color='#333333',linestyle='--',linewidth=1,label='baseline')
        axis.set_ylabel(label)
        axis.set_ylim(0,max(1.2,max(p.get_height() for p in axis.patches)*1.12))
        axis.grid(axis='y',alpha=.2);axis.set_axisbelow(True)
        axis.spines[['right','top']].set_visible(False)
    axes[0].legend(ncol=3,loc='lower left')
    axes[-1].set_xticks(x,[r['benchmark'] for r in valid],rotation=45,ha='right')
    fig.suptitle(f'LUT{data["lut_size"]} mapping: {len(valid)} CEC-proven EPFL benchmarks',fontsize=17,y=.98)
    fig.text(.5,.025,'Lower ratios are better. Unit-delay model; routed timing is separate. '
        +'Excluded/unproven: '+(', '.join(excluded) or 'none')+'.',ha='center',fontsize=10)
    fig.tight_layout(rect=[0,.06,1,.95])
    args.out.parent.mkdir(parents=True,exist_ok=True)
    fig.savefig(args.out.with_suffix('.png'),dpi=150)
    fig.savefig(args.out.with_suffix('.svg'))
    plt.close(fig)
    print(args.out.with_suffix('.png'))


if __name__=='__main__':
    main()
