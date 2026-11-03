# -*- coding: utf-8 -*-
"""SpeleoMap Generator - INPUT: lettura file LAS."""
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


def read_las(path):
    """Legge X,Y,Z da un LAS non compresso (versioni 1.0-1.4, tutti i formati punto)."""
    with open(path, "rb") as fh:
        h = fh.read(375)
    if h[:4] != b"LASF":
        raise RuntimeError("Il file selezionato non e' un LAS valido.")
    if h[104] & 0x80 or path.lower().endswith(".laz"):
        raise RuntimeError("Il file e' un LAZ compresso: esportalo come LAS non compresso (es. da Scaniverse o CloudCompare).")
    ver_minor = h[25]
    offset = struct.unpack("<I", h[96:100])[0]
    rec_len = struct.unpack("<H", h[105:107])[0]
    n = struct.unpack("<I", h[107:111])[0]
    if ver_minor >= 4:
        n64 = struct.unpack("<Q", h[247:255])[0]
        n = n64 or n
    sc = struct.unpack("<3d", h[131:155])
    of = struct.unpack("<3d", h[155:179])
    if n == 0:
        raise RuntimeError("Il file LAS non contiene punti.")
    mm = np.memmap(path, dtype=np.uint8, mode="r", offset=offset, shape=(n, rec_len))
    raw = np.ascontiguousarray(mm[:, :12]).view("<i4").reshape(n, 3)
    x = raw[:, 0] * sc[0] + of[0]
    y = raw[:, 1] * sc[1] + of[1]
    z = raw[:, 2] * sc[2] + of[2]
    del mm
    return x, y, z



def read_las_epsg(path):
    """Codice EPSG scritto nel LAS (GeoKeyDirectory 34735 o WKT 2112). None se assente."""
    import re
    try:
        with open(path, "rb") as fh:
            h = fh.read(375)
            if h[:4] != b"LASF":
                return None
            hs = struct.unpack("<H", h[94:96])[0]
            nv = struct.unpack("<I", h[100:104])[0]
            fh.seek(hs)
            for _ in range(nv):
                r = fh.read(54)
                rid = struct.unpack("<H", r[18:20])[0]
                ln = struct.unpack("<H", r[20:22])[0]
                data = fh.read(ln)
                if rid == 34735 and ln >= 8:
                    vals = struct.unpack("<%dH" % (ln // 2), data[: (ln // 2) * 2])
                    nkeys = vals[3]
                    keys = {}
                    for k in range(nkeys):
                        kid, loc, cnt, val = vals[4 + 4 * k: 8 + 4 * k]
                        if loc == 0:
                            keys[kid] = val
                    for kid in (3072, 2048):
                        if kid in keys and 1024 < keys[kid] < 32767:
                            return int(keys[kid])
                if rid == 2112:
                    ids = re.findall(r'AUTHORITY\["EPSG","(\d+)"\]', data.decode("latin-1", "ignore"))
                    if ids:
                        return int(ids[-1])
    except Exception:
        return None
    return None
