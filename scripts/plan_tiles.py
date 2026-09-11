#!/usr/bin/env python3
"""Plan a native-size tiled image job without touching image pixels.

The planner deliberately does not guess a generator's output size.  Without
native tile dimensions it emits a needs_probe plan; once a real probe has been
measured, pass those dimensions back in and the grid becomes deterministic.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from typing import Any


def rounded(value: float) -> int:
    """Round halves up so planning is stable across Python versions."""

    return max(1, int(math.floor(value + 0.5)))


def split_bounds(total: int, count: int) -> list[tuple[int, int]]:
    """Split a dimension into count integer intervals covering it exactly."""

    return [(total * index // count, total * (index + 1) // count) for index in range(count)]


def parse_grid(value: str) -> tuple[int, int]:
    match = re.fullmatch(r"\s*(\d+)\s*[xX×]\s*(\d+)\s*", value)
    if not match:
        raise ValueError(f"invalid grid {value!r}; use CxR, for example 4x2")
    columns, rows = (int(part) for part in match.groups())
    if columns < 1 or rows < 1:
        raise ValueError("grid rows and columns must be positive")
    return columns, rows


def rect(x: int, y: int, width: int, height: int) -> dict[str, int]:
    return {"x": x, "y": y, "width": width, "height": height}


def tile_filename(row: int, column: int, rows: int) -> str:
    if rows == 2:
        side = "top" if row == 0 else "bottom"
        return f"tile_{side}_{column + 1:02d}.png"
    return f"tile_r{row + 1:02d}_c{column + 1:02d}.png"


def build_tiles(
    target_width: int,
    target_height: int,
    columns: int,
    rows: int,
    overlap: int,
) -> list[dict[str, Any]]:
    x_bounds = split_bounds(target_width, columns)
    y_bounds = split_bounds(target_height, rows)
    tiles: list[dict[str, Any]] = []

    for row, (y0, y1) in enumerate(y_bounds):
        for column, (x0, x1) in enumerate(x_bounds):
            crop_x0 = max(0, x0 - overlap)
            crop_y0 = max(0, y0 - overlap)
            crop_x1 = min(target_width, x1 + overlap)
            crop_y1 = min(target_height, y1 + overlap)
            tiles.append(
                {
                    "id": f"tile_r{row + 1:02d}_c{column + 1:02d}",
                    "row": row,
                    "column": column,
                    "crop": rect(crop_x0, crop_y0, crop_x1 - crop_x0, crop_y1 - crop_y0),
                    "core": rect(x0, y0, x1 - x0, y1 - y0),
                    "native_width": None,
                    "native_height": None,
                    "path": tile_filename(row, column, rows),
                }
            )
    return tiles


def max_crop_dimensions(tiles: list[dict[str, Any]]) -> tuple[int, int]:
    if not tiles:
        return 0, 0
    return (
        max(tile["crop"]["width"] for tile in tiles),
        max(tile["crop"]["height"] for tile in tiles),
    )


def target_from_args(args: argparse.Namespace) -> tuple[str, float | None, int, int]:
    if args.scale is not None and (args.target_width is not None or args.target_height is not None):
        raise ValueError("do not combine --scale with explicit target dimensions")
    if args.long_edge is not None and (args.target_width is not None or args.target_height is not None):
        raise ValueError("do not combine --long-edge with explicit target dimensions")
    if args.scale is not None:
        if args.scale <= 0:
            raise ValueError("scale must be greater than zero")
        return "scale", args.scale, rounded(args.input_width * args.scale), rounded(args.input_height * args.scale)

    if args.long_edge is not None:
        if args.long_edge < 1:
            raise ValueError("long edge must be positive")
        if args.input_width >= args.input_height:
            return "long_edge", None, args.long_edge, rounded(args.input_height * args.long_edge / args.input_width)
        return "long_edge", None, rounded(args.input_width * args.long_edge / args.input_height), args.long_edge

    if args.target_width is None or args.target_height is None:
        raise ValueError("explicit sizing requires both --target-width and --target-height")
    if args.target_width < 1 or args.target_height < 1:
        raise ValueError("target dimensions must be positive")
    return "explicit", None, args.target_width, args.target_height


def plan(args: argparse.Namespace) -> dict[str, Any]:
    mode, scale, target_width, target_height = target_from_args(args)
    native_width = args.native_tile_width
    native_height = args.native_tile_height
    has_native = native_width is not None and native_height is not None

    if (native_width is None) != (native_height is None):
        raise ValueError("--native-tile-width and --native-tile-height must be supplied together")
    if has_native and (native_width < 1 or native_height < 1):
        raise ValueError("native tile dimensions must be positive")

    if args.overlap_px is not None:
        if args.overlap_px < 0:
            raise ValueError("overlap must not be negative")
        overlap = args.overlap_px
    elif has_native:
        overlap = max(1, min(512, rounded(min(native_width, native_height) * 0.08)))
    else:
        # This is only a provisional value. It is replaced after probing.
        overlap = 128

    fixed_grid = parse_grid(args.grid) if args.grid else None
    reason: str | None = None
    status = "needs_probe" if not has_native else "planned"

    if fixed_grid:
        columns, rows = fixed_grid
    elif has_native:
        core_width = native_width - 2 * overlap
        core_height = native_height - 2 * overlap
        if core_width < 1 or core_height < 1:
            columns, rows = 0, 0
            status = "insufficient_native"
            reason = "overlap leaves no positive core area in the native tile"
        else:
            columns = max(1, math.ceil(target_width / core_width))
            rows = max(1, math.ceil(target_height / core_height))
    else:
        columns, rows = None, None

    tiles: list[dict[str, Any]] = []
    if columns and rows:
        tiles = build_tiles(target_width, target_height, columns, rows, overlap)
        if has_native:
            for tile in tiles:
                tile["native_width"] = native_width
                tile["native_height"] = native_height

    max_crop_width, max_crop_height = max_crop_dimensions(tiles)
    if has_native and tiles and status != "insufficient_native":
        if max_crop_width > native_width or max_crop_height > native_height:
            status = "insufficient_native"
            reason = (
                f"largest crop is {max_crop_width}x{max_crop_height}, "
                f"but native output is {native_width}x{native_height}"
            )

    tile_count = len(tiles)
    tile_raw_bytes = sum(tile["crop"]["width"] * tile["crop"]["height"] * 4 for tile in tiles)
    final_raw_bytes = target_width * target_height * 4
    needs_confirmation = tile_count > 16
    if status == "insufficient_native":
        needs_confirmation = False

    return {
        "request": {
            "raw_text": args.raw_request or "",
            "mode": mode,
            "scale": scale,
            "long_edge": args.long_edge if mode == "long_edge" else None,
            "source_width": args.input_width,
            "source_height": args.input_height,
            "target_width": target_width,
            "target_height": target_height,
            "explicit_target": mode == "explicit",
            "aspect_ratio_changed": target_width * args.input_height != target_height * args.input_width,
        },
        "grid": {
            "columns": columns,
            "rows": rows,
            "tile_count": tile_count,
            "overlap_px": overlap,
            "overlap_source": "explicit" if args.overlap_px is not None else "auto",
            "native_tile_width": native_width,
            "native_tile_height": native_height,
            "max_crop_width": max_crop_width,
            "max_crop_height": max_crop_height,
            "grid_selection": "fixed" if fixed_grid else ("auto" if has_native else "pending_probe"),
            "coordinate_origin": "top-left",
            "axis_direction": "x-right,y-down",
            "alpha_policy": "preserve_source_alpha",
            "stitch_mode": "direct",
            "resized": False,
            "blended": False,
            "status": status,
            "needs_confirmation": needs_confirmation,
        },
        "planning": {
            "probe_required": not has_native,
            "estimated_generation_calls": (1 if not has_native else 0) + tile_count,
            "estimated_tile_raw_bytes": tile_raw_bytes,
            "estimated_final_raw_bytes": final_raw_bytes,
            "estimated_tile_raw_mib": round(tile_raw_bytes / (1024 * 1024), 2),
            "estimated_final_raw_mib": round(final_raw_bytes / (1024 * 1024), 2),
            "reason": reason,
        },
        "tiles": tiles,
        "quality": {
            "native_required": True,
            "native_dimensions_recorded": has_native,
            "resized": False,
            "blended": False,
            "format": args.format,
            "status": status,
        },
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-width", type=int, required=True)
    parser.add_argument("--input-height", type=int, required=True)
    target = parser.add_mutually_exclusive_group()
    target.add_argument("--scale", type=float)
    target.add_argument("--long-edge", type=int, help="target long edge, e.g. 4096, 8192, or 16384")
    target.add_argument("--target-width", "--width", dest="target_width", type=int)
    parser.add_argument("--target-height", "--height", dest="target_height", type=int)
    parser.add_argument("--native-tile-width", type=int)
    parser.add_argument("--native-tile-height", type=int)
    parser.add_argument("--grid", help="fixed columns x rows, for example 4x2")
    parser.add_argument("--overlap-px", type=int)
    parser.add_argument("--raw-request", default="")
    parser.add_argument("--format", choices=("png",), default="png")
    parser.add_argument("--output", default="-", help="JSON output path, or - for stdout")
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        if args.input_width < 1 or args.input_height < 1:
            raise ValueError("input dimensions must be positive")
        if args.scale is None and args.long_edge is None and args.target_width is None:
            raise ValueError("provide --scale, --long-edge, or --target-width/--target-height")
        result = plan(args)
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
