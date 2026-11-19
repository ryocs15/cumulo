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

            // Python 1.3.x klasör kipi güncellemesi: yeni exe, _update'ten hedefe dosyaları taşır
            var target = Path.Combine(dir, "kurulum");
            var stage = Path.Combine(dir, "_update");
            Directory.CreateDirectory(Path.Combine(target, "_internal"));
            Directory.CreateDirectory(Path.Combine(stage, "_internal", "alt"));
            File.WriteAllText(Path.Combine(target, "HavaDurumu.exe"), "eski");
            File.WriteAllText(Path.Combine(target, "_internal", "eski.dll"), "eski");
            File.WriteAllText(Path.Combine(target, "config.json"), "{\"korunur\": true}");
            File.WriteAllText(Path.Combine(stage, "HavaDurumu.exe"), "yeni");
            File.WriteAllText(Path.Combine(stage, "_internal", "alt", "yeni.dll"), "yeni");
            UpdateService.ApplyUpdate(target, stage, launch: false);
            Check(File.ReadAllText(Path.Combine(target, "HavaDurumu.exe")) == "yeni"
                  && File.Exists(Path.Combine(target, "_internal", "alt", "yeni.dll"))
                  && !File.Exists(Path.Combine(target, "_internal", "eski.dll")), "güncelleme: yeni exe ve _internal yerinde");
            Check(File.ReadAllText(Path.Combine(target, "config.json")).Contains("korunur"), "güncelleme: config.json korunur");
            Check(!UpdateService.CleanupOld(target, Path.Combine(target, "HavaDurumu.exe"))
                  && !File.Exists(Path.Combine(target, "HavaDurumu.exe.old")) && !Directory.Exists(Path.Combine(target, "_internal.old")),
                "güncelleme: eski dosyalar temizlenir");

            // hedef kilitliyken geri alma: kaynakta _internal yok ama exe yok -> kopyalama başarısız, eski geri gelir
            var t2 = Path.Combine(dir, "kurulum2");
            Directory.CreateDirectory(Path.Combine(t2, "_internal"));
            File.WriteAllText(Path.Combine(t2, "HavaDurumu.exe"), "eski");
            UpdateService.ApplyUpdate(t2, Path.Combine(dir, "yok"), launch: false);
            Check(File.ReadAllText(Path.Combine(t2, "HavaDurumu.exe")) == "eski" && Directory.Exists(Path.Combine(t2, "_internal")),
                "güncelleme başarısızsa eski sürüm geri konur");
        }
        finally
        {
            try { Directory.Delete(dir, true); } catch (IOException) { }
        }
        Console.WriteLine(_fail == 0 ? "Sonuç: hepsi geçti." : $"Sonuç: {_fail} hata.");
        return _fail == 0 ? 0 : 1;
    }
}
