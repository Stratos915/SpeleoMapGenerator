# -*- coding: utf-8 -*-
"""SpeleoMap Generator - finestra SEZIONI E PROFILI A SCELTA.

Le sezioni e i profili li decide chi rileva: si disegnano in QGIS su due layer di linee (uno per le tracce delle
sezioni, uno per le linee dei profili), poi il plugin ricava la pianta dal LAS e fa le sezioni esattamente sulle
tracce (A-A', B-B'... o i nomi scritti nel campo 'nome') e sviluppa ogni profilo lungo la sua linea.
Il primo profilo e' il profilo longitudinale principale (stazioni, quote, terreno); gli altri vanno sotto, nella
stessa tavola. La finestra resta aperta mentre si disegna."""
import os
import traceback

from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QFormLayout, QLabel, QLineEdit, QPushButton,
                                 QComboBox, QFileDialog, QMessageBox, QApplication, QDoubleSpinBox, QCheckBox)
from qgis.core import (QgsProject, QgsSettings, QgsVectorLayer, QgsField, QgsWkbTypes, QgsCoordinateTransform,
                       QgsMapLayerProxyModel, QgsFeature, QgsGeometry, QgsPointXY)
from qgis.gui import QgsProjectionSelectionWidget, QgsMapLayerComboBox, QgsFieldComboBox
from qgis.PyQt.QtCore import QVariant

SK = "SpeleoMapGenerator/"
TITLE = "SpeleoMap Generator - Sezioni e profili a scelta"
_KEEP = []


def _out_base():
    return os.path.join(os.path.expanduser("~"), "SpeleoMap")


def _lines(layer, field, crs):
    """[(nome, [(x, y), ...]), ...] delle linee del layer, nel sistema 'crs', ordinate per nome."""
    if layer is None:
        return []
    tr = None
    if layer.crs() != crs:
        tr = QgsCoordinateTransform(layer.crs(), crs, QgsProject.instance())
    out = []
    for i, f in enumerate(layer.getFeatures()):
        g = QgsGeometry(f.geometry())
        if g.isEmpty():
            continue
        if tr:
            g.transform(tr)
        parts = g.asMultiPolyline() if g.isMultipart() else [g.asPolyline()]
        pts = [(p.x(), p.y()) for part in parts for p in part]
        if len(pts) < 2:
            continue
        nm = ""
        if field and field in f.fields().names():
            v = f[field]
            nm = "" if v is None or str(v) == "NULL" else str(v).strip()
        out.append((nm, i, pts))
    out.sort(key=lambda r: (r[0] == "", r[0], r[1]))
    return [(nm, pts) for nm, _, pts in out]


