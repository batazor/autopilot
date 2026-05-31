import AppKit
import Foundation
import ScreenCaptureKit

struct CaptureRequest: Decodable {
    let instance_id: String
    let window_id: UInt32?
    let window_title: String?
    let crop: [Double]?
    let target_width: Int
    let target_height: Int
}

struct ErrorResponse: Encodable {
    let ok: Bool
    let error: String
}

struct CaptureResponse: Encodable {
    let ok: Bool
    let format: String
    let width: Int
    let height: Int
    let bytes: Int
    let window_id: UInt32
}

@main
struct Main {
    static var windowCache: [String: SCWindow] = [:]

    static func main() async {
        _ = NSApplication.shared
        let stdin = FileHandle.standardInput
        while let line = readLine() {
            guard !line.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else {
                continue
            }
            do {
                let data = Data(line.utf8)
                let request = try JSONDecoder().decode(CaptureRequest.self, from: data)
                let (response, pixels) = try await capture(request)
                try writeJSONLine(response)
                FileHandle.standardOutput.write(pixels)
                FileHandle.standardOutput.write(Data([0x0A]))
            } catch {
                let response = ErrorResponse(ok: false, error: String(describing: error))
                try? writeJSONLine(response)
            }
        }
        _ = stdin
    }

    static func capture(_ request: CaptureRequest) async throws -> (CaptureResponse, Data) {
        let window = try await resolveWindow(request)
        let filter = SCContentFilter(desktopIndependentWindow: window)
        let config = SCStreamConfiguration()
        config.width = request.target_width
        config.height = request.target_height
        config.showsCursor = false
        config.capturesAudio = false
        let bg = CGColor(red: 0, green: 0, blue: 0, alpha: 1)
        config.backgroundColor = bg
        config.sourceRect = sourceRect(for: window, request: request)

        let image = try await SCScreenshotManager.captureImage(contentFilter: filter, configuration: config)
        // Send raw BGRA pixels (no padding): the Python side reshapes the buffer
        // straight into a numpy array, skipping a PNG encode here and a PNG
        // decode there — the per-frame hot path for emulator capture.
        guard let pixels = bgraData(from: image) else {
            throw RuntimeError("failed to extract BGRA pixels")
        }
        return (
            CaptureResponse(
                ok: true,
                format: "bgra",
                width: image.width,
                height: image.height,
                bytes: pixels.count,
                window_id: window.windowID
            ),
            pixels
        )
    }

    static func resolveWindow(_ request: CaptureRequest) async throws -> SCWindow {
        let key = cacheKey(for: request)
        if let cached = windowCache[key] {
            return cached
        }
        let content = try await SCShareableContent.excludingDesktopWindows(false, onScreenWindowsOnly: false)
        let window = try pickWindow(from: content.windows, request: request)
        windowCache[key] = window
        return window
    }

