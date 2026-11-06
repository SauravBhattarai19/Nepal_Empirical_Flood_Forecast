# Operator guide

For whoever runs or changes this forecast. It covers where the forecast comes
from, what to edit for each kind of change, how the page ranks rivers, and what
to do when something is wrong. The science is in [methods.md](methods.md) and
the checks in [verification.md](verification.md).

## 1. Where the forecast comes from

| | Source | When |
|---|---|---|
| Rain forecast (normal case) | **Open-Meteo** forecast API: ECMWF IFS 0.25°, NOAA GFS, DWD ICON, hourly, 7 days back and 7 days ahead, free, no account | every update |
| Rain forecast (backup) | **NOAA GFS 0.25°** read directly from the NOAA Open Data bucket on AWS | only when Open-Meteo cannot be reached; the page then shows an orange notice and one model |
| Rivers, catchments, descriptors | `static/`, made once by `nepal-forecast prepare` from the Copernicus DEM, ESA WorldCover and ERA5 | fixed until you rebuild it |
| Formulas | `nepal_forecast/formulas.py` | every update |

Open-Meteo's free tier allows 10,000 calls a day for non-commercial use. One
update costs about 380 calls (377 grid nodes), so a 2-hourly update uses ~4,500
a day. GitHub's shared runners can occasionally be throttled; for a guaranteed
service, buy an Open-Meteo API key and add it as the repository secret
`OPEN_METEO_API_KEY` (or the environment variable of the same name on a server).

`nepal-forecast check` tells you at any time whether both feeds can be reached.

## 2. What to change, and where

Almost everything is in **`config.yaml`**. The forecast re-reads it at every update.

### Changes that take effect at the next update (edit, commit, done)

