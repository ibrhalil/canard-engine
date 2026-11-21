# Yetki ve İzinler

Canard'ın hangi sistem izinlerini neden kullandığı, içeriğe hangi yetkileri verdiği ve nelerin nerede saklandığı. Şu an yalnızca **macOS** desteklenir; Windows/Linux bölümleri ilgili fazlarda eklenecek.

## İzin kime verilir?

Canard şimdilik terminalden (`uv run canard`) çalışır. macOS izinleri **Canard'a değil, onu başlatan terminal uygulamasına** (iTerm, Terminal, Warp…) verilir. Bu yüzden bir izni açtıktan sonra **terminali kapatıp yeniden açmak** gerekebilir.

Faz 6'da paketlenmiş `Canard.app` ile izinler doğrudan Canard'a bağlanacak ve izin pencerelerinde kendi açıklamaları görünecek.

## macOS sistem izinleri

| İzin | Ne zaman gerekir | Gerekli mi? | Ayar yeri |
|---|---|---|---|
| **Mikrofon** | İçerik mikrofon istediğinde (ör. mscillus "Mikrofon" kaynağı, BlackHole gibi sanal giriş aygıtları) | Yalnız mikrofon kaynağı için | Sistem Ayarları › Gizlilik ve Güvenlik › **Mikrofon** |
| **Sistem sesi kaydı** | İçerik sistem sesi istediğinde (ör. mscillus "Sistem sesi"); Core Audio tap ile — **macOS 14.2+** | Sistem sesi için | Sistem Ayarları › Gizlilik ve Güvenlik › **Ekran ve Sistem Sesi Kaydı** › *Yalnızca Sistem Sesi Kaydı* |
| **Ekran kaydı** | Yalnız içerik *görüntülü* ekran paylaşımı isterse ve `tools/audio-probe/sck_probe.py` deneme betiği için. Sistem sesi bu izni **gerektirmez** | Normal kullanımda hayır | Sistem Ayarları › Gizlilik ve Güvenlik › **Ekran ve Sistem Sesi Kaydı** |
| **Kamera** | Yalnız içerik kamera isterse | Normal kullanımda hayır | Sistem Ayarları › Gizlilik ve Güvenlik › **Kamera** |
| Erişilebilirlik | **Gerekmez.** Global kısayollar Carbon hotkey ile çalışır; klavye dinlenmez, yalnız kayıtlı tuş kombinasyonları alınır | — | — |

Sistem sesi yakalanırken macOS menü çubuğunda bir kayıt göstergesi görünebilir. Canard yakalamayı yalnızca bir sayfa sistem sesi istiyorsa ve duvar kağıdı görünürken açık tutar: içerik durunca, duvar kağıdı örtülünce (tam ekran uygulama) ya da Canard kapanınca yakalama tamamen kapanır.

### İzni geri almak / sıfırlamak

Ayarlar ekranındaki listeden terminal uygulamasının anahtarını kapatmak yeterlidir. Terminalden sıfırlamak için (iTerm örneği; Terminal için `com.apple.Terminal`):

```bash
tccutil reset Microphone com.googlecode.iterm2
tccutil reset ScreenCapture com.googlecode.iterm2
```

Bir sonraki kullanımda macOS izni yeniden sorar.

## İçeriğe verilen yetkiler

Canard, yüklediği web içeriğine (uzak URL, yerel build, dev sunucusu) şu kararları **otomatik** uygular (`canard/web/policy.py`):

| Web izni | Karar |
|---|---|
| Mikrofon, kamera, mikrofon+kamera | İzin verilir |
| Ekran paylaşımı (`getDisplayMedia`) | İzin verilir; ilk ekran otomatik seçilir |
| Sistem sesi (`getDisplayMedia({audio: true})`) | Motorun yakaladığı sistem sesi verilir (`--no-system-audio` ile kapatılır) |
| Bildirim, konum, pano, diğerleri | Reddedilir |
| Ses/video otomatik oynatma | Kullanıcı etkileşimi beklemeden izinli (duvar kağıdında tıklanacak kimse yok) |

> ⚠️ **Güvenlik notu:** Bu kararlar duvar kağıdı içeriğinin kendi kendine başlayabilmesi için gevşektir. Canard'a yalnızca **güvendiğin** kaynakları ekle: eklediğin bir site, izin sormadan mikrofonu ve sistem sesini dinleyebilir. Kaynak başına izin ayarı Faz 5'te (ayarlar) ele alınacak.

Sistem sesi sayfaya Qt'nin kendi köprüsüyle (QWebChannel) iletilir: açık bir ağ portu yoktur, başka bir uygulama ya da tarayıcıdaki bir site bu sese erişemez.

## Ağ

- **Uzak kaynaklar:** Yalnız eklediğin URL'ye bağlanılır.
- **Yerel build (`--path`):** Klasör, yalnızca bu bilgisayardan erişilebilen `127.0.0.1` üzerinde sunulur; klasör dışına çıkan istekler reddedilir. Port ilk açılışta seçilip kaynağa kaydedilir ve sonraki açılışlarda aynı port kullanılır. İçeriğin localStorage gibi yerel verileri adrese (port dahil) bağlı olduğu için bu, ayarların korunmasını sağlar.
- Canard hiçbir telemetri ya da kullanım verisi göndermez.

## Saklanan veriler

| Ne | Nerede (macOS) |
|---|---|
| Kaynak listesi, aktif kaynak, boot script'ler | `~/Library/Application Support/Canard/Canard/settings.json` |
| İçeriğin localStorage, çerez ve önbelleği | `~/Library/Application Support/Canard/Canard/webprofile/` |

Hepsini sıfırlamak için Canard kapalıyken:

```bash
rm -rf ~/Library/Application\ Support/Canard
```

## Global kısayollar

Duvar kağıdı modu çalışırken şu kombinasyonlar **tüm sistemde** Canard'a ayrılır ve diğer uygulamalara ulaşmaz:

| Kısayol | Görev |
|---|---|
| `⌃⌥⌘←` / `⌃⌥⌘→` | İçeriğe ← / → tuşu gönderir (mscillus'ta önceki/sonraki mod) |
| `⌃⌥⌘↑` | Etkileşimli mod aç/kapat |

`⌘⌥←/→` JetBrains IDE'ler ve Chrome ile çakıştığı için `⌃⌥⌘` seçildi. Canard kapanınca kısayollar serbest kalır.
