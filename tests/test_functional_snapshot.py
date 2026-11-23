"""Long solves cannot monopolize mutations or certify a subsequently edited board."""
import contextlib,copy,json,tempfile,threading,unittest
from pathlib import Path
from unittest.mock import patch
from pcb_mcp import registry
from pcb_mcp.common import DomainError
from pcb_mcp.editing import EditingService,spec_hash
from pcb_mcp.models import ReplacementTarget
from pcb_mcp.functional import FunctionalService,FunctionalContract,FunctionalCheck,Scenario
from pcb_mcp_client.journal import Journal, save

class Service(FunctionalService,EditingService):
    def __init__(self,root):
        self.root=Path(root);self.mutex=threading.Lock()
        self.state=dict(id='a'*32,revision=0,components={'a':{'value':'original'}})
        self.runtime_source_sha256=registry.sha(b''.join(p.read_bytes() for p in sorted(Path(__import__('pcb_mcp.functional',fromlist=['__file__']).__file__).parent.glob('*.py'))))
    @contextlib.contextmanager
    def locked(self):
        with self.mutex,registry.using(self.root):yield
    def load(self,pid):return self.state
    def project_path(self,pid):return self.root/pid

def request(s):
    contract=FunctionalContract.model_validate(dict(description='Synthetic transaction-only fixture for concurrency regression.',scenarios=[dict(id='nominal',engine='spice_op')],requirements=[dict(id='output',description='Fixture voltage range for transaction behavior only.',quantity='voltage',positive=dict(ref='J1',number='1'),negative=dict(ref='J1',number='2'),minimum=0.,maximum=5.,scenarios=['nominal'])]))
    return FunctionalCheck(project_id=s.state['id'],revision=0,expected_spec_sha256=spec_hash(s.state),contract=contract,contract_sha256=registry.sha(registry.canonical(contract.model_dump(mode='json'))))

