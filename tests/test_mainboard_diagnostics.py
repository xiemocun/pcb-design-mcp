from pathlib import Path
import sys,unittest
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[3]
sys.path.insert(0,str(ROOT/'work/mainboard-chain-review'))
import run_diagnostics as d

class DiagnosticTests(unittest.TestCase):
    def test_full_circuit_components_load_and_timing_unchanged(self):
        for slope in (1,3):
            original=(d.OLD/'board-fixtures'/f'chain_s{slope}_ltspice.cir').read_text()
            for solver in ('alt_trap','alt_gear'):
                new=d.circuit_text(slope,solver,False)
                self.assertEqual(new.replace(f'.options solver=alt method={solver.split("_")[1]}\n',''),original)

    def test_probe_only_shortens_observation(self):
        for solver in ('alt_trap','alt_gear'):
            self.assertEqual(d.circuit_text(1,solver,True).replace('.tran 20n 9m 0 20n','.tran 20n 35m 0 20n'),
                             d.circuit_text(1,solver,False))

    def test_unknown_solver_rejected(self):
        with self.assertRaisesRegex(ValueError,'UNKNOWN_SOLVER'):
            d.circuit_text(1,'loosen_tolerance',False)

    def test_single_thread_study_keeps_all_circuit_values(self):
        original=(d.OLD/'board-fixtures'/'chain_s1_ltspice.cir').read_text()
        for solver in ('norm_trap','norm_gear'):
            for step in (20,100):
                text=d.circuit_text(1,solver,False,threads=1,max_step_ns=step)
                text=text.replace(f'.options solver=norm method={solver.split("_")[1]} threads=1\n','')
                text=text.replace(f'.tran {step}n 35m 0 {step}n','.tran 20n 35m 0 20n')
                self.assertEqual(text,original)

    def test_unknown_time_step_rejected(self):
        with self.assertRaisesRegex(ValueError,'UNKNOWN_TIME_STEP'):
            d.circuit_text(1,'norm_gear',False,max_step_ns=10000)

if __name__=='__main__':unittest.main()
