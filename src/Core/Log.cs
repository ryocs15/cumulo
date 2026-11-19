using System.IO;

namespace HavaDurumu.Core;

/// widget.log'a satır ekler (Python log() ile aynı biçim). Yol atanmazsa sessizce atlar.
public static class Log
{
    public static string? FilePath { get; set; }

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
            File.AppendAllText(FilePath,
                $"{DateTime.Now:yyyy-MM-dd HH:mm:ss} {message}{Environment.NewLine}");
        }
        catch (Exception ex) when (ex is IOException or UnauthorizedAccessException)
        {
            // Günlük yazılamadıysa uygulama çalışmaya devam etsin.
        }
    }
}
