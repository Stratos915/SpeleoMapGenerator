# -*- coding: utf-8 -*-
"""SpeleoMap Generator - UNIONE di piu' rilievi gia' elaborati in un'unica serie di tavole.

Ogni parte e' un gruppo 'SpeleoMap ... - <nome>' del progetto (generato dal plugin). Le tavole:
  1   pianta d'insieme (tutte le parti, con le parti esterne in grigio)
  2.. profilo e sezioni di ogni parte
  ul. inquadramento d'insieme (se una parte ha il contesto terreno)
"""
import datetime
import os

from qgis.core import (Qgis, QgsProject, QgsPrintLayout, QgsLayoutItemPage, QgsLayoutExporter, QgsRectangle, QgsGeometry,
                       QgsPointXY, QgsFeatureRequest, QgsVectorLayer, QgsRasterLayer)

from .smg_layers import mem_layer, add_rows, style_marker, label
from .smg_layout import add_page


def _group_layers(root, gname):
    g = root.findGroup(gname)
    if g is None:
        raise RuntimeError("Gruppo '%s' non trovato nel progetto: genera prima la parte con il plugin." % gname)
    out = {}
    for sg in g.children():
        sub = sg.name() if not hasattr(sg, "layer") else ""
        for n in (sg.findLayers() if hasattr(sg, "findLayers") else []):
            lyr = n.layer()
            if lyr is not None:
                out.setdefault(sub, {})[lyr.name()] = lyr
    return out


def _relabel_entrances(lyr, prefix, crs, name):
    """Copia degli ingressi con i nomi della parte (es. 'Ingresso A', 'Ingresso B1')."""
    feats = sorted(lyr.getFeatures(), key=lambda f: f.id())
    m = mem_layer("Point", name, crs, [("nome", "s")])
    rows = []
    for k, f in enumerate(feats):
        nm = "Ingresso %s" % prefix if len(feats) == 1 else "Ingresso %s%d" % (prefix, k + 1)
        rows.append((QgsGeometry(f.geometry()), [nm]))
    add_rows(m, rows)
    m.setRenderer(lyr.renderer().clone())
    label(m, "nome", 8, bold=True, around=True, dist=1.5)
    return m


