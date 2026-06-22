from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtWidgets import QAction
from qgis.core import QgsApplication
import os


class GeoScatterPlugin:
    def __init__(self, iface):
        self.iface = iface
        self.dock = None
        self.action = None

    def initGui(self):
        icon_path = os.path.join(os.path.dirname(__file__), "icon.png")
        self.action = QAction(QIcon(icon_path), "GeoScatter", self.iface.mainWindow())
        self.action.setCheckable(True)
        self.action.triggered.connect(self.toggle_dock)
        self.iface.addToolBarIcon(self.action)
        self.iface.addPluginToMenu("GeoScatter", self.action)

    def unload(self):
        self.iface.removeToolBarIcon(self.action)
        self.iface.removePluginMenu("GeoScatter", self.action)
        if self.dock is not None:
            self.iface.removeDockWidget(self.dock)
            self.dock.deleteLater()
            self.dock = None

    def toggle_dock(self, checked):
        if checked:
            self._ensure_dock()
            self.dock.show()
        else:
            if self.dock is not None:
                self.dock.hide()

    def _ensure_dock(self):
        if self.dock is None:
            from .scatter_dock import ScatterDock
            from qgis.PyQt.QtCore import Qt
            self.dock = ScatterDock(self.iface, self.iface.mainWindow())
            self.dock.visibilityChanged.connect(self._on_dock_visibility_changed)
            self.iface.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.dock)

    def _on_dock_visibility_changed(self, visible):
        self.action.setChecked(visible)
