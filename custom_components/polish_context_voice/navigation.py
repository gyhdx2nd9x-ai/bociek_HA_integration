"""Describe our own flow navigation for the bundled, scoped frontend adapter."""

import json


class NavigationOptions:
    def _navigation(self, result):
        step = result["step_id"]
        if result["type"] == "menu":
            parent = {
                "voice_room": "voice_roles",
                "voice_lighting": "voice_room",
                "voice_profile_saved": "voice_lighting",
            }.get(step)
            payload = {"next_step_id": parent} if parent else None
        else:
            payload = {"back": True}
            if step == "voice_lights":
                payload = {"action": "back"}
            elif step == "voice_review":
                payload = {"action": "edit"}
            elif step == "voice_saved":
                payload = {"action": "room"}
            elif step == "voice_empty":
                payload = {}
        if payload is not None:
            # A string placeholder survives HA's public flow serialization. It is not
            # displayed in descriptions. The server still validates every navigation.
            result["description_placeholders"] = {
                **(result.get("description_placeholders") or {}),
                "pcv_navigation": json.dumps({"domain": "polish_context_voice", "data": payload}),
            }
        return result

    def async_show_form(self, **kwargs):
        return self._navigation(super().async_show_form(**kwargs))

    def async_show_menu(self, **kwargs):
        return self._navigation(super().async_show_menu(**kwargs))
