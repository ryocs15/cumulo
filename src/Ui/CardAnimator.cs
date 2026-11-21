using System.Diagnostics;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Media;
using System.Windows.Threading;

namespace HavaDurumu.Ui;

/// Efekt katmanı: tek bir DrawingVisual. Her karede yalnızca bu görsel yeniden kaydedilir
/// (yerleşim / ölçüm yok); arka planla yazılar arasında durur, tıklamaları geçirir.
public sealed class FxHost : FrameworkElement
{
    private readonly DrawingVisual _visual = new();
    private AmbientFx? _fx;

    public FxHost()
    {
        IsHitTestVisible = false;
        AddVisualChild(_visual);
    }

    protected override int VisualChildrenCount => 1;
    protected override Visual GetVisualChild(int index) => _visual;

    public void SetFx(AmbientFx? fx, double w, double h, double radius)
    {
        if (!ReferenceEquals(fx, _fx))
        {
            _fx = fx;
            if (fx is null) using (_visual.RenderOpen()) { }   // efekt kalkınca katmanı boşalt
        }
        Clip = new RectangleGeometry(new Rect(0, 0, w, h), radius, radius);   // kartın yuvarlak köşeleri
    }

    public void Draw(double t)
    {
        if (_fx is null) return;
        using var dc = _visual.RenderOpen();
        _fx.Draw(dc, t);
    }
}

/// Animasyonlu ikon ve efekt katmanı olan kart.
public interface IAnimatedCard
{
    FxHost FxLayer { get; }
    Image IconImage { get; }
}

/// Kart animasyonlarını yürütür. Kareler WPF'in ekran yenilemesine bağlı olayından
/// (CompositionTarget.Rendering) gelir ve 60 fps ile sınırlanır; ikon ve efekt aynı karede çizilir
/// (120/144 Hz ekranda her yenilemede çizmek işlemciyi ve dwm'i boşuna yorar).
/// - İkon: döngünün kareleri bir kez bitmap'e çizilip önbellekte durur; karede yalnızca Image.Source değişir.
/// - Widget görünmüyorsa (başka pencerelerin altında) kare olayından çıkılır; 400 ms'de bir denetlenir.
public sealed class CardAnimator
{
    private const double FxSec = 1.0 / 60;
    private readonly DispatcherTimer _seenTimer = new() { Interval = TimeSpan.FromMilliseconds(400) };
    private readonly Stopwatch _clock = Stopwatch.StartNew();
    private readonly List<(IAnimatedCard Card, CardModel Model)> _targets = new();
    private readonly Dictionary<IAnimatedCard, (string Key, AmbientFx Fx)> _fxCache = new();
    private readonly Func<bool> _isSeen;
    private readonly Func<double> _pixelScale;
    private bool _hooked;
    private double _next;
    private int _lastFrame = -1;
    private int _iconFrames, _ticks;
    private double _rateT;

    /// isSeen: pencere ekranda görünüyor mu; pixelScale: tasarım biriminden fiziksel piksele (DPI x ölçek).
    public CardAnimator(Func<bool> isSeen, Func<double> pixelScale)
    {
        _isSeen = isSeen;
        _pixelScale = pixelScale;
        _seenTimer.Tick += (_, _) => CheckSeen();
    }

    /// Bu kartları canlandır (öncekiler bırakılır). Model değişince yeniden çağrılır.
    public void SetTargets(IEnumerable<(IAnimatedCard Card, CardModel Model)> targets)
    {
        _targets.Clear();
        foreach (var (card, model) in targets)
        {
            AttachFx(card, model);
            card.IconImage.Source = model.Icon;   // sabit ikon; animasyonluysa karede değişir
            _targets.Add((card, model));
        }
        _lastFrame = -1;
        if (_targets.Any(x => x.Model.IconAnimated || x.Model.FxKind is not null))
        {
            _seenTimer.Start();
            CheckSeen();
            Draw(_clock.Elapsed.TotalSeconds, fx: true);
        }
        else Stop();
    }

    public void Stop()
    {
        _seenTimer.Stop();
        Hook(false);
        _targets.Clear();
    }

