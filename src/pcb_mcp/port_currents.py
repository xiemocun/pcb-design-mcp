"""Resolve derived terminal currents without adding circuit elements."""
import numpy as np


def rows(probes):
    return [row for pins in probes.values() for row in pins if row.get('scope')=='external_port']


def derived_rows(probes):
    return [row for pins in probes.values() for row in pins if 'terms' in row]


def vectors(probes):
    result=[]
    for row in derived_rows(probes):
        for term in row['terms']:
            if term['kind']=='direct':result.append(term['vector'])
            else:result.extend(term[key] for key in ('positive','negative') if term.get(key))
    return list(dict.fromkeys(result))


def materialize(probes,waves,engine):
    length=len(next(iter(waves.values())))
    if any(len(value)!=length for value in waves.values()):raise ValueError('SPICE_VECTOR_LENGTH_MISMATCH')
    def voltage(name):return np.zeros(length) if name is None else waves[name]
    for row in derived_rows(probes):
        current=np.zeros(length,dtype=complex if engine=='spice_ac' else float)
        for term in row['terms']:
            kind=term['kind']
            if kind=='direct':value=waves[term['vector']]
            elif kind=='ohmic':value=(voltage(term['positive'])-voltage(term['negative']))/term['resistance_ohm']
            elif kind=='current_sink':
                delta=voltage(term['positive'])-voltage(term['negative'])
                value=term['current_a']*np.clip(delta/term['compliance_v'],0.,1.)
            elif kind=='ac_capacitance':value=2j*np.pi*waves['frequency'].real*term['capacitance_f']*(voltage(term['positive'])-voltage(term['negative']))
            elif kind=='zero':value=0.
            else:raise ValueError('EXTERNAL_PORT_CURRENT_TERM_INVALID')
            current+=term['factor']*value
        if not np.all(np.isfinite(current)):raise ValueError('EXTERNAL_PORT_CURRENT_NONFINITE')
        waves[row['vector']]=current
