# Discord Video Downloader

Discord sohbetindeki sosyal medya bağlantılarını algılayıp medyayı ve erişilebilen gönderi metnini paylaşan Python botu. Platforma göre birden fazla ücretsiz sağlayıcıyı dener; ilk kullanılabilir tam sonucu gönderir.

## Desteklenen bağlantılar

| Platform | Bağlantı | Çıktı |
| --- | --- | --- |
| Instagram | Gönderi, reel, video | Fotoğraf/video galerisi, açıklama, kaynak düğmesi |
| TikTok | Video, fotoğraf | Medya galerisi, açıklama, kaynak düğmesi |
| Facebook | Gönderi, reel, video, fotoğraf | Medya galerisi, erişilebilen açıklama, kaynak düğmesi |
| X / Twitter | Tweet | Koyu tweet PNG kartı; varsa medya ayrıca |
| Reddit | Gönderi ve `/r/.../s/...` kısa bağlantısı | Metin koyu PNG sayfalarında; varsa medya ayrıca |
| YouTube | Video, Shorts, canlı yayın bağlantısı | MP3, 360p, 720p, 1080p düğmeleri |
| Spotify | Tekil parça | Eşleşen YouTube kaydından MP3, doğrudan dosya eki |
| Threads | Gönderi | Web sağlayıcısı ve önizleme yedekleri |
| Douyin | Video, not | `yt-dlp` ve doğrudan sayfa yolları |
| Weibo | Gönderi | `gallery-dl` ve `yt-dlp` |
| Streamable | Video | Streamable API ve `yt-dlp` |

Profil, arama, tüm albüm ve oynatma listesi bağlantıları topluca indirilmez. Özel gönderiler, oturum isteyen içerikler ve bölgesel engeller başarısız olabilir.

![X kartı örneği](card-preview.png)

## Gereksinimler

