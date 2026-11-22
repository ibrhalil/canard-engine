// Canard.saver — macOS'un gerçek ekran koruyucusu olarak Canard'ın canlı içeriğini gösterir (Faz 3a, yol B).
//
// macOS kilitli oturumda uygulama pencerelerini gizler ve legacyScreenSaver içinde WKWebView animasyonu
// kısar (tools/saver-probe ölçümleri, PLAN.md). Bu yüzden içerik Canard'da çizilir; bu görünüm yalnızca
// 127.0.0.1:47391'den gelen donanım HEVC karelerini AVSampleBufferDisplayLayer ile gösterir.
// Protokol: canard/app/saver_stream.py.
//
// legacyScreenSaver tuzakları: stopAnimation her zaman çağrılmıyor ve görünümler bellekten atılmıyor →
// "willstop" bildirimi ve animateOneFrame bekçisi bağlantıyı keser.
import AVFoundation
import CoreMedia
import Network
import ScreenSaver
import os

private let saverLog = Logger(subsystem: "dev.canard.saver", category: "saver")
private let streamPort: NWEndpoint.Port = 47391
private let watchdogSeconds: TimeInterval = 2.0
// Kareler Canard'daki yakalanma anlarına göre bu sabit gecikmeyle gösterilir: kodlama/ağ dalgalanması
// takılma olarak görünmez (ilk sürümde DisplayImmediately ile her kare geldiği an gösteriliyordu).
private let playoutDelay = CMTime(value: 100, timescale: 1000)

@objc(CanardSaverView)
final class CanardSaverView: ScreenSaverView {
    private let videoLayer = AVSampleBufferDisplayLayer()
    private let messageLayer = CATextLayer()
    private let queue = DispatchQueue(label: "dev.canard.saver.net")
    private let sid = String(UUID().uuidString.prefix(4))
    private var connection: NWConnection?
    private var buffer = Data()
    private var format: CMVideoFormatDescription?
    private var streamWidth: Int32 = 0
    private var streamHeight: Int32 = 0
    private var waitingForKeyframe = true
    private var active = false
    private var lastTick = Date()
    private var lastFrameAt = Date.distantPast
    private var connectedAt = Date.distantPast
    private var watchdog: Timer?
    private var retry: DispatchWorkItem?
    private var willStopObserver: NSObjectProtocol?
    private var frames = 0
    private var timebase: CMTimebase?
    private var timebaseRunning = false
    private var resyncs = 0
    private var refusal: (message: String, at: Date)?  // Canard çalışıyor ama yayını reddetti (ör. yayın kapalı)

    override init?(frame: NSRect, isPreview: Bool) {
        super.init(frame: frame, isPreview: isPreview)
        animationTimeInterval = 1.0 / 30.0
        wantsLayer = true
        layer?.backgroundColor = NSColor.black.cgColor
        videoLayer.videoGravity = .resizeAspect
        videoLayer.frame = bounds
        videoLayer.autoresizingMask = [.layerWidthSizable, .layerHeightSizable]
        layer?.addSublayer(videoLayer)
        var clock: CMTimebase?
        if CMTimebaseCreateWithSourceClock(allocator: kCFAllocatorDefault, sourceClock: CMClockGetHostTimeClock(),
                                           timebaseOut: &clock) == noErr, let clock {
            timebase = clock
            videoLayer.controlTimebase = clock
        }
        messageLayer.alignmentMode = .center
        messageLayer.foregroundColor = NSColor(white: 0.55, alpha: 1).cgColor
        messageLayer.fontSize = isPreview ? 11 : 15
        messageLayer.isWrapped = true
        layer?.addSublayer(messageLayer)
        willStopObserver = DistributedNotificationCenter.default().addObserver(
            forName: Notification.Name("com.apple.screensaver.willstop"), object: nil, queue: .main
        ) { [weak self] _ in self?.halt("willstop") }
        if isPreview {
            setMessage("Canard\nCanlı içerik, ekran koruyucu açıldığında Canard'dan gelir.")
        }
        saverLog.log("[\(self.sid, privacy: .public)] init preview=\(isPreview, privacy: .public) \(NSStringFromRect(frame), privacy: .public)")
    }

    required init?(coder: NSCoder) {
        super.init(coder: coder)
    }

    deinit {
        if let willStopObserver { DistributedNotificationCenter.default().removeObserver(willStopObserver) }
        connection?.cancel()
    }

    override func layout() {
        super.layout()
        videoLayer.frame = bounds
        let scale = window?.backingScaleFactor ?? 2
        messageLayer.contentsScale = scale
        messageLayer.frame = CGRect(x: 20, y: bounds.height * 0.08, width: bounds.width - 40, height: 60)
    }

    override func startAnimation() {
        super.startAnimation()
        guard !isPreview else { return }
        active = true
        lastTick = Date()
        connect()
        if watchdog == nil {
            watchdog = Timer.scheduledTimer(withTimeInterval: 0.5, repeats: true) { [weak self] _ in self?.check() }
        }
    }

