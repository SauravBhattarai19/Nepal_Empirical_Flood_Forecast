# -*- coding: utf-8 -*-
"""``nepal-forecast …``: the command line."""

import argparse
import json
import os
import sys
import time
from datetime import datetime

from . import config as _config

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _cfg(args):
    return _config.load(args.config)


def cmd_run(args):
    from . import cycle, publish
    cfg = _cfg(args)
    if args.only_if_stale:
        age = publish.published_age_minutes(cfg)
        if age is not None and age < args.only_if_stale:
            print(f"The published forecast is {age:.0f} min old: nothing to do.")
            return 0
    site = args.site or os.path.join(ROOT, "site")
    try:
        cycle.run(cfg, static_dir=args.static, site_dir=site)
    except Exception as exc:                      # keep the last good site
        print(f"[ERROR] the update failed: {exc}", file=sys.stderr)
        if args.debug:
            raise
        return 1
    if args.publish or (cfg.get("publish") or {}).get("git") or (cfg.get("publish") or {}).get("command") \
            or os.environ.get("NEPAL_FORECAST_PUBLISH_REMOTE"):
        try:
            publish.publish(cfg, site)
        except Exception as exc:
            print(f"[ERROR] publishing failed: {exc}", file=sys.stderr)
            return 2
    return 0


def cmd_serve(args):
    """Run an update now and then every ``every_hours``, at minute 10."""
    while True:
        cfg = _cfg(args)
        t0 = time.time()
        cmd_run(args)
        every = float(cfg["every_hours"]) * 3600.0
        now = time.time()
        nxt = (now // every + 1) * every + 600          # next slot + 10 min
        if nxt - now > every:
            nxt -= every
        wait = max(nxt - now, 300)
        print(f"Next update in {wait / 60:.0f} min (this one took {now - t0:.0f} s).", flush=True)
        time.sleep(wait)


def cmd_site(args):
    from . import cycle
    site = args.site or os.path.join(ROOT, "site")
    cycle.build_site(site, args.static or os.path.join(ROOT, "static"))
    print(f"Rebuilt the page files in {site}")
    return 0


def cmd_preview(args):
    import functools
    import http.server
    site = args.site or os.path.join(ROOT, "site")
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=site)
    print(f"Serving {site} on http://localhost:{args.port}/  (Ctrl-C to stop)")
    http.server.ThreadingHTTPServer(("", args.port), handler).serve_forever()


def cmd_status(args):
    site = args.site or os.path.join(ROOT, "site")
    p = os.path.join(site, "data", "latest.json")
    if not os.path.exists(p):
        print("No forecast yet: run 'nepal-forecast run'.")
        return 1
    d = json.load(open(p))
    age = (datetime.utcnow() - datetime.strptime(d["issued_utc"], "%Y-%m-%dT%H:%MZ")).total_seconds() / 60
    print(f"Last update {d['issued_utc']} ({age:.0f} min ago), rain from {d['source']}, "
          f"models {', '.join(m['name'] for m in d['models'])}, took {d['runtime_s']} s")
    for m, c in d["counts"].items():
        print(f"  {m}: " + ", ".join(f">= {T}-yr: {n}" for T, n in c.items()))
    for e in d.get("errors", []):
        print(f"  problem: {e}")
    return 0


def cmd_check(args):
    """Can the rain feeds be reached?"""
    import pandas as pd
    from .sources import FeedError, open_meteo
    cfg = _cfg(args)
    nodes = pd.read_csv(os.path.join(args.static or os.path.join(ROOT, "static"), "rain_nodes.csv")).head(3)
    cfg["open_meteo"]["chunk"] = 3
    ok = True
    try:
        g = open_meteo.fetch(nodes, cfg, log=lambda *_: None)
        print(f"Open-Meteo: OK, {len(g.times)} hours, models {', '.join(g.models())}")
    except FeedError as exc:
        ok = False
        print(f"Open-Meteo: FAILED: {exc}")
    try:
        from .sources import noaa_gfs_aws
        run = noaa_gfs_aws._latest_run(datetime.utcnow())
        print(f"GFS on AWS (fallback): OK, newest complete cycle {run:%Y-%m-%d %H} UTC")
        import eccodes  # noqa: F401
    except ImportError:
        print("GFS on AWS (fallback): reachable, but 'pip install eccodes' is needed to read it")
    except FeedError as exc:
        print(f"GFS on AWS (fallback): FAILED: {exc}")
    return 0 if ok else 1


