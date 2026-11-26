# 工程交接：从 Clone 到接入真实后端

更新：2026-11-27。第三轮已提交并审阅的基线为 `81356dd`；第四轮改动目前仅在本地，详见 [iteration_04.md](progress/iteration_04.md)。

## 1. 项目目标与边界

这是香港理工大学课题组的横向工程落地项目，目前不是论文投稿项目。目标是理解复杂长篇图文 PDF，并检索外部数据库、保留证据链：

```text
PDF → 页面渲染 → OCR / 文档 MLLM → 结构化图文索引
    → 核心 MLLM 提取图文线索 → Tool Calling → 外部数据库检索 → 证据回链
```

采用 OCR-first。PyMuPDF 用于元信息、渲染和产物校验，不把 PDF 原生文字抽取当作 OCR 替代。当前完成了渲染和 Adapter 工程数据闭环；**真实 OCR/MLLM、布局模型、Tool Calling 和数据库均未接入**。

## 2. 已完成能力与源码入口

下表源码均位于 `src/multimodal_pdf_qa/`，对应测试位于 `tests/`。

| 模块 | 入口及职责 | 测试证据 |
| --- | --- | --- |
| `pdf.py` | inspect_pdf、select_pages、page_geometry、transform_bbox：元信息、1-based 页范围、旋转/裁剪坐标 | test_coordinates、test_pipeline |
| `prepare.py` | prepare_pdf：逐页 PNG、历史预检、JSONL 检查点、跳过/恢复、印刷页码映射 | test_pipeline、test_recovery |
| `models.py` / `adapters.py` | PageRecord.from_dict、DocumentParserAdapter、ParserResult：统一页面/模型输出契约 | test_parser_validation、test_runner |
| `validation.py` | validate_parser_result：类型、bbox、置信度、ID/引用、阶段归属、来源及合并候选验证 | test_parser_validation、跨阶段 Runner 测试 |
| `runner.py` | run_parser：Manifest/索引预检、PNG 核验、显式 Adapter 调用、分阶段替换、恢复 | test_runner、test_runner_manifest |
| `storage.py` | read_jsonl、write_jsonl、write_json、sha256_file：原子替换与哈希 | Runner 写入失败及中断测试 |
| `audit.py` | audit_document、manifest_diagnostics：源文件、产物、汇总一致性与解析结构检查 | test_audit_manifest、test_recovery |
| `cli.py` | inspect / prepare / audit；JSON stdout、进度/错误 stderr | test_cli |

本轮本地完整测试 **222 passed，5 条 SWIG 弃用警告**；前三轮 195 项用例保留通过。详细执行环境、隔离环境与 CLI 证据见本轮报告。测试里的 SyntheticAdapter 完全是人工定义数据，只用于临时合成 PDF，不是模型识别成果。

`render_status=success` 仅表示页面图像已生成。manifest.complete 只描述渲染准备状态；audit.render_complete 也不等于 OCR 完成。OCR/Layout not_run 是正常待处理状态；audit.ok 是结构、状态与完整性检查通过，**不证明文字、类别或语义正确**。

## 3. 新环境启动

需要有 Private Repository 访问权限和 Python 3.11+，优先使用已验证的 **3.11.18**。下面命令从新终端执行；认证使用本人正常 GitHub 登录方式，不在命令/配置中写 Token：

```bash
git clone https://github.com/BennettHuangGuobin/Multimodal-PDF-QA.git
cd Multimodal-PDF-QA
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install -c constraints/verified.txt setuptools
python -m pip install --no-build-isolation -c constraints/verified.txt -e '.[dev]'
python -m pip check
python -m pytest -q
```

确认仓库已包含第四轮提交，能找到本文件与 constraints/verified.txt；`81356dd` 是审阅基线，尚不包含这两个新增文件。Python 3.12+ 可替换解释器命令，但本轮未实测这些版本。Ubuntu 若缺 venv 模块，先由环境维护者安装对应 Python 的 venv 支持；不需要 CUDA。

[constraints/verified.txt](../constraints/verified.txt) 固定已验证组合：PyMuPDF 1.28.2、pytest 9.2.0、iniconfig 2.3.1、packaging 26.4、pluggy 1.7.0、Pygments 2.21.0、构建后端 setuptools 84.2.1。先安装固定 setuptools，再以 `--no-build-isolation` 构建本项目，避免隔离构建另取未约束版本。`pyproject.toml` 保留通用安装范围；范围内每个版本都通过测试并不是本项目的承诺。

