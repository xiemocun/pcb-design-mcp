"""Multilayer geometry contracts and independently calculated copper references."""
import copy
import tempfile
from pathlib import Path
import unittest

from pcb_mcp.models import BoardConstraints
from pcb_mcp.stackup import copper_layers, fabrication_layers, apply, inspect, inspect_gerbers
from pcb_mcp.explicit_placement import silkscreen_position, validate
from pcb_mcp.copper import reference_current, layer_thicknesses
from pcb_mcp.power_paths import selected_path


def four_layer():
    return dict(layers=4, thickness_mm=1.6, stackup=dict(
        basis='Synthetic geometry fixture, not a manufacturer construction specification.',
        copper=[dict(name=name, thickness_um=thickness) for name, thickness in
                [('F.Cu', 35.), ('In1.Cu', 18.), ('In2.Cu', 18.), ('B.Cu', 35.)]],
        dielectrics=[dict(kind=kind, thickness_mm=thickness, material='Synthetic FR4',
                          epsilon_r=4.2, loss_tangent=.02)
                     for kind, thickness in [('prepreg', .2), ('core', 1.094), ('prepreg', .2)]]))


class StackupTests(unittest.TestCase):
    def board(self, **changes):
        args=dict(project_id='a'*32, expected_revision=0, idempotency_key='stackup-test', **four_layer())
        args.update(changes)
        return BoardConstraints.model_validate(args)

    def test_four_layer_requires_construction(self):
        with self.assertRaisesRegex(ValueError, 'FOUR_LAYER_STACKUP_REQUIRED'):
            self.board(stackup=None)

    def test_wrong_order_and_missing_dielectric_rejected(self):
        for key in ('copper', 'dielectrics'):
            stack=four_layer()['stackup']
            if key=='copper':stack[key][1],stack[key][2]=stack[key][2],stack[key][1]
            else:stack[key].pop()
            with self.assertRaises(ValueError):self.board(stackup=stack)

    def test_inconsistent_thickness_rejected(self):
        with self.assertRaisesRegex(ValueError, 'STACKUP_TOTAL_THICKNESS_MISMATCH'):
            self.board(thickness_mm=1.2)

    def test_nonfinite_material_and_copper_parameters_rejected(self):
        for key in ('epsilon_r', 'thickness_mm', 'loss_tangent'):
            stack=four_layer()['stackup'];stack['dielectrics'][0][key]=float('nan')
            with self.assertRaises(ValueError):self.board(stackup=stack)
        stack=four_layer()['stackup'];stack['copper'][0]['thickness_um']=0.
        with self.assertRaises(ValueError):self.board(stackup=stack)

    def test_old_two_layer_default_is_valid(self):
        b=self.board(layers=2, stackup=None)
        self.assertEqual(copper_layers(b.model_dump()), ['F.Cu','B.Cu'])
        self.assertEqual(len(fabrication_layers(b.model_dump())),9)

    def test_silkscreen_positions_are_rounded_to_native_nanometres(self):
        self.assertEqual(silkscreen_position(dict(x_mm=32.3, y_mm=29.4)), (32300000, 29400000))
        self.assertEqual(silkscreen_position(dict(x_mm=32.300001, y_mm=29.4000006)), (32300001, 29400001))

    def test_export_includes_both_inner_layers(self):
        layers=fabrication_layers(self.board().model_dump())
        self.assertEqual(layers[:4], ['F.Cu','In1.Cu','In2.Cu','B.Cu'])
        self.assertEqual(len(layers),11)

    def test_inner_local_route_on_two_layer_board_refused(self):
        bc=dict(width_mm=60.,height_mm=40.,placement_profile='explicit',
                placements=[],local_routes=[dict(net_id='n',layer='In1.Cu',width_mm=.3,
                points=[dict(x_mm=10.,y_mm=10.),dict(x_mm=20.,y_mm=10.)])])
        with self.assertRaisesRegex(ValueError, 'LOCAL_ROUTE_LAYER_DISABLED'):
            validate(dict(components={},nets={'n':dict(kind='signal')}),bc)

    def test_saved_stackup_is_checked_not_trusted(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'board.kicad_pcb'
            base='(kicad_pcb (general (thickness 1.6)) (layers (0 "F.Cu" signal) (4 "In1.Cu" signal) (6 "In2.Cu" signal) (2 "B.Cu" signal)) (setup))'
            path.write_text(base);apply(path,four_layer())
            self.assertTrue(inspect(path,four_layer())['passed'])
            path.write_text(path.read_text().replace('(epsilon_r 4.2)', '(epsilon_r 8.4)',1))
            self.assertIn('PCB_NATIVE_STACKUP_MISMATCH',inspect(path,four_layer())['errors'])
            path.write_text(base.replace('(6 "In2.Cu" signal)',''));apply(path,four_layer())
            self.assertIn('PCB_COPPER_LAYERS_MISMATCH',inspect(path,four_layer())['errors'])

    def test_internal_current_coefficient_and_declared_thickness(self):
        self.assertAlmostEqual(reference_current(.3,18.,10.,True),reference_current(.3,18.,10.)/2)
        s=dict(board_constraints=four_layer())
        self.assertEqual(layer_thicknesses(s,dict(copper_thickness_um=35.))['In1.Cu'],18.)
        self.assertEqual(layer_thicknesses(s,dict(copper_thickness_um=10.))['In1.Cu'],10.)

    def test_actual_path_uses_thinner_internal_copper(self):
        nodes={'source':dict(kind='pad',net='VCC',contacts=['inner']),
               'inner':dict(kind='track',net='VCC',contacts=['load'],layer='In1.Cu',length_mm=20.,width_mm=.3),
               'load':dict(kind='pad',net='VCC',contacts=[])}
        result=selected_path(nodes,{'source'},{'load'},35.,{'In1.Cu':18.})
        expected=1.72e-8*.020/(.0003*.000018)
        self.assertAlmostEqual(result['track_reference_ohm'],expected,12)
        with self.assertRaisesRegex(ValueError,'undeclared copper layer'):
            selected_path(nodes,{'source'},{'load'},35.,{'F.Cu':35.})

    def test_same_number_of_gerbers_with_wrong_inner_identity_fails(self):
        functions=['Copper,L1,Top','Copper,L2,Inr','Copper,L3,Inr','Copper,L4,Bot',
                   'Paste,Top','Paste,Bot','Legend,Top','Legend,Bot',
                   'Soldermask,Top','Soldermask,Bot','Profile,NP']
        with tempfile.TemporaryDirectory() as d:
            folder=Path(d)
            for i,function in enumerate(functions):
                (folder/(str(i)+'.gbr')).write_text('%TF.FileFunction,'+function+'*%\n%MOMM*%\nM02*\n')
            self.assertTrue(inspect_gerbers(folder,four_layer())['passed'])
            p=folder/'2.gbr';p.write_text(p.read_text().replace('L3','L5'))
            with self.assertRaisesRegex(ValueError,'GERBER_LAYER_SET_MISMATCH'):
                inspect_gerbers(folder,four_layer())
            p.write_text(p.read_text().replace('L5','L2'))
            with self.assertRaisesRegex(ValueError,'DUPLICATED'):
                inspect_gerbers(folder,four_layer())


if __name__=='__main__':unittest.main()
