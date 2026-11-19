using System.IO;
using System.Runtime.InteropServices;
using System.Text.Json.Nodes;
using System.Text.RegularExpressions;
using System.Windows;
using System.Windows.Interop;
using System.Windows.Threading;
using HavaDurumu.Core;
using HavaDurumu.Data;
using HavaDurumu.Update;

namespace HavaDurumu.Ui;

/// Pencerenin pencere-dışı özellikleri: bildirimler, olay günlüğü, kısayol tuşu, ek widget'lar,
/// ayar aktarımı, güncelleme. Python WeatherWidget'ın ilgili bölümlerinin karşılığı.
public partial class WidgetWindow
{
    private EventLog _events = null!;
    private readonly Dictionary<string, object> _notified = new();      // yağış / UV bildirimi tekrarlanmasın
    private readonly Dictionary<string, string?> _thrState = new();      // eşik durumu (low / high / null)
    private ReleaseInfo? _updateInfo;
    private bool _updating;
    private bool? _hotkeyOk;
    private bool _peeking;
    private bool _locked, _away, _displayOff;   // ekran kilidi, oturum başka kullanıcıda / uzakta, ekran kapalı
    private IntPtr _powerNotify;
    private DateTime _exeMtime;
    private readonly DispatcherTimer _peekTimer = new();
    private readonly DispatcherTimer _updateTimer = new() { Interval = TimeSpan.FromHours(24) };
    private readonly DispatcherTimer _cleanupTimer = new() { Interval = TimeSpan.FromSeconds(15) };
    private int _cleanupTries = 12;

    private bool IsMain => _paths.Profile.Length == 0;
    private static string ExePath => Environment.ProcessPath ?? "";

    private static readonly (string Key, string Label, uint Mods, uint Vk)[] Hotkeys =
    {
        ("win_shift_w", "Win+Shift+W", 0x8 | 0x4, 0x57),
        ("ctrl_alt_w", "Ctrl+Alt+W", 0x2 | 0x1, 0x57),
        ("ctrl_shift_h", "Ctrl+Shift+H", 0x2 | 0x4, 0x48),
        ("off", T("Kapalı"), 0, 0),
    };

    /// OnSourceInitialized sonunda bir kez.
    private void InitFeatures()
    {
        _events = new EventLog(_paths.Events);
        try { _exeMtime = File.GetLastWriteTimeUtc(ExePath); } catch (IOException) { }
        _peekTimer.Tick += (_, _) => PeekTimeout();
        _updateTimer.Tick += async (_, _) => { if (_cfg.Get("auto_update", true)) await CheckUpdateAsync(false); };
        _cleanupTimer.Tick += (_, _) =>
        {
            if (!UpdateService.CleanupOld(_paths.AppDir, ExePath) || --_cleanupTries <= 0) _cleanupTimer.Stop();
        };
        HwndSource.FromHwnd(_hwnd)?.AddHook(WndProc);
        Microsoft.Win32.SystemEvents.SessionSwitch += OnSessionSwitch;
        var g = Native.GuidConsoleDisplayState;
        _powerNotify = Native.RegisterPowerSettingNotification(_hwnd, ref g, 0);   // ekran açılıp kapanınca WM_POWERBROADCAST
        if (IsMain)
        {
            if (UpdateService.CleanupOld(_paths.AppDir, ExePath)) _cleanupTimer.Start();   // eski süreçler kapanınca yeniden dener
            ApplyHotkey();
            ApplyStartShortcut();
            Startup.MigrateLegacy();
            SpawnProfiles();
            _updateTimer.Start();
            if (Environment.GetCommandLineArgs().Contains("--update"))   // HavaDurumu.exe --update: varsa sormadan kur
                Dispatcher.BeginInvoke(async () => await CheckUpdateAsync(false, install: true), DispatcherPriority.ApplicationIdle);
            else if (_cfg.Get("auto_update", true))
                Dispatcher.BeginInvoke(async () => await CheckUpdateAsync(false), DispatcherPriority.ApplicationIdle);
        }
    }

