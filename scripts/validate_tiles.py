#!/usr/bin/env python3
"""Validate a completed native-size tile manifest and optional final PNG."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from collections import defaultdict
from typing import Any


def image_size(path: str) -> tuple[int, int]:
    try:
        result = subprocess.run(
            ["sips", "-g", "pixelWidth", "-g", "pixelHeight", path],
            check=True,
            capture_output=True,
            text=True,
        )
    except (FileNotFoundError, subprocess.CalledProcessError) as error:
        detail = getattr(error, "stderr", "") or str(error)
        raise ValueError(f"cannot read image dimensions for {path}: {detail.strip()}") from error

    values: dict[str, int] = {}
    for line in result.stdout.splitlines():
        if ":" not in line:
            continue
        key, value = (part.strip() for part in line.split(":", 1))
        if key in {"pixelWidth", "pixelHeight"}:
            values[key] = int(value)
    if set(values) != {"pixelWidth", "pixelHeight"}:
        raise ValueError(f"sips did not return dimensions for {path}")
    return values["pixelWidth"], values["pixelHeight"]


def mime_type(path: str) -> str:
    try:
        result = subprocess.run(
            ["file", "--brief", "--mime-type", path],
            check=True,
            capture_output=True,
            text=True,
        )
    except (FileNotFoundError, subprocess.CalledProcessError) as error:
        detail = getattr(error, "stderr", "") or str(error)
        raise ValueError(f"cannot read MIME type for {path}: {detail.strip()}") from error
    return result.stdout.strip().lower()


def core_rect(tile: dict[str, Any]) -> tuple[int, int, int, int]:
    core = tile["core"]
    return core["x"], core["y"], core["width"], core["height"]


def validate_coverage(tiles: list[dict[str, Any]], columns: int, rows: int, width: int, height: int) -> list[str]:
    issues: list[str] = []
    by_row: dict[int, list[dict[str, Any]]] = defaultdict(list)
    by_column: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for tile in tiles:
        by_row[tile["row"]].append(tile)
        by_column[tile["column"]].append(tile)

    for row in range(rows):
        row_tiles = sorted(by_row[row], key=lambda tile: tile["core"]["x"])
        if len(row_tiles) != columns:
            issues.append(f"row {row} has {len(row_tiles)} tiles, expected {columns}")
            continue
        cursor = 0
        y_range: tuple[int, int] | None = None
        for tile in row_tiles:
            x, y, tile_width, tile_height = core_rect(tile)
            if x != cursor:
                issues.append(f"horizontal gap/overlap before {tile['id']} in row {row}")
            if y_range is None:
                y_range = (y, y + tile_height)
            elif y_range != (y, y + tile_height):
                issues.append(f"inconsistent y range in row {row}")
            cursor = x + tile_width
        if cursor != width:
            issues.append(f"row {row} ends at x={cursor}, expected {width}")

    for column in range(columns):
        column_tiles = sorted(by_column[column], key=lambda tile: tile["core"]["y"])
        if len(column_tiles) != rows:
            issues.append(f"column {column} has {len(column_tiles)} tiles, expected {rows}")
            continue
        cursor = 0
        x_range: tuple[int, int] | None = None
        for tile in column_tiles:
            x, y, tile_width, tile_height = core_rect(tile)
            if y != cursor:
                issues.append(f"vertical gap/overlap before {tile['id']} in column {column}")
            if x_range is None:
                x_range = (x, x + tile_width)
            elif x_range != (x, x + tile_width):
                issues.append(f"inconsistent x range in column {column}")
            cursor = y + tile_height
        if cursor != height:
            issues.append(f"column {column} ends at y={cursor}, expected {height}")
    return issues


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--tiles-dir", required=True)
    parser.add_argument("--final")
    parser.add_argument("--json-out")
    parser.add_argument("--visual-seam", choices=("passed", "failed", "not_reviewed"), default="not_reviewed")
    parser.add_argument("--visual-note", default="")
    args = parser.parse_args()

    issues: list[str] = []
    warnings: list[str] = []
    try:
        with open(args.manifest, encoding="utf-8") as handle:
            manifest = json.load(handle)
    except (OSError, json.JSONDecodeError) as error:
        print(f"ERROR: cannot read manifest: {error}", file=sys.stderr)
        return 1

    request = manifest.get("request", {})
    grid = manifest.get("grid", {})
    tiles = manifest.get("tiles", [])
    target_width = request.get("target_width")
    target_height = request.get("target_height")
    columns = grid.get("columns")
    rows = grid.get("rows")

    if not isinstance(target_width, int) or not isinstance(target_height, int) or target_width < 1 or target_height < 1:
        issues.append("manifest has invalid target dimensions")
    if not isinstance(columns, int) or not isinstance(rows, int) or columns < 1 or rows < 1:
        issues.append("manifest has no completed native grid")
    if grid.get("stitch_mode") != "direct":
        issues.append("stitch_mode must be direct")
    if grid.get("resized") is not False:
        issues.append("resized must be false")
    if grid.get("blended") is not False:
        issues.append("blended must be false")

    expected_count = columns * rows if isinstance(columns, int) and isinstance(rows, int) else None
    if expected_count is not None and len(tiles) != expected_count:
        issues.append(f"manifest has {len(tiles)} tiles, expected {expected_count}")

    native_dimensions: list[dict[str, Any]] = []
    seen_positions: set[tuple[int, int]] = set()
    geometry_ok = True
    for tile in tiles:
        tile_id = tile.get("id", "<unknown>")
        path_value = tile.get("path") or f"{tile_id}.png"
        tile_path = path_value if os.path.isabs(path_value) else os.path.join(args.tiles_dir, path_value)
        if not os.path.isfile(tile_path):
            issues.append(f"missing tile file: {tile_path}")
            continue
        if os.path.splitext(tile_path)[1].lower() != ".png":
            issues.append(f"tile is not a PNG path: {tile_path}")
        try:
            actual_width, actual_height = image_size(tile_path)
            actual_mime = mime_type(tile_path)
        except ValueError as error:
            issues.append(str(error))
            continue
        if actual_mime != "image/png":
            issues.append(f"tile is not image/png: {tile_path} ({actual_mime})")

        crop = tile.get("crop", {})
        core = tile.get("core", {})
        try:
            crop_width = int(crop["width"])
            crop_height = int(crop["height"])
            core_x = int(core["x"])
            core_y = int(core["y"])
            core_width = int(core["width"])
            core_height = int(core["height"])
            crop_x = int(crop["x"])
            crop_y = int(crop["y"])
        except (KeyError, TypeError, ValueError):
            issues.append(f"tile {tile_id} has invalid crop/core")
            geometry_ok = False
            continue

        if actual_width < crop_width or actual_height < crop_height:
            issues.append(
                f"tile {tile_id} is {actual_width}x{actual_height}, smaller than crop {crop_width}x{crop_height}; no resize allowed"
            )
        declared_width = tile.get("native_width")
        declared_height = tile.get("native_height")
        if not isinstance(declared_width, int) or not isinstance(declared_height, int):
            issues.append(f"tile {tile_id} is missing native dimensions")
        elif (declared_width, declared_height) != (actual_width, actual_height):
            issues.append(
                f"tile {tile_id} native manifest size {declared_width}x{declared_height} differs from actual {actual_width}x{actual_height}"
            )
        if crop_x > core_x or crop_y > core_y or crop_x + crop_width < core_x + core_width or crop_y + crop_height < core_y + core_height:
            issues.append(f"tile {tile_id} crop does not contain core")
        if isinstance(target_width, int) and isinstance(target_height, int):
            if core_x < 0 or core_y < 0 or core_x + core_width > target_width or core_y + core_height > target_height:
                issues.append(f"tile {tile_id} core is outside target canvas")

        position = (tile.get("row"), tile.get("column"))
        if position in seen_positions:
            issues.append(f"duplicate tile position: {position}")
        seen_positions.add(position)
        native_dimensions.append(
            {
                "id": tile_id,
                "row": tile.get("row"),
                "column": tile.get("column"),
                "width": actual_width,
                "height": actual_height,
                "path": tile_path,
            }
        )

    coverage_issues: list[str] = []
    if geometry_ok and isinstance(columns, int) and isinstance(rows, int) and isinstance(target_width, int) and isinstance(target_height, int):
        coverage_issues = validate_coverage(tiles, columns, rows, target_width, target_height)
        issues.extend(coverage_issues)

    final_size: dict[str, int] | None = None
    final_mime: str | None = None
    if args.final:
        if not os.path.isfile(args.final):
            issues.append(f"missing final file: {args.final}")
        else:
            try:
                final_width, final_height = image_size(args.final)
                final_mime = mime_type(args.final)
                final_size = {"width": final_width, "height": final_height}
                if isinstance(target_width, int) and isinstance(target_height, int) and (final_width, final_height) != (target_width, target_height):
                    issues.append(
                        f"final is {final_width}x{final_height}, expected {target_width}x{target_height}; no final resize allowed"
                    )
                if final_mime != "image/png":
                    issues.append(f"final is not image/png: {final_mime}")
            except ValueError as error:
                issues.append(str(error))
    else:
        warnings.append("final image was not supplied; final-size and final-format checks were skipped")

    if args.visual_seam == "failed":
        issues.append(f"visual seam check failed: {args.visual_note or 'visible discontinuity requires tile regeneration'}")
    elif args.visual_seam == "not_reviewed":
        warnings.append("visual seam check was not reviewed; inspect line, contour, and color continuity")

    tile_count_ok = expected_count is None or len(tiles) == expected_count
    native_size_ok = not any("native" in issue or "smaller than crop" in issue for issue in issues)
    format_ok = not any("PNG" in issue or "image/png" in issue for issue in issues)
    final_size_check: bool | str = "not_requested"
    if args.final:
        final_size_check = final_size is not None and not any("final is" in issue for issue in issues)

    if issues:
        status = "failed"
    elif args.visual_seam == "not_reviewed":
        status = "passed_structural_visual_review_pending"
    else:
        status = "passed"

    result = {
        "status": status,
        "target_size": {"width": target_width, "height": target_height},
        "final_size": final_size,
        "tile_count": len(tiles),
        "native_dimensions": native_dimensions,
        "checks": {
            "tile_count": tile_count_ok,
            "native_size": native_size_ok,
            "coordinate_coverage": geometry_ok and not coverage_issues,
            "format": format_ok,
            "resized": grid.get("resized") is False,
            "blended": grid.get("blended") is False,
            "final_size": final_size_check,
            "visual_seam": args.visual_seam,
        },
        "resized": False,
        "blended": False,
        "visual_seam_check": args.visual_seam,
        "visual_seam_note": args.visual_note,
        "warnings": warnings,
        "issues": issues,
    }
    encoded = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8") as handle:
            handle.write(encoded)
    print(encoded, end="")
    return 0 if not issues else 1


if __name__ == "__main__":
    raise SystemExit(main())
