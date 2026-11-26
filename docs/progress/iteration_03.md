# 第三轮迭代报告：OCR / Layout Adapter 最小数据闭环

日期：2026-11-26（Asia/Shanghai）。已审阅基线：`94dafcd`。本轮在当前 Mac 本地工作区完成，未 commit/push；不生成源码 ZIP。

## 1. 目标与完成范围

横向工程长期流程仍为：PDF → 页面渲染 → OCR/文档 MLLM → 结构化索引 → MLLM 线索提取 → Tool Calling → 外部数据库检索 → 证据回链。本轮只完成后端接入所需的 Python 数据通路及可靠性验证。

| 状态 | 实际范围 |
| --- | --- |
| Completed | ParserResult 运行时校验、页面对象解码、通用布局/无坐标文字结构、分阶段合并、逐页原子保存、失败与中断恢复、合成 Adapter 测试；running 计数差异的有限宽容；文档与本报告 |
| In Progress | 真实模型版本、环境和少量代表页面的验收准备，尚无实测模型结果 |
| Planned | MiniCPM-OCR / PP-DocLayoutV3 真实后端、Ubuntu/GPU 验证、R5 渲染版本溯源与 R6 依赖锁定；后续 MLLM、数据库检索等业务能力 |

未连接服务器，未安装深度学习框架或下载模型，未调用收费 API，未运行真实报告的 OCR/MLLM，未修改原始 PDF。依赖与 CLI 命令保持现状；没有数据库、队列或 Web UI。

## 2. 源码及具体实现

| 文件 | 关键对象/函数 | 实现及验证 |
| --- | --- | --- |
| `src/multimodal_pdf_qa/models.py` | LayoutRegion、PageText、ParserProvenance、Relation.stage、PageRecord.from_dict | 增加通用布局、无 bbox 页面文字、按阶段来源与关系归属；严格解码列表/对象，旧 v1.0 缺省字段兼容。迁移与非法持久化结构测试通过。 |
| `src/multimodal_pdf_qa/adapters.py` | ParserResult、DocumentParserAdapter | 声明 name/stages/parse；返回文字、布局、关系、独立状态、错误及明确的空结果原因。不导入模型框架。 |
| `src/multimodal_pdf_qa/validation.py`（新增） | validate_adapter、validate_payload、validate_page_payload、validate_parser_result | 纯函数验证类型、有限数值、PNG 像素边界、置信度、阅读顺序、唯一 ID、引用、阶段权限、错误和来源；深拷贝后生成完整候选，验证通过才可保存。54 项直接校验测试。 |
| `src/multimodal_pdf_qa/runner.py`（新增） | run_parser | 全历史索引预检；成功渲染页才调用；PNG 解码/尺寸/哈希校验；模型前后核验输入；阶段替换、失败保留旧值、逐页原子 JSONL、恢复跳过，最后 audit。31 项 Runner 测试。 |
| `src/multimodal_pdf_qa/audit.py` | record_errors、manifest_diagnostics、audit_document | 兼容页面 1.0/1.1；增加 invalid_parser_records；验证解析字段与来源但不冒充语义质检；running 只容许一页合法状态转移的计数滞后。新增 15 项计数回归用例，保留旧恢复测试。 |
| `tests/mock_adapters.py`（新增） | SyntheticAdapter、ocr_result、layout_result、joint_result | 只在 tests 中定义人工结果，来源固定标记 synthetic-test；解码临时 PNG 后返回确定性数据，不做真实推理。 |

`pdf.py`、`prepare.py`、`storage.py`、`cli.py` 本轮没有修改。沿用原子写入工具及既有渲染、坐标、页码、印刷页码映射、异常退出码。prepare 构造新 PageRecord 时自动采用新增字段的默认值和页面 Schema 1.1，强制重渲染继续清空所有下游结果。

## 3. 数据结构与状态契约

页面 Schema 从 1.0 扩展为 1.1，manifest/audit 独立版本仍为 1.0。新增字段：

