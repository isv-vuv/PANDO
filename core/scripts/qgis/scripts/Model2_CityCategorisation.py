"""
Central Place Categorisation Script (Dynamic Model - Median Calibration & QA Logging)

This algorithm categorizes central places based on population and dynamic spatial separation.
It utilizes Median Nearest Neighbor distance and Median Spatial Density baselines to build
a robust spatial catchment hierarchy.

Methodological Features:
    • Median Distance Nearest Neighbor: Dynamic spatial scaling based on the median distance
      between reference urban centers (top percentile), avoiding outlier distortion.
    • Median Density Baseline: Density normalization relative to national median levels.
    • Functional Dual Centres: Pre-clustering of close municipalities into functional urban areas.
    • Centrality Mass Index (CMI): Global sorting based on population mass and local relative density.
    • Audit & QA Logging: Comprehensive scientific decision reporting and hierarchy verification.
"""

import statistics
from qgis.core import (
    QgsProcessing,
    QgsProcessingAlgorithm,
    QgsProcessingParameterFeatureSource,
    QgsProcessingParameterFeatureSink,
    QgsProcessingParameterNumber,
    QgsProcessingParameterCrs,
    QgsFeature,
    QgsField,
    QgsFeatureSink,
    QgsSpatialIndex,
    QgsDistanceArea
)
from qgis.PyQt.QtCore import QMetaType


