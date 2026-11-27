"""Bounded, source-local presentation memory; never creates or executes a plan."""

from time import monotonic

from .response_facts import capability_speech

ACKNOWLEDGEMENTS = ("Gotowe.", "OK.", "Zrobione.", "Już.", "Służę.")


class Presentation:
    def __init__(self, clock=monotonic):
        self.clock = clock
        self.acks = {}
        self.fact_replies = {}
        self.retries = {}

    def _prune(self, values):
        now = self.clock()
        for key, (_, expires) in list(values.items()):
            if expires <= now:
                values.pop(key, None)

    def _store(self, values, key, value, ttl):
        if key not in values and len(values) >= 512:
            values.pop(next(iter(values)))
        values[key] = (value, self.clock() + ttl)

    def retry_timer(self, result, request):
        """Offer one new listen for an unparsed timer, without storing an action."""
        self._prune(self.retries)
        if request.dry_run or not (request.satellite_id or request.device_id):
            return
        if result.status == "ignored":
            return
        key = request.session_key
        if result.status == "unsupported" and result.reason == "timer_no_match":
            if key not in self.retries:
                self._store(self.retries, key, True, 30)
                result.continue_conversation = True
            else:
                result.speech = "Nadal nie rozumiem polecenia minutnika."
        else:
            self.retries.pop(key, None)

    def acknowledgement(self, speech, result, request):
        """Vary audible factual replies and generic complete-action acknowledgements."""
        self._prune(self.acks)
        self._prune(self.fact_replies)
        source = request.satellite_id or request.device_id or request.conversation_id
        if (
            speech
            and result.status in {"executed", "answered"}
            and not request.dry_run
            and source
            and speech == capability_speech(result)
        ):
            # A factual response has its own sequence. Silence, previews and
            # other sources cannot consume it or fall into the generic ACKs.
            key = (request.user_id, source, result._capability_report.id)
            previous = self.fact_replies.get(key, (-1, 0))[0]
            index = (previous + 1) % 3
            self._store(self.fact_replies, key, index, 600)
            return capability_speech(result, index)
        if (
            speech != "Gotowe."
            or request.dry_run
            or result.status != "executed"
            or not result.targets
            or set(result.targets) != set(result.completed)
        ):
            return speech
        # Persist across consecutive wake-word sessions on the same satellite.
        # Another room/user cannot advance this room's response sequence.
        if not source:
            return speech
        key = (request.user_id, source)
        previous = self.acks.get(key, (-1, 0))[0]
        index = (previous + 1) % len(ACKNOWLEDGEMENTS)
        self._store(self.acks, key, index, 600)
        return ACKNOWLEDGEMENTS[index]
