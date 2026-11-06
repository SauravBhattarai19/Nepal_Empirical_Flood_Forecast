# -*- coding: utf-8 -*-
"""Shared settings of the preparation steps."""

import os
import sys

# West, south, east, north (degrees). Covers Nepal and every catchment that
# drains into it (Tibet, Uttarakhand) with a margin.
BOX = (79.0, 26.0, 89.0, 32.0)
RES_DEG = 1.0 / 600.0             # 6 arc-seconds, about 185 m
MIN_RIVER_KM2 = 20.0              # small Chure streams start near 20 km2

CACHE = os.environ.get("NEPAL_FORECAST_CACHE",
                       os.path.join(os.getcwd(), "prepare_cache"))


def log(msg):
    print(msg, file=sys.stderr, flush=True)
