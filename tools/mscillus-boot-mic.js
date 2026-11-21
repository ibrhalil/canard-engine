(function () {
  var tries = 0;
  var timer = setInterval(function () {
    var cards = document.querySelectorAll('.picker__cards .card');
    if (cards.length >= 4) {
      clearInterval(timer);
      cards[3].click();
      console.log('[canard-boot] mscillus: mikrofon kaynağı seçildi');
      return;
    }
    tries += 1;
    if (tries > 100) {
      clearInterval(timer);
      console.log('[canard-boot] mscillus: kaynak kartları bulunamadı');
    }
  }, 200);
})();
