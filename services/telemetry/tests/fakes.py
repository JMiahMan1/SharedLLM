"""In-memory Redis double for telemetry service tests (no fakeredis dependency)."""


class FakeRedis:
    def __init__(self):
        self.strings: dict[str, str] = {}
        self.sets: dict[str, set] = {}
        self.zsets: dict[str, dict] = {}
        self.lists: dict[str, list] = {}

    async def get(self, key):
        return self.strings.get(key)

    async def set(self, key, value, ex=None, nx=False):
        if nx and key in self.strings:
            return None
        self.strings[key] = value
        return True

    async def delete(self, key):
        self.strings.pop(key, None)
        self.sets.pop(key, None)
        self.zsets.pop(key, None)
        self.lists.pop(key, None)
        return 1

    async def sadd(self, key, *members):
        self.sets.setdefault(key, set()).update(members)
        return len(members)

    async def srem(self, key, *members):
        bucket = self.sets.get(key, set())
        before = len(bucket)
        bucket.difference_update(members)
        return before - len(bucket)

    async def smembers(self, key):
        return self.sets.get(key, set())

    async def zadd(self, key, mapping):
        self.zsets.setdefault(key, {}).update(mapping)
        return len(mapping)

    async def zrem(self, key, member):
        return 1 if self.zsets.get(key, {}).pop(member, None) is not None else 0

    async def zrangebyscore(self, key, min_, max_, start=0, num=10):
        items = sorted(
            ((score, member) for member, score in self.zsets.get(key, {}).items() if score <= max_),
            key=lambda x: x[0],
        )
        return [m for _, m in items[start:start + num]]

    async def zrevrange(self, key, start, end):
        items = sorted(self.zsets.get(key, {}).items(), key=lambda x: x[1], reverse=True)
        ids = [m for m, _ in items]
        return ids[start:] if end == -1 else ids[start:end + 1]

    async def rpush(self, key, value):
        self.lists.setdefault(key, []).append(value)
        return len(self.lists[key])

    async def ltrim(self, key, start, end):
        items = self.lists.get(key, [])
        self.lists[key] = items[start:] if end == -1 else items[start:end + 1]

    async def lrange(self, key, start, end):
        items = self.lists.get(key, [])
        return items[start:] if end == -1 else items[start:end + 1]

    async def scan_iter(self, match="*", count=100):
        prefix = match.rstrip("*")
        for key in list(self.strings):
            if key.startswith(prefix):
                yield key

    async def aclose(self):
        return None
