from __future__ import annotations

from dataclasses import asdict, dataclass
import html
import math
import re
from typing import Callable

from PySide6.QtCore import QPoint, QPointF, QRectF, QSignalBlocker, QSizeF, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QFontMetricsF, QImage, QLinearGradient, QPainter, QPageSize, QPen, QPolygonF, QRadialGradient, QTextCharFormat, QTextCursor, QTextOption
from PySide6.QtPrintSupport import QPrinter
from PySide6.QtSvg import QSvgGenerator
from PySide6.QtWidgets import QGraphicsEllipseItem, QGraphicsItem, QGraphicsLineItem, QGraphicsPolygonItem, QGraphicsRectItem, QGraphicsScene, QGraphicsSimpleTextItem, QGraphicsTextItem, QGraphicsView

from app.gui.annotation_state import AnnotationState
from app.phylo.model import TreeModel, TreeNode


SCALE_BAR_ID = "__scale_bar__"


@dataclass(frozen=True)
class TreeRenderStats:
    node_count: int
    leaf_count: int


@dataclass
class TreeRenderOptions:
    layout_mode: str = "rectangular"
    canvas_width: int = 1000
    canvas_height: int = 800
    ignore_branch_lengths: bool = False
    align_tip_labels: bool = False
    show_tip_labels: bool = True
    show_node_circles: bool = False
    show_selected_node_circle: bool = False
    show_support_labels: bool = False
    show_leader_lines: bool = True
    font_family: str = "Arial"
    font_size: int = 16
    support_font_size: int = 12
    support_color: str = "#4b5563"
    support_offset_x: float = -36.0
    support_offset_y: float = -22.0
    view_offset_x: float = 0.0
    view_offset_y: float = 0.0
    circular_start_angle: float = -90.0
    circular_gap_degrees: float = 18.0
    circular_label_follow_branch: bool = False
    leader_line_color: str = "#9ca3af"
    leader_line_width: float = 1.0
    branch_color: str = "#000000"
    branch_width: float = 1.2
    group_line_width: float = 6.0
    node_circle_size: float = 7.2
    node_circle_color: str = "#000000"
    collapsed_triangle_color: str = "#6b7280"
    scale_bar_visible: bool = True
    scale_bar_auto: bool = True
    scale_bar_length: float = 0.1
    scale_bar_position: str = "left"


@dataclass
class RenderContext:
    leaf_order: list[str]
    leaf_index: dict[str, int]
    x_raw: dict[str, float]
    y_raw: dict[str, float]
    max_x: float
    max_y: float


class MovableLabelItem(QGraphicsSimpleTextItem):
    def __init__(self, node_id: str, on_moved, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._node_id = node_id
        self._on_moved = on_moved
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, True)

    def mouseReleaseEvent(self, event) -> None:
        super().mouseReleaseEvent(event)
        self._on_moved(self._node_id, self.pos().x(), self.pos().y())


class MovableTextItem(QGraphicsTextItem):
    def __init__(self, node_id: str, on_moved, on_edited, movable: bool = True, on_editing_changed=None) -> None:
        super().__init__()
        self._node_id = node_id
        self._on_moved = on_moved
        self._on_edited = on_edited
        self._movable = movable
        self._on_editing_changed = on_editing_changed
        self._editing = False
        self._before_edit_html = ""
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, movable)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsFocusable, True)
        self.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
        self.setTextWidth(-1)
        text_option = self.document().defaultTextOption()
        text_option.setWrapMode(QTextOption.WrapMode.NoWrap)
        self.document().setDefaultTextOption(text_option)
        self.document().contentsChanged.connect(self._sync_text_layout)
        self._sync_text_layout()

    def mouseReleaseEvent(self, event) -> None:
        super().mouseReleaseEvent(event)
        if not self._editing and self._movable:
            self._on_moved(self._node_id, self.pos().x(), self.pos().y())

    def mouseDoubleClickEvent(self, event) -> None:
        self._before_edit_html = self.toHtml()
        self._editing = True
        if self._on_editing_changed is not None:
            self._on_editing_changed(True)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, False)
        self.setTextInteractionFlags(Qt.TextInteractionFlag.TextEditorInteraction)
        self.setFocus(Qt.FocusReason.MouseFocusReason)
        super().mouseDoubleClickEvent(event)

    def focusOutEvent(self, event) -> None:
        was_editing = self._editing
        super().focusOutEvent(event)
        if was_editing:
            self._finish_editing()

    def keyPressEvent(self, event) -> None:
        if self._editing and event.key() == Qt.Key.Key_F2:
            cursor = self.textCursor()
            fmt = cursor.charFormat()
            fmt.setFontItalic(not fmt.fontItalic())
            cursor.mergeCharFormat(fmt)
            self.mergeCurrentCharFormat(fmt)
            event.accept()
            return
        if self._editing and event.key() == Qt.Key.Key_Escape:
            self.setHtml(self._before_edit_html)
            self._finish_editing()
            event.accept()
            return
        if self._editing and event.key() in {Qt.Key.Key_Enter, Qt.Key.Key_Return} and not (event.modifiers() & Qt.KeyboardModifier.ShiftModifier):
            self._finish_editing()
            event.accept()
            return
        super().keyPressEvent(event)

    def _finish_editing(self) -> None:
        if not self._editing:
            return
        self._editing = False
        if self._on_editing_changed is not None:
            self._on_editing_changed(False)
        self.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, self._movable)
        self.clearFocus()
        self._sync_text_layout()
        self._on_edited(self._node_id, self.toPlainText(), self._body_html())

    def _body_html(self) -> str:
        html = self.toHtml()
        match = re.search(r"<body[^>]*>(.*)</body>", html, flags=re.IGNORECASE | re.DOTALL)
        if not match:
            return html
        return match.group(1).strip()

    def _sync_text_layout(self) -> None:
        self.setTextWidth(-1)
        self.document().adjustSize()
        self.update()


class ScaleBarItem(QGraphicsRectItem):
    def __init__(self, on_moved) -> None:
        super().__init__()
        self._on_moved = on_moved
        self._editing = False
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, True)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, True)
        self.setBrush(QBrush(Qt.GlobalColor.transparent))
        self.setPen(QPen(Qt.GlobalColor.transparent))
        self.setZValue(1.0)

    def set_editing(self, editing: bool) -> None:
        self._editing = editing
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, not editing)

    def mouseReleaseEvent(self, event) -> None:
        super().mouseReleaseEvent(event)
        if not self._editing:
            self._on_moved(self.pos().x(), self.pos().y())


class GroupAnnotationItem(QGraphicsRectItem):
    def __init__(self, group_id: str, on_moved) -> None:
        super().__init__()
        self._group_id = group_id
        self._on_moved = on_moved
        self._editing = False
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, True)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, True)
        self.setBrush(QBrush(Qt.GlobalColor.transparent))
        self.setPen(QPen(Qt.GlobalColor.transparent))

    def set_editing(self, editing: bool) -> None:
        self._editing = editing
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, not editing)

    def mouseReleaseEvent(self, event) -> None:
        super().mouseReleaseEvent(event)
        if not self._editing:
            self._on_moved(self._group_id, self.pos().x(), self.pos().y())


