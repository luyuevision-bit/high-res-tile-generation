#!/usr/bin/env swift

import CoreGraphics
import Darwin
import Foundation
import ImageIO
import UniformTypeIdentifiers

struct Rectangle: Codable {
    let x: Int
    let y: Int
    let width: Int
    let height: Int

    var maxX: Int { x + width }
    var maxY: Int { y + height }
}

struct Request: Codable {
    let targetWidth: Int
    let targetHeight: Int
}

struct Grid: Codable {
    let columns: Int?
    let rows: Int?
    let overlapPx: Int?
    let stitchMode: String?
    let resized: Bool?
    let blended: Bool?
}

struct Tile: Codable {
    let id: String
    let row: Int
    let column: Int
    let crop: Rectangle
    let core: Rectangle
    let nativeWidth: Int?
    let nativeHeight: Int?
    let path: String?
}

struct Manifest: Codable {
    let request: Request
    let grid: Grid
    let tiles: [Tile]
}

struct NativeRecord: Codable {
    let id: String
    let row: Int
    let column: Int
    let path: String
    let nativeWidth: Int
    let nativeHeight: Int
    let alphaInfo: String
}

struct QualityReport: Codable {
    let status: String
    let format: String
    let finalWidth: Int
    let finalHeight: Int
    let tileCount: Int
    let resized: Bool
    let blended: Bool
    let coordinateCoverage: String
    let visualSeamCheck: String
    let visualSeamNote: String
    let nativeDimensions: [NativeRecord]
}

let arguments = Array(CommandLine.arguments.dropFirst())

func option(_ name: String) -> String? {
    guard let index = arguments.firstIndex(of: name), index + 1 < arguments.count else { return nil }
    return arguments[index + 1]
}

func fail(_ message: String) -> Never {
    FileHandle.standardError.write(Data("ERROR: \(message)\n".utf8))
    exit(1)
}

guard let manifestPath = option("--manifest") else { fail("missing --manifest") }
guard let tilesDirectoryPath = option("--tiles-dir") else { fail("missing --tiles-dir") }
guard let outputPath = option("--output") else { fail("missing --output") }

let reportPath = option("--report") ?? {
    let outputURL = URL(fileURLWithPath: outputPath)
    return outputURL.deletingPathExtension().path + ".quality-report.json"
}()

let manifestURL = URL(fileURLWithPath: manifestPath)
let tilesDirectoryURL = URL(fileURLWithPath: tilesDirectoryPath)
let outputURL = URL(fileURLWithPath: outputPath)

let manifestData: Data
do {
    manifestData = try Data(contentsOf: manifestURL)
} catch {
    fail("cannot read manifest: \(error.localizedDescription)")
}

let decoder = JSONDecoder()
decoder.keyDecodingStrategy = .convertFromSnakeCase
let manifest: Manifest
do {
    manifest = try decoder.decode(Manifest.self, from: manifestData)
} catch {
    fail("invalid manifest: \(error.localizedDescription)")
}

guard let columns = manifest.grid.columns, let rows = manifest.grid.rows,
      columns > 0, rows > 0 else {
    fail("manifest has no planned grid; run a native capability probe first")
}
guard manifest.grid.stitchMode == "direct" else { fail("only direct stitch_mode is supported") }
guard manifest.grid.resized != true, manifest.grid.blended != true else {
    fail("manifest must explicitly set resized=false and blended=false")
}
guard manifest.request.targetWidth > 0, manifest.request.targetHeight > 0 else {
    fail("target dimensions must be positive")
}
guard manifest.tiles.count == columns * rows else {
    fail("manifest has \(manifest.tiles.count) tiles but grid requires \(columns * rows)")
}

let targetWidth = manifest.request.targetWidth
let targetHeight = manifest.request.targetHeight

func validateRectangle(_ rectangle: Rectangle, name: String) {
    guard rectangle.x >= 0, rectangle.y >= 0, rectangle.width > 0, rectangle.height > 0,
          rectangle.maxX <= targetWidth, rectangle.maxY <= targetHeight else {
        fail("\(name) is outside the final canvas: \(rectangle)")
    }
}

