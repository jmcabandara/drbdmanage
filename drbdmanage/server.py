#!/usr/bin/python
"""
    drbdmanage - management of distributed DRBD9 resources
    Copyright (C) 2013, 2014   LINBIT HA-Solutions GmbH
                               Author: R. Altnoeder

    This program is free software: you can redistribute it and/or modify
    it under the terms of the GNU General Public License as published by
    the Free Software Foundation, either version 3 of the License, or
    (at your option) any later version.

    This program is distributed in the hope that it will be useful,
    but WITHOUT ANY WARRANTY; without even the implied warranty of
    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
    GNU General Public License for more details.

    You should have received a copy of the GNU General Public License
    along with this program.  If not, see <http://www.gnu.org/licenses/>.
"""

import sys
import os
import gobject
import subprocess
import fcntl
import logging
import logging.handlers

from drbdmanage.dbusserver import *
from drbdmanage.exceptions import *
from drbdmanage.drbd.drbdcore import *
from drbdmanage.drbd.persistence import *
from drbdmanage.storage.storagecore import *
from drbdmanage.conf.conffile import *
from drbdmanage.utils import *
from drbdmanage.consts import *


class DrbdManageServer(object):
    
    """
    drbdmanage server - main class
    """
    
    DM_VERSION = "0.10"
    
    CONFFILE = "/etc/drbdmanaged.conf"
    EVT_UTIL = "drbdsetup"
    
    EVT_TYPE_CHANGE = "change"
    EVT_SRC_CON     = "connection"
    EVT_SRC_RES     = "resource"
    EVT_ARG_NAME    = "name"
    EVT_ARG_ROLE    = "role"
    EVT_ARG_CON     = "connection"
    
    EVT_ROLE_PRIMARY   = "Primary"
    EVT_ROLE_SECONDARY = "Secondary"
    DRBDCTRL_RES_NAME  = ".drbdctrl"
    
    LOGGING_FORMAT = "drbdmanaged[%(process)d]: %(levelname)-10s %(message)s"
    
    KEY_STOR_NAME      = "storage-plugin"
    KEY_DEPLOYER_NAME  = "deployer-plugin"
    KEY_MAX_NODE_ID    = "max-node-id"
    KEY_MAX_PEERS      = "max-peers"
    KEY_MIN_MINOR_NR   = "min-minor-nr"
    KEY_MIN_PORT_NR    = "min-port-nr"
    KEY_MAX_PORT_NR    = "max-port-nr"
    
    KEY_DRBDADM_PATH   = "drbdadm-path"
    KEY_EXTEND_PATH    = "extend-path"
    KEY_DRBD_CONFPATH  = "drbd-conf-path"
    
    KEY_DEFAULT_SECRET = "default-secret"
    
    DEFAULT_MAX_NODE_ID  =   31
    DEFAULT_MAX_PEERS    =    7
    DEFAULT_MIN_MINOR_NR =  100
    DEFAULT_MIN_PORT_NR  = 7000
    DEFAULT_MAX_PORT_NR  = 7999
    
    # defaults
    CONF_DEFAULTS = {
      KEY_STOR_NAME      : "drbdmanage.storage.lvm.LVM",
      KEY_DEPLOYER_NAME  : "drbdmanage.deployers.BalancedDeployer",
      KEY_MAX_NODE_ID    : str(DEFAULT_MAX_NODE_ID),
      KEY_MAX_PEERS      : str(DEFAULT_MAX_PEERS),
      KEY_MIN_MINOR_NR   : str(DEFAULT_MIN_MINOR_NR),
      KEY_MIN_PORT_NR    : str(DEFAULT_MIN_PORT_NR),
      KEY_MAX_PORT_NR    : str(DEFAULT_MAX_PORT_NR),
      KEY_DRBDADM_PATH   : "/usr/sbin",
      KEY_EXTEND_PATH    : "/sbin:/usr/sbin:/bin:/usr/bin",
      KEY_DRBD_CONFPATH  : "/var/drbd.d",
      KEY_DEFAULT_SECRET : "default"
    }
    
    # BlockDevice manager
    _bd_mgr    = None
    # Configuration objects maps
    _nodes     = None
    _resources = None
    # Events log pipe
    _evt_file  = None
    # Subprocess handle for the events log source
    _proc_evt  = None
    # Reader for the events log
    _reader    = None
    # Event handler for incoming data
    _evt_in_h  = None
    # Event handler for the hangup event on the subprocess pipe
    _evt_hup_h = None
    
    # The name of the node this server is running on
    _instance_node_name = None
    
    # The hash of the currently loaded configuration
    _conf_hash = None
    
    # Server configuration
    _conf      = None
    
    # Logging
    _root_logger = None
    DM_LOGLEVELS = {
      "CRITICAL" : logging.CRITICAL,
      "ERROR"    : logging.ERROR,
      "WARNING"  : logging.WARNING,
      "INFO"     : logging.INFO,
      "DEBUG"    : logging.DEBUG
    }
    
    # DEBUGGING FLAGS
    dbg_events = False
    
    
    def __init__(self):
        """
        Initialize and start up the drbdmanage server
        """
        # The "(unknown)" node name never matches, because brackets are not
        # allowed characters in node names
        self._instance_node_name = "(unknown)"
        if len(sys.argv) >= 2:
            self._instance_node_name = sys.argv[1]
        else:
            try:
                uname = os.uname()
                if len(uname) >= 2:
                    self._instance_node_name = uname[1]
            except Exception:
                pass
        self.init_logging()
        logging.info("DRBDmanage server, version %s"
              " -- initializing on node '%s'"
              % (self.DM_VERSION, self._instance_node_name))
        self._nodes     = dict()
        self._resources = dict()
        # load the server configuration file
        self.load_server_conf()
        # ensure that the PATH environment variable is set up
        extend_path(self.get_conf_value(self.KEY_EXTEND_PATH))
        self._bd_mgr    = BlockDeviceManager(self._conf[self.KEY_STOR_NAME])
        self._drbd_mgr  = DrbdManager(self)
        self._drbd_mgr.drbdctrl_res_up()
        # load the drbdmanage database from the control volume
        self.load_conf()
        # start up the resources deployed by drbdmanage on the current node
        self._drbd_mgr.initial_up()
        try:
            self.init_events()
        except (OSError, IOError):
            logging.critical("failed to initialize drbdsetup events tracing, "
                "aborting startup")
            exit(1)
        # update storage pool information if it is unknown
        inst_node = self.get_instance_node()
        if inst_node is not None:
            poolsize = inst_node.get_poolsize()
            poolfree = inst_node.get_poolfree()
            if poolsize == -1 or poolfree == -1:
                self.update_pool()
    
    
    def run(self):
        """
        drbdmanage server main loop
        
        Waits for client requests or events generated by "drbdsetup events".
        """
        gobject.MainLoop().run()


    def init_events(self):
        """
        Initialize callbacks for events generated by "drbdsetup events"
        
        Starts "drbdsetup events" as a child process with drbdsetup's standard
        output piped back to the drbdmanage server. A GMainLoop controlled
        callback is set up, so the drbdmanage server can react to log entries
        generated by drbdsetup.
        
        The callback functions are:
            drbd_event        whenever data becomes readable on the pipe
            restart_events    when the pipe needs to be reopened
        """
        # FIXME: maybe any existing subprocess should be killed first?
        evt_util = build_path(self.get_conf_value(self.KEY_DRBDADM_PATH),
          self.EVT_UTIL)
        self._proc_evt = subprocess.Popen([self.EVT_UTIL, "events", "all"], 0,
          evt_util, stdout=subprocess.PIPE, close_fds=True)
        self._evt_file = self._proc_evt.stdout
        fcntl.fcntl(self._evt_file.fileno(),
          fcntl.F_SETFL, fcntl.F_GETFL | os.O_NONBLOCK)
        self._reader = NioLineReader(self._evt_file)
        # detect readable data on the pipe
        self._evt_in_h = gobject.io_add_watch(self._evt_file.fileno(),
          gobject.IO_IN, self.drbd_event)
        # detect broken pipe
        self._evt_hup_h = gobject.io_add_watch(self._evt_file.fileno(),
          gobject.IO_HUP, self.restart_events)
    
    
    def restart_events(self, evt_fd, condition):
        """
        Detects broken pipe, killed drbdsetup process, etc. and reinitialize
        the event callbacks
        """
        # unregister any existing event handlers for the events log
        log_error = True
        retry = False
        logging.error("drbdsetup events tracing has failed, restarting")
        if self._evt_in_h is not None:
            gobject.source_remove(self._evt_in_h)
        while True:
            try:
                self.init_events()
                retry = False
            except OSError:
                retry = True
            except IOError:
                retry = True
            if log_error:
                logging.critical("cannot restart drbdsetup events tracing, "
                    "this node is inoperational")
                logging.critical("retrying restart of drbdsetup events "
                    "tracing every 30 seconds")
                log_error = False
            if not retry:
                break
            time.sleep(30)
        logging.info("drbdsetup events tracing reestablished")
        self._drbd_mgr.run()
        # Unregister this event handler, init_events has registered a new one
        # for the new events pipe
        return False
    
    
    def drbd_event(self, evt_fd, condition):
        """
        Receives log entries from the "drbdsetup events" child process
        
        Detect state changes by reading the drbdsetup events log. If another
        node modifies the configuration on the drbdmanage control volume,
        this becomes visible in the event log as a remote role change on the
        drbdmanage control volume. In this case, the DRBD resource manager is
        invoked to check, whether any changes are required on this node.
        """
        changed = False
        while True:
            line = self._reader.readline()
            if line is None:
                break
            else:
                if line.endswith("\n"):
                    line = line[:len(line) - 1]
                if self.dbg_events:
                    logging.debug("received event line: %s" % line)
                sys.stderr.flush()
                if not changed:
                    event_type   = get_event_type(line)
                    event_source = get_event_source(line)
                    if event_type is not None and event_source is not None:
                        # If the configuration resource changes to "Secondary"
                        # role on a connected node, the configuration may have
                        # changed
                        if event_type == self.EVT_TYPE_CHANGE and \
                          event_source == self.EVT_SRC_CON:
                            event_res  = get_event_arg(line, self.EVT_ARG_NAME)
                            event_role = get_event_arg(line, self.EVT_ARG_ROLE)
                            if event_res == self.DRBDCTRL_RES_NAME and \
                              event_role == self.EVT_ROLE_SECONDARY:
                                event_con = get_event_arg(line,
                                  self.EVT_ARG_CON)
                                # if there is no "connection:" change
                                # (peer connecting/disconnecting),
                                # then the event is assumed to be a role change
                                if event_con is None:
                                    changed = True
        if changed:
            self._drbd_mgr.run()
        # True = GMainLoop shall not unregister this event handler
        return True
    
    
    def init_logging(self):
        """
        Initialize global logging
        """
        self._root_logger = logging.getLogger("")
        syslog_h    = logging.handlers.SysLogHandler(address="/dev/log")
        syslog_f    = logging.Formatter(fmt=self.LOGGING_FORMAT)
        syslog_h.setFormatter(syslog_f)
        self._root_logger.addHandler(syslog_h)
        self._root_logger.setLevel(logging.INFO)
    
    
    def load_server_conf(self):
        """
        Loads the server configuration file
        
        The server configuration is loaded from the server's configuration
        file (commonly /etc/drbdmanaged.conf), and is then unified with any
        existing default values.
        Values from the configuration override default configuration values.
        Values not specified in the configuration file are inherited from 
        the default configuration. Any values specified in the configuration
        file that are not known in the default configuration are discarded.
        """
        in_file = None
        try:
            in_file = open(self.CONFFILE, "r")
            conffile = ConfFile(in_file)
            conf_loaded = conffile.get_conf()
            if conf_loaded is not None:
                self._conf = (
                  ConfFile.conf_defaults_merge(self.CONF_DEFAULTS, conf_loaded)
                  )
            else:
                self._conf = self.CONF_DEFAULTS
        except IOError as ioerr:
            if ioerr.errno == errno.EACCES:
                logging.warning("cannot open configuration file '%s', "
                  "permission denied" % self.CONFFILE)
            elif ioerr.errno != errno.ENOENT:
                logging.warning("cannot open configuration file '%s', "
                  "error returned by the OS is: %s"
                  % (self.CONFFILE, ioerr.strerror))
        finally:
            if self._conf is None:
                self._conf = self.CONF_DEFAULTS
            if in_file is not None:
                in_file.close()
    
    
    def get_conf_value(self, key):
        """
        Return a configuration value.
        
        All configuration values are stored as strings. If another type is
        required, any function that retrieves the configuration value
        should attempt to convert the value to the required type. If that
        conversion fails, the configuration value from the default
        configuration (CONF_DEFAULTS) should be used instead.
        
        @param   key: the name (key) of the configuration value
        @return: configuration value
        @rtype:  str
        """
        return self._conf.get(key)
    
    
    def get_drbd_mgr(self):
        return self._drbd_mgr
    
    
    def get_bd_mgr(self):
        return self._bd_mgr
    
    
    def iterate_nodes(self):
        """
        Returns an iterator over all registered nodes
        """
        return self._nodes.itervalues()
    

    def iterate_resources(self):
        """
        Returns an iterator over all registered resources
        """
        return self._resources.itervalues()
    
    
    def get_node(self, name):
        """
        Retrieves a node by its name
        
        @return: the named node object or None if no object with the specified
                 name exists
        """
        node = None
        try:
            node = self._nodes[name]
        except KeyError:
            pass
        except Exception as exc:
            DrbdManageServer.catch_internal_error(exc)
            return DM_DEBUG
        return node
    
    
    def get_resource(self, name):
        """
        Retrieves a resource by its name
        
        @return: the named resource object or None if no object with the
                 specified name exists
        """
        resource = None
        try:
            resource = self._resources.get(name)
        except Exception as exc:
            DrbdManageServer.catch_internal_error(exc)
        return resource
    
    
    def get_volume(self, name, vol_id):
        """
        Retrieves a volume by its name
        
        @return: the volume object specified by the name of the resource it is
                 contained in and by its volume id or None if no object with
                 the specified name exists
        """
        volume = None
        try:
            resource = self._resources.get(name)
            if resource is not None:
                volume = resource.get_volume(vol_id)
        except Exception as exc:
            DrbdManageServer.catch_internal_error(exc)
        return volume
        
    
    # Get the node this server is running on
    def get_instance_node(self):
        """
        Retrieves the node that represents the host this instance of
        drbdmanage is currently running on.
        
        @return: the node object this instance of drbdmanage is running on
                 or None if no node object is registered for this host
        """
        node = None
        try:
            node = self._nodes[self._instance_node_name]
        except KeyError:
            pass
        except Exception as exc:
            DrbdManageServer.catch_internal_error(exc)
        return node
    
    
    # Get the name of the node this server is running on
    def get_instance_node_name(self):
        """
        Returns the name used by the drbdmanage server to look for a node
        object that represents the hosts this drbdmanage server is currently
        running on
        
        @return: name of the node object this drbdmanage server is running on
        """
        return self._instance_node_name
    
    
    def create_node(self, name, props):
        """
        Registers a DRBD cluster node
        
        @return: standard return code defined in drbdmanage.exceptions
        """
        fn_rc   = DM_EPERSIST
        persist = None
        node    = None
        try:
            persist = self.begin_modify_conf()
            if persist is not None:
                if self._nodes.get(name) is not None:
                    fn_rc = DM_EEXIST
                else:
                    addr    = None
                    addrfam = DrbdNode.AF_IPV4
                    try:
                        addr     = props[NODE_ADDR]
                    except KeyError:
                        pass
                    try:
                        af_label = props[NODE_AF]
                        if af_label == DrbdNode.AF_IPV4_LABEL:
                            addrfam = DrbdNode.AF_IPV4
                        elif af_label == DrbdNode.AF_IPV6_LABEL:
                            addrfam = DrbdNode.AF_IPV6
                    except KeyError:
                        pass
                    try:
                        if addr is not None and addrfam is not None:
                            node = DrbdNode(name, addr, addrfam)
                            self._nodes[node.get_name()] = node
                            self.save_conf_data(persist)
                            fn_rc = DM_SUCCESS
                        else:
                            fn_rc = DM_EINVAL
                    except InvalidNameException:
                        fn_rc = DM_ENAME
        except PersistenceException:
            pass
        except Exception as exc:
            DrbdManageServer.catch_internal_error(exc)
            fn_rc = DM_DEBUG
        finally:
            self.end_modify_conf(persist)
        return fn_rc
    
    
    def remove_node(self, name, force):
        """
        Marks a node for removal from the DRBD cluster
        * Orders the node to undeploy all volumes
        * Orders all other nodes to disconnect from the node
        
        @return: standard return code defined in drbdmanage.exceptions
        """
        fn_rc      = DM_EPERSIST
        persist = None
        node    = None
        try:
            persist = self.begin_modify_conf()
            if persist is not None:
                node = self._nodes[name]
                if (not force) and node.has_assignments():
                    for assignment in node.iterate_assignments():
                        assignment.undeploy()
                        resource = assignment.get_resource()
                        for peer_assg in resource.iterate_assignments():
                            peer_assg.update_connections()
                    node.remove()
                    self._drbd_mgr.perform_changes()
                else:
                    # drop all associated assignments
                    for assignment in node.iterate_assignments():
                        resource = assignment.get_resource()
                        resource.remove_assignment(assignment)
                        # tell the remaining nodes that have this resource to
                        # drop the connection to the deleted node
                        for peer_assg in resource.iterate_assignments():
                            peer_assg.update_connections()
                    del self._nodes[name]
                self.save_conf_data(persist)
                fn_rc = DM_SUCCESS
        except KeyError:
            fn_rc = DM_ENOENT
        except PersistenceException:
            pass
        except Exception as exc:
            DrbdManageServer.catch_internal_error(exc)
            fn_rc = DM_DEBUG
        finally:
            self.end_modify_conf(persist)
        return fn_rc
    
    
    def create_resource(self, name, props):
        """
        Registers a new resource that can be deployed to DRBD cluster nodes
        
        @return: standard return code defined in drbdmanage.exceptions
        """
        fn_rc = DM_EPERSIST
        resource = None
        persist  = None
        try:
            persist = self.begin_modify_conf()
            if persist is not None:
                resource = self._resources.get(name)
                if resource is not None:
                    fn_rc = DM_EEXIST
                else:
                    port = DrbdResource.PORT_NR_AUTO
                    secret = self.get_conf_value(self.KEY_DEFAULT_SECRET)
                    try:
                        port = int(props[RES_PORT])
                    except KeyError:
                        pass
                    try:
                        secret = props[RES_SECRET]
                    except KeyError:
                        pass
                    if port == DrbdResource.PORT_NR_AUTO:
                        port = self.get_free_port_nr()
                    if port < 1 or port > 65535:
                        fn_rc = DM_EPORT
                    else:
                        resource = DrbdResource(name, port)
                        resource.set_secret(secret)
                        self._resources[resource.get_name()] = resource
                        self.save_conf_data(persist)
                        fn_rc = DM_SUCCESS
        except ValueError:
            fn_rc = DM_EINVAL
        except PersistenceException:
            pass
        except InvalidNameException:
            fn_rc = DM_ENAME
        except Exception as exc:
            DrbdManageServer.catch_internal_error(exc)
            fn_rc = DM_DEBUG
        finally:
            self.end_modify_conf(persist)
        return fn_rc
    
    
    def modify_resource(self, name, props):
        """
        Modifies resource properties
        
        @return: standard return code defined in drbdmanage.exceptions
        """
        fn_rc = DM_EPERSIST
        resource = None
        persist  = None
        try:
            persist = self.begin_modify_conf()
            if persist is not None:
                resource = self._resources.get(name)
                if resource is None:
                    fn_rc = DM_ENOENT
                else:
                    port_nr = None
                    secret  = None
                    for keyval in props.iteritems():
                        key = keyval[0]
                        val = keyval[1]
                        if key == RES_PORT:
                            try:
                                port_nr = int(val)
                            except ValueError:
                                fn_rc = DM_EINVAL
                        elif key == RES_SECRET:
                            secret = val
                        else:
                            fn_rc = DM_EINVAL
                        # TODO: port change - not implemented
                        if secret is not None:
                            resource.set_secret(secret)
                        self._resources[resource.get_name()] = resource
                        self.save_conf_data(persist)
                        fn_rc = DM_SUCCESS
        except PersistenceException:
            pass
        except InvalidNameException:
            fn_rc = DM_ENAME
        except Exception as exc:
            DrbdManageServer.catch_internal_error(exc)
            fn_rc = DM_DEBUG
        finally:
            self.end_modify_conf(persist)
        return fn_rc
    
    
    def remove_resource(self, name, force):
        """
        Marks a resource for removal from the DRBD cluster
        * Orders all nodes to undeploy all volume of this resource
        
        @return: standard return code defined in drbdmanage.exceptions
        """
        fn_rc = DM_EPERSIST
        persist  = None
        resource = None
        try:
            persist = self.begin_modify_conf()
            if persist is not None:
                resource = self._resources[name]
                if (not force) and resource.has_assignments():
                    for assg in resource.iterate_assignments():
                        assg.undeploy()
                    resource.remove()
                    self._drbd_mgr.perform_changes()
                else:
                    for assg in resource.iterate_assignments():
                        node = assg.get_node()
                        node.remove_assignment(assg)
                    del self._resources[resource.get_name()]
                self.save_conf_data(persist)
                fn_rc = DM_SUCCESS
        except KeyError:
            fn_rc = DM_ENOENT
        except PersistenceException:
            pass
        except Exception as exc:
            DrbdManageServer.catch_internal_error(exc)
            fn_rc = DM_DEBUG
        finally:
            self.end_modify_conf(persist)
        return fn_rc
    
    
    def create_volume(self, name, size_kiB, props):
        """
        Adds a volume to a resource
        
        @return: standard return code defined in drbdmanage.exceptions
        """
        fn_rc      = DM_EPERSIST
        volume  = None
        persist = None
        try:
            persist = self.begin_modify_conf()
            if persist is not None:
                resource = self._resources.get(name)
                if resource is None:
                    fn_rc = DM_ENOENT
                else:
                    minor = MinorNr.MINOR_NR_AUTO
                    try:
                        minor = int(props[VOL_MINOR])
                    except KeyError:
                        pass
                    except ValueError:
                        raise InvalidMinorNrException
                    if minor == MinorNr.MINOR_NR_AUTO:
                        minor = self.get_free_minor_nr()
                    if minor == MinorNr.MINOR_NR_ERROR:
                        raise InvalidMinorNrException
                    vol_id = self.get_free_volume_id(resource)
                    if vol_id == -1:
                        fn_rc = DM_EVOLID
                    else:
                        volume = DrbdVolume(vol_id, size_kiB, MinorNr(minor))
                        resource.add_volume(volume)
                        for assg in resource.iterate_assignments():
                            assg.update_volume_states()
                            vol_st = assg.get_volume_state(volume.get_id())
                            if vol_st is not None:
                                vol_st.deploy()
                                vol_st.attach()
                        self._drbd_mgr.perform_changes()
                        self.save_conf_data(persist)
                        fn_rc = DM_SUCCESS
        except InvalidNameException:
            fn_rc = DM_ENAME
        except InvalidMinorNrException:
            fn_rc = DM_EMINOR
        except VolSizeRangeException:
            fn_rc = DM_EVOLSZ
        except PersistenceException:
            pass
        except Exception as exc:
            DrbdManageServer.catch_internal_error(exc)
            fn_rc = DM_DEBUG
        finally:
            self.end_modify_conf(persist)
        return fn_rc
    
    
    def remove_volume(self, name, vol_id, force):
        """
        Marks a volume for removal from the DRBD cluster
        * Orders all nodes to undeploy the volume
        
        @return: standard return code defined in drbdmanage.exceptions
        """
        fn_rc      = DM_EPERSIST
        persist = None
        try:
            persist = self.begin_modify_conf()
            if persist is not None:
                resource = self._resources[name]
                volume   = resource.get_volume(vol_id)
                if volume is None:
                    raise KeyError
                else:
                    if (not force) and resource.has_assignments():
                        for assg in resource.iterate_assignments():
                            peer_vol_st = assg.get_volume_state(vol_id)
                            if peer_vol_st is not None:
                                peer_vol_st.undeploy()
                        volume.remove()
                        self._drbd_mgr.perform_changes()
                    else:
                        resource.remove_volume(vol_id)
                        for assg in resource.iterate_assignments():
                            assg.remove_volume_state(vol_id)                    
                    self.save_conf_data(persist)
                    fn_rc = DM_SUCCESS
        except KeyError:
            fn_rc = DM_ENOENT
        except PersistenceException:
            pass
        except Exception as exc:
            DrbdManageServer.catch_internal_error(exc)
            fn_rc = DM_DEBUG
        finally:
            self.end_modify_conf(persist)
        return fn_rc
    
    
    def assign(self, node_name, resource_name, cstate, tstate):
        """
        Assigns a resource to a node
        * Orders all participating nodes to deploy all volumes of
          resource
          
        @return: standard return code defined in drbdmanage.exceptions
        """
        fn_rc      = DM_EPERSIST
        persist = None
        try:
            persist = self.begin_modify_conf()
            if persist is not None:
                node     = self._nodes.get(node_name)
                resource = self._resources.get(resource_name)
                if node is None or resource is None:
                    fn_rc = DM_ENOENT
                else:
                    assignment = node.get_assignment(resource.get_name())
                    if assignment is not None:
                        fn_rc = DM_EEXIST
                    else:
                        overwrite = (True if (tstate
                          & Assignment.FLAG_OVERWRITE) != 0 else False)
                        if (overwrite and
                          (tstate & Assignment.FLAG_DISKLESS) != 0):
                            fn_rc = DM_EINVAL
                        elif (overwrite and
                          (tstate & Assignment.FLAG_DISCARD) != 0):
                            fn_rc = DM_EINVAL
                        else:
                            # If the overwrite flag is set on this
                            # assignment, turn it off on all the assignments
                            # to other nodes
                            if overwrite:
                                for assg in resource.iterate_assignments():
                                    assg.clear_tstate_flags(
                                      Assignment.FLAG_OVERWRITE)
                            fn_rc = self._assign(node, resource, cstate, tstate)
                            if fn_rc == DM_SUCCESS:
                                self._drbd_mgr.perform_changes()
                                self.save_conf_data(persist)
        except PersistenceException:
            pass
        except Exception as exc:
            DrbdManageServer.catch_internal_error(exc)
            fn_rc = DM_DEBUG
        finally:
            self.end_modify_conf(persist)
        return fn_rc
    
    
    def unassign(self, node_name, resource_name, force):
        """
        Removes the assignment of a resource to a node
        * Orders the node to undeploy all volumes of the resource
        
        @return: standard return code defined in drbdmanage.exceptions
        """
        fn_rc      = DM_EPERSIST
        persist = None
        try:
            persist = self.begin_modify_conf()
            if persist is not None:
                node   = self._nodes.get(node_name)
                resource = self._resources.get(resource_name)
                if node is None or resource is None:
                    fn_rc = DM_ENOENT
                else:
                    assignment = node.get_assignment(resource.get_name())
                    if assignment is None:
                        fn_rc = DM_ENOENT
                    else:
                        fn_rc = self._unassign(assignment, force)
                        if fn_rc == DM_SUCCESS:
                            self._drbd_mgr.perform_changes()
                            self.save_conf_data(persist)
        except PersistenceException:
            pass
        except Exception as exc:
            DrbdManageServer.catch_internal_error(exc)
            fn_rc = DM_DEBUG
        finally:
            self.end_modify_conf(persist)
        return fn_rc
    
    
    def _assign(self, node, resource, cstate, tstate):
        """
        Implementation - see assign()
        
        @return: standard return code defined in drbdmanage.exceptions
        """
        fn_rc = DM_DEBUG
        try:
            node_id = self.get_free_node_id(resource)
            if node_id == -1:
                # no free node ids
                fn_rc = DM_ENODEID
            else:
                # The block device is set upon allocation of the backend
                # storage area on the target node
                assignment = Assignment(node, resource, node_id,
                  cstate, tstate)
                for vol_state in assignment.iterate_volume_states():
                    vol_state.deploy()
                    if tstate & Assignment.FLAG_DISKLESS == 0:
                        vol_state.attach()
                node.add_assignment(assignment)
                resource.add_assignment(assignment)
                for assignment in resource.iterate_assignments():
                    if assignment.is_deployed():
                        assignment.update_connections()
                fn_rc = DM_SUCCESS
        except Exception as exc:
            DrbdManageServer.catch_internal_error(exc)
        return fn_rc
    
    
    def _unassign(self, assignment, force):
        """
        Implementation - see unassign()
        
        @return: standard return code defined in drbdmanage.exceptions
        """
        try:
            node     = assignment.get_node()
            resource = assignment.get_resource()
            if (not force) and assignment.is_deployed():
                assignment.disconnect()
                assignment.undeploy()
            else:
                assignment.remove()
            for assignment in resource.iterate_assignments():
                if assignment.get_node() != node \
                  and assignment.is_deployed():
                    assignment.update_connections()
            self.cleanup()
        except Exception as exc:
            DrbdManageServer.catch_internal_error(exc)
            return DM_DEBUG
        return DM_SUCCESS
    
    
    def auto_deploy(self, resource_name, count):
        """
        Deploys a resource to a number of nodes
        
        @return: standard return code defined in drbdmanage.exceptions
        """
        fn_rc      = DM_DEBUG
        persist = None
        try:
            deployer = plugin_import(
              self.get_conf_value(self.KEY_DEPLOYER_NAME))
            if deployer is None:
                raise PluginException

            persist  = self.begin_modify_conf()
            if persist is None:
                raise PersistenceException
            
            maxnodes = self.DEFAULT_MAX_NODE_ID
            try:
                maxnodes = int(self._conf[self.KEY_MAX_NODE_ID]) + 1
            except ValueError:
                pass
            crtnodes = len(self._nodes)
            maxcount = maxnodes if maxnodes < crtnodes else crtnodes
            resource = self._resources[resource_name]
            if ((not resource.has_assignments()) and count >= 1
              and count <= maxcount):
                """
                calculate the amount of memory required to deploy all
                volumes of the resource
                """
                size_sum = 0
                for vol in resource.iterate_volumes():
                    size_sum += vol.get_size_kiB()
                """
                Call the deployer plugin to select nodes for deploying the
                resource
                """
                selected = []
                fn_rc = deployer.deploy_select(self._nodes, selected, count,
                  size_sum, True)
                if fn_rc == DM_SUCCESS:
                    tstate = (Assignment.FLAG_DEPLOY | Assignment.FLAG_CONNECT)
                    for node in selected:
                        self._assign(node, resource, 0, tstate)
                    self._drbd_mgr.perform_changes()
                    self.save_conf_data(persist)
                    fn_rc = DM_SUCCESS
            else:
                if resource.has_assignments():
                    fn_rc = DM_EEXIST
                elif not count >= 1:
                    fn_rc = DM_EINVAL
                else: # count > number of nodes
                    fn_rc = DM_ENODECNT
        except KeyError:
            fn_rc = DM_ENOENT
        except PersistenceException:
            fn_rc = DM_EPERSIST
        except PluginException:
            fn_rc = DM_EPLUGIN
        except Exception as exc:
            DrbdManageServer.catch_internal_error(exc)
            fn_rc = DM_DEBUG
        finally:
            self.end_modify_conf(persist)
        return fn_rc
    
    
    def auto_extend(self, resource_name, count, rel_flag):
        """
        Extend a deployment by a number of nodes
        
        @return: standard return code defined in drbdmanage.exceptions
        """
        fn_rc      = DM_DEBUG
        persist = None
        try:
            deployer = plugin_import(
              self.get_conf_value(self.KEY_DEPLOYER_NAME))
            if deployer is None:
                raise PluginException

            persist  = self.begin_modify_conf()
            if persist is None:
                raise PersistenceException
            
            maxnodes = self.DEFAULT_MAX_NODE_ID
            try:
                maxnodes = int(self._conf[self.KEY_MAX_NODE_ID]) + 1
            except ValueError:
                pass
            crtnodes = len(self._nodes)
            maxcount = maxnodes if maxnodes < crtnodes else crtnodes
            resource = self._resources[resource_name]
            assigned_count = resource.assigned_count()
            if rel_flag:
                final_count = count + assigned_count
            else:
                final_count = count
                if count > assigned_count:
                    count -= assigned_count
                else:
                    count = 0
            if ((resource.has_assignments()) and count >= 1
              and final_count <= maxcount):
                """
                calculate the amount of memory required to deploy all
                volumes of the resource
                """
                size_sum = 0
                for vol in resource.iterate_volumes():
                    size_sum += vol.get_size_kiB()
                """
                filter nodes that do not have the resource deployed yet
                """
                undeployed = dict()
                for node in self._nodes.itervalues():
                    if (resource.get_assignment(node.get_name())
                      is not None):
                        # skip nodes, where:
                        #   - resource is deployed already
                        #   - resource is being deployed
                        #   - resource is being undeployed
                        continue
                    undeployed[node.get_name()] = node
                """
                Call the deployer plugin to select nodes for deploying the
                resource
                """
                selected = []
                fn_rc = deployer.deploy_select(undeployed, selected, count,
                  size_sum, True)
                if fn_rc == DM_SUCCESS:
                    for node in selected:
                        self._assign(node, resource, 0,
                          Assignment.FLAG_DEPLOY | Assignment.FLAG_CONNECT)
                    self._drbd_mgr.perform_changes()
                    self.save_conf_data(persist)
            else:
                if not resource.has_assignments():
                    fn_rc = DM_ENOENT
                elif not count >= 1:
                    fn_rc = DM_EINVAL
                else: # count > number of nodes
                    fn_rc = DM_ENODECNT
        except KeyError:
            fn_rc = DM_ENOENT
        except PersistenceException:
            fn_rc = DM_EPERSIST
        except PluginException:
            fn_rc = DM_EPLUGIN
        except Exception as exc:
            DrbdManageServer.catch_internal_error(exc)
            fn_rc = DM_DEBUG
        finally:
            self.end_modify_conf(persist)
        return fn_rc
    
    
    def auto_reduce(self, resource_name, count, rel_flag):
        """
        Reduce a deployment by a number of nodes
        
        @return: standard return code defined in drbdmanage.exceptions
        """
        fn_rc      = DM_EPERSIST
        persist = None
        fn_rc      = DM_DEBUG
        persist = None
        try:
            deployer = plugin_import(
              self.get_conf_value(self.KEY_DEPLOYER_NAME))
            if deployer is None:
                raise PluginException

            persist  = self.begin_modify_conf()
            if persist is None:
                raise PersistenceException
        
            resource = self._resources[resource_name]
            assigned_count = resource.assigned_count()
            if rel_flag:
                final_count = assigned_count - count
            else:
                final_count = count
                if not final_count < assigned_count:
                    final_count = 0
            if (resource.has_assignments()) and final_count >= 1:
                ctr = assigned_count
                # If there are assignments that are waiting for deployment,
                # but do not have the resource deployed yet, undeploy those
                # first
                if ctr > final_count:
                    for assg in resource.iterate_assignments():
                        if ((assg.get_tstate() & Assignment.FLAG_DEPLOY
                          != 0)
                          and (assg.get_cstate() & Assignment.FLAG_DEPLOY
                          == 0)):
                            assg.undeploy()
                            ctr -= 1
                        if not ctr > final_count:
                            break
                # Undeploy from nodes that have the resource deployed
                if ctr > final_count:
                    deployed = dict()
                    for assg in resource.iterate_assignments():
                        if ((assg.get_tstate() & Assignment.FLAG_DEPLOY
                          != 0)
                          and (assg.get_cstate() & Assignment.FLAG_DEPLOY
                          != 0)):
                            node = assg.get_node()
                            deployed[node.get_name()] = node
                    """
                    Call the deployer plugin to select nodes for undeployment
                    of the resource
                    """
                    selected = []
                    deployer.undeploy_select(deployed, selected,
                      (ctr - final_count), True)
                    for node in selected:
                        assg = node.get_assignment(resource.get_name())
                        self._unassign(assg, False)
                self._drbd_mgr.perform_changes()
                self.save_conf_data(persist)
                fn_rc = DM_SUCCESS
            else:
                if not resource.has_assignments():
                    fn_rc = DM_ENOENT
                else:
                    fn_rc = DM_EINVAL
        except KeyError:
            fn_rc = DM_ENOENT
        except PersistenceException:
            fn_rc = DM_EPERSIST
        except PluginException:
            fn_rc = DM_EPLUGIN
        except Exception as exc:
            DrbdManageServer.catch_internal_error(exc)
            fn_rc = DM_DEBUG
        finally:
            self.end_modify_conf(persist)
        return fn_rc
    
    
    def auto_undeploy(self, resource_name, force):
        """
        Undeploys a resource from all nodes
        
        @return: standard return code defined in drbdmanage.exceptions
        """
        fn_rc      = DM_DEBUG
        persist = None
        try:
            persist = self.begin_modify_conf()
            if persist is None:
                raise PersistenceException
            resource = self._resources[resource_name]
            removable = []
            for assg in resource.iterate_assignments():
                if (not force) and assg.is_deployed():
                    assg.disconnect()
                    assg.undeploy()
                else:
                    removable.append(assg)
            for assg in removable:
                assg.remove()
            self._drbd_mgr.perform_changes()
            self.save_conf_data(persist)
            fn_rc = DM_SUCCESS
        except KeyError:
            fn_rc = DM_ENOENT
        except PersistenceException:
            fn_rc = DM_EPERSIST
        except Exception as exc:
            DrbdManageServer.catch_internal_error(exc)
            fn_rc = DM_DEBUG
        finally:
            self.end_modify_conf(persist)
        return fn_rc
    
    
    def modify_state(self, node_name, resource_name,
      cstate_clear_mask, cstate_set_mask, tstate_clear_mask, tstate_set_mask):
        """
        Modifies the tstate (target state) of an assignment
        
        @return: standard return code defined in drbdmanage.exceptions
        """
        fn_rc      = DM_EPERSIST
        persist = None
        try:
            persist = self.begin_modify_conf()
            if persist is not None:
                node = self._nodes.get(node_name)
                if node is None:
                    fn_rc = DM_ENOENT
                else:
                    assg = node.get_assignment(resource_name)
                    if assg is None:
                        fn_rc = DM_ENOENT
                    else:
                        # OVERWRITE overrides DISCARD
                        if (tstate_set_mask & Assignment.FLAG_OVERWRITE) != 0:
                            tstate_clear_mask |= Assignment.FLAG_DISCARD
                            tstate_set_mask = ((tstate_set_mask
                              | Assignment.FLAG_DISCARD)
                              ^ Assignment.FLAG_DISCARD)
                        elif (tstate_set_mask & Assignment.FLAG_DISCARD ) != 0:
                            tstate_clear_mask |= Assignment.FLAG_OVERWRITE
                        assg.clear_cstate_flags(cstate_clear_mask)
                        assg.set_cstate_flags(cstate_set_mask)
                        assg.clear_tstate_flags(tstate_clear_mask)
                        assg.set_tstate_flags(tstate_set_mask)
                        # Upon setting the OVERWRITE flag on this assignment,
                        # clear it on all other assignments
                        if (tstate_set_mask & Assignment.FLAG_OVERWRITE) != 0:
                            resource = assg.get_resource()
                            for peer_assg in resource.iterate_assignments():
                                if peer_assg != assg:
                                    peer_assg.clear_tstate_flags(
                                      Assignment.FLAG_OVERWRITE)
                        self._drbd_mgr.perform_changes()
                        self.save_conf_data(persist)
                        fn_rc = DM_SUCCESS
        except PersistenceException:
            pass
        except Exception as exc:
            DrbdManageServer.catch_internal_error(exc)
            fn_rc = DM_DEBUG
        finally:
            self.end_modify_conf(persist)
        return fn_rc
    
    
    def connect(self, node_name, resource_name, reconnect):
        """
        Sets the CONNECT or RECONNECT flag on a resource's target state
        
        @return: standard return code defined in drbdmanage.exceptions
        """
        fn_rc = DM_EPERSIST
        node     = None
        resource = None
        persist  = None
        try:
            persist = self.begin_modify_conf()
            if persist is not None:
                node     = self._nodes.get(node_name)
                resource = self._resources.get(resource_name)
                if node is None or resource is None:
                    fn_rc = DM_ENOENT
                else:
                    assignment = node.get_assignment(resource.get_name())
                    if assignment is None:
                        fn_rc = DM_ENOENT
                    else:
                        if reconnect:
                            assignment.reconnect()
                        else:
                            assignment.connect()
                        self._drbd_mgr.perform_changes()
                        self.save_conf_data(persist)
                        fn_rc = DM_SUCCESS
        except PersistenceException:
            pass
        except Exception as exc:
            DrbdManageServer.catch_internal_error(exc)
            fn_rc = DM_DEBUG
        finally:
            self.end_modify_conf(persist)
        return fn_rc
    
    
    def disconnect(self, node_name, resource_name):
        """
        Clears the CONNECT flag on a resource's target state
        
        @return: standard return code defined in drbdmanage.exceptions
        """
        fn_rc = DM_EPERSIST
        node     = None
        resource = None
        persist  = None
        try:
            persist = self.begin_modify_conf()
            if persist is not None:
                node     = self._nodes.get(node_name)
                resource = self._resources.get(resource_name)
                if node is None or resource is None:
                    fn_rc = DM_ENOENT
                else:
                    assignment = node.get_assignment(resource.get_name())
                    if assignment is None:
                        fn_rc = DM_ENOENT
                    else:
                        assignment.disconnect()
                        self._drbd_mgr.perform_changes()
                        self.save_conf_data(persist)
                        fn_rc = DM_SUCCESS
        except PersistenceException:
            pass
        except Exception as exc:
            DrbdManageServer.catch_internal_error(exc)
            fn_rc = DM_DEBUG
        finally:
            self.end_modify_conf(persist)
        return fn_rc
    
    
    def attach(self, node_name, resource_name, volume_id):
        """
        Sets the ATTACH flag on a volume's target state
        
        @return: standard return code defined in drbdmanage.exceptions
        """
        fn_rc = DM_EPERSIST
        node      = None
        resource  = None
        vol_state = None
        persist   = None
        try:
            persist = self.begin_modify_conf()
            if persist is not None:
                node     = self._nodes.get(node_name)
                resource = self._resources.get(resource_name)
                if node is None or resource is None:
                    fn_rc = DM_ENOENT
                else:
                    assignment = node.get_assignment(resource.get_name())
                    if assignment is None:
                        fn_rc = DM_ENOENT
                    else:
                        vol_state = assignment.get_volume_state(volume_id)
                        if vol_state is None:
                            fn_rc = DM_ENOENT
                        else:
                            vol_state.attach()
                            self._drbd_mgr.perform_changes()
                            self.save_conf_data(persist)
                            fn_rc = DM_SUCCESS
        except PersistenceException:
            pass
        except Exception as exc:
            DrbdManageServer.catch_internal_error(exc)
            fn_rc = DM_DEBUG
        finally:
            self.end_modify_conf(persist)
        return fn_rc
    
    
    def detach(self, node_name, resource_name, volume_id):
        """
        Clears the ATTACH flag on a volume's target state
        
        @return: standard return code defined in drbdmanage.exceptions
        """
        fn_rc = DM_EPERSIST
        node      = None
        resource  = None
        vol_state = None
        persist   = None
        try:
            persist = self.begin_modify_conf()
            if persist is not None:
                node     = self._nodes.get(node_name)
                resource = self._resources.get(resource_name)
                if node is None or resource is None:
                    fn_rc = DM_ENOENT
                else:
                    assignment = node.get_assignment(resource.get_name())
                    if assignment is None:
                        fn_rc = DM_ENOENT
                    else:
                        vol_state = assignment.get_volume_state(volume_id)
                        if vol_state is None:
                            fn_rc = DM_ENOENT
                        else:
                            vol_state.detach()
                            self._drbd_mgr.perform_changes()
                            self.save_conf_data(persist)
                            fn_rc = DM_SUCCESS
        except PersistenceException:
            pass
        except Exception as exc:
            DrbdManageServer.catch_internal_error(exc)
            fn_rc = DM_DEBUG
        finally:
            self.end_modify_conf(persist)
        return fn_rc
    
    
    def update_pool(self):
        """
        Updates information about the current node's storage pool
        
        @return: standard return code defined in drbdmanage.exceptions
        free space
        """
        fn_rc = DM_EPERSIST
        persist = None
        try:
            persist = self.begin_modify_conf()
            if persist is not None:
                logging.info("updating storage pool information")
                fn_rc = self.update_pool_data()
                self.cleanup()
                self.save_conf_data(persist)
        except PersistenceException:
            logging.error("cannot save updated storage pool information")
        except Exception as exc:
            DrbdManageServer.catch_internal_error(exc)
            fn_rc = DM_DEBUG
        finally:
            self.end_modify_conf(persist)
        return fn_rc
    
    
    def update_pool_data(self):
        """
        Updates information about the current node's storage pool
        free space
        
        @return: standard return code defined in drbdmanage.exceptions
        """
        fn_rc = DM_ESTORAGE
        try:
            inst_node = self.get_instance_node()
            if inst_node is not None:
                stor_rc = self._bd_mgr.update_pool(inst_node)
                if stor_rc == 0:
                    fn_rc = DM_SUCCESS
            else:
                fn_rc = DM_ENOENT
        except Exception as exc:
            DrbdManageServer.catch_internal_error(exc)
            fn_rc = DM_DEBUG
        return fn_rc
    
    
    def cleanup(self):
        """
        Removes entries of undeployed nodes, resources, volumes or their
        supporting data structures (volume state and assignment entries)
        
        @return: standard return code defined in drbdmanage.exceptions
        """
        try:
            removable = []
            # delete assignments that have been undeployed
            for node in self._nodes.itervalues():
                for assignment in node.iterate_assignments():
                    tstate = assignment.get_tstate()
                    cstate = assignment.get_cstate()
                    if (cstate & Assignment.FLAG_DEPLOY) == 0 \
                      and (tstate & Assignment.FLAG_DEPLOY) == 0:
                        removable.append(assignment)
            for assignment in removable:
                assignment.remove()
            # delete nodes that are marked for removal and that do not
            # have assignments anymore
            removable = []
            for node in self._nodes.itervalues():
                nodestate = node.get_state()
                if (nodestate & DrbdNode.FLAG_REMOVE) != 0:
                    if not node.has_assignments():
                        removable.append(node)
            for node in removable:
                del self._nodes[node.get_name()]
            # delete volume assignments that are marked for removal
            # and that have been undeployed
            for resource in self._resources.itervalues():
                for assg in resource.iterate_assignments():
                    removable = []
                    for vol_state in assg.iterate_volume_states():
                        vol_cstate = vol_state.get_cstate()
                        vol_tstate = vol_state.get_tstate()
                        if (vol_cstate & DrbdVolumeState.FLAG_DEPLOY == 0) \
                          and (vol_tstate & DrbdVolumeState.FLAG_DEPLOY == 0):
                            removable.append(vol_state)
                    for vol_state in removable:
                        assg.remove_volume_state(vol_state.get_id())
            # delete volumes that are marked for removal and that are not
            # deployed on any node
            for resource in self._resources.itervalues():
                volumes = dict()
                # collect volumes marked for removal
                for volume in resource.iterate_volumes():
                    if volume.get_state() & DrbdVolume.FLAG_REMOVE != 0:
                        volumes[volume.get_id()] = volume
                for assg in resource.iterate_assignments():
                    removable = []
                    for vol_state in assg.iterate_volume_states():
                        volume = volumes.get(vol_state.get_id())
                        if volume is not None:
                            if vol_state.get_cstate() \
                              & DrbdVolumeState.FLAG_DEPLOY != 0:
                                # delete the volume from the removal list
                                del volumes[vol_state.get_id()]
                            else:
                                removable.append(vol_state)
                        for vol_state in removable:
                            assg.remove_volume_state(vol_state.get_id())
                for vol_id in volumes.iterkeys():
                    resource.remove_volume(vol_id)
            # delete resources that are marked for removal and that do not
            # have assignments any more
            removable = []
            for resource in self._resources.itervalues():
                res_state = resource.get_state()
                if (res_state & DrbdResource.FLAG_REMOVE) != 0:
                    if not resource.has_assignments():
                        removable.append(resource)
            for resource in removable:
                del self._resources[resource.get_name()]
        except Exception as exc:
            DrbdManageServer.catch_internal_error(exc)
            return DM_DEBUG
        return DM_SUCCESS
    
    
    def node_list(self):
        """
        Generates a list of node views suitable for serialized transfer
        
        Used by the drbdmanage client to display the node list
        """
        try:
            node_list = []
            for node in self._nodes.itervalues():
                properties = DrbdNodeView.get_properties(node)
                node_list.append(properties)
            return node_list
        except Exception as exc:
            DrbdManageServer.catch_internal_error(exc)
        return None
    
    
    def resource_list(self):
        """
        Generates a list of resources views suitable for serialized transfer
        
        Used by the drbdmanage client to display the resources/volumes list
        """
        try:
            resource_list = []
            for resource in self._resources.itervalues():
                properties = DrbdResourceView.get_properties(resource)
                resource_list.append(properties)
            return resource_list
        except Exception as exc:
            DrbdManageServer.catch_internal_error(exc)
        return None
    
    
    def assignment_list(self):
        """
        Generates a list of assignment views suitable for serialized transfer
        
        Used by the drbdmanage client to display the assignments list
        """
        try:
            assignment_list = []
            for node in self._nodes.itervalues():
                for assignment in node.iterate_assignments():
                    properties = AssignmentView.get_properties(assignment)
                    assignment_list.append(properties)
            return assignment_list
        except Exception as exc:
            DrbdManageServer.catch_internal_error(exc)
        return None
    
    
    def save_conf(self):
        """
        Saves the current configuration to the drbdmanage control volume
        
        @return: standard return code defined in drbdmanage.exceptions
        """
        fn_rc = DM_EPERSIST
        persist  = None
        try:
            persist = persistence_impl()
            if persist.open(True):
                self.save_conf_data(persist)
                fn_rc = DM_SUCCESS
        except PersistenceException:
            pass
        except Exception as exc:
            DrbdManageServer.catch_internal_error(exc)
            return DM_DEBUG
        finally:
            self.end_modify_conf(persist)
        return fn_rc
    
    
    def load_conf(self):
        """
        Loads the current configuration from the drbdmanage control volume
        
        @return: standard return code defined in drbdmanage.exceptions
        """
        fn_rc = DM_EPERSIST
        persist  = None
        try:
            persist = persistence_impl()
            if persist.open(False):
                self.load_conf_data(persist)
                persist.close()
                fn_rc = DM_SUCCESS
        except PersistenceException:
            pass
        except Exception as exc:
            DrbdManageServer.catch_internal_error(exc)
            return DM_DEBUG
        finally:
            self.end_modify_conf(persist)
        return fn_rc
    
    
    def load_conf_data(self, persist):
        """
        Loads the current configuration from the supplied persistence object
        
        Used by the drbdmanage server to load the configuration after the
        persistence layer had already opened it before
        
        @return: standard return code defined in drbdmanage.exceptions
        """
        persist.load(self._nodes, self._resources)
        self._conf_hash = persist.get_stored_hash()
    
    
    def save_conf_data(self, persist):
        """
        Saves the current configuration to the supplied persistence object
        
        Used by the drbdmanage server to save the configuration after the
        persistence layer had already opened and locked it before
        
        @return: standard return code defined in drbdmanage.exceptions
        """
        hash_obj = None
        persist.save(self._nodes, self._resources)
        hash_obj = persist.get_hash_obj()
        if hash_obj is not None:
            self._conf_hash = hash_obj.get_hex_hash()
    
    
    def open_conf(self):
        """
        Opens the configuration on persistent storage for reading
        This function is only there because drbdcore cannot import anything
        from persistence, so the code for creating a PersistenceImpl object
        has to be somwhere else.
        Returns a PersistenceImpl object on success, or None if the operation
        fails due to errors in the persistence layer
        
        @return: persistence layer object
        """
        ret_persist = None
        persist     = None
        try:
            persist = persistence_impl()
            if persist.open(False):
                ret_persist = persist
        except Exception as exc:
            # DEBUG
            exc_type, exc_obj, exc_tb = sys.exc_info()
            logging.error("cannot open control volume, unhandled exception: %s"
              % str(exc))
            logging.debug("Stack trace:\n%s" % str(exc_tb))
            persist.close()
        return ret_persist
    
    
    def begin_modify_conf(self):
        """
        Opens the configuration on persistent storage for writing,
        implicitly locking out all other nodes, and reloads the configuration
        if it has changed.
        Returns a PersistenceImpl object on success, or None if the operation
        fails due to errors in the persistence layer
        
        @return: persistence layer object
        """
        ret_persist = None
        persist     = None
        try:
            persist = persistence_impl()
            if persist.open(True):
                if not self.hashes_match(persist.get_stored_hash()):
                    self.load_conf_data(persist)
                ret_persist = persist
        except Exception as exc:
            exc_type, exc_obj, exc_tb = sys.exc_info()
            logging.error("cannot open the control volume for modification, "
                "unhandled exception: %s" % str(exc))
            logging.debug("Stack trace:\n%s" % str(exc_tb))
            persist.close()
        return ret_persist
    
    
    def end_modify_conf(self, persist):
        """
        Closes the configuration on persistent storage.
        
        @param   persist: persistence layer object to close
        """
        try:
            if persist is not None:
                persist.close()
        except Exception:
            pass
    
    
    # TODO: more precise error handling
    def export_conf(self, res_name):
        """
        For a named resource, exports a configuration file for drbdadm
        
        Exports a configuration file for drbdadm generated from the current
        configuration of a resource managed by the drbdmanage server on the
        current host.
        If the resource name is "*", configuration files for all resources
        currently deployed on the current host are generated.
        
        @return: standard return code defined in drbdmanage.exceptions
        """
        fn_rc = DM_SUCCESS
        node = self.get_instance_node()
        if node is not None:
            if res_name is None:
                res_name = ""
            if len(res_name) > 0 and res_name != "*":
                assg = node.get_assignment(res_name)
                if assg is not None:
                    if self.export_assignment_conf(assg) != 0:
                        fn_rc = DM_DEBUG
                else:
                    fn_rc = DM_ENOENT
            else:
                for assg in node.iterate_assignments():
                    if self.export_assignment_conf(assg) != 0:
                        fn_rc = DM_DEBUG
        return fn_rc
    
    
    # TODO: move over existing file instead of directly overwriting an
    #       existing file
    def export_assignment_conf(self, assignment):
        """
        From an assignment object, exports a configuration file for drbdadm
        
        Exports a configuration file for drbdadm generated from the current
        configuration of an assignment object managed by the drbdmanage server
        
        The drbdmanage server uses this function to generate temporary
        configuration files for drbdadm callbacks by the DRBD kernel module
        as well.
        
        @return: 0 on success, 1 on error
        """
        fn_rc = 0
        resource = assignment.get_resource()
        file_path = self._conf[self.KEY_DRBD_CONFPATH]
        if not file_path.endswith("/"):
            file_path += "/"
        file_path += "drbdmanage_" + resource.get_name() + ".res"
        assg_conf = None
        try:
            assg_conf = open(file_path, "w")
            writer    = DrbdAdmConf()
            writer.write(assg_conf, assignment, False)
        except IOError as ioerr:
            logging.error("cannot write to configuration file '%s', error "
              "returned by the OS is: %s"
              % (file_path, ioerr.strerror))
            fn_rc = 1
        finally:
            if assg_conf is not None:
                assg_conf.close()
        return fn_rc
    
    
    def remove_assignment_conf(self, resource_name):
        """
        Removes (unlinks) a drbdadm configuration file
        
        The drbdmanage server uses this function to remove configuration files
        of resources that become undeployed on the current host.
        
        @return: 0 on success, 1 on error
        """
        fn_rc = 0
        file_path = self._conf[self.KEY_DRBD_CONFPATH]
        if not file_path.endswith("/"):
            file_path += "/"
        file_path += "drbdmanage_" + resource_name + ".res"
        try:
            os.unlink(file_path)
        except OSError as oserr:
            logging.error("cannot remove configuration file '%s', "
              "error returned by the OS is: %s" % (file_path, oserr.strerror))
            fn_rc = 1
        return fn_rc
    
    
    def get_conf_hash(self):
        """
        Retrieves the hash code of the currently loaded configuration
        
        @return: hash code of the currently loaded configuration
        @rtype:  str
        """
        return self._conf_hash
    
    
    def hashes_match(self, cmp_hash):
        """
        Checks whether the currently known hash matches the supplied hash
        
        Configuration changes on the drbdmanage control volume are detected
        by checking whether the hash has changed. This is done by comparing
        the hash of the currently known configuration to the hash stored on
        the control volume whenever the data on the control volume may have
        changed.
        
        @return: True if the hashes match, False otherwise
        @rtype:  bool
        """
        if self._conf_hash is not None and cmp_hash is not None:
            if self._conf_hash == cmp_hash:
                return True
        return False
    
    
    def reconfigure(self):
        """
        Reconfigures the server
        
        @return: standard return code defined in drbdmanage.exceptions
        """
        fn_rc      = DM_EPERSIST
        try:
            self.load_server_conf()
            fn_rc = self.load_conf()
            self._drbd_mgr.reconfigure()
            self._bd_mgr = BlockDeviceManager(self._conf[self.KEY_STOR_NAME])
        except PersistenceException:
            pass
        except Exception as exc:
            DrbdManageServer.catch_internal_error(exc)
            fn_rc = DM_DEBUG
        return fn_rc
    
    
    def debug_console(self, command):
        """
        Set debugging options
        """
        fn_rc = 1
        try:
            if command.startswith("set "):
                # remove "set "
                command = command[4:]
                pos = command.find("=")
                if pos != -1:
                    key = command[:pos]
                    val = command[pos + 1:]
                    if key == "dbg_events":
                        self.dbg_events = self._debug_parse_flag(val)
                        fn_rc = 0
                    elif key.lower() == "loglevel":
                        loglevel = self._debug_parse_loglevel(val)
                        self._root_logger.setLevel(loglevel)
                        fn_rc = 0
        except SyntaxException:
            pass
        return fn_rc
    
    
    def _debug_parse_flag(self, val):
        """
        Convert a string argument to boolean values
        """
        if val == "1":
            flag = True
        elif val == "0":
            flag = False
        else:
            raise SyntaxException
        return flag
    
    
    def _debug_parse_loglevel(self, val):
        """
        Convert a string argument to a standard log level
        """
        for name in self.DM_LOGLEVELS.iterkeys():
            if val.upper() == name:
                return self.DM_LOGLEVELS[name]
        raise SyntaxException
    
    
    def shutdown(self):
        """
        Stops this drbdmanage server instance
        """
        logging.info("server shutdown (requested by function call)")
        # FIXME: Maybe the drbdsetup child process should be terminated first?
        exit(0)
    
    
    def get_free_minor_nr(self):
        """
        Retrieves a free (unused) minor number
        
        Minor numbers are allocated in the range from the configuration value
        KEY_MIN_MINOR_NR to the constant MinorNr.MINOR_NR_MAX. A minor number
        that is unique across the drbdmanage cluster is allocated for each
        volume.
        
        @return: next free minor number; or -1 on error
        """
        try:
            min_nr = int(self._conf[self.KEY_MIN_MINOR_NR])            
            minor_list = []
            for resource in self._resources.itervalues():
                for vol in resource.iterate_volumes():
                    minor_obj = vol.get_minor()
                    nr_item = minor_obj.get_value()
                    if nr_item >= min_nr and nr_item <= MinorNr.MINOR_NR_MAX:
                        minor_list.append(nr_item)
            minor_nr = get_free_number(min_nr, MinorNr.MINOR_NR_MAX,
              minor_list)
            if minor_nr == -1:
                raise ValueError
        except ValueError:
            minor_nr = MinorNr.MINOR_NR_ERROR
        return minor_nr
    
    
    def get_free_port_nr(self):
        """
        Retrieves a free (unused) network port number
        
        Port numbers are allocated in the range of the configuration values
        KEY_MIN_PORT_NR..KEY_MAX_PORT_NR. A port number that is unique
        across the drbdmanage cluster is allocated for each resource.
        
        @return: next free network port number; or -1 on error
        """
        try:
            min_nr    = int(self._conf[self.KEY_MIN_PORT_NR])
            max_nr    = int(self._conf[self.KEY_MAX_PORT_NR])
            
            port_list = []
            for resource in self._resources.itervalues():
                nr_item = resource.get_port()
                if nr_item >= min_nr and nr_item <= max_nr:
                    port_list.append(nr_item)
            port = get_free_number(min_nr, max_nr, port_list)
            if port == -1:
                raise ValueError
        except ValueError:
            port = DrbdResource.PORT_NR_ERROR
        return port
    
    
    def get_free_node_id(self, resource):
        """
        Retrieves a free (unused) node id number
        
        Node IDs range from 0 to the configuration value of KEY_MAX_NODE_ID
        and are allocated per resource (the node IDs of the same nodes can
        differ from one assigned resource to another)
        
        @return: next free node id number; or -1 on error
        """
        try:
            max_node_id = int(self._conf[self.KEY_MAX_NODE_ID])
            
            id_list = []
            for assg in resource.iterate_assignments():
                id_item = assg.get_node_id()
                if id_item >= 0 and id_item <= int(max_node_id):
                    id_list.append(id_item)
            node_id = get_free_number(0, int(max_node_id),
              id_list)
            if node_id == -1:
                raise ValueError
        except ValueError:
            node_id = Assignment.NODE_ID_ERROR
        return node_id
    
    
    def get_free_volume_id(self, resource):
        """
        Retrieves a free (unused) volume id number
        
        Volume IDs range from 0 to MAX_RES_VOLS and are allocated per resource
        
        @return: next free volume id number; or -1 on error
        """
        id_list = []
        for vol in resource.iterate_volumes():
            id_item = vol.get_id()
            if id_item >= 0 and id_item <= DrbdResource.MAX_RES_VOLS:
                id_list.append(id_item)
        vol_id = get_free_number(0, DrbdResource.MAX_RES_VOLS, id_list)
        return vol_id
    
    
    @staticmethod
    def catch_internal_error(exc):
        try:
            exc_type, exc_obj, exc_tb = sys.exc_info()
            logging.critical("Internal error: unhandled exception: %s"
              % (str(exc)))
            logging.debug("Stack trace:\n%s", str(exc_tb))
        except Exception:
            pass

"""
Tracing - may be used for debugging
"""
def traceit(frame, event, arg):
    if event == "line":
        lineno = frame.f_lineno
        print frame.f_code.co_filename, ":", "line", lineno
    return traceit

"""
Uncomment the statement below to enable tracing
"""
#sys.settrace(traceit)
