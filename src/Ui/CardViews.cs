using System.Windows;
using System.Windows.Controls;

namespace HavaDurumu.Ui;

/// Ortak kart davranışı: ana ikon bağlama ile değil kodla atanır (animasyon her tikte Source'u
/// değiştirir; bağlama kullanılsaydı ilk atamada bağlama silinirdi).
internal static class CardIcon
{
    public static void Hook(FrameworkElement card, Image icon) =>
        card.DataContextChanged += (_, e) =>
        {
            if (e.NewValue is CardModel m) icon.Source = m.Icon;
        };
}

public partial class MiniCard : UserControl, IAnimatedCard
{
    public MiniCard()
    {
        InitializeComponent();
        CardIcon.Hook(this, MainIcon);
    }

    public FxHost FxLayer => Fx;
    public Image IconImage => MainIcon;
}

public partial class MicroCard : UserControl, IAnimatedCard
{
    public MicroCard()
    {
        InitializeComponent();
        CardIcon.Hook(this, MainIcon);
    }

    public FxHost FxLayer => Fx;
    public Image IconImage => MainIcon;
}

public partial class VMiniCard : UserControl, IAnimatedCard
{
    public VMiniCard()
    {
        InitializeComponent();
        CardIcon.Hook(this, MainIcon);
    }

    public FxHost FxLayer => Fx;
    public Image IconImage => MainIcon;
}

public partial class DetailCard : UserControl, IAnimatedCard
{
    public DetailCard()
    {
        InitializeComponent();
        CardIcon.Hook(this, MainIcon);
    }

    public FxHost FxLayer => Fx;
    public Image IconImage => MainIcon;
}

public partial class WeeklyCard : UserControl, IAnimatedCard
{
    public WeeklyCard()
    {
        InitializeComponent();
        CardIcon.Hook(this, MainIcon);
    }

    public FxHost FxLayer => Fx;
    public Image IconImage => MainIcon;
}

public static class CardViews
{
    /// Düzene uygun kart görünümü (row/col: her şehir için bir mini kart).
    public static UserControl Create(string layout) => layout switch
    {
        "micro" => new MicroCard(),
        "vmini" => new VMiniCard(),
        "weekly" => new WeeklyCard(),
        "compact" or "peek" or "medium" or "full" => new DetailCard(),
        _ => new MiniCard(),
    };

    /// row/col düzeninde kartların çizildiği tekil düzen.
    public static string CardLayout(string layout) => Layouts.IsMulti(layout) ? "mini" : layout;
}
