# -*- coding: utf-8 -*-
"""SpeleoMap Generator - EXPORT CAD: DXF R12 editabile."""
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
from .smg_util import *  # noqa: F401,F403


def export_dxf(path, layers, crs, extent):
    try:
        ms = QgsMapSettings()
        ms.setLayers(layers)
        ms.setDestinationCrs(crs)
        ms.setExtent(extent)
        dxf = QgsDxfExport()
        dxf.setMapSettings(ms)
        dxf.addLayers([QgsDxfExport.DxfLayer(l) for l in layers])
        dxf.setDestinationCrs(crs)
        try:
            dxf.setSymbologyExport(Qgis.FeatureSymbologyExport.NoSymbology)
        except Exception:
            pass
        f = QFile(path)
        f.open(QIODevice.WriteOnly | QIODevice.Truncate)
        res = dxf.writeToFile(f, "UTF-8")
        f.close()
        return res == QgsDxfExport.ExportResult.Success if hasattr(QgsDxfExport, "ExportResult") else True
    except Exception as e:
        log("DXF export error: %s" % e)
        return False


class DxfR12:
    """Scrittore DXF R12 minimale: solo POLYLINE, POINT, TEXT su layer separati (nessun blocco, nessuna immagine)."""
    def __init__(self):
        self.layers = []
        self.ent = []

    def _layer(self, name):
        if name not in self.layers:
            self.layers.append(name)

    @staticmethod
    def _txt(v):
        return str(v).replace("\n", " ")

    def polyline(self, layer, pts, closed=False):
        if len(pts) < 2:
            return
        self._layer(layer)
        e = ["0", "POLYLINE", "8", layer, "66", "1", "10", "0.0", "20", "0.0", "30", "0.0", "70", "1" if closed else "0"]
        for x, y in pts:
            e += ["0", "VERTEX", "8", layer, "10", "%.4f" % x, "20", "%.4f" % y, "30", "0.0"]
        e += ["0", "SEQEND", "8", layer]
        self.ent.append(e)

    def point(self, layer, x, y):
        self._layer(layer)
        self.ent.append(["0", "POINT", "8", layer, "10", "%.4f" % x, "20", "%.4f" % y, "30", "0.0"])

    def text(self, layer, x, y, h, value, center=False, align=None):
        if value is None or str(value) == "" or str(value) == "NULL":
            return
        self._layer(layer)
        if align is None:
            align = 1 if center else 0
        e = ["0", "TEXT", "8", layer, "10", "%.4f" % x, "20", "%.4f" % y, "30", "0.0", "40", "%.4f" % h, "1", self._txt(value)]
        if align:
            e += ["72", str(align), "11", "%.4f" % x, "21", "%.4f" % y, "31", "0.0"]
        self.ent.append(e)

    def grid_frame(self, prefix, ext, iv, scale, labels=True, grid=True, decimals=0):
        """Cornice della tavola + griglia a crocette con coordinate (come nel PDF), su layer separati."""
        x0, y0, x1, y1 = ext.xMinimum(), ext.yMinimum(), ext.xMaximum(), ext.yMaximum()
        self.polyline(prefix + "_FRAME", [(x0, y0), (x1, y0), (x1, y1), (x0, y1)], closed=True)
        if not grid or not iv or iv <= 0:
            return
        g = prefix + "_GRID"
        cl = 1.5 * scale / 1000.0
        th = 3.0 * scale / 1000.0
        fmt = "%%.%df" % decimals
        gx = math.ceil(x0 / iv) * iv
        xs = []
        while gx <= x1 + 1e-9:
            xs.append(gx)
            gx += iv
        gy = math.ceil(y0 / iv) * iv
        ys = []
        while gy <= y1 + 1e-9:
            ys.append(gy)
            gy += iv
        for x in xs:
            for y in ys:
                self.polyline(g, [(max(x0, x - cl), y), (min(x1, x + cl), y)])
                self.polyline(g, [(x, max(y0, y - cl)), (x, min(y1, y + cl))])
        if labels:
            for x in xs:
                self.polyline(g, [(x, y0), (x, y0 - cl)])
                self.text(g, x, y0 - cl - th * 1.6, th, fmt % x, align=1)
            for y in ys:
                self.polyline(g, [(x0, y), (x0 - cl, y)])
                self.text(g, x0 - cl - th * 0.8, y - th * 0.5, th, fmt % y, align=2)

    def add_layer(self, layer, vl, name_layer, label_field=None, text_h=0.5, text_only=False, center=False):
        from qgis.core import QgsWkbTypes
        for f in vl.getFeatures():
            g = f.geometry()
            if g is None or g.isEmpty():
                continue
            gt = QgsWkbTypes.geometryType(g.wkbType())
            if gt == QgsWkbTypes.PolygonGeometry:
                polys = g.asMultiPolygon() if g.isMultipart() else [g.asPolygon()]
                for poly in polys:
                    for ring in poly:
                        pts = [(p.x(), p.y()) for p in ring]
                        if len(pts) > 2 and pts[0] == pts[-1]:
                            pts = pts[:-1]
                        self.polyline(layer, pts, closed=True)
            elif gt == QgsWkbTypes.LineGeometry:
                lines = g.asMultiPolyline() if g.isMultipart() else [g.asPolyline()]
                for ln in lines:
                    self.polyline(layer, [(p.x(), p.y()) for p in ln])
            elif gt == QgsWkbTypes.PointGeometry:
                pts = g.asMultiPoint() if g.isMultipart() else [g.asPoint()]
                for pt in pts:
                    if not text_only:
                        self.point(layer, pt.x(), pt.y())
                    if label_field:
                        off = 0 if (text_only or center) else text_h * 0.6
                        self.text(name_layer, pt.x() + off, pt.y() + off, text_h, f[label_field], center=center)

    def save(self, path):
        out = ["0", "SECTION", "2", "HEADER", "9", "$ACADVER", "1", "AC1009", "9", "$DWGCODEPAGE", "3", "ANSI_1252",
               "9", "$INSUNITS", "70", "6", "0", "ENDSEC",
               "0", "SECTION", "2", "TABLES", "0", "TABLE", "2", "LAYER", "70", str(len(self.layers))]
        for i, name in enumerate(self.layers):
            col = 7
            for key, c in (("ENTRANCE", 1), ("FLOOR", 30), ("ROOF", 150), ("OUTLINE", 7), ("AXIS", 1), ("STATIONS", 3), ("SECTION_LABELS", 6), ("LABELS", 4), ("SECTIONS", 5)):
                if name.endswith(key):
                    col = c
                    break
            out += ["0", "LAYER", "2", name, "70", "0", "62", str(col), "6", "CONTINUOUS"]
        out += ["0", "ENDTAB", "0", "ENDSEC", "0", "SECTION", "2", "ENTITIES"]
        for e in self.ent:
            out += e
        out += ["0", "ENDSEC", "0", "EOF"]
        with open(path, "w", encoding="cp1252", errors="replace", newline="\r\n") as fh:
            fh.write("\n".join(out) + "\n")
        return True


def write_dxf(path, scale, items, frame=None):
    """items: (layer_dxf, vettoriale, campo_etichetta, altezza_testo_mm, solo_testo, centrato[, layer_testi]).
    frame: dict(prefix, ext, iv, grid, decimals) per cornice e griglia."""
    w = DxfR12()
    if frame:
        w.grid_frame(frame["prefix"], frame["ext"], frame.get("iv"), scale, grid=frame.get("grid", True),
                     decimals=frame.get("decimals", 0))
    for it in items:
        layer, vl, field, h_mm, text_only, center = it[:6]
        lab = it[6] if len(it) > 6 else (layer + "_TESTI" if (field and not text_only) else layer)
        w.add_layer(layer, vl, lab, field, h_mm * scale / 1000.0, text_only, center)
    return w.save(path)
