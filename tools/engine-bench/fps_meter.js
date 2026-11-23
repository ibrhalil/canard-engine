// Motor kıyası (tools/engine-bench): sayfanın gerçek çizim hızını requestAnimationFrame ile ölçer, saniyede bir bildirir.
// WKWebView'da webkit.messageHandlers.bench, QtWebEngine'de konsol ("[canard-bench] …") üzerinden.
(function () {
  if (window.__canardBench) return;
  window.__canardBench = true;
  var handler = window.webkit && window.webkit.messageHandlers && window.webkit.messageHandlers.bench;
  var send = function (message) {
    if (handler) handler.postMessage(message);
    else console.log('[canard-bench] ' + message);
  };
  var frames = 0, worst = 0, last = performance.now(), start = last;
  function tick(now) {
    var delta = now - last;
    last = now;
    frames++;
    if (delta > worst) worst = delta;
    if (now - start >= 1000) {
      // En büyük tuvalin gerçek piksel boyutu: iki motorun aynı çözünürlükte çizdiğini doğrulamak için.
      var canvas = '0x0', area = 0;
      document.querySelectorAll('canvas').forEach(function (c) {
        if (c.width * c.height > area) { area = c.width * c.height; canvas = c.width + 'x' + c.height; }
      });
      send('fps=' + (frames * 1000 / (now - start)).toFixed(1) + ' worst_ms=' + worst.toFixed(1) +
           ' canvas=' + canvas + ' dpr=' + window.devicePixelRatio + (document.hidden ? ' hidden=1' : ''));
      frames = 0;
      worst = 0;
      start = now;
    }
    requestAnimationFrame(tick);
  }
  requestAnimationFrame(tick);
})();
