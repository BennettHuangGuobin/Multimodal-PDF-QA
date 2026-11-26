# 第一阶段工程验收与审查准备报告

- 项目：Multimodal-PDF-QA，版本 `0.1.0`。
- 日期：2026-11-25，时区 Asia/Shanghai；真实产物复核时间 22:31。
- 工作性质：Review Preparation。读取真实代码与文档、重跑既有测试、在临时目录复现边界问题、核对已有产物、整理审查材料。
- 变更范围：仅新增本报告与源码 ZIP；没有修改已有业务代码、测试、配置、README 或架构文档。原始 PDF 和已有处理产物保持不变。
- 验收结论：**可作为第一阶段工程基线交给技术审阅者；不等于所有边界验收通过。** 当前主流程与真实报告前 3 页产物通过，另发现 4 项 Medium、3 项 Low 问题/接入限制，详见第 6 节。未发现需要立即停止当前本地样本处理的 High 问题，不表示已证明不存在其他问题。

## 1. 项目当前目标

这是香港理工大学课题组的横向工程项目，目标是理解复杂长篇图文 PDF，提取时尚商品及消费趋势线索，联动外部数据库，并能把检索结论回链到报告页面和区域；当前不是论文投稿系统。

总体流程：

```text
PDF → 页面渲染 → OCR / 文档 MLLM → 结构化索引
    → MLLM 线索提取 → Tool Calling → 外部数据库检索 → 证据回链
```

当前实际边界为 Mac CPU 的基础处理工程：PyMuPDF 读取元信息、逐页渲染，建立尚无 OCR 结果的页面索引，检查产物完整性。源码主流程没有 `get_text()` 抽取，也没有模型推理或收费 API 调用；不会把原生文字抽取当作 OCR。未来模型负责真实图文解析，当前仅有接口契约。

## 2. 本轮实际完成的检查及功能核验

下表描述**现有实现**，不是声称本轮新增了这些功能。文件位置均相对于项目根目录；函数名可以直接搜索定位。

