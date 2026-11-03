# -*- coding: utf-8 -*-
"""SpeleoMap Generator - LAYOUT: tavole PDF (pagina, mappa, griglia, cartiglio)."""
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
from qgis.core import QgsLayoutMeasurement, QgsLayoutItemLegend, QgsLegendStyle  # noqa: E402
from qgis.PyQt.QtCore import Qt  # noqa: E402
from .smg_geometry import *  # noqa: F401,F403


PAGE_W, PAGE_H = 420.0, 297.0


MARGIN = 10.0


FOOTER_H = 42.0


GRID_PAD = 9.0


def choose_scale(w_m, h_m, frame_w_mm, frame_h_mm):
    need = max(w_m / (frame_w_mm / 1000.0), h_m / (frame_h_mm / 1000.0))
    for s in STD_SCALES:
        if s >= need:
            return s
    return int(math.ceil(need / 1000.0) * 1000)


def add_page(layout, idx, crs, layers, extent, scale_hint, title, subtitle, info, note,
             grid=True, north=False, grid_iv=None, meta=None, legend=None):
    pc = layout.pageCollection()
    if idx > 0:
        pg = QgsLayoutItemPage(layout)
        pg.setPageSize("A3", QgsLayoutItemPage.Landscape)
        pc.addPage(pg)

    def mv(item, x, y, w=None, h=None):
        item.attemptMove(QgsLayoutPoint(x, y, QgsUnitTypes.LayoutMillimeters), True, False, idx)
        if w is not None:
            item.attemptResize(QgsLayoutSize(w, h, QgsUnitTypes.LayoutMillimeters))

    pad = GRID_PAD if grid else 2.0
    fx, fy = MARGIN + pad, MARGIN + pad
    fw = PAGE_W - 2 * MARGIN - 2 * pad
    fh = PAGE_H - 2 * MARGIN - FOOTER_H - 2 * pad

    scale = scale_hint or choose_scale(extent.width() * 1.08, extent.height() * 1.08, fw, fh)
    c = extent.center()
    wm, hm = fw * scale / 1000.0, fh * scale / 1000.0
    ext = QgsRectangle(c.x() - wm / 2, c.y() - hm / 2, c.x() + wm / 2, c.y() + hm / 2)

    mp = QgsLayoutItemMap(layout)
    layout.addLayoutItem(mp)
    mv(mp, fx, fy, fw, fh)
    mp.setCrs(crs)
    mp.setLayers(layers)
    mp.setKeepLayerSet(True)
    mp.setExtent(ext)
    mp.setFrameEnabled(True)
    mp.setFrameStrokeWidth(QgsLayoutMeasurement(0.4))

    if grid:
        iv = grid_iv or nice_round(max(wm, hm) / 8.0, [0.5, 1, 2, 5, 10, 20, 50, 100, 200, 500])
        g = QgsLayoutItemMapGrid("griglia", mp)
        mp.grids().addGrid(g)
        g.setIntervalX(iv)
        g.setIntervalY(iv)
        g.setStyle(QgsLayoutItemMapGrid.Cross)
        g.setCrossLength(1.5)
        g.setGridLineColor(QColor(120, 120, 120))
        g.setAnnotationEnabled(True)
        g.setAnnotationPrecision(0 if iv >= 1 else 1)
        tf = QgsTextFormat()
        tf.setFont(QFont("Arial"))
        tf.setSize(7)
        g.setAnnotationTextFormat(tf)
        for side in (QgsLayoutItemMapGrid.Left, QgsLayoutItemMapGrid.Right,
                     QgsLayoutItemMapGrid.Top, QgsLayoutItemMapGrid.Bottom):
            g.setAnnotationPosition(QgsLayoutItemMapGrid.OutsideMapFrame, side)
        g.setAnnotationDirection(QgsLayoutItemMapGrid.Vertical, QgsLayoutItemMapGrid.Left)
        g.setAnnotationDirection(QgsLayoutItemMapGrid.Vertical, QgsLayoutItemMapGrid.Right)
        g.setFrameStyle(QgsLayoutItemMapGrid.LineBorder)
        g.setFrameWidth(0.3)

    # cornice pagina e cartiglio
    frame = QgsLayoutItemShape(layout)
    frame.setShapeType(QgsLayoutItemShape.Rectangle)
    frame.setSymbol(QgsFillSymbol.createSimple({"color": "255,255,255,0", "outline_color": "0,0,0", "outline_width": "0.6"}))
    layout.addLayoutItem(frame)
    mv(frame, MARGIN, MARGIN, PAGE_W - 2 * MARGIN, PAGE_H - 2 * MARGIN)

    fy0 = PAGE_H - MARGIN - FOOTER_H
    box = QgsLayoutItemShape(layout)
    box.setShapeType(QgsLayoutItemShape.Rectangle)
    box.setSymbol(QgsFillSymbol.createSimple({"color": "255,255,255,255", "outline_color": "0,0,0", "outline_width": "0.4"}))
    layout.addLayoutItem(box)
    mv(box, MARGIN, fy0, PAGE_W - 2 * MARGIN, FOOTER_H)

    def text(t, x, y, w, h, size, bold=False):
        lb = QgsLayoutItemLabel(layout)
        lb.setText(t)
        tf = QgsTextFormat()
        f = QFont("Arial")
        f.setBold(bold)
        tf.setFont(f)
        tf.setSize(size)
        lb.setTextFormat(tf)
        layout.addLayoutItem(lb)
        mv(lb, x, y, w, h)
        return lb

    meta = meta or {}
    # colonna A: titolo, tipo di tavola, note, scala
    xA = MARGIN + 4
    text(title, xA, fy0 + 3, 142, 9, 18, True)
    text(subtitle, xA, fy0 + 12, 142, 6, 12, True)
    if note:
        text(note, xA, fy0 + 18.5, 142, 12, 7)
    text("Scala 1:%d" % scale, xA, fy0 + 33, 26, 6, 9, False)

    sb = QgsLayoutItemScaleBar(layout)
    sb.setLinkedMap(mp)
    sb.setStyle("Single Box")
    sb.setUnits(QgsUnitTypes.DistanceMeters)
    sb.setUnitLabel("m")
    sb.setNumberOfSegmentsLeft(0)
    seg = nice_ceil(50.0 * scale / 1000.0 / 4.0)
    sb.setNumberOfSegments(4)
    sb.setUnitsPerSegment(seg)
    stf = QgsTextFormat()
    stf.setFont(QFont("Arial"))
    stf.setSize(7)
    sb.setTextFormat(stf)
    sb.setHeight(2.0)
    layout.addLayoutItem(sb)
    sb.applyDefaultSize()
    mv(sb, xA + 26, fy0 + 31.5)

    # colonna B: legenda grafica
    xB = MARGIN + 150
    if legend:
        lg = QgsLayoutItemLegend(layout)
        lg.setTitle("Legenda")
        lg.setLinkedMap(mp)
        lg.setAutoUpdateModel(False)
        layout.addLayoutItem(lg)
        root = lg.model().rootGroup()
        root.removeAllChildren()
        for lyr, lab in legend:
            if lyr is None:
                continue
            nd = root.addLayer(lyr)
            nd.setUseLayerName(False)
            nd.setName(lab)
            nd.setCustomProperty("legend/title-label", lab)
            try:
                from qgis.core import QgsMapLayerLegendUtils
                QgsMapLayerLegendUtils.setLegendNodeUserLabel(nd, 0, lab)
                lg.model().refreshLayerLegend(nd)
            except Exception:
                pass
        for st_, sz, bold in ((QgsLegendStyle.Title, 8, True), (QgsLegendStyle.SymbolLabel, 7, False)):
            try:
                tf_ = lg.rstyle(st_).textFormat()
                f_ = QFont("Arial")
                f_.setBold(bold)
                tf_.setFont(f_)
                tf_.setSize(sz)
                lg.rstyle(st_).setTextFormat(tf_)
            except Exception:
                pass
        lg.setSymbolWidth(6)
        lg.setSymbolHeight(3)
        lg.setBoxSpace(1.5)
        try:
            lg.updateLegend()
        except Exception:
            pass
        lg.adjustBoxSize()
        mv(lg, xB, fy0 + 1.5)
        lg.refresh()

    # colonna C: dati del rilievo e della grotta
    xC = MARGIN + 242
    text(info, xC, fy0 + 3, 106, FOOTER_H - 5, 7.5)

    # colonna D: gruppo, logo, oggetto, numero tavola
    xD = PAGE_W - MARGIN - 54
    dbox = QgsLayoutItemShape(layout)
    dbox.setShapeType(QgsLayoutItemShape.Rectangle)
    dbox.setSymbol(QgsFillSymbol.createSimple({"color": "255,255,255,0", "outline_color": "0,0,0", "outline_width": "0.4"}))
    layout.addLayoutItem(dbox)
    mv(dbox, xD, fy0, 54, FOOTER_H)
    y = fy0 + 2
    logo = meta.get("logo") or ""
    if logo and os.path.exists(logo):
        pic = QgsLayoutItemPicture(layout)
        pic.setPicturePath(logo)
        try:
            pic.setResizeMode(QgsLayoutItemPicture.Zoom)
        except Exception:
            pass
        layout.addLayoutItem(pic)
        mv(pic, xD + 2, y, 50, 13)
        y += 14
    g_lb = text(meta.get("group") or "", xD + 2, y, 50, 9, 8, True)
    g_lb.setHAlign(Qt.AlignHCenter)
    y += 9
    if meta.get("oggetto"):
        o_lb = text(meta.get("oggetto"), xD + 2, y, 50, 8, 6.5)
        o_lb.setHAlign(Qt.AlignHCenter)
    if meta.get("tav"):
        t_lb = text("TAV. %s" % meta.get("tav"), xD + 2, fy0 + FOOTER_H - 10, 50, 8, 12, True)
        t_lb.setHAlign(Qt.AlignHCenter)

    if north:
        na = QgsLayoutItemPicture(layout)
        na.setPicturePath(":/images/north_arrows/layout_default_north_arrow.svg")
        layout.addLayoutItem(na)
        mv(na, fx + fw - 20, fy + 3, 16, 16)
        try:
            na.setLinkedMap(mp)
        except Exception:
            pass
    return mp, scale



