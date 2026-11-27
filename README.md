# Multimodal-PDF-QA

面向长篇、图文混排 PDF 的智能理解与外部数据检索项目。目标不是单纯提取 PDF 文字，而是让 **MLLM 结合页面图像和文字提取有用线索，再调用工具检索外部数据库，并回链原文证据**。

## 三阶段技术路线

### Stage 1 — PDF 解析与结构化索引（进行中）
`PDF → PNG 页面渲染 → OCR / Layout → 结构化图文索引`

生成可追溯到 PDF 页码的页面图片、文本与布局信息，为后续 MLLM 提供输入。

### Stage 2 — MLLM 图文理解与线索提取（未开发）
核心 MLLM 结合页面文字、图像及布局信息，理解报告内容并提取有价值的**文本线索和视觉线索**。

### Stage 3 — Tool Calling 与外部数据库检索（未开发）
由 MLLM 利用提取的图文线索调用外部数据库检索工具，并将结果关联回 PDF 页面及相应证据。

## 当前进度：Stage 1

**已完成**
- 基于 PyMuPDF 的 PDF 页面渲染（PNG）、页码/几何信息记录及断点恢复。
- `pages.jsonl` 结构化页面索引、阶段状态与来源记录，以及完整性审计。
- 可扩展的 OCR / Layout Adapter 接口与 Runner。
- 本地 Ollama + **MiniCPM-OCR 1.0 F16** OCR 后端：识别文字写入 `PageText`，无可靠 bbox 时不伪造坐标。
- **一键 PDF → PNG → OCR → JSON**：输入 PDF 自动生成页面图片、识别文字及 `ocr_results.json`。

**待完善**
- 真实 Layout 模型、图片区域/表格结构与阅读顺序解析；当前 OCR 主要输出页面级文字。
- 更多复杂页面的人工质量验收；Stage 2 和 Stage 3 尚未实现。

**本地验证：** MacBook Air M4（16GB 统一内存）运行 MiniCPM-OCR F16，已对 107 页时尚报告的前 3 页完成真实 OCR，初步人工对照效果较好。这不代表整份报告的准确率或 PolyU GPU 环境已验证。

## 快速开始

需要 **Python 3.11+**、已启动的本地 **Ollama** 服务及安装好的 `openbmb/minicpm-ocr1.0:f16` 模型。以下命令在项目根目录执行：

```bash
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install -c constraints/verified.txt setuptools
python -m pip install --no-build-isolation -c constraints/verified.txt -e '.[dev]'
ollama pull openbmb/minicpm-ocr1.0:f16
```

确认 Ollama 正在 `http://127.0.0.1:11434` 运行。将待处理 PDF 放入 `data/input/`，先试少量页：

```bash
python scripts/pdf_to_ocr_json.py "data/input/report.pdf" --pages 1-3 --show-text
```

- 不加 `--pages`：处理整份 PDF；页数较多时耗时较长。
- `--show-text`：在终端展示识别文字；不加则仅打印摘要。
- `--force-ocr`：重新识别指定页；默认可复用已成功的结果。
- 虚拟环境激活且脚本有执行权限时，也可直接运行 `./scripts/pdf_to_ocr_json.py ...`。

## 输出文件

默认保存在 `data/ocr_pipeline/doc_<PDF哈希前16位>/`：

```text
pages/              # 每页 PNG
pages.jsonl         # 逐页结构化索引、OCR 状态及 PageText
ocr_results.json    # 按页汇总的 OCR 文字，便于后续程序读取
manifest.json       # PDF 来源与渲染元信息
audit.json          # 索引与产物完整性审计
ollama_runs/        # 本地私有推理日志与耗时指标
```

主要入口：`scripts/pdf_to_ocr_json.py`（一键处理）、`scripts/run_ollama_ocr.py`（单独 OCR）、`src/multimodal_pdf_qa/`（解析、索引、Adapter、Runner）。代码测试：`python -m pytest -q`。

**数据说明：** 仓库只保存代码、测试与配置，不包含真实 PDF、页面图片、OCR 原文或模型权重；处理产物位于 Git 忽略的 `data/` 目录。完整性审计成功不等于 OCR 内容完全正确。

## 下一步

完善 Stage 1 的 Layout/图像区域解析及真实样本验收，再实现 Stage 2 的 MLLM 图文线索提取，最后接入 Stage 3 的数据库检索与证据回链。
