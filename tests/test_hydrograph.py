"""The rainfall -> discharge engine."""

import numpy as np
import pytest

from nepal_forecast import formulas as F
from nepal_forecast import hydrograph as H


@pytest.mark.parametrize("area,tc", [(20, 0.4), (199, 3.1), (5000, 30.0)])
def test_unit_hydrograph_volume(area, tc):
    uh = H.unit_hydrograph(area, tc)
    vol = uh.sum() * 3600.0 / H.SUB              # m3 from 1 mm of excess
    assert vol == pytest.approx(area * 1000.0, rel=1e-9)
    # SCS: peak near Tp = D/2 + 0.6 tc
    tp = 0.125 + 0.6 * tc
    assert abs((np.argmax(uh) + 1) / H.SUB - tp) <= max(0.5, 0.15 * tp)


def test_route_conserves_volume():
    ex = np.zeros((1, 400))
    ex[0, 10:14] = [5, 10, 3, 2]
    q = np.zeros(400 * H.SUB)
    uh = H.unit_hydrograph(300, 4.0)
    sub = np.repeat(ex[0] / H.SUB, H.SUB)
    q = np.convolve(sub, uh)[: 400 * H.SUB]
    assert q.sum() * 3600 / H.SUB == pytest.approx(ex.sum() * 300 * 1000, rel=1e-6)
    hourly = H.route(ex, np.array([300.0]), np.array([4.0]))
    assert hourly.max() == pytest.approx(q.max())     # the hourly series keeps the peak


def test_scs_single_event_matches_formula():
    rain = np.zeros((1, 200))
    rain[0, 130:140] = 15.0                          # 150 mm, no rain in the 5 days before
    ex = H.scs_excess(rain, np.array([75.0]))
    cn1 = F.cn_amc(75.0, 1)                         # dry antecedent -> AMC I
    assert ex.sum() == pytest.approx(float(F.scs_runoff(150.0, cn1)), rel=1e-9)


def test_scs_wet_antecedent_uses_amc3():
    rain = np.zeros((1, 300))
    rain[0, 100:110] = 8.0                           # 80 mm, event 1
    rain[0, 160:170] = 10.0                          # 100 mm, event 2 (50 h later, 80 mm in 5 days)
    ex = H.scs_excess(rain, np.array([75.0]), gap_h=12)
    e2 = ex[0, 150:].sum()
    assert e2 == pytest.approx(float(F.scs_runoff(100.0, F.cn_amc(75.0, 3))), rel=1e-9)


def test_rational_steady_state():
    rain = np.full((1, 48), 10.0)                    # 10 mm/h for two days
    q = H.rational_series(rain, np.array([0.3]), np.array([100.0]), np.array([5.0]))
    assert q[0, -1] == pytest.approx(0.3 * 10 * 100 / 3.6)


def test_classify():
    thr = np.array([[10, 20, 30, 40, 50, 60.0]])
    rps = [2, 5, 10, 20, 50, 100]
    assert H.classify(np.array([5.0]), thr, rps)[0] == 0
    assert H.classify(np.array([25.0]), thr, rps)[0] == 5
    assert H.classify(np.array([99.0]), thr, rps)[0] == 100
