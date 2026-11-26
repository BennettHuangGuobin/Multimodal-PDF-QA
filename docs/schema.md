# pages.jsonl v1.1（兼容 v1.0）

一行一个 JSON 对象，按 `pdf_page` 升序；所有计划页，包括失败/尚未执行页，都有记录。无 OCR 时绝不填充伪造文本、区域或关系。

核心字段在 README 中汇总；数据模型见 `src/multimodal_pdf_qa/models.py`。以下是结构说明，尖括号是说明占位符，不是可运行输出：

```text
document_id: doc_<content hash prefix>
pdf_page: 1
printed_page: null
page_width / page_height: rotated visible page dimensions in points
rotation: 0 | 90 | 180 | 270
media_box / crop_box: [x0, y0, x1, y1] in PyMuPDF points
image_width / image_height: actual PNG pixels
render_dpi: 144
page_image_path: pages/page_0001.png
page_image_sha256: <PNG content hash>
text_blocks: []
image_regions: []
layout_regions: []
page_text: null
parser_provenance: {}
relations: []
render_status: success
ocr_status: not_run
layout_status: not_run
errors: []
schema_version: "1.1"
bbox_coordinate_system: image_pixels_top_left_xyxy
pdf_to_mupdf_matrix / mupdf_to_image_matrix: [a, b, c, d, e, f]
```

## 坐标系

- PDF 原始 user space 通常左下角原点，y 向上；PyMuPDF 通常左上角原点，y 向下，point 单位。不可仅凭宽高猜测转换，尤其有旋转、CropBox 偏移时。
- `page_width/height` 等于 `page.rect.width/height`，即旋转后可见区域；`media_box/crop_box` 直接保存 PyMuPDF 提供的页面框，不是图像像素 bbox。
- 所有 OCR/布局区域 bbox 使用原始渲染 PNG 的像素，格式 `[x0,y0,x1,y1]`，允许浮点，要求 `0 ≤ x0 < x1 ≤ image_width`、`0 ≤ y0 < y1 ≤ image_height`。矩形右/下界为几何边界，裁剪时可按半开区间理解；取整由使用方明确决定。
- 六元素仿射矩阵：`x'=a*x+c*y+e`，`y'=b*x+d*y+f`。第二轮起不再直接存储旋转页面的 `page.transformation_matrix`，而是根据页面框独立构造 `pdf_to_mupdf_matrix`，见下方公式。`mupdf_to_image_matrix` 仍为 `page.rotation_matrix × Scale(dpi/72) × Translate(-pixmap_origin)`，该段计算未改变。
- 从 PyMuPDF 的未旋转 bbox 转换时只使用 `mupdf_to_image_matrix`；从原始 PDF bbox 转换时先使用 `pdf_to_mupdf_matrix`，再使用前者。用 `transform_bbox` 对矩形四角变换并取轴对齐外包框，不只是分别缩放宽高。
- PNG 尺寸以实际 pixmap 为准，不强制等于 point × DPI 的整数舍入；栅格化允许约 1 像素差异。

### 原始 PDF → 未旋转 PyMuPDF → PNG

对普通 `UserUnit=1` 页面，设 PyMuPDF 返回 `media_box=M`、`crop_box=C`。CropBox 的 y 坐标相对于 MediaBox 上边沿，原始 PDF 的有效框为：

```text
raw_crop = [C.x0, M.y1 - C.y1, C.x1, M.y1 - C.y0]
pdf_to_mupdf_matrix = [1, 0, 0, -1, -raw_crop.x0, raw_crop.y1]
x_unrotated = x_pdf - raw_crop.x0
y_unrotated = raw_crop.y1 - y_pdf
```

这一步只处理原点平移与 y 翻转，**不包含页面旋转**。之后才应用 `mupdf_to_image_matrix`，得到显示方向下的 PNG 像素。输入 bbox 必须已经在页面 PDF user space 内；Form XObject、图片或内容流的局部坐标需要先应用它们自己的 CTM，不能直接套用页面变换。

