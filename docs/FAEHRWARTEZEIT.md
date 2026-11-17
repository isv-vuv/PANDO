# Fährwartezeit

Der Projektparameter „Wartezeit je Fährnutzung“ (Minuten, Standard: 20; 0 deaktiviert den Zuschlag) kann bereits vor der Visum-Verarbeitung in Schritt 5 sowie nachträglich in Schritt 6 eingestellt werden. Speichern in Schritt 6 merkt Analyse und Darstellung zur Neuberechnung vor; über die vorhandene Schaltfläche zur Aktualisierung wird der vorbereitete Visum-Netzstand weiterverwendet, ohne OSM erneut zu importieren. Die Einstellung steht in `ferry_parameters.json` im Projektverzeichnis.

`Ferry` (Importer-Typ 94 / Master-Typ 939) und `Ferry_Access` (Importer-Typ 95 / Master-Typ 940) werden beide als Teil des Fährsystems behandelt: alle Verkehrssysteme einschließlich Fußverkehr (`WALK` / `PUTW`) bewegen sich auf beiden Typen mit 20 km/h (`GTYPE = 100`).

Vor dem Verfahrensablauf wird der Zuschlag genau einmal je Fährnutzung beim Auffahren aus dem übrigen Straßennetz in das Fährsystem `{Ferry, Ferry_Access}` gesetzt:
- Bei direkt angebundenen Fähren am Abbieger `Straße -> Ferry`.
- Bei über `Ferry_Access` angebundenen Fähranlegern am Abbieger `Straße -> Ferry_Access`.
- Der Übergang `Ferry_Access -> Ferry` sowie `Ferry -> Ferry` (geteilte OSM-Fährstrecken) erhält **keinen** weiteren Zuschlag (`0`). Ein direkter Wechsel zwischen zwei Fährdiensten ohne Landstrecke wird damit als durchgehende Fahrt behandelt.

Die vereinfachte ÖV-Kenngröße summiert die Wartezeit direkt von den Abbiegern;
die Faktoren für Minibus/Dreirad vervielfachen sie nicht. Für die nachträgliche
Fußverkehrskorrektur wird die Wartezeit zusätzlich als Kenngröße `AD2` berechnet:
begrenzt wird nur die übrige Reisezeit, danach wird die Wartezeit addiert.
`AddVal2` ist dafür reserviert. Bereits anderweitig belegte Werte führen zu einem
Fehler, statt unbemerkt in die Fährwartezeit einzufließen.

Die automatisierten Tests prüfen die Zuschlagslogik und beide Verfahrensvorlagen.
Die COM-Aufrufe, Routensuche und resultierenden Matrizen müssen ergänzend mit
einem Visum-Testmodell geprüft werden (20, 5 und 0 Minuten; Straßenvergleichsroute,
geteilte Fähre und zwei aufeinanderfolgende Fährnutzungen mit Landweg dazwischen).
