# high-res-tile-generation

一个用于生成超高清大图的 Codex Skill：先理解“高清”“放大两倍”“8K/16K”等自然语言目标，再独立生成分片，并以 PNG 无损直拼成最终图片。

## 特性

- `高清`：默认宽高各放大 2 倍
- `放大两倍`：按原图宽高精确 ×2
- `4K / 8K / 16K`：目标长边为 4096 / 8192 / 16384 px
- 明确像素尺寸优先
- 先探测生成器原生尺寸，再自动规划网格
- 不偷偷放大，不使用 JPEG，不默认混合接缝

## 使用

在 Codex 中直接描述需求，或显式调用：

```text
$high-res-tile-generation
把这张图片放大两倍，输出无损 PNG。
```

当自动网格超过 16 片时，Skill 会先展示分片数量、预计调用次数和输出大小，等待确认后再批量生成。

## 脚本

```text
scripts/parse_resolution.py    解析自然语言尺寸
scripts/plan_tiles.py          规划目标尺寸、网格、overlap 和 core
scripts/probe_native.py        读取真实 probe 尺寸并重新规划
scripts/record_dimensions.py   记录每片实际原生尺寸和透明度
scripts/stitch_lossless.swift  只拼接 core，输出 PNG
scripts/validate_tiles.py      校验文件、尺寸、格式、覆盖和接缝结果
```

详细规则见 [SKILL.md](SKILL.md)、[resolution-language.md](references/resolution-language.md) 和 [stitching-contract.md](references/stitching-contract.md)。

## 质量原则

最终报告会分别记录目标尺寸、分片原生尺寸和最终尺寸。若原生输出不足或视觉接缝失败，流程会停止报告，不用缩放、模糊或渐变来掩盖问题。
