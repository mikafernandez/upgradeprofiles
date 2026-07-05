from __future__ import annotations
import pandas as pd
from pydantic import BaseModel
from schemas.upgrade_profile import UpgradeProfile, UpgradeInstance, BucketNode, AggregationOperator
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
    caveats: list[str] = []
    missing_branches: list[str] = []
    bucket_tree: "BucketEvalResult | None" = None


class BucketEvalResult(BaseModel):
    """Auswertungs-Ergebnis eines einzelnen BucketNode-Knotens (nicht persistiert)."""
    name: str
    node_type: str  # "leaf" | "branch"
    value: float = 0.0
    missing: bool = False
    reason: str | None = None
    unknown: bool = False              # and_gate: unentscheidbar (nie hart auf False gesetzt)
    partial_coverage: bool = False
    coverage: float = 1.0
    missing_children: list[str] = []
    children: list["BucketEvalResult"] = []


BucketEvalResult.model_rebuild()
EvaluationResult.model_rebuild()


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


def _dimension_kind_lookup(profile: UpgradeProfile) -> dict[str, str]:
    return {d.name: d.kind for d in profile.data_dimensions}


def _evaluate_leaf(
    node: BucketNode,
    dim_lookup: dict[str, str],
    werkstatt_mappings: dict[str, GueteflagResult],
    machine_df: pd.DataFrame,
    instance_values: dict[str, float],
    context_params: dict,
) -> BucketEvalResult:
    namespace: dict = {}
    for factor in node.mechanism.factors:
        ref = factor.dimension_ref
        kind = dim_lookup.get(ref)
        if kind == "EC":
            mapping = werkstatt_mappings.get(ref)
            if mapping is None or mapping.flag == "RED":
                return BucketEvalResult(
                    name=node.name,
                    node_type="leaf",
                    value=0.0,
                    missing=True,
                    reason=f"Signal '{ref}' nicht verfuegbar (Dimension nicht operationalisierbar)",
                )
            if ref == "op_hours_year":
                namespace[factor.name] = _compute_op_hours_year(machine_df)
            elif ref == "hourly_distance":
                namespace[factor.name] = _compute_hourly_distance(machine_df)
            else:
                namespace[factor.name] = 0.0
        elif kind == "instance":
            namespace[factor.name] = instance_values.get(ref, 0.0)
        else:  # "context" oder unbekannte Dimension
            namespace[factor.name] = context_params.get(ref, 0.0)

    value = _eval_formula(node.mechanism.formula, namespace)
    return BucketEvalResult(name=node.name, node_type="leaf", value=value)


def _evaluate_bucket_node(
    node: BucketNode,
    dim_lookup: dict[str, str],
    werkstatt_mappings: dict[str, GueteflagResult],
    machine_df: pd.DataFrame,
    instance_values: dict[str, float],
    context_params: dict,
) -> BucketEvalResult:
    if node.node_type == "leaf":
        return _evaluate_leaf(node, dim_lookup, werkstatt_mappings, machine_df, instance_values, context_params)

    children = [
        _evaluate_bucket_node(c, dim_lookup, werkstatt_mappings, machine_df, instance_values, context_params)
        for c in node.children
    ]
    missing_children = [c.name for c in children if c.missing or c.missing_children or c.unknown]

    if node.operator == AggregationOperator.SUM:
        value = sum(c.value for c in children)
        return BucketEvalResult(
            name=node.name, node_type="branch", value=value,
            missing_children=missing_children, children=children,
        )

    if node.operator == AggregationOperator.AND_GATE:
        if missing_children:
            return BucketEvalResult(
                name=node.name, node_type="branch", value=0.0, unknown=True,
                missing_children=missing_children, children=children,
            )
        value = 1.0 if children and all(bool(c.value) for c in children) else 0.0
        return BucketEvalResult(name=node.name, node_type="branch", value=value, children=children)

    if node.operator == AggregationOperator.PROPORTIONAL_SCALE:
        known = [c for c in children if not (c.missing or c.missing_children or c.unknown)]
        value = sum(c.value for c in known)
        coverage = (len(known) / len(children)) if children else 1.0
        return BucketEvalResult(
            name=node.name, node_type="branch", value=value,
            partial_coverage=coverage < 1.0, coverage=coverage,
            missing_children=missing_children, children=children,
        )

    return BucketEvalResult(name=node.name, node_type="branch", value=0.0, missing=True,
                             reason=f"Unbekannter Operator: {node.operator}", children=children)


def _collect_caveats(result: BucketEvalResult) -> list[str]:
    caveats: list[str] = []

    def walk(n: BucketEvalResult) -> None:
        if n.node_type == "leaf":
            if n.missing:
                caveats.append(f"{n.name}: nicht eingepreist – {n.reason}")
        else:
            if n.unknown:
                caveats.append(
                    f"{n.name}: Ergebnis unbekannt – fehlende Aeste: {', '.join(n.missing_children)}"
                )
            elif n.partial_coverage:
                caveats.append(
                    f"{n.name}: nur teilweise abgedeckt ({n.coverage:.0%}) – "
                    f"fehlende Aeste: {', '.join(n.missing_children)}"
                )
            elif n.missing_children:
                caveats.append(
                    f"{n.name}: Teilbetrag nicht eingepreist – fehlende Aeste: {', '.join(n.missing_children)}"
                )
            for c in n.children:
                walk(c)

    walk(result)
    return caveats


def _collect_missing_branches(result: BucketEvalResult) -> list[str]:
    flagged: list[str] = []

    def walk(n: BucketEvalResult) -> None:
        if n.missing or n.unknown or n.partial_coverage or n.missing_children:
            flagged.append(n.name)
        for c in n.children:
            walk(c)

    walk(result)
    return flagged


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
    dim_lookup = _dimension_kind_lookup(resolved_profile)
    gate = resolved_profile.feasibility_gate

    for machine_id in machine_ids:
        if "machine_id" in telemetry_df.columns:
            mdf = telemetry_df[telemetry_df["machine_id"] == machine_id].copy()
        else:
            mdf = telemetry_df.copy()

        notes: list[str] = []
        guete_flags = {name: m.flag for name, m in werkstatt_mappings.items()}

        gate_mapping = werkstatt_mappings.get(gate.dimension_ref)
        out_of_scope = gate_mapping is None or gate_mapping.flag == "RED"

        if out_of_scope:
            notes.append(
                f"out_of_scope: Gate-Dimension '{gate.dimension_ref}' konnte nicht "
                "operationalisiert werden."
            )
            results.append(EvaluationResult(
                machine_id=machine_id,
                feasible=False,
                gate_dimension_value=0.0,
                gate_threshold=gate.threshold,
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

        feasible = _apply_operator(gate_dim, gate.operator, gate.threshold)
        package_candidate = not feasible and gate_dim <= gate.threshold * 1.5

        tree_result = _evaluate_bucket_node(
            resolved_profile.savings_mechanism, dim_lookup, werkstatt_mappings,
            mdf, instance_values, context_params,
        )
        savings = tree_result.value
        caveats = _collect_caveats(tree_result)
        missing_branches = _collect_missing_branches(tree_result)
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
        notes.extend(caveats)

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
            caveats=caveats,
            missing_branches=missing_branches,
            bucket_tree=tree_result,
        ))

    return results
