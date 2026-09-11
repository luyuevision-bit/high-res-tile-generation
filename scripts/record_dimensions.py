#!/usr/bin/env python3
"""Record the actual native dimensions of every generated tile in a manifest."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
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
        raise ValueError(f"cannot read dimensions for {path}: {detail.strip()}") from error
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


def has_alpha(path: str) -> bool:
    try:
        result = subprocess.run(
            ["sips", "-g", "hasAlpha", path],
            check=True,
            capture_output=True,
            text=True,
        )
    except (FileNotFoundError, subprocess.CalledProcessError) as error:
        detail = getattr(error, "stderr", "") or str(error)
        raise ValueError(f"cannot read alpha information for {path}: {detail.strip()}") from error
    for line in result.stdout.splitlines():
        if line.strip().startswith("hasAlpha:"):
            value = line.split(":", 1)[1].strip().lower()
            if value in {"yes", "true"}:
                return True
            if value in {"no", "false"}:
                return False
    raise ValueError(f"sips did not return alpha information for {path}")


def resolve_tile_path(tile: dict[str, Any], tiles_dir: str) -> str:
    path = tile.get("path") or f"{tile.get('id', 'tile')}.png"
    return path if os.path.isabs(path) else os.path.join(tiles_dir, path)


def record(manifest: dict[str, Any], tiles_dir: str) -> dict[str, Any]:
    dimensions: list[tuple[int, int]] = []
    alpha_values: list[bool] = []
    insufficient_tiles: list[str] = []
    for tile in manifest.get("tiles", []):
        path = resolve_tile_path(tile, tiles_dir)
        if not os.path.isfile(path):
            raise ValueError(f"missing tile file: {path}")
        width, height = image_size(path)
        alpha = has_alpha(path)
        tile["native_width"] = width
        tile["native_height"] = height
        tile["has_alpha"] = alpha
        dimensions.append((width, height))
        alpha_values.append(alpha)
        crop = tile.get("crop", {})
        if width < int(crop.get("width", 0)) or height < int(crop.get("height", 0)):
            insufficient_tiles.append(tile.get("id", "<unknown>"))

    unique_dimensions = sorted(set(dimensions))
    grid = manifest.setdefault("grid", {})
    if insufficient_tiles:
        grid["status"] = "insufficient_native"
        manifest.setdefault("planning", {})["reason"] = (
            "native output is smaller than crop for: " + ", ".join(insufficient_tiles)
        )
    grid["native_dimensions_vary"] = len(unique_dimensions) > 1
    if len(unique_dimensions) == 1:
        grid["native_tile_width"], grid["native_tile_height"] = unique_dimensions[0]
    else:
        # A single top-level size would be misleading when the generator varies
        # output dimensions. Per-tile native_* fields remain authoritative.
        grid["native_tile_width"] = None
        grid["native_tile_height"] = None
    quality = manifest.setdefault("quality", {})
    quality["native_dimensions_recorded"] = True
    quality["resized"] = False
    quality["blended"] = False
    quality["native_size_ok"] = not insufficient_tiles
    quality["status"] = "insufficient_native" if insufficient_tiles else quality.get("status", "planned")
    alpha_status = "mixed" if len(set(alpha_values)) > 1 else ("present" if alpha_values and alpha_values[0] else "absent")
    manifest["alpha"] = {"status": alpha_status, "per_tile_recorded": True}
    quality["alpha_recorded"] = True
    quality["alpha_status"] = alpha_status
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--tiles-dir", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    try:
        with open(args.manifest, encoding="utf-8") as handle:
            manifest = json.load(handle)
        result = record(manifest, args.tiles_dir)
        with open(args.output, "w", encoding="utf-8") as handle:
            json.dump(result, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
    except (OSError, json.JSONDecodeError, ValueError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print(f"Recorded native dimensions for {len(result.get('tiles', []))} tiles: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
