# 第四轮迭代报告：工程交接可靠性

日期：2026-11-27（Asia/Shanghai）。本地起始 HEAD 为已审阅基线 `81356dd`，工作区无待提交修改。本轮仅本地开发、测试和文档整理，未 commit/push，未生成源码 ZIP。

## 1. 实际代码变更与行为差异

### Runner 的 Manifest 写前预检

修改 `src/multimodal_pdf_qa/runner.py::run_parser`，复用 `audit.py` 现有 `render_counts` / `manifest_diagnostics`，没有另写计数规则，也没有调用会改写 audit.json 的 audit_document 来做预检。

- 先沿用全部历史 JSONL 的结构/区域/来源校验，再按真实索引状态核对 Manifest 的 schema_version、status、expected_page_count、successful/failed/pending_render_count。
- 索引全部声称渲染成功时，用既有 check_image 逐页核验所有 PNG，包括未请求页，使 complete 声明的检查有实际产物依据；不把全部图像存进内存。
- 遇到 issue，调用 Adapter 之前抛出 ValueError，错误含具体字段及 recorded/index 值；不改写已有 JSONL、manifest、audit 或 PNG。损坏 PNG 的诊断含实际 PDF 页码。
- 合法 running 单页检查点滞后、旧 running 缺汇总字段及可解释的 incomplete 保持现有 warning/recovery 规则；未成功渲染的页继续跳过。模型流程不修改渲染 manifest，末尾 audit 保留 warning。

此前，汇总字段明显冲突可能直到模型执行/JSONL 写回后的 audit 才暴露；现在提前拒绝。预检仍不是源 PDF 内容的全面重审，源文件核验由 audit 完成；实际缺失索引记录需先 prepare 恢复。预检也不是并发锁，单 writer 约束不变。

`audit.py`、`prepare.py`、`pdf.py`、`storage.py`、模型 Schema 和 CLI 本轮均未修改，避免推翻已有渲染/坐标/恢复体系。

### 跨阶段关系诊断

修改 `validation.py::validate_payload` 和 `validate_parser_result` 的错误信息，保持原有安全拒绝、阶段所有权、旧内容保护及原子写入策略：

| 冲突类型 | 新诊断 |
| --- | --- |
| 区域 ID 重复 | 具体 ID、两处列表/元素位置，如 text_blocks[0] 与 image_regions[0] |
| related_image_ids 错误 | 所属 TextBlock ID、缺失 ImageRegion ID 列表、重复 ID 列表 |
| 悬空/重复 Relation | 关系索引、stage、relation_type、source_id→target_id、缺失 ID |
| 候选验证失败 | PDF 页码、本次替换阶段、拒绝/保留旧内容，以及保持稳定 ID 或联合重跑的提示 |

例如 Layout 改名导致旧 OCR 关系失效时，可定位 `stage='ocr' type='caption_of' 'ocr:text' -> 'layout:title'` 与缺失的 layout:title。不会自动删除有效关系，也不会将非法新结果记为成功；失败阶段状态和错误会更新，原有有效内容/来源保留供排查。

文档明确：单阶段重跑需要稳定且语义一致的被引用 ID；更换 ID 时可联合返回两阶段新区域和引用；人工修复必须停 writer、备份、核实映射/来源并校验后原子保存。两个独立 Runner 调用不是联合事务。

## 2. 新增回归测试

新增 **27** 个参数化展开后的测试实例，原有 195 个全部保留；另增强两个既有跨阶段测试的诊断及来源保留断言。

| 文件 | 新增实例 | 覆盖 |
| --- | ---: | --- |
| `tests/test_runner_manifest.py` | 23 | 12 种错误版本/状态/汇总值；5 种必需字段缺失；4 种正常或兼容检查点；真实 JSONL→manifest 间中断后解析及恢复；complete 下未请求 PNG 缺失 |
| `tests/test_runner.py` | 4 | Layout 图像框保留 ID 时成功、改 ID 时拒绝；联合更新区域与所有者引用成功；OCR ID 变化破坏 Layout 引用时拒绝 |

坏 Manifest 测试检查 Adapter 调用列表为空，并比较文档目录内**全部文件内容及清单**（含 audit.json 与 PNG），确保预检拒绝没有写副作用。合法中断测试通过 monkeypatch 在真实 prepare 的检查点写入处抛出 KeyboardInterrupt，随后 Runner 解析成功页、跳过待渲染页；恢复 prepare 后继续解析剩余页。