    static func cacheKey(for request: CaptureRequest) -> String {
        if let windowID = request.window_id {
            return "id:\(windowID)"
        }
        let title = (request.window_title ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
        return "instance:\(request.instance_id):title:\(title)"
    }

    static func pickWindow(from windows: [SCWindow], request: CaptureRequest) throws -> SCWindow {
        if let windowID = request.window_id {
            if let match = windows.first(where: { $0.windowID == windowID }) {
                return match
            }
            throw RuntimeError("ScreenCaptureKit window_id \(windowID) not found")
        }

        let visible = windows.filter { window in
            window.frame.width >= 300 && window.frame.height >= 300
        }
        let titleHint = (request.window_title ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
        if !titleHint.isEmpty {
            let lowerHint = titleHint.lowercased()
            let matches = visible.filter { window in
                let app = window.owningApplication?.applicationName.lowercased() ?? ""
                let title = window.title?.lowercased() ?? ""
                return app.contains(lowerHint) || title.contains(lowerHint)
            }
            if let match = largest(matches) {
                return match
            }
            throw RuntimeError("ScreenCaptureKit window_title '\(titleHint)' not found")
        }

        // Real BlueStacks instance windows carry a non-empty title ("BlueStacks
        // Air N"); the empty-title rows are toolbar/chrome strips. Prefer titled
        // instance windows so we never guess a chrome strip.
        let instanceWindows = visible.filter { window in
            let app = window.owningApplication?.applicationName.lowercased() ?? ""
            let title = window.title ?? ""
            return app.contains("bluestacks")
                && !title.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
                && !title.lowercased().contains("keymap")
        }
        if instanceWindows.count > 1 {
            // Multiple instances are open and no explicit title/id was given.
            // Guessing the largest would silently make multiple devices share
            // one window — fail loudly instead.
            let titles = instanceWindows.compactMap { $0.title }.sorted().joined(separator: ", ")
            throw RuntimeError(
                "cannot resolve ScreenCaptureKit window for instance '\(request.instance_id)': "
                    + "\(instanceWindows.count) BlueStacks windows are open (\(titles)). "
                    + "Set quartz_window_title or quartz_window_id for this device."
            )
        }
        if let match = instanceWindows.first {
            return match
        }

        let matches = visible.filter { window in
            let app = window.owningApplication?.applicationName.lowercased() ?? ""
            let title = window.title?.lowercased() ?? ""
            return app.contains("bluestacks") && !title.contains("keymap")
        }
        if let match = largest(matches) {
            return match
        }
        throw RuntimeError("no BlueStacks ScreenCaptureKit window found")
    }

    static func largest(_ windows: [SCWindow]) -> SCWindow? {
        windows.max { lhs, rhs in
            (lhs.frame.width * lhs.frame.height) < (rhs.frame.width * rhs.frame.height)
        }
    }

    static func sourceRect(for window: SCWindow, request: CaptureRequest) -> CGRect {
        if let crop = request.crop, crop.count == 4 {
            let scale = backingScaleFactor(for: window)
            return CGRect(
                x: crop[0] / scale,
                y: crop[1] / scale,
                width: crop[2] / scale,
                height: crop[3] / scale
            )
        }

        let targetRatio = Double(request.target_width) / Double(request.target_height)
        let topChrome = min(32.5, max(0.0, window.frame.height - 1.0))
        var h = max(1.0, window.frame.height - topChrome)
        var w = h * targetRatio
        var y = topChrome
        if w > window.frame.width {
            w = window.frame.width
            h = w / targetRatio
            y = max(0.0, window.frame.height - h)
        }
        return CGRect(x: 0, y: y, width: w, height: h)
    }

    static func backingScaleFactor(for window: SCWindow) -> Double {
        for screen in NSScreen.screens {
            if screen.frame.intersects(window.frame) {
                return Double(screen.backingScaleFactor)
            }
        }
        return Double(NSScreen.main?.backingScaleFactor ?? 2.0)
    }

    static func bgraData(from image: CGImage) -> Data? {
        let width = image.width
        let height = image.height
        let bytesPerRow = width * 4
        guard width > 0, height > 0 else {
            return nil
        }
        let colorSpace = CGColorSpaceCreateDeviceRGB()
        // premultipliedFirst + byteOrder32Little lays the bytes out as B, G, R, A
        // in memory — exactly what OpenCV expects once we drop the alpha column.
        // Frames are opaque (alpha == 255) so premultiplied == straight.
        let bitmapInfo = CGImageAlphaInfo.premultipliedFirst.rawValue
            | CGBitmapInfo.byteOrder32Little.rawValue
        guard let ctx = CGContext(
            data: nil,
            width: width,
            height: height,
            bitsPerComponent: 8,
            bytesPerRow: bytesPerRow,
            space: colorSpace,
            bitmapInfo: bitmapInfo
        ) else {
            return nil
        }
        ctx.draw(image, in: CGRect(x: 0, y: 0, width: width, height: height))
        guard let buffer = ctx.data else {
            return nil
        }
        return Data(bytes: buffer, count: bytesPerRow * height)
    }

    static func writeJSONLine<T: Encodable>(_ value: T) throws {
        let data = try JSONEncoder().encode(value)
        FileHandle.standardOutput.write(data)
        FileHandle.standardOutput.write(Data([0x0A]))
    }
}

struct RuntimeError: Error, CustomStringConvertible {
    let description: String

    init(_ description: String) {
        self.description = description
    }
}
