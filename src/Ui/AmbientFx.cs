using System.Windows;
using System.Windows.Media;
using System.Windows.Media.Effects;
using System.Windows.Media.Imaging;
using HavaDurumu.Core;

namespace HavaDurumu.Ui;

/// Kartın arka planı ile yazılar arasındaki hava efekti (Python AmbientFX): üst üste çizilen katmanlar
/// (parıltı, yıldız, bulut, yağış...). Parçacıkların başlangıç değerleri bir kez (sabit tohumla) üretilir;
/// konumlar zamana göre hesaplanır, kare hızı düşse de hareket aynı hızda kalır.
/// Yumuşak şekiller (bulut, sis bandı, parıltı) Python'daki gibi bir kez bulanıklaştırılmış bitmap olur;
/// karede yalnızca konumları ve parlaklıkları değişir. Fırça ve kalemler dondurulmuş; karede yeni nesne
/// yalnızca sıcak hava dalgalarının çizgisi. Koordinatlar tasarım birimi (ölçek 1.0).
public sealed class AmbientFx
{
    private static readonly Color Sun = Color.FromRgb(255, 226, 150), Moon = Color.FromRgb(205, 220, 255),
        White = Colors.White, NCloud = Color.FromRgb(125, 140, 172);   // gece bulutu: ay ışığında mavimsi gri

    private readonly double _w, _h;
    private readonly bool _light;
    private readonly Random _rnd;
    private readonly List<Action<DrawingContext, double>> _layers = new();
    private readonly Dictionary<Color, Brush[]> _ramps = new();

    public string Kind { get; }

    /// Python fx_kind: hava kodu -> efekt türü. Gündüz / gece farkı efektin içinde çizilir.
    public static string KindFor(string code) => code switch
    {
        "A" => "clear", "AB" => "fewclouds", "PB" => "partly", "CB" => "overcast", "KKY" => "sleet",
        "DY" => "hail", "GSY" or "KGSY" => "storm", "SIS" => "fog", "PUS" => "haze", "DMN" => "smoke",
        "R" or "GKR" or "KKR" => "wind", "KF" => "dust", "SCK" => "heat", "SGK" => "frost", "HHY" => "rain",
        "HSY" or "SY" or "MSY" or "KSY" => "showers",
        _ => Constants.Rain.ContainsKey(code) ? "rain" : Constants.Snow.ContainsKey(code) ? "snow" : "partly",
    };

