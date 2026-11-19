using System.Text.Json.Nodes;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Input;
using System.Windows.Interop;
using System.Windows.Media;
using System.Windows.Media.Animation;
using System.Windows.Threading;
using HavaDurumu.Core;
using HavaDurumu.Data;
using Microsoft.Win32;

namespace HavaDurumu.Ui;

/// Masaüstü widget penceresi. Python WeatherWidget sınıfının pencere, etkileşim ve zamanlayıcı kısmı.
/// CPU: boşta hiçbir kare çizilmez; yalnızca veri / saat değişince kart yeniden bağlanır.
/// Büyüme animasyonu clip + opaklık ile GPU'da (WPF composition) yapılır.
public partial class WidgetWindow : Window
{
    private static readonly Location DefaultLoc = new()
    {
        Il = "Ankara", Ilce = "Keçiören", MerkezId = JsonValue.Create(90601), Sondurum = 17130,
        Gunluk = 90601, Saatlik = 17130, Lat = 39.9727, Lon = 32.8637,
    };

    private readonly AppPaths _paths;
    private readonly AppConfig _cfg;
    private readonly WeatherCache _cache;
    private readonly Dictionary<string, string> _errors = new();
    private List<Location> _locs;
    private bool _placeholder;   // ilk açılış: varsayılan şehir, eklenen ilk şehir onun yerine geçer (Python placeholder)
    private int _idx;

    private IntPtr _hwnd;
    private double _dpi = 1.0;
    private int _x, _y, _w, _h;               // pencere, fiziksel piksel
    private (int X, int Y)? _collapsedPos;    // büyütülmeden önceki küçük kartın konumu

    private string _smallLayout = "";
    private UserControl? _smallView;
    private DetailCard? _bigView;

    private bool _expanded;
    private bool _animating;
    private bool _hoverArmed = true;
    private int _expIdle;
    private bool _refreshing;

    private readonly DispatcherTimer _hoverTimer = new() { Interval = TimeSpan.FromMilliseconds(250) };
    private readonly DispatcherTimer _leaveTimer = new() { Interval = TimeSpan.FromMilliseconds(120) };
    private readonly DispatcherTimer _clickTimer = new() { Interval = TimeSpan.FromMilliseconds(260) };
    private readonly DispatcherTimer _expTimer = new() { Interval = TimeSpan.FromSeconds(1) };
    private readonly DispatcherTimer _refreshTimer = new();
    private readonly DispatcherTimer _rotateTimer = new();
    private readonly DispatcherTimer _minuteTimer = new() { Interval = TimeSpan.FromSeconds(30) };

    private bool _pressed, _moved;
    private Native.POINT _pressCursor;
    private int _pressX, _pressY;

    private int _page;                         // mini sayfası: 0 şimdi, 1 saatlik, 2 yarın
    private int _multiCount;                   // row/col: çizilen kart sayısı
    private HoverPopup? _popup;
    private int _popupCandidate = -1;
    private readonly DispatcherTimer _popupTimer = new() { Interval = TimeSpan.FromMilliseconds(600) };
    private readonly CardAnimator _animator;

