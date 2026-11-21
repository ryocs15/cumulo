using System.IO;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Media;
using System.Windows.Media.Effects;
using System.Windows.Media.Imaging;
using HavaDurumu.Core;

namespace HavaDurumu.Ui;

/// "Çizimli sahne" ve "kendi görselim" temalarının arka planı (Python scene_bg / image_bg).
/// Bir kez bitmap'e çizilir ve önbellekte durur; karede yalnızca hazır doku birleştirilir.
public static class SceneBg
{
    private const double Px = 2;   // çözünürlük çarpanı (DPI ve ölçek 2x'e kadar keskin)
    private static readonly Dictionary<string, ImageBrush> Cache = new();

    public static Brush Scene(double w, double h, string code, bool night, double dayf)
    {
        var grp = Theme.SceneGroup(code);
        double q = Math.Round(dayf * 8) / 8;   // 8 kademe: önbellek küçük kalır
        var key = $"s|{grp}|{q}|{w}x{h}";
        return Cached(key, w, h, dc =>
        {
            if (q is > 0 and < 1)
            {
                // alacakaranlık: gece ve gündüz sahneleri karışır, ufka turuncu, üste mor ışık
                Draw(dc, w, h, code, true);
                dc.PushOpacity(q);
                Draw(dc, w, h, code, false);
                dc.Pop();
                double tw = 1 - Math.Abs(2 * q - 1), k = tw * (grp is "clear" or "partly" ? 1.0 : 0.45);
                var warm = new LinearGradientBrush(Color.FromArgb(0, 255, 150, 80), Color.FromArgb((byte)(190 * k), 255, 150, 80), 90);
                var dusk = new LinearGradientBrush(Color.FromArgb((byte)(70 * k), 190, 120, 190), Color.FromArgb(0, 190, 120, 190), 90);
                dc.DrawRectangle(warm, null, new Rect(0, 0, w, h));
                dc.DrawRectangle(dusk, null, new Rect(0, 0, w, h));
            }
            else Draw(dc, w, h, code, q <= 0);   // Python: night = q <= 0
        });
    }

    /// Kullanıcının görseli: karta göre kırpılır (UniformToFill), hafif bulanık ve %38 koyulaştırılmış.
    public static Brush? Image(string path, double w, double h)
    {
        // silinmiş / taşınmış görsel: her çizimde yeniden açıp günlüğe yazmasın; kart düz renge düşer
        if (!File.Exists(path)) return null;
        DateTime mtime;
        try { mtime = File.GetLastWriteTimeUtc(path); }
        catch (Exception ex) when (ex is IOException or UnauthorizedAccessException or ArgumentException) { return null; }
        var key = $"i|{path}|{mtime.Ticks}|{w}x{h}";
        if (Cache.TryGetValue(key, out var b)) return b;
        BitmapImage src;
        try
        {
            src = new BitmapImage();
            src.BeginInit();
            src.UriSource = new Uri(path);
            src.CacheOption = BitmapCacheOption.OnLoad;   // dosya kilitli kalmasın
            src.EndInit();
            src.Freeze();
        }
        catch (Exception ex) when (ex is IOException or NotSupportedException or UriFormatException or ArgumentException)
        {
            Log.Write("arka plan görseli açılamadı: " + ex.Message);
            return null;
        }
        var grid = new Grid { Width = w * Px, Height = h * Px, ClipToBounds = true };
        grid.Children.Add(new System.Windows.Controls.Image
        {
            Source = src, Stretch = Stretch.UniformToFill, Effect = new BlurEffect { Radius = 1.5 * Px },
        });
        grid.Children.Add(new Border { Background = new SolidColorBrush(Color.FromArgb(97, 0, 0, 0)) });   // %38 koyu
        grid.Measure(new Size(w * Px, h * Px));
        grid.Arrange(new Rect(0, 0, w * Px, h * Px));
        var rtb = new RenderTargetBitmap((int)(w * Px), (int)(h * Px), 96, 96, PixelFormats.Pbgra32);
        rtb.Render(grid);
        return Store(key, rtb);
    }

    private static ImageBrush Cached(string key, double w, double h, Action<DrawingContext> draw)
    {
        if (Cache.TryGetValue(key, out var b)) return b;
        var dv = new DrawingVisual();
        using (var dc = dv.RenderOpen())
        {
            dc.PushTransform(new ScaleTransform(Px, Px));
            draw(dc);
            dc.Pop();
        }
        var rtb = new RenderTargetBitmap((int)(w * Px), (int)(h * Px), 96, 96, PixelFormats.Pbgra32);
        rtb.Render(dv);
        return Store(key, rtb);
    }

    private static ImageBrush Store(string key, BitmapSource bmp)
    {
        if (bmp.CanFreeze) bmp.Freeze();
        if (Cache.Count > 40) Cache.Clear();
        var brush = new ImageBrush(bmp) { Stretch = Stretch.Fill };
        brush.Freeze();
        Cache[key] = brush;
        return brush;
    }

