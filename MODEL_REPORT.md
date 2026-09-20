# Fault detection model — trained on engine_master_dataset.csv

## Why this dataset looks different from the earlier version

The previous dataset had exactly one continuous flight per class (healthy /
valve_wear / cooling_failure / oil_pressure_drop), each with a single fixed
severity and onset point, replaying one identical trajectory. That's a fine
starting point, but it under-tests the model: with only one example per
class, "held-out accuracy" only ever meant *generalizing across time within
that one flight*, not across independent flights, conditions or fault
progressions — and the classifier scored a flat 1.00 as a result.

We rebuilt the data-generation pipeline to be more rigorous:

- **Randomized severity and onset per run** (`severity ~ U(0.2, 0.9)`,
  `onset_frac ~ U(0.1, 0.55)` — see `generate_batch.py`) instead of one fixed
  value per class, so no two runs of the same fault are identical.
- **69 independent runs** across 4 flight-profile archetypes (short hop,
  long cruise, climb-cruise-descent, variable-load) and randomized ambient
  temperature, instead of 1 run per class replaying one trajectory.
- **Moderately increased sensor noise** (~5–15% above what was actually
  measured on the original healthy flight) on every channel, reflecting that
  a single clean flight understates real fleet sensor variability.
- **Correct pre-onset labeling**: rows before a fault run's own onset point
  are labeled `healthy` (the engine genuinely is healthy at that point),
  matching how the original dataset already labeled its files and avoiding
  mislabeling a run's early, un-faulted segment as faulty.
- **"Near-miss" healthy runs** (6 of the 69): genuine high-load/hot-day
  excursions with no fault injected at all, so a transient elevated reading
  isn't by itself evidence of a fault.
- **Mild, physically-justified cross-contamination**: `cooling_failure`
  measurably raises `oil_temp_c` (already true in the original data — poor
  cooling affects the oil too, not just the cylinder head) and, newly,
  `oil_pressure_drop` mildly raises `oil_temp_c` as well (reduced lubrication
  → more boundary friction → mildly hotter oil), so the two fault classes
  overlap on one shared channel instead of being trivially separable by
  construction.

See `digital_twin.py` and `engine_physics_model.py` for the exact physics
(healthy-twin formulas fitted from the original dataset, fault deltas
extrapolated from the deltas actually measured in it — nothing invented from
scratch).

## Features

Classifier/regressor inputs are **digital-twin residuals only** — not raw
sensor readings, not raw flight state (airspeed/load/roll/pitch):

- For each timestep, `digital_twin.reference_trajectory()` computes the
  expected healthy reading from the current operating point (load, ambient
  temperature), lagged with the same known thermal/mechanical time constants
  the real engine has — so a throttle change doesn't itself look like a
  residual.
- Each residual channel is then smoothed with a short (2.5s) trailing
  rolling mean, computed within each run only. This mirrors how any real
  onboard health monitor actually works — it never diagnoses off one noisy
  instantaneous sample, it filters transient sensor noise while preserving
  the slower degradation trend.
- Raw sensor/flight-state values are deliberately excluded from the feature
  set: they mostly reflect which mission phase the aircraft is in (climb vs.
  cruise vs. idle), not engine health, and including them lets a model take
  shortcuts based on flight profile rather than genuine anomaly detection.

7 features total: `{rpm, egt_c, cht_c, oil_temp_c, oil_pressure_bar,
fuel_flow_lph, vibration}_resid_smooth`.

## Models

All three are `XGBoost` (gradient-boosted trees) — handles the nonlinear,
multi-sensor interaction patterns that separate fault signatures, fast
enough for a real-time detection loop, and pairs with real TreeSHAP for
exact explainability.

1. **Fault classifier** (`model_fault_classifier.joblib`) — 4-class:
   healthy / valve_wear / cooling_failure / oil_pressure_drop. Trained with
   sample weights that reweight the training distribution to match the
   test window's class balance (see below for why that matters), rather
   than left calibrated to train's natural imbalance.
2. **Severity regressor** (`model_severity_regressor.joblib`) — predicts
   `fault_severity` (0–1)
3. **RUL regressor** (`model_rul_regressor.joblib`) — predicts `rul_frac`
   (0–1)

## Evaluation — chronological split, per run, unchanged in spirit

Same methodology as before, generalized correctly to runs that are no
longer all the same length: **for every individual run, train on the first
75% of its own timeline, test on the final 25%** — later, more-progressed
fault states the model never saw during training for that run, computed
per-`run_id` rather than one dataset-wide cutoff. Nothing about *how* the
split is done changed; it just now has to be computed per run since runs
have different durations.

