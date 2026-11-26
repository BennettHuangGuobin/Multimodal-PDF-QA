# Iteration 02：坐标正确性与审计可靠性修复

- 日期：2026-11-26（Asia/Shanghai）。
- 本地基线：`main`，提交 `c037f74`；开始时工作区无未提交变更。
- 范围：第一阶段审查报告 R1–R4 的修复、回归测试、CLI 约定和工程文档同步。
- 结论：**本轮修复及本地验收完成，95 项测试通过。** 支持的常规页面几何与恢复路径已验证，特殊 PDF 范围、R5–R7 和 Ubuntu/GPU 验证仍有明确限制。
- 操作边界：没有接入 OCR/MLLM、下载模型、调用收费 API、连接服务器、修改原始 PDF、生成源码 ZIP、提交 commit 或 push。

## 1. 本轮具体问题与修复前后行为

| 问题 / 优先级 | 修复前 | 修复后 |
| --- | --- | --- |
| R1 / P1：原始 PDF 坐标转换 | 旋转页面直接保存 `page.transformation_matrix`，非零 CropBox 偏移丢失；原始 bbox 映射错误 | 从 MediaBox/CropBox 独立计算平移和 y 翻转，原有未旋转坐标 → PNG 算法不变；请求页旧矩阵不匹配时重建 |
| R4 / P1：历史索引损坏先写后报错 | 未请求页缺 `render_status` 可在 manifest 已改写后抛 KeyError | 对全部历史记录预检，出现缺字段、非法类型/状态/路径等问题时，在写入前给出含记录序号及 PDF 页码的错误；旧文件字节保持不变 |
| R2 / P1：manifest 与 audit 不一致 | 汇总数或 schema 被篡改仍可 audit 通过，状态含义不充分 | 校验 schema、计划页数、索引状态计数和 complete 声明；对 running 的合理检查点滞后和已恢复的 incomplete 给出 warning；新增独立渲染完整性字段 |
| R3 / P2：批量 audit 漏目录 | 仅枚举 `*/manifest.json`，缺 manifest 的产物目录静默消失 | 通过 manifest/JSONL/pages 产物标记发现候选，缺 manifest 时明确输出失败结果和非零退出码；不因目录叫 doc_ 就把普通目录误判为损坏文档 |

第一阶段报告保留为历史证据，不回写当时的检查结论。当前状态以本报告、README、Schema 与 Roadmap 为准。

## 2. 修改的源码和关键函数

| 文件 | 关键函数/类 | 本轮变更 |
| --- | --- | --- |
| `src/multimodal_pdf_qa/pdf.py` | `page_geometry` | 根据有效页面框构造原始 PDF → 未旋转页面矩阵；不调用 set_rotation，不修改文档内存状态或源文件；加入不支持几何的明确诊断 |
| 同上 | `PageRangeError`、`select_pages` | 页范围错误使用独立 ValueError 子类，CLI 可以区分用户参数错误与损坏产物 |
| `src/multimodal_pdf_qa/prepare.py` | `prepare_pdf` | 写前检查全部历史记录和 manifest 基本结构；复用共享校验；重算请求页几何以识别旧矩阵；各检查点保存索引状态计数；最终 audit 与实际 manifest 状态一致 |
| `src/multimodal_pdf_qa/audit.py` | `record_errors` | 补充物理页码、整数图像尺寸、成功页图像哈希格式等必要结构检查 |
| 同上 | `manifest_scope` | prepare/audit 共用文档身份和计划页范围校验，避免对无法解释的 manifest 继续读写 |
| 同上 | `render_counts`、`manifest_diagnostics` | 从索引统计 success/failed/not_run；按 manifest 状态判断计数、版本、完成声明的不一致是 issue 还是恢复提示 |
| 同上 | `audit_document` | 新增 `render_complete`、`manifest_status`、`index_render_counts`，补充具体计数诊断，保持 OCR/布局 not_run 为正常待办 |
| `src/multimodal_pdf_qa/cli.py` | `discover_document_dirs`、`main` | 发现缺 manifest 的产物目录；文档异常同时输出 stderr 和失败 JSON；非法页范围退出 2；批量错误保留最高的 1/2 退出码 |

运行时依赖和项目结构未改变，没有引入数据库、持久化框架或 Adapter runner。`models.py`、`storage.py`、`adapters.py` 没有在本轮修改。

## 3. 坐标修复的独立复现及支持范围

### 修复前复现