    /// Dakikada bir (saat çizimiyle birlikte): sabah özeti, ek widget güncellendiyse yeniden başlama.
    private void MinuteTick()
    {
        try { MorningCheck(); }
        catch (Exception ex) { Log.Write("sabah özeti: " + ex.Message); }
        RestartIfExeChanged();
    }

    // ------------------------------------------------------------------ bildirimler
    private void Notify(string title, string text, string? code = null) => Notifier.Show(title, text, code);

    /// Sessiz saatlerde mi? (Python in_quiet)
    private bool InQuiet()
    {
        if (!_cfg.Get("quiet_on", false)) return false;
        if (ParseHhmm(_cfg.Get("quiet_start", "23:00")) is not int s || ParseHhmm(_cfg.Get("quiet_end", "07:00")) is not int e)
            return false;
        var n = DateTime.Now;
        int cur = n.Hour * 60 + n.Minute;
        return s < e ? s <= cur && cur < e : cur >= s || cur < e;
    }

    /// "SS:DD" -> dakika (geçersizse null).
    public static int? ParseHhmm(string? s)
    {
        var m = Regex.Match(s ?? "", @"^\s*(\d{1,2})[:.](\d{2})\s*$");
        if (!m.Success) return null;
        int h = int.Parse(m.Groups[1].Value), mi = int.Parse(m.Groups[2].Value);
        return h > 23 || mi > 59 ? null : h * 60 + mi;
    }

    /// Yenileme bitince: olay günlüğü ve bildirimler (Python _got + _done_loading + check_notifications).
    private void AfterRefresh()
    {
        try
        {
            foreach (var l in _locs)
            {
                if (_cache.ById.TryGetValue(l.Key, out var d)) _events.Observe(l, d);
                _events.ObserveAlerts(l, _cache.Alerts);
            }
            _events.Save();
        }
        catch (Exception ex) { Log.Write("olay günlüğü: " + ex.Message); }
        try { CheckNotifications(); }
        catch (Exception ex) { Log.Write("bildirimler: " + ex.Message); }
    }