- Python 3.11+.
- [FFmpeg](https://ffmpeg.org/download.html) ve `ffprobe` PATH üzerinde. Ses dönüştürme ve bazı videolarda ses/görüntü birleştirme için gerekir.
- Discord uygulaması, bot token'ı ve **Message Content Intent**.
- İsteğe bağlı: [Cobalt](https://github.com/imputnet/cobalt) için Docker veya Node.js. Cobalt olmadan diğer sağlayıcılar çalışır. WSL zorunlu değildir.

Botun Python modülü Windows, Linux ve macOS'ta çalıştırılabilir. `.ps1` başlatma betikleri Windows içindir.

## Discord kurulumu

1. [Discord Developer Portal](https://discord.com/developers/applications) üzerinde uygulama ve bot oluşturup token alın.
2. **Bot → Privileged Gateway Intents → Message Content Intent** seçeneğini açıp kaydedin. Aksi halde `PrivilegedIntentsRequired` hatası alınır.
3. Botu sunucuya **View Channels**, **Read Message History**, **Send Messages** ve **Attach Files** izinleriyle ekleyin. Kanal izinlerini ayrıca kontrol edin.

## Windows'ta çalıştırma

PowerShell'de proje klasöründe:

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
```

`.env` dosyasındaki `DISCORD_TOKEN` değerini kendi token'ınızla doldurun. `ffmpeg -version` komutunun çalıştığını kontrol edin.

```powershell
.\start-bot.ps1
```

Botu `Ctrl+C` ile durdurabilirsiniz. Linux/macOS'ta sanal ortamı etkinleştirip `python -m media_bot.bot` çalıştırın. Botun çevrimiçi kalması için çalıştığı makine açık kalmalıdır.

## `.env` ayarları

| Değişken | Varsayılan | Görevi |
| --- | --- | --- |
| `DISCORD_TOKEN` | Yok | Zorunlu bot token'ı |
| `CHANNEL_IDS` | Boş | Virgülle ayrılmış kanal ID'leri; boşsa erişilebilen tüm sunucu metin kanalları |
| `MAX_UPLOAD_MB` | `20` | Dosya başına yerel sınır; Discord sınırınıza uygun seçin |
| `MAX_LINKS_PER_MESSAGE` | `3` | Tek mesajda işlenen bağlantı sayısı; en fazla 10 |
| `MAX_CONCURRENT_JOBS` | `2` | Eşzamanlı mesaj işi; en fazla 10 |
| `DATABASE_PATH` | `data/bot.sqlite3` | +18 kotası için SQLite dosyası |
| `COBALT_API_URL` | Boş | İsteğe bağlı Cobalt adresi, örn. `http://127.0.0.1:9000/` |
| `COBALT_API_KEY` | Boş | Cobalt örneği gerektiriyorsa anahtar |
| `COOKIE_FILE` | Boş | Erişim hakkınız olan hesabın Netscape biçimli çerez dosyası |
| `GALLERY_DL_RS_PATH` | Boş | İsteğe bağlı `gallery-dl-rs` program yolu |

Kanal ID'si ve Cobalt anahtarı zorunlu değildir. Gerçek `.env`, çerezler, SQLite veritabanı ve geçici indirmeler Git'e eklenmez.

## Cobalt (isteğe bağlı)

Docker ile yerel Cobalt çalıştırmak için:

```powershell
docker compose -f cobalt.compose.yaml up -d
```

`.env` içine `COBALT_API_URL=http://127.0.0.1:9000/` ekleyin. Durdurmak için `docker compose -f cobalt.compose.yaml down` kullanın. Docker istemiyorsanız Cobalt kaynaklarını [resmî adımlarla](https://github.com/imputnet/cobalt/blob/main/docs/run-an-instance.md) `vendor/cobalt` altına kurup Node.js ile `start-cobalt.ps1` çalıştırabilirsiniz. `start-bot.ps1` yerel Cobalt adresi ayarlıysa onu başlatmayı dener; başarısız olursa bot diğer sağlayıcılarla devam eder. `stop-cobalt.ps1` yerel Node sürecini durdurur.

`start-cobalt.ps1` geliştirildiği bilgisayardaki Codex Node yoluna da yedek olarak bakar. Başka bir bilgisayarda Node'u PATH'e ekleyin veya Docker kullanın. Bun, bu Cobalt sürümünün `isolated-vm` modülüyle uyumlu değildir. `vendor/` kaynak kopyası bu depoya gönderilmez.

## Mesaj düzeni ve sınırlar

- Fotoğraf ve videolar **Discord Components V2 Media Gallery** içinde, erişilebilen açıklama ve **Kaynak** bağlantı düğmesiyle gönderilir. Piksel içindeki yazı OCR ile okunmaz; gönderi açıklaması alınır.
- Bir gönderiden en çok **100 dosya** alınır. Discord'un mesaj başına 10 dosya sınırı için dosyalar 10'arlı mesajlara bölünür. Uzun açıklamalar metin sınırına göre devam mesajlarına ayrılır.
- X tweet metni, alıntılar, profil fotoğrafları, mavi tikler ve erişilebilen sayaçlar koyu PNG karta çizilir. Medya varsa ayrıca yüklenir.
- Reddit metni koyu PNG sayfalarına çizilir. Birden çok sayfa aynı Media Gallery mesajındadır; 10 sayfadan sonra yeni mesaj açılır.
- YouTube düğmelerini yalnızca bağlantıyı gönderen kişi kullanır. Düğmeler beş dakika geçerlidir. MP3 128 kb/sn hazırlanır. Çözünürlük düğmeleri üst sınırdır.
- Spotify'ın korumalı ses akışı indirilmez. Bot parça adı, sanatçı ve süreyle YouTube'da tam uzunlukta kayıt arar; uygun eşleşmeyi MP3 dosyası olarak **doğrudan ekler**, embed kullanmaz. Kayıplı kaynaktan gerçek FLAC üretilemeyeceği için FLAC etiketi kullanılmaz.
- Sağlayıcılar kısa aralıklarla yarışır; ilk eksiksiz sonuç alınır. Platformlar ve web servisleri değişebildiğinden kesintisiz başarı garantisi yoktur. Sağlayıcı sırası ve araştırma notları [PROVIDERS.md](PROVIDERS.md) içindedir.
- Dosyalar geçici klasörde işlenip Discord'a gönderildikten sonra silinir. Bot medya arşivi tutmaz.

## +18 koruması ve SQLite

Reddit gönderisi veya subreddit +18 işaretliyse bağlantı yalnızca **NSFW kanalda** veya o kanalın thread'inde işlenir. Kullanıcı başına **Türkiye saatine göre günde bir +18 işlem** hakkı vardır. Hak işlem başladığında tüketilir ve sayaç `DATABASE_PATH` içindeki SQLite dosyasında atomik tutulur; MongoDB sunucusu gerekmez.

Reddit'in +18 durumu doğrulanamazsa bağlantı işlenmez. Bu denetim **Reddit'e özgüdür**; diğer platformlardaki işaretsiz yetişkin içeriğini güvenilir biçimde sınıflandırmaz.

## Kod haritası

| Yol | Görevi |
| --- | --- |
| `media_bot/bot.py` | Discord olayları, mesaj akışı, Components V2, yapılandırma |
| `media_bot/links.py` | Bağlantı tanıma ve normalleştirme |
| `media_bot/download.py`, `race.py` | Sağlayıcı sırası ve indirme yarışı |
| `media_bot/direct_providers.py`, `web_providers.py`, `cobalt.py`, `gendownload.py` | İndirme sağlayıcıları |
| `media_bot/youtube.py`, `youtube_view.py`, `spotify.py` | YouTube seçenekleri ve Spotify eşleştirmesi |
| `media_bot/reddit_post.py`, `reddit_card.py`, `adult.py` | Reddit metni/kartları ve +18 kotası |
| `media_bot/x_card.py`, `x_story.py` | Tweet verisi ve görseli |
| `media_bot/captions.py`, `metadata_fallback.py`, `limits.py` | Açıklama, önizleme, dosya sınırları |
| `tests/` | Birim ve akış testleri |

Firefox eklentisi ayrı bir yerel çalışma klasörüdür; `.gitignore` içinde tutulur ve bu repoya dahil edilmez.

## Test ve sorun giderme

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -p "test_*.py"
```

Testler, üçüncü taraf web servislerinin her zaman çalışacağını garanti etmez; canlı testi herkese açık bir bağlantıyla ayrıca yapın.

- **Bot açılmıyor:** Message Content Intent ve token'ı kontrol edin.
- **Bot yanıt vermiyor:** Desteklenen tekil bağlantı biçimini, kanal izinlerini ve `CHANNEL_IDS` filtresini kontrol edin.
- **Dosya yüklenmiyor:** Discord'un yükleme sınırı ve `MAX_UPLOAD_MB` değerini kontrol edin.
- **YouTube/Spotify 403:** Bot birden fazla sağlayıcı ve oynatıcı istemcisi dener. Gerekirse `yt-dlp` güncelleyin; kendi erişim hakkınız olan çerezleri `COOKIE_FILE` ile verin.
- **Cobalt başlamıyor:** URL'yi boş bırakın veya Docker/Node kurulumunu ve `127.0.0.1:9000` durumunu kontrol edin.
- **Reddit işlenmiyor:** +18 bilgisi doğrulanamazsa veya kanal NSFW değilse bot bağlantıyı reddeder.

Token, çerez dosyası, SQLite veritabanı ve indirilen medya dosyalarını repoya göndermeyin.