修改前先生成并保存合成 PDF，直接向 PDF 内容流写入矩形绘制指令，不使用待测矩阵来构造真值。240×320 point 页面，CropBox 为 `[20,30,220,290]`，原始 PDF bbox `[40,140,120,230]`：

| 旋转 | 独立预期的未旋转 PyMuPDF bbox | 基线代码实际输出 |
| --- | --- | --- |
| 0° | `[20,60,100,150]` | `[20,60,100,150]` |
| 90° | `[20,60,100,150]` | `[40,30,120,120]` |
| 180° | `[20,60,100,150]` | `[40,30,120,120]` |
| 270° | `[20,60,100,150]` | `[40,30,120,120]` |

### 修复方式

对当前支持的普通 `UserUnit=1` 页面，设 PyMuPDF 的 MediaBox 为 M，CropBox 为 C：

```text
raw_crop = [C.x0, M.y1 - C.y1, C.x1, M.y1 - C.y0]
pdf_to_mupdf_matrix = [1, 0, 0, -1, -raw_crop.x0, raw_crop.y1]
```

第一段负责将原始 PDF y 向上的页面坐标转换成以裁剪后可见区域左上角为原点、y 向下的未旋转坐标。第二段仍采用原有 `rotation_matrix × Scale(dpi/72) × Translate(-pixmap_origin)` 得到显示方向的 PNG 像素坐标。PDF 页面物理页码仍为 1-based，printed_page 仍独立且未知为 null。

新增测试的原始矩形由 PDF 内容流定义，未旋转预期由独立几何计算，旋转预期由显式 0/90/180/270°公式计算，并遍历实际 PNG 的红色像素取得真实区域边界。因此验证不是只检查同一矩阵的正逆变换。测试还确认读取几何后 `doc.is_dirty=false`、页面旋转不变、源文件字节不变。

第一阶段旧索引的处理：只对本次请求页重算几何，旧矩阵不同会触发重渲染和下游状态重置。没有自动全量迁移未请求页。曾有旋转+裁剪的历史页面应显式重跑相应页码；audit 本身尚未独立逐页重算源几何，不能代替这一步迁移。

### 明确不支持 / 未验证

- 对非默认 UserUnit 导致的页面尺度变化，以及超出 MediaBox 的 CropBox，当前返回 `Unsupported page geometry`。inspect 失败；prepare 记录该页 failed 并继续其他页，不生成伪正确坐标。
- 原因是本轮本地实验还观察到非默认 UserUnit 下底层 rotation_matrix 的平移未包含对应尺度；本轮不扩大修复范围去替换已正确的普通页面 PNG 矩阵。
- 尺度检查有有限浮点容差。畸形 PDF 的底层自动修复、所有 UserUnit/继承页面框组合未全面验证。
- 页面矩阵输入是 PDF 页面 user space，不是图片/Form XObject 的局部坐标；后者必须先应用自身 CTM。
- 这些限制已同步到 `docs/schema.md`、README 和 Roadmap，没有把特殊页面支持写成已完成。

## 4. 写前校验、审计状态与 CLI 语义

### 历史记录保护

prepare 先检查 manifest 的 schema、身份、页范围、状态和 created_at 基本类型，然后检查全部已有 JSONL 记录的必需字段、类型/状态、页码唯一性及历史范围、相对图像路径和 JSON 可序列化性。这里的校验不要求 PNG 已存在：结构合法的缺失/损坏图片、failed/not_run 页面仍然能够恢复。

无法安全解释的历史记录会给出类似 `record 2, PDF page 2: missing fields: render_status; invalid render_status` 的诊断。在这些预检拒绝路径中，不创建新检查点、不改写旧 manifest/JSONL，也不触碰旧 PNG/audit。回归测试按目录内所有文件的字节快照比较验证。

### manifest 与 audit

