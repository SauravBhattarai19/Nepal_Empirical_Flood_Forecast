# -*- coding: utf-8 -*-
"""Rainfall climatology for the regional formulas: the monsoon wetness index
(MWI, mean June-September rain) and the annual wetness index (AWI, mean annual
rain), from ERA5 hourly large-scale + convective precipitation, 2019-2023, read
from the NSF NCAR ERA5 copy on AWS open data (no account needed).

ERA5 files are chunked as whole global fields, so each half-month file
(~270 MB) is downloaded, the Nepal box summed, and the file deleted."""

import os
import re
import subprocess
import sys
import time
import urllib.request
from concurrent.futures import ProcessPoolExecutor

import numpy as np

from .common import CACHE, log

BUCKET = "https://nsf-ncar-era5.s3.amazonaws.com"
LAT0, LAT1, LON0, LON1 = 32.25, 25.75, 78.75, 89.25        # ERA5 0.25 deg box, north first
YEARS = (2019, 2023)


def keys(yyyymm):
    x = urllib.request.urlopen(
        f"{BUCKET}/?list-type=2&prefix=e5.oper.fc.sfc.accumu/{yyyymm}/").read().decode()
    return [k for k in re.findall(r"<Key>([^<]+)</Key>", x)
            if re.search(r"_(lsp|cp)\.ll025sc", k)]


def _one(args):
    key, out_dir = args
    import h5py
    name = os.path.basename(key)
    out = os.path.join(out_dir, name.replace(".nc", ".npy"))
    if os.path.exists(out):
        return name, 0.0
    tmp = os.path.join(out_dir, name + ".part")
    for _ in range(3):
        try:
            t = time.time()
            subprocess.run(["curl", "-sS", "-f", "-o", tmp, f"{BUCKET}/{key}"], check=True)
            with h5py.File(tmp, "r") as h:
                var = [v for v in h if v in ("LSP", "CP")][0]
                lat, lon = h["latitude"][:], h["longitude"][:]
                i0, i1 = int(np.argmin(abs(lat - LAT0))), int(np.argmin(abs(lat - LAT1)))
                j0, j1 = int(np.argmin(abs(lon - LON0))), int(np.argmin(abs(lon - LON1)))
                d = h[var]
                acc = np.zeros((i1 - i0 + 1, j1 - j0 + 1))
                for t0 in range(d.shape[0]):
                    acc += np.nansum(d[t0, :, i0:i1 + 1, j0:j1 + 1], axis=0)
            os.remove(tmp)
            np.save(out, (acc * 1000.0).astype("float32"))          # m -> mm
            return name, time.time() - t
        except Exception as exc:                                      # retry
            print(f"retry {name}: {exc}", file=sys.stderr, flush=True)
            time.sleep(5)
    return name, -1.0


def download(out_dir=None, years=YEARS, workers=4):
    out_dir = out_dir or os.path.join(CACHE, "era5")
    os.makedirs(out_dir, exist_ok=True)
    ks = []
    for y in range(years[0], years[1] + 1):
        for m in range(1, 13):
            ks += keys(f"{y}{m:02d}")
    log(f"  ERA5: {len(ks)} files")
    with ProcessPoolExecutor(workers) as ex:
        for name, dt in ex.map(_one, [(k, out_dir) for k in ks]):
            log(f"  {name} {dt:.0f} s")
    return out_dir


def climatology(out_dir=None, years=YEARS):
    """-> (lat, lon, annual_mm, jjas_mm) means over *years* on the ERA5 grid."""
    out_dir = out_dir or os.path.join(CACHE, "era5")
    ann, jjas = 0.0, 0.0
    for y in range(years[0], years[1] + 1):
        for f in os.listdir(out_dir):
            m = re.search(r"\.(\d{4})(\d{2})\d{4}_\d{10}\.npy$", f)
            if not m or int(m[1]) != y:
                continue
            a = np.load(os.path.join(out_dir, f)).astype("f8")
            ann = ann + a
            if 6 <= int(m[2]) <= 9:
                jjas = jjas + a
    n = years[1] - years[0] + 1
    lat = np.arange(LAT0, LAT1 - 1e-9, -0.25)
    lon = np.arange(LON0, LON1 + 1e-9, 0.25)
    return lat, lon, ann / n, jjas / n


def run():
    download()
    lat, lon, ann, jjas = climatology()
    out = os.path.join(CACHE, "climate.npz")
    np.savez_compressed(out, lat=lat, lon=lon, annual=ann, jjas=jjas)
    log(f"  climate -> {out}")
    return out


if __name__ == "__main__":
    run()
