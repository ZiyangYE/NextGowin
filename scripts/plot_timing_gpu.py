"""Static diagnostic timing plots with five-sample min/max whiskers."""
import argparse
import json
from pathlib import Path
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt


def main():
    ap=argparse.ArgumentParser();ap.add_argument('directory',type=Path);args=ap.parse_args()
    summary=json.loads((args.directory/'manifest.json').read_text())
    if summary['status']!='complete':raise ValueError('Timing suite not complete with GPU validation')
    names=['ctrl','log2','sqrt','core'];methods=[('cpu_parallel_ms','CPU (up to 8 threads)','#525963'),
        ('gpu_resident_wall_ms','GPU resident graph','#2a936e'),
        ('gpu_with_seed_transfer_and_readback_ms','GPU + seed/result copies','#4278bd')]
    fig,axes=plt.subplots(1,2,figsize=(12,5.7),sharey=True)
    for axis,batch in zip(axes,[1,64]):
        labels=[]
        for name in names:
            row=next(r for r in summary['runs'] if r['name']==name and r['batch']==batch)
            labels.append(f'{name}\n{row["nodes"]:,} nodes\n{row["levels"]} levels')
        for method,(key,label,color) in enumerate(methods):
            values=[];low=[];high=[]
            for name in names:
                data=json.loads((args.directory/f'{name}_{batch}'/'measurement/manifest.json').read_text())
                samples=np.asarray(data[key]);median=float(np.median(samples));values.append(median)
                low.append(median-float(min(samples)));high.append(float(max(samples))-median)
            x=np.arange(len(names))+(method-1)*.24
            axis.errorbar(x,values,color=color,label=label,yerr=[low,high],capsize=3,fmt='o',ms=5,lw=.9)
        axis.set_title(f'{batch} timing snapshot'+('s' if batch!=1 else ''))
        axis.set_xticks(np.arange(len(names)),labels,fontsize=8)
        axis.set_yscale('log');axis.grid(axis='y',alpha=.2,which='major');axis.set_axisbelow(True)
        axis.spines[['top','right']].set_visible(False)
    axes[0].set_ylabel('Milliseconds per full batch (log scale)')
    axes[1].legend(fontsize=8,loc='upper left')
    fig.suptitle('Forward + backward timing on real LUT graphs',fontsize=14)
    fig.text(.5,.015,'Exact int64; median of 5, whiskers min/max. Concurrent host/GPU activity: diagnostic data, not mapper speedup.',ha='center',fontsize=8)
    fig.tight_layout(rect=[0,.06,1,.94])
    for extension in ['png','svg']:fig.savefig(args.directory/('timing_comparison.'+extension),dpi=180)


if __name__=='__main__':main()
