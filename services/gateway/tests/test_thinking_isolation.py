import json
import pytest
from services.gateway.llm_providers import (
    StreamingThinkingFilter,
    extract_thinking_and_content,
    strip_thinking_blocks,
)
from services.gateway.main import (
    _make_openai_chunk,
    _make_ollama_chunk,
    _make_openai_response,
    _make_ollama_response,
)


def test_streaming_thinking_filter_clean_split():
    """Verify that StreamingThinkingFilter properly separates thinking from clean content."""
    filt = StreamingThinkingFilter()

    # Feed chunks
    c1, t1 = filt.process_with_thinking("<think>Step 1: calculate 2+2")
    assert c1 == ""
    assert t1 == "Step 1: calculate 2+2"

    c2, t2 = filt.process_with_thinking(" = 4.</think>The answer is 4.")
    assert t2 == " = 4."
    assert c2 == "The answer is 4."

    c3, t3 = filt.process_with_thinking(" Have a nice day!")
    assert t3 == ""
    assert c3 == " Have a nice day!"

    tail_c, tail_t = filt.flush_both()
    assert tail_c == ""
    assert tail_t == ""


def test_streaming_thinking_filter_partial_tags():
    """Verify that partial opening and closing tags are buffered across chunk boundaries."""
    filt = StreamingThinkingFilter()

    c1, t1 = filt.process_with_thinking("Hello <thi")
    assert c1 == "Hello "
    assert t1 == ""

    c2, t2 = filt.process_with_thinking("nk>internal thoughts</thi")
    assert c2 == ""
    assert t2 == "internal thoughts"

    c3, t3 = filt.process_with_thinking("nk>world!")
    assert t3 == ""
    assert c3 == "world!"


def test_extract_thinking_and_content():
    """Verify that extract_thinking_and_content isolates reasoning and answer."""
    raw = "<think>Planning to turn on the lamp</think>The lamp is now on."
    thinking, content = extract_thinking_and_content(raw)
    assert thinking == "Planning to turn on the lamp"
    assert content == "The lamp is now on."

    # Multiple think tags
    raw2 = "<think>Part 1</think>Middle<think>Part 2</think>End"
    thinking2, content2 = extract_thinking_and_content(raw2)
    assert "Part 1" in thinking2 and "Part 2" in thinking2
    assert content2 == "MiddleEnd"


def test_openai_and_ollama_chunk_structure():
    """Verify chunk schemas conform to OpenAI (delta.reasoning_content) and Ollama (message.thinking)."""
    # OpenAI content chunk
    chunk_ai_content = _make_openai_chunk("Hello", "jarvis")
    assert chunk_ai_content["choices"][0]["delta"]["content"] == "Hello"
    assert "reasoning_content" not in chunk_ai_content["choices"][0]["delta"]

    # OpenAI reasoning chunk
    chunk_ai_reasoning = _make_openai_chunk("", "jarvis", reasoning_content="Thinking step")
    assert chunk_ai_reasoning["choices"][0]["delta"]["reasoning_content"] == "Thinking step"
    assert "content" not in chunk_ai_reasoning["choices"][0]["delta"]

    # Ollama content chunk
    chunk_ol_content = _make_ollama_chunk("Hello", "jarvis")
    assert chunk_ol_content["message"]["content"] == "Hello"
    assert "thinking" not in chunk_ol_content["message"]

    # Ollama reasoning chunk
    chunk_ol_reasoning = _make_ollama_chunk("", "jarvis", thinking="Thinking step")
    assert chunk_ol_reasoning["message"]["thinking"] == "Thinking step"
    assert chunk_ol_reasoning["message"]["content"] == ""


def test_openai_and_ollama_non_streaming_response():
    """Verify non-streaming responses properly isolate thinking and never pollute content."""
    resp_ai = _make_openai_response("Final answer", "jarvis", thinking="Internal reason")
    data_ai = json.loads(resp_ai.body.decode("utf-8"))
    msg_ai = data_ai["choices"][0]["message"]
    assert msg_ai["content"] == "Final answer"
    assert msg_ai["reasoning_content"] == "Internal reason"

    resp_ol = _make_ollama_response("Final answer", "jarvis", thinking="Internal reason")
    data_ol = json.loads(resp_ol.body.decode("utf-8"))
    msg_ol = data_ol["message"]
    assert msg_ol["content"] == "Final answer"
    assert msg_ol["thinking"] == "Internal reason"
