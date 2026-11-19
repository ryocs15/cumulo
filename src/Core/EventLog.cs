using System.Globalization;
using System.IO;
using System.Text;
using System.Text.Encodings.Web;
using System.Text.Json;
using System.Text.Json.Nodes;
using HavaDurumu.Data;

namespace HavaDurumu.Core;

/// Hava olaylarının günlüğü (events.json, Python EventLog ile aynı biçim). Olay, iki ölçüm arasındaki
/// DEĞİŞİMDİR; bu yüzden şehir başına son durum da saklanır (yeniden başlatınca aynı olay tekrar yazılmaz).
/// Bir şehir ilk kez görüldüğünde yalnızca durumu kaydedilir, olay üretilmez.
public sealed class EventLog
{
    private const int EventMax = 500;            // dosyada tutulan en fazla kayıt
    private const double TempDelta = 5.0;        // °C: en çok 3 saatte bu kadar değişim olay sayılır
    private const double UvLimit = 8.0, WindLimit = 60.0;

    private static readonly JsonSerializerOptions WriteOptions = new()
    {
        WriteIndented = true, Encoder = JavaScriptEncoder.UnsafeRelaxedJsonEscaping,
    };

    private readonly string _path;

    public EventLog(string path)
    {
        _path = path;
        try
        {
            if (File.Exists(path) && JsonNode.Parse(File.ReadAllText(path, Encoding.UTF8)) is JsonObject d)
            {
                Events = J.Arr(d["events"]).OfType<JsonObject>().Where(e => J.StrOrNull(e["t"]) is not null)
                    .Select(e => (JsonObject)e.DeepClone()).ToList();
                State = (JsonObject)J.Obj(d["state"]).DeepClone();
            }
        }
        catch (Exception ex) when (ex is IOException or JsonException or UnauthorizedAccessException)
        {
            Log.Write("olay günlüğü okunamadı: " + ex.Message);
        }
    }

    public List<JsonObject> Events { get; private set; } = new();
    public JsonObject State { get; private set; } = new();

    public void Save()
    {
        if (Events.Count > EventMax) Events.RemoveRange(0, Events.Count - EventMax);
        var root = new JsonObject
        {
            ["events"] = new JsonArray(Events.Select(e => (JsonNode?)e.DeepClone()).ToArray()),
            ["state"] = State.DeepClone(),
        };
        try
        {
            var tmp = _path + ".tmp";
            File.WriteAllText(tmp, root.ToJsonString(WriteOptions), new UTF8Encoding(false));
            File.Move(tmp, _path, overwrite: true);
        }
        catch (Exception ex) when (ex is IOException or UnauthorizedAccessException)
        {
            Log.Write("olay günlüğü yazılamadı: " + ex.Message);
        }
    }

    public void Clear()
    {
        Events.Clear();
        Save();
    }

    /// Son `hours` saatin olayları, en yeni başta.
    public List<JsonObject> Recent(int hours = 24)
    {
        var cut = DateTime.UtcNow.AddHours(-hours);
        var res = new List<JsonObject>();
        for (int i = Events.Count - 1; i >= 0; i--)
            if (AlertService.TryParseUtc(J.StrOrNull(Events[i]["t"]), out var t) && t >= cut) res.Add(Events[i]);
        return res;
    }

    private JsonObject Add(Location loc, string kind, DateTime now, JsonObject? extra = null)
    {
        var e = new JsonObject
        {
            ["t"] = now.ToString("yyyy-MM-dd'T'HH:mm:ss'.000Z'", CultureInfo.InvariantCulture),
            ["key"] = loc.Key, ["city"] = Fmt.LocShort(loc), ["kind"] = kind,
        };
        if (extra is not null)
            foreach (var (k, v) in extra) e[k] = v?.DeepClone();
        Events.Add(e);
        return e;
    }

