using System.Text.Json.Nodes;
using System.Windows;
using System.Windows.Media;
using HavaDurumu.Core;
using HavaDurumu.Data;

namespace HavaDurumu.Ui;

/// Kart çiziminde kullanılan ayarlar (config.json'dan bir kez okunur).
public sealed record CardSettings(
    string Theme, string? CustomColor, double Opacity, double Radius, bool ColoredTemp,
    string MiniLine, bool Clock, bool AlertBorder, string HourlyMode, bool PagesOn,
    bool Effects = true, bool Animate = false, string? BgImage = null)
{
    public static CardSettings From(AppConfig c) => new(
        c.Get("theme", "auto"), c.Get<string?>("custom_color", null), c.Get("opacity", 1.0),
        c.Get("radius", 22.0), c.Get("colored_temp", false), c.Get("mini_line", "hl"),
        c.Get("clock", true), c.Get("alert_border", true), c.Get("hourly", "icons"),
        c.Get("mini_click", "expand") == "page" || c.Get("wheel_action", "city") == "page",
        c.Get("effects", true), c.Get("animate", false), c.Get<string?>("bg_image", null));
}

public sealed record DotItem(double Size, double Opacity, int Index, Brush Fill)
{
    public string Action => "city:" + Index;
}

public sealed record DetailItem(string Label, string Value, Brush ValueBrush, Brush? DotBrush, double Left, double Top)
{
    public Visibility DotVisibility => DotBrush is null ? Visibility.Collapsed : Visibility.Visible;
}

/// Saatlik sütun. Top: etiketin üst kenarı; IconSize ve TempGap düzene göre (Python koordinatları).
public sealed record HourItem(string Label, ImageSource Icon, string Temp, Brush TempBrush, string Prob,
    double Left, double ColW, double Top, double IconSize, double TempGap)
{
    public Visibility ProbVisibility => Prob.Length > 0 ? Visibility.Visible : Visibility.Collapsed;
    public Thickness IconMargin => new(0, 3, 0, 0);
    public Thickness TempMargin => new(0, TempGap, 0, 0);
}

/// Günlük satır (Tam düzen: 5 gün, Haftalık: 7 gün). Konumlar satırın sol üstüne göre değil, kartın
/// soluna göre mutlak; Top satırın üst kenarı (yc - 15).
public sealed record DayItem(string Name, ImageSource Icon, double IconLeft, string Min, double MinRight, string Max,
    double TrackLeft, double TrackWidth, double BarLeft, double BarWidth, Brush BarBrush, double Top,
    string Prob, Brush ProbBrush, FontWeight ProbWeight, Brush TextBrush, Brush MutedBrush, Brush TrackBrush)
{
    public double MinLeft => MinRight - 60;
    public double MaxLeft => TrackLeft + TrackWidth + 34 - 50;   // sağ kenar W - P
    public double ProbLeft => 186 - 50;
    public Visibility ProbVisibility => Prob.Length > 0 ? Visibility.Visible : Visibility.Collapsed;
}

/// Saatlik grafik noktası (hourly == "chart").
public sealed record ChartPoint(double X, double Y, string Temp, string Label, Brush Fill)
{
    public double DotLeft => X - 3;
    public double DotTop => Y - 3;
    public double TempLeft => X - 25;
    public double TempTop => Y - 8 - 13;
    public double LabelLeft => X - 25;
}

public sealed record SparkBar(double Left, double Top, double Height);

/// Bir şehrin kartı için hazır gösterim değerleri. XAML yalnızca bunlara bağlanır;
/// hesaplama burada bir kez yapılır (çizim sırasında iş yok).
public sealed class CardModel
{
    public const double Pad = 20;

    // ---- ortak
    public Brush Background { get; init; } = Brushes.Transparent;
    public System.Windows.Media.Effects.Effect? ContentEffect { get; init; }
    public CornerRadius Radius { get; init; }
    public Brush AccentBrush { get; init; } = Brushes.Transparent;
    public Thickness AccentThickness { get; init; }
    public Brush TextBrush { get; init; } = Brushes.White;
    public Brush MutedBrush { get; init; } = Brushes.White;
    public Brush FaintBrush { get; init; } = Brushes.White;
    public Brush BlueBrush { get; init; } = Brushes.White;
    public Brush StatusBrush { get; init; } = Brushes.White;
    public double Width { get; init; }
    public double Height { get; init; }

    public bool HasData { get; init; }
    public Visibility DataVisibility => HasData ? Visibility.Visible : Visibility.Collapsed;
    public Visibility NoDataVisibility => HasData ? Visibility.Collapsed : Visibility.Visible;
    public string StatusText { get; init; } = "";

    public ImageSource? Icon { get; init; }

    // ---- animasyon (CardAnimator)
    public string IconCode { get; init; } = "CB";
    public bool IconNight { get; init; }
    public bool IconLight { get; init; }
    public bool IconAnimated { get; set; }
    public string? FxKind { get; set; }
    public int FxLevel { get; init; } = 3;
    public Point SunCenter { get; init; }
    public double SunR { get; init; }

    public string TempText { get; init; } = "";
    public Brush TempBrush { get; init; } = Brushes.White;
    public string City { get; init; } = "";
    public string Line2 { get; init; } = "";

    public Brush? AlertBrush { get; init; }
    public Visibility AlertVisibility => AlertBrush is null ? Visibility.Collapsed : Visibility.Visible;
    public string AlertLabel { get; init; } = "";

    public IReadOnlyList<DotItem> Dots { get; init; } = Array.Empty<DotItem>();
    public bool ShowRefresh { get; init; } = true;
    public Visibility RefreshVisibility => ShowRefresh ? Visibility.Visible : Visibility.Collapsed;

