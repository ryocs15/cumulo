using System.Globalization;
using System.Text.Json.Nodes;
using HavaDurumu.Core;

namespace HavaDurumu.Data;

/// Open-Meteo: yurt dışı konumlar, ayrıca Türkiye'de ek veriler (hava kalitesi, UV, polen,
/// yağış olasılığı, dünle fark). API anahtarı gerekmez.
public static class OpenMeteoClient
{
    private const double OneDay = 86400;

    private static string Inv(double d) => d.ToString(CultureInfo.InvariantCulture);

    /// Unix saniyesi -> "yyyy-MM-ddTHH:mm:ss.000Z" (UTC). Python _iso() ile aynı biçim.
    public static string Iso(double ts) =>
        DateTimeOffset.FromUnixTimeSeconds((long)Math.Floor(ts))
            .UtcDateTime.ToString("yyyy-MM-dd'T'HH:mm:ss'.000Z'", CultureInfo.InvariantCulture);

    /// WMO kodu -> MGM hadise kodu (eşleşmezse "CB").
    public static string Wmo(double? code) =>
        code is double c && Constants.Wmo.TryGetValue((int)c, out var s) ? s : "CB";

    private static JsonNode? V(double? d) => JsonValue.Create(d);

    public static async Task<List<Location>> SearchAsync(string name, CancellationToken ct)
    {
        var url = Constants.OpenMeteoGeocode + "?" + Net.Query(
            ("name", name), ("count", "10"), ("language", Lang.Code));
        var j = J.Obj(await Net.GetJsonAsync(url, null, ct).ConfigureAwait(false));
        var result = new List<Location>();
        foreach (var r in J.Arr(j["results"]).OfType<JsonObject>())
        {
            var lat = J.Num(r["latitude"]);
            var lon = J.Num(r["longitude"]);
            if (lat is null || lon is null) continue;
            result.Add(new Location
            {
                Src = "om",
                Il = J.Str(r["country"]),
                Ilce = J.Str(r["name"]),
                Admin = J.Str(r["admin1"]),
                MerkezId = JsonValue.Create($"om_{Fmt3(lat.Value)}_{Fmt3(lon.Value)}"),
                Lat = lat.Value,
                Lon = lon.Value,
            });
        }
        return result;
    }

    private static string Fmt3(double d) => d.ToString("0.000", CultureInfo.InvariantCulture);

