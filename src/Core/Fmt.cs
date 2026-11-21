using System.Globalization;
using System.Text.Json.Nodes;

namespace HavaDurumu.Core;

/// Birim ayarları (config: units_temp "C"/"F", units_wind "kmh"/"ms").
public static class Units
{
    public static bool Fahrenheit { get; set; }
    public static bool WindMs { get; set; }
    public static bool Clock12 { get; set; }
}

/// Gösterim biçimleri — Python sürümündeki fmt_temp, tint, fmt_wind, wind_dir, aqi_level vb.
public static class Fmt
{
    private static readonly CultureInfo Inv = CultureInfo.InvariantCulture;

    public static readonly string[] Gunler = { T("Pazartesi"), T("Salı"), T("Çarşamba"), T("Perşembe"), T("Cuma"), T("Cumartesi"), T("Pazar") };
    public static readonly string[] GunKisa = { T("Pzt"), T("Sal"), T("Çar"), T("Per"), T("Cum"), T("Cmt"), T("Paz") };
    public static readonly string[] AyKisa = { T("Oca"), T("Şub"), T("Mar"), T("Nis"), T("May"), T("Haz"), T("Tem"), T("Ağu"), T("Eyl"), T("Eki"), T("Kas"), T("Ara") };

    public static bool Ok(double? v) => v is double d && d != Constants.MissingValue;

    public static double TConv(double v) => Units.Fahrenheit ? v * 9 / 5 + 32 : v;

    /// Tam sayı sıcaklık (tahminler). Python round(): yarımda çifte yuvarlar.
    public static int Tint(double v) => (int)Math.Round(TConv(v), MidpointRounding.ToEven);

    /// Anlık ölçüm, bir ondalık: "8.8°".
    public static string Temp(double? v)
    {
        if (!Ok(v)) return "–";
        double r = Math.Round(TConv(v!.Value), 1, MidpointRounding.ToEven);
        if (r == 0) r = 0; // "-0.0" olmasın
        return r.ToString("0.0", Inv) + "°";
    }

    public static string Wind(double v) =>
        Units.WindMs ? T("{:.1f} m/sn").F(v / 3.6) : T("{} km/sa").F(Math.Round(v, MidpointRounding.ToEven).ToString("0", Inv));

    public static string WindDir(double? deg)
    {
        if (!Ok(deg)) return "";
        string[] d = T("K KD D GD G GB B KB").Split(' ');
        return d[(int)((deg!.Value % 360 + 360) % 360 / 45 + 0.5) % 8];
    }

    public static string WindText(double? speed, double? deg)
    {
        if (!Ok(speed)) return "–";
        return speed < 1 ? T("Sakin") : $"{Wind(speed!.Value)} {WindDir(deg)}".Trim();
    }

    public static (string Name, (byte R, byte G, byte B) Color) AqiLevel(double v)
    {
        if (v <= 20) return (T("İyi"), (80, 200, 120));
        if (v <= 40) return (T("Makul"), (160, 210, 80));
        if (v <= 60) return (T("Orta"), (240, 200, 60));
        if (v <= 80) return (T("Kötü"), (245, 140, 60));
        if (v <= 100) return (T("Çok kötü"), (225, 70, 70));
        return (T("Aşırı kötü"), (160, 70, 170));
    }

    public static string UvLevel(double v) =>
        v <= 2 ? T("Düşük") : v <= 5 ? T("Orta") : v <= 7 ? T("Yüksek") : v <= 10 ? T("Çok yüksek") : T("Aşırı");

    public static string YesterdayText(double diff)
    {
        double d = Units.Fahrenheit ? diff * 9 / 5 : diff;
        int r = (int)Math.Round(Math.Abs(d), MidpointRounding.ToEven);
        return r == 0 ? T("dünle aynı") : T("dünden {}° daha {}").F(r, d > 0 ? T("sıcak") : T("soğuk"));
    }

    public static string Cut(string s, int n) => s.Length > n ? s[..n] : s;

    public static string Minutes(int m)
    {
        if (m < 60) return T("{} dk").F(m);
        int h = m / 60, mm = m % 60;
        return mm != 0 ? T("{} sa {} dk").F(h, mm) : T("{} sa").F(h);
    }

    private static readonly HashSet<string> Wet = new(
        Constants.Rain.Keys.Concat(Constants.Snow.Keys).Concat(new[] { "KKY", "DY", "GSY", "KGSY" }));

    public static bool IsWet(string code) => Wet.Contains(code);

