# Pipeline-Benchmarks

Jeder Aufruf der Gesamtpipeline oder der OSM-Vorverarbeitung erzeugt einen
eigenen Ordner `pipeline_metrics/<UTC-Zeit>_<Lauf-ID>` im Projekt.

- `steps.csv`: sequenzielle Schritte, UTC-Startzeit, Laufzeit in Sekunden,
  Wiederverwendungskennzeichen und Status. Zeitmessung mit `perf_counter_ns`;
  Nanosekundenauflösung bedeutet keine garantierte Nanosekundengenauigkeit.
- `regions.csv`: Einwohnerzahl (`population`, modellierter POP-Wert), ellipsoidische
  Fläche in km² und Zahl der Rasterzellen für PA, IA1 und IA2 getrennt.
  Quelle ist das endgültige Modell-4-Grid, keine Addition überlappender Ebenen.
  Bei reinen Vorverarbeitungsläufen fehlt diese Tabelle. Falls die Statistik
  nicht erstellt werden kann, enthält `run.json` den Grund unter
  `region_statistics_error`; fehlende Daten werden nicht als Null ausgegeben.
- `run.json`: Laufstatus, Gesamtdauer, Projektgröße zu Beginn und Höchststand,
  Eingabe-PBF-Größen, Aufrufparameter und Rechner-/Python-Daten.

## Speicherwerte auswerten

Alle Größen sind logische Dateigrößen in Bytes im Projektordner, ohne die
Benchmark-Protokolle selbst. `delta_bytes = end_bytes - start_bytes` kann negativ
sein. Anfangsgröße plus kumulierte Deltas ergibt den jeweiligen Endbestand,
aber **nicht** den maximalen Speicherbedarf. Dafür `cumulative_peak_bytes` bzw.
`peak_project_bytes` verwenden. `additional_peak_bytes` beschreibt den höchsten
beobachteten Mehrbedarf gegenüber dem Schritt- bzw. Laufbeginn. Die zusätzlichen
Höchststände verschiedener Schritte dürfen nicht summiert werden.

Eine Hintergrundmessung erfolgt einmal pro Sekunde zuzüglich Messungen an
Schrittgrenzen. Sehr kurzlebige Dateien können zwischen Messungen verschwinden;
die Höchststände sind daher beobachtete Untergrenzen. Große Verzeichnisbäume
verlängern das Messintervall. Externe Eingabedateien, temporäre Dateien außerhalb
des Projekts, RAM, Dateisystem-Metadaten und tatsächliche Blockbelegung sind nicht
enthalten. Die Zahlen sind keine Garantie für den gesamten freien Plattenplatz,
den ein QGIS-Lauf benötigt.

Die Zeiten beinhalten Orchestrierung und Messaufwand. QGIS-Modelle werden als
ganze Schritte erfasst, interne Processing-Algorithmen nicht einzeln.
Abgebrochene/fehlgeschlagene Aufrufe werden beim regulären Exception-Unwinding
abgeschlossen; bei Prozessabsturz oder Stromausfall können letzte Daten fehlen.
Für Vergleiche nur Läufe mit vergleichbaren Parametern, Rechnern und
Wiederverwendungsbedingungen heranziehen. Die Einwohnerzahlen stammen aus der
verwendeten Bevölkerungsgrundlage und sind modellierte Werte.
