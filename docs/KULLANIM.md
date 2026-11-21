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
- **Mod:** `preview` (normal pencere, varsayılan), `wallpaper` (masaüstü simgelerinin arkasında canlı duvar kağıdı), `screensaver` (Faz 3a'da gelecek).
- **Boot script:** Sayfa her yüklendiğinde çalışan JS dosyası; içeriği otomatik başlatmak için (ör. mscillus'ta ses kaynağı kartına tıklamak). Kaynağa kalıcı bağlanır.

## Komutlar

| Seçenek | Açıklama |
|---|---|
| `--mode preview\|wallpaper` | Çalışma modu (varsayılan `preview`) |
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

Çıkmak için terminalde **Ctrl+C**. Duvar kağıdı her Space'te görünür, her ekrana ayrı pencere açılır; tamamen örtülünce (ör. tam ekran uygulama) içerik duraklatılır ve CPU kullanımı ~%1'e iner, görünür olunca devam eder.

**Duvar kağıdı kısayolları** (başka uygulama öndeyken de çalışır):

| Kısayol | Görev |
|---|---|
| `⌃⌥⌘←` / `⌃⌥⌘→` | İçeriğe ← / → tuşu gönderir (mscillus: önceki/sonraki mod) |
| `⌃⌥⌘↑` | Etkileşimli mod aç/kapat — açıkken menülere tıklanabilir, masaüstü simgeleri örtülür |

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
| Her şeyi sıfırlamak | Canard kapalıyken `rm -rf ~/Library/Application\ Support/Canard` |

## Teşhis araçları

```bash
uv run python tools/audio-probe/tap_probe.py            # sistem sesi yakalama testi (Core Audio tap)
uv run python tools/audio-probe/sck_probe.py            # sistem sesi testi (ScreenCaptureKit, ekran kaydı izni ister)
uv run canard --path tools/mic-test                     # mikrofon/kodek teşhis sayfası
uv run canard --mode wallpaper --path tools/wallpaper-demo   # etkileşimsiz duvar kağıdı demosu
```