    /// 15 dakikalık yağış tahmininden kısa geri sayım (Python rain_eta).
    public static string? RainEta(JsonObject extra, string code, double nowUnix)
    {
        var steps = new List<(double At, double Mm)>();
        foreach (var n in J.Arr(extra["rain15"]))
        {
            var pair = J.Arr(n);
            if (pair.Count < 2 || J.Num(pair[0]) is not double a) continue;
            if (a + 900 > nowUnix) steps.Add((a, J.Num(pair[1]) ?? 0));
        }
        if (steps.Count == 0) return null;
        const double th = 0.1;
        if (steps[0].Mm >= th)
        {
            foreach (var (a, b) in steps.Skip(1))
                if (b < th)
                    return T("☂ {} daha").F(Minutes(Math.Max(15, (int)Math.Round((a - nowUnix) / 300) * 5)));
            return T("☂ 4 sa+ daha");
        }
        if (IsWet(code)) return T("Yağış sürüyor");
        foreach (var (a, b) in steps.Skip(1))
            if (b >= th)
                return T("☂ {} sonra").F(Minutes(Math.Max(5, (int)Math.Round((a - nowUnix) / 300) * 5)));
        return T("Yağış yok");
    }

    /// UTC zamanı verinin saat dilimine (yurt dışı: utc_offset) ya da sistem saatine çevirir.
    public static DateTime LocalTime(DateTime utc, TimeSpan? offset)
    {
        utc = DateTime.SpecifyKind(utc, DateTimeKind.Utc);
        return offset is TimeSpan o ? DateTime.SpecifyKind(utc + o, DateTimeKind.Unspecified) : utc.ToLocalTime();
    }

    public static DateTime NowLocal(TimeSpan? offset) => LocalTime(DateTime.UtcNow, offset);

    public static string ClockText(TimeSpan? offset)
    {
        var n = NowLocal(offset);
        int wd = ((int)n.DayOfWeek + 6) % 7;
        return $"{GunKisa[wd]} {n.Day} {AyKisa[n.Month - 1]} · {Hm(n)}";
    }

    /// Saat: "14:05" ya da 12 saat seçiliyse "2:05 PM" (config "clock_12h").
    public static string Hm(DateTime t) => t.ToString(Units.Clock12 ? "h:mm tt" : "HH:mm", Inv);

    /// Saatlik tahmin etiketi: "14:00" ya da "2 PM" (dar sütunlara sığsın).
    public static string Hour(DateTime t) => t.ToString(Units.Clock12 ? (t.Minute == 0 ? "h tt" : "h:mm tt") : "HH:mm", Inv);

    public static string LocTitle(Location l)
    {
        if (!string.IsNullOrEmpty(l.Name)) return l.Name!;
        return string.IsNullOrEmpty(l.Ilce) || l.Ilce == l.Il || l.Ilce == "Merkez" ? l.Il : $"{l.Ilce}, {l.Il}";
    }

    public static string LocShort(Location l)
    {
        if (!string.IsNullOrEmpty(l.Name)) return l.Name!;
        return string.IsNullOrEmpty(l.Ilce) || l.Ilce == l.Il || l.Ilce == "Merkez" ? l.Il : l.Ilce;
    }

    public static string Hadise(string code) => Constants.Hadise.TryGetValue(code, out var s) ? s : code;

    private static readonly string[] MoonShort = { T("Yeni ay"), T("Hilal"), T("İlk dördün"), T("Şişkin ay"), T("Dolunay"), T("Şişkin ay"), T("Son dördün"), T("Hilal") };

    /// Ay evresinin kısa adı ve aydınlık yüzdesi (Python moon_info).
    public static (string Short, double Illum) MoonInfo(DateTime utc)
    {
        var refT = new DateTime(2000, 1, 6, 18, 14, 0, DateTimeKind.Utc);
        double p = ((utc - refT).TotalDays / 29.530588853) % 1.0;
        if (p < 0) p += 1;
        int i = (int)(p * 8 + 0.5) % 8;
        return (MoonShort[i], (1 - Math.Cos(2 * Math.PI * p)) / 2 * 100);
    }

    /// En yoğun polen türü ve seviyesi (Python pollen_summary); yoksa null.
    public static (string Kind, string Level)? PollenSummary(JsonObject? pol)
    {
        if (pol is null || pol.Count == 0) return null;
        string? kind = null;
        double best = double.MinValue;
        foreach (var (k, v) in pol)
            if (J.Num(v) is double d && d > best) { best = d; kind = k; }
        if (kind is null || best < 1) return null;
        string level = best < 10 ? T("düşük") : best < 50 ? T("orta") : best < 200 ? T("yüksek") : T("çok yüksek");
        return (Constants.Pollen.TryGetValue(kind, out var name) ? name : kind, level);
    }

    /// Python "{v:g}" benzeri: gereksiz sıfırsız kısa sayı.
    public static string G(double v) => v.ToString("0.######", Inv);

    public static string N0(double v) => Math.Round(v, MidpointRounding.ToEven).ToString("0", Inv);
}