    public WidgetWindow(AppPaths paths)
    {
        _paths = paths;
        Log.FilePath = paths.LogFile;
        _cfg = AppConfig.Load(paths.Config);
        if (_cfg.LoadFailed) Log.Write("ayar dosyası okunamadı; varsayılanlarla açılıyor, dosyaya yazılmayacak");
        _cache = new WeatherCache(paths.Cache);
        _cache.Load();
        _locs = _cfg.GetLocations();
        _placeholder = !_cfg.Keys.Contains("locs") && !_cfg.Keys.Contains("loc");
        if (_locs.Count == 0) _locs.Add(DefaultLoc);
        _idx = Math.Clamp(_cfg.Get("idx", 0), 0, _locs.Count - 1);
        Units.Fahrenheit = _cfg.Get("units_temp", "C") == "F";
        Units.WindMs = _cfg.Get("units_wind", "kmh") == "ms";

        InitializeComponent();
        _animator = new CardAnimator(() => Native.IsSeen(_hwnd, _x, _y, _w, _h), () => S);

        SourceInitialized += OnSourceInitialized;
        ContentRendered += (_, _) => ApplyZOrder();   // Show() pencereyi en üste taşır; "Masaüstünde" ise geri sabitle
        MouseEnter += (_, _) => OnHoverEnter();
        MouseLeave += (_, _) => { OnHoverLeave(); PopupHide(); };
        MouseMove += (_, _) => PopupCheck();
        PreviewMouseLeftButtonDown += OnPress;
        PreviewMouseMove += OnDrag;
        PreviewMouseLeftButtonUp += OnRelease;
        MouseRightButtonUp += (_, e) => { ShowMenu(); e.Handled = true; };
        MouseWheel += OnWheel;

        _hoverTimer.Tick += (_, _) => { _hoverTimer.Stop(); if (CursorOnWindow()) Expand(true); };
        _leaveTimer.Tick += (_, _) => { if (_expanded && !_animating && !CursorOnWindow() && !MenuOpen) OnHoverLeave(); };
        _clickTimer.Tick += (_, _) => { _clickTimer.Stop(); ClickAction(); };
        _expTimer.Tick += (_, _) => ExpandIdleTick();
        _refreshTimer.Tick += async (_, _) => await RefreshAsync();
        _rotateTimer.Tick += (_, _) => RotateTick();
        _minuteTimer.Tick += (_, _) => { Render(); MinuteTick(); };
        _popupTimer.Tick += (_, _) =>
        {
            _popupTimer.Stop();
            if (_popupCandidate >= 0 && CursorOnWindow()) PopupShow(_popupCandidate);
        };
    }

    private string Layout => Layouts.Supported(_cfg.Get("layout", "mini"));
    private double Scale => Math.Clamp(_cfg.Get("scale", 1.0), 0.5, 2.0);
    private double S => _dpi * Scale;
    private int Px(double v) => (int)Math.Round(v * S);
    private bool HoverExpandOn => Layouts.IsSmall(Layout) && _cfg.Get("mini_hover", "expand") == "expand";
    private bool MenuOpen => ContextMenu is { IsOpen: true };
    private Location Loc => _locs[Math.Clamp(_idx, 0, _locs.Count - 1)];

    // ------------------------------------------------------------------ pencere
    private void OnSourceInitialized(object? sender, EventArgs e)
    {
        _hwnd = new WindowInteropHelper(this).Handle;
        _dpi = VisualTreeHelper.GetDpi(this).DpiScaleX;
        // Farklı ölçekli ekrana geçince WPF içeriği yeni ölçekle çizer; pencere boyutu (fiziksel piksel) da yeniden
        // hesaplanmazsa kart kırpılır ya da boşluk kalır.
        DpiChanged += (_, e) =>
        {
            if (Math.Abs(e.NewDpi.DpiScaleX - _dpi) < 0.001) return;
            _dpi = e.NewDpi.DpiScaleX;
            ApplySettingsChanged(relayout: true);
        };
        Native.SetToolWindow(_hwnd);
        StageScale.ScaleX = StageScale.ScaleY = Scale;

        var (dw, dh) = Layouts.WindowSize(Layout, _locs.Count);
        _w = Px(dw);
        _h = Px(dh);
        int? cx = _cfg.Get<int?>("x", null), cy = _cfg.Get<int?>("y", null);
        double vx = SystemParameters.VirtualScreenLeft * _dpi, vy = SystemParameters.VirtualScreenTop * _dpi;
        double vw = SystemParameters.VirtualScreenWidth * _dpi, vh = SystemParameters.VirtualScreenHeight * _dpi;
        if (cx is int x && cy is int y && vx - 50 <= x && x <= vx + vw - 50 && vy <= y && y <= vy + vh - 50)
        {
            _x = x;
            _y = y;
        }
        else
        {
            _x = (int)(SystemParameters.PrimaryScreenWidth * _dpi) - _w - Px(32);
            _y = Px(48);
        }
        Native.Place(_hwnd, _x, _y, _w, _h);
        ApplyZOrder();
        // "Tıklamayı geçir" Python'da tepsi ikonu gerektiriyordu; tepsi yok: widget her zaman tıklanabilir.

        Render();
        StartTimers();
        InitFeatures();
        _ = RefreshAsync();
        if (_placeholder && IsMain)   // ilk açılış: şehir seçtir (İngilizcede şehir araması odakta)
            Dispatcher.BeginInvoke(() => CityManager.Open(this), System.Windows.Threading.DispatcherPriority.ApplicationIdle);
    }

