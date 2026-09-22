"""Load and validate config.yaml. All config objects are immutable."""

import logging
import json
import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from dotenv import load_dotenv

logger = logging.getLogger(__name__)

USER_CONFIG_PATH = Path("~/.flowspeech/config.yaml").expanduser()


def config_path() -> Path:
    """Where config.yaml lives, in priority order:

    1. $FLOWSPEECH_CONFIG (explicit override)
    2. ./config.yaml (running from the repo)
    3. ~/.flowspeech/config.yaml (running as FlowSpeech.app)

    When bundled as .app and no user config exists yet, the default
    config shipped inside the bundle is copied to ~/.flowspeech/.
    """
    env = os.environ.get("FLOWSPEECH_CONFIG")
    if env:
        return Path(env).expanduser()

    local = Path("config.yaml")
    if local.exists():
        return local

    if not USER_CONFIG_PATH.exists():
        # py2app sets RESOURCEPATH inside the bundle.
        resources = os.environ.get("RESOURCEPATH")
        bundled = Path(resources) / "config.yaml" if resources else None
        if bundled and bundled.exists():
            USER_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(bundled, USER_CONFIG_PATH)
    return USER_CONFIG_PATH

VALID_PROVIDERS = ("claude", "openai", "deepseek", "groq", "ollama", "none")
# Mirrors hotkey.HOTKEY_MAP (config.py must not import pynput).
VALID_HOTKEYS = (
    "right_option", "right_command", "right_shift", "right_control",
    "f13", "f14", "f15",
)
VALID_WHISPER_CLOUD = ("none", "groq")
VALID_WHISPER_MODELS = ("tiny", "base", "small", "medium", "large-v3")
VALID_MARKDOWN_EXPORT_MODES = ("daily", "separate")

# Which environment variable holds the API key for each provider.
# Ollama runs locally and needs no key.
PROVIDER_ENV_KEYS = {
    "claude": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
    "deepseek": "DEEPSEEK_API_KEY",
    "groq": "GROQ_API_KEY",
}


class ConfigError(Exception):
    """Raised when config.yaml is missing or invalid."""


@dataclass(frozen=True)
class WhisperConfig:
    model: str
    language: str  # "auto" or ISO code like "ru"
    device: str
    # Cloud STT: "groq" = Groq whisper-large-v3-turbo with fallback to the
    # local model on any error; "none" = always local.
    cloud: str = "none"


@dataclass(frozen=True)
class AudioConfig:
    # Extra capture time after the hotkey is released: the last audio block is
    # still inside PortAudio when the user lets go, and without this the final
    # word of every dictation is clipped.
    tail_seconds: float = 0.25


@dataclass(frozen=True)
class ProviderConfig:
    name: str
    model: str
    base_url: str | None
    api_key: str | None


@dataclass(frozen=True)
class LLMConfig:
    provider: str  # active provider name
    providers: dict[str, ProviderConfig]

    def active(self) -> ProviderConfig | None:
        if self.provider == "none":
            return None
        return self.providers[self.provider]


@dataclass(frozen=True)
class PrivateMode:
    """Private-mode state (SPEC.md §4.3), persisted so it survives restarts.

    When enabled, whisper.cloud and llm.provider in config.yaml are already
    forced to their offline values — this record only remembers what they were
    so they can be restored on disable.
    """
    enabled: bool = False
    saved_cloud: str = "none"
    saved_provider: str = "none"


@dataclass(frozen=True)
class MarkdownExportConfig:
    """An explicit user-owned destination for final Markdown dictations.

    This is separate from `paths.data_dir` and history retention: internal
    storage must never silently turn on user-visible file export.
    """

    enabled: bool = False
    directory: Path | None = None
    mode: str = "daily"