class Snapshot(unittest.TestCase):
    def test_other_mutation_completes_during_solve_and_result_remains_historical(self):
        with tempfile.TemporaryDirectory() as d:
            service=Service(d);req=request(service);entered=threading.Event();finish=threading.Event();results=[];errors=[];snapshots=[]
            def evaluate(state,req,folder):
                entered.set()
                if not finish.wait(3):raise AssertionError('Independent mutation blocked by solve')
                snapshots.append(copy.deepcopy(state));return dict(project_id=state['id'],revision=state['revision'],verdict='pass')
            def run():
                try:results.append(service.functional_check(req))
                except BaseException as exc:errors.append(exc)
            with patch('pcb_mcp.functional.evaluate',side_effect=evaluate),patch('pcb_mcp.functional.parts.rules_hash',return_value='b'*64):
                worker=threading.Thread(target=run);worker.start()
                try:
                    self.assertTrue(entered.wait(2));self.assertTrue(service.mutex.acquire(timeout=1))
                    try:service.state['revision']=1;service.state['components']['a']['value']='edited'
                    finally:service.mutex.release()
                finally:finish.set();worker.join(3)
            self.assertFalse(worker.is_alive());self.assertEqual(errors,[])
            self.assertEqual(snapshots[0]['components']['a']['value'],'original')
            self.assertFalse(results[0]['applies_to_current_spec']);self.assertEqual(results[0]['revision'],0)
            folder=Path(results[0]['artifact_directory']);self.assertEqual(json.loads((folder/'state.json').read_text())['revision'],0)
            index=json.loads((service.project_path(service.state['id'])/'functional_index.json').read_text());self.assertEqual(len(index),1)
    def test_source_change_during_execution_cannot_publish_evidence(self):
        with tempfile.TemporaryDirectory() as d:
            service=Service(d);req=request(service)
            def evaluate(*args):service.runtime_source_sha256='0'*64;return dict(verdict='pass')
            with patch('pcb_mcp.functional.evaluate',side_effect=evaluate),patch('pcb_mcp.functional.parts.rules_hash',return_value='b'*64),self.assertRaisesRegex(Exception,'source changed'):
                service.functional_check(req)
            self.assertFalse((service.project_path(service.state['id'])/'functional_index.json').exists())

    def test_candidate_report_is_scoped_to_the_exact_unapplied_replacement(self):
        with tempfile.TemporaryDirectory() as d:
            service=Service(d)
            target=ReplacementTarget(component_id='a'*32,candidate_id='b'*32,footprint_candidate_id='c'*32)
            original=copy.deepcopy(service.state)
            proposed=copy.deepcopy(original)
            proposed['components']['a']['value']='replacement'
            req=request(service).model_copy(update={'replacement':target,'expected_spec_sha256':spec_hash(proposed)})
            backend='b'*64

            def evaluate(state,req,folder):
                self.assertEqual(state,proposed)
                return dict(project_id=state['id'],revision=state['revision'],spec_sha256=spec_hash(state),
                            contract_sha256=req.contract_sha256,verdict='pass',findings=[],runs=[dict(engine='linear_dc')])

            with (
                patch.object(service,'replacement_snapshot',return_value=(proposed,{},{})),
                patch('pcb_mcp.functional.evaluate',side_effect=evaluate),
                patch('pcb_mcp.functional.parts.rules_hash',return_value=backend),
            ):
                result=service.functional_check(req)
            self.assertEqual(service.state,original)
            self.assertFalse(result['applies_to_current_spec'])
            self.assertTrue(result['replacement_base_current'])
            with registry.using(service.root):
                entry,report=service.replacement_evidence(original,proposed,target.model_dump(),result['run_id'],backend)
                self.assertEqual(entry['scope'],'replacement')
                self.assertEqual(report['spec_sha256'],spec_hash(proposed))
                with self.assertRaisesRegex(DomainError,'REPLACEMENT_FUNCTIONAL_SNAPSHOT_CHANGED'):
                    service.replacement_evidence(original,original,target.model_dump(),result['run_id'],backend)
                with self.assertRaisesRegex(DomainError,'REPLACEMENT_FUNCTIONAL_ENVIRONMENT_CHANGED'):
                    service.replacement_evidence(original,proposed,target.model_dump(),result['run_id'],'d'*64)

    def test_explicit_method_and_time_budget_are_bounded_and_do_not_change_legacy_identity(self):
        old=Scenario(id='op',engine='spice_op').model_dump(mode='json')
        self.assertNotIn('integration_method',old);self.assertNotIn('solver_timeout_s',old)
        for value in [0.,301.,float('inf')]:
            with self.assertRaises(ValueError):Scenario(id='op',engine='spice_op',solver_timeout_s=value)
        with self.assertRaisesRegex(ValueError,'REQUIRES_TRANSIENT'):Scenario(id='op',engine='spice_op',integration_method='gear')
        with self.assertRaises(ValueError):Scenario(id='t',engine='spice_transient',stop_s=1.,step_s=.01,integration_method='gear .control')
        self.assertEqual(Scenario(id='t',engine='spice_transient',stop_s=1.,step_s=.01,integration_method='gear',solver_timeout_s=60.).model_dump()['integration_method'],'gear')

    def test_group_evidence_cannot_authorize_a_different_set(self):
        with tempfile.TemporaryDirectory() as d:
            service=Service(d)
            original=copy.deepcopy(service.state)
            proposed=copy.deepcopy(original)
            proposed['components']['a']['value']='group replacement'
            targets=[ReplacementTarget(component_id=key*32,candidate_id='b'*32,footprint_candidate_id='c'*32)
                     for key in ('a','d')]
            target_values=[row.model_dump() for row in targets]
            req=request(service).model_copy(update={'replacements':targets,'expected_spec_sha256':spec_hash(proposed)})
            backend='b'*64
            report=dict(project_id=original['id'],revision=original['revision'],spec_sha256=spec_hash(proposed),
                        contract_sha256=req.contract_sha256,verdict='pass',findings=[],runs=[])
            with (
                patch.object(service,'replacement_group_snapshot',return_value=(proposed,[])),
                patch('pcb_mcp.functional.evaluate',return_value=report),
                patch('pcb_mcp.functional.parts.rules_hash',return_value=backend),
            ):
                result=service.functional_check(req)
            self.assertEqual(result['replacements'],target_values)
            self.assertEqual(service.state,original)
            with registry.using(service.root):
                service.replacement_evidence(original,proposed,target_values,result['run_id'],backend)
                altered=[target_values[0],dict(target_values[1],candidate_id='f'*32)]
                for target in (target_values[0],altered):
                    with self.assertRaisesRegex(DomainError,'REPLACEMENT_FUNCTIONAL_SNAPSHOT_CHANGED'):
                        service.replacement_evidence(original,proposed,target,result['run_id'],backend)

    def test_lost_functional_response_recovers_only_intact_completed_evidence(self):
        with tempfile.TemporaryDirectory() as d:
            service=Service(d)
            req=request(service)
            project=service.project_path(req.project_id)
            project.mkdir()
            save(project/'state.json',service.state)
            binding={'server':'b'*64}

            def evaluate(state, req, folder):
                (folder/'trace.csv').write_text('time,voltage\n0,1\n')
                return dict(project_id=req.project_id,revision=req.revision,spec_sha256=spec_hash(state),
                            contract_sha256=req.contract_sha256,verdict='pass',runs=[dict(engine='linear_dc')])

            with patch('pcb_mcp.functional.evaluate',side_effect=evaluate),patch(
                    'pcb_mcp.functional.parts.rules_hash',return_value=binding['server']):
                service.functional_check(req)
                path=Path(d)/'journal.json'
                journal=Journal(path,binding,service.root)
                entry,_=journal.begin('project_create',{})
                journal.finish(entry,dict(isError=False,structuredContent={'data':{'project_id':req.project_id}}))
                args=req.model_dump(mode='json')
                journal.begin('functional_check',args)
                report=service.functional_check(req)
            trace=Path(report['artifact_directory'])/'trace.csv'
            original=trace.read_bytes()
            trace.write_bytes(original+b'1,3\n')
            with self.assertRaisesRegex(ValueError,'RESUME_FUNCTIONAL_EVIDENCE_CHANGED'):
                Journal(path,binding,service.root,resume=True)
            self.assertNotIn('response',json.loads(path.read_bytes())['calls'][-1])
            trace.write_bytes(original)
            recovered=Journal(path,binding,service.root,resume=True)
            recovered.begin('project_create',{})
            entry,cached=recovered.begin('functional_check',args)
            self.assertTrue(cached)
            self.assertEqual(entry['response']['structuredContent']['data'],report)
            self.assertEqual(entry['recovered_from'],'functional_index')

if __name__=='__main__':unittest.main()
