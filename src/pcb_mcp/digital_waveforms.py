"""Dual-threshold edge interpolation for sampled receiver voltages."""
from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class Edge:
    start: float
    end: float
    level: int


def logic_edges(values, axis, low, high, *, protocol, idle_high=False):
    levels = np.where(values <= low, 0, np.where(values >= high, 1, -1))
    if idle_high and (levels[0] != 1 or levels[-1] != 1):
        raise ValueError(protocol + '_WINDOW_REQUIRES_VALID_HIGH_IDLE_ENDPOINTS')
    if levels[0] < 0 or levels[-1] < 0:
        raise ValueError(protocol + '_WINDOW_REQUIRES_VALID_LOGIC_ENDPOINTS')

    def at(index, threshold):
        fraction = (threshold - values[index-1]) / (values[index] - values[index-1])
        return float(axis[index-1] + fraction * (axis[index] - axis[index-1]))

    initial = stable = int(levels[0])
    edges = []
    departure = None
    for index in np.flatnonzero(np.diff(levels)) + 1:
        level = int(levels[index])
        if departure is None:
            departure = at(index, high if stable else low)
        if level < 0:
            continue
        if level == stable:
            raise ValueError(protocol + '_AMBIGUOUS_LOGIC_EXCURSION')
        edges.append(Edge(departure, at(index, high if level else low), level))
        departure = None
        stable = level
    return initial, edges
