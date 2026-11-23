"""Controlled impedance dependency verified against independent divider laws."""
from pathlib import Path
import tempfile,unittest
import numpy as np
from pcb_mcp import spice
from pcb_mcp.spice_bundle import isolate

LIBRARY=Path(__file__).resolve().parents[1]/'work/v1/development/thermostatic-fan/source-research/zx-compatibility.lib'


@unittest.skipUnless(Path('/Applications/KiCad/KiCad.app/Contents/Frameworks/libngspice.0.dylib').is_file(),'Native solver unavailable')
class Impedance(unittest.TestCase):
    def test_static_and_continuous_control_match_independent_positive_impedances(self):
        cards,entry=isolate(spice.model_lines(LIBRARY.read_text()),'ZX',1)
        deck='Controlled impedance reference\nV1 in 0 1\nVC control 0 PWL(0 .5 .001 .5 .002 2 .003 2)\nRref z 0 50\nX1 control 0 z in out '+entry+'\nRload out 0 50\n'+'\n'.join(cards)+'\n.options reltol=1e-10 abstol=1e-13 vntol=1e-12\n.tran 1u 3m 0 1u\n.end\n'
        with tempfile.TemporaryDirectory() as d:
            _,w=spice.run(deck,['time','v(control)','v(out)'],Path(d)/'run')
            np.testing.assert_allclose(w['v(out)'],1/(1+w['v(control)']),rtol=1e-8,atol=1e-10)


if __name__=='__main__':unittest.main()
