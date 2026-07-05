from __future__ import annotations
import json
import streamlit as st
from schemas.upgrade_profile import (
    UpgradeProfile, BucketNode, AggregationOperator, Factor, SavingsMechanism,
    save_profile_yaml,
)

STATUS_COLOR = {"ok": "#2ecc71", "partial": "#e67e22", "missing": "#e74c3c", "neutral": "#6b7280"}
STATUS_LABEL = {"ok": "vollstaendig", "partial": "teilweise fehlend", "missing": "fehlend", "neutral": "nicht ausgewertet"}


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


def _flatten_paths(node: BucketNode, path=(), depth=0, acc=None):
    if acc is None:
        acc = []
    prefix = ("　" * depth) + ("└ " if depth else "")
    acc.append((path, f"{prefix}{node.name} [{node.node_type}]", node))
    for i, child in enumerate(node.children):
        _flatten_paths(child, path + (i,), depth + 1, acc)
    return acc


def _rebuild_with_new_child(
    node: BucketNode, path: tuple, new_leaf: BucketNode,
    operator: AggregationOperator, new_branch_name: str,
) -> BucketNode:
    if not path:
        if node.node_type == "leaf":
            # Elternknoten war bisher ein Blatt -> zu Ast promoten, altes Blatt als Kind uebernehmen.
            return BucketNode(name=new_branch_name, node_type="branch", operator=operator,
                               children=[node, new_leaf])
        return node.model_copy(update={"children": list(node.children) + [new_leaf]})
    idx = path[0]
    new_children = list(node.children)
    new_children[idx] = _rebuild_with_new_child(new_children[idx], path[1:], new_leaf, operator, new_branch_name)
    return node.model_copy(update={"children": new_children})


def render_bucket_assignment_form(family: UpgradeProfile, key_prefix: str) -> None:
    """Formularbasierter Ersatz fuer Drag-and-Drop (in Vanilla Streamlit nicht verfuegbar):
    neues Blatt definieren, Elternknoten per Selectbox waehlen, Operator waehlen, speichern."""
    root = family.savings_mechanism
    paths = _flatten_paths(root)

    st.markdown("**Neues Bucket-Blatt anlegen und zuordnen**")
    with st.form(f"{key_prefix}_bucket_form"):
        leaf_name = st.text_input("Name des neuen Blatts", key=f"{key_prefix}_leaf_name")
        leaf_formula = st.text_input(
            "Formel (Python-Ausdruck)", key=f"{key_prefix}_leaf_formula",
            placeholder="op_hours_year * maintenance_saving_eur_h",
        )
        leaf_unit = st.text_input("Einheit", value="EUR/year", key=f"{key_prefix}_leaf_unit")
        factors_raw = st.text_area(
            "Faktoren (JSON-Liste von {name, dimension_ref, description})",
            value="[]", key=f"{key_prefix}_leaf_factors", height=80,
        )

        parent_idx = st.selectbox(
            "Elternknoten", options=list(range(len(paths))),
            format_func=lambda i: paths[i][1], key=f"{key_prefix}_parent",
        )
        operator_choice = st.selectbox(
            "Aggregationsoperator (nur relevant, falls Elternknoten bisher ein Blatt ist)",
            options=[op.value for op in AggregationOperator], key=f"{key_prefix}_operator",
        )
        new_branch_name = st.text_input(
            "Name des neuen Astes (nur falls Elternknoten bisher ein Blatt war)",
            value="", key=f"{key_prefix}_branch_name",
            placeholder="z.B. gesamt_sum",
        )

        submitted = st.form_submit_button("Bucket zuordnen und speichern")
        if submitted:
            try:
                factors = [Factor(**f) for f in json.loads(factors_raw)]
                new_leaf = BucketNode(
                    name=leaf_name, node_type="leaf",
                    mechanism=SavingsMechanism(formula=leaf_formula, factors=factors, unit=leaf_unit),
                )
                path, _, target_node = paths[parent_idx]
                branch_name = new_branch_name.strip() or f"{target_node.name}_bucket"
                new_root = _rebuild_with_new_child(
                    root, path, new_leaf, AggregationOperator(operator_choice), branch_name,
                )
                updated_family = family.model_copy(update={"savings_mechanism": new_root})
                saved = save_profile_yaml(updated_family)
                st.success(f"Bucket-Baum gespeichert: `{saved}`")
                st.rerun()
            except Exception as e:
                st.error(f"Fehler: {e}")