def add_table_page(layout, idx, title, subtitle, info, note, meta, headers, rows, col_widths, aligns=None, bold_last=False):
    """Pagina A3 con una tabella (righe di testo) e lo stesso cartiglio delle tavole grafiche."""
    from qgis.core import QgsLayoutItemManualTable, QgsLayoutFrame, QgsTableCell, QgsLayoutTableColumn
    pc = layout.pageCollection()
    if idx > 0:
        pg = QgsLayoutItemPage(layout)
        pg.setPageSize("A3", QgsLayoutItemPage.Landscape)
        pc.addPage(pg)

    def mv(item, x, y, w=None, h=None):
        item.attemptMove(QgsLayoutPoint(x, y, QgsUnitTypes.LayoutMillimeters), True, False, idx)
        if w is not None:
            item.attemptResize(QgsLayoutSize(w, h, QgsUnitTypes.LayoutMillimeters))

    def text(t, x, y, w, h, size, bold=False):
        lb = QgsLayoutItemLabel(layout)
        lb.setText(t)
        tf = QgsTextFormat()
        f = QFont("Arial")
        f.setBold(bold)
        tf.setFont(f)
        tf.setSize(size)
        lb.setTextFormat(tf)
        layout.addLayoutItem(lb)
        mv(lb, x, y, w, h)
        return lb

    def shape(x, y, w, h, fill="255,255,255,0", width="0.4"):
        sh = QgsLayoutItemShape(layout)
        sh.setShapeType(QgsLayoutItemShape.Rectangle)
        sh.setSymbol(QgsFillSymbol.createSimple({"color": fill, "outline_color": "0,0,0", "outline_width": width}))
        layout.addLayoutItem(sh)
        mv(sh, x, y, w, h)
        return sh

    shape(MARGIN, MARGIN, PAGE_W - 2 * MARGIN, PAGE_H - 2 * MARGIN, width="0.6")

    # tabella
    tb = QgsLayoutItemManualTable.create(layout)
    layout.addMultiFrame(tb)
    cols = []
    aligns = aligns or [Qt.AlignHCenter] * len(headers)
    for h, w, a in zip(headers, col_widths, aligns):
        c = QgsLayoutTableColumn()
        c.setHeading(h)
        c.setWidth(w)
        c.setHAlignment(a)
        cols.append(c)
    tb.setHeaders(cols)
    tb.setIncludeTableHeader(True)
    content = []
    for r in rows:
        content.append([QgsTableCell(str(v)) for v in r])
    if bold_last and content:
        for cell in content[-1]:
            try:
                tfb = QgsTextFormat()
                fb = QFont("Arial")
                fb.setBold(True)
                tfb.setFont(fb)
                tfb.setSize(8)
                cell.setTextFormat(tfb)
            except Exception:
                pass
    tb.setTableContents(content)
    try:
        tb.setColumnWidths([float(w) for w in col_widths])
    except Exception:
        pass
    try:
        tfh = QgsTextFormat()
        fh = QFont("Arial")
        fh.setBold(True)
        tfh.setFont(fh)
        tfh.setSize(8)
        tb.setHeaderTextFormat(tfh)
        tfc = QgsTextFormat()
        tfc.setFont(QFont("Arial"))
        tfc.setSize(8)
        tb.setContentTextFormat(tfc)
    except Exception:
        pass
    tb.setCellMargin(1.2)
    tb.setGridStrokeWidth(0.2)
    tb.setShowGrid(True)
    tw = sum(col_widths) + 2 * 1.2 * len(col_widths) + 2
    th = PAGE_H - 2 * MARGIN - FOOTER_H - 22
    tx = (PAGE_W - tw) / 2.0
    fr = QgsLayoutFrame(layout, tb)
    tb.addFrame(fr)
    mv(fr, tx, MARGIN + 14, tw, th)
    text(subtitle, tx, MARGIN + 6, tw, 7, 12, True)

    # cartiglio (come le altre tavole, senza scala grafica e legenda)
    fy0 = PAGE_H - MARGIN - FOOTER_H
    shape(MARGIN, fy0, PAGE_W - 2 * MARGIN, FOOTER_H, fill="255,255,255,255")
    meta = meta or {}
    xA = MARGIN + 4
    text(title, xA, fy0 + 3, 142, 9, 18, True)
    text(subtitle, xA, fy0 + 12, 142, 6, 12, True)
    if note:
        text(note, xA, fy0 + 18.5, 230, 18, 7)
    xC = MARGIN + 242
    text(info, xC, fy0 + 3, 106, FOOTER_H - 5, 7.5)
    xD = PAGE_W - MARGIN - 54
    shape(xD, fy0, 54, FOOTER_H)
    y = fy0 + 2
    logo = meta.get("logo") or ""
    if logo and os.path.exists(logo):
        pic = QgsLayoutItemPicture(layout)
        pic.setPicturePath(logo)
        try:
            pic.setResizeMode(QgsLayoutItemPicture.Zoom)
        except Exception:
            pass
        layout.addLayoutItem(pic)
        mv(pic, xD + 2, y, 50, 13)
        y += 14
    g_lb = text(meta.get("group") or "", xD + 2, y, 50, 9, 8, True)
    g_lb.setHAlign(Qt.AlignHCenter)
    y += 9
    if meta.get("oggetto"):
        o_lb = text(meta.get("oggetto"), xD + 2, y, 50, 8, 6.5)
        o_lb.setHAlign(Qt.AlignHCenter)
    if meta.get("tav"):
        t_lb = text("TAV. %s" % meta.get("tav"), xD + 2, fy0 + FOOTER_H - 10, 50, 8, 12, True)
        t_lb.setHAlign(Qt.AlignHCenter)
    return tb