    // ---- küçük kartlar: sayfalar ve Mini+ grafiği
    public int Page { get; set; }
    public bool PagesOn { get; set; }
    public Visibility Page0Visibility => Page == 0 && HasData ? Visibility.Visible : Visibility.Collapsed;
    public Visibility Page1Visibility => Page == 1 && HasData ? Visibility.Visible : Visibility.Collapsed;
    public Visibility Page2Visibility => Page == 2 && HasData ? Visibility.Visible : Visibility.Collapsed;
    public Visibility PageDotsVisibility => PagesOn ? Visibility.Visible : Visibility.Collapsed;
    public Visibility SmallAlertVisibility => Page == 0 ? AlertVisibility : Visibility.Collapsed;
    public IReadOnlyList<double> PageDotOpacity => new[] { Page == 0 ? 1.0 : 0.35, Page == 1 ? 1.0 : 0.35, Page == 2 ? 1.0 : 0.35 };
    public double CityDotsMaxWidth => PagesOn ? Width - 30 - 32 : Width - 30 - 8;
    /// Alt satır (noktalar): Python'da merkez H - 8, 12 px yüksek kutu.
    public double BottomTop => Height - 14;
    public IReadOnlyList<HourItem> PageHours { get; set; } = Array.Empty<HourItem>();
    public string PageHoursEmpty { get; set; } = "";
    public ImageSource? TomorrowIcon { get; set; }
    public string TomorrowHiLo { get; set; } = "";
    public Brush TomorrowBrush { get; set; } = Brushes.White;
    public string TomorrowProb { get; set; } = "";
    public string TomorrowEmpty { get; set; } = "";

    public bool ShowSpark { get; set; }
    public Visibility SparkVisibility => ShowSpark && HasData ? Visibility.Visible : Visibility.Collapsed;
    public PointCollection SparkLine { get; set; } = new();
    public PointCollection SparkFill { get; set; } = new();
    public IReadOnlyList<SparkBar> SparkBars { get; set; } = Array.Empty<SparkBar>();
    public Brush SparkLineBrush { get; set; } = Brushes.White;
    public Brush SparkFillBrush { get; set; } = Brushes.Transparent;
    public string SparkFirst { get; set; } = "";
    public Point SparkFirstAt { get; set; }
    public string SparkLast { get; set; } = "";
    public double SparkLastTop { get; set; }
    public string SparkEmpty { get; set; } = "";

    // ---- büyük kartlar (kompakt / ayrıntılı / orta / tam / haftalık)
    public string Title { get; init; } = "";
    public string ClockText { get; init; } = "";
    public Visibility ClockVisibility => ClockText.Length > 0 ? Visibility.Visible : Visibility.Collapsed;
    public double TitleWidth => Width - 2 * Pad - 22;
    public double RefreshLeft => Width - Pad - 15;
    public double LineWidth => Width - 2 * Pad;
    public string HiLo { get; init; } = "";
    public string Feels { get; init; } = "";
    public string Condition { get; init; } = "";
    public string Yesterday { get; init; } = "";

    /// Durum satırının genişliği: uyarı etiketi varsa ona yer bırakılır.
    public double ConditionMaxWidth => AlertBrush is null
        ? LineWidth
        : Math.Max(120, LineWidth - (AlertLabel.Length * 6.6 + 16) - 10);

    public IReadOnlyList<double> Lines { get; set; } = Array.Empty<double>();
    public IReadOnlyList<DetailItem> Items { get; set; } = Array.Empty<DetailItem>();
    public double ItemWidth { get; set; } = 75;
    public IReadOnlyList<HourItem> Hours { get; set; } = Array.Empty<HourItem>();
    public string NoHoursText { get; set; } = "";
    public double NoHoursTop { get; set; } = 296;

    public bool ShowChart { get; set; }
    public Visibility ChartVisibility => ShowChart ? Visibility.Visible : Visibility.Collapsed;
    public PointCollection ChartLine { get; set; } = new();
    public PointCollection ChartFill { get; set; } = new();
    public IReadOnlyList<ChartPoint> ChartPoints { get; set; } = Array.Empty<ChartPoint>();
    public Brush ChartLineBrush { get; set; } = Brushes.White;
    public Brush ChartFillBrush { get; set; } = Brushes.Transparent;

    public IReadOnlyList<DayItem> Days { get; set; } = Array.Empty<DayItem>();

    public string FooterText { get; set; } = "";
    public Brush FooterBrush { get; set; } = Brushes.White;
    public FontWeight FooterWeight { get; set; } = FontWeights.Normal;
    public double FooterTop => Height - 14 - 12;
    public double DotsTop => Height - 18 - 6;

    // haftalık düzen
    public string WeekEmpty { get; set; } = "";
    public string TrendTitle { get; set; } = "";
    public string TrendText { get; set; } = "";
    public Visibility WeekTrendVisibility => TrendTitle.Length > 0 ? Visibility.Visible : Visibility.Collapsed;
}

