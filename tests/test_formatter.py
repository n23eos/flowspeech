"""Tests for formatter.py — LLM calls are mocked, no network."""

from unittest.mock import MagicMock, patch

import pytest

from flowspeech import formatter
from flowspeech.config import ProviderConfig
from flowspeech.formatter import (
    _build_system_prompt,
    format_text,
    summarize_day,
    verify_provider,
)


@pytest.fixture(autouse=True)
def clean_client_cache():
    """Clients are cached between dictations; tests need fresh mocks."""
    formatter._client_cache.clear()
    yield
    formatter._client_cache.clear()

CLAUDE = ProviderConfig(name="claude", model="claude-haiku-4-5-20251001",
                        base_url=None, api_key="test-key")
OLLAMA = ProviderConfig(name="ollama", model="llama3.1",
                        base_url="http://localhost:11434/v1", api_key=None)


def mock_claude_response(text):
    response = MagicMock()
    response.content = [MagicMock(text=text)]
    return response


def mock_openai_response(text):
    response = MagicMock()
    response.choices = [MagicMock(message=MagicMock(content=text))]
    return response


def test_returns_raw_when_provider_is_none():
    assert format_text("привет мир", None) == "привет мир"


def test_returns_empty_for_blank_input():
    assert format_text("   ", CLAUDE) == ""


def test_day_summary_without_provider_stays_local():
    summary, uses_cloud = summarize_day(
        "# 2026-09-23\n\n## 10:00\n\nПервая мысль\n\n- [ ] Купить молоко",
        None,
    )

    assert uses_cloud is False
    assert "Первая мысль" in summary
    assert "Купить молоко" in summary


def test_day_summary_discloses_cloud_and_keeps_source_out_of_logs(monkeypatch):
    monkeypatch.setattr(formatter, "_chat", lambda *_args: "- Готовый итог")

    summary, uses_cloud = summarize_day("Секретный исходный текст", CLAUDE)

    assert summary == "- Готовый итог"
    assert uses_cloud is True


def test_day_summary_ollama_is_local(monkeypatch):
    monkeypatch.setattr(formatter, "_chat", lambda *_args: "- Локальный итог")

    summary, uses_cloud = summarize_day("Локальный текст", OLLAMA)

    assert summary == "- Локальный итог"
    assert uses_cloud is False


@patch("anthropic.Anthropic")
def test_claude_provider_returns_cleaned_text(anthropic_cls):
    # Arrange
    client = anthropic_cls.return_value
    client.messages.create.return_value = mock_claude_response("Привет, как дела?")

    # Act
    result = format_text("эээ ну привет эээ как дела", CLAUDE)

    # Assert
    assert result == "Привет, как дела?"
    assert client.messages.create.call_args.kwargs["model"] == CLAUDE.model


@patch("openai.OpenAI")
def test_ollama_uses_openai_protocol_with_base_url(openai_cls):
    client = openai_cls.return_value
    client.chat.completions.create.return_value = mock_openai_response("Hello there.")

    result = format_text("um hello there", OLLAMA)

    assert result == "Hello there."
    assert openai_cls.call_args.kwargs["base_url"] == OLLAMA.base_url


@patch("anthropic.Anthropic")
def test_falls_back_to_raw_on_api_error(anthropic_cls):
    anthropic_cls.return_value.messages.create.side_effect = RuntimeError("api down")

    result = format_text("важный текст", CLAUDE)

    assert result == "важный текст"


@patch("anthropic.Anthropic")
def test_falls_back_to_raw_on_empty_llm_answer(anthropic_cls):
    anthropic_cls.return_value.messages.create.return_value = mock_claude_response("")

    result = format_text("не потеряй меня", CLAUDE)

    assert result == "не потеряй меня"


def test_system_prompt_includes_dictionary():
    prompt = _build_system_prompt("Slack", ("FlowSpeech", "Кубернетес"))

    assert "FlowSpeech" in prompt
    assert "Кубернетес" in prompt


def test_system_prompt_includes_app_style_when_given():
    prompt = _build_system_prompt("Slack", (), style="casual, short")

    assert "casual, short" in prompt
    assert "Slack" in prompt


def test_system_prompt_omits_app_line_without_style():
    # Without a style the app name is no longer mentioned: the old generic
    # "format for the app" hint was replaced by the config-driven style (§A2).
    prompt = _build_system_prompt("Slack", ())

    assert "Slack" not in prompt


