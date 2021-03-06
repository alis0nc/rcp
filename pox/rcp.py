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

def path_to_edgelist(path):
  edgelist = []
  i = 1
  while i < len(path):
    a, b = path[i-1], path[i]
    edgelist.append((a, b))
    i += 1
  return edgelist

def partition (multipath, src, dst):
  """
  Partition a directed multipath graph into several distinct path graphs
  
  @param multipath The multipath graph
  @param src The source node
  @param dst The destination node
  
  @return list of path graphs
  """
  paths = []
  tempmultipath = multipath.copy()
  while tempmultipath.number_of_edges():
    p = nx.shortest_path(tempmultipath, src, dst)
    for s, t in path_to_edgelist(p): 
      tempmultipath.remove_edge(s, t)
    G = nx.DiGraph()
    G.add_path(p)
    paths.append(G)
  return paths

def edge_disjoint_paths (graph, src, dst, fully_disjoint=True, max_paths=-1, weight='w'):
  """
  finds edge disjoint paths
  
  @param graph the graph on which to find the paths
  @param src the source node
  @param dst the destination node
  @param fully_disjoint require fully disjoint paths? (default=True)
  @param max_paths maximum number of paths to return (default=-1 (unlimited))
  @param weight edge attribute to use for weight (default='w')
  
  @return (multipath digraph of all paths, list of path graphs)
  
  It is an error not to specify a maximum number of paths while not enforcing 
  full disjointness. (If you say fully_disjoint=False, then you must specify a 
  maximum number of paths.)
  """
  ### TODO: Recheck this with Bhandari's book

  if not fully_disjoint and max_paths == -1:
    # Error!
    raise ValueError('You must specify a maximum number of paths if not fully disjoint')
  # Let's start with a working copy of the graph because call by reference
  tempgraph = graph.to_directed()
  inf2 = (max(tempgraph.edges(data=True), key=lambda e: e[2][weight]))[2][weight]*tempgraph.number_of_edges() + 1
  # Multipath graph
  multipath = nx.DiGraph() if fully_disjoint else nx.MultiDiGraph()
  brk = False
  while max_paths and not brk:
    brk = False
    shortest_path = path_to_edgelist(nx.shortest_path(tempgraph, src, dst, weight))
    for s, t in shortest_path:
      if tempgraph.has_edge(s, t):
        tempgraph[s][t][weight] += inf2
      if tempgraph.has_edge(t, s):
        tempgraph[t][s][weight] = 0 # -tempgraph[t][s][weight]
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
  paths = partition(multipath, src, dst)
  # Return what we've got
  log.debug([p.edges() for p in paths])
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
    self.source_host_port = self.dest_host_port = of.OFPP_NONE
    self.paths = [] # List of distinct paths
    self.vlan_for_path = {} # key: path.edges(); value: vlan used by path
    self.flowspace = {} # key: switch dpid; value: list of flow_mods
    self.channel = None # Messenger channel
    self._connected = False
    # FIXME this might need to be a MPLS tag or some other way of telling paths apart
    self.basevlan = 1000
    self.vlans_in_use = []
    core.listen_to_dependencies(self)
    
  def timer_elapsed (self):
    """
    Timer elapsed
    """
    self.get_stats()
    
  def get_stats(self):
    """
    Sends flow and port stats requests to source and destination switches
    """  
    if self.source:
      core.openflow.connections[self.source].send(of.ofp_stats_request(body=of.ofp_flow_stats_request()))
      core.openflow.connections[self.source].send(of.ofp_stats_request(body=of.ofp_port_stats_request()))
    if self.dest:
      core.openflow.connections[self.dest].send(of.ofp_stats_request(body=of.ofp_flow_stats_request()))
      core.openflow.connections[self.dest].send(of.ofp_stats_request(body=of.ofp_port_stats_request()))  
      
  def handle_rcp_msg(self, event, msg):
    m = str(event.msg.get('msg'))
    cmd = str(event.msg.get('cmd'))
    if cmd == 'hello':
      log.debug('Received hello message')
    elif cmd == 'stats':
      log.debug('Received stats message')
    elif cmd == 'connect':
      log.debug('Received connect message')
      self.establish_connection(event.msg.get('source'), event.msg.get('dest'))
    elif cmd == 'disconnect':
      log.debug('Received disconnect message')
      self.disestablish_connection()
      
  def handle_join(self, event):
    log.debug(str(event.msg))
      
  def _all_dependencies_met (self):
    """
    All dependencies met: then set up the Messenger channel and event handlers
    for it
    """
    self.channel = core.MessengerNexus.get_channel('RCP')
    self.channel.addListener(MessageReceived, self.handle_rcp_msg)
    self.channel.addListener(ChannelJoin, self.handle_join)
    
  def conn_ack(self):
    """
    Notifys messenger subscribers that we have established a connection.
    """
    self.channel.send({'cmd': 'ack', 'src': self.source, 'dst': self.dest})
    
  def conn_nak(self, already_connected=False):
    """
    Notifys messenger subscribers that we can't establish connection.
    """
    self.channel.send({'cmd': 'nak', 'src': self.source, 'dst': self.dest, 'already_connected': already_connected})
    
  def conn_fin(self):
    """
    Notifys messenger subscribers that we have torn down a connection.
    """
    self.channel.send({'cmd': 'fin', 'src': self.source, 'dst': self.dest})
    
  def _handle_core_GoingDownEvent(self, event):
    """
    Handles a GoingDown event.
    """
    self.disestablish_connection()
    
  def _handle_openflow_FlowStatsReceived (self, event):
    """
    Handles a FlowStatsReceived event.
    """
    log.debug("Flow stats received")

  def _handle_openflow_PortStatsReceived (self, event):
    """
    Handles a PortStatsReceived event.
    """
    log.debug("Port stats received")

  def _handle_openflow_ConnectionUp (self, event):
    """
    Handles a ConnectionUp event by adding the switch to the network graph.
    """
    self.flowspace[event.dpid] = []
    self.network_graph.add_node(event.dpid)

  def _handle_openflow_ConnectionDown (self, event):
    """
    Handles a ConnectionDown event by removing the switch from the network graph. 
    """
    if event.dpid in self.network_graph: 
      self.network_graph.remove_node(event.dpid)
      del self.flowspace[event.dpid]

  def _handle_openflow_discovery_LinkEvent (self, event):
    """
    Handles a LinkEvent by adding or removing the link to the network graph.
    If a connection is in progress when this happens, it recalculates potential 
    paths and maybe changes the installed flows.
    """
    s1 = event.link.dpid1
    s2 = event.link.dpid2
    assert s1 in self.network_graph
    assert s2 in self.network_graph
    if event.added:
      port_dict={s1: event.port_for_dpid(s1),
                 s2: event.port_for_dpid(s2), 
                 'w': 1}
      self.network_graph.add_edge(s1, s2, port_dict)
    elif event.removed:
      if (s1, s2) in self.network_graph.edges():
        self.network_graph.remove_edge(s1, s2)
    if self._connected:
      # We're connected, and we have to deal with a connectivity change, so let's recalculate paths
      self.change_paths()
                  
  def install_flows(self, paths, source, dest):
    """
    Installs flows in the network for each path
    
    @param paths a list of directed path graphs
    @param source the source node
    @param dest the destination node 
    """
    if source in self.flowspace and any([x.match.in_port == self.source_host_port for x in self.flowspace[source]]):
      source_f = [x for x in self.flowspace[source] if x.match.in_port == self.source_host_port][0]
      source_f.command = of.OFPFC_MODIFY
      del self.flowspace[source][self.flowspace[source].index(source_f)]
    else: 
      source_f = of.ofp_flow_mod(command=of.OFPFC_ADD)
      source_f.match = of.ofp_match(in_port=self.source_host_port)
    for path in paths:
      vlan = self.basevlan
      # take the lowest unused vlan
      while vlan in self.vlan_for_path.values(): vlan += 1
      self.vlan_for_path[tuple(path.edges())] = vlan
      for node in path:
        if node == source:
          # special case for source
          out_port = self.network_graph[node][path.neighbors(node)[0]][node]
          source_f.actions.append(of.ofp_action_vlan_vid(vlan_vid=vlan))
          source_f.actions.append(of.ofp_action_output(port=out_port))
        elif node == dest:
          # special case for dest
          f = of.ofp_flow_mod()
          f.command = of.OFPFC_ADD
          in_port = self.network_graph[node][path.predecessors(node)[0]][node]
          out_port = self.dest_host_port
          f.match = of.ofp_match(in_port=in_port, dl_vlan=vlan)
          f.actions.append(of.ofp_action_strip_vlan())
          f.actions.append(of.ofp_action_output(port=out_port))
          self.flowspace[node].append(f)
          log.debug("Installing flow on %s: \n%s" % (poxutil.dpid_to_str(node), str(f)))
          core.openflow.connections[node].send(f)
        else: 
          # general case
          f = of.ofp_flow_mod()
          f.command = of.OFPFC_ADD
          in_port = self.network_graph[node][path.predecessors(node)[0]][node]
          out_port = self.network_graph[node][path.neighbors(node)[0]][node]
          f.match = of.ofp_match(in_port=in_port, dl_vlan=vlan)
          f.actions.append(of.ofp_action_vlan_vid(vlan_vid = vlan))
          f.actions.append(of.ofp_action_output(port=out_port))
          self.flowspace[node].append(f)
          log.debug("Installing flow on %s: \n%s" % (poxutil.dpid_to_str(node), str(f)))
          core.openflow.connections[node].send(f)
    # finally we can install the flow on source
    self.flowspace[source].append(source_f)
    log.debug("Installing flow on %s: \n%s" % (poxutil.dpid_to_str(source), str(source_f)))
    core.openflow.connections[source].send(source_f)
      
  def remove_flows(self, paths, source, dest):
    """
    Removes flows from the network for each path
    
    @param paths a list of directed path graphs
    @param source the source node
    @param dest the destination node 
    """
    for path in paths:
      vlan = self.vlan_for_path[tuple(path.edges())]
      for node in path:
        if node == source:
          # special case for source
          # find the replicatory flow, and if vlan is in our actions, remove it
          f = [x for x in self.flowspace[node] if len(x.actions) > 2][0]
          idx = f.actions.index([a for a in f.actions if isinstance(a, of.ofp_action_vlan_vid) and a.vlan_vid == vlan][0])
          del f.actions[idx]
          del f.actions[idx]
          f.command = of.OFPFC_MODIFY
          log.debug("Modifying flow on %s: \n%s" % (poxutil.dpid_to_str(node), str(f)))
          core.openflow.connections[node].send(f)
          # We don't need to update flowspace, since we modified that flow in place
        else:
          ftr = [f for f in self.flowspace[node] if f.match.dl_vlan == vlan]
          for f in ftr: 
            f.command = of.OFPFC_DELETE 
            log.debug("Removing flow on %s: \n%s" % (poxutil.dpid_to_str(node), str(f)))
            core.openflow.connections[node].send(f)
            self.flowspace[node] = [f for f in self.flowspace[node] if f.match.dl_vlan != vlan]
      del self.vlan_for_path[tuple(path.edges())]

  def establish_connection(self, source, dest):
    """
    Establishes a connection between source and destination switches. This should 
    get called from an event in future. (probably a command received through pox.messenger)
    
    @param source The source switch's dpid
    @param dest The destination switch's dpid
    """
    # check if we are already connected, in which case nak
    if self._connected: 
      self.conn_nak(already_connected=True)
      return
    
    self.source = source
    self.dest = dest
    _, self.paths = edge_disjoint_paths(self.network_graph, self.source, self.dest, 
      fully_disjoint=True, max_paths=2, weight='w')
    # Install the flows
    # TODO integrate host_tracker so that we don't have to assume that hosts 
    # are connected to port 1. (this assumption really only works in mininet anyway)
    self.source_host_port = self.dest_host_port = 1
    core.callLater(self.install_flows, self.paths, self.source, self.dest)
    core.callLater(self.install_flows, [p.reverse() for p in self.paths], self.dest, self.source)
    # Start the send stats request timer
    self.timer = recoco.Timer(30, self.timer_elapsed, recurring=True, started=False)
    self.timer.start()
    # Notify subscribers that a connection is establish
    self._connected = True
    self.conn_ack()
    
  def change_paths(self, avoid=None):
    """
    Recalculates paths.
    
    @param avoid path to avoid (could be due to deterioration) (default: None)
    """
    # Empirical timing analysis
    import time
    t_ = time.clock()
    msg = of.ofp_echo_request(body="Path recalculation started")
    core.openflow.connections[self.source].send(msg)
    if not self._connected: # nothing doing
      return
    newpaths = []
    if avoid:
      newgraph = self.network_graph.copy()
      newgraph.remove_edges_from(avoid.edges())
      _, newpaths = edge_disjoint_paths(newgraph, self.source, self.dest, max_paths=2)
    else:
      _, newpaths = edge_disjoint_paths(self.network_graph, self.source, self.dest, max_paths=2)
    for p in newpaths:
      if p.edges() not in [P.edges() for P in self.paths]: # if we've gained a path
        core.callLater(self.install_flows, [p], self.source, self.dest)
        core.callLater(self.install_flows,[p.reverse()], self.dest, self.source)
        self.paths.append(p)
    for p in self.paths:
      if p.edges() not in [P.edges() for P in newpaths]: # if we've lost a path
        core.callLater(self.remove_flows, [p], self.source, self.dest)
        core.callLater(self.remove_flows, [p.reverse()], self.dest, self.source)
        self.paths.remove(p)
    # Empirical timing analysis    
    msg = of.ofp_echo_request(body="Path recalculation finished")
    core.openflow.connections[self.source].send(msg)
    print("Changing paths took %f seconds" % (time.clock() - t_))
    
  def disestablish_connection(self):
    """
    Tears down a RCP connection by clearing the entire relevant flowspace.
    """
    self._connected = False
    for sw in self.flowspace: 
      for flow in self.flowspace[sw]:
        flow.command = of.OFPFC_DELETE
        core.openflow.connections[sw].send(flow)
      self.flowspace[sw] = []
    self.conn_fin()
    self.source = self.dest = None
    self.paths = []
    self.timer.cancel()


def _go_up (event): pass

@poxutil.eval_args
def launch ():
  """
  Launch function
  """

  if not core.hasComponent("RCP"):
    core.registerNew(RCP)
  core.addListenerByName("UpEvent", _go_up)
