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
- [x] **İndirme/yükleme örtüşmesi** - çok linkli mesajlarda tamamlandı. Tek linkte çok dosyalı galeriler için `download_media` dosya-bazlı akış (`on_file`) ve tek-kazanan garanti modeliyle kazanan sağlayıcının hazır dosyalarını indirme sürerken gönderir; tek dosyalı videolarda örtüşme sınırlı kalabilir.

## Ölçüm kontrol listesi

- `!metrics 24s` → platform/sağlayıcı p50–p95 ve kazanan sayıları.
- `!stats 24s` → ortalama işlem süresi, dosya boyutu.
- Aynı üç link (Instagram reel, YouTube 720p, X video) için uçtan uca süre:
  ilk istek + 30 dk sonraki tekrar (önbellek etkisi).
- Değişiklik başına ayrı commit; testler: `python -m unittest discover -s tests -q`.

## Operasyon paketi — 17 Kasım 2026

- [x] **Dönen günlük dosyası** — `LOG_FILE` (varsayılan `data/bot.log`), `LOG_LEVEL`;
  10 MB × 7 dosya. Boş/`0` değeri yalnızca konsolu bırakır.
- [x] **Sağlık uç noktası** — `media_bot/health.py`, stdlib `http.server`;
  `HEALTH_HOST`/`HEALTH_PORT` (varsayılan kapalı). `/healthz` durum, uptime,
  sunucu sayısı, gecikme, açık devre kesiciler, Cobalt, bakım ve çerez özeti döner.
- [x] **Günlük SQLite yedeği** — `media_bot/backup.py`,
  `sqlite3.Connection.backup` ile `data/backups/<tarih>/`; `BACKUP_KEEP`
  (varsayılan 7) eski klasör silinir. `BACKUP_ENABLED=0` kapatır.
- [x] **Çerez izleme** — `cookie_report`/`cookie_warning`; süresi dolmuş, 3 gün
  içinde dolacak veya Netscape olmayan dosya için sahibine DM + günlük uyarısı.
- [x] **Otomatik başlatma** — `scripts/install-autostart.ps1` oturum açılışında
  `run-bot-loop.ps1` görevini kurar; mutex ikinci kopyayı engeller, çökmede
  5–60 sn artan beklemeyle yeniden dener, `data/autostart.log` tutar.
- [x] **Testler** — `tests/test_ops.py` (15 test): yedek/geri yükleme, saklama
  sınırı, çerez raporu, devre kesici anlık görüntüsü, `/healthz` ve bot payload'ı.

## Discord UX paketi — tamamlandı

- [x] **Sunucu ayarları** — `media_bot/guild_settings.py`: `auto_thread` ve `youtube_quality` (`sor`/`mp3`/`360`/`720`/`1080`) per-sunucu SQLite'da; `!ayar` prefix komutu ve `/ayar` slash komutu, `manage_guild`/sahip ile sınırlı.
- [x] **Otomatik thread** — `media_bot/bot.py`: `ThreadMessage` dönüştürücü, mesajı thread içinde yanıtlar; `guild_settings.auto_thread` ile açılır/kapanır.
- [x] **Varsayılan YouTube kalitesi** — `youtube_view.deliver_youtube` yeniden kullanılabilir teslimat yolu; `sor` olduğunda düğme penceresi gösterilir, diğer seçeneklerde direkt indirilir.
- [x] **Testler** — `tests/test_guild_settings.py` (15 test): ayar kalıcılığı, yetki kontrolü, thread oluşturma, varsayılan kalite yönlendirmesi.

## CI + benchmark paketi — tamamlandı

- [x] **GitHub Actions CI** — `.github/workflows/ci.yml`: `push`/`pull_request`'te `ruff check` + tam test paketi (`windows-latest`, `ubuntu-latest`, Python 3.12).
- [x] **Ruff yapılandırması** — `pyproject.toml`: `E` (pycodestyle hataları) ve `F` (pyflakes) kural kümesi; `E501` hariç, `vendor`/`data`/`.venv` hariç.
- [x] **Benchmark aracı** — `scripts/benchmark.py`: `links.txt` dosyasındaki bağlantıları `download_media` ile ölçer, sağlayıcı/platform bazlı p50/p95 raporu üretir (`rapor.json`). Discord'a bağlanma gerekmez.
- [x] **Testler** — 252 test geçiyor; CI'de her push'ta çalışır.

## Sağlayıcı dayanıklılığı — tamamlandı

- [x] **YouTube bot-check algılama** — `download.py` içinde "Sign in to confirm you're not a bot" vb. hatalar tespit edilince kullanıcıya güncel `COOKIE_FILE` (Netscape, oturum açık) kullanması öneriliyor; metriklerde `bot_check` sonucu kaydediliyor.
- [x] **`!metrics` devre kesici durumu** — açık devreler ve kalan soğuma süreleri metrik özetine ekleniyor (`BREAKERS.snapshot()`).
- [~] **Merkezi kota koordinasyonu** — highlight'lar için mevcut bucket/kullanıcı kotası korunuyor; ek global koordinasyon şimdilik gerekmiyor.

## Yüksek etki / kullanılabilirlik iyileştirmeleri

