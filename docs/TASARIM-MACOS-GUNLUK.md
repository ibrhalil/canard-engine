# Tasarım: macOS'ta terminalsiz günlük kullanım

Durum: **plan onaylı** (2026-11-22, insan-onaylı — bkz. PLAN.md). Bu belge uygulayacak session için araştırma, doğrulanmış bilgiler, tasarım kararları, riskler ve test planını toplar. "Agent önerisi" yazan maddeler uygulama sırasında sahibine danışılmadan değiştirilmemeli; değişirse PLAN.md'ye işlenir.

## 1. Hedef ve kapsam

Sahibi Canard'ı her gün terminal açmadan kullanabilsin:

1. **Menü çubuğu simgesi** (`canard --tray`): duvar kağıdı, kaynak, başlangıç betiği, etkileşimli mod, sistem sesi, ekran koruyucu ayarları, oturum açılışında başlatma.
2. **`Canard.app`** (geliştirici uygulaması): izinler "Canard" adına, Dock'ta görünmez, Canard.saver içinde, oturum açılışında başlar.
3. **Ekran koruyucu başlarken ~1 sn ses kesintisinin giderilmesi.**

Kapsam dışı (Faz 6): Python + Qt'yi gömen dağıtılabilir paket, Developer ID imzası, notarization, kodek kararı. Menü çubuğu rengi (sahibi erteledi).

## 2. Süreç mimarisi

```
Canard.app/Contents/MacOS/Canard          (Swift başlatıcı, Mach-O — TCC'nin "sorumlu kod"u)
  └─ <repo>/.venv/bin/python -m canard.app.main --tray --log-file
       ├─ QtWebEngineProcess (renderer, GPU, audio service …)   ← mikrofon burada açılır
       └─ Core Audio tap (sistem sesi, Python sürecinde)
legacyScreenSaver → Canard.saver  ──TCP 127.0.0.1:47391──►  Python (kare yayını)
```

- Python, uv'nin yorumlayıcısıdır (`~/.local/share/uv/python/cpython-3.12.15-macos-aarch64-none/`), `.venv/bin/python` ona sembolik bağ. Paket dışında kalması TCC için sorun değil (§3).
- Depo yolu derleme anında Info.plist'e yazılır (`CanardRoot` gibi özel bir anahtar); başlatıcı oradan `.venv/bin/python`'u bulur.

## 3. Doğrulanmış bilgiler (kaynaklı)

### 3.1 TCC "sorumlu süreç" — izinler kime bağlanır?

