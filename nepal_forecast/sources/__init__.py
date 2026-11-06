# -*- coding: utf-8 -*-
"""Rain forecast sources. Each returns a :class:`RainGrid`: hourly rain at the
0.25 deg grid nodes of ``static/rain_nodes.csv`` for one or more weather models."""

from dataclasses import dataclass, field

import numpy as np


@dataclass
class RainGrid:
    times: list                       # hourly UTC datetimes (end of each hour)
    precip: dict                      # model id -> (nodes, hours) total precipitation, mm/h
    liquid: dict                      # model id -> (nodes, hours) rain without snowfall, mm/h
    source: str = ""
    info: dict = field(default_factory=dict)   # model id -> {"run": ..., "coverage": ...}

    def models(self):
        return list(self.precip)


class FeedError(RuntimeError):
    """A feed that could not be read; the message says why in plain words."""


def clean(a):
    a = np.asarray(a, dtype=float)
    return np.where(np.isfinite(a), np.maximum(a, 0.0), 0.0)
