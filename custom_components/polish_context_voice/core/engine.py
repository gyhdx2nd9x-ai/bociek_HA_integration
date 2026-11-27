"""Shared pipeline: NLU → resolution → policy → fresh validation → execution."""

import logging
from dataclasses import dataclass, replace
from time import monotonic, perf_counter
from uuid import uuid4

from . import nlu
from .capability_catalog import definition_for
from .capability_contracts import annotate_capability
from .communication import ACTION_FAILED_SPEECH, UNRECOGNIZED_COMMAND_SPEECH
from .context import resolve_scope
from .context.dialogue import (
    CONFIRMATIONS,
    REJECTIONS,
    Command,
    Dialogue,
    LightChoice,
    Turn,
    classify_reply,
    corrected_room,
    is_choice_correction,
    is_confirmation_reply,
    light_category_reply,
    location_reply,
    ordinal_reply,
    preferred_light_reply,
    reference_reply,
    repeated_room,
    reply_words,
    target_reply,
)
from .context.light_sides import LightSideReference, light_side_anchor, matching_light_sides
from .diagnostics import mark_milestone, measure_stage
from .home_model.light_profiles import (
    has_default_light,
    has_light_semantic_roles,
    lights_for_command,
    matches_light_role,
)
from .light_feedback import light_state_feedback, snapshot_light_state
from .llm.limits import FallbackBusy
from .nlu.light_prefix import light_prefixes
from .nlu.light_sides import side_reply
from .nlu.normalize import has_foreign_letters, normalize
from .policy import evaluate
from .resolver import ResolutionTrace, named_matches, resolve
from .response_facts import capability_speech
from .types import Action, Request, Result, Risk

LOGGER = logging.getLogger(__name__)
REFERENCE_CANDIDATE_LIMIT = 64
REFERENCE_GROUP_LIMIT = 256
LIGHT_ALTERNATIVES = {"voice_task": "robocze", "voice_ambient": "dekoracyjne"}
ROOM_LIGHT_KINDS = nlu.LIT_KINDS | {"light", "all_lights", "purpose_light"}


@dataclass
class Pending:
    proposal: object
    fingerprint: tuple
    expires_at: float
    background: bool = False
    background_prompt: str = ""
    background_words: tuple[int, int] = (0, 0)
    completed_room_areas: tuple[str, ...] | None = None
    light_choice: LightChoice | None = None


def fingerprint(result):
    return (
        result.targets,
        result.area_ids,
        result.risk,
        result.status,
        tuple(
            (c.domain, c.service, c.entity_ids, tuple(sorted(c.data.items())),
             c.guard_entity_ids, c.required_state)
            for c in result.calls
        ),
    )


def reference_structure(home, identifiers):
    """Snapshot only selected roots and their bounded safety/identity metadata.

    Live state changes are harmless here. Group membership, roles, permissions,
    location, aliases and supported operations must survive the question intact.
    """
    found, waiting = {}, list(identifiers)
    while waiting:
        identifier = waiting.pop()
        if identifier in found:
            continue
        entity = home.entities.get(identifier)
        if entity is None or len(found) >= REFERENCE_GROUP_LIMIT:
            return None
        found[identifier] = (
            entity.id,
            entity.name,
            entity.aliases,
            entity.area_id,
            entity.exposed,
            entity.available,
            tuple(sorted(entity.labels)),
            entity.device_class,
            entity.members,
            tuple(entity.attributes.get("supported_color_modes", ())),
        )
        waiting.extend(entity.members)
    return tuple(found[identifier] for identifier in sorted(found))


def category_roots(home, area, role):
    """The same exposed aggregate roots as the ordinary role resolver."""
    return tuple(
        sorted(
            entity.id
            for entity in home.entities.values()
            if entity.domain == "light"
            and entity.area_id == area
            and entity.exposed
            and not entity.labels & {"voice_block", "voice_exclude_all"}
            and matches_light_role(entity.labels, role)
        )
    )


def preferred_lights(candidates, home):
    return tuple(
        key for key in candidates if key.startswith("light.")
        and "voice_primary" in home.entities[key].labels
    )


