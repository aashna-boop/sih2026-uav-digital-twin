from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager, suppress
from dataclasses import asdict
import os

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from core.runtime import SimulationRuntime
from simulator.engine_plant import DEGRADATION_RATES, FAULTS
from telemetry.mavlink_adapter import MavlinkFlightAdapter


class ScenarioRequest(BaseModel):
    fault: str | None = None
    degradation_rate: str | None = None
    enabled: bool | None = None
    paused: bool | None = None
    ambient_offset_c: float | None = Field(default=None, ge=-40, le=40)
    reset: bool = False


class OperatorResponseRequest(BaseModel):
    response: str


class TelemetryHub:
    def __init__(self, requested_mode: str = "replay") -> None:
        self.runtime = SimulationRuntime()
        self.requested_mode = requested_mode if requested_mode in {"replay", "sitl", "auto"} else "replay"
        self.active_mode = "replay"
        self.adapter = (
            MavlinkFlightAdapter(os.getenv("AEGISTWIN_MAVLINK_ENDPOINT", "udpin:0.0.0.0:14550"))
            if self.requested_mode in {"sitl", "auto"}
            else None
        )
        self.subscribers: set[asyncio.Queue[dict]] = set()
        self.task: asyncio.Task | None = None

    async def run(self) -> None:
        while True:
            flight = None
            if self.adapter is not None:
                try:
                    flight = self.adapter.poll(self.runtime.scenario.ambient_offset_c)
                except Exception as exc:
                    # A telemetry transport failure must never stop the replay
                    # fallback or the dashboard stream.
                    self.adapter.mark_error(exc)
            self.active_mode = "sitl" if flight is not None else "replay"
            frame = self.runtime.step(flight=flight).to_dict()
            for queue in tuple(self.subscribers):
                if queue.full():
                    with suppress(asyncio.QueueEmpty):
                        queue.get_nowait()
                with suppress(asyncio.QueueFull):
                    queue.put_nowait(frame)
            await asyncio.sleep(self.runtime.dt)

    def status(self) -> dict:
        return {
            "requested_mode": self.requested_mode,
            "active_mode": self.active_mode,
            "replay_fallback_active": self.requested_mode != "replay" and self.active_mode == "replay",
            "mavlink": self.adapter.status() if self.adapter is not None else None,
        }

    def subscribe(self) -> asyncio.Queue[dict]:
        queue: asyncio.Queue[dict] = asyncio.Queue(maxsize=1)
        self.subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue[dict]) -> None:
        self.subscribers.discard(queue)


hub = TelemetryHub(os.getenv("AEGISTWIN_TELEMETRY_MODE", "replay").lower())


@asynccontextmanager
async def lifespan(_: FastAPI):
    hub.task = asyncio.create_task(hub.run())
    try:
        yield
    finally:
        if hub.task:
            hub.task.cancel()
            with suppress(asyncio.CancelledError):
                await hub.task
        if hub.adapter is not None:
            hub.adapter.close()


app = FastAPI(
    title="SIH26054 Mission Reliability Digital Twin",
    version="0.1.0",
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:4173",
        "http://127.0.0.1:4173",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health")
def health() -> dict:
    return {
        "status": "ok",
        "mode": hub.active_mode,
        "telemetry": hub.status(),
        "stream_hz": round(1 / hub.runtime.dt, 1),
        "profile_id": (
            hub.runtime.last_frame.flight.profile_id
            if hub.runtime.last_frame is not None
            else hub.runtime.profile.profile_id
        ),
    }


@app.get("/api/scenarios")
def scenarios() -> dict:
    return {
        "faults": list(FAULTS),
        "degradation_rates": list(DEGRADATION_RATES),
        "current": asdict(hub.runtime.scenario),
        "operator": hub.runtime.operator_state(),
    }


@app.post("/api/scenario")
def configure_scenario(request: ScenarioRequest) -> dict:
    if request.fault is not None and request.fault not in FAULTS:
        raise HTTPException(status_code=422, detail=f"Unsupported fault: {request.fault}")
    try:
        state = hub.runtime.configure(**request.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"status": "updated", "scenario": asdict(state)}


@app.post("/api/operator-response")
def record_operator_response(request: OperatorResponseRequest) -> dict:
    try:
        state = hub.runtime.record_operator_response(request.response)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {
        "status": "recorded",
        "operator": state,
        "note": "Advisory acknowledgement only; no autonomous flight command was sent.",
    }


@app.get("/api/telemetry/latest")
def latest_telemetry() -> dict:
    if hub.runtime.last_frame is None:
        return hub.runtime.step().to_dict()
    return hub.runtime.last_frame.to_dict()


@app.websocket("/ws/telemetry")
async def telemetry_socket(websocket: WebSocket) -> None:
    await websocket.accept()
    queue = hub.subscribe()
    try:
        while True:
            frame = await queue.get()
            await websocket.send_json(frame)
    except WebSocketDisconnect:
        pass
    finally:
        hub.unsubscribe(queue)