例如 240×320 页面、裁剪 `[20,30,220,290]`，PDF bbox `[40,140,120,230]` 应变为未旋转框 `[20,60,100,150]`，不随页面旋转改变。测试通过直接写入 PDF 内容流定义矩形，覆盖 0/90/180/270°、有/无裁剪、两个 MediaBox 原点，并核对实际 PNG 色块边界；不是仅用同一矩阵正逆变换验证。

当前支持范围要求 CropBox 完全位于 MediaBox 内、无额外页面尺度变化。非默认 `UserUnit` 导致尺寸比例变化、CropBox 越界或空框会给出 `Unsupported page geometry`：inspect 失败，prepare 将该页记为 `failed` 并继续其他页，不输出伪正确矩阵。对尺度使用有限浮点容差检测；畸形 PDF 的底层自动修复、其他特殊坐标组合尚未全面验证。

第一阶段的旧索引没有自动全量迁移。prepare 对**本次请求页**重新计算几何元信息，若旧矩阵不同，会重渲染并重置该页下游结果。未请求页不修改；应显式重跑曾出现旋转和裁剪组合的页码。audit 仍不是逐页源几何的独立重算器，不能据其通过认定未迁移的旧矩阵已经正确。

## 解析结构与运行时校验

| 对象 | 字段 |
| --- | --- |
| `TextBlock` | `id`, `text`, `bbox`, `source`, `confidence`, `reading_order`, `layout_type`, `related_image_ids` |
| `ImageRegion` | `id`, `bbox`, `source`, `confidence`, `reading_order`, `layout_type` |
| `LayoutRegion` | `id`, `bbox`, `source`, **必填** `layout_type`, `confidence`, `reading_order`；标题/段落/表格等通用布局 |
| `PageText` | `text`, `source`, `confidence`；页面级文字，无 bbox，无区域 ID |
| `Relation` | `source_id`, `target_id`, `relation_type`, `source`, `confidence`, `stage`（`ocr` / `layout`，默认 `layout`） |
| `ParserProvenance` | `adapter`, `image_sha256`, `empty_reason`；按阶段保存在 `parser_provenance` |
| `ProcessingError` | `stage`, `message`, `error_type` |

`ImageRegion` 只用于实际图像内容；PP-DocLayoutV3 等后端的标题、段落、表格、图片等检测类别可全部保存在 `LayoutRegion`，不必复制到 ImageRegion。只有需要单独表示实际图像证据时才填 ImageRegion，避免重复。不能将每个检测框都解释为图片。只有页面级文字时使用 `page_text`，不将整页尺寸伪装成文字定位；该对象没有区域 ID，不能成为区域关系端点。

`validation.py` 检查完整合并候选：

- 三种区域 ID 必须是非空字符串，单页内跨类型唯一；建议 `ocr:` / `layout:` 前缀及稳定的页内编号。关系必须指向该页真实区域；`related_image_ids` 只指向 ImageRegion，不能指向通用布局框。重复关系（归属、端点、类型相同）和重复图像引用拒绝。
- bbox 为四个有限 Python int/float（排除 bool），不得 NaN/Infinity；严格满足图像边界及正面积，不能归一化或倒序。JSON 表示为数组。后端的 NumPy 数字需先转为普通 Python 数字。Runner 实际解码 PNG 核对尺寸及 SHA-256，验证器按这份已核验的页面尺寸检查坐标。
- `confidence=null` 或有限 `[0,1]` 数值；`reading_order=null` 或从 1 开始的整数（排除 bool）；不要求多个区域的阅读顺序值互不相同。
- `source`、ID、文字、关系类型必须是非空字符串；`layout_type` 为非空字符串，TextBlock/ImageRegion 允许 null，LayoutRegion 必填。类别不封闭枚举，允许后端映射 `paragraph/title/table/figure` 等。
- 对象类型、列表类型、错误字段、所有阶段状态及来源指纹都检查；错误的空对象 `{}` 不能冒充空数组。未知字段反序列化报错，不静默丢弃。结构检查不证明文本准确性、布局类别正确性或关系语义。

### 阶段、所有权及合并

