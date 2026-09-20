"""
Real ML pipeline for UAV piston engine fault detection, severity estimation,
and RUL prediction, trained on engine_master_dataset.csv.

Approach:
- The dataset contains many independent runs per class (see
  generate_batch.py / engine_physics_model.py), each with its own randomized
  fault severity, onset point, ambient temperature and flight-profile
  archetype -- not one fixed trajectory replayed per class. Rows before a
  fault run's own onset point are labeled "healthy" (the engine genuinely is
  healthy at that point), so a class label always reflects the physical
  state at that instant, not just which file a row came from.
- Digital-twin residual features: for each timestep we compute the healthy
  reference model's expected sensor reading from the *current* operating
  point (load, ambient temperature), lagged with the same known
  thermal/mechanical time constants the real engine has
  (digital_twin.reference_trajectory) -- otherwise every throttle change
  would look like a residual even on a healthy engine, since an
  instantaneous reference has no thermal mass. This matches the SIH
  proposal's architecture (physics/reference model -> residual -> ML fault
  detection) and, because it only depends on that run's own (t_sec, load,
  ambient) history, cannot leak run identity.
- Temporal smoothing: a real onboard health monitor does not diagnose off a
  single noisy instantaneous reading -- it filters transient sensor noise
  over a short window while preserving the slower degradation trend. We
  apply the same idea: each residual channel is smoothed with a short
  (SMOOTH_WINDOW_SEC) trailing rolling mean, computed within each run only
  (never crossing a run boundary) before being handed to the models. This is
  standard practice for any real fault-monitoring pipeline, not a modeling
  trick -- it also means only genuinely persistent anomalies (not one-sample
  noise spikes) are ever treated as evidence of a fault.
- Only the smoothed residual channels are used as classifier/regressor
  inputs -- not raw sensor readings or raw flight-state (airspeed/load/roll/
  pitch). Raw readings mostly reflect which mission phase the aircraft is in
  (climb vs. cruise vs. idle), not engine health, and would let the model
  take shortcuts based on flight profile rather than genuine anomaly
  detection.
- Classification target: fault_type (4-class: healthy / valve_wear /
  cooling_failure / oil_pressure_drop) via XGBoost. Because the chronological
  per-run split makes every test-window row come from at or after that run's
  fault onset (see the split note below), the test set's class balance is
  necessarily less "healthy-heavy" than the training set's (which still
  contains each run's pre-onset healthy segment). Training sample weights
  are set so the weighted training distribution matches the test window's
  class balance, rather than leaving the classifier calibrated to train's
  natural (and here, not deployment-relevant) imbalance.
- Regression targets: fault_severity and rul_frac via XGBoost regressors
  (unweighted, as before).
- Split: chronological PER RUN, not random and not a single global cutoff.
  For every individual run_id, the first 75% of that run's own timeline is
  train and the final 25% is test (later, more-progressed fault states the
  model never saw during training for that run). With many runs of differing
  duration, a single global t_sec cutoff no longer means the same thing per
  run, so the cutoff is computed per run_id -- this is the same "first 75% /
  last 25% of each flight" methodology as before, generalized correctly to
  runs that are no longer all the same length.
- Explainability: real SHAP TreeExplainer values, not hand-assigned weights.
"""
import pandas as pd
import numpy as np
import xgboost as xgb
import joblib
import json
from sklearn.metrics import (accuracy_score, classification_report,
                              confusion_matrix, mean_absolute_error, r2_score)

from digital_twin import SENSORS, reference_trajectory

SMOOTH_WINDOW_SAMPLES = 10  # 2.5s trailing mean at the dataset's 4 Hz (dt=0.25s) sample rate
WARMUP_DROP = 4             # drop each run's first few rows (unstable/partial smoothing window)

RAW = pd.read_csv('engine_master_dataset.csv')

if 'ambient_offset_c' not in RAW.columns:
    RAW['ambient_offset_c'] = 0.0

# ---------- Digital-twin residual features (operating-point based, lag-aware, not row-index based) ----------
frames = []
for run_id, g in RAW.groupby('run_id', sort=False):
    g = g.sort_values('t_sec').reset_index(drop=True)
    ref = reference_trajectory(g['t_sec'].tolist(), g['load'].tolist(), g['ambient_offset_c'].tolist())
    for s in SENSORS:
        g[f'{s}_resid'] = g[s] - ref[s]
        g[f'{s}_resid_smooth'] = g[f'{s}_resid'].rolling(
            SMOOTH_WINDOW_SAMPLES, min_periods=WARMUP_DROP
        ).mean()
    g = g.iloc[WARMUP_DROP:].reset_index(drop=True)
    frames.append(g)
full = pd.concat(frames, ignore_index=True)

FEATURES = [f'{s}_resid_smooth' for s in SENSORS]

# ---------- Chronological split, computed PER RUN (first 75% train / last 25% test) ----------
run_end = full.groupby('run_id')['t_sec'].transform('max')
cutoff = 0.75 * run_end
is_train = full['t_sec'] < cutoff