| Change | Edit in `config.yaml` |
|---|---|
| Which weather models, or a fourth model | `models:` (any [Open-Meteo model id](https://open-meteo.com/en/docs), e.g. `ecmwf_aifs025`, `gem_seamless`, `ukmo_seamless`); the first is the main one |
| Forecast length, past days shown | `forecast_days`, `past_days`, `show_past_hours` |
| Method that colours the map | `runoff: primary: scs` or `rational` |
| Storm-event and moisture settings | `runoff:` `event_gap_h`, `amc_dry_mm`, `amc_wet_mm`, `ia_ratio`, `lag_ratio` |
| Base flow method or scaling | `runoff: baseflow:` `WECS/DHM 1990`, `NEA 1997` or `DHM 2004`; `baseflow_factor` |
| Flood levels: which return periods | `thresholds: return_periods: [2, 5, 10, 20, 50, 100]` (any list; colours follow) |
| Flood levels: median, lowest or highest of the methods | `thresholds: statistic: median` / `min` / `max` |
| **Known flood levels at a station or point** (e.g. from a DHM frequency analysis) | `flood_levels:` keyed by DHM number or point name, e.g. `"283.3": {2: 150, 100: 700}`; the formulas fill the return periods you leave out |
| MHSP region boundaries | `thresholds: mhsp_split_lon: [83.0, 86.0]` |
| Page title, time zone | `title`, `timezone` |
| Where the site is published | `publish: git:` or `publish: command:` |

### Changes that need the network rebuilt (about 10 minutes, ~300 MB download)

These change `static/`: run the commands, check, then **commit `static/`**.

| Change | Edit | Then |
|---|---|---|
| Add a forecast point (intake, bridge, canal crossing, a catchment outlet) | `points:` with `name`, `lat`, `lon` and, if known, `area_km2` (the point snaps to the river within ~1.8 km whose traced area matches best) | rebuild |
| Curve numbers by land cover | `runoff: cn_by_landcover:` | rebuild |
| Runoff coefficients C by land cover | `runoff: c_by_landcover:` | rebuild |
| Smallest river shown (default 20 km²) | `MIN_RIVER_KM2` in `nepal_forecast/prepare/common.py` | rebuild |

The rebuild:

```bash
pip install -e ".[prepare]"
nepal-forecast prepare dem         # Copernicus DEM tiles from AWS (~280 MB, a few minutes)
nepal-forecast prepare flow        # river tracing (~1 min)
nepal-forecast prepare landcover   # ESA WorldCover (~15 s)
nepal-forecast prepare climate     # uses the ERA5 grid kept in static/ (instant; --refresh redownloads, ~1 h)
nepal-forecast prepare network     # descriptors, basins, rain weights (~1 min)
nepal-forecast prepare verify      # rewrites docs/verification.md
pytest -q
```

The downloads stay in `prepare_cache/` (not in git), so later rebuilds need only
the last two steps.

### Changes in the code (for someone who knows Python/JavaScript)

| Change | File |
|---|---|
| Add or correct a formula | `nepal_forecast/formulas.py` (+ a test in `tests/test_formulas.py`); to make a new design-flood method count in the flood levels, add it to `DESIGN_METHODS` and `design_floods()` |
| How rain becomes discharge | `nepal_forecast/hydrograph.py` |
| What the update writes | `nepal_forecast/cycle.py` |
| A new rain source | a module in `nepal_forecast/sources/` returning a `RainGrid`, listed in `fallback_sources` |
| Page text, layout, colours | `nepal_forecast/web/index.html`, `style.css` (colours of each flood level: `--l2` … `--l100`), `app.js` |
| Update frequency on GitHub | the `cron:` line in `.github/workflows/forecast.yml` (and `every_hours` in `config.yaml` for a local server) |

After any code change: `pytest -q`, then `nepal-forecast run` and
`nepal-forecast preview` to look at the page.

## 3. How the page ranks and colours rivers

**Colour of a river (and the counts at the top).** For each river reach, station
and point, the highest discharge forecast from now to the end of the forecast
(main method, normal flow included) is compared with its flood levels. The colour
is the largest return period reached: below the 2-year flood (blue), then 2, 5, 10,
20, 50, 100-year. The model buttons choose whose forecast:

* one model (ECMWF, GFS or ICON);
* **2+ agree**: the second-worst of the models, i.e. a level at least two models reach.
  The most robust view for decisions;
* **Any model**: the worst of the models. The most cautious view; more false alarms.

**Order of the Hotspots list (and the Stations tab)**, most serious first:

1. **flood level reached** (100-year before 20-year before … before none);
2. **how many models reach that level** (3 of 3 before 2 of 3 before 1 of 3);
3. **how far the forecast rise goes towards the first flood level**:
   (peak − normal flow) ÷ (2-year flood − normal flow). 1 means it reaches the 2-year
   flood. The month's normal flow counts for nothing here, so big rivers in their
   usual monsoon flow do not crowd the top;
4. **the soonest peak**.

Only the most serious reach of each river is listed (one entry per main stem). When
nothing rises the list says "No river is forecast to rise noticeably".

These are **return periods of empirical estimates, not DHM's warning and danger
levels** (which are water levels at gauges). If DHM's warning or danger discharge,
or a frequency analysis of a gauge record, is known for a station, put it under
`flood_levels:` and the station is judged by it.

The same ranking is written to `site/data/latest.json` (`hotspots`), for anyone who
wants to send alerts (SMS, e-mail, Slack) from it.

## 4. Handing over

* **Repository**: give the new team write access (Settings → Collaborators), or
  transfer the repository (Settings → Transfer ownership). Actions, Pages and
  secrets go with it; re-enable Pages if GitHub asks.
* **Secrets**: only the optional `OPEN_METEO_API_KEY`. No other password exists.
* **A server, if used**: `nepal-forecast service install` on the new computer; on the
  old one `nepal-forecast service uninstall`. Only one of them should publish, or set
  the repository variable `ONLY_IF_STALE_MIN` so GitHub only steps in as a backup.
* **Scheduled runs pause after 60 days** without commits in a public repository. Any
  commit (or the "Enable workflow" button in Actions) restarts them.

## 5. When something is wrong

| You see | Do |
|---|---|
| Red "Late" pill on the page | Actions → flood-forecast: open the last run and read the log; or `nepal-forecast status` on the server |
| Orange "Open-Meteo could not be reached" notice | usually passes by itself (rate limit or outage); if it persists, add an API key |
| The workflow fails at "Forecast update" | the log says which feed failed; `nepal-forecast check` locally |
| The site is not updating but the workflow is green | Settings → Pages must be "Deploy from a branch: gh-pages / root" |
| A point is on the wrong river | give its `area_km2` in `points:` and rebuild; `static/stations.csv` shows the traced area of each station |
| Numbers look too high or too low everywhere | see "Limits" in the README; calibrate `cn_by_landcover`, `baseflow_factor`, or give `flood_levels` |

## 6. Map of the code

```
config.yaml                    settings (read at every update)
static/                        the river network, made by 'prepare' (committed)
nepal_forecast/
  cli.py                       the 'nepal-forecast' command
  cycle.py                     one update: rain -> catchments -> methods -> site/data
  formulas.py                  every empirical relation
  hydrograph.py                SCS-CN + unit hydrograph, Rational, daily peaks, base flow
  sources/open_meteo.py        the rain forecast
  sources/noaa_gfs_aws.py      the backup rain forecast
  publish.py                   push site/ to the gh-pages branch (or a command)
  service.py                   systemd / cron on a server
  prepare/                     the one-off network build (dem, flow, climate, landcover, network, verify)
  web/                         the page (index.html, app.js, style.css, Leaflet)
tests/                         pytest: formulas vs reference values, hydrographs, a whole update
.github/workflows/forecast.yml every 2 hours on GitHub
```
