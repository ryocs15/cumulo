using System.IO;
using System.Text;
using System.Text.Encodings.Web;
using System.Text.Json;
using System.Text.Json.Nodes;

namespace HavaDurumu.Core;

/// config.json / config_AD.json okuyucu ve yazıcı. Python sürümüyle aynı dosya biçimi.
/// Bilinmeyen anahtarlar olduğu gibi korunur (sürümler arası uyumluluk).
/// Okuma hatalı ise (bozuk JSON) Save() dosyanın üzerine yazmayı reddeder.
public sealed class AppConfig
{
    private static readonly JsonSerializerOptions WriteOptions = new()
    {
        WriteIndented = true,
        // Python ensure_ascii=False ile yazdığı için Türkçe karakterler olduğu gibi kalsın.
        Encoder = JavaScriptEncoder.UnsafeRelaxedJsonEscaping,
    };

    private readonly JsonObject _root;

    private AppConfig(string filePath, JsonObject root, bool loadFailed)
    {
        FilePath = filePath;
        _root = root;
        LoadFailed = loadFailed;
    }

    public string FilePath { get; }

    public bool LoadFailed { get; }

    /// Dosyayı okur; yazmaz. Dosya yoksa boş ayarlarla döner.
    public static AppConfig Load(string filePath)
    {
        if (!File.Exists(filePath))
            return new AppConfig(filePath, new JsonObject(), loadFailed: false);
        try
        {
            var text = File.ReadAllText(filePath, Encoding.UTF8);
            var options = new JsonDocumentOptions
            {
                CommentHandling = JsonCommentHandling.Skip,
                AllowTrailingCommas = true,
            };
            var node = JsonNode.Parse(text, documentOptions: options) as JsonObject;
            return node is null
                ? new AppConfig(filePath, new JsonObject(), loadFailed: true)
                : new AppConfig(filePath, node, loadFailed: false);
        }
        catch (Exception ex) when (ex is IOException or JsonException or UnauthorizedAccessException)
        {
            return new AppConfig(filePath, new JsonObject(), loadFailed: true);
        }
    }

    public T Get<T>(string key, T fallback)
    {
        try
        {
            var node = _root[key];
            if (node is null) return fallback;
            var value = node.Deserialize<T>();
            return value is null ? fallback : value;
        }
        catch (Exception ex) when (ex is JsonException or InvalidOperationException or FormatException)
        {
            return fallback;
        }
    }

    public void Set<T>(string key, T value) => _root[key] = JsonSerializer.SerializeToNode(value);

    public List<Location> GetLocations()
    {
        var list = new List<Location>();
        if (_root["locs"] is JsonArray arr)
            foreach (var n in arr)
                if (n is JsonObject o) list.Add(Location.FromJson(o));
        return list;
    }

    public void SetLocations(IEnumerable<Location> locations)
    {
        var items = locations.Select(l => (JsonNode?)l.ToJson()).ToArray();
        _root["locs"] = new JsonArray(items);
    }

    /// Atomik yazma: önce .tmp'ye, sonra üzerine taşıma.
    public void Save()
    {
        if (LoadFailed)
            throw new InvalidOperationException("config.json okunamadı; üzerine yazılmadı.");
        var tmp = FilePath + ".tmp";
        File.WriteAllText(tmp, _root.ToJsonString(WriteOptions), new UTF8Encoding(false));
        File.Move(tmp, FilePath, overwrite: true);
    }

    public IEnumerable<string> Keys => _root.Select(kv => kv.Key);
}
