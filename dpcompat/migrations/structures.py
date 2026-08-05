"""Migration rule wrapper for entity NBT inside structure resources.

The wrapper handles file discovery and diagnostics while :mod:`structure_nbt` performs the
actual typed transformation.
"""

from __future__ import annotations

import struct

from .. import nbt
from ..models import Compatibility, Diagnostic, MigrationRecord, PackFormat, Severity
from .base import MigrationContext, RuleResult, crosses
from .common import policy_diagnostic
from .structure_nbt import downgrade_entity, upgrade_entity


class StructureEntityNbtRule:
    """Rewrite supported entity payloads in binary structure NBT files."""

    id = "structure.entity-nbt@71"
    boundary = PackFormat(71)

    def applies(self, source: PackFormat, target: PackFormat) -> bool:
        return crosses(source, target, self.boundary)

    def apply(self, context: MigrationContext) -> RuleResult:
        upgrading = context.source < self.boundary <= context.target
        transform = upgrade_entity if upgrading else downgrade_entity
        changed_files = 0
        changed_nodes = 0
        diagnostics: list[Diagnostic] = []
        for path in sorted((context.root / "data").rglob("*.nbt")):
            relative = "/" + context.relative(path)
            if "/structure/" not in relative:
                continue
            try:
                document = nbt.load_path(path)
                root = nbt.compound(document.root)
                if root is None:
                    raise nbt.NbtError("Structure root is not a compound")
                entities_tag = root.get("entities")
                entities = nbt.list_values(entities_tag, nbt.TAG_COMPOUND) if entities_tag else None
                if entities is None:
                    continue
                local_changed = 0
                for entry_tag in entities:
                    entry = nbt.compound(entry_tag)
                    entity_tag = entry.get("nbt") if entry else None
                    entity = nbt.compound(entity_tag) if entity_tag else None
                    if entity is None:
                        continue
                    id_tag = entity.get("id")
                    entity_id = id_tag.value if id_tag and id_tag.type_id == nbt.TAG_STRING else ""
                    result = transform(entity_id, entity)
                    local_changed += result.changed
                    for warning in result.warnings:
                        diagnostics.append(
                            policy_diagnostic(
                                context,
                                compatibility=Compatibility.LOSSY,
                                code="structure-entity-nbt-lossy-conversion",
                                message=warning,
                                path=context.relative(path),
                                line=None,
                                rule_id=self.id,
                                details={"entity": entity_id},
                            )
                        )
                if local_changed:
                    nbt.dump_path(path, document)
                    changed_files += 1
                    changed_nodes += local_changed
            except (OSError, nbt.NbtError, struct.error) as exc:
                diagnostics.append(
                    Diagnostic(
                        Severity.ERROR,
                        "structure-nbt-migration-failed",
                        str(exc),
                        path=context.relative(path),
                        compatibility=Compatibility.UNKNOWN,
                        rule_id=self.id,
                    )
                )
        return RuleResult(
            MigrationRecord(self.id, Compatibility.LOSSLESS, changed_files, changed_nodes),
            diagnostics,
        )
