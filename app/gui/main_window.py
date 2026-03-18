from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, dataclass
import html
import json
import re
from pathlib import Path
from uuid import uuid4

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QColor, QFont, QKeySequence, QTextCharFormat
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFontComboBox,
    QFormLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QListWidget,
    QListWidgetItem,
    QTextEdit,
    QScrollArea,
    QSlider,
    QSpinBox,
    QSplitter,
    QSizePolicy,
    QToolBar,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from app.gui.annotation_state import AnnotationState, CladeHighlight, LeafGroupAnnotation, NodeLabelOverride, ScaleBarLabelOverride, TipStyleOverride
from app.gui.tree_view import SCALE_BAR_ID, TreeRenderOptions, TreeView
from app.phylo.model import TreeModel, TreeNode
from app.phylo.parse import load_trees
from app.phylo.style_config import LabelSpanRule, LabelStyle, TreeStyles, label_to_html, load_and_apply_config


BASIC_COLOR_SEQUENCE = [
    "#000000", "#8B0000", "#006400", "#A35C00", "#0F9D0F", "#9AA300", "#25D425", "#7ED321",
    "#0D1B8C", "#9C0A8C", "#0B4F6C", "#B565A7", "#0E9F8A", "#B3B39D", "#19D88B", "#A8F28A",
    "#1537D1", "#8A2BE2", "#2457FF", "#C07BFF", "#2F8BFF", "#9AA9FF", "#48D6FF", "#7FE7E7",
    "#204A87", "#FF1493", "#5B5F97", "#FF5A7A", "#5DA271", "#FFB07C", "#68E07A", "#F3F08D",
    "#4B0082", "#E600E6", "#5A54D6", "#E056FD", "#5DA9E9", "#E0A3F5", "#76E3EA", "#F4F4F4",
    "#1E90FF", "#FFD700", "#7CFC00", "#FFFF00", "#FF8C00", "#FF4500", "#ADFF2F", "#32CD32",
]


@dataclass
class HistoryState:
    model: TreeModel
    styles: TreeStyles | None
    annotations: AnnotationState
    selected_node_id: str | None
    selected_ids: list[str]
    id_seq: int
    render_options: TreeRenderOptions


class TipStyleDialog(QDialog):
    def __init__(self, name: str, current: TipStyleOverride | None, default_font: QFont, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("编辑样品文本")
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self._text_edit = QTextEdit(self)
        self._text_edit.setAcceptRichText(True)
        self._text_edit.setMinimumHeight(150)
        self._text_edit.setFont(default_font)
        initial_text = current.display_text if current and current.display_text is not None else name
        initial_html = self._build_initial_html(initial_text, current)
        if initial_html:
            self._text_edit.setHtml(initial_html)
        else:
            self._text_edit.setPlainText(initial_text)
        form.addRow("显示文本", self._text_edit)
        layout.addLayout(form)

        fmt_row = QHBoxLayout()
        self._fmt_font = NoWheelFontComboBox(self)
        self._fmt_font.currentFontChanged.connect(self._apply_selected_font_family)
        self._fmt_size = NoWheelSpinBox(self)
        self._fmt_size.setRange(6, 96)
        self._fmt_size.setValue(max(6, int(default_font.pointSizeF()) if default_font.pointSizeF() > 0 else 11))
        self._fmt_size.valueChanged.connect(self._apply_selected_font_size)
        self._btn_bold = QPushButton("加粗", self)
        self._btn_bold.setCheckable(True)
        self._btn_bold.toggled.connect(self._apply_selected_bold)
        self._btn_italic = QPushButton("斜体", self)
        self._btn_italic.setCheckable(True)
        self._btn_italic.toggled.connect(self._apply_selected_italic)
        self._btn_color = QPushButton("文字颜色", self)
        self._btn_color.clicked.connect(self._apply_selected_color)
        self._btn_clear = QPushButton("清除格式", self)
        self._btn_clear.clicked.connect(self._clear_formatting)
        fmt_row.addWidget(self._fmt_font)
        fmt_row.addWidget(self._fmt_size)
        fmt_row.addWidget(self._btn_bold)
        fmt_row.addWidget(self._btn_italic)
        fmt_row.addWidget(self._btn_color)
        fmt_row.addWidget(self._btn_clear)
        layout.addLayout(fmt_row)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel, self)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _build_initial_html(self, initial_text: str, current: TipStyleOverride | None) -> str | None:
        if current and current.rich_html:
            return current.rich_html
        if current is None:
            return None
        css = []
        if current.font_family:
            css.append(f"font-family:{current.font_family}")
        if current.font_size:
            css.append(f"font-size:{current.font_size}px")
        if current.color:
            css.append(f"color:{current.color}")
        if current.bold:
            css.append("font-weight:bold")
        if not css:
            return None
        safe_text = initial_text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("\n", "<br/>")
        return f'<span style="{";".join(css)}">{safe_text}</span>'

    def _merge_format_on_selection(self, formatter) -> None:
        cursor = self._text_edit.textCursor()
        if not cursor.hasSelection():
            cursor.select(cursor.SelectionType.WordUnderCursor)
            self._text_edit.setTextCursor(cursor)
        fmt = QTextCharFormat()
        formatter(fmt)
        cursor.mergeCharFormat(fmt)
        self._text_edit.mergeCurrentCharFormat(fmt)

    def _apply_selected_font_family(self, font: QFont) -> None:
        self._merge_format_on_selection(lambda fmt: fmt.setFontFamily(font.family()))

    def _apply_selected_font_size(self, size: int) -> None:
        self._merge_format_on_selection(lambda fmt: fmt.setFontPointSize(float(size)))

    def _apply_selected_bold(self, enabled: bool) -> None:
        self._merge_format_on_selection(lambda fmt: fmt.setFontWeight(QFont.Weight.Bold if enabled else QFont.Weight.Normal))

    def _apply_selected_italic(self, enabled: bool) -> None:
        self._merge_format_on_selection(lambda fmt: fmt.setFontItalic(enabled))

    def _apply_selected_color(self) -> None:
        color = QColorDialog.getColor(QColor("#111827"), self, "选择文字颜色")
        if not color.isValid():
            return
        self._merge_format_on_selection(lambda fmt: fmt.setForeground(color))

    def _clear_formatting(self) -> None:
        plain = self._text_edit.toPlainText()
        self._text_edit.setPlainText(plain)

    def _rich_body_html(self) -> str:
        html = self._text_edit.toHtml()
        match = re.search(r"<body[^>]*>(.*)</body>", html, flags=re.IGNORECASE | re.DOTALL)
        if not match:
            return html
        return match.group(1).strip()

    def get_values(self) -> tuple[str, str]:
        plain_text = self._text_edit.toPlainText()
        rich_html = self._rich_body_html()
        return plain_text, rich_html

class GroupDialog(QDialog):
    def __init__(self, parent=None, *, background_only: bool = False, initial_color: str | None = None) -> None:
        super().__init__(parent)
        self._background_only = background_only
        self.setWindowTitle("新增底色" if background_only else "新增分组")
        self.setMinimumWidth(360)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self._name_edit: QLineEdit | None = None
        initial_qcolor = QColor(initial_color) if initial_color else QColor("#374151")
        self._line_color = QColor(initial_qcolor)
        self._bg_start = QColor(initial_qcolor)
        self._bg_end = QColor(initial_qcolor)
        self._bg_start_custom = False
        self._bg_end_custom = False
        if not background_only:
            self._name_edit = QLineEdit(self)
            form.addRow("分组名称", self._name_edit)
            self._line_color_btn = QPushButton("选择分组颜色", self)
            self._line_color_btn.clicked.connect(self._choose_line_color)
            form.addRow("分组颜色", self._line_color_btn)
            self._bg_enabled = QCheckBox("增加标签底色", self)
            self._bg_enabled.setChecked(True)
            self._bg_enabled.toggled.connect(self._sync_background_controls)
            form.addRow(self._bg_enabled)
        else:
            self._line_color_btn = None
            self._bg_enabled = None
        self._bg_scope = NoWheelComboBox(self)
        self._bg_scope.addItems(["label", "full"])
        if not background_only:
            self._bg_scope.setCurrentText("full")
        form.addRow("底色范围", self._bg_scope)
        self._gradient_enabled = QCheckBox("使用渐变", self)
        if not background_only:
            self._gradient_enabled.setChecked(True)
        self._gradient_enabled.toggled.connect(self._on_gradient_toggled)
        form.addRow(self._gradient_enabled)
        self._bg_start_btn = QPushButton("底色起点颜色", self)
        self._bg_start_btn.clicked.connect(self._choose_bg_start)
        form.addRow("起点颜色", self._bg_start_btn)
        self._bg_end_btn = QPushButton("底色终点颜色", self)
        self._bg_end_btn.clicked.connect(self._choose_bg_end)
        form.addRow("终点颜色", self._bg_end_btn)
        layout.addLayout(form)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel, self)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self._sync_group_background_defaults(force=True)
        self._refresh_color_buttons()
        self._sync_background_controls()

    def _choose_line_color(self) -> None:
        color = QColorDialog.getColor(self._line_color, self, "选择分组颜色")
        if color.isValid():
            self._line_color = color
            self._sync_group_background_defaults()
            self._refresh_color_buttons()

    def _choose_bg_start(self) -> None:
        color = QColorDialog.getColor(self._bg_start, self, "选择底色起点颜色")
        if color.isValid():
            self._bg_start = color
            self._bg_start_custom = True
            self._refresh_color_buttons()

    def _choose_bg_end(self) -> None:
        color = QColorDialog.getColor(self._bg_end, self, "选择底色终点颜色")
        if color.isValid():
            self._bg_end = color
            self._bg_end_custom = True
            self._refresh_color_buttons()

    def _button_style_for_color(self, color: QColor) -> str:
        text_color = "#111827" if color.lightnessF() > 0.6 else "#f9fafb"
        return f"background-color:{color.name()}; color:{text_color};"

    def _refresh_color_buttons(self) -> None:
        if self._line_color_btn is not None:
            self._line_color_btn.setText(self._line_color.name())
            self._line_color_btn.setStyleSheet(self._button_style_for_color(self._line_color))
        self._bg_start_btn.setText(self._bg_start.name())
        self._bg_start_btn.setStyleSheet(self._button_style_for_color(self._bg_start))
        self._bg_end_btn.setText(self._bg_end.name())
        self._bg_end_btn.setStyleSheet(self._button_style_for_color(self._bg_end))

    def _sync_group_background_defaults(self, force: bool = False) -> None:
        if self._background_only:
            return
        if force or not self._bg_start_custom:
            self._bg_start = QColor("#ffffff") if self._gradient_enabled.isChecked() else QColor(self._line_color)
        if force or not self._bg_end_custom:
            self._bg_end = QColor(self._line_color)

    def _sync_background_controls(self) -> None:
        enabled = self._background_only or bool(self._bg_enabled and self._bg_enabled.isChecked())
        self._bg_scope.setEnabled(enabled)
        self._gradient_enabled.setEnabled(enabled)
        self._bg_start_btn.setEnabled(enabled)
        self._bg_end_btn.setEnabled(enabled and self._gradient_enabled.isChecked())

    def _on_gradient_toggled(self, checked: bool) -> None:
        if not self._background_only and not self._bg_start_custom:
            self._bg_start = QColor("#ffffff") if checked else QColor(self._line_color)
        if not self._background_only and not self._bg_end_custom:
            self._bg_end = QColor(self._line_color)
        self._refresh_color_buttons()
        self._sync_background_controls()

    def values(self) -> dict:
        if self._background_only:
            background_color_start = self._bg_start.name()
            background_color_end = self._bg_end.name() if self._gradient_enabled.isChecked() else None
        else:
            if self._gradient_enabled.isChecked():
                background_color_start = self._bg_start.name() if self._bg_start_custom else "#ffffff"
                background_color_end = self._bg_end.name() if self._bg_end_custom else self._line_color.name()
            else:
                background_color_start = self._bg_start.name() if self._bg_start_custom else self._line_color.name()
                background_color_end = None
        return {
            "name": self._name_edit.text().strip() if self._name_edit is not None else "",
            "color": self._line_color.name(),
            "background_enabled": True if self._background_only else bool(self._bg_enabled and self._bg_enabled.isChecked()),
            "background_scope": self._bg_scope.currentText(),
            "background_color_start": background_color_start,
            "background_color_end": background_color_end,
            "show_marker": not self._background_only,
        }


class BatchReplaceDialog(QDialog):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("批量替换名称")
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self._pattern_edit = QLineEdit(self)
        self._repl_edit = QLineEdit(self)
        self._ignore_case = QCheckBox("忽略大小写", self)
        form.addRow("匹配内容/正则", self._pattern_edit)
        form.addRow("替换为", self._repl_edit)
        form.addRow(self._ignore_case)
        layout.addLayout(form)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel, self)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def values(self) -> tuple[str, str, int]:
        flags = re.IGNORECASE if self._ignore_case.isChecked() else 0
        return self._pattern_edit.text(), self._repl_edit.text(), flags


class FocusWheelMixin:
    def wheelEvent(self, event) -> None:
        if self.hasFocus():
            super().wheelEvent(event)
            return
        event.ignore()


class NoWheelComboBox(FocusWheelMixin, QComboBox):
    pass


class NoWheelFontComboBox(FocusWheelMixin, QFontComboBox):
    pass


class NoWheelSpinBox(FocusWheelMixin, QSpinBox):
    pass


class NoWheelDoubleSpinBox(FocusWheelMixin, QDoubleSpinBox):
    pass


