"""
Real ML pipeline for UAV piston engine fault detection, severity estimation,
and RUL prediction, trained on engine_master_dataset.csv.

Approach:
- The dataset contains 4 continuous flight runs (healthy, valve_wear,
  cooling_failure, oil_pressure_drop) that replay the IDENTICAL flight
  trajectory (confirmed: load/airspeed/roll/pitch match row-for-row across
  files). This lets us build genuine digital-twin residual features: for
  each timestep, compare the actual sensor reading against the healthy
  run's reading at the same point in the flight, exactly as the SIH
  proposal's architecture describes (physics/reference model -> residual
  -> ML fault detection).
- Classification target: fault_type (4-class: healthy / valve_wear /
  cooling_failure / oil_pressure_drop) via XGBoost.
- Regression target: fault_severity and rul_frac via XGBoost regressors.
- Split: chronological, not random. Train on the first 75% of each run's
  timeline, test on the final 25% (later, more-progressed fault states the
  model never saw during training). This is a materially harder and more
  honest test than a random row split, since adjacent rows are highly
  autocorrelated and a random split would leak.
- Explainability: real SHAP TreeExplainer values, not hand-assigned weights.
"""
import pandas as pd
import numpy as np
import xgboost as xgb
import shap
import joblib
import json
from sklearn.metrics import (accuracy_score, classification_report,
                              confusion_matrix, mean_absolute_error, r2_score)

RAW = pd.read_csv('engine_master_dataset.csv')
SENSORS = ['rpm', 'egt_c', 'cht_c', 'oil_temp_c', 'oil_pressure_bar', 'fuel_flow_lph', 'vibration']
FLIGHT_STATE = ['airspeed', 'load', 'roll', 'pitch']

# ---------- Build healthy baseline (the "digital twin expected value") ----------
healthy = RAW[RAW.source_file == 'engine_healthy.csv'].sort_values('t_sec').reset_index(drop=True)
baseline = healthy[SENSORS].copy()
baseline['idx'] = baseline.index

# ---------- Build residual features for every run (row-aligned, same trajectory) ----------
frames = []
for src in RAW.source_file.unique():
    run = RAW[RAW.source_file == src].sort_values('t_sec').reset_index(drop=True)
    run = run.copy()
    run['idx'] = run.index
    for s in SENSORS:
        run[f'{s}_expected'] = baseline[s].values[:len(run)]
        run[f'{s}_resid'] = run[s] - run[f'{s}_expected']
        run[f'{s}_resid_pct'] = run[f'{s}_resid'] / (run[f'{s}_expected'].abs() + 1e-6)
    frames.append(run)
full = pd.concat(frames, ignore_index=True)

FEATURES = SENSORS + FLIGHT_STATE + [f'{s}_resid' for s in SENSORS] + [f'{s}_resid_pct' for s in SENSORS]

# ---------- Chronological split (train on early flight, test on later/more-progressed) ----------
CUTOFF_T = 485.0  # ~75% of the 647s flight
train = full[full.t_sec < CUTOFF_T].reset_index(drop=True)
test = full[full.t_sec >= CUTOFF_T].reset_index(drop=True)
print(f"Train rows: {len(train)}  Test rows: {len(test)}")
print("Train fault_type counts:\n", train.fault_type.value_counts())
print("Test fault_type counts:\n", test.fault_type.value_counts())

CLASSES = sorted(full.fault_type.unique().tolist())
label_map = {c: i for i, c in enumerate(CLASSES)}
inv_label_map = {i: c for c, i in label_map.items()}

X_train, X_test = train[FEATURES], test[FEATURES]
y_train_cls = train.fault_type.map(label_map)
y_test_cls = test.fault_type.map(label_map)