def build_union(parts, name, out_dir, crs, info_extra=None, meta=None, layout_name=None, extras=None):
    """parts: [dict(label='A', title='Rodini', group='SpeleoMap DEV - Rodini', length=..., entr_z=[...])].
    extras: elementi di riferimento esterni alle grotte (sentieri, tetti...): [dict(layer=QgsVectorLayer, legend='...')],
    disegnati sotto le grotte nella pianta d'insieme e nell'inquadramento."""
    extras = [e for e in (extras or []) if e.get("layer") is not None]
    proj = QgsProject.instance()
    root = proj.layerTreeRoot()
    os.makedirs(out_dir, exist_ok=True)
    P = []
    for p in parts:
        L = _group_layers(root, p["group"])
        P.append(dict(p, L=L))
    lm = proj.layoutManager()
    lname = layout_name or ("SpeleoMap - %s" % name)
    for l in lm.layouts():
        if l.name() == lname:
            lm.removeLayout(l)
    layout = QgsPrintLayout(proj)
    layout.initializeDefaults()
    layout.pageCollection().page(0).setPageSize("A3", QgsLayoutItemPage.Landscape)
    layout.setName(lname)
    lm.addLayout(layout)
    keep = []          # i layer creati qui restano vivi finche' serve il layout

    # ---- distanza tra le parti (contorni)
    outl = [next(p["L"]["Pianta"]["contorno"].getFeatures()).geometry() for p in P]
    gaps = []
    for i in range(len(outl)):
        for j in range(i + 1, len(outl)):
            gaps.append((P[i]["label"], P[j]["label"], outl[i].distance(outl[j])))
    tot = sum(p.get("length") or 0 for p in P)
    n_tav = 1 + 2 * len(P) + (1 if any("Contesto terreno" in p["L"] for p in P) else 0)
    info_lines = (info_extra or [])[:]
    info_lines.append("Parti: " + "; ".join("%s = %s (sviluppo %.1f m)" % (p["label"], p["title"], p.get("length") or 0) for p in P))
    info_lines.append("Sviluppo totale rilevato: %.1f m" % tot +
                      ("  -  " + "; ".join("distanza %s-%s: %.0f m" % g for g in gaps) if gaps else ""))
    info_lines.append("Sistema di riferimento: %s %s" % (crs.authid(), crs.description()))
    info_lines.append("QGIS %s / SpeleoMap Generator - unione - %s" % (Qgis.version().split("-")[0], datetime.date.today().strftime("%d/%m/%Y")))
    info = "\n".join(info_lines)
    meta = dict(meta or {})
    meta.setdefault("group", "Gruppo speleologico")
    meta.setdefault("oggetto", "Pianta d'insieme - Profili - Sezioni")

    # ---- TAV 1: pianta d'insieme
    plan_layers, leg = [], []
    lab_rows = []
    ext = QgsRectangle()
    for p in P:
        pl = p["L"]["Pianta"]
        ent = _relabel_entrances(pl["ingresso"], p["label"], crs, "ingressi %s" % p["label"])
        keep.append(ent)
        proj.addMapLayer(ent, False)
        g = next(pl["contorno"].getFeatures()).geometry()
        ext.combineExtentWith(g.boundingBox())
        c = g.pointOnSurface().asPoint()
        lab_rows.append((QgsGeometry.fromPointXY(QgsPointXY(c)), ["%s - %s" % (p["label"], p["title"])]))
        lay = [ent, pl.get("ingressi_limite"), pl.get("asse"), pl.get("pareti"), pl.get("punti_pianta"), pl.get("resto_rilievo")]
        plan_layers += [l for l in lay if l is not None]
        if not leg:
            leg = [(ent, "Ingressi"), (pl["asse"], "Asse della cavita'"), (pl["pareti"], "Contorno (pareti)")]
            if pl.get("ingressi_limite") is not None:
                leg.append((pl["ingressi_limite"], "Limite dell'ingresso"))
    for e in extras:
        plan_layers.append(e["layer"])
        ext.combineExtentWith(e["layer"].extent())
        leg.append((e["layer"], e["legend"]))
    names = mem_layer("Point", "nomi delle parti", crs, [("txt", "s")])
    add_rows(names, lab_rows)
    style_marker(names, "0,0,0,0", "0,0,0,0", 0.1)
    label(names, "txt", 11, bold=True, around=False)
    keep.append(names)
    proj.addMapLayer(names, False)
    plan_layers.insert(0, names)
    ext.grow(5.0)
    note = ("Pianta d'insieme delle parti rilevate separatamente; in grigio le parti esterne (solo riferimento)"
            + ("; tratteggiati gli elementi esterni rilevati a parte" if extras else "") + ".\n"
            "Le parti non si sovrappongono: la posizione reciproca viene dalla georeferenziazione di ciascun rilievo "
            "(GPS del telefono, incertezza di alcuni metri). Nord di reticolo (%s)." % crs.authid())
    add_page(layout, 0, crs, plan_layers, ext, None, name, "PIANTA D'INSIEME", info, note,
             grid=True, north=True, meta=dict(meta, tav="1 di %d" % n_tav), legend=leg)

    # ---- profilo e sezioni di ogni parte
    idx = 1
    for p in P:
        pr = p["L"]["Profilo longitudinale (coord. locali)"]
        lay = [pr.get(k) for k in ("profilo_stazioni_lab", "profilo_stazioni", "profilo_terreno", "profilo_sezione",
                                    "profilo_contorno", "profilo_proiezione")]
        lay = [l for l in lay if l is not None]
        e = QgsRectangle(pr["profilo_proiezione"].extent())
        e.combineExtentWith(pr["profilo_stazioni_lab"].extent())
        if pr.get("profilo_terreno") is not None:
            e.combineExtentWith(pr["profilo_terreno"].extent())
        e.grow(1.0)
        lg = [(pr["profilo_contorno"], "Sezione lungo l'asse"), (pr["profilo_proiezione"], "Proiezione dell'intera cavita'"),
              (pr["profilo_stazioni"], "Stazioni e sezioni")]
        if pr.get("profilo_terreno") is not None:
            lg.append((pr["profilo_terreno"], "Superficie del terreno (DEM)"))
        add_page(layout, idx, crs, lay, e, None, name, "PROFILO LONGITUDINALE - %s (%s)" % (p["label"], p["title"]), info,
                 "Parte %s - %s. Ascisse: progressiva lungo l'asse della parte (m). Ordinate: quota (m).\n"
                 "Scala orizzontale = scala verticale." % (p["label"], p["title"]),
                 grid=True, north=False, meta=dict(meta, tav="%d di %d" % (idx + 1, n_tav)), legend=lg)
        idx += 1
        se = p["L"]["Sezioni (coord. locali)"]
        lay = [se.get(k) for k in ("sezioni_etichette", "sezioni_asse", "sezioni_punti", "sezioni_profilo")]
        e = QgsRectangle(se["sezioni_punti"].extent())
        e.combineExtentWith(se["sezioni_etichette"].extent())
        add_page(layout, idx, crs, [l for l in lay if l is not None], e, None, name,
                 "SEZIONI TRASVERSALI - %s (%s)" % (p["label"], p["title"]), info,
                 "Parte %s - %s. Viste nel verso della progressiva della parte." % (p["label"], p["title"]),
                 grid=False, north=False, meta=dict(meta, tav="%d di %d" % (idx + 1, n_tav)),
                 legend=[(se["sezioni_profilo"], "Sezione trasversale"), (se["sezioni_asse"], "Posizione dell'asse")])
        idx += 1

    # ---- inquadramento d'insieme
    tp = next((p for p in P if "Contesto terreno" in p["L"]), None)
    if tp is not None:
        T = tp["L"]["Contesto terreno"]
        lay, tleg = [], []
        for p in P:
            ct = p["L"].get("Contesto terreno", {})
            ent = [l for l in plan_layers if l.name() == "ingressi %s" % p["label"]][0]
            lay += [ent] + [l for l in (ct.get("pareti (inquadramento)"), ct.get("limite ingressi (inquadramento)"),
                                         ct.get("contorno (inquadramento)"), p["L"]["Pianta"].get("resto_rilievo"))
                            if l is not None]
            if not tleg and ct.get("pareti (inquadramento)") is not None:
                tleg = [(ent, "Ingressi"), (ct["pareti (inquadramento)"], "Contorno delle grotte")]
        lay.insert(0, names)
        bb = QgsRectangle(ext)
        bb.grow(120.0)
        # DEM, ombreggiatura e curve di livello su tutta la tavola di inquadramento (non solo attorno a una parte)
        dem_note = "DEM e curve dalla parte %s." % tp["label"]
        try:
            ctr, cl, sh, dem_note2 = _context_terrain(bb, crs, out_dir, name, keep)
            T = dict(T)
            T["curve di livello"] = cl
            # sopra l'ortofoto niente ombreggiatura (coprirebbe l'immagine); senza ortofoto si usa quella nuova
            T["ombreggiatura"] = None if (T.get("Esri World Imagery") is not None or T.get("OpenStreetMap") is not None) else sh
            dem_note = dem_note2
        except Exception:
            pass
        for e in extras:
            lay.append(e["layer"])
            tleg.append((e["layer"], e["legend"]))
        for k in ("curve di livello", "ombreggiatura", "Esri World Imagery", "OpenStreetMap"):
            if T.get(k) is not None:
                lay.append(T[k])
        if T.get("curve di livello") is not None:
            tleg.append((T["curve di livello"], "Curve di livello"))
        add_page(layout, idx, crs, lay, bb, None, name, "INQUADRAMENTO", info,
                 "Inquadramento delle parti sul terreno. Immagini (c) Esri, Maxar, Earthstar Geographics;\n" + dem_note,
                 grid=True, north=True, meta=dict(meta, tav="%d di %d" % (idx + 1, n_tav)), legend=tleg)

    pdf = os.path.join(out_dir, "%s.pdf" % name.replace(" ", "_").replace("'", ""))
    st = QgsLayoutExporter.PdfExportSettings()
    st.dpi = 300
    st.rasterizeWholeImage = False
    res = QgsLayoutExporter(layout).exportToPdf(pdf, st)
    if res != QgsLayoutExporter.Success:
        raise RuntimeError("Esportazione PDF fallita (codice %s)" % res)
    return dict(pdf=pdf, layout=lname, pages=layout.pageCollection().pageCount(), gaps=gaps, total=tot, keep=keep)

