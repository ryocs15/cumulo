using System.Text.Json.Nodes;
using HavaDurumu.Core;

namespace HavaDurumu.Data;

/// MGM servisi (servis.mgm.gov.tr) — yalnızca Türkiye.
public static class MgmClient
{
    private static readonly KeyValuePair<string, string>[] Headers =
    {
        new("Origin", "https://www.mgm.gov.tr"),
        new("Referer", "https://www.mgm.gov.tr/"),
        new("User-Agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"),
        new("Accept", "application/json"),
    };

    public static Task<JsonNode?> ApiAsync(string path, CancellationToken ct, params (string Key, string Value)[] query)
    {
        var url = Constants.MgmApi + path;
        if (query.Length > 0)
            url += "?" + Net.Query(query);
        return Net.GetJsonAsync(url, Headers, ct);
    }
}
