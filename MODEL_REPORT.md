# Fault detection model — trained on engine_master_dataset.csv

## What's in the dataset

Four continuous flight runs (~647s each, 25 Hz, ~16,180 rows each): `healthy`,
`valve_wear`, `cooling_failure`, `oil_pressure_drop`. Confirmed by comparing
`load`/`airspeed`/`roll`/`pitch` row-for-row: **all four runs replay the exact
same flight trajectory** — only the engine condition differs. That's what
makes a genuine digital-twin residual approach possible: for any timestep, the
healthy run tells you exactly what "expected" looks like at that same point in
the flight, and the deviation from it is a real physical signal, not a guess.

Each fault ramps in gradually partway through its flight (cooling failure at
t≈259s, valve wear at t≈194s, oil pressure drop at t≈324s) with `fault_severity`
climbing from 0 toward a class-specific ceiling (0.5–0.7) and `rul_frac`
declining in lockstep.

## Features

- Raw sensor readings: RPM, EGT, CHT, oil temp, oil pressure, fuel flow, vibration
- Flight state: airspeed, load, roll, pitch
- **Residual features** (the digital-twin part): each sensor's deviation from
  the healthy run's value at the same point in the flight, both as an absolute
  difference and a percentage

## Models

All three are `XGBoost` (gradient-boosted trees), chosen because: it handles
the nonlinear, multi-sensor interaction patterns that separate fault
signatures well; it's fast enough for a real-time detection loop; and it
pairs with `TreeExplainer` for exact, real SHAP values rather than an
approximation.

1. **Fault classifier** (`model_fault_classifier.joblib`) — 4-class:
   healthy / valve_wear / cooling_failure / oil_pressure_drop
2. **Severity regressor** (`model_severity_regressor.joblib`) — predicts
   `fault_severity` (0–1)
3. **RUL regressor** (`model_rul_regressor.joblib`) — predicts `rul_frac` (0–1)

## Evaluation — chronological split, not random

Random row splits would leak badly here (adjacent rows at 25 Hz are nearly
identical). Instead: **train on the first 75% of each run's timeline
(t < 485s), test on the final 25% (t ≥ 485s)** — later, more-progressed fault
states the model never saw during training. This is the honest test:
generalizing to *unseen severity levels*, not memorizing rows.

| Task | Metric | Result |
|---|---|---|
| Fault classification | Accuracy | 1.00 (all 4 classes, precision/recall/F1 all 1.00) |
| Severity regression | R² / MAE | 0.59 / 0.104 |
| RUL regression | R² / MAE | 0.59 / 0.104 |

**On the perfect classification score**: this isn't a red flag here — it
reflects that each fault's sensor signature (checked via residuals against
the healthy baseline) is large and physically distinctive at this dataset's
injected severities, even in the held-out later-time segment. The harder,
more honest numbers are the regressions (R²≈0.59): estimating *exactly how
severe* or *how much life remains* is a genuinely harder continuous problem,
and those scores show real but imperfect generalization — a `MAE` of ~0.10
on a 0–1 scale, which is a reasonable starting point to improve on with more
runs.

**Caveat worth flagging to judges**: with only one run per fault type, "test
accuracy" here means generalizing across *time within the same flight*, not
across independent flights or aircraft. Real deployment validation needs
multiple independent flight runs per fault type — this is a limitation of
the dataset size, not the modeling approach, and it's the same fault-injection
limitation your feasibility slide already names.

## Real SHAP explainability (not hand-assigned)

Top features per class, by mean absolute SHAP value on the held-out set:

- **oil_pressure_drop**: `oil_pressure_bar_resid`, `oil_pressure_bar`,
  `oil_pressure_bar_resid_pct` — physically correct, dominated by the
  sensor the fault directly affects
- **cooling_failure**: `cht_c`, `cht_c_resid`, `oil_temp_c`,
  `oil_temp_c_resid`, `egt_c` — physically correct, CHT-led
- **valve_wear**: `rpm`, `rpm_resid_pct`, `rpm_resid`, `egt_c` —
  physically correct, RPM-instability-led
- `roll` shows up as a secondary feature across classes — it's acting as a
  flight-phase proxy (faults are injected partway through the flight, so
  flight phase correlates with severity), not a fault signal on its own.
  Worth noting explicitly in a demo Q&A so it doesn't look like the model is
  keying off something non-physical.

See `feature_importance.png` and `confusion_matrix.png` for plots, and
`model_report.json` for the full numbers.

## Fix applied after real-world testing (Windows)

The first version used the external `shap` library's `TreeExplainer`, which
parses the XGBoost model's internal serialized format directly. That parsing
is version-sensitive: a model trained/saved on one `xgboost` version can fail
to load in `shap` on a machine with a different `xgboost`/`shap` version pair
(`ValueError: could not convert string to float` on `base_score`, seen on
Windows/Python 3.10). Fixed by switching to XGBoost's own **native SHAP
computation** (`booster.predict(..., pred_contribs=True)`) — the same exact
TreeSHAP algorithm, computed by the same library that saved the model, so
there's nothing to version-mismatch. Verified it produces identical
attribution values to the old approach. `shap` is no longer a dependency.

## What this replaces from the earlier browser demo

The interactive HTML demo's "ML model" was hand-weighted fault signatures —
plausible but not fit to any data. This pipeline is a real trained model on
your actual dataset with a genuine held-out evaluation. It can't run directly
in a browser (XGBoost isn't portable to vanilla JS), so it's now served by a
small FastAPI backend (`app.py` + `static/index.html`) that loads the
`.joblib` files and streams real predictions to the dashboard over a
WebSocket — matching the FastAPI + real-time backend architecture in the
original deck, not a browser-only mock.

## Live dashboard notes (from testing app.py end-to-end)

- The dashboard streams **real rows from your dataset** at ~5x real time
  (full 647s flight in ~2 minutes) so a fault's onset and ramp-in are visible
  within a live demo without a long wait.
- **Severity threshold recalibration**: the trained severity regressor's
  predictions peak around 0.32–0.35 for these fault runs, not the full 0–1
  scale (even at the fault's true maximum severity of 0.5–0.7). The
  dashboard's caution/critical thresholds were set to 0.12 / 0.25 to match
  this model's actual output range — using naive 0.35/0.55 thresholds (a
  reasonable-looking default before checking) would mean the safety banner
  almost never fires. This is a good example to mention to judges: it shows
  the team validated the model's real behavior rather than assuming it.
- Verified end-to-end: fault injection → detection confidence reaching
  ~100% → caution banner → critical banner → operator confirmation → the
  flight path visibly begins a descent glide on the dashboard.
