"""Small queue shared by the execution lanes; watchers use the same enqueue entry point."""

from collections import OrderedDict


class SyncQueue:
    """Pending folders (with their reasons) plus the folders a lane is running right now.

    A folder is either pending or running, never both, and never twice: that is what keeps
    two lanes off the same folder.  ``pop`` takes the oldest pending entry the caller accepts.
    """

    def __init__(self):
        self.pending = OrderedDict()
        self.active = set()

    @property
    def current(self):
        """Compatibility: some running folder, or None (single-lane view)."""
        return next(iter(sorted(self.active)), None)

    def enqueue(self, folder_id, reason):
        if folder_id in self.active:
            return False
        if folder_id in self.pending:
            self.pending[folder_id].add(reason)
            return False
        self.pending[folder_id] = {reason}
        return True

    def pop(self, accept=None, concurrent=False):
        """Oldest accepted pending entry. Without ``concurrent`` only one folder may run."""
        if self.active and not concurrent:
            return None
        for folder_id, reasons in self.pending.items():
            if accept is None or accept(reasons):
                del self.pending[folder_id]
                self.active.add(folder_id)
                return folder_id, reasons
        return None

    def finish(self, folder_id=None):
        if folder_id is None:
            self.active.clear()
        else:
            self.active.discard(folder_id)

    def requeue(self, folder_id, reasons):
        """Put a popped entry back (its folder was busy); it keeps waiting."""
        self.active.discard(folder_id)
        self.pending[folder_id] = set(reasons)

    def clear(self):
        pending = list(self.pending)
        self.pending.clear()
        return pending
