import collectd

def write(vl, data=None):
  print vl
  for i in vl.values:
    print "wow such data: %s (%s): %f" % (vl.plugin, vl.type, i)
    
collectd.register_write(write)
