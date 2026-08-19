import os
import subprocess
import re
import shutil
import xml.etree.ElementTree as ET
import pandas as pd
import numpy as np


# ==============================================================================
# 1. STRUKTURANALYSE DES MASTER-NETZES (Für die Bracket-Berechnung)
# ==============================================================================

def parse_net_link_types(master_net_path, log=print):
    """Liest die $LINKTYPE-Tabelle direkt aus der Master-.net-Textdatei."""
    log(f"Analysiere Master-Netzdatei für Klassen-Bracketing: {master_net_path}")
    with open(master_net_path, 'r', encoding='utf-8', errors='ignore') as f:
        lines = f.readlines()

    link_type_lines = []
    headers = []
    is_table = False
    for line in lines:
        line_str = line.strip()
        if line_str.startswith('$LINKTYPE:'):
            is_table = True
            headers = [h.strip() for h in line_str.replace('$LINKTYPE:', '').split(';')]
            continue
        if is_table:
            if line_str.startswith('$') or line_str.startswith('*') or not line_str:
                if link_type_lines: break
                continue
            link_type_lines.append([v.strip() for v in line_str.split(';')])

    df = pd.DataFrame(link_type_lines, columns=headers[:len(link_type_lines[0])])
    df['TYPENO'] = pd.to_numeric(df['NO'], errors='coerce').fillna(0).astype(int)
    df['NUMLANES'] = pd.to_numeric(df['NUMLANES'], errors='coerce').fillna(1).astype(int)
    df['V0PRT'] = pd.to_numeric(df['V0PRT'], errors='coerce').fillna(30).astype(float)
    return df[['TYPENO', 'NAME', 'NUMLANES', 'V0PRT']]


def calculate_dynamic_brackets(df_all_types):
    """Berechnet die mathematischen Klassengrenzen (Ober-/Untergrenzen) der Geschwindigkeiten."""
    df_new_types = df_all_types[df_all_types['TYPENO'] >= 100].copy()
    # Avoid pandas' Arrow string regex backend here. Some supported QGIS
    # installations bundle pandas with an older PyArrow whose compute module
    # does not yet provide match_substring_regex.
    non_bus_rows = ["bus" not in str(value).casefold() for value in df_new_types["NAME"]]
    df_new_types = df_new_types.loc[non_bus_rows].copy()

    cat_map_abbr = {
        "Mo": "Motorway", "Tr": "Trunk", "Pr": "Primary", "Se": "Secondary", "Te": "Tertiary",
        "Un": "Unclassified", "Re": "Residential", "Link Mo": "Motorway_link", "Link Tr": "Trunk_link",
        "Link Pr": "Primary_link", "Link Se": "Secondary_link", "Link Te": "Tertiary_link"
    }

    def get_base_name(name):
        parts = name.split(" ")
        if len(parts) > 1 and f"{parts[0]} {parts[1]}" in cat_map_abbr:
            return cat_map_abbr[f"{parts[0]} {parts[1]}"]
        if parts[0] in cat_map_abbr:
            return cat_map_abbr[parts[0]]
        return name

    df_new_types["OSM_NAME"] = df_new_types["NAME"].apply(get_base_name)
    brackets_db = {}

    for category_name in df_new_types['OSM_NAME'].unique():
        category_df = df_new_types[df_new_types['OSM_NAME'] == category_name]
        brackets_db[category_name] = {}

        for num_lanes in category_df['NUMLANES'].unique():
            lane_specific_types = category_df[category_df['NUMLANES'] == num_lanes].sort_values('V0PRT',
                                                                                                ascending=False)
            speed_steps = lane_specific_types['V0PRT'].tolist()
            brackets_db[category_name][num_lanes] = []

            for j, v0prt in enumerate(speed_steps):
                upper_bound = float('inf') if j == 0 else (v0prt + speed_steps[j - 1]) / 2
                lower_bound = 0 if j == len(speed_steps) - 1 else (v0prt + speed_steps[j + 1]) / 2

                brackets_db[category_name][num_lanes].append({
                    'target_speed': int(v0prt),
                    'target_lanes': int(num_lanes),
                    'lower_bound': lower_bound,
                    'upper_bound': upper_bound
                })

    return brackets_db


# ==============================================================================
# 2. TOPOLOGISCHE KONTEXT-IMPUTATION & STRUKTURIERTE LOG-GENERIERUNG
# ==============================================================================

