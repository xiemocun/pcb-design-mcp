"""Execute a data plan against existing MCP; stop on errors, retain raw evidence.

New designs create empty projects. Revisions fork a delivered base.
Interrupted runs can resume from a durable journal.
"""
import argparse,asyncio,fcntl,hashlib,json,os,sys,time,uuid
from datetime import timedelta
from pathlib import Path
from jsonschema import Draft202012Validator
from mcp import ClientSession,StdioServerParameters
from mcp.client.stdio import stdio_client
from .plan import Plan,resolve,check_pin_coverage,select_part
from .journal import Journal,save,digest
from .acceptance import criteria,delivery_criteria,evaluate
from . import extension
from .extension import CircuitExtension
from .revision_summary import differences, write_summary
from .revision import (ComponentGroupRevision, ComponentRevision, ConnectivityRevision, CircuitRemoval, FunctionalRevision, MechanicalRevision, MountingRevision, OutlineRevision, StackupRevision,
                       baseline, board_with_placements, check_base_state, check_board_revision_mode, check_connectivity_plan, check_i2c_pullup_plan,
                       connectivity_changes, connectivity_revision, contract_description_change, functional_additions, functional_limit_changes, i2c_pullup_changes,
                       mounting_request, outline_request, placement_requirements, resize_board,
                       removal_contract, removal_request, removal_snapshot,
                       replacement_request, revised_board, revised_criteria, revised_mounting, revised_stackup, stackup_request,
                       revised_functional_contract, revised_requirement_items, verify_delivered_placements,
                       verify_placement_requirements, verify_replacement_group_state, verify_replacement_state,
                       verify_reused_pcb, verify_revision_state)
from pcb_mcp import verification_models
from pcb_mcp.config import data_dir
from pcb_mcp.editing import spec_hash
from pcb_mcp.functional import FunctionalContract
from pcb_mcp.registry import canonical,sha as canonical_sha

def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()

def error_message(exc):
 if isinstance(exc,BaseExceptionGroup):return '; '.join(error_message(item) for item in exc.exceptions)
 return type(exc).__name__+': '+str(exc)

def check_findings(result):
 errors=[f for f in result.get('findings',[]) if f.get('severity')=='error']
 if errors:raise ValueError('CHECK_ERRORS: '+json.dumps(errors,ensure_ascii=False))
 if result.get('verdict')=='fail':raise ValueError('CHECK_FAILED')

def check_pins(plan,components):
 check_pin_coverage(plan,components)
 for n in plan.nets:
  for p in n.pins:resolve({'$pin':[p.ref,p.number]},components,{})

async def resolve_catalog(call, entries):
 records={};symbols={};footprints={};components={};catalog={}
 for component in entries:
  key=(component.mpn,component.part_definition_hash)
  if key not in records:
   found=await call('parts_search',dict(query=component.mpn))
   selected=select_part(found['candidates'],*key)
   records[key]=await call('part_get',dict(candidate_id=selected['candidate_id']))
  record=records[key]
  if record['definition_hash']!=component.part_definition_hash or record['evidence_status']!='available':
   raise ValueError('PART_EVIDENCE_CHANGED: '+component.mpn)
  lib=record['symbol_id'];fid=record['footprint_id']
  if lib not in symbols:
   found=await call('library_search',dict(query=lib));matches=[x for x in found['candidates'] if x.get('library_id')==lib]
   if len(matches)!=1:raise ValueError('EXACT_SYMBOL_NOT_UNIQUE: '+lib)
   symbols[lib]=await call('library_get',dict(candidate_id=matches[0]['candidate_id']))
   if not symbols[lib]['supported']:raise ValueError('UNSUPPORTED_SYMBOL: '+lib)
  if fid not in footprints:
   found=await call('footprint_search',dict(query=fid));matches=[x for x in found['candidates'] if x['footprint_id']==fid]
   if len(matches)!=1:raise ValueError('EXACT_FOOTPRINT_NOT_UNIQUE: '+fid)
   footprints[fid]=await call('footprint_get',dict(candidate_id=matches[0]['candidate_id']))
  components[component.ref]=dict(component_id=uuid.uuid4().hex,pins=symbols[lib]['pins'])
  catalog[component.ref]=dict(part=record,symbol=symbols[lib],footprint=footprints[fid])
 return components,catalog


