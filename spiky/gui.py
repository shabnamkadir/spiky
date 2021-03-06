from galry import *
from views import *
import tools
from dataio import MockDataProvider

SETTINGS = tools.init_settings()

__all__ = ['SpikyMainWindow']

def get_default_widget_controller():
    vbox = QtGui.QVBoxLayout()
    
    return vbox


class VisualizationWidget(QtGui.QWidget):
    def __init__(self, dataholder):
        super(VisualizationWidget, self).__init__()
        self.dataholder = dataholder
        self.view = self.create_view(dataholder)
        self.controller = self.create_controller()
        self.initialize()

    def create_view(self, dataholder):
        """Create the view and return it.
        The view must be an instance of a class deriving from `QWidget`.
        
        To be overriden."""
        return None

    def create_controller(self):
        """Create the controller and return it.
        The controller must be an instance of a class deriving from `QLayout`.
        
        To be overriden."""
        
        # horizontal layout for the controller
        hbox = QtGui.QHBoxLayout()
        
        # we add the "isolated" checkbox
        self.isolated_control = QtGui.QCheckBox("isolated")
        hbox.addWidget(self.isolated_control, stretch=1, alignment=QtCore.Qt.AlignLeft)
        
        self.reset_view_control = QtGui.QPushButton("reset view")
        hbox.addWidget(self.reset_view_control, stretch=1, alignment=QtCore.Qt.AlignLeft)
        
        # hbox.addWidget(QtGui.QCheckBox("test"), stretch=1, alignment=QtCore.Qt.AlignLeft)
        hbox.addStretch(10)
        
        return hbox
        
    def initialize(self):
        """Initialize the user interface.
        
        By default, add the controller at the top, and the view at the bottom.
        
        To be overriden."""
        # put the controller and the view vertically
        vbox = QtGui.QVBoxLayout()
        # add the controller (which must be a layout)
        vbox.addLayout(self.controller)
        # add the view (which must be a widget, typically deriving from
        # GalryWidget)
        vbox.addWidget(self.view)
        # set the VBox as layout of the widget
        self.setLayout(vbox)
        

        
        
class WaveformWidget(VisualizationWidget):
    def create_view(self, dh):
        view = WaveformView()
        view.set_data(dh.waveforms,
                      clusters=dh.clusters,
                      cluster_colors=dh.clusters_info.colors,
                      geometrical_positions=dh.probe.positions,
                      masks=dh.masks)
        return view

    
    
    
class FeatureWidget(VisualizationWidget):
    def create_view(self, dh):
        view = FeatureView()
        view.set_data(dh.features, clusters=dh.clusters,
                      fetdim=3,
                      cluster_colors=dh.clusters_info.colors,
                      masks=dh.masks)
        return view

    def create_controller(self):
        box = super(FeatureWidget, self).create_controller()
        # box.addWidget(QtGui.QCheckBox("hi"))
        return box
    
    
class CorrelogramsWidget(VisualizationWidget):
    def create_view(self, dh):
        view = CorrelogramsView()
        view.set_data(histograms=dh.correlograms,
                        # nclusters=dh.nclusters,
                        cluster_colors=dh.clusters_info.colors)
        return view

    
    
    
class CorrelationMatrixWidget(VisualizationWidget):
    def create_view(self, dh):
        view = CorrelationMatrixView()
        view.set_data(dh.correlationmatrix)
        return view




class SpikyMainWindow(QtGui.QMainWindow):
    def __init__(self):
        super(SpikyMainWindow, self).__init__()
        
        self.setAnimated(False)
        self.setTabPosition(
            QtCore.Qt.LeftDockWidgetArea |
            QtCore.Qt.RightDockWidgetArea |
            QtCore.Qt.TopDockWidgetArea |
            QtCore.Qt.BottomDockWidgetArea
            ,
            QtGui.QTabWidget.North)
        
        # load mock data
        provider = MockDataProvider()
        self.dh = provider.load(nspikes=100)
        
        self.setDockNestingEnabled(True)
        
        # self.add_dock(FeatureWidget, QtCore.Qt.RightDockWidgetArea)
        self.add_central(FeatureWidget)
        
        self.add_dock(WaveformWidget, QtCore.Qt.LeftDockWidgetArea)
        
        self.add_dock(CorrelogramsWidget, QtCore.Qt.RightDockWidgetArea)
        self.add_dock(CorrelationMatrixWidget, QtCore.Qt.RightDockWidgetArea)

        
        self.restore_geometry()
        
        self.show()

    def add_dock(self, widget_class, position, name=None, minsize=None):
        if name is None:
            name = widget_class.__name__
            
        widget = widget_class(self.dh)
        if minsize is not None:
            widget.setMinimumSize(*minsize)
        
        dockwidget = QtGui.QDockWidget(name)
        dockwidget.setObjectName(name)
        
        dockwidget.setWidget(widget)
        dockwidget.setFeatures(QtGui.QDockWidget.DockWidgetFloatable | \
            QtGui.QDockWidget.DockWidgetMovable)
            
        self.addDockWidget(position, dockwidget)
        
    def add_central(self, widget_class, name=None, minsize=None):
        if name is None:
            name = widget_class.__name__
            
        widget = widget_class(self.dh)
        widget.setObjectName(name)
        if minsize is not None:
            widget.setMinimumSize(*minsize)
        
        self.setWindowTitle("Spiky")
        
        self.setCentralWidget(widget)
        
        
    def save_geometry(self):
        SETTINGS.set("mainWindow/geometry", self.saveGeometry())
        SETTINGS.set("mainWindow/windowState", self.saveState())
        
    def restore_geometry(self):
        g = SETTINGS.get("mainWindow/geometry")
        w = SETTINGS.get("mainWindow/windowState")
        if g:
            self.restoreGeometry(g)
        if w:
            self.restoreState(w)
        
        
        
        
    def closeEvent(self, e):
        self.save_geometry()
        super(SpikyMainWindow, self).closeEvent(e)


if __name__ == '__main__':
    window = show_window(SpikyMainWindow)


