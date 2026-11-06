"""The formulas against their published closed forms."""

import math

import pytest

from nepal_forecast import formulas as F

AREAS = (200.0, 500.0, 1000.0)


@pytest.mark.parametrize("a", AREAS)
def test_q100_closed_forms(a):
    """Each 100-year flood against its published closed form."""
    assert math.isclose(F.dhm_2004(a, 100), 20.7 * a ** 0.72, rel_tol=1e-3)
    assert math.isclose(F.mhsp_1997(a, 100, "western"), 5.2225 * a ** 0.8352, rel_tol=1e-6)
    assert math.isclose(F.wecs_2019_specific(a, 100), 61.4 * a ** -0.49 * a, rel_tol=1e-6)
    p = 100.0 * 6.0 / a
    ct = 2.342 * math.log10(60.0) * math.log10(1185.0 / p) + 4.0
    assert math.isclose(F.modified_dickens(a, 100), ct * a ** 0.75, rel_tol=1e-9)
    assert math.isclose(F.dickens(a, 11), 11 * a ** 0.75)
    assert math.isclose(F.inglis(a), 124.0 * a / math.sqrt(a + 10.4))


def test_normal_variate():
    for T, s in F.S_TABLE.items():
        assert F.normal_variate(T) == s
    assert abs(F.normal_variate(25) - 1.751) < 1e-3


def test_lognormal_endpoints():
    a = 300.0
    assert math.isclose(F.wecs_dhm_1990(a, 2), 1.8767 * 301 ** 0.8783)
    assert math.isclose(F.wecs_dhm_1990(a, 100), 14.63 * 301 ** 0.7342, rel_tol=1e-3)


def test_design_flood_order():
    d = F.design_floods(600, 600, 600, 80.9)
    med = [d["median"][T] for T in F.RETURN_PERIODS]
    assert all(b > a for a, b in zip(med, med[1:]))
    assert d["MHSP 1997"][2] is None            # outside the MHSP table
    assert d["region"] == "western"


def test_specific_discharge_small_area():
    assert F.wecs_2019_specific(10.0, 100) is None


def test_kirpich_minutes_form():
    # 0.00032 h = 0.0195 min / 60 (the minutes form of Kirpich)
    tc = F.kirpich_tc_hours(10000, 100)
    assert math.isclose(tc * 60, 0.0195 * 10000 ** 0.77 * 0.01 ** -0.385, rel_tol=0.02)


def test_scs_runoff():
    cn = 75.0
    s = 25400 / cn - 254
    assert F.scs_runoff(0.2 * s - 1, cn) == 0
    p = 150.0
    q = float(F.scs_runoff(p, cn))
    assert math.isclose(q, (p - 0.2 * s) ** 2 / (p + 0.8 * s))
    assert F.cn_amc(75, 3) > 75 > F.cn_amc(75, 1)


def test_richards_areal_factor_positive():
    # one source prints 0.6628 ln A, which goes negative; the correct value is 0.066628
    assert F.richards(100, 3, 600) > 0


def test_monthly_flows_positive():
    for m in range(1, 13):
        for v in F.monthly_flows(m, 600, 600, 600, 600, 1500, 2000).values():
            assert v > 0
