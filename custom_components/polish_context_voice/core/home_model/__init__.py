"""HA metadata indexes with lightweight per-request visibility views."""

from copy import copy
from dataclasses import dataclass, field, replace
from time import monotonic

from ..nlu.light_sides import SideForm, side_name
from ..nlu.morphology import LightMorphology, Morphology, light_name_key

# These keys are already normalized by the configured morphology.
_LOCATION_FORMS = {
    "aneksie": "aneks",
    "aneksu": "aneks",
    "aneksie kuchennym": "aneks kuchenny",
    "aneksu kuchennego": "aneks kuchenny",
    "pokoj dziennym": "pokoj dzienny",
    "pokoj dziennego": "pokoj dzienny",
    "pokoj goscinnym": "pokoj goscinny",
    "pokoj goscinnego": "pokoj goscinny",
}


@dataclass(frozen=True)
class Area:
    id: str
    name: str
    aliases: tuple[str, ...] = ()
    floor_id: str | None = None
    labels: frozenset[str] = frozenset()


@dataclass(frozen=True)
class Floor:
    id: str
    name: str
    aliases: tuple[str, ...] = ()


@dataclass(frozen=True)
class Entity:
    id: str
    name: str
    area_id: str | None
    aliases: tuple[str, ...] = ()
    labels: frozenset[str] = frozenset()
    device_class: str | None = None
    exposed: bool = False
    available: bool = True
    state: str = "unknown"
    attributes: dict = field(default_factory=dict)
    device_id: str | None = None
    device_name: str | None = None
    platform: str | None = None
    config_entry_id: str | None = None
    unique_id: str | None = None

    @property
    def domain(self) -> str:
        return self.id.split(".", 1)[0]

    @property
    def members(self) -> tuple[str, ...]:
        value = self.attributes.get("entity_id", ())
        return tuple(value) if isinstance(value, (list, tuple)) else ()


class HomeModel:
    def __init__(
        self,
        areas=(),
        floors=(),
        entities=(),
        satellites=None,
        devices=None,
        morphology: Morphology | None = None,
    ):
        self.areas = {a.id: a for a in areas}
        self.floors = {f.id: f for f in floors}
        self.entities = {e.id: e for e in entities}
        self.satellites = dict(satellites or {})
        self.devices = dict(devices or {})
        self.morphology = morphology or LightMorphology()
        self.created_at = monotonic()
        self.location_index: dict[str, set[tuple[str, str]]] = {}
        self.entity_index: dict[str, set[str]] = {}
        self._light_name_index: dict[str, set[str]] = {}
        self.light_side_names: dict[str, frozenset[tuple[SideForm, str]]] = {}
        self.light_side_index: dict[tuple[str, str, str], set[str]] = {}
        for kind, items in (("area", self.areas), ("floor", self.floors)):
            for identifier, item in items.items():
                for alias in (identifier, item.name, *item.aliases):
                    key = self.location_key(alias)
                    self.location_index.setdefault(key, set()).add((kind, identifier))
        for entity in self.entities.values():
            is_light = entity.domain == "light"
            sides = set()
            for alias in (entity.name, *entity.aliases):
                key = self.key(alias)
                self.entity_index.setdefault(key, set()).add(entity.id)
                if is_light:
                    light_key = light_name_key(key)
                    if light_key is not None:
                        self._light_name_index.setdefault(light_key, set()).add(entity.id)
                    if entity.area_id and (match := side_name(light_key or key)):
                        form, name = match
                        sides.add(match)
                        self.light_side_index.setdefault(
                            (entity.area_id, form.side, name), set(),
                        ).add(entity.id)
            if sides:
                self.light_side_names[entity.id] = frozenset(sides)

    def key(self, value: str) -> str:
        return self.morphology.key(value)

    def match_entity_name(self, value: str) -> set[str]:
        key = self.key(value)
        if matches := self.entity_index.get(key):
            return matches
        light_key = light_name_key(key)
        return self._light_name_index.get(light_key, set()) if light_key is not None else set()

    def location_key(self, value: str) -> str:
        """Match a short list of common room forms without rewriting device names."""
        key = self.key(value)
        return _LOCATION_FORMS.get(key, key)

    def with_entities(self, entities):
        """Reuse indexes when only permissions or live state differ, never names/aliases."""
        view = copy(self)
        view.entities = {entity.id: entity for entity in entities}
        return view

    def visible_to(self, can_read):
        """Share immutable entries; allocate a replacement only for denied exposure."""
        return self.with_entities(
            replace(entity, exposed=False) if entity.exposed and not can_read(entity.id) else entity
            for entity in self.entities.values()
        )

    def export(self) -> dict:
        from dataclasses import asdict

        def serial(item):
            result = asdict(item)
            if "labels" in result:
                result["labels"] = sorted(result["labels"])
            return result

        return {
            "areas": [serial(a) for a in self.areas.values()],
            "floors": [serial(f) for f in self.floors.values()],
            "entities": [serial(e) for e in self.entities.values()],
            "satellites": self.satellites,
            "devices": self.devices,
        }

    @classmethod
    def from_dict(cls, data: dict):
        def convert(item):
            return {
                **item,
                "labels": frozenset(item.get("labels", ())),
                "aliases": tuple(item.get("aliases", ())),
            }

        return cls(
            areas=[Area(**convert(a)) for a in data.get("areas", ())],
            floors=[Floor(**f) for f in data.get("floors", ())],
            entities=[Entity(**convert(e)) for e in data.get("entities", ())],
            satellites=data.get("satellites"),
            devices=data.get("devices"),
        )
