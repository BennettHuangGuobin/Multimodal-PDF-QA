# Multimodal-PDF-QA


**接手开发先读 [docs/handoff.md](docs/handoff.md)**：包含环境启动、源码入口、Adapter 示例、跨阶段更新策略和服务器后续任务。

## 技术路线与当前状态

```text
PDF → 页面渲染 → OCR / 文档 MLLM → 结构化图文索引
    → 核心 MLLM 提取图文线索 → Tool Calling → 外部数据库检索 → 证据回链
```

采用 OCR-first；PyMuPDF 负责元信息、页面几何、渲染与校验，不把原生 PDF 文字抽取当作 OCR 替代。

| 状态 | 范围 |
| --- | --- |
| Completed | inspect / prepare / audit；坐标、索引、恢复、Adapter 校验和写回；Ollama 本地 MiniCPM-OCR 后端，107 页报告前 3 页真实 OCR 实验 |
| In Progress | 三页真实结果的人工质量确认与更复杂样本验收准备；未量化准确率 |
| Planned | PP-DocLayoutV3；核心 MLLM、Tool Calling、数据库及证据检索；R5、Ubuntu/RTX 3080 验证 |

**渲染成功不等于 OCR/理解成功。** prepare 仍产生空解析字段和 not_run 状态；真实 OCR 通过显式调用 Ollama 后端完成，文字保存为 PageText，不编造 bbox。tests 中的 Mock HTTP/Adapter 仅用于合成临时数据，不冒充真实识别。

## 安装与快速验证

需要 Python 3.11+，推荐已验证的 3.11.18。以下命令在项目根目录执行；Clone 与权限说明见交接文档。

```bash
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install -c constraints/verified.txt setuptools
python -m pip install --no-build-isolation -c constraints/verified.txt -e '.[dev]'
python -m pip check
python -m pytest -q
```

[版本约束](constraints/verified.txt) 固定本地验证的 PyMuPDF 1.28.2、pytest 9.2.0 及测试依赖/构建后端。无 CUDA 或深度学习依赖。Mac ARM64 隔离环境已验证，Ubuntu x86_64 与其他 Python 版本**未验证**；第四轮隔离安装使用现有缓存，第五轮未新增依赖，公网从零下载路径未验证。约束文件不包含虚拟环境、Python 可执行文件或平台 wheel。

无需真实 PDF 的端到端验证：

```bash
python scripts/create_demo_pdf.py --output data/input/demo.pdf
multimodal-pdf-qa inspect data/input/demo.pdf
multimodal-pdf-qa prepare data/input/demo.pdf --pages 1-3 --dpi 144 --output data/processed-demo
multimodal-pdf-qa audit data/processed-demo
```

预期三页渲染和审计通过，OCR/Layout 仍 not_run。重复 prepare 跳过成功页；demo.pdf 已存在时生成脚本会拒绝覆盖，直接复用即可。也支持 `python -m multimodal_pdf_qa`。

## 输入与输出

真实报告放在 data/input/，仓库不附带。两份已有测试报告分别为《26春夏抖音电商时尚消费趋势报告》（107 页）及《26SS春夏淘宝服饰白皮书》（128 页）；先用少量页验证：

```bash
multimodal-pdf-qa prepare 'data/input/26SS春夏淘宝服饰白皮书.pdf' --pages 1-3 --output data/processed
multimodal-pdf-qa audit data/processed
```

输入支持单文件或直接子文件目录。所有 PDF 页码从 1 开始，范围含两端；省略 --pages 会处理全量。DPI 默认 144。配置示例见 [prepare.example.toml](configs/prepare.example.toml)，CLI 参数优先。人工印刷页码映射用 --printed-pages；printed_page 未知为 null，不默认等于 pdf_page。

```text
data/processed/doc_<PDF哈希前16位>/
├── pages/page_0001.png
├── pages.jsonl
├── manifest.json
└── audit.json
```

内容变化产生新 document_id；页图路径相对文档目录。pages.jsonl 一页一行，记录几何、图像哈希、文字/布局/关系、独立阶段状态和来源。新页面 Schema 1.1 兼容旧未解析 1.0；manifest/audit 独立版本仍为 1.0。详见 [Schema](docs/schema.md)。

prepare 检查历史核心结构；成功页只有 DPI、几何及 PNG 核验一致时才跳过。缺失/失败页可重跑；--force 或图像变化会清空下游解析结果。重建已有成果前先备份。同一文档目录只允许一个 writer。

