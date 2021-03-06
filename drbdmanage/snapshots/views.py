#!/usr/bin/env python2
"""
    drbdmanage - management of distributed DRBD9 resources
    Copyright (C) 2013 - 2017  LINBIT HA-Solutions GmbH
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

import drbdmanage.drbd.views as drbdviews
import drbdmanage.utils as utils
import drbdmanage.consts as consts
import drbdmanage.exceptions as exc


class DrbdSnapshotView(drbdviews.GenericView):
    _name     = None
    _resource = None

    _machine_readable = False


    def __init__(self, properties, machine_readable):
        try:
            super(DrbdSnapshotView, self).__init__(properties)
            self._name     = properties[consts.SNAPS_NAME]
            self._resource = properties[consts.RES_NAME]
        except KeyError:
            raise exc.IncompatibleDataException
        self._machine_readable = machine_readable


    @classmethod
    def get_name_maxlen(cls):
        return consts.SNAPS_NAME_MAXLEN


class DrbdSnapshotAssignmentView(drbdviews.GenericView):
    _node         = None
    _resource     = None
    _snapshot     = None

    _machine_readable = False

    _error_code   = 0

    # Machine readable texts for current state flags
    MR_CSTATE_TEXTS = [
        [consts.CSTATE_PREFIX + consts.FLAG_DEPLOY,
         consts.FLAG_DEPLOY,     None,       None]
    ]

    # Machine readable texts for target state flags
    MR_TSTATE_TEXTS = [
        [consts.TSTATE_PREFIX + consts.FLAG_DEPLOY,
         consts.FLAG_DEPLOY,     None,       None]
    ]

    # Human readable texts for current state flags
    HR_CSTATE_TEXTS = [
        [consts.CSTATE_PREFIX + consts.FLAG_DEPLOY,
         "d",     "-",    "?"]
    ]

    # Human readable texts for target state flags (without action flags)
    HR_TSTATE_TEXTS = [
        [consts.TSTATE_PREFIX + consts.FLAG_DEPLOY,
         "d",     "-",    "?"]
    ]


    def __init__(self, properties, machine_readable):
        try:
            super(DrbdSnapshotAssignmentView, self).__init__(properties)
            self._node     = properties[consts.NODE_NAME]
            self._resource = properties[consts.RES_NAME]
            self._snapshot = properties[consts.SNAPS_NAME]
            try:
                self._error_code = int(properties[consts.ERROR_CODE])
            except ValueError:
                self._error_code = -1
            except KeyError:
                self._error_code = 0
        except KeyError:
            raise exc.IncompatibleDataException
        self._machine_readable = machine_readable


    def state_info(self):
        c_deploy = utils.string_to_bool(
            self.get_property(consts.CSTATE_PREFIX + consts.FLAG_DEPLOY)
        )
        t_deploy = utils.string_to_bool(
            self.get_property(consts.TSTATE_PREFIX + consts.FLAG_DEPLOY)
        )

        fail_count = 0
        try:
            fail_count_str = self.get_property(consts.FAIL_COUNT)
            if fail_count_str is not None:
                fail_count = int(fail_count_str)
        except (ValueError, TypeError):
            pass

        if (not c_deploy) and (not t_deploy):
            self.add_pending_text("cleanup")
        elif c_deploy and (not t_deploy):
            self.add_pending_text("delete")
            self.raise_level(drbdviews.GenericView.STATE_ALERT)
        elif (not c_deploy) and t_deploy and self._error_code != 0:
            # Pending create actions are hidden if the creation of the snapshot
            # failed, because it will not be retried anyway
            self.add_pending_text("create")
            self.raise_level(drbdviews.GenericView.STATE_WARN)

        # Snapshot creation either succeeds immediately, or it fails and
        # is never retried
        if self._error_code != 0:
            self.add_state_text("FAILED")
            self.raise_level(drbdviews.GenericView.STATE_ALERT)
        elif fail_count > 0:
            self.add_state_text("FAILED(" + str(fail_count) + ")")
            self.raise_level(drbdviews.GenericView.STATE_ALERT)

        state_label = self.format_state_info()

        return self.get_level(), state_label


    def get_cstate(self):
        if self._machine_readable:
            text = self.state_text(self.MR_CSTATE_TEXTS, "|")
        else:
            text = self.state_text(self.HR_CSTATE_TEXTS, "")
        return text


    def get_tstate(self):
        if self._machine_readable:
            text = self.state_text(self.MR_TSTATE_TEXTS, "|")
        else:
            text = self.state_text(self.HR_TSTATE_TEXTS, "")
        return text


class DrbdSnapshotVolumeStateView(drbdviews.GenericView):
    _id      = None

    _machine_readable = False

    # Machine readable texts for current state flags
    MR_CSTATE_TEXTS = [
        [consts.CSTATE_PREFIX + consts.FLAG_DEPLOY,
         consts.FLAG_DEPLOY,     None,   None]
    ]

    # Human readable texts for current state flags
    HR_CSTATE_TEXTS = [
        [consts.CSTATE_PREFIX + consts.FLAG_DEPLOY,
         "d",     "-",    "?"]
    ]

    # Machine readable texts for target state flags
    MR_TSTATE_TEXTS = [
        [consts.TSTATE_PREFIX + consts.FLAG_DEPLOY,
         consts.FLAG_DEPLOY,     None,   None]
    ]

    # Human readable texts for target state flags
    HR_TSTATE_TEXTS = [
        [consts.TSTATE_PREFIX + consts.FLAG_DEPLOY,
         "d",     "-",    "?"]
    ]


    def __init__(self, properties, machine_readable):
        try:
            super(DrbdSnapshotVolumeStateView, self).__init__(properties)
            self._id = properties[consts.VOL_ID]
        except KeyError:
            raise exc.IncompatibleDataException
        self._machine_readable = machine_readable


    def get_id(self):
        return self._id


    def state_info(self):
        c_deploy = utils.string_to_bool(
            self.get_property(consts.CSTATE_PREFIX + consts.FLAG_DEPLOY)
        )
        t_deploy = utils.string_to_bool(
            self.get_property(consts.TSTATE_PREFIX + consts.FLAG_DEPLOY)
        )

        if (not c_deploy) and (not t_deploy):
            self.add_pending_text("cleanup")
        elif c_deploy and (not t_deploy):
            self.add_pending_text("delete")
            self.raise_level(drbdviews.GenericView.STATE_ALERT)
        elif (not c_deploy) and t_deploy:
            self.add_pending_text("create")
            self.raise_level(drbdviews.GenericView.STATE_WARN)

        return self.get_level(), self.format_state_info()


    def get_cstate(self):
        if self._machine_readable:
            text = self.state_text(self.MR_CSTATE_TEXTS, "|")
        else:
            text = self.state_text(self.HR_CSTATE_TEXTS, "")
        return text


    def get_tstate(self):
        if self._machine_readable:
            text = self.state_text(self.MR_TSTATE_TEXTS, "|")
        else:
            text = self.state_text(self.HR_TSTATE_TEXTS, "")
        return text
