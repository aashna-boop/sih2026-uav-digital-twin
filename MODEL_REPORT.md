# Fault detection model — trained on engine_master_dataset.csv

## What changed in this revision: 3 fault classes → 7

The original dataset covered `valve_wear`, `cooling_failure`,
`oil_pressure_drop` — a good start, but DRDO's problem statement (section C,
"Fault Detection & Predictive Analytics") explicitly names four more failure
modes we weren't covering: **sensor drift/failure, misfire conditions,
injector abnormalities, and combustion instability**. This revision adds all
four, retrains on the expanded dataset, and reports the results honestly —
including where the new classes turned out to be genuinely hard to detect.
See `digital_twin.py` and `engine_physics_model.py` for the exact physics of
every fault, old and new (module docstrings there explain the reasoning
behind each one in full).

**73 independent runs** across 8 classes (was 69 runs / 4 classes), same
generation methodology as before (`generate_batch.py`): `severity ~
U(0.2, 0.9)`, `onset_frac ~ U(0.1, 0.55)`, randomized ambient temperature,
4 flight-profile archetypes, near-miss healthy runs, per-run chronological
train/test split. The dataset is otherwise unchanged in spirit — only the
fault-class list and run count grew.

### The 4 new fault classes, and why each needed its own *mechanism*

None of the 4 new faults is a clean, steady offset from the healthy target
the way the original 3 mostly are, so each needed a genuinely different
piece of physics rather than just new numbers in the same formula:

- **`misfire`** — intermittent, COMPLETE loss of one combustion event, not a
  continuous degradation. Modeled as scheduled short (0.3s) events whose
  *frequency* (down to ~1.2s apart at full severity) and *per-event
  amplitude* both grow with severity: each event dips RPM, spikes vibration,
  and briefly raises EGT (unburnt fuel afterburning in the exhaust — a real,
  documented misfire symptom on piston/automotive engines, not invented for
  this project).
- **`injector_fault`** — fuel delivery *inconsistency*, from a
  leaking/dribbling injector. Modeled as a static rich-mixture bias (fuel
  flow up, EGT **down**, RPM mildly down — over-fueling burns cooler and
  less efficiently) plus a slow, irregular random-walk wobble on top of the
  fuel-flow target, so it never looks like a clean ramp. The EGT direction
  is the deliberate, physically-real point of separation from `valve_wear`,
  which runs leaner/hotter (compression leaking past worn valves), not
  richer/cooler.
- **`combustion_instability`** — erratic combustion "without a steady
  wear-based cause" (DRDO's phrasing almost exactly). Modeled as a
  severity-scaled *inflation of sensor noise* on vibration/RPM/EGT, with
  only a small static mean bias — i.e., more **variance**, not a directional
  trend and not a dropped-out cylinder.
- **`sensor_fault`** — a decision we made deliberately, see below. The
  *engine* stays fully healthy; one randomly chosen sensor channel starts
  lying, in one of three modes chosen once per run: `drift` (slow additive
  bias), `flatline` (frozen at its pre-fault value), or `noisy` (extra
  zero-mean noise). Applied after the lag+noise pipeline, directly to the
  reported reading only — a bad sensor has no thermal mass, it just reports
  wrong instantly. It is the only class that ever touches just one channel;
  every engine fault here has a multi-channel signature by construction.

**Design decision, as asked**: does sensor drift/failure need its own label,
separate from engine health, or a flag alongside the existing ones? We gave
it its **own top-level `fault_type` label** (`sensor_fault`), for two
reasons: (1) DRDO's PS lists it as its own detectable failure mode, not a
qualifier on the others; (2) the correct operator response is different —
recalibrate or replace a sensor, not inspect the engine — matching the same
human-in-the-loop, fault-specific advisory framing the rest of this project
already uses (`ADVISORIES` in `static/index.html`). The cost of that choice
shows up directly in the results below: the classifier has no way to know
sensor_fault is categorically different from an engine fault except by
learning it from the residual pattern, and it partially fails to.

