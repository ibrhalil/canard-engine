// Motor kıyası, WebKit tarafı: Swift + WKWebView ile ana ekranı kaplayan pencere (Qt tarafının birebir karşılığı).
//
// Doğrudan çalıştırılmaz; tools/engine-bench/run.py başlatır ve stdout satırlarını okur:
//   ready · fps=… worst_ms=… · capture_ms=… frames=… skipped=…
// Esc ile çıkılır. --capture: WKWebView.takeSnapshot → BGRA piksel tamponu → (--encode) donanım HEVC; Canard.saver
// hattının WebKit'teki karşılığı. Terminalden başlatıldığı için izinler (mikrofon) terminal uygulamasına bağlıdır.
import AppKit
import VideoToolbox
import WebKit

func emit(_ line: String) {
    FileHandle.standardOutput.write((line + "\n").data(using: .utf8)!)
}

struct Options {
    var url = ""
    var boot: String?
    var prelude: String?
    var keys = 0
    var capture = 0
    var encode = false

    init(_ arguments: [String]) {
        var iterator = arguments.dropFirst().makeIterator()
        while let argument = iterator.next() {
            switch argument {
            case "--url": url = iterator.next() ?? ""
            case "--boot": boot = iterator.next().flatMap { try? String(contentsOfFile: $0, encoding: .utf8) }
            case "--prelude": prelude = iterator.next().flatMap { try? String(contentsOfFile: $0, encoding: .utf8) }
            case "--keys": keys = Int(iterator.next() ?? "") ?? 0
            case "--capture": capture = Int(iterator.next() ?? "") ?? 0
            case "--encode": encode = true
            default: break
            }
        }
    }
}

final class BenchWindow: NSWindow {
    override var canBecomeKey: Bool { true }
    override func keyDown(with event: NSEvent) {
        if event.keyCode == 53 { NSApp.terminate(nil) } else { super.keyDown(with: event) }  // Esc
    }
}

final class Bench: NSObject, NSApplicationDelegate, WKNavigationDelegate, WKUIDelegate, WKScriptMessageHandler {
    let options = Options(CommandLine.arguments)
    var window: BenchWindow!
    var webView: WKWebView!
    var ready = false
    var pending = false
    var spentMs = 0.0
    var frames = 0
    var skipped = 0
    var encoder: VTCompressionSession?
    var encoderSize = (0, 0)

    func applicationDidFinishLaunching(_ notification: Notification) {
        for sig in [SIGTERM, SIGINT] {
            signal(sig, SIG_IGN)
            let source = DispatchSource.makeSignalSource(signal: sig, queue: .main)
            source.setEventHandler { NSApp.terminate(nil) }
            source.resume()
            signalSources.append(source)
        }
        let here = URL(fileURLWithPath: #filePath).deletingLastPathComponent()
        let meter = (try? String(contentsOf: here.appendingPathComponent("fps_meter.js"), encoding: .utf8)) ?? ""
        let configuration = WKWebViewConfiguration()
        configuration.mediaTypesRequiringUserActionForPlayback = []
        configuration.userContentController.addUserScript(
            WKUserScript(source: meter, injectionTime: .atDocumentStart, forMainFrameOnly: true))
        if let prelude = options.prelude {  // içerik kodundan önce (ör. mscillus ayarları ve modu; bkz. suite.sh)
            configuration.userContentController.addUserScript(
                WKUserScript(source: prelude, injectionTime: .atDocumentStart, forMainFrameOnly: true))
        }
        configuration.userContentController.add(self, name: "bench")

        let frame = NSScreen.screens[0].frame  // menü çubuklu ekran (NSScreen.main odaklı pencerenin ekranıdır)
        window = BenchWindow(contentRect: frame, styleMask: [.borderless], backing: .buffered, defer: false)
        window.title = "Canard motor kıyası — WebKit"
        webView = WKWebView(frame: NSRect(origin: .zero, size: frame.size), configuration: configuration)
        webView.navigationDelegate = self
        webView.uiDelegate = self
        webView.autoresizingMask = [.width, .height]
        window.contentView = webView
        // Odak almadan en üstte: kıyas sırasında başka ekranda çalışan kullanıcının klavyesi buraya gelmesin
        // (mscillus harfleri mod kısayolu sayar). Esc yalnız pencereye tıklanınca çalışır.
        window.level = .floating
        window.orderFrontRegardless()
        webView.load(URLRequest(url: URL(string: options.url)!))
    }

    var signalSources: [DispatchSourceSignal] = []

    func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
        guard !ready else { return }
        ready = true
        emit("ready")
        if let boot = options.boot { webView.evaluateJavaScript(boot) }
        for index in 0..<options.keys {
            DispatchQueue.main.asyncAfter(deadline: .now() + 5 + Double(index) * 0.4) { self.sendKey("ArrowRight") }
        }
        if options.capture > 0 {
            DispatchQueue.main.asyncAfter(deadline: .now() + 1) { self.startCapture() }
        }
    }

