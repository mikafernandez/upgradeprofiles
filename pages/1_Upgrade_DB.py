from __future__ import annotations
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import streamlit as st
import yaml
from schemas.upgrade_profile import (
    UpgradeProfile, UpgradeInstance, FeasibilityGate, SavingsMechanism, BucketNode,
    load_families, load_instances, instance_diff, save_profile_yaml, save_instance_yaml,
    bucket_display_formula,
)
from utils.bucket_tree_ui import render_bucket_tree_readonly, render_bucket_assignment_form

st.set_page_config(page_title="Upgrade DB", layout="wide")

st.markdown("""
<style>
    .dim-card {
        background: #1f2937; border-radius: 8px;
        padding: 0.7rem 1rem; margin-bottom: 0.4rem;
        border-left: 4px solid #374151;
    }
    .dim-EC { border-left-color: #3b82f6; }
    .dim-instance { border-left-color: #10b981; }
    .dim-context { border-left-color: #8b5cf6; }
    .diff-cell { color: #fbbf24; }
    .field-label { color: #9ca3af; font-size: 0.85em; }
</style>
""", unsafe_allow_html=True)

st.title("Upgrade-Steckbrief Datenbank")
st.caption("Familie-Steckbriefe definieren den Mechanismus. Instanzen konkretisieren ihn baureihenspezifisch.")

tab_fam, tab_inst, tab_tree, tab_new = st.tabs(["Familien", "Instanzen", "Bucket-Baum", "Neu anlegen"])


def render_dimensions(dimensions):
    kind_colors = {"EC": "#3b82f6", "instance": "#10b981", "context": "#8b5cf6"}
    for dim in dimensions:
        color = kind_colors.get(dim.kind, "#6b7280")
        st.markdown(
            f'<div class="dim-card dim-{dim.kind}">'
            f'<b>{dim.name}</b> &nbsp;'
            f'<span style="color:{color};font-size:0.8em">[{dim.kind}]</span> &nbsp;'
            f'<span style="color:#6b7280;font-size:0.8em">{dim.unit} · {dim.resolution}</span><br>'
            f'<span style="font-size:0.85em;color:#d1d5db">{dim.description[:120]}</span>'
            f'</div>',
            unsafe_allow_html=True,
        )


def render_family(fam: UpgradeProfile):
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Komponente", f"{fam.component_cost_eur:,.0f} EUR")
    c2.metric("Installation", f"{fam.installation_cost_eur:,.0f} EUR")
    c3.metric("Gesamtinvestition", f"{fam.total_investment:,.0f} EUR")
    c4.metric("Konfidenz", f"{fam.confidence*100:.0f}%")

    st.markdown("---")
    left, right = st.columns(2)

    with left:
        st.markdown("**Block A · Identity**")
        st.write(f"ID: `{fam.id}` · Version: `{fam.version}` · Domain: `{fam.domain}`")
        st.write(f"Status: `{fam.status}`")
        st.write(fam.description.strip() if fam.description else "—")

        st.markdown("**Block B · Characteristics**")
        if fam.components:
            for c in fam.components:
                st.write(f"- {c}")
        if fam.performance_delta:
            for k, v in fam.performance_delta.items():
                st.write(f"- {k}: {v}")

        st.markdown("**Block D · Feasibility Gate**")
        gate = fam.feasibility_gate
        st.code(
            f"{gate.dimension_ref} {gate.operator} {gate.threshold} "
            f"({gate.unit if hasattr(gate,'unit') else ''})\n"
            f"# {gate.condition_description}",
            language="python",
        )

        st.markdown("**Block E · Savings Mechanism**")
        sm = fam.savings_mechanism
        if sm.node_type == "leaf":
            st.code(sm.mechanism.formula, language="python")
            for factor in sm.mechanism.factors:
                st.write(f"- `{factor.name}` ({factor.dimension_ref}): {factor.description}")
        else:
            st.code(bucket_display_formula(sm), language="python")
            st.caption("Bucket-Baum — Struktur bearbeitbar im Tab 'Bucket-Baum'.")

    with right:
        st.markdown("**Block F · Dependencies**")
        if fam.requires:
            st.write(f"Requires: {', '.join(f'`{r}`' for r in fam.requires)}")
        if fam.excludes:
            st.write(f"Excludes: {', '.join(f'`{e}`' for e in fam.excludes)}")
        for syn in fam.synergies:
            st.write(f"Synergy `{syn.with_upgrade}` [{syn.type}]: {syn.description[:80]}")

        st.markdown("**Block G · Data Dimensions**")
        render_dimensions(fam.data_dimensions)

        st.markdown("**Block H · Provenance**")
        st.write(f"Quelle: {fam.source} · Datum: {fam.date}")
        st.write(fam.notes.strip() if fam.notes else "—")


