from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable

from app.phylo.model import TreeModel, TreeNode


@dataclass(frozen=True)
class RenameRule:
    pattern: str
    repl: str
    flags: int = 0


def apply_rename(model: TreeModel, rename_map: dict[str, str] | None = None, rules: list[RenameRule] | None = None) -> None:
    rename_map = rename_map or {}
    rules = rules or []

    compiled = [(re.compile(r.pattern, r.flags), r.repl) for r in rules]

    for n in model.iter_nodes():
        if not n.is_leaf():
            continue
        if n.original_name is None:
            n.original_name = n.name

        cur = n.name or ""

        # 1) 精确映射（优先）
        if cur in rename_map:
            cur = rename_map[cur]

        # 2) 规则替换（顺序执行）
        for pat, repl in compiled:
            cur = pat.sub(repl, cur)

        n.name = cur

