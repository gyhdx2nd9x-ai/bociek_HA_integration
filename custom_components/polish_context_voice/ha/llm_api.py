"""Optional HA LLM API: a read-only preview using the same semantic pipeline."""

import probatio as vol
from homeassistant.helpers import llm

from ..const import DOMAIN
from ..core.types import Request


class PreviewTool(llm.Tool):
    name = "preview_polish_home_command"
    description = (
        "Preview a Polish home command. Never executes actions. Ask the user to issue "
        "the command to HA Polish Context Voice to execute it."
    )
    integration = DOMAIN
    parameters = vol.Schema({vol.Required("text"): vol.All(str, vol.Length(min=1, max=2000))})
    annotations = llm.ToolAnnotations(
        read_only=True, destructive=False, idempotent=True, open_world=True
    )

    def __init__(self, runtime):
        self.runtime = runtime

    async def async_call(self, hass, tool_input, llm_context):
        result = await self.runtime.process(
            Request(
                text=tool_input.tool_args["text"],
                device_id=llm_context.device_id,
                user_id=llm_context.context.user_id if llm_context.context else None,
                dry_run=True,
            ),
            llm_context.context,
        )
        return llm.ToolResult(data=result.to_dict(), error=result.status in {"error", "blocked"})


class SemanticAPI(llm.API):
    def __init__(self, hass, runtime):
        super().__init__(hass=hass, id=DOMAIN, name="Polish Context Voice — preview")
        self.runtime = runtime

    async def async_get_api_instance(self, llm_context):
        return llm.APIInstance(
            api=self,
            api_prompt=(
                "Use the preview tool to inspect semantic targets. "
                "A preview is not an executed action. Never claim execution."
            ),
            llm_context=llm_context,
            tools=[PreviewTool(self.runtime)],
        )