class TracceDialog(QDialog):
    def __init__(self, iface):
        super().__init__(iface.mainWindow())
        self.iface = iface
        self.setWindowTitle(TITLE)
        self.setModal(False)
        self.resize(760, 560)
        st = QgsSettings()
        v = QVBoxLayout(self)
        intro = QLabel("Disegna in QGIS dove vuoi le <b>sezioni</b> (una linea per sezione, da A verso A') e dove vuoi "
                       "i <b>profili</b> (una linea, anche spezzata, per profilo). La pianta la ricava il plugin dal LAS. "
                       "Con i pulsanti qui sotto carichi il LAS sulla mappa e crei i due layer da disegnare; la finestra "
                       "puo' restare aperta mentre disegni.")
        intro.setWordWrap(True)
        v.addWidget(intro)
        f = QFormLayout()
        h = QHBoxLayout()
        self.las = QLineEdit(st.value(SK + "tr_las", ""))
        b = QPushButton("Sfoglia...")
        b.clicked.connect(self._pick_las)
        h.addWidget(self.las)
        h.addWidget(b)
        f.addRow("File LAS", h)
        self.crs = QgsProjectionSelectionWidget()
        self.crs.setCrs(QgsProject.instance().crs())
        f.addRow("Sistema di riferimento", self.crs)
        self.name = QLineEdit(st.value(SK + "tr_name", ""))
        f.addRow("Nome della grotta", self.name)
        h = QHBoxLayout()
        self.out = QLineEdit(st.value(SK + "tr_out", ""))
        b = QPushButton("Sfoglia...")
        b.clicked.connect(self._pick_out)
        h.addWidget(self.out)
        h.addWidget(b)
        f.addRow("Cartella dei risultati", h)
        v.addLayout(f)

        h = QHBoxLayout()
        for txt, fn in (("Carica il LAS sulla mappa", self._load_las), ("Crea i layer da disegnare", self._make_layers)):
            b = QPushButton(txt)
            b.clicked.connect(fn)
            h.addWidget(b)
        h.addStretch(1)
        v.addLayout(h)

        f = QFormLayout()
        self.l_sec = QgsMapLayerComboBox()
        self.l_sec.setFilters(QgsMapLayerProxyModel.LineLayer)
        self.l_sec.setAllowEmptyLayer(True)
        self.f_sec = QgsFieldComboBox()
        self.f_sec.setAllowEmptyFieldName(True)
        self.l_sec.layerChanged.connect(self.f_sec.setLayer)
        h = QHBoxLayout()
        h.addWidget(self.l_sec, 3)
        h.addWidget(QLabel("nome dal campo"))
        h.addWidget(self.f_sec, 1)
        f.addRow("Tracce delle sezioni", h)
        self.l_prof = QgsMapLayerComboBox()
        self.l_prof.setFilters(QgsMapLayerProxyModel.LineLayer)
        self.l_prof.setAllowEmptyLayer(True)
        self.f_prof = QgsFieldComboBox()
        self.f_prof.setAllowEmptyFieldName(True)
        self.l_prof.layerChanged.connect(self.f_prof.setLayer)
        h = QHBoxLayout()
        h.addWidget(self.l_prof, 3)
        h.addWidget(QLabel("nome dal campo"))
        h.addWidget(self.f_prof, 1)
        f.addRow("Linee dei profili", h)
        self.th = QDoubleSpinBox()
        self.th.setRange(0.02, 5.0)
        self.th.setDecimals(2)
        self.th.setValue(float(st.value(SK + "tr_th", 0.30)))
        self.th.setSuffix(" m")
        f.addRow("Spessore della fetta delle sezioni (meta')", self.th)
        self.phw = QDoubleSpinBox()
        self.phw.setRange(0.05, 20.0)
        self.phw.setDecimals(2)
        self.phw.setValue(float(st.value(SK + "tr_phw", 1.50)))
        self.phw.setSuffix(" m")
        f.addRow("Larghezza della fascia dei profili (meta')", self.phw)
        self.ent = QLineEdit(st.value(SK + "tr_ent", ""))
        self.ent.setPlaceholderText("vuoto = automatico; stazione (S0, fine) del primo profilo")
        f.addRow("Ingresso", self.ent)
        self.c_3d = QCheckBox("Vista 3D percorribile")
        self.c_3d.setChecked(True)
        f.addRow("", self.c_3d)
        v.addLayout(f)
        self._pick_defaults()

        self.msg = QLabel("")
        self.msg.setWordWrap(True)
        v.addWidget(self.msg)
        h = QHBoxLayout()
        h.addStretch(1)
        self.b_run = QPushButton("Crea pianta, sezioni e profili")
        self.b_run.clicked.connect(self._run)
        b = QPushButton("Chiudi")
        b.clicked.connect(self.close)
        h.addWidget(self.b_run)
        h.addWidget(b)
        v.addLayout(h)

    # ------------------------------------------------------------------ aiuti
    def _pick_defaults(self):
        for lyr in QgsProject.instance().mapLayers().values():
            if lyr.name() == "tracce_sezioni_da_disegnare":
                self.l_sec.setLayer(lyr)
                self.f_sec.setLayer(lyr)
                self.f_sec.setField("nome")
            if lyr.name() == "linee_profili_da_disegnare":
                self.l_prof.setLayer(lyr)
                self.f_prof.setLayer(lyr)
                self.f_prof.setField("nome")

    def _pick_las(self):
        fn, _ = QFileDialog.getOpenFileName(self, "File LAS", self.las.text() or os.path.expanduser("~"), "LAS (*.las)")
        if fn:
            self.las.setText(fn)
            if not self.name.text().strip():
                self.name.setText(os.path.splitext(os.path.basename(fn))[0])
            try:
                from .smg_insieme import las_epsg
                from qgis.core import QgsCoordinateReferenceSystem
                e = las_epsg(fn)
                if e:
                    self.crs.setCrs(QgsCoordinateReferenceSystem("EPSG:%d" % e))
                    self.msg.setText("Sistema di riferimento impostato dal LAS: EPSG:%d (puoi cambiarlo)." % e)
            except Exception:
                pass

    def _pick_out(self):
        d = QFileDialog.getExistingDirectory(self, "Cartella dei risultati", self.out.text() or _out_base())
        if d:
            self.out.setText(d)

    def _load_las(self):
        from qgis.core import QgsPointCloudLayer
        fn = self.las.text().strip()
        if not fn or not os.path.exists(fn):
            QMessageBox.information(self, TITLE, "Scegli prima il file LAS.")
            return
        lyr = QgsPointCloudLayer(fn, os.path.splitext(os.path.basename(fn))[0], "pdal")
        if not lyr.isValid():
            self.msg.setText("QGIS non riesce ad aprire il LAS come nuvola di punti.")
            return
        lyr.setCrs(self.crs.crs())
        QgsProject.instance().addMapLayer(lyr)
        self.iface.mapCanvas().setExtent(QgsCoordinateTransform(lyr.crs(), self.iface.mapCanvas().mapSettings().destinationCrs(),
                                                                 QgsProject.instance()).transformBoundingBox(lyr.extent()))
        self.iface.mapCanvas().refresh()
        self.msg.setText("LAS caricato (QGIS crea un indice la prima volta: puo' volerci un momento).")

    def _make_layers(self):
        crs = self.crs.crs().authid()
        made = []
        for nm, combo, fcombo in (("tracce_sezioni_da_disegnare", self.l_sec, self.f_sec),
                                  ("linee_profili_da_disegnare", self.l_prof, self.f_prof)):
            ex = [l for l in QgsProject.instance().mapLayersByName(nm)]
            if ex:
                lyr = ex[0]
            else:
                lyr = QgsVectorLayer("LineString?crs=%s&field=nome:string(20)" % crs, nm, "memory")
                QgsProject.instance().addMapLayer(lyr)
                _KEEP.append(lyr)
                made.append(nm)
            combo.setLayer(lyr)
            fcombo.setLayer(lyr)
            fcombo.setField("nome")
        lyr = QgsProject.instance().mapLayersByName("tracce_sezioni_da_disegnare")[0]
        self.iface.setActiveLayer(lyr)
        lyr.startEditing()
        self.msg.setText("Layer pronti%s. Disegna le sezioni nel layer 'tracce_sezioni_da_disegnare' (attivo e in modifica) "
                         "e i profili in 'linee_profili_da_disegnare'; nel campo 'nome' puoi scrivere la lettera "
                         "(A, B...). Sono layer temporanei: per conservarli, tasto destro > Rendi permanente."
                         % ((" (creati: " + ", ".join(made) + ")") if made else ""))

    def _progress(self, m):
        self.msg.setText(m)
        QApplication.processEvents()

    # ------------------------------------------------------------------ esecuzione
    def _run(self):
        from .speleomap_engine import generate
        las = self.las.text().strip()
        if not las or not os.path.exists(las):
            QMessageBox.information(self, TITLE, "Scegli il file LAS.")
            return
        crs = self.crs.crs()
        for lyr in (self.l_sec.currentLayer(), self.l_prof.currentLayer()):
            if lyr is not None and lyr.isEditable():
                lyr.commitChanges()
        secs = _lines(self.l_sec.currentLayer(), self.f_sec.currentField(), crs)
        profs = _lines(self.l_prof.currentLayer(), self.f_prof.currentField(), crs)
        if not secs and not profs:
            QMessageBox.information(self, TITLE, "Disegna almeno una sezione o un profilo (o scegli i layer).")
            return
        name = self.name.text().strip() or os.path.splitext(os.path.basename(las))[0]
        out = self.out.text().strip() or os.path.join(_out_base(), name.replace(" ", "_").replace("'", ""))
        st = QgsSettings()
        for k, val in (("tr_las", las), ("tr_name", name), ("tr_out", self.out.text().strip()), ("tr_th", self.th.value()),
                       ("tr_phw", self.phw.value()), ("tr_ent", self.ent.text().strip())):
            st.setValue(SK + k, val)
        axis_layer = None
        if profs:
            axis_layer = QgsVectorLayer("LineString?crs=%s" % crs.authid(), "asse_dal_primo_profilo", "memory")
            ft = QgsFeature()
            ft.setGeometry(QgsGeometry.fromPolylineXY([QgsPointXY(*p) for p in profs[0][1]]))
            axis_layer.dataProvider().addFeatures([ft])
            _KEEP.append(axis_layer)
        letters = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
        section_lines = [dict(lab=(nm or (letters[i] if i < 26 else "S%d" % i)), pts=pts) for i, (nm, pts) in enumerate(secs)]
        extra = [dict(lab=(nm or "Profilo %d" % (i + 2)), pts=pts) for i, (nm, pts) in enumerate(profs[1:])]
        params = dict(las_path=las, crs=crs, cave_name=name, location=st.value(SK + "location", ""),
                      authors=st.value(SK + "authors", ""), survey_date=st.value(SK + "survey_date", ""),
                      station_step=2.0, section_step=5.0, section_thickness=self.th.value(),
                      profile_halfwidth=self.phw.value(), out_dir=out, axis_layer=axis_layer,
                      entrance_station=self.ent.text().strip(), entrance_latlon=None, entrance_alt=None,
                      group=st.value(SK + "group", ""), logo=st.value(SK + "logo", ""),
                      drafter=st.value(SK + "drafter", ""), instruments=st.value(SK + "instruments", "Scansione LiDAR"),
                      grade=st.value(SK + "grade", ""), oggetto="Pianta - Profili - Sezioni",
                      terrain=dict(enable=True, basemap=st.value(SK + "terrain_base", "esri"), dem="auto", dem_file="",
                                   catasto=False, buffer=30, profile_line=True, align=False, lidar_path=""),
                      entrance_style=None, viewer3d=self.c_3d.isChecked(), viewer_points=1000000, survey_type="grotta",
                      gallery_below=2.5, gallery_layer=None, access_layer=None, access_quota=None, shift_mode="none",
                      known_point=None, shift_dx=0, shift_dy=0, shift_rot=0,
                      section_lines=section_lines, extra_profiles=extra)
        self.b_run.setEnabled(False)
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            res = generate(params, progress=self._progress)
            lines = ["Fatto: %d sezioni, %d profili. Risultati in %s" % (res.get("sections", 0), len(profs), out)]
            if res.get("pdf"):
                lines.append("Tavole PDF: %s" % os.path.basename(res["pdf"]))
            if res.get("qgz"):
                lines.append("Progetto QGIS: %s" % os.path.basename(res["qgz"]))
            if not profs:
                lines.append("Nessun profilo disegnato: il profilo longitudinale segue l'asse calcolato dal plugin.")
            self.msg.setText("\n".join(lines))
        except Exception as e:
            self.msg.setText("Errore: %s" % e)
            traceback.print_exc()
        finally:
            QApplication.restoreOverrideCursor()
            self.b_run.setEnabled(True)
