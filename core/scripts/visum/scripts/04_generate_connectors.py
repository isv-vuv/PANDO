import os
import sys
import logging
from pathlib import Path
import pandas as pd
import win32com.client as com

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
    stream=sys.stdout
)

USE_ISLAND_ID = False  # Auf True setzen, falls Insel-IDs ausgewertet werden sollen


def get_project_paths(target_project_dir=None) -> tuple[Path, Path, Path]:
    """Ermittelt dynamisch die Projektpfade relativ zum Skriptort oder Übergabeparameter."""
    if target_project_dir:
        base_project_dir = Path(target_project_dir).resolve()
    elif len(sys.argv) > 1 and sys.argv[1].strip():
        base_project_dir = Path(sys.argv[1]).resolve()
    else:
        script_dir = Path(__file__).resolve().parent
        base_project_dir = script_dir.parent if script_dir.name == "visum_processing" else script_dir.parent.parent.parent

    visum_input_dir = base_project_dir / "processed" / "visum"

    script_dir = Path(__file__).resolve().parent
    visum_helper_dir = script_dir.parent / "helper_files"
    return base_project_dir, visum_input_dir, visum_helper_dir


def find_helper_file(helper_dir: Path, base_dir: Path, filename: str) -> Path | None:
    """Sucht erst im Hilfsdatei-Ordner und danach rekursiv im Projektbaum."""
    core_name = filename
    if "_" in filename:
        parts = filename.split("_")
        if parts[0].isdigit() or parts[0].startswith("0"):
            core_name = "_".join(parts[1:])

    search_dirs = [helper_dir, base_dir]
    if helper_dir.parent.exists() and helper_dir.parent not in search_dirs:
        search_dirs.append(helper_dir.parent)

    for s_dir in search_dirs:
        if s_dir.exists():
            direct_match = list(s_dir.rglob(filename))
            if direct_match:
                return direct_match[0]
            for p in s_dir.rglob("*"):
                if p.is_file() and (core_name.lower() in p.name.lower() or filename.lower() in p.name.lower()):
                    return p

    return None


def open_visum():
    logging.info("Initialisiere Visum-Instanz...")
    visum = com.Dispatch("Visum.Visum.250")
    visum.Graphic.ShowMaximized()
    return visum


def add_uda(netobj, uda_name: str, value_type: int, def_val, comment: str = '', CanBeEmpty: bool = False,
            formula: str = None):
    """Erzeugt ein benutzerdefiniertes Attribut (UDA) oder Formel-Attribut in Visum."""
    try:
        is_formula_attribute = formula is not None
        netobj.AddUserDefinedAttribute(
            uda_name, uda_name, uda_name, value_type, 2, False, None, None,
            def_val, None, False,
            formula if is_formula_attribute else "",
            "", CanBeEmpty
        )
        uda = netobj.Attributes.ItemByKey(uda_name)
        uda.Comment = comment
        if not is_formula_attribute:
            netobj.SetAllAttValues(uda_name, def_val, OnlyActive=False)
            if value_type != 5:
                uda.ValueDefault = def_val
            else:
                uda.StringValueDefault = def_val
    except Exception as e:
        if "object already exists" in str(e).lower():
            logging.debug(f"UDA '{uda_name}' existiert bereits.")
        else:
            logging.error(f"Fehler beim Erstellen von UDA '{uda_name}': {e}")


def add_mat(visum, obj_id: int, mat_code: str, mat_name: str, objecttype: int = 2, matrixtype: int = 3):
    """Erzeugt eine Nachfragematrix in Visum."""
    M = visum.Net.AddMatrix(obj_id, objecttype, matrixtype)
    M.SetAttValue("Code", mat_code)
    M.SetAttValue("Name", mat_name if mat_name else mat_code)
    return M


def ensure_connector_layout_file(visum_helper_dir: Path) -> Path:
    """Erzeugt eine minimale Layout-Datei für effizienten Export der Anbindungstabelle."""
    visum_helper_dir.mkdir(parents=True, exist_ok=True)
    layout_file = visum_helper_dir / "Anb_MinLayout.net"
    if not layout_file.exists():
        with open(layout_file, "w", encoding="utf-8") as f:
            f.write(
                "$VISION\n$VERSION:VERSNR;FILETYPE;LANGUAGE;UNIT\n15;Net;ENG;KM\n\n* Tabelle: Anbindungen\n$CONNECTOR:ZONENO;NODENO;DIRECTION;TYPENO;TSYSSET\n")
    return layout_file