    private void ApplyZOrder()
    {
        if (_cfg.Get("on_top", false)) Native.PinOnTop(_hwnd);
        else Native.PinToDesktop(_hwnd);
    }

    private void StartTimers()
    {
        _refreshTimer.Interval = TimeSpan.FromMinutes(Math.Clamp(_cfg.Get("refresh_min", 15), 5, 60));
        _refreshTimer.Start();
        _minuteTimer.Start();
        ScheduleRotate();
    }

    private void ScheduleRotate()
    {
        _rotateTimer.Stop();
        if (_cfg.Get("rotate", false) && _locs.Count > 1)
        {
            _rotateTimer.Interval = TimeSpan.FromSeconds(Math.Clamp(_cfg.Get("rotate_sec", 10), 5, 300));
            _rotateTimer.Start();
        }
    }

    private void RotateTick()
    {
        // imleç üstündeyken ya da kart büyükken şehir değişmesin
        if (_expanded || _pressed || MenuOpen || CursorOnWindow() || Layouts.IsMulti(Layout)) return;
        _idx = (_idx + 1) % _locs.Count;
        Render();
    }

    // ------------------------------------------------------------------ çizim
    private CardSettings Settings()
    {
        var s = CardSettings.From(_cfg);
        if (s.Theme == "system") s = s with { Theme = WindowsLightTheme() ? "light" : "dark" };
        return s;
    }

    private CardModel BuildModel(string layout, int? index = null, int? multi = null, CardSettings? settings = null)
    {
        int i = index ?? _idx;
        var loc = _locs[Math.Clamp(i, 0, _locs.Count - 1)];
        _cache.ById.TryGetValue(loc.Key, out var data);
        _errors.TryGetValue(loc.Key, out var err);
        return CardBuilder.Build(layout, loc, data, err, _cache.Alerts, settings ?? Settings(), i, _locs.Count,
            DateTime.UtcNow, _page, multi);
    }

    /// Kartı veriye yeniden bağlar. Görünüm nesnesi düzen değişmedikçe yeniden oluşturulmaz.
    private static readonly bool Debug = Environment.GetEnvironmentVariable("HAVADURUMU_DEBUG") == "1";

    private void Render()
    {
        if (!Debug)
        {
            RenderCore();
            return;
        }
        var sw = System.Diagnostics.Stopwatch.StartNew();
        var cpu0 = System.Diagnostics.Process.GetCurrentProcess().TotalProcessorTime;
        RenderCore();
        long build = sw.ElapsedMilliseconds;
        Dispatcher.InvokeAsync(() =>
        {
            var cpu = System.Diagnostics.Process.GetCurrentProcess().TotalProcessorTime - cpu0;
            Log.Write($"render: model {build} ms, boşa kadar {sw.ElapsedMilliseconds} ms, CPU {cpu.TotalMilliseconds:0} ms");
        }, DispatcherPriority.ApplicationIdle);
    }

    private void RenderCore()
    {
        var layout = Layout;
        if (Layouts.IsMulti(layout))
        {
            RenderMulti(layout);
            return;
        }
        if (_smallView is null || _smallLayout != layout)
        {
            _smallView = CardViews.Create(layout);
            _smallLayout = layout;
            SmallHost.Content = _smallView;
        }
        _smallView.DataContext = BuildModel(layout);
        if (_expanded && _bigView is not null)
            _bigView.DataContext = BuildModel(DetailLayout);
        if (_popup is { Index: >= 0 } p) PopupShow(p.Index);
        UpdateAnimation();
    }

