"""Routing recovery cannot erase original failures or retry other DRC errors."""
import copy
import unittest
from pcb_mcp_client.routing import connectivity_retry_allowed,route_and_verify


def failure(*types):
    return dict(verdict='fail',findings=[dict(type=t,severity='error') for t in types],
                details=dict(unconnected_count=sum(t=='unconnected_items' for t in types)))


class Recovery(unittest.IsolatedAsyncioTestCase):
    async def run_flow(self,reports,limit=2,*,reuse_routed=False):
        calls=[];history=[];index=0
        async def call(tool,args):
            nonlocal index
            calls.append((tool,dict(args)))
            if tool=='drc_run':
                result=reports[min(index,len(reports)-1)];index+=1;return copy.deepcopy(result)
            if tool=='pcb_route_retry_prepare':return dict(pcb_build_id='child')
            if tool=='pcb_copper_check':return dict(verdict='incomplete',geometry_verdict='pass',findings=[])
            return dict(verdict='pass',findings=[])
        def persist(attempts):history.append(copy.deepcopy(attempts))
        try:result=await route_and_verify(call,dict(project_id='p',pcb_build_id='parent'),7,limit,persist,
                                          reuse_routed=reuse_routed)
        except ValueError as error:result=error
        return result,calls,history

    async def test_only_connectivity_failure_is_repaired_and_preserved(self):
        result,calls,history=await self.run_flow([failure('unconnected_items','via_dangling'),dict(verdict='pass',findings=[])])
        target,checks,attempts=result
        self.assertEqual(target['pcb_build_id'],'child')
        self.assertEqual([a['status'] for a in attempts],['failed','passed'])
        self.assertEqual(attempts[0]['checks']['drc_run']['verdict'],'fail')
        self.assertEqual(attempts[1]['parent_pcb_build_id'],'parent')
        self.assertEqual(len([c for c in calls if c[0]=='pcb_verify']),4)
        self.assertEqual(checks['pcb_copper_check']['verdict'],'incomplete')

    async def test_clearance_short_or_width_errors_stop(self):
        for kind in ('clearance','shorting_items','track_width'):
            result,calls,history=await self.run_flow([failure('unconnected_items',kind)])
            self.assertIsInstance(result,ValueError)
            self.assertFalse(any(tool=='pcb_route_retry_prepare' for tool,args in calls))
            self.assertEqual(history[-1][0]['status'],'failed')

    async def test_attempt_budget_is_enforced(self):
        result,calls,history=await self.run_flow([failure('unconnected_items')])
        self.assertIsInstance(result,ValueError)
        self.assertEqual(len([c for c in calls if c[0]=='pcb_autoroute']),2)
        self.assertEqual(len(history[-1]),2)

    async def test_reused_pcb_requires_fresh_checks_and_cannot_be_rerouted(self):
        good = dict(verdict='pass',findings=[])
        for report in (good, failure('unconnected_items')):
            with self.subTest(verdict=report['verdict']):
                result,calls,history=await self.run_flow([report],reuse_routed=True)
                tools = {name for name, _ in calls}
                self.assertIn('drc_run', tools)
                self.assertIn('pcb_verify', tools)
                self.assertFalse({'pcb_route_retry_prepare', 'pcb_autoroute'} & tools)
                self.assertEqual(len(history[-1]), 1)
                self.assertEqual(history[-1][0]['status'], 'passed' if report is good else 'failed')
                self.assertEqual(isinstance(result, ValueError), report is not good)

    def test_missing_structured_connectivity_proof_does_not_enable_retry(self):
        self.assertFalse(connectivity_retry_allowed(dict(verdict='fail',findings=[])))
        self.assertFalse(connectivity_retry_allowed(failure('via_dangling')))


if __name__=='__main__':unittest.main()
