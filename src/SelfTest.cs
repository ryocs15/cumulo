using System.IO;
using System.Text.Json.Nodes;
using HavaDurumu.Core;
using HavaDurumu.Data;
using HavaDurumu.Ui;
using HavaDurumu.Update;

namespace HavaDurumu;

/// --selftest: yeni mantığın küçük doğrulamaları (ağ ve kullanıcı dosyası kullanmaz; geçici klasörde çalışır).
internal static class SelfTest
{
    private static int _fail;

    private static void Check(bool ok, string what)
    {
        Console.WriteLine((ok ? "  tamam  " : "  HATA   ") + what);
        if (!ok) _fail++;
    }

    public static int Run()
    {
        Console.WriteLine("Öz-test:");
        Check(WidgetWindow.ParseHhmm("07:30") == 450 && WidgetWindow.ParseHhmm("7.05") == 425, "saat ayrıştırma");
        Check(WidgetWindow.ParseHhmm("24:00") is null && WidgetWindow.ParseHhmm("abc") is null, "geçersiz saat reddedilir");
        Check(UpdateService.IsNewer("1.3.2", "1.3.1") && !UpdateService.IsNewer("1.3.1", "1.3.1")
              && UpdateService.IsNewer("1.3.1.0", "1.3.1") && !UpdateService.IsNewer("1.2.9", "1.3"), "sürüm karşılaştırma (Python tuple)");
        var t1405 = new DateTime(2026, 11, 21, 14, 5, 0);
        bool h24 = Fmt.Hm(t1405) == "14:05" && Fmt.Hour(t1405.AddMinutes(-5)) == "14:00";
        Units.Clock12 = true;
        bool h12 = Fmt.Hm(t1405) == "2:05 PM" && Fmt.Hour(t1405.AddMinutes(-5)) == "2 PM" && Fmt.Hm(t1405.AddHours(-14)) == "12:05 AM";
        Units.Clock12 = false;
        Check(h24 && h12, "saat biçimi: 24 saat ve 12 saat (AM/PM)");
        var red = Theme.StyleFor("dark", null, "#ff0000");
        Check(red.Text == System.Windows.Media.Color.FromRgb(255, 0, 0) && red.Muted.A == Theme.Dark.Muted.A && red.Muted.R == 255
              && Theme.StyleFor("dark", null, "kırmızı") == Theme.Dark && Theme.StyleFor("light", null, null) == Theme.Light,
            "yazı rengi: seçilen renk, soluk tonlar temanın saydamlığıyla; geçersiz renk yok sayılır");

        static bool Trusted(string u) { try { UpdateService.EnsureTrustedUrl(u); return true; } catch (InvalidDataException) { return false; } }
        Check(Trusted("https://github.com/ryocs15/cumulo/releases/download/v1.7.0/HavaDurumu-Kurulum.exe")
              && Trusted("https://github.com/ryocs15/hava-durumu-widget/releases/download/v1.7.0/HavaDurumu-Kurulum.exe")
              && !Trusted("https://github.com/baskasi/cumulo/releases/download/v1.7.0/HavaDurumu-Kurulum.exe")
              && !Trusted("https://github.com/ryocs15/cumulo-kotu/releases/download/x.exe")
              && !Trusted("http://github.com/ryocs15/cumulo/releases/download/x.exe"), "güncelleme: yalnızca iki depo adının indirme adresi");

        var mx = Data.WeatherService.MgmExtras(new JsonObject
        {
            ["hourly"] = new JsonArray(
                new JsonObject { ["tarih"] = "2026-11-19T06:00:00.000Z", ["sicaklik"] = 9.0, ["hadise"] = "A" },     // 3 sa önce: atlanır
                new JsonObject { ["tarih"] = "2026-11-19T09:00:00.000Z", ["sicaklik"] = 12.0, ["hadise"] = "Y" },
                new JsonObject { ["tarih"] = "2026-11-19T12:00:00.000Z", ["sicaklik"] = -9999.0, ["hadise"] = "Y" }),   // eksik: atlanır
            ["daily"] = new JsonObject
            {
                ["tarihGun1"] = "2026-11-20T00:00:00.000Z", ["enDusukGun1"] = 5.0, ["enYuksekGun1"] = 14.0, ["hadiseGun1"] = "PB",
                ["tarihGun2"] = "2026-11-21T00:00:00.000Z", ["enDusukGun2"] = -9999.0, ["enYuksekGun2"] = 10.0,
            },
        }, new DateTime(2026, 11, 19, 9, 0, 0, DateTimeKind.Utc));
        Check(J.Arr(mx["temps12"]).Count == 1 && J.Str(J.Arr(J.Arr(mx["temps12"])[0])[2]) == "Y"
              && J.Arr(mx["daily16"]).Count == 1 && J.Str(J.Obj(J.Arr(mx["daily16"])[0])["d"]) == "2026-11-20",
            "MGM saatlik ve 5 günlük tahmin temps12 / daily16 biçiminde");

        // MGM uyarıları: süresi dolan atlanır; ilçe hem sarı hem turuncu listedeyse en ciddisi (turuncu) alınır
        var ilce = new Location { Il = "Ankara", Ilce = "Keçiören", MerkezId = JsonValue.Create(90601) };
        JsonObject Alert(string no, string end, JsonObject towns) => new()
        {
            ["alertNo"] = no, ["begin"] = "2026-11-21T06:00:00.000Z", ["end"] = end, ["towns"] = towns,
            ["weather"] = new JsonObject { ["orange"] = new JsonArray("wind"), ["yellow"] = new JsonArray("rain") },
            ["text"] = new JsonObject { ["orange"] = "Kuvvetli rüzgar" },
        };
        var alerts = AlertService.AlertsFor(ilce, new JsonArray(
            Alert("1", "2026-11-21T08:00:00.000Z", new JsonObject { ["red"] = new JsonArray(90601) }),                  // süresi doldu
            Alert("2", "2026-11-21T20:00:00.000Z", new JsonObject { ["yellow"] = new JsonArray(90601), ["orange"] = new JsonArray("90601") }),
            Alert("3", "2026-11-21T20:00:00.000Z", new JsonObject { ["red"] = new JsonArray(12345) })),                  // başka ilçe
            new DateTime(2026, 11, 21, 12, 0, 0, DateTimeKind.Utc));
        Check(alerts.Count == 1 && alerts[0].Color == "orange" && alerts[0].No == "2" && alerts[0].Codes.SequenceEqual(new[] { "wind" }),
            "MGM uyarısı: ilçe eşleşir (sayı ya da metin kimlik), süresi dolan atlanır, en ciddi renk seçilir");
        Check(AlertService.AlertsFor(new Location { Src = "om", MerkezId = JsonValue.Create("om_1_2") }, new JsonArray(
            Alert("4", "2026-11-21T20:00:00.000Z", new JsonObject { ["red"] = new JsonArray("om_1_2") }))).Count == 0,
            "MGM uyarısı: yurt dışı konumda uyarı yok");

        var dir = Path.Combine(Path.GetTempPath(), "hd_selftest_" + Environment.ProcessId);
        Directory.CreateDirectory(dir);
        try
        {
            // olay günlüğü: ilk ölçümde olay yok; yağış başlayınca ve 5°+ değişimde olay
            var log = new EventLog(Path.Combine(dir, "events.json"));
            var loc = new Location { Il = "Test", Ilce = "Merkez", MerkezId = JsonValue.Create(1) };
            WeatherData Data(string code, double t) => new(new JsonObject
            {
                ["cur"] = new JsonObject { ["hadiseKodu"] = code, ["sicaklik"] = t, ["ruzgarHiz"] = 5 },
                ["extra"] = new JsonObject(),
            });
            Check(log.Observe(loc, Data("A", 10)).Count == 0, "olay günlüğü: ilk ölçüm olay üretmez");
            var ev = log.Observe(loc, Data("Y", 16));
            Check(ev.Any(e => J.Str(e["kind"]) == "rain_start") && ev.Any(e => J.Str(e["kind"]) == "temp"), "olay günlüğü: yağış başladı + 6° yükseldi");
            Check(EventLog.Text(new JsonObject { ["kind"] = "alert", ["color"] = "orange", ["what"] = "wind, rain" }) == "Turuncu uyarı: Kuvvetli rüzgar, Kuvvetli yağış"
                  && EventLog.Text(new JsonObject { ["kind"] = "alert", ["color"] = "yellow", ["what"] = "Sis" }) == "Sarı uyarı: Sis",
                "olay günlüğü: uyarı kodları gösterimde çevrilir, eski Türkçe kayıt korunur");
            log.Save();
            Check(new EventLog(Path.Combine(dir, "events.json")).Events.Count == 2, "olay günlüğü: dosyaya yazıp geri okuma");

            // bozuk ayar dosyası: okunamazsa üzerine yazılmaz (kullanıcının şehirleri silinmesin)
            var cfgPath = Path.Combine(dir, "config.json");
            const string broken = "{\"locs\": [ {\"il\": \"Ankara\"";
            File.WriteAllText(cfgPath, broken);
            var cfg = AppConfig.Load(cfgPath);
            cfg.Set("layout", "mini");
            bool refused = false;
            try { cfg.Save(); } catch (InvalidOperationException) { refused = true; }
            Check(cfg.LoadFailed && refused && File.ReadAllText(cfgPath) == broken, "ayarlar: bozuk dosyanın üzerine yazılmaz");
            var ok = AppConfig.Load(Path.Combine(dir, "yeni.json"));
            ok.Set("layout", "mini");
            ok.Save();
            Check(AppConfig.Load(Path.Combine(dir, "yeni.json")).Get("layout", "") == "mini", "ayarlar: kaydedip geri okuma");

            // Python klasör kipi güncellemesinden kalan dosyalar açılışta silinir
            var target = Path.Combine(dir, "kurulum");
            Directory.CreateDirectory(Path.Combine(target, "_internal.old"));
            File.WriteAllText(Path.Combine(target, "HavaDurumu.exe.old"), "eski");
            Check(!UpdateService.CleanupOld(target, Path.Combine(target, "HavaDurumu.exe"))
                  && !File.Exists(Path.Combine(target, "HavaDurumu.exe.old")) && !Directory.Exists(Path.Combine(target, "_internal.old")),
                "güncelleme: eski dosyalar temizlenir");
        }
        finally
        {
            try { Directory.Delete(dir, true); } catch (IOException) { }
        }
        Console.WriteLine(_fail == 0 ? "Sonuç: hepsi geçti." : $"Sonuç: {_fail} hata.");
        return _fail == 0 ? 0 : 1;
    }
}
