from dataclasses import replace

import pytest

from custom_components.polish_context_voice.core.capabilities import plan_capability
from custom_components.polish_context_voice.core.home_model import Area, Entity, HomeModel
from custom_components.polish_context_voice.core.nlu.capabilities import parse_capability
from custom_components.polish_context_voice.core.types import Request


@pytest.mark.parametrize(
    "text,operation",
    [
        ("Która jest godzina?", "time"),
        ("Jaki dzisiaj dzień?", "date"),
        ("Czy okna są otwarte w salonie?", "state"),
        ("Które drzwi są otwarte", "state"),
        ("Które światła są włączone", "state"),
        ("Jaki jest stan lampa przy stole", "state"),
        ("Jaka jest wilgotność", "sensor"),
        ("Wstrzymaj muzykę", "media_pause"),
        ("Wznów muzykę w kuchni", "media_play"),
        ("Następny utwór", "media_next_track"),
        ("Poprzedni utwór", "media_previous_track"),
        ("Głośniej", "volume_up"),
        ("Ciszej", "volume_down"),
        ("Ustaw głośność na 30 procent w salonie", "volume_set"),
        ("Wycisz muzykę", "mute"),
        ("Przywróć dźwięk", "unmute"),
        ("Dodaj Żółty ser do listy zakupów", "todo_add"),
        ("Przeczytaj listę zakupów", "todo_read"),
        ("Co jest na liście Zadania", "todo_read"),
        ("Jaka jest pogoda", "weather"),
        ("Jaka będzie pogoda jutro", "forecast"),
        ("Czy jutro będzie padać", "forecast"),
        ("Co mam jutro w kalendarzu", "calendar"),
        ("Co mam dzisiaj w kalendarzu Rodzina", "calendar"),
        ("Uruchom scenę Kino", "routine"),
        ("Uruchom rutynę Dobranoc", "routine"),
        ("Ogłoś w kuchni: Obiad gotowy", "announce"),
        ("Ogłoś w kuchni że obiad gotowy", "announce"),
        ("Zapytaj w kuchni czy obiad jest gotowy", "ask"),
    ],
)
def test_whole_sentence_capabilities(text, operation):
    cap = parse_capability(text)
    assert cap is not None and cap.operation == operation


@pytest.mark.parametrize(
    "text",
    [
        "Nie wstrzymuj muzyki",
        "Wstrzymaj muzykę i włącz światła",
        "Głośniej ale nie teraz",
        "Która godzina wyłącz światła",
        "Jeśli jutro będzie padać zamknij okno",
        "Ustaw głośność na minus 5",
        "Odlicz dziesięć sekund",
        "Dodaj 10 sekund do minutnika",
        "Zapal światła",
        "nie dodaj mleko do zakupów",
        "a" * 601,
    ],
)
def test_unknown_or_compound_is_not_trimmed(text):
    assert parse_capability(text) is None


def entity(domain="media_player", area="living_room", **kwargs):
    return Entity(
        f"{domain}.test",
        "Odtwarzacz",
        area,
        exposed=True,
        attributes={"supported_features": 65535},
        **kwargs,
    )


def home(*entities):
    return HomeModel(
        [Area("living_room", "Living Room", ("salon",)), Area("kitchen", "Kitchen", ("kuchnia",))],
        entities=entities,
        satellites={"assist_satellite.origin": "living_room"},
    )


def plan(text, *entities, **kwargs):
    return plan_capability(parse_capability(text), Request(text, **kwargs), home(*entities))


def test_media_explicit_area_and_default_source():
    living = entity()
    kitchen = replace(living, id="media_player.kitchen", area_id="kitchen")
    result = plan("wstrzymaj muzykę w kuchni", living, kitchen, source_area="living_room")
    assert result.targets == (kitchen.id,) and result.status == "confirm"
    assert result.calls[0].service == "media_pause"
    assert plan("głośniej", living).reason == "missing_area"
    assert plan("ciszej", living, satellite_id="assist_satellite.origin").targets == (living.id,)


@pytest.mark.parametrize(
    "changes,reason",
    [
        ({"exposed": False}, "no_targets"),
        ({"available": False}, "unavailable"),
        ({"labels": frozenset({"voice_block"})}, "no_targets"),
        ({"labels": frozenset({"voice_high_risk"})}, "high_risk_disabled"),
        ({"attributes": {"supported_features": 0}}, "capability"),
        (
            {
                "attributes": {
                    "supported_features": 65535,
                    "group_members": ["media_player.test", "media_player.hidden"],
                }
            },
            "group_unsupported",
        ),
    ],
)
def test_media_guards(changes, reason):
    result = plan("wstrzymaj muzykę", replace(entity(), **changes), source_area="living_room")
    assert result.reason == reason and not result.calls


def test_media_ambiguity_and_primary():
    a, b = entity(), replace(entity(), id="media_player.other")
    assert plan("ciszej", a, b, source_area="living_room").reason == "ambiguous_target"
    b = replace(b, labels=frozenset({"voice_primary"}))
    assert plan("ciszej", a, b, source_area="living_room").targets == (b.id,)
    assert plan("ustaw głośność na 101 procent", b, source_area="living_room").reason == "range"