# ================= 1. Fault classification =================
clf = xgb.XGBClassifier(
    n_estimators=200, max_depth=5, learning_rate=0.08,
    subsample=0.85, colsample_bytree=0.85,
    objective='multi:softprob', num_class=len(CLASSES),
    eval_metric='mlogloss', random_state=42
)
clf.fit(X_train, y_train_cls)
pred_cls = clf.predict(X_test)
acc = accuracy_score(y_test_cls, pred_cls)
report = classification_report(y_test_cls, pred_cls, target_names=[inv_label_map[i] for i in range(len(CLASSES))], output_dict=True)
cm = confusion_matrix(y_test_cls, pred_cls)
print(f"\nClassification accuracy (held-out, later-timestep test set): {acc:.4f}")
print(classification_report(y_test_cls, pred_cls, target_names=CLASSES))

# ================= 2. Severity regression =================
y_train_sev = train.fault_severity
y_test_sev = test.fault_severity
reg_sev = xgb.XGBRegressor(
    n_estimators=250, max_depth=5, learning_rate=0.06,
    subsample=0.85, colsample_bytree=0.85, random_state=42
)
reg_sev.fit(X_train, y_train_sev)
pred_sev = reg_sev.predict(X_test)
mae_sev = mean_absolute_error(y_test_sev, pred_sev)
r2_sev = r2_score(y_test_sev, pred_sev)
print(f"\nSeverity regression -> MAE: {mae_sev:.4f}  R2: {r2_sev:.4f}")

# ================= 3. RUL regression =================
y_train_rul = train.rul_frac
y_test_rul = test.rul_frac
reg_rul = xgb.XGBRegressor(
    n_estimators=250, max_depth=5, learning_rate=0.06,
    subsample=0.85, colsample_bytree=0.85, random_state=42
)
reg_rul.fit(X_train, y_train_rul)
pred_rul = reg_rul.predict(X_test)
mae_rul = mean_absolute_error(y_test_rul, pred_rul)
r2_rul = r2_score(y_test_rul, pred_rul)
print(f"RUL regression -> MAE: {mae_rul:.4f}  R2: {r2_rul:.4f}")

# ================= 4. Real SHAP explainability =================
explainer = shap.TreeExplainer(clf)
shap_values = explainer.shap_values(X_test)  # shape (n_samples, n_features, n_classes) for newer shap, or list
if isinstance(shap_values, list):
    mean_abs_shap = {CLASSES[c]: np.abs(shap_values[c]).mean(axis=0) for c in range(len(CLASSES))}
else:
    mean_abs_shap = {CLASSES[c]: np.abs(shap_values[:, :, c]).mean(axis=0) for c in range(len(CLASSES))}

shap_summary = {}
for c, vals in mean_abs_shap.items():
    ranked = sorted(zip(FEATURES, vals), key=lambda x: -x[1])[:6]
    shap_summary[c] = [{'feature': f, 'mean_abs_shap': float(v)} for f, v in ranked]

print("\nTop SHAP features per class:")
for c, feats in shap_summary.items():
    print(c, [f"{f['feature']}={f['mean_abs_shap']:.3f}" for f in feats])

# ================= Save everything =================
joblib.dump(clf, 'model_fault_classifier.joblib')
joblib.dump(reg_sev, 'model_severity_regressor.joblib')
joblib.dump(reg_rul, 'model_rul_regressor.joblib')

with open('model_report.json', 'w') as f:
    json.dump({
        'classes': CLASSES,
        'features': FEATURES,
        'train_rows': len(train),
        'test_rows': len(test),
        'chronological_split_cutoff_sec': CUTOFF_T,
        'classification_accuracy': acc,
        'classification_report': report,
        'confusion_matrix': cm.tolist(),
        'severity_mae': mae_sev,
        'severity_r2': r2_sev,
        'rul_mae': mae_rul,
        'rul_r2': r2_rul,
        'shap_top_features_per_class': shap_summary,
    }, f, indent=2)

print("\nSaved: model_fault_classifier.joblib, model_severity_regressor.joblib, model_rul_regressor.joblib, model_report.json")