public static class CardBuilder
{
    /// layout: micro, mini, miniplus, vmini, compact, peek, medium, full, weekly.
    /// multi: çoklu düzende (row/col) kartın sırası; null = tek kart.
    public static CardModel Build(
        string layout, Location loc, WeatherData? data, string? error, JsonArray alertsAll,
        CardSettings s, int cityIndex, int cityCount, DateTime nowUtc, int page = 0, int? multi = null)
    {
        var (w, h) = Layouts.Size(layout);
        var cur = data?.Cur;
        string code = cur is null ? "CB" : J.Str(cur["hadiseKodu"]);
        if (code.Length == 0) code = "CB";
        bool night = Sun.IsNight(loc.Lat, loc.Lon, nowUtc);
        double dayf = Sun.DayFactor(loc.Lat, loc.Lon, nowUtc);
        var st = Theme.StyleFor(s.Theme, s.CustomColor);
        var extra = data?.Extra ?? new JsonObject();
        var offset = data?.UtcOffset;
        var days = data?.DailyRows() ?? new();
        var localToday = DateOnly.FromDateTime(Fmt.NowLocal(offset));
        (DateOnly Day, string Code, double Lo, double Hi)? today =
            days.Count > 0 && days[0].Day == localToday ? days[0] : null;
        var alerts = AlertService.AlertsFor(loc, alertsAll, nowUtc);

        bool small = Layouts.IsSmall(layout);
        double baseRadius = layout == "micro" ? 7 : small ? 9 : 22;
        double radius = baseRadius * s.Radius / 22.0;

        Brush accent = Brushes.Transparent;
        double accentW = 0;
        Brush? alertBrush = null;
        string alertLabel = "";
        if (alerts.Count > 0)
        {
            alertBrush = Theme.Frozen(AlertRgb(alerts[0].Color));
            alertLabel = T("{} uyarı").F(alerts[0].ColorName) + (alerts[0].Kinds.Count > 0 ? " · " + alerts[0].Kinds[0] : "");
            if (s.AlertBorder)
            {
                accent = alertBrush;
                accentW = 2;
            }
        }
        if (accentW == 0 && s.Theme != "clear" && (s.Theme == "glass" || s.Opacity < 1))
        {
            accent = Theme.GlassEdge;   // cam / yarı saydam zeminde ince açık kenar
            accentW = 1;
        }

        double? tv = J.Num(cur?["sicaklik"]);
        Brush TempFill(double? t)
        {
            if (!s.ColoredTemp || !Fmt.Ok(t)) return st.TextBrush;
            var c = Theme.TempColor(t!.Value);
            if (st.LightIcons) c = Theme.Mix(c, Colors.Black, 0.38);
            return Theme.Frozen(c);
        }

        var dots = new List<DotItem>();
        int n = Math.Min(cityCount, 12);
        if (n > 1 && multi is null)
            for (int i = 0; i < n; i++)
                dots.Add(new DotItem(i == cityIndex ? 6 : 4.4, i == cityIndex ? 1.0 : 0.45, i, st.TextBrush));

        bool pagesOn = s.PagesOn && multi is null && layout is "mini" or "miniplus";
        // Ana ikonun yeri ve boyutu (Python isz / ipos) — güneş parıltısı ikonun arkasında durur.
        var (ix, iy, isz) = layout switch
        {
            "mini" or "miniplus" => (3.0, 6.0, 58.0),
            "micro" => (3.0, 4.0, 36.0),
            "vmini" => ((w - 54) / 2, 6.0, 54.0),
            "weekly" => (18.0, 50.0, 44.0),
            _ => (12.0, 42.0, 88.0),
        };
        var model = new CardModel
        {
            IconCode = code,
            IconNight = night,
            IconLight = st.LightIcons,
            FxLevel = Constants.Rain.TryGetValue(code, out var lvl) ? lvl : 3,
            SunCenter = new Point(ix + isz / 2, iy + isz / 2),
            SunR = isz * 0.75,
            Width = w,
            Height = h,
            Background = Theme.Background(s.Theme, code, night, dayf, s.CustomColor, s.Opacity, w, h, s.BgImage),
            ContentEffect = s.Theme == "clear" ? ClearShadow(s.Opacity) : null,
            Radius = new CornerRadius(radius),
            AccentBrush = accent,
            AccentThickness = new Thickness(accentW),
            TextBrush = st.TextBrush,
            MutedBrush = st.MutedBrush,
            FaintBrush = st.FaintBrush,
            BlueBrush = st.BlueBrush,
            StatusBrush = error is null ? st.MutedBrush : st.WarnBrush,
            StatusText = error ?? T("Yükleniyor…"),
            HasData = cur is not null,
            Icon = WeatherIcons.Get(code, night, st.LightIcons),
            TempText = Fmt.Temp(tv),
            TempBrush = TempFill(tv),
            City = Fmt.LocShort(loc),
            Line2 = cur is null ? "" : MiniLine(s.MiniLine, cur, today, extra, offset, loc, code, nowUtc) ?? "",
            AlertBrush = alertBrush,
            AlertLabel = alertLabel,
            Dots = dots,
            ShowRefresh = multi is null or 0,
            Title = Fmt.LocTitle(loc),
            ClockText = s.Clock && !small ? Fmt.ClockText(offset) : "",
            HiLo = today is { } td ? $"↑ {Fmt.Tint(td.Hi)}°   ↓ {Fmt.Tint(td.Lo)}°" : "",
            Feels = Fmt.Ok(J.Num(cur?["hissedilenSicaklik"])) ? T("Hissedilen {}").F(Fmt.Temp(J.Num(cur?["hissedilenSicaklik"]))) : "",
            Condition = Fmt.Hadise(code),
            Yesterday = J.Num(extra["yesterday_diff"]) is double yd ? "  ·  " + Fmt.YesterdayText(yd) : "",
            Page = pagesOn ? page : 0,
            PagesOn = pagesOn,
        };

        if (cur is null) return model;

        // Efekt ve ikon animasyonu (Python: effects açık ve tema "clear" değilse; ikon yalnızca ilk sayfada).
        if (s.Effects && s.Theme != "clear") model.FxKind = AmbientFx.KindFor(code);
        model.IconAnimated = s.Animate && model.Page == 0 && WeatherIcons.IsAnimated(code, night);

        if (layout is "mini" or "miniplus")
        {
            if (model.Page == 1) MiniHours(model, extra, loc, offset, st, TempFill);
            if (model.Page == 2) MiniTomorrow(model, days, extra, localToday, st, TempFill);
            if (layout == "miniplus") Sparkline(model, extra, st);
        }
        else if (layout == "peek")
        {
            model.Lines = new[] { 166.0, 256.0 };
            model.Items = PeekItems(cur, extra, code, loc, nowUtc, offset, st);
            model.Hours = PeekHours(extra, data!, loc, nowUtc, offset, st, TempFill, out var noHours);
            model.NoHoursText = noHours;
            Footer(model, data!, error, offset, st);
        }
        else if (layout is "compact")
        {
            Footer(model, data!, error, offset, st);
        }
        else if (layout is "medium" or "full")
        {
            model.Lines = layout == "full" ? new[] { 302.0, 398.0 } : new[] { 302.0 };
            model.ItemWidth = 80;
            model.Items = MediumItems(cur, extra, loc, nowUtc, offset, st);
            MediumHours(model, data!, loc, nowUtc, offset, st, s.HourlyMode == "chart");
            if (layout == "full") model.Days = FullDays(days, localToday, st, w);
            Footer(model, data!, error, offset, st);
        }
        else if (layout == "weekly")
        {
            Weekly(model, extra, localToday, st, w, data!.Src == "om");
            model.Lines = new[] { 104.0 };
            var src = data!.Src == "om" ? T("Kaynak: Open-Meteo") : T("Kaynak: MGM (5 günlük tahmin)");
            model.FooterText = error ?? src;
            model.FooterBrush = error is null ? st.MutedBrush : st.WarnBrush;
            model.FooterWeight = error is null ? FontWeights.Normal : FontWeights.SemiBold;
        }
        return model;
    }