CLI 输出 JSON 到 stdout、进度/错误到 stderr。退出码：0=请求完成，1=文档/产物错误，2=参数/配置/输入选择错误，130=中断。audit 的 warnings、source_verified、ocr/layout 状态须单独检查；not_run 正常，不代表已识别。

## 接入 Adapter

`DocumentParserAdapter` 要求 name、stages 和 `parse(image_path, page) -> ParserResult`。调用 `run_parser(document_dir, adapter, pages="1-3", force=False)`，只处理已经 prepare 的页面。新增独立本地 OCR 脚本，核心 CLI 的三个命令不变。

TextBlock 保存有可靠 bbox 的文字；PageText 保存无可靠 bbox 的页面文字；LayoutRegion 表示通用布局，ImageRegion 只表示实际图像区域。bbox 为原始 PNG 像素 `[x0,y0,x1,y1]`，需通过边界、有限数、置信度、ID/引用校验。

Runner 在调用和写入前复用 Manifest 诊断，拒绝非法版本、状态及不可解释的汇总差异，保留合法 running 恢复语义。成功阶段整体替换自己的输出；OCR 与 Layout 独立更新。更换 ID 导致其他阶段悬空引用会失败并保留旧数据，错误列出具体端点；需要稳定 ID、联合重跑或有依据的人工修复，不自动删除关系。见 [接入及更新策略](docs/handoff.md#5-adapter-接入) 与 [架构](docs/architecture.md)。

## Ollama 本地 OCR 小实验

要求本机已经运行 Ollama 并安装 `openbmb/minicpm-ocr1.0:f16`；代码不下载模型，不调用收费 API。服务地址为 `http://127.0.0.1:11434`。先把单个已准备文档目录复制到新的 `data/experiments/` 子目录，避免覆盖既有成果；完整复制示例见 [handoff](docs/handoff.md#ollama-真实后端已实现)。本次已有实验可这样读取/复用：

```bash
python scripts/run_ollama_ocr.py data/experiments/iteration_05_ollama_f16/doc_ad04687d78935bea --pages 1-3 --timeout 180
multimodal-pdf-qa audit data/experiments/iteration_05_ollama_f16/doc_ad04687d78935bea
```

`--pages` 必填，不默认全量处理；同模型摘要/配置及 PNG 的成功页默认跳过。`--force` 会重新识别并替换该目录中的 OCR 结果。默认提示词 `Text Recognition:`，temperature=0、num_ctx=16384、num_predict=8192；后两项可通过脚本参数调整。timeout 是 socket 超时，不保证取消服务端推理。

原文写入 pages.jsonl 的 page_text，原始文本及 API 时长写入同目录 `ollama_runs/*.json`；这些都是被 Git 忽略的私有数据。脚本 stdout 仅输出不含识别全文的摘要。模型名/摘要、Ollama 版本及关键配置绑定到 Adapter 身份；普通空白、截断和异常响应不标成功。

第五轮实际三页 OCR 成功，分别 **35 / 991 / 943 字符**，客户端约 **9.25 / 16.97 / 16.85 秒**，整次约 43.38 秒。没有结构化 bbox，Layout 均 not_run；人工质量评价仍待用户确认。API 指标及实验限制见 [iteration_05.md](docs/progress/iteration_05.md)。

## 测试、交接与版本管理

第五轮以已提交的 **3c51559** 为基线，保留原 222 项测试并增加 Ollama Mock HTTP 回归；当前 **265 passed，5 条既有 SWIG 警告**。真实实验前完整测试 264 项通过；实验后补充服务不可用的脚本回归测试。证据见 [iteration_05.md](docs/progress/iteration_05.md)，历史报告不回写。本轮尚未 commit/push。

下一步由师兄在 PolyU 环境重新验证 OCR 部署/版本/显存和性能，接入 PP-DocLayoutV3，并在少量授权页面人工检查质量。Mac 的三页实验不能替代 Ubuntu/GPU 或业务准确率验收；后续路线见 [Roadmap](docs/roadmap.md)。

现有 [Private Repository](https://github.com/BennettHuangGuobin/Multimodal-PDF-QA) 用于审阅。可提交代码、测试、公开配置示例、constraints、文档及 .gitkeep；不提交真实报告、处理结果、环境/缓存、模型权重、API Key 或服务器凭据。提交前检查文件清单；不自动 commit/push，不生成源码 ZIP。
