from __future__ import annotations

from dataclasses import dataclass
from io import StringIO
from pathlib import Path
import re

from Bio import Phylo

from app.phylo.model import TreeModel, TreeNode


@dataclass(frozen=True)
class LoadedTrees:
    trees: list[TreeModel]
    format: str  # "newick" | "nexus"


def _parse_support(clade) -> float | None:
    """
    支持度可能来自：
    - clade.confidence
    - clade.comment 中显式的 prob/prob(percent) 或纯数字
    - 内部节点的 clade.name 为纯数字
    """
    conf = getattr(clade, "confidence", None)
    if conf is not None:
        try:
            return float(conf)
        except Exception:
            pass

    comm = getattr(clade, "comment", None)
    if comm:
        text = str(comm).strip()
        for pattern in (
            r'^\s*(?P<value>-?\d+(?:\.\d+)?)\s*$',
            r'prob\(percent\)\s*=\s*"?(?P<value>-?\d+(?:\.\d+)?)',
            r'prob\s*=\s*(?P<value>-?\d+(?:\.\d+)?)',
        ):
            match = re.search(pattern, text, flags=re.IGNORECASE)
            if not match:
                continue
            try:
                value = float(match.group("value"))
                if "prob" in pattern and "percent" not in pattern and value <= 1.0:
                    return value * 100.0
                return value
            except Exception:
                continue

    name = getattr(clade, "name", None)
    if name:
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


def _read_text(path: Path) -> str:
    for encoding in ("utf-8", "utf-8-sig", "gb18030", "gbk", "latin-1"):
        try:
            return path.read_text(encoding=encoding)
        except UnicodeDecodeError:
            continue
    return path.read_text(encoding="utf-8", errors="replace")


def _strip_quotes(token: str) -> str:
    token = token.strip()
    if len(token) >= 2 and token[0] == token[-1] and token[0] in {"'", '"'}:
        return token[1:-1]
    return token


def _looks_like_nexus(text: str) -> bool:
    head = text.lstrip()[:256].lower()
    lowered = text.lower()
    return head.startswith("#nexus") or "begin trees;" in lowered or "translate" in lowered


def _parse_translate_block(text: str) -> dict[str, str]:
    match = re.search(r"(?is)\btranslate\b(.*?);", text)
    if not match:
        return {}
    body = match.group(1)
    mapping: dict[str, str] = {}
    for key, value in re.findall(r"\s*([^,\s]+)\s+([^,;]+?)\s*(?:,|$)", body):
        mapping[_strip_quotes(key)] = _strip_quotes(value)
    return mapping


def _extract_support_value(comment_text: str) -> str | None:
    patterns = (
        r'prob\(percent\)\s*=\s*"?(?P<value>-?\d+(?:\.\d+)?)',
        r'prob\s*=\s*(?P<value>-?\d+(?:\.\d+)?)',
        r'^\s*(?P<value>-?\d+(?:\.\d+)?)\s*$',
    )
    for pattern in patterns:
        match = re.search(pattern, comment_text, flags=re.IGNORECASE)
        if not match:
            continue
        try:
            value = float(match.group("value"))
        except Exception:
            continue
        if "prob(percent)" in pattern:
            return f"{value:g}"
        if "prob" in pattern and value <= 1.0:
            value *= 100.0
        return f"{value:g}"
    return None


def _normalize_beast_newick(tree_text: str) -> str:
    text = tree_text.strip()
    text = re.sub(r"^\s*\[\&[^\]]*\]\s*", "", text)

    def replace_internal_support(match: re.Match[str]) -> str:
        support = _extract_support_value(match.group(1))
        return ")" + (support or "")

    # 将内部节点上的 BEAST 注释转换成标准 Newick 支持度，如 )100:0.01
    text = re.sub(r"\)\s*\[\&([^\]]*)\]\s*(?=:)", replace_internal_support, text)
    return text


def _translate_tree_labels(tree, translate_map: dict[str, str]) -> None:
    if not translate_map:
        return
    for clade in tree.find_clades():
        if clade.name is None:
            continue
        key = str(clade.name).strip()
        translated = translate_map.get(key)
        if translated:
            clade.name = translated


def _parse_newick_text(text: str, name: str) -> LoadedTrees:
    trees = list(Phylo.parse(StringIO(text), "newick"))
    if not trees:
        raise ValueError("Newick 文件中未找到树。")
    return LoadedTrees(
        trees=[_as_tree_model(tree, name=getattr(tree, "name", None) or name) for tree in trees],
        format="newick",
    )


def _parse_nexus_fallback(path: Path, text: str | None = None) -> LoadedTrees:
    text = text if text is not None else _read_text(path)
    translate_map = _parse_translate_block(text)
    tree_matches = list(
        re.finditer(
            r"(?is)\btree\s+([^=\s]+)\s*=\s*(\[\&[^\]]*\]\s*)?(.*?);",
            text,
        )
    )
    trees: list[TreeModel] = []
    for match in tree_matches:
        tree_name = _strip_quotes(match.group(1))
        raw_tree = match.group(3).strip() + ";"
        normalized = _normalize_beast_newick(raw_tree)
        phylo_tree = Phylo.read(StringIO(normalized), "newick")
        _translate_tree_labels(phylo_tree, translate_map)
        trees.append(_as_tree_model(phylo_tree, name=tree_name or path.name))
    if not trees:
        raise ValueError(f"NEXUS 文件中未找到 tree：{path}")
    return LoadedTrees(trees=trees, format="nexus")


def load_trees(path: str | Path) -> LoadedTrees:
    p = Path(path)
    suffix = p.suffix.lower()
    text = _read_text(p)

    if suffix in {".nwk", ".newick"}:
        return _parse_newick_text(text, p.name)

    if suffix in {".nex", ".nexus"}:
        try:
            trees = [_as_tree_model(t, name=getattr(t, "name", None) or p.name) for t in Phylo.parse(StringIO(text), "nexus")]
            if trees:
                return LoadedTrees(trees=trees, format="nexus")
        except Exception:
            pass
        return _parse_nexus_fallback(p, text)

    if suffix == ".tre":
        if _looks_like_nexus(text):
            try:
                trees = [_as_tree_model(t, name=getattr(t, "name", None) or p.name) for t in Phylo.parse(StringIO(text), "nexus")]
                if trees:
                    return LoadedTrees(trees=trees, format="nexus")
            except Exception:
                pass
            try:
                return _parse_nexus_fallback(p, text)
            except Exception:
                pass
        return _parse_newick_text(text, p.name)

    try:
        return _parse_newick_text(text, p.name)
    except Exception:
        if _looks_like_nexus(text):
            return _parse_nexus_fallback(p, text)
        raise
