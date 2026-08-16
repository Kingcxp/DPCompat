"""Plugin marketplace: remote repositories, catalog browsing, search, and install.

A repository is any static file server exposing the DPCompat plugin repository
layout: a root ``index.json`` listing categories, per-category ``INDEX.json``
files, and one folder per plugin holding the plugin file itself.  Plugin
metadata is parsed from the downloaded file with the same ``PluginStore``
inspector used for local installs, so marketplace and local plugins behave
identically (localization included).

Repository registrations live in ``~/.dpcompat/repos.toml``
(``DPCOMPAT_REPOS_FILE`` overrides).  The official repository is pre-registered
but can be removed; ``DPCOMPAT_PLUGIN_DIR`` still decides where installed
plugins land.
"""

from __future__ import annotations

import json
import os
import tomllib
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import Field

from .models import FrozenModel
from .plugins import PluginInfo, PluginStore

REPOS_FILE_ENV = "DPCOMPAT_REPOS_FILE"
DEFAULT_REPO_NAME = "official"
DEFAULT_REPO_URL = "https://raw.githubusercontent.com/Kingcxp/DPCompat-repo/main"
_FETCH_TIMEOUT = 15
_USER_AGENT = "DPCompat-market/1 (+https://github.com/Kingcxp/DPCompat)"


class MarketError(RuntimeError):
    """A repository or plugin could not be fetched or installed."""


class RepoSpec(FrozenModel):
    """One registered plugin repository."""

    name: str = Field(min_length=1, pattern=r"^[a-z0-9][a-z0-9._-]*$")
    #: Base URL of the repository file root; file paths are appended directly.
    url: str = Field(min_length=1)
    enabled: bool = True

    def base(self) -> str:
        return self.url.rstrip("/")


class CategoryInfo(FrozenModel):
    """One category declared in the repository root index."""

    id: str = Field(min_length=1)
    path: str = Field(min_length=1)
    display_name: str = ""


class RepoCatalog(FrozenModel):
    """Parsed root ``index.json`` of a repository."""

    name: str = Field(min_length=1)
    schema_version: int = Field(alias="schema")
    categories: tuple[CategoryInfo, ...] = Field(min_length=1)


class CategoryIndex(FrozenModel):
    """Parsed per-category ``INDEX.json``."""

    category: str = Field(min_length=1)
    plugins: tuple[str, ...]


class MarketPluginMeta(FrozenModel):
    """Optional ``plugin.json`` marketplace fields shown in the detail view."""

    author: str = ""
    license: str = ""
    homepage: str = ""
    tags: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class MarketPlugin:
    """A browsable plugin: DPCompat metadata plus marketplace extras."""

    info: PluginInfo
    repo: str
    category: str
    meta: MarketPluginMeta = field(default_factory=MarketPluginMeta)


# -- repository registry -------------------------------------------------------


def repos_path() -> Path:
    """Return the TOML file holding the repository registrations."""

    override = os.environ.get(REPOS_FILE_ENV)
    if override:
        return Path(override).expanduser().resolve()
    return Path.home() / ".dpcompat" / "repos.toml"


def load_repos() -> list[RepoSpec]:
    """Return the official repository plus every registered repository."""

    path = repos_path()
    repos: list[RepoSpec] = [RepoSpec(name=DEFAULT_REPO_NAME, url=DEFAULT_REPO_URL)]
    try:
        with path.open("rb") as handle:
            raw = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError):
        return repos
    table = raw.get("repo")
    if not isinstance(table, dict):
        return repos
    for name, entry in table.items():
        if not isinstance(entry, dict) or not isinstance(entry.get("url"), str):
            continue
        repos.append(
            RepoSpec(
                name=str(name),
                url=entry["url"],
                enabled=bool(entry.get("enabled", True)),
            )
        )
    return repos


