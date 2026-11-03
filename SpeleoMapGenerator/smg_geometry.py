# -*- coding: utf-8 -*-
"""SpeleoMap Generator - GEOMETRIE: contorno, asse, proiezioni, pavimento/volta."""
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


STD_SCALES = [10, 20, 25, 50, 100, 200, 250, 500, 1000, 2000, 2500, 5000, 10000]


NICE = [0.1, 0.2, 0.25, 0.5, 1, 2, 2.5, 5, 10, 20, 25, 50, 100, 200, 250, 500]


def nice_ceil(v, values=NICE):
    for n in values:
        if n >= v:
            return n
    return values[-1]


def nice_round(v, values=NICE):
    return min(values, key=lambda n: abs(n - v))


def occupancy_polygon(a, b, cell, min_count=2, smooth=None, min_area=1.0, keep_holes=True):
    """Poligono dell'area occupata dai punti (a,b) su griglia di passo 'cell'."""
    if len(a) == 0:
        return None
    a0, b0 = float(a.min()), float(b.min())
    ia = ((a - a0) / cell).astype(np.int64)
    ib = ((b - b0) / cell).astype(np.int64)
    key = ia * 1000003 + ib
    u, cnt = np.unique(key, return_counts=True)
    u = u[cnt >= min_count]
    if len(u) == 0:
        return None
    rects = [QgsGeometry.fromRect(QgsRectangle(a0 + (k // 1000003) * cell, b0 + (k % 1000003) * cell,
                                               a0 + (k // 1000003 + 1) * cell, b0 + (k % 1000003 + 1) * cell))
             for k in u.tolist()]
    g = QgsGeometry.unaryUnion(rects)
    sm = cell * 1.2 if smooth is None else smooth
    if sm > 0:
        g = g.buffer(sm, 4).buffer(-sm, 4)
    parts = g.asGeometryCollection() if g.isMultipart() else [g]
    out = []
    for p in parts:
        if p.area() < min_area:
            continue
        rings = p.asPolygon()
        if not rings:
            continue
        keep = [rings[0]]
        if keep_holes:
            for r in rings[1:]:
                if abs(QgsGeometry.fromPolygonXY([r]).area()) >= max(min_area, 1.0):
                    keep.append(r)
        out.append(QgsGeometry.fromPolygonXY(keep))
    if not out:
        return None
    res = QgsGeometry.collectGeometry(out) if len(out) > 1 else out[0]
    return res.simplify(cell * 0.3)


def centerline(x, y, step=1.0):
    """Asse della grotta: direzione principale + baricentri per fasce trasversali, levigati."""
    cx, cy = float(x.mean()), float(y.mean())
    w, v = np.linalg.eigh(np.cov(np.vstack([x - cx, y - cy])))
    ux, uy = float(v[0, 1]), float(v[1, 1])
    if ux < 0 or (abs(ux) < 1e-9 and uy < 0):
        ux, uy = -ux, -uy
    s = (x - cx) * ux + (y - cy) * uy
    smin, smax = np.percentile(s, 0.2), np.percentile(s, 99.8)
    edges = np.arange(smin, smax + step, step)
    pts = []
    for i in range(len(edges) - 1):
        m = (s >= edges[i]) & (s < edges[i + 1])
        if m.sum() < 20:
            continue
        pts.append((float(np.median(x[m])), float(np.median(y[m]))))
    if len(pts) < 2:
        pts = [(cx + ux * smin, cy + uy * smin), (cx + ux * smax, cy + uy * smax)]
    P = np.array(pts)
    if len(P) >= 5:
        Q = P.copy()
        for i in range(1, len(P) - 1):
            lo, hi = max(0, i - 2), min(len(P), i + 3)
            Q[i] = P[lo:hi].mean(axis=0)
        P = Q
    # estende le estremita' fino ai limiti della nuvola lungo la direzione principale
    d0 = P[0] - P[1]
    d1 = P[-1] - P[-2]
    s_first = (P[0, 0] - cx) * ux + (P[0, 1] - cy) * uy
    s_last = (P[-1, 0] - cx) * ux + (P[-1, 1] - cy) * uy
    if np.linalg.norm(d0) > 0:
        P[0] = P[0] + d0 / np.linalg.norm(d0) * max(0.0, s_first - smin)
    if np.linalg.norm(d1) > 0:
        P[-1] = P[-1] + d1 / np.linalg.norm(d1) * max(0.0, smax - s_last)
    return [QgsPointXY(float(a), float(b)) for a, b in P]


def project_on_polyline(V, x, y):
    """Per ogni punto: progressiva s lungo la polilinea e distanza laterale con segno."""
    V = np.asarray(V, dtype=float)
    seg = V[1:] - V[:-1]
    L = np.hypot(seg[:, 0], seg[:, 1])
    cum = np.concatenate([[0.0], np.cumsum(L)])
    best_d = np.full(len(x), np.inf)
    best_s = np.zeros(len(x))
    best_o = np.zeros(len(x))
    for k in range(len(seg)):
        if L[k] <= 0:
            continue
        ax, ay = V[k]
        dx, dy = seg[k] / L[k]
        rx, ry = x - ax, y - ay
        t = np.clip(rx * dx + ry * dy, 0.0, L[k])
        px, py = ax + dx * t, ay + dy * t
        d = (x - px) ** 2 + (y - py) ** 2
        m = d < best_d
        best_d[m] = d[m]
        best_s[m] = cum[k] + t[m]
        best_o[m] = (dx * ry - dy * rx)[m]
    return best_s, best_o, cum[-1]


def point_and_dir_at(V, s):
    V = np.asarray(V, dtype=float)
    seg = V[1:] - V[:-1]
    L = np.hypot(seg[:, 0], seg[:, 1])
    cum = np.concatenate([[0.0], np.cumsum(L)])
    k = int(np.clip(np.searchsorted(cum, s) - 1, 0, len(seg) - 1))
    t = (s - cum[k]) / L[k] if L[k] > 0 else 0.0
    p = V[k] + seg[k] * t
    # direzione media locale (piu' stabile)
    k0, k1 = max(0, k - 1), min(len(seg) - 1, k + 1)
    d = (V[k1 + 1] - V[k0])
    d = d / (np.linalg.norm(d) or 1.0)
    return p, d


def envelope(a, b, step, min_count=3, min_height=0.3, max_gap_bins=2, max_jump=None, min_pts=3):
    """Pavimento (inviluppo basso) e volta (inviluppo alto) di una nuvola 2D (a = ascissa, b = quota).
    Restituisce due liste di tratti [(x, y), ...]; i tratti si interrompono dove mancano i dati
    e la volta compare solo dove l'altezza libera supera min_height (dove la scansione l'ha ripresa)."""
    if len(a) < min_count:
        return [], []
    a0 = float(a.min())
    idx = ((a - a0) / step).astype(np.int64)
    order = np.argsort(idx)
    idx, bs = idx[order], b[order]
    uniq, start = np.unique(idx, return_index=True)
    ends = list(start[1:]) + [len(idx)]
    rows = []
    for i, s0, s1 in zip(uniq, start, ends):
        if s1 - s0 < min_count:
            continue
        bz = bs[s0:s1]
        rows.append((int(i), a0 + (i + 0.5) * step, float(np.percentile(bz, 2)), float(np.percentile(bz, 98))))

    jump = max_jump if max_jump is not None else max(0.3, 4.0 * step)

    def segs(pick, ok):
        out, cur, last = [], [], None
        for r in rows:
            if not ok(r):
                if len(cur) > 1:
                    out.append(cur)
                cur, last = [], None
                continue
            if last is not None and (r[0] - last > max_gap_bins or abs(pick(r) - cur[-1][1]) > jump):
                if len(cur) > 1:
                    out.append(cur)
                cur = []
            cur.append((r[1], pick(r)))
            last = r[0]
        if len(cur) > 1:
            out.append(cur)
        sm = []
        out = [c for c in out if len(c) >= min_pts]
        for c in out:
            if len(c) >= 3:
                ys = [c[0][1]] + [sorted([c[k - 1][1], c[k][1], c[k + 1][1]])[1] for k in range(1, len(c) - 1)] + [c[-1][1]]
                c = [(c[k][0], ys[k]) for k in range(len(c))]
            sm.append(c)
        return sm

    floor = segs(lambda r: r[2], lambda r: True)
    roof = segs(lambda r: r[3], lambda r: r[3] - r[2] >= min_height)
    return floor, roof
