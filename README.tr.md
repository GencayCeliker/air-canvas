[English](README.md) | **Türkçe**

# Air Canvas

[![tests](https://github.com/GencayCeliker/air-canvas/actions/workflows/tests.yml/badge.svg)](https://github.com/GencayCeliker/air-canvas/actions/workflows/tests.yml)
![Python](https://img.shields.io/badge/python-3.9%2B-blue)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)

**Parmağınla havaya çiz.** Air Canvas sıradan bir web kamerasıyla elini takip eder, işaret parmağını fırçaya dönüştürür, elle çizdiğin yamuk daire, dikdörtgen ve üçgenleri kusursuz şekillere çevirir ve Türkçe sesli komutları anlar.

<p align="center"><img src="docs/demo.gif" alt="Air Canvas: hazır şablondan dron gövdesi, yapay zekânın çizim kontrolü, ardından 5 inç pervane" width="640"></p>

> Air Canvas, masaüstü asistan projem Moly/Jarvis'in bir modülü olarak başladı; artık tek başına çalışan bir uygulama olarak da kullanılabiliyor.

## Özellikler

- **El hareketiyle çizim:** MediaPipe ile el takibi. Özel bir donanım gerekmez, web kamerası yeterli.
- **Şekil tanıma:** Kabaca çizilen çizgiler düzgün bir doğruya, üçgene, kareye, dikdörtgene, daireye veya elipse dönüşür (eğik çizilse de yönü korunur). Düzeltme sadece o çizgiyi değiştirir, diğer çizimlere dokunmaz.
- **Türkçe sesli komutlar:** *"kırmızı daire çiz"*, *"sol üste büyük mavi yıldız"*, *"kırmızı daire ve yeşil kare çiz"* (birden çok şekil, yan yana), *"silgi"*, *"tahta modu"*, *"kalınlaştır"*, *"geri al"*, *"kaydet"*… Şekiller: daire, kare, dikdörtgen, üçgen, elips, altıgen, yıldız, çizgi.
- **Sesli notlar:** Komut olmayan her söz, çizimin yanına not olarak kaydedilir. Bilgisayarda yerel bir dil modeli (LM Studio / Ollama) çalışıyorsa uzun notlar otomatik özetlenir.
- **Doğal Türkçe sesli geri bildirim:** Edge nöral sesi (`tr-TR-AhmetNeural`); internet yoksa gTTS veya Türkçe Windows sesi.
- **Geri al / yinele** (sınırlı geçmiş), silgi, altı renk, ayarlanabilir fırça, tahta modu.
- **Kaydetme:** Çizimin şeffaf PNG'si, ekranın tam görüntüsü ve sesli notların bulunduğu bir metin dosyası.
- **Fare desteği:** Fareyle de çizebilir, tekerlekle fırça kalınlığını değiştirebilirsin.
- **Yapay zekâ destekli 3D tasarım asistanı:** *"dron tasarla"*, *"M3 vida çiz"* veya *"model araba yap"* de; vidadan araba modeline kadar 3D baskıya uygun bir tasarım hazırlanır. Tasarım parçalara ayrılır, üzerinden çizebileceğin yarı saydam bir 3D rehber olarak gösterilir, adım adım sesli anlatılır ve tablaya hazır STL dosyaları olarak kaydedilir.
- **Yerel modelle, internetsiz çalışma** (`--offline`): tasarımları LM Studio / Ollama yapar, konuşma faster-whisper ile tanınır ve yerel Windows sesiyle okunur — hiçbir veri bilgisayardan çıkmaz. Yapay zekâ tasarlamadan önce eksik ölçüleri sorabilir ve tasarımı tuvale çizgi taslağı olarak çizebilir.
- **Varsayılan olarak tam ekran** (`F` ile pencereye geçilir).
- Titremeyi azaltan yumuşatma ve kısa bir tolerans süresi sayesinde, el takibi bir an kaybolsa da çizgi kopmaz.

## Kurulum

Python 3.9+ ve bir web kamerası gerekir.

```bash
git clone https://github.com/GencayCeliker/air-canvas.git
cd air-canvas
pip install -e ".[voice]"      # veya: pip install -e .   (ses özellikleri olmadan)
pip install -e ".[ai]"         # isteğe bağlı: 3D tasarım asistanı için Claude
pip install -e ".[offline]"    # isteğe bağlı: internetsiz konuşma tanıma (faster-whisper)
```

MediaPipe el modeli (~7,5 MB) ilk açılışta otomatik indirilir.

## Çalıştırma

```bash
air-canvas                     # veya: python -m air_canvas
air-canvas --board             # düz tahta ile başla
air-canvas --camera 1 --no-voice
air-canvas --windowed          # tam ekran yerine pencerede başla
air-canvas --no-shape-assist   # şekil tanıma kapalı başla (aç/kapat: A)
air-canvas --design "dron"     # açılışta bir 3D tasarım hazırla
air-canvas --project dron     # projeler/dron klasörünü aç (ölçüler, fotoğraflar, STL parçalar)
air-canvas --offline           # internetsiz: yerel LLM, Whisper ve yerel ses
air-canvas --ai local          # tasarımı yerel LLM yapsın (çevrimiçi ses tanıma serbest)
air-canvas --help
```

## Kontroller

| Hareket | İşlev |
| --- | --- |
| ☝️ İşaret parmağı açık | Çiz |
| ✌️ İşaret + orta parmak | Seç — üst menüdeki bir butonun üzerine gelerek bas |
| 🖐️ Dört parmak açık | Fırça kalınlığı — başparmak ile işaret parmağını yaklaştır/uzaklaştır |

| Tuş | İşlev | Tuş | İşlev |
| --- | --- | --- | --- |
| `1`–`6` | Renk | `E` | Silgi / kalem |
| `Z` / `Y` | Geri al / yinele | `C` | Temizle |
| `S` | Kaydet | `V` | Sesli komut / not |
| `B` | Tahta modu | `A` | Şekil tanıma aç/kapat |
| `+` / `-` | Fırça kalınlığı | `H` | Yardım panelini göster/gizle |
| `Q` / `Esc` | Çıkış | `F` | Tam ekran / pencere |
| `N` / `P` | Sonraki / önceki tasarım adımı | `R` | Adımı tekrarla |
| `G` | 3D rehberi göster/gizle | `X` | Tasarımı STL olarak kaydet |
| `K` | Yapay zekâ çizimini kontrol etsin | `T` | Tasarım taslağını tuvale çiz |
| `I` | 3D modeli incele (sürükle / ok tuşları döndürür, tekerlek yakınlaştırır, `Esc` kapatır) | | |

## 3D tasarım asistanı

`V` tuşuna bas ve ne yapmak istediğini söyle. İçinde *tasarla*, *3D* ya da *vida*, *somun*, *dron*, *pervane*/*kanat*, *motor*, *araba*, *tekerlek*, *fan*, *SSD* gibi bir nesne geçen her cümle bir tasarım başlatır.

1. Yapay zekâ nesneyi basılabilir parçalara ayırır (delikler, standart ölçüler, en az 1,2 mm duvar, dik sarkma yok) ve her parçayı kutu, silindir/koni/altıgen ve kürelerle modeller.
   Tasarımı belirgin biçimde değiştirecek bir ölçü eksikse yapay zekâ önce sorar (*"Kaç inç pervane kullanacaksın?"*) ve cevabını dinler. *"Sen karar ver"* dersen standart ölçüler kullanılır, *"vazgeç"* dersen istek iptal edilir. İçinde sayı geçen isteklerde soru sorulmaz.
2. Model, çizdiğin çizgilerin altında yarı saydam bir 3D rehber olarak görünür. Sıradaki parça vurgulanır, sağdaki panelde talimatı ve milimetre cinsinden ölçüsü yazar.
3. Parçalar arasında *"sonraki"* / *"önceki"* / *"tekrar"* diyerek gezin. *"Kontrol et"* deyince yapay zekâ çizimine bakar; istediğini de sorabilirsin (*"duvar kalınlığı kaç olmalı?"*). *"Taslağı çiz"* (veya `T`) tasarımın dış hatlarını tuvale mürekkep olarak çizer; üzerinden geçebilir, kaydedebilir veya geri alabilirsin.
4. Modeli incelemek için *"modeli incele"* de, `I`'ya veya **INCELE** düğmesine bas. Bu sırada çizim durur; fareyle veya işaret parmağınla sürükleyerek (ya da ok tuşlarıyla) döndür, tekerlekle yakınlaştır. `N`/`P` tek parçayı milimetre ölçüsüyle, `R` tüm montajı gösterir. `I` veya `Esc` çizime döndürür.
5. *"STL"* de (veya `X`'e bas). Her parça tablaya düz oturan ayrı bir STL olarak; montaj önizlemesi, `talimatlar.txt` ve `tasarim.json` ile birlikte `<kayıt klasörü>/3D/` altına yazılır.

**Hangi yapay zekâ kullanılıyor?** `ai` eki kuruluysa ve bir Anthropic kimlik bilgisi (`ANTHROPIC_API_KEY` veya `ant auth login` profili) varsa, tarif edebildiğin her şeyi Claude (`claude-opus-5-5`, yapılandırılmış JSON çıktı) tasarlar. Claude yoksa isteğe uyan yerleşik şablon önce kullanılır; küçük yerel modeller dron veya vida için bile kaba kutu ve silindirler üretiyor. Şablonu olmayan her şey yerel dil modeline (LM Studio / Ollama) gider; basit bir kap dışında tek parça, iki şekilli bir cevap bir kez geri gönderilir. Yerleşik şablonlar internet olmadan da çalışır: cıvata, somun, dron, dron kolu, pervane (inç ve kanat sayısı konuşmadan, ör. *"5 inç pervane"*), fırçasız motor maketi, araba, tekerlek, fan ızgarası, SSD braketi. Açıkça seçmek için: `--ai auto|claude|local|template`.

| Dron gövdesi, kol vurgulu | Yapay zekâ çizimi kontrol ediyor | 5 inç pervane |
| --- | --- | --- |
| ![Dron şablonu](docs/tasarim-dron.png) | ![Çizim kontrolü](docs/cizim-kontrolu.png) | ![Pervane](docs/pervane.png) |

### Proje klasörü

Tek bir nesne üzerinde kendi referanslarınla çalışmak için *"dron projesini aç"* de (veya `--project dron` ile başlat). `<kayıt klasörü>/projeler/dron/` ilk kullanımda oluşturulur:

```
bilgi.txt     ölçüler ve notlar, ör. "pervane: 7 inç", "motor: 2207", "kol: 150 mm", "fc: 20 mm", "vida: M3x12"
foto/         referans fotoğraflar
referans/     hazır STL parçalar
```

- `anahtar: değer` satırları, sen sayı söylemediğinde yerleşik şablonlara gider (pervane boyu ve kanat sayısı, kol uzunluğu, motor ve uçuş kontrolcüsü delik aralığı, vida ölçüsü). Tüm notlar Claude'a veya yerel modele bağlam olarak verilir ve ölçü sorusu sorulmaz.
- Fotoğraflar bir kez görsel modele (Claude veya yerel VL modeli) gösterilir. Model yapıyı (tip, parçalar, yerleşim) tasarımcı için özetler; ölçüler her zaman `bilgi.txt`'den gelir, çünkü fotoğraftan milimetre güvenilir okunamaz.
- `referans/` içindeki her su geçirmez STL bir parça olur: 3D rehberde görünür, montajda tasarımın yanına yerleşir ve onunla birlikte dışa aktarılır. Açık ağlar ve çok büyük dosyalar (80 000 üçgenden fazla) mesajla atlanır. İndirilen modellerin lisansına dikkat et; oyun asetleri genelde su geçirmez değildir ve gerçek ölçü taşımaz, onları yalnızca görsel referans olarak kullan.
- Kapatmak için *"projeyi kapat"* de.

### İnternetsiz / yerel model

```bash
pip install -e ".[voice,offline]"
air-canvas --offline
```

1. LM Studio (`:1234`) veya Ollama'yı (`:11434`) başlat ve bir model yükle. Tasarım için adında *coder* geçen (ör. `qwen2.5-coder` 7B/14B), `kontrol et` için *VL* bir model (ör. `qwen2.5-vl`) otomatik seçilir; `AIR_CANVAS_LLM_DESIGN_MODEL` / `AIR_CANVAS_LLM_VISION_MODEL` ile değiştirilebilir. Sorular ve özetler zaten yüklü olan modeli kullanır; GPU belleğine ikinci bir model alınmaz.
2. Whisper konuşma modeli (varsayılan `small`) ilk kullanımda **bir kez** indirilir; sonra yerel önbellekten, hiçbir ağ isteği yapılmadan yüklenir. Tamamen internetsiz bir bilgisayar için bir kez internete bağlıyken çalıştır ya da `AIR_CANVAS_WHISPER_MODEL` ile yerel bir model klasörü göster. Whisper'a komut kelimeleri ipucu olarak verilir; bu sayede kısa Türkçe komutlar çok daha güvenilir tanınır.
3. Sesli geri bildirimde Türkçe Windows sesi (*Microsoft Tolga*) kullanılır; ses yalnızca yeni (OneCore) ses olarak kurulu olsa da çalışır. Ses yoksa **Windows Ayarlar → Saat ve dil → Konuşma** bölümünden ekle.

Yanıtlar akış olarak alınır ve HUD'da ilerleme görünür (`412 token (46/sn)`). `AIR_CANVAS_LLM_TIMEOUT` (varsayılan 180 sn), sunucunun **hiç veri göndermeden** bekleyebileceği süredir; yavaş ama çalışan bir model yarıda kesilmez. Model tamamen GPU'daysa 7B bir model birkaç saniyede tasarlar. HUD'da *yavas* yazıyorsa model işlemcide çalışıyor demektir (bkz. Sorun giderme). Model geçersiz JSON döndürürse hatası söylenerek bir kez daha istenir.

## Ayarlar

| Ortam değişkeni | Varsayılan |
| --- | --- |
| `AIR_CANVAS_SAVE_DIR` | `~/Pictures/AirCanvas` |
| `AIR_CANVAS_MODEL` | kullanıcı önbellek klasörü (`%LOCALAPPDATA%\air_canvas` / `~/.cache/air_canvas`) |
| `AIR_CANVAS_VOICE` | `tr-TR-AhmetNeural` (veya `tr-TR-EmelNeural`) |
| `AIR_CANVAS_LLM_URL` | sırasıyla LM Studio `:1234` ve Ollama `:11434` denenir |
| `AIR_CANVAS_LLM_MODEL` | her iş için tek model (boşsa işe göre otomatik seçilir) |
| `AIR_CANVAS_LLM_DESIGN_MODEL` | 3D tasarım (JSON) modeli; varsayılan: adında "coder" geçen model |
| `AIR_CANVAS_LLM_VISION_MODEL` | çizim kontrolü (görsel) modeli; varsayılan: adında "vl"/"vision" geçen model |
| `ANTHROPIC_API_KEY` | 3D tasarım asistanında Claude'u etkinleştirir |
| `AIR_CANVAS_CLAUDE_MODEL` | `claude-opus-5-5` |
| `AIR_CANVAS_AI` | `auto` (Claude → şablon → yerel LLM), `claude`, `local` veya `template` |
| `AIR_CANVAS_OFFLINE` | `1` = `--offline` ile aynı |
| `AIR_CANVAS_STT` | `auto` (önce Google, sonra Whisper), `google` veya `whisper` |
| `AIR_CANVAS_WHISPER_MODEL` | `small` (veya `tiny`/`base`/`medium` ya da yerel klasör) |
| `AIR_CANVAS_LLM_TIMEOUT` | tasarım sırasında yerel modelin sessiz kalabileceği süre: `180` sn |
| `AIR_CANVAS_CLARIFY` | `1`; `0` = tasarımdan önce ölçü sorma |

## Kütüphane olarak kullanım

```python
from air_canvas import AirCanvas

canvas = AirCanvas(save_dir="cizimler")
canvas.start_async(on_finish=print)   # engellemez; engelleyerek çalıştırmak için canvas.run()
```

Kendi ses motorunu (`speaker=` — `say(text)`, `is_busy()` ve isteğe bağlı `stop()` metotları olan herhangi bir nesne), konuşma tanıyıcını (`listener=` — metin döndüren `listen()`), dil modeli istemcini (`llm=`) ve tasarım asistanını (`assistant=`) bağlayabilirsin. `AirCanvas(offline=True)` ve `ai_mode=` komut satırı bayraklarıyla aynıdır.

## Şekil tanıma nasıl çalışıyor?

Bir çizgi bittiğinde noktaları `approxPolyDP` ile sadeleştirilir ve dışbükey zarfı (convex hull) üzerinden incelenir. Başlangıç–bitiş mesafesinin çizgi uzunluğuna oranı, doğruyu kapalı şekillerden ayırır. Köşe sayısı, dairesellik (`4πA/P²`) ve doluluk oranı (alan ÷ sınırlayıcı kutu alanı) üçgen, dikdörtgen ve yuvarlak şekilleri birbirinden ayırır. `minAreaRect` ve `fitEllipse` yönü ve boyutu verir. Yeterince kapalı olmayan çizgiler çizildiği gibi bırakılır.

## Testler

```bash
pip install -e ".[dev]"
pytest
```

Testler şekil sınıflandırma, geri al/yinele, katman birleştirme, menü butonları, hareket zamanlaması, sesli komut çözümleme, ses kuyruğu, dinleme hataları, yerel model tasarımları ve kaydetmeyi kapsar. Hepsi sentetik veri ve sahte motorlarla çalışır; kamera, mikrofon veya internet gerekmez.

## Proje yapısı

```
air_canvas/
  canvas.py      Çizim motoru: el takibi, hareketler, çizgiler, şekiller, menü, kaydetme
  speech.py      Türkçe sesli geri bildirim (Edge TTS → gTTS → Türkçe Windows sesi), kesilebilir
  listen.py      Konuşma tanıma: çevrimiçi Google, çevrimdışı faster-whisper, anlaşılır hata mesajları
  llm.py         İsteğe bağlı yerel dil modeli istemcisi (LM Studio / Ollama): özet, tasarım, soru, çizim kontrolü
  textutil.py    Model çıktısını seslendirmeye hazırlar (düşünce blokları, markdown, emoji)
  design.py      3D tasarımlar: parçalar, katı modelleme (manifold3d), STL, 3D önizleme, şablonlar
  project.py     Proje klasörleri: bilgi.txt ölçüleri, referans fotoğraflar, referans STL parçalar
  ai.py          Tasarım asistanı: önce Claude, sonra yerleşik şablonlar, sonra yerel dil modeli
  config.py      Varsayılan yollar (ortam değişkenleriyle değiştirilebilir)
  __main__.py    Komut satırı uygulaması (air-canvas)
tests/           pytest testleri, yalnızca sentetik veri
```

## Sorun giderme

| Sorun | Çözüm |
| --- | --- |
| "Kamera açılamadı" | Kamerayı kullanan diğer uygulamaları kapat (Teams, Zoom, tarayıcı sekmeleri) veya `--camera 1` dene. |
| `pip`, `mediapipe` paketini bulamıyor | Python sürümün MediaPipe için fazla yeni olabilir. Python 3.10–3.12 kullan. |
| Ses yok veya İngilizce aksanlı | Ses eklerini kur (`pip install -e ".[voice]"`). Edge TTS ve gTTS internet ister; internetsiz kullanımda Türkçe Windows sesi (*Microsoft Tolga*) kullanılır. Ses yoksa **Windows Ayarlar → Saat ve dil → Konuşma** bölümünden ekle. |
| Sesli komutlar algılanmıyor | Asistan artık nedenini söyler: mikrofon/`PyAudio` yok, internet yok ya da konuşma anlaşılamadı. Çevrimiçi tanıma Google'ın ücretsiz servisini kullanır; internetsiz kullanım için `offline` ekini (faster-whisper) kur. |
| Yerel model yavaş (HUD'da *yavas*, saniyede birkaç token) | Model işlemcide çalışıyor. Çoğunlukla iki model aynı anda yüklüdür ve GPU belleği dolmuştur (logda uyarı çıkar). LM Studio'da tek model yüklü bırak ve *GPU Offload* ayarını en yükseğe çek; ya da `lms unload --all` ardından `lms load <model> --gpu max` çalıştır. RTX 4060 (8 GB) üzerinde 7B coder modeli bu şekilde saniyede 6 token'dan 46 token'a çıktı. |
| Yerel model sonuç vermiyor | LM Studio / Ollama sunucu kaydına bak, `AIR_CANVAS_LLM_TIMEOUT` değerini artır. Hangi modelin seçildiğini görmek için `-v` ile çalıştır. |
| `pip check` bir `click` çakışması gösteriyor | `gTTS` `click<8.2` ister; faster-whisper'ın getirdiği yeni `huggingface-hub` ise `click>=8.4` ister. Ekleri tek komutla kur (`pip install -e ".[voice,offline]"`), pip uyumlu sürümleri seçsin; ya da `pip install "huggingface-hub<1.0" "click<8.2"` çalıştır. |
| El modeli indirilemiyor | [`hand_landmarker.task`](https://storage.googleapis.com/mediapipe-models/hand_landmarker/hand_landmarker/float16/1/hand_landmarker.task) dosyasını elle indir ve `AIR_CANVAS_MODEL` değişkeniyle yolunu göster. |
| Çizgiler titriyor | Ortamı eşit aydınlat, elini kameradan 40–80 cm uzakta tut. |

## Yol haritası

- [x] Demo GIF'i
- [ ] İngilizce sesli komutlar ve arayüz
- [ ] İki elle hareketler (tuvali yakınlaştırma / kaydırma)
- [x] İnternetsiz konuşma tanıma (faster-whisper)
- [x] Yerel modelle tasarım ve çevrimdışı mod
- [ ] SVG olarak dışa aktarma

## Katkıda bulunma

Issue ve pull request'ler memnuniyetle karşılanır — bkz. [CONTRIBUTING.md](CONTRIBUTING.md). Değişiklikler [CHANGELOG.md](CHANGELOG.md) dosyasında listelenir.

## Lisans

[MIT](LICENSE) © Gencay Çeliker
