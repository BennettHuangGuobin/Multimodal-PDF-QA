# pages.jsonl v1.0

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
relations: []
render_status: success
ocr_status: not_run
layout_status: not_run
errors: []
schema_version: "1.0"
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

## 区域与关系预留结构

| 对象 | 字段 |
| --- | --- |
| `TextBlock` | `id`, `text`, `bbox`, `source`, `confidence`, `reading_order`, `layout_type`, `related_image_ids` |
| `ImageRegion` | `id`, `bbox`, `source`, `confidence`, `reading_order`, `layout_type` |
| `Relation` | `source_id`, `target_id`, `relation_type`, `source`, `confidence` |
| `ProcessingError` | `stage`, `message`, `error_type` |

区域 ID 在单页内唯一；关系 ID 引用同页区域。`source` 应记录真实模型/版本或人工来源。`confidence` 可为空，填写时约定为 `[0,1]`；`reading_order` 可为空，填写时为从 1 开始的局部顺序。`layout_type` 不封闭枚举，便于后端映射 paragraph/title/table/figure 等类型。

当前 dataclass/Protocol 提供结构契约，audit 检查核心字段和渲染完整性；尚未实现推理结果的深层语义验证或融合。`success` 且空数组未来可表示模型运行后未发现目标；`not_run` 明确表示没有运行，二者不能混用。

## manifest 与 audit

manifest 中 `expected_pages` 是历次请求范围并集，`source_page_count` 是读取源 PDF 的实际值快照，`last_run` 记录最近请求的页码、DPI、rendered/skipped/failed 计数。`schema_version` 目前仅接受 `1.0`。`expected_page_count` 必须等于计划集合大小，JSONL 实际行数/记录数也必须匹配。

`successful_render_count`、`failed_render_count`、新增的 `pending_render_count` 是索引中 `render_status` 为 success/failed/not_run 的记录数，在每个检查点更新。pending 字段为兼容性新增字段，旧 v1.0 manifest 可以缺省。索引 success 不保证 PNG 在后续仍然完好：audit 顶层 `successful_render_count` 只计入当前可用页面，`index_render_counts` 单独给出索引状态计数，用来核对 manifest。

| manifest.status | 语义及审计规则 |
| --- | --- |
| `complete` | 声明所有计划页的渲染产物完整。若 JSONL/PNG 检查不通过，报告明确矛盾并使 `ok=false`；不代表 OCR、布局或语义质量通过。 |
| `running` | 尚未最终确认的检查点。状态本身是 warning；JSONL 与 manifest 两次原子替换间中断，可造成合法范围内的计数滞后，差异作为 warning 展示。旧 running 文件缺成功/失败计数也可恢复。缺页、失败、不可读 PNG 仍导致 `render_complete=false`、`ok=false`。 |
| `incomplete` | 上次渲染准备未完整完成。计数仍须与索引一致；如果产物已修复且全部通过，保留 warning 提示重新 prepare 收尾，不把该状态一律当作不可恢复错误。 |

未知状态、未知 schema、非整数/负数/超过计划页数的计数是 issue。complete/incomplete 的计数与索引不一致也是 issue；只有 running 的合理检查点差异允许 warning。`last_run` 是运行历史，audit 无法仅凭索引独立重建 rendered 与 skipped 的区别，当前不把它当成产物数量的权威值。

audit 将源 PDF 实际页数、manifest 页数、预期页数、JSONL 行数/有效记录数、成功/失败计数分别输出。`failed_render_count` 只统计显式 `failed`；PNG 损坏记录在 `image_errors`，所有不能使用的计划页汇总为 `unusable_render_pages`。缺失记录并不伪装为运行失败；`missing_pages`、`duplicate_pages`、`pending_render_pages` 分别记录。

`render_complete` 表示 JSONL 结构、计划页与当前 PNG 的完整性；`ok` 进一步要求 manifest/源文件核验没有 issue，且 OCR/布局没有显式 failed。OCR/布局 `not_run` 单独列出，属于正常待办，不会导致失败；`render_complete=true` 与 `ok=true` 均不证明 OCR 或语义质量。

`manifest_status` 在 audit 中保留实际状态，`issues`/`warnings` 给出字段名及 recorded/index 数值。状态为 running/incomplete 时，即使当前产物通过，也应先阅读 warning 并按需重跑 prepare。`source_verified=false` 时，不能把产物自检当成源 PDF 核验成功。若 manifest 身份/页码范围无法解释或 JSON 本身不可读，CLI 返回明确的文档错误，无法生成可信 audit；不会伪造一份成功报告。
