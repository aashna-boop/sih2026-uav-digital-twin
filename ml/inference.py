from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
import xgboost as xgb

from core.contracts import FlightState


SENSORS = (
    "rpm",
    "cht_c",
    "egt_c",
    "oil_temp_c",
    "oil_pressure_bar",
    "fuel_flow_lph",
    "vibration",
)


@dataclass(slots=True)
class InferenceResult:
    fault: str = "HEALTHY"
    confidence: float = 0.5
    rul_low: float | None = None
    rul_median: float | None = None
    rul_high: float | None = None
    contributors: list[dict[str, float | str]] | None = None


@lru_cache(maxsize=2)
def _load_bundle(model_path: str):
    return joblib.load(model_path)


class ModelInference:
    """One-hertz rolling feature and inference layer for the trained bundle."""

    def __init__(self, model_path: Path) -> None:
        bundle = _load_bundle(str(model_path.resolve()))
        if int(bundle.get("schema_version", 1)) < 2:
            raise ValueError("Model bundle predates the portable XGBoost schema; retrain models.")
        classifier_path = model_path.parent / str(bundle["classifier_file"])
        self.classifier = xgb.XGBClassifier()
        self.classifier.load_model(classifier_path)
        self.labels: list[str] = list(bundle["labels"])
        self.rul_models = bundle["rul_models"]
        self.feature_columns: list[str] = bundle["feature_columns"]
        self.interval_padding = float(bundle.get("rul_interval_padding_minutes", 0.0))
        self.persistence_frames = int(bundle.get("temporal_persistence_frames", 3))
        self.history: deque[tuple[dict[str, float], FlightState]] = deque(maxlen=10)
        self.last_sample_t: float | None = None
        self.stable_fault = "HEALTHY"
        self.candidate_fault = "HEALTHY"
        self.candidate_count = 0
        self.latest = InferenceResult()

    def reset(self) -> None:
        self.history.clear()
        self.last_sample_t = None
        self.stable_fault = "HEALTHY"
        self.candidate_fault = "HEALTHY"
        self.candidate_count = 0
        self.latest = InferenceResult()

    def update(self, normalized: dict[str, float], flight: FlightState) -> InferenceResult:
        if self.last_sample_t is not None and flight.t_sec - self.last_sample_t < 0.95:
            return self.latest
        if self.last_sample_t is not None and flight.t_sec < self.last_sample_t:
            self.reset()
        self.last_sample_t = flight.t_sec
        self.history.append((dict(normalized), flight))
        feature_row = self._feature_row(normalized, flight)
        data = pd.DataFrame([feature_row], columns=self.feature_columns).fillna(0.0)

        probabilities = self.classifier.predict_proba(data)[0]
        probability_map = dict(zip(self.labels, probabilities, strict=True))
        raw_fault = self.labels[int(np.argmax(probabilities))]
        self._update_stable_fault(raw_fault)
        confidence = float(probability_map.get(self.stable_fault, max(probabilities)))
        contributors = self._tree_shap(data, self.stable_fault)

        if self.stable_fault == "HEALTHY":
            self.latest = InferenceResult(
                fault="HEALTHY", confidence=confidence, contributors=contributors
            )
            return self.latest

        low = float(self.rul_models["low"].predict(data)[0]) - self.interval_padding
        median = float(self.rul_models["median"].predict(data)[0])
        high = float(self.rul_models["high"].predict(data)[0]) + self.interval_padding
        median = max(0.0, median)
        low = min(max(0.0, low), median)
        high = max(median, high)
        self.latest = InferenceResult(
            fault=self.stable_fault,
            confidence=confidence,
            rul_low=low,
            rul_median=median,
            rul_high=high,
            contributors=contributors,
        )
        return self.latest

    def _tree_shap(
        self, data: pd.DataFrame, predicted_fault: str
    ) -> list[dict[str, float | str]]:
        class_index = self.labels.index(predicted_fault)
        matrix = xgb.DMatrix(data, feature_names=self.feature_columns)
        values = np.asarray(
            self.classifier.get_booster().predict(
                matrix, pred_contribs=True, strict_shape=True
            )
        )
        if values.ndim == 3:
            class_values = values[0, class_index, :-1]
        elif values.ndim == 2:
            class_values = values[0, :-1]
        else:
            return []
        ranked = np.argsort(np.abs(class_values))[::-1][:5]
        return [
            {
                "sensor": self.feature_columns[index],
                "label": self._feature_label(self.feature_columns[index]),
                "z_score": round(float(data.iloc[0, index]), 3),
                "shap_value": round(float(class_values[index]), 5),
                "magnitude": round(float(abs(class_values[index])), 5),
                "kind": "tree_shap",
            }
            for index in ranked
        ]

    @staticmethod
    def _feature_label(feature: str) -> str:
        label = feature
        if label.startswith("z_"):
            label = label[2:]
        label = label.replace("_mean10", " rolling mean")
        label = label.replace("_std10", " variability")
        label = label.replace("_slope5", " trend")
        label = label.replace("phase_", "flight phase: ")
        return label.replace("_", " ")

    def _update_stable_fault(self, raw_fault: str) -> None:
        if raw_fault == self.stable_fault:
            self.candidate_fault = self.stable_fault
            self.candidate_count = 0
            return
        if raw_fault == self.candidate_fault:
            self.candidate_count += 1
        else:
            self.candidate_fault = raw_fault
            self.candidate_count = 1
        if self.candidate_count >= self.persistence_frames:
            self.stable_fault = self.candidate_fault
            self.candidate_count = 0

    def _feature_row(self, normalized: dict[str, float], flight: FlightState) -> dict[str, float]:
        row = {column: 0.0 for column in self.feature_columns}
        for sensor in SENSORS:
            source = f"z_{sensor}"
            values = [sample[0][sensor] for sample in self.history]
            row[source] = float(normalized[sensor])
            row[f"{source}_mean10"] = float(np.mean(values))
            row[f"{source}_std10"] = float(np.std(values, ddof=1)) if len(values) > 1 else 0.0
            row[f"{source}_slope5"] = (
                float((values[-1] - values[-6]) / 5.0) if len(values) >= 6 else 0.0
            )
        row.update(
            {
                "altitude_m": flight.altitude_m,
                "airspeed_mps": flight.airspeed_mps,
                "vertical_speed_mps": flight.vertical_speed_mps,
                "throttle_pct": flight.throttle_pct,
                "ambient_temp_c": flight.ambient_temp_c,
                f"phase_{flight.flight_phase}": 1.0,
            }
        )
        return row
