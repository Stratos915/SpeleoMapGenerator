# -*- coding: utf-8 -*-
"""SpeleoMap Generator - Utilita' comuni (log, nomi file)."""
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


def log(msg):
    QgsMessageLog.logMessage(str(msg), "SpeleoMap Generator", Qgis.Info)


def slug(text):
    t = unicodedata.normalize("NFKD", text or "grotta").encode("ascii", "ignore").decode()
    t = "".join(c if c.isalnum() else "_" for c in t).strip("_")
    return t or "grotta"



def parse_latlon(text):
    """Legge coordinate come le copia un telefono: '36.4321, 28.2201', '36,4321 28,2201',
    '36°25'55.6"N 28°13'12.3"E'. Restituisce (lat, lon) in gradi decimali WGS84, None se vuoto."""
    import re
    t = (text or "").strip().upper()
    for a, b in (("″", '"'), ("”", '"'), ("''", '"'), ("′", "'"), ("’", "'"), ("º", "°")):
        t = t.replace(a, b)
    if not t:
        return None
    dms = re.findall(r"(\d+(?:[.,]\d+)?)\s*°\s*(?:(\d+(?:[.,]\d+)?)\s*'\s*)?(?:(\d+(?:[.,]\d+)?)\s*\"\s*)?([NSEWO])", t)
    if len(dms) == 2:
        vals = {}
        for d, m, s, h in dms:
            v = float(d.replace(",", ".")) + float((m or "0").replace(",", ".")) / 60.0 + float((s or "0").replace(",", ".")) / 3600.0
            if h in ("S", "W", "O"):
                v = -v
            vals["lat" if h in ("N", "S") else "lon"] = v
        if "lat" not in vals or "lon" not in vals:
            raise ValueError("servono una latitudine (N/S) e una longitudine (E/O)")
        lat, lon = vals["lat"], vals["lon"]
    else:
        toks = [x for x in re.split(r"[;\s]+|,\s+", t) if x]
        if len(toks) == 1 and toks[0].count(",") == 1:
            toks = toks[0].split(",")
        if len(toks) != 2:
            raise ValueError("formato non riconosciuto")
        lat, lon = [float(x.replace(",", ".")) for x in toks]
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        raise ValueError("valori fuori scala (latitudine prima, longitudine dopo)")
    return lat, lon