| 字段 | 用途及默认值 |
| --- | --- |
| `layout_regions` | `[]`；LayoutRegion 承载 title/paragraph/table/figure 等类别，不将标题/表格强制归为图片 |
| `page_text` | `null`；PageText 保存无可靠定位的页面文字，无 bbox，不伪造整页文字框 |
| `parser_provenance` | `{}`；按 ocr/layout 保存最后接受结果的 adapter、image_sha256、empty_reason |
| `Relation.stage` | 默认 layout；OCR 产生的关系显式填 ocr，用于确定替换权限 |

原有 TextBlock/ImageRegion 保持用途和字段。bbox 必须为四个有限数字、正面积并位于**实际已核验 PNG**边界内，保留左上原点 `[x0,y0,x1,y1]` 像素约定。未改变第二轮 PDF→未旋转 PyMuPDF→PNG 矩阵。

OCR 拥有 text_blocks/page_text，Layout 拥有 image_regions/layout_regions。每个成功阶段整体替换自己的数据和关系，不 append；其他阶段原样保留。区域 ID 跨类型唯一；关系引用最终合并结果，避免覆盖布局后留下 OCR 悬空引用。不同模型同阶段输出采用替换策略，不自动融合。

- `not_run`：该阶段未运行或已因重渲染失效，没有数据/来源/错误。ParserResult 中未声明阶段的 not_run 表示不更新已有阶段。
- `success`：Adapter 已被调用且候选通过验证，有内容，或有明确的 `*_empty_reason` 说明实际执行后确实无目标；空数组本身不能获得成功。成功结果必须绑定当前 PNG 哈希。
- `failed`：后端异常、明确失败或非法结果；必须记录错误，不接受部分新失败数据。保留上次接受的内容/来源用于诊断，但阶段为 failed，使用方不能把旧数组当成当前成功成果。重试成功后替换旧值并清除本阶段错误。

兼容性：原 prepare 生成的 v1.0 not_run/空记录可在请求页调用落盘时升级，未请求页保持旧记录，允许同文件混合版本。旧 ImageRegion 不重分类，旧 Relation 缺 stage 可解码为 layout。旧手工解析结果缺可信来源时 audit 警告；Runner 在调用与写入前拒绝自动升级，需先备份并 force-prepare 重建该页。此限制避免补造模型来源、关系归属或空识别原因。

## 4. Runner 调用与检查点

```python
from pathlib import Path
from multimodal_pdf_qa.adapters import DocumentParserAdapter
from multimodal_pdf_qa.runner import run_parser

def parse_pages(directory: Path, adapter: DocumentParserAdapter) -> dict:
    return run_parser(directory, adapter, pages="1-3", force=False)
```

上述函数需要接手者提供真实后端；仓库不提供默认模型实例，不能直接把 Mock 当作报告识别后端。

实际顺序：

1. 校验 Adapter name/stages、manifest 范围和全部历史索引；未请求页解析数据损坏也拒绝，调用数为零且索引/manifest/audit 不改写。
2. 跳过未成功渲染页。对成功页读取 PNG 并核验尺寸/哈希；通过后才考虑缓存跳过或调用模型。
3. 同 name（应含版本/配置）、同图像、所有负责阶段 success 时可跳过；force=True 重新执行。PageRecord 输入为深拷贝。
4. 调用并校验完整候选，再次核验 PNG；模型异常或非法输出记为本次负责阶段失败，保留之前有效内容。联合 Adapter 可显式返回部分阶段失败，合法成功阶段仍可提交；整个对象非法则本次负责阶段全部失败。
5. 原子替换 JSONL，写入成功才推进内存检查点。I/O 异常和中断向上抛出，不误记为模型失败。结束时刷新 audit，不修改渲染 manifest。

如果模型已经执行、JSONL 尚未提交便中断，重试会再次调用模型；不保证 exactly-once 推理。相同输出按阶段替换，不产生重复区域。audit 写入失败可能发生在 JSONL 已提交之后，重新 audit 即可；audit 是快照，不能依赖陈旧报告判断当前文件。

## 5. 发现并修复的问题与行为变化

