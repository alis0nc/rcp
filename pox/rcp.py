# coding=utf8
# Copyright 2014 Alison Chan
# Kettering University
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at:
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""
Reliable Communications Protocol


"""

# Import some POX stuff
from pox.core import core                     # Main POX object
import pox.openflow.libopenflow_01 as of      # OpenFlow 1.0 library
import pox.lib.packet as pkt                  # Packet parsing/construction
from pox.lib.addresses import EthAddr, IPAddr # Address types
import pox.lib.util as poxutil                # Various util functions
import pox.lib.revent as revent               # Event library
import pox.lib.recoco as recoco               # Multitasking library
from pox.messenger import *
import pox.openflow

#import networkx
import networkx as nx

import datetime as dt
import subprocess

# Create a logger for this component
log = core.getLogger()

def edge_disjoint_paths (graph, src, dst, fully_disjoint=True, max_paths=-1, weight='w'):
  """
  finds edge disjoint paths
  
  @param graph the graph on which to find the paths
  @param src the source node
  @param dst the destination node
  @param fully_disjoint require fully disjoint paths? (default=True)
  @param max_paths maximum number of paths to return (default=-1 (unlimited))
  @param weight edge attribute to use for weight (default='w')
  
  @return (multipath graph of all paths, list of path graphs)
  
  It is an error not to specify a maximum number of paths while not enforcing 
  full disjointness. (If you say fully_disjoint=False, then you must specify a 
  maximum number of paths.)
  """
  ### TODO: Recheck this with Bhandari's book
  def path_to_edgelist(path):
    edgelist = []
    i = 1
    while i < len[path]:
      a, b = path[i-1], path[i]
      edgelist.append((a, b))
      i += 1
    return edgelist
  if not fully_disjoint and max_paths == -1:
    # Error!
    raise ValueError('You must specify a maximum number of paths if not fully disjoint')
  # Let's start with a working copy of the graph because call by reference
  tempgraph = graph.to_directed()
  inf2 = (max(tempgraph.edges(data=True), key=lambda e: e[2][weight]))[2][weight]*tempgraph.number_of_edges() + 1
  # Multipath graph
  multipath = nx.DiGraph() if fully_disjoint else nx.MultiDiGraph()
  # List of individual paths
  paths = []
  while max_paths and not brk:
    brk = False
    shortest_path = path_to_edgelist(nx.shortest_path(tempgraph, src, dst, weight))
    for s, t in shortest_path:
      if tempgraph.has_edge(s, t):
        tempgraph[s][t][weight] += inf2
      if tempgraph.has_edge(t, s):
        tempgraph[t][s][weight] = -tempgraph[t][s][weight]
    for s, t in shortest_path:
      # add the path to the multipath graph, erasing interlacing edges
      if multipath.has_edge(s, t) and fully_disjoint:
        # adding this edge would result in not being fully disjoint anymore
        brk = True
      elif multipath.has_edge(t, s):
        multipath.remove_edge(t, s)
      else: 
        multipath.add_edge(s, t)
    max_paths -= 1
  # Partition the multipath graph into distinct paths
  tempmultipath = multipath.copy()
  while tempmultipath.number_of_edges():
    p = nx.shortest_path(tempmultipath, src, dst)
    for s, t in path_to_edgelist(p): 
      tempmultipath.remove_edge(s, t)
    paths.append(nx.DiGraph(p))
  # Return what we've got
  return multipath, paths

class RCP (object):
  """
  RCP class: Main class for RCP 
  """
  def __init__ (self):
    self.network_graph = nx.Graph() # Graph view of the network; (de)populated by
                                    # LinkEvents and openflow.discovery
    self.source = None
    self.dest = None
    self.multipath = None # Multipath graph
    self.paths = [] # List of distinct paths
    self.channel = None # Messenger channel
    self.timer = recoco.Timer(30, self.timer_elapsed, recurring=True, started=False)
    core.listen_to_dependencies(self)
    
  def timer_elapsed (self):
    """
    Timer elapsed
    """
    pass    
      
  def _all_dependencies_met (self):
    """
    All dependencies met: then set up the Messenger channel and event handlers
    for it
    """
    self.channel = core.MessengerNexus.get_channel('RCP')
    def handle_rcp_msg (event, msg):
      m = str(event.msg.get('msg'))
      cmd = str(event.msg.get('cmd'))
      if cmd == 'hello':
        log.debug('Received hello message')
      elif cmd == 'stats':
        log.debug('Received stats message')
    def handle_join(event):
      log.debug(str(event.msg))
    self.chan.addListener(MessageReceived, handle_rcp_msg)
    self.chan.addListener(ChannelJoin, handle_join)

  def _handle_openflow_FlowStatsReceived (self, event):
    log.debug("Flow stats received")
    log.debug(str(event.stats))

  def _handle_openflow_PortStatsReceived (self, event):
    log.debug("Port stats received")
    log.debug(str(event.stats))

  def _handle_openflow_ConnectionUp (self, event):
    self.network_graph.add_node(event.dpid)

  def _handle_openflow_ConnectionDown (self, event):
    self.network_graph.remove_node(event.dpid)

  def _handle_openflow_discovery_LinkEvent (self, event):
    s1 = event.link.dpid1
    s2 = event.link.dpid2
    assert s1 in self.network_graph
    assert s2 in self.network_graph
    if event.added:
      self.network_graph.add_edge(s1, s2, {'w':1})
      self.network_graph[s1][s2] = event.port_for_dpid(s1)
      self.network_graph[s2][s1] = event.port_for_dpid(s2)
    elif event.removed:
      if (s1, s2) in self.network_graph.edges():
        self.network_graph.remove_edge(s1, s2)
      if s2 in self.network_graph[s1]: del self.network_graph[s1][s2]
      if s1 in self.network_graph[s2]: del self.network_graph[s2][s1]     

  def establish_connection(self, source, dest, sourcename=None, destname=None):
    pass   
    
def install_flows(sw, port1, port2, remove=False):
  pass

def _go_up (event): pass

@poxutil.eval_args
def launch (source=None, dest=None, sourcename=None, destname=None):
  """
  Launch function
  """

  if not core.hasComponent("RCP"):
    core.registerNew(RCP, source, dest, sourcename, destname)
  core.addListenerByName("UpEvent", _go_up)
