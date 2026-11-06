# -*- coding: utf-8 -*-
"""
Fallback: NOAA GFS 0.25 deg straight from the NOAA Open Data bucket on AWS,
used only when Open-Meteo cannot be reached. Only the total-precipitation
record of each file is read (an HTTP range of ~300 kB, found in the .idx).

* forecast: the newest cycle with 168 h out; hourly to 120 h, then 3-hourly
  (spread evenly over its hours);
* the past days: the first 6 hours of each earlier cycle.

All precipitation is taken as liquid (GFS's frozen fraction is not read).
Needs the optional ``eccodes`` package (``pip install eccodes``).
"""

from datetime import datetime, timedelta

import numpy as np

from . import FeedError, RainGrid
from ._http import get

BUCKET = "https://noaa-gfs-bdp-pds.s3.amazonaws.com"
MODEL_ID = "gfs_aws"


def _path(run, fh):
    return (f"{BUCKET}/gfs.{run:%Y%m%d}/{run:%H}/atmos/"
            f"gfs.t{run:%H}z.pgrb2.0p25.f{fh:03d}")


def _total_record(run, fh):
    """Bytes of the 0-fh hour total precipitation record."""
    idx = get(_path(run, fh) + ".idx", timeout=30, retries=2).splitlines()
    for k, line in enumerate(idx):
        p = line.split(":")
        if p[3] == "APCP" and (p[5].startswith("0-") or "day acc" in p[5]):
            start = int(p[1])
            end = int(idx[k + 1].split(":")[1]) - 1 if k + 1 < len(idx) else ""
            return get(_path(run, fh), timeout=60, retries=3, binary=True,
                       headers={"Range": f"bytes={start}-{end}"})
    raise FeedError(f"no APCP total in {_path(run, fh)}")


def _values(msg, ii, jj):
    import eccodes
    h = eccodes.codes_new_from_message(msg)
    try:
        ni, nj = eccodes.codes_get(h, "Ni"), eccodes.codes_get(h, "Nj")
        v = eccodes.codes_get_values(h).reshape(nj, ni)
    finally:
        eccodes.codes_release(h)
    return v[ii, jj]


def _latest_run(now):
    t = now.replace(minute=0, second=0, microsecond=0)
    t -= timedelta(hours=t.hour % 6)
    for _ in range(8):
        try:
            get(_path(t, 168) + ".idx", timeout=20, retries=1)
            return t
        except FeedError:
            t -= timedelta(hours=6)
    raise FeedError("no complete GFS cycle on AWS in the last two days")


def fetch(nodes, cfg, now=None, log=print):
    try:
        import eccodes  # noqa: F401
    except ImportError:
        raise FeedError("the GFS fallback needs 'pip install eccodes'")
    now = now or datetime.utcnow()
    lat, lon = nodes["lat"].to_numpy(), nodes["lon"].to_numpy()
    ii = np.round((90.0 - lat) / 0.25).astype(int)
    jj = np.round((lon % 360.0) / 0.25).astype(int)
    run = _latest_run(now)
    log(f"  GFS on AWS: cycle {run:%Y-%m-%d %H} UTC")
    past = int(cfg["past_days"]) * 24
    start = run - timedelta(hours=past)
    hours = [start + timedelta(hours=k) for k in range(1, past + 24 * int(cfg["forecast_days"]) + 1)]
    rain = np.zeros((len(lat), len(hours)))
    pos = {t: k for k, t in enumerate(hours)}
    # the past: hours 1-6 of each earlier cycle
    for c in range(past // 6):
        cyc = start + timedelta(hours=6 * c)
        try:
            prev = np.zeros(len(lat))
            for fh in range(1, 7):
                tot = _values(_total_record(cyc, fh), ii, jj)
                rain[:, pos[cyc + timedelta(hours=fh)]] = np.maximum(tot - prev, 0)
                prev = tot
        except FeedError as exc:
            log(f"  GFS {cyc:%m-%d %H}: {exc}")
    # the forecast
    prev, prev_h = np.zeros(len(lat)), 0
    steps = list(range(1, 121)) + list(range(123, 24 * int(cfg["forecast_days"]) + 1, 3))
    for fh in steps:
        tot = _values(_total_record(run, fh), ii, jj)
        inc = np.maximum(tot - prev, 0) / (fh - prev_h)
        for h in range(prev_h + 1, fh + 1):
            t = run + timedelta(hours=h)
            if t in pos:
                rain[:, pos[t]] = inc
        prev, prev_h = tot, fh
        if fh % 24 == 0:
            log(f"  GFS on AWS: +{fh} h")
    return RainGrid(times=hours, precip={MODEL_ID: rain}, liquid={MODEL_ID: rain.copy()},
                    source="noaa-gfs-aws", info={MODEL_ID: {"run": f"{run:%Y-%m-%d %H}:00 UTC"}})
