"""Assist conversation platform preserving satellite, user and chat context."""

from homeassistant.components import conversation
from homeassistant.helpers import intent
from homeassistant.util import dt as dt_util

from .const import DOMAIN
from .core.communication import response_speech
from .core.presentation import Presentation
from .core.types import Request


async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities([PolishConversation(entry, hass.data[DOMAIN][entry.entry_id])])


class PolishConversation(conversation.ConversationEntity):
    _attr_name = "HA Polish Context Voice"
    _attr_supported_features = conversation.ConversationEntityFeature.CONTROL

    def __init__(self, entry, runtime):
        self.entry, self.runtime = entry, runtime
        self._attr_unique_id = entry.entry_id
        self.presentation = Presentation()

    @property
    def supported_languages(self):
        return ["pl"]

    async def async_added_to_hass(self):
        await super().async_added_to_hass()
        conversation.async_set_agent(self.hass, self.entry, self)

    async def async_will_remove_from_hass(self):
        conversation.async_unset_agent(self.hass, self.entry)
        await super().async_will_remove_from_hass()

    async def _async_handle_message(self, user_input, chat_log):
        request = Request(
            text=user_input.text,
            satellite_id=user_input.satellite_id,
            device_id=user_input.device_id,
            conversation_id=chat_log.conversation_id,
            user_id=user_input.context.user_id,
        )
        result = await self.runtime.process(request, user_input.context)
        self.presentation.retry_timer(result, request)
        settings = {**self.entry.data, **self.entry.options}
        model = self.runtime.cache.get()
        speech = response_speech(
            result,
            request,
            model,
            settings,
            dt_util.now().time(),
            quiet_signal=any(
                self.hass.states.is_state(identifier, "on")
                for identifier in settings.get("quiet_signals", [])
            ),
        )
        speech = self.presentation.acknowledgement(speech, result, request)
        chat_log.async_add_assistant_content_without_tools(
            conversation.AssistantContent(
                agent_id=user_input.agent_id,
                content=speech,
            )
        )
        response = intent.IntentResponse(language=user_input.language)
        response.async_set_speech(
            speech,
            extra_data={"silent_ack": True, "reason": "quiet_local_light"}
            if not speech and result.speech
            else None,
        )
        if result.completed:
            response.async_set_results(
                [
                    intent.IntentResponseTarget(
                        name=model.entities[identifier].name
                        if identifier in model.entities
                        else identifier,
                        type=intent.IntentResponseTargetType.ENTITY,
                        id=identifier,
                    )
                    for identifier in result.completed
                ]
            )
        if result.continue_conversation or result.status in {
            "answered",
            "confirm",
            "dry_run",
            "cancelled",
            "ignored",
        }:
            response.response_type = intent.IntentResponseType.QUERY_ANSWER
        if result.status in {"error", "partial"}:
            response.async_set_error(intent.IntentResponseErrorCode.FAILED_TO_HANDLE, result.speech)
        elif not result.continue_conversation and result.status in {
            "blocked",
            "unsupported",
            "clarify",
            "busy",
        }:
            response.async_set_error(intent.IntentResponseErrorCode.NO_VALID_TARGETS, result.speech)
        return conversation.ConversationResult(
            response=response,
            conversation_id=chat_log.conversation_id,
            continue_conversation=result.status == "confirm" or result.continue_conversation,
        )
