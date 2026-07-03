from __future__ import annotations
from typing import Literal
from pydantic import BaseModel
from schemas.upgrade_profile import DataDimension


class GueteflagResult(BaseModel):
    soll_name: str
    ist_columns: list[str]
    proxy_formula: str
    flag: Literal["GREEN", "AMBER", "RED"]
    confidence: float
    note: str


FLAG_COLOR = {"GREEN": "#2ecc71", "AMBER": "#e67e22", "RED": "#e74c3c"}
FLAG_LABEL = {
    "GREEN": "Direktmatch",
    "AMBER": "Proxy (Konfidenz reduziert)",
    "RED": "Nicht operationalisierbar",
}
FLAG_CONFIDENCE = {"GREEN": 1.0, "AMBER": 0.70, "RED": 0.0}

# Hartcodierte Proxy-Regeln fuer bekannte Soll-Dimensionen.
# Neue Dimensionen hier ergaenzen -> sofortige Wirkung auf Auto-Match.
PROXY_RULES: dict[str, dict] = {
    "hourly_distance": {
        "required_columns": ["track_speed_left_ms", "track_speed_right_ms"],
        "proxy_formula": "(track_speed_left_ms + track_speed_right_ms) / 2 * 3600",
        "flag": "AMBER",
        "note": (
            "Proxy via Raupengeschwindigkeiten: Mittlere Fahrgeschwindigkeit x 3600 s/h. "
            "Keine Kurvenkorrektur moeglich (kein Lenkwinkel); Vorwaerts/Rueckwaerts "
            "werden zusammengezaehlt (ueberschaetzt Radius bei reversierenden Maschinen). "
            "Systematischer Fehler geschaetzt 15-25%. Aufloesung von L3 auf L2 degradiert."
        ),
    },
    "op_hours_year": {
        "required_columns": ["op_hours_daily"],
        "proxy_formula": "op_hours_daily * (365 / n_days)",
        "flag": "GREEN",
        "note": "Direktmatch: Tagessumme hochgerechnet auf Jahreswert.",
    },
    "fuel_rate_l_h": {
        "required_columns": ["fuel_consumed_l", "op_hours_daily"],
        "proxy_formula": "fuel_consumed_l / op_hours_daily",
        "flag": "AMBER",
        "note": (
            "Proxy via Tagesverbrauch / Betriebsstunden. Achtung: umfasst auch Leerlauf. "
            "Fuer instance-Dimensionen Festwert aus Instanz-Profil bevorzugen."
        ),
    },
}


def match_dimension(soll: DataDimension, available_columns: list[str]) -> GueteflagResult:
    if soll.name in available_columns:
        return GueteflagResult(
            soll_name=soll.name,
            ist_columns=[soll.name],
            proxy_formula=soll.name,
            flag="GREEN",
            confidence=FLAG_CONFIDENCE["GREEN"],
            note="Direktmatch: Spaltenname stimmt exakt ueberein.",
        )

    if soll.name in PROXY_RULES:
        rule = PROXY_RULES[soll.name]
        required = rule["required_columns"]
        if all(col in available_columns for col in required):
            return GueteflagResult(
                soll_name=soll.name,
                ist_columns=required,
                proxy_formula=rule["proxy_formula"],
                flag=rule["flag"],
                confidence=FLAG_CONFIDENCE[rule["flag"]],
                note=rule["note"],
            )

    return GueteflagResult(
        soll_name=soll.name,
        ist_columns=[],
        proxy_formula="—",
        flag="RED",
        confidence=0.0,
        note=(
            f"Keine passende Spalte fuer '{soll.name}' gefunden. "
            "Upgrade wird als out_of_scope markiert."
        ),
    )


def auto_match_all(
    dimensions: list[DataDimension], available_columns: list[str]
) -> dict[str, GueteflagResult]:
    return {
        dim.name: match_dimension(dim, available_columns)
        for dim in dimensions
        if dim.kind == "EC"
    }


def overall_confidence(mappings: dict[str, GueteflagResult]) -> float:
    if not mappings:
        return 0.0
    vals = [m.confidence for m in mappings.values()]
    return sum(vals) / len(vals)
