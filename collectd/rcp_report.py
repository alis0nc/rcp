# -*- coding: utf-8 -*-
# vim: fileencoding=utf-8
#
# Copyright © 2014 Alison Chan <alisonc@alisonc.net>
#

"""
collectd python plugin to report to rcp controller 

This plugin aggregates all of a cycle's write data as follows:
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


def dictify(vl):
  """
  Translates object to dictionary

  @param vl object
  @return dictionary
  """
  return dict((n, getattr(vl, n)) for n in dir(vl) if not callable(getattr(vl, n)) and not n.startswith('__'))

class RcpReporter (object):
  """
  The class that implements aggregating messages and sending to rcp controller.
  """
  def __init__(self):
    self.connected = False
    self.sock = None
    self.host = None
    self.port = None
    self.to_write = {}
    self.channel_name = None
    self.should_quit = False
    self.relevant_intf = []
    self.connect_timer = threading.Timer(30, self.try_connect)
    self.write_timer = threading.Timer(2, self.dwrite)
    

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

  def dwrite(self):
    """
    Writes data after a delay
    
    @return boolean success
    """
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
      elif c.key == 'Channel':
        self.channel_name = c.values[0]
      elif c.key == 'Interfaces':
        self.relevant_intf = list(c.values)


  def init(self):
    """
    collectd init callback
    """  
    self.try_connect()
    # send hello message
    if self.sock and self.connected:
      hellomsg = {}
      hellomsg['channel'] = self.channel_name
      hellomsg['cmd'] = 'join_channel'
      self.sock.send(json.dumps(hellomsg))
      hellomsg = {}
      hellomsg['CHANNEL'] = self.channel_name
      hellomsg['cmd'] = 'hello'
      hellomsg['hostname'] = socket.gethostname()
      hellomsg['interfaces'] = self.relevant_intf
      self.sock.send(json.dumps(hellomsg))
    self.write_timer = threading.Timer(2, self.dwrite)
    self.write_timer.start()
    

  def write(self, vl, data=None):
    """
    collectd write callback

    @param vl collectd.Values to write
    
    """
    self.to_write['time'] = vl.time
    self.to_write['CHANNEL'] = self.channel_name
    self.to_write['host'] = vl.host
    self.to_write['cmd'] = 'stats'
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
      
  
  def shutdown(self):
    """
    collectd shutdown callback
    """  
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
