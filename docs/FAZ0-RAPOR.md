# Faz 0 Araştırma Raporu — Canard

- Tarih: 2026-11-21 · Durum: **sahip onayı bekleniyor**
- Kapsam: sahibin 2026-11-21 yönlendirmesiyle macOS önceliklendirildi ("önce mac tarafında ekran koruyucu ve duvar kağıdı, sonra diğer cihazlar"). Bu rapor macOS'u derinleştirir; Windows/Linux ön tarama düzeyindedir ve kendi fazlarında derinleştirilecek.
- Gereksinim sözleşmesi: [PROMPT.md](PROMPT.md) §9'daki yedi çıktı bu raporun bölümleridir.

## 0. Yöntem ve kaynaklar

| Konu | Kaynak | Sonuç |
|---|---|---|
| mscillus incelemesi | `~/dev/IdeaProjects/mscillus` kaynak kodu (tüm modüller) | §1.5, §7-R5 |
| Screen Saver framework | developer.apple.com/documentation/screensaver (2026'da canlı) | `.saver` mekanizması geçerli; Objective-C, `ScreenSaverView` alt sınıfı, universal binary zorunluğu |
| Pencere seviyeleri | developer.apple.com/documentation/coregraphics/cgwindowlevelkey | `desktopWindow` ve `desktopIconWindow` seviyeleri resmi enum'da mevcut |
| PySide6 | pypi.org/project/PySide6 — 6.12.0 (2026-10-08) | Python 3.10–3.15, macOS 14+ universal2 wheel; lisans LGPL-3 **veya GPL-2 veya GPL-3** (GPLv3 seçeneği GPL-3.0 projemizle uyumlu) |
| Duvar kağıdı tekniği örnekliği | Plash (sindresorhus) | Uygulama canlı ve tekniği yıllardır kullanıyor; kaynak kodu 2024'te kapatıldı → kanıt olarak yalnızca varlık bilgisi |
| .saver ekosistemi örnekliği | Aerial (JohnCoates/Aerial) | V4'te `.saver` yerine App Extension'a geçildi → klasik `.saver`'ın geleceğiyle ilgili sinyal (risk §7-R4) |

Not: Qt dokümantasyonu (doc.qt.io) bu ortamdan 403 döndü; QtWebEngine'in pip wheel içeriği ve Chromium sürümü **kurulumda yerel olarak doğrulanacak** (§7-R3).

## 1. Önerilen mimari

```text
┌──────────────────────────────────────────────────────────┐
│                      canard / app                         │
│  mod yöneticisi: wallpaper │ screensaver │ preview │ tray │
│  yaşam döngüsü, ayarlar (kalıcı), performans politikası   │
├──────────────────────────────────────────────────────────┤
│  content (kaynak kayıt defteri)        web (renderer)     │
│  remote URL │ local build (+statik     QWebEngineView,     │
│  sunucu, dist/ kökten) │ dev server    kalıcı profil,      │
│  │ bundled paket                      autoplay off,       │
│  her kaynağın adı+ayarları            hata ekranı,         │
│                                       JS host-köprüsü     │
├──────────────────────────────────────────────────────────┤
│          platform adapters (işletim sistemi bağımsızlığı) │
│  macos: NSWindow seviyesi, ekran/Space, boşta ölçümü,     │
│         (Faz 4) sistem sesi                              │
│  windows / linux: Faz 2b/2c'de                           │
├──────────────────────────────────────────────────────────┤
│  native/macos-saver: ince Swift `.saver` (WKWebView)      │
│  — Faz 3a hedefi; Python çekirdeğinden bağımsız yükler    │
│    aynı web kaynağını (remote URL veya yerel sunucu)      │
└──────────────────────────────────────────────────────────┘
```

Temel ilkeler (PROMPT §5–§12 ile hizalı):

- Renderer tek: bütün modlar aynı QWebEngine altyapısını kullanır; `.saver` aynı web içeriğini WKWebView ile açar (renderer parçalanmasını §7-R4 altında dürüstçe ele alıyoruz).
- Platform bağımlılığı yalnız adapter katmanında; çekirdek saf Python.
- mscillus dahil hiçbir içerik yeniden yazılmaz; host gereksinimleri köprü/parametreyle karşılanır.

### 1.5 mscillus bulguları (kod incelemesi özeti)

- React 19.3 + Vite 8.3 + TS; Three.js r187 → **WebGL2 şart**; SPA, router yok; `base` tanımsız → **dist/ site kökünden sunulmalı** (`vite.config.ts:4-10`, `dist/index.html:8-9`).
- Ses: `AudioContext` + `AnalyserNode` (fft 2048); 4 kaynak (tab/system `getDisplayMedia`, dosya, mikrofon). **AudioWorklet yok**; analiz ana thread'de (`src/audio/AudioEngine.ts`).
- Kritik host gereksinimleri: (1) uygulama **kaynak seçim ekranıyla açılıyor** — odaksız modda burada takılır → host köprüsü/parametreyle kaynak enjekte edilmeli; (2) `PlaybackRequiresUserGesture` kapatılmalı; (3) kalıcı WebEngine profili (localStorage ayarlar); (4) sistem sesi host tarafında yakalanıp beslenmeli (Faz 4); (5) çalışma zamanı dış ağ isteği yok → offline çalışır, yalnız tembel chunk yüklemeleri için yerel sunucu gerekir.
- Performans kontrolleri içeride var: FPS sınırı (0/30/60), otomatik çözünürlük merdiveni, `webglcontextlost` kurtarması — duvar kağıdı senaryosuna uygun.

## 2. Teknoloji seçimlerinin gerekçeleri

| Seçim | Gerekçe | Reddedilen alternatifler |
|---|---|---|
| **Python 3.12 + PySide6 6.12** | Sahibin şartı olan Python çekirdeği; üç platformda tek widget+pencere soyutlaması; WebEngine ile tek Chromium renderer; GPLv3 seçeneği GPL-3.0 ile uyumlu | CEF (resmi Python bağları yok), pywebview (platform WebView'ları → renderer parçalanır: WKWebView/WebView2/WebKitGTK), Tauri (Rust; Python şartına aykırı) |
| **Qt WebEngine** | Chromium tabanlı, WebGL2 + modern JS; profil/izin/navigasyon kontrolü; köprü (QWebChannel) | Yalnız-macOS WKWebView ana renderer olamaz (Windows/Linux şartı) |
| **pyobjc** (macOS adapter) | NSWindow seviyesi, ekran/Space bildirimleri, idle ölçümü için ince, MIT lisanslı erişim | Tam Swift yardımcı uygulama (Python çekirdeği şartına aykırı; yalnız `.saver` için Swift) |
| **Swift `.saver` (Faz 3a)** | Ekran koruyucu motoruna gerçek kayıt; ScreenSaverView resmi ve güncel; WKWebView ile aynı web içeriği | Python'dan `.saver` yazılamaz; "boşta tam ekran" gerçek entegrasyon değil (MVP olarak ayrıca değerlendirildi, §6-karar) |
| **QLocalServer statik dosya sunucusu** (yerel) | dist/ kökten sunma gereksinimi; üçüncü parti sunucu süreci istemiyoruz | `file://` (CORS/asset sorunları), harici nginx/python-http.server süreci |

Lisans denetimi (PLAN.md takibi): PySide6 **GPLv3 seçeneği — uyumlu**; pyobjc MIT — uyumlu; Swift `.saver` bizim kodumuz → GPL-3.0. Yeni bağımlılık eklendikçe PLAN.md'ye işlenecek.

## 3. Platform uyumluluk matrisi

| Özellik | macOS (bu fazda derinleşti) | Windows 10/11 | Linux |
|---|---|---|---|
| Canlı duvar kağıdı | Pencereyi `CGWindowLevelForKey(desktopWindow)` seviyesine konumlandırma — public API; simgelerin arkası, duvar kağıdı üstü. Spaces/Stage Manager davranışı prototipte doğrulanacak | WorkerW/Progman tekniği (ön tarama; Faz 2b'de doğrulanacak) | X11: `_NET_WM_WINDOW_TYPE_DESKTOP` (ön tarama); Wayland: layer-shell — Qt'de destek yok, KDE/GNOME'a göre değişir (dürüst rapor Faz 2c) |
| Ekran koruyucu | `.saver` + ScreenSaverView (resmi, 2026'da canlı) veya idle-algılı tam ekran (MVP) | `.scr` = `/s` argümanlı exe (ön tarama) | X11 `xscreensaver`/KDE SE (ön tarama) |
| Sistem sesi | ScreenCaptureKit/Core Audio tap (Faz 4) | WASAPI loopback (Faz 4) | PipeWire (Faz 4) |
| Çoklu monitör | NSScreen + Qt ekran soyutlaması | aynı | aynı |
| Dağıtım | codesign + notarization (Gatekeeper devre dışı bırakılmaz) | codesign + SmartScreen | AppImage/deb |

Açık söylenen sınırlar: (1) Apple, masaüstü seviyesini "uygulama çatıları içindir" diye belgeler; bu teknik destekli ama duvar kağıdı kullanımı **belgelenmemiştir** (§7-R1). (2) Linux Wayland'da gerçek entegrasyon masaüstü ortamına bağlı olabilir; mümkün değilse açıkça söylenecek (PROMPT §3).

## 4. Proje klasör yapısı

PROMPT §5 önerisinin uyarlaması (paket adı `canard`, `native/` katmanı eklendi):

```text
canard-engine/
├── pyproject.toml
├── canard/
│   ├── app/          # main.py, application.py, modes.py, settings.py, lifecycle.py
│   ├── content/      # sources.py (kayıt defteri), server.py (statik sunucu), bundling
│   ├── web/          # engine.py (QWebEngineView host), bridge.py (host köprüsü), policy.py
│   ├── platform/     # base.py + macos/ + windows/ + linux/
│   ├── audio/        # Faz 4
│   ├── perf/         # otomatik duraklatma, FPS politikası
│   └── ui/           # tray + ayar penceresi (Faz 5)
├── native/
│   └── macos-saver/  # Swift .saver (WKWebView yükleyici)
├── tests/            # pytest
├── docs/             # PROMPT.md, FAZ0-RAPOR.md
├── PLAN.md · README.md · AGENTS.md · LICENSE
```

## 5. Geliştirme aşamaları (macOS öncelikli yeniden sıralama)

| Faz | İçerik | Çıkış kriteri |
|---|---|---|
| **1 — Web Renderer (macOS)** | Kaynak defteri (ilk kayıt: mscillus canlı subdomain), QWebEngineView host, autoplay off, kalıcı profil, hata/yeniden dene ekranı, dist/ statik sunucu | mscillus canlı URL'de animasyon çalışıyor; localStorage ayarları kalıcı |
| **2a — macOS Duvar Kağıdı** | NSWindow seviyesi (pyobjc), monitör başına pencere, Spaces/Mission Control uyumu, tam ekran uygulama algılandığında duraklatma, pil modu | Duvar kağıdı simgelerin arkasında, çoklu monitörde kararlı |
| **3a — macOS Ekran Koruyucu** | MVP: idle-algılı tam ekran (CGEventSource) → hedef: Swift `.saver` (aynı web kaynağı) | Sistem ekran koruyucu listesinde Canard; fare/klavye ile çıkış |
| **2b/2c — Windows, Linux duvar kağıdı** | Sahibin diğer cihazlarında ayrı doğrulama turu (WorkerW; X11/Wayland) | platform matrisi güncellenir |
| **3b/3c — Windows `.scr`, Linux** | aynı | aynı |
| **4 — Ses** | ScreenCaptureKit sistem sesi → içeriğe aktarım yolu (sanal aygıt mı köprü mü — ölçümle karar, PLAN.md açık sorusu) | müzik ritmine görselleştirme duvar kağıdında da çalışıyor |
| **5 — Ayarlar & Performans** | tray/arayüz, FPS/kalite, otomatik duraklatma seçenekleri | PROMPT §8 listesi |
| **6 — Paketleme** | imza + notarization, autostart, paketler | üç platform kurulum paketi |

## 6. Test stratejisi

- **pytest (otomatik):** kaynak defteri CRUD, statik sunucu (kökten sunum, SPA varlıkları, MIME), ayar kalıcılığı, köprü protokolü (sahte test sayfasıyla), mod geçişleri.
- **macOS manuel matrisi (her faz sonunda):** tek/çok monitör · Spaces · Mission Control · Stage Manager · tam ekran uygulama açıkken duraklatma · uyku/uyanma/hazırda bekletme · pil · kilit ekranı güvenliği · Wallpaper↔Screensaver geçişi · İnternet kesintisinde offline içerik · WebGL bağlam kaybı.
- **mscillus iki listede smoke:** canlı subdomain (remote) + production build (local) — ikisi de Faz 1 çıkış kriteri.
- Windows/Linux matrisleri kendi fazlarında işlenir.

## 7. Bilinen teknik riskler

| # | Risk | Düzey | Tedbir |
|---|---|---|---|
| R1 | Masaüstü pencere seviyesi Apple tarafından duvar kağıdı kullanımı için belgelenmemiş; OS güncellemesiyle davranış değişebilir | Orta | Prototipte erken doğrulama (Faz 2a ilk iş); Plash yıllardır aynı teknikle canlı → pratik kanıt |
| R2 | Spaces/Mission Control/Stage Manager'da duvar kağıdı penceresinin davranışı (her Space'te görünme, Mission Control'te sıçrama) | Orta | Faz 2a manuel matrisi; gerekirse Space başına pencere |
| R3 | PySide6 wheel'inde QtWebEngine'in pip kurulumunda sağlanması/Chromium sürümü doc.qt.io 403 nedeniyle bu turda doğrulanamadı | Düşük | Faz 1 ilk adımı: sanal ortamda kurulum + `QtWebEngineQuick`/`QtWebEngineWidgets` import doğrulaması |
| R4 | Klasik `.saver`'ın geleceği: Aerial V4 App Extension'a geçti; macOS 26'da davranış değişebilir | Orta | MVP olarak idle-algılı tam ekran da hazır; `.saver` hedefi korunur ama tek başına güvence sayılmaz |
| R5 | mscillus kaynak seçim ekranı: host köprüsü olmadan duvar kağıdı/ekran koruyucuda takılır | Kesin | Faz 1'de host köprüsü (parametre + QWebChannel inject; mscillus'a dokunulmaz) |
| R6 | Ses: odaksız WebView'de `getDisplayMedia` çalışmaz; ScreenCaptureKit izni gerekir; gecikme bilinmiyor | Orta | Faz 4 ölçümü; PLAN.md açık sorusu |
| R7 | Dağıtım: WebEngine'in aplikasyon boyutu; notarization süreci; küçük `127.0.0.1` sunucusunun güvenlik politikası | Orta | Faz 6; sunucu yalnız localhost'a bağlanır, uzak arabindirme yok |
| R8 | Linux Wayland gerçek entegrasyon belirsiz (layer-shell Qt'de yok) | Orta | Faz 2c'de dürüst rapor; mümkün değilse açıkça söylenecek |

## 8. Onay istenen kararlar

1. Mimari + klasör yapısı (§1, §4).
2. Ekran koruyucu stratejisi: **önce MVP idle-algılı tam ekran, paralelde ince Swift `.saver` hedefi** (agent önerisi — sahibin onayına sunulur).
3. Platform sırası **macOS → Windows → Linux** sahibin bugünkü talimatıyla kesinleşti; PLAN.md'ye işlenir.
4. Test için mscillus canlı **subdomain URL'i** PLAN.md'ye kaydedilsin (yeni ayar dosyasına gireceğiz; repo'da secret yok).