| 问题 | 修复前 | 本轮行为 |
| --- | --- | --- |
| Adapter 仅为类型契约 | 无安全执行、验证、合并和持久化通路 | Python Runner 贯通实际 PNG→Adapter→严格验证→原子 JSONL→audit |
| 非图片布局与无框文字难表达 | 只有 TextBlock/ImageRegion，易误用图片类型或整页框 | LayoutRegion 与 PageText 分开表达，保留真实未知定位 |
| 区域与状态缺运行时约束 | 可写 NaN、越界框、重复 ID、悬空引用、空 success | 校验拒绝非法结果，失败阶段有错误，成功须内容/明确空原因及来源 |
| 重复执行/不同阶段覆盖风险 | 未有合并策略 | 所有权明确、整阶段替换、跨阶段图验证、缓存与来源绑定 PNG |
| 反序列化边界（开发时发现） | 简单遍历空对象 `{}` 可能将其转成空列表 | from_dict 明确要求列表和对象类型；持久化损坏数据回归测试通过 |
| running 审计过宽 | 任意范围内计数差异都可能 warning | 只允许单页合法转移且两边总数守恒；多页、反向 pending、不守恒成为 issue |
| 旧测试以空数组手工标成功 | 不符合本轮成功契约 | 改用仅测试的 Adapter 生成有效检查点；失败 fixture 补充真实合成错误 |

计数规则来自实际 prepare 写入顺序：初始 manifest→完整初始索引；随后逐页 PNG→JSONL→manifest。逐页中断最多使 manifest 落后一页，按 `(success,failed,pending)` 的差异仅容许 `(1,0,-1)`、`(0,1,-1)`、`(1,-1,0)`、`(-1,1,0)`。旧 running 缺计数仍保留 warning 兼容，但不宣称该字段已验证。较大差异是审计 issue，不表示 prepare 无法恢复；测试验证重跑可修复汇总。

## 6. 实际测试证据

环境：Mac 本地，Python **3.11.18**、PyMuPDF **1.28.2**、pytest **9.2.0**。使用已有 `.venv`，未增加依赖。

修改前执行：

```text
.venv/bin/python -m pytest -q
95 passed, 5 warnings in 1.42s
exit code: 0
```

修复及新增回归测试后的完整执行：

```text
.venv/bin/python -m pytest -q
195 passed, 5 warnings in 2.72s
exit code: 0
```

新增 **100** 个参数化展开后的测试实例：54 个直接校验、31 个 Runner、15 个审计边界；原有 95 个全部保留并通过，其中两个旧 fixture 按新增契约调整。没有失败或跳过项。

| 测试文件 | 当前实例数 | 覆盖重点 |
| --- | ---: | --- |
| `tests/test_parser_validation.py` | 54 | bbox 格式、越界、NaN/Infinity、置信度、所有输出对象类型、source/layout_type/reading_order、唯一 ID/引用、状态、阶段权限、明确空结果、无框文字 |
| `tests/test_runner.py` | 31 | 正常写入、两种阶段执行顺序、重复/force、换后端替换、保留另一阶段、非法结果与异常、联合部分失败、未渲染/坏 PNG 拒绝、输入变化、上下文隔离、写入失败/中断续跑、重渲染失效、v1.0 兼容及未请求页损坏预检 |
| `tests/test_audit_manifest.py` | 34 | 原汇总字段/状态测试 + 15 项单页允许、多页差异拒绝、计数守恒、旧 pending 缺省、重跑恢复；渲染通过不代表 OCR 通过 |
| `tests/test_pipeline.py` | 29 | 元信息、PNG/JSONL、DPI/页范围/印刷映射、重复跳过、错误与中断恢复、源核验 |
| `tests/test_coordinates.py` | 21 | 第二轮独立 PDF 几何/实际像素真值，旋转和 CropBox 回归 |
| `tests/test_recovery.py` | 12 | 历史坏记录写前拒绝、JSONL 与 manifest 间中断恢复 |
| `tests/test_cli.py` | 14 | 既有命令、输入错误、退出码、批量遗漏目录等 |
| **合计** | **195** | 全部通过 |

已知警告：SWIG 类型 `SwigPyPacked`（2 条）、`SwigPyObject`（2 条）、`swigvarlink`（1 条）缺少 `__module__` 的 DeprecationWarning，与基线相同。解释器退出另输出同类 swigvarlink 警告；没有屏蔽警告来获得通过结果。

### 现有真实报告产物的兼容性检查

仅将现有输出复制到系统临时目录后调用 audit，源 PDF 只读核验，不重新渲染、不运行任何 Adapter。结果：

