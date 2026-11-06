# -*- coding: utf-8 -*-
"""
One forecast update:

1. download the rain forecast of every model at the grid nodes (Open-Meteo;
   the GFS-on-AWS fallback when Open-Meteo cannot be reached);
2. average it over the catchment of every forecast point (static weights);
3. turn it into discharge with every rainfall-based method (hydrograph.py);
4. compare the peaks with the design floods of the area-based methods;
5. write the web site's data files into ``site/data/``.

A failed update leaves the last good site in place.
"""

import json
import os
import shutil
import time
from datetime import datetime, timedelta

import numpy as np
import pandas as pd

from . import config as _config
from . import formulas as F
from . import hydrograph as H

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WEB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")
CHUNK = 150                      # points per hydrograph file (one file loads per click)


def log(msg):
    print(msg, flush=True)


# ── static network ─────────────────────────────────────────────────────────
class Network:
    def __init__(self, static_dir):
        self.dir = static_dir
        self.targets = pd.read_csv(os.path.join(static_dir, "targets.csv"),
                                   dtype={"station": str}, keep_default_na=False,
                                   na_values={"mwi_mm": [""], "awi_mm": [""]})
        self.nodes = pd.read_csv(os.path.join(static_dir, "rain_nodes.csv"))
        w = np.load(os.path.join(static_dir, "rain_weights.npz"))
        W = np.zeros(tuple(w["shape"]), dtype="float64")
        W[w["rows"], w["cols"]] = w["vals"]
        self.W = W                                     # (targets, nodes), rows sum to 1
        self.n = len(self.targets)

    def desc(self):
        t = self.targets
        d = {k: t[k].to_numpy(float).copy() for k in
             ("area_km2", "a3000_km2", "a5000_km2", "elev_mean_m", "tc_h",
              "c_rational", "cn2", "mwi_mm", "awi_mm", "lon")}
        # without a rainfall climatology the monthly formulas get a typical value
        for k, typical in (("mwi_mm", 1500.0), ("awi_mm", 2000.0)):
            bad = ~np.isfinite(d[k])
            d[k][bad] = typical
        return d


def design_thresholds(net, cfg):
    """(n, len(T)) thresholds and the per-method table for the page."""
    rps = list(cfg["thresholds"]["return_periods"])
    stat = cfg["thresholds"]["statistic"]
    split = tuple(cfg["thresholds"]["mhsp_split_lon"])
    t = net.targets
    thr = np.full((net.n, len(rps)), np.nan)
    table = []
    for i in range(net.n):
        d = F.design_floods(t.area_km2[i], t.a3000_km2[i], t.a5000_km2[i], t.lon[i], rps, split)
        thr[i] = [d[stat][T] if d[stat][T] is not None else np.nan for T in rps]
        table.append({m: [None if d[m][T] is None else round(d[m][T], 1) for T in rps]
                      for m in F.DESIGN_METHODS})
    # floods known for a station or point (flood_levels: in config.yaml) replace the formulas
    given = cfg.get("flood_levels") or {}
    if given:
        for i in range(net.n):
            keys = [str(t.station[i]), str(t.name[i])]
            lv = next((given[k] for k in keys if k and k in given), None)
            if not lv:
                continue
            for k, T in enumerate(rps):
                if T in lv:
                    thr[i, k] = lv[T]
            table[i]["Given in config.yaml"] = [lv.get(T) for T in rps]
    return rps, thr, table


def progress(peak, rise, first_level):
    """How far the forecast rise goes from the normal flow towards the first
    flood level (1 = reaches it). The normal flow itself counts for nothing,
    so big rivers in their usual monsoon flow do not top the list."""
    base = peak - rise
    room = np.maximum(first_level - base, 0.1 * first_level)
    return np.where(np.isfinite(first_level), rise / room, 0.0)


