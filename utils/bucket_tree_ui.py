from __future__ import annotations
import json
import graphviz
import streamlit as st
from schemas.upgrade_profile import (
    UpgradeProfile, BucketNode, AggregationOperator, Factor, SavingsMechanism,
    EdgeModifier, ModifierKind, FeasibilityGate,
    save_profile_yaml, attach_mechanism_to_bucket, list_output_bucket_options,
)

STATUS_COLOR = {"ok": "#2ecc71", "partial": "#e67e22", "missing": "#e74c3c", "neutral": "#6b7280"}
STATUS_LABEL = {"ok": "vollstaendig", "partial": "teilweise fehlend", "missing": "fehlend", "neutral": "nicht ausgewertet"}
MODIFIER_EDGE_COLOR = {
    ModifierKind.VETO_HARD: "#e74c3c",
    ModifierKind.VETO_SCALED: "#e74c3c",
    ModifierKind.SYNERGY_MULTIPLIER: "#2ecc71",
}


def _node_status(node: BucketNode, eval_result) -> str:
    if eval_result is None:
        return "neutral"
    if node.node_type == "leaf":
        return "missing" if eval_result.missing else "ok"
    if eval_result.unknown:
        return "missing"
    if eval_result.partial_coverage or eval_result.missing_children:
        return "partial"
    return "ok"


def _operator_label(node: BucketNode, eval_result) -> str:
    if node.operator == AggregationOperator.PROPORTIONAL_SCALE and eval_result is not None:
        return f"proportional_scale · × {eval_result.coverage:.0%} Abdeckung"
    return node.operator.value if node.operator else ""


def bucket_tree_to_graphviz(root: BucketNode, eval_result=None) -> graphviz.Digraph:
    """Baut eine echte Baumgrafik: Branch-Knoten als abgerundete Box, Leaf-Knoten als
    Ellipse, Fuellfarbe nach Auswertungsstatus. Kanten mit edge_modifier werden
    gestrichelt gezeichnet und mit dem Modifikator-Typ (bzw. dessen Wirkung, falls
    eval_result vorliegt) beschriftet."""
    dot = graphviz.Digraph()
    dot.attr("graph", rankdir="TB", bgcolor="transparent")
    dot.attr("node", fontname="Helvetica", fontsize="11", color="#374151")
    dot.attr("edge", fontname="Helvetica", fontsize="9", color="#9ca3af", fontcolor="#9ca3af")

    def add_node(node: BucketNode, node_eval, node_id: str) -> None:
        status = _node_status(node, node_eval)
        color = STATUS_COLOR[status]
        label = node.name
        if node_eval is not None:
            label += f"\n{node_eval.value:,.0f}"
        if node.node_type == "leaf":
            dot.node(node_id, label, shape="ellipse", style="filled", fillcolor=color, fontcolor="white")
        else:
            dot.node(node_id, label, shape="box", style="filled,rounded", fillcolor=color, fontcolor="white")

        children_eval = node_eval.children if node_eval is not None else [None] * len(node.children)
        for i, (child, child_eval) in enumerate(zip(node.children, children_eval)):
            child_id = f"{node_id}_{i}"
            add_node(child, child_eval, child_id)

            edge_label = node.operator.value if node.node_type == "branch" and node.operator else ""
            edge_kwargs: dict[str, str] = {}
            if child.edge_modifier is not None:
                mod = child.edge_modifier
                mod_color = MODIFIER_EDGE_COLOR[mod.kind]
                edge_kwargs = {"style": "dashed", "color": mod_color, "fontcolor": mod_color}
                mod_label = mod.kind.value
                if child_eval is not None and child_eval.modifier_applied:
                    mod_label = child_eval.modifier_applied
                edge_label = f"{edge_label}\n[{mod_label}]" if edge_label else f"[{mod_label}]"
            dot.edge(node_id, child_id, label=edge_label, **edge_kwargs)

    add_node(root, eval_result, "root")
    return dot


def render_bucket_tree_graph(root: BucketNode, eval_result=None) -> None:
    dot = bucket_tree_to_graphviz(root, eval_result)
    st.graphviz_chart(dot, use_container_width=True)


