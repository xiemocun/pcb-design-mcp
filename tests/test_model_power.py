"""A stale or changed simulation must never unlock prototype power review."""
import json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from pcb_mcp import registry,model_power,spice


class ModelPowerTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.context=registry.using(self.temp.name);self.context.__enter__()
        self.folder=Path(self.temp.name)/('a'*32);self.run=self.folder/'functional_runs'/('b'*32);self.run.mkdir(parents=True)
        pin=dict(component_id='J',pin_id='1')
        self.s=dict(id='a'*32,revision=2,components={},power_envelope=dict(min_v=5.,nominal_v=5.,max_v=5.,positive_pin=pin,ground_net_id='g',ambient_c=25.,available_current_ma=10.),power_sources={'source':dict(net_id='p',ground_net_id='g',boundary_pin=pin,nominal_v=5.)})
        self.report=dict(spec_sha256='s',revision=2,verdict='pass',backend_sha256='runtime',runs=[dict(engine='spice_op',scenario='on',models=[],electrical_reference=dict(stress_reference_complete=True,temperature_c=25.,sources=[dict(net_id='p',ground_net_id='g',min_v=5.,max_v=5.,min_current_a=0.,max_current_a=.001,pulse=None)],component_stress=[]))])
        self.solver_identity=dict(library='synthetic-test-engine',library_sha256='a'*64,dialect='native',code_models={})
        self.report['runs'][0]['solver']=dict(dialect='native',runtime_sha256=spice.runtime_hash(self.solver_identity))
    def tearDown(self):self.context.__exit__(None,None,None);self.temp.cleanup()
    def save(self):
        raw=registry.canonical(self.report);(self.run/'report.json').write_bytes(raw)
        (self.folder/'functional_index.json').write_text(json.dumps([dict(run_id='b'*32,report_sha256=registry.sha(raw))]))
    def check(self,fixed=None,replacement_evidence=None):
        with patch('pcb_mcp.parts.reference_catalog',return_value=fixed or []),patch('pcb_mcp.parts.rules_hash',return_value='runtime'),patch('pcb_mcp.model_power.spec_hash',return_value='s'),patch('pcb_mcp.spice.runtime_identity',return_value=self.solver_identity):
            return model_power.reference(self.s,replacement_evidence)
    def test_current_complete_reference_is_found(self):
        self.save();self.assertEqual(self.check()['rows'][0]['maximum_reference_a'],.001)
    def test_candidate_evidence_is_only_used_when_explicitly_supplied(self):
        self.report['replacement']=dict(component_id='c'*32)
        self.save()
        self.assertIsNone(self.check())
        evidence=(dict(run_id='b'*32,report_sha256='hash',scope='replacement'),self.report)
        self.assertEqual(self.check(replacement_evidence=evidence)['rows'][0]['maximum_reference_a'],.001)
        self.report['runs'][0]['electrical_reference']['sources'][0]['max_current_a']=.02
        self.assertEqual(self.check(replacement_evidence=evidence)['findings'][0]['code'],'BUDGET_SOURCE_CAPACITY')
    def test_group_candidate_evidence_is_only_used_when_explicitly_supplied(self):
        self.report['replacements']=[dict(component_id=key*32) for key in ('c','d')]
        self.save()
        self.assertIsNone(self.check())
        evidence=(dict(run_id='b'*32,report_sha256='hash',scope='replacement'),self.report)
        self.assertEqual(self.check(replacement_evidence=evidence)['rows'][0]['maximum_reference_a'],.001)
    def test_other_fixtures_cannot_inherit_regulated_supply_scope(self):
        row=self.report['runs'][0]['electrical_reference']['sources'][0]
        for field,value in [('pwl',[[0.,5.],[1.,5.]]),('sine',dict(offset=5.,amplitude=1.,frequency_hz=1.)),
                            ('series_resistance_ohm',1.),('series_connection',dict(control_pwl=[[0.,1.],[1.,0.]]))]:
            with self.subTest(field=field):
                row[field]=value;self.save();self.assertIsNone(self.check());del row[field]
    def test_generic_spice_reference_still_cannot_override_a_known_fixed_family(self):
        self.s['components']={'k':dict(library_id='Known:Fixed')};self.save();self.assertIsNone(self.check(fixed=[dict(symbol_id='Known:Fixed',kind='ic')]))
    def test_qualified_static_reference_keeps_sink_and_source_capacity_checks(self):
        from pcb_mcp import digital_power
        run=self.report['runs'][0];run.update(engine='digital_truth',solver=dict(dialect='digital_static',runtime_sha256=digital_power.runtime_hash()))
        run['electrical_reference'].update(stress_reference_complete=False,static_supply_reference_complete=True)
        run['electrical_reference']['sources'][0].update(min_current_a=-.001,max_current_a=.02)
        self.s['components']={'k':dict(library_id='Known:Fixed')};self.save()
        r=self.check(fixed=[dict(symbol_id='Known:Fixed',kind='ic')]);self.assertEqual({f['code'] for f in r['findings']},{'BUDGET_SOURCE_CAPACITY','MODEL_SOURCE_SINK_CAPACITY_UNKNOWN'})
    def test_current_drive_is_not_a_regulated_voltage_source(self):
        self.report['runs'][0]['electrical_reference']['sources'][0]['drive_kind']='current'
        self.save();self.assertIsNone(self.check())
    def test_stale_revision_is_not_used(self):
        self.report['revision']=1;self.save();self.assertIsNone(self.check())
    def test_stale_runtime_is_not_used(self):
        self.report['backend_sha256']='old';self.save();self.assertIsNone(self.check())
    def test_changed_solver_binary_is_not_used(self):
        self.save();self.solver_identity['library_sha256']='b'*64;self.assertIsNone(self.check())
    def test_missing_solver_identity_is_not_used(self):
        del self.report['runs'][0]['solver'];self.save();self.assertIsNone(self.check())
    def test_model_without_stress_coverage_is_not_used(self):
        self.report['runs'][0]['electrical_reference']['stress_reference_complete']=False;self.save();self.assertIsNone(self.check())
    def test_failed_requirement_is_not_used(self):
        self.report['verdict']='fail';self.save();self.assertIsNone(self.check())
    def test_changed_output_file_is_rejected(self):
        self.save();(self.run/'report.json').write_text('{}')
        with self.assertRaisesRegex(ValueError,'REPORT_CHANGED'):self.check()
    def test_nominal_model_cannot_prove_continuous_source_range(self):
        self.save();self.s['power_envelope']['min_v']=4.75;self.assertIsNone(self.check())
    def test_overcurrent_stays_error(self):
        self.s['power_envelope']['available_current_ma']=.1;self.save();self.assertEqual(self.check()['findings'][0]['code'],'BUDGET_SOURCE_CAPACITY')
    def test_reversed_current_requires_sink_capacity(self):
        self.report['runs'][0]['electrical_reference']['sources'][0]['min_current_a']=-.001;self.save();self.assertEqual(self.check()['findings'][0]['code'],'MODEL_SOURCE_SINK_CAPACITY_UNKNOWN')
    def startup(self):
        run=self.report['runs'][0];run['engine']='spice_transient'
        ref=run['electrical_reference'];ref['duration_s']=.3
        row=ref['sources'][0];row.update(min_v=0.,start_v=0.,end_v=5.,pulse=[0.,5.,1e-6,20e-6,20e-6,.5,1.])
        return ref,row
    def test_complete_single_startup_is_found(self):
        self.startup();self.save();self.assertEqual(self.check()['scenarios'],['on'])
    def test_startup_peak_is_not_averaged_away(self):
        _,row=self.startup();row['max_current_a']=1.;self.save()
        self.assertEqual(self.check()['findings'][0]['code'],'BUDGET_SOURCE_CAPACITY')
    def test_startup_reverse_current_stays_error(self):
        _,row=self.startup();row['min_current_a']=-.5;self.save()
        self.assertEqual(self.check()['findings'][0]['code'],'MODEL_SOURCE_SINK_CAPACITY_UNKNOWN')
    def test_repeated_or_incomplete_startup_is_not_regulated_supply(self):
        ref,row=self.startup()
        for field,value in [(6,.2),(5,.1),(3,0.),(1,4.9),(0,1.)]:
            with self.subTest(field=field):
                previous=row['pulse'][field];row['pulse'][field]=value;self.save()
                self.assertIsNone(self.check());row['pulse'][field]=previous
        for field,value in [('end_v',4.9),('max_v',5.1),('start_v',1.),('min_v',-.1),('drive_kind','current')]:
            with self.subTest(field=field):
                previous=row.get(field);row[field]=value;self.save();self.assertIsNone(self.check())
                if previous is None:del row[field]
                else:row[field]=previous
        ref['duration_s']=None;self.save();self.assertIsNone(self.check())
    def test_stale_solver_still_rejects_startup(self):
        self.startup();self.save();self.solver_identity['library_sha256']='c'*64;self.assertIsNone(self.check())
    def test_constant_transient_requires_complete_metadata(self):
        run=self.report['runs'][0];run['engine']='spice_transient';ref=run['electrical_reference']
        ref.update(duration_s=.3);ref['sources'][0].update(start_v=5.,end_v=5.)
        self.save();self.assertIsNotNone(self.check())
        del ref['sources'][0]['end_v'];self.save();self.assertIsNone(self.check())
    def test_explicit_sink_capacity_does_not_change_source_limit(self):
        _,row=self.startup();row.update(min_current_a=-.002,max_current_a=.011)
        self.s['power_envelope'].update(available_sink_current_ma=3.,sink_capacity_basis='Explicit synthetic fixture only')
        self.save();self.assertEqual([f['code'] for f in self.check()['findings']],['BUDGET_SOURCE_CAPACITY'])
    def test_sink_overload_and_missing_basis_stay_errors(self):
        _,row=self.startup();row['min_current_a']=-.002
        self.s['power_envelope'].update(available_sink_current_ma=1.,sink_capacity_basis='Explicit synthetic fixture only')
        self.save();self.assertEqual(self.check()['findings'][0]['code'],'BUDGET_SOURCE_SINK_CAPACITY')
        self.s['power_envelope']['sink_capacity_basis']=None;self.save()
        self.assertEqual(self.check()['findings'][0]['code'],'MODEL_SOURCE_SINK_CAPACITY_UNKNOWN')
    def test_sink_declaration_needs_basis_and_finite_capacity(self):
        from pcb_mcp.models import PowerEnvelope
        fields=dict(project_id='a'*32,expected_revision=0,idempotency_key='sink-test',positive_pin=dict(component_id='d'*32,pin_id='e'*32),ground_net_id='c'*32,nominal_v=5,min_v=5,max_v=5,available_current_ma=500,reason='Synthetic boundary')
        for extra in [dict(available_sink_current_ma=2),dict(sink_capacity_basis='Explicit fixture'),dict(available_sink_current_ma=float('nan'),sink_capacity_basis='Explicit fixture')]:
            with self.subTest(extra=extra),self.assertRaises(ValueError):PowerEnvelope.model_validate(dict(fields,**extra))
        self.assertEqual(PowerEnvelope.model_validate(dict(fields,available_sink_current_ma=2,sink_capacity_basis='Explicit fixture')).available_sink_current_ma,2)


if __name__=='__main__':unittest.main()