| 文档 ID | 实读 PDF 页数 | 已处理 PDF 页码 | PNG / JSONL / 来源核验 | OCR / Layout |
| --- | ---: | --- | --- | --- |
| `doc_ad04687d78935bea` | 107 | 1、2、3 | 3 个有效 PNG、3 条 v1.0 记录，audit.ok=true、source_verified=true，issues/warnings/invalid_parser_records 均为空 | 均 not_run |
| `doc_bd54d6eeb4876761` | 128 | 1、2、3 | 3 个有效 PNG、3 条 v1.0 记录，audit.ok=true、source_verified=true，issues/warnings/invalid_parser_records 均为空 | 均 not_run |

开始与检查结束的 `data/` 全部 **16 个文件 SHA-256 清单一致**，真实 PDF 和原处理数据未改变。这验证旧记录读取/审计兼容性，不代表新增模型能力或整份报告质量通过。

## 7. 剩余风险与接手建议

- **真实模型仍未验证。** MiniCPM-OCR/PP-DocLayoutV3 的实际输出字段、坐标反变换、类别映射、显存和准确率均未实测。结构 success 不能证明真实识别正确；empty_reason/provenance 也是后端契约，不是执行证明系统。
- **单 writer 与跨文件边界。** 无并发锁或跨文件事务，推理后写入中断会再次推理。prepare 和 Runner 不可同时写同一目录；不要修改解析中的 PNG。异常信息和路径属于本地产物，不应上传真实数据。
- **替换策略及依赖。** 同阶段只保留一个当前结果集合，不做多模型融合；更换 ID 可能使另一阶段引用失效，会明确失败。缓存没有自动跟踪其他阶段内容变化，依赖发生变化时需 force 或联合重跑。失败阶段旧结果仅供诊断。
- **旧手工结果兼容限制。** 可读、可审计并警告，不能自动补成有来源的 v1.1 成果；必要时另行制定备份和人工迁移流程。默认历史 prepare 空记录迁移已测试。
- **既有后续项。** R5 每页实际渲染器版本、R6 依赖锁定/干净环境重建仍未实现；Ubuntu/RTX 3080 未验证。非默认 UserUnit、越界 CropBox 的明确限制保持不变。

工程基础**适合交给师兄开始真实后端接入**，不应宣称真实模型或生产环境已验收。优先任务最多三个：

1. 实现一个 OCR-only Adapter：记录版本/配置身份，真实调用 MiniCPM-OCR；可靠定位映射 TextBlock，无定位用 PageText；先独立测试输出转换，再调用 Runner。
2. 实现 Layout-only Adapter：PP-DocLayoutV3 类别映射 LayoutRegion，反变换 bbox、稳定 ID、明确关系阶段；少量授权样本人工核对 OCR/布局及 evidence，不默认全量报告。
3. 完成 R5/R6 与干净 Ubuntu CPU 测试，再记录 GPU 推理环境、真实输出和验收证据；按实际需求决定是否增加依赖跟踪或手工迁移工具。

## 8. GitHub 交接与文档

README、architecture、schema、roadmap 已同步：写清 Python 入口、校验/替换/恢复契约、v1.0 边界、模型尚未接入，以及 `94dafcd` 已由用户上传并审阅的事实。历史第一阶段/第二轮报告保留原样。

`.gitignore` 已检查，无需新增规则：原始 PDF、全部处理数据、图片、虚拟环境、模型权重、.env、私人配置和服务器凭据均有忽略规则；`git check-ignore` 的代表路径检查通过。Git 现有 data 跟踪项只有两个 `.gitkeep`。对当前可提交文本进行常见访问令牌、私钥、服务器 IP、显式密钥赋值模式检查，未发现匹配；这不是对未知凭据格式的绝对保证，提交前仍应检查暂存清单。

适合提交现有 Private Repository 供审阅的范围：修改的 models/adapters/audit、两个新增核心模块、合成 Mock 和相关测试、四份更新文档及本报告。没有新增依赖或真实数据。`git diff --check` 通过；未自动 git add/commit/push，HEAD 仍为 `94dafcd`。

建议 Commit Message：`feat: add validated OCR/layout adapter runner and regression coverage`
