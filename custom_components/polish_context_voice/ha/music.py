"""Music execution through public HA entity services, with backend identity checks."""

import asyncio

from homeassistant.helpers import entity_registry as er

from ..core.communication import ACTION_FAILED_SPEECH
from ..core.types import Result
from .intents import HAExecutor


async def process(runtime, request, context, command, user):
    def validate_backend(plan):
        data = plan.response_data or {}
        if data.get("backend") == "music_assistant":
            entry = er.async_get(runtime.hass).async_get(plan.calls[0].entity_ids[0])
            if (
                entry is None
                or entry.platform != "music_assistant"
                or entry.config_entry_id != data.get("config_entry_id")
            ):
                return Result(
                    "blocked",
                    "Ten skrót wymaga głośnika z tej samej instancji "
                    "Music Assistant, w której wybrano muzykę.",
                    reason="music_backend_mismatch",
                )
        if not runtime.hass.services.has_service(plan.calls[0].domain, plan.calls[0].service):
            return Result(
                "blocked",
                "Integracja odtwarzacza nie jest dostępna.",
                reason="music_service_missing",
            )
        return None

    try:
        async with asyncio.timeout(15):
            return await runtime.music.process(
                command,
                request,
                lambda: runtime.visible_model(user),
                runtime.music_settings,
                HAExecutor(runtime.hass, context),
                validate_backend,
            )
    except TimeoutError:
        return Result(
            "error",
            ACTION_FAILED_SPEECH,
            route="music",
            reason="music_timeout",
            conversation_id=request.conversation_id,
        )
