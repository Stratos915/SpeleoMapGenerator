# -*- coding: utf-8 -*-
"""SpeleoMap Generator - finestra RILIEVO D'INSIEME (piu' grotte + scansioni di collegamento)."""
import os
import traceback

from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QFormLayout, QLabel, QLineEdit, QPushButton,
                                 QTableWidget, QTableWidgetItem, QCheckBox, QComboBox, QFileDialog, QMessageBox,
                                 QHeaderView, QApplication, QDoubleSpinBox, QAbstractItemView)
from qgis.core import QgsProject, QgsSettings
from qgis.gui import QgsProjectionSelectionWidget

PREFIX = "SpeleoMap - "
SK = "SpeleoMapGenerator/"
TITLE = "SpeleoMap Generator - Rilievo d'insieme"
_KEEP = []          # i layer creati restano vivi finche' QGIS e' aperto

MODES = [("aggancia", "Aggancia sul terreno in comune (valori indicativi + affinamento)"),
         ("monconi", "Monconi: prosecuzione delle gallerie di una parte gia' sistemata"),
         ("perno", "Ruota attorno a un punto (perno E N), poi sposta"),
         ("manuale", "Manuale (valori esatti)"), ("gps", "Posizione GPS (com'e')")]


def _out_base():
    return os.path.join(os.path.expanduser("~"), "SpeleoMap")