def calculate_zone_distances(visum, nf, use_island_logic: bool = False):
    """Berechnet die Distanz zum nächstgelegenen Zugangsknoten (ZK-Typ 1-4)."""
    if use_island_logic:
        logging.info("Starte Distanz- und Insel-Match-Berechnung für Bezirke...")
    else:
        logging.info("Starte Distanzberechnung für Bezirke zu Zugangsknoten (Typen 1 bis 4)...")

    uda_dist_name = "Distance_ZK_1-4_m"
    uda_island_match_name = "IS_SAME_ISLAND"

    add_uda(visum.Net.Zones, uda_dist_name, 2, 99999.9, "Direct distance [m] to nearest access node of type 1-4.")
    add_uda(visum.Net.Zones, uda_island_match_name, 1, 1 if not use_island_logic else 0,
            "1 if zone and nearest node are on same island.")

    nf.Init()
    if visum.Net.Nodes.AttrExists("ZK_TYP"):
        nf.AddCondition("OP_NONE", False, "ZK_TYP", "LessEqualVal", 4)

    reach_attr = "Reachable_Node" if visum.Net.Nodes.AttrExists("Reachable_Node") else "REACHABLE_NODE"
    if visum.Net.Nodes.AttrExists(reach_attr):
        nf.AddCondition("OP_AND", False, reach_attr, "EqualVal", 1)

    zone_attributes = ["NO", "XCOORD", "YCOORD"]
    if use_island_logic and visum.Net.Zones.AttrExists("Island_ID"):
        zone_attributes.append("Island_ID")

    zone_data = visum.Net.Zones.GetMultipleAttributes(zone_attributes)
    if not zone_data:
        logging.warning("Keine Bezirksdaten für Distanzberechnung gefunden.")
        nf.Init()
        return

    df_zones = pd.DataFrame(zone_data, columns=zone_attributes)
    map_matcher = visum.Net.CreateMapMatcher()
    MAX_RADIUS_METERS = 500 * 1000

    def get_nearest_node_info(zone_row):
        dist = 99999.9
        is_same_island = 1 if not use_island_logic else 0
        result = map_matcher.GetNearestNode(zone_row["XCOORD"], zone_row["YCOORD"], MAX_RADIUS_METERS, True)

        if result.Success:
            dist = result.Distance
            if use_island_logic and "Island_ID" in zone_row:
                try:
                    node_insel_id = int(result.Node.AttValue("NODE_ISLAND_ID"))
                    zone_insel_id = int(zone_row["Island_ID"])
                    if zone_insel_id == node_insel_id and zone_insel_id != -1:
                        is_same_island = 1
                except (ValueError, TypeError, AttributeError):
                    pass
        return pd.Series([dist, is_same_island])

    results_df = df_zones.apply(get_nearest_node_info, axis=1)
    results_df.columns = [uda_dist_name, uda_island_match_name]
    df_zones = pd.concat([df_zones, results_df], axis=1)

    visum_ids_data = visum.Net.Zones.GetMultiAttValues("NO")
    df_visum_ids = pd.DataFrame(visum_ids_data, columns=["VisumID", "NO"])
    df_zones_final = pd.merge(df_zones, df_visum_ids, on="NO")

    visum.Net.Zones.SetMultiAttValues(uda_dist_name, df_zones_final[['VisumID', uda_dist_name]].values.tolist())
    visum.Net.Zones.SetMultiAttValues(uda_island_match_name,
                                      df_zones_final[['VisumID', uda_island_match_name]].values.tolist())

    nf.Init()
    logging.info("Distanzberechnung abgeschlossen und Ergebnisse nach Visum geschrieben.")


