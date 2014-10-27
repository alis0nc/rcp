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

host = '127.0.0.1'
port = '36000'

"""
dictify: Translates object to dictionary

@param vl object
@return dictionary
"""
def dictify(vl):
  return dict((n, getattr(vl, n)) for n in dir(vl) if not callable(getattr(vl, n)) and not n.startswith('__'))

"""
config: collectd config callback

@param conf collectd.Config object
"""
def config(conf):
  global host, port
  for c in conf.children:
    if c.key == 'Host':
      host = c.values[0]
    elif c.key == 'Port':
      port = c.values[0]

"""
init: collectd init callback
"""  
def init():
  global sock
  sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
  sock.connect((host, int(port)))

"""
write: collectd write callback

@param vl collectd.Values to write
"""
def write(vl, data=None):
  d = dictify(vl)
  if sock:
    sock.send(json.dumps(d))
    
"""
shutdown: collectd shutdown callback
"""    
def shutdown():
  # gracefully clean up and close the socket
  sock.shutdown(socket.SHUT_RDWR)
  sock.close()
    
collectd.register_config(config)
collectd.register_init(init)
collectd.register_write(write)
collectd.register_shutdown(shutdown)