from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
import sklearn
import xgboost as xgb
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.metrics import (
    classification_report,
    confusion_matrix,
    mean_absolute_error,
)

from simulator.flight_profile import HELDOUT_DEMO_PROFILE_IDS


SENSORS = (
    "rpm",
    "cht_c",
    "egt_c",
    "oil_temp_c",
    "oil_pressure_bar",
    "fuel_flow_lph",
    "vibration",
)
TEMPORAL_PERSISTENCE_FRAMES = 4


def engineer_features(frame: pd.DataFrame) -> tuple[pd.DataFrame, list[str]]:
    frame = frame.sort_values(["run_id", "t_sec"]).copy()
    groups = frame.groupby("run_id", sort=False)
    feature_columns = [f"z_{sensor}" for sensor in SENSORS]
    for sensor in SENSORS:
        source = f"z_{sensor}"
        frame[f"{source}_mean10"] = groups[source].transform(
            lambda values: values.rolling(10, min_periods=1).mean()
        )
        frame[f"{source}_std10"] = groups[source].transform(
            lambda values: values.rolling(10, min_periods=2).std()
        ).fillna(0.0)
        frame[f"{source}_slope5"] = groups[source].diff(5).fillna(0.0) / 5.0
        feature_columns.extend(
            [f"{source}_mean10", f"{source}_std10", f"{source}_slope5"]
        )

    context = [
        "altitude_m",
        "airspeed_mps",
        "vertical_speed_mps",
        "throttle_pct",
        "ambient_temp_c",
    ]
    feature_columns.extend(context)
    phases = pd.get_dummies(frame["flight_phase"], prefix="phase", dtype=float)
    frame = pd.concat([frame, phases], axis=1)
    phase_columns = sorted(phases.columns)
    feature_columns.extend(phase_columns)
    frame[feature_columns] = frame[feature_columns].replace([np.inf, -np.inf], np.nan).fillna(0.0)
    return frame, feature_columns


def profile_split(profile_ids: list[str]) -> tuple[set[str], set[str], set[str]]:
    ids = sorted(profile_id for profile_id in profile_ids if profile_id not in HELDOUT_DEMO_PROFILE_IDS)
    rng = np.random.default_rng(26054)
    rng.shuffle(ids)
    train_end = max(1, int(len(ids) * 0.70))
    validation_end = max(train_end + 1, int(len(ids) * 0.85))
    return set(ids[:train_end]), set(ids[train_end:validation_end]), set(ids[validation_end:])


def false_alarm_events_per_hour(data: pd.DataFrame, predictions: np.ndarray) -> float:
    subset = data[["run_id", "t_sec", "fault_type"]].copy()
    subset["prediction"] = predictions
    healthy = subset[subset["fault_type"] == "HEALTHY"].copy()
    if healthy.empty:
        return 0.0
    healthy["false"] = healthy["prediction"] != "HEALTHY"
    events = 0
    for _, run in healthy.groupby("run_id"):
        starts = run["false"] & ~run["false"].shift(1, fill_value=False)
        events += int(starts.sum())
    hours = max(healthy["t_sec"].count() / 3600.0, 1e-9)
    return events / hours


def apply_temporal_persistence(data: pd.DataFrame, predictions: np.ndarray, frames: int = 3) -> np.ndarray:
    """Require repeated evidence before changing the stable diagnostic state."""
    work = data[["run_id", "t_sec"]].copy()
    work["prediction"] = predictions
    stable_output = pd.Series(index=work.index, dtype="object")
    for _, run in work.groupby("run_id", sort=False):
        stable = "HEALTHY"
        candidate = stable
        count = 0
        values: list[str] = []
        for prediction in run["prediction"].astype(str):
            if prediction == stable:
                candidate = stable
                count = 0
            elif prediction == candidate:
                count += 1
            else:
                candidate = prediction
                count = 1
            if count >= frames:
                stable = candidate
                count = 0
            values.append(stable)
        stable_output.loc[run.index] = values
    return stable_output.loc[data.index].to_numpy()