这是版本约束集合，不是含平台 wheel/哈希的完整锁文件；没有锁定 OS、解释器或 pip。安装器按当前平台选包，不应拷贝 Mac 的 .venv、Python 可执行文件或 wheel 到服务器。Mac ARM64 的隔离环境已验证；**Ubuntu x86_64 / GPU 未验证**。当前执行环境首次 pip 获取固定版本未成功，改用本机已有 uv 缓存离线安装后完成 pip 本地项目安装与检查；公网从零下载路径未验证。新环境需要能获取这些版本的包源或由维护者准备匹配平台的可信缓存，不依赖仓库中的 .tools/.python/.uv-cache。

### 不依赖真实 PDF 的端到端验证

```bash
python scripts/create_demo_pdf.py --output data/input/demo.pdf
multimodal-pdf-qa inspect data/input/demo.pdf
multimodal-pdf-qa prepare data/input/demo.pdf --pages 1-3 --dpi 144 --output data/processed-demo
multimodal-pdf-qa audit data/processed-demo
```

预期：三页成功，audit.ok=true、source_verified=true，OCR/Layout 均 not_run。重复执行 prepare 应跳过三页；生成脚本拒绝覆盖已有 demo.pdf，文件已存在时直接复用或换新名字。`data/processed-demo` 是被 Git 忽略的合成输出目录。Adapter 闭环验证使用 `python -m pytest tests/test_runner.py tests/test_runner_manifest.py -q`；这些测试不会触碰真实报告。

CLI 退出码：0=完成/审计通过，1=文档或产物错误，2=参数/配置/输入选择错误，130=中断。批量 1/2 混合时以 2 为最终码，其他文档可能已完成。Python Runner 使用异常报告预检和存储错误，正常返回中的 failed_pages/audit 也必须检查，不能只看函数没有抛异常。

## 4. 真实资料与产物

两份已有业务测试报告为《26春夏抖音电商时尚消费趋势报告》（107 页）及《26SS春夏淘宝服饰白皮书》（128 页）。文件不包含在 GitHub 中，经授权获取后放在 data/input/；不要提交原文或生成图片。本轮没有处理原始报告。

```bash
multimodal-pdf-qa inspect 'data/input/26SS春夏淘宝服饰白皮书.pdf'
multimodal-pdf-qa prepare 'data/input/26SS春夏淘宝服饰白皮书.pdf' --pages 1-3 --output data/processed
multimodal-pdf-qa audit data/processed
```

页码均为 1-based，范围含两端；不传 --pages 会处理全部页面。目录输入只扫描直接子文件 PDF。单文件人工印刷页码映射用 `--printed-pages configs/printed_pages.example.json`，实际映射复制到被忽略的 configs/local_pages.json 后按证据编辑；printed_page 未知为 null，绝不默认等于 pdf_page。

```text
data/processed/doc_<PDF哈希前16位>/
├── pages/page_0001.png
├── pages.jsonl       # 每个计划 PDF 页一行；内容、几何、状态、来源与错误
├── manifest.json     # 源哈希/页数、计划页并集、渲染计数和检查点状态
└── audit.json        # 最近一次检查的快照，不是自动更新的实时状态
```

内容哈希定义 document_id，PNG 路径相对该文档目录。更改 DPI、几何或 force 重渲染会清空该页下游结果；正常渲染跳过保留解析内容。单独重渲染/运行后端前先备份有价值的处理数据。

## 5. Adapter 接入

必要属性/方法：`name: str`（模型版本及关键配置身份）、`stages: tuple[str,...]`（ocr、layout 或二者，无重复）、`parse(image_path: Path, page: PageRecord) -> ParserResult`。不要求继承基类。只读输入 PNG，上下文是副本；模型加载和框架导入放在独立后端层。

| 输出 | 用途/归属 |
| --- | --- |
| text_blocks: list[TextBlock] | OCR 的有可靠定位文字：id/text/bbox/source，可选 confidence/reading_order/layout_type/related_image_ids |
| page_text: PageText 或 None | OCR 的页面级文字：text/source/confidence；无可靠 bbox 时使用，不制造整页文字框，无区域 ID |
| layout_regions: list[LayoutRegion] | Layout 的通用区域：标题/段落/表格/图片等；layout_type 必填 |
| image_regions: list[ImageRegion] | Layout 的实际图像证据区域，不把所有布局框解释成图片；一般不必复制已存在的布局框 |
| relations: list[Relation] | 同页 ID 引用，source_id/target_id/relation_type/source/confidence；stage 决定由谁替换，OCR 关系显式填 stage="ocr"，默认 layout |
| ocr_status / layout_status | not_run / success / failed；只更新 stages 声明的阶段 |
| errors | ProcessingError(stage, message, error_type)，只能归属本次负责的失败阶段 |
| ocr_empty_reason / layout_empty_reason | 确认实际执行后无目标时的非空原因；空数组本身不能代表成功 |

