# -*- coding: utf-8 -*-
"""
The empirical flood and flow relations used in Nepali practice, each named by
its original source (WECS/DHM 1990, DHM 2004, MHSP 1997, WECS 2019, ...).
Areas are km2, discharges m3/s,
rainfall mm, times hours unless stated.

Three families:

* **Design floods** Q_T from catchment area (WECS/DHM 1990, MHSP 1997,
  DHM 2004, WECS 2019, Modified Dickens; Dickens, Ryves, Inglis). They need no rainfall, so the forecast uses them as *flood thresholds*
  ("2-year flood", "20-year flood", ...).
* **Rainfall-based peaks** (Rational, B.D. Richards, Mononobe,
  SCS curve number). These turn the *forecast* rainfall into discharge.
* **Mean monthly flows** (WECS/DHM 1990, NEA 1997, DHM 2004): the
  normal flow of the month, used as the base flow under each forecast.

Nothing here reads files or the network, so it can be tested on its own
(tests/test_formulas.py checks them against reference values).
"""

import math
from statistics import NormalDist

import numpy as np

RETURN_PERIODS = (2, 5, 10, 20, 50, 100)

# Standard normal variate S for return period T
S_TABLE = {2: 0.0, 5: 0.842, 10: 1.282, 20: 1.645, 50: 2.054, 100: 2.326, 200: 2.576, 500: 2.878}


def normal_variate(T):
    """S for return period T (other T from the normal distribution)."""
    if T in S_TABLE:
        return S_TABLE[T]
    return NormalDist().inv_cdf(1.0 - 1.0 / float(T))


def _lognormal(q2, q100, T):
    """Equations (3)-(4): Q_T = exp(ln Q2 + S sigma), sigma = ln(Q100/Q2)/2.326."""
    sigma = math.log(q100 / q2) / 2.326
    return math.exp(math.log(q2) + normal_variate(T) * sigma)


def _interp_in_S(table, T):
    """Interpolate ln Q linearly in the normal variate between tabulated T
    (used where a method gives only some return periods)."""
    ts = sorted(table)
    if T in table:
        return table[T]
    if T < ts[0] or T > ts[-1]:
        return None                                   # never extrapolate
    for a, b in zip(ts, ts[1:]):
        if a < T < b:
            sa, sb, s = normal_variate(a), normal_variate(b), normal_variate(T)
            la, lb = math.log(table[a]), math.log(table[b])
            return math.exp(la + (lb - la) * (s - sa) / (sb - sa))
    return None


# ── WECS/DHM 1990 (HYDEST) ─────────────────────────────────────────────────
def wecs_dhm_1990(a3000, T):
    """Instantaneous flood."""
    q2 = 1.8767 * (a3000 + 1.0) ** 0.8783
    q100 = 14.63 * (a3000 + 1.0) ** 0.7342
    return _lognormal(q2, q100, T)


def wecs_dhm_1990_daily(a3000, T):
    """Daily mean flood."""
    q2 = 0.8154 * (a3000 + 1.0) ** 0.9527
    q100 = 4.144 * (a3000 + 1.0) ** 0.8448
    return _lognormal(q2, q100, T)


# ── DHM 2004 (modified HYDEST) ─────────────────────────────────────────────
def dhm_2004(a3000, T):
    """Log-normal between Q2 and Q100, as WECS/DHM 1990."""
    a = max(a3000, 1e-6)
    return _lognormal(2.29 * a ** 0.86, 20.7 * a ** 0.72, T)


