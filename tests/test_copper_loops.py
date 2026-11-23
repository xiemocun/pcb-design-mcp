"""Declared DC loops use real terminals and routed track budgets."""

from copy import deepcopy
import unittest

from pcb_mcp.copper import policy_findings
from pcb_mcp.power_paths import evaluate_loops


class CopperLoopTests(unittest.TestCase):
    def setUp(self):
        def pin(component, number):
            return dict(component_id=component, pin_id=number)

        source, load = 'source', 'load'
        self.state = dict(board_constraints=dict(layers=2), components={
            source: dict(ref='J1', pins=[dict(pin_id=number, number=number) for number in ('1', '2')]),
            load: dict(ref='J2', pins=[dict(pin_id=number, number=number) for number in ('1', '2')]),
        }, nets={
            'v': dict(id='v', name='VIN', kind='power', pins=[pin(source, '1'), pin(load, '1')]),
            'g': dict(id='g', name='GND', kind='ground', pins=[pin(load, '2'), pin(source, '2')]),
        })
        self.loop = dict(name='Load loop', maximum_track_drop_v=.1, legs=[
            dict(source=pin(source, '1'), target=pin(load, '1')),
            dict(source=pin(load, '2'), target=pin(source, '2')),
        ])
        self.state['pcb_copper_policy'] = dict(copper_thickness_um=35., temperature_rise_c=10.,
            rules=[dict(net_id=net, min_width_mm=.15, design_current_a=.5, reference_drop_budget_v=.1)
                   for net in ('v', 'g')], dc_loops=[self.loop])
        nodes = {}
        for number, net in [('1', 'VIN'), ('2', 'GND')]:
            tracks = f't{number}'
            pads = [f'{ref}.{number}' for ref in ('J1', 'J2')]
            nodes[tracks] = dict(kind='track', net=net, layer='F.Cu', width_mm=.25, length_mm=40., contacts=pads)
            for ref, uid in zip(('J1', 'J2'), pads):
                nodes[uid] = dict(kind='pad', net=net, reference=ref, number=number, contacts=[tracks])
        self.native = dict(direct_contacts=dict(nodes=nodes))

    def test_sums_feed_and_return_at_the_declared_net_currents(self):
        self.assertEqual(policy_findings(self.state), [])
        row, = evaluate_loops(self.state, self.native, True)
        self.assertEqual(row['status'], 'pass')
        expected = 2 * .5 * 1.72e-8 * .040 / (.00025 * .000035)
        self.assertAlmostEqual(row['track_drop_reference_v'], expected)

    def test_drop_budget_and_native_continuity_fail_separately(self):
        self.loop['maximum_track_drop_v'] = .05
        row, = evaluate_loops(self.state, self.native, True)
        self.assertEqual(row['reason'], 'COPPER_LOOP_TRACK_DROP_EXCEEDS_BUDGET')
        disconnected = deepcopy(self.native)
        disconnected['direct_contacts']['nodes']['J2.2']['contacts'] = []
        row, = evaluate_loops(self.state, disconnected, True)
        self.assertEqual(row['status'], 'fail')
        self.assertIn('No actual direct-contact path', row['reason'])

    def test_declared_wrong_net_and_unverified_native_input_are_rejected(self):
        row, = evaluate_loops(self.state, self.native, False)
        self.assertEqual(row['status'], 'incomplete')
        self.state['components']['load']['pins'].append(dict(pin_id='3', number='3'))
        self.state['nets']['v']['pins'].append(dict(component_id='load', pin_id='3'))
        self.loop['legs'][1]['source']['pin_id'] = '3'
        self.assertEqual(policy_findings(self.state)[0]['code'], 'COPPER_LOOP_LEG_NEEDS_ONE_RULED_NET')
