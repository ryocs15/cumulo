using System.Globalization;
using System.IO;
using System.Net.Http;
using System.Text.Json.Nodes;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Controls.Primitives;
using System.Windows.Input;
using System.Windows.Media;
using System.Windows.Media.Imaging;
using System.Windows.Shapes;
using System.Windows.Threading;
using HavaDurumu.Core;
using HavaDurumu.Data;

namespace HavaDurumu.Ui;

/// Küçük ayar pencereleri (Python tk.Toplevel diyaloglarının karşılığı). Kodla kurulur.
public static class Dialogs
{
    private static readonly CultureInfo Tr = new("tr-TR");

    internal static Window NewWindow(string title, double width = double.NaN)
    {
        var w = new Window
        {
            Title = title, Topmost = true, ResizeMode = ResizeMode.NoResize, SizeToContent = SizeToContent.WidthAndHeight,
            WindowStartupLocation = WindowStartupLocation.CenterScreen, ShowInTaskbar = true, Width = width,
            FontFamily = new FontFamily("Segoe UI"), FontSize = 13,
        };
        w.KeyDown += (_, e) => { if (e.Key == Key.Escape) w.Close(); };
        return w;
    }

    internal static TextBlock Label(string text, double top = 0, Brush? fg = null) =>
        new() { Text = text, Margin = new Thickness(0, top, 0, 2), TextWrapping = TextWrapping.Wrap, Foreground = fg ?? Brushes.Black };

    internal static Button Btn(string text, Action click, double minWidth = 80)
    {
        var b = new Button { Content = text, MinWidth = minWidth, Padding = new Thickness(10, 3, 10, 3), Margin = new Thickness(6, 0, 0, 0) };
        b.Click += (_, _) => click();
        return b;
    }

    /// Tek satır metin sorar; iptalde null.
    public static string? Prompt(string title, string label, string initial)
    {
        var w = NewWindow(title, 360);
        var box = new TextBox { Text = initial, Margin = new Thickness(0, 4, 0, 10) };
        string? result = null;
        var ok = Btn(T("Tamam"), () => { result = box.Text; w.Close(); });
        ok.IsDefault = true;
        var panel = new StackPanel { Margin = new Thickness(14) };
        panel.Children.Add(Label(label));
        panel.Children.Add(box);
        var row = new StackPanel { Orientation = Orientation.Horizontal, HorizontalAlignment = HorizontalAlignment.Right };
        row.Children.Add(ok);
        row.Children.Add(Btn(T("İptal"), w.Close));
        panel.Children.Add(row);
        w.Content = panel;
        w.Loaded += (_, _) => { box.Focus(); box.SelectAll(); };
        w.ShowDialog();
        return result;
    }

    /// Türkçe alfabeye göre karşılaştırma ve küçük harf (Python tr_key / tr_lower).
    internal static readonly StringComparer TrCompare = StringComparer.Create(Tr, ignoreCase: true);
    internal static string TrLower(string s) => s.ToLower(Tr);

    /// MGM uyarısının tam metni (Python AlertDialog).
    public static void Alert(Location loc, IReadOnlyList<AlertItem> alerts)
    {
        var w = NewWindow(T("MGM uyarısı — {}").F(Fmt.LocTitle(loc)), 460);
        var panel = new StackPanel { Margin = new Thickness(14) };
        foreach (var a in alerts)
        {
            var c = a.Color switch { "red" => Color.FromRgb(240, 70, 70), "orange" => Color.FromRgb(255, 150, 50), _ => Color.FromRgb(250, 204, 50) };
            panel.Children.Add(new Border
            {
                Background = new SolidColorBrush(c), Padding = new Thickness(6, 2, 6, 2), HorizontalAlignment = HorizontalAlignment.Left,
                Child = new TextBlock
                {
                    Text = T("{} uyarı: {}").F(a.ColorName, a.Kinds.Count > 0 ? string.Join(", ", a.Kinds) : T("Meteorolojik uyarı")),
                    FontWeight = FontWeights.SemiBold, Foreground = new SolidColorBrush(Color.FromRgb(28, 28, 28)),
                },
            });
            if (AlertService.TryParseUtc(a.Begin, out var b) && AlertService.TryParseUtc(a.End, out var e))
            {
                var tr = TimeSpan.FromHours(3);   // MGM uyarıları yalnızca Türkiye için: kartla aynı Türkiye saati (WeatherData.UtcOffset)
                panel.Children.Add(Label($"{Fmt.LocalTime(b, tr):dd.MM} {Fmt.Hm(Fmt.LocalTime(b, tr))} – {Fmt.LocalTime(e, tr):dd.MM} {Fmt.Hm(Fmt.LocalTime(e, tr))}", 4, Brushes.DimGray));
            }
            panel.Children.Add(Label(a.Text, 4));
            panel.Children.Add(new Separator { Margin = new Thickness(0, 8, 0, 8) });
        }
        var close = Btn(T("Kapat"), w.Close);
        close.HorizontalAlignment = HorizontalAlignment.Right;
        close.IsCancel = true;
        panel.Children.Add(close);
        w.Content = panel;
        w.Show();
    }

