# -*- coding: utf-8 -*-
# vim: fileencoding=utf-8
#
# Copyright © 2014 Alison Chan <alisonc@alisonc.net>
#

"""
collectd python plugin to report to rcp controller 

 write callbacks come many within 1s, store in to_write
 xx                                                xx
0----1----2----3----4----5----6----7----8----9---10---> time (s)
    |         |         |         |         |
    dwrite timer every 2 seconds will send and invalidate to_write
    if it is more than a second old
"""

import collectd
import socket
import json
import threading
import time

"""
dictify: Translates object to dictionary

@param vl object
@return dictionary
"""
def dictify(vl):
  return dict((n, getattr(vl, n)) for n in dir(vl) if not callable(getattr(vl, n)) and not n.startswith('__'))

class RcpReporter (object):
  """
  __init__
  """
  def __init__(self):
    self.connected = False
    self.sock = None
    self.host = None
    self.port = None
    self.to_write = {}
    self.channel_name = None
    self.should_quit = False
    self.relevant_intf = ['eth0', 'wlan0']
    self.connect_timer = threading.Timer(30, self.try_connect)
    self.write_timer = threading.Timer(2, self.dwrite)
    
  """
  try_connect: tries to connect to the server, sets up a retry timer if unsuccessful
  """
  def try_connect(self):
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

  """
  dwrite: writes data
  
  @return boolean success
  """
  def dwrite(self):
    if self.should_quit: 
      # don't restart the timer, we are about to quit
      return False
    result = False
    if self.sock and self.connected and self.to_write and time.time() > self.to_write['time'] + 1:
      try:
        self.sock.send(json.dumps(self.to_write))
        result = True
      except socket.error as ex:
        # if we have an error, then we're not connected anymore
        collectd.error("Failed to write; will try reconnecting (%s)" % str(ex))
        self.connected = False
        self.try_connect()
        result = False
      # invalidate data
      self.to_write = {}
    self.write_timer = threading.Timer(2, self.dwrite)
    self.write_timer.start()
    return result
  
  """
  config: collectd config callback

  @param conf collectd.Config object
  """
  def config(self, conf):
    for c in conf.children:
      if c.key == 'Host':
        self.host = c.values[0]
      elif c.key == 'Port':
        self.port = c.values[0]
      elif c.key == 'Channel':
        self.channel_name = c.values[0]
      elif c.key == 'Interfaces':
        self.relevant_intf = list(c.values)

  """
  init: collectd init callback
  """  
  def init(self):
    self.try_connect()
    self.write_timer = threading.Timer(2, self.dwrite)
    self.write_timer.start()
    
  """
  write: collectd write callback

  @param vl collectd.Values to write
  
  """
  def write(self, vl, data=None):
    self.to_write['time'] = vl.time
    self.to_write['CHANNEL'] = self.channel_name
    self.to_write['host'] = vl.host
    if vl.plugin == 'interface' or vl.plugin == 'wireless':
      if vl.plugin_instance in self.relevant_intf:
        self.to_write[vl.plugin_instance + '_' + vl.type] = vl.values
      else:
        pass
    elif vl.plugin == 'ping':
      # TODO what is the format of a ping message
      pass
    else:
      # it's not a plugin we are interested in
      pass
      
  """
  shutdown: collectd shutdown callback
  """    
  def shutdown(self):
    # gracefully clean up and close the socket
    self.should_quit = True
    if self.sock and self.connected:
      collectd.info("Caught shutdown callback; shutting down")
      self.sock.shutdown(socket.SHUT_RDWR)
      self.sock.close()


reporter = RcpReporter()      
collectd.register_config(reporter.config)
collectd.register_init(reporter.init)
collectd.register_write(reporter.write)
collectd.register_shutdown(reporter.shutdown)
