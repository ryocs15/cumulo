using System.Globalization;
using System.Text.Json.Nodes;
using HavaDurumu.Core;

namespace HavaDurumu.Data;

/// Bir konumu kapsayan MGM meteorolojik uyarısı (Python alerts_for).
public sealed record AlertItem(
    int Rank,
    string Color,
    string ColorName,
    string? No,
    IReadOnlyList<string> Kinds,
    IReadOnlyList<string> Codes,
    string Text,
    string? Begin,
    string? End);

public static class AlertService
{
    public static async Task<JsonArray> FetchAsync(CancellationToken ct) =>
        J.Arr(await MgmClient.ApiAsync("/meteoalarm/today", ct).ConfigureAwait(false));

    /// Bu konumu kapsayan uyarılar, en ciddisi başta. Open-Meteo konumlarında uyarı yoktur.
    public static List<AlertItem> AlertsFor(Location loc, JsonArray? alerts, DateTime? nowUtc = null)
    {
        var result = new List<AlertItem>();
        if (loc.IsOpenMeteo || alerts is null) return result;

        var now = nowUtc ?? DateTime.UtcNow;
        var key = loc.Key;
        foreach (var node in alerts.OfType<JsonObject>())
        {
            if (TryParseUtc(J.StrOrNull(node["end"]), out var end) && end < now)
                continue;

            var towns = J.Obj(node["towns"]);
            for (int rank = 0; rank < 3; rank++)
            {
                var color = rank switch { 0 => "red", 1 => "orange", _ => "yellow" };
                var list = J.Arr(towns[color]);
                if (!list.Any(x => J.Scalar(x) == key)) continue;

                var codes = J.Arr(J.Obj(node["weather"])[color]).Select(k => J.Str(k)).ToList();
                var kinds = codes.Select(k => Constants.AlertKinds.GetValueOrDefault(k, k)).ToList();
                var text = J.Str(J.Obj(node["text"])[color]);
                result.Add(new AlertItem(
                    Rank: rank,
                    Color: color,
                    ColorName: Constants.AlertColorNames[color],
                    No: J.StrOrNull(node["alertNo"]) ?? J.Scalar(node["alertNo"]),
                    Kinds: kinds,
                    Codes: codes,
                    Text: text,
                    Begin: J.StrOrNull(node["begin"]),
                    End: J.StrOrNull(node["end"])));
                break;
            }
        }
        return result.OrderBy(a => a.Rank).ToList();
    }

    /// Python parse_utc(): ilk 19 karakter "yyyy-MM-ddTHH:mm:ss", UTC kabul edilir.
    public static bool TryParseUtc(string? s, out DateTime value)
    {
        value = default;
        if (s is null || s.Length < 19) return false;
        return DateTime.TryParseExact(s[..19], "yyyy-MM-dd'T'HH:mm:ss", CultureInfo.InvariantCulture,
            DateTimeStyles.AssumeUniversal | DateTimeStyles.AdjustToUniversal, out value);
    }
}