    /// Sıcaklık eşikleri: alt ve üst (boş = kapalı). İçeride hep °C saklanır.
    public static void Thresholds(AppConfig cfg, Action saved)
    {
        string unit = Units.Fahrenheit ? "°F" : "°C";
        var w = NewWindow(T("Sıcaklık eşikleri"), 380);
        var panel = new StackPanel { Margin = new Thickness(14) };
        panel.Children.Add(Label(T("Sıcaklık bu değerlere ulaşınca bildirim gelir.\nKapatmak için kutuyu boş bırak.")));
        var boxes = new Dictionary<string, TextBox>();
        foreach (var (key, label) in new[] { ("alert_low", T("Alt eşik ({}) — şu değere düşünce").F(unit)), ("alert_high", T("Üst eşik ({}) — şu değere çıkınca").F(unit)) })
        {
            var v = cfg.Get<double?>(key, null);
            var box = new TextBox { Width = 70, Text = v is double d ? Fmt.G(Math.Round(Fmt.TConv(d), 2)) : "" };
            var row = new DockPanel { Margin = new Thickness(0, 6, 0, 0) };
            DockPanel.SetDock(box, Dock.Right);
            row.Children.Add(box);
            row.Children.Add(new TextBlock { Text = label, VerticalAlignment = VerticalAlignment.Center });
            panel.Children.Add(row);
            boxes[key] = box;
        }
        var msg = Label("", 8, Brushes.Firebrick);
        panel.Children.Add(msg);
        var save = Btn(T("Kaydet"), () =>
        {
            var vals = new Dictionary<string, double?>();
            foreach (var (key, box) in boxes)
            {
                var s = box.Text.Trim().Replace(',', '.');
                if (s.Length == 0) { vals[key] = null; continue; }
                if (!double.TryParse(s, NumberStyles.Float, CultureInfo.InvariantCulture, out var v))
                {
                    msg.Text = T("Sayı girin (ör. 2 ya da 35.5).");
                    return;
                }
                vals[key] = Units.Fahrenheit ? (v - 32) * 5 / 9 : v;
            }
            foreach (var (k, v) in vals) cfg.Set(k, v);
            saved();
            w.Close();
        });
        save.IsDefault = true;
        save.HorizontalAlignment = HorizontalAlignment.Right;
        panel.Children.Add(save);
        w.Content = panel;
        w.Show();
    }

