from __future__ import annotations

import html
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

import yaml

from app.phylo.model import TreeModel, TreeNode
from app.phylo.rename import RenameRule, apply_rename


@dataclass
class LabelStyle:
    color: str | None = None
    font_family: str | None = None
    font_size: int | None = None
    font_weight: str | None = None  # "normal"|"bold"|...
    node_color: str | None = None


@dataclass
class LabelSpanRule:
    pattern: str
    style: LabelStyle
    regex: bool = False
    flags: int = 0


@dataclass
class TreeStyles:
    label_style_by_taxon: dict[str, LabelStyle]
    label_spans_by_taxon: dict[str, list[LabelSpanRule]]
    annotations_by_taxon: dict[str, str]


def _read_config(path: str | Path) -> dict[str, Any]:
    p = Path(path)
    text = p.read_text(encoding="utf-8", errors="replace")
    if p.suffix.lower() in {".yaml", ".yml"}:
        data = yaml.safe_load(text) or {}
    else:
        data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError("配置文件顶层必须是一个对象(dict)")
    return data


def load_and_apply_config(model: TreeModel, path: str | Path) -> TreeStyles:
    cfg = _read_config(path)

    # 1) rename
    rename_map = cfg.get("rename_map") or {}
    if not isinstance(rename_map, dict):
        raise ValueError("rename_map 必须是 dict")

    rules_cfg = cfg.get("rename_rules") or []
    rules: list[RenameRule] = []
    if not isinstance(rules_cfg, list):
        raise ValueError("rename_rules 必须是 list")
    for r in rules_cfg:
        if not isinstance(r, dict) or "pattern" not in r or "repl" not in r:
            raise ValueError("rename_rules 每一项必须包含 pattern 与 repl")
        flags = 0
        if r.get("ignore_case"):
            flags |= re.IGNORECASE
        rules.append(RenameRule(pattern=str(r["pattern"]), repl=str(r["repl"]), flags=flags))

    apply_rename(model, rename_map=rename_map, rules=rules)

    # 2) label styles
    label_styles_cfg = cfg.get("label_styles") or {}
    if not isinstance(label_styles_cfg, dict):
        raise ValueError("label_styles 必须是 dict")
    label_style_by_taxon: dict[str, LabelStyle] = {}
    for taxon, st in label_styles_cfg.items():
        if not isinstance(st, dict):
            continue
        label_style_by_taxon[str(taxon)] = LabelStyle(
            color=st.get("color"),
            font_family=st.get("fontFamily") or st.get("font_family"),
            font_size=st.get("fontSize") or st.get("font_size"),
            font_weight=st.get("fontWeight") or st.get("font_weight"),
            node_color=st.get("nodeColor") or st.get("node_color"),
        )

    # 3) label spans
    spans_cfg = cfg.get("label_spans") or {}
    if not isinstance(spans_cfg, dict):
        raise ValueError("label_spans 必须是 dict")
    label_spans_by_taxon: dict[str, list[LabelSpanRule]] = {}
    for taxon, rules_list in spans_cfg.items():
        if not isinstance(rules_list, list):
            continue
        out: list[LabelSpanRule] = []
        for rr in rules_list:
            if not isinstance(rr, dict) or "pattern" not in rr or "style" not in rr:
                continue
            st = rr["style"] if isinstance(rr["style"], dict) else {}
            out.append(
                LabelSpanRule(
                    pattern=str(rr["pattern"]),
                    regex=bool(rr.get("regex", False)),
                    flags=re.IGNORECASE if rr.get("ignore_case") else 0,
                    style=LabelStyle(
                        color=st.get("color"),
                        font_family=st.get("fontFamily") or st.get("font_family"),
                        font_size=st.get("fontSize") or st.get("font_size"),
                        font_weight=st.get("fontWeight") or st.get("font_weight"),
                    ),
                )
            )
        label_spans_by_taxon[str(taxon)] = out

    ann = cfg.get("annotations") or {}
    if ann and not isinstance(ann, dict):
        raise ValueError("annotations 必须是 dict")

    annotations_by_taxon = {str(k): str(v) for k, v in (ann or {}).items()}
    return TreeStyles(
        label_style_by_taxon=label_style_by_taxon,
        label_spans_by_taxon=label_spans_by_taxon,
        annotations_by_taxon=annotations_by_taxon,
    )


def label_to_html(label: str, base: LabelStyle | None, spans: list[LabelSpanRule] | None) -> str:
    """
    把标签渲染成 HTML（用于 QGraphicsTextItem.setHtml），支持子串级样式。
    规则：只对非重叠的首次匹配做分段；如果有多个规则，按顺序应用。
    """
    text = label or ""
    if not spans:
        return _wrap_base_style(html.escape(text), base)

    segments = [(text, None)]  # (raw_text, LabelStyle|None)

    def apply_rule(segments_in: list[tuple[str, LabelStyle | None]], rule: LabelSpanRule) -> list[tuple[str, LabelStyle | None]]:
        out: list[tuple[str, LabelStyle | None]] = []
        for seg_text, seg_style in segments_in:
            # 已有样式的片段不再细分，避免规则互相覆盖导致不可控。
            if seg_style is not None:
                out.append((seg_text, seg_style))
                continue

            if not seg_text:
                out.append((seg_text, seg_style))
                continue

            if rule.regex:
                m = re.search(rule.pattern, seg_text, flags=rule.flags)
                if not m:
                    out.append((seg_text, None))
                    continue
                a, b = m.span()
            else:
                needle = rule.pattern
                if rule.flags & re.IGNORECASE:
                    a = seg_text.lower().find(needle.lower())
                else:
                    a = seg_text.find(needle)
                if a < 0:
                    out.append((seg_text, None))
                    continue
                b = a + len(needle)

            out.append((seg_text[:a], None))
            out.append((seg_text[a:b], rule.style))
            out.append((seg_text[b:], None))
        return out

    for r in spans:
        segments = apply_rule(segments, r)

    parts = []
    for seg_text, seg_style in segments:
        if not seg_text:
            continue
        esc = html.escape(seg_text)
        if seg_style is None:
            parts.append(esc)
        else:
            parts.append(_wrap_span_style(esc, seg_style))

    return _wrap_base_style("".join(parts), base)


def _wrap_base_style(inner_html: str, base: LabelStyle | None) -> str:
    if not base:
        return inner_html
    style = _css_from_style(base)
    return f"<span style=\"{style}\">{inner_html}</span>" if style else inner_html


def _wrap_span_style(inner_html: str, st: LabelStyle) -> str:
    style = _css_from_style(st)
    return f"<span style=\"{style}\">{inner_html}</span>" if style else inner_html


def _css_from_style(st: LabelStyle) -> str:
    css = []
    if st.color:
        css.append(f"color:{st.color}")
    if st.font_family:
        css.append(f"font-family:{st.font_family}")
    if st.font_size:
        css.append(f"font-size:{int(st.font_size)}px")
    if st.font_weight:
        css.append(f"font-weight:{st.font_weight}")
    return ";".join(css)


