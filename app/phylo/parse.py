from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

from Bio import Phylo

from app.phylo.model import TreeModel, TreeNode


@dataclass(frozen=True)
class LoadedTrees:
    trees: list[TreeModel]
    format: str  # "newick" | "nexus"


def _parse_support(clade) -> float | None:
    """
    支持度可能来自：
    - clade.confidence（Bio.Phylo 常用字段）
    - clade.comment（Newick 方括号注释，如 :0.01[100]）
    - clade.name（有些软件把支持度放到内部节点 name）
    """
    conf = getattr(clade, "confidence", None)
    if conf is not None:
        try:
            return float(conf)
        except Exception:
            pass

    comm = getattr(clade, "comment", None)
    if comm:
        try:
            return float(str(comm).strip())
        except Exception:
            pass

    name = getattr(clade, "name", None)
    if name:
        # 内部节点 name 可能是数字支持度；叶子 name 通常是物种名
        try:
            return float(str(name).strip())
        except Exception:
            return None

    return None


def _as_tree_model(tree, name: str | None = None) -> TreeModel:
    i = 0

    def make_id() -> str:
        nonlocal i
        i += 1
        return f"n{i}"

    def convert(clade) -> TreeNode:
        nm = getattr(clade, "name", None)
        node = TreeNode(
            id=make_id(),
            name=nm,
            original_name=nm,
            branch_length=getattr(clade, "branch_length", None),
            support=_parse_support(clade),
            children=[],
        )
        for child in getattr(clade, "clades", []) or []:
            node.children.append(convert(child))
        return node

    root = convert(tree.root)
    return TreeModel(root=root, name=name or getattr(tree, "name", None))


def load_trees(path: str | Path) -> LoadedTrees:
    p = Path(path)
    suffix = p.suffix.lower()
    if suffix in {".nwk", ".newick"}:
        tree = Phylo.read(str(p), "newick")
        return LoadedTrees(trees=[_as_tree_model(tree, name=p.name)], format="newick")

    if suffix in {".nex", ".nexus", ".tre"}:
        trees = []
        for t in Phylo.parse(str(p), "nexus"):
            trees.append(_as_tree_model(t, name=getattr(t, "name", None) or p.name))
        if not trees:
            raise ValueError(f"NEXUS 文件中未找到 tree：{p}")
        return LoadedTrees(trees=trees, format="nexus")

    # 尝试兜底：按 newick 读
    tree = Phylo.read(str(p), "newick")
    return LoadedTrees(trees=[_as_tree_model(tree, name=p.name)], format="newick")