    /// Sabah özeti saati ve sessiz saatler.
    public static void Times(AppConfig cfg, Action saved)
    {
        var w = NewWindow(T("Bildirim saatleri"), 400);
        var panel = new StackPanel { Margin = new Thickness(14) };
        TextBox Row(string label, string value)
        {
            var box = new TextBox { Width = 60, Text = value };
            var row = new DockPanel { Margin = new Thickness(0, 6, 0, 0) };
            DockPanel.SetDock(box, Dock.Right);
            row.Children.Add(box);
            row.Children.Add(new TextBlock { Text = label, VerticalAlignment = VerticalAlignment.Center });
            panel.Children.Add(row);
            return box;
        }
        var morning = Row(T("Sabah özeti / hatırlatma saati (SS:DD)"), cfg.Get("morning_time", "07:30"));
        panel.Children.Add(new Separator { Margin = new Thickness(0, 10, 0, 6) });
        var quiet = new CheckBox { Content = T("Sessiz saatler (bu aralıkta bildirim gelmez)"), IsChecked = cfg.Get("quiet_on", false) };
        panel.Children.Add(quiet);
        var q1 = Row(T("Başlangıç (SS:DD)"), cfg.Get("quiet_start", "23:00"));
        var q2 = Row(T("Bitiş (SS:DD)"), cfg.Get("quiet_end", "07:00"));
        panel.Children.Add(Label(T("Sessiz saatlerde gelmeyen uyarılar, sessizlik bitince gösterilir."), 6, Brushes.DimGray));
        var msg = Label("", 6, Brushes.Firebrick);
        panel.Children.Add(msg);
        var save = Btn(T("Kaydet"), () =>
        {
            var vals = new Dictionary<string, string>();
            foreach (var (key, box) in new[] { ("morning_time", morning), ("quiet_start", q1), ("quiet_end", q2) })
            {
                if (WidgetWindow.ParseHhmm(box.Text) is not int m)
                {
                    msg.Text = T("Saatleri SS:DD biçiminde girin (ör. 07:30).");
                    return;
                }
                vals[key] = $"{m / 60:00}:{m % 60:00}";
            }
            if (vals["morning_time"] != cfg.Get("morning_time", "07:30")) cfg.Set("last_morning", "");   // yeni saat bugün de gelsin
            foreach (var (k, v) in vals) cfg.Set(k, v);
            cfg.Set("quiet_on", quiet.IsChecked == true);
            saved();
            w.Close();
        });
        save.IsDefault = true;
        save.HorizontalAlignment = HorizontalAlignment.Right;
        panel.Children.Add(save);
        w.Content = panel;
        w.Show();
    }
}

/// Şehir listesi: ekle / kaldır / sırala / adlandır / göster (Python CityManager).
internal sealed class CityManager
{
    private readonly WidgetWindow _app;
    private readonly Window _w;
    private readonly ListBox _list = new() { Height = 150, Width = 300 };
    private readonly ComboBox _il = new() { IsEditable = true, IsTextSearchEnabled = true };
    private readonly ComboBox _ilce = new() { IsEditable = true, IsTextSearchEnabled = true };
    private readonly TextBox _query = new();
    private readonly ComboBox _results = new();
    private readonly TextBlock _status = Dialogs.Label(T("İller yükleniyor…"), 10, Brushes.DimGray);
    private List<JsonObject> _iller = new(), _ilceler = new();
    private List<Location> _found = new();

    private static CityManager? _open;

    public static void Open(WidgetWindow app)
    {
        if (_open is not null) { _open._w.Activate(); return; }
        _open = new CityManager(app);
    }

