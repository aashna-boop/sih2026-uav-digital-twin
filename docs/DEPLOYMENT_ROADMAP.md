# Standalone deployment roadmap

**Owner:** Rijul — Documentation & Architecture  
**Audience:** SIH finals judges and engineering team  
**Baseline:** `main`, commit `11986638fd5f56beb4b1e38cacdbeb388704dc85`  
**Reviewed:** 19 September 2026  
**Status:** Proposed deployment design; implementation evidence is identified separately.

## 1. Deployment objective and scope

The target is an engine-health monitoring system that runs independently of a development laptop, continues local analysis during communication loss, and supplies authenticated telemetry and maintenance evidence to ground operators and eventually a fleet service.

The first deployment milestone is a reproducible ground installation. Subsequent milestones introduce vendor-specific ECU data through a vendor-neutral adapter contract, an onboard companion computer, and fleet aggregation. Completion depends on acceptance evidence, not calendar dates: no finals deadline, engine vendor, ECU protocol or onboard hardware has been selected.

This document addresses deployment of a health-monitoring and decision-support system. Engine control remains with the ECU/FADEC and flight controller. Physical engine integration is a future engineering stage, not a capability established by the current simulation.

Related deliverables: [modular architecture](MODULAR_ARCHITECTURE.md) and [requirements traceability](FINALS_REQUIREMENTS_TRACEABILITY.md).

## 2. Starting point: what exists today

| Area | Evidence in the current repository | Deployment implication |
|---|---|---|
| Replay | `app.py` loads CSV runs and broadcasts predictions over `/ws` | Useful deterministic input for packaging and regression checks |
| Live flight context | `app_live.py` consumes MAVLink attitude and VFR data | An initial flight adapter exists; engine measurements are still simulated |
| Engine model | `live_engine.py:LiveEngine` returns actual/expected sensor dictionaries | Engine-model boundary exists, but real sensor acquisition and healthy prediction need separate contracts |
| Prediction | Three XGBoost joblib artifacts; duplicated inference functions in both backends | Consolidate loading, ordered features and label mappings before deployment |
| Operator interface | React dashboard and `static/index.html` | Select one release build and document compatibility for the second interface |
| Maintenance/reporting | `frontend/src/lib/missionAnalytics.js`, `SectionF.jsx` | Browser-side analytics and exports exist; persistent fleet storage does not |
| Timing | Backend loop sleeps 0.4 s; replay advances 50 CSV rows per tick | Wall time and simulated mission time differ; this is not a measured real-time deadline |
| Security | Plain WebSocket, shared global state, unrestricted CORS in replay app | Authentication, encrypted transport and asset isolation remain proposed |

The previous local AegisTwin checkout is not this baseline. Its five-class results, profile-separated metrics, calibrated RUL intervals, automatic source failover and launch scripts must not be attributed to this repository.

## 3. Deployment stages and acceptance gates

All thresholds below are **proposed engineering acceptance targets**, not measured results or manufacturer specifications. Record the selected hardware, data rate and test procedure alongside each result.

| Stage | Engineering deliverable | Exit evidence | Dependency |
|---|---|---|---|
| D0 — Reproducible baseline | Lock dependencies, model manifest, one supported launch procedure, offline dashboard build | Fresh-machine installation; artifact hashes; model/feature compatibility check; replay result comparison | Current source and artifacts |
| D1 — Standalone ground package | Versioned local service, configuration, durable event/report storage, health/status interface | Cold start, offline operation, restart recovery, two-hour replay soak, bounded storage and memory | D0 |
| D2 — Hardware-neutral interfaces | Typed telemetry contract; replay, synthetic ECU and flight-context adapters; shared inference | Contract fixtures, malformed/stale/missing-signal tests, per-engine isolation | D0; can overlap D1 |
| D3 — ECU/CAN bench integration | Vendor signal map, isolated acquisition gateway, reference capture and decoding | Vendor-reference comparison, timing/error measurements, acquisition interference assessment | D2 plus vendor documentation and hardware |
| D4 — Onboard shadow operation | Companion-computer service, local recording, bounded queues, link-loss behavior | Hardware resource profile, thermal/power tests, recovery tests; no control dependence on analytics | D3 and platform review |
| D5 — Secure ground link | Device identity, encrypted connection, operator roles and audit trail | Unauthorized-client rejection, tamper/replay tests, revocation and outage recovery | D1/D2; required before remote operational use |
| D6 — Fleet pilot | Asset registry, per-engine history, ingestion and model-release service | Mixed-asset isolation, deduplication, load/outage tests, staged rollout/rollback | D4/D5 and maintenance data agreement |