def impute_and_classify_osm(input_xml_path, output_xml_path, brackets_db, log=print):
    """Berechnet Mittelwerte, analysiert Nachbarschaften topologisch und harmonisiert das Netz."""
    log("Analysiere Netzwerktopologie und berechne regionale Kennwerte...")
    tree = ET.parse(input_xml_path)
    root = tree.getroot()

    osm_speeds, osm_lanes = {}, {}
    node_to_ways = {}  # Intersection dictionary: node_id -> list of way_ids
    original_ways_data = {}  # Unaltered memory snapshot of the raw OSM network

    # Pass 1: Build topological network map and gather global type stats
    for way in root.findall('way'):
        way_id = way.get('id')
        tags = {tag.get('k'): tag.get('v') for tag in way.findall('tag')}
        nodes = [nd.get('ref') for nd in way.findall('nd')]

        if 'highway' not in tags: continue
        hw = tags['highway']

        original_ways_data[way_id] = {'tags': tags, 'nodes': nodes, 'hw': hw}

        # Connect nodes to way elements
        for node_ref in nodes:
            if node_ref not in node_to_ways:
                node_to_ways[node_ref] = []
            node_to_ways[node_ref].append(way_id)

        # Collect values for arithmetic regional average
        if hw not in osm_speeds: osm_speeds[hw], osm_lanes[hw] = [], []
        if 'maxspeed' in tags:
            digits = re.findall(r'\d+', tags['maxspeed'])
            if digits: osm_speeds[hw].append(int(digits[0]))
        if 'lanes' in tags:
            digits = re.findall(r'\d+', tags['lanes'])
            if digits: osm_lanes[hw].append(int(digits[0]))

    # Compute static regional average thresholds
    global_fallbacks = {'motorway': (130, 2), 'trunk': (100, 2), 'primary': (100, 2), 'secondary': (80, 2),
                        'tertiary': (70, 1), 'unclassified': (50, 1), 'residential': (30, 1)}
    mean_rules = {}
    for hw in set(osm_speeds.keys()).union(global_fallbacks.keys()):
        speeds, lanes = osm_speeds.get(hw, []), osm_lanes.get(hw, [])
        avg_speed = float(np.mean(speeds)) if speeds else float(global_fallbacks.get(hw, (130, 2))[0])
        avg_lanes = float(np.mean(lanes)) if lanes else float(global_fallbacks.get(hw, (130, 2))[1])
        mean_rules[hw] = {'speed': avg_speed, 'lanes': max(1.0, avg_lanes)}

    osm_to_cat_map = {'motorway': 'Motorway', 'motorway_link': 'Motorway_link', 'trunk': 'Trunk',
                      'trunk_link': 'Trunk_link', 'primary': 'Primary', 'primary_link': 'Primary_link',
                      'secondary': 'Secondary', 'secondary_link': 'Secondary_link', 'tertiary': 'Tertiary',
                      'tertiary_link': 'Tertiary_link', 'unclassified': 'Unclassified', 'residential': 'Residential'}

    # Pass 2: Neighborhood network scanning, outlier filtration & structured logging
    modified_ways = 0
    for way in root.findall('way'):
        way_id = way.get('id')
        if way_id not in original_ways_data: continue

        w_data = original_ways_data[way_id]
        hw = w_data['hw']
        orig_tags = w_data['tags']
        nodes = w_data['nodes']

        fallback = mean_rules.get(hw, {'speed': 30.0, 'lanes': 1.0})
        cat_name = osm_to_cat_map.get(hw, hw.capitalize())

        orig_speed_str = orig_tags.get('maxspeed', 'missing')
        orig_lanes_str = orig_tags.get('lanes', 'missing')

        # ----------------------------------------------------------------------
        # MAXSPEED PROCESSING ENGINE
        # ----------------------------------------------------------------------
        if orig_speed_str != 'missing':
            try:
                raw_speed = int(re.findall(r'\d+', str(orig_speed_str))[0])
                if raw_speed > 250:  # Unplausible threshold
                    current_speed = int(fallback['speed'])
                    speed_log = f"[OSM-Unplausible] Raw: {orig_speed_str} km/h -> Capped to Template-Max:"
                else:
                    current_speed = raw_speed
                    speed_log = f"[OSM-Original] {orig_speed_str} km/h -> Template-Matched:"
            except:
                current_speed = int(fallback['speed'])
                speed_log = f"[OSM-Original] Invalid ({orig_speed_str}) -> Fallback to Regional Avg ({fallback['speed']:.1f} km/h) -> Template-Matched:"
        else:
            # Spatial Neighborhood Query
            neighbor_speed = None
            chosen_neighbor_id = None
            if nodes:
                endpoints = [nodes[0], nodes[-1]] if len(nodes) >= 2 else nodes
                connected_ways = set()
                for ep in endpoints:
                    if ep in node_to_ways: connected_ways.update(node_to_ways[ep])
                connected_ways.discard(way_id)

                for n_id in connected_ways:
                    n_data = original_ways_data.get(n_id)
                    if n_data and osm_to_cat_map.get(n_data['hw']) == cat_name:
                        n_speed_str = n_data['tags'].get('maxspeed', 'missing')
                        if n_speed_str != 'missing':
                            try:
                                neighbor_speed = int(re.findall(r'\d+', str(n_speed_str))[0])
                                chosen_neighbor_id = n_id
                                break
                            except:
                                pass

            if neighbor_speed is not None:
                # Plausibility check against regional mean (Delta max 30 km/h)
                if abs(neighbor_speed - fallback['speed']) <= 30:
                    current_speed = neighbor_speed
                    speed_log = f"[Neighbor-Match] From Neighbor ID {chosen_neighbor_id} ({neighbor_speed} km/h) -> Template-Matched:"
                else:
                    current_speed = int(fallback['speed'])
                    speed_log = f"[Neighbor-No-Match] Neighbor ID {chosen_neighbor_id} ({neighbor_speed} km/h) rejected vs. Regional Avg ({fallback['speed']:.1f} km/h) -> Regional Fallback applied -> Template-Matched:"
            else:
                current_speed = int(fallback['speed'])
                speed_log = f"[Regional-Average] Type '{hw}' regional mean = {fallback['speed']:.1f} km/h -> Template-Matched:"

        # ----------------------------------------------------------------------
        # LANES PROCESSING ENGINE
        # ----------------------------------------------------------------------
        if orig_lanes_str != 'missing':
            try:
                raw_lanes = int(re.findall(r'\d+', str(orig_lanes_str))[0])
                if raw_lanes > 8:  # Unplausible threshold
                    current_lanes = int(np.round(fallback['lanes']))
                    lanes_log = f"[OSM-Unplausible] Raw: {orig_lanes_str} lanes -> Capped to Template-Max:"
                else:
                    current_lanes = raw_lanes
                    lanes_log = f"[OSM-Original] {orig_lanes_str} lanes -> Template-Matched:"
            except:
                current_lanes = int(np.round(fallback['lanes']))
                lanes_log = f"[OSM-Original] Invalid ({orig_lanes_str}) -> Fallback to Regional Avg ({fallback['lanes']:.1f} lanes) -> Template-Matched:"
        else:
            # Spatial Neighborhood Query
            neighbor_lanes = None
            chosen_neighbor_id = None
            if nodes:
                endpoints = [nodes[0], nodes[-1]] if len(nodes) >= 2 else nodes
                connected_ways = set()
                for ep in endpoints:
                    if ep in node_to_ways: connected_ways.update(node_to_ways[ep])
                connected_ways.discard(way_id)

                for n_id in connected_ways:
                    n_data = original_ways_data.get(n_id)
                    if n_data and osm_to_cat_map.get(n_data['hw']) == cat_name:
                        n_lanes_str = n_data['tags'].get('lanes', 'missing')
                        if n_lanes_str != 'missing':
                            try:
                                neighbor_lanes = int(re.findall(r'\d+', str(n_lanes_str))[0])
                                chosen_neighbor_id = n_id
                                break
                            except:
                                pass

            if neighbor_lanes is not None:
                # Plausibility check against regional mean (Delta max 2 lanes)
                if abs(neighbor_lanes - fallback['lanes']) <= 2:
                    current_lanes = neighbor_lanes
                    lanes_log = f"[Neighbor-Match] From Neighbor ID {chosen_neighbor_id} ({neighbor_lanes} lanes) -> Template-Matched:"
                else:
                    current_lanes = int(np.round(fallback['lanes']))
                    lanes_log = f"[Neighbor-No-Match] Neighbor ID {chosen_neighbor_id} ({neighbor_lanes} lanes) rejected vs. Regional Avg ({fallback['lanes']:.1f} lanes) -> Regional Fallback applied -> Template-Matched:"
            else:
                rounded_lanes = int(np.round(fallback['lanes']))
                current_lanes = rounded_lanes
                lanes_log = f"[Regional-Average] Type '{hw}' regional mean = {fallback['lanes']:.1f} lanes (rounded: {rounded_lanes}) -> Template-Matched:"

        # ----------------------------------------------------------------------
        # BRACKET MATCHING & ADJUSTMENT TO FIXED TEMPLATE LINK TYPES
        # ----------------------------------------------------------------------
        target_speed, target_lanes = current_speed, current_lanes
        if cat_name in brackets_db:
            available_lanes = brackets_db[cat_name]
            chosen_lanes = current_lanes if current_lanes in available_lanes else max(available_lanes.keys(),
                                                                                      default=current_lanes)
            if chosen_lanes in available_lanes:
                for bracket in available_lanes[chosen_lanes]:
                    if bracket['lower_bound'] <= current_speed < bracket['upper_bound']:
                        target_speed = bracket['target_speed']
                        target_lanes = bracket['target_lanes']
                        break

        # Append structured resolution text string
        maxspeed_source = f"{speed_log} {target_speed} km/h"
        lanes_source = f"{lanes_log} {target_lanes} lanes"

        # Update XML elements
        tags_dict = {tag.get('k'): tag for tag in way.findall('tag')}
        tags_to_write = [
            ('maxspeed', target_speed),
            ('lanes', target_lanes),
            ('maxspeed_original', orig_speed_str),
            ('lanes_original', orig_lanes_str),
            ('maxspeed_source', maxspeed_source),
            ('lanes_source', lanes_source)
        ]

        for key, final_val in tags_to_write:
            if key in tags_dict:
                tags_dict[key].set('v', str(final_val))
            else:
                way.append(ET.Element('tag', k=key, v=str(final_val)))
            modified_ways += 1

    tree.write(output_xml_path, encoding='utf-8', xml_declaration=True)
    log(f"Bearbeitete Netzdatei erfolgreich aktualisiert: {output_xml_path}")


