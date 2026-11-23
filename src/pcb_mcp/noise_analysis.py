"""Stationary differential output noise in a frozen, source-linked SPICE model."""
import math
import numpy as np

from . import spice
from .measurements import frequency_axis, samples


def evaluate(dc_deck, folder, scenario, positive, negative, evidence, models, dialect):
    if dialect not in ('native', 'pspice'):
        raise ValueError('NOISE_REQUIRES_NGSPICE')
    if positive == negative:
        raise ValueError('NOISE_REQUIRES_DISTINCT_MEASUREMENT_NETS')

    start, end = scenario.frequency_hz
    for row in evidence:
        if 'model_id' not in row:
            continue  # The compiler emits standard passive primitives and ideal contacts directly.
        ref = row['reference']
        scope = models[ref][0].get('spice_noise_scope')
        if not scope:
            raise ValueError('NOISE_MODEL_COVERAGE_MISSING: ' + ref)
        model_start, model_end = scope['frequency_hz']
        if start < model_start or end > model_end:
            raise ValueError('NOISE_MODEL_BAND_UNCOVERED: ' + ref)
        row['spice_noise_scope'] = scope

    index, drive = next((i, d) for i, d in enumerate(scenario.drives) if d.ac_v is not None or d.ac_a is not None)
    source = f'{"V" if drive.kind == "voltage" else "I"}drive{index}'
    deck = dc_deck.removesuffix('.op\n.end\n')
    deck += f'.noise V({positive},{negative}) {source} lin {scenario.points} {start:.15g} {end:.15g}\n.end\n'
    vectors = ['noise1.frequency', 'noise1.onoise_spectrum', 'noise2.onoise_total']
    options = {} if scenario.solver_timeout_s is None else {'timeout': scenario.solver_timeout_s}
    meta, waves = spice.run(deck, vectors, folder, dialect=dialect, **options)

    density, _ = samples(waves['noise1.onoise_spectrum'])
    axis = frequency_axis(waves['noise1.frequency'], density.shape)
    total, _ = samples(waves['noise2.onoise_total'])
    if len(axis) != scenario.points or not np.allclose(axis[[0, -1]], [start, end], rtol=1e-9, atol=0):
        raise ValueError('NOISE_ANALYSIS_BAND_INCOMPLETE')
    if len(total) != 1 or total[0] < 0 or np.any(density < 0):
        raise ValueError('NOISE_SPECTRUM_OR_INTEGRAL_INVALID')
    # The native spectrum is amplitude density in V/sqrt(Hz). Integrate its
    # square once as an independent check on the native integrated vector.
    with np.errstate(over='ignore', invalid='ignore'):
        squared = np.square(density)
        rms = float(np.sqrt(np.sum(np.diff(axis) * (squared[:-1] + squared[1:]) / 2)))
    native_rms = float(total[0])
    if not math.isfinite(rms) or not math.isclose(rms, native_rms, rel_tol=.01):
        raise ValueError('NOISE_INTEGRAL_GRID_OR_SOLVER_DISAGREEMENT')
    return native_rms, dict(scenario=scenario.id, engine=scenario.engine, solver=meta, models=evidence,
        artifact_directory=str(folder), noise_reference=dict(frequency_hz=[float(axis[0]), float(axis[-1])],
            frequency_points=len(axis), output_nodes=[positive, negative], reference_drive_index=index,
            density_units='V/sqrt(Hz)', integrated_units='V RMS', native_integrated_rms_v=native_rms,
            spectral_trapezoid_rms_v=rms, integral_comparison_relative_tolerance=.01,
            temperature_c=scenario.temperature_c,
            scope='stationary noise from included model and fixture sources around the matching DC bias'))