    /// Open-Meteo tahminini MGM veri biçimine çevirir (cache.json ve çizim kodu aynı yapıyı görür).
    public static async Task<JsonObject> ForecastAsync(Location loc, CancellationToken ct)
    {
        var url = Constants.OpenMeteoForecast + "?" + Net.Query(
            ("latitude", Inv(loc.Lat)), ("longitude", Inv(loc.Lon)),
            ("current", "temperature_2m,relative_humidity_2m,apparent_temperature,weather_code," +
                        "pressure_msl,wind_speed_10m,wind_direction_10m"),
            ("hourly", "temperature_2m,weather_code,visibility"),
            ("daily", "weather_code,temperature_2m_max,temperature_2m_min,precipitation_sum"),
            ("timezone", "auto"), ("forecast_days", "6"), ("timeformat", "unixtime"));
        var j = J.Obj(await Net.GetJsonAsync(url, null, ct).ConfigureAwait(false));
        var c = J.Obj(j["current"]);
        var h = J.Obj(j["hourly"]);
        var dl = J.Obj(j["daily"]);
        long off = (long)(J.Num(j["utc_offset_seconds"]) ?? 0);
        double nowTs = J.Num(c["time"]) ?? DateTimeOffset.UtcNow.ToUnixTimeSeconds();

        var ht = J.Nums(h["time"]);
        var temp = J.Nums(h["temperature_2m"]);
        var hcodes = J.Nums(h["weather_code"]);
        var vis = J.Nums(h["visibility"]);
        int start = ht.FindIndex(t => t >= nowTs - 1800);
        if (start < 0) start = ht.Count;

        var hourly = new JsonArray();
        for (int i = start; i < Math.Min(ht.Count, start + 24); i += 3)
        {
            var t = J.At(temp, i);
            if (t is null) continue;
            hourly.Add(new JsonObject
            {
                ["tarih"] = Iso(ht[i] ?? 0),
                ["hadise"] = Wmo(J.At(hcodes, i)),
                ["sicaklik"] = V(t),
            });
        }

        var dts = J.Nums(dl["time"]);
        var dmin = J.Nums(dl["temperature_2m_min"]);
        var dmax = J.Nums(dl["temperature_2m_max"]);
        var dcode = J.Nums(dl["weather_code"]);
        var daily = new JsonObject();
        for (int i = 0; i < dts.Count; i++)
        {
            daily[$"tarihGun{i}"] = Iso((dts[i] ?? 0) + off)[..10] + "T00:00:00.000Z";
            daily[$"enDusukGun{i}"] = V(J.At(dmin, i));
            daily[$"enYuksekGun{i}"] = V(J.At(dmax, i));
            daily[$"hadiseGun{i}"] = Wmo(J.At(dcode, i));
        }

        double? rain = J.At(J.Nums(dl["precipitation_sum"]), 0);
        double? visNow = J.At(vis, start);
        var cur = new JsonObject
        {
            ["sicaklik"] = V(J.Num(c["temperature_2m"])),
            ["hissedilenSicaklik"] = V(J.Num(c["apparent_temperature"])),
            ["nem"] = V(J.Num(c["relative_humidity_2m"])),
            ["ruzgarHiz"] = V(J.Num(c["wind_speed_10m"])),
            ["ruzgarYon"] = V(J.Num(c["wind_direction_10m"])),
            ["denizeIndirgenmisBasinc"] = V(J.Num(c["pressure_msl"])),
            ["hadiseKodu"] = Wmo(J.Num(c["weather_code"])),
            ["veriZamani"] = Iso(nowTs),
            ["yagis24Saat"] = V(rain ?? Constants.MissingValue),
            ["gorus"] = V(visNow ?? Constants.MissingValue),
            ["denizSicaklik"] = V(Constants.MissingValue),
            ["karYukseklik"] = V(Constants.MissingValue),
            ["kapalilik"] = V(Constants.MissingValue),
        };

        return new JsonObject
        {
            ["merkezId"] = loc.MerkezId?.DeepClone(),
            ["src"] = "om",
            ["utc_offset"] = JsonValue.Create(off),
            ["cur"] = cur,
            ["daily"] = daily,
            ["hourly"] = hourly,
            ["fetched"] = DateTime.UtcNow.ToString("o"),
        };
    }

