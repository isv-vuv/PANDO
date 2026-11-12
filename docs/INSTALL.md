## Installations- & Startanweisungen

### Windows 

1. Herunterladen / Klonen des Repositories aus GitHub.
2. Doppelklick auf die Datei `start.bat` im Hauptverzeichnis des Tools.
   - Das Skript sucht automatisch nach QGIS-Installationen unter `%ProgramFiles%` sowie OSGeo4W unter `%LOCALAPPDATA%\Programs\OSGeo4W`, `%SystemDrive%\OSGeo4W` und `%SystemDrive%\OSGeo4W64` und prüft deren Python-Umgebung. Auch `python-qgis.bat` im `PATH` wird berücksichtigt; ungeeignete Launcher werden übersprungen.
   - Für andere Installationsorte kann die Umgebungsvariable `PANDO_QGIS_PYTHON` auf den vollständigen Pfad zu `python-qgis.bat` gesetzt werden.
   - Falls QGIS 4 noch nicht installiert ist, wird angeboten, QGIS automatisch über `winget install OSGeo.QGIS` zu installieren.
   - Das Skript prüft und installiert alle benötigten Python-Abhängigkeiten (`requirements.txt`) und startet anschließend die Anwendung (`MainQt.py`).

### macOS und Linux 

1. Repository herunterladen oder klonen.
2. Starter ausführbar machen und starten:
   ```bash
   chmod +x start.sh
   ./start.sh
   ```
   Das Skript erkennt eine vorhandene QGIS-4-Python-Umgebung und prüft auch die Qt-Bibliotheken. Ungeeignete Interpreter werden übersprungen. Unter macOS werden QGIS-App-Bundles in `~/Applications`, `/Applications` und den Homebrew-Cask-Verzeichnissen berücksichtigt; unter Linux werden der Systeminterpreter und `python3` im `PATH` geprüft. Falls QGIS fehlt, bietet es die Installation über Homebrew (`brew`) unter macOS beziehungsweise über `apt`, `dnf`, `apk`, `pacman` oder `zypper` unter Linux an. Anschließend werden die Python-Abhängigkeiten geprüft und PANDO gestartet.

   Bei einer Installation an einem ungewöhnlichen Ort kann der Interpreter explizit angegeben werden:
   ```bash
   PANDO_QGIS_PYTHON=/pfad/zum/qgis-python ./start.sh
   ```
