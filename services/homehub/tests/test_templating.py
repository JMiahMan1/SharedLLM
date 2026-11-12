"""HA's template dialect on the hub: the functions, filters and parsing Jarvis and the automations use."""
from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest

from services.homehub import templating as t
from services.homehub.core import Area, DeviceEntry, EntityEntry, Hub

NOW = datetime(2026, 11, 11, 19, 0, tzinfo=UTC)  # noon at UTC-7, a Wednesday


def clock() -> datetime:
    return NOW


@pytest.fixture
def hub() -> Hub:
    hub = Hub(time_zone="Etc/GMT+7")
    hub.areas["office"] = Area("office", "Office")
    hub.areas["kitchen"] = Area("kitchen", "Kitchen", aliases=["Cook Room"])
    hub.devices["dev1"] = DeviceEntry(id="dev1", name="Desk Lamp", area_id="office", manufacturer="WiZ",
                                      model="A19")
    hub.entities["light.desk"] = EntityEntry("light.desk", "wiz", "u1", device_id="dev1")
    hub.entities["light.kitchen"] = EntityEntry("light.kitchen", "wiz", "u2", area_id="kitchen")
    hub.entities["sensor.temp"] = EntityEntry("sensor.temp", "esphome", "u3", device_id="dev1")
    hub.states.set("light.desk", "on", {"friendly_name": "Desk Lamp", "brightness": 128})
    hub.states.set("light.kitchen", "off", {"friendly_name": "Kitchen"})
    hub.states.set("sensor.temp", "72.456", {"unit_of_measurement": "°F", "friendly_name": "Temp"})
    hub.states.set("media_player.tv", "playing", {"friendly_name": "Living Room TV", "volume_level": 0.25,
                                                   "media_title": "Song"})
    hub.states.set("media_player.speaker", "unavailable", {"friendly_name": "Speaker"})
    hub.states.set("group.lights", "on", {"entity_id": ["light.desk", "light.kitchen"]})
    hub.states.set("sensor.gone", "unknown", {})
    return hub


def r(hub: Hub, tpl: str, variables: dict | None = None) -> str:
    return t.render(hub, tpl, variables, now=clock)


def test_the_jarvis_entity_listing_renders_valid_json(hub: Hub) -> None:
    tpl = """[
  {%- for state in states %}
  {
    "eid": "{{ state.entity_id }}",
    "a": "{{ area_name(state.entity_id) or '' }}"
  }{{ "," if not loop.last }}
  {%- endfor %}
]"""
    rows = json.loads(r(hub, tpl))
    assert [x["eid"] for x in rows] == sorted(s.entity_id for s in hub.states.all())
    by = {x["eid"]: x["a"] for x in rows}
    assert by["light.desk"] == "Office"  # through its device
    assert by["light.kitchen"] == "Kitchen"  # its own area
    assert by["media_player.tv"] == ""


def test_states_is_callable_iterable_and_indexable(hub: Hub) -> None:
    assert r(hub, "{{ states('light.desk') }}") == "on"
    assert r(hub, "{{ states('light.nope') }}") == "unknown"
    assert r(hub, "{{ states('sensor.temp', with_unit=True) }}") == "72.456 °F"
    assert r(hub, "{{ states.light.desk.state }}") == "on"
    assert r(hub, "{{ states.light.nope is none }}") == "True"
    assert r(hub, "{{ states['media_player.tv'].name }}") == "Living Room TV"
    assert r(hub, "{{ states.light | list | count }}") == "2"
    assert r(hub, "{{ states.light | count }}") == "2"
    assert r(hub, "{{ states.media_player | map(attribute='entity_id') | list }}") == \
        "['media_player.speaker', 'media_player.tv']"
    assert r(hub, "{{ states.media_player | selectattr('state', 'in', ['playing', 'paused']) "
                  "| map(attribute='name') | join(',') }}") == "Living Room TV"
    assert r(hub, "{{ states.media_player | rejectattr('state', 'eq', 'unavailable') | list | length }}") == "1"


def test_state_object_exposes_has_shape(hub: Hub) -> None:
    out = r(hub, "{% set s = states.light.desk %}{{ s.entity_id }}|{{ s.domain }}|{{ s.object_id }}|{{ s.name }}|"
                 "{{ s.attributes.brightness }}|{{ s.attributes.get('missing', 'x') }}|"
                 "{{ s.last_changed is datetime }}|{{ s.last_updated.year }}")
    assert out == f"light.desk|light|desk|Desk Lamp|128|x|True|{datetime.now(UTC).year}"


def test_state_helpers(hub: Hub) -> None:
    assert r(hub, "{{ is_state('light.desk', 'on') }}") == "True"
    assert r(hub, "{{ is_state('light.desk', ['off', 'on']) }}") == "True"
    assert r(hub, "{{ is_state('light.nope', 'on') }}") == "False"
    assert r(hub, "{{ state_attr('media_player.tv', 'volume_level') }}") == "0.25"
    assert r(hub, "{{ state_attr('media_player.nope', 'volume_level') }}") == "None"
    assert r(hub, "{{ is_state_attr('light.desk', 'brightness', 128) }}") == "True"
    assert r(hub, "{{ has_value('light.desk') }} {{ has_value('sensor.gone') }} {{ has_value('x.y') }}") == \
        "True False False"
    assert r(hub, "{{ 'light.desk' is is_state('on') }}") == "True"
    assert r(hub, "{{ 'light.desk' | is_state('on') }}") == "True"