def save_repos(repos: list[RepoSpec]) -> None:
    """Persist the repository registrations (the official default is not saved)."""

    path = repos_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# DPCompat plugin repositories. Managed by `dpcompat plugin repo`; edit with care.",
        "# The official repository is built in and does not need an entry here.",
    ]
    for spec in repos:
        if spec.name == DEFAULT_REPO_NAME:
            continue
        lines.append(f'[repo."{spec.name}"]')
        lines.append(f'url = "{spec.url}"')
        lines.append(f"enabled = {'true' if spec.enabled else 'false'}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def add_repo(name: str, url: str, *, replace: bool = False) -> RepoSpec:
    """Register a repository after validating that its catalog is reachable."""

    spec = RepoSpec(name=name, url=url)
    repos = load_repos()
    if any(item.name == name for item in repos) and not replace:
        raise MarketError(f"Repository {name!r} is already registered; use --replace to update it")
    try:
        fetch_catalog(spec)
    except MarketError as exc:
        raise MarketError(f"Cannot reach repository {name!r}: {exc}") from exc
    repos = [item for item in repos if item.name != name]
    repos.append(spec)
    save_repos(repos)
    return spec


def remove_repo(name: str) -> None:
    """Unregister a repository (the official default stays registered)."""

    repos = load_repos()
    remaining = [item for item in repos if item.name != name]
    if len(remaining) == len(repos):
        raise MarketError(f"Repository {name!r} is not registered")
    save_repos(remaining)


# -- fetching ------------------------------------------------------------------


def _fetch_bytes(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=_FETCH_TIMEOUT) as response:
            return bytes(response.read())
    except urllib.error.HTTPError as exc:
        raise MarketError(f"HTTP {exc.code} for {url}") from exc
    except urllib.error.URLError as exc:
        raise MarketError(f"Network error for {url}: {exc.reason}") from exc
    except TimeoutError as exc:
        raise MarketError(f"Timed out fetching {url}") from exc


def fetch_json(url: str) -> Any:
    """Fetch and parse one JSON document from a repository."""

    try:
        return json.loads(_fetch_bytes(url).decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise MarketError(f"Invalid JSON from {url}: {exc}") from exc


def fetch_catalog(repo: RepoSpec) -> RepoCatalog:
    """Fetch and validate the repository root ``index.json``."""

    raw = fetch_json(f"{repo.base()}/index.json")
    try:
        return RepoCatalog.model_validate(raw)
    except Exception as exc:
        raise MarketError(f"{repo.name}: invalid repository catalog: {exc}") from exc


def fetch_category_index(repo: RepoSpec, category: CategoryInfo) -> CategoryIndex:
    """Fetch one category's plugin list."""

    raw = fetch_json(f"{repo.base()}/{category.path}/INDEX.json")
    try:
        return CategoryIndex.model_validate(raw)
    except Exception as exc:
        raise MarketError(f"{repo.name}/{category.path}: invalid INDEX.json: {exc}") from exc


def _plugin_file_urls(repo: RepoSpec, category: CategoryInfo, plugin_id: str) -> tuple[str, str]:
    base = f"{repo.base()}/{category.path}/{plugin_id}"
    return f"{base}/{plugin_id}.py", f"{base}/{plugin_id}.json"


def _fetch_plugin_bytes(repo: RepoSpec, category: CategoryInfo, plugin_id: str) -> tuple[bytes, str]:
    """Download the plugin file; returns (bytes, suffix) preferring .py."""

    errors: list[str] = []
    for url, suffix in zip(_plugin_file_urls(repo, category, plugin_id), (".py", ".json"), strict=True):
        try:
            return _fetch_bytes(url), suffix
        except MarketError as exc:
            errors.append(str(exc))
    raise MarketError(f"{repo.name}/{plugin_id}: cannot download plugin file: {'; '.join(errors)}")


def _fetch_market_meta(repo: RepoSpec, category: CategoryInfo, plugin_id: str) -> MarketPluginMeta:
    try:
        raw = fetch_json(f"{repo.base()}/{category.path}/{plugin_id}/plugin.json")
        return MarketPluginMeta.model_validate(raw)
    except Exception:  # missing or malformed marketplace metadata is not fatal
        return MarketPluginMeta()


def inspect_plugin_file(data: bytes, suffix: str, *, source: str) -> PluginInfo:
    """Inspect a downloaded plugin file with the same machinery as local installs."""

    import tempfile

    with tempfile.TemporaryDirectory(prefix="dpcompat-market-") as temp_dir:
        path = Path(temp_dir) / f"plugin{suffix}"
        path.write_bytes(data)
        try:
            return PluginStore()._inspect_file(path)
        except ValueError as exc:
            raise MarketError(f"{source}: dpcompat rejected the plugin: {exc}") from exc


def fetch_market_plugin(repo: RepoSpec, category: CategoryInfo, plugin_id: str) -> MarketPlugin:
    """Download, inspect, and annotate one plugin from a repository."""

    data, suffix = _fetch_plugin_bytes(repo, category, plugin_id)
    info = inspect_plugin_file(data, suffix, source=f"{repo.name}/{plugin_id}")
    if info.id != plugin_id:
        raise MarketError(f"{repo.name}/{plugin_id}: plugin declares id {info.id!r}")
    return MarketPlugin(
        info=info,
        repo=repo.name,
        category=category.id,
        meta=_fetch_market_meta(repo, category, plugin_id),
    )


def _catalog_entries(repo: RepoSpec) -> list[tuple[CategoryInfo, str, PluginInfo, MarketPluginMeta]]:
    """Fetch every plugin of a repository as (category, plugin_id, info, meta)."""

    catalog = fetch_catalog(repo)
    entries: list[tuple[CategoryInfo, str, PluginInfo, MarketPluginMeta]] = []
    for category in catalog.categories:
        index = fetch_category_index(repo, category)
        for plugin_id in index.plugins:
            try:
                data, suffix = _fetch_plugin_bytes(repo, category, plugin_id)
            except MarketError:
                continue
            info = inspect_plugin_file(data, suffix, source=f"{repo.name}/{plugin_id}")
            entries.append((category, plugin_id, info, _fetch_market_meta(repo, category, plugin_id)))
    return entries


def list_categories(repos: list[RepoSpec] | None = None) -> list[CategoryInfo]:
    """Return the union of categories declared by the enabled repositories."""

    seen: dict[str, CategoryInfo] = {}
    for repo in [item for item in (repos or load_repos()) if item.enabled]:
        try:
            catalog = fetch_catalog(repo)
        except MarketError:
            continue  # an unreachable repository must not hide the others
        for category in catalog.categories:
            seen.setdefault(category.id, category)
    return list(seen.values())


def list_market_plugins(
    *,
    repos: list[RepoSpec] | None = None,
    repo_name: str | None = None,
    category: str | None = None,
    query: str | None = None,
) -> list[MarketPlugin]:
    """Browse repositories; ``query`` matches id, name, description, tags, target."""

    repos = [item for item in (repos or load_repos()) if item.enabled]
    if repo_name:
        repos = [item for item in repos if item.name == repo_name]
    needle = (query or "").strip().lower()
    results: list[MarketPlugin] = []
    for repo in repos:
        try:
            entries = _catalog_entries(repo)
        except MarketError:
            continue  # an unreachable repository must not hide the others
        for cat, plugin_id, info, meta in entries:
            if category and cat.id != category:
                continue
            if needle:
                haystack = " ".join(
                    (
                        plugin_id,
                        info.name,
                        info.description,
                        info.target_version,
                        " ".join(meta.tags),
                    )
                ).lower()
                if needle not in haystack:
                    continue
            results.append(MarketPlugin(info=info, repo=repo.name, category=cat.id, meta=meta))
    return results


def install_market_plugin(plugin_id: str, store: PluginStore, *, repo_name: str | None = None) -> PluginInfo:
    """Download and install a plugin by id from the enabled repositories."""

    repos = [item for item in load_repos() if item.enabled]
    if repo_name:
        repos = [item for item in repos if item.name == repo_name]
    errors: list[str] = []
    for repo in repos:
        try:
            plugin = _find_in_repo(repo, plugin_id)
        except MarketError as exc:
            errors.append(str(exc))
            continue
        data, suffix = _fetch_plugin_bytes(repo, plugin.cat, plugin_id)
        import tempfile

        with tempfile.TemporaryDirectory(prefix="dpcompat-market-") as temp_dir:
            path = Path(temp_dir) / f"plugin{suffix}"
            path.write_bytes(data)
            try:
                return store.install(path)
            except ValueError as exc:
                raise MarketError(f"Installing {plugin_id!r} failed: {exc}") from exc
    raise MarketError(f"Plugin {plugin_id!r} was not found in any repository: {'; '.join(errors)}")


@dataclass(frozen=True, slots=True)
class _LocatedPlugin:
    """Internal helper: where a plugin id lives inside a repository."""

    cat: CategoryInfo


def _find_in_repo(repo: RepoSpec, plugin_id: str) -> _LocatedPlugin:
    """Locate a plugin id inside one repository."""

    catalog = fetch_catalog(repo)
    for category in catalog.categories:
        index = fetch_category_index(repo, category)
        if plugin_id in index.plugins:
            return _LocatedPlugin(cat=category)
    raise MarketError(f"{repo.name}: no plugin named {plugin_id!r}")
