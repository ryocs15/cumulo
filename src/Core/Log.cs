using System.IO;

namespace HavaDurumu.Core;

/// widget.log'a satır ekler (Python log() ile aynı biçim). Yol atanmazsa sessizce atlar.
public static class Log
{
    public static string? FilePath { get; set; }

    private const long MaxBytes = 1 << 20;

    /// HAVADURUMU_DEBUG=1 iken yazılan ayrıntı kayıtları.
    public static readonly bool DebugOn = Environment.GetEnvironmentVariable("HAVADURUMU_DEBUG") == "1";

    public static void WriteDebug(string message)
    {
        if (DebugOn) Write(message);
    }

    public static void Write(string message)
    {
        if (FilePath is null) return;
        try
        {
            // ağ yokken dakikada bir hata yazılır (günde ~1,5 MB); 1 MB'ı geçince tek yedeğe taşınır
            if (File.Exists(FilePath) && new FileInfo(FilePath).Length > MaxBytes)
                File.Move(FilePath, FilePath + ".1", overwrite: true);
            File.AppendAllText(FilePath,
                $"{DateTime.Now:yyyy-MM-dd HH:mm:ss} {message}{Environment.NewLine}");
        }
        catch (Exception ex) when (ex is IOException or UnauthorizedAccessException)
        {
            // Günlük yazılamadıysa uygulama çalışmaya devam etsin.
        }
    }
}
