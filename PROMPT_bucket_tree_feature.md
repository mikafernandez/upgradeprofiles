# Prompt: MECE-Bucket-Baum mit fixem Aggregations-Vokabular in der Integration-Sektion

## Kontext

Dies ist ein Streamlit-Tool (`tool/`) zur Validierung eines Upgrade-Profil/Mediator-Konzepts (Masterarbeit, KIT wbk). Architektur: `schemas/upgrade_profile.py` (Pydantic-Modelle), `mediator/werkstatt.py` (Soll-Ist-Matching pro Dimension, Güte-Flag), `mediator/integration.py` (`evaluate_upgrade()`, rechnet Payback/Konfidenz), `pages/1_Upgrade_DB.py`, `pages/2_Werkstatt.py`, `pages/3_Auswertung.py` (Streamlit-Seiten).

**Lies vor jeder Änderung zuerst den kompletten aktuellen Code**, insbesondere `pages/1_Upgrade_DB.py`, `pages/2_Werkstatt.py` und `utils/generate_sample.py` (noch nicht inspiziert) zusätzlich zu den bereits bekannten Dateien, um Navigations- und Speicherkonventionen nicht zu brechen.

## Ist-Zustand (Stand heute)

- `UpgradeProfile.savings_mechanism` ist **ein einziges flaches Objekt**: eine Formel (String) + `inputs` (Dict von `{kind: EC|instance|context}`). Keine Baumstruktur, kein Konzept von "Buckets".
- `evaluate_upgrade()` in `mediator/integration.py` ist **binär**: Sobald irgendeine benötigte `EC`-Dimension in der Werkstatt nicht gematcht werden kann (Flag `RED`/`out_of_scope`), fällt die **gesamte** Maschine aus der Auswertung raus. Es gibt kein Konzept von "ein Teil des Ergebnisses fehlt, der Rest bleibt gültig".
- `mediator/werkstatt.py` matched pro einzelner Dimension (`PROXY_RULES`, `GueteflagResult` mit Flag `high/med/low/out_of_scope`), aber dieses Ergebnis wird nirgends in eine hierarchische Struktur eingehängt.
- `pages/3_Auswertung.py` zeigt Ergebnis-Tabelle, Payback-Chart, Paket-Empfehlung — aber keine Baum-Visualisierung, kein Editor für Struktur.

## Ziel-Feature

Ein **MECE-Baum** (Mutually Exclusive, Collectively Exhaustive) aus "Buckets" pro Upgrade-Profil, mit dem Einsparungen/Machbarkeit aus mehreren Teil-Mechanismen zusammengesetzt werden, statt aus einer einzigen Formel. Jeder Knoten hat entweder Kinder (mit einem Aggregationsoperator) oder ist ein Blatt (mit Formel + `inputs`, wie heute). Fehlende Daten an einem Blatt sollen **nicht die ganze Auswertung kippen**, sondern nur den betroffenen Ast als fehlend markieren — mit einem Flag, das bis in die finale Ausgabe sichtbar bleibt.

## Anforderungen

### 1. Schema-Erweiterung (`schemas/upgrade_profile.py`)

- Neuer Typ `BucketNode` (rekursiv): entweder Blatt (`formula`, `inputs`, wie bisheriges `SavingsMechanism`) oder Ast (`children: list[BucketNode]`, `operator: AggregationOperator`).
- `AggregationOperator` als **festes, geschlossenes Vokabular** (Enum), nicht frei erweiterbar:
  - `sum` — additive Kombination (z. B. Kostenanteile).
  - `and_gate` — boolesche Machbarkeit (alle Kinder müssen erfüllt sein; ein `missing`-Kind macht das Ergebnis `unknown`, nicht automatisch `false`).
  - `proportional_scale` — Skalierung über einen Abdeckungsgrad (`coverage_input`: welcher Anteil der Kinder/Daten bekannt ist), liefert einen anteiligen Wert statt eines Totalausfalls.
  - Begründung/Zitat im Code-Kommentar: Grabisch et al. (2009), *Aggregation Functions*, als Taxonomie-Anker für additive vs. boolesche vs. gewichtete Kombination — bitte diesen Kommentar so übernehmen, nicht umformulieren, das ist der Zitationsanker aus der Arbeit.
- **Migration/Abwärtskompatibilität**: ein bestehendes Profil mit einfachem `savings_mechanism.formula` muss weiterhin laden — intern als triviale Ein-Blatt-Baum-Instanz von `BucketNode` interpretiert werden. Keine bestehende YAML-Datei darf manuell nachbearbeitet werden müssen.

### 2. Fehlende-Daten-Regel (verbindlich, keine Ausnahmen)