    /// Yağış yaklaşması, sıcaklık eşiği, UV ve yeni MGM uyarıları için bildirim (Python check_notifications).
    private void CheckNotifications()
    {
        if (InQuiet()) return;   // sessiz saatler: hiçbir şey "görüldü" sayılmaz, sonra gelir
        var now = DateTime.UtcNow;
        var msgs = new List<(string, string, string?)>();
        var seen = _cfg.Get("seen_alerts", new List<string>());
        int seenN = seen.Count;
        double? lo = _cfg.Get<double?>("alert_low", null), hi = _cfg.Get<double?>("alert_high", null);
        foreach (var loc in _locs)
        {
            var k = loc.Key;
            if (!_cache.ById.TryGetValue(k, out var data) || data.Cur is not JsonObject cur) continue;
            var name = Fmt.LocTitle(loc);
            if (_cfg.Get("notify_rain", true) && !Fmt.IsWet(J.Str(cur["hadiseKodu"])))
            {
                foreach (var e in data.Hourly.OfType<JsonObject>())
                {
                    if (!AlertService.TryParseUtc(J.StrOrNull(e["tarih"]), out var t)) continue;
                    var hc = J.Str(e["hadise"]);
                    if (now < t && t <= now.AddHours(3) && Fmt.IsWet(hc))
                    {
                        if (!_notified.TryGetValue("rain:" + k, out var last) || (now - (DateTime)last).TotalHours > 6)
                        {
                            _notified["rain:" + k] = now;
                            msgs.Add((T("{}: yağış yaklaşıyor").F(name),
                                T("{:%H:%M} civarında yağış bekleniyor ({}).").F(t.ToLocalTime(), Constants.Hadise.TryGetValue(hc, out var hn) ? hn : T("Yağışlı")), hc));
                        }
                        break;
                    }
                }
            }
            var tv = J.Num(cur["sicaklik"]);
            string? state = null;
            if (Fmt.Ok(tv)) state = lo is double l && tv <= l ? "low" : hi is double h && tv >= h ? "high" : null;
            _thrState.TryGetValue(k, out var prev);
            if (state != prev)
            {
                _thrState[k] = state;
                if (state == "low") msgs.Add(($"{name}: {Fmt.Temp(tv)}", T("Sıcaklık alt eşiğin ({}) altına düştü.").F(Fmt.Temp(lo)), "SGK"));
                if (state == "high") msgs.Add(($"{name}: {Fmt.Temp(tv)}", T("Sıcaklık üst eşiğin ({}) üstüne çıktı.").F(Fmt.Temp(hi)), "SCK"));
            }
            var uvn = J.Num(data.Extra["uv_now"]);
            var day = DateTime.Now.ToString("yyyy-MM-dd");
            if (_cfg.Get("notify_uv", true) && uvn >= 8 && !Equals(_notified.GetValueOrDefault("uv:" + k), day))
            {
                _notified["uv:" + k] = day;
                msgs.Add((T("{}: UV çok yüksek").F(name), T("UV indeksi {:.0f} ({}). Güneş kremi ve şapka önerilir.").F(uvn!.Value, Fmt.UvLevel(uvn.Value)), "SCK"));
            }
            if (_cfg.Get("notify_alerts", true))
                foreach (var a in AlertService.AlertsFor(loc, _cache.Alerts, now))
                {
                    var sid = $"{a.No}:{k}";
                    if (seen.Contains(sid)) continue;
                    seen.Add(sid);
                    var body = a.Kinds.Count > 0 ? string.Join(", ", a.Kinds) : a.Text[..Math.Min(200, a.Text.Length)];
                    msgs.Add((T("{}: {} uyarı").F(name, a.ColorName), body, J.Str(cur["hadiseKodu"])));
                }
        }
        if (seen.Count != seenN)
        {
            _cfg.Set("seen_alerts", seen.TakeLast(200).ToList());
            TrySave();
        }
        for (int i = 0; i < Math.Min(4, msgs.Count); i++)   // art arda gelmesin: 5 sn arayla
        {
            var (t, b, c) = msgs[i];
            Delay(i * 5000, () => Notify(t, b, c));
        }
    }

    private static void Delay(int ms, Action act)
    {
        if (ms <= 0) { act(); return; }
        var t = new DispatcherTimer { Interval = TimeSpan.FromMilliseconds(ms) };
        t.Tick += (_, _) => { t.Stop(); act(); };
        t.Start();
    }

    /// Sabah özeti ve/veya şemsiye-mont hatırlatması (Python morning_message).
    private (string Title, string Text, string Code)? MorningMessage(Location loc)
    {
        if (!_cache.ById.TryGetValue(loc.Key, out var data) || data.Cur is not JsonObject cur) return null;
        var extra = data.Extra;
        var days = data.DailyRows();
        var localToday = DateOnly.FromDateTime(Fmt.NowLocal(data.UtcOffset));
        (DateOnly Day, string Code, double Lo, double Hi)? today = days.Count > 0 && days[0].Day == localToday ? days[0] : null;
        var code = today?.Code ?? J.Str(cur["hadiseKodu"]);
        var lines = new List<string>();
        var tips = new List<string>();
        bool morning = _cfg.Get("morning_on", true), reminders = _cfg.Get("reminders_on", true);
        if (morning)
        {
            var s = Fmt.Hadise(code);
            if (today is { } td) s += $", {Fmt.Tint(td.Lo)}° / {Fmt.Tint(td.Hi)}°";
            lines.Add(s);
            if (J.Num(extra["rain_prob"]) is double rp) lines.Add(T("Yağış ihtimali") + " " + Pct(rp));
            if (J.Num(extra["european_aqi"]) is double aq) lines.Add(T("Hava kalitesi: {}").F(Fmt.AqiLevel(aq).Name));
        }
        if (reminders)
        {
            bool wet = (J.Num(extra["rain_prob"]) ?? 0) >= 50 || Fmt.IsWet(code);
            foreach (var (utc, hc, _) in data.HourlyRows(DateTime.UtcNow))
            {
                var lt = Fmt.LocalTime(utc, data.UtcOffset);
                if (DateOnly.FromDateTime(lt) == localToday && lt.Hour is >= 7 and <= 22 && Fmt.IsWet(hc)) wet = true;
            }
            if (wet) tips.Add(T("☂ Şemsiyeni al"));
            var fl = J.Num(cur["hissedilenSicaklik"]);
            if (today is { Lo: <= 5 } || (Fmt.Ok(fl) && fl <= 3)) tips.Add(T("🧥 Kalın giyin"));
            if (today is { Hi: >= 33 }) tips.Add(T("Bol su iç, öğle güneşinden kaçın"));
            if ((J.Num(extra["uv_max"]) ?? 0) >= 6) tips.Add(T("UV yüksek, güneş kremi sür"));
        }
        if (lines.Count == 0 && tips.Count == 0) return null;
        var title = (morning ? T("Günaydın — {}") : T("Hatırlatma — {}")).F(Fmt.LocTitle(loc));
        return (title, string.Join("\n", lines.Concat(tips)), code);
    }

