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
| Completed | inspect / prepare / audit CLI；逐页渲染、坐标转换、印刷页码映射、断点恢复；统一索引、运行时验证、OCR/Layout Adapter Python Runner；Manifest 写前诊断、跨阶段引用保护、版本约束与交接文档 |
| In Progress | 真实模型接入与少量代表页验收准备，尚未获得真实模型实验结论 |
| Planned | MiniCPM-OCR、PP-DocLayoutV3 真实推理；核心 MLLM、Tool Calling、数据库及证据检索；R5、Ubuntu/GPU 验证 |

**渲染成功不等于 OCR/理解成功。** prepare 产生空解析字段和 not_run 状态；Runner 仅调用显式传入的后端。当前没有真实模型实现，tests 中的 Mock 仅用于合成临时数据。

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

[版本约束](constraints/verified.txt) 固定本地验证的 PyMuPDF 1.28.2、pytest 9.2.0 及测试依赖/构建后端。无 CUDA 或深度学习依赖。Mac ARM64 隔离环境已验证，Ubuntu x86_64 与其他 Python 版本**未验证**；本次依赖安装使用现有缓存，公网从零下载路径未验证。约束文件不包含虚拟环境、Python 可执行文件或平台 wheel。

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

`DocumentParserAdapter` 要求 name、stages 和 `parse(image_path, page) -> ParserResult`。调用 `run_parser(document_dir, adapter, pages="1-3", force=False)`，只处理已经 prepare 的页面；暂不提供模型 CLI。

TextBlock 保存有可靠 bbox 的文字；PageText 保存无可靠 bbox 的页面文字；LayoutRegion 表示通用布局，ImageRegion 只表示实际图像区域。bbox 为原始 PNG 像素 `[x0,y0,x1,y1]`，需通过边界、有限数、置信度、ID/引用校验。

Runner 在调用和写入前复用 Manifest 诊断，拒绝非法版本、状态及不可解释的汇总差异，保留合法 running 恢复语义。成功阶段整体替换自己的输出；OCR 与 Layout 独立更新。更换 ID 导致其他阶段悬空引用会失败并保留旧数据，错误列出具体端点；需要稳定 ID、联合重跑或有依据的人工修复，不自动删除关系。见 [接入及更新策略](docs/handoff.md#5-adapter-接入) 与 [架构](docs/architecture.md)。

## 测试、交接与版本管理

第四轮以已提交并审阅的 **81356dd** 为基线：原 195 项测试通过，新增 27 项，当前 **222 passed，5 条既有 SWIG 警告**。本轮代码尚未 commit/push。证据及剩余限制见 [iteration_04.md](docs/progress/iteration_04.md)，历史 [iteration_03.md](docs/progress/iteration_03.md) 等报告保留原结论。

下一步由师兄验证真实 OCR/文档 MLLM 和 PP-DocLayoutV3，并在少量授权页面人工检查质量。具体模型版本、RTX 3080 显存适配及准确率尚未验证；后续业务扩展见 [Roadmap](docs/roadmap.md)。

现有 [Private Repository](https://github.com/BennettHuangGuobin/Multimodal-PDF-QA) 用于审阅。可提交代码、测试、公开配置示例、constraints、文档及 .gitkeep；不提交真实报告、处理结果、环境/缓存、模型权重、API Key 或服务器凭据。提交前检查文件清单；不自动 commit/push，不生成源码 ZIP。