跨阶段失败测试同时检查旧文字、布局、图像、关系和 provenance 保留；成功联合重跑同步改变 ID 和端点，确认不存在悬空引用或重复结果。没有新增真实模型或自动关系修复算法。

## 3. 环境复现交付及验证边界

检查 `pyproject.toml` 后保留通用范围：Python >=3.11、PyMuPDF >=1.24,<2、pytest >=8,<10、setuptools >=68。这些范围表示安装约束，不表示每个版本均已测试。

新增 `constraints/verified.txt`，固定实际验证组合：

| 项目 | 版本 |
| --- | --- |
| Python（本轮解释器） | 3.11.18 |
| PyMuPDF | 1.28.2 |
| pytest | 9.2.0 |
| iniconfig | 2.3.1 |
| packaging | 26.4 |
| pluggy | 1.7.0 |
| Pygments | 2.21.0 |
| setuptools（构建后端） | 84.2.1 |
| pip（隔离环境安装器，未锁定） | 26.3 |

新环境建议命令已写入 README/handoff：

```bash
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install -c constraints/verified.txt setuptools
python -m pip install --no-build-isolation -c constraints/verified.txt -e '.[dev]'
python -m pip check
python -m pytest -q
```

先安装固定 setuptools，再关闭本项目构建隔离，防止隔离构建另取未约束后端。约束文件只含包名/版本，不含本机路径、环境、解释器或平台 wheel；pip 按平台选择发行包。没有新增运行时依赖，也没有安装 CUDA/模型框架。

### 隔离复现的实际过程

1. 在系统临时目录新建 Python 3.11.18 venv，没有复制原 .venv，也没有启用 system-site-packages。
2. 首次 pip 获取固定 setuptools 版本返回 ResolutionImpossible / 无匹配发行包，未把失败写成在线安装通过。随后使用当前工作区已有 uv 缓存，以 `--offline` 和上述版本约束安装 setuptools、运行及测试依赖。
3. 将可提交的源码、测试、配置、约束及文档复制到临时目录的源码副本，排除真实 data、.venv、.git、缓存和工具。使用隔离环境的 pip 执行 `install --no-build-isolation -c constraints/verified.txt -e '.[dev]'` 成功；确认 import 来自临时源码副本，而非原工作区。
4. `pip check` 返回 `No broken requirements found.`；在该副本中完整测试和 CLI smoke 均通过。

这是**新虚拟环境 + 独立源码副本 + 已缓存精确版本**的复现证据，不是公网 Clone/包下载成功的证据。GitHub 未在本轮重新 Clone，公网从零获取依赖路径未验证。pip 曾提示默认缓存目录不可写并禁用缓存，不影响后续本地安装/检查。R6 的基础约束与 Mac 隔离验证已完成；跨平台哈希锁、OS/解释器/pip 锁定、Ubuntu x86_64、其他 Python 版本及 GPU 均未验证。

## 4. 师兄接手入口

新增 **[docs/handoff.md](../handoff.md)**，作为正式交接入口，涵盖：

- 横向工程目标、OCR-first Pipeline、已完成模块/入口与测试证据。
- Private Repository Clone、Python venv、约束安装、pytest、三页合成 PDF 及 inspect/prepare/audit 可执行命令。
- 107 / 128 页真实报告的放置方式、少量页处理与私有数据边界；没有复制真实报告内容。
- Adapter 必要属性和方法、ParserResult 返回契约、像素 bbox、PageText、三类区域、独立阶段更新、ID 证据关联。
- 包装调用者真实 infer_text 函数的最小 Python 示例；读取 JSONL/manifest/audit 的示例，明确不把测试 Mock 视为模型。
- Manifest 预检和中断恢复、跨阶段单独/联合/人工修复的适用条件、下一阶段 A/B/C 与所有已知限制。

README 改为快速入门并链接 handoff；Roadmap 区分已完成的本地交接工作与需要服务器实测的工作；architecture/schema 同步新增预检与诊断行为。第三轮 GitHub 状态更新为 `81356dd` 已提交并审阅。历史 phase1_review_report、iteration_02、iteration_03 不回写。

