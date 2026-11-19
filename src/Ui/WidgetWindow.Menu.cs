using System.Windows;
using System.Windows.Controls;
using HavaDurumu.Core;
using HavaDurumu.Data;

namespace HavaDurumu.Ui;

/// Sağ tık menüsü (Python MenuPanel sayfaları, WPF alt menüleri olarak) ve şehir listesi işlemleri.
public partial class WidgetWindow
{
    // ------------------------------------------------------------------ diyaloglar için
    internal IReadOnlyList<Location> Locations => _locs;
    internal int CurrentIndex => _idx;
    internal Location CurrentLocation => Loc;

    internal void SaveConfig() => TrySave();

    internal void SwitchTo(int i)
    {
        _idx = ((i % _locs.Count) + _locs.Count) % _locs.Count;
        Render();
        if (!_cfg.LoadFailed)
        {
            _cfg.Set("idx", _idx);
            TrySave();
        }
        ScheduleRotate();   // elle geçince döndürme sayacı baştan başlasın
        if (!_cache.ById.ContainsKey(Loc.Key)) _ = RefreshAsync();
    }

    private void CitiesChanged()
    {
        _cfg.SetLocations(_locs);
        _cfg.Set("idx", _idx);
        TrySave();
        if (Layouts.IsMulti(Layout)) ApplySettingsChanged(relayout: true);   // kart sayısı değişti
        else Render();
        ScheduleRotate();
    }

    internal void AddLocation(Location loc)
    {
        if (_placeholder)
        {
            _locs.Clear();
            _placeholder = false;
        }
        _locs.Add(loc);
        if (_locs.Count == 2 && !_cfg.Get("rotate_touched", false)) _cfg.Set("rotate", true);
        _idx = _locs.Count - 1;
        CitiesChanged();
        if (!_cache.ById.ContainsKey(loc.Key)) _ = RefreshAsync();
    }

    internal void RemoveLocation(int i)
    {
        if (_locs.Count <= 1) return;
        var loc = _locs[i];
        _locs.RemoveAt(i);
        _cache.ById.Remove(loc.Key);
        _errors.Remove(loc.Key);
        if (_idx > i || _idx >= _locs.Count) _idx = Math.Max(0, _idx - 1);
        _cache.Save(_locs.Select(l => l.Key));
        CitiesChanged();
    }

    internal void RenameLocation(int i, string name)
    {
        _locs[i].Name = name.Length > 0 ? name : null;
        CitiesChanged();
    }

    internal void MoveLocation(int i, int j)
    {
        (_locs[i], _locs[j]) = (_locs[j], _locs[i]);
        _idx = _idx == i ? j : _idx == j ? i : _idx;
        CitiesChanged();
    }

    private void SetRotate(bool? on = null, int? sec = null)
    {
        _cfg.Set("rotate_touched", true);
        if (on is bool o) _cfg.Set("rotate", o);
        if (sec is int s)
        {
            _cfg.Set("rotate_sec", s);
            if (on is null) _cfg.Set("rotate", true);   // süre seçmek özelliği de açar
        }
        TrySave();
        ScheduleRotate();
    }

    /// Ayar değişince: birimler, pencere boyutu (ölçek / düzen), yeniden çizim.
    private void ApplySettingsChanged(bool relayout = false)
    {
        Units.Fahrenheit = _cfg.Get("units_temp", "C") == "F";
        Units.WindMs = _cfg.Get("units_wind", "kmh") == "ms";
        if (relayout)
        {
            if (_expanded) Expand(false);
            PopupHide();
            _page = 0;
            StageScale.ScaleX = StageScale.ScaleY = Scale;
            var (dw, dh) = Layouts.WindowSize(Layout, _locs.Count);
            SetWindowRect(_x, _y, Px(dw), Px(dh));
        }
        Render();
    }

    private void Set<T>(string key, T value, Action? after = null)
    {
        _cfg.Set(key, value);
        TrySave();
        (after ?? (() => Render()))();
    }