| 功能 | 主要文件、函数/类 | 实现方式 | 实际证据与相关测试 | 核验结论 |
| --- | --- | --- | --- | --- |
| PDF 发现及打开 | `src/multimodal_pdf_qa/pdf.py`：`discover_pdfs`、`open_pdf` | 单文件或目录当前层扫描；扩展名不区分大小写；拒绝非 PDF、需要口令或零页文件 | `test_discovery_and_identity`、`test_encrypted_pdf_rejected`；本轮重新打开两份真实 PDF | 已验证现有测试范围；无口令但受权限限制的加密 PDF 未单独验证 |
| 元信息、文档 ID | 同文件：`inspect_pdf`；`storage.py`：`sha256_file` | 分块 SHA-256，前 16 位构造 ID，保留完整哈希；逐页读取尺寸、旋转、页面框 | `test_inspect_metadata_geometry`、`test_discovery_and_identity`、`test_inspect_cli`；真实文件实读 107/128 页并核对完整哈希 | 已验证 |
| 页码范围 | `pdf.py`：`select_pages`；`prepare.py`：`prepare_pdf` | 1-based 闭区间及逗号列表，去重排序；调用 `load_page(number - 1)`；省略范围为全量 | `test_page_selection`、8 个非法范围参数化用例、`test_page_range_union_dpi_change_and_printed_mapping` | 已验证；真实报告本轮没有全量渲染 |
| 顺序 PNG 渲染 | `prepare.py`：`render_page` | `get_pixmap(dpi=..., RGB, alpha=False, annots=True)`；逐页生成临时 PNG 后替换目标文件；默认 144 DPI | `test_prepare_schema_audit_and_resume`、`test_transforms_match_visible_colored_region`；6 张已有真实 PNG 均可解码 | 已验证；大单页峰值内存和极高 DPI 未验证 |
| 页面索引与检查点 | `models.py`：`PageRecord`；`storage.py`：`write_jsonl`、`atomic_text`；`prepare.py` | 先写计划页，后逐页重写已排序 JSONL；文字输出先 flush/fsync 再 replace；manifest 记录计划并集和最近进度 | `test_prepare_schema_audit_and_resume`、`test_interrupt_and_resume`、真实 JSONL 计数检查 | 主路径已验证；没有跨文件事务，损坏语义记录存在 R4 问题 |
| 印刷页码 | `models.py`：`PageRecord.printed_page`；`prepare.py`：`load_printed_pages` | 默认 `None`，独立人工 JSON 映射，可为字符串、整数或 null；不读取 PDF page label 推测页码 | `test_page_range_union_dpi_change_and_printed_mapping`、`test_invalid_mapping_and_dpi`；6 条真实记录均为 null | 基本路径已验证；映射清空/保留的全部边界未单测 |
| 坐标和区域结构 | `pdf.py`：`page_geometry`、`transform_bbox`；`models.py` | 页面尺寸为 point；区域 bbox 为 PNG 像素 xyxy；存旋转、MediaBox/CropBox 和两段仿射矩阵 | 既有坐标测试通过；本轮增加独立原生 PDF bbox 临时探针发现 R1 | **部分验证，存在已复现错误**；不可笼统标为坐标转换全部通过 |
| 跳过、失败恢复与中断 | `prepare.py`：`prepare_pdf` | 跳过前核对成功状态、DPI、字段、解码、尺寸、图像哈希；重渲染清空下游结果；单页异常记录后继续 | `test_prepare_schema_audit_and_resume`、3 个 PNG 损坏用例、`test_render_failure_then_retry`、`test_interrupt_and_resume`、`test_force_resets_downstream_status` | 已验证模拟错误和 KeyboardInterrupt；未做断电、SIGKILL、磁盘耗尽或并发写测试 |
| 完整性 audit | `audit.py`：`audit_document`、`record_errors`、`check_image` | 对源 PDF 哈希/页数、JSONL 行数/页码/字段、PNG 内容/尺寸检查；`not_run` 不视为 OCR/布局失败 | `test_audit_missing_duplicate_and_malformed_rows`、5 个坏记录用例、`test_source_relocation_and_mismatch` 等；真实产物副本重新 audit | 当前样本通过；manifest 汇总及批量目录覆盖存在 R2/R3 |
| Adapter | `adapters.py`：`DocumentParserAdapter`、`ParserResult`；`models.py`：`TextBlock`、`ImageRegion`、`Relation` | Protocol 定义 `parse(image_path, page) -> ParserResult`；文本、bbox、来源、置信度、阅读顺序、类型和关系有字段 | 实际读取接口和导入路径；没有后端推理/结果合并测试 | **仅接口实现已确认**；模型接入、结果验证、持久化未实现，见 R7 |
| CLI 与配置 | `cli.py`：`build_parser`、`prepare_options`、`main`；`pyproject.toml` | argparse 三命令；TOML 默认项，CLI 优先；逐文档捕获异常继续；模块入口和安装入口 | `tests/test_cli.py` 全部 6 项通过，包括真实子进程入口；异常边界另见 R3/R4 | 正常命令路径已验证；异常分类存在文档差异，见第 6 节 |
| 文档与交接 | `README.md`、`docs/*.md`、`.gitignore` | 中英文技术说明，阶段状态、恢复边界、使用命令与数据排除规则 | 本轮逐文件对照源码及真实产物；候选打包文件逐项扫描 | 功能阶段描述基本准确；需带本报告一并审阅 |

## 3. 实际目录结构与模块职责

以下是交接所需文件，不展开环境、缓存或真实数据。ZIP 只包含此处的文件，目录路径也是 ZIP 内部路径。

```text
README.md
pyproject.toml
.gitignore
src/multimodal_pdf_qa/
├── __init__.py
├── __main__.py
├── adapters.py
├── audit.py
├── cli.py
├── models.py
├── pdf.py
├── prepare.py
└── storage.py
tests/
├── conftest.py
├── test_cli.py
└── test_pipeline.py
scripts/
└── create_demo_pdf.py
configs/
├── prepare.example.toml
└── printed_pages.example.json
docs/
├── architecture.md
├── roadmap.md
├── schema.md
└── progress/
    └── phase1_review_report.md
```

| Python 模块 | 职责 |
| --- | --- |
| `__init__.py` | 包描述及版本号 `0.1.0` |
| `__main__.py` | `python -m multimodal_pdf_qa` 转发 CLI |
| `pdf.py` | PDF 发现、打开、哈希元信息、页面几何、bbox 变换、页码选择 |
| `prepare.py` | 人工印刷页码映射、渲染、复用判断、逐页检查点及 manifest |
| `storage.py` | 流式哈希、原子 JSON/JSONL、逐行读取、安全相对路径解析 |
| `models.py` | dataclass 数据结构和阶段状态类型；类型注解不等于运行时校验 |
| `audit.py` | 索引/图像/源文件检查，返回并写入 audit；不会修复索引 |
| `adapters.py` | 未来解析后端的 Protocol 和结果数据类型，没有可执行后端 |
| `cli.py` | 命令解析、配置优先级、批量调度、JSON 输出与退出码 |
| `tests/conftest.py` | 临时创建四页 PDF，含 90°旋转、CropBox 偏移、270°旋转叠加裁剪 |
| `tests/test_pipeline.py` | 元信息、页码、渲染、产物、恢复、坐标、映射与加密输入测试 |
| `tests/test_cli.py` | inspect/prepare/audit、配置覆盖、失败继续、入口子进程测试 |
| `scripts/create_demo_pdf.py` | 三页合成演示文件生成器，拒绝覆盖已有目标；无 OCR |

