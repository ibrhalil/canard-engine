#!/bin/zsh
# Motor kıyası seti: QtWebEngine, Swift + WKWebView ve PyObjC + WKWebView'ı aynı mscillus modlarında sırayla ölçer.
# Motorlar her turda yer değiştirir (ısınma/termal kayma tek motoru cezalandırmasın). Sonuç: run.py compare.
#
#   tools/engine-bench/suite.sh                      # varsayılan: 2 tur × 3 mod × (yayınsız, yayınlı) × qt/webkit
#   ROUNDS=1 tools/engine-bench/suite.sh             # tek tur
#   ENGINES="qt webkit wkpy" ...                     # wkpy: PyObjC + WKWebView (yayın ölçümü yok, atlanır)
#   MODES="fire kaleidoscope" ...                    # mscillus mod kimlikleri (registry.ts)
#   SETTINGS=build/engine-bench/user-settings.json   # mscillus ayarlarının tamamı (mod seçenekleri dahil)
#   CAPTURE=0 ...                                    # yayınlı (Canard.saver hattı) ölçümleri atla
#   DURATION=60 ...                                  # ölçüm süresi sn (ısınma birikimi için uzun)
#
# Ayarlar ve mod her ölçümde içerikten önce verilir (bench-prelude.js), mikrofon seçimi yüklemeden sonra
# (bench-boot.js). Ölçüm boyunca ana ekran odak almayan bir pencereyle kaplanır; Ctrl+C seti durdurur.
set -uo pipefail
here=${0:A:h}
root=${here:h:h}
cd "$root"

rounds=${ROUNDS:-2}
warmup=${WARMUP:-20}
duration=${DURATION:-30}
modes=(${=${MODES:-bars milkdrop galaxy}})
order=(${=${ENGINES:-qt webkit}})
settings=${SETTINGS:-}
captures=("")
[[ ${CAPTURE:-1} == 0 ]] || captures+=("--capture 30 --encode")
boot="$here/bench-boot.js"
preludes=build/engine-bench/prelude
mkdir -p $preludes

if pgrep -f canard.app.main >/dev/null; then
  echo "Canard çalışıyor: ölçümü kirletir. Önce menüden Çık, sonra yeniden başlat." >&2
  exit 1
fi
[[ -x build/engine-bench/WebKitBench.app/Contents/MacOS/WebKitBench ]] || "$here/build.sh" || exit 1
[[ -z $settings || -f $settings ]] || { echo "ayar dosyası yok: $settings" >&2; exit 1 }
trap 'echo "\ndurduruldu"; exit 130' INT

# bench-prelude.js'in başına yapılandırma ekler: ayarlar (SETTINGS) ve mod.
make_prelude() {
  local file="$preludes/$1.js"
  local json=null
  [[ -n $settings ]] && json=$(<$settings)
  print -r -- "var CANARD_BENCH = {\"settings\": $json, \"mode\": \"$1\"};" > $file
  cat "$here/bench-prelude.js" >> $file
  print -r -- $file
}

bench() { uv run python "$here/run.py" "$@" || echo "⚠︎ ölçüm başarısız: $*" }

per_mode=0
for engine in $order; do
  [[ $engine == wkpy ]] && (( per_mode += 1 )) || (( per_mode += ${#captures} ))
done
total=$(( rounds * ${#modes} * per_mode ))
step=0
echo "Kıyas: ${#modes} mod × ${#order} motor × $rounds tur = $total ölçüm, ~$(( (total * (warmup + duration + 10) + 30) / 60 )) dk"

echo "\n── taban: motor yok (sistem boşta)"
bench idle --label taban --duration 15

for (( round = 1; round <= rounds; round++ )); do
  engines=($order)
  (( round % 2 == 0 )) && engines=(${(Oa)order})
  for mode in $modes; do
    prelude=$(make_prelude $mode)
    for capture in "${captures[@]}"; do  # tırnaksız açılım boş elemanı ("yayınsız") düşürür
      for engine in $engines; do
        [[ $engine == wkpy && -n $capture ]] && continue
        step=$(( step + 1 ))
        echo "\n── [$step/$total] tur $round · $engine · $mode${capture:+ · yayın}"
        bench $engine --prelude $prelude --boot $boot --label "$mode·$round" --warmup $warmup --duration $duration \
          ${=capture}
      done
    done
  done
done

echo "\n══ sonuçlar"
uv run python "$here/run.py" compare
