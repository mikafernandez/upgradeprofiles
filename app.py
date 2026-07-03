from __future__ import annotations
import sys
from pathlib import Path

# make sibling packages importable from pages/
sys.path.insert(0, str(Path(__file__).parent))

import streamlit as st
from utils.generate_sample import ensure_sample_exists
from schemas.upgrade_profile import load_families, load_instances

st.set_page_config(
    page_title="Upgrade-Steckbrief Tool",
    page_icon="⚡",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown("""
<style>
    [data-testid="stSidebar"] { background: #111827; }
    .stMetric { background: #1f2937; border-radius: 8px; padding: 0.5rem 1rem; }
    .block-container { padding-top: 2rem; }
    .layer-card {
        background: #1f2937;
        border: 1px solid #374151;
        border-radius: 10px;
        padding: 1.2rem 1.4rem;
        height: 100%;
    }
    .layer-arrow {
        display: flex;
        align-items: center;
        justify-content: center;
        font-size: 2rem;
        color: #4f8ef7;
        padding-top: 1.5rem;
    }
    .badge {
        display: inline-block;
        padding: 2px 10px;
        border-radius: 12px;
        font-size: 0.78em;
        font-weight: 600;
    }
    .badge-draft { background: #92400e; color: #fde68a; }
    .badge-validated { background: #065f46; color: #6ee7b7; }
    .badge-deprecated { background: #374151; color: #9ca3af; }
</style>
""", unsafe_allow_html=True)

# Ensure sample telemetry exists
ensure_sample_exists()

# ── Header ────────────────────────────────────────────────────────────────────
st.title("Upgrade-Steckbrief Validation Tool")
st.caption("Masterarbeit · KIT Institut fuer Produktionstechnik (wbk) · Midlife Upgrade Framework")
st.divider()

# ── Stats ─────────────────────────────────────────────────────────────────────
families = load_families()
instances_map = load_instances()
mapping_dir = Path(__file__).parent / "data" / "mappings"
n_mappings = len(list(mapping_dir.glob("*.json"))) if mapping_dir.exists() else 0

col_a, col_b, col_c, col_d = st.columns(4)
col_a.metric("Upgrade-Familien", len(families))
col_b.metric("Instanzen", len(instances_map))
col_c.metric("Gespeicherte Mappings", n_mappings)
col_d.metric("Daten-Dimensionstypen", 3, help="EC · instance · context")

st.divider()

# ── Architecture ──────────────────────────────────────────────────────────────
st.subheader("Framework-Architektur")

col1, col_arr1, col2, col_arr2, col3 = st.columns([3, 0.3, 3, 0.3, 3])

with col1:
    st.markdown("""
<div class="layer-card">
<strong style="color:#4f8ef7; font-size:1.05em">Profilschicht</strong><br><br>
<b>Familie-Steckbrief</b><br>
Generischer Mechanismus · Platzhalter-Werte<br><br>
<b>Instanz-Steckbrief</b><br>
Erbt von Familie · Konkrete Baureihe<br>
Ueberschreibt abweichende Felder<br><br>
<span style="color:#6b7280; font-size:0.85em">→ Seite: Upgrade DB</span>
</div>
""", unsafe_allow_html=True)

with col_arr1:
    st.markdown('<div class="layer-arrow">→</div>', unsafe_allow_html=True)

with col2:
    st.markdown("""
<div class="layer-card">
<strong style="color:#4f8ef7; font-size:1.05em">Mediatorschicht</strong><br><br>
<b>Werkstatt</b><br>
Soll-Dimensionen ↔ Ist-Spalten<br>
Proxy-Bildung · Guete-Flag<br><br>
<b>Integration</b><br>
Marktparameter · Auswertungs-Pipeline<br>
Konfidenz-Aggregation<br><br>
<span style="color:#6b7280; font-size:0.85em">→ Seite: Werkstatt</span>
</div>
""", unsafe_allow_html=True)

with col_arr2:
    st.markdown('<div class="layer-arrow">→</div>', unsafe_allow_html=True)

with col3:
    st.markdown("""
<div class="layer-card">
<strong style="color:#4f8ef7; font-size:1.05em">Consumer-Schicht</strong><br><br>
<b>Auswertung</b><br>
Feasibility · TCO · Payback<br>
Flotten-Uebersicht<br><br>
<b>Paket-Empfehlung</b><br>
Grenzfall-Maschinen identifizieren<br>
Kombinations-Upgrade vorschlagen<br><br>
<span style="color:#6b7280; font-size:0.85em">→ Seite: Auswertung</span>
</div>
""", unsafe_allow_html=True)

st.divider()

# ── Quick Overview ─────────────────────────────────────────────────────────────
st.subheader("Verfuegbare Upgrade-Familien")

if not families:
    st.info("Keine Familie-Profile gefunden. Lege das erste Upgrade an (Seite: Upgrade DB).")
else:
    for fid, fam in families.items():
        badge_class = f"badge badge-{fam.status}"
        instanzen = [iid for iid, (inst, _) in instances_map.items() if inst.family_id == fid]
        with st.expander(f"**{fam.name}** `{fam.id}`  —  {len(instanzen)} Instanz(en)"):
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("Komponente", f"{fam.component_cost_eur:,.0f} EUR")
            c2.metric("Gesamtinvestition", f"{fam.total_investment:,.0f} EUR")
            c3.metric("Konfidenz", f"{fam.confidence*100:.0f}%")
            c4.metric("Dimensionen (EC)", sum(1 for d in fam.data_dimensions if d.kind == "EC"))

            st.write(f"**Status:** <span class='{badge_class}'>{fam.status}</span>", unsafe_allow_html=True)
            st.write(f"**Feasibility Gate:** {fam.feasibility_gate.condition_description}")
            st.write(f"**Sparformel:** `{fam.savings_mechanism.formula}`")

            if instanzen:
                st.write(f"**Instanzen:** {', '.join(f'`{i}`' for i in instanzen)}")

st.divider()
st.caption(
    "Datenstrukturen aendern: `data/upgrades/families/*.yaml` · `schemas/upgrade_profile.py` · "
    "Mediator-Logik: `mediator/werkstatt.py` · `mediator/integration.py`"
)
