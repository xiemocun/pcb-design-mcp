"""Environmental stimulus adaptation preserves source and actual connections."""
from pathlib import Path
import tempfile,unittest
import numpy as np
from pcb_mcp.spice_temperature import adapt
from pcb_mcp.temperature_resistance import TemperatureProfile
from pcb_mcp.functional import Scenario
from pcb_mcp import spice


class BodyTemperature(unittest.TestCase):
    def test_absent_profile_preserves_previous_scenario_serialization(self):
        dumped=Scenario(id='old',engine='spice_op').model_dump(mode='json')
        self.assertNotIn('body_temperature_profiles',dumped)

    def test_unsupported_temperature_use_and_out_of_scope_are_rejected(self):
        p=TemperatureProfile(points=[[0.,0.],[1.,60.]])
        for lines in (['.subckt a 1 2','R1 1 2 {TEMP}','.ends'],
                      ['.subckt a 1 2','R1 1 2 1k','.ends'],
                      ['.subckt a 1 2','X1 1 2 missing','.ends'],
                      ['.subckt a 1 2','E1 1 2 VALUE={1} TEMP','.ends']):
            with self.subTest(lines=lines),self.assertRaises(ValueError):adapt(lines,'a',1,p,[0.,60.],'native')
        lines=['.subckt a 1 2','E1 1 2 VALUE={TEMP}','.ends']
        with self.assertRaisesRegex(ValueError,'OUTSIDE_SCOPE'):adapt(lines,'a',1,p,[10.,50.],'native')

    @unittest.skipUnless(Path('/Applications/KiCad/KiCad.app/Contents/Frameworks/libngspice.0.dylib').is_file(),'Native solver unavailable')
    def test_two_independent_instances_nested_model_and_global_process_temperature(self):
        original=['.subckt root out gnd','X1 out gnd inner','.ends root',
                  '.subckt inner out gnd','E1 out gnd VALUE={0.5 + TEMP*0.01}','.ends inner']
        profiles=[TemperatureProfile(points=[[0.,0.],[.002,60.],[.004,0.]]),
                  TemperatureProfile(points=[[0.,40.],[.002,10.],[.004,40.]])]
        deck=['Independent body temperature references']
        for index,p in enumerate(profiles,1):
            lines,entry,node,source,count=adapt(original,'root',index,p,[0.,60.],'native')
            self.assertEqual(count,1);deck.extend([source,f'X{index} out{index} 0 {node} {entry}',*lines])
        self.assertIn('TEMP',original[-2]);deck.extend(['.temp 25','.tran 2u 4m 0 2u','.end'])
        with tempfile.TemporaryDirectory() as d:
            _,w=spice.run('\n'.join(deck)+'\n',['time','v(out1)','v(out2)'],Path(d)/'run')
            for index,p in enumerate(profiles,1):
                t=np.interp(w['time'],[a for a,b in p.points],[b for a,b in p.points])
                np.testing.assert_allclose(w[f'v(out{index})'],.5+.01*t,rtol=1e-9,atol=1e-12)


if __name__=='__main__':unittest.main()
