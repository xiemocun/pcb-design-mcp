"""A single-unit source model must cover the real multi-unit package."""
from pathlib import Path
import tempfile,unittest
from pcb_mcp.verification_models import VerificationModel
from pcb_mcp.functional import Scenario,compile_spice
from pcb_mcp import spice
from test_verification_models import fixture


class PhysicalUnits(unittest.TestCase):
    def test_mapping_cannot_omit_or_add_a_physical_pin_or_duplicate_unit(self):
        raw=fixture();raw.update(pins=['1','2','3'],spice_units=[['1','2'],['2','3']])
        VerificationModel.model_validate(raw)
        for units in ([['1','2']],[['1','2'],['2','4']],[['1','2'],['2','3','1']],[['1','2'],['1','2']]):
            with self.subTest(units=units),self.assertRaises(ValueError):
                VerificationModel.model_validate(dict(raw,spice_units=units))
        with self.assertRaisesRegex(ValueError,'REQUIRE_SUBCIRCUIT'):
            VerificationModel.model_validate(dict(raw,engine='spice_device',device='D'))

    @unittest.skipUnless(Path('/Applications/KiCad/KiCad.app/Contents/Frameworks/libngspice.0.dylib').is_file(),'Native solver unavailable')
    def test_native_two_units_follow_distinct_actual_package_connections(self):
        def component(ref,numbers,library):return dict(id=ref,ref=ref,library_id=library,pins=[dict(number=n,pin_id=n) for n in numbers])
        s=dict(id='two_units',components={
            'J1':component('J1',['1','2','3'],'Connector_Generic:Conn_01x03'),
            'U1':component('U1',['1','2','3','4','5'],'EXT:TwoUnits'),
            'R1':component('R1',['1','2'],'Device:R'),'R2':component('R2',['1','2'],'Device:R')},nets={},pin_intents={})
        for name,ps in [('a',[('J1','1'),('U1','1')]),('b',[('J1','2'),('U1','3')]),
            ('outa',[('U1','2'),('R1','1')]),('outb',[('U1','4'),('R2','1')]),
            ('gnd',[('J1','3'),('U1','5'),('R1','2'),('R2','2')])]:
            s['nets'][name]=dict(id=name,pins=[dict(component_id=r,pin_id=n) for r,n in ps])
        records={'J1':dict(kind='connector',facts={})}
        for ref in ('R1','R2'):records[ref]=dict(kind='resistor',facts=dict(value_ohm=1000.),definition_hash='r'*64)
        records['U1']=dict(kind='amplifier',facts={},definition_hash='a'*64)
        scenario=Scenario(id='units',engine='spice_op',drives=[dict(positive=dict(ref='J1',number=n),negative=dict(ref='J1',number='3'),dc_v=v) for n,v in [('1',1.),('2',2.)]])
        with tempfile.TemporaryDirectory() as d:
            folder=Path(d)/('f'*64);folder.mkdir();(folder/'model.lib').write_text('.subckt unit input output ground\nE1 output ground input ground 2\n.ends unit\n')
            model=dict(name='unit',engine='spice_subcircuit',pins=['1','2','3','4','5'],excluded_pins={},
                spice_units=[['1','2','5'],['3','4','5']],spice_dialect='native',model_asset='model.lib',temperature_range_c=[0.,60.],conditions='Synthetic two-unit electrical mapping fixture only.')
            deck,names,evidence,_,_=compile_spice(s,scenario,{'U1':(model,folder)},records)
            _,w=spice.run(deck,['v('+names[n]+')' for n in ('outa','outb')],Path(d)/'run')
            self.assertAlmostEqual(w['v('+names['outa']+')'][0],2.)
            self.assertAlmostEqual(w['v('+names['outb']+')'][0],4.)
            self.assertEqual(next(row for row in evidence if row.get('reference')=='U1')['physical_unit_mapping'],model['spice_units'])
            model['excluded_pins']={'4':'The second physical output is deliberately unconnected.'};model['pins'].remove('4')
            with self.assertRaisesRegex(ValueError,'EXCLUDED_PIN_NOT_NC'):compile_spice(s,scenario,{'U1':(model,folder)},records)


if __name__=='__main__':unittest.main()
