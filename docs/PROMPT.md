# Masaüstü Web İçerik Motoru — Proje Promptu

- Kaynak: sahibin 2026-11-20'de yapıştırdığı iki prompt; metin aşağıda aynen korunur.
- Bu dosya projenin gereksinim sözleşmesidir; kararlar ve durum `PLAN.md` defterinde tutulur.
- Motivasyon örneği: `~/dev/IdeaProjects/mscillus` (React + Vite müzik görselleştirici; XP dönemi müzikli ekran koruyucu/MilkDrop hissi).

---

# Proje: Cross-Platform Interactive Wallpaper & Screensaver

## Rolün

Bu projede kıdemli yazılım mimarı ve teknik lider olarak hareket etmeni istiyorum. Amacımız Windows, macOS ve Linux üzerinde çalışabilen, web tabanlı görselleştirmeleri masaüstü deneyimine dönüştüren bağımsız ve genişletilebilir bir uygulama geliştirmek.

Sadece çalışan bir prototip değil, ileride farklı görselleştirmelerin ve etkileşimlerin eklenebileceği, sürdürülebilir bir ürün altyapısı oluşturmalıyız.

## 1. Projenin amacı

Elimde React + Vite ile geliştirilmiş, localhost üzerinden çalışan ve müzik ritmine göre gerçek zamanlı animasyonlar oluşturan bir web uygulaması var.

Bu uygulamanın mevcut görselleştirme kabiliyetlerini koruyarak iki temel çalışma modu kazandırmak istiyorum:

### A. Interactive Live Wallpaper (Canlı ve Etkileşimli Duvar Kağıdı)

Uygulama masaüstü arka planında, masaüstü simgelerinin arkasında çalışabilmeli.

Beklentiler:

- Masaüstünde sürekli animasyon gösterebilmeli.
- React, Canvas, WebGL ve CSS animasyonlarını desteklemeli.
- Müzik ritmine göre gerçek zamanlı görselleştirme yapabilmeli.
- Fare hareketlerine ve kullanıcı etkileşimlerine tepki verebilmeli.
- Etkileşimli ve etkileşimsiz çalışma seçenekleri sunmalı.
- Çoklu monitör desteği olmalı.
- Her monitör için farklı görselleştirme veya ayarlar seçilebilmeli.
- Performans yönetimi yapılmalı.
- Tam ekran oyun veya uygulama çalışırken otomatik duraklatma seçeneği bulunmalı.
- Bilgisayar pilde çalışırken performans ve enerji tüketimi ayarlanabilmeli.
- Masaüstü kullanımını ve diğer uygulamaları olumsuz etkilememeli.

Burada klasik tam ekran uygulama istemiyorum. Gerçek anlamda masaüstü arka planına yerleşen canlı duvar kağıdı deneyimi istiyorum.

### B. Screensaver (Ekran Koruyucu)

Aynı görselleştirme motoru ekran koruyucu olarak da çalışabilmeli.

Beklentiler:

- Kullanıcı belirlenen süre boyunca bilgisayarı kullanmadığında otomatik başlamalı.
- Tam ekran görüntülenmeli.
- Fare veya klavye hareketiyle sonlandırılmalı.
- Birden fazla monitörü desteklemeli.
- Monitörlerin çözünürlüğüne ve yönüne uyum sağlamalı.
- Kullanıcı ayarlarından başlama süresi belirlenebilmeli.
- Önizleme modu bulunmalı.
- Sistem kilit ekranının güvenliğini veya işletim sistemi davranışlarını bozmamalı.

Duvar kağıdı ve ekran koruyucu aynı görselleştirme motorunu kullanmalı. İki ayrı uygulama geliştirmek istemiyorum.

## 2. Teknoloji ve mimari beklentileri

Python tabanlı ortak bir uygulama çekirdeği istiyorum.

İlk değerlendirilmesini istediğim teknolojiler:

- Python: Uygulama mantığı ve orkestrasyon.
- PySide6 / Qt: Masaüstü arayüzü ve pencere yönetimi.
- Qt WebEngine: Mevcut React uygulamasının çalıştırılması.
- React + Vite: Görselleştirme ve etkileşim katmanı.
- Platform adapters: İşletim sistemine özel entegrasyonlar.

