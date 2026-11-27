"""Review a local feedback document entered on synthetic data."""

import json

import probatio as vol
from homeassistant.const import __version__ as HA_VERSION
from homeassistant.helpers import selector
from homeassistant.loader import async_get_integration

from .const import DOMAIN
from .core.feedback import (
    AREAS,
    DIALOGUE_STATUSES,
    MAX_TURNS,
    OPERATIONS,
    STATUSES,
    TARGETS,
    FeedbackError,
    dialogue_turn,
    document,
)


def choices(options, *, multiple=False, translation_key=None):
    config = selector.SelectSelectorConfig(
        options=options, multiple=multiple, mode=selector.SelectSelectorMode.DROPDOWN,
    )
    if translation_key is not None:
        config["translation_key"] = translation_key
    return selector.SelectSelector(config)


def feedback_json(report):
    # The preview uses a Markdown code block. Keep arbitrary user input inside it.
    return json.dumps(report, ensure_ascii=False, indent=2).translate(str.maketrans({
        "`": "\\u0060", "<": "\\u003c", ">": "\\u003e", "&": "\\u0026",
    }))


def light_choices():
    return choices([
        {"value": identifier, "label": light.name} for identifier, light in TARGETS.items()
    ], multiple=True)


def room_choices():
    return choices([{"value": area.id, "label": area.name} for area in AREAS])


def expectation_fields(*, dialogue=False):
    return {
        vol.Optional("text"): selector.TextSelector(),
        vol.Optional("expected_status"): choices(
            list(DIALOGUE_STATUSES if dialogue else STATUSES), translation_key="feedback_status",
        ),
        vol.Optional("operation"): choices(list(OPERATIONS), translation_key="feedback_operation"),
        vol.Optional("targets"): light_choices(),
        vol.Optional("brightness_pct"): selector.NumberSelector(selector.NumberSelectorConfig(
            min=1, max=100, step=1, mode=selector.NumberSelectorMode.BOX,
        )),
    }


class FeedbackOptions:
    async def async_step_voice_feedback_example(self, user_input=None):
        return await self._voice_feedback_form("example", user_input)

    async def async_step_voice_feedback_idea(self, user_input=None):
        return await self._voice_feedback_form("idea", user_input)

    async def _voice_feedback_form(self, kind, user_input):
        if user_input is not None and user_input.get("back"):
            self._voice_feedback_draft = {}
            return await self.async_step_voice_trial_result()
        errors = {}
        values = getattr(self, "_voice_feedback_draft", {})
        if user_input is not None:
            values = {key: val for key, val in user_input.items() if key != "back"}
            try:
                report = await self._voice_feedback_document(kind, values)
            except FeedbackError as error:
                errors[error.field if error.field != "versions" else "base"] = (
                    "voice_feedback_" + error.field
                )
            else:
                self._voice_feedback_draft = values
                self._voice_feedback_kind = kind
                self._voice_feedback_json = feedback_json(report)
                return await self.async_step_voice_feedback_review()
        fields = {vol.Optional("back", default=False): bool}
        if kind == "idea":
            fields[vol.Optional("idea")] = selector.TextSelector(
                selector.TextSelectorConfig(multiline=True),
            )
        else:
            fields.update(expectation_fields())
            fields[vol.Optional("source_area")] = room_choices()
        return self.async_show_form(
            step_id="voice_feedback_" + kind,
            data_schema=self.add_suggested_values_to_schema(vol.Schema(fields), values),
            errors=errors,
            last_step=False,
        )

    async def _voice_feedback_document(self, kind, values):
        integration = await async_get_integration(self.hass, DOMAIN)
        return document(
            kind, values, bociek=integration.manifest.get("version"), ha_core=HA_VERSION,
        )

    async def async_step_voice_feedback_dialogue(self, user_input=None):
        turns = getattr(self, "_voice_feedback_turns", [])
        self._voice_feedback_turns = turns
        values = getattr(self, "_voice_feedback_draft", {})
        errors = {}
        if user_input is not None and user_input.get("back"):
            if not turns:
                self._voice_feedback_draft = {}
                return await self.async_step_voice_trial_result()
            values = turns.pop()
        elif user_input is not None:
            values = {k: v for k, v in user_input.items() if k != "back"}
            area = values.get("source_area") if not turns else turns[0].get("source_area")
            try:
                dialogue_turn(values)
                if values.get("turn_action") == "review":
                    report = await self._voice_feedback_document(
                        "dialogue", {"source_area": area, "turns": [*turns, values]},
                    )
                    turns.append(values)
                    self._voice_feedback_kind = "dialogue"
                    self._voice_feedback_draft = {}
                    self._voice_feedback_json = feedback_json(report)
                    return await self.async_step_voice_feedback_review()
                turns.append(values)
                values = {}
            except FeedbackError as error:
                field = "base" if error.field in {"versions", "turns"} else error.field
                errors[field] = "voice_feedback_" + error.field
        self._voice_feedback_draft = values
        first = not turns
        fields = {
            vol.Optional("back", default=False): bool,
            **({vol.Optional("source_area"): room_choices()} if first else {}),
            **expectation_fields(dialogue=True),
            vol.Optional("lights_on"): light_choices(),
            vol.Optional("lights_off"): light_choices(),
            vol.Optional("advance_seconds"): selector.NumberSelector(selector.NumberSelectorConfig(
                min=0, max=600, step=1, mode=selector.NumberSelectorMode.BOX,
            )),
            vol.Optional("followup"): choices(["yes", "no"], translation_key="feedback_followup"),
            vol.Optional("turn_action", default="review" if len(turns) >= MAX_TURNS - 1 else "add"):
                choices(
                    ["add"] if first else ["review"] if len(turns) >= MAX_TURNS - 1 else
                    ["add", "review"], translation_key="feedback_turn_action",
                ),
        }
        return self.async_show_form(
            step_id="voice_feedback_dialogue",
            data_schema=self.add_suggested_values_to_schema(vol.Schema(fields), values),
            description_placeholders={"turn": str(len(turns) + 1), "maximum": str(MAX_TURNS)},
            errors=errors,
            last_step=False,
        )

    async def async_step_voice_feedback_review(self, user_input=None):
        if user_input is not None:
            self._voice_feedback_json = ""
            if user_input.get("back"):
                if self._voice_feedback_kind == "dialogue":
                    self._voice_feedback_draft = self._voice_feedback_turns.pop()
                    return await self.async_step_voice_feedback_dialogue()
                return await self._voice_feedback_form(self._voice_feedback_kind, None)
            self._voice_feedback_draft = {}
            self._voice_feedback_turns = []
            return await self.async_step_voice_trial_result()
        return self.async_show_form(
            step_id="voice_feedback_review",
            data_schema=vol.Schema({vol.Optional("back", default=False): bool}),
            description_placeholders={"report": self._voice_feedback_json},
            last_step=False,
        )
