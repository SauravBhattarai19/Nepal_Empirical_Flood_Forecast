# -*- coding: utf-8 -*-
"""
Forecast rainfall -> discharge at every forecast point, with the rainfall-based
methods used in Nepali practice. All arrays are (points, hours), vectorised
over the points.

SCS curve number + SCS unit hydrograph  (the main method)
    Storm events are split by ``event_gap_h`` dry hours. Each event gets the
    curve number of its antecedent moisture condition (AMC I/II/III from the
    5-day rain before it). The cumulative event rain gives the cumulative
    runoff depth; its hourly increments are routed with the SCS
    dimensionless unit hydrograph, lag = 0.6 t_c (Kirpich), on a
    15-minute step so that small, flashy catchments keep their peaks.
Rational method
    Q = C i_tc A / 3.6, i_tc being the mean rain intensity over the last t_c.
Mononobe-Rational and B.D. Richards
    From each day's 24-hour rain: one peak per day.

The base flow under every method is the normal flow of the month
(WECS/DHM 1990 by default), interpolated between mid-month values.
"""

import calendar
from datetime import datetime, timedelta

import numpy as np

from . import formulas as F

# SCS dimensionless unit hydrograph (USDA NEH-4 / NEH-630 Ch. 16, Table 16-1)
_T_TP = np.array([0, .1, .2, .3, .4, .5, .6, .7, .8, .9, 1.0, 1.1, 1.2, 1.3, 1.4, 1.5, 1.6,
                  1.8, 2.0, 2.2, 2.4, 2.6, 2.8, 3.0, 3.2, 3.4, 3.6, 3.8, 4.0, 4.5, 5.0])
_Q_QP = np.array([0, .03, .10, .19, .31, .47, .66, .82, .93, .99, 1.0, .99, .93, .86, .78,
                  .68, .56, .39, .28, .207, .147, .107, .077, .055, .04, .029, .021, .015,
                  .011, .005, 0])

SUB = 4                       # sub-steps per hour (15 minutes)


def unit_hydrograph(area_km2, tc_h, lag_ratio=0.6):
    """SCS UH for 1 mm of excess spread over one 15-minute step, sampled at
    15-minute steps (m3/s per mm). Its volume is exactly 1 mm over the area."""
    dt = 1.0 / SUB
    tp = dt / 2.0 + lag_ratio * max(tc_h, 0.05)
    t = np.arange(1, int(np.ceil(5.0 * tp / dt)) + 2) * dt
    # integrate the curve over each step for volume conservation
    fine = np.linspace(0, t[-1], len(t) * 10 + 1)
    q = np.interp(fine / tp, _T_TP, _Q_QP, right=0.0)
    cum = np.concatenate([[0.0], np.cumsum((q[1:] + q[:-1]) / 2 * np.diff(fine))])
    blocks = np.interp(np.arange(len(t) + 1) * dt, fine, cum)
    uh = np.diff(blocks)
    uh = uh / uh.sum() * (area_km2 * 1000.0 / (dt * 3600.0))   # 1 mm over A in one step
    return uh


def scs_excess(rain, cn2, gap_h=12, dry_mm_h=0.2, amc_dry=35.6, amc_wet=53.3, ia_ratio=0.2):
    """Hourly runoff excess (mm) of an event-based SCS-CN with AMC per event.
    rain: (n, h) mm per hour; cn2: (n,) AMC II curve numbers."""
    n, h = rain.shape
    excess = np.zeros_like(rain)
    active = np.zeros(n, bool)
    dryrun = np.full(n, gap_h, dtype=int)
    cum_p = np.zeros(n)
    cum_q = np.zeros(n)
    s = np.ones(n)
    csum = np.concatenate([np.zeros((n, 1)), np.cumsum(rain, axis=1)], axis=1)
    cn1, cn3 = F.cn_amc(cn2, 1), F.cn_amc(cn2, 3)
    amc_used = np.zeros((n, h), np.int8)
    for t in range(h):
        r = rain[:, t]
        wet = r >= dry_mm_h
        start = wet & ~active
        if start.any():
            p5 = csum[start, t] - csum[start, max(t - 120, 0)]
            cn = np.where(p5 < amc_dry, cn1[start], np.where(p5 > amc_wet, cn3[start], cn2[start]))
            s[start] = 25400.0 / np.minimum(cn, 99.0) - 254.0
            cum_p[start] = 0.0
            cum_q[start] = 0.0
            active |= start
        on = active
        cum_p[on] += r[on]
        ia = ia_ratio * s[on]
        q = np.where(cum_p[on] > ia, (cum_p[on] - ia) ** 2 / (cum_p[on] - ia + s[on]), 0.0)
        excess[on, t] = q - cum_q[on]
        cum_q[on] = q
        amc_used[on, t] = np.where(s[on] > 0, 1, 0)
        dryrun = np.where(wet, 0, dryrun + 1)
        active &= dryrun < gap_h
    return excess


def route(excess, area, tc, lag_ratio=0.6):
    """Direct runoff (m3/s), hourly: the peak of the four 15-minute values in
    each hour (instantaneous peaks are what the design floods describe)."""
    n, h = excess.shape
    out = np.zeros((n, h))
    for i in range(n):
        uh = unit_hydrograph(area[i], tc[i], lag_ratio)
        sub = np.repeat(excess[i] / SUB, SUB)
        q = np.convolve(sub, uh)[: h * SUB]
        out[i] = q.reshape(h, SUB).max(axis=1)
    return out


def rational_series(rain, c, area, tc):
    """Q(t) = C i_tc(t) A / 3.6 with i_tc the mean intensity over the last
    max(1 h, t_c)."""
    n, h = rain.shape
    csum = np.concatenate([np.zeros((n, 1)), np.cumsum(rain, axis=1)], axis=1)
    win = np.clip(np.round(tc).astype(int), 1, h)
    idx = np.arange(1, h + 1)[None, :]
    lo = np.maximum(idx - win[:, None], 0)
    depth = np.take_along_axis(csum, idx.repeat(n, 0), 1) - np.take_along_axis(csum, lo, 1)
    i_tc = depth / win[:, None]
    return c[:, None] * i_tc * area[:, None] / 3.6


def daily_totals(rain, times, tz_offset_h=5.75):
    """Local-day rain totals: (days list, (n, d) mm)."""
    local = [t + timedelta(hours=tz_offset_h) for t in times]
    days = sorted({t.date() for t in local})
    tot = np.zeros((rain.shape[0], len(days)))
    pos = {d: k for k, d in enumerate(days)}
    for j, t in enumerate(local):
        tot[:, pos[t.date()]] += rain[:, j]
    return days, tot


def baseflow(times, desc, method="WECS/DHM 1990", factor=1.0):
    """Normal flow of the month under each point, (n, h), interpolated linearly
    between mid-month values."""
    n = len(desc["area_km2"])
    t0, t1 = times[0], times[-1]
    anchors, months = [], []
    y, m = t0.year, t0.month - 1
    if m == 0:
        y, m = y - 1, 12
    while True:
        mid = datetime(y, m, 15, 12)
        anchors.append(mid)
        months.append(m)
        if mid > t1:
            break
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    vals = np.zeros((n, len(months)))
    for k, mo in enumerate(months):
        for i in range(n):
            q = F.monthly_flows(mo, desc["area_km2"][i], desc["a3000_km2"][i],
                                desc["a5000_km2"][i], desc["elev_mean_m"][i],
                                desc["mwi_mm"][i], desc["awi_mm"][i])
            vals[i, k] = q[method]
    xa = np.array([(a - t0).total_seconds() for a in anchors])
    xt = np.array([(t - t0).total_seconds() for t in times])
    out = np.stack([np.interp(xt, xa, vals[i]) for i in range(n)])
    return out * factor


MONTHLY_METHODS = ("WECS/DHM 1990", "NEA 1997", "DHM 2004")


def month_normals(month, desc):
    """{method: (n,) normal flow of *month*} for the page."""
    n = len(desc["area_km2"])
    res = {k: np.zeros(n) for k in MONTHLY_METHODS}
    for i in range(n):
        q = F.monthly_flows(month, desc["area_km2"][i], desc["a3000_km2"][i],
                            desc["a5000_km2"][i], desc["elev_mean_m"][i],
                            desc["mwi_mm"][i], desc["awi_mm"][i])
        for k in res:
            res[k][i] = q[k]
    return res


def year_normals(desc):
    """{method: (n, 12)} mean flow of every month, January to December, for the
    page's monthly-flow chart."""
    by_month = [month_normals(mo, desc) for mo in range(1, 13)]
    return {k: np.stack([bm[k] for bm in by_month], axis=1) for k in MONTHLY_METHODS}


def simulate(rain, times, desc, cfg):
    """All methods for one weather model.

    rain: (n, h) liquid catchment rain, mm per hour; times: hourly UTC datetimes.
    Returns a dict of arrays: 'scs', 'rational', 'base' (n, h) and the daily
    'mononobe', 'richards' (n, d) with 'days'."""
    ro = cfg["runoff"]
    area = np.asarray(desc["area_km2"], float)
    tc = np.asarray(desc["tc_h"], float)
    c = np.asarray(desc["c_rational"], float)
    cn2 = np.asarray(desc["cn2"], float)
    base = baseflow(times, desc, ro["baseflow"], ro["baseflow_factor"])
    ex = scs_excess(rain, cn2, ro["event_gap_h"], ro["dry_mm_h"], ro["amc_dry_mm"],
                    ro["amc_wet_mm"], ro["ia_ratio"])
    scs = route(ex, area, tc, ro["lag_ratio"]) + base
    rat = rational_series(rain, c, area, tc) + base
    days, r24 = daily_totals(rain, times)
    # base flow of each local day: its mean
    local_day = [(t + timedelta(hours=5.75)).date() for t in times]
    bday = np.stack([base[:, [j for j, d in enumerate(local_day) if d == day]].mean(axis=1)
                     for day in days], axis=1)
    mono = np.zeros_like(r24)
    rich = np.zeros_like(r24)
    for i in range(len(area)):
        for k in range(len(days)):
            mono[i, k] = F.mononobe_rational(c[i], r24[i, k], tc[i], area[i])
            rich[i, k] = F.richards(r24[i, k], tc[i], area[i])
    return {"scs": scs, "rational": rat, "base": base, "excess": ex, "days": days,
            "rain24": r24, "mononobe": mono + bday, "richards": rich + bday}


def classify(peak, thresholds, return_periods):
    """Highest return period reached by *peak* (0: below the smallest).
    thresholds: (n, len(return_periods))."""
    cls = np.zeros(len(peak), int)
    for k, T in enumerate(return_periods):
        cls = np.where(np.isfinite(thresholds[:, k]) & (peak >= thresholds[:, k]), T, cls)
    return cls


def days_in_month(y, m):
    return calendar.monthrange(y, m)[1]
