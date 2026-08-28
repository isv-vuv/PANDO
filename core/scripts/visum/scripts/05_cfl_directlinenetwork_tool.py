## @package cfl_directlinenetwork_tool
# @brief Contains general methods and the DirectLineNetworkCalculator class for calculating direct-line connections
# considering the centrality of zones
#
# This tool enables the calculation of air-line network connections between different zones while
# considering their central place hierarchy. It provides the following main functionalities:
# - Connection calculations based on centrality levels
# - Flexible distance calculations (Haversine or Euclidean distance)
# - Import and export of Visum network data
# - Support for multilingual outputs
# - Comprehensive network connection analysis capabilities
#
# The tool was developed to assist transport planners in analyzing catchment areas and
# supply relationships between zones.
#
# @author MaS, loosely based on C# Code GS 2009
# @date 2022


import pandas as pd
import logging
import numpy as np
from scipy.spatial import Delaunay
from pathlib import Path
from math import radians
import win32com.client as com
import importlib
try:
    from language_management import Translator
except ImportError:
    Translator = importlib.import_module("05_language_management").Translator


# ====== general, useful functions =====

## Opens a Visum instance if not already open
# Enables simultaneous calling of the file internally and externally in Visum
# @param path (Path/str) to a Visum version file
# @param version Visum version, default 240
# @return Visum instance
def open_visum(path, version=240):
    try:
        # tests if the variable Visum exists
        global Visum
        Visum
    except NameError:
        # if not - open a Visum instance
        logging.info('Initializing Visum instance.')
        Visum = com.Dispatch(f"Visum.Visum.{version}")
        logging.info('Opening version file:' + f'{path}')
        Visum.LoadVersion(path)
        logging.info('Version file successfully loaded.')
    return Visum


## Exports the data of a Visum object type in network file format
# @param[in] object Visum object type (singular), e.g. 'link'
# @param[in] df_object_attributes_to_write Data table of the object. Table contains only attributes that can be
# imported into Visum (especially the necessary attributes)
# @param[in] file Target file, in write or append mode (w or a)
def write_object_to_net(object, df_object_attributes_to_write, file):
    header = ["*", "*"]
    header.insert(1, "* Table: " + object + "s")
    header.append(
        ("$" + object.upper().replace(" ", "") + ":" + ";".join(df_object_attributes_to_write.columns)).upper() + "\n")

    # Header is written
    file.write("\n".join(header))
    # Table is written
    df_object_attributes_to_write.to_csv(file, header=False, sep=";", index=False)


## Checks a matrix for symmetry
# @param[in] matrix Matrix to be tested for symmetry
# @param[in] tol Tolerance for allowed deviation, default 1e-8
# @return True or False
def is_symmetric(matrix, tol=1e-8):
    # Application of the maximum norm for the difference between the matrix and its transpose
    # Norm > 0 -> no symmetry
    return np.linalg.norm(matrix.astype(int) - matrix.T.astype(int), np.inf) < tol


## Calculation of the distance between coordinates (Lat, Lon)
# Implementation of the Haversine formula
# @param[in] x1 x-coordinate of point 1
# @param[in] y1 y-coordinate of point 1
# @param[in] vec_x2 x-coordinate vector of points
# @param[in] vec_y2 y-coordinate vector of points
# @return Vector with distances of all points in the point vector to point 1
def calculate_distance_coordinates_haversine(x1, y1, vec_x2, vec_y2):
    # approximate radius of earth in km
    R = 6373.0

    lat1 = radians(y1)
    lon1 = radians(x1)
    vec_lat2 = np.radians(vec_y2)
    vec_lon2 = np.radians(vec_x2)

    # todo Case distinction for negative coordinates
    diff_lon = vec_lon2 - lon1
    diff_lat = vec_lat2 - lat1

    # Haversine formula
    tmp = np.sin(diff_lat / 2) ** 2 + np.cos(lat1) * np.cos(vec_lat2) * np.sin(diff_lon / 2) ** 2
    distances_km = R * 2 * np.arcsin(np.sqrt(tmp))

    return distances_km


## Calculation of the distance between coordinates (x, y)
# Calculates the Euclidean distance between a reference point and a set of points
# using the formula: distance = sqrt((x2-x1)^2 + (y2-y1)^2)
# @param[in] x1 x-coordinate of point 1 (reference point)
# @param[in] y1 y-coordinate of point 1 (reference point)
# @param[in] vec_x2 Vector of x-coordinates for comparison points
# @param[in] vec_y2 Vector of y-coordinates for comparison points
# @return Vector containing distances from reference point to all comparison points
def calculate_eucl_distance_coordinates(x1, y1, vec_x2, vec_y2):
    diff_x = vec_x2 - x1
    diff_y = vec_y2 - y1

    distances = np.sqrt(np.square(diff_x) + np.square(diff_y))

    return distances


