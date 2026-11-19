using System.Windows;
using System.Windows.Media;
using System.Windows.Media.Imaging;
using HavaDurumu.Core;

namespace HavaDurumu.Ui;

/// Hava durumu ikonları: Python make_icon() çiziminin vektör karşılığı (100x100 tasarım alanı).
/// Vektör olduğu için her boyutta keskin; dondurulmuş (Freeze) ve önbellekli — çizim maliyeti bir kez.
public static class WeatherIcons
{
    private static readonly Dictionary<(string, bool, bool, int, int), DrawingImage> Cache = new();

    private static readonly Color SunC = Color.FromRgb(255, 200, 61);
    private static readonly Color SunHot = Color.FromRgb(255, 140, 50);
    private static readonly Color RainC = Color.FromRgb(92, 176, 255);

    /// Animasyon döngüsü 2,25 sn (Python ANIM_CYCLE); 60 fps için 135 kare. Kullanıcı kararı (2026-11-19):
    /// ikon ekranın Hz değerinde değil 60 fps'te çizilir; 144 Hz'de toplam yükü ~10 puan azaltır.
    public const double CycleSec = 2.25;
    public const int AnimFrames = 135;

    /// Bu ikonun hareketli bir parçası var mı? (yağmur, kar, güneş ışınları) — Python icon_animated.
    public static bool IsAnimated(string? code, bool night)
    {
        var c = code ?? "";
        if (Constants.Rain.ContainsKey(c) || Constants.Snow.ContainsKey(c) || c == "KKY") return true;
        return !night && c is "A" or "SCK" or "AB" or "PB" or "PUS";
    }

    /// lightScheme: açık zemin için koyu tonlar (Python scheme == "light").
    /// frame: null = sabit ikon; 0..47 = animasyon karesi (kareler de önbellekte; her tikte yalnızca kaynak değişir).
    public static DrawingImage Get(string? code, bool night, bool lightScheme, int? frame = null)
    {
        int f = frame is int fr && IsAnimated(code, night) ? ((fr % AnimFrames) + AnimFrames) % AnimFrames : -1;
        int moonQ = night ? MoonQ(DateTime.UtcNow) : 0;
        var key = (code ?? "CB", night, lightScheme, moonQ, f);
        if (Cache.TryGetValue(key, out var img)) return img;
        if (Cache.Count > AnimFrames * 6) Cache.Clear();
        img = Build(key.Item1, night, lightScheme, moonQ / 28.0, f);
        Cache[key] = img;
        return img;
    }

    private static readonly Dictionary<(string, bool, bool, int, int, int), BitmapSource> BitmapCache = new();

    /// Animasyon karesi, ekran çözünürlüğünde hazır bitmap olarak (her tikte GPU yalnızca dokuyu
    /// birleştirir; vektör geometri her karede yeniden işlenmez). px: fiziksel piksel kenar uzunluğu.
    public static BitmapSource GetBitmap(string? code, bool night, bool lightScheme, int frame, int px)
    {
        px = Math.Clamp(px, 8, 512);
        int f = ((frame % AnimFrames) + AnimFrames) % AnimFrames;
        int moonQ = night ? MoonQ(DateTime.UtcNow) : 0;
        var key = (code ?? "CB", night, lightScheme, moonQ, f, px);
        if (BitmapCache.TryGetValue(key, out var bmp)) return bmp;
        if (BitmapCache.Count > AnimFrames * 6) BitmapCache.Clear();
        var drawing = Get(code, night, lightScheme, f).Drawing;
        var dv = new DrawingVisual();
        using (var dc = dv.RenderOpen())
        {
            dc.PushTransform(new ScaleTransform(px / 100.0, px / 100.0));
            dc.DrawDrawing(drawing);
        }
        var rtb = new RenderTargetBitmap(px, px, 96, 96, PixelFormats.Pbgra32);
        rtb.Render(dv);
        rtb.Freeze();
        BitmapCache[key] = rtb;
        return rtb;
    }

    /// 0..27 (0 yeni ay, 14 dolunay). Ortalama sinodik ay ile; ±1 gün.
    public static int MoonQ(DateTime utc)
    {
        var refT = new DateTime(2000, 1, 6, 18, 14, 0, DateTimeKind.Utc);
        double p = ((utc - refT).TotalDays / 29.530588853) % 1.0;
        if (p < 0) p += 1;
        return (int)Math.Round(p * 28) % 28;
    }