# ── rain ───────────────────────────────────────────────────────────────────
def get_rain(net, cfg, now=None):
    from .sources import FeedError, open_meteo
    errors = []
    try:
        return open_meteo.fetch(net.nodes, cfg, log=log), errors
    except FeedError as exc:
        errors.append(f"Open-Meteo: {exc}")
        log(f"  {errors[-1]}")
    for name in cfg.get("fallback_sources") or []:
        try:
            if name == "noaa_gfs_aws":
                from .sources import noaa_gfs_aws
                return noaa_gfs_aws.fetch(net.nodes, cfg, now=now, log=log), errors
        except FeedError as exc:
            errors.append(f"{name}: {exc}")
            log(f"  {errors[-1]}")
    raise RuntimeError("no rain forecast could be read: " + "; ".join(errors))


# ── output helpers ─────────────────────────────────────────────────────────
def sig(a, digits=3):
    """Round to *digits* significant figures (compact JSON)."""
    a = np.asarray(a, dtype=float)
    out = np.zeros_like(a)
    nz = np.isfinite(a) & (a != 0)
    mag = np.floor(np.log10(np.abs(a[nz])))
    f = 10.0 ** (digits - 1 - mag)
    out[nz] = np.round(a[nz] * f) / f
    out[~np.isfinite(a)] = np.nan
    return out


def jlist(a, digits=3, decimals=None):
    a = np.asarray(a, dtype=float)
    a = np.round(a, decimals) if decimals is not None else sig(a, digits)
    res = []
    for v in a.ravel().tolist():
        if v != v:
            res.append(None)
        elif v == int(v) and abs(v) < 1e15:
            res.append(int(v))
        else:
            res.append(v)
    return res


