from __future__ import annotations

import math


WEIGHTS = {
    "rpm": 0.10,
    "cht_c": 0.20,
    "egt_c": 0.10,
    "oil_temp_c": 0.13,
    "oil_pressure_bar": 0.23,
    "fuel_flow_lph": 0.08,
    "vibration": 0.16,
}


DISPLAY_NAMES = {
    "rpm": "RPM",
    "cht_c": "cylinder-head temperature",
    "egt_c": "exhaust-gas temperature",
    "oil_temp_c": "oil temperature",
    "oil_pressure_bar": "oil pressure",
    "fuel_flow_lph": "fuel flow",
    "vibration": "vibration",
}


def health_score(normalized: dict[str, float]) -> float:
    gated_risk = sum(
        WEIGHTS[name] * max(0.0, abs(value) - 1.25) ** 1.35 for name, value in normalized.items()
    )
    return max(0.0, min(100.0, 100.0 * math.exp(-0.23 * gated_risk)))


def classify_fault(normalized: dict[str, float]) -> tuple[str, float, list[dict[str, float | str]]]:
    z = normalized
    evidence = {
        "COOLING_DEGRADATION": (
            0.55 * max(0.0, z["cht_c"])
            + 0.20 * max(0.0, z["egt_c"])
            + 0.25 * max(0.0, z["oil_temp_c"])
        ),
        "LUBRICATION_DEGRADATION": (
            0.55 * max(0.0, -z["oil_pressure_bar"])
            + 0.25 * max(0.0, z["oil_temp_c"])
            + 0.20 * max(0.0, z["vibration"])
        ),
        "IGNITION_MISFIRE": (
            0.30 * abs(z["rpm"])
            + 0.20 * abs(z["egt_c"])
            + 0.35 * max(0.0, z["vibration"])
            + 0.15 * max(0.0, z["fuel_flow_lph"])
        ),
        "VALVE_WEAR": (
            0.45 * max(0.0, -z["rpm"])
            + 0.15 * max(0.0, z["egt_c"])
            + 0.15 * max(0.0, z["vibration"])
            + 0.20 * max(0.0, z["fuel_flow_lph"])
            + 0.05 * max(0.0, -z["oil_pressure_bar"])
        ),
    }
    fault, score = max(evidence.items(), key=lambda item: item[1])
    if score < 1.35:
        fault = "HEALTHY"
        confidence = max(0.55, min(0.98, 1.0 - score / 3.0))
    else:
        confidence = min(0.99, 0.50 + 0.12 * score)

    contributors = sorted(
        (
            {
                "sensor": name,
                "label": DISPLAY_NAMES[name],
                "z_score": round(value, 2),
                "magnitude": round(abs(value), 2),
            }
            for name, value in z.items()
        ),
        key=lambda item: float(item["magnitude"]),
        reverse=True,
    )[:3]
    return fault, confidence, contributors


def explain_fault(fault: str, contributors: list[dict[str, float | str]]) -> list[str]:
    if fault == "HEALTHY":
        return ["Observed engine behaviour remains within the calibrated healthy envelope."]
    names = ", ".join(str(item["label"]) for item in contributors[:3])
    messages = {
        "COOLING_DEGRADATION": "Thermal residuals are increasing beyond their context-adjusted expectations.",
        "LUBRICATION_DEGRADATION": "Oil pressure is below expectation while oil temperature and vibration evidence are rising.",
        "IGNITION_MISFIRE": "Combustion instability is indicated by RPM/EGT disturbance and elevated vibration.",
        "VALVE_WEAR": "Reduced breathing/compression efficiency is indicated by falling RPM with rising EGT, fuel demand and vibration.",
    }
    method = (
        "Highest exact TreeSHAP contributions"
        if contributors and contributors[0].get("kind") == "tree_shap"
        else "Largest normalized contributors"
    )
    return [messages[fault], f"{method}: {names}."]