    /// Tek bir sahne (gece ya da gündüz).
    private static void Draw(DrawingContext dc, double w, double h, string code, bool night)
    {
        var grp = Theme.SceneGroup(code);
        var (top, bot) = Theme.PaletteFor(code, night);
        dc.DrawRectangle(new LinearGradientBrush(top, bot, 90), null, new Rect(0, 0, w, h));
        var rnd = new Random((int)(w * 7919 + h * 104729 + grp.Length * 31 + (night ? 1 : 0)));
        double R() => rnd.NextDouble();

        void Glow(double cx, double cy, double rad, Color c, byte a) =>
            dc.DrawEllipse(Soft(c, a), null, new Point(cx, cy), rad, rad);

        void Blobs(int n, Color c, byte a, double y0, double y1)
        {
            var brush = Soft(c, a);
            for (int i = 0; i < n; i++)
            {
                double cx = (-0.1 + R() * 1.2) * w, cy = (y0 + R() * (y1 - y0)) * h;
                double rx = (0.18 + R() * 0.18) * w * 1.25, ry = rx * (0.35 + R() * 0.2);
                dc.DrawEllipse(brush, null, new Point(cx, cy), rx, ry);
            }
        }

        if (grp is "clear" or "partly")
        {
            if (night)
            {
                for (int i = 0; i < (int)(w * h / 1800 * 1.56); i++)
                {
                    double r = new[] { 0.5, 0.8, 1.1, 1.5 }[rnd.Next(4)] * 0.8;
                    dc.DrawEllipse(new SolidColorBrush(Color.FromArgb((byte)rnd.Next(70, 230), 255, 255, 255)), null,
                        new Point(R() * w, R() * h * 0.75), r, r);
                }
                Glow(w * 0.86, h * 0.1, w * 0.45, Color.FromRgb(210, 225, 255), 55);
            }
            else Glow(w * 0.88, h * 0.06, w * 0.75, Color.FromRgb(255, 225, 150), 150);
        }
        if (grp is "partly" or "cloudy" or "rain" or "snow" or "storm")
        {
            bool dark = grp is "rain" or "storm";
            var col = night ? Color.FromRgb(210, 220, 235) : dark ? Color.FromRgb(70, 78, 96) : Colors.White;
            Blobs(grp != "partly" ? 9 : 5, col, (byte)(night ? 70 : dark ? 120 : 85), -0.05, 0.38);
            if (dark) Blobs(6, Color.FromRgb(230, 236, 245), 35, 0.0, 0.3);
        }
        if (grp is "rain" or "storm")
        {
            var pen = new Pen(new SolidColorBrush(Color.FromArgb(60, 200, 220, 255)), 0.8);
            for (int i = 0; i < (int)(w * h / 1400 * 1.56); i++)
            {
                double x = R() * w * 1.1, y = R() * h, ln = 8 + R() * 8;
                dc.DrawLine(pen, new Point(x, y), new Point(x - ln * 0.25, y + ln));
            }
        }
        if (grp == "snow")
        {
            for (int i = 0; i < (int)(w * h / 1200 * 1.56); i++)
            {
                double r = 0.6 + R() * 1.3;
                dc.DrawEllipse(new SolidColorBrush(Color.FromArgb((byte)rnd.Next(110, 220), 255, 255, 255)), null,
                    new Point(R() * w, R() * h), r, r);
            }
        }
        if (grp == "fog")
        {
            var band = Soft(Color.FromRgb(235, 238, 242), 60);
            for (int i = 0; i < 6; i++)
                dc.DrawEllipse(band, null, new Point(w / 2, (0.15 + R() * 0.8) * h), w * 0.7, h * 0.09);
        }
        if (grp == "storm")
        {
            var pts = new List<Point>();
            double x = w * (0.55 + R() * 0.25), y = 0;
            while (y < h * 0.55)
            {
                pts.Add(new Point(x, y));
                x += -11 + R() * 22;
                y += 8 + R() * 8;
            }
            var geo = new StreamGeometry();
            using (var g = geo.Open())
            {
                g.BeginFigure(pts[0], false, false);
                g.PolyLineTo(pts.Skip(1).ToList(), true, true);
            }
            dc.DrawGeometry(null, new Pen(new SolidColorBrush(Color.FromArgb(70, 235, 225, 255)), 7), geo);   // hale
            dc.DrawGeometry(null, new Pen(new SolidColorBrush(Color.FromArgb(200, 235, 225, 255)), 1.6), geo);
        }
    }

    /// Ortası opak, kenarı sıfıra inen yumuşak leke (Python glow / GaussianBlur yerine).
    private static Brush Soft(Color c, byte a) => new RadialGradientBrush(new GradientStopCollection
    {
        new(Color.FromArgb(a, c.R, c.G, c.B), 0),
        new(Color.FromArgb((byte)(a * 0.5), c.R, c.G, c.B), 0.5),
        new(Color.FromArgb(0, c.R, c.G, c.B), 1),
    });
}