    /// Ayarlanan saatte (3 saat içinde) günde bir kez sabah özeti (Python morning_check).
    private void MorningCheck()
    {
        if (!(_cfg.Get("morning_on", true) || _cfg.Get("reminders_on", true)) || !IsMain) return;
        if (ParseHhmm(_cfg.Get("morning_time", "07:30")) is not int hm) return;
        var now = DateTime.Now;
        var start = now.Date.AddMinutes(hm);
        var today = now.ToString("yyyy-MM-dd");
        if (_cfg.Get("last_morning", "") == today || now < start || now >= start.AddHours(3) || InQuiet()) return;
        if (MorningMessage(_locs[0]) is not { } msg) return;   // veri henüz gelmedi: sonraki dakikada yeniden
        _cfg.Set("last_morning", today);
        TrySave();
        Notify(msg.Title, msg.Text, msg.Code);
    }

    private void NotifyMorningNow()
    {
        var m = MorningMessage(_locs[0]);
        if (m is { } msg) Notify(msg.Title, msg.Text, msg.Code);
        else Notify(T("Sabah özeti"), T("Veri henüz gelmedi."));
    }

    private void ApplyStartShortcut()
    {
        if (!IsMain) return;
        var target = Notifier.ApplyStartShortcut(_cfg.Get("start_shortcut", true), _cfg.Get("shortcut_target", ""));
        if (target != _cfg.Get("shortcut_target", ""))
        {
            _cfg.Set("shortcut_target", target);
            TrySave();
        }
    }

    // ------------------------------------------------------------------ kısayol tuşu ve "öne getir"
    private IntPtr WndProc(IntPtr hwnd, int msg, IntPtr wParam, IntPtr lParam, ref bool handled)
    {
        if (msg == 0x0312)   // WM_HOTKEY
        {
            handled = true;
            Peek();
        }
        else if (msg == 0x0218 && wParam == 0x8013 && lParam != IntPtr.Zero   // WM_POWERBROADCAST, PBT_POWERSETTINGCHANGE
                 && Marshal.PtrToStructure<Guid>(lParam) == Native.GuidConsoleDisplayState)
        {
            _displayOff = Marshal.ReadInt32(lParam, 20) == 0;   // POWERBROADCAST_SETTING.Data: 0 kapalı, 1 açık, 2 kısık
            Log.WriteDebug($"ekran durumu: {Marshal.ReadInt32(lParam, 20)} (0 kapalı, 1 açık, 2 kısık)");
            UpdateSuspended();
        }
        return IntPtr.Zero;
    }