bbox 使用输入**渲染 PNG 像素** `[x0,y0,x1,y1]`，左上原点，x 向右、y 向下，要求 `0≤x0<x1≤image_width`、`0≤y0<y1≤image_height`。后端必须自行逆转缩放/letterbox/裁剪，转成 Python 有限 int/float，不能填 NaN/Infinity 或归一化坐标。置信度 null 或 [0,1]，阅读顺序 null 或正整数，区域 ID 跨类型单页唯一。详细字段见 [schema.md](schema.md)。

未负责阶段必须在 ParserResult 中保持 not_run 且不返回该阶段数据，它不会覆盖已有页面状态。负责阶段必须明确 success/failed。success 需要内容或明确空原因，Runner 写入绑定 PNG 哈希的 provenance；failed 必须有错误且不得带部分新数据。异常/非法结果拒绝提交新内容，保存阶段失败及之前有效数据，旧数据仅供诊断。

### 最小 Python 接口示例

下面只展示如何包装**调用者已经实现的真实文字推理函数**，不是 MiniCPM-OCR 实现，也没有内置 Mock：

```python
from collections.abc import Callable
from pathlib import Path
from multimodal_pdf_qa.adapters import ParserResult
from multimodal_pdf_qa.models import PageRecord, PageText
from multimodal_pdf_qa.runner import run_parser

class PageTextOCRAdapter:
    stages = ("ocr",)

    def __init__(self, name: str, infer_text: Callable[[Path], str]):
        self.name = name  # 含真实模型版本/配置；不是 API Key
        self.infer_text = infer_text

    def parse(self, image_path: Path, page: PageRecord) -> ParserResult:
        text = self.infer_text(image_path)  # 此函数由真实后端提供
        if not isinstance(text, str) or not text.strip():
            raise ValueError("后端没有返回非空文字；须区分运行失败与确认无文字")
        return ParserResult(
            ocr_status="success", page_text=PageText(text=text, source=self.name)
        )

def parse_ocr(document_dir: Path, infer_text: Callable[[Path], str], model_identity: str):
    adapter = PageTextOCRAdapter(model_identity, infer_text)
    return run_parser(document_dir, adapter, pages="1-3", force=False)
```

document_dir 是单个 doc_... 目录，不是 processed 根目录；从 prepare 的 stdout JSON 读取 document_dir。上例对空返回保守报错；真实后端只有在确认运行完成且未检出文字时，才返回 `ParserResult(ocr_status="success", ocr_empty_reason="具体原因")`。不要直接调用 tests 的 SyntheticAdapter 处理真实报告。

Runner 先检查全部索引，并复用 manifest_diagnostics 核对版本/状态/计数。在索引全为渲染成功时，还逐页核验 PNG，使 manifest.complete 不能掩盖未请求页损坏。出现 issue 会在调用模型与修改任何输出前拒绝；合法单页计数滞后的 running、旧 running 缺计数和可解释的 incomplete 保留 warning/recovery 语义。未完成页面跳过，源 PDF 内容核验仍由 audit 完成。缺失索引记录需要先 prepare 恢复，不能只调用 Runner。

成功阶段整批替换自己的输出，不 append，不覆盖另一阶段。相同 name+PNG 哈希且负责阶段均 success 时默认跳过；换版本/配置应改 name 或 force。逐页原子写 JSONL，结束刷新 audit，不修改渲染 manifest。存储失败或中断向上抛出，模型可能已调用，重试不保证 exactly-once；强制重试需继续传 force=True。

### 跨阶段 ID 与关系更新