def run_generate_connectors(target_project_dir=None, visum=None):
    logging.info("--- Visum Schritt 4: Anbindungserzeugung gestartet ---")
    base_project_dir, visum_input_dir, visum_helper_dir = get_project_paths(target_project_dir)

    ver_dir = visum_input_dir / "ver"
    ver_dir.mkdir(parents=True, exist_ok=True)
    output_ver = ver_dir / "05_Connectors_Generated.ver"

    procedure_xml = find_helper_file(visum_helper_dir, base_project_dir,
                                     "20230210_1erUmlegung_strtypbdl_TestAnbindungen.xml")
    if not procedure_xml:
        procedure_xml = find_helper_file(visum_helper_dir, base_project_dir,
                                         "20230814_1erUmlegung_TestAnbindungsknoten.xml")

    if not procedure_xml:
        logging.error(f"Keine passende 1er-Umlegungs-XML im Ordner '{visum_helper_dir}' gefunden!")
        sys.exit(1)

    logging.info(f"Verwende Anbindungs-Verfahrensdatei: {procedure_xml.name}")

    layout_file = ensure_connector_layout_file(visum_helper_dir)

    if visum is None:
        input_ver = ver_dir / "04_LinkTypes_Restructured.ver"
        if not input_ver.exists():
            input_ver = visum_input_dir / "04_LinkTypes_Restructured.ver"
        if not input_ver.exists():
            input_ver = ver_dir / "03_AccessNodes_Derived.ver"
        if not input_ver.exists():
            input_ver = visum_input_dir / "03_AccessNodes_Derived.ver"

        visum = open_visum()
        logging.info(f"Lade Version: {input_ver.name}")
        visum.LoadVersion(str(input_ver))

    lf = visum.Filters.LinkFilter()
    nf = visum.Filters.NodeFilter()
    zf = visum.Filters.ZoneFilter()
    cf = visum.Filters.ConnectorFilter()

    # 1. Distanzen berechnen
    calculate_zone_distances(visum, nf, use_island_logic=USE_ISLAND_ID)

    # 2. Zonen klassifizieren
    logging.info("Klassifiziere Bezirke in 'Near' und 'Far' basierend auf der Distanz.")
    uda_category_name = 'Zone_Category'
    add_uda(netobj=visum.Net.Zones, uda_name=uda_category_name, value_type=5, def_val="Far",
            comment='Zone classification: Near/Far to access nodes based on same-island distance.')

    distance_threshold = 5000.0  # 5 km

    zf.Init()
    zf.AddCondition("OP_NONE", False, "IS_SAME_ISLAND", "EqualVal", 1)
    zf.AddCondition("OP_AND", False, "Distance_ZK_1-4_m", "LessVal", distance_threshold)
    visum.Net.Zones.SetAllAttValues(uda_category_name, "Near", OnlyActive=True)
    zf.Init()

    # 3. Anbindungskonfigurationen (Exakt aus main_2.py)
    connector_config_near = {
        0: ['0', 0, 15000, 20],
        1: ['0,1', 1, 15000, 20],
        2: ['0,1,2', 2, 6000, 10],
        3: ['0,1,2,3', 3, 3000, 5],
        4: ['0,1,2,3,4', 4, 2000, 5],
        5: ['0,1,2,3,4,9', 5, 1000, 2]
    }

    connector_config_far = {
        9: ['0,1,2,3,4,5,9', 99, 99999999, 3]
    }

    node_types_to_try = [int(v[1]) for v in connector_config_near.values()]
    output_connectors_dir = visum_input_dir / "net"
    output_connectors_dir.mkdir(parents=True, exist_ok=True)

    addnetread_anbindungen = visum.IO.CreateAddNetReadController()
    addnetread_anbindungen.SetWhatToDo("Connector", 4)
    visum.UserPreferences.NetworkUserPreferences.SetAttValue("DEFAULTCONNECTORSPEED(PrT)", 20)

    visum.Net.Matrices.RemoveAll()
    matrix_1er = add_mat(visum, obj_id=1, mat_code="1erUmlegung_TestAnbindungen",
                         mat_name="1erUmlegung_TestAnbindungen")

    visum.Net.DemandSegments.RemoveAll()
    dseg_1erumlegung_name = "1erUmlegung_TestAnbindungen"

    pkw_code = "CAR"
    for m in visum.Net.Modes.GetAll:
        if {m.AttValue("NAME"), m.AttValue("CODE")} & {"Pkw", "P", "Car", "CAR", "C"}:
            pkw_code = m.AttValue("CODE")
            break

    dseg_1erumlegung = visum.Net.AddDemandSegment(dseg_1erumlegung_name, pkw_code)
    dseg_1erumlegung.SetAttValue("NAME", dseg_1erumlegung_name)
    dseg_1erumlegung.GetDemandDescription().SetAttValue("Matrix", 'Matrix([CODE]="' + dseg_1erumlegung_name + '")')
    visum.Procedures.Open(str(procedure_xml))

    add_uda(netobj=visum.Net.Zones, uda_name='Zone_Connected', value_type=9, def_val=False,
            comment='Temporary attribute: Is the zone connected in the current step?')

    reach_attr = "Reachable_Node" if visum.Net.Nodes.AttrExists("Reachable_Node") else "REACHABLE_NODE"

    def create_connectors_for_type(anb_typeno, filter_zentralitaet, anb_knoten_typnr, max_entfernung, anz_anb,
                                   merge_netfile_before_save=None):
        lf.Init()
        zf.AddCondition("OP_AND", False, "TYPENO", "ContainedIn", filter_zentralitaet)

        if visum.Net.Zones.CountActive == 0:
            return

        # 1. Erster Anbindungsversuch
        nf.Init()
        if visum.Net.Nodes.AttrExists("ZK_TYP"):
            nf.AddCondition("OP_NONE", False, "ZK_TYP", "LessEqualVal", anb_knoten_typnr)
        if visum.Net.Nodes.AttrExists(reach_attr):
            nf.AddCondition("OP_AND", False, reach_attr, "EqualVal", 1)

        visum.Net.CreateODConnectors(0, max_entfernung, anz_anb, anz_anb, anb_typeno)

        # 2. Iterative Retries für unangebundene Bezirke (NUMCONNECTORS == 0)
        zf.AddCondition("OP_AND", False, "NUMCONNECTORS", "EqualVal", 0)

        for nt in node_types_to_try:
            if nt <= anb_knoten_typnr:
                continue
            if visum.Net.Zones.CountActive == 0:
                break

            logging.info(
                f"{visum.Net.Zones.CountActive} Bezirk(e) ohne Anbindung an ZK-Typ {anb_knoten_typnr} ({max_entfernung} m) – erweitere auf Stufe {nt}..."
            )

            anb_knoten_typnr = nt
            nf.Init()
            if visum.Net.Nodes.AttrExists("ZK_TYP"):
                nf.AddCondition("OP_NONE", False, "ZK_TYP", "LessEqualVal", anb_knoten_typnr)
            if visum.Net.Nodes.AttrExists(reach_attr):
                nf.AddCondition("OP_AND", False, reach_attr, "EqualVal", 1)

            visum.Net.CreateODConnectors(0, max_entfernung, anz_anb, anz_anb, anb_typeno)

            if visum.Net.Zones.CountActive == 0:
                break

        # 3. Erweiterte Anbindungssuche für verbleibende Bezirke (Radius unbegrenzt, ZK_TYP <= 5)
        if visum.Net.Zones.CountActive > 0:
            logging.info(
                f"Erweiterte Anbindungssuche für {visum.Net.Zones.CountActive} verbleibende(n) Bezirk(e) (Radius unbegrenzt, ZK-Typ ≤ 5)..."
            )

            nf.Init()
            if visum.Net.Nodes.AttrExists("ZK_TYP"):
                nf.AddCondition("OP_NONE", False, "ZK_TYP", "LessEqualVal", 5)
            if visum.Net.Nodes.AttrExists(reach_attr):
                nf.AddCondition("OP_AND", False, reach_attr, "EqualVal", 1)

            visum.Net.CreateODConnectors(0, 99999999, anz_anb, anz_anb, anb_typeno)

        # 3b. Fallback für weit entfernte Bezirke an alle erreichbaren/aktiven Netzknoten (Radius unbegrenzt)
        if visum.Net.Zones.CountActive > 0:
            logging.info(
                f"Fallback-Anbindungssuche für {visum.Net.Zones.CountActive} verbleibende(n) Bezirk(e) an alle verfügbaren Netzknoten (Radius unbegrenzt)..."
            )
            nf.Init()
            if visum.Net.Nodes.AttrExists(reach_attr):
                nf.AddCondition("OP_NONE", False, reach_attr, "EqualVal", 1)
            visum.Net.CreateODConnectors(0, 99999999, anz_anb, anz_anb, anb_typeno)

        # 3c. Letzte Notfall-Anbindung an beliebige Netzknoten
        if visum.Net.Zones.CountActive > 0:
            logging.info(
                f"Notfall-Anbindungssuche für {visum.Net.Zones.CountActive} verbleibende(n) Bezirk(e) an beliebige Netzknoten..."
            )
            nf.Init()
            visum.Net.CreateODConnectors(0, 99999999, anz_anb, anz_anb, anb_typeno)

        # 4. Abbruch-Schranke: Warnung/Exception bei unangebundenen Zonen
        if visum.Net.Zones.CountActive > 0:
            raise Exception(f'Es verbleiben {visum.Net.Zones.CountActive} Bezirke, die absolut nicht angebunden werden konnten!')

        # 5. 1er-Testumlegung zur Ausdünnung
        visum.Net.Zones.SetAllAttValues('Zone_Connected', False, OnlyActive=False)
        zf.Init()
        zf.AddCondition("OP_NONE", False, "NUMCONNECTORS", "GreaterVal", 0)
        visum.Net.Zones.SetAllAttValues('Zone_Connected', True, OnlyActive=True)
        zf.Init()

        # 5. 1er-Testumlegung zur Ausdünnung
        formel = "If(FROM[Zone_Connected]=1 & TO[Zone_Connected]=1, 1, 0)"
        matrix_1er.SetValuesToResultOfFormula(formel)

        umlegung_erfolgreich = False
        try:
            visum.Procedures.Execute()
            umlegung_erfolgreich = True
        except Exception as proc_exc:
            logging.info(f"Hinweis bei Anbindungstest-Umlegung (Typ {anb_typeno}): {proc_exc}")

        # 6. Ungenutzte Anbindungen löschen (NUR wenn Umlegung erfolgreich war UND Zone behält mind. 1 Anbindung!)
        if umlegung_erfolgreich:
            cf.Init()
            cf.AddCondition("OP_NONE", False, "TYPENO", "EqualVal", anb_typeno)
            cf.AddCondition("OP_AND", False, "VOLVEHPRT(AP)", "EqualVal", 0)
            cf.AddCondition("OP_AND", False, r"REVERSECONNECTOR\VOLVEHPRT(AP)", "EqualVal", 0)
            cf.AddCondition("OP_AND", False, r"ZONE\NUMCONNECTORS", "GreaterVal", 1)
            visum.Net.Connectors.RemoveAll(OnlyActive=True)
            cf.Init()
        else:
            logging.info(
                f"Testumlegung für Typ {anb_typeno} unvollständig. "
                f"Behalte alle generierten Anbindungen ({visum.Net.Connectors.CountActive}) für diese Bezirke bei."
            )

        path_anb_type = output_connectors_dir / f'Anbindungen_Typ{anb_typeno}.net'

        if merge_netfile_before_save and merge_netfile_before_save.exists():
            logging.info(f"Führe Anbindungen aus '{merge_netfile_before_save.name}' vor dem Speichern hinzu.")
            visum.IO.LoadNet(NetFile=str(merge_netfile_before_save), ReadAdditive=True,
                             AddNetRead=addnetread_anbindungen)

        cf.Init()
        cf.AddCondition("OP_NONE", False, "TYPENO", "EqualVal", anb_typeno)

        logging.info(f"Speichere {visum.Net.Connectors.CountActive} Anbindungen in Datei {path_anb_type.name}...")
        visum.IO.SaveNet(NetFile=str(path_anb_type), LayoutFile=str(layout_file), ActiveNetElemsOnly=True)

        conn_uda_name = f"Anbindung_Typ{anb_typeno}"
        add_uda(visum.Net.Connectors, conn_uda_name, 1, 0, f'CFL connector type {anb_typeno}')

        cf.Init()
        cf.AddCondition("OP_NONE", False, "TYPENO", "EqualVal", anb_typeno)
        visum.Net.Connectors.SetAllAttValues(conn_uda_name, 1, OnlyActive=True)

        conn_atts4att = ["ZONENO", "NODENO", "DIRECTION", "TYPENO", "TSYSSET", conn_uda_name]
        conn_list = visum.Workbench.Lists.CreateConnectorList
        conn_list.SetObjects(True)
        for att in conn_atts4att:
            conn_list.AddColumn(att)
        conn_list.SaveToAttributeFile(str(path_anb_type.parent / f'{path_anb_type.stem}.att'), 59)
        visum.Net.Connectors.RemoveAll(OnlyActive=True)

    # 4. Anbindungen nacheinander erzeugen (Exakt analog main_2.py)
    visum.Net.Zones.SetAllAttValues('Zone_Connected', False, OnlyActive=False)

    logging.info("Erzeuge Anbindungen für nahe Bezirke ('Near')...")
    for anb_typeno, (filter_zentralitaet, anb_knoten_typnr, max_entf, anz) in connector_config_near.items():
        zf.Init()
        zf.AddCondition("OP_NONE", False, uda_category_name, "EqualVal", "Near")
        create_connectors_for_type(anb_typeno, filter_zentralitaet, anb_knoten_typnr, max_entf, anz)

    logging.info("Erzeuge Anbindungen für entfernte Bezirke ('Far')...")
    visum.Net.Zones.SetAllAttValues('Zone_Connected', False, OnlyActive=False)
    zf.Init()
    zf.AddCondition("OP_NONE", False, uda_category_name, "EqualVal", "Near")
    visum.Net.Zones.SetAllAttValues('Zone_Connected', False, OnlyActive=True)
    zf.Init()

    for anb_typeno, (filter_zentralitaet, anb_knoten_typnr, max_entf, anz) in connector_config_far.items():
        zf.Init()
        zf.AddCondition("OP_NONE", False, uda_category_name, "EqualVal", "Far")

        path_to_merge = None
        potential_existing_file = output_connectors_dir / f'Anbindungen_Typ{anb_typeno}.net'
        if potential_existing_file.exists():
            path_to_merge = potential_existing_file

        create_connectors_for_type(anb_typeno, filter_zentralitaet, anb_knoten_typnr, max_entf, anz,
                                   merge_netfile_before_save=path_to_merge)

    zf.Init()

    # 5. Alle erzeugten Anbindungen wieder einlesen
    logging.info("Lade alle generierten Anbildungsdateien ins Modell...")
    all_connector_types = list(connector_config_near.keys()) + list(connector_config_far.keys())
    read_anb_typenos = sorted(list(set(all_connector_types)), reverse=True)

    for anb_typeno in read_anb_typenos:
        path_anb_type = output_connectors_dir / f'Anbindungen_Typ{anb_typeno}.net'
        if path_anb_type.exists():
            visum.IO.LoadNet(NetFile=str(path_anb_type), ReadAdditive=True, AddNetRead=addnetread_anbindungen)

    for anb_typeno in read_anb_typenos:
        path_anb_type_att = output_connectors_dir / f'Anbindungen_Typ{anb_typeno}.att'
        if path_anb_type_att.exists():
            visum.IO.LoadAttributeFile(str(path_anb_type_att))

    # 6. Finale 1:1 Prüfung über das gesamte Netz
    logging.info("Finale Prüfung: Full 1-to-1 Assignment...")
    matrix_1er.SetValuesToResultOfFormula("1")
    try:
        visum.Procedures.Execute()
        logging.info("Finale Zuordnung erfolgreich. Alle Bezirke sind vollständig angebunden.")
    except Exception as exc:
        logging.info(f"Test-Umlegung durchgeführt (Hinweis: {exc})")

    # 7. Aufräumen & Zwischenattribute löschen
    logging.info("Bereinige temporäre Nachfrage- und Analyseobjekte...")
    visum.Net.Matrices.RemoveAll()
    visum.Net.DemandSegments.RemoveAll()

    udas_to_delete = ["Distance_ZK_1-4_m", "IS_SAME_ISLAND", 'Zone_Connected', "Zone_Category"]
    for uda_name in udas_to_delete:
        try:
            if visum.Net.Zones.Attributes.ItemByKey(uda_name) is not None:
                visum.Net.Zones.DeleteUserDefinedAttribute(uda_name)
        except Exception:
            pass

    init_xml = find_helper_file(visum_helper_dir, base_project_dir, "init.xml")
    if init_xml:
        try:
            visum.Procedures.Open(str(init_xml))
            visum.Procedures.Execute()
            logging.info("Init.xml erfolgreich ausgeführt.")
        except Exception as e:
            logging.debug(f"Init.xml Hinweis: {e}")

    logging.info(f"Speichere finale Version unter: {output_ver.name}")
    visum.SaveVersion(str(output_ver))

    return visum


run_connector_generation = run_generate_connectors


if __name__ == "__main__":
    visum_instance = run_generate_connectors()
    print("\n" + "=" * 60)
    print("Erfolg: Anbindungen erfolgreich erzeugt!")
    print("Gespeicherte Datei: 05_Connectors_Generated.ver")
    print("=" * 60)
    input("\n[Hinweis] Visum bleibt geöffnet. Drücke ENTER im Terminal zum Beenden...")