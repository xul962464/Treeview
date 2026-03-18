from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class TipStyleOverride:
    taxon_name: str
    font_family: str | None = None
    font_size: float | None = None
    bold: bool | None = None
    color: str | None = None
    display_text: str | None = None
    rich_html: str | None = None


@dataclass
class NodeLabelOverride:
    node_id: str
    display_text: str | None = None
    rich_html: str | None = None


@dataclass
class ScaleBarLabelOverride:
    display_text: str | None = None
    rich_html: str | None = None


@dataclass
class CladeHighlight:
    node_id: str
    color_start: str
    color_end: str | None = None
    opacity: float = 0.28

    @property
    def is_gradient(self) -> bool:
        return bool(self.color_end and self.color_end != self.color_start)


@dataclass
class LeafGroupAnnotation:
    group_id: str
    name: str
    start_leaf_index: int
    end_leaf_index: int
    level: int = 0
    color: str = "#374151"
    background_enabled: bool = False
    background_scope: str = "label"
    background_color_start: str | None = None
    background_color_end: str | None = None
    leaf_ids: list[str] = field(default_factory=list)
    child_group_ids: list[str] = field(default_factory=list)


@dataclass
class AnnotationState:
    tip_style_overrides: dict[str, TipStyleOverride] = field(default_factory=dict)
    node_label_overrides: dict[str, NodeLabelOverride] = field(default_factory=dict)
    scale_bar_label_override: ScaleBarLabelOverride | None = None
    clade_highlights: dict[str, CladeHighlight] = field(default_factory=dict)
    leaf_groups: list[LeafGroupAnnotation] = field(default_factory=list)
    node_label_offsets: dict[str, tuple[float, float]] = field(default_factory=dict)
    tip_label_offsets: dict[str, tuple[float, float]] = field(default_factory=dict)
    scale_bar_offset: tuple[float, float] | None = None
