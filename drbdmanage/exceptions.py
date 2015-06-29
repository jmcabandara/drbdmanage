#!/usr/bin/env python2
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

"""
Global exceptions and error codes for drbdmanage

This module defines exceptions, numeric error codes and the corresponding
default error messages for drbdmanage and utility functions to work with
those objects.
"""


# return code for successful operations
DM_SUCCESS  = 0

# ========================================
# return codes for failed operations
# ========================================

# function not implemented
DM_ENOTIMPL = 0x7fffffff

# invalid name for an object
DM_ENAME    = 100

# no entry = object not found
DM_ENOENT   = 101

# entry already exists
DM_EEXIST   = 102

# invalid IP type (not 4=IPv4 or 6=IPv6)
DM_EIPTYPE  = 103

# invalid minor number
DM_EMINOR   = 104

# Volume size out of range
DM_EVOLSZ   = 105

# Invalid option value
DM_EINVAL   = 106

# Cannot write configuration to or load configuration from persistent storage
DM_EPERSIST = 107

# Invalid node id or no free node id for auto-assignment
DM_ENODEID  = 108

# Invalid volume id or no free volume id for auto-assignment
DM_EVOLID   = 109

# Invalid port number or no free port numbers for auto-assignment
DM_EPORT    = 110

# An operation of the storage subsystem layer failed
DM_ESTORAGE = 111

# Not enough free memory
DM_ENOSPC   = 112

# Not enough nodes for deployment
DM_ENODECNT = 113

# Plugin load failed
DM_EPLUGIN  = 114

# Generation of the shared secret failed
DM_ESECRETG = 115

# Control volume error
DM_ECTRLVOL = 116

# DEBUG value
DM_DEBUG    = 1023

_DM_EXC_TEXTS = {}
_DM_EXC_TEXTS[DM_SUCCESS]  = "Operation completed successfully"
_DM_EXC_TEXTS[DM_ENAME]    = "Invalid name"
_DM_EXC_TEXTS[DM_ENOENT]   = "Object not found"
_DM_EXC_TEXTS[DM_EEXIST]   = "Object already exists"
_DM_EXC_TEXTS[DM_EIPTYPE]  = "Invalid IP protocol type"
_DM_EXC_TEXTS[DM_EMINOR]   = "Minor number out of range or no " \
                             "free minor numbers"
_DM_EXC_TEXTS[DM_EVOLSZ]   = "Volume size out of range"
_DM_EXC_TEXTS[DM_EINVAL]   = "Invalid option"
_DM_EXC_TEXTS[DM_DEBUG]    = "Debug exception / internal error"
_DM_EXC_TEXTS[DM_ENOTIMPL] = "Function not implemented"
_DM_EXC_TEXTS[DM_EPERSIST] = "I/O error while accessing persistent " \
                             "configuration storage"
_DM_EXC_TEXTS[DM_ENODEID]  = "Invalid node id or no free node id number"
_DM_EXC_TEXTS[DM_EVOLID]   = "Invalid volume id or no free volume id number"
_DM_EXC_TEXTS[DM_EPORT]    = "Invalid port number or no free port numbers"
_DM_EXC_TEXTS[DM_ESTORAGE] = "The storage subsystem failed to perform the " \
                             "requested operation"
_DM_EXC_TEXTS[DM_ENOSPC]   = "Not enough free space"
_DM_EXC_TEXTS[DM_ENODECNT] = "Deployment node count exceeds the number of " \
                             "nodes in the cluster"
_DM_EXC_TEXTS[DM_EPLUGIN]  = "Plugin cannot be loaded"
_DM_EXC_TEXTS[DM_ESECRETG] = "Generation of the shared secret failed"
_DM_EXC_TEXTS[DM_ECTRLVOL] = "Reconfiguring the control volume failed"


def dm_exc_text(exc_id):
    """
    Retrieve the default error message for a standard return code
    """
    try:
        text = _DM_EXC_TEXTS[exc_id]
    except KeyError:
        text = "<<No error message for id %d>>" % (str(exc_id))
    return text


class InvalidNameException(Exception):

    """
    Raised on an attempt to use a string that does not match the naming
    criteria as a name for an object
    """

    def __init__(self):
        super(InvalidNameException, self).__init__()


class InvalidAddrFamException(Exception):

    """
    Raised if an unknown address family is specified
    """

    def __init__(self):
        super(InvalidAddrFamException, self).__init__()


class VolSizeRangeException(Exception):

    """
    Raised if the size specification for a volume is out of range
    """

    def __init__(self):
        super(VolSizeRangeException, self).__init__()


class InvalidMinorNrException(Exception):

    """
    Raised if a device minor number is out of range or unparseable
    """

    def __init__(self):
        super(InvalidMinorNrException, self).__init__()


class InvalidMajorNrException(Exception):

    """
    Raised if a device major number is out of range or unparseable
    """

    def __init__(self):
        super(InvalidMajorNrException, self).__init__()


class IncompatibleDataException(Exception):

    """
    Raised if received data is not in a format expected and/or recognizable
    by the receiver.

    This exception is used by the drbdmanage client to signal that a list view
    generated by the drbdmanage server cannot be deserialized by the client.
    That should only happen with the combination of incompatible versions of
    drbdmanage client and server, otherwise it is a bug.
    """

    def __init__(self):
        super(IncompatibleDataException, self).__init__()


class SyntaxException(Exception):

    """
    Raised on syntax errors in input data
    """

    def __init__(self):
        super(SyntaxException, self).__init__()


class PersistenceException(Exception):

    """
    Raised if access to persistent storage fails
    """

    def __init__(self):
        super(PersistenceException, self).__init__()


class PluginException(Exception):

    """
    Raised if a plugin cannot be loaded
    """

    def __init__(self):
        super(PluginException, self).__init__()


class AbortException(Exception):

    """
    Raised to abort execution of a chain of operations
    """

    def __init__(self):
        super(AbortException, self).__init__()


class DebugException(Exception):

    """
    Raised to indicate an implementation error
    """

    def __init__(self):
        super(DebugException, self).__init__()
