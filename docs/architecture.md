# 本地 PDF 准备与 Adapter 数据闭环

当前模块均运行在 Mac CPU，本地文件系统是唯一持久化介质。

```text
CLI (argparse + tomllib)
 ├─ pdf: discover / inspect / page geometry / page selection
 ├─ prepare: sequential get_pixmap → atomic PNG → JSONL checkpoint
 └─ audit: source verification + JSONL + PNG + parser data validation

Python API: runner.run_parser(document_dir, adapter, pages=..., force=False)
 └─ preflight → read PNG → adapter.parse → validate + merge → atomic JSONL → audit

models: dataclasses 共用结构
storage: SHA-256、JSON/JSONL 原子替换、安全相对路径
adapters: DocumentParserAdapter Protocol + ParserResult（尚无真实模型后端）
validation: bbox / types / IDs / references / stage ownership / provenance
```

## 状态与恢复

每页独立记录三个阶段状态，`not_run → success | failed`。首次计划页以 `not_run` 保存；渲染失败保留真实错误，并继续后续页。prepare 本身不运行 OCR/布局；第三轮 Runner 仅调用调用者显式提供的 Adapter。当前只有 tests 下的合成实现，无真实模型推理。

在任何已有产物写入前，prepare 校验 manifest 身份、版本与计划范围，并检查**全部历史记录**的必需字段、状态、基本类型、路径与 JSON 可序列化性。未请求页缺字段也会明确报错，拒绝时不改写 manifest/JSONL/PNG。Parser Runner 在此基础上还对全部历史页面解码并验证解析字段、区域引用和来源；audit 复用同一深层结构校验。该校验不评判模型识别的语义准确性。

结构合法的未完成/失败页，以及缺失/损坏的图片，仍可通过指定页码重跑恢复。对请求页检查状态、DPI、当前几何元信息、PNG 解码、像素尺寸和内容哈希，符合条件才跳过。旧坐标矩阵不匹配、变更 DPI 或强制重渲染会重置该页下游结果。范围取并集，未请求页面保持历史结果。未请求页图片损坏由 audit 指出，不越过用户范围重渲染；未请求页索引结构损坏则在写入前拒绝。

每页渲染时仅持有当前页面的 pixmap；Python 内存还包括小型元数据索引与 MuPDF 自身的文档缓存，不同时收集全部页图像。索引按页原子重写，针对约 100–130 页报告足够简单；不是为百万页任务设计。很高 DPI 或超大单页仍会显著增加内存。

PNG、JSONL、manifest 各自原子写入，不提供跨文件事务。意外中断可留下一张未索引 PNG 或未完成状态，下次运行会重渲染/恢复。JSONL 先于每页 manifest 检查点落盘：running 的计数只有符合**一页 pending→success/failed 或 success↔failed，且总页数守恒**的差异才为 warning；较大差异或不可能的方向为 issue。实际缺页仍报缺页。manifest.complete 与缺失/损坏产物矛盾时是错误；incomplete 的产物已恢复则允许通过并提示收尾。具体向量及旧缺字段兼容见 [schema.md](schema.md)。当前单进程顺序使用，不支持两个 writer 同时写同一文档目录。

audit 对源 PDF、JSONL 与 PNG 只读，会替换 audit.json。批量 CLI 将存在 `manifest.json`、`pages.jsonl` 或 `pages/` 的直接子目录视为文档候选；缺 manifest 也输出失败结果、stderr 诊断并返回非零。只有普通说明文件或仅有 `doc_` 名称的空目录不会被当成损坏文档。

## 标识与来源

`doc_<SHA256前16位>` 标识 PDF 字节内容，完整哈希写入 manifest 并用于核验。重命名不会改变 ID；修改 PDF 会创建新 ID。短哈希冲突时完整哈希核验阻止覆盖。元信息与路径也属于本地产物，默认不提交。

`printed_page` 独立于物理页码。人工映射有证据后填写；后续视觉页码检测应保留识别来源/置信度并接受人工纠正。当前不推测印刷页码。

## Adapter Runner 流程

入口为 `runner.run_parser(document_dir, adapter, *, pages=None, force=False)`。没有新 CLI 命令、任务队列或数据库；返回 document_id、parsed_pages、failed_pages、skipped_pages（含原因）和最终 audit。pages=None 表示当前 manifest 已计划页，并非整个源 PDF；显式页码仍从 1 开始且必须已 prepare。