师兄需在用户确认上传第四轮后 Clone/更新，才能获得本轮新增文件；仅 checkout 81356dd 不包含 handoff 和 constraints。

## 5. 实际测试结果

主机：macOS 15.6.1 ARM64，Python 3.11.18。

| 环境/时机 | 实际执行 | 结果 |
| --- | --- | --- |
| 原环境、修改前 | `.venv/bin/python -m pytest -q` | **195 passed, 5 warnings in 2.60s**，退出码 0 |
| 原环境、全部代码修复后 | `.venv/bin/python -m pytest -q` | **222 passed, 5 warnings in 3.33s**，退出码 0 |
| 新 venv、独立源码副本 | 隔离解释器 `-m pytest -q` | **222 passed, 5 warnings in 3.47s**，退出码 0 |

无失败、无跳过。警告与基线一致：SWIG 的 SwigPyPacked 2 条、SwigPyObject 2 条、swigvarlink 1 条缺少 `__module__` 的弃用警告；解释器退出还输出同类 swigvarlink 警告，没有屏蔽。

在新环境/源码副本执行交接文档的合成端到端命令：

| 步骤 | 实际结果 |
| --- | --- |
| create_demo_pdf.py | 生成 3 页合成 PDF，第三页旋转 |
| inspect | 实读 3 页 |
| prepare --pages 1-3 --dpi 144 | 3 页成功，PNG/JSONL/manifest/audit 生成 |
| audit | ok=true、render_complete=true、source_verified=true；OCR/Layout 均 [1,2,3] not_run |
| 再次 prepare | rendered=0、skipped=3，PNG 修改时间未改变 |

本轮只用合成数据运行测试/CLI，未对两份真实报告运行 prepare、Runner 或模型。结束检查确认 data/ 的全部 **16 个文件 SHA-256 与开发前一致**；原始 PDF 与既有处理数据没有改变，也不属于提交范围。

## 6. 剩余风险与优先任务

- **真实后端未验证。** MiniCPM-OCR、PP-DocLayoutV3 的部署、版本、显存及准确率仍未知；不能承诺 RTX 3080 单卡足够。结构 success/audit.ok 不代表模型质量成功。
- **状态和依赖。** 失败阶段保留旧数据只供诊断；跨阶段引用还需检查依赖阶段状态。缓存不跟踪其他阶段内容变化；稳定 ID 只能保证引用存在，不能自动证明同一语义区域。
- **持久化边界。** 单 writer，无跨文件事务或 exactly-once 推理；预检至写入之间不防御并发外部修改。末尾 audit 失败时可能已有 JSONL 检查点，恢复需重新检查。
- **环境复现边界。** Mac 隔离安装来自现有缓存；公网新包获取、Ubuntu/GPU 及其他解释器未验证。固定包版本有助复现，但不是完整带哈希部署锁。
- **既有约束。** v1.0 手工解析数据不能自动补造来源；特殊 UserUnit/CropBox 几何限制不变；R5 每页实际渲染器版本溯源仍待完善。数据库/MLLM 业务能力尚未实现。

下一步按 handoff 的 A/B/C：先在授权 GPU 环境验证真实 OCR/文档后端，再接布局模型并转换契约，最后少量代表页人工验收文字/区域/类别/来源；之后才推进核心 MLLM 和外部检索。

## 7. GitHub 提交范围

可提交：runner.py、validation.py、测试变更、新增 constraints/verified.txt、handoff、本报告及更新的 README/architecture/schema/roadmap。原有 .gitignore 已覆盖 data 下全部非占位数据（包括 processed-demo）、虚拟环境、模型/缓存、密钥和私人配置；无需提交任何平台二进制。

结束检查：`git diff --check` 通过；38 个可提交文件的常见访问令牌、私钥、服务器 IP 和显式密钥赋值模式扫描未发现匹配（不保证覆盖未知凭据格式）；新增/更新文档的本地链接可解析、Python 示例可编译。data 的跟踪项只有两个 .gitkeep，代表性敏感/大型文件路径均被 git check-ignore 命中。三个历史报告与 HEAD 字节一致。

当前没有执行 git add/commit/push，也没有生成 ZIP；HEAD 保持 81356dd。建议提交现有 Private Repository，继续独立技术审阅；提交时仍需检查实际暂存差异。

建议 Commit Message：`fix: harden parser preflight and document reproducible handoff`
