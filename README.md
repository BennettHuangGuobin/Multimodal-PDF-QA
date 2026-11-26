# Multimodal-PDF-QA

香港理工大学课题组横向工程项目：面向复杂长篇图文 PDF，建立智能理解、外部数据库检索和证据回链系统。当前是工程基础设施阶段，不是论文投稿项目。

主要业务测试材料为《26春夏抖音电商时尚消费趋势报告》（约 107 页）与《26SS春夏淘宝服饰白皮书》（约 128 页）。报告包含大量图片、图片内文字、时尚商品及复杂排版，单纯抽取 PDF 原生文字无法覆盖完整信息。仓库不附带这些第三方报告，开发和测试不依赖它们。

## 技术路线与实现边界

采用 **OCR-first**：

```text
PDF → 页面渲染 → OCR / 文档 MLLM 图文解析 → 结构化索引
    → 核心 MLLM 理解与图文线索提取 → Tool Calling → 外部数据库检索 → 证据回链
```

PyMuPDF 当前只负责文件元信息、页面几何信息、渲染及图像检查。主流程没有 PDF 原生文字抽取，也没有将其当作 OCR 替代品。

| 状态 | 范围 |
| --- | --- |
| Completed | Mac CPU 本地 inspect / prepare / audit CLI，逐页 PNG、断点恢复、JSONL、manifest、完整性检查、印刷页码映射；R1–R4 修复；第三轮 Adapter Runner、结果运行时校验、分阶段合并与恢复、v1.0 兼容及合成测试 |
| In Progress | 更复杂页面的业务样本验收准备、OCR/布局后端的部署与评测方案；尚未全量处理报告或验证模型 |
| Planned | MiniCPM-OCR、PP-DocLayoutV3 接入与模型推理；MLLM 线索提取、Tool Calling、外部数据库查询、证据回链 |

**当前没有接入任何真实 OCR / 布局模型，没有模型下载、收费 API、RAG 或数据库服务。** `prepare` 输出空区域、空关系以及 `not_run` 状态。现在可通过 Python Runner 调用显式传入的 Adapter；仓库只有 `tests/` 下的合成 Mock 用于验证接口，不会默认解析真实报告。

## 环境安装

要求 Python **3.11+**；运行时只有 PyMuPDF，开发测试增加 pytest。支持 macOS CPU，不依赖 CUDA、Linux 组件或 GPU。

```bash
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
python -m pytest
```

本次工作区已用 uv 在 `.python/` 安装 Python 3.11，并创建 `.venv/`。直接执行 `source .venv/bin/activate` 即可使用，无需替换 macOS 系统 Python。`.tools/`、`.python/`、`.uv-cache/`、`.venv/` 都不提交 Git。若环境需要重建且本机无 Python 3.11，可使用已安装的项目工具：

```bash
UV_CACHE_DIR=.uv-cache UV_PYTHON_INSTALL_DIR=.python .tools/bin/uv python install 3.11 --no-bin
UV_CACHE_DIR=.uv-cache UV_PYTHON_INSTALL_DIR=.python .tools/bin/uv venv --python 3.11 .venv
UV_CACHE_DIR=.uv-cache .tools/bin/uv pip install --python .venv/bin/python -e '.[dev]'
```

上面两种安装方法任选一种。新克隆环境若没有 `.tools/bin/uv`，使用标准 Python 安装方法。支持 `python -m multimodal_pdf_qa` 和安装后的 `multimodal-pdf-qa`。

## 快速验证：无需真实 PDF

仓库附带小型合成 PDF 生成脚本，只用于工程验证，不模拟 OCR：

```bash
source .venv/bin/activate
python scripts/create_demo_pdf.py
multimodal-pdf-qa inspect data/input/demo.pdf
multimodal-pdf-qa prepare data/input/demo.pdf --pages 1-3 --dpi 144
multimodal-pdf-qa audit data/processed
```

`inspect` 输出真实页数、PDF 元信息、逐页尺寸和旋转；`prepare` 自动创建目录。进度和异常诊断写入 stderr，JSON 结果写入 stdout；audit 的字段问题也在 JSON 的 issues/warnings 中列出。目录内某个 PDF 失败不会阻止处理其他 PDF。

| 退出码 | 约定 |
| --- | --- |
| `0` | 请求成功或 audit 通过；仍需查看 warnings、source_verified 和待解析状态。 |
| `1` | 文档/产物错误：坏 PDF、不支持的页面几何、历史 JSONL 损坏、缺 manifest、渲染失败或 audit 不通过。 |
| `2` | 参数/配置/输入选择错误：非法命令参数、DPI、TOML、页范围（含超出实际页数），没有找到 PDF/候选产物，或单文件选项用于多个文件。 |
| `130` | 用户中断；已有成功检查点保留，可重跑。 |

