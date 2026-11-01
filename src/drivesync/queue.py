"""Small serialized queue; future watchers use the same enqueue entry point."""

from collections import OrderedDict


class SyncQueue:
    def __init__(self):
        self.pending = OrderedDict()
        self.current = None

    def enqueue(self, folder_id, reason):
        if folder_id == self.current:
            return False
        if folder_id in self.pending:
            self.pending[folder_id].add(reason)
            return False
        self.pending[folder_id] = {reason}
        return True

    def pop(self):
        if self.current is not None or not self.pending:
            return None
        folder_id, reasons = self.pending.popitem(last=False)
        self.current = folder_id
        return folder_id, reasons

    def finish(self):
        self.current = None

    def clear(self):
        pending = list(self.pending)
        self.pending.clear()
        return pending
