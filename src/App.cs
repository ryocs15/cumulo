using System.IO;
using System.Security.Cryptography;
using System.Text;
using System.Text.RegularExpressions;
using System.Windows;
using HavaDurumu.Core;
using HavaDurumu.Ui;

namespace HavaDurumu;

/// Dosya yolları (Python APP_DIR, profile_paths). Ek widget'lar --profile AD ile ayrı ayar dosyası kullanır.
public sealed record AppPaths(string AppDir, string Profile, string Config, string Cache, string LogFile)
{
    public string Events => Path.Combine(AppDir, Profile.Length > 0 ? $"events_{Profile}.json" : "events.json");

    public static string ConfigFor(string appDir, string profile) =>
        Path.Combine(appDir, profile.Length > 0 ? $"config_{profile}.json" : "config.json");

    /// Ek widget profilleri: config_AD.json dosyaları.
    public List<string> ListProfiles() => Directory.GetFiles(AppDir, "config_*.json")
        .Select(f => Path.GetFileNameWithoutExtension(f)[7..])
        .Where(n => Regex.IsMatch(n, "^[A-Za-z0-9_-]{1,32}$")).OrderBy(n => n).ToList();

    public static AppPaths From(string[] args)
    {
        var dir = AppContext.BaseDirectory.TrimEnd('\\', '/');
        var profile = "";
        int i = Array.IndexOf(args, "--profile");
        if (i >= 0 && i + 1 < args.Length && Regex.IsMatch(args[i + 1], "^[A-Za-z0-9_-]{1,32}$"))
            profile = args[i + 1];
        var suf = profile.Length > 0 ? "_" + profile : "";
        return new AppPaths(dir, profile,
            Path.Combine(dir, $"config{suf}.json"),
            Path.Combine(dir, $"cache{suf}.json"),
            Path.Combine(dir, "widget.log"));
    }

    /// Python single_instance() ile aynı ad: aynı klasörde aynı profil ikinci kez açılmaz
    /// (Python sürümü aynı klasörde çalışıyorsa o da algılanır).
    public string MutexName
    {
        get
        {
            var hash = MD5.HashData(Encoding.UTF8.GetBytes(AppDir.ToLowerInvariant()));
            var tag = Convert.ToHexString(hash)[..8].ToLowerInvariant();
            return $"Local\\HavaDurumuWidget_{tag}_{(Profile.Length > 0 ? Profile : "main")}";
        }
    }
}

public sealed class App : Application
{
    private readonly AppPaths _paths;

    public App(AppPaths paths)
    {
        _paths = paths;
        ShutdownMode = ShutdownMode.OnMainWindowClose;
    }

    protected override void OnStartup(StartupEventArgs e)
    {
        base.OnStartup(e);
        Resources.MergedDictionaries.Add(new ResourceDictionary
        {
            Source = new Uri("pack://application:,,,/HavaDurumu;component/src/Ui/Cards.xaml"),
        });
        DispatcherUnhandledException += (_, ev) =>
        {
            Log.Write("beklenmeyen hata: " + ev.Exception);
            ev.Handled = true;
        };
        var win = new WidgetWindow(_paths);
        MainWindow = win;
        win.Show();
    }
}