No milestone implies approval for operational flight. Bench, hardware-in-the-loop and any later flight evaluation require the platform owner's review and a defined test envelope.

## 4. Standalone ground installation

Package a supported Python runtime, resolved dependencies, model files, configuration, the local UI build and a launch/service definition. A native service is the initial default; container packaging is an alternative if it simplifies the chosen deployment environment. Neither packaging method alone demonstrates onboard suitability.

Before packaging:

- Resolve runtime/training dependencies separately: `pymavlink`, `scikit-learn` and `shap` are imported by relevant source paths but are absent from the current requirements list.
- Record model format, library versions, SHA-256 hashes, ordered features, class labels, training-data identity and operating envelope in a manifest. Load only trusted, verified artifacts; joblib loading is not a safe boundary for arbitrary uploads.
- Resolve paths from an installation/configuration root rather than the shell's current directory.
- Build local dashboard assets. The React landing page currently fetches Spline scenes remotely; fonts and other remote resources must be bundled where permitted or given an offline fallback. Test with the network disabled.
- Define replay/live mode, endpoint, engine profile, storage limits and logging through configuration.
- Preserve the distinction between simulator commands and production operator acknowledgement. Current confirmation modifies displayed altitude; a deployed monitor must retain measured altitude and record acknowledgement separately.
- Make diagnosis and event recording independent of whether a browser is connected. Both current loops condition processing on viewer presence.

Suggested D1 tests: install on a clean supported machine, start with no internet, replay each supported fault, disconnect all browsers, reconnect, restart the service and recover reports. Propose a two-hour soak with no unbounded queue growth and under 10% steady-state memory growth after warm-up. These are initial acceptance criteria to refine through measurement.

## 5. Vendor-neutral CAN bus / FADEC integration

### 5.1 What vendor-neutral means

The analytics contract is stable; each ECU adapter is qualified for its actual protocol. CAN supplies a transport, not a universal engine-signal dictionary. Do not assume an arbitrary FADEC uses J1939, DroneCAN, a standard DBC, or publishes all required measurements.

Obtain an interface-control document or equivalent signal specification containing bus type, bitrate, identifier format, frame layout, scaling, endianness, units, validity codes, counters/checksums, update rates and diagnostic access rules. Record engine/ECU firmware versions with the mapping. FADEC means full-authority digital engine control; the monitoring application is not itself a FADEC.

### 5.2 Candidate acquisition routes

| Route | Select when | What must be demonstrated |
|---|---|---|
| ECU → isolated CAN interface → companion computer | Vendor permits access and supplies decoding information | Electrical compatibility, message decoding, timestamps, supported signals, effects on bus loading |
| ECU → supported autopilot EFI driver → MAVLink → companion computer | The exact ECU/firmware is supported and signal coverage is adequate | Driver configuration, emitted message fields, rates, validity and conversion accuracy |
| ECU vendor gateway → documented serial/Ethernet API → adapter | Raw CAN access is unavailable or a gateway is the supported interface | API availability, timing behavior, error handling and gateway version compatibility |

