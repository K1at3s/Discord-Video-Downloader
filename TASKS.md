# Görevler

## Yarın: hızlandırma (etkiye göre sıralı)

Aşağıdaki liste "önce ölç, sonra değiştir" mantığıyla ilerler. Her maddeden önce
`!metrics 24s` çıktısı ve bir örnek isteğin uçtan uca süresi not edilsin; değişiklik
sonrası aynı ölçüm tekrarlansın.

1. **Windows Defender istisnaları** (kod yok, en yüksek etki)
   Proje klasörü, `data/`, `.venv/` ve yt-dlp/ffmpeg çalışma klasörlerini gerçek
   zamanlı taramadan muaf tut. İndirme ve ffmpeg yeniden kodlama sırasında oluşan
   gecikmenin büyük kısmı buradan geliyor. Ölçüm: aynı 720p videonun indirme+serme
   süresi önce/sonra.

2. **Paylaşılan `httpx.AsyncClient`** (`media_bot/http.py`)
   Şu anda 20'den fazla yerde istek başına yeni client açılıyor; her biri yeni TLS
   el sıkışması demek. Sağlayıcı bazlı timeout'ları `client.get(..., timeout=...)`
   ile koru, uygulama kapanışında tek yerde `aclose()` çağır. Hedef: istek başına
   50–200 ms ve gereksiz bağlantı yükü.

3. **Sağlayıcıları ölçülen hıza göre sırala**
   `provider_metrics` zaten `p50_ms`/`p95_ms` ve kazanan sayısını tutuyor.
   `download.py` içindeki sabit sıra yerine: yeterli örnek varsa (örn. son 24 saatte
   ≥ 5 başarı) `wins` ve `p95`'e göre sırala; kota geçmişi olan sağlayıcıyı sona at;
   örnek yoksa mevcut sabit sıraya dön. Test: sıralama fonksiyonu için birim testi.

4. **Uyarlanabilir hedge süresi**
   `provider_memory.hedge_seconds()` şu an sabit `PROVIDER_HEDGE_SECONDS` (8 sn).
   Önce tercih edilen sağlayıcının `p95` değerini kullan (3–20 sn arası sınırla),
   env değeri verilmişse onu üstün tut. Yavaş sağlayıcıda gereksiz bekleme, hızlıda
   gereksiz ikinci indirme önlenir.

5. **Story/highlight zaman aşımını düşür**
   `download.py` içinde story ve highlight yollarında ilk sağlayıcı 90 sn
   bekleyebiliyor; tek küçük dosya için bu, kuyruğu kilitliyor. İlk denemeyi
   15–20 sn'ye indir, sonra hedge'e geç. Hata mesajı aynı kalsın.

6. **Bir mesajdaki linkleri paralel işle**
   `MAX_LINKS_PER_MESSAGE` kadar link şu an sırayla işleniyor. İndirmeleri paralel
   başlat (mevcut `job_slots` sınırı içinde), yüklemeleri tamamlandıkça sırayla yap.
   Discord rate limit'e dikkat: yükleme tarafı seri kalsın.

7. **yt-dlp bayrakları ve boyuta göre format seçimi**
   `--concurrent-fragments 4`, `--no-part`, `--retries 3`, `--fragment-retries 3`.
   Format seçiminde `-S res:…,ext:mp4` ile hedef yükleme limitine en yakın
   çözünürlüğü seç; gereksiz yüksek çözünürlük indirip sonra küçültme.

8. **413'te otomatik kalite düşürme**
   Discord "Payload Too Large" döndüğünde aynı dosyayı bir alt çözünürlükte
   (1080 → 720 → 480) yeniden kodla ve tekrar gönder. Reddedilen boyutu kısa süreli
   bellekte tut, aynı istekte ikinci kez denemesin. `video_fit` zaten var; eksik olan
   retry akışı ve boyut ipucu.

9. **Teslim önbelleği süresini uzat**
   `MEDIA_CACHE` TTL'i 20 dk; aynı gönderi yarım saat sonra tekrar indiriliyor.
   Platforma göre 6–24 saat TTL kullan (YouTube/Instagram gönderileri değişmez).
   Not: Discord botları mesaj forward edemez; kazanç, dosyayı yerelden yeniden
   yükleyip indirmeyi tamamen atlamak.

10. **İndirme/yükleme örtüşmesi**
    Çok dosyalı gönderilerde ilk dosya inince yüklemeyi başlat, kalan dosyalar
    arka planda inerken ilk dosya Discord'a gönderilsin. 6. maddeyle birlikte
    uygulanırsa toplam süre en çok burada düşer.

11. **SQLite WAL ve busy_timeout**
    `provider_metrics`, `request_log`, `adult` kotası ve highlight state için
    `PRAGMA journal_mode=WAL` + `busy_timeout=5000`. Eşzamanlı yazmalarda kilit
    beklemesini azaltır; tek satırlık değişiklik.

12. **Linux + uvloop (opsiyonel, dağıtım)**
    Botu Linux'ta çalıştırıp `uvloop` kullanmak, paralel indirme ve ffmpeg
    alt süreçlerinde Windows'a göre belirgin kazanç sağlar. Docker paketleme
    maddesiyle birlikte değerlendirilebilir.

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