class InsiemeDialog(QDialog):
    def __init__(self, iface):
        super().__init__(iface.mainWindow())
        self.iface = iface
        self.setWindowTitle(TITLE)
        self.resize(980, 720)
        v = QVBoxLayout(self)
        intro = QLabel("Unisce grotte gia' elaborate con il plugin (le <b>parti</b>, nella loro posizione definitiva) e le "
                       "<b>scansioni di collegamento</b> (strade, sentieri, tetti, pezze) in un unico rilievo: "
                       "LAS d'insieme ordinato, tavole PDF d'insieme e vista 3D percorribile.")
        intro.setWordWrap(True)
        v.addWidget(intro)
        f = QFormLayout()
        self.name = QLineEdit(QgsSettings().value(SK + "ins_name", "Rilievo d'insieme"))
        f.addRow("Nome del rilievo", self.name)
        h = QHBoxLayout()
        self.out = QLineEdit(QgsSettings().value(SK + "ins_out", ""))
        b = QPushButton("Sfoglia...")
        b.clicked.connect(self._pick_out)
        h.addWidget(self.out)
        h.addWidget(b)
        f.addRow("Cartella dei risultati", h)
        self.crs = QgsProjectionSelectionWidget()
        self.crs.setCrs(QgsProject.instance().crs())
        f.addRow("Sistema di riferimento", self.crs)
        f.addRow("", QLabel("<i>Si imposta da solo dal primo LAS aggiunto; puoi sempre cambiarlo.</i>"))
        v.addLayout(f)

        v.addWidget(QLabel("<b>Parti</b> (le grotte) - quelle gia' elaborate nel progetto compaiono da sole; con "
                           "<i>Aggiungi LAS come parte</i> se ne aggiungono di nuove, elaborate prima dell'unione (per le nuove si "
                           "puo' scegliere la posizione: GPS, aggancio alle parti precedenti o valori manuali). "
                           "Solo le righe spuntate vengono usate; l'ordine della tabella e' l'ordine del giro 3D."))
        self.tp = QTableWidget(0, 13)
        self.tp.setHorizontalHeaderLabels(["Usa", "Lettera", "Nome nelle tavole", "File LAS della parte", "Tipo",
                                           "Gruppo nel progetto", "Posizione", "Rotazione", "E", "N", "Z",
                                           "Ingresso (stazione)", "Perno (E N)"])
        self.tp.horizontalHeader().setSectionResizeMode(3, QHeaderView.Stretch)
        self.tp.setSelectionBehavior(QAbstractItemView.SelectRows)
        v.addWidget(self.tp)
        h = QHBoxLayout()
        for txt, fn in (("Su", lambda: self._move(-1)), ("Giu'", lambda: self._move(1)),
                        ("Aggiungi LAS come parte...", self._add_part_las), ("Rimuovi", self._del_part),
                        ("Cambia LAS...", self._pick_part_las), ("Aggiorna elenco", self._fill_parts)):
            b = QPushButton(txt)
            b.clicked.connect(fn)
            h.addWidget(b)
        h.addStretch(1)
        v.addLayout(h)

        lab = QLabel("<b>Scansioni di collegamento</b> - rotazione in gradi (antioraria, attorno al centro della "
                     "scansione) e spostamento in metri. Posizioni (valgono anche per le parti nuove): "
                     "<i>Aggancia</i> = bastano valori indicativi (entro ~20 gradi e ~20 m), il programma affina sul "
                     "terreno in comune; <i>Monconi</i> = le gallerie proseguono quelle di una parte gia' sistemata "
                     "(estremi su monconi; con valori indicativi si sceglie tra piu' soluzioni); <i>Ruota attorno a un "
                     "punto</i> = rotazione attorno al perno E N (es. un ingresso), poi spostamento. "
                     "<i>Anteprima</i> calcola le posizioni nell'ordine della tabella, le mostra sulla mappa e scrive i "
                     "valori esatti.")
        lab.setWordWrap(True)
        v.addWidget(lab)
        self.tc = QTableWidget(0, 8)
        self.tc.setHorizontalHeaderLabels(["File LAS", "Descrizione (legenda)", "Posizione", "Rotazione", "E", "N", "Z",
                                           "Perno (E N)"])
        self.tc.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.tc.setSelectionBehavior(QAbstractItemView.SelectRows)
        v.addWidget(self.tc)
        h = QHBoxLayout()
        for txt, fn in (("Aggiungi LAS...", self._add_conn), ("Rimuovi", self._del_conn),
                        ("Sposta tra le parti", self._conn_to_part), ("Anteprima posizioni", self._preview)):
            b = QPushButton(txt)
            b.clicked.connect(fn)
            h.addWidget(b)
        h.addStretch(1)
        v.addLayout(h)

        h = QHBoxLayout()
        h.addWidget(QLabel("Ingressi allo stesso piano - parti:"))
        self.lv_parts = QLineEdit()
        self.lv_parts.setPlaceholderText("es. B, C, D (vuoto = nessun allineamento)")
        h.addWidget(self.lv_parts)
        h.addWidget(QLabel("quota comune (m s.l.m.):"))
        self.lv_z = QLineEdit()
        self.lv_z.setPlaceholderText("vuota = mediana degli ingressi indicati")
        h.addWidget(self.lv_z)
        v.addLayout(h)
        hint = QLabel("<i>Per le parti indicate il programma alza o abbassa ogni rilievo finche' il pavimento del suo ingresso "
                      "e' alla quota comune (utile quando il GPS del telefono sbaglia la quota), poi lo rielabora. "
                      "L'ingresso si indica nella colonna 'Ingresso': stazione (S0, S40, 'fine') oppure coordinate E N "
                      "nel sistema del rilievo (es. 609527 4033729): vale la stazione piu' vicina.</i>")
        hint.setWordWrap(True)
        v.addWidget(hint)
        h = QHBoxLayout()
        self.c_pdf = QCheckBox("Tavole PDF d'insieme")
        self.c_las = QCheckBox("LAS d'insieme")
        self.c_3d = QCheckBox("Vista 3D percorribile")
        for c in (self.c_pdf, self.c_las, self.c_3d):
            c.setChecked(True)
            h.addWidget(c)
        h.addStretch(1)
        v.addLayout(h)
        self.msg = QLabel("")
        self.msg.setWordWrap(True)
        v.addWidget(self.msg)
        h = QHBoxLayout()
        h.addStretch(1)
        self.b_run = QPushButton("Crea il rilievo d'insieme")
        self.b_run.clicked.connect(self._run)
        b = QPushButton("Chiudi")
        b.clicked.connect(self.reject)
        h.addWidget(self.b_run)
        h.addWidget(b)
        v.addLayout(h)
        self._fill_parts()

    # ------------------------------------------------------------------ parti
    def _fill_parts(self):
        from .smg_insieme import find_part_groups, part_info
        self.tp.setRowCount(0)
        for k, g in enumerate(find_part_groups(PREFIX)):
            try:
                info = part_info(g, PREFIX)
            except Exception:
                continue
            self._set_row(self.tp.rowCount(), dict(use=False, label=chr(ord("A") + k) if k < 26 else str(k + 1),
                                                   title=info["title"], las=info["las"], group=g,
                                                   entrance=", ".join(e["station"] for e in info["entrances"])))
        if self.tp.rowCount() == 0:
            self.msg.setText("Nel progetto non ci sono grotte elaborate: aggiungile con 'Aggiungi LAS come parte'.")

    # una riga della tabella delle parti <-> dizionario (spostare righe con menu e numeri resta sicuro)
    def _row(self, r):
        def it(c):
            return self.tp.item(r, c).text() if self.tp.item(r, c) else ""
        new = self.tp.cellWidget(r, 4) is not None
        d = dict(use=self.tp.item(r, 0).checkState() == Qt.Checked, label=it(1), title=it(2), las=it(3),
                 group="" if new else it(5), entrance=it(11), perno=it(12))
        if new:
            d.update(kind=self.tp.cellWidget(r, 4).currentData(), mode=self.tp.cellWidget(r, 6).currentData(),
                     rot=self.tp.cellWidget(r, 7).value(), de=self.tp.cellWidget(r, 8).value(),
                     dn=self.tp.cellWidget(r, 9).value(), dz=self.tp.cellWidget(r, 10).value())
        return d

    def _ro(self, t):
        x = QTableWidgetItem(t)
        x.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
        return x

    def _set_row(self, r, d):
        if r >= self.tp.rowCount():
            self.tp.insertRow(r)
        it = QTableWidgetItem()
        it.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled)
        it.setCheckState(Qt.Checked if d.get("use") else Qt.Unchecked)
        self.tp.setItem(r, 0, it)
        self.tp.setItem(r, 1, QTableWidgetItem(d.get("label", "")))
        self.tp.setItem(r, 2, QTableWidgetItem(d.get("title", "")))
        self.tp.setItem(r, 3, QTableWidgetItem(d.get("las", "")))
        for c in range(4, 11):
            self.tp.removeCellWidget(r, c)
            self.tp.setItem(r, c, self._ro(""))
        self.tp.setItem(r, 11, QTableWidgetItem(d.get("entrance", "")))
        self.tp.setItem(r, 12, QTableWidgetItem(d.get("perno", "")))
        if d.get("group"):
            self.tp.setItem(r, 4, self._ro("gia' elaborata"))
            self.tp.setItem(r, 5, self._ro(d["group"]))
            return
        self.tp.setItem(r, 5, self._ro("(nuova: da elaborare)"))
        cb = QComboBox()
        for k, t in (("grotta", "Grotta (tutto il rilievo)"), ("esterno", "Grotta con parti all'aperto"),
                     ("ipogeo", "Ipogeo / galleria (automatico)")):
            cb.addItem(t, k)
        cb.setCurrentIndex(max(0, cb.findData(d.get("kind", "grotta"))))
        self.tp.setCellWidget(r, 4, cb)
        mb = QComboBox()
        for k, t in MODES:
            mb.addItem(t, k)
        mb.setCurrentIndex(max(0, mb.findData(d.get("mode", "gps"))))
        self.tp.setCellWidget(r, 6, mb)
        for c, key, rng in ((7, "rot", 180.0), (8, "de", 500.0), (9, "dn", 500.0), (10, "dz", 100.0)):
            sp = QDoubleSpinBox()
            sp.setRange(-rng, rng)
            sp.setDecimals(2)
            sp.setValue(float(d.get(key) or 0.0))
            self.tp.setCellWidget(r, c, sp)

    def _move(self, dd):
        r = self.tp.currentRow()
        t = r + dd
        if r < 0 or t < 0 or t >= self.tp.rowCount():
            return
        a, b = self._row(r), self._row(t)
        self._set_row(r, b)
        self._set_row(t, a)
        self.tp.setCurrentCell(t, 1)

    def _check_crs(self, fn):
        from .smg_insieme import las_epsg
        from qgis.core import QgsCoordinateReferenceSystem
        e = las_epsg(fn)
        if e and self.crs.crs().postgisSrid() != e:
            self.crs.setCrs(QgsCoordinateReferenceSystem("EPSG:%d" % e))
            self.msg.setText("Sistema di riferimento impostato dal file %s: EPSG:%d." % (os.path.basename(fn), e))

    def _new_part_row(self, fn, kind="grotta"):
        r = self.tp.rowCount()
        used = {self._row(i)["label"] for i in range(r) if self._row(i)["use"]}
        lab = next((chr(c) for c in range(ord("A"), ord("Z") + 1) if chr(c) not in used), str(r + 1))
        self._set_row(r, dict(use=True, label=lab, title=os.path.splitext(os.path.basename(fn))[0], las=fn,
                              kind=kind, mode="gps"))
        self._check_crs(fn)

    def _add_part_las(self):
        fns, _ = QFileDialog.getOpenFileNames(self, "LAS delle nuove parti (grotte)", os.path.expanduser("~"), "LAS (*.las)")
        for fn in fns:
            self._new_part_row(fn)

    def _del_part(self):
        rows = sorted({i.row() for i in self.tp.selectedIndexes()}, reverse=True) or \
            ([self.tp.currentRow()] if self.tp.currentRow() >= 0 else [])
        for r in rows:
            self.tp.removeRow(r)

    def _conn_to_part(self):
        rows = sorted({i.row() for i in self.tc.selectedIndexes()}, reverse=True) or \
            ([self.tc.currentRow()] if self.tc.currentRow() >= 0 else [])
        for r in sorted(rows):
            self._new_part_row(self.tc.item(r, 0).text())
        for r in rows:
            self.tc.removeRow(r)

    def _pick_part_las(self):
        r = self.tp.currentRow()
        if r < 0:
            return
        fn, _ = QFileDialog.getOpenFileName(self, "LAS della parte", self.tp.item(r, 3).text() or os.path.expanduser("~"), "LAS (*.las)")
        if fn:
            self.tp.setItem(r, 3, QTableWidgetItem(fn))

    def _pick_out(self):
        d = QFileDialog.getExistingDirectory(self, "Cartella dei risultati", self.out.text() or _out_base())
        if d:
            self.out.setText(d)

    # ------------------------------------------------------------------ collegamenti
    def _add_conn(self):
        fns, _ = QFileDialog.getOpenFileNames(self, "Scansioni di collegamento", os.path.expanduser("~"), "LAS (*.las)")
        for fn in fns:
            r = self.tc.rowCount()
            self.tc.insertRow(r)
            self.tc.setItem(r, 0, QTableWidgetItem(fn))
            self._check_crs(fn)
            self.tc.setItem(r, 1, QTableWidgetItem(os.path.splitext(os.path.basename(fn))[0]))
            cb = QComboBox()
            for k, t in MODES:
                cb.addItem(t, k)
            self.tc.setCellWidget(r, 2, cb)
            for c, rng in ((3, 180.0), (4, 500.0), (5, 500.0), (6, 100.0)):
                sp = QDoubleSpinBox()
                sp.setRange(-rng, rng)
                sp.setDecimals(2)
                self.tc.setCellWidget(r, c, sp)
            self.tc.setItem(r, 7, QTableWidgetItem(""))

    def _del_conn(self):
        rows = sorted({i.row() for i in self.tc.selectedIndexes()}, reverse=True) or \
            ([self.tc.currentRow()] if self.tc.currentRow() >= 0 else [])
        for r in rows:
            self.tc.removeRow(r)

    def _conns(self):
        out = []
        for r in range(self.tc.rowCount()):
            out.append(dict(las=self.tc.item(r, 0).text(), label=self.tc.item(r, 1).text(),
                            mode=self.tc.cellWidget(r, 2).currentData(),
                            rot=self.tc.cellWidget(r, 3).value(), de=self.tc.cellWidget(r, 4).value(),
                            dn=self.tc.cellWidget(r, 5).value(), dz=self.tc.cellWidget(r, 6).value(),
                            perno=self.tc.item(r, 7).text() if self.tc.item(r, 7) else ""))
        return out

    def _parts(self):
        out = []
        for r in range(self.tp.rowCount()):
            d = self._row(r)
            if d.pop("use"):
                d["label"] = d["label"].strip() or "?"
                d["title"] = d["title"].strip()
                d["las"] = d["las"].strip()
                out.append(d)
        return out

    def _progress(self, m):
        self.msg.setText(m)
        QApplication.processEvents()

    def _preview(self):
        """Calcola la posizione delle parti nuove (aggancio, monconi, perno, manuale) e dei collegamenti, nell'ordine
        della tabella; scrive in tabella i valori esatti (posizione 'Manuale') e mostra i contorni sulla mappa."""
        from .smg_insieme import part_info, las_read, place_connection, outline_layer
        import numpy as np
        parts = self._parts()
        movable = [d for d in parts if not d.get("group") and d.get("mode") not in (None, "gps")]
        if not parts or (self.tc.rowCount() == 0 and not movable):
            QMessageBox.information(self, TITLE, "Spunta almeno una parte; l'anteprima serve per le parti nuove con una "
                                    "posizione da calcolare o per le scansioni di collegamento.")
            return
        try:
            QApplication.setOverrideCursor(Qt.WaitCursor)
            root = QgsProject.instance().layerTreeRoot()
            old = root.findGroup("Anteprima rilievo d'insieme")
            if old is not None:
                for n in old.findLayers():
                    QgsProject.instance().removeMapLayer(n.layerId())
                root.removeChildNode(old)
            grp = root.insertGroup(0, "Anteprima rilievo d'insieme")
            lines, tg = [], []

            def show(x, name):
                lyr = outline_layer(x, "%s (anteprima)" % name, self.crs.crs(), "255,140,0,80", "200,90,0")
                if lyr is not None:
                    QgsProject.instance().addMapLayer(lyr, False)
                    grp.addLayer(lyr)
                    _KEEP.append(lyr)

            rows = [r for r in range(self.tp.rowCount()) if self._row(r)["use"]]
            for r in rows:
                d = self._row(r)
                las = d["las"] or (part_info(d["group"], PREFIX)["las"] if d["group"] else "")
                if not las or not os.path.exists(las):
                    raise RuntimeError("Manca il file LAS della parte %s." % d["label"])
                self._progress("Lettura %s..." % os.path.basename(las))
                L = las_read(las)
                if not d["group"] and d.get("mode") not in (None, "gps"):
                    mode = d["mode"]
                    if mode in ("aggancia", "monconi") and not tg:
                        mode = "manuale"
                    targets = np.vstack(tg) if tg else None
                    self._progress("Posizione della parte %s..." % d["label"])
                    x, how, vals = place_connection(L, dict(d, mode=mode), targets, progress=self._progress)
                    lines.append("Parte %s: %s" % (d["label"], how))
                    self.tp.cellWidget(r, 6).setCurrentIndex(self.tp.cellWidget(r, 6).findData("manuale"))
                    for col, val in zip((7, 8, 9, 10), vals):
                        self.tp.cellWidget(r, col).setValue(val)
                    show(x, d["title"] or d["label"])
                else:
                    x = L["xyz"]
                tg.append(x[:: max(1, len(x) // 800000)])
            for r, c in enumerate(self._conns()):
                self._progress("Posizione di %s..." % os.path.basename(c["las"]))
                L = las_read(c["las"])
                x, how, vals = place_connection(L, c, np.vstack(tg) if tg else None, progress=self._progress)
                tg.append(x[:: max(1, len(x) // 400000)])
                lines.append("%s: %s" % (c["label"], how))
                if c["mode"] != "gps":                      # da qui in poi i valori sono esatti
                    self.tc.cellWidget(r, 2).setCurrentIndex(self.tc.cellWidget(r, 2).findData("manuale"))
                    for col, val in zip((3, 4, 5, 6), vals):
                        self.tc.cellWidget(r, col).setValue(val)
                show(x, c["label"])
            self.msg.setText("Anteprima pronta (gruppo 'Anteprima rilievo d'insieme' sulla mappa). I valori in tabella "
                             "sono ora quelli esatti (posizione 'Manuale').\n" + "\n".join(lines) +
                             "\nSe la posizione va bene premi 'Crea'; altrimenti correggi i valori e ripeti.")
        except Exception as e:
            self.msg.setText("Anteprima non riuscita: %s" % e)
        finally:
            QApplication.restoreOverrideCursor()

    # ------------------------------------------------------------------ esecuzione
    def _run(self):
        from .smg_insieme import build_all
        parts = self._parts()
        if len(parts) < 1:
            QMessageBox.information(self, TITLE, "Spunta almeno una parte.")
            return
        name = self.name.text().strip() or "Rilievo d'insieme"
        out = self.out.text().strip() or os.path.join(_out_base(), name.replace(" ", "_").replace("'", ""))
        st = QgsSettings()
        st.setValue(SK + "ins_name", name)
        st.setValue(SK + "ins_out", self.out.text().strip())
        meta = dict(group=st.value(SK + "group", "Gruppo speleologico"), logo=st.value(SK + "logo", ""),
                    oggetto="Pianta d'insieme - Profili - Sezioni")
        info = []
        if st.value(SK + "authors", ""):
            info.append("Rilevatori: %s - Elaborazione grafica: %s" % (st.value(SK + "authors", ""), st.value(SK + "drafter", "")))
        part_defaults = dict(authors=st.value(SK + "authors", ""), group=meta["group"], logo=meta["logo"],
                             drafter=st.value(SK + "drafter", ""), instruments=st.value(SK + "instruments", "Scansione LiDAR"),
                             grade=st.value(SK + "grade", ""), basemap=st.value(SK + "terrain_base", "esri"))
        cfg = dict(name=name, out_dir=out, crs=self.crs.crs(), prefix=PREFIX, parts=parts, connections=self._conns(),
                   part_defaults=part_defaults, level_parts=self.lv_parts.text().strip(),
                   level_z=(self.lv_z.text().strip().replace(",", ".") or None),
                   pdf=self.c_pdf.isChecked(), las=self.c_las.isChecked(), viewer3d=self.c_3d.isChecked(),
                   meta=meta, info_extra=info)
        from .smg_insieme import las_epsg
        want = self.crs.crs().postgisSrid()
        diff = sorted({(os.path.basename(q["las"]), e) for q in parts + cfg["connections"]
                       for e in [las_epsg(q["las"]) if q.get("las") and os.path.exists(q["las"]) else None]
                       if e and e != want})
        if diff:
            if QMessageBox.question(self, TITLE, "Questi file dichiarano un sistema diverso da %s:\n%s\n\nUsare comunque %s?"
                                    % (self.crs.crs().authid(), "\n".join("  %s: EPSG:%d" % d for d in diff), self.crs.crs().authid()),
                                    QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
                return
            cfg["crs_confirmed"] = True
        self.b_run.setEnabled(False)
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            res = build_all(cfg, progress=self._progress)
            _KEEP.extend(res.get("keep", []))
            lines = ["Rilievo d'insieme creato in %s" % out]
            for k, t in (("pdf", "Tavole PDF"), ("las", "LAS d'insieme"), ("viewer3d", "Vista 3D")):
                if res.get(k):
                    lines.append("%s: %s" % (t, os.path.basename(res[k])))
            for c in res.get("connections", []):
                lines.append("%s: %s" % (os.path.basename(c["las"]), c["how"]))
            if res.get("tour_length"):
                lines.append("Giro 3D: %.0f m" % res["tour_length"])
            self.msg.setText("\n".join(lines))
            QMessageBox.information(self, TITLE, "\n".join(lines))
        except Exception as e:
            self.msg.setText("Errore: %s" % e)
            QMessageBox.warning(self, TITLE, "%s\n\n%s" % (e, traceback.format_exc()[-1500:]))
        finally:
            QApplication.restoreOverrideCursor()
            self.b_run.setEnabled(True)
