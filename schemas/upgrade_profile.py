from __future__ import annotations
from typing import Any, Literal
from pathlib import Path
import yaml
from pydantic import BaseModel, ConfigDict


class DataDimension(BaseModel):
    name: str
    kind: Literal["EC", "instance", "context"]
    unit: str
    resolution: Literal["L0", "L1", "L2", "L3"]
    description: str = ""


class FeasibilityGate(BaseModel):
    condition_description: str
    dimension_ref: str
    threshold: float
    operator: Literal["<=", ">=", "<", ">", "=="]


class Factor(BaseModel):
    name: str
    dimension_ref: str
    description: str = ""


class SavingsMechanism(BaseModel):
    formula: str
    factors: list[Factor] = []
    unit: str = "EUR/year"


class Synergy(BaseModel):
    with_upgrade: str
    type: Literal["additive", "non_additive", "enabling"]
    description: str = ""


class UpgradeProfile(BaseModel):
    """
    Familie-Profil: Generische, baureihenunabhaengige Beschreibung eines Upgrade-Mechanismus.
    Numerische Werte sind Platzhalter oder Richtwerte. Instanzen ueberschreiben mit echten Daten.
    """
    model_config = ConfigDict(extra="allow")

    # A: Identity
    id: str
    name: str
    version: str = "1.0"
    domain: str = "construction_machinery"
    status: Literal["draft", "validated", "deprecated"] = "draft"
    description: str = ""
    # B: Characteristics
    components: list[str] = []
    interfaces: list[str] = []
    performance_delta: dict[str, str] = {}
    # C: Costs
    component_cost_eur: float = 0.0
    installation_cost_eur: float = 0.0
    disposal_cost_eur: float = 0.0
    # D: Feasibility Gate
    feasibility_gate: FeasibilityGate
    # E: Savings Mechanism
    savings_mechanism: SavingsMechanism
    # F: Dependencies
    requires: list[str] = []
    excludes: list[str] = []
    synergies: list[Synergy] = []
    # G: Data Binding
    data_dimensions: list[DataDimension] = []
    # H: Provenance
    source: str = ""
    confidence: float = 0.5
    date: str = ""
    notes: str = ""

    @property
    def total_investment(self) -> float:
        return self.component_cost_eur + self.installation_cost_eur

    @property
    def status_color(self) -> str:
        return {"draft": "#f39c12", "validated": "#2ecc71", "deprecated": "#7f8c8d"}.get(self.status, "#ccc")


class UpgradeInstance(BaseModel):
    """
    Instanz-Profil: Baureihenspezifische Konkretisierung einer Familie.
    Erbt alle Felder der Familie, ueberschreibt nur abweichende Werte via overrides.
    instance_values liefert Festwerte fuer DataDimensions mit kind='instance'.
    """
    model_config = ConfigDict(extra="allow")

    id: str
    family_id: str
    machine_model: str
    overrides: dict[str, Any] = {}
    instance_values: dict[str, float] = {}
    notes: str = ""


def resolve_instance(family: UpgradeProfile, instance: UpgradeInstance) -> UpgradeProfile:
    """
    Merged Familie mit Instanz-Overrides. Instanzwerte haben Vorrang.
    Gibt ein vollstaendig aufgeloestes UpgradeProfile zurueck.
    """
    merged = family.model_dump()
    merged.update(instance.overrides)
    return UpgradeProfile(**merged)


def _base_path() -> Path:
    return Path(__file__).parent.parent


def load_families(base_path: Path | None = None) -> dict[str, UpgradeProfile]:
    path = (base_path or _base_path()) / "data" / "upgrades" / "families"
    families: dict[str, UpgradeProfile] = {}
    if not path.exists():
        return families
    for f in sorted(path.glob("*.yaml")):
        data = yaml.safe_load(f.read_text(encoding="utf-8"))
        profile = UpgradeProfile(**data)
        families[profile.id] = profile
    return families


def load_instances(base_path: Path | None = None) -> dict[str, tuple[UpgradeInstance, UpgradeProfile]]:
    families = load_families(base_path)
    path = (base_path or _base_path()) / "data" / "upgrades" / "instances"
    instances: dict[str, tuple[UpgradeInstance, UpgradeProfile]] = {}
    if not path.exists():
        return instances
    for f in sorted(path.glob("*.yaml")):
        data = yaml.safe_load(f.read_text(encoding="utf-8"))
        inst = UpgradeInstance(**data)
        if inst.family_id in families:
            resolved = resolve_instance(families[inst.family_id], inst)
            instances[inst.id] = (inst, resolved)
    return instances


def instance_diff(family: UpgradeProfile, instance: UpgradeInstance) -> dict[str, dict]:
    """Returns fields that differ between family and resolved instance."""
    resolved = resolve_instance(family, instance)
    family_flat = {
        k: v for k, v in family.model_dump().items()
        if isinstance(v, (str, int, float, bool)) or v is None
    }
    resolved_flat = {
        k: v for k, v in resolved.model_dump().items()
        if isinstance(v, (str, int, float, bool)) or v is None
    }
    diffs = {}
    for k in instance.overrides:
        if k in family_flat and k in resolved_flat:
            diffs[k] = {"family": family_flat[k], "instance": resolved_flat[k]}
    return diffs


def save_profile_yaml(profile: UpgradeProfile, base_path: Path | None = None) -> Path:
    path = (base_path or _base_path()) / "data" / "upgrades" / "families" / f"{profile.id}.yaml"
    data = profile.model_dump()
    path.write_text(yaml.dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return path


def save_instance_yaml(instance: UpgradeInstance, base_path: Path | None = None) -> Path:
    safe_id = instance.id.replace("::", "--")
    path = (base_path or _base_path()) / "data" / "upgrades" / "instances" / f"{safe_id}.yaml"
    data = instance.model_dump()
    path.write_text(yaml.dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return path
