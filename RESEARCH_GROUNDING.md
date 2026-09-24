# Research grounding — how this project's approach connects to prior work

*A short note for judges/reviewers on where this project's methodology comes
from, written to be checkable rather than name-dropped. We describe the
methodological parallels we're actually applying, not citations we can't
verify — no paper titles, authors or DOIs are asserted here that we haven't
directly confirmed.*

## 1. The core idea isn't new — we're applying a known, credible pattern to a new domain

The overall shape of this project — **simulate degradation on top of a
physics-based reference, label the simulated data with the true remaining
life / severity, train a supervised model on the resulting sensor time
series** — is the same methodological pattern used by NASA's Prognostics
Center of Excellence for its widely-used **C-MAPSS** (Commercial Modular
Aero-Propulsion System Simulation) turbofan degradation datasets, which have
been a standard benchmark in the prognostics-and-health-management (PHM)
research community for over a decade.

The parallel, concretely:

| C-MAPSS (turbofan engines) | This project (piston UAV engine) |
|---|---|
| A physics-based turbofan simulation models multiple engine subsystems (fan, compressors, turbines) under different flight conditions and fault modes | `digital_twin.py` + `engine_physics_model.py` model a piston engine's thermal/mechanical response to load and ambient temperature, with fault-specific perturbations |
| Degradation is injected and progressed over a run until a defined failure threshold | `fault_severity` ramps from an onset point per run, per `generate_batch.py`'s randomized methodology |
| Each run is labeled with ground-truth Remaining Useful Life (RUL) at every timestep, computed from the simulation itself (not measured after the fact) | `rul_frac` is computed directly from the simulation's own severity state at every timestep, the same way |
| Multiple operating conditions and fault modes are simulated across many independent runs, not one fixed trajectory | `generate_batch.py` randomizes severity, onset, ambient temperature and flight-profile archetype across 73 runs and (as of this revision) 7 fault classes |
| Sensor readings are synthetic but physically grounded, not statistically fabricated | Every sensor formula in `digital_twin.py` is fit by regression against a real recorded flight, not invented; fault magnitudes are extrapolated from real measured deltas (see `engine_physics_model.py`'s module docstring) |
| A held-out test set (typically different simulated units/runs) evaluates whether a trained model generalizes RUL/fault prediction to units it never trained on | `train_model.py`'s per-run chronological split (train on each run's first 75%, test on the last 25%) evaluates on later, more-progressed fault states never seen for that run |

**What's genuinely different here, stated plainly**: C-MAPSS models a turbine
jet engine's aerodynamic/thermodynamic subsystems; this project models a
much simpler piston reciprocating engine's thermal and mechanical response.
The fault-injection philosophy and the "physics simulator → labeled
degradation dataset → supervised prognostics model" pipeline is the same
*idea*, applied to a different, much smaller and more tractable engine
class — which is also why this project's dataset generation (a few scripts,
runs in seconds) is orders of magnitude simpler than a full C-MAPSS-style
turbofan simulation would be. We are not claiming to have reproduced
C-MAPSS or validated against its published benchmarks; we are stating that
we deliberately followed the same *methodology* for a reason PHM researchers
already validated: it's the practical way to get labeled, physically
grounded degradation data when you can't run hundreds of real engines to
failure.

## 2. Digital-twin-based residual fault detection is an established diagnosis pattern

The specific technique this project's ML pipeline is built on —
**physics-based reference model → residual (actual − expected) → learned
classifier/regressor on the residual** — is the standard architecture
described in the broader physics-informed / digital-twin fault-diagnosis
literature, and matches this project's own SIH proposal architecture
("physics/reference model → residual → ML fault detection"), which
`train_model.py`'s and `digital_twin.py`'s docstrings already reference
directly.

The reasoning this project applies, and why it's a recognized approach
rather than an ad hoc choice:

- **A raw sensor reading conflates two things**: the engine's actual health,
  and which flight-phase/throttle setting the aircraft happens to be in
  right now. A residual against a *load- and ambient-conditioned* reference
  (not a fixed baseline) removes the second factor, so what's left is closer
  to a pure health signal. This is exactly why `digital_twin.py`'s reference
  model is a function of `(load, ambient_offset_c)`, not a fixed healthy
  value, and why raw sensor/flight-state values are deliberately excluded
  from the feature set (see `MODEL_REPORT.md`, "Features").
- **Thermal/mechanical lag has to be modeled in the reference itself**, not
  just the fault. A cylinder head doesn't jump to a new temperature the
  instant throttle changes — modeling that lag in `reference_trajectory()`
  (not just in the "actual" simulated reading) is what stops a normal
  throttle change from itself looking like a fault residual. This is a
  standard digital-twin design point: the twin has to have the same dynamics
  as the real system, not just its steady-state value.
- **Temporal smoothing before diagnosis** (the 2.5s trailing mean, see
  `MODEL_REPORT.md`'s Features section) mirrors standard practice in
  condition-monitoring systems: filter transient sensor noise while
  preserving the slower degradation trend, rather than diagnosing off one
  noisy instantaneous sample. This is a general engineering-reliability
  practice (not unique to any one paper), applied consistently here.

## 3. Where this project's evaluation methodology is deliberately more conservative than a naive approach

Two choices in `train_model.py` exist specifically because a naive
evaluation would have overstated performance — worth naming as evidence of
methodological care, not just results:

- **Chronological, per-run splitting instead of a random row split.**
  Adjacent rows at this dataset's 4 Hz sampling rate are highly
  autocorrelated; a random split would let the model see rows from
  essentially the same moment in training and test, inflating accuracy
  without testing real generalization. Splitting by time *within each run*
  (train on the first 75%, test on the last 25%) tests generalization to
  *later, more-progressed* fault states specifically — the actual
  deployment-relevant question ("will this catch a fault further along than
  anything it trained on for this flight").
- **Sample reweighting to match the test window's class balance**, computed
  from the data itself (not tuned against the test score) — because the
  chronological split structurally makes the test window less
  "healthy-heavy" than the full training set (every test-window row is
  at-or-after that run's fault onset). Leaving the classifier calibrated to
  training's natural imbalance would silently bias it away from the
  distribution it's actually evaluated (and would be deployed) against.

## 4. What we are explicitly NOT claiming

- We are not citing specific C-MAPSS papers, PHM conference papers, or
  digital-twin fault-diagnosis papers by name, author or DOI, because we
  have not verified specific claims against primary sources for this note —
  doing so honestly would require reading and confirming those sources, not
  asserting them from memory. The parallels above describe *methodology*,
  which is checkable directly against this project's own code
  (`digital_twin.py`, `engine_physics_model.py`, `train_model.py`), not an
  appeal to authority.
- We are not claiming this project's results are comparable in scale,
  rigor, or engine complexity to a full aerospace PHM benchmark. 73 runs
  generated by a physics-approximation simulator is a defensible
  proof-of-concept methodology, not a substitute for fleet data or an
  independently validated turbofan-scale simulation.