# ── Tab: Familien ─────────────────────────────────────────────────────────────
with tab_fam:
    families = load_families()
    if not families:
        st.info("Noch keine Familie-Profile. Lege das erste Upgrade unter 'Neu anlegen' an.")
    else:
        selected_fam = st.selectbox(
            "Familie auswaehlen",
            list(families.keys()),
            format_func=lambda k: f"{families[k].name} ({k})",
        )
        fam = families[selected_fam]
        render_family(fam)


# ── Tab: Instanzen ────────────────────────────────────────────────────────────
with tab_inst:
    families = load_families()
    instances_map = load_instances()

    if not instances_map:
        st.info("Noch keine Instanzen vorhanden.")
    else:
        selected_inst = st.selectbox(
            "Instanz auswaehlen",
            list(instances_map.keys()),
            format_func=lambda k: f"{instances_map[k][0].machine_model} — {k}",
        )
        inst, resolved = instances_map[selected_inst]
        fam = families.get(inst.family_id)

        c1, c2, c3 = st.columns(3)
        c1.metric("Gesamtinvestition", f"{resolved.total_investment:,.0f} EUR")
        c2.metric("Baureihe", inst.machine_model)
        c3.metric("Erbt von", inst.family_id)

        if inst.instance_values:
            st.markdown("**Instanz-Festwerte** (kind=instance Dimensionen)")
            iv_cols = st.columns(len(inst.instance_values))
            for col, (k, v) in zip(iv_cols, inst.instance_values.items()):
                col.metric(k, v)

        if fam:
            diffs = instance_diff(fam, inst)
            if diffs:
                st.markdown("**Abweichungen von der Familie**")
                for field, vals in diffs.items():
                    st.write(
                        f"- `{field}`: "
                        f"<span style='color:#6b7280'>{vals['family']}</span> → "
                        f"<span style='color:#fbbf24'><b>{vals['instance']}</b></span>",
                        unsafe_allow_html=True,
                    )

        with st.expander("Aufgeloestes Profil (Familie + Instanz-Overrides)"):
            render_family(resolved)

        if inst.notes:
            st.markdown("**Notizen**")
            st.write(inst.notes.strip())


# ── Tab: Bucket-Baum ──────────────────────────────────────────────────────────
with tab_tree:
    families = load_families()
    if not families:
        st.info("Noch keine Familie-Profile. Lege das erste Upgrade unter 'Neu anlegen' an.")
    else:
        selected_tree_fam = st.selectbox(
            "Familie auswaehlen",
            list(families.keys()),
            format_func=lambda k: f"{families[k].name} ({k})",
            key="tree_fam_select",
        )
        tree_fam = families[selected_tree_fam]

        st.markdown("**Struktur (read-only)**")
        render_bucket_tree_readonly(tree_fam.savings_mechanism)

        st.divider()
        render_bucket_assignment_form(tree_fam, key_prefix=f"tree_{selected_tree_fam}")


