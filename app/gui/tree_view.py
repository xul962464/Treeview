from __future__ import annotations

from dataclasses import asdict, dataclass
import html
import math
import re
from typing import Callable

from PySide6.QtCore import QPoint, QPointF, QRectF, QSignalBlocker, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QFontMetricsF, QImage, QLinearGradient, QPainter, QPen, QPolygonF, QTextCharFormat, QTextCursor, QTextOption
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
    canvas_width: int = 1600
    canvas_height: int = 1000
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
    support_offset_x: float = -20.0
    support_offset_y: float = -18.0
    view_offset_x: float = 0.0
    view_offset_y: float = 0.0
    leader_line_color: str = "#9ca3af"
    leader_line_width: float = 1.0
    branch_color: str = "#000000"
    branch_width: float = 1.2
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
        self._label_html_provider: Callable[[TreeNode], str] | None = None
        self._annotation_state = AnnotationState()
        self._last_model: TreeModel | None = None
        self._render_options = TreeRenderOptions()
        self._in_rubberband = False

    def clear(self) -> None:
        self._scene.clear()
        self._node_items.clear()
        self._edge_items.clear()
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

        def collect(node: TreeNode) -> None:
            if node.is_leaf() or node.collapsed:
                leaves.append(node)
                return
            for child in node.children:
                collect(child)

        collect(model.root)
        leaf_order = [node.id for node in leaves]
        leaf_index = {node_id: idx for idx, node_id in enumerate(leaf_order)}
        x_raw: dict[str, float] = {model.root.id: 0.0}
        y_raw: dict[str, float] = {}

        if self._render_options.ignore_branch_lengths:
            depth_raw: dict[str, float] = {model.root.id: 0.0}
            max_leaf_depth = 0.0

            def walk_depth(node: TreeNode, depth: float) -> None:
                nonlocal max_leaf_depth
                depth_raw[node.id] = depth
                if node.is_leaf() or node.collapsed:
                    y_raw[node.id] = float(leaf_index[node.id])
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
                if node.is_leaf() or node.collapsed:
                    y_raw[node.id] = float(leaf_index[node.id])
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

        self._draw_group_backgrounds_rectangular(context, label_column_left, label_column_width)
        self._draw_rectangular_backgrounds(model, px_x, px_y)
        edge_pen = QPen(QColor(options.branch_color))
        edge_pen.setWidthF(options.branch_width)

        def draw_edges(node: TreeNode) -> None:
            if node.is_leaf() or node.collapsed:
                return
            x0 = px_x(node.id)
            ys_ = [px_y(child.id) for child in node.children]
            if ys_:
                v = QGraphicsLineItem(x0, min(ys_), x0, max(ys_))
                v.setPen(edge_pen)
                self._scene.addItem(v)
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
                h = QGraphicsLineItem(x0, y1, x1, y1)
                h.setPen(edge_pen)
                h.setData(0, child.id)
                h.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, True)
                self._scene.addItem(h)
                hh = QGraphicsLineItem(x0, y1, x1, y1)
                hh.setPen(QPen(QColor(0, 0, 0, 0), 10.0))
                hh.setData(0, child.id)
                hh.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, True)
                self._scene.addItem(hh)
                self._edge_items.setdefault(child.id, []).extend([h, hh])
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
                self._draw_collapsed_triangle_rect(node.id, x, y)
            if options.show_tip_labels and (node.is_leaf() or node.collapsed):
                self._draw_tip_label_rect(node, x, y, label_column_left, align_labels)
            if not node.collapsed:
                for child in node.children:
                    draw_nodes(child)

        draw_edges(model.root)
        draw_nodes(model.root)
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

        def py(index: int) -> float:
            if context.max_y <= 0:
                return mt + ch / 2.0 + float(options.view_offset_y)
            return mt + float(index) * ys + float(options.view_offset_y)

        for group in self._annotation_state.leaf_groups:
            if not group.background_enabled or not group.background_color_start:
                continue
            y0, y1 = py(group.start_leaf_index) - 10, py(group.end_leaf_index) + 10
            x0 = label_column_left - 8 if group.background_scope == "label" else 20 + float(options.view_offset_x)
            width = (float(options.canvas_width) - x0 - 30) if group.background_scope == "full" else max(220.0, label_column_width + 16.0)
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

    def _group_lane_x_positions(self) -> dict[int, float]:
        if not self._annotation_state.leaf_groups:
            return {}
        font = QFont(self._render_options.font_family, max(9, self._render_options.font_size - 1))
        metrics = QFontMetricsF(font)
        lane_widths: dict[int, float] = {}
        for group in self._annotation_state.leaf_groups:
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

        def py(index: int) -> float:
            if context.max_y <= 0:
                return margin_top + content_height / 2.0
            return margin_top + float(index) * y_scale

        for group in self._annotation_state.leaf_groups:
            x = label_right_edge + lane_offsets.get(group.level, 20.0)
            y0, y1 = py(group.start_leaf_index), py(group.end_leaf_index)
            pen = QPen(QColor(group.color))
            pen.setWidthF(1.4)
            line = QGraphicsLineItem(x, y0, x, y1)
            line.setPen(pen)
            line.setData(1, ("group", group.group_id))
            self._scene.addItem(line)
            self._scene.addLine(x - 6, y0, x, y0, pen)
            self._scene.addLine(x - 6, y1, x, y1, pen)
            label = QGraphicsSimpleTextItem(group.name)
            label.setFont(QFont(self._render_options.font_family, max(9, self._render_options.font_size - 1)))
            label.setBrush(QBrush(QColor(group.color)))
            label.setPos(x + 4, (y0 + y1) / 2 - 8)
            label.setData(1, ("group", group.group_id))
            self._scene.addItem(label)

    def _draw_collapsed_triangle_rect(self, node_id: str, x: float, y: float) -> None:
        color = QColor(self._render_options.collapsed_triangle_color)
        tri = QGraphicsPolygonItem(QPolygonF([QPointF(x + 2, y), QPointF(x + 14, y - 7), QPointF(x + 14, y + 7)]))
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

        def angle_for(node_id: str) -> float:
            idx = context.y_raw.get(node_id, 0.0)
            denom = max(1.0, float(max(len(context.leaf_order) - 1, 1)))
            return -math.pi / 2 + (idx / denom) * math.pi * 2

        def radius_for(node_id: str) -> float:
            return inner_radius + (context.x_raw.get(node_id, 0.0) / max_x) * radius_span

        coords: dict[str, QPointF] = {}
        for node in model.iter_nodes():
            a = angle_for(node.id)
            r = radius_for(node.id)
            coords[node.id] = QPointF(center.x() + math.cos(a) * r, center.y() + math.sin(a) * r)

        pen = QPen(Qt.GlobalColor.black)
        pen.setWidthF(1.2)
        for node in model.iter_nodes():
            if node.is_leaf() or node.collapsed:
                continue
            r0 = radius_for(node.id)
            angles = [angle_for(child.id) for child in node.children]
            steps = max(8, len(node.children) * 6)
            for i in range(steps):
                a0 = min(angles) + (max(angles) - min(angles)) * i / steps
                a1 = min(angles) + (max(angles) - min(angles)) * (i + 1) / steps
                self._scene.addLine(center.x() + math.cos(a0) * r0, center.y() + math.sin(a0) * r0, center.x() + math.cos(a1) * r0, center.y() + math.sin(a1) * r0, pen)
            p0 = coords[node.id]
            for child in node.children:
                p1 = coords[child.id]
                self._scene.addLine(p0.x(), p0.y(), p1.x(), p1.y(), pen)
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
                self._draw_tip_label_circular(node, pt, angle_for(node.id), outer_radius)
        self._draw_scale_bar(context, 40.0 + float(options.view_offset_x), float(options.canvas_height) - 30.0 + float(options.view_offset_y))

    def _draw_tip_label_circular(self, node: TreeNode, pt: QPointF, angle: float, outer_radius: float) -> None:
        options = self._render_options
        display_text, font, color = self._resolve_tip_label(node)
        html_text = self._label_html_provider(node) if self._label_html_provider else self._plain_text_html(display_text)
        label = self._create_tip_label_item(node.id, display_text, html_text, font, color)
        label.setData(0, node.id)
        label.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, True)
        offset = 18.0 if options.align_tip_labels else 10.0
        lx = pt.x() + math.cos(angle) * offset
        ly = pt.y() + math.sin(angle) * offset
        if math.cos(angle) < 0:
            lx -= label.boundingRect().width()
        self._set_tip_label_pos(node.id, label, lx, ly - label.boundingRect().height() / 2)
        self._scene.addItem(label)
        self._label_items[node.id] = label
        if options.align_tip_labels and options.show_leader_lines:
            target_r = outer_radius + 14.0
            tx = float(options.canvas_width) / 2.0 + float(options.view_offset_x) + math.cos(angle) * target_r
            ty = float(options.canvas_height) / 2.0 + float(options.view_offset_y) + math.sin(angle) * target_r
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
                    if pen.color().alpha() == 0:
                        pen.setColor(QColor(0, 0, 0, 0))
                        pen.setWidthF(10.0)
                    else:
                        pen.setColor(Qt.GlobalColor.black)
                        pen.setWidthF(1.2)
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
        gen.setSize(rect.size().toSize())
        gen.setViewBox(rect)
        gen.setTitle("PhyloTree Viewer Export")
        painter = QPainter(gen)
        painter.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.TextAntialiasing)
        self._scene.render(painter, target=rect, source=rect)
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
        self._scene.render(painter, target=rect, source=rect)
        painter.end()
        if not img.save(path):
            raise ValueError("PNG 保存失败")

    def export_pdf(self, path: str) -> None:
        rect = self._export_rect()
        printer = QPrinter(QPrinter.PrinterMode.HighResolution)
        printer.setOutputFormat(QPrinter.OutputFormat.PdfFormat)
        printer.setOutputFileName(path)
        printer.setFullPage(True)
        painter = QPainter(printer)
        painter.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.TextAntialiasing)
        scale = min(printer.pageRect(QPrinter.Unit.Point).width() / rect.width(), printer.pageRect(QPrinter.Unit.Point).height() / rect.height())
        painter.scale(scale, scale)
        self._scene.render(painter, target=rect, source=rect)
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