def test_named_media_alias_overrides_primary_without_hiding_conflicts():
    a = replace(entity(), aliases=("sony",))
    b = replace(
        entity(), id="media_player.other", name="Other", labels=frozenset({"voice_primary"})
    )
    result = plan("ciszej na odtwarzaczu sony", a, b, source_area="living_room")
    assert result.targets == (a.id,)
    assert parse_capability("czy okna są otwarte na odtwarzaczu sony") is None
    conflict = replace(b, aliases=("sony",))
    assert (
        plan("ciszej na odtwarzaczu sony", a, conflict, source_area="living_room").reason
        == "ambiguous_target"
    )


def test_shopping_opt_in_literal_payload_and_global_selection():
    todo = entity("todo", None, labels=frozenset({"voice_shopping"}))
    cap = parse_capability("Dodaj Żółty ser i chleb do listy zakupów")
    assert cap.payload == "Żółty ser i chleb"
    result = plan_capability(cap, Request("x", source_area="living_room"), home(todo))
    assert result.calls[0].data == {"item": "Żółty ser i chleb"}
    assert result.targets == (todo.id,)
    assert plan("dodaj mleko do zakupów", replace(todo, labels=frozenset())).reason == "no_targets"


def test_named_list_alias_conflict_is_not_hidden_by_primary():
    a = entity("todo", None, aliases=("zadania",), labels=frozenset({"voice_primary"}))
    b = replace(a, id="todo.second", labels=frozenset())
    assert plan("przeczytaj listę zadania", a, b).reason == "ambiguous_target"


def test_state_unknown_is_retained_but_hidden_excluded():
    a = entity("binary_sensor", device_class="window", state="on")
    b = replace(a, id="binary_sensor.unknown", state="unavailable", available=False)
    hidden = replace(a, id="binary_sensor.hidden", exposed=False)
    result = plan("czy okna są otwarte", a, b, hidden, source_area="living_room")
    assert set(result.targets) == {a.id, b.id}


def test_routines_require_explicit_approval_label():
    script = entity("script", None, aliases=("dobranoc",))
    assert plan("uruchom rutynę dobranoc", script).reason == "routine_not_approved"
    script = replace(script, labels=frozenset({"voice_routine"}))
    assert plan("uruchom rutynę dobranoc", script).calls[0].service == "turn_on"


def test_satellite_destination_and_source_guards():
    satellite = entity(
        "assist_satellite",
        "kitchen",
        labels=frozenset({"voice_announce"}),
        state="idle",
        device_id="kitchen_device",
    )
    result = plan(
        "zapytaj w kuchni czy obiad gotowy", satellite, satellite_id="assist_satellite.origin"
    )
    assert result.status == "confirm" and result.calls[0].service == "ask_question"
    assert (
        plan("ogłoś w kuchni: test", satellite, device_id="kitchen_device").reason
        == "same_satellite"
    )
    assert (
        plan("ogłoś w kuchni: test", replace(satellite, state="listening")).reason
        == "satellite_busy"
    )


def test_global_provider_ambiguity_does_not_choose_by_order():
    a = entity("weather", None)
    b = replace(a, id="weather.second")
    assert plan("jaka jest pogoda", a, b).reason == "ambiguous_target"
    assert plan("jaka jest pogoda", a, replace(b, labels=frozenset({"voice_primary"}))).targets == (
        b.id,
    )


@pytest.mark.parametrize(
    "text,operation,day",
    [
        ("Jaka dziś pogoda?", "weather", 0),
        ("Jaka dzisiaj pogoda?", "weather", 0),
        ("Jaka jest dziś pogoda?", "weather", 0),
        ("Jaka dzisiaj jest pogoda?", "weather", 0),
        ("Jaka pogoda dziś?", "weather", 0),
        ("Jaka pogoda jest dzisiaj?", "weather", 0),
        ("Jaka jest pogoda dzisiaj?", "weather", 0),
        ("Jaka mamy pogode?", "weather", 0),
        ("Jaką mamy dziś pogodę?", "weather", 0),
        ("Jaką dzisiaj mamy pogodę?", "weather", 0),
        ("Jaką mamy pogodę dzisiaj?", "weather", 0),
        ("Proszę, jaka dziś pogoda?", "weather", 0),
        ("Jaka jutro pogoda?", "forecast", 1),
        ("Jaka jutro będzie pogoda?", "forecast", 1),
        ("Jaka będzie jutro pogoda?", "forecast", 1),
        ("Jaka pogoda będzie jutro?", "forecast", 1),
        ("Jaka będzie pogoda jutro?", "forecast", 1),
        ("Jaka będzie dziś pogoda?", "forecast", 0),
        ("Jaka dziś będzie pogoda?", "forecast", 0),
        ("Jaka będzie pogoda dzisiaj?", "forecast", 0),
        ("Jaka będzie pogoda?", "forecast", 0),
        ("Czy dziś będzie padać?", "forecast", 0),
        ("Czy będzie dzisiaj padać?", "forecast", 0),
        ("Czy będzie padać dzisiaj?", "forecast", 0),
        ("Czy będzie padać?", "forecast", 0),
        ("Czy jutro będzie padać?", "forecast", 1),
        ("Czy będzie jutro padać?", "forecast", 1),
        ("Czy będzie padać jutro?", "forecast", 1),
    ],
)
def test_weather_word_order_and_forecast_date(text, operation, day):
    cap = parse_capability(text)
    assert cap is not None
    assert (cap.operation, cap.domain, cap.day) == (operation, "weather", day)