1. 检查 Adapter name/stages、manifest 身份与范围，以及**全部**历史 JSONL 的基本结构、区域、引用、来源；错误在调用和写入前拒绝。v1.0 未解析页允许渐进升级，选中的旧手工解析页缺来源则拒绝自动升级。
2. 对请求页，render_status 非 success 时跳过并说明原因。成功页解码实际 PNG，核对像素尺寸、DPI 比例及 SHA-256；输入损坏抛出 ValueError，要求 prepare 恢复。不会给未渲染页虚构模型失败。
3. 同一 name、图像哈希、所负责阶段均 success 且非 force 时跳过，否则传入图像 Path 和 PageRecord 深拷贝调用 parse。name 应包含模型版本与影响结果的配置；更名触发重新解析。
4. `validate_parser_result` 构造独立候选，只替换成功且属于该 Adapter 的阶段字段，检查完整候选的 bbox、置信度、ID、跨阶段引用、类型、状态和来源。验证函数不做文件写入，不修改输入。
5. 明确的阶段失败保留该阶段旧有效内容及来源，并记录错误；后端异常或非法结果使本次声明阶段都 failed。另一阶段不受影响。模型结果验证后再次核验 PNG，避免解析期间输入变化造成坐标绑定错误。
6. 使用现有 `storage.write_jsonl → atomic_text` 逐页保存完整索引。存储异常和 KeyboardInterrupt 在模型异常处理之外向上抛出，不能伪装成模型失败；旧检查点可读。完成后 audit 刷新报告，manifest 不被模型流程改写，其 complete 只表示渲染状态。

JSONL 写入失败前模型可能已调用；重试会再次调用该页，不能保证 exactly-once 推理。只依赖同目录原子替换及单 writer，不提供断电持久化事务或并发锁。audit 写入失败时 JSONL 可能已经完成，重新 audit 即可，不能把整个调用当成事务回滚。有效检查点会在重试时跳过；已有失败阶段则重新执行。

模型失败后保留的数组是诊断历史，使用方必须先检查阶段状态。跨阶段引用保留意味着失败布局可能仍被 OCR 数据引用；下游使用该关系前应要求相关两阶段均有效。结果缓存不是依赖跟踪系统，若后端使用另一阶段内容而该内容有变化，应显式 force 重跑；尚未实现依赖变化自动失效。

## 师兄接入后端

后端实现 `DocumentParserAdapter` 的三个成员即可，不需要继承类，也不在核心包 import 时加载深度学习框架：

| 成员 | 要求 |
| --- | --- |
| `name: str` | 非空、稳定的模型/版本/配置身份；配置变更时更改 name 或 force |
| `stages: tuple[str, ...]` | `("ocr",)`、`("layout",)` 或 `("ocr", "layout")`，不重复 |
| `parse(image_path: Path, page: PageRecord) -> ParserResult` | 只读 PNG；真实调用后端，返回 dataclass 对象；运行异常可直接抛出 |

OCR-only 的输出必须 layout_status=not_run；它表示“不更新布局”，不会清掉已有页面布局 success。Layout-only 同理不更新 OCR。TextBlock/PageText 属于 OCR，ImageRegion/LayoutRegion 属于 Layout，Relation.stage 说明关系的生成阶段。成功采用整阶段替换而非 append；换不同模型也不自动融合，保持一页一个当前结果集合。

接手建议按以下顺序实施：

1. 在独立推理环境验证具体模型版本、硬件适配与最小输入，模型依赖放在后端层，Mac 核心依赖维持轻量。当前尚无真实 MiniCPM-OCR / PP-DocLayoutV3 类。
2. MiniCPM-OCR 有可信文字 bbox 时映射 TextBlock，无可靠 bbox 时返回 PageText；不能制造整页框。PP-DocLayoutV3 的 title/paragraph/table/figure 等输出映射 LayoutRegion，实际图像证据才考虑 ImageRegion。所有输出转成 Python 内置类型。
3. 后端负责将缩放、letterbox、裁剪后的 bbox 反变换至输入 PNG 像素空间，不自动截断越界框掩盖错误。保留真实来源、可选置信度、阅读顺序；未知关系为空，不能按邻近位置假装模型已经判定。
4. 为后端添加独立转换测试，再用 run_parser 在少量已授权代表页上验证，查看 audit 的结构诊断，人工核对文本、布局及坐标。仅空数组不代表成功；确认真实执行后未发现目标时才填明确的 *_empty_reason。

可参考 `tests/mock_adapters.py` 的协议形状与 `tests/test_runner.py` 的调用，但所有 Mock 内容均为人工指定的合成数据，只写入 pytest 临时目录，不能用于真实报告成果。模型类别映射、推理准确率、GPU 内存和耗时仍需真实实验。数据字段、替换语义和 v1.0 限制见 [schema.md](schema.md)。

## 坐标依据

PyMuPDF 使用未旋转的页面坐标；显示渲染需要旋转矩阵和 DPI 缩放。实现依据 [PyMuPDF Page API](https://pymupdf.readthedocs.io/en/latest/page.html) 与 [图像渲染说明](https://pymupdf.readthedocs.io/en/latest/recipes-images.html)，同时保存 MediaBox、CropBox 和变换矩阵。具体定义见 [schema.md](schema.md)。

第二轮针对实际 PyMuPDF 1.28.2 的旋转+裁剪偏移问题，改为从页面框计算原始 PDF → 未旋转坐标矩阵，没有修改页面旋转或源文件。未旋转 → PNG 的算法保持原样。非默认 UserUnit/裁剪越界目前明确拒绝；独立内容流真值及实际像素边界测试覆盖已支持组合。
