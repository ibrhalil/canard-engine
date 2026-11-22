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
      send('fps=' + (frames * 1000 / (now - start)).toFixed(1) + ' worst_ms=' + worst.toFixed(1));
      frames = 0;
      worst = 0;
      start = now;
    }
    requestAnimationFrame(tick);
  }
  requestAnimationFrame(tick);
})();
