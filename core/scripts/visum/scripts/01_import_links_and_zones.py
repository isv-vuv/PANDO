import os
import re
import sys
import logging
import tempfile
from pathlib import Path
from datetime import datetime
import pandas as pd
import geopandas as gpd
import win32com.client as com

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# English professional descriptions for user-defined attributes (UDA)
UDA_DESCRIPTIONS = {
    # --- Demographics & Spatial Structure ---
    "POP": "Total population in the traffic zone (aggregated GHS-POP dataset, sum-conserving redistributed)",
    "TYPENO": "Hierarchy or centrality level of the traffic cell/area (RIN / Central Places)",
    "MAINZONENO": "Reference ID of the superordinate main zone / aggregation area",
    "ZONETYPE": "Zone type classification: PA (Planning Area), IA1/IA2 (Influence Areas 1/2), EA (External Area)",
    "ISLAND_ID": "Unique ID of the contiguous landmass/island for topological separation",

    # --- Administrative Boundaries (GADM) ---
    "GID_0": "GADM country identifier (Level 0 ISO code)",
    "NAME_0": "GADM country name (Level 0)",
    "GID_1": "GADM state/region identifier (Level 1)",
    "NAME_1": "GADM state/region name (Level 1)",
    "GID_2": "GADM district/city identifier (Level 2)",
    "NAME_2": "GADM district/city name (Level 2)",

    # --- Grid Hierarchy References ---
    "NO_E0": "Reference ID of grid cell level E0 (Influence Area 2, cell size = 4,500 m)",
    "NO_E1": "Reference ID of grid cell level E1 (Influence Area 1, cell size = 1,500 m)",
    "NO_E2": "Reference ID of grid cell level E2 (Planning Area, cell size = 500 m)",

    # --- POI Functional Area Intensities ---
    "HEALTHCARE": "Accumulated intensity score for healthcare and social services POIs (Tiers 1-3)",
    "LEISURE": "Accumulated intensity score for leisure and culture POIs (Tiers 1-3)",
    "RETAIL": "Accumulated intensity score for retail and local supply POIs (Tiers 1-3)",
    "EDUCATION": "Accumulated intensity score for educational facilities POIs (Tiers 1-3)",
    "GOVERNMENT": "Accumulated intensity score for government and public service POIs (Tiers 1-3)",

    # --- Relative Shares of Functional Areas ---
    "P_HEALTHCARE": "Percentage share of healthcare facilities relative to total zone POI intensity",
    "P_LEISURE": "Percentage share of leisure facilities relative to total zone POI intensity",
    "P_RETAIL": "Percentage share of retail locations relative to total zone POI intensity",
    "P_EDUCATION": "Percentage share of educational facilities relative to total zone POI intensity",
    "P_GOVERNMENT": "Percentage share of government/public services relative to total zone POI intensity",

    # --- POI Intensities & Land Use Mix ---
    "TOTAL_INTENSITY": "Total accumulated POI intensity score across all functional areas (basis for centrality)",
    "ENTROPY": "Absolute entropy value describing land use mix and functional diversity in the cell",
    "ENTROPY_INDEX": "Normalized entropy index (0 to 1) of land use mixture (1 = maximum functional mix)",

    # --- Detailed Facility Counts (Totals & Sub-categories) ---
    "NUM_HEALTHCARE": "Total count of healthcare facilities in the zone",
    "NUM_HEALTHCARE_O": "Count of specialized healthcare facilities (Category O)",
    "NUM_HEALTHCARE_N": "Count of basic/neighborhood healthcare facilities (Category N)",
    "NUM_HEALTHCARE_M": "Count of medical supply/pharmacy locations (Category M)",

    "NUM_GOVERNMENT": "Total count of government and public administration service points",
    "NUM_GOVERNMENT_A": "Count of central/major government facilities (Category A)",
    "NUM_GOVERNMENT_B": "Count of regional public administration service points (Category B)",
    "NUM_GOVERNMENT_C": "Count of local administrative branches (Category C)",

    "NUM_EDUCATION": "Total count of educational institutions in the zone",
    "NUM_EDUCATION_D": "Count of primary and secondary schools (Category D)",
    "NUM_EDUCATION_E": "Count of higher education and vocational institutions (Category E)",
    "NUM_EDUCATION_F": "Count of early childhood education and care centers (Category F)",

    "NUM_RETAIL": "Total count of retail and grocery supply points",
    "NUM_RETAIL_R": "Count of general retail stores and supermarkets (Category R)",
    "NUM_RETAIL_S": "Count of specialized retail shops (Category S)",
    "NUM_RETAIL_T": "Count of local daily supply locations (Category T)",

    "NUM_LEISURE": "Total count of leisure, sports, and cultural venues",
    "NUM_LEISURE_J": "Count of cultural and arts facilities (Category J)",
    "NUM_LEISURE_K": "Count of sports and outdoor recreation sites (Category K)",
    "NUM_LEISURE_L": "Count of entertainment and gastronomy locations (Category L)",
}


