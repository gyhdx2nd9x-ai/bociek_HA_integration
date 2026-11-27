"""Atomic snapshot replacement with explicit invalidation and bounded age."""

from time import monotonic, perf_counter


class ModelCache:
    def __init__(self, loader, max_age=30, clock=monotonic):
        self.loader = loader
        self.max_age = max_age
        self.model = None
        self.dirty = True
        self.clock = clock
        self.loaded_at = 0.0
        self.rebuilds = 0
        self.state_updates = 0
        self.last_rebuild_ms = 0.0
        self.revision = 0

    def invalidate(self, *_):
        self.dirty = True

    def get(self, force=False):
        if (
            force
            or self.dirty
            or self.model is None
            or (self.clock() - self.loaded_at > self.max_age)
        ):
            self.dirty = True
            started = perf_counter()
            model = self.loader()  # Failure keeps the old snapshot dirty, never silently valid.
            self.model = model
            self.dirty = False
            self.loaded_at = self.clock()
            self.rebuilds += 1
            self.revision += 1
            self.last_rebuild_ms = (perf_counter() - started) * 1000
        return self.model

    def update_entity(self, entity):
        """HA-loop-only update: ordinary state changes do not rebuild alias indexes."""
        if self.model is None or self.dirty:
            return
        previous = self.model.entities.get(entity.id)
        if previous is None or previous.name != entity.name or previous.aliases != entity.aliases:
            self.invalidate()
            return
        if previous != entity:
            self.model.entities[entity.id] = entity
            self.state_updates += 1
            self.revision += 1

    def metrics(self):
        return {
            "rebuilds": self.rebuilds,
            "state_updates": self.state_updates,
            "last_rebuild_ms": round(self.last_rebuild_ms, 3),
        }
