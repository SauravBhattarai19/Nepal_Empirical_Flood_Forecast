# -*- coding: utf-8 -*-
"""ESA WorldCover 2021 (10 m, AWS open data) sampled onto the 6" grid from the
COG overviews, then turned into the runoff coefficient C of the Rational method
(by land-cover class) and an SCS curve number (AMC II).

The curve numbers assume hydrologic soil group C (USDA TR-55), because no soil
map is used yet: a deliberate, conservative (wetter) choice for flood warning.
Both tables can be changed in config.yaml without re-running the preparation."""

import os

import numpy as np

from .common import BOX, CACHE, RES_DEG, log

URL = ("https://esa-worldcover.s3.eu-central-1.amazonaws.com/v200/2021/map/"
       "ESA_WorldCover_10m_2021_v200_{tile}_Map.tif")

# WorldCover class -> name
CLASSES = {10: "tree cover", 20: "shrubland", 30: "grassland", 40: "cropland",
           50: "built-up", 60: "bare / sparse", 70: "snow and ice", 80: "water",
           90: "herbaceous wetland", 95: "mangroves", 100: "moss and lichen"}


def tiles():
    w, s, e, n = BOX
    out = []
    for lat in range(int(np.floor(s / 3) * 3), int(np.ceil(n)), 3):
        for lon in range(int(np.floor(w / 3) * 3), int(np.ceil(e)), 3):
            out.append((lat, lon, f"N{lat:02d}E{lon:03d}"))
    return out


def run(out=None):
    import rasterio
    from rasterio.enums import Resampling
    from rasterio.windows import from_bounds
    os.environ.setdefault("GDAL_DISABLE_READDIR_ON_OPEN", "EMPTY_DIR")
    if os.path.exists("/root/.ccr/ca-bundle.crt"):
        os.environ.setdefault("CURL_CA_BUNDLE", "/root/.ccr/ca-bundle.crt")
    out = out or os.path.join(CACHE, "landcover6.npz")
    w, s, e, n = BOX
    per_deg = int(round(1 / RES_DEG))
    lc = np.zeros((int((n - s) * per_deg), int((e - w) * per_deg)), dtype="uint8")
    for lat, lon, tile in tiles():
        url = "/vsicurl/" + URL.format(tile=tile)
        bw, bs, be, bn = max(lon, w), max(lat, s), min(lon + 3, e), min(lat + 3, n)
        if bw >= be or bs >= bn:
            continue
        try:
            with rasterio.open(url) as r:
                win = from_bounds(bw, bs, be, bn, r.transform)
                shape = (int(round((bn - bs) * per_deg)), int(round((be - bw) * per_deg)))
                a = r.read(1, window=win, out_shape=shape, resampling=Resampling.nearest)
        except Exception as exc:
            log(f"  no WorldCover tile {tile}: {exc}")
            continue
        r0, c0 = int(round((n - bn) * per_deg)), int(round((bw - w) * per_deg))
        lc[r0:r0 + a.shape[0], c0:c0 + a.shape[1]] = a
        log(f"  WorldCover {tile} {a.shape}")
    np.savez_compressed(out, lc=lc)
    log(f"  land cover -> {out}")
    return out


if __name__ == "__main__":
    run()