- `expected_page_count` 对照计划集合核验；JSONL 行数/有效记录数也必须与计划一致，缺失/重复/意外页分别列出。
- manifest 的成功/失败/待处理计数表示**索引 render_status**，不是当前图片可用数。audit 用 `index_render_counts` 核对它们，顶层成功数则表示实际可用 PNG。
- 新 manifest 各检查点都有 `pending_render_count`；旧 v1.0 manifest 没有此新增字段仍兼容。旧 running 文件省略成功/失败计数时报告恢复 warning。
- `complete` 必须与当前 JSONL/PNG 完整性相符，否则是 issue。schema 不支持、状态未知、非法计数以及最终状态中的计数矛盾都能得到具体诊断。
- `running` 的状态本身不是失败；JSONL 原子替换后、manifest 替换前中断造成的合理计数滞后是 warning。缺页、失败或损坏 PNG 仍会导致完整性失败。
- `incomplete` 若产物已全部恢复且计数一致，允许通过，但提示重新 prepare 收尾；不能据此隐藏计数错误。
- `render_complete=true` 只代表索引及渲染产物完整。`ok` 还考虑 manifest/源文件问题和已显式标为 failed 的 OCR/布局阶段。not_run 正常；两者都不证明 OCR 或语义质量。
- `last_run` 是历史执行指标，无法仅由索引重建 rendered/skipped 的区别，当前不是 audit 的独立产物计数来源。

原子替换仍然是逐文件操作，没有跨文件事务或多进程锁。本轮新增了在 JSONL 与 manifest 两次写入之间注入 KeyboardInterrupt 的测试，并验证已有成功页跳过、剩余页继续、最终状态收尾。

### CLI 退出码

| 码 | 当前约定 |
| --- | --- |
| 0 | 成功或 audit 通过；允许存在恢复/源文件不可用 warning，调用方仍需检查详情 |
| 1 | 坏 PDF、损坏数据、缺 manifest、unsupported geometry、渲染失败或 audit 不通过；坏印刷页码映射属于逐文档处理错误 |
| 2 | 命令参数、配置或输入选择错误，包括非法/越界页码、DPI/TOML、空输入选择、单文档选项用于多文档 |
| 130 | 用户中断，成功检查点保留 |

批量 audit 根据 manifest.json、pages.jsonl 或 pages/ 标记发现当前层候选。缺 manifest 的候选明确返回 `{ok:false, document_dir, error}` 并输出 stderr；普通无关目录忽略。多个文件中错误码 1/2 混合时最终为 2；批次中其他合法文档仍可能完成，非零不等于全批无写入。

## 5. 新增与调整的回归测试

新增用例按 pytest 参数化展开计数：

| 测试文件 | 新增数 | 关键覆盖 |
| --- | ---: | --- |
| `tests/test_coordinates.py` | 21 | 四种旋转 × 有/无裁剪 × 两个 MediaBox 原点共 16 种独立真值；旧矩阵重建 1 项；UserUnit/越界 CropBox 的显式拒绝 4 项 |
| `tests/test_recovery.py` | 12 | 未请求页 8 类结构损坏、3 类 manifest 基本结构错误均写前拒绝；一次跨 JSONL/manifest 检查点中断恢复 |
| `tests/test_audit_manifest.py` | 19 | 篡改/删除 manifest 字段、running/incomplete 恢复语义、旧 running 缺计数、complete 与坏图片矛盾、渲染完成与 OCR 失败分离 |
| `tests/test_cli.py` | 8 | 两类无 manifest 候选与无关目录、4 类坏配置、非法/越界页码返回 2 且不创建输出 |
| 合计 | **60** | R1–R4 与相关 CLI 行为 |

保留既有 35 项测试，并调整其中 `test_transforms_match_visible_colored_region` 的原始 PDF bbox 真值：不再用同一底层矩阵的逆构造原始框，而是使用 fixture 已知页面高度和绘制位置推导的独立原始 PDF 坐标。旧的 m2 正逆检查只是辅助验证，新增像素边界测试提供独立依据。

## 6. 实际执行结果

基线与最终执行命令相同：

```bash
.venv/bin/python -m pytest -q
```

| 阶段 | 实际摘要 | 退出码 |
| --- | --- | --- |
| 修改前基线 | `35 passed, 5 warnings in 0.57s` | 0 |
| 修复与回归完成后 | `95 passed, 5 warnings in 1.48s` | 0 |

最终失败 0、错误 0、跳过 0。正常页面渲染、重复跳过、错误重试、中断恢复、范围并集、DPI 更换、印刷页码映射和两种 CLI 入口均包含在通过的测试中。

环境：Python 3.11.18、PyMuPDF 1.28.2、pytest 9.2.0，Mac 本地 CPU。警告为 SWIG 内建类型缺 `__module__` 的 DeprecationWarning：SwigPyPacked 2 条、SwigPyObject 2 条、swigvarlink 1 条；进程退出另输出一次 swigvarlink 提示，不计入 pytest 摘要的 5 条。未因警告屏蔽测试，也没有把未运行项目记为通过。

