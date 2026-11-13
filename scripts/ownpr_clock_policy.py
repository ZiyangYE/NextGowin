"""Record an internal clock policy change while preserving every other line."""
def allow_auto_clock(cst,net):
    expected=f'CLOCK_LOC "{net}" LOCAL_CLOCK;'
    lines=cst.splitlines(keepends=True);matches=[i for i,line in enumerate(lines) if line.strip()==expected]
    if len(matches)!=1:raise ValueError('Expected one explicit local-clock directive')
    index=matches[0]
    return ''.join(lines[:index]+lines[index+1:])
