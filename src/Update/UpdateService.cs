using System.Globalization;
using System.IO;
using System.Net.Http;
using System.Security.Cryptography;
using System.Text.Json.Nodes;
using System.Text.RegularExpressions;
using HavaDurumu.Core;
using HavaDurumu.Data;

namespace HavaDurumu.Update;

public sealed record ReleaseInfo(
    string Version,
    string? SetupUrl, long? SetupSize, string? SetupSha256,
    string? PageUrl, string Notes);

/// GitHub Releases üzerinden güncelleme: sürüm karşılaştırma, indirme ve doğrulama.
/// Bu sürüm kendini yalnızca kurulum dosyasıyla günceller (Python 1.4.1+ ile aynı).
/// ApplyUpdate, 1.3.x Python istemcilerinin zip'le gelen yeni exe'yi --apply-update ile çalıştırması içindir.
public static class UpdateService
{
    public static int[] ParseVersion(string? s) =>
        Regex.Matches(s ?? "", @"\d+")
            .Take(4)
            .Select(m => int.TryParse(m.Value, NumberStyles.None, CultureInfo.InvariantCulture, out var v) ? v : 0)
            .ToArray();

    /// Python tuple karşılaştırması: (1,3,1) < (1,3,1,0).
    public static bool IsNewer(string remote, string local)
    {
        var a = ParseVersion(remote);
        var b = ParseVersion(local);
        int n = Math.Min(a.Length, b.Length);
        for (int i = 0; i < n; i++)
        {
            if (a[i] != b[i]) return a[i] > b[i];
        }
        return a.Length > b.Length;
    }

    public static async Task<ReleaseInfo> GetLatestAsync(CancellationToken ct)
    {
        var j = J.Obj(await Net.GetJsonAsync(
            $"https://api.github.com/repos/{Constants.UpdateRepos[0]}/releases/latest",
            new[]
            {
                KeyValuePair.Create("User-Agent", "HavaDurumuWidget"),
                KeyValuePair.Create("Accept", "application/vnd.github+json"),
            }, ct).ConfigureAwait(false));

        var assets = J.Arr(j["assets"]).OfType<JsonObject>().ToList();
        (string? Url, long? Size, string? Sha) Asset(string name)
        {
            var a = assets.FirstOrDefault(x =>
                string.Equals(J.Str(x["name"]), name, StringComparison.OrdinalIgnoreCase));
            if (a is null) return (null, null, null);
            var digest = J.Str(a["digest"]);
            var size = J.Num(a["size"]) is double d ? (long?)d : null;
            var sha = digest.StartsWith("sha256:", StringComparison.Ordinal) ? digest[7..] : null;
            return (J.StrOrNull(a["browser_download_url"]), size, sha);
        }

        var setup = Asset(Constants.UpdateSetup);
        if (setup.Url is null) setup = Asset(Constants.UpdateSetupOld);   // yalnızca eski adla yüklenmiş sürüm
        return new ReleaseInfo(
            Version: J.Str(j["tag_name"]).TrimStart('v', 'V'),
            SetupUrl: setup.Url, SetupSize: setup.Size, SetupSha256: setup.Sha,
            PageUrl: J.StrOrNull(j["html_url"]),
            Notes: J.Str(j["body"]).Trim());
    }

    /// Yalnızca bu deponun (eski ya da yeni adıyla) GitHub indirme adresleri kabul edilir.
    public static void EnsureTrustedUrl(string url)
    {
        if (!Constants.UpdateRepos.Any(r => url.StartsWith($"https://github.com/{r}/releases/download/", StringComparison.Ordinal)))
            throw new InvalidDataException("beklenmeyen indirme adresi");
    }

    /// İndirir ve doğrular: adres, dosya imzası (MZ / PK), boyut ve varsa SHA-256.
    public static async Task DownloadAsync(
        string url, bool isZip, long? expectedSize, string? expectedSha256, string destPath, CancellationToken ct)
    {
        EnsureTrustedUrl(url);
        var magic = isZip ? new byte[] { 0x50, 0x4B } : new byte[] { 0x4D, 0x5A };

        using var req = new HttpRequestMessage(HttpMethod.Get, url);
        req.Headers.TryAddWithoutValidation("User-Agent", "HavaDurumuWidget");
        using var resp = await Net.Downloads.SendAsync(req, HttpCompletionOption.ResponseHeadersRead, ct)
            .ConfigureAwait(false);
        resp.EnsureSuccessStatusCode();

        using var src = await resp.Content.ReadAsStreamAsync(ct).ConfigureAwait(false);
        using var dst = File.Create(destPath);
        using var hasher = IncrementalHash.CreateHash(HashAlgorithmName.SHA256);
        var buffer = new byte[1 << 16];
        long total = 0;
        int read;
        while ((read = await src.ReadAsync(buffer.AsMemory(0, buffer.Length), ct).ConfigureAwait(false)) > 0)
        {
            if (total == 0 && (read < 2 || buffer[0] != magic[0] || buffer[1] != magic[1]))
                throw new InvalidDataException($"indirilen dosya bir {(isZip ? "zip" : "exe")} değil");
            await dst.WriteAsync(buffer.AsMemory(0, read), ct).ConfigureAwait(false);
            hasher.AppendData(buffer, 0, read);
            total += read;
        }

        if (expectedSize is long size && total != size)
            throw new InvalidDataException($"dosya boyutu tutmuyor ({total} / {size})");
        if (!string.IsNullOrEmpty(expectedSha256))
        {
            var actual = Convert.ToHexString(hasher.GetHashAndReset()).ToLowerInvariant();
            if (!string.Equals(actual, expectedSha256, StringComparison.OrdinalIgnoreCase))
                throw new InvalidDataException("SHA-256 doğrulaması başarısız");
        }
    }