    /// Saydam tema: zemin yok; yazı ve ikonların çevresinde koyu yumuşak gölge (Python shadow_alpha).
    /// "Opaklık" bu temada gölgenin koyuluğunu ayarlar.
    private static System.Windows.Media.Effects.Effect ClearShadow(double op)
    {
        var e = new System.Windows.Media.Effects.DropShadowEffect
        {
            Color = Color.FromRgb(8, 12, 20), ShadowDepth = 0, BlurRadius = 6, Opacity = Math.Clamp(0.55 + 0.45 * op, 0, 1),
        };
        e.Freeze();
        return e;
    }

    private static Color AlertRgb(string color) => color switch
    {
        "red" => Color.FromRgb(240, 70, 70),
        "orange" => Color.FromRgb(255, 150, 50),
        _ => Color.FromRgb(250, 204, 50),
    };

    private static Brush WithAlpha(Color c, byte a) => Theme.Frozen(Color.FromArgb(a, c.R, c.G, c.B));

    /// Mini kartın ikinci satırı (Python mini_line).
    private static string? MiniLine(string kind, JsonObject cur, (DateOnly Day, string Code, double Lo, double Hi)? today,
        JsonObject extra, TimeSpan? offset, Location loc, string code, DateTime nowUtc)
    {
        double nowUnix = (nowUtc - DateTime.UnixEpoch).TotalSeconds;
        if (kind == "rain_eta" && Fmt.RainEta(extra, code, nowUnix) is { } r) return r;
        if (kind == "sun" && Sun.Eta(loc.Lat, loc.Lon, nowUtc, offset) is { } sn) return sn;
        if (kind == "clock") return Fmt.NowLocal(offset).ToString("HH:mm");
        var feels = J.Num(cur["hissedilenSicaklik"]);
        if (kind == "feels" && Fmt.Ok(feels)) return T("Hiss. {}").F(Fmt.Temp(feels));
        var rz = J.Num(cur["ruzgarHiz"]);
        if (kind == "wind" && Fmt.Ok(rz)) return Fmt.WindText(rz, J.Num(cur["ruzgarYon"]));
        var nem = J.Num(cur["nem"]);
        if (kind == "humidity" && Fmt.Ok(nem)) return T("Nem %{}").F(Fmt.N0(nem!.Value));
        if (kind == "aqi" && J.Num(extra["european_aqi"]) is double aqi)
            return T("HK {:.0f} · {}").F(aqi, Fmt.AqiLevel(aqi).Name);
        if (kind == "rain" && J.Num(extra["rain_prob"]) is double rp) return T("Yağış %{:.0f}").F(rp);
        if (kind == "uv" && J.Num(extra["uv_now"]) is double uv) return T("UV {:.0f} · {}").F(uv, Fmt.UvLevel(uv));
        return today is { } t ? $"{Fmt.Tint(t.Hi)}° / {Fmt.Tint(t.Lo)}°" : null;
    }

    private static List<(DateTime Utc, double Temp, string Code, double? Prob)> Temps12(JsonObject extra)
    {
        var rows = new List<(DateTime, double, string, double?)>();
        foreach (var node in J.Arr(extra["temps12"]))
        {
            var r = J.Arr(node);
            if (r.Count < 3 || J.Num(r[0]) is not double ts || J.Num(r[1]) is not double temp) continue;
            rows.Add((DateTime.UnixEpoch.AddSeconds(ts), temp, J.Str(r[2]), r.Count > 3 ? J.Num(r[3]) : null));
        }
        return rows;
    }

    // ---------------------------------------------------------------- mini sayfaları
    /// Sayfa 1: önümüzdeki saatlerden üçü (1., 3. ve 6. satır).
    private static void MiniHours(CardModel m, JsonObject extra, Location loc, TimeSpan? offset, CardStyle st,
        Func<double?, Brush> tempFill)
    {
        var rows = Temps12(extra);
        var picks = new[] { 1, 3, 6 }.Where(i => i < rows.Count).Select(i => rows[i]).ToList();
        m.PageHoursEmpty = picks.Count == 0 ? T("Saatlik veri yok") : "";
        m.PageHours = picks.Select((r, i) => new HourItem(
            Fmt.LocalTime(r.Utc, offset).ToString("HH:mm"),
            WeatherIcons.Get(r.Code, Sun.IsNight(loc.Lat, loc.Lon, r.Utc), st.LightIcons),
            $"{Fmt.Tint(r.Temp)}°", tempFill(r.Temp), "", i * 50, 50, 3, 28, 0)).ToList();
    }

