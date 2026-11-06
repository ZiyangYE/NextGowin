"""Check reported effective I/O delays for the supported zero-delay contract.

File hashes alone cannot detect constraints changed inside a native STA process.
This independently checks displayed tIn/tOut increments in every timing section.
Endpoint completeness remains the responsibility of the full coverage audit.
"""
import math,re


def audit(text,sdc):
    commands=re.findall(r'^\s*set_(?:input|output)_delay\b([^\n]*)',sdc,re.M)
    if not commands:raise ValueError('Missing explicit I/O delay contract')
    for command in commands:
        value=re.search(r'-(?:min|max)\s+([-+\d.eE]+)\s+-clock\b',command)
        if not value or not math.isfinite(float(value[1])) or float(value[1])!=0:
            raise ValueError('Effective I/O audit currently requires zero min/max delays')
    counts={'tIn':0,'tOut':0};sections={};kind=None
    for line in text.splitlines():
        heading=re.match(r'^3\.(?:3|7)\.\d+ (Setup|Hold|Recovery|Removal) Analysis Report',line)
        if heading:kind=heading[1]
        elif re.match(r'^3\.\d+\.',line):kind=None
        match=re.match(r'^\s*([-+\d.]+)\s+([-+\d.]+)\s+(tIn|tOut)\b',line)
        if not match:continue
        if kind is None:raise ValueError('I/O timing row outside a supported analysis section')
        delay=float(match[2]);typ=match[3]
        if not math.isfinite(delay) or abs(delay)>.00051:
            raise ValueError('Effective I/O delay differs from source SDC: '+line.strip())
        counts[typ]+=1;sections[kind]=sections.get(kind,0)+1
    if not all(counts.values()):raise ValueError('Missing effective input/output timing rows')
    return dict(status='reported_effective_zero_io_delays_verified',rows=counts,sections=sections,
        tolerance_ns=.00051,scope='All displayed tIn/tOut increments; coverage and clock semantics checked separately.')