    // ------------------------------------------------------------------ menü
    private void ShowMenu()
    {
        _hoverTimer.Stop();
        PopupHide();
        bool On(string k, bool def = false) => _cfg.Get(k, def);
        var menu = new ContextMenu();
        var items = menu.Items;

        items.Add(Act(T("Yenile"), () => _ = RefreshAsync(), enabled: !_refreshing));
        items.Add(Act("Radar", () => RadarWindow.Open(this, _cfg)));
        items.Add(Check(T("Kilitle"), On("locked"), v => Set("locked", v)));
        items.Add(Check(T("Üstte"), On("on_top"), v => Set("on_top", v, ApplyZOrder)));
        items.Add(new Separator());

        // Şehirler
        var cities = Sub(T("Şehirler"), Fmt.LocShort(Loc) + (_locs.Count > 1 ? $"  ·  {_locs.Count}" : ""));
        for (int i = 0; i < _locs.Count; i++)
        {
            int k = i;
            var cur = _cache.ById.TryGetValue(_locs[i].Key, out var d) ? J.Num(d.Cur?["sicaklik"]) : null;
            cities.Items.Add(Radio(Fmt.LocTitle(_locs[i]) + (Fmt.Ok(cur) ? $"   {Fmt.Tint(cur!.Value)}°" : ""), i == _idx, () => SwitchTo(k)));
        }
        cities.Items.Add(new Separator());
        cities.Items.Add(Check(T("Otomatik geçiş"), On("rotate"), v => SetRotate(on: v), enabled: _locs.Count > 1));
        var rot = Sub(T("Geçiş süresi"));
        foreach (var (v, label) in new[] { (5, T("5 sn")), (10, T("10 sn")), (15, T("15 sn")), (30, T("30 sn")), (60, T("1 dk")), (300, T("5 dk")) })
            rot.Items.Add(Radio(label, _cfg.Get("rotate_sec", 15) == v, () => SetRotate(sec: v)));
        rot.IsEnabled = _locs.Count > 1;
        if (_locs.Count < 2) cities.Items.Add(Info(T("Otomatik geçiş için en az 2 şehir ekleyin.")));
        cities.Items.Add(rot);
        cities.Items.Add(Act(T("Şehirleri yönet…"), () => CityManager.Open(this)));
        items.Add(cities);

        // Görünüm
        var look = Sub(T("Görünüm"));
        var layout = Sub(T("Düzen"));
        foreach (var (key, label) in Layouts.Menu)
            layout.Items.Add(Radio(label, Layout == key, () => Set("layout", key, () => ApplySettingsChanged(relayout: true))));
        look.Items.Add(layout);
        var theme = Sub(T("Tema"));
        foreach (var (key, label) in new[] { ("auto", T("Hava durumuna göre")), ("dark", T("Koyu")), ("light", T("Açık", "Light")), ("glass", T("Cam (yarı saydam)")),
                     ("clear", T("Saydam (yalnızca yazı)")), ("system", T("Windows temasını izle (koyu / açık)")), ("scene", T("Çizimli sahne (havaya göre)")),
                     ("custom", T("Özel renk")), ("image", T("Kendi görselim…")) })
        {
            theme.Items.Add(Radio(label, _cfg.Get("theme", "auto") == key, key == "image" && string.IsNullOrEmpty(_cfg.Get<string?>("bg_image", null))
                ? ChooseBgImage
                : () => Set("theme", key)));
        }
        theme.Items.Add(new Separator());
        var colors = Sub(T("Özel renk"));
        foreach (var (hex, name) in Swatches)
            colors.Items.Add(Radio(name, _cfg.Get("theme", "auto") == "custom" && string.Equals(_cfg.Get("custom_color", ""), hex, StringComparison.OrdinalIgnoreCase),
                () => { _cfg.Set("custom_color", hex); Set("theme", "custom"); }));
        colors.Items.Add(Act(T("Başka renk…"), ChooseCustomColor));
        theme.Items.Add(colors);
        theme.Items.Add(Act(T("Arka plan görselini seç…"), ChooseBgImage));
        look.Items.Add(theme);
        look.Items.Add(Choice(T("Ölçek"), _cfg.Get("scale", 1.0), new[] { 0.7, 0.85, 1.0, 1.15, 1.3, 1.5 }.Select(v => (v, Pct(v * 100))),
            v => Set("scale", v, () => ApplySettingsChanged(relayout: true))));
        look.Items.Add(Choice(T("Köşeler"), _cfg.Get("radius", 22.0), new[] { (0.0, T("Köşeli")), (10.0, T("Az")), (22.0, T("Normal")), (32.0, T("Çok")) },
            v => Set("radius", v)));
        look.Items.Add(Choice(_cfg.Get("theme", "auto") == "clear" ? T("Gölge yoğunluğu") : T("Arka plan opaklığı"), _cfg.Get("opacity", 1.0),
            new[] { 1.0, 0.9, 0.8, 0.7, 0.6, 0.5 }.Select(v => (v, Pct(v * 100))), v => Set("opacity", v)));
        look.Items.Add(Choice(T("Saatlik tahmin"), _cfg.Get("hourly", "icons"), new[] { ("icons", T("İkonlar")), ("chart", T("Sıcaklık grafiği")) },
            v => Set("hourly", v)));
        var mini = Sub(T("Mini kart"));
        mini.Items.Add(Choice(T("Fareyle üzerine gelince"), _cfg.Get("mini_hover", "expand"),
            new[] { ("expand", T("Büyüt")), ("popup", T("Ayrıntı kartı")), ("none", T("Hiçbir şey")) }, v => Set("mini_hover", v, () => { PopupHide(); Render(); })));
        mini.Items.Add(Choice(T("Karta tıklayınca"), _cfg.Get("mini_click", "expand"),
            new[] { ("expand", T("Büyüt")), ("page", T("Sayfa değiştir")), ("none", T("Hiçbir şey")) }, v => Set("mini_click", v)));
        mini.Items.Add(Choice(T("Büyüyünce göster"), _cfg.Get("expand_detail", "rich"),
            new[] { ("rich", T("Ayrıntılı (+ 12 saat)")), ("compact", T("Sade (anlık)")) }, v => Set("expand_detail", v, () => Expand(false))));
        mini.Items.Add(Choice(T("Fare tekerleği"), _cfg.Get("wheel_action", "city"),
            new[] { ("city", T("Şehir değiştir")), ("page", T("Sayfa değiştir")) }, v => Set("wheel_action", v)));
        mini.Items.Add(Choice(T("İkinci satır"), _cfg.Get("mini_line", "hl"), new[]
        {
            ("hl", T("En yüksek / en düşük")), ("feels", T("Hissedilen")), ("rain", T("Yağış olasılığı")), ("wind", T("Rüzgar")),
            ("humidity", T("Nem")), ("aqi", T("Hava kalitesi")), ("uv", T("UV indeksi")), ("clock", T("Saat")),
            ("rain_eta", T("Yağış geri sayımı")), ("sun", T("Gün doğumu / batımı")),
        }, v => Set("mini_line", v)));
        if (Layout is not ("mini" or "miniplus" or "vmini" or "row" or "col"))
            mini.Items.Add(Info(T("Mini, Mini+, Dikey mini ve çoklu düzenlerde görünür.")));
        mini.Items.Add(new Separator());
        foreach (var tip in new[] { T("Sayfalar: şimdi → saatlik → yarın (Mini, Mini+)."), T("Üzerine gelince büyüt: fare çekilince küçülür."),
                     T("Tıklayınca büyüt: tekrar tıkla ya da 6 sn bekle."), T("Yan yana / Alt alta: Shift + sürükle ile sırala.") })
            mini.Items.Add(Info(tip));
        look.Items.Add(mini);
        look.Items.Add(new Separator());
        look.Items.Add(Check(T("Saat ve tarihi göster"), On("clock", true), v => Set("clock", v)));
        look.Items.Add(Check(T("Animasyonlu ikonlar"), On("animate"), v => Set("animate", v)));
        look.Items.Add(Check(T("Arka plan efektleri (yağmur, kar, yıldızlar…)"), On("effects", true), v => Set("effects", v)));
        look.Items.Add(Check(T("Üzerine gelince ayrıntı kartı"), On("hover", true), v => Set("hover", v, () => { PopupHide(); Render(); })));
        look.Items.Add(Check(T("Renkli sıcaklık (soğuk mavi, sıcak turuncu)"), On("colored_temp"), v => Set("colored_temp", v)));
        look.Items.Add(Check(T("Uyarı varken kart kenarı renkli"), On("alert_border", true), v => Set("alert_border", v)));
        items.Add(look);

        // Birimler
        var units = Sub(T("Birimler"), (_cfg.Get("units_temp", "C") == "F" ? "°F" : "°C") + " · " + (_cfg.Get("units_wind", "kmh") == "ms" ? "m/sn" : "km/sa"));
        units.Items.Add(Choice(T("Sıcaklık"), _cfg.Get("units_temp", "C"), new[] { ("C", T("Santigrat (°C)")), ("F", T("Fahrenhayt (°F)")) },
            v => Set("units_temp", v, () => ApplySettingsChanged())));
        units.Items.Add(Choice(T("Rüzgar"), _cfg.Get("units_wind", "kmh"), new[] { ("kmh", "km/saat"), ("ms", "m/sn") },
            v => Set("units_wind", v, () => ApplySettingsChanged())));
        items.Add(units);
        items.Add(new Separator());

        // Bildirimler
        int cnt = new[] { "notify_rain", "notify_alerts", "notify_uv", "morning_on", "reminders_on" }.Count(k => On(k, true));
        var notif = Sub(T("Bildirimler"), cnt > 0 ? T("{} açık").F(cnt) : T("Kapalı"));
        void Noop() { }
        notif.Items.Add(Check(T("Yağış yaklaşırken haber ver"), On("notify_rain", true), v => Set("notify_rain", v, Noop)));
        notif.Items.Add(Check(T("MGM uyarılarını bildir"), On("notify_alerts", true), v => Set("notify_alerts", v, Noop)));
        notif.Items.Add(Check(T("UV çok yüksek olunca haber ver"), On("notify_uv", true), v => Set("notify_uv", v, Noop)));
        notif.Items.Add(new Separator());
        notif.Items.Add(Check(T("Sabah özeti ({})").F(_cfg.Get("morning_time", "07:30")), On("morning_on", true), v => Set("morning_on", v, Noop)));
        notif.Items.Add(Check(T("Şemsiye / mont hatırlatması"), On("reminders_on", true), v => Set("reminders_on", v, Noop)));
        notif.Items.Add(Act(T("Sabah saati, sessiz saatler…") + (On("quiet_on") ? T("   (sessiz: açık)") : ""),
            () => Dialogs.Times(_cfg, TrySave)));
        double? lo = _cfg.Get<double?>("alert_low", null), hi = _cfg.Get<double?>("alert_high", null);
        var thr = string.Join(", ", new[] { lo is double l ? "≤ " + Fmt.Temp(l) : null, hi is double h ? "≥ " + Fmt.Temp(h) : null }.Where(x => x is not null));
        notif.Items.Add(Act(T("Sıcaklık eşikleri…") + $"   ({(thr.Length > 0 ? thr : T("Kapalı"))})", () => Dialogs.Thresholds(_cfg, () =>
        {
            TrySave();
            _thrState.Clear();
            CheckNotifications();
        })));
        notif.Items.Add(new Separator());
        notif.Items.Add(Check(T("Başlat menüsü kısayolu"), On("start_shortcut", true), v => Set("start_shortcut", v, ApplyStartShortcut), enabled: IsMain));
        notif.Items.Add(Act(T("Sabah özetini şimdi göster"), NotifyMorningNow));
        items.Add(notif);

        // Yenileme
        var refresh = Sub(T("Yenileme"), T("{} dk").F(_cfg.Get("refresh_min", 15)));
        foreach (var v in new[] { 5, 10, 15, 30, 60 })
            refresh.Items.Add(Radio(T("{} dakikada bir").F(v), _cfg.Get("refresh_min", 15) == v, () => Set("refresh_min", v, () =>
            {
                _refreshTimer.Interval = TimeSpan.FromMinutes(v);
                _ = RefreshAsync();
            })));
        refresh.Items.Add(new Separator());
        refresh.Items.Add(Act(_refreshing ? T("Yenileniyor…") : T("Şimdi yenile"), () => _ = RefreshAsync(), enabled: !_refreshing));
        items.Add(refresh);

        // Olay günlüğü
        var recent = _events.Recent(24);
        var events = Sub(T("Olay günlüğü"), recent.Count > 0 ? T("{} olay").F(recent.Count) : T("Boş"));
        if (recent.Count == 0) events.Items.Add(Info(T("Son 24 saatte kayıtlı olay yok.")));
        string? lastDay = null;
        foreach (var e in recent.Take(80))
        {
            if (!AlertService.TryParseUtc(J.StrOrNull(e["t"]), out var t)) continue;
            var lt = t.ToLocalTime();
            var day = lt.Date == DateTime.Today ? T("Bugün") : T("Dün");
            if (day != lastDay) { events.Items.Add(Info(day, bold: true)); lastDay = day; }
            events.Items.Add(Info($"{lt:HH:mm}   {J.Str(e["city"])}   —   {EventLog.Text(e)}"));
        }
        events.Items.Add(new Separator());
        events.Items.Add(Info(T("Kaydedilenler: yağış başlangıcı ve bitişi,")));
        events.Items.Add(Info(T("3 saatte 5°+ sıcaklık değişimi, UV 8+,")));
        events.Items.Add(Info(T("rüzgar 60+ km/sa, yeni MGM uyarısı.")));
        events.Items.Add(Act(T("Günlüğü temizle"), _events.Clear, enabled: _events.Events.Count > 0));
        items.Add(events);

        // Pencere
        var win = Sub(T("Pencere"), On("on_top") ? T("Üstte") : T("Masaüstü"));
        win.Items.Add(Choice(T("Konum"), On("on_top") ? "top" : "desktop", new[] { ("desktop", T("Masaüstünde")), ("top", T("Her zaman üstte")) },
            v => Set("on_top", v == "top", ApplyZOrder)));
        win.Items.Add(Check(T("Konumu kilitle"), On("locked"), v => Set("locked", v)));
        win.Items.Add(Check(T("Ekran kenarına yapış"), On("snap", true), v => Set("snap", v)));
        if (IsMain)
        {
            var hk = Sub(T("Kısayol tuşu (öne getir)"), Hotkeys.FirstOrDefault(h => h.Key == _cfg.Get("hotkey", "win_shift_w")).Label
                                                    + (_hotkeyOk == false ? T("  — kullanımda!") : ""));
            foreach (var spec in Hotkeys)
                hk.Items.Add(Radio(spec.Label, _cfg.Get("hotkey", "win_shift_w") == spec.Key, () =>
                {
                    _cfg.Set("hotkey_touched", true);
                    Set("hotkey", spec.Key, ApplyHotkey);
                }));
            hk.Items.Add(Info(T("Basınca widget öne gelir, tekrar basınca geri döner.")));
            win.Items.Add(hk);
        }
        items.Add(win);

        // Widget'lar ve ayarlar
        var widgets = Sub(T("Widget'lar ve ayarlar"));
        if (IsMain)
        {
            widgets.Items.Add(Act(T("Yeni widget ekle (başka şehir / ekran)"), AddWidget));
            widgets.Items.Add(Info(T("Ek widget sayısı") + $": {_paths.ListProfiles().Count}"));
        }
        else widgets.Items.Add(Act(T("Bu widget'ı kaldır…"), RemoveWidget));
        widgets.Items.Add(new Separator());
        widgets.Items.Add(Act(T("Ayarları dışa aktar…"), ExportSettings));
        widgets.Items.Add(Act(T("Ayarları içe aktar…"), ImportSettings));
        if (IsMain)
        {
            widgets.Items.Add(new Separator());
            widgets.Items.Add(Check(T("Windows ile başlat"), Startup.Enabled, Startup.Set));
            var lang = Sub("Dil / Language", Lang.Code == "tr" ? "Türkçe" : "English");
            foreach (var (k, label) in new[] { ("tr", "Türkçe"), ("en", "English") })
                lang.Items.Add(Radio(label, Lang.Code == k, () => SetLang(k)));
            widgets.Items.Add(lang);
        }
        items.Add(widgets);

        // Güncelleme
        if (IsMain)
        {
            var upd = Sub(T("Güncelleme"), _updateInfo is not null ? T("Yeni sürüm!") : T("Sürüm {}").F(Constants.AppVersion));
            upd.Items.Add(Info(T("Kurulu sürüm") + $": {Constants.AppVersion}"));
            if (_updateInfo is { } rel) upd.Items.Add(Act(T("Sürüm {} — şimdi güncelle").F(rel.Version), () => _ = PromptUpdateAsync()));
            upd.Items.Add(Act(T("Güncellemeleri denetle"), () => _ = CheckUpdateAsync(manual: true)));
            upd.Items.Add(Check(T("Otomatik denetle (günde bir)"), On("auto_update", true), v => Set("auto_update", v, Noop)));
            items.Add(upd);
        }

        items.Add(new Separator());
        items.Add(Act(T("Kaynak sitesini aç"), OpenSite));
        items.Add(Act(T("Kapat"), Close));

        menu.Closed += (_, _) => { if (!_cfg.Get("on_top", false)) Native.KeepBottom(_hwnd); };
        ContextMenu = menu;
        menu.IsOpen = true;
    }

