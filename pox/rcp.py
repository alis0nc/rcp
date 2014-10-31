# coding=utf8
# Copyright 2014 Alison Chan
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

# http://stackoverflow.com/a/136280/1760157
# except this answer actually works
def tail(f, n, offset=0):
  stdout = subprocess.check_output(['tail', '-n', str(n+offset), f])
  lines = stdout.splitlines()
  return lines[:-offset if offset else None]

class RCP (object):

  def __init__(self, source=None, dest=None, csvpath=None, sourcename=None, destname=None):
    self.graph = nx.DiGraph()
    self.source = source
    self.dest = dest
    self.csvpath = csvpath
    self.paths = []
    self.active_path = 0
    self.chan = None
    self.timer = recoco.Timer(30, self.timer_elapsed, recurring=True, started=False)
    core.listen_to_dependencies(self)
    #core.openflow.addListener(pox.openflow.FlowStatsReceived, self._handle_openflow_FlowStatsReceived)
    #core.openflow.addListener(pox.openflow.PortStatsReceived, self._handle_openflow_PortStatsReceived)

  def timer_elapsed(self):
    self.get_stats()
  
  def get_averages(self, csvfile, s, d, q):
    # assume 0.1 Hz reporting rate (every 10s)
    # also we want 15 minutes worth of data
    nlines = 15*60 / 10
    with tail('/'.join((csvfile, s, 'ping', '-'.join((q, d, str(dt.date.today()))))), nlines) as a:
      vals = []
      for line in a:
        vals.append(line.split(',')[1])
      # calculate the averages
      onemin = sum(vals[-6:]) / 6
      fivemin = sum(vals[-30:]) / 30
      fifteenmin = sum(vals[-90:]) / 90
      return onemin, fivemin, fifteenmin
  
  def get_stats(self):
    """
    Sends flow and port stats requests to source and dest switches
    Also parses csv data collected from collectd
    """
    # TODO Should we do this for all switches?
    core.openflow.connections[self.source].send(of.ofp_stats_request(body=of.ofp_flow_stats_request()))
    core.openflow.connections[self.source].send(of.ofp_stats_request(body=of.ofp_port_stats_request()))
    core.openflow.connections[self.dest].send(of.ofp_stats_request(body=of.ofp_flow_stats_request()))
    core.openflow.connections[self.dest].send(of.ofp_stats_request(body=of.ofp_port_stats_request()))
    if self.csvpath:
      src_ping_avgs = get_averages(self.csvpath, self.sourcename, self.destname, 'ping')
      src_drop_avgs = get_averages(self.csvpath, self.sourcename, self.destname, 'ping_droprate')
      dest_ping_avgs = get_averages(self.csvpath, self.destname, self.sourcename, 'ping')
      dest_drop_avgs = get_averages(self.csvpath, self.destname, self.sourcename, 'ping_droprate')        
      
  def _all_dependencies_met (self):
    # Set up the RCP channel
    self.chan = core.MessengerNexus.get_channel("RCP")
    def handle_rcp_msg (event, msg):
      m = str(msg.get("msg"))
      cmd = event.msg.get("cmd")
      log.debug("Received message: " + str(event.msg))
    def handle_join(event):
      log.debug(str(event.msg))
    self.chan.addListener(MessageReceived, handle_rcp_msg)
    self.chan.addListener(ChannelJoin, handle_join)

  def _handle_openflow_FlowStatsReceived (self, event):
    log.debug("Flow stats received")

  def _handle_openflow_PortStatsReceived (self, event):
    log.debug("Port stats received")

  def _handle_openflow_ConnectionUp (self, event):
    #sw = poxutil.dpid_to_str(event.dpid)
    if not event.dpid in self.graph:
      self.graph.add_node(event.dpid)

  def _handle_openflow_ConnectionDown (self, event):
    #sw = poxutil.dpid_to_str(event.dpid)
    if event.dpid in self.graph:
      self.graph.remove_node(event.dpid)

  def _handle_openflow_discovery_LinkEvent (self, event):
    s1 = event.link.dpid1
    s2 = event.link.dpid2

    assert s1 in self.graph
    assert s2 in self.graph
    port_dict = {s1: event.port_for_dpid(s1),
                 s2: event.port_for_dpid(s2),
                 'w': 1}

    if event.added:
      self.graph.add_edge(s1, s2, port_dict)
    elif event.removed:
      if (s1, s2) in self.graph.edges():
        self.graph.remove_edge(s1, s2)

  def find_edge_disjoint_paths(self, fully_disjoint=False):
    def path_to_edgelist(path):
      edgelist=[]
      i=1
      while i < len(path):
          a,b = path[i-1],path[i]
          edgelist.append((a, b))
          i+=1
      return edgelist
    # working copy of the graph
    tempgraph = self.graph.copy()
    #INF2
    inf2 = (max(tempgraph.edges(data=True), key=lambda e: e[2]['w']))[2]['w']*tempgraph.number_of_edges() + 1
    # Multi-path graph
    paths = nx.MultiDiGraph()
    # Search for paths
    try:
      while True:
        shortest_path = path_to_edgelist(nx.shortest_path(tempgraph, self.source, self.dest, 'w'))
        # assign infinite weight to edges on that path, and negative weight
        # to back edges
        for s, t in shortest_path:
            if tempgraph.has_edge(s,t):
              tempgraph.remove_edge(s,t) #G[s][t]['weight'] += inf2
            if tempgraph.has_edge(t,s):
              tempgraph[t][s]['weight'] = 0 #-G[t][s]['weight']
            # ^^ does it work with zero-weight back edges? let's see..
        #add that path, erasing interlacing edges
        for s, t in shortest_path:
          if paths.has_edge(t, s):
            paths.remove_edge(t, s)
          else:
            paths.add_edge(s, t)
    except nx.NetworkXNoPath as ex:
      # we're done here, split the paths and add them to self.paths
      while paths.number_of_edges():
        p = nx.shortest_path(paths, self.source, self.dest)
        for s, t in path_to_edgelist(p):
          paths.remove_edge(s, t)
        self.paths.append(p)

  def establish_connection(self, source, dest, sourcename=None, destname=None):
    self.source = source
    self.dest = dest
    self.find_edge_disjoint_paths()
    path1 = self.paths[0]
    #assume hosts are always 1 because mininet for now FIXME TODO
    entry_host_port = 1
    exit_host_port = 1
    #flooooows
    install_flows(path1[0],
          entry_host_port,
          self.graph[path1[0]][path1[1]][path1[0]])
    for i in xrange(1, len(path1)-1):
      #link the neighbours together
      install_flows(path1[i],
            self.graph[path1[i]][path1[i-1]][path1[i]],
            self.graph[path1[i]][path1[i+1]][path1[i]])
    install_flows(path1[-1],
          self.graph[path1[-1]][path1[-2]][path1[-1]],
          exit_host_port)
    # now we get to set up the timer and periodically grab stats
    self.timer.start()

  def change_connection(self):
    curr_path = self.paths[self.active_path]
    self.active_path += 1
    self.active_path %= 2
    new_path = self.paths[self.active_path]
    #assume hosts are always 1 because mininet for now FIXME TODO
    entry_host_port = 1
    exit_host_port = 1
    #flooooows
    install_flows(curr_path[0],
          entry_host_port,
          self.graph[curr_path[0]][curr_path[1]][curr_path[0]],
          remove=True)
    for i in xrange(1, len(curr_path)-1):
      #unlink the neighbours
      install_flows(curr_path[i],
            self.graph[curr_path[i]][curr_path[i-1]][curr_path[i]],
            self.graph[curr_path[i]][curr_path[i+1]][curr_path[i]],
            remove=True)
    install_flows(curr_path[-1],
          self.graph[curr_path[-1]][curr_path[-2]][curr_path[-1]],
          exit_host_port,
          remove=True)

    install_flows(new_path[0],
          entry_host_port,
          self.graph[new_path[0]][new_path[1]][new_path[0]])
    for i in xrange(1, len(new_path)-1):
      #link the neighbours together
      install_flows(new_path[i],
            self.graph[new_path[i]][new_path[i-1]][new_path[i]],
            self.graph[new_path[i]][new_path[i+1]][new_path[i]])
    install_flows(new_path[-1],
          self.graph[new_path[-1]][new_path[-2]][new_path[-1]],
          exit_host_port)

  def path_established(self, path):
    """
    Notify subscribers that a path between the source and destination
    host has been established.
    """
    pass

  def path_degradation_detected(self, path):
    """
    Notify subscribers that degradation along a path has been detected.
    """
    pass

  def longest_shortest_path(self):
    """finds the longest shortest path"""
    l = []
    for s in self.graph.nodes():
      for d in self.graph.nodes():
        try:
          p = nx.shortest_path(self.graph, source=s, target=d)
          if len(p) > len(l):
            l = p
        except nx.NetworkXNoPath as ex:
          # nodes could be disconnected
          pass
    return l


def install_flows(sw, port1, port2, remove=False):
  """installs forward and reverse flows"""
  command = of.OFPFC_DELETE if remove else of.OFPFC_ADD
  msg = of.ofp_flow_mod(command=command)
  msg.match.in_port=port1
  msg.actions.append(of.ofp_action_output(port=port2))
  core.openflow.connections[sw].send(msg)
  msg = of.ofp_flow_mod(command=command)
  msg.match.in_port=port2
  msg.actions.append(of.ofp_action_output(port=port1))
  core.openflow.connections[sw].send(msg)

def _go_up (event): pass

@poxutil.eval_args
def launch (source=None, dest=None, csvpath=None, sourcename=None, destname=None):
  """
  Launch function
  """

  if not core.hasComponent("RCP"):
    core.registerNew(RCP, source, dest, csvpath, sourcename, destname)
  core.addListenerByName("UpEvent", _go_up)
