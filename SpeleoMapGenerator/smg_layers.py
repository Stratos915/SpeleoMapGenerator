# -*- coding: utf-8 -*-
"""SpeleoMap Generator - LAYER: creazione, stile e salvataggio dei layer QGIS."""
import os
import math
import struct
import datetime
import unicodedata

import numpy as np

from qgis.core import (
    Qgis, QgsProject, QgsVectorLayer, QgsFeature, QgsGeometry, QgsPointXY, QgsRectangle, QgsField,
    QgsCoordinateReferenceSystem, QgsCoordinateTransformContext, QgsVectorFileWriter,
    QgsMarkerSymbol, QgsLineSymbol, QgsFillSymbol, QgsSingleSymbolRenderer,
    QgsPalLayerSettings, QgsTextFormat, QgsTextBufferSettings, QgsVectorLayerSimpleLabeling,
    QgsPrintLayout, QgsLayoutItemPage, QgsLayoutItemMap, QgsLayoutItemMapGrid, QgsLayoutItemLabel,
    QgsLayoutItemShape, QgsLayoutItemScaleBar, QgsLayoutItemPicture, QgsLayoutPoint, QgsLayoutSize,
    QgsUnitTypes, QgsLayoutExporter, QgsMapSettings, QgsDxfExport, QgsMessageLog, QgsLayerTreeLayer
)
from qgis.PyQt.QtCore import QVariant, QFile, QIODevice
from qgis.PyQt.QtGui import QColor, QFont


def mem_layer(gt, name, crs, fields):
    vl = QgsVectorLayer(gt, name, "memory")
    vl.setCrs(crs)
    pr = vl.dataProvider()
    flds = []
    for fname, ftype in fields:
        flds.append(QgsField(fname, {"s": QVariant.String, "d": QVariant.Double, "i": QVariant.Int}[ftype]))
    if flds:
        pr.addAttributes(flds)
        vl.updateFields()
    return vl


def add_rows(vl, rows):
    feats = []
    for geom, attrs in rows:
        f = QgsFeature(vl.fields())
        f.setGeometry(geom)
        if attrs:
            f.setAttributes(list(attrs))
        feats.append(f)
    vl.dataProvider().addFeatures(feats)
    vl.updateExtents()
    return vl


def lines_layer(name, crs, segments, attrs=None):
    vl = mem_layer("LineString", name, crs, [("nome", "s")])
    rows = [(QgsGeometry.fromPolylineXY([QgsPointXY(x, y) for x, y in sg]), [attrs or ""]) for sg in segments]
    return add_rows(vl, rows)


def points_layer(name, crs, a, b, color, size):
    vl = mem_layer("Point", name, crs, [])
    feats = []
    for i in range(len(a)):
        f = QgsFeature()
        f.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(float(a[i]), float(b[i]))))
        feats.append(f)
    vl.dataProvider().addFeatures(feats)
    vl.updateExtents()
    sym = QgsMarkerSymbol.createSimple({"name": "circle", "color": color, "outline_style": "no", "size": str(size)})
    vl.setRenderer(QgsSingleSymbolRenderer(sym))
    return vl


def style_line(vl, color="0,0,0", width=0.35, dash=False):
    sym = QgsLineSymbol.createSimple({"line_color": color, "line_width": str(width),
                                      "line_style": "dash" if dash else "solid", "capstyle": "round"})
    vl.setRenderer(QgsSingleSymbolRenderer(sym))


def style_fill(vl, fill="255,255,255,0", stroke="0,0,0", width=0.6):
    sym = QgsFillSymbol.createSimple({"color": fill, "outline_color": stroke, "outline_width": str(width),
                                      "joinstyle": "round"})
    vl.setRenderer(QgsSingleSymbolRenderer(sym))


def style_marker(vl, color="255,255,255", stroke="0,0,0", size=2.0, shape="circle"):
    sym = QgsMarkerSymbol.createSimple({"name": shape, "color": color, "outline_color": stroke,
                                        "outline_width": "0.3", "size": str(size)})
    vl.setRenderer(QgsSingleSymbolRenderer(sym))


