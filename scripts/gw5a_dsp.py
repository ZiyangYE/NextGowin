"""Map checked Verific multiply helpers to combinational GW5A hard DSPs.

Helper inference leaves sequential logic outside DSPs. A separately proved,
opt-in output-register packing pass can authorize OREG use during emission.
Unknown helper bodies are rejected rather than interpreted by their names.
"""
import hashlib
from pathlib import Path
import re
from gowin_cones import masked
from verific_bridge import units
from gowin_netlist_format import LIBRARY,EXPECTED,formats


def extend(signal,width,signed,target):
    if width==target:return signal
    return '{{%d{%s}},%s}' % (target-width,f'{signal}[{width-1}]' if signed else "1'b0",signal)


def choose(aw,asigned,bw,bsigned):
    aa=aw+int(not asigned);bb=bw+int(not bsigned)
    if max(aa,bb)<=12:return 'MULT12X12',12,12,False
    for kind,a,b in [('MULTALU27X18',27,18),('MULT27X36',27,36)]:
        if aa<=a and bb<=b:return kind,a,b,False
        if bb<=a and aa<=b:return kind,a,b,True
    return None


def primitive_library():
    if hashlib.sha256(LIBRARY.read_bytes()).hexdigest()!=EXPECTED:raise ValueError('Audited GW5A model changed')
    source=LIBRARY.read_text(encoding='utf-8');rows=[]
    for kind in ('MULT12X12','MULTALU27X18','MULT27X36'):
        a,b=units(source)[kind];body=masked(source[a:b]);head=body[:body.index(';')+1]
        declarations=re.findall(r'^\s*(?:parameter|input|output)\b[^;]*;',body,re.M)
        rows.append('(* blackbox = 1 *) '+head+'\n'+'\n'.join(declarations)+'\nendmodule\n')
    return '// Interfaces extracted from hash-pinned Gowin GW5A prim_sim.v.\n'+'\n'.join(rows)


def explicit_configuration(design,authorized_packed=()):
    """Make simulator/native-reader defaults explicit; bind inactive clock pins.

Combinational instances may bind inactive clock pins. An authorized OREG
retains its proven clock/enable pins; other register stages remain bypassed.
"""
    library=formats();records=[]
    for module,row in design['modules'].items():
        if int(row.get('attributes',{}).get('blackbox','0'),2):continue
        clk=row.get('ports',{}).get('clk',{})
        for name,cell in row.get('cells',{}).items():
            if cell['type'] not in ('MULT12X12','MULTALU27X18','MULT27X36') or not name.endswith('.ng_dsp'):continue
            defaults=library[cell['type']]
            params=cell['parameters']
            packed=(module,name) in authorized_packed
            for key,value in defaults.items():
                if key in params:continue
                if value.startswith('"'):params[key]=value.strip('"')
                else:
                    match=re.fullmatch(r"(\d+)'([bhd])([0-9a-fA-F]+)",value)
                    if match:params[key]=format(int(match[3],{'b':2,'h':16,'d':10}[match[2]]),'0'+match[1]+'b')
                    elif value.isdigit():params[key]=format(int(value),'032b')
                    else:raise ValueError('Unsupported native DSP default: '+key)
            active={key:value for key,value in params.items() if key.endswith('_CLK') and value!='BYPASS'}
            if active and (not packed or active!={'OREG_CLK':'CLK0'}):
                raise ValueError('Combinational DSP acquired registered behavior')
            old=list(cell['connections']['CLK'])
            if not packed and clk.get('direction')=='input' and len(clk.get('bits',[]))==1:
                cell['connections']['CLK']=clk['bits']*2
            records.append(dict(module=module,instance=name,cell=cell['type'],clock_before=old,
                clock_after=cell['connections']['CLK'],all_register_clocks_bypassed=not packed,proved_output_register=packed,explicit_parameters=len(params)))
    return records


def prepare(text):
    edits=[];records=[];gold=[];gate=[]
    for name,(a,b) in units(text).items():
        match=re.fullmatch(r'mult_(\d+)([su])_(\d+)([su])',name)
        if not match:continue
        aw=int(match[1]);bw=int(match[3]);sa=match[2]=='s';sb=match[4]=='s'
        if sa!=sb:continue
        ow=aw+bw
        header=f'module {name} (a,b,o); input [{aw-1}:0] a; input [{bw-1}:0] b; output [{ow-1}:0] o;'
        if sa:
            expected=header+f'wire [{aw-1}:0] l; wire [{bw-1}:0] r; assign l=a[{aw-1}]?-a:a; assign r=b[{bw-1}]?-b:b; assign o=(a[{aw-1}]^b[{bw-1}])?-(l*r):l*r; endmodule'
        else:expected=header+'assign o=a*b; endmodule'
        clean=lambda s:re.sub(r'\s+','',masked(s))
        original=text[a:b]
        if clean(original)!=clean(expected):raise ValueError('Unrecognized multiply semantics: '+name)
        selected=choose(aw,sa,bw,sb)
        if selected is None:continue
        kind,pa,pb,swap=selected
        ax,bx=('b','a') if swap else ('a','b');wa,wb=(bw,aw) if swap else (aw,bw)
        ea=extend(ax,wa,sa,pa);eb=extend(bx,wb,sb,pb)
        width={'MULT12X12':24,'MULTALU27X18':48,'MULT27X36':63}[kind]
        body=header+f'\nwire [{width-1}:0] product;\n'
        pins=[f'.A({ea})',f'.B({eb})',".CLK(2'b00)",".CE(2'b00)",".RESET(2'b00)",'.DOUT(product)']
        params=''
        if kind!='MULT12X12':pins += [".D(26'b0)",".PSEL(1'b0)",".PADDSUB(1'b0)"]
        if kind=='MULTALU27X18':
            params="#(.C_SEL(1'b0)) "
            pins += [".C(48'b0)",".SIA(27'b0)",".CASI(48'b0)",".ACCSEL(1'b0)",".ASEL(1'b0)",".CSEL(1'b0)",".CASISEL(1'b0)",".ADDSUB(2'b00)",'.CASO()', '.SOA()']
        body+=kind+' '+params+'ng_dsp('+','.join(pins)+f');\nassign o=product[{ow-1}:0];\nendmodule'
        edits.append((a,b,body));records.append(dict(module=name,cell=kind,a_width=aw,b_width=bw,signed=sa,swapped=swap,
            output_width=ow,latency=0,registers_absorbed=False))
        gold.append(original.replace('module '+name,'module gold_'+name,1));gate.append(body.replace('module '+name,'module gate_'+name,1))
    for a,b,body in reversed(edits):text=text[:a]+body+text[b:]
    return text,dict(schema='gw5a-dsp-helper-map-v1',multipliers=records,model_sha256=EXPECTED,
                     scope='Checked full-product signed/unsigned helpers; DSP registers bypassed; external sequential logic unchanged.'), '\n'.join(gold), '\n'.join(gate)
