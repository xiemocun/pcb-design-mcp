"""Strict vendor result parsing, model identity and external fixture boundaries."""
from pathlib import Path
import tempfile
import json
import time
import unittest
from unittest.mock import patch
import numpy as np
from pcb_mcp import ltspice,spice
from pcb_mcp.functional import BoundaryLoad,Scenario,compile_spice


class RawData(unittest.TestCase):
    def parse(self,values='0\t2.5\n\t-0.0025\n',flags='real',points=1):
        text=f'Title: fixture\nPlotname: Operating Point\nFlags: {flags}\nNo. Variables: 2\nNo. Points: {points}\nVariables:\n0 V(out) voltage\n1 I(V1) current\nValues:\n'+values
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'r.raw';p.write_bytes(text.replace('\n','\r\n').encode());return ltspice.read_ascii_raw(p)

    def test_actual_real_format_retains_current_sign(self):
        waves,meta=self.parse();self.assertEqual(meta['points'],1)
        self.assertEqual(waves['v(out)'][0],2.5);self.assertEqual(waves['i(v1)'][0],-.0025)

    def test_complex_format_retains_signed_phase(self):
        waves,_=self.parse('0\t3,4\n\t-3,-4\n',flags='complex')
        np.testing.assert_array_equal(waves['v(out)'],[3+4j]);np.testing.assert_array_equal(waves['i(v1)'],[-3-4j])

    def test_truncation_wrong_indices_and_nan_rejected(self):
        for values,points in [('0\t2.5\n',1),('1\t2.5\n\t-.0025\n',1),('0\tnan\n\t0\n',1),('0\t2.5\n\t0\n',2)]:
            with self.subTest(values=values),self.assertRaises(ValueError):self.parse(values,points=points)

    def test_large_valid_shape_still_detects_missing_rows(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'truncated.raw'
            fields = ''.join(f'{i} V(n{i}) voltage\n' for i in range(55))
            header = ('Title: fixture\nPlotname: Transient Analysis\nFlags: real\n'
                      'No. Variables: 55\nNo. Points: 109535\nVariables:\n')
            path.write_text(header + fields + 'Values:\n')
            with self.assertRaisesRegex(ValueError, 'LTSPICE_RAW_INCOMPLETE'):
                ltspice.read_ascii_raw(path)
            path.write_text(header.replace('109535', '500000') + fields + 'Values:\n')
            with self.assertRaisesRegex(ValueError, 'LTSPICE_RAW_DIMENSIONS_INVALID'):
                ltspice.read_ascii_raw(path)

    def test_library_paths_cannot_escape_or_name_commands(self):
        with tempfile.TemporaryDirectory() as d:
            for value in ['../model.sub','/tmp/model.sub','sub/model;bad.sub','sub\\model.sub']:
                with self.subTest(value=value),self.assertRaises(ValueError):ltspice.library_file(Path(d),value)

    def test_missing_runtime_is_explicit(self):
        with patch.dict('os.environ',{},clear=True),self.assertRaisesRegex(ValueError,'NOT_CONFIGURED'):ltspice.paths()

    def test_binary_and_original_symbol_identity_remain_bound(self):
        with tempfile.TemporaryDirectory() as d:
            library=Path(d);(library/'sub').mkdir();(library/'sym').mkdir()
            model=library/'sub/vendor.sub';model.write_bytes(b'\r\n<Binary File>\r\nsynthetic parser fixture; never executed')
            symbol=library/'sym/vendor.asy';symbol.write_text('SYMATTR SpiceModel vendor-1.0\nSYMATTR ModelFile vendor.sub\nPIN 0 0 NONE 0\nPINATTR PinName 4\nPINATTR SpiceOrder 1\nPIN 0 1 NONE 0\nPINATTR PinName 2\nPINATTR SpiceOrder 2\n')
            spec=dict(model_relative_path='sub/vendor.sub',model_sha256=ltspice.sha(model),symbol_relative_path='sym/vendor.asy',symbol_sha256=ltspice.sha(symbol))
            with patch('pcb_mcp.ltspice.paths',return_value=(library,library,library,library,library)):
                self.assertEqual(ltspice.resolve_model(spec,'vendor-1.0',['4','2']),model)
                with self.assertRaisesRegex(ValueError,'PORT_COUNT'):ltspice.resolve_model(spec,'vendor-1.0',['4'])
                with self.assertRaisesRegex(ValueError,'IDENTITY_MISMATCH'):ltspice.resolve_model(spec,'wrong-model',['4','2'])
                with self.assertRaisesRegex(ValueError,'PIN_ORDER_MISMATCH'):ltspice.resolve_model(spec,'vendor-1.0',['2','4'])
                model.write_bytes(model.read_bytes()+b'changed')
                with self.assertRaisesRegex(ValueError,'MODEL_CHANGED'):ltspice.resolve_model(spec,'vendor-1.0',['4','2'])

    def test_timeout_stops_owned_child_group_and_retains_failure(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);launcher=root/'fake-engine';launcher.write_text('#!/bin/sh\nsleep 30 &\nprintf "partial solver progress\\n" > circuit.log\nwait\n');launcher.chmod(0o700)
            import subprocess,signal,os
            real_popen=subprocess.Popen;ready={}
            def launched(*args,**kwargs):
                process=real_popen(*args,**kwargs);deadline=time.monotonic()+5.
                progress=Path(kwargs['cwd'])/'circuit.log'
                while not (progress.is_file() and progress.read_text()=='partial solver progress\n'):
                    if process.poll() is not None or time.monotonic()>deadline:
                        if process.poll() is None:os.killpg(process.pid,signal.SIGKILL)
                        process.wait();raise AssertionError('Synthetic engine did not produce progress before timeout test')
                    time.sleep(.01)
                ready['at']=time.monotonic();return process
            # Make producer readiness explicit. Under CPU load a shell need not
            # start within0.3s; absence of unwritten output is not retention loss.
            with patch('pcb_mcp.ltspice.runtime_identity',return_value={}),patch('pcb_mcp.ltspice.paths',return_value=(root,root,root,root,launcher)),patch('pcb_mcp.ltspice.subprocess.Popen',side_effect=launched),self.assertRaisesRegex(ValueError,'LTSPICE_TIMEOUT'):
                ltspice.run('Reference\n.op\n.end\n',['v(out)'],root/'run',timeout=.3)
            self.assertLess(time.monotonic()-ready['at'],2)
            self.assertEqual(json.loads((root/'run/process.json').read_text())['status'],'timeout')
            self.assertEqual((root/'run/circuit.log').read_text(),'partial solver progress\n')

    def test_long_unicode_retained_path_uses_short_vendor_workspace(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);launcher=root/'fake-engine'
            raw='Title: fixture\nPlotname: Operating Point\nFlags: real\nNo. Variables: 1\nNo. Points: 1\nVariables:\n0 V(out) voltage\nValues:\n0 2.5\n'
            launcher.write_text('#!/bin/sh\n[ ${#PWD} -lt 200 ] || exit 17\nprintf "reference fixture\\n" > circuit.log\ncat > circuit.raw <<\'EOF\'\n'+raw+'EOF\n');launcher.chmod(0o700)
            retained=root/('用户 folder '+ 'a'*65)/('nested folder '+ 'b'*65)/('last folder '+ 'c'*65)/'run'
            self.assertGreater(len(str(retained)),260)
            with patch('pcb_mcp.ltspice.runtime_identity',return_value={}),patch('pcb_mcp.ltspice.paths',return_value=(root,root,root,root,launcher)):
                # This case checks path handling, not sub-second process startup.
                # The separate timeout test still enforces its0.3s execution cap.
                _,waves=ltspice.run('Reference\n.op\n.end\n',['v(out)'],retained,timeout=5)
            self.assertEqual(waves['v(out)'][0],2.5);self.assertTrue((retained/'circuit.raw').is_file())
            process=json.loads((retained/'process.json').read_text());self.assertEqual(process['command'][-1],'circuit.cir')
            self.assertFalse(Path(process['command'][2]).parent.exists())

    def test_broker_timeout_reaps_exited_wrapper_before_group_signal(self):
        from unittest.mock import Mock
        import subprocess
        proc=Mock();proc.poll.return_value=0
        proc.communicate.side_effect=[subprocess.TimeoutExpired('fixture',.2),('',''),('','')]
        with patch('pcb_mcp.ltspice.os.killpg') as group,patch('pcb_mcp_client.process_job.stop_marked',return_value=dict(verified_stopped=True,signals=[],survivors=[])) as marked:
            _,_,cleanup=ltspice.stop_timeout(proc,'/private/tmp/unique/circuit.cir')
        group.assert_not_called();marked.assert_called_once();self.assertTrue(cleanup['verified_stopped'])

    def test_vendor_utf16_error_is_readable_without_bom(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'log';path.write_bytes('Could not open input deck'.encode('utf-16-le'))
            self.assertEqual(ltspice.diagnostic(path),'Could not open input deck')


class BoundaryData(unittest.TestCase):
    def ports(self):return dict(positive=dict(ref='J1',number='1'),negative=dict(ref='J1',number='2'))

    def test_fixture_kind_and_initial_voltage_are_explicit(self):
        for extra in [{},dict(resistance_ohm=10,capacitance_f=.1),dict(resistance_ohm=10,initial_v=3)]:
            with self.subTest(extra=extra),self.assertRaises(ValueError):BoundaryLoad(**self.ports(),**extra)

    def test_initial_capacitor_only_enters_transient(self):
        cap=BoundaryLoad(**self.ports(),capacitance_f=.01,initial_v=3.)
        with self.assertRaisesRegex(ValueError,'REQUIRES_TRANSIENT'):Scenario(id='dc',engine='spice_op',boundary_loads=[cap])

    def test_inductive_fixture_requires_finite_series_resistance(self):
        for extra in [dict(inductance_h=.1),dict(capacitance_f=.01,inductance_h=.1)]:
            with self.assertRaises(ValueError):BoundaryLoad(**self.ports(),**extra)
        self.assertEqual(BoundaryLoad(**self.ports(),resistance_ohm=220,inductance_h=.1).inductance_h,.1)

    def test_internal_node_cannot_be_used_as_external_load(self):
        state=dict(id='test',nets={'g':dict(id='g',pins=[dict(component_id='j')]),'p':dict(id='p',pins=[dict(component_id='j')]),'internal':dict(id='internal',pins=[])},components={'j':dict(ref='J1',library_id='Connector_Generic:Conn_01x02')})
        scenario=Scenario(id='t',engine='spice_transient',stop_s=1,step_s=.01,drives=[dict(**self.ports(),dc_v=5)],boundary_loads=[dict(positive=dict(ref='U1',number='1'),negative=dict(ref='J1',number='2'),capacitance_f=.01,initial_v=3)])
        mapping={('J1','1'):'p',('J1','2'):'g',('U1','1'):'internal'}
        with patch('pcb_mcp.functional.terminals',return_value=mapping),self.assertRaisesRegex(ValueError,'DISTINCT_EXTERNAL_BOUNDARY'):
            compile_spice(state,scenario,{}, {'J1':dict(kind='connector',facts={})})
        scenario.boundary_loads[0].positive.ref='J1'
        with patch('pcb_mcp.functional.terminals',return_value=mapping):
            deck,*_=compile_spice(state,scenario,{}, {'J1':dict(kind='connector',facts={})})
        self.assertIn('Cfixture0 n3 0 0.01',deck)
        self.assertIn('.ic V(n3)=3',deck)
        self.assertIn('.tran 0.01 1 0 0.01',deck)
        self.assertNotIn('uic',deck)

    def test_operating_point_hint_compiles_only_for_an_internal_net(self):
        state = dict(id='test', nets={
            'g': dict(id='g', pins=[dict(component_id='j')]),
            'p': dict(id='p', pins=[dict(component_id='j')]),
            'inside': dict(id='inside', pins=[]),
        }, components={'j': dict(ref='J1', library_id='Connector_Generic:Conn_01x02')})
        mapping = {('J1', '1'): 'p', ('J1', '2'): 'g', ('U1', '4'): 'inside'}
        records = {'J1': dict(kind='connector', facts={})}
        models = {'U1': (dict(engine='spice_subcircuit', spice_dialect='ltspice'), None)}
        scenario = Scenario(id='startup', engine='spice_transient', stop_s=1, step_s=.01,
                            drives=[dict(**self.ports(), dc_v=5)],
                            operating_point_hints=[dict(terminal=dict(ref='U1', number='4'), guess_v=.39)])
        with patch('pcb_mcp.functional.terminals', return_value=mapping):
            deck, *_ = compile_spice(state, scenario, models, records)
            self.assertIn('.nodeset V(n2)=0.39', deck)
            self.assertNotIn('.ic', deck)
            scenario.operating_point_hints[0].terminal.ref = 'J1'
            scenario.operating_point_hints[0].terminal.number = '1'
            with self.assertRaisesRegex(ValueError, 'OPERATING_POINT_HINT_REQUIRES_INTERNAL_NET'):
                compile_spice(state, scenario, models, records)


@unittest.skipUnless(Path('/Applications/KiCad/KiCad.app/Contents/Frameworks/libngspice.0.dylib').is_file(),'Native ngspice unavailable')
class NativeFixture(unittest.TestCase):
    def test_rl_current_matches_finite_ramp_closed_form(self):
        with tempfile.TemporaryDirectory() as d:
            deck='Independent external RL reference\nVdrive0 p 0 PULSE(0 12 .001 .000001 .000001 .005 .01)\nRfixture0 p internal 220\nLfixture0 internal 0 .1\n.tran .000001 .004 0 .000001\n.end\n'
            _,waves=spice.run(deck,['time','i(vdrive0)'],Path(d)/'run')
            t=waves['time'];tau=.1/220;rise=1e-6;sel=t>=.001+rise
            expected=12/220*(1-tau/rise*np.expm1(rise/tau)*np.exp(-(t[sel]-.001)/tau))
            np.testing.assert_allclose(-waves['i(vdrive0)'][sel],expected,atol=3e-7,rtol=1e-4)

    def test_constrained_initial_point_charges_parallel_board_capacitor(self):
        with tempfile.TemporaryDirectory() as d:
            deck='Independent consistent initial state\nR1 out 0 1000\nCboard out 0 1u\nCfixture out 0 .01\n.ic V(out)=3\n.tran .0001 .01 0 .0001\n.end\n'
            _,waves=spice.run(deck,['time','v(out)'],Path(d)/'run')
            np.testing.assert_allclose(waves['v(out)'],3*np.exp(-waves['time']/(1000*.010001)),rtol=1e-7,atol=1e-8)


if __name__=='__main__':unittest.main()
