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
- 六元素仿射矩阵：`x'=a*x+c*y+e`，`y'=b*x+d*y+f`。`pdf_to_mupdf_matrix` 为 PyMuPDF 的 `page.transformation_matrix`。`mupdf_to_image_matrix` 为 `page.rotation_matrix × Scale(dpi/72) × Translate(-pixmap_origin)`。
- 从 PyMuPDF 的未旋转 bbox 转换时只使用 `mupdf_to_image_matrix`；从原始 PDF bbox 转换时先使用 `pdf_to_mupdf_matrix`，再使用前者。用 `transform_bbox` 对矩形四角变换并取轴对齐外包框，不只是分别缩放宽高。
- PNG 尺寸以实际 pixmap 为准，不强制等于 point × DPI 的整数舍入；栅格化允许约 1 像素差异。更复杂 UserUnit / 特殊页面框需用样本进一步验收。

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

manifest 中 `expected_pages` 是历次请求范围并集，`source_page_count` 是读取源 PDF 的实际值快照，`last_run` 记录最近请求的页码、DPI、rendered/skipped/failed 计数。`status=complete` 仅表示已计划的渲染任务完整，不是全报告 OCR 完成。

audit 将源 PDF 实际页数、manifest 页数、预期页数、JSONL 行数/有效记录数、成功/失败计数分别输出。`failed_render_count` 只统计显式 `failed`；PNG 损坏记录在 `image_errors`，所有不能使用的计划页汇总为 `unusable_render_pages`。缺失记录并不伪装为运行失败；`missing_pages`、`duplicate_pages`、`pending_render_pages` 分别记录。

OCR/布局未运行页单独列出，属于正常待办；显式阶段失败才计入失败。`source_verified=false` 时，应查看 warning/issue，不能把产物自检当成源 PDF 核验成功。
