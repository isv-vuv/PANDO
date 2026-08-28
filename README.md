# Methoden für die automatisierte Erstellung von Verkehrsnachfragemodellen (PANDO)

**PANDO** ist ein Werkzeug zur automatisierten Aufbereitung von Geodaten für die Generierung von Verkehrsnachfragemodellen. 

Der Name ist angelehnt an [**Pando**](https://de.wikipedia.org/wiki/Pando_(Baum)) ([Wikipedia-Link EN](https://en.wikipedia.org/wiki/Pando_(tree))), eine Klonkolonie einer Amerikanischen Zitterpappel im Fishlake National Forest in Utah, USA. Pando zeichnet sich durch ein riesiges, tief miteinander verwurzeltes und zusammenhängendes Wurzelsystem aus – genau wie die miteinander verwurzelten Geodaten in diesem Tool.

---

## Modellübersicht

![Model Overview](docs/assets/Model%20Overview.png)

- [Interaktive Version auf tldraw](https://www.tldraw.com/f/pxgJW2BGy4tr0inALeOnY?d=v291.-2110.9354.5325.OP4KBZrmNWJ8ZbXHBBGtg)
- [Ausführliche Dokumentation (PDF)](docs/SVT_Generische%20Nachfragemodelle%20mit%20Open-Source-Methoden%20und%20Open-Data.pdf)
- [Dokumentation der Ordnerstruktur & Architektur (Markdown)](docs/ORDNERSTRUKTUR.md)

---

## Funktionsumfang

PANDO leitet Schritt für Schritt durch den Prozess der Modellgenerierung:

0. **Willkommen**: Willkommensseite.
1. **Ortssuche**: Auswahl des Modellortes.
2. **Projektordner & Geodaten-Setup**: Erstellung des Projektordners, Bereitstellung der Basisdaten (GADM, GHS-POP) und Download der OSM-Daten (PBF).
3. **Untersuchungsraum & Gitterfestlegung**: Festlegung des Gitters, Definition der Modellräume (PA, IA1, IA2) sowie ADM2-Außenraum-Vorschau.
4. **Verarbeitung der Geodaten**: Ausführung der OSM-Vorverarbeitung mit Osmium sowie der QGIS-Modellverarbeitungsschritte (Datenvorbereitung, Klassifizierung zentraler Orte, Gittererzeugung & -zuweisung, Intensitätsschätzungen und innergemeindliche Zentralitäten im Untersuchungsraum sowie landesweit, Verkehrszellengenerierung).
5. **Visum-Import & Auswertung**:
   - Import von Streckennetz (Links) und Verkehrszellen (Zones).
   - Anbindungserzeugung (Connectors).
   - Luftlinienmatrizen (Direct Line Matrices).
   - Ausführen des Verfahrensablaufs (RIN-Netzkategorisierung und Erreichbarkeitsanalysen).
   - Speichern der finalen Visum-Datei und Anwendung von Grafikparametern.
6. **Ergebnisse**: Übersicht und QGIS-/Visum-Export ausgewählter Ergebnisdaten und Analysen.

---

## Verwendete QGIS-Skripte und Modelle

QGIS-Python-Skripte und `.model3`-Dateien werden beim Start automatisch in das QGIS-Standardprofil (`%APPDATA%\QGIS\QGIS4\profiles\default\processing\scripts` & `models`) kopiert. Diese können in QGIS über die Verarbeitungswerkzeuge (Verarbeitung ► Werkzeugkiste ► Skripte) sowie den Modellentwurf (Verarbeitung ► Modellentwurf ► Modelle öffnen) manuell aufgerufen und genutzt werden.

---

## Installations- & Startanweisungen

### Windows (Automatischer Start - Empfohlen)

1. Herunterladen / Klonen des Repositories aus GitHub.
2. Doppelklick auf die Datei `start.bat` im Hauptverzeichnis des Tools.
   - Das Skript sucht automatisch nach der lokalen QGIS- bzw. OSGeo4W-Installation (`python-qgis.bat`).
   - Falls QGIS 4 noch nicht installiert ist, wird angeboten, QGIS automatisch über `winget install OSGeo.QGIS` zu installieren.
   - Das Skript prüft und installiert alle benötigten Python-Abhängigkeiten (`requirements.txt`) und startet anschließend die Anwendung (`MainQt.py`).

### Windows (Manueller Aufruf via Kommandozeile)

1. QGIS 4 installieren (falls nicht vorhanden):
   ```cmd
   winget install OSGeo.QGIS
   ```
2. In das `bin`-Verzeichnis der QGIS-Installation wechseln (z. B. `C:\Program Files\QGIS 4.2.0\bin` oder `C:\OSGeo4W\bin`).
3. Python-Dependencies installieren:
   ```cmd
   python-qgis.bat -m pip install -r [Pfad zu Tool]\requirements.txt
   ```
4. Anwendung starten:
   ```cmd
   python-qgis.bat [Pfad zu Tool]\MainQt.py
   ```

### MacOS

1. Herunterladen der Dateien aus GitHub.
2. Installieren von QGIS:
   ```bash
   brew install QGIS
   ```
3. Pfad zur QGIS-Python-Installation aufrufen:
   ```bash
   cd /opt/homebrew/Caskroom/qgis/4.2.0/QGIS-final-4_2_0.app/Contents/MacOS/python
   ```
4. Dependencies installieren:
   ```bash
   python -m pip install -r [Pfad zu Tool]/requirements.txt
   ```
5. Programm starten:
   ```bash
   python [Pfad zu Tool]/MainQt.py
   ```

---

## Nutzungsbedingungen & Copyrights

Vor der Ausführung fragt PANDO automatisch die Akzeptanz der Nutzungsbedingungen aller verwendeten Datenquellen und Werkzeuge ab (GADM, GHS-POP, OpenStreetMap/Geofabrik, Osmium, QGIS-Modelle, Visum-Skripte).

### UI- und Karten-Icons
- **QGIS Icon Theme:** Die Kartennavigations- und Mess-Icons (`mActionPan.svg`, `mActionMeasure.svg`, `mActionLayers.svg`, `mCapturePoint.svg`, `mActionSelectRectangle.svg`, `CRS.svg` etc.) stammen aus dem [QGIS-Projekt](https://github.com/qgis/QGIS) und unterliegen der **GNU General Public License (GPLv2+)**, © QGIS Development Team.

### Datenschutz & Netzwerknutzung
PANDO respektiert Ihre Privatsphäre und enthält keinerlei Tracking, Telemetrie oder Analyse-Tools.

Für die ordnungsgemäße Funktion stellt die Anwendung bei aktiver Internetverbindung Anfragen an folgende externe Dienste:
- **Nominatim (OpenStreetMap Foundation):** Zur Suche und Geokodierung von Städten und Regionen.
- **Geofabrik GmbH:** Zum Herunterladen aktueller OpenStreetMap-Rohdatenextrakte (`.osm.pbf`).
- **Kartenkacheldienste (OpenStreetMap / Esri):** Zur visuellen Darstellung der interaktiven Basiskarte.
- **Universität Stuttgart (ISV, `map.isv.uni-stuttgart.de`):** Zum automatisierten Herunterladen vorbereiteter globaler Geodatensätze (GHS-POP GeoTIFF, GADM GeoPackages).
- **GitHub:** Zur Prüfung auf Software-Aktualisierungen.

Bei diesen Abfragen wird technisch bedingt Ihre IP-Adresse an den jeweiligen Serverbetreiber übermittelt. Alle Abfragen erfolgen unter Einhaltung der jeweiligen Fair-Use- und Server-Nutzungsrichtlinien.

---

Hinweis: Sämtlicher Python-Code zur **Darstellung** der Applikation wurde mit Hilfe von LLMs generiert.