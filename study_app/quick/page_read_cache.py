"""Short-lived worker-only snapshots for revisiting unchanged read-only pages."""
from copy import deepcopy
from datetime import date
from pathlib import Path
from time import monotonic


class PageReadCache:
    def __init__(self, service):
        names = ("db_path", "model_path", "records_path")
        self.paths = tuple(Path(getattr(service, name)) for name in names) if all(hasattr(service, name) for name in names) else ()
        self.entries = {}

    def signature(self):
        files = (*self.paths, Path(str(self.paths[0]) + "-wal"), Path(str(self.paths[0]) + "-journal"))
        result = [date.today().isoformat()]
        for path in files:
            try:
                stat = path.stat()
                result.append((stat.st_mtime_ns, stat.st_ctime_ns, stat.st_size))
            except FileNotFoundError:
                result.append(None)
        return tuple(result)

    def read(self, key, loader, reuse=False):
        if not self.paths:
            return loader()
        before = self.signature()
        cached = self.entries.get(key)
        if reuse and cached and cached[0] == before and monotonic() - cached[1] < 30:
            return deepcopy(cached[2])
        value = loader()
        if before == self.signature():
            if len(self.entries) >= 2:
                self.entries.clear()
            self.entries[key] = (before, monotonic(), deepcopy(value))
        else:
            self.entries.pop(key, None)
        return value