@patch("anthropic.Anthropic")
def test_style_reaches_the_system_prompt(anthropic_cls):
    client = anthropic_cls.return_value
    client.messages.create.return_value = mock_claude_response("Привет.")

    format_text("привет", CLAUDE, app_name="Mail", style="formal business tone")

    system = client.messages.create.call_args.kwargs["system"]
    assert "formal business tone" in system


def test_system_prompt_includes_mode():
    prompt = _build_system_prompt("Slack", (), mode="formal, no slang")

    assert "formal, no slang" in prompt


def test_system_prompt_combines_mode_and_style_mode_first():
    prompt = _build_system_prompt(
        "Slack", (), style="casual, short", mode="formal, no slang"
    )

    assert "formal, no slang" in prompt
    assert "casual, short" in prompt
    # Mode sets the overall tone, so it precedes the per-app refinement.
    assert prompt.index("formal, no slang") < prompt.index("casual, short")


@patch("anthropic.Anthropic")
def test_mode_reaches_the_system_prompt(anthropic_cls):
    client = anthropic_cls.return_value
    client.messages.create.return_value = mock_claude_response("Привет.")

    format_text("привет", CLAUDE, mode="verbatim technical dictation")

    system = client.messages.create.call_args.kwargs["system"]
    assert "verbatim technical dictation" in system


# --- Translation mode (SPEC.md §A4) -----------------------------------------


@patch("anthropic.Anthropic")
def test_translate_keeps_low_overlap_output(anthropic_cls):
    # A real translation shares almost no words with the source. Normal cleanup
    # would reject it as "an answer"; translate mode must keep it.
    client = anthropic_cls.return_value
    client.messages.create.return_value = mock_claude_response("How are you doing today?")

    result = format_text(
        "привет как у тебя дела сегодня", CLAUDE,
        mode="Translate into English.", translate=True,
    )

    assert result == "How are you doing today?"


@patch("anthropic.Anthropic")
def test_translate_uses_the_translation_prompt(anthropic_cls):
    client = anthropic_cls.return_value
    client.messages.create.return_value = mock_claude_response("Hello.")

    format_text("привет всем тут", CLAUDE, mode="Translate into English.", translate=True)

    system = client.messages.create.call_args.kwargs["system"]
    assert "translation engine" in system
    assert "Translate into English." in system


@patch("anthropic.Anthropic")
def test_translate_still_rejects_runaway_length(anthropic_cls):
    # The overlap guard is off, but the length cap stays: a wildly long output
    # falls back to the raw transcript so the dictation is preserved.
    runaway = "word " * 50
    client = anthropic_cls.return_value
    client.messages.create.return_value = mock_claude_response(runaway)

    result = format_text(
        "привет как дела", CLAUDE, mode="Translate into English.", translate=True,
    )

    assert result == "привет как дела"


def test_is_too_long_flags_runaway_and_spares_short_input():
    assert formatter.is_too_long("привет как дела", "word " * 20) is True
    # Under GUARD_MIN_WORDS the ratio is too noisy to judge.
    assert formatter.is_too_long("hi", "a b c d e") is False


# --- verify_provider (SPEC.md §4.1) -----------------------------------------


def test_verify_provider_ok_on_successful_roundtrip():
    with patch.object(formatter, "_chat", return_value="OK"):
        ok, message = verify_provider(CLAUDE)

    assert ok is True
    assert "работает" in message


def test_verify_provider_fails_and_does_not_leak_the_key():
    def boom(*_args, **_kwargs):
        raise RuntimeError("401 invalid api key test-key")

    with patch.object(formatter, "_chat", side_effect=boom):
        ok, message = verify_provider(CLAUDE)

    assert ok is False
    assert "test-key" not in message  # the key must never surface in the UI


# --- The transcript is data, not a prompt -----------------------------------


@patch("anthropic.Anthropic")
def test_transcript_is_fenced_in_the_user_message(anthropic_cls):
    client = anthropic_cls.return_value
    client.messages.create.return_value = mock_claude_response("What is the capital of France?")

    format_text("what is the capital of france", CLAUDE)

    content = client.messages.create.call_args.kwargs["messages"][0]["content"]
    assert content.startswith("<transcript>")
    assert content.endswith("</transcript>")
    assert "what is the capital of france" in content