坏印刷页码映射文件按文档处理错误返回 1。批量任务如果同时出现 1 和 2，以 2 为最终退出码；其他合法文档仍可完成，不能把非零退出码理解为整个批次完全没有写入。

## 输入与输出

把真实报告放入 `data/input/`，示例（按实际文件名修改）：

```bash
multimodal-pdf-qa prepare data/input --pages 1-3 --output data/processed --dpi 144
multimodal-pdf-qa prepare 'data/input/26SS春夏淘宝服饰白皮书.pdf' --pages 1,3-5
multimodal-pdf-qa prepare data/input/demo.pdf --config configs/prepare.example.toml
```

文件或目录均可输入；目录仅扫描当前层 `.pdf`，扩展名不区分大小写。所有用户可见页码 **从 1 开始**，范围两端均包含。未提供 `--pages` 时处理所有页；真实报告首次验收务必显式传入 `--pages 1-3`。页码超出实际范围会报错，不静默截断。TOML 配置支持 `dpi`、`pages`、`output`，CLI 参数优先；相对路径均相对于当前工作目录。

```text
data/
├── input/                         # 原始文件，只读使用
│   └── demo.pdf
└── processed/
    └── doc_<SHA256前16位>/        # PDF 内容标识；同名不同内容不会覆盖
        ├── pages/
        │   ├── page_0001.png
        │   └── ...
        ├── pages.jsonl
        ├── manifest.json
        └── audit.json
```

PNG 路径相对于其所属文档输出目录，便于整体搬移。相同 PDF 内容（即使改名）复用同一输出；内容变化生成新目录，manifest 同时记录完整 SHA-256。原始 PDF 不会被修改。

### 重复运行与恢复

- 成功记录且 DPI、重新计算的页面几何、PNG 解码、尺寸和哈希均匹配时跳过。`--force` 强制重渲染指定页。旧坐标矩阵不匹配会重建该请求页，未请求页不会自动迁移。
- 缺失、损坏、失败、未完成或 DPI 变化的指定页会重新渲染。未请求的历史页保持原样。
- 多次页码范围取并集；`pages.jsonl` 按页码排序，每个已计划页面仅一条记录。初始待处理记录先落盘，每渲染一页原子替换索引和进度，支持中断后重跑。
- DPI 可以逐次变化，当前请求页使用新 DPI；每条记录的 `render_dpi` 才是该页的真实值，manifest 的 `last_run.render_dpi` 仅描述最近请求。
- 跳过的页面保留已有解析字段；重新渲染会清空该页 OCR/布局结果并重置为 `not_run`，避免坐标失效。
- prepare 写入前检查全部历史记录的渲染核心结构。已有 JSONL 无法解析、缺字段、核心类型/状态错误、路径越界、重复页或 document ID 不匹配时拒绝覆盖，即使损坏页不在当前请求范围内。已有 manifest 的基本身份、schema 和页范围也需合法。解析区域及来源的深层检查由 Runner/audit 执行；先运行 audit，备份并修复索引或使用新的输出根目录。结构合法的失败/待处理页和损坏 PNG 仍可重跑恢复。
- 同一文档输出目录仅允许**一个写入进程**。原子替换防止半条 JSON，未实现多进程写锁或跨文件事务。磁盘写入失败会中止，之后可审计和恢复。

## 索引 Schema

每行一个已计划 PDF 页面，未选择的页面不写入当前索引；manifest 的 `expected_pages` 指定审计范围。

| 字段 | 含义 |
| --- | --- |
| `document_id`, `pdf_page` | 文档内容标识、1-based PDF 物理页码 |
| `printed_page` | 印刷页码字符串/整数，未知为 `null`，绝不默认复制物理页码 |
| `page_width`, `page_height` | 旋转后的可见 CropBox 宽高，PDF point（1/72 英寸） |
| `rotation`, `media_box`, `crop_box` | 页面旋转角度及原始页面框，框为 PyMuPDF 返回的 point 坐标 |
| `pdf_to_mupdf_matrix`, `mupdf_to_image_matrix` | 六元素仿射矩阵，辅助原生 PDF → PyMuPDF → 图像像素坐标转换 |
| `image_width`, `image_height`, `render_dpi` | PNG 实际像素尺寸及渲染 DPI |
| `page_image_path`, `page_image_sha256` | 相对路径及图像完整性指纹 |
| `text_blocks`, `page_text` | 有可靠 bbox 的文字块；无可靠 bbox 的页面文字单独保存，不伪造坐标 |
| `image_regions`, `layout_regions`, `relations` | 实际图像区域、通用布局区域、区域关系；布局标题/段落/表格不强制解释为图片 |
| `parser_provenance` | 每阶段最后接受结果的 Adapter 标识、输入 PNG 哈希、可选空结果原因 |
| `render_status`, `ocr_status`, `layout_status` | `not_run` / `success` / `failed` |
| `errors` | 错误列表：`stage`, `message`, `error_type` |
| `schema_version`, `bbox_coordinate_system` | 新页面为 `1.1`，兼容读取 `1.0`；`image_pixels_top_left_xyxy` |

