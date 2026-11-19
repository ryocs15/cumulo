using System.IO;
using Microsoft.Win32;

namespace HavaDurumu.Core;

/// "Windows ile başlat": HKCU\...\Run kaydı. Python sürümü Başlangıç klasörüne bir VBS koyuyordu; temiz
/// Windows 11'de VBScript motoru olmadığı için (Microsoft kaldırıyor) o dosya çalışmaz. Eski VBS bu exe'yi
/// gösteriyorsa açılışta Run kaydına taşınır.
public static class Startup
{
    private const string RunKey = @"Software\Microsoft\Windows\CurrentVersion\Run";
    private const string ValueName = "Cumulo";

    private static string VbsPath => Path.Combine(
        Environment.GetFolderPath(Environment.SpecialFolder.Startup), "HavaDurumuWidget.vbs");

    private static string Exe => Environment.ProcessPath ?? "";

    public static bool Enabled
    {
        get
        {
            using var k = Registry.CurrentUser.OpenSubKey(RunKey);
            return k?.GetValue(ValueName) is string s && s.Trim('"').Equals(Exe, StringComparison.OrdinalIgnoreCase);
        }
    }

    public static void Set(bool on)
    {
        try
        {
            using var k = Registry.CurrentUser.CreateSubKey(RunKey);
            if (on) k.SetValue(ValueName, $"\"{Exe}\"");
            else k.DeleteValue(ValueName, throwOnMissingValue: false);
            if (File.Exists(VbsPath) && File.ReadAllText(VbsPath).Contains(Exe, StringComparison.OrdinalIgnoreCase))
                File.Delete(VbsPath);   // eski (Python) başlangıç dosyası
        }
        catch (Exception ex) when (ex is IOException or UnauthorizedAccessException or System.Security.SecurityException)
        {
            Log.Write("başlangıç ayarı değiştirilemedi: " + ex.Message);
        }
    }

    /// Python sürümünün bu exe için bıraktığı VBS varsa Run kaydına taşır (başka konumdaki kopyalara dokunmaz).
    public static void MigrateLegacy()
    {
        try
        {
            if (File.Exists(VbsPath) && File.ReadAllText(VbsPath).Contains(Exe, StringComparison.OrdinalIgnoreCase))
                Set(true);
        }
        catch (IOException ex)
        {
            Log.Write("eski başlangıç dosyası okunamadı: " + ex.Message);
        }
    }
}
