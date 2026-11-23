"""Bounded incremental routing recovery through the public, immutable MCP API."""
import uuid
import asyncio


def connectivity_retry_allowed(report):
    findings=report.get('findings',[])
    return (report.get('verdict')=='fail' and bool(findings)
            and report.get('details',{}).get('unconnected_count',0)>0
            and any(f.get('type')=='unconnected_items' for f in findings)
            and all(f.get('type') in ('unconnected_items','via_dangling') for f in findings))


def require_check(tool,report):
    if report.get('verdict')=='fail':raise ValueError(tool+'_FAILED')
    if any(f.get('severity')=='error' for f in report.get('findings',[])):
        raise ValueError(tool+'_CHECK_ERRORS')
    expected=report.get('geometry_verdict') if tool=='pcb_copper_check' else report.get('verdict')
    if expected!='pass':raise ValueError(tool+'_FAILED')


async def route_and_verify(call,target,revision,attempt_limit,persist,max_passes=20,*,reuse_routed=False):
    if type(attempt_limit) is not int or not 1<=attempt_limit<=4:
        raise ValueError('ROUTING_ATTEMPT_LIMIT_INVALID')
    if type(max_passes) is not int or not 1<=max_passes<=100:
        raise ValueError('ROUTING_PASS_LIMIT_INVALID')
    target=dict(target);attempts=[]
    for number in range(1,attempt_limit+1):
        attempt=dict(number=number,target=dict(target),status='running',checks={})
        if reuse_routed:attempt['mode']='verified_parent_pcb'
        if attempts:attempt['parent_pcb_build_id']=attempts[-1]['target']['pcb_build_id']
        attempts.append(attempt);persist(attempts)
        try:
            for tool in ('pcb_verify','pcb_layout_check'):
                report=await call(tool,dict(target,stage='unrouted'))
                attempt['checks'][tool+'_unrouted']=report;persist(attempts)
                require_check(tool,report)
            if not reuse_routed:
                await call('pcb_autoroute',dict(target,max_passes=max_passes))
            report=await call('pcb_verify',target)
            attempt['checks']['pcb_verify']=report;persist(attempts);require_check('pcb_verify',report)
            report=await call('drc_run',target)
            attempt['checks']['drc_run']=report;persist(attempts)
            if not reuse_routed and connectivity_retry_allowed(report) and number<attempt_limit:
                attempt.update(status='failed',repair='incremental_connectivity_retry')
                persist(attempts)
                prepared=await call('pcb_route_retry_prepare',dict(target,expected_revision=revision,
                                                                 idempotency_key=uuid.uuid4().hex))
                if prepared['pcb_build_id']==target['pcb_build_id']:
                    raise ValueError('ROUTING_REPAIR_DID_NOT_CREATE_CHILD')
                target=dict(project_id=target['project_id'],pcb_build_id=prepared['pcb_build_id'])
                continue
            require_check('drc_run',report)
            for tool in ('pcb_layout_check','pcb_copper_check'):
                report=await call(tool,target);attempt['checks'][tool]=report;persist(attempts)
                require_check(tool,report)
            attempt['status']='passed';persist(attempts)
            return target,attempt['checks'],attempts
        except BaseException as error:
            attempt.update(status='interrupted' if isinstance(error,(KeyboardInterrupt,asyncio.CancelledError)) else 'failed',error=str(error))
            persist(attempts)
            raise
    raise ValueError('ROUTING_ATTEMPT_BUDGET_EXHAUSTED')