## Identifies the nearest n points from a given set of points to a single point.
# First, the distances of all points to the single point are calculated.
# Then, the n closest points are filtered, and their indices are returned
# @param[in] x_point x-coordinate of the reference point
# @param[in] y_point y-coordinate of the reference point
# @param[in] array_points Array with the x & y coordinates of the points
# @param[in] formula Distance formula to use ("haversine" or "euclidean")
# @param[in] n Desired number of points
# @return list_indices List of indices of the n nearest points
def get_nearest_points_from_set(x_point, y_point, array_points, formula, n=None):
    # If no selection exists
    if (n is not None) and (n >= len(array_points)):
        # all possible points are returned
        return list(range(0, len(array_points)))

    # Calculate distances
    if formula == "haversine":
        distances = calculate_distance_coordinates_haversine(x1=x_point, y1=y_point, vec_x2=array_points[:, 0],
                                                             vec_y2=array_points[:, 1])
    elif formula == "euclidean":
        distances = calculate_eucl_distance_coordinates(x1=x_point, y1=y_point, vec_x2=array_points[:, 0],
                                                        vec_y2=array_points[:, 1])
    else:
        logging.warning("Distance calculation formula is not implemented.")

    # Index of the n lowest values
    list_indices = np.argpartition(distances, n)[:n]

    return list_indices


## Opens the Readme file
# @param path_scripts The directory path where the README.md file is located. Default: Current working directory.
def show_info(path_scripts: Path = Path.cwd()):
    webbrowser.open(str(path_scripts / "README.md"), new=2)


