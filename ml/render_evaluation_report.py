from __future__ import annotations

import argparse
import html
import json
from pathlib import Path


SHORT_LABELS = {
    "COOLING_DEGRADATION": "Cooling",
    "HEALTHY": "Healthy",
    "IGNITION_MISFIRE": "Ignition",
    "LUBRICATION_DEGRADATION": "Lubrication",
    "VALVE_WEAR": "Valve wear",
}


def render(metrics_path: Path, output_dir: Path) -> None:
    metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
    classification = metrics["classification"]
    labels = classification["labels"]
    matrix = classification["confusion_matrix"]
    output_dir.mkdir(parents=True, exist_ok=True)

    cell = 82
    left = 145
    top = 125
    width = left + cell * len(labels) + 45
    height = top + cell * len(labels) + 95
    maximum = max(max(row) for row in matrix)
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="#07131d"/>',
        '<style>text{font-family:Inter,Segoe UI,sans-serif;fill:#dceaf5}.muted{fill:#83a0b3}.small{font-size:11px}.value{font-size:15px;font-weight:700}</style>',
        '<text x="28" y="35" font-size="22" font-weight="700">Held-out profile confusion matrix</text>',
        '<text x="28" y="58" class="muted small">Rows: actual class · columns: predicted class · 14,476 test samples</text>',
        f'<text x="{left + cell * len(labels) / 2}" y="82" text-anchor="middle" class="muted small">PREDICTED</text>',
        f'<text x="28" y="{top + cell * len(labels) / 2}" transform="rotate(-90 28 {top + cell * len(labels) / 2})" text-anchor="middle" class="muted small">ACTUAL</text>',
    ]
    for index, label in enumerate(labels):
        name = html.escape(SHORT_LABELS.get(label, label))
        x = left + index * cell + cell / 2
        y = top - 12
        parts.append(
            f'<text x="{x}" y="{y}" text-anchor="middle" class="small" transform="rotate(-28 {x} {y})">{name}</text>'
        )
        row_y = top + index * cell + cell / 2 + 4
        parts.append(
            f'<text x="{left - 12}" y="{row_y}" text-anchor="end" class="small">{name}</text>'
        )

    for row_index, row in enumerate(matrix):
        row_total = max(sum(row), 1)
        for column_index, value in enumerate(row):
            ratio = value / maximum
            opacity = 0.12 + ratio * 0.78
            x = left + column_index * cell
            y = top + row_index * cell
            color = "#35d0ad" if row_index == column_index else "#ff796e"
            parts.append(
                f'<rect x="{x + 2}" y="{y + 2}" width="{cell - 4}" height="{cell - 4}" rx="4" fill="{color}" fill-opacity="{opacity:.3f}"/>'
            )
            parts.append(
                f'<text x="{x + cell / 2}" y="{y + cell / 2 - 2}" text-anchor="middle" class="value">{value}</text>'
            )
            parts.append(
                f'<text x="{x + cell / 2}" y="{y + cell / 2 + 17}" text-anchor="middle" class="muted small">{value / row_total * 100:.1f}%</text>'
            )
    parts.extend(
        [
            f'<text x="28" y="{height - 42}" class="muted small">Macro-F1 {classification["macro_f1"]:.4f} · Healthy false alarms {classification["false_alarm_events_per_healthy_hour"]:.1f}/h</text>',
            f'<text x="28" y="{height - 21}" class="muted small">Simulation-based; train/validation/test profiles are disjoint.</text>',
            "</svg>",
        ]
    )
    (output_dir / "confusion_matrix.svg").write_text("\n".join(parts), encoding="utf-8")

    report = [
        "# AegisTwin model evaluation",
        "",
        "> Simulation-based evidence. Profile IDs are separated across train, validation, and test.",
        "",
        f"![Held-out confusion matrix](confusion_matrix.svg)",
        "",
        "| Metric | Result |",
        "|---|---:|",
        f'| Macro-F1 | {classification["macro_f1"]:.4f} |',
        f'| Healthy false alarms/hour | {classification["false_alarm_events_per_healthy_hour"]:.1f} |',
        f'| Mean warning lead | {metrics["early_warning"]["mean_lead_time_sec"]:.1f} s |',
        f'| Median warning lead | {metrics["early_warning"]["median_lead_time_sec"]:.1f} s |',
        f'| RUL MAE | {metrics["rul"]["mae_minutes"]:.2f} simulated min |',
        f'| RUL interval coverage | {metrics["rul"]["interval_coverage"] * 100:.1f}% |',
        "",
        "The authoritative numeric source is `ml/models/metrics.json`.",
    ]
    (output_dir / "model_evaluation.md").write_text("\n".join(report) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Render portable model-evaluation artifacts")
    parser.add_argument("--metrics", type=Path, default=Path("ml/models/metrics.json"))
    parser.add_argument("--output-dir", type=Path, default=Path("reports"))
    args = parser.parse_args()
    render(args.metrics, args.output_dir)


if __name__ == "__main__":
    main()
