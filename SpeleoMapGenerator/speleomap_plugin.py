from qgis.PyQt.QtWidgets import QAction
from qgis.PyQt.QtGui import QIcon
import os

from .speleomap_dialog import SpeleoMapDialog

MENU = "SpeleoMap Generator"


class SpeleoMapPlugin:
    def __init__(self, iface):
        self.iface = iface
        self.action = None
        self.action_ins = None
        self.action_tr = None
        self.dlg_tr = None

    def initGui(self):
        icon_path = os.path.join(os.path.dirname(__file__), "icon.png")
        self.action = QAction(QIcon(icon_path), "Generate Cave Map", self.iface.mainWindow())
        self.action.triggered.connect(self.run)
        self.iface.addPluginToMenu(MENU, self.action)
        self.action_ins = QAction(QIcon(icon_path), "Rilievo d'insieme (piu' grotte e collegamenti)", self.iface.mainWindow())
        self.action_ins.triggered.connect(self.run_insieme)
        self.iface.addPluginToMenu(MENU, self.action_ins)
        self.action_tr = QAction(QIcon(icon_path), "Sezioni e profili a scelta (tracce disegnate)", self.iface.mainWindow())
        self.action_tr.triggered.connect(self.run_tracce)
        self.iface.addPluginToMenu(MENU, self.action_tr)

    def unload(self):
        if self.dlg_tr is not None:
            self.dlg_tr.close()
            self.dlg_tr = None
        for a in (self.action, self.action_ins, self.action_tr):
            if a:
                self.iface.removePluginMenu(MENU, a)
        self.action = self.action_ins = self.action_tr = None

    def run(self):
        dlg = SpeleoMapDialog(self.iface)
        dlg.exec_()

    def run_insieme(self):
        from .smg_insieme_dialog import InsiemeDialog
        dlg = InsiemeDialog(self.iface)
        dlg.exec_()

    def run_tracce(self):
        # finestra non modale: resta aperta mentre si disegnano le tracce in QGIS
        from .smg_tracce_dialog import TracceDialog
        if self.dlg_tr is None:
            self.dlg_tr = TracceDialog(self.iface)
        self.dlg_tr._pick_defaults()
        self.dlg_tr.show()
        self.dlg_tr.raise_()
        self.dlg_tr.activateWindow()