class TreeView(QGraphicsView):
    nodeClicked = Signal(str)
    selectionChanged = Signal(list)
    contextMenuRequested = Signal(str, str, QPoint)
    nodeLabelMoved = Signal(str, float, float)
    nodeLabelEdited = Signal(str, str, str)
    tipLabelMoved = Signal(str, float, float)
    tipLabelEdited = Signal(str, str, str)
    scaleBarMoved = Signal(float, float)
    scaleBarEdited = Signal(str, str)
    groupMoved = Signal(str, float, float)
    groupEdited = Signal(str, str, str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.TextAntialiasing)
        self.setDragMode(QGraphicsView.DragMode.NoDrag)
        self.setViewportUpdateMode(QGraphicsView.ViewportUpdateMode.FullViewportUpdate)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self._scene = QGraphicsScene(self)
        self.setScene(self._scene)
        self._scene.selectionChanged.connect(self._on_selection_changed)
        self._current_selected_ids: set[str] = set()
        self._node_items: dict[str, QGraphicsItem] = {}
        self._edge_items: dict[str, list[QGraphicsItem]] = {}
        self._edge_visible_items: dict[str, list[QGraphicsLineItem]] = {}
        self._label_items: dict[str, QGraphicsTextItem] = {}
        self._label_text: dict[str, str] = {}
        self._label_html: dict[str, str] = {}
        self._label_color: dict[str, QColor] = {}
        self._label_font: dict[str, QFont] = {}
        self._node_default_brush: dict[str, QBrush] = {}
        self._node_label_items: dict[str, QGraphicsTextItem] = {}
        self._node_label_html: dict[str, str] = {}
        self._node_label_font: dict[str, QFont] = {}
        self._node_label_color: dict[str, QColor] = {}
        self._node_anchor_pos: dict[str, QPointF] = {}
        self._tip_anchor_pos: dict[str, QPointF] = {}
        self._scale_bar_anchor_pos: QPointF | None = None
        self._scale_bar_item: ScaleBarItem | None = None
        self._scale_bar_label_item: QGraphicsTextItem | None = None
        self._scale_bar_html = ""
        self._scale_bar_font: QFont | None = None
        self._scale_bar_color: QColor | None = None
        self._scale_bar_width = 0.0
        self._scale_bar_line_height = 0.0
        self._group_items: dict[str, GroupAnnotationItem] = {}
        self._group_label_items: dict[str, QGraphicsTextItem] = {}
        self._group_html: dict[str, str] = {}
        self._group_font: dict[str, QFont] = {}
        self._group_color: dict[str, QColor] = {}
        self._group_anchor_pos: dict[str, QPointF] = {}
        self._label_html_provider: Callable[[TreeNode], str] | None = None
        self._annotation_state = AnnotationState()
        self._last_model: TreeModel | None = None
        self._render_options = TreeRenderOptions()
        self._in_rubberband = False

    def clear(self) -> None:
        self._scene.clear()
        self._node_items.clear()
        self._edge_items.clear()
        self._edge_visible_items.clear()
        self._label_items.clear()
        self._label_text.clear()
        self._label_html.clear()
        self._label_color.clear()
        self._label_font.clear()
        self._node_default_brush.clear()
        self._node_label_items.clear()
        self._node_label_html.clear()
        self._node_label_font.clear()
        self._node_label_color.clear()
        self._node_anchor_pos.clear()
        self._tip_anchor_pos.clear()
        self._scale_bar_anchor_pos = None
        self._scale_bar_item = None
        self._scale_bar_label_item = None
        self._scale_bar_html = ""
        self._scale_bar_font = None
        self._scale_bar_color = None
        self._scale_bar_width = 0.0
        self._scale_bar_line_height = 0.0
        self._group_items.clear()
        self._group_label_items.clear()
        self._group_html.clear()
        self._group_font.clear()
        self._group_color.clear()
        self._group_anchor_pos.clear()
        self._current_selected_ids.clear()
        self._support_items: dict[str, QGraphicsTextItem] = {}

    def set_label_html_provider(self, provider: Callable[[TreeNode], str] | None) -> None:
        self._label_html_provider = provider

    def set_annotation_state(self, state: AnnotationState) -> None:
        self._annotation_state = state

    def get_annotation_state(self) -> AnnotationState:
        return self._annotation_state

    def get_leaf_order(self) -> list[str]:
        if self._last_model is None:
            return []
        return self._collect_context(self._last_model).leaf_order

    def get_render_options(self) -> TreeRenderOptions:
        return TreeRenderOptions(**asdict(self._render_options))

    def set_render_options(self, options: TreeRenderOptions) -> None:
        self._render_options = TreeRenderOptions(**asdict(options))

    def set_layout_mode(self, mode: str) -> None:
        self._render_options.layout_mode = mode

    def set_ignore_branch_lengths(self, enabled: bool) -> None:
        self._render_options.ignore_branch_lengths = enabled

    def set_align_tip_labels(self, enabled: bool) -> None:
        self._render_options.align_tip_labels = enabled

    def set_show_tip_labels(self, enabled: bool) -> None:
        self._render_options.show_tip_labels = enabled

    def set_show_node_circles(self, enabled: bool) -> None:
        self._render_options.show_node_circles = enabled

    def set_show_support_labels(self, enabled: bool) -> None:
        self._render_options.show_support_labels = enabled

    def set_canvas_size(self, width: int, height: int) -> None:
        self._render_options.canvas_width = max(360, int(width))
        self._render_options.canvas_height = max(280, int(height))

    def _on_selection_changed(self) -> None:
        ids: list[str] = []
        for item in self._scene.selectedItems():
            node_id = item.data(0)
            if isinstance(node_id, str) and node_id:
                ids.append(node_id)
        uniq = list(dict.fromkeys(ids))
        self._current_selected_ids = set(uniq)
        self._update_highlight(set(uniq))
        self._refresh_node_circle_visibility()
        self.selectionChanged.emit(uniq)

    def _collect_context(self, model: TreeModel) -> RenderContext:
        leaves: list[TreeNode] = []

        def collect_all_leaves(node: TreeNode) -> None:
            if node.is_leaf():
                leaves.append(node)
                return
            for child in node.children:
                collect_all_leaves(child)

        collect_all_leaves(model.root)
        leaf_order = [node.id for node in leaves]
        leaf_index = {node_id: idx for idx, node_id in enumerate(leaf_order)}
        x_raw: dict[str, float] = {model.root.id: 0.0}
        y_raw: dict[str, float] = {}

        def descendant_leaf_indices(node: TreeNode) -> list[int]:
            out: list[int] = []

            def walk(cur: TreeNode) -> None:
                if cur.is_leaf():
                    idx = leaf_index.get(cur.id)
                    if idx is not None:
                        out.append(idx)
                    return
                for child in cur.children:
                    walk(child)

            walk(node)
            return out

        if self._render_options.ignore_branch_lengths:
            depth_raw: dict[str, float] = {model.root.id: 0.0}
            max_leaf_depth = 0.0

            def walk_depth(node: TreeNode, depth: float) -> None:
                nonlocal max_leaf_depth
                depth_raw[node.id] = depth
                if node.is_leaf():
                    y_raw[node.id] = float(leaf_index[node.id])
                    max_leaf_depth = max(max_leaf_depth, depth)
                    return
                if node.collapsed:
                    indices = descendant_leaf_indices(node)
                    if indices:
                        y_raw[node.id] = sum(float(index) for index in indices) / len(indices)
                    else:
                        y_raw[node.id] = 0.0
                    max_leaf_depth = max(max_leaf_depth, depth)
                    return
                for child in node.children:
                    walk_depth(child, depth + 1.0)
                y_raw[node.id] = sum(y_raw[ch.id] for ch in node.children) / max(1, len(node.children))

            if model.root.is_leaf():
                y_raw[model.root.id] = 0.0
                max_leaf_depth = 1.0
            else:
                for child in model.root.children:
                    walk_depth(child, 1.0)
                y_raw[model.root.id] = sum(y_raw[ch.id] for ch in model.root.children) / max(1, len(model.root.children))

            for node in model.iter_nodes():
                if node.is_leaf() or node.collapsed:
                    x_raw[node.id] = max_leaf_depth
                else:
                    x_raw[node.id] = depth_raw.get(node.id, 0.0)
        else:
            def edge_length(node: TreeNode) -> float:
                return float(node.branch_length or 0.0)

            def walk(node: TreeNode, parent_x: float) -> None:
                x_raw[node.id] = parent_x + edge_length(node)
                if node.is_leaf():
                    y_raw[node.id] = float(leaf_index[node.id])
                    return
                if node.collapsed:
                    indices = descendant_leaf_indices(node)
                    if indices:
                        y_raw[node.id] = sum(float(index) for index in indices) / len(indices)
                    else:
                        y_raw[node.id] = 0.0
                    return
                for child in node.children:
                    walk(child, x_raw[node.id])
                y_raw[node.id] = sum(y_raw[ch.id] for ch in node.children) / max(1, len(node.children))

            if model.root.is_leaf():
                y_raw[model.root.id] = 0.0
            else:
                for child in model.root.children:
                    walk(child, 0.0)
                y_raw[model.root.id] = sum(y_raw[ch.id] for ch in model.root.children) / max(1, len(model.root.children))

        return RenderContext(leaf_order, leaf_index, x_raw, y_raw, max(x_raw.values()) if x_raw else 0.0, max(y_raw.values()) if y_raw else 0.0)

    def render_tree(self, model: TreeModel) -> TreeRenderStats:
        self._last_model = model
        self.clear()
        context = self._collect_context(model)
        if self._render_options.layout_mode == "circular":
            self._render_circular(model, context)
        else:
            self._render_rectangular(model, context)
        canvas_rect = QRectF(0.0, 0.0, float(self._render_options.canvas_width), float(self._render_options.canvas_height))
        items_rect = self._scene.itemsBoundingRect().adjusted(-24.0, -24.0, 24.0, 24.0)
        target_rect = items_rect.united(canvas_rect) if not items_rect.isNull() else canvas_rect
        self._scene.setSceneRect(target_rect)
        self.fitInView(target_rect, Qt.AspectRatioMode.KeepAspectRatio)
        return TreeRenderStats(len(model.iter_nodes()), len(context.leaf_order))

    def restore_selection(self, node_ids: list[str]) -> None:
        unique_ids = list(dict.fromkeys(node_id for node_id in node_ids if self._selection_item_for_node(node_id) is not None))
        blocker = QSignalBlocker(self._scene)
        self._scene.clearSelection()
        for node_id in unique_ids:
            item = self._selection_item_for_node(node_id)
            if item is not None:
                item.setSelected(True)
        del blocker
        self._current_selected_ids = set(unique_ids)
        self._update_highlight(set(unique_ids))
        self._refresh_node_circle_visibility()
        if unique_ids or node_ids:
            self.selectionChanged.emit(unique_ids)

    def set_selected_ids(self, node_ids: list[str]) -> None:
        self._current_selected_ids = set(node_ids)
        self._refresh_node_circle_visibility()

    def _selection_item_for_node(self, node_id: str) -> QGraphicsItem | None:
        if node_id == SCALE_BAR_ID:
            return self._scale_bar_item
        if node_id in self._group_items:
            return self._group_items[node_id]
        if node_id in self._label_items:
            return self._label_items[node_id]
        if node_id in self._node_label_items:
            return self._node_label_items[node_id]
        if node_id in self._node_items:
            return self._node_items[node_id]
        edge_items = self._edge_items.get(node_id)
        if edge_items:
            return edge_items[-1]
        return None

    def _render_rectangular(self, model: TreeModel, context: RenderContext) -> None:
        options = self._render_options
        align_labels = options.align_tip_labels and not options.ignore_branch_lengths
        label_column_width = max(180.0, self._max_visible_tip_label_width(model) + 20.0)
        ml, mt, mr, mb = 40.0, 36.0, (max(300.0, label_column_width + 120.0) if align_labels else 220.0), 56.0
        cw = max(80.0, float(options.canvas_width) - ml - mr)
        ch = max(80.0, float(options.canvas_height) - mt - mb)
        xs = cw / context.max_x if context.max_x > 0 else 1.0
        ys = ch / context.max_y if context.max_y > 0 else 1.0
        offset_x = float(options.view_offset_x)
        offset_y = float(options.view_offset_y)
        label_column_left = ml + cw + 20.0 + offset_x
        label_column_right = label_column_left + label_column_width

        def px_x(node_id: str) -> float:
            return ml + context.x_raw.get(node_id, 0.0) * xs + offset_x

        def px_y(node_id: str) -> float:
            if context.max_y <= 0:
                return mt + ch / 2.0 + offset_y
            return mt + context.y_raw.get(node_id, 0.0) * ys + offset_y

        def subtree_leaf_count(node: TreeNode) -> int:
            if node.is_leaf():
                return 1
            return max(1, sum(subtree_leaf_count(child) for child in node.children))

        self._draw_rectangular_backgrounds(model, px_x, px_y)

        def draw_edges(node: TreeNode) -> None:
            if node.is_leaf() or node.collapsed:
                return
            x0 = px_x(node.id)
            ys_ = [px_y(child.id) for child in node.children]
            if ys_:
                v_pen = QPen(QColor(self._annotation_state.branch_colors.get(node.id, options.branch_color)))
                v_pen.setWidthF(options.branch_width)
                v = QGraphicsLineItem(x0, min(ys_), x0, max(ys_))
                v.setPen(v_pen)
                self._scene.addItem(v)
                self._edge_visible_items.setdefault(node.id, []).append(v)
                vh = QGraphicsLineItem(x0, min(ys_), x0, max(ys_))
                vh.setPen(QPen(QColor(0, 0, 0, 0), 10.0))
                vh.setData(0, node.id)
                vh.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, True)
                self._scene.addItem(vh)
                self._edge_items.setdefault(node.id, []).append(vh)
                if options.show_support_labels and node.support is not None and node.id != model.root.id:
                    sup = self._create_node_label_item(node, f"{node.support:g}")
                    self._set_node_label_pos(node.id, sup, x0 + options.support_offset_x, px_y(node.id) + options.support_offset_y)
                    sup.setData(0, node.id)
                    sup.setData(1, ("support", node.id))
                    sup.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, True)
                    self._scene.addItem(sup)
                    self._node_label_items[node.id] = sup
                    self._support_items[node.id] = sup
            for child in node.children:
                x1, y1 = px_x(child.id), px_y(child.id)
                h_pen = QPen(QColor(self._annotation_state.branch_colors.get(child.id, options.branch_color)))
                h_pen.setWidthF(options.branch_width)
                h = QGraphicsLineItem(x0, y1, x1, y1)
                h.setPen(h_pen)
                h.setData(0, child.id)
                self._scene.addItem(h)
                self._edge_visible_items.setdefault(child.id, []).append(h)
                hh = QGraphicsLineItem(x0, y1, x1, y1)
                hh.setPen(QPen(QColor(0, 0, 0, 0), 10.0))
                hh.setData(0, child.id)
                hh.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, True)
                self._scene.addItem(hh)
                self._edge_items.setdefault(child.id, []).append(hh)
                draw_edges(child)

        def draw_nodes(node: TreeNode) -> None:
            x, y = px_x(node.id), px_y(node.id)
            if (options.show_node_circles or options.show_selected_node_circle) and node.id != model.root.id:
                diameter = max(2.0, float(options.node_circle_size))
                dot = QGraphicsEllipseItem(x - diameter / 2, y - diameter / 2, diameter, diameter)
                dot.setBrush(QBrush(QColor(options.node_circle_color)))
                dot.setPen(QPen(Qt.GlobalColor.transparent))
                dot.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, True)
                dot.setData(0, node.id)
                self._scene.addItem(dot)
                self._node_items[node.id] = dot
                self._node_default_brush[node.id] = dot.brush()
            if node.collapsed:
                collapsed_span = max(14.0, float(max(0, subtree_leaf_count(node) - 1)) * ys)
                self._draw_collapsed_triangle_rect(node.id, x, y, collapsed_span)
            if options.show_tip_labels and (node.is_leaf() or node.collapsed):
                self._draw_tip_label_rect(node, x, y, label_column_left, align_labels)
            if not node.collapsed:
                for child in node.children:
                    draw_nodes(child)

        draw_edges(model.root)
        draw_nodes(model.root)
        self._draw_group_backgrounds_rectangular(context, label_column_left, label_column_width)
        self._draw_groups_rectangular(context, label_column_right, mt + offset_y, ch, ys)
        self._draw_scale_bar(context, ml + offset_x, float(options.canvas_height) - 30.0 + offset_y)

    def _draw_tip_label_rect(self, node: TreeNode, x: float, y: float, label_column_left: float, align_labels: bool) -> None:
        options = self._render_options
        display_text, font, color = self._resolve_tip_label(node)
        html_text = self._label_html_provider(node) if self._label_html_provider else self._plain_text_html(display_text)
        label = self._create_tip_label_item(node.id, display_text, html_text, font, color)

        if align_labels:
            lx = label_column_left
            if options.show_leader_lines:
                pen = QPen(QColor(options.leader_line_color))
                pen.setWidthF(options.leader_line_width)
                pen.setStyle(Qt.PenStyle.DashLine)
                self._scene.addLine(x, y, lx - 5, y, pen)
        else:
            lx = x + 10
        self._set_tip_label_pos(node.id, label, lx, y - label.boundingRect().height() / 2)
        label.setData(0, node.id)
        label.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, True)
        self._scene.addItem(label)
        self._label_items[node.id] = label

    def _draw_rectangular_backgrounds(self, model: TreeModel, px_x, px_y) -> None:
        for highlight in self._annotation_state.clade_highlights.values():
            node = next((n for n in model.iter_nodes() if n.id == highlight.node_id), None)
            if node is None:
                continue
            leaf_ids = self._leaf_ids(node)
            if not leaf_ids:
                continue
            ys_ = [px_y(node_id) for node_id in leaf_ids]
            x = px_x(node.id)
            rect = QGraphicsRectItem(x + 4, min(ys_) - 10, max(180.0, float(self._render_options.canvas_width) - x - 50), max(20.0, max(ys_) - min(ys_) + 20))
            if highlight.is_gradient and highlight.color_end:
                gradient = QLinearGradient(rect.rect().left(), rect.rect().top(), rect.rect().left(), rect.rect().bottom())
                c1 = QColor(highlight.color_start)
                c1.setAlphaF(highlight.opacity)
                c2 = QColor(highlight.color_end)
                c2.setAlphaF(highlight.opacity)
                gradient.setColorAt(0.0, c1)
                gradient.setColorAt(1.0, c2)
                rect.setBrush(QBrush(gradient))
            else:
                color = QColor(highlight.color_start)
                color.setAlphaF(highlight.opacity)
                rect.setBrush(QBrush(color))
            rect.setPen(QPen(Qt.GlobalColor.transparent))
            rect.setZValue(-10)
            self._scene.addItem(rect)

    def _draw_group_backgrounds_rectangular(self, context: RenderContext, label_column_left: float, label_column_width: float) -> None:
        options = self._render_options
        if not self._annotation_state.leaf_groups:
            return
        mt = 36.0
        ch = max(80.0, float(options.canvas_height) - mt - 56.0)
        ys = ch / context.max_y if context.max_y > 0 else 1.0
        pad = max(10.0, ys / 2.0)

        def py(index: int) -> float:
            if context.max_y <= 0:
                return mt + ch / 2.0 + float(options.view_offset_y)
            return mt + float(index) * ys + float(options.view_offset_y)

        def group_leaf_ids(group) -> list[str]:
            leaf_ids = [leaf_id for leaf_id in (group.leaf_ids or []) if leaf_id in context.leaf_order and leaf_id in self._label_items]
            if not leaf_ids and 0 <= group.start_leaf_index <= group.end_leaf_index < len(context.leaf_order):
                leaf_ids = [leaf_id for leaf_id in context.leaf_order[group.start_leaf_index : group.end_leaf_index + 1] if leaf_id in self._label_items]
            return leaf_ids

        def label_union_rect(group) -> QRectF | None:
            leaf_ids = group_leaf_ids(group)
            rect: QRectF | None = None
            for leaf_id in leaf_ids:
                item_rect = self._label_items[leaf_id].sceneBoundingRect()
                rect = item_rect if rect is None else rect.united(item_rect)
            return rect

        uniform_right = None
        if options.ignore_branch_lengths or options.align_tip_labels:
            rights = [item.sceneBoundingRect().right() for item in self._label_items.values()]
            if rights:
                uniform_right = max(rights) + 8.0

        for group in self._annotation_state.leaf_groups:
            if not group.background_enabled or not group.background_color_start:
                continue
            actual_label_rect = label_union_rect(group)
            if actual_label_rect is not None:
                padded_label_rect = actual_label_rect.adjusted(-8.0, -2.0, 8.0, 2.0)
                first_index = max(0, group.start_leaf_index)
                last_index = min(len(context.leaf_order) - 1, group.end_leaf_index)
                y0 = padded_label_rect.top()
                y1 = padded_label_rect.bottom()
                if first_index > 0:
                    y0 = max(y0, (py(first_index - 1) + py(first_index)) / 2.0)
                if last_index < len(context.leaf_order) - 1:
                    y1 = min(y1, (py(last_index) + py(last_index + 1)) / 2.0)
                right_edge = uniform_right if uniform_right is not None else padded_label_rect.right()
                if group.background_scope == "label":
                    rect = QGraphicsRectItem(padded_label_rect.left(), y0, max(12.0, right_edge - padded_label_rect.left()), max(6.0, y1 - y0))
                else:
                    x0 = 20.0 + float(options.view_offset_x)
                    rect = QGraphicsRectItem(x0, y0, max(40.0, right_edge - x0), max(20.0, y1 - y0))
            else:
                y0, y1 = py(group.start_leaf_index) - pad, py(group.end_leaf_index) + pad
                background_right = uniform_right if uniform_right is not None else label_column_left - 8 + max(220.0, label_column_width + 16.0)
                x0 = label_column_left - 8 if group.background_scope == "label" else 20 + float(options.view_offset_x)
                width = max(40.0, background_right - x0)
                rect = QGraphicsRectItem(x0, y0, width, max(20.0, y1 - y0))
            if group.background_color_end and group.background_color_end != group.background_color_start:
                gradient = QLinearGradient(rect.rect().left(), rect.rect().top(), rect.rect().right(), rect.rect().top())
                c1 = QColor(group.background_color_start)
                c2 = QColor(group.background_color_end)
                c1.setAlphaF(0.2)
                c2.setAlphaF(0.2)
                gradient.setColorAt(0.0, c1)
                gradient.setColorAt(1.0, c2)
                rect.setBrush(QBrush(gradient))
            else:
                color = QColor(group.background_color_start)
                color.setAlphaF(0.2)
                rect.setBrush(QBrush(color))
            rect.setPen(QPen(Qt.GlobalColor.transparent))
            rect.setZValue(-20)
            self._scene.addItem(rect)

    def _group_angle_bounds(self, context: RenderContext, group, angle_for, angle_step: float) -> tuple[float, float] | None:
        leaf_ids = [leaf_id for leaf_id in (group.leaf_ids or []) if leaf_id in context.leaf_index]
        if not leaf_ids and 0 <= group.start_leaf_index <= group.end_leaf_index < len(context.leaf_order):
            leaf_ids = context.leaf_order[group.start_leaf_index : group.end_leaf_index + 1]
        if not leaf_ids:
            return None
        angles = [angle_for(leaf_id) for leaf_id in leaf_ids]
        if not angles:
            return None
        if len(angles) == 1:
            center_angle = angles[0]
            pad = max(angle_step / 2.0, math.radians(4.0))
            return center_angle - pad, center_angle + pad
        pad = max(angle_step / 2.0, math.radians(1.0))
        return min(angles) - pad, max(angles) + pad

    def _annular_sector_polygon(self, center: QPointF, inner_radius: float, outer_radius: float, start_angle: float, end_angle: float) -> QPolygonF:
        sweep = max(0.001, end_angle - start_angle)
        steps = max(18, int(abs(sweep) * 48.0 / math.pi))
        points: list[QPointF] = []
        for index in range(steps + 1):
            angle = start_angle + sweep * index / steps
            points.append(QPointF(center.x() + math.cos(angle) * outer_radius, center.y() + math.sin(angle) * outer_radius))
        for index in range(steps, -1, -1):
            angle = start_angle + sweep * index / steps
            points.append(QPointF(center.x() + math.cos(angle) * inner_radius, center.y() + math.sin(angle) * inner_radius))
        return QPolygonF(points)

    def _draw_group_backgrounds_circular(self, context: RenderContext, center: QPointF, inner_radius: float, outer_radius: float, angle_for, angle_step: float) -> None:
        options = self._render_options
        if not self._annotation_state.leaf_groups:
            return
        label_outer_radius = outer_radius + (60.0 if options.align_tip_labels else 36.0)
        label_inner_radius = outer_radius + 6.0
        uniform_outer_radius = None
        if options.ignore_branch_lengths or options.align_tip_labels:
            all_label_radii: list[float] = []
            for item in self._label_items.values():
                rect = item.sceneBoundingRect()
                corners = [rect.topLeft(), rect.topRight(), rect.bottomLeft(), rect.bottomRight()]
                for corner in corners:
                    all_label_radii.append(math.hypot(corner.x() - center.x(), corner.y() - center.y()))
            if all_label_radii:
                uniform_outer_radius = max(all_label_radii) + 8.0

        def label_polar_bounds(group) -> tuple[float, float, float, float] | None:
            leaf_ids = [leaf_id for leaf_id in (group.leaf_ids or []) if leaf_id in self._label_items]
            if not leaf_ids and 0 <= group.start_leaf_index <= group.end_leaf_index < len(context.leaf_order):
                leaf_ids = [leaf_id for leaf_id in context.leaf_order[group.start_leaf_index : group.end_leaf_index + 1] if leaf_id in self._label_items]
            if not leaf_ids:
                return None
            min_radius: float | None = None
            max_radius: float | None = None
            for leaf_id in leaf_ids:
                rect = self._label_items[leaf_id].sceneBoundingRect().adjusted(-4.0, -4.0, 4.0, 4.0)
                corners = [rect.topLeft(), rect.topRight(), rect.bottomLeft(), rect.bottomRight()]
                for corner in corners:
                    dx = corner.x() - center.x()
                    dy = corner.y() - center.y()
                    radius = math.hypot(dx, dy)
                    min_radius = radius if min_radius is None else min(min_radius, radius)
                    max_radius = radius if max_radius is None else max(max_radius, radius)
            slot_bounds = self._group_angle_bounds(context, group, angle_for, angle_step)
            if slot_bounds is None or None in {min_radius, max_radius}:
                return None
            return slot_bounds[0], slot_bounds[1], min_radius, max_radius

        for group in self._annotation_state.leaf_groups:
            if not group.background_enabled or not group.background_color_start:
                continue
            label_bounds = label_polar_bounds(group)
            bounds = self._group_angle_bounds(context, group, angle_for, angle_step)
            if bounds is None:
                continue
            start_bound, end_bound = bounds
            target_outer = uniform_outer_radius if uniform_outer_radius is not None else ((label_bounds[3] + 8.0) if label_bounds is not None else label_outer_radius)
            if group.background_scope == "full":
                sector_inner = max(8.0, inner_radius - 2.0)
                sector_outer = max(label_outer_radius, target_outer)
            else:
                if label_bounds is not None:
                    sector_inner = max(inner_radius, label_bounds[2] - 8.0)
                    sector_outer = target_outer
                else:
                    sector_inner = label_inner_radius
                    sector_outer = max(label_outer_radius, target_outer)
            poly = self._annular_sector_polygon(center, sector_inner, sector_outer, start_bound, end_bound)
            item = QGraphicsPolygonItem(poly)
            if group.background_color_end and group.background_color_end != group.background_color_start:
                gradient = QRadialGradient(center, max(1.0, sector_outer))
                c1 = QColor(group.background_color_start)
                c2 = QColor(group.background_color_end)
                c1.setAlphaF(0.2)
                c2.setAlphaF(0.2)
                inner_ratio = max(0.0, min(1.0, sector_inner / max(1.0, sector_outer)))
                gradient.setColorAt(0.0, c1)
                gradient.setColorAt(inner_ratio, c1)
                gradient.setColorAt(1.0, c2)
                item.setBrush(QBrush(gradient))
            else:
                color = QColor(group.background_color_start)
                color.setAlphaF(0.2)
                item.setBrush(QBrush(color))
            item.setPen(QPen(Qt.GlobalColor.transparent))
            item.setZValue(-20)
            self._scene.addItem(item)

    def _group_lane_x_positions(self) -> dict[int, float]:
        if not self._annotation_state.leaf_groups:
            return {}
        font = QFont(self._render_options.font_family, max(9, self._render_options.font_size - 1))
        metrics = QFontMetricsF(font)
        lane_widths: dict[int, float] = {}
        for group in self._annotation_state.leaf_groups:
            if not group.show_marker:
                continue
            lane_widths[group.level] = max(lane_widths.get(group.level, 0.0), metrics.horizontalAdvance(group.name) + 20.0)
        x_positions: dict[int, float] = {}
        cursor = 20.0
        for level in sorted(lane_widths):
            x_positions[level] = cursor
            cursor += lane_widths[level] + 12.0
        return x_positions

    def _draw_groups_rectangular(self, context: RenderContext, label_right_edge: float, margin_top: float, content_height: float, y_scale: float) -> None:
        if not self._annotation_state.leaf_groups:
            return
        lane_offsets = self._group_lane_x_positions()
        align_labels = self._render_options.align_tip_labels or self._render_options.ignore_branch_lengths

        def group_label_rect(group) -> QRectF | None:
            leaf_ids = [leaf_id for leaf_id in (group.leaf_ids or []) if leaf_id in self._label_items]
            if not leaf_ids and 0 <= group.start_leaf_index <= group.end_leaf_index < len(context.leaf_order):
                leaf_ids = [leaf_id for leaf_id in context.leaf_order[group.start_leaf_index : group.end_leaf_index + 1] if leaf_id in self._label_items]
            rect: QRectF | None = None
            for leaf_id in leaf_ids:
                item_rect = self._label_items[leaf_id].sceneBoundingRect()
                rect = item_rect if rect is None else rect.united(item_rect)
            return rect

        def py(index: int) -> float:
            if context.max_y <= 0:
                return margin_top + content_height / 2.0
            return margin_top + float(index) * y_scale

        for group in self._annotation_state.leaf_groups:
            if not group.show_marker:
                continue
            label_rect = group_label_rect(group)
            base_x = label_right_edge if align_labels or label_rect is None else label_rect.right() + 12.0
            x = base_x + lane_offsets.get(group.level, 20.0)
            y0, y1 = py(group.start_leaf_index), py(group.end_leaf_index)
            if group.start_leaf_index == group.end_leaf_index:
                leaf_id = group.leaf_ids[0] if group.leaf_ids else context.leaf_order[group.start_leaf_index]
                tip_item = self._label_items.get(leaf_id)
                if tip_item is not None:
                    rect = tip_item.sceneBoundingRect()
                    y0, y1 = rect.top(), rect.bottom()
            self._create_group_annotation_item(group, x, y0, y1)

    def _draw_groups_circular(self, context: RenderContext, center: QPointF, outer_radius: float, angle_for, angle_step: float) -> None:
        if not self._annotation_state.leaf_groups:
            return
        lane_offsets = self._group_lane_x_positions()
        max_label_radius = outer_radius + 80.0
        for item in self._label_items.values():
            rect = item.sceneBoundingRect()
            corners = [rect.topLeft(), rect.topRight(), rect.bottomLeft(), rect.bottomRight()]
            for corner in corners:
                max_label_radius = max(max_label_radius, math.hypot(corner.x() - center.x(), corner.y() - center.y()))
        base_radius = max_label_radius + 40.0
        for group in self._annotation_state.leaf_groups:
            if not group.show_marker:
                continue
            bounds = self._group_angle_bounds(context, group, angle_for, angle_step)
            if bounds is None:
                continue
            start_angle, end_angle = bounds
            radius = base_radius + lane_offsets.get(group.level, 20.0)
            self._create_group_annotation_item_circular(group, center, radius, start_angle, end_angle)

    def _create_group_annotation_item(self, group, x: float, y0: float, y1: float) -> None:
        color = QColor(group.color)
        font = QFont(self._render_options.font_family, max(9, self._render_options.font_size - 1))
        html_text = group.rich_html if group.rich_html else self._plain_text_html(group.name)
        item = GroupAnnotationItem(group.group_id, self._emit_group_moved)
        item.setData(0, group.group_id)
        item.setData(1, ("group", group.group_id))
        bar_width = max(2.0, float(self._render_options.group_line_width))
        label = MovableTextItem(
            group.group_id,
            lambda *_: None,
            lambda gid, plain, html: self.groupEdited.emit(gid, plain, html),
            movable=False,
            on_editing_changed=item.set_editing,
        )
        label.setParentItem(item)
        label.setData(0, group.group_id)
        label.setData(1, ("group", group.group_id))
        label.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, True)
        label.document().setDefaultFont(font)
        label.setDefaultTextColor(color)
        label.setHtml(html_text)
        label.adjustSize()
        line_height = max(1.0, abs(y1 - y0))
        label_height = max(1.0, label.boundingRect().height())
        content_height = max(line_height, label_height)
        line_y = (content_height - line_height) / 2.0
        label_y = (content_height - label_height) / 2.0
        line = QGraphicsRectItem(-bar_width / 2.0, line_y, bar_width, line_height, item)
        line.setBrush(QBrush(color))
        line.setPen(QPen(Qt.GlobalColor.transparent))
        line.setData(1, ("group", group.group_id))
        label.setPos(max(8.0, bar_width / 2.0 + 6.0), label_y)
        width = max(14.0, label.pos().x() + label.boundingRect().width() + 6.0)
        item.setRect(-bar_width / 2.0 - 2.0, 0.0, width + 2.0, max(1.0, content_height))
        top = min(y0, y1) - line_y
        self._group_anchor_pos[group.group_id] = QPointF(x, top)
        offset = group.offset or (0.0, 0.0)
        item.setPos(x + float(offset[0]), top + float(offset[1]))
        self._group_items[group.group_id] = item
        self._group_label_items[group.group_id] = label
        self._group_html[group.group_id] = html_text
        self._group_font[group.group_id] = QFont(font)
        self._group_color[group.group_id] = QColor(color)
        self._scene.addItem(item)

    def _create_group_annotation_item_circular(self, group, center: QPointF, radius: float, start_angle: float, end_angle: float) -> None:
        color = QColor(group.color)
        font = QFont(self._render_options.font_family, max(9, self._render_options.font_size - 1))
        html_text = group.rich_html if group.rich_html else self._plain_text_html(group.name)
        item = GroupAnnotationItem(group.group_id, self._emit_group_moved)
        item.setData(0, group.group_id)
        item.setData(1, ("group", group.group_id))
        mid_angle = (start_angle + end_angle) / 2.0
        anchor_radius = radius
        anchor = QPointF(center.x() + math.cos(mid_angle) * anchor_radius, center.y() + math.sin(mid_angle) * anchor_radius)
        polygon = self._annular_sector_polygon(center, radius, radius + max(2.0, float(self._render_options.group_line_width)), start_angle, end_angle)
        local_polygon = QPolygonF([point - anchor for point in polygon])
        band = QGraphicsPolygonItem(local_polygon, item)
        band.setBrush(QBrush(color))
        band.setPen(QPen(Qt.GlobalColor.transparent))
        band.setData(1, ("group", group.group_id))
        label = MovableTextItem(
            group.group_id,
            lambda *_: None,
            lambda gid, plain, html: self.groupEdited.emit(gid, plain, html),
            movable=False,
            on_editing_changed=item.set_editing,
        )
        label.setParentItem(item)
        label.setData(0, group.group_id)
        label.setData(1, ("group", group.group_id))
        label.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, True)
        label.document().setDefaultFont(font)
        label.setDefaultTextColor(color)
        label.setHtml(html_text)
        label.adjustSize()
        text_radius = radius + max(2.0, float(self._render_options.group_line_width)) + 10.0
        text_anchor = QPointF(center.x() + math.cos(mid_angle) * text_radius, center.y() + math.sin(mid_angle) * text_radius) - anchor
        width = label.boundingRect().width()
        height = label.boundingRect().height()
        if math.cos(mid_angle) >= 0:
            label.setPos(text_anchor.x() + 4.0, text_anchor.y() - height / 2.0)
        else:
            label.setPos(text_anchor.x() - width - 4.0, text_anchor.y() - height / 2.0)
        bounds = item.childrenBoundingRect().adjusted(-4.0, -4.0, 4.0, 4.0)
        item.setRect(bounds)
        self._group_anchor_pos[group.group_id] = anchor
        offset = group.offset or (0.0, 0.0)
        item.setPos(anchor.x() + float(offset[0]), anchor.y() + float(offset[1]))
        self._group_items[group.group_id] = item
        self._group_label_items[group.group_id] = label
        self._group_html[group.group_id] = html_text
        self._group_font[group.group_id] = QFont(font)
        self._group_color[group.group_id] = QColor(color)
        self._scene.addItem(item)

    def _draw_collapsed_triangle_rect(self, node_id: str, x: float, y: float, span: float) -> None:
        color = QColor(self._render_options.collapsed_triangle_color)
        half = max(7.0, span / 2.0)
        tip_x = x
        base_x = x + 18.0
        tri = QGraphicsPolygonItem(QPolygonF([QPointF(tip_x, y), QPointF(base_x, y - half), QPointF(base_x, y + half)]))
        tri.setBrush(QBrush(color))
        tri.setPen(QPen(color))
        tri.setData(0, node_id)
        tri.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, True)
        self._scene.addItem(tri)

    def _draw_collapsed_triangle_circular(self, node_id: str, pt: QPointF) -> None:
        color = QColor(self._render_options.collapsed_triangle_color)
        tri = QGraphicsPolygonItem(QPolygonF([QPointF(pt.x(), pt.y() - 8), QPointF(pt.x() + 8, pt.y() + 8), QPointF(pt.x() - 8, pt.y() + 8)]))
        tri.setBrush(QBrush(color))
        tri.setPen(QPen(color))
        tri.setData(0, node_id)
        tri.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, True)
        self._scene.addItem(tri)

    def _draw_scale_bar(self, context: RenderContext, x_left: float, y: float) -> None:
        options = self._render_options
        if not options.scale_bar_visible or options.ignore_branch_lengths or context.max_x <= 0:
            return
        length = options.scale_bar_length if not options.scale_bar_auto else self._nice_scale(context.max_x)
        width = (length / context.max_x) * max(80.0, float(options.canvas_width) - 320.0)
        if width <= 0:
            return
        x = x_left if options.scale_bar_position == "left" else float(options.canvas_width) - width - 60
        pen = QPen(QColor(options.branch_color))
        pen.setWidthF(options.branch_width)
        font = QFont(options.font_family, max(9, options.font_size - 1))
        color = QColor("#111827")
        item = self._create_scale_bar_item(f"{length:g}", width, 8.0, font, color)
        main_line = QGraphicsLineItem(0.0, 0.0, width, 0.0, item)
        main_line.setPen(pen)
        left_tick = QGraphicsLineItem(0.0, -4.0, 0.0, 4.0, item)
        left_tick.setPen(pen)
        right_tick = QGraphicsLineItem(width, -4.0, width, 4.0, item)
        right_tick.setPen(pen)
        self._set_scale_bar_pos(item, x, y)
        self._scene.addItem(item)

    def _render_circular(self, model: TreeModel, context: RenderContext) -> None:
        options = self._render_options
        center = QPointF(float(options.canvas_width) / 2.0 + float(options.view_offset_x), float(options.canvas_height) / 2.0 + float(options.view_offset_y))
        outer_radius = min(options.canvas_width, options.canvas_height) / 2.0 - 90.0
        inner_radius = 26.0
        radius_span = max(50.0, outer_radius - inner_radius)
        max_x = context.max_x if context.max_x > 0 else 1.0
        start_angle = math.radians(float(options.circular_start_angle))
        gap_radians = math.radians(max(0.0, min(300.0, float(options.circular_gap_degrees))))
        angle_span = max(0.01, math.pi * 2 - gap_radians)

        def angle_for(node_id: str) -> float:
            idx = context.y_raw.get(node_id, 0.0)
            denom = max(1.0, float(max(len(context.leaf_order) - 1, 1)))
            return start_angle + (idx / denom) * angle_span

        angle_step = angle_span / max(1.0, float(max(len(context.leaf_order) - 1, 1)))

        def radius_for(node_id: str) -> float:
            return inner_radius + (context.x_raw.get(node_id, 0.0) / max_x) * radius_span

        def point_at(angle: float, radius: float) -> QPointF:
            return QPointF(center.x() + math.cos(angle) * radius, center.y() + math.sin(angle) * radius)

        coords: dict[str, QPointF] = {}
        for node in model.iter_nodes():
            a = angle_for(node.id)
            r = radius_for(node.id)
            coords[node.id] = point_at(a, r)

        for node in model.iter_nodes():
            if node.is_leaf() or node.collapsed:
                continue
            r0 = radius_for(node.id)
            angles = [angle_for(child.id) for child in node.children]
            arc_pen = QPen(QColor(self._annotation_state.branch_colors.get(node.id, options.branch_color)))
            arc_pen.setWidthF(options.branch_width)
            steps = max(8, len(node.children) * 6)
            for i in range(steps):
                a0 = min(angles) + (max(angles) - min(angles)) * i / steps
                a1 = min(angles) + (max(angles) - min(angles)) * (i + 1) / steps
                arc = self._scene.addLine(
                    center.x() + math.cos(a0) * r0,
                    center.y() + math.sin(a0) * r0,
                    center.x() + math.cos(a1) * r0,
                    center.y() + math.sin(a1) * r0,
                    arc_pen,
                )
                self._edge_visible_items.setdefault(node.id, []).append(arc)
            for child in node.children:
                child_angle = angle_for(child.id)
                p1 = coords[child.id]
                p0 = point_at(child_angle, r0)
                radial_pen = QPen(QColor(self._annotation_state.branch_colors.get(child.id, options.branch_color)))
                radial_pen.setWidthF(options.branch_width)
                radial = self._scene.addLine(p0.x(), p0.y(), p1.x(), p1.y(), radial_pen)
                self._edge_visible_items.setdefault(child.id, []).append(radial)
                hit = QGraphicsLineItem(p0.x(), p0.y(), p1.x(), p1.y())
                hit.setPen(QPen(QColor(0, 0, 0, 0), 10.0))
                hit.setData(0, child.id)
                hit.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, True)
                self._scene.addItem(hit)
                self._edge_items.setdefault(child.id, []).append(hit)
                if options.show_support_labels and child.support is not None and not child.is_leaf():
                    sup = self._create_node_label_item(child, f"{child.support:g}")
                    self._set_node_label_pos(child.id, sup, p1.x() + options.support_offset_x, p1.y() + options.support_offset_y)
                    sup.setData(0, child.id)
                    sup.setData(1, ("support", child.id))
                    sup.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, True)
                    self._scene.addItem(sup)
                    self._node_label_items[child.id] = sup
                    self._support_items[child.id] = sup

        for node in model.iter_nodes():
            pt = coords[node.id]
            if (options.show_node_circles or options.show_selected_node_circle) and node.id != model.root.id:
                diameter = max(2.0, float(options.node_circle_size))
                dot = QGraphicsEllipseItem(pt.x() - diameter / 2, pt.y() - diameter / 2, diameter, diameter)
                dot.setBrush(QBrush(QColor(options.node_circle_color)))
                dot.setPen(QPen(Qt.GlobalColor.transparent))
                dot.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, True)
                dot.setData(0, node.id)
                self._scene.addItem(dot)
                self._node_items[node.id] = dot
                self._node_default_brush[node.id] = dot.brush()
            if node.collapsed:
                self._draw_collapsed_triangle_circular(node.id, pt)
            if options.show_tip_labels and (node.is_leaf() or node.collapsed):
                self._draw_tip_label_circular(node, pt, angle_for(node.id), outer_radius, center)
        self._draw_group_backgrounds_circular(context, center, inner_radius, outer_radius, angle_for, angle_step)
        self._draw_groups_circular(context, center, outer_radius, angle_for, angle_step)
        self._draw_scale_bar(context, 40.0 + float(options.view_offset_x), float(options.canvas_height) - 30.0 + float(options.view_offset_y))

    def _draw_tip_label_circular(self, node: TreeNode, pt: QPointF, angle: float, outer_radius: float, center: QPointF) -> None:
        options = self._render_options
        display_text, font, color = self._resolve_tip_label(node)
        html_text = self._label_html_provider(node) if self._label_html_provider else self._plain_text_html(display_text)
        label = self._create_tip_label_item(node.id, display_text, html_text, font, color)
        label.setData(0, node.id)
        label.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, True)
        align_labels = bool(options.align_tip_labels)
        anchor_radius = outer_radius + (28.0 if align_labels else 12.0)
        anchor = QPointF(center.x() + math.cos(angle) * anchor_radius, center.y() + math.sin(angle) * anchor_radius) if align_labels else QPointF(pt.x() + math.cos(angle) * 10.0, pt.y() + math.sin(angle) * 10.0)
        width = label.boundingRect().width()
        height = label.boundingRect().height()
        if options.circular_label_follow_branch:
            deg = math.degrees(angle)
            right_side = math.cos(angle) >= 0
            rotation = deg if right_side else deg + 180.0
            if right_side:
                label.setTransformOriginPoint(0.0, height / 2.0)
                self._set_tip_label_pos(node.id, label, anchor.x(), anchor.y() - height / 2.0)
            else:
                label.setTransformOriginPoint(width, height / 2.0)
                self._set_tip_label_pos(node.id, label, anchor.x() - width, anchor.y() - height / 2.0)
            label.setRotation(rotation)
        else:
            label.setRotation(0.0)
            label.setTransformOriginPoint(0.0, 0.0)
            lx = anchor.x()
            ly = anchor.y()
            if math.cos(angle) < 0:
                lx -= width
            self._set_tip_label_pos(node.id, label, lx, ly - height / 2)
        self._scene.addItem(label)
        self._label_items[node.id] = label
        if align_labels and options.show_leader_lines:
            tx = anchor.x()
            ty = anchor.y()
            pen = QPen(QColor(options.leader_line_color))
            pen.setWidthF(options.leader_line_width)
            pen.setStyle(Qt.PenStyle.DashLine)
            self._scene.addLine(pt.x(), pt.y(), tx, ty, pen)

    def _leaf_ids(self, node: TreeNode) -> list[str]:
        out: list[str] = []

        def walk(cur: TreeNode) -> None:
            if cur.is_leaf() or cur.collapsed:
                out.append(cur.id)
                return
            for child in cur.children:
                walk(child)

        walk(node)
        return out

    def _nice_scale(self, max_x: float) -> float:
        rough = max_x / 5.0
        magnitude = 10 ** math.floor(math.log10(max(rough, 1e-9)))
        normalized = rough / magnitude
        if normalized < 1.5:
            nice = 1.0
        elif normalized < 3.5:
            nice = 2.0
        elif normalized < 7.5:
            nice = 5.0
        else:
            nice = 10.0
        return nice * magnitude

    def scale_bar_default_text(self, model: TreeModel | None) -> str:
        if model is None:
            return ""
        options = self._render_options
        if options.ignore_branch_lengths:
            return ""
        context = self._collect_context(model)
        if context.max_x <= 0:
            return ""
        length = options.scale_bar_length if not options.scale_bar_auto else self._nice_scale(context.max_x)
        return f"{length:g}"

    def _max_visible_tip_label_width(self, model: TreeModel) -> float:
        width = 0.0
        for node in model.iter_nodes():
            if not (node.is_leaf() or node.collapsed):
                continue
            display_text, font, _ = self._resolve_tip_label(node)
            metrics = QFontMetricsF(font)
            lines = display_text.splitlines() or [display_text]
            width = max(width, max((metrics.horizontalAdvance(line) for line in lines), default=0.0))
        return width

    def clear_label_highlight(self) -> None:
        for node_id, item in self._label_items.items():
            item.setHtml(self._label_html.get(node_id, self._label_text.get(node_id, "")))
            item.setDefaultTextColor(self._label_color.get(node_id, QColor("#111827")))
            font = self._label_font.get(node_id)
            if font is not None:
                item.document().setDefaultFont(font)
        for node_id, item in self._node_label_items.items():
            item.setHtml(self._node_label_html.get(node_id, item.toPlainText()))
            item.setDefaultTextColor(self._node_label_color.get(node_id, QColor(self._render_options.support_color)))
            font = self._node_label_font.get(node_id)
            if font is not None:
                item.document().setDefaultFont(font)
        if self._scale_bar_label_item is not None:
            self._scale_bar_label_item.setHtml(self._scale_bar_html or self._scale_bar_label_item.toPlainText())
            self._scale_bar_label_item.setDefaultTextColor(self._scale_bar_color or QColor("#111827"))
            if self._scale_bar_font is not None:
                self._scale_bar_label_item.document().setDefaultFont(self._scale_bar_font)
        for group_id, item in self._group_label_items.items():
            item.setHtml(self._group_html.get(group_id, item.toPlainText()))
            item.setDefaultTextColor(self._group_color.get(group_id, QColor("#374151")))
            font = self._group_font.get(group_id)
            if font is not None:
                item.document().setDefaultFont(font)

    def highlight_labels_contains(self, query: str) -> int:
        q = (query or "").strip().lower()
        self.clear_label_highlight()
        if not q:
            return 0
        hit = 0
        for node_id, item in self._label_items.items():
            if q in item.toPlainText().lower():
                item.setDefaultTextColor(QColor("#dc2626"))
                hit += 1
        return hit

    def _resolve_tip_label(self, node: TreeNode) -> tuple[str, QFont, QColor]:
        options = self._render_options
        text = node.name or ""
        override = self._annotation_state.tip_style_overrides.get(text)
        display_text = override.display_text if override and override.display_text is not None else text
        font = QFont(options.font_family, options.font_size)
        font.setPointSizeF(float(options.font_size))
        font.setBold(False)
        if override:
            if override.font_family:
                font.setFamily(override.font_family)
            if override.font_size is not None:
                font.setPointSizeF(float(override.font_size))
            if override.bold is not None:
                font.setBold(bool(override.bold))
        color = QColor(override.color) if override and override.color else QColor("#111827")
        return display_text, font, color

    def _plain_text_html(self, text: str) -> str:
        return html.escape(text or "").replace("\n", "<br/>")

    def _create_tip_label_item(self, node_id: str, display_text: str, html_text: str, font: QFont, color: QColor) -> QGraphicsTextItem:
        label = MovableTextItem(
            node_id,
            lambda nid, x, y: self._emit_tip_label_moved(nid, x, y),
            lambda nid, plain, html: self.tipLabelEdited.emit(nid, plain, html),
        )
        label.document().setDefaultFont(font)
        label.setDefaultTextColor(color)
        label.setHtml(html_text)
        label.adjustSize()
        self._label_text[node_id] = display_text
        self._label_html[node_id] = html_text
        self._label_font[node_id] = QFont(font)
        self._label_color[node_id] = QColor(color)
        return label

    def _create_node_label_item(self, node: TreeNode, default_text: str) -> QGraphicsTextItem:
        override = self._annotation_state.node_label_overrides.get(node.id)
        display_text = override.display_text if override and override.display_text is not None else default_text
        font = QFont(self._render_options.font_family, self._render_options.support_font_size)
        color = QColor(self._render_options.support_color)
        html_text = self._plain_text_html(display_text)
        if override and override.rich_html:
            html_text = override.rich_html
        label = MovableTextItem(
            node.id,
            lambda nid, x, y: self._emit_node_label_moved(nid, x, y),
            lambda nid, plain, html: self.nodeLabelEdited.emit(nid, plain, html),
        )
        label.document().setDefaultFont(font)
        label.setDefaultTextColor(color)
        label.setHtml(html_text)
        label.adjustSize()
        self._node_label_html[node.id] = html_text
        self._node_label_font[node.id] = QFont(font)
        self._node_label_color[node.id] = QColor(color)
        return label

    def _create_scale_bar_item(self, default_text: str, width: float, line_height: float, font: QFont, color: QColor) -> ScaleBarItem:
        override = self._annotation_state.scale_bar_label_override
        display_text = override.display_text if override and override.display_text is not None else default_text
        html_text = override.rich_html if override and override.rich_html else self._plain_text_html(display_text)
        item = ScaleBarItem(self._emit_scale_bar_moved)
        item.setData(0, SCALE_BAR_ID)
        label = MovableTextItem(
            SCALE_BAR_ID,
            lambda *_: None,
            lambda _nid, plain, html: self.scaleBarEdited.emit(plain, html),
            movable=False,
            on_editing_changed=item.set_editing,
        )
        label.setParentItem(item)
        label.setData(0, SCALE_BAR_ID)
        label.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, True)
        label.document().setDefaultFont(font)
        label.setDefaultTextColor(color)
        label.setHtml(html_text)
        label.adjustSize()
        self._scale_bar_width = width
        self._scale_bar_line_height = line_height
        label.document().contentsChanged.connect(self._refresh_scale_bar_layout)
        self._scale_bar_item = item
        self._scale_bar_label_item = label
        self._scale_bar_html = html_text
        self._scale_bar_font = QFont(font)
        self._scale_bar_color = QColor(color)
        self._refresh_scale_bar_layout()
        return item

    def _refresh_scale_bar_layout(self) -> None:
        if self._scale_bar_item is None or self._scale_bar_label_item is None:
            return
        self._scale_bar_label_item.adjustSize()
        width = self._scale_bar_width
        line_height = self._scale_bar_line_height
        label_rect = self._scale_bar_label_item.boundingRect()
        self._scale_bar_label_item.setPos(width / 2 - label_rect.width() / 2, line_height + 6.0)
        self._scale_bar_item.setRect(-8.0, -10.0, width + 16.0, line_height + label_rect.height() + 24.0)

    def _body_html(self, item: QGraphicsTextItem) -> str:
        html = item.toHtml()
        match = re.search(r"<body[^>]*>(.*)</body>", html, flags=re.IGNORECASE | re.DOTALL)
        if not match:
            return html
        return match.group(1).strip()

    def scale_bar_text_snapshot(self) -> tuple[str, str] | None:
        if self._scale_bar_label_item is None:
            return None
        return self._scale_bar_label_item.toPlainText(), self._body_html(self._scale_bar_label_item)

    def scale_bar_text_format(self) -> tuple[QFont, QColor] | None:
        if self._scale_bar_label_item is None:
            return None
        cursor = self._scale_bar_label_item.textCursor()
        fmt = cursor.charFormat()
        font = fmt.font()
        if not font.family() and self._scale_bar_font is not None:
            font = QFont(self._scale_bar_font)
        if font.pointSizeF() <= 0 and self._scale_bar_font is not None:
            font.setPointSizeF(self._scale_bar_font.pointSizeF())
        color = fmt.foreground().color()
        if not color.isValid():
            color = QColor(self._scale_bar_color or QColor("#111827"))
        return font, color

    def apply_scale_bar_text_format(
        self,
        *,
        font_family: str | None = None,
        font_size: float | None = None,
        bold: bool | None = None,
        italic: bool | None = None,
        color: QColor | None = None,
    ) -> tuple[str, str] | None:
        if self._scale_bar_label_item is None:
            return None
        cursor = self._scale_bar_label_item.textCursor()
        if not cursor.hasSelection():
            cursor.select(QTextCursor.SelectionType.Document)
            self._scale_bar_label_item.setTextCursor(cursor)
        fmt = QTextCharFormat()
        if font_family is not None:
            fmt.setFontFamily(font_family)
        if font_size is not None:
            fmt.setFontPointSize(float(font_size))
        if bold is not None:
            fmt.setFontWeight(QFont.Weight.Bold if bold else QFont.Weight.Normal)
        if italic is not None:
            fmt.setFontItalic(italic)
        if color is not None:
            fmt.setForeground(color)
        cursor.mergeCharFormat(fmt)
        self._scale_bar_label_item.mergeCurrentCharFormat(fmt)
        current = self.scale_bar_text_format()
        if current is not None:
            self._scale_bar_font, self._scale_bar_color = current
        self._refresh_scale_bar_layout()
        return self.scale_bar_text_snapshot()

    def _set_node_label_pos(self, node_id: str, label: QGraphicsTextItem, anchor_x: float, anchor_y: float) -> None:
        self._node_anchor_pos[node_id] = QPointF(anchor_x, anchor_y)
        offset = self._annotation_state.node_label_offsets.get(node_id)
        if offset is None:
            label.setPos(anchor_x, anchor_y)
            return
        label.setPos(anchor_x + offset[0], anchor_y + offset[1])

    def _emit_node_label_moved(self, node_id: str, x: float, y: float) -> None:
        anchor = self._node_anchor_pos.get(node_id)
        if anchor is None:
            self.nodeLabelMoved.emit(node_id, x, y)
            return
        self.nodeLabelMoved.emit(node_id, x - anchor.x(), y - anchor.y())

    def _set_tip_label_pos(self, node_id: str, label: QGraphicsTextItem, anchor_x: float, anchor_y: float) -> None:
        self._tip_anchor_pos[node_id] = QPointF(anchor_x, anchor_y)
        offset = self._annotation_state.tip_label_offsets.get(node_id)
        if offset is None:
            label.setPos(anchor_x, anchor_y)
            return
        label.setPos(anchor_x + offset[0], anchor_y + offset[1])

    def _emit_tip_label_moved(self, node_id: str, x: float, y: float) -> None:
        anchor = self._tip_anchor_pos.get(node_id)
        if anchor is None:
            self.tipLabelMoved.emit(node_id, x, y)
            return
        self.tipLabelMoved.emit(node_id, x - anchor.x(), y - anchor.y())

    def _set_scale_bar_pos(self, item: ScaleBarItem, anchor_x: float, anchor_y: float) -> None:
        self._scale_bar_anchor_pos = QPointF(anchor_x, anchor_y)
        offset = self._annotation_state.scale_bar_offset
        if offset is None:
            item.setPos(anchor_x, anchor_y)
            return
        item.setPos(anchor_x + offset[0], anchor_y + offset[1])

    def _emit_scale_bar_moved(self, x: float, y: float) -> None:
        anchor = self._scale_bar_anchor_pos
        if anchor is None:
            self.scaleBarMoved.emit(x, y)
            return
        self.scaleBarMoved.emit(x - anchor.x(), y - anchor.y())

    def _emit_group_moved(self, group_id: str, x: float, y: float) -> None:
        anchor = self._group_anchor_pos.get(group_id)
        if anchor is None:
            self.groupMoved.emit(group_id, x, y)
            return
        self.groupMoved.emit(group_id, x - anchor.x(), y - anchor.y())

    def _refresh_node_circle_visibility(self) -> None:
        show_all = self._render_options.show_node_circles
        show_selected = self._render_options.show_selected_node_circle
        for node_id, item in self._node_items.items():
            visible = show_all or (show_selected and node_id in self._current_selected_ids)
            item.setVisible(visible)

    def _update_highlight(self, selected_ids: set[str]) -> None:
        for node_id, item in self._node_items.items():
            brush = self._node_default_brush.get(node_id, QBrush(Qt.GlobalColor.black))
            if node_id in selected_ids:
                item.setBrush(QBrush(QColor("#facc15")))
                pen = QPen(QColor("#92400e"))
                pen.setWidthF(1.4)
                item.setPen(pen)
            else:
                item.setBrush(brush)
                item.setPen(QPen(Qt.GlobalColor.transparent))
        for node_id, items in self._edge_items.items():
            for edge in items:
                pen = edge.pen()
                if node_id in selected_ids:
                    pen.setColor(QColor("#2563eb"))
                    pen.setWidthF(max(2.0, pen.widthF()))
                else:
                    pen.setColor(QColor(0, 0, 0, 0))
                    pen.setWidthF(10.0)
                edge.setPen(pen)
        for node_id, items in self._edge_visible_items.items():
            for edge in items:
                pen = edge.pen()
                pen.setWidthF(max(2.0, float(self._render_options.branch_width)) if node_id in selected_ids else float(self._render_options.branch_width))
                edge.setPen(pen)
        if self._scale_bar_item is not None:
            if SCALE_BAR_ID in selected_ids:
                self._scale_bar_item.setPen(QPen(QColor("#2563eb"), 1.4))
                self._scale_bar_item.setBrush(QBrush(QColor(37, 99, 235, 25)))
            else:
                self._scale_bar_item.setPen(QPen(Qt.GlobalColor.transparent))
                self._scale_bar_item.setBrush(QBrush(Qt.GlobalColor.transparent))

    def _export_rect(self) -> QRectF:
        rect = self._scene.sceneRect()
        if rect.isNull():
            rect = self._scene.itemsBoundingRect()
        if rect.isNull():
            raise ValueError("当前没有可导出的内容")
        return rect

    def export_svg(self, path: str) -> None:
        rect = self._export_rect()
        gen = QSvgGenerator()
        gen.setFileName(path)
        export_rect = QRectF(0.0, 0.0, rect.width(), rect.height())
        gen.setSize(export_rect.size().toSize())
        gen.setViewBox(export_rect)
        gen.setTitle("PhyloTree Viewer Export")
        painter = QPainter(gen)
        painter.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.TextAntialiasing)
        self._scene.render(painter, target=export_rect, source=rect)
        painter.end()

    def export_png(self, path: str, scale: float = 2.0) -> None:
        rect = self._export_rect()
        w = max(1, int(rect.width() * scale))
        h = max(1, int(rect.height() * scale))
        img = QImage(w, h, QImage.Format.Format_ARGB32_Premultiplied)
        img.fill(QColor("white"))
        painter = QPainter(img)
        painter.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.TextAntialiasing)
        painter.scale(scale, scale)
        target = QRectF(0.0, 0.0, rect.width(), rect.height())
        self._scene.render(painter, target=target, source=rect)
        painter.end()
        if not img.save(path):
            raise ValueError("PNG 保存失败")

    def export_pdf(self, path: str) -> None:
        rect = self._export_rect()
        printer = QPrinter(QPrinter.PrinterMode.HighResolution)
        printer.setOutputFormat(QPrinter.OutputFormat.PdfFormat)
        printer.setOutputFileName(path)
        printer.setPageSize(QPageSize(QSizeF(max(1.0, rect.width()), max(1.0, rect.height())), QPageSize.Unit.Point, "PhyloTreeContent"))
        printer.setFullPage(True)
        painter = QPainter(printer)
        painter.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.TextAntialiasing)
        page = printer.pageRect(QPrinter.Unit.DevicePixel)
        target = QRectF(0.0, 0.0, page.width(), page.height())
        self._scene.render(painter, target=target, source=rect)
        painter.end()

    def set_message(self, text: str) -> None:
        self.clear()
        item = self._scene.addText(text)
        item.setDefaultTextColor(Qt.GlobalColor.darkGray)
        item.setPos(10, 10)
        self._scene.setSceneRect(QRectF(0.0, 0.0, float(self._render_options.canvas_width), float(self._render_options.canvas_height)))

    def wheelEvent(self, event) -> None:
        if event.angleDelta().y() == 0:
            return super().wheelEvent(event)
        self.scale(1.15 if event.angleDelta().y() > 0 else 1 / 1.15, 1.15 if event.angleDelta().y() > 0 else 1 / 1.15)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            item = self.scene().itemAt(self.mapToScene(event.position().toPoint()), self.transform())
            if item is None:
                self._in_rubberband = True
                self.setDragMode(QGraphicsView.DragMode.RubberBandDrag)
                return super().mousePressEvent(event)
        if event.button() == Qt.MouseButton.RightButton:
            item = self.scene().itemAt(self.mapToScene(event.position().toPoint()), self.transform())
            if item is not None:
                group_data = item.data(1)
                if isinstance(group_data, tuple) and len(group_data) == 2:
                    self.contextMenuRequested.emit(group_data[0], group_data[1], self.mapToGlobal(event.position().toPoint()))
                    return
                node_id = item.data(0)
                if isinstance(node_id, str) and node_id:
                    if node_id == SCALE_BAR_ID:
                        kind = "scale_bar"
                    else:
                        kind = "tip" if node_id in self._label_items else "node"
                    self.contextMenuRequested.emit(kind, node_id, self.mapToGlobal(event.position().toPoint()))
                    return
            self.contextMenuRequested.emit("canvas", "", self.mapToGlobal(event.position().toPoint()))
            return
        if event.button() == Qt.MouseButton.LeftButton:
            self._in_rubberband = False
            self.setDragMode(QGraphicsView.DragMode.NoDrag)
            item = self.scene().itemAt(self.mapToScene(event.position().toPoint()), self.transform())
            if item is not None:
                node_id = item.data(0)
                if isinstance(node_id, str) and node_id:
                    self.nodeClicked.emit(node_id)
        return super().mousePressEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self._in_rubberband:
            self._in_rubberband = False
            self.setDragMode(QGraphicsView.DragMode.NoDrag)
        return super().mouseReleaseEvent(event)

