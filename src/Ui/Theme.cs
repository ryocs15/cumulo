using System.Text.RegularExpressions;
using System.Windows.Media;
using HavaDurumu.Core;

namespace HavaDurumu.Ui;

/// Kartın yazı renkleri (Python STYLE_DARK / STYLE_LIGHT / STYLE_CLEAR).
public sealed record CardStyle(Color Text, Color Muted, Color Faint, Color Track, Color Warn, bool LightIcons)
{
    public Brush TextBrush => Theme.Frozen(Text);
    public Brush MutedBrush => Theme.Frozen(Muted);
    public Brush FaintBrush => Theme.Frozen(Faint);
    public Brush WarnBrush => Theme.Frozen(Warn);
    public Brush BlueBrush => Theme.Frozen(LightIcons ? Color.FromRgb(30, 100, 190) : Color.FromRgb(120, 190, 255));
}

/// Arka plan paletleri ve tema renkleri — Python PALETTES, THEME_BG, palette_at, custom_colors.
public static class Theme
{
    public static readonly CardStyle Dark = new(
        Color.FromArgb(255, 255, 255, 255), Color.FromArgb(175, 255, 255, 255), Color.FromArgb(45, 255, 255, 255),
        Color.FromArgb(50, 255, 255, 255), Color.FromArgb(255, 255, 190, 170), LightIcons: false);

    public static readonly CardStyle Light = new(
        Color.FromArgb(255, 22, 28, 38), Color.FromArgb(160, 22, 28, 38), Color.FromArgb(40, 22, 28, 38),
        Color.FromArgb(32, 22, 28, 38), Color.FromArgb(255, 190, 60, 40), LightIcons: true);

    public static readonly CardStyle Clear = new(
        Color.FromArgb(255, 255, 255, 255), Color.FromArgb(228, 255, 255, 255), Color.FromArgb(110, 255, 255, 255),
        Color.FromArgb(110, 255, 255, 255), Color.FromArgb(255, 255, 200, 180), LightIcons: false);

    private static readonly Dictionary<string, (Color Top, Color Bot)> Palettes = new()
    {
        ["day"] = (Color.FromRgb(70, 140, 228), Color.FromRgb(30, 74, 152)),
        ["night"] = (Color.FromRgb(30, 40, 80), Color.FromRgb(10, 14, 34)),
        ["cloudy"] = (Color.FromRgb(96, 110, 132), Color.FromRgb(46, 54, 72)),
        ["rain"] = (Color.FromRgb(62, 82, 110), Color.FromRgb(26, 36, 54)),
        ["snow"] = (Color.FromRgb(112, 134, 164), Color.FromRgb(56, 70, 96)),
        ["storm"] = (Color.FromRgb(66, 52, 98), Color.FromRgb(22, 18, 40)),
        ["fog"] = (Color.FromRgb(122, 126, 134), Color.FromRgb(62, 66, 76)),
    };

    private static readonly Dictionary<string, (Color Top, Color Bot, byte Alpha)> ThemeBg = new()
    {
        ["dark"] = (Color.FromRgb(44, 48, 56), Color.FromRgb(22, 24, 29), 242),
        ["light"] = (Color.FromRgb(250, 251, 253), Color.FromRgb(230, 235, 242), 246),
        ["glass"] = (Color.FromRgb(34, 40, 54), Color.FromRgb(14, 18, 28), 140),
    };

    public static Brush Frozen(Color c)
    {
        var b = new SolidColorBrush(c);
        b.Freeze();
        return b;
    }

    public static string SceneGroup(string code)
    {
        if (code is "A" or "SCK") return "clear";
        if (code is "AB" or "PB") return "partly";
        if (code is "GSY" or "KGSY") return "storm";
        if (Constants.Snow.ContainsKey(code)) return "snow";
        if (Constants.Rain.ContainsKey(code) || code is "KKY" or "DY") return "rain";
        if (code is "SIS" or "PUS" or "DMN" or "KF") return "fog";
        return "cloudy";
    }

    public static (Color Top, Color Bot) PaletteFor(string code, bool night)
    {
        string g;
        if (code is "A" or "AB" or "PB" or "SCK") g = night ? "night" : "day";
        else if (Constants.Rain.ContainsKey(code) || code is "KKY" or "DY") g = "rain";
        else if (Constants.Snow.ContainsKey(code) || code == "SGK") g = "snow";
        else if (code is "GSY" or "KGSY") g = "storm";
        else if (code is "SIS" or "PUS" or "DMN" or "KF") g = "fog";
        else g = "cloudy";
        var (top, bot) = Palettes[g];
        if (night && g != "night")
        {
            top = Scale(top, 0.55);
            bot = Scale(bot, 0.6);
        }
        return (top, bot);
    }

