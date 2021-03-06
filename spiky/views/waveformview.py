import numpy as np
import numpy.random as rdn
import collections
import operator
import time

from galry import *
from common import *

__all__ = ['WaveformView']


VERTEX_SHADER = """
    // get channel position
    vec2 channel_position = channel_positions[int(channel)];
    
    // get the box position
    vec2 box_position = channel_position;
    
    // take probe scaling into account
    box_position *= probe_scale;
    
    // adjust box position in the separated case
    if (!superimposed)
    {
        box_position.x += box_size_margin.x * (0.5 + cluster - 0.5 * nclusters);
    }
    
    // move the vertex to its position0
    vec2 position = position0 * 0.5 * box_size + box_position;
    
    // compute the color: cluster color and mask for the transparency
    varying_color.xyz = cluster_colors[int(cluster)];
    varying_color.w = mask;
    
    // highlighting: change color, not transparency
    // HACK: when OLDGLSL is enabled, highlight is not a bool but a number
    // because attributes cannot be bools. so this test works in both cases
    if (highlight > 0)
        varying_color = vec4(1, 1, 1, varying_color.w);
"""
        
FRAGMENT_SHADER = """
    out_color = varying_color;
"""

HIGHLIGHT_CLOSE_BOXES_COUNT = 4

WaveformSpatialArrangement = enum("Linear", "Geometrical")
WaveformSuperposition = enum("Superimposed", "Separated")
WaveformEventEnum = enum(
    "ToggleSuperpositionEvent", 
    "ToggleSpatialArrangementEvent",
    "ChangeBoxScaleEvent",
    "ChangeProbeScaleEvent",
    "HighlightSpikeEvent",
    )
    


    
    
