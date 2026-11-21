using System.Text.Json.Nodes;
using HavaDurumu.Core;

namespace HavaDurumu.Data;

/// Tek konum için hava durumu verisi. Kök nesne Python cache.json biçimindedir.
public sealed class WeatherData
{
    public WeatherData(JsonObject root) => Root = root;

    public JsonObject Root { get; }

    public JsonObject? Cur => Root["cur"] as JsonObject;

    public JsonObject? Daily => Root["daily"] as JsonObject;

    public JsonArray Hourly => J.Arr(Root["hourly"]);

    public JsonObject Extra => J.Obj(Root["extra"]);

    public string? Src => J.StrOrNull(Root["src"]);

    /// Şehrin saat farkı: Open-Meteo verisinde utc_offset; MGM verisinde Türkiye saati (UTC+3, 2016'dan beri
    /// yaz saati yok). Bilgisayarın saat dilimi kullanılsaydı yurt dışından bakan Türkiye saatlerini kaymış görürdü.
    public TimeSpan? UtcOffset => J.Num(Root["utc_offset"]) is double s ? TimeSpan.FromSeconds(s) : TimeSpan.FromHours(3);

    /// (gün, hadise, en düşük, en yüksek) — bugünden başlayarak en çok 5 gün (Python daily_rows).
    public List<(DateOnly Day, string Code, double Lo, double Hi)> DailyRows()
    {
        var dd = Daily ?? new JsonObject();
        var today = DateOnly.FromDateTime(Fmt.NowLocal(UtcOffset));
        var byDay = new SortedDictionary<DateOnly, (DateOnly, string, double, double)>();
        for (int i = 0; i < 6; i++)
        {
            var ts = J.Str(dd[$"tarihGun{i}"]);
            var lo = J.Num(dd[$"enDusukGun{i}"]);
            var hi = J.Num(dd[$"enYuksekGun{i}"]);
            if (ts.Length < 10 || !Fmt.Ok(lo) || !Fmt.Ok(hi)) continue;
            if (!DateOnly.TryParseExact(ts[..10], "yyyy-MM-dd", out var day) || day < today) continue;
            byDay[day] = (day, J.Str(dd[$"hadiseGun{i}"]),
                Math.Round(lo!.Value, MidpointRounding.ToEven), Math.Round(hi!.Value, MidpointRounding.ToEven));
        }
        return byDay.Values.Take(5).ToList();
    }

    /// MGM saatlik tahmin satırları (Open-Meteo yoksa yedek): (UTC zaman, hadise, sıcaklık).
    public List<(DateTime Utc, string Code, double Temp)> HourlyRows(DateTime nowUtc)
    {
        var rows = new List<(DateTime, string, double)>();
        foreach (var e in Hourly.OfType<JsonObject>())
        {
            if (!AlertService.TryParseUtc(J.StrOrNull(e["tarih"]), out var t)) continue;
            var temp = J.Num(e["sicaklik"]);
            if (t < nowUtc.AddMinutes(-90) || !Fmt.Ok(temp)) continue;
            rows.Add((t, J.Str(e["hadise"]), temp!.Value));
        }
        return rows.Take(7).ToList();
    }
}

public static class WeatherService
{
    /// Python fetch_weather(): ana veri (MGM ya da Open-Meteo) + ek veriler + hadise kodu düzeltmesi.
    /// Türkiye'deki konumlar yalnızca MGM verisiyle gösterilir; Open-Meteo yurt dışı içindir.
    public static async Task<WeatherData> FetchAsync(Location loc, CancellationToken ct)
    {
        var root = loc.IsOpenMeteo
            ? await OpenMeteoClient.ForecastAsync(loc, ct).ConfigureAwait(false)
            : await FetchMgmAsync(loc, ct).ConfigureAwait(false);
        root["extra"] = loc.IsOpenMeteo ? await OpenMeteoClient.ExtrasAsync(loc, ct).ConfigureAwait(false) : MgmExtras(root);
        FixCode(root);
        return new WeatherData(root);
    }