def get_project_paths() -> tuple[Path, Path, Path]:
    """Ermittelt dynamisch die Projektpfade relativ zum Skriptort."""
    script_dir = Path(__file__).resolve().parent
    base_project_dir = script_dir.parent if script_dir.name == "visum_processing" else script_dir.parent.parent

    visum_input_dir = base_project_dir / "Models Input" / "Visum"
    osm_input_dir = base_project_dir / "Models Input" / "OSM"
    return base_project_dir, visum_input_dir, osm_input_dir


def get_osm_importer_dir(importer_folder_name: str = "PANDO_Importer") -> Path:
    """Ermittelt den Pfad zum Importer-Ordner im AppData-Verzeichnis des aktuellen Users."""
    appdata = Path(os.environ.get("APPDATA", ""))
    target_dir = appdata / "PTV Vision" / "PTV Visum 2025" / "Importer" / "PANDO_Importer"

    if target_dir.exists():
        return target_dir

    ptv_dir = appdata / "PTV Vision"
    if ptv_dir.exists():
        for found_dir in ptv_dir.rglob(importer_folder_name):
            if found_dir.is_dir():
                return found_dir

    raise FileNotFoundError(f"OSM-Importer '{importer_folder_name}' wurde nicht in AppData gefunden!")


def get_visum_projection_wkt(gdf: gpd.GeoDataFrame) -> str:
    """Sucht direkt passend zum Visum-Namensschema 'WGS 1984 UTM Zone XX[N/S].prj'."""
    if not gdf.crs:
        raise ValueError("Das GeoPackage besitzt kein gültiges Koordinatensystem (CRS)!")

    crs_name = gdf.crs.name or ""
    logging.info(f"Erkanntes Koordinatensystem (Bezirke): {crs_name}")

    match = re.search(r"zone\s*(\d+[N|S]?)", crs_name, re.IGNORECASE)
    if not match:
        match = re.search(r"(\d{1,2}[N|S])", crs_name, re.IGNORECASE)

    zone_str = match.group(1).upper() if match else ""
    if zone_str:
        logging.info(f"Erkannte UTM-Zone für Dateisuche: {zone_str}")

    appdata = os.environ.get("APPDATA")
    if appdata and zone_str:
        wgs84_utm_dir = Path(
            appdata) / "PTV Vision" / "PTV Visum 2025" / "Projections" / "Projected Coordinate Systems" / "Utm" / "Wgs 1984"

        if not wgs84_utm_dir.exists():
            ptv_dir = Path(appdata) / "PTV Vision"
            found_dirs = [d for d in ptv_dir.rglob("Wgs 1984") if "Utm" in str(d)]
            if found_dirs:
                wgs84_utm_dir = found_dirs[0]

        if wgs84_utm_dir.exists():
            for prj_file in wgs84_utm_dir.glob("*.prj"):
                file_upper = prj_file.name.upper()
                if zone_str in file_upper and "ZONE" in file_upper:
                    logging.info(f"Passende Visum-Projektion gefunden: {prj_file.name}")
                    return prj_file.read_text(encoding="utf-8")

    logging.warning("Keine exakte .prj-Datei für die Zone im Visum-Ordner gefunden. Nutze WKT-Fallback.")
    try:
        return gdf.crs.to_wkt(version="WKT1_ESRI")
    except Exception:
        return gdf.crs.to_wkt()


def map_dtype_to_visum(col_name: str, dtype) -> str:
    """Bestimmt den Visum-Datentyp für benutzerdefinierte Attribute (UDA)."""
    col_upper = col_name.upper()

    if col_upper.startswith("NUM_") or col_upper.startswith("NO_E") or col_upper in ["POP", "MAINZONENO", "TYPENO"]:
        return "Int"

    if col_upper in ["ENTROPY_INDEX", "ENTROPY", "TOTAL_INTENSITY", "HEALTHCARE", "LEISURE", "RETAIL", "EDUCATION",
                     "GOVERNMENT"] or col_upper.startswith("P_"):
        return "Double"

    if pd.api.types.is_float_dtype(dtype):
        return "Double"
    elif pd.api.types.is_integer_dtype(dtype):
        return "Int"
    elif pd.api.types.is_bool_dtype(dtype):
        return "Boolean"
    else:
        return "Text"


