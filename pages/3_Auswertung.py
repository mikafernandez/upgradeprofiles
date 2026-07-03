from __future__ import annotations
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import json
import yaml
import streamlit as st
import pandas as pd
import plotly.graph_objects as go
import plotly.express as px
from schemas.upgrade_profile import load_instances
from mediator.werkstatt import GueteflagResult, auto_match_all
from mediator.integration import evaluate_upgrade, EvaluationResult
from utils.generate_sample import ensure_sample_exists

st.set_page_config(page_title="Auswertung", layout="wide")

st.markdown("""
<style>
    .result-feasible { background: #064e3b; border-left: 4px solid #10b981; }
    .result-infeasible { background: #7f1d1d; border-left: 4px solid #ef4444; }
    .result-package { background: #78350f; border-left: 4px solid #f59e0b; }
    .result-oos { background: #1f2937; border-left: 4px solid #6b7280; }
    .result-card {
        border-radius: 8px; padding: 0.8rem 1rem;
        margin-bottom: 0.5rem; font-size: 0.9em;
    }
</style>
""", unsafe_allow_html=True)

st.title("Auswertung — Flotten-Evaluation")
st.caption("Bewertet jede Maschine im Datensatz gegen den ausgewaehlten Upgrade-Steckbrief.")

ensure_sample_exists()

# ── Konfiguration ─────────────────────────────────────────────────────────────
st.subheader("1 · Konfiguration")

instances_map = load_instances()
if not instances_map:
    st.error("Keine Instanzen gefunden. Bitte zuerst Instanzen anlegen.")
    st.stop()

col1, col2 = st.columns(2)
with col1:
    inst_key = st.selectbox(
        "Upgrade-Instanz",
        list(instances_map.keys()),
        format_func=lambda k: f"{instances_map[k][0].machine_model} — {k}",
    )

inst, resolved = instances_map[inst_key]

# Load mapping if saved, else auto-match
mapping_path = Path(__file__).parent.parent / "data" / "mappings" / f"{inst_key.replace('::', '--')}.json"
saved_mapping: dict | None = None
if mapping_path.exists():
    saved_mapping = json.loads(mapping_path.read_text(encoding="utf-8"))

with col2:
    mapping_src = st.radio(
        "Werkstatt-Mapping",
        (
            ["Gespeichertes Mapping laden", "Auto-Mapping (Werkstatt-Defaults)"]
            if saved_mapping
            else ["Auto-Mapping (Werkstatt-Defaults)"]
        ),
        horizontal=True,
    )

# Load telemetry
sample_path = ensure_sample_exists()
data_opt = st.radio("Telemetrie-Quelle", ["Sample-Datensatz", "Eigene CSV"], horizontal=True)
if data_opt == "Sample-Datensatz":
    df = pd.read_csv(sample_path)
else:
    uploaded = st.file_uploader("CSV hochladen", type=["csv"])
    if not uploaded:
        st.stop()
    df = pd.read_csv(uploaded)

# Load market params
ctx_path = Path(__file__).parent.parent / "data" / "context" / "market_params.yaml"
context_params: dict = yaml.safe_load(ctx_path.read_text(encoding="utf-8")) if ctx_path.exists() else {}

# ── Marktparameter-Slider ─────────────────────────────────────────────────────
with st.expander("Marktparameter anpassen (Sensitivitaetsanalyse)"):
    s1, s2, s3 = st.columns(3)
    context_params["diesel_price_eur_l"] = s1.slider(
        "Dieselpreis EUR/L", 0.80, 2.50,
        float(context_params.get("diesel_price_eur_l", 1.60)), 0.05,
    )
    context_params["electricity_equiv_eur_l"] = s2.slider(
        "Strom-Aequivalent EUR/L", 0.20, 1.20,
        float(context_params.get("electricity_equiv_eur_l", 0.60)), 0.05,
    )
    context_params["co2_price_eur_t"] = s3.slider(
        "CO2-Preis EUR/t", 20.0, 150.0,
        float(context_params.get("co2_price_eur_t", 65.0)), 5.0,
    )

st.divider()

# ── Mapping aufloesen ─────────────────────────────────────────────────────────
ec_dims = [d for d in resolved.data_dimensions if d.kind == "EC"]