@dataclass(frozen=True)
class AppConfig:
    hotkey: str
    whisper: WhisperConfig
    llm: LLMConfig
    data_dir: Path
    audio: AudioConfig = AudioConfig()
    # Command Mode (SPEC.md §A1): hold, speak an editing command, release —
    # the selected text is rewritten in place. "" disables the feature.
    command_hotkey: str = "right_command"
    # Per-app cleanup style (SPEC.md §A2): frontmost app name → a phrase spliced
    # into the LLM cleanup prompt (e.g. Slack → "casual, short"). The "default"
    # key applies to apps not listed. Empty when the feature is unused, and
    # irrelevant when llm.provider is "none" (there is no cleanup pass at all).
    app_styles: dict[str, str] = field(default_factory=dict)
    # Cleanup modes/presets (SPEC.md §A3): display name → a prompt fragment
    # describing the overall tone (e.g. "Формальный" → "formal, no slang").
    # The empty-fragment entry ("Обычный") is the neutral default. Which mode
    # is active is app state, not config; a mode combines with app_styles —
    # the mode sets the tone, the app style refines it.
    modes: dict[str, str] = field(default_factory=dict)
    # Auto-translate presets (SPEC.md §A4): target language → translation
    # instruction. Each becomes a "Перевод → <language>" entry in the mode
    # menu. A special mode: the word-overlap guard is off and an LLM is
    # required. Combined into the same «Режим» picker as `modes`.
    translate_to: dict[str, str] = field(default_factory=dict)
    # How long dictation texts are kept (SPEC.md §4.2): 0 = don't keep texts,
    # N = keep N days, -1 = keep forever. Applied as a rotation pass on start
    # to stats.db and feedback.jsonl. Aggregate stats always survive.
    history_retention_days: int = 30
    # Private mode (SPEC.md §4.3): 100% offline. See PrivateMode.
    private_mode: PrivateMode = PrivateMode()
    markdown_export: MarkdownExportConfig = MarkdownExportConfig()

    def app_style_for(self, app_name: str) -> str:
        """Cleanup style for the frontmost app, or the "default" entry.

        Matched by case-insensitive substring in both directions so a table
        key survives however macOS reports the app: "Mail" matches the app
        reported as "Mail", and "Visual Studio Code" in the table matches the
        app reported as "Code" (and vice-versa). First match wins; the
        "default" key is never used for matching, only as the fallback.
        """
        if not self.app_styles:
            return ""
        default = self.app_styles.get("default", "")
        if not app_name:
            return default
        needle = app_name.casefold()
        for key, style in self.app_styles.items():
            if key == "default":
                continue
            folded = key.casefold()
            if folded in needle or needle in folded:
                return style
        return default


def _str_dict(raw, name: str) -> dict[str, str]:
    """Coerce an optional YAML mapping to a str→str dict; never raise.

    A missing or malformed section (someone typed a scalar instead of a
    mapping) degrades to an empty dict with a warning — an optional feature
    must not stop the app from starting.
    """
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        logger.warning("%s is not a mapping; ignoring it", name)
        return {}
    return {str(k): str(v) for k, v in raw.items()}


def _require(mapping: dict, key: str, context: str):
    if key not in mapping:
        raise ConfigError(f"Missing '{key}' in {context} section of config.yaml")
    return mapping[key]


def _build_provider(name: str, raw: dict) -> ProviderConfig:
    api_key = None
    env_key = PROVIDER_ENV_KEYS.get(name)
    if env_key:
        api_key = os.environ.get(env_key)
    return ProviderConfig(
        name=name,
        model=_require(raw, "model", f"llm.{name}"),
        base_url=raw.get("base_url"),
        api_key=api_key,
    )


def _save_config_value(key: str, value: str, path: str | Path | None = None) -> None:
    """Rewrite a single `key: value` line in place, keeping comments intact.

    Matches the key at any indentation and preserves it, so a nested key like
    `whisper.cloud` (indented under `whisper:`) is rewritten where it lives
    instead of being duplicated at the top level.
    """
    import re

    target = Path(path) if path is not None else config_path()
    text = target.read_text(encoding="utf-8")
    pattern = rf"(?m)^(?P<indent>[ \t]*){re.escape(key)}:.*$"
    if re.search(pattern, text):
        updated = re.sub(
            pattern, lambda m: f"{m.group('indent')}{key}: {value}", text, count=1
        )
    else:
        updated = text.rstrip("\n") + f"\n{key}: {value}\n"
    target.write_text(updated, encoding="utf-8")


def save_hotkey(name: str, path: str | Path | None = None) -> None:
    """Persist the hotkey choice back to config.yaml."""
    _save_config_value("hotkey", name, path)


def save_command_hotkey(name: str, path: str | Path | None = None) -> None:
    """Persist the Command Mode hotkey back to config.yaml."""
    _save_config_value("command_hotkey", name, path)


def save_whisper_cloud(value: str, path: str | Path | None = None) -> None:
    """Persist the ASR cloud engine (whisper.cloud) back to config.yaml.

    `value` is one of VALID_WHISPER_CLOUD ("none" | "groq"). The `cloud:` line
    lives indented under `whisper:`, which _save_config_value handles.
    """
    _save_config_value("cloud", value, path)


