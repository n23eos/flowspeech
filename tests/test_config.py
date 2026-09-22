"""Tests for config loading and validation."""

import pytest

from flowspeech.config import (
    ConfigError,
    load_config,
    save_markdown_export,
    save_hotkey,
    save_journal_hotkey,
    save_private_mode,
    save_provider,
    save_whisper_cloud,
)

CLOUD_PROVIDERS = {"claude", "openai", "deepseek", "groq"}

VALID_YAML = """
hotkey: right_option
whisper:
  model: small
  language: auto
llm:
  provider: ollama
  claude:
    model: claude-haiku-4-5-20251001
  ollama:
    model: llama3.1
    base_url: http://localhost:11434/v1
paths:
  data_dir: ~/.flowspeech
"""


def write_config(tmp_path, text):
    path = tmp_path / "config.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def test_loads_valid_config(tmp_path):
    # Arrange
    path = write_config(tmp_path, VALID_YAML)

    # Act
    config = load_config(path)

    # Assert
    assert config.hotkey == "right_option"
    assert config.whisper.model == "small"
    assert config.llm.provider == "ollama"
    assert config.llm.active().base_url == "http://localhost:11434/v1"
    assert config.data_dir.name == ".flowspeech"


def test_missing_file_raises_clear_error(tmp_path):
    with pytest.raises(ConfigError, match="not found"):
        load_config(tmp_path / "nope.yaml")


def test_invalid_provider_rejected(tmp_path):
    path = write_config(tmp_path, VALID_YAML.replace("provider: ollama", "provider: grok"))

    with pytest.raises(ConfigError, match="grok"):
        load_config(path)


def test_invalid_whisper_model_rejected(tmp_path):
    path = write_config(tmp_path, VALID_YAML.replace("model: small", "model: huge", 1))

    with pytest.raises(ConfigError, match="huge"):
        load_config(path)


def test_cloud_provider_without_api_key_raises(tmp_path, monkeypatch):
    # Arrange: claude selected but no ANTHROPIC_API_KEY in environment
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    path = write_config(tmp_path, VALID_YAML.replace("provider: ollama", "provider: claude"))

    # Act / Assert
    with pytest.raises(ConfigError, match="ANTHROPIC_API_KEY"):
        load_config(path)


def test_save_hotkey_updates_value_and_keeps_comments(tmp_path):
    # Arrange: config with a comment on the hotkey line
    yaml_with_comment = VALID_YAML.replace(
        "hotkey: right_option", "hotkey: right_option  # push-to-talk"
    )
    path = write_config(tmp_path, yaml_with_comment)

    # Act
    save_hotkey("f13", path)

    # Assert: value changed, rest of the file untouched
    config = load_config(path)
    assert config.hotkey == "f13"
    assert "llm:" in path.read_text(encoding="utf-8")


def test_journal_hotkey_loads_and_rejects_collision(tmp_path):
    path = write_config(tmp_path, VALID_YAML + "\njournal_hotkey: f13\n")
    assert load_config(path).journal_hotkey == "f13"

    save_journal_hotkey("right_option", path)

    assert load_config(path).journal_hotkey == ""


def test_save_whisper_cloud_rewrites_nested_line_in_place(tmp_path):
    yaml_text = (
        "whisper:\n"
        "  model: small\n"
        "  cloud: none  # локально\n"
        "llm:\n"
        "  provider: none\n"
    )
    path = write_config(tmp_path, yaml_text)

    save_whisper_cloud("groq", path)

    text = path.read_text(encoding="utf-8")
    # Indentation preserved and the top level untouched (no duplicate key).
    assert "  cloud: groq" in text
    assert "\ncloud:" not in text
    assert load_config(path).whisper.cloud == "groq"


