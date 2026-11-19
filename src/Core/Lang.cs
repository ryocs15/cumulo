using System.Globalization;
using System.IO;
using System.Reflection;
using System.Text.Json;
using System.Text.Json.Nodes;
using System.Text.RegularExpressions;

namespace HavaDurumu.Core;

/// Arayüz dili (Python T / LANG). Metinler kaynakta Türkçe yazılır; İngilizcesi iki gömülü sözlükten gelir:
/// lang_en.json (Python lang_en.py'den tools\export_lang.py ile üretilir, tek kaynak) ve
/// lang_en_wpf.json (yalnızca bu sürümdeki metinler). Dil açılışta bir kez okunur; değişince widget'lar
/// yeniden başlar (tablolar ilk kullanımda çevrilir).
public static class Lang
{
    private static Dictionary<string, string> _en = new();

    /// "tr" ya da "en".
    public static string Code { get; private set; } = "tr";

    /// Ana config.json'daki "lang"; yoksa Windows'un arayüz dili (Türkçe değilse İngilizce).
    public static void Init(string mainConfigPath)
    {
        var lang = "";
        try
        {
            if (File.Exists(mainConfigPath) && JsonNode.Parse(File.ReadAllText(mainConfigPath)) is JsonObject o)
                lang = o["lang"]?.GetValue<string>() ?? "";
        }
        catch (Exception ex) when (ex is IOException or JsonException or InvalidOperationException or UnauthorizedAccessException) { }
        if (lang is not ("tr" or "en"))
            lang = CultureInfo.CurrentUICulture.TwoLetterISOLanguageName == "tr" ? "tr" : "en";
        Code = lang;
        if (lang == "en") _en = LoadEn();
    }

    public static Dictionary<string, string> LoadEn()
    {
        var d = new Dictionary<string, string>();
        foreach (var name in new[] { "lang_en.json", "lang_en_wpf.json" })
        {
            using var s = Assembly.GetExecutingAssembly().GetManifestResourceStream("HavaDurumu." + name);
            if (s is null) continue;
            foreach (var (k, v) in JsonSerializer.Deserialize<Dictionary<string, string>>(s)!) d[k] = v;
        }
        return d;
    }

    /// Türkçe metnin seçili dildeki karşılığı (sözlükte yoksa Türkçesi).
    public static string T(string tr) => Code == "tr" ? tr : _en.GetValueOrDefault(tr, tr);

    /// Sözlükteki karşılığı bu yerde yanlış olan metin (Python T(tr, en)): "Açık" tema adı "Light", hava durumu "Clear".
    public static string T(string tr, string en) => Code == "tr" ? tr : en;

    /// Python str.format'ın kullanılan alt kümesi: {} , {:.1f} (sayı), {:%H:%M} (saat).
    /// Sözlük anahtarları Python'la aynı kalsın diye yer tutucular Python biçiminde yazılır.
    public static string F(this string fmt, params object?[] args)
    {
        int i = 0;
        return Placeholder.Replace(fmt, m =>
        {
            if (m.Value is "{{" or "}}") return m.Value[..1];
            var a = i < args.Length ? args[i++] : null;
            var spec = m.Groups[1].Value;
            if (spec.StartsWith('.') && spec.EndsWith('f') && a is IConvertible c)
                return c.ToDouble(CultureInfo.InvariantCulture).ToString("F" + spec[1..^1], CultureInfo.InvariantCulture);
            if (spec == "%H:%M" && a is DateTime dt) return dt.ToString("HH:mm", CultureInfo.InvariantCulture);
            return Convert.ToString(a, CultureInfo.InvariantCulture) ?? "";
        });
    }

    private static readonly Regex Placeholder = new(@"\{\{|\}\}|\{:?([^{}]*)\}", RegexOptions.Compiled);

    /// Yüzde: Türkçede "%71", İngilizcede "71%" (Python pct).
    public static string Pct(double v) =>
        Code == "tr" ? "%" + Math.Round(v).ToString(CultureInfo.InvariantCulture) : Math.Round(v).ToString(CultureInfo.InvariantCulture) + "%";
}
