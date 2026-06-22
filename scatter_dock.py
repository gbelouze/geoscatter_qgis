import os
import numpy as np

from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtWidgets import (
    QDockWidget, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QComboBox, QLabel, QFileDialog, QSizePolicy,
)
from qgis.core import (
    QgsPointXY, QgsCoordinateReferenceSystem, QgsCoordinateTransform, QgsProject,
)

from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.backends.backend_qt import NavigationToolbar2QT as NavigationToolbar
from matplotlib.figure import Figure


SUPPORTED_FILTERS = "Geo files (*.parquet *.gpkg *.geojson *.json);;All files (*)"


class ScatterDock(QDockWidget):
    def __init__(self, iface, parent=None):
        super().__init__("GeoScatter", parent)
        self.iface = iface
        self._gdf = None          # current GeoDataFrame
        self._scatter = None      # matplotlib PathCollection
        self._src_crs = None      # CRS of loaded data

        self.setMinimumWidth(400)
        self._build_ui()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self):
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(4)

        # File picker row
        file_row = QHBoxLayout()
        self._file_label = QLabel("No file loaded")
        self._file_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self._file_label.setWordWrap(False)
        open_btn = QPushButton("Open…")
        open_btn.setFixedWidth(64)
        open_btn.clicked.connect(self._on_open)
        file_row.addWidget(self._file_label)
        file_row.addWidget(open_btn)
        layout.addLayout(file_row)

        # Column selectors
        col_row = QHBoxLayout()
        col_row.addWidget(QLabel("X (prediction):"))
        self._pred_combo = QComboBox()
        self._pred_combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        col_row.addWidget(self._pred_combo)
        col_row.addWidget(QLabel("Y (label):"))
        self._label_combo = QComboBox()
        self._label_combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        col_row.addWidget(self._label_combo)
        plot_btn = QPushButton("Plot")
        plot_btn.setFixedWidth(48)
        plot_btn.clicked.connect(self._on_plot)
        col_row.addWidget(plot_btn)
        layout.addLayout(col_row)

        # Matplotlib canvas + native navigation toolbar
        self._figure = Figure(tight_layout=True)
        self._ax = self._figure.add_subplot(111)
        self._canvas = FigureCanvas(self._figure)
        self._canvas.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._canvas.mpl_connect("pick_event", self._on_pick)
        self._toolbar = NavigationToolbar(self._canvas, container)
        layout.addWidget(self._toolbar)
        layout.addWidget(self._canvas)

        # Status bar
        self._status = QLabel("")
        self._status.setAlignment(Qt.AlignmentFlag.AlignLeft)
        layout.addWidget(self._status)

        self.setWidget(container)

    # ------------------------------------------------------------------
    # Slots
    # ------------------------------------------------------------------

    def _on_open(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Open geo data file", "", SUPPORTED_FILTERS
        )
        if not path:
            return
        self._load_file(path)

    def _load_file(self, path):
        try:
            import geopandas as gpd
        except ImportError:
            self._status.setText("Error: geopandas is not installed in QGIS's Python.")
            return

        try:
            if path.endswith(".parquet"):
                gdf = gpd.read_parquet(path)
            else:
                gdf = gpd.read_file(path)
        except Exception as exc:
            self._status.setText(f"Error loading file: {exc}")
            return

        if gdf.geometry is None or gdf.geometry.isna().all():
            self._status.setText("Error: file has no geometry column.")
            return

        self._gdf = gdf
        self._src_crs = gdf.crs

        # Populate column combos with numeric columns only
        numeric_cols = [
            c for c in gdf.columns
            if c != gdf.geometry.name and np.issubdtype(gdf[c].dtype, np.number)
        ]
        for combo in (self._pred_combo, self._label_combo):
            combo.blockSignals(True)
            combo.clear()
            combo.addItems(numeric_cols)
            combo.blockSignals(False)

        # Heuristic defaults: prefer columns whose names hint at pred/label
        self._pick_default(self._pred_combo, numeric_cols, ["pred", "score", "output", "y_pred", "yhat"])
        self._pick_default(self._label_combo, numeric_cols, ["label", "target", "gt", "y_true", "class", "y"])

        short = os.path.basename(path)
        self._file_label.setText(short)
        self._status.setText(f"Loaded {len(gdf):,} features  |  CRS: {self._src_crs}")

        # Auto-plot if defaults were resolved
        if self._pred_combo.currentText() != self._label_combo.currentText():
            self._on_plot()

    def _pick_default(self, combo, cols, hints):
        for hint in hints:
            for i, col in enumerate(cols):
                if hint.lower() in col.lower():
                    combo.setCurrentIndex(i)
                    return

    def _on_plot(self):
        if self._gdf is None:
            return
        pred_col = self._pred_combo.currentText()
        label_col = self._label_combo.currentText()
        if not pred_col or not label_col:
            return

        gdf = self._gdf.dropna(subset=[pred_col, label_col])
        x = gdf[pred_col].to_numpy()
        y = gdf[label_col].to_numpy()

        self._ax.clear()
        self._scatter = self._ax.scatter(
            x, y,
            s=12, alpha=0.6, linewidths=0,
            picker=True, pickradius=6,
        )
        # identity line
        lo, hi = min(x.min(), y.min()), max(x.max(), y.max())
        self._ax.plot([lo, hi], [lo, hi], "r--", linewidth=0.8, zorder=0)
        self._ax.set_xlabel(pred_col)
        self._ax.set_ylabel(label_col)
        self._ax.set_title("Prediction vs label")

        # Store index mapping: scatter point index → original gdf index
        self._plotted_index = gdf.index.to_numpy()

        self._canvas.draw()
        self._status.setText(f"Plotted {len(x):,} points  |  click a point to navigate")

    def _on_pick(self, event):
        if event.artist is not self._scatter:
            return
        indices = event.ind
        if len(indices) == 0:
            return

        # Use the first picked point
        row_idx = self._plotted_index[indices[0]]
        geom = self._gdf.loc[row_idx, "geometry"]
        if geom is None or geom.is_empty:
            return

        point = geom.centroid  # works for Point, but also Polygon centroids
        self._pan_to(point.x, point.y)

    def _pan_to(self, x, y):
        canvas = self.iface.mapCanvas()
        map_crs = canvas.mapSettings().destinationCrs()

        pt = QgsPointXY(x, y)
        if self._src_crs and self._src_crs.to_epsg() is not None:
            src = QgsCoordinateReferenceSystem(f"EPSG:{self._src_crs.to_epsg()}")
            if src.isValid() and src != map_crs:
                transform = QgsCoordinateTransform(src, map_crs, QgsProject.instance())
                pt = transform.transform(pt)

        canvas.setCenter(pt)
        canvas.refresh()
        self._status.setText(f"Navigated to ({x:.6f}, {y:.6f})")
