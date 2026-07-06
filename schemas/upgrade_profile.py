from __future__ import annotations
from enum import Enum
from typing import Any, Literal
from pathlib import Path
import yaml
from pydantic import BaseModel, ConfigDict, model_validator


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
    output_bucket: str = ""  # Kontenrahmen-Pfad (z.B. "cost.opex.energy"); "" = Entwurf, noch nicht zugeordnet


class Synergy(BaseModel):
    with_upgrade: str
    type: Literal["additive", "non_additive", "enabling"]
    description: str = ""


class AggregationOperator(str, Enum):
    # Grabisch et al. (2009), Aggregation Functions, als Taxonomie-Anker für additive
    # vs. gewichtete Kombination kompensatorischer Werte-Buckets
    SUM = "sum"
    PROPORTIONAL_SCALE = "proportional_scale"


class ModifierKind(str, Enum):
    VETO_HARD = "veto_hard"
    VETO_SCALED = "veto_scaled"
    SYNERGY_MULTIPLIER = "synergy_multiplier"


class EdgeModifier(BaseModel):
    """Modifikator auf der Kante von einem BucketNode zu seinem Elternknoten.
    Feiner als das globale FeasibilityGate: daempft gezielt einen Ast, statt die
    gesamte Auswertung zu blockieren. Beide Konzepte bestehen nebeneinander."""
    kind: ModifierKind
    gate: FeasibilityGate | None = None    # Bedingung fuer veto_hard/veto_scaled
    factor: float | None = None            # Multiplikator fuer synergy_multiplier (z.B. 0.7 = 30% Reduktion)
    with_upgrade: str | None = None        # Provenance bei Synergie: welches Upgrade sie ausloest
    description: str = ""

    @model_validator(mode="after")
    def _check_shape(self) -> "EdgeModifier":
        if self.kind in (ModifierKind.VETO_HARD, ModifierKind.VETO_SCALED) and self.gate is None:
            raise ValueError(f"EdgeModifier {self.kind.value} benoetigt gate")
        if self.kind == ModifierKind.SYNERGY_MULTIPLIER and self.factor is None:
            raise ValueError("EdgeModifier synergy_multiplier benoetigt factor")
        return self


# yaml.dump (Standard-Dumper, siehe save_profile_yaml) findet ohne diesen Representer keine
# passende Serialisierung fuer die Enum-Instanz und faellt auf ein !!python/object-Tag zurueck.
yaml.add_representer(AggregationOperator, lambda dumper, data: dumper.represent_str(data.value))
yaml.add_representer(ModifierKind, lambda dumper, data: dumper.represent_str(data.value))


class BucketNode(BaseModel):
    """
    MECE-Bucket-Baum: Blatt traegt eine SavingsMechanism-Formel (wie bisher),
    Ast kombiniert Kinder ueber einen AggregationOperator. Ein bestehendes
    flaches savings_mechanism (formula/factors/unit) wird beim Laden
    transparent als triviales Ein-Blatt interpretiert (siehe _wrap_legacy_shape).
    """
    name: str
    node_type: Literal["leaf", "branch"]
    mechanism: SavingsMechanism | None = None      # nur bei leaf
    operator: AggregationOperator | None = None    # nur bei branch
    children: list[BucketNode] = []                # nur bei branch
    edge_modifier: EdgeModifier | None = None      # Modifikator auf der Kante zum Elternknoten

    @model_validator(mode="before")
    @classmethod
    def _wrap_legacy_shape(cls, data: Any) -> Any:
        if isinstance(data, dict) and "node_type" not in data and "formula" in data:
            return {
                "name": data.get("name", "root"),
                "node_type": "leaf",
                "mechanism": {
                    "formula": data["formula"],
                    "factors": data.get("factors", []),
                    "unit": data.get("unit", "EUR/year"),
                },
            }
        return data

    @model_validator(mode="after")
    def _check_shape(self) -> "BucketNode":
        if self.node_type == "leaf" and (self.mechanism is None or self.children or self.operator is not None):
            raise ValueError("BucketNode leaf benoetigt mechanism, darf keine children/operator haben")
        if self.node_type == "branch" and (self.operator is None or self.mechanism is not None):
            raise ValueError("BucketNode branch benoetigt operator+children, darf kein mechanism haben")
        return self


