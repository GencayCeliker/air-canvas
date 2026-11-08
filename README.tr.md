[English](README.md) | **Türkçe**

# Air Canvas

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
- Titremeyi azaltan yumuşatma ve kısa bir tolerans süresi sayesinde, el takibi bir an kaybolsa da çizgi kopmaz.

## Kurulum

Python 3.9+ ve bir web kamerası gerekir.

```bash
git clone https://github.com/GencayCeliker/air-canvas.git
cd air-canvas
pip install -e ".[voice]"      # veya: pip install -e .   (ses özellikleri olmadan)
```

MediaPipe el modeli (~7,5 MB) ilk açılışta otomatik indirilir.

## Çalıştırma

```bash
air-canvas                     # veya: python -m air_canvas
air-canvas --board             # düz tahta ile başla
air-canvas --camera 1 --no-voice
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
| `Q` / `Esc` | Çıkış | | |

## Ayarlar

| Ortam değişkeni | Varsayılan |
| --- | --- |
| `AIR_CANVAS_SAVE_DIR` | `~/Pictures/AirCanvas` |
| `AIR_CANVAS_MODEL` | kullanıcı önbellek klasörü (`%LOCALAPPDATA%\air_canvas` / `~/.cache/air_canvas`) |
| `AIR_CANVAS_VOICE` | `tr-TR-AhmetNeural` (veya `tr-TR-EmelNeural`) |
| `AIR_CANVAS_LLM_URL` | sırasıyla LM Studio `:1234` ve Ollama `:11434` denenir |
| `AIR_CANVAS_LLM_MODEL` | sunucunun bildirdiği ilk model |

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

## Lisans

[MIT](LICENSE) © Gencay Çeliker