    // Mikrofon/kamera: Canard'ın politikası gibi sormadan izin ver.
    func webView(_ webView: WKWebView, requestMediaCapturePermissionFor origin: WKSecurityOrigin,
                 initiatedByFrame frame: WKFrameInfo, type: WKMediaCaptureType,
                 decisionHandler: @escaping (WKPermissionDecision) -> Void) {
        decisionHandler(.grant)
    }

    func userContentController(_ controller: WKUserContentController, didReceive message: WKScriptMessage) {
        if let text = message.body as? String { emit(text) }
    }

    func sendKey(_ key: String) {
        webView.evaluateJavaScript("""
            (function(k){var o={key:k,code:k,bubbles:true,cancelable:true};
            document.dispatchEvent(new KeyboardEvent('keydown',o));
            document.dispatchEvent(new KeyboardEvent('keyup',o));})('\(key)');
            """)
    }

    // MARK: - Kare yakalama (Canard.saver hattının WebKit karşılığı)

    func startCapture() {
        Timer.scheduledTimer(withTimeInterval: 1.0 / Double(options.capture), repeats: true) { _ in self.capture() }
        Timer.scheduledTimer(withTimeInterval: 1, repeats: true) { _ in
            if self.frames > 0 || self.skipped > 0 {
                emit(String(format: "capture_ms=%.1f frames=%d skipped=%d",
                            self.spentMs / Double(max(1, self.frames)), self.frames, self.skipped))
            }
            self.spentMs = 0
            self.frames = 0
            self.skipped = 0
        }
    }

    func capture() {
        if pending {
            skipped += 1  // önceki anlık görüntü bitmedi: Qt tarafındaki gibi kare atlanır, birikme olmaz
            return
        }
        pending = true
        let started = CACurrentMediaTime()
        let configuration = WKSnapshotConfiguration()
        configuration.afterScreenUpdates = false
        webView.takeSnapshot(with: configuration) { image, _ in
            defer { self.pending = false }
            guard let image, let cgImage = image.cgImage(forProposedRect: nil, context: nil, hints: nil),
                  let buffer = self.pixelBuffer(from: cgImage)
            else { return }
            if self.options.encode { self.encode(buffer, width: cgImage.width & ~1, height: cgImage.height & ~1) }
            self.spentMs += (CACurrentMediaTime() - started) * 1000
            self.frames += 1
        }
    }

    func pixelBuffer(from image: CGImage) -> CVPixelBuffer? {
        var buffer: CVPixelBuffer?
        guard CVPixelBufferCreate(nil, image.width, image.height, kCVPixelFormatType_32BGRA, nil, &buffer) == kCVReturnSuccess,
              let buffer
        else { return nil }
        CVPixelBufferLockBaseAddress(buffer, [])
        defer { CVPixelBufferUnlockBaseAddress(buffer, []) }
        guard let context = CGContext(
            data: CVPixelBufferGetBaseAddress(buffer), width: image.width, height: image.height, bitsPerComponent: 8,
            bytesPerRow: CVPixelBufferGetBytesPerRow(buffer), space: CGColorSpaceCreateDeviceRGB(),
            bitmapInfo: CGImageAlphaInfo.premultipliedFirst.rawValue | CGBitmapInfo.byteOrder32Little.rawValue)
        else { return nil }
        context.draw(image, in: CGRect(x: 0, y: 0, width: image.width, height: image.height))
        return buffer
    }

    func encode(_ buffer: CVPixelBuffer, width: Int, height: Int) {
        if encoder == nil || encoderSize != (width, height) {
            var session: VTCompressionSession?
            VTCompressionSessionCreate(allocator: nil, width: Int32(width), height: Int32(height),
                                       codecType: kCMVideoCodecType_HEVC, encoderSpecification: nil,
                                       imageBufferAttributes: nil, compressedDataAllocator: nil, outputCallback: nil,
                                       refcon: nil, compressionSessionOut: &session)
            guard let session else { return }
            let fps = options.capture
            VTSessionSetProperty(session, key: kVTCompressionPropertyKey_RealTime, value: kCFBooleanTrue)
            VTSessionSetProperty(session, key: kVTCompressionPropertyKey_AllowFrameReordering, value: kCFBooleanFalse)
            VTSessionSetProperty(session, key: kVTCompressionPropertyKey_AverageBitRate,
                                 value: NSNumber(value: Int(Double(width * height * fps) * 0.12)))
            VTSessionSetProperty(session, key: kVTCompressionPropertyKey_ExpectedFrameRate, value: NSNumber(value: fps))
            encoder = session
            encoderSize = (width, height)
        }
        let pts = CMTime(seconds: CACurrentMediaTime(), preferredTimescale: 1_000_000)
        VTCompressionSessionEncodeFrame(encoder!, imageBuffer: buffer, presentationTimeStamp: pts, duration: .invalid,
                                        frameProperties: nil, infoFlagsOut: nil) { _, _, _ in }
    }
}

let app = NSApplication.shared
app.setActivationPolicy(.regular)
let bench = Bench()
app.delegate = bench
app.run()
