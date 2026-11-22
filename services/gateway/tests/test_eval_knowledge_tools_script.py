"""``scripts/eval_knowledge_tools.py``: close its HTTP clients and name the tools it saw.

The live eval talks to a model through the gateway's shared aiohttp sessions. Leaving them open made
Python print "Unclosed client session" after the report. It also printed only ``want=``/``got=``,
which made a production failure hard to read; every case now says expected and actual tool.
"""
from __future__ import annotations

import asyncio
import importlib.util
import pathlib
import sys

import pytest

from services import config
from services.gateway import http_client, knowledge_eval
from services.gateway import main as gateway_main

SCRIPT = pathlib.Path(__file__).resolve().parents[3] / "scripts" / "eval_knowledge_tools.py"


def _load_script() -> object:
    spec = importlib.util.spec_from_file_location("eval_knowledge_tools_under_test", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def script(monkeypatch):
    module = _load_script()
    monkeypatch.setattr(sys, "argv", ["eval_knowledge_tools.py", "--model", "test-model", "--no-hub"])
    monkeypatch.setattr(config, "any_home_hub", lambda: False)
    monkeypatch.setattr(gateway_main, "select_system_instruction_for_query", lambda _q, _m: "prompt")
    monkeypatch.setattr(knowledge_eval, "check_routing", lambda **_kw: [])
    closed: list[bool] = []

    async def close() -> None:
        closed.append(True)

    monkeypatch.setattr(http_client, "close_all_clients", close)
    return module, closed


def test_each_case_names_the_expected_and_actual_tool(script, monkeypatch, capsys) -> None:
    module, closed = script

    async def run(*_args, **_kwargs):
        return {
            "cases": 2,
            "passed": 1,
            "results": [
                {"id": "weather", "ok": True, "want": "get_weather", "got": "get_weather", "why": ""},
                {"id": "forecast", "ok": False, "want": "get_forecast", "got": "get_weather",
                 "why": "answered from the wrong tool"},
            ],
        }

    monkeypatch.setattr(knowledge_eval, "run", run)
    assert asyncio.run(module.main()) == 1
    out = capsys.readouterr().out
    assert "PASS weather: expected tool 'get_weather', got 'get_weather'" in out
    assert ("FAIL forecast: expected tool 'get_forecast', got 'get_weather' "
            "(answered from the wrong tool)") in out
    assert closed == [True]


def test_the_http_clients_are_closed_even_when_the_eval_raises(script, monkeypatch) -> None:
    module, closed = script

    async def run(*_args, **_kwargs):
        raise RuntimeError("model unavailable")

    monkeypatch.setattr(knowledge_eval, "run", run)
    with pytest.raises(RuntimeError, match="model unavailable"):
        asyncio.run(module.main())
    assert closed == [True]
