// Faz 3a spike B2-1 — .saver, Canard'ın çizdiği kareleri yerel bağlantıdan alıp gösterebilir mi?
// Ölçülen: legacyScreenSaver kum havuzunda 127.0.0.1 TCP bağlantısı, alınan/çizilen kare hızı,
// ekran koruyucunun kendi çizim döngüsü (animateOneFrame) hızı. Web görünümü kullanılmaz.
// Protokol: her kare [4 bayt uzunluk (big-endian)][JPEG] (bkz. frame_server.py).
import ImageIO
import Network
import ScreenSaver
import os

private let b2log = Logger(subsystem: "dev.canard.probe", category: "b2")
private let framePort: NWEndpoint.Port = 47391

@objc(CanardFrameProbeView)
final class CanardFrameProbeView: ScreenSaverView {
    private let probeID = String(UUID().uuidString.prefix(4))
    private let queue = DispatchQueue(label: "dev.canard.probe.b2")
    private let imageLayer = CALayer()
    private var connection: NWConnection?
    private var buffer = Data()
    private var received = 0
    private var applied = 0
    private var ticks = 0
    private var bytes = 0
    private var lastReport = Date()
    private var statsTimer: Timer?
    private var stopped = false

    override init?(frame: NSRect, isPreview: Bool) {
        super.init(frame: frame, isPreview: isPreview)
        animationTimeInterval = 1.0 / 60.0
        wantsLayer = true
        layer?.backgroundColor = NSColor(red: 0.02, green: 0.03, blue: 0.05, alpha: 1).cgColor
        imageLayer.frame = bounds
        imageLayer.contentsGravity = .resizeAspect
        imageLayer.autoresizingMask = [.layerWidthSizable, .layerHeightSizable]
        layer?.addSublayer(imageLayer)
        b2log.log("B2[\(self.probeID, privacy: .public)] init preview=\(isPreview, privacy: .public) frame=\(NSStringFromRect(frame), privacy: .public)")
        connect()
        statsTimer = Timer.scheduledTimer(withTimeInterval: 1, repeats: true) { [weak self] _ in self?.report() }
    }

    required init?(coder: NSCoder) {
        super.init(coder: coder)
    }

    deinit {
        b2log.log("B2 deinit")
    }

    override func animateOneFrame() {
        ticks += 1
    }

    override func startAnimation() {
        super.startAnimation()
        b2log.log("B2[\(self.probeID, privacy: .public)] startAnimation")
    }

    override func stopAnimation() {
        super.stopAnimation()
        b2log.log("B2[\(self.probeID, privacy: .public)] stopAnimation")
        stopped = true
        statsTimer?.invalidate()
        connection?.cancel()
        connection = nil
    }

    private func connect() {
        let conn = NWConnection(host: "127.0.0.1", port: framePort, using: .tcp)
        conn.stateUpdateHandler = { [weak self] state in
            guard let self else { return }
            b2log.log("B2[\(self.probeID, privacy: .public)] bağlantı: \(String(describing: state), privacy: .public)")
            switch state {
            case .ready:
                self.receive()
            case .failed, .waiting:
                conn.cancel()
                self.queue.asyncAfter(deadline: .now() + 2) { [weak self] in
                    guard let self, !self.stopped else { return }
                    self.connect()
                }
            default:
                break
            }
        }
        connection = conn
        conn.start(queue: queue)
    }

    private func receive() {
        connection?.receive(minimumIncompleteLength: 1, maximumLength: 1 << 20) { [weak self] data, _, done, error in
            guard let self else { return }
            if let data {
                self.bytes += data.count
                self.buffer.append(data)
                self.drain()
            }
            if let error {
                b2log.log("B2[\(self.probeID, privacy: .public)] alma hatası: \(String(describing: error), privacy: .public)")
                return
            }
            if done {
                b2log.log("B2[\(self.probeID, privacy: .public)] sunucu bağlantıyı kapattı")
                return
            }
            self.receive()
        }
    }

    private func drain() {
        while buffer.count >= 4 {
            let start = buffer.startIndex
            let length = buffer[start..<start + 4].reduce(0) { ($0 << 8) | Int($1) }
            guard buffer.count >= 4 + length else { return }
            let jpeg = buffer.subdata(in: start + 4..<start + 4 + length)
            buffer.removeSubrange(start..<start + 4 + length)
            received += 1
            guard let source = CGImageSourceCreateWithData(jpeg as CFData, nil),
                  let image = CGImageSourceCreateImageAtIndex(source, 0, nil) else { continue }
            DispatchQueue.main.async { [weak self] in
                guard let self else { return }
                CATransaction.begin()
                CATransaction.setDisableActions(true)
                self.imageLayer.contents = image
                CATransaction.commit()
                self.applied += 1
            }
        }
    }

    private func report() {
        let now = Date()
        let elapsed = now.timeIntervalSince(lastReport)
        lastReport = now
        let line = String(format: "alınan %.0f kare/sn · çizilen %.0f kare/sn · animateOneFrame %.0f/sn · %.0f KB/sn · ölçüm aralığı %.2f sn",
                          Double(received) / elapsed, Double(applied) / elapsed, Double(ticks) / elapsed,
                          Double(bytes) / 1024 / elapsed, elapsed)
        b2log.log("B2[\(self.probeID, privacy: .public)] \(line, privacy: .public)")
        received = 0
        applied = 0
        ticks = 0
        bytes = 0
    }
}