    public AmbientFx(string kind, double w, double h, bool light, int level, Point sunCenter, double sunR, bool night)
    {
        Kind = kind;
        _w = w;
        _h = h;
        _light = light;
        _rnd = new Random(StableSeed($"{kind}{night}{w}x{h}"));
        double area = Math.Sqrt(Math.Max(0.4, w * h / (188.0 * 98.0)));   // mini kart = 1
        int N(double b, double cap = 90) => Math.Max(1, (int)Math.Min(cap, b * area));
        double cx = sunCenter.X, cy = sunCenter.Y, cr = sunR > 0 ? sunR : h * 0.3;
        if (sunCenter == default) (cx, cy) = (w * 0.2, h * 0.35);
        var rainc = night ? Color.FromRgb(175, 205, 255) : White;
        double ram = night ? 0.8 : 1.0;

        switch (kind)
        {
            case "clear":
                if (night)
                {
                    Stars(N(14, 40));
                    Glow(cx, cy, cr, Moon, 0.10, 0.18);
                }
                else
                {
                    Glow(cx, cy, cr, Sun, 0.26, 0.45);
                    Spots(cx, cy, cr);
                    Motes(N(6, 12), Color.FromRgb(255, 240, 200), 80);
                }
                break;
            case "fewclouds":
            case "partly":
            {
                // güneş/ay açık kalsın: küçük, belirgin bulutlar arasında mavi gökyüzü görünsün
                bool few = kind == "fewclouds";
                if (night) Glow(cx, cy, cr, Moon, 0.10, 0.17);
                else
                {
                    Glow(cx, cy, cr, Sun, 0.24, 0.42);
                    Spots(cx, cy, cr, few ? 0.8 : 0.6);
                    Motes(N(few ? 4 : 3, 8), Color.FromRgb(255, 240, 200), 75);
                }
                Clouds(few ? 1 : 2, few ? 1 : 2, night ? NCloud : White, night ? 135 : 150, 0.45,
                    few ? (0.22, 0.34) : (0.26, 0.4), blur: 0.07, speed: 1.3);
                break;
            }
            case "overcast":
                Clouds(3, 3, night ? Color.FromRgb(100, 112, 140) : Color.FromRgb(228, 232, 240), 105, 0.6, (0.6, 0.95));
                break;
            case "showers":   // sağanak: hızlı koyu bulutlar, dalga dalga gelen eğik yoğun yağmur
                Clouds(2, 2, night ? Color.FromRgb(60, 68, 92) : Color.FromRgb(88, 98, 122), 130, 0.1, (0.55, 0.9), speed: 2.4);
                Rain(N(level switch { 2 => 22, 4 => 44, _ => 32 }), rainc, ram, slant: 0.45, gust: true);
                break;
            case "rain":
            case "snow":
            case "sleet":
            case "hail":
            case "storm":
            {
                bool dark = kind == "storm";
                var cc = dark ? (night ? Color.FromRgb(45, 50, 70) : Color.FromRgb(70, 78, 100))
                    : night ? NCloud : Color.FromRgb(215, 222, 232);
                Clouds(1, 2, cc, dark ? 115 : 85, 0.12, (0.55, 0.9), speed: dark ? 1.6 : 1.0);
                int nr = (dark ? 4 : level) switch { 2 => 11, 4 => 26, _ => 18 };
                var snowc = night ? Color.FromRgb(215, 228, 255) : White;
                if (kind is "rain" or "storm") Rain(N(nr), rainc, ram);
                else if (kind == "sleet")
                {
                    Rain(N(nr * 0.5), rainc, ram);
                    Snow(N(9, 40), snowc);
                }
                else if (kind == "snow") Snow(N(18, 80), snowc);
                else
                {
                    Rain(N(10), rainc, ram);   // dolu
                    Pellets(N(8, 30));
                }
                if (dark) Flash((byte)(night ? 95 : 70));
                break;
            }
            case "fog":
            case "haze":
            case "smoke":
            {
                var col = kind switch
                {
                    "fog" => night ? Color.FromRgb(150, 165, 190) : Color.FromRgb(225, 232, 240),
                    "haze" => night ? Color.FromRgb(140, 150, 175) : Color.FromRgb(240, 232, 210),
                    _ => night ? Color.FromRgb(110, 100, 95) : Color.FromRgb(175, 160, 140),
                };
                if (kind == "haze")
                {
                    Glow(cx, cy, cr, night ? Moon : Sun, 0.12, 0.22);
                    if (!night) Spots(cx, cy, cr, 0.45);
                }
                Bands(kind == "fog" ? 4 : 3, col, kind == "fog" ? 60 : 45);
                break;
            }
            case "wind":
            case "dust":
                if (kind == "dust")
                {
                    Streaks(N(9, 24), Color.FromRgb(205, 175, 125), night ? 0.30 : 0.45);
                    Dust(N(26, 70), Color.FromRgb(210, 180, 130), night ? 0.6 : 1.0);
                }
                else if (night)
                {
                    Clouds(1, 2, NCloud, 65, 0.4, (0.5, 0.85), speed: 3.0);
                    Streaks(N(9, 24), White, 0.30);
                }
                else
                {
                    Streaks(N(9, 24), White, 0.45);
                    Leaves(N(4, 10));
                }
                break;
            case "heat":
                if (night)
                {
                    Stars(N(8, 24));
                    Motes(N(5, 10), Color.FromRgb(255, 200, 150), 60);
                }
                else
                {
                    Glow(cx, cy, cr, Sun, 0.3, 0.5);
                    Sunflare(cx, cy, cr, 1.1);
                    Spots(cx, cy, cr, 1.1);
                    Heatwaves(N(4, 8));
                }
                break;
            case "frost":
                if (night) Stars(N(10, 30));
                else Glow(cx, cy, cr, Color.FromRgb(225, 240, 255), 0.10, 0.18);
                Sparkles(N(8, 22));
                break;
        }
    }

    /// t: saniye.
    public void Draw(DrawingContext dc, double t)
    {
        foreach (var layer in _layers) layer(dc, t);
    }