def test_registry_helpers(hub: Hub) -> None:
    assert r(hub, "{{ area_name('light.desk') }}") == "Office"
    assert r(hub, "{{ area_name('kitchen') }}") == "Kitchen"
    assert r(hub, "{{ area_id('Office') }}") == "office"
    assert r(hub, "{{ area_id('light.kitchen') }}") == "kitchen"
    assert r(hub, "{{ area_id('Cook Room') }}") == "kitchen"
    assert r(hub, "{{ area_entities('Office') | sort }}") == "['light.desk', 'sensor.temp']"
    assert r(hub, "{{ areas() | sort }}") == "['kitchen', 'office']"
    assert r(hub, "{{ device_id('light.desk') }}") == "dev1"
    assert r(hub, "{{ device_id('Desk Lamp') }}") == "dev1"
    assert r(hub, "{{ device_attr('light.desk', 'manufacturer') }}") == "WiZ"
    assert r(hub, "{{ device_attr(device_id('light.desk'), 'model') }}") == "A19"
    assert r(hub, "{{ 'light.desk' | area_name }}") == "Office"


def test_time_functions_use_the_hub_zone_and_the_given_clock(hub: Hub) -> None:
    assert r(hub, "{{ now().hour }} {{ now().tzinfo }}") == "12 Etc/GMT+7"
    assert r(hub, "{{ utcnow().hour }}") == "19"
    assert r(hub, "{{ today_at('07:30').isoformat() }}") == "2026-11-11T07:30:00-07:00"
    assert r(hub, "{{ now() > today_at('07:30') }}") == "True"
    assert r(hub, "{{ as_timestamp('2026-11-11T19:00:00+00:00') }}") == str(NOW.timestamp())
    assert r(hub, "{{ as_timestamp('bad', 0) }}") == "0"
    assert r(hub, "{{ as_datetime('2026-11-11T19:00:00+00:00').hour }}") == "19"
    assert r(hub, "{{ as_local(as_datetime('2026-11-11T19:00:00+00:00')).hour }}") == "12"
    assert r(hub, "{{ (now() - timedelta(hours=2)).hour }}") == "10"
    assert r(hub, "{{ relative_time(now() - timedelta(hours=3)) }}") == "3 hours"
    assert r(hub, "{{ relative_time(now() - timedelta(minutes=1)) }}") == "1 minute"
    ts = NOW.timestamp()
    assert r(hub, f"{{{{ {ts} | timestamp_custom('%a %H:%M') }}}}") == "Wed 12:00"
    assert r(hub, f"{{{{ {ts} | timestamp_custom('%H', false) }}}}") == "19"
    assert r(hub, "{{ as_timestamp('2026-11-12T12:00:00') | timestamp_custom('%a') }}") == "Thu"


def test_number_filters_follow_ha(hub: Hub) -> None:
    assert r(hub, "{{ '5' | int + 1 }}") == "6"
    assert r(hub, "{{ 'x' | int(-1) }}") == "-1"
    assert r(hub, "{{ '3.9' | int }}") == "3"
    assert r(hub, "{{ int('7') }}") == "7"
    with pytest.raises(t.TemplateError, match="int got invalid input"):
        r(hub, "{{ 'x' | int }}")
    assert r(hub, "{{ states('sensor.temp') | float | round(1) }}") == "72.5"
    assert r(hub, "{{ 2.5 | round }}") == "3"  # half away from zero, not banker's
    assert r(hub, "{{ 2.45 | round(1, 'floor') }}") == "2.4"
    assert r(hub, "{{ 'x' | float(0) }}") == "0"
    assert r(hub, "{{ 'on' | bool }} {{ 'no' | bool }} {{ 'maybe' | bool(None) }}") == "True False None"
    assert r(hub, "{{ is_number('4.2') }} {{ 'x' is is_number }}") == "True False"
    assert r(hub, "{{ [3, 1, 2] | min }} {{ max(4, 9) }}") == "1 9"


