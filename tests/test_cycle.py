"""A whole update on the committed network with made-up rain (no internet)."""

import json
import os
from datetime import datetime, timedelta

import numpy as np
import pytest

from nepal_forecast import config, cycle
from nepal_forecast.sources import RainGrid

STATIC = os.path.join(os.path.dirname(os.path.dirname(__file__)), "static")
pytestmark = pytest.mark.skipif(not os.path.exists(os.path.join(STATIC, "targets.csv")),
                                reason="static network not built")


def synthetic(net, now, storm_mm):
    t0 = now.replace(minute=0) - timedelta(days=7)
    times = [t0 + timedelta(hours=k) for k in range(1, 14 * 24 + 1)]
    rain = np.zeros((len(net.nodes), len(times)))
    k0 = 7 * 24 + 30                                 # a storm a day after "now"
    rain[:, k0:k0 + 24] = storm_mm / 24.0
    return RainGrid(times=times, precip={"m1": rain}, liquid={"m1": rain}, source="test")


def test_cycle_writes_site(tmp_path):
    cfg = config.load("/nonexistent.yaml")
    net = cycle.Network(STATIC)
    now = datetime(2026, 8, 10, 6, 0)
    latest = cycle.run(cfg, STATIC, str(tmp_path), now=now, rain=synthetic(net, now, 250.0))
    d = tmp_path / "data"
    s = json.loads((d / "summary.json").read_text())
    assert len(s["peak"]["m1"]) == net.n
    assert latest["counts"]["m1"]["2"] > 0           # 250 mm in a day floods some rivers
    assert (tmp_path / "index.html").exists() and (tmp_path / "static" / "reaches.geojson").exists()
    g = json.loads((d / "rain_grid.json").read_text())
    assert len(g["lat"]) == len(net.nodes)
    series = json.loads(next((d / "series").glob("b*.json")).read_text())
    e = next(iter(series.values()))
    assert len(e["scs"]["m1"]) == latest["n_hours"]
    # the peak time indexes the shown series, and falls on the storm (now + 30-54 h), not later
    i = int(np.argmax(s["peak"]["m1"]))
    tp = s["tpeak"]["m1"][i]
    assert latest["now_index"] + 30 <= tp <= latest["now_index"] + 54 + 24
    assert s["peak"]["m1"][i] == pytest.approx(max(series_of(d, s, i)["scs"]["m1"]), rel=0.01)
    # the page's monthly-flow chart: twelve months by each formula, this month's matching the summary
    mo = series_of(d, s, i)["monthly"]
    assert sorted(mo) == sorted(s["normal"]) and all(len(v) == 12 for v in mo.values())
    for k, v in mo.items():
        assert v[latest["month"] - 1] == s["normal"][k][i]
    assert mo["WECS/DHM 1990"][7] > mo["WECS/DHM 1990"][1]      # August above February
    assert latest["baseflow"] == {"method": "WECS/DHM 1990", "factor": 1.0}


def series_of(d, s, i):
    return json.loads((d / "series" / (s["grp"][i] + ".json")).read_text())[str(i)]


def test_no_rain_stays_below_floods(tmp_path):
    cfg = config.load("/nonexistent.yaml")
    net = cycle.Network(STATIC)
    now = datetime(2026, 1, 10, 6, 0)
    latest = cycle.run(cfg, STATIC, str(tmp_path), now=now, rain=synthetic(net, now, 0.0))
    assert latest["counts"]["m1"]["2"] == 0          # dry season, no rain: no flood anywhere


def test_config_changes_take_effect(tmp_path):
    """The changes the operator guide promises work without touching code."""
    cfg = config.load("/nonexistent.yaml")
    cfg["runoff"]["primary"] = "rational"
    cfg["thresholds"]["return_periods"] = [2, 5, 25, 100]
    cfg["thresholds"]["statistic"] = "max"
    cfg["flood_levels"] = {"283.3": {2: 150.0, 100: 700.0}}
    net = cycle.Network(STATIC)
    now = datetime(2026, 8, 10, 6, 0)
    latest = cycle.run(cfg, STATIC, str(tmp_path), now=now, rain=synthetic(net, now, 250.0))
    s = json.loads((tmp_path / "data" / "summary.json").read_text())
    assert latest["primary_method"] == "rational" and latest["return_periods"] == [2, 5, 25, 100]
    assert s["peak"]["m1"] == s["rat_peak"]["m1"]          # rational colours the map
    i = int(net.targets.index[net.targets.station == "283.3"][0])
    assert s["q2"][i] == 150 and s["q100"][i] == 700 and s["q25"][i] > 150
    # ranking: level first, then agreement, then progress
    hot = latest["hotspots"]
    keys = [(-h["level"], -h["models_agree"], -h["progress"]) for h in hot]
    assert keys == sorted(keys)


def test_progress_ignores_normal_flow():
    import numpy as np
    from nepal_forecast.cycle import progress
    # a big river at its normal monsoon flow (no rise) scores 0, a small rising one more
    p = progress(np.array([900.0, 60.0]), np.array([0.0, 40.0]), np.array([1000.0, 100.0]))
    assert p[0] == 0 and p[1] == pytest.approx(40 / 80)


def test_every_reach_target_is_its_outlet():
    """Each reach is forecast at its outlet: never smaller than the reach above it."""
    reaches = json.loads(open(os.path.join(STATIC, "reaches.geojson")).read())["features"]
    import pandas as pd
    t = pd.read_csv(os.path.join(STATIC, "targets.csv"), dtype={"station": str}, keep_default_na=False)
    area_of_target = dict(zip(t.target, t.area_km2))
    by_id = {f["properties"]["id"]: f["properties"] for f in reaches}
    for p in by_id.values():
        if p["ds"] >= 0 and p["t"] >= 0 and by_id[p["ds"]]["t"] >= 0:
            assert area_of_target[p["t"]] < area_of_target[by_id[p["ds"]]["t"]]