    // ------------------------------------------------------------------ katmanlar
    /// Kümülüs biçimli yumuşak bulutlar, iki derinlikte (uzaktakiler soluk ve yavaş).
    private void Clouds(int far, int near, Color col, double alpha, double yHi, (double Lo, double Hi) size,
        double speed = 1.0, double blur = 0.13)
    {
        double w = _w, h = _h;
        col = Ink(col);
        var puffs = new[] { (0.0, 0.55, 1.0, 1.0), (0.08, 0.3, 0.45, 0.95), (0.28, 0.02, 0.72, 0.85), (0.52, 0.18, 0.92, 0.92) };
        var items = new List<(ImageSource Spr, Rect R, double X0, double Y, double V)>();
        foreach (var (depth, cnt) in new[] { (0, far), (1, near) })
            for (int i = 0; i < cnt; i++)
            {
                double cw = w * U(size.Lo, size.Hi) * (depth == 0 ? 0.8 : 1.0), ch = cw * U(0.3, 0.4);
                double a = alpha * (depth == 0 ? 0.6 : 1.0) * (_light ? 0.45 : 1);
                var (spr, r) = SoftSprite(cw, ch, col, a, Math.Max(1.5, ch * blur),
                    puffs.Select(p => new Rect(p.Item1 * cw, p.Item2 * ch, (p.Item3 - p.Item1) * cw, (p.Item4 - p.Item2) * ch)));
                double v = (depth == 0 ? U(4, 7) : U(8, 14)) * speed;
                items.Add((spr, r, U(0, w + r.Width), U(-0.15, yHi) * h, v));
            }
        _layers.Add((dc, t) =>
        {
            foreach (var (spr, r, x0, y, v) in items)
                dc.DrawImage(spr, new Rect(Mod(x0 + v * t, w + r.Width) - r.Width, y, r.Width, r.Height));
        });
    }

    private void Rain(int n, Color col, double am = 1.0, double slant = 0.22, bool gust = false)
    {
        double w = _w, h = _h, k = gust ? 1.5 : 1.0;   // sağanakta damlalar daha uzun ve hızlı
        var items = Enumerable.Range(0, n).Select(_ => (X0: U(0, w + h * slant), Y0: U(0, h), V: U(190, 280) * k,
            L: U(7, 13) * k, A: U(55, 105) * am * (_light ? 0.8 : 1))).ToList();
        var ramp = Ramp(Ink(col));
        var pens = ramp.Select(b => Fz(new Pen(b, 1.0))).ToArray();
        double span = w + h * slant;
        _layers.Add((dc, t) =>
        {
            foreach (var (x0, y0, v, l, a) in items)
            {
                double y = Mod(y0 + v * t, h + l), x = Mod(x0 - slant * (y0 + v * t), span);
                double al = gust ? a * (0.35 + 0.65 * Math.Max(0, Math.Sin(t * 1.3 - x / w * 4))) : a;   // yağmur perdeleri
                dc.DrawLine(pens[Lv(al)], new Point(x + slant * l, y - l), new Point(x, y));
            }
        });
    }

    private void Snow(int n, Color col)
    {
        double w = _w, h = _h;
        var ramp = Ramp(Ink(col));
        var items = Enumerable.Range(0, n).Select(_ => (X0: U(0, w), Y0: U(0, h), R: U(1.0, 2.2), V: U(16, 30), Amp: U(7, 14),
            F: U(0.6, 1.5), Ph: U(0, 6.3), B: ramp[Lv(U(150, 220) * (_light ? 0.7 : 1))])).ToList();
        _layers.Add((dc, t) =>
        {
            foreach (var (x0, y0, r, v, amp, f, ph, b) in items)
                dc.DrawEllipse(b, null, new Point(Mod(x0 + amp * Math.Sin(t * f + ph), w), Mod(y0 + v * t, h + 2 * r) - r), r, r);
        });
    }

    /// Dolu: hızlı düşen iri, parlak taneler.
    private void Pellets(int n)
    {
        double w = _w, h = _h;
        var ramp = Ramp(Ink(White));
        var items = Enumerable.Range(0, n).Select(_ => (X0: U(0, w), Y0: U(0, h), R: U(1.4, 2.4), V: U(280, 360),
            B: ramp[Lv(U(170, 230))])).ToList();
        _layers.Add((dc, t) =>
        {
            foreach (var (x0, y0, r, v, b) in items)
                dc.DrawEllipse(b, null, new Point(Mod(x0 - 0.1 * (y0 + v * t), w), Mod(y0 + v * t, h + 2 * r) - r), r, r);
        });
    }

