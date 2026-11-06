# -*- coding: utf-8 -*-
"""D8 flow directions from the 6" DEM and the upstream area of every cell.

Every depression is filled (priority-flood), because false "dams" left by the
DEM in narrow Himalayan gorges are often deeper than 100 m and would cut the
big rivers. The genuinely endorheic lake basins of the Tibetan plateau are then
cut out again: depressions at least ENDO_DEPTH_M deep whose floor lies above
ENDO_MIN_ELEV_M. Checked against the drainage areas of the 112 DHM stations
(see docs/verification.md)."""

import os

import numpy as np

from .common import CACHE, log

ENDO_DEPTH_M = 100.0
ENDO_MIN_ELEV_M = 4000.0
ENDO_MIN_KM2 = 20.0


def fill_voids(dem, nodata=-9999.0):
    from scipy import ndimage
    bad = dem == nodata
    if bad.any():
        idx = ndimage.distance_transform_edt(bad, return_distances=False, return_indices=True)
        dem = dem[tuple(idx)]
        log(f"  filled {int(bad.sum())} void cells from their nearest neighbour")
    return dem


def endorheic_mask(dem, transform):
    """Cells of the deep high-plateau depressions (True = drains nowhere)."""
    import pyflwdir
    flw = pyflwdir.from_dem(dem, nodata=-9999.0, transform=transform, latlon=True,
                            outlets="edge", max_depth=ENDO_DEPTH_M)
    upa = flw.upstream_area("km2")
    pits = flw.idxs_pit
    r, c = np.unravel_index(pits, dem.shape)
    edge = (r == 0) | (c == 0) | (r == dem.shape[0] - 1) | (c == dem.shape[1] - 1)
    keep = (~edge) & (dem.ravel()[pits] > ENDO_MIN_ELEV_M) & (upa.ravel()[pits] > ENDO_MIN_KM2)
    basins = flw.basins(idxs=pits[keep], ids=np.arange(1, keep.sum() + 1, dtype=np.int32))
    log(f"  {int(keep.sum())} endorheic plateau basins, {upa.ravel()[pits[keep]].sum():,.0f} km2 cut")
    return basins > 0


def run(dem_npz=None, out=None):
    import pyflwdir
    from affine import Affine
    dem_npz = dem_npz or os.path.join(CACHE, "dem6.npz")
    out = out or os.path.join(CACHE, "flow6.npz")
    d = np.load(dem_npz)
    dem = fill_voids(d["dem"])
    transform = Affine(*d["transform"][:6])
    log("  flow directions (priority-flood, all depressions filled)…")
    flw = pyflwdir.from_dem(dem, nodata=-9999.0, transform=transform, latlon=True,
                            outlets="edge", max_depth=-1)
    d8 = flw.to_array("d8")
    endo = endorheic_mask(dem, transform)
    d8[endo] = 0                                   # each such cell becomes its own pit
    flw = pyflwdir.from_array(d8, ftype="d8", transform=transform, latlon=True)
    upa = flw.upstream_area("km2").astype("float32")
    np.savez_compressed(out, d8=d8, dem=dem.astype("float32"), upa=upa, endo=endo,
                        transform=np.array(transform)[:6])
    log(f"  flow grid -> {out}; largest upstream area {upa.max():,.0f} km2")
    return out
