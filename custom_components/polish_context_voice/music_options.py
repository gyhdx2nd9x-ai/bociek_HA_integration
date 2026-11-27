"""Native HA forms for portable music shortcuts and installation-specific rooms."""

from uuid import uuid4

import probatio as vol
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import selector

from .core.music import validate_shortcut


def choices(items):
    return selector.SelectSelector(
        selector.SelectSelectorConfig(
            options=[{"value": key, "label": label} for key, label in items],
            mode=selector.SelectSelectorMode.DROPDOWN,
        )
    )


class MusicOptions:
    @property
    def values(self):
        return {**self.config_entry.data, **self.config_entry.options}

    def save_music(self, **updates):
        return self.async_create_entry(title="", data={**self.config_entry.options, **updates})

    async def async_step_music_shortcuts(self, user_input=None):
        shortcuts = self.values.get("music_shortcuts", {})
        if user_input is not None:
            if user_input.get("back"):
                return await self.async_step_init()
            selected = user_input["shortcut"]
            if selected != "__new__" and selected not in shortcuts:
                return self.async_abort(reason="music_selection_missing")
            self._music_id = uuid4().hex if selected == "__new__" else selected
            return await self.async_step_music_shortcut()
        return self.async_show_form(
            step_id="music_shortcuts",
            last_step=False,
            data_schema=vol.Schema(
                {
                    vol.Optional("back", default=False): bool,
                    vol.Required("shortcut", default="__new__"): choices(
                        [("__new__", "＋ Dodaj skrót / Add shortcut")]
                        + [(key, value["name"]) for key, value in shortcuts.items()]
                    ),
                }
            ),
        )

    async def async_step_music_shortcut(self, user_input=None):
        shortcuts = dict(self.values.get("music_shortcuts", {}))
        current = shortcuts.get(self._music_id, {})
        issues = {}
        if user_input is not None:
            if user_input.get("back"):
                return await self.async_step_music_shortcuts()
            if user_input.get("delete"):
                shortcuts.pop(self._music_id, None)
                rooms = {
                    key: {**value, "default_shortcut": ""}
                    if value.get("default_shortcut") == self._music_id
                    else value
                    for key, value in self.values.get("music_rooms", {}).items()
                }
                return self.save_music(music_shortcuts=shortcuts, music_rooms=rooms)
            if not user_input.get("media"):
                return self.async_show_form(
                    step_id="music_shortcut",
                    last_step=False,
                    data_schema=self._music_shortcut_schema,
                    errors={"base": "music_media_required"},
                )
            media = dict(user_input["media"])
            media.pop(
                "metadata", None
            )  # Decoration and transient image URLs are not configuration.
            selected = er.async_get(self.hass).async_get(media.get("entity_id"))
            candidate = {
                "name": user_input["name"].strip(),
                "aliases": [
                    line.strip()
                    for line in user_input.get("aliases", "").splitlines()
                    if line.strip()
                ],
                "backend": user_input["backend"],
                "media": media,
                "config_entry_id": selected.config_entry_id if selected else None,
            }
            error = validate_shortcut(candidate, shortcuts, self._music_id)
            if candidate["backend"] == "music_assistant" and (
                selected is None or selected.platform != "music_assistant"
            ):
                error = "choose_ma_player"
            if error:
                issues["base"] = error
            else:
                shortcuts[self._music_id] = candidate
                return self.save_music(music_shortcuts=shortcuts)
            current = candidate
        fields = {
            vol.Optional("back", default=False): bool,
            vol.Required("name", default=current.get("name", "")): str,
            vol.Optional(
                "aliases", default="\n".join(current.get("aliases", []))
            ): selector.TextSelector(selector.TextSelectorConfig(multiline=True)),
            vol.Required("backend", default=current.get("backend", "music_assistant")): choices(
                [
                    ("music_assistant", "Music Assistant"),
                    ("home_assistant", "Home Assistant"),
                ]
            ),
            vol.Optional(
                "media", **({"default": current["media"]} if current.get("media") else {})
            ): selector.MediaSelector(),
        }
        if self._music_id in shortcuts:
            fields[vol.Optional("delete", default=False)] = bool
        self._music_shortcut_schema = vol.Schema(fields)
        return self.async_show_form(
            step_id="music_shortcut",
            last_step=False,
            data_schema=self._music_shortcut_schema,
            errors=issues,
        )

    async def async_step_music_rooms(self, user_input=None):
        if user_input is not None:
            if user_input.get("back"):
                return await self.async_step_init()
            self._music_area = user_input.get("area")
            if ar.async_get(self.hass).async_get_area(self._music_area) is None:
                return self.async_abort(reason="music_selection_missing")
            return await self.async_step_music_room()
        return self.async_show_form(
            step_id="music_rooms",
            last_step=False,
            data_schema=vol.Schema(
                {
                    vol.Optional("back", default=False): bool,
                    vol.Optional("area"): selector.AreaSelector(),
                }
            ),
        )

    async def async_step_music_room(self, user_input=None):
        rooms = dict(self.values.get("music_rooms", {}))
        current = rooms.get(self._music_area, {})
        shortcuts = self.values.get("music_shortcuts", {})
        issues = {}
        if user_input is not None:
            if user_input.get("back"):
                return await self.async_step_music_rooms()
            if user_input.get("delete"):
                rooms.pop(self._music_area, None)
                return self.save_music(music_rooms=rooms)
            player_id = user_input.get("player")
            entity = er.async_get(self.hass).async_get(player_id) if player_id else None
            if player_id and (
                entity is None
                or er.async_get_effective_area_id(self.hass, entity) != self._music_area
            ):
                issues["base"] = "music_player_area"
            elif (
                user_input.get("default_shortcut")
                and user_input["default_shortcut"] not in shortcuts
            ):
                issues["base"] = "music_selection_missing"
            elif entity is not None and entity.domain != "media_player":
                issues["base"] = "music_player_area"
            else:
                default = shortcuts.get(user_input.get("default_shortcut"))
                if (
                    default
                    and entity is not None
                    and default["backend"] == "music_assistant"
                    and (
                        entity.platform != "music_assistant"
                        or entity.config_entry_id != default.get("config_entry_id")
                    )
                ):
                    issues["base"] = "choose_ma_player"
            if not issues:
                rooms[self._music_area] = {
                    k: user_input.get(k) or "" for k in ("player", "default_shortcut")
                }
                return self.save_music(music_rooms=rooms)
            current = user_input
        area = ar.async_get(self.hass).async_get_area(self._music_area)
        fields = {
            vol.Optional("back", default=False): bool,
            vol.Optional(
                "player", description={"suggested_value": current.get("player")}
            ): selector.EntitySelector(selector.EntitySelectorConfig(domain="media_player")),
            vol.Optional("default_shortcut", default=current.get("default_shortcut", "")): choices(
                [("", "— Bez domyślnego skrótu / No default —")]
                + [(key, value["name"]) for key, value in shortcuts.items()]
            ),
        }
        if self._music_area in rooms:
            fields[vol.Optional("delete", default=False)] = bool
        return self.async_show_form(
            step_id="music_room",
            last_step=False,
            data_schema=vol.Schema(fields),
            errors=issues,
            description_placeholders={
                "area": area.name if area else self._music_area,
            },
        )