    private CityManager(WidgetWindow app)
    {
        _app = app;
        _w = Dialogs.NewWindow(T("Şehirler"), 420);
        _w.Closed += (_, _) => _open = null;
        var p = new StackPanel { Margin = new Thickness(14) };
        p.Children.Add(Dialogs.Label(T("Şehirlerim")));
        var listRow = new DockPanel();
        var btns = new StackPanel { Margin = new Thickness(8, 0, 0, 0) };
        foreach (var (t, a) in new (string, Action)[] { (T("Göster"), Show), (T("Ad ver…"), Rename), (T("Yukarı"), () => Move(-1)), (T("Aşağı"), () => Move(1)), (T("Kaldır"), Remove) })
        {
            var b = Dialogs.Btn(t, a);
            b.Margin = new Thickness(0, 0, 0, 4);
            btns.Children.Add(b);
        }
        DockPanel.SetDock(btns, Dock.Right);
        listRow.Children.Add(btns);
        listRow.Children.Add(_list);
        p.Children.Add(listRow);
        _list.MouseDoubleClick += (_, _) => Show();
        _list.KeyDown += (_, e) => { if (e.Key == Key.Delete) Remove(); };

        p.Children.Add(new Separator { Margin = new Thickness(0, 10, 0, 10) });
        p.Children.Add(Dialogs.Label(T("Türkiye'den ekle (MGM) — İl")));
        p.Children.Add(_il);
        p.Children.Add(Dialogs.Label(T("İlçe"), 6));
        var ilceRow = new DockPanel();
        var add = Dialogs.Btn(T("Ekle"), AddMgm);
        DockPanel.SetDock(add, Dock.Right);
        ilceRow.Children.Add(add);
        ilceRow.Children.Add(_ilce);
        p.Children.Add(ilceRow);
        _il.SelectionChanged += async (_, _) => { if (_il.SelectedItem is string s) await LoadIlceler(s); };
        _il.KeyDown += async (_, e) => { if (e.Key == Key.Enter && Match(_il.Text, IlNames) is { } s) await LoadIlceler(s); };

        p.Children.Add(new Separator { Margin = new Thickness(0, 10, 0, 10) });
        p.Children.Add(Dialogs.Label(T("Yurt dışından ekle (Open-Meteo) — şehir adı")));
        var qRow = new DockPanel();
        var find = Dialogs.Btn(T("Ara", "Search"), async () => await Find());
        DockPanel.SetDock(find, Dock.Right);
        qRow.Children.Add(find);
        qRow.Children.Add(_query);
        p.Children.Add(qRow);
        _query.KeyDown += async (_, e) => { if (e.Key == Key.Enter) await Find(); };
        var rRow = new DockPanel { Margin = new Thickness(0, 6, 0, 0) };
        var addOm = Dialogs.Btn(T("Ekle"), AddOm);
        DockPanel.SetDock(addOm, Dock.Right);
        rRow.Children.Add(addOm);
        rRow.Children.Add(_results);
        p.Children.Add(rRow);

        var bottom = new DockPanel { Margin = new Thickness(0, 6, 0, 0) };
        var close = Dialogs.Btn(T("Kapat"), _w.Close);
        DockPanel.SetDock(close, Dock.Right);
        bottom.Children.Add(close);
        bottom.Children.Add(_status);
        p.Children.Add(bottom);
        _w.Content = p;
        RefreshList();
        _w.Show();
        _ = LoadIller();
    }

    private List<string> IlNames => _iller.Select(e => J.Str(e["il"])).Distinct().OrderBy(n => n, Dialogs.TrCompare).ToList();

    private static string? Match(string text, List<string> names)
    {
        var q = Dialogs.TrLower(text.Trim());
        return names.FirstOrDefault(n => Dialogs.TrLower(n) == q) ?? names.FirstOrDefault(n => Dialogs.TrLower(n).StartsWith(q));
    }

    private void Say(string text, bool err = false)
    {
        _status.Text = text;
        _status.Foreground = err ? Brushes.Firebrick : Brushes.DimGray;
    }

    private void RefreshList(int? sel = null)
    {
        _list.Items.Clear();
        foreach (var l in _app.Locations) _list.Items.Add(Fmt.LocTitle(l) + (l.IsOpenMeteo ? T("  (yurt dışı)") : ""));
        if (_list.Items.Count > 0) _list.SelectedIndex = Math.Clamp(sel ?? _app.CurrentIndex, 0, _list.Items.Count - 1);
    }

    private void Show() { if (_list.SelectedIndex >= 0) _app.SwitchTo(_list.SelectedIndex); }

    private void Rename()
    {
        int i = _list.SelectedIndex;
        if (i < 0) return;
        var loc = _app.Locations[i];
        var orig = Fmt.LocTitle(new Location { Il = loc.Il, Ilce = loc.Ilce });
        var name = Dialogs.Prompt(T("Ad ver"), T("{} için görünen ad\n(boş bırakırsan özgün adı kullanılır):").F(orig), loc.Name ?? "");
        if (name is null) return;
        _app.RenameLocation(i, name.Trim());
        RefreshList(i);
        Say(T("Ad güncellendi."));
    }

    private void Move(int delta)
    {
        int i = _list.SelectedIndex, j = i + delta;
        if (i < 0 || j < 0 || j >= _app.Locations.Count) return;
        _app.MoveLocation(i, j);
        RefreshList(j);
    }

