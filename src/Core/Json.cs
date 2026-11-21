using System.Text.Json.Nodes;

namespace HavaDurumu.Core;

/// System.Text.Json düğümleri için küçük yardımcılar. Eksik ya da bozuk alan
/// hata fırlatmaz; varsayılan döner (Python sürümündeki `.get(..) or ..` davranışı).
public static class J
{
    public static JsonObject Obj(JsonNode? n) => n as JsonObject ?? new JsonObject();

    public static JsonArray Arr(JsonNode? n) => n as JsonArray ?? new JsonArray();

    public static double? Num(JsonNode? n) =>
        n is JsonValue v && v.TryGetValue<double>(out var d) ? d : null;

    public static string? StrOrNull(JsonNode? n) =>
        n is JsonValue v && v.TryGetValue<string>(out var s) ? s : null;

    public static string Str(JsonNode? n) => StrOrNull(n) ?? "";

    /// Sayı ya da metin farketmeksizin metin karşılığı (MGM merkez kimlikleri için).
    public static string Scalar(JsonNode? n) =>
        n is JsonValue v ? (v.TryGetValue<string>(out var s) ? s : v.ToString()) : "";

    public static List<double?> Nums(JsonNode? n)
    {
        var list = new List<double?>();
        if (n is JsonArray a)
            foreach (var x in a) list.Add(Num(x));
        return list;
    }

    public static double? At(IReadOnlyList<double?> list, int i) =>
        i >= 0 && i < list.Count ? list[i] : null;
}
