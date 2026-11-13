"""Scalarize netlist bus ports without merging distinct timing endpoints."""
def pins(cell,direction=None):
    for port,bits in cell['connections'].items():
        actual=cell['port_directions'][port]
        if direction is not None and actual!=direction:continue
        if not bits:continue
        for index,bit in enumerate(bits):
            yield (port if len(bits)==1 else f'{port}[{index}]'),bit
