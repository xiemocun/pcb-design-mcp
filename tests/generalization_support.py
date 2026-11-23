"""Protocol fixture helpers; no circuit-family or MPN dispatch."""
import json,uuid
from pcb_mcp.registry import canonical,sha


class Fixture:
    def __init__(self,session,output):self.session=session;self.output=output;self.calls=[];self.pid=None;self.rev=None;self.components={}
    async def call(self,name,args,error=False):
        reply=await self.session.call_tool(name,dict(request=args));self.calls.append(dict(tool=name,request=dict(package_sha256=sha(canonical(args))) if name in ('catalog_import','verification_model_import') else args,response=reply.model_dump(mode='json')))
        (self.output/'transcript.json').write_text(json.dumps(self.calls,indent=2)+'\n')
        assert bool(reply.isError)==error,str(reply)
        return reply.structuredContent['data'] if not error else None
    async def mutate(self,tool,**args):
        result=await self.call(tool,dict(project_id=self.pid,expected_revision=self.rev,idempotency_key=uuid.uuid4().hex,**args));self.rev=result['revision'];return result
    def pin(self,ref,number):
        c=self.components[ref];return dict(component_id=c['component_id'],pin_id=next(p['pin_id'] for p in c['pins'] if p['number']==number))
    async def create(self,name,components,nets,no_connects=()):
        created=await self.call('project_create',dict(name=name,profile='schematic_experiment'));self.pid=created['project_id'];self.rev=created['revision']
        for ref,mpn,value in components:
            part=(await self.call('parts_search',dict(query=mpn)))['candidates'][0];part=await self.call('part_get',dict(candidate_id=part['candidate_id']))
            symbol=next(x for x in (await self.call('library_search',dict(query=part['symbol_id'])))['candidates'] if x['library_id']==part['symbol_id'])
            symbol=await self.call('library_get',dict(candidate_id=symbol['candidate_id']))
            c=await self.mutate('component_add',candidate_id=symbol['candidate_id'],ref=ref,value=value or symbol['symbol']);self.components[ref]=c
            library,footprint_name=part['footprint_id'].split(':',1)
            fp=next(x for x in (await self.call('footprint_search',dict(query=footprint_name,library=library)))['candidates'] if x['footprint_id']==part['footprint_id'])
            fp=await self.call('footprint_get',dict(candidate_id=fp['candidate_id']))
            await self.mutate('footprint_bind',component_id=c['component_id'],candidate_id=fp['candidate_id'],reason='Exact source-selected package for protocol fixture')
            await self.mutate('part_bind',component_id=c['component_id'],candidate_id=part['candidate_id'])
        for name,kind,endpoints in nets:
            net=await self.mutate('net_create',name=name,kind=kind);await self.mutate('net_members_set',net_id=net['net_id'],pins=[self.pin(*p) for p in endpoints])
        for ref,number in no_connects:await self.mutate('pin_intent_set',pin=self.pin(ref,number),intent='no_connect',reason='Source-declared physical no-connect terminal')
        return await self.call('project_read',dict(project_id=self.pid))
