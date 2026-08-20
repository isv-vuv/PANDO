# -*- coding: utf-8 -*-
"""Portable raster neighborhood maximum for Model 5."""

import os

import numpy as np
from osgeo import gdal
from qgis.core import (
    QgsProcessingAlgorithm,
    QgsProcessingException,
    QgsProcessingParameterNumber,
    QgsProcessingParameterRasterDestination,
    QgsProcessingParameterRasterLayer,
)


def neighborhood_maximum(values, size, nodata=None, progress=None):
    """Calculate a square moving-window maximum without optional GIS providers."""

    values = np.asarray(values, dtype=np.float64)
    valid = np.isfinite(values)
    if nodata is not None:
        valid &= values != nodata
    source = np.where(valid, values, -np.inf)
    radius = size // 2
    padded = np.pad(source, radius, mode="constant", constant_values=-np.inf)
    maximum = np.full(source.shape, -np.inf, dtype=np.float64)
    total = size * size
    completed = 0
    for row_offset in range(size):
        for column_offset in range(size):
            view = padded[
                row_offset : row_offset + source.shape[0],
                column_offset : column_offset + source.shape[1],
            ]
            np.maximum(maximum, view, out=maximum)
            completed += 1
            if progress:
                progress(completed / total * 90)
    return maximum


class RasterNeighborhoodMaximumAlgorithm(QgsProcessingAlgorithm):
    INPUT = "input"
    SIZE = "size"
    OUTPUT = "output"

    def name(self):
        return "raster_neighborhood_max"

    def displayName(self):
        return "Raster neighborhood maximum (portable)"

    def group(self):
        return "PANDO"

    def groupId(self):
        return "pando"

    def createInstance(self):
        return RasterNeighborhoodMaximumAlgorithm()

    def initAlgorithm(self, config=None):
        self.addParameter(QgsProcessingParameterRasterLayer(self.INPUT, "Input raster"))
        self.addParameter(
            QgsProcessingParameterNumber(
                self.SIZE,
                "Square neighborhood size",
                type=QgsProcessingParameterNumber.Integer,
                defaultValue=3,
                minValue=1,
            )
        )
        self.addParameter(QgsProcessingParameterRasterDestination(self.OUTPUT, "Maximum raster"))

    def processAlgorithm(self, parameters, context, feedback):
        layer = self.parameterAsRasterLayer(parameters, self.INPUT, context)
        size = self.parameterAsInt(parameters, self.SIZE, context)
        output_path = self.parameterAsOutputLayer(parameters, self.OUTPUT, context)
        if layer is None:
            raise QgsProcessingException("No valid input raster was provided.")
        if size < 1 or size % 2 == 0:
            raise QgsProcessingException("Neighborhood size must be a positive odd number.")

        dataset = gdal.Open(layer.source(), gdal.GA_ReadOnly)
        if dataset is None:
            raise QgsProcessingException(f"Could not open input raster: {layer.source()}")
        band = dataset.GetRasterBand(1)
        values = band.ReadAsArray()
        if values is None:
            raise QgsProcessingException("Could not read input raster band 1.")

        nodata = band.GetNoDataValue()
        def report_progress(value):
            if feedback.isCanceled():
                raise QgsProcessingException("Raster neighborhood calculation was canceled.")
            feedback.setProgress(value)

        maximum = neighborhood_maximum(
            values,
            size,
            nodata,
            progress=report_progress,
        )

        output_nodata = nodata if nodata is not None else -9999.0
        maximum[~np.isfinite(maximum)] = output_nodata
        os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
        driver = gdal.GetDriverByName("GTiff")
        output = driver.Create(
            output_path,
            dataset.RasterXSize,
            dataset.RasterYSize,
            1,
            gdal.GDT_Float32,
            options=["COMPRESS=DEFLATE", "TILED=YES"],
        )
        if output is None:
            raise QgsProcessingException(f"Could not create output raster: {output_path}")
        output.SetGeoTransform(dataset.GetGeoTransform())
        output.SetProjection(dataset.GetProjection())
        output_band = output.GetRasterBand(1)
        output_band.SetNoDataValue(float(output_nodata))
        output_band.WriteArray(maximum.astype(np.float32))
        output_band.FlushCache()
        output.FlushCache()
        output = None
        dataset = None
        feedback.setProgress(100)
        return {self.OUTPUT: output_path}