def detection_lead_times(data: pd.DataFrame, predictions: np.ndarray) -> list[float]:
    work = data.copy()
    work["prediction"] = predictions
    lead_times: list[float] = []
    for _, run in work.groupby("run_id"):
        scenario = str(run["fault_scenario"].iloc[0])
        if scenario == "HEALTHY":
            continue
        active = run[run["fault_severity"] >= 0.04]
        correct = active[active["prediction"] == scenario]
        if correct.empty:
            continue
        detection_time = float(correct["t_sec"].iloc[0])
        threshold_mask = (
            (run["actual_cht_c"] >= 260)
            | (run["actual_oil_temp_c"] >= 145)
            | (run["actual_oil_pressure_bar"] <= 1.8)
            | (run["actual_vibration"] >= 3.2)
        )
        threshold = run[threshold_mask]
        if not threshold.empty:
            lead_times.append(float(threshold["t_sec"].iloc[0]) - detection_time)
    return lead_times


def train(dataset: Path, output_dir: Path) -> dict:
    output_dir.mkdir(parents=True, exist_ok=True)
    raw = pd.read_csv(dataset)
    data, feature_columns = engineer_features(raw)
    train_ids, validation_ids, test_ids = profile_split(data["profile_id"].unique().tolist())

    train_data = data[data["profile_id"].isin(train_ids)]
    validation_data = data[data["profile_id"].isin(validation_ids)]
    test_data = data[data["profile_id"].isin(test_ids)]

    labels = sorted(data["fault_type"].unique().tolist())
    label_to_index = {label: index for index, label in enumerate(labels)}
    train_targets = train_data["fault_type"].map(label_to_index).astype(int)
    counts = train_data["fault_type"].value_counts()
    sample_weights = train_data["fault_type"].map(
        lambda label: len(train_data) / (len(labels) * counts[label])
    )
    classifier = xgb.XGBClassifier(
        n_estimators=320,
        max_depth=6,
        learning_rate=0.06,
        subsample=0.86,
        colsample_bytree=0.86,
        min_child_weight=2.0,
        reg_lambda=1.2,
        objective="multi:softprob",
        num_class=len(labels),
        eval_metric="mlogloss",
        tree_method="hist",
        random_state=26054,
        n_jobs=-1,
    )
    classifier.fit(
        train_data[feature_columns], train_targets, sample_weight=sample_weights
    )
    raw_prediction_ids = classifier.predict(test_data[feature_columns]).astype(int)
    raw_predictions = np.asarray([labels[index] for index in raw_prediction_ids])
    predictions = apply_temporal_persistence(
        test_data, raw_predictions, frames=TEMPORAL_PERSISTENCE_FRAMES
    )
    report = classification_report(
        test_data["fault_type"], predictions, labels=labels, output_dict=True, zero_division=0
    )
    matrix = confusion_matrix(test_data["fault_type"], predictions, labels=labels).tolist()

    fault_train = train_data[
        (train_data["fault_type"] != "HEALTHY") & (train_data["fault_severity"] < 0.999)
    ]
    fault_test = test_data[
        (test_data["fault_type"] != "HEALTHY") & (test_data["fault_severity"] < 0.999)
    ]
    quantiles = {}
    for name, quantile in (("low", 0.10), ("median", 0.50), ("high", 0.90)):
        model = HistGradientBoostingRegressor(
            loss="quantile",
            quantile=quantile,
            max_iter=180,
            max_leaf_nodes=24,
            learning_rate=0.07,
            random_state=26054,
        )
        model.fit(fault_train[feature_columns], fault_train["rul_minutes"])
        quantiles[name] = model

    fault_validation = validation_data[
        (validation_data["fault_type"] != "HEALTHY")
        & (validation_data["fault_severity"] < 0.999)
    ]
    validation_low = quantiles["low"].predict(fault_validation[feature_columns])
    validation_high = quantiles["high"].predict(fault_validation[feature_columns])
    validation_actual = fault_validation["rul_minutes"].to_numpy()
    nonconformity = np.maximum.reduce(
        [validation_low - validation_actual, validation_actual - validation_high, np.zeros_like(validation_actual)]
    )
    calibration_padding = float(np.quantile(nonconformity, 0.90, method="higher"))

    rul_low = quantiles["low"].predict(fault_test[feature_columns])
    rul_median = quantiles["median"].predict(fault_test[feature_columns])
    rul_high = quantiles["high"].predict(fault_test[feature_columns])
    rul_low = np.maximum(0.0, rul_low - calibration_padding)
    rul_high = rul_high + calibration_padding
    actual_rul = fault_test["rul_minutes"].to_numpy()
    coverage = float(np.mean((actual_rul >= rul_low) & (actual_rul <= rul_high)))
    leads = detection_lead_times(test_data, predictions)

    metrics = {
        "synthetic_simulation_based": True,
        "dataset_rows": int(len(data)),
        "model_schema_version": 2,
        "models": {
            "classifier": "XGBoost multi-class classifier",
            "classifier_format": "portable UBJ",
            "explainability": "native exact TreeSHAP pred_contribs",
            "rul": "calibrated scikit-learn quantile gradient boosting",
            "xgboost_version": xgb.__version__,
            "scikit_learn_version": sklearn.__version__,
        },
        "profiles": {
            "train": sorted(train_ids),
            "validation": sorted(validation_ids),
            "test": sorted(test_ids),
            "heldout_demo": sorted(HELDOUT_DEMO_PROFILE_IDS),
        },
        "classification": {
            "labels": labels,
            "macro_f1": float(report["macro avg"]["f1-score"]),
            "temporal_persistence_frames": TEMPORAL_PERSISTENCE_FRAMES,
            "report": report,
            "confusion_matrix": matrix,
            "false_alarm_events_per_healthy_hour": float(
                false_alarm_events_per_hour(test_data, predictions)
            ),
        },
        "early_warning": {
            "mean_lead_time_sec": float(np.mean(leads)) if leads else None,
            "median_lead_time_sec": float(np.median(leads)) if leads else None,
            "evaluated_runs": len(leads),
        },
        "rul": {
            "mae_minutes": float(mean_absolute_error(actual_rul, rul_median)),
            "interval_coverage": coverage,
            "validation_calibration_padding_minutes": calibration_padding,
            "test_samples": int(len(fault_test)),
        },
        "validation_rows": int(len(validation_data)),
        "test_rows": int(len(test_data)),
    }

    classifier_file = "fault_classifier.ubj"
    classifier.save_model(output_dir / classifier_file)
    joblib.dump(
        {
            "schema_version": 2,
            "classifier_file": classifier_file,
            "classifier_type": "xgboost_ubj",
            "rul_models": quantiles,
            "feature_columns": feature_columns,
            "labels": labels,
            "rul_interval_padding_minutes": calibration_padding,
            "temporal_persistence_frames": TEMPORAL_PERSISTENCE_FRAMES,
        },
        output_dir / "model_bundle.joblib",
    )
    (output_dir / "metrics.json").write_text(json.dumps(metrics, indent=2), encoding="utf-8")
    manifest = {
        "schema_version": 2,
        "classifier_file": classifier_file,
        "classifier_format": "xgboost_ubj",
        "feature_count": len(feature_columns),
        "labels": labels,
        "xgboost_version": xgb.__version__,
        "scikit_learn_version": sklearn.__version__,
        "dataset_sha256": hashlib.sha256(dataset.read_bytes()).hexdigest(),
        "dataset_rows": int(len(data)),
        "profile_split": metrics["profiles"],
    }
    (output_dir / "model_manifest.json").write_text(
        json.dumps(manifest, indent=2), encoding="utf-8"
    )
    return metrics


def main() -> None:
    parser = argparse.ArgumentParser(description="Train profile-separated SIH26054 models")
    parser.add_argument(
        "--dataset",
        type=Path,
        default=Path("data/generated/engine_trajectories.csv"),
    )
    parser.add_argument("--output-dir", type=Path, default=Path("ml/models"))
    args = parser.parse_args()
    metrics = train(args.dataset, args.output_dir)
    print(json.dumps(metrics, indent=2))


if __name__ == "__main__":
    main()
