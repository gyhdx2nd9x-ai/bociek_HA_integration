"""Bounded confirmations and per-player admission for named media playback."""

from dataclasses import dataclass, replace
from time import monotonic, perf_counter

from .communication import ACTION_FAILED_SPEECH
from .context import resolve_scope
from .context.admission import Admission, RequestBusy
from .context.dialogue import classify_reply, target_reply
from .engine import fingerprint
from .music import plan_music
from .nlu.normalize import normalize
from .types import Result


def music_fingerprint(plan):
    return fingerprint(plan), tuple(sorted((plan.response_data or {}).items()))


@dataclass(frozen=True)
class PendingMusic:
    command: object
    fingerprint: tuple
    expires_at: float


@dataclass(frozen=True)
class MusicChoice:
    command: object
    candidates: tuple[str, ...]
    expires_at: float


@dataclass(frozen=True)
class MusicMemory:
    player: str
    expires_at: float


class MusicSession:
    def __init__(self, clock=monotonic):
        self.clock = clock
        self.pending = {}
        self.questions = {}
        self.memory = {}
        self.admission = Admission(limit=8)

    def follows(self, request, model=None):
        self.prune()
        choice = self.questions.get(request.session_key)
        if choice and model is not None:
            return (
                classify_reply(request.text) is False
                or len(target_reply(normalize(request.text), choice.candidates, model)) == 1
            )
        return (
            request.session_key in self.pending and classify_reply(request.text) is not None
        )

    def prune(self):
        for mapping in (self.pending, self.questions, self.memory):
            for key, item in list(mapping.items()):
                if item.expires_at <= self.clock():
                    mapping.pop(key, None)

    def clear(self):
        self.pending.clear()
        self.questions.clear()
        self.memory.clear()

    def memory_key(self, request, model):
        from .music import MusicCommand

        scope = resolve_scope(MusicCommand("context"), request, model)
        if scope.error or len(scope.areas) != 1:
            return None
        return (request.user_id, request.satellite_id, request.device_id, scope.areas[0])

    @staticmethod
    def put(mapping, key, value):
        if len(mapping) >= 512:
            mapping.pop(next(iter(mapping)))
        mapping[key] = value

    async def process(self, command, request, model, settings, executor, validate_backend):
        started = perf_counter()
        try:
            result = await self._process(
                command, request, model, settings, executor, validate_backend
            )
        except RequestBusy:
            result = Result(
                "busy",
                "Ten głośnik obsługuje już polecenie. Spróbuj za chwilę.",
                reason="music_player_busy",
            )
        except Exception:
            # Provider messages may contain signed URLs. Do not log payloads or secrets.
            result = Result(
                "error",
                ACTION_FAILED_SPEECH,
                reason="music_failed",
            )
        result.route = "music"
        result.conversation_id = request.conversation_id
        result.latency_ms = round((perf_counter() - started) * 1000, 3)
        return result

    async def _process(self, command, request, model, settings, executor, validate_backend):
        self.prune()
        choice = self.questions.pop(request.session_key, None)
        previous = self.pending.pop(request.session_key, None)
        reply = classify_reply(request.text) if command is None else None
        if command is None and choice:
            if reply is False:
                return Result("cancelled", "Anulowano.")
            selected = target_reply(normalize(request.text), choice.candidates, model())
            if len(selected) != 1:
                return Result("clarify", "Podaj nazwę głośnika.", reason="ambiguous_music_session")
            command = replace(choice.command, selected_player=selected[0])
        confirmed = command is None and reply is True
        if command is None:
            if not previous:
                return Result(
                    "clarify", "Nie ma operacji muzycznej do potwierdzenia.", reason="no_pending"
                )
            if not confirmed:
                return Result("cancelled", "Anulowano.")
            command = previous.command
        memory_key = self.memory_key(request, model())
        remembered = self.memory.get(memory_key)
        if remembered and command.contextual and not command.player_name and not command.location:
            command = replace(command, preferred_player=remembered.player)
        plan = plan_music(command, request, model(), settings)
        if plan.reason == "ambiguous_music_session" and plan.continue_conversation:
            if not request.dry_run:
                self.put(
                    self.questions,
                    request.session_key,
                    MusicChoice(
                        command, plan.response_data["player_candidates"], self.clock() + 30
                    ),
                )
            return plan
        if plan.status not in {"ready", "confirm"}:
            return plan
        failure = validate_backend(plan)
        if failure:
            return failure
        if confirmed and music_fingerprint(plan) != previous.fingerprint:
            return Result(
                "clarify",
                "Zmieniło się odtwarzanie lub konfiguracja. Powtórz polecenie.",
                reason="music_plan_changed",
            )
        if request.dry_run:
            plan.status = "dry_run"
            plan.speech = "Plan muzyczny gotowy; odtwarzanie nie zostało zmienione."
            return plan
        with self.admission.enter(plan.response_data["playback_key"]):
            await executor.preflight(plan.calls, request)
            current = plan_music(command, request, model(), settings)
            if music_fingerprint(current) != music_fingerprint(plan):
                return Result(
                    "clarify",
                    "Zmienił się odtwarzacz. Powtórz polecenie.",
                    reason="music_plan_changed",
                )
            failure = validate_backend(current)
            if failure:
                return failure
            if plan.status == "confirm" and not confirmed:
                if len(self.pending) >= 512:
                    self.pending.pop(next(iter(self.pending)))
                self.pending[request.session_key] = PendingMusic(
                    command, music_fingerprint(plan), self.clock() + 30
                )
                return plan
            await executor.execute(plan.calls[0], request)
            plan.status = "executed"
            plan.reason = ""
            plan.speech = plan.response_data["success_speech"]
            plan.completed = plan.targets
            player = plan.response_data["selected_player"]
            if memory_key is not None and model().entities[player].area_id == memory_key[-1]:
                self.put(self.memory, memory_key, MusicMemory(player, self.clock() + 60))
            return plan