def render_bucket_tree_readonly(node: BucketNode, eval_result=None, depth: int = 0) -> None:
    """Rekursive, read-only Baumdarstellung. eval_result (BucketEvalResult) ist optional
    fuer die reine Struktur-Ansicht (z.B. beim Editieren), Pflicht fuer Zustandsfarben."""
    status = _node_status(node, eval_result)
    color = STATUS_COLOR[status]
    indent = depth * 24

    if node.node_type == "leaf":
        label = f"🍃 {node.name}"
        detail = f"<code>{node.mechanism.formula}</code>"
    else:
        label = f"🌳 {node.name}"
        detail = f"<span style='color:#9ca3af'>▸ {_operator_label(node, eval_result)}</span>"

    value_txt = ""
    if eval_result is not None:
        value_txt = f" &nbsp; <b>{eval_result.value:,.1f}</b>"
        if status != "ok":
            value_txt += f" <span style='color:{color};font-size:0.8em'>[{STATUS_LABEL[status]}]</span>"

    st.markdown(
        f'<div style="margin-left:{indent}px;border-left:4px solid {color};'
        f'padding:0.3rem 0.6rem;margin-bottom:0.25rem;background:#1f2937;border-radius:4px">'
        f'{label} &nbsp; {detail}{value_txt}'
        f'</div>',
        unsafe_allow_html=True,
    )
    if eval_result is not None and node.node_type == "leaf" and eval_result.missing and eval_result.reason:
        st.markdown(
            f"<div style='margin-left:{indent + 24}px;color:{STATUS_COLOR['missing']};font-size:0.8em'>"
            f"{eval_result.reason}</div>",
            unsafe_allow_html=True,
        )

    children_eval = eval_result.children if eval_result is not None else [None] * len(node.children)
    for child, child_eval in zip(node.children, children_eval):
        render_bucket_tree_readonly(child, child_eval, depth + 1)


_OUTPUT_BUCKET_PLACEHOLDER = "— bitte waehlen —"


