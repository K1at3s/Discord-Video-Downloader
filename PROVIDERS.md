# Sağlayıcı araştırması

Bu belgede **rota**, botun `provider_order()` listesinde ayrı bir deneme olarak yer alan yoldur. Rota sayısı bağımsız çıkarıcı sayısı değildir: web servisleri aynı altyapıyı kullanabilir. Medya bağlantılarında ilk üç rota kısa aralıklarla başlar; ilk tam dosya setini indiren kazanır. Bir gönderide 100 dosyaya kadar alınır ve Discord'a 10'arlı mesajlar halinde yüklenir. Listede olmak her bağlantının çalışacağını garanti etmez.

Sağlayıcı sağlığını görmek için bot gerçek indirme denemelerinde `platform`, `sağlayıcı`, `sonuç` ve `süre` alanlarını yerel SQLite dosyasına yazar; URL ve kullanıcı bilgisi saklanmaz. `.\.venv\Scripts\python.exe -m media_bot.provider_metrics` son 24 saatin başarı oranını, arıza sayısını ve başarılı denemelerin ortalama süresini gösterir. Atlanan ve yarışta iptal edilen denemeler başarı paydasına katılmaz. Kullanıcının indirmesiz kontrol tercihi nedeniyle günlük örnek URL'lerle otomatik medya sağlık testi çalıştırılmaz.

Bluesky ayrı bir resmî API akışıdır ve `provider_order()` yarışına dahil değildir. Herkese açık gönderi, metin ve görseller `public.api.bsky.app` üzerinden alınır; video oynatma listesi FFmpeg ile MP4'e aktarılır. Video boyutu sınırı aşarsa HLS listesindeki daha düşük kalite denenir; aynı anda en fazla iki FFmpeg işi çalışır. Ücretsiz ve anahtarsız erişim için üçüncü taraf embed düzelticisi kullanılmaz. API 429 dönerse `Retry-After` en fazla üç saniye beklenerek bir kez tekrar denenir. Gönderi, hesap veya resmî moderasyon servisi yetişkin içerik etiketi döndürürse NSFW kanal ve günlük +18 kota uygulanır. Anonim API'nin döndürmediği veya hiç etiketlenmemiş içerik bu denetimle saptanamaz.

## YouTube seçimi