    /// Gün doğumu/batımında gece ve gündüz renkleri karışır; alt kenara sıcak ufuk tonu eklenir.
    public static (Color Top, Color Bot) PaletteAt(string code, bool night, double dayf)
    {
        if (dayf <= 0 || dayf >= 1) return PaletteFor(code, night);
        var (nt, nb) = PaletteFor(code, true);
        var (dt, db) = PaletteFor(code, false);
        double tw = 1 - Math.Abs(2 * dayf - 1);
        var g = SceneGroup(code);
        double warm = 0.35 * tw * (g is "clear" or "partly" ? 1.0 : 0.4);
        return (Mix(nt, dt, dayf), Mix(Mix(nb, db, dayf), Color.FromRgb(235, 130, 80), warm));
    }

    public static Color HexRgb(string? s)
    {
        var m = Regex.Match(s ?? "", "^#?([0-9a-fA-F]{6})$");
        if (!m.Success) return Color.FromRgb(59, 110, 165);
        var h = m.Groups[1].Value;
        return Color.FromRgb(Convert.ToByte(h[..2], 16), Convert.ToByte(h[2..4], 16), Convert.ToByte(h[4..6], 16));
    }

    public static double Luminance(Color c) => (0.2126 * c.R + 0.7152 * c.G + 0.0722 * c.B) / 255;

    public static (Color Top, Color Bot) CustomColors(string? hex)
    {
        var c = HexRgb(hex);
        static byte Up(byte v) => (byte)Math.Min(255, (int)(v + (255 - v) * 0.12));
        return (Color.FromRgb(Up(c.R), Up(c.G), Up(c.B)), Scale(c, 0.55));
    }

    /// Temaya göre yazı stili (Python style()).
    public static CardStyle StyleFor(string theme, string? customHex)
    {
        if (theme == "light") return Light;
        if (theme == "clear") return Clear;
        if (theme == "custom" && Luminance(CustomColors(customHex).Top) > 0.62) return Light;
        return Dark;
    }

    /// Kart arka planı (Python compose_bg). "clear" temada saydam; opacity tüm zemine uygulanır.
    /// scene / image temaları bir kez bitmap'e çizilir (SceneBg); diğerleri dikey geçiş.
    public static Brush Background(string theme, string code, bool night, double dayf, string? customHex,
        double opacity, double w = 150, double h = 78, string? bgImage = null)
    {
        if (theme == "clear") return Brushes.Transparent;
        Color top, bot;
        byte alpha = 242;
        if (ThemeBg.TryGetValue(theme, out var tb)) (top, bot, alpha) = tb;
        else if (theme == "custom") (top, bot) = CustomColors(customHex);
        else (top, bot) = PaletteAt(code, night, dayf);   // auto ve diğerleri: havaya göre palet
        double a = Math.Clamp(alpha * opacity, 1, 255) / 255.0;

        Brush? bmp = theme switch
        {
            "scene" => SceneBg.Scene(w, h, code, night, dayf),
            "image" when !string.IsNullOrEmpty(bgImage) => SceneBg.Image(bgImage!, w, h),
            _ => null,
        };
        if (bmp is not null)
        {
            if (a >= 0.999) return bmp;
            var faded = bmp.CloneCurrentValue();
            faded.Opacity = a;
            faded.Freeze();
            return faded;
        }
        top.A = bot.A = (byte)(a * 255);
        var brush = new LinearGradientBrush(top, bot, 90);
        brush.Freeze();
        return brush;
    }

    /// Cam teması ya da yarı saydam zeminde ince açık kenar (Python compose_bg "edge").
    public static readonly Brush GlassEdge = Frozen(Color.FromArgb(70, 255, 255, 255));

    private static readonly (double T, Color C)[] TempStops =
    {
        (-10, Color.FromRgb(130, 170, 255)), (0, Color.FromRgb(110, 205, 255)), (10, Color.FromRgb(120, 225, 170)),
        (20, Color.FromRgb(255, 214, 90)), (30, Color.FromRgb(255, 145, 70)), (40, Color.FromRgb(240, 80, 80)),
    };

    public static Color TempColor(double t)
    {
        if (t <= TempStops[0].T) return TempStops[0].C;
        for (int i = 0; i < TempStops.Length - 1; i++)
        {
            var (t1, c1) = TempStops[i];
            var (t2, c2) = TempStops[i + 1];
            if (t <= t2) return Mix(c1, c2, (t - t1) / (t2 - t1));
        }
        return TempStops[^1].C;
    }

    public static Color Mix(Color a, Color b, double t) => Color.FromArgb(
        (byte)(a.A + (b.A - a.A) * t), (byte)(a.R + (b.R - a.R) * t),
        (byte)(a.G + (b.G - a.G) * t), (byte)(a.B + (b.B - a.B) * t));

    private static Color Scale(Color c, double k) => Color.FromRgb((byte)(c.R * k), (byte)(c.G * k), (byte)(c.B * k));
}
