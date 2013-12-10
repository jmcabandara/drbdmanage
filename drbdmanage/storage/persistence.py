#!/usr/bin/python

from drbdmanage.persistence import *
import drbdmanage.storage.storagecore

__author__="raltnoeder"
__date__ ="$Sep 30, 2013 12:19:56 PM$"


class BlockDevicePersistence(GenericPersistence):
    
    """
    Serializes/deserializes BlockDevice objects
    
    This class is for use by storage plugins.
    """
    
    SERIALIZABLE = [ "_name", "_path" ]
    def __init__(self, bd):
        super(BlockDevicePersistence, self).__init__(bd)
    
    def save(self, container):
        bd = self.get_object()
        properties = self.load_dict(self.SERIALIZABLE)
        properties["size_MiB"] = bd.get_size_MiB()
        container[bd.get_name()] = properties
    
    @classmethod
    def load(cls, properties):
        BlockDevice = drbdmanage.storage.storagecore.BlockDevice
        bd = None
        try:
            bd = BlockDevice(
              properties["_name"],
              properties["size_MiB"],
              properties["_path"]
              )
        except Exception as exc:
            pass
        return bd