    private void Remove()
    {
        int i = _list.SelectedIndex;
        if (i < 0) return;
        if (_app.Locations.Count <= 1) { Say(T("En az bir şehir kalmalı."), true); return; }
        var name = Fmt.LocTitle(_app.Locations[i]);
        _app.RemoveLocation(i);
        RefreshList(Math.Min(i, _app.Locations.Count - 1));
        Say(T("{} kaldırıldı.").F(name));
    }

    private void Add(Location loc)
    {
        if (_app.Locations.Any(l => l.Key == loc.Key)) { Say(T("{} zaten listede.").F(Fmt.LocTitle(loc)), true); return; }
        _app.AddLocation(loc);
        RefreshList(_app.CurrentIndex);
        Say(T("{} eklendi.").F(Fmt.LocTitle(loc)));
    }

    private async Task LoadIller()
    {
        try
        {
            _iller = J.Arr(await MgmClient.ApiAsync("/merkezler/iller", CancellationToken.None)).OfType<JsonObject>().ToList();
            _il.ItemsSource = IlNames;
            Say(T("Eklemek için il seçin ya da şehir arayın."));
            if (Lang.Code == "tr") _il.Focus(); else _query.Focus();
        }
        catch (Exception ex) when (ex is HttpRequestException or TaskCanceledException or System.Text.Json.JsonException)
        {
            Say(Fmt.Cut(T("Bağlantı hatası: {}").F(ex.Message), 60), true);
        }
    }

    private async Task LoadIlceler(string il)
    {
        Say(T("{} ilçeleri yükleniyor…").F(il));
        try
        {
            var data = J.Arr(await MgmClient.ApiAsync("/merkezler/ililcesi", CancellationToken.None, ("il", il))).OfType<JsonObject>().ToList();
            if (_il.Text != il && _il.SelectedItem as string != il) return;
            _ilceler = data;
            var names = data.Select(e => J.Str(e["ilce"])).Where(n => n.Length > 0).Distinct().OrderBy(n => n, Dialogs.TrCompare).ToList();
            _ilce.ItemsSource = names;
            var center = _iller.FirstOrDefault(e => J.Str(e["il"]) == il);
            _ilce.Text = center is not null && names.Contains(J.Str(center["ilce"])) ? J.Str(center["ilce"]) : names.FirstOrDefault() ?? "";
            Say(T("{} ilçe — seçip Ekle'ye basın.").F(names.Count));
        }
        catch (Exception ex) when (ex is HttpRequestException or TaskCanceledException or System.Text.Json.JsonException)
        {
            Say(T("Bağlantı hatası"), true);
        }
    }

    private void AddMgm()
    {
        var names = _ilceler.Select(e => J.Str(e["ilce"])).ToList();
        var ilce = Match(_ilce.Text, names);
        var entry = _ilceler.FirstOrDefault(e => J.Str(e["ilce"]) == ilce);
        if (entry is null) { Say(T("Listeden bir ilçe seçin."), true); return; }
        Add(Location.FromMgmEntry(entry));
    }

    private async Task Find()
    {
        var q = _query.Text.Trim();
        if (q.Length < 2) { Say(T("En az 2 harf yazın."), true); return; }
        Say(T("“{}” aranıyor…").F(q));
        try
        {
            _found = await OpenMeteoClient.SearchAsync(q, CancellationToken.None);
            _results.ItemsSource = _found.Select(r => string.Join(", ", new[] { r.Ilce, r.Admin, r.Il }.Where(x => !string.IsNullOrEmpty(x)))).ToList();
            if (_found.Count > 0) { _results.SelectedIndex = 0; Say(T("{} sonuç — seçip Ekle'ye basın.").F(_found.Count)); }
            else Say(T("Sonuç bulunamadı."), true);
        }
        catch (Exception ex) when (ex is HttpRequestException or TaskCanceledException or System.Text.Json.JsonException)
        {
            Say(T("Bağlantı hatası"), true);
        }
    }

