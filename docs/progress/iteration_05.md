# 第五轮：MiniCPM-OCR / Ollama 本地真实实验

日期：2026-11-27（Asia/Shanghai）。起始 HEAD 为 `3c51559`，工作区干净。本轮只在 Mac 本地开发和实验，没有自动 commit/push，没有生成 ZIP。

## 1. 实际完成范围

| 状态 | 内容 |
| --- | --- |
| Completed：本地集成 | 标准库 OllamaOCRAdapter、模型/配置身份、PageText 写回、请求/超时/空结果分类、显式页码脚本、私有实验日志、Mock HTTP 测试 |
| Completed：真实推理验证 | 已安装的 `openbmb/minicpm-ocr1.0:f16` 经 Ollama 0.40.18 对 107 页报告第 1–3 页成功识别并通过结构审计；重复命令跳过三页 |
| In Progress | 本次原文的用户人工质量确认；没有计算或宣称 OCR 准确率 |
| Planned | PP-DocLayoutV3、完整图文 MLLM/线索提取、Tool Calling、外部数据库；Ubuntu/RTX 3080 重验 |

本轮没有下载模型、安装重型 Python 依赖、连接 PolyU/SSH、修改原 PDF、进行全报告 OCR，或实现 Agent/RAG/Web UI。使用的是用户已经安装并运行过的本地模型。

## 2. 源码和数据契约

| 文件 | 入口及实现 |
| --- | --- |
| `src/multimodal_pdf_qa/backends/__init__.py` | 独立后端包，核心导入不会加载模型框架 |
| `src/multimodal_pdf_qa/backends/ollama_ocr.py` | OllamaOCRAdapter；构造时只读 version/tags，parse 发送单页 PNG 到 /api/generate，返回 OCR-only ParserResult/PageText；attempts 保留私有响应和指标 |
| `scripts/run_ollama_ocr.py` | 单个已准备文档目录 + 必填 --pages；调用既有 Runner，生成私有运行日志，stdout 不输出全文；退出码 0 成功、1 运行失败、2 参数错误、130 中断 |
| `tests/test_ollama_ocr.py` | Mock HTTP 回归，测试数据均明确为 synthetic，不调用真实 Ollama |
| `.gitignore` | 在已有 data/环境/权重/凭据规则之外补充 *.gguf 和 .ollama/ |

没有修改 ParserResult、PageRecord、Runner、validation 或 audit 的数据契约和实现。PageText.text 保留原始 response 字符串，包括换行，不制造文字框、图像区域或关系；stages=("ocr",)，Layout 状态与有效结果由既有 Runner 保留。页面 Schema 仍为 1.1，manifest/audit 仍为 1.0。

### 请求、身份与状态

使用 urllib.request/json/base64/time 等标准库。地址只允许 HTTP loopback，禁用环境代理和重定向；没有公网服务或收费 API 路径。模型不存在时不 pull，构造失败，未调用生成 API。

Adapter.name 绑定模型精确标签、/api/tags 的完整 digest、服务版本及配置指纹；完整配置另保存在本地运行日志。指纹包括 prompt、options、keep_alive、empty_marker、timeout、stream/raw、后端版本和端点。timeout 统一为浮点秒，避免 Python 的 180 与 CLI 的 180.0 产生不同缓存身份。一次运行期间不可更新模型标签；这些是启动时元信息，不是每个 token 的独立溯源证明。

| 情况 | 处理 |
| --- | --- |
| done=true，正常完成且返回非空文字 | ParserResult.ocr_status=success，PageText 原样保存；再经 Runner 校验/原子写回 |
| 普通空字符串/空白 | 失败，不能把空响应认定为空页 |
| 显式 empty_marker | 仅 Python 调用明确配置、prompt 中约定、完整响应精确匹配时允许 empty_reason；仍是模型断言，非人工确认。本次真实实验未启用此功能 |
| 连接失败/HTTP 错误 | OllamaRequestError；不回显可能包含私有内容的 HTTP body |
| 直接或 URLError 包装的超时 | OllamaTimeoutError；不自动重试，不宣称服务端已取消 |
| 非法 JSON/非有限数、错误模型名、未完成/截断、非法时长 | OllamaResponseError；不接受部分新结果为成功 |
| 重试/失败 | 沿用 Runner 的阶段替换、旧有效内容保护和原子检查点；模型完成但写入失败可能需要再次调用 |

脚本必须位于本 checkout，并只向其被忽略的 data/ 文档目录写入私有日志。日志不是 JSONL 的事务组成部分：页面已提交但日志失败、进程强杀等情形，需检查实际索引。adapter_outcome 表示 HTTP 输出验收，persisted_ocr_status 单独记录最终页面状态，避免把“模型返回文字”直接等同于“落盘成功”。

