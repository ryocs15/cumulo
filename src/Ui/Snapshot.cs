using System.IO;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Media;
using System.Windows.Media.Imaging;
using HavaDurumu.Core;
using HavaDurumu.Data;

namespace HavaDurumu.Ui;

/// Test için: kartları pencere açmadan PNG olarak çizer (--snapshot <klasör>).
/// Ayar ve önbellek exe'nin klasöründen okunur; hiçbir dosyaya yazılmaz (yalnızca PNG'ler).
public static class Snapshot
{
    public static int Run(AppPaths paths, string outDir)
    {
        var app = new Application { ShutdownMode = ShutdownMode.OnExplicitShutdown };
        app.Resources.MergedDictionaries.Add(new ResourceDictionary
        {
            Source = new Uri("pack://application:,,,/HavaDurumu;component/src/Ui/Cards.xaml"),
        });

        var cfg = AppConfig.Load(paths.Config);
        var cache = new WeatherCache(paths.Cache);
        cache.Load();
        var locs = cfg.GetLocations();
        Directory.CreateDirectory(outDir);
        var settings = CardSettings.From(cfg);
        CardSettings.ApplyGlobals(cfg);
        var now = DateTime.UtcNow;
        int n = 0;

        CardModel Model(string layout, int i, CardSettings s, int page = 0, int? multi = null)
        {
            cache.ById.TryGetValue(locs[i].Key, out var data);
            return CardBuilder.Build(layout, locs[i], data, data is null ? T("Yükleniyor…") : null,
                cache.Alerts, s, i, locs.Count, now, page, multi);
        }

        for (int i = 0; i < locs.Count; i++)
        {
            foreach (var layout in new[] { "micro", "mini", "miniplus", "vmini", "compact", "peek", "medium", "full", "weekly" })
            {
                var view = CardViews.Create(layout);
                view.DataContext = Model(layout, i, settings);
                Save(view, Layouts.Size(layout), Path.Combine(outDir, $"{i}_{layout}.png"));
                n++;
            }
        }

        // mini sayfaları (sayfa kipi açık), saatlik grafik kipi
        var paged = settings with { PagesOn = true };
        for (int page = 0; page < 3; page++)
        {
            var v = new MiniCard { DataContext = Model("mini", 0, paged, page) };
            Save(v, Layouts.Size("mini"), Path.Combine(outDir, $"page{page}_mini.png"));
            n++;
        }
        int withHours = Math.Max(0, locs.FindIndex(l => cache.ById.TryGetValue(l.Key, out var d) && d.Hourly.Count > 0));
        var chart = new DetailCard { DataContext = Model("medium", withHours, settings with { HourlyMode = "chart" }) };
        Save(chart, Layouts.Size("medium"), Path.Combine(outDir, "chart_medium.png"));
        n++;

        // efekt galerisi: aynı veri, farklı hava kodları (gündüz / gece zamanı zorlanarak)
        int gi = Math.Max(0, withHours);
        if (cache.ById.TryGetValue(locs[gi].Key, out var baseData))
        {
            var fxSettings = settings with { Effects = true, Animate = true };
            var noonUtc = now.Date.AddHours(9.5);    // Türkiye öğle
            var nightUtc = now.Date.AddHours(20);    // Türkiye gece
            foreach (var (code, utc, name) in new[]
                     {
                         ("Y", noonUtc, "rain"), ("GSY", noonUtc, "storm"), ("K", noonUtc, "snow"),
                         ("SIS", noonUtc, "fog"), ("R", noonUtc, "wind"), ("A", noonUtc, "sun"),
                         ("A", nightUtc, "stars"), ("PB", noonUtc, "clouds"),
                         ("SY", noonUtc, "showers"), ("KKY", noonUtc, "sleet"), ("YKY", noonUtc, "heavysnow"),
                     })
            {
                var root = (System.Text.Json.Nodes.JsonObject)baseData.Root.DeepClone();
                if (root["cur"] is System.Text.Json.Nodes.JsonObject cur) cur["hadiseKodu"] = code;
                var data = new WeatherData(root);
                foreach (var layout in new[] { "mini", "compact" })
                {
                    var m = CardBuilder.Build(layout, locs[gi], data, null, cache.Alerts, fxSettings, gi, locs.Count, utc);
                    var v = CardViews.Create(layout);
                    v.DataContext = m;
                    Save(v, Layouts.Size(layout), Path.Combine(outDir, $"fx_{name}_{layout}.png"));
                    n++;
                }
            }
        }

        // tema galerisi
        if (cache.ById.TryGetValue(locs[gi].Key, out var td))
        {
            var noon = now.Date.AddHours(9.5);
            var img = Path.Combine(paths.AppDir, "..", "..", "..", "..", "..", "hava_durumu_widget", "icon.png");
            foreach (var (theme, utc, name) in new[] { ("scene", noon, "sahne_gunduz"), ("scene", now.Date.AddHours(20), "sahne_gece"),
                         ("clear", noon, "saydam"), ("glass", noon, "cam"), ("image", noon, "gorsel"), ("custom", noon, "ozel") })
            {
                var s = settings with { Theme = theme, CustomColor = "#7a3fa0", BgImage = Environment.GetEnvironmentVariable("HAVADURUMU_TEST_IMAGE") ?? img };
                var m = CardBuilder.Build("compact", locs[gi], td, null, cache.Alerts, s, gi, locs.Count, utc);
                var v = CardViews.Create("compact");
                v.DataContext = m;
                Save(v, Layouts.Size("compact"), Path.Combine(outDir, $"tema_{name}.png"));
                n++;
            }
        }

        // yan yana / alt alta
        foreach (var lay in new[] { "row", "col" })
        {
            var panel = new StackPanel { Orientation = lay == "row" ? Orientation.Horizontal : Orientation.Vertical };
            int count = Math.Min(locs.Count, Layouts.MultiMax);
            for (int i = 0; i < count; i++)
            {
                var card = new MiniCard { DataContext = Model("mini", i, settings, 0, i) };
                if (i > 0)
                    card.Margin = lay == "row" ? new Thickness(Layouts.MultiGap, 0, 0, 0) : new Thickness(0, Layouts.MultiGap, 0, 0);
                panel.Children.Add(card);
            }
            Save(panel, Layouts.WindowSize(lay, locs.Count), Path.Combine(outDir, $"multi_{lay}.png"));
            n++;
        }

        Console.WriteLine($"{n} kart çizildi: {outDir}");
        return 0;
    }

