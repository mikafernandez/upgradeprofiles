from __future__ import annotations
import pandas as pd
from pydantic import BaseModel
from schemas.upgrade_profile import UpgradeProfile, UpgradeInstance
from mediator.werkstatt import GueteflagResult, FLAG_CONFIDENCE


class EvaluationResult(BaseModel):
    machine_id: str
    feasible: bool
    gate_dimension_value: float
    gate_threshold: float
    savings_eur_year: float
    investment_eur: float
    payback_years: float | None
    confidence: float
    guete_flags: dict[str, str]
    package_candidate: bool
    out_of_scope: bool
    notes: list[str]


def _apply_operator(value: float, operator: str, threshold: float) -> bool:
    return {
        "<=": value <= threshold,
        ">=": value >= threshold,
        "<": value < threshold,
        ">": value > threshold,
        "==": abs(value - threshold) < 1e-9,
    }.get(operator, False)


def _eval_formula(formula: str, namespace: dict) -> float:
    try:
        safe_builtins = {"abs": abs, "max": max, "min": min, "round": round}
        return float(eval(formula, {"__builtins__": safe_builtins}, namespace))
    except Exception:
        return 0.0


def _compute_hourly_distance(machine_df: pd.DataFrame) -> float:
    left = "track_speed_left_ms"
    right = "track_speed_right_ms"
    if left in machine_df.columns and right in machine_df.columns:
        daily = (machine_df[left] + machine_df[right]).abs() / 2 * 3600
        return float(daily.mean())
    return 0.0


def _compute_op_hours_year(machine_df: pd.DataFrame) -> float:
    if "op_hours_daily" in machine_df.columns and len(machine_df) > 0:
        total = machine_df["op_hours_daily"].sum()
        return float(total / len(machine_df) * 365)
    return 0.0


def evaluate_upgrade(
    resolved_profile: UpgradeProfile,
    instance: UpgradeInstance | None,
    werkstatt_mappings: dict[str, GueteflagResult],
    telemetry_df: pd.DataFrame,
    context_params: dict,
) -> list[EvaluationResult]:
    results: list[EvaluationResult] = []

    if "machine_id" in telemetry_df.columns:
        machine_ids = sorted(telemetry_df["machine_id"].unique().tolist())
    else:
        machine_ids = ["default"]

    instance_values = instance.instance_values if instance else {}

    for machine_id in machine_ids:
        if "machine_id" in telemetry_df.columns:
            mdf = telemetry_df[telemetry_df["machine_id"] == machine_id].copy()
        else:
            mdf = telemetry_df.copy()

        notes: list[str] = []
        guete_flags = {name: m.flag for name, m in werkstatt_mappings.items()}
        out_of_scope = any(m.flag == "RED" for m in werkstatt_mappings.values())

        if out_of_scope:
            red_dims = [n for n, m in werkstatt_mappings.items() if m.flag == "RED"]
            notes.append(
                f"out_of_scope: Dimensionen {red_dims} konnten nicht operationalisiert werden."
            )
            results.append(EvaluationResult(
                machine_id=machine_id,
                feasible=False,
                gate_dimension_value=0.0,
                gate_threshold=resolved_profile.feasibility_gate.threshold,
                savings_eur_year=0.0,
                investment_eur=resolved_profile.total_investment,
                payback_years=None,
                confidence=0.0,
                guete_flags=guete_flags,
                package_candidate=False,
                out_of_scope=True,
                notes=notes,
            ))
            continue

        gate_dim = _compute_hourly_distance(mdf)
        op_hours_year = _compute_op_hours_year(mdf)

        gate = resolved_profile.feasibility_gate
        feasible = _apply_operator(gate_dim, gate.operator, gate.threshold)
        package_candidate = not feasible and gate_dim <= gate.threshold * 1.5

        namespace = {
            "op_hours_year": op_hours_year,
            "fuel_rate_l_h": instance_values.get("fuel_rate_l_h", 0.0),
            **context_params,
        }
        savings = _eval_formula(resolved_profile.savings_mechanism.formula, namespace)
        investment = resolved_profile.total_investment
        payback = (investment / savings) if savings > 0 else None

        conf_vals = [FLAG_CONFIDENCE[f] for f in guete_flags.values()]
        confidence = sum(conf_vals) / len(conf_vals) if conf_vals else 0.0

        if not feasible:
            if package_candidate:
                notes.append(
                    f"Grenzfall: {gate_dim:.0f} m/h (Gate: {gate.threshold:.0f} m/h). "
                    "Paket mit U-MINIBATT pruefen (Erweiterung auf 350 m/h)."
                )
            else:
                notes.append(
                    f"Gate nicht erfuellt: {gate_dim:.0f} m/h > {gate.threshold:.0f} m/h. "
                    "Kabelelectrifizierung nicht empfohlen."
                )
        if confidence < 0.9:
            notes.append(
                f"Konfidenz reduziert ({confidence:.0%}) durch Proxy-Dimensionen. "
                "Guete-Flags pruefen."
            )

        results.append(EvaluationResult(
            machine_id=machine_id,
            feasible=feasible,
            gate_dimension_value=gate_dim,
            gate_threshold=gate.threshold,
            savings_eur_year=savings,
            investment_eur=investment,
            payback_years=payback,
            confidence=confidence,
            guete_flags=guete_flags,
            package_candidate=package_candidate,
            out_of_scope=False,
            notes=notes,
        ))

    return results