Linux SocketCAN is a candidate host interface for CAN acquisition and virtual-CAN bench fixtures. It supplies CAN access, not an ECU decoder. See the [Linux SocketCAN documentation](https://docs.kernel.org/networking/can.html).

ArduPilot documents supported EFI integrations, but that list does not establish compatibility with an unspecified FADEC. Verify the selected hardware against the [ArduPilot EFI documentation](https://ardupilot.org/plane/docs/common-efi.html).

### 5.3 Acquisition boundary and qualification

Start with vendor-approved observation-only acquisition. Specify electrical isolation, wiring/termination assessment and true listen-only capability where required; omitting transmit calls in Python alone does not establish electrically passive operation. Where a vendor interface requires diagnostic requests, qualify their rates and effects separately.

Keep the engine-control bus separated from ground/fleet networks by the acquisition gateway. The proposed monitor has no general path for ground clients to transmit arbitrary CAN frames. Hardware fault behavior and gateway failure must not interrupt engine control.

Qualification sequence:

1. Replay vendor-provided or captured bench frames through a decoder fixture.
2. Compare each signal against a trusted vendor instrument/log and declared tolerance.
3. Check units, timestamp alignment, missing frames, stale values, invalid codes and bus errors.
4. Run hardware-in-the-loop and representative load changes; capture raw frames plus normalized outputs.
5. Calibrate the healthy reference against measured engine data before applying simulation-trained decisions as operational evidence.

If the vendor mapping is unavailable, complete only the synthetic adapter/contract demonstration. Mark physical integration as blocked on the interface documentation.

### 5.4 Signal mapping rules

| Signal group | Proposed handling |
|---|---|
| RPM, CHT, EGT, fuel flow | Explicit units and channel/cylinder identity; preserve sensor source and validity |
| Oil pressure/temperature | Acquire only from a documented ECU message or qualified independent sensor; do not invent missing channels |
| Flight altitude, airspeed, attitude | Obtain from the flight controller and align to engine acquisition time |
| Load | Distinguish ECU-reported load from the current derived estimate; they are not interchangeable features without validation |
| Vibration | Define measurement basis, bandwidth and units; an ECU scalar may not replace an accelerometer-derived feature |
| Optional timing fields | Preserve their definition and missingness; see Section 9 |

The MAVLink [`EFI_STATUS` definition](https://mavlink.io/en/messages/common.html#EFI_STATUS) provides an engine telemetry option including RPM, temperatures, fuel flow, ignition timing and injection time. It does not guarantee that every ECU publishes every field or that the message covers our entire sensor contract.

## 6. Edge/onboard deployment

### 6.1 Placement of responsibilities

| Location | Proposed responsibilities | Link-loss behavior |
|---|---|---|
| ECU / flight controller | Existing engine/flight control and source telemetry | Control continues independently of the monitor |
| Companion computer | Acquisition, validation, time alignment, healthy prediction, inference, essential advisory/event recording | Continue from valid local sensors; mark missing sensor analysis unavailable; buffer locally |
| Ground station | Operator display, acknowledgement, investigation and report access | Show last-seen time and stale status; do not display old predictions as fresh |
| Fleet service | Cross-mission history, maintenance planning, model governance | Optional to current onboard inference; accept delayed uploads later |

Loss of the ground link and loss of a required engine sensor are different events. The first permits local analysis; the second requires a degraded or unavailable diagnosis according to the model's missing-input policy. Never silently replace a real-aircraft stream with demonstration data.

### 6.2 Hardware selection and resource budget

Begin with a CPU-only ARM64 or x86 Linux companion candidate. Select hardware after measuring runtime compatibility, CPU/RAM, thermal behavior, power draw, startup time, storage endurance and CAN interfaces. A GPU is not assumed necessary for the current tree models; any later model change reopens the resource assessment.

Keep raw acquisition rates, model feature windows, inference rate and dashboard publication rate independently configurable. Respect sensor bandwidth, particularly vibration; low-rate scalar telemetry cannot establish high-frequency fault coverage. Changing model sampling/window cadence requires feature-equivalence and prediction validation.

Initial test targets: 5 Hz normalized processing for the prototype scalar stream, p95 valid-input-to-diagnosis latency below 200 ms, explicit overruns, and bounded queues. These targets are proposed and must not be quoted as achieved. Higher-rate acquisition may require aggregation before inference.

Storage planning example: at an assumed 1 KiB per normalized sample and 5 samples/s, 24 hours consumes about 422 MiB before indexes, event records and raw CAN/vibration logs. Measure actual serialized sizes and provision an outage buffer plus reserve. Define retention, full-disk behavior and explicit data-loss events.

### 6.3 Operational behavior

Use a supervised service, watchdog, health indicators and resource limits. A restart creates a new boot identity, restores approved configuration/model versions and marks thermal/rolling-state warm-up. Use monotonic time for age checks and retain UTC acquisition time plus synchronization quality for cross-system records. Clock correction must not reverse sample order.

A signed release contains application, schema, model and engine-configuration versions. Check compatibility before activation, stage updates when grounded, retain a verified previous release, and test rollback. Training and unsupervised model replacement are outside the onboard inference process.

## 7. Secure telemetry architecture

### 7.1 Proposed trust boundaries

```text
Engine-control domain              Monitoring domain
ECU / FADEC -- approved link --> acquisition gateway --> onboard twin + recorder
                                                        |
                                             authenticated encrypted uplink
                                                        |
Ground domain                         authenticated ground ingestion + storage
                                                        |
                                     HTTPS/WSS operator UI with roles/audit
                                                        |
Fleet domain                         authorized fleet ingestion and reporting
```

This is a proposed design. The repository currently implements neither these security boundaries nor fleet ingestion.

The design follows the principle that network location alone does not establish trust. Device and operator identities receive explicit resource permissions, consistent with [NIST SP 800-207](https://csrc.nist.gov/pubs/sp/800/207/final). This reference guides design; it is not a compliance claim.

### 7.2 Controls and tests

| Boundary / risk | Proposed control | Evidence required |
|---|---|---|
| Device impersonation | Unique device credentials; mutually authenticated TLS between managed endpoints; protected key storage | Unregistered/revoked device rejected; provisioning and renewal tested |
| Uplink interception | TLS-protected application channel or approved encrypted tunnel appropriate to transport | Packet capture does not expose application telemetry; certificates verified |
| Duplicate/replayed uploads | Asset, engine, mission, boot/session identity and sequence number; bounded freshness rules | Duplicate upload is idempotent; delayed records remain historical, never current |
| Unauthorized dashboard commands | Authenticated users, viewer/operator/maintainer roles, WebSocket authorization, explicit allowed origins | Unauthorized scenario/configuration action rejected; operator actions attributed |
| Model tampering | Signed manifest and artifact hashes; trusted release channel | Altered/unapproved bundle fails activation; rollback works |
| Saturation or slow clients | Message-size/rate bounds, separate queues and prioritization of health events | Ground client failure cannot stall local acquisition/inference |
| Sensitive mission information | Minimize data, restrict access, encrypt storage where required, documented retention | Permission and backup/restore checks; retention actions recorded |
| Loss of connectivity | Bounded store-and-forward and last-seen display | Local inference continues, buffered uploads reconcile without duplicate alarms |

MAVLink 2 signing authenticates messages and supports replay resistance; it does not encrypt the payload. Where MAVLink crosses an untrusted link, confidentiality requires an additional protected transport. See [MAVLink message signing](https://mavlink.io/en/guide/message_signing.html). Signature verification is distinct from sensor correctness and device authorization.

Allow vendor-supported acquisition requests without exposing a generic engine-control tunnel. Keep simulation fault injection unavailable in a real-aircraft deployment profile. Operator acknowledgement records an advisory response; it does not confer engine or flight actuation authority.

## 8. Fleet-level monitoring vision

Start with one engine and durable ground storage. Add a second engine to prove state isolation before increasing fleet load. Proposed fleet entities include aircraft, removable engine, ECU, sensor calibration, mission, maintenance event, model release and operator action. Engine identity must persist when an engine changes aircraft.

Partition state and storage by asset/engine/mission. Each engine has independent thermal state, rolling windows, fault persistence and report state. Ingestion validates schema and identity, deduplicates by session/sequence, and preserves acquisition/arrival times. Raw evidence belongs in bounded archival storage; searchable summaries and maintenance history belong in indexed storage.

Fleet views should show last contact, data quality, current diagnosis, unresolved advisories, engine hours and post-maintenance changes. Compare engines only within compatible configurations and model versions. A fleet risk ranking is a planning aid, not proof of a transferable RUL model.

Progressive tests: 2 engines for state isolation, then 10 and 100 synthetic publishers at a declared rate. At an illustrative 100 engines × 5 Hz × 1 KiB, payload traffic is about 500 KiB/s before protocol overhead. Measure ingestion latency, backlog recovery, storage growth, failure isolation and operator query time before publishing a supported fleet capacity.

Maintain a model registry with validation datasets and rollout history. Maintenance outcomes may support later retraining, but require reviewed labels, independent missions and engine-separated evaluation. Fleet connectivity must not become a prerequisite for immediate onboard diagnosis.

## 9. Injection timing parameters — acknowledged future scope

**Status: not implemented in the reviewed source, training features or telemetry payload.** Fault injection buttons simulate degradation; they do not provide fuel-injection timing telemetry or control.

| Parameter | Meaning | Proposed representation |
|---|---|---|
| Injection start angle | Start of fuel injection relative to an engine-cycle reference | Crank degrees with explicit BTDC/ATDC convention, cycle reference, cylinder and event identity |
| Injector pulse duration | Time for which an injection event is commanded | Milliseconds; distinguish commanded from measured duration |
| Ignition timing | Spark event angle | Separate crank-angle field, never relabelled as injection angle |

In MAVLink `EFI_STATUS`, `injection_time` uses milliseconds, while `ignition_timing` uses crank-angle degrees. The message does not define a fuel-injection start-angle field. An angular injection parameter therefore needs a documented ECU mapping and, if transported through MAVLink, an appropriate supported extension/dialect or separate application message. See the [official field definitions](https://mavlink.io/en/messages/common.html#EFI_STATUS).

Future work requires: ECU field availability and semantics; timestamps synchronized to the relevant engine events; calibration across load/RPM; optional-field validity rules; measured labels; revised twin equations; a versioned feature schema; retraining and independent validation. An unavailable parameter is null/unsupported, never zero or inferred from fuel flow without a validated model.

Acceptance evidence must distinguish parameter transport from useful diagnosis: first verify decoded timing against ECU reference measurements, then show whether timing features improve held-out fault detection or uncertainty. Existing models remain on their original feature contract until that evidence exists. Closed-loop injection optimization is outside this roadmap's monitoring scope.

## 10. Decisions and evidence to collect

| Decision | Owner role | Required before |
|---|---|---|
| Engine/ECU and access to interface documents | Hardware integration lead | D3 |
| Canonical schema, state boundaries and compatibility rules | Architecture + backend | D2 |
| Supported ground OS and offline asset policy | Deployment + frontend | D1 |
| Model feature provenance, RUL units and operating envelope | ML lead | D0/D3 |
| Onboard compute, power/thermal limits and test envelope | Platform integration lead | D4 |
| Device identity, link protection and key lifecycle | Security/backend lead | D5 |
| Fleet data retention, engine identity and maintenance workflow | Fleet/backend + domain reviewer | D6 |

Rijul owns the documentation and evidence register. Engineering ownership above is proposed by role; assigning a person does not imply that the subsystem has been built.

For each completed gate, retain configuration, software/model hashes, input capture, expected outcome, observed result and reviewer. Only verified results should replace the proposed acceptance targets in this document.