失败或未处理页面的未知几何/图像字段允许 `null`。bbox 统一为渲染 PNG 的 **像素坐标 `[x0, y0, x1, y1]`，左上角原点，x 向右、y 向下**；不是归一化坐标。manifest / audit 的独立版本仍为 `1.0`。详细字段及兼容规则见 [docs/schema.md](docs/schema.md)。

第二轮修正了旋转页面直接使用 `page.transformation_matrix` 时丢失 CropBox 偏移的问题，从页面框独立计算原始 PDF → 未旋转坐标，再沿用旋转/DPI 矩阵转成 PNG 坐标。当前明确拒绝非默认 UserUnit 导致的尺度变化和越界 CropBox；这些页会报告 `Unsupported page geometry`，不生成伪正确矩阵。

印刷页码只能根据证据补充。单 PDF 可提供人工映射：

```bash
multimodal-pdf-qa prepare data/input/demo.pdf --pages 1-3 --printed-pages configs/printed_pages.example.json
```

示例映射仅说明格式，不代表真实报告页码；复制为 `configs/local_pages.json` 后再编辑。未指定的键保留历史值，显式 `null` 清空历史值。也不会自动把 PDF page label 当作可信印刷页码。

## 完整性检查

`manifest.json` 记录源文件哈希、真实页数快照、计划页集合、渲染器版本和最新进度；`audit.json` 独立检查：

- 重新打开源 PDF，核对实际页数和 SHA-256；JSONL 行数、有效对象数、重复/缺失/意外页码。
- 字段、状态、PNG 存在性与可解码性、尺寸、DPI 比例和 SHA-256。
- 成功、失败及不可用页面数量；哪些页面 OCR/布局仍为 `not_run`。
- manifest.schema_version、预期页数、成功/失败/待处理计数，以及 complete 声明是否与当前产物一致。running 只有计数守恒且可由一页写入解释的滞后才作为 warning；较大或方向不可能的差异是 issue。incomplete 在产物恢复后可通过并提示重跑收尾。
- 解析区域类型、bbox、置信度、ID/引用、阶段状态及结果对应的 PNG 哈希。`invalid_parser_records` 单独列出模型数据结构问题，使 `ok=false`，不把这类问题伪装成 PNG 渲染失败。

audit 新增 `render_complete`（索引及 PNG 完整性）、`manifest_status` 和 `index_render_counts`。manifest 计数来自索引状态，audit 顶层成功数来自实际可用 PNG，二者在图像损坏后可能不同。新检查点保存 `pending_render_count`，旧 v1.0 文件可缺省此字段。详细状态/兼容规则见 Schema 文档。

批量 audit 会发现包含 `manifest.json`、`pages.jsonl` 或 `pages/` 的直接子目录；候选目录缺 manifest 时明确报告错误并返回 1，不再静默遗漏。普通说明目录、只有 doc_ 名称的空目录会被忽略。范围不递归到更深层。

OCR/布局 `not_run` 是正常待处理状态，不会导致 audit 失败。`success` 必须是 Adapter 执行后经校验接受的结果；空数组本身不能作为成功证据。audit 的 `ok` 是结构、阶段状态及渲染完整性结论，**不代表 OCR 或语义质量通过**。

若源 PDF 已搬移，默认 audit 会给出 warning，`source_verified=false`、`actual_pdf_page_count=null`，仅对现有产物给出完整性结论。可用 `audit <文档输出目录> --pdf <新路径>` 重新核验；显式源路径不存在、内容变化或页数不符均不通过。索引损坏时 audit 记录问题，不自动修复。manifest 本身无法读取/格式无效时 CLI 非零退出，不能生成可信报告。

## Adapter 程序调用与接手方式

公共入口为 `multimodal_pdf_qa.runner.run_parser`，仅提供 Python API。本项目不附带真实模型实例；以下函数接收后续实现的后端：

```python
from pathlib import Path
from multimodal_pdf_qa.adapters import DocumentParserAdapter
from multimodal_pdf_qa.runner import run_parser

def parse_prepared_document(document_dir: Path, adapter: DocumentParserAdapter) -> dict:
    return run_parser(document_dir, adapter, pages="1-3", force=False)
```

`document_dir` 指向含 manifest、JSONL 和 PNG 的单个文档目录，传入页码必须已经 prepare。Adapter 提供 `name`、`stages=("ocr",)` / `("layout",)` / `("ocr", "layout")` 和 `parse(image_path, page) -> ParserResult`。`name` 应包含后端版本及影响输出的配置，用作缓存身份。输入图像只读；上下文是副本。