`ParserResult` 包含上述输出字段、`ocr_status`、`layout_status`、`errors` 及 `ocr_empty_reason/layout_empty_reason`。Adapter 必须声明唯一非空 `stages` tuple，值只能是 `ocr`、`layout`。声明负责的阶段必须返回 success 或 failed；未声明阶段在结果中只能 not_run 且没有数据/错误/关系。结果中的 not_run 表示“不更新此阶段”，不能覆盖已有页面状态。

| 页面阶段状态 | 契约 |
| --- | --- |
| `not_run` | 尚未运行或重渲染后失效；该阶段没有数据、关系、来源或错误。 |
| `success` | 本次 Adapter 调用返回结果并通过验证，必须有文字/区域内容，或明确非空 `*_empty_reason` 说明真实执行后为何无目标；不能以空数组自动判断成功，也不能同时有内容与 empty_reason。必须有匹配 PNG 的 provenance，不能带本阶段未解决错误。 |
| `failed` | 后端报告失败、抛出异常或输出非法。必须有本阶段 ProcessingError；部分新输出不接受。可保留上次已接受内容及 provenance 供排查，但不代表当前成功。 |

OCR 拥有 text_blocks/page_text，Layout 拥有 image_regions/layout_regions。关系按 stage 归属：OCR 生成的关系必须显式 `stage="ocr"`，默认值用于布局关系。成功阶段整体替换自己的内容和关系、清理自己的旧错误并更新 provenance；另一阶段的内容、状态、来源和错误原样保留。同一阶段换 Adapter 也采用替换策略，不做追加或多模型融合。

替换后检查整页引用，跨阶段 ID 碰撞或悬空引用使本次结果失败。若另一阶段依赖即将移除的 ID，需要维持稳定 ID、先更新依赖，或用联合 Adapter 一并替换；不能静默删除另一阶段的关系。联合 Adapter 可明确返回某一阶段 failed、另一阶段 success，此时只提交通过验证的成功部分及失败状态；整个结果类型/引用等非法时，本次声明的全部阶段记为 failed。

`parser_provenance` 保存每阶段最后接受的 Adapter 标识（版本/配置应包含在 name）和输入 PNG SHA-256；empty_reason 只允许在无内容时填写。阶段失败后保留旧 provenance，失败调用身份在 errors.message 中。默认仅跳过同 Adapter 标识、同 PNG 哈希且状态 success 的阶段组合；force 重新调用。重渲染创建新 PageRecord，清空全部解析数据和来源并设为 not_run；中断期间 PNG 与旧索引哈希不符时 audit/Runner 拒绝把旧结果当作有效结果。

消费者应同时检查 render_status、所用阶段状态及 audit；引用跨阶段结果时还需检查依赖阶段。保留的失败阶段数据、legacy warning 下的数据不可当作已验证识别成果。来源记录与空结果原因是后端契约，不能从结构检查独立证明模型真的执行或识别准确。

### v1.0 兼容和迁移

`PageRecord.from_dict` 对缺省的 layout_regions/page_text/parser_provenance 分别补 `[]/null/{}`，旧 Relation 缺 stage 时解码为 layout；旧 ImageRegion 保持 ImageRegion，不自动重分类。decode 仅还原类型，业务使用仍须通过验证。v1.0 和 v1.1 可以同文件共存；manifest/audit 的独立 schema_version 仍为 1.0。

历史 prepare 生成的 v1.0 记录为 OCR/Layout not_run 且为空，Runner 可在真正调用并落盘该页时升级到 1.1；未请求页不改动。prepare 跳过旧页时保留旧版本，重渲染时生成 1.1。新 v1.1 记录三个新增字段必须存在。

若旧记录手工填过解析数据或非 not_run 状态，audit 可以读取、校验通用字段并发出“无可信执行来源”的 warning；不将旧空 success 自动当作本轮验证成功。Runner 对选中的这类页在调用/写入前拒绝，要求先备份，再显式 force-prepare 重建该页后由真实后端重跑。不猜测旧关系归属、不补造模型来源或空结果原因。这是有意保留的兼容限制。

## manifest 与 audit

