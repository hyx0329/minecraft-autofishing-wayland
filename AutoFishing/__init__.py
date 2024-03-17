import numpy as np
import dbus
import re

from gi.repository import GLib
from dbus.mainloop.glib import DBusGMainLoop

import gi
gi.require_version("Gst", "1.0")
from gi.repository import GObject, Gst


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
        empty_dict = dbus.Dictionary(signature="sv")
        fd_object = self.cast_interface.OpenPipeWireRemote(self.session, empty_dict,
                                            dbus_interface=self.screen_cast_iface)
        fd = fd_object.take()
        pipeline = Gst.parse_launch('pipewiresrc fd=%d path=%u ! videoconvert ! autovideosink' % (fd, node_id))
        pipeline.set_state(Gst.State.PLAYING)
        pipeline.get_bus().connect('message', on_gst_message)

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