def save_provider(name: str, path: str | Path | None = None) -> None:
    """Persist the active LLM provider (llm.provider) back to config.yaml."""
    _save_config_value("provider", name, path)


def save_private_mode(
    enabled: bool,
    saved_cloud: str,
    saved_provider: str,
    path: str | Path | None = None,
) -> None:
    """Rewrite the `private_mode:` block in config.yaml (SPEC.md §4.3).

    The block is a small multi-line section, so it is replaced wholesale
    rather than line by line: any existing block (header plus its indented
    lines) is removed and a fresh one appended.
    """
    import re

    target = Path(path) if path is not None else config_path()
    text = target.read_text(encoding="utf-8")
    block = (
        "private_mode:\n"
        f"  enabled: {'true' if enabled else 'false'}\n"
        f"  saved_cloud: {saved_cloud}\n"
        f"  saved_provider: {saved_provider}\n"
    )
    # Header line plus any following indented lines belonging to the block.
    pattern = r"(?ms)^private_mode:[ \t]*\n(?:[ \t]+.*\n?)*"
    if re.search(pattern, text):
        text = re.sub(pattern, "", text, count=1)
    text = text.rstrip("\n") + "\n\n" + block
    target.write_text(text, encoding="utf-8")


def save_markdown_export(
    enabled: bool,
    directory: Path | None,
    path: str | Path | None = None,
    *,
    mode: str = "daily",
) -> None:
    """Persist the whole optional `markdown_export:` block safely.

    The destination is written as a JSON string, which is also valid YAML and
    preserves spaces and Cyrillic without relying on a hand-escaped scalar.
    """
    if enabled and directory is None:
        raise ValueError("A directory is required when Markdown export is enabled")
    if mode not in VALID_MARKDOWN_EXPORT_MODES:
        raise ValueError(f"Unsupported Markdown export mode: {mode}")

    import re

    target = Path(path) if path is not None else config_path()
    text = target.read_text(encoding="utf-8")
    directory_value = "" if directory is None else str(directory)
    block = (
        "markdown_export:\n"
        f"  enabled: {'true' if enabled else 'false'}\n"
        f"  mode: {mode}\n"
        f"  directory: {json.dumps(directory_value, ensure_ascii=False)}\n"
    )
    pattern = r"(?ms)^markdown_export:[ \t]*\n(?:[ \t]+.*\n?)*"
    if re.search(pattern, text):
        text = re.sub(pattern, "", text, count=1)
    text = text.rstrip("\n") + "\n\n" + block
    target.write_text(text, encoding="utf-8")


