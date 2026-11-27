# 一键 PDF → PNG → MiniCPM-OCR → JSON

这个入口针对 **Multimodal-PDF-QA** 的当前 OCR 阶段：只输入一份 PDF，先复用 PyMuPDF `prepare_pdf` 生成 PNG/索引，再调用已有 `OllamaOCRAdapter` + `run_parser`，最后导出按页组织的 `ocr_results.json`。它不负责 Layout、表格单元格恢复、完整图文理解或外部数据库检索。

## 运行前提

- 已 Clone 私有仓库，使用 Python 3.11 和项目虚拟环境安装依赖；见 [handoff.md](handoff.md)。
- 本机 Ollama 服务运行在 `http://127.0.0.1:11434`；预先安装 `openbmb/minicpm-ocr1.0:f16`。工程不会自动下载模型。
- 将**有授权的** PDF 放在 `data/input/`；真实报告、渲染图和识别结果不提交 Git。

检查本地模型：

```bash
curl --noproxy '*' http://127.0.0.1:11434/api/version
ollama list
```

## 一条命令处理

项目根目录执行，推荐先测试 1–3 页：

```bash
.venv/bin/python scripts/pdf_to_ocr_json.py \
  "data/input/report.pdf" \
  --pages 1-3 \
  --show-text
```

若已先执行 `source .venv/bin/activate`，也可以改为 `python scripts/pdf_to_ocr_json.py ...`。

无需 `--pages` 时默认处理整个 PDF，百页级报告可能需要较长时间：

```bash
.venv/bin/python scripts/pdf_to_ocr_json.py "data/input/report.pdf"
```

常用参数：

| 参数 | 行为 |
| --- | --- |
| `--pages 1-3` | 只渲染、OCR 指定的 1-based 页，默认全页 |
| `--dpi 144` | PNG 渲染分辨率，默认 144 |
| `--show-text` | 在当前终端显示所选页 OCR 全文，默认关闭，注意终端日志隐私 |
| `--timeout 180` | 单次 Ollama socket 超时，默认 180 秒；不是服务端强制取消 |
| `--force-ocr` | 重识别所选页；会替换当前输出目录中的 OCR 阶段结果 |
| `--output-root data/ocr_pipeline` | 默认独立私有输出根目录；必须位于本仓库被忽略的 `data/` 子目录 |
| `--model openbmb/minicpm-ocr1.0:f16` | 选择预装模型，默认 F16 |

## 产物

默认目录：

```text
data/ocr_pipeline/doc_<PDF哈希前16位>/
├── pages/
│   ├── page_0001.png
│   └── ...
├── manifest.json
├── pages.jsonl           # 原始结构化索引，page_text.text 为 OCR 全文
├── ocr_results.json      # 一键导出的按页 JSON
├── audit.json            # 结构与完整性检查
└── ollama_runs/*.json    # 私有推理文字、时长及运行配置
```

`ocr_results.json` 的 `pages` 数组包含 `pdf_page`、`printed_page`、`page_image`（相对文档目录）、`ocr_status`、`character_count`、`text`、`ocr_source`、`errors` 等字段。`requested_pages` 标注本次请求页码；如果此前已经处理其他页，`pages` 还可能包含此前准备过的页面，不能把两者混淆。

仅把 `ocr_status=success` 的已接受文字导出为 `text`；失败后保留在内部索引的旧文字不会被误当作新结果。无可靠 bbox 时只保存页面文字，不生成假的文字框；`layout_status` 仍为 `not_run`。

同 PDF、同 DPI、同 OCR 身份的成功页可复用，重复运行不重复推理；进程中断后可再次运行恢复。脚本不覆盖已有 `data/processed/`，但对 **同一个 `data/ocr_pipeline/doc_...` 输出目录** 重跑会更新该目录中的索引和运行日志。要保留不同实验版本，请使用不同的 `--output-root`。

**数据边界：** `data/` 已被 .gitignore 忽略，禁止人工 `git add -f` 上传 PDF、页面图、`pages.jsonl`、`ocr_results.json` 或 `ollama_runs`。当前仅验证本地 OCR 文字输出，不能将结构审计成功等同于识别准确率、表格结构恢复或后续 MLLM/数据库流程完成。

## 已验证样本

2026-11-27，MacBook Air M4 16GB + Ollama + MiniCPM-OCR 1.0 F16：107 页报告的前 3 页一键处理成功，实际观察到 PNG 渲染、3/3 页 OCR 成功、终端全文输出及 JSON 保存。该运行结果由用户在本地验证；完整第五轮 API 实验指标见 [iteration_05.md](progress/iteration_05.md)。其它页、其它模型及 PolyU Ubuntu/RTX 3080 环境待重新验证。
