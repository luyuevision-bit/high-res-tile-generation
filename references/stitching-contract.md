# 分片 Manifest 与无损拼接约定

## 坐标

所有坐标都是最终画布上的整数、左上角原点坐标：`x` 向右，`y` 向下。每个 tile 有两个矩形：

- `crop`：从生成图片中可使用的连续区域，包含相邻边界所需的 overlap。
- `core`：最终真正写入画布的区域。core 之间必须无缝覆盖目标宽高，不能有空洞或重叠。

通常 `crop` 的四边分别向 core 外扩 overlap，碰到最终画布边缘时截断。因此边缘 tile 的 crop 可能比中间 tile 小；奇数尺寸和末列/末行的余数都要直接记录，不得用固定格宽推算。

## 最低字段

```json
{
  "request": {
    "raw_text": "放大两倍",
    "mode": "scale",
    "scale": 2.0,
    "source_width": 2132,
    "source_height": 738,
    "target_width": 4264,
    "target_height": 1476
  },
  "grid": {
    "columns": 4,
    "rows": 2,
    "tile_count": 8,
    "overlap_px": 192,
    "stitch_mode": "direct",
    "resized": false,
    "blended": false
  },
  "tiles": [
    {
      "id": "tile_r01_c01",
      "row": 0,
      "column": 0,
      "crop": {"x": 0, "y": 0, "width": 1200, "height": 900},
      "core": {"x": 0, "y": 0, "width": 1000, "height": 700},
      "native_width": 1200,
      "native_height": 900,
      "path": "tile_r01_c01.png"
    }
  ]
}
```

实际 manifest 必须列出全部 tile；上面的 `tiles` 数组只是接口示意。`native_width` 和 `native_height` 必须等于文件实际像素尺寸。`path` 可以是 tile 目录下的相对路径，也可以是绝对路径。

## 直接拼接规则

1. 读取每个 tile 的 PNG，不改变尺寸、不旋转、不改变方向。
2. 用 `core.x - crop.x` 与 `core.y - crop.y` 定位 core 在 tile 内的局部坐标。
3. 将 core 原样写入最终画布对应的 `(core.x, core.y)`；不要对相邻 core 做渐变、羽化、模糊或颜色平均。
4. 输出 PNG。PNG 的内部压缩可以减少文件体积，但属于无损编码；不得改用 JPEG 或有损质量参数。
5. `resized` 与 `blended` 必须保持 `false`。如果流程需要把它们改为 `true`，就不再是本 Skill 的无损交付。

Overlap 的像素不要求不同 AI 独立生成结果逐像素相同；它用于给提示词提供连续边界、做人工视觉接缝检查，以及在问题明确时重生成相关 tile。视觉检查应关注断线、重复轮廓和颜色硬切；每个相关 tile 最多重生成两次，失败后报告而不是用混合掩盖。

## 确认门和报告

当自动网格超过 16 张时，先报告目标尺寸、行列数、tile 数、预计生成调用次数（包括一次 probe）和预计输出大小，等待用户确认。manifest 和质量报告要同时保存：目标尺寸、每片实际原生尺寸、最终尺寸、tile 文件路径、是否缩放、是否混合、格式、坐标覆盖结果，以及视觉接缝检查是“已通过”“需人工检查”还是“失败”。
