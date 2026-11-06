# -*- coding: utf-8 -*-
"""Settings: config.yaml in the repository root, over these defaults."""

import copy
import os

import yaml

# SCS curve number, AMC II, by ESA WorldCover class, for hydrologic soil group C
# (USDA TR-55 Table 2-2): no soil map is used, and group C is the conservative
# (wetter) choice for flood warning.
DEFAULT_CN = {
    10: 70,    # tree cover: woods, good condition
    20: 65,    # shrubland: brush, good
    30: 74,    # grassland: pasture, good
    40: 85,    # cropland: row crops, straight row, good
    50: 90,    # built-up
    60: 91,    # bare / sparse vegetation: rock and bare soil
    70: 90,    # snow and ice
    80: 98,    # open water
    90: 87,    # herbaceous wetland
    95: 70,    # mangroves
    100: 80,   # moss and lichen: thin alpine soils
}

DEFAULTS = {
    "title": "Nepal empirical flood forecast (experimental)",
    "timezone": "Asia/Kathmandu",
    "every_hours": 2,
    "forecast_days": 7,
    "past_days": 7,              # rain before now: antecedent moisture and runoff in transit
    "show_past_hours": 24,       # of that past, how much the page shows
    "models": [                  # Open-Meteo weather models (the first is the main one)
        {"id": "ecmwf_ifs025", "name": "ECMWF IFS"},
        {"id": "gfs_seamless", "name": "NOAA GFS"},
        {"id": "icon_seamless", "name": "DWD ICON"},
    ],
    "open_meteo": {
        "url": "https://api.open-meteo.com/v1/forecast",
        "api_key": None,          # or the OPEN_METEO_API_KEY environment variable
        "chunk": 100,             # locations per request
        "pause_s": 1.0,
    },
    "fallback_sources": ["noaa_gfs_aws"],   # when Open-Meteo cannot be reached
    "runoff": {
        "primary": "scs",         # the method that colours the map: scs | rational
        "ia_ratio": 0.2,          # SCS initial abstraction
        "event_gap_h": 12,        # dry hours that end a storm event
        "dry_mm_h": 0.2,          # catchment rain below this counts as dry
        "amc_dry_mm": 35.6,       # 5-day antecedent rain, growing season (NEH-4)
        "amc_wet_mm": 53.3,
        "lag_ratio": 0.6,         # SCS lag = 0.6 t_c
        "baseflow": "WECS/DHM 1990",   # normal flow of the month under the event runoff
        "baseflow_factor": 1.0,
        "cn_by_landcover": DEFAULT_CN,
        "c_by_landcover": None,   # None: by land cover (formulas.RATIONAL_C)
    },
    "thresholds": {
        "return_periods": [2, 5, 10, 20, 50, 100],
        "statistic": "median",    # of the area-based methods: median | min | max
        "mhsp_split_lon": [83.0, 86.0],
    },
    "points": [],                 # extra forecast points: {name, lat, lon, area_km2}
    "flood_levels": {},           # known floods of a station or point: {"283.3": {2: 150, 100: 700}}
    "publish": {
        "git": None,              # {remote: ..., branch: gh-pages}
        "command": None,          # or any command, {site} = the built site folder
    },
}


def deep_merge(base, over):
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def load(path=None):
    path = path or os.environ.get("NEPAL_FORECAST_CONFIG", "config.yaml")
    user = {}
    if path and os.path.exists(path):
        with open(path) as f:
            user = yaml.safe_load(f) or {}
    cfg = deep_merge(DEFAULTS, user)
    cfg["runoff"]["cn_by_landcover"] = {int(k): float(v) for k, v in
                                        cfg["runoff"]["cn_by_landcover"].items()}
    if cfg["runoff"].get("c_by_landcover"):
        cfg["runoff"]["c_by_landcover"] = {int(k): float(v) for k, v in
                                           cfg["runoff"]["c_by_landcover"].items()}
    cfg["flood_levels"] = {str(k): {int(t): float(q) for t, q in (v or {}).items()}
                           for k, v in (cfg.get("flood_levels") or {}).items()}
    cfg["_path"] = path
    return cfg
