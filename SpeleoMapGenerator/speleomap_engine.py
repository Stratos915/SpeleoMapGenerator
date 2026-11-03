# -*- coding: utf-8 -*-
"""SpeleoMap Generator - MOTORE: orchestrazione (input -> geometrie -> layer -> tavole -> export).
Le funzioni di supporto sono nei moduli smg_*.py."""
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
    QgsUnitTypes, QgsLayoutExporter, QgsMapSettings, QgsDxfExport, QgsMessageLog, QgsLayerTreeLayer, QgsFeatureRequest
)
from qgis.PyQt.QtCore import QVariant, QFile, QIODevice
from qgis.PyQt.QtGui import QColor, QFont
from .smg_util import *  # noqa: F401,F403
from .smg_profile_data import profile_samples, value_at, write_profile_csv
from .smg_export_cave import write_svx, write_compass_dat
from .smg_input_las import *  # noqa: F401,F403
from .smg_geometry import *  # noqa: F401,F403
from .smg_layers import *  # noqa: F401,F403
from .smg_layout import *  # noqa: F401,F403
from .smg_export_dxf import *  # noqa: F401,F403


def generate(params, progress=None):
    """params: las_path, crs (QgsCoordinateReferenceSystem), cave_name, location, authors, survey_date,
    station_step, section_step, section_thickness, profile_halfwidth, out_dir, axis_layer (opzionale)."""
    def step(msg):
        log(msg)
        if progress:
            progress(msg)

    las = params["las_path"]
    crs = params["crs"]
    name = (params.get("cave_name") or "Grotta").strip()
    out_dir = params["out_dir"]
    os.makedirs(out_dir, exist_ok=True)
    st_step = float(params.get("station_step") or 2.0)
    sec_step = float(params.get("section_step") or 5.0)
    th = float(params.get("section_thickness") or 0.10)
    phw = float(params.get("profile_halfwidth") or 0.30)

    step("Lettura LAS...")
    x, y, z = read_las(las)
    n_all = len(x)
    if n_all > 2000000:
        k = int(math.ceil(n_all / 2000000.0))
        x, y, z = x[::k], y[::k], z[::k]
    step("Punti letti: %d" % n_all)

    # ---------------- RILIEVO: correzione della posizione, soglia d'accesso, gallerie (Fase 7)
    from .smg_ipogei import prepare_survey
    prep = prepare_survey(x, y, z, params, crs, step)
    x, y, z = prep["x"], prep["y"], prep["z"]
    ipogeo = prep["kind"] == "ipogeo"

    ext_w = float(x.max() - x.min())
    ext_h = float(y.max() - y.min())
    cell = max(0.1, min(0.5, max(ext_w, ext_h) / 250.0))

    # ---------------- PIANTA
    step("Contorno in pianta...")
    outline = occupancy_polygon(x, y, cell, min_count=2, min_area=max(1.0, cell * cell * 20))
    if outline is None:
        raise RuntimeError("Impossibile ricavare il contorno della grotta dai punti.")

    step("Asse della grotta...")
    axis_pts = None
    al = params.get("axis_layer")
    if al is not None and al.isValid() and al.featureCount() > 0:
        g = next(al.getFeatures()).geometry()
        if al.crs() != crs:
            from qgis.core import QgsCoordinateTransform
            g.transform(QgsCoordinateTransform(al.crs(), crs, QgsProject.instance()))
        line = g.asMultiPolyline()[0] if g.isMultipart() else g.asPolyline()
        if len(line) >= 2:
            axis_pts = [QgsPointXY(p) for p in line]
    if axis_pts is None:
        axis_pts = centerline(x, y, step=max(0.5, cell * 4))
    V = np.array([[p.x(), p.y()] for p in axis_pts])
    s_all, o_all, L = project_on_polyline(V, x, y)

    axis = mem_layer("LineString", "asse", crs, [("length_m", "d")])
    add_rows(axis, [(QgsGeometry.fromPolylineXY(axis_pts), [round(L, 2)])])
    style_line(axis, "200,0,0", 0.35, dash=True)

    stations = mem_layer("Point", "stazioni", crs, [("station_id", "s"), ("s_m", "d")])
    rows = []
    sv, k = 0.0, 0
    while sv <= L + 1e-6:
        p, d = point_and_dir_at(V, sv)
        rows.append((QgsGeometry.fromPointXY(QgsPointXY(p[0], p[1])), ["S%d" % k, round(sv, 2)]))
        sv += st_step
        k += 1
    add_rows(stations, rows)
    style_marker(stations, "255,255,255", "200,0,0", 1.6)
    label(stations, "station_id", 6, around=True, dist=0.8, color="#b00000")

    outline_l = mem_layer("Polygon", "contorno", crs, [("area_m2", "d")])
    add_rows(outline_l, [(outline, [round(outline.area(), 1)])])
    style_fill(outline_l, "255,255,255,0", "0,0,0", 0.6)

    dec = max(1, len(x) // 80000)
    plan_pts = points_layer("punti_pianta", crs, x[::dec], y[::dec], "150,150,150", 0.12)

    # ---------------- SEZIONI
    step("Sezioni trasversali...")
    sec_pos = []
    sv = sec_step / 2.0 if L < sec_step * 2 else sec_step
    while sv < L - 0.25:
        sec_pos.append(sv)
        sv += sec_step
    if not sec_pos:
        sec_pos = [L / 2.0]
    letters = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    sections = []
    user_sec = params.get("section_lines") or []
    if user_sec:
        sec_pos = []                                   # sezioni dove le ha disegnate l'utente
    for k, us in enumerate(user_sec):
        a_, b_ = np.array(us["pts"][0], float), np.array(us["pts"][-1], float)
        ln = float(np.hypot(*(b_ - a_)))
        if ln < 0.2:
            continue
        nrm = (b_ - a_) / ln                          # direzione della traccia (da A ad A')
        d = np.array([nrm[1], -nrm[0]])
        rx, ry = x - a_[0], y - a_[1]
        t = rx * nrm[0] + ry * nrm[1]
        along = rx * d[0] + ry * d[1]
        m = (np.abs(along) <= th) & (t >= -0.5) & (t <= ln + 0.5)
        if m.sum() < 30:
            step("Sezione %s: troppo pochi punti lungo la traccia (spostala sulla grotta)" % us.get("lab", k + 1))
            continue
        tt, zz = t[m], z[m]
        # incrocio con l'asse della grotta: li' va lo zero della sezione (linea rossa), come nelle sezioni automatiche
        _tq = np.arange(0.0, ln + 1e-6, 0.1)
        _sq, _oq, _ = project_on_polyline(V, a_[0] + nrm[0] * _tq, a_[1] + nrm[1] * _tq)
        _k = int(np.argmin(np.abs(_oq)))
        t0 = float(_tq[_k]) if abs(_oq[_k]) < 2.0 else ln / 2.0
        s_mid = float(_sq[_k])
        tt = tt - t0
        a_ = a_ + nrm * t0
        lab = (us.get("lab") or "").strip() or (letters[k] if k < 26 else "S%d" % k)
        sections.append(dict(lab=lab, s=s_mid, p=a_, nrm=nrm, t=tt, z=zz,
                             tmin=float(np.percentile(tt, 0.5)), tmax=float(np.percentile(tt, 99.5)),
                             zmin=float(zz.min()), zmax=float(zz.max())))
    for i, s0 in enumerate(sec_pos):
        p, d = point_and_dir_at(V, s0)
        nrm = np.array([-d[1], d[0]])
        rx, ry = x - p[0], y - p[1]
        along = rx * d[0] + ry * d[1]
        t = rx * nrm[0] + ry * nrm[1]
        m = (np.abs(along) <= th) & (np.abs(s_all - s0) <= max(3.0, 4 * th))
        if m.sum() < 30:
            continue
        tt, zz = t[m], z[m]
        lab = letters[i] if i < 26 else "S%d" % i
        sections.append(dict(lab=lab, s=s0, p=p, nrm=nrm, t=tt, z=zz,
                             tmin=float(np.percentile(tt, 0.5)), tmax=float(np.percentile(tt, 99.5)),
                             zmin=float(zz.min()), zmax=float(zz.max())))

    traces = mem_layer("LineString", "tracce_sezioni", crs, [("sezione", "s"), ("s_m", "d")])
    tr_lab = mem_layer("Point", "etichette_tracce", crs, [("txt", "s")])
    rows, lrows = [], []
    for sc in sections:
        a = QgsPointXY(sc["p"][0] + sc["nrm"][0] * (sc["tmin"] - 0.5), sc["p"][1] + sc["nrm"][1] * (sc["tmin"] - 0.5))
        b = QgsPointXY(sc["p"][0] + sc["nrm"][0] * (sc["tmax"] + 0.5), sc["p"][1] + sc["nrm"][1] * (sc["tmax"] + 0.5))
        rows.append((QgsGeometry.fromPolylineXY([a, b]), [sc["lab"], round(sc["s"], 2)]))
        lrows.append((QgsGeometry.fromPointXY(a), [sc["lab"]]))
        lrows.append((QgsGeometry.fromPointXY(b), [sc["lab"] + "'"]))
    add_rows(traces, rows)
    add_rows(tr_lab, lrows)
    style_line(traces, "0,0,160", 0.3)
    style_marker(tr_lab, "0,0,160", "0,0,160", 0.6)
    label(tr_lab, "txt", 8, bold=True, around=True, dist=0.6, color="#0000a0")

    # disposizione delle sezioni in una griglia (coordinate locali in metri)
    sec_pts_a, sec_pts_b = [], []
    sec_lab_rows, sec_axis_rows, sec_outline_rows = [], [], []
    sec_floor_rows, sec_roof_rows = [], []
    if sections:
        W = max(sc["tmax"] - sc["tmin"] for sc in sections) + 2.0
        H = max(sc["zmax"] - sc["zmin"] for sc in sections) + 3.0
        ncol = max(1, int(round(math.sqrt(len(sections) * (W and (H / W) * 1.4 or 1)))))
        ncol = min(ncol, len(sections))
        for i, sc in enumerate(sections):
            r, cidx = divmod(i, ncol)
            ox = cidx * W - (sc["tmin"] + sc["tmax"]) / 2.0
            oy = -r * H - sc["zmin"]
            sec_pts_a.append(sc["t"] + ox)
            sec_pts_b.append(sc["z"] + oy)
            sec_lab_rows.append((QgsGeometry.fromPointXY(QgsPointXY(cidx * W, sc["zmax"] + oy + 0.8)),
                                 ["%s - %s'   (prog. %.1f m)" % (sc["lab"], sc["lab"], sc["s"])]))
            sec_axis_rows.append((QgsGeometry.fromPolylineXY([QgsPointXY(ox, sc["zmin"] + oy - 0.3),
                                                              QgsPointXY(ox, sc["zmax"] + oy + 0.3)]), []))
            poly = occupancy_polygon(sc["t"] + ox, sc["z"] + oy, 0.08, min_count=1, smooth=0.06, min_area=0.0,
                                     keep_holes=True)
            if poly is not None:
                sec_outline_rows.append((poly, [sc["lab"]]))
            fl, rf = envelope(sc["t"] + ox, sc["z"] + oy, 0.10, min_count=3, min_height=0.3)
            sec_floor_rows += [(sg, sc["lab"]) for sg in fl]
            sec_roof_rows += [(sg, sc["lab"]) for sg in rf]
        sa, sb = np.concatenate(sec_pts_a), np.concatenate(sec_pts_b)
    else:
        sa, sb = np.array([0.0]), np.array([0.0])
    sec_points = points_layer("sezioni_punti", crs, sa, sb, "40,40,40", 0.15)
    sec_shape = mem_layer("Polygon", "sezioni_profilo", crs, [("sezione", "s")])
    add_rows(sec_shape, sec_outline_rows)
    style_fill(sec_shape, "225,215,195,255", "0,0,0", 0.25)
    sec_labels = mem_layer("Point", "sezioni_etichette", crs, [("txt", "s")])
    add_rows(sec_labels, sec_lab_rows)
    style_marker(sec_labels, "0,0,0,0", "0,0,0,0", 0.1)
    label(sec_labels, "txt", 9, bold=True, around=False)
    sec_axis = mem_layer("LineString", "sezioni_asse", crs, [])
    add_rows(sec_axis, sec_axis_rows)
    style_line(sec_axis, "200,0,0", 0.2, dash=True)
    sec_floor = mem_layer("LineString", "sezioni_pavimento", crs, [("sezione", "s")])
    add_rows(sec_floor, [(QgsGeometry.fromPolylineXY([QgsPointXY(x, y) for x, y in sg]), [lab]) for sg, lab in sec_floor_rows])
    style_line(sec_floor, "150,90,20", 0.35)
    sec_roof = mem_layer("LineString", "sezioni_volta", crs, [("sezione", "s")])
    add_rows(sec_roof, [(QgsGeometry.fromPolylineXY([QgsPointXY(x, y) for x, y in sg]), [lab]) for sg, lab in sec_roof_rows])
    style_line(sec_roof, "0,110,160", 0.35)

    # ---------------- PROFILO LONGITUDINALE
    step("Profilo longitudinale...")
    zm = float(z.min())
    mslab = np.abs(o_all) <= phw
    dec2 = max(1, len(x) // 60000)
    extra_prof = []                                    # profili in piu' disegnati dall'utente
    _gap = float(z.max() - zm) + 6.0
    for k, pl in enumerate(params.get("extra_profiles") or []):
        V2 = np.array(pl["pts"], float)
        if len(V2) < 2:
            continue
        s2, o2, L2 = project_on_polyline(V2, x, y)
        m2 = (np.abs(o2) <= phw) & (s2 >= 0) & (s2 <= L2)
        if m2.sum() < 50:
            step("Profilo %s: troppo pochi punti lungo la linea" % pl.get("lab", k + 2))
            continue
        oy = -(k + 1) * _gap
        extra_prof.append(dict(lab=(pl.get("lab") or "Profilo %d" % (k + 2)), s=s2[m2], z=z[m2] + oy, L=L2, oy=oy,
                               zmin=float(z[m2].min()) + oy, zmax=float(z[m2].max()) + oy))
    _ps = np.concatenate([s_all[mslab]] + [e["s"] for e in extra_prof])
    _pz = np.concatenate([z[mslab]] + [e["z"] for e in extra_prof])
    prof_bg = points_layer("profilo_proiezione", crs, s_all[::dec2], z[::dec2], "190,190,190", 0.12)
    prof_pts = points_layer("profilo_sezione", crs, _ps, _pz, "30,30,30", 0.18)
    prof_shape_g = occupancy_polygon(s_all[mslab], z[mslab], 0.1, min_count=1, smooth=0.1, min_area=0.0)
    prof_shape = mem_layer("Polygon", "profilo_contorno", crs, [])
    if prof_shape_g is not None:
        add_rows(prof_shape, [(prof_shape_g, [])])
    for e in extra_prof:
        _g = occupancy_polygon(e["s"], e["z"], 0.1, min_count=1, smooth=0.1, min_area=0.0)
        if _g is not None:
            add_rows(prof_shape, [(_g, [])])
    style_fill(prof_shape, "225,215,195,255", "0,0,0", 0.25)
    pfl, prf = envelope(s_all[mslab], z[mslab], 0.25, min_count=3, min_height=0.3)
    for e in extra_prof:
        _f, _r = envelope(e["s"], e["z"], 0.25, min_count=3, min_height=0.3)
        pfl, prf = list(pfl) + list(_f), list(prf) + list(_r)
    prof_floor = lines_layer("profilo_pavimento", crs, pfl, "pavimento")
    style_line(prof_floor, "150,90,20", 0.35)
    prof_roof = lines_layer("profilo_volta", crs, prf, "volta")
    style_line(prof_roof, "0,110,160", 0.35)

    # dati del profilo campionati ogni 0,5 m (passo 3.1)
    samples = profile_samples(s_all, o_all, z, L, step=0.5, half_width=0.75, thickness=0.30)
    prof_data = mem_layer("Point", "profilo_dati", crs, [("chainage", "d"), ("z_floor", "d"), ("z_roof", "d"), ("z_center", "d"),
                                                         ("width", "d"), ("height", "d"), ("n_points", "i")])
    rows = []
    for rr in samples:
        pp, _d = point_and_dir_at(V, rr["chainage"])
        rows.append((QgsGeometry.fromPointXY(QgsPointXY(pp[0], pp[1])),
                     [rr["chainage"], rr["z_floor"], rr["z_roof"], rr["z_center"], rr["width"], rr["height"], rr["n_points"]]))
    add_rows(prof_data, rows)
    style_marker(prof_data, "255,140,0", "255,140,0", 0.8)
    # quota delle stazioni (pavimento e volta interpolati dal profilo)
    stations.dataProvider().addAttributes([QgsField("z_floor", QVariant.Double), QgsField("z_roof", QVariant.Double)])
    stations.updateFields()
    i_f, i_r = stations.fields().indexOf("z_floor"), stations.fields().indexOf("z_roof")
    chg = {}
    for f in stations.getFeatures():
        chg[f.id()] = {i_f: value_at(samples, f["s_m"], "z_floor"), i_r: value_at(samples, f["s_m"], "z_roof")}
    stations.dataProvider().changeAttributeValues(chg)

    # ingresso e profondita' (passo 3.2)
    ent_req = str(params.get("entrance_station") or "").strip()
    st_feats = sorted(stations.getFeatures(), key=lambda f: f["s_m"])
    import re as _re

    def _resolve_entrance(tok):
        t = tok.strip().lower()
        if t in ("", "inizio", "start"):
            return st_feats[0]
        if t in ("fine", "end", "ultima"):
            return st_feats[-1]
        fnd = [f for f in st_feats if str(f["station_id"]).lower() == t]
        if fnd:
            return fnd[0]
        step("Stazione d'ingresso '%s' non trovata: ignorata" % tok.strip())
        return None
    ent_list = []
    for _tok in (_re.split(r"[,;]", ent_req) if ent_req else [""]):
        _f = _resolve_entrance(_tok)
        if _f is not None and all(_f.id() != g.id() for g in ent_list):
            ent_list.append(_f)
    acc_xy = prep.get("access")
    p_ents = prep.get("entrances") or []
    ent_pos = None
    if p_ents:
        # ingressi indicati (disegnati o trovati in automatico): per ognuno la stazione piu' vicina fa da riferimento
        ent_list, ent_pos = [], []
        for e in p_ents:
            _f = min(st_feats, key=lambda f: f.geometry().asPoint().distance(QgsPointXY(e["xy"][0], e["xy"][1])))
            ent_list.append(_f)
            ent_pos.append(e)
        acc_xy = p_ents[0]["xy"]
    elif acc_xy is not None:
        ent_list = [min(st_feats, key=lambda f: f.geometry().asPoint().distance(QgsPointXY(acc_xy[0], acc_xy[1])))]
    if not ent_list:
        ent_list = [st_feats[0]]
    ent_f = ent_list[0]
    ent_names = [str(f["station_id"]) for f in ent_list]
    ent_name = str(ent_f["station_id"])
    ent_s = float(ent_f["s_m"])
    floors = [rr["z_floor"] for rr in samples if rr["z_floor"] is not None]
    ent_z = value_at(samples, ent_s, "z_floor")
    if prep.get("access_z") is not None:
        ent_z = float(prep["access_z"])
    elif ent_pos and ent_pos[0].get("z") is not None:
        ent_z = float(ent_pos[0]["z"])
    if ent_z is None:
        ent_z = float(np.median(z))
    depth = max(0.0, ent_z - min(floors)) if floors else 0.0
    rise = max(0.0, max(floors) - ent_z) if floors else 0.0
    # coordinate GPS dell'ingresso prese col telefono
    gps = params.get("entrance_latlon")
    gps_alt = params.get("entrance_alt")
    gps_line, scarto, gE, gN = "", None, None, None
    ent_pt = ent_f.geometry().asPoint() if acc_xy is None else QgsPointXY(acc_xy[0], acc_xy[1])
    entrance_gps = mem_layer("Point", "ingresso_gps", crs, [("nome", "s"), ("lat", "d"), ("lon", "d"), ("quota_gps", "d"), ("scarto_m", "d")])
    if gps:
        from qgis.core import QgsCoordinateTransform
        trf = QgsCoordinateTransform(QgsCoordinateReferenceSystem("EPSG:4326"), crs, QgsProject.instance())
        gp = trf.transform(QgsPointXY(gps[1], gps[0]))
        gE, gN = gp.x(), gp.y()
        scarto = math.hypot(gE - ent_pt.x(), gN - ent_pt.y())
        add_rows(entrance_gps, [(QgsGeometry.fromPointXY(QgsPointXY(gE, gN)),
                                 ["Ingresso GPS", gps[0], gps[1], gps_alt, round(scarto, 2)])])
        if scarto > 100:
            step("Attenzione: il punto GPS dista %.0f m dall'ingresso del rilievo: controlla coordinate e sistema di riferimento." % scarto)

    # coordinate dell'ingresso ricavate dal LAS (se il rilievo e' georeferenziato, es. Scaniverse)
    las_ll = None
    try:
        if crs.isValid() and not crs.isGeographic() and (abs(ent_pt.x()) > 10000 or abs(ent_pt.y()) > 10000):
            from qgis.core import QgsCoordinateTransform
            _g = QgsCoordinateTransform(crs, QgsCoordinateReferenceSystem("EPSG:4326"), QgsProject.instance()).transform(ent_pt)
            if -90 <= _g.y() <= 90 and -180 <= _g.x() <= 180:
                las_ll = (_g.y(), _g.x())
    except Exception:
        las_ll = None

    def _fmt_ll(ll):
        return "%.6f %s, %.6f %s" % (abs(ll[0]), "N" if ll[0] >= 0 else "S", abs(ll[1]), "E" if ll[1] >= 0 else "O")
    _parts = []
    if las_ll:
        _parts.append("Ingresso WGS84 (dal LAS): " + _fmt_ll(las_ll))
    if gps:
        _parts.append(("GPS: scarto %.1f m" % scarto) if las_ll else ("Ingresso GPS (WGS84): " + _fmt_ll(gps)))
    if gps_alt is not None:
        _parts.append("%.0f m s.l.m." % gps_alt)
    gps_line = "  -  ".join(_parts)
    style_marker(entrance_gps, "220,0,0", "220,0,0", 2.6, shape="cross2")
    entrance = mem_layer("Point", "ingresso", crs, [("nome", "s"), ("stazione", "s"), ("quota", "d"), ("lat_las", "d"), ("lon_las", "d"), ("lat", "d"), ("lon", "d"),
                                                  ("est_gps", "d"), ("nord_gps", "d"), ("quota_gps", "d"), ("scarto_m", "d")])
    add_rows(entrance, [(QgsGeometry.fromPointXY(QgsPointXY(ent_pt)),
                         ["Accesso" if ipogeo else "Ingresso", ent_name, round(ent_z, 2), round(las_ll[0], 7) if las_ll else None, round(las_ll[1], 7) if las_ll else None,
                          gps[0] if gps else None, gps[1] if gps else None,
                          round(gE, 3) if gE is not None else None, round(gN, 3) if gN is not None else None,
                          gps_alt, round(scarto, 2) if scarto is not None else None])])
    style_marker(entrance, "255,255,255", "0,0,0", 3.2, shape="triangle")
    if ipogeo:
        style_access(entrance)
    label(entrance, "nome", 8, bold=True, around=True, dist=1.5, color="#1030d0" if ipogeo else "#000000")
    if len(ent_list) > 1:
        from qgis.core import QgsCoordinateTransform as _QCT
        _to_ll = _QCT(crs, QgsCoordinateReferenceSystem("EPSG:4326"), QgsProject.instance())
        _rows = []
        for k, ef in enumerate(ent_list[1:], start=2):
            _p = ef.geometry().asPoint()
            _zq = value_at(samples, float(ef["s_m"]), "z_floor")
            if ent_pos:
                _p = QgsPointXY(ent_pos[k - 1]["xy"][0], ent_pos[k - 1]["xy"][1])
                if ent_pos[k - 1].get("z") is not None:
                    _zq = ent_pos[k - 1]["z"]
            _ll = _to_ll.transform(QgsPointXY(_p)) if las_ll else None
            _rows.append((QgsGeometry.fromPointXY(QgsPointXY(_p)), ["Ingresso %d" % k, str(ef["station_id"]), round(_zq, 2) if _zq is not None else None,
                                                       round(_ll.y(), 7) if _ll else None, round(_ll.x(), 7) if _ll else None,
                                                       None, None, None, None, None, None]))
        add_rows(entrance, _rows)
        _i0 = entrance.fields().indexOf("nome")
        _first = sorted(entrance.getFeatures(), key=lambda f: f.id())[0]
        entrance.dataProvider().changeAttributeValues({_first.id(): {_i0: "Ingresso 1"}})
    other_entr = [(str(f["station_id"]), value_at(samples, float(f["s_m"]), "z_floor")) for f in ent_list[1:]]
    if ent_pos:
        other_entr = [("%d" % (k + 2), (e.get("z") if e.get("z") is not None else value_at(samples, float(f["s_m"]), "z_floor")))
                      for k, (f, e) in enumerate(zip(ent_list[1:], ent_pos[1:]))]

    # contorno aperto in corrispondenza degli ingressi alle estremita' dell'asse
    from qgis.core import QgsWkbTypes as _WKB
    ent_style = (params.get("entrance_style") or "tratteggiata").lower()
    _bnd = QgsGeometry(outline.constGet().boundary())
    walls_g = QgsGeometry(_bnd)
    gap_lines = []
    _Vn = np.asarray(V, dtype=float)
    # negli ipogei l'accesso e' una scala o una porta dentro il contorno: niente apertura del contorno
    for ef in ([] if (ipogeo or ent_pos) else ent_list):
        if ef.id() == st_feats[0].id():
            _dv = _Vn[0] - _Vn[1]
        elif ef.id() == st_feats[-1].id():
            _dv = _Vn[-1] - _Vn[-2]
        else:
            continue
        _nv = float(np.linalg.norm(_dv))
        if _nv == 0:
            continue
        _dv = _dv / _nv
        _p = ef.geometry().asPoint()
        _ray = QgsGeometry.fromPolylineXY([QgsPointXY(_p.x() - _dv[0] * 0.5, _p.y() - _dv[1] * 0.5),
                                           QgsPointXY(_p.x() + _dv[0] * 40.0, _p.y() + _dv[1] * 40.0)])
        _int = _ray.intersection(_bnd)
        _pts = []
        if not _int.isEmpty() and _int.type() == _WKB.PointGeometry:
            _pts = [QgsPointXY(q) for q in (_int.asMultiPoint() if _int.isMultipart() else [_int.asPoint()])]
        _pts = [q for q in _pts if (q.x() - _p.x()) * _dv[0] + (q.y() - _p.y()) * _dv[1] > -0.5]
        if _pts:
            _po = min(_pts, key=lambda q: (q.x() - _p.x()) * _dv[0] + (q.y() - _p.y()) * _dv[1])
        else:
            _po = _bnd.nearestPoint(QgsGeometry.fromPointXY(_p)).asPoint()
        _s0 = float(ef["s_m"])
        _lw = value_at(samples, _s0, "left") or 1.0
        _rw = value_at(samples, _s0, "right") or 1.0
        _r = min(6.0, max(0.6, (_lw + _rw) / 2.0 * 0.8))
        _circ = QgsGeometry.fromPointXY(QgsPointXY(_po)).buffer(_r, 32)
        _cross = _bnd.intersection(QgsGeometry(_circ.constGet().boundary()))
        walls_g = walls_g.difference(_circ)
        _cp = []
        if not _cross.isEmpty() and _cross.type() == _WKB.PointGeometry:
            _cp = [QgsPointXY(q) for q in (_cross.asMultiPoint() if _cross.isMultipart() else [_cross.asPoint()])]
        if len(_cp) >= 2:
            _a, _b = max(((a, b) for i, a in enumerate(_cp) for b in _cp[i + 1:]), key=lambda ab: ab[0].distance(ab[1]))
            gap_lines.append(QgsGeometry.fromPolylineXY([_a, _b]))
    # ingressi trovati tra grotta ed esterno: apertura lungo la soglia (corda tra i due estremi, agganciata al contorno)
    for e in (ent_pos or []):
        if ipogeo or not e.get("chord"):
            continue
        if e.get("gate"):
            # tratti del contorno che toccano l'esterno: li', invece della parete, la linea d'ingresso
            _gz = QgsGeometry.unaryUnion([QgsGeometry.fromPointXY(QgsPointXY(gx, gy)).buffer(0.45, 4) for gx, gy in e["gate"]])
            _op = _bnd.intersection(_gz)
            if not _op.isEmpty() and _op.type() == _WKB.LineGeometry:
                _op = _op.mergeLines()
                walls_g = walls_g.difference(_gz)
                for _pl in (_op.asMultiPolyline() if _op.isMultipart() else [_op.asPolyline()]):
                    if len(_pl) >= 2 and QgsGeometry.fromPolylineXY(_pl).length() >= 0.5:
                        gap_lines.append(QgsGeometry.fromPolylineXY(_pl))
                continue
        _a, _b = QgsPointXY(*e["chord"][0]), QgsPointXY(*e["chord"][1])
        _a = _bnd.nearestPoint(QgsGeometry.fromPointXY(_a)).asPoint()
        _b = _bnd.nearestPoint(QgsGeometry.fromPointXY(_b)).asPoint()
        if _a.distance(_b) < 0.3:
            continue
        _ch = QgsGeometry.fromPolylineXY([_a, _b])
        walls_g = walls_g.difference(_ch.buffer(0.35, 8))
        gap_lines.append(_ch)
    walls = mem_layer("LineString", "pareti", crs, [])
    _parts = walls_g.asMultiPolyline() if walls_g.isMultipart() else [walls_g.asPolyline()]
    add_rows(walls, [(QgsGeometry.fromPolylineXY(pl), []) for pl in _parts if len(pl) >= 2])
    style_line(walls, "0,0,0", 0.6)
    ent_lines = mem_layer("LineString", "ingressi_limite", crs, [])
    if ent_style.startswith("tratt"):
        add_rows(ent_lines, [(g, []) for g in gap_lines])
    style_line(ent_lines, "0,0,0", 0.4, dash=True)
    stations.dataProvider().addAttributes([QgsField("quota_rel", QVariant.Double)])
    stations.updateFields()
    i_q = stations.fields().indexOf("quota_rel")
    chg = {}
    for f in stations.getFeatures():
        zf = f["z_floor"]
        chg[f.id()] = {i_q: (round(float(zf) - ent_z, 2) if zf is not None and str(zf) != "NULL" else None)}
    stations.dataProvider().changeAttributeValues(chg)
    if gps_alt is not None:
        stations.dataProvider().addAttributes([QgsField("quota_slm", QVariant.Double)])
        stations.updateFields()
        i_s = stations.fields().indexOf("quota_slm")
        chg = {}
        for f in stations.getFeatures():
            qr = f["quota_rel"]
            chg[f.id()] = {i_s: (round(float(qr) + gps_alt, 2) if qr is not None and str(qr) != "NULL" else None)}
        stations.dataProvider().changeAttributeValues(chg)
    # ipogei: quote del piano di calpestio rispetto all'accesso (punto piu' basso e una per sezione) e resto del rilievo
    spots = context_l = None
    if ipogeo:
        spots = mem_layer("Point", "quote_calpestio", crs, [("quota_rel", "d"), ("quota", "d"), ("txt", "s")])
        # il punto piu' basso e poi una quota per ogni stazione (cosi' ogni ramo ha la sua)
        _cand = []
        _fl = [(rr["chainage"], rr["z_floor"]) for rr in samples if rr["z_floor"] is not None]
        if _fl:
            _cand.append(min(_fl, key=lambda a: a[1]))
        for f in st_feats:
            _zf = value_at(samples, float(f["s_m"]), "z_floor")
            if _zf is not None:
                _cand.append((float(f["s_m"]), _zf))
        _rows, _used = [], []
        for s0, zf in _cand:
            if any(abs(s0 - u) < 1.5 for u in _used):
                continue
            _used.append(s0)
            pp, _d = point_and_dir_at(V, s0)
            _rows.append((QgsGeometry.fromPointXY(QgsPointXY(pp[0], pp[1])), [round(zf - ent_z, 2), round(zf, 2), "%+.2f" % (zf - ent_z)]))
        add_rows(spots, _rows)
        style_spot(spots)
        label(spots, "txt", 7, around=True, dist=0.6, color="#1030d0")
    if prep.get("context") is not None:
        cx_, cy_, cz_ = prep["context"]
        context_l = points_layer("resto_rilievo", crs, cx_, cy_, "205,205,205", 0.10)

    zmax = float(z.max())
    prof_st = mem_layer("LineString", "profilo_stazioni", crs, [("station_id", "s")])
    prof_stl = mem_layer("Point", "profilo_stazioni_lab", crs, [("station_id", "s")])
    r1, r2 = [], []
    for f in stations.getFeatures():
        s0 = f["s_m"]
        r1.append((QgsGeometry.fromPolylineXY([QgsPointXY(s0, zmax + 0.4), QgsPointXY(s0, zmax + 0.9)]), [f["station_id"]]))
        r2.append((QgsGeometry.fromPointXY(QgsPointXY(s0, zmax + 1.0)), [f["station_id"]]))
    for sc in sections:
        r1.append((QgsGeometry.fromPolylineXY([QgsPointXY(sc["s"], zm - 0.3), QgsPointXY(sc["s"], zmax + 0.3)]), [sc["lab"]]))
    for e in extra_prof:
        r2.append((QgsGeometry.fromPointXY(QgsPointXY(0.0, e["zmax"] + 1.0)), ["%s (sviluppo %.1f m)" % (e["lab"], e["L"])]))
    add_rows(prof_st, r1)
    add_rows(prof_stl, r2)
    style_line(prof_st, "200,0,0", 0.2, dash=True)
    style_marker(prof_stl, "0,0,0,0", "0,0,0,0", 0.1)
    label(prof_stl, "station_id", 6, around=False, color="#b00000")

    # ---------------- PROGETTO QGIS
    step("Composizione tavole...")
    proj = QgsProject.instance()
    root = proj.layerTreeRoot()
    gname = "SpeleoMap - %s" % name
    old = root.findGroup(gname)
    if old:
        for lyr in [n.layer() for n in old.findLayers()]:
            if lyr:
                proj.removeMapLayer(lyr.id())
        root.removeChildNode(old)
    # salva tutti i layer nel GeoPackage (il progetto si riapre completo)
    base = slug(name)
    gpkg = os.path.join(out_dir, "%s_speleomap.gpkg" % base)
    (tr_lab, stations, traces, axis, outline_l, plan_pts,
     prof_stl, prof_st, prof_pts, prof_shape, prof_bg,
     sec_labels, sec_axis, sec_points, sec_shape, sec_floor, sec_roof, prof_floor, prof_roof, prof_data, entrance, entrance_gps, walls, ent_lines) = persist_layers(gpkg, [
        (tr_lab, "pianta_etichette_tracce"), (stations, "stations_ref"), (traces, "section_traces"),
        (axis, "axis"), (outline_l, "outline"), (plan_pts, "pianta_punti"),
        (prof_stl, "profilo_stazioni_lab"), (prof_st, "profilo_stazioni"), (prof_pts, "profilo_sezione"),
        (prof_shape, "profilo_contorno"), (prof_bg, "profilo_proiezione"),
        (sec_labels, "sezioni_etichette"), (sec_axis, "sezioni_asse"), (sec_points, "sezioni_punti"),
        (sec_shape, "sezioni_profilo"), (sec_floor, "sezioni_pavimento"), (sec_roof, "sezioni_volta"),
        (prof_floor, "profilo_pavimento"), (prof_roof, "profilo_volta"), (prof_data, "profilo_dati"), (entrance, "ingresso"), (entrance_gps, "ingresso_gps"), (walls, "pareti"), (ent_lines, "ingressi_limite")])
    step("Layer salvati in %s" % gpkg)

    grp = root.insertGroup(0, gname)
    g_plan = grp.addGroup("Pianta")
    g_prof = grp.addGroup("Profilo longitudinale (coord. locali)")
    g_sec = grp.addGroup("Sezioni (coord. locali)")
    for lyr, gg in [(tr_lab, g_plan), (stations, g_plan), (traces, g_plan), (axis, g_plan), (outline_l, g_plan), (plan_pts, g_plan),
                    (prof_stl, g_prof), (prof_st, g_prof), (prof_pts, g_prof), (prof_shape, g_prof), (prof_bg, g_prof),
                    (sec_labels, g_sec), (sec_axis, g_sec), (sec_floor, g_sec), (sec_roof, g_sec), (sec_points, g_sec), (sec_shape, g_sec),
                    (prof_floor, g_prof), (prof_roof, g_prof), (prof_data, g_plan), (entrance, g_plan), (entrance_gps, g_plan), (walls, g_plan), (ent_lines, g_plan)]:
        proj.addMapLayer(lyr, False)
        gg.addLayer(lyr)
    gal_raw_l = None
    if ipogeo or context_l is not None:
        _ex = [(l, t) for l, t in ((spots, "quote_calpestio"), (context_l, "resto_rilievo")) if l is not None]
        _sv = persist_layers(gpkg, _ex)
        for (l0, t), l1 in zip(_ex, _sv):
            if t == "quote_calpestio":
                spots = l1
            else:
                context_l = l1
            proj.addMapLayer(l1, False)
            g_plan.addLayer(l1)
        if prep.get("gallery_raw") is not None:
            try:
                from .smg_ipogei import gallery_layer_raw
                gal_raw_l = gallery_layer_raw(crs, prep["gallery_raw"], gpkg)
                gal_raw_l.setRenderer(QgsSingleSymbolRenderer(QgsFillSymbol.createSimple(
                    {"color": "255,200,0,50", "outline_color": "230,120,0", "outline_width": "0.5"})))
                g_raw = grp.addGroup("Da modificare (sistema del LAS, prima della correzione)")
                proj.addMapLayer(gal_raw_l, False)
                g_raw.addLayer(gal_raw_l)
            except Exception as e:
                step("Limite gallerie non salvato (%s)" % e)
    g_prof.setItemVisibilityChecked(False)
    _n = root.findLayer(prof_data.id())
    if _n:
        _n.setItemVisibilityChecked(False)
    g_sec.setItemVisibilityChecked(False)

    lm = proj.layoutManager()
    lname = "SpeleoMap - %s" % name
    for l in lm.layouts():
        if l.name() == lname:
            lm.removeLayout(l)
    layout = QgsPrintLayout(proj)
    layout.initializeDefaults()
    layout.pageCollection().page(0).setPageSize("A3", QgsLayoutItemPage.Landscape)
    layout.setName(lname)
    lm.addLayout(layout)

    dz = float(z.max() - z.min())
    info_lines = [
        "Localita': %s  -  Data rilievo: %s" % (params.get("location") or "-", params.get("survey_date") or "-"),
        "Rilevatori: %s" % (params.get("authors") or "-"),
        "Elaborazione grafica: %s" % (params.get("drafter") or "-"),
        "Strumenti: %s  -  Grado: %s" % (params.get("instruments") or "Scansione LiDAR", params.get("grade") or "n.d."),
        "Dati: %s punti - %s" % ("{:,}".format(n_all).replace(",", "."), os.path.basename(las)),
        "Sistema di riferimento: %s %s" % (crs.authid(), crs.description()),
    ]
    if ipogeo or not prep["shift"].describe() == "":
        _ll_acc = None
        try:
            from qgis.core import QgsCoordinateTransform
            _g = QgsCoordinateTransform(crs, QgsCoordinateReferenceSystem("EPSG:4326"), QgsProject.instance()).transform(QgsPointXY(ent_pt))
            _ll_acc = (_g.y(), _g.x())
        except Exception:
            pass
        _lbl = "Accesso" if ipogeo else "Ingresso"
        if _ll_acc:
            info_lines.append("%s: %s (WGS84%s)" % (_lbl, _fmt_ll(_ll_acc), ", posizione corretta" if prep["shift"].describe() else ""))
    elif las_ll:
        info_lines.append("Ingresso: %s (WGS84, dal LAS)" % _fmt_ll(las_ll))
    elif gps:
        info_lines.append("Ingresso: %s (WGS84, GPS)" % _fmt_ll(gps))
    _q = "Quota ingresso: %.1f m (rilievo)" % ent_z
    if ipogeo:
        _q = "Quota di accesso: %.2f m %s" % (ent_z, "s.l.m. (inserita)" if prep.get("quota_src") == "inserita" else "(quota del rilievo, non assoluta)")
    elif prep.get("quota_src") == "inserita":
        _q = "Quota ingresso: %.2f m s.l.m. (inserita)" % ent_z
    if gps_alt is not None:
        _q += "  -  %.0f m s.l.m. (GPS)" % gps_alt
    if gps and las_ll:
        _q += "  -  scarto GPS %.1f m" % scarto
    for _nm, _zz in other_entr:
        _q += "  -  ingresso %s: %s m" % (_nm, ("%.1f" % _zz) if _zz is not None else "n.d.")
    info_lines.append(_q)
    info_lines.append("Sviluppo: %.1f m  -  Profondita': -%.1f m  -  Risalita: +%.1f m" % (L, depth, rise))
    info_lines.append("QGIS %s / SpeleoMap Generator 1.1 - %s" % (Qgis.version().split("-")[0], datetime.date.today().strftime("%d/%m/%Y")))
    info = "\n".join(info_lines)
    n_tav = 5 if (params.get("terrain") or {}).get("enable") else 4
    meta = dict(group=params.get("group") or "Gruppo speleologico", logo=params.get("logo") or "",
                oggetto=params.get("oggetto") or "Pianta - Profilo longitudinale - Sezioni")
    nord = "Nord di reticolo (%s)." % crs.authid()

    plan_layers = [entrance_gps, entrance, tr_lab, stations, traces, axis, ent_lines, walls, plan_pts]
    ext_plan = QgsRectangle(outline.boundingBox())
    ext_plan.combineExtentWith(traces.extent())
    plan_leg = [(entrance, "Ingresso" if len(ent_list) == 1 else "Ingressi")]
    if entrance_gps.featureCount() > 0:
        plan_leg.append((entrance_gps, "Ingresso GPS (telefono)"))
    plan_leg += [(stations, "Stazioni ogni %.1f m" % st_step), (axis, "Asse della cavita'"),
                 (traces, "Tracce delle sezioni"), (walls, "Contorno (pareti)")]
    if ent_lines.featureCount() > 0:
        plan_leg.append((ent_lines, "Limite dell'ingresso"))
    plan_note = "Contorno ricavato dalla nuvola di punti in proiezione orizzontale.\n" + nord
    if ipogeo:
        plan_leg[0] = (entrance, "Accesso")
        if spots is not None:
            plan_layers.insert(0, spots)
            plan_leg.insert(1, (spots, "Quote del piano di calpestio rispetto all'accesso"))
        if context_l is not None:
            plan_layers.append(context_l)
        plan_note = ("Planimetria delle gallerie dalla nuvola di punti; in grigio il resto del rilievo.\n" +
                     "; ".join(prep.get("notes") or []) + ("\n" if prep.get("notes") else "") + nord)
    else:
        if context_l is not None:
            plan_layers.append(context_l)
    if not ipogeo and prep.get("notes"):
        plan_note = "Contorno ricavato dalla nuvola di punti in proiezione orizzontale.\n" + "; ".join(prep["notes"]) + ". " + nord
    mp_plan, sc_plan = add_page(layout, 0, crs, plan_layers, ext_plan, None, name, "PIANTA", info,
                          plan_note,
                          grid=True, north=True, meta=dict(meta, tav="1 di %d" % n_tav), legend=plan_leg)

    # ---------------- DEM (scaricato una sola volta: serve al profilo e all'inquadramento)
    tparams = params.get("terrain") or {}
    _dem_cache = {}

    def _get_dem():
        if "done" in _dem_cache:
            return _dem_cache.get("res"), _dem_cache.get("err")
        _dem_cache["done"] = True
        dsrc = tparams.get("dem", "auto")
        if dsrc is True:
            dsrc = "auto"
        try:
            from .smg_terrain import copernicus_dem, tinitaly_dem, file_dem, in_italy
            _ll = las_ll or gps
            _ita = bool(_ll and in_italy(_ll[0], _ll[1]))
            _lp = (tparams.get("lidar_path") or "").strip()
            if dsrc == "auto":
                dsrc = ("lidar" if _lp else "tinitaly") if _ita else "copernicus"
            if not dsrc:
                return None, None
            _bb = QgsRectangle(outline.boundingBox())
            _bb.grow(float(tparams.get("buffer") or 150.0))
            # area effettiva della tavola di inquadramento (stessa scala che usera' il layout) + 5%
            _fw = PAGE_W - 2 * MARGIN - 2 * GRID_PAD
            _fh = PAGE_H - 2 * MARGIN - FOOTER_H - 2 * GRID_PAD
            _sc = choose_scale(_bb.width() * 1.08, _bb.height() * 1.08, _fw, _fh)
            _c = _bb.center()
            _hw, _hh = _fw * _sc / 2000.0 * 1.05, _fh * _sc / 2000.0 * 1.05
            dem_ext = QgsRectangle(_c.x() - _hw, _c.y() - _hh, _c.x() + _hw, _c.y() + _hh)
            dem_tif = os.path.join(out_dir, "%s_dem.tif" % base)
            # DEM locale dell'utente (cartella ~/SpeleoMap/DEM) che copre l'area: ha la precedenza in automatico
            _loc = None
            if tparams.get("dem", "auto") in ("auto", True) and dsrc != "lidar":
                try:
                    from .smg_terrain import local_dem
                    _loc = local_dem(dem_ext, crs)
                except Exception:
                    _loc = None
                if _loc:
                    dsrc = "locale"
                    step("DEM locale trovato: %s" % os.path.basename(_loc))
            # LiDAR nazionale (MASE): quali tavole coprono l'area, scritto in un file accanto alle tavole
            if _ita:
                try:
                    from .smg_terrain import lidar_index, lidar_report
                    _tiles = lidar_index(dem_ext, crs)
                    lidar_report(_tiles, os.path.join(out_dir, "%s_lidar_tavole.txt" % base), name)
                    _dem_cache["lidar_tiles"] = _tiles
                    if _tiles and dsrc != "lidar":
                        step("LiDAR %.0f m disponibile per quest'area (%d tavole): elenco in %s_lidar_tavole.txt" % (_tiles[0]["res"], len(_tiles), base))
                except Exception as _e:
                    step("Quadro d'unione LiDAR non consultabile (%s)" % _e)
            if dsrc == "lidar":
                try:
                    from .smg_terrain import lidar_dem
                    _fill = None
                    try:
                        _fill, _r = tinitaly_dem(dem_ext, crs, os.path.join(out_dir, "%s_dem_tinitaly.tif" % base))
                    except Exception:
                        _fill = None
                    dem_tif, (zmn, zmx), _cov = lidar_dem(_lp, dem_ext, crs, dem_tif, res=1.0, fill=_fill)
                    _dem_cache["lidar_cover"] = _cov
                    step("DTM LiDAR 1 m: copre il %.0f%% dell'area%s" % (_cov * 100, ", il resto da TINITALY" if _cov < 0.999 and _fill else ""))
                except Exception as _e:
                    step("LiDAR non usato (%s): uso TINITALY" % _e)
                    dsrc = "tinitaly"
            if dsrc == "locale":
                dem_tif, (zmn, zmx) = file_dem(_loc, dem_ext, crs, dem_tif)
            if dsrc == "file":
                dem_tif, (zmn, zmx) = file_dem(tparams.get("dem_file") or "", dem_ext, crs, dem_tif)
            elif dsrc == "tinitaly":
                try:
                    dem_tif, (zmn, zmx) = tinitaly_dem(dem_ext, crs, dem_tif)
                except Exception as _e:
                    step("TINITALY non disponibile (%s): uso Copernicus" % _e)
                    dsrc = "copernicus"
            if dsrc == "copernicus":
                dem_tif, (zmn, zmx) = copernicus_dem(dem_ext, crs, dem_tif)
            _dem_cache["res"] = (dsrc, dem_tif, zmn, zmx)
        except Exception as e:
            _dem_cache["err"] = str(e)
        return _dem_cache.get("res"), _dem_cache.get("err")

    # linea del terreno sopra la grotta nel profilo (passo 6.5)
    prof_terr, terr_note, terr_rows, terr_info = None, "", [], {}
    if tparams.get("enable") and tparams.get("profile_line", True):
        step("Linea del terreno nel profilo...")
        _res, _err = _get_dem()
        if _res is None:
            if _err:
                step("Linea del terreno: DEM non disponibile (%s)" % _err)
        else:
            try:
                from qgis.core import QgsRasterLayer
                _dsrc, _dtif = _res[0], _res[1]
                _dl = QgsRasterLayer(_dtif, "dem")
                _dp = _dl.dataProvider()

                _dx0, _dy1 = _dl.extent().xMinimum(), _dl.extent().yMaximum()
                _rx, _ry = _dl.rasterUnitsPerPixelX(), _dl.rasterUnitsPerPixelY()

                def _zpx(px, py):
                    v, ok = _dp.sample(QgsPointXY(px, py), 1)
                    return float(v) if (ok and v == v) else None

                def _zdem(px, py):
                    # interpolazione bilineare tra i 4 centri cella piu' vicini
                    fx = (px - _dx0) / _rx - 0.5
                    fy = (_dy1 - py) / _ry - 0.5
                    i0, j0 = math.floor(fx), math.floor(fy)
                    tx, ty = fx - i0, fy - j0
                    vv = []
                    for dj in (0, 1):
                        for di in (0, 1):
                            v = _zpx(_dx0 + (i0 + di + 0.5) * _rx, _dy1 - (j0 + dj + 0.5) * _ry)
                            if v is None:
                                return _zpx(px, py)
                            vv.append(v)
                    return (vv[0] * (1 - tx) + vv[1] * tx) * (1 - ty) + (vv[2] * (1 - tx) + vv[3] * tx) * ty
                _ze = _zdem(ent_pt.x(), ent_pt.y())
                _align = bool(tparams.get("align", True)) and _ze is not None
                t_off = (ent_z - _ze) if _align else 0.0
                tpts = []
                nst = max(2, int(math.ceil(L / 0.5)) + 1)
                for k in range(nst):
                    s_k = min(L, k * 0.5)
                    pk, _d = point_and_dir_at(V, s_k)
                    zd = _zdem(pk[0], pk[1])
                    if zd is None:
                        continue
                    zt = zd + t_off
                    zr = value_at(samples, s_k, "z_roof")
                    terr_rows.append((s_k, pk[0], pk[1], zd, zt, zr, (zt - zr) if zr is not None else None))
                    tpts.append(QgsPointXY(s_k, zt))
                if len(tpts) > 1:
                    prof_terr = mem_layer("LineString", "profilo_terreno", crs, [("fonte", "s"), ("correzione_m", "d")])
                    add_rows(prof_terr, [(QgsGeometry.fromPolylineXY(tpts), [_dsrc, round(t_off, 2)])])
                    style_line(prof_terr, "40,120,30", 0.5)
                    prof_terr = persist_layers(gpkg, [(prof_terr, "profilo_terreno")])[0]
                    proj.addMapLayer(prof_terr, False)
                    g_prof.addLayer(prof_terr)
                    _nm = {"tinitaly": "TINITALY 10 m (INGV)", "copernicus": "Copernicus GLO-30 (30 m, con alberi ed edifici)",
                           "file": "DEM da file", "lidar": "DTM LiDAR 1 m (MASE)", "locale": "DEM locale"}.get(_dsrc, "DEM")
                    # vicino agli ingressi il terreno sta per forza sotto la volta (e' l'imbocco): esclusi 3 m
                    _ent_s = [float(f2["s_m"]) for f2 in ent_list]
                    covers = [r[6] for r in terr_rows if r[6] is not None and min(abs(r[0] - es) for es in _ent_s) > 3.0]
                    terr_note = "Terreno: %s" % _nm
                    if _align:
                        terr_note += ", allineato all'ingresso %s (%+.1f m)" % (ent_name, t_off)
                    _res2 = []
                    for _f2 in ent_list[1:]:
                        _p2 = _f2.geometry().asPoint()
                        _zd2 = _zdem(_p2.x(), _p2.y())
                        _zf2 = value_at(samples, float(_f2["s_m"]), "z_floor")
                        if _zd2 is not None and _zf2 is not None:
                            _res2.append((str(_f2["station_id"]), _zd2 + t_off - _zf2))
                    if _res2:
                        terr_note += "; scarto a " + ", ".join("%s %+.1f m" % rr for rr in _res2)
                    if covers:
                        if min(covers) < 0:
                            _neg = sum(0.5 for cv in covers if cv < 0)
                            terr_note += (". Attenzione: per %.1f m il DEM passa sotto la volta (DEM poco preciso qui)" % _neg)
                        else:
                            terr_note += ". Roccia sopra la volta: %.1f - %.1f m" % (min(covers), max(covers))
                    terr_info = dict(source=_dsrc, offset=round(t_off, 2), dem_entrance=(round(_ze, 2) if _ze is not None else None),
                                     cover_min=(round(min(covers), 2) if covers else None), cover_max=(round(max(covers), 2) if covers else None),
                                     residuals=[(a, round(b, 2)) for a, b in _res2])
            except Exception as e:
                step("Linea del terreno non disegnata (%s)" % e)
                prof_terr = None

    prof_layers = [prof_stl, prof_st, prof_pts, prof_shape, prof_bg]
    ext_prof = QgsRectangle(0, zm - 0.5, L, zmax + 1.5)
    for e in extra_prof:
        ext_prof.combineExtentWith(QgsRectangle(0, e["zmin"] - 0.5, e["L"], e["zmax"] + 1.5))
    if prof_terr is not None:
        prof_layers.insert(2, prof_terr)
        _te = prof_terr.extent()
        ext_prof.combineExtentWith(QgsRectangle(0, _te.yMinimum() - 0.5, L, _te.yMaximum() + 1.0))
    mp_prof, sc_prof = add_page(layout, 1, crs, prof_layers, ext_prof, None, name, "PROFILO LONGITUDINALE", info,
                          "Ascisse: progressiva lungo l'asse (m). Ordinate: quota (m).\n"
                          "Scala orizzontale = scala verticale." + (("\n" + terr_note) if terr_note else ""),
                          grid=True, north=False, meta=dict(meta, tav="2 di %d" % n_tav),
                          legend=[(prof_shape, "Sezione lungo l'asse (fascia +/-%.2f m)" % phw),
                                  (prof_bg, "Proiezione dell'intera cavita'"), (prof_st, "Stazioni e sezioni")] +
                                 ([(prof_terr, "Superficie del terreno (DEM)")] if prof_terr is not None else []))

    sec_layers = [sec_labels, sec_axis, sec_points, sec_shape]
    ext_sec = QgsRectangle(sec_points.extent())
    ext_sec.combineExtentWith(sec_labels.extent())
    mp_sec, sc_sec = add_page(layout, 2, crs, sec_layers, ext_sec, None, name, "SEZIONI TRASVERSALI", info,
                         ("Viste con l'estremo A della traccia a sinistra e A' a destra.\n"
                          "Fascia +/-%.2f m, sezioni sulle tracce disegnate (asse rosso = incrocio con l'asse della grotta)." % th
                          if user_sec else "Viste nel verso della progressiva (da S0 verso la fine).\n"
                          "Fascia +/-%.2f m, una sezione ogni %.1f m." % (th, sec_step)),
                         grid=False, north=False, meta=dict(meta, tav="3 di %d" % n_tav),
                         legend=[(sec_shape, "Sezione trasversale"), (sec_axis, "Posizione dell'asse")])


    # ---------------- TABELLA POLIGONALE (passo 3.3b)
    step("Tabella poligonale...")
    from qgis.core import QgsDistanceArea, QgsCoordinateTransform
    da = QgsDistanceArea()
    da.setSourceCrs(crs, QgsProject.instance().transformContext())
    da.setEllipsoid("EPSG:7030")
    to_ll = QgsCoordinateTransform(crs, QgsCoordinateReferenceSystem("EPSG:4326"), QgsProject.instance())
    stl = sorted(stations.getFeatures(), key=lambda f: f["s_m"])

    def _num(v):
        return None if v is None or str(v) == "NULL" else float(v)
    shots, tot_len, tot_h = [], 0.0, 0.0
    for a, b in zip(stl[:-1], stl[1:]):
        pa, pb = a.geometry().asPoint(), b.geometry().asPoint()
        dh = da.measureLine(QgsPointXY(pa), QgsPointXY(pb))
        az = (math.degrees(da.bearing(QgsPointXY(pa), QgsPointXY(pb))) + 360.0) % 360.0
        za, zb = _num(a["z_floor"]), _num(b["z_floor"])
        if za is not None and zb is not None:
            dz = zb - za
            ln = math.hypot(dh, dz)
            inc = math.degrees(math.atan2(dz, dh))
        else:
            dz, ln, inc = None, dh, None
        note = "INGRESSO" if (str(a["station_id"]) in ent_names or str(b["station_id"]) in ent_names) else ""
        if za is None or zb is None:
            note = (note + " quota n.d.").strip()
        shots.append(dict(da_st=str(a["station_id"]), a_st=str(b["station_id"]), az=az, ln=ln, dh=dh, inc=inc, dz=dz, note=note))
        tot_len += ln
        tot_h += dh

    def _f(v, fmt):
        return "-" if v is None else (fmt % v).replace(".", ",")
    rows = [["%s - %s" % (r["da_st"], r["a_st"]), _f(r["az"], "%.1f"), _f(r["ln"], "%.2f"), _f(r["inc"], "%+.1f"),
             _f(r["dz"], "%+.2f"), r["note"]] for r in shots]
    zf0, zf1 = _num(stl[0]["z_floor"]), _num(stl[-1]["z_floor"])
    rows.append(["Totale", "", _f(tot_len, "%.2f"), "", _f((zf1 - zf0) if (zf0 is not None and zf1 is not None) else None, "%+.2f"),
                 "planimetrico %s m" % _f(tot_h, "%.2f")])
    add_table_page(layout, 3, name, "TABELLA POLIGONALE", info,
                   "Tratte rettilinee tra stazioni consecutive dell'asse (ogni %.1f m di progressiva).\n"
                   "Azimut riferito al nord geografico (ellissoide WGS84), in gradi da 0 a 360.\n"
                   "Lunghezza inclinata tra le quote del pavimento delle stazioni; pendenza in gradi, positiva in salita.\n"
                   "Somma delle tratte %s m; sviluppo misurato lungo l'asse %s m." % (st_step, _f(tot_len, "%.1f"), _f(L, "%.1f")),
                   dict(meta, tav="4 di %d" % n_tav),
                   ["Tratta", "Azimut (°)", "Lunghezza (m)", "Pendenza (°)", "Dislivello (m)", "Note"], rows,
                   [26, 24, 26, 26, 26, 50], bold_last=True)

    # CSV della poligonale e delle stazioni
    import csv
    with open(os.path.join(out_dir, "%s_poligonale.csv" % base), "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh, delimiter=";")
        w.writerow(["da", "a", "azimut_geografico", "lunghezza_m", "pendenza_gradi", "dislivello_m", "distanza_orizzontale_m", "note"])
        for r in shots:
            w.writerow([r["da_st"], r["a_st"], _f(r["az"], "%.1f"), _f(r["ln"], "%.2f"), _f(r["inc"], "%.1f"),
                        _f(r["dz"], "%.2f"), _f(r["dh"], "%.2f"), r["note"]])
    with open(os.path.join(out_dir, "%s_stazioni.csv" % base), "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh, delimiter=";")
        w.writerow(["stazione", "progressiva_m", "est", "nord", "lat", "lon", "quota_pavimento", "quota_rel_ingresso", "quota_slm"])
        for f in stl:
            p = f.geometry().asPoint()
            g = to_ll.transform(QgsPointXY(p))
            qs = f["quota_slm"] if stations.fields().indexOf("quota_slm") >= 0 else None
            w.writerow([f["station_id"], _f(_num(f["s_m"]), "%.2f"), _f(p.x(), "%.3f"), _f(p.y(), "%.3f"), _f(g.y(), "%.7f"), _f(g.x(), "%.7f"),
                        _f(_num(f["z_floor"]), "%.2f"), _f(_num(f["quota_rel"]), "%.2f"), _f(_num(qs), "%.2f")])


    # ---------------- CONTESTO TERRENO: tavola di inquadramento (Fase 6)
    terrain_info = {}
    tparams = params.get("terrain") or {}
    if tparams.get("enable"):
        step("Contesto terreno (basemap, DEM, ombreggiatura, curve)...")
        from .smg_terrain import (basemap_layer, copernicus_dem, hillshade, contour_interval, contours, style_contours,
                                  tinitaly_dem, file_dem, in_italy, DEM_CREDITS, catasto_it_layer)
        from qgis.core import QgsRasterLayer
        tbuf = float(tparams.get("buffer") or 150.0)
        bb = QgsRectangle(outline.boundingBox())
        bb.grow(tbuf)
        credits, t_leg = [], [(entrance, "Accesso" if ipogeo else "Ingresso"), (outline_l, "Contorno della grotta")]
        hs_l = cont_l = None
        _res, _err = _get_dem()
        if _err:
            step("Contesto terreno: DEM non disponibile (%s)" % _err)
        dem_src = _res[0] if _res else None
        if dem_src:
            try:
                dem_src, dem_tif, zmn, zmx = _res
                hs_tif = hillshade(dem_tif, os.path.join(out_dir, "%s_ombreggiatura.tif" % base))
                iv = contour_interval(zmx - zmn)
                cg = contours(dem_tif, os.path.join(out_dir, "%s_curve_livello.gpkg" % base), iv)
                hs_l = QgsRasterLayer(hs_tif, "ombreggiatura")
                try:
                    from qgis.PyQt.QtGui import QPainter
                    hs_l.renderer().setOpacity(0.55 if tparams.get("basemap") else 0.9)
                    if tparams.get("basemap"):
                        hs_l.setBlendMode(QPainter.CompositionMode_Multiply)
                except Exception:
                    pass
                cont_l = QgsVectorLayer(cg + "|layername=curve_livello", "curve di livello", "ogr")
                style_contours(cont_l, iv, on_image=bool(tparams.get("basemap")))
                t_leg.append((cont_l, "Curve di livello ogni %d m" % iv))
                dem_l = QgsRasterLayer(dem_tif, "dem")
                zv, okz = dem_l.dataProvider().sample(QgsPointXY(ent_f.geometry().asPoint()), 1)
                terrain_info = dict(dem_source=dem_src, dem=dem_tif, hillshade=hs_tif, contours=cg, interval=iv,
                                    dem_entrance=(round(zv, 1) if okz else None), dem_range=(round(zmn, 1), round(zmx, 1)))
                credits.append(DEM_CREDITS.get(dem_src, "DEM") +
                               ("; quota DEM all'ingresso %.1f m" % zv if okz else ""))
            except Exception as e:
                step("Contesto terreno: DEM non disponibile (%s)" % e)
        # contorno ben visibile sull'ortofoto (copia del layer, la pianta resta invariata)
        cave_t = QgsVectorLayer(gpkg + "|layername=outline", "contorno (inquadramento)", "ogr")
        cave_t.setRenderer(QgsSingleSymbolRenderer(QgsFillSymbol.createSimple(
            {"color": "255,40,40,60", "outline_style": "no"})))
        walls_t = QgsVectorLayer(gpkg + "|layername=pareti", "pareti (inquadramento)", "ogr")
        walls_t.setRenderer(QgsSingleSymbolRenderer(QgsLineSymbol.createSimple({"line_color": "255,40,40", "line_width": "0.6"})))
        entl_t = QgsVectorLayer(gpkg + "|layername=ingressi_limite", "limite ingressi (inquadramento)", "ogr")
        entl_t.setRenderer(QgsSingleSymbolRenderer(QgsLineSymbol.createSimple({"line_color": "255,40,40", "line_width": "0.7", "use_custom_dash": "1", "customdash": "1;0.7", "customdash_unit": "MM", "capstyle": "flat"})))
        t_leg[1] = (walls_t, "Contorno della grotta")
        if entl_t.featureCount() > 0:
            t_leg.insert(2, (entl_t, "Limite dell'ingresso"))
        # sull'inquadramento il simbolo d'ingresso va appena fuori dall'apertura, cosi' non copre il tratteggio
        entrance_t = entrance
        try:
            chords = [f.geometry() for f in entl_t.getFeatures()]
            cfeat = next(cave_t.getFeatures(), None)
            if chords and cfeat is not None:
                cen = cfeat.geometry().centroid().asPoint()
                off = 0.02 * max(bb.width(), bb.height())
                entrance_t = entrance.materialize(QgsFeatureRequest())
                entrance_t.setName(entrance.name() + " (inquadramento)")
                entrance_t.setRenderer(entrance.renderer().clone())
                if entrance.labeling() is not None:
                    entrance_t.setLabeling(entrance.labeling().clone())
                    entrance_t.setLabelsEnabled(entrance.labelsEnabled())
                entrance_t.startEditing()
                for f in entrance_t.getFeatures():
                    pt = f.geometry().asPoint()
                    best = min(chords, key=lambda g: g.distance(QgsGeometry.fromPointXY(pt)))
                    if best.distance(QgsGeometry.fromPointXY(pt)) > 0.05 * max(bb.width(), bb.height()):
                        continue
                    v = best.asPolyline() if not best.isMultipart() else best.asMultiPolyline()[0]
                    a, b = v[0], v[-1]
                    mx, my = (a.x() + b.x()) / 2.0, (a.y() + b.y()) / 2.0
                    nx, ny = -(b.y() - a.y()), b.x() - a.x()
                    ln = math.hypot(nx, ny) or 1.0
                    nx, ny = nx / ln, ny / ln
                    if nx * (mx - cen.x()) + ny * (my - cen.y()) < 0:
                        nx, ny = -nx, -ny
                    entrance_t.changeGeometry(f.id(), QgsGeometry.fromPointXY(QgsPointXY(mx + nx * off, my + ny * off)))
                entrance_t.commitChanges()
                t_leg = [((entrance_t if lg[0] is entrance else lg[0]),) + tuple(lg[1:]) for lg in t_leg]
        except Exception as _e:
            step("Inquadramento: simbolo ingressi non spostato (%s)" % _e)
            entrance_t = entrance
        bm, bcred = basemap_layer(tparams.get("basemap") or "")
        if bm is not None:
            credits.append(bcred)
        cat_l = cat_leg = None
        if tparams.get("catasto"):
            try:
                from .smg_terrain import catasto_it_raster, catasto_legend_layer
                _fw = PAGE_W - 2 * MARGIN - 2 * GRID_PAD
                _fh = PAGE_H - 2 * MARGIN - FOOTER_H - 2 * GRID_PAD
                _sc = choose_scale(bb.width() * 1.08, bb.height() * 1.08, _fw, _fh)
                _c = bb.center()
                _hw, _hh = _fw * _sc / 2000.0 * 1.05, _fh * _sc / 2000.0 * 1.05
                _cext = QgsRectangle(_c.x() - _hw, _c.y() - _hh, _c.x() + _hw, _c.y() + _hh)
                step("Particelle catastali (Agenzia delle Entrate)...")
                cat_l = catasto_it_raster(_cext, crs, os.path.join(out_dir, "%s_catasto.tif" % base))
            except Exception as _e:
                step("Particelle catastali non disponibili (%s)" % _e)
                cat_l = None
            if cat_l is not None:
                cat_leg = catasto_legend_layer(crs)
                credits.append("Cartografia catastale (c) Agenzia delle Entrate")
                t_leg.append((cat_leg, "Particelle catastali"))
        t_layers = [l for l in (entrance_t, walls_t, entl_t, cave_t, cont_l, cat_leg, cat_l, hs_l, bm) if l is not None]
        g_terr = grp.addGroup("Contesto terreno")
        for l in ((entrance_t if entrance_t is not entrance else None), walls_t, entl_t, cave_t, cont_l, cat_leg, cat_l, hs_l, bm):
            if l is not None:
                proj.addMapLayer(l, False)
                g_terr.addLayer(l)
        g_terr.setItemVisibilityChecked(False)
        add_page(layout, 4, crs, t_layers, bb, None, name, "INQUADRAMENTO", info,
                 "Inquadramento della grotta sul terreno (margine %d m).\n" % tbuf + "\n".join(credits),
                 grid=True, north=True, meta=dict(meta, tav="5 di %d" % n_tav), legend=t_leg)

    # ---------------- 3D (Fase 5): asse e stazioni con la quota, per la vista 3D di QGIS e il visualizzatore HTML
    axis3d, st3d = [], []
    try:
        # quota dell'asse (centro galleria) e quota di "cammino" (pavimento + 1,6 m, sotto la volta),
        # levigate con una mediana su 3 m per togliere i picchi dove la volta non e' stata rilevata
        def _fill(vals):
            out, last = [], None
            for v in vals:
                last = v if v is not None else last
                out.append(last)
            first = next((v for v in out if v is not None), None)
            return [v if v is not None else first for v in out]

        def _med(vals, k=3):
            return [float(np.median([v for v in vals[max(0, i - k):i + k + 1] if v is not None])) for i in range(len(vals))]
        _zf = _fill([rr.get("z_floor") for rr in samples])
        _zr = [rr.get("z_roof") for rr in samples]
        _zc = _fill([rr.get("z_center") if rr.get("z_center") is not None else
                     ((rr["z_floor"] + rr["z_roof"]) / 2.0 if rr.get("z_floor") is not None and rr.get("z_roof") is not None else None)
                     for rr in samples])
        if _zf and _zf[0] is not None and _zc[0] is not None:
            _zc, _zf = _med(_zc), _med(_zf)
            _zrm = _med([v for v in _zr]) if any(v is not None for v in _zr) else [None] * len(_zr)
            for i, rr in enumerate(samples):
                ze = _zf[i] + 1.6
                if _zrm[i] is not None and _zrm[i] - _zf[i] > 0.5:
                    ze = min(ze, _zrm[i] - 0.25)
                pk, _d = point_and_dir_at(V, rr["chainage"])
                axis3d.append((float(rr["chainage"]), float(pk[0]), float(pk[1]), float(_zc[i]), float(ze)))
        for f in st_feats:
            _sm = float(f["s_m"])
            _zf = value_at(samples, _sm, "z_floor")
            if _zf is None and axis3d:
                _zf = min(axis3d, key=lambda a: abs(a[0] - _sm))[3]
            _pt = f.geometry().asPoint()
            if _zf is not None:
                st3d.append((str(f["station_id"]), _sm, _pt.x(), _pt.y(), float(_zf)))
        if len(axis3d) > 1 and params.get("layers3d", True):
            from .smg_export_3d import layers_3d, style_3d
            _a3, _s3 = layers_3d(crs, axis3d, st3d)
            _a3, _s3 = persist_layers(gpkg, [(_a3, "asse_3d"), (_s3, "stazioni_3d")])
            style_3d(_a3, _s3)
            g_3d = grp.addGroup("3D (quote reali)")
            for _l in (_s3, _a3):
                proj.addMapLayer(_l, False)
                g_3d.addLayer(_l)
            g_3d.setItemVisibilityChecked(False)
    except Exception as e:
        step("Layer 3D non creati (%s)" % e)

    # ---------------- EXPORT SPELEOLOGICO: Survex e Compass (Fase 4)
    step("Export Survex e Compass...")
    cave_st = []
    for f in stl:
        p = f.geometry().asPoint()
        s0 = _num(f["s_m"])
        lr = (value_at(samples, s0, "left"), value_at(samples, s0, "right"), None, 0.0)
        zf, zr = _num(f["z_floor"]), _num(f["z_roof"])
        if zf is not None and zr is not None:
            lr = (lr[0], lr[1], round(zr - zf, 2), 0.0)
        cave_st.append(dict(id=str(f["station_id"]), e=p.x(), n=p.y(), z=zf, lrud=lr))
    cave_meta = dict(crs=crs.authid(), date=params.get("survey_date"), team=params.get("authors"), entrances=ent_names,
                     grade=params.get("grade"), instruments=params.get("instruments"))
    svx_path = write_svx(os.path.join(out_dir, "%s.svx" % base), name, cave_st, ent_name, cave_meta)
    dat_path = write_compass_dat(os.path.join(out_dir, "%s.dat" % base), name, cave_st, cave_meta)

    # ---------------- EXPORT
    step("Esportazione PDF e DXF...")
    pdf = os.path.join(out_dir, "%s_pianta_profilo_sezioni.pdf" % base)
    exp = QgsLayoutExporter(layout)
    st = QgsLayoutExporter.PdfExportSettings()
    st.dpi = 300
    st.rasterizeWholeImage = False
    res = exp.exportToPdf(pdf, st)
    if res != QgsLayoutExporter.Success:
        raise RuntimeError("Esportazione PDF fallita (codice %s)" % res)

    dxf_plan = os.path.join(out_dir, "%s_pianta.dxf" % base)
    dxf_prof = os.path.join(out_dir, "%s_profilo.dxf" % base)
    dxf_sec = os.path.join(out_dir, "%s_sezioni.dxf" % base)
    write_dxf(dxf_plan, sc_plan, [("SP_PLAN_ENTRANCE", entrance, "nome", 2.5, False, False, "SP_PLAN_ENTRANCE"),
                                   ("SP_PLAN_ENTRANCE_GPS", entrance_gps, None, 0, False, False),
                                   ("SP_PLAN_OUTLINE", walls, None, 0, False, False),
                                   ("SP_PLAN_ENTRANCE_LINE", ent_lines, None, 0, False, False),
                                   ("SP_PLAN_AXIS", axis, None, 0, False, False),
                                   ("SP_PLAN_STATIONS", stations, "station_id", 1.8, False, False, "SP_PLAN_LABELS"),
                                   ("SP_PLAN_SECTIONS", traces, None, 0, False, False),
                                   ("SP_PLAN_SECTION_LABELS", tr_lab, "txt", 2.5, True, True)] +
                                  ([("SP_PLAN_SPOT_HEIGHTS", spots, "txt", 1.8, False, False, "SP_PLAN_SPOT_LABELS")] if spots is not None else []),
              frame=dict(prefix="SP_PLAN", ext=mp_plan.extent(), iv=nice_round(max(mp_plan.extent().width(), mp_plan.extent().height()) / 8.0, [0.5, 1, 2, 5, 10, 20, 50, 100, 200, 500]), grid=True))
    write_dxf(dxf_prof, sc_prof, [("SP_LONG_OUTLINE", prof_shape, None, 0, False, False),
                                   ("SP_LONG_FLOOR", prof_floor, None, 0, False, False),
                                   ("SP_LONG_ROOF", prof_roof, None, 0, False, False)] +
                                  ([("SP_LONG_TERRAIN", prof_terr, None, 0, False, False)] if prof_terr is not None else []) + [
                                   ("SP_LONG_STATIONS", prof_st, None, 0, False, False),
                                   ("SP_LONG_LABELS", prof_stl, "station_id", 1.8, True, True)],
              frame=dict(prefix="SP_LONG", ext=mp_prof.extent(), iv=nice_round(max(mp_prof.extent().width(), mp_prof.extent().height()) / 8.0, [0.5, 1, 2, 5, 10, 20, 50, 100, 200, 500]), grid=True))
    write_dxf(dxf_sec, sc_sec, [("SP_SEC_OUTLINE", sec_shape, None, 0, False, False),
                                 ("SP_SEC_FLOOR", sec_floor, None, 0, False, False),
                                 ("SP_SEC_ROOF", sec_roof, None, 0, False, False),
                                 ("SP_SEC_AXIS", sec_axis, None, 0, False, False),
                                 ("SP_SEC_LABELS", sec_labels, "txt", 2.5, True, True)],
              frame=dict(prefix="SP_SEC", ext=mp_sec.extent(), iv=nice_round(max(mp_sec.extent().width(), mp_sec.extent().height()) / 8.0, [0.5, 1, 2, 5, 10, 20, 50, 100, 200, 500]), grid=False))


    write_profile_csv(os.path.join(out_dir, "%s_profilo_dati.csv" % base), samples)
    if terr_rows:
        with open(os.path.join(out_dir, "%s_terreno_profilo.csv" % base), "w", encoding="utf-8", newline="") as _fh:
            _fh.write("progressiva_m;x;y;z_dem;z_terreno;z_volta;roccia_sopra_volta_m\n")
            for r in terr_rows:
                _fh.write(";".join(("%.3f" % v) if v is not None else "" for v in r) + "\n")
        terrain_info["profile"] = terr_info
    viewer3d = None
    if params.get("viewer3d", True) and len(axis3d) > 1:
        step("Visualizzatore 3D...")
        try:
            from .smg_export_3d import write_viewer
            viewer3d = write_viewer(os.path.join(out_dir, "%s_3d.html" % base), las, axis3d, st3d, ent_names, name,
                                    crs.authid(), max_points=int(params.get("viewer_points") or 1000000),
                                    prep=(prep if (prep.get("is_gallery_raw") is not None or not prep["shift"].is_identity()) else None))
        except Exception as e:
            step("Visualizzatore 3D non creato (%s)" % e)
    # progetto QGIS autonomo nella cartella dei risultati (layer, stili e tavole, percorsi relativi)
    qgz = None
    if params.get("qgz", True):
        step("Progetto QGIS esportabile...")
        try:
            from .smg_qgz import export_project
            qgz = export_project(out_dir, base, name, crs, groups=[gname], layouts=[lname], progress=step)
        except Exception as e:
            step("Progetto QGIS non creato (%s)" % e)
    # parametri essenziali accanto alle tavole: servono al rilievo d'insieme per ritrovare il LAS della parte
    try:
        import json
        with open(os.path.join(out_dir, "%s_parametri.json" % base), "w", encoding="utf-8") as fh:
            json.dump(dict(las_path=las, cave_name=name, group=gname, crs=crs.authid(),
                           survey_type=prep["kind"], date=datetime.date.today().isoformat()), fh, ensure_ascii=False, indent=1)
    except Exception:
        pass
    step("Fatto.")
    return dict(pdf=pdf, dxf=[dxf_plan, dxf_prof, dxf_sec], gpkg=gpkg, layout=lname,
                n_points=n_all, length=L, svx=svx_path, dat=dat_path, terrain=terrain_info, entrance=ent_name, entrance_z=round(ent_z, 2), entrance_latlon_las=las_ll, gps_scarto=(round(scarto, 2) if scarto is not None else None), depth=round(depth, 2), rise=round(rise, 2), sections=len(sections), scales=(sc_plan, sc_prof, sc_sec), viewer3d=viewer3d, qgz=qgz,
                survey_type=prep["kind"], access_xy=((round(ent_pt.x(), 3), round(ent_pt.y(), 3)) if prep.get("access") else None),
                access_src=prep.get("access_src"), threshold_raw=prep.get("threshold_raw"),
                shift=dict(dx=prep["shift"].dx, dy=prep["shift"].dy, rot=prep["shift"].rot, dz=prep["shift"].dz),
                spots=([(f["txt"]) for f in spots.getFeatures()] if spots is not None else None), notes=prep.get("notes"))


def run_from_gpkg(gpkg_path):
    raise RuntimeError("Questa versione lavora direttamente dal file LAS: usa la nuova finestra del plugin.")