    /// Görünen kartları animasyona verir: küçük kart (büyükken görünmez, canlandırılmaz) ya da büyük kart,
    /// row/col'da tüm kartlar.
    private void UpdateAnimation()
    {
        var targets = new List<(IAnimatedCard, CardModel)>();
        if (SmallHost.Content is StackPanel panel)
        {
            foreach (var child in panel.Children)
                if (child is IAnimatedCard c and FrameworkElement { DataContext: CardModel m }) targets.Add((c, m));
        }
        else if (_expanded && !_animating && _bigView is { DataContext: CardModel bm })
        {
            targets.Add((_bigView, bm));
        }
        else if (_smallView is IAnimatedCard sc && _smallView.DataContext is CardModel sm)
        {
            targets.Add((sc, sm));
            if (_expanded && _bigView is { DataContext: CardModel bm2 }) targets.Add((_bigView, bm2));
        }
        _animator.SetTargets(targets);
    }

    /// Yan yana / alt alta: her şehir için bir mini kart (en çok 5), aralarında 6 px boşluk.
    private void RenderMulti(string layout)
    {
        int n = Math.Clamp(_locs.Count, 1, Layouts.MultiMax);
        if (SmallHost.Content is not StackPanel panel || _smallLayout != layout || _multiCount != n)
        {
            panel = new StackPanel { Orientation = layout == "row" ? Orientation.Horizontal : Orientation.Vertical };
            for (int i = 0; i < n; i++)
            {
                var card = new MiniCard();
                if (i > 0)
                    card.Margin = layout == "row" ? new Thickness(Layouts.MultiGap, 0, 0, 0) : new Thickness(0, Layouts.MultiGap, 0, 0);
                panel.Children.Add(card);
            }
            SmallHost.Content = panel;
            _smallView = null;
            _smallLayout = layout;
            _multiCount = n;
        }
        var settings = Settings();
        for (int i = 0; i < n; i++)
            ((MiniCard)panel.Children[i]).DataContext = BuildModel("mini", i, i, settings);
        if (_popup is { Index: >= 0 } p) PopupShow(p.Index);
        UpdateAnimation();
    }

    private string DetailLayout => _cfg.Get("expand_detail", "rich") == "compact" ? "compact" : "peek";

    // ------------------------------------------------------------------ veri
    /// Yalnızca ölçüm/test için: ağ yenilemesini kapatır (önbellekteki veriyle çalışır).
    private static readonly bool NoRefresh = Environment.GetEnvironmentVariable("HAVADURUMU_NO_REFRESH") == "1";

    private async Task RefreshAsync()
    {
        if (_refreshing || NoRefresh) return;
        _refreshing = true;
        try
        {
            var order = Enumerable.Range(0, _locs.Count).Select(i => (i + _idx) % _locs.Count).ToList();
            if (_locs.Any(l => !l.IsOpenMeteo))
            {
                try { _cache.Alerts = await AlertService.FetchAsync(CancellationToken.None); }
                catch (Exception ex) { Log.Write("uyarılar alınamadı: " + ex.Message); }
            }
            foreach (var i in order)
            {
                var loc = _locs[i];
                try
                {
                    _cache.ById[loc.Key] = await WeatherService.FetchAsync(loc, CancellationToken.None);
                    _errors.Remove(loc.Key);
                }
                catch (Exception ex)
                {
                    Log.Write($"veri alınamadı ({Fmt.LocShort(loc)}): {ex.Message}");
                    _errors[loc.Key] = ex is System.Net.Http.HttpRequestException or TaskCanceledException ? T("Bağlantı yok") : T("Veri alınamadı");
                }
                Render();
            }
            _cache.Save(_locs.Select(l => l.Key));
        }
        finally
        {
            _refreshing = false;
        }
        AfterRefresh();
        _refreshTimer.Stop();   // başarısız şehir varsa 1 dk sonra yeniden dene
        _refreshTimer.Interval = _errors.Count > 0 ? TimeSpan.FromMinutes(1) : TimeSpan.FromMinutes(Math.Clamp(_cfg.Get("refresh_min", 15), 1, 60));
        _refreshTimer.Start();
    }

