using System.IO;
using System.Security;
using System.Windows.Media.Imaging;
using HavaDurumu.Core;
using Microsoft.Win32;
using Windows.Data.Xml.Dom;
using Windows.UI.Notifications;

namespace HavaDurumu.Ui;

/// Windows bildirimleri (WinRT toast) ve Başlat menüsü kısayolu.
/// Python sürümü bildirim başlığındaki uygulama adı için kısayola AUMID yazıyordu (COM); burada
/// Windows 10 1809+ ile gelen kayıt defteri yolu kullanılır (HKCU\Software\Classes\AppUserModelId).
public static class Notifier
{
    /// Testlerde başka ad verilir (Python HAVADURUMU_SHORTCUT ile aynı): gerçek kısayol ezilmesin.
    public static readonly string ShortcutName =
        Environment.GetEnvironmentVariable("HAVADURUMU_SHORTCUT") is { Length: > 0 } n ? n : Constants.AppName;

    /// AUMID eski kalır (güncelleme zinciri ve bildirim kaydı); testte ayrı ad.
    public static readonly string Aumid = ShortcutName == Constants.AppName ? "HavaDurumu.Widget" : "HavaDurumu.Widget.Test";

    public static string ShortcutPath => Path.Combine(
        Environment.GetFolderPath(Environment.SpecialFolder.Programs), ShortcutName + ".lnk");

    private static bool _registered;

    public static bool Show(string title, string text, string? code)
    {
        try
        {
            var icon = IconPng(code ?? "PB");
            Register(icon);
            var xml = new XmlDocument();
            xml.LoadXml("<toast><visual><binding template=\"ToastGeneric\"><text/><text/>" +
                        "<image placement=\"appLogoOverride\"/></binding></visual></toast>");
            var texts = xml.GetElementsByTagName("text");
            texts[0].AppendChild(xml.CreateTextNode(title));
            texts[1].AppendChild(xml.CreateTextNode(text));
            ((XmlElement)xml.GetElementsByTagName("image")[0]).SetAttribute("src", new Uri(icon).AbsoluteUri);
            ToastNotificationManager.CreateToastNotifier(Aumid).Show(new ToastNotification(xml));
            return true;
        }
        catch (Exception ex)
        {
            Log.Write("bildirim gönderilemedi: " + ex.Message);
            return false;
        }
    }

    /// Bildirimde görünen ad ve simge (Python'daki kısayolun yerine).
    private static void Register(string iconPath)
    {
        if (_registered) return;
        try
        {
            using var key = Registry.CurrentUser.CreateSubKey($@"Software\Classes\AppUserModelId\{Aumid}");
            key.SetValue("DisplayName", Constants.AppName);
            key.SetValue("IconUri", iconPath);
            _registered = true;
        }
        catch (Exception ex) when (ex is SecurityException or UnauthorizedAccessException or IOException)
        {
            Log.Write("bildirim kaydı yapılamadı: " + ex.Message);
        }
    }

    /// Bildirimde gösterilen hava ikonu (geçici klasörde PNG; bir kez üretilir).
    private static string IconPng(string code)
    {
        var dir = Path.Combine(Path.GetTempPath(), "HavaDurumuWidget");
        Directory.CreateDirectory(dir);
        var path = Path.Combine(dir, $"toast_{code}.png");
        if (File.Exists(path)) return path;
        var enc = new PngBitmapEncoder();
        enc.Frames.Add(BitmapFrame.Create(WeatherIcons.GetBitmap(code, false, false, 0, 96)));
        using var fs = File.Create(path);
        enc.Save(fs);
        return path;
    }

    /// Başlat menüsü kısayolu (config "start_shortcut"). Yalnızca bu uygulamanın oluşturduğu kısayol silinir.
    /// Dönen değer: config'e yazılacak hedef ("" = kısayol yok).
    public static string ApplyStartShortcut(bool on, string storedTarget)
    {
        var lnk = ShortcutPath;
        var exe = Environment.ProcessPath ?? "";
        var old = Path.Combine(Path.GetDirectoryName(lnk)!, "Hava Durumu.lnk");   // 1.4.1 ve öncesinin adı
        if (ShortcutName == Constants.AppName && File.Exists(old))   // testte gerçek kısayola dokunulmaz
        {
            try { File.Delete(old); storedTarget = ""; }
            catch (Exception ex) when (ex is IOException or UnauthorizedAccessException) { Log.Write("eski kısayol silinemedi: " + ex.Message); }
        }
        try
        {
            if (!on)
            {
                if (storedTarget.Length > 0 && File.Exists(lnk)) File.Delete(lnk);
                return "";
            }
            var shellType = Type.GetTypeFromProgID("WScript.Shell");
            if (shellType is null) return storedTarget;
            dynamic shell = Activator.CreateInstance(shellType)!;
            dynamic sc = shell.CreateShortcut(lnk);   // dosya varsa onu okur
            // Kısayolun gösterdiği yere bakılır, kayıtlı metne değil: Python hedefi tırnaklı yazıyordu, kurulumun
            // oluşturduğu kısayolda kayıt hiç yok. Doğru yeri gösteren kısayol (AUMID'iyle birlikte) olduğu gibi kalır.
            if (File.Exists(lnk) && string.Equals((string)sc.TargetPath, exe, StringComparison.OrdinalIgnoreCase)) return exe;
            sc.TargetPath = exe;
            sc.WorkingDirectory = Path.GetDirectoryName(exe);
            sc.IconLocation = exe;
            sc.Description = T("{} hava durumu widget'ı").F(Constants.AppName);
            sc.Save();
            return exe;
        }
        catch (Exception ex)
        {
            Log.Write("Başlat menüsü kısayolu güncellenemedi: " + ex.Message);
            return storedTarget;
        }
    }
}