def _context_terrain(bb, crs, out_dir, name, keep):
    """DEM (TINITALY in Italia, altrimenti Copernicus GLO-30) su tutta l'area della tavola di inquadramento,
    con ombreggiatura e curve di livello. Restituisce (dem_tif, curve, ombreggiatura, nota)."""
    from qgis.core import QgsCoordinateTransform, QgsCoordinateReferenceSystem
    from .smg_terrain import copernicus_dem, tinitaly_dem, in_italy, hillshade, contour_interval, contours, style_contours
    from .smg_layout import PAGE_W, PAGE_H, MARGIN, FOOTER_H, GRID_PAD
    fw = PAGE_W - 2 * MARGIN - 2 * GRID_PAD
    fh = PAGE_H - 2 * MARGIN - FOOTER_H - 2 * GRID_PAD
    w, h = bb.width(), bb.height()
    if w / h < fw / fh:
        w = h * fw / fh
    else:
        h = w * fh / fw
    c = bb.center()
    e = QgsRectangle(c.x() - w * 0.56, c.y() - h * 0.56, c.x() + w * 0.56, c.y() + h * 0.56)
    tr = QgsCoordinateTransform(crs, QgsCoordinateReferenceSystem("EPSG:4326"), QgsProject.instance())
    ll = tr.transform(c)
    base = os.path.join(out_dir, name.replace(" ", "_").replace("'", ""))
    tif = base + "_dem_inquadramento.tif"
    from .smg_terrain import local_dem, file_dem
    loc = local_dem(e, crs)
    if loc:
        tif, (mn, mx) = file_dem(loc, e, crs, tif)
        note = "DEM e curve: DEM locale %s, su tutta la tavola." % os.path.basename(loc)
    elif in_italy(ll.y(), ll.x()):
        tif, (mn, mx) = tinitaly_dem(e, crs, tif)
        note = "DEM e curve: TINITALY 1.1 (INGV, CC BY 4.0) su tutta la tavola."
    else:
        tif, (mn, mx) = copernicus_dem(e, crs, tif)
        note = "DEM e curve: Copernicus GLO-30 (c) DLR/Airbus, ESA, su tutta la tavola."
    hs = hillshade(tif, base + "_ombreggiatura_inquadramento.tif")
    iv = max(1, min(contour_interval(mx - mn), 2 if (mx - mn) < 150 else 5))   # curve fitte come nelle tavole delle parti
    cg = contours(tif, base + "_curve_inquadramento.gpkg", iv)
    cl = QgsVectorLayer("%s|layername=curve_livello" % cg, "curve di livello", "ogr")
    style_contours(cl, iv, on_image=True)
    sh = QgsRasterLayer(hs, "ombreggiatura")
    for l in (cl, sh):
        QgsProject.instance().addMapLayer(l, False)
        keep.append(l)
    return tif, cl, sh, note