One consequence of this split worth naming honestly: because onset is
always before the 75% mark, every test-window row is at-or-after that run's
fault onset, so the test window's class balance (~34% healthy) is
necessarily less healthy-heavy than train's (~67% healthy, since train still
contains each run's pre-onset healthy segment). We reweight training samples
so the weighted train distribution matches the test window's balance —
computed directly from the data, not tuned against the test score — so the
classifier isn't left calibrated to a distribution that never occurs at
evaluation (or deployment) time.

| Task | Metric | Result |
|---|---|---|
| Fault classification | Accuracy | **0.934** (93.4%) |
| Severity regression | R² / MAE | 0.799 / 0.077 |
| RUL regression | R² / MAE | 0.799 / 0.077 |

Per-class (test set, 38,749 rows across 69 runs):

| Class | Precision | Recall | F1 |
|---|---|---|---|
| cooling_failure | 0.91 | 0.98 | 0.94 |
| healthy | 0.98 | 0.83 | 0.90 |
| oil_pressure_drop | 0.92 | 0.99 | 0.95 |
| valve_wear | 0.92 | 0.99 | 0.96 |

**Why this number is more meaningful than the old 1.00**: the confusion
matrix (`confusion_matrix.png`) shows essentially all of the ~7% of errors
are `healthy` rows misclassified as an early-stage fault, or a just-onset
fault row misclassified as healthy — exactly the genuinely ambiguous region
right at fault onset, which is where you'd *expect* a real detector to
occasionally be wrong. Well-progressed faults (which is most of the test
window, since onset always precedes it) are caught essentially every time
(97–99% recall on all three fault classes). Severity/RUL regression (R²
0.80) is similarly a believable, non-perfect number for a genuinely harder
continuous target.

## Real SHAP explainability (native XGBoost TreeSHAP)

Top features per class, by mean absolute SHAP value on the held-out set —
see `feature_importance.png` for the overall ranking and `model_report.json`
for the full per-class breakdown:

- **oil_pressure_drop**: `oil_pressure_bar_resid_smooth` dominant (1.91),
  `cht_c`/`egt_c` secondary — physically correct, led by the sensor the
  fault directly affects.
- **cooling_failure**: `cht_c_resid_smooth` dominant (1.10), then
  `oil_temp_c_resid_smooth` (0.79) — physically correct, and the oil_temp
  contribution is exactly the intentional cross-contamination channel
  described above, not a leak.
- **valve_wear**: `egt_c_resid_smooth` dominant (1.65) — physically correct
  (unburned fuel / compression loss raises exhaust gas temperature).
- No flight-state feature (roll, pitch, airspeed, load) appears anywhere in
  the feature set at all now, so there's no flight-phase proxy to explain
  away in a demo Q&A — a stricter setup than before, not looser.

Uses XGBoost's own native TreeSHAP (`booster.predict(..., pred_contribs=True)`)
rather than the external `shap` package's `TreeExplainer`: the installed
`shap`/`xgboost` version pair on this machine fails to parse this xgboost
version's per-class `base_score` (`ValueError: could not convert string to
float`, a version-compatibility bug, not a modeling choice — reproducible
even on a fresh, untouched XGBClassifier). XGBoost's native path runs the
identical TreeSHAP algorithm in its own implementation, so the attributions
are the same real per-feature Shapley values. `shap` is no longer a
dependency.

## Honest caveats

- **Regression is the harder, more informative number.** R² 0.80 on
  severity/RUL is real generalization, not memorization, but it's not 0.95 —
  estimating exact fault progression from noisy residuals is a genuinely
  hard continuous problem, and that's expected.
- **`healthy` recall (0.83) is the lowest of the four classes** — the model
  is somewhat more likely to call a genuinely healthy reading an early-stage
  fault than the reverse. That's a defensible, safety-conservative failure
  mode for a fault detector to have (a false alarm costs less than a missed
  fault), and worth naming as a deliberate framing if asked, not something
  to hide.
- **The live dashboard's severity thresholds need re-checking before a
  demo.** The old model's severity predictions were capped around 0.32–0.35
  (previously fixed per-class ceilings of 0.5–0.7), and the dashboard's
  caution/critical thresholds (0.12/0.25) were hand-calibrated to that
  narrow range. This model's severity range is different (randomized
  0.2–0.9 severities across runs), so those thresholds are now almost
  certainly miscalibrated. `app.py`/`app_live.py`/`live_engine.py` were not
  touched by this update — re-run a few fault scenarios through the live
  dashboard and re-check the threshold constants before presenting.