class Engine:
    def __init__(
        self,
        model_provider,
        executor=None,
        fallback=None,
        confirmation_ttl=30,
        clock=monotonic,
        pending=None,
        dialogue=None,
        *,
        explain_resolution=False,
    ):
        self.model_provider, self.executor, self.fallback = model_provider, executor, fallback
        self.confirmation_ttl, self.clock = confirmation_ttl, clock
        self.pending = pending if pending is not None else {}
        self.dialogue = dialogue if dialogue is not None else Dialogue()
        self.explain_resolution = explain_resolution

    async def process(self, request: Request) -> Result:
        started = perf_counter()
        request = replace(request, conversation_id=request.conversation_id or str(uuid4()))
        try:
            result = await self._process(request)
            if speech := capability_speech(result):
                result.speech = speech
        except Exception:
            LOGGER.exception("Voice pipeline failed")
            result = Result(
                "error", "Nie udało się przetworzyć polecenia.", reason="pipeline_error"
            )
        result.conversation_id = request.conversation_id
        result.latency_ms = round((perf_counter() - started) * 1000, 3)
        return result

    def plan(self, proposal, request, *, explain=False):
        # Includes each model lookup and fresh validation, not just the first proposal.
        with measure_stage("resolution_policy"):
            command = proposal if isinstance(proposal, Command) else Command(proposal)
            home = self.model_provider()
            if command.category_fallback:
                query = command.query or command.proposal
                scope = resolve_scope(query, request, home)
                if (
                    scope.error
                    or len(scope.areas) != 1
                    or has_default_light(home, scope.areas[0])
                    or category_roots(home, scope.areas[0], query.qualifier) != command.selected
                ):
                    return Result(
                        "clarify",
                        "Zmieniły się urządzenia lub ustawienia świateł. Powtórz pełne polecenie.",
                        reason="context_changed",
                    )
            if command.expected_structure is not None and (
                reference_structure(home, command.selected) != command.expected_structure
            ):
                return Result(
                    "clarify",
                    "Zmieniły się urządzenia lub uprawnienia. Powtórz pełne polecenie.",
                    reason="context_changed",
                )
            if command.light_side_reference is not None and matching_light_sides(
                home, command.light_side_reference
            ) != frozenset(command.selected):
                return Result(
                    "clarify",
                    "Układ lamp się zmienił. Podaj nazwę lampy.",
                    reason="context_changed",
                )
            trace = ResolutionTrace() if explain and request.dry_run else None
            resolution = resolve(
                command.query or command.proposal, request, home, command.selected, trace=trace,
                selected_leaves=command.selected_leaves,
            )
            if command.expected_targets is not None and (
                resolution.error
                or tuple(e.id for e in resolution.entities) != command.expected_targets
                or resolution.area_ids != command.expected_areas
            ):
                return Result(
                    "clarify",
                    "Zmieniły się urządzenia lub ich dostępność. Powtórz pełne polecenie.",
                    reason="context_changed",
                )
            result = evaluate(command.proposal, resolution)
            if trace is not None:
                result._resolution_trace = trace
            result.response_data = {
                **(result.response_data or {}),
                "interpretation": {
                    "action": command.proposal.action.value,
                    "kind": command.proposal.kind,
                    "location": (command.query or command.proposal).location,
                    "value": command.proposal.value,
                    "target_source": (
                        "dialogue" if command.query or command.selected else "utterance"
                    ),
                    "confidence_source": "proposal_score_not_probability",
                },
            }
            if not hasattr(result, "_capability_report"):
                # The migrated policy already describes a prepared operation.
                # Early resolution/risk failures still need their bounded report.
                annotate_capability(result, definition_for(command.proposal.action))
            if not request.dry_run:
                # Keep the observation on this particular plan, outside the
                # serialized dataclass fields. Reuse the model already read for
                # fresh resolution; another registry scan is unnecessary.
                result._light_state_snapshot = snapshot_light_state(command.proposal, home, result)
            if result.status in {"ready", "confirm"} and len(resolution.entities) == 1:
                result._light_side_anchor = light_side_anchor(home, resolution.entities[0])
            return result

    async def _process(self, request):
        self.dialogue.prune(self.clock())
        for key, value in list(self.pending.items()):
            if value.expires_at <= self.clock():
                del self.pending[key]
        with measure_stage("interpretation"):
            key, text = request.session_key, normalize(request.text)
            reply = classify_reply(request.text)
        background = self.pending.get(key)
        background = background if background and background.background else None
        if reply is False:
            self.pending.pop(key, None)
            self.dialogue.discard(key)
            return Result(
                "cancelled", "Polecenie nie zostało wykonane." if background else "Anulowano."
            )
        if background and reply is True:
            self.pending.pop(key, None)
            self.dialogue.discard(key)
            current = self._validated_background(background, request)
            if current is None:
                return Result(
                    "clarify",
                    "Zmieniły się urządzenia lub uprawnienia. Powtórz pełne polecenie.",
                    reason="plan_changed",
                )
            return await self._execute_and_remember(current, request, background.proposal)
        if background:
            # A short unclear reply may repeat the same question. It never
            # refreshes its lifetime, executes it, or interprets foreign text
            # as an affirmative after ASCII normalization.
            if (
                len(request.text) <= 160
                and len(request.text.split()) <= 8
                and nlu.parse(request.text) is None
                and not nlu.unsafe_for_fallback(request.text)
            ):
                current = self._validated_background(background, request)
                if current is None:
                    self.pending.pop(key, None)
                    return Result(
                        "clarify",
                        "Zmieniły się urządzenia lub uprawnienia. Powtórz pełne polecenie.",
                        reason="plan_changed",
                    )
                return self._background_question(background, current)
            self.pending.pop(key, None)
        if reply is True or text in CONFIRMATIONS or text in REJECTIONS:
            question = self.dialogue.questions.get(key)
            if question and question.missing_category:
                self.dialogue.questions.pop(key, None)
                self.dialogue.memory.pop(key, None)
                return await self._answer_category(question, request)
            if question and question.missing_target:
                return self._repeat_reference(question, request)
            self.dialogue.discard(key)
            pending = self.pending.pop(key, None)  # Single use, including failed executions.
            if reply is not True:
                return Result(
                    "clarify", "Odpowiedź jest niejednoznaczna. Powtórz polecenie.",
                    reason="unclear_reply",
                )
            if pending is None:
                return Result(
                    "clarify", "Nie ma aktualnej operacji do potwierdzenia.", reason="no_pending"
                )
            current = self.plan(pending.proposal, request)
            if fingerprint(current) != pending.fingerprint:
                return Result(
                    "clarify",
                    "Zmieniły się urządzenia lub uprawnienia. Powtórz polecenie.",
                    reason="plan_changed",
                )
            return await self._execute_and_remember(
                current, request, pending.proposal, light_choice=pending.light_choice,
            )
        # Invalidate the old action before interpreting a new instruction.
        pending = self.pending.pop(key, None)
        question = self.dialogue.questions.pop(key, None)
        memory = self.dialogue.memory.pop(key, None)
        with measure_stage("interpretation"):
            proposal = nlu.parse(request.text)
        if proposal is not None and proposal.kind in nlu.LIT_KINDS and has_foreign_letters(
            request.text
        ):
            return Result("clarify", "Powtórz pełne polecenie.", reason="unclear_reply")
        if proposal is None:
            position, attempted = ordinal_reply(
                request.text, suffixes=(
                    "lampa", "lampe", "lampka", "lampke", "swiatlo", "urzadzenie", "opcja", "opcje",
                ),
            )
            if attempted:
                if question is None and is_choice_correction(request.text):
                    return self._correct_light_choice(memory, pending, position, request)
                return await self._answer_numbered_choice(question, position, request)
        if (
            question
            and question.missing_category
            and (
                proposal is None
                or proposal.kind == "reference"
                or has_foreign_letters(request.text)
            )
        ):
            return await self._answer_category(question, request)
        if (
            question
            and question.missing_target
            and (
                proposal is None
                or proposal.kind == "reference"
                or has_foreign_letters(request.text)
            )
        ):
            return await self._answer_reference(question, request, proposal)
        if proposal is not None and proposal.kind == "reference":
            if has_foreign_letters(request.text):
                return Result("clarify", "Powtórz pełne polecenie.", reason="no_reference")
            if memory is None or proposal.action not in {Action.ON, Action.OFF, Action.BRIGHTNESS}:
                return self._ask_reference(proposal, request)
            previous = memory.command
            query = previous.query or previous.proposal
            if proposal.location is not None:
                query = replace(query, location=proposal.location)
            command = replace(
                previous,
                query=query,
                selected_leaves=previous.expected_targets if query.kind in nlu.LIT_KINDS else
                previous.selected_leaves,
                light_side_reference=None,
                proposal=replace(
                    previous.proposal,
                    action=proposal.action,
                    value=proposal.value,
                ),
            )
            return await self._run(command, request)
        if proposal is None and question is None and (side := side_reply(text)):
            if has_foreign_letters(request.text):
                return Result("clarify", "Podaj nazwę lampy.", reason="no_reference")
            return await self._repeat_light_side(memory, request, side)
        if proposal is None and question is None and (area := repeated_room(text)):
            if memory is None or memory.command.query or memory.command.selected:
                return Result(
                    "clarify", "Powiedz, co zrobić w tym pomieszczeniu.", reason="no_reference"
                )
            previous = memory.command.proposal
            if (
                previous.kind not in ROOM_LIGHT_KINDS | {"single_light"}
                or nlu.unsafe_for_fallback(area)
                or has_foreign_letters(request.text)
            ):
                return Result("clarify", "Powtórz pełne polecenie.", reason="no_reference")
            return await self._run(Command(replace(previous, location=area)), request)
        if proposal is None:
            correction = corrected_room(request.text) if text.startswith("nie ") else None
            if correction and question is None:
                return self._correct_room(memory, pending, request, correction)
            if question and (correction or not nlu.unsafe_for_fallback(text)):
                return await self._answer_clarification(
                    question, request, reply_words(correction or request.text),
                )
            if question is None and preferred_light_reply(text):
                return Result(
                    "clarify", "Podaj pełne polecenie z nazwą lampy.", reason="no_reference",
                )
        if proposal is None:
            background = self._ask_background(request)
            if background is not None:
                return background
        if proposal is None and self.fallback and not nlu.unsafe_for_fallback(request.text):
            try:
                with measure_stage("interpretation"):
                    proposal = await self.fallback.propose(
                        request.text, request, self.model_provider()
                    )
                    # Explicit location survives omissions and contradictions from an LLM.
                    _, explicit_location = nlu.split_location(
                        nlu.canonical_order(normalize(request.text))
                    )
                    if proposal is not None and explicit_location:
                        proposal = replace(proposal, location=explicit_location)
            except FallbackBusy:
                return Result(
                    "busy",
                    "Trwa inne złożone polecenie. Użyj prostszego polecenia.",
                    route="llm",
                    reason="fallback_busy",
                )
            except Exception:
                LOGGER.warning("Semantic fallback unavailable", exc_info=True)
                return Result(
                    "unsupported",
                    UNRECOGNIZED_COMMAND_SPEECH,
                    route="llm",
                    reason="fallback_unavailable",
                )
        if proposal is None:
            return Result(
                "unsupported", UNRECOGNIZED_COMMAND_SPEECH, reason="no_intent"
            )
        return await self._run(Command(proposal), request)

    async def _repeat_light_side(self, memory, request, side):
        if (
            memory is None or memory.light_side_anchor is None
            or memory.command.proposal.action not in {Action.ON, Action.OFF, Action.BRIGHTNESS}
        ):
            return Result("clarify", "Podaj pełne polecenie z nazwą lampy.", reason="no_reference")
        reference = LightSideReference(memory.light_side_anchor, side)
        home = self.model_provider()
        matches = matching_light_sides(home, reference)
        if not matches:
            return Result("clarify", "Podaj pełne polecenie z nazwą lampy.", reason="no_reference")
        if len(matches) > 1:
            proposal = replace(
                memory.command.proposal, kind="single_light", name=None,
                location=reference.anchor.area_id, qualifier=None, prefer_single_light=False,
            )
            return self._ask_reference(proposal, request, only_ids=matches)
        identifier = next(iter(matches))
        entity = home.entities[identifier]
        # The preceding action may have selected its target by a role or alias.
        # Reuse its operation/value and let the ordinary policy check the new target.
        proposal = replace(
            memory.command.proposal, kind="named", name=entity.name,
            location=reference.anchor.area_id, qualifier=None, prefer_single_light=False,
        )
        return await self._run(Command(
            proposal, selected=(identifier,), expected_targets=(identifier,),
            expected_areas=(reference.anchor.area_id,), light_side_reference=reference,
        ), request)

    def _correct_room(self, memory, pending, request, correction):
        previous = pending.proposal if pending else memory.command if memory else None
        if (
            previous is None or previous.query or previous.selected
            or previous.proposal.kind not in ROOM_LIGHT_KINDS
            or previous.proposal.action not in {Action.ON, Action.OFF}
        ):
            return Result(
                "clarify", "Powiedz, co zrobić w tym pokoju.", reason="no_reference",
            )
        words = reply_words(correction)
        if has_foreign_letters(correction) or nlu.unsafe_for_fallback(words):
            return Result("clarify", "Powtórz pełne polecenie.", reason="unclear_reply")
        home = self.model_provider()
        location = location_reply(words, home)
        matches = home.location_index.get(home.location_key(location), ()) if location else ()
        if len(matches) != 1:
            return Result(
                "clarify", "Podaj dokładną nazwę pokoju.",
                reason="ambiguous_location" if matches else "unknown_location",
            )
        kind, area = next(iter(matches))
        if kind != "area":
            return Result("clarify", "Podaj nazwę pokoju.", reason="room_correction_needs_area")
        completed_areas = pending.completed_room_areas if pending else previous.expected_areas
        if completed_areas is not None and area in completed_areas:
            return Result(
                "answered",
                "Poprzednia próba objęła już ten pokój." if request.dry_run else
                "Poprzednie polecenie objęło już ten pokój.",
                reason="room_already_in_scope",
            )
        command = Command(replace(previous.proposal, location=location))
        result = self.plan(command, request, explain=self.explain_resolution)
        if result.status not in {"ready", "confirm"}:
            if result.reason == "no_targets":
                self._light_no_targets(command, request, result)
            return result
        self._remember_confirmation(
            command, result, request,
            expires_at=pending.expires_at if pending else None,
            completed_room_areas=completed_areas,
        )
        proposal = command.proposal
        adjective = {
            "voice_main": "główne", **LIGHT_ALTERNATIVES,
        }.get(proposal.qualifier)
        nouns = " ".join(filter(None, (
            "wszystkie" if proposal.kind.startswith("all_") else None,
            "zapalone" if proposal.kind in nlu.LIT_KINDS else None,
            "światła", adjective,
        )))
        verb = "Włączyć" if proposal.action == Action.ON else "Zgasić"
        result.status = "confirm"
        result.reason = "room_correction_confirmation"
        place = correction[0].lower() + correction[1:]
        if completed_areas is not None:
            previous_note = (
                "Poprzednia próba wskazała inny pokój." if request.dry_run else
                "Gotowe."
            )
            result.speech = f"{previous_note} {verb} też {nouns} {place}?"
        else:
            result.speech = f"{verb} {nouns} {place}?"
        if (result.response_data or {}).get("light_state", {}).get("unknown"):
            result.speech += " Brakuje odczytu stanu pozostałych świateł."
        result.continue_conversation = True
        return result

    async def _answer_clarification(self, question, request, text):
        home = self.model_provider()
        if text and not has_foreign_letters(request.text) and not nlu.unsafe_for_fallback(text):
            if question.missing_area:
                if location := location_reply(text, home):
                    return await self._run(
                        Command(replace(question.command.proposal, location=location)), request
                    )
            elif len(selected := target_reply(text, question.candidates, home)) == 1:
                return await self._select_option(question, selected[0], request)
            elif not selected and preferred_light_reply(text) and question.preferred_light:
                base = question.command
                current = resolve(base.query or base.proposal, request, home, base.selected)
                if preferred_lights(current.candidates, home) != (question.preferred_light,):
                    return Result(
                        "clarify", "Zmieniła się opcja domyślna. Powtórz polecenie.",
                        reason="plan_changed",
                    )
                return await self._select_option(question, question.preferred_light, request)
        self.dialogue.put(self.dialogue.questions, request.session_key, question)
        return Result(
            "clarify",
            "Podaj nazwę pomieszczenia." if question.missing_area else
            "Podaj nazwę urządzenia lub wybierz pozycję z podanej listy.",
            reason="clarification_unresolved", continue_conversation=True,
        )

    async def _answer_numbered_choice(self, question, position, request):
        if (
            question is None
            or question.missing_area
            or question.missing_target
            or question.missing_category
        ):
            if question:
                self.dialogue.put(self.dialogue.questions, request.session_key, question)
            return Result(
                "clarify", "Podaj nazwę urządzenia lub pomieszczenia.",
                reason="no_numbered_options", continue_conversation=question is not None,
            )
        if position is None or position >= len(question.candidates):
            self.dialogue.put(self.dialogue.questions, request.session_key, question)
            return Result(
                "clarify", "Wybierz jedną z podanych pozycji albo podaj jej nazwę.",
                reason="clarification_unresolved", continue_conversation=True,
            )
        return await self._select_option(question, question.candidates[position], request)

    async def _select_option(self, question, identifier, request):
        command = replace(question.command, selected=(identifier,))
        if fingerprint(self.plan(command, request)) != question.plans[identifier]:
            return Result(
                "clarify", "Zmieniły się urządzenia lub uprawnienia. Powtórz polecenie.",
                reason="plan_changed",
            )
        choice = None
        if (
            identifier.startswith("light.") and command.proposal.action in {Action.ON, Action.OFF}
            and len(question.candidates) > 1
        ):
            choice = LightChoice(question)
        return await self._run(command, request, light_choice=choice)

    def _correct_light_choice(self, memory, pending, position, request):
        choice = pending.light_choice if pending else memory.light_choice if memory else None
        now = self.clock()
        if (
            choice is None or choice.question.expires_at <= now or position is None
            or position >= len(choice.question.candidates)
        ):
            return Result(
                "clarify", "Podaj pełne polecenie z nazwą światła.", reason="no_numbered_options",
            )
        question = choice.question
        identifier = question.candidates[position]
        if not identifier.startswith("light."):
            return Result(
                "clarify", "Podaj pełne polecenie z nazwą urządzenia.",
                reason="no_numbered_options",
            )
        command = replace(question.command, selected=(identifier,))
        result = self.plan(command, request, explain=self.explain_resolution)
        if result.status not in {"ready", "confirm"} or (
            fingerprint(result) != question.plans[identifier]
        ):
            return Result(
                "clarify", "Zmieniły się urządzenia lub uprawnienia. Powtórz polecenie.",
                reason="plan_changed",
            )
        if identifier in choice.completed_options:
            return Result(
                "answered",
                "Poprzednia próba objęła już to światło." if request.dry_run else
                "Poprzednie polecenie objęło już to światło.",
                reason="choice_already_in_scope",
            )
        targets = set(result.targets)
        if any(targets.intersection(question.plans[done][0]) for done in choice.completed_options):
            return Result(
                "clarify", "Te opcje obejmują wspólne światła. Podaj nazwę konkretnej lampy.",
                reason="overlapping_light_choice",
            )
        home = self.model_provider()
        entity = home.entities.get(identifier)
        if entity is None:
            return Result(
                "clarify", "Zmieniły się urządzenia. Powtórz polecenie.", reason="plan_changed",
            )
        name = (entity.aliases[0] if entity.aliases else entity.name)[:80]
        verb = "Włączyć" if command.proposal.action == Action.ON else "Zgasić"
        place = home.areas.get(entity.area_id)
        label = f"„{name}” ({place.name})" if place else f"„{name}”"
        if choice.completed_options:
            note = "Poprzednia próba wskazała inne światło." if request.dry_run else "Gotowe."
            result.speech = f"{note} {verb} też {label}?"
        else:
            result.speech = f"{verb} {label}?"
        self._remember_confirmation(
            command, result, request, light_choice=choice,
            expires_at=min(
                question.expires_at, pending.expires_at if pending else now + self.confirmation_ttl,
            ),
        )
        result.status = "confirm"
        result.reason = "light_choice_correction_confirmation"
        result.continue_conversation = True
        return result

    def _ask_reference(self, proposal, request, *, only_ids=None):
        purpose = proposal.kind == "purpose_light"
        reason = "no_targets" if purpose else "no_reference"
        if only_ids is not None:
            reason = "ambiguous_target"
        result = Result(
            "clarify", "Które światło masz na myśli? Podaj jego nazwę.", reason=reason,
        )
        if proposal.action not in {Action.ON, Action.OFF, Action.BRIGHTNESS}:
            return result
        home = self.model_provider()
        scope = resolve_scope(proposal, request, home)
        if scope.error:
            result.speech = "Nie udało się ustalić pomieszczenia. Powtórz pełne polecenie."
            return result
        if purpose and not scope.areas:
            result.speech = "Podaj nazwę dostępnego światła i pomieszczenia."
            return result
        command = Command(replace(proposal, kind="single_light", name=None, qualifier=None))
        candidates = [
            entity
            for entity in home.entities.values()
            if entity.domain == "light"
            and entity.exposed
            and (not scope.areas or entity.area_id in scope.areas)
            and (only_ids is None or entity.id in only_ids)
        ]
        if len(candidates) > REFERENCE_CANDIDATE_LIMIT:
            result.speech = "Podaj pełne polecenie z nazwą światła i pomieszczenia."
            return result
        plans = {}
        for entity in candidates:
            query = replace(command.proposal, kind="named", name=entity.name)
            plan = self.plan(replace(command, query=query, selected=(entity.id,)), request)
            structure = reference_structure(home, (entity.id,))
            if plan.status in {"ready", "confirm"} and structure is not None:
                plans[entity.id] = fingerprint(plan), structure
        if not plans:
            result.speech = "Brak dostępnych świateł dla tego polecenia. Podaj pełne polecenie."
            return result
        question = Turn(
            command,
            self.clock() + self.dialogue.clarification_ttl,
            candidates=tuple(sorted(plans)),
            missing_target=True,
            plans=plans,
        )
        listed = question.candidates if only_ids is not None else ()
        question_result = self._repeat_reference(question, request, listed, reason=reason)
        if purpose:
            verb = "włączyć" if proposal.action == Action.ON else "zgasić"
            question_result.speech = f"Którą lampę {verb}? Podaj jej nazwę."
        return question_result

    def _repeat_reference(self, question, request, selected=(), reason="clarification_unresolved"):
        # Repeating the question never extends its lifetime or admits new targets.
        self.dialogue.put(self.dialogue.questions, request.session_key, question)
        result = Result(
            "clarify",
            "Które światło masz na myśli? Podaj jego nazwę.",
            reason="ambiguous_target" if len(selected) > 1 else reason,
            continue_conversation=True,
        )
        if 2 <= len(selected) <= 5:
            home = self.model_provider()
            options = []
            for identifier in selected:
                entity = home.entities.get(identifier)
                if entity is None:
                    continue
                area = home.areas.get(entity.area_id)
                options.append(
                    {
                        "entity_id": identifier,
                        "name": entity.name[:80],
                        "area": area.name if area else None,
                    }
                )
            names = [f"{o['name']} ({o['area']})" if o["area"] else o["name"] for o in options]
            result.speech = "Które światło masz na myśli: " + " czy ".join(names) + "?"
            result.response_data = {"clarification_options": options}
        return result

    async def _answer_reference(self, question, request, proposal):
        text = normalize(request.text)
        if proposal is not None or nlu.unsafe_for_fallback(text):
            return self._repeat_reference(question, request)
        home = self.model_provider()
        selected = reference_reply(request.text, question.candidates, home)
        if not selected:
            return self._repeat_reference(question, request)
        query = replace(question.command.proposal, kind="named", name=text)
        # A newly added alias collision is a configuration change, not consent
        # to silently pick the old target from an otherwise ambiguous alias.
        resolution = resolve(query, request, home)
        if resolution.error == "ambiguous_target" and (
            set(resolution.candidates) - set(question.candidates)
        ):
            return Result(
                "clarify",
                "Zmieniły się urządzenia lub uprawnienia. Powtórz pełne polecenie.",
                reason="plan_changed",
            )
        # Recheck every matching choice before advertising it, including current
        # visibility. A hidden or changed option must not reappear in a question.
        for identifier in selected:
            saved, structure = question.plans[identifier]
            command = replace(
                question.command,
                query=query,
                selected=(identifier,),
                expected_targets=saved[0],
                expected_areas=saved[1],
                expected_structure=structure,
            )
            if fingerprint(self.plan(command, request)) != saved:
                return Result(
                    "clarify",
                    "Zmieniły się urządzenia lub uprawnienia. Powtórz pełne polecenie.",
                    reason="plan_changed",
                )
        if len(selected) != 1:
            return self._repeat_reference(question, request, selected)
        return await self._run(command, request)

    async def _run(self, command, request, *, light_choice=None):
        result = self.plan(command, request, explain=self.explain_resolution)
        if result.status == "confirm":
            self._remember_confirmation(command, result, request, light_choice=light_choice)
            return result
        if result.status != "ready":
            if (
                command.query is None
                and not command.selected
                and command.expected_structure is None
                and result.reason in {"no_targets", "unknown_location", "ambiguous_location"}
            ):
                background = self._ask_background(request, command.proposal, result)
                if background is not None:
                    return background
            if result.reason == "no_targets":
                if (
                    command.proposal.kind == "purpose_light"
                    and command.query is None and not command.selected
                ):
                    question = self._ask_reference(command.proposal, request)
                    if trace := getattr(result, "_resolution_trace", None):
                        question._resolution_trace = trace
                    return question
                alternative = self._ask_category(command, request)
                if alternative is not None:
                    if trace := getattr(result, "_resolution_trace", None):
                        alternative._resolution_trace = trace
                    return alternative
                self._light_no_targets(command, request, result)
            if result.reason in {"ambiguous_target", "missing_area"}:
                self._ask(command, request, result)
            return result
        current = self.plan(command, request)
        if fingerprint(result) != fingerprint(current):
            return Result(
                "clarify",
                "Zmienił się stan konfiguracji. Powtórz polecenie.",
                reason="plan_changed",
            )
        if trace := getattr(result, "_resolution_trace", None):
            current._resolution_trace = trace
        return await self._execute_and_remember(
            current, request, command, light_choice=light_choice,
        )

    def _remember_confirmation(
        self, command, result, request, *,
        expires_at=None, completed_room_areas=None, light_choice=None,
    ):
        if len(self.pending) >= 512:
            self.pending.pop(next(iter(self.pending)))
        self.pending[request.session_key] = Pending(
            command,
            fingerprint(result),
            self.clock() + self.confirmation_ttl if expires_at is None else expires_at,
            completed_room_areas=completed_room_areas,
            light_choice=light_choice,
        )

    def _background_roots(self, proposal, request, home):
        scope = resolve_scope(proposal, request, home)
        if scope.error or len(scope.areas) != 1:
            return ()
        if proposal.location is not None:
            # Even a floor containing just one room is outside this first slice.
            matches = home.location_index.get(home.location_key(proposal.location), set())
            if any(kind != "area" for kind, _ in matches):
                return ()
        entities = [
            entity
            for entity in home.entities.values()
            if entity.exposed and entity.area_id == scope.areas[0]
        ]
        if proposal.kind == "light":
            entities = [
                entity
                for entity in lights_for_command(entities, proposal.action, proposal.qualifier)
                if not entity.labels & {"voice_block", "voice_exclude_all"}
            ]
        elif proposal.kind == "single_light":
            entities = [entity for entity in entities if entity.domain == "light"]
        elif proposal.kind == "named":
            matches = named_matches(proposal, home)
            entities = [entity for entity in entities if entity.id in matches]
            if proposal.qualifier:
                entities = [
                    entity
                    for entity in entities
                    if matches_light_role(entity.labels, proposal.qualifier)
                ]
        else:
            return ()
        if len(entities) > REFERENCE_CANDIDATE_LIMIT:
            return ()
        return tuple(sorted(entity.id for entity in entities))

    @staticmethod
    def _background_question(pending, result=None, *, preview=False):
        result = result or Result("confirm", pending.background_prompt)
        result.status = "confirm"
        result.speech = pending.background_prompt
        result.reason = "background_confirmation"
        result.route = "fast"
        result.continue_conversation = not preview
        result.response_data = {
            **(result.response_data or {}),
            "background": {
                "kind": "light_prefix",
                "prefix_words": pending.background_words[0],
                "trailing_words": pending.background_words[1],
            },
        }
        return result

    def _validated_background(self, pending, request):
        command = pending.proposal
        home = self.model_provider()
        if self._background_roots(command.proposal, request, home) != command.selected:
            return None
        current = self.plan(command, request)
        return current if fingerprint(current) == pending.fingerprint else None

    def _ask_background(self, request, full_proposal=None, rejected=None):
        candidates = light_prefixes(request.text)
        if not candidates:
            return None
        # Refusing recovery must retain the complete request's existing result.
        # For a parse failure this also prevents a rejected light prefix from
        # reaching the optional, more permissive semantic fallback.
        rejected = rejected or Result(
            "unsupported", UNRECOGNIZED_COMMAND_SPEECH, reason="no_intent"
        )
        home = self.model_provider()
        # A complete known alias/location wins even if hidden, blocked or
        # ambiguous. Recovery must not shorten it into a different device.
        if full_proposal is not None and (
            (full_proposal.kind == "named" and named_matches(full_proposal, home))
            or (
                full_proposal.location is not None
                and home.location_key(full_proposal.location) in home.location_index
            )
        ):
            return None
        for candidate in candidates:
            proposal = candidate.proposal
            # Resolve without selection first, retaining every ordinary ambiguity
            # and restriction. Only a ready LOW-risk plan for lights is eligible.
            result = self.plan(Command(proposal), request, explain=self.explain_resolution)
            known_alias = proposal.kind == "named" and named_matches(proposal, home)
            if result.status != "ready" or result.risk != Risk.LOW:
                if known_alias:
                    return rejected
                continue
            if (
                not result.targets
                or any(not identifier.startswith("light.") for identifier in result.targets)
                or len(result.area_ids) != 1
            ):
                if known_alias:
                    return None
                continue
            tail = normalize(request.text).split()[-candidate.trailing_words :]
            mentions_location = any(
                home.location_key(" ".join(tail[start : start + size])) in home.location_index
                for size in range(1, min(6, len(tail)) + 1)
                for start in range(len(tail) - size + 1)
            )
            if candidate.unsafe_tail or mentions_location or has_foreign_letters(request.text):
                return rejected
            roots = self._background_roots(proposal, request, home)
            structure = reference_structure(home, roots) if roots else None
            if structure is None or not roots:
                continue
            command = Command(
                proposal,
                selected=roots,
                expected_targets=result.targets,
                expected_areas=result.area_ids,
                expected_structure=structure,
            )
            if fingerprint(self.plan(command, request)) != fingerprint(result):
                return Result("clarify", "Powtórz pełne polecenie.", reason="plan_changed")
            area = home.areas.get(result.area_ids[0])
            if area is None:
                return rejected
            noun = "światła"
            if proposal.kind == "light":
                role = proposal.qualifier
                noun = {
                    "voice_main": "główne światła",
                    "voice_task": "światła robocze",
                    "voice_ambient": "światła dekoracyjne",
                }.get(role, "główne światła" if proposal.action == Action.ON else "światła")
            elif len(roots) == 1:
                noun = f"„{home.entities[roots[0]].name[:80]}”"
            operation = "Włączyć" if proposal.action == Action.ON else "Zgasić"
            pending = Pending(
                command,
                fingerprint(result),
                self.clock() + min(self.confirmation_ttl, 30),
                background=True,
                background_prompt=f"{operation} {noun} w pomieszczeniu {area.name[:80]}?",
                background_words=(candidate.prefix_words, candidate.trailing_words),
            )
            if not request.dry_run:
                self.dialogue.discard(request.session_key)
                if len(self.pending) >= 512:
                    self.pending.pop(next(iter(self.pending)))
                self.pending[request.session_key] = pending
            return self._background_question(pending, result, preview=request.dry_run)
        return None

    def _ask_category(self, command, request):
        """Offer a configured category only when no default light is configured."""
        proposal = command.proposal
        if (
            proposal.action != Action.ON
            or proposal.kind != "light"
            or proposal.qualifier is not None
            or command.query is not None
            or command.selected
        ):
            return None
        home = self.model_provider()
        scope = resolve_scope(proposal, request, home)
        if scope.error or not scope.areas:
            return None
        if len(scope.areas) != 1:
            return Result(
                "clarify",
                "Podaj jedno pomieszczenie, aby wybrać dostępną kategorię świateł.",
                reason="missing_default_light",
            )
        area = scope.areas[0]
        if has_default_light(home, area):
            return None
        plans = {}
        for role in LIGHT_ALTERNATIVES:
            roots = category_roots(home, area, role)
            if not roots or len(roots) > REFERENCE_CANDIDATE_LIMIT:
                continue
            structure = reference_structure(home, roots)
            if structure is None:
                continue
            alternative = replace(
                command,
                proposal=replace(proposal, qualifier=role),
                selected=roots,
                category_fallback=True,
            )
            plan = self.plan(alternative, request)
            if plan.status not in {"ready", "confirm"}:
                continue
            guarded = replace(
                alternative,
                expected_targets=plan.targets,
                expected_areas=plan.area_ids,
                expected_structure=structure,
            )
            plans[role] = guarded, fingerprint(plan)
        if not plans:
            return None
        question = Turn(
            command,
            self.clock() + self.dialogue.clarification_ttl,
            candidates=tuple(plans),
            missing_category=True,
            plans=plans,
        )
        return self._repeat_category(question, request, reason="missing_default_light")

    def _repeat_category(self, question, request, reason="clarification_unresolved"):
        self.dialogue.put(self.dialogue.questions, request.session_key, question)
        categories = [LIGHT_ALTERNATIVES[role] for role in question.candidates]
        if len(categories) == 1:
            prompt = f"Włączyć światła {categories[0]}?"
        else:
            prompt = "Które włączyć: " + " czy ".join(categories) + "?"
        return Result(
            "clarify",
            "W tym pomieszczeniu nie ma ustawionych świateł głównych. " + prompt,
            reason=reason,
            continue_conversation=True,
            response_data={"light_categories": list(question.candidates)},
        )

    async def _answer_category(self, question, request):
        role = light_category_reply(request.text)
        if role is None and len(question.candidates) == 1 and is_confirmation_reply(request.text):
            role = question.candidates[0]
        if role not in question.plans:
            return self._repeat_category(question, request)
        command, saved = question.plans[role]
        if fingerprint(self.plan(command, request)) != saved:
            return Result(
                "clarify",
                "Zmieniły się urządzenia lub ustawienia świateł. Powtórz pełne polecenie.",
                reason="plan_changed",
            )
        # Selecting a category never waives an entity's separate voice_confirm.
        return await self._run(command, request)

    def _light_no_targets(self, command, request, result):
        proposal = command.proposal
        if proposal.kind != "light" or command.query is not None or command.selected:
            return
        home = self.model_provider()
        scope = resolve_scope(proposal, request, home)
        if scope.error or len(scope.areas) != 1:
            return
        accessible = any(
            entity.domain == "light"
            and entity.area_id == scope.areas[0]
            and entity.exposed
            and entity.available
            and not entity.labels & {"voice_block", "voice_exclude_all"}
            for entity in home.entities.values()
        )
        if not accessible:
            result.speech = "Nie ma dostępnych świateł, którymi mogę sterować w tym pomieszczeniu."
        elif proposal.qualifier in LIGHT_ALTERNATIVES:
            category = {"voice_task": "roboczych", "voice_ambient": "dekoracyjnych"}[
                proposal.qualifier
            ]
            result.speech = f"W tym pomieszczeniu nie ma dostępnych świateł {category}."
        elif proposal.qualifier is None and proposal.action == Action.OFF:
            lights = [
                entity
                for entity in home.entities.values()
                if entity.domain == "light"
                and entity.area_id == scope.areas[0]
                and entity.exposed
            ]
            if has_light_semantic_roles(lights):
                result.speech = (
                    "W tym pomieszczeniu nie ma świateł przypisanych do wspólnego wyłączania. "
                    "Sprawdź ustawienia oświetlenia."
                )
            else:
                result.speech = "Nie ma dostępnych świateł do wyłączenia w tym pomieszczeniu."
        elif proposal.qualifier is None and proposal.action == Action.ON:
            result.speech = (
                "W tym pomieszczeniu nie ma dostępnych świateł głównych. "
                "Wybierz światło lub sprawdź ustawienia oświetlenia."
            )

    def _ask(self, command, request, result):
        home = self.model_provider()
        resolution = resolve(command.query or command.proposal, request, home, command.selected)
        candidates = resolution.candidates
        plans = {}
        preferred = None
        if result.reason == "missing_area":
            result.speech = "W którym pomieszczeniu?"
        else:
            if not 2 <= len(candidates) <= 5:
                result.speech = "Podaj dokładną nazwę urządzenia lub pomieszczenie."
                return
            # Only advertise candidates individually eligible under the current policy.
            for identifier in candidates:
                plan = self.plan(replace(command, selected=(identifier,)), request)
                if plan.status in {"ready", "confirm", "answered"}:
                    plans[identifier] = fingerprint(plan)
            if command.proposal.action in {Action.ON, Action.OFF}:
                preferred_ids = preferred_lights(candidates, home)
                if len(preferred_ids) == 1 and preferred_ids[0] in plans:
                    preferred = preferred_ids[0]
                if trace := getattr(result, "_resolution_trace", None):
                    trace.preferred_light = preferred
                    trace.light_preferences_conflict = len(preferred_ids) > 1
            candidates = tuple(plans)
            if not candidates:
                return
            if preferred:
                candidates = (preferred,) + tuple(key for key in candidates if key != preferred)
            options = []
            for identifier in candidates:
                entity = home.entities[identifier]
                name = (entity.aliases[0] if entity.aliases else entity.name)[:80]
                area = home.areas.get(entity.area_id)
                options.append(
                    {"entity_id": identifier, "name": name, "area": area.name if area else None}
                )
            names = [f"{o['name']} ({o['area']})" if o["area"] else o["name"] for o in options]
            if preferred:
                names[0] += " (opcja domyślna)"
                result.speech = "Które urządzenie? " + " czy ".join(names) + "?"
            else:
                result.speech = "Które urządzenie masz na myśli: " + " czy ".join(names) + "?"
            result.response_data["clarification_options"] = options
        result.continue_conversation = True
        self.dialogue.put(
            self.dialogue.questions,
            request.session_key,
            Turn(
                command,
                self.clock() + self.dialogue.clarification_ttl,
                candidates=candidates,
                missing_area=result.reason == "missing_area",
                plans=plans,
                preferred_light=preferred,
            ),
        )

    async def _execute_and_remember(self, result, request, command, *, light_choice=None):
        observed = getattr(result, "_light_state_snapshot", None)
        capability = getattr(result, "_capability_report", None)
        result = await self._execute(result, request)
        if capability is not None:
            annotate_capability(result, previous=capability)
        if feedback := light_state_feedback(observed, result):
            result.speech = feedback
        if result.status == "executed" and (result.response_data or {}).get(
            "light_state", {}
        ).get("unknown"):
            result.speech = "Zgaszono zapalone światła. Brakuje odczytu stanu pozostałych."
        if (
            result.status in {"executed", "dry_run"}
            and result.targets
            and all(identifier.startswith("light.") for identifier in result.targets)
        ):
            if light_choice:
                light_choice = replace(
                    light_choice,
                    completed_options=light_choice.completed_options | frozenset(command.selected),
                )
            bound = replace(
                command,
                expected_targets=result.targets,
                expected_areas=result.area_ids,
                category_fallback=False,
            )
            self.dialogue.put(
                self.dialogue.memory,
                request.session_key,
                Turn(
                    bound,
                    self.clock() + self.dialogue.ttl,
                    light_choice=light_choice,
                    light_side_anchor=getattr(result, "_light_side_anchor", None),
                ),
            )
        return result

    async def _execute(self, result, request):
        # Only reached after a stable plan (and any required consent) was revalidated.
        mark_milestone("plan_ready")
        if request.dry_run:
            result.status, result.speech = (
                "dry_run",
                "Plan gotowy; urządzenia nie zostały zmienione.",
            )
            return result
        if self.executor is None:
            return Result("error", "Brak połączenia wykonawczego z HA.", reason="no_executor")
        with measure_stage("execution"):
            completed = []
            try:
                # Adapter MUST preflight permissions/exposure for ALL calls before any write.
                await self.executor.preflight(result.calls, request)
                for call in result.calls:
                    await self.executor.execute(call, request)
                    completed.extend(call.entity_ids)
            except Exception:
                LOGGER.warning(
                    "HA execution failed after %d target(s)", len(completed), exc_info=True
                )
                result.status = "partial" if completed else "error"
                result.speech = (
                    "Wykonano tylko część polecenia."
                    if completed
                    else ACTION_FAILED_SPEECH
                )
                result.reason = "execution_failed"
            else:
                result.status, result.speech = "executed", "Gotowe."
            result.completed = tuple(completed)
        return result