BucketNode.model_rebuild()


def bucket_leaf_count(node: BucketNode) -> int:
    if node.node_type == "leaf":
        return 1
    return sum(bucket_leaf_count(c) for c in node.children)


def bucket_display_formula(node: BucketNode) -> str:
    """Kurzdarstellung fuer UI-Stellen, die frueher direkt savings_mechanism.formula gelesen haben."""
    if node.node_type == "leaf":
        return node.mechanism.formula
    return f"[Bucket-Baum, {bucket_leaf_count(node)} Blaetter, Operator {node.operator.value}]"


# Fixierter Wurzelschnitt fuer Block E (savings_mechanism), angelehnt an VDI 2884.
# Block C (component/installation/disposal_cost_eur) bleibt bewusst ein flaches Skalarfeld
# und wird NICHT in diesen Baum ueberfuehrt (siehe Auftragsdokument, Abschnitt 2.5).
KONTENRAHMEN_CAPEX = ["cost.capex.component", "cost.capex.installation_base", "cost.capex.disposal_prev"]
KONTENRAHMEN_OPEX = ["cost.opex.energy", "cost.opex.maintenance", "cost.opex.downtime"]


def build_kontenrahmen_skeleton() -> BucketNode:
    """Fixierter Wurzelschnitt: root -> cost -> {capex, opex} -> je drei leere Hauptklassen-Aeste."""
    def _empty_branch(name: str) -> BucketNode:
        return BucketNode(name=name, node_type="branch", operator=AggregationOperator.SUM, children=[])

    capex = BucketNode(
        name="capex", node_type="branch", operator=AggregationOperator.SUM,
        children=[_empty_branch(p.rsplit(".", 1)[-1]) for p in KONTENRAHMEN_CAPEX],
    )
    opex = BucketNode(
        name="opex", node_type="branch", operator=AggregationOperator.SUM,
        children=[_empty_branch(p.rsplit(".", 1)[-1]) for p in KONTENRAHMEN_OPEX],
    )
    cost = BucketNode(name="cost", node_type="branch", operator=AggregationOperator.SUM, children=[capex, opex])
    return BucketNode(name="root", node_type="branch", operator=AggregationOperator.SUM, children=[cost])


def list_output_bucket_options() -> list[str]:
    """Flache Liste aller fixen Hauptklassen-Pfade, fuer Dropdown in der UI."""
    return KONTENRAHMEN_CAPEX + KONTENRAHMEN_OPEX


def attach_mechanism_to_bucket(
    root: BucketNode, output_bucket: str, leaf_name: str,
    mechanism: SavingsMechanism, edge_modifier: EdgeModifier | None = None,
) -> BucketNode:
    """Haengt ein neues Blatt unter den durch output_bucket bezeichneten Ast.
    output_bucket darf ein fixer Pfad sein (z.B. 'cost.opex.energy') oder ein freier
    Unterpfad davon (z.B. 'cost.opex.energy.idle_share'); im zweiten Fall werden
    fehlende Zwischenaeste mit operator=sum automatisch angelegt."""
    segments = [s for s in output_bucket.split(".") if s]
    if not segments:
        raise ValueError("output_bucket darf nicht leer sein")

    def walk(node: BucketNode, remaining: list[str]) -> BucketNode:
        if not remaining:
            leaf = BucketNode(name=leaf_name, node_type="leaf", mechanism=mechanism, edge_modifier=edge_modifier)
            return node.model_copy(update={"children": list(node.children) + [leaf]})
        head, rest = remaining[0], remaining[1:]
        children = list(node.children)
        for i, child in enumerate(children):
            if child.name == head:
                children[i] = walk(child, rest)
                return node.model_copy(update={"children": children})
        new_branch = walk(
            BucketNode(name=head, node_type="branch", operator=AggregationOperator.SUM, children=[]), rest,
        )
        return node.model_copy(update={"children": children + [new_branch]})

    return walk(root, segments)


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
    savings_mechanism: BucketNode
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

    @model_validator(mode="after")
    def _check_root_edge_modifier(self) -> "UpgradeProfile":
        if self.savings_mechanism.edge_modifier is not None:
            raise ValueError("edge_modifier ist an der Wurzel von savings_mechanism nicht erlaubt")
        return self

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