    /// Sayfa 2: yarın.
    private static void MiniTomorrow(CardModel m, List<(DateOnly Day, string Code, double Lo, double Hi)> days,
        JsonObject extra, DateOnly localToday, CardStyle st, Func<double?, Brush> tempFill)
    {
        var tomorrow = localToday.AddDays(1);
        (string Code, double Lo, double Hi)? row = days.Where(d => d.Day == tomorrow)
            .Select(d => ((string, double, double)?)(d.Code, d.Lo, d.Hi)).FirstOrDefault();
        JsonObject? d16 = J.Arr(extra["daily16"]).OfType<JsonObject>()
            .FirstOrDefault(o => J.Str(o["d"]) == tomorrow.ToString("yyyy-MM-dd"));
        if (row is null && d16 is not null && J.Num(d16["lo"]) is double lo && J.Num(d16["hi"]) is double hi)
            row = (J.Str(d16["code"]), lo, hi);
        if (row is not { } r)
        {
            m.TomorrowEmpty = T("Yarın verisi yok");
            return;
        }
        m.TomorrowIcon = WeatherIcons.Get(r.Code, false, st.LightIcons);
        m.TomorrowHiLo = $"{Fmt.Tint(r.Hi)}° / {Fmt.Tint(r.Lo)}°";
        m.TomorrowBrush = tempFill(r.Hi);
        if (d16 is not null && J.Num(d16["prob"]) is double p) m.TomorrowProb = T("Yağış %{:.0f}").F(p);
    }

    /// Mini+: önümüzdeki 12 saatin sıcaklık eğrisi, yağış olasılığı çubuklarla (Python draw_sparkline).
    private static void Sparkline(CardModel m, JsonObject extra, CardStyle st)
    {
        m.ShowSpark = true;
        const double x1 = 12, y1 = 70, x2 = 138, y2 = 97;
        var rows = Temps12(extra).Take(13).ToList();
        if (rows.Count < 2)
        {
            m.SparkEmpty = T("Saatlik veri yok");
            return;
        }
        double lo = rows.Min(r => r.Temp), hi = rows.Max(r => r.Temp), span = Math.Max(1.0, hi - lo);
        double top = y1 + 9, bot = y2 - 2;
        int n = rows.Count;
        var xs = Enumerable.Range(0, n).Select(i => x1 + (x2 - x1) * i / (n - 1)).ToArray();
        var ys = rows.Select(r => bot - (bot - top) * (r.Temp - lo) / span).ToArray();
        var line = new PointCollection(xs.Zip(ys, (x, y) => new Point(x, y)));
        var fill = new PointCollection(line) { new Point(xs[^1], y2), new Point(xs[0], y2) };
        line.Freeze();
        fill.Freeze();
        m.SparkLine = line;
        m.SparkFill = fill;
        var bars = new List<SparkBar>();
        for (int i = 0; i < n; i++)
            if (rows[i].Prob is double p && p >= 20)
            {
                double bh = (y2 - y1) * 0.55 * p / 100;
                bars.Add(new SparkBar(xs[i] - 1.4, y2 - bh, bh));
            }
        m.SparkBars = bars;
        m.SparkLineBrush = WithAlpha(st.Text, 225);
        m.SparkFillBrush = WithAlpha(st.Text, 26);
        m.SparkFirst = $"{Fmt.Tint(rows[0].Temp)}°";
        m.SparkFirstAt = new Point(xs[0], ys[0] - 3 - 10);
        m.SparkLast = T("+{} sa  {}°").F(n - 1, Fmt.Tint(rows[^1].Temp));
        m.SparkLastTop = ys[^1] - 3 - 10;
    }

    // ---------------------------------------------------------------- büyütülmüş mini (peek)
    private static List<DetailItem> PeekItems(JsonObject cur, JsonObject extra, string code, Location loc,
        DateTime nowUtc, TimeSpan? offset, CardStyle st)
    {
        double nowUnix = (nowUtc - DateTime.UnixEpoch).TotalSeconds;
        var nem = J.Num(cur["nem"]);
        var bas = Pressure(cur);
        var sun = Sun.Times(loc.Lat, loc.Lon, nowUtc, offset);
        var aqi = J.Num(extra["european_aqi"]);
        var uvn = J.Num(extra["uv_now"]);
        var rp = J.Num(extra["rain_prob"]);
        var eta = Fmt.RainEta(extra, code, nowUnix) ?? "";

        (string, string, Brush?, Brush?) rainItem;
        if (eta.StartsWith('☂'))
        {
            var v = eta[1..].Trim();
            rainItem = v.EndsWith(T(" sonra"))
                ? (T("Yağışa kalan"), v[..^T(" sonra").Length], null, st.BlueBrush)
                : (T("Yağış sürüyor"), v, null, st.BlueBrush);
        }
        else rainItem = (T("Yağış ihtimali"), rp is double p ? Pct(p) : "–", null, null);

        var raw = new List<(string Label, string Value, Brush? Dot, Brush? Col)>
        {
            (T("Nem"), Fmt.Ok(nem) ? Pct(nem!.Value) : "–", null, null),
            (T("Rüzgar"), Fmt.WindText(J.Num(cur["ruzgarHiz"]), J.Num(cur["ruzgarYon"])), null, null),
            rainItem,
            (T("UV indeksi"), uvn is double u ? $"{Fmt.N0(u)} · {Fmt.UvLevel(u)}" : "–", null, null),
            (T("Gün doğumu"), sun?.Rise ?? "–", null, null),
            (T("Gün batımı"), sun?.Set ?? "–", null, null),
            (T("Hava kalitesi"), aqi is double a ? $"{Fmt.N0(a)} · {Fmt.AqiLevel(a).Name}" : "–", AqiDot(aqi), null),
            (T("Basınç"), Fmt.Ok(bas) ? Fmt.N0(bas!.Value) + " hPa" : "–", null, null),
        };
        // MGM'de olmayan (Open-Meteo) değerler boşsa yerine MGM ölçümleri (Python ile aynı sıra)
        var yg = J.Num(cur["yagis24Saat"]);
        var kap = J.Num(cur["kapalilik"]);
        var (moonShort, illum) = Fmt.MoonInfo(nowUtc);
        var spare = new Queue<(string, string, Brush?, Brush?)>(new[]
        {
            (T("Yağış 24s"), Fmt.Ok(yg) ? Fmt.G(yg!.Value) + " mm" : "–", (Brush?)null, (Brush?)null),
            (T("Görüş"), VisText(J.Num(cur["gorus"])), null, null),
            Fmt.Ok(kap) ? (T("Bulutluluk"), $"{Fmt.N0(kap!.Value)}/8", null, null) : (T("Ay · %{:.0f}").F(illum), moonShort, null, null),
        });
        var optional = new[] { T("Yağış ihtimali"), T("UV indeksi"), T("Hava kalitesi") };
        raw = raw.Select(it => it.Value == "–" && optional.Contains(it.Label) && spare.Count > 0 ? spare.Dequeue() : it).ToList();
        return Grid4(raw, 340, 186, 42, st, shortenLevels: true);
    }