    override func stopAnimation() {
        super.stopAnimation()
        halt("stopAnimation")
    }

    override func animateOneFrame() {
        // Yalnız bekçi için. Yeniden bağlanma yalnız startAnimation'da: willstop'tan sonra kapanan görünüme
        // gelen son animateOneFrame'ler yeniden bağlanmaya yol açıyordu (sahip testi, 2026-11-22).
        lastTick = Date()
    }

    // MARK: - Yaşam döngüsü

    private func halt(_ reason: String) {
        guard active || connection != nil else { return }
        active = false
        retry?.cancel()
        retry = nil
        connection?.cancel()
        connection = nil
        saverLog.log("[\(self.sid, privacy: .public)] durdu (\(reason, privacy: .public)), \(self.frames) kare gösterildi, \(self.resyncs) saat eşitleme")
    }

    private func check() {
        guard active else { return }
        let now = Date()
        if now.timeIntervalSince(lastTick) > watchdogSeconds {
            halt("bekçi: animateOneFrame gelmiyor")
            return
        }
        if let refusal, now.timeIntervalSince(refusal.at) < 5 {
            setMessage("Canard: \(refusal.message)")  // yeniden denemeler arasında "çalışmıyor"a dönmesin
        } else if connection == nil {
            setMessage("Canard çalışmıyor\nCanard uygulamasını aç  ·  ya da: uv run canard --mode wallpaper")
        } else if now.timeIntervalSince(lastFrameAt) > watchdogSeconds && now.timeIntervalSince(connectedAt) > watchdogSeconds {
            setMessage("Canard'dan görüntü bekleniyor…")
        }
    }

    // MARK: - Bağlantı

    private func connect() {
        guard active, connection == nil, !isPreview else { return }
        guard let window, let screen = window.screen,
              let display = (screen.deviceDescription[NSDeviceDescriptionKey("NSScreenNumber")] as? NSNumber)?.uint32Value
        else {
            scheduleRetry(after: 0.3)  // pencere/ekran henüz bağlanmadı
            return
        }
        let scale = window.backingScaleFactor
        let width = Int(bounds.width * scale), height = Int(bounds.height * scale)
        let conn = NWConnection(host: "127.0.0.1", port: streamPort, using: .tcp)
        connection = conn
        buffer = Data()
        format = nil
        waitingForKeyframe = true
        timebaseRunning = false
        videoLayer.sampleBufferRenderer.flush()
        conn.stateUpdateHandler = { [weak self, weak conn] state in
            guard let self, let conn else { return }
            switch state {
            case .ready:
                saverLog.log("[\(self.sid, privacy: .public)] bağlandı: ekran \(display) \(width)x\(height)")
                let hello = "{\"v\":1,\"display\":\(display),\"width\":\(width),\"height\":\(height)}\n"
                conn.send(content: hello.data(using: .utf8), completion: .contentProcessed { _ in })
                DispatchQueue.main.async { self.connectedAt = Date() }
                self.receive(on: conn)
            case .failed(let error), .waiting(let error):
                saverLog.log("[\(self.sid, privacy: .public)] bağlantı yok: \(String(describing: error), privacy: .public)")
                conn.cancel()
                DispatchQueue.main.async {
                    if self.connection === conn { self.connection = nil }
                    self.scheduleRetry(after: 2)
                }
            default:
                break
            }
        }
        conn.start(queue: queue)
    }

    private func scheduleRetry(after seconds: Double) {
        retry?.cancel()
        let work = DispatchWorkItem { [weak self] in self?.connect() }
        retry = work
        DispatchQueue.main.asyncAfter(deadline: .now() + seconds, execute: work)
    }

    private func receive(on conn: NWConnection) {
        conn.receive(minimumIncompleteLength: 1, maximumLength: 1 << 21) { [weak self, weak conn] data, _, done, error in
            guard let self, let conn else { return }
            if let data, !data.isEmpty {
                self.buffer.append(data)
                self.drain()
            }
            if done || error != nil {
                saverLog.log("[\(self.sid, privacy: .public)] bağlantı kapandı")
                conn.cancel()
                DispatchQueue.main.async {
                    if self.connection === conn { self.connection = nil }
                    self.scheduleRetry(after: 1)
                }
                return
            }
            self.receive(on: conn)
        }
    }

    // MARK: - Paketler (ağ kuyruğunda)

    private func drain() {
        while buffer.count >= 5 {
            let bytes = [UInt8](buffer.prefix(5))
            let length = Int(bytes[1]) << 24 | Int(bytes[2]) << 16 | Int(bytes[3]) << 8 | Int(bytes[4])
            guard buffer.count >= 5 + length else { return }
            let payload = Data(buffer.subdata(in: buffer.startIndex + 5..<buffer.startIndex + 5 + length))
            buffer = Data(buffer.suffix(from: buffer.startIndex + 5 + length))
            handle(kind: bytes[0], payload: payload)
        }
    }