    private void Stars(int n)
    {
        double w = _w, h = _h;
        var items = Enumerable.Range(0, n).Select(_ => (X: U(0, w), Y: U(0, Math.Min(h * 0.75, 150)), R: U(0.6, 1.3),
            F: U(0.6, 1.8), Ph: U(0, 6.3), Lo: U(20, 50), Hi: U(150, 230))).ToList();
        var (sph, sx, sy) = (U(0, 11), U(0.1, 0.5), U(0.05, 0.3));   // kayan yıldız: faz, başlangıç
        var ramp = Ramp(Ink(White));
        var pens = ramp.Select(b => Fz(new Pen(b, 1.0))).ToArray();
        _layers.Add((dc, t) =>
        {
            foreach (var (x, y, r, f, ph, lo, hi) in items)
                dc.DrawEllipse(ramp[Lv(lo + (hi - lo) * (0.5 + 0.5 * Math.Sin(t * f + ph)))], null, new Point(x, y), r, r);
            double p = Mod(t + sph, 11.0);
            if (p >= 0.8) return;   // 11 saniyede bir kayan yıldız
            double q = p / 0.8, x1 = w * (sx + 0.45 * q), y1 = h * (sy + 0.16 * q);
            for (int j = 0; j < 6; j++)
            {
                double f = j / 6.0;
                var end = new Point(x1 - w * 0.05 * (1 - f), y1 - h * 0.02 * (1 - f));
                dc.DrawLine(pens[Lv(220 * f * Math.Sin(q * Math.PI))], new Point(end.X - w * 0.05, end.Y - h * 0.02), end);
            }
        });
    }

    /// Güneşin / ayın arkasında nefes alır gibi parlayan ışık.
    private void Glow(double cx, double cy, double r, Color col, double lo, double hi)
    {
        double R = r * 1.9;
        col = _light ? Color.FromRgb(120, 90, 30) : col;
        var (spr, rect) = SoftSprite(2 * R, 2 * R, col, 255, R * 0.35, new[] { new Rect(R * 0.35, R * 0.35, R * 1.3, R * 1.3) });
        var at = new Rect(cx - rect.Width / 2, cy - rect.Height / 2, rect.Width, rect.Height);
        _layers.Add((dc, t) =>
        {
            dc.PushOpacity(lo + (hi - lo) * (0.5 - 0.5 * Math.Cos(t / 2.8 * 2 * Math.PI)));   // 2,8 sn'de bir nefes
            dc.DrawImage(spr, at);
            dc.Pop();
        });
    }

    /// Güneşin çevresinde nabız gibi atan ince hale halkası (yalnızca çok sıcak günlerde).
    private void Sunflare(double cx, double cy, double r, double s = 1.0)
    {
        if (_light) return;   // açık temada sarı ışık zemini kirletir
        var col = Color.FromRgb(255, 244, 210);   // sarı mavi zeminde griye döner: neredeyse beyaz, sıcak bir ışık
        double q = r * 0.95, th = Math.Max(2, r * 0.09), m = 4 * th;
        var dv = new DrawingVisual { Effect = new BlurEffect { Radius = th * 0.8 * 3, KernelType = KernelType.Gaussian } };
        using (var dc = dv.RenderOpen())
            dc.DrawEllipse(null, Fz(new Pen(Fz(new SolidColorBrush(col)), th)), new Point(m + q, m + q), q, q);
        var spr = Bake(dv, 2 * (q + m), 2 * (q + m));
        var at = new Rect(cx - q - m, cy - q - m, 2 * (q + m), 2 * (q + m));
        _layers.Add((dc, t) =>
        {
            dc.PushOpacity(s * (0.25 + 0.5 * (0.5 - 0.5 * Math.Cos(t / 3.2 * 2 * Math.PI))));   // 3,2 sn'de bir nabız
            dc.DrawImage(spr, at);
            dc.Pop();
        });
    }

