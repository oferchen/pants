# Copyright 2021 Pants project contributors (see CONTRIBUTORS.md).
# Licensed under the Apache License, Version 2.0 (see LICENSE).

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence


@dataclass(frozen=True)
class JavaImport:
    name: str
    is_static: bool = False
    is_asterisk: bool = False

    @classmethod
    def from_json_dict(cls, imp: dict[str, Any]) -> JavaImport:
        return cls(
            name=imp["name"],
            is_asterisk=imp["isAsterisk"],
            is_static=imp["isStatic"],
        )

    def to_debug_json_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "is_static": self.is_static,
            "is_asterisk": self.is_asterisk,
        }


@dataclass(frozen=True)
class JavaSourceDependencyAnalysis:
    declared_package: str | None
    imports: Sequence[JavaImport]
    top_level_types: Sequence[str]
    consumed_unqualified_types: Sequence[str]

    @classmethod
    def from_json_dict(cls, analysis: dict[str, Any]) -> JavaSourceDependencyAnalysis:
        return cls(
            declared_package=analysis.get("declaredPackage"),
            imports=tuple(JavaImport.from_json_dict(imp) for imp in analysis["imports"]),
            top_level_types=tuple(analysis["topLevelTypes"]),
            consumed_unqualified_types=tuple(analysis["consumedUnqualifiedTypes"]),
        )

    def to_debug_json_dict(self) -> dict[str, Any]:
        return {
            "declared_package": self.declared_package,
            "imports": [imp.to_debug_json_dict() for imp in self.imports],
            "top_level_types": self.top_level_types,
            "consumed_unqualified_types": self.consumed_unqualified_types,
        }
