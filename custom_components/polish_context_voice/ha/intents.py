"""Execution boundary: exact service targets with HA user permissions and Context."""

from homeassistant.auth.permissions.const import POLICY_CONTROL
from homeassistant.components.homeassistant import exposed_entities
from homeassistant.exceptions import HomeAssistantError

from ..core.diagnostics import measure_service_call, measure_stage


class HAExecutor:
    def __init__(self, hass, context=None):
        self.hass = hass
        self.context = context  # One executor per request, never shared between conversations.

    async def preflight(self, calls, request):
        with measure_stage("ha_preflight"):
            await self._preflight(calls, request)

    async def _preflight(self, calls, request):
        user = None
        if request.user_id:
            user = await self.hass.auth.async_get_user(request.user_id)
            if user is None or not user.is_active:
                raise HomeAssistantError("User unavailable")
        for call in calls:
            if not self.hass.services.has_service(call.domain, call.service):
                raise HomeAssistantError("Service unavailable")
            for entity_id in dict.fromkeys((*call.entity_ids, *call.guard_entity_ids)):
                state = self.hass.states.get(entity_id)
                if state is None or state.state in {"unknown", "unavailable"}:
                    raise HomeAssistantError("Entity unavailable")
                if (
                    call.required_state is not None and entity_id in call.entity_ids
                    and state.state != call.required_state
                ):
                    raise HomeAssistantError("Entity state changed")
                if not exposed_entities.async_should_expose(self.hass, "conversation", entity_id):
                    raise HomeAssistantError("Entity no longer exposed")
                if user and not user.permissions.check_entity(entity_id, POLICY_CONTROL):
                    raise HomeAssistantError("Control not permitted")

    async def execute(self, call, request):
        await self.preflight((call,), request)
        with measure_service_call():
            await self.hass.services.async_call(
                call.domain,
                call.service,
                {**call.data, "entity_id": list(call.entity_ids)},
                blocking=True,
                context=self.context,
            )