    private static DrawingImage Build(string c, bool night, bool light, double moonP, int frame)
    {
        double ph = frame < 0 ? 0 : frame / (double)AnimFrames;   // 0..1 döngü fazı

        // i. damlanın/tanenin (aşağı kayma, opaklık) değeri — Python fall()
        (double Dy, byte Alpha) Fall(int i, double speed = 2)
        {
            if (frame < 0) return (0, 255);
            double t = (ph * speed + i * 0.37) % 1.0;
            double a = Math.Min(1.0, Math.Min(t / 0.15, (1.0 - t) / 0.35));
            return (t * 10 - 2, (byte)(255 * Math.Max(0, a)));
        }

        Color cloud, cloudGrey, cloudDark, snow, fog, moon;
        if (light)
        {
            cloud = Color.FromRgb(198, 209, 224); cloudGrey = Color.FromRgb(152, 165, 184);
            cloudDark = Color.FromRgb(108, 116, 138); snow = Color.FromRgb(140, 182, 232);
            fog = Color.FromRgb(132, 142, 160); moon = Color.FromRgb(214, 190, 110);
        }
        else
        {
            cloud = Color.FromRgb(238, 243, 250); cloudGrey = Color.FromRgb(176, 188, 204);
            cloudDark = Color.FromRgb(128, 136, 156); snow = Colors.White;
            fog = Color.FromRgb(214, 220, 228); moon = Color.FromRgb(236, 232, 210);
        }

        var g = new DrawingGroup();
        // Görünmez çerçeve: tüm ikonlar aynı 100x100 alanı kaplasın (hizalama sabit kalsın).
        g.Children.Add(new GeometryDrawing(Brushes.Transparent, null, new RectangleGeometry(new Rect(0, 0, 100, 100))));

        void Circ(double x, double y, double r, Color col) =>
            g.Children.Add(new GeometryDrawing(B(col), null, new EllipseGeometry(new Point(x, y), r, r)));

        void Ln(double x1, double y1, double x2, double y2, double w, Color col)
        {
            var pen = new Pen(B(col), w) { StartLineCap = PenLineCap.Round, EndLineCap = PenLineCap.Round };
            pen.Freeze();
            g.Children.Add(new GeometryDrawing(null, pen, new LineGeometry(new Point(x1, y1), new Point(x2, y2))));
        }

        void SunAt(double x, double y, double r, Color col)
        {
            for (int i = 0; i < 8; i++)
            {
                double a = i * Math.PI / 4 + ph * Math.PI / 4;   // 8 ışın: 45° dönünce döngü kusursuz
                Ln(x + Math.Cos(a) * (r + 6), y + Math.Sin(a) * (r + 6),
                   x + Math.Cos(a) * (r + 12), y + Math.Sin(a) * (r + 12), 4.5, col);
            }
            Circ(x, y, r, col);
        }

        void MoonAt(double x, double y, double r)
        {
            Circ(x, y, r, Color.FromArgb(45, moon.R, moon.G, moon.B));
            // Aydınlık yarım: büyürken sağ, küçülürken sol yarı.
            bool waxing = moonP < 0.5;
            var half = new PathGeometry();
            var fig = new PathFigure { StartPoint = new Point(x, y - r), IsClosed = true };
            fig.Segments.Add(new ArcSegment(new Point(x, y + r), new Size(r, r), 0, false,
                waxing ? SweepDirection.Clockwise : SweepDirection.Counterclockwise, true));
            half.Figures.Add(fig);
            double cc = Math.Cos(2 * Math.PI * moonP); // 1: yeni ay, -1: dolunay
            double ew = (cc > 0 ? Math.Min(cc, 0.55) : -cc) * r;
            Geometry lit = ew < 0.01
                ? half
                : new CombinedGeometry(cc > 0 ? GeometryCombineMode.Exclude : GeometryCombineMode.Union,
                    half, new EllipseGeometry(new Point(x, y), ew, r));
            g.Children.Add(new GeometryDrawing(B(moon), null, lit));
        }

        void Sky(double x, double y, double r)
        {
            if (night) MoonAt(x, y, r * 1.15);
            else SunAt(x, y, r, SunC);
        }

        void Cloud(double ox, double oy, double sc, Color col)
        {
            var gg = new GeometryGroup { FillRule = FillRule.Nonzero };
            foreach (var (x, y, r) in new[] { (-20.0, 4.0, 14.0), (2.0, -6.0, 20.0), (22.0, 6.0, 12.0) })
                gg.Children.Add(new EllipseGeometry(new Point(ox + x * sc, oy + y * sc), r * sc, r * sc));
            gg.Children.Add(new RectangleGeometry(
                new Rect(ox - 34 * sc, oy + 2 * sc, 68 * sc, 16 * sc), 8 * sc, 8 * sc));
            g.Children.Add(new GeometryDrawing(B(col), null, gg));
        }

        static double[] Xs(int n) => n switch
        {
            1 => new[] { 50.0 },
            2 => new[] { 40.0, 60.0 },
            3 => new[] { 32.0, 50.0, 68.0 },
            _ => new[] { 28.0, 42.0, 56.0, 70.0 },
        };

        void Drops(int n)
        {
            var xs = Xs(n);
            for (int i = 0; i < xs.Length; i++)
            {
                var (dy, al) = Fall(i);
                double yy = 74 + (i % 2 == 1 ? 6 : 0) + dy;
                Ln(xs[i] - dy * 0.33, yy, xs[i] - 4 - dy * 0.33, yy + 12, 5, Color.FromArgb(al, RainC.R, RainC.G, RainC.B));
            }
        }

        // Altı kollu kar tanesi: 3 çizgi + her kolun ucunda küçük V (Python flake).
        void Flake(double x, double y, double r, Color col, double rot)
        {
            for (int j = 0; j < 6; j++)
            {
                double a = rot + j * Math.PI / 3;
                Ln(x, y, x + Math.Cos(a) * r, y + Math.Sin(a) * r, 2.6, col);
                double mx = x + Math.Cos(a) * r * 0.6, my = y + Math.Sin(a) * r * 0.6;
                foreach (var s in new[] { -0.7, 0.7 })
                    Ln(mx, my, mx + Math.Cos(a + s) * r * 0.32, my + Math.Sin(a + s) * r * 0.32, 2.2, col);
            }
        }

        void Flakes(int n)
        {
            double[] xs = n switch
            {
                1 => new[] { 50.0 },
                2 => new[] { 38.0, 62.0 },
                3 => new[] { 30.0, 50.0, 70.0 },
                _ => new[] { 27.0, 43.0, 59.0, 75.0 },
            };
            for (int i = 0; i < xs.Length; i++)
            {
                var (dy, al) = Fall(i, speed: 1);
                double sway = frame >= 0 ? Math.Sin((ph + i * 0.3) * 2 * Math.PI) * 2 : 0;
                Flake(xs[i] + sway, 78 + (i % 2 == 1 ? 8 : 0) + dy, n < 4 ? 7 : 6,
                    Color.FromArgb(al, snow.R, snow.G, snow.B), ph * Math.PI / 3 + i);
            }
        }

        if (c is "A" or "SCK")
        {
            if (night) MoonAt(50, 50, 28);
            else SunAt(50, 50, 20, c == "SCK" ? SunHot : SunC);
        }
        else if (c == "AB") { Sky(40, 38, 17); Cloud(60, 64, 0.72, cloud); }
        else if (c == "PB") { Sky(36, 34, 17); Cloud(56, 62, 0.95, cloud); }
        else if (c == "CB") { Cloud(38, 42, 0.72, cloudGrey); Cloud(54, 58, 1.0, cloud); }
        else if (c is "HSY" or "SY" or "MSY" or "KSY")   // sağanak: koyu, alçak bulut + rüzgârla eğik sicim yağmur
        {
            Cloud(36, 40, 0.8, cloudDark);
            Cloud(54, 46, 1.05, cloudGrey);
            int n = Constants.Rain[c] + 2;
            for (int i = 0; i < n; i++)
            {
                var (dy, al) = Fall(i, speed: 3);
                double x = 26 + i * 52.0 / (Constants.Rain[c] + 1), y = 66 + (i % 2 == 1 ? 5 : 0) + dy;
                Ln(x - dy * 0.6, y, x - 8 - dy * 0.6, y + 18, 4, Color.FromArgb(al, RainC.R, RainC.G, RainC.B));
            }
        }
        else if (Constants.Rain.TryGetValue(c, out var rn))
        {
            Cloud(52, 46, 1.05, rn >= 3 ? cloudGrey : cloud);
            Drops(rn);
        }
        else if (c == "KKY")   // karla karışık: mavi damla ve beyaz kar tanesi sırayla
        {
            Cloud(50, 44, 1.05, cloud);
            double[] kx = { 28, 44, 60, 76 };
            for (int i = 0; i < kx.Length; i++)
            {
                var (dy, al) = Fall(i, speed: 1.5);
                double y = 72 + (i % 2 == 1 ? 8 : 0) + dy;
                if (i % 2 == 1) Flake(kx[i], y + 2, 7, Color.FromArgb(al, snow.R, snow.G, snow.B), ph * Math.PI / 3 + i);
                else Ln(kx[i] - dy * 0.33, y - 4, kx[i] - 4 - dy * 0.33, y + 9, 5, Color.FromArgb(al, RainC.R, RainC.G, RainC.B));
            }
        }
        else if (Constants.Snow.TryGetValue(c, out var sn)) { Cloud(50, 46, 1.05, cloud); Flakes(sn); }
        else if (c == "DY")
        {
            Cloud(50, 46, 1.05, cloudGrey);
            foreach (var (x, y) in new[] { (34.0, 80.0), (50.0, 88.0), (66.0, 80.0) })
                Circ(x, y, 5.5, Color.FromRgb(220, 238, 255));
        }
        else if (c is "GSY" or "KGSY")
        {
            Cloud(50, 44, 1.05, cloudDark);
            var bolt = new StreamGeometry();
            using (var ctx = bolt.Open())
            {
                ctx.BeginFigure(new Point(54, 58), true, true);
                ctx.PolyLineTo(new[] { new Point(40, 80), new Point(50, 80), new Point(44, 98),
                    new Point(64, 72), new Point(53, 72), new Point(60, 58) }, true, true);
            }
            g.Children.Add(new GeometryDrawing(B(SunC), null, bolt));
            Ln(30, 72, 26, 84, 5, RainC);
            Ln(72, 72, 68, 84, 5, RainC);
        }
        else if (c is "SIS" or "PUS" or "DMN" or "KF")
        {
            var col = c == "KF" ? Color.FromRgb(214, 180, 130) : fog;
            if (c == "PUS") Sky(50, 38, 16);
            else if (c is "SIS" or "DMN") Cloud(50, 38, 0.9, cloud);
            foreach (var (y, x1, x2) in new[] { (66.0, 20.0, 80.0), (77.0, 28.0, 72.0), (88.0, 20.0, 80.0) })
                Ln(x1, y, x2, y, 5, col);
        }
        else if (c is "R" or "GKR" or "KKR")
        {
            foreach (var (y, x2) in new[] { (34.0, 66.0), (50.0, 80.0), (66.0, 60.0) })
            {
                Ln(18, y, x2, y, 6, fog);
                // Python: arc(-100°..110°), merkez (x2, y-8), yarıçap 8
                double a1 = -100 * Math.PI / 180, a2 = 110 * Math.PI / 180;
                var p1 = new Point(x2 + 8 * Math.Cos(a1), y - 8 + 8 * Math.Sin(a1));
                var p2 = new Point(x2 + 8 * Math.Cos(a2), y - 8 + 8 * Math.Sin(a2));
                var arc = new PathGeometry();
                var f = new PathFigure { StartPoint = p1, IsClosed = false };
                f.Segments.Add(new ArcSegment(p2, new Size(8, 8), 0, true, SweepDirection.Clockwise, true));
                arc.Figures.Add(f);
                var pen = new Pen(B(fog), 6) { StartLineCap = PenLineCap.Round, EndLineCap = PenLineCap.Round };
                pen.Freeze();
                g.Children.Add(new GeometryDrawing(null, pen, arc));
            }
        }
        else if (c == "SGK")
        {
            for (int i = 0; i < 3; i++)
            {
                double a = i * Math.PI / 3;
                Ln(50 - Math.Cos(a) * 30, 50 - Math.Sin(a) * 30, 50 + Math.Cos(a) * 30, 50 + Math.Sin(a) * 30,
                    7, Color.FromRgb(190, 225, 255));
            }
        }
        else Cloud(50, 52, 1.05, cloud);

        var image = new DrawingImage(g);
        image.Freeze();
        return image;
    }

    private static Brush B(Color c) => Theme.Frozen(c);
}
