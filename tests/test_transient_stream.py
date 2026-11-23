import hashlib
from pathlib import Path
import struct
import tempfile
import unittest
import numpy as np
from pcb_mcp_client.transient import read_raw, sha
from pcb_mcp_client.transient_stream import raw_chunks, power_chain_metrics


class StreamingTests(unittest.TestCase):
    def make_raw(self, directory, rows=None, encoding='utf-16-le', flags='real forward'):
        rows = [(0., 0.), (.001, 3.), (.002, 3.3), (.003, 2.)] if rows is None else rows
        h = f'Title: test\nPlotname: Transient Analysis\nFlags: {flags}\nNo. Variables: 2\nNo. Points: {len(rows)}\nVariables:\n0 time time\n1 V(out) voltage\nBinary:\n'
        p = Path(directory)/'test.raw'
        p.write_bytes(h.encode(encoding) + b''.join(struct.pack('<df', *r) for r in rows))
        return p

    def test_matches_existing_reader_both_encodings(self):
        for encoding in ('utf-8', 'utf-16-le'):
            with tempfile.TemporaryDirectory() as d:
                p = self.make_raw(d, encoding=encoding); old = read_raw(p)
                chunks = list(raw_chunks(p, chunk_points=2))
                for k in old:
                    np.testing.assert_array_equal(old[k], np.concatenate([w[k] for w in chunks]))
                self.assertEqual(sha(p), hashlib.sha256(p.read_bytes()).hexdigest())

    def test_timestamp_regression_across_chunk_boundary(self):
        with tempfile.TemporaryDirectory() as d:
            p=self.make_raw(d, [(0., 0.), (.002, 3.), (.001, 3.), (.003, 3.)])
            with self.assertRaisesRegex(ValueError, 'TIME_ORDER'):
                list(raw_chunks(p, chunk_points=2))

    def test_nonfinite_in_later_chunk(self):
        with tempfile.TemporaryDirectory() as d:
            p=self.make_raw(d, [(0., 0.), (.001, 3.), (.002, float('nan'))])
            with self.assertRaisesRegex(ValueError, 'NONFINITE'):
                list(raw_chunks(p, chunk_points=2))

    def test_extra_or_truncated_bytes(self):
        for extra in (True, False):
            with tempfile.TemporaryDirectory() as d:
                p=self.make_raw(d); b=p.read_bytes(); p.write_bytes(b+b'x' if extra else b[:-1])
                with self.assertRaisesRegex(ValueError, 'TRUNCATED_OR_EXTRA'):
                    list(raw_chunks(p))

    def test_changed_file_not_accepted(self):
        with tempfile.TemporaryDirectory() as d:
            p=self.make_raw(d); chunks=raw_chunks(p, chunk_points=2); next(chunks)
            with p.open('ab') as f: f.write(b'x')
            with self.assertRaisesRegex(ValueError, 'CHANGED_DURING_READ'):
                list(chunks)

    def test_unsupported_flags_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaisesRegex(ValueError, 'UNSUPPORTED_FLAGS'):
                list(raw_chunks(self.make_raw(d, flags='real forward double')))

    def wave(self):
        t=np.linspace(0,.035,35001)
        return {'time': t, 'v(v3v3)': np.minimum(t/.001,1)*3.318,
                'v(mcu_en)': 3.318*(1-np.exp(-np.maximum(t-.001,0)/.01)),
                'i(vraw)': np.full(t.shape,-.7)}

    def check(self, wave, size):
        return power_chain_metrics({k:v[i:i+size] for k,v in wave.items()}
                                   for i in range(0,len(wave['time']),size))

    def test_chunk_size_independent_including_reset_boundary(self):
        reference=self.check(self.wave(),35001)
        self.assertEqual(reference['verdict'],'scenario_checks_pass')
        for size in (1, 67, 905, 907, 1000, 4096):
            self.assertEqual(self.check(self.wave(),size),reference)

    def test_one_sample_droop_not_hidden(self):
        w=self.wave();w['v(v3v3)'][25000]=2.9
        r=self.check(w,1000)
        self.assertFalse(r['checks']['rail_in_range']);self.assertFalse(r['checks']['reset_held_low'])

    def test_overcurrent_and_early_en(self):
        w=self.wave();w['i(vraw)'][1000]=-1.6;w['v(mcu_en)'][500]=.7
        r=self.check(w,500)
        self.assertFalse(r['checks']['input_current_in_range']);self.assertFalse(r['checks']['reset_held_low'])

    def test_never_stable(self):
        w=self.wave();w['v(v3v3)'][:]=2.5
        self.assertIsNone(self.check(w,3000)['stable_after_s'])

    def test_truncated_window_and_sparse_data(self):
        w=self.wave()
        with self.assertRaisesRegex(ValueError, 'INCOMPLETE'):
            self.check({k:v[:-100] for k,v in w.items()},1000)
        with self.assertRaisesRegex(ValueError, 'UNDERSAMPLED'):
            self.check({k:v[::100] for k,v in w.items()},1000)

if __name__=='__main__':unittest.main()
