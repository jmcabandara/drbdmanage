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


import logging
import drbdmanage.utils

from drbdmanage.storage.storagecommon import GenericStorage
from drbdmanage.exceptions import (
    InvalidMajorNrException, InvalidMinorNrException
)
from drbdmanage.exceptions import DM_ENOENT, DM_ESTORAGE, DM_DEBUG


class BlockDevice(GenericStorage):
    """
    Represents a block device
    """
    NAME_MAXLEN = 60 ## at least as long as res name

    _path     = None
    _name     = None


    def __init__(self, name, size_kiB, path):
        super(BlockDevice, self).__init__(size_kiB)
        self._path     = path
        self._name     = self.name_check(name)


    def name_check(self, name):
        return drbdmanage.drbd.drbdcore.GenericDrbdObject.name_check(
            name, BlockDevice.NAME_MAXLEN
        )


    def get_name(self):
        return self._name


    def get_path(self):
        return self._path


class BlockDeviceManager(object):
    _plugin = None


    def __init__(self, plugin_name):
        # self._plugin = self._plugin_import(plugin_name)
        self._plugin = drbdmanage.utils.plugin_import(plugin_name)
        if self._plugin is None:
            logging.error(
                "cannot import the storage management plugin (%s)"
                % (plugin_name)
            )


    def get_blockdevice(self, bd_name):
        blockdev = self._plugin.get_blockdevice(bd_name)
        return blockdev


    def create_blockdevice(self, name, vol_id, size):
        blockdev = self._plugin.create_blockdevice(name, vol_id, size)
        if blockdev is not None:
            status = "successful"
        else:
            status = "failed"
        logging.debug(
            "BlockDeviceManager: create '%s': volume #%u, %u kiB, %s"
            % (name, vol_id, size, status)
        )
        return blockdev


    def remove_blockdevice(self, bd_name):
        fn_rc = DM_ESTORAGE
        blockdev = self._plugin.get_blockdevice(bd_name)
        if blockdev is not None:
            fn_rc = self._plugin.remove_blockdevice(blockdev)
            logging.debug(
                "BlockDeviceManager: remove '%s': blockdev=%s, rc=%d"
                % (bd_name, blockdev, fn_rc)
            )
        else:
            logging.debug(
                "BlockDeviceManager: remove '%s': has no storage block device"
                % (bd_name)
            )
            fn_rc = DM_ENOENT
        return fn_rc


    def up_blockdevice(self, bd_name):
        blockdev = self._plugin.get_blockdevice(bd_name)
        if blockdev is not None:
            return self._plugin.up_blockdevice(blockdev)
        else:
            logging.debug(
                "BlockDeviceManager: up '%s': has no storage block device"
                % (bd_name)
            )
        return DM_ENOENT


    def down_blockdevice(self, bd_name):
        blockdev = self._plugin.get_blockdevice(bd_name)
        if blockdev is not None:
            return self._plugin.down_blockdevice(blockdev)
        else:
            logging.debug(
                "BlockDeviceManager: down '%s': has no storage block device"
                % (bd_name)
            )
        return DM_ENOENT


    def create_snapshot(self, name, vol_id, src_bd_name):
        successful = False
        blockdev   = None
        src_blockdev = self._plugin.get_blockdevice(src_bd_name)
        if src_blockdev is not None:
            blockdev = self._plugin.create_snapshot(name, vol_id, src_blockdev)
            if blockdev is not None:
                successful = True
        else:
            logging.debug(
                "BlockDeviceManager: create snapshot '%s' volume #%u: "
                "source block device %s not found"
                % (name, vol_id, src_bd_name)
            )
        status_text = "successful" if successful else "failed"
        logging.debug(
            "BlockDeviceManager: create snapshot '%s': volume #%u, %s"
            % (name, vol_id, status_text)
        )
        return blockdev


    def remove_snapshot(self, bd_name):
        fn_rc = DM_DEBUG
        rm_blockdev = self._plugin.get_blockdevice(bd_name)
        if rm_blockdev is not None:
            fn_rc = self._plugin.remove_blockdevice(rm_blockdev)
        else:
            logging.debug(
                "BlockDeviceManager: remove snapshot: volume '%s' not found"
                % (bd_name)
            )
        status_text = "successful" if fn_rc == 0 else "failed"
        logging.debug(
            "BlockDeviceManager: remove snapshot blockdev=%s, rc=%d, %s"
            % (bd_name, fn_rc, status_text)
        )
        return fn_rc


    def update_pool(self, drbdnode):
        return self._plugin.update_pool(drbdnode)


    def reconfigure(self):
        pass


    def _plugin_import(self, path):
        p_mod   = None
        p_class = None
        p_inst  = None
        try:
            if path is not None:
                idx = path.rfind(".")
                if idx != -1:
                    p_name = path[idx + 1:]
                    p_path = path[:idx]
                else:
                    p_name = path
                    p_path = ""
                p_mod   = __import__(p_path, globals(), locals(), [p_name], -1)
                p_class = getattr(p_mod, p_name)
                p_inst  = p_class()
        except Exception as exc:
            logging.error(
                "plugin import failed, exception returned by the "
                "import system is: %s"
                % (str(exc))
            )
        return p_inst


class MinorNr(object):
    """
    Contains the minor number of a unix device file
    """
    _minor = None
    MINOR_NR_MAX = 0xfffff

    MINOR_NR_AUTO     = -1
    MINOR_NR_ERROR    = -2

    def __init__(self, minor):
        self._minor = MinorNr.minor_check(minor)


    def get_value(self):
        return self._minor


    @classmethod
    def minor_check(cls, minor):
        if minor != cls.MINOR_NR_AUTO:
            if minor < 0 or minor > cls.MINOR_NR_MAX:
                raise InvalidMinorNrException
        return minor


class MajorNr(object):
    """
    Contains the major number of a unix device file
    """
    _major = None
    MAJOR_NR_MAX = 0xfff


    def __init__(self, major):
        self._major = MajorNr.major_check(major)


    def get_value(self):
        return self._major


    @classmethod
    def major_check(cls, major):
        if major < 0 or major > cls.MAJOR_NR_MAX:
            raise InvalidMajorNrException
        return major


class DevNr(object):
    """
    Contains the major/minor numbers of unix device files
    """
    _minor = None
    _major = None


    def __init__self(self, major, minor):
        self._minor = minor
        self._major = major


    def get_minor(self):
        self._minor.get_value()


    def get_major(self):
        self._major.get_value()


class StoragePlugin(object):

    """
    Interface for storage plugins

    Storage plugins are loaded dynamically at runtime. The block device manager
    expects storage plugins to implement the functions in this interface.
    Storage plugins should be subclasses of this interface, although
    technically, this is not strictly required, because Python does not care
    about the class hierarchy of objects as long as it finds all the
    functions it looks for.
    """

    def __init__(self):
        """
        Initializes the storage plugin
        """
        pass


    def get_blockdevice(self, bd_name):
        """
        Retrieves a registered BlockDevice object

        The BlockDevice object allocated and registered under the supplied
        resource name and volume id is returned.

        @return: the specified block device; None on error
        @rtype:  BlockDevice object
        """
        raise NotImplementedError


    def create_blockdevice(self, name, vol_id, size):
        """
        Allocates a block device as backing storage for a DRBD volume

        @param   name: resource name; subject to name constraints
        @type    name: str
        @param   vol_id: volume id
        @type    vol_id: int
        @param   size: size of the block device in kiB (binary kilobytes)
        @type    size: long
        @return: block device of the specified size
        @rtype:  BlockDevice object; None if the allocation fails
        """
        raise NotImplementedError


    def create_snapshot(self, name, vol_id, blockdevice):
        """
        Allocates a block device as a snapshot of an existing block device

        @param   name: snapshot name; subject to name constraints
        @type    name: str
        @param   vol_id: volume id
        @type    vol_id: int
        @param   blockdevice: the existing block device to snapshot
        @type    blockdevice: BlockDevice object
        @return: block device of the specified size
        @rtype:  BlockDevice object; None if the allocation fails
        """
        raise NotImplementedError


    def remove_snapshot(self, blockdevice):
        """
        Deallocates a snapshot block device

        @param   blockdevice: the block device to deallocate
        @type    blockdevice: BlockDevice object
        @return: standard return code (see drbdmanage.exceptions)
        """
        raise NotImplementedError


    def remove_blockdevice(self, blockdevice):
        """
        Deallocates a block device

        @param   blockdevice: the block device to deallocate
        @type    blockdevice: BlockDevice object
        @return: standard return code (see drbdmanage.exceptions)
        """
        raise NotImplementedError


    def up_blockdevice(self, blockdevice):
        """
        Activates a block device (e.g., connects an iSCSI resource)

        @param blockdevice: the block device to deactivate
        @type  blockdevice: BlockDevice object
        """
        raise NotImplementedError


    def down_blockdevice(self, blockdevice):
        """
        Deactivates a block device (e.g., disconnects an iSCSI resource)

        @param blockdevice: the block device to deactivate
        @type  blockdevice: BlockDevice object
        """
        raise NotImplementedError


    def update_pool(self, drbdnode):
        """
        Updates the DrbdNode object with the current storage status

        Determines the current total and free space that is available for
        allocation on the host this instance of the drbdmanage server is
        running on and updates the DrbdNode object with that information.

        @param   node: The node to update
        @type    node: DrbdNode object
        @return: standard return code (see drbdmanage.exceptions)
        """
        raise NotImplementedError


    def reconfigure(self):
        """
        Reconfigures the storage plugin
        """
        raise NotImplementedError