    private static string VisText(double? vis) =>
        !Fmt.Ok(vis) ? "–" : vis >= 1000 ? Fmt.N0(vis!.Value / 1000) + " km" : Fmt.N0(vis!.Value) + " m";

    private static double? Pressure(JsonObject cur)
    {
        var bas = J.Num(cur["denizeIndirgenmisBasinc"]);
        return Fmt.Ok(bas) ? bas : J.Num(cur["aktuelBasinc"]);
    }

    private static Brush? AqiDot(double? aqi)
    {
        if (aqi is not double a) return null;
        var c = Fmt.AqiLevel(a).Color;
        return Theme.Frozen(Color.FromRgb(c.R, c.G, c.B));
    }

    /// 4 sütunlu ayrıntı ızgarası. baseline: ilk satır etiketinin taban çizgisi (Python y).
    private static List<DetailItem> Grid4(List<(string Label, string Value, Brush? Dot, Brush? Col)> raw,
        double width, double baseline, double rowStep, CardStyle st, bool shortenLevels)
    {
        double cw = (width - 2 * CardModel.Pad) / 4;
        var items = new List<DetailItem>();
        for (int i = 0; i < raw.Count; i++)
        {
            var (lab, val, dot, col) = raw[i];
            // Dar sütuna sığmazsa yalnızca seviye: "Makul"
            if (shortenLevels && val.Contains(" · ") && val.Length > 9) val = val.Split(" · ", 2)[1];
            items.Add(new DetailItem(lab, val, col ?? st.TextBrush, dot,
                CardModel.Pad + (i % 4) * cw, baseline + (i / 4) * rowStep - 12));
        }
        return items;
    }

    /// Önümüzdeki 12 saat (2 saatte bir, 7 sütun). Open-Meteo yoksa MGM saatlik tahmini.
    private static List<HourItem> PeekHours(JsonObject extra, WeatherData data, Location loc, DateTime nowUtc,
        TimeSpan? offset, CardStyle st, Func<double?, Brush> tempFill, out string noHours)
    {
        var t12 = Temps12(extra);
        int step = t12.Count > 1 && (t12[1].Utc - t12[0].Utc).TotalHours < 2 ? 2 : 1;   // MGM zaten 3 saatlik
        var rows = t12.Where((_, i) => i % step == 0).Take(7)
            .Select(r => (r.Utc, r.Code, r.Temp, r.Prob)).ToList();
        if (rows.Count == 0)
            rows = data.HourlyRows(nowUtc).Select(x => (x.Utc, x.Code, x.Temp, (double?)null)).ToList();

        noHours = rows.Count == 0 ? T("Saatlik tahmin yok") : "";
        var list = new List<HourItem>();
        if (rows.Count == 0) return list;
        double colw = (340 - 2 * CardModel.Pad) / rows.Count;
        for (int i = 0; i < rows.Count; i++)
        {
            var (utc, code, temp, prob) = rows[i];
            bool now = i == 0 && utc <= nowUtc.AddMinutes(30);
            string lab = now ? T("Şimdi") : Fmt.LocalTime(utc, offset).ToString("HH:mm");
            bool hn = Sun.IsNight(loc.Lat, loc.Lon, now ? nowUtc : utc);
            // "Şimdi" saatlik tahmin değil, üstteki anlık gözlemle aynı ikonu göstersin
            var ic = now && J.Str(data.Cur?["hadiseKodu"]) is { Length: > 0 } cc ? cc : code;
            list.Add(new HourItem(lab, WeatherIcons.Get(ic, hn, st.LightIcons), $"{Fmt.Tint(temp)}°",
                tempFill(temp), prob is double p && p >= 20 ? Pct(p) : "",
                CardModel.Pad + colw * i, colw, 264, 28, 2));
        }
        return list;
    }

