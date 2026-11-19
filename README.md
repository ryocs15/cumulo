<p align="center">
  <img src="docs/icon.png" width="96" alt="Cumulo icon">
</p>

<h1 align="center">Cumulo</h1>

<p align="center">
  A weather widget for the Windows 10/11 desktop · Windows masaüstü için hava durumu widget'ı<br>
  <a href="../../releases/latest"><b>Download / İndir</b></a> · <a href="#türkçe">Türkçe</a>
</p>

![Cumulo widget layouts](docs/screenshot.png)

## Download

Get **`Cumulo-Setup.exe`** from the [latest release](../../releases/latest) and run it.

- Installs for your user only, no administrator rights needed. Nothing else to install: the .NET runtime is included.
- Adds Cumulo to the Start menu; uninstall it from **Settings > Apps**.
- Updates itself: new versions are downloaded and installed from inside the app.
- Requires 64-bit Windows 10 (1809 or later) or Windows 11.

The installer is not code-signed, so Windows SmartScreen may warn on first run
(**More info > Run anyway**).

## Features

- Current conditions, hourly forecast, 5-day forecast, and a weekly view with a 16-day trend
- Any city in the world (Open-Meteo); Turkish provinces and districts use official MGM data
- Several cities with automatic cycling, and extra widgets for other cities or screens
- Layouts from a tiny icon + temperature card up to the full forecast, plus side-by-side and stacked city rows
- Animated weather icons and backgrounds (rain, snow, clouds, fog, stars, sun glow…); animations pause while the
  widget is covered, the screen is locked or turned off
- Themes: match the weather, illustrated scene, dark, light, glass, transparent, follow Windows, custom color or your own image
- Notifications: rain on the way, temperature thresholds, very high UV, morning summary, umbrella/coat reminders, quiet hours
- Air quality, UV index, pollen, sunrise/sunset and moon phase
- MGM weather warnings and radar for Turkey
- A hotkey to bring the widget forward, start with Windows
- °C / °F, km/h / m/s, English and Turkish interface (follows your Windows language)
- Settings export and import

![Side-by-side city row](docs/screenshot-multi.png)

## Usage

Right-click the widget to open the menu. Drag with the left mouse button to move it.
Change the language under **Widgets and settings > Dil / Language**.

## Data sources

- [Open-Meteo](https://open-meteo.com) for forecasts worldwide, air quality and pollen
- [Turkish State Meteorological Service (MGM)](https://www.mgm.gov.tr) for locations in Turkey

Cumulo is not an official product of Open-Meteo or MGM.

## Building from source

Requires Windows and the [.NET 10 SDK](https://dotnet.microsoft.com/download/dotnet/10.0). No NuGet packages.

```
dotnet build -c Release                                   # build
bin\Release\net10.0-windows10.0.19041.0\HavaDurumu.exe    # run
bin\Release\net10.0-windows10.0.19041.0\HavaDurumu.exe --selftest
python tools\test_lang.py                                 # every UI text has an English translation
build.bat                                                 # self-contained build in build\publish
```

UI texts are written in Turkish and wrapped in `T(...)`; English translations live in `src/Core/lang_en.json`
(shared with the earlier Python version) and `src/Core/lang_en_wpf.json`.
The installer is built from `installer.iss` with [Inno Setup 6](https://jrsoftware.org/isinfo.php);
`release.py` builds and publishes a release.

Versions up to 1.6.0 were written in Python; that code is kept in the
[`python-1.6`](../../tree/python-1.6) branch.

## License

[MIT](LICENSE)

---

## Türkçe

![Cumulo Türkçe arayüz](docs/screenshot-tr.png)

### İndirme

[Son sürüm](../../releases/latest) sayfasından **`Cumulo-Setup.exe`** dosyasını indirip çalıştırın.

- Yalnızca sizin kullanıcınıza kurulur, yönetici izni gerekmez. Başka bir şey kurmanız gerekmez: .NET içindedir.
- Başlat menüsüne eklenir; **Ayarlar > Uygulamalar** bölümünden kaldırılabilir.
- Kendini günceller: yeni sürümler uygulama içinden indirilip kurulur.
- 64 bit Windows 10 (1809 ve sonrası) ya da Windows 11 gerekir.

Kurulum dosyası imzalı olmadığı için Windows SmartScreen ilk açılışta uyarı gösterebilir
(**Ek bilgi > Yine de çalıştır**).

### Özellikler

- Anlık durum, saatlik ve 5 günlük tahmin, 16 günlük eğilimli haftalık görünüm
- Türkiye'deki il ve ilçeler için MGM verileri, uyarıları ve radarı; dünyadaki her şehir için Open-Meteo
- Birden çok şehir ve şehirler arasında otomatik geçiş; başka şehir ya da ekran için ek widget'lar
- Mikro karttan tam tahmine kadar düzenler, şehirleri yan yana ya da alt alta gösterme
- Hareketli hava ikonları ve arka planlar (yağmur, kar, bulut, sis, yıldız, güneş parıltısı…); widget'ın üstü
  kapalıyken, ekran kilitliyken ya da kapalıyken animasyonlar durur
- Temalar: havaya göre, çizimli sahne, koyu, açık, cam, saydam, Windows temasını izle, özel renk ya da kendi görseliniz
- Bildirimler: yaklaşan yağış, sıcaklık eşikleri, çok yüksek UV, sabah özeti, şemsiye / mont hatırlatması, sessiz saatler
- Hava kalitesi, UV indeksi, polen, gün doğumu / batımı ve ay evresi
- Öne getirme kısayol tuşu, Windows ile başlatma
- °C / °F, km/sa / m/sn, Türkçe ve İngilizce arayüz (Windows diline göre seçilir)
- Ayarları dışa ve içe aktarma

### Kullanım

Widget'a sağ tıklayınca menü açılır. Sol tuşla sürükleyerek taşınır.
Dili **Widget'lar ve ayarlar > Dil / Language** bölümünden değiştirebilirsiniz.

### Veri kaynakları

- [Meteoroloji Genel Müdürlüğü (MGM)](https://www.mgm.gov.tr): Türkiye'deki konumlar
- [Open-Meteo](https://open-meteo.com): dünya geneli tahmin, hava kalitesi ve polen

Cumulo, MGM'nin ya da Open-Meteo'nun resmi bir ürünü değildir.

### Kaynaktan derleme

Windows ve .NET 10 SDK gerekir; ayrıntılar yukarıdaki [Building from source](#building-from-source) bölümünde.
1.6.0'a kadarki Python sürümü [`python-1.6`](../../tree/python-1.6) dalındadır.

### Lisans

[MIT](LICENSE)