manifest 中 `expected_pages` 是历次请求范围并集，`source_page_count` 是读取源 PDF 的实际值快照，`last_run` 记录最近请求的页码、DPI、rendered/skipped/failed 计数。`schema_version` 目前仅接受 `1.0`。`expected_page_count` 必须等于计划集合大小，JSONL 实际行数/记录数也必须匹配。

`successful_render_count`、`failed_render_count`、新增的 `pending_render_count` 是索引中 `render_status` 为 success/failed/not_run 的记录数，在每个检查点更新。pending 字段为兼容性新增字段，旧 v1.0 manifest 可以缺省。索引 success 不保证 PNG 在后续仍然完好：audit 顶层 `successful_render_count` 只计入当前可用页面，`index_render_counts` 单独给出索引状态计数，用来核对 manifest。

| manifest.status | 语义及审计规则 |
| --- | --- |
| `complete` | 声明所有计划页的渲染产物完整。若 JSONL/PNG 检查不通过，报告明确矛盾并使 `ok=false`；不代表 OCR、布局或语义质量通过。 |
| `running` | 尚未最终确认的检查点。状态本身是 warning；逐页 JSONL 先于 manifest 落盘，只容许下述单页状态转移造成的滞后。旧 running 文件缺成功/失败计数仍提示 warning，计数一致性未完全验证。缺页、失败、不可读 PNG 仍导致 `render_complete=false`、`ok=false`。 |
| `incomplete` | 上次渲染准备未完整完成。计数仍须与索引一致；如果产物已修复且全部通过，保留 warning 提示重新 prepare 收尾，不把该状态一律当作不可恢复错误。 |

未知状态、未知 schema、非整数/负数/超过计划页数的计数是 issue。complete/incomplete 的计数与索引不一致也是 issue；只有 running 的合理检查点差异允许 warning。`last_run` 是运行历史，audit 无法仅凭索引独立重建 rendered 与 skipped 的区别，当前不把它当成产物数量的权威值。

第三轮限定 running 的计数宽容边界：按 `(success, failed, pending)`，`index - manifest` 只能是 `(1,0,-1)`、`(0,1,-1)`、`(1,-1,0)`、`(-1,1,0)`，分别对应一页 pending→success/failed、failed→success、success→failed。两侧计数必须非负且合计等于 expected_page_count。两页以上差异、不守恒或回退为 pending 均是 issue；差异为零无需计数 warning。旧 manifest 缺 pending 时可由总页数减去 success/failed 推算；不能从缺失计数推断任意大差异合法。首次/扩展计划时先写 manifest 后写索引，中断留下的缺页仍按缺页报告，可通过 prepare 恢复。

audit 将源 PDF 实际页数、manifest 页数、预期页数、JSONL 行数/有效记录数、成功/失败计数分别输出。`failed_render_count` 只统计显式 `failed`；PNG 损坏记录在 `image_errors`，所有不能使用的计划页汇总为 `unusable_render_pages`。缺失记录并不伪装为运行失败；`missing_pages`、`duplicate_pages`、`pending_render_pages` 分别记录。

`render_complete` 表示 JSONL 核心结构、计划页与当前 PNG 的完整性；`ok` 进一步要求 manifest/源文件核验没有 issue，`invalid_parser_records` 为空且 OCR/布局没有显式 failed。解析对象深层错误单列于 invalid_parser_records，避免伪装成渲染失败；核心字段缺失/非法状态仍可能同时进入 invalid_records。OCR/布局 `not_run` 单独列出，属于正常待办，不会导致失败；`render_complete=true` 与 `ok=true` 均不证明 OCR 或语义质量。

`manifest_status` 在 audit 中保留实际状态，`issues`/`warnings` 给出字段名及 recorded/index 数值。状态为 running/incomplete 时，即使当前产物通过，也应先阅读 warning 并按需重跑 prepare。`source_verified=false` 时，不能把产物自检当成源 PDF 核验成功。若 manifest 身份/页码范围无法解释或 JSON 本身不可读，CLI 返回明确的文档错误，无法生成可信 audit；不会伪造一份成功报告。

audit.json 是生成时的快照。中断、外部文件变化或 audit 自身写入失败后应重新运行 audit，不能仅凭旧报告认定当前文件有效。
