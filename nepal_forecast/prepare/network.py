# -*- coding: utf-8 -*-
"""
The river network of Nepal and what the formulas need at every point of it.

From the flow grid (flow.py), the land cover (landcover.py) and the rainfall
climatology (climate.py) this writes, into ``static/``:

``reaches.geojson``     every river reach of Nepal draining >= 20 km2, with its id,
                        downstream reach, upstream area, name and basin
``targets.csv``         one row per forecast point (each reach outlet and each DHM
                        station): the catchment descriptors of the formulas
``rain_weights.npz``    for each forecast point, the share of its catchment nearest
                        each 0.25 deg rain-grid node (the forecast rain is averaged
                        over the catchment with these weights)
``rain_nodes.csv``      the rain-grid nodes to download
``basins.geojson``      the river systems that leave Nepal (>= 100 km2)
``stations.csv``        the DHM stations, as snapped, with traced vs published area
``nepal.geojson``       the national boundary (Natural Earth)

Every descriptor is summed over a target's *own* cells first (the cells whose
first target downstream it is) and then along the target graph, so the whole
country takes a minute.
"""

import json
import os

import numpy as np
import pandas as pd

from .common import CACHE, MIN_RIVER_KM2, log

# Major basins that no DHM station names:
# (name, outlet longitude range, approximate traced area km2, for matching)
MAJOR_BASINS = [("Mahakali", 79.9, 80.6, 16200.0), ("Kamala", 85.9, 86.5, 2100.0),
                 ("Mechi", 87.95, 88.3, 540.0)]

CLIMATE_FILE = "climate_era5_2019_2023.npz"   # small (0.25 deg) grid, kept in static/

NODE_DEG = 0.25                     # the ECMWF IFS and GFS 0.25 deg grid
NODE_LAT0, NODE_LON0 = 25.75, 78.75
NE_URL = ("https://raw.githubusercontent.com/nvkelso/natural-earth-vector/master/"
          "geojson/ne_10m_admin_0_countries.geojson")


# ── helpers ────────────────────────────────────────────────────────────────
def nepal_polygon(cache=CACHE):
    import geopandas as gpd
    p = os.path.join(cache, "nepal.geojson")
    if not os.path.exists(p):
        import urllib.request
        src = os.path.join(cache, "ne10.geojson")
        if not os.path.exists(src):
            urllib.request.urlretrieve(NE_URL, src)
        ne = gpd.read_file(src)
        ne[ne.ADMIN == "Nepal"][["geometry"]].to_file(p, driver="GeoJSON")
    return gpd.read_file(p).geometry.iloc[0]


def _longest_path(seq, ds, z, nrow, ncol, res, lat_top):
    """Longest flow length (m) upstream of every cell and the elevation of the
    cell where that path starts (numba, upstream -> downstream)."""
    import numba

    @numba.njit(cache=False)
    def run(seq, ds, z, nrow, ncol, res, lat_top):
        n = z.size
        L = np.zeros(n, np.float64)
        zs = z.astype(np.float64).copy()
        for k in range(seq.size - 1, -1, -1):
            i = seq[k]
            j = ds[i]
            if j == i:
                continue
            ri, ci = i // ncol, i % ncol
            rj, cj = j // ncol, j % ncol
            lat = lat_top - (ri + 0.5) * res
            dy = (rj - ri) * res * 111195.0
            dx = (cj - ci) * res * 111195.0 * np.cos(np.radians(lat))
            cand = L[i] + np.sqrt(dx * dx + dy * dy)
            if cand > L[j]:
                L[j] = cand
                zs[j] = zs[i]
        return L, zs

    return run(seq, ds, z, nrow, ncol, res, lat_top)


