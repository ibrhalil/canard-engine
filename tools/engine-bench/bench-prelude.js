// Motor kıyası ön betiği: belge oluşturulurken, mscillus'un kodundan önce çalışır (qt_bench/WebKitBench/spike
// --prelude). Ayarları ve modu mscillus ilk okumada buradan alır; sayfa kapanırken (pagehide) bellekteki ayarları
// yeniden yazdığı için ayarlar yüklemeden sonra verilirse kaybolur (2026-11-23 Qt ölçümlerinde oldu).
// suite.sh başa `var CANARD_BENCH = {settings, mode}` ekler: settings verilirse ayarların tamamı (mod seçenekleri
// dahil) onunla değiştirilir, mode mscillus mod kimliğidir (ör. "fire").
// Sabitlenenler: otomatik çözünürlük ("auto") FPS düşünce çizimi küçültür ve iki motor farklı çözünürlükte çizip
// kıyası bozar → "high"; kare sınırı yok; otomatik mod değiştirme ve FPS göstergesi kapalı.
(function () {
  var config = typeof CANARD_BENCH !== 'undefined' ? CANARD_BENCH : {};
  var KEY = 'mscillus.settings.v1';
  var pin = { resolution: 'high', fpsCap: 0, autoCycle: false, showFps: false };
  try {
    var settings = config.settings
      ? JSON.parse(JSON.stringify(config.settings))
      : JSON.parse(localStorage.getItem(KEY) || '{}') || {};
    Object.keys(pin).forEach(function (k) { settings[k] = pin[k]; });
    localStorage.setItem(KEY, JSON.stringify(settings));
    if (config.mode) sessionStorage.setItem('mscillus.lastMode', config.mode);
  } catch (e) {
    console.log('[canard-boot] kıyas ayarları yazılamadı: ' + e);
  }
})();
