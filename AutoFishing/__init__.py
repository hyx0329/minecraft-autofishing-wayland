import typing as typ

import numpy as np
import dbus
import time

from pynput.mouse import Button as mBtn
from pynput.mouse import Controller as mCon
from pynput.keyboard import Key as kKey
from pynput.keyboard import Controller as kCon

from gi.repository import GLib
from dbus.mainloop.glib import DBusGMainLoop

import gi
gi.require_version("Gst", "1.0")
gi.require_version('GstVideo', '1.0')
gi.require_version('GstApp', '1.0')
from gi.repository import GObject, Gst, GstVideo, GstApp
from .gst_toolbox import utils as gstutils

from .simple_image_process import split_by_color_distance, cutout_center


DBusGMainLoop(set_as_default=True)
GObject.threads_init()
Gst.init(None)


import logging
import sys
logging.basicConfig(stream=sys.stdout, level=logging.DEBUG)
log = logging.getLogger("AutoFishing")


def on_gst_message(bus, message, loop):
    """Pipeline message handler, not necessarily required
    """
    t = message.type
    if t == Gst.MessageType.EOS:
        loop.quit()
    elif t == Gst.MessageType.WARNING:
        err, debug = message.parse_warning()
        log.warning('{}: {}\n'.format(err, debug))
    elif t == Gst.MessageType.ERROR:
        err, debug = message.parse_error()
        log.error('{}: {}\n'.format(err, debug))
        loop.quit()
    else:
        log.debug("Got something from bus: %s" % t)
    return True


def extract_buffer(sample: Gst.Sample, channel_count=0) -> np.ndarray:
    """Extracts Gst.Buffer from Gst.Sample and converts to np.ndarray"""

    buffer = sample.get_buffer()  # Gst.Buffer

    # print(buffer.pts, buffer.dts, buffer.offset)

    caps_format = sample.get_caps().get_structure(0)  # Gst.Structure

    # GstVideo.VideoFormat
    video_format = GstVideo.VideoFormat.from_string(
        caps_format.get_value('format'))

    w, h = caps_format.get_value('width'), caps_format.get_value('height')
    if channel_count > 0:
        c = channel_count
    else:
        # TODO: fix this
        c = gstutils.get_num_channels(video_format)

    buffer_size = buffer.get_size()
    shape = (h, w, c) if (h * w * c == buffer_size) else buffer_size
    array = np.ndarray(shape=shape, buffer=buffer.extract_dup(0, buffer_size),
                       dtype=gstutils.get_np_dtype(video_format))

    return np.squeeze(array)  # remove single dimension if exists


