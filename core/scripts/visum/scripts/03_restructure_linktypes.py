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


def get_project_paths(target_project_dir=None) -> tuple[Path, Path]:
    """Ermittelt dynamisch die Projektpfade relativ zum Skriptort oder Übergabeparameter."""
    if target_project_dir:
        base_project_dir = Path(target_project_dir).resolve()
    elif len(sys.argv) > 1 and sys.argv[1].strip():
        base_project_dir = Path(sys.argv[1]).resolve()
    else:
        script_dir = Path(__file__).resolve().parent
        base_project_dir = script_dir.parent if script_dir.name == "visum_processing" else script_dir.parent.parent.parent

    visum_input_dir = base_project_dir / "processed" / "visum"
    return base_project_dir, visum_input_dir


def open_visum():
    logging.info("Initialisiere Visum-Instanz...")
    visum = com.Dispatch("Visum.Visum.250")
    visum.Graphic.ShowMaximized()
    return visum


def run_restructure_link_types(target_project_dir=None, visum=None):
    logging.info("--- Visum Schritt 3: Umstrukturierung der Streckentypen gestartet ---")
    base_project_dir, visum_input_dir = get_project_paths(target_project_dir)

    ver_dir = visum_input_dir / "ver"
    ver_dir.mkdir(parents=True, exist_ok=True)
    output_ver = ver_dir / "04_LinkTypes_Restructured.ver"

    # Dynamische Suche nach der Linktypes-Master-Datei
    linktype_files = list(visum_input_dir.glob("*_export_excel_linktypes.net"))
    if not linktype_files:
        script_dir = Path(__file__).resolve().parent
        helper_net = script_dir.parent / "helper_files" / "master_linktypes.net"
        if helper_net.exists():
            linktype_files = [helper_net]
        else:
            helper_files = list((script_dir.parent / "helper_files").glob("*.net"))
            if helper_files:
                linktype_files = helper_files
    if not linktype_files:
        raise FileNotFoundError(
            f"Keine Linktypes-Datei im Format '*_export_excel_linktypes.net' oder 'master_linktypes.net' gefunden!"
        )

    master_net_path = max(linktype_files, key=lambda p: p.stat().st_mtime)
    logging.info(f"Verwende Master-Linktypes-Datei: {master_net_path.name}")

    if visum is None:
        input_ver = ver_dir / "03_AccessNodes_Derived.ver"
        if not input_ver.exists():
            input_ver = visum_input_dir / "03_AccessNodes_Derived.ver"
        if not input_ver.exists():
            input_ver = ver_dir / "02_Zones_Imported.ver"
        if not input_ver.exists():
            input_ver = visum_input_dir / "02_Zones_Imported.ver"
        logging.info(f"Starte Visum und lade Version: {input_ver.name}")
        visum = open_visum()
        visum.LoadVersion(str(input_ver))

    logging.info("Starte Streckentypen-Neustrukturierung...")

    # Phase 0: Löschen überflüssiger Hilfsstrecken aus Schritt 02
    logging.info("Phase 0: Lösche überflüssige Hilfsstrecken ('ZK_Hilfsstrecken_Überflüssig')...")
    lf = visum.Filters.LinkFilter()
    try:
        old_link_types = visum.Net.LinkTypes.GetMultipleAttributes(["NO", "NAME"])
        typeno_to_delete = next((no for no, name in old_link_types if name == "ZK_Hilfsstrecken_Überflüssig"), None)
        if typeno_to_delete is not None:
            lf.Init()
            lf.AddCondition("OP_NONE", False, "TYPENO", "ContainedIn", str(typeno_to_delete))
            count = visum.Net.Links.CountActive
            if count > 0:
                visum.Net.Links.RemoveAll(OnlyActive=True)
                logging.info(f"  -> {count} überflüssige Hilfsstrecken gelöscht.")
            else:
                logging.info("  -> Keine überflüssigen Hilfsstrecken gefunden.")
    except Exception as e:
        logging.warning(f"Hinweis beim Bereinigen von Hilfsstrecken: {e}")
    finally:
        lf.Init()

    # Phase 1: Master-Streckentypen einlesen und Regelwerk aufbauen
    logging.info("Phase 1: Lade Ziel-Streckentypen aus Master-Netzdatei...")
    visum.IO.LoadNet(str(master_net_path), True)

    master_attributes = ["NO", "NAME", "NUMLANES", "V0PRT"]
    all_types_data = visum.Net.LinkTypes.GetMultipleAttributes(master_attributes)
    df_all_types = pd.DataFrame(all_types_data, columns=["TYPENO", "NAME", "NUMLANES", "V0PRT"])

    df_new_types = df_all_types[df_all_types['TYPENO'] >= 100].copy()
    df_new_types = df_new_types[[("bus" not in str(x).lower()) for x in df_new_types['NAME']]].copy()
    df_new_types.dropna(subset=["TYPENO", "NAME"], inplace=True)
    df_new_types = df_new_types.astype({"TYPENO": int, "NUMLANES": int, "V0PRT": float})

    df_old_types = df_all_types[df_all_types['TYPENO'] < 100].copy()
    logging.info(f"  -> Regelwerk mit {len(df_new_types)} Ziel-Streckentypen (TypNo >= 100) geladen.")

    # Namenszuordnung aufschlüsseln
    cat_map_abbr = {
        "Mo": "Motorway", "Tr": "Trunk", "Pr": "Primary", "Se": "Secondary", "Te": "Tertiary",
        "Un": "Unclassified", "Re": "Residential", "Link Mo": "Motorway_link", "Link Tr": "Trunk_link",
        "Link Pr": "Primary_link", "Link Se": "Secondary_link", "Link Te": "Tertiary_link"
    }
    cat_map_full = {
        "Motorway, 4 lanes": "Motorway", "Motorway, 3 lanes": "Motorway", "Motorway, 2 lanes": "Motorway",
        "Motorway, 1 lane": "Motorway", "Motorway_link, 2 lanes": "Motorway_link",
        "Motorway_link, 1 lane": "Motorway_link",
        "Motorway_link": "Motorway_link", "Trunk, 3 lanes": "Trunk", "Trunk, 2 lanes": "Trunk",
        "Trunk, 1 lane": "Trunk",
        "Trunk_link, 2 lanes": "Trunk_link", "Trunk_link, 1 lane": "Trunk_link", "Trunk_link": "Trunk_link",
        "Primary, 3 lanes": "Primary", "Primary, 2 lanes": "Primary", "Primary, 1 lane": "Primary",
        "Primary_link": "Primary_link", "Secondary, 2 lanes": "Secondary", "Secondary, 1 lane": "Secondary",
        "Secondary_link": "Secondary_link", "Tertiary, 2 lanes": "Tertiary", "Tertiary, 1 lane": "Tertiary",
        "Tertiary_link": "Tertiary_link", "Unclassified, 1 lane": "Unclassified", "Blocked Oneway": "Blocked Oneway",
        "Construction": "Construction", "Residential": "Residential", "Road": "Road", "Living_Street": "Living_Street",
        "Service": "Service", "Footway/Pedestrian": "Footway/Pedestrian", "Steps": "Steps", "Path": "Path",
        "Cycleway": "Cycleway", "Track": "Track", "General rail": "General rail", "Rail": "Rail",
        "Light rail": "Light rail", "Subway": "Subway", "Tram": "Tram", "Ferry": "Ferry",
        "Ferry_Access": "Ferry_Access", "UnknownByImporter(Default)": "UnknownByImporter",
        "ZK_Hilfsstrecken": "ZK_Hilfsstrecken"
    }

    def get_base_name(name, is_new_type):
        if is_new_type:
            if name in cat_map_full:
                return cat_map_full[name]
            parts = name.split(" ")
            if len(parts) > 1 and f"{parts[0]} {parts[1]}" in cat_map_abbr:
                return cat_map_abbr[f"{parts[0]} {parts[1]}"]
            if parts[0] in cat_map_abbr:
                return cat_map_abbr[parts[0]]
            return name
        else:
            return cat_map_full.get(name, "Unknown")

    df_new_types["OSM_NAME"] = df_new_types["NAME"].apply(lambda x: get_base_name(x, is_new_type=True))
    df_old_types["OSM_NAME"] = df_old_types["NAME"].apply(lambda x: get_base_name(x, is_new_type=False))
    old_typenos_by_cat = df_old_types.groupby('OSM_NAME')['TYPENO'].apply(list).to_dict()

    # Phase 2: Iterative Filter-Zuweisung DIREKT über Visum-Standardattribute (NUMLANES & V0PRT)
    logging.info("Phase 2: Führe Zuweisung über native Visum-Attribute ('NUMLANES' & 'V0PRT') aus...")
    structured_categories = {
        "Motorway", "Trunk", "Primary", "Secondary", "Tertiary", "Unclassified",
        "Motorway_link", "Trunk_link", "Primary_link", "Secondary_link", "Tertiary_link"
    }
    df_structured_rules = df_new_types[df_new_types['OSM_NAME'].isin(structured_categories)]

    processing_order = [
        "Motorway", "Motorway_link", "Primary", "Primary_link", "Trunk", "Trunk_link",
        "Secondary", "Secondary_link", "Tertiary", "Tertiary_link", "Unclassified"
    ]

    for category_name in processing_order:
        if category_name not in df_structured_rules['OSM_NAME'].unique():
            continue

        category_df = df_structured_rules[df_structured_rules['OSM_NAME'] == category_name]
        old_typenos = old_typenos_by_cat.get(category_name)
        if not old_typenos:
            continue

        total_assigned_in_cat = 0
        lane_counts = sorted(category_df['NUMLANES'].unique(), reverse=True)

        for i, num_lanes in enumerate(lane_counts):
            lane_specific_types = category_df[category_df['NUMLANES'] == num_lanes].sort_values('V0PRT', ascending=False)
            speed_steps = lane_specific_types['V0PRT'].tolist()

            for j, v0prt in enumerate(speed_steps):
                rule_row = lane_specific_types.iloc[j]
                new_typeno = int(rule_row['TYPENO'])

                upper_bound = float('inf') if j == 0 else (v0prt + speed_steps[j - 1]) / 2
                lower_bound = 0 if j == len(speed_steps) - 1 else (v0prt + speed_steps[j + 1]) / 2

                lf.Init()
                lf.AddCondition("OP_NONE", False, "TYPENO", "ContainedIn", ",".join(map(str, old_typenos)))

                # DIREKTER FILTER AUF DAS VISUM-STANDARDATTRIBUT 'NUMLANES'
                if i == 0:
                    lf.AddCondition("OP_AND", False, "NUMLANES", "GreaterEqualVal", num_lanes)
                else:
                    lf.AddCondition("OP_AND", False, "NUMLANES", "EqualVal", num_lanes)

                # DIREKTER FILTER AUF DAS VISUM-STANDARDATTRIBUT 'V0PRT'
                lf.AddCondition("OP_AND", False, "V0PRT", "GreaterEqualVal", lower_bound)
                if upper_bound != float('inf'):
                    lf.AddCondition("OP_AND", False, "V0PRT", "LessVal", upper_bound)

                active_count = visum.Net.Links.CountActive
                if active_count > 0:
                    visum.Net.Links.SetAllAttValues("TYPENO", new_typeno, OnlyActive=True)
                    total_assigned_in_cat += active_count

        logging.info(f"  -> Kategorie '{category_name:15s}': {total_assigned_in_cat:6d} Strecken zugewiesen.")

    # Phase 3: 1:1 Zuweisung für einfache Nebennetze
    logging.info("Phase 3: Führe 1:1 Zuweisung für einfache Streckentypen aus...")
    df_simple_rules = df_new_types[~df_new_types['OSM_NAME'].isin(structured_categories)]
    simple_map = df_simple_rules.drop_duplicates(subset=['OSM_NAME']).set_index('OSM_NAME')['TYPENO'].to_dict()

    for osm_name, new_typeno in simple_map.items():
        old_typenos = old_typenos_by_cat.get(osm_name)
        if not old_typenos:
            continue

        lf.Init()
        lf.AddCondition("OP_NONE", False, "TYPENO", "ContainedIn", ",".join(map(str, old_typenos)))
        active_count = visum.Net.Links.CountActive
        if active_count > 0:
            visum.Net.Links.SetAllAttValues("TYPENO", new_typeno, OnlyActive=True)
            logging.info(f"  -> Kategorie '{osm_name:15s}': {active_count:6d} Strecken -> Typ {new_typeno}")

    # Phase 4: Bereinigung & Qualitätsprüfung
    logging.info("Phase 4: Finale Bereinigung & Qualitätsprüfung...")
    blocked_oneway_type_df = df_new_types[df_new_types['OSM_NAME'] == 'Blocked Oneway']

    if not blocked_oneway_type_df.empty:
        blocked_oneway_typeno = int(blocked_oneway_type_df.iloc[0]['TYPENO'])
        all_old_typenos = df_old_types['TYPENO'].tolist()

        # Prüfe nur noch auf echte 0-Spuren im Visum-Standardattribut NUMLANES
        lf.Init()
        lf.AddCondition("OP_NONE", False, "TYPENO", "ContainedIn", ",".join(map(str, all_old_typenos)))
        lf.AddCondition("OP_AND", False, "NUMLANES", "EqualVal", 0)

        active_count = visum.Net.Links.CountActive
        if active_count > 0:
            visum.Net.Links.SetAllAttValues("TYPENO", blocked_oneway_typeno, OnlyActive=True)
            logging.info(
                f"  -> {active_count} gesperrte Gegenrichtungen (NUMLANES=0) auf 'Blocked Oneway' (Typ {blocked_oneway_typeno}) gesetzt."
            )

    # Restkontrolle
    all_old_typenos = df_old_types['TYPENO'].tolist()
    lf.Init()
    lf.AddCondition("OP_NONE", False, "TYPENO", "ContainedIn", ",".join(map(str, all_old_typenos)))
    remaining_count = visum.Net.Links.CountActive
    if remaining_count > 0:
        logging.warning(f"  -> ACHTUNG: {remaining_count} Strecken besitzen noch unberücksichtigte alte Streckentypen!")
    else:
        logging.info("  -> Alle Strecken im Netz wurden erfolgreich neu klassifiziert.")

    lf.Init()
    logging.info(f"Speichere neu strukturierte Version unter: {output_ver.name}")
    visum.SaveVersion(str(output_ver))

    return visum


if __name__ == "__main__":
    visum_instance = run_restructure_link_types()
    print("\n" + "=" * 60)
    print("Erfolg: Streckentypen erfolgreich neu strukturiert!")
    print("Gespeicherte Datei: 04_LinkTypes_Restructured.ver")
    print("=" * 60)
    input("\n[Hinweis] Visum bleibt geöffnet. Drücke ENTER im Terminal zum Beenden...")