// Sayfa konsolu → Canard logu (WKWebView'da Chromium'daki javaScriptConsoleMessage karşılığı yok). Saniyede en çok
// 50 mesaj; yakalanmayan hatalar ve reddedilen sözler de gider. Motor tarafı: webkit.messageHandlers.canardConsole.
(function () {
  var handler = window.webkit && window.webkit.messageHandlers && window.webkit.messageHandlers.canardConsole;
  if (!handler) return;
  var budget = 50;
  var windowStart = Date.now();
  var text = function (value) {
    if (typeof value === 'string') return value;
    if (value instanceof Error) return value.stack ? value.message + '\n' + value.stack : String(value);
    try {
      return JSON.stringify(value);
    } catch (error) {
      return String(value);
    }
  };
  var send = function (level, args) {
    var now = Date.now();
    if (now - windowStart > 1000) {
      budget = 50;
      windowStart = now;
    }
    if (budget-- <= 0) return;
    try {
      handler.postMessage({ level: level, text: Array.prototype.map.call(args, text).join(' ').slice(0, 4000) });
    } catch (error) {}
  };
  ['debug', 'log', 'info', 'warn', 'error'].forEach(function (level) {
    var original = console[level];
    console[level] = function () {
      send(level, arguments);
      return original.apply(console, arguments);
    };
  });
  window.addEventListener('error', function (event) {
    send('error', [event.message + (event.filename ? '  (' + event.filename + ':' + event.lineno + ')' : '')]);
  });
  window.addEventListener('unhandledrejection', function (event) {
    send('error', ['Unhandled rejection: ' + text(event.reason)]);
  });
})();
