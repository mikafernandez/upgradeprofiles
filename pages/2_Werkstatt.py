from __future__ import annotations
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import json
import streamlit as st
import pandas as pd
from schemas.upgrade_profile import load_families, load_instances
from mediator.werkstatt import (
    auto_match_all, match_dimension, GueteflagResult,
    FLAG_COLOR, FLAG_LABEL, FLAG_CONFIDENCE,
)
from utils.generate_sample import ensure_sample_exists

st.set_page_config(page_title="Werkstatt", layout="wide")

st.markdown("""
<style>
    .guete-badge {
        display: inline-block; padding: 3px 12px;
        border-radius: 12px; font-size: 0.8em; font-weight: 600;
    }
    .dim-row {
        background: #1f2937; border-radius: 8px;
        padding: 0.8rem 1rem; margin-bottom: 0.6rem;
    }
</style>
""", unsafe_allow_html=True)

st.title("Werkstatt — Operationalisierung")
st.caption(
    "Verbindet abstrakte Soll-Dimensionen mit verfuegbaren Telemetrie-Spalten. "
    "Bewertet die Guete der Abbildung (GREEN / AMBER / RED)."
)

ensure_sample_exists()

# ── Step 1: Upgrade auswaehlen ────────────────────────────────────────────────
st.subheader("1 · Upgrade-Instanz auswaehlen")

families = load_families()
instances_map = load_instances()

col1, col2 = st.columns([2, 1])
with col1:
    if not instances_map:
        st.warning("Keine Instanzen gefunden. Lege zuerst eine Instanz unter 'Upgrade DB' an.")
        st.stop()
    inst_key = st.selectbox(
        "Instanz",
        list(instances_map.keys()),
        format_func=lambda k: f"{instances_map[k][0].machine_model} — {k}",
    )

inst, resolved = instances_map[inst_key]

with col2:
    st.metric("Feasibility Gate", resolved.feasibility_gate.condition_description)

ec_dims = [d for d in resolved.data_dimensions if d.kind == "EC"]
inst_dims = [d for d in resolved.data_dimensions if d.kind == "instance"]
ctx_dims = [d for d in resolved.data_dimensions if d.kind == "context"]

st.divider()

# ── Step 2: Telemetrie laden ───────────────────────────────────────────────────
st.subheader("2 · Telemetrie-Datensatz")

data_source = st.radio("Quelle", ["Sample-Datensatz (fleet_telemetry.csv)", "Eigene CSV hochladen"], horizontal=True)

df: pd.DataFrame | None = None

if data_source.startswith("Sample"):
    sample_path = ensure_sample_exists()
    df = pd.read_csv(sample_path)
    st.success(f"Sample geladen: {len(df)} Zeilen · {df['machine_id'].nunique()} Maschinen")
    with st.expander("Vorschau (erste 10 Zeilen)"):
        st.dataframe(df.head(10), use_container_width=True)
else:
    uploaded = st.file_uploader("CSV hochladen", type=["csv"])
    if uploaded:
        df = pd.read_csv(uploaded)
        st.success(f"Geladen: {len(df)} Zeilen")
        with st.expander("Vorschau"):
            st.dataframe(df.head(10), use_container_width=True)

if df is None:
    st.info("Bitte einen Datensatz auswaehlen oder hochladen.")
    st.stop()

available_cols = list(df.columns)
st.divider()

# ── Step 3: Soll → Ist Mapping ────────────────────────────────────────────────
st.subheader("3 · Dimension-Mapping")

col_info1, col_info2, col_info3 = st.columns(3)
col_info1.markdown(
    "<span style='color:#3b82f6'>■</span> **EC** = aus Telemetrie (Soll→Ist mapping erforderlich)",
    unsafe_allow_html=True,
)
col_info2.markdown(
    "<span style='color:#10b981'>■</span> **instance** = Festwert aus Instanz-Profil (automatisch)",
    unsafe_allow_html=True,
)
col_info3.markdown(
    "<span style='color:#8b5cf6'>■</span> **context** = Marktparameter (automatisch aus market_params.yaml)",
    unsafe_allow_html=True,
)

# Auto-match alle EC-Dimensionen
auto_results = auto_match_all(ec_dims, available_cols)

if "werkstatt_overrides" not in st.session_state:
    st.session_state.werkstatt_overrides = {}

mappings: dict[str, GueteflagResult] = {}

st.markdown("#### EC-Dimensionen (Nutzungsdaten)")