def style_access(vl, color="16,48,208"):
    """Simbolo d'accesso degli ipogei: una 'Γ' blu (come nelle tavole del GSU)."""
    try:
        # costruito con la fabbrica di QGIS e passato al simbolo (che ne diventa proprietario): niente doppia distruzione
        from qgis.core import QgsFontMarkerSymbolLayer
        fl = QgsFontMarkerSymbolLayer.create({"font": "Arial", "chr": "\u0393", "size": "5", "color": color,
                                              "outline_width": "0"})
        sym = QgsMarkerSymbol.createSimple({"name": "circle", "size": "1"})
        sym.changeSymbolLayer(0, fl)
        vl.setRenderer(QgsSingleSymbolRenderer(sym))
    except Exception:
        style_marker(vl, "255,255,255", color, 3.2, shape="triangle")


def style_spot(vl, color="16,48,208"):
    """Quote del piano di calpestio: croce blu."""
    sym = QgsMarkerSymbol.createSimple({"name": "cross", "color": color, "outline_color": color,
                                        "outline_width": "0.5", "size": "2.6"})
    vl.setRenderer(QgsSingleSymbolRenderer(sym))


def label(vl, field, size=8, bold=False, around=True, dist=1.2, color="black"):
    pal = QgsPalLayerSettings()
    pal.enabled = True
    pal.fieldName = field
    fmt = QgsTextFormat()
    f = QFont("Arial")
    f.setBold(bold)
    fmt.setFont(f)
    fmt.setSize(size)
    fmt.setColor(QColor(color))
    buf = QgsTextBufferSettings()
    buf.setEnabled(True)
    buf.setSize(0.8)
    buf.setColor(QColor("white"))
    fmt.setBuffer(buf)
    pal.setFormat(fmt)
    try:
        pal.placement = Qgis.LabelPlacement.AroundPoint if around else Qgis.LabelPlacement.OverPoint
    except Exception:
        pal.placement = QgsPalLayerSettings.AroundPoint if around else QgsPalLayerSettings.OverPoint
    pal.dist = dist
    vl.setLabeling(QgsVectorLayerSimpleLabeling(pal))
    vl.setLabelsEnabled(True)


def write_gpkg(path, layers_names):
    first = True
    for lyr, name in layers_names:
        o = QgsVectorFileWriter.SaveVectorOptions()
        o.driverName = "GPKG"
        o.layerName = name
        o.actionOnExistingFile = (QgsVectorFileWriter.CreateOrOverwriteFile if first
                                  else QgsVectorFileWriter.CreateOrOverwriteLayer)
        QgsVectorFileWriter.writeAsVectorFormatV3(lyr, path, QgsCoordinateTransformContext(), o)
        first = False


def persist_layers(gpkg, items):
    """Salva i layer temporanei nel GeoPackage e li ricarica da file mantenendo lo stile."""
    out = []
    exists = os.path.exists(gpkg)
    for lyr, table in items:
        o = QgsVectorFileWriter.SaveVectorOptions()
        o.driverName = "GPKG"
        o.layerName = table
        o.actionOnExistingFile = (QgsVectorFileWriter.CreateOrOverwriteLayer if exists
                                  else QgsVectorFileWriter.CreateOrOverwriteFile)
        err = QgsVectorFileWriter.writeAsVectorFormatV3(lyr, gpkg, QgsCoordinateTransformContext(), o)
        if err[0] != QgsVectorFileWriter.NoError:
            raise RuntimeError("Salvataggio layer '%s' nel GeoPackage fallito: %s" % (table, err[1]))
        exists = True
        nl = QgsVectorLayer("%s|layername=%s" % (gpkg, table), lyr.name(), "ogr")
        if not nl.isValid():
            raise RuntimeError("Impossibile ricaricare il layer '%s' dal GeoPackage." % table)
        nl.setCrs(lyr.crs())
        if lyr.renderer():
            nl.setRenderer(lyr.renderer().clone())
        if lyr.labeling():
            nl.setLabeling(lyr.labeling().clone())
            nl.setLabelsEnabled(lyr.labelsEnabled())
        out.append(nl)
    return out