# ── MHSP 1997 (NEA) ────────────────────────────────────────────────────────
# T -> (k, b) per region. The last row is 10,000 years.
MHSP = {
    "western": {5: (2.0409, 0.8632), 20: (3.2895, 0.8510), 50: (4.2570, 0.8444),
                100: (5.2225, 0.8352), 1000: (9.2290, 0.8148), 10000: (14.4580, 0.8063)},
    "central": {5: (1.6762, 0.9660), 20: (3.2303, 0.9281), 50: (4.6090, 0.9071),
                100: (5.9865, 0.8888), 1000: (12.6603, 0.8429), 10000: (24.6431, 0.8019)},
    "eastern": {5: (7.4008, 0.7862), 20: (13.0848, 0.7535), 50: (17.6058, 0.7380),
                100: (21.5181, 0.7281), 1000: (39.9035, 0.6969), 10000: (69.7807, 0.6695)},
}


def mhsp_region(lon, west_of=83.0, east_of=86.0):
    """The MHSP region of a site from its longitude. No map of the three
    regions is used; the default split (west of 83 E: western; east of 86 E:
    eastern) follows the Karnali/Gandaki and Gandaki/Koshi divides roughly and
    can be changed in config.yaml."""
    if lon < west_of:
        return "western"
    if lon > east_of:
        return "eastern"
    return "central"


def mhsp_1997(area, T, region):
    """Q_T = k A^b. T between tabulated values is interpolated in S;
    T = 2 is outside the table and returns None."""
    tab = {t: k * area ** b for t, (k, b) in MHSP[region].items()}
    return _interp_in_S(tab, T)


# ── Specific discharge (WECS 2019) ─────────────────────────────────────────
def wecs_2019_specific(area, T):
    """Not applicable below 12 km2 (returns None)."""
    if area < 12.0:
        return None
    tab = {2: 4.2 * area ** -0.30, 25: 30.8 * area ** -0.44,
           50: 43.3 * area ** -0.46, 100: 61.4 * area ** -0.49}
    q = _interp_in_S({t: v * area for t, v in tab.items()}, T)
    return q


# ── Modified Dickens (IRI Roorkee) ─────────────────────────────────────────
def modified_dickens(area, T, snow_area=0.0):
    """snow_area a = permanent snow, taken as the area above
    about 5,000 m."""
    p = 100.0 * (snow_area + 6.0) / (area + snow_area)
    ct = 2.342 * math.log10(0.6 * T) * math.log10(1185.0 / p) + 4.0
    return ct * area ** 0.75


# ── Indian formulas: cross-checks only (no return period) ──────────────────
def dickens(area, cd=11.0):
    """C_D 11-14 for North Indian hilly regions."""
    return cd * area ** 0.75


def ryves(area, cr=10.2):
    """C_R 10.2 'limited areas near hills'; no Terai/Siwalik basis."""
    return cr * area ** (2.0 / 3.0)


def inglis(area):
    """Upper-bound check."""
    return 124.0 * area / math.sqrt(area + 10.4)


# ── Design floods of a site: every method, every return period ─────────────
DESIGN_METHODS = ("WECS/DHM 1990", "DHM 2004", "MHSP 1997", "WECS 2019", "Modified Dickens")


def design_floods(area, a3000, a5000, lon, return_periods=RETURN_PERIODS,
                  mhsp_split=(83.0, 86.0)):
    """{method: {T: Q_T or None}} for the area-based flood methods, plus
    'median', 'min', 'max' over the methods available for each T."""
    snow = max(area - a5000, 0.0)
    region = mhsp_region(lon, *mhsp_split)
    out = {m: {} for m in DESIGN_METHODS}
    for T in return_periods:
        out["WECS/DHM 1990"][T] = wecs_dhm_1990(a3000, T)
        out["DHM 2004"][T] = dhm_2004(a3000, T)
        out["MHSP 1997"][T] = mhsp_1997(area, T, region)
        out["WECS 2019"][T] = wecs_2019_specific(area, T)
        out["Modified Dickens"][T] = modified_dickens(area, T, snow)
    for stat, fn in (("median", np.median), ("min", np.min), ("max", np.max)):
        out[stat] = {}
        for T in return_periods:
            vals = [out[m][T] for m in DESIGN_METHODS if out[m][T] is not None]
            out[stat][T] = float(fn(vals)) if vals else None
    out["region"] = region
    return out