class CentralPlaceCategorisationAlgorithm(QgsProcessingAlgorithm):
    INPUT = 'INPUT'
    ADM2_INPUT = 'ADM2_INPUT'
    
    MIN_POP_TYPENO0 = 'min_pop_type0'
    MIN_POP_TYPENO1 = 'min_pop_type1'
    MIN_POP_TYPENO2 = 'min_pop_type2'
    
    POP_TOLERANCE = 'population_tolerance_percentage'
    DIST_TOLERANCE = 'distance_tolerance_percent'
    DUAL_DIST = 'dual_centre_search_radius_km'
    DUAL_POP_TOL = 'dual_centre_population_tolerance_percentage'
    DUAL_MIN_POP_SHARE = 'dual_centre_minimum_population_share_percentage'
    
    OUTPUT = 'OUTPUT'
    OUTPUT_CRS = 'OUTPUT_CRS'

    def initAlgorithm(self, config=None):
        self.addParameter(QgsProcessingParameterFeatureSource(self.INPUT, 'Input Central Place Point Layer', [QgsProcessing.TypeVectorPoint]))
        self.addParameter(QgsProcessingParameterFeatureSource(self.ADM2_INPUT, 'ADM2 Boundary Polygon Layer', [QgsProcessing.TypeVectorPolygon]))

        self.addParameter(QgsProcessingParameterNumber(self.MIN_POP_TYPENO0, 'TypeNo 0 (MR) - Minimum Population', type=QgsProcessingParameterNumber.Integer, defaultValue=500000))
        self.addParameter(QgsProcessingParameterNumber(self.MIN_POP_TYPENO1, 'TypeNo 1 (HOC) - Minimum Population', type=QgsProcessingParameterNumber.Integer, defaultValue=200000))
        self.addParameter(QgsProcessingParameterNumber(self.MIN_POP_TYPENO2, 'TypeNo 2 (MOC) - Minimum Population', type=QgsProcessingParameterNumber.Integer, defaultValue=100000))

        self.addParameter(QgsProcessingParameterNumber(self.POP_TOLERANCE, 'Population Tolerance (%)', type=QgsProcessingParameterNumber.Integer, defaultValue=10, minValue=0, maxValue=50))
        self.addParameter(QgsProcessingParameterNumber(self.DIST_TOLERANCE, 'Distance Tolerance (%)', type=QgsProcessingParameterNumber.Integer, defaultValue=10, minValue=0, maxValue=30))
        
        self.addParameter(QgsProcessingParameterNumber(self.DUAL_DIST, 'Dual Centre Search Radius (km)', type=QgsProcessingParameterNumber.Integer, defaultValue=5))
        self.addParameter(QgsProcessingParameterNumber(self.DUAL_POP_TOL, 'Dual Centre Population Tolerance (%)', type=QgsProcessingParameterNumber.Integer, defaultValue=20, minValue=0, maxValue=50))
        self.addParameter(QgsProcessingParameterNumber(self.DUAL_MIN_POP_SHARE, 'Dual Centre Primary - Minimum Population Share (%)', type=QgsProcessingParameterNumber.Integer, defaultValue=15, minValue=0, maxValue=50))

        self.addParameter(QgsProcessingParameterCrs(self.OUTPUT_CRS, 'Output CRS', defaultValue='ProjectCrs'))
        self.addParameter(QgsProcessingParameterFeatureSink(self.OUTPUT, 'Categorized Layer'))

    def name(self): return 'central_place_categorisation'
    def displayName(self): return 'Central Place Categorisation (Dynamic Median)'
    def group(self): return 'PANDO'
    def groupId(self): return 'pando'
    def createInstance(self): return CentralPlaceCategorisationAlgorithm()

    def processAlgorithm(self, parameters, context, feedback):
        
        pop_field = 'POP'
        name_field = 'NAME'
        zone_pt_field = 'ZONENO'
        zone_adm_field = 'ZONENO'

        Reference_Percentile = 95
        TypeNo0_Radius_Factor = 1.5
        TypeNo1_Radius_Factor = 0.8
        TypeNo2_Radius_Factor = 0.4
        TypeNo3_Radius_Factor = 0.15
        Tuning_Exponent_K = 1.0
        Raw_Pop_Dominance_Factor = 3.0

        source = self.parameterAsSource(parameters, self.INPUT, context)
        adm_source = self.parameterAsSource(parameters, self.ADM2_INPUT, context)
        
        param_pop_type0 = self.parameterAsInt(parameters, self.MIN_POP_TYPENO0, context)
        param_pop_type1 = self.parameterAsInt(parameters, self.MIN_POP_TYPENO1, context)
        param_pop_type2 = self.parameterAsInt(parameters, self.MIN_POP_TYPENO2, context)
        
        pop_tolerance_pct = self.parameterAsInt(parameters, self.POP_TOLERANCE, context)
        dist_tolerance_pct = self.parameterAsInt(parameters, self.DIST_TOLERANCE, context)
        dual_dist_km = self.parameterAsInt(parameters, self.DUAL_DIST, context)
        dual_pop_tol_pct = self.parameterAsInt(parameters, self.DUAL_POP_TOL, context)
        dual_min_pop_share_pct = self.parameterAsInt(parameters, self.DUAL_MIN_POP_SHARE, context)

        output_crs = self.parameterAsCrs(parameters, self.OUTPUT_CRS, context)
        if not output_crs.isValid(): output_crs = source.sourceCrs()

        da_adm = QgsDistanceArea()
        da_adm.setSourceCrs(adm_source.sourceCrs(), context.transformContext())
        da_adm.setEllipsoid('WGS84')
        
        adm2_area_registry = {}
        for adm_feat in adm_source.getFeatures():
            z_id = adm_feat[zone_adm_field]
            if z_id is not None:
                clean_id = str(z_id).strip()
                area_m2 = da_adm.measureArea(adm_feat.geometry())
                adm2_area_registry[clean_id] = max(area_m2 / 1000000.0, 0.001)

        da_pts = QgsDistanceArea()
        da_pts.setSourceCrs(source.sourceCrs(), context.transformContext())
        da_pts.setEllipsoid('WGS84')

        def get_ellipsoid_dist_meters(g1, g2):
            return da_pts.measureLine(g1.asPoint(), g2.asPoint())

        fields = source.fields()
        fields.append(QgsField('TypeNo', QMetaType.Type.Int, '', 10, 0))
        fields.append(QgsField('DualCentre', QMetaType.Type.QString, '', 254, 0)) 
        fields.append(QgsField('DualPartner', QMetaType.Type.QString, '', 254, 0))
        fields.append(QgsField('EffPop', QMetaType.Type.Int, '', 12, 0))
        fields.append(QgsField('Density', QMetaType.Type.Double, '', 10, 2))
        fields.append(QgsField('RelDensity', QMetaType.Type.Double, '', 10, 2))
        fields.append(QgsField('InitLevel', QMetaType.Type.Int, '', 10, 0))
        fields.append(QgsField('InCatchmOf', QMetaType.Type.QString, '', 254, 0))
        fields.append(QgsField('CatchmLevel', QMetaType.Type.Int, '', 10, 0))
        fields.append(QgsField('CatReason', QMetaType.Type.QString, '', 254, 0))
        fields.append(QgsField('BaseDist', QMetaType.Type.Double, '', 10, 2))
        fields.append(QgsField('CentMassIdx', QMetaType.Type.Double, '', 12, 2))

        sink, dest_id = self.parameterAsSink(parameters, self.OUTPUT, context, fields, source.wkbType(), output_crs)

        feats = list(source.getFeatures())
        feat_dict = {f.id(): f for f in feats}
        spatial_index = QgsSpatialIndex()
        for f in feats: spatial_index.addFeature(f)

        feat_density = {}
        for f in feats:
            orig_pop = float(f[pop_field]) if f[pop_field] is not None else 0.0
            clean_pt_id = str(f[zone_pt_field]).strip()
            zone_area = adm2_area_registry.get(clean_pt_id, None)
            feat_density[f.id()] = (orig_pop / zone_area) if zone_area is not None else 0.0

        # =========================================================================
        # HEADER LOGGING
        # =========================================================================
        feedback.pushInfo("="*80)
        feedback.pushInfo("CENTRAL PLACE CATEGORISATION (Median Dynamic Model v2.0)")
        feedback.pushInfo("="*80)
        feedback.pushInfo(f"  Input points              : {len(feats):,}")
        feedback.pushInfo(f"  ADM2 polygons             : {len(adm2_area_registry):,}")
        feedback.pushInfo("")
        feedback.pushInfo(f"  Reference percentile      : {Reference_Percentile} %")
        feedback.pushInfo(f"  Dual centre search radius : {dual_dist_km} km")
        feedback.pushInfo(f"  Dual min. pop share       : {dual_min_pop_share_pct} %")
        feedback.pushInfo(f"  Population tolerance      : {pop_tolerance_pct} %")
        feedback.pushInfo(f"  Distance tolerance        : {dist_tolerance_pct} %")
        feedback.pushInfo("="*80 + "\n")

        # =========================================================================
        # PHASE 1: FUNCTIONAL CENTRE CLUSTERING
        # =========================================================================
        feedback.pushInfo("="*80)
        feedback.pushInfo("PHASE 1 - FUNCTIONAL CENTRE CLUSTERING")
        feedback.pushInfo("="*80)

        dual_dist_meters = dual_dist_km * 1000
        adj = {}
        for f in feats:
            geom = f.geometry()
            bbox = geom.boundingBox()
            bbox.grow(dual_dist_meters)
            candidates = spatial_index.intersects(bbox)
            adj[f.id()] = [c_id for c_id in candidates if c_id != f.id() and get_ellipsoid_dist_meters(geom, feat_dict[c_id].geometry()) < dual_dist_meters]

        visited = set()
        effective_pop = {}
        is_secondary_dual = {}
        primary_dual_set = set()
        dual_partners_registry = {}
        cluster_records = []
        cluster_warnings = []

        cluster_counter = 0
        for f in feats:
            f_id = f.id()
            if f_id not in visited:
                cluster = []
                queue = [f_id]
                visited.add(f_id)
                while queue:
                    curr = queue.pop(0)
                    cluster.append(curr)
                    for nxt in adj[curr]:
                        if nxt not in visited:
                            visited.add(nxt)
                            queue.append(nxt)
                
                if len(cluster) > 1:
                    cluster_counter += 1
                    total_cluster_pop = sum(float(feat_dict[fid][pop_field]) if feat_dict[fid][pop_field] is not None else 0.0 for fid in cluster)

                    # Top density candidate in cluster
                    top_density_candidate = max(cluster, key=lambda fid: (feat_density[fid], float(feat_dict[fid][pop_field]) if feat_dict[fid][pop_field] is not None else 0.0))
                    top_candidate_pop = float(feat_dict[top_density_candidate][pop_field]) if feat_dict[top_density_candidate][pop_field] is not None else 0.0
                    top_candidate_share = (top_candidate_pop / total_cluster_pop) * 100.0 if total_cluster_pop > 0 else 0.0

                    pop_eligible = [fid for fid in cluster if (float(feat_dict[fid][pop_field]) if feat_dict[fid][pop_field] is not None else 0.0) >= total_cluster_pop * (dual_min_pop_share_pct / 100.0)]
                    
                    if not pop_eligible:
                        pop_eligible = cluster

                    primary_id = max(pop_eligible, key=lambda fid: (feat_density[fid], float(feat_dict[fid][pop_field]) if feat_dict[fid][pop_field] is not None else 0.0))
                    
                    # Track if primary was replaced due to share rule
                    if primary_id != top_density_candidate:
                        top_cand_name = str(feat_dict[top_density_candidate][name_field]) if feat_dict[top_density_candidate][name_field] is not None else f"ID {top_density_candidate}"
                        prim_name_str = str(feat_dict[primary_id][name_field]) if feat_dict[primary_id][name_field] is not None else f"ID {primary_id}"
                        cluster_warnings.append({
                            'rejected_primary': top_cand_name,
                            'pop': top_candidate_pop,
                            'cluster_pop': total_cluster_pop,
                            'share': top_candidate_share,
                            'replaced_by': prim_name_str
                        })

                    effective_pop[primary_id] = total_cluster_pop
                    primary_dual_set.add(primary_id)
                    
                    primary_name = str(feat_dict[primary_id][name_field]) if feat_dict[primary_id][name_field] is not None else f"ID {primary_id}"
                    secondary_names = []
                    for fid in cluster:
                        if fid != primary_id:
                            is_secondary_dual[fid] = True
                            effective_pop[fid] = float(feat_dict[fid][pop_field])
                            dual_partners_registry[fid] = primary_name
                            sec_name = str(feat_dict[fid][name_field]) if feat_dict[fid][name_field] is not None else f"ID {fid}"
                            secondary_names.append(sec_name)
                    
                    partners_str = ", ".join(secondary_names)
                    dual_partners_registry[primary_id] = partners_str
                    cluster_records.append({
                        'id': cluster_counter,
                        'primary': primary_name,
                        'members_count': len(cluster),
                        'members_list': [str(feat_dict[fid][name_field]) for fid in cluster],
                        'cluster_pop': int(total_cluster_pop)
                    })
                else:
                    effective_pop[f_id] = float(f[pop_field]) if f[pop_field] is not None else 0.0
                    dual_partners_registry[f_id] = "-"

        cluster_records.sort(key=lambda x: x['cluster_pop'], reverse=True)

        feedback.pushInfo(f"Detected {len(cluster_records)} functional dual centre clusters:\n")
        feedback.pushInfo(f"  {'Cluster':<10} | {'Primary Centre':<28} | {'Members':<10} | {'Cluster Pop.'}")
        feedback.pushInfo("  " + "-"*65)
        for cl in cluster_records[:8]:
            feedback.pushInfo(f"  {cl['id']:<10} | {cl['primary']:<28} | {cl['members_count']:<10} | {cl['cluster_pop']:,}")
        if len(cluster_records) > 8:
            feedback.pushInfo(f"  ... and {len(cluster_records)-8} additional clusters formed.")
        
        if cluster_records:
            largest = cluster_records[0]
            feedback.pushInfo(f"\nExample Structure (Largest Cluster: {largest['primary']}):")
            feedback.pushInfo(f"  Members ({largest['members_count']}): {', '.join(largest['members_list'][:10])}{'...' if len(largest['members_list']) > 10 else ''}")

        if cluster_warnings:
            feedback.pushInfo("\n" + "!"*80)
            feedback.pushInfo("CLUSTER PRIMARY SHARE REPLACEMENTS (QA WARNINGS)")
            feedback.pushInfo("!"*80)
            for cw in cluster_warnings:
                feedback.pushInfo(f"  [WARNING] Rejected Primary Candidate: {cw['rejected_primary']}")
                feedback.pushInfo(f"            Pop: {int(cw['pop']):,} | Cluster Pop: {int(cw['cluster_pop']):,} | Share: {cw['share']:.1f}% (Min required: {dual_min_pop_share_pct}%)")
                feedback.pushInfo(f"            --> Replaced by Primary Core: {cw['replaced_by']}")
                feedback.pushInfo("  " + "-"*65)

        feedback.pushInfo("="*80 + "\n")

        # =========================================================================
        # PHASE 2: BASELINE CALIBRATION & MEDIAN NEAREST NEIGHBOR
        # =========================================================================
        feedback.pushInfo("="*80)
        feedback.pushInfo("PHASE 2 - BASELINE CALIBRATION (MEDIAN APPROACH)")
        feedback.pushInfo("="*80)

        ref_candidates = [f for f in feats if not is_secondary_dual.get(f.id(), False)]
        ref_feats = []
        for f in ref_candidates:
            try:
                zone_val = int(float(str(f[zone_pt_field]).strip()))
                if 2000000 < zone_val < 3000000: ref_feats.append(f)
            except (ValueError, TypeError): continue
        
        if not ref_feats: ref_feats = ref_candidates

        feats_sorted_by_orig = list(ref_feats)
        feats_sorted_by_orig.sort(key=lambda f: effective_pop[f.id()], reverse=True)
        cutoff = max(2, int(len(feats_sorted_by_orig) * ((100.0 - Reference_Percentile) / 100.0)))
        top_category_feats = feats_sorted_by_orig[:cutoff]

        top_idx = QgsSpatialIndex()
        top_dict = {}
        for tf in top_category_feats:
            top_idx.addFeature(tf)
            top_dict[tf.id()] = tf

        top_city_logs = []
        nn_distances_km = []

        for tf in top_category_feats:
            nearest = top_idx.nearestNeighbor(tf.geometry(), 2)
            closest_centre = "Unknown"
            dist_to_centre = 0.0
            for n_id in nearest:
                if n_id != tf.id():
                    dist_m = get_ellipsoid_dist_meters(tf.geometry(), top_dict[n_id].geometry())
                    dist_to_centre = dist_m / 1000.0
                    closest_centre = str(top_dict[n_id][name_field]) if top_dict[n_id][name_field] else f"ID {n_id}"
                    nn_distances_km.append(dist_to_centre)
                    break
            c_name = str(tf[name_field]) if tf[name_field] else f"ID {tf.id()}"
            c_pop = int(effective_pop[tf.id()])
            top_city_logs.append((c_name, c_pop, closest_centre, dist_to_centre))

        top_city_logs.sort(key=lambda x: x[1], reverse=True)

        feedback.pushInfo(f"Reference Centres Analyzed ({len(top_category_feats)} nodes):\n")
        feedback.pushInfo(f"  {'City Name':<30} | {'Population':<12} | {'Closest Reference':<25} | {'NN Distance'}")
        feedback.pushInfo("  " + "-"*78)
        for log in top_city_logs[:8]:
            feedback.pushInfo(f"  {log[0]:<30} | {log[1]:<12,} | {log[2]:<25} | {log[3]:.1f} km")
        if len(top_city_logs) > 8:
            feedback.pushInfo(f"  ... and {len(top_city_logs)-8} additional reference nodes analyzed.")

        # MEDIAN DISTANCE CALCULATION
        min_nn_dist = min(nn_distances_km) if nn_distances_km else 0.0
        median_nn_dist = statistics.median(nn_distances_km) if nn_distances_km else 50.0
        mean_nn_dist = statistics.mean(nn_distances_km) if nn_distances_km else 50.0
        max_nn_dist = max(nn_distances_km) if nn_distances_km else 0.0

        base_dist_km = median_nn_dist
        base_dist_type0 = base_dist_km * TypeNo0_Radius_Factor
        base_dist_type1 = base_dist_km * TypeNo1_Radius_Factor
        base_dist_type2 = base_dist_km * TypeNo2_Radius_Factor
        base_dist_type3 = base_dist_km * TypeNo3_Radius_Factor

        feedback.pushInfo("\nNearest Neighbor Distance Statistics:")
        feedback.pushInfo(f"  Minimum Distance   : {min_nn_dist:.1f} km")
        feedback.pushInfo(f"  Median Distance    : {median_nn_dist:.1f} km  <-- (Used as National Spatial Baseline)")
        feedback.pushInfo(f"  Mean Distance      : {mean_nn_dist:.1f} km")
        feedback.pushInfo(f"  Maximum Distance   : {max_nn_dist:.1f} km")
        feedback.pushInfo("")
        feedback.pushInfo("Derived Network Protection Radii:")
        feedback.pushInfo(f"  TypeNo 0 (Metropolitan Regions) : {base_dist_type0:.1f} km")
        feedback.pushInfo(f"  TypeNo 1 (Higher-Order Centres)  : {base_dist_type1:.1f} km")
        feedback.pushInfo(f"  TypeNo 2 (Middle-Order Centres)  : {base_dist_type2:.1f} km")
        feedback.pushInfo(f"  TypeNo 3 (Lower-Order Centres)   : {base_dist_type3:.1f} km")
        feedback.pushInfo("="*80 + "\n")

        # =========================================================================
        # PHASE 3: DENSITY STATISTICS (MEDIAN BASELINE)
        # =========================================================================
        feedback.pushInfo("="*80)
        feedback.pushInfo("PHASE 3 - DENSITY STATISTICS")
        feedback.pushInfo("="*80)

        ref_densities = [feat_density[f.id()] for f in ref_feats]
        
        min_density = min(ref_densities) if ref_densities else 0.0
        median_density = statistics.median(ref_densities) if ref_densities else 1.0
        mean_density = statistics.mean(ref_densities) if ref_densities else 1.0
        max_density = max(ref_densities) if ref_densities else 0.0

        # Replace average baseline with median baseline for relative density calculations
        avg_density = median_density

        feedback.pushInfo("Empirical Density Baseline Statistics (Pop/km²):")
        feedback.pushInfo(f"  Minimum Density    : {min_density:.1f}")
        feedback.pushInfo(f"  Median Density     : {median_density:.1f}  <-- (Used as Baseline Normalizer)")
        feedback.pushInfo(f"  Mean Density       : {mean_density:.1f}")
        feedback.pushInfo(f"  Maximum Density    : {max_density:.1f}")

        density_rankings = [(str(f[name_field]) if f[name_field] else f"ID {f.id()}", feat_density[f.id()]) for f in ref_feats]
        density_rankings.sort(key=lambda x: x[1], reverse=True)

        feedback.pushInfo("\nHighest Density Locations:")
        for dr in density_rankings[:5]:
            feedback.pushInfo(f"  • {dr[0]:<30} : {dr[1]:,.1f} Pop/km²")
        feedback.pushInfo("="*80 + "\n")

        # =========================================================================
        # PHASE 4: CENTRALITY MASS INDEX (CMI)
        # =========================================================================
        feedback.pushInfo("="*80)
        feedback.pushInfo("PHASE 4 - CENTRALITY MASS INDEX (CMI)")
        feedback.pushInfo("="*80)

        gen_factor = 1.0 - (pop_tolerance_pct / 100.0)
        dual_factor = 1.0 - (dual_pop_tol_pct / 100.0)

        feat_elig_category = {}
        for f in feats:
            f_id = f.id()
            if is_secondary_dual.get(f_id, False):
                feat_elig_category[f_id] = 4
                continue
            pop = effective_pop[f_id]
            is_dual = f_id in primary_dual_set
            current_factor = dual_factor if is_dual else gen_factor
            
            if pop >= (param_pop_type0 * current_factor): feat_elig_category[f_id] = 0
            elif pop >= (param_pop_type1 * current_factor): feat_elig_category[f_id] = 1
            elif pop >= (param_pop_type2 * current_factor): feat_elig_category[f_id] = 2
            else: feat_elig_category[f_id] = 3

        feat_rel_density = {}
        feat_cmi = {}
        for f in feats:
            f_id = f.id()
            density = feat_density[f_id]
            rel_density = density / median_density if median_density > 0 else 1.0
            feat_rel_density[f_id] = rel_density
            pop = effective_pop[f_id]
            feat_cmi[f_id] = pop * (rel_density ** Tuning_Exponent_K)

        feats.sort(key=lambda f: -feat_cmi[f.id()])

        feedback.pushInfo("Top Queue Priorities (Sorted by CMI):\n")
        feedback.pushInfo(f"  {'City Name':<30} | {'Effective Pop':<14} | {'Rel Density':<12} | {'CMI Score'}")
        feedback.pushInfo("  " + "-"*75)
        for f in feats[:10]:
            f_id = f.id()
            c_name = str(f[name_field]) if f[name_field] else f"ID {f_id}"
            feedback.pushInfo(f"  {c_name:<30} | {int(effective_pop[f_id]):<14,} | {feat_rel_density[f_id]:<12.2f} | {feat_cmi[f_id]:,.0f}")
        feedback.pushInfo("="*80 + "\n")

        # =========================================================================
        # PHASE 5: HIERARCHICAL ALLOCATION & SPATIAL CONFLICT RESOLUTION
        # =========================================================================
        feedback.pushInfo("="*80)
        feedback.pushInfo("PHASE 5 - HIERARCHICAL ALLOCATION")
        feedback.pushInfo("="*80)

        assigned_centers = {'Type0': [], 'Type1': [], 'Type2': [], 'Type3': []}
        conflict_count = 0
        conflict_logs = []
        pop_dominance_logs = []

        for feat in feats:
            if feedback.isCanceled(): break
            f_id = feat.id()
            geom = feat.geometry()
            orig_pop = float(feat[pop_field]) if feat[pop_field] is not None else 0.0
            partner_string = dual_partners_registry.get(f_id, "-")
            density = feat_density[f_id]
            elig_category = feat_elig_category[f_id]
            c_name = str(feat[name_field]) if feat[name_field] is not None else f"ID {f_id}"

            if is_secondary_dual.get(f_id, False):
                out_feat = QgsFeature(fields)
                out_feat.setGeometry(geom)
                out_feat.setAttributes(feat.attributes() + [4, 'Secondary', partner_string, int(orig_pop), density, 0.0, 4, "-", -1, f"Dual Centre Subordination: Integrated with primary partner {partner_string}.", base_dist_km, 0.0])
                sink.addFeature(out_feat, QgsFeatureSink.FastInsert)
                continue

            pop = effective_pop[f_id]
            dual_status = f"Dual Centre: {partner_string}" if f_id in primary_dual_set else 'None'
            current_factor = dual_factor if f_id in primary_dual_set else gen_factor

            thresh_type0 = param_pop_type0 * current_factor
            thresh_type1 = param_pop_type1 * current_factor
            thresh_type2 = param_pop_type2 * current_factor

            rel_density = feat_rel_density[f_id]
            reduction_pct = min(float(dist_tolerance_pct), (rel_density - 1.0) * 5.0) if rel_density > 1.0 else 0.0
            radius_modifier = 1.0 - (reduction_pct / 100.0)

            dist_type0_m = base_dist_type0 * 1000 * radius_modifier
            dist_type1_m = base_dist_type1 * 1000 * radius_modifier
            dist_type2_m = base_dist_type2 * 1000 * radius_modifier
            dist_type3_m = base_dist_type3 * 1000 * radius_modifier

            close_to_type0 = False
            blck_by_city = "-"
            blck_by_cat = -1
            blck_by_rawpop = 0.0
            for other_name, other_geom, other_rawpop in assigned_centers['Type0']:
                if get_ellipsoid_dist_meters(geom, other_geom) < dist_type0_m:
                    close_to_type0 = True
                    blck_by_city = other_name
                    blck_by_cat = 0
                    blck_by_rawpop = other_rawpop
                    break

            close_to_type0_at_type1 = False
            close_to_type1_at_type1 = False
            for other_name, other_geom, other_rawpop in assigned_centers['Type0']:
                if get_ellipsoid_dist_meters(geom, other_geom) < dist_type1_m:
                    close_to_type0_at_type1 = True
                    if blck_by_city == "-": blck_by_city, blck_by_cat, blck_by_rawpop = other_name, 0, other_rawpop
            for other_name, other_geom, other_rawpop in assigned_centers['Type1']:
                if get_ellipsoid_dist_meters(geom, other_geom) < dist_type1_m:
                    close_to_type1_at_type1 = True
                    if blck_by_city == "-": blck_by_city, blck_by_cat, blck_by_rawpop = other_name, 1, other_rawpop

            close_to_type0_at_type2 = False
            close_to_type1_at_type2 = False
            close_to_type2_at_type2 = False
            for other_name, other_geom, other_rawpop in assigned_centers['Type0']:
                if get_ellipsoid_dist_meters(geom, other_geom) < dist_type2_m: close_to_type0_at_type2 = True
            for other_name, other_geom, other_rawpop in assigned_centers['Type1']:
                if get_ellipsoid_dist_meters(geom, other_geom) < dist_type2_m: close_to_type1_at_type2 = True
            for other_name, other_geom, other_rawpop in assigned_centers['Type2']:
                if get_ellipsoid_dist_meters(geom, other_geom) < dist_type2_m:
                    close_to_type2_at_type2 = True
                    if blck_by_city == "-": blck_by_city, blck_by_cat, blck_by_rawpop = other_name, 2, other_rawpop

            type_no = 3
            reason = ""

            if pop >= thresh_type0 and not close_to_type0:
                type_no = 0
                assigned_centers['Type0'].append((c_name, geom, orig_pop))
                reason = "TypeNo 0 (MR): Independent"
            elif pop >= thresh_type1 and not close_to_type0_at_type1 and not close_to_type1_at_type1:
                type_no = 1
                assigned_centers['Type1'].append((c_name, geom, orig_pop))
                reason = "TypeNo 1 (HOC): Independent"
            elif pop >= thresh_type2 and not close_to_type0_at_type2 and not close_to_type1_at_type2 and not close_to_type2_at_type2:
                type_no = 2
                assigned_centers['Type2'].append((c_name, geom, orig_pop))
                reason = "TypeNo 2 (MOC): Independent"
            else:
                all_centers = [(0, n, g, p) for n, g, p in assigned_centers['Type0']] + \
                              [(1, n, g, p) for n, g, p in assigned_centers['Type1']] + \
                              [(2, n, g, p) for n, g, p in assigned_centers['Type2']] + \
                              [(3, n, g, p) for n, g, p in assigned_centers['Type3']]
                close_to_any_type3 = False
                for other_cat, other_name, other_geom, other_rawpop in all_centers:
                    if get_ellipsoid_dist_meters(geom, other_geom) < dist_type3_m:
                        close_to_any_type3 = True
                        if blck_by_city == "-":
                            blck_by_city, blck_by_cat, blck_by_rawpop = other_name, other_cat, other_rawpop
                        break
                
                if close_to_any_type3:
                    type_no = 4
                    if pop < thresh_type2: reason = "TypeNo 4 (Mun): Insufficient population"
                    else: reason = f"Subsumed inside TypeNo {blck_by_cat} radius ({blck_by_city})"
                else:
                    type_no = 3
                    assigned_centers['Type3'].append((c_name, geom, orig_pop))
                    if pop < thresh_type2: reason = "TypeNo 3 (LOC): Local spatial independence"
                    else: reason = f"Constrained inside TypeNo {blck_by_cat} radius ({blck_by_city})"

            if elig_category < type_no:
                conflict_count += 1
                conflict_logs.append((c_name, elig_category, type_no, blck_by_city, blck_by_cat, reason))

            if blck_by_city != "-" and blck_by_rawpop > 0 and orig_pop >= blck_by_rawpop * Raw_Pop_Dominance_Factor:
                pop_dominance_logs.append((c_name, int(orig_pop), blck_by_city, int(blck_by_rawpop), type_no, blck_by_cat))

            out_feat = QgsFeature(fields)
            out_feat.setGeometry(geom)
            out_feat.setAttributes(feat.attributes() + [
                type_no, dual_status, partner_string, int(pop), density, rel_density, elig_category, blck_by_city, blck_by_cat, reason, base_dist_km, float(feat_cmi[f_id])
            ])
            sink.addFeature(out_feat, QgsFeatureSink.FastInsert)

        feedback.pushInfo(f"Allocation Decisions & Spatial Adjustments ({len(conflict_logs)} downgrades):\n")
        feedback.pushInfo(f"  {'City Name':<26} | {'Eligible':<10} | {'Assigned':<10} | {'Reason / Suppressor'}")
        feedback.pushInfo("  " + "-"*75)
        for log in conflict_logs[:12]:
            feedback.pushInfo(f"  {log[0]:<26} | TypeNo {log[1]:<3} | TypeNo {log[2]:<3} | {log[5]}")
        if len(conflict_logs) > 12:
            feedback.pushInfo(f"  ... and {len(conflict_logs)-12} additional spatial conflict downgrades evaluated.")
        feedback.pushInfo("="*80 + "\n")

        # =========================================================================
        # PHASE 6: QUALITY ASSURANCE & AUDIT SUMMARY
        # =========================================================================
        feedback.pushInfo("="*80)
        feedback.pushInfo("PHASE 6 - QUALITY ASSURANCE & AUDIT SUMMARY")
        feedback.pushInfo("="*80)

        if pop_dominance_logs:
            feedback.pushInfo(f"HIERARCHY WARNINGS (Raw Population Dominance >= {Raw_Pop_Dominance_Factor:.0f}x Blocker):")
            for log in pop_dominance_logs[:10]:
                ratio = log[1] / log[3] if log[3] > 0 else 0
                feedback.pushInfo(f"  [WARNING] {log[0]} (POP: {log[1]:,}) downgraded by {log[2]} (POP: {log[3]:,})")
                feedback.pushInfo(f"            Pop Ratio: {ratio:.1f}x | Result: TypeNo {log[4]} vs Blocker TypeNo {log[5]}")
                feedback.pushInfo("  " + "-"*65)
            if len(pop_dominance_logs) > 10:
                feedback.pushInfo(f"  ... and {len(pop_dominance_logs)-10} additional dominance warnings.")
            feedback.pushInfo("")

        total_warnings = len(cluster_warnings) + len(pop_dominance_logs)
        feedback.pushInfo("QUALITY SUMMARY MATRIX:")
        feedback.pushInfo(f"  • Total Central Places Processed : {len(feats):,}")
        feedback.pushInfo(f"  • Functional Clusters Formed     : {len(cluster_records)}")
        feedback.pushInfo(f"  • Cluster Primaries Replaced     : {len(cluster_warnings)}")
        feedback.pushInfo(f"  • Hierarchy Conflicts Solved     : {conflict_count}")
        feedback.pushInfo(f"  • Raw Pop Dominance Warnings     : {len(pop_dominance_logs)}")
        feedback.pushInfo(f"  • Total Audit Warnings           : {total_warnings}")
        feedback.pushInfo("="*80 + "\n")

        return {self.OUTPUT: dest_id}