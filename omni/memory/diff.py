"""Structural diff for memory values (JSON documents)."""

from __future__ import annotations

from typing import Any

from omni.contracts.memory import MemoryDiffItem, MemoryDiffOp


def diff_values(old: dict[str, Any] | None, new: dict[str, Any] | None) -> list[MemoryDiffItem]:
    """Single-level structural diff of two JSON documents.

    Paths are dot-joined keys. Nested dicts are traversed; lists are compared
    by index up to the shared length.
    """
    old = old or {}
    new = new or {}
    items: list[MemoryDiffItem] = []
    _walk(old, new, "", items)
    return items


def _walk(
    old: Any,
    new: Any,
    path: str,
    out: list[MemoryDiffItem],
) -> None:
    # value changed
    if type(old) is not type(new):
        out.append(
            MemoryDiffItem(
                op=MemoryDiffOp.REPLACE,
                path=path or "<root>",
                old_value=old,
                new_value=new,
            )
        )
        return

    if isinstance(old, dict) and isinstance(new, dict):
        for key in sorted(set(old) | set(new)):
            child = f"{path}.{key}" if path else key
            if key not in old:
                out.append(MemoryDiffItem(op=MemoryDiffOp.ADD, path=child, new_value=new[key]))
            elif key not in new:
                out.append(MemoryDiffItem(op=MemoryDiffOp.REMOVE, path=child, old_value=old[key]))
            else:
                _walk(old[key], new[key], child, out)
        return

    if isinstance(old, list) and isinstance(new, list):
        for i in range(max(len(old), len(new))):
            child = f"{path}[{i}]"
            if i >= len(old):
                out.append(MemoryDiffItem(op=MemoryDiffOp.ADD, path=child, new_value=new[i]))
            elif i >= len(new):
                out.append(MemoryDiffItem(op=MemoryDiffOp.REMOVE, path=child, old_value=old[i]))
            else:
                _walk(old[i], new[i], child, out)
        return

    if old != new:
        out.append(
            MemoryDiffItem(
                op=MemoryDiffOp.REPLACE,
                path=path or "<root>",
                old_value=old,
                new_value=new,
            )
        )