    // ------------------------------------------------------------------ büyüme (üzerine gelince / tıklayınca)
    private void OnHoverEnter()
    {
        if (!HoverExpandOn || _expanded || _pressed || !_hoverArmed) return;
        _hoverTimer.Stop();
        _hoverTimer.Start();
    }

    private void OnHoverLeave()
    {
        _hoverTimer.Stop();
        if (MenuOpen || _pressed) return;
        if (_expanded && HoverExpandOn && !CursorOnWindow())
        {
            Expand(false);
            _hoverArmed = !CursorInSmall();
        }
        else if (!_expanded)
        {
            _hoverArmed = true;
        }
    }

    private bool CursorOnWindow()
    {
        if (!Native.GetCursorPos(out var p)) return false;
        return _x <= p.X && p.X < _x + _w && _y <= p.Y && p.Y < _y + _h && IsMouseOver;
    }

    private bool CursorInSmall()
    {
        if (!Native.GetCursorPos(out var p)) return false;
        var (x, y) = _collapsedPos ?? (_x, _y);
        var (dw, dh) = Layouts.Size(Layout);
        return x <= p.X && p.X < x + Px(dw) && y <= p.Y && p.Y < y + Px(dh);
    }

    private void Expand(bool on)
    {
        if (on == _expanded || _animating || (on && !Layouts.IsSmall(Layout))) return;
        var (sw, sh) = Layouts.Size(Layout);

        if (on)
        {
            var layout = DetailLayout;
            var (bw, bh) = Layouts.Size(layout);
            int nw = Px(bw), nh = Px(bh);
            var wa = Native.WorkArea(_x + _w / 2, _y + _h / 2);
            int nx = Math.Max(wa.Left, Math.Min(_x, wa.Right - nw));   // ekrandan taşacaksa sola / yukarı büyür
            int ny = Math.Max(wa.Top, Math.Min(_y, wa.Bottom - nh));
            _collapsedPos = (_x, _y);
            PopupHide();

            _bigView ??= new DetailCard();
            _bigView.DataContext = BuildModel(layout);
            BigHost.Content = _bigView;

            double ox = (_x - nx) / S, oy = (_y - ny) / S;
            Canvas.SetLeft(SmallHost, ox);
            Canvas.SetTop(SmallHost, oy);
            SmallHost.IsHitTestVisible = false;
            _expanded = true;
            SetWindowRect(nx, ny, nw, nh);

            double r = ((CardModel)_bigView.DataContext).Radius.TopLeft;
            var clip = new RectangleGeometry(new Rect(ox, oy, sw, sh), r, r);
            BigHost.Clip = clip;
            BigHost.Visibility = Visibility.Visible;
            UpdateAnimation();   // büyüme sırasında ikisi birlikte canlı
            Animate(clip, new Rect(0, 0, bw, bh), 180, 0.0, () =>
            {
                BigHost.Clip = null;
                _leaveTimer.Start();
                UpdateAnimation();   // artık yalnızca büyük kart
            });
            if (!HoverExpandOn)
            {
                _expIdle = 0;
                _expTimer.Start();   // tıklayarak büyüdüyse 6 sn sonra kendiliğinden küçülür
            }
        }
        else
        {
            _leaveTimer.Stop();
            _expTimer.Stop();
            var big = (CardModel)_bigView!.DataContext;
            double ox = Canvas.GetLeft(SmallHost), oy = Canvas.GetTop(SmallHost);
            var clip = new RectangleGeometry(new Rect(0, 0, big.Width, big.Height), big.Radius.TopLeft, big.Radius.TopLeft);
            BigHost.Clip = clip;
            Animate(clip, new Rect(ox, oy, sw, sh), 130, 1.0, () =>
            {
                BigHost.Visibility = Visibility.Collapsed;
                BigHost.Clip = null;
                Canvas.SetLeft(SmallHost, 0);
                Canvas.SetTop(SmallHost, 0);
                SmallHost.IsHitTestVisible = true;
                var (cx, cy) = _collapsedPos ?? (_x, _y);
                _expanded = false;
                SetWindowRect(cx, cy, Px(sw), Px(sh));
                UpdateAnimation();
                if (!_cfg.Get("on_top", false)) Native.KeepBottom(_hwnd);
            });
        }
    }

