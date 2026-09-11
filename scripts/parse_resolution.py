#!/usr/bin/env python3
"""Normalize common Chinese/English high-resolution requests to JSON."""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from typing import Any


LONG_EDGES = {"4k": 4096, "8k": 8192, "16k": 16384}
CHINESE_NUMBERS = {
    "一": 1.0,
    "二": 2.0,
    "两": 2.0,
    "三": 3.0,
    "四": 4.0,
    "五": 5.0,
    "六": 6.0,
    "七": 7.0,
    "八": 8.0,
    "九": 9.0,
    "十": 10.0,
}


def round_half_up(value: float) -> int:
    return max(1, int(math.floor(value + 0.5)))


def explicit_dimensions(text: str) -> tuple[int, int] | None:
    pair_text = text.replace(",", "").replace("，", "")
    pair = re.search(r"(?<!\d)(\d{2,7})\s*(?:×|x|X|\*)\s*(\d{2,7})(?!\d)", pair_text)
    if pair:
        return int(pair.group(1)), int(pair.group(2))

    labeled = re.search(
        r"(?:宽|宽度|width)\s*[:：]?\s*(\d{2,7})\s*(?:px|像素)?"
        r"[\s,，、;；/]*"
        r"(?:高|高度|height)\s*[:：]?\s*(\d{2,7})\s*(?:px|像素)?",
        text,
        re.IGNORECASE,
    )
    if labeled:
        return int(labeled.group(1)), int(labeled.group(2))
    return None


def requested_scale(text: str) -> float | None:
    digit = re.search(r"(?<![\d.])([0-9]+(?:\.[0-9]+)?)\s*(?:倍|x|X)\b", text)
    if digit:
        return float(digit.group(1))
    chinese = re.search(r"([一二两三四五六七八九十])\s*倍", text)
    if chinese:
        return CHINESE_NUMBERS[chinese.group(1)]
    if re.search(r"\bdouble\b", text, re.IGNORECASE):
        return 2.0
    return None


def calculate_target(input_width: int, input_height: int, mode: str, scale: float | None, long_edge: int | None, width: int | None, height: int | None) -> tuple[int, int]:
    if mode == "explicit":
        assert width is not None and height is not None
        return width, height
    if mode == "scale":
        assert scale is not None
        return round_half_up(input_width * scale), round_half_up(input_height * scale)
    assert mode == "long_edge" and long_edge is not None
    if input_width >= input_height:
        return long_edge, round_half_up(input_height * long_edge / input_width)
    return round_half_up(input_width * long_edge / input_height), long_edge


def parse_request(input_width: int, input_height: int, text: str) -> dict[str, Any]:
    if input_width < 1 or input_height < 1:
        raise ValueError("input dimensions must be positive")
    raw_text = text.strip()
    dimensions = explicit_dimensions(raw_text)
    if dimensions:
        mode = "explicit"
        scale = None
        long_edge = None
        target_width, target_height = dimensions
        reason = "explicit pixel dimensions have highest priority"
    else:
        scale = requested_scale(raw_text)
        long_edge_match = re.search(r"(?<!\d)(4k|8k|16k)(?!\d)", raw_text, re.IGNORECASE)
        if scale is not None:
            mode = "scale"
            long_edge = None
            target_width, target_height = calculate_target(input_width, input_height, mode, scale, None, None, None)
            reason = "explicit enlargement multiplier"
        elif long_edge_match:
            mode = "long_edge"
            long_edge = LONG_EDGES[long_edge_match.group(1).lower()]
            target_width, target_height = calculate_target(input_width, input_height, mode, None, long_edge, None, None)
            scale = None
            reason = "K label maps to target long edge"
        else:
            mode = "scale"
            scale = 2.0
            long_edge = None
            target_width, target_height = calculate_target(input_width, input_height, mode, scale, None, None, None)
            reason = "generic 高清/高分辨率 default is 2x linear size"

    aspect_ratio_changed = target_width * input_height != target_height * input_width
    return {
        "raw_text": raw_text,
        "mode": mode,
        "scale": scale,
        "long_edge": long_edge,
        "target_width": target_width,
        "target_height": target_height,
        "source_width": input_width,
        "source_height": input_height,
        "aspect_ratio_changed": aspect_ratio_changed,
        "reason": reason,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-width", type=int, required=True)
    parser.add_argument("--input-height", type=int, required=True)
    parser.add_argument("--text", required=True)
    parser.add_argument("--output", default="-", help="JSON output path, or - for stdout")
    args = parser.parse_args()
    try:
        result = parse_request(args.input_width, args.input_height, args.text)
    except ValueError as error:
        parser.error(str(error))
    encoded = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output == "-":
        sys.stdout.write(encoded)
    else:
        with open(args.output, "w", encoding="utf-8") as handle:
            handle.write(encoded)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
