# -*- coding: utf-8 -*-
# vim: fileencoding=utf-8
#
# Copyright © 2014 Alison Chan <alisonc@alisonc.net>
#

"""
collectd python plugin to stream values over TCP in JSON format
"""

import collectd
import socket
import json
import threading


def dictify(vl):
  """
  Translates object to dictionary
  
  @param vl object
  @return dictionary
  """
  return dict((n, getattr(vl, n)) for n in dir(vl) if not callable(getattr(vl, n)) and not n.startswith('__'))

class JsonStreamer (object):
  """
  The class that implements JSON streaming.
  """
  def __init__(self):
    self.connected = False
    self.sock = None
    self.host = None
    self.port = None
    self.connect_timer = threading.Timer(30, self.try_connect)
    

  def try_connect(self):  
    """
    Tries to connect to the server, and sets up a retry timer if unsuccessful
    """
    if not self.connected:
      try:
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.connect((self.host, int(self.port)))
        self.connected = True
      except socket.error as ex:
        # couldn't connect for some reason
        self.connected = False
        # log message
        collectd.error("Couldn't connect to server; will retry in 30s (%s)" % str(ex))
        if not self.connect_timer.is_alive():
          self.connect_timer = threading.Timer(30, self.try_connect)
          self.connect_timer.start()



  def config(self, conf):
    """
    collectd config callback

    @param conf collectd.Config object
    """
    for c in conf.children:
      if c.key == 'Host':
        self.host = c.values[0]
      elif c.key == 'Port':
        self.port = c.values[0]

 
  def init(self):
    """
    collectd init callback
    """ 
    self.try_connect()
    

  def write(self, vl, data=None):
    """
    collectd write callback

    @param vl collectd.Values to write
    """
    d = dictify(vl)
    if self.sock and self.connected:
      try:
        self.sock.send(json.dumps(d))
      except socket.error as ex:
        # if we have an error, then we're not connected anymore
        collectd.error("Failed to stream JSON; will try reconnecting (%s)" % str(ex))
        self.connected = False
        self.try_connect()
      
   
  def shutdown(self):
    """
    collectd shutdown callback
    """ 
    # gracefully clean up and close the socket
    if self.sock and self.connected:
      collectd.info("Caught shutdown callback; shutting down")
      self.sock.shutdown(socket.SHUT_RDWR)
      self.sock.close()


streamer = JsonStreamer()      
collectd.register_config(streamer.config)
collectd.register_init(streamer.init)
collectd.register_write(streamer.write)
collectd.register_shutdown(streamer.shutdown)
