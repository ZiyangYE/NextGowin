"""Initial PCLK table-90/91 and ordinary clock-switch requirements.

Validated graph edges are required. This does not encode I/O banks, source
primitive modes, global commands or a complete hardware image.
"""
from collections import defaultdict
import re
from ownpr_fuse_audit import SwitchChoices


class ClockChoices:
    def __init__(self,device,graph):
        self.device=device;self.graph=graph;self.ordinary=SwitchChoices(device);self.tables={};self.global_choices={};self.global_masks={}
        self.indegree=defaultdict(int)
        self.predecessors=defaultdict(set)
        self.selected_parents={}
        for a,bs in graph.adj.items():
            for b in set(bs):self.indegree[b]+=1;self.predecessors[b].add(a)
        self.locations=defaultdict(list)
        for r,row in enumerate(device.grid):
            for c,typ in enumerate(row):self.locations[typ].append((r,c))

    def table(self,typ,kind):
        key=typ,kind
        if key not in self.tables:
            records=[t for t in self.device.fse.blocks[str(typ)]['tables'] if t['type']==kind]
            if not records:self.tables[key]={};return {}
            if len(records)!=1:raise ValueError('Ambiguous clock configuration table')
            mask=defaultdict(set);choices={}
            for row in self.device.fse.read_rows(records[0]):
                source,destination=row[:2]
                if source<0 or destination<0:raise ValueError('Unimplemented signed clock choice')
                bits={self.ordinary.bit(typ,i) for i in row[2:] if i>=0};pair=source,destination
                if pair in choices:raise ValueError('Duplicate clock table choice')
                choices[pair]=bits;mask[destination].update(bits)
            self.tables[key]={pair:{bit:int(bit in selected) for bit in mask[pair[1]]} for pair,selected in choices.items()}
        return self.tables[key]

    def global_choice(self,kind,source,destination):
        key=kind,source,destination
        if key not in self.global_choices:
            matches=[]
            for typ,locations in self.locations.items():
                tables=[t for t in self.device.fse.blocks[str(typ)]['tables'] if t['type']==kind]
                if not any(row[:2]==(source,destination) for t in tables for row in self.device.fse.read_rows(t)):continue
                values=self.table(typ,kind).get((source,destination))
                if values is not None:
                    for r,c in locations:matches.append((r,c,values))
            if len(matches)>1:raise ValueError('Global clock choice has no unique physical configuration site')
            self.global_choices[key]=matches[0] if matches else None
        return self.global_choices[key]

    def primary_alias_parent(self,node):
        index=self.graph.by_coordinate[tuple(node)];record=self.graph.nodes[index];parents=self.predecessors[index]
        if not re.fullmatch(r'P\d+A',record['name'] or ''):return None
        matches=[self.graph.nodes[p] for p in parents if (self.graph.nodes[p]['name'] or '')+'A'==record['name'] and tuple(self.graph.nodes[p]['coordinate'][:2])==tuple(node[:2])]
        if len(matches)!=1:return None
        parent=matches[0]
        return tuple(parent['coordinate'])

    def disabled_global_mux(self,kind,destination):
        key=kind,destination
        if key not in self.global_masks:
            found={}
            for typ,locations in self.locations.items():
                tables=[t for t in self.device.fse.blocks[str(typ)]['tables'] if t['type']==kind]
                if not any(row[1]==destination for t in tables for row in self.device.fse.read_rows(t)):continue
                masks=[set(values) for (src,dst),values in self.table(typ,kind).items() if dst==destination]
                if not masks or any(m!=masks[0] for m in masks):raise ValueError('Inconsistent global clock mux mask')
                for r,c in locations:found[r,c]=masks[0]
            # The calibrated P31/P33 selectors span these two FSE sites.
            # Secondary drive requires every competing primary input disabled.
            if kind!=91 or destination not in (17,19) or set(found)!={(54,88),(81,94)}:raise ValueError('Uncalibrated distributed primary-clock disable mask')
            self.global_masks[key]={(r,c,y,x):0 for (r,c),bits in found.items() for y,x in bits}
        return dict(self.global_masks[key])

    @staticmethod
    def secondary_bottom_alias(source,destination):
        # Independently captured BS0(A)->SPCLKBOT0->P31A and
        # BS1(A)->SPCLKBOT1->P33A only; other SCLK regions remain closed.
        return (tuple(source),tuple(destination)) in (((-2,-1,261),(-2,-1,298)),((-2,-1,262),(-2,-1,300)))

    def edge(self,source,destination):
        source=tuple(source);destination=tuple(destination);a=self.graph.by_coordinate[source];b=self.graph.by_coordinate[destination]
        if b not in self.graph.adj[a]:raise ValueError('Clock configuration edge is not in the native graph')
        from ownpr_primary_p33_profile import edge as primary_profile
        primary=primary_profile(self,source,destination)
        if primary is not None:return primary
        sr,sc,si=source;dr,dc,di=destination;group={(-1,-2):90,(-2,-1):91}
        if (source,destination)==((-2,183,192),(-2,183,214)):
            # L22/F6 -> SPCLK33 source mux. Independently isolated prefix
            # and complete-path native captures agree with this table-48
            # mask, including its competing-input zero bit. No extrapolation
            # to another pad, source, tile type or device is permitted.
            if self.indegree[b]!=1 or self.device.grid[81][181]!=276:raise ValueError('Uncalibrated SPCLK33 source site')
            values=self.table(276,48).get((5,27))
            if values is None:raise ValueError('Missing native SPCLK33 source selection')
            return {(81,181,y,x):v for (y,x),v in values.items()},'spclk33_source_mux'
        if (source,destination)==((-2,183,214),(-2,-1,213)):
            if self.indegree[b]!=1:raise ValueError('SPCLK33 entry has multiple drivers')
            return {},'fixed_spclk33_entry'
        if source==(-2,-1,132) and destination in ((-1,-1,40),(-1,-1,41)):
            choice=self.global_choice(38,si,di)
            if choice is None:raise ValueError('Missing secondary clock source selection')
            r,c,values=choice
            return {(r,c,y,x):v for (y,x),v in values.items()},'secondary_source_mux'
        if (source,destination) in (((-1,-1,40),(-1,-1,277)),((-1,-1,277),(-2,-1,261)),((-1,-1,41),(-1,-1,278)),((-1,-1,278),(-2,-1,262))):
            if self.indegree[b]!=1:raise ValueError('Secondary clock fixed link has multiple drivers')
            return {},'fixed_secondary_bottom_link'
        if self.secondary_bottom_alias(source,destination):
            values=self.disabled_global_mux(91,17 if destination[2]==298 else 19)
            if destination[2]==298:
                alternate=self.global_choice(91,245,298)
                if alternate is None:raise ValueError('Missing bottom-clock alternate-input mask')
                r,c,mask=alternate;values.update({(r,c,y,x):0 for y,x in mask})
            return values,'secondary_bottom_alias_disable_primary'
        if source==(-2,-1,133) and destination==(-1,-1,16):
            # The central P30 input belongs to FSE table 38, not a regional
            # table-90/91 selector. Checked in both native P&R and independent
            # clock encoding for the fixed PCLKB0 input profile.
            choice=self.global_choice(38,133,16)
            if choice is None:raise ValueError('Missing central P30 clock selection')
            r,c,values=choice
            if (r,c)!=(54,88):raise ValueError('Uncalibrated central clock configuration site')
            return {(r,c,y,x):v for (y,x),v in values.items()},'central_p30_table38'
        if (source,destination) in (((-1,-1,16),(-1,-1,297)),((-1,-1,297),(-1,-2,245)),((-1,-1,297),(-2,-1,245))):
            if self.indegree[b]!=1:raise ValueError('Central clock copy has competing inputs')
            return {},'fixed_central_clock_copy'
        if min(sr,sc,dr,dc)>=1 and (sr,sc)==(dr,dc):
            r,c=dr-1,dc-1;pair=self.device.names.get(si),self.device.names.get(di)
            if pair in self.ordinary.template(self.device.grid[r][c]):
                return self.ordinary.requirements((r,c,*pair)),'ordinary_switch'
        if (sr,sc) in group and (sr,sc)==(dr,dc):
            choice=self.global_choice(group[sr,sc],si,di)
            if choice is None:
                if self.primary_alias_parent(destination)==source:return {},'primary_alias_selection_at_span'
                raise ValueError('Global clock choice has no physical configuration site')
            r,c,values=choice
            return {(r,c,y,x):v for (y,x),v in values.items()},'cmux_switch'
        if (sr,sc) in group and dr>=1 and dc>=1 and di in (291,292):
            r,c=dr-1,dc-1;values=self.table(self.device.grid[r][c],group[sr,sc]).get((si,di))
            if values is None:
                parent=self.primary_alias_parent(source)
                selected=self.selected_parents.get(source)
                configured_secondary=(source==(-2,-1,304) and selected==(-2,-1,261) and self.global_choice(91,261,304) is not None)
                # PCLKBOT0 directly drives four regional aliases per half. The
                # selector uses alias IDs, but local GT00/GT10 span tables use
                # primary IDs. Keep these namespaces distinct. All eight
                # selectors and 89 active spans match a reversible native
                # encoding; seven selectors also occur in an independent P&R
                # reference. This does not generalize to other alias families.
                configured_bottom=(source[:2] in group and source[2] in (286,292,298,304)
                    and selected==source[:2]+(245,) and self.global_choice(group[source[:2]],245,source[2]) is not None)
                if parent is not None and (selected==parent or self.secondary_bottom_alias(selected or (),source) or configured_secondary or configured_bottom):
                    values=self.table(self.device.grid[r][c],group[sr,sc]).get((parent[2],di))
            if values is None:raise ValueError(f'Unsupported clock span alias or entry: {source} -> {destination}; parent={self.selected_parents.get(source)}')
            return {(r,c,y,x):v for (y,x),v in values.items()},'span_entry'
        if min(sr,sc,dr,dc)>=1 and si==di and si in (291,292):return {},'fixed_tap_connection'
        if min(sr,sc,dr,dc)>=1 and si in (293,294) and di in range(269,277) and self.indegree[b]==1:
            return {},'fixed_branch_connection'
        fixed=self.graph.data['fixed_clock_source']
        if a==fixed['id'] and b in fixed['dedicated_entry_ids']:return {},'fixed_pad_clock_entry'
        raise ValueError(f'Unimplemented clock configuration edge: {source} -> {destination}')

    def requirements(self,tree):
        self.selected_parents={tuple(r['node']):tuple(r['parent']) if r['parent'] else None for r in tree['parents']}
        if len(self.selected_parents)!=len(tree['parents']):raise ValueError('Duplicate clock parent entry')
        values={};conflicts=[];kinds=defaultdict(int);edges=[]
        for row in tree['parents']:
            if row['parent'] is None:continue
            bits,kind=self.edge(row['parent'],row['node']);kinds[kind]+=1
            edges.append(dict(source=row['parent'],destination=row['node'],kind=kind,bits=len(bits)))
            for bit,value in bits.items():
                if bit in values and values[bit]!=value:conflicts.append(list(bit))
                values[bit]=value
        return values,dict(edge_kinds=dict(kinds),edges=edges,conflicts=conflicts,
            source_modes_verified=False,full_clock_legality_verified=False,full_bitstream_verified=False)
