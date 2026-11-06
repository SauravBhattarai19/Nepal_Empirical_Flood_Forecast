# -*- coding: utf-8 -*-
"""
Open-Meteo forecast API (free for non-commercial use, CC BY 4.0, no key).

One request asks for up to ``chunk`` grid nodes and every model at once
(hourly precipitation and snowfall). Open-Meteo counts each location as one
call, so the 377 nodes of Nepal cost about 380 calls per update, ~4,500 a day
at a 2-hourly update: inside the free 10,000 a day. With an API key
(``open_meteo.api_key`` or OPEN_METEO_API_KEY) the customer server is used.
"""

import os
import time
from datetime import datetime, timezone

import numpy as np

from . import FeedError, RainGrid, clean
from ._http import get_json

ATTRIBUTION = "Weather data by Open-Meteo.com (CC BY 4.0)"
SNOW_CM_TO_MM = 10.0 / 7.0       # Open-Meteo: 7 cm of snow = 10 mm water


def _url(cfg, lats, lons, models, past_days, forecast_days):
    om = cfg["open_meteo"]
    key = om.get("api_key") or os.environ.get("OPEN_METEO_API_KEY")
    base = om["url"]
    if key and "customer-" not in base:
        base = base.replace("://api.", "://customer-api.")
    q = (f"{base}?latitude={','.join(f'{x:.2f}' for x in lats)}"
         f"&longitude={','.join(f'{x:.2f}' for x in lons)}"
         f"&hourly=precipitation,snowfall&models={','.join(models)}"
         f"&past_days={past_days}&forecast_days={forecast_days}"
         f"&timezone=GMT&timeformat=unixtime")
    if key:
        q += f"&apikey={key}"
    return q


def fetch(nodes, cfg, log=print):
    """nodes: DataFrame with lat, lon. -> RainGrid for every configured model."""
    models = [m["id"] for m in cfg["models"]]
    chunk = int(cfg["open_meteo"]["chunk"])
    past, fdays = int(cfg["past_days"]), int(cfg["forecast_days"])
    lats, lons = nodes["lat"].to_numpy(), nodes["lon"].to_numpy()
    times = None
    precip = {m: [] for m in models}
    snow = {m: [] for m in models}
    for k in range(0, len(lats), chunk):
        data = get_json(_url(cfg, lats[k:k + chunk], lons[k:k + chunk], models, past, fdays),
                        timeout=90)
        if isinstance(data, dict) and data.get("error"):
            raise FeedError(f"Open-Meteo: {data.get('reason')}")
        items = data if isinstance(data, list) else [data]
        for it in items:
            h = it["hourly"]
            t = h["time"]
            if times is None:
                times = t
            elif t != times:
                raise FeedError("Open-Meteo returned different times for different places")
            for m in models:
                key_p = f"precipitation_{m}" if f"precipitation_{m}" in h else "precipitation"
                key_s = f"snowfall_{m}" if f"snowfall_{m}" in h else "snowfall"
                precip[m].append(np.array(h.get(key_p, [None] * len(t)), dtype=float))
                snow[m].append(np.array(h.get(key_s, [None] * len(t)), dtype=float))
        log(f"  Open-Meteo: {min(k + chunk, len(lats))}/{len(lats)} nodes")
        time.sleep(float(cfg["open_meteo"].get("pause_s", 1.0)))
    # Open-Meteo hourly precipitation is the sum of the *preceding* hour
    tt = [datetime.fromtimestamp(int(x), tz=timezone.utc).replace(tzinfo=None) for x in times]
    out_p, out_l, info = {}, {}, {}
    for m in models:
        p = np.stack(precip[m])
        s = np.stack(snow[m])
        cover = float(np.isfinite(p).mean())
        if cover < 0.5:
            log(f"  Open-Meteo: {m} has data for only {cover:.0%} of the hours; left out")
            continue
        out_p[m] = clean(p)
        out_l[m] = np.maximum(out_p[m] - clean(s) * SNOW_CM_TO_MM, 0.0)
        info[m] = {"coverage": round(cover, 3)}
    if not out_p:
        raise FeedError("Open-Meteo returned no usable model")
    return RainGrid(times=tt, precip=out_p, liquid=out_l, source="open-meteo", info=info)
