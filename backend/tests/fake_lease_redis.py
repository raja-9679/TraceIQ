"""A tiny in-memory Redis for the exclusive-environment lease tests (WP16c).

Only what app/services/environment_lock.py uses: strings with TTL, hashes,
SCAN, and the two compare-and-* Lua scripts (recognised by text). `now` is
advanced by hand so expiry is testable without sleeping.
"""
import fnmatch

from app.services import environment_lock


class FakeLeaseRedis:
    def __init__(self, fail=False):
        self.data, self.expiry, self.now, self.fail = {}, {}, 0.0, fail

    def _check(self):
        if self.fail:
            raise ConnectionError("redis down")

    def _alive(self, key):
        exp = self.expiry.get(key)
        if exp is not None and exp <= self.now:
            self.data.pop(key, None)
            self.expiry.pop(key, None)
        return key in self.data

    def advance(self, seconds):
        self.now += seconds

    def get(self, key):
        self._check()
        return self.data[key] if self._alive(key) and isinstance(self.data[key], str) else None

    def set(self, key, value, nx=False, ex=None):
        self._check()
        if nx and self._alive(key):
            return None
        self.data[key] = str(value)
        if ex:
            self.expiry[key] = self.now + ex
        else:
            self.expiry.pop(key, None)
        return True

    def hset(self, key, mapping):
        self._check()
        if not self._alive(key):
            self.data[key] = {}
        self.data[key].update({k: str(v) for k, v in mapping.items()})

    def hgetall(self, key):
        self._check()
        return dict(self.data[key]) if self._alive(key) else {}

    def expire(self, key, seconds):
        if not self._alive(key):
            return 0
        self.expiry[key] = self.now + int(seconds)
        return 1

    def ttl(self, key):
        if not self._alive(key):
            return -2
        exp = self.expiry.get(key)
        return -1 if exp is None else int(exp - self.now)

    def delete(self, *keys):
        self._check()
        n = 0
        for k in keys:
            if self._alive(k):
                n += 1
            self.data.pop(k, None)
            self.expiry.pop(k, None)
        return n

    def eval(self, script, numkeys, key, *args):
        self._check()
        if self.get(key) != str(args[0]):
            return 0
        if script == environment_lock._CAD:
            return self.delete(key)
        if script == environment_lock._CAE:
            return self.expire(key, args[1])
        raise AssertionError("unknown script")

    def scan_iter(self, match="*", count=None):
        self._check()
        return [k for k in list(self.data) if self._alive(k) and fnmatch.fnmatch(k, match)]