    /// Clip dikdörtgeni ve küçük kartın opaklığı aynı süreyle (hızlı başlayıp yavaşlayan).
    private void Animate(RectangleGeometry clip, Rect to, int ms, double smallOpacityTo, Action done)
    {
        _animating = true;
        var dur = new Duration(TimeSpan.FromMilliseconds(ms));
        var ease = new CubicEase { EasingMode = EasingMode.EaseOut };
        var rectAnim = new RectAnimation(to, dur) { EasingFunction = ease };
        rectAnim.Completed += (_, _) =>
        {
            _animating = false;
            done();
        };
        clip.BeginAnimation(RectangleGeometry.RectProperty, rectAnim);
        SmallHost.BeginAnimation(OpacityProperty, new DoubleAnimation(smallOpacityTo, dur) { EasingFunction = ease });
    }

    private void ExpandIdleTick()
    {
        if (!_expanded || HoverExpandOn)
        {
            _expTimer.Stop();
            return;
        }
        _expIdle = CursorOnWindow() || MenuOpen ? 0 : _expIdle + 1;
        if (_expIdle >= 6) Expand(false);
    }

    private void SetWindowRect(int x, int y, int w, int h)
    {
        _x = x; _y = y; _w = w; _h = h;
        Native.Place(_hwnd, x, y, w, h);
    }

    // ------------------------------------------------------------------ fare
    private void OnPress(object sender, MouseButtonEventArgs e)
    {
        if (e.ClickCount == 2)
        {
            _clickTimer.Stop();
            _ = RefreshAsync();
            e.Handled = true;
            return;
        }
        _pressed = true;
        _moved = false;
        _hoverTimer.Stop();
        PopupHide();
        Native.GetCursorPos(out _pressCursor);
        _pressX = _x;
        _pressY = _y;
        // yan yana / alt alta: Shift + sürükle (ya da konum kilitliyken sürükle) kartları sıralar
        _reorder = Layouts.IsMulti(Layout) && (Keyboard.Modifiers.HasFlag(ModifierKeys.Shift) || _cfg.Get("locked", false))
            ? CardIndexAtCursor() : null;
        CaptureMouse();
    }

    private int? _reorder;

    private void OnDrag(object sender, MouseEventArgs e)
    {
        if (_pressed && _reorder is int from && CardIndexAtCursor() is int to && to != from)
        {
            MoveLocation(from, to);
            _reorder = to;
            _moved = true;
            return;
        }
        if (!_pressed || _reorder is not null || _cfg.Get("locked", false) || _animating) return;
        Native.GetCursorPos(out var p);
        int nx = _pressX + p.X - _pressCursor.X, ny = _pressY + p.Y - _pressCursor.Y;
        if (!_moved && Math.Abs(nx - _x) + Math.Abs(ny - _y) <= 2) return;
        _moved = true;
        if (_cfg.Get("snap", true))
        {
            var wa = Native.WorkArea(nx + _w / 2, ny + _h / 2);
            int th = Px(14);
            if (Math.Abs(nx - wa.Left) < th) nx = wa.Left;
            else if (Math.Abs(nx + _w - wa.Right) < th) nx = wa.Right - _w;
            if (Math.Abs(ny - wa.Top) < th) ny = wa.Top;
            else if (Math.Abs(ny + _h - wa.Bottom) < th) ny = wa.Bottom - _h;
        }
        if (_expanded && _collapsedPos is var (cx, cy))
            _collapsedPos = (cx + nx - _x, cy + ny - _y);   // küçülünce de yeni yerinde olsun
        SetWindowRect(nx, ny, _w, _h);
    }

