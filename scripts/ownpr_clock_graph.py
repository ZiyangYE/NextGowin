"""Native clock connectivity model; configuration/capacity gates stay explicit."""
import json
from collections import defaultdict,deque
from pathlib import Path
from capture_runtime_tables import EXPECTED
from ownpr_image_storage import load_json,sha


class ClockGraph:
    @classmethod
    def for_tree(cls,tree):
        """Select an audited native capture by its content hash, never by pin guess."""
        root=Path(__file__).resolve().parents[1]/'results'
        for name in ('native_clock_inventory_005','native_clock_vex_input_002'):
            folder=root/name;meta=json.loads((folder/'manifest.json').read_text())
            if meta.get('topology_sha256')==tree.get('topology_sha256'):return cls(folder)
        raise ValueError('No approved native clock inventory matches this tree')

    def __init__(self,folder):
        folder=Path(folder);meta=json.loads((folder/'manifest.json').read_text());path=folder/'clock_topology.json'
        if (meta.get('status')!='captured' or meta.get('dll_sha256')!=EXPECTED
                or meta.get('open_chip_database_used') is not False or meta.get('vendor_pnr_executed') is not False
                or sha(path)!=meta.get('topology_sha256')):
            raise ValueError('Unverified native clock topology')
        self.sha256=meta['topology_sha256'];self.data=load_json(path);self.nodes=self.data['nodes']
        self.by_coordinate={tuple(n['coordinate']):n['id'] for n in self.nodes}
        if len(self.by_coordinate)!=len(self.nodes):raise ValueError('Ambiguous native clock coordinates')
        if any(n['id']!=i for i,n in enumerate(self.nodes)):raise ValueError('Noncanonical clock node numbering')
        self.adj=defaultdict(list)
        for a,b in self.data['edges']:
            if not 0<=a<len(self.nodes) or not 0<=b<len(self.nodes):raise ValueError('Invalid clock edge')
            self.adj[a].append(b)

    def audit_paths(self,paths):
        missing=[];edges=set()
        for path in paths:
            for destination,source in zip(path,path[1:]):
                a=self.by_coordinate.get(tuple(source));b=self.by_coordinate.get(tuple(destination))
                if a is None or b is None or b not in self.adj[a]:missing.append(dict(source=source,destination=destination))
                else:edges.add((a,b))
        return dict(status='clock_paths_match' if paths and not missing else 'incomplete_or_mismatched',paths=len(paths),
            unique_edges=len(edges),missing=missing,configuration_verified=False,timing_signoff=False)

    def candidate(self,source,sinks,clock_policy):
        if clock_policy!='AUTO':raise ValueError('Dedicated clock candidate requires an explicitly permitted clock routing policy')
        root=self.by_coordinate[tuple(source)];targets={self.by_coordinate[tuple(p)] for p in sinks};remaining=targets-{root}
        parents={root:None};queue=deque([root])
        while queue and remaining:
            node=queue.popleft()
            for target in self.adj[node]:
                if target not in parents:
                    parents[target]=node;queue.append(target);remaining.discard(target)
        if remaining:raise ValueError('Dedicated clock graph cannot reach all requested endpoints')
        selected={root:None};paths=[]
        for target in sorted(targets):
            chain=[];node=target
            while node is not None:
                chain.append(self.nodes[node]['coordinate']);selected[node]=parents[node];node=parents[node]
            paths.append(chain)
        check=self.audit_paths(paths)
        if check['status']!='clock_paths_match':raise ValueError('Constructed clock tree is disconnected')
        return dict(status='clock_topology_candidate',source=list(source),
            parents=[dict(node=self.nodes[n]['coordinate'],parent=self.nodes[p]['coordinate'] if p is not None else None,
                name=self.nodes[n]['name'],namespace=self.nodes[n]['namespace']) for n,p in sorted(selected.items())],
            paths_sink_to_source=paths,topology_sha256=self.sha256,connectivity_audit=check,
            clock_policy=clock_policy,configuration_verified=False,resource_capacity_verified=False,timing_signoff=False,
            scope='Own breadth-first clock-tree candidate on captured directed resources. Source modes, mux configuration and shared clock-resource legality remain to be verified.')

    def retarget(self,tree,source,sinks):
        """Keep unchanged sink paths and attach new sinks by added-edge BFS."""
        if tree.get('topology_sha256')!=self.sha256 or tree.get('source')!=list(source) or tree.get('clock_policy')!='AUTO':raise ValueError('Clock anchor source/topology/policy changed')
        paths=tree['paths_sink_to_source']
        if self.audit_paths(paths)['status']!='clock_paths_match':raise ValueError('Clock anchor has invalid paths')
        old={};old_paths={}
        for path in paths:
            if path[-1]!=list(source) or tuple(path[0]) in old_paths:raise ValueError('Clock anchor has wrong root or duplicate sinks')
            old_paths[tuple(path[0])]=path
            for i,point in enumerate(path):
                key=tuple(point);parent=tuple(path[i+1]) if i+1<len(path) else None
                if key in old and old[key]!=parent:raise ValueError('Conflicting clock anchor parents')
                old[key]=parent
        if old!={tuple(r['node']):tuple(r['parent']) if r['parent'] else None for r in tree['parents']}:raise ValueError('Clock anchor paths/parents differ')
        root=self.by_coordinate[tuple(source)];targets={self.by_coordinate[tuple(p)] for p in sinks};parents={root:None};retained=0
        for point,path in old_paths.items():
            if self.by_coordinate[point] not in targets:continue
            retained+=1
            for i,coordinate in enumerate(path):parents[self.by_coordinate[tuple(coordinate)]]=self.by_coordinate[tuple(path[i+1])] if i+1<len(path) else None
        remaining=targets-parents.keys();queue=deque(sorted(parents))
        while queue and remaining:
            node=queue.popleft()
            for target in self.adj[node]:
                if target not in parents:parents[target]=node;queue.append(target);remaining.discard(target)
        if remaining:raise ValueError('Anchored clock tree cannot reach new sinks')
        selected={root:None};new_paths=[]
        for target in sorted(targets):
            chain=[];node=target;seen=set()
            while node is not None:
                if node in seen:raise ValueError('Retargeted clock cycle')
                seen.add(node);chain.append(self.nodes[node]['coordinate']);selected[node]=parents[node];node=parents[node]
            if tuple(chain[0]) in old_paths and chain!=old_paths[tuple(chain[0])]:raise ValueError('Unchanged clock sink path moved')
            new_paths.append(chain)
        audit=self.audit_paths(new_paths)
        if audit['status']!='clock_paths_match':raise ValueError('Retargeted clock topology mismatch')
        return dict(status='clock_topology_candidate',source=list(source),parents=[dict(node=self.nodes[n]['coordinate'],parent=self.nodes[p]['coordinate'] if p is not None else None,name=self.nodes[n]['name'],namespace=self.nodes[n]['namespace']) for n,p in sorted(selected.items())],
            paths_sink_to_source=new_paths,topology_sha256=self.sha256,connectivity_audit=audit,clock_policy='AUTO',
            retained_sink_paths=retained,added_sink_paths=len(targets)-retained,removed_sink_paths=len(old_paths)-retained,
            configuration_verified=False,resource_capacity_verified=False,timing_signoff=False,
            scope='Unchanged sink paths preserved; new sink branches need fresh configuration, occupancy and whole-tree STA validation.')
