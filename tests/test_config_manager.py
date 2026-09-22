"""ConfigManager: subscribers get fresh configs; a broken reload keeps the old."""

import os
import textwrap

import pytest

from flowspeech.config import ConfigError, load_config, save_markdown_export
from flowspeech.config_manager import ConfigManager

CONFIG_TEMPLATE = textwrap.dedent(
    """
    hotkey: right_option
    whisper:
      model: small
      language: auto
      device: auto
    llm:
      provider: none
      ollama:
        model: llama3.1
        base_url: http://localhost:11434/v1
    paths:
      data_dir: {data_dir}
    """
)


@pytest.fixture
def config_file(tmp_path, monkeypatch):
    path = tmp_path / "config.yaml"
    path.write_text(CONFIG_TEMPLATE.format(data_dir=tmp_path), encoding="utf-8")
    monkeypatch.setenv("FLOWSPEECH_CONFIG", str(path))
    monkeypatch.chdir(tmp_path)  # keep ./config.yaml lookup inside the sandbox
    return path


def test_apply_keys_persists_and_notifies(config_file, tmp_path, monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    manager = ConfigManager(load_config())
    seen = []
    manager.subscribe(seen.append)

    new_config = manager.apply_keys({"GROQ_API_KEY": "gsk_fresh"})

    assert os.environ["GROQ_API_KEY"] == "gsk_fresh"
    assert (tmp_path / ".env").exists()
    assert seen == [new_config]
    assert manager.config is new_config


def test_broken_reload_keeps_previous_config(config_file):
    manager = ConfigManager(load_config())
    old = manager.config
    config_file.write_text("llm: [broken", encoding="utf-8")
    with pytest.raises(ConfigError):
        manager.reload()
    assert manager.config is old


def test_failing_subscriber_does_not_block_others(config_file):
    manager = ConfigManager(load_config())
    seen = []
    manager.subscribe(lambda cfg: (_ for _ in ()).throw(RuntimeError("boom")))
    manager.subscribe(seen.append)
    manager.reload()
    assert len(seen) == 1


def test_reload_notifies_subscribers_about_markdown_export(config_file, tmp_path):
    manager = ConfigManager(load_config())
    seen = []
    manager.subscribe(seen.append)
    destination = tmp_path / "Dictations"
    destination.mkdir()

    save_markdown_export(True, destination, config_file)
    new_config = manager.reload()

    assert new_config.markdown_export.enabled is True
    assert new_config.markdown_export.directory == destination
    assert seen == [new_config]
