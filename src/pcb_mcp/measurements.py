"""Explicit sampled-waveform measurements, with time-weighted aggregation."""
import numpy as np


def ac_component(signal,mode):
    values=np.asarray(signal)
    if values.ndim!=1 or not len(values) or not np.all(np.isfinite(values)):raise ValueError('FINITE_AC_VECTOR_REQUIRED')
    if mode=='magnitude':return np.abs(values)
    if mode=='real':return values.real
    if mode=='imaginary':return values.imag
    raise ValueError('AC_COMPONENT_UNSUPPORTED')


def samples(signal, time=None, window=None):
    signal=np.asarray(signal)
    if signal.ndim!=1 or not len(signal) or np.iscomplexobj(signal) or not np.all(np.isfinite(signal)):
        raise ValueError('REAL_FINITE_MEASUREMENT_REQUIRED')
    if time is not None:
        time=np.asarray(time)
        if time.shape!=signal.shape or not np.all(np.isfinite(time)) or np.any(np.diff(time)<0):
            raise ValueError('MEASUREMENT_TIME_AXIS_INVALID')
        # ngspice can include two equal-time boundary samples. Keep the last
        # sample at that time, so interpolation has a single-valued domain.
        keep=np.r_[np.diff(time)>0,True]
        time,signal=time[keep],signal[keep]
    if window is not None:
        if time is None or time[0]>window[0] or time[-1]<window[1]:
            raise ValueError('TRANSIENT_WINDOW_NOT_COVERED')
        lo,hi=window;inside=(time>lo)&(time<hi)
        signal=np.r_[np.interp(lo,time,signal),signal[inside],np.interp(hi,time,signal)]
        time=np.r_[lo,time[inside],hi]
    return signal,time


def periodic(signal,time,threshold,window=None):
    """Per-cycle threshold frequency/duty on the piecewise-linear trace.

    Require three complete cycles and one falling crossing per period. This
    measures the sampled model; it does not establish unsampled glitch absence.
    """
    signal,time=samples(signal,time,window)
    if time is None or not np.isfinite(threshold):raise ValueError('PERIODIC_TIME_AND_THRESHOLD_REQUIRED')
    def crossings(rising):
        indices=np.flatnonzero((signal[:-1]<threshold)&(signal[1:]>=threshold) if rising
                               else (signal[:-1]>=threshold)&(signal[1:]<threshold))
        return np.array([time[i]+(threshold-signal[i])*(time[i+1]-time[i])/(signal[i+1]-signal[i]) for i in indices])
    rising,falling=crossings(True),crossings(False)
    if len(rising)<4:raise ValueError('PERIODIC_THREE_COMPLETE_CYCLES_REQUIRED')
    periods=np.diff(rising)
    if np.any(periods<=0):raise ValueError('PERIODIC_INVALID_PERIOD')
    duty=[]
    for start,end in zip(rising[:-1],rising[1:]):
        edges=falling[(falling>start)&(falling<end)]
        if len(edges)!=1:raise ValueError('PERIODIC_FALLING_EDGE_AMBIGUOUS')
        duty.append((edges[0]-start)/(end-start))
    return dict(frequency=1/periods,duty_cycle=np.array(duty),
                evidence=dict(complete_cycles=len(periods),threshold=threshold,
                              rising_crossings_s=rising.tolist(),
                              scope='interpolated_sampled_model_cycles_not_glitch_or_physical_qualification'))


def aggregate(signal, mode, time=None, window=None):
    signal,time=samples(signal,time,window)
    if mode=='final':low=high=float(signal[-1])
    elif mode=='peak':low=high=float(np.max(signal))
    elif mode=='minimum':low=high=float(np.min(signal))
    elif mode=='peak_to_peak':low=high=float(np.max(signal)-np.min(signal))
    elif mode in ('average','rms'):
        if time is None or len(time)<2 or time[-1]<=time[0]:
            raise ValueError('NONZERO_TRANSIENT_INTERVAL_REQUIRED')
        dt=np.diff(time);a,b=signal[:-1],signal[1:]
        # Exact integral of the piecewise-linear sampled signal (or its square),
        # rather than an arithmetic mean biased by the adaptive solver grid.
        area=np.sum(dt*(a+b)/2) if mode=='average' else np.sum(dt*(a*a+a*b+b*b)/3)
        value=float(area/(time[-1]-time[0]))
        low=high=value if mode=='average' else float(np.sqrt(max(0.,value)))
    elif mode=='range':low=float(np.min(signal));high=float(np.max(signal))
    else:raise ValueError('UNKNOWN_MEASUREMENT_AGGREGATION')
    if not np.isfinite(low) or not np.isfinite(high):raise ValueError('NONFINITE_MEASUREMENT_RESULT')
    return low,high


def ratio_of_aggregates(numerator,denominator,mode,time=None,window=None,denominator_minimum_abs=1e-15):
    """Aggregate each signed sum first; averaging instantaneous ratios differs."""
    if mode not in ('final','average','rms'):raise ValueError('RATIO_AGGREGATION_UNSUPPORTED')
    if not np.isfinite(denominator_minimum_abs) or denominator_minimum_abs<=0:raise ValueError('RATIO_DENOMINATOR_FLOOR_INVALID')
    shape=None
    def total(terms):
        nonlocal shape
        if not len(terms):raise ValueError('RATIO_TERMS_REQUIRED')
        values=[]
        for term in terms:
            term=np.asarray(term)
            if shape is None:shape=term.shape
            if term.shape!=shape:raise ValueError('RATIO_VECTOR_LENGTH_MISMATCH')
            samples(term,time)
            values.append(term)
        return np.sum(np.stack(values),axis=0)
    a=aggregate(total(numerator),mode,time,window)[0]
    b=aggregate(total(denominator),mode,time,window)[0]
    if abs(b)<denominator_minimum_abs:raise ValueError('RATIO_DENOMINATOR_TOO_SMALL')
    ratio=a/b
    if not np.isfinite(ratio):raise ValueError('NONFINITE_MEASUREMENT_RESULT')
    return dict(ratio=ratio,numerator_aggregate=a,denominator_aggregate=b,
                denominator_minimum_abs=denominator_minimum_abs,
                operation='ratio of aggregates of signed sums on the same actual trace and window')


def frequency_axis(frequency,shape):
    axis=np.asarray(frequency)
    if np.iscomplexobj(axis):
        if np.any(axis.imag!=0):raise ValueError('AC_FREQUENCY_AXIS_COMPLEX')
        axis=axis.real
    if axis.ndim!=1 or axis.shape!=shape or not len(axis) or not np.all(np.isfinite(axis)) or np.any(axis<=0) or np.any(np.diff(axis)<=0):raise ValueError('AC_FREQUENCY_AXIS_INVALID')
    return axis


def ac_gain_db(gain,frequency,reference_hz=None):
    gain=np.asarray(gain);samples(gain)
    if np.any(gain<=0):raise ValueError('AC_DB_REQUIRES_POSITIVE_GAIN')
    axis=frequency_axis(frequency,gain.shape);reference=1.;evidence=dict(reference_frequency_hz=None,reference_gain=1.,definition='20log10 of voltage transfer magnitude')
    if reference_hz is not None:
        if not np.isfinite(reference_hz) or reference_hz<=0:raise ValueError('AC_REFERENCE_FREQUENCY_INVALID')
        selected=np.flatnonzero(np.isclose(axis,reference_hz,rtol=1e-10,atol=0.))
        if len(selected)!=1:raise ValueError('AC_REFERENCE_MUST_MATCH_ONE_MEASURED_FREQUENCY')
        index=int(selected[0]);reference=float(gain[index]);evidence.update(reference_frequency_hz=float(axis[index]),reference_gain=reference,definition='20log10 of voltage transfer magnitude divided by actual measured reference gain')
    # Subtract logs rather than divide gains, avoiding avoidable overflow.
    db=20*(np.log10(gain)-np.log10(reference))
    if not np.all(np.isfinite(db)):raise ValueError('NONFINITE_MEASUREMENT_RESULT')
    return db,evidence


def ac_frequency_crossing(db,frequency,spec):
    values,_=samples(db);axis=frequency_axis(frequency,values.shape)
    lo,hi=spec.search_window_hz
    if axis[0]>lo or axis[-1]<hi:raise ValueError('AC_CROSSING_SEARCH_UNCOVERED')
    level=spec.level_db
    if spec.direction=='rising':indices=np.flatnonzero((values[:-1]<level)&(values[1:]>=level))
    else:indices=np.flatnonzero((values[:-1]>=level)&(values[1:]<level))
    found=[]
    for i in indices:
        estimate=float(axis[i]+(level-values[i])*(axis[i+1]-axis[i])/(values[i+1]-values[i]))
        if not lo<=estimate<=hi:continue
        bracket=[float(axis[i]),float(axis[i+1])]
        if values[i]==level:bracket=[float(axis[i])]*2
        elif values[i+1]==level:bracket=[float(axis[i+1])]*2
        found.append(dict(estimate_hz=estimate,bracket_hz=bracket))
    if len(found)<spec.occurrence:raise ValueError('AC_CROSSING_NOT_FOUND')
    result=found[spec.occurrence-1]
    result.update(level_db=level,direction=spec.direction,occurrence=spec.occurrence,
        scope='adjacent actual sample bracket, with a linear-in-Hz estimate; no assertion about unobserved additional crossings')
    return result