    /// SystemEvents kendi iş parçacığından çağırır; durum pencerenin iş parçacığında değişir.
    private void OnSessionSwitch(object? sender, Microsoft.Win32.SessionSwitchEventArgs e) => Dispatcher.BeginInvoke(() =>
    {
        switch (e.Reason)
        {
            case Microsoft.Win32.SessionSwitchReason.SessionLock: _locked = true; break;
            case Microsoft.Win32.SessionSwitchReason.SessionUnlock: _locked = false; break;
            case Microsoft.Win32.SessionSwitchReason.ConsoleDisconnect or Microsoft.Win32.SessionSwitchReason.RemoteDisconnect: _away = true; break;
            case Microsoft.Win32.SessionSwitchReason.ConsoleConnect or Microsoft.Win32.SessionSwitchReason.RemoteConnect: _away = false; break;
        }
        Log.WriteDebug("oturum: " + e.Reason);
        UpdateSuspended();
    });

    private void UpdateSuspended() => _animator.Suspended = _locked || _away || _displayOff;

    private void StopSystemNotifications()
    {
        Microsoft.Win32.SystemEvents.SessionSwitch -= OnSessionSwitch;   // statik olay: kapanınca bırakılmalı
        if (_powerNotify != IntPtr.Zero) Native.UnregisterPowerSettingNotification(_powerNotify);
        _powerNotify = IntPtr.Zero;
    }

    private void ApplyHotkey()
    {
        if (!IsMain) return;
        Native.UnregisterHotKey(_hwnd, 1);
        var key = _cfg.Get("hotkey", "win_shift_w");
        var spec = Hotkeys.FirstOrDefault(h => h.Key == key);
        if (spec.Key is null) spec = Hotkeys[0];
        if (spec.Key == "off")
        {
            _hotkeyOk = null;
            return;
        }
        _hotkeyOk = Native.RegisterHotKey(_hwnd, 1, spec.Mods | 0x4000, spec.Vk);
        if (_hotkeyOk == false && !_cfg.Get("hotkey_touched", false))
        {
            // kullanıcı seçmediyse: varsayılan doluysa sıradaki boş kısayola geç
            foreach (var alt in Hotkeys.Where(h => h.Key != "off" && h.Key != spec.Key))
                if (Native.RegisterHotKey(_hwnd, 1, alt.Mods | 0x4000, alt.Vk))
                {
                    Log.Write($"kısayol {spec.Label} kullanımda; {alt.Label} kullanılıyor");
                    _cfg.Set("hotkey", alt.Key);
                    TrySave();
                    _hotkeyOk = true;
                    return;
                }
        }
        if (_hotkeyOk == false) Log.Write("kısayol kaydedilemedi (başka bir uygulama kullanıyor olabilir): " + spec.Label);
    }

    /// Kısayol: widget'ı geçici olarak bütün pencerelerin üstüne getirir; tekrar basınca geri.
    private void Peek()
    {
        if (_peeking)
        {
            EndPeek();
            return;
        }
        _peeking = true;
        Native.PinOnTop(_hwnd);
        UpdateAnimation();   // artık görünür: animasyon yeniden denetlensin
        _peekTimer.Interval = TimeSpan.FromSeconds(15);
        _peekTimer.Start();
    }

    private void PeekTimeout()
    {
        if (CursorOnWindow()) _peekTimer.Interval = TimeSpan.FromSeconds(3);
        else EndPeek();
    }

    private void EndPeek()
    {
        _peekTimer.Stop();
        _peeking = false;
        ApplyZOrder();
    }

    // ------------------------------------------------------------------ ek widget'lar
    private void SpawnProfiles()
    {
        foreach (var name in _paths.ListProfiles())
        {
            try { UpdateService.Spawn(ExePath, "--profile", name); }
            catch (Exception ex) { Log.Write($"ek widget açılamadı ({name}): {ex.Message}"); }
        }
    }

