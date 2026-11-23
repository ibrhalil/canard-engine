// Motor kıyası boot betiği (sayfa yüklendikten sonra): mscillus'ta mikrofon kaynağını seçer ve açık modu saniyede
// bir bildirir (sonuç tablosunda etiket yerine gerçek mod görünsün). Ayarlar ve mod bench-prelude.js'te verilir.
(function () {
  var handler = window.webkit && window.webkit.messageHandlers && window.webkit.messageHandlers.bench;
  var send = function (message) {
    if (handler) handler.postMessage(message);
    else console.log('[canard-bench] ' + message);
  };

  var tries = 0;
  var timer = setInterval(function () {
    var cards = document.querySelectorAll('.picker__cards .card');
    if (cards.length >= 4) {
      clearInterval(timer);
      cards[3].click();
      console.log('[canard-boot] mscillus: mikrofon kaynağı seçildi');
      setInterval(function () { send('mode=' + sessionStorage.getItem('mscillus.lastMode')); }, 1000);
      return;
    }
    tries += 1;
    if (tries > 100) {
      clearInterval(timer);
      console.log('[canard-boot] mscillus: kaynak kartları bulunamadı');
    }
  }, 200);
})();