# ===== Class definition ======
## @class DirectLineNetworkCalculator
# The class contains attributes and calculation methods to do a triangulation for traffic cells and to determine the
# centrality function level (CFL) of the connections
#
# This class provides functionality to:
# - Calculate connections between zones based on their centrality levels
# - Export results in various formats to a PTV Visum model
#
# The calculations consider:
# - Zone hierarchies (centrality levels)
# - Connections to neighbours up to a maximum distance
# - Number of supplier connections
# - Zone filters for origins and destinations
#
# Example usage:
# @code
# calculator = DirectLineNetworkCalculator(visum_instance)
# calculator.calculate_main()
# calculator.export_net()
# @endcode
#
# @see calculate_main() for the primary calculation method
class DirectLineNetworkCalculator:

    ## Constructor
    # @param[in] origin Filename (str) or Visum instance
    # @param[in] attr_cfl Name of the zone attribute that contains the categorization in OZ,MZ,UZ ... Default: TypeNo
    # @param[in] dict_cfl Dictionary containing the attribute values for the respective VFS
    # @param[in] max_distance Specification of the distance up to which neighbors will be connected
    # @param[in] no_suppliers Specification of how many higher-ranking centers a zone should be connected to
    # @param[in] attr_orig Name of the attribute that indicates whether the zone is considered as a origin. Default: None
    # @param[in] attr_dest Name of the attribute that indicates whether the zone is considered as a destination. Default: None
    # @param[in] use_filter Indicates whether only active zones are considered. Can only be used if source = Visum instance
    # @param[in] formula_distance Defines the distance function for determining the supply centers.
    # Note: For triangulation, the air-line connections are determined using the Euclidean distance.
    # Delaunay triangulation only works with a projection of Lat/Lon coordinates.
    # @param[in] path_output Optional possibility to specify a path for file export. Default: None. Then the current folder is used if needed.
    # @param[in] translator Optional Translator instance for multilingual export.
    def __init__(self, source,
                 attr_cfl: str = "TypeNo",
                 dict_cfl: dict = {"cfl_0": 0, "cfl_1": 1, "cfl_2": 2, "cfl_3": 3, "cfl_4": 4, "cfl_5": 5},
                 max_distance=1,
                 no_suppliers=0,
                 attr_orig=None,
                 attr_dest=None,
                 use_filter: bool = False,
                 formula_distance: str = "euclidean",
                 path_output=None,
                 translator: Translator = None):

        ## Translator instance for logging
        if translator is None:
            # Create a default translator if none is provided
            self.translator = Translator()
        else:
            self.translator = translator

        ## Debug mode flag. Enables the execution of intermediate analyses that are not considered in the normal program flow
        self.debug_mode = False

        # Processing the input parameters

        ## Required zone attributes
        self.attr_zones = ["No", "Name", "XCoord", "YCoord"]
        ## Centrality attribute
        self.attr_central_level = attr_cfl
        self.attr_zones.append(self.attr_central_level)
        if attr_orig is not None:
            self.attr_zones.append(attr_orig)
        if attr_dest is not None:
            self.attr_zones.append(attr_dest)

        ## Output directory
        self.path_output = path_output

        ## List of VFS to be processed
        self.cfl = dict_cfl

        ## Dictionary to hold display names for CFLs for export
        self.cfl_labels = dict(zip(self.cfl.keys(), self.cfl.keys()))   # default

        ## Distance calculation
        self.formula_dist = formula_distance

        ## Specification of the neighborhood degree to which equal-ranking connections should be followed
        # (formerly 'exchange function')
        # @type dict
        self.deg_neighbourhood_cfl = None

        ## Specification of how many (higher-ranking) suppliers should be connected
        # @type dict
        self.num_suppliers_cfl = None

        # fill self.deg_neighbourhood
        if isinstance(max_distance, int):
            # Conversion to dict with scalar for each VFS
            self.deg_neighbourhood_cfl = dict(zip(self.cfl.keys(), max_distance * np.ones(len(self.cfl), dtype=int)))
        elif isinstance(max_distance, dict):
            self.deg_neighbourhood_cfl = max_distance
        else:
            raise TypeError("Parameter type not implemented.")

        # fill self.num_suppliers_cfl
        if isinstance(no_suppliers, int):
            # Conversion to dict with scalar for each VFS
            self.num_suppliers_cfl = dict(
                zip(self.cfl.keys(), no_suppliers * np.ones(len(self.cfl), dtype=int)))
        elif isinstance(no_suppliers, dict):
            self.num_suppliers_cfl = no_suppliers
        else:
            raise TypeError("Parameter type not implemented.")

        # Reading the zone data
        # Important: Index of the table = 0...n
        if not isinstance(source, str):
            ## Visum instance
            self.visum = source
            attr_zones = self.attr_zones
            ## Table with zone data
            self.zones = pd.DataFrame(source.Net.Zones.GetMultipleAttributes(attr_zones, OnlyActive=False),
                                      columns=attr_zones)
            raw_active_zones = source.Net.Zones.GetMultiAttValues("No", OnlyActive=use_filter)
            arr_active = np.array(raw_active_zones, dtype=int)
            if arr_active.ndim > 1 and arr_active.shape[1] > 1:
                set_active_zones = set(arr_active[:, 1])
            else:
                set_active_zones = set(arr_active.ravel())
            self.zones["IsActive"] = self.zones["No"].isin(set_active_zones)

            logging.info("%s Zones loaded", len(self.zones))
        else:
            self.visum = None
            logging.warning("Loading zone data failed, input format is not implemented.")

        if attr_orig is None:
            attr_orig = 'origin'
            self.zones[attr_orig] = 1

        if attr_dest is None:
            attr_dest = 'destination'
            self.zones[attr_dest] = 1

        # Handle attr_dest=attr_origin: Delete column duplicate
        if attr_dest == attr_orig:
            self.zones = self.zones.loc[:, ~self.zones.columns.duplicated()]

        ## Source filter attribute
        self.attr_is_from_zone = attr_orig
        ## Destination filter attribute
        self.attr_is_to_zone = attr_dest

        # Init VFS matrices
        # Dict with matrix per VFS: Number of zones x Number of zones
        self.init_results()

        ## Set language of Visum instance
        self.language = self.visum.GetCurrentLanguage()

        # Init dict export
        ## LookupTable Infrastructure: Node number assigned to the zone
        self.dict_export_zone2node = {}  # Contains the number of nodes that are inserted for the zone to be able to insert links
        ## LookUpTable Infrastructure: Mapping internal link number to Visum link number
        self.dict_export_links_vfs = {}  # Contains the links (FromNode-ToNode)
        ## LookUpTable Infrastructure: Mapping connection function level - Visum link type
        self.dict_export_linktypes = {}

        ## DataFrame with the link data of the air-line connections.
        self.edges = pd.DataFrame()

    ## Translates the adjacency matrices of the desired CFL into an edge list
    # @param list_cfl: List of CFL. If not given, all CFL of the instance are used
    # @return df_edges: DataFrame with all edges and their CFL. Attention: Duplicates are not removed
    def adj_matrix_to_links(self, list_cfl=None):

        if list_cfl is None:
            list_cfl = self.cfl.keys()

        list_df_edges = []

        # loop over all CFL
        for cfl in list_cfl:
            if not is_symmetric(self.matrices_cfl[cfl]):
                logging.warning(f'{cfl}: Adjacency matrix is not symmetric.')

            # unstack matrix to get initial edge list
            df_edges = pd.DataFrame(self.matrices_cfl[cfl]).stack().reset_index()
            df_edges.columns = ["FromNodeNo", "ToNodeNo", "TypeNo"]

            # filtering edges of cfl/type
            df_edges = df_edges.loc[df_edges["TypeNo"] == True, :]

            # set attribute cfl
            df_edges.loc[:, "TypeNo"] = cfl

            list_df_edges.append(df_edges)

        df_edges = pd.concat(list_df_edges)

        # only one edge between two zones
        df_edges = df_edges.groupby(["FromNodeNo", "ToNodeNo"]).agg(TypeNo=("TypeNo", min),
                                                                    ListTypeNo=("TypeNo", list)).reset_index()

        df_edges["FromNodeNo"].replace(self.zones["No"], inplace=True)
        df_edges["ToNodeNo"].replace(self.zones["No"], inplace=True)

        return df_edges

    ## Converts the adjacency matrix into a list of connected zones per zone.
    # @param cfl: str, name of the cfl to be considered
    # @param use_zone_names: bool, if true, the zone names are used
    # @return df_set_zones: DataFrame with list object per zone and a column containing the number
    def adj_matrix_to_set_of_connected_zones(self, cfl, use_zone_names=True):
        if use_zone_names:
            col_labels = self.zones["Name"].tolist()
            idx_labels = self.zones["Name"]
        else:
            col_labels = list(range(len(self.zones)))
            idx_labels = self.zones.index

        matrix = self.matrices_cfl[cfl]
        sets = [set(np.array(col_labels)[row > 0]) for row in matrix]

        df_set_zones = pd.DataFrame({"set zones": sets}, index=idx_labels)
        df_set_zones["no zones"] = df_set_zones["set zones"].apply(len)
        return df_set_zones

    ## Calculates which neighbours can be reached within n steps.
    # @param max_steps: maximum distance (steps)
    # @param cfl: cfl to be analysed
    # @return matrix: Adjacency matrix for the reachable neighbours within the max-steps
    def calculate_reachability_max_steps(self, max_steps, cfl):

        # Summiere alle Potenzen von 1 bis max_steps
        matrix = sum(np.linalg.matrix_power(self.matrices_cfl[cfl], k)
                     for k in range(1, max_steps + 1))

        np.fill_diagonal(matrix, 0)

        # Boolean casting for values greater than zero (binarisation).
        # For an adjacency/reachability matrix, it is only relevant whether there is a connection, not how many.
        matrix = (matrix > 0).astype(int)

        return matrix

    ## Calculates the adjacency matrix for each stored cfl of the instance.
    # This is the main calculation method that:
    # 1. Initializes the result matrices
    # 2. Iterates through all cConnectivity function levels (cfl)
    # 3. Calculates the connections for each cfl
    # @return None - Results are stored in internal matrices
    def calculate_main(self):
        # initialize result matrices
        logging.info("Starting calculation for all CFLs.")
        self.init_results()
        logging.info("Adjacency matrices have been initialized.")

        # loop over all cfl
        for cfl in self.cfl:
            # calculate adjacency matrix for cfl
            self.calculate_cfl(cfl)

        logging.info("The calculation for all CFLs is complete.")

    ## Calculates the connections of a Connectivity Function Level (CFL).
    # This method determines all valid connections between zones for a specific CFL by:
    # - Filtering zones based on their centrality/hierarchy
    # - Calculating distances between eligible zones
    # - Applying maximum distance constraints
    # - Considering neighborhood relationships
    # - Determining supplier-customer relationships
    # - Creating adjacency matrices for the connections
    #
    # The calculation process follows these steps:
    # 1. Identify origin and destination zones based on CFL
    # 2. Calculate distances between all potential zone pairs
    # 3. Apply distance thresholds and neighborhood constraints
    # 4. Ensure the required number of supplier connections
    # 5. Generate the final connection matrix
    #
    # @param[in] cfl: The connection function level for which connections are determined.
    #            Higher levels typically represent more important central places.
    # @return None - Results are stored in the internal matrices_cfl dictionary
    # @see calculate_main() for the overall calculation workflow
    # @note The results can be exported using export_matrix() or export_net()
    def calculate_cfl(self, cfl):

        # attribute value of zones for cfl
        value_vfs = self.cfl[cfl]

        # get attributes of cfl
        k_neighbour = self.deg_neighbourhood_cfl[cfl]
        num_suppliers_cfl = self.num_suppliers_cfl[cfl]

        # filter traffic cells/zones based on the following condition:
        # are active zones
        # central level <= value cfl
        active_zones = self.zones
        active_zones = active_zones.loc[(active_zones[self.attr_central_level] <= value_vfs)
                                        & (active_zones["IsActive"] > 0),
                       :]

        # check, if there are zones with identical coordinates (creates wrong result in triangulation)
        # if yes, raise an error
        if len(active_zones) > len(active_zones[["XCoord", "YCoord"]].drop_duplicates()):
            duplicate_zones = active_zones[active_zones.duplicated(subset=["XCoord", "YCoord"], keep=False)]
            duplicate_zones_string = ', '.join(
                duplicate_zones["No"].apply(lambda x: str(int(x))) + "/" + duplicate_zones["Name"])
            error_msg = f'Aborted: Zones with identical coordinates found (NUMBER/NAME): {duplicate_zones_string}'
            raise ValueError(error_msg)
        elif len(active_zones) < 3:
            logging.info(f'{cfl}: Too few zones are active.')
        else:
            logging.info(f'{cfl}: Performing Delaunay triangulation for {len(active_zones)} zones')

            if k_neighbour > 0:

                # Delaunay Triangulation
                tri = Delaunay(active_zones[["XCoord", "YCoord"]])
                zone_orig_idx_triangles = active_zones.index.values[tri.simplices]
                logging.info(f'{cfl}: {len(zone_orig_idx_triangles)} triangles were created')

                # fill adjacency matrix:
                # loop over all triangles
                for p1, p2, p3 in zone_orig_idx_triangles:
                    # p1, p2, p3 are points of current triangle
                    # add the following adjacencies/edges
                    # p1 - p2, p2 - p1, p1 - p3, p3 - p1, p3 - p2, p2 - p3
                    self.matrices_cfl[cfl][p1, p2] = 1
                    self.matrices_cfl[cfl][p1, p3] = 1
                    self.matrices_cfl[cfl][p2, p1] = 1
                    self.matrices_cfl[cfl][p2, p3] = 1
                    self.matrices_cfl[cfl][p3, p1] = 1
                    self.matrices_cfl[cfl][p3, p2] = 1

            # if more than the neighbours of degree 1 are considered:
            if k_neighbour > 1:
                logging.info(f'{cfl}: the neighborhood degree must be calculated')
                # get adjacency matrx based on the reachability matrix in k steps
                adj_k_steps = self.calculate_reachability_max_steps(k_neighbour, cfl)
                # set adjacency matrix of cfl to reachability matrix
                self.matrices_cfl[cfl] = adj_k_steps

            # connections with supply function
            if num_suppliers_cfl > 0:
                # get list of connected traffic cells for each cell
                df_list_zones = self.adj_matrix_to_set_of_connected_zones(cfl, use_zone_names=False)

                # dataframe with possible supply centres
                provider = self.zones.loc[(self.zones[self.attr_central_level] < self.cfl[cfl])
                                          & (self.zones[self.attr_is_from_zone] > 0), :]
                # set of all possible supply centers
                set_names_provider = set(provider.index)

                # Determine for each active traffic cell whether it is already connected to enough supply centres
                df_list_zones = df_list_zones.loc[df_list_zones.index.isin(
                    active_zones.loc[active_zones[self.attr_is_from_zone] > 0, :].index), :]
                df_list_zones["no_provider"] = df_list_zones["set zones"].apply(set_names_provider.intersection).apply(
                    len)
                df_list_zones["provider"] = (df_list_zones.index.isin(set_names_provider)) \
                                            | (df_list_zones["no_provider"] >= num_suppliers_cfl)

                # For all traffic cell that do not fulfil the condition: Connect the nearest k supply centres
                for zone in df_list_zones.index[df_list_zones["provider"] < True]:
                    zone_data = self.zones.loc[zone, :]

                    # if already connected to a supply centre -> delete the supply centre from the set
                    tmp_set_provider = set_names_provider - df_list_zones.loc[zone, "set zones"]
                    provider_tmp = provider.loc[list(tmp_set_provider), :]

                    # Determine the missing number of supply centres
                    # Selection criterion: nearest
                    list_idx_provider = get_nearest_points_from_set(x_point=zone_data.loc["XCoord"],
                                                                    y_point=zone_data.loc["YCoord"],
                                                                    n=num_suppliers_cfl - df_list_zones.loc[
                                                                        zone, "no_provider"],
                                                                    array_points=provider_tmp[
                                                                        ["XCoord", "YCoord"]].values,
                                                                    formula=self.formula_dist)
                    self.matrices_cfl[cfl][zone, provider_tmp.index[list_idx_provider]] = 1
                    self.matrices_cfl[cfl][provider_tmp.index[list_idx_provider], zone] = 1

            # debugbefehl distances
            # distances = calculate_distance_coordinates(x1=zone_data.loc["XCoord"], y1=zone_data.loc["YCoord"],
            #                                            vec_x2=provider_tmp.loc[:, "XCoord"].values,
            #                                            vec_y2=provider_tmp.loc[:, "YCoord"].values)

            # inactive origin or destination

            # Structure mask with active and inactive OD pairs
            # origin and target must be active and the transposed matrix thereof (symmetry)
            # Logic: Filter OD pairs with origin & target active...
            #
            # origin * target = matrix
            # (1 0).T * (1 1) = (1 1
            # 0 0)
            #
            # and symmetrize this
            # (1 1
            # 1 0)

            # origin and destination
            vector_is_from_zone = self.zones[self.attr_is_from_zone].values
            vector_is_to_zone = self.zones[self.attr_is_to_zone].values
            # Link via dyadic product ("outer product")
            # Place logic as a mask over existing matrix
            idx_active = np.outer(vector_is_from_zone, vector_is_to_zone).astype(bool)
            # Symmetrize the matrix (Bool OR operation with transposed matrix)
            # Where OD relation, there DO relation
            idx_active_symm = idx_active + idx_active.T

            # Adjacency matrix is elementwise-multiplied by mask to contain the values of the active pairs
            self.matrices_cfl[cfl] = self.matrices_cfl[cfl] * idx_active_symm.astype(int)

            # check for symmetry
            if np.sum(self.matrices_cfl[cfl] - self.matrices_cfl[cfl].T) > 0:
                raise ValueError("Error: Matrix is not symmetric")

            # debug
            if self.debug_mode:
                # shows which cells a traffic cell is connected to (= neighbouring centres)
                list_zones = self.adj_matrix_to_set_of_connected_zones(cfl)

                # export resulting infrastructure of triangular network to visum
                self.export_net(links_additive=False, list_cfl=[cfl])

                logging.info(f'{cfl}: : The result can be viewed in Visum.')

            logging.info(f'The calculation for {cfl} is completed.')

            df_zones_info = self.adj_matrix_to_set_of_connected_zones(cfl)
            df_zones_info["set zones"] = df_zones_info["set zones"].str.join(",")
            # logging.info('\t' + df_zones_info.to_string().replace('\n', '\n\t'))

    ## Deletes nodes in Visum that do not connect any links.
    # All nodes without links are filtered & the active nodes are deleted.
    # The filter is then reset.
    # @return No return. The visum instance is changed.
    def delete_unused_nodes(self):
        if self.visum is None:
            logging.warning("Delete nodes: No Visum instance is linked.")
            return

        # filter isolated nodes in Visum model
        self.visum.Filters.NodeFilter().Init()
        self.visum.Filters.NodeFilter().AddCondition("OP_NONE", False, "Count:InLinks", "EqualVal", 0)
        self.visum.Filters.NodeFilter().AddCondition("OP_AND", False, "Count:OutLinks", "EqualVal", 0)
        self.visum.Filters.NodeFilter().UseFilter = True

        n = self.visum.Net.Nodes.CountActive

        # delete
        self.visum.Net.Nodes.RemoveAll(OnlyActive=True)

        # reset filter
        self.visum.Filters.NodeFilter().Init()

        logging.info(f'{n} isolated nodes were deleted.')

    ## Exports the desired adjacency matrices either directly to Visum (if Visum instance is linked)
    # or as .mtx file.
    # Existing matrices are overwritten.
    # @param visum: optional visum instance. Default None
    # @param list_cfl: optional set of CFL. Default: None (all of the object)
    def export_matrix(self, list_cfl=None):
        # If visum instance recognised:
        # create & export data directly in visa (for networks with <1500 districts via SetValues otherwise using an mtx file in O-Fromat)
        # Otherwise: Save .mtx file

        if list_cfl is None:
            list_cfl = self.cfl.keys()

        logging.info(f'Starting the export of {len(list_cfl)} matrices.')

        for cfl in list_cfl:
            if cfl not in self.matrices_cfl.keys():
                logging.warning(f'Error: {cfl}  is not in the list of calculated CFLs.')
                continue

            matrix_cfl = self.matrices_cfl[cfl]

            # Get the user-facing, translated label for the matrix name
            cfl_label = self.cfl_labels.get(cfl, cfl)  # Fallback to the key if no label is found

            # name of matrix/file
            if self.num_suppliers_cfl[cfl] < 1:
                # no term regarding supply function
                name_matrix = f"RIN_{cfl_label}_n={self.deg_neighbourhood_cfl[cfl]}"
            else:
                # includes term regarding supply function
                name_matrix = f"RIN_{cfl_label}_n={self.deg_neighbourhood_cfl[cfl]}_v={self.num_suppliers_cfl[cfl]}"

            # get output directory (not used if matrix is written to Visum directly)
            path_mat = self.path_output or Path.cwd() / 'mtx'
            path_mat.mkdir(parents=True, exist_ok=True)
            path_mat_file = path_mat / f"{name_matrix}.mtx"

            # Create the matrix in Visum, if necessary

            # Save matrix to .mtx file in O-Format
            df_mat = pd.DataFrame(matrix_cfl,
                                  columns=self.zones["No"].values.astype(int),
                                  index=self.zones["No"].values.astype(int),
                                  dtype=int
                                  ).stack().reset_index()

            df_mat.columns = ['origin', 'destination', 'value']

            # Das O-Format kommt ohne 0 Werte aus, bereite einen entsprechenden DataFrame vor
            df_mat_light = df_mat.loc[df_mat['value'] != 0]

            with open(path_mat_file, "w", newline='\n') as f:
                str_header = '''$O
* Universität Stuttgart
*
* Verbindungsfunktionsstufe 5
*
* symmetrische Matrix
*
* Parameter
* Nachbarschaftsniveau Z-Z:
* Nachbarschaftsniveau Z-Z+:
*
* Zeitbereich
0 24
*
* Faktor
*
1.0
*
* VonBezirk NachBezirk Matrixwert
'''

                f.write(str_header)
                df_mat_light.to_csv(f, header=False, sep=" ", index=False)
                logging.info(f'Matrix {name_matrix} saved to file: {path_mat_file}')

            # If an instance exists, import content into Visum
            if self.visum is not None:

                # chack if matrix has to be created
                if self.visum.Net.Matrices.Count < 1:
                    # create matrix
                    matrix_instance = self.visum.Net.AddMatrix(-1, 2, 3)
                    matrix_instance.SetAttValue("CODE", name_matrix)
                    matrix_instance.SetAttValue("NAME", name_matrix)
                else:
                    # search existing matrix with same name
                    matrix_instances = self.visum.Net.Matrices.ItemsByRef(f'''Matrix([CODE]= "{name_matrix}") ''')
                    if matrix_instances.Count < 1:
                        del matrix_instances
                        # create matrix
                        matrix_instance = self.visum.Net.AddMatrix(-1, 2, 3)
                        matrix_instance.SetAttValue("CODE", name_matrix)
                        matrix_instance.SetAttValue("NAME", name_matrix)
                    elif matrix_instances.Count > 1:
                        logging.warning("Matrix code exists multiple times, the first matrix will be overwritten.")
                        matrix_instance = matrix_instances.Iterator.Item
                    else:
                        matrix_instance = matrix_instances.Iterator.Item
                        logging.info("Matrix code exists, content will be overwritten.")

                # set values
                # If there are fewer than 1500 zones, you can work with SetValues without any problems.
                # Otherwise an mtx file must be written
                if self.visum.Net.Zones.Count < 1500:
                    matrix_instance.SetValues(matrix_cfl)
                    logging.info(f'{name_matrix}: was read into Visum.')
                else:
                    matrix_instance.Open(path_mat_file, ReadAdditive=False)
                    logging.info(f'{name_matrix}: was read into Visum.')

            else:
                logging.info(f'Visum is not running. Matrices were exported as files to: {path_mat}')

    ## Creates the infrastructure objects in preparation for exporting the infrastructure in the form of dictionaries for nodes, lines, and line types.
    # Called if an object is not present in the dictionaries during export.
    # Prevents the multiple creation of lines and nodes.
    #  @return No return. The results are stored internally.
    def extract_net(self):

        # Create a node list
        df_nodes = self.zones.copy()
        df_nodes = df_nodes.astype({'No': int, self.attr_central_level: int})

        # Create an assignment zones -> nodes
        if self.visum is None:
            no_node_start = 1
            no_link_start = 1
            no_linktype_start = 1
        else:
            no_link_max = int(self.visum.Net.AttValue(r"Max:Links\No") or 0)
            no_linktype_max = int(self.visum.Net.AttValue(r"Max:LinkTypes\No") or 0)
            no_node_max = int(self.visum.Net.AttValue(r"Max:Nodes\No") or 0)

            no_node_start = no_node_max + 1
            no_link_start = no_link_max + 1
            no_linktype_start = no_linktype_max + 1

        # Assignment of the old numbering to the new
        # dict_no_nodes can be used to convince connections, as it links the old numbers (of zones) with the new numbers (nodes)
        # dict[]
        self.dict_export_zone2node = dict(
            zip(df_nodes["No"].astype(int).drop_duplicates(), range(no_node_start, no_node_start + len(df_nodes) + 1)))

        # add link type to dict dict[Name]=Number
        self.dict_export_linktypes = dict(
            zip(self.cfl.keys(), range(no_linktype_start, no_linktype_start + len(self.cfl.keys()) + 1)))

        # adjacency matrix to link table
        df_edges = self.adj_matrix_to_links()

        # replace cfl with typeno
        df_edges["TypeNo"].replace(self.dict_export_linktypes, inplace=True)

        # translate id to zone number
        df_edges["FromNodeNo"].replace(self.dict_export_zone2node, inplace=True)
        df_edges["ToNodeNo"].replace(self.dict_export_zone2node, inplace=True)

        # Add a number
        # 1. Identification of the outward & return direction
        df_edges["No"] = df_edges[["FromNodeNo", "ToNodeNo"]].min(axis=1).astype(str) + "_" + df_edges[
            ["FromNodeNo", "ToNodeNo"]].max(axis=1).astype(str)

        # 2. numbering
        self.dict_export_links_vfs = dict(
            zip(df_edges["No"].drop_duplicates(), range(no_link_start, no_link_start + int(len(df_edges) / 2) + 1)))

        # Test: there is a number for each link
        if len(self.dict_export_links_vfs) != len(df_edges["No"].drop_duplicates()):
            logging.error("Link numbering does not match the number of links.")

        df_edges.loc[:, "Name"] = df_edges["No"]
        df_edges["No"].replace(self.dict_export_links_vfs, inplace=True)

        self.edges = df_edges

    ## Exports a net file
    # if a visum instance exists, the net file is loaded in Visum
    # @param links_additive: if False, the existing routes in Visum are deleted
    # @param list_cfl: List of CFLs that are to be taken into account. Default: All of the object
    # @param create_connectors: if True, connectors are created for each CFL. Default: False
    # @return: None
    def export_net(self, links_additive=True, list_cfl=None, create_connectors=True):
        if self.path_output is None:
            # if no file path is passed: Use visum file path if instance exists, otherwise use the current path
            if self.visum is not None:
                path_net = Path(self.visum.GetPath(1))
            else:
                path_net = Path.cwd()
        else:
            path_net = self.path_output

        if list_cfl is None:
            list_cfl = self.cfl.keys()

        list_labels = [self.cfl_labels[key] for key in list_cfl]
        path_net = path_net / f"{self.translator.translate('name_tool_short')}_{'_'.join(list_labels)}.net"

        # Check: Extract_net notwendig?
        # Erstelle Streckenliste
        df_edges = self.adj_matrix_to_links(list_cfl)
        set_zones = set(df_edges['FromNodeNo']).union(set(df_edges['ToNodeNo']))

        if (len(set_zones - set(self.dict_export_zone2node.keys())) > 0) | (len(df_edges) > len(self.edges)):
            self.extract_net()

        if len(self.edges) < 1:
            logging.info("No links to export, aborting.")
            return

        df_nodes = self.zones.copy()

        if "TypeNo" not in df_nodes.columns.tolist():
            df_nodes["TypeNo"] = df_nodes[self.attr_central_level]

        # Überarbeiten
        df_nodes = df_nodes.astype({'No': int, 'TypeNo': int})
        df_nodes.loc[:, 'Name'] = self.translator.translate("name_tool_short") + df_nodes['No'].astype(int).astype(str) + ' ' + df_nodes['Name']
        df_nodes["CODE"] = df_nodes["No"].astype(int)
        df_nodes["No"].replace(self.dict_export_zone2node, inplace=True)
        df_nodes = df_nodes[['No', 'Name', 'XCoord', 'YCoord', 'TypeNo', 'CODE']]

        df_edges = self.edges.loc[self.edges["ListTypeNo"].apply(lambda x: bool(set(x).intersection(list_cfl))), :]

        list_tsys_net = pd.DataFrame(self.visum.Net.TSystems.GetMultipleAttributes(["Code"])).squeeze().values.tolist()
        df_linktypes = pd.DataFrame.from_dict(self.dict_export_linktypes, orient="index").reset_index()
        df_linktypes.columns = ["Name", "No"]
        df_linktypes["TSysSet"] = ",".join(list_tsys_net)
        df_linktypes["Rank"] = df_linktypes["No"]

        if create_connectors:
            # create dataframe with connectors
            df_conn = pd.DataFrame(list(self.dict_export_zone2node.items()), columns=["ZONENO", "NODENO"])
            # Duplicate rows for Directions O/D
            df_conn = pd.concat([df_conn] * 2, ignore_index=True)
            # Sort the DataFrame so
            df_conn.sort_values(by=["ZONENO", "NODENO"], inplace=True)
            # Reset index
            df_conn.reset_index(drop=True, inplace=True)
            # Add DIRECTION column
            df_conn["DIRECTION"] = ["O", "D"] * (len(df_conn) // 2)
            # Add TSYSSET for IV-Sys
            tsys_net = pd.DataFrame(self.visum.Net.TSystems.GetMultipleAttributes(["CODE", "TYPE"]),
                                    columns=["CODE", "TYPE"])
            list_ivtsys_net = tsys_net[tsys_net['TYPE'] != 'PUT']["CODE"].to_list()
            df_conn["TSYSSET"] = ",".join(list_ivtsys_net)

        # Write .net file
        with open(path_net, mode="w", newline="\n", encoding="latin-1") as f:
            header = '''$VISION
* Universität Stuttgart Fakultät 2 Bau+Umweltingenieurwissenschaften Stuttgart
* 08/23/22
* Table: Version block
$VERSION:VERSNR;FILETYPE;LANGUAGE;UNIT
13;Net;ENG;KM

'''
            f.write(header)
            write_object_to_net("Node", df_nodes, f)
            write_object_to_net("Link type", df_linktypes, f)
            write_object_to_net("Link", df_edges[["No", "FromNodeNo", "ToNodeNo", "TypeNo", "Name"]], f)
            if create_connectors:
                # Write table: Connectors to the net file
                write_object_to_net("Connector", df_conn, f)

        # If Visum instance is provided: load the .net file
        if self.visum is not None:
            # Conflict management
            # controller = self.visum.IO.CreateAddNetReadController()

            if links_additive is not True:
                self.visum.Net.Links.RemoveAll(OnlyActive=True)

            self.visum.IO.LoadNet(path_net, ReadAdditive=True)

            if self.visum.Net.Links.Count < len(df_edges):
                logging.warning("Error importing the network file.")

        logging.info(f'The network file of {len(list_cfl)} CFLs has been exported to Visum.')

    ## Exports the connections and the number of connections as zone UDAs to Visum
    #  @param cfl The cfl (connection function level) for which the connections should be exported
    #  @return No return value. The Visum instance is modified.
    def export_zones_uda_connections(self, cfl):
        # Create UDA if not exists

        str_no_conn = f'RIN_n_{self.translator.translate("connections")}_{self.cfl_labels[cfl]}'.replace(" ", "")
        str_conn = f'RIN_connections_{self.cfl_labels[cfl]}'.replace(" ", "")

        try:
            self.visum.Net.Zones.AddUserDefinedAttribute(str_no_conn,
                                                         str_no_conn,
                                                         str_no_conn, 5)
            self.visum.Net.Zones.AddUserDefinedAttribute(str_conn,
                                                         str_conn,
                                                         str_conn, 5)
        except:
            pass

        # Load connections
        df_zones = self.adj_matrix_to_set_of_connected_zones(cfl).reset_index()
        df_zones["No"] = df_zones.Name.replace(self.zones.set_index("Name")["No"].astype(int).to_dict())
        df_zones.set_index("No", inplace=True)

        # Write the result to Visum
        df_format = pd.DataFrame(self.visum.Net.Zones.GetMultiAttValues("No"), columns=["Idx", "No"]).set_index("No")

        df_format = df_format.join(df_zones)
        df_format[str_conn] = df_format["set zones"].str.join(",")

        self.visum.Net.Zones.SetMultiAttValues(str_no_conn, df_format.loc[:, ["Idx", "no zones"]].values)
        self.visum.Net.Zones.SetMultiAttValues(str_conn, df_format.loc[:, ["Idx", str_conn]].values)

    ## Initializes the adjacency matrices
    #  @return No return value. The results are stored internally.
    def init_results(self):
        dict_cfl = {}
        for cfl in self.cfl:
            dict_cfl[cfl] = np.zeros([len(self.zones), len(self.zones)], dtype=bool)

        ## Dictionary with the resulting adjacency matrices of the connection function levels
        self.matrices_cfl = dict_cfl

    ## Filters the links of the inserted link types in Visum.
    #  @return No return value. The Visum instance is modified.
    def filter_links_cfl(self):
        filter = self.visum.Filters.LinkFilter()
        filter.Init()
        filter.AddCondition("OP_NONE", False, "TypeNo", "ContainedIn",
                            ",".join(str(x) for x in self.dict_export_linktypes.values()))
        filter.UseFilter = True

    ## Filters the zones for which the given attribute is greater than 0.
    #  @param filterFromZones Boolean flag to determine whether to filter origin zones (True) or destination zones (False). Default: True
    #  @return No return value. The Visum instance is modified.
    def filter_zones_origin_destination(self, filterFromZones: bool = True):
        filter = self.visum.Filters.ZoneFilter()
        filter.Init()
        if filterFromZones:
            if self.attr_is_from_zone is not None:
                filter.AddCondition("OP_NONE", False, self.attr_is_from_zone, "GreaterVal", 0)
        else:
            if self.attr_is_to_zone is not None:
                filter.AddCondition("OP_NONE", False, self.attr_is_to_zone, "GreaterVal", 0)

        filter.UseFilter = True

    ## Deletes the cfl links inserted in Visum.
    #  @return No return value. The Visum instance is modified.
    def delete_added_links(self):
        # Attention: Does NOT delete link types
        self.filter_links_cfl()
        self.visum.Net.Links.RemoveAll(OnlyActive=True)
        self.visum.Filters.LinkFilter().Init()

    ## Updates cfl labels for neat export names
    #  @return No return value. self.cfl_labels is modified
    def update_label_cfl(self):
        # implemented for keys 'cfl_x_optional_text'
        # if other keys are used -> return key as backup value
        self.cfl_labels = {}
        for key in self.cfl:
            if '_' in key:
                parts = key.split('_')
                self.cfl_labels[key] = f"{self.translator.translate(parts[0])} {'_'.join(parts[1:])}"
            else:
                self.cfl_labels[key] = key