    /// Yeni widget: başka ekran varsa onun sağ üstüne, yoksa bu widget'ın yanına (Python add_widget).
    private void AddWidget()
    {
        var names = _paths.ListProfiles().ToHashSet();
        int i = 1;
        while (names.Contains($"w{i}")) i++;
        var name = $"w{i}";
        int cw = Px(150);
        var here = Native.WorkArea(_x + _w / 2, _y + _h / 2);
        int x = _x - cw - Px(16) >= here.Left ? _x - cw - Px(16) : _x + _w + Px(16), y = _y;
        var cfg = new JsonObject
        {
            ["locs"] = new JsonArray(Loc.ToJson()), ["idx"] = 0, ["layout"] = "mini", ["x"] = x, ["y"] = y,
            ["scale"] = _cfg.Get("scale", 1.0), ["theme"] = _cfg.Get("theme", "auto"), ["opacity"] = _cfg.Get("opacity", 1.0),
            ["radius"] = _cfg.Get("radius", 22.0), ["custom_color"] = _cfg.Get<string?>("custom_color", null),
            ["bg_image"] = _cfg.Get<string?>("bg_image", null), ["units_temp"] = _cfg.Get("units_temp", "C"),
            ["units_wind"] = _cfg.Get("units_wind", "kmh"), ["tray"] = false, ["notify_rain"] = false,
            ["notify_alerts"] = false, ["notify_uv"] = false, ["morning_on"] = false, ["reminders_on"] = false,
            ["auto_update"] = false, ["hotkey"] = "off", ["rotate_touched"] = true,
        };
        File.WriteAllText(AppPaths.ConfigFor(_paths.AppDir, name), cfg.ToJsonString(new() { WriteIndented = true }));
        UpdateService.Spawn(ExePath, "--profile", name);
    }

    private void RemoveWidget()
    {
        if (IsMain) return;
        if (MessageBox.Show(T("Bu ek widget kapatılıp ayarları silinsin mi?\nAna widget ve diğer widget'lar etkilenmez."),
                T("Widget'ı kaldır"), MessageBoxButton.YesNo, MessageBoxImage.Question) != MessageBoxResult.Yes) return;
        foreach (var p in new[] { _paths.Config, _paths.Cache, _paths.Events })
        {
            try { File.Delete(p); }
            catch (Exception ex) when (ex is IOException or UnauthorizedAccessException) { }
        }
        Close();
    }

    /// Dil değişti: tablolar ilk kullanımda çevrildiği için bütün widget'lar yeniden başlar
    /// (ana widget açılınca ek widget'ları da açar; Python restart_for_lang).
    private void SetLang(string code)
    {
        if (!IsMain || code == Lang.Code) return;
        _cfg.Set("lang", code);
        TrySave();
        UpdateService.StopOtherInstances(ExePath);
        UpdateService.Spawn(ExePath, "--wait-pid", Environment.ProcessId.ToString());
        Close();
    }

    /// Ana widget güncellendiyse ek widget'lar da yeni sürümle yeniden başlar.
    private void RestartIfExeChanged()
    {
        if (IsMain || _exeMtime == default) return;
        DateTime m;
        try { m = File.GetLastWriteTimeUtc(ExePath); } catch (IOException) { return; }
        if (m == _exeMtime) return;
        UpdateService.Spawn(ExePath, "--profile", _paths.Profile, "--wait-pid", Environment.ProcessId.ToString());
        Close();
    }

    // ------------------------------------------------------------------ ayar aktarımı
    private static readonly HashSet<string> ExportSkip = new() { "x", "y", "idx", "seen_alerts", "last_morning", "seen_update" };

    private void ExportSettings()
    {
        var dlg = new Microsoft.Win32.SaveFileDialog
        {
            Title = T("Ayarları dışa aktar"), FileName = "hava-durumu-ayarlar.json", DefaultExt = ".json", Filter = "Ayar dosyası|*.json",
        };
        if (dlg.ShowDialog() != true) return;
        var data = new JsonObject();
        foreach (var k in _cfg.Keys.Where(k => !ExportSkip.Contains(k))) data[k] = _cfg.Get<JsonNode?>(k, null)?.DeepClone();
        data["_app"] = "HavaDurumuWidget";
        data["_version"] = Constants.AppVersion;
        File.WriteAllText(dlg.FileName, data.ToJsonString(new() { WriteIndented = true, Encoder = System.Text.Encodings.Web.JavaScriptEncoder.UnsafeRelaxedJsonEscaping }));
    }

