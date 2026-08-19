# Methoden für die automatisierte Erstellung von Verkehrsnachfragemodellen (PANDO)

**PANDO** ist ein Werkzeug zur automatisierten Aufbereitung von Geodaten, Netzwerken und Generierung von Verkehrsnachfragemodellen. 

Der Name ist angelehnt an [**Pando**](https://de.wikipedia.org/wiki/Pando_(Baum)) ([Wikipedia-Link EN](https://en.wikipedia.org/wiki/Pando_(tree))), eine Klonkolonie einer Amerikanischen Zitterpappel im Fishlake National Forest in Utah, USA. Pando zeichnet sich durch ein riesiges, tief miteinander verwurzeltes und zusammenhängendes Wurzelsystem aus – genau wie die miteinander verwurzelten Geodaten in diesem Tool.

---

## Modellübersicht (Model Overview)

![Model Overview](docs/assets/Model%20Overview.png)

- [Interaktive Version auf tldraw](https://www.tldraw.com/f/pxgJW2BGy4tr0inALeOnY?d=v291.-2110.9354.5325.OP4KBZrmNWJ8ZbXHBBGtg)
- [Ausführliche Dokumentation (PDF)](docs/SVT_Generische%20Nachfragemodelle%20mit%20Open-Source-Methoden%20und%20Open-Data.pdf)

---

## Funktionsumfang (Functionality)

PANDO leitet Schritt für Schritt durch den Prozess der Modellgenerierung:

0. **Willkommen**: Willkommensseite.
1. **Ortssuche**: Auswahl des Modellortes.
2. **Modellräume**: Definition der Modellräume.
3. **Datendownload**: Download benötigter Daten.
4. **Verarbeitung der Geodaten**: Ausführung von QGIS-Verarbeitungsschritten und Verarbeitung der OSM-Daten mit Osmium.
5. **Visum-Import & Auswertung**:
   - Import von Straßennetz (Links) und Verkehrszellen (Zones).
   - Anbindungserzeugung (Connectors).
   - Luftlinienmatrizen (Direct Line Matrices).
   - Ausführen eines Verfahrensablaufs.
   - Speichern der finalen Visum-Datei.
6. **Ergebnisse**: Auflistung einer Auswahl an Ergebnissen des Verfahrens.

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

---

Hinweis: Sämtlicher Python-Code zur **Darstellung** der Applikation wurde mit Hilfe von LLMs generiert.