    /// Güneşten kartın içine doğru dizilen, hafifçe süzülüp parlayan soluk ışık lekeleri.
    private void Spots(double cx, double cy, double r, double s = 1.0)
    {
        if (_light) return;
        double dx = _w * 0.62 - cx, dy = _h * 0.62 - cy;
        var ghosts = new List<(double F, ImageSource Spr, double D, double A)>();
        foreach (var (f, rr, col, a) in new[] { (0.45, 0.16, Color.FromRgb(255, 225, 150), 0.55), (0.8, 0.3, Color.FromRgb(190, 225, 255), 0.30),
                     (1.15, 0.1, Color.FromRgb(255, 210, 120), 0.65), (1.5, 0.45, Color.FromRgb(255, 240, 205), 0.22) })
        {
            double q = Math.Floor(r * rr) + 1;
            var (spr, rect) = SoftSprite(2 * q, 2 * q, col, 255, q * 0.35, new[] { new Rect(0, 0, 2 * q, 2 * q) });
            ghosts.Add((f, spr, rect.Width, a * s));
        }
        _layers.Add((dc, t) =>
        {
            for (int i = 0; i < ghosts.Count; i++)
            {
                var (f, spr, d, a) = ghosts[i];
                double g = f + 0.06 * Math.Sin(t * 0.45 + i * 1.3);
                dc.PushOpacity(a * (0.4 + 0.6 * (0.5 + 0.5 * Math.Sin(t * 0.8 + i * 1.7))));
                dc.DrawImage(spr, new Rect(cx + dx * g - d / 2, cy + dy * g - d / 2, d, d));
                dc.Pop();
            }
        });
    }

    /// Aşağıdan yukarı süzülen, sönerek kaybolan ışık zerreleri.
    private void Motes(int n, Color col, double amax)
    {
        double w = _w, h = _h;
        var ramp = Ramp(_light ? Color.FromRgb(120, 90, 20) : col);
        var items = Enumerable.Range(0, n).Select(_ => (X0: U(0, w), P0: U(0, 1), V: U(5, 10), R: U(1.2, 2.4),
            F: U(0.6, 1.4), Ph: U(0, 6.3))).ToList();
        _layers.Add((dc, t) =>
        {
            foreach (var (x0, p0, v, r, f, ph) in items)
            {
                double life = Mod(p0 + t * v / Math.Max(1, h), 1.0);
                dc.DrawEllipse(ramp[Lv(amax * Math.Sin(life * Math.PI))], null,
                    new Point(x0 + 6 * Math.Sin(t * f + ph), h * (1 - life)), r, r);
            }
        });
    }

    /// Sis / pus / duman: yavaşça dalgalanan yumuşak bantlar.
    private void Bands(int n, Color col, double alpha)
    {
        double w = _w, h = _h;
        var items = new List<(ImageSource Spr, Rect R, double Y, double Amp, double F, double Ph)>();
        for (int i = 0; i < n; i++)
        {
            double bw = w * 1.6, bh = h * U(0.16, 0.26);
            var (spr, r) = SoftSprite(bw, bh, Ink(col), alpha * U(0.8, 1.15) * (_light ? 0.4 : 1), 7, new[] { new Rect(0, 0, bw, bh) });
            double yc = h * (0.12 + 0.8 * i / Math.Max(1, n - 1));
            items.Add((spr, r, yc - r.Height / 2, w * U(0.15, 0.3), U(0.18, 0.35), U(0, 6.3)));
        }
        _layers.Add((dc, t) =>
        {
            foreach (var (spr, r, y, amp, f, ph) in items)
                dc.DrawImage(spr, new Rect(-w * 0.3 + amp * Math.Sin(t * f + ph), y, r.Width, r.Height));
        });
    }

    /// Rüzgar: hızla geçen, iki ucu solan ince çizgiler.
    private void Streaks(int n, Color col, double am)
    {
        double w = _w, h = _h;
        var c = Ink(col);
        var peak = WithA(c, (byte)Math.Clamp(255 * am * (_light ? 0.8 : 1), 0, 255));
        var brush = Fz(new LinearGradientBrush(new GradientStopCollection { new(WithA(c, 0), 0), new(peak, 0.5), new(WithA(c, 0), 1) },
            new Point(0, 0), new Point(1, 0)));
        var items = Enumerable.Range(0, n).Select(_ => (L: U(30, 60), X0: 0.0, Y0: U(0.08, 0.92) * h, V: U(140, 210), Ph: U(0, 6.3)))
            .Select(s => s with { X0 = U(0, w + s.L) }).ToList();
        _layers.Add((dc, t) =>
        {
            foreach (var (l, x0, y0, v, ph) in items)
                dc.DrawRectangle(brush, null, new Rect(Mod(x0 + v * t, w + l) - l, y0 + 3 * Math.Sin(t * 1.3 + ph), l, 1.0));
        });
    }

