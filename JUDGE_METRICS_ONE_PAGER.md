# AegisTwin — Prototype Evidence Sheet

**SIH26054 · DRDO / DDP / iDEX · Simulation-based prototype**

## What is different

AegisTwin compares a faultable virtual piston engine with an independent healthy digital twin under the same live flight conditions. It detects correlated degradation before fixed thresholds, explains the diagnosis with exact TreeSHAP, estimates a calibrated RUL range, and compares conservative RUL with safe recovery time before recommending continue, monitor/RTB, or abort/land.

## Verified held-out results

| Evidence | Result |
|---|---:|
| Dataset | 360 trajectories / 86,856 samples |
| Profile-separated test set | 14,476 rows |
| Fault classes | Cooling, lubrication, ignition/misfire, valve wear + healthy |
| Classification macro-F1 | **0.9853** |
| Healthy false-alarm events | **0.0 per simulated hour** |
| Mean warning before conventional threshold | **86.8 s** |
| Median warning before conventional threshold | **69.5 s** |
| RUL median MAE | **2.06 simulated min** |
| Calibrated RUL interval coverage | **92.0%** |
| Runtime latency (500 frames) | **1.84 ms mean / 9.27 ms p95** |
| Dashboard stream | 5 Hz |

## Demonstration sequence

1. Start on the held-out replay profile; actual and expected engine channels agree.
2. Inject rapid lubrication degradation or valve wear.
3. Show the twin warning while the conventional threshold remains `NORMAL`.
4. Open the signed TreeSHAP evidence and decreasing RUL interval.
5. Show `RUL lower bound − safe recovery time` changing the mission recommendation.
6. Confirm or dismiss the advisory; emphasize that no autonomous flight command is sent.
7. Optional: run `start-live.ps1` to show live ArduPlane GPS/flight state, then demonstrate automatic replay fallback.

## Reproducibility and boundary

- Profiles are separated across train/validation/test; three more profiles are reserved for demos.
- The classifier is portable XGBoost UBJ. A manifest records features, ordered labels, versions, profile split, dataset row count, and SHA-256.
- Results are synthetic/simulation-defined, not flight-certified. Deployment requires dynamometer and fleet-data calibration.
- Engine sensors remain physics-generated because standard ArduPlane SITL does not emulate this MALE piston-engine ECU.

Machine-readable evidence: `ml/models/metrics.json` and `ml/models/model_manifest.json`.