# ── Kirpich time of concentration ──────────────────────────────────────────
def kirpich_tc_hours(length_m, drop_m):
    """t_c = 0.00032 L^0.77 S^-0.385 (hours), S = H/L."""
    slope = max(drop_m, 1.0) / max(length_m, 1.0)
    return 0.00032 * length_m ** 0.77 * slope ** -0.385


# ── Rainfall-based peaks ───────────────────────────────────────────────────
RATIONAL_C = {  # by ESA WorldCover class
    10: 0.30, 20: 0.30, 30: 0.30, 90: 0.30, 95: 0.30,     # forest, shrub, grass, wetland
    40: 0.50,                                             # agriculture
    60: 0.25, 100: 0.25,                                  # barren
    50: 0.865,                                            # built-up
    70: 0.90,                                             # snow and glacier
    80: 1.00,                                             # open water: all rain runs off
}


def rational(c, intensity_mm_h, area):
    """Q = C I A / 3.6."""
    return c * intensity_mm_h * area / 3.6


def mononobe_intensity(r24, tc):
    """I_tc = (R24/24) (24/t_c)^(2/3) in mm/h."""
    return (r24 / 24.0) * (24.0 / max(tc, 0.1)) ** (2.0 / 3.0)


def mononobe_rational(c, r24, tc, area):
    """Rational peak with the Mononobe intensity of the day's rain."""
    return rational(c, mononobe_intensity(r24, tc), area)


def richards(r24, tc, area):
    """Q = 0.222 A I F, F = 1.09352 - 0.066628 ln A,
    I = R_tc / t_c, R_tc = 0.22127 R_T t_c^0.476577 (R_T: 24-hour rain).
    t_c is from Kirpich here (the iterative t_c of the method is not
    reproduced); the method is meant for t_c up to 24 h, so t_c is capped."""
    tc = min(max(tc, 0.1), 24.0)
    f = max(1.09352 - 0.066628 * math.log(area), 0.0)
    r_tc = 0.22127 * r24 * tc ** 0.476577
    return 0.222 * area * (r_tc / tc) * f


# ── SCS curve number ───────────────────────────────────────────────────────
def scs_retention(cn):
    """S = 25400/CN - 254 (mm)."""
    return 25400.0 / cn - 254.0


def scs_runoff(p, cn, ia_ratio=0.2):
    """Direct runoff depth (mm) from rain depth p (mm)."""
    s = scs_retention(cn)
    ia = ia_ratio * s
    p = np.asarray(p, dtype=float)
    return np.where(p > ia, (p - ia) ** 2 / np.maximum(p - ia + s, 1e-9), 0.0)


def cn_amc(cn2, amc):
    """Curve number for antecedent moisture condition I, II or III
    (Chow, Maidment and Mays 1988, Eqs. 5.5.4-5.5.5)."""
    if amc == 1:
        return 4.2 * cn2 / (10.0 - 0.058 * cn2)
    if amc == 3:
        return 23.0 * cn2 / (10.0 + 0.13 * cn2)
    return cn2


def amc_class(p5_mm, dry=35.6, wet=53.3):
    """AMC from the 5-day antecedent rain (USDA NEH-4, growing season)."""
    return 1 if p5_mm < dry else (3 if p5_mm > wet else 2)


