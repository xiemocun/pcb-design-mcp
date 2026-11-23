"""Explicit model dialect and native switch checks against a resistor divider."""
from pathlib import Path
import tempfile
import unittest
import numpy as np
from pcb_mcp import spice, spice_bundle


class DialectData(unittest.TestCase):
    def test_vendor_comment_encoding_preserves_ascii_cards(self):
        raw=b'* Manufacturer model \x96 revision 1\nR1 a b 1000\n'
        self.assertEqual(spice.model_lines(spice.model_text(raw)),['R1 a b 1000'])
        with self.assertRaisesRegex(ValueError,'NONASCII_MODEL_CARD'):
            spice.model_text(b'R1 a b \x961000')

    def test_vendor_local_initial_conditions_and_end_comments(self):
        data='.subckt sample a b params: VSS=0\n; descriptive comment\nR1 a b 1k ; output resistance\n.ic V(q) {VSS} V(a)=0\n.ends'
        self.assertIn('.ic V(q) {VSS} V(a)=0',spice.model_lines(data,'ltspice'))
        for bad in ('.ic V(q)=0','.subckt x a\n.ic V(q)=file(x)\n.ends',
                    '.subckt x a\n.ic V(q) {VSS+1}\n.ends',
                    'R1 a b 1k ; shell bad','R1 a b 1k ; `bad`'):
            with self.subTest(bad=bad),self.assertRaises(ValueError):spice.model_lines(bad,'ltspice')

    def test_builtin_voltage_and_current_switch_cards_are_data(self):
        self.assertEqual(len(spice.model_lines('.subckt sample a b c d\nS1 a b c d sw\nW1 a b VS sw\n.ends sample')),4)

    def test_unqualified_xor_and_unknown_modes_rejected(self):
        with self.assertRaisesRegex(ValueError,'XOR_REQUIRES'):
            spice.model_lines('B1 a 0 v={v(b)^v(c)}',dialect='pspice')
        with self.assertRaisesRegex(ValueError,'DIALECT_UNSUPPORTED'):spice.runtime_identity('arbitrary commands')

    def test_pspice_voltage_comparison_xor_in_ltspice(self):
        raw = b'E1 out 0 VALUE={IF(V(a) > .5 ^\n+ V(b) > {threshold}, 1, 0)}\n'
        ltspice = {'spice_dialect': 'ltspice'}
        with self.assertRaisesRegex(ValueError, 'SOURCE_DIALECT_REQUIRED'):
            spice_bundle.source_cards(raw, ltspice)
        pspice_source = dict(ltspice, source_spice_dialect='pspice')
        self.assertEqual(spice_bundle.source_cards(raw, pspice_source), (
            ['E1 out 0 VALUE={IF(XOR((V(a) > .5),(V(b) > {threshold})), 1, 0)}'], 1
        ))
        other = b'E1 out 0 VALUE={V(a)^2}\n'
        self.assertEqual(spice_bundle.source_cards(other, ltspice), ([other.decode().strip()], 0))
        with self.assertRaisesRegex(ValueError, 'XOR_REQUIRES_VERIFIED_ADAPTER'):
            spice_bundle.source_cards(other, pspice_source)

    def test_external_code_stays_forbidden(self):
        for text in ('.control\nshell bad\n.endc','.include /tmp/model','A1 a 0 external','.osdi /tmp/model','.load bad','load bad','B1 a 0 V={load(bad)}'):
            with self.assertRaises(ValueError):spice.model_lines(text,dialect='pspice')
        self.assertEqual(spice.model_lines('X1 IN OUT LOAD 0 sense'),['X1 IN OUT LOAD 0 sense'])

    def test_transmission_delay_does_not_enable_commands_or_external_models(self):
        for data in ('T1 a 0 b 0 Z0=50 TD=39n\n.control\nshell bad\n.endc',
                     'T1 a 0 b 0 Z0=50 TD=file(/tmp/delay)',
                     'T1 a 0 b 0 Z0=50 TD=39n; shell bad',
                     'T1 a 0 b 0 Z0=50 TD=39n\nA1 a 0 external'):
            with self.subTest(data=data),self.assertRaises(ValueError):
                spice.model_lines(data,dialect='pspice')

    def test_subcircuit_parameters_do_not_enable_arbitrary_colons(self):
        data='.subckt parameterized a k params: r=1000\nR1 a k {r}\n.ends'
        self.assertEqual(spice.model_lines(data)[0],'.subckt parameterized a k params: r=1000')
        self.assertEqual(spice.model_lines('X1 a b parameterized PARAMS: r=1000')[0],'X1 a b parameterized PARAMS: r=1000')
        self.assertEqual(spice.model_lines('+ PARAMS: r=1000')[0],'+ PARAMS: r=1000')
        for bad in ['.include params:bad','.subckt bad a k params: r=C:bad','R1 a k C:bad']:
            with self.assertRaises(ValueError):spice.model_lines(bad)


@unittest.skipUnless(Path('/Applications/KiCad/KiCad.app/Contents/PlugIns/sim/ngspice/analog.cm').is_file(),'Native standard ngspice models unavailable')
class NativePSpice(unittest.TestCase):
    def test_matched_transmission_line_preserves_measured_propagation_delay(self):
        model='\n'.join(spice.model_lines('.subckt delay a b\nT1 a 0 b 0 Z0=50 TD=39n\n.ends delay',dialect='pspice'))
        deck=('Matched lossless line timing reference\n'
              'V1 s 0 PULSE(0 1 50n 1n 1n 100n 300n)\n'
              'R1 s a 50\nX1 a b delay\nR2 b 0 50\n'+model+
              '\n.tran .1n 250n 0 .1n\n.end\n')
        with tempfile.TemporaryDirectory() as d:
            _,waves=spice.run(deck,['time','v(s)','v(b)'],Path(d)/'delay',dialect='pspice')
            def rising(values,threshold):
                indices=np.flatnonzero((values[:-1]<threshold)&(values[1:]>=threshold))
                self.assertEqual(len(indices),1)
                i=indices[0];t=waves['time']
                return t[i]+(threshold-values[i])*(t[i+1]-t[i])/(values[i+1]-values[i])
            self.assertAlmostEqual(rising(waves['v(b)'],.25)-rising(waves['v(s)'],.5),39e-9,delta=.15e-9)
            self.assertAlmostEqual(float(np.interp(120e-9,waves['time'],waves['v(b)'])),.5,delta=1e-6)

    def test_vswitch_on_off_against_independent_resistor_solution(self):
        with tempfile.TemporaryDirectory(prefix='pcb pspice spaces ') as d:
            for control,resistance in [(0.,1e9),(1.,1.)]:
                deck=('Switch compatibility reference\nV1 s 0 1\nVC c 0 '+str(control)+
                      '\nR1 s o 1000\nS1 o 0 c 0 SW\n.model SW VSWITCH(Ron=1 Roff=1e9 Von=.8 Voff=.2)\n.op\n.end\n')
                meta,waves=spice.run(deck,['v(o)'],Path(d)/str(control),dialect='pspice')
                self.assertEqual(meta['dialect'],'pspice');self.assertTrue(meta['code_models'])
                self.assertAlmostEqual(float(waves['v(o)'][0]),resistance/(resistance+1000),delta=1e-8)


if __name__=='__main__':unittest.main()