API 约定参照 [Ollama Generate](https://docs.ollama.com/api/generate) 与 [本地模型清单接口](https://docs.ollama.com/api/tags)；默认 OCR 提示词参照 [OpenBMB 模型页面](https://ollama.com/openbmb/minicpm-ocr1.0:f16)。本报告只使用实际本地返回值，不引用公开榜单作为本项目准确率。

## 3. 真实实验设置与隔离

设备信息由用户提供：MacBook Air M4，16GB 统一内存。运行环境沿用 Python 3.11.18 / PyMuPDF 1.28.2 / pytest 9.2.0；未新增依赖。本轮未测量峰值内存、Metal/GPU 占用、温度或功耗。

本机 API 实读：

```text
Ollama: 0.40.18
Model: openbmb/minicpm-ocr1.0:f16
Digest: 45e9630cdab157f63ca27b7ae26694bbc5edfab3c63aa7b6af4d99abc24705d3
Quantization (tags): F16
Prompt: Text Recognition:
Options: temperature=0, num_ctx=16384, num_predict=8192
keep_alive: 5m
timeout: 180 seconds (socket)
stream: false; raw: false; empty_marker: null
```

最初沙箱内本机 HTTP 被拒绝；随后获准仅访问 loopback 服务，先查询元信息，完整 pytest 通过后才运行真实 OCR。没有连接外部推理服务。

输入是《26春夏抖音电商时尚消费趋势报告》（107 页）的已有第 1–3 页 PNG，均为 **144 DPI、1225×1650 像素**。先将 `data/processed/doc_ad04687d78935bea` 完整复制到新目录：

```text
data/experiments/iteration_05_ollama_f16/doc_ad04687d78935bea/
```

复制前确认目标不存在，复制后核对每个文件哈希。没有重新渲染，也没有改写原始 PDF 或原 processed 目录。真实调用：

```bash
.venv/bin/python scripts/run_ollama_ocr.py data/experiments/iteration_05_ollama_f16/doc_ad04687d78935bea --pages 1-3 --timeout 180
```

脚本实际顺序请求三页，不并行。首次 API 实验日志（仅本地，不在 Git）：

```text
data/experiments/iteration_05_ollama_f16/doc_ad04687d78935bea/ollama_runs/20261127T083603Z_d4ddc63f.json
```

识别全文同时存在该目录的 pages.jsonl.page_text 和日志 response_text；报告只列汇总指标，不复制识别原文或图片。

## 4. 实际性能结果

下表每页均使用上述相同 F16 标签/完整 digest。字符数是实际 response 的 Python len，含换行，不是 token 数；正文没有做 strip/改写后再计数。

| PDF 页码 | OCR 写回 | 字符数 | 客户端单页耗时（秒） | API total（秒） | API load（秒） | API prompt_eval（秒） | API eval（秒） | 结构化 bbox |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| 1 | success | 35 | 9.247531 | 9.227923 | 2.359973 | 6.316814 | 0.382546 | 无 |
| 2 | success | 991 | 16.971743 | 16.959480 | 0.030443 | 6.351141 | 10.475790 | 无 |
| 3 | success | 943 | 16.847877 | 16.838977 | 0.016177 | 6.377389 | 10.363698 | 无 |

API 原始 duration 单位为纳秒，上表除以 1e9 并四舍五入；完整原始值如下，便于复核：

| PDF 页码 | total_duration | load_duration | prompt_eval_duration | eval_duration | prompt_eval_count | eval_count | prompt_eval_cached_count |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 1 | 9227922708 | 2359972792 | 6316814000 | 382546000 | 2700 | 22 | 0 |
| 2 | 16959479875 | 30442583 | 6351141000 | 10475790000 | 2700 | 592 | 0 |
| 3 | 16838976875 | 16176584 | 6377389000 | 10363698000 | 2700 | 590 | 0 |

三页均 done_reason=stop、adapter_outcome=success、persisted_ocr_status=success。整次脚本计时 **43.382082666 秒**，包括模型元信息查询、Runner 的图像/索引核验、三页处理与 audit，不包括最后运行日志自身落盘。单页计时包含编码/请求/响应处理；各 API 时长不要求严格相加等于 total。

本次 API 没有结构化 bbox 字段。对原文做非内容披露的检查，三页均未发现 bbox/bounding-box 关键词或四数字坐标数组；这不是定位能力的完整评测。没有提取或构造任何框，text_blocks/image_regions/layout_regions/relations 均为空，Layout 仍 not_run。

审计实读：ok=true、render_complete=true、source_verified=true，issues/warnings/invalid_parser_records 均为空；ocr_not_run_pages=[]，layout_not_run_pages=[1,2,3]。

相同命令第二次执行：**三页全部 already_parsed，attempts=[]、parsed_pages=[]**，整次约 0.173917 秒，只查询服务元信息并检查/复用已有结果，没有新增模型生成。跳过日志为 `ollama_runs/20261127T084338Z_1a61791c.json`，不能将此耗时当作 OCR 性能。

**人工质量评价仍待用户确认。** 用户此前报告 CLI 的第 2 页约 19.30 秒、第 3 页约 16.91 秒及初步观感较好，属于先前用户观察，不是本轮脚本测量/准确率证据。提示词、参数、加载/缓存等条件未完全对齐，不能从单次差异宣称性能提升。本轮没有计算准确率、遗漏率或对整份报告作质量保证。

## 5. 测试证据

| 执行时机 | 命令 | 实际结果 |
| --- | --- | --- |
| 修改前基线 | `.venv/bin/python -m pytest -q` | **222 passed, 5 warnings in 3.26s**，退出码 0 |
| 真实推理之前 | 同上 | **264 passed, 5 warnings in 3.66s**，退出码 0 |
| 实验后最终代码 | 同上 | **265 passed, 5 warnings in 3.83s**，退出码 0 |

最终新增 **43** 项参数化展开后的 Mock HTTP 用例，保留全部 222 项原有测试。包括：

- 非空原文保存、PNG Base64 请求、模型/版本/配置身份、API 指标及无 bbox/不改 Layout。
- 空白失败、显式空标记成功、缺失可选指标与非法指标。
- 超时（直接及 URLError 包装）、连接拒绝/重置、HTTP 错误、JSON/编码/类型错误、错误模型、未完成/截断。
- 失败保留旧有效结果并可重试、重复执行跳过 HTTP、版本/digest/参数变化触发新识别、整数/浮点 timeout 身份一致。
- 配置与 loopback 限制、模型不存在不下载、重定向拒绝、中断保留索引。
- 脚本显式页码、原文不进入 stdout、私有日志、服务不可用时保存设置失败但不改页面索引。最后一个测试在真实实验后补充，因此最终比实验前多一项。

警告与基线相同：SwigPyPacked 2 条、SwigPyObject 2 条、swigvarlink 1 条缺少 `__module__` 的弃用警告，解释器退出另输出同类 swigvarlink 警告；没有隐藏警告。pytest 不访问真实 Ollama，本报告将 Mock 证据与真实 API 实验分开。

## 6. 交接与剩余限制

README、handoff、roadmap 已更新，architecture 中过时的“无真实后端/全流程 CPU”描述同步修正。复制实验目录、脚本和 Python 调用示例见 [handoff](../handoff.md#ollama-真实后端已实现)。历史报告不回写。

Mac M4 当前适用于工程开发和少量页面实测；本次只有三页、单次推理样本，没有测量峰值 16GB 统一内存压力、并发、长时间热降频或 107/128 页全量表现。timeout 不能取消服务端任务；重试前检查实际运行状态，避免重叠请求。当前脚本一次运行会在内存保留响应文本，适合显式少量页。

仍为单 writer，跨阶段 ID 依赖/旧 Schema 手工结果迁移/特殊 PDF 几何限制不变。R5 渲染器版本来源和 R6 跨平台复现尚有后续项。Ollama tag 在运行期间被替换、服务版本升级、默认模板改变等都需重新验收；生成完成但缺省 done_reason 时只能依据 done=true 接受，日志保留该字段缺失情况，真实三页均实际提供 stop。

师兄后续迁移 PolyU 时需重新验证：

1. Ubuntu/x86_64、Ollama 或其他推理引擎版本、精确权重 digest/量化、模板与请求参数；相同模型名称不保证相同构建。
2. GPU/驱动/显存、图像预处理、上下文长度、加载与稳定吞吐、超时/重试，以及固定小样本输出差异。Mac 耗时不能直接迁移到 RTX 3080。
3. 人工核对更多代表页，再接 PP-DocLayoutV3 的真实区域/阅读顺序。图文理解、MLLM 线索提取和数据库检索仍未完成，不能把 OCR PageText 成功当成这些阶段成功。

## 7. 数据与 Git 边界

开始时对原有 data/ 的 16 个文件保存 SHA-256，真实实验后核对全部保持不变；新增文件只在独立 data/experiments 目录。原 PDF、既有 PNG/JSONL/manifest/audit 均未覆盖。

只准备提交后端源码、脚本、合成 HTTP 测试、更新的文档及 .gitignore；模型权重、GGUF、.ollama、两份 PDF、PNG、PageText/OCR 原文、实验日志、虚拟环境和任何凭据都不提交。git check-ignore 已确认实验路径被忽略；本地运行日志包含私有原文，不能因其带性能指标就整份上传。

最终检查：原有 16 个数据文件哈希不变，新添 8 个数据文件全部位于独立实验目录；Git data 跟踪项仍只有两个 .gitkeep。43 个可提交文件的常见凭据模式检查未发现匹配（仅允许公开的 loopback 地址），未发现三页 OCR 全文出现在可提交文件中；这不替代提交前人工检查。git diff --check、文档本地链接和 Python 示例编译检查通过；四份历史报告与 HEAD 字节一致。

适合提交现有 Private Repository 供技术审阅；真实识别质量仍需用户确认。未执行 git add/commit/push。建议 Commit Message：

```text
feat: integrate local Ollama OCR with private experiment telemetry
```