Ancak bu teknolojileri sorgulamadan kabul etme. Özellikle gerçek masaüstü duvar kağıdı entegrasyonunda Qt'nin yeterli olup olmadığını araştır.

Gerekli durumlarda Swift, Objective-C, C++, C# veya işletim sistemine özel küçük yerel bileşenler kullanılabilir.

Önemli olan Python tabanlı ortak çekirdeğin korunmasıdır.

## 3. Platform desteği

Üç işletim sistemini hedefliyoruz:

| Platform | Live Wallpaper | Screensaver |
|---|---|---|
| Windows 10/11 | Gerçek masaüstü entegrasyonu | Yerel ekran koruyucu entegrasyonu |
| macOS | Gerçek masaüstü entegrasyonu | Yerel ekran koruyucu entegrasyonu |
| Linux | X11 ve Wayland değerlendirmesi | Masaüstü ortamına uygun entegrasyon |

Her platformda aynı kod tabanını kullanmak istiyoruz fakat işletim sistemlerinin farklılıklarını gizlememeliyiz.

Özellikle şu konuları araştır:

- Windows masaüstü WorkerW / Progman entegrasyonu ve alternatifleri.
- Windows `.scr` ekran koruyucu mekanizması.
- macOS masaüstü arka planına yerleşme yöntemleri.
- macOS `ScreenSaverView` ve güncel ekran koruyucu altyapısı.
- Linux X11 masaüstü entegrasyonu.
- Linux Wayland ve masaüstü ortamlarının sınırlamaları.
- GNOME, KDE Plasma gibi masaüstü ortamları arasındaki farklılıklar.

Bir platformda gerçek entegrasyon mümkün değilse bunu açıkça belirt. Tam ekran pencereyi gerçek duvar kağıdı gibi göstermeye çalışma.

## 4. Ses ve müzik analizi

Mevcut React projemin önemli özelliklerinden biri müziğe göre animasyon üretmesi.

Bu yeteneği kaybetmemeliyiz.

Aşağıdaki ses kaynaklarını değerlendirmek istiyorum:

1. Uygulamanın kendi içerisinden çalınan müzik.
2. Mikrofon üzerinden alınan ses.
3. İşletim sisteminin çıkış sesini (system audio / loopback) yakalama.
4. İleride Spotify veya diğer müzik servisleriyle entegrasyon.

Ses analizinde mümkün olduğunca mevcut React uygulamasının Audio API altyapısını koru.

Ancak sistem sesini yakalamak gerektiğinde platforma özel çözümleri değerlendir:

- Windows: WASAPI loopback.
- macOS: ScreenCaptureKit veya uygun ses yakalama API'leri.
- Linux: PipeWire / PulseAudio.

İzinler, güvenlik ve gecikme konularını araştır.

## 5. Uygulama mimarisi

Aşağıdaki yapıyı başlangıç önerisi olarak değerlendir:

```text
interactive-desktop/
│
├── app/
│   ├── main.py
│   ├── application.py
│   ├── settings.py
│   └── lifecycle.py
│
├── core/
│   ├── wallpaper.py
│   ├── screensaver.py
│   ├── display_manager.py
│   ├── audio_manager.py
│   └── performance_manager.py
│
├── web/
│   ├── webview.py
│   └── bridge.py
│
├── platform/
│   ├── windows/
│   ├── macos/
│   └── linux/
│
├── ui/
│   ├── settings/
│   └── preview/
│
├── assets/
│
├── tests/
│
└── pyproject.toml
```

Bu yapı kesin bir zorunluluk değil. Gerekirse daha doğru bir mimari öner.

Temel prensipler:

- Platform bağımlılıkları ayrı adapter katmanlarında tutulmalı.
- Görselleştirme motoru işletim sisteminden bağımsız olmalı.
- Duvar kağıdı ve ekran koruyucu aynı renderer altyapısını kullanmalı.
- React ile Python arasındaki iletişim kontrollü bir bridge üzerinden yapılmalı.
- Ayarlar kalıcı olarak saklanmalı.
- Yeni görselleştirmeler plugin veya benzeri genişletilebilir bir yapı üzerinden eklenebilmeli.
- İleride birden fazla web tabanlı görselleştirme desteklenebilmeli.

## 6. Performans ve kaynak tüketimi

Bu uygulamanın masaüstünde uzun süre açık kalacağını unutma.

Özellikle:

- CPU ve GPU tüketimini düşük tut.
- Gereksiz render işlemlerini azalt.
- WebView bellek tüketimini takip et.
- Animasyon FPS sınırı belirlenebilsin.
- Kullanıcı bilgisayarı boşta bırakmadığında wallpaper modunda gereksiz iş yapılmasın.
- Ekran kapandığında veya tam ekran uygulama açıldığında duraklatma seçenekleri olsun.
- Çoklu monitörde her ekran için kaynak kullanımını yönet.
- Uyku, hazırda bekletme ve yeniden başlatma senaryolarını test et.

## 7. Güvenlik ve dağıtım

- Yerel React uygulamasının production build'ini kullanmayı tercih et.
- Geliştirme sırasında localhost URL'si seçilebilir olsun.
- İnternet bağlantısı olmadan çalışabilmeli.
- Gereksiz ağ erişimi yapılmamalı.
- Mikrofon ve sistem sesi izinleri açıkça yönetilmeli.
- WebView içerisinde gereksiz JavaScript köprüleri açılmamalı.
- Üçüncü taraf ekran koruyucu paketlerine körü körüne güvenilmemeli.
- Kod imzalama, notarization ve işletim sistemi güvenlik mekanizmaları araştırılmalı.
- Gatekeeper veya diğer güvenlik kontrollerini devre dışı bırakan yöntemler önerilmemeli.

## 8. Arayüz beklentileri

İlerleyen aşamada aşağıdaki özelliklere sahip basit bir yönetim arayüzü istiyorum:

- Wallpaper / Screensaver modu seçimi.
- Görselleştirme seçimi.
- Animasyon önizlemesi.
- Monitör seçimi.
- FPS ve kalite ayarları.
- Ses kaynağı seçimi.
- Ses hassasiyeti ve analiz ayarları.
- Otomatik duraklatma ayarları.
- Ekran koruyucu başlama süresi.
- Başlangıçta otomatik çalıştırma.

İlk aşamada kapsamlı bir ayar arayüzü geliştirme. Önce çalışan altyapıyı oluştur.

## 9. Çalışma yöntemin

Geliştirmeye hemen kod yazarak başlama.

Önce:

1. Mevcut React + Vite projesini incele.
2. Animasyonun nasıl çalıştığını ve ses analizinin nasıl yapıldığını tespit et.
3. Mevcut bağımlılıkları ve teknik kısıtları belirle.
4. Python + PySide6 + Qt WebEngine yaklaşımının uygunluğunu araştır.
5. Üç işletim sistemi için gerçek wallpaper ve screensaver entegrasyonlarını ayrı ayrı araştır.
6. Alternatif teknolojilerle karşılaştırma yap.
7. Teknik riskleri ve uygulanabilir olmayan özellikleri açıkça raporla.

Araştırmanda güncel resmi dokümantasyonları, işletim sistemi API'lerini ve ilgili açık kaynak projeleri kullan.

Ardından şu çıktıları hazırla:

- Önerilen mimari.
- Teknoloji seçimlerinin gerekçeleri.
- Platform uyumluluk matrisi.
- Proje klasör yapısı.
- Geliştirme aşamaları.
- Test stratejisi.
- Bilinen teknik riskler.

## 10. Geliştirme aşamaları

Geliştirmeyi küçük ve doğrulanabilir aşamalara böl:

**Faz 1 — Web Renderer**

Mevcut React uygulamasını Python masaüstü uygulaması içerisinde çalıştır. Animasyon ve ses analizinin çalıştığını doğrula.

**Faz 2 — Desktop Wallpaper**