Runner 校验全部历史索引，检查选中页的 PNG 实际尺寸与哈希，调用后端，校验合并候选，再逐页原子写回 JSONL，结束时刷新 audit。只允许成功渲染页进入后端。OCR 拥有 `text_blocks/page_text`，Layout 拥有 `image_regions/layout_regions`；关系按 `Relation.stage` 归属。成功阶段整体替换自己的结果，不追加，不覆盖另一阶段。非法输出或异常标记本次负责的阶段为 `failed`，保留上次有效内容供排查；消费者须检查阶段状态，不能继续把失败阶段的旧数组当作当前成功结果。

有效 bbox、置信度、唯一 ID、关系引用、状态与类型均有运行时校验。没有可靠 bbox 的文字使用 `PageText`；真实执行后确实没有目标，需明确返回对应 `*_empty_reason`，不能只写 `success` 和空数组。再次执行默认跳过同后端/同图像的成功结果；`force=True` 可重新解析。换后端会替换该阶段结果，不自动融合多个模型。

v1.0 未解析记录可按请求页升级为 v1.1；未请求页保留原样。缺少来源证据的旧手工解析结果只能读取并提示未验证，Runner 拒绝自动升级；需先备份并显式重建该页。跨阶段引用必须指向合并后仍存在的 ID；更换区域 ID 前应协调依赖或使用联合 Adapter。完整契约、错误恢复和接入步骤见 [架构](docs/architecture.md) 与 [Schema](docs/schema.md)。

## 后续开发与环境分工

Mac M4 完成输入管理、渲染、索引、质量检查、接口和单元测试；不要求 GPU、网络模型服务或真实报告。PolyU Ubuntu RTX 3080 后续承担 MiniCPM-OCR / PP-DocLayoutV3 的部署与推理实验，具体模型版本、显存需求与运行配置仍需验证，不能预先保证单卡足够。

`DocumentParserAdapter` 的运行、校验与落盘闭环已实现；**尚未实现任何真实模型后端或 CLI 推理命令**。下一步将 MiniCPM-OCR 的真实文字与 PP-DocLayoutV3 的通用布局输出映射到上述契约，并评估少量页面。之后才研究 MLLM 线索提取、Tool Calling、外部数据库检索与页面/区域证据回链。详见 [开发路线](docs/roadmap.md)。

## 测试与版本管理

```bash
python -m pytest
```

pytest 在临时目录用 PyMuPDF 创建多页、旋转、裁剪等测试 PDF；覆盖渲染、元信息、JSONL、页码、错误恢复、重复运行和 CLI，不访问服务器或模型。`tests/mock_adapters.py` 的所有文字、布局和关系均为人工定义的合成测试数据，仅用于临时 PDF。`scripts/create_demo_pdf.py` 为手动复现提供三页合成数据。

2026-11-25 本地验证：**35 项测试通过**（Python 3.11.18 / PyMuPDF 1.28.2 / pytest 9.2.0）。两份真实报告实读页数分别为 107 和 128，均只处理前 3 页，144 DPI，共 6 页成功，源文件哈希与 audit 通过。视觉抽查未见明显旋转或裁剪错误；这不代表整份报告或未来 OCR 质量验收完成。PyMuPDF 底层 SWIG 输出了弃用警告，未导致测试失败。

2026-11-26 第二轮：基线 35 项通过，修复后 **95 项测试通过、5 条 SWIG 弃用警告**，见 [iteration_02.md](docs/progress/iteration_02.md)。第三轮以已审阅提交 `94dafcd` 为基线，实现 R7 最小数据闭环并收紧 running 审计边界，新增 100 项用例，**195 项通过、5 条相同 SWIG 弃用警告**，见 [iteration_03.md](docs/progress/iteration_03.md)。原[第一阶段审查报告](docs/progress/phase1_review_report.md)保留为历史证据。R5 渲染器版本溯源、R6 依赖锁定、Ubuntu 实测与真实模型推理仍为后续工作。

可提交：`src/`、`tests/`、`scripts/`、`configs/*.example.*`、`docs/`、`README.md`、`pyproject.toml`、`.gitignore` 和两个 `.gitkeep`。不提交原始文档、渲染图片、处理结果、模型/缓存、环境、密钥、服务器凭据或私人配置。现有 [Private Repository](https://github.com/BennettHuangGuobin/Multimodal-PDF-QA) 的第二轮提交 `94dafcd` 已由用户上传并完成核心源码审阅；第三轮仅本地修改，尚未 commit/push，也不生成源码 ZIP。`.gitignore` 不是保密扫描工具，每次提交前仍应查看暂存文件清单；项目元信息和输出中可能包含本机路径与第三方报告内容。