    /// Ek bilgiler (Python fetch_extras). Hata olursa eksik alanlarla döner; ana veriyi etkilemez.
    public static async Task<JsonObject> ExtrasAsync(Location loc, CancellationToken ct)
    {
        var o = new JsonObject();
        double lat = loc.Lat, lon = loc.Lon;
        double now = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds() / 1000.0;

        try
        {
            var url = Constants.OpenMeteoForecast + "?" + Net.Query(
                ("latitude", Inv(lat)), ("longitude", Inv(lon)),
                ("hourly", "temperature_2m,precipitation_probability,uv_index,weather_code"),
                ("minutely_15", "precipitation"), ("forecast_minutely_15", "16"), ("past_minutely_15", "1"),
                ("daily", "weather_code,temperature_2m_max,temperature_2m_min," +
                          "precipitation_probability_max,uv_index_max"),
                ("past_days", "1"), ("forecast_days", "16"), ("timezone", "auto"), ("timeformat", "unixtime"));
            var j = J.Obj(await Net.GetJsonAsync(url, null, ct).ConfigureAwait(false));
            var h = J.Obj(j["hourly"]);
            var dl = J.Obj(j["daily"]);
            var ts = J.Nums(h["time"]);
            int i = LastIndexAtOrBefore(ts, now);
            if (i >= 0)
            {
                var temps = J.Nums(h["temperature_2m"]);
                var probs = J.Nums(h["precipitation_probability"]);
                var uv = J.Nums(h["uv_index"]);
                var codes = J.Nums(h["weather_code"]);

                var target = (ts[i] ?? 0) - OneDay;
                int j24 = ts.FindIndex(x => x == target);
                var ti = J.At(temps, i);
                var tj = j24 >= 0 ? J.At(temps, j24) : null;
                if (ti is double a && tj is double b)
                    o["yesterday_diff"] = JsonValue.Create(a - b);

                var uvNow = J.At(uv, i);
                if (uvNow is not null) o["uv_now"] = JsonValue.Create(uvNow);

                var nxt = probs.Skip(i).Take(12).Where(p => p is not null).Select(p => p!.Value).ToList();
                if (nxt.Count > 0) o["rain_prob_12h"] = JsonValue.Create(nxt.Max());

                var t12 = new JsonArray();
                for (int n = i; n < Math.Min(ts.Count, i + 13); n++)
                {
                    if (J.At(temps, n) is not double tn) continue;
                    t12.Add(new JsonArray(
                        JsonValue.Create(ts[n]),
                        JsonValue.Create(tn),
                        JsonValue.Create(Wmo(J.At(codes, n))),
                        V(J.At(probs, n))));
                }
                if (t12.Count > 0) o["temps12"] = t12;
            }

            var m15 = J.Obj(j["minutely_15"]);
            var mt = J.Nums(m15["time"]);
            var mp = J.Nums(m15["precipitation"]);
            var r15 = new JsonArray();
            for (int n = 0; n < Math.Min(mt.Count, mp.Count); n++)
                r15.Add(new JsonArray(JsonValue.Create(mt[n]), V(mp[n])));
            if (r15.Count > 0) o["rain15"] = r15;

            var dts = J.Nums(dl["time"]);
            int ti2 = LastIndexAtOrBefore(dts, now);
            if (ti2 >= 0)
            {
                var pm = J.At(J.Nums(dl["precipitation_probability_max"]), ti2);
                var um = J.At(J.Nums(dl["uv_index_max"]), ti2);
                if (pm is not null) o["rain_prob"] = JsonValue.Create(pm);
                if (um is not null) o["uv_max"] = JsonValue.Create(um);

                double off = J.Num(j["utc_offset_seconds"]) ?? 0;
                var codes = J.Nums(dl["weather_code"]);
                var his = J.Nums(dl["temperature_2m_max"]);
                var los = J.Nums(dl["temperature_2m_min"]);
                var probs = J.Nums(dl["precipitation_probability_max"]);
                var d16 = new JsonArray();
                for (int n = ti2; n < dts.Count; n++)
                {
                    if (J.At(his, n) is not double hi || J.At(los, n) is not double lo) continue;
                    d16.Add(new JsonObject
                    {
                        ["d"] = Iso((dts[n] ?? 0) + off)[..10],
                        ["code"] = Wmo(J.At(codes, n)),
                        ["lo"] = JsonValue.Create(lo),
                        ["hi"] = JsonValue.Create(hi),
                        ["prob"] = V(J.At(probs, n)),
                    });
                }
                if (d16.Count > 0) o["daily16"] = d16;
            }
        }
        catch (Exception ex) when (ex is not OperationCanceledException)
        {
            Log.Write("ek tahmin alınamadı: " + ex.Message);
        }

        try
        {
            var pollenKeys = string.Join(",", Constants.Pollen.Keys.Select(p => p + "_pollen"));
            var url = Constants.OpenMeteoAir + "?" + Net.Query(
                ("latitude", Inv(lat)), ("longitude", Inv(lon)), ("timezone", "auto"),
                ("current", "european_aqi,pm2_5,pm10,ozone," + pollenKeys));
            var c = J.Obj(J.Obj(await Net.GetJsonAsync(url, null, ct).ConfigureAwait(false))["current"]);
            foreach (var key in new[] { "european_aqi", "pm2_5", "pm10", "ozone" })
            {
                var v = J.Num(c[key]);
                if (v is not null) o[key] = JsonValue.Create(v);
            }
            var pol = new JsonObject();
            foreach (var p in Constants.Pollen.Keys)
            {
                var v = J.Num(c[p + "_pollen"]);
                if (v is not null) pol[p] = JsonValue.Create(v);
            }
            if (pol.Count > 0) o["pollen"] = pol;
        }
        catch (Exception ex) when (ex is not OperationCanceledException)
        {
            Log.Write("hava kalitesi alınamadı: " + ex.Message);
        }

        return o;
    }

    private static int LastIndexAtOrBefore(List<double?> ts, double now)
    {
        for (int i = ts.Count - 1; i >= 0; i--)
            if (ts[i] is double t && t <= now) return i;
        return -1;
    }
}