# ── Mean monthly flows ─────────────────────────────────────────────────────
# WECS/DHM 1990, month -> (C, A1, A2, A3)
WECS_DHM_1990_MONTHLY = {
    1: (0.0142, 0.0, 0.9777, 0.0), 2: (0.0122, 0.0, 0.9766, 0.0),
    3: (0.0100, 0.0, 0.9948, 0.0), 4: (0.0080, 0.0, 1.0435, 0.0),
    5: (0.0084, 0.0, 1.0898, 0.0), 6: (0.0069, 0.9968, 0.0, 0.2610),
    7: (0.0212, 0.0, 1.0093, 0.2523), 8: (0.0255, 0.0, 0.9963, 0.2620),
    9: (0.0168, 0.0, 0.9894, 0.2878), 10: (0.0097, 0.0, 0.9880, 0.2508),
    11: (0.0018, 0.9605, 0.0, 0.3910), 12: (0.0015, 0.9605, 0.0, 0.3607),
}
# NEA 1997 (MHSP), month -> (C, A1, A2)
NEA_1997_MONTHLY = {
    1: (0.0312, 0.8644, 0.0), 2: (0.0242, 0.8752, 0.0), 3: (0.0205, 0.8902, 0.0),
    4: (0.0178, 0.9558, 0.0), 5: (0.0119, 0.9657, 0.0), 6: (0.0114, 0.9466, 0.2402),
    7: (0.0164, 0.9216, 0.3534), 8: (0.0259, 0.9095, 0.3242), 9: (0.0221, 0.8963, 0.3217),
    10: (0.0150, 0.8772, 0.2848), 11: (0.0079, 0.8804, 0.2707), 12: (0.0054, 0.8890, 0.2580),
}
# DHM 2004, month -> ("ln", C, eps, rho, mu) or ("sqrt", C, delta)
DHM_2004_MONTHLY = {
    1: ("ln", -16.7, 1.36, 0.470, 0.820), 2: ("ln", -17.2, 1.42, 0.456, 0.814),
    3: ("sqrt", 0.384, 0.091), 4: ("sqrt", 0.181, 0.104), 5: ("sqrt", 0.001, 0.136),
    6: ("ln", -19.5, 1.61, 0.709, 0.872), 7: ("ln", -16.3, 1.26, 0.759, 0.884),
    8: ("ln", -14.7, 1.24, 0.622, 0.871), 9: ("ln", -13.7, 1.09, 0.594, 0.872),
    10: ("ln", -15.3, 1.21, 0.600, 0.846), 11: ("ln", -16.7, 1.36, 0.543, 0.826),
    12: ("ln", -17.0, 1.39, 0.504, 0.822),
}


def monthly_wecs_dhm_1990(month, area, a5000, mwi):
    """Q = C A^A1 (A5000 + 1)^A2 MWI^A3."""
    c, a1, a2, a3 = WECS_DHM_1990_MONTHLY[month]
    return c * area ** a1 * (a5000 + 1.0) ** a2 * max(mwi, 1.0) ** a3


def monthly_nea_1997(month, area, mwi):
    """Q = C A^A1 MWI^A2."""
    c, a1, a2 = NEA_1997_MONTHLY[month]
    return c * area ** a1 * max(mwi, 1.0) ** a2


def monthly_dhm_2004(month, a3000, a5000, avg_elev, awi):
    """DHM 2004 mean monthly flow."""
    row = DHM_2004_MONTHLY[month]
    if row[0] == "sqrt":
        _, c, delta = row
        return max(c + delta * math.sqrt(max(a5000, 0.0)), 0.0) ** 2
    _, c, eps, rho, mu = row
    return math.exp(c + eps * math.log(max(avg_elev, 1.0)) + rho * math.log(max(awi, 1.0))
                    + mu * math.log(max(a3000, 1.0)))


def monthly_flows(month, area, a3000, a5000, avg_elev, mwi, awi):
    """{method: Q_mean} for one month."""
    return {"WECS/DHM 1990": monthly_wecs_dhm_1990(month, area, a5000, mwi),
            "NEA 1997": monthly_nea_1997(month, area, mwi),
            "DHM 2004": monthly_dhm_2004(month, a3000, a5000, avg_elev, awi)}


def environmental_flow(q_min_month):
    """Q_e >= 0.10 Q_min."""
    return 0.10 * q_min_month
