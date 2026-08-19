import os
import sys
import logging
from pathlib import Path
import numpy as np
import pandas as pd
import networkx as nx
import win32com.client as com

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')


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


def find_helper_file(search_dir: Path, filename: str) -> Path:
    """Sucht flexibel im Projekt- oder Visum-Ordner nach einer benötigten Hilfsdatei (z.B. XML)."""
    for s_dir in [search_dir, search_dir.parent]:
        if s_dir.exists():
            matches = list(s_dir.rglob(filename))
            if matches:
                return matches[0]

    core_name = filename
    if "_" in filename:
        parts = filename.split("_")
        if parts[0].isdigit() or parts[0].startswith("0"):
            core_name = "_".join(parts[1:])

    for s_dir in [search_dir, search_dir.parent]:
        if s_dir.exists():
            for p in s_dir.rglob("*"):
                if p.is_file() and (core_name.lower() in p.name.lower() or filename.lower() in p.name.lower()):
                    return p

    raise FileNotFoundError(f"Die benötigte Hilfsdatei '{filename}' wurde nicht gefunden!")


class AccessNodeDeriver:
    def __init__(self, visum_instance, working_dir: Path):
        self.Visum = visum_instance
        self.working_dir = working_dir

        self.Visum.Filters.InitAll()
        self.lf = self.Visum.Filters.LinkFilter()
        self.nf = self.Visum.Filters.NodeFilter()

        self.edit_link_atts = self.Visum.Net.CreateEditAttributePara()
        self.edit_link_atts.SetAttValue("NETOBJECTTYPE", "LINK")

    def process_access_nodes(self, output_ver_path: Path = None, procedure_xml: Path = None):
        """Führt den gesamten Ablauf der Zugangsknotenableitung sequentiell aus."""
        logging.info("--- Visum Schritt 2: Ableitung von Zugangsknoten & Anbindungen gestartet ---")
        if procedure_xml is None:
            base_project_dir, _ = get_project_paths()
            procedure_xml = find_helper_file(base_project_dir, "20230814_1erUmlegung_TestAnbindungsknoten.xml")

        # 1. Netzanpassungen (Einbahnstraßen, Fähren)
        logging.info("1/6 Preprocessing: Einbahnstraßen & Fährverbindungen...")
        self.preprocess_one_way_streets()
        self.preprocess_ferries()

        # 2. Netzvorbereitung & Analyse (Gtypes, Rampen, U-Turns)
        logging.info("2/6 Netz-Analyse: Rampen, U-Turns & Knotentypen (kann 5–10 Minuten dauern)...")
        self.prepare_net()
        self.preprocess_roundabouts("OSM_ROUNDABOUT")
        self.ramp_analysis()
        self.identify_uturns()
        self.postprocess_gtypes()

        # 3. Knotentypen bestimmen
        logging.info("3/6 Knotentypen der Kreuzungen bestimmen...")
        self.determine_nodetype()

        # 4. Erreichbarkeit prüfen
        logging.info("4/6 Berechne Erreichbarkeit via Isochronen...")
        self.calculate_reachable_nodes(max_start_nodes=5)

        # 5. Clustering nach Knotentyp
        logging.info("5/6 Clustere Zugangsknoten nach Hierarchie-Stufen...")
        nodetype_buffer_dict = {
            '1-1': 1000.0, '1-2': 500.0, '1-3': 250.0, '1-4': 250.0, '1-5': 250.0,
            '2-2': 250.0, '2-3': 125.0, '2-4': 125.0, '2-5': 12.5,
            '3-3': 150.0, '3-4': 50.0, '3-5': 12.5,
            '4-4': 50.0, '4-5': 12.5, '5-5': 0.0
        }
        self.cluster_by_nodetype(nodetype_buffer_dict)

        # 6. Hilfsnetz erzeugen, umlegen & ausdünnen (inkl. Reachable_Node Fix für neue Knoten)
        logging.info("6/6 Erzeuge Hilfsnetz und führe Ausdünnung aus...")
        temp_helper_net = self.working_dir / "temp_helper_connector_nodes.net"
        self.prepare_and_import_helper_net(temp_helper_net)
        self.postprocess_helper_links(procedure_xml)

        # 7. Aufräumen & Speichern
        if output_ver_path is None:
            ver_dir = self.working_dir / "ver"
            ver_dir.mkdir(parents=True, exist_ok=True)
            output_ver_path = ver_dir / "03_AccessNodes_Derived.ver"

        self.clean_up_and_save(output_ver_path)
        logging.info("Schritt 2 komplett abgeschlossen: Zugangsknoten und Anbindungen erfolgreich erzeugt.")

        if temp_helper_net.exists():
            try:
                temp_helper_net.unlink()
            except Exception:
                pass


    def add_uda(self, netobj, uda_name: str, value_type: int, def_val, comment: str = ''):
        """Erzeugt ein benutzerdefiniertes Attribut (UDA), falls nicht vorhanden."""
        try:
            netobj.AddUserDefinedAttribute(uda_name, uda_name, uda_name, value_type, DefVal=def_val)
            netobj.SetAllAttValues(uda_name, def_val, OnlyActive=False)
            uda = netobj.Attributes.ItemByKey(uda_name)
            uda.Comment = comment
            if value_type != 5:
                uda.ValueDefault = def_val
            else:
                uda.StringValueDefault = def_val
        except Exception:
            logging.debug(f"UDA '{uda_name}' existiert bereits.")

    def add_mat(self, obj_id: int, mat_code: str, mat_name: str, objecttype: int = 2, matrixtype: int = 3):
        """Erzeugt eine Visum-Matrix."""
        M = self.Visum.Net.AddMatrix(obj_id, objecttype, matrixtype)
        M.SetAttValue("Code", mat_code)
        M.SetAttValue("Name", mat_name if mat_name else mat_code)
        return M

    def get_multi_netobj_atts_with_id(self, netobj, atts_dict_withdtype: dict) -> pd.DataFrame:
        """Liest Attribute inkl. Visum-ID als Pandas DataFrame aus."""
        netobj_type = netobj._oleobj_.GetTypeInfo().GetDocumentation(-1)[0]

        if netobj_type != 'ILinks':
            df_netobj = pd.DataFrame(
                netobj.GetMultipleAttributes(list(atts_dict_withdtype.keys()), OnlyActive=True),
                columns=atts_dict_withdtype.keys()
            ).astype(atts_dict_withdtype)
            df_netobj_index = pd.DataFrame(
                netobj.GetMultiAttValues("NO", OnlyActive=False),
                columns=["VISUM_ID", "NO"]
            ).astype({"VISUM_ID": int, "NO": int})
            df_netobj = pd.merge(df_netobj, df_netobj_index, on='NO')
            v_id = df_netobj.pop('VISUM_ID')
            df_netobj.insert(0, 'VISUM_ID', v_id)
        else:
            self.add_uda(netobj, 'LINK_IDENTIFIER', 5, '', 'Link ID Identifier')
            x = self.Visum.Net.CreateEditAttributePara()
            x.SetAttValue("NETOBJECTTYPE", "LINK")
            x.SetAttValue("INCLUDESUBCATEGORIES", "0")
            x.SetAttValue("ONLYACTIVE", "0")
            x.SetAttValue("RESULTATTRNAME", "LINK_IDENTIFIER")
            x.SetAttValue("FORMULA", 'NUMTOSTR([NO],0) + "-" + NUMTOSTR([FROMNODENO],0) + "-" + NUMTOSTR([TONODENO],0)')
            self.Visum.Net.EditAttribute(x)

            atts_dict_withdtype['LINK_IDENTIFIER'] = str
            df_links = pd.DataFrame(
                netobj.GetMultipleAttributes(list(atts_dict_withdtype.keys()), OnlyActive=True),
                columns=atts_dict_withdtype.keys()
            ).astype(atts_dict_withdtype)
            df_links_index = pd.DataFrame(
                netobj.GetMultiAttValues("LINK_IDENTIFIER", OnlyActive=False),
                columns=["VISUM_ID", "LINK_IDENTIFIER"]
            ).astype({"VISUM_ID": int, "LINK_IDENTIFIER": str})
            df_links = pd.merge(df_links, df_links_index, on='LINK_IDENTIFIER')
            v_id = df_links.pop('VISUM_ID')
            df_links.insert(0, 'VISUM_ID', v_id)
            df_netobj = df_links.copy()
        return df_netobj

    def new_object_number(self, netobject: str) -> int:
        """Ermittelt eine freie, ausreichend große Objektnummer für neue Elemente."""
        highest_no = self.Visum.Net.AttValue(rf'MAX:{netobject}\NO')
        highest_no = int(highest_no) if highest_no is not None else 0
        power = 1
        while power < highest_no:
            power *= 10
        return int(10 * power)

    def preprocess_one_way_streets(self):
        """Weist NUR den echten gesperrten Gegenrichtungen von Einbahnstraßen den Typ '0' (Blocked Oneway) zu."""
        logging.info("Weise gesperrten Einbahnstraßen-Rückrichtungen den Typ '0' (Blocked Oneway) zu...")

        atts = {
            "TYPENO": int,
            "TSYSSET": str,
            r"REVERSELINK\TSYSSET": str
        }
        link_data = self.get_multi_netobj_atts_with_id(self.Visum.Net.Links, atts)

        if not link_data.empty:
            has_car_dir = pd.Series(['car' in str(x).lower() for x in link_data['TSYSSET']], index=link_data.index)
            has_car_rev = pd.Series(['car' in str(x).lower() for x in link_data[r"REVERSELINK\TSYSSET"]], index=link_data.index)

            # ECHTE Einbahnstraße: Diese Richtung hat KEIN Car, aber die Gegenrichtung HAT Car!
            mask = link_data['TYPENO'].between(10, 73) & (~has_car_dir) & (has_car_rev)
            filtered = link_data[mask]

            if not filtered.empty:
                vals = filtered[['VISUM_ID']].copy()
                vals['NEW_TYPE'] = 0
                self.Visum.Net.Links.SetMultiAttValues("TYPENO", vals.values)
                logging.info(
                    f"{len(filtered)} Einbahnstraßen-Rückrichtungen exakt als 'Blocked Oneway' (Typ 0) markiert.")

    def preprocess_ferries(self):
        """Kennzeichnet Fährknoten und bindet diese über 'Ferry_Access' an (via Pandas)."""
        logging.info("Verarbeite Fährverbindungen und Fähr-Anbindungen...")
        self.add_uda(self.Visum.Net.Nodes, 'Ferry_Origin', 9, False, '1 wenn Knoten eine Fährverbindung besitzt')

        link_data = self.get_multi_netobj_atts_with_id(self.Visum.Net.Links, {"TONODENO": int, "TYPENO": int})
        if not link_data.empty:
            ferry_nodes = link_data[link_data['TYPENO'] == 94]['TONODENO'].unique()

            node_data = self.get_multi_netobj_atts_with_id(self.Visum.Net.Nodes, {"NO": int})
            if not node_data.empty and len(ferry_nodes) > 0:
                node_data['Ferry_Origin'] = node_data['NO'].isin(ferry_nodes)
                ferry_vals = node_data[node_data['Ferry_Origin']][['VISUM_ID', 'Ferry_Origin']].copy()
                if not ferry_vals.empty:
                    self.Visum.Net.Nodes.SetMultiAttValues('Ferry_Origin', ferry_vals.values)
                    logging.info(f"{len(ferry_vals)} Fähr-Knoten mit 'Ferry_Origin = 1' markiert.")

        ferry_access_type_no = 96
        try:
            self.Visum.Net.LinkTypes.ItemByKey(ferry_access_type_no)
        except Exception:
            lt = self.Visum.Net.AddLinkType(ferry_access_type_no)
            lt.SetAttValue("NAME", "Ferry_Access")
            lt.SetAttValue("V0PRT", 5)
            lt.SetAttValue("NUMLANES", 1)
            lt.SetAttValue("CAPPRT", 999)

        try:
            connect_para = self.Visum.Net.CreateConnectNodesViaLinksPara()
            connect_para.SetAttValue("MINLENGTH", 0)
            connect_para.SetAttValue("MAXLENGTH", 5000)
            connect_para.SetAttValue("LINKTYPENO", ferry_access_type_no)
            connect_para.SetAttValue("ONLYACTIVEORIGINS", True)

            self.nf.Init()
            self.nf.AddCondition("OP_NONE", False, "Ferry_Origin", "EqualVal", True)
            self.Visum.Net.ConnectNodesViaLinks(connect_para)
            logging.info("Fähr-Anbindungen ('Ferry_Access') erfolgreich generiert.")
        except Exception as e:
            logging.debug(f"Fähr-Anbindungen (ConnectNodesViaLinks) übersprungen/nicht unterstützt: {e}")
        self.nf.Init()

        try:
            self.lf.Init()
            self.lf.AddCondition("OP_NONE", False, "LINKTYPE\\NAME", "EqualVal", "Ferry_Access")
            turn_para = self.Visum.Net.CreateCalculateTurnsPara()
            turn_para.SetAttValue("CALCULATEFROMMODES", True)
            self.Visum.Net.CalculateTurns(turn_para)
            logging.info("Abbieger für 'Ferry_Access'-Strecken freigegeben.")
        except Exception as e:
            logging.debug(f"Fähr-Abbieger (CalculateTurns) übersprungen/nicht unterstützt: {e}")
        self.lf.Init()

    def prepare_net(self):
        """Setzt die globalen Strecken-Obertypen (GTYPE) basierend auf OSM-Namen."""
        logging.info("Setze globale Strecken-Obertypen (GTYPE) basierend auf OSM-Straßenklassen...")
        self.lf.Init()
        self.lf.AddCondition("OP_NONE", False, "TSYSSET", "EqualVal", "")
        self.Visum.Net.Links.SetAllAttValues(Attribut="TYPENO", NewValue=0, OnlyActive=True)
        self.lf.Init()

        linktypes_df = self.get_multi_netobj_atts_with_id(self.Visum.Net.LinkTypes, {"NO": int, "NAME": str})
        osm_obertypen = {
            'motorway': 1, 'trunk': 2, 'primary': 2, 'secondary': 3,
            'tertiary': 4, 'unclassified': 5, 'residential': 5,
            'road': 5, 'living_street': 5, 'service': 6
        }
        linktypes_df['GTYPE'] = 99
        for road_type, main_type in osm_obertypen.items():
            mask = pd.Series([road_type.lower() in str(x).lower() for x in linktypes_df['NAME']], index=linktypes_df.index)
            linktypes_df.loc[mask, 'GTYPE'] = main_type
        linktypes_df.loc[pd.Series(['_link' in str(x).lower() for x in linktypes_df['NAME']], index=linktypes_df.index), 'GTYPE'] = 77
        self.Visum.Net.LinkTypes.SetMultiAttValues("GTYPE", linktypes_df[["VISUM_ID", "GTYPE"]].values)

        self.add_uda(self.Visum.Net.Links, uda_name='ZK_OBERTYP_UNGERICHTET', value_type=1, def_val=99)
        self.edit_link_atts.SetAttValue("ONLYACTIVE", "0")
        self.edit_link_atts.SetAttValue("RESULTATTRNAME", "ZK_OBERTYP_UNGERICHTET")
        self.edit_link_atts.SetAttValue("FORMULA", r'MIN([GTYPE], [REVERSELINK\LINKTYPE\GTYPE])')
        self.Visum.Net.EditAttribute(self.edit_link_atts)

    def preprocess_roundabouts(self, roundabout_uda: str = "OSM_ROUNDABOUT"):
        """Verarbeitet Kreisverkehre analog zu Rampen."""
        if self.Visum.Net.Links.AttrExists(roundabout_uda):
            self.edit_link_atts.SetAttValue("ONLYACTIVE", "0")
            self.edit_link_atts.SetAttValue("RESULTATTRNAME", "ZK_OBERTYP_UNGERICHTET")
            self.edit_link_atts.SetAttValue("FORMULA", f'IF([{roundabout_uda}]="1", 77, [ZK_OBERTYP_UNGERICHTET])')
            self.Visum.Net.EditAttribute(self.edit_link_atts)
        self.edit_link_atts.SetAttValue("FORMULA", 'IF([ZK_OBERTYP_UNGERICHTET] = 77, -1, [ZK_OBERTYP_UNGERICHTET])')
        self.Visum.Net.EditAttribute(self.edit_link_atts)

    def ramp_analysis(self):
        """Analysiert zusammenhängende Rampenstrukturen via NetworkX."""
        logging.info("Analysiere Rampen- und Knotenverbindungen...")
        self.add_uda(self.Visum.Net.Links, 'ZK_OBERTYP_VERSCHIEDENE', value_type=5, def_val='')
        self.lf.Init()
        self.lf.AddCondition("OP_NONE", False, "ZK_OBERTYP_UNGERICHTET", "EqualVal", -1)

        if self.Visum.Net.Links.CountActive:
            atts_dict = {
                "NO": int, "FROMNODENO": int, "TONODENO": int,
                r"FROMNODE\DISTINCT: INLINKS\ZK_OBERTYP_UNGERICHTET": str,
                r"TONODE\DISTINCT: INLINKS\ZK_OBERTYP_UNGERICHTET": str
            }
            df_links = self.get_multi_netobj_atts_with_id(self.Visum.Net.Links, atts_dict)
            df_links['TFGTYPE'] = (
                    df_links[r"FROMNODE\DISTINCT: INLINKS\ZK_OBERTYP_UNGERICHTET"] + ',' +
                    df_links[r"TONODE\DISTINCT: INLINKS\ZK_OBERTYP_UNGERICHTET"]
            ).apply(lambda a: [int(x) for x in a.split(',') if x != ''])
            df_links['TFGTYPE_less5'] = df_links['TFGTYPE'].apply(lambda s: [x for x in s if 0 <= x <= 5])

            G = nx.from_pandas_edgelist(df_links, 'FROMNODENO', 'TONODENO',
                                        edge_attr=['VISUM_ID', 'NO', 'TFGTYPE_less5'], create_using=nx.DiGraph())
            grouped_links = []
            for component in nx.weakly_connected_components(G):
                subgraph = G.subgraph(component)
                ids = [d['VISUM_ID'] for _, _, d in subgraph.edges(data=True)]
                nos = [d['NO'] for _, _, d in subgraph.edges(data=True)]
                tfgtype_sorted = sorted(
                    [item for sublist in [d['TFGTYPE_less5'] for _, _, d in subgraph.edges(data=True)] for item in
                     sublist])
                grouped_links.append({'visum_ids': ids, 'linknos': nos, 'ftgtypes': tfgtype_sorted})

            grouped_df = pd.DataFrame(grouped_links)
            grouped_df = grouped_df[grouped_df['ftgtypes'].map(len) > 0]
            grouped_df['ZK_OBERTYP_VERSCHIEDENE'] = grouped_df['ftgtypes'].apply(
                lambda x: ','.join([str(y) for y in set(x)]))
            grouped_df.loc[grouped_df.ftgtypes.apply(lambda x: len(x) == 2), 'ZK_OBERTYP_VERSCHIEDENE'] = '6'

            linkids2edit = grouped_df.explode('visum_ids')[['visum_ids', 'ZK_OBERTYP_VERSCHIEDENE']]
            self.Visum.Net.Links.SetMultiAttValues('ZK_OBERTYP_VERSCHIEDENE', linkids2edit.to_numpy())

    def identify_uturns(self):
        """Identifiziert U-Turn-Schleifen (< 50 m auf gleichem Straßennamen)."""
        logging.info("Identifiziere U-Turns...")
        self.add_uda(self.Visum.Net.Links, 'ZK_IS_UTURN', value_type=9, def_val=False)
        self.Visum.Net.Links.SetAllAttValues('ZK_IS_UTURN', False, OnlyActive=False)

        self.lf.Init()
        self.lf.AddCondition("OP_NONE", False, r"LINKTYPE\GTYPE", "EqualVal", 77)
        self.lf.AddCondition("OP_AND", False, "ZK_OBERTYP_VERSCHIEDENE", "NotEqualVal", "*,*")

        if self.Visum.Net.Links.CountActive:
            atts_dict = {
                "NO": int, "FROMNODENO": int, "TONODENO": int, "LENGTH": float,
                r"FROMNODE\CONCATENATE:INLINKS\NAME": str, r"TONODE\CONCATENATE:INLINKS\NAME": str
            }
            df_links = self.get_multi_netobj_atts_with_id(self.Visum.Net.Links, atts_dict)
            df_links['TFNAME'] = (
                    df_links[r"FROMNODE\CONCATENATE:INLINKS\NAME"] + ',' + df_links[r"TONODE\CONCATENATE:INLINKS\NAME"]
            ).apply(lambda a: set([x for x in a.split(',') if x != '']))

            G = nx.from_pandas_edgelist(df_links, 'FROMNODENO', 'TONODENO',
                                        edge_attr=['VISUM_ID', 'NO', 'LENGTH', 'TFNAME'])
            grouped_links = []
            for component in nx.connected_components(G):
                subgraph = G.subgraph(component)
                ids = [d['VISUM_ID'] for _, _, d in subgraph.edges(data=True)]
                nos = [d['NO'] for _, _, d in subgraph.edges(data=True)]
                length_sum = sum([d['LENGTH'] for _, _, d in subgraph.edges(data=True)])
                ftnames = set().union(*[d['TFNAME'] for _, _, d in subgraph.edges(data=True)])
                grouped_links.append({'visum_ids': ids, 'linknos': nos, 'total_length': length_sum, 'ftnames': ftnames})

            grouped_df = pd.DataFrame(grouped_links)
            uturns = grouped_df[(grouped_df['ftnames'].apply(len) == 1) & (grouped_df["total_length"] <= 0.05)].copy()
            uturns['ZK_IS_UTURN'] = 1

            linksnos2edit = uturns.explode('linknos')[['linknos', 'ZK_IS_UTURN']]
            linksnos2edit.set_index(pd.Index(linksnos2edit.linknos.to_list()), inplace=True)
            df_links_index = pd.DataFrame(self.Visum.Net.Links.GetMultiAttValues("NO", OnlyActive=False),
                                          columns=["VISUM_ID", "NO"]).astype({"VISUM_ID": int, "NO": int})
            df_links_index['ZK_IS_UTURN'] = df_links_index["NO"].map(linksnos2edit['ZK_IS_UTURN']).fillna(0)
            self.Visum.Net.Links.SetMultiAttValues('ZK_IS_UTURN',
                                                   df_links_index[['VISUM_ID', 'ZK_IS_UTURN']].to_numpy())

    def postprocess_gtypes(self):
        """Finalisiert gerichtete und ungerichtete Obertypen."""
        self.lf.Init()
        self.lf.AddCondition("OP_NONE", False, "ZK_OBERTYP_UNGERICHTET", "GreaterVal", -1)
        self.edit_link_atts.SetAttValue("ONLYACTIVE", "1")
        self.edit_link_atts.SetAttValue("RESULTATTRNAME", "ZK_OBERTYP_VERSCHIEDENE")
        self.edit_link_atts.SetAttValue("FORMULA", 'NUMTOSTR([ZK_OBERTYP_UNGERICHTET],0)')
        self.Visum.Net.EditAttribute(self.edit_link_atts)
        self.lf.Init()

        self.add_uda(self.Visum.Net.Links, 'ZK_OBERTYP_STRECKE', value_type=1, def_val=99)
        self.edit_link_atts.SetAttValue("ONLYACTIVE", "0")
        self.edit_link_atts.SetAttValue("RESULTATTRNAME", "ZK_OBERTYP_STRECKE")
        self.edit_link_atts.SetAttValue("FORMULA", r'IF([ZK_IS_UTURN] & [LINKTYPE\GTYPE] != 99, 88, [LINKTYPE\GTYPE])')
        self.Visum.Net.EditAttribute(self.edit_link_atts)

    def determine_nodetype(self):
        """Ermittelt Knotentypen anhand einmündender Streckenklassen."""
        logging.info("Bestimme Knotentypen der Kreuzungen...")
        self.add_uda(self.Visum.Net.Nodes, 'ZK_TYP_DETAIL', value_type=5, def_val='')
        self.Visum.Filters.InitAll()
        self.lf.AddCondition("OP_NONE", False, "ZK_OBERTYP_UNGERICHTET", "LessEqualVal", 5)

        nodelist_colnames_dtypes = {
            "NO": int,
            r"CONCATENATEACTIVE: INLINKS\ZK_OBERTYP_VERSCHIEDENE": str,
            r"CONCATENATEACTIVE: INLINKS\ZK_OBERTYP_STRECKE": str,
            r"CONCATENATEACTIVE: OUTLINKS\ZK_OBERTYP_STRECKE": str
        }
        df_nodes = self.get_multi_netobj_atts_with_id(self.Visum.Net.Nodes, nodelist_colnames_dtypes)
        df_nodes = df_nodes[df_nodes[r"CONCATENATEACTIVE: INLINKS\ZK_OBERTYP_VERSCHIEDENE"].apply(
            lambda s: all(part.isdigit() for part in s.split(',')) if s else False)]

        str_cols = list(nodelist_colnames_dtypes.keys())[1:]
        df_nodes[str_cols] = df_nodes[str_cols].apply(
            lambda col: col.map(lambda x: sorted([int(y) for y in x.split(',')])))
        df_nodes["IO"] = df_nodes.apply(lambda x: sorted(
            x[r"CONCATENATEACTIVE: INLINKS\ZK_OBERTYP_STRECKE"] + x[r"CONCATENATEACTIVE: OUTLINKS\ZK_OBERTYP_STRECKE"]),
                                        axis=1)

        def calc_detail_type(x):
            nums_dist = x[r"CONCATENATEACTIVE: INLINKS\ZK_OBERTYP_VERSCHIEDENE"]
            nums_dist_dir = [i for i in x["IO"] if i < 77]
            nums_unq = sorted(list(set(nums_dist)))
            if len(nums_unq) > 1:
                res = f'{nums_unq[0]}-{nums_unq[1]}'
                if len(nums_dist_dir) > 0 and nums_dist_dir.count(min(nums_dist_dir)) > 4:
                    res = f'{min(nums_dist_dir)}-{min(nums_dist_dir)}'
                return res
            else:
                return f'{nums_unq[0]}-{nums_unq[0]}'

        df_nodes["Knotentyp_Detail"] = df_nodes.apply(calc_detail_type, axis=1)
        df_nodes["Knotentyp_Aggregiert"] = df_nodes["Knotentyp_Detail"].apply(lambda r: int(r.split('-')[1]))

        def filter_intersections(row):
            io = row["IO"]
            io_excl = [i for i in io if i < 88]
            undir_deg = len(io) / 2
            total_deg = len(io_excl)
            if any([total_deg >= 5, undir_deg >= 4, undir_deg == 3 and (77 in io),
                    len(set(i for i in io_excl if i < 77)) > 1]):
                return row["Knotentyp_Aggregiert"]
            return 99

        df_nodes["Knotentyp_Aggregiert"] = df_nodes.apply(filter_intersections, axis=1)
        df_nodes["Knotentyp_Detail"] = df_nodes.apply(
            lambda r: r["Knotentyp_Detail"] if r["Knotentyp_Aggregiert"] < 99 else 'N-N', axis=1)

        self.Visum.Net.Nodes.SetMultiAttValues("TYPENO", df_nodes[["VISUM_ID", "Knotentyp_Aggregiert"]].to_numpy())
        self.Visum.Net.Nodes.SetMultiAttValues("ZK_TYP_DETAIL", df_nodes[["VISUM_ID", "Knotentyp_Detail"]].to_numpy())

    def calculate_reachable_nodes(self, max_start_nodes: int = 5):
        """Ermittelt erreichbare Knoten über Isochronen für mehrere Startknoten."""
        logging.info("Berechne 'Reachable_Node' Attribut via Isochronen für mehrere Startknoten...")
        uda_name = "Reachable_Node"
        self.add_uda(self.Visum.Net.Nodes, uda_name, 9, False, 'True wenn Knoten im Straßennetz per Kfz erreichbar ist')

        all_nodes = self.Visum.Net.Nodes.GetMultipleAttributes(["NO", "COUNT:INLINKS"], OnlyActive=False)
        if not all_nodes:
            return

        sorted_nodes = sorted(all_nodes, key=lambda x: x[1] if x[1] is not None else 0, reverse=True)
        start_node_nos = [int(n[0]) for n in sorted_nodes[:max_start_nodes]]

        nodes_isoctimes_df = pd.DataFrame(self.Visum.Net.Nodes.GetMultipleAttributes(["NO"], OnlyActive=False),
                                          columns=(["NO"])).astype(int)
        nodes_isoctimes_df['Accessible'] = False

        for node_no in start_node_nos:
            try:
                self.Visum.Analysis.Isochrones.Clear()
                net_elems_nodes = self.Visum.CreateNetElements()
                node_obj = self.Visum.Net.Nodes.ItemByKey(node_no)
                if node_obj is None:
                    continue
                net_elems_nodes.Add(node_obj)
                temp_df = pd.DataFrame(self.Visum.Net.Nodes.GetMultipleAttributes(["NO"], OnlyActive=False),
                                       columns=(["NO"])).astype(int)

                for dir_x, is_dest in [("O", False), ("D", True)]:
                    self.Visum.Analysis.Isochrones.ExecutePrT(NetElms=net_elems_nodes, TSysCode="CAR", WKriterium=0,
                                                              IsDestinationBased=is_dest)
                    temp_df['IcocTimePrt_' + dir_x] = pd.DataFrame(
                        self.Visum.Net.Nodes.GetMultipleAttributes(["ISOCTIMEPRT"], OnlyActive=False))

                accessible_from_this = (temp_df['IcocTimePrt_O'] < 360000000) & (temp_df['IcocTimePrt_D'] < 360000000)
                nodes_isoctimes_df['Accessible'] = nodes_isoctimes_df['Accessible'] | accessible_from_this
            except Exception as e:
                logging.warning(f"Isochronen-Berechnung für Startknoten {node_no} fehlgeschlagen: {e}")
            finally:
                self.Visum.Analysis.Isochrones.Clear()

        nodes_isoctimes_df[uda_name] = nodes_isoctimes_df['Accessible']
        n_df_outputformat = pd.DataFrame(self.Visum.Net.Nodes.GetMultiAttValues("NO", OnlyActive=False),
                                         columns=["Visum_Index", "NO"]).astype(int)
        n_df_outputformat.set_index("NO", inplace=True)
        nodes_isoctimes_df.set_index("NO", inplace=True)
        n_df_outputformat = n_df_outputformat.join(nodes_isoctimes_df, how="inner")

        self.Visum.Net.Nodes.SetMultiAttValues(uda_name, n_df_outputformat[['Visum_Index', uda_name]].values)

        self.nf.Init()
        self.nf.AddCondition("OP_NONE", False, uda_name, "EqualVal", 0)
        if self.Visum.Net.Nodes.CountActive > 0:
            self.Visum.Net.Nodes.SetAllAttValues("TYPENO", 99, OnlyActive=True)
            self.Visum.Net.Nodes.SetAllAttValues("ZK_TYP_DETAIL", "N-N", OnlyActive=True)
        self.nf.Init()
        logging.info("Erreichbarkeitsprüfung abgeschlossen.")

    def create_cluster_actnodes(self, buffer: float, uda_name: str, nodecounter: int) -> pd.DataFrame:
        """Erzeugt räumliche Knoten-Cluster über Pufferdistanzen."""
        intersect_att = self.Visum.Net.CreateIntersectAttributePara()
        intersect_att.SetAttValue("SOURCENETOBJECTTYPE", "NODE")
        intersect_att.SetAttValue("SOURCEONLYACTIVE", "1")
        intersect_att.SetAttValue("SOURCEBUFFERSIZE", f"{buffer}m")
        intersect_att.SetAttValue("DESTNETOBJECTTYPE", "NODE")
        intersect_att.SetAttValue("DESTONLYACTIVE", "1")
        intersect_att.SetAttValue("DESTBUFFERSIZE", f"{buffer}m")
        intersect_att.SetAttValue("SOURCEATTRNAME", "NO")
        intersect_att.SetAttValue("DESTATTRNAME", uda_name)
        intersect_att.SetAttValue("NUMERICOPERATION", "INTERSECTION_SUM")
        intersect_att.SetAttValue("STRINGOPERATION", "INTERSECTION_CONCATENATE")
        intersect_att.SetAttValue("CONCATMAXLEN", "999999")
        intersect_att.SetAttValue("CONCATSEPARATOR", ",")

        self.Visum.Net.IntersectAttributes(intersect_att)
        nodelist_colnames = {"NO": int, "ZK_TYP_DETAIL": str, "ZK_NODES_INTERSECT": str, "XCOORD": float,
                             "YCOORD": float}
        df_nodes = self.get_multi_netobj_atts_with_id(self.Visum.Net.Nodes, nodelist_colnames)

        import re
        df_nodes['ZK_NODES_INTERSECT'] = [re.sub(r'[\s,\.]+', ',', str(x)).strip(',') for x in df_nodes['ZK_NODES_INTERSECT']]

        node_sets_ind = []
        for x in df_nodes['ZK_NODES_INTERSECT']:
            if not x or x == 'nan':
                continue
            valid_ids = []
            for i in x.split(','):
                if i.isdigit():
                    valid_ids.append(int(i))
            if valid_ids:
                node_sets_ind.append(set(valid_ids))

        node_sets_joint = []
        for s in node_sets_ind:
            overlaps = [r for r in node_sets_joint if not r.isdisjoint(s)]
            temp_s = s | set().union(*overlaps)
            node_sets_joint = [r for r in node_sets_joint if r not in overlaps]
            node_sets_joint.append(temp_s)

        df_mainnodes = pd.DataFrame({"node_clusters": node_sets_joint})
        if not df_mainnodes.empty:
            df_mainnodes["node_count"] = df_mainnodes["node_clusters"].apply(len)
            df_mainnodes['cluster_id'] = df_mainnodes.index + nodecounter

            for _, row in df_mainnodes.iterrows():
                cluster = row["node_clusters"]
                df_nodes.loc[df_nodes['NO'].isin(cluster), 'ZK_CLUSTER_ANZAHL_KNOTEN'] = row["node_count"]
                df_nodes.loc[df_nodes['NO'].isin(cluster), 'ZK_CLUSTER_ID'] = row['cluster_id']
        return df_nodes

    def cluster_by_nodetype(self, nodetype_buffer_dict: dict):
        """Clustert Knoten je Knotentyp-Kategorie."""
        self.add_uda(self.Visum.Net.Nodes, 'ZK_NODES_INTERSECT', value_type=62, def_val='')
        self.add_uda(self.Visum.Net.Nodes, 'ZK_CLUSTER_ID', value_type=1, def_val=0)
        self.add_uda(self.Visum.Net.Nodes, 'ZK_CLUSTER_ANZAHL_KNOTEN', value_type=1, def_val=0)

        zugangsknoten_counter = self.new_object_number('Nodes')
        master_df = pd.DataFrame()

        for knotentyp, puffer in nodetype_buffer_dict.items():
            self.nf.Init()
            self.nf.AddCondition("OP_NONE", False, "ZK_TYP_DETAIL", "EqualVal", knotentyp)

            if puffer > 0 and self.Visum.Net.Nodes.CountActive > 0:
                logging.info(f"Clustere Knotentyp '{knotentyp}' mit {puffer}m Puffer...")
                df_nodes = self.create_cluster_actnodes(puffer, 'ZK_NODES_INTERSECT', zugangsknoten_counter)
                if not df_nodes.empty and 'ZK_CLUSTER_ID' in df_nodes.columns and not df_nodes[
                    'ZK_CLUSTER_ID'].isna().all():
                    zugangsknoten_counter = int(df_nodes["ZK_CLUSTER_ID"].max()) + 1
                master_df = pd.concat([master_df, df_nodes])
            elif puffer == 0 and self.Visum.Net.Nodes.CountActive > 0:
                nodelist_colnames = {"NO": int, "ZK_TYP_DETAIL": str, "ZK_NODES_INTERSECT": str, "XCOORD": float,
                                     "YCOORD": float}
                df_nodes = self.get_multi_netobj_atts_with_id(self.Visum.Net.Nodes, nodelist_colnames)
                df_nodes["ZK_CLUSTER_ID"] = df_nodes.index + zugangsknoten_counter
                zugangsknoten_counter = int(df_nodes["ZK_CLUSTER_ID"].max()) + 1
                df_nodes["ZK_CLUSTER_ANZAHL_KNOTEN"] = 1
                master_df = pd.concat([master_df, df_nodes])

        if not master_df.empty:
            self.Visum.Net.Nodes.SetMultiAttValues("ZK_CLUSTER_ID", master_df[["VISUM_ID", "ZK_CLUSTER_ID"]].to_numpy())
            self.Visum.Net.Nodes.SetMultiAttValues("ZK_CLUSTER_ANZAHL_KNOTEN",
                                                   master_df[["VISUM_ID", "ZK_CLUSTER_ANZAHL_KNOTEN"]].to_numpy())
            master_df.set_index("VISUM_ID", drop=False, inplace=True)
        self.access_nodes_df = master_df.copy()

    def prepare_and_import_helper_net(self, netfile_path: Path):
        """Erzeugt neue Zugangsknoten und Hilfsstrecken (Typ 777) als .net Datei und lädt sie."""
        logging.info("Erzeuge Zugangsknoten und Hilfsstrecken (Typ 777)...")
        master = self.access_nodes_df.copy()
        if master.empty:
            return

        self.add_uda(self.Visum.Net.Nodes, uda_name='ZK_TYP', value_type=1, def_val=99)
        master['ZK_TYP'] = master['ZK_TYP_DETAIL'].apply(lambda row: int(row.split('-')[1]) if '-' in row else 99)

        dict_ranks = {key: int(key.replace('-', '')) for key in set(master["ZK_TYP_DETAIL"]) if '-' in key}
        if not dict_ranks:
            dict_ranks = {99: 99}

        agg_dict = {
            "VISUM_ID": set, "NO": set, "XCOORD": 'mean', "YCOORD": 'mean',
            "ZK_TYP_DETAIL": lambda x: min(x, key=lambda y: dict_ranks.get(y, 99)),
            "ZK_TYP": 'min'
        }
        zk_agg = master.groupby(["ZK_CLUSTER_ID"]).agg(agg_dict)
        zk_agg["ZK_CLUSTER_ID"] = zk_agg.index
        zk_agg["ZK_CLUSTER_ANZAHL_KNOTEN"] = zk_agg["VISUM_ID"].apply(len)

        mask_1er = (zk_agg.ZK_CLUSTER_ANZAHL_KNOTEN == 1)
        df_1er = zk_agg[mask_1er]
        if not df_1er.empty:
            self.Visum.Net.Nodes.SetMultiAttValues("ZK_TYP", df_1er.explode("VISUM_ID")[["VISUM_ID", "ZK_TYP"]].values)

        df_newnodes = zk_agg[~mask_1er][['ZK_CLUSTER_ID', 'ZK_TYP_DETAIL', 'XCOORD', 'YCOORD', 'ZK_TYP']].copy()
        df_newnodes.rename(columns={'ZK_CLUSTER_ID': 'NO'}, inplace=True)

        lt777 = self.Visum.Net.AddLinkType(777)
        lt777.SetAttValue("NAME", 'ZK_Hilfsstrecken')
        lt777.SetAttValue("V0PRT", 20)
        lt777.SetAttValue("NUMLANES", 1)
        lt777.SetAttValue("CAPPRT", 9999)

        zk_agg['Links'] = zk_agg.apply(
            lambda row: [(row['ZK_CLUSTER_ID'], i) for i in row['NO']] + [(i, row['ZK_CLUSTER_ID']) for i in row['NO']],
            axis=1)
        df_newlinks = pd.DataFrame({"Links": zk_agg.Links.explode()})
        df_newlinks[['FromNodeNo', 'ToNodeNo']] = pd.DataFrame(df_newlinks['Links'].tolist(), index=df_newlinks.index)

        df_newlinks["Sort"] = df_newlinks[['FromNodeNo', 'ToNodeNo']].min(axis=1)
        df_newlinks.sort_values(by=['Sort', 'FromNodeNo'], inplace=True)
        df_newlinks.drop(["Links", "Sort"], axis=1, inplace=True)
        df_newlinks.reset_index(drop=True, inplace=True)

        zugangsstrecken_counter = self.new_object_number('Links')
        df_newlinks.insert(0, "No", df_newlinks.index.repeat(2)[:len(df_newlinks)] + zugangsstrecken_counter)
        df_newlinks['TypeNo'] = 777
        df_newlinks['ZK_OBERTYP_STRECKE'] = 777

        with open(netfile_path, "w", encoding="utf-8") as file:
            file.write("$VISION\n$VERSION:VERSNR;FILETYPE;LANGUAGE;UNIT\n15;Net;ENG;KM\n")
            file.write(f"\n* Tabelle: Knoten\n$NODE:{';'.join(df_newnodes.columns).upper()}\n")
            df_newnodes.to_csv(file, header=False, sep=";", index=False, lineterminator='\n')

            file.write(f"\n* Tabelle: Strecken\n$LINK:{';'.join(df_newlinks.columns).upper()}\n")
            df_newlinks.to_csv(file, header=False, sep=";", index=False, lineterminator='\n')

            zone_df = df_newnodes[['NO', 'XCOORD', 'YCOORD']].copy()
            zone_df["NODENO"] = zone_df["NO"]
            new_zone_no = self.new_object_number('Zones')
            zone_df["NO"] = zone_df.index + new_zone_no
            zone_df["TYPENO"] = 99

            file.write(f"\n* Tabelle: Bezirke\n$ZONE:{';'.join(zone_df.drop('NODENO', axis=1).columns).upper()}\n")
            zone_df.drop("NODENO", axis=1).to_csv(file, header=False, sep=";", index=False, lineterminator='\n')

            conn_df = pd.DataFrame({'ZONENO': zone_df['NO'], 'NODENO': zone_df['NODENO']})
            conn_df = pd.concat([conn_df, conn_df], axis=0)
            conn_df.sort_values('ZONENO', inplace=True)
            conn_df.reset_index(drop=True, inplace=True)
            conn_df['DIRECTION'] = conn_df.apply(lambda x: 'O' if x.name % 2 == 0 else 'D', axis=1)
            conn_df['TSYSSET'] = ','.join([tsys.AttValue("CODE") for tsys in self.Visum.Net.TSystems.GetAll])

            file.write(f"\n* Tabelle: Anbindungen\n$CONNECTOR:{';'.join(conn_df.columns).upper()}\n")
            conn_df.to_csv(file, header=False, sep=";", index=False, lineterminator='\n')

        self.Visum.IO.LoadNet(str(netfile_path), ReadAdditive=True)

        # Assign Reachable_Node = True to newly created access nodes (ZK_TYP <= 5)
        self.nf.Init()
        if self.Visum.Net.Nodes.AttrExists("ZK_TYP"):
            self.nf.AddCondition("OP_NONE", False, "ZK_TYP", "LessEqualVal", 5)
            count_new = self.Visum.Net.Nodes.CountActive
            if count_new > 0:
                for reach_attr in ["Reachable_Node", "REACHABLE_NODE", "REACHABLE_NODE_PKW"]:
                    if self.Visum.Net.Nodes.AttrExists(reach_attr):
                        self.Visum.Net.Nodes.SetAllAttValues(reach_attr, True, OnlyActive=True)
                        logging.info(
                            f"Erreichbarkeits-Attribut ('{reach_attr} = True') für {count_new} neue Zugangsknoten gesetzt.")
                        break
        self.nf.Init()

    def postprocess_helper_links(self, procedure_xml_path: Path):
        """Führt eine 1er-Testumlegung aus und setzt ungenutzte Hilfsstrecken auf Typ 776."""
        logging.info("Führe Testumlegung zur Ausdünnung überflüssiger Hilfsstrecken durch...")
        self.Visum.Net.Matrices.RemoveAll()

        matrix_1er = self.add_mat(1, "1erUmlegung_TestAnbindungsknoten", "1erUmlegung_TestAnbindungsknoten")
        self.Visum.Net.DemandSegments.RemoveAll()

        pkw_code = "CAR"
        for m in self.Visum.Net.Modes.GetAll:
            if {m.AttValue("NAME"), m.AttValue("CODE")} & {"Pkw", "P", "Car", "CAR", "C"}:
                pkw_code = m.AttValue("CODE")
                break

        dseg = self.Visum.Net.AddDemandSegment("1erUmlegung_TestAnbindungsknoten", pkw_code)
        dseg.SetAttValue("NAME", "1erUmlegung_TestAnbindungsknoten")
        dseg.GetDemandDescription().SetAttValue("Matrix", 'Matrix([CODE]="1erUmlegung_TestAnbindungsknoten")')

        matrix_1er.SetValuesToResultOfFormula("If(FROM[TYPENO]=99 & TO[TYPENO]=99, 1, 0)")
        matrix_1er.SetAttValue("DSEGCODE", "1erUmlegung_TestAnbindungsknoten")

        self.Visum.Procedures.Open(str(procedure_xml_path))
        logging.info("Starte Visum-Verfahrensablauf zur Netz-Umlegung und Hilfsstrecken-Ausdünnung (kann ca. 10–15 Minuten dauern, bitte warten...)...")
        self.Visum.Procedures.Execute()
        logging.info("Visum-Verfahrensablauf erfolgreich ausgeführt.")

        self.lf.Init()
        self.lf.AddCondition("OP_NONE", False, "TYPENO", "ContainedIn", "777")
        self.lf.AddCondition("OP_AND", False, "VOLVEHPRT(AP)", "EqualVal", 0)

        lt776 = self.Visum.Net.AddLinkType(776)
        lt776.SetAttValue("NAME", 'ZK_Hilfsstrecken_Überflüssig')
        lt776.SetAttValue("V0PRT", 0)
        lt776.SetAttValue("NUMLANES", 0)
        lt776.SetAttValue("CAPPRT", 0)

        self.Visum.Net.Links.SetAllAttValues("TSYSSET", "", OnlyActive=True)
        self.Visum.Net.Links.SetAllAttValues("ZK_OBERTYP_STRECKE", 776, OnlyActive=True)
        self.Visum.Net.Links.SetAllAttValues("TYPENO", 776, OnlyActive=True)
        self.Visum.Filters.InitAll()

    def clean_up_and_save(self, output_ver_path: Path):
        """Bereinigt temporäre Elemente und speichert die Version."""
        logging.info("Räume temporäre Objekte auf...")
        self.Visum.Net.Matrices.RemoveAll()

        zf = self.Visum.Filters.ZoneFilter()
        zf.Init()
        zf.AddCondition("OP_NONE", False, "TYPENO", "ContainedIn", "99")
        self.Visum.Net.Zones.RemoveAll(OnlyActive=True)

        udas_to_del = ['LINK_IDENTIFIER', 'ZK_OBERTYP_UNGERICHTET', 'ZK_OBERTYP_VERSCHIEDENE', 'ZK_IST_RANDKNOTEN',
                       'ZK_NODES_INTERSECT']
        for uda in udas_to_del:
            try:
                self.Visum.Net.Links.DeleteUserDefinedAttribute(uda)
            except Exception:
                pass
            try:
                self.Visum.Net.Nodes.DeleteUserDefinedAttribute(uda)
            except Exception:
                pass

        self.Visum.Filters.InitAll()
        logging.info(f"Speichere abgeleitete Zugangsknoten unter: {output_ver_path.name}")
        self.Visum.SaveVersion(str(output_ver_path))