class WaveformHighlightManager(HighlightManager):
    def initialize(self):
        """Set info from the data manager."""
        super(WaveformHighlightManager, self).initialize()
        data_manager = self.data_manager
        self.get_data_position = self.data_manager.get_data_position
        self.full_masks = self.data_manager.full_masks
        self.clusters_rel = self.data_manager.clusters_rel
        self.cluster_colors = self.data_manager.cluster_colors
        self.nchannels = data_manager.nchannels
        self.nclusters = data_manager.nclusters
        self.nsamples = data_manager.nsamples
        self.nspikes = data_manager.nspikes
        self.npoints = data_manager.npoints
        self.get_data_position = data_manager.get_data_position
        self.highlighted_spikes = []
        self.highlight_mask = np.zeros(self.npoints, dtype=np.int32)

    def find_enclosed_spikes(self, enclosing_box):
        x0, y0, x1, y1 = enclosing_box
        
        # press_position
        xp, yp = x0, y0

        # reorder
        xmin, xmax = min(x0, x1), max(x0, x1)
        ymin, ymax = min(y0, y1), max(y0, y1)

        # transformation
        box_positions, box_size = self.position_manager.get_transformation()
        Tx, Ty = box_positions
        w, h = box_size
        a, b = w / 2, h / 2
        
        # find the enclosed channels and clusters
        sx, sy = self.interaction_manager.sx, self.interaction_manager.sy
        dist = (np.abs(Tx - xp) * sx) ** 2 + (np.abs(Ty - yp) * sy) ** 2
        # find the K closest boxes, with K at least HIGHLIGHT_CLOSE_BOXES_COUNT
        # or nclusters (so that all spikes are selected in superimposed mode
        closest = np.argsort(dist.ravel())[:HIGHLIGHT_CLOSE_BOXES_COUNT]
        
        spkindices = []
        for index in closest:
            # find the channel and cluster of this close box
            channel, cluster_rel = index // self.nclusters, np.mod(index, self.nclusters)
            # find the position of the points in the data buffer
            start, end = self.get_data_position(channel, cluster_rel)
            
            # offset
            u = Tx[channel, cluster_rel]
            v = Ty[channel, cluster_rel]

            # original data
            waveforms = self.data_manager.normalized_data[start:end,:]
            masks = self.full_masks[start:end]

            # get the waveforms and masks
            # waveforms = positioned_data[start:end,:]
            # find the indices of the points in the enclosed box
            # inverse transformation of x => ax+u, y => by+v
            indices = ((masks > 0) & \
                      (waveforms[:,0] >= (xmin-u)/a) & (waveforms[:,0] <= (xmax-u)/a) & \
                      (waveforms[:,1] >= (ymin-v)/b) & (waveforms[:,1] <= (ymax-v)/b))
            # absolute indices in the data
            indices = np.nonzero(indices)[0] + start
            # spike indices, independently of the channel
            spkindices.append(np.mod(indices, self.nspikes * self.nsamples) // self.nsamples)        
        spkindices = np.hstack(spkindices)
        spkindices = np.unique(spkindices)
        spkindices.sort()
        return spkindices

    def find_indices_from_spikes(self, spikes):
        if spikes is None or len(spikes)==0:
            return None
        n = len(spikes)
        # find point indices in the data buffer corresponding to 
        # the selected spikes. In particular, waveforms of those spikes
        # across all channels should be selected as well.
        spikes = np.array(spikes, dtype=np.int32)
        ind = np.repeat(spikes * self.nsamples, self.nsamples)
        ind += np.tile(np.arange(self.nsamples), n)
        ind = np.tile(ind, self.nchannels)
        ind += np.repeat(np.arange(self.nchannels) * self.nsamples * self.nspikes,
                                self.nsamples * n)
        return ind
        
    def set_highlighted_spikes(self, spikes):
        """Update spike colors to mark transiently selected spikes with
        a special color."""
        if len(spikes) == 0:
            # do update only if there were previously selected spikes
            do_update = len(self.highlighted_spikes) > 0
            self.highlight_mask[:] = 0
        else:
            do_update = True
            ind = self.find_indices_from_spikes(spikes)
        
            self.highlight_mask[:] = 0
            self.highlight_mask[ind] = 1
        
        if do_update:
            self.paint_manager.set_data(
                highlight=self.highlight_mask,
                dataset=self.paint_manager.ds_waveforms)
        
        self.highlighted_spikes = spikes
        
    def highlighted(self, box):
        # get selected spikes
        spikes = self.find_enclosed_spikes(box) 
        
        # update the data buffer
        self.set_highlighted_spikes(spikes)
                      
    def cancel_highlight(self):
        super(WaveformHighlightManager, self).cancel_highlight()
        self.set_highlighted_spikes([])
    
    
class WaveformPositionManager(object):
    # Initialization methods
    # ----------------------
    def __init__(self):
        # set parameters
        self.alpha = .02
        self.beta = .02
        self.box_size_min = .01
        self.probe_scale_min = .01
        self.probe_scale = (1., 1.)
        # for each spatial arrangement, the box sizes automatically computed,
        # or modified by the user
        self.box_sizes = dict()
        self.box_sizes.__setitem__(WaveformSpatialArrangement.Linear, None)
        self.box_sizes.__setitem__(WaveformSpatialArrangement.Geometrical, None)
        # self.T = None
        self.spatial_arrangement = WaveformSpatialArrangement.Linear
        self.superposition = WaveformSuperposition.Separated
        
        # channel positions
        self.channel_positions = {}
        
    def normalize_channel_positions(self, spatial_arrangement, channel_positions):
        
        channel_positions = channel_positions.copy()
        
        # waveform data bounds
        xmin = channel_positions[:,0].min()
        xmax = channel_positions[:,0].max()
        ymin = channel_positions[:,1].min()
        ymax = channel_positions[:,1].max()
        
        w, h = self.find_box_size(spatial_arrangement=spatial_arrangement)
    
        if xmin == xmax:
            ax = 0.
        else:
            ax = (2 - self.nclusters * w * (1 + 2 * self.alpha)) / (xmax - xmin)
        if ymin == ymax:
            ay = 0.
        else:
            ay = (2 - h * (1 + 2 * self.alpha)) / (ymax - ymin)
        
        # set bx and by to have symmetry
        bx = -.5 * ax * (xmax + xmin)
        by = -.5 * ay * (ymax + ymin)
        
        # transform the boxes positions so that everything fits on the screen
        channel_positions[:,0] = ax * channel_positions[:,0] + bx
        channel_positions[:,1] = ay * channel_positions[:,1] + by
        
        return enforce_dtype(channel_positions, np.float32)
    
    def get_channel_positions(self):
        return self.channel_positions[self.spatial_arrangement]
    
    def set_info(self, nchannels, nclusters, 
                       geometrical_positions=None):
        """Specify the information needed to position the waveforms in the
        widget.
        
          * nchannels: number of channels
          * nclusters: number of clusters
          * coordinates of the electrodes
          
        """
        self.nchannels = nchannels
        self.nclusters = nclusters
        # HEURISTIC
        self.diffxc, self.diffyc = [np.sqrt(float(self.nchannels))] * 2
        
        linear_positions = np.zeros((self.nchannels, 2), dtype=np.float32)
        linear_positions[:,1] = np.linspace(1., -1., self.nchannels)
        
        # default geometrical position
        if geometrical_positions is None:
            geometrical_positions = linear_positions.copy()
                         
        # normalize and save channel position
        self.channel_positions[WaveformSpatialArrangement.Linear] = \
            self.normalize_channel_positions(WaveformSpatialArrangement.Linear, linear_positions)
        self.channel_positions[WaveformSpatialArrangement.Geometrical] = \
            self.normalize_channel_positions(WaveformSpatialArrangement.Geometrical, geometrical_positions)
              
        
        # set waveform positions
        self.update_arrangement()
        
    def update_arrangement(self, spatial_arrangement=None, superposition=None,
                                 box_size=None, probe_scale=None):
        """Update the waveform arrangement (self.channel_positions).
        
          * spatial_arrangement: WaveformSpatialArrangement enum, Linear or Geometrical
          * superposition: WaveformSuperposition enum, Superimposed or Separated
          
        """
        # save spatial arrangement
        if spatial_arrangement is not None:
            self.spatial_arrangement = spatial_arrangement
        if superposition is not None:
            self.superposition = superposition
        
        # save box size
        if box_size is not None:
            self.save_box_size(*box_size)
        
        # save probe scale
        if probe_scale is not None:
            self.probe_scale = probe_scale
        
        # retrieve info
        channel_positions = self.channel_positions[self.spatial_arrangement]
        
        w, h = self.load_box_size()
        
        # update translation vector
        # order: cluster, channel
        T = np.repeat(channel_positions, self.nclusters, axis=0)
        Tx = np.reshape(T[:,0], (self.nchannels, self.nclusters))
        Ty = np.reshape(T[:,1], (self.nchannels, self.nclusters))
        
        # take probe scale into account
        psx, psy = self.probe_scale
        Tx *= psx
        Ty *= psy
        
        # shift in the separated case
        if self.superposition == WaveformSuperposition.Separated:
            clusters = np.tile(np.arange(self.nclusters), (self.nchannels, 1))
            Tx += w * (1 + 2 * self.alpha) * \
                                    (.5 + clusters - self.nclusters / 2.)
        
        # record box positions and size
        self.box_positions = Tx, Ty
        self.box_size = (w, h)
                      
    def get_transformation(self):
        return self.box_positions, self.box_size
                      
    # Internal methods
    # ----------------
    def save_box_size(self, w, h, arrangement=None):
        if arrangement is None:
            arrangement = self.spatial_arrangement
        self.box_sizes[arrangement] = (w, h)

    def load_box_size(self, arrangement=None):
        if arrangement is None:
            arrangement = self.spatial_arrangement
        if self.box_sizes[arrangement] is None:
            self.find_box_size()
        return self.box_sizes[arrangement]
    
    def find_box_size(self, spatial_arrangement=None, superposition=None):
        do_save = (spatial_arrangement is None) and (superposition is None)
        if spatial_arrangement is None:
            spatial_arrangement = self.spatial_arrangement
        if superposition is None:
            superposition = self.superposition
            
        if spatial_arrangement == WaveformSpatialArrangement.Linear:
            if superposition == WaveformSuperposition.Superimposed:
                w = 2./(1+2*self.alpha)
                h = 2./(self.nchannels*(1+self.beta))
            elif superposition == WaveformSuperposition.Separated:
                w = 2./(self.nclusters*(1+2*self.alpha))
                h = 2./(self.nchannels*(1+2*self.beta))
        elif spatial_arrangement == WaveformSpatialArrangement.Geometrical:
            if superposition == WaveformSuperposition.Superimposed:
                w = 2./(self.diffxc*(1+2*self.beta))
                h = 2./(self.diffyc*(1+2*self.beta))
            elif superposition == WaveformSuperposition.Separated:
                w = 2./((1+2*self.alpha)*(1+2*self.beta)*self.nclusters*
                                self.diffxc)
                h = 2./((1+2*self.beta)*self.diffyc)
        if do_save:
            self.save_box_size(w, h)
        return w, h
        
    # Interactive update methods
    # --------------------------
    def change_box_scale(self, dsx, dsy):
        w, h = self.load_box_size()
        w = max(self.box_size_min, w + dsx)
        h = max(self.box_size_min, h + dsy)
        self.update_arrangement(box_size=(w,h))
        self.paint_manager.auto_update_uniforms("box_size", "box_size_margin")
        
    def change_probe_scale(self, dsx, dsy):
        # w, h = self.load_box_size()
        sx, sy = self.probe_scale
        sx = max(self.probe_scale_min, sx + dsx)
        sy = max(self.probe_scale_min, sy + dsy)
        self.update_arrangement(probe_scale=(sx, sy))
        self.paint_manager.auto_update_uniforms("probe_scale")
        
    def toggle_superposition(self):
        # switch superposition
        if self.superposition == WaveformSuperposition.Separated:
            self.superposition = WaveformSuperposition.Superimposed
        else:
            self.superposition = WaveformSuperposition.Separated
        # recompute the waveforms positions
        self.update_arrangement(superposition=self.superposition,
                                spatial_arrangement=self.spatial_arrangement)
        self.paint_manager.auto_update_uniforms("superimposed", "box_size", "box_size_margin")

    def toggle_spatial_arrangement(self):
        # switch spatial arrangement
        if self.spatial_arrangement == WaveformSpatialArrangement.Linear:
            self.spatial_arrangement = WaveformSpatialArrangement.Geometrical
        else:
            self.spatial_arrangement = WaveformSpatialArrangement.Linear
        # recompute the waveforms positions
        self.update_arrangement(superposition=self.superposition,
                                spatial_arrangement=self.spatial_arrangement)
        self.paint_manager.auto_update_uniforms("channel_positions", "box_size", "box_size_margin")
        
    # Get methods
    # -----------
    def get_viewbox(self, channels):
        """Return the smallest viewbox such that the selected channels are
        visible.
        """
        channels = np.array(channels)
        pos = self.box_positions[channels,:]
        # find the box enclosing all channels center positions
        xmin, ymin = np.min(pos, axis=0)
        xmax, ymax = np.max(pos, axis=0)
        # take the size of the individual boxes into account
        mx = self.w * (.5 + self.alpha)
        my = self.h * (.5 + self.alpha)
        xmin -= self.w * mx
        xmax += self.w * mx
        ymin -= self.h * my
        ymax += self.h * my
        return xmin, ymin, xmax, ymax
        

class WaveformDataManager(object):
    # Initialization methods
    # ----------------------
    def set_data(self, waveforms, clusters=None, cluster_colors=None,
                 masks=None, geometrical_positions=None, spike_ids=None):
        """
        waveforms is a Nspikes x Nsamples x Nchannels array.
        clusters is a Nspikes array, with the cluster absolute index for each
                    spike
        cluster_colors is a Nclusters x 3 array (RGB components)
            cluster_colors[i] is the color of cluster #i where i is the RELATIVE
            index
        masks is a Nspikes x Nchannels array (with values in [0,1])
        spike_ids is a Nspikes array, it contains the absolute indices of spikes
        """
        
        self.nspikes, self.nsamples, self.nchannels = waveforms.shape
        self.npoints = waveforms.size
        self.geometrical_positions = geometrical_positions
        self.spike_ids = spike_ids
        self.waveforms = waveforms
        
        # data organizer: reorder data according to clusters
        self.data_organizer = SpikeDataOrganizer(waveforms,
                                                clusters=clusters,
                                                cluster_colors=cluster_colors,
                                                masks=masks,
                                                nchannels=self.nchannels,
                                                spike_ids=spike_ids)
        
        # get reordered data
        self.permutation = self.data_organizer.permutation
        self.waveforms_reordered = self.data_organizer.data_reordered
        self.nclusters = self.data_organizer.nclusters
        self.clusters = self.data_organizer.clusters
        self.masks = self.data_organizer.masks
        self.cluster_colors = self.data_organizer.cluster_colors
        self.clusters_unique = self.data_organizer.clusters_unique
        self.clusters_rel = self.data_organizer.clusters_rel
        self.cluster_sizes = self.data_organizer.cluster_sizes
        self.cluster_sizes_cum = self.data_organizer.cluster_sizes_cum
        self.cluster_sizes_dict = self.data_organizer.cluster_sizes_dict
        
        # prepare GPU data: waveform initial positions and colors
        data = self.prepare_waveform_data()
        
        # masks
        self.full_masks = np.repeat(self.masks.T.ravel(), self.nsamples)
        self.full_clusters = np.tile(np.repeat(self.clusters_rel, self.nsamples), self.nchannels)
        self.full_channels = np.repeat(np.arange(self.nchannels, dtype=np.int32), self.nspikes * self.nsamples)
        
        # normalize the initial waveforms
        self.data_normalizer = DataNormalizer(data)
        self.normalized_data = self.data_normalizer.normalize()
        
        # position waveforms
        self.position_manager.set_info(self.nchannels, self.nclusters, 
                                       geometrical_positions=self.geometrical_positions)
        
        # update the highlight manager
        self.highlight_manager.initialize()
        
    # Internal methods
    # ----------------
    def prepare_waveform_data(self):
        """Define waveform data."""
        # prepare data for GPU transfer
        # in GPU memory, X coordinates are always between -1 and 1
        X = np.tile(np.linspace(-1., 1., self.nsamples),
                                (self.nchannels * self.nspikes, 1))
        
        # a (Nsamples x Nspikes) x Nchannels array
        Y = np.vstack(self.waveforms_reordered)
        
        # create a Nx2 array with all coordinates
        data = np.empty((X.size, 2), dtype=np.float32)
        data[:,0] = X.ravel()
        data[:,1] = Y.T.ravel()
        return data
    
    def get_data_position(self, channel, cluster_rel):
        """Return the position in the normalized data of the waveforms of the 
        given cluster (relative index) and channel.
        
        """
        # get absolute cluster index
        cluster = self.clusters_unique[cluster_rel]
        i0 = self.nsamples * (channel * self.nspikes + self.cluster_sizes_cum[cluster])
        i1 = i0 + self.nsamples * self.cluster_sizes_dict[cluster]
        return i0, i1
    
    
    
    
class WaveformTemplate(DefaultTemplate):
    def initialize(self, npoints=None, nclusters=None, nchannels=None, 
        nsamples=None, nspikes=None,
        **kwargs):
        
        self.npoints = npoints
        self.nsamples = nsamples
        self.nspikes = nspikes
        self.size = self.npoints
        self.nclusters = nclusters
        self.nchannels = nchannels
        
        self.bounds = np.arange(0, self.npoints + 1, 
                                self.nsamples, dtype=np.int32)
        self.primitive_type = PrimitiveType.LineStrip
        
        
        self.add_attribute("position0", vartype="float", ndim=2)
        self.add_attribute("mask", vartype="float", ndim=1)
        self.add_attribute("cluster", vartype="int", ndim=1)
        self.add_attribute("channel", vartype="int", ndim=1)
        self.add_attribute("highlight", vartype="int", ndim=1)
        
        self.add_uniform("nclusters", vartype="int", ndim=1, data=nclusters)
        self.add_uniform("nchannels", vartype="int", ndim=1, data=nchannels)
        self.add_uniform("box_size", vartype="float", ndim=2)
        self.add_uniform("box_size_margin", vartype="float", ndim=2)
        self.add_uniform("probe_scale", vartype="float", ndim=2)
        self.add_uniform("superimposed", vartype="bool", ndim=1)
        self.add_uniform("cluster_colors", vartype="float", ndim=3,
            size=self.nclusters)
        self.add_uniform("channel_positions", vartype="float", ndim=2,
            size=self.nchannels)
        
        self.add_varying("varying_color", vartype="float", ndim=4)
        
        self.add_vertex_main(VERTEX_SHADER)
        self.add_fragment_main(FRAGMENT_SHADER)
        
        self.initialize_default(**kwargs)
    
    
class WaveformPaintManager(PaintManager):
    
    def get_uniform_value(self, name):
        if name == "box_size":
            w, h = self.position_manager.load_box_size()
            return (np.float32(w), np.float32(h))
        if name == "box_size_margin":
            w, h = self.position_manager.load_box_size()
            alpha, beta = self.position_manager.alpha, self.position_manager.beta
            return (np.float32(w * (1 + 2 * alpha)), np.float32(h * (1 + 2 * beta)))
        if name == "probe_scale":
            return self.position_manager.probe_scale
        if name == "superimposed":
            return self.position_manager.superposition == WaveformSuperposition.Superimposed
        if name == "cluster_colors":
            return self.data_manager.cluster_colors
        if name == "channel_positions":
            return self.position_manager.get_channel_positions()
    
    def auto_update_uniforms(self, *names):
        dic = dict([(name, self.get_uniform_value(name)) for name in names])
        self.set_data(dataset=self.ds_waveforms, **dic)
    
    def initialize(self):
        self.ds_waveforms = self.create_dataset(WaveformTemplate,
            npoints=self.data_manager.npoints,
            nchannels=self.data_manager.nchannels,
            nclusters=self.data_manager.nclusters,
            nsamples=self.data_manager.nsamples,
            nspikes=self.data_manager.nspikes,
            position0=self.data_manager.normalized_data,
            mask=self.data_manager.full_masks,
            cluster= self.data_manager.full_clusters,
            channel=self.data_manager.full_channels,
            highlight=self.highlight_manager.highlight_mask,
        )
        
        self.auto_update_uniforms("box_size", "box_size_margin", "probe_scale",
            "superimposed", "cluster_colors", "channel_positions",)
        
        
        
        
class WaveformInteractionManager(InteractionManager):
    def process_none_event(self):
        super(WaveformInteractionManager, self).process_none_event()
        self.highlight_manager.cancel_highlight()
        
    def process_custom_event(self, event, parameter):
        # toggle arrangements
        if event == WaveformEventEnum.ToggleSuperpositionEvent:
            self.position_manager.toggle_superposition()
        if event == WaveformEventEnum.ToggleSpatialArrangementEvent:
            self.position_manager.toggle_spatial_arrangement()
        # change scale
        if event == WaveformEventEnum.ChangeBoxScaleEvent:
            self.position_manager.change_box_scale(*parameter)
        if event == WaveformEventEnum.ChangeProbeScaleEvent:
            self.position_manager.change_probe_scale(*parameter)
        # transient selection
        if event == WaveformEventEnum.HighlightSpikeEvent:
            self.highlight_manager.highlight(parameter)
            self.cursor = cursors.CrossCursor
        
  
class WaveformBindings(DefaultBindingSet):
    def set_panning(self):
        # Panning: left button mouse, wheel
        self.set(UserActions.LeftButtonMouseMoveAction, InteractionEvents.PanEvent,
                    param_getter=lambda p: (p["mouse_position_diff"][0],
                                            p["mouse_position_diff"][1]))
                    
        # Panning: keyboard arrows
        self.set(UserActions.KeyPressAction, InteractionEvents.PanEvent,
                    key=QtCore.Qt.Key_Left,
                    param_getter=lambda p: (.24, 0))
        self.set(UserActions.KeyPressAction, InteractionEvents.PanEvent,
                    key=QtCore.Qt.Key_Right,
                    param_getter=lambda p: (-.24, 0))
        self.set(UserActions.KeyPressAction, InteractionEvents.PanEvent,
                    key=QtCore.Qt.Key_Up,
                    param_getter=lambda p: (0, -.24))
        self.set(UserActions.KeyPressAction, InteractionEvents.PanEvent,
                    key=QtCore.Qt.Key_Down,
                    param_getter=lambda p: (0, .24))
                
    def set_zooming(self):
        # Zooming: right button mouse
        self.set(UserActions.RightButtonMouseMoveAction, InteractionEvents.ZoomEvent,
                    param_getter=lambda p: (p["mouse_position_diff"][0]*2.5,
                                            p["mouse_press_position"][0],
                                            p["mouse_position_diff"][1]*2.5,
                                            p["mouse_press_position"][1]))
        # Zooming: zoombox (drag and drop)
        # self.set(UserActions.MiddleButtonMouseMoveAction, InteractionEvents.ZoomBoxEvent,
                    # param_getter=lambda p: (p["mouse_press_position"][0],
                                            # p["mouse_press_position"][1],
                                            # p["mouse_position"][0],
                                            # p["mouse_position"][1]))
                     
        # Zooming: ALT + key arrows
        self.set(UserActions.KeyPressAction, InteractionEvents.ZoomEvent,
                    key=QtCore.Qt.Key_Left, key_modifier=QtCore.Qt.Key_Shift, 
                    param_getter=lambda p: (-.25, 0, 0, 0))
        self.set(UserActions.KeyPressAction, InteractionEvents.ZoomEvent,
                    key=QtCore.Qt.Key_Right, key_modifier=QtCore.Qt.Key_Shift, 
                    param_getter=lambda p: (.25, 0, 0, 0))
        self.set(UserActions.KeyPressAction, InteractionEvents.ZoomEvent,
                    key=QtCore.Qt.Key_Up, key_modifier=QtCore.Qt.Key_Shift, 
                    param_getter=lambda p: (0, 0, .25, 0))
        self.set(UserActions.KeyPressAction, InteractionEvents.ZoomEvent,
                    key=QtCore.Qt.Key_Down, key_modifier=QtCore.Qt.Key_Shift, 
                    param_getter=lambda p: (0, 0, -.25, 0))
        
        # Zooming: wheel
        self.set(UserActions.WheelAction, InteractionEvents.ZoomEvent,
                    param_getter=lambda p: (
                                    p["wheel"]*.002, 
                                    p["mouse_position"][0],
                                    p["wheel"]*.002, 
                                    p["mouse_position"][1]))
        
    def set_reset(self):
        # Reset view
        self.set(UserActions.KeyPressAction, InteractionEvents.ResetEvent, key=QtCore.Qt.Key_R)
        # Reset zoom
        self.set(UserActions.DoubleClickAction, InteractionEvents.ResetEvent)
        
    def set_arrangement_toggling(self):
        # toggle superposition
        self.set(UserActions.KeyPressAction,
                 WaveformEventEnum.ToggleSuperpositionEvent,
                 key=QtCore.Qt.Key_O)
                 
        # toggle spatial arrangement
        self.set(UserActions.KeyPressAction,
                 WaveformEventEnum.ToggleSpatialArrangementEvent,
                 key=QtCore.Qt.Key_G)

    def set_box_scaling(self):
        # change probe scale: CTRL + right mouse
        self.set(UserActions.RightButtonMouseMoveAction,
                 WaveformEventEnum.ChangeBoxScaleEvent,
                 key_modifier=QtCore.Qt.Key_Shift,
                 param_getter=lambda p: (p["mouse_position_diff"][0]*.1,
                                         p["mouse_position_diff"][1]*.5))

    def set_probe_scaling(self):
        # change probe scale: CTRL + right mouse
        self.set(UserActions.RightButtonMouseMoveAction,
                 WaveformEventEnum.ChangeProbeScaleEvent,
                 key_modifier=QtCore.Qt.Key_Control,
                 param_getter=lambda p: (p["mouse_position_diff"][0]*1,
                                         p["mouse_position_diff"][1]*1))

    def set_highlight(self):
        # highlight
        # self.set(UserActions.MiddleButtonMouseMoveAction,
                 # WaveformEventEnum.HighlightSpikeEvent,
                 # param_getter=lambda p: (p["mouse_press_position"][0],
                                         # p["mouse_press_position"][1],
                                         # p["mouse_position"][0],
                                         # p["mouse_position"][1]))
        
        self.set(UserActions.LeftButtonMouseMoveAction,
                 WaveformEventEnum.HighlightSpikeEvent,
                 key_modifier=QtCore.Qt.Key_Control,
                 param_getter=lambda p: (p["mouse_press_position"][0],
                                         p["mouse_press_position"][1],
                                         p["mouse_position"][0],
                                         p["mouse_position"][1]))
        
    def extend(self):
        self.set_arrangement_toggling()
        self.set_box_scaling()
        self.set_probe_scaling()
        self.set_highlight()
    
    
    
    
class WaveformView(GalryWidget):
    def initialize(self):
        self.constrain_navigation = False
        self.set_bindings(WaveformBindings)
        self.set_companion_classes(
                paint_manager=WaveformPaintManager,
                interaction_manager=WaveformInteractionManager,
                data_manager=WaveformDataManager,
                position_manager=WaveformPositionManager,
                highlight_manager=WaveformHighlightManager,
                )
        
    def set_data(self, *args, **kwargs):
        self.data_manager.set_data(*args, **kwargs)


# if __name__ == '__main__':
    
    # spikes = 1000
    # data = np.load("data/data%d.npz" % spikes)
    # waveforms = data["waveforms"]
    # clusters = data["clusters"]
    # geometrical_positions = data["electrode_positions"]
    # masks = data["masks"]
    
    # # select clusters
    # nclusters = 3
    
    # # select largest clusters
    # c = collections.Counter(clusters)
    # best_clusters = np.array(map(operator.itemgetter(0), c.most_common(nclusters)))
    # indices = np.zeros(spikes, dtype=bool)
    # for i in xrange(nclusters):
        # indices = indices | (clusters == best_clusters[-i])
        
    # # for testing, we just use the first colors for our clusters
    # cluster_colors = np.array(colors.generate_colors(nclusters), dtype=np.float32)
        
    # waveforms = waveforms[indices,:,:]
    # clusters = clusters[indices]
    # masks = masks[indices,:]
    
    # print waveforms.shape, waveforms.size
    
    
    