## 4. 测试执行与证据

### 4.1 本轮 pytest 实际结果

在现有环境执行用户指定命令，退出码为 0：

```bash
.venv/bin/python -m pytest -q
```

实际摘要：

```text
...................................                                      [100%]
35 passed, 5 warnings in 0.56s
```

- 收集并执行 35 个测试用例（包括参数化展开），通过 35，失败 0，错误 0，跳过 0。
- 当前平台：macOS 15.6.1、arm64；Python 3.11.18、PyMuPDF 1.28.2、pytest 9.2.0。
- 5 条 `DeprecationWarning` 来自导入时的 SWIG 内建类型：`SwigPyPacked` 2 条、`SwigPyObject` 2 条、`swigvarlink` 1 条，均为缺少 `__module__`。
- pytest 摘要之后，进程退出还输出了一次 `sys:1: DeprecationWarning: builtin type swigvarlink has no __module__ attribute`；这是额外输出，未计入摘要的 5 条。
- 测试依靠临时合成 PDF，没有读取第三方报告或加载模型。没有安装额外测试插件，也没有生成覆盖率统计；**行/分支覆盖率未测量**。

### 4.2 两份真实报告已有产物

核验方法：读取原 manifest/JSONL/audit；把每份文档的少量已有输出复制到临时目录，在副本调用实际 `audit_document`，仍读取原 PDF 校验完整哈希和实际页数。另将每条记录的尺寸、旋转、CropBox 与原 PDF 对应页直接比对。原 audit 除 `checked_at` 外与本轮重算结果完全一致。

| 材料 | 实读/manifest 总页数 | 计划页与 JSONL 页码 | PNG 与 JSONL | 图像参数 | 本轮核验 |
| --- | --- | --- | --- | --- | --- |
| 《26春夏抖音电商时尚消费趋势报告》 | 107 / 107 | `[1,2,3]` | PNG 3 张；JSONL 3 行/3 条有效记录 | 均 144 DPI、1225×1650 px | 源文件哈希一致；PNG 可解码、尺寸/哈希一致；audit `ok=true` |
| 《26SS春夏淘宝服饰白皮书》 | 128 / 128 | `[1,2,3]` | PNG 3 张；JSONL 3 行/3 条有效记录 | 均 144 DPI、1225×1650 px | 源文件哈希一致；PNG 可解码、尺寸/哈希一致；audit `ok=true` |

两份产物均无缺页、重复页、意外页或渲染错误；manifest 的当前汇总计数与独立重算一致。6 条记录的 `printed_page=null`，OCR/布局均 `not_run`，区域和关系为空。

原 manifest 最近一次请求均为前 3 页，计数是 `rendered=0, skipped=3, failed=0`。这是**已有产物中的历史记录**，本轮没有再次对真实报告执行 prepare；当前重复运行与恢复能力由重新执行的合成测试验证，不能把历史记录冒充本轮运行日志。

对 `data/` 内本轮读取的全部 16 个文件（含占位文件）做检查前后 SHA-256 比较，完全相同。本轮没有重渲染真实 PDF，没有覆盖原 `audit.json`，也没有修改原始 PDF。

**未验证**：其余 104/125 页、完整报告全量处理、逐页视觉质量、文字识别准确率、图文关系、OCR/布局模型。本轮没有重新做人工视觉逐页评审；旧 README 的视觉抽查描述是上一轮记录。

### 4.3 重复执行、失败与中断

- PNG 的 mtime 与 JSONL 在重复执行中保持一致，最近运行计数为跳过；对应 `test_prepare_schema_audit_and_resume`。
- 缺失 PNG、不可读文件、被替换为另一张有效 PNG：3 个参数化用例均由 audit 检出，重跑仅修复受影响页。
- 注入第 2 页 `RuntimeError`：第 1/3 页继续成功，第 2 页有真实错误记录；移除注入后重试成功。
- 注入第 2 页 `KeyboardInterrupt`：第 1 页保存成功，后两页为 `not_run`；恢复后全部成功。
- 变更 DPI/强制重渲染、页范围并集及 OCR 状态重置有测试；没有通过运行模型验证下游数据融合。
- 原子文件替换不等于多文件事务或进程锁；真正掉电、SIGKILL、文件系统写入失败、同目录两个写入者均未实测。

### 4.4 本轮补充探针

探针在临时目录和合成 PDF 上执行，不加入项目测试、不修改业务代码；它们不计入 35 个 pytest 用例。

| 探针 | 操作 | 实测结果 |
| --- | --- | --- |
| 原始 PDF 坐标的独立真值 | 240×320 point 页面先画矩形，再设置非零 CropBox 和旋转 | 0°一致；90/180/270°使用存储矩阵得到错误 bbox，见 R1 |
| manifest 与索引矛盾 | 把两页产物的预期数改为 999、成功数改为 0、失败数改为 999、schema 改为不支持的值 | audit 仍 `ok=true`、`issues=[]`，见 R2 |
| 缺 manifest 的输出目录 | 合法文档目录旁增加含 `pages.jsonl`、没有 manifest 的文档目录 | 根目录 audit 退出码 0，只输出 1 份报告，见 R3 |
| 未请求页缺字段 | 两页索引第 2 页删除 `render_status`，重跑只请求第 1 页 | 抛 `KeyError('render_status')`；manifest 已改写为 `running`，见 R4 |
| 历史渲染器版本 | 将临时 manifest 的版本改为标记值，再正常重跑 | 0 页渲染、2 页跳过，但版本被改成当前 1.28.2，见 R5 |
| 区域结果非法 | 临时记录写入越界 bbox、置信度 9、重复 ID、悬空关系 | audit 仍 `ok=true`，见 R7；这是缺失的模型结果校验，不是 OCR 推理 |

### 4.5 尚未覆盖的重要测试

1. R1–R4 的失败行为没有既有回归测试；现有 35 项全部通过不能排除这些缺陷。
2. `test_bad_config_and_empty_input` 实际只测试空目录，未测试坏 TOML、未知配置键或错误字段类型，名称大于覆盖范围。
3. 原始 PDF 已知坐标真值、180°旋转、非零 MediaBox、UserUnit、更多非整数 DPI/尺寸组合仍需系统覆盖；本轮探针只补充了一个固定裁剪样本。
4. CLI 的真实中断退出码 130、操作系统信号、磁盘/权限错误、并发写入及 JSON/PNG 替换边界未覆盖。
5. Adapter 深层校验、JSON → 嵌套 dataclass 恢复、结果合并、模型输出失败类型和版本溯源未测试，也未实现完整 runner。
6. 干净环境从 ZIP 安装、最低依赖版本、其他 Python 版本、Ubuntu CPU 和 RTX 3080 均未验证。

## 5. 数据结构及真实脱敏示例

### 5.1 三份产物的关系

| 文件 | 角色 | 核心关联 |
| --- | --- | --- |
| `manifest.json` | 文档级源信息、任务范围和运行快照 | 完整源 SHA-256、`document_id`、实际页数快照、历次 `expected_pages` 并集、最近请求和计数、渲染器版本 |
| `pages.jsonl` | 每个计划页面的主记录 | 同一 `document_id`；`pdf_page` 唯一；图像路径、几何、阶段状态、区域及错误；未计划页面不写入 |
| `audit.json` | 某时刻重新检查的报告 | 从 manifest 取计划集合，对源 PDF、JSONL 和 PNG 实测，记录缺失/重复/失败及待处理阶段；不应作为主索引 |

`manifest.status=complete` 只表示计划页的准备任务完成，不意味着 PDF 所有页处理完，更不意味着 OCR 完成。最近请求页可以只是累计计划范围的一部分；每页真实 DPI 以 JSONL 为准。

audit 目前没有校验 manifest 所有冗余统计字段，也不检查历史 audit 是否过期；**三个文件存在设计上的关联，但通用相互一致性不能认定为已完全验证**。本轮对两份实际产物的冗余计数另做比对，结果一致。

默认源路径缺失时，audit 将其视为 warning，仍可能 `ok=true`，同时 `source_verified=false`、实际页数为 null；这是已文档化的行为。需要严格验收源文件时，必须同时检查 `source_verified`，或用 `--pdf` 指定搬迁后的文件。

### 5.2 真实记录，经过脱敏