# ── Tab: Neu anlegen ─────────────────────────────────────────────────────────
with tab_new:
    st.subheader("Neues Upgrade anlegen")
    typ = st.radio("Typ", ["Familie (generischer Mechanismus)", "Instanz (baureihenspezifisch)"], horizontal=True)
    is_instance = typ.startswith("Instanz")

    with st.form("new_upgrade_form"):
        if is_instance:
            families = load_families()
            family_id = st.selectbox("Eltern-Familie", list(families.keys()))
            machine_model = st.text_input("Baureihe / Modell", placeholder="z.B. EZ26")
            inst_id = st.text_input("Instanz-ID", placeholder=f"{family_id}::EZ26")
            st.markdown("**Overrides** (JSON, Felder die von der Familie abweichen)")
            overrides_raw = st.text_area(
                "overrides",
                value='{\n  "component_cost_eur": 20000.0,\n  "installation_cost_eur": 1500.0\n}',
                height=120,
            )
            st.markdown("**instance_values** (Festwerte fuer kind=instance Dimensionen)")
            iv_raw = st.text_area(
                "instance_values",
                value='{\n  "fuel_rate_l_h": 3.5,\n  "engine_kw": 18.0\n}',
                height=100,
            )
            notes = st.text_area("Notizen", height=80)
            submitted = st.form_submit_button("Instanz speichern")

            if submitted:
                import json
                try:
                    overrides = json.loads(overrides_raw)
                    iv = json.loads(iv_raw)
                    new_inst = UpgradeInstance(
                        id=inst_id,
                        family_id=family_id,
                        machine_model=machine_model,
                        overrides=overrides,
                        instance_values=iv,
                        notes=notes,
                    )
                    saved = save_instance_yaml(new_inst)
                    st.success(f"Instanz gespeichert: `{saved}`")
                    st.rerun()
                except Exception as e:
                    st.error(f"Fehler: {e}")

        else:
            col1, col2 = st.columns(2)
            with col1:
                fam_id = st.text_input("ID", placeholder="U-HYD-REGEN")
                fam_name = st.text_input("Name", placeholder="Hydraulik-Rekuperation")
                domain = st.text_input("Domain", value="construction_machinery")
                version = st.text_input("Version", value="1.0")
                status = st.selectbox("Status", ["draft", "validated", "deprecated"])
                description = st.text_area("Beschreibung", height=80)
                components_raw = st.text_area(
                    "Komponenten (eine pro Zeile)",
                    placeholder="Hydraulikspeicher\nDruckventil",
                    height=80,
                )
            with col2:
                component_cost = st.number_input("Komponentenkosten EUR", value=20000.0, step=500.0)
                installation_cost = st.number_input("Installationskosten EUR", value=2000.0, step=200.0)
                disposal_cost = st.number_input("Entsorgungskosten EUR", value=500.0, step=100.0)

                st.markdown("**Feasibility Gate**")
                gate_desc = st.text_input("Gate-Beschreibung", placeholder="Betriebsstunden >= 500 h/Jahr")
                gate_dim = st.text_input("Dimension (dimension_ref)", placeholder="op_hours_year")
                gate_op = st.selectbox("Operator", [">=", "<=", ">", "<", "=="])
                gate_thresh = st.number_input("Schwellenwert", value=500.0)

                st.markdown("**Sparformel**")
                formula = st.text_input(
                    "Formel (Python-Ausdruck)",
                    placeholder="op_hours_year * regen_rate_l_h * diesel_price_eur_l",
                )
                formula_unit = st.text_input("Einheit", value="EUR/year")

                source = st.text_input("Quelle", placeholder="Konzeptentwurf / Literatur")
                confidence = st.slider("Konfidenz", 0.0, 1.0, 0.5, 0.05)
                date = st.text_input("Datum", value="2025-07-01")
                notes_fam = st.text_area("Notizen", height=60)

            submitted = st.form_submit_button("Familie speichern")

            if submitted:
                try:
                    new_fam = UpgradeProfile(
                        id=fam_id,
                        name=fam_name,
                        version=version,
                        domain=domain,
                        status=status,
                        description=description,
                        components=[c.strip() for c in components_raw.splitlines() if c.strip()],
                        component_cost_eur=component_cost,
                        installation_cost_eur=installation_cost,
                        disposal_cost_eur=disposal_cost,
                        feasibility_gate=FeasibilityGate(
                            condition_description=gate_desc,
                            dimension_ref=gate_dim,
                            threshold=gate_thresh,
                            operator=gate_op,
                        ),
                        savings_mechanism=BucketNode(
                            name="root",
                            node_type="leaf",
                            mechanism=SavingsMechanism(formula=formula, unit=formula_unit),
                        ),
                        source=source,
                        confidence=confidence,
                        date=date,
                        notes=notes_fam,
                    )
                    saved = save_profile_yaml(new_fam)
                    st.success(f"Familie gespeichert: `{saved}`")
                    st.rerun()
                except Exception as e:
                    st.error(f"Fehler: {e}")
