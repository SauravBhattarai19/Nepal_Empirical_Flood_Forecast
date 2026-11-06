# -*- coding: utf-8 -*-
"""
One-off preparation of the static river network ("prepare"), run once on any
computer with internet. Its results are committed in ``static/`` so the
2-hourly forecast never needs these heavy downloads or GIS libraries.

Steps (``nepal-forecast prepare all``):

1. ``dem``       Copernicus GLO-90 DEM (AWS open data) -> mosaic at 6 arc-seconds
2. ``flow``      D8 flow directions (priority-flood), upstream area
3. ``climate``   ERA5 2019-2023 annual and June-September rainfall (AWS open data)
4. ``landcover`` ESA WorldCover 2021 (AWS open data) -> runoff coefficient and CN
5. ``network``   river reaches (>= 20 km2), catchment descriptors, basins, rain weights

Extra dependencies: ``pip install -e ".[prepare]"``.
"""
