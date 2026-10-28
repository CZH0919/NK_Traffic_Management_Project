"""Small process-local cache for map display responses, never source records."""
import json
import threading
import time
from collections import OrderedDict
from django.core.serializers.json import DjangoJSONEncoder


class MapCache:
    def __init__(self, max_bytes=24 * 1024 * 1024, max_entries=64, ttl=90):
        self.max_bytes, self.max_entries, self.ttl = max_bytes, max_entries, ttl
        self._items = OrderedDict()
        self._bytes = 0
        self._lock = threading.Lock()

    def get(self, key):
        with self._lock:
            item = self._items.pop(key, None)
            if item is None:
                return None
            expiry, value = item
            if expiry < time.monotonic():
                self._bytes -= len(value)
                return None
            self._items[key] = item
        return json.loads(value)

    def set(self, key, value):
        raw = json.dumps(value, cls=DjangoJSONEncoder, separators=(',', ':')).encode()
        if len(raw) > self.max_bytes:
            return
        with self._lock:
            previous = self._items.pop(key, None)
            if previous:
                self._bytes -= len(previous[1])
            self._items[key] = (time.monotonic() + self.ttl, raw)
            self._bytes += len(raw)
            while self._bytes > self.max_bytes or len(self._items) > self.max_entries:
                _, (_, old) = self._items.popitem(last=False)
                self._bytes -= len(old)

    def clear(self):
        with self._lock:
            self._items.clear()
            self._bytes = 0


map_cache = MapCache()