def test_iif_expand_json_regex_slugify(hub: Hub) -> None:
    assert r(hub, "{{ iif(is_state('light.desk', 'on'), 'yes', 'no') }}") == "yes"
    assert r(hub, "{{ (1 > 2) | iif('a', 'b') }}") == "b"
    assert r(hub, "{{ iif(None, 'a', 'b', 'n') }}") == "n"
    assert r(hub, "{{ expand('group.lights') | map(attribute='entity_id') | list }}") == \
        "['light.desk', 'light.kitchen']"
    assert r(hub, "{{ expand('group.lights') | selectattr('state', 'eq', 'on') | map(attribute='name') "
                  "| list }}") == "['Desk Lamp']"
    assert r(hub, "{{ expand(states.light, 'light.desk') | count }}") == "2"
    assert r(hub, "{{ ('{\"a\": [1, 2]}' | from_json).a[1] }}") == "2"
    assert json.loads(r(hub, "{{ {'b': 1, 'a': 'é'} | to_json(sort_keys=True) }}")) == {"a": "é", "b": 1}
    assert r(hub, "{{ 'Hello World' | regex_match('hello', ignorecase=True) }}") == "True"
    assert r(hub, "{{ 'a1b22' | regex_replace('[0-9]+', '#') }}") == "a#b#"
    assert r(hub, "{{ 'abc' is match('a') }}") == "True"
    assert r(hub, "{{ 'Kid Work - Tire Shop!' | slugify }}") == "kid_work_tire_shop"
    assert r(hub, "{{ slugify('Café Lights') }}") == "cafe_lights"


def test_namespace_and_loops_as_the_cyd_script_writes_them(hub: Hub) -> None:
    tpl = ("{% set play = states.media_player | selectattr('state','in',['playing','paused']) | list %}"
           "{% set out = namespace(items=[]) %}{% for s in play %}{% set nm = s.name %}"
           "{% set out.items = out.items + [{'e': s.entity_id, 'n': (nm if nm|length <= 12 else nm[:10] ~ '..')}] %}"
           "{% endfor %}{{ out.items }}")
    assert t.render_complex(hub, tpl, {}, now=clock) == [{"e": "media_player.tv", "n": "Living Roo.."}]


def test_render_complex_parses_native_types_like_ha(hub: Hub) -> None:
    value = {"n": "{{ 3 }}", "f": "{{ '0.4' }}", "lead": "{{ '0123' }}", "s": "{{ 'text' }}",
             "b": "{{ true }}", "l": "{{ [1, 'a'] }}", "d": "{{ {'k': 1} }}", "plain": "kept",
             "nested": [{"x": "{{ states('light.desk') }}"}], 5: "{{ 1 + 1 }}",
             "{{ 'key' }}": "templated key", "none": "{{ none }}"}
    out = t.render_complex(hub, value, {}, now=clock)
    assert out == {"n": 3, "f": 0.4, "lead": "0123", "s": "text", "b": True, "l": [1, "a"], "d": {"k": 1},
                   "plain": "kept", "nested": [{"x": "on"}], 5: 2, "key": "templated key", "none": None}


def test_variables_and_trigger_style_dicts(hub: Hub) -> None:
    trig = {"event": {"data": {"entity_id": "timer.assist_timer1"}}}
    assert r(hub, "{{ trigger.event.data.entity_id[:18] == 'timer.assist_timer' }}", {"trigger": trig}) == "True"
    pl = {"items": [{"name": "Mix", "uri": "lib://1"}]}
    assert r(hub, "{{ pl['items'][0]['name'] if pl['items']|length > 0 else '' }}", {"pl": pl}) == "Mix"
    assert r(hub, "{{ pl is defined }}") == "False"
    assert r(hub, "{{ settings.get('a') }}", {"settings": {"a": 1}}) == "1"


def test_render_strips_and_leaves_plain_strings_alone(hub: Hub) -> None:
    assert r(hub, "  plain  ") == "plain"
    assert r(hub, "  {{ 1 }}  ") == "1"
    assert t.render_bool(hub, "{{ is_state('light.desk', 'on') }}", now=clock)
    assert not t.render_bool(hub, "{{ 0 }}", now=clock)


def test_errors_and_the_sandbox(hub: Hub) -> None:
    with pytest.raises(t.TemplateError):
        r(hub, "{{ states('x' }}")
    with pytest.raises(t.TemplateError):
        r(hub, "{{ ().__class__.__bases__[0].__subclasses__() }}")
    with pytest.raises(t.TemplateError):
        r(hub, "{% set x = [1] %}{{ x.append(2) }}")  # immutable sandbox, as in HA
    # Private attributes are invisible to templates (the sandbox renders them as undefined).
    assert r(hub, "{{ states.light.desk._state }}") == ""
    with pytest.raises(t.TemplateError):
        r(hub, "{{ undefined_thing.attr }}")


def test_references_now() -> None:
    assert t.references_now("{{ now().hour > 5 }}")
    assert not t.references_now("{{ states('x.y') }}")


def test_template_states_compare_by_value(hub: Hub) -> None:
    a = t.wrap_state(hub, hub.states.get("light.desk"))
    b = t.wrap_state(hub, hub.states.get("light.desk"))
    assert a == b
    hub.states.set("light.desk", "off", {})
    assert t.wrap_state(hub, hub.states.get("light.desk")) != a
    assert r(hub, "{{ a != b }}", {"a": a, "b": t.wrap_state(hub, hub.states.get("light.desk"))}) == "True"


def test_time_zone_falls_back_to_utc_for_a_bad_zone() -> None:
    hub = Hub(time_zone="Not/AZone")
    assert t.render(hub, "{{ now().tzinfo }}", now=lambda: NOW) == "UTC"
    assert t.render(hub, "{{ (now() + timedelta(days=1)).day }}", now=lambda: NOW + timedelta(0)) == "12"
