using System.IO;
using System.Threading;
using HavaDurumu.Core;
using HavaDurumu.Ui;

namespace HavaDurumu;

internal static class Program
{
    [STAThread]
    private static int Main(string[] args)
    {
        if (args.Length == 1 && args[0] == "--version")
        {
            AttachParentConsole();
            Console.WriteLine(Constants.AppVersion);
            return 0;
        }

        if (args.Length == 1 && args[0] == "--toast-test")   // bildirim altyapısını doğrulamak için tek bildirim
        {
            AttachParentConsole();
            bool ok = Notifier.Show(Constants.AppName, "Bildirim testi", "A");
            Console.WriteLine(ok ? "bildirim gönderildi" : "bildirim gönderilemedi (widget.log)");
            Thread.Sleep(1500);   // süreç hemen kapanırsa bildirim düşmesin
            return ok ? 0 : 1;
        }

        if (args.Length == 1 && args[0] == "--selftest")
        {
            AttachParentConsole();
            return SelfTest.Run();
        }

        if (args.Length == 2 && args[0] == "--check-config")
        {
            AttachParentConsole();
            return CheckConfig(args[1]);
        }

        var paths = AppPaths.From(args);
        Lang.Init(AppPaths.ConfigFor(paths.AppDir, ""));   // dil ana widget'ın ayarından (ek widget'lar da aynı dilde)
        if (args.Length >= 2 && args[0] == "--snapshot")
        {
            AttachParentConsole();
            return Snapshot.Run(paths, args[1]);
        }

        // Python sürümleriyle aynı güncelleme protokolü: eski sürüm yeni exe'yi bu argümanlarla başlatır.
        int wi = Array.IndexOf(args, "--wait-pid");
        if (wi >= 0 && wi + 1 < args.Length && int.TryParse(args[wi + 1], out var waitPid))
            Update.UpdateService.WaitPid(waitPid);
        int ai = Array.IndexOf(args, "--apply-update");
        if (ai >= 0 && ai + 1 < args.Length && Directory.Exists(args[ai + 1]))
        {
            Update.UpdateService.ApplyUpdate(args[ai + 1]);   // dosyaları taşı, yeni sürümü başlat, çık
            return 0;
        }

        using var mutex = new Mutex(false, paths.MutexName, out bool createdNew);
        if (!createdNew) return 0;   // aynı klasörde aynı widget zaten açık

        var app = new App(paths);
        return app.Run();
    }

    /// WinExe olduğu için konsol yok; komut satırından çağrıldıysa çıktıyı o konsola yaz.
    private static void AttachParentConsole()
    {
        if (Native.AttachConsole(-1))
        {
            var stdout = new StreamWriter(Console.OpenStandardOutput(), new System.Text.UTF8Encoding(false)) { AutoFlush = true };
            Console.SetOut(stdout);
        }
    }

    /// Salt okunur kontrol: config.json'ı okur, özetler, hiçbir şey yazmaz.
    private static int CheckConfig(string path)
    {
        var cfg = AppConfig.Load(path);
        if (cfg.LoadFailed)
        {
            Console.WriteLine($"HATA: {path} okunamadı (bozuk JSON olabilir).");
            return 2;
        }

        var locs = cfg.GetLocations();
        Console.WriteLine($"Dosya        : {path}");
        Console.WriteLine($"Anahtar sayısı: {cfg.Keys.Count()}");
        Console.WriteLine($"Konum sayısı : {locs.Count} (Open-Meteo: {locs.Count(l => l.IsOpenMeteo)})");
        Console.WriteLine($"Seçili düzen : {cfg.Get("layout", "?")}");
        Console.WriteLine($"Tema / ölçek : {cfg.Get("theme", "?")} / {cfg.Get("scale", 1.0)}");
        Console.WriteLine($"Yenileme dk  : {cfg.Get("refresh_min", 15)}");
        Console.WriteLine($"Otomatik güncelleme: {cfg.Get("auto_update", true)}");
        return 0;
    }
}