var seenPositions = Set<String>()
for tile in manifest.tiles {
    guard tile.row >= 0, tile.row < rows, tile.column >= 0, tile.column < columns else {
        fail("tile \(tile.id) has an invalid row/column")
    }
    let positionKey = "\(tile.row):\(tile.column)"
    guard seenPositions.insert(positionKey).inserted else { fail("duplicate tile position \(positionKey)") }
    validateRectangle(tile.core, name: "core of \(tile.id)")
    validateRectangle(tile.crop, name: "crop of \(tile.id)")
    guard tile.crop.x <= tile.core.x, tile.crop.y <= tile.core.y,
          tile.crop.maxX >= tile.core.maxX, tile.crop.maxY >= tile.core.maxY else {
        fail("crop of \(tile.id) does not contain its core")
    }
    guard let nativeWidth = tile.nativeWidth, let nativeHeight = tile.nativeHeight,
          nativeWidth > 0, nativeHeight > 0 else {
        fail("tile \(tile.id) is missing recorded native dimensions")
    }
}

// A grid is valid only when every row and every column forms one exact cover.
for row in 0..<rows {
    let rowTiles = manifest.tiles.filter { $0.row == row }.sorted { $0.core.x < $1.core.x }
    guard rowTiles.count == columns else { fail("row \(row) does not contain \(columns) tiles") }
    var cursor = 0
    var rowY: Int?
    var rowBottom: Int?
    for tile in rowTiles {
        guard tile.core.x == cursor else { fail("horizontal gap or overlap before \(tile.id)") }
        if let rowY, tile.core.y != rowY { fail("row \(row) has inconsistent y coordinates") }
        if let rowBottom, tile.core.maxY != rowBottom { fail("row \(row) has inconsistent height") }
        rowY = tile.core.y
        rowBottom = tile.core.maxY
        cursor = tile.core.maxX
    }
    guard cursor == targetWidth, rowY != nil, rowBottom != nil else {
        fail("row \(row) does not cover the full width")
    }
}

for column in 0..<columns {
    let columnTiles = manifest.tiles.filter { $0.column == column }.sorted { $0.core.y < $1.core.y }
    guard columnTiles.count == rows else { fail("column \(column) does not contain \(rows) tiles") }
    var cursor = 0
    var columnX: Int?
    var columnRight: Int?
    for tile in columnTiles {
        guard tile.core.y == cursor else { fail("vertical gap or overlap before \(tile.id)") }
        if let columnX, tile.core.x != columnX { fail("column \(column) has inconsistent x coordinates") }
        if let columnRight, tile.core.maxX != columnRight { fail("column \(column) has inconsistent width") }
        columnX = tile.core.x
        columnRight = tile.core.maxX
        cursor = tile.core.maxY
    }
    guard cursor == targetHeight, columnX != nil, columnRight != nil else {
        fail("column \(column) does not cover the full height")
    }
}

func resolvedTileURL(_ tile: Tile) -> URL {
    guard let path = tile.path, !path.isEmpty else {
        return tilesDirectoryURL.appendingPathComponent("\(tile.id).png")
    }
    if path.hasPrefix("/") { return URL(fileURLWithPath: path) }
    return tilesDirectoryURL.appendingPathComponent(path)
}

func loadImage(_ url: URL) -> (CGImage, CGImageSource) {
    guard let source = CGImageSourceCreateWithURL(url as CFURL, nil),
          let image = CGImageSourceCreateImageAtIndex(source, 0, nil) else {
        fail("cannot decode tile at \(url.path)")
    }
    guard let type = CGImageSourceGetType(source),
          let uniformType = UTType(type as String), uniformType.conforms(to: .png) else {
        fail("tile is not PNG: \(url.path)")
    }
    return (image, source)
}