# ==============================================================================
# 3. OSMIUM PIPELINE EXECUTIVE
# ==============================================================================

def build_hierarchical_network(merged_pbf, osm_dir, osmium_exe, bin_dir, poly_pa_ia1, poly_ia2, master_net_path,
                               template_cfg_path=None, template_xml_path=None, *, output_original=None,
                               output_modified=None, run_command=None, log=print, progress=None):
    progress = progress or (lambda _name, _index, _total: None)
    total_steps = 11
    path_poly_pa_ia1 = poly_pa_ia1 if os.path.isabs(poly_pa_ia1) else os.path.join(osm_dir, poly_pa_ia1)
    path_poly_ia2 = poly_ia2 if os.path.isabs(poly_ia2) else os.path.join(osm_dir, poly_ia2)

    if not os.path.exists(path_poly_pa_ia1) or not os.path.exists(path_poly_ia2):
        log("Fehler: .poly-Dateien fehlen.")
        return None

    try:
        progress("master_network", 0, total_steps)
        df_types = parse_net_link_types(master_net_path, log=log)
        brackets_db = calculate_dynamic_brackets(df_types)
    except Exception as e:
        raise RuntimeError(f"Master-Netz-Analyse fehlgeschlagen: {e}") from e

    rel_temp_pa_ia1 = "temp_net_pa_ia1_extract.osm.pbf"
    rel_net_pa_ia1 = "temp_net_1_pa_ia1.osm.pbf"
    rel_temp_ia2 = "temp_net_ia2_extract.osm.pbf"
    rel_net_ia2 = "temp_net_2_ia2.osm.pbf"
    rel_net_oa = "temp_net_3_oa.osm.pbf"
    rel_net_ferry = "temp_net_4_ferry.osm.pbf"

    rel_final = "hierarchical_network.osm"
    abs_final_osm = os.path.join(osm_dir, rel_final)
    abs_final_orig_xml = os.path.join(osm_dir, "hierarchical_network_original.osm")
    abs_final_mod_xml = os.path.join(osm_dir, "hierarchical_network_modified.osm")
    output_original = output_original or os.path.join(osm_dir, "road_network_hierarchical_original.osm")
    output_modified = output_modified or os.path.join(osm_dir, "road_network_hierarchical_modified.osm")

    env = os.environ.copy()
    env["PATH"] = bin_dir + os.pathsep + env.get("PATH", "")

    def run_osmium(cmd):
        if run_command:
            return run_command(cmd[1:], cwd=osm_dir)
        kwargs = {"env": env, "capture_output": True, "text": True, "cwd": osm_dir}
        if os.name == "nt":
            kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
        res = subprocess.run(cmd, **kwargs)
        if res.returncode != 0:
            raise Exception(f"Osmium Fehler: {res.stderr.strip()}")
        return res

    try:
        progress("network_pa_extract", 1, total_steps)
        log("A) Verarbeite Kernzone (PA + IA1): Gebiet ausschneiden ...")
        run_osmium([osmium_exe, "extract", "-p", path_poly_pa_ia1, merged_pbf, "-o", rel_temp_pa_ia1, "--overwrite"])
        progress("network_pa_filter", 2, total_steps)
        log("A) Verarbeite Kernzone (PA + IA1): Straßentypen filtern ...")
        filter_pa_ia1 = "w/highway=motorway,motorway_link,trunk,trunk_link,primary,primary_link,secondary,secondary_link,tertiary,tertiary_link,unclassified,residential"
        run_osmium([osmium_exe, "tags-filter", rel_temp_pa_ia1, filter_pa_ia1, "-o", rel_net_pa_ia1, "--overwrite"])

        progress("network_ia2_extract", 3, total_steps)
        log("B) Verarbeite Einflusszone (IA2): Gebiet ausschneiden ...")
        run_osmium([osmium_exe, "extract", "-p", path_poly_ia2, merged_pbf, "-o", rel_temp_ia2, "--overwrite"])
        progress("network_ia2_filter", 4, total_steps)
        log("B) Verarbeite Einflusszone (IA2): Straßentypen filtern ...")
        filter_ia2 = "w/highway=motorway,motorway_link,trunk,trunk_link,primary,primary_link,secondary,secondary_link,tertiary,tertiary_link,unclassified"
        run_osmium([osmium_exe, "tags-filter", rel_temp_ia2, filter_ia2, "-o", rel_net_ia2, "--overwrite"])

        progress("network_oa_filter", 5, total_steps)
        log("C) Verarbeite Außenraum (OA) ...")
        filter_oa = "w/highway=motorway,motorway_link,trunk,trunk_link,primary,primary_link,secondary,secondary_link"
        run_osmium([osmium_exe, "tags-filter", merged_pbf, filter_oa, "-o", rel_net_oa, "--overwrite"])

        progress("network_ferry_filter", 6, total_steps)
        log("D) Extrahiere Fährverbindungen ...")
        run_osmium([osmium_exe, "tags-filter", merged_pbf, "w/route=ferry", "-o", rel_net_ferry, "--overwrite"])

        progress("network_merge", 7, total_steps)
        log("E) Führe alle Netz-Schichten zusammen ...")
        run_osmium([osmium_exe, "merge", rel_net_pa_ia1, rel_net_ia2, rel_net_oa, rel_net_ferry, "-o", rel_final,
                    "--overwrite"])

        # 1. Save out raw unaltered baseline copy
        shutil.copyfile(abs_final_osm, abs_final_orig_xml)
        progress("network_original", 8, total_steps)
        if output_original.endswith(".osm"):
            shutil.copyfile(abs_final_orig_xml, output_original)
        else:
            run_osmium([osmium_exe, "cat", abs_final_orig_xml, "-o", output_original, "--overwrite"])
        log(f"Unbearbeitetes Straßennetz gespeichert: {output_original}")

        # 2. Run neighborhood topological imputation engine and output full modified copy
        progress("network_classification", 9, total_steps)
        impute_and_classify_osm(abs_final_osm, abs_final_mod_xml, brackets_db, log=log)
        progress("network_modified", 10, total_steps)
        if output_modified.endswith(".osm"):
            shutil.copyfile(abs_final_mod_xml, output_modified)
        else:
            run_osmium([osmium_exe, "cat", abs_final_mod_xml, "-o", output_modified, "--overwrite"])
        log(f"Bearbeitetes Straßennetz gespeichert: {output_modified}")

        # Cleanup transient working file
        if os.path.exists(abs_final_osm):
            os.remove(abs_final_osm)

        return {"network_original": output_original, "network_modified": output_modified}
    except Exception as e:
        log(f"Fehler in der Pipeline: {e}")
        return None
    finally:
        for tmp in [
            rel_temp_pa_ia1, rel_net_pa_ia1, rel_temp_ia2, rel_net_ia2,
            rel_net_oa, rel_net_ferry, abs_final_osm, abs_final_orig_xml, abs_final_mod_xml
        ]:
            abs_tmp = tmp if os.path.isabs(tmp) else os.path.join(osm_dir, tmp)
            if os.path.exists(abs_tmp): os.remove(abs_tmp)
