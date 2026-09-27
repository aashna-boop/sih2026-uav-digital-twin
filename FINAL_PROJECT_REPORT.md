# UAV piston-engine digital twin — final project report

**Project**: SIH26054 — DRDO, AI-enabled digital twin for MALE UAV piston
engine health monitoring
**Repo**: `sih2026-uav-digital-twin`, branch `feature/extended-fault-detection`
(PR [#7](https://github.com/aashna-boop/sih2026-uav-digital-twin/pull/7))
**This report's baseline**: everything merged into `main` plus this PR, as of
2026-09-24.

*This is the single, currently-accurate account of the whole project —
what it does, how it works, what it actually achieves, and what's still
missing. Other docs in this repo (`README.md`, `docs/*.md`) describe earlier
states of the project and are now partly stale in specific, identified ways
(see [§9](#9-document-map--what-else-exists-in-this-repo)); this report
supersedes their factual claims about current results, class counts and
accuracy numbers. It does not rewrite or delete them.*

---

## 1. What this project is, in one paragraph

A MALE UAV (Medium Altitude Long Endurance drone) flies for hours on a
piston engine. This project watches that engine's sensors, compares them
against a **physics-based digital twin's** prediction of what a healthy
engine should read *at this exact throttle setting and ambient
temperature*, and uses a trained ML model on the resulting deviation
("residual") to name which of 7 specific fault types is developing, how
severe it is, how much runway is left, and *why* (a real SHAP
explanation) — surfaced on a live dashboard where a human operator, not the
software, decides what to do about it. Two working demo modes exist: replay
a labeled dataset, or drive the same pipeline from a real, currently-running
ArduPilot flight simulator (SITL) over MAVLink.

## 2. How the project evolved — the honest timeline

This project went through several real, substantive iterations, not just UI
polish. Understanding *why* the numbers changed at each step is as important
as the numbers themselves.

| When | What changed | Who |
|---|---|---|
| 2026-09-01 | Initial commit: physics simulator + 3-fault dataset (one fixed severity per class, single flight trajectory) + first XGBoost models + dashboard | Aashna |
| 2026-09-02 | Frontend fixes, flight-state features removed (were acting as a time-of-flight proxy rather than a genuine health signal) | Navneet, Isha |
| 2026-09-14 | A separate, unrelated prototype ("AegisTwin") was briefly substituted into the branch, then reverted back to this project's own baseline | Rijul |
| 2026-09-19 | **Section F**: efficiency trend charts, maintenance advisories, mission-wise health reports added to the dashboard | Isha |
| 2026-09-20 | Finals documentation: deployment roadmap, modular-architecture argument, requirements traceability register (`docs/`) — written against the pre-retrain, 4-class/25-feature baseline | Rijul |
| 2026-09-20 | **7-feature retrain**: replaced the 25-raw-feature model with a 7-feature, digital-twin-residual-only model (`{sensor}_resid_smooth`); fixed a live feature-mismatch crash; added a severity/RUL trend view; ported `digital_twin.py`/`engine_physics_model.py` (randomized severity/onset, 4 flight archetypes, 69 runs, 4 classes) | Aashna (this assistant, PR [#5](https://github.com/aashna-boop/sih2026-uav-digital-twin/pull/5)) |
| 2026-09-20 | **Section E**: altitude/density and battery/alternator physics, 4 environmental-scenario replay archetypes (high-altitude cruise, hot-weather ops, rapid throttle transitions, endurance mission) | Navneet |
| 2026-09-24 | **This PR**: extended fault detection from 3 to 7 classes (misfire, injector_fault, combustion_instability, sensor_fault), retrained on 73 runs/8 classes, wired into both dashboards, added `DEPLOYMENT_ROADMAP.md` and `RESEARCH_GROUNDING.md`, fixed 2 unrelated crash/logic bugs found along the way | Aashna (this assistant, PR [#7](https://github.com/aashna-boop/sih2026-uav-digital-twin/pull/7)) |

**Why the headline accuracy number has changed twice, and why that's a good
sign, not a bad one**: the project reported 100% accuracy on the very first
iteration (1 flight per class, 1 fixed severity), then 93.4% after the
7-feature retrain (69 runs, randomized severity/onset, 4 classes — a
genuinely harder, more realistic test), then 66.1% after this PR (73 runs,
8 classes, 4 of which were deliberately built to be hard). Each drop came
from making the evaluation *more honest*, not from the system getting worse
at what it already did — the original 3 fault classes still score
97–100% recall today, unchanged since the 7-feature retrain.

## 3. Architecture — end to end

```text
                    ┌─────────────────────────────────────────┐
                    │        digital_twin.py                   │
                    │  fitted healthy-engine reference model    │
                    │  (load, ambient, altitude) -> expected     │
                    │  sensor readings, + thermal/mechanical      │
                    │  lag constants, + battery/alternator model  │
                    └───────────────┬───────────────┬───────────┘
                                    │                │
                (offline dataset)   │                │  (live SITL)
                                    ▼                ▼
                    engine_physics_model.py     live_engine.py
                    - randomized flight          - same physics,
                      archetypes                   one sample at a
                    - 7 fault mechanisms            time, driven by
                      (steady bias / event           real MAVLink
                      schedule / noise               flight state
                      inflation / single-
                      channel corruption)
                                    │                │
                                    ▼                ▼
                    generate_batch.py           app_live.py
                    -> data/runs/*.csv          (SITL connection,
                    combine_datasets.py          fault injection,
                    -> engine_master_             MAVLink response
                       dataset.csv                commands)
                                    │                │
                                    ▼                │
                            train_model.py            │
                    (7 residual-smooth features,       │
                     chronological per-run split,       │
                     XGBoost x3, native TreeSHAP)         │
                                    │                      │
                                    ▼                      ▼
                         model_*.joblib  ─────────►  predict_row()
                         model_report.json           (identical logic
                                                       in app.py and
                                                       app_live.py)
                                    │                      │
                                    └──────────┬───────────┘
                                               ▼
                                  WebSocket /ws payload
                                  (prediction, SHAP, history)
                                               │
                                               ▼
                          static/index.html  or  frontend/ (React)
                     live telemetry · diagnosis · efficiency trends ·
                     maintenance advisory · mission reports · safety
                     banner requiring human confirmation
```

**The one governing design principle, stated once**: the ML model never
sees a raw sensor value or raw flight state. It only ever sees *how far the
actual reading has drifted from what the digital twin says it should be at
this exact operating point*, smoothed over a short trailing window. This
is what makes the residual a genuine health signal instead of a proxy for
"which part of the mission is this."

## 4. The data-generation pipeline

1. **`digital_twin.py`** — the single source of truth for "what should a
   healthy engine read right now." Every sensor formula (`rpm`, `egt_c`,
   `cht_c`, `oil_temp_c`, `oil_pressure_bar`, `fuel_flow_lph`, `vibration`)
   is fit by regression against one real recorded SITL flight — not
   invented. Includes thermal/mechanical lag time constants (CHT/oil temp
   lag seconds-to-tens-of-seconds behind a throttle change; RPM/oil
   pressure track almost instantly), an ISA-standard-atmosphere altitude
   correction, and a battery/alternator baseline model.
2. **`engine_physics_model.py`** — takes a randomized flight profile (one
   of 4 core archetypes: short hop, long cruise, climb-cruise-descent,
   variable-load; plus 4 Section-E environmental-scenario archetypes used
   only for live demo playback, not for training data) and generates one
   run's full telemetry, with realistic sensor noise and, optionally, one
   of **7 injected faults**:
   - `valve_wear`, `cooling_failure`, `oil_pressure_drop` — the original 3,
     each a steady physical offset from the healthy target that ramps in
     after a randomized onset.
   - `misfire` (new) — scheduled short, complete combustion-loss events;
     both frequency and per-event size grow with severity.
   - `injector_fault` (new) — a rich-mixture mean bias (EGT **down**, the
     opposite direction from `valve_wear`) plus an irregular fuel-flow
     wobble.
   - `combustion_instability` (new) — inflates sensor *noise* rather than
     shifting the mean; "erratic... without a steady wear-based cause."
   - `sensor_fault` (new) — corrupts one randomly chosen sensor channel's
     *reported reading* only (drift / flatline / noisy); the engine itself
     stays genuinely healthy. Given its own top-level label rather than a
     flag alongside engine health, because the correct operator response
     is categorically different (recalibrate a sensor, not inspect the
     engine).
3. **`generate_batch.py`** — calls the simulator many times with
   `severity ~ U(0.2, 0.9)`, `onset_frac ~ U(0.1, 0.55)`, randomized ambient
   temperature and archetype, per the project's own methodology spec.
   **73 runs** in the current dataset (was 69 before this PR): 8 classes
   (7 faults + healthy), including 4 "near-miss" healthy runs (a genuine
   high-load/hot excursion with no fault, so a transient elevated reading
   isn't by itself evidence of a fault).
4. **`combine_datasets.py`** — merges every run into
   `engine_master_dataset.csv` (**~164,000 rows**, ~69 MB).

## 5. The ML models — what they actually achieve, reported honestly

Three XGBoost models, trained by `train_model.py` on 7 features:
`{rpm, egt_c, cht_c, oil_temp_c, oil_pressure_bar, fuel_flow_lph,
vibration}_resid_smooth` — each a digital-twin residual, smoothed with a
2.5s trailing mean computed within each run only. Evaluated with a
**per-run chronological split** (train on each run's first 75%, test on the
last 25% — later, more-progressed fault states the model never saw for that
run), with training samples reweighted to match the test window's class
balance. Full detail, per-class numbers and the confusion matrix are in
[`MODEL_REPORT.md`](MODEL_REPORT.md); the essentials:

| Metric | Result |
|---|---|
| Overall classification accuracy (8 classes) | **66.1%** |
| `cooling_failure` / `oil_pressure_drop` / `valve_wear` recall | 97–100% (unchanged from the 4-class version) |
| `injector_fault` precision / recall | 73% / 59% |
| `misfire` precision / recall | 57% / 28% |
| `combustion_instability` precision / recall | 65% / 25% |
| `sensor_fault` precision / recall (splits by sub-mode) | 50% / 33% (flatline 48%, noisy 16%) |
| `healthy` precision / recall | 53% / 77% |
| Overall severity/RUL regression R² | **0.02** (was 0.80 on the 3-fault version) |

**The two findings worth remembering over any single number**:

1. **`misfire` and `combustion_instability` are mostly confused with
   "healthy," not with each other.** That's a false-negative pattern (a
   real, developing fault reads as nothing wrong), the more dangerous
   direction to be wrong in for a safety system — worth saying to judges
   directly rather than only citing the aggregate accuracy figure.
2. **Severity/RUL regression broke down, not because of a bug, but because
   one 0–1 "severity" number stopped meaning something comparable across 7
   physically different fault mechanisms** (a steady offset, an event
   duty-cycle, a noise inflation, and a single-channel corruption don't
   share a natural severity scale). This is flagged plainly in
   `MODEL_REPORT.md` rather than shipped as a misleading number.

Both are genuine, useful engineering findings — precisely the kind of
result a rigorous evaluation is supposed to surface, and exactly what was
asked for when this extension was scoped: *"tell me plainly if any of these
fault types turn out to be physically very hard to distinguish... that's
useful information even if it's not what I'd hoped to hear."*

**Explainability**: every prediction carries a real SHAP explanation from
XGBoost's native `pred_contribs` (not the external `shap` package, which
has a version-compatibility issue on this project's environment — see
`MODEL_REPORT.md`). For `oil_pressure_drop`, `oil_pressure_bar_resid_smooth`
dominates; for `cooling_failure`, `cht_c_resid_smooth` does — physically
correct in both cases.

## 6. The dashboards

Both dashboards (`app.py` replay, `app_live.py` live-SITL) share
`static/index.html` (or the equivalent React app in `frontend/`) and the
exact same trained models and `predict_row()` logic.

- **Live telemetry & diagnosis**: current sensor readings vs. twin-expected,
  predicted fault + confidence, severity gauge, RUL estimate, real
  per-prediction SHAP attribution.
- **Fault injection**: buttons for all 7 fault classes (extended from 3 in
  this PR) — replay streams a representative recorded run; live SITL
  injects the fault into the real-time physics simulation, ramping in over
  ~15 wall-clock seconds for a visible demo.
- **Safety banner & human-in-the-loop**: caution/critical banners at fixed
  severity thresholds (`SEV_MONITOR=0.12`, `SEV_CRITICAL=0.25`), requiring
  explicit operator confirmation before any response — in live SITL mode, a
  few real MAVLink commands (throttle override, RTL, a GUIDED altitude
  change) are actually sent on confirmation; `sensor_fault` deliberately
  sends none, since it isn't an engine fault.
- **Engine efficiency trends** (Section F, Isha): fuel efficiency,
  lubrication, CHT excess, model health, and remaining-life indices,
  smoothed and plotted live with slope-per-minute.
- **Maintenance advisory** (Section F): fault-specific, severity-graded
  checklists (now covering all 7 fault types), debounced against classifier
  flicker, latched with SHAP-based evidence once confirmed.
- **Mission-wise health reports** (Section F): every run recorded, exported
  as CSV/JSON/print, with detection latency vs. ground truth, peak
  severity, and a full event timeline.
- **Environmental scenarios** (Section E, Navneet): 4 pre-generated healthy
  flights (high-altitude cruise, hot-weather ops, rapid throttle
  transitions, endurance mission) showing how the digital twin's own
  expected values adapt to different conditions, plus live battery/
  alternator telemetry in the payload.

**Severity thresholds are still an open item, unresolved across every
revision of this project so far**: `0.12`/`0.25` were hand-calibrated
against an early model's narrow severity range, flagged as likely stale
after the 7-feature retrain, and — per §5 above — severity is now only
reliable for 3 of 8 classes regardless of where the threshold sits. This
needs a deliberate decision, not a copy-paste number change; see
`MODEL_REPORT.md` for the exact constant locations (there are 4 copies
across `static/index.html` and `frontend/src/lib/missionAnalytics.js` /
`DiagnosisPanel.jsx`, 2 of which were consolidated to import from a single
source in the 7-feature-retrain PR).

## 7. Deployment roadmap and research grounding

Two new documents this PR adds, directly answering DRDO's "Deliverables
Expected from Teams" (technical documentation and deployment roadmap):

- **[`DEPLOYMENT_ROADMAP.md`](DEPLOYMENT_ROADMAP.md)** — honest current
  state (a simulation-validated software prototype, not yet touching real
  sensors), what real CAN-bus/ECU integration would require, and a 4-phase
  plan: Phase 1 simulation-validated prototype (**done**) → Phase 2
  hardware-in-the-loop testing → Phase 3 edge deployment (model
  quantization/pruning, onboard inference split) → Phase 4 fleet
  aggregation. Only Phase 1 is claimed as complete.
- **[`RESEARCH_GROUNDING.md`](RESEARCH_GROUNDING.md)** — the methodological
  parallel to NASA's C-MAPSS-style synthetic turbofan degradation datasets
  (physics simulation → labeled RUL dataset → supervised prognostics model)
  and to physics-informed/digital-twin residual fault diagnosis generally.
  Deliberately states methodology, not fabricated citations.

**Note on `docs/DEPLOYMENT_ROADMAP.md`**: there is a second,
differently-scoped deployment roadmap already in this repo, written by
Rijul, focused on modular architecture, CAN/FADEC vendor integration
contracts and a security architecture. It predates this PR and describes
the pre-retrain (4-class, 25-feature) baseline in its evidence tables. The
two documents don't contradict each other's *recommendations* — they cover
different layers (this PR's roadmap: the ML/detection system's phased
validation path; Rijul's: the software architecture's modularity and
security engineering path) — but a team member should reconcile the class
counts/feature descriptions in `docs/FINALS_REQUIREMENTS_TRACEABILITY.md`'s
evidence register before a judged demo, since it currently states "25
features; four class labels," both now out of date.

## 8. Honest limitations — consolidated across the whole project

- **All 73 runs are synthetic (physics-simulated)**, not independent real
  SITL flights or real engine data — see `DEPLOYMENT_ROADMAP.md` for
  exactly what real-hardware validation would require.
- **`combustion_instability` and the "noisy" sensor_fault sub-mode are
  close to undetectable** with the current mean-residual-only feature set
  (25% and 16% recall). A rolling std/range feature is the natural next
  step, out of scope for this PR.
- **Severity/RUL regression is only reliable for 3 of 8 classes**
  (`cooling_failure`, `oil_pressure_drop`, `valve_wear`) — see §5.
- **No unknown-fault fallback**: the classifier always picks one of its 8
  known classes, even for a fault type it's never seen.
- **Severity/critical alert thresholds have never been recalibrated**
  against any post-3-fault model revision — flagged at the 7-feature
  retrain, still flagged now.
- **The live dashboard depends on a working SITL connection** on the
  expected port; no connection means a "not connected" state, not
  fabricated data — the correct behavior, but a hard external dependency
  for the live demo.
- **The project's own architecture review** (`docs/MODULAR_ARCHITECTURE.md`,
  `docs/FINALS_REQUIREMENTS_TRACEABILITY.md`) independently identifies
  further, complementary gaps not duplicated here: duplicated inference
  code between `app.py`/`app_live.py`, no per-engine state isolation (a
  second connected engine would share state with the first), no
  authentication/encryption on the WebSocket, and no persistent
  (server-side) mission report storage — all real, all still open.

## 9. Document map — what else exists in this repo

| Document | Status | What it's for |
|---|---|---|
| `README.md` | **Stale** — describes the original 4-class, 100%-accuracy, R²=0.59 version; the fault list it names ("valve wear, cooling failure, oil pressure drop, ignition fault") doesn't match any version of the actually-trained model (there has never been a trained "ignition fault" class) | First-look project pitch; needs a refresh pass |
| `PROJECT_DOCUMENTATION.md` | Current — updated through this PR | Full plain-language write-up, file-by-file |
| `MODEL_REPORT.md` | Current — updated through this PR | Full technical model results, per-class, per-revision |
| `DEPLOYMENT_ROADMAP.md` (this PR, root) | Current | ML/detection system's phased path to real deployment |
| `RESEARCH_GROUNDING.md` (this PR, root) | Current | Methodological grounding vs. prior work |
| `FINAL_PROJECT_REPORT.md` (this file) | Current | This document — the single up-to-date synthesis |
| `docs/DEPLOYMENT_ROADMAP.md` | Baselined 2026-09-19, **describes the pre-retrain system** | Software architecture's CAN/FADEC/security/fleet engineering plan |
| `docs/MODULAR_ARCHITECTURE.md` | Baselined 2026-09-19, **describes the pre-retrain system** | Modularity/swappability argument for judges |
| `docs/FINALS_REQUIREMENTS_TRACEABILITY.md` | Baselined 2026-09-19, **evidence table now stale** (states 25 features / 4 classes) | Requirement-by-requirement evidence register |

## 10. How to run it

```bash
git clone https://github.com/aashna-boop/sih2026-uav-digital-twin
cd sih2026-uav-digital-twin
git checkout feature/extended-fault-detection   # or main, once PR #7 is merged
pip install -r requirements.txt
```

**Replay mode** (no SITL needed):
```bash
python -m uvicorn app:app --port 8000
```
Open http://localhost:8000 and use the 7 fault buttons.

**Live mode** (SITL required, tested via WSL on this Windows machine):

Terminal 1:
```bash
cd ~/ardupilot/ArduPlane
../Tools/autotest/sim_vehicle.py --console --map --out=udp:127.0.0.1:14551
```
Terminal 2:
```bash
PYTHONUNBUFFERED=1 python3 -m uvicorn app_live:app --host 127.0.0.1 --port 8000
```
Open http://localhost:8000 — the dashboard shows a "Waiting for SITL"
overlay until real telemetry arrives, then reveals itself automatically.

To regenerate the dataset and retrain from scratch:
```bash
python generate_batch.py --clean --healthy_normal 13 --healthy_near_miss 4 --per_fault 8
python combine_datasets.py
python train_model.py
```

## 11. Team contributions (by commit history)

- **Aashna** — initial physics simulator, dataset and models; 7-feature
  digital-twin residual retrain; connection-stability fixes; this PR's
  7-class fault extension, dashboard wiring, deployment roadmap and
  research grounding docs.
- **Isha Pareek** — Section F (efficiency trends, maintenance advisory,
  mission-wise health reports) and the initial flight-state feature
  cleanup that removed a time-of-flight proxy from the feature set.
- **Navneet Kumar / upesnavneet** — Section E (altitude/density physics,
  battery/alternator simulation, 4 environmental-scenario archetypes),
  frontend fixes, merge coordination.
- **Rijul Mittal** — finals documentation (deployment roadmap, modular
  architecture argument, requirements traceability register).
- **Madhav Tiwari / dractonixx / codemaddy17** — README/merge contributions.

---

*This report was generated by an AI coding assistant (Claude, via Claude
Code) working directly in this repository, cross-checked against the
project's actual code, trained model outputs, and git history rather than
summarized from memory. Every number above is reproducible from
`model_report.json` and the commands in §10.*
