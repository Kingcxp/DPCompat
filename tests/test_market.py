"""Tests for the plugin marketplace client against a local HTTP repository."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest
from dpcompat import market
from dpcompat.plugins import PluginStore

from helpers import repo_server

_PLUGIN_PY = '''"""demo.alpha: fixture plugin for marketplace tests."""

from dpcompat.migrations.base import MigrationContext, RuleResult, crosses
from dpcompat.models import Compatibility, MigrationRecord, PackFormat

PLUGIN = {
    "id": "demo.alpha@88",
    "name": "演示 Alpha 插件",
    "description": "Marketplace test fixture.",
    "version": "1.0.0",
    "target_version": "1.21.9",
    "readme": "# Demo Alpha\\n\\nReadme.",
    "localizations": {
        "en": {
            "name": "Demo Alpha EN",
            "description": "English description.",
            "readme": "## English readme",
        }
    },
    "official_sources": ["https://www.minecraft.net/en-us/article/minecraft-java-edition-1-21-9"],
}


class AlphaRule:
    id = "demo.alpha.rule@88"
    boundary = PackFormat(88)
    priority = 450

    def applies(self, source, target):
        return crosses(source, target, self.boundary)

    def apply(self, context):
        return RuleResult(MigrationRecord(self.id, Compatibility.LOSSLESS, 0))


RULES = (AlphaRule(),)
'''

_INDEX_JSON = {
    "name": "test-repo",
    "schema": 1,
    "categories": [
        {"id": "1.21.9", "path": "1.21.9", "display_name": "1.21.9 / 1.21.10"}
    ],
}


def _build_repo_tree(root: Path) -> None:
    (root / "index.json").write_text(json.dumps(_INDEX_JSON), encoding="utf-8")
    category = root / "1.21.9"
    category.mkdir()
    (category / "INDEX.json").write_text(
        json.dumps({"category": "1.21.9", "plugins": ["demo.alpha@88"]}),
        encoding="utf-8",
    )
    plugin = category / "demo.alpha@88"
    plugin.mkdir()
    (plugin / "demo.alpha@88.py").write_text(_PLUGIN_PY, encoding="utf-8")
    (plugin / "plugin.json").write_text(
        json.dumps({"author": "Tests", "license": "MIT", "tags": ["fixture"]}),
        encoding="utf-8",
    )


@pytest.fixture()
def repo_server_url(tmp_path: Path) -> Iterator[str]:
    """Serve a fixture repository over HTTP; returns its base URL."""

    _build_repo_tree(tmp_path)
    with repo_server(tmp_path) as base:
        yield base


@pytest.fixture()
def repos_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "repos.toml"
    monkeypatch.setenv(market.REPOS_FILE_ENV, str(path))
    return path


def _only(monkeypatch: pytest.MonkeyPatch, repo_spec: market.RepoSpec) -> None:
    """Replace the repository registry with a single repository (no network)."""

    monkeypatch.setattr(market, "load_repos", lambda: [repo_spec])


# -- repository registry -------------------------------------------------------


def test_repo_add_list_remove_round_trip(repo_server_url: str, repos_file: Path) -> None:
    spec = market.add_repo("mine", repo_server_url)
    assert spec.name == "mine"
    assert market.load_repos()[-1].name == "mine"
    with pytest.raises(market.MarketError, match="already registered"):
        market.add_repo("mine", repo_server_url)
    market.add_repo("mine", repo_server_url, replace=True)  # --replace updates the URL
    assert market.load_repos()[-1].url == repo_server_url

    market.remove_repo("mine")
    assert all(item.name != "mine" for item in market.load_repos())
    with pytest.raises(market.MarketError, match="not registered"):
        market.remove_repo("mine")


def test_add_repo_rejects_unreachable_url(repos_file: Path) -> None:
    with pytest.raises(market.MarketError, match="Cannot reach repository"):
        market.add_repo("broken", "http://127.0.0.1:1")  # nothing listens on port 1


def test_load_repos_defaults_to_official(repos_file: Path) -> None:
    repos = market.load_repos()
    assert repos[0].name == market.DEFAULT_REPO_NAME
    assert repos[0].url == market.DEFAULT_REPO_URL


# -- catalog / search ----------------------------------------------------------


def test_list_market_plugins_finds_plugins(repo_server_url: str, monkeypatch: pytest.MonkeyPatch) -> None:
    _only(monkeypatch, market.RepoSpec(name="test", url=repo_server_url))
    plugins = market.list_market_plugins()
    assert len(plugins) == 1
    plugin = plugins[0]
    assert plugin.info.id == "demo.alpha@88"
    assert plugin.info.target_version == "1.21.9"
    assert plugin.repo == "test"
    assert plugin.category == "1.21.9"
    assert plugin.meta.author == "Tests"
    assert plugin.meta.tags == ("fixture",)


def test_market_search_matches_id_name_and_description(repo_server_url: str, monkeypatch: pytest.MonkeyPatch) -> None:
    _only(monkeypatch, market.RepoSpec(name="test", url=repo_server_url))
    assert [p.info.id for p in market.list_market_plugins(query="alpha")] == ["demo.alpha@88"]
    assert [p.info.id for p in market.list_market_plugins(query="演示")] == ["demo.alpha@88"]
    assert [p.info.id for p in market.list_market_plugins(query="fixture")] == ["demo.alpha@88"]
    assert market.list_market_plugins(query="nothing-matches") == []


def test_market_plugin_localization_resolves(repo_server_url: str, monkeypatch: pytest.MonkeyPatch) -> None:
    _only(monkeypatch, market.RepoSpec(name="test", url=repo_server_url))
    plugin = market.list_market_plugins()[0]
    assert plugin.info.localized("en").name == "Demo Alpha EN"
    assert plugin.info.localized("zh-CN").name == "演示 Alpha 插件"


def test_list_categories_unions_repositories(repo_server_url: str, monkeypatch: pytest.MonkeyPatch) -> None:
    _only(monkeypatch, market.RepoSpec(name="test", url=repo_server_url))
    categories = market.list_categories()
    assert [category.id for category in categories] == ["1.21.9"]


def test_unreachable_repo_does_not_hide_others(repo_server_url: str, monkeypatch: pytest.MonkeyPatch) -> None:
    _only(monkeypatch, market.RepoSpec(name="test", url=repo_server_url))
    monkeypatch.setattr(
        market,
        "load_repos",
        lambda: [
            market.RepoSpec(name="broken", url="http://127.0.0.1:1"),
            market.RepoSpec(name="test", url=repo_server_url),
        ],
    )
    plugins = market.list_market_plugins()
    assert [p.info.id for p in plugins] == ["demo.alpha@88"]
    assert [c.id for c in market.list_categories()] == ["1.21.9"]


# -- install -------------------------------------------------------------------


def test_install_from_market_lands_in_the_plugin_store(
    repo_server_url: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _only(monkeypatch, market.RepoSpec(name="test", url=repo_server_url))
    plugin_dir = tmp_path / "plugins"
    monkeypatch.setenv("DPCOMPAT_PLUGIN_DIR", str(plugin_dir))
    store = PluginStore()

    info = market.install_market_plugin("demo.alpha@88", store, repo_name="test")
    assert info.id == "demo.alpha@88"
    assert "demo.alpha@88" in {item.id for item in store.list_plugins()}
    assert (plugin_dir / "demo.alpha@88.py").is_file()

    with pytest.raises(market.MarketError, match="not found"):
        market.install_market_plugin("does-not-exist@1", store)


# -- CLI -----------------------------------------------------------------------


def _run_cli(argv: list[str]) -> int:
    from dpcompat.cli import run_application

    return run_application(argv)


def test_cli_repo_add_list_remove(repo_server_url: str, repos_file: Path, capsys: pytest.CaptureFixture) -> None:
    assert _run_cli(["plugin", "repo", "add", "mine", repo_server_url]) == 0
    assert _run_cli(["plugin", "repo", "list", "--json"]) == 0
    out = capsys.readouterr().out
    assert '"mine"' in out
    assert _run_cli(["plugin", "repo", "remove", "mine"]) == 0


def test_cli_market_list_show_install(
    repo_server_url: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture,
) -> None:
    _only(monkeypatch, market.RepoSpec(name="test", url=repo_server_url))
    monkeypatch.setenv("DPCOMPAT_PLUGIN_DIR", str(tmp_path / "plugins"))

    assert _run_cli(["plugin", "market", "list", "--json"]) == 0
    out = capsys.readouterr().out
    assert "demo.alpha@88" in out
    assert "Alpha" in out

    assert _run_cli(["plugin", "market", "show", "demo.alpha@88"]) == 0
    out = capsys.readouterr().out
    assert "演示 Alpha 插件" in out
    assert "作者 Tests" in out  # marketplace metadata is rendered

    assert _run_cli(["plugin", "market", "install", "demo.alpha@88"]) == 0
    assert "demo.alpha@88" in {item.id for item in PluginStore().list_plugins()}


def test_cli_market_show_unknown_plugin_fails(
    repo_server_url: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    _only(monkeypatch, market.RepoSpec(name="test", url=repo_server_url))
    assert _run_cli(["plugin", "market", "show", "missing@1"]) == 2
    assert "No plugin named" in capsys.readouterr().err