def _snap(upa, transform, lat, lon, area=None, radius=10):
    """The cell near (lat, lon) whose upstream area best matches *area*
    (or the largest upstream area when no area is given)."""
    r = int((lat - transform[5]) / transform[4])
    c = int((lon - transform[2]) / transform[0])
    win = upa[r - radius:r + radius + 1, c - radius:c + radius + 1]
    yy, xx = np.mgrid[-radius:radius + 1, -radius:radius + 1]
    dist = np.hypot(yy, xx)
    if area:
        score = np.abs(np.log(np.maximum(win, 1e-3) / area)) + 0.02 * dist
    else:
        score = -np.log(np.maximum(win, 1e-3)) + 0.3 * dist
    k = np.unravel_index(np.argmin(score), win.shape)
    rr, cc = r + k[0] - radius, c + k[1] - radius
    return rr * upa.shape[1] + cc, float(upa[rr, cc]), float(dist[k])


# ── main ───────────────────────────────────────────────────────────────────
def run(static_dir, stations_csv, cache=CACHE, extra_points=(), cn_table=None, c_table=None):
    import geopandas as gpd
    import pyflwdir
    from affine import Affine
    from shapely.geometry import shape, mapping

    from ..formulas import RATIONAL_C, kirpich_tc_hours, mhsp_region
    from .landcover import CLASSES  # noqa: F401  (documented classes)

    os.makedirs(static_dir, exist_ok=True)
    f = np.load(os.path.join(cache, "flow6.npz"))
    T = Affine(*f["transform"][:6])
    dem, upa = f["dem"], f["upa"]
    nrow, ncol = dem.shape
    flw = pyflwdir.from_array(f["d8"], ftype="d8", transform=T, latlon=True)
    lat_c = T[5] + T[4] * (np.arange(nrow) + 0.5)
    lon_c = T[2] + T[0] * (np.arange(ncol) + 0.5)
    ca = np.repeat((pyflwdir.gis_utils.cellarea(lat_c, T[0], T[4]) / 1e6)[:, None], ncol, 1)

    # cell fields
    lc = np.load(os.path.join(cache, "landcover6.npz"))["lc"]
    from ..config import DEFAULT_CN
    c_lut = np.full(256, 0.30)
    for k, v in (c_table or RATIONAL_C).items():
        c_lut[int(k)] = float(v)
    c_cell = c_lut[lc]
    cn_lut = np.full(256, 74.0)
    for k, v in (cn_table or DEFAULT_CN).items():
        cn_lut[int(k)] = float(v)
    cn_cell = cn_lut[lc]
    clim_p = os.path.join(cache, "climate.npz")
    if not os.path.exists(clim_p):                 # the copy kept in the repository
        clim_p = os.path.join(static_dir, CLIMATE_FILE)
    if os.path.exists(clim_p):
        from scipy.interpolate import RegularGridInterpolator
        cl = np.load(clim_p)
        lats, lons = cl["lat"][::-1], cl["lon"]
        pts_lat = np.repeat(lat_c, ncol)
        pts_lon = np.tile(lon_c, nrow)
        def interp(field):
            fi = RegularGridInterpolator((lats, lons), field[::-1], bounds_error=False,
                                         fill_value=None)
            return fi(np.c_[pts_lat, pts_lon]).reshape(nrow, ncol)
        jjas, ann = interp(cl["jjas"]), interp(cl["annual"])
        log("  rainfall climatology: ERA5")
    else:
        jjas = ann = np.full((nrow, ncol), np.nan)
        log("  WARNING: no climate.npz; MWI and AWI left empty")

    # 1. river reaches (whole domain: the Tibetan reaches are needed upstream)
    log("  river reaches…")
    feats = flw.streams(mask=upa >= MIN_RIVER_KM2, uparea=upa)
    # pyflwdir gives each segment's *upstream* cell (idx) and the first cell of the
    # segment below it (idx_ds); the forecast point of a reach is its outlet, the
    # last cell before idx_ds
    ds_all = flw.idxs_ds
    reach_out = np.empty(len(feats), dtype=np.int64)
    for k, ft in enumerate(feats):
        cur, stop = int(ft["properties"]["idx"]), int(ft["properties"]["idx_ds"])
        for _ in range(len(ft["geometry"]["coordinates"]) + 2):
            nxt = int(ds_all[cur])
            if nxt == stop or nxt == cur:
                break
            cur = nxt
        reach_out[k] = cur
    geoms = [shape(ft["geometry"]) for ft in feats]
    nepal = nepal_polygon(cache)
    from shapely.prepared import prep
    pn = prep(nepal.buffer(0.01))
    in_nepal = np.array([pn.intersects(g) for g in geoms])
    log(f"  {len(feats)} reaches in the domain, {in_nepal.sum()} in Nepal")

    # 2. DHM stations (snapped by their published drainage area) and extra points
    st = pd.read_csv(stations_csv, dtype={"dhm": str})
    st_idx, st_area, st_move = [], [], []
    for _, s in st.iterrows():
        i, a, d = _snap(upa, T, s.lat, s.lon, s.area_km2)
        st_idx.append(i); st_area.append(a); st_move.append(d * 0.185)
    st["area_traced_km2"] = np.round(st_area, 2)
    st["area_ratio"] = np.round(st["area_traced_km2"] / st["area_km2"], 3)
    st["moved_km"] = np.round(st_move, 2)
    ex = []
    for p in extra_points:
        i, a, _ = _snap(upa, T, p["lat"], p["lon"], p.get("area_km2"))
        ex.append((i, a, p))

    # 3. targets: reach outlets, then stations, then extra points
    t_idx = np.concatenate([reach_out, np.array(st_idx, np.int64),
                            np.array([e[0] for e in ex], np.int64)])
    t_idx_u, first = np.unique(t_idx, return_index=True)
    n_t = len(t_idx_u)
    label = flw.basins(idxs=t_idx_u, ids=np.arange(1, n_t + 1, dtype=np.int32)).ravel()
    ds_cell = flw.idxs_ds
    # downstream target of each target: label of the cell below its outlet
    below = ds_cell[t_idx_u]
    t_ds = np.where(below == t_idx_u, 0, label[below]) - 1        # -1: none
    log(f"  {n_t} forecast targets")

    # 4. incremental sums by target, then accumulation along the target graph
    lab = label - 1
    ok = lab >= 0
    z = dem.ravel()
    fields = {
        "area": ca.ravel(),
        "a3000": ca.ravel() * (z < 3000),
        "a5000": ca.ravel() * (z < 5000),
        "elev": ca.ravel() * z,
        "c": ca.ravel() * c_cell.ravel(),
        "cn": ca.ravel() * cn_cell.ravel(),
        "mwi": ca.ravel() * np.nan_to_num(jjas.ravel()),
        "awi": ca.ravel() * np.nan_to_num(ann.ravel()),
        "glacier": ca.ravel() * (lc.ravel() == 70),
    }
    inc = {k: np.bincount(lab[ok], weights=v[ok], minlength=n_t) for k, v in fields.items()}
    # rain nodes
    rr, cc = np.divmod(np.arange(z.size), ncol)
    node_r = np.round((lat_c[rr] - NODE_LAT0) / NODE_DEG).astype(np.int32)
    node_c = np.round((lon_c[cc] - NODE_LON0) / NODE_DEG).astype(np.int32)
    n_nc = int(node_c.max()) + 1
    node = node_r * n_nc + node_c
    del rr, cc
    # order targets upstream -> downstream (by upstream area)
    order = np.argsort(upa.ravel()[t_idx_u])
    acc = {k: v.copy() for k, v in inc.items()}
    for i in order:
        j = t_ds[i]
        if j >= 0:
            for k in acc:
                acc[k][j] += acc[k][i]

    # 5. which targets are forecast (in Nepal) -> rain weights only for them
    is_reach = np.zeros(n_t, bool)
    reach_of_target = np.full(n_t, -1)
    pos = np.searchsorted(t_idx_u, reach_out)
    is_reach[pos] = True
    reach_of_target[pos] = np.arange(len(reach_out))
    keep = np.zeros(n_t, bool)
    keep[pos[in_nepal]] = True
    keep[np.searchsorted(t_idx_u, np.array(st_idx, np.int64))] = True
    for e in ex:
        keep[np.searchsorted(t_idx_u, e[0])] = True
    # upstream sets: walk the target graph once (children lists)
    children = [[] for _ in range(n_t)]
    for i in range(n_t):
        if t_ds[i] >= 0:
            children[t_ds[i]].append(i)
    # incremental node histograms (sparse dict per target)
    key = lab[ok].astype(np.int64) * (node.max() + 1) + node[ok]
    uk, inv = np.unique(key, return_inverse=True)
    wsum = np.bincount(inv, weights=ca.ravel()[ok])
    inc_t, inc_n = np.divmod(uk, node.max() + 1)
    by_t = {}
    for t, nd, w in zip(inc_t, inc_n, wsum):
        by_t.setdefault(int(t), []).append((int(nd), w))
    rows, cols, vals = [], [], []
    keep_ids = np.where(keep)[0]
    for out_row, t in enumerate(keep_ids):
        hist = {}
        stack = [t]
        while stack:
            u = stack.pop()
            for nd, w in by_t.get(u, ()):
                hist[nd] = hist.get(nd, 0.0) + w
            stack.extend(children[u])
        tot = sum(hist.values())
        for nd, w in hist.items():
            rows.append(out_row); cols.append(nd); vals.append(w / tot)
    used_nodes = np.unique(cols)
    node_pos = {nd: k for k, nd in enumerate(used_nodes)}
    cols = [node_pos[c] for c in cols]
    np.savez_compressed(os.path.join(static_dir, "rain_weights.npz"),
                        rows=np.array(rows, np.int32), cols=np.array(cols, np.int32),
                        vals=np.array(vals, np.float32), shape=np.array([len(keep_ids), len(used_nodes)]))
    nodes = pd.DataFrame({"node": np.arange(len(used_nodes)),
                          "lat": np.round(NODE_LAT0 + (used_nodes // n_nc) * NODE_DEG, 2),
                          "lon": np.round(NODE_LON0 + (used_nodes % n_nc) * NODE_DEG, 2)})
    nodes.to_csv(os.path.join(static_dir, "rain_nodes.csv"), index=False)
    log(f"  rain weights: {len(keep_ids)} targets over {len(used_nodes)} grid nodes")

    # 6. longest flow path -> Kirpich t_c
    log("  longest flow paths…")
    L, zsrc = _longest_path(flw.idxs_seq.astype(np.int64), ds_cell.astype(np.int64),
                            z.astype(np.float64), nrow, ncol, T[0], T[5])

    # 7. names: DHM station rivers along their main stems
    reach_ds = np.full(len(reach_out), -1)
    rds_cell = ds_cell[reach_out]
    cell2reach = {int(c): k for k, c in enumerate(reach_out)}
    # the reach a cell belongs to = label of target; map target -> reach
    for k, c in enumerate(rds_cell):
        if c == reach_out[k]:
            continue
        t = label[c] - 1
        # walk down targets until a reach target
        while t >= 0 and not is_reach[t]:
            t = t_ds[t]
        reach_ds[k] = reach_of_target[t] if t >= 0 else -1
    reach_up = [[] for _ in range(len(reach_out))]
    for k, d in enumerate(reach_ds):
        if d >= 0:
            reach_up[d].append(k)
    r_area = upa.ravel()[reach_out]
    names = [""] * len(reach_out)
    st_reach = []
    for i in st_idx:
        t = label[i] - 1
        while t >= 0 and not is_reach[t]:
            t = t_ds[t]
        st_reach.append(reach_of_target[t] if t >= 0 else -1)
    st["reach"] = st_reach
    def st_name(k):
        return str(st.river.iloc[k]).replace("River", "").strip().title()
    order = np.argsort(st["area_km2"].to_numpy())
    # upstream first, smallest station first: each reach takes the name of the
    # nearest station downstream on its main stem
    for k in order:
        u = st_reach[k]
        while u >= 0 and not names[u]:
            names[u] = st_name(k)
            ups = reach_up[u]
            u = max(ups, key=lambda x: r_area[x]) if ups else -1
    # then downstream, largest station first, while the river stays the main stem
    for k in order[::-1]:
        prev = st_reach[k]
        if prev < 0:
            continue
        d = reach_ds[prev]
        while d >= 0 and not names[d] and max(reach_up[d], key=lambda x: r_area[x]) == prev:
            names[d] = st_name(k)
            prev, d = d, reach_ds[d]

    # 8. basins leaving Nepal (>= 100 km2) -> polygons, names from stations
    log("  basins…")
    from rasterio import features as rfeat
    def leaves_for_good(k):                 # no reach further down is in Nepal again
        d = reach_ds[k]
        while d >= 0:
            if in_nepal[d]:
                return False
            d = reach_ds[d]
        return True
    exits = [k for k in range(len(reach_out)) if in_nepal[k] and r_area[k] >= 100
             and (reach_ds[k] < 0 or not in_nepal[reach_ds[k]]) and leaves_for_good(k)]
    exits = sorted(exits, key=lambda k: -r_area[k])
    bas = flw.basins(idxs=reach_out[exits], ids=np.arange(1, len(exits) + 1, dtype=np.int32))
    basin_of_reach = np.zeros(len(reach_out), np.int32)
    basin_of_reach[:] = bas.ravel()[reach_out]
    shapes = rfeat.shapes(bas.astype(np.int32), mask=bas > 0, transform=T)
    polys = {}
    from shapely.ops import unary_union
    for geom, v in shapes:
        polys.setdefault(int(v), []).append(shape(geom))
    bfeat = []
    bnames = {}
    for b, k in enumerate(exits, start=1):
        inside = st[[basin_of_reach[r] == b if r >= 0 else False for r in st_reach]]
        nm = ""
        if len(inside):
            big = inside.sort_values("area_km2").iloc[-1]
            if big.area_km2 >= 0.5 * r_area[k]:
                nm = str(big.river).replace("River", "").strip().title()
        lon_o = lon_c[reach_out[k] % ncol]
        for ref, lo, hi, a_ref in MAJOR_BASINS:
            if not nm and lo <= lon_o <= hi and 0.5 <= r_area[k] / a_ref <= 1.5 \
                    and ref not in bnames.values():
                nm = ref
        bnames[b] = nm
        g = unary_union(polys.get(b, [])).simplify(0.004, preserve_topology=True)
        lon, lat = lon_c[reach_out[k] % ncol], lat_c[reach_out[k] // ncol]
        bfeat.append({"type": "Feature", "geometry": mapping(g), "properties": {
            "basin": b, "name": nm or f"River system at {lat:.2f} N {lon:.2f} E",
            "area_km2": round(float(r_area[k]), 1), "outlet_reach": int(k)}})
    _write_geojson(os.path.join(static_dir, "basins.geojson"), bfeat, digits=3)

    # 9. descriptors of the forecast targets
    rows_out = []
    keep_set = {int(t): n for n, t in enumerate(keep_ids)}
    for t in keep_ids:
        cell = int(t_idx_u[t])
        A = acc["area"][t]
        r, c = divmod(cell, ncol)
        lat, lon = float(lat_c[r]), float(lon_c[c])
        zo = float(z[cell])
        Lm = float(L[cell]) + 0.5 * 185.0
        H = max(float(zsrc[cell]) - zo, 1.0)
        reach = int(reach_of_target[t]) if is_reach[t] else -1
        rows_out.append({
            "target": keep_set[int(t)], "reach": reach,
            "lat": round(lat, 5), "lon": round(lon, 5),
            "area_km2": round(A, 2), "a3000_km2": round(acc["a3000"][t], 2),
            "a5000_km2": round(acc["a5000"][t], 2),
            "elev_mean_m": round(acc["elev"][t] / A, 1), "elev_outlet_m": round(zo, 1),
            "flow_length_km": round(Lm / 1000, 2), "drop_m": round(H, 1),
            "tc_h": round(kirpich_tc_hours(Lm, H), 2),
            "c_rational": round(acc["c"][t] / A, 3), "cn2": round(acc["cn"][t] / A, 1),
            "mwi_mm": round(acc["mwi"][t] / A, 0) if os.path.exists(clim_p) else np.nan,
            "awi_mm": round(acc["awi"][t] / A, 0) if os.path.exists(clim_p) else np.nan,
            "glacier_frac": round(acc["glacier"][t] / A, 4),
            "mhsp_region": mhsp_region(lon),
            "basin": int(bas.ravel()[cell]),
            "name": names[reach] if reach >= 0 else "",
        })
    tg = pd.DataFrame(rows_out)
    # station rows get their station id and name
    tg["station"] = ""
    for k, i in enumerate(st_idx):
        t = keep_set[int(np.searchsorted(t_idx_u, i))]
        tg.at[t, "station"] = str(st.dhm.iloc[k])
        river = str(st.river.iloc[k]).replace("River", "").strip().title()
        tg.at[t, "name"] = f"{river} at {st.location.iloc[k]}"
    st["target"] = [keep_set[int(np.searchsorted(t_idx_u, i))] for i in st_idx]
    for e in ex:
        t = keep_set[int(np.searchsorted(t_idx_u, e[0]))]
        tg.at[t, "name"] = e[2].get("name", tg.at[t, "name"])
    tg["kind"] = np.where(tg["reach"] >= 0, "reach", "")
    tg.loc[tg["station"] != "", "kind"] = "station"
    for e in ex:
        tg.at[keep_set[int(np.searchsorted(t_idx_u, e[0]))], "kind"] = "point"
    tg["basin_name"] = tg["basin"].map(bnames).fillna("")
    tg.to_csv(os.path.join(static_dir, "targets.csv"), index=False)
    st.to_csv(os.path.join(static_dir, "stations.csv"), index=False)

    # 10. reach lines of Nepal
    rfeat_out = []
    target_of_reach = {int(r): int(t) for t, r in zip(tg.target, tg.reach) if r >= 0}
    for k in np.where(in_nepal)[0]:
        g = geoms[k].simplify(0.0015, preserve_topology=False)
        d = int(reach_ds[k])
        rfeat_out.append({"type": "Feature", "geometry": mapping(g), "properties": {
            "id": int(k), "t": target_of_reach.get(int(k), -1),
            "ds": d if d >= 0 and in_nepal[d] else -1,
            "a": round(float(r_area[k]), 1), "n": names[k],
            "b": int(basin_of_reach[k])}})
    _write_geojson(os.path.join(static_dir, "reaches.geojson"), rfeat_out, digits=4)
    import shutil
    shutil.copy(os.path.join(cache, "nepal.geojson"), os.path.join(static_dir, "nepal.geojson"))
    log(f"  wrote {static_dir}: {len(rfeat_out)} reaches, {len(tg)} targets, {len(bfeat)} basins")
    return tg


def _write_geojson(path, feats, digits=4):
    def rnd(x):
        if isinstance(x, (list, tuple)):
            return [rnd(v) for v in x]
        return round(x, digits)
    for ft in feats:
        ft["geometry"] = {"type": ft["geometry"]["type"],
                          "coordinates": rnd(ft["geometry"]["coordinates"])}
    with open(path, "w") as f:
        json.dump({"type": "FeatureCollection", "features": feats}, f, separators=(",", ":"))