    private static void Save(FrameworkElement view, (double W, double H) size, string file)
    {
        var (w, h) = size;
        // efekt ve ikon animasyonunun bir anı (t = 1,3 sn) — animasyonlu ikon da o kareyle
        if (view is IAnimatedCard ac && view.DataContext is CardModel m)
        {
            if (m.FxKind is { } kind)
            {
                ac.FxLayer.SetFx(new AmbientFx(kind, m.Width, m.Height, m.IconLight, m.FxLevel, m.SunCenter, m.SunR, m.IconNight),
                    m.Width, m.Height, m.Radius.TopLeft);
                ac.FxLayer.Draw(1.3);
            }
            if (m.IconAnimated) ac.IconImage.Source = WeatherIcons.Get(m.IconCode, m.IconNight, m.IconLight, WeatherIcons.AnimFrames / 2);
        }
        view.Measure(new Size(w, h));
        view.Arrange(new Rect(0, 0, w, h));
        view.UpdateLayout();
        var bmp = new RenderTargetBitmap((int)Math.Ceiling(w * 2), (int)Math.Ceiling(h * 2), 192, 192, PixelFormats.Pbgra32);
        bmp.Render(view);
        var enc = new PngBitmapEncoder();
        enc.Frames.Add(BitmapFrame.Create(bmp));
        using var fs = File.Create(file);
        enc.Save(fs);
    }
}
