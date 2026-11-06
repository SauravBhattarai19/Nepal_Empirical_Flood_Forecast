"""The Open-Meteo client on responses shaped like the API's (no internet)."""

import pandas as pd
import pytest

from nepal_forecast import config
from nepal_forecast.sources import FeedError, open_meteo

MODELS = ["ecmwf_ifs025", "gfs_seamless", "icon_seamless"]


def fake_item(n=48, rain=1.0, snow=0.0, missing=None):
    t0 = 1793404800                                   # 2026-10-31 00:00 UTC
    h = {"time": [t0 + 3600 * k for k in range(n)]}
    for m in MODELS:
        h[f"precipitation_{m}"] = [None if m == missing else rain] * n
        h[f"snowfall_{m}"] = [None if m == missing else snow] * n
    return {"latitude": 28.0, "longitude": 84.0, "hourly": h}


def test_parse_multi_location(monkeypatch):
    calls = []

    def fake_get_json(url, **kw):
        calls.append(url)
        n = url.split("latitude=")[1].split("&")[0].count(",") + 1
        return [fake_item(rain=2.0, snow=0.7) for _ in range(n)] if n > 1 else fake_item(rain=2.0, snow=0.7)

    monkeypatch.setattr(open_meteo, "get_json", fake_get_json)
    cfg = config.load("/nonexistent.yaml")
    cfg["open_meteo"]["chunk"] = 2
    cfg["open_meteo"]["pause_s"] = 0
    nodes = pd.DataFrame({"lat": [27.0, 27.25, 27.5], "lon": [85.0, 85.0, 85.0]})
    g = open_meteo.fetch(nodes, cfg, log=lambda *_: None)
    assert len(calls) == 2                              # 2 + 1 locations
    assert "models=ecmwf_ifs025,gfs_seamless,icon_seamless" in calls[0]
    assert "past_days=7" in calls[0] and "forecast_days=7" in calls[0]
    assert set(g.models()) == set(MODELS)
    assert g.precip["ecmwf_ifs025"].shape == (3, 48)
    # 0.7 cm of snow = 1 mm of water: 2 mm precipitation -> 1 mm of rain
    assert g.liquid["gfs_seamless"][0, 0] == pytest.approx(1.0)
    assert g.times[0].hour == 0                         # stamped at the end of the hour summed


def test_model_without_data_is_left_out(monkeypatch):
    monkeypatch.setattr(open_meteo, "get_json", lambda url, **kw: fake_item(missing="icon_seamless"))
    cfg = config.load("/nonexistent.yaml")
    cfg["open_meteo"]["pause_s"] = 0
    g = open_meteo.fetch(pd.DataFrame({"lat": [27.0], "lon": [85.0]}), cfg, log=lambda *_: None)
    assert "icon_seamless" not in g.models() and "ecmwf_ifs025" in g.models()


def test_api_error(monkeypatch):
    monkeypatch.setattr(open_meteo, "get_json", lambda url, **kw: {"error": True, "reason": "Daily limit exceeded"})
    cfg = config.load("/nonexistent.yaml")
    with pytest.raises(FeedError, match="Daily limit"):
        open_meteo.fetch(pd.DataFrame({"lat": [27.0], "lon": [85.0]}), cfg, log=lambda *_: None)