    /// Yeni ölçümü önceki durumla karşılaştırır; oluşan olayları döndürür.
    public List<JsonObject> Observe(Location loc, WeatherData data)
    {
        var res = new List<JsonObject>();
        if (data.Cur is not JsonObject cur) return res;
        var now = DateTime.UtcNow;
        double ts = (now - DateTime.UnixEpoch).TotalSeconds;
        bool first = State[loc.Key] is not JsonObject;
        var st = first ? new JsonObject() : (JsonObject)State[loc.Key]!;
        string code = J.Str(cur["hadiseKodu"]);
        bool wet = Fmt.IsWet(code);
        var tv = J.Num(cur["sicaklik"]);
        var rz = J.Num(cur["ruzgarHiz"]);
        var uv = J.Num(data.Extra["uv_now"]);
        bool hiUv = uv >= UvLimit, hiW = Fmt.Ok(rz) && rz >= WindLimit;
        if (!first)
        {
            bool wasWet = st["wet"]?.GetValue<bool>() == true;
            if (wet && !wasWet) res.Add(Add(loc, "rain_start", now, new() { ["code"] = code }));
            else if (!wet && wasWet) res.Add(Add(loc, "rain_end", now, new() { ["code"] = code }));
            var tref = J.Num(st["t_ref"]);
            double trefTs = J.Num(st["t_ref_ts"]) ?? 0;
            if (Fmt.Ok(tv) && tref is double r && Math.Abs(tv!.Value - r) >= TempDelta)
            {
                res.Add(Add(loc, "temp", now, new()
                {
                    ["a"] = r, ["b"] = tv.Value, ["h"] = Math.Max(1, (int)Math.Round((ts - trefTs) / 3600)),
                    ["code"] = tv > r ? "SCK" : "SGK",
                }));
                st["t_ref"] = null;   // yeni ölçümden itibaren yeniden say
            }
            if (hiUv && st["uv"]?.GetValue<bool>() != true) res.Add(Add(loc, "uv", now, new() { ["a"] = uv, ["code"] = "SCK" }));
            if (hiW && st["wind"]?.GetValue<bool>() != true) res.Add(Add(loc, "wind", now, new() { ["a"] = rz, ["code"] = "R" }));
        }
        st["wet"] = wet;
        st["uv"] = hiUv;
        st["wind"] = hiW;
        // sıcaklık referansı: 3 saatten eskiyse yenilenir (kayan pencere)
        if (Fmt.Ok(tv) && (J.Num(st["t_ref"]) is null || ts - (J.Num(st["t_ref_ts"]) ?? 0) > 3 * 3600))
        {
            st["t_ref"] = tv;
            st["t_ref_ts"] = ts;
        }
        State[loc.Key] = st;
        return res;
    }

    /// Bu şehir için ilk kez görülen MGM uyarılarını günlüğe yazar.
    public List<JsonObject> ObserveAlerts(Location loc, JsonArray alerts)
    {
        var res = new List<JsonObject>();
        if (State[loc.Key] is not JsonObject st) State[loc.Key] = st = new JsonObject();
        var seen = J.Arr(st["alerts"]).Select(J.Scalar).ToList();
        foreach (var a in AlertService.AlertsFor(loc, alerts))
        {
            var sid = a.No ?? "";
            if (seen.Contains(sid)) continue;
            seen.Add(sid);
            res.Add(Add(loc, "alert", DateTime.UtcNow, new() { ["color"] = a.Color, ["what"] = string.Join(", ", a.Codes) }));   // kod: dil değişince yeni dilde gösterilir
        }
        st["alerts"] = new JsonArray(seen.TakeLast(30).Select(x => (JsonNode?)x).ToArray());
        return res;
    }

    /// Olayın kısa metni (sıcaklık ve rüzgar, seçili birimle).
    public static string Text(JsonObject e) => J.Str(e["kind"]) switch
    {
        "rain_start" => T("Yağış başladı"),
        "rain_end" => T("Yağış bitti"),
        "temp" => TempText(J.Num(e["a"]) ?? 0, J.Num(e["b"]) ?? 0, (int)(J.Num(e["h"]) ?? 1)),
        "uv" => T("UV {:.0f} (çok yüksek)").F(J.Num(e["a"]) ?? 0),
        "wind" => T("Rüzgar ") + Fmt.Wind(J.Num(e["a"]) ?? 0),
        "alert" => T("{} uyarı").F(Constants.AlertColorNames.TryGetValue(J.Str(e["color"]), out var n) ? n : "")
                   + (J.Str(e["what"]).Length > 0
                       ? ": " + string.Join(", ", J.Str(e["what"]).Split(", ").Select(w => Constants.AlertKinds.GetValueOrDefault(w, w)))
                       : ""),   // eski kayıtlarda Türkçe ad var; olduğu gibi kalır
        var k => J.Str(e["what"]) is { Length: > 0 } w ? w : k,
    };

    private static string TempText(double a, double b, int h) =>
        T("{:.0f}° {} ({} sa)").F(Math.Abs(Fmt.TConv(b) - Fmt.TConv(a)), b > a ? T("yükseldi") : T("düştü"), h);
}