def test_provider_none_needs_no_key(tmp_path, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    path = write_config(tmp_path, VALID_YAML.replace("provider: ollama", "provider: none"))

    config = load_config(path)

    assert config.llm.active() is None


# --- Markdown export --------------------------------------------------------


def test_markdown_export_defaults_to_disabled(tmp_path):
    config = load_config(write_config(tmp_path, VALID_YAML))

    assert config.markdown_export.enabled is False
    assert config.markdown_export.directory is None
    assert config.markdown_export.mode == "daily"
    assert config.markdown_export.structure == "flat"
    assert config.markdown_export.template == "# {date}\n\n"
    assert config.markdown_export.voice_prefixes is False
    assert config.markdown_export.live_preview is False


def test_markdown_export_loads_enabled_directory(tmp_path):
    export_dir = tmp_path / "Dictations"
    export_dir.mkdir()
    path = write_config(
        tmp_path,
        VALID_YAML + f"\nmarkdown_export:\n  enabled: true\n  directory: {export_dir}\n",
    )

    config = load_config(path)

    assert config.markdown_export.enabled is True
    assert config.markdown_export.directory == export_dir


def test_malformed_markdown_export_is_safely_disabled(tmp_path):
    config = load_config(write_config(tmp_path, VALID_YAML + "\nmarkdown_export: 42\n"))

    assert config.markdown_export.enabled is False
    assert config.markdown_export.directory is None


def test_enabled_markdown_export_without_directory_is_disabled(tmp_path):
    config = load_config(
        write_config(tmp_path, VALID_YAML + "\nmarkdown_export:\n  enabled: true\n")
    )

    assert config.markdown_export.enabled is False


def test_history_retention_zero_does_not_enable_markdown_export(tmp_path):
    config = load_config(write_config(tmp_path, VALID_YAML + "\nhistory_retention_days: 0\n"))

    assert config.history_retention_days == 0
    assert config.markdown_export.enabled is False


def test_save_markdown_export_rewrites_only_its_block(tmp_path):
    export_dir = tmp_path / "Заметки"
    export_dir.mkdir()
    path = write_config(tmp_path, VALID_YAML + "\n# keep this comment\n")

    save_markdown_export(True, export_dir, path)

    config = load_config(path)
    assert config.markdown_export.enabled is True
    assert config.markdown_export.directory == export_dir
    assert config.markdown_export.mode == "daily"
    assert "# keep this comment" in path.read_text(encoding="utf-8")


def test_markdown_export_rejects_unknown_mode(tmp_path):
    config = load_config(
        write_config(
            tmp_path,
            VALID_YAML + "\nmarkdown_export:\n  enabled: true\n  mode: other\n  directory: /tmp\n",
        )
    )

    assert config.markdown_export.mode == "daily"


def test_save_markdown_export_persists_separate_mode(tmp_path):
    export_dir = tmp_path / "Dictations"
    export_dir.mkdir()
    path = write_config(tmp_path, VALID_YAML)

    save_markdown_export(True, export_dir, path, mode="separate")

    assert load_config(path).markdown_export.mode == "separate"


def test_markdown_export_loads_structure_and_template(tmp_path):
    export_dir = tmp_path / "Dictations"
    export_dir.mkdir()
    path = write_config(tmp_path, VALID_YAML)

    save_markdown_export(
        True,
        export_dir,
        path,
        structure="year_month",
        template="# Daily {date}\n\n",
        voice_prefixes=True,
        live_preview=True,
    )

    loaded = load_config(path).markdown_export
    assert loaded.structure == "year_month"
    assert loaded.template == "# Daily {date}\n\n"
    assert loaded.voice_prefixes is True
    assert loaded.live_preview is True


# --- app_styles (SPEC.md §A2) ------------------------------------------------

APP_STYLES_YAML = VALID_YAML + """
app_styles:
  Slack: "casual, short"
  Mail: "formal business tone"
  "Visual Studio Code": "verbatim"
  default: "neutral"
"""


def test_app_styles_loaded_from_yaml(tmp_path):
    config = load_config(write_config(tmp_path, APP_STYLES_YAML))

    assert config.app_styles["Slack"] == "casual, short"


def test_app_style_matches_case_insensitively(tmp_path):
    config = load_config(write_config(tmp_path, APP_STYLES_YAML))

    assert config.app_style_for("slack") == "casual, short"
    assert config.app_style_for("Mail") == "formal business tone"


def test_app_style_matches_substring_both_directions(tmp_path):
    config = load_config(write_config(tmp_path, APP_STYLES_YAML))

    # Table key longer than the reported name ("Code") and vice-versa.
    assert config.app_style_for("Code") == "verbatim"
    assert config.app_style_for("Slack (2)") == "casual, short"


def test_app_style_falls_back_to_default(tmp_path):
    config = load_config(write_config(tmp_path, APP_STYLES_YAML))

    assert config.app_style_for("Terminal") == "neutral"
    assert config.app_style_for("") == "neutral"


def test_app_style_empty_when_no_section(tmp_path):
    config = load_config(write_config(tmp_path, VALID_YAML))

    assert config.app_styles == {}
    assert config.app_style_for("Slack") == ""


def test_app_styles_malformed_section_ignored(tmp_path):
    bad = VALID_YAML + "\napp_styles: not-a-mapping\n"
    config = load_config(write_config(tmp_path, bad))

    assert config.app_styles == {}
    assert config.app_style_for("Slack") == ""


# --- modes (SPEC.md §A3) -----------------------------------------------------

MODES_YAML = VALID_YAML + """
modes:
  Обычный: ""
  Формальный: "formal, no slang"
  Код: "verbatim technical dictation"
"""


def test_modes_loaded_from_yaml(tmp_path):
    config = load_config(write_config(tmp_path, MODES_YAML))

    assert config.modes["Формальный"] == "formal, no slang"
    assert config.modes["Обычный"] == ""


def test_modes_empty_when_no_section(tmp_path):
    config = load_config(write_config(tmp_path, VALID_YAML))

    assert config.modes == {}


def test_modes_malformed_section_ignored(tmp_path):
    bad = VALID_YAML + "\nmodes: 42\n"
    config = load_config(write_config(tmp_path, bad))

    assert config.modes == {}


# --- translate_to (SPEC.md §A4) ----------------------------------------------

TRANSLATE_YAML = VALID_YAML + """
translate_to:
  English: "Translate into English."
  Русский: "Translate into Russian."
"""


def test_translate_to_loaded_from_yaml(tmp_path):
    config = load_config(write_config(tmp_path, TRANSLATE_YAML))

    assert config.translate_to["English"] == "Translate into English."
    assert "Русский" in config.translate_to


def test_translate_to_empty_when_no_section(tmp_path):
    config = load_config(write_config(tmp_path, VALID_YAML))

    assert config.translate_to == {}


# --- history_retention_days (SPEC.md §4.2) -----------------------------------


def test_history_retention_defaults_to_30(tmp_path):
    config = load_config(write_config(tmp_path, VALID_YAML))

    assert config.history_retention_days == 30


def test_history_retention_reads_value(tmp_path):
    yaml_text = VALID_YAML + "\nhistory_retention_days: 0\n"
    config = load_config(write_config(tmp_path, yaml_text))

    assert config.history_retention_days == 0


def test_history_retention_invalid_falls_back_to_30(tmp_path):
    yaml_text = VALID_YAML + "\nhistory_retention_days: forever\n"
    config = load_config(write_config(tmp_path, yaml_text))

    assert config.history_retention_days == 30


# --- Private mode (SPEC.md §4.3) ---------------------------------------------

PRIVATE_YAML = VALID_YAML + """
private_mode:
  enabled: true
  saved_cloud: groq
  saved_provider: claude
"""


def test_private_mode_parsed(tmp_path):
    config = load_config(write_config(tmp_path, PRIVATE_YAML))

    assert config.private_mode.enabled is True
    assert config.private_mode.saved_cloud == "groq"
    assert config.private_mode.saved_provider == "claude"


def test_private_mode_defaults_off(tmp_path):
    config = load_config(write_config(tmp_path, VALID_YAML))

    assert config.private_mode.enabled is False


def test_private_mode_config_has_no_active_cloud_provider(tmp_path):
    # The acceptance invariant: while private mode is on, the loaded config
    # exposes no cloud provider as active and no cloud ASR.
    config = load_config(write_config(tmp_path, PRIVATE_YAML))

    assert config.whisper.cloud == "none"
    assert config.llm.provider not in CLOUD_PROVIDERS
    active = config.llm.active()
    assert active is None or active.name not in CLOUD_PROVIDERS


def test_save_private_mode_roundtrip(tmp_path):
    path = write_config(tmp_path, VALID_YAML)

    save_private_mode(True, "groq", "claude", path)
    config = load_config(path)

    assert config.private_mode.enabled is True
    assert config.private_mode.saved_cloud == "groq"
    assert config.private_mode.saved_provider == "claude"
    # llm.provider is untouched by save_private_mode (still ollama).
    assert config.llm.provider == "ollama"


def test_save_private_mode_replaces_existing_block(tmp_path):
    path = write_config(tmp_path, PRIVATE_YAML)

    save_private_mode(False, "none", "none", path)
    text = path.read_text(encoding="utf-8")

    assert text.count("private_mode:") == 1  # no duplicate block
    assert load_config(path).private_mode.enabled is False


def test_save_provider_updates_nested_line(tmp_path):
    path = write_config(tmp_path, VALID_YAML)

    save_provider("none", path)

    assert load_config(path).llm.provider == "none"