    // ---------------------------------------------------------------- orta / tam
    private static List<DetailItem> MediumItems(JsonObject cur, JsonObject extra, Location loc, DateTime nowUtc,
        TimeSpan? offset, CardStyle st)
    {
        var nem = J.Num(cur["nem"]);
        var bas = Pressure(cur);
        var yg = J.Num(cur["yagis24Saat"]);
        var vis = J.Num(cur["gorus"]);
        var deniz = J.Num(cur["denizSicaklik"]);
        var kar = J.Num(cur["karYukseklik"]);
        var kap = J.Num(cur["kapalilik"]);
        var sun = Sun.Times(loc.Lat, loc.Lon, nowUtc, offset);
        var pol = Fmt.PollenSummary(extra["pollen"] as JsonObject);
        (string, string, Brush?, Brush?) slot4 =
            Fmt.Ok(deniz) ? (T("Deniz suyu"), Fmt.Temp(deniz), null, null)
            : Fmt.Ok(kar) && kar > 0 ? (T("Kar kalınlığı"), Fmt.G(kar!.Value) + " cm", null, null)
            : pol is { } pl ? (T("Polen"), $"{pl.Kind} · {pl.Level}", null, null)
            : Fmt.Ok(kap) ? (T("Bulutluluk"), $"{Fmt.N0(kap!.Value)}/8", null, null)
            : (T("Bulutluluk"), "–", null, null);
        var (moonShort, illum) = Fmt.MoonInfo(nowUtc);
        var aqi = J.Num(extra["european_aqi"]);
        var uvn = J.Num(extra["uv_now"]);
        var rp = J.Num(extra["rain_prob"]);
        string visText = VisText(vis);

        var raw = new List<(string Label, string Value, Brush? Dot, Brush? Col)>
        {
            (T("Nem"), Fmt.Ok(nem) ? Pct(nem!.Value) : "–", null, null),
            (T("Rüzgar"), Fmt.WindText(J.Num(cur["ruzgarHiz"]), J.Num(cur["ruzgarYon"])), null, null),
            (T("Basınç"), Fmt.Ok(bas) ? Fmt.N0(bas!.Value) + " hPa" : "–", null, null),
            (T("Yağış 24s"), Fmt.Ok(yg) ? Fmt.G(yg!.Value) + " mm" : "–", null, null),
            (T("Görüş"), visText, null, null),
            (T("Gün doğumu"), sun?.Rise ?? "–", null, null),
            (T("Gün batımı"), sun?.Set ?? "–", null, null),
            (T("Ay · %{:.0f}").F(illum), moonShort, null, null),
            (T("Hava kalitesi"), aqi is double a ? $"{Fmt.N0(a)} · {Fmt.AqiLevel(a).Name}" : "–", AqiDot(aqi), null),
            (T("UV indeksi"), uvn is double u ? $"{Fmt.N0(u)} · {Fmt.UvLevel(u)}" : "–", null, null),
            (T("Yağış ihtimali"), rp is double p ? Pct(p) : "–", null, null),
            slot4,
        };
        // MGM konumlarında olmayan (Open-Meteo) değerler "–" yazmasın, hiç gösterilmesin
        var optional = new[] { T("Hava kalitesi"), T("UV indeksi"), T("Yağış ihtimali"), T("Bulutluluk") };
        raw = raw.Where(it => it.Value != "–" || !optional.Contains(it.Label)).ToList();
        return Grid4(raw, 360, 178, 44, st, shortenLevels: false);
    }

    /// Orta/Tam düzenin saatlik satırı: ikonlar ya da grafik (config "hourly").
    private static void MediumHours(CardModel m, WeatherData data, Location loc, DateTime nowUtc, TimeSpan? offset,
        CardStyle st, bool chart)
    {
        var hours = data.HourlyRows(nowUtc);
        if (hours.Count == 0)
        {
            m.NoHoursText = T("Saatlik tahmin yok");
            m.NoHoursTop = 345;
            return;
        }
        const double W = 360;
        int n = hours.Count;
        double colw = (W - 2 * CardModel.Pad) / n;
        var xs = Enumerable.Range(0, n).Select(i => CardModel.Pad + colw * i + colw / 2).ToArray();
        var labels = hours.Select((h, i) => i == 0 && h.Utc <= nowUtc ? T("Şimdi") : Fmt.LocalTime(h.Utc, offset).ToString("HH:mm")).ToArray();

        if (!chart)
        {
            var curCode = J.Str(data.Cur?["hadiseKodu"]);
            m.Hours = hours.Select((h, i) => new HourItem(labels[i],
                i == 0 && h.Utc <= nowUtc && curCode.Length > 0   // "Şimdi": anlık gözlemin ikonu
                    ? WeatherIcons.Get(curCode, Sun.IsNight(loc.Lat, loc.Lon, nowUtc), st.LightIcons)
                    : WeatherIcons.Get(h.Code, Sun.IsNight(loc.Lat, loc.Lon, h.Utc), st.LightIcons),
                $"{Fmt.Tint(h.Temp)}°", st.TextBrush, "", CardModel.Pad + colw * i, colw, 312, 30, 4)).ToList();
            return;
        }

        m.ShowChart = true;
        double lo = hours.Min(h => h.Temp), hi = hours.Max(h => h.Temp), span = Math.Max(1.0, hi - lo);
        var ys = hours.Select(h => 370 - (h.Temp - lo) / span * 26).ToArray();
        var line = new PointCollection(xs.Zip(ys, (x, y) => new Point(x, y)));
        var fill = new PointCollection(line) { new Point(xs[^1], 378), new Point(xs[0], 378) };
        line.Freeze();
        fill.Freeze();
        m.ChartLine = line;
        m.ChartFill = fill;
        m.ChartLineBrush = WithAlpha(st.Text, 230);
        m.ChartFillBrush = WithAlpha(st.Text, 26);
        m.ChartPoints = hours.Select((h, i) => new ChartPoint(xs[i], ys[i], $"{Fmt.Tint(h.Temp)}°", labels[i],
            Theme.Frozen(Theme.TempColor(h.Temp)))).ToList();
    }

    private static List<DayItem> FullDays(List<(DateOnly Day, string Code, double Lo, double Hi)> days,
        DateOnly localToday, CardStyle st, double w)
    {
        if (days.Count == 0) return new();
        double lo = days.Min(d => d.Lo), span = Math.Max(1, days.Max(d => d.Hi) - lo);
        double bx1 = w - CardModel.Pad - 130, bx2 = w - CardModel.Pad - 34;
        return days.Select((d, i) => DayRow(d.Day, d.Code, d.Lo, d.Hi, null, 404 + i * 30 + 15, localToday,
            lo, span, bx1, bx2, 118, bx1 - 10, w, st)).ToList();
    }

