"""Current-source units and native polarity against independent Norton equations."""
from pathlib import Path
import tempfile
import unittest
import numpy as np
from pcb_mcp.functional import Drive, drive_lines, Scenario, FunctionalContract
from pcb_mcp import spice


class SourceData(unittest.TestCase):
    def source(self,**kwargs):
        return Drive(positive=dict(ref='J1',number='1'),negative=dict(ref='J1',number='2'),**kwargs)

    def test_explicit_source_units_cannot_be_mixed(self):
        for data in [dict(dc_a=.001),dict(kind='current',dc_v=1.),dict(kind='current',dc_a=.001,ac_v=1.),dict(dc_v=1.,ac_a=.001)]:
            with self.subTest(data=data),self.assertRaises(ValueError):self.source(**data)

    def test_current_cannot_enter_a_truth_table_engine(self):
        with self.assertRaisesRegex(ValueError,'CURRENT_DRIVE_REQUIRES_SPICE'):
            Scenario(id='case',engine='digital_truth',drives=[self.source(kind='current',dc_a=1e-6)])

    def test_existing_voltage_drive_keeps_its_source(self):
        self.assertEqual(drive_lines(self.source(dc_v=3.3),0,'p','0'),['Vdrive0 p 0 DC 3.3'])

    def test_ac_voltage_requires_explicit_component(self):
        raw=dict(description='Explicit AC voltage measurement convention',requirements=[dict(id='v',description='AC output amplitude under explicit excitation',positive=dict(ref='J1',number='1'),negative=dict(ref='J1',number='2'),quantity='voltage',minimum=0.,maximum=1.,scenarios=['ac'])],scenarios=[dict(id='ac',engine='spice_ac',frequency_hz=[100.,1000.],drives=[self.source(dc_v=0.,ac_v=1.).model_dump()])])
        with self.assertRaisesRegex(ValueError,'AC_VOLTAGE_COMPONENT_REQUIRED'):FunctionalContract.model_validate(raw)
        raw['requirements'][0]['ac_measurement']='magnitude'
        self.assertEqual(FunctionalContract.model_validate(raw).requirements[0].ac_measurement,'magnitude')

    def test_complex_components_keep_their_explicit_signs(self):
        from pcb_mcp.measurements import ac_component
        values=np.array([3+4j,-3-4j])
        np.testing.assert_array_equal(ac_component(values,'magnitude'),[5.,5.])
        np.testing.assert_array_equal(ac_component(values,'real'),[3.,-3.])
        np.testing.assert_array_equal(ac_component(values,'imaginary'),[4.,-4.])


@unittest.skipUnless(Path('/Applications/KiCad/KiCad.app/Contents/Frameworks/libngspice.0.dylib').is_file(),'Native ngspice unavailable')
class NativeCurrent(unittest.TestCase):
    source=SourceData.source
    def test_dc_voltage_and_probe_sign_follow_kcl(self):
        with tempfile.TemporaryDirectory() as directory:
            for index,current in enumerate([1e-6,-2e-6]):
                lines=drive_lines(self.source(kind='current',dc_a=current),0,'p','0')
                deck='Norton reference\n'+'\n'.join(lines)+'\nR1 p 0 10000\n.op\n.end\n'
                _,waves=spice.run(deck,['v(p)','i(vdrive0)'],Path(directory)/str(index))
                self.assertAlmostEqual(float(waves['v(p)'][0]),-current*10000,delta=1e-10)
                self.assertAlmostEqual(float(waves['i(vdrive0)'][0]),current,delta=1e-12)

    def test_ac_current_source_respects_rc_impedance(self):
        with tempfile.TemporaryDirectory() as directory:
            lines=drive_lines(self.source(kind='current',dc_a=0.,ac_a=1e-6),0,'p','0')
            deck='AC Norton reference\n'+'\n'.join(lines)+'\nR1 p 0 10000\nC1 p 0 1e-7\n.ac lin 3 100 1000\n.end\n'
            _,waves=spice.run(deck,['frequency','v(p)'],Path(directory)/'run')
            expected=-1e-6/(1/10000+2j*np.pi*waves['frequency']*1e-7)
            np.testing.assert_allclose(waves['v(p)'],expected,rtol=1e-8,atol=1e-12)


if __name__=='__main__':unittest.main()