### 已有真实报告的兼容性复核

本轮没有重新渲染真实报告。将各自已有的少量产物复制到临时目录，使用新 audit 读取原 PDF 并核验：

| 报告 | 实际总页数 | 已处理页 | JSONL/PNG 成功数 | 结果 |
| --- | ---: | --- | ---: | --- |
| 26春夏抖音电商时尚消费趋势报告 | 107 | 1–3 | 3 / 3 | ok、render_complete、source_verified 均 true；issues/warnings 均空 |
| 26SS春夏淘宝服饰白皮书 | 128 | 1–3 | 3 / 3 | ok、render_complete、source_verified 均 true；issues/warnings 均空 |

这同时验证了旧 v1.0 complete manifest 缺少新增 pending 计数字段的兼容性。真实 data/ 下全部 16 个文件在本轮前后的 SHA-256 一致，包括原 PDF、原 JSONL、原 manifest/audit、PNG 和占位文件。原 audit 没有被副本审计覆盖。

`git diff --check` 通过。未进行全报告性能/视觉验收、覆盖率统计、Ubuntu/GPU 测试或模型推理。

## 7. 文档与 GitHub 交接

更新文档：

- `README.md`：第二轮已完成范围、CLI 退出码、写前校验、候选目录、审计字段、几何限制和 Private GitHub 当前工作方式。
- `docs/architecture.md`：预检与写入顺序、可恢复检查点语义、批量目录发现、坐标修复依据。
- `docs/schema.md`：三段坐标关系及公式、特殊页面限制、旧索引处理、计数与状态/审计语义。
- `docs/roadmap.md`：R1–R4 修复记录；R5–R7 继续保留为后续工作。
- 本文件：`docs/progress/iteration_02.md`。

`.gitignore` 原规则满足本轮要求，因此未无谓修改。用实际 Git 执行 23 个忽略/允许路径检查，覆盖真实输入、处理图片/JSONL、环境、缓存、模型与凭据目录以及应提交的代码/示例/报告路径；全部符合预期。`git ls-files --cached --ignored --exclude-standard` 无结果；真实数据仅跟踪两个 `.gitkeep`，没有输入 PDF、图像、环境或模型被跟踪。

对当前源码、示例与文档进行敏感内容扫描和人工核对，唯一凭据赋值命中是加密 PDF 单测的固定合成口令，不是真实服务器凭据。没有发现需要放入 Git 的真实 API Key、服务器地址、密码、访问令牌或私钥。本报告不包含真实报告正文、图像、绝对源路径或文件指纹。

建议提交本轮 4 个核心源码文件、2 个调整后的已有测试文件、3 个新增测试文件、4 个更新文档及本迭代报告。没有新增依赖、改动原始数据、建立新仓库或生成 ZIP。提交建议：

```text
fix: correct PDF transforms and harden preparation and audit consistency
```

本轮未自动 commit 或 push；等用户确认后再提交上传。后续技术审阅通过现有 Private Repository 进行。

## 8. 剩余风险与下一步

1. **R5（未修复）**：每页实际 renderer 版本与本次运行环境没有完全区分，全部跳过仍可能改写 manifest 中的 renderer.version。
2. **R6（未修复）**：依赖没有锁定，其他版本及 Ubuntu 环境未验证；不可直接把 Mac `.venv` 复制到服务器使用。
3. **R7（未实现）**：Adapter 仍为 Protocol/dataclass 接口；需要补齐 bbox、置信度、ID/关系引用校验及结果合并/持久化 runner，现有 audit 不等同于模型结果质量检查。
4. 非默认 UserUnit、越界 CropBox、更多畸形/特殊 PDF 坐标组合仍受限制。旧索引没有全量自动迁移，受影响历史页需要显式请求重跑。
5. 仍为单写入进程，未验证磁盘耗尽、真实掉电、SIGKILL 等系统级故障；跨文件中断通过注入 KeyboardInterrupt 的测试验证，不等于覆盖所有存储故障。

**交给师兄的判断：适合作为下一阶段 OCR/布局接入工作的基础。** 可以开始确定后端输入输出与部署方案，但在正式把模型输出写入统一索引前，应先落实 R7 的最小数据契约闭环，并在 Ubuntu CPU 环境复现当前基础测试，再单独验证 RTX 3080 的模型兼容性和显存。尚不能称为模型接入或 GPU 验收完成。