以下取自第一份实际报告物理第 1 页。**仅替换 `document_id` 与 `page_image_sha256` 两个文件指纹，其余字段和值未修改**；未复制文档标题、报告正文、绝对源路径或任何图片。为可读性展开排版，原 `pages.jsonl` 中它占单行。占位指纹不能直接用于生产 audit。

```json
{
  "document_id": "doc_REDACTED",
  "pdf_page": 1,
  "render_dpi": 144,
  "page_image_path": "pages/page_0001.png",
  "printed_page": null,
  "page_width": 612.239990234375,
  "page_height": 824.8800048828125,
  "image_width": 1225,
  "image_height": 1650,
  "rotation": 0,
  "media_box": [0.0, 0.0, 612.239990234375, 824.8800048828125],
  "crop_box": [0.0, 0.0, 612.239990234375, 824.8800048828125],
  "pdf_to_mupdf_matrix": [1.0, 0.0, 0.0, -1.0, 0.0, 824.8800048828125],
  "mupdf_to_image_matrix": [2.0, 0.0, 0.0, 2.0, 0.0, 0.0],
  "page_image_sha256": "REDACTED_SHA256",
  "text_blocks": [],
  "image_regions": [],
  "relations": [],
  "render_status": "success",
  "ocr_status": "not_run",
  "layout_status": "not_run",
  "errors": [],
  "schema_version": "1.0",
  "bbox_coordinate_system": "image_pixels_top_left_xyxy"
}
```

该记录只表示页面渲染成功。OCR/文档 MLLM 和布局均没有运行，核心 MLLM 更未运行；schema 目前没有单独的核心 MLLM 状态字段。空数组不是识别出了“没有内容”。`printed_page=null` 是未知印刷页码，绝不能根据物理第 1 页把它改为 1。

### 5.3 bbox、旋转与裁剪

约定 bbox 为 `[x0,y0,x1,y1]`、原始 PNG 像素、左上原点、x 向右/y 向下，允许浮点；统一到最终 PNG 空间有利于后端接入。页面宽高为旋转后的 `page.rect` point 尺寸；MediaBox/CropBox 保留 PyMuPDF point 坐标，不是区域像素 bbox。

`mupdf_to_image_matrix` 按旋转、DPI 缩放、pixmap 原点平移组合；现有测试以真实绘制区域像素验证了这一段。`pdf_to_mupdf_matrix` 直接保存底层属性，其在“旋转 + 非零 CropBox”组合下存在 R1；目前 OCR 区域为空，因此真实报告样本没有错误区域被写出，但未来 PDF 原始坐标回链不能直接信任这一段。

## 6. 当前技术问题与严重程度

分级口径：High 为当前主路径严重数据损坏/无法使用或敏感泄露；Medium 为已复现的局部正确性/验收可靠性问题；Low 为溯源、复现性或已披露的阶段性接入限制。本轮只记录，不修复。

### High

本轮检查范围内未发现。没有进行漏洞扫描、恶意 PDF 模糊测试或依赖安全审计，因此不能给出全面安全认证。

### R1 — Medium：原生 PDF 坐标在旋转叠加裁剪时转换错误

- 位置：`src/multimodal_pdf_qa/pdf.py:30–44`；对外约定在 `docs/schema.md` 坐标系章节。
- 触发：页面有非零 CropBox 偏移，并旋转 90/180/270°；直接使用存储的 `pdf_to_mupdf_matrix` 转换原生 PDF bbox。
- 实测：240×320 point 页面，裁剪 `[20,30,220,290]`，绘制区域的原始 PDF bbox 为 `[40,140,120,230]`。正确未旋转 PyMuPDF bbox 是 `[20,60,100,150]`，存储矩阵却给出 `[40,30,120,120]`。0°时结果正确。
- 复核：另将四种旋转保存成真实临时 PDF，经当前 `prepare_pdf` 重新打开、渲染并写出 JSONL，再取落盘矩阵验证，得到相同结果；不是仅发生在内存编辑状态的问题。
- 影响：后续原始 PDF 区域与渲染图像的对应位置错误；不影响当前 `get_pixmap` 本身，也不代表从已有 PyMuPDF bbox 到 PNG 的那段转换已失败。
- 测试遗漏：`tests/test_pipeline.py:240–241` 用同一 `page.transformation_matrix` 的逆生成“原始框”，再正向变换，属于自洽检查，不能作为独立原始 PDF 真值。
- 建议：下一轮先增加独立内容坐标真值测试，再修正/限制矩阵生成契约，修复前标明该组合不可直接用于原始 PDF 回链。

### R2 — Medium：audit 可接受与 JSONL 矛盾的 manifest 汇总和版本