    private void OnRelease(object sender, MouseButtonEventArgs e)
    {
        if (!_pressed) return;
        _pressed = false;
        ReleaseMouseCapture();
        if (_reorder is not null)
        {
            _reorder = null;   // sıralama MoveLocation ile zaten kaydedildi
        }
        else if (_moved)
        {
            SavePosition();
        }
        else if (FindAction(e.OriginalSource as DependencyObject) is string action)
        {
            DoAction(action);
        }
        else
        {
            _clickTimer.Stop();
            _clickTimer.Start();   // çift tıklama değilse büyüt
        }
        if (!_cfg.Get("on_top", false)) Native.KeepBottom(_hwnd);
    }

    private static string? FindAction(DependencyObject? d)
    {
        while (d is not null)
        {
            if (d is FrameworkElement { Tag: string tag }) return tag;
            d = d is Visual or System.Windows.Media.Media3D.Visual3D ? VisualTreeHelper.GetParent(d) : LogicalTreeHelper.GetParent(d);
        }
        return null;
    }

    private void DoAction(string action)
    {
        if (action == "refresh") _ = RefreshAsync();
        else if (action == "alert") ShowAlerts(CardIndexAtCursor() ?? _idx);
        else if (action == "page") NextPage(1);
        else if (action.StartsWith("city:") && int.TryParse(action[5..], out var i)) SwitchTo(i);
    }

    private void ClickAction()
    {
        if (_expanded)
        {
            if (!HoverExpandOn) Expand(false);
            return;
        }
        var layout = Layout;
        if (!Layouts.IsSmall(layout)) return;
        var act = _cfg.Get("mini_click", "expand");
        if (HoverExpandOn || act == "expand") Expand(true);
        else if (act == "page" && layout is "mini" or "miniplus") NextPage(1);
    }

    /// Mini kart sayfası: 0 şimdi, 1 saatlik, 2 yarın.
    private void NextPage(int delta)
    {
        _page = ((_page + delta) % 3 + 3) % 3;
        Render();
    }

    private void OnWheel(object sender, MouseWheelEventArgs e)
    {
        int step = e.Delta > 0 ? -1 : 1;
        var layout = Layout;
        if (_cfg.Get("wheel_action", "city") == "page" && layout is "mini" or "miniplus" && !_expanded)
        {
            NextPage(step);
            return;
        }
        if (_locs.Count < 2 || Layouts.IsMulti(layout)) return;
        SwitchTo(_idx + step);
    }

    // ------------------------------------------------------------------ üzerine gelince ayrıntı kartı
    /// Python hover_check: row/col/kompakt (ve "büyüt" kapalıyken küçük kartlar) için orta boy kart.
    private bool PopupOn
    {
        get
        {
            if (!_cfg.Get("hover", true) || _expanded || _pressed || MenuOpen) return false;
            var lay = Layout;
            if (Layouts.IsSmall(lay)) return _cfg.Get("mini_hover", "expand") is not ("expand" or "none");
            return lay is "row" or "col" or "compact";
        }
    }

    /// İmlecin altındaki şehir kartı (row/col'da sıraya göre; diğerlerinde seçili şehir).
    private int? CardIndexAtCursor()
    {
        if (!Layouts.IsMulti(Layout)) return _idx;
        if (!Native.GetCursorPos(out var p)) return null;
        int cw = Px(150), ch = Px(78), gap = Px(Layouts.MultiGap);
        int i = Layout == "row" ? (p.X - _x) / (cw + gap) : (p.Y - _y) / (ch + gap);
        return i >= 0 && i < _multiCount ? i : null;
    }