def run_access_node_derivation(target_project_dir=None, visum=None):
    base_project_dir, visum_input_dir = get_project_paths(target_project_dir)

    ver_dir = visum_input_dir / "ver"
    ver_dir.mkdir(parents=True, exist_ok=True)
    output_ver = ver_dir / "03_AccessNodes_Derived.ver"

    procedure_xml = find_helper_file(base_project_dir, "20230814_1erUmlegung_TestAnbindungsknoten.xml")
    logging.info(f"Verwende Verfahrensdatei: {procedure_xml.name}")

    if visum is None:
        input_ver = ver_dir / "02_Zones_Imported.ver"
        if not input_ver.exists():
            input_ver = visum_input_dir / "02_Zones_Imported.ver"

        if not input_ver.exists():
            logging.error(f"Eingabeversion fehlt: {input_ver}")
            sys.exit(1)

        logging.info(f"Starte Visum und lade: {input_ver.name}")
        visum = com.Dispatch("Visum.Visum.250")
        visum.Graphic.ShowMaximized()
        visum.LoadVersion(str(input_ver))

    deriver = AccessNodeDeriver(visum, visum_input_dir)
    deriver.process_access_nodes(output_ver_path=output_ver, procedure_xml=procedure_xml)

    return visum


if __name__ == "__main__":
    visum_instance = run_access_node_derivation()
    print("\n" + "=" * 60)
    print("Erfolg: Zugangsknoten erfolgreich abgeleitet!")
    print("Gespeicherte Datei: 03_AccessNodes_Derived.ver")
    print("=" * 60)
    input("\n[Hinweis] Visum bleibt geöffnet. Drücke ENTER im Terminal zum Beenden...")