from __future__ import annotations
import sys
import json
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import streamlit as st
import yaml
from schemas.upgrade_profile import (
    UpgradeProfile, UpgradeInstance, FeasibilityGate, DataDimension,
    load_families, load_instances, instance_diff, save_profile_yaml, save_instance_yaml,
    bucket_display_formula, build_kontenrahmen_skeleton,
)
from utils.bucket_tree_ui import render_bucket_tree_readonly, render_bucket_tree_graph, render_bucket_assignment_form

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

        st.markdown("**Struktur**")
        render_bucket_tree_graph(tree_fam.savings_mechanism)
        with st.expander("Textform (Debug)"):
            render_bucket_tree_readonly(tree_fam.savings_mechanism)

        st.divider()

        pending_demo = None
        pending = st.session_state.get("demo_pending_mechanisms")
        if pending and selected_tree_fam == st.session_state.get("demo_family_id"):
            next_mech = pending[0]
            st.info(
                f"**Demo-Modus** — {len(pending)} vordefinierte(r) Mechanismus/-men wartet auf Zuordnung. "
                f"Naechster: `{next_mech['leaf_name']}` — Formel/Faktoren sind vorausgefuellt, "
                f"vorgeschlagene Hauptklasse: `{next_mech['suggested_output_bucket']}`, "
                f"Modifikator-Hinweis: {next_mech['modifier_hint']}"
            )
            pending_demo = next_mech

        render_bucket_assignment_form(tree_fam, key_prefix=f"tree_{selected_tree_fam}", pending_demo=pending_demo)


# ── Tab: Neu anlegen ─────────────────────────────────────────────────────────
DEMO_FAMILY_DEFAULTS = {
    "new_fam_id": "U-DEMO-BETREUER",
    "new_fam_name": "Demo-Upgrade fuer Betreuer-Besprechung",
    "new_domain": "construction_machinery",
    "new_version": "1.0",
    "new_status": "draft",
    "new_description": (
        "Demo-Familie fuer die Live-Vorfuehrung des Kontenrahmens: zwei Sparmechanismen "
        "sind fertig formuliert, aber bewusst noch keinem output_bucket zugeordnet. "
        "Zahlen angelehnt an U-ELEC-CABLE::EZ17."
    ),
    "new_components": "Elektromotor (AC-Asynchron oder PMSM)\nUmrichter / Frequenzumformer",
    "new_component_cost": 15000.0,
    "new_installation_cost": 1200.0,
    "new_disposal_cost": 400.0,
    "new_gate_desc": "Mittlerer Aktionsradius der Maschine <= 200 m/h",
    "new_gate_dim": "hourly_distance",
    "new_gate_op": "<=",
    "new_gate_thresh": 200.0,
    "new_source": "Demo-Vorlage / Betreuer-Besprechung",
    "new_confidence": 0.6,
    "new_date": "2026-07-06",
    "new_notes_fam": (
        "Live-Demo: zwei Mechanismen ohne output_bucket, ein veto_scaled- und ein "
        "synergy_multiplier-Modifikator ohne Kantenzuordnung. Im Tab 'Bucket-Baum' live zuordnen."
    ),
    "new_data_dimensions_json": json.dumps([
        {"name": "hourly_distance", "kind": "EC", "unit": "m/h", "resolution": "L3",
         "description": "Mittlerer Aktionsradius, Proxy aus Fahrgeschwindigkeiten."},
        {"name": "op_hours_year", "kind": "EC", "unit": "h/year", "resolution": "L1",
         "description": "Jaehrliche Betriebsstunden."},
        {"name": "fuel_rate_l_h", "kind": "instance", "unit": "L/h", "resolution": "L1",
         "description": "Kraftstoffverbrauch, Instanz-Festwert."},
        {"name": "diesel_price_eur_l", "kind": "context", "unit": "EUR/L", "resolution": "L0",
         "description": "Dieselpreis, Marktparameter."},
        {"name": "electricity_equiv_eur_l", "kind": "context", "unit": "EUR/L", "resolution": "L0",
         "description": "Strom-Aequivalentpreis, Marktparameter."},
        {"name": "maintenance_interval_h", "kind": "EC", "unit": "h", "resolution": "L2",
         "description": "Wartungsintervall — bewusst kein Signal verfuegbar (Demo fuer missing-Caveat)."},
    ], indent=2, ensure_ascii=False),
}