def prepare_geopackage_df(gdf: gpd.GeoDataFrame, is_mainzone: bool = False) -> pd.DataFrame:
    """Bereinigt das GeoDataFrame, überführt X/Y-Koordinaten und berechnet Polygon-Geometrien."""
    df = gdf.copy()

    cols_to_drop = [c for c in df.columns if c.upper() in ["FID_OLD", "WKTSURFACE", "WKTLOC"]]
    if cols_to_drop:
        df = df.drop(columns=cols_to_drop)

    rename_map = {
        "ZONENO": "NO", "ZoneNo": "NO", "Zone_No": "NO",
        "Name": "NAME", "NAME": "NAME",
        "TypeNo": "TYPENO", "TYPE_NO": "TYPENO",
        "MAINZONENO": "MAINZONENO", "MainZoneNo": "MAINZONENO",
        "FID": "CODE", "fid": "CODE",
        "Zone_Type": "ZONETYPE", "ZONETYPE": "ZONETYPE",
        "XCoord": "XCOORD", "XCOORD": "XCOORD",
        "YCoord": "YCOORD", "YCOORD": "YCOORD"
    }

    df = df.rename(columns=rename_map)

    if df.geometry is not None:
        df["WKTSURFACE"] = df.geometry.apply(lambda geom: geom.wkt if geom and not geom.is_empty else "")
        if "XCOORD" not in df.columns or "YCOORD" not in df.columns:
            centroids = df.geometry.centroid
            df["XCOORD"] = centroids.x
            df["YCOORD"] = centroids.y
        df = df.drop(columns=["geometry"])

    df.columns = [col.upper() for col in df.columns]

    for col in df.columns:
        col_upper = col.upper()
        if col_upper.startswith("NUM_") or col_upper.startswith("NO_E") or col_upper in ["POP", "MAINZONENO", "TYPENO",
                                                                                         "NO"]:
            df[col] = pd.to_numeric(df[col], errors='coerce').astype("Int64")
        elif col_upper in ["ENTROPY_INDEX", "ENTROPY", "TOTAL_INTENSITY", "HEALTHCARE", "LEISURE", "RETAIL",
                           "EDUCATION", "GOVERNMENT", "XCOORD", "YCOORD"] or col_upper.startswith("P_"):
            df[col] = pd.to_numeric(df[col], errors='coerce').astype("float64")

    return df


def build_uda_rows(df: pd.DataFrame, table_type: str, standard_atts: set) -> list[str]:
    """Erstellt $USERATTDEF Zeilen mit englischen Beschreibungen."""
    uda_rows = []
    for col_name in df.columns:
        if col_name in standard_atts:
            continue

        visum_type = map_dtype_to_visum(col_name, df[col_name].dtype)
        comment = UDA_DESCRIPTIONS.get(col_name, f"Custom attribute: {col_name}")

        if visum_type == "Double":
            row = f"{table_type};{col_name};{col_name};{col_name};Double;MIN;MAX;;;{comment};0;4;Data;;0;SUM;0;;1;;"
        elif visum_type == "Int":
            row = f"{table_type};{col_name};{col_name};{col_name};Int;MIN;MAX;;;{comment};0;0;Data;;0;SUM;0;;1;;"
        elif visum_type == "Boolean":
            row = f"{table_type};{col_name};{col_name};{col_name};Boolean;;;;;{comment};0;0;Data;;0;SUM;0;;1;;"
        else:
            row = f"{table_type};{col_name};{col_name};{col_name};Text;MIN;MAX;;;{comment};255;0;Data;;0;SUM;0;;1;;"

        uda_rows.append(row)

    return uda_rows