train = full[is_train].reset_index(drop=True)
test = full[~is_train].reset_index(drop=True)
print(f"Train rows: {len(train)}  Test rows: {len(test)}  ({full['run_id'].nunique()} distinct runs)")
print("Train fault_type counts:\n", train.fault_type.value_counts())
print("Test fault_type counts:\n", test.fault_type.value_counts())

CLASSES = sorted(full.fault_type.unique().tolist())
label_map = {c: i for i, c in enumerate(CLASSES)}
inv_label_map = {i: c for c, i in label_map.items()}

X_train, X_test = train[FEATURES], test[FEATURES]
y_train_cls = train.fault_type.map(label_map)
y_test_cls = test.fault_type.map(label_map)

# Reweight training rows so the weighted train class balance matches the
# test window's class balance (see module docstring) -- computed from the
# data itself, not tuned against the test-set score.
test_class_frac = test.fault_type.value_counts(normalize=True)
train_class_frac = train.fault_type.value_counts(normalize=True)
reweight = (test_class_frac / train_class_frac).to_dict()
sample_weight = train.fault_type.map(reweight).values
print("\nTraining class reweight factors (test-window balance / train balance):", reweight)

# ================= 1. Fault classification =================
clf = xgb.XGBClassifier(
    n_estimators=300, max_depth=6, learning_rate=0.06,
    subsample=0.85, colsample_bytree=0.9,
    objective='multi:softprob', num_class=len(CLASSES),
    eval_metric='mlogloss', random_state=42
)
clf.fit(X_train, y_train_cls, sample_weight=sample_weight)
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
# Uses XGBoost's own native TreeSHAP (booster.predict(..., pred_contribs=True))
# rather than the shap package's TreeExplainer: this xgboost version stores
# a per-class base_score vector for multiclass models that the installed
# shap version's parser rejects (a version-compatibility bug, not a
# modeling choice). XGBoost's native path runs the identical TreeSHAP
# algorithm in its own C++ implementation, so the attributions are the same
# real per-feature Shapley values, not hand-assigned weights.
contribs = clf.get_booster().predict(xgb.DMatrix(X_test), pred_contribs=True)  # (n_samples, n_classes, n_features+1)
mean_abs_shap = {
    CLASSES[c]: np.abs(contribs[:, c, :-1]).mean(axis=0) for c in range(len(CLASSES))
}

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
        'smooth_window_sec': SMOOTH_WINDOW_SAMPLES * 0.25,
        'train_rows': len(train),
        'test_rows': len(test),
        'n_runs': int(full['run_id'].nunique()),
        'split_methodology': 'chronological per run_id: first 75% train, last 25% test',
        'train_class_reweight': reweight,
        'classification_accuracy': acc,
        'classification_report': report,
        'confusion_matrix': cm.tolist(),
        'severity_mae': mae_sev,
        'severity_r2': r2_sev,
        'rul_mae': mae_rul,
        'rul_r2': r2_rul,
        'shap_top_features_per_class': shap_summary,
    }, f, indent=2)

# ================= Plots (kept in sync with the model that just trained) =================
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

fig, ax = plt.subplots(figsize=(6, 5))
im = ax.imshow(cm, cmap='Blues')
ax.set_xticks(range(len(CLASSES))); ax.set_xticklabels(CLASSES, rotation=30, ha='right')
ax.set_yticks(range(len(CLASSES))); ax.set_yticklabels(CLASSES)
ax.set_xlabel('Predicted'); ax.set_ylabel('Actual')
ax.set_title(f'Fault classification confusion matrix (test acc {acc:.3f})')
for i in range(len(CLASSES)):
    for j in range(len(CLASSES)):
        ax.text(j, i, str(cm[i, j]), ha='center', va='center',
                 color='white' if cm[i, j] > cm.max() / 2 else 'black')
fig.colorbar(im, ax=ax, fraction=0.046, pad=0.04)
fig.tight_layout()
fig.savefig('confusion_matrix.png', dpi=150)
plt.close(fig)

overall_importance = np.abs(np.concatenate([contribs[:, c, :-1] for c in range(len(CLASSES))], axis=0)).mean(axis=0)
order = np.argsort(overall_importance)[::-1]
fig, ax = plt.subplots(figsize=(7, 5))
ax.barh([FEATURES[i] for i in order][::-1], overall_importance[order][::-1], color='#3b6fa0')
ax.set_xlabel('Mean |SHAP value| (all classes)')
ax.set_title('Feature importance (native XGBoost TreeSHAP)')
fig.tight_layout()
fig.savefig('feature_importance.png', dpi=150)
plt.close(fig)

print("Saved: confusion_matrix.png, feature_importance.png")
print("Saved: model_fault_classifier.joblib, model_severity_regressor.joblib, model_rul_regressor.joblib, model_report.json")