# adapted from https://gitlab.gnome.org/-/snippets/19
class AutoFishing:

    request_iface = 'org.freedesktop.portal.Request'
    screen_cast_iface = 'org.freedesktop.portal.ScreenCast'

    def __init__(self):
        self.cut_center_ratio = 0.01

        self.session_bus = dbus.SessionBus()
        self.my_dbus_name = self.session_bus.get_unique_name()[1:].replace('.', '_')
        log.debug("My dbus name: %s" % self.my_dbus_name)

        self.cast_obj = self.session_bus.get_object('org.freedesktop.portal.Desktop',
                                                    '/org/freedesktop/portal/desktop')
        self.cast_interface = dbus.Interface(self.cast_obj, self.screen_cast_iface)
        self.request_token_counter = 0
        self.session_token_counter = 0
        self.pipeline = None
        self.gst_loop = GLib.MainLoop()
        self.session = None
        self.session_path = None
        self.session_token = None

        self.mouse = mCon()
        self.keyboard = kCon()
        self.autofishing_state = dict()
        self.autofishing_state['starting'] = True
        self.autofishing_state['on_going'] = False

    def terminate(self):
        if self.pipeline is not None:
            self.pipeline.set_state(Gst.State.NULL)
        self.gst_loop.quit()

    def new_request_path(self):
        self.request_token_counter += 1
        token = 'u%d' % self.request_token_counter
        path = '/org/freedesktop/portal/desktop/request/%s/%s'%(self.my_dbus_name, token)
        return (path, token)
    
    def new_session_path(self):
        self.session_token_counter += 1
        token = 'u%d' % self.session_token_counter
        path = '/org/freedesktop/portal/desktop/session/%s/%s'%(self.my_dbus_name, token)
        return (path, token)

    def screen_cast_call(self, method, callback, *args, options={}):
        (request_path, request_token) = self.new_request_path()
        self.session_bus.add_signal_receiver(callback,
                                        'Response',
                                        self.request_iface,
                                        'org.freedesktop.portal.Desktop',
                                        request_path)
        options['handle_token'] = request_token
        method(*(args + (options, )),
            dbus_interface=self.screen_cast_iface)
    
    def on_create_session_response(self, response, results):
        # first step: create a session
        if response != 0:
            log.error("Failed to create session: %d" % response)
        
        self.session = results['session_handle']
        log.debug("session %s created" % self.session)

        # proceed to 2nd step: select a video source
        # https://docs.flatpak.org/en/latest/portal-api-reference.html#gdbus-method-org-freedesktop-portal-ScreenCast.SelectSources
        self.screen_cast_call(self.cast_interface.SelectSources, self.on_select_sources_response,
                     self.session,
                     options={ 'multiple': False,
                               'types': dbus.UInt32(1|2) })
    
    def on_select_sources_response(self, response, results):
        # 2nd step
        if response != 0:
            log.error("Failed to select source(s): %d" % response)
            self.terminate()
            return

        log.debug("Cast source is being selected by user")

        # 3rd step: start the cast
        # https://docs.flatpak.org/en/latest/portal-api-reference.html#gdbus-method-org-freedesktop-portal-ScreenCast.Start
        self.screen_cast_call(self.cast_interface.Start, self.on_start_response,
                        self.session, '')
    
    def on_start_response(self, response, results):
        if response != 0:
            log.error("Failed to start: %s. Maybe no cast source selected." % response)
            self.terminate()
            return

        # final step: start the inference loop/Play the video
        log.info("streams:")
        for (node_id, stream_properties) in results['streams']:
            log.info("stream {}".format(node_id))
            log.info("stream props {}".format(stream_properties))
            self.play_pipewire_stream(node_id)


    # This is also the actual streaming program
    def play_pipewire_stream(self, node_id):
        LEAKY_Q = 'queue max-size-buffers=1 leaky=downstream'
        app_sink = "appsink emit-signals=True name=autofishing"

        empty_dict = dbus.Dictionary(signature="sv")
        fd_object = self.cast_interface.OpenPipeWireRemote(self.session, empty_dict,
                                            dbus_interface=self.screen_cast_iface)
        fd = fd_object.take()
        pipeline_script = 'pipewiresrc fd={fd} path={path} ! videoconvert ! capsfilter caps=video/x-raw,format=BGRA ! {leaky_q} ! {appsink}'.format(fd=fd, path=node_id, leaky_q=LEAKY_Q, appsink=app_sink)
        pipeline = Gst.parse_launch(pipeline_script)
        
        app_sink_obj = pipeline.get_by_name('autofishing')
        app_sink_obj.connect("new-sample", self.on_fishing_new_frame, None)

        pipeline.get_bus().connect('message', on_gst_message)
        pipeline.set_state(Gst.State.PLAYING)
    
    def on_fishing_new_frame(self, sink: GstApp.AppSink, data: typ.Any) -> Gst.FlowReturn:
        """Callback on 'new-sample' signal"""
        # Emit 'pull-sample' signal
        # https://lazka.github.io/pgi-docs/GstApp-1.0/classes/AppSink.html#GstApp.AppSink.signals.pull_sample
        sample = sink.emit("pull-sample")  # Gst.Sample
        if isinstance(sample, Gst.Sample):
            array = extract_buffer(sample, channel_count=4)
            # print(
            #     "Received {type} with shape {shape} of type {dtype}".format(
            #         type=type(array),
            #         shape=array.shape,
            #         dtype=array.dtype
            #     )
            # )
            
            ##### Autofishing stuff #####

            if self.autofishing_state['starting']:
                log.info("Preparing auto-fishing")
                time.sleep(5)
                self.keyboard.press(kKey.esc)
                self.keyboard.release(kKey.esc)
                time.sleep(1)
                self.use_fishing_rod()
                time.sleep(2)
                self.autofishing_state['starting'] = False
                return Gst.FlowReturn.OK

            # assume full screen
            window_center = cutout_center(array, width=20, height=20)
            window_center = window_center[:,:,:3]
            split_mask = split_by_color_distance(window_center, np.array([0,0,0]), 10)
            a = np.sum(split_mask)
            b = np.prod(np.shape(split_mask))
            ratio = a/b
            log.debug("Ratio: {}".format(ratio))

            # wait for fish
            if ratio > 0.025:
                return Gst.FlowReturn.OK
            
            # fish caught
            log.info("Fish caught (possibly)")
            self.use_fishing_rod()
            time.sleep(1)
            log.info("Start another round")
            self.use_fishing_rod()
            time.sleep(2)

            return Gst.FlowReturn.OK
        return Gst.FlowReturn.ERROR

    def use_fishing_rod(self):
        self.mouse.press(mBtn.right)
        self.mouse.release(mBtn.right)

    ### TEST ###

    def run(self):
        # compose and start pipeline, and the inference loop

        (session_path, session_token) = self.new_session_path()
        self.session_path = session_path
        self.session_token = session_token

        # first step: create a session
        # https://docs.flatpak.org/en/latest/portal-api-reference.html#gdbus-method-org-freedesktop-portal-ScreenCast.CreateSession
        self.screen_cast_call(self.cast_interface.CreateSession, self.on_create_session_response,
                 options={ 'session_handle_token': session_token })

        try:
            self.gst_loop.run()
        except KeyboardInterrupt:
            log.info("Exiting")
        finally:
            # cleanup
            self.terminate()
            while GLib.MainContext.default().iteration(False):
                pass
