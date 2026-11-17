# Netzaufbereitung und Routing ohne PTV Visum (Open-Source-Migrationsleitfaden)

Dieses Dokument beschreibt, wie die bisher in PTV Visum ausgeführten Netzaufbereitungs- und Routing-Schritte (insbesondere Fähranbindung, Abbiege-/Graphlogik, Fährwartezeit und Erreichbarkeitsprüfung) perspektivisch vollständig **Open Source** in Python/QGIS (z. B. mit `xml.etree.ElementTree`, `pyproj`, `scipy.spatial.cKDTree` und `networkx`) direkt auf der OSM-Datei bzw. einem GeoPackage/Graphen umgesetzt werden können.

---

## Überblick: Visum vs. Open-Source-Äquivalente

| Arbeitsschritt in PTV Visum | Aktuelles Skript | Open-Source-Ersatz |
| :--- | :--- | :--- |
| OSM-Import & Knoten-Splitting | `01_import_links_and_zones.py` + `PANDO_Importer` | `xml.etree.ElementTree` / `osmium` + Graphaufbau aus Ways/Nodes |
| Fährknoten-Qualifizierung (`Ferry_Node`, `Ferry_Degree`, `OSM_FerryTerminal`, `Isolated_Ferry`) | `ferry_access.py` | Direkte Auswertung der OSM-Ways (`route=ferry`, `highway=*`) + 200-m-Snap |
| „Connect nodes via links“ (`Ferry_Access`, 0–1 Link, max. 5 km) | `ferry_access.py` (`Nodes.ConnectNodes`) | `scipy.spatial.cKDTree` im metrischen UTM-Koordinatensystem |
| Abbieger-Freigabe & Fährwartezeit (`PANDO_FERRY_WAIT`) | `ferry_access.py`, `ferry_wait.py` | Kanten-/Knoten-Expansion im gerichteten `networkx.DiGraph` (Dual Graph / Turn-Weights) |
| Erreichbarkeitsprüfung (`Reachable_Node` via Isochronen) | `02_derive_access_nodes.py` | Stark zusammenhängende Komponenten (`nx.strongly_connected_components`) |
| Kürzeste-Wege-Suche / Reisezeitmatrizen | `DE_Verfahrensablauf.xml` / `EN_ProcedureSequence.xml` | `networkx.multi_source_dijkstra` oder `igraph` / `rustworkx` / OSRM / Valhalla |

---

## 1. Fährknoten-Qualifizierung direkt auf der OSM-Datei

Die zentrale Herausforderung bei OSM-Fährnetzen ist die Unterscheidung zwischen:
1. **Bereits angebundenen Fährterminals** (ankommende **und** abgehende Straße mit `CAR` und `WALK`),
2. **Zwischen- und Verzweigungsknoten auf dem Wasser** (mehrere Fähr-Nachbarn, `Ferry_Degree >= 2`, kein Terminal-Tag), die **nicht** an das Straßennetz gezogen werden dürfen, und
3. **Isolierten Fähranlegern (`Isolated_Ferry = 1`)**, die entweder explizit getaggt sind (`OSM_FerryTerminal = 1`, inkl. 200-m-Toleranz für leicht versetzte Terminal-Punkte am Ufer) oder ein Fähr-Endknoten (`Ferry_Degree == 1`) ohne Straßenanbindung sind.

```python
import xml.etree.ElementTree as ET
from collections import defaultdict
import numpy as np
from pyproj import Transformer
from scipy.spatial import cKDTree

FERRY_TERMINAL_TAGS = {
    "amenity": {"ferry_terminal"},
    "ferry": {"yes"},
    "mooring": {"ferry", "yes"},
    "man_made": {"pier"},
    "waterway": {"dock"},
}


def is_terminal_node(tags: dict[str, str]) -> bool:
    return any(tags.get(k, "").strip().lower() in vals for k, vals in FERRY_TERMINAL_TAGS.items())


def qualifies_for_car_and_walk(tags: dict[str, str]) -> bool:
    hw = tags.get("highway", "")
    if not hw or hw in {"path", "footway", "pedestrian", "steps", "cycleway", "track", "construction"}:
        return False
    if tags.get("motor_vehicle") == "no" or tags.get("car") == "no" or tags.get("foot") == "no":
        return False
    if hw in {"motorway", "motorway_link", "trunk", "trunk_link"} and tags.get("foot") != "yes":
        return False
    return True


def extract_and_classify_ferry_nodes(osm_path: str, utm_epsg: str):
    tree = ET.parse(osm_path)
    root = tree.getroot()
    transformer = Transformer.from_crs("EPSG:4326", utm_epsg, always_xy=True)

    coords_wgs84 = {}
    tagged_terminals = set()
    for n in root.findall("node"):
        nid = n.get("id")
        coords_wgs84[nid] = (float(n.get("lon")), float(n.get("lat")))
        tags = {t.get("k"): t.get("v") for t in n.findall("tag")}
        if is_terminal_node(tags):
            tagged_terminals.add(nid)

    ferry_neighbors = defaultdict(set)
    ferry_endpoints = set()
    ferry_midnodes = set()
    road_in = set()
    road_out = set()

    for w in root.findall("way"):
        tags = {t.get("k"): t.get("v") for t in w.findall("tag")}
        nds = [nd.get("ref") for nd in w.findall("nd")]
        if len(nds) < 2:
            continue

        if tags.get("route") == "ferry":
            ferry_endpoints.update((nds[0], nds[-1]))
            ferry_midnodes.update(nds[1:-1])
            for a, b in zip(nds[:-1], nds[1:]):
                if a != b:
                    ferry_neighbors[a].add(b)
                    ferry_neighbors[b].add(a)
        elif "highway" in tags and qualifies_for_car_and_walk(tags):
            oneway = tags.get("oneway") in {"yes", "1", "true"}
            road_out.update(nds[:-1])
            road_in.update(nds[1:])
            if not oneway:
                road_out.update(nds[1:])
                road_in.update(nds[:-1])

    ferry_nodes = sorted(ferry_neighbors.keys())
    road_connected = road_in & road_out
    mid_only = ferry_midnodes - ferry_endpoints

    # Projektion in metrisches UTM-System
    ferry_xy = np.array([transformer.transform(*coords_wgs84[nid]) for nid in ferry_nodes])

    # 1:1 Nearest-Snap (<= 200 m) für leicht versetzte Terminal-Punkte (z. B. am Ufer neben dem Pier)
    snapped_terminals = {nid for nid in ferry_nodes if nid in tagged_terminals}
    unmatched_terminals = [nid for nid in tagged_terminals if nid not in snapped_terminals and nid in coords_wgs84]
    if unmatched_terminals and len(ferry_nodes) > 0:
        tree_ferry = cKDTree(ferry_xy)
        u_xy = np.array([transformer.transform(*coords_wgs84[nid]) for nid in unmatched_terminals])
        dists, idxs = tree_ferry.query(u_xy, distance_upper_bound=200.0)
        for dist, idx in zip(dists, idxs):
            if np.isfinite(dist) and idx < len(ferry_nodes):
                snapped_terminals.add(ferry_nodes[int(idx)])

    classified = {}
    for nid in ferry_nodes:
        f_deg = len(ferry_neighbors[nid])
        has_road_io = nid in road_connected
        is_term = (nid in snapped_terminals) or (f_deg == 1 and nid not in mid_only)
        classified[nid] = {
            "Ferry_Node": True,
            "Ferry_Degree": f_deg,
            "OSM_FerryTerminal": nid in snapped_terminals,
            "Isolated_Ferry": bool(is_term and not has_road_io),
        }

    road_target_nodes = sorted(road_connected - set(ferry_nodes))
    return classified, road_target_nodes, coords_wgs84, transformer
```

---

## 2. „Connect nodes via links“ ohne Visum (`cKDTree` + optionaler OSM-Export)

Sobald `Isolated_Ferry = True` und `road_target_nodes` bekannt sind, bildet ein einziger `cKDTree`-Aufruf das Visum-Verfahren *„Connect nodes via links“* (`Min = 0`, `Max = 1`, `Radius = 5000 m`, `LinkType = Ferry_Access`) exakt nach.

Soll das Ergebnis wieder als `.osm`-Datei (z. B. für QGIS, Osmium, OSRM oder Valhalla) gespeichert werden, lassen sich direkt synthetische `<way>`-Elemente anhängen:

```python
def connect_isolated_ferries_cKDTree(classified, road_target_nodes, coords_wgs84, transformer, max_dist_m=5000.0):
    isolated_ids = [nid for nid, info in classified.items() if info["Isolated_Ferry"]]
    if not isolated_ids or not road_target_nodes:
        return []

    target_xy = np.array([transformer.transform(*coords_wgs84[nid]) for nid in road_target_nodes])
    origin_xy = np.array([transformer.transform(*coords_wgs84[nid]) for nid in isolated_ids])

    target_tree = cKDTree(target_xy)
    dists, idxs = target_tree.query(origin_xy, distance_upper_bound=max_dist_m)

    access_edges = []
    for origin_nid, dist, idx in zip(isolated_ids, dists, idxs):
        if np.isfinite(dist) and idx < len(road_target_nodes):
            target_nid = road_target_nodes[int(idx)]
            access_edges.append((origin_nid, target_nid, float(dist)))
    return access_edges


def append_ferry_access_ways_to_osm(osm_in_path: str, osm_out_path: str, access_edges):
    tree = ET.parse(osm_in_path)
    root = tree.getroot()
    next_way_id = -1000000

    for u_nid, v_nid, _dist_m in access_edges:
        way = ET.SubElement(root, "way", {"id": str(next_way_id), "version": "1"})
        next_way_id -= 1
        ET.SubElement(way, "nd", {"ref": str(u_nid)})
        ET.SubElement(way, "nd", {"ref": str(v_nid)})
        ET.SubElement(way, "tag", {"k": "highway", "v": "service"})
        ET.SubElement(way, "tag", {"k": "pando_type", "v": "ferry_access"})
        ET.SubElement(way, "tag", {"k": "maxspeed", "v": "1"})
        ET.SubElement(way, "tag", {"k": "foot", "v": "yes"})
        ET.SubElement(way, "tag", {"k": "motorcar", "v": "yes"})

    tree.write(osm_out_path, encoding="utf-8", xml_declaration=True)
```

> [!NOTE]
> Durch `distance_upper_bound=5000.0` (entspricht `MinNewLinksPerSourceNode = 0`, `MaxNewLinksPerSourceNode = 1`, `MaxDistance = 5000 m`) werden weit abgelegene Insel-Anleger ohne Hauptstraße im Außenraum **nicht** fälschlich über das offene Meer zwangsverbunden.

---

## 3. Abbieger, Fährwartezeit und Erreichbarkeit in `networkx`

### A) Fährwartezeit (`PANDO_FERRY_WAIT`) ohne Visum-Abbiegetabelle
In Visum wird die Wartezeit nur beim **Einstieg** (Abbieger von Nicht-Fähre $\rightarrow$ Fähre) aufgeschlagen, nicht aber an Zwischenknoten einer geteilten Fährlinie (Fähre $\rightarrow$ Fähre).

Im Open-Source-Graph lässt sich das über einen **Zustands-Graphen (State-Augmented Graph)** mit zwei Knoten-Zuständen je realem Knoten `(node_id, on_ferry: bool)` ohne kompletten Dual-Graph abbilden:
* Kante auf Land / `Ferry_Access` (`link_type != "Ferry"`):
  * `(u, False) -> (v, False)` mit Reisezeit `t0_link`
  * `(u, True)  -> (v, False)` mit Reisezeit `t0_link` (Aussteigen kostet keine Wartezeit)
* Kante auf Fähre (`link_type == "Ferry"`):
  * `(u, False) -> (v, True)` mit Reisezeit `t0_link + ferry_wait_sec` (Einstieg)
  * `(u, True)  -> (v, True)` mit Reisezeit `t0_link` (Weiterfahrt über geteilte OSM-Fähr-Ways ohne doppelte Wartezeit)

```python
import networkx as nx


def build_state_graph_with_ferry_wait(edges, ferry_wait_sec: float = 1200.0) -> nx.DiGraph:
    """Erzeugt einen gerichteten Routing-Graphen inkl. einmaliger Fährwartezeit pro Einstieg."""
    G = nx.DiGraph()
    for u, v, length_m, speed_kmh, is_ferry in edges:
        t0_sec = (length_m / 1000.0) / max(speed_kmh, 0.1) * 3600.0
        if is_ferry:
            G.add_edge((u, False), (v, True), weight=t0_sec + ferry_wait_sec, wait=ferry_wait_sec)
            G.add_edge((u, True), (v, True), weight=t0_sec, wait=0.0)
        else:
            G.add_edge((u, False), (v, False), weight=t0_sec, wait=0.0)
            G.add_edge((u, True), (v, False), weight=t0_sec, wait=0.0)
    return G
```

### B) `Reachable_Node` ohne Visum-Isochronen
Die Visum-Isochronenprüfung in `02_derive_access_nodes.py` prüft, ob jeder Knoten vom Hauptstraßennetz aus hin **und** zurück per `CAR` erreichbar ist. In `networkx` entspricht das exakt der Zugehörigkeit zur größten **stark zusammenhängenden Komponente (`strongly_connected_components`)** (bzw. der Komponente, die die Hauptnetz-Startknoten enthält):

```python
def compute_reachable_nodes(G_car: nx.DiGraph, min_share: float = 0.15) -> set:
    total = max(1, G_car.number_of_nodes())
    reachable = set()
    for comp in nx.strongly_connected_components(G_car):
        if len(comp) / total >= min_share:
            reachable.update(comp)
    return reachable
```

