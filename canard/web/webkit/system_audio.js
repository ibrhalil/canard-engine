// Canard sistem sesi köprüsü, WebKit sürümü: getDisplayMedia({audio: true}) motorun yakaladığı sistem sesini taşıyan
// bir MediaStream döndürür, içerik değişiklik gerektirmez. Chromium sürümünün (canard/web/chromium/system_audio.js)
// dayandığı MediaStreamTrackGenerator WebKit'te ses için yok; burada AudioWorklet + MediaStreamAudioDestinationNode.
// Motor tarafı (canard/web/webkit/engine.py): webkit.messageHandlers.canardAudio ← {cmd: start|stop|stats};
// → window.__canardAudio.started(bilgi) / push(base64 float32 PCM) / format(bilgi).
(function () {
  var mediaDevices = navigator.mediaDevices;
  var handler = window.webkit && window.webkit.messageHandlers && window.webkit.messageHandlers.canardAudio;
  if (!mediaDevices || !handler || typeof AudioWorkletNode !== 'function') return;
  var originalGetDisplayMedia = mediaDevices.getDisplayMedia ? mediaDevices.getDisplayMedia.bind(mediaDevices) : null;

  // İşleyici: gelen PCM parçalarını sıraya alır, render kuantumu başına (128 kare) çıkışa yazar. Çıkış aygıtı değişip
  // örnekleme hızı bağlamınkinden farklılaşırsa en yakın örnekle yeniden örnekler (görselleştirme için yeterli).
  var PROCESSOR = [
    'class CanardFeed extends AudioWorkletProcessor {',
    '  constructor(options) {',
    '    super();',
    '    this.setFormat(options.processorOptions);',
    '    this.queue = []; this.position = 0; this.underruns = 0; this.dropped = 0; this.peak = 0;',
    '    this.port.onmessage = (event) => {',
    '      const data = event.data;',
    '      if (data === "stats") {',
    '        this.port.postMessage({underruns: this.underruns, dropped: this.dropped, queued: this.queue.length,',
    '                               peak: Math.round(this.peak * 1000) / 1000});',
    '        this.underruns = this.dropped = this.peak = 0;',
    '        return;',
    '      }',
    '      if (data && data.format) { this.setFormat(data.format); return; }',
    '      this.queue.push(data);',
    '      // Sayfa yetişemezse gecikme birikmesin (~120 ms üstü atılır); görselleştirmede tazelik önemli.',
    '      if (this.queue.length > 6) {',
    '        this.dropped += this.queue.length - 2;',
    '        this.queue.splice(0, this.queue.length - 2);',
    '        this.position = 0;',
    '      }',
    '    };',
    '  }',
    '  setFormat(format) {',
    '    this.channels = format.channels;',
    '    this.interleaved = format.interleaved;',
    '    this.step = format.sampleRate / sampleRate;',
    '  }',
    '  process(inputs, outputs) {',
    '    const out = outputs[0], n = out[0].length, ch = this.channels;',
    '    let missing = false;',
    '    for (let i = 0; i < n; i++) {',
    '      let buf = this.queue[0];',
    '      while (buf && this.position >= buf.length / ch) {',
    '        this.position -= buf.length / ch; this.queue.shift(); buf = this.queue[0];',
    '      }',
    '      if (!buf) { for (let c = 0; c < out.length; c++) out[c][i] = 0; missing = true; continue; }',
    '      const frames = buf.length / ch, frame = Math.floor(this.position);',
    '      for (let c = 0; c < out.length; c++) {',
    '        const sc = Math.min(c, ch - 1);',
    '        const v = this.interleaved ? buf[frame * ch + sc] : buf[sc * frames + frame];',
    '        out[c][i] = v;',
    '        if (v > this.peak) this.peak = v;',
    '      }',
    '      this.position += this.step;',
    '    }',
    '    if (missing) this.underruns++;',
    '    return true;',
    '  }',
    '}',
    'registerProcessor("canard-feed", CanardFeed);',
  ].join('\n');

  var pipeline = null;
  var pendingStart = null;

  var decode = function (b64) {
    var binary = atob(b64);
    var bytes = new Uint8Array(binary.length);
    for (var i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
    return new Float32Array(bytes.buffer);
  };

  Object.defineProperty(window, '__canardAudio', {
    value: Object.freeze({
      started: function (info) {
        var resolve = pendingStart;
        pendingStart = null;
        if (resolve) resolve(info);
      },
      push: function (b64) {
        if (pipeline && pipeline.node) {
          var samples = decode(b64);
          pipeline.node.port.postMessage(samples, [samples.buffer]);
        }
      },
      format: function (info) {
        if (pipeline && pipeline.node) pipeline.node.port.postMessage({ format: info });
      },
    }),
  });

  var startPipeline = function () {
    if (pipeline) return pipeline.ready;
    var p = (pipeline = { refs: 0 });
    p.ready = new Promise(function (resolve) {
      pendingStart = resolve;
      handler.postMessage({ cmd: 'start' });
    }).then(function (info) {
      if (!info || !info.ok) {
        throw new DOMException((info && info.error) || 'Sistem sesi başlatılamadı', 'NotAllowedError');
      }
      p.ctx = new AudioContext({ sampleRate: info.sampleRate });
      var url = URL.createObjectURL(new Blob([PROCESSOR], { type: 'application/javascript' }));
      return p.ctx.audioWorklet.addModule(url).then(function () {
        URL.revokeObjectURL(url);
        p.node = new AudioWorkletNode(p.ctx, 'canard-feed', {
          numberOfInputs: 0,
          outputChannelCount: [info.channels],
          processorOptions: { channels: info.channels, interleaved: info.interleaved, sampleRate: info.sampleRate },
        });
        p.node.port.onmessage = function (event) { handler.postMessage({ cmd: 'stats', stats: event.data }); };
        p.statsTimer = setInterval(function () { p.node.port.postMessage('stats'); }, 10000);
        p.dest = p.ctx.createMediaStreamDestination();
        p.dest.channelCount = info.channels;
        p.node.connect(p.dest);
        // Beklenmez: WebKit görünmeyen sayfada resume'u görünür olana dek bekletebilir; akış yine de dönmeli.
        p.ctx.resume().catch(function () {});
        return p;
      });
    });
    p.ready.catch(function () { stopPipeline(p); });
    return p.ready;
  };

  var stopPipeline = function (p) {
    if (pipeline === p) pipeline = null;
    clearInterval(p.statsTimer);
    if (p.ctx) p.ctx.close().catch(function () {});
    handler.postMessage({ cmd: 'stop' });
  };

  var placeholderVideoTrack = function () {
    // getDisplayMedia her zaman bir video track döndürür; uyumluluk için kare üretmeyen boş bir track eklenir.
    try {
      var canvas = document.createElement('canvas');
      canvas.width = canvas.height = 2;
      return canvas.captureStream(0).getVideoTracks()[0] || null;
    } catch (error) {
      return null;
    }
  };

  var systemAudioStream = function () {
    return startPipeline().then(function (p) {
      p.refs += 1;
      var track = p.dest.stream.getAudioTracks()[0].clone();
      var released = false;
      var release = function () {
        if (released) return;
        released = true;
        window.removeEventListener('pagehide', release);
        p.refs -= 1;
        if (p.refs <= 0) stopPipeline(p);
      };
      var nativeStop = track.stop.bind(track);
      track.stop = function () {
        release();
        nativeStop();
      };
      window.addEventListener('pagehide', release);
      var tracks = [track];
      var video = placeholderVideoTrack();
      if (video) tracks.unshift(video);
      return new MediaStream(tracks);
    });
  };

  mediaDevices.getDisplayMedia = function (constraints) {
    if (constraints && constraints.audio) return systemAudioStream();
    if (originalGetDisplayMedia) return originalGetDisplayMedia(constraints);
    return Promise.reject(new DOMException('getDisplayMedia desteklenmiyor', 'NotSupportedError'));
  };
})();
