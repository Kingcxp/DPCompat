"""Generate GitHub Wiki pages from the repository documentation.

The release and wiki workflows run this script to produce a staging
directory (default ``.wiki/``) containing Home.md, the key developer docs
as wiki pages, and a _Sidebar.md.  The workflows then clone the wiki
repository, copy the staged files over it, and push.

Local preview:

    uv run python scripts/sync_wiki.py --output .wiki
"""

from __future__ import annotations

import argparse
import re
import shutil
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]

# (project-relative source path, wiki page name).  Page names are ASCII so
# sidebar links stay stable, while the content stays in the original language.
PAGES: tuple[tuple[str, str], ...] = (
    ("README.md", "Home.md"),
    ("plugin-development/PLUGIN_DEVELOPMENT.zh-CN.md", "Plugin-Development.md"),
    ("docs/ADDING_A_NEW_VERSION.zh-CN.md", "Adding-A-New-Version.md"),
    ("docs/ARCHITECTURE.zh-CN.md", "Architecture.md"),
    ("docs/RULE_AUTHORING.zh-CN.md", "Rule-Authoring.md"),
    ("docs/SAFETY_MODEL.zh-CN.md", "Safety-Model.md"),
    ("docs/RELEASE_CHECKLIST.zh-CN.md", "Release-Checklist.md"),
    ("CHANGELOG.md", "Changelog.md"),
)

_LINK_TARGETS = {source: page for source, page in PAGES}
_LINK_RE = re.compile(r"\]\(([^)]+)\)")

SIDEBAR = """## 快速导航

* [首页](Home)
* [插件开发](Plugin-Development)
* [添加新版本](Adding-A-New-Version)
* [架构说明](Architecture)
* [规则编写](Rule-Authoring)
* [安全模型](Safety-Model)
* [发布清单](Release-Checklist)
* [更新日志](Changelog)
"""


def _rewrite_links(text: str) -> str:
    """Point markdown links to shipped pages at their wiki page names.

    Both ``docs/X.zh-CN.md``/``plugin-development/X.zh-CN.md`` and bare
    ``X.zh-CN.md`` references resolve to the wiki page; links to anything
    else are left untouched.
    """

    def replace(match: re.Match[str]) -> str:
        target = match.group(1)
        candidates = (target, target.removeprefix("docs/"), target.removeprefix("plugin-development/"))
        for candidate in candidates:
            if candidate in _LINK_TARGETS:
                return f"]({_LINK_TARGETS[candidate].removesuffix('.md')})"
        return match.group(0)

    return _LINK_RE.sub(replace, text)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=PROJECT_ROOT / ".wiki", help="staging directory")
    args = parser.parse_args(argv)
    output = args.output
    if output.exists():
        shutil.rmtree(output)
    output.mkdir(parents=True)
    for source, page in PAGES:
        path = PROJECT_ROOT / source
        if not path.is_file():
            print(f"sync_wiki: missing source {source}", file=sys.stderr)
            return 1
        (output / page).write_text(_rewrite_links(path.read_text(encoding="utf-8")), encoding="utf-8")
    (output / "_Sidebar.md").write_text(SIDEBAR, encoding="utf-8")
    print(f"sync_wiki: wrote {len(PAGES) + 1} pages to {output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