    private void PopupCheck()
    {
        if (!PopupOn)
        {
            PopupHide();
            return;
        }
        var i = CardIndexAtCursor();
        if (i is not int idx) return;
        if (_popup is { Index: >= 0 } p)
        {
            if (p.Index != idx) PopupShow(idx);   // kartlar arasında gezinirken bekletme
            return;
        }
        if (idx != _popupCandidate)
        {
            _popupCandidate = idx;
            _popupTimer.Stop();
            _popupTimer.Start();
        }
        else if (!_popupTimer.IsEnabled)
        {
            _popupTimer.Start();
        }
    }

    private void PopupShow(int i)
    {
        if (i < 0 || i >= _locs.Count) return;
        var s = Settings();
        if (s.Theme == "clear") s = s with { Theme = "dark" };   // başka pencerelerin üstünde zeminsiz okunmaz
        var model = BuildModel("medium", i, -1, s);   // Python multi=("pop", …): nokta ve yenile simgesi yok
        int pw = Px(360), ph = Px(426), gap = Px(8);
        var wa = Native.WorkArea(_x + _w / 2, _y + _h / 2);
        int cx = _x, cy = _y, cwid = _w;
        var lay = Layout;
        if (Layouts.IsMulti(lay))
        {
            int cw = Px(150), ch = Px(78), g = Px(Layouts.MultiGap);
            cx = _x + (lay == "row" ? (cw + g) * i : 0);
            cy = _y + (lay == "row" ? 0 : (ch + g) * i);
            cwid = cw;
        }
        int x, y;
        if (lay == "row")   // yatay şeritte kartın altına / üstüne
        {
            x = Math.Min(Math.Max(wa.Left, cx), wa.Right - pw);
            y = _y + _h + gap + ph <= wa.Bottom ? _y + _h + gap : _y - gap - ph;
        }
        else                // sağına, yer yoksa soluna
        {
            x = cx + cwid + gap + pw <= wa.Right ? cx + cwid + gap : cx - gap - pw;
            y = Math.Min(Math.Max(wa.Top, cy), wa.Bottom - ph);
        }
        _popup ??= new HoverPopup();
        _popup.ShowCard(model, Scale, i, x, y, pw, ph);
    }

    private void PopupHide()
    {
        _popupTimer.Stop();
        _popupCandidate = -1;
        _popup?.HideCard();
    }

    private void SavePosition()
    {
        var (x, y) = _expanded && _collapsedPos is { } cp ? cp : (_x, _y);
        _cfg.Set("x", x);
        _cfg.Set("y", y);
        TrySave();
    }

    private void TrySave()
    {
        if (_cfg.LoadFailed) return;
        try { _cfg.Save(); }
        catch (Exception ex) { Log.Write("ayarlar kaydedilemedi: " + ex.Message); }
    }

    // ------------------------------------------------------------------ uyarı penceresi
    private void ShowAlerts(int index)
    {
        var loc = _locs[Math.Clamp(index, 0, _locs.Count - 1)];
        var alerts = AlertService.AlertsFor(loc, _cache.Alerts);
        if (alerts.Count > 0) Dialogs.Alert(loc, alerts);
    }

    private static bool WindowsLightTheme()
    {
        try
        {
            using var key = Registry.CurrentUser.OpenSubKey(@"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize");
            return key?.GetValue("AppsUseLightTheme") is int v && v != 0;
        }
        catch (Exception ex) when (ex is System.Security.SecurityException or UnauthorizedAccessException)
        {
            return false;
        }
    }

    protected override void OnClosed(EventArgs e)
    {
        foreach (var t in new[] { _hoverTimer, _leaveTimer, _clickTimer, _expTimer, _refreshTimer, _rotateTimer, _minuteTimer, _popupTimer, _peekTimer, _updateTimer, _cleanupTimer })
            t.Stop();
        _popup?.Close();
        _animator.Stop();
        StopSystemNotifications();
        if (_hwnd != IntPtr.Zero) Native.UnregisterHotKey(_hwnd, 1);
        Application.Current.Shutdown();   // açık diyaloglar (radar, şehirler) uygulamayı açık tutmasın
        base.OnClosed(e);
    }
}
