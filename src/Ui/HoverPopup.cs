using System.Windows;
using System.Windows.Interop;
using System.Windows.Media;

namespace HavaDurumu.Ui;

/// Üzerine gelince açılan ayrıntı kartı (Python Popup + popup_image): Orta düzende kart,
/// widget'ın yanında, her zaman üstte. Tıklamaları geçirir; fareyi widget'tan çalmaz.
public sealed class HoverPopup : Window
{
    private readonly DetailCard _card = new();
    private readonly ScaleTransform _scale = new(1, 1);
    private IntPtr _hwnd;

    public HoverPopup()
    {
        WindowStyle = WindowStyle.None;
        AllowsTransparency = true;
        Background = Brushes.Transparent;
        ShowInTaskbar = false;
        ShowActivated = false;
        Topmost = true;
        ResizeMode = ResizeMode.NoResize;
        SizeToContent = SizeToContent.Manual;
        Focusable = false;
        IsHitTestVisible = false;
        _card.LayoutTransform = _scale;
        _card.HorizontalAlignment = HorizontalAlignment.Left;
        _card.VerticalAlignment = VerticalAlignment.Top;
        Content = _card;
        SourceInitialized += (_, _) =>
        {
            _hwnd = new WindowInteropHelper(this).Handle;
            Native.SetToolWindow(_hwnd);
            Native.SetClickThrough(_hwnd, true);
        };
    }

    public int Index { get; private set; } = -1;

    /// Kartı fiziksel piksel konumunda gösterir.
    public void ShowCard(CardModel model, double scale, int index, int x, int y, int w, int h)
    {
        Index = index;
        _scale.ScaleX = _scale.ScaleY = scale;
        _card.DataContext = model;
        if (!IsVisible)
        {
            Left = -10000;
            Top = -10000;
            Show();
        }
        Native.Place(_hwnd, x, y, w, h);
    }

    public void HideCard()
    {
        Index = -1;
        if (IsVisible) Hide();
    }
}