DEMO_MECHANISMS = [
    {
        "leaf_name": "energie_einsparung",
        "formula": "op_hours_year * fuel_rate_l_h * (diesel_price_eur_l - electricity_equiv_eur_l)",
        "unit": "EUR/year",
        "factors": [
            {"name": "op_hours_year", "dimension_ref": "op_hours_year", "description": "Jaehrliche Betriebsstunden"},
            {"name": "fuel_rate_l_h", "dimension_ref": "fuel_rate_l_h", "description": "Kraftstoffverbrauch"},
            {"name": "diesel_price_eur_l", "dimension_ref": "diesel_price_eur_l", "description": "Dieselpreis"},
            {"name": "electricity_equiv_eur_l", "dimension_ref": "electricity_equiv_eur_l", "description": "Strom-Aequivalent"},
        ],
        "modifier_hint": "veto_scaled auf hourly_distance <= 200 (Machbarkeits-Abdeckung des Lastkollektivs)",
        "suggested_output_bucket": "cost.opex.energy",
    },
    {
        "leaf_name": "wartungs_einsparung",
        "formula": "maintenance_interval_h * 5.0",
        "unit": "EUR/year",
        "factors": [
            {"name": "maintenance_interval_h", "dimension_ref": "maintenance_interval_h",
             "description": "Wartungsintervall (bewusst nicht operationalisierbar)"},
        ],
        "modifier_hint": "synergy_multiplier x0.7 mit U-MINIBATT",
        "suggested_output_bucket": "cost.opex.maintenance",
    },
]

with tab_new:
    st.subheader("Neues Upgrade anlegen")
    typ = st.radio("Typ", ["Familie (generischer Mechanismus)", "Instanz (baureihenspezifisch)"], horizontal=True)
    is_instance = typ.startswith("Instanz")

    if not is_instance:
        if st.button("Demo-Vorlage laden (Betreuer-Besprechung)"):
            for k, v in DEMO_FAMILY_DEFAULTS.items():
                st.session_state[k] = v
            st.session_state["demo_pending_mechanisms"] = list(DEMO_MECHANISMS)
            st.session_state["demo_family_id"] = DEMO_FAMILY_DEFAULTS["new_fam_id"]
            st.rerun()

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
            for k, v in {
                "new_fam_id": "", "new_fam_name": "", "new_domain": "construction_machinery",
                "new_version": "1.0", "new_status": "draft", "new_description": "",
                "new_components": "", "new_component_cost": 20000.0, "new_installation_cost": 2000.0,
                "new_disposal_cost": 500.0, "new_gate_desc": "", "new_gate_dim": "",
                "new_gate_op": ">=", "new_gate_thresh": 500.0, "new_source": "",
                "new_confidence": 0.5, "new_date": "2025-07-01", "new_notes_fam": "",
                "new_data_dimensions_json": "[]",
            }.items():
                st.session_state.setdefault(k, v)

            col1, col2 = st.columns(2)
            with col1:
                fam_id = st.text_input("ID", key="new_fam_id", placeholder="U-HYD-REGEN")
                fam_name = st.text_input("Name", key="new_fam_name", placeholder="Hydraulik-Rekuperation")
                domain = st.text_input("Domain", key="new_domain")
                version = st.text_input("Version", key="new_version")
                status = st.selectbox("Status", ["draft", "validated", "deprecated"], key="new_status")
                description = st.text_area("Beschreibung", key="new_description", height=80)
                components_raw = st.text_area(
                    "Komponenten (eine pro Zeile)", key="new_components",
                    placeholder="Hydraulikspeicher\nDruckventil", height=80,
                )
                st.markdown("**Daten-Dimensionen**")
                data_dimensions_raw = st.text_area(
                    "JSON-Liste von {name, kind, unit, resolution, description} — kind: EC/instance/context",
                    key="new_data_dimensions_json", height=140,
                )
            with col2:
                component_cost = st.number_input("Komponentenkosten EUR", key="new_component_cost", step=500.0)
                installation_cost = st.number_input("Installationskosten EUR", key="new_installation_cost", step=200.0)
                disposal_cost = st.number_input("Entsorgungskosten EUR", key="new_disposal_cost", step=100.0)

                st.markdown("**Feasibility Gate**")
                gate_desc = st.text_input("Gate-Beschreibung", key="new_gate_desc", placeholder="Betriebsstunden >= 500 h/Jahr")
                gate_dim = st.text_input("Dimension (dimension_ref)", key="new_gate_dim", placeholder="op_hours_year")
                gate_op = st.selectbox("Operator", [">=", "<=", ">", "<", "=="], key="new_gate_op")
                gate_thresh = st.number_input("Schwellenwert", key="new_gate_thresh")

                st.caption(
                    "Sparmechanismus: Familien starten mit dem Kontenrahmen-Skeleton "
                    "(cost.capex/opex.*). Mechanismen werden im Tab 'Bucket-Baum' angelegt."
                )

                source = st.text_input("Quelle", key="new_source", placeholder="Konzeptentwurf / Literatur")
                confidence = st.slider("Konfidenz", 0.0, 1.0, step=0.05, key="new_confidence")
                date = st.text_input("Datum", key="new_date")
                notes_fam = st.text_area("Notizen", key="new_notes_fam", height=60)

            submitted = st.form_submit_button("Familie speichern")

            if submitted:
                try:
                    data_dimensions = [DataDimension(**d) for d in json.loads(data_dimensions_raw)]
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
                        savings_mechanism=build_kontenrahmen_skeleton(),
                        data_dimensions=data_dimensions,
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