def export_gpkg_to_visum_net(
        zones_gpkg_path: Path,
        output_net_path: Path,
        mainzones_gpkg_path: Path | None = None
) -> tuple[Path, str, pd.DataFrame, pd.DataFrame | None]:
    """Liest GeoPackages ein, erzeugt die Visum .net Datei und gibt Datenframes mit WKT-Geometrien zurück."""
    logging.info(f"Lese Bezirke: {zones_gpkg_path}")
    gdf_zones = gpd.read_file(zones_gpkg_path)

    wkt_text = get_visum_projection_wkt(gdf_zones)
    df_zones = prepare_geopackage_df(gdf_zones, is_mainzone=False)

    df_mainzones = None
    if mainzones_gpkg_path and mainzones_gpkg_path.exists():
        logging.info(f"Lese Hauptbezirke: {mainzones_gpkg_path}")
        gdf_mainzones = gpd.read_file(mainzones_gpkg_path)
        df_mainzones = prepare_geopackage_df(gdf_mainzones, is_mainzone=True)

    std_zone_atts = {"NO", "NAME", "CODE", "TYPENO", "MAINZONENO", "XCOORD", "YCOORD", "WKTSURFACE", "WKTLOC"}
    std_mainzone_atts = {"NO", "NAME", "CODE", "XCOORD", "YCOORD", "WKTSURFACE", "WKTLOC"}

    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    header = f"""$VISION
* Visum Import Export
* {now_str}
* 
* Tabelle: Versionsblock
*
$VERSION:VERSNR;FILETYPE;LANGUAGE;UNIT
15;Net;ENG;KM

*
* Tabelle: Projektion
*
$PROJECTION:WKT
{wkt_text}

*
* Tabelle: Benutzerdefinierte Attribute
*
$USERATTDEF:OBJID;ATTID;CODE;NAME;VALUETYPE;MINVALUE;MAXVALUE;DEFAULTVALUE;DEFAULTSTRINGVALUE;COMMENT;MAXSTRINGLENGTH;NUMDECPLACES;DATASOURCETYPE;FORMULA;SCALEDBYLENGTH;CROSSSECTIONLOGIC;CSLIGNORECLOSED;SUBATTRS;CANBEEMPTY;USERDEFINEDGROUPNAME;OPERATIONREFERENCE
"""

    uda_rows = []
    if df_mainzones is not None:
        uda_rows.extend(build_uda_rows(df_mainzones, "MAINZONE", std_mainzone_atts))
    uda_rows.extend(build_uda_rows(df_zones, "ZONE", std_zone_atts))

    uda_block = "\n".join(uda_rows) + "\n\n"

    with open(output_net_path, "w", encoding="utf-8-sig") as f:
        f.write(header)
        f.write(uda_block)

        if df_mainzones is not None:
            f.write("* \n* Tabelle: Hauptbezirke\n* \n$MAINZONE:" + ";".join(df_mainzones.columns) + "\n")
            f.write(df_mainzones.to_csv(sep=";", index=False, header=False, na_rep=""))
            f.write("\n")

        f.write("* \n* Tabelle: Bezirke\n* \n$ZONE:" + ";".join(df_zones.columns) + "\n")
        f.write(df_zones.to_csv(sep=";", index=False, header=False, na_rep=""))

    logging.info(f"Netzdatei der Bezirke erzeugt: {output_net_path.name}")
    return output_net_path, wkt_text, df_zones, df_mainzones


def set_zone_based_zoom(visum, margin_factor: float = 0.05):
    """Setzt den Bildausschnitt (Zoom) in Visum basierend auf den Quantilen der Bezirkskoordinaten."""
    zone_coords = visum.Net.Zones.GetMultipleAttributes(["XCOORD", "YCOORD"])
    if not zone_coords:
        logging.warning("Keine Bezirke in Visum für Zoom-Einstellung gefunden.")
        return

    df_coords = pd.DataFrame(zone_coords, columns=["XCOORD", "YCOORD"])
    x_coords = pd.to_numeric(df_coords['XCOORD'], errors='coerce').dropna()
    y_coords = pd.to_numeric(df_coords['YCOORD'], errors='coerce').dropna()

    x_min = float(x_coords.quantile(0.005))
    x_max = float(x_coords.quantile(0.995))
    y_min = float(y_coords.quantile(0.005))
    y_max = float(y_coords.quantile(0.995))

    dx = x_max - x_min
    dy = y_max - y_min

    x_min_padded = x_min - (dx * margin_factor)
    x_max_padded = x_max + (dx * margin_factor)
    y_min_padded = y_min - (dy * margin_factor)
    y_max_padded = y_max + (dy * margin_factor)

    logging.info(
        f"Setze fokussierten Zoom: [{x_min_padded:.1f}, {y_min_padded:.1f}] bis [{x_max_padded:.1f}, {y_max_padded:.1f}]")
    visum.Graphic.SetWindow(x_min_padded, y_min_padded, x_max_padded, y_max_padded)


