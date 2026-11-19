using System.Net.Http;
using System.Text.Json;
using System.Text.Json.Nodes;
using HavaDurumu.Core;

namespace HavaDurumu.Data;

/// HTTP yardımcıları. Python http_json() davranışı: 3 deneme, aralarında 1 sn / 2 sn bekleme.
public static class Net
{
    /// Kısa istekler için (15 sn zaman aşımı).
    public static readonly HttpClient Client = new() { Timeout = TimeSpan.FromSeconds(15) };

    /// Güncelleme indirmeleri için: zaman aşımı yok, iptal CancellationToken ile.
    public static readonly HttpClient Downloads = new() { Timeout = Timeout.InfiniteTimeSpan };

    public static async Task<JsonNode?> GetJsonAsync(
        string url,
        IEnumerable<KeyValuePair<string, string>>? headers = null,
        CancellationToken ct = default)
    {
        for (int attempt = 0; ; attempt++)
        {
            try
            {
                using var req = new HttpRequestMessage(HttpMethod.Get, url);
                if (headers is null)
                    req.Headers.TryAddWithoutValidation("User-Agent", "Mozilla/5.0");
                else
                    foreach (var kv in headers)
                        req.Headers.TryAddWithoutValidation(kv.Key, kv.Value);

                using var resp = await Client.SendAsync(req, ct).ConfigureAwait(false);
                resp.EnsureSuccessStatusCode();
                var text = await resp.Content.ReadAsStringAsync(ct).ConfigureAwait(false);
                return JsonNode.Parse(text);
            }
            catch (Exception ex) when (attempt < 2
                                       && !ct.IsCancellationRequested
                                       && (ex is HttpRequestException or TaskCanceledException or JsonException))
            {
                Log.Write($"ağ yeniden denenecek ({attempt + 1}): {ex.Message}");
                await Task.Delay(TimeSpan.FromSeconds(1 + attempt), ct).ConfigureAwait(false);
            }
        }
    }

    /// Sorgu dizgisi; değerler URL-kodlanır.
    public static string Query(params (string Key, string Value)[] items) =>
        string.Join("&", items.Select(i => $"{Uri.EscapeDataString(i.Key)}={Uri.EscapeDataString(i.Value)}"));
}
