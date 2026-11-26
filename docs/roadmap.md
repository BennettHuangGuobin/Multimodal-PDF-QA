# 开发状态与后续验收

## Completed

- 标准 Python 3.11+ src 工程，PyMuPDF 单一运行时依赖。
- 单 PDF/目录元信息检查、可配置 DPI、1-based 页码选择、PNG 顺序渲染。
- 内容 ID、逐页 JSONL、manifest、audit、原子检查点、失败记录与续跑。
- 人工印刷页码映射、旋转/CropBox/矩阵元信息、OCR-first 空区域状态。
- 不加载模型的 DocumentParserAdapter / ParserResult 契约及 Python Runner；运行时校验、分阶段替换、原子写入、错误恢复与结果来源。
- 临时 PDF 自动化测试、合成 PDF 脚本、中文文档、私有数据 Git 忽略规则。
- 2026-11-25：35 项自动化测试通过；两份真实报告读取为 107 / 128 页，各前 3 页在 144 DPI 下渲染与 audit 通过，源文件哈希核验通过。
- 2026-11-26 第二轮：修复 R1–R4，补充 60 个参数化展开后的回归用例，共 95 项通过。覆盖旋转+裁剪坐标、历史索引写前校验、manifest 一致性与恢复语义、无 manifest 的批量候选目录。详见 [iteration_02.md](progress/iteration_02.md)。
- 2026-11-26 第三轮：完成 **R7 最小 Adapter 数据闭环**，新增 LayoutRegion、PageText、ParserProvenance 和 Relation.stage，页面 Schema 1.1 兼容旧未解析 v1.0；严格 running 单页计数滞后判断。新增 100 项回归用例，195 项通过、5 条 SWIG 警告；仅合成 Adapter，未运行真实模型。详见 [iteration_03.md](progress/iteration_03.md)。
- Private GitHub 仓库的第二轮提交 `94dafcd` 已由用户上传并审阅；第三轮本地修改待审查，未自动 commit/push。

## In Progress

- 业务样本验收准备：已完成各前 3 页 smoke test 和 PNG 视觉抽查；后续挑选更复杂图文页面，检查版面、文字清晰度、旋转与裁剪，再决定全量 DPI。当前未全量处理。
- 后端部署方案准备：尚未选定并验证模型版本、运行环境及 RTX 3080 显存预算。

## Planned

近期保留第一阶段审查中的后续项：

- **R5：渲染来源溯源。** 区分每页实际生成版本与本次运行环境，避免全部跳过仍覆盖 manifest.renderer.version。
- **R6：依赖可复现性。** 选择已测试版本约束，在干净环境与 Ubuntu CPU 上重跑基础测试；目前没有锁文件或 Ubuntu 实测结论。
- 特殊 PDF：非默认 UserUnit、CropBox 越界等当前明确不支持，需要独立验证后再扩展；旧受影响页应显式重跑 prepare，未请求历史页不会自动迁移。
- 真实模型的语义质检：R7 工程闭环已完成，但结构审计不能证明 OCR 准确性、布局类别或图文关系正确。旧手工解析数据无执行来源时仅可读并警告，不自动补造可信来源；如需保留，先制定人工迁移流程。

模型与后续业务路线仍为 Planned：

1. **接通首个 OCR 后端。** 在经授权的 GPU 环境独立验证 MiniCPM-OCR，记录版本、缩放、耗时和显存；通过既有 Runner 写回。无可靠 bbox 的输出用 PageText，有可靠定位才用 TextBlock；不把模型依赖加入 Mac 核心依赖。
2. **接通布局后端并验证少量代表页。** PP-DocLayoutV3 的标题、段落、表格、图片等使用 LayoutRegion；反变换到 PNG 像素，使用稳定 ID，验证联合/分开运行和跨阶段引用。建立人工验收样本；没有依据的关系仍为空。
3. **提升复现与交接证据。** 完成 R5/R6，在干净 Ubuntu CPU 环境复跑现有测试，再记录真实模型版本和运行配置。按需求再决定是否增加依赖失效跟踪或旧手工结果迁移，当前不引入数据库/并发框架。

更远期再研究核心 MLLM 提取时尚属性与图文线索，保留 `document_id + pdf_page + region_id` 证据标识；设计受控 Tool Calling 并接入经授权的外部数据库，保存查询条件、返回实体和证据。以上均未实现，不属于第三轮范围。

Mac M4 持续承担工程开发、测试及产物检查；RTX 3080 负责未来需要 GPU 的模型推理实验，模型规模适配需实测。第三轮不进行 SSH、模型下载、真实模型接入或 push；技术审阅采用现有 Private GitHub，不生成源码 ZIP。