def run_import_links_and_zones(
        zones_gpkg: Path,
        mainzones_gpkg: Path,
        osm_file: Path,
        output_dir: Path,
        visum=None,
        importer_name: str = "PANDO_Importer"
):
    """Führt den Strecken- und Bezirksimport sequentiell und georeferenziert aus."""
    ver_dir = output_dir / "ver"
    ver_dir.mkdir(parents=True, exist_ok=True)
    net_dir = output_dir / "net"
    net_dir.mkdir(parents=True, exist_ok=True)

    zones_net_path = net_dir / "zones_import.net"
    net_path, wkt_text, df_zones, df_mainzones = export_gpkg_to_visum_net(
        zones_gpkg_path=zones_gpkg,
        output_net_path=zones_net_path,
        mainzones_gpkg_path=mainzones_gpkg if mainzones_gpkg.exists() else None
    )

    if visum is None:
        logging.info("Starte frische Visum COM-Instanz...")
        visum = com.Dispatch("Visum.Visum.250")
        visum.Graphic.ShowMaximized()

    try:
        import importlib
        step7 = importlib.import_module("07_apply_gpa_parameters")
        app_lang = step7.determine_app_language(output_dir.parent.parent if output_dir.name == "visum" else output_dir.parent, visum)
        visum = step7.ensure_visum_language(visum, app_lang, log=print)
    except Exception:
        pass

    visum.ClearNet()

    # Define WKT string for WGS84
    wgs84_geographic_wkt = 'GEOGCS["GCS_WGS_1984",DATUM["D_WGS_1984",SPHEROID["WGS_1984",6378137,298.257223563]],PRIMEM["Greenwich",0],UNIT["Degree",0.017453292519943295]]'

    # Schritt 1: Importiere OSM-Streckennetz
    logging.info("Schritt 1: Importiere OSM-Streckennetz")
    importer_dir = get_osm_importer_dir(importer_name)
    param_file_names = [
        str(importer_dir / f"{importer_name}.xml"),
        str(importer_dir / f"{importer_name}.cfg"),
        str(importer_dir / f"{importer_name}.net"),
        str(Path(tempfile.gettempdir()) / f"{osm_file.stem}_converted.net"),
        str(importer_dir / f"{importer_name}.gpa") if (importer_dir / f"{importer_name}.gpa").exists() else ""
    ]
    logging.info("Importiere OSM-Streckennetz in Visum via PTV OSM-Importer (Vorgang dauert ca. 5–15 Minuten, bitte warten)...")
    visum.IO.ImportOpenStreetMap([str(osm_file)], param_file_names, False, 0)
    logging.info("OSM-Streckennetz erfolgreich in Visum importiert.")

    # Set WGS84 as explicit source coordinate system post-import
    visum.Net.SetProjection(wgs84_geographic_wkt, False)

    step1_ver = ver_dir / "01_Links_Imported.ver"
    logging.info(f"Speichere Schritt 1 unter: {step1_ver}")
    visum.SaveVersion(str(step1_ver))

    # Schritt 2: Umschalten auf UTM & OSM-Strecken mathematisch transformieren
    logging.info("Schritt 2: Schalte auf UTM-Zielprojektion um & transformiere Streckenkoordinaten")
    visum.Net.SetProjection(wkt_text, True)  # True = Rechnet WGS84-Grad-Koordinaten der Strecken in UTM-Meter um

    # Erst jetzt die Bezirke additiv ins UTM-Netz laden
    logging.info("Lade Bezirke additiv ins UTM-Netz...")
    visum.IO.LoadNet(str(zones_net_path), ReadAdditive=True)

    # Polygon-Flächengeometrien nativ in Visum-Bezirke schreiben (mit Mapping auf Visums interne VISUM_ID)
    logging.info("Weise Polygon-Geometrien ('WKTSURFACE') direkt den Visum-Bezirken zu...")
    if "WKTSURFACE" in df_zones.columns:
        visum_zone_ids = pd.DataFrame(
            visum.Net.Zones.GetMultiAttValues("NO", OnlyActive=False),
            columns=["VISUM_ID", "NO"]
        ).astype({"VISUM_ID": int, "NO": int})

        df_zones_copy = df_zones.copy()
        df_zones_copy["NO"] = df_zones_copy["NO"].astype(int)

        df_zones_merged = pd.merge(df_zones_copy, visum_zone_ids, on="NO")
        zone_wkts = df_zones_merged[["VISUM_ID", "WKTSURFACE"]].dropna().values.tolist()

        if zone_wkts:
            visum.Net.Zones.SetMultiAttValues("WKTSURFACE", zone_wkts)
            logging.info(f"{len(zone_wkts)} Polygon-Flächengeometrien erfolgreich den Bezirken zugewiesen.")

    if df_mainzones is not None and "WKTSURFACE" in df_mainzones.columns:
        visum_mainzone_ids = pd.DataFrame(
            visum.Net.MainZones.GetMultiAttValues("NO", OnlyActive=False),
            columns=["VISUM_ID", "NO"]
        ).astype({"VISUM_ID": int, "NO": int})

        df_mainzones_copy = df_mainzones.copy()
        df_mainzones_copy["NO"] = df_mainzones_copy["NO"].astype(int)

        df_mainzones_merged = pd.merge(df_mainzones_copy, visum_mainzone_ids, on="NO")
        mainzone_wkts = df_mainzones_merged[["VISUM_ID", "WKTSURFACE"]].dropna().values.tolist()

        if mainzone_wkts:
            visum.Net.MainZones.SetMultiAttValues("WKTSURFACE", mainzone_wkts)

    # Basemap & Zoom setzen
    visum.Net.GraphicParameters.BaseMap.SetAttValue("DRAW", 1)
    visum.Net.GraphicParameters.BaseMap.SetAttValue("USEDEFAULTPROVIDER", 0)
    visum.Net.GraphicParameters.BaseMap.SetAttValue("MAPPROVIDERNAME", "MapTiler")

    set_zone_based_zoom(visum)
    visum.Graphic.Redraw()

    step2_ver = ver_dir / "02_Zones_Imported.ver"
    logging.info(f"Speichere Schritt 2 unter: {step2_ver}")
    visum.SaveVersion(str(step2_ver))

    return visum


