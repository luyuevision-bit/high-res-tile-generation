#!/usr/bin/env python3
"""Read one real probe image and re-plan a manifest around its native size.

Image generation itself stays with the agent and image-generation tool. This
helper is deliberately deterministic: it only measures the returned file and
reuses the original request to produce a new grid.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from typing import Any


SCRIPT_DIRECTORY = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIRECTORY not in sys.path:
    sys.path.insert(0, SCRIPT_DIRECTORY)

import plan_tiles  # noqa: E402  (local deterministic planner)


def read_dimensions(path: str) -> tuple[int, int]:
    if not os.path.isfile(path):
        raise ValueError(f"probe file does not exist: {path}")
    try:
        result = subprocess.run(
            ["sips", "-g", "pixelWidth", "-g", "pixelHeight", path],
            check=True,
            capture_output=True,
            text=True,
        )
    except (FileNotFoundError, subprocess.CalledProcessError) as error:
        detail = getattr(error, "stderr", "") or str(error)
        raise ValueError(f"cannot read probe dimensions: {detail.strip()}") from error

    values: dict[str, int] = {}
    for line in result.stdout.splitlines():
        if ":" not in line:
            continue
        key, value = (part.strip() for part in line.split(":", 1))
        if key in {"pixelWidth", "pixelHeight"}:
            values[key] = int(value)
    if set(values) != {"pixelWidth", "pixelHeight"}:
        raise ValueError(f"sips did not return both dimensions for {path}")
    return values["pixelWidth"], values["pixelHeight"]


def request_args(manifest: dict[str, Any], native_width: int, native_height: int) -> argparse.Namespace:
    request = manifest.get("request", {})
    grid = manifest.get("grid", {})
    mode = request.get("mode")
    if mode not in {"scale", "long_edge", "explicit"}:
        raise ValueError(f"unsupported manifest request mode: {mode!r}")

    scale = request.get("scale") if mode == "scale" else None
    long_edge = request.get("long_edge") if mode == "long_edge" else None
    target_width = request.get("target_width") if mode == "explicit" else None
    target_height = request.get("target_height") if mode == "explicit" else None
    if not isinstance(request.get("source_width"), int) or not isinstance(request.get("source_height"), int):
        raise ValueError("manifest request is missing source dimensions")

    columns = grid.get("columns")
    rows = grid.get("rows")
    fixed_grid = (
        f"{columns}x{rows}"
        if grid.get("grid_selection") == "fixed" and isinstance(columns, int) and isinstance(rows, int)
        else None
    )
    overlap = grid.get("overlap_px") if grid.get("overlap_source") == "explicit" else None
    return argparse.Namespace(
        input_width=request["source_width"],
        input_height=request["source_height"],
        scale=scale,
        long_edge=long_edge,
        target_width=target_width,
        target_height=target_height,
        native_tile_width=native_width,
        native_tile_height=native_height,
        grid=fixed_grid,
        overlap_px=overlap,
        raw_request=request.get("raw_text", ""),
        format="png",
        output="-",
    )


def replan(manifest: dict[str, Any], probe_path: str) -> dict[str, Any]:
    native_width, native_height = read_dimensions(probe_path)
    args = request_args(manifest, native_width, native_height)
    result = plan_tiles.plan(args)
    result["probe"] = {
        "path": os.path.abspath(probe_path),
        "native_width": native_width,
        "native_height": native_height,
        "measured_by": "sips",
    }
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--image", required=True, help="one real generator output used as the capability probe")
    parser.add_argument("--output", default="-", help="replanned manifest path, or - for stdout")
    args = parser.parse_args()
    try:
        with open(args.manifest, encoding="utf-8") as handle:
            manifest = json.load(handle)
        result = replan(manifest, args.image)
    except (OSError, json.JSONDecodeError, ValueError) as error:
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
