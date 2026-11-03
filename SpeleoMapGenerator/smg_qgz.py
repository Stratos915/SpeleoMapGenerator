# -*- coding: utf-8 -*-
"""SpeleoMap Generator - progetto QGIS esportabile (Fase 8).

Scrive nella cartella dei risultati un progetto QGIS autonomo (.qgz) con i layer del rilievo, i loro stili ed
etichette e le tavole (layout), con percorsi relativi: chi riceve la cartella apre il .qgz su un altro computer,
ritocca e riesporta PDF, DXF o immagini senza rifare il rilievo.
- i layer gia' su file (GeoPackage, GeoTIFF della cartella) restano dove sono;
- i layer solo in memoria vengono salvati in <base>_progetto.gpkg;
- i layer su file fuori dalla cartella dei risultati vengono copiati dentro (sottocartella 'dati_progetto');
- immagini delle tavole (logo) fuori dalla cartella vengono copiate accanto al progetto;
- i servizi web (ortofoto, OSM, catasto WMS) restano collegamenti: servono Internet per vederli.
Il progetto aperto in QGIS non viene toccato."""

import os
import re
import shutil


def _slug(s):
    return re.sub(r"[^A-Za-z0-9_]+", "_", s).strip("_")[:40] or "layer"


def _inside(path, folder):
    try:
        return os.path.commonpath([os.path.abspath(path), os.path.abspath(folder)]) == os.path.abspath(folder)
    except ValueError:
        return False


def _copy_style(src, dst):
    from qgis.PyQt.QtXml import QDomDocument
    doc = QDomDocument()
    src.exportNamedStyle(doc)
    dst.importNamedStyle(doc)


def _file_of(layer):
    """Percorso del file di un layer su file (None per servizi web e memoria)."""
    if layer.providerType() not in ("ogr", "gdal"):
        return None
    p = layer.source().split("|")[0]
    return p if os.path.exists(p) else None


def _copy_layer(lyr, out_dir, base, extra_gpkg, used, step):
    """Copia indipendente del layer per il nuovo progetto, con lo stesso id (le tavole lo ritrovano)."""
    from qgis.core import QgsVectorLayer, QgsVectorFileWriter, QgsProject, QgsRasterLayer, QgsMapLayerType
    if lyr.type() == QgsMapLayerType.VectorLayer and lyr.providerType() == "memory":
        name = _slug(lyr.name())
        k = 1
        while name in used:
            k += 1
            name = "%s_%d" % (_slug(lyr.name()), k)
        used.add(name)
        o = QgsVectorFileWriter.SaveVectorOptions()
        o.driverName = "GPKG"
        o.layerName = name
        o.actionOnExistingFile = (QgsVectorFileWriter.CreateOrOverwriteLayer if os.path.exists(extra_gpkg)
                                  else QgsVectorFileWriter.CreateOrOverwriteFile)
        err = QgsVectorFileWriter.writeAsVectorFormatV3(lyr, extra_gpkg, QgsProject.instance().transformContext(), o)
        if err[0] != QgsVectorFileWriter.NoError:
            step("Progetto QGIS: layer '%s' non salvato (%s)" % (lyr.name(), err[1]))
            return None
        new = QgsVectorLayer("%s|layername=%s" % (extra_gpkg, name), lyr.name(), "ogr")
        _copy_style(lyr, new)
    else:
        f = _file_of(lyr)
        if f and not _inside(f, out_dir):
            # file fuori dalla cartella dei risultati (es. un DEM locale): si copia dentro
            dd = os.path.join(out_dir, "dati_progetto")
            os.makedirs(dd, exist_ok=True)
            dst = os.path.join(dd, os.path.basename(f))
            if not os.path.exists(dst):
                shutil.copy2(f, dst)
            src = lyr.source().replace(f, dst)
            new = (QgsRasterLayer(src, lyr.name(), lyr.providerType()) if lyr.type() == QgsMapLayerType.RasterLayer
                   else QgsVectorLayer(src, lyr.name(), lyr.providerType()))
            _copy_style(lyr, new)
        else:
            new = lyr.clone()
    if new is None or not new.isValid():
        step("Progetto QGIS: layer '%s' non copiato" % lyr.name())
        return None
    try:
        new.setId(lyr.id())
    except Exception:
        pass
    return new


