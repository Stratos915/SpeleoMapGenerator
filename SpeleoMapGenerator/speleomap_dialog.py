# -*- coding: utf-8 -*-
import os
from qgis.PyQt.QtWidgets import (QDialog, QVBoxLayout, QFormLayout, QHBoxLayout, QPushButton, QFileDialog,
                                 QLineEdit, QDoubleSpinBox, QLabel, QMessageBox, QComboBox, QApplication,
                                 QTabWidget, QWidget, QCheckBox)
from qgis.PyQt.QtCore import QUrl
from qgis.PyQt.QtGui import QDesktopServices
from qgis.core import QgsProject, QgsPointCloudLayer, QgsVectorLayer, QgsWkbTypes, QgsCoordinateReferenceSystem, QgsSettings
from qgis.gui import QgsProjectionSelectionWidget

from .speleomap_engine import generate


class SpeleoMapDialog(QDialog):
    def __init__(self, iface):
        super().__init__(iface.mainWindow())
        self.iface = iface
        self.setWindowTitle("SpeleoMap Generator 1.1 - pianta, profilo, sezioni da LAS")
        self.resize(660, 520)
        lay = QVBoxLayout(self)
        # tre schede: Rilievo, Cartiglio e gruppo, Parametri
        tabs = QTabWidget()
        pages = {}
        for key, title in (("ril", "Rilievo"), ("pos", "Posizione e accesso"), ("cart", "Cartiglio e gruppo"),
                           ("par", "Parametri"), ("terr", "Terreno")):
            w = QWidget()
            pages[key] = QFormLayout(w)
            tabs.addTab(w, title)
        f_ril, f_cart, f_par, f_terr = pages["ril"], pages["cart"], pages["par"], pages["terr"]
        f_pos = pages["pos"]
        form = f_ril

        # file LAS
        self.las = QLineEdit()
        b = QPushButton("...")
        b.setMaximumWidth(40)
        b.clicked.connect(self.pick_las)
        h = QHBoxLayout(); h.addWidget(self.las); h.addWidget(b)
        form.addRow("File LAS", h)

        self.crs = QgsProjectionSelectionWidget()
        self.crs.setCrs(QgsCoordinateReferenceSystem("EPSG:32633"))
        form.addRow("Sistema di riferimento", self.crs)
        self.crs_hint = QLabel("")
        self.crs_hint.setStyleSheet("color:#555")
        form.addRow("", self.crs_hint)
        self.las.editingFinished.connect(lambda: self.set_las(self.las.text().strip()) if os.path.exists(self.las.text().strip()) else None)

        self.name = QLineEdit(); form.addRow("Nome grotta", self.name)
        self.loc = QLineEdit(); form.addRow("Localita'", self.loc)
        self.auth = QLineEdit(); form.addRow("Rilevatori", self.auth)
        self.date = QLineEdit(); form.addRow("Data rilievo", self.date)

        # dati del gruppo e del cartiglio: generici, modificabili, ricordati tra una sessione e l'altra
        form = f_cart
        st = QgsSettings()
        self._sk = "SpeleoMapGenerator/"
        self.auth.setText(st.value(self._sk + "authors", ""))
        self.group = QLineEdit(st.value(self._sk + "group", "Gruppo speleologico"))
        form.addRow("Gruppo", self.group)
        self.logo = QLineEdit(st.value(self._sk + "logo", ""))
        self.logo.setPlaceholderText("facoltativo: immagine PNG/SVG/JPG del logo")
        bl = QPushButton("...")
        bl.setMaximumWidth(40)
        bl.clicked.connect(self.pick_logo)
        hl = QHBoxLayout(); hl.addWidget(self.logo); hl.addWidget(bl)
        form.addRow("Logo", hl)
        self.drafter = QLineEdit(st.value(self._sk + "drafter", ""))
        form.addRow("Elaborazione grafica", self.drafter)
        self.instr = QLineEdit(st.value(self._sk + "instruments", "Scansione LiDAR (Scaniverse)"))
        form.addRow("Strumenti", self.instr)
        self.grade = QComboBox()
        self.grade.setEditable(True)
        self._grades = [
            ("UISv1 X-4-E", "Scansione LiDAR (es. iPhone/Scaniverse): grado X, dettagli in scala, dati elettronici"),
            ("UISv1 5-4-E", "DistoX calibrato + TopoDroid, disegno in scala, dati scaricati elettronicamente"),
            ("UISv1 5-4-BE", "Come sopra, con anelli chiusi e compensati"),
            ("UISv1 6-4-BCE", "Strumenti calibrati su treppiede, anelli chiusi, strumenti controllati"),
            ("UISv1 4-3-A", "Bussola, clinometro e cordella; schizzi in grotta non in scala"),
            ("UISv1 2-2-A", "Schizzo con stime, senza strumenti"),
            ("n.d.", "Grado non dichiarato"),
        ]
        for code, desc in self._grades:
            self.grade.addItem(code)
        self.grade_info = QLabel("")
        self.grade_info.setWordWrap(True)
        self.grade_info.setStyleSheet("color: gray; font-size: 11px;")
        self.grade.currentTextChanged.connect(self._grade_changed)
        self.grade.setCurrentText(st.value(self._sk + "grade", "") or "UISv1 X-4-E")
        self._grade_changed(self.grade.currentText())
        form.addRow("Grado del rilievo (UIS)", self.grade)
        form.addRow("", self.grade_info)
        self.oggetto = QLineEdit(st.value(self._sk + "oggetto", "Pianta - Profilo longitudinale - Sezioni"))
        form.addRow("Oggetto", self.oggetto)

        form = f_par
        self.axis = QComboBox()
        self.axis.addItem("Automatico (calcolato dai punti)", None)
        for l in QgsProject.instance().mapLayers().values():
            if isinstance(l, QgsVectorLayer) and l.geometryType() == QgsWkbTypes.LineGeometry:
                self.axis.addItem("Layer: " + l.name(), l.id())
        form.addRow("Asse", self.axis)

        def spin(v, lo, hi, dec, suf):
            s = QDoubleSpinBox(); s.setRange(lo, hi); s.setDecimals(dec); s.setValue(v); s.setSuffix(suf); return s
        form = f_ril
        self.entr = QLineEdit(); self.entr.setPlaceholderText("S0 = inizio asse; piu' ingressi separati da virgola, es. S0, fine")
        form.addRow("Stazione d'ingresso", self.entr)
        self.gps = QLineEdit(); self.gps.setPlaceholderText("dal telefono, es. 36.43210, 28.22010  oppure  36°25'55\"N 28°13'12\"E")
        form.addRow("Coordinate ingresso (GPS)", self.gps)
        self.gps_alt = QLineEdit(); self.gps_alt.setPlaceholderText("facoltativa, metri s.l.m.")
        form.addRow("Quota ingresso GPS", self.gps_alt)
        form = f_par
        self.ent_style = QComboBox()
        self.ent_style.addItem("Contorno aperto con linea tratteggiata", "tratteggiata")
        self.ent_style.addItem("Contorno solo aperto", "aperta")
        self.ent_style.setCurrentIndex(max(0, self.ent_style.findData(QgsSettings().value(self._sk + "entrance_style", "tratteggiata"))))
        form.addRow("Disegno degli ingressi", self.ent_style)
        self.st = spin(2.0, 0.5, 50, 1, " m"); form.addRow("Passo stazioni", self.st)
        self.sec = spin(5.0, 0.5, 100, 1, " m"); form.addRow("Passo sezioni trasversali", self.sec)
        self.th = spin(0.10, 0.02, 1.0, 2, " m"); form.addRow("Spessore fascia sezione (+/-)", self.th)
        self.ph = spin(0.30, 0.05, 3.0, 2, " m"); form.addRow("Fascia profilo longitudinale (+/-)", self.ph)
        self.v3d = QCheckBox("Visualizzatore 3D (file HTML con percorso lungo l'asse)")
        self.v3d.setChecked(QgsSettings().value(self._sk + "viewer3d", "true") == "true")
        form.addRow(self.v3d)
        self.v3dn = QDoubleSpinBox(); self.v3dn.setRange(50000, 2000000); self.v3dn.setDecimals(0); self.v3dn.setSingleStep(50000)
        self.v3dn.setValue(float(QgsSettings().value(self._sk + "viewer_points", 1000000)))
        form.addRow("Punti nel visualizzatore 3D", self.v3dn)

        self.out = QLineEdit()
        b2 = QPushButton("...")
        b2.setMaximumWidth(40)
        b2.clicked.connect(self.pick_out)
        h2 = QHBoxLayout(); h2.addWidget(self.out); h2.addWidget(b2)
        form.addRow("Cartella risultati", h2)
        # scheda Posizione e accesso (Fase 7): tipo di rilievo, gallerie, accesso, quote, correzione della posizione
        sp = QgsSettings()
        self.kind = QComboBox()
        self.kind.addItem("Grotta: si usa tutto il rilievo", "grotta")
        self.kind.addItem("Grotta con parti esterne: si tiene la parte coperta, ingressi automatici", "esterno")
        self.kind.addItem("Ipogeo: solo le gallerie sotto la soglia d'accesso", "ipogeo")
        self.kind.setCurrentIndex(max(0, self.kind.findData(sp.value(self._sk + "survey_type", "grotta"))))
        f_pos.addRow("Tipo di rilievo", self.kind)
        self.below = QDoubleSpinBox(); self.below.setRange(0.3, 30.0); self.below.setDecimals(1); self.below.setSuffix(" m")
        self.below.setValue(float(sp.value(self._sk + "gallery_below", 2.5)))
        f_pos.addRow("Gallerie: pavimento almeno", self.below)
        f_pos.addRow("", self._hint("sotto la soglia d'accesso (solo per gli ipogei)"))
        self.gal_l = QComboBox(); self.gal_l.addItem("Automatico (proposta salvata come 'limite gallerie')", None)
        self.acc_l = QComboBox(); self.acc_l.addItem("Automatico / stazione d'ingresso (scheda Rilievo)", None)
        for l in QgsProject.instance().mapLayers().values():
            if isinstance(l, QgsVectorLayer) and l.geometryType() == QgsWkbTypes.PolygonGeometry:
                self.gal_l.addItem("Layer: " + l.name(), l.id())
            if isinstance(l, QgsVectorLayer) and l.geometryType() == QgsWkbTypes.PointGeometry:
                self.acc_l.addItem("Layer: " + l.name(), l.id())
        f_pos.addRow("Limite delle gallerie", self.gal_l)
        f_pos.addRow("Punto d'accesso", self.acc_l)
        f_pos.addRow("", self._hint("Punti e poligono si disegnano sopra la nuvola LAS caricata in QGIS (prima della correzione). "
                                    "Un layer di punti puo' contenere piu' ingressi: il primo e' quello principale."))
        self.acc_q = QLineEdit(); self.acc_q.setPlaceholderText("es. 255.51 - quota dell'ingresso principale o della soglia d'accesso, m s.l.m.")
        f_pos.addRow("Quota dell'ingresso", self.acc_q)
        self.sh_mode = QComboBox()
        self.sh_mode.addItem("Nessuna (coordinate del LAS)", "none")
        self.sh_mode.addItem("Punto noto: coordinate vere dell'accesso", "punto")
        self.sh_mode.addItem("Spostamento dE, dN e rotazione", "dxdy")
        self.sh_mode.setCurrentIndex(max(0, self.sh_mode.findData(sp.value(self._sk + "shift_mode", "none"))))
        f_pos.addRow("Correzione della posizione", self.sh_mode)
        self.known = QLineEdit(); self.known.setPlaceholderText("lat, lon (es. 43.68741, 12.96407)  oppure  E, N (es. 335926.70, 4839162.42)")
        f_pos.addRow("Punto noto", self.known)
        self.sh_dx = QDoubleSpinBox(); self.sh_dx.setRange(-1000, 1000); self.sh_dx.setDecimals(2); self.sh_dx.setSuffix(" m")
        self.sh_dy = QDoubleSpinBox(); self.sh_dy.setRange(-1000, 1000); self.sh_dy.setDecimals(2); self.sh_dy.setSuffix(" m")
        self.sh_rot = QDoubleSpinBox(); self.sh_rot.setRange(-180, 180); self.sh_rot.setDecimals(2); self.sh_rot.setSuffix(" gradi")
        hsh = QHBoxLayout(); hsh.addWidget(QLabel("dE")); hsh.addWidget(self.sh_dx); hsh.addWidget(QLabel("dN")); hsh.addWidget(self.sh_dy)
        hsh.addWidget(QLabel("rot.")); hsh.addWidget(self.sh_rot)
        f_pos.addRow("Spostamento", hsh)
        f_pos.addRow("", self._hint("Il GPS del telefono sbaglia di qualche metro: misura lo scarto su catasto o con un GNSS e correggilo qui. "
                                    "La rotazione e' antioraria attorno all'accesso."))
        # scheda Terreno: tavola di inquadramento facoltativa
        stt = QgsSettings()
        self.t_on = QCheckBox("Aggiungi la tavola di inquadramento (5a tavola)")
        self.t_on.setChecked(stt.value(self._sk + "terrain_on", "false") == "true")
        f_terr.addRow(self.t_on)
        self.t_base = QComboBox()
        for key, lab in (("esri", "Esri World Imagery (ortofoto mondiale)"), ("osm", "OpenStreetMap"), ("", "Nessuna basemap")):
            self.t_base.addItem(lab, key)
        i = self.t_base.findData(stt.value(self._sk + "terrain_base", "esri"))
        self.t_base.setCurrentIndex(max(0, i))
        f_terr.addRow("Basemap", self.t_base)
        self.t_dem = QComboBox()
        for key, lab in (("auto", "Automatico: LiDAR 1 m se indicato, TINITALY 10 m in Italia, Copernicus 30 m altrove"),
                         ("lidar", "LiDAR 1 m MASE (ZIP o cartella scaricati dal portale PST)"),
                         ("tinitaly", "TINITALY 10 m (Italia, INGV)"),
                         ("copernicus", "Copernicus 30 m (tutto il mondo)"),
                         ("file", "File GeoTIFF (es. LiDAR regionale 1-2 m)"),
                         ("", "Nessun DEM")):
            self.t_dem.addItem(lab, key)
        _dv = stt.value(self._sk + "terrain_dem_src", "auto")
        self.t_dem.setCurrentIndex(max(0, self.t_dem.findData(_dv)))
        f_terr.addRow("DEM (ombreggiatura e curve)", self.t_dem)
        self.t_demf = QLineEdit(stt.value(self._sk + "terrain_dem_file", ""))
        self.t_demf.setPlaceholderText("solo se DEM = File GeoTIFF")
        bdf = QPushButton("...")
        bdf.setMaximumWidth(40)
        bdf.clicked.connect(self.pick_dem)
        hdf = QHBoxLayout(); hdf.addWidget(self.t_demf); hdf.addWidget(bdf)
        f_terr.addRow("File DEM", hdf)
        self.t_lidar = QLineEdit(stt.value(self._sk + "terrain_lidar", ""))
        self.t_lidar.setPlaceholderText("ZIP o cartella del DTM LiDAR scaricato dal portale MASE (facoltativo)")
        blz = QPushButton("ZIP"); blz.setMaximumWidth(46); blz.clicked.connect(self.pick_lidar_zip)
        bld = QPushButton("Cartella"); bld.setMaximumWidth(70); bld.clicked.connect(self.pick_lidar_dir)
        hli = QHBoxLayout(); hli.addWidget(self.t_lidar); hli.addWidget(blz); hli.addWidget(bld)
        f_terr.addRow("LiDAR 1 m", hli)
        bchk = QPushButton("Quali tavole LiDAR servono per questo rilievo?")
        bchk.clicked.connect(self.check_lidar)
        f_terr.addRow(bchk)
        self.t_cat = QCheckBox("Particelle catastali (Italia, Agenzia delle Entrate)")
        self.t_cat.setChecked(stt.value(self._sk + "terrain_catasto", "false") == "true")
        f_terr.addRow(self.t_cat)
        self.t_prof = QCheckBox("Linea del terreno nel profilo longitudinale")
        self.t_prof.setChecked(stt.value(self._sk + "terrain_profile", "true") == "true")
        f_terr.addRow(self.t_prof)
        self.t_align = QCheckBox("Allinea il DEM alla quota dell'ingresso (consigliato per LAS da telefono)")
        self.t_align.setChecked(stt.value(self._sk + "terrain_align", "true") == "true")
        f_terr.addRow(self.t_align)
        self.t_buf = QDoubleSpinBox(); self.t_buf.setRange(20, 5000); self.t_buf.setDecimals(0); self.t_buf.setSuffix(" m")
        self.t_buf.setValue(float(stt.value(self._sk + "terrain_buf", 150)))
        f_terr.addRow("Margine attorno alla grotta", self.t_buf)
        note = QLabel("Serve la connessione a Internet. TINITALY (INGV, 10 m) copre solo l'Italia; Copernicus (30 m) e' un "
                      "modello di superficie (alberi ed edifici compresi): va bene per l'inquadramento, non per il dettaglio. "
                      "Per il massimo dettaglio usa un file GeoTIFF (es. LiDAR regionale).")
        note.setWordWrap(True)
        note.setStyleSheet("color: gray; font-size: 11px;")
        f_terr.addRow(note)
        lay.addWidget(tabs)

        self.status = QLabel("")
        lay.addWidget(self.status)
        self.run = QPushButton("Genera le tavole: pianta, profilo, sezioni, tabella (PDF + DXF + CSV)")
        self.run.clicked.connect(self.generate)
        lay.addWidget(self.run)

        # precompila con la nuvola di punti caricata nel progetto
        for l in QgsProject.instance().mapLayers().values():
            if isinstance(l, QgsPointCloudLayer) and l.source().lower().endswith(".las"):
                self.set_las(l.source(), l.crs())
                break

    def set_las(self, path, crs=None):
        self.las.setText(path)
        from .smg_input_las import read_las_epsg
        code = read_las_epsg(path)
        if code:                                   # il sistema dichiarato nel LAS ha la precedenza (resta modificabile)
            crs = QgsCoordinateReferenceSystem("EPSG:%d" % code)
        if crs is not None and crs.isValid():
            self.crs.setCrs(crs)
        self.crs_hint.setText(("Letto dal file LAS: EPSG:%d (puoi cambiarlo)" % code) if code else
                              "Il LAS non dichiara il sistema di riferimento: sceglilo tu.")
        if not self.out.text():
            self.out.setText(os.path.join(os.path.expanduser("~"), "SpeleoMap",
                                          os.path.splitext(os.path.basename(path))[0].replace(" ", "_")))

    def pick_las(self):
        p, _ = QFileDialog.getOpenFileName(self, "Seleziona file LAS", "", "LAS (*.las *.LAS)")
        if p:
            self.out.setText("")
            self.set_las(p)

    @staticmethod
    def _hint(text):
        lb = QLabel(text)
        lb.setWordWrap(True)
        lb.setStyleSheet("color: gray; font-size: 11px;")
        return lb

    def _grade_changed(self, text):
        desc = dict(self._grades).get(text.strip(), "Codice personalizzato (formato UISv1 X-Y-Z)")
        self.grade_info.setText(desc + ".  X = poligonale (1-6, X), Y = dettagli (1-4), lettere = controlli (B anelli, C strumenti, E dati elettronici, F ingresso preciso).")

    def pick_dem(self):
        p, _ = QFileDialog.getOpenFileName(self, "File DEM", "", "Raster (*.tif *.tiff *.asc *.vrt)")
        if p:
            self.t_demf.setText(p)
            self.t_dem.setCurrentIndex(max(0, self.t_dem.findData("file")))

    def pick_lidar_zip(self):
        p, _ = QFileDialog.getOpenFileName(self, "ZIP del LiDAR (portale MASE)", "", "ZIP o GeoTIFF (*.zip *.tif *.tiff)")
        if p:
            self.t_lidar.setText(p)
            self.t_dem.setCurrentIndex(max(0, self.t_dem.findData("lidar")))

    def pick_lidar_dir(self):
        p = QFileDialog.getExistingDirectory(self, "Cartella del LiDAR (portale MASE)")
        if p:
            self.t_lidar.setText(p)
            self.t_dem.setCurrentIndex(max(0, self.t_dem.findData("lidar")))

    def check_lidar(self):
        """Interroga il quadro d'unione del MASE sull'area del LAS (con il margine della scheda Terreno)."""
        import struct
        from qgis.core import QgsRectangle
        from .smg_terrain import lidar_index, LIDAR_PORTAL
        las = self.las.text().strip()
        if not las or not os.path.exists(las):
            QMessageBox.warning(self, "LiDAR", "Seleziona prima il file LAS.")
            return
        with open(las, "rb") as fh:
            h = fh.read(227)
        mxx, mnx, mxy, mny = struct.unpack("<4d", h[179:211])
        ext = QgsRectangle(mnx, mny, mxx, mxy)
        ext.grow(self.t_buf.value())
        try:
            tiles = lidar_index(ext, self.crs.crs())
        except Exception as e:
            QMessageBox.warning(self, "LiDAR", "Non riesco a consultare il quadro d'unione del Ministero: %s" % e)
            return
        if not tiles:
            QMessageBox.information(self, "LiDAR", "Nessuna tavola LiDAR del Ministero copre quest'area: si usera' TINITALY 10 m.")
            return
        txt = "Tavole LiDAR (passo %.0f m) che coprono il rilievo e il margine di %.0f m:\n\n" % (tiles[0]["res"], self.t_buf.value())
        txt += "\n".join("%s  (%s, %s)" % (t["tavola"], t["regione"], t["data"]) for t in tiles)
        txt += ("\n\nScaricale dal portale MASE (gratis, CC BY 4.0): seleziona l'area, aggiungi al carrello il DTM 1x1 e "
                "'Scarica tutto'. Poi indica qui lo ZIP. Apro il portale?")
        if QMessageBox.question(self, "LiDAR", txt) == QMessageBox.Yes:
            QDesktopServices.openUrl(QUrl(LIDAR_PORTAL))

    def pick_logo(self):
        p, _ = QFileDialog.getOpenFileName(self, "Logo del gruppo", "", "Immagini (*.png *.svg *.jpg *.jpeg)")
        if p:
            self.logo.setText(p)

    def pick_out(self):
        p = QFileDialog.getExistingDirectory(self, "Cartella risultati", self.out.text())
        if p:
            self.out.setText(p)

    def generate(self):
        las = self.las.text().strip()
        if not las or not os.path.exists(las):
            QMessageBox.warning(self, "File mancante", "Seleziona un file LAS valido.")
            return
        if not self.name.text().strip():
            QMessageBox.warning(self, "Nome grotta", "Inserisci il nome della grotta (va nel cartiglio).")
            return
        from .smg_input_las import read_las_epsg
        _code = read_las_epsg(las)
        if _code and self.crs.crs().postgisSrid() != _code:
            if QMessageBox.question(self, "Sistema di riferimento",
                                    "Il file LAS dichiara EPSG:%d, ma hai scelto %s.\n\nUsare comunque %s?\n"
                                    "(No = torna alla finestra per correggere)" % (_code, self.crs.crs().authid(), self.crs.crs().authid()),
                                    QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
                return
        out = self.out.text().strip() or os.path.join(os.path.dirname(las), "SpeleoMap")
        from .smg_util import parse_latlon
        try:
            latlon = parse_latlon(self.gps.text())
        except ValueError as e:
            QMessageBox.warning(self, "Coordinate ingresso", "Non riesco a leggere le coordinate: %s." % e)
            return
        alt = None
        if self.gps_alt.text().strip():
            try:
                alt = float(self.gps_alt.text().strip().replace(",", "."))
            except ValueError:
                QMessageBox.warning(self, "Quota ingresso", "La quota deve essere un numero (metri s.l.m.).")
                return
        axis_layer = None
        if self.axis.currentData():
            axis_layer = QgsProject.instance().mapLayer(self.axis.currentData())
        # posizione e accesso (Fase 7)
        acc_q = None
        if self.acc_q.text().strip():
            try:
                acc_q = float(self.acc_q.text().strip().replace(",", "."))
            except ValueError:
                QMessageBox.warning(self, "Quota di accesso", "La quota di accesso deve essere un numero (metri s.l.m.).")
                return
        known = None
        if self.sh_mode.currentData() == "punto":
            from .smg_ipogei import parse_known_point
            try:
                known = parse_known_point(self.known.text(), self.crs.crs())
            except ValueError as e:
                QMessageBox.warning(self, "Punto noto", "Non riesco a leggere il punto noto: %s." % e)
                return
            if known is None:
                QMessageBox.warning(self, "Punto noto", "Inserisci le coordinate vere dell'accesso (lat, lon oppure E, N).")
                return
            if not self.acc_l.currentData():
                QMessageBox.warning(self, "Punto noto", "Per correggere con un punto noto serve il punto d'accesso disegnato sulla nuvola LAS.")
                return
        pos = dict(survey_type=self.kind.currentData(), gallery_below=self.below.value(),
                   gallery_layer=(QgsProject.instance().mapLayer(self.gal_l.currentData()) if self.gal_l.currentData() else None),
                   access_layer=(QgsProject.instance().mapLayer(self.acc_l.currentData()) if self.acc_l.currentData() else None),
                   access_quota=acc_q, shift_mode=self.sh_mode.currentData(), known_point=known,
                   shift_dx=self.sh_dx.value(), shift_dy=self.sh_dy.value(), shift_rot=self.sh_rot.value())
        params = dict(las_path=las, crs=self.crs.crs(), cave_name=self.name.text(), location=self.loc.text(),
                      authors=self.auth.text(), survey_date=self.date.text(), station_step=self.st.value(),
                      section_step=self.sec.value(), section_thickness=self.th.value(),
                      profile_halfwidth=self.ph.value(), out_dir=out, axis_layer=axis_layer,
                      entrance_station=self.entr.text().strip(), entrance_latlon=latlon, entrance_alt=alt,
                      group=self.group.text().strip(), logo=self.logo.text().strip(), drafter=self.drafter.text().strip(),
                      instruments=self.instr.text().strip(), grade=self.grade.currentText().strip(), oggetto=self.oggetto.text().strip(),
                      terrain=dict(enable=self.t_on.isChecked(), basemap=self.t_base.currentData(), dem=self.t_dem.currentData(),
                                   dem_file=self.t_demf.text().strip(), catasto=self.t_cat.isChecked(), buffer=self.t_buf.value(),
                                   profile_line=self.t_prof.isChecked(), align=self.t_align.isChecked(),
                                   lidar_path=self.t_lidar.text().strip()),
                      entrance_style=self.ent_style.currentData(),
                      viewer3d=self.v3d.isChecked(), viewer_points=int(self.v3dn.value()), **pos)
        st = QgsSettings()
        st.setValue(self._sk + "survey_type", self.kind.currentData())
        st.setValue(self._sk + "gallery_below", self.below.value())
        st.setValue(self._sk + "shift_mode", self.sh_mode.currentData())
        for k, w in (("authors", self.auth), ("group", self.group), ("logo", self.logo), ("drafter", self.drafter),
                     ("instruments", self.instr), ("oggetto", self.oggetto)):
            st.setValue(self._sk + k, w.text().strip())
        st.setValue(self._sk + "grade", self.grade.currentText().strip())
        st.setValue(self._sk + "terrain_on", "true" if self.t_on.isChecked() else "false")
        st.setValue(self._sk + "terrain_base", self.t_base.currentData())
        st.setValue(self._sk + "terrain_dem_src", self.t_dem.currentData())
        st.setValue(self._sk + "terrain_dem_file", self.t_demf.text().strip())
        st.setValue(self._sk + "terrain_catasto", "true" if self.t_cat.isChecked() else "false")
        st.setValue(self._sk + "entrance_style", self.ent_style.currentData())
        st.setValue(self._sk + "terrain_buf", self.t_buf.value())
        st.setValue(self._sk + "terrain_lidar", self.t_lidar.text().strip())
        st.setValue(self._sk + "viewer3d", "true" if self.v3d.isChecked() else "false")
        st.setValue(self._sk + "viewer_points", int(self.v3dn.value()))
        st.setValue(self._sk + "terrain_profile", "true" if self.t_prof.isChecked() else "false")
        st.setValue(self._sk + "terrain_align", "true" if self.t_align.isChecked() else "false")
        self.run.setEnabled(False)

        def prog(msg):
            self.status.setText(msg)
            QApplication.processEvents()
        try:
            r = generate(params, progress=prog)
        except Exception as e:
            self.run.setEnabled(True)
            QMessageBox.critical(self, "Errore", str(e))
            return
        self.run.setEnabled(True)
        msg = ("Fatto.\n\nPDF (4 tavole: pianta, profilo, sezioni, tabella poligonale):\n%s\n\nDXF:\n%s\n\nCSV: dati del profilo, poligonale, stazioni\n\n"
               "Sviluppo asse %.1f m - %d sezioni - scala 1:%d\n\n"
               "Il layout '%s' e' aperto in Gestione layout per eventuali ritocchi.") % (
            r["pdf"], "\n".join(os.path.basename(d) for d in r["dxf"]), r["length"], r["sections"], r["scales"][0], r["layout"])
        QMessageBox.information(self, "SpeleoMap Generator", msg)
        QDesktopServices.openUrl(QUrl.fromLocalFile(r["pdf"]))
