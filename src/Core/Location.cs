using System.Text.Json.Nodes;

namespace HavaDurumu.Core;

/// Bir konum: MGM merkezi (Türkiye, src yok) ya da Open-Meteo koordinatı (src == "om").
/// config.json içindeki "locs" girdileriyle birebir uyumludur.
public sealed class Location
{
    public string Il { get; set; } = "";
    public string Ilce { get; set; } = "";
    public string? Admin { get; set; }
    public string? Src { get; set; }

    /// Kullanıcının verdiği özel ad (config "name"); yoksa null.
    public string? Name { get; set; }

    /// MGM: sayısal merkezId; Open-Meteo: "om_lat_lon" metni. Yazarken aynen korunur.
    public JsonNode? MerkezId { get; set; }
    public long? Sondurum { get; set; }
    public long? Gunluk { get; set; }
    public long? Saatlik { get; set; }
    public double Lat { get; set; } = 39.0;
    public double Lon { get; set; } = 35.0;

    public bool IsOpenMeteo => Src == "om";

    /// Önbellek anahtarı ve MGM istek parametresi olarak kullanılan metin.
    public string Key => J.Scalar(MerkezId);

    public static Location FromJson(JsonObject o) => new()
    {
        Il = J.Str(o["il"]),
        Ilce = J.Str(o["ilce"]),
        Admin = J.StrOrNull(o["admin"]),
        Src = J.StrOrNull(o["src"]),
        Name = J.StrOrNull(o["name"]),
        MerkezId = o["merkezId"]?.DeepClone(),
        Sondurum = Long(o, "sondurum"),
        Gunluk = Long(o, "gunluk"),
        Saatlik = Long(o, "saatlik"),
        Lat = J.Num(o["lat"]) ?? 39.0,
        Lon = J.Num(o["lon"]) ?? 35.0,
    };

    /// MGM "merkez" kaydından widget'ın ihtiyaç duyduğu alanlar (Python loc_from_entry).
    public static Location FromMgmEntry(JsonObject e)
    {
        var mid = e["merkezId"]?.DeepClone();
        long? Id(string k) => J.Num(e[k]) is double d ? (long?)d : null;
        long? m = J.Num(mid) is double md ? (long?)md : null;
        return new Location
        {
            Il = J.Str(e["il"]), Ilce = J.Str(e["ilce"]), MerkezId = mid,
            Sondurum = Id("sondurumIstNo") ?? m,
            Gunluk = Id("gunlukTahminIstNo") ?? m,
            Saatlik = Id("saatlikTahminIstNo") ?? Id("sondurumIstNo") ?? m,
            Lat = J.Num(e["enlem"]) ?? 39.0, Lon = J.Num(e["boylam"]) ?? 35.0,
        };
    }

    public JsonObject ToJson()
    {
        var o = new JsonObject
        {
            ["il"] = Il,
            ["ilce"] = Ilce,
            ["merkezId"] = MerkezId?.DeepClone(),
            ["sondurum"] = JsonValue.Create(Sondurum),
            ["gunluk"] = JsonValue.Create(Gunluk),
            ["saatlik"] = JsonValue.Create(Saatlik),
            ["lat"] = Lat,
            ["lon"] = Lon,
        };
        if (Admin is not null) o["admin"] = Admin;
        if (Src is not null) o["src"] = Src;
        if (Name is not null) o["name"] = Name;
        return o;
    }

    private static long? Long(JsonObject o, string key) =>
        J.Num(o[key]) is double d ? (long?)d : null;
}