    private static MenuItem Sub(string header, string? value = null) =>
        new() { Header = value is { Length: > 0 } ? $"{header}   ({value})" : header };

    private static MenuItem Act(string header, Action act, bool enabled = true)
    {
        var mi = new MenuItem { Header = header, IsEnabled = enabled };
        mi.Click += (_, _) => act();
        return mi;
    }

    private static MenuItem Radio(string header, bool on, Action act)
    {
        var mi = new MenuItem { Header = header, IsChecked = on };
        mi.Click += (_, _) => act();
        return mi;
    }

    private static MenuItem Check(string header, bool on, Action<bool> set, bool enabled = true)
    {
        var mi = new MenuItem { Header = header, IsCheckable = true, IsChecked = on, IsEnabled = enabled, StaysOpenOnClick = false };
        mi.Click += (_, _) => set(mi.IsChecked);
        return mi;
    }

    private static MenuItem Info(string text, bool bold = false) =>
        new() { Header = text, IsEnabled = false, FontWeight = bold ? FontWeights.SemiBold : FontWeights.Normal };

    /// Seçenek listesi: geçerli değere en yakın olan işaretli.
    private static MenuItem Choice<T>(string header, T current, IEnumerable<(T Value, string Label)> options, Action<T> set)
    {
        var list = options.ToList();
        var curLabel = list.FirstOrDefault(o => Equals(o.Value, current)).Label;
        if (curLabel is null && current is double cd)
            curLabel = list.OrderBy(o => Math.Abs(Convert.ToDouble(o.Value) - cd)).First().Label;
        var mi = Sub(header, curLabel);
        foreach (var (v, label) in list)
            mi.Items.Add(Radio(label, label == curLabel, () => set(v)));
        return mi;
    }
}
