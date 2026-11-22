// Canard.app başlatıcısı (docs/ARCHITECTURE.md): depodaki `.venv` Python'unu `--tray` ile alt süreç
// olarak çalıştırır ve o bitene kadar yaşar; böylece macOS izinleri (TCC) Canard.app adına sorulur.
import AppKit

// Kullanıcıya görünen metinler sistem diline göre İngilizce ya da Türkçe.
private let turkish = Locale.preferredLanguages.first?.hasPrefix("tr") ?? false
private func localized(_ english: String, _ turkishText: String) -> String { turkish ? turkishText : english }

final class Launcher: NSObject, NSApplicationDelegate {
    private var child: Process?
    private var stopping = false
    private var signalSources: [DispatchSourceSignal] = []
    private let logURL = FileManager.default.homeDirectoryForCurrentUser
        .appendingPathComponent("Library/Logs/Canard/launcher.log")

    func applicationDidFinishLaunching(_ notification: Notification) {
        for sig in [SIGTERM, SIGINT, SIGHUP] {
            signal(sig, SIG_IGN)
            let source = DispatchSource.makeSignalSource(signal: sig, queue: .main)
            source.setEventHandler { NSApp.terminate(nil) }
            source.resume()
            signalSources.append(source)
        }
        guard let root = Bundle.main.object(forInfoDictionaryKey: "CanardRoot") as? String else {
            fail(localized("CanardRoot is missing from Info.plist. Rebuild the app with native/macos-app/build.sh.",
                           "Info.plist'te CanardRoot yok. Uygulamayı native/macos-app/build.sh ile yeniden derle."))
        }
        let python = root + "/.venv/bin/python"
        guard FileManager.default.isExecutableFile(atPath: python) else {
            fail(localized("Python environment not found:\n\(python)\n\nRun `uv sync` in the project folder.",
                           "Python ortamı bulunamadı:\n\(python)\n\nProje klasöründe `uv sync` çalıştır."))
        }

        let process = Process()
        process.executableURL = URL(fileURLWithPath: python)
        process.arguments = ["-m", "canard.app.main", "--tray"]
        process.currentDirectoryURL = URL(fileURLWithPath: root)
        var environment = ProcessInfo.processInfo.environment
        environment["CANARD_APP_BUNDLE"] = Bundle.main.bundlePath
        environment["PYTHONUNBUFFERED"] = "1"
        environment["NO_COLOR"] = "1"
        process.environment = environment
        // Ham terminal çıktısı her açılışta baştan yazılır; Canard'ın kendi logu canard.log'da.
        if let output = openLog() {
            process.standardOutput = output
            process.standardError = output
        }
        process.terminationHandler = { [weak self] finished in
            DispatchQueue.main.async { self?.childExited(finished) }
        }
        do {
            try process.run()
        } catch {
            fail(localized("Canard could not be started: ", "Canard başlatılamadı: ") + error.localizedDescription)
        }
        child = process
    }

    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        guard let child, child.isRunning else { return .terminateNow }
        stopping = true
        child.terminate()  // SIGTERM → Canard düzgün kapanır (sayfalar, profil, kilit dosyası)
        DispatchQueue.main.asyncAfter(deadline: .now() + 10) {
            if child.isRunning { kill(child.processIdentifier, SIGKILL) }
        }
        return .terminateLater
    }

    private func childExited(_ process: Process) {
        if stopping {
            NSApp.reply(toApplicationShouldTerminate: true)
            return
        }
        let status = process.terminationStatus
        if status != 0 {
            let reason = process.terminationReason == .uncaughtSignal
                ? localized("signal \(status)", "sinyal \(status)") : localized("exit code \(status)", "çıkış kodu \(status)")
            alert(localized("Canard quit unexpectedly (\(reason)).", "Canard beklenmedik şekilde kapandı (\(reason)).")
                  + "\n\n\(logTail())\n\nLog: ~/Library/Logs/Canard/")
        }
        exit(status == 0 ? 0 : 1)
    }

    private func openLog() -> FileHandle? {
        let manager = FileManager.default
        try? manager.createDirectory(at: logURL.deletingLastPathComponent(), withIntermediateDirectories: true)
        manager.createFile(atPath: logURL.path, contents: nil)
        return try? FileHandle(forWritingTo: logURL)
    }

    private func logTail(lines: Int = 6) -> String {
        guard let text = try? String(contentsOf: logURL, encoding: .utf8) else { return "" }
        return text.split(separator: "\n").suffix(lines).joined(separator: "\n")
    }

    private func alert(_ message: String) {
        NSApp.activate(ignoringOtherApps: true)
        let alert = NSAlert()
        alert.messageText = "Canard"
        alert.informativeText = message
        alert.alertStyle = .warning
        alert.runModal()
    }

    private func fail(_ message: String) -> Never {
        alert(message)
        exit(1)
    }
}

let app = NSApplication.shared
let launcher = Launcher()
app.delegate = launcher
app.run()