    /// Yeni süreç başlatır (ebeveynden bağımsız).
    public static void Spawn(string exe, params string[] args)
    {
        var psi = new System.Diagnostics.ProcessStartInfo(exe) { UseShellExecute = false, WorkingDirectory = Path.GetDirectoryName(exe) };
        foreach (var a in args) psi.ArgumentList.Add(a);
        System.Diagnostics.Process.Start(psi);
    }

    /// Verilen süreç kapanana kadar bekler (en çok 20 sn) — Python wait_pid.
    public static void WaitPid(int pid)
    {
        try
        {
            using var p = System.Diagnostics.Process.GetProcessById(pid);
            p.WaitForExit(20_000);
        }
        catch (ArgumentException) { }   // süreç zaten kapanmış
    }

    /// Aynı exe'den çalışan diğer süreçleri kapatır (ek widget'lar dosyaları kilitli tutmasın).
    /// Ayarlarını her değişiklikte kaydettikleri için veri kaybı olmaz; ana widget onları yeniden açar.
    public static void StopOtherInstances(string exe)
    {
        int me = Environment.ProcessId;
        foreach (var p in System.Diagnostics.Process.GetProcessesByName(Path.GetFileNameWithoutExtension(exe)))
        {
            using (p)
            {
                try
                {
                    if (p.Id == me || !string.Equals(p.MainModule?.FileName, exe, StringComparison.OrdinalIgnoreCase)) continue;
                    p.Kill();
                    p.WaitForExit(5000);
                }
                catch (Exception ex) when (ex is InvalidOperationException or System.ComponentModel.Win32Exception) { }
            }
        }
    }

    /// Güncellemeden kalan eski dosyaları siler (Python cleanup_old_exe). Kilitli olanlar sonra denenir.
    public static bool CleanupOld(string appDir, string exe)
    {
        bool left = false;
        foreach (var p in new[] { exe + ".old", Path.Combine(appDir, "_internal.old"), Path.Combine(appDir, "_update"), Path.Combine(appDir, "_update.zip") })
        {
            try
            {
                if (Directory.Exists(p)) Directory.Delete(p, recursive: true);
                else if (File.Exists(p)) File.Delete(p);
            }
            catch (Exception ex) when (ex is IOException or UnauthorizedAccessException) { left = true; }
        }
        return left;
    }

    /// Klasör kipi güncellemesinin ikinci yarısı (Python apply_update; Python 1.3.x de bunu çağırır):
    /// bu süreç _update klasöründeki YENİ exe'dir. Eski sürüm kapanınca exe ve _internal'ı hedefe taşır,
    /// yeni sürümü başlatır. Bir adım başarısız olursa eski dosyalar geri konur ve eski sürüm başlar.
    public static void ApplyUpdate(string target, string? srcDir = null, bool launch = true)
    {
        var src = srcDir ?? Path.GetDirectoryName(Environment.ProcessPath)!;
        string exeT = Path.Combine(target, "HavaDurumu.exe"), intT = Path.Combine(target, "_internal");
        void ULog(string s)
        {
            try { File.AppendAllText(Path.Combine(target, "widget.log"), $"{DateTime.Now:yyyy-MM-dd HH:mm:ss} güncelleme: {s}{Environment.NewLine}"); }
            catch (Exception ex) when (ex is IOException or UnauthorizedAccessException) { }
        }
        bool movedInt = false, movedExe = false;
        try
        {
            if (Directory.Exists(intT + ".old")) Directory.Delete(intT + ".old", true);
            if (File.Exists(exeT + ".old")) File.Delete(exeT + ".old");
            for (int i = 0; ; i++)   // eski süreçlerin dosyaları bırakması için ~15 sn
            {
                try
                {
                    if (Directory.Exists(intT)) Directory.Move(intT, intT + ".old");
                    movedInt = true;
                    break;
                }
                catch (IOException) when (i < 75) { Thread.Sleep(200); }
            }
            File.Move(exeT, exeT + ".old");
            movedExe = true;
            CopyDir(Path.Combine(src, "_internal"), intT);
            File.Copy(Path.Combine(src, "HavaDurumu.exe"), exeT);
            ULog("tamamlandı");
        }
        catch (Exception ex)
        {
            ULog("başarısız, eski sürüme dönülüyor: " + ex);
            if (movedInt && Directory.Exists(intT + ".old"))
            {
                if (Directory.Exists(intT)) Directory.Delete(intT, true);
                Directory.Move(intT + ".old", intT);
            }
            if (movedExe)
            {
                if (File.Exists(exeT)) File.Delete(exeT);
                File.Move(exeT + ".old", exeT);
            }
        }
        if (launch) Spawn(exeT);
    }

    private static void CopyDir(string from, string to)
    {
        Directory.CreateDirectory(to);
        if (!Directory.Exists(from)) return;
        foreach (var f in Directory.GetFiles(from)) File.Copy(f, Path.Combine(to, Path.GetFileName(f)));
        foreach (var d in Directory.GetDirectories(from)) CopyDir(d, Path.Combine(to, Path.GetFileName(d)));
    }
}