- 位置：`src/multimodal_pdf_qa/audit.py:82–108,146–169`。
- 实测：两页正常合成产物，将 manifest 改为 `expected_page_count=999`、`successful_render_count=0`、`failed_render_count=999`、`status=incomplete`、不支持的 `schema_version`，audit 仍返回 `ok=true`、`issues=[]`、重算预期页数 2。
- 原因：只验证 `expected_pages` 与源页数等部分字段，重算 JSONL 计数时未核对 manifest 的冗余计数、版本和状态语义。
- 影响：使用者可能把“audit 通过”误解为全部产物一致。本轮真实文件的这些字段经独立核对一致，未发现实际损坏。
- 建议：明确哪些字段是权威/缓存值，对不一致给出诊断；中断后的 `running` 等状态可按语义分类，不宜一律当失败。

### R3 — Medium：批量 audit 静默漏掉缺少 manifest 的文档目录

- 位置：`src/multimodal_pdf_qa/cli.py:57–71`。
- 实测：processed 根目录下有一个合法目录和一个仅含 `pages.jsonl` 的文档目录，`audit <root>` 返回 0，仅报告合法目录。
- 原因：只枚举 `*/manifest.json`，无法发现丢失/尚未写入 manifest 的目录。
- 影响：根目录级“全部通过”的退出码并不能证明每个候选文档都已审计；直接 audit 孤立目录会报错，但批量模式遗漏它。
- 建议：扫描候选文档目录，对存在 pages/JSONL 却缺 manifest 的目录单独报告，不把无关目录误判为文档。

### R4 — Medium：未请求页语义损坏时，prepare 先改写再失败

- 位置：`src/multimodal_pdf_qa/prepare.py:75–85,108–110,150–154`。
- 实测：两页有效索引删去第 2 页 `render_status`，重跑 `pages='1'`，前置加载只核对页码和 document ID；随后 manifest 已被改写为 `running`，在最终统计处抛 `KeyError('render_status')`。
- 影响：错误不是清晰的索引损坏诊断，且已经改变已有 manifest 的运行上下文。不是正常生成数据必现，也没有证据表明会修改源 PDF。
- 建议：任何写入前校验所有历史记录所需的基本字段；明确可自动恢复与必须拒绝的损坏类型。加入“无关页损坏也不先覆盖”的回归测试。

### R5 — Low：全部跳过时仍覆盖历史 renderer 版本

- 位置：`src/multimodal_pdf_qa/prepare.py:97–118`。
- 实测：临时产物 manifest 的 renderer version 设为历史标记后重跑，`rendered=0, skipped=2`，版本仍被替换为当前 `1.28.2`。
- 影响：升级依赖后，旧图像可能被标记成新版本生成；混合历史版本的页面缺少真实来源。当前样本在同一版本生成，不证明已经出现实际版本混用。
- 建议：区分“本次运行环境”与“每页实际渲染来源”，或明确版本变化的缓存策略。无需为了本阶段增加复杂任务系统。

### R6 — Low：依赖范围较宽，尚无跨环境复现证据

- 位置：`pyproject.toml`、`README.md` 安装章节。
- 事实：运行依赖为 `PyMuPDF>=1.24,<2`，开发依赖为 `pytest>=8,<10`，构建依赖 `setuptools>=68`；没有锁文件或完整版本约束快照。
- 影响：下周在另一台机器安装时可能得到不同版本。当前 Mac 成功不能直接等同于 Ubuntu、最低依赖版本或未来版本验证成功。
- 建议：先保留当前已测试版本信息，为交接选择可复现的依赖约束，并在 Ubuntu CPU 环境重跑基础测试。GPU 模型兼容性单独评估。

### R7 — Low：Adapter 是静态契约，未闭合运行时数据校验与恢复

- 位置：`adapters.py:10–30`、`models.py:17–76`、`audit.py:60–62`。
- 事实：dataclass/Protocol 为后端提供良好起点，但没有 JSON 嵌套对象重建、bbox 边界/置信度校验、区域 ID 唯一性、关系引用校验或 ParserResult 合并/持久化 runner。
- 实测：临时 JSONL 中插入越界 bbox、置信度 9、重复 ID、悬空引用，audit 仍可通过；它当前仅检查这些字段是 list。
- 影响：接入真实模型时不能把当前 audit 当作解析结果质检。README 和 schema 文档已经明确未实现深层校验，因此这是**阶段性接入限制，不是虚假完成声明**。
- 建议：先以人工构造的小型结果验证入库契约，禁止无效区域进入统一索引，再接模型。布局中只有区域没有文字的类别目前需要明确表示策略；不可把所有区域都强行当作图片。