- [x] **Canlı indirme/gönderim ilerlemesi** — genel medya akışında indirme sırasında `⏳ İndiriliyor… N dosya hazır/hazırlanıyor`, gönderim sırasında `📤 X/Y grup gönderiliyor…` ve bitişte `✅ N dosya gönderildi` durumları gösteriliyor; Bluesky çoklu batch gönderiminde de grup ilerlemesi var.
- [x] **İndirme öncesi YouTube kalite tahmini** — `youtube.suggest_choice()` yükleme limitine göre istenen kaliteyi önceden sınırlar (örn. küçük limitte 1080p→720p/360p), gereksiz indirme ve 413 denemelerini azaltır.
- [x] **İndirme→yükleme akışını tam birleştirme** — `download_media` dosya-bazlı akış (`on_file` callback) ve tek-kazanan garanti modeliyle yeniden tasarlandı: yarışan sağlayıcılarda ilk kararlı medyayı üreten deneme kazanan ilan edilip diğer denemeler iptal edilir; kazananın yeni hazır dosyaları indirme sürerken Discord’a gönderilir. Genel bot akışı bu akışı kullanıyor; metrik/önbellek/breaker/kota/temizlik davranışı korunuyor. Tek dosyalı videolarda örtüşme sınırlı olabilir, çok dosyalı galerilerde asıl UX kazanımı sağlanır.

## Yüksek/orta etki paketi — 19 Kasım 2026

- [x] **X/Reddit kart önbelleği** — `media_bot/card_cache.py`; `CARD_CACHE_TTL_MINUTES`
  (varsayılan 30) ve `CARD_CACHE_MAX_MB` (varsayılan 256). `/cache` ve `/cachetemizle`
  kart istatistiklerini de gösterir/temizler.
- [x] **Kullanıcı kotası** — `media_bot/user_quota.py`; `USER_HOURLY_LIMIT` (30) ve
  `USER_DAILY_LIMIT` (200), `0` ilgili pencereyi kapatır. +18 kotası ayrı kalır,
  bot sahibi muaftır; kota dolduğunda kalan süre yerelleştirilmiş mesajla bildirilir.
- [x] **URL/SSRF güvenliği** — `media_bot/safety.py`; şema, kimlik bilgisi, port,
  localhost/özel IP ve DNS cevapları kontrol edilir. Bağlantı ayrıştırma ve önizleme/
  altyazı istekleri bu kontrolden geçer.
- [x] **Hata UX** — `media_bot/errors.py`; sağlayıcı hataları özel/giriş, bot doğrulaması,
  hız sınırı, zaman aşımı, boyut, bulunamadı ve desteklenmeyen olarak sınıflanır;
  kullanıcıya ham hata yerine kısa TR/EN mesaj ve tekrar dene düğmesi gösterilir.
- [x] **i18n (TR/EN)** — `media_bot/i18n.py`; sunucu bazlı `dil` ayarı (`tr`/`en`/`auto`,
  varsayılan `tr`), durum/kota/hata/ayar metinleri ve `/ayar dil:` seçeneği.
- [x] **X/Reddit ilerleme + boyut tahmini** — `media_bot/progress.py` ve
  `media_bot/sizes.py`; `Content-Length`/`Content-Range` üzerinden yaklaşık toplam
  boyut, indirilen dosya sayısı ve grup yükleme durumu canlı gösterilir.
- [x] **Uzun işler için iptal** — `media_bot/cancel_view.py`; durum mesajındaki
  `İptal` düğmesi yalnızca bağlantıyı paylaşan kullanıcı ve bot sahibi tarafından
  kullanılabilir; görev iptal edilir ve mesaj güncellenir.
- [x] **Test/CI genişletmesi** — kart önbelleği, kullanıcı kotası, SSRF kontrolleri,
  hata sınıflandırma, ilerleme biçimlendirme ve dil ayarları için testler eklendi;
  `ruff check` ve tüm test paketi CI'de çalışmaya devam eder.

## Hızlı kazanımlar — 19 Kasım 2026

- [x] **Yerelleştirilmiş yardım** — `!yardim` / `!help` artık sunucunun dil ayarına
  göre `i18n.help_text()` kullanıyor; sabit `HELP_TEXT` kaldırıldı.
- [x] **Güvenlik CI'ı** — `.github/workflows/ci.yml` içinde ayrı `security` işi
  `pip-audit -r requirements.txt` çalıştırıyor. `Pillow>=13,<14` güncellendi,
  kullanılmayan `discord.py[voice]`/PyNaCl bağımlılığı kaldırıldı.
- [x] **Dependabot** — `.github/dependabot.yml` pip ve GitHub Actions güncellemelerini
  haftalık, gruplanmış PR'larla takip ediyor.
- [x] **Pre-commit** — `.pre-commit-config.yaml`; YAML/TOML doğrulaması, büyük dosya
  kontrolü, boşluk düzeltmesi ve `ruff --fix`.
- [x] **`/metrics` zenginleştirmesi** — kart önbelleği kayıt/dosya/boyut/isabet oranı
  ve bu saat/gün toplam kota doluluğu özete eklendi (`UserQuota.summary()`).

## Sıradaki paketler

- [ ] Docker/Compose paketleme (Cobalt + bot + ffmpeg tek komut).