    /// Ekran kilitli / kapalı ya da oturum başka kullanıcıda: kare olayından çıkılır, pencere denetlenmez.
    public bool Suspended
    {
        get => _suspended;
        set
        {
            if (value == _suspended) return;
            _suspended = value;
            Core.Log.WriteDebug(value ? "animasyon: ekran kilitli / kapalı, duraklatıldı" : "animasyon: ekran açıldı");
            if (_seenTimer.IsEnabled) CheckSeen();
        }
    }

    private bool _suspended;

    private void CheckSeen()
    {
        if (_suspended)
        {
            Hook(false);
            return;
        }
        bool seen = _isSeen();
        if (seen != _hooked)
            Core.Log.WriteDebug(seen ? "animasyon: widget görünür, devam" : "animasyon: widget görünmüyor, duraklatıldı");
        Hook(seen);
    }

    private void Hook(bool on)
    {
        if (on == _hooked) return;
        _hooked = on;
        if (on) CompositionTarget.Rendering += OnRendering;
        else CompositionTarget.Rendering -= OnRendering;
    }

    private void OnRendering(object? sender, EventArgs e)
    {
        double t = _clock.Elapsed.TotalSeconds;
        _ticks++;
        // 60 fps: sabit adımlı hedef zaman (Python _fx_next). Bu olay düzenli gelmez: 144 Hz ekranda, çizim olmayan
        // karelerde WPF döngüyü yavaşlatır (~77/sn ölçüldü). "Son çizimden 1/60 sn geçti mi" ~40 fps verir; yarım kare
        // toleranslı hedef zaman ortalamayı 60'a yaklaştırır.
        if (t + FxSec / 2 < _next) return;
        _next = _next + FxSec < t ? t + FxSec : _next + FxSec;
        Draw(t, fx: true);
        _iconFrames++;
        if (t - _rateT >= 5)   // HAVADURUMU_DEBUG=1: ikon ve efekt hızını widget.log'a yazar
        {
            Core.Log.WriteDebug($"animasyon: {_iconFrames / (t - _rateT):F0} kare/sn (ekran olayı {_ticks / (t - _rateT):F0}/sn)");
            _ticks = 0;
            _rateT = t;
            _iconFrames = 0;
        }
    }

    private void AttachFx(IAnimatedCard card, CardModel m)
    {
        if (m.FxKind is not string kind)
        {
            card.FxLayer.SetFx(null, m.Width, m.Height, m.Radius.TopLeft);
            _fxCache.Remove(card);
            return;
        }
        // Aynı tür ve boyutta parçacıklar korunur: şehir değişince efekt baştan başlamasın.
        var key = $"{kind}|{m.IconNight}|{m.Width}x{m.Height}|{m.IconLight}|{m.FxLevel}|{m.SunCenter}|{m.SunR}";
        if (!_fxCache.TryGetValue(card, out var cached) || cached.Key != key)
        {
            cached = (key, new AmbientFx(kind, m.Width, m.Height, m.IconLight, m.FxLevel, m.SunCenter, m.SunR, m.IconNight));
            _fxCache[card] = cached;
        }
        card.FxLayer.SetFx(cached.Fx, m.Width, m.Height, m.Radius.TopLeft);
    }

    private static int FrameAt(double t) =>
        (int)(t / WeatherIcons.CycleSec * WeatherIcons.AnimFrames) % WeatherIcons.AnimFrames;

    private void Draw(double t, bool fx)
    {
        int frame = FrameAt(t);
        bool newFrame = frame != _lastFrame;
        _lastFrame = frame;
        double s = _pixelScale();
        foreach (var (card, m) in _targets)
        {
            if (m.IconAnimated && newFrame)
            {
                int px = (int)Math.Round(card.IconImage.Width * s);
                card.IconImage.Source = WeatherIcons.GetBitmap(m.IconCode, m.IconNight, m.IconLight, frame, px);
            }
            if (fx && m.FxKind is not null) card.FxLayer.Draw(t);
        }
    }
}