    private void ImportSettings()
    {
        var dlg = new Microsoft.Win32.OpenFileDialog { Title = T("Ayarları içe aktar"), Filter = "Ayar dosyası|*.json" };
        if (dlg.ShowDialog() != true) return;
        var data = AppConfig.Load(dlg.FileName);
        var locs = data.GetLocations();
        // güven sınırı: başka bir dosyadan geliyor — konumlar geçerli olmadan hiçbir şey yazılmaz
        if (data.LoadFailed || locs.Count == 0 || locs.Any(l => l.MerkezId is null))
        {
            MessageBox.Show(T("Bu dosya geçerli bir ayar dosyası değil."), T("Ayarları içe aktar"), MessageBoxButton.OK, MessageBoxImage.Warning);
            return;
        }
        var names = string.Join(", ", locs.Take(6).Select(Fmt.LocTitle)) + (locs.Count > 6 ? " …" : "");
        if (MessageBox.Show(T("Bütün ayarlar bu dosyadakilerle değiştirilsin mi?\n\nŞehirler: {}").F(names), T("Ayarları içe aktar"),
                MessageBoxButton.YesNo, MessageBoxImage.Question) != MessageBoxResult.Yes) return;
        foreach (var k in data.Keys.Where(k => !ExportSkip.Contains(k) && !k.StartsWith('_')))
            _cfg.Set(k, data.Get<JsonNode?>(k, null)?.DeepClone());
        _cfg.Set("idx", 0);
        TrySave();
        _locs = _cfg.GetLocations();
        _idx = 0;
        _cache.ById.Clear();
        _errors.Clear();
        ApplySettingsChanged(relayout: true);
        ApplyHotkey();
        _ = RefreshAsync();
    }

    // ------------------------------------------------------------------ tema seçimleri
    private static readonly (string Hex, string Name)[] Swatches =
    {
        ("#3b6ea5", T("Mavi")), ("#2e7d5b", T("Yeşil")), ("#1f7a8c", T("Turkuaz")), ("#7a3fa0", T("Mor")),
        ("#b23a48", T("Kırmızı")), ("#c46a1c", T("Turuncu")), ("#555c66", T("Gri")),
    };

    private void ChooseCustomColor()
    {
        var hex = Dialogs.Prompt(T("Kart rengi"), T("Renk kodu (#RRGGBB):"), _cfg.Get("custom_color", "#3b6ea5"));
        if (hex is null) return;
        hex = hex.Trim();
        if (!Regex.IsMatch(hex, "^#?[0-9a-fA-F]{6}$"))
        {
            MessageBox.Show(T("Renk kodunu #RRGGBB biçiminde girin (ör. #3b6ea5)."), T("Kart rengi"));
            return;
        }
        _cfg.Set("custom_color", hex.StartsWith('#') ? hex : "#" + hex);
        _cfg.Set("theme", "custom");
        TrySave();
        Render();
    }

    private void ChooseBgImage()
    {
        var dlg = new Microsoft.Win32.OpenFileDialog { Title = T("Arka plan görseli"), Filter = "Görseller|*.jpg;*.jpeg;*.png;*.bmp;*.webp" };
        if (dlg.ShowDialog() == true)
        {
            _cfg.Set("bg_image", dlg.FileName);
            _cfg.Set("theme", "image");
        }
        else if (string.IsNullOrEmpty(_cfg.Get<string?>("bg_image", null)) && _cfg.Get("theme", "auto") == "image")
            _cfg.Set("theme", "auto");   // görsel seçilmediyse temayı geri al
        TrySave();
        Render();
    }