def get_project_paths(target_project_dir=None) -> tuple[Path, Path, Path]:
    """Ermittelt dynamisch die Projektpfade relativ zum Skriptort oder Übergabeparameter."""
    if target_project_dir:
        base_project_dir = Path(target_project_dir).resolve()
    elif len(sys.argv) > 1 and sys.argv[1].strip():
        base_project_dir = Path(sys.argv[1]).resolve()
    else:
        script_dir = Path(__file__).resolve().parent
        base_project_dir = script_dir.parent if script_dir.name == "visum_processing" else script_dir.parent.parent.parent

    visum_dir = base_project_dir / "processed" / "visum"
    if not visum_dir.exists():
        visum_dir = base_project_dir
    osm_dir = base_project_dir / "processed" / "osm" / "04_network"
    if not osm_dir.exists():
        osm_dir = base_project_dir / "processed" / "osm"
    return base_project_dir, visum_dir, osm_dir


if __name__ == "__main__":
    base_dir, visum_input_dir, osm_input_dir = get_project_paths()

    zones_gpkg = base_dir / "processed" / "qgis_output" / "model6_ZoneAssembler" / "zones.gpkg"
    if not zones_gpkg.exists():
        zones_gpkg = visum_input_dir / "Zones.gpkg"

    mainzones_gpkg = base_dir / "processed" / "qgis_output" / "model6_ZoneAssembler" / "mainzones.gpkg"
    if not mainzones_gpkg.exists():
        mainzones_gpkg = visum_input_dir / "Mainzones.gpkg"

    osm_file = osm_input_dir / "road_network_hierarchical_modified.osm.pbf"
    if not osm_file.exists():
        osm_file = osm_input_dir / "road_network_hierarchical_modified.osm"
    if not osm_file.exists():
        osm_file = osm_input_dir / "hierarchical_network_modified.osm"

    if not zones_gpkg.exists():
        logging.error(f"Eingabedatei fehlt: {zones_gpkg}")
        sys.exit(1)

    if not osm_file.exists():
        logging.error(f"OSM-Datei fehlt: {osm_file}")
        sys.exit(1)

    visum_instance = run_import_links_and_zones(
        zones_gpkg=zones_gpkg,
        mainzones_gpkg=mainzones_gpkg,
        osm_file=osm_file,
        output_dir=visum_input_dir,
        importer_name="PANDO_Importer"
    )

    print("\n" + "=" * 60)
    print("SUCCESS: Schritt 1 & 2 abgeschlossen!")
    print("Erzeugte Dateien:")
    print(" 1. 01_Links_Imported.ver")
    print(" 2. 02_Zones_Imported.ver")
    print("=" * 60)