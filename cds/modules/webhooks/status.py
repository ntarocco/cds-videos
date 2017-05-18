# -*- coding: utf-8 -*-
#
# This file is part of CERN Document Server.
# Copyright (C) 2016 CERN.
#
# CERN Document Server is free software; you can redistribute it
# and/or modify it under the terms of the GNU General Public License as
# published by the Free Software Foundation; either version 2 of the
# License, or (at your option) any later version.
#
# CERN Document Server is distributed in the hope that it will be
# useful, but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU
# General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with CERN Document Server; if not, write to the
# Free Software Foundation, Inc., 59 Temple Place, Suite 330, Boston,
# MA 02111-1307, USA.
#
# In applying this license, CERN does not
# waive the privileges and immunities granted to it by virtue of its status
# as an Intergovernmental Organization or submit itself to any jurisdiction.

"""Task status manipulation."""

from __future__ import absolute_import

import json
import sqlalchemy

from celery import states

from invenio_webhooks.models import Event


def get_deposit_events(deposit_id):
    """Get a list of events associated with a deposit."""
    #  return Event.query.filter(
    #      Event.payload.op('->>')(
    #          'deposit_id').cast(String) == self['_deposit']['id']).all()
    deposit_id = str(deposit_id)
    return Event.query.filter(
        sqlalchemy.cast(
            Event.payload['deposit_id'],
            sqlalchemy.String) == sqlalchemy.type_coerce(
                deposit_id, sqlalchemy.JSON)
    ).all()


def iterate_events_results(events, fun):
    """Iterate over the results of each event."""
    for event in events:
        if event.receiver.has_result(event):
            raw_info = event.receiver._raw_info(event)
            iterate_result(raw_info=raw_info, fun=fun)
    return fun


def get_tasks_status_by_task(events, statuses=None):
    """Get tasks status grouped by task name."""
    statuses = statuses or {}
    status_extractor = CollectStatusesByTask(statuses=statuses)
    iterate_events_results(events=events, fun=status_extractor)
    return status_extractor.statuses


def iterate_result(raw_info, fun):
    """Iterate through raw information generated by celery receivers.

    :param raw_info: raw information from celery receiver.
    :param fun: A function that extract some information from celery result.
        E.g. lambda task_name, result: result.status
    :returns: Elaborated version by fun() of raw_info data.
    """
    if isinstance(raw_info, list):
        # group results
        return list(iterate_result(el, fun) for el in raw_info)
    elif isinstance(raw_info, tuple):
        # chain results
        return tuple(iterate_result(el, fun) for el in raw_info)
    else:
        # single result
        task_name, result = next(iter(raw_info.items()))
        return fun(task_name, result)


def _compute_status(statuses):
    """Compute minimum state."""
    if len(statuses) > 0 and all(status_to_check is None
                                 for status_to_check in statuses):
        return states.PENDING
    for status_to_check in [states.FAILURE, states.STARTED,
                            states.RETRY, states.PENDING]:
        if any(status == status_to_check for status in statuses):
            return status_to_check
    if len(statuses) > 0 and all(status_to_check == states.REVOKED
                                 for status_to_check in statuses):
        return states.REVOKED
    return states.SUCCESS


class ComputeGlobalStatus(object):
    """Compute a global status from celery receiver raw info."""

    def __init__(self):
        """Init status collection list."""
        self._statuses = []

    def __call__(self, task_name, result):
        """Accumulate status information."""
        self._statuses.append(result.status)

    @property
    def status(self):
        """Elaborate global status."""
        return _compute_status(self._statuses)


class ResultEncoder(json.JSONEncoder):
    """Celery task result encoder."""

    def default(self, obj):
        """Encode the result."""
        if isinstance(obj, Exception):
            return str(obj)
        return json.JSONEncoder.default(self, obj)


def collect_info(task_name, result):
    """Collect information from a celery result."""
    return {
        'id': result.id,
        'status': result.status,
        'info': result.info,
        'name': task_name
    }


class CollectStatusesByTask(object):
    """Collect status information and organize by task name."""

    def __init__(self, statuses):
        """Init status collection list."""
        self._statuses = {}
        self._original = statuses

    def __call__(self, task_name, result):
        """Update status collection."""
        old_status = self._statuses.get(task_name)
        # get new status from celery only if still exists on celery cache
        new_status = result.status \
            if result.result is not None else None
        self._statuses[task_name] = _compute_status([old_status, new_status])

    @property
    def statuses(self):
        """Get new status or original."""
        from copy import deepcopy
        # take the calculated
        statuses = deepcopy(self._statuses)
        # and add orignal value if there is no new value
        keys = set(self._original) - set(self._statuses)
        for key in keys:
            statuses[key] = self._original[key]
        return statuses


class CollectInfoTasks(object):
    """Collect information from the tasks."""

    def __init__(self):
        """Init."""
        self._task_names = []

    def __call__(self, task_name, result):
        """Accumulate task name information."""
        self._task_names.append((task_name, result))

    def __iter__(self):
        """Iterator."""
        for info in self._task_names:
            yield info


class GetInfoByID(object):
    """Find task name by task id."""

    def __init__(self, task_id):
        """Init."""
        self._task_id = task_id
        self.task_name = ''

    def __call__(self, task_name, result):
        """Search task name."""
        if result.id == self._task_id:
            self.task_name = task_name
            self.result = result


def replace_task_id(result, old_task_id, new_task_id):
    """Replace task id in a serialized version of results."""
    try:
        (head, tail) = result
        if head == old_task_id:
            return new_task_id, replace_task_id(tail, old_task_id, new_task_id)
        else:
            return [replace_task_id(head, old_task_id, new_task_id),
                    replace_task_id(tail, old_task_id, new_task_id)]
    except ValueError:
        if isinstance(result, list) or isinstance(result, tuple):
            return [replace_task_id(r, old_task_id, new_task_id)
                    for r in result]
        return result
    except TypeError:
        return result