def render_bucket_assignment_form(family: UpgradeProfile, key_prefix: str, pending_demo: dict | None = None) -> None:
    """Neues Bucket-Blatt definieren und ueber output_bucket im Kontenrahmen zuordnen,
    optional mit einem Modifikator (veto_hard/veto_scaled/synergy_multiplier) auf der
    neuen Kante. pending_demo (optional) prefuellt Formel/Faktoren aus der Demo-Vorlage,
    output_bucket und Modifikator bleiben bewusst frei fuer die Live-Zuordnung."""
    root = family.savings_mechanism
    output_options = list_output_bucket_options()

    # Streamlit fuehrt den Code in jedem st.tabs()-Block bei JEDER Interaktion aus, auch wenn
    # der Tab gerade nicht sichtbar ist. Deshalb hier hart ueberschreiben (nicht setdefault):
    # sobald pending_demo gesetzt ist, MUSS es die Anzeige bestimmen, sonst haette ein frueherer,
    # unsichtbarer Render-Durchlauf mit pending_demo=None bereits leere Defaults einzementiert.
    if pending_demo is not None:
        st.session_state[f"{key_prefix}_leaf_name"] = pending_demo["leaf_name"]
        st.session_state[f"{key_prefix}_leaf_formula"] = pending_demo["formula"]
        st.session_state[f"{key_prefix}_leaf_unit"] = pending_demo["unit"]
        st.session_state[f"{key_prefix}_leaf_factors"] = json.dumps(pending_demo["factors"])
    else:
        st.session_state.setdefault(f"{key_prefix}_leaf_name", "")
        st.session_state.setdefault(f"{key_prefix}_leaf_formula", "")
        st.session_state.setdefault(f"{key_prefix}_leaf_unit", "EUR/year")
        st.session_state.setdefault(f"{key_prefix}_leaf_factors", "[]")

    st.markdown("**Neues Bucket-Blatt anlegen und im Kontenrahmen zuordnen**")
    with st.form(f"{key_prefix}_bucket_form"):
        leaf_name = st.text_input("Name des neuen Blatts", key=f"{key_prefix}_leaf_name")
        leaf_formula = st.text_input(
            "Formel (Python-Ausdruck)", key=f"{key_prefix}_leaf_formula",
            placeholder="op_hours_year * maintenance_saving_eur_h",
        )
        leaf_unit = st.text_input("Einheit", key=f"{key_prefix}_leaf_unit")
        factors_raw = st.text_area(
            "Faktoren (JSON-Liste von {name, dimension_ref, description})",
            key=f"{key_prefix}_leaf_factors", height=80,
        )

        output_bucket_main = st.selectbox(
            "Kontenrahmen-Hauptklasse (output_bucket)",
            options=[""] + output_options, key=f"{key_prefix}_output_bucket",
            format_func=lambda v: _OUTPUT_BUCKET_PLACEHOLDER if v == "" else v,
        )
        output_bucket_suffix = st.text_input(
            "Optionaler Unterpfad (freier Suffix, z.B. 'idle_share')",
            value="", key=f"{key_prefix}_output_suffix",
        )

        st.markdown("**Modifikator auf dieser Kante (optional)**")
        modifier_choice = st.radio(
            "Modifikator-Typ", ["kein Modifikator", "veto_hard", "veto_scaled", "synergy_multiplier"],
            horizontal=True, key=f"{key_prefix}_modifier_kind",
        )
        gate_desc = st.text_input("Bedingungsbeschreibung", key=f"{key_prefix}_mod_gate_desc")
        gate_dim = st.text_input("Dimension (dimension_ref)", key=f"{key_prefix}_mod_gate_dim")
        gate_op = st.selectbox("Operator", [">=", "<=", ">", "<", "=="], key=f"{key_prefix}_mod_gate_op")
        gate_thresh = st.number_input("Schwellenwert", value=0.0, key=f"{key_prefix}_mod_gate_thresh")
        factor = st.number_input("Faktor (z.B. 0.7 = 30% Reduktion)", value=0.7, step=0.05, key=f"{key_prefix}_mod_factor")
        with_upgrade = st.text_input("Ausloesendes Upgrade (with_upgrade)", key=f"{key_prefix}_mod_with_upgrade")

        submitted = st.form_submit_button("Bucket zuordnen und speichern")
        if submitted:
            try:
                if not output_bucket_main:
                    raise ValueError("Bitte eine Kontenrahmen-Hauptklasse (output_bucket) waehlen")
                factors = [Factor(**f) for f in json.loads(factors_raw)]
                full_output_bucket = output_bucket_main
                if output_bucket_suffix.strip():
                    full_output_bucket += f".{output_bucket_suffix.strip()}"
                mechanism = SavingsMechanism(
                    formula=leaf_formula, factors=factors, unit=leaf_unit, output_bucket=full_output_bucket,
                )

                edge_modifier = None
                if modifier_choice == "veto_hard":
                    edge_modifier = EdgeModifier(kind=ModifierKind.VETO_HARD, gate=FeasibilityGate(
                        condition_description=gate_desc, dimension_ref=gate_dim, threshold=gate_thresh, operator=gate_op,
                    ))
                elif modifier_choice == "veto_scaled":
                    edge_modifier = EdgeModifier(kind=ModifierKind.VETO_SCALED, gate=FeasibilityGate(
                        condition_description=gate_desc, dimension_ref=gate_dim, threshold=gate_thresh, operator=gate_op,
                    ))
                elif modifier_choice == "synergy_multiplier":
                    edge_modifier = EdgeModifier(
                        kind=ModifierKind.SYNERGY_MULTIPLIER, factor=factor, with_upgrade=with_upgrade,
                    )

                new_root = attach_mechanism_to_bucket(root, full_output_bucket, leaf_name, mechanism, edge_modifier)
                updated_family = family.model_copy(update={"savings_mechanism": new_root})
                saved = save_profile_yaml(updated_family)

                pending = st.session_state.get("demo_pending_mechanisms")
                if pending and pending_demo is not None and pending[0]["leaf_name"] == leaf_name:
                    st.session_state["demo_pending_mechanisms"] = pending[1:]

                st.success(f"Bucket-Baum gespeichert: `{saved}`")
                st.rerun()
            except Exception as e:
                st.error(f"Fehler: {e}")
