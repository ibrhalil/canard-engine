# Terminalden Kullanım

Canard şimdilik terminalden çalıştırılır. Tüm komutlar proje klasöründe (`canard-engine/`) çalıştırılır. Yetki ve izinlerin açıklaması: [IZINLER.md](IZINLER.md).

## Gereksinimler

- macOS (Windows/Linux sonraki fazlarda). Sistem sesi için **macOS 14.2+**
- Python 3.11+ ve [uv](https://docs.astral.sh/uv/)

```bash
uv sync                # bağımlılıkları kur (ilk sefer ve pyproject değişince)
uv run canard --help   # tüm seçenekler
```

## Temel kavramlar

- **Kaynak:** Gösterilecek web içeriği. Üç tür: uzak URL, dev sunucusu URL'si, yerel production build klasörü. Kaynaklar kaydedilir; son kullanılan **aktif kaynak** olur ve bir dahaki çalıştırmada kaynak belirtmeye gerek kalmaz.
- **Mod:** `preview` (normal pencere, varsayılan), `wallpaper` (masaüstü simgelerinin arkasında canlı duvar kağıdı), `screensaver` (yalnız ekran koruyucu: boşta kalınca tam ekran).
- **Boot script:** Sayfa her yüklendiğinde çalışan JS dosyası; içeriği otomatik başlatmak için (ör. mscillus'ta ses kaynağı kartına tıklamak). Kaynağa kalıcı bağlanır.

## Komutlar

| Seçenek | Açıklama |
|---|---|
| `--mode preview\|wallpaper\|screensaver` | Çalışma modu (varsayılan `preview`); `screensaver` = duvar kağıdı olmadan yalnız ekran koruyucu |
| `--screensaver-after DK` | Canard'ın kendi boşta tam ekranı (alternatif, gerçek ekran koruyucu değil) |
| `--saver-fps N` / `--no-saver-stream` | Canard.saver yayını: kare hızı (varsayılan 30) / kapat |
| `--url URL` | Uzak site ya da dev sunucusu ekler ve aktif yapar |
| `--path KLASÖR` | `index.html` içeren yerel build klasörü ekler ve aktif yapar |
| `--source ID` | Kayıtlı bir kaynağı aktif yapıp açar |
| `--list` | Kayıtlı kaynakları listeler (`*` aktif olan) |
| `--remove ID` | Kaynağı siler |
| `--interactive` | Duvar kağıdını etkileşimli başlatır (fare içeriğe gider, simgeler örtülür) |
| `--boot-file DOSYA` | Boot script'i aktif kaynağa bağlar (kalıcı) |
| `--clear-boot` | Aktif kaynağın boot script'ini kaldırır |
| `--no-system-audio` | İçeriğe sistem sesi verilmez |
| `--settings DOSYA` | Başka bir ayar dosyası kullanır (test/ayrı profil için) |
| `--log-level debug\|info\|warning\|error` | Terminal log düzeyi (varsayılan `info`) |
| `-v`, `--verbose` | Ayrıntılı log (`--log-level debug`) |
| `--log-file [YOL]` | Logu dosyaya da yazar (her zaman ayrıntılı); yol verilmezse `~/Library/Logs/Canard/canard.log` |

## Örnekler

### Kaynak eklemek ve önizlemek

```bash
# Uzak site — eklenir, aktif olur, pencerede açılır
uv run canard --url https://ornek.site

# Dev sunucusu (ör. mscillus'ta `npm run dev`)
uv run canard --url http://localhost:5173

# Yerel production build (ör. mscillus'ta `npm run build`)
uv run canard --path ~/dev/IdeaProjects/mscillus/dist

# Aktif kaynağı yeniden aç (kaynak belirtmeden)
uv run canard
```

### Kaynakları yönetmek

```bash
uv run canard --list
#  * 3f9a1c2b7d10  remote     ornek.site  https://ornek.site
#    a81e44c09b3f  build      dist        /Users/…/mscillus/dist

uv run canard --source a81e44c09b3f            # başka kaynağa geç
uv run canard --remove 3f9a1c2b7d10            # kaynağı sil
```

### Canlı duvar kağıdı

```bash
uv run canard --mode wallpaper                  # aktif kaynak, simgelerin arkasında
uv run canard --mode wallpaper --interactive    # fareyle kullanılabilir başlat
```

Çıkmak için terminalde **Ctrl+C**. Duvar kağıdı her Space'te görünür, her ekrana ayrı pencere açılır (ekran başına ayrı içerik durumu). Harici ekran çıkarılınca o ekranın sayfası 10 dakika dondurulup saklanır; aynı ekran bu sürede geri takılırsa içerik kaldığı yerden (aynı mod) sürer, yeniden yüklenmez; tamamen örtülünce (ör. tam ekran uygulama) içerik duraklatılır ve CPU kullanımı ~%1'e iner, görünür olunca devam eder.

**Duvar kağıdı kısayolları** (başka uygulama öndeyken de çalışır):

| Kısayol | Görev |
|---|---|
| `⌃⌥⌘←` / `⌃⌥⌘→` | İçeriğe ← / → tuşu gönderir (mscillus: önceki/sonraki mod) |
| `⌃⌥⌘↑` | Etkileşimli mod aç/kapat — açıkken menülere tıklanabilir, masaüstü simgeleri örtülür |
| `⌃⌥⌘↓` | Ekran koruyucuyu hemen başlat (`--screensaver-after` verildiyse) |

### Ekran koruyucu: Canard.saver (gerçek macOS ekran koruyucusu)

`Canard.saver`, Sistem Ayarları'ndaki ekran koruyucu listesinde **"Canard"** olarak görünür; zamanlayıcı ve parola/kilit tamamen macOS'ta kalır. Görüntüyü çalışan Canard üretir: duvar kağıdı sayfası kilit ekranının arkasında çizilmeye devam eder, her ekranın kendi içeriği donanım HEVC ile `Canard.saver`'a aktarılır (sistem sesi ve mod korunur).

Kurulum (bir kez; Xcode gerekmez, Command Line Tools yeterli):

```bash
native/macos-saver/build.sh --open     # derler ve macOS kurulum penceresini açar → "Yalnızca bu kullanıcı için kur"
```

Sonra Sistem Ayarları › Ekran Koruyucu › **Canard**'ı seç. Canard duvar kağıdı çalışırken ekran koruyucu başlayınca içerik gelir:

```bash
uv run canard --mode wallpaper --boot-file tools/mscillus-boot-system.js     # duvar kağıdı + ekran koruyucu
uv run canard --mode screensaver --boot-file tools/mscillus-boot-system.js   # yalnız ekran koruyucu (duvar kağıdı yok)
```

| Seçenek | Açıklama |
|---|---|
| `--saver-fps N` | Yayın kare hızı (varsayılan 30) |
| `--no-saver-stream` | Canard.saver'a yayın yapma |

Canard çalışmıyorsa ekran koruyucu siyah ekranda "Canard çalışmıyor" yazar ve Canard açılınca kendiliğinden bağlanır. Kaldırmak: `rm -rf ~/Library/Screen\ Savers/Canard.saver` ve Sistem Ayarları'ndan başka bir ekran koruyucu seç.

> ℹ️ **Kare hızı sınırı:** macOS kilitli oturumda ekran yakalama API'lerini (ScreenCaptureKit) askıya alır ve ekran koruyucu içindeki WebKit animasyonu kısar; kilit altında çalışan tek yol sayfanın Canard içinde kare kare kopyalanmasıdır. Ölçüm (M serisi, iki ekran, yerel çözünürlük — 3420×2224 + 1920×1080): **~30 kare/sn**; kareler yakalanma anlarına göre ~100 ms sabit gecikmeyle eşit aralıklı gösterilir. Görüntü tam çözünürlükte ve keskindir; macOS'un kendi 60 kare/sn video ekran koruyucuları kadar akıcı değildir. Ağır içerikte Canard yetişemezse kare hızı kendiliğinden düşer (`-v` logunda `kare/sn gönderildi`).

### Ekran koruyucu: Canard'ın kendi boşta tam ekranı (alternatif)

Canard.saver kurmadan, Canard kendi sayacıyla boşta kalınca içeriği tam ekran gösterebilir:

```bash
uv run canard --mode wallpaper --boot-file tools/mscillus-boot-system.js --screensaver-after 5
uv run canard --mode wallpaper --screensaver-after 0.25     # denemek için 15 sn · ya da istediğin an ⌃⌥⌘↓
```

Herhangi bir fare/klavye hareketinde kapanır; başka uygulama ekranı uyanık tutuyorsa (video), oturum kilitlenince, ekran uyuyunca ya da macOS ekran koruyucusu başlayınca çekilir.

> ⚠️ **Bu gerçek bir sistem ekran koruyucusu değildir.** Sistem listesinde görünmez ve **açıkken oturum kilitli değildir** (macOS kilitli oturumda uygulama pencerelerini gizler). Kilit gerekiyorsa Canard.saver'ı kullan. Bu yolda önerilen macOS ayarları: "Etkin değilken ekran koruyucuyu başlat" → Asla ya da Canard süresinden uzun; "Ekranı kapat" → Canard süresinden uzun; parola → Hemen.

### mscillus ile

```bash
# Arka planda çalan müziği (Spotify, Apple Music, tarayıcı…) duvar kağıdında görselleştir
uv run canard --mode wallpaper --boot-file tools/mscillus-boot-system.js

# Mikrofonu ya da BlackHole gibi bir giriş aygıtını dinle
uv run canard --mode wallpaper --boot-file tools/mscillus-boot-mic.js

# Kaynağı (dosya/mikrofon/sistem sesi) mscillus ekranından elle seçmek için
uv run canard --mode wallpaper --clear-boot --interactive
```

`--boot-file` bir kere verilince kaydedilir; sonraki `uv run canard --mode wallpaper` aynı script'i yine çalıştırır. Açılışta `Boot script aktif` satırı bunu hatırlatır.

### Sistem sesi

İçerik `getDisplayMedia({audio: true})` istediğinde Canard sistem sesini verir (mscillus'ta "Sistem sesi" ve "Sekme sesi" kartları). Terminalde şu satırlar görünür:

```
[audio] sistem sesi yakalanıyor (48000 Hz, 2 kanal)
[audio] sistem sesi durdu
[audio] çıkış aygıtı değişti, yakalama yeniden kuruldu
```

Kapatmak için: `uv run canard --mode wallpaper --no-system-audio`

## Loglar

Her satır `saat düzey bileşen mesaj` biçimindedir; uyarılar sarı, hatalar kırmızı görünür:

```
23:41:02 INFO   audio    sistem sesi yakalanıyor (48000 Hz, 2 kanal)
23:41:09 INFO   screen   kaldırıldı: E1511A
23:41:15 UYARI  web      konsol: Uncaught TypeError: …  (https://…/app.js:12)
```

| Bileşen | Ne yazar |
|---|---|
| `app` | Başlangıç, kaynak, boot script, CLI hataları |
| `wallpaper` | Etkileşimli mod, ekran başına duraklat/devam |
| `screensaver` | Boşta tam ekran (alternatif): başladı/bitti ve nedeni, erteleme; debug'da kilit/uyku olayları |
| `saver` | Canard.saver yayını: hazır, bağlandı (ekran, çözünürlük, kare hızı, bit hızı), ayrıldı, reddedildi; debug'da gönderilen kare/sn |
| `screen` | Ekran ekleme/çıkarma; debug'da geometri ve menü çubuğu boşluğu |
| `hotkey` | Kayıtlı kısayollar; debug'da her basış |
| `audio` | Sistem sesi başlat/durdur, aygıt değişimi; debug'da abone sayfalar |
| `web` | Sayfa konsolu (hata → UYARI, uyarı → INFO, `console.log` → DEBUG), yükleme süresi, izin kararları, sayfa süreci çökmesi |
| `server` | Yerel build sunucusu istekleri (debug) |
| `qt` | Qt'nin kendi mesajları |

```bash
uv run canard --mode wallpaper -v                         # ayrıntılı terminal logu
uv run canard --mode wallpaper --log-file                  # ~/Library/Logs/Canard/canard.log
uv run canard --mode wallpaper --log-level warning         # yalnız uyarı ve hatalar
tail -f ~/Library/Logs/Canard/canard.log                   # başka terminalden izle
```

Log dosyası 2 MB'ı geçince döndürülür (3 yedek). Canard çökerse Python yığın izi de log dosyasına yazılır; hata bildirirken bu dosyayı paylaşmak yeterlidir. Chromium'un iç günlüğü yalnız `debug` düzeyinde terminale düşer.

## Desteklenen ses/video formatları

Hazır Qt paketindeki Chromium'da lisanslı kodekler yoktur: **mp3, ogg, opus, flac, wav, webm** çalışır; **m4a/aac ve mp4/H.264 çalışmaz.** Çözüm Faz 6'da karara bağlanacak.

## Sorun giderme

| Belirti | Çözüm |
|---|---|
| Duvar kağıdında mscillus seçim ekranı hiç görünmüyor | Kayıtlı boot script kaynağı otomatik seçiyor: `--clear-boot` |
| Menülere tıklanamıyor | Etkileşimli mod kapalı: `⌃⌥⌘↑` ya da `--interactive` |
| Mikrofon kaynağı sessiz | Terminal uygulamasına Mikrofon izni ver, terminali yeniden başlat ([IZINLER.md](IZINLER.md)) |
| Sistem sesi kaynağı sessiz | Ekran ve Sistem Sesi Kaydı › *Yalnızca Sistem Sesi Kaydı* listesine terminali ekle, terminali yeniden başlat; macOS 14.2+ gerekir |
| Kısayollar çalışmıyor | Başka bir uygulama aynı kombinasyonu almış olabilir; açılıştaki `Kısayol: …` satırları kayıt başarılı olanları listeler |
| "İçerik yüklenemedi" ekranı | URL'yi/ağı kontrol et, "Yeniden Dene"; yerel build için klasörde `index.html` olmalı |
| `Canard zaten çalışıyor (PID …)` | Aynı anda tek Canard çalışabilir (ikincisi içerik ayarlarını kaydedemez, kısayollar çakışır). Diğerini kapat: kendi terminalinde Ctrl+C ya da `kill PID` |
| `Port … dolu, … kullanılıyor` uyarısı | Yerel build'in kayıtlı portunu başka bir program almış; yeni port seçildi, içeriğin yerel verileri bu kaynak için sıfırlanır |
| Ekran koruyucuda "Canard çalışmıyor" | Canard'ı `--mode wallpaper` ya da `--mode screensaver` ile başlat; `--no-saver-stream` verilmemeli. Logda `ekran koruyucu yayını hazır` görünmeli |
| Ekran koruyucu günlüğü | `log stream --predicate 'subsystem == "dev.canard.saver"' --style compact` |
| Ne olduğunu anlamak / hata bildirmek | `-v --log-file` ile çalıştır, `~/Library/Logs/Canard/canard.log` dosyasını paylaş |
| Her şeyi sıfırlamak | Canard kapalıyken `rm -rf ~/Library/Application\ Support/Canard` |

## Teşhis araçları

```bash
uv run python tools/audio-probe/tap_probe.py            # sistem sesi yakalama testi (Core Audio tap)
uv run python tools/audio-probe/sck_probe.py            # sistem sesi testi (ScreenCaptureKit, ekran kaydı izni ister)
uv run canard --path tools/mic-test                     # mikrofon/kodek teşhis sayfası
uv run canard --mode wallpaper --path tools/wallpaper-demo   # etkileşimsiz duvar kağıdı demosu
```