    private void OpenSite()
    {
        var url = Loc.IsOpenMeteo
            ? "https://open-meteo.com/"
            : "https://www.mgm.gov.tr/tahmin/il-ve-ilceler.aspx?il=" + Uri.EscapeDataString(Loc.Il) + "&ilce=" + Uri.EscapeDataString(Loc.Ilce);
        System.Diagnostics.Process.Start(new System.Diagnostics.ProcessStartInfo(url) { UseShellExecute = true });
    }

    // ------------------------------------------------------------------ güncelleme
    /// manual: sonucu pencereyle bildir; install: yeni sürüm varsa sormadan kur.
    private async Task CheckUpdateAsync(bool manual, bool install = false)
    {
        if (!IsMain) return;
        ReleaseInfo rel;
        try { rel = await UpdateService.GetLatestAsync(CancellationToken.None); }
        catch (Exception ex)
        {
            if (manual) MessageBox.Show(T("Güncelleme denetlenemedi:\n{}").F(ex.Message), T("Güncelleme"), MessageBoxButton.OK, MessageBoxImage.Warning);
            return;
        }
        bool newer = UpdateService.IsNewer(rel.Version, Constants.AppVersion) && rel.SetupUrl is not null;
        _updateInfo = newer ? rel : null;
        if (!newer)
        {
            if (manual) MessageBox.Show(T("En güncel sürümü kullanıyorsun (sürüm {}).").F(Constants.AppVersion), T("Güncelleme"));
            return;
        }
        if (install) await InstallUpdateAsync();
        else if (manual) await PromptUpdateAsync();
        else if (_cfg.Get("seen_update", "") != rel.Version)   // sürüm başına bir bildirim
        {
            _cfg.Set("seen_update", rel.Version);
            TrySave();
            Notify(T("{}: yeni sürüm").F(Constants.AppName), T("Sürüm {} hazır. Kurmak için widget'a sağ tıklayıp Güncelleme menüsünü aç.").F(rel.Version));
        }
    }

    private async Task PromptUpdateAsync()
    {
        if (_updateInfo is not { } rel || _updating) return;
        var notes = rel.Notes.Length > 500 ? rel.Notes[..500] + "…" : rel.Notes;
        if (MessageBox.Show(T("Yeni sürüm: {}  (kurulu sürüm: {})\n\n").F(rel.Version, Constants.AppVersion) +
                            (notes.Length > 0 ? notes + "\n\n" : "") + T("Şimdi indirilip kurulsun mu? Widget yeniden başlayacak."),
                T("Güncelleme"), MessageBoxButton.YesNo, MessageBoxImage.Question) == MessageBoxResult.Yes)
            await InstallUpdateAsync();
    }

    /// Kurulum dosyasını indirip sessiz kipte bu klasöre kurar (Python install_update_setup). Kurulum,
    /// kapanan widget'ı bekler (Restart Manager), dosyaları değiştirir ve widget'ı yeniden başlatır.
    private async Task InstallUpdateAsync()
    {
        if (_updateInfo is not { } rel || _updating) return;
        _updating = true;
        var setup = Path.Combine(Path.GetTempPath(), Constants.UpdateSetup);
        try
        {
            try { await UpdateService.DownloadAsync(rel.SetupUrl!, false, rel.SetupSize, rel.SetupSha256, setup, CancellationToken.None); }
            catch
            {
                try { if (File.Exists(setup)) File.Delete(setup); }
                catch (Exception ex2) when (ex2 is IOException or UnauthorizedAccessException) { }
                throw;
            }
            TrySave();
            UpdateService.StopOtherInstances(ExePath);
            UpdateService.Spawn(setup, "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/CLOSEAPPLICATIONS", "/DIR=" + _paths.AppDir);
            Close();
        }
        catch (Exception ex)
        {
            _updating = false;
            MessageBox.Show(T("Güncelleme indirilemedi:\n{}").F(ex.Message), T("Güncelleme"), MessageBoxButton.OK, MessageBoxImage.Warning);
        }
    }
}
