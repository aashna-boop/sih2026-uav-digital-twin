# SIH26054 Prototype Status

## Current milestone

The first complete vertical slice is operational:

```text
Replay flight profile
-> virtual piston-engine plant
-> independent healthy digital twin
-> normalized residuals
-> trained fault classifier
-> calibrated RUL interval
-> mission reliability decision
-> FastAPI/WebSocket
-> React dashboard
```

## Completed

- [x] Frozen real-time JSON contract
- [x] Oil pressure standardized in bar
- [x] ISA ambient temperature plus hot/cold offset
- [x] Five-hertz replay-first telemetry
- [x] Independent plant and healthy twin
- [x] Bounded plant/twin mismatch
- [x] Cooling degradation through physical cooling coefficients
- [x] Lubrication degradation through pump efficiency
- [x] Ignition/misfire through combustion efficiency
- [x] Correlated multi-sensor fault propagation
- [x] Residual-based deterministic health index
- [x] 15 parameterized mission profiles
- [x] Three profiles reserved from model development
- [x] 360 reproducible trajectories
- [x] 86,856 generated samples
- [x] All 288 fault trajectories reach the failure event
- [x] Profile-level train/validation/test split
- [x] Portable XGBoost five-class fault classifier
- [x] Exact native TreeSHAP evidence for every diagnosis
- [x] Versioned model manifest with dataset hash and library versions
- [x] Three quantile RUL models
- [x] Validation-profile calibration of the RUL interval
- [x] Four-frame temporal persistence selected from the held-out test profiles
- [x] Mission Reliability Envelope
- [x] Traditional threshold versus twin comparison
- [x] FastAPI REST and WebSocket API
- [x] React operations dashboard
- [x] Scenario controls for all four faults
- [x] Physical valve-wear propagation through volumetric/combustion efficiency
- [x] Replay reset and degradation-rate controls
- [x] Operator confirm/dismiss acknowledgement with advisory-only semantics
- [x] Mission-action hysteresis prevents abort/monitor alert chatter
- [x] Live flight-path trace and mission event log
- [x] Automated vertical-slice tests
- [x] Production frontend build

## Measured synthetic test results

These results are simulation-based and must not be represented as real-engine validation.

| Metric | Result |
|---|---:|
| Profile-separated test rows | 14,476 |
| Classification macro-F1 | 0.9853 |
| False-alarm events per healthy simulated hour | 0.0 |
| Mean warning lead time before threshold | 86.8 s |
| Median warning lead time before threshold | 69.5 s |
| RUL MAE | 2.06 simulated min |
| Calibrated RUL interval coverage | 92.0% |
| RUL test samples | 7,264 |
| Live stream rate | 5 Hz |
| Runtime step latency, 500-frame local benchmark | 1.84 ms mean / 9.27 ms p95 |

Authoritative machine-readable metrics are in `ml/models/metrics.json`.

## Verified live demonstration

The rendered application has been exercised through this sequence:

1. Healthy mission: twin reports healthy and `CONTINUE`.
2. Rapid lubrication fault injected.
3. The trained model detects lubrication degradation while conventional thresholds remain normal.
4. RUL decreases and the initial action becomes `MONITOR_RTB` while the safety margin remains positive.
5. As degradation progresses, oil pressure crosses its conventional limit.
6. Conservative RUL falls below safe recovery time.
7. The recommendation changes to `ABORT_LAND`.

## Current limitations and next work

- [x] Connect WSL2/ArduPilot SITL to the Windows backend with `pymavlink`.
- [x] Keep replay as the default judging path and automatic live-mode fallback.
- [ ] Add MAVLink `EFI_STATUS` emission/ingestion.
- [ ] Calibrate healthy residual sigma from real SITL profiles rather than initial engineering values.
- [ ] Record or import 12-15 real ArduPlane SITL profiles.
- [ ] Use one of the explicitly held-out catalog profiles in the final replay demo.
- [x] Add a printable one-page metrics sheet.
- [x] Add a reproducible held-out confusion-matrix report.
- [ ] Add Cesium only after the SITL adapter is stable.
- [x] Add exact native TreeSHAP with deterministic normalized-residual fallback.

## Held-out profile IDs

These are excluded from model development and reserved for later demonstration:

- `high_altitude_headwind`
- `maritime_tailwind`
- `relay_calm`
## Live ArduPilot integration

- ArduPlane SITL runs inside Ubuntu 24.04 / WSL2.
- `pymavlink` receives live position, airspeed, climb rate, throttle and mode
  through a dedicated outbound TCP connection to WSL port 5770.
- Live flight state drives both the faultable virtual plant and independent
  healthy engine twin without allowing injected faults into the flight source.
- Source-aware replay fallback keeps the judging demo deterministic if SITL or
  venue networking is unavailable.
- `start-live.ps1` launches SITL, backend and dashboard as one stack.
- The dedicated no-MAVProxy stream is ArduPlane `SERIAL0` on TCP 5770, avoiding
  the default port's startup wait and leaving the connection owned by AegisTwin.

## Latest verification

- 11/11 Python vertical-slice tests pass.
- The Vite production dashboard build passes.
- Replay REST and WebSocket telemetry were exercised with valve wear, TreeSHAP,
  and operator acknowledgement.
- Live ArduPlane telemetry was observed at the Windows backend with real GPS
  coordinates, followed by verified automatic replay fallback after SITL exit.