    /// Toz fırtınası: hızla savrulan küçük taneler.
    private void Dust(int n, Color col, double am)
    {
        double w = _w, h = _h;
        var ramp = Ramp(Ink(col));
        var items = Enumerable.Range(0, n).Select(_ => (X0: U(0, w), Y0: U(0, h), R: U(0.6, 1.3), V: U(60, 120), Amp: U(2, 6),
            F: U(1, 3), Ph: U(0, 6.3), B: ramp[Lv(U(60, 140) * am)])).ToList();
        _layers.Add((dc, t) =>
        {
            foreach (var (x0, y0, r, v, amp, f, ph, b) in items)
                dc.DrawEllipse(b, null, new Point(Mod(x0 + v * t, w), y0 + amp * Math.Sin(t * f + ph)), r, r);
        });
    }

    /// Rüzgarlı gündüz: dönerek uçuşan yapraklar.
    private void Leaves(int n)
    {
        double w = _w, h = _h;
        var cols = new[] { Color.FromRgb(196, 160, 72), Color.FromRgb(150, 170, 80), Color.FromRgb(205, 120, 60) };
        var items = Enumerable.Range(0, n).Select(i => (X0: U(0, w), Y0: U(0.15, 0.85) * h, R: U(2.2, 3.4), V: U(50, 90),
            Amp: U(6, 14), F: U(0.8, 1.6), Ph: U(0, 6.3), Spin: U(2, 4), B: Fz(new SolidColorBrush(WithA(Ink(cols[i % 3]), 170))))).ToList();
        _layers.Add((dc, t) =>
        {
            foreach (var (x0, y0, r, v, amp, f, ph, spin, b) in items)
            {
                double rx = r * Math.Abs(Math.Cos(t * spin + ph)) + 0.5;
                dc.DrawEllipse(b, null, new Point(Mod(x0 + v * t, w + 10) - 5, y0 + amp * Math.Sin(t * f + ph)), rx, r * 0.55);
            }
        });
    }

    /// Sıcak gündüz: yukarı doğru yükselen, dalgalı sıcak hava çizgileri.
    private void Heatwaves(int n)
    {
        double w = _w, h = _h;
        var items = Enumerable.Range(0, n).Select(_ => (P0: U(0, 1), Ph: U(0, 6.3))).ToList();
        var pens = Ramp(_light ? Color.FromRgb(120, 90, 30) : Color.FromRgb(255, 230, 190)).Select(b => Fz(new Pen(b, 1.0))).ToArray();
        _layers.Add((dc, t) =>
        {
            foreach (var (p0, ph) in items)
            {
                double life = Mod(p0 + t * 0.12, 1.0), y = h * (0.95 - 0.7 * life);
                var g = new StreamGeometry();
                using (var ctx = g.Open())
                {
                    ctx.BeginFigure(new Point(0, y + 2 * Math.Sin(t * 3 + ph)), false, false);
                    for (double x = 6; x <= w + 6; x += 6)
                        ctx.LineTo(new Point(x, y + 2 * Math.Sin(x * 0.06 + t * 3 + ph)), true, false);
                }
                g.Freeze();
                dc.DrawGeometry(null, pens[Lv(55 * Math.Sin(life * Math.PI))], g);
            }
        });
    }

    /// Soğuk hava: ara sıra parlayıp sönen kırağı pırıltıları.
    private void Sparkles(int n)
    {
        double w = _w, h = _h;
        var pens = Ramp(Ink(Color.FromRgb(220, 235, 255))).Select(b => Fz(new Pen(b, 1.0))).ToArray();
        var items = Enumerable.Range(0, n).Select(_ => (X: U(0, w), Y: U(0, h), S: U(2, 4), F: U(0.25, 0.6), Ph: U(0, 1))).ToList();
        _layers.Add((dc, t) =>
        {
            foreach (var (x, y, s, f, ph) in items)
            {
                double k = Math.Pow(Math.Max(0, Math.Sin(Mod(t * f + ph, 1.0) * Math.PI)), 3);
                if (k < 0.05) continue;
                double l = s * k;
                var pen = pens[Lv(230 * k)];
                dc.DrawLine(pen, new Point(x - l, y), new Point(x + l, y));
                dc.DrawLine(pen, new Point(x, y - l), new Point(x, y + l));
            }
        });
    }

