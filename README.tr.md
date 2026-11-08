[English](README.md) | **Türkçe**

# Air Canvas

[![tests](https://github.com/GencayCeliker/air-canvas/actions/workflows/tests.yml/badge.svg)](https://github.com/GencayCeliker/air-canvas/actions/workflows/tests.yml)
![Python](https://img.shields.io/badge/python-3.9%2B-blue)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)

**Parmağınla havaya çiz.** Air Canvas sıradan bir web kamerasıyla elini takip eder, işaret parmağını fırçaya dönüştürür, elle çizdiğin yamuk daire, dikdörtgen ve üçgenleri kusursuz şekillere çevirir ve Türkçe sesli komutları anlar.

<!-- TODO: demo GIF'i ekle, ör. docs/demo.gif -->

> Air Canvas, masaüstü asistan projem Moly/Jarvis'in bir modülü olarak başladı; artık tek başına çalışan bir uygulama olarak da kullanılabiliyor.

## Özellikler

- **El hareketiyle çizim:** MediaPipe ile el takibi. Özel bir donanım gerekmez, web kamerası yeterli.
- **Şekil tanıma:** Kabaca çizilen çizgiler düzgün bir doğruya, üçgene, kareye, dikdörtgene, daireye veya elipse dönüşür (eğik çizilse de yönü korunur). Düzeltme sadece o çizgiyi değiştirir, diğer çizimlere dokunmaz.
- **Türkçe sesli komutlar:** *"kırmızı daire çiz"*, *"silgi"*, *"tahta modu"*, *"kalınlaştır"*, *"geri al"*, *"kaydet"*…
- **Sesli notlar:** Komut olmayan her söz, çizimin yanına not olarak kaydedilir. Bilgisayarda yerel bir dil modeli (LM Studio / Ollama) çalışıyorsa uzun notlar otomatik özetlenir.
- **Doğal Türkçe sesli geri bildirim:** Edge nöral sesi (`tr-TR-AhmetNeural`); internet yoksa gTTS veya Türkçe Windows sesi.
- **Geri al / yinele** (sınırlı geçmiş), silgi, altı renk, ayarlanabilir fırça, tahta modu.
- **Kaydetme:** Çizimin şeffaf PNG'si, ekranın tam görüntüsü ve sesli notların bulunduğu bir metin dosyası.
- **Fare desteği:** Fareyle de çizebilir, tekerlekle fırça kalınlığını değiştirebilirsin.
- **Yapay zekâ destekli 3D tasarım asistanı:** *"dron tasarla"*, *"M3 vida çiz"* veya *"model araba yap"* de; vidadan araba modeline kadar 3D baskıya uygun bir tasarım hazırlanır. Tasarım parçalara ayrılır, üzerinden çizebileceğin yarı saydam bir 3D rehber olarak gösterilir, adım adım sesli anlatılır ve tablaya hazır STL dosyaları olarak kaydedilir.
- **Varsayılan olarak tam ekran** (`F` ile pencereye geçilir).
- Titremeyi azaltan yumuşatma ve kısa bir tolerans süresi sayesinde, el takibi bir an kaybolsa da çizgi kopmaz.

## Kurulum

Python 3.9+ ve bir web kamerası gerekir.

```bash
git clone https://github.com/GencayCeliker/air-canvas.git
cd air-canvas
pip install -e ".[voice]"      # veya: pip install -e .   (ses özellikleri olmadan)
pip install -e ".[ai]"         # isteğe bağlı: 3D tasarım asistanı için Claude
```

MediaPipe el modeli (~7,5 MB) ilk açılışta otomatik indirilir.

## Çalıştırma

```bash
air-canvas                     # veya: python -m air_canvas
air-canvas --board             # düz tahta ile başla
air-canvas --camera 1 --no-voice
air-canvas --windowed          # tam ekran yerine pencerede başla
air-canvas --design "dron"     # açılışta bir 3D tasarım hazırla
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
| `K` | Yapay zekâ çizimini kontrol etsin | | |

## 3D tasarım asistanı

`V` tuşuna bas ve ne yapmak istediğini söyle. İçinde *tasarla*, *3D* ya da *vida*, *somun*, *dron*, *araba*, *tekerlek*, *fan*, *SSD* gibi bir nesne geçen her cümle bir tasarım başlatır.

1. Yapay zekâ nesneyi basılabilir parçalara ayırır (delikler, standart ölçüler, en az 1,2 mm duvar, dik sarkma yok) ve her parçayı kutu, silindir/koni/altıgen ve kürelerle modeller.
2. Model, çizdiğin çizgilerin altında yarı saydam bir 3D rehber olarak görünür. Sıradaki parça vurgulanır, sağdaki panelde talimatı ve milimetre cinsinden ölçüsü yazar.
3. Parçalar arasında *"sonraki"* / *"önceki"* / *"tekrar"* diyerek gezin. *"Kontrol et"* deyince yapay zekâ çizimine bakar; istediğini de sorabilirsin (*"duvar kalınlığı kaç olmalı?"*).
4. *"STL"* de (veya `X`'e bas). Her parça tablaya düz oturan ayrı bir STL olarak; montaj önizlemesi, `talimatlar.txt` ve `tasarim.json` ile birlikte `<kayıt klasörü>/3D/` altına yazılır.

**Hangi yapay zekâ kullanılıyor?** `ai` eki kuruluysa ve bir Anthropic kimlik bilgisi (`ANTHROPIC_API_KEY` veya `ant auth login` profili) varsa, tarif edebildiğin her şeyi Claude (`claude-opus-5-5`, yapılandırılmış JSON çıktı) tasarlar. Claude yoksa yerel dil modeli (LM Studio / Ollama) denenir. Yerleşik şablonlar internet olmadan da çalışır: cıvata, somun, dron, dron kolu, araba, tekerlek, fan ızgarası, SSD braketi.

## Ayarlar

| Ortam değişkeni | Varsayılan |
| --- | --- |
| `AIR_CANVAS_SAVE_DIR` | `~/Pictures/AirCanvas` |
| `AIR_CANVAS_MODEL` | kullanıcı önbellek klasörü (`%LOCALAPPDATA%\air_canvas` / `~/.cache/air_canvas`) |
| `AIR_CANVAS_VOICE` | `tr-TR-AhmetNeural` (veya `tr-TR-EmelNeural`) |
| `AIR_CANVAS_LLM_URL` | sırasıyla LM Studio `:1234` ve Ollama `:11434` denenir |
| `AIR_CANVAS_LLM_MODEL` | sunucunun bildirdiği ilk model |
| `ANTHROPIC_API_KEY` | 3D tasarım asistanında Claude'u etkinleştirir |
| `AIR_CANVAS_CLAUDE_MODEL` | `claude-opus-5-5` |

## Kütüphane olarak kullanım

```python
from air_canvas import AirCanvas

canvas = AirCanvas(save_dir="cizimler")
canvas.start_async(on_finish=print)   # engellemez; engelleyerek çalıştırmak için canvas.run()
```

Kendi ses motorunu (`speaker=` — `say(text)` ve `is_busy()` metotları olan herhangi bir nesne) ve dil modeli istemcini (`llm=`) bağlayabilirsin.

## Şekil tanıma nasıl çalışıyor?

Bir çizgi bittiğinde noktaları `approxPolyDP` ile sadeleştirilir ve dışbükey zarfı (convex hull) üzerinden incelenir. Başlangıç–bitiş mesafesinin çizgi uzunluğuna oranı, doğruyu kapalı şekillerden ayırır. Köşe sayısı, dairesellik (`4πA/P²`) ve doluluk oranı (alan ÷ sınırlayıcı kutu alanı) üçgen, dikdörtgen ve yuvarlak şekilleri birbirinden ayırır. `minAreaRect` ve `fitEllipse` yönü ve boyutu verir. Yeterince kapalı olmayan çizgiler çizildiği gibi bırakılır.

## Testler

```bash
pip install -e ".[dev]"
pytest
```

Testler şekil sınıflandırma, geri al/yinele, katman birleştirme, menü butonları, hareket zamanlaması, sesli komut çözümleme ve kaydetmeyi kapsar. Hepsi sentetik veriyle çalışır, kamera gerekmez.

## Proje yapısı

```
air_canvas/
  canvas.py      Çizim motoru: el takibi, hareketler, çizgiler, şekiller, menü, kaydetme
  speech.py      Türkçe sesli geri bildirim (Edge TTS → gTTS → Türkçe Windows sesi)
  llm.py         Sesli not özetleri için isteğe bağlı yerel dil modeli istemcisi (LM Studio / Ollama)
  design.py      3D tasarımlar: parçalar, katı modelleme (manifold3d), STL, 3D önizleme, şablonlar
  ai.py          Tasarım asistanı: önce Claude, sonra yerel dil modeli, sonra yerleşik şablonlar
  config.py      Varsayılan yollar (ortam değişkenleriyle değiştirilebilir)
  __main__.py    Komut satırı uygulaması (air-canvas)
tests/           pytest testleri, yalnızca sentetik veri
```

## Sorun giderme

| Sorun | Çözüm |
| --- | --- |
| "Kamera açılamadı" | Kamerayı kullanan diğer uygulamaları kapat (Teams, Zoom, tarayıcı sekmeleri) veya `--camera 1` dene. |
| `pip`, `mediapipe` paketini bulamıyor | Python sürümün MediaPipe için fazla yeni olabilir. Python 3.10–3.12 kullan. |
| Ses yok veya İngilizce aksanlı | Ses eklerini kur (`pip install -e ".[voice]"`). Edge TTS ve gTTS internet ister; internetsiz kullanım için **Windows Ayarlar → Saat ve dil → Konuşma** bölümünden Türkçe sesi ekle. |
| Sesli komutlar algılanmıyor | Konuşma tanıma Google'ın ücretsiz web servisini kullanır; internet ve çalışan bir mikrofon (`PyAudio`) gerekir. |
| El modeli indirilemiyor | [`hand_landmarker.task`](https://storage.googleapis.com/mediapipe-models/hand_landmarker/hand_landmarker/float16/1/hand_landmarker.task) dosyasını elle indir ve `AIR_CANVAS_MODEL` değişkeniyle yolunu göster. |
| Çizgiler titriyor | Ortamı eşit aydınlat, elini kameradan 40–80 cm uzakta tut. |

## Yol haritası

- [ ] Demo GIF'i
- [ ] İngilizce sesli komutlar ve arayüz
- [ ] İki elle hareketler (tuvali yakınlaştırma / kaydırma)
- [ ] İnternetsiz konuşma tanıma (faster-whisper)
- [ ] SVG olarak dışa aktarma

## Katkıda bulunma

Issue ve pull request'ler memnuniyetle karşılanır — bkz. [CONTRIBUTING.md](CONTRIBUTING.md). Değişiklikler [CHANGELOG.md](CHANGELOG.md) dosyasında listelenir.

## Lisans

[MIT](LICENSE) © Gencay Çeliker
