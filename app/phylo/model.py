from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class TreeNode:
    id: str
    name: Optional[str] = None
    original_name: Optional[str] = None
    branch_length: float | None = None
    support: float | None = None
    collapsed: bool = False
    children: list["TreeNode"] = field(default_factory=list)

    def is_leaf(self) -> bool:
        return not self.children


@dataclass
class TreeModel:
    root: TreeNode
    name: str | None = None

    def iter_nodes(self) -> list[TreeNode]:
        out: list[TreeNode] = []
        stack = [self.root]
        while stack:
            n = stack.pop()
            out.append(n)
            stack.extend(reversed(n.children))
        return out