def _copy_tree(src_grp, dst_grp, new_layers):
    from qgis.core import QgsLayerTreeGroup, QgsLayerTreeLayer
    for ch in src_grp.children():
        if isinstance(ch, QgsLayerTreeGroup):
            g = dst_grp.addGroup(ch.name())
            g.setItemVisibilityChecked(ch.itemVisibilityChecked())
            g.setExpanded(ch.isExpanded())
            _copy_tree(ch, g, new_layers)
        elif isinstance(ch, QgsLayerTreeLayer) and ch.layer() is not None:
            nl = new_layers.get(ch.layer().id())
            if nl is not None:
                n = dst_grp.addLayer(nl)
                n.setItemVisibilityChecked(ch.itemVisibilityChecked())


def export_project(out_dir, base, title, crs, groups=(), layouts=(), extra_layers=(), progress=None):
    """Scrive <out_dir>/<base>.qgz con i gruppi di layer 'groups' (nomi nel progetto aperto), i layer in piu'
    'extra_layers' (usati dalle tavole ma fuori dai gruppi) e le tavole 'layouts' (nomi). Restituisce il percorso."""
    from qgis.core import (QgsProject, QgsPrintLayout, QgsReadWriteContext, QgsLayoutItemPicture, Qgis,
                           QgsLayoutItemMap)
    from qgis.PyQt.QtXml import QDomDocument
    step = progress or (lambda m: None)
    src = QgsProject.instance()
    root = src.layerTreeRoot()
    extra_gpkg = os.path.join(out_dir, "%s_progetto.gpkg" % base)
    path = os.path.join(out_dir, "%s.qgz" % base)
    # tutti i layer che servono: quelli dei gruppi + quelli citati dalle tavole
    want = {}
    for gname in groups:
        g = root.findGroup(gname)
        if g is not None:
            for n in g.findLayers():
                if n.layer() is not None:
                    want[n.layer().id()] = n.layer()
    for l in extra_layers or ():
        if l is not None:
            want[l.id()] = l
    lays = [l for l in src.layoutManager().layouts() if l.name() in layouts]
    for lay in lays:
        for it in lay.items():
            if isinstance(it, QgsLayoutItemMap):
                for l in it.layers():
                    if l is not None:
                        want[l.id()] = l
    proj = QgsProject()
    proj.setCrs(crs)
    proj.setTitle(title)
    try:
        proj.setFilePathStorage(Qgis.FilePathType.Relative)
    except Exception:
        proj.writeEntry("Paths", "/Absolute", False)
    if os.path.exists(extra_gpkg):
        try:
            os.remove(extra_gpkg)
        except OSError:
            pass
    used, new_layers = set(), {}
    for lid, l in want.items():
        nl = _copy_layer(l, out_dir, base, extra_gpkg, used, step)
        if nl is not None:
            new_layers[lid] = nl
            proj.addMapLayer(nl, False)
    # albero dei layer: gli stessi gruppi; in fondo i layer usati solo dalle tavole
    placed = set()
    for gname in groups:
        g = root.findGroup(gname)
        if g is None:
            continue
        ng = proj.layerTreeRoot().addGroup(g.name())
        _copy_tree(g, ng, new_layers)
        placed |= {n.layer().id() for n in g.findLayers() if n.layer() is not None}
    rest = [lid for lid in new_layers if lid not in placed]
    if rest:
        rg = proj.layerTreeRoot().addGroup("Usati dalle tavole")
        for lid in rest:
            rg.addLayer(new_layers[lid])
    # tavole: copiate via XML (le mappe ritrovano i layer per id)
    ctx = QgsReadWriteContext()
    for lay in lays:
        doc = QDomDocument()
        el = lay.writeXml(doc, ctx)
        nl = QgsPrintLayout(proj)
        nl.readXml(el, doc, ctx)
        nl.setName(lay.name())
        for it in nl.items():
            if isinstance(it, QgsLayoutItemPicture):
                p = it.picturePath()
                if p and os.path.exists(p) and not _inside(p, out_dir):
                    dst = os.path.join(out_dir, "%s_%s" % (base, os.path.basename(p)))
                    if not os.path.exists(dst):
                        shutil.copy2(p, dst)
                    it.setPicturePath(dst)
        proj.layoutManager().addLayout(nl)
    if not proj.write(path):
        raise RuntimeError("Progetto QGIS non scritto: %s" % proj.error())
    step("Progetto QGIS salvato: %s (%d layer, %d tavole)" % (os.path.basename(path), len(new_layers), len(lays)))
    return path