@pytest.mark.parametrize(
    "text",
    [
        "Jaka dziś pogoda i włącz światła",
        "Jaka dziś pogoda? Wyłącz lampę.",
        "Nie pytam jaka dziś pogoda",
        "Jaka dziś pogoda w Warszawie",
        "Jaka pogoda pojutrze",
        "Jaka wczoraj była pogoda",
        "Jaka dzisiaj pogoda jutro",
        "Czy dziś będzie padać jutro",
        "Czy jutro będzie padać albo dzisiaj",
        "Jeśli będzie dziś padać, zamknij rolety",
    ],
)
def test_weather_does_not_discard_location_dates_or_extra_commands(text):
    assert parse_capability(text) is None


def test_weather_natural_phrase_still_requires_exposed_unambiguous_provider():
    weather = entity("weather", None)
    assert plan("Jaka dziś pogoda?", replace(weather, exposed=False)).reason == "no_targets"
    assert (
        plan("Jaka dziś pogoda?", weather, replace(weather, id="weather.other")).reason
        == "ambiguous_target"
    )
    assert plan("Jaka dziś pogoda?", weather, source_area="living_room").targets == (weather.id,)


@pytest.mark.parametrize(
    "text,expected",
    [
        (
            "Jaka jest pogoda?",
            "Nie mogę teraz sprawdzić pogody. Dane pogodowe są niedostępne.",
        ),
        ("Jaka będzie jutro pogoda?", "Nie mam teraz dostępu do prognozy pogody."),
        (
            "Kiedy przestanie padać?",
            "Nie mogę teraz sprawdzić, kiedy przestanie padać. Prognoza jest niedostępna.",
        ),
    ],
)
@pytest.mark.parametrize("mode", ["missing", "unexposed", "blocked", "unavailable", "unknown"])
def test_weather_missing_data_explains_unavailable_answer(text, expected, mode):
    weather = entity("weather", None)
    changes = {
        "unexposed": {"exposed": False},
        "blocked": {"labels": frozenset({"voice_block"})},
        "unavailable": {"available": False, "state": "unavailable"},
        "unknown": {"available": False, "state": "unknown"},
    }
    entities = () if mode == "missing" else (replace(weather, **changes[mode]),)
    result = plan(text, *entities, satellite_id="assist_satellite.origin")
    assert result.status == "clarify" and result.route == "ha_native"
    assert result.speech == expected
    assert result.reason == ("unavailable" if mode in {"unavailable", "unknown"} else "no_targets")
    assert not result.calls


@pytest.mark.parametrize("text", ["Jaka jest pogoda?", "Jaka będzie jutro pogoda?"])
def test_weather_ambiguous_provider_explains_choice_instead_of_asking_for_room(text):
    weather = entity("weather", None)
    result = plan(text, weather, replace(weather, id="weather.other"))
    assert result.status == "clarify" and result.reason == "ambiguous_target"
    assert result.speech == "Mam kilka źródeł pogody i nie wiem, którego użyć."
    assert not result.calls and not result.targets


def test_unavailable_primary_weather_does_not_silently_switch_provider():
    primary = entity(
        "weather", None, available=False, state="unavailable", labels=frozenset({"voice_primary"})
    )
    other = replace(primary, id="weather.other", available=True, state="sunny", labels=frozenset())
    result = plan("Jaka jest pogoda?", primary, other)
    assert result.reason == "unavailable" and result.targets == (primary.id,)
    assert result.speech == "Nie mogę teraz sprawdzić pogody. Dane pogodowe są niedostępne."
    assert not result.calls


@pytest.mark.parametrize(
    "text",
    [
        "Kiedy przestanie padać?",
        "O której przestanie padać?",
        "Kiedy przestanie padać deszcz?",
        "Do której będzie padać?",
    ],
)
def test_rain_end_question_is_native_weather(text):
    cap = parse_capability(text)
    assert (cap.operation, cap.domain, cap.kind) == ("weather", "weather", "rain_end")


@pytest.mark.parametrize(
    "text",
    [
        "Kiedy przestanie padać w Warszawie?",
        "Kiedy przestanie padać i włącz światła",
        "Nie pytam kiedy przestanie padać",
    ],
)
def test_rain_end_never_discards_unknown_suffix(text):
    assert parse_capability(text) is None
