#!/usr/bin/python

from drbdmanage.storage.storagecore import MinorNr
from drbdmanage.drbd.drbdcore import *
from drbdmanage.persistence import *
from drbdmanage.exceptions import *
from drbdmanage.utils import *
import sys
import os
import errno
import time
import json

__author__="raltnoeder"
__date__ ="$Sep 24, 2013 3:33:50 PM$"


class PersistenceImpl(object):
    _file       = None
    _server     = None
    _writeable  = False
    _hash_obj   = None
    
    BLKSZ       = 0x1000 # 4096
    IDX_OFFSET  = 0x1800 # 6144
    HASH_OFFSET = 0x1900 # 6400
    DATA_OFFSET = 0x2000 # 8192
    ZEROFILLSZ  = 0x0400 # 1024
    CONF_FILE   = "/tmp/drbdmanaged.bin"
    
    # fail counter for attempts to open the config file (CONF_FILE)
    MAX_FAIL_COUNTER = 10
    
    
    def __init__(self):
        pass
    
    
    def open(self):
        rc = False
        fail_ctr = 0
        while fail_ctr < 10:
            try:
                self._file      = open(self.CONF_FILE, "r")
                self._writeable = False
                rc = True
                break
            except IOError as io_err:
                if io_err.errno == errno.ENOENT:
                    sys.stderr.write("Cannot open %s: not found\n"
                      % (self.CONF_FILE))
                fail_ctr += 1
                b = os.urandom(1)
                cs = ord(b) / 100
                time.sleep(0.5 + cs)
        if not fail_ctr < 10:
            sys.stderr.write("Cannot open %s (%d failed attempts)\n"
              % (self.CONF_FILE, self.MAX_FAIL_COUNT))
        return rc
    
    
    def open_modify(self):
        rc = False
        fail_ctr = 0
        while fail_ctr < 10:
            try:
                self._file      = open(self.CONF_FILE, "r+")
                self._writeable = True
                rc = True
                break
            except IOError as io_err:
                if io_err.errno == errno.ENOENT:
                    sys.stderr.write("Cannot open %s: not found\n"
                      % (self.CONF_FILE))
                fail_ctr += 1
                b = os.urandom(1)
                cs = ord(b) / 100
                time.sleep(0.5 + cs)
        if not fail_ctr < 10:
            sys.stderr.write("Cannot open %s (%d failed attempts)\n"
              % (self.CONF_FILE, self.MAX_FAIL_COUNT))
        return rc
    
    
    # TODO: clean implementation - this is a prototype
    def save(self, nodes, volumes):
        if self._writeable:
            try:
                p_nodes_con = dict()
                p_vol_con   = dict()
                p_assg_con  = dict()
                hash        = DataHash()
                
                # Prepare nodes container (and build assignments list)
                assignments = []
                for node in nodes.itervalues():
                    p_node = DrbdNodePersistence(node)
                    p_node.save(p_nodes_con)
                    for assg in node.iterate_assignments():
                        assignments.append(assg)
                
                # Prepare volumes container
                for volume in volumes.itervalues():
                    p_volume = DrbdVolumePersistence(volume)
                    p_volume.save(p_vol_con)
                
                # Prepare assignments container
                for assignment in assignments:
                    p_assignment = AssignmentPersistence(assignment)
                    p_assignment.save(p_assg_con)
                
                # Save data
                self._file.seek(self.DATA_OFFSET)
                
                nodes_off = self._file.tell()
                save_data = self._container_to_json(p_nodes_con)
                hash.update(save_data)
                self._file.write(save_data)
                nodes_len = self._file.tell() - nodes_off
                
                self._align_zero_fill()
                
                vol_off = self._file.tell()
                save_data = self._container_to_json(p_vol_con)
                self._file.write(save_data)
                hash.update(save_data)
                vol_len = self._file.tell() - vol_off
                
                self._align_zero_fill()
                
                assg_off = self._file.tell()
                save_data = self._container_to_json(p_assg_con)
                self._file.write(save_data)
                hash.update(save_data)
                assg_len = self._file.tell() - assg_off
                
                self._file.seek(self.IDX_OFFSET)
                self._file.write(
                  long_to_bin(nodes_off)
                  + long_to_bin(nodes_len)
                  + long_to_bin(vol_off)
                  + long_to_bin(vol_len)
                  + long_to_bin(assg_off)
                  + long_to_bin(assg_len))
                self._file.seek(self.HASH_OFFSET)
                self._file.write(hash.get_hash())
                self._hash_obj = hash
            except Exception as exc:
                sys.stderr.write("persistence save(): " + str(exc) + "\n")
                raise PersistenceException
        else:
            # file not open for writing
            raise IOError("Persistence save() without a "
              "writable file descriptor")
    
    
    # Get the hash of the configuration on persistent storage
    def get_stored_hash(self):
        stored_hash = None
        if self._file is not None:
            try:
                hash = DataHash()
                self._file.seek(self.HASH_OFFSET)
                stored_hash = self._file.read(hash.get_hash_len())
            except Exception:
                raise PersistenceException
        else:
            # file not open
            raise IOError("Persistence load() without an "
              "open file descriptor")
        return stored_hash
    
    
    # TODO: clean implementation - this is a prototype
    def load(self, nodes, volumes):
        errors = False
        if self._file is not None:
            try:
                hash = DataHash()
                self._file.seek(self.IDX_OFFSET)
                f_index = self._file.read(48)
                nodes_off = long_from_bin(f_index[0:8])
                nodes_len = long_from_bin(f_index[8:16])
                vol_off   = long_from_bin(f_index[16:24])
                vol_len   = long_from_bin(f_index[24:32])
                assg_off  = long_from_bin(f_index[32:40])
                assg_len  = long_from_bin(f_index[40:48])
                
                nodes_con = None
                vol_con   = None
                assg_con  = None
                
                self._file.seek(nodes_off)
                load_data = self._file.read(nodes_len)
                hash.update(load_data)
                try:
                    nodes_con = self._json_to_container(load_data)
                except Exception:
                    pass
                
                self._file.seek(vol_off)
                load_data = self._file.read(vol_len)
                hash.update(load_data)
                try:
                    vol_con   = self._json_to_container(load_data)
                except Exception:
                    pass
                
                self._file.seek(assg_off)
                load_data = self._file.read(assg_len)
                hash.update(load_data)
                try:
                    assg_con  = self._json_to_container(load_data)
                except Exception:
                    pass
                
                self._file.seek(self.HASH_OFFSET)
                computed_hash = hash.get_hash()
                stored_hash   = self._file.read(hash.get_hash_len())
                if computed_hash != stored_hash:
                    sys.stderr.write("Warning: configuration data does not "
                      "match its signature\n")
                # TODO: if the signature is wrong, load an earlier backup
                #       of the configuration
                
                nodes.clear()
                if nodes_con is not None:
                    for properties in nodes_con.itervalues():
                        node = DrbdNodePersistence.load(properties)
                        if node is not None:
                            nodes[node.get_name()] = node
                        else:
                            print "Nodes", properties # DEBUG
                            errors = True
                
                volumes.clear()
                if vol_con is not None:
                    for properties in vol_con.itervalues():
                        volume = DrbdVolumePersistence.load(properties)
                        if volume is not None:
                            volumes[volume.get_name()] = volume
                        else:
                            print "Volumes", properties # DEBUG
                            errors = True
                
                if assg_con is not None:
                    for properties in assg_con.itervalues():
                        assignment = AssignmentPersistence.load(properties,
                          nodes, volumes)
                        if assignment is None:
                            print "Assignments", properties # DEBUG
                            errors = True
                    self._hash_obj = hash
            except Exception as exc:
                exc_type, exc_obj, exc_tb = sys.exc_info()
                sys.stderr.write("DEBUG: Exception %s (%s), %s\n%s\n"
                  % (str(exc), exc_type, exc_obj, exc_tb))
                raise PersistenceException
        else:
            sys.stderr.write("DEBUG: File not open\n" % str(exc))
            # file not open
            raise IOError("Persistence load() without an "
              "open file descriptor")
        if errors:
            raise PersistenceException
    
    
    def close(self):
        try:
            if self._file is not None:
                self._writeable = False
                self._file.close()
                self._file      = None
        except Exception:
            pass
    
    
    def get_hash_obj(self):
        return self._hash_obj
    
    
    def _container_to_json(self, container):
        return (json.dumps(container, indent=4, sort_keys=True) + "\n")
    
    
    def _json_to_container(self, json_doc):
        return json.loads(json_doc)
    
    
    def _align_offset(self):
        if self._file is not None:
            offset = self._file.tell()
            if offset % self.BLKSZ != 0:
                offset = ((offset / self.BLKSZ) + 1) * self.BLKSZ
                self._file.seek(offset)
    
    
    def _align_zero_fill(self):
        if self._file is not None:
            offset = self._file.tell()
            if offset % self.BLKSZ != 0:
                fillbuf = ('\0' * self.ZEROFILLSZ)
                blk  = ((offset / self.BLKSZ) + 1) * self.BLKSZ
                diff = blk - offset;
                fillnr = diff / self.ZEROFILLSZ
                ctr = 0
                while ctr < fillnr:
                    self._file.write(fillbuf)
                    ctr += 1
                diff -= (self.ZEROFILLSZ * fillnr)
                self._file.write(fillbuf[:diff])
    
    
    def _next_json(self, stream):
        read = False
        json_blk = None
        cfgline = stream.readline()
        while len(cfgline) > 0:
            if cfgline == "{\n":
                read = True
            if read:
                if json_blk is None:
                    json_blk = ""
                json_blk += cfgline
            if cfgline == "}\n":
                break
            cfgline = stream.readline()
        return json_blk


