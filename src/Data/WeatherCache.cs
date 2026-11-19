using System.IO;
using System.Text;
using System.Text.Encodings.Web;
using System.Text.Json;
using System.Text.Json.Nodes;
using HavaDurumu.Core;

namespace HavaDurumu.Data;

/// cache.json: {"by_id": {merkezId: veri}, "alerts": [...]}. Eski tek konumlu biçim de okunur.
/// Python sürümüyle aynı dosya biçimi.
public sealed class WeatherCache
{
    private static readonly JsonSerializerOptions WriteOptions = new()
    {
        Encoder = JavaScriptEncoder.UnsafeRelaxedJsonEscaping,
    };

    public WeatherCache(string filePath) => FilePath = filePath;

    public string FilePath { get; }

    public Dictionary<string, WeatherData> ById { get; } = new();

    public JsonArray Alerts { get; set; } = new();

    public void Load()
    {
        ById.Clear();
        Alerts = new JsonArray();
        try
        {
            if (!File.Exists(FilePath)) return;
            if (JsonNode.Parse(File.ReadAllText(FilePath, Encoding.UTF8)) is not JsonObject root) return;
            if (root.ContainsKey("merkezId"))
                root = new JsonObject { ["by_id"] = new JsonObject { [J.Scalar(root["merkezId"])] = root.DeepClone() } };
            foreach (var (key, node) in J.Obj(root["by_id"]))
            {
                if (node is not JsonObject o) continue;
                var copy = (JsonObject)o.DeepClone();
                WeatherService.FixCode(copy);
                ById[key] = new WeatherData(copy);
            }
            Alerts = (JsonArray)J.Arr(root["alerts"]).DeepClone();
        }
        catch (Exception ex) when (ex is IOException or JsonException or UnauthorizedAccessException)
        {
            Log.Write("önbellek okunamadı: " + ex.Message);
        }
    }

    /// Yalnızca listede kalan konumların verisi yazılır (Python save_cache).
    public void Save(IEnumerable<string> keepKeys)
    {
        var keep = keepKeys.ToHashSet();
        var byId = new JsonObject();
        foreach (var (k, v) in ById)
            if (keep.Contains(k)) byId[k] = v.Root.DeepClone();
        var root = new JsonObject { ["by_id"] = byId, ["alerts"] = Alerts.DeepClone() };
        try
        {
            var tmp = FilePath + ".tmp";
            File.WriteAllText(tmp, root.ToJsonString(WriteOptions), new UTF8Encoding(false));
            File.Move(tmp, FilePath, overwrite: true);
        }
        catch (Exception ex) when (ex is IOException or UnauthorizedAccessException)
        {
            Log.Write("önbellek yazılamadı: " + ex.Message);
        }
    }
}
