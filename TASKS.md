# Görevler

## Hızlandırma — 17 Kasım 2026

Aşağıdaki maddeler tamamlandı; her biri testlerle doğrulandı
(`python -m unittest discover -s tests -q`).

- [x] **Windows Defender istisnaları** — `scripts/defender-exclusions.ps1`
  proje, `data`, `.venv`, `vendor` klasörlerini ve python/ffmpeg/ffprobe/bun
  süreçlerini muaf tutuyor. Geçici indirmeler artık `data/tmp` altında; 24 saatten
  eski klasörler açılışta siliniyor.
- [x] **Paylaşılan `httpx.AsyncClient`** — `media_bot/http.py`; aynı timeout/header/
  cookie/transport imzası için bağlantı havuzu yeniden kullanılıyor, bot kapanışında
  `close_shared_clients()` çağrılıyor. 19 istek başına client oluşturma kaldırıldı.
- [x] **Ölçülen hıza göre sağlayıcı sıralaması** — `ProviderMetrics.p95_ms` ve
  `successful_latencies`; `provider_memory.ranked()` son 24 saatte ≥4 başarılı
  denemesi olan sağlayıcıları p95'e göre öne alıyor, veri yoksa sabit sıra korunuyor.
- [x] **Uyarlanabilir hedge** — `hedge_seconds()` boşsa tercih edilen sağlayıcının
  p95'inin ~1.2 katını 3–20 sn aralığına sıkıştırıyor; `PROVIDER_HEDGE_SECONDS`
  verilirse sabit değer kullanılıyor.
- [x] **Story/highlight ilk deneme zaman aşımı** — 90 sn → 18 sn
  (`STORY_FIRST_TIMEOUT`); ardından normal yarışa düşüyor.
- [x] **Mesajdaki linkleri paralel işleme** — tek mesajda birden çok link varsa
  ikisi aynı anda indiriliyor, hazır olan hemen yükleniyor. Tek linkte davranış
  aynı; durum mesajı yarışını önlemek için paralel modda statik metin kullanılıyor.
- [x] **yt-dlp bayrakları** — `--concurrent-fragments 4`, `--no-part`,
  `--retries 3`, `--fragment-retries 3` (genel indirici ve YouTube). YouTube video
  seçiminde 1080 → 720 → 480 → 360 kalite merdiveni eklendi; yalnızca boyut/format
  kaynaklı başarısızlıkta bir alt kalite deneniyor.
- [x] **413 otomatik kalite düşürme** — Discord yüklemeyi reddederse video 480p ve
  daha düşük bit hızıyla yeniden kodlanıp bir kez tekrar gönderiliyor
  (`fit_video(force=True)`).
- [x] **Teslim önbelleği süresi** — `MEDIA_CACHE_TTL_MINUTES` varsayılanı 20 → 360
  dakika (en fazla 7 gün). Aynı gönderi tekrar istendiğinde indirme atlanıyor.
- [x] **SQLite WAL** — `media_bot/sqlite_util.open_db()` tüm store'larda
  `journal_mode=WAL`, `busy_timeout=5000`, `synchronous=NORMAL` uyguluyor.
- [x] **Linux/uvloop (opsiyonel)** — `requirements.txt` içine Windows dışı için
  `uvloop` eklendi; `main()` mevcutsa politikayı kuruyor.
- [~] **İndirme/yükleme örtüşmesi** — çok linkli mesajlarda tamamlandı. Tek linkin
  çok dosyalı galerisinde yükleme hâlâ tüm indirmeler bitince başlıyor; bunun için
  `download_media`'nın dosya bazlı akış döndürmesi gerekiyor.

## Ölçüm kontrol listesi

- `!metrics 24s` → platform/sağlayıcı p50–p95 ve kazanan sayıları.
- `!stats 24s` → ortalama işlem süresi, dosya boyutu.
- Aynı üç link (Instagram reel, YouTube 720p, X video) için uçtan uca süre:
  ilk istek + 30 dk sonraki tekrar (önbellek etkisi).
- Değişiklik başına ayrı commit; testler: `python -m unittest discover -s tests -q`.

## Sonraki Discord işleri (hız listesinden bağımsız)

- Sunucu bazlı ayarlar (`/ayar`): otomatik thread, varsayılan kalite, NSFW politikası.
- Sağlık uç noktası (`/healthz`) ve basit uptime takibi.
- Docker/Compose paketleme (Cobalt + bot + ffmpeg tek komut).
- i18n (TR/EN) ve hata mesajlarının sadeleştirilmesi.
- GitHub Actions: test + ruff.
- Tek linkte çok dosyalı galeri için indirme/yükleme akışını birleştirme.