async def add_components(mutate, entries, catalog):
 components={}
 for component in entries:
  selected=catalog[component.ref]
  added=await mutate('component_add',candidate_id=selected['symbol']['candidate_id'],
                     ref=component.ref,value=component.value or selected['symbol']['symbol'])
  components[component.ref]=added
  await mutate('footprint_bind',component_id=added['component_id'],candidate_id=selected['footprint']['candidate_id'],
               reason='Real source-selected package from resolved data plan')
  await mutate('part_bind',component_id=added['component_id'],candidate_id=selected['part']['candidate_id'])
 return components


async def execute(plan_path,requirement,out,preflight_only=False,resume=False,functional=None,model_bindings=None,base_run=None,
                  require_delivery=False):
 out=out.resolve()
 if not resume:out.mkdir(parents=True,exist_ok=False)
 lock=(out/'.run.lock').open('a')
 try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
 except OSError:
  lock.close();raise ValueError('RUN_ALREADY_ACTIVE')
 calls=[];journal=None;checks={};requested=None;revision_record=None;result=dict(status='running',project_id=None,started_from_empty=False,template_used=False,physical_verified=False,new_gerber_generated=False);start=time.monotonic()
 try:
  plan_data=json.loads(plan_path.read_text())
  mode=plan_data.get('mode') if isinstance(plan_data,dict) else None
  plan_type={'mechanical_revision':MechanicalRevision,'component_revision':ComponentRevision,
             'functional_revision':FunctionalRevision,'circuit_extension':CircuitExtension,
             'connectivity_revision':ConnectivityRevision,'circuit_removal':CircuitRemoval,
             'stackup_revision':StackupRevision,'outline_revision':OutlineRevision,
             'mounting_revision':MountingRevision}.get(mode,Plan) if isinstance(mode,str) else Plan
  if plan_type is ComponentRevision and 'replacements' in plan_data:
   plan_type=ComponentGroupRevision
  plan=plan_type.model_validate(plan_data)
  revising=plan_type is not Plan
  mechanical=plan_type is MechanicalRevision
  verification_only=plan_type is FunctionalRevision
  grouped_replacement=plan_type is ComponentGroupRevision
  replacing=plan_type is ComponentRevision or grouped_replacement
  replacements=plan.replacements if grouped_replacement else [plan.replacement] if replacing else []
  extending=plan_type is CircuitExtension
  rewiring=plan_type is ConnectivityRevision
  if sha(requirement)!=plan.requirement_sha256:raise ValueError('REQUIREMENT_HASH_CHANGED')
  request=json.loads(requirement.read_text())
  requested_moves=connectivity_changes(request)
  check_connectivity_plan(plan,requested_moves)
  pullup_changes=i2c_pullup_changes(request)
  description_change=contract_description_change(request)
  removal=removal_request(request)
  construction=stackup_request(request)
  outline=outline_request(request)
  mounting=mounting_request(request)
  if (plan_type is CircuitRemoval)!=(removal is not None):raise ValueError('CIRCUIT_REMOVAL_PLAN_MISMATCH')
  check_board_revision_mode(plan,request)
  placement_targets=placement_requirements(request)
  if revising:
   if base_run is None:raise ValueError('REVISION_BASE_RUN_REQUIRED')
   if functional is not None or (model_bindings is not None and not extending):
    raise ValueError('REVISION_INHERITS_BASE_FUNCTIONAL_CONTRACT')
   new_models=extension.added_model_bindings(plan,model_bindings if model_bindings is not None else {}) if extending else {}
   base,base_requirement,functional,model_bindings=baseline(base_run)
   base_contract,base_models=functional,model_bindings
   if removal is not None:
    functional=removal_contract(functional,removal)
    model_bindings={ref:key for ref,key in model_bindings.items() if ref not in removal.components}
   replaced_models=[dict(ref=row.ref,previous_model_id=model_bindings.get(row.ref),model_id=row.model_id)
                    for row in replacements if row.model_id is not None]
   new_models.update({row['ref']:row['model_id'] for row in replaced_models})
   model_bindings={**model_bindings,**new_models}
   if base['base_sha256']!=plan.base_sha256:raise ValueError('REVISION_BASE_EVIDENCE_CHANGED')
   check_i2c_pullup_plan(plan,pullup_changes,base_run,base)
   requested=revised_criteria(base_requirement,request,outline=outline,construction=construction,mounting=mounting)
   limit_changes=functional_limit_changes(request)
   additions,scenarios=functional_additions(request)
   if verification_only and not (limit_changes or additions or description_change is not None):
    raise ValueError('REVISION_FUNCTIONAL_LIMIT_CHANGE_REQUIRED')
   functional=revised_functional_contract(functional,limit_changes,additions,scenarios,pullup_changes=pullup_changes,
                                         description_change=description_change)
  else:
   if base_run is not None:raise ValueError('BASE_RUN_REQUIRES_REVISION_PLAN')
   if pullup_changes:raise ValueError('REVISION_I2C_PULLUP_REQUIRES_COMPONENT_REVISION')
   if description_change is not None:raise ValueError('CONTRACT_DESCRIPTION_CHANGE_REQUIRES_REVISION_PLAN')
   requested=criteria(request)
   if require_delivery and not requested:requested=delivery_criteria()
   if not preflight_only and functional is None and any(row.report=='functional_check' for row in requested):
    raise ValueError('FUNCTIONAL_CONTRACT_REQUIRED: supply the declared contract with --functional')
  from pcb_mcp.parts import rules_hash
  binding=dict(plan=sha(plan_path),requirement=sha(requirement),data_dir=str(data_dir()),preflight_only=preflight_only,
               client=digest({p.name:sha(p) for p in Path(__file__).parent.glob('*.py')}),server=rules_hash())
  functional_args=None
  if functional is not None:
   binding['functional']=digest(dict(contract=functional,model_bindings=model_bindings or {}))
   contract=FunctionalContract.model_validate(functional).model_dump(mode='json')
   functional_args=dict(contract=contract,contract_sha256=canonical_sha(canonical(contract)),model_bindings=model_bindings or {})
  if require_delivery:binding['require_delivery']=True
  if revising or require_delivery:
   effective_acceptance=[row.model_dump(mode='json') for row in requested]
   binding['effective_acceptance']=digest(effective_acceptance)
  if revising:binding['base']=base
  journal=Journal(out/'journal.json',binding,data_dir(),resume);calls=journal.calls
  if revising or require_delivery:save(out/'effective-acceptance.json',effective_acceptance)
  result['project_id']=journal.value['project_id']
  save(out/'run.json',result)
  save(out/'plan.json',plan.model_dump(mode='json'));(out/'requirement.json').write_bytes(requirement.read_bytes())
  result.update(plan_sha256=sha(out/'plan.json'),requirement_sha256=plan.requirement_sha256,runner_sha256=sha(Path(__file__)))
  server=StdioServerParameters(command=sys.executable,args=['-m','pcb_mcp.server','--pcb-run-marker',str(out)],env={**os.environ,'PCB_MCP_DATA_DIR':str(data_dir())})
  async with stdio_client(server) as (a,b):
   async with ClientSession(a,b) as session:
    await session.initialize();listing=await session.list_tools();schemas={t.name:t.inputSchema for t in listing.tools}
    schema_hash=digest(schemas)
    if journal.value.get('schema_hash',schema_hash)!=schema_hash:raise ValueError('RESUME_TOOL_SCHEMA_CHANGED')
    journal.value['schema_hash']=schema_hash;journal.flush();save(out/'tools.json',listing.model_dump(mode='json'))
    async def call(tool,args=None):
     params={} if args is None else dict(request=args)
     if tool not in schemas:raise ValueError('TOOL_NOT_AVAILABLE: '+tool)
     Draft202012Validator(schemas[tool]).validate(params)
     entry,cached=journal.begin(tool,args)
     print(tool+(' [replayed]' if cached else ''),flush=True)
     if not cached:
      params={} if entry['arguments'] is None else dict(request=entry['arguments'])
      response=await session.call_tool(tool,params,read_timeout_seconds=timedelta(seconds=300))
      journal.finish(entry,response.model_dump(mode='json'))
     response=entry['response'];save(out/'transcript.json',calls)
     if response.get('isError'):raise ValueError('MCP_ERROR '+tool+': '+str(response))
     return response['structuredContent']['data']
    if revising:
     original=await call('project_read',dict(project_id=base['project_id'],revision=base['revision']))
     check_base_state(base,original)
     change=dict(key='revision_'+plan.requirement_sha256[:24],value=request['natural_language'].strip(),
                 origin='user_statement',source_message_ref='requirement.json')
     result['parent']=base
     revision_record=dict(parent=base,request=change,board_before=original['board_constraints'])
     if any(item['key']==change['key'] for item in original['requirements']):
      raise ValueError('REVISION_REQUEST_ALREADY_APPLIED')
     requirements=[*revised_requirement_items(original['requirements'],[*limit_changes,*pullup_changes]),change]
     if limit_changes or pullup_changes or additions or removal is not None or description_change is not None:
      revision_record.update(functional_limit_changes=[row.model_dump(mode='json',exclude_none=True) for row in limit_changes],
                             previous_contract_sha256=base['contract_sha256'],
                             revised_contract_sha256=functional_args['contract_sha256'])
     if pullup_changes:
      revision_record['i2c_pullup_changes']=[row.model_dump(mode='json',exclude_none=True) for row in pullup_changes]
     if description_change is not None:
      revision_record['contract_description_change']=description_change.model_dump(mode='json')
     if additions:
      revision_record['functional_additions']=[row.id for row in additions]
     if scenarios:
      revision_record['functional_scenario_additions']=[row.id for row in scenarios]
     expected=original
     board=original['board_constraints']
     expected_positions=base['placements']
     if placement_targets:
      revision_record['placement_requirements']=request['placement_requirements']
      verify_placement_requirements(plan,placement_targets,expected_positions)
     if construction is not None:
      revision_record['stackup_change']=construction.model_dump(mode='json')
     if mounting is not None:
      revision_record['mounting_hole_change']=mounting.model_dump(mode='json',exclude_none=True)
     if mechanical:
      board,moved=revised_board(plan,original,expected_positions)
      expected_positions={**expected_positions,**{ref:row['after'] for ref,row in moved.items()}}
      revision_record.update(moved=moved,board_proposed=board)
     elif outline is not None:
      board,moved=resize_board(outline,plan,original,expected_positions,construction,mounting=mounting,
                               placements_bound=bool(placement_targets))
      expected_positions={**expected_positions,**{ref:row['after'] for ref,row in moved.items()}}
      revision_record.update(outline_change=outline.model_dump(mode='json'),moved=moved,board_proposed=board)
     elif construction is not None:
      board,moved=revised_stackup(construction,original,expected_positions,mounting=mounting,plan=plan)
      if moved:
       expected_positions={**expected_positions,**{ref:row['after'] for ref,row in moved.items()}}
       revision_record.update(moved=moved,board_proposed=board)
     elif mounting is not None:
      board,moved=revised_mounting(mounting,original,expected_positions,plan=plan)
      expected_positions={**expected_positions,**{ref:row['after'] for ref,row in moved.items()}}
      revision_record.update(moved=moved,board_proposed=board)
     elif replacing:
      targets=[await replacement_request(call,row,original,model_bindings) for row in replacements]
      replacement_args={'replacements':targets} if grouped_replacement else targets[0]
      replacement_scope=replacement_args if grouped_replacement else {'replacement':replacement_args}
      if replaced_models:
       if grouped_replacement:
        revision_record['replacement_model_bindings']=replaced_models
       else:
        revision_record['replacement_model_binding']=replaced_models[0]
     elif extending:
      symbols,catalog=await resolve_catalog(call,plan.components)
      expected_positions=extension.preflight(plan,original,symbols,expected_positions)
      if new_models:
       available=verification_models.packages()
       for ref,model_id in new_models.items():
        verification_models.bound_model(available,ref,model_id,catalog[ref]['part']['definition_hash'])
       revision_record['added_model_bindings']=new_models
      save(out/'resolved-catalog.json',catalog)
      revision_record['circuit_extension']=plan.model_dump(mode='json',include={'components','nets','attachments','no_connects','placements'})
     elif rewiring:
      moves,expected=connectivity_revision(plan.moves,original)
      revision_record['connectivity_changes']=plan.model_dump(mode='json')['moves']
     elif removal is not None:
      removal_ids,expected=removal_snapshot(removal,original)
      expected_positions={ref:pose for ref,pose in expected_positions.items() if ref not in removal.components}
      board=expected['board_constraints']
      revision_record['circuit_removal']=removal.model_dump(mode='json')
      if board.get('placement_profile')!='explicit':
       board=board_with_placements(expected,expected_positions)
     save(out/'revision.json',dict(revision_record,status='preflight_passed'))
    else:
     # Resolve all identities/pins and validate all settings before creating a project.
     components,catalog=await resolve_catalog(call,plan.components)
     check_pins(plan,components);dummy_nets={n.name:uuid.uuid4().hex for n in plan.nets}
     for setting in plan.settings:
      args=resolve(setting.args,components,dummy_nets)
      Draft202012Validator(schemas[setting.tool]).validate(dict(request=dict(project_id='0'*32,expected_revision=0,idempotency_key='preflight',**args)))
     save(out/'resolved-catalog.json',catalog)
    result['preflight_passed']=True
    if preflight_only:
     result['status']='preflight_passed';return result
    if revising:
     created=await call('project_fork',dict(project_id=base['project_id'],expected_revision=base['revision'],
                                           name=plan.name,idempotency_key=uuid.uuid4().hex))
     expected_parent=dict(parent_project_id=base['project_id'],parent_revision=base['revision'],
                          parent_spec_sha256=base['spec_sha256'],approvals_inherited=False)
     if created['provenance']!=expected_parent or created['project_id']==base['project_id']:
      raise ValueError('REVISION_FORK_PROVENANCE_MISMATCH')
     pid=created['project_id'];rev=created['revision'];result['project_id']=pid;save(out/'run.json',result)
    else:
     created=await call('project_create',dict(name=plan.name,profile='schematic_experiment',idempotency_key=uuid.uuid4().hex));pid=created['project_id'];rev=created['revision'];result['project_id']=pid;save(out/'run.json',result)
     empty=await call('project_read',dict(project_id=pid))
     if empty['components'] or empty['nets'] or empty.get('provenance',{}).get('parent_project_id'):raise ValueError('PROJECT_NOT_EMPTY')
     result['started_from_empty']=True
    async def mutate(tool,**args):
     nonlocal rev
     response=await call(tool,dict(project_id=pid,expected_revision=rev,idempotency_key=uuid.uuid4().hex,**args));rev=response['revision'];result['revision']=rev;return response
    if revising:
     if mechanical or construction is not None or outline is not None or mounting is not None:
      await mutate('board_constraints_set',**board)
     elif replacing:
      preview=await call('component_replacement_preview',dict(project_id=pid,revision=rev,**replacement_args))
      revision_record['replacement']=preview
      if preview['original_spec_sha256']!=base['spec_sha256']:
       raise ValueError('REVISION_COMPONENT_REPLACEMENT_BASE_CHANGED')
      if grouped_replacement or pullup_changes or not preview['compatible'] or replaced_models:
       revision_record['replacement_initial_preview']=preview
       candidate=await call('functional_check',dict(
        project_id=pid,revision=rev,expected_spec_sha256=preview['proposed_spec_sha256'],
        **replacement_scope,**functional_args))
       revision_record['replacement_functional_check']=candidate
       check_findings(candidate)
       if candidate['verdict']!='pass' or candidate['replacement_base_current'] is not True:
        raise ValueError('REVISION_REPLACEMENT_FUNCTIONAL_CHECK_INCOMPLETE')
       replacement_args=dict(replacement_args,functional_run_id=candidate['run_id'])
       preview=await call('component_replacement_preview',dict(project_id=pid,revision=rev,**replacement_args))
       revision_record['replacement']=preview
      if not preview['compatible'] or preview['original_spec_sha256']!=base['spec_sha256']:
       raise ValueError('REVISION_COMPONENT_REPLACEMENT_PREVIEW_FAILED')
      await mutate('component_part_replace',**replacement_args,plan_sha256=preview['plan_sha256'])
      expected=await call('project_read',dict(project_id=pid,revision=rev))
      if grouped_replacement:
       verify_replacement_group_state(original,expected,preview['replacements'],replacements,
                                      preview['proposed_spec_sha256'])
      else:
       verify_replacement_state(original,expected,preview,plan.replacement)
     elif extending:
      components=await add_components(mutate,plan.components,catalog)
      expected=await extension.connect(mutate,plan,original,components)
      board=board_with_placements(extension.with_components(expected,components),expected_positions)
      await mutate('board_constraints_set',**board)
     elif rewiring:
      await mutate('net_pins_move',moves=moves)
     elif removal is not None:
      await mutate('circuit_remove',component_ids=removal_ids)
      if board!=expected['board_constraints']:
       await mutate('board_constraints_set',**board)
     await mutate('requirements_set',items=requirements)
    else:
     components=await add_components(mutate,plan.components,catalog)
     nets={}
     for n in plan.nets:
      net=await mutate('net_create',name=n.name,kind=n.kind);nets[n.name]=net['net_id']
      await mutate('net_members_set',net_id=net['net_id'],pins=[resolve({'$pin':[p.ref,p.number]},components,nets) for p in n.pins])
     for nc in plan.no_connects:await mutate('pin_intent_set',pin=resolve({'$pin':[nc.pin.ref,nc.pin.number]},components,nets),intent='no_connect',reason=nc.reason)
     for setting in plan.settings:await mutate(setting.tool,**resolve(setting.args,components,nets))
    state=await call('project_read',dict(project_id=pid));save(out/'state.json',state)
    if revising:
     if extending:expected=extension.verify_components(expected,state,catalog,components)
     verify_revision_state(expected,state,board,requirements)
     save(out/'revision.json',dict(revision_record,project_id=pid,revision=rev,status='edited'))
    if functional_args is not None:
     checks['functional_check']=await call('functional_check',dict(project_id=pid,revision=rev,expected_spec_sha256=spec_hash(state),**functional_args))
     save(out/'checks.json',checks);check_findings(checks['functional_check'])
     if checks['functional_check']['verdict']!='pass':raise ValueError('FUNCTIONAL_CONTRACT_NOT_MET')
     result['functional_reference_passed']=True
    for tool in ['spec_validate','bom_check','electrical_check']:
     checks[tool]=await call(tool,dict(project_id=pid,revision=rev));save(out/'checks.json',checks);check_findings(checks[tool])
    if checks['spec_validate']['verdict']!='pass':raise ValueError('SPEC_NOT_READY')
    checks['power_budget_check']=await call('power_budget_check',dict(project_id=pid,revision=rev));save(out/'checks.json',checks);check_findings(checks['power_budget_check'])
    sch=await call('schematic_build',dict(project_id=pid,expected_revision=rev,idempotency_key=uuid.uuid4().hex));result['schematic_build_id']=sch['build_id'];save(out/'run.json',result)
    st=dict(project_id=pid,build_id=sch['build_id'])
    for tool in ['schematic_verify','erc_run']:
     check=await call(tool,st);checks[tool]=check;save(out/'checks.json',checks)
     if check['verdict']!='pass':raise ValueError(tool+'_FAILED')
    await call('schematic_preview',st);checks['schematic_export']=await call('schematic_export',st)
    pcb_args=dict(project_id=pid,expected_revision=rev,idempotency_key=uuid.uuid4().hex,schematic_build_id=sch['build_id'])
    if mechanical:
     pcb_args['routing_base_pcb_build_id']=base['pcb_build_id']
    elif verification_only:
     pcb_args['reuse_parent_pcb_build_id']=base['pcb_build_id']
    pcb=await call('pcb_build',pcb_args);result['pcb_build_id']=pcb['pcb_build_id'];save(out/'run.json',result)
    pt=dict(project_id=pid,pcb_build_id=pcb['pcb_build_id'])
    from .routing import route_and_verify
    def persist_routes(attempts):
     save(out/'routing-attempts.json',attempts)
     checks.update(attempts[-1]['checks']);save(out/'checks.json',checks)
     result['pcb_build_id']=attempts[-1]['target']['pcb_build_id']
     result['routing_attempt_count']=0 if verification_only else len(attempts);save(out/'run.json',result)
    pt,routing_checks,routing_attempts=await route_and_verify(call,pt,rev,plan.routing_attempt_limit,
     persist_routes,plan.routing_max_passes,reuse_routed=verification_only)
    checks.update(routing_checks)
    await call('pcb_preview',pt);checks['pcb_review_export']=await call('pcb_review_export',pt);checks['bom_export']=await call('bom_export',dict(project_id=pid));save(out/'checks.json',checks)
    acceptance=evaluate(requested,checks);save(out/'acceptance.json',acceptance)
    result['software_acceptance']=acceptance['verdict']
    if acceptance['verdict']=='fail':raise ValueError('DECLARED_ACCEPTANCE_FAILED')
    if requested and acceptance['verdict']=='incomplete':raise ValueError('DECLARED_ACCEPTANCE_INCOMPLETE')
    if revising or require_delivery:
     delivery=checks['pcb_review_export']['manifest']
     if (acceptance['verdict']!='pass' or delivery['assembly']['status']!='complete_source_linked_tables'
         or delivery['portable_project']['passed'] is not True):
      raise ValueError('REVISION_DELIVERY_INCOMPLETE' if revising else 'DESIGN_JOB_DELIVERY_INCOMPLETE')
    if revising:
     if verification_only:
      verify_reused_pcb(base_run,base,checks['pcb_review_export']['manifest'])
      revision_record['retained_pcb_sha256']=pcb['board_hash']
     revision_record['actual_placements']=verify_delivered_placements(checks['pcb_review_export'],expected_positions)
     revision_record.update(pcb_build_id=result['pcb_build_id'],bundle_path=checks['pcb_review_export']['bundle_path'])
     result['revision_summary_sha256']=write_summary(
      out,base_run,base,checks['pcb_review_export'],revision_record['actual_placements'],change['value'],
      base_contract,functional_args['contract'],differences(base_models,model_bindings),
      base_requirement['software_acceptance'],acceptance,project_id=pid,retained_pcb=verification_only,
      mounting_change=mounting)
    result.update(status='cad_checks_passed',full_function_qualified=False,elapsed_seconds=time.monotonic()-start)
    return result
 except BaseException as exc:
  result.update(status='interrupted' if isinstance(exc,(KeyboardInterrupt,asyncio.CancelledError)) else 'failed',error=error_message(exc),elapsed_seconds=time.monotonic()-start)
  raise
 finally:
  # Refused resumes must not overwrite the original result or journal.
  if journal is not None or not resume:
   if requested is not None:
    acceptance=evaluate(requested,checks);save(out/'acceptance.json',acceptance);result['software_acceptance']=acceptance['verdict']
   if revision_record is not None:
    revision_record.update(project_id=result['project_id'],revision=result.get('revision'),status=result['status'])
    if 'error' in result:revision_record['error']=result['error']
    save(out/'revision.json',revision_record)
   result['call_count']=len(calls);save(out/'run.json',result)
  lock.close()

def cli(argv=None):
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('plan',type=Path);p.add_argument('--requirement',required=True,type=Path);p.add_argument('--output',required=True,type=Path);p.add_argument('--preflight-only',action='store_true');p.add_argument('--resume',action='store_true');p.add_argument('--functional',type=Path);p.add_argument('--model-bindings',type=Path);p.add_argument('--base-run',type=Path,help='Completed runner directory for a revision.');p.add_argument('--require-delivery',action='store_true',help='Require delivery checks and supply defaults when the request omits them.');args=p.parse_args(argv)
 try:
  result=asyncio.run(execute(args.plan,args.requirement,args.output,args.preflight_only,args.resume,json.loads(args.functional.read_text()) if args.functional else None,json.loads(args.model_bindings.read_text()) if args.model_bindings else None,args.base_run,require_delivery=args.require_delivery));print(json.dumps(result,ensure_ascii=False,indent=2));return 0
 except KeyboardInterrupt:return 130
 except Exception as exc:
  print(json.dumps(dict(status='failed',error=error_message(exc)),ensure_ascii=False),file=sys.stderr);return 1
if __name__=='__main__':raise SystemExit(cli())
