# Methods

Each method is named by its original source. The code is in
`nepal_forecast/formulas.py` (formulas), `nepal_forecast/hydrograph.py`
(rain → discharge) and `nepal_forecast/prepare/` (catchment descriptors).

## 1. The idea

The empirical methods of Nepal come in two kinds, and the forecast uses each
for what it can do:

* **Area-based design floods** (WECS/DHM 1990, DHM 2004, MHSP 1997, WECS 2019
  specific discharge, Modified Dickens) give the T-year flood of a catchment from
  its area. They know nothing about today's rain, so they become the **flood
  levels** against which the forecast is measured: "this river is forecast to
  reach its 5-year flood".
* **Rainfall-based methods** (SCS curve number with a unit hydrograph, Rational,
  Mononobe, B.D. Richards) turn a rainfall into a discharge, so they take the
  **forecast rain** and give the **forecast discharge**.

The **mean monthly flow** methods (WECS/DHM 1990, NEA 1997, DHM 2004) give the
normal flow of the month, the base under every forecast.

## 2. Catchment descriptors (once, `nepal-forecast prepare`)

| Descriptor | How |
|---|---|
| River network | Copernicus GLO-90 DEM, block-minimum to 6″ (keeps gorge floors), priority-flood D8 with pyflwdir; deep (≥100 m) depressions above 4,000 m kept as endorheic. Rivers: upstream area ≥ 20 km² |
| A, A₃₀₀₀, A₅₀₀₀ | Sum of cell areas upstream, all / below 3,000 m / below 5,000 m |
| Snow area a (Modified Dickens) | A − A₅₀₀₀ (area above 5,000 m) |
| Mean elevation | Area-weighted DEM mean |
| L, H → t_c | Longest flow path upstream of the point (m) and the drop from its start to the point; Kirpich t_c = 0.00032 L^0.77 (H/L)^-0.385 h. This uses the channel path, not the catchment slope |
| MWI, AWI | Mean June-September and mean annual rain over the catchment, ERA5 2019-2023 (0.25°, bilinear) |
| C (Rational) | By ESA WorldCover class: forest/shrub/grass/wetland 0.30, cropland 0.50, bare 0.25, built-up 0.865, snow/ice 0.90, water 1.0; area-weighted |
| CN (AMC II) | USDA TR-55, hydrologic soil group C, by WorldCover class (woods 70, brush 65, pasture 74, row crops 85, urban 90, bare 91, snow 90, water 98, wetland 87, moss 80); area-weighted |
| MHSP region | From the outlet longitude: western < 83° E < central < 86° E < eastern (no official map of the regions is used; configurable) |
| Rain weights | Share of the catchment nearest each 0.25° grid node |

## 3. Flood levels (each update)

For T = 2, 5, 10, 20, 50, 100 years:

* **WECS/DHM 1990**: Q₂ = 1.8767 (A₃₀₀₀+1)^0.8783, Q₁₀₀ = 14.63 (A₃₀₀₀+1)^0.7342,
  Q_T = exp(ln Q₂ + S σ), σ = ln(Q₁₀₀/Q₂)/2.326, S the standard normal variate of T.
* **DHM 2004**: Q₂ = 2.29 A₃₀₀₀^0.86, Q₁₀₀ = 20.7 A₃₀₀₀^0.72, then log-normal as above.
* **MHSP 1997**: Q_T = k A^b, k and b by region and T. T = 10 is interpolated in the normal
  variate between 5 and 20; T = 2 is outside the table and not used.
* **WECS 2019 specific discharge**: q₂, q₂₅, q₅₀, q₁₀₀; T = 5, 10, 20
  interpolated in the normal variate between 2 and 25. Not used below 12 km².
* **Modified Dickens**: C_T = 2.342 log(0.6T) log(1185/p) + 4,
  p = 100 (a+6)/(A+a), Q_T = C_T A^¾.

The flood level used is the **median** of the methods available for that T
(`thresholds: statistic:`). The page lists every method's values.
Dickens (C = 11), Ryves and Inglis are only cross-checks in the code: the
Nepal Bridge Standards 2067 do not allow them for design and they have no return period.
PCJ 1996 / Modified PCJ 2024 need station coefficient tables that are not
publicly available, so they are not used.

## 4. Forecast discharge (each update)

The rain of each model at the grid nodes is averaged over each catchment with the
static weights, giving hourly catchment rain (liquid: snowfall is removed using
Open-Meteo's snowfall, 7 cm of snow = 10 mm of water). The series runs from 7 days
before now (antecedent moisture, water still in the rivers) to 7 days ahead.

**SCS curve number + SCS unit hydrograph (main method).**
The rain is split into storm events by 12 dry hours (< 0.2 mm/h). Each event takes
CN for the antecedent moisture condition given by the 5-day rain before it (USDA
NEH-4, growing season: AMC I below 35.6 mm, AMC III above 53.3 mm; CN_I and CN_III
from Chow et al., 1988). Within an event, cumulative runoff
Q = (P − 0.2S)² / (P + 0.8S), S = 25400/CN − 254; its hourly increments are
spread evenly over 15-minute steps and convolved with the SCS dimensionless unit
hydrograph (NEH-630 Ch. 16) with lag 0.6 t_c and T_p = Δt/2 + lag. The hourly value
kept is the highest of its four 15-minute values, so small flashy catchments keep their
instantaneous peaks, which is what the design floods describe.

**Rational method.** Q = C i A / 3.6, with i the mean intensity over the last
max(1 h, t_c). WECS 2019 limits it to 50 km²; the page says so above that.

**Mononobe-Rational.** For each Nepal calendar day, R₂₄ = the day's rain,
I = (R₂₄/24)(24/t_c)^⅔, Q = C I A/3.6.

**B.D. Richards.** R_tc = 0.22127 R₂₄ t_c^0.476577, I = R_tc/t_c,
F = 1.09352 − 0.066628 ln A, Q = 0.222 A I F, with the day's rain as R₂₄
and Kirpich t_c (capped at 24 h) instead of the method's iterative t_c.

**Base flow.** Every method adds the normal flow of the month, WECS/DHM 1990 by
default, interpolated linearly between mid-month values. NEA 1997 and DHM 2004
are shown on the page for comparison.

## 5. Flood level of a forecast

For each point and model, the highest SCS discharge from now to the end of the
forecast is compared with the flood levels: the class is the largest T whose flood it
reaches (0 = below the 2-year flood). The map colours each river by that class, for
one model or for the highest of the three ("Any model").

## 6. Known weaknesses

See "Limits" in the README. The most important: no calibration against observed floods;
weather-model rain is too coarse for the smallest Chure catchments; snowmelt, GLOFs and
reservoirs are absent; the regional formulas were fitted mostly outside the Chure.
Regional flood frequency with L-moments, transfer from gauged donor stations, and
observed records such as DHM 283.3 would directly improve the flood levels used here.