class DrbdNodePersistence(GenericPersistence):
    SERIALIZABLE = [ "_name", "_ip", "_af", "_state",
      "_poolsize", "_poolfree" ]
    
    
    def __init__(self, node):
        super(DrbdNodePersistence, self).__init__(node)
    
    
    def save(self, container):
        node = self.get_object()
        properties  = self.load_dict(self.SERIALIZABLE)
        container[node.get_name()] = properties
    
        
    @classmethod
    def load(cls, properties):
        node = None
        try:
            node = DrbdNode(
              properties["_name"],
              properties["_ip"],
              int(properties["_af"])
              )
            node.set_state(long(properties["_state"]))
            node.set_poolsize(long(properties["_poolsize"]))
            node.set_poolfree(long(properties["_poolfree"]))
        except Exception:
            # DEBUG
            exc_type, exc_obj, exc_tb = sys.exc_info()
            print exc_type
            print exc_obj
            print exc_tb
        return node


class DrbdVolumePersistence(GenericPersistence):
    SERIALIZABLE = [ "_name", "_state", "_size_MiB" ]
    
    
    def __init__(self, volume):
        super(DrbdVolumePersistence, self).__init__(volume)
    
    
    def save(self, container):
        volume = self.get_object()
        properties  = self.load_dict(self.SERIALIZABLE)
        minor = volume.get_minor()
        properties["minor"] = minor.get_value()
        container[volume.get_name()] = properties
    
    
    @classmethod
    def load(cls, properties):
        volume = None
        try:
            minor_nr = properties["minor"]
            minor = MinorNr(minor_nr)
            volume = DrbdVolume(
              properties["_name"],
              long(properties["_size_MiB"]),
              minor
              )
            volume.set_state(long(properties["_state"]))
        except Exception:
            pass
        return volume


