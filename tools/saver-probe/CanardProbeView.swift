// Faz 3a spike B1 — WKWebView macOS 27'de legacyScreenSaver içinde çalışıyor mu?
// Ölçülen: requestAnimationFrame hızı, setInterval hızı, WebGL çizimi, içeriğin süreklilik (3 sn sonra
// kaybolma hatası). Sonuçlar hem ekranda hem sistem günlüğünde ("CanardProbe" ile) görünür.
import ScreenSaver
import WebKit
import os

private let logger = Logger(subsystem: "dev.canard.probe", category: "saver")

private let testPage = """
<!doctype html><html><head><meta charset="utf-8"><style>
html,body{margin:0;height:100%;background:#06070d;color:#e8e8ff;font:28px -apple-system,Helvetica;overflow:hidden}
#hud{position:absolute;left:40px;top:40px;line-height:1.5;white-space:pre}
canvas{position:absolute;right:60px;bottom:60px;width:360px;height:360px}
#bar{position:absolute;top:0;width:60px;height:100%;background:#ff50a0}
</style></head><body><div id="bar"></div><div id="hud">başlıyor…</div><canvas id="gl" width="360" height="360"></canvas>
<script>
const t0 = performance.now(); let raf = 0, iv = 0, rafTotal = 0, ivTotal = 0, glOk = false, x = 0;
const bar = document.getElementById('bar'), hud = document.getElementById('hud');
const gl = document.getElementById('gl').getContext('webgl');
let prog = null;
if (gl) {
  const sh = (t, s) => { const o = gl.createShader(t); gl.shaderSource(o, s); gl.compileShader(o); return o; };
  prog = gl.createProgram();
  gl.attachShader(prog, sh(gl.VERTEX_SHADER, 'attribute vec2 p;uniform float a;void main(){float c=cos(a),s=sin(a);gl_Position=vec4(p.x*c-p.y*s,p.x*s+p.y*c,0,1);}'));
  gl.attachShader(prog, sh(gl.FRAGMENT_SHADER, 'precision mediump float;void main(){gl_FragColor=vec4(.3,.8,1,1);}'));
  gl.linkProgram(prog); gl.useProgram(prog);
  gl.bindBuffer(gl.ARRAY_BUFFER, gl.createBuffer());
  gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([0,.8,-.7,-.6,.7,-.6]), gl.STATIC_DRAW);
  gl.enableVertexAttribArray(0); gl.vertexAttribPointer(0, 2, gl.FLOAT, false, 0, 0);
  glOk = true;
}
function frame(now) {
  raf++; rafTotal++; x = (x + 6) % innerWidth; bar.style.left = x + 'px';
  if (glOk) { gl.clearColor(0,0,0,0); gl.clear(gl.COLOR_BUFFER_BIT); gl.uniform1f(gl.getUniformLocation(prog,'a'), now/600); gl.drawArrays(gl.TRIANGLES,0,3); }
  requestAnimationFrame(frame);
}
requestAnimationFrame(frame);
setInterval(() => { iv++; ivTotal++; }, 100);
setInterval(() => {
  const s = ((performance.now() - t0) / 1000).toFixed(0);
  const line = `geçen ${s} sn · rAF/sn ${raf} · setInterval(100ms)/sn ${iv} (beklenen 10) · WebGL ${glOk ? 'var' : 'YOK'}`;
  hud.textContent = 'Canard WKWebView .saver testi\\n' + line.replaceAll(' · ', '\\n');
  try { webkit.messageHandlers.probe.postMessage(line); } catch (e) {}
  raf = 0; iv = 0;
}, 1000);
</script></body></html>
"""

@objc(CanardProbeView)
final class CanardProbeView: ScreenSaverView, WKScriptMessageHandler {
    private var webView: WKWebView?

    override init?(frame: NSRect, isPreview: Bool) {
        super.init(frame: frame, isPreview: isPreview)
        animationTimeInterval = 1.0 / 30.0
        logger.log("CanardProbe init preview=\(isPreview, privacy: .public) frame=\(NSStringFromRect(frame), privacy: .public)")
        let config = WKWebViewConfiguration()
        config.userContentController.add(self, name: "probe")
        // Deneme: tam ekran ekran koruyucuda WebKit sayfayı "etkin değil" sayıp rAF'ı 0'a, zamanlayıcıları
        // ~1-2/sn'ye kısıyor (B1 ilk ölçüm). macOS 14+ herkese açık ayar: etkin değilken de kısma.
        config.preferences.inactiveSchedulingPolicy = .none
        let web = WKWebView(frame: bounds, configuration: config)
        web.autoresizingMask = [.width, .height]
        addSubview(web)
        web.loadHTMLString(testPage, baseURL: nil)
        webView = web
    }

    required init?(coder: NSCoder) {
        super.init(coder: coder)
    }

    deinit {
        logger.log("CanardProbe deinit")
    }

    override func layout() {
        super.layout()
        webView?.frame = bounds  // ikinci ekrandaki kenar boşluğu: görünüm ekranı tam kaplasın
    }

    override func startAnimation() {
        super.startAnimation()
        logger.log("CanardProbe startAnimation")
    }

    override func stopAnimation() {
        super.stopAnimation()
        logger.log("CanardProbe stopAnimation")
    }

    func userContentController(_ controller: WKUserContentController, didReceive message: WKScriptMessage) {
        logger.log("CanardProbe \(String(describing: message.body), privacy: .public)")
    }
}