def write_json(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(obj, f, separators=(",", ":"), allow_nan=False)
    os.replace(tmp, path)


# ── the update ─────────────────────────────────────────────────────────────
def run(cfg=None, static_dir=None, site_dir=None, now=None, rain=None):
    t_start = time.time()
    cfg = cfg or _config.load()
    static_dir = static_dir or os.path.join(ROOT, "static")
    site_dir = site_dir or os.path.join(ROOT, "site")
    now = now or datetime.utcnow().replace(second=0, microsecond=0)
    net = Network(static_dir)
    log(f"Forecast update {now:%Y-%m-%d %H:%M} UTC: {net.n} points, {len(net.nodes)} rain nodes")

    errors = []
    if rain is None:
        rain, errors = get_rain(net, cfg, now)
    times = rain.times
    h = len(times)
    names = {m["id"]: m["name"] for m in cfg["models"]}
    names.setdefault("gfs_aws", "NOAA GFS (AWS)")
    models = rain.models()
    log(f"  rain from {rain.source}: {', '.join(models)}; {h} hours "
        f"{times[0]:%m-%d %H}h to {times[-1]:%m-%d %H}h UTC")

    # index of "now" in the hourly axis: first hour ending after now
    i_now = next((k for k, t in enumerate(times) if t > now), h - 1)
    show0 = max(i_now - int(cfg["show_past_hours"]), 0)

    desc = net.desc()
    rps, thr, thr_table = design_thresholds(net, cfg)
    primary = cfg["runoff"]["primary"]

    res = {}
    for m in models:
        cr = net.W @ rain.liquid[m]                  # (targets, hours) catchment rain
        ct = net.W @ rain.precip[m]
        sim = H.simulate(cr, times, desc, cfg)
        sim["rain"], sim["precip"] = cr, ct
        res[m] = sim
        log(f"  {m}: simulated ({time.time() - t_start:.0f} s)")

    # peaks over the forecast (from now on)
    fut = slice(i_now, h)
    summary = {"peak": {}, "tpeak": {}, "rise": {}, "prog": {}, "cls": {}, "scs_peak": {}, "rat_peak": {}, "mono_peak": {},
               "rich_peak": {}, "rain72": {}, "rain_total": {}}
    days = res[models[0]]["days"]
    today = (now + timedelta(hours=5.75)).date()
    future_days = [k for k, d in enumerate(days) if d >= today]
    for m in models:
        q = res[m][primary][:, fut]
        k = np.argmax(q, axis=1)
        pk = q[np.arange(net.n), k]
        summary["peak"][m] = jlist(pk)
        summary["tpeak"][m] = (k + i_now - show0).tolist()   # index in the shown series
        rise = pk - res[m]["base"][np.arange(net.n), k + i_now]    # above the normal flow
        summary["rise"][m] = jlist(np.maximum(rise, 0.0))
        summary["prog"][m] = jlist(progress(pk, np.maximum(rise, 0.0), thr[:, 0]), decimals=2)
        summary["cls"][m] = H.classify(pk, thr, rps).tolist()
        summary["scs_peak"][m] = jlist(res[m]["scs"][:, fut].max(axis=1))
        summary["rat_peak"][m] = jlist(res[m]["rational"][:, fut].max(axis=1))
        summary["mono_peak"][m] = jlist(res[m]["mononobe"][:, future_days].max(axis=1))
        summary["rich_peak"][m] = jlist(res[m]["richards"][:, future_days].max(axis=1))
        summary["rain72"][m] = jlist(res[m]["precip"][:, i_now:i_now + 72].sum(axis=1), decimals=1)
        summary["rain_total"][m] = jlist(res[m]["precip"][:, fut].sum(axis=1), decimals=1)
    cls_all = np.stack([summary["cls"][m] for m in models])
    summary["cls_max"] = cls_all.max(axis=0).tolist()
    summary["n_models_q2"] = (cls_all >= rps[0]).sum(axis=0).tolist()
    for k, T in enumerate(rps):
        summary[f"q{T}"] = jlist(thr[:, k])
    month = (now + timedelta(hours=5.75)).month
    year = H.year_normals(desc)                      # (n, 12) per method: the page charts the year
    summary["normal"] = {k: jlist(v[:, month - 1]) for k, v in year.items()}

    # site data
    data = os.path.join(site_dir, "data")
    tmp = os.path.join(site_dir, "data.new")
    shutil.rmtree(tmp, ignore_errors=True)
    os.makedirs(os.path.join(tmp, "series"))

    # hydrograph files: by basin, in chunks of at most CHUNK points
    t = net.targets
    groups = t.groupby("basin").groups
    grp = [""] * net.n
    for b, all_idx in groups.items():
      all_idx = np.asarray(all_idx)
      for c0 in range(0, len(all_idx), CHUNK):
        idx = all_idx[c0:c0 + CHUNK]
        fname = f"b{int(b)}_{c0 // CHUNK}"
        out = {}
        for i in idx:
            e = {"base": jlist(res[models[0]]["base"][i, show0::24]),
                 "scs": {}, "rain": {}, "mono": {}, "rich": {}}
            for m in models:
                e["scs"][m] = jlist(res[m]["scs"][i, show0:])
                e["rain"][m] = jlist(res[m]["rain"][i, show0:], decimals=1)
                e["mono"][m] = jlist(res[m]["mononobe"][i], 3)
                e["rich"][m] = jlist(res[m]["richards"][i], 3)
            e["design"] = thr_table[i]
            e["monthly"] = {k: jlist(v[i]) for k, v in year.items()}   # January to December
            out[str(int(i))] = e
            grp[int(i)] = fname
        write_json(os.path.join(tmp, "series", fname + ".json"), out)
    summary["grp"] = grp
    write_json(os.path.join(tmp, "summary.json"), summary)

    # precipitation maps: 3-hourly sums at the nodes from the shown start
    step = 3
    n_steps = (h - show0) // step
    pm = {}
    for m in models:
        p = rain.precip[m][:, show0:show0 + n_steps * step]
        pm[m] = [jlist(p[:, k * step:(k + 1) * step].sum(axis=1), decimals=1)
                 for k in range(n_steps)]
    write_json(os.path.join(tmp, "rain_grid.json"), {
        "lat": net.nodes.lat.tolist(), "lon": net.nodes.lon.tolist(), "step_h": step,
        "start_utc": times[show0].strftime("%Y-%m-%dT%H:00Z"), "now_index": (i_now - show0) // step,
        "models": pm})

    # basins: catchment rain and the worst class
    basins = []
    for b, idx in groups.items():
        if int(b) == 0:
            continue
        idx = np.asarray(idx)
        big = idx[np.argmax(t.area_km2.to_numpy()[idx])]
        basins.append({"basin": int(b), "name": t.basin_name[big] or f"River system {int(b)}",
                       "area_km2": float(t.area_km2[big]), "outlet": int(big),
                       "rain72": {m: round(float(res[m]["precip"][big, i_now:i_now + 72].sum()), 1)
                                  for m in models},
                       "rain_total": {m: round(float(res[m]["precip"][big, fut].sum()), 1)
                                      for m in models},
                       "cls_max": int(np.max(cls_all[:, idx]))})
    basins.sort(key=lambda x: -x["area_km2"])

    # hotspots, most serious first: flood level reached, then how many models
    # agree on it, then how far the rise goes towards the first flood level,
    # then the soonest peak (see docs/operator-guide.md, "How the lists rank")
    prog_all = np.stack([np.asarray(summary["prog"][m], float) for m in models])
    lvl = cls_all.max(axis=0)
    agree = (cls_all >= np.maximum(lvl, 1)[None, :]).sum(axis=0)
    best = np.argmax(cls_all * 1000.0 + np.minimum(prog_all, 999.0), axis=0)
    lead = np.array([summary["tpeak"][models[b]][i] for i, b in enumerate(best)]) - (i_now - show0)
    prog_best = prog_all[best, np.arange(net.n)]
    eligible = t.kind.to_numpy() != "" if "kind" in t else (t.reach.to_numpy() >= 0)
    order = np.lexsort((lead, -prog_best, -agree, -lvl))
    hot = []
    seen = set()
    for i in order:
        if len(hot) >= 60 or (lvl[i] == 0 and prog_best[i] < 0.05):
            break
        key = (t.name[i], int(t.basin[i])) if t.name[i] else i     # one per named river
        if not eligible[i] or key in seen:
            continue
        seen.add(key)
        m = models[best[i]]
        hot.append({"t": int(i), "level": int(lvl[i]), "models_agree": int(agree[i]),
                    "progress": round(float(prog_best[i]), 2), "model": m,
                    "peak": summary["peak"][m][i], "tpeak": summary["tpeak"][m][i],
                    "lead_h": int(lead[i])})

    counts = {m: {str(T): int((np.asarray(summary["cls"][m]) >= T)[t.reach.to_numpy() >= 0].sum())
                  for T in rps} for m in models}
    latest = {
        "title": cfg["title"],
        "issued_utc": now.strftime("%Y-%m-%dT%H:%MZ"),
        "next_update_utc": (now + timedelta(hours=float(cfg["every_hours"]))).strftime("%Y-%m-%dT%H:%MZ"),
        "display_timezone": cfg["timezone"],
        "source": rain.source, "errors": errors,
        "models": [{"id": m, "name": names.get(m, m), **rain.info.get(m, {})} for m in models],
        "primary_method": primary,
        "baseflow": {"method": cfg["runoff"]["baseflow"],
                     "factor": float(cfg["runoff"]["baseflow_factor"])},
        "return_periods": rps,
        "threshold_statistic": cfg["thresholds"]["statistic"],
        "series_start_utc": times[show0].strftime("%Y-%m-%dT%H:00Z"),
        "now_index": i_now - show0, "n_hours": h - show0,
        "days": [d.isoformat() for d in days], "month": month,
        "counts": counts, "hotspots": hot, "basins": basins,
        "runtime_s": round(time.time() - t_start, 1),
    }
    write_json(os.path.join(tmp, "latest.json"), latest)
    shutil.rmtree(data + ".old", ignore_errors=True)
    if os.path.isdir(data):
        os.replace(data, data + ".old")
    os.replace(tmp, data)
    shutil.rmtree(data + ".old", ignore_errors=True)
    build_site(site_dir, static_dir)
    log(f"  site written to {site_dir} in {time.time() - t_start:.0f} s")
    return latest


def build_site(site_dir, static_dir):
    """Copy the page and the static layers next to the data."""
    os.makedirs(os.path.join(site_dir, "static"), exist_ok=True)
    for f in os.listdir(WEB):
        shutil.copy2(os.path.join(WEB, f), os.path.join(site_dir, f))
    for f in ("reaches.geojson", "basins.geojson", "nepal.geojson", "targets.csv",
              "stations.csv"):
        src = os.path.join(static_dir, f)
        if os.path.exists(src):
            shutil.copy2(src, os.path.join(site_dir, "static", f))
    open(os.path.join(site_dir, ".nojekyll"), "w").close()