    private static DayItem DayRow(DateOnly day, string code, double tmin, double tmax, double? prob, double yc,
        DateOnly localToday, double lo, double span, double bx1, double bx2, double iconLeft, double minRight,
        double w, CardStyle st)
    {
        string name = day == localToday ? T("Bugün") : day == localToday.AddDays(1) ? T("Yarın")
            : Fmt.Gunler[((int)day.DayOfWeek + 6) % 7];
        const double bh = 5;
        double sx1 = bx1 + (bx2 - bx1) * (tmin - lo) / span;
        double sx2 = Math.Max(sx1 + bh, bx1 + (bx2 - bx1) * (tmax - lo) / span);
        var bar = new LinearGradientBrush(Theme.TempColor(tmin), Theme.TempColor(tmax), 0);
        bar.Freeze();
        bool wet = prob >= 50;
        return new DayItem(name, WeatherIcons.Get(code, false, st.LightIcons), iconLeft,
            $"{Fmt.Tint(tmin)}°", minRight, $"{Fmt.Tint(tmax)}°",
            bx1, bx2 - bx1, sx1, sx2 - sx1, bar, yc - 15,
            prob is double p ? Pct(p) : "", wet ? st.BlueBrush : st.MutedBrush,
            wet ? FontWeights.SemiBold : FontWeights.Normal, st.TextBrush, st.MutedBrush, Theme.Frozen(st.Track))
        {
        };
    }

    private static void Footer(CardModel m, WeatherData data, string? error, TimeSpan? offset, CardStyle st)
    {
        if (error is not null)
        {
            m.FooterText = error;
            m.FooterBrush = st.WarnBrush;
            m.FooterWeight = FontWeights.SemiBold;
            return;
        }
        var foot = data.Src == "om" ? T("Kaynak: Open-Meteo") : T("Kaynak: MGM");
        if (AlertService.TryParseUtc(J.StrOrNull(data.Cur?["veriZamani"]), out var vt))
            foot += T(" · Ölçüm ") + Fmt.LocalTime(vt, offset).ToString("HH:mm");
        m.FooterText = foot;
        m.FooterBrush = st.MutedBrush;
    }

    // ---------------------------------------------------------------- haftalık
    private static void Weekly(CardModel m, JsonObject extra, DateOnly localToday, CardStyle st, double w, bool om)
    {
        var d16 = new List<(DateOnly Day, string Code, double Lo, double Hi, double? Prob)>();
        foreach (var o in J.Arr(extra["daily16"]).OfType<JsonObject>())
        {
            if (!DateOnly.TryParseExact(J.Str(o["d"]), "yyyy-MM-dd", out var day)) continue;
            if (J.Num(o["lo"]) is not double lo || J.Num(o["hi"]) is not double hi) continue;
            if (day >= localToday) d16.Add((day, J.Str(o["code"]), lo, hi, J.Num(o["prob"])));
        }
        var week = d16.Take(7).ToList();
        if (week.Count == 0)
        {
            m.WeekEmpty = T("Haftalık tahmin alınamadı");
            return;
        }
        double min = week.Min(r => r.Lo), span = Math.Max(1.0, week.Max(r => r.Hi) - min);
        double bx1 = w - CardModel.Pad - 116, bx2 = w - CardModel.Pad - 34;
        m.Days = week.Select((r, i) => DayRow(r.Day, r.Code, r.Lo, r.Hi, r.Prob, 110 + i * 30 + 15, localToday,
            min, span, bx1, bx2, 112, bx1 - 8, w, st)).ToList();
        if (!om) return;   // 16 günlük eğilim yalnızca Open-Meteo'da (MGM 5 gün verir)
        int wetDays = d16.Count(r => (r.Prob ?? 0) >= 50);
        double avgLo = d16.Average(r => r.Lo), avgHi = d16.Average(r => r.Hi);
        m.Lines = new[] { 104.0, 326.0 };
        m.TrendTitle = T("{} günlük eğilim (model ortalaması, kesin tahmin değil)").F(d16.Count);
        m.TrendText = T("Ortalama {}° / {}°   ·   {} yağışlı gün").F(Fmt.Tint(avgLo), Fmt.Tint(avgHi), wetDays);
    }
}

/// Düzen boyutları (ölçek 1.0'da mantıksal piksel) — Python LAYOUTS.
public static class Layouts
{
    public const int MultiMax = 5;
    public const double MultiGap = 6;

    public static readonly (string Key, string Label)[] Menu =
    {
        ("micro", T("Mikro (ikon + sıcaklık)")),
        ("mini", T("Mini (yalnızca sıcaklık)")),
        ("miniplus", T("Mini+ (+ 12 saatlik grafik)")),
        ("vmini", T("Dikey mini")),
        ("compact", T("Kompakt (anlık durum)")),
        ("medium", T("Orta (+ ayrıntılar, saatlik)")),
        ("full", T("Tam (+ 5 günlük tahmin)")),
        ("weekly", T("Haftalık (7 gün + 16 günlük eğilim)")),
        ("row", T("Yan yana (tüm şehirler)")),
        ("col", T("Alt alta (tüm şehirler)")),
    };

    public static (double W, double H) Size(string layout) => layout switch
    {
        "micro" => (100, 44),
        "mini" or "row" or "col" => (150, 78),
        "miniplus" => (150, 112),
        "vmini" => (84, 150),
        "compact" => (340, 190),
        "peek" => (340, 368),
        "medium" => (360, 426),
        "full" => (360, 584),
        "weekly" => (360, 404),
        _ => (150, 78),
    };

    /// Pencere boyutu: row/col'da şehir sayısına göre.
    public static (double W, double H) WindowSize(string layout, int cities)
    {
        var (w, h) = Size(layout);
        int n = Math.Clamp(cities, 1, MultiMax);
        return layout switch
        {
            "row" => (n * w + (n - 1) * MultiGap, h),
            "col" => (w, n * h + (n - 1) * MultiGap),
            _ => (w, h),
        };
    }

    public static bool IsSmall(string layout) => layout is "micro" or "mini" or "miniplus" or "vmini";

    public static bool IsMulti(string layout) => layout is "row" or "col";

    public static string Supported(string? layout) => layout switch
    {
        "micro" or "mini" or "miniplus" or "vmini" or "compact" or "medium" or "full" or "weekly" or "row" or "col" => layout,
        _ => "full",   // Python: tanınmayan düzen -> "full"
    };
}