| 方式 | 适用条件与限制 |
| --- | --- |
| 单阶段重跑 | 未改另一阶段引用的 ID，或没有跨阶段引用。稳定 ID 必须仍指向同一语义区域，不能仅为绕过校验随意复用。缓存不追踪依赖内容变化，必要时 force 重跑依赖阶段。 |
| 联合重跑 | 区域 ID 需改变且另一阶段引用它。用 stages=("ocr","layout") 一次返回新区域及更新后的所有所属引用；单页候选整体验证后原子保存。不要把两个独立 Runner 调用当作联合事务。 |
| 人工修复 | 仅在可根据原图确认映射/错误关系时采用。先停止 writer、备份产物，记录人工来源与理由，在候选副本里同步修改区域和所有引用，再校验、原子保存、重新 audit。无通用修复 CLI；不要只删报错关系或捏造成功/来源来绕过验证。 |

更换 Layout ID 导致 OCR 引用失效时，错误会给出页码、替换阶段、具体关系索引/类型/端点或 TextBlock.related_image_ids 的缺失 ID。旧内容和关系保留，Layout 记 failed，OCR 原状态保留；消费者使用跨阶段关系前须检查两阶段状态。反向 OCR 改 ID 破坏 Layout 引用同样拒绝。

### 读取结果与证据标识

```python
import json
from pathlib import Path
from multimodal_pdf_qa.models import PageRecord
from multimodal_pdf_qa.storage import read_jsonl
from multimodal_pdf_qa.audit import audit_document

def read_outputs(document_dir: Path):
    audit = audit_document(document_dir)  # 重查产物并更新 audit.json
    manifest = json.loads((document_dir / "manifest.json").read_text(encoding="utf-8"))
    rows, errors, line_count = read_jsonl(document_dir / "pages.jsonl")
    if errors or not audit["ok"]:
        raise ValueError({"jsonl_errors": errors, "audit": audit})
    return manifest, [PageRecord.from_dict(row) for row in rows], audit
```

继续使用前检查 ocr_status/layout_status 是否 success、source_verified 和 warnings；audit.ok 可在 OCR 尚未运行或旧数据未验证时成立。区域证据用 `(document_id, pdf_page, region_id)`，结合 page_image_path、bbox、source 与 parser_provenance 的 adapter/PNG 哈希。PageText 只能作为页面级证据，不虚构 region_id；printed_page 是独立展示字段。强制替换可能改变区域 ID，尚无跨版本永久区域标识。

## 6. 接手后的前三项任务

A. 经授权在 PolyU RTX 3080 环境验证真实 OCR/文档 MLLM，优先验证 MiniCPM-OCR 的最小可用输入/输出；记录模型版本、依赖、缩放、耗时、显存和空结果行为，再包装 Adapter。

B. 验证 PP-DocLayoutV3 等布局后端，将真实类别、坐标、阅读顺序和来源转换为当前契约；先写输出转换测试，再接 Runner。不要把标题/表格等都写成 ImageRegion。

C. 用少量代表页人工核对文字、图像/布局区域、类别和证据来源，检查错误恢复及跨阶段引用，再推进核心 MLLM 线索提取和外部数据库检索。

**MiniCPM-OCR 和 PP-DocLayoutV3 的部署、模型版本、显存适配及准确率均未验证**，不能保证指定模型在 3080 单卡上满足需求。

## 7. 已知限制与提交边界

- 单 writer，无多进程锁/跨文件事务；不要并发运行 prepare 与 Runner。audit 是快照，中断或外部修改后重新检查。
- 跨阶段依赖由开发者维护，不自动删除关系、重建引用或融合多模型；失败保留的旧数组不是当前成功成果。
- 页面 v1.1 兼容原 v1.0 空/not_run 页；手工解析过但无来源的旧数据只能读取并警告，Runner 拒绝自动迁移。先备份，再显式 force-prepare 重建或制定人工迁移流程。
- 非默认 UserUnit、越界 CropBox 等特殊几何明确不支持；旧未请求页的矩阵不自动修复。audit 不能替代独立几何真值测试。
- R5 每页实际渲染器版本溯源待完善。R6 本轮已有版本约束及 Mac 隔离验证，完整哈希锁、Python 其他版本及 Ubuntu x86_64/GPU 仍未验证。
- 无真实 OCR/MLLM/布局、数据库、RAG、Agent 或 Web UI 实现；后端测试成功不等于业务准确率通过。

只提交代码、测试、公开示例、约束文件、文档和 .gitkeep。原始报告、生成数据、环境、缓存、模型权重、.env/API Key/服务器凭据均不提交。每次提交前查看 `git status --short` 与实际暂存差异；.gitignore 不能替代敏感内容检查。本轮未 commit/push，后续通过现有 Private Repository 审阅。
