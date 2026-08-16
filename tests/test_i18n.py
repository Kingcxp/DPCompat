"""Unit tests for UI localization: lookup, fallback, and preference persistence."""

from __future__ import annotations

from pathlib import Path

import pytest
from dpcompat import i18n


def test_tr_resolves_and_falls_back() -> None:
    assert i18n.tr("zh-CN", "picker.cancel") == "取消"
    assert i18n.tr("en", "picker.cancel") == "Cancel"
    # Unknown languages fall back to the default language, then to the key itself.
    assert i18n.tr("fr", "picker.cancel") == "取消"
    assert i18n.tr("zh-CN", "no.such.key") == "no.such.key"


def test_tr_formats_kwargs() -> None:
    assert i18n.tr("en", "plugins.installed_notify", name="Demo", id="demo@88") == "Installed plugin: Demo (demo@88)"
    assert i18n.tr("zh-CN", "plugins.installed_notify", name="演示", id="demo@88") == "已安装插件：演示 (demo@88)"


def test_every_registered_language_has_translations() -> None:
    missing: list[str] = []
    for key, entry in i18n.TRANSLATIONS.items():
        for language in i18n.LANGUAGES:
            if language not in entry:
                missing.append(f"{key}[{language}]")
    assert missing == []


def test_language_preferences_round_trip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    prefs_file = tmp_path / "prefs.toml"
    monkeypatch.setattr(i18n, "PREFS_FILE", prefs_file)
    assert i18n.load_preferred_language() == i18n.DEFAULT_LANGUAGE
    i18n.save_preferred_language("en")
    assert prefs_file.is_file()
    assert i18n.load_preferred_language() == "en"


def test_env_language_overrides_prefs_and_explicit_wins(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(i18n, "PREFS_FILE", tmp_path / "prefs.toml")
    monkeypatch.delenv(i18n.ENV_LANGUAGE, raising=False)
    i18n.save_preferred_language("en")
    assert i18n.resolve_language() == "en"
    monkeypatch.setenv(i18n.ENV_LANGUAGE, "zh-CN")
    assert i18n.resolve_language() == "zh-CN"
    assert i18n.resolve_language("en") == "en"  # explicit --lang beats the environment


def test_save_preferred_language_rejects_unknown_codes() -> None:
    with pytest.raises(ValueError, match="Unknown language"):
        i18n.save_preferred_language("de")