guard let colorSpace = CGColorSpace(name: CGColorSpace.sRGB) else { fail("cannot create sRGB color space") }
let bitmapInfo = CGImageAlphaInfo.premultipliedLast.rawValue | CGBitmapInfo.byteOrder32Big.rawValue
guard let context = CGContext(
    data: nil,
    width: targetWidth,
    height: targetHeight,
    bitsPerComponent: 8,
    bytesPerRow: targetWidth * 4,
    space: colorSpace,
    bitmapInfo: bitmapInfo
) else { fail("cannot allocate final canvas \(targetWidth)x\(targetHeight)") }

context.clear(CGRect(x: 0, y: 0, width: targetWidth, height: targetHeight))
context.interpolationQuality = .none

var nativeRecords: [NativeRecord] = []
for tile in manifest.tiles.sorted(by: { $0.row == $1.row ? $0.column < $1.column : $0.row < $1.row }) {
    let tileURL = resolvedTileURL(tile)
    let (image, _) = loadImage(tileURL)
    guard image.width == tile.nativeWidth, image.height == tile.nativeHeight else {
        fail("native dimensions in manifest do not match \(tileURL.path): manifest \(tile.nativeWidth!)x\(tile.nativeHeight!), actual \(image.width)x\(image.height)")
    }
    guard image.width >= tile.crop.width, image.height >= tile.crop.height else {
        fail("tile \(tile.id) is smaller than its crop; refusing to resize")
    }

    let localCore = CGRect(
        x: tile.core.x - tile.crop.x,
        y: tile.core.y - tile.crop.y,
        width: tile.core.width,
        height: tile.core.height
    )
    guard localCore.minX >= 0, localCore.minY >= 0,
          localCore.maxX <= CGFloat(image.width), localCore.maxY <= CGFloat(image.height),
          let coreImage = image.cropping(to: localCore) else {
        fail("core of \(tile.id) is not inside its decoded image")
    }

    // CoreGraphics uses a bottom-left drawing context; manifest coordinates are
    // top-left image coordinates. No scaling or filtering occurs here.
    let destination = CGRect(
        x: tile.core.x,
        y: targetHeight - tile.core.y - tile.core.height,
        width: tile.core.width,
        height: tile.core.height
    )
    context.draw(coreImage, in: destination)
    nativeRecords.append(
        NativeRecord(
            id: tile.id,
            row: tile.row,
            column: tile.column,
            path: tileURL.path,
            nativeWidth: image.width,
            nativeHeight: image.height,
            alphaInfo: String(describing: image.alphaInfo)
        )
    )
}

guard let finalImage = context.makeImage() else { fail("cannot materialize final image") }
let outputDirectory = outputURL.deletingLastPathComponent()
let reportURL = URL(fileURLWithPath: reportPath)
do {
    try FileManager.default.createDirectory(at: outputDirectory, withIntermediateDirectories: true)
    try FileManager.default.createDirectory(at: reportURL.deletingLastPathComponent(), withIntermediateDirectories: true)
} catch {
    fail("cannot create output directory: \(error.localizedDescription)")
}
guard let destination = CGImageDestinationCreateWithURL(
    outputURL as CFURL,
    UTType.png.identifier as CFString,
    1,
    nil
) else { fail("cannot create PNG destination") }
CGImageDestinationAddImage(destination, finalImage, nil)
guard CGImageDestinationFinalize(destination) else { fail("PNG write failed") }

let report = QualityReport(
    status: "passed_coordinate_checks",
    format: "png",
    finalWidth: targetWidth,
    finalHeight: targetHeight,
    tileCount: manifest.tiles.count,
    resized: false,
    blended: false,
    coordinateCoverage: "passed",
    visualSeamCheck: "agent_review_required",
    visualSeamNote: "Independent generated tiles are not expected to have pixel-identical overlap. Review visible line, contour, and color continuity; retry affected tiles at most twice.",
    nativeDimensions: nativeRecords
)
let encoder = JSONEncoder()
encoder.outputFormatting = [.prettyPrinted, .sortedKeys]
do {
    try encoder.encode(report).write(to: URL(fileURLWithPath: reportPath))
} catch {
    fail("cannot write quality report: \(error.localizedDescription)")
}

print("Wrote lossless direct PNG: \(outputURL.path)")
print("Wrote quality report: \(reportPath)")