class AssignmentPersistence(GenericPersistence):
    SERIALIZABLE = [ "_blockdevice", "_bd_path", "_node_id",
      "_cstate", "_tstate", "_rc" ]
    
    
    def __init__(self, assignment):
        super(AssignmentPersistence, self).__init__(assignment)
        
        
    def save(self, container):
        properties = self.load_dict(self.SERIALIZABLE)
        
        # Serialize the names of nodes and volumes only
        assignment  = self.get_object()
        node        = assignment.get_node()
        volume      = assignment.get_volume()
        node_name   = node.get_name()
        vol_name    = volume.get_name()
        
        properties["node"]        = node_name
        properties["volume"]      = vol_name
        
        assg_name = node_name + ":" + vol_name
        
        container[assg_name] = properties
    
    
    @classmethod
    def load(cls, properties, nodes, volumes):
        assignment = None
        try:
            node = nodes[properties["node"]]
            volume = volumes[properties["volume"]]
            assignment = Assignment(
              node,
              volume,
              int(properties["_node_id"]),
              long(properties["_cstate"]),
              long(properties["_tstate"])
              )
            blockdevice = None
            bd_path     = None
            try:
                blockdevice = properties["_blockdevice"]
                bd_path     = properties["_bd_path"]
            except KeyError:
                pass
            if blockdevice is not None and bd_path is not None:
                assignment.set_blockdevice(blockdevice, bd_path)
            assignment.set_rc(properties["_rc"])
            node.add_assignment(assignment)
            volume.add_assignment(assignment)
        except Exception as exc:
            pass
        return assignment
