// Canard sistem sesi köprüsü: getDisplayMedia({audio: true}) motorun yakaladığı sistem sesini taşıyan bir
// MediaStream döndürür, içerik değişiklik gerektirmez. qwebchannel.js ile aynı IIFE'de enjekte edilir.
var mediaDevices = navigator.mediaDevices;
if (mediaDevices && typeof MediaStreamTrackGenerator === 'function' && typeof AudioData === 'function') {
  var originalGetDisplayMedia = mediaDevices.getDisplayMedia ? mediaDevices.getDisplayMedia.bind(mediaDevices) : null;
  var audioObjectPromise = null;

  var audioObject = function () {
    if (!audioObjectPromise) {
      audioObjectPromise = new Promise(function (resolve, reject) {
        if (!window.qt || !window.qt.webChannelTransport) {
          reject(new DOMException('Canard köprüsü yok', 'NotSupportedError'));
          return;
        }
        new QWebChannel(window.qt.webChannelTransport, function (channel) {
          resolve(channel.objects.canardAudio);
        });
      });
    }
    return audioObjectPromise;
  };

  var decode = function (b64) {
    var binary = atob(b64);
    var bytes = new Uint8Array(binary.length);
    for (var i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
    return bytes;
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
    return audioObject().then(function (audio) {
      return new Promise(function (resolve) { audio.start(resolve); }).then(function (info) {
        if (!info || !info.ok) {
          throw new DOMException((info && info.error) || 'Sistem sesi başlatılamadı', 'NotAllowedError');
        }
        var format = info;
        var generator = new MediaStreamTrackGenerator({ kind: 'audio' });
        var writer = generator.writable.getWriter();
        var timestamp = 0;
        var released = false;

        var onChunk = function (b64) {
          if (released) return;
          var bytes = decode(b64);
          var frames = bytes.byteLength / 4 / format.channels;
          var duration = frames * 1e6 / format.sampleRate;
          // Sayfa yetişemiyorsa (ör. ağır kare) paket atılır; görselleştirme için gecikme birikmesinden iyidir.
          if (writer.desiredSize !== null && writer.desiredSize <= 0) {
            timestamp += duration;
            return;
          }
          var data = new AudioData({
            format: format.interleaved ? 'f32' : 'f32-planar',
            sampleRate: format.sampleRate,
            numberOfFrames: frames,
            numberOfChannels: format.channels,
            timestamp: timestamp,
            data: bytes,
          });
          timestamp += duration;
          writer.write(data).catch(function () {});
        };
        var onFormat = function (next) { format = Object.assign({}, format, next); };

        var release = function () {
          if (released) return;
          released = true;
          audio.chunk.disconnect(onChunk);
          audio.formatChanged.disconnect(onFormat);
          audio.stop();
          writer.close().catch(function () {});
          window.removeEventListener('pagehide', release);
        };

        audio.chunk.connect(onChunk);
        audio.formatChanged.connect(onFormat);
        window.addEventListener('pagehide', release);

        var nativeStop = generator.stop.bind(generator);
        generator.stop = function () {
          release();
          nativeStop();
        };

        var tracks = [generator];
        var video = placeholderVideoTrack();
        if (video) tracks.unshift(video);
        return new MediaStream(tracks);
      });
    });
  };

  mediaDevices.getDisplayMedia = function (constraints) {
    if (constraints && constraints.audio) return systemAudioStream();
    if (originalGetDisplayMedia) return originalGetDisplayMedia(constraints);
    return Promise.reject(new DOMException('getDisplayMedia desteklenmiyor', 'NotSupportedError'));
  };
}