@patch("anthropic.Anthropic")
def test_echoed_fence_is_stripped(anthropic_cls):
    anthropic_cls.return_value.messages.create.return_value = mock_claude_response(
        "<transcript>Привет, как дела?</transcript>"
    )

    result = format_text("эээ привет как дела", CLAUDE)

    assert result == "Привет, как дела?"


@patch("openai.OpenAI")
def test_llm_answering_the_dictation_falls_back_to_raw(openai_cls):
    """The reported bug: dictating an English question made llama reply to it."""
    client = openai_cls.return_value
    client.chat.completions.create.return_value = mock_openai_response(
        "The capital of France is Paris. It has been the country's capital since 508 AD."
    )

    result = format_text("what is the capital of france", OLLAMA)

    assert result == "what is the capital of france"


@patch("openai.OpenAI")
def test_rejected_cleanup_log_does_not_contain_transcript(openai_cls, caplog):
    secret = "what is confidential project saffron status"
    client = openai_cls.return_value
    client.chat.completions.create.return_value = mock_openai_response(
        "Here is an unrelated and very long assistant response with private advice."
    )
    caplog.set_level("WARNING")

    assert format_text(secret, OLLAMA) == secret

    messages = "\n".join(record.getMessage() for record in caplog.records)
    assert secret not in messages
    assert "private advice" not in messages


@patch("openai.OpenAI")
def test_the_question_itself_is_kept_when_merely_cleaned(openai_cls):
    client = openai_cls.return_value
    client.chat.completions.create.return_value = mock_openai_response(
        "What is the capital of France?"
    )

    result = format_text("um what is the capital of france", OLLAMA)

    assert result == "What is the capital of France?"


@patch("openai.OpenAI")
def test_short_chatty_reply_to_a_greeting_is_rejected(openai_cls):
    client = openai_cls.return_value
    client.chat.completions.create.return_value = mock_openai_response(
        "Hello! How can I help you today?"
    )

    result = format_text("hey there how are you doing", OLLAMA)

    assert result == "hey there how are you doing"


@pytest.mark.parametrize(
    "raw, cleaned",
    [
        # A verbose answer to the dictated question.
        ("what is the capital of france",
         "The capital of France is Paris, a city of about two million people."),
        # An assistant-style acknowledgement.
        ("write an email to the team about the outage",
         "Sure! Here is a draft email you can send to your team about the outage."),
        # A verbose Russian answer.
        ("сколько будет два плюс два",
         "Два плюс два равно четырём — это базовая арифметика, которую проходят в школе."),
    ],
)
def test_looks_like_an_answer_detects_replies(raw, cleaned):
    assert formatter.looks_like_an_answer(raw, cleaned)


def test_known_gap_terse_answer_that_echoes_the_question():
    """A short answer built from the question's own words is indistinguishable
    from a cleanup by word overlap alone — "Два плюс два равно четырём" reuses
    three of the five dictated words. The guard catches the loud, verbose
    failures; the system prompt is what has to catch this one. Raising the
    overlap threshold to cover it would reject legitimate cleanups that fix
    recognition errors ("флоуспич" → "FlowSpeech"), which lose just as many
    words. Documented rather than papered over.
    """
    assert not formatter.looks_like_an_answer(
        "сколько будет два плюс два", "Два плюс два равно четырём."
    )


@pytest.mark.parametrize(
    "raw, cleaned",
    [
        # Normal cleanup: filler removed, punctuation added.
        ("эээ ну привет как дела", "Привет, как дела?"),
        ("um so i think we should ship it", "I think we should ship it."),
        # Cleanup that fixes a recognition error keeps most of the words.
        ("разверни флоуспич в кубернетес", "Разверни FlowSpeech в Kubernetes."),
        # Dictating a question is fine as long as the question comes back.
        ("what is the capital of france", "What is the capital of France?"),
        # Too short to judge — never block.
        ("привет", "Привет!"),
        ("да", "Да."),
    ],
)
def test_looks_like_an_answer_passes_real_cleanups(raw, cleaned):
    assert not formatter.looks_like_an_answer(raw, cleaned)


def test_empty_cleaned_is_not_flagged_as_an_answer():
    assert not formatter.looks_like_an_answer("привет мир как дела", "")