    private void AddOm()
    {
        if (_results.SelectedIndex >= 0 && _results.SelectedIndex < _found.Count) Add(_found[_results.SelectedIndex]);
    }
}

/// MGM Türkiye radar kompozitinin son 15 görüntüsü (Python RadarWindow). "Şehrim" görünümünde
/// widget'ta seçili şehrin çevresi büyütülür; şehir değişince görünüm de değişir.
internal sealed class RadarWindow
{
    private const string Url = "https://www.mgm.gov.tr/FTPDATA/uzal/radar/comp/compppi{0}.jpg";
    private const int Frames = 15;
    // Radar görüntüsünde (880x720) coğrafi konum -> piksel (Python radar_xy; 27 ile uydurulmuş 2. derece polinom)
    private static readonly double[] Cx = { 422.19135, 35.20408, -0.07062, -7e-05, 0.00192, 0.05927 };
    private static readonly double[] Cy = { 292.43477, -0.03726, -45.45515, 0.00719, 0.01528, -0.44161 };
    private static readonly Int32Rect Map = new(0, 0, 880, 552);
    private static readonly Int32Rect TimeBox = new(572, 672, 160, 48);
    private const int CropW = 264, CropH = 216;

    private static RadarWindow? _open;
    private readonly WidgetWindow _app;
    private readonly AppConfig _cfg;
    private readonly Window _w;
    private readonly Image _view = new() { Stretch = Stretch.Fill };
    private readonly Image _time = new() { HorizontalAlignment = HorizontalAlignment.Right, VerticalAlignment = VerticalAlignment.Bottom, Margin = new Thickness(6) };
    private readonly Canvas _marker = new();
    private readonly Slider _slider = new() { Minimum = 1, Maximum = Frames, IsSnapToTickEnabled = true, TickFrequency = 1, Width = 200, VerticalAlignment = VerticalAlignment.Center };
    private readonly TextBlock _info = new() { Width = 150, VerticalAlignment = VerticalAlignment.Center, Margin = new Thickness(10, 0, 10, 0) };
    private readonly TextBlock _note = Dialogs.Label("", 0, Brushes.DimGray);
    private readonly Button _play;
    private readonly ToggleButton _tr = new() { Content = T("Türkiye"), Padding = new Thickness(8, 2, 8, 2), Margin = new Thickness(10, 0, 0, 0) };
    private readonly ToggleButton _city = new() { Padding = new Thickness(8, 2, 8, 2) };
    private readonly DispatcherTimer _step = new(), _reload = new() { Interval = TimeSpan.FromMinutes(10) }, _watch = new() { Interval = TimeSpan.FromMilliseconds(500) };
    private readonly double _vw, _vh;
    private List<BitmapSource> _frames = new();
    private int _i;
    private bool _playing = true, _setting;
    private string? _cityKey;

    public static void Open(WidgetWindow app, AppConfig cfg)
    {
        if (_open is not null) { _open._w.Activate(); return; }
        _open = new RadarWindow(app, cfg);
    }

    private RadarWindow(WidgetWindow app, AppConfig cfg)
    {
        _app = app;
        _cfg = cfg;
        double f = Math.Min(1.0, Math.Min(SystemParameters.PrimaryScreenWidth * 0.6 / 880, SystemParameters.PrimaryScreenHeight * 0.72 / 720));
        _vw = 880 * f;
        _vh = 720 * f;
        _w = Dialogs.NewWindow(T("Radar — MGM"));
        _w.Closed += (_, _) => { _step.Stop(); _reload.Stop(); _watch.Stop(); _open = null; };
        var stage = new Grid { Width = _vw, Height = _vh, Background = new SolidColorBrush(Color.FromRgb(17, 17, 17)) };
        stage.Children.Add(_view);
        stage.Children.Add(_marker);
        stage.Children.Add(_time);
        _play = Dialogs.Btn(T("Duraklat"), Toggle);
        _play.Margin = new Thickness(0);
        var bar = new DockPanel { Margin = new Thickness(10, 6, 10, 6) };
        var left = new StackPanel { Orientation = Orientation.Horizontal };
        left.Children.Add(_play);
        left.Children.Add(_tr);
        left.Children.Add(_city);
        left.Children.Add(_slider);
        DockPanel.SetDock(left, Dock.Left);
        var reload = Dialogs.Btn(T("Yenile"), () => _ = Load());
        DockPanel.SetDock(reload, Dock.Right);
        bar.Children.Add(left);
        bar.Children.Add(reload);
        bar.Children.Add(_info);
        var root = new StackPanel();
        root.Children.Add(stage);
        root.Children.Add(bar);
        _note.Margin = new Thickness(10, 0, 10, 8);
        root.Children.Add(_note);
        _w.Content = root;
        _tr.Click += (_, _) => SetMode("tr");
        _city.Click += (_, _) => SetMode("city");
        _slider.ValueChanged += (_, _) =>
        {
            if (_setting || _frames.Count == 0) return;
            _playing = false;
            _play.Content = T("Oynat");
            _i = Math.Clamp((int)Math.Round(_slider.Value) - 1, 0, _frames.Count - 1);
            Show();
        };
        _w.KeyDown += (_, e) => { if (e.Key == Key.Space) Toggle(); };
        _step.Tick += (_, _) => Step();
        _reload.Tick += (_, _) => _ = Load();
        _watch.Tick += (_, _) => { if (_frames.Count > 0 && _app.CurrentLocation.Key != _cityKey) Rebuild(); };
        _w.Show();
        _watch.Start();
        _ = Load();
    }

    private async Task Load()
    {
        _info.Text = T("İndiriliyor…");
        try
        {
            var tasks = Enumerable.Range(1, Frames).Select(Fetch).ToList();
            _frames = (await Task.WhenAll(tasks)).ToList();
            _i = _frames.Count - 1;
            Rebuild();
            if (_playing) { _step.Interval = TimeSpan.FromMilliseconds(1500); _step.Start(); }
            _reload.Interval = TimeSpan.FromMinutes(10);
        }
        catch (Exception ex) when (ex is HttpRequestException or TaskCanceledException or InvalidDataException or NotSupportedException)
        {
            _info.Text = T("Radar alınamadı");
            _reload.Interval = TimeSpan.FromMinutes(1);
        }
        _reload.Start();
    }

    private static async Task<BitmapSource> Fetch(int n)
    {
        using var req = new HttpRequestMessage(HttpMethod.Get, string.Format(CultureInfo.InvariantCulture, Url, n));
        req.Headers.TryAddWithoutValidation("Referer", "https://www.mgm.gov.tr/");
        req.Headers.TryAddWithoutValidation("User-Agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64)");
        using var resp = await Net.Client.SendAsync(req);
        resp.EnsureSuccessStatusCode();
        var bytes = await resp.Content.ReadAsByteArrayAsync();
        if (bytes.Length < 2 || bytes[0] != 0xFF || bytes[1] != 0xD8) throw new InvalidDataException("radar görüntüsü alınamadı");
        var bmp = new BitmapImage();
        bmp.BeginInit();
        bmp.StreamSource = new MemoryStream(bytes);
        bmp.CacheOption = BitmapCacheOption.OnLoad;
        bmp.EndInit();
        bmp.Freeze();
        return bmp;
    }

    private static Point RadarXy(double lat, double lon)
    {
        double u = lon - 35.0, v = lat - 39.0;
        double[] f = { 1.0, u, v, u * v, u * u, v * v };
        return new Point(f.Zip(Cx, (a, c) => a * c).Sum(), f.Zip(Cy, (a, c) => a * c).Sum());
    }

    /// Şehrin çevresini kapsayan kırpma kutusu ve şehrin kutu içindeki konumu; radar dışındaysa null.
    private static (Int32Rect Box, Point Pos)? CityBox(Location loc)
    {
        var p = RadarXy(loc.Lat, loc.Lon);
        if (!(Map.X + 10 <= p.X && p.X <= Map.Width - 10 && Map.Y + 10 <= p.Y && p.Y <= Map.Height - 10)) return null;
        int bx = (int)Math.Min(Math.Max(Map.X, p.X - CropW / 2.0), Map.Width - CropW);
        int by = (int)Math.Min(Math.Max(Map.Y, p.Y - CropH / 2.0), Map.Height - CropH);
        return (new Int32Rect(bx, by, CropW, CropH), new Point(p.X - bx, p.Y - by));
    }

