[README auf Deutsch](README_de.md)

# Methods for the Automated Creation of Travel Demand Models (PANDO)

**PANDO** is a program for the automated processing of geospatial data to generate travel demand models. It allows users to create a generic transport model for a region with minimal effort.

---

## Features
- Automatic downloading of the required publicly available data
- Definition of the study area and division into transport zones
- Enhanced plausibility checks for publicly available population data
- Calculation of centralities (including within settlements) to create a straight-line network
- Visualization of results
- Generation of a fully functional VISUM transport model

---

## Model Overview

![Model Overview](docs/assets/Model%20Overview.png)

- [Interactive version on tldraw](https://www.tldraw.com/f/pxgJW2BGy4tr0inALeOnY?d=v291.-2110.9354.5325.OP4KBZrmNWJ8ZbXHBBGtg)
- [Detailed documentation (PDF)](docs/SVT_Generische%20Nachfragemodelle%20mit%20Open-Source-Methoden%20und%20Open-Data.pdf)
- [Folder structure and architecture documentation (Markdown)](docs/ORDNERSTRUKTUR.md)

---

## Installation and Startup Instructions

### Windows

1. Download or clone the repository from GitHub.
2. Double-click the `start.bat` file in the tool's root directory.

### macOS and Linux

1. Download or clone the repository.
2. Make the launcher executable and run it:
   ```bash
   chmod +x start.sh
   ./start.sh
   ```

Detailed installation information is available [here](docs/INSTALL.md).

---

## User Guide

A detailed guide is available in the "docs" folder: [Guides](docs/)

### Workflow

PANDO guides users through the model generation process step by step:

0. **Welcome**: Welcome page.
1. **Location Search**: Select the model location.
2. **Project Folder and Geospatial Data Setup**: Create the project folder, prepare the base data (GADM, GHS-POP), and download the OSM data (PBF).
3. **Study Area and Grid Definition**: Define the grid and model areas (PA, IA1, IA2), and preview the ADM2 external area.
4. **Geospatial Data Processing**: Run OSM preprocessing with Osmium and the QGIS model processing steps (data preparation, classification of central places, grid generation and assignment, intensity estimates and intramunicipal centralities both within the study area and nationwide, and transport zone generation).
5. **Visum Import and Analysis**:
   - Import the link network (Links) and transport zones (Zones).
   - Generate connectors (Connectors).
   - Generate straight-line distance matrices (Direct Line Matrices).
   - Run the procedure sequence (RIN network categorization and accessibility analyses).
   - Save the final Visum file and apply graphic parameters.
6. **Results**: View an overview and export selected result data and analyses to QGIS/Visum.
7. **Report**: Generate nine QGIS maps and a PDF report using the actual project inputs and calculated parameters, preview it and save a copy. Templates, maps and PDF are stored in the project’s `report/` folder.

---

## Terms of Use and Copyrights

Before execution, PANDO automatically asks users to accept the terms of use for all data sources and tools used (GADM, GHS-POP, OpenStreetMap/Geofabrik, Osmium, QGIS models, Visum scripts).

### UI and Map Icons
- **QGIS Icon Theme:** The map navigation and measurement icons (`mActionPan.svg`, `mActionMeasure.svg`, `mActionLayers.svg`, `mCapturePoint.svg`, `mActionSelectRectangle.svg`, `CRS.svg`, etc.) originate from the [QGIS project](https://github.com/qgis/QGIS) and are licensed under the **GNU General Public License (GPLv2+)**, © QGIS Development Team.

### Privacy and Network Usage

PANDO respects your privacy and contains no tracking, telemetry, or analytics tools.

To function properly, the application sends requests to the following external services when an internet connection is active:
- **Nominatim (OpenStreetMap Foundation):** To search for and geocode cities and regions.
- **Geofabrik GmbH:** To download up-to-date OpenStreetMap raw data extracts (`.osm.pbf`).
- **Map tile services (OpenStreetMap / Esri):** To display the interactive base map.
- **University of Stuttgart (ISV, `map.isv.uni-stuttgart.de`):** To automatically download prepared global geospatial datasets (GHS-POP GeoTIFF, GADM GeoPackages).
- **GitHub:** To check for software updates.

For technical reasons, these requests transmit your IP address to the respective server operator. All requests comply with the respective fair use and server usage policies.

---

Note: All Python code for **displaying** the application and its internal management logic (e.g., data downloads) was generated with the help of LLMs. The core model logic was written manually.

### Contributors

- Yannick Roth (model logic and UI)
- Vincent Wettengel (UI)

The name is inspired by [**Pando**](https://de.wikipedia.org/wiki/Pando_(Baum)) ([English Wikipedia link](https://en.wikipedia.org/wiki/Pando_(tree))), a clonal colony of quaking aspen in Fishlake National Forest in Utah, USA. Pando is characterized by a vast, deeply interconnected root system—much like the interconnected geospatial data in this tool.
