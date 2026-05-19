import AppKit
import Foundation
import ImageIO
import ScreenCaptureKit
import UniformTypeIdentifiers

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
                let (response, png) = try await capture(request)
                try writeJSONLine(response)
                FileHandle.standardOutput.write(png)
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
        guard let png = pngData(from: image) else {
            throw RuntimeError("failed to encode PNG")
        }
        return (
            CaptureResponse(
                ok: true,
                width: image.width,
                height: image.height,
                bytes: png.count,
                window_id: window.windowID
            ),
            png
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

        let airTitle = defaultBlueStacksAirTitle(for: request.instance_id)
        if !airTitle.isEmpty {
            let matches = visible.filter { window in
                (window.owningApplication?.applicationName ?? "") == "BlueStacks"
                    && (window.title ?? "") == airTitle
            }
            if let match = largest(matches) {
                return match
            }
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

    static func defaultBlueStacksAirTitle(for instanceID: String) -> String {
        let lower = instanceID.lowercased()
        guard lower.hasPrefix("bs"), let n = Int(lower.dropFirst(2)), n > 0 else {
            return ""
        }
        return "BlueStacks Air \(n - 1)"
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

    static func pngData(from image: CGImage) -> Data? {
        let data = NSMutableData()
        guard let dest = CGImageDestinationCreateWithData(data, UTType.png.identifier as CFString, 1, nil) else {
            return nil
        }
        CGImageDestinationAddImage(dest, image, nil)
        guard CGImageDestinationFinalize(dest) else {
            return nil
        }
        return data as Data
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