- Wenn ein Blatt-Input (`kind: EC`) in der Werkstatt nicht matchbar ist (Flag `out_of_scope`): Blattwert wird **0 bzw. leer**, NIEMALS ein geschätzter/interpolierter Default. Das Blatt trägt ein `missing: true` + `reason`-Feld.
- Dieses Flag **propagiert nach oben durch die Aggregation**, es wird nicht lokal verschluckt:
  - unter `sum`: fehlendes Kind trägt 0 bei, Elternknoten erbt `missing_children: [...]`.
  - unter `and_gate`: Ergebnis wird `unknown` (nicht `false`), mit Verweis auf die fehlende Dimension.
  - unter `proportional_scale`: Wert wird mit dem tatsächlichen Abdeckungsgrad skaliert (z. B. 3 von 4 Unter-Werten bekannt → 75 %-Skalierung), plus Flag `partial_coverage`.
- Am Wurzelknoten muss das Endergebnis eine **lesbare Liste von Caveats** enthalten (z. B. `["Wartungseinsparung nicht eingepreist: Signal 'maintenance_interval' nicht verfügbar"]`), keine reine Zahl.

### 3. Integration-Logik (`mediator/integration.py`)

- `evaluate_upgrade()` von der heutigen binären Alles-oder-Nichts-Logik auf einen **rekursiven Baum-Evaluator** umbauen: jeder Knoten wird einzeln ausgewertet, Fehler/Fehlende-Daten propagieren als Flags statt als Abbruch der gesamten Maschine.
- Bestehende Spezialfälle (EZ17-Proxy in `_compute_hourly_distance()`, `_compute_op_hours_year()`, Paket-Kandidat-Logik für Grenzfall + U-MINIBATT) müssen als Blätter/Sonderfälle im neuen Baum weiterfunktionieren — nicht neu erfinden, nur einhängen.
- `EvaluationResult` um Felder für Caveats/fehlende Äste erweitern, ohne bestehende Felder zu brechen (falls nötig, mit sinnvollen Defaults).

### 4. UI: Baum-Ansicht + Zuordnung neuer Buckets (neue oder erweiterte Seite)

- Visualisierung des Baums (read-only): Knoten farblich nach Zustand (vollständig / teilweise fehlend / komplett fehlend), Kanten beschriftet mit dem Operator (z. B. „× Abdeckungsgrad" bei `proportional_scale`-Kanten).
- Editor-Funktion: neue, noch nicht zugeordnete Buckets („unassigned") einem Elternknoten zuweisen und dabei den Aggregationsoperator wählen.
- **Wichtiger Hinweis zur Umsetzung**: echtes Maus-Drag-and-Drop ist in Vanilla Streamlit nicht nativ verfügbar. Pragmatisch lösen über eine geeignete Zusatzbibliothek (z. B. `streamlit-agraph` für die Baum-Visualisierung, `streamlit-tree-select` oder ein einfaches Auswahlformular mit Parent-Select + Operator-Select für die Zuordnung) — funktional gleichwertig zu Drag-and-Drop, muss aber nicht pixelgenau Drag-and-Drop sein. Bitte diese Entscheidung selbst pragmatisch treffen und kurz begründen, nicht an echtem HTML5-Drag-and-Drop aufhalten.
- Änderungen persistieren in die YAML-Struktur des Profils (gleiche Speicherkonvention wie bestehende `save_profile_yaml()`).

### 5. Nicht-Ziele (bewusst außen vor)

- Keine automatische Bucket-Vorschlagslogik (ML/Heuristik, welche Struktur "sinnvoll" wäre) — Struktur wird vom Nutzer manuell festgelegt.
- Kein neuer Aggregationsoperator jenseits der drei genannten (`sum`, `and_gate`, `proportional_scale`) — das Vokabular ist bewusst fix und geschlossen, nicht frei erweiterbar durch Konfiguration.
- Choquet-Integral / nicht-additive Synergie-Aggregation ist NICHT Teil dieses Auftrags (offene Frage in der Arbeit, separat zu behandeln).

## Vorgehen

1. Erst die noch ungelesenen Dateien (`pages/1_Upgrade_DB.py`, `pages/2_Werkstatt.py`, `utils/generate_sample.py`, `test_smoke.py`) lesen.
2. Schema + Migration umsetzen, bestehende Profile testen (müssen weiterhin laden).
3. Integration-Logik umbauen, an mindestens einem bestehenden Profil (z. B. EZ17-Instanz) mit einer künstlich fehlenden Dimension durchspielen.
4. UI umsetzen.
5. `test_smoke.py` erweitern oder neuen Test ergänzen: ein Profil mit 2+ Buckets und einer bewusst fehlenden Dimension — Flag muss bis in `evaluate_upgrade()`-Output und bis in die UI-Anzeige durchgereicht werden.

## Akzeptanzkriterien

- Bestehende Profile (`U-ELEC-CABLE`, `U-ELEC-CABLE::EZ17`, `U-MINIBATT`) laufen unverändert weiter.
- Ein neues Test-Profil mit fehlender Dimension liefert ein Ergebnis (kein Absturz, kein stiller Default-Wert) und zeigt den Caveat-Text in der Auswertungs-Seite an.
- Der Aggregationsoperator-Enum hat exakt drei Werte, keine weiteren.
- Kanten-Beschriftung/Baum-Ansicht macht sichtbar, welcher Ast fehlt bzw. nur teilweise abgedeckt ist.