## Features — unchanged

Same 7 features as before, same reasoning: `{rpm, egt_c, cht_c, oil_temp_c,
oil_pressure_bar, fuel_flow_lph, vibration}_resid_smooth` — each a
digital-twin residual (actual − `reference_trajectory()`'s lagged healthy
expectation), then a 2.5s trailing rolling mean computed within each run.
No raw sensor values, no flight state, no new features added for the new
classes — deliberately kept identical so this revision is a fair test of
"can the *existing* feature set carry 4 more classes," not a redesign.

## Models — unchanged in structure

Same 3 XGBoost models, same training-sample reweighting to match the test
window's class balance, same native-TreeSHAP explainability. `train_model.py`
itself needed **zero structural changes** — it derives `CLASSES` from the
data and trains a generic `num_class=len(CLASSES)` classifier, so it handled
going from 4 to 8 classes automatically.

## Evaluation — same per-run chronological split

**Overall accuracy: 0.661** (66.1%), down from 0.934 on the 3-fault problem
— expected and, we think, honest: 4 of the 8 classes were deliberately
designed to be hard given a mean-residual-only feature set (see below), not
an accident of tuning.

Per-class (test set, 41,022 rows across 73 runs, later 25% of each run):

| Class | Precision | Recall | F1 | Support |
|---|---|---|---|---|
| cooling_failure | 0.85 | 0.97 | 0.91 | 4,390 |
| oil_pressure_drop | 0.84 | 1.00 | 0.91 | 4,476 |
| valve_wear | 0.72 | 0.99 | 0.83 | 4,700 |
| injector_fault | 0.73 | 0.59 | 0.65 | 4,067 |
| healthy | 0.53 | 0.77 | 0.63 | 9,564 |
| misfire | 0.57 | 0.28 | 0.37 | 4,987 |
| sensor_fault | 0.50 | 0.33 | 0.40 | 4,290 |
| combustion_instability | 0.65 | 0.25 | 0.36 | 4,548 |

The original 3 classes are essentially unaffected by adding 4 more —
`cooling_failure`/`oil_pressure_drop`/`valve_wear` all still sit at
97–100% recall, same as before. **All of the accuracy drop is concentrated
in the 4 new classes**, exactly as the physics design predicted.

### Confusion matrix (rows = true, columns = predicted; alphabetical order)

|  | comb_inst | cooling | healthy | injector | misfire | oil_press | sensor | valve |
|---|---|---|---|---|---|---|---|---|
| **combustion_instability** | 1149 | 88 | 1952 | 108 | 421 | 38 | 354 | 438 |
| **cooling_failure** | 10 | 4251 | 82 | 11 | 11 | 11 | 5 | 9 |
| **healthy** | 298 | 315 | 7402 | 430 | 454 | 171 | 207 | 287 |
| **injector_fault** | 33 | 80 | 927 | 2405 | 4 | 44 | 562 | 12 |
| **misfire** | 154 | 182 | 2191 | 86 | 1390 | 112 | 277 | 595 |
| **oil_pressure_drop** | 1 | 5 | 11 | 0 | 4 | 4454 | 1 | 0 |
| **sensor_fault** | 108 | 52 | 1333 | 242 | 126 | 503 | 1430 | 496 |
| **valve_wear** | 5 | 2 | 26 | 0 | 27 | 0 | 10 | 4630 |

## Please tell me plainly if any of these are hard to distinguish — yes, honestly:

**The single clearest, most important finding**: `misfire` and
`combustion_instability` are *not* mainly confused with each other (only
3–9% cross-confusion each way). They are both mainly confused with
**`healthy`** — 44% of `misfire` rows and 43% of `combustion_instability`
rows get called healthy. That is a **false-negative** failure mode (a real,
developing fault reads as "nothing wrong"), which is the worse direction to
fail in for a safety system, and it's worth being direct about that with
judges rather than only reporting the aggregate accuracy number.