### 其他设计与文档观察

- 未见明显不必要的重构需求。stdlib + 单一运行时依赖、顺序处理、Protocol 的规模适合第一阶段。
- 原子重写整份 JSONL 会带来随页数增长的重复序列化；约 100–130 页场景尚可，不建议在缺少性能证据时引入数据库或任务队列。真正内存/耗时基准未做。
- 文档身份拼接、状态字符串、统计逻辑有少量重复；短期可维护，若后续增加 schema 校验可统一权威定义。宽泛 `except Exception` 有助于隔离坏文档，但仅保留文本错误、缺少可选 traceback，调试困难时应改善诊断。
- README 称输入/配置错误返回 2，但非法页范围在逐文档 try 中被捕获，实际属于返回 1 的路径；audit 分支异常写 JSON 结果而不是 stderr。这是从代码确认的文档精度差异，本轮未逐项执行所有退出码分支。
- README 的 Completed/Planned 与模型未执行声明基本属实；`In Progress` 是验收/部署方案准备，不能理解为模型工程已经实现。坐标转换和“完整性”说明需结合 R1–R3 的限制阅读。
- 测试中 `test_bad_config_and_empty_input` 的坏配置部分缺失，适合接手时顺手补齐；不建议为此先做大规模重构。

## 7. 下一阶段建议（最多三个优先任务）

1. **先修复验收可靠性并补回归测试。** 师兄先复现 R1–R4：独立原始 PDF 坐标真值、manifest 汇总一致性、批量孤立目录、历史损坏记录写入前检查；确认改动范围后再修复，同时校准 CLI 文档。当前本轮不做修复。
2. **完成可复现的基础环境交接。** 固定一组已验证依赖，检查 ZIP/新克隆的安装、合成演示与 pytest；在 Ubuntu CPU 环境先通过基础流程，并明确原 PDF 搬迁后的 `--pdf` 用法。评估页面渲染来源记录，不复制 Mac `.venv` 到服务器。
3. **补齐 ParserResult 数据契约再部署模型。** 先确定区域类型、坐标、来源/版本、错误、结果校验与落盘的最小闭环，再在授权的 GPU 环境分别验证 MiniCPM-OCR 和 PP-DocLayoutV3。单卡 RTX 3080 的显存适配尚未验证，不提前承诺；暂不扩展 RAG、Web UI、Agent 或外部数据库服务。

### Ubuntu + RTX 3080 迁移判断

源码仅依赖 Python 3.11+、标准库与 PyMuPDF，无 Mac GUI、SSH、CUDA 或 Linux 专有调用，路径使用 pathlib，**静态检查上适合迁移基础处理层**。真实产物图像路径相对于文档目录，便于搬移；manifest 的 `source_pdf` 为绝对路径，搬移后默认核验会警告，需通过 `audit <目录> --pdf <新文件>` 核对，或用原 prepare 流程更新源路径。

Ubuntu 安装、系统字体差异、PyMuPDF wheel、文件权限/原子替换和全部测试仍是“未验证”；模型推理与驱动/显存不在当前基础工程验收结论内。

## 8. GitHub 与源码 ZIP 交接检查

### 8.1 当前仓库及忽略规则

- 当前工作区没有 `.git/`；本轮未初始化 Git、未创建远程、未提交或 push，无法给出暂存区或历史提交的保密审计结论。
- `.gitignore` 将 `data/input/*`、`data/processed/*` 排除，只例外保留各自 `.gitkeep`；因此两份真实输入和当前 PNG/JSONL/manifest/audit 均在默认排除范围内。
- 已有规则排除虚拟环境/本地 Python/uv 缓存、常见模型权重与 checkpoint、`.env`、`.aws/`、`.ssh/`、私钥扩展名和 local/private 配置。
- 本轮实际源码、公开示例、README、文档候选文件中未发现真实 API Key、访问令牌、服务器 IP、服务器口令或私钥。规则检查是静态审阅，未为了测试规则初始化 Git。
- `.gitignore` 不能阻止强制添加文件，也无法识别任意源码中的未来硬编码密钥；不能对未来所有提交作绝对保证。确认的是**当前审查白名单与当前默认排除路径**。

### 8.2 打包清单及敏感内容检查

ZIP 使用显式白名单，共 **22 个文件**：根目录 3 个，src Python 9 个，tests Python 3 个，scripts Python 1 个，公开配置示例 2 个，docs 文档 4 个（含本报告）。完整清单见第 3 节。