if saved_mapping and mapping_src.startswith("Gespeichertes"):
    werkstatt_mappings = {
        name: GueteflagResult(**val)
        for name, val in saved_mapping["mappings"].items()
    }
else:
    werkstatt_mappings = auto_match_all(ec_dims, list(df.columns))

# ── Evaluation ────────────────────────────────────────────────────────────────
st.subheader("2 · Flotten-Ergebnisse")

results: list[EvaluationResult] = evaluate_upgrade(
    resolved_profile=resolved,
    instance=inst,
    werkstatt_mappings=werkstatt_mappings,
    telemetry_df=df,
    context_params=context_params,
)

# Summary metrics
n_feasible = sum(1 for r in results if r.feasible)
n_package = sum(1 for r in results if r.package_candidate)
n_infeasible = sum(1 for r in results if not r.feasible and not r.package_candidate and not r.out_of_scope)
n_oos = sum(1 for r in results if r.out_of_scope)

sm1, sm2, sm3, sm4 = st.columns(4)
sm1.metric("Feasible", n_feasible, help="Gate erfuellt — Upgrade direkt empfehlenswert")
sm2.metric("Paket-Kandidat", n_package, help="Grenzfall — Paket mit U-MINIBATT pruefen")
sm3.metric("Nicht feasible", n_infeasible, help="Gate nicht erfuellt")
sm4.metric("Out of Scope", n_oos, help="Operationalisierung nicht moeglich")

st.divider()

# Results table
table_rows = []
for r in results:
    status = (
        "Feasible" if r.feasible else
        "Paket-Kandidat" if r.package_candidate else
        "out_of_scope" if r.out_of_scope else
        "Nicht feasible"
    )
    payback_str = f"{r.payback_years:.1f} a" if r.payback_years else "—"
    table_rows.append({
        "Maschine": r.machine_id,
        "Status": status,
        f"Gate ({resolved.feasibility_gate.dimension_ref}) [m/h]": f"{r.gate_dimension_value:.0f}",
        "Threshold [m/h]": f"{r.gate_threshold:.0f}",
        "Einsparung EUR/a": f"{r.savings_eur_year:,.0f}" if not r.out_of_scope else "—",
        "Payback": payback_str,
        "Konfidenz": f"{r.confidence*100:.0f}%",
    })

result_df = pd.DataFrame(table_rows)

def color_status(val):
    colors = {
        "Feasible": "color: #10b981; font-weight: bold",
        "Paket-Kandidat": "color: #f59e0b; font-weight: bold",
        "Nicht feasible": "color: #ef4444",
        "out_of_scope": "color: #6b7280",
    }
    return colors.get(val, "")

try:
    styled = result_df.style.map(color_status, subset=["Status"])
except AttributeError:
    styled = result_df.style.applymap(color_status, subset=["Status"])
st.dataframe(styled, use_container_width=True, hide_index=True)

st.divider()

# ── Charts ─────────────────────────────────────────────────────────────────────
st.subheader("3 · Visualisierung")

chart_tab1, chart_tab2 = st.tabs(["Payback-Periode", "Gate-Dimension"])

with chart_tab1:
    chart_data = [
        {
            "Maschine": r.machine_id,
            "Payback [Jahre]": r.payback_years or 0,
            "Status": (
                "Feasible" if r.feasible else
                "Paket-Kandidat" if r.package_candidate else
                "out_of_scope" if r.out_of_scope else
                "Nicht feasible"
            ),
        }
        for r in results if not r.out_of_scope
    ]
    if chart_data:
        cdf = pd.DataFrame(chart_data)
        color_map = {
            "Feasible": "#10b981",
            "Paket-Kandidat": "#f59e0b",
            "Nicht feasible": "#ef4444",
            "out_of_scope": "#6b7280",
        }
        fig = px.bar(
            cdf, x="Maschine", y="Payback [Jahre]", color="Status",
            color_discrete_map=color_map,
            template="plotly_dark",
            title=f"Payback-Periode — {resolved.name}",
        )
        fig.update_layout(
            paper_bgcolor="#0e1117", plot_bgcolor="#0e1117",
            yaxis_title="Payback [Jahre]",
        )
        st.plotly_chart(fig, use_container_width=True)

