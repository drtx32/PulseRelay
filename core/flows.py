"""Flow graph validation and execution for persisted PulseRelay flows."""
from __future__ import annotations
import uuid
import json
from jinja2 import StrictUndefined
from jinja2.sandbox import SandboxedEnvironment
from core.routing import matches
from core.event import ensure_event

NODE_TYPES={"endpoint_trigger","filter","transform","webhook_outbound","emit_event"}

def validate_flow(flow):
    nodes=flow.get('nodes') or []; edges=flow.get('edges') or []
    ids=[n.get('id') for n in nodes]
    errors=[]
    if not flow.get('id') or not flow.get('name'): errors.append('id and name are required')
    if not ids or len(ids)!=len(set(ids)): errors.append('nodes must have unique ids')
    if sum(n.get('type')=='endpoint_trigger' for n in nodes)!=1: errors.append('exactly one endpoint_trigger is required')
    if any(n.get('type') not in NODE_TYPES for n in nodes): errors.append('unsupported node type')
    if any(a not in ids or b not in ids for a,b in edges): errors.append('edge references unknown node')
    adjacency={i:[] for i in ids}
    for a,b in edges:
        if a in adjacency: adjacency[a].append(b)
    visiting=set(); visited=set()
    def walk(n):
        if n in visiting: return False
        if n in visited:return True
        visiting.add(n)
        ok=all(walk(x) for x in adjacency.get(n,[]))
        visiting.remove(n);visited.add(n);return ok
    if any(not walk(node_id) for node_id in ids):errors.append('flow graph contains a cycle')
    return {'valid':not errors,'errors':errors}

def execute_flow(store, flow, event, endpoint_id, relay=None):
    run_id='fr_'+uuid.uuid4().hex; store.create_flow_run(run_id,flow['id'],event.id)
    nodes={n['id']:n for n in flow['nodes']}; outgoing={i:[] for i in nodes}
    for a,b in flow['edges']:outgoing[a].append(b)
    trigger=next(n for n in flow['nodes'] if n['type']=='endpoint_trigger')
    if trigger.get('endpoint_id')!=endpoint_id:store.finish_flow_run(run_id,'skipped');return run_id
    queue=[(trigger['id'],event.to_dict())]; seen=set(); status='success'; error=''
    while queue:
        nid,value=queue.pop(0)
        if (nid,repr(value)) in seen:continue
        seen.add((nid,repr(value))); node=nodes[nid]; typ=node['type']; output=value; state='success'; delivery_id=None
        try:
            if typ=='filter' and not matches(ensure_event(value),node.get('expression') or {}): state='skipped'
            elif typ=='transform': output=SandboxedEnvironment(undefined=StrictUndefined,autoescape=False).from_string(node.get('template','{{ input }}')).render(input=value,event=value)
            elif typ=='webhook_outbound':
                dest=store.get_destination(node.get('endpoint_id',''))
                if dest is None or not dest.enabled: raise ValueError('webhook outbound connector is missing or disabled')
                payload=event.to_dict()
                payload['id']='evt_flow_'+uuid.uuid4().hex
                payload['source']={'type':'flow','id':flow['id']}
                payload['event']={'type':'flow.output','action':'','timestamp':event.event.timestamp,'dedupe_key':f'flow:{run_id}:{nid}'}
                payload['content']={'title':flow['name'],'text':output if isinstance(output,str) else json.dumps(output,ensure_ascii=False,default=str),'raw':output if isinstance(output,dict) else {'value':output},'html':'','files':[]}
                outbound_event=ensure_event(payload)
                store.record_event('flow',outbound_event)
                delivery_id='del_'+uuid.uuid4().hex
                store.create_delivery(delivery_id,dest.id,f'flow:{run_id}:{nid}',event_id=outbound_event.id,flow_run_id=run_id,node_id=nid)
            elif typ=='emit_event':
                if relay:
                    emitted=ensure_event(output if isinstance(output,dict) else {'content':{'text':str(output)},'event':{'type':'flow.emit'}})
                    emitted.context.extra['flow_run_id']=run_id
                    relay.ingest(emitted,source_type='flow')
            store.create_node_run(run_id,nid,state,value,output,delivery_id=delivery_id)
            if state=='success': queue.extend((child,output) for child in outgoing[nid])
        except Exception as exc:
            state='failed';status='failed';error=str(exc);store.create_node_run(run_id,nid,state,value,error=error);break
    store.finish_flow_run(run_id,status,error);return run_id
