# -*- coding: utf-8 -*-
"""Download the Copernicus GLO-90 DEM tiles and mosaic them at 6 arc-seconds
(about 185 m), the grid on which the river network is traced."""

import os
import urllib.request

import numpy as np

from .common import BOX, CACHE, RES_DEG, log

BUCKET = "https://copernicus-dem-90m.s3.amazonaws.com"


def tile_name(lat, lon):
    ns = "N" if lat >= 0 else "S"
    ew = "E" if lon >= 0 else "W"
    return f"Copernicus_DSM_COG_30_{ns}{abs(lat):02d}_00_{ew}{abs(lon):03d}_00_DEM"


def download(folder=None):
    folder = folder or os.path.join(CACHE, "dem")
    os.makedirs(folder, exist_ok=True)
    w, s, e, n = BOX
    paths = []
    for lat in range(int(np.floor(s)), int(np.ceil(n))):
        for lon in range(int(np.floor(w)), int(np.ceil(e))):
            name = tile_name(lat, lon)
            p = os.path.join(folder, name + ".tif")
            if not os.path.exists(p) or os.path.getsize(p) == 0:
                log(f"  downloading {name}")
                try:
                    urllib.request.urlretrieve(f"{BUCKET}/{name}/{name}.tif", p)
                except Exception as exc:  # sea tiles do not exist
                    log(f"  (no tile {name}: {exc})")
                    continue
            paths.append(p)
    return paths


def mosaic(paths, out=None):
    """Block-minimum the 3" tiles onto the 6" grid of BOX: the lowest 90 m cell
    keeps the floor of narrow gorges, where an average would dam the big rivers."""
    import rasterio
    out = out or os.path.join(CACHE, "dem6.npz")
    w, s, e, n = BOX
    per_deg = int(round(1.0 / RES_DEG))
    nrow, ncol = int(round((n - s) * per_deg)), int(round((e - w) * per_deg))
    dem = np.full((nrow, ncol), -9999.0, dtype="float32")
    for p in paths:
        with rasterio.open(p) as r:
            a = r.read(1).astype("float32")
            if r.nodata is not None:
                a[a == r.nodata] = np.nan
            west, north = r.bounds.left, r.bounds.top
        f = a.shape[1] // per_deg                     # 3" tiles: 2 cells per 6" cell
        a = a[: per_deg * f, : per_deg * f].reshape(per_deg, f, per_deg, f)
        with np.errstate(all="ignore"):
            m = np.nanmin(np.nanmin(a, axis=3), axis=1)
        m[~np.isfinite(m)] = -9999.0
        r0 = int(round((n - north) * per_deg))
        c0 = int(round((west - w) * per_deg))
        dem[r0:r0 + per_deg, c0:c0 + per_deg] = m
    transform = (RES_DEG, 0.0, w, 0.0, -RES_DEG, n)
    np.savez_compressed(out, dem=dem, transform=np.array(transform))
    log(f"  DEM mosaic {dem.shape} -> {out}")
    return out


def run():
    return mosaic(download())