检查方式：逐个读取 UTF-8 文本；扫描私钥头、常见 API/访问令牌、凭据赋值、含认证信息 URL、IPv4 地址和 SSH/SCP 连接目标；结合人工源码审阅确认命中性质。拒绝符号链接、二进制文件和任何白名单外路径。

唯一凭据赋值命中是 `tests/test_pipeline.py:270` 的加密 PDF 测试固定值 `owner` / `user`。它们仅为 pytest 临时文件内的合成加密口令，没有服务器、账户或外部连接，不是真实敏感凭据，因此测试源码原样保留。没有把真实密钥“脱敏后仍直接打包”的情况，也没有为了扫描修改源码。

本报告仅包含一条已去除文件指纹的页面几何记录，不包含第三方报告正文或图片。README 已公开说明的两份材料名称保留用于对应验收对象。

排除项：`data/` 全部内容（包括占位文件也不放入此次 ZIP）、`.venv/`、`.python/`、`.tools/`、`.uv-cache/`、`.git/`、`__pycache__/`、`.pytest_cache/`、egg-info、密钥/私人配置、权重、真实或测试生成的 PDF/图片。ZIP 不携带任何可执行二进制环境。

打包采用相对文件路径，ZIP 中直接包含 README 等文件，不嵌套本机绝对路径。完成后校验 ZIP 成员集合、CRC 和解压字节与源文件 SHA-256 一致；ZIP 自身 SHA-256 在交付消息中提供，避免报告自引用造成哈希循环。

### 8.3 审阅者可复现的入口

当前工作区可以直接运行：

```bash
.venv/bin/python -m pytest -q
.venv/bin/multimodal-pdf-qa inspect data/input
.venv/bin/multimodal-pdf-qa audit data/processed
```

注意：最后一条命令会更新本地 audit 的检查时间；本轮为了保留原产物，实际采用临时副本审计。`audit <root>` 还有 R3 已知限制。

ZIP 不含真实数据和虚拟环境。独立审阅者在具备 Python 3.11+ 的环境中按 README 建立虚拟环境，执行 `python -m pip install -e '.[dev]'` 和 `python -m pytest -q`；然后可用 `python scripts/create_demo_pdf.py` 创建合成演示文件。脚本会自动创建缺失的输入目录，prepare 会自动创建处理目录。干净环境安装命令在本轮未重新联网安装验证，不应标成已在 Ubuntu 通过。

**Private GitHub 判断：当前白名单源码与文档适合提交为待审查的第一阶段基线，需保留本报告的已知问题；不应标记为无缺陷最终验收版。** 提交真实数据不在授权范围，未来首次提交仍应核对暂存文件清单。

## 附录 A：坐标问题最小复现

以下只创建内存中的合成 PDF，不读真实资料、不写业务文件：

```python
import pymupdf
from multimodal_pdf_qa.pdf import page_geometry, transform_bbox

for rotation in (0, 90, 180, 270):
    with pymupdf.open() as doc:
        page = doc.new_page(width=240, height=320)
        shape = pymupdf.Rect(40, 90, 120, 180)
        page.draw_rect(shape, fill=(1, 0, 0), color=None)
        raw_pdf_box = list(shape * ~page.transformation_matrix)
        page.set_cropbox(pymupdf.Rect(20, 30, 220, 290))
        page.set_rotation(rotation)
        actual = list(page.get_drawings()[0]["rect"])
        geometry = page_geometry(page, 144)
        converted = transform_bbox(raw_pdf_box, geometry["pdf_to_mupdf_matrix"])
        print(rotation, actual, converted, actual == converted)
```

本轮输出中 0°为 True，90/180/270°均为 False；具体数值见 R1。文件保存并重新经过 prepare 的情况也已复核。修复时应将独立原始坐标与 PNG 像素边界一并加入正式回归测试。

## 附录 B：后续迭代交付约定

根据本轮用户要求，下一轮开始每次开发结束均应在 `docs/progress/` 生成对应迭代报告，记录目标、实际变更、源码位置、测试证据、风险和未完成项，并在对话中按以下八项汇报：

```text
【本轮开发状态】
【实际完成的功能与关键源码】
【测试结果】
【发现的问题及严重程度】
【下一阶段建议】
【生成的审查报告路径】
【生成的源码 ZIP 路径】
【是否适合提交 Private GitHub】
```

如后续轮次未要求打包，ZIP 项如实标明“本轮未生成”，不得假称存在。由技术审阅者检查后再决定后续开发范围。