    private func handle(kind: UInt8, payload: Data) {
        switch kind {
        case UInt8(ascii: "C"):
            if let info = try? JSONSerialization.jsonObject(with: payload) as? [String: Any] {
                streamWidth = Int32(info["width"] as? Int ?? 0)
                streamHeight = Int32(info["height"] as? Int ?? 0)
            }
        case UInt8(ascii: "H"):
            let atoms: NSDictionary = ["hvcC": payload as NSData]
            let extensions: NSDictionary = [kCMFormatDescriptionExtension_SampleDescriptionExtensionAtoms: atoms]
            var description: CMVideoFormatDescription?
            let status = CMVideoFormatDescriptionCreate(allocator: kCFAllocatorDefault, codecType: kCMVideoCodecType_HEVC,
                                                        width: streamWidth, height: streamHeight,
                                                        extensions: extensions, formatDescriptionOut: &description)
            if status == noErr {
                format = description
            } else {
                saverLog.error("[\(self.sid, privacy: .public)] biçim oluşturulamadı: \(status)")
            }
        case UInt8(ascii: "F"):
            guard let format, payload.count > 9 else { return }
            let bytes = [UInt8](payload.prefix(9))
            let keyframe = bytes[0] & 1 == 1
            let micros = bytes[1...8].reduce(Int64(0)) { $0 << 8 | Int64($1) }
            if waitingForKeyframe && !keyframe { return }
            waitingForKeyframe = false
            let pts = CMTime(value: micros, timescale: 1_000_000)
            if let sample = makeSample(Data(payload.dropFirst(9)), format: format, pts: pts) {
                DispatchQueue.main.async { self.show(sample, pts: pts) }
            }
        case UInt8(ascii: "E"):
            let message = String(data: payload, encoding: .utf8) ?? "?"
            saverLog.log("[\(self.sid, privacy: .public)] Canard reddetti: \(message, privacy: .public)")
            DispatchQueue.main.async {
                self.refusal = (message, Date())
                self.setMessage("Canard: \(message)")
            }
        default:
            break
        }
    }

    private func makeSample(_ data: Data, format: CMVideoFormatDescription, pts: CMTime) -> CMSampleBuffer? {
        var block: CMBlockBuffer?
        let length = data.count
        guard CMBlockBufferCreateWithMemoryBlock(allocator: kCFAllocatorDefault, memoryBlock: nil, blockLength: length,
                                                 blockAllocator: kCFAllocatorDefault, customBlockSource: nil,
                                                 offsetToData: 0, dataLength: length,
                                                 flags: kCMBlockBufferAssureMemoryNowFlag,
                                                 blockBufferOut: &block) == kCMBlockBufferNoErr, let block
        else { return nil }
        let copied = data.withUnsafeBytes { raw in
            CMBlockBufferReplaceDataBytes(with: raw.baseAddress!, blockBuffer: block, offsetIntoDestination: 0,
                                          dataLength: length)
        }
        guard copied == kCMBlockBufferNoErr else { return nil }
        var sample: CMSampleBuffer?
        var sizes = [length]
        var timing = CMSampleTimingInfo(duration: .invalid, presentationTimeStamp: pts, decodeTimeStamp: .invalid)
        guard CMSampleBufferCreateReady(allocator: kCFAllocatorDefault, dataBuffer: block, formatDescription: format,
                                        sampleCount: 1, sampleTimingEntryCount: 1, sampleTimingArray: &timing,
                                        sampleSizeEntryCount: 1, sampleSizeArray: &sizes,
                                        sampleBufferOut: &sample) == noErr, let sample
        else { return nil }
        return sample
    }

    // MARK: - Görüntü (ana iş parçacığı)

    private func show(_ sample: CMSampleBuffer, pts: CMTime) {
        guard active else { return }
        let renderer = videoLayer.sampleBufferRenderer
        if renderer.status == .failed {
            saverLog.error("[\(self.sid, privacy: .public)] çözücü hatası: \(String(describing: renderer.error), privacy: .public)")
            renderer.flush()
            timebaseRunning = false
            queue.async { self.waitingForKeyframe = true }
            return
        }
        if let timebase {
            // Kare, saatin ne kadar önünde? Normalde ≈ playoutDelay. Geç kalırsa (bağlantı yavaşladı) ya da çok
            // öndeyse (Canard yeniden başladı) saat yeniden eşitlenir.
            let lead = CMTimeGetSeconds(CMTimeSubtract(pts, CMTimebaseGetTime(timebase)))
            if !timebaseRunning || lead < -0.05 || lead > 0.5 {
                if timebaseRunning { resyncs += 1 }
                CMTimebaseSetTime(timebase, time: CMTimeSubtract(pts, playoutDelay))
                CMTimebaseSetRate(timebase, rate: 1.0)
                timebaseRunning = true
            }
        }
        renderer.enqueue(sample)
        frames += 1
        lastFrameAt = Date()
        refusal = nil
        setMessage(nil)
    }

    private func setMessage(_ text: String?) {
        CATransaction.begin()
        CATransaction.setDisableActions(true)
        messageLayer.string = text
        messageLayer.isHidden = text == nil
        CATransaction.commit()
    }
}
