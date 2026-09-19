# Finals deliverables: evidence, gaps and acceptance register

**Owner:** Rijul — Documentation & Architecture  
**Baseline:** `main` at `11986638fd5f56beb4b1e38cacdbeb388704dc85`  
**Date:** 19 September 2026  
**Readers:** judges and engineering team

## 1. Scope and status definitions

This register covers the team's requested documentation deliverables. Official Section A wording is unavailable; identifiers below are local tracking IDs, not official requirement numbers. No deadline or hardware vendor has been selected.

- **Source-evidenced:** functionality or separation visible in reviewed source; not a claim of new runtime testing.
- **Partial:** relevant building blocks exist but the complete claim is not demonstrated.
- **Proposed:** design and acceptance criteria documented; not implemented by this documentation task.
- **Future scope:** acknowledged capability awaiting data, hardware or model work.

## 2. Deliverable coverage

| ID | Requested outcome | Current evidence/status | Documented response | Acceptance evidence still needed |
|---|---|---|---|---|
| R-D01 | Standalone deployment roadmap | Partial: local FastAPI/React applications | [Roadmap §§3–4](DEPLOYMENT_ROADMAP.md#3-deployment-stages-and-acceptance-gates) | Clean install, offline operation, durable recording and restart tests |
| R-D02 | CAN/FADEC integration path | Proposed: current live adapter ingests flight context, not ECU measurements | [Roadmap §5](DEPLOYMENT_ROADMAP.md#5-vendor-neutral-can-bus--fadec-integration) | Vendor interface mapping, decoder fixtures and approved bench evidence |
| R-D03 | Edge/onboard deployment | Proposed: no onboard resource evidence reviewed | [Roadmap §6](DEPLOYMENT_ROADMAP.md#6-edgeonboard-deployment) | Hardware selection, latency/thermal/power/storage and outage measurements |
| R-D04 | Secure telemetry architecture | Proposed: plain WebSocket and no application identity layer in baseline | [Roadmap §7](DEPLOYMENT_ROADMAP.md#7-secure-telemetry-architecture) | Authentication, encryption, replay/revocation and isolation tests |
| R-D05 | Fleet monitoring vision | Partial: mission reports exist in the browser; no fleet backend | [Roadmap §8](DEPLOYMENT_ROADMAP.md#8-fleet-level-monitoring-vision) | Per-engine isolation, durable history and declared fleet load test |
| R-A01 | Argue modular Digital Twin Core Framework | Partial: engine class, model artifacts, UI components, common payload and shared reporting | [Architecture §§1–3](MODULAR_ARCHITECTURE.md#1-the-claim-we-can-defend) | Shared inference, versioned interfaces and replacement tests |
| R-A02 | Explain swappability | Proposed formal interfaces over existing component boundaries | [Architecture §5](MODULAR_ARCHITECTURE.md#5-how-components-can-be-swapped) | Equivalent-fixture adapter swap and compatible model replacement |
| R-A03 | Explain scalability | Current process globals are a single session | [Architecture §6](MODULAR_ARCHITECTURE.md#6-scaling-argument-and-deployment-shape) | Isolated per-engine state, backpressure and worker-recovery tests |
| R-I01 | Acknowledge injection timing | Future scope; not in inspected input/model contract | [Roadmap §9](DEPLOYMENT_ROADMAP.md#9-injection-timing-parameters--acknowledged-future-scope) | Vendor timing fields, units/reference, capture and validation before retraining |

## 3. Baseline evidence register

| Evidence | Verified by this review | Limits |
|---|---|---|
| [`app.py`](../app.py) | CSV replay, residual features, three predictors, `/ws`, process-global state | No fleet identity; confirmation changes display altitude; computation depends on connected clients |
| [`app_live.py`](../app_live.py) | MAVLink flight context, `LiveEngine` call, same payload structure | Actual engine channels remain synthetic; shared globals and duplicated inference |
| [`live_engine.py`](../live_engine.py) | Stateful healthy temperatures and simulated valve/cooling/oil-pressure faults | Hardcoded engine parameters, actual copied from expected then perturbed; no injection timing |
| [`train_model.py`](../train_model.py) and [`model_report.json`](../model_report.json) | 25 features; four class labels; chronological split cutoff 485 s | Same underlying flight trajectory; no independent-engine/general mission claim |
| [`missionAnalytics.js`](../frontend/src/lib/missionAnalytics.js) | Trend indices, persistence for maintenance advice, mission summaries/exports | Browser execution; three-sample confirmation is report/advisory logic, not a universal backend classification gate |
| [`SectionF.jsx`](../frontend/src/components/SectionF.jsx) | React report/advisory integration | Does not establish centrally persisted fleet reports |
| [`sync_dashboard_analytics.py`](../scripts/sync_dashboard_analytics.py) | Synchronization of analytics into static dashboard | Copies must be checked for drift during release |
| [`LandingPage.jsx`](../frontend/src/pages/LandingPage.jsx) | Remote Spline scene references | Offline React landing experience needs local assets or fallback |
| [`requirements.txt`](../requirements.txt) | Runtime dependency list | Relevant imports absent; versions unpinned; installation remains a deployment gate |

Source inspection was used; this task did not install dependencies, load model binaries, run the application, conduct hardware tests or benchmark capacity. Existing report metrics are historical artifact contents and were not re-evaluated here.

## 4. Documentation corrections to track

Existing README/project descriptions contain claims that need reconciliation before final submission. This task adds qualified architecture documents rather than rewriting the full project narrative.

| Existing narrative / ambiguity | Source-based clarification | Follow-up |
|---|---|---|
| Four fault modes including ignition | Current class vocabulary is healthy, valve wear, cooling failure and oil pressure drop | Distinguish described simulation possibilities from implemented/trained classes |
| Generator scripts listed as repository files | `engine_physics_model.py`, `merge_mission_profile.py`, `combine_datasets.py` are not present at the reviewed root | Obtain originals or label them external historical generation tools |
| Severity/RUL R² described as percent accuracy | R² is a regression fit statistic, not percentage accuracy | Report metric names and evaluation scope precisely |
| RUL displayed as seconds | `DiagnosisPanel.jsx` multiplies a fraction by 160 | Document/validate the horizon before claiming measured time to failure |
| Perfect classification implies reliability | Historical same-profile chronological evaluation is narrow | Add independent missions/engines and uncertainty before operational claims |
| Confirm action / simulated descent | Backends alter displayed altitude; no actual flight-command evidence | Separate visualization from measured altitude and operator action in deployment |
| “Offline” without qualification | Core replay is local; React landing scenes require external resources | Verify both supported UI paths with networking disabled |
| Multiple clients implies fleet support | Clients share backend globals | Demonstrate independent engine sessions and report persistence |

Do not restore claims from the archived local prototype by citation or assumption. Its classifier, metrics, RUL intervals and fallback architecture are outside this baseline.

## 5. Proposed work packages

| Priority | Work package | Proposed owner role | Completion evidence |
|---|---|---|---|
| P0 | Reconcile current claims and artifact provenance | Documentation + ML | Feature/class/version table and reviewed project narrative |
| P0 | Reproduce clean ground installation | Backend/deployment | Resolved environment, model compatibility, offline startup record |
| P1 | Extract common predictor and source contract | Backend/architecture | Replay/live equivalence fixtures and explicit failure cases |
| P1 | Separate source/engine/mission state | Backend | Two interleaved engines match isolated runs |
| P1 | Persistent reports and real acknowledgement | Backend + frontend | Same history across clients, restart recovery, unmodified measured telemetry |
| P1 | Authentication and secure remote transport | Backend/security | Unauthorized/replayed/tampered traffic tests |
| P2 | Synthetic ECU/SocketCAN adapter harness | Hardware/backend | Decoder fixtures and quality/timestamp checks |
| P2 | Actual ECU bench integration | Hardware + engine reviewer | Vendor mapping and approved reference measurements |
| P2 | Edge qualification | Deployment/platform | Power/thermal/resource/latency/rollback records |
| P3 | Fleet pilot and maintenance feedback | Backend + ML/domain | Capacity and isolation report, reviewed maintenance labels |
| Future | Injection timing | Hardware + ML | Field semantics, measured data and incremental model validation |

Priorities are a proposed sequence, not assignments of implementation work already authorized or a promised finals schedule. Dependencies and acceptance gates control progression.

## 6. Evidence checklist for a judge rehearsal

1. Identify the exact commit, artifact versions and source mode being demonstrated.
2. Walk through one sample from source to expected sensors, residuals, model output and mission report.
3. Show the component boundaries in code; disclose duplicated inference and shared-state limitations.
4. Present a planned adapter swap and its fixture test, or a recorded passing test once implemented.
5. Explain the difference between local monitoring, onboard monitoring and fleet aggregation.
6. Distinguish encrypted transport, message authentication and measurement validity.
7. Identify injection timing as future scope and explain angle versus duration.
8. Present measured runtime/model results only with their input data, hardware, procedure and limitations.

## 7. Reusable acceptance record

For every future implementation gate, record:

```text
Requirement / gate ID:
Source commit and model/configuration versions:
Hardware, ECU firmware and environment:
Input fixture or capture identity:
Procedure and operating conditions:
Expected outcome / target:
Observed outcome / measured values:
Evidence file(s):
Pass / fail / blocked and reason:
Reviewer and date:
```

A documented design, a passing simulation test and a validated physical integration are different levels of evidence. Keep that distinction visible as the finals build evolves.