Windows, macOS ve Linux için masaüstü arka planına gerçek entegrasyonu ayrı ayrı gerçekleştir. İlk olarak bir platformda çalışan prototip oluştur.

**Faz 3 — Screensaver**

Boşta kalma algılama, tam ekran görüntüleme, çoklu monitör ve kullanıcı hareketiyle çıkış özelliklerini ekle.

**Faz 4 — Audio Integration**

Mikrofon, uygulama sesi ve sistem sesi desteğini geliştir.

**Faz 5 — Settings & Performance**

Ayar arayüzü, performans optimizasyonu ve otomatik duraklatma sistemini tamamla.

**Faz 6 — Packaging & Distribution**

Üç platform için kurulum paketleri, otomatik başlatma, imzalama ve dağıtım süreçlerini hazırla.

Her fazın sonunda test sonuçlarını ve kalan sorunları raporla.

---

## 11. Temel kararlarımız

Aşağıdaki prensipleri proje boyunca koru:

- Mevcut React projesini yeniden yazma.
- Görselleştirme motorunu Python içerisine taşımaya çalışma.
- Üç platformu tek bir teknolojiye zorlayarak gerçek entegrasyon özelliklerinden vazgeçme.
- Gerekli yerde yerel kod kullanmaktan çekinme.
- Önce çalışan ve test edilebilir bir çekirdek oluştur.
- Gereksiz özellik ve bağımlılık ekleme.
- Her aşamada çalışan yazılımı koru.
- Teknik varsayımları doğrulamadan mimari karar alma.

**Nihai hedef:** Kullanıcının kendi web tabanlı animasyonlarını ve görselleştirmelerini hem interaktif canlı duvar kağıdı hem de otomatik ekran koruyucu olarak kullanabildiği, Windows, macOS ve Linux destekli, açık ve genişletilebilir bir masaüstü uygulaması geliştirmek.

---

## 12. Generic Web Content Engine — Kritik Gereksinim

Uygulama yalnızca belirli bir React projesini çalıştıran bir wrapper olmamalıdır.

Genel amaçlı bir Web Content Engine geliştirmeliyiz.

Kullanıcı aşağıdaki kaynaklardan herhangi birini seçerek uygulamayı wallpaper, screensaver veya preview modunda çalıştırabilmeli:

1. **Remote Website:** Herhangi bir HTTP/HTTPS web adresi.
2. **Local Web Build:** React, Vue, Angular, Svelte veya düz HTML/CSS/JavaScript projelerinin production build çıktısı.
3. **Local Development Server:** localhost üzerinden çalışan web uygulamaları.
4. **Bundled Content:** Daha sonra uygulama içerisine eklenebilecek hazır web tabanlı görselleştirme paketleri.

Her içerik türü ortak renderer üzerinden çalıştırılmalı.

Gereksinimler:

- Kullanıcı kaynak ekleyebilmeli, kaldırabilmeli ve düzenleyebilmeli.
- Her içerik için ayrı isim ve ayar saklanabilmeli.
- URL ve yerel build kaynakları desteklenmeli.
- Build klasörleri için güvenilir yerel sunum mekanizması oluşturulmalı.
- SPA routing, asset resolution ve relative path sorunları ele alınmalı.
- WebView cookie, cache ve profil yönetimi kontrollü olmalı.
- İnternet erişimi olmayan içerikler offline çalışabilmeli.
- Kaynak yüklenemediğinde hata ekranı ve yeniden deneme mekanizması bulunmalı.
- WebView navigasyonu, izinler ve JavaScript bridge güvenlik politikaları tanımlanmalı.
- Mümkün olduğunca standart web teknolojileri kullanılmalı.
- Chromium/WebView uyumluluğu olmayan özellikler raporlanmalı.

**Önemli:** Kullanıcının mevcut React projesine bağımlı bir mimari kurma. O proje yalnızca bu motorun çalıştıracağı örnek içeriklerden biri olmalıdır.

Nihai ürün, web tabanlı içerikleri işletim sisteminin masaüstü arka planında veya ekran koruyucusunda çalıştırabilen genel amaçlı bir motor olmalıdır.
