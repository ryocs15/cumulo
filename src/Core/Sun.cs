namespace HavaDurumu.Core;

/// Güneş konumu hesapları (Python _sun_hours, is_night, day_factor, sun_times). Yaklaşık ±3 dk.
public static class Sun
{
    /// (doğuş, batış) UTC saat (0..24). Kutup gecesinde Polar = -1, kutup gündüzünde Polar = 1.
    public static (double Rise, double Set, int Polar) Hours(double lat, double lon, DateTime utc)
    {
        int n = utc.DayOfYear;
        double b = 2 * Math.PI * (n - 81) / 364;
        double eot = 9.87 * Math.Sin(2 * b) - 7.53 * Math.Cos(b) - 1.5 * Math.Sin(b);
        double decl = Rad(23.44) * Math.Sin(2 * Math.PI * (n - 81) / 365);
        double x = -Math.Tan(Rad(lat)) * Math.Tan(decl);
        if (x >= 1) return (0, 0, -1);
        if (x <= -1) return (0, 0, 1);
        double h0 = Deg(Math.Acos(x)) + 0.83;
        double noon = 12 - lon / 15 - eot / 60;
        return (Mod(noon - h0 / 15, 24), Mod(noon + h0 / 15, 24), 0);
    }

    public static bool IsNight(double lat, double lon, DateTime utc)
    {
        var (rise, set, polar) = Hours(lat, lon, utc);
        if (polar != 0) return polar < 0;
        double hour = utc.Hour + utc.Minute / 60.0;
        return rise < set ? !(rise <= hour && hour < set) : set <= hour && hour < rise;
    }

    /// 0 = gece, 1 = gündüz; doğuş/batış çevresinde 80 dk yumuşak geçiş.
    public static double DayFactor(double lat, double lon, DateTime utc)
    {
        var (rise, set, polar) = Hours(lat, lon, utc);
        if (polar != 0) return polar < 0 ? 0 : 1;
        double hour = utc.Hour + utc.Minute / 60.0 + utc.Second / 3600.0;
        static double Diff(double a, double b) => Mod(a - b + 12, 24) - 12;
        const double span = 80.0 / 60;
        double up = Diff(hour, rise) / span + 0.5;
        double down = Diff(set, hour) / span + 0.5;
        return Math.Clamp(Math.Min(up, down), 0, 1);
    }

    /// Bugünün doğuş/batış saatleri "SS:DD" (verilen saat diliminde); kutup durumunda null.
    public static (string Rise, string Set)? Times(double lat, double lon, DateTime utc, TimeSpan? offset)
    {
        var (rise, set, polar) = Hours(lat, lon, utc);
        if (polar != 0) return null;
        var day = utc.Date;
        return (Fmt.Hm(Fmt.LocalTime(day.AddHours(rise), offset)),
                Fmt.Hm(Fmt.LocalTime(day.AddHours(set), offset)));
    }

    /// Bir sonraki doğuş/batış: 2 saatten uzaksa saati, yakınsa kalan süre (Python sun_eta).
    public static string? Eta(double lat, double lon, DateTime nowUtc, TimeSpan? offset)
    {
        var events = new List<(DateTime At, string Name)>();
        for (int dd = 0; dd < 2; dd++)
        {
            var day = nowUtc.AddDays(dd);
            var (rise, set, polar) = Hours(lat, lon, day);
            if (polar != 0) continue;
            events.Add((day.Date.AddHours(rise), T("Doğuş")));
            events.Add((day.Date.AddHours(set), T("Batış")));
        }
        var next = events.Where(e => e.At > nowUtc).OrderBy(e => e.At).Cast<(DateTime, string)?>().FirstOrDefault();
        if (next is not { } nx) return null;
        int mins = Math.Max(1, (int)Math.Round((nx.Item1 - nowUtc).TotalMinutes));
        return mins > 120
            ? $"{nx.Item2} {Fmt.Hm(Fmt.LocalTime(nx.Item1, offset))}"
            : $"{nx.Item2} {Fmt.Minutes(mins)}";
    }

    private static double Rad(double d) => d * Math.PI / 180;
    private static double Deg(double r) => r * 180 / Math.PI;
    private static double Mod(double a, double m) => ((a % m) + m) % m;
}