class ExportBundleDialog(QDialog):
    def __init__(self, initial_directory: str, initial_prefix: str, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("一键导出")
        self.setMinimumWidth(420)

        layout = QVBoxLayout(self)
        form = QFormLayout()

        self._directory_edit = QLineEdit(initial_directory, self)
        self._prefix_edit = QLineEdit(initial_prefix, self)

        browse_row = QWidget(self)
        browse_layout = QHBoxLayout(browse_row)
        browse_layout.setContentsMargins(0, 0, 0, 0)
        browse_layout.setSpacing(8)
        browse_layout.addWidget(self._directory_edit)
        browse_button = QPushButton("浏览...", self)
        browse_button.clicked.connect(self._browse_directory)
        browse_layout.addWidget(browse_button)

        form.addRow("输出目录", browse_row)
        form.addRow("文件前缀", self._prefix_edit)
        layout.addLayout(form)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel, self)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _browse_directory(self) -> None:
        base_dir = self._directory_edit.text().strip() or str(Path.home())
        directory = QFileDialog.getExistingDirectory(self, "选择输出目录", base_dir)
        if directory:
            self._directory_edit.setText(directory)

    def values(self) -> tuple[str, str]:
        return self._directory_edit.text().strip(), self._prefix_edit.text().strip()


class TrimmedDoubleSpinBox(NoWheelDoubleSpinBox):
    def textFromValue(self, value: float) -> str:
        text = f"{value:.6f}".rstrip("0").rstrip(".")
        return text if text else "0"


