namespace HavaDurumu.Core;

/// Python sürümündeki sabitler ve eşlemeler (hava_widget.py) birebir taşındı.
public static class Constants
{
#if UPDATE_TEST_V2
    public const string AppVersion = "1.7.1";   // yalnızca güncelleme testi derlemesi (C-4)
#else
    public const string AppVersion = "1.7.0";
#endif

    /// Görünen ad. exe (HavaDurumu.exe), kurulum klasörü, AUMID ve indirme dosyası adları
    /// güncelleme zinciri kırılmasın diye BİLEREK eski kalır.
    public const string AppName = "Cumulo";

    // Güncelleme: GitHub Releases. İlki sorgulanan depo; indirme adresi listedekilerden birine ait olmalı
    // (depo ryocs15/hava-durumu-widget iken cumulo'ya yeniden adlandırıldı, GitHub eski adresi yönlendiriyor).
#if UPDATE_TEST
    // Güncelleme testi derlemesi (C-4): geçici gizli test deposu. Yayın derlemesinde yok.
    public static readonly string[] UpdateRepos = { "ryocs15/cumulo-test" };
#else
    public static readonly string[] UpdateRepos = { "ryocs15/cumulo", "ryocs15/hava-durumu-widget" };
#endif
    // Kurulum dosyası iki adla yüklenir (aynı dosya): Cumulo-Setup.exe insanlar ve 1.7+ için,
    // HavaDurumu-Kurulum.exe 1.4.1–1.6.0 (Python) istemcilerinin güncellemesi için.
    public const string UpdateSetup = "Cumulo-Setup.exe";
    public const string UpdateSetupOld = "HavaDurumu-Kurulum.exe";

    public const string MgmApi = "https://servis.mgm.gov.tr/web";
    public const string OpenMeteoForecast = "https://api.open-meteo.com/v1/forecast";
    public const string OpenMeteoAir = "https://air-quality-api.open-meteo.com/v1/air-quality";
    public const string OpenMeteoGeocode = "https://geocoding-api.open-meteo.com/v1/search";

    public const int MissingValue = -9999;

    /// MGM hadise kodu -> Türkçe ad.
    public static readonly IReadOnlyDictionary<string, string> Hadise = new Dictionary<string, string>
    {
        ["A"] = T("Açık"), ["AB"] = T("Az Bulutlu"), ["PB"] = T("Parçalı Bulutlu"), ["CB"] = T("Çok Bulutlu"),
        ["HY"] = T("Hafif Yağmurlu"), ["Y"] = T("Yağmurlu"), ["KY"] = T("Kuvvetli Yağmurlu"),
        ["KKY"] = T("Karla Karışık Yağmurlu"), ["HKY"] = T("Hafif Kar Yağışlı"), ["K"] = T("Kar Yağışlı"),
        ["YKY"] = T("Yoğun Kar Yağışlı"), ["HSY"] = T("Hafif Sağanak Yağışlı"), ["SY"] = T("Sağanak Yağışlı"),
        ["KSY"] = T("Kuvvetli Sağanak Yağışlı"), ["MSY"] = T("Mevzi Sağanak Yağışlı"), ["DY"] = T("Dolu"),
        ["GSY"] = T("Gökgürültülü Sağanak Yağışlı"), ["KGSY"] = T("Kuvvetli Gökgürültülü Sağanak Yağışlı"),
        ["SIS"] = T("Sisli"), ["PUS"] = T("Puslu"), ["DMN"] = T("Dumanlı"), ["KF"] = T("Toz veya Kum Fırtınası"),
        ["R"] = T("Rüzgarlı"), ["GKR"] = T("Güneyli Kuvvetli Rüzgar"), ["KKR"] = T("Kuzeyli Kuvvetli Rüzgar"),
        ["SCK"] = T("Sıcak"), ["SGK"] = T("Soğuk"), ["HHY"] = T("Yağışlı"),
    };

    public static readonly IReadOnlyDictionary<string, int> Rain = new Dictionary<string, int>
    {
        ["HY"] = 2, ["HSY"] = 2, ["HHY"] = 2, ["Y"] = 3, ["SY"] = 3, ["MSY"] = 3, ["KY"] = 4, ["KSY"] = 4,
    };

    public static readonly IReadOnlyDictionary<string, int> Snow = new Dictionary<string, int>
    {
        ["HKY"] = 2, ["K"] = 3, ["YKY"] = 4,
    };

    /// Open-Meteo WMO kodu -> MGM hadise kodu.
    public static readonly IReadOnlyDictionary<int, string> Wmo = new Dictionary<int, string>
    {
        [0] = "A", [1] = "AB", [2] = "PB", [3] = "CB", [45] = "SIS", [48] = "SIS", [51] = "HY", [53] = "HY",
        [55] = "Y", [56] = "KKY", [57] = "KKY", [61] = "HY", [63] = "Y", [65] = "KY", [66] = "KKY", [67] = "KKY",
        [71] = "HKY", [73] = "K", [75] = "YKY", [77] = "HKY", [80] = "HSY", [81] = "SY", [82] = "KSY",
        [85] = "HKY", [86] = "YKY", [95] = "GSY", [96] = "KGSY", [99] = "KGSY",
    };

    public static readonly IReadOnlyDictionary<string, string> Pollen = new Dictionary<string, string>
    {
        ["alder"] = T("Kızılağaç"), ["birch"] = T("Huş"), ["grass"] = T("Çim"), ["mugwort"] = T("Pelin"),
        ["olive"] = T("Zeytin"), ["ragweed"] = T("Ambrosia"),
    };

    public static readonly IReadOnlyDictionary<string, string> AlertColorNames = new Dictionary<string, string>
    {
        ["yellow"] = T("Sarı"), ["orange"] = T("Turuncu"), ["red"] = T("Kırmızı"),
    };

    public static readonly IReadOnlyDictionary<string, string> AlertKinds = new Dictionary<string, string>
    {
        ["wind"] = T("Kuvvetli rüzgar"), ["rain"] = T("Kuvvetli yağış"), ["thunderstorm"] = T("Gök gürültülü sağanak"),
        ["snow"] = T("Kar"), ["cold"] = T("Soğuk hava / don"), ["hot"] = T("Sıcak hava"), ["fog"] = T("Sis"),
        ["ice"] = T("Buzlanma"), ["dust"] = T("Toz taşınımı"), ["avalanche"] = T("Çığ"), ["snowmelt"] = T("Kar erimesi"),
        ["flood"] = T("Sel"), ["agricultural"] = T("Zirai don"), ["storm"] = T("Fırtına"),
    };
}
