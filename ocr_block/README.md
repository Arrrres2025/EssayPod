# OCR Block — 远端 Paddle Layout OCR 图片拆分

任务处理采用混合策略：独立的大尺寸 PDF 内嵌位图直接提取；由矢量线条、文字标签或碎片位图组成的 Figure 页面才提交给 **远端 Paddle Layout OCR**，再按 `layout_blocks[].type` 裁剪并记录原论文页码。

## 环境变量

```env
PADDLE_OCR_BASE_URL=https://your-ocr-host.example.com
```

- 不要尾部 `/`
- 经 ngrok 访问时客户端已自动加 `ngrok-skip-browser-warning: true`

## 安装

```bash
pip install -r EssayPod/ocr_block/requirements.txt
```

## CLI

```bash
cd EssayPod

python -m ocr_block.run --task-dir tasks/<task_id>
# 或
python -m ocr_block.run --pdf tasks/<id>/input.pdf --out-dir /tmp/ocr_out --mode accurate --device gpu
```

## 输出（`tasks/<id>/ocr_block/`）

| 文件 | 内容 |
|------|------|
| `layout_ocr_result.json` | 远端 OCR 完整响应 |
| `ocr_job_submit.json` | 提交回执（含 `job_id`） |
| `layout_by_type.json` | 全部 layout block，按 `type` 分组 |
| `ocr_blocks.json` | 裁剪后的图片列表 + timing |
| `timing.json` | 各阶段耗时 |
| `status.json` | running / ready / failed |
| `images/page_0003_figure_01.png` | 按 type 命名的裁剪图 |

`ocr_blocks.json` 中每张图：

```json
{
  "type": "figure",
  "page_index": 2,
  "page_number": 3,
  "bbox_pdf": [48.0, 120.5, 380.2, 290.0],
  "bbox_ocr": [96, 241, 760, 580],
  "image_path": "ocr_block/images/page_0003_figure_01.png",
  "block_id": "p0003_figure_01"
}
```

## 裁剪的 type

默认仅裁剪以下 layout type（可在 `layout_parse.py` 的 `DEFAULT_IMAGE_TYPES` 调整）：

`figure`, `image`, `picture`, `photo`, `chart`, `diagram`, `graph`, `illustration`

`text` / `table` 等会写入 `layout_by_type.json`，但不会裁剪为图片。

## 与播客并行

`POST /api/tasks/{id}/generate` 时后台并行启动混合图片提取，默认使用 fast OCR，状态见 `ocr_block/status.json`。

## 耗时字段（`timing.json`）

| 字段 | 含义 |
|------|------|
| `remote_ocr_sec` | 提交 + 轮询远端 job |
| `parse_sec` | 解析 layout_blocks |
| `crop_sec` | PyMuPDF 按 bbox 裁剪 |
| `total_sec` | 总耗时 |