    private static async Task<JsonObject> FetchMgmAsync(Location loc, CancellationToken ct)
    {
        var cur = J.Arr(await MgmClient.ApiAsync("/sondurumlar", ct, ("merkezid", loc.Key)).ConfigureAwait(false));
        if (cur.Count == 0 && loc.Sondurum is long istNo)
            cur = J.Arr(await MgmClient.ApiAsync("/sondurumlar", ct, ("istno", istNo.ToString())).ConfigureAwait(false));

        var daily = J.Arr(await MgmClient.ApiAsync("/tahminler/gunluk", ct,
            ("istno", (loc.Gunluk ?? 0).ToString())).ConfigureAwait(false));

        var hourly = new JsonArray();
        try
        {
            var hr = J.Arr(await MgmClient.ApiAsync("/tahminler/saatlik", ct,
                ("istno", (loc.Saatlik ?? 0).ToString())).ConfigureAwait(false));
            if (hr.Count > 0 && J.Obj(hr[0])["tahmin"] is JsonArray t)
                hourly = (JsonArray)t.DeepClone();
            if (hourly.Count == 0 && loc.Il.Length > 0)   // ilçenin saatlik istasyonu yoksa il merkezinin tahmini
            {
                var ist = J.Arr(await MgmClient.ApiAsync("/merkezler", ct, ("il", loc.Il)).ConfigureAwait(false))
                    .OfType<JsonObject>().Select(e => J.Scalar(e["saatlikTahminIstNo"])).FirstOrDefault(s => s.Length > 0);
                if (ist is not null)
                {
                    hr = J.Arr(await MgmClient.ApiAsync("/tahminler/saatlik", ct, ("istno", ist)).ConfigureAwait(false));
                    if (hr.Count > 0 && J.Obj(hr[0])["tahmin"] is JsonArray t2)
                        hourly = (JsonArray)t2.DeepClone();
                }
            }
        }
        catch (Exception ex) when (ex is not OperationCanceledException)
        {
            Log.Write("saatlik tahmin alınamadı: " + ex.Message);
        }

        return new JsonObject
        {
            ["merkezId"] = loc.MerkezId?.DeepClone(),
            ["cur"] = cur.Count > 0 ? cur[0]?.DeepClone() : null,
            ["daily"] = daily.Count > 0 ? daily[0]?.DeepClone() : null,
            ["hourly"] = hourly,
            ["fetched"] = DateTime.UtcNow.ToString("o"),
        };
    }

    /// MGM saatlik (3 saatte bir) ve 5 günlük tahmini, Open-Meteo ek verisiyle aynı biçime (temps12, daily16)
    /// çevrilir; saatlik şerit, grafik ve haftalık düzen MGM verisiyle çalışır (Python mgm_extras).
    public static JsonObject MgmExtras(JsonObject root, DateTime? nowUtc = null)
    {
        var now = nowUtc ?? DateTime.UtcNow;
        var o = new JsonObject();
        var t12 = new JsonArray();
        foreach (var e in J.Arr(root["hourly"]).OfType<JsonObject>())
        {
            if (!AlertService.TryParseUtc(J.StrOrNull(e["tarih"]), out var t) || t < now.AddSeconds(-5400)) continue;
            if (J.Num(e["sicaklik"]) is not double temp || !Fmt.Ok(temp)) continue;
            var code = J.Str(e["hadise"]);
            t12.Add(new JsonArray(JsonValue.Create((t - DateTime.UnixEpoch).TotalSeconds), JsonValue.Create(temp),
                JsonValue.Create(code.Length > 0 ? code : "CB"), null));
        }
        if (t12.Count > 0) o["temps12"] = t12;
        var dd = J.Obj(root["daily"]);
        var d16 = new SortedDictionary<string, JsonObject>(StringComparer.Ordinal);
        for (int i = 0; i < 6; i++)
        {
            var ts = J.Str(dd[$"tarihGun{i}"]);
            if (ts.Length < 10 || J.Num(dd[$"enDusukGun{i}"]) is not double lo || J.Num(dd[$"enYuksekGun{i}"]) is not double hi
                || !Fmt.Ok(lo) || !Fmt.Ok(hi)) continue;
            var code = J.Str(dd[$"hadiseGun{i}"]);
            d16[ts[..10]] = new JsonObject
            {
                ["d"] = ts[..10], ["code"] = code.Length > 0 ? code : "CB", ["lo"] = lo, ["hi"] = hi, ["prob"] = null,
            };
        }
        if (d16.Count > 0) o["daily16"] = new JsonArray(d16.Values.Select(x => (JsonNode?)x).ToArray());
        return o;
    }

    /// MGM bazen hadise kodunu boş / -9999 gönderir. O zaman aynı saatin Open-Meteo kodu
    /// (yoksa "Çok Bulutlu") kullanılır; kartta "-9999" görünmesin.
    public static void FixCode(JsonObject root)
    {
        if (root["cur"] is not JsonObject cur) return;
        if (Constants.Hadise.ContainsKey(J.Str(cur["hadiseKodu"]))) return;

        var t12 = J.Obj(root["extra"])["temps12"] as JsonArray;
        var first = t12 is { Count: > 0 } ? J.Arr(t12[0]) : null;
        var alt = first is { Count: > 2 } ? J.Str(first[2]) : "";
        cur["hadiseKodu"] = Constants.Hadise.ContainsKey(alt) ? alt : "CB";
    }
}