| Bilgi | Kaynak |
|---|---|
| macOS izin kararını ve istemi "sorumlu kod"a bağlar; genelde alt süreçten üst sürece zinciri izler | [Apple Forums #805245](https://developer.apple.com/forums/thread/805245), [#731504](https://developer.apple.com/forums/thread/731504) |
| Uygulama **Finder'dan ya da `open` ile** açılırsa uygulama sorumlu olur; iç ikiliyi terminalden çalıştırmak terminali sorumlu yapar (bugün Canard'ın izinleri bu yüzden iTerm'e bağlı) | [Qt blog: The curious case of the responsible process](https://www.qt.io/blog/the-curious-case-of-the-responsible-process) |
| `responsibility_spawnattrs_setdisclaim` kullanılırsa alt süreç kendi izinlerinden sorumlu olur — **kullanılmamalı** | Qt blog |
| Alt süreç kendini daemon'a çevirir, çift fork yapar ya da üst süreçten uzun yaşarsa zincir kırılabilir — Python başlatıcının doğrudan çocuğu kalmalı, başlatıcı Python bitene kadar yaşamalı | [#678819](https://developer.apple.com/forums/thread/678819), #805245 |
| Kararın hatırlanması **kararlı imza kimliği** ister; ad-hoc imzada her yeniden derleme yeni uygulama sayılabilir → tekrar tekrar izin sorulur | #678819 (TN3127'ye atıf) |
| Paketin `CFBundleExecutable`'ı Mach-O olmalı (betik olmamalı) → Swift başlatıcı | #678819 |
| `com.apple.security.inherit` TCC'yi etkilemez | #678819 |
| macOS 26.1'de paketsiz çalıştırılabilirler "Ekran ve Sistem Sesi Kaydı" listesinde görünmüyor (hata, 26.3'te benzeri düzeldi); paketli uygulamayı etkilemez | [#807898](https://developer.apple.com/forums/thread/807898) |

> Kaynakların hiçbiri **mikrofon (Chromium audio service alt süreci)** ve **Core Audio tap** için bu zinciri ayrıca doğrulamıyor → §6'daki ilk spike.

### 3.2 Info.plist izin açıklamaları

| Anahtar | Neden | Kaynak |
|---|---|---|
| `NSMicrophoneUsageDescription` | mscillus mikrofon kaynağı (Chromium `getUserMedia`) | Apple |
| `NSAudioCaptureUsageDescription` | Core Audio tap (sistem sesi, TCC servisi `kTCCServiceAudioCapture`); Xcode listesinde yok, Info.plist'e elle yazılır | [insidegui/AudioCap](https://github.com/insidegui/AudioCap), [dev.to: 2000 buffers of nothing](https://dev.to/nickdelv/2000-buffers-of-nothing-3i8) |
| `NSCameraUsageDescription` | içerik kamera isterse (policy.py izin veriyor) | Apple |
| `LSUIElement` = true | Dock'ta ve ⌘Tab'da görünmez, yalnız menü çubuğu | Apple |

### 3.3 Oturum açılışında başlatma

- macOS 13+ önerilen yol `SMAppService` ([Apple](https://developer.apple.com/documentation/servicemanagement/smappservice)). Ad-hoc imzalı uygulamada davranışı belgelerde yok; çağrı Swift'ten yapılmalı.
- **Agent önerisi: LaunchAgent** (`~/Library/LaunchAgents/dev.canard.app.plist`): imza gerektirmez, Python'dan yönetilebilir. `ProgramArguments = ["/usr/bin/open", "-a", "<Canard.app yolu>"]` → uygulama LaunchServices üzerinden açılır, TCC sorumlusu Canard.app olur (§3.1). `RunAtLoad = true`, `AssociatedBundleIdentifiers = ["dev.canard.app"]` (Sistem Ayarları › Giriş Öğeleri'nde Canard adıyla görünür; #678819). Aç/kapat: `launchctl bootstrap|bootout gui/$UID <plist>`.

### 3.4 Bu makinede (2026-11-22)

- Kod imzalama kimliği **yok** (`security find-identity -v -p codesigning` → 0). Ad-hoc imzayla her yeniden derlemede izin tekrar sorulabilir. Çözüm seçenekleri: (a) Anahtar Zinciri Erişimi › Sertifika Yardımcısı › Sertifika Oluştur → tür "Kod İmzalama", kendinden imzalı (Apple hesabı gerekmez; kararlı kimlik), `build.sh` `CANARD_SIGN_IDENTITY` ile kullanır; (b) ad-hoc'ta kalıp yalnız gerektiğinde yeniden derlemek.
- `QSystemTrayIcon.isSystemTrayAvailable()` = True; `QIcon.setIsMask` var (şablon ikon: açık/koyu menü çubuğuna uyar); `NSApplicationActivationPolicyAccessory` = 1 (paketsiz `--tray` çalışırken Dock simgesini gizlemek için).
- `launchctl print gui/501` erişilebilir.
- Projede ikon dosyası yok → menü çubuğu ikonu çalışma anında QPainter ile çizilen tek renk şablon (agent önerisi: tuval/çerçeve motifi), uygulama ikonu Faz 6'ya kadar varsayılan.

## 4. Tasarım kararları (agent önerisi)

### 4.1 Çalışma zamanı denetleyicisi (önce yapılacak yeniden düzenleme)

Bugün `CanardApp.run()` tek seferlik: kaynak, mod, boot script, sistem sesi CLI'dan gelir ve değişmez. Menü için çalışırken değişebilmeleri gerekir:

- `CanardApp.run` içindeki kurulum bir **`Runtime`** sınıfına taşınır: `start_wallpaper()` / `stop_wallpaper()`, `set_source(id)` (statik sunucu gerekirse başlatılır, sayfalar `host.load(url)` ile yeniden yüklenir, `HostBridge` kaynak bilgisi güncellenir), `set_boot_script(text|None)` (WebHost'un `_boot_script`'i + yeniden yükleme), `set_interactive(bool)`, `set_system_audio(bool)` (QWebEngineScript profilden eklenir/çıkarılır + yeniden yükleme), `set_saver_stream(enabled, fps)`.
- CLI (`--mode wallpaper` vb.) aynı `Runtime`'ı kullanır; davranışı değişmez.
- Tek örnek kilidi aynen kalır: menü uygulaması açıkken terminaldeki `canard` reddedilir (mesaj zaten açıklayıcı).

### 4.2 Menü çubuğu (`canard --tray`)

`QSystemTrayIcon` + `QMenu`; paketsiz çalışırken `NSApp.setActivationPolicy_(Accessory)`. Menü (onaylı plan):

```
● Canard — duvar kağıdı açık · mscillus (dist)
──────────────
Duvar kağıdı                      ✓
Kaynak                            ▸  ● dist (build) · ○ mscillus.ibrhalil.com · ─ · URL ekle… · Klasör ekle…
Başlangıç betiği                  ▸  ○ Yok · ● mscillus: sistem sesi · ○ mscillus: mikrofon · ○ Dosya seç…
Etkileşimli mod        ⌃⌥⌘↑       ✓
Sistem sesi                       ✓
Ekran koruyucu                    ▸  Canard.saver: kurulu ✓ / Kur… · Kare hızı ▸ 24 · ● 30 · Yayın ✓
──────────────
Oturum açılışında başlat          ✓
Log klasörünü aç · Hakkında
Çık                               ⌘Q
```

- Ayarlar `settings.json`'a: `tray.wallpaper`, `tray.interactive`, `tray.system_audio`, `saver.enabled`, `saver.fps` (kaynak ve boot script zaten kayıtlı).
- "Canard.saver kurulu mu": `~/Library/Screen Savers/Canard.saver` var mı; "Kur…" → `open <Canard.app>/Contents/Resources/Canard.saver` (resmi kurulum penceresi). Seçili ekran koruyucu macOS'tan okunmaz (Sistem Ayarları'nı açan bir öğe yeterli: `open "x-apple.systempreferences:com.apple.ScreenSaver-Settings.extension"` — **doğrulanmalı**).
- Başlangıç betiği hazırları `tools/mscillus-boot-*.js`'den gelir; ileride içerik başına hazır betikler bir klasörden listelenebilir.
- Global kısayollar menüyle aynı eylemleri çağırır (etkileşimli mod menüdeki işareti günceller).

### 4.3 `Canard.app` (`native/macos-app/`)

- **Başlatıcı (Swift, ~100 satır):** Info.plist'ten `CanardRoot`'u okur → `posix_spawn`/`Process` ile `<root>/.venv/bin/python -m canard.app.main --tray --log-file` (çalışma dizini `<root>`, **disclaim yok**). SIGTERM/SIGINT'i çocuğa iletir, çocuk bitince kendisi de çıkar; `.venv` yoksa `NSAlert` ile "önce `uv sync`" der. Python stdout/stderr'i log dosyasına yönlendirilir (Canard zaten `--log-file` yazıyor).
- **Info.plist:** `CFBundleIdentifier = dev.canard.app`, `LSUIElement`, §3.2 anahtarları, `CanardRoot`, `LSMinimumSystemVersion = 14.0`.
- **İçerik:** `Contents/Resources/Canard.saver` (native/macos-saver çıktısı).
- **Derleme:** `native/macos-app/build.sh [--install]` → `build/Canard.app`, `--install` → `~/Applications/Canard.app`; evrensel ikili; imza `CANARD_SIGN_IDENTITY` (yoksa ad-hoc).
- **Oturum açılışı:** §3.3 LaunchAgent; menüdeki işaret plist'in varlığını gösterir.

### 4.4 Ses kesintisi düzeltmesi

Neden: ekran koruyucu başlayınca macOS duvar kağıdı pencerelerini önce "örtülü" bildiriyor → sayfalar donuyor ve sistem sesi yakalaması duruyor; ~1 sn sonra Canard.saver bağlanınca sayfalar yeniden canlanıyor (PLAN 2026-11-22).

Öneri: `WallpaperManager`, `com.apple.screensaver.didstart` dağıtık bildirimini dinler; yayın sunucusu açıkken birkaç saniyelik bir "ekran koruyucu bağlanıyor" penceresi açar ve bu süre içinde gelen "örtülü" olaylarında sayfayı dondurmaz. Bu süre içinde Canard.saver bağlanmazsa (başka ekran koruyucu seçili) gerçek görünürlük uygulanır (`window_visible`). Olay sırası (didstart → örtülme) önce `-v` loguyla doğrulanmalı.

## 5. Dosya planı

| Dosya | İş |
|---|---|
| `canard/app/runtime.py` (yeni) | §4.1 Runtime |
| `canard/app/tray.py` (yeni) | §4.2 menü, şablon ikon çizimi, LaunchAgent yönetimi |
| `canard/app/application.py` | CanardApp.run → Runtime; WallpaperManager'a §4.4 |
| `canard/app/main.py` | `--tray` |
| `canard/platform/macos/adapter.py` (+ yardımcı) | etkinleştirme politikası (Accessory), LaunchAgent, `didstart` dinleme |
| `native/macos-app/` (yeni) | `CanardLauncher.swift`, `build.sh` |
| `docs/KULLANIM.md`, `docs/IZINLER.md`, `README.md` | menü kullanımı; izinlerin artık "Canard"a bağlandığı, sıfırlama (`tccutil reset … dev.canard.app`) |

## 6. Riskler ve önce yapılacak doğrulamalar (spike)

1. **TCC zinciri (en kritik):** Canard.app `open` ile açıldığında (a) mikrofon istemi "Canard" adına mı geliyor (istek QtWebEngine audio service alt sürecinden), (b) Core Audio tap sistem sesi istemi "Canard" adına mı? Doğrulama: `sudo launchctl procinfo <pid> | grep responsible` (Python ve QtWebEngineProcess için), `log stream --debug --predicate 'subsystem == "com.apple.TCC"'`. Olumsuzsa seçenekler: Python'u bundle içine bağlamak (`Contents/MacOS/python` sembolik bağı/kopya) ya da izinlerin terminalde kalmasını kabul etmek — sahibine sorulur.
2. **Ad-hoc imza:** her yeniden derlemede tekrar izin. §3.4 seçeneklerini sahibine sun.
3. **Menü çubuğu kalabalığı:** çentikli ekranda menü çubuğu doluysa simge çentiğin arkasına düşebilir (macOS davranışı); belgeye not.
4. **Runtime yeniden düzenlemesi:** park/geri getirme, yayın, sistem sesi aboneleri ve tek örnek kilidi ile etkileşim — mevcut davranışın korunduğunu `-v` loguyla yeniden test et (iki ekran, tak-çıkar, ekran koruyucu).
5. **Sistem Ayarları derin bağlantısı** (`x-apple.systempreferences:` ekran koruyucu bölmesi) macOS 27'de değişmiş olabilir — denenmeli, olmazsa yalnız Sistem Ayarları açılır.

## 7. Test planı (sahibin manuel testi)

1. `native/macos-app/build.sh --install`, `open ~/Applications/Canard.app` → menü çubuğunda simge, Dock'ta yok.
2. İlk açılışta izin istemleri "Canard" adına mı? (mikrofon, sistem sesi) → Sistem Ayarları › Gizlilik'te "Canard" listede mi?
3. Menüden: duvar kağıdı kapat/aç, kaynak değiştir (uzak ↔ yerel build), başlangıç betiği değiştir, etkileşimli mod, sistem sesi kapat/aç — her biri anında etkili mi, uygulama yeniden açılınca hatırlanıyor mu?
4. Ekran koruyucu: menüden kare hızı 24/30, ekran koruyucu başlarken ses kesintisi kalktı mı (`-v` log: başlangıçta `sistem sesi durdu` görünmemeli).
5. "Oturum açılışında başlat" → çıkış yap/gir → Canard kendiliğinden geliyor mu; Sistem Ayarları › Giriş Öğeleri'nde Canard görünüyor mu?
6. Terminalden `uv run canard --mode wallpaper` → "Canard zaten çalışıyor" ile reddedilmeli.
7. Menüden Çık → duvar kağıdı kalkar, kilit dosyası silinir, ses yakalaması durur.
