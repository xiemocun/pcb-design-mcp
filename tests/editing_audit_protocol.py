"""Actual MCP fork/atomic replacement/fresh audit + real KiCad/Freerouting."""
import asyncio,hashlib,json,os,shutil,sys,uuid
from pathlib import Path
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
R=Path(__file__).resolve().parents[1]

async def main():
    fixture=json.loads((R/'acceptance-parts/report.json').read_text())
    out=R/'acceptance-editing';out.mkdir(exist_ok=True)
    data=out/('run-'+uuid.uuid4().hex);shutil.copytree(fixture['data_root'],data)
    calls=[];checks=[]
    params=StdioServerParameters(command=sys.executable,args=[str(R/'run_server.py')],env={**os.environ,'PCB_MCP_DATA_DIR':str(data)})
    async with stdio_client(params) as (r,w):
      async with ClientSession(r,w) as session:
        await session.initialize();contract=await session.list_tools();assert len(contract.tools)==68
        (R/'tool-contract.json').write_text(json.dumps(contract.model_dump(mode='json'),ensure_ascii=False,indent=2))
        async def call(tool,request=None,error=None):
            result=await session.call_tool(tool,{} if request is None else {'request':request})
            calls.append(dict(tool=tool,arguments=request,result=result.model_dump(mode='json')))
            (out/'transcript.json').write_text(json.dumps(calls,ensure_ascii=False,indent=2))
            if error:
                assert result.isError and error in '\n'.join(b.text for b in result.content if hasattr(b,'text')),(tool,error,result)
                checks.append(error);return
            assert not result.isError,(tool,result)
            return result.structuredContent['data']
        caps=await call('system_capabilities');assert caps['contract_version']=='0.12.0'
        original_pid=fixture['project_id'];source=await call('project_read',dict(project_id=original_pid));sourcepath=data/original_pid/'state.json'
        original_bytes=sourcepath.read_bytes();rev=source['revision']
        forkreq=dict(project_id=original_pid,expected_revision=rev,name='Atomic source-linked 330 to 470 ohm edit',idempotency_key=uuid.uuid4().hex)
        fork=await call('project_fork',forkreq);pid=fork['project_id']
        assert (await call('project_fork',forkreq))==fork
        await call('project_fork',{**forkreq,'name':'conflicting duplicate'},'IDEMPOTENCY_CONFLICT')
        await call('project_fork',{**forkreq,'expected_revision':rev-1,'idempotency_key':uuid.uuid4().hex},'REVISION_CONFLICT')
        await call('project_fork',{**forkreq,'approved':True},'extra_forbidden')
        assert sourcepath.read_bytes()==original_bytes
        state=await call('project_read',dict(project_id=pid));rev=state['revision']
        assert rev==0 and not state['builds'] and not state['pcb_builds'] and not state['manufacturing_reviews'] and not state.get('idempotency')
        assert state['components']==source['components'] and state['nets']==source['nets']
        assert not any((data/pid).rglob('approval.json')) and not any((data/pid).rglob('*.gbr'))
        assert fork['provenance']['parent_revision']==source['revision'] and not fork['provenance']['approvals_inherited']
        empty_audit=await call('design_audit',dict(project_id=pid));assert empty_audit['status']=='blocked'
        component=next(c for c in state['components'].values() if c['ref']=='R1');cid=component['id']
        def m(**kw):return dict(project_id=pid,expected_revision=rev,idempotency_key=uuid.uuid4().hex,**kw)
        parts={}
        for mpn in ['MRS25000C3300FCT00','MRS25000C4700FCT00','WP7113ID']:
            found=await call('parts_search',dict(query=mpn));assert found['total']==1
            candidate=found['candidates'][0];parts[mpn]=candidate
            if mpn=='MRS25000C3300FCT00':
                fp_id=state['footprint_bindings'][cid]['candidate_id']
                await call('component_replacement_preview',dict(project_id=pid,revision=rev,component_id=cid,candidate_id=candidate['candidate_id'],footprint_candidate_id=fp_id),'PART_NOT_RESOLVED')
            parts[mpn]=await call('part_get',dict(candidate_id=candidate['candidate_id']))
        part330=parts['MRS25000C3300FCT00'];part470=parts['MRS25000C4700FCT00']
        fp=await call('footprint_search',dict(query=part330['footprint_id']));fp=await call('footprint_get',dict(candidate_id=fp['candidates'][0]['candidate_id']))
        fields=dict(component_id=cid,candidate_id=part330['candidate_id'],footprint_candidate_id=fp['candidate_id'])
        await call('component_replacement_preview',dict(project_id=pid,revision=rev,**{**fields,'candidate_id':parts['WP7113ID']['candidate_id']}),'REPLACEMENT_SYMBOL_MISMATCH')
        old=await call('footprint_search',dict(query='Resistor_THT:R_Axial_DIN0207_L6.3mm_D2.5mm_P7.62mm_Horizontal'))
        old=await call('footprint_get',dict(candidate_id=old['candidates'][0]['candidate_id']))
        await call('component_replacement_preview',dict(project_id=pid,revision=rev,**{**fields,'footprint_candidate_id':old['candidate_id']}),'PART_BINDING_MISMATCH')
        plan=await call('component_replacement_preview',dict(project_id=pid,revision=rev,**fields));assert plan['compatible'] and plan['connectivity_preserved']
        assert (await call('project_read',dict(project_id=pid)))['revision']==0
        original=(data/pid/'state.json').read_bytes()
        await call('component_part_replace',m(**fields,plan_sha256='0'*64),'REPLACEMENT_PLAN_CHANGED')
        await call('component_part_replace',m(**fields,plan_sha256=plan['plan_sha256'],approved=True),'extra_forbidden')
        assert (data/pid/'state.json').read_bytes()==original
        applyreq=m(**fields,plan_sha256=plan['plan_sha256']);applied=await call('component_part_replace',applyreq);rev=applied['revision']
        assert rev==1 and applied['value']=='330' and applied['mpn']==part330['mpn']
        assert (await call('component_part_replace',applyreq))==applied
        await call('component_part_replace',{**applyreq,'candidate_id':part470['candidate_id']},'IDEMPOTENCY_CONFLICT')
        await call('component_part_replace',{**applyreq,'idempotency_key':uuid.uuid4().hex},'REVISION_CONFLICT')
        edited=await call('project_read',dict(project_id=pid));assert edited['nets']==source['nets'] and edited['components'][cid]['pins']==component['pins']
        assert (await call('engineering_check',dict(project_id=pid)))['verdict']=='pass'
        b330=await call('schematic_build',m());target330=dict(project_id=pid,build_id=b330['build_id'])
        assert (await call('schematic_verify',target330))['verdict']=='pass'
        assert (await call('erc_run',target330))['verdict']=='pass'
        # Raising the declared range exposes an over-current replacement. The
        # mutation must fail atomically, and a valid 470-ohm replacement repairs it.
        cond=edited['operating_conditions'];cond.pop('reason');cond['max_v']=5.5;cond['reason']='Isolated replacement stress-bound negative test, not an actual new supply'
        rev=(await call('operating_conditions_set',m(**cond)))['revision']
        plan=await call('component_replacement_preview',dict(project_id=pid,revision=rev,**fields))
        assert not plan['compatible'] and any(f['code']=='LED_CURRENT_MARGIN' for f in plan['specification']['findings'])
        original=(data/pid/'state.json').read_bytes()
        await call('component_part_replace',m(**fields,plan_sha256=plan['plan_sha256']),'REPLACEMENT_VALIDATION_FAILED')
        assert (data/pid/'state.json').read_bytes()==original
        fields['candidate_id']=part470['candidate_id'];plan=await call('component_replacement_preview',dict(project_id=pid,revision=rev,**fields));assert plan['compatible']
        # Change any context after preview: same component/MPN is still not the same plan.
        rev=(await call('requirements_set',m(items=[dict(key='purpose',value='470 ohm LED edit acceptance',origin='proposed_assumption')])))['revision']
        await call('component_part_replace',m(**fields,plan_sha256=plan['plan_sha256']),'REPLACEMENT_PLAN_CHANGED')
        plan=await call('component_replacement_preview',dict(project_id=pid,revision=rev,**fields));rev=(await call('component_part_replace',m(**fields,plan_sha256=plan['plan_sha256'])))['revision']
        await call('schematic_verify',target330,'STALE_VALIDATION')
        cond['max_v']=3.3;cond['reason']='Restored original conditional 3V laboratory supply for 470 ohm LED'
        rev=(await call('operating_conditions_set',m(**cond)))['revision']
        b1=await call('schematic_build',m());b2=await call('schematic_build',m())
        await call('design_audit',dict(project_id=pid),'AUDIT_TARGET_AMBIGUOUS')
        sch=dict(project_id=pid,build_id=b2['build_id'])
        assert (await call('schematic_verify',sch))['verdict']=='pass'
        assert (await call('erc_run',sch))['verdict']=='pass'
        await call('schematic_preview',sch);await call('schematic_export',sch)
        pcb=await call('pcb_build',m(schematic_build_id=b2['build_id']));pt=dict(project_id=pid,pcb_build_id=pcb['pcb_build_id'])
        assert (await call('pcb_verify',{**pt,'stage':'unrouted'}))['verdict']=='pass'
        await call('pcb_autoroute',pt);assert (await call('pcb_verify',pt))['verdict']=='pass'
        assert (await call('drc_run',pt))['verdict']=='pass'
        # Readiness must require an actual preview, consistent with review_prepare.
        readiness=await call('design_readiness',dict(project_id=pid));assert any(b['code']=='PCB_PREVIEW' for b in readiness['blockers'])
        preview=await call('pcb_preview',pt)
        await call('design_audit',dict(project_id=pid,schematic_build_id=b1['build_id'],pcb_build_id=pcb['pcb_build_id']),'AUDIT_BUILD_MISMATCH')
        audit=await call('design_audit',dict(project_id=pid,revision=rev,pcb_build_id=pcb['pcb_build_id']))
        assert audit['status']=='ready_for_human_review',audit
        assert all(s['status'] in ('pass','not_applicable') for s in audit['stages'])
        assert audit['artifact_evidence'] and hashlib.sha256(Path(audit['json_path']).read_bytes()).hexdigest()==audit['sha256']
        assert not audit['manufacturing_approved'] and not audit['gerber_generated']
        # Fresh audit must notice a source board change and a missing/tampered preview.
        folder=data/pid/'pcb_builds'/pcb['pcb_build_id'];path=folder/'routed.kicad_pcb';raw=path.read_bytes();path.write_bytes(raw+b'\n')
        tampered=await call('design_audit',dict(project_id=pid,pcb_build_id=pcb['pcb_build_id']));assert tampered['status']=='blocked'
        assert any(s.get('error',{}).get('code')=='ARTIFACT_CHANGED' for s in tampered['stages']);path.write_bytes(raw)
        path=Path(preview['preview_path']);raw=path.read_bytes();path.write_bytes(raw+b'\n')
        assert (await call('design_audit',dict(project_id=pid,pcb_build_id=pcb['pcb_build_id'])))['status']=='blocked';path.write_bytes(raw)
        audit=await call('design_audit',dict(project_id=pid,pcb_build_id=pcb['pcb_build_id']));assert audit['status']=='ready_for_human_review'
        review=await call('manufacturing_review_prepare',pt)
        await call('fabrication_export',dict(project_id=pid,review_id=review['review_id']),'APPROVAL_AUTHORITY_UNCONFIGURED')
        assert sourcepath.read_bytes()==original_bytes
        assert not any((data/pid).rglob('*.gbr'))
        checks+=['Fork preserves original byte-for-byte and copies no builds/signatures/Gerbers','Resolved-source same-symbol replacement updates value/MPN/exact footprint atomically; connections unchanged','Rejected edits leave exact state bytes unchanged; stale preview/revision and idempotency replay/conflict checked','330-ohm over-current rejected at 5.5V; 470-ohm source-linked replacement repairs it','Actual 470-ohm KiCad netlist/ERC, PCB pad identity, Freerouting, zero DRC and preview','Fresh audits reject ambiguous/mismatched targets, changed board and preview; reports bind exact artifact hashes','Old builds invalidated; separate manufacturing signature still mandatory']
    report=dict(passed=True,call_count=len(calls),tool_count=len(contract.tools),checks=checks,data_root=str(data),original_project_id=original_pid,project_id=pid,revision=rev,schematic_build_id=b2['build_id'],pcb_build_id=pcb['pcb_build_id'],audit=audit,review=review,original_preserved=True,no_new_gerber=True,real_human_approval=False)
    (out/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2));print(json.dumps({k:v for k,v in report.items() if k not in ('audit','checks')},ensure_ascii=False,indent=2))

if __name__=='__main__':asyncio.run(main())
