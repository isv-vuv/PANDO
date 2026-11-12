# Fährwartezeit

Schritt 6 enthält den Projektparameter „Wartezeit je Fährnutzung“ (Minuten).
Standard: 20; 0 deaktiviert den Zuschlag. Speichern merkt Analyse und Darstellung
zur Neuberechnung vor. Über die vorhandene Schaltfläche zur Aktualisierung wird
der vorbereitete Visum-Netzstand weiterverwendet; OSM wird nicht erneut importiert.
Die Einstellung steht in `ferry_parameters.json` im Projektverzeichnis.

Vor dem Verfahrensablauf wird der Zuschlag auf Abbieger von einer Nicht-Fährstrecke
auf eine Strecke mit dem Streckentypnamen `Ferry` gesetzt. Das umfasst importierte
Fähren (Typ 94) und den Mastertyp 939. `Ferry_Access` gilt als Landzugang.
Die Zeit wird in Sekunden zu `T0PRT` addiert. Das Attribut `PANDO_FERRY_WAIT`
merkt den zuletzt verwendeten Zuschlag, damit Wiederholungen ihn ersetzen.
Fähre–Fähre-Abbieger erhalten keinen weiteren Zuschlag. Ein direkter Wechsel
zwischen zwei Fährdiensten ohne Landstrecke wird damit als durchgehende Fahrt
behandelt; OSM-Fährdienste werden derzeit nicht separat identifiziert.

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