[Discord Components V2](https://discord.com/developers/docs/components/overview) düğmeleri Python tarafında [discord.py `LayoutView`](https://discordpy.readthedocs.io/en/stable/interactions/api.html#discord.ui.LayoutView) ile kuruldu. MP3, 360p, 720p ve 1080p seçenekleri bağlantıyı paylaşana gösterilir.

| Yol | Tür | Doğrulama |
| --- | --- | --- |
| [GenDownload](https://gendownload.com/api-docs) | Ücretsiz barındırılan web API | Kısa herkese açık örnekte MP3 (179.685 bayt) ve 360p MP4 (742.165 bayt) tam akıştan indirildi. Önceki bazı isteklerde geçici bağlantı hataları görüldü. |
| [Cobalt](https://github.com/imputnet/cobalt/blob/main/api/README.md) | Açık kaynak, mevcut yerel API | YouTube.js tabanlı. Kısa herkese açık örnekte MP3 (305.708 bayt) ve 360p MP4 (742.287 bayt) gerçekten indirildi. |
| [yt-dlp](https://github.com/yt-dlp/yt-dlp) | Ücretsiz Python indiricisi | 12 Kasım'da Spotify parçasına eşlenen YouTube kaydı alternatif oynatıcı istemcisiyle 3,1 MB MP3 olarak indirildi; başka bağlantılarda 403 görülebilir. |

GenDownload farklı bir barındırma yedeğidir; çıkarıcı motorunun `yt-dlp`'den bağımsız olduğu doğrulanmadı. Cobalt, YouTube için `youtube.js` kullanır; bu iki yerel yol farklı çıkarıcı altyapılarıdır. Yeni paket kurulmadı.

| Yol | Platform | Bağımlılık | Kapsam / sınır |
| --- | --- | --- | --- |
| [gallery-dl](https://github.com/mikf/gallery-dl/blob/master/docs/supportedsites.md) | Beşi de | Python paketi | Özellikle fotoğraf ve galeriler; bazı gönderiler için çerez gerekebilir. |
| [yt-dlp](https://github.com/yt-dlp/yt-dlp/blob/master/supportedsites.md) | Beşi de | Python paketi, video birleştirmede FFmpeg | Video odaklı; platform değişikliklerine bağlı. |
| [Cobalt](https://github.com/imputnet/cobalt/blob/main/api/README.md) | Beşi de | Kendi API örneği | Instagram, TikTok ve X medyası; Facebook'ta herkese açık video, Reddit'te GIF/video. Kendi sunucunuz gerekir; genel API yok. |
| [Instaloader](https://instaloader.github.io/as-module.html) | Instagram | Python paketi | Gönderi/reel/fotoğraf/karusel; bazı içerikler oturum ister. |
| [FxTwitter API](https://github.com/FxEmbed/FxEmbed/blob/main/docs/specs/fxtwitter-openapi.json) | X | Genel API | Tweet görsel/video URL'leri ve kart verisi; üçüncü taraf API erişimine bağlı. |
| [Reddit gönderi JSON'u](https://www.reddit.com/dev/api/) | Reddit | Eski anonim `.json` yolu | Kodda son çare olarak durur; bu ortamda 403 verdiği için güvenilir sağlayıcı sayılmaz. |
| [TikWM API](https://github.com/krypton-byte/tiktok-downloader/blob/master/tiktok_downloader/tikwm.py) | TikTok | Üçüncü taraf genel API | Video ve fotoğraf slaytları; resmî TikTok API'si değil, değişebilir. |
| [Facebook herkese açık sayfa çıkarımı](https://github.com/amiolodev/viddown) | Facebook | Sayfa HTML'si | Genel video alanları ve Open Graph fotoğrafı; paylaşım/giriş yönlendirmelerinde kırılabilir. |
| [GenDownload](https://gendownload.com/api-docs) | TikTok, Instagram, Facebook, X | Anahtarsız barındırılan web API | Dört platformda örnek medya seçenekleri doğrulandı; TikTok örneğinde 3,36 MB MP4 dosyası geçici klasöre indirildi. Reddit yanıtı doğrulanamadı. |
| [VxTwitter API](https://github.com/Yamada-Factory/vxtwitter/blob/main/api.md) | X | Genel API | FxTwitter'dan ayrı JSON ve medya URL yolu; erişim değişebilir. |
| [you-get](https://github.com/soimort/you-get) | Instagram, X | Python paketi | Ayrı Python indiricisi; destek listesinde iki platform var, güncel post erişimi değişebilir. |
| [Social Media Downloader](https://github.com/Vette1123/social-media-downloader/blob/main/README.md) | Instagram, Facebook video, Reddit video | Anahtarsız barındırılan web API | `socialdownloader.space` örneği üzerinden Instagram karuseli 4/4 fotoğraf, Facebook ve Reddit videosu canlı indirildi. Birden çok dahili çıkarıcı kullanıyor; bazıları mevcut rotalarla aynı. |

## Ek platform yolları (12 Kasım 2026)

| Platform | Varsayılan yollar | Doğrulama ve sınır |
| --- | --- | --- |
| Reddit kısa `/s/` | [Reddit Atom akışı](https://www.reddit.com/r/modnews/comments/1xuj8jp/mod_monthly_your_november_2026_newsletter_is_here/); [Arctic Shift](https://github.com/ArthurHeitmann/arctic_shift/blob/master/api/README.md); [PullPush](https://pullpush.io/) | Atom örnek gönderide **13 Kasım 2026'da hâlâ 200** döndü; Reddit aynı gün RSS desteğini bitireceğini duyurdu. Arşiv yolları yeni gönderilerde gecikebilir ve canlı dosya testiyle doğrulanmadı. Tam gönderi ve +18 durumu doğrulanamazsa bot güvenlik nedeniyle Discord önizlemesini de göndermiyor. Reddit bu yüzden en iyi çaba platformudur. |
| [Threads](https://pypi.org/project/yt-dlp-threads/) | [threadsphotodownloader.com](https://threadsphotodownloader.com/) (`threadsdl`, normal gönderilerde çerezsiz ana yol); oturumlu açık gönderi sayfasının JSON verisi (`threads-page`: fotoğraf, video, karusel); `yt-dlp-threads` 0.1.0 video yedeği; [Social Media Downloader](https://github.com/Vette1123/social-media-downloader) web API; varsa `gallery-dl-rs` native | `/share/` yalnızca geçerli bir Threads oturumu varken gerçek gönderiye yönleniyor; `COOKIE_FILE` ayarlandığında çözümleyici bu yönlendirmeyi takip eder. Normal gönderi bağlantıları `threadsdl` ile çerezsiz indirilebilir. Canlı doğrulandı: `/share/BAaTVei4V4` oturumla çözüldü; kanonik gönderilerde çerezsiz fotoğraf ve video alındı. |
| [Douyin](https://github.com/yt-dlp/yt-dlp/blob/master/supportedsites.md) | `yt-dlp`; video sayfasındaki Open Graph doğrudan URL | Kurulu `yt-dlp` Douyin çıkarıcısı içeriyor. Doğrudan yol yalnızca video gönderilerinde uygulanır; canlı Douyin dosyası denenmedi. |
| [Weibo](https://github.com/mikf/gallery-dl/blob/master/docs/supportedsites.md) | `gallery-dl` fotoğraf/galeri; `yt-dlp` video | İki kurulu aracın Weibo çıkarıcıları doğrulandı; canlı gönderi dosyası denenmedi. |
| [Streamable](https://streamable-support.zendesk.com/hc/en-us/articles/35415672400916-API-Documentation) | Resmî `/videos/{id}` API'si; `yt-dlp`; yerel Cobalt açıksa Cobalt | Resmî API örneğinde `200`, `mp4` ve `mp4-mobile` alanları canlı görüldü. Tam video aktarımı denenmedi. |
| [Spotify](https://developer.spotify.com/documentation/embeds/reference/oembed) | Herkese açık parça sayfası + YouTube ses sağlayıcıları | Başlık, sanatçı ve süreyle eşleşen tam YouTube kaydı MP3'e indiriliyor. Örnek parçada 3,1 MB MP3 doğrulandı. Spotify'ın korumalı ses akışı indirilmez; FLAC üretilmez. |

## İsteğe bağlı deneysel yol

[gallery-dl-rs](https://github.com/aagedal/gallery-dl-rs) beş platform için `--extractor native` modunda farklı bir Rust çıkarımı sunuyor. Proje kendi açıklamasında Instagram, Facebook ve X tarafını deneysel olarak işaretliyor. Derlenmiş programı `GALLERY_DL_RS_PATH` ile gösterirseniz bot son yedek olarak dener. **Varsayılan uyumluluk modu gallery-dl'yi çağırdığı için ayrı sağlayıcı sayılmıyor**; bot yalnızca `native` modunu kullanır.

## Her platformda kaç rota var?

Sayılar `media_bot/download.py` içindeki `provider_order()` fonksiyonundan çıkar. Kurulumunuza göre güncel listeyi görmek için:

```powershell
.\.venv\Scripts\python.exe -c "from media_bot.download import provider_order; platforms=('instagram','tiktok','facebook','x','reddit','threads','douyin','weibo','streamable'); [print(p, len(provider_order(p)), len(provider_order(p, cobalt_enabled=True)), ', '.join(provider_order(p, cobalt_enabled=True))) for p in platforms]"
```

Her satır sırasıyla platformu, Cobalt kapalı/açık rota sayısını ve Cobalt açık rotaları verir. `GALLERY_DL_RS_PATH` ayarlanmışsa `gallery_rs_enabled=True` ile tekrar çalıştırın. Bunlar eşit kapsamlı veya tamamen bağımsız motorlar değildir. GenDownload uzak sunucusu `yt-dlp` ile aynı çıkarıcıyı kullanıyor olabilir. Gizli veya izin isteyen gönderilere hiçbir yedek garanti vermez.

## Yeni adayların kontrolü (12 Kasım 2026)

- [Vette1123/social-media-downloader](https://github.com/Vette1123/social-media-downloader/blob/main/README.md) gerçek, MIT lisanslı bir proje. Barındırılan örneğin `/api/download` yanıtını ve `/api/image`, `/api/video` dosya akışlarını ayrı ayrı denedim. Instagram örneğinde dört fotoğrafın tamamı yaklaşık 2,5 saniyede indi; Facebook ve Reddit video örnekleri de tam indirildi. Bu üç platforma yedek rota eklendi. Facebook'ta yalnızca video URL'lerinde kullanılıyor; fotoğraf albümünü tek videoya düşürmemeli. TikTok'ta JSON bağlantısı dönmesine karşın gerçek video proxy'si 502 verdi; bu yüzden varsayılan TikTok rotası sayılmıyor. X yolu da mevcut VxTwitter ve Cobalt yollarıyla örtüştüğü ve karma görsel setini doğrulamadığım için eklenmedi. Uzak örnek süre ve erişim sınırları koyabilir.
- `risunCode/api-xtfetch` deposu GitHub'da 404 döndü; belgelenen playground ucu veya lisansı doğrulanamadı. Çalışan URL olmadan sağlayıcı olarak eklenmedi.
- `on_message` akışı Discord etkileşimi değildir; üç saniyelik `defer()` kuralı burada uygulanmaz. YouTube seçim düğmesinin callback'i zaten hemen `defer()` ediyor. İlk üç sağlayıcı yarışı başarısız/iptal edilen işleri durduruyor. Dosya akışı `Content-Length` sınırı varsa büyük dosyayı indirmeden reddediyor; ayrıca `HEAD` turu eklenmedi. Bütün sağlayıcıları 8-10 saniyede kesmek büyük video ve galerileri eksik bırakabileceği için mevcut aktarım süreleri korundu. Devre kesici sağlayıcı + platform bazında art arda üç zaman aşımı, TLS/bağlantı veya 5xx hatasında 150 saniye açılır. Süre bitince yalnızca bir deneme geçer. 403, 404, 429 ve gizli/giriş isteyen gönderiler sağlayıcı arızası sayılmaz. Durum bot süreci belleğindedir; yeniden başlatınca sıfırlanır.

### Ek web ve kütüphane adayları

- [FastSaver](https://api.fastsaver.io/) ücretsiz başlangıç için 1.000 kredi veriyor; resmî sayfası bunu aylık yenilenen ücretsiz kota olarak açıklamıyor. API anahtarı şart, çağrılar kredi tüketiyor. Kalıcı anahtarsız sağlayıcı koşuluna uymuyor.
- [EXAPI / Execross](https://execross.com/resources/api) resmî sayfasında anahtarlı ücretsiz katman için saatte 60, ayda 500 istek yazıyor. 100 ms iddiası gerçek dosya akışı veya galeri bütünlüğü testi değil. Anahtar olmadan canlı doğrulanamadı; varsayılan zincire eklenmedi.
- [Apify Ultra-Fast Social Media Downloader](https://apify.com/rover-omniscraper/media-downloader-actor) sonuç başına ücretli; kendi açıklamasına göre içinde Cobalt çalıştırıyor. Mevcut Cobalt'tan bağımsız motor sayılmaz.
- [TikHub](https://tikhub.io/pricing) kayıtla yaklaşık 50 deneme isteği veriyor, sonrasında kullanım başına ücretli. [RedFox SDK](https://github.com/redfox-data/redfox-python-sdk) API anahtarıyla çalışan veri hizmetinin istemcisi; ücretsiz ve sınırsız medya sağlayıcısı olduğu doğrulanmadı.
- [Qivaryn](https://pypi.org/project/qivaryn-engine/) gerçekten kendi Instagram, TikTok, YouTube ve Facebook çıkarıcılarını kullandığını ve `yt-dlp` gerektirmediğini belgeliyor. Bu listedeki gerçek bağımsız motor adayı; ancak yeni yerel paket ve bazı akışlar için FFmpeg gerektiriyor, karusel ve hız canlı test edilmedi. Kullanıcının daha fazla yerel kurulum istememe tercihi nedeniyle kurulmadı veya rota sayılmadı. Lisansı özel atıf koşullu.
- [Elengrab](https://github.com/neosy/elengrab) 1.000+ site kapsamını `yt-dlp` çıkarıcılarından alıyor; ek bağımsız provider değil. `socdl` ve `ytconv` de mevcut `yt-dlp`/`gallery-dl` motorlarına arayüz sunuyor. Aramada bulunan `socdl` deposu doğrudan açıldığında 404 döndüğü için ayrıca doğrulanamadı.
- [JIRO](https://github.com/DevAnimecx/jiro) arama ve Markdown sayfa kazıma projesi. Sosyal gönderi kazıma araçları kendi README'sindeki Pro katmanında; ücretsiz tam medya indirme yedeği değil.

### Reddit topluluğu ve Qivaryn kaynak kodu (12 Kasım 2026)

- [r/DataHoarder](https://www.reddit.com/r/DataHoarder/comments/1lcuzu9/social_media_downloading_alternatives/) ve [r/selfhosted](https://www.reddit.com/r/selfhosted/comments/1diwdxk) yorumlarında `gallery-dl`, `yt-dlp`, JDownloader, Cobalt ve Downr sık geçiyor. Bunlar bağımsız hız ölçümleri değil; çoğu, tekil bağlantı veya profil toplu indirme deneyimi. Botun ilk üç sağlayıcı yarışına yeni kanıtlanmış API sağlamıyor.
- [Downr](https://github.com/downr) ücretsiz web arayüzü sunuyor, ancak kendi GitHub açıklaması web uygulamasının kapalı kaynak olduğunu söylüyor. Belgelenmiş geliştirici API'si bulunamadı; kullanıcı yorumundaki "hızlı" ifadesi bot entegrasyonu için yeterli değil. ReelPluck da kullanıcıların önerdiği Instagram sitesi, fakat açık API ve tam karusel çıkışı doğrulanmadı.
- [Qivaryn kaynak kodu](https://github.com/usamanadim/qivaryn-engine) 16 Ekim 2026'da açılmış; 12 Kasım'daki GitHub verisinde 0 yıldız, 0 fork ve açık issue yok. `pyproject.toml` 0.2.5 sürümünü, `requests`, `click`, `tqdm` bağımlılıklarını ve özel atıf lisansını gösteriyor. Instagram kodu web/mobile JSON, HTML ve Open Graph yollarını; TikTok kodu mobil sayfa ve içerik API'sini; Facebook kodu sayfa içindeki video URL'lerini; YouTube kodu InnerTube istemcilerini kullanıyor. `yt-dlp` sarmalayıcısı değil, ancak botun mevcut doğrudan çıkarım yollarıyla aynı platform uçlarına temas ediyor. YouTube şifreli akışları JavaScript çözücüsü olmadan destekleyemediği kendi kaynak kodunda yazıyor. Reddit/X desteği yok. Henüz canlı dosya ve hız testi yapılmadığından rota sayısına eklenmedi.

## Ek sağlayıcı doğrulamaları (12 Kasım 2026)

Kaynak kodları incelenerek anahtarsız dört web yedeği eklendi. Yükseltilmiş komut ortamında örnek herkese açık gönderilerle gerçek medya indirmesi doğrulandı. InDown Instagram karuselinde dört fotoğrafın tamamını, reels'te videoyu; TikTokIO ve btch TikTok videosunu; RapidSave Reddit videosunu indirdi. btch'nin Instagram ve Facebook örnekleri zaman aşımına uğradığı için o iki platformda varsayılan sıraya konmadı. Sağlayıcı adı bot logunda görünür.

| Yeni rota | Platform / kapsam | Kaynak | Durum |
| --- | --- | --- | --- |
| Instagram embed | Instagram reel, /tv/ videosu, fotoğraf ve karusel gönderileri | Instagram'ın `/reel|p|tv/{code}/embed/` sayfası | `contextJSON` içindeki `shortcode_media` ve `edge_sidecar_to_children` okunur; karuselde tüm slaytlar sırayla alınır. Kısa/bot User-Agent ile giriş duvarı atlanıyor; tam tarayıcı UA'sında sayfa login duvarı döndürüyor. Tek bir slaytın URL'si bile eksikse albüm başarısız sayılır. |
| Threads Downloader | Threads video, fotoğraf ve karusel | [threadsphotodownloader.com](https://threadsphotodownloader.com/) `/download` formu | Normal gönderi bağlantılarında çerezsiz ilk yoldur. Form token'ı alınıp gönderi POST ediliyor, `/download/{job}/status` medya listesi verene kadar yoklanıyor. Sitenin proxy adresi (`cdn.threadsphotodownloader.com/{host}/{path}`) orijinal Instagram CDN adresine çevrilip dosya doğrudan oradan akıtılıyor; kalite tam boy. Site yazarın profil resmini de "Threads media" olarak listeliyor; `-19` / `t1.*-9` avatar yolları eleniyor. Çerez varsa `threads-page` oturumlu yedek olarak kalır. 13 Kasım canlı testinde `/share/BAaTVei4V4` oturumla iki görsele çözüldü; kanonik örneklerde çerezsiz fotoğraf ve video alındı. |
| InDown | Instagram post, reel ve karusel | [Scrapr InDown kodu](https://github.com/coflyn/scrapr/blob/main/lib/instagram/indown/index.js) | `api/ajaxSearch` yanıtındaki tüm medya öğeleri okunuyor; eksik aktarım başarısız sayılıyor. Hızlı olduğu ve karuseli tam verdiği için ilk yarış grubunda. |
| TikTokIO | TikTok video ve fotoğraf slaytları | [Scrapr TikTokIO kodu](https://github.com/coflyn/scrapr/blob/main/lib/tiktok/tiktokio/index.js) | Video varyantından biri veya slayttaki tüm fotoğraflar alınıyor. |
| RapidSave | Reddit videosu, birleşik sesli akış | [Scrapr RapidSave kodu](https://github.com/coflyn/scrapr/blob/main/lib/reddit/rapidsave/index.js) | Fotoğraf galerisi yolu olarak kullanılmıyor. |
| btch-downloader-api açık demosu | TikTok video | [API belgeleri](https://github.com/boy-offi9-inc/btch-downloader-api#api-reference) | JSON sonucu ve sunucunun korumalı `/api/fetch-media` akışıyla 10,9 MB video indirildi. Instagram/Facebook zaman aşımına uğradı; fotoğraf albümlerine uygulanmıyor. |

Diğer öneriler:

- [SnapSave](https://github.com/coflyn/scrapr/blob/main/README.md) kendi API'si belgelenmeyen, paketlenmiş JavaScript çıktısı çözümlenen bir site. Canlı yanıt doğrulanmadan varsayılan bot rotasına alınmadı. `snapsave-media-downloader` paketinin TikTok/X yolları da ayrı web sitelerine gidiyor; dört platform için tek bağımsız motor olarak sayılmamalı.
- [SSSTik/SnapTik](https://github.com/coflyn/scrapr/blob/main/README.md) ek TikTok seçenekleri, ancak bu ortamda canlı dosya testi mümkün olmadığından varsayılan rotaya alınmadı. Önce eklenen TikTokIO fotoğraf slaytı da destekliyor.
- [fdown](https://github.com/coflyn/scrapr/blob/main/README.md) Chrome/Puppeteer gerektiriyor. [Instagram Direct Media URL Extractor](https://github.com/mustafachyi/Instagram-Direct-Media-URL-Extractor) ayrı sunucu/proxy gerektiriyor. Kullanıcının yerel kurulum istememe tercihine uymuyorlar.
- [redvid](https://github.com/elmoiv/redvid) Reddit videosuna odaklı yerel Python aracı; RapidSave ve mevcut yollar varken yeni kurulum yapılmadı.
- [rahad-all-downloader](https://pypi.org/project/rahad-all-downloader/) TikWM ve VxTwitter gibi mevcut yolları sarmalıyor; tek başına bağımsız sağlayıcı değil. `teamdev-aio` için doğrulanabilir resmî paket/kaynak bulunamadı.
- [MediaFetch](https://github.com/lukedunsmoto/mediafetch) yt-dlp arayüzü. [FileShot API](https://fileshot.io/api-docs) indirme çıkarıcısı değil dosya paylaşımı API'si; ayrıca ücretli Creator katmanı ve anahtar istiyor. [VidFetch](https://www.vidfetch.dev/) ayda 1 GB ücretsiz, sonrası ücretli. [Video Download API](https://videodownloadapi.com/pricing) 30 günde 10 dakika ücretsiz; dolayısıyla sınırsız ücretsiz yedek değiller.

### Hesap ve sunucu gerektiren adaylar

- [scrapeMM](https://pypi.org/project/scrapeMM/) gerçekten açık kaynak ve metin, görsel, video içeren çıktı üretiyor. Ancak istemci paketi tek başına indirmiyor: Docker sunucusu, Chromium, Firecrawl ve bazı kaynaklar için ek sırlar/servisler gerekiyor. Genel video çıkarımı yt-dlp de kullanıyor. Hız ve yerel kurulum tercihleri nedeniyle bu bota eklenmedi; kendi sunucusunu çalıştırmak API kesintisi veya platform sınırlarını sıfırlamıyor.
- [saveapi](https://saveapi.org/en/docs/) hesap/API anahtarı ve başlangıçta 1.000 kredi istiyor; başarılı indirme kredi tüketiyor. [TrueScrape](https://truescrape.com/pricing) ücretsiz 1.000 krediyi yalnızca bir kez veriyor. [ScrapeCreators](https://scrapecreators.com/) 100 başlangıç kredisi veriyor, sonrası kredi satın alma modeli. Kullanıcının kalıcı ücretsiz ve anahtarsız sağlayıcı koşuluna uymuyorlar.
- [Apify X Media Scraper](https://apify.com/feedminer/x-media-scraper) Apify hesabı ve ücretli sonuç modeli gerektiriyor. Tweet metni ve medya zaten botun X akışında ayrı çekiliyor; mevcut kart çizimi ve indirme zincirine yeni zorunlu hesap eklenmedi.
- `btch-downloader-api` ve `gallery-dl` mevcut listede zaten değerlendirildi. btch TikTok videosunda canlı doğrulandı; Instagram/Facebook örnekleri zaman aşımına uğradı. `gallery-dl` fotoğraf ve karusel yedeği olarak kalıyor.

### Metadata yedeği ve FxEmbed

- [FxEmbed](https://github.com/FxEmbed/FxEmbed/blob/main/README.md) Cloudflare Worker olarak veya Docker içindeki yerel Wrangler çalıştırıcısıyla barındırılabiliyor. Ancak kendi örneğini açmak platformun erişim sınırlarını veya Cloudflare kota/kesinti riskini kaldırmaz; ayrıca hesap/deploy ayarı ister. Bot şimdiden X Syndication, VxTwitter ve FxTwitter'ı tweet metni/kartı için deniyor. Medyasız X gönderisi koyu kart olarak iletiliyor.
- Genel Open Graph yedeği eklendi: tüm medya sağlayıcıları başarısız olursa açıklama, `og:title`, `og:description` ve güvenilir platform CDN'inden `og:image` varsa kaynak düğmeli Discord embed'i gönderiliyor. Metin uzun olduğunda tümü birden fazla embed'e bölünüyor. Canlı Facebook `share/p` örneğinde üç alan da geldi; örnek Instagram reels sayfasında dışarıdan metadata alınamadı. Bu yol yalnızca sayfanın herkese açık metadata sunduğu durumda içerik verir.
- Reddit'in anonim `.json` yolu canlı denemede yanıt vermedi. Kodda medya için son çare olsa da güvenilir yol sayılmıyor. Gönderi metni için Atom ve arşiv denemeleri var; gönderi ve +18 durumu doğrulanamazsa güvenlik kapısı Open Graph/Discord önizlemesini göndermiyor.

## Ücretsiz web servisleri araştırması (11 Kasım 2026)

- [AllDL](https://ahm7xmakki.com/alldl) beş platform için anahtarsız API belgeliyor; ancak eski `/api/alldl` bugün JSON yerine taşınma sayfası döndürdü. Yeni `freeapi.pro` adresine canlı bağlantı kurulamadı. Bu yüzden çalışan rota olarak eklenmedi.
- [WeirdDL](https://weirddl.sbs/) X ve Instagram için anahtarsız `/api/info` belgeliyor. Kendi örnek X bağlantısında `extract_failed` yanıtı alındı; örnek Instagram bağlantısına bağlantı kurulamadı. Çalışan rota sayılmadı.
- [curl-x](https://www.curl-x.com/) beş platformun tamamını desteklediğini belirten ücretsiz bir web sitesi sunuyor. Belgelenmiş geliştirici API'si bulunamadı ve sayfadaki kaynak kod bağlantısı 404 döndürdü. Bot entegrasyonuna alınmadı.
- [Cobalt](https://github.com/imputnet/cobalt/blob/main/docs/api.md) açık kaynak bir web API'sidir. Resmî belgeleri, genel örneklerinin başka projelerce izinsiz kullanılmamasını söylüyor; bu nedenle mevcut yerel örnek korunuyor. [Topluluk örnekleri dizini](https://cobalt.directory/) var, ancak örnek sahibinin bot erişimine izin verdiği doğrulanmadan otomatik kullanılmıyor.
- [GenDownload](https://gendownload.com/api-docs) anahtarsız API'si TikTok, Instagram, Facebook ve X'te canlı JSON medya seçenekleri döndürdü; TikTok örneğinde 3,36 MB MP4 dosyası geçici klasöre indirildi. Reddit isteğinde ağ hatası alındığı için o platforma bağlanmadı. Hizmetin kendi altyapısının ve çıkarıcılarının ayrıntıları yayımlanmadığı için motor bağımsızlığı bilinmiyor.
- [RapidSave](https://rapidsave.com/) için 11 Kasım'da belgelenmiş bot API'si bulunamamıştı. Daha sonra [Scrapr RapidSave çıkarım yolu](https://github.com/coflyn/scrapr/blob/main/lib/reddit/rapidsave/index.js) incelenip Reddit video yedeği olarak eklendi; bugünkü rota listesi bu yeni durumu gösterir.
- [Satoru web API](https://www.npmjs.com/package/%40satorufx/mediadownloader) anahtarsız, 100 istek penceresi olduğunu belirtiyor; yayımlanmış istemcide `GET /api/downallv2?url=...` çağrısı görüldü. Instagram ve TikTok örneklerinde JSON yanıtı alındı, ancak tekrar denemelerde TLS hataları ve zaman aşımı yaşandı; beş platform için kararlı medya akışı doğrulanmadığından varsayılan yola eklenmedi.
- [SaveDock](https://savedock.pro/mcp-server/) Facebook, Instagram, TikTok ve X için anahtarsız barındırılan MCP/REST hizmeti sunuyor; ücretsiz MCP sınırı IP başına saatte 10 video. Kendi açıklamasına göre çıkarıcı motoru `yt-dlp`; fotoğraf/galeri kapsamı da sınırlı. Mevcut yerel `yt-dlp` ile aynı motoru kullandığından ayrı çıkarıcı sayılmadı.
- [DL Reddit](https://github.com/Lachee/reddit-download/) açık kaynak kodlu ve herkese açık web örneği sunuyor; bu ortamdan örnek gönderiye bağlantı zaman aşımına uğradı. Belgelenmiş bot API'si doğrulanamadığından eklenmedi. Projenin açıklamasına göre kendi sunucusu Reddit OAuth hesabı kullanıyor.
- [Reddit'in geçiş takvimine](https://www.reddit.com/r/redditdev/comments/1wubcvf/moving_data_api_apps_to_the_developer_platform/) göre yeni public API erişim talepleri 31 Ekim 2026'da durdu; kayıtlı olmayanların erişimi 12 Ocak 2027'den itibaren kaldırılacak, kalan public erişim Mart 2027'de sona erecek. Yalnızca kayıt olmak kalıcı erişim sağlamıyor.
- Botun anahtarsız web yolları FxTwitter/VxTwitter, TikWM, GenDownload, InDown, TikTokIO, RapidSave, btch ve Social Media Downloader gibi farklı servisleri içerir. Etkin rota listesi için yukarıdaki `provider_order()` komutunu kullanın; Reddit'in anonim JSON'u güvenilir yedek sayılmaz.

## İncelenip varsayılan yola alınmayanlar

- [Apify](https://github.com/apify/apify-mcp-server): platformlar için çok sayıda Actor mevcut, ancak hesap/anahtar ve kullanım ücreti gerektirir; Actor çıktıları ve medya lisansları farklıdır.
- [SocialCrawl](https://www.socialcrawl.dev/docs/api-reference): beş platformun gönderi verileri için kapsamlı bir API; medya dosyası indirme ürünü olarak belgelenmediği ve anahtar/kredi gerektirdiği için indirme sağlayıcısı sayılmadı.
- [FastSaver](https://fastsaver.io/blog/fastsaver-api-getting-started): sınırlı ücretsiz kredi sonrası ücretli olduğu için kullanıcının yalnızca ücretsiz servis tercihine uymuyor.
- [Lux](https://github.com/iawia002/lux): yerel Go programı; web API'si değil ve yayımlanmış ikilisi eski. Kurulumu ve entegrasyonu kaldırıldı.
- `gallery-dl-rs` uyumluluk modu: doğrudan `gallery-dl` çağırır; bağımsız yedek değildir.
- `yt-dlp` kullanan diğer “downloader” web siteleri ve botlar: farklı arayüz sunsalar da aynı çıkarıcı başarısızsa genellikle aynı yerde kırılır.
