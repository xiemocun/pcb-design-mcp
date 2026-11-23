import copy
import json
from pathlib import Path
import struct
import tempfile
import unittest

from pcb_mcp_client.transient import read_raw, verified_circuit, pulse_metrics, symbol_order

ROOT = Path(__file__).resolve().parents[3]
W = ROOT / 'work/pulse-load-startup'


class TransientTests(unittest.TestCase):
    def setUp(self):
        self.req=json.loads((W/'frozen-requirement.json').read_text())
        self.req['operation'].update(steady_state_after_power_on_s=.1, continuous_observation_s=.2)

    def wave(self, frequency=50, high=5, low=0, stop=.4):
        t=[i/100000 for i in range(int(stop*100000)+1)]
        v=[high if (x*frequency)%1 < .67 else low for x in t]
        # Begin low so startup and the first rise can be measured.
        v[0]=low
        return {'time':t,'v(pulse_out)':v}

    def test_continuous_pulse_and_plateau(self):
        r=pulse_metrics(self.wave(),self.req,5)
        self.assertEqual(r['verdict'],'pass')
        self.assertTrue(r['continuous_coverage'])
        self.assertAlmostEqual(r['frequency_hz']['min'],50,delta=.03)

    def test_wrong_frequency_fails(self):
        self.assertEqual(pulse_metrics(self.wave(frequency=30),self.req,5)['verdict'],'fail')

    def test_wrong_voltage_fails(self):
        self.assertEqual(pulse_metrics(self.wave(high=3.8),self.req,5)['verdict'],'fail')

    def test_stuck_output_fails(self):
        self.assertEqual(pulse_metrics(self.wave(high=0),self.req,5)['verdict'],'fail')

    def test_dropout_is_not_hidden_by_average(self):
        w=self.wave()
        for i,t in enumerate(w['time']):
            if .16<t<.20: w['v(pulse_out)'][i]=0
        self.assertEqual(pulse_metrics(w,self.req,5)['verdict'],'fail')

    def test_short_record_rejected(self):
        with self.assertRaisesRegex(ValueError,'WINDOW_INCOMPLETE'):
            pulse_metrics(self.wave(stop=.25),self.req,5)

    def test_sparse_record_rejected(self):
        w=self.wave();w={k:v[::100] for k,v in w.items()}
        with self.assertRaisesRegex(ValueError,'UNDERSAMPLED'):
            pulse_metrics(w,self.req,5)

    def raw(self, directory, row2=(.001,5.), cut=0):
        p=Path(directory)/'test.raw'
        header='Title: fixture\nPlotname: Transient Analysis\nFlags: real forward\nNo. Variables: 2\nNo. Points: 2\nVariables:\n\t0\ttime\ttime\n\t1\tV(out)\tvoltage\nBinary:\n'
        data=header.encode('utf-16-le')+struct.pack('<dfdf',0.,0.,*row2)
        p.write_bytes(data[:-cut] if cut else data)
        return p

    def test_raw_values(self):
        with tempfile.TemporaryDirectory() as d:
            w=read_raw(self.raw(d)); self.assertEqual(list(w['v(out)']),[0.,5.])

    def test_truncated_raw_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaisesRegex(ValueError,'TRUNCATED'):
                read_raw(self.raw(d,cut=1))

    def test_nonfinite_raw_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaisesRegex(ValueError,'NONFINITE'):
                read_raw(self.raw(d,row2=(.001,float('nan'))))

    def test_reversed_raw_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaisesRegex(ValueError,'TIME_ORDER'):
                read_raw(self.raw(d,row2=(-.001,5.)))

    def test_actual_netlist_and_changed_pin(self):
        state=json.loads((ROOT/'work/pulse-ground-plane/final-run/state.json').read_text())
        verified_circuit(state,W/'actual-fresh.xml')
        bad=copy.deepcopy(state)
        bad['nets'][next(iter(bad['nets']))]['name']='WRONG_NET'
        with self.assertRaisesRegex(ValueError,'CONNECTION_MISMATCH'):
            verified_circuit(bad,W/'actual-fresh.xml')

    def test_changed_component_value(self):
        state=json.loads((ROOT/'work/pulse-ground-plane/final-run/state.json').read_text())
        next(c for c in state['components'].values() if c['ref']=='R1')['value']='220k'
        with self.assertRaisesRegex(ValueError,'COMPONENT_MISMATCH'):
            verified_circuit(state,W/'actual-fresh.xml')

    def test_missing_model_order_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'x.asy'; p.write_text('PIN 0 0 LEFT 8\nPINATTR PinName V+\n')
            with self.assertRaisesRegex(ValueError,'ORDER_MISSING'):
                symbol_order(p)


if __name__=='__main__': unittest.main()