class CollapsibleSection(QWidget):
    def __init__(self, title: str, content: QWidget, expanded: bool = False, parent=None) -> None:
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        self._toggle = QToolButton(self)
        self._toggle.setText(title)
        self._toggle.setCheckable(True)
        self._toggle.setChecked(expanded)
        self._toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self._toggle.setArrowType(Qt.ArrowType.DownArrow if expanded else Qt.ArrowType.RightArrow)
        self._toggle.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._toggle.setStyleSheet(
            """
            QToolButton {
                background-color: #dbeafe;
                color: #1e3a8a;
                border: 1px solid #93c5fd;
                border-radius: 8px;
                padding: 10px 12px;
                font-weight: 600;
                text-align: left;
                min-width: 0;
            }
            QToolButton:hover {
                background-color: #bfdbfe;
            }
            """
        )
        self._toggle.clicked.connect(self._on_toggled)

        self._content = content
        self._content.setVisible(expanded)
        self._content.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        self._content.setStyleSheet(
            """
            QWidget {
                background-color: #f8fafc;
            }
            """
        )

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 10)
        layout.setSpacing(6)
        layout.addWidget(self._toggle)
        layout.addWidget(self._content)

    def _on_toggled(self, checked: bool) -> None:
        self._toggle.setArrowType(Qt.ArrowType.DownArrow if checked else Qt.ArrowType.RightArrow)
        self._content.setVisible(checked)


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("PhyloTree Viewer")
        self.resize(1500, 920)

        self._viewer = TreeView(self)
        self._current_tree_path: Path | None = None
        self._model: TreeModel | None = None
        self._styles: TreeStyles | None = None
        self._annotations = AnnotationState()
        self._selected_node_id: str | None = None
        self._selected_ids: list[str] = []
        self._last_search = ""
        self._id_seq = 0
        self._history_limit = 40
        self._undo_stack: list[HistoryState] = []
        self._redo_stack: list[HistoryState] = []
        self._syncing_controls = False
        self._view_drag_snapshot: HistoryState | None = None

        self._init_menu()
        self._init_toolbar()
        self._build_ui()
        self._viewer.set_message("请在“文件 -> 打开树文件”中选择 .nwk/.newick/.tre/.nex/.nexus 文件。")
        self._viewer.nodeClicked.connect(self._on_node_clicked)
        self._viewer.selectionChanged.connect(self._on_selection_changed)
        self._viewer.contextMenuRequested.connect(self._show_context_menu)
        self._viewer.nodeLabelMoved.connect(self._on_node_label_moved)
        self._viewer.nodeLabelEdited.connect(self._on_node_label_edited)
        self._viewer.tipLabelMoved.connect(self._on_tip_label_moved)
        self._viewer.tipLabelEdited.connect(self._on_tip_label_edited)
        self._viewer.scaleBarMoved.connect(self._on_scale_bar_moved)
        self._viewer.scaleBarEdited.connect(self._on_scale_bar_edited)
        self._viewer.groupMoved.connect(self._on_group_moved)
        self._viewer.groupEdited.connect(self._on_group_edited)
        self._viewer.insetOverviewMoved.connect(self._on_inset_overview_moved)
        self._viewer.insetOverviewScaleChanged.connect(self._on_inset_overview_scale_changed)
        self._sync_controls_from_options()
        self._sync_node_label_controls()
        self._sync_scale_bar_controls()
        self._set_actions_enabled(False)

    def _init_menu(self) -> None:
        file_menu = self.menuBar().addMenu("文件(&F)")
        open_action = QAction("打开树文件(&O)...", self)
        open_action.setShortcut(QKeySequence.StandardKey.Open)
        open_action.triggered.connect(self._open_tree_file)
        file_menu.addAction(open_action)
        reload_action = QAction("重新加载(&R)", self)
        reload_action.setShortcut(QKeySequence.Refresh)
        reload_action.triggered.connect(self._reload_current)
        file_menu.addAction(reload_action)
        file_menu.addSeparator()
        export_bundle_action = QAction("一键导出...", self)
        export_bundle_action.triggered.connect(self._export_bundle)
        file_menu.addAction(export_bundle_action)
        export_png_action = QAction("导出 PNG...", self)
        export_png_action.triggered.connect(self._export_png)
        file_menu.addAction(export_png_action)
        export_pdf_action = QAction("导出 PDF...", self)
        export_pdf_action.triggered.connect(self._export_pdf)
        file_menu.addAction(export_pdf_action)
        export_nwk_action = QAction("导出 NWK...", self)
        export_nwk_action.triggered.connect(self._export_nwk)
        file_menu.addAction(export_nwk_action)
        export_state_action = QAction("导出当前树状态...", self)
        export_state_action.triggered.connect(self._export_tree_state)
        file_menu.addAction(export_state_action)
        file_menu.addSeparator()
        exit_action = QAction("退出(&X)", self)
        exit_action.setShortcut(QKeySequence.StandardKey.Quit)
        exit_action.triggered.connect(self.close)
        file_menu.addAction(exit_action)

        edit_menu = self.menuBar().addMenu("编辑(&E)")
        self._act_undo = QAction("撤销(&U)", self)
        self._act_undo.setShortcut(QKeySequence.StandardKey.Undo)
        self._act_undo.triggered.connect(self._undo)
        edit_menu.addAction(self._act_undo)
        self._act_redo = QAction("重做(&Y)", self)
        self._act_redo.setShortcut(QKeySequence.StandardKey.Redo)
        self._act_redo.triggered.connect(self._redo)
        edit_menu.addAction(self._act_redo)
        edit_menu.addSeparator()
        search_action = QAction("搜索物种名(&S)...", self)
        search_action.setShortcut(QKeySequence.Find)
        search_action.triggered.connect(self._search_taxa)
        edit_menu.addAction(search_action)
        batch_replace_action = QAction("批量替换名称...", self)
        batch_replace_action.triggered.connect(self._batch_replace_names)
        edit_menu.addAction(batch_replace_action)
        quick_replace_action = QAction("一键替换登录号格式", self)
        quick_replace_action.triggered.connect(self._quick_replace_accession_format)
        edit_menu.addAction(quick_replace_action)
        quick_replace_underscore_action = QAction("一键替换下划线格式", self)
        quick_replace_underscore_action.triggered.connect(self._quick_replace_underscore_format)
        edit_menu.addAction(quick_replace_underscore_action)
        italic_all_action = QAction("一键斜体", self)
        italic_all_action.triggered.connect(self._italicize_all_tip_labels)
        edit_menu.addAction(italic_all_action)
        sort_tree_action = QAction("智能排序（长枝在下）", self)
        sort_tree_action.triggered.connect(self._sort_tree_by_topology_depth)
        edit_menu.addAction(sort_tree_action)
        auto_adjust_action = QAction("自动调整", self)
        auto_adjust_action.triggered.connect(self._auto_adjust_tree)
        edit_menu.addAction(auto_adjust_action)

        cfg_menu = self.menuBar().addMenu("配置(&C)")
        import_cfg_action = QAction("导入配置文件(&I)...", self)
        import_cfg_action.triggered.connect(self._import_config)
        cfg_menu.addAction(import_cfg_action)
        help_menu = self.menuBar().addMenu("帮助(&H)")
        config_help_action = QAction("配置文件格式说明", self)
        config_help_action.triggered.connect(self._show_config_help)
        help_menu.addAction(config_help_action)

    def _init_toolbar(self) -> None:
        tb = QToolBar("顶部工具栏", self)
        tb.setMovable(False)
        tb.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        self.addToolBar(Qt.ToolBarArea.TopToolBarArea, tb)
        tb.addAction(self._act_undo)
        tb.addAction(self._act_redo)
        tb.addSeparator()
        act_open = QAction("打开", self)
        act_open.triggered.connect(self._open_tree_file)
        tb.addAction(act_open)
        act_reload = QAction("重载", self)
        act_reload.triggered.connect(self._reload_current)
        tb.addAction(act_reload)
        act_search = QAction("搜索", self)
        act_search.triggered.connect(self._search_taxa)
        tb.addAction(act_search)
        act_export = QAction("一键导出", self)
        act_export.triggered.connect(self._export_bundle)
        tb.addAction(act_export)

    def _build_ui(self) -> None:
        splitter = QSplitter(Qt.Orientation.Horizontal, self)
        splitter.setChildrenCollapsible(False)
        splitter.addWidget(self._build_left_panel())
        splitter.addWidget(self._viewer)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([330, 1170])
        self.setCentralWidget(splitter)

    def _build_left_panel(self) -> QWidget:
        container = QWidget(self)
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        sections = [
            CollapsibleSection("布局", self._build_layout_page(), True, container),
            CollapsibleSection("树调整", self._build_tree_page(), False, container),
            CollapsibleSection("外观", self._build_appearance_page(), True, container),
            CollapsibleSection("样品标签", self._build_tip_label_page(), False, container),
            CollapsibleSection("节点标签", self._build_node_label_page(), False, container),
            CollapsibleSection("分组注释", self._build_group_page(), False, container),
            CollapsibleSection("比例尺", self._build_scale_bar_page(), False, container),
        ]
        for section in sections:
            layout.addWidget(section)
        layout.addStretch(1)
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setWidget(container)
        scroll.setMinimumWidth(300)
        return scroll

    def _build_layout_page(self) -> QWidget:
        page = QWidget(self)
        layout = QFormLayout(page)
        self._cmb_layout = NoWheelComboBox(self)
        self._cmb_layout.addItems(["rectangular", "circular"])
        self._cmb_layout.currentTextChanged.connect(self._on_layout_changed)
        layout.addRow("布局模式", self._cmb_layout)
        self._chk_ignore_lengths = QCheckBox("忽略枝长", self)
        self._chk_ignore_lengths.toggled.connect(self._on_ignore_lengths_changed)
        layout.addRow(self._chk_ignore_lengths)
        self._chk_align_labels = QCheckBox("样品名称右侧对齐", self)
        self._chk_align_labels.toggled.connect(self._on_align_labels_changed)
        layout.addRow(self._chk_align_labels)
        self._circular_start_angle_spin = NoWheelDoubleSpinBox(self)
        self._circular_start_angle_spin.setRange(-360.0, 360.0)
        self._circular_start_angle_spin.setSingleStep(5.0)
        self._circular_start_angle_spin.setSuffix("°")
        self._circular_start_angle_spin.valueChanged.connect(self._on_circular_start_angle_changed)
        layout.addRow("环形起始角度", self._circular_start_angle_spin)
        self._circular_gap_spin = NoWheelDoubleSpinBox(self)
        self._circular_gap_spin.setRange(0.0, 300.0)
        self._circular_gap_spin.setSingleStep(1.0)
        self._circular_gap_spin.setSuffix("°")
        self._circular_gap_spin.valueChanged.connect(self._on_circular_gap_changed)
        layout.addRow("环形留缝角度", self._circular_gap_spin)
        self._chk_circular_follow_branch = QCheckBox("环形文字跟随分支方向", self)
        self._chk_circular_follow_branch.toggled.connect(self._on_circular_follow_branch_changed)
        layout.addRow(self._chk_circular_follow_branch)
        self._width_slider, self._width_spin = self._make_slider_spin(400, 3200, self._on_width_changed)
        layout.addRow("宽度", self._wrap_slider_spin(self._width_slider, self._width_spin))
        self._height_slider, self._height_spin = self._make_slider_spin(280, 2400, self._on_height_changed)
        layout.addRow("高度", self._wrap_slider_spin(self._height_slider, self._height_spin))
        self._view_offset_x_spin = NoWheelDoubleSpinBox(self)
        self._view_offset_x_spin.setRange(-2000.0, 2000.0)
        self._view_offset_x_spin.setSingleStep(5.0)
        self._view_offset_x_spin.valueChanged.connect(self._on_view_offset_x_changed)
        layout.addRow("整体 X 偏移", self._view_offset_x_spin)
        self._view_offset_y_spin = NoWheelDoubleSpinBox(self)
        self._view_offset_y_spin.setRange(-2000.0, 2000.0)
        self._view_offset_y_spin.setSingleStep(5.0)
        self._view_offset_y_spin.valueChanged.connect(self._on_view_offset_y_changed)
        layout.addRow("整体 Y 偏移", self._view_offset_y_spin)
        self._chk_inset_overview = QCheckBox("显示拓扑副图", self)
        self._chk_inset_overview.toggled.connect(self._on_inset_overview_changed)
        layout.addRow(self._chk_inset_overview)
        self._btn_reset_inset_overview = QPushButton("重置副图位置/大小", self)
        self._btn_reset_inset_overview.clicked.connect(self._reset_inset_overview)
        layout.addRow(self._btn_reset_inset_overview)
        self._inset_branch_width_spin = NoWheelDoubleSpinBox(self)
        self._inset_branch_width_spin.setRange(0.1, 20.0)
        self._inset_branch_width_spin.setSingleStep(0.1)
        self._inset_branch_width_spin.valueChanged.connect(self._on_inset_branch_width_changed)
        layout.addRow("副图线条粗细", self._inset_branch_width_spin)
        return page

    def _build_tree_page(self) -> QWidget:
        page = QWidget(self)
        layout = QVBoxLayout(page)
        self._chk_reroot_on_top = QCheckBox("定根后选中分支在上", self)
        self._chk_reroot_on_top.setChecked(False)
        layout.addWidget(self._chk_reroot_on_top)
        self._btn_reroot = QPushButton("定根到选中节点", self)
        self._btn_reroot.clicked.connect(self._reroot_to_selected)
        layout.addWidget(self._btn_reroot)
        self._btn_rotate = QPushButton("交换子树", self)
        self._btn_rotate.clicked.connect(self._rotate_selected)
        layout.addWidget(self._btn_rotate)
        self._btn_collapse = QPushButton("折叠 / 展开", self)
        self._btn_collapse.clicked.connect(self._toggle_collapse_selected)
        layout.addWidget(self._btn_collapse)
        self._btn_collapse_color = QPushButton("设置折叠三角颜色", self)
        self._btn_collapse_color.clicked.connect(self._choose_collapsed_triangle_color)
        layout.addWidget(self._btn_collapse_color)
        self._btn_sort_tree = QPushButton("智能排序（长枝在下）", self)
        self._btn_sort_tree.clicked.connect(self._sort_tree_by_topology_depth)
        layout.addWidget(self._btn_sort_tree)
        self._btn_auto_adjust = QPushButton("自动调整", self)
        self._btn_auto_adjust.setStyleSheet("font-weight: 700;")
        self._btn_auto_adjust.clicked.connect(self._auto_adjust_tree)
        layout.addWidget(self._btn_auto_adjust)
        layout.addStretch(1)
        return page

    def _build_appearance_page(self) -> QWidget:
        page = QWidget(self)
        layout = QFormLayout(page)
        self._chk_show_leader_lines = QCheckBox("显示末端虚线", self)
        self._chk_show_leader_lines.toggled.connect(self._on_show_leader_lines_changed)
        layout.addRow(self._chk_show_leader_lines)
        self._font_combo = NoWheelFontComboBox(self)
        self._font_combo.currentFontChanged.connect(self._on_global_font_changed)
        layout.addRow("全局字体", self._font_combo)
        self._font_size_spin = NoWheelSpinBox(self)
        self._font_size_spin.setRange(6, 48)
        self._font_size_spin.valueChanged.connect(self._on_global_font_size_changed)
        layout.addRow("字体大小", self._font_size_spin)
        self._btn_leader_color = QPushButton("设置虚线颜色", self)
        self._btn_leader_color.clicked.connect(self._choose_leader_line_color)
        layout.addRow(self._btn_leader_color)
        self._leader_width_spin = NoWheelDoubleSpinBox(self)
        self._leader_width_spin.setRange(0.1, 10.0)
        self._leader_width_spin.setSingleStep(0.1)
        self._leader_width_spin.valueChanged.connect(self._on_leader_width_changed)
        layout.addRow("虚线粗细", self._leader_width_spin)
        self._btn_branch_color = QPushButton("设置全部分支颜色", self)
        self._btn_branch_color.clicked.connect(self._choose_branch_color)
        layout.addRow(self._btn_branch_color)
        self._btn_selected_branch_color = QPushButton("设置选中分支颜色", self)
        self._btn_selected_branch_color.clicked.connect(self._choose_selected_branch_color)
        layout.addRow(self._btn_selected_branch_color)
        self._branch_width_spin = NoWheelDoubleSpinBox(self)
        self._branch_width_spin.setRange(0.1, 10.0)
        self._branch_width_spin.setSingleStep(0.1)
        self._branch_width_spin.valueChanged.connect(self._on_branch_width_changed)
        layout.addRow("分支粗细", self._branch_width_spin)
        self._btn_search = QPushButton("搜索物种", self)
        self._btn_search.clicked.connect(self._search_taxa)
        layout.addRow(self._btn_search)
        self._btn_batch = QPushButton("批量替换名称", self)
        self._btn_batch.clicked.connect(self._batch_replace_names)
        layout.addRow(self._btn_batch)
        self._btn_import_config = QPushButton("导入配置文件", self)
        self._btn_import_config.clicked.connect(self._import_config)
        layout.addRow(self._btn_import_config)
        return page

    def _build_tip_label_page(self) -> QWidget:
        page = QWidget(self)
        layout = QFormLayout(page)
        self._chk_show_tip_labels = QCheckBox("显示样品名称", self)
        self._chk_show_tip_labels.toggled.connect(self._on_show_tip_labels_changed)
        layout.addRow(self._chk_show_tip_labels)
        btn = QPushButton("编辑选中样品文本", self)
        btn.clicked.connect(self._edit_selected_tip_font)
        layout.addRow(btn)
        return page

    def _build_node_label_page(self) -> QWidget:
        page = QWidget(self)
        layout = QFormLayout(page)
        self._chk_show_node_circles = QCheckBox("显示所有子节点圆圈", self)
        self._chk_show_node_circles.toggled.connect(self._on_show_node_circles_changed)
        layout.addRow(self._chk_show_node_circles)
        self._chk_show_selected_node_circle = QCheckBox("显示选中节点圆圈", self)
        self._chk_show_selected_node_circle.toggled.connect(self._on_show_selected_node_circle_changed)
        layout.addRow(self._chk_show_selected_node_circle)
        self._chk_show_support = QCheckBox("显示节点置信度", self)
        self._chk_show_support.toggled.connect(self._on_show_support_changed)
        layout.addRow(self._chk_show_support)
        self._support_size_spin = NoWheelSpinBox(self)
        self._support_size_spin.setRange(6, 32)
        self._support_size_spin.valueChanged.connect(self._on_support_size_changed)
        layout.addRow("置信度字号", self._support_size_spin)
        self._node_circle_size_spin = NoWheelDoubleSpinBox(self)
        self._node_circle_size_spin.setRange(2.0, 32.0)
        self._node_circle_size_spin.setSingleStep(0.2)
        self._node_circle_size_spin.valueChanged.connect(self._on_node_circle_size_changed)
        layout.addRow("圆圈大小", self._node_circle_size_spin)
        self._btn_node_circle_color = QPushButton("设置圆圈颜色", self)
        self._btn_node_circle_color.clicked.connect(self._choose_node_circle_color)
        layout.addRow(self._btn_node_circle_color)
        self._node_offset_x_spin = NoWheelDoubleSpinBox(self)
        self._node_offset_x_spin.setRange(-500.0, 500.0)
        self._node_offset_x_spin.setSingleStep(1.0)
        self._node_offset_x_spin.valueChanged.connect(self._on_node_offset_x_changed)
        layout.addRow("置信度全局 X 偏移", self._node_offset_x_spin)
        self._node_offset_y_spin = NoWheelDoubleSpinBox(self)
        self._node_offset_y_spin.setRange(-500.0, 500.0)
        self._node_offset_y_spin.setSingleStep(1.0)
        self._node_offset_y_spin.valueChanged.connect(self._on_node_offset_y_changed)
        layout.addRow("置信度全局 Y 偏移", self._node_offset_y_spin)
        btn_row = QHBoxLayout()
        self._btn_reset_node_offset = QPushButton("重置偏移", self)
        self._btn_reset_node_offset.clicked.connect(self._reset_selected_node_label_offset)
        btn_row.addWidget(self._btn_reset_node_offset)
        self._btn_reset_node_text = QPushButton("还原默认文本", self)
        self._btn_reset_node_text.clicked.connect(self._reset_selected_node_label_text)
        btn_row.addWidget(self._btn_reset_node_text)
        layout.addRow(btn_row)
        hint = QLabel("双击主视图中的节点标签可直接编辑；拖动后这里的 offset 会同步更新。", self)
        hint.setWordWrap(True)
        layout.addRow(hint)
        return page

    def _build_group_page(self) -> QWidget:
        page = QWidget(self)
        layout = QVBoxLayout(page)
        btn_group = QPushButton("将选中样品新增为分组", self)
        btn_group.clicked.connect(self._create_group_from_selection)
        layout.addWidget(btn_group)
        btn_group_bg = QPushButton("将选中样品新增底色", self)
        btn_group_bg.clicked.connect(self._create_background_from_selection)
        layout.addWidget(btn_group_bg)
        btn_group_parent = QPushButton("将选中分组合并为上级分组", self)
        btn_group_parent.clicked.connect(self._create_group_from_selected_groups)
        layout.addWidget(btn_group_parent)
        btn_highlight = QPushButton("为选中节点设置子树底色", self)
        btn_highlight.clicked.connect(self._set_selected_clade_highlight)
        layout.addWidget(btn_highlight)
        width_row = QHBoxLayout()
        width_row.addWidget(QLabel("分组竖线宽度", self))
        self._group_line_width_spin = NoWheelDoubleSpinBox(self)
        self._group_line_width_spin.setRange(1.0, 12.0)
        self._group_line_width_spin.setSingleStep(0.2)
        self._group_line_width_spin.valueChanged.connect(self._on_group_line_width_changed)
        width_row.addWidget(self._group_line_width_spin)
        layout.addLayout(width_row)
        self._group_list = QListWidget(self)
        self._group_list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        layout.addWidget(self._group_list)
        btn_row = QHBoxLayout()
        btn_rename_group = QPushButton("重命名选中分组", self)
        btn_rename_group.clicked.connect(self._rename_selected_group)
        btn_delete_group = QPushButton("删除选中分组", self)
        btn_delete_group.clicked.connect(self._delete_selected_group)
        btn_row.addWidget(btn_rename_group)
        btn_row.addWidget(btn_delete_group)
        layout.addLayout(btn_row)
        tip = QLabel("样品分组仍要求叶序连续；已有分组可多选后继续向右合并成上级分组。", self)
        tip.setWordWrap(True)
        layout.addWidget(tip)
        layout.addWidget(QLabel("右键分组标注可重命名、改色或删除。", self))
        layout.addStretch(1)
        return page

    def _build_scale_bar_page(self) -> QWidget:
        page = QWidget(self)
        layout = QFormLayout(page)
        self._chk_scale_bar = QCheckBox("显示 Scale Bar", self)
        self._chk_scale_bar.toggled.connect(self._on_scale_bar_visible_changed)
        layout.addRow(self._chk_scale_bar)
        self._chk_scale_auto = QCheckBox("自动计算长度", self)
        self._chk_scale_auto.toggled.connect(self._on_scale_bar_auto_changed)
        layout.addRow(self._chk_scale_auto)
        self._scale_length_spin = TrimmedDoubleSpinBox(self)
        self._scale_length_spin.setDecimals(6)
        self._scale_length_spin.setRange(0.000001, 1000000.0)
        self._scale_length_spin.setSingleStep(0.01)
        self._scale_length_spin.valueChanged.connect(self._on_scale_length_changed)
        layout.addRow("手动长度", self._scale_length_spin)
        self._cmb_scale_pos = NoWheelComboBox(self)
        self._cmb_scale_pos.addItems(["left", "right"])
        self._cmb_scale_pos.currentTextChanged.connect(self._on_scale_position_changed)
        layout.addRow("位置", self._cmb_scale_pos)
        self._scale_offset_x_spin = NoWheelDoubleSpinBox(self)
        self._scale_offset_x_spin.setRange(-1000.0, 1000.0)
        self._scale_offset_x_spin.setSingleStep(1.0)
        self._scale_offset_x_spin.valueChanged.connect(self._on_scale_offset_x_changed)
        layout.addRow("X 偏移", self._scale_offset_x_spin)
        self._scale_offset_y_spin = NoWheelDoubleSpinBox(self)
        self._scale_offset_y_spin.setRange(-1000.0, 1000.0)
        self._scale_offset_y_spin.setSingleStep(1.0)
        self._scale_offset_y_spin.valueChanged.connect(self._on_scale_offset_y_changed)
        layout.addRow("Y 偏移", self._scale_offset_y_spin)
        btn_row = QHBoxLayout()
        self._btn_reset_scale_offset = QPushButton("重置位置", self)
        self._btn_reset_scale_offset.clicked.connect(self._reset_scale_bar_offset)
        btn_row.addWidget(self._btn_reset_scale_offset)
        self._btn_reset_scale_text = QPushButton("还原默认文字", self)
        self._btn_reset_scale_text.clicked.connect(self._reset_scale_bar_text)
        btn_row.addWidget(self._btn_reset_scale_text)
        layout.addRow(btn_row)
        self._btn_edit_scale_text = QPushButton("编辑比例尺文字", self)
        self._btn_edit_scale_text.clicked.connect(self._edit_scale_bar_text)
        layout.addRow(self._btn_edit_scale_text)
        hint = QLabel("可在主视图中直接拖动比例尺；双击比例尺文字可进行富文本编辑。", self)
        hint.setWordWrap(True)
        layout.addRow(hint)
        return page

    def _make_slider_spin(self, minimum: int, maximum: int, callback) -> tuple[QSlider, QSpinBox]:
        slider = QSlider(Qt.Orientation.Horizontal, self)
        slider.setRange(minimum, maximum)
        slider.setSingleStep(10)
        slider.valueChanged.connect(callback)
        slider.sliderPressed.connect(self._begin_view_drag)
        slider.sliderReleased.connect(self._end_view_drag)
        spin = NoWheelSpinBox(self)
        spin.setRange(minimum, maximum)
        spin.setSingleStep(10)
        spin.setSuffix(" px")
        spin.valueChanged.connect(callback)
        return slider, spin

    def _wrap_slider_spin(self, slider: QSlider, spin: QSpinBox) -> QWidget:
        widget = QWidget(self)
        layout = QHBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(slider, 1)
        layout.addWidget(spin)
        return widget

    def _current_options(self) -> TreeRenderOptions:
        return self._viewer.get_render_options()

    def _sync_controls_from_options(self) -> None:
        options = self._current_options()
        self._syncing_controls = True
        self._cmb_layout.setCurrentText(options.layout_mode)
        self._chk_ignore_lengths.setChecked(options.ignore_branch_lengths)
        self._chk_align_labels.setChecked(options.align_tip_labels)
        self._circular_start_angle_spin.setValue(float(options.circular_start_angle))
        self._circular_gap_spin.setValue(float(options.circular_gap_degrees))
        self._chk_circular_follow_branch.setChecked(bool(options.circular_label_follow_branch))
        self._chk_show_tip_labels.setChecked(options.show_tip_labels)
        self._chk_show_node_circles.setChecked(options.show_node_circles)
        self._chk_show_selected_node_circle.setChecked(options.show_selected_node_circle)
        self._chk_show_leader_lines.setChecked(options.show_leader_lines)
        self._chk_show_support.setChecked(options.show_support_labels)
        self._font_combo.setCurrentFont(QFont(options.font_family))
        self._font_size_spin.setValue(options.font_size)
        self._support_size_spin.setValue(options.support_font_size)
        self._node_circle_size_spin.setValue(options.node_circle_size)
        self._node_offset_x_spin.setValue(options.support_offset_x)
        self._node_offset_y_spin.setValue(options.support_offset_y)
        self._group_line_width_spin.setValue(float(options.group_line_width))
        self._leader_width_spin.setValue(options.leader_line_width)
        self._branch_width_spin.setValue(options.branch_width)
        self._width_slider.setValue(options.canvas_width)
        self._width_spin.setValue(options.canvas_width)
        self._height_slider.setValue(options.canvas_height)
        self._height_spin.setValue(options.canvas_height)
        self._view_offset_x_spin.setValue(float(options.view_offset_x))
        self._view_offset_y_spin.setValue(float(options.view_offset_y))
        self._chk_inset_overview.setChecked(bool(options.inset_overview_enabled))
        self._inset_branch_width_spin.setValue(float(options.inset_overview_branch_width))
        self._chk_scale_bar.setChecked(options.scale_bar_visible)
        self._chk_scale_auto.setChecked(options.scale_bar_auto)
        self._scale_length_spin.setValue(max(0.000001, options.scale_bar_length))
        self._cmb_scale_pos.setCurrentText(options.scale_bar_position)
        self._scale_length_spin.setEnabled(not options.scale_bar_auto and self._scale_bar_range_available(options))
        self._syncing_controls = False
        self._sync_node_label_controls()
        self._sync_scale_bar_controls()

    def _make_label_provider(self, styles: TreeStyles | None):
        def provider(node: TreeNode) -> str:
            label = node.name or ""
            override = self._annotations.tip_style_overrides.get(label)
            display_text = override.display_text if override and override.display_text is not None else label
            html_text = html.escape(display_text).replace("\n", "<br/>")
            if styles is not None:
                lookup_keys = [label]
                if node.original_name and node.original_name not in lookup_keys:
                    lookup_keys.append(node.original_name)
                style_key = next(
                    (
                        key
                        for key in lookup_keys
                        if key in styles.label_style_by_taxon
                        or key in styles.label_spans_by_taxon
                        or key in styles.annotations_by_taxon
                    ),
                    label,
                )
                base = styles.label_style_by_taxon.get(style_key)
                spans = styles.label_spans_by_taxon.get(style_key) or []
                html_text = label_to_html(label, base, spans)
                ann = styles.annotations_by_taxon.get(style_key)
                if ann:
                    html_text = f'{html_text} <span style="color:#6b7280">{html.escape(ann)}</span>'
                if display_text != label:
                    html_text = html.escape(display_text).replace("\n", "<br/>")
            if override:
                css = []
                if override.color:
                    css.append(f"color:{override.color}")
                if override.font_family:
                    css.append(f"font-family:{override.font_family}")
                if override.font_size:
                    css.append(f"font-size:{override.font_size}px")
                if override.bold:
                    css.append("font-weight:bold")
                if override.rich_html:
                    html_text = override.rich_html
                    if css:
                        html_text = f'<span style="{";".join(css)}">{html_text}</span>'
                else:
                    if css:
                        html_text = f'<span style="{";".join(css)}">{html_text}</span>'
            return html_text

        return provider

    def _matching_config_keys(self, node: TreeNode) -> list[str]:
        keys: list[str] = []
        if node.name:
            keys.append(node.name)
        if node.original_name and node.original_name not in keys:
            keys.append(node.original_name)
        return keys

    def _summarize_config_application(self, before_leaf_names: dict[str, str], styles: TreeStyles) -> dict[str, int]:
        summary = {
            "renamed": 0,
            "label_styles": 0,
            "label_spans": 0,
            "annotations": 0,
        }
        if self._model is None:
            return summary
        for node in self._model.iter_nodes():
            if not node.is_leaf():
                continue
            before_name = before_leaf_names.get(node.id, "")
            after_name = node.name or ""
            if before_name != after_name:
                summary["renamed"] += 1
            keys = self._matching_config_keys(node)
            if any(key in styles.label_style_by_taxon for key in keys):
                summary["label_styles"] += 1
            if any(key in styles.label_spans_by_taxon for key in keys):
                summary["label_spans"] += 1
            if any(key in styles.annotations_by_taxon for key in keys):
                summary["annotations"] += 1
        return summary

    def _apply_state_to_viewer(self) -> None:
        self._viewer.set_annotation_state(self._annotations)
        self._viewer.set_label_html_provider(self._make_label_provider(self._styles))

    def _current_leaf_order(self) -> list[str]:
        if self._model is None:
            return []
        leaves: list[str] = []

        def walk(node: TreeNode) -> None:
            if node.is_leaf() or node.collapsed:
                leaves.append(node.id)
                return
            for child in node.children:
                walk(child)

        walk(self._model.root)
        return leaves

    def _group_map(self) -> dict[str, LeafGroupAnnotation]:
        return {group.group_id: group for group in self._annotations.leaf_groups}

    def _resolve_group_leaf_ids(
        self,
        group_id: str,
        groups: dict[str, LeafGroupAnnotation],
        cache: dict[str, list[str]],
        visiting: set[str],
    ) -> list[str]:
        if group_id in cache:
            return cache[group_id]
        if group_id in visiting:
            return []
        group = groups.get(group_id)
        if group is None:
            return []
        visiting.add(group_id)
        leaf_ids = list(dict.fromkeys(group.leaf_ids))
        for child_id in group.child_group_ids:
            leaf_ids.extend(self._resolve_group_leaf_ids(child_id, groups, cache, visiting))
        visiting.remove(group_id)
        cache[group_id] = list(dict.fromkeys(leaf_ids))
        return cache[group_id]

    def _normalize_leaf_groups(self) -> None:
        if self._model is None or not self._annotations.leaf_groups:
            return
        leaf_order = self._current_leaf_order()
        if not leaf_order:
            self._annotations.leaf_groups = []
            return
        leaf_index = {node_id: index for index, node_id in enumerate(leaf_order)}
        groups = self._group_map()
        cache: dict[str, list[str]] = {}
        normalized: list[LeafGroupAnnotation] = []

        for group in self._annotations.leaf_groups:
            resolved_ids = [node_id for node_id in self._resolve_group_leaf_ids(group.group_id, groups, cache, set()) if node_id in leaf_index]
            if not resolved_ids:
                continue
            indices = sorted({leaf_index[node_id] for node_id in resolved_ids})
            if indices != list(range(indices[0], indices[-1] + 1)):
                continue
            group.leaf_ids = [leaf_order[index] for index in indices]
            group.start_leaf_index = indices[0]
            group.end_leaf_index = indices[-1]
            normalized.append(group)

        normalized.sort(key=lambda group: (group.start_leaf_index, group.end_leaf_index, group.name.lower()))
        lane_groups: list[list[LeafGroupAnnotation]] = []
        group_by_id = {group.group_id: group for group in normalized}
        for group in normalized:
            base_level = 0
            if group.child_group_ids:
                base_level = max((group_by_id[child_id].level for child_id in group.child_group_ids if child_id in group_by_id), default=-1) + 1
            level = base_level
            while True:
                lane = lane_groups[level] if level < len(lane_groups) else []
                overlap = any(
                    not (group.end_leaf_index < other.start_leaf_index or group.start_leaf_index > other.end_leaf_index)
                    for other in lane
                )
                if not overlap:
                    break
                level += 1
            group.level = level
            while len(lane_groups) <= level:
                lane_groups.append([])
            lane_groups[level].append(group)

        normalized.sort(key=lambda group: (group.level, group.start_leaf_index, group.end_leaf_index, group.name.lower()))
        self._annotations.leaf_groups = normalized

    def _rerender_current_tree(self) -> None:
        if self._model is None:
            return
        self._normalize_leaf_groups()
        self._apply_state_to_viewer()
        selected_ids = list(self._selected_ids)
        if not selected_ids and self._selected_node_id:
            selected_ids = [self._selected_node_id]
        self._viewer.set_selected_ids(selected_ids)
        self._viewer.render_tree(self._model)
        self._viewer.clear_label_highlight()
        self._viewer.restore_selection(selected_ids)
        self._sync_node_label_controls()
        self._sync_scale_bar_controls()
        self._refresh_group_list()

    def _capture_history_state(self) -> HistoryState | None:
        if self._model is None:
            return None
        return HistoryState(
            model=deepcopy(self._model),
            styles=deepcopy(self._styles),
            annotations=deepcopy(self._annotations),
            selected_node_id=self._selected_node_id,
            selected_ids=list(self._selected_ids),
            id_seq=self._id_seq,
            render_options=self._current_options(),
        )

    def _push_undo_state(self, state: HistoryState | None) -> None:
        if state is None:
            return
        self._undo_stack.append(state)
        if len(self._undo_stack) > self._history_limit:
            self._undo_stack = self._undo_stack[-self._history_limit :]
        self._redo_stack.clear()
        self._update_history_actions()

    def _apply_history_state(self, state: HistoryState) -> None:
        self._model = deepcopy(state.model)
        self._styles = deepcopy(state.styles)
        self._annotations = deepcopy(state.annotations)
        self._selected_node_id = state.selected_node_id
        self._selected_ids = list(state.selected_ids)
        self._id_seq = state.id_seq
        self._viewer.set_render_options(state.render_options)
        self._sync_controls_from_options()
        self._rerender_current_tree()
        self._set_actions_enabled(self._model is not None)

    def _update_history_actions(self) -> None:
        self._act_undo.setEnabled(bool(self._undo_stack))
        self._act_redo.setEnabled(bool(self._redo_stack))

    def _reset_history(self) -> None:
        self._undo_stack.clear()
        self._redo_stack.clear()
        self._update_history_actions()

    def _undo(self) -> None:
        if not self._undo_stack or self._model is None:
            return
        current = self._capture_history_state()
        if current is not None:
            self._redo_stack.append(current)
        self._apply_history_state(self._undo_stack.pop())
        self._update_history_actions()
        self.statusBar().showMessage("已撤销上一步操作。", 4000)

    def _redo(self) -> None:
        if not self._redo_stack or self._model is None:
            return
        current = self._capture_history_state()
        if current is not None:
            self._undo_stack.append(current)
        self._apply_history_state(self._redo_stack.pop())
        self._update_history_actions()
        self.statusBar().showMessage("已重做上一步操作。", 4000)

    def _set_actions_enabled(self, enabled: bool) -> None:
        for widget in [self._btn_reroot, self._btn_rotate, self._btn_collapse, self._btn_sort_tree, self._btn_auto_adjust, self._btn_search, self._btn_batch, self._btn_import_config, self._btn_selected_branch_color, self._btn_reset_inset_overview]:
            widget.setEnabled(enabled)

    def _on_node_clicked(self, node_id: str) -> None:
        self._selected_node_id = node_id
        self.statusBar().showMessage(f"已选择节点：{node_id}", 3000)

    def _on_selection_changed(self, node_ids: list) -> None:
        self._selected_ids = list(node_ids)
        if node_ids:
            self._selected_node_id = node_ids[-1]
        self._sync_node_label_controls()
        self._sync_scale_bar_controls()
        self.statusBar().showMessage(f"已选中 {len(node_ids)} 个对象。", 3000)

    def _on_node_label_moved(self, node_id: str, x: float, y: float) -> None:
        if not self._model:
            return
        before = self._capture_history_state()
        self._annotations.node_label_offsets[node_id] = (x, y)
        self._push_undo_state(before)
        self._sync_node_label_controls()

    def _on_node_label_edited(self, node_id: str, plain_text: str, rich_html: str) -> None:
        if not self._model:
            return
        default_plain = self._node_default_label_text(node_id)
        normalized_rich = self._normalized_rich_html(rich_html, plain_text)
        desired_display = None if plain_text == default_plain and normalized_rich is None else plain_text
        current = self._annotations.node_label_overrides.get(node_id)
        current_display = current.display_text if current else None
        current_rich = current.rich_html if current and current.rich_html is not None else None
        if desired_display == current_display and normalized_rich == current_rich:
            return
        self._push_undo_state(self._capture_history_state())
        if desired_display is None and normalized_rich is None:
            self._annotations.node_label_overrides.pop(node_id, None)
        else:
            self._annotations.node_label_overrides[node_id] = NodeLabelOverride(
                node_id=node_id,
                display_text=desired_display,
                rich_html=normalized_rich,
            )
        self._rerender_current_tree()

    def _on_tip_label_moved(self, node_id: str, x: float, y: float) -> None:
        if not self._model:
            return
        before = self._capture_history_state()
        self._annotations.tip_label_offsets[node_id] = (x, y)
        self._push_undo_state(before)

    def _on_tip_label_edited(self, node_id: str, plain_text: str, rich_html: str) -> None:
        if not self._model:
            return
        node, _ = self._find_node_and_parent(self._model.root, node_id)
        if node is None or not node.name:
            return
        taxon_name = node.name
        normalized_rich = self._normalized_rich_html(rich_html, plain_text)
        desired_display = None if plain_text == taxon_name and normalized_rich is None else plain_text
        current = self._annotations.tip_style_overrides.get(taxon_name)
        current_display = current.display_text if current else None
        current_rich = current.rich_html if current and current.rich_html is not None else None
        if desired_display == current_display and normalized_rich == current_rich:
            return
        font_family = current.font_family if current else None
        font_size = current.font_size if current else None
        bold = current.bold if current else None
        color = current.color if current else None
        self._push_undo_state(self._capture_history_state())
        if desired_display is None and normalized_rich is None and not any(value is not None for value in (font_family, font_size, bold, color)):
            self._annotations.tip_style_overrides.pop(taxon_name, None)
        else:
            self._annotations.tip_style_overrides[taxon_name] = TipStyleOverride(
                taxon_name=taxon_name,
                font_family=font_family,
                font_size=font_size,
                bold=bold,
                color=color,
                display_text=desired_display,
                rich_html=normalized_rich,
            )
        self._rerender_current_tree()

    def _on_scale_bar_moved(self, x: float, y: float) -> None:
        if not self._model:
            return
        before = self._capture_history_state()
        self._annotations.scale_bar_offset = (x, y)
        self._push_undo_state(before)
        self._sync_scale_bar_controls()

    def _on_scale_bar_edited(self, plain_text: str, rich_html: str) -> None:
        if not self._model:
            return
        default_plain = self._scale_bar_default_text()
        normalized_rich = self._normalized_rich_html(rich_html, plain_text)
        desired_display = None if plain_text == default_plain and normalized_rich is None else plain_text
        current = self._annotations.scale_bar_label_override
        current_display = current.display_text if current else None
        current_rich = current.rich_html if current and current.rich_html is not None else None
        if desired_display == current_display and normalized_rich == current_rich:
            return
        self._push_undo_state(self._capture_history_state())
        if desired_display is None and normalized_rich is None:
            self._annotations.scale_bar_label_override = None
        else:
            self._annotations.scale_bar_label_override = ScaleBarLabelOverride(
                display_text=desired_display,
                rich_html=normalized_rich,
            )
        self._rerender_current_tree()

    def _on_group_moved(self, group_id: str, x: float, y: float) -> None:
        group = next((g for g in self._annotations.leaf_groups if g.group_id == group_id), None)
        if group is None:
            return
        before = self._capture_history_state()
        group.offset = (x, y)
        self._push_undo_state(before)
        self._refresh_group_list()

    def _on_group_edited(self, group_id: str, plain_text: str, rich_html: str) -> None:
        group = next((g for g in self._annotations.leaf_groups if g.group_id == group_id), None)
        if group is None:
            return
        normalized_rich = self._normalized_rich_html(rich_html, plain_text)
        current_rich = group.rich_html
        if group.name == plain_text and current_rich == normalized_rich:
            return
        before = self._capture_history_state()
        group.name = plain_text
        group.rich_html = normalized_rich
        self._push_undo_state(before)
        self._rerender_current_tree()

    def _edit_scale_bar_text(self) -> None:
        if not self._model:
            return
        current = self._annotations.scale_bar_label_override
        current_tip = None
        if current is not None:
            current_tip = TipStyleOverride(taxon_name="scale_bar", display_text=current.display_text, rich_html=current.rich_html)
        font = QFont(self._current_options().font_family, max(9, self._current_options().font_size - 1))
        dialog = TipStyleDialog("比例尺", current_tip, font, self)
        dialog.setWindowTitle("编辑比例尺文字")
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        display_text, rich_html = dialog.get_values()
        self._push_undo_state(self._capture_history_state())
        self._annotations.scale_bar_label_override = ScaleBarLabelOverride(display_text=display_text, rich_html=rich_html)
        self._rerender_current_tree()

    def _selected_support_node(self) -> TreeNode | None:
        if not self._model or not self._selected_node_id:
            return None
        node, _ = self._find_node_and_parent(self._model.root, self._selected_node_id)
        if node is None or node.id == self._model.root.id or node.support is None:
            return None
        return node

    def _sync_node_label_controls(self) -> None:
        if not hasattr(self, "_node_offset_x_spin"):
            return
        node = self._selected_support_node()
        options = self._current_options()
        self._syncing_controls = True
        self._node_offset_x_spin.setValue(float(options.support_offset_x))
        self._node_offset_y_spin.setValue(float(options.support_offset_y))
        self._node_offset_x_spin.setEnabled(options.show_support_labels)
        self._node_offset_y_spin.setEnabled(options.show_support_labels)
        self._node_circle_size_spin.setValue(float(options.node_circle_size))
        self._node_circle_size_spin.setEnabled(options.show_node_circles or options.show_selected_node_circle)
        self._btn_reset_node_offset.setEnabled(node is not None and node.id in self._annotations.node_label_offsets)
        self._btn_reset_node_text.setEnabled(node is not None and node.id in self._annotations.node_label_overrides)
        self._syncing_controls = False

    def _scale_bar_range_available(self, options: TreeRenderOptions | None = None) -> bool:
        options = options or self._current_options()
        if self._model is None:
            return False
        if not options.ignore_branch_lengths:
            return True
        return bool(options.inset_overview_enabled and options.layout_mode == "rectangular")

    def _scale_bar_available(self) -> bool:
        options = self._current_options()
        return self._model is not None and options.scale_bar_visible and not options.ignore_branch_lengths

    def _sync_scale_bar_controls(self) -> None:
        if not hasattr(self, "_scale_offset_x_spin"):
            return
        enabled = self._scale_bar_available()
        offset = self._annotations.scale_bar_offset or (0.0, 0.0)
        self._syncing_controls = True
        self._scale_offset_x_spin.setEnabled(enabled)
        self._scale_offset_y_spin.setEnabled(enabled)
        self._btn_reset_scale_offset.setEnabled(enabled and self._annotations.scale_bar_offset is not None)
        self._btn_reset_scale_text.setEnabled(enabled and self._annotations.scale_bar_label_override is not None)
        self._scale_offset_x_spin.setValue(float(offset[0]))
        self._scale_offset_y_spin.setValue(float(offset[1]))
        self._syncing_controls = False

    def _show_context_menu(self, kind: str, object_id: str, pos) -> None:
        if self._model is None:
            return
        if object_id:
            self._selected_node_id = object_id
        if kind == "support":
            kind = "node"
        if kind == "canvas" and self._selected_node_id:
            if self._selected_node_id == SCALE_BAR_ID:
                kind = "scale_bar"
            else:
                node, _ = self._find_node_and_parent(self._model.root, self._selected_node_id)
                if node is not None and node.is_leaf():
                    kind = "tip"
                else:
                    kind = "node"
        menu = QMenu(self)
        if kind in {"node", "tip"}:
            menu.addAction("定根到此节点", self._reroot_to_selected)
            menu.addAction("交换子树", self._rotate_selected)
            menu.addAction("折叠 / 展开", self._toggle_collapse_selected)
            menu.addSeparator()
            menu.addAction("设置子树底色", self._set_selected_clade_highlight)
        if kind == "node" and self._selected_support_node() is not None:
            menu.addSeparator()
            menu.addAction("重置节点标签偏移", self._reset_selected_node_label_offset)
            menu.addAction("还原默认节点标签", self._reset_selected_node_label_text)
        if kind == "tip":
            menu.addAction("编辑样品文本", self._edit_selected_tip_font)
            menu.addAction("新增底色", self._create_background_from_selection)
            if len(self._selected_tip_names()) >= 1 or bool(object_id):
                menu.addAction("新增分组", self._create_group_from_selection)
        if kind == "group":
            menu.addAction("重命名分组", lambda: self._rename_group(object_id))
            menu.addAction("修改分组颜色", lambda: self._recolor_group(object_id))
            menu.addAction("删除分组", lambda: self._delete_group(object_id))
        if kind == "scale_bar":
            menu.addAction("编辑比例尺文字", self._edit_scale_bar_text)
            menu.addAction("重置比例尺位置", self._reset_scale_bar_offset)
            menu.addAction("还原默认比例尺文字", self._reset_scale_bar_text)
        menu.exec(pos)

    def _open_tree_file(self) -> None:
        path_str, _ = QFileDialog.getOpenFileName(self, "打开树文件", "", "树文件 (*.nwk *.newick *.tre *.nex *.nexus *.json);;所有文件 (*.*)")
        if not path_str:
            return
        self._current_tree_path = Path(path_str)
        self._load_tree_into_viewer(self._current_tree_path)

    def _reload_current(self) -> None:
        if self._current_tree_path is not None:
            self._load_tree_into_viewer(self._current_tree_path)

    def _load_tree_into_viewer(self, path: Path) -> None:
        if path.suffix.lower() == ".json":
            try:
                state_data = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                state_data = None
            if isinstance(state_data, dict) and state_data.get("format") == "phylo-tree-state":
                self._load_tree_state(state_data, path)
                return
        try:
            loaded = load_trees(path)
        except Exception as exc:
            QMessageBox.critical(self, "解析失败", f"无法解析树文件：\n{path}\n\n{exc}")
            return
        model = loaded.trees[0]
        if len(loaded.trees) > 1:
            names = [f"{index}. {tree.name or f'tree_{index}'}" for index, tree in enumerate(loaded.trees, start=1)]
            choice, ok = QInputDialog.getItem(self, "选择树", f"该文件中检测到 {len(loaded.trees)} 棵树：", names, 0, False)
            if not ok:
                return
            model = loaded.trees[max(0, names.index(choice))]
        self._model = model
        self._styles = None
        self._annotations = AnnotationState()
        self._selected_ids = []
        self._selected_node_id = None
        self._reset_id_seq()
        self._reset_history()
        self._rerender_current_tree()
        self._set_actions_enabled(True)

    def _serialize_tree_node(self, node: TreeNode) -> dict:
        return {
            "id": node.id,
            "name": node.name,
            "original_name": node.original_name,
            "branch_length": node.branch_length,
            "support": node.support,
            "collapsed": node.collapsed,
            "children": [self._serialize_tree_node(child) for child in node.children],
        }

    def _serialize_annotation_state(self) -> dict:
        return {
            "tip_style_overrides": {k: asdict(v) for k, v in self._annotations.tip_style_overrides.items()},
            "node_label_overrides": {k: asdict(v) for k, v in self._annotations.node_label_overrides.items()},
            "scale_bar_label_override": asdict(self._annotations.scale_bar_label_override) if self._annotations.scale_bar_label_override else None,
            "clade_highlights": {k: asdict(v) for k, v in self._annotations.clade_highlights.items()},
            "branch_colors": self._annotations.branch_colors,
            "leaf_groups": [asdict(group) for group in self._annotations.leaf_groups],
            "node_label_offsets": self._annotations.node_label_offsets,
            "tip_label_offsets": self._annotations.tip_label_offsets,
            "scale_bar_offset": self._annotations.scale_bar_offset,
            "preserve_root_order": self._annotations.preserve_root_order,
            "next_group_color_index": self._annotations.next_group_color_index,
        }

    def _serialize_styles(self) -> dict | None:
        return asdict(self._styles) if self._styles is not None else None

    def _deserialize_styles(self, data: dict | None) -> TreeStyles | None:
        if not isinstance(data, dict):
            return None
        label_style_by_taxon: dict[str, LabelStyle] = {}
        for taxon, style_data in (data.get("label_style_by_taxon") or {}).items():
            if isinstance(style_data, dict):
                label_style_by_taxon[str(taxon)] = LabelStyle(**style_data)

        label_spans_by_taxon: dict[str, list[LabelSpanRule]] = {}
        for taxon, rules_data in (data.get("label_spans_by_taxon") or {}).items():
            rules: list[LabelSpanRule] = []
            if isinstance(rules_data, list):
                for rule_data in rules_data:
                    if not isinstance(rule_data, dict):
                        continue
                    style_data = rule_data.get("style") or {}
                    if not isinstance(style_data, dict):
                        style_data = {}
                    rules.append(
                        LabelSpanRule(
                            pattern=str(rule_data.get("pattern", "")),
                            style=LabelStyle(**style_data),
                            regex=bool(rule_data.get("regex", False)),
                            flags=int(rule_data.get("flags", 0)),
                        )
                    )
            label_spans_by_taxon[str(taxon)] = rules

        annotations_by_taxon = {str(k): str(v) for k, v in (data.get("annotations_by_taxon") or {}).items()}
        return TreeStyles(
            label_style_by_taxon=label_style_by_taxon,
            label_spans_by_taxon=label_spans_by_taxon,
            annotations_by_taxon=annotations_by_taxon,
        )

    def _deserialize_tree_node(self, data: dict) -> TreeNode:
        return TreeNode(
            id=str(data.get("id", self._new_id())),
            name=data.get("name"),
            original_name=data.get("original_name"),
            branch_length=data.get("branch_length"),
            support=data.get("support"),
            collapsed=bool(data.get("collapsed", False)),
            children=[self._deserialize_tree_node(child) for child in data.get("children", [])],
        )

    def _deserialize_annotation_state(self, data: dict) -> AnnotationState:
        return AnnotationState(
            tip_style_overrides={k: TipStyleOverride(**v) for k, v in (data.get("tip_style_overrides") or {}).items()},
            node_label_overrides={k: NodeLabelOverride(**v) for k, v in (data.get("node_label_overrides") or {}).items()},
            scale_bar_label_override=ScaleBarLabelOverride(**data["scale_bar_label_override"]) if data.get("scale_bar_label_override") else None,
            clade_highlights={k: CladeHighlight(**v) for k, v in (data.get("clade_highlights") or {}).items()},
            branch_colors={str(k): str(v) for k, v in (data.get("branch_colors") or {}).items()},
            leaf_groups=[LeafGroupAnnotation(**group) for group in (data.get("leaf_groups") or [])],
            node_label_offsets={k: tuple(v) for k, v in (data.get("node_label_offsets") or {}).items()},
            tip_label_offsets={k: tuple(v) for k, v in (data.get("tip_label_offsets") or {}).items()},
            scale_bar_offset=tuple(data["scale_bar_offset"]) if data.get("scale_bar_offset") is not None else None,
            preserve_root_order=bool(data.get("preserve_root_order", False)),
            next_group_color_index=int(data.get("next_group_color_index", 0)),
        )

    def _html_to_plain_text(self, rich_html: str) -> str:
        text = re.sub(r"<br\\s*/?>", "\n", rich_html, flags=re.IGNORECASE)
        text = re.sub(r"</p\\s*>", "\n", text, flags=re.IGNORECASE)
        text = re.sub(r"<[^>]+>", "", text)
        return html.unescape(text).replace("\xa0", " ").strip()

    def _normalized_rich_html(self, rich_html: str, plain_text: str) -> str | None:
        body = (rich_html or "").strip()
        if not body:
            return None
        text_plain = self._html_to_plain_text(body)
        markers = ("<span", "<img", "<a ", "font-family", "font-size", "font-style", "font-weight", "color:", "text-decoration")
        if text_plain == plain_text and not any(marker in body.lower() for marker in markers):
            return None
        return body

    def _node_default_label_text(self, node_id: str) -> str:
        if self._model is None:
            return ""
        node, _ = self._find_node_and_parent(self._model.root, node_id)
        if node is None or node.support is None:
            return ""
        return f"{node.support:g}"

    def _scale_bar_default_text(self) -> str:
        return self._viewer.scale_bar_default_text(self._model)

    def _load_tree_state(self, state_data: dict, path: Path) -> None:
        try:
            model_data = state_data["model"]
            annotations_data = state_data.get("annotations", {})
            options_data = state_data.get("render_options", {})
            styles_data = state_data.get("styles")
            self._model = TreeModel(root=self._deserialize_tree_node(model_data["root"]), name=model_data.get("name"))
            self._annotations = self._deserialize_annotation_state(annotations_data)
            self._styles = self._deserialize_styles(styles_data)
            self._selected_ids = []
            self._selected_node_id = None
            options = self._current_options()
            for key, value in options_data.items():
                if hasattr(options, key):
                    setattr(options, key, value)
            self._viewer.set_render_options(options)
            self._current_tree_path = path
            self._reset_id_seq()
            self._reset_history()
            self._sync_controls_from_options()
            self._rerender_current_tree()
            self._set_actions_enabled(True)
        except Exception as exc:
            QMessageBox.critical(self, "状态加载失败", f"无法加载树状态：\n{path}\n\n{exc}")
            return

    def _reset_id_seq(self) -> None:
        self._id_seq = 0
        if self._model is None:
            return
        existing = {node.id for node in self._model.iter_nodes()}
        while f"x{self._id_seq + 1}" in existing:
            self._id_seq += 1

    def _new_id(self) -> str:
        self._id_seq += 1
        return f"x{self._id_seq}"

    def _mutate_view_options(self, mutator) -> None:
        if self._syncing_controls:
            return
        if self._model is not None and self._view_drag_snapshot is None:
            self._push_undo_state(self._capture_history_state())
        options = self._current_options()
        mutator(options)
        self._viewer.set_render_options(options)
        self._sync_controls_from_options()
        self._rerender_current_tree()

    def _begin_view_drag(self) -> None:
        if self._model is not None and self._view_drag_snapshot is None:
            self._view_drag_snapshot = self._capture_history_state()

    def _end_view_drag(self) -> None:
        if self._view_drag_snapshot is not None:
            self._push_undo_state(self._view_drag_snapshot)
            self._view_drag_snapshot = None

    def _on_layout_changed(self, value: str) -> None:
        self._mutate_view_options(lambda o: setattr(o, "layout_mode", value))

    def _on_ignore_lengths_changed(self, checked: bool) -> None:
        self._mutate_view_options(lambda o: setattr(o, "ignore_branch_lengths", checked))

    def _on_align_labels_changed(self, checked: bool) -> None:
        self._mutate_view_options(lambda o: setattr(o, "align_tip_labels", checked))

    def _on_circular_start_angle_changed(self, value: float) -> None:
        if self._syncing_controls:
            return
        self._mutate_view_options(lambda o: setattr(o, "circular_start_angle", float(value)))

    def _on_circular_gap_changed(self, value: float) -> None:
        if self._syncing_controls:
            return
        self._mutate_view_options(lambda o: setattr(o, "circular_gap_degrees", float(value)))

    def _on_circular_follow_branch_changed(self, checked: bool) -> None:
        self._mutate_view_options(lambda o: setattr(o, "circular_label_follow_branch", checked))

    def _on_show_tip_labels_changed(self, checked: bool) -> None:
        self._mutate_view_options(lambda o: setattr(o, "show_tip_labels", checked))

    def _on_show_node_circles_changed(self, checked: bool) -> None:
        self._mutate_view_options(lambda o: setattr(o, "show_node_circles", checked))

    def _on_show_selected_node_circle_changed(self, checked: bool) -> None:
        self._mutate_view_options(lambda o: setattr(o, "show_selected_node_circle", checked))

    def _on_show_leader_lines_changed(self, checked: bool) -> None:
        self._mutate_view_options(lambda o: setattr(o, "show_leader_lines", checked))

    def _on_show_support_changed(self, checked: bool) -> None:
        self._mutate_view_options(lambda o: setattr(o, "show_support_labels", checked))

    def _on_global_font_changed(self, font: QFont) -> None:
        self._mutate_view_options(lambda o: setattr(o, "font_family", font.family()))

    def _on_global_font_size_changed(self, value: int) -> None:
        self._mutate_view_options(lambda o: setattr(o, "font_size", value))

    def _on_support_size_changed(self, value: int) -> None:
        self._mutate_view_options(lambda o: setattr(o, "support_font_size", value))

    def _on_node_offset_x_changed(self, value: float) -> None:
        if self._syncing_controls:
            return
        self._mutate_view_options(lambda o: setattr(o, "support_offset_x", float(value)))

    def _on_node_offset_y_changed(self, value: float) -> None:
        if self._syncing_controls:
            return
        self._mutate_view_options(lambda o: setattr(o, "support_offset_y", float(value)))

    def _on_node_circle_size_changed(self, value: float) -> None:
        if self._syncing_controls:
            return
        self._mutate_view_options(lambda o: setattr(o, "node_circle_size", float(value)))

    def _on_group_line_width_changed(self, value: float) -> None:
        if self._syncing_controls:
            return
        self._mutate_view_options(lambda o: setattr(o, "group_line_width", float(value)))

    def _reset_selected_node_label_offset(self) -> None:
        node = self._selected_support_node()
        if node is None or node.id not in self._annotations.node_label_offsets:
            return
        before = self._capture_history_state()
        self._annotations.node_label_offsets.pop(node.id, None)
        self._push_undo_state(before)
        self._rerender_current_tree()

    def _reset_selected_node_label_text(self) -> None:
        node = self._selected_support_node()
        if node is None or node.id not in self._annotations.node_label_overrides:
            return
        before = self._capture_history_state()
        self._annotations.node_label_overrides.pop(node.id, None)
        self._push_undo_state(before)
        self._rerender_current_tree()

    def _on_leader_width_changed(self, value: float) -> None:
        self._mutate_view_options(lambda o: setattr(o, "leader_line_width", float(value)))

    def _on_branch_width_changed(self, value: float) -> None:
        self._mutate_view_options(lambda o: setattr(o, "branch_width", float(value)))

    def _on_width_changed(self, value: int) -> None:
        if self._syncing_controls:
            return
        self._syncing_controls = True
        self._width_slider.setValue(value)
        self._width_spin.setValue(value)
        self._syncing_controls = False
        options = self._current_options()
        options.canvas_width = value
        self._viewer.set_render_options(options)
        self._rerender_current_tree()

    def _on_height_changed(self, value: int) -> None:
        if self._syncing_controls:
            return
        self._syncing_controls = True
        self._height_slider.setValue(value)
        self._height_spin.setValue(value)
        self._syncing_controls = False
        options = self._current_options()
        options.canvas_height = value
        self._viewer.set_render_options(options)
        self._rerender_current_tree()

    def _on_view_offset_x_changed(self, value: float) -> None:
        if self._syncing_controls:
            return
        self._mutate_view_options(lambda o: setattr(o, "view_offset_x", float(value)))

    def _on_view_offset_y_changed(self, value: float) -> None:
        if self._syncing_controls:
            return
        self._mutate_view_options(lambda o: setattr(o, "view_offset_y", float(value)))

    def _on_inset_overview_changed(self, checked: bool) -> None:
        self._mutate_view_options(lambda o: setattr(o, "inset_overview_enabled", checked))

    def _reset_inset_overview(self) -> None:
        if self._model is None:
            return
        before = self._capture_history_state()
        options = self._current_options()
        changed = (
            options.inset_overview_offset_x != 24.0
            or options.inset_overview_offset_y != 24.0
            or abs(float(options.inset_overview_scale) - 0.24) > 1e-9
        )
        options.inset_overview_offset_x = 24.0
        options.inset_overview_offset_y = 24.0
        options.inset_overview_scale = 0.24
        options.inset_overview_branch_width = 4.0
        self._viewer.set_render_options(options)
        if changed:
            self._push_undo_state(before)
        self._sync_controls_from_options()
        self._rerender_current_tree()

    def _on_scale_bar_visible_changed(self, checked: bool) -> None:
        self._mutate_view_options(lambda o: setattr(o, "scale_bar_visible", checked))

    def _on_inset_branch_width_changed(self, value: float) -> None:
        if self._syncing_controls:
            return
        self._mutate_view_options(lambda o: setattr(o, "inset_overview_branch_width", float(value)))

    def _on_scale_bar_auto_changed(self, checked: bool) -> None:
        self._mutate_view_options(lambda o: setattr(o, "scale_bar_auto", checked))

    def _on_scale_length_changed(self, value: float) -> None:
        self._mutate_view_options(lambda o: setattr(o, "scale_bar_length", float(value)))

    def _on_scale_position_changed(self, value: str) -> None:
        self._mutate_view_options(lambda o: setattr(o, "scale_bar_position", value))

    def _on_scale_offset_x_changed(self, value: float) -> None:
        if self._syncing_controls or not self._scale_bar_available():
            return
        before = self._capture_history_state()
        _, cur_y = self._annotations.scale_bar_offset or (0.0, 0.0)
        self._annotations.scale_bar_offset = (float(value), float(cur_y))
        self._push_undo_state(before)
        self._rerender_current_tree()

    def _on_scale_offset_y_changed(self, value: float) -> None:
        if self._syncing_controls or not self._scale_bar_available():
            return
        before = self._capture_history_state()
        cur_x, _ = self._annotations.scale_bar_offset or (0.0, 0.0)
        self._annotations.scale_bar_offset = (float(cur_x), float(value))
        self._push_undo_state(before)
        self._rerender_current_tree()

    def _on_inset_overview_moved(self, x: float, y: float) -> None:
        if self._model is None:
            return
        before = self._capture_history_state()
        options = self._current_options()
        if abs(float(options.inset_overview_offset_x) - float(x)) < 1e-9 and abs(float(options.inset_overview_offset_y) - float(y)) < 1e-9:
            return
        options.inset_overview_offset_x = float(x)
        options.inset_overview_offset_y = float(y)
        self._viewer.set_render_options(options)
        self._push_undo_state(before)
        self._sync_controls_from_options()
        self.statusBar().showMessage("已更新拓扑副图位置。", 3000)

    def _on_inset_overview_scale_changed(self, scale: float) -> None:
        if self._model is None:
            return
        before = self._capture_history_state()
        options = self._current_options()
        scale_value = max(0.10, min(0.60, float(scale)))
        if abs(float(options.inset_overview_scale) - scale_value) < 1e-9:
            return
        options.inset_overview_scale = scale_value
        self._viewer.set_render_options(options)
        self._push_undo_state(before)
        self._rerender_current_tree()
        self.statusBar().showMessage(f"拓扑副图缩放：{scale_value:.2f}", 3000)

    def _reset_scale_bar_offset(self) -> None:
        if self._annotations.scale_bar_offset is None:
            return
        before = self._capture_history_state()
        self._annotations.scale_bar_offset = None
        self._push_undo_state(before)
        self._rerender_current_tree()

    def _reset_scale_bar_text(self) -> None:
        if self._annotations.scale_bar_label_override is None:
            return
        before = self._capture_history_state()
        self._annotations.scale_bar_label_override = None
        self._push_undo_state(before)
        self._rerender_current_tree()

    def _choose_leader_line_color(self) -> None:
        color = QColorDialog.getColor(QColor(self._current_options().leader_line_color), self, "选择虚线颜色")
        if color.isValid():
            self._mutate_view_options(lambda o: setattr(o, "leader_line_color", color.name()))

    def _choose_branch_color(self) -> None:
        color = QColorDialog.getColor(QColor(self._current_options().branch_color), self, "选择分支颜色")
        if not color.isValid():
            return
        before = self._capture_history_state()
        self._annotations.branch_colors.clear()
        self._push_undo_state(before)
        self._mutate_view_options(lambda o: setattr(o, "branch_color", color.name()))

    def _choose_selected_branch_color(self) -> None:
        if not self._model or not self._selected_node_id:
            QMessageBox.information(self, "提示", "请先选中一个分支或对应节点。")
            return
        current_color = self._annotations.branch_colors.get(self._selected_node_id, self._current_options().branch_color)
        color = QColorDialog.getColor(QColor(current_color), self, "选择选中分支颜色")
        if not color.isValid():
            return
        before = self._capture_history_state()
        self._annotations.branch_colors[self._selected_node_id] = color.name()
        self._push_undo_state(before)
        self._rerender_current_tree()

    def _choose_node_circle_color(self) -> None:
        color = QColorDialog.getColor(QColor(self._current_options().node_circle_color), self, "选择节点圆圈颜色")
        if color.isValid():
            self._mutate_view_options(lambda o: setattr(o, "node_circle_color", color.name()))

    def _choose_collapsed_triangle_color(self) -> None:
        color = QColorDialog.getColor(QColor(self._current_options().collapsed_triangle_color), self, "选择折叠三角颜色")
        if color.isValid():
            self._mutate_view_options(lambda o: setattr(o, "collapsed_triangle_color", color.name()))

    def _find_node_and_parent(self, root: TreeNode, target_id: str) -> tuple[TreeNode | None, dict[str, TreeNode]]:
        parent: dict[str, TreeNode] = {}
        stack = [root]
        found: TreeNode | None = None
        while stack:
            node = stack.pop()
            if node.id == target_id:
                found = node
            for child in node.children:
                parent[child.id] = node
                stack.append(child)
        return found, parent

    def _reroot_to_selected(self) -> None:
        if not self._model or not self._selected_node_id:
            return
        target, parent = self._find_node_and_parent(self._model.root, self._selected_node_id)
        if target is None:
            return
        root = self._model.root
        if root.children and target.id == root.id:
            return
        before = self._capture_history_state()
        if len(root.children) == 2 and target.id in {root.children[0].id, root.children[1].id}:
            other = root.children[0] if root.children[1].id == target.id else root.children[1]
            root.children = [target, other] if self._chk_reroot_on_top.isChecked() else [other, target]
            self._annotations.leaf_groups.clear()
            self._annotations.preserve_root_order = True
            self._push_undo_state(before)
            self._rerender_current_tree()
            return
        par = parent.get(target.id)
        if par is None:
            return
        self._push_undo_state(before)
        length = float(target.branch_length or 0.0)
        try:
            par.children.remove(target)
        except ValueError:
            pass
        new_root = TreeNode(id=self._new_id())
        target.branch_length = length / 2.0
        up_len = float(par.branch_length or 0.0)
        par.branch_length = length / 2.0
        cur = par
        while cur.id in parent:
            up = parent[cur.id]
            next_up_len = float(up.branch_length or 0.0)
            try:
                up.children.remove(cur)
            except ValueError:
                pass
            cur.children.append(up)
            up.branch_length = up_len
            cur = up
            up_len = next_up_len
        new_root.children = [target, par] if self._chk_reroot_on_top.isChecked() else [par, target]
        new_root.branch_length = None
        self._model.root = new_root
        self._annotations.leaf_groups.clear()
        self._annotations.preserve_root_order = True
        self._rerender_current_tree()

    def _rotate_selected(self) -> None:
        if not self._model or not self._selected_node_id:
            return
        target, _ = self._find_node_and_parent(self._model.root, self._selected_node_id)
        if not target or len(target.children) < 2:
            return
        self._push_undo_state(self._capture_history_state())
        target.children.reverse()
        self._annotations.leaf_groups.clear()
        self._rerender_current_tree()

    def _toggle_collapse_selected(self) -> None:
        if not self._model or not self._selected_node_id:
            return
        target, _ = self._find_node_and_parent(self._model.root, self._selected_node_id)
        if not target or target.is_leaf():
            return
        self._push_undo_state(self._capture_history_state())
        target.collapsed = not target.collapsed
        self._annotations.leaf_groups.clear()
        self._rerender_current_tree()

    def _max_visible_leaf_depth(self, node: TreeNode, depth: int = 0) -> int:
        if node.is_leaf() or node.collapsed:
            return depth
        return max(self._max_visible_leaf_depth(child, depth + 1) for child in node.children)

    def _sort_tree_by_topology_depth_inplace(self) -> bool:
        if not self._model:
            return False
        max_depth = self._max_visible_leaf_depth(self._model.root, 0)
        changed = False
        preserve_root_order = bool(self._annotations.preserve_root_order)

        def sort_node(node: TreeNode, depth: int, is_root: bool = False) -> None:
            nonlocal changed
            if node.is_leaf() or node.collapsed or len(node.children) < 2:
                return
            for child in node.children:
                sort_node(child, depth + 1, False)
            if is_root and preserve_root_order:
                return

            def child_key(child: TreeNode) -> tuple[int, float, str]:
                display_depth = max_depth if child.is_leaf() or child.collapsed else depth + 1
                terminal_branch_length = 0.0
                if child.is_leaf() or child.collapsed:
                    terminal_branch_length = float(child.branch_length or 0.0)
                label = child.name or child.id
                return (display_depth, terminal_branch_length, label.lower())

            old_order = [child.id for child in node.children]
            node.children.sort(key=child_key)
            if old_order != [child.id for child in node.children]:
                changed = True

        sort_node(self._model.root, 0, True)
        if changed:
            self._annotations.leaf_groups.clear()
        return changed

    def _sort_tree_by_topology_depth(self) -> None:
        if not self._model:
            return
        before = self._capture_history_state()
        changed = self._sort_tree_by_topology_depth_inplace()
        if not changed:
            self.statusBar().showMessage("当前树顺序无需调整。", 3000)
            return
        self._push_undo_state(before)
        self._rerender_current_tree()

    def _search_taxa(self) -> None:
        if not self._model:
            return
        text, ok = QInputDialog.getText(self, "搜索物种名", "输入关键字（不区分大小写）：", text=self._last_search)
        if not ok:
            return
        self._last_search = text
        hit = self._viewer.highlight_labels_contains(text)
        self.statusBar().showMessage(f"搜索命中 {hit} 个标签。", 4000)

    def _batch_replace_names(self) -> None:
        if not self._model:
            return
        dialog = BatchReplaceDialog(self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        pattern, repl, flags = dialog.values()
        if not pattern:
            return
        try:
            rx = re.compile(pattern, flags=flags)
        except Exception as exc:
            QMessageBox.critical(self, "正则错误", str(exc))
            return
        before = self._capture_history_state()
        hit = 0
        for node in self._model.iter_nodes():
            if not node.is_leaf():
                continue
            if node.original_name is None:
                node.original_name = node.name
            old = node.name or ""
            new = rx.sub(repl, old)
            if new != old:
                node.name = new
                hit += 1
        if hit == 0:
            self.statusBar().showMessage("没有标签发生变化。", 3000)
            return
        self._push_undo_state(before)
        self._rerender_current_tree()

    def _quick_replace_accession_format_inplace(self) -> int:
        if not self._model:
            return 0
        hit = 0
        for node in self._model.iter_nodes():
            if not node.is_leaf():
                continue
            old = node.name or ""
            new = old.replace("---", " (").replace("--", ")")
            if new != old:
                node.name = new
                hit += 1
        return hit

    def _quick_replace_accession_format(self) -> None:
        if not self._model:
            return
        before = self._capture_history_state()
        hit = self._quick_replace_accession_format_inplace()
        if hit == 0:
            self.statusBar().showMessage("没有名称需要替换。", 3000)
            return
        self._push_undo_state(before)
        self._rerender_current_tree()

    def _quick_replace_underscore_format_inplace(self) -> int:
        if not self._model:
            return 0
        hit = 0
        for node in self._model.iter_nodes():
            if not node.is_leaf():
                continue
            old = node.name or ""
            new = old.replace("_", " ")
            new = re.sub(r"\bNC (?=\d)", "NC_", new)
            if new != old:
                node.name = new
                hit += 1
        return hit

    def _quick_replace_underscore_format(self) -> None:
        if not self._model:
            return
        before = self._capture_history_state()
        hit = self._quick_replace_underscore_format_inplace()
        if hit == 0:
            self.statusBar().showMessage("没有下划线需要替换。", 3000)
            return
        self._push_undo_state(before)
        self._rerender_current_tree()

    def _italicize_all_tip_labels_inplace(self) -> int:
        if not self._model:
            return 0
        hit = 0
        for node in self._model.iter_nodes():
            if not node.is_leaf() or not node.name:
                continue
            name = node.name
            current = self._annotations.tip_style_overrides.get(name)
            display_text = current.display_text if current and current.display_text is not None else name
            rich_html = self._scientific_name_html(display_text)
            if current and current.rich_html == rich_html:
                continue
            self._annotations.tip_style_overrides[name] = TipStyleOverride(
                taxon_name=name,
                display_text=display_text,
                rich_html=rich_html,
            )
            hit += 1
        return hit

    def _italicize_all_tip_labels(self) -> None:
        if not self._model:
            return
        before = self._capture_history_state()
        hit = self._italicize_all_tip_labels_inplace()
        if hit == 0:
            self.statusBar().showMessage("没有样品名可更新。", 3000)
            return
        self._push_undo_state(before)
        self._rerender_current_tree()

    def _auto_adjust_tree(self) -> None:
        if not self._model:
            return
        before = self._capture_history_state()
        accession_hit = self._quick_replace_accession_format_inplace()
        underscore_hit = self._quick_replace_underscore_format_inplace()
        italic_hit = self._italicize_all_tip_labels_inplace()
        sorted_changed = self._sort_tree_by_topology_depth_inplace()
        if accession_hit == 0 and underscore_hit == 0 and italic_hit == 0 and not sorted_changed:
            self.statusBar().showMessage("自动调整未检测到需要更新的内容。", 4000)
            return
        self._push_undo_state(before)
        self._rerender_current_tree()
        parts = [
            f"登录号替换 {accession_hit}",
            f"下划线替换 {underscore_hit}",
            f"一键斜体 {italic_hit}",
            f"排序 {'已执行' if sorted_changed else '未变化'}",
        ]
        self.statusBar().showMessage("自动调整完成：" + "，".join(parts) + "。", 5000)

    def _is_accession_token(self, token: str) -> bool:
        return bool(re.fullmatch(r"[A-Z]{1,2}_?\d{6,}(?:\.\d+)?", token))

    def _format_scientific_segment_html(self, segment: str) -> str:
        parts = re.split(r"(\s+)", segment)
        plain_terms = {
            "var.",
            "var",
            "sp.",
            "sp",
            "cf.",
            "cf",
            "aff.",
            "aff",
            "subsp.",
            "subsp",
            "ssp.",
            "ssp",
            "subup",
            "x",
        }
        out: list[str] = []
        seen_meaningful = 0
        previous_core = ""
        for part in parts:
            if not part:
                continue
            if part.isspace():
                out.append(part.replace("\n", "<br/>"))
                continue
            leading = ""
            trailing = ""
            core = part
            while core and core[0] in "\"'[{<":
                leading += core[0]
                core = core[1:]
            while core and core[-1] in ",;:!?\"']}>":
                trailing = core[-1] + trailing
                core = core[:-1]
            safe_leading = leading.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            safe_trailing = trailing.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            safe_core = core.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            if not core:
                out.append(safe_leading + safe_trailing)
                continue
            is_accession = self._is_accession_token(core)
            is_plain_term = core.lower() in plain_terms
            is_postfix_capitalized = (
                seen_meaningful > 0
                and not self._is_accession_token(previous_core)
                and bool(re.match(r"[A-Z][a-zA-Z-]*$", core))
            )
            if is_plain_term or is_accession or is_postfix_capitalized:
                out.append(f"{safe_leading}{safe_core}{safe_trailing}")
            else:
                out.append(f"{safe_leading}<i>{safe_core}</i>{safe_trailing}")
            previous_core = core
            seen_meaningful += 1
        return "".join(out)

    def _scientific_name_html(self, text: str) -> str:
        pieces = re.split(r"(\([^()]*\))", text.strip())
        out: list[str] = []
        for piece in pieces:
            if not piece:
                continue
            if piece.startswith("(") and piece.endswith(")"):
                out.append(piece.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("\n", "<br/>"))
            else:
                out.append(self._format_scientific_segment_html(piece))
        return "".join(out)

    def _show_config_help(self) -> None:
        QMessageBox.information(
            self,
            "配置文件格式说明",
            "\n".join(
                [
                    "支持 YAML 或 JSON。",
                    "",
                    "主要字段：",
                    "rename_map: 精确改名映射 old -> new",
                    "rename_rules: 正则批量替换，字段包括 pattern / repl / ignore_case",
                    "label_styles: 某个样品整体样式，支持 color / fontFamily / fontSize / fontWeight / nodeColor",
                    "label_spans: 某个样品名内部子串样式，支持 pattern / regex / ignore_case / style",
                    "annotations: 某个样品名后追加注释文本",
                    "",
                    "示例文件：examples/annotations.yaml",
                ]
            ),
        )

    def _read_tree_state_payload(self, path_str: str) -> dict | None:
        path = Path(path_str)
        if path.suffix.lower() != ".json":
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return None
        if isinstance(payload, dict) and payload.get("format") == "phylo-tree-state":
            return payload
        return None

    def _import_config(self) -> None:
        path_str, _ = QFileDialog.getOpenFileName(self, "导入配置文件", "", "配置文件 (*.yaml *.yml *.json);;所有文件 (*.*)")
        if not path_str:
            return
        tree_state_payload = self._read_tree_state_payload(path_str)
        if tree_state_payload is not None:
            self._load_tree_state(tree_state_payload, Path(path_str))
            self.statusBar().showMessage("已导入树状态文件。", 5000)
            return
        if not self._model:
            QMessageBox.information(self, "提示", "请先打开一个树文件。")
            return
        before = self._capture_history_state()
        before_leaf_names = {node.id: (node.name or "") for node in self._model.iter_nodes() if node.is_leaf()}
        try:
            self._styles = load_and_apply_config(self._model, path_str)
        except Exception as exc:
            QMessageBox.critical(self, "配置导入失败", f"无法导入配置：\n{path_str}\n\n{exc}")
            return
        self._push_undo_state(before)
        self._rerender_current_tree()
        summary = self._summarize_config_application(before_leaf_names, self._styles)
        if not any(summary.values()):
            QMessageBox.warning(
                self,
                "配置已导入",
                "\n".join(
                    [
                        f"配置文件已读取：{path_str}",
                        "",
                        "但没有命中任何样品。",
                        "现在样式会同时按“当前名称”和“原始名称”匹配。",
                        "如果仍未生效，请检查配置中的物种名是否与树中的标签一致。",
                    ]
                ),
            )
            return
        message = "，".join(
            [
                f"改名 {summary['renamed']} 个",
                f"整体样式命中 {summary['label_styles']} 个",
                f"局部样式命中 {summary['label_spans']} 个",
                f"注释命中 {summary['annotations']} 个",
            ]
        )
        self.statusBar().showMessage(f"配置导入完成：{message}。", 6000)
        QMessageBox.information(self, "配置导入完成", f"{message}。")

    def _selected_tip_names(self) -> list[str]:
        if self._model is None:
            return []
        selected = set(self._selected_ids)
        out: list[str] = []
        for node in self._model.iter_nodes():
            if node.id in selected and node.is_leaf() and node.name:
                out.append(node.name)
        if not out and self._selected_node_id:
            node, _ = self._find_node_and_parent(self._model.root, self._selected_node_id)
            if node is not None and node.is_leaf() and node.name:
                out.append(node.name)
        return out

    def _edit_selected_tip_font(self) -> None:
        names = self._selected_tip_names()
        if len(names) != 1:
            QMessageBox.information(self, "提示", "请只选中一个样品名称后再编辑文本。")
            return
        name = names[0]
        current = self._annotations.tip_style_overrides.get(name)
        font = QFont(
            current.font_family if current and current.font_family else self._current_options().font_family,
            current.font_size if current and current.font_size else self._current_options().font_size,
        )
        font.setBold(bool(current.bold) if current else False)
        dialog = TipStyleDialog(name, current, font, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        display_text, rich_html = dialog.get_values()
        self._push_undo_state(self._capture_history_state())
        self._annotations.tip_style_overrides[name] = TipStyleOverride(
            taxon_name=name,
            display_text=display_text,
            rich_html=rich_html,
        )
        self._rerender_current_tree()

    def _set_selected_clade_highlight(self) -> None:
        if not self._model or not self._selected_node_id:
            return
        color1 = QColorDialog.getColor(QColor("#fde68a"), self, "选择底色起始颜色")
        if not color1.isValid():
            return
        use_gradient, ok = QInputDialog.getItem(self, "渐变模式", "是否使用渐变色？", ["否", "是"], 0, False)
        if not ok:
            return
        color2_name = None
        if use_gradient == "是":
            color2 = QColorDialog.getColor(QColor("#fca5a5"), self, "选择底色结束颜色")
            if not color2.isValid():
                return
            color2_name = color2.name()
        self._push_undo_state(self._capture_history_state())
        self._annotations.clade_highlights[self._selected_node_id] = CladeHighlight(self._selected_node_id, color1.name(), color2_name)
        self._rerender_current_tree()

    def _create_group_from_selection(self) -> None:
        if not self._model:
            return
        leaf_order = self._current_leaf_order()
        if not leaf_order:
            return
        selected = list(dict.fromkeys(node_id for node_id in self._selected_ids if node_id in leaf_order))
        if not selected and self._selected_node_id in leaf_order:
            selected = [self._selected_node_id]
        if len(selected) < 1:
            QMessageBox.information(self, "提示", "请先选择至少一个样品后再新增分组。")
            return
        indices = sorted(leaf_order.index(node_id) for node_id in selected)
        if indices != list(range(indices[0], indices[-1] + 1)):
            QMessageBox.warning(self, "分组失败", "当前只支持连续样品分组。")
            return
        dialog = GroupDialog(self, initial_color=self._next_group_palette_color())
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        values = dialog.values()
        if not values["name"]:
            QMessageBox.information(self, "提示", "请输入分组名称。")
            return
        self._append_leaf_group(indices, leaf_order, values)

    def _create_background_from_selection(self) -> None:
        if not self._model:
            return
        leaf_order = self._current_leaf_order()
        if not leaf_order:
            return
        selected = list(dict.fromkeys(node_id for node_id in self._selected_ids if node_id in leaf_order))
        if not selected and self._selected_node_id in leaf_order:
            selected = [self._selected_node_id]
        if len(selected) < 1:
            QMessageBox.information(self, "提示", "请先选择至少一个样品后再新增底色。")
            return
        indices = sorted(leaf_order.index(node_id) for node_id in selected)
        if indices != list(range(indices[0], indices[-1] + 1)):
            QMessageBox.warning(self, "新增底色失败", "当前只支持连续样品底色。")
            return
        dialog = GroupDialog(self, background_only=True, initial_color=self._next_group_palette_color())
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        values = dialog.values()
        self._append_leaf_group(indices, leaf_order, values)

    def _append_leaf_group(self, indices: list[int], leaf_order: list[str], values: dict, child_group_ids: list[str] | None = None) -> None:
        leaf_ids = [leaf_order[index] for index in indices]
        self._push_undo_state(self._capture_history_state())
        self._annotations.leaf_groups.append(
            LeafGroupAnnotation(
                group_id=str(uuid4()),
                name=values["name"] if values.get("show_marker", True) else (values["name"] or "底色"),
                start_leaf_index=indices[0],
                end_leaf_index=indices[-1],
                color=values["color"],
                show_marker=bool(values.get("show_marker", True)),
                background_enabled=values["background_enabled"],
                background_scope=values["background_scope"],
                background_color_start=values["background_color_start"],
                background_color_end=values["background_color_end"],
                leaf_ids=leaf_ids,
                child_group_ids=list(dict.fromkeys(child_group_ids or [])),
            )
        )
        self._annotations.next_group_color_index += 1
        self._rerender_current_tree()

    def _create_group_from_selected_groups(self) -> None:
        if not self._model:
            return
        group_ids = self._selected_group_ids()
        if len(group_ids) < 2:
            QMessageBox.information(self, "提示", "请在左侧列表中至少选中两个已有分组。")
            return
        leaf_order = self._current_leaf_order()
        leaf_index = {node_id: index for index, node_id in enumerate(leaf_order)}
        groups = self._group_map()
        combined_ids: list[str] = []
        for group_id in group_ids:
            combined_ids.extend(self._resolve_group_leaf_ids(group_id, groups, {}, set()))
        combined_ids = [node_id for node_id in dict.fromkeys(combined_ids) if node_id in leaf_index]
        if not combined_ids:
            QMessageBox.warning(self, "分组失败", "选中的分组没有可用样品。")
            return
        indices = sorted({leaf_index[node_id] for node_id in combined_ids})
        if indices != list(range(indices[0], indices[-1] + 1)):
            QMessageBox.warning(self, "分组失败", "上级分组覆盖的样品在当前树中必须连续。")
            return
        dialog = GroupDialog(self, initial_color=self._next_group_palette_color())
        dialog.setWindowTitle("新增上级分组")
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        values = dialog.values()
        if not values["name"]:
            QMessageBox.information(self, "提示", "请输入分组名称。")
            return
        self._append_leaf_group(indices, leaf_order, values, child_group_ids=group_ids)

    def _refresh_group_list(self) -> None:
        if not hasattr(self, "_group_list"):
            return
        selected_ids = set(self._selected_group_ids())
        self._group_list.clear()
        for group in self._annotations.leaf_groups:
            if not group.show_marker:
                group_type = "底色"
            else:
                group_type = "上级" if group.child_group_ids else "叶组"
            item = QListWidgetItem(
                f"{group.name} [{group.start_leaf_index + 1}-{group.end_leaf_index + 1}] L{group.level} {group_type}"
            )
            item.setData(Qt.ItemDataRole.UserRole, group.group_id)
            self._group_list.addItem(item)
            if group.group_id in selected_ids:
                item.setSelected(True)

    def _selected_group_id(self) -> str | None:
        if not hasattr(self, "_group_list"):
            return None
        item = self._group_list.currentItem()
        if item is None:
            return None
        group_id = item.data(Qt.ItemDataRole.UserRole)
        return str(group_id) if group_id else None

    def _selected_group_ids(self) -> list[str]:
        if not hasattr(self, "_group_list"):
            return []
        out: list[str] = []
        for item in self._group_list.selectedItems():
            group_id = item.data(Qt.ItemDataRole.UserRole)
            if group_id:
                out.append(str(group_id))
        return list(dict.fromkeys(out))

    def _rename_selected_group(self) -> None:
        group_id = self._selected_group_id()
        if group_id:
            self._rename_group(group_id)

    def _delete_selected_group(self) -> None:
        group_ids = self._selected_group_ids()
        if not group_ids:
            group_id = self._selected_group_id()
            if group_id:
                group_ids = [group_id]
        if group_ids:
            self._delete_groups(group_ids)

    def _rename_group(self, group_id: str) -> None:
        group = next((g for g in self._annotations.leaf_groups if g.group_id == group_id), None)
        if group is None:
            return
        name, ok = QInputDialog.getText(self, "重命名分组", "新分组名称：", text=group.name)
        if not ok or not name.strip():
            return
        self._push_undo_state(self._capture_history_state())
        group.name = name.strip()
        group.rich_html = None
        self._rerender_current_tree()

    def _recolor_group(self, group_id: str) -> None:
        group = next((g for g in self._annotations.leaf_groups if g.group_id == group_id), None)
        if group is None:
            return
        color = QColorDialog.getColor(QColor(group.color), self, "选择分组颜色")
        if not color.isValid():
            return
        self._push_undo_state(self._capture_history_state())
        old_color = (group.color or "").lower()
        new_color = color.name()
        group.color = new_color
        if group.background_enabled:
            start = group.background_color_start
            end = group.background_color_end
            is_gradient = bool(end and start and end != start)
            if is_gradient:
                if start and start.lower() == old_color:
                    group.background_color_start = new_color
                if end and end.lower() == old_color:
                    group.background_color_end = new_color
                elif end:
                    group.background_color_end = new_color
                elif start is None:
                    group.background_color_start = "#ffffff"
                    group.background_color_end = new_color
            else:
                group.background_color_start = new_color
                group.background_color_end = None
        self._rerender_current_tree()

    def _delete_group(self, group_id: str) -> None:
        self._delete_groups([group_id])

    def _delete_groups(self, group_ids: list[str]) -> None:
        if not group_ids:
            return
        groups = self._group_map()
        to_delete = set(group_ids)
        changed = True
        while changed:
            changed = False
            for group in groups.values():
                if group.group_id in to_delete:
                    continue
                if any(child_id in to_delete for child_id in group.child_group_ids):
                    to_delete.add(group.group_id)
                    changed = True
        new_groups = [group for group in self._annotations.leaf_groups if group.group_id not in to_delete]
        if len(new_groups) == len(self._annotations.leaf_groups):
            return
        before = self._capture_history_state()
        self._annotations.leaf_groups = new_groups
        self._push_undo_state(before)
        self._rerender_current_tree()

    def _newick_label(self, text: str | None) -> str:
        if not text:
            return ""
        if re.fullmatch(r"[A-Za-z0-9_.-]+", text):
            return text
        escaped = text.replace("'", "''")
        return f"'{escaped}'"

    def _tree_to_newick(self, node: TreeNode, is_root: bool = False) -> str:
        if node.children and not node.collapsed:
            inner = ",".join(self._tree_to_newick(child, False) for child in node.children)
            label = ""
            if node.support is not None:
                label = f"{node.support:g}"
            elif node.name:
                label = self._newick_label(node.name)
            text = f"({inner}){label}"
        else:
            label = node.name or (node.original_name or "")
            text = self._newick_label(label)
        if not is_root and node.branch_length is not None:
            text += f":{float(node.branch_length):g}"
        return text

    def _default_export_prefix(self) -> str:
        candidates: list[str] = []
        if self._current_tree_path is not None:
            candidates.append(self._current_tree_path.stem)
        if self._model and self._model.name:
            candidates.append(self._model.name)
        for candidate in candidates:
            prefix = re.sub(r'[\\/:*?"<>|]+', "_", candidate).strip().strip(".")
            prefix = re.sub(r"\s+", "_", prefix)
            if prefix:
                return prefix
        return "tree_export"

    def _default_export_path(self, filename: str) -> str:
        if self._current_tree_path is not None:
            return str(self._current_tree_path.with_name(filename))
        return str(Path.cwd() / filename)

    def _next_group_palette_color(self) -> str:
        index = max(0, int(self._annotations.next_group_color_index))
        return BASIC_COLOR_SEQUENCE[index % len(BASIC_COLOR_SEQUENCE)]

    def _tree_state_payload(self) -> dict:
        assert self._model is not None
        return {
            "format": "phylo-tree-state",
            "model": {
                "name": self._model.name,
                "root": self._serialize_tree_node(self._model.root),
            },
            "styles": self._serialize_styles(),
            "annotations": self._serialize_annotation_state(),
            "render_options": asdict(self._current_options()),
        }

    def _write_nwk_file(self, path: Path) -> None:
        assert self._model is not None
        text = self._tree_to_newick(self._model.root, True) + ";\n"
        path.write_text(text, encoding="utf-8")

    def _write_tree_state_file(self, path: Path) -> None:
        payload = self._tree_state_payload()
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def _export_bundle(self) -> None:
        if not self._model:
            return
        initial_directory = str(self._current_tree_path.parent) if self._current_tree_path is not None else str(Path.cwd())
        dialog = ExportBundleDialog(initial_directory, self._default_export_prefix(), self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        directory_text, prefix_text = dialog.values()
        if not directory_text:
            QMessageBox.information(self, "提示", "请先选择输出目录。")
            return
        prefix = prefix_text or self._default_export_prefix()
        prefix = re.sub(r'[\\/:*?"<>|]+', "_", prefix).strip().strip(".")
        prefix = re.sub(r"\s+", "_", prefix)
        if not prefix:
            QMessageBox.information(self, "提示", "文件前缀不能为空。")
            return
        output_dir = Path(directory_text)
        try:
            output_dir.mkdir(parents=True, exist_ok=True)
            nwk_path = output_dir / f"{prefix}.nwk"
            png_path = output_dir / f"{prefix}.png"
            pdf_path = output_dir / f"{prefix}.pdf"
            state_path = output_dir / f"{prefix}_state.json"
            self._write_nwk_file(nwk_path)
            self._viewer.export_png(str(png_path), scale=2.0)
            self._viewer.export_pdf(str(pdf_path))
            self._write_tree_state_file(state_path)
        except Exception as exc:
            QMessageBox.critical(self, "导出失败", f"一键导出失败：\n{exc}")
            return
        self.statusBar().showMessage(f"已导出到：{output_dir}", 5000)

    def _export_nwk(self) -> None:
        if not self._model:
            return
        default_path = self._default_export_path(f"{self._default_export_prefix()}.nwk")
        path, _ = QFileDialog.getSaveFileName(self, "导出 NWK", default_path, "Newick (*.nwk *.newick);;所有文件 (*.*)")
        if not path:
            return
        self._write_nwk_file(Path(path))

    def _export_tree_state(self) -> None:
        if not self._model:
            return
        default_path = self._default_export_path(f"{self._default_export_prefix()}_state.json")
        path, _ = QFileDialog.getSaveFileName(self, "导出当前树状态", default_path, "Tree State (*.json);;所有文件 (*.*)")
        if not path:
            return
        self._write_tree_state_file(Path(path))

    def _export_png(self) -> None:
        if not self._model:
            return
        default_path = self._default_export_path(f"{self._default_export_prefix()}.png")
        path, _ = QFileDialog.getSaveFileName(self, "导出 PNG", default_path, "PNG (*.png)")
        if path:
            self._viewer.export_png(path, scale=2.0)

    def _export_pdf(self) -> None:
        if not self._model:
            return
        default_path = self._default_export_path(f"{self._default_export_prefix()}.pdf")
        path, _ = QFileDialog.getSaveFileName(self, "导出 PDF", default_path, "PDF (*.pdf)")
        if path:
            self._viewer.export_pdf(path)
