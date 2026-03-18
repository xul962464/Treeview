## 进化树 GUI 查看器（PhyloTree Viewer）

一个本地离线的 Windows 桌面程序，用于打开和交互式查看进化树文件（Newick/NEXUS），支持节点操作、搜索、样式/标注配置导入，以及导出矢量或位图图片。

### 功能

- **文件支持**：`.nwk` / `.newick` / `.tre` / `.nex` / `.nexus`，其中 NEXUS 支持多棵树选择
- **交互操作**：
  - 定根到选中节点
  - 交换子树顺序
  - 折叠 / 展开 clade
- **视图控制**：滚轮缩放、画布尺寸调整、末端标签对齐、显示/隐藏引导虚线
- **文字编辑**：支持单个样品名修改文本、字体、颜色，以及富文本逐字符样式
- **搜索**：按物种名关键字搜索并高亮
- **配置导入**：YAML/JSON 批量改名、标签样式、子串样式、附加注释
- **导出**：SVG / PNG / PDF

### 安装依赖

```bash
python -m pip install -r requirements.txt
```

### 运行

```bash
python -m app.main
```

打开后在菜单 **文件 -> 打开树文件** 中选择你的 `.nwk` / `.nex` / `.tre` 文件。

### 配置文件

示例见：[examples/annotations.yaml](examples/annotations.yaml)

支持字段：

- `rename_map`：精确改名映射（`old -> new`）
- `rename_rules`：按顺序执行的正则批量替换
  - `pattern`
  - `repl`
  - `ignore_case`
- `label_styles`：针对某个 taxon 的整体标签样式
  - `color`
  - `fontFamily`
  - `fontSize`
  - `fontWeight`
  - `nodeColor`
- `label_spans`：针对某个 taxon 的标签内部子串样式
  - `pattern`
  - `regex`
  - `ignore_case`
  - `style`
- `annotations`：给某个 taxon 追加显示附加文本

### 打包

安装 PyInstaller：

```bash
python -m pip install -r requirements-dev.txt
```

打包命令（生成 `dist/PhyloTreeViewer.exe`）：

```bash
pyinstaller -F -w -n PhyloTreeViewer app/main.py
```

# Treeview