Why: our features are a **smoothed mean** residual per channel per instant
(see `train_model.py`/`live_engine.py`) — there is no variance/spectral
feature in the model at all. `combustion_instability` was deliberately built
to manifest almost entirely as increased *variance* with a near-zero mean
shift, so it is close to invisible to this feature set by construction — a
mean-only classifier genuinely cannot see most of its signal. `misfire`'s
signal is a one-directional but brief, infrequent event (duty-cycle as low
as ~4% of the timeline at low severity); the resulting mean shift survives
smoothing but is small relative to sensor noise, so recall is weak (28%)
without being zero.

**`sensor_fault` is two very different problems wearing one label**, split
cleanly by sub-mode (diagnostic-only `sensor_fault_mode` column in the
dataset, not a model feature):

| Sub-mode | Test rows | Recall | Mostly confused with |
|---|---|---|---|
| `flatline` (frozen reading) | 2,371 | **0.48** | healthy (22%), oil_pressure_drop (15%) |
| `noisy` (extra zero-mean noise) | 1,919 | **0.16** | healthy (43%) |

`flatline` produces a genuine, growing divergence as the flight state moves
away from the value the sensor got stuck at, so it's detectable — comparably
so to `injector_fault`. `noisy` is a near-zero-mean corruption, so it hits
the exact same "invisible to a mean-only feature" wall as
`combustion_instability`, and is the single hardest sub-case in the whole
dataset (16% recall — worse than random guessing among 8 classes would
already beat, since it's mostly just called healthy). (This run's dataset
happened not to draw enough `drift`-mode test rows to report a third row
here; by construction `drift` should behave like `flatline` — a clean,
growing, single-channel divergence — since it's also a persistent additive
bias, not a zero-mean one.)

**`injector_fault`** (0.73 precision / 0.59 recall) is the best-performing
new class, because its design gives it a real, distinctive **mean** signal
(EGT down, where every other fault here either doesn't touch EGT or pushes
it up) — confirming that the classes we gave a genuine directional
mean-residual signature are learnable with this feature set, and the ones we
built around variance/near-zero-mean/single-channel corruption mostly are
not, or are only weakly so.

### Severity / RUL regression has effectively collapsed on the 8-class problem

