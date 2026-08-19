# -*- coding: utf-8 -*-

from qgis.PyQt.QtCore import QCoreApplication
from qgis.core import (QgsProcessing,
                      QgsProcessingAlgorithm,
                      QgsProcessingParameterVectorLayer,
                      QgsProcessingParameterField,
                      QgsProcessingParameterFileDestination,
                      QgsProcessingParameterBoolean,
                      QgsProcessingParameterEnum,
                      QgsProcessingParameterString,
                      QgsCoordinateReferenceSystem,
                      QgsCoordinateTransform,
                      QgsProject,
                      QgsWkbTypes)
import os
import re

class PolyExportAlgorithm(QgsProcessingAlgorithm):
    """
    Processing algorithm to generate poly files from polygon features.
    Adapted from the OSM Poly Export plugin for direct use in QGIS models.
    """
    
    # Parameter identifiers
    INPUT = 'INPUT'
    OUTPUT_DIR = 'OUTPUT_DIR'
    USE_SELECTED = 'USE_SELECTED'
    NAMING_METHOD = 'NAMING_METHOD'
    FIELD_FOR_NAMES = 'FIELD_FOR_NAMES'
    CUSTOM_NAME_TEMPLATE = 'CUSTOM_NAME_TEMPLATE'
    
    def tr(self, string):
        return QCoreApplication.translate('Processing', string)
        
    def createInstance(self):
        return PolyExportAlgorithm()
        
    def name(self):
        return "osmpoly_generator"
        
    def displayName(self):
        return self.tr('Generate OSM Poly Files')
        
    def group(self):
        return 'PANDO'
        
    def groupId(self):
        return 'pando'
        
    def shortHelpString(self):
        return self.tr('''Exports polygon features to .poly files for OSM tools.
        
This algorithm converts polygons to .poly files for Osmosis and other OSM tools.

Three naming methods are available:
- Method A: Layer name + feature ID (example: Districts_fid_1.poly)
- Method B: Field value (uses a field from attribute table)
- Method C: Custom template (with variables)

All .poly files will be in WGS84 (EPSG:4326) coordinates as required by OSM tools.''')
    
    def initAlgorithm(self, config=None):
        # Input & Output parameters
        self.addParameter(
            QgsProcessingParameterVectorLayer(
                self.INPUT,
                self.tr('Input polygon layer'),
                [QgsProcessing.TypeVectorPolygon]
            )
        )
        
        self.addParameter(
            QgsProcessingParameterFileDestination(
                self.OUTPUT_DIR,
                self.tr('Output directory'),
                fileFilter='Directory',
                optional=False
            )
        )
        
        # Feature Selection parameters
        self.addParameter(
            QgsProcessingParameterBoolean(
                self.USE_SELECTED,
                self.tr('Use selected features only'),
                defaultValue=False
            )
        )
        
        # Naming method parameters
        self.addParameter(
            QgsProcessingParameterEnum(
                self.NAMING_METHOD,
                self.tr('<b>Filename method</b>'),
                options=[
                    'Method A: Layer name + ID',
                    'Method B: Field value',
                    'Method C: Custom template'
                ],
                defaultValue=0
            )
        )
        
        # Method B options
        self.addParameter(
            QgsProcessingParameterField(
                self.FIELD_FOR_NAMES,
                self.tr('└─ Method B: Select attribute field for filenames'),
                None,
                self.INPUT,
                optional=True
            )
        )
        
        # Method C options
        self.addParameter(
            QgsProcessingParameterString(
                self.CUSTOM_NAME_TEMPLATE,
                self.tr('└─ Method C: Enter custom template') + 
                '\n' + self.tr('Available variables: {id}, {layername}, {FIELD_NAME}') +
                '\n' + self.tr('Examples: region_{NAME}, {id}_{layername}'),
                optional=True,
                defaultValue=''
            )
        )

    def checkParameterValues(self, parameters, context):
        """Validates parameter selection before execution to prevent runtime UI errors."""
        naming_method = self.parameterAsEnum(parameters, self.NAMING_METHOD, context)
        field_name = self.parameterAsString(parameters, self.FIELD_FOR_NAMES, context)
        custom_template = self.parameterAsString(parameters, self.CUSTOM_NAME_TEMPLATE, context)

        if naming_method == 1 and not field_name:
            return (False, self.tr("Error: Method B (Field value) requires selecting an attribute field for filenames."))

        if naming_method == 2 and not custom_template.strip():
            return (False, self.tr("Error: Method C (Custom template) requires entering a custom template string."))

        return super().checkParameterValues(parameters, context)
        
    def processAlgorithm(self, parameters, context, feedback):
        # Get parameters
        layer = self.parameterAsVectorLayer(parameters, self.INPUT, context)
        use_selected = self.parameterAsBool(parameters, self.USE_SELECTED, context)
        naming_method = self.parameterAsEnum(parameters, self.NAMING_METHOD, context)
        field_name = self.parameterAsString(parameters, self.FIELD_FOR_NAMES, context)
        custom_template = self.parameterAsString(parameters, self.CUSTOM_NAME_TEMPLATE, context)
        output_dir = self.parameterAsString(parameters, self.OUTPUT_DIR, context)
        
        # Fix for temporary output directory or file path parameter
        if output_dir.endswith('.file') or output_dir.endswith('.dir'):
            output_dir = output_dir.rsplit('.', 1)[0]
            
        # Ensure target directory exists (if output_dir is a file path, extract parent directory)
        target_dir = output_dir if os.path.isdir(output_dir) or not os.path.splitext(output_dir)[1] else os.path.dirname(output_dir)
        if target_dir:
            os.makedirs(target_dir, exist_ok=True)
            
        # Check if layer is valid
        if not layer:
            feedback.reportError(self.tr('No valid layer selected'))
            return {}
            
        if layer.geometryType() != QgsWkbTypes.PolygonGeometry:
            feedback.reportError(self.tr('Layer is not a polygon layer'))
            return {}
        
        # Get features to process    
        if use_selected and layer.selectedFeatureCount() > 0:
            feedback.pushInfo(f'Generating poly files from {layer.selectedFeatureCount()} selected features')
            features = list(layer.selectedFeatures())
        else:
            feedback.pushInfo(f'Generating poly files from all {layer.featureCount()} features')
            features = list(layer.getFeatures())
            
        if len(features) == 0:
            feedback.reportError(self.tr('No features to process'))
            return {}
        
        # Setup coordinate transform to EPSG:4326 (WGS84)
        source_crs = layer.crs()
        target_crs = QgsCoordinateReferenceSystem('EPSG:4326')
        transform = QgsCoordinateTransform(source_crs, target_crs, QgsProject.instance())
        
        # Add a specific OSM Poly tip
        feedback.pushInfo('Remember: Coordinate order in .poly files is longitude, latitude (X,Y)')
        
        # Process features
        total = len(features)
        success_count = 0
        for current, feature in enumerate(features):
            if feedback.isCanceled():
                break
                
            feedback.setProgress(int(current * 100 / total))
            
            # Generate filename based on selected naming method
            if naming_method == 0:  # Method A: Layer name + feature ID
                poly_filename = f'{layer.name().replace(" ","")}_fid_{str(feature.id())}'
            elif naming_method == 1:  # Method B: Field value
                if not field_name:
                    feedback.reportError(self.tr('Method B requires selecting a field. Please choose a field from the dropdown.'))
                    return {}
                
                poly_filename = str(feature[field_name])
                if not poly_filename or poly_filename == 'NULL':
                    poly_filename = f"feature_{str(feature.id())}"
                    feedback.pushInfo(f'Empty field value for feature ID {feature.id()}, using {poly_filename} instead')
            elif naming_method == 2:  # Method C: Custom template
                if not custom_template:
                    feedback.reportError(self.tr('Custom template is empty'))
                    return {}
                    
                # Start with the template
                poly_filename = custom_template
                
                # Replace {id} with feature ID
                poly_filename = poly_filename.replace('{id}', str(feature.id()))
                
                # Replace {layername} with layer name (spaces removed)
                poly_filename = poly_filename.replace('{layername}', layer.name().replace(" ",""))
                
                # Replace field values like {NAME}
                for field in feature.fields():
                    field_name_placeholder = '{' + field.name() + '}'
                    if field_name_placeholder in poly_filename:
                        field_value = str(feature[field.name()])
                        if field_value and field_value != 'NULL':
                            poly_filename = poly_filename.replace(field_name_placeholder, field_value)
                        else:
                            poly_filename = poly_filename.replace(field_name_placeholder, '')
            
            # Sanitize filename for operating system compatibility
            poly_filename = re.sub(r'[\\/*?:"<>|]', '_', poly_filename)

            # Get geometry and transform to WGS84
            geom = feature.geometry()
            geom.transform(transform)
            
            # Flatten geometry type to ignore Z/M dimensions during validation
            flat_type = QgsWkbTypes.flatType(geom.wkbType())
            
            # Extract polygons
            if flat_type == QgsWkbTypes.Polygon:
                polygons = [geom.asPolygon()]
            elif flat_type == QgsWkbTypes.MultiPolygon:
                polygons = geom.asMultiPolygon()
            else:
                feedback.reportError(f'Invalid geometry type ({geom.wkbType()}) for feature ID {feature.id()}')
                continue
                
            # Write the poly file
            if output_dir.lower().endswith('.poly'):
                file_path = output_dir
            else:
                file_path = os.path.join(output_dir, f'{poly_filename}.poly')
            
            # Log the file being written
            feedback.pushInfo(f'Writing {file_path}')
            
            try:
                # FORCE UNIX LINE ENDINGS: 'newline="\n"' verhindert den Windows-Bug für Osmium
                with open(file_path, 'w', encoding='utf-8', newline='\n') as f:
                    f.write(f"{poly_filename}\n")
                    
                    # i represents the part ID of the respective polygon piece
                    for i, polygon in enumerate(polygons, 1):
                        for j, ring in enumerate(polygon, 1):
                            prefix = "!" if j > 1 else ""
                            # Outer boundary and corresponding inner holes share the exact same ID
                            f.write(f"{prefix}{i}\n")
                            
                            for vertex in ring:
                                # Strict Osmium/Osmosis standard: 3 spaces indentation, fixed 6 decimal places
                                f.write(f"   {vertex[0]:.6f}   {vertex[1]:.6f}\n")
                            
                            f.write("END\n")
                    
                    f.write("END\n")
                success_count += 1
            except Exception as e:
                feedback.reportError(f'Error writing file {file_path}: {str(e)}')
                continue
                
        feedback.pushInfo(f'Successfully exported {success_count} poly files to {output_dir}')
        return {self.OUTPUT_DIR: output_dir}