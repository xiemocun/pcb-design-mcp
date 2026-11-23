"""Event timing on the actual solver's piecewise-linear sampled trace."""
import numpy as np
from .measurements import samples


def crossings(signal,time,threshold,direction):
    values,time=samples(signal,time)
    if time is None or len(time)<2 or not np.isfinite(threshold):raise ValueError('EDGE_TIME_AND_THRESHOLD_REQUIRED')
    if direction=='rising':indices=np.flatnonzero((values[:-1]<threshold)&(values[1:]>=threshold))
    elif direction=='falling':indices=np.flatnonzero((values[:-1]>=threshold)&(values[1:]<threshold))
    else:raise ValueError('EDGE_DIRECTION_INVALID')
    return np.array([time[i]+(threshold-values[i])*(time[i+1]-time[i])/(values[i+1]-values[i]) for i in indices])


def trigger_time(signal,time,trigger):
    # Search on the whole trace, then filter. Trimming first would lose a
    # crossing exactly on the beginning of the declared search window.
    samples(signal,time,trigger.search_window_s)
    edges=crossings(signal,time,trigger.threshold,trigger.direction)
    lo,hi=trigger.search_window_s;edges=edges[(edges>=lo)&(edges<=hi)]
    if len(edges)<trigger.occurrence:raise ValueError('TRIGGER_EDGE_NOT_FOUND')
    return float(edges[trigger.occurrence-1])


def measure(signal,trigger_signal,time,event):
    trigger=trigger_time(trigger_signal,time,event.trigger);end=event.observation_end_s
    values,axis=samples(signal,time,[trigger,end])
    if hasattr(event,'target_threshold'):
        edges=crossings(signal,time,event.target_threshold,event.target_direction)
        edges=edges[(edges>=trigger)&(edges<=end)]
        if not len(edges):raise ValueError('TARGET_EDGE_NOT_FOUND_AFTER_TRIGGER')
        at=float(edges[0]);result=dict(seconds=at-trigger,target_crossing_s=at)
    else:
        lo,hi=event.band
        if not lo<=values[-1]<=hi:raise ValueError('SETTLING_BAND_NOT_REACHED')
        outside=np.flatnonzero((values<lo)|(values>hi))
        at=trigger
        if len(outside):
            i=int(outside[-1]);boundary=lo if values[i]<lo else hi
            at=float(axis[i]+(boundary-values[i])*(axis[i+1]-axis[i])/(values[i+1]-values[i]))
        hold=end-at
        if hold<event.minimum_hold_s:raise ValueError('SETTLING_HOLD_NOT_COVERED')
        result=dict(seconds=at-trigger,settled_at_s=at,band=event.band,observed_hold_s=hold,minimum_hold_s=event.minimum_hold_s)
    result.update(trigger_crossing_s=trigger,observation_end_s=end,
        scope='linear interpolation of finite solver samples; settling holds through observation end, not beyond it or between unobserved physical events')
    return result
