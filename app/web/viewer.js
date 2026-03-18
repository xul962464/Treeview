function byId(id) {
  const el = document.getElementById(id);
  if (!el) throw new Error(`missing element #${id}`);
  return el;
}

window.loadRawTree = function loadRawTree(payload) {
  byId("sourcePath").textContent = payload.sourcePath || "（未知文件）";
  byId("status").textContent = "已加载（原始文本视图）";
  byId("raw").textContent = payload.raw || "";
};