def cmd_prepare(args):
    os.environ.setdefault("NEPAL_FORECAST_CACHE", os.path.join(ROOT, "prepare_cache"))
    from .prepare import common
    common.CACHE = os.environ["NEPAL_FORECAST_CACHE"]
    os.makedirs(common.CACHE, exist_ok=True)
    steps = ["dem", "flow", "climate", "landcover", "network", "verify"] if args.step == "all" else [args.step]
    cfg = _cfg(args)
    for s in steps:
        print(f"== prepare {s}", flush=True)
        if s == "dem":
            from .prepare import dem
            dem.run()
        elif s == "flow":
            from .prepare import flow
            flow.run()
        elif s == "climate":
            # ERA5 takes about an hour; the result is small and kept in static/
            import shutil
            from .prepare import climate
            from .prepare.network import CLIMATE_FILE
            kept = os.path.join(args.static or os.path.join(ROOT, "static"), CLIMATE_FILE)
            if os.path.exists(kept) and not args.refresh:
                shutil.copy(kept, os.path.join(common.CACHE, "climate.npz"))
                print(f"  using {kept} (add --refresh to download ERA5 again)")
            else:
                shutil.copy(climate.run(), kept)
        elif s == "landcover":
            from .prepare import landcover
            landcover.run()
        elif s == "network":
            from .prepare import network
            network.run(args.static or os.path.join(ROOT, "static"),
                        os.path.join(ROOT, "static", "dhm_stations_2025.csv"),
                        extra_points=cfg.get("points") or [],
                        cn_table=cfg["runoff"]["cn_by_landcover"],
                        c_table=cfg["runoff"].get("c_by_landcover"))
        elif s == "verify":
            from .prepare import verify
            print(verify.run(args.static or os.path.join(ROOT, "static"),
                             os.path.join(ROOT, "docs", "verification.md")))
    return 0


def cmd_service(args):
    from . import service
    return service.install(ROOT) if args.action == "install" else service.uninstall()


def main(argv=None):
    ap = argparse.ArgumentParser(prog="nepal-forecast", description=__doc__)
    ap.add_argument("-c", "--config", default=os.path.join(ROOT, "config.yaml"))
    ap.add_argument("--static", default=None, help="the static network folder (default static/)")
    ap.add_argument("--site", default=None, help="the site folder (default site/)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("run", help="one forecast update now (and publish if set)")
    p.add_argument("--publish", action="store_true")
    p.add_argument("--only-if-stale", type=float, default=None, metavar="MIN",
                   help="skip when the published forecast is younger than MIN minutes")
    p.add_argument("--debug", action="store_true")
    p.set_defaults(fn=cmd_run)
    p = sub.add_parser("serve", help="keep updating every few hours")
    p.add_argument("--publish", action="store_true")
    p.add_argument("--only-if-stale", type=float, default=None)
    p.add_argument("--debug", action="store_true")
    p.set_defaults(fn=cmd_serve)
    sub.add_parser("site", help="rebuild the page files only").set_defaults(fn=cmd_site)
    p = sub.add_parser("preview", help="serve site/ on localhost")
    p.add_argument("--port", type=int, default=8000)
    p.set_defaults(fn=cmd_preview)
    sub.add_parser("status", help="the last update").set_defaults(fn=cmd_status)
    sub.add_parser("check", help="can the rain feeds be reached?").set_defaults(fn=cmd_check)
    p = sub.add_parser("prepare", help="(re)build the static river network (heavy, once)")
    p.add_argument("--refresh", action="store_true", help="download ERA5 again (climate step)")
    p.add_argument("step", nargs="?", default="all",
                   choices=["all", "dem", "flow", "climate", "landcover", "network", "verify"])
    p.set_defaults(fn=cmd_prepare)
    p = sub.add_parser("service", help="run 'serve' in the background on this computer")
    p.add_argument("action", choices=["install", "uninstall"])
    p.set_defaults(fn=cmd_service)
    args = ap.parse_args(argv)
    return args.fn(args) or 0


if __name__ == "__main__":
    sys.exit(main())
