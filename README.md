# Canard — CANvas boARD engine

Web tabanlı içerikleri (uzak URL, yerel production build, dev sunucusu, paketlenmiş içerik) **canlı duvar kağıdı** ve **ekran koruyucusu** olarak masaüstünde çalıştıran, Python çekirdekli, açık ve genişletilebilir bir motor.

- Windows · macOS · Linux — tek Python çekirdeği, platform başına ince adapter katmanları
- Gerçek masaüstü entegrasyonu: macOS'ta simgelerin arkasında pencere, yerel `.saver`; Windows'ta WorkerW + `.scr`; Linux'ta X11/Wayland (Faz 2b/2c)
- İçerikten bağımsız: ilk örnek içerik [mscillus](https://github.com/ibrhalil/mscillus) müzik görselleştirici; motor herhangi bir web kaynağını çalıştırır

## Durum

macOS: Faz 1 (web renderer) ve Faz 4a (sistem sesi) tamamlandı; Faz 2a (duvar kağıdı) kapanış aşamasında. Faz planı ve tüm kararlar: [PLAN.md](PLAN.md) · Gereksinimler: [docs/PROMPT.md](docs/PROMPT.md)

## Hızlı başlangıç

```bash
uv sync
uv run canard --url https://ornek.site                       # önizleme penceresi
uv run canard --mode wallpaper                               # aktif kaynağı duvar kağıdı yap
uv run canard --mode wallpaper --boot-file tools/mscillus-boot-system.js   # mscillus + sistem sesi
```

- **Sistem sesi (macOS 14.2+):** içerik `getDisplayMedia({audio: true})` istediğinde motor sistem sesini verir; mscillus'un "Sistem sesi" kartı değişiklik gerekmeden çalışır.
- **Duvar kağıdı kısayolları:** `⌃⌥⌘←/→` içeriğe ←/→ tuşu gönderir, `⌃⌥⌘↑` etkileşimli modu açar/kapatır. Örtülünce (tam ekran uygulama) içerik duraklatılır.

Tüm komutlar, örnekler ve sorun giderme: **[docs/KULLANIM.md](docs/KULLANIM.md)** · Yetki ve izinler: **[docs/IZINLER.md](docs/IZINLER.md)**

## Lisans

GPL-3.0 — bkz. [LICENSE](LICENSE)