with chart_tab2:
    gate_data = [
        {
            "Maschine": r.machine_id,
            "Gate-Dimension [m/h]": r.gate_dimension_value,
            "Status": (
                "Feasible" if r.feasible else
                "Paket-Kandidat" if r.package_candidate else
                "Nicht feasible"
            ),
        }
        for r in results if not r.out_of_scope
    ]
    if gate_data:
        gdf = pd.DataFrame(gate_data)
        color_map_g = {
            "Feasible": "#10b981",
            "Paket-Kandidat": "#f59e0b",
            "Nicht feasible": "#ef4444",
        }
        fig2 = px.bar(
            gdf, x="Maschine", y="Gate-Dimension [m/h]", color="Status",
            color_discrete_map=color_map_g,
            template="plotly_dark",
            title=f"Aktionsradius je Maschine — Gate-Schwellenwert: {resolved.feasibility_gate.threshold:.0f} m/h",
        )
        # Gate threshold line
        fig2.add_hline(
            y=resolved.feasibility_gate.threshold,
            line_dash="dash", line_color="#4f8ef7",
            annotation_text=f"Gate: {resolved.feasibility_gate.threshold:.0f} m/h",
            annotation_position="right",
        )
        # Package threshold line (1.5x gate)
        fig2.add_hline(
            y=resolved.feasibility_gate.threshold * 1.5,
            line_dash="dot", line_color="#f59e0b",
            annotation_text=f"Paket-Grenze: {resolved.feasibility_gate.threshold*1.5:.0f} m/h",
            annotation_position="right",
        )
        fig2.update_layout(paper_bgcolor="#0e1117", plot_bgcolor="#0e1117")
        st.plotly_chart(fig2, use_container_width=True)

st.divider()

# ── Paket-Empfehlungen ─────────────────────────────────────────────────────────
if n_package > 0:
    st.subheader("4 · Paket-Empfehlung")
    st.info(
        f"**{n_package} Maschine(n) sind Grenzfall-Kandidaten** (Gate: {resolved.feasibility_gate.threshold:.0f} m/h, "
        f"Paket-Grenze: {resolved.feasibility_gate.threshold*1.5:.0f} m/h)."
    )

    # Check if synergy partner exists
    synergy_partners = [s.with_upgrade for s in resolved.synergies]
    if synergy_partners:
        st.write(f"Empfohlenes Paket: `{inst.family_id}` + `{synergy_partners[0]}`")
        st.write(
            "Durch Kombination mit einem Batterie-Extender (U-MINIBATT) wird der "
            "zulaeessige Aktionsradius auf ~350 m/h erweitert."
        )

    package_machines = [r for r in results if r.package_candidate]
    for r in package_machines:
        st.write(
            f"- **{r.machine_id}**: {r.gate_dimension_value:.0f} m/h — "
            f"liegt im Grenzfall-Bereich ({resolved.feasibility_gate.threshold:.0f}–"
            f"{resolved.feasibility_gate.threshold*1.5:.0f} m/h)"
        )

st.divider()

# ── Detail-Notizen ────────────────────────────────────────────────────────────
with st.expander("Detail-Notizen je Maschine"):
    for r in results:
        if r.notes:
            st.markdown(f"**{r.machine_id}**")
            for note in r.notes:
                st.write(f"  - {note}")

# ── Werkstatt-Mapping Uebersicht ──────────────────────────────────────────────
with st.expander("Werkstatt-Mapping (aktiv)"):
    from mediator.werkstatt import FLAG_COLOR, FLAG_LABEL
    for name, m in werkstatt_mappings.items():
        color = FLAG_COLOR.get(m.flag, "#6b7280")
        label = FLAG_LABEL.get(m.flag, m.flag)
        st.markdown(
            f"**{name}** "
            f'<span style="color:{color}; font-weight:600">{label}</span>  \n'
            f'Ist-Spalten: `{m.ist_columns}` · Formel: `{m.proxy_formula}`  \n'
            f'<span style="color:#9ca3af; font-size:0.82em">{m.note}</span>',
            unsafe_allow_html=True,
        )