    private void SetMode(string mode)
    {
        _cfg.Set("radar_mode", mode);
        _app.SaveConfig();
        Rebuild();
    }

    private void Rebuild()
    {
        var loc = _app.CurrentLocation;
        _cityKey = loc.Key;
        var cb = CityBox(loc);
        _city.Content = T("Şehrim ({})").F(Fmt.LocShort(loc));
        _city.IsEnabled = cb is not null;
        bool city = _cfg.Get("radar_mode", "tr") == "city" && cb is not null;
        _tr.IsChecked = !city;
        _city.IsChecked = city;
        _marker.Children.Clear();
        _time.Source = null;
        if (city && cb is { } c)
        {
            double k = _vw / CropW;
            double r = Math.Max(7, _vw * 0.012), x = c.Pos.X * k, y = c.Pos.Y * k;
            foreach (var (stroke, rr, th) in new[] { (Brushes.White, r + 2, 5.0), (new SolidColorBrush(Color.FromRgb(210, 30, 30)), r, 3.0) })
            {
                var e = new Ellipse { Width = 2 * rr, Height = 2 * rr, Stroke = stroke, StrokeThickness = th };
                Canvas.SetLeft(e, x - rr);
                Canvas.SetTop(e, y - rr);
                _marker.Children.Add(e);
            }
            var name = new TextBlock { Text = Fmt.LocShort(loc), FontWeight = FontWeights.SemiBold, FontSize = Math.Max(13, _vh * 0.026), Foreground = Brushes.Black, Background = new SolidColorBrush(Color.FromArgb(200, 255, 255, 255)), Padding = new Thickness(3, 0, 3, 0) };
            Canvas.SetLeft(name, x + r + 8 + 120 > _vw ? x - r - 8 - 120 : x + r + 8);
            Canvas.SetTop(name, y - 10);
            _marker.Children.Add(name);
            // 1° boylam bu haritada ~35,2 px ve Türkiye enleminde ~86 km
            int km = (int)(Math.Round(CropW / 35.2 * 86 / 50) * 50);
            _note.Text = T("{} çevresi (yaklaşık {} km genişlik) · kaynak: MGM radar kompoziti · saat UTC").F(Fmt.LocTitle(loc), km);
        }
        else
        {
            _note.Text = T("Kaynak: MGM radar kompoziti · son 15 görüntü, yaklaşık 6 dakika arayla · saat görüntünün sağ altında (UTC)")
                         + (cb is null ? T(" · bu şehir radar alanının dışında") : "");
        }
        _i = Math.Min(_i, _frames.Count - 1);
        Show();
    }

    private void Show()
    {
        if (_frames.Count == 0) return;
        var src = _frames[_i];
        var cb = CityBox(_app.CurrentLocation);
        if (_cfg.Get("radar_mode", "tr") == "city" && cb is { } c && src.PixelWidth >= 880)
        {
            _view.Source = new CroppedBitmap(src, c.Box);
            _time.Source = new CroppedBitmap(src, TimeBox);   // özgün görüntüdeki saat (UTC)
        }
        else _view.Source = src;
        _setting = true;
        _slider.Value = _i + 1;
        _setting = false;
        _info.Text = T("Görüntü {}/{}").F(_i + 1, _frames.Count) + (_i == _frames.Count - 1 ? T("  (en yeni)") : "");
    }

    private void Step()
    {
        if (_frames.Count == 0 || !_playing) { _step.Stop(); return; }
        _i = (_i + 1) % _frames.Count;
        Show();
        _step.Interval = TimeSpan.FromMilliseconds(_i == _frames.Count - 1 ? 1500 : 320);
    }

    private void Toggle()
    {
        _playing = !_playing;
        _play.Content = _playing ? T("Duraklat") : T("Oynat");
        if (_playing) { _step.Interval = TimeSpan.FromMilliseconds(320); _step.Start(); }
        else _step.Stop();
    }
}