def load_config(path: str | Path | None = None) -> AppConfig:
    """Read config.yaml, validate it, and pull API keys from the environment."""
    load_dotenv()  # pick up keys from .env if present
    load_dotenv(USER_CONFIG_PATH.parent / ".env")  # keys next to user config (.app mode)

    resolved = Path(path) if path is not None else config_path()
    if not resolved.exists():
        raise ConfigError(f"Config file not found: {resolved.resolve()}")

    try:
        with open(resolved, encoding="utf-8") as f:
            raw = yaml.safe_load(f)
    except yaml.YAMLError as error:
        # A hand-edited config with a typo must surface as a ConfigError the
        # app can explain, not as a raw parser traceback.
        raise ConfigError(f"config.yaml is not valid YAML: {error}") from error
    if not isinstance(raw, dict):
        raise ConfigError("config.yaml is empty or not a mapping")

    whisper_raw = _require(raw, "whisper", "top-level")
    whisper_model = _require(whisper_raw, "model", "whisper")
    if whisper_model not in VALID_WHISPER_MODELS:
        raise ConfigError(
            f"whisper.model '{whisper_model}' is invalid; "
            f"choose one of {', '.join(VALID_WHISPER_MODELS)}"
        )
    whisper_cloud = whisper_raw.get("cloud", "none")
    if whisper_cloud not in VALID_WHISPER_CLOUD:
        raise ConfigError(
            f"whisper.cloud '{whisper_cloud}' is invalid; "
            f"choose one of {', '.join(VALID_WHISPER_CLOUD)}"
        )
    whisper = WhisperConfig(
        model=whisper_model,
        language=whisper_raw.get("language", "auto"),
        device=whisper_raw.get("device", "auto"),
        cloud=whisper_cloud,
    )

    llm_raw = _require(raw, "llm", "top-level")
    provider = _require(llm_raw, "provider", "llm")
    if provider not in VALID_PROVIDERS:
        raise ConfigError(
            f"llm.provider '{provider}' is invalid; "
            f"choose one of {', '.join(VALID_PROVIDERS)}"
        )

    providers = {
        name: _build_provider(name, llm_raw[name])
        for name in ("claude", "openai", "deepseek", "groq", "ollama")
        if name in llm_raw
    }
    if provider != "none" and provider not in providers:
        raise ConfigError(f"llm.provider is '{provider}' but llm.{provider} section is missing")

    active = providers.get(provider)
    if active and provider in PROVIDER_ENV_KEYS and not active.api_key:
        raise ConfigError(
            f"Provider '{provider}' needs the {PROVIDER_ENV_KEYS[provider]} "
            f"environment variable. Add it to .env or export it, "
            f"or switch llm.provider to 'ollama' / 'none'."
        )

    data_dir = Path(raw.get("paths", {}).get("data_dir", "~/.flowspeech")).expanduser()

    audio_raw = raw.get("audio") or {}
    audio = AudioConfig(tail_seconds=float(audio_raw.get("tail_seconds", 0.25)))

    hotkey = raw.get("hotkey", "right_option")

    # Optional and forgiving: a missing or malformed app_styles/modes section
    # just means "feature unused", never a startup failure. Keys and values are
    # coerced to str so a bare number in the YAML can't crash prompt building.
    app_styles = _str_dict(raw.get("app_styles"), "app_styles")
    modes = _str_dict(raw.get("modes"), "modes")
    translate_to = _str_dict(raw.get("translate_to"), "translate_to")

    # Retention is optional and forgiving: a nonsense value falls back to the
    # 30-day default rather than stopping the app from starting.
    retention_raw = raw.get("history_retention_days", 30)
    try:
        history_retention_days = int(retention_raw)
    except (TypeError, ValueError):
        logger.warning(
            "history_retention_days %r is not an integer; using 30", retention_raw
        )
        history_retention_days = 30

    pm_raw = raw.get("private_mode")
    if isinstance(pm_raw, dict):
        private_mode = PrivateMode(
            enabled=bool(pm_raw.get("enabled", False)),
            saved_cloud=str(pm_raw.get("saved_cloud", "none")),
            saved_provider=str(pm_raw.get("saved_provider", "none")),
        )
    else:
        private_mode = PrivateMode()

    export_raw = raw.get("markdown_export")
    if isinstance(export_raw, dict):
        raw_directory = export_raw.get("directory")
        directory = None
        if isinstance(raw_directory, str) and raw_directory.strip():
            directory = Path(raw_directory).expanduser()
        mode = export_raw.get("mode", "daily")
        if mode not in VALID_MARKDOWN_EXPORT_MODES:
            logger.warning("markdown_export.mode %r is invalid; using daily", mode)
            mode = "daily"
        enabled = export_raw.get("enabled") is True and directory is not None
        if export_raw.get("enabled") is True and directory is None:
            logger.warning("markdown_export enabled without a directory; disabling it")
        markdown_export = MarkdownExportConfig(
            enabled=enabled,
            directory=directory,
            mode=mode,
        )
    else:
        if export_raw is not None:
            logger.warning("markdown_export is not a mapping; disabling it")
        markdown_export = MarkdownExportConfig()

    # Command Mode is optional: a broken value must not stop dictation from
    # starting, so it degrades to "disabled" with a logged warning instead of
    # raising like the mandatory sections above.
    command_hotkey = raw.get("command_hotkey", "right_command") or ""
    if command_hotkey and command_hotkey not in VALID_HOTKEYS:
        logger.warning(
            "command_hotkey '%s' is not one of %s; Command Mode disabled",
            command_hotkey, ", ".join(VALID_HOTKEYS),
        )
        command_hotkey = ""
    if command_hotkey and command_hotkey == hotkey:
        logger.warning(
            "command_hotkey must differ from hotkey ('%s'); Command Mode disabled",
            hotkey,
        )
        command_hotkey = ""

    return AppConfig(
        hotkey=hotkey,
        whisper=whisper,
        llm=LLMConfig(provider=provider, providers=providers),
        data_dir=data_dir,
        audio=audio,
        command_hotkey=command_hotkey,
        app_styles=app_styles,
        modes=modes,
        translate_to=translate_to,
        history_retention_days=history_retention_days,
        private_mode=private_mode,
        markdown_export=markdown_export,
    )