    private void Flash(byte alpha)
    {
        var b = Fz(new SolidColorBrush(Color.FromArgb(alpha, 255, 255, 255)));
        var r = new Rect(0, 0, _w, _h);
        _layers.Add((dc, t) =>
        {
            double p = t % 6.5;   // şimşek: kısa, iki çakışlı
            if (p < 0.07 || (0.16 < p && p < 0.22)) dc.DrawRectangle(b, null, r);
        });
    }

    // ------------------------------------------------------------------ yardımcılar
    private double U(double a, double b) => a + _rnd.NextDouble() * (b - a);

    /// Açık temada açık renkler görünmez: efektler koyu mavi-gri çizilir.
    private Color Ink(Color c) => _light ? Color.FromRgb(35, 55, 85) : c;

    /// Rengin 0..255 opaklıklarının 33 kademelik dondurulmuş fırçaları (karede yeni fırça yok).
    private Brush[] Ramp(Color c)
    {
        if (_ramps.TryGetValue(c, out var r)) return r;
        r = Enumerable.Range(0, 33).Select(i => (Brush)Fz(new SolidColorBrush(WithA(c, (byte)Math.Min(255, i * 8))))).ToArray();
        _ramps[c] = r;
        return r;
    }

    private static int Lv(double alpha) => Math.Clamp((int)Math.Round(alpha / 8), 0, 32);

    /// Yumuşak kenarlı (bulanık) şekil, bir kez bitmap'e çizilir (Python _soft_sprite). Dönen dikdörtgen
    /// kenar payıyla birlikte boyuttur; şekil payın içinden başlar.
    private static (ImageSource, Rect) SoftSprite(double w, double h, Color col, double alpha, double blur, IEnumerable<Rect> shapes)
    {
        double m = Math.Floor(blur * 2.5) + 1;
        var brush = Fz(new SolidColorBrush(WithA(col, (byte)Math.Clamp(alpha, 0, 255))));
        var geo = new GeometryGroup { FillRule = FillRule.Nonzero };
        foreach (var s in shapes) geo.Children.Add(new EllipseGeometry(new Rect(s.X + m, s.Y + m, s.Width, s.Height)));
        geo.Freeze();
        // PIL GaussianBlur(radius) bir standart sapmadır; WPF BlurEffect.Radius çekirdek yarıçapı (~3 sigma)
        var dv = new DrawingVisual { Effect = new BlurEffect { Radius = blur * 3, KernelType = KernelType.Gaussian } };
        using (var dc = dv.RenderOpen()) dc.DrawGeometry(brush, null, geo);
        double bw = Math.Max(1, w) + 2 * m, bh = Math.Max(1, h) + 2 * m;
        return (Bake(dv, bw, bh), new Rect(0, 0, bw, bh));
    }

    /// Görseli 2 kat çözünürlükte bitmap'e çizer (ölçekli kartta da yumuşak kalsın).
    private static ImageSource Bake(Visual v, double w, double h)
    {
        var rtb = new RenderTargetBitmap((int)Math.Ceiling(w * 2), (int)Math.Ceiling(h * 2), 192, 192, PixelFormats.Pbgra32);
        rtb.Render(v);
        rtb.Freeze();
        return rtb;
    }

    private static Color WithA(Color c, byte a) => Color.FromArgb(a, c.R, c.G, c.B);
    private static double Mod(double a, double m) => ((a % m) + m) % m;

    private static T Fz<T>(T f) where T : Freezable
    {
        f.Freeze();
        return f;
    }

    /// string.GetHashCode süreç başına rastgele; aynı kart her açılışta aynı görünsün diye sabit tohum.
    private static int StableSeed(string s)
    {
        unchecked
        {
            int h = 17;
            foreach (char ch in s) h = h * 31 + ch;
            return h;
        }
    }
}
