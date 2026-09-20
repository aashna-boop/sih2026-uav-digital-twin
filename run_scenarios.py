"""
run_scenarios.py

Generates telemetry CSV files for all 4 environmental flight scenarios
(high_altitude_cruise, hot_weather_ops, rapid_throttle_transition, endurance_mission)
and prints a summary comparing how the digital twin expected values shift
under each environmental condition.
"""
import pandas as pd
from engine_physics_model import generate_run

SCENARIOS = [
    ("high_altitude_cruise", 0, "High Altitude Cruise (3.5km)"),
    ("hot_weather_ops", 20.0, "Hot Weather Ops (45°C ambient)"),
    ("rapid_throttle_transition", 0, "Rapid Throttle Transitions"),
    ("endurance_mission", 0, "Endurance Mission (Long Flight)"),
]

def main():
    print("==========================================================================")
    print("Generating Environmental Scenario Telemetry Runs & Digital Twin Analysis")
    print("==========================================================================")

    results = []

    for arch, amb_off, label in SCENARIOS:
        rows = generate_run(
            fault_type="healthy",
            severity=0.0,
            onset_frac=0.0,
            ambient_offset_c=amb_off,
            seed=42,
            run_id=f"scenario_{arch}",
            archetype=arch
        )
        df = pd.DataFrame(rows)
        out_filename = f"scenario_{arch}.csv"
        df.to_csv(out_filename, index=False)

        # Calculate mean actual vs expected for key channels
        avg_egt = df["egt_c"].mean()
        avg_cht = df["cht_c"].mean()
        avg_rpm = df["rpm"].mean()
        avg_v = df["battery_voltage_v"].mean()
        avg_soc = df["battery_soc_pct"].iloc[-1]

        results.append({
            "Scenario": label,
            "CSV": out_filename,
            "Duration (s)": len(df) * 0.25,
            "Mean RPM": round(avg_rpm, 1),
            "Mean EGT (°C)": round(avg_egt, 1),
            "Mean CHT (°C)": round(avg_cht, 1),
            "Mean Batt Voltage (V)": round(avg_v, 2),
            "End SOC (%)": round(avg_soc, 1),
        })

    summary_df = pd.DataFrame(results)
    print("\n" + summary_df.to_string(index=False))
    print("\nAll scenario CSVs generated successfully!")

if __name__ == "__main__":
    main()