for dim in ec_dims:
    auto = auto_results.get(dim.name)
    override_key = f"mapping_{dim.name}"

    with st.container():
        st.markdown(f'<div class="dim-row">', unsafe_allow_html=True)

        row_c1, row_c2, row_c3 = st.columns([2, 3, 2])

        with row_c1:
            flag_color = FLAG_COLOR.get(auto.flag if auto else "RED", "#e74c3c")
            flag_label = FLAG_LABEL.get(auto.flag if auto else "RED", "—")
            st.markdown(
                f"**{dim.name}**  \n"
                f"<span style='color:#6b7280; font-size:0.82em'>{dim.unit} · {dim.resolution}</span>",
                unsafe_allow_html=True,
            )
            st.markdown(
                f'<span class="guete-badge" style="background:{flag_color}22; color:{flag_color}; '
                f'border: 1px solid {flag_color}55">{flag_label}</span>',
                unsafe_allow_html=True,
            )

        with row_c2:
            # Allow user to override column selection
            default_cols = auto.ist_columns if auto and auto.ist_columns else []
            chosen_cols = st.multiselect(
                f"Ist-Spalte(n) fuer `{dim.name}`",
                available_cols,
                default=default_cols,
                key=f"cols_{dim.name}",
                label_visibility="collapsed",
            )
            custom_formula = st.text_input(
                "Proxy-Formel (optional)",
                value=auto.proxy_formula if auto else "",
                key=f"formula_{dim.name}",
                label_visibility="collapsed",
                placeholder="z.B. (track_speed_left_ms + track_speed_right_ms) / 2 * 3600",
            )

        with row_c3:
            if auto:
                st.markdown(
                    f"<span style='font-size:0.8em; color:#9ca3af'>{auto.note[:120]}</span>",
                    unsafe_allow_html=True,
                )

        # Recompute flag based on actual column selection
        if chosen_cols:
            if auto and set(chosen_cols) == set(auto.ist_columns):
                result = auto
            else:
                # User chose custom columns — GREEN if direct match, otherwise AMBER
                is_direct = len(chosen_cols) == 1 and chosen_cols[0] == dim.name
                result = GueteflagResult(
                    soll_name=dim.name,
                    ist_columns=chosen_cols,
                    proxy_formula=custom_formula or chosen_cols[0],
                    flag="GREEN" if is_direct else "AMBER",
                    confidence=FLAG_CONFIDENCE["GREEN"] if is_direct else FLAG_CONFIDENCE["AMBER"],
                    note="Manuell zugeordnet." if not is_direct else "Direktmatch.",
                )
        else:
            result = GueteflagResult(
                soll_name=dim.name,
                ist_columns=[],
                proxy_formula="—",
                flag="RED",
                confidence=0.0,
                note="Keine Spalte ausgewaehlt.",
            )

        mappings[dim.name] = result
        st.markdown("</div>", unsafe_allow_html=True)

# Preview computed dimension values
if mappings and len(df) > 0:
    st.divider()
    st.markdown("#### Berechnete Dimensionswerte (Vorschau, Sample-Maschine)")

    sample_machine = df["machine_id"].iloc[0] if "machine_id" in df.columns else None
    preview_df = df[df["machine_id"] == sample_machine].copy() if sample_machine else df.copy()

    for dim_name, res in mappings.items():
        if res.flag != "RED" and res.ist_columns:
            try:
                if dim_name == "hourly_distance":
                    val = ((preview_df["track_speed_left_ms"] + preview_df["track_speed_right_ms"]) / 2 * 3600).mean()
                    st.metric(f"`{dim_name}` (Mittelwert)", f"{val:.1f} m/h", help=res.proxy_formula)
                elif dim_name == "op_hours_year":
                    total = preview_df["op_hours_daily"].sum()
                    annualized = total / len(preview_df) * 365
                    st.metric(f"`{dim_name}` (annualisiert)", f"{annualized:.0f} h/Jahr", help=res.proxy_formula)
            except Exception:
                pass

# instance und context Dimensionen — info only
if inst_dims or ctx_dims:
    with st.expander("instance & context Dimensionen (automatisch aufgeloest)"):
        if inst_dims:
            st.markdown("**kind=instance** — Festwerte aus Instanz-Profil")
            for dim in inst_dims:
                val = inst.instance_values.get(dim.name, "N/A")
                st.write(f"- `{dim.name}`: **{val}** {dim.unit}")
        if ctx_dims:
            st.markdown("**kind=context** — Marktparameter (market_params.yaml)")
            from pathlib import Path
            import yaml as _yaml
            ctx_path = Path(__file__).parent.parent / "data" / "context" / "market_params.yaml"
            if ctx_path.exists():
                ctx = _yaml.safe_load(ctx_path.read_text(encoding="utf-8"))
                for dim in ctx_dims:
                    val = ctx.get(dim.name, "N/A")
                    st.write(f"- `{dim.name}`: **{val}** {dim.unit}")

st.divider()

# ── Step 4: Konfidenz-Uebersicht + Speichern ───────────────────────────────────
st.subheader("4 · Konfidenz-Uebersicht")

all_flags = [r.flag for r in mappings.values()]
conf_total = sum(FLAG_CONFIDENCE[f] for f in all_flags) / len(all_flags) if all_flags else 0.0

c1, c2, c3 = st.columns(3)
c1.metric("Gesamt-Konfidenz", f"{conf_total*100:.0f}%")
c2.metric("GREEN", all_flags.count("GREEN"))
c3.metric("AMBER / RED", all_flags.count("AMBER") + all_flags.count("RED"))

if all_flags.count("RED") > 0:
    st.error("Mindestens eine Dimension ist RED — Auswertung als out_of_scope markiert.")
elif all_flags.count("AMBER") > 0:
    st.warning("Proxy-Abbildungen vorhanden (AMBER). Konfidenz in Auswertung beruecksichtigt.")
else:
    st.success("Alle Dimensionen direkt abgebildet (GREEN).")

save_col, _ = st.columns([1, 3])
with save_col:
    if st.button("Mapping speichern", type="primary"):
        mapping_data = {
            "upgrade_id": inst_key,
            "family_id": inst.family_id,
            "machine_model": inst.machine_model,
            "overall_confidence": conf_total,
            "mappings": {
                name: res.model_dump() for name, res in mappings.items()
            },
        }
        save_path = Path(__file__).parent.parent / "data" / "mappings" / f"{inst_key.replace('::', '--')}.json"
        save_path.parent.mkdir(parents=True, exist_ok=True)
        save_path.write_text(json.dumps(mapping_data, indent=2, ensure_ascii=False), encoding="utf-8")
        st.success(f"Mapping gespeichert: `{save_path.name}`  →  Weiter zur Auswertung")
