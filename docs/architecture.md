# 第一阶段架构

当前模块均运行在 Mac CPU，本地文件系统是唯一持久化介质。

```text
CLI (argparse + tomllib)
 ├─ pdf: discover / inspect / page geometry / page selection
 ├─ prepare: sequential get_pixmap → atomic PNG → JSONL checkpoint
 └─ audit: source verification + JSONL + PNG decode / hash / geometry

models: dataclasses 共用结构
storage: SHA-256、JSON/JSONL 原子替换、安全相对路径
adapters: DocumentParserAdapter Protocol + ParserResult（尚无后端）
```

## 状态与恢复

每页独立记录三个阶段状态，`not_run → success | failed`。首次计划页以 `not_run` 保存；渲染失败保留真实错误，并继续后续页。OCR、布局在当前实现中永远不运行。

重新运行对指定页检查状态、DPI、完整字段、PNG 解码、像素尺寸和内容哈希，符合条件才跳过。变更 DPI 或强制重渲染会重置该页下游结果。范围取并集，未请求的页面保持历史结果。若未请求页损坏，自动 audit 会指出它，但不会越过用户指定范围去重渲染。

每页渲染时仅持有当前页面的 pixmap；Python 内存还包括小型元数据索引与 MuPDF 自身的文档缓存，不同时收集全部页图像。索引按页原子重写，针对约 100–130 页报告足够简单；不是为百万页任务设计。很高 DPI 或超大单页仍会显著增加内存。

PNG、JSONL、manifest 各自原子写入，不提供跨文件事务。意外中断可留下一张未索引 PNG 或未完成状态，下次运行会重渲染/恢复。完整 manifest 无效或 JSONL 语法损坏时停止覆盖，先审计。当前单进程顺序使用，不支持两个 writer 同时写同一文档目录。

## 标识与来源

`doc_<SHA256前16位>` 标识 PDF 字节内容，完整哈希写入 manifest 并用于核验。重命名不会改变 ID；修改 PDF 会创建新 ID。短哈希冲突时完整哈希核验阻止覆盖。元信息与路径也属于本地产物，默认不提交。

`printed_page` 独立于物理页码。人工映射有证据后填写；后续视觉页码检测应保留识别来源/置信度并接受人工纠正。当前不推测印刷页码。

## Adapter 接入约定

未来后端实现 `parse(image_path, page) -> ParserResult`。输入为已成功渲染图像及其索引上下文，输出为真实推理结果。OCR-only 后端保持 `layout_status=not_run`；layout-only 后端保持 `ocr_status=not_run`。

后端负责将自身缩放、letterbox、裁剪后的 bbox 反变换至原始 PNG 像素空间。每个区域保留模型/版本来源、可选置信度和阅读顺序。未知的关联关系保持空列表，不能按邻近位置假装模型已推理出关系。未来 runner 还需要区域边界校验、ID 引用校验、阶段结果合并与持久化，当前没有实现这些推理执行能力。

## 坐标依据

PyMuPDF 使用未旋转的页面坐标；显示渲染需要旋转矩阵和 DPI 缩放。实现依据 [PyMuPDF Page API](https://pymupdf.readthedocs.io/en/latest/page.html) 与 [图像渲染说明](https://pymupdf.readthedocs.io/en/latest/recipes-images.html)，同时保存 MediaBox、CropBox 和变换矩阵。具体定义见 [schema.md](schema.md)。
