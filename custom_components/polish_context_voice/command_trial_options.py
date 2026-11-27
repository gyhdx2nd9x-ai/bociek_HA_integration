"""Try a local control command from the HA configuration flow."""

import re

import probatio as vol
from homeassistant.helpers import selector

from .feedback_options import FeedbackOptions, choices
from .ha.command_trial import preview
from .ha.registry_sync import snapshot


def plain(value):
    """Keep registry names and engine speech as text inside HA's Markdown description."""
    return re.sub(r"([\\`*_{}\[\]()<>#+.!|])", r"\\\1", " ".join(value.split()))


class CommandTrialOptions(FeedbackOptions):
    async def async_step_voice_trial(self, user_input=None):
        if user_input is not None and user_input.get("back"):
            self._voice_trial_input = {}
            return await self.async_step_init()
        errors = {}
        values = getattr(self, "_voice_trial_input", {})
        if user_input is not None:
            text = user_input.get("text", "").strip()
            area = user_input.get("area")
            values = {"text": text[:400], **({"area": area} if area else {})}
            if not 1 <= len(text) <= 400:
                errors["text"] = "voice_trial_text"
            else:
                model = snapshot(self.hass)
                if area and area not in model.areas:
                    errors["area"] = "voice_trial_area"
                else:
                    result = await preview(model, text, area or None)
                    self._voice_trial_input = values
                    self._voice_trial_result = {
                        "speech": plain(result["speech"]),
                        **{key: plain(", ".join(result[key])) or "—"
                           for key in ("targets", "areas", "actions")},
                        "explanation": "\n".join(
                            f"- {plain(line)}" for line in result["explanation"]
                        ) or "—",
                    }
                    return await self.async_step_voice_trial_result()
        return self.async_show_form(
            step_id="voice_trial",
            last_step=False,
            data_schema=self.add_suggested_values_to_schema(
                vol.Schema({
                    vol.Optional("back", default=False): bool,
                    vol.Optional("text"): selector.TextSelector(),
                    vol.Optional("area"): selector.AreaSelector(),
                }),
                values,
            ),
            errors=errors,
        )

    async def async_step_voice_trial_result(self, user_input=None):
        if user_input is not None:
            if user_input.get("back"):
                self._voice_trial_result = {}
                return await self.async_step_voice_trial()
            if user_input.get("next") == "example":
                return await self.async_step_voice_feedback_example()
            if user_input.get("next") == "dialogue":
                return await self.async_step_voice_feedback_dialogue()
            if user_input.get("next") == "idea":
                return await self.async_step_voice_feedback_idea()
            self._voice_trial_input = self._voice_trial_result = {}
            return await self.async_step_init()
        return self.async_show_form(
            step_id="voice_trial_result",
            last_step=False,
            data_schema=vol.Schema({
                vol.Optional("back", default=False): bool,
                vol.Optional("next", default="done"): choices(
                    ["done", "example", "dialogue", "idea"], translation_key="trial_next",
                ),
            }),
            description_placeholders=self._voice_trial_result.copy(),
        )
