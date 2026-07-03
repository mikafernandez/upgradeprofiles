"""Generate synthetic fleet telemetry for development and testing."""
from __future__ import annotations
import pandas as pd
import numpy as np
from pathlib import Path


MACHINE_PROFILES = {
    "A-7F3": {
        "target_mh": 70,
        "fuel_rate_l_h": 2.2,
        "op_hours_mean": 5.5,
        "label": "Kleinstbagger (stationaer)",
    },
    "A-2B9": {
        "target_mh": 140,
        "fuel_rate_l_h": 2.2,
        "op_hours_mean": 6.5,
        "label": "Kleinstbagger (leicht mobil)",
    },
    "A-5C1": {
        "target_mh": 215,
        "fuel_rate_l_h": 8.5,
        "op_hours_mean": 7.0,
        "label": "Mobilbagger (Grenzfall)",
    },
    "A-9D4": {
        "target_mh": 390,
        "fuel_rate_l_h": 8.5,
        "op_hours_mean": 8.0,
        "label": "Mobilbagger (hoch mobil)",
    },
}


def generate_fleet_telemetry(
    output_path: Path | None = None,
    n_days: int = 90,
    seed: int = 42,
) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    dates = pd.date_range("2024-07-01", periods=n_days, freq="D")
    rows: list[dict] = []

    for machine_id, params in MACHINE_PROFILES.items():
        target_ms = params["target_mh"] / 3600.0
        noise_scale = target_ms * 0.18

        for date in dates:
            op_hours = float(np.clip(rng.normal(params["op_hours_mean"], 0.9), 2.0, 10.0))
            left = float(np.clip(rng.normal(target_ms, noise_scale), 0.0, None))
            right = float(np.clip(rng.normal(target_ms, noise_scale), 0.0, None))
            fuel = op_hours * params["fuel_rate_l_h"] * float(rng.normal(1.0, 0.04))
            rpm = float(rng.normal(1870, 110))

            rows.append({
                "date": date.strftime("%Y-%m-%d"),
                "machine_id": machine_id,
                "op_hours_daily": round(op_hours, 2),
                "track_speed_left_ms": round(left, 5),
                "track_speed_right_ms": round(right, 5),
                "fuel_consumed_l": round(max(0.0, fuel), 2),
                "engine_rpm_mean": round(rpm, 1),
            })

    df = pd.DataFrame(rows)

    if output_path is not None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        df.to_csv(output_path, index=False)

    return df


def ensure_sample_exists(base_path: Path | None = None) -> Path:
    """Create sample CSV if it doesn't exist yet. Returns the path."""
    root = base_path or Path(__file__).parent.parent
    target = root / "data" / "samples" / "fleet_telemetry.csv"
    if not target.exists():
        generate_fleet_telemetry(output_path=target)
    return target


if __name__ == "__main__":
    out = Path(__file__).parent.parent / "data" / "samples" / "fleet_telemetry.csv"
    df = generate_fleet_telemetry(output_path=out)
    print(f"Generiert: {len(df)} Zeilen fuer {df['machine_id'].nunique()} Maschinen -> {out}")
    print(df.groupby("machine_id")[["op_hours_daily", "track_speed_left_ms"]].mean().round(4))