**Overall: R² 0.019, MAE 0.181** (was R² 0.799 on the 3-fault problem). This
is a much bigger drop than classification took, and worth explaining rather
than burying: per-class R² (measured on that class's own fault rows) is
**negative** for 5 of the 7 fault classes (worse than predicting the mean
severity for every row) —

| Class | Per-class severity R² | MAE |
|---|---|---|
| cooling_failure | +0.21 | 0.13 |
| oil_pressure_drop | −0.16 | 0.14 |
| injector_fault | −0.35 | 0.18 |
| valve_wear | −0.41 | 0.12 |
| sensor_fault | −1.94 | 0.31 |
| combustion_instability | −5.14 | 0.31 |
| misfire | −6.40 | 0.37 |

One regressor is being asked to map the same 7 mean-residual features to a
"severity" whose *physical meaning is no longer comparable across classes*:
a `fault_severity` of 0.5 means a specific, large CHT delta for
`cooling_failure`, but for `sensor_fault` it means a specific-magnitude lie
on one randomly chosen channel, and for `combustion_instability` it means a
noise-variance multiplier the regressor's mean-based features can barely
see. Pooling all of that into one 0–1 regression target with no fault-type
input was a reasonable design when there were 3 physically-similar
"steady-offset" faults; it stops being reasonable at 7 classes with three
fundamentally different failure mechanisms (steady offset / event duty-cycle
/ single-channel corruption / pure variance). We did **not** attempt to fix
this in this revision — it would mean either training a separate regressor
per predicted class or adding the classifier's own output as a regression
feature, both real architecture changes beyond "retrain on the expanded
dataset" — and are flagging it plainly rather than quietly shipping a
misleading severity number for 5 of 7 fault classes. The dashboard's
severity gauge and thresholds should be treated as validated for
`cooling_failure`/`oil_pressure_drop`/`valve_wear` only until this is
revisited.

## Real SHAP explainability (native XGBoost TreeSHAP) — top features per class

- **oil_pressure_drop**: `oil_pressure_bar_resid_smooth` dominant (2.03) —
  physically correct, unchanged from before.
- **cooling_failure**: `cht_c_resid_smooth` dominant (1.00), `egt_c`/
  `oil_temp_c` secondary — physically correct, unchanged from before.
- **valve_wear**: `egt_c_resid_smooth` dominant (1.54) — unchanged from
  before.
- **injector_fault**: `egt_c_resid_smooth` dominant (1.27) — same feature as
  `valve_wear`'s top driver, but the two classes pull it in *opposite*
  directions (see above); SHAP magnitude alone doesn't show sign, so this is
  a case where the SHAP ranking looks similar across two classes but the
  underlying residual value is not — worth explaining exactly this way in a
  demo if asked.
- **misfire**: `egt_c_resid_smooth` dominant (0.64), `oil_pressure_bar` and
  `vibration_resid_smooth` secondary — vibration shows up as expected
  (event-driven spikes), though its SHAP weight is modest given how much of
  its signal is duty-cycle-diluted.
- **combustion_instability**: `egt_c_resid_smooth` dominant (0.61), but
  every feature's SHAP weight is markedly lower than for any other class —
  consistent with there being little clean mean-residual signal for the
  model to key off at all.
- **sensor_fault**: `fuel_flow_lph_resid_smooth` / `egt_c_resid_smooth` /
  `vibration_resid_smooth` roughly tied — this is the averaged fingerprint
  of a fault whose true affected channel changes every run, so no single
  feature dominates the way it does for the single-channel-consistent
  classes; this itself is a useful, honest tell that the model is not
  keying off one specific sensor for this class the way it does for the
  others.

Uses XGBoost's own native TreeSHAP (`booster.predict(..., pred_contribs=True)`),
not the external `shap` package — see the previous revision's note (still
applies): a `shap`/`xgboost` version-compatibility issue on this machine,
unrelated to this revision's changes.

## Honest caveats (carried over + new)

- **The 4 original classes are unaffected.** If a demo only needs to show
  `valve_wear`/`cooling_failure`/`oil_pressure_drop`, those numbers are
  exactly as strong as before (97–100% recall). The new classes are real,
  additional capability, not a regression in what already worked.
- **`combustion_instability` and the `noisy` sensor_fault sub-mode are, on
  the current feature set, closer to "mostly not detected" than "detected
  imperfectly."** Both are honest, deliberate consequences of using
  mean-residual-only features against faults whose real signature is
  variance, not mean. Extending the feature set (e.g., a rolling std/range
  alongside the rolling mean) is the natural next step if these two classes
  need to work better before a judged demo — we did not do this here because
  it was out of scope for "same 7-residual-feature approach."
- **Severity/RUL regression should not be presented as working across all 8
  classes.** See the collapsed-R² section above. It's still meaningful for
  `cooling_failure`/`oil_pressure_drop`/`valve_wear`.
- **The live dashboard's severity thresholds still need re-checking before
  a demo** (carried over from the previous revision, still unresolved):
  `SEV_MONITOR`/`SEV_CRITICAL` (0.12/0.25) were calibrated against the
  3-fault model's severity range and were already flagged as likely
  miscalibrated; this revision doesn't change that, and — per the point
  above — severity itself is now only reliable for 3 of 7 fault classes
  regardless of where the threshold is set.
- **One run per (fault × sub-mode) combination is thin.** With `per_fault=8`
  runs and (for `sensor_fault`) 3 sub-modes splitting those 8 runs further,
  each sub-mode's test-window numbers above come from a handful of runs.
  Directionally reliable, not a large-N result.
