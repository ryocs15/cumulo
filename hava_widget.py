"""
Cumulo — hava durumu masaüstü widget'ı (Windows 10/11)

Veriler:
- Türkiye: MGM (servis.mgm.gov.tr) — anlık ölçüm, saatlik ve 5 günlük tahmin, uyarılar, radar.
- Yurt dışı şehirler ve ek bilgiler (hava kalitesi, UV, polen, yağış olasılığı, dünle fark):
  Open-Meteo. API anahtarı gerekmez.

Kullanım: widget'a ya da tepsi ikonuna sağ tık -> menü. Sol tuşla sürükle -> taşı.
Çift tık -> yenile. Kısayol (varsayılan Win+Shift+W) -> widget'ı öne getir.
Ek widget'lar `--profile AD` ile ayrı ayar dosyasıyla (config_AD.json) çalışır.
"""

import colorsys
import ctypes
import hashlib
import io
import json
import math
import os
import pathlib
import itertools
import queue
import random
from collections import deque
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import unicodedata
import urllib.parse
import urllib.request
import webbrowser
import zipfile
import tkinter as tk
from tkinter import colorchooser, filedialog, messagebox, simpledialog, ttk
from ctypes import wintypes
from datetime import datetime, timezone, timedelta
from xml.sax.saxutils import escape as xml_escape

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont, ImageOps, ImageTk

FROZEN = getattr(sys, "frozen", False)
APP_DIR = (os.path.dirname(sys.executable) if FROZEN
           else os.path.dirname(os.path.abspath(__file__)))
CONFIG_PATH = os.path.join(APP_DIR, "config.json")
CACHE_PATH = os.path.join(APP_DIR, "cache.json")
LOG_PATH = os.path.join(APP_DIR, "widget.log")
STARTUP_VBS = os.path.join(
    os.environ.get("APPDATA", ""),
    r"Microsoft\Windows\Start Menu\Programs\Startup\HavaDurumuWidget.vbs",
)
FONT_DIR = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts")
REFRESH_MS = 10 * 60 * 1000


def ui_lang():
    """Arayüz dili: ana config.json'daki "lang" (tüm widget'lar için ortak), yoksa Windows dili."""
    try:
        with open(CONFIG_PATH, encoding="utf-8") as f:
            v = json.load(f).get("lang")
        if v in ("tr", "en"):
            return v
    except Exception:
        pass
    return "tr" if ctypes.windll.kernel32.GetUserDefaultUILanguage() & 0x3FF == 0x1F else "en"


LANG = ui_lang()  # modül tabloları içe aktarımda çevrildiği için dil değişince uygulama yeniden başlar
from lang_en import EN  # noqa: E402


def T(s, en=None):
    """Arayüz metni. Anahtar Türkçe metnin kendisi; aynı Türkçe sözcüğün farklı İngilizcesi
    gerekiyorsa (ör. "Açık": hava / tema) en= ile verilir."""
    return s if LANG == "tr" else (en or EN.get(s, s))


def pct(v):
    return f"%{v:.0f}" if LANG == "tr" else f"{v:.0f}%"

APP_VERSION = "1.6.0"
APP_NAME = "Cumulo"  # görünen ad; exe, klasör, AUMID ve indirme dosyası adları güncelleme için eski kalır
UPDATE_REPO = "ryocs15/cumulo"  # GitHub Releases: yeni sürümler buradan iner
UPDATE_ASSET = "HavaDurumu.exe"   # tek dosyalık sürüm (1.2.0 ve öncesi bununla güncellenir)
UPDATE_ZIP = "HavaDurumu.zip"     # klasör kipi: HavaDurumu.exe + _internal
UPDATE_SETUP = "HavaDurumu-Kurulum.exe"  # 1.4.1 ve sonrası bununla güncellenir


def is_onedir():
    """Klasör kipinde mi çalışıyoruz? (exe'nin yanında _internal klasörü var)"""
    return FROZEN and os.path.isdir(os.path.join(APP_DIR, "_internal"))

PROFILE = ""  # "" = ana widget; ek widget'lar --profile AD ile ayrı ayar dosyasıyla çalışır


def profile_paths(name):
    suf = f"_{name}" if name else ""
    return (os.path.join(APP_DIR, f"config{suf}.json"), os.path.join(APP_DIR, f"cache{suf}.json"))


def events_path(name):
    return os.path.join(APP_DIR, f"events_{name}.json" if name else "events.json")


EVENTS_PATH = events_path("")


def set_profile(name):
    global PROFILE, CONFIG_PATH, CACHE_PATH, EVENTS_PATH
    PROFILE = name
    CONFIG_PATH, CACHE_PATH = profile_paths(name)
    EVENTS_PATH = events_path(name)


def list_profiles():
    out = []
    for fn in sorted(os.listdir(APP_DIR)):
        m = re.fullmatch(r"config_([A-Za-z0-9_-]{1,32})\.json", fn)
        if m:
            out.append(m.group(1))
    return out


def launch_cmd(profile="", *extra):
    """Bu uygulamayı (exe ya da .py) yeniden başlatma komutu."""
    if FROZEN:
        cmd = [sys.executable]
    else:
        pyw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
        cmd = [pyw if os.path.exists(pyw) else sys.executable, os.path.abspath(__file__)]
    if profile:
        cmd += ["--profile", profile]
    return cmd + list(extra)


def spawn(cmd):
    # PyInstaller tek-dosya: yeni süreç, bu sürecin geçici klasörünü devralmasın
    env = dict(os.environ, PYINSTALLER_RESET_ENVIRONMENT="1")
    subprocess.Popen(cmd, env=env, close_fds=True,
                     creationflags=0x00000008 | 0x00000200)  # DETACHED | NEW_PROCESS_GROUP


_k32 = ctypes.WinDLL("kernel32", use_last_error=True)
_k32.CreateMutexW.restype = wintypes.HANDLE
_k32.OpenProcess.restype = wintypes.HANDLE
_k32.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR,
                                            ctypes.POINTER(wintypes.DWORD)]
_k32.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
_k32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
_k32.CloseHandle.argtypes = [wintypes.HANDLE]
_mutex = None


def single_instance(name):
    """Aynı klasörde aynı widget ikinci kez açılmasın (adlandırılmış mutex)."""
    global _mutex
    tag = hashlib.md5(APP_DIR.lower().encode("utf-8")).hexdigest()[:8]
    _mutex = _k32.CreateMutexW(None, False, f"Local\\HavaDurumuWidget_{tag}_{name or 'main'}")
    return ctypes.get_last_error() != 183  # ERROR_ALREADY_EXISTS


def wait_pid(pid, timeout_ms=20000):
    h = _k32.OpenProcess(0x00100000, False, int(pid))  # SYNCHRONIZE
    if h:
        _k32.WaitForSingleObject(h, timeout_ms)
        _k32.CloseHandle(h)


if sys.stderr is None or sys.stdout is None:  # pythonw altında
    _f = open(LOG_PATH, "a", encoding="utf-8", buffering=1)
    sys.stderr = sys.stdout = _f


def log(*a):
    print(datetime.now().strftime("%Y-%m-%d %H:%M:%S"), *a, flush=True)


# --------------------------------------------------------------------------- MGM API

API = "https://servis.mgm.gov.tr/web"
HEADERS = {
    "Origin": "https://www.mgm.gov.tr",
    "Referer": "https://www.mgm.gov.tr/",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
    "Accept": "application/json",
}


def http_json(url, headers=None):
    req = urllib.request.Request(url, headers=headers or {"User-Agent": "Mozilla/5.0"})
    for attempt in range(3):  # sunucu bazen bağlantıyı koparıyor: tekrar dene
        try:
            with urllib.request.urlopen(req, timeout=15) as r:
                return json.loads(r.read().decode("utf-8"))
        except (OSError, ValueError):
            if attempt == 2:
                raise
            time.sleep(1.0 + attempt)


def api(path, **params):
    """MGM servisi (servis.mgm.gov.tr)."""
    url = API + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    return http_json(url, HEADERS)


def loc_from_entry(e):
    """MGM 'merkez' kaydından widget'ın ihtiyaç duyduğu alanları çıkarır."""
    mid = e.get("merkezId")
    return {
        "il": e.get("il") or "",
        "ilce": e.get("ilce") or "",
        "merkezId": mid,
        "sondurum": e.get("sondurumIstNo") or mid,
        "gunluk": e.get("gunlukTahminIstNo") or mid,
        "saatlik": e.get("saatlikTahminIstNo") or e.get("sondurumIstNo") or mid,
        "lat": e.get("enlem") or 39.0,
        "lon": e.get("boylam") or 35.0,
    }


DEFAULT_LOC = {"il": "Ankara", "ilce": "Keçiören", "merkezId": 90601, "sondurum": 17130,
               "gunluk": 90601, "saatlik": 17130, "lat": 39.9727, "lon": 32.8637}


# ---- otomatik güncelleme (GitHub Releases)
def parse_version(s):
    return tuple(int(x) for x in re.findall(r"\d+", s or "")[:4])


def latest_release():
    """Depodaki son sürüm: {version, url, size, sha256, page, notes}."""
    j = http_json(f"https://api.github.com/repos/{UPDATE_REPO}/releases/latest",
                  {"User-Agent": "HavaDurumuWidget", "Accept": "application/vnd.github+json"})
    def asset(name):
        a = next((a for a in j.get("assets") or []
                  if (a.get("name") or "").lower() == name.lower()), None) or {}
        digest = a.get("digest") or ""
        return a.get("browser_download_url"), a.get("size"), \
            (digest[7:] if digest.startswith("sha256:") else None)
    url, size, sha = asset(UPDATE_ASSET)
    zurl, zsize, zsha = asset(UPDATE_ZIP)
    surl, ssize, ssha = asset(UPDATE_SETUP)
    return {"version": (j.get("tag_name") or "").lstrip("vV"),
            "url": url, "size": size, "sha256": sha,
            "zip_url": zurl, "zip_size": zsize, "zip_sha256": zsha,
            "setup_url": surl, "setup_size": ssize, "setup_sha256": ssha,
            "page": j.get("html_url"), "notes": (j.get("body") or "").strip()}


def download_update(rel, dest, kind="exe"):
    """Yeni sürümü indirir ve doğrular (kaynak adres, boyut, dosya imzası, varsa SHA-256).
    kind: "exe" (tek dosya), "zip" (klasör kipi) ya da "setup" (kurulum dosyası)."""
    if kind in ("zip", "setup"):
        rel = {k: rel.get(f"{kind}_{k}") for k in ("url", "size", "sha256")}
    magic, what = (b"PK", "zip") if kind == "zip" else (b"MZ", "exe")
    url = rel.get("url") or ""
    if not url.startswith(f"https://github.com/{UPDATE_REPO}/releases/download/"):
        raise ValueError(T("beklenmeyen indirme adresi"))
    h = hashlib.sha256()
    n = 0
    req = urllib.request.Request(url, headers={"User-Agent": "HavaDurumuWidget"})
    with urllib.request.urlopen(req, timeout=60) as r, open(dest, "wb") as f:
        while True:
            chunk = r.read(1 << 16)
            if not chunk:
                break
            if n == 0 and chunk[:2] != magic:
                raise ValueError(T('indirilen dosya bir {} değil').format(what))
            f.write(chunk)
            h.update(chunk)
            n += len(chunk)
    if rel.get("size") and n != rel["size"]:
        raise ValueError(T('dosya boyutu tutmuyor ({} / {})').format(n, rel['size']))
    if rel.get("sha256") and h.hexdigest() != rel["sha256"]:
        raise ValueError(T("SHA-256 doğrulaması başarısız"))
    return dest


def extract_update(zip_path, stage):
    """Güncelleme zip'ini stage klasörüne açar; içinde exe ve _internal olduğunu denetler."""
    shutil.rmtree(stage, ignore_errors=True)
    with zipfile.ZipFile(zip_path) as z:
        for n in z.namelist():
            p = os.path.normpath(n)
            if os.path.isabs(p) or p.startswith(".."):
                raise ValueError(T("güncelleme paketinde geçersiz dosya yolu"))
        z.extractall(stage)
    if not (os.path.isfile(os.path.join(stage, "HavaDurumu.exe"))
            and os.path.isdir(os.path.join(stage, "_internal"))):
        raise ValueError(T("güncelleme paketi eksik"))
    return stage


def other_instances():
    """Aynı exe'den çalışan diğer süreçlerin kimlikleri (ek widget'lar)."""
    me = os.getpid()
    exe = os.path.normcase(os.path.abspath(sys.executable))
    psapi = ctypes.WinDLL("psapi")
    arr = (wintypes.DWORD * 4096)()
    got = wintypes.DWORD()
    if not psapi.EnumProcesses(arr, ctypes.sizeof(arr), ctypes.byref(got)):
        return []
    out = []
    for pid in arr[:got.value // ctypes.sizeof(wintypes.DWORD)]:
        if not pid or pid == me:
            continue
        h = _k32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            continue
        buf, size = ctypes.create_unicode_buffer(1024), wintypes.DWORD(1024)
        if _k32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)) and \
                os.path.normcase(buf.value) == exe:
            out.append(pid)
        _k32.CloseHandle(h)
    return out


def stop_other_instances():
    """Ek widget'ları kapatır (güncelleme sırasında _internal klasörünü kilitli tutmasınlar).
    Ayarlarını her değişiklikte kaydettikleri için veri kaybı olmaz; güncellemeden sonra ana
    widget onları yeniden açar."""
    for pid in other_instances():
        h = _k32.OpenProcess(0x0001 | 0x00100000, False, pid)  # TERMINATE | SYNCHRONIZE
        if h:
            _k32.TerminateProcess(h, 0)
            _k32.WaitForSingleObject(h, 5000)
            _k32.CloseHandle(h)


def apply_update(target):
    """Klasör kipi güncellemesinin ikinci yarısı. Bu süreç _update klasöründeki YENİ exe'dir:
    eski sürüm kapanınca exe ve _internal'ı hedefe taşır, yeni sürümü başlatır. Bir adım
    başarısız olursa eski dosyalar geri konur ve eski sürüm başlatılır."""
    src = os.path.dirname(os.path.abspath(sys.executable))
    exe_t, int_t = os.path.join(target, "HavaDurumu.exe"), os.path.join(target, "_internal")

    def ulog(*a):
        try:
            with open(os.path.join(target, "widget.log"), "a", encoding="utf-8") as f:
                f.write(datetime.now().strftime("%Y-%m-%d %H:%M:%S") + " güncelleme: "
                        + " ".join(str(x) for x in a) + "\n")
        except OSError:
            pass

    moved = []
    try:
        for p in (int_t + ".old", exe_t + ".old"):
            if os.path.isdir(p):
                shutil.rmtree(p, ignore_errors=True)
            elif os.path.exists(p):
                os.remove(p)
        for _ in range(75):  # eski süreçlerin dosyaları bırakması için ~15 sn
            try:
                os.rename(int_t, int_t + ".old")
                moved.append("int")
                break
            except OSError:
                time.sleep(0.2)
        else:
            raise OSError("_internal klasörü hâlâ kullanımda")
        os.rename(exe_t, exe_t + ".old")
        moved.append("exe")
        shutil.copytree(os.path.join(src, "_internal"), int_t)
        shutil.copy2(os.path.join(src, "HavaDurumu.exe"), exe_t)
        ulog("tamamlandı")
    except Exception:
        ulog("başarısız, eski sürüme dönülüyor:", traceback.format_exc())
        if "int" in moved:
            shutil.rmtree(int_t, ignore_errors=True)
            os.rename(int_t + ".old", int_t)
        if "exe" in moved:
            if os.path.exists(exe_t):
                os.remove(exe_t)
            os.rename(exe_t + ".old", exe_t)
    spawn([exe_t])


def swap_exe(new, exe):
    """Çalışan exe'yi yenisiyle değiştirir. Windows çalışan dosyanın adını değiştirmeye
    izin verir: eski dosya .old olur (sonraki açılışta silinir), yenisi yerine geçer."""
    old = exe + ".old"
    if os.path.exists(old):
        os.remove(old)
    os.rename(exe, old)
    try:
        os.rename(new, exe)
    except OSError:
        os.rename(old, exe)  # geri al
        raise


# ---- MGM meteorolojik uyarılar (meteoalarm)
ALERT_COLORS = {"yellow": (T("Sarı"), (250, 204, 50)), "orange": (T("Turuncu"), (255, 150, 50)),
                "red": (T("Kırmızı"), (240, 70, 70))}
ALERT_KINDS = {
    "wind": T("Kuvvetli rüzgar"), "rain": T("Kuvvetli yağış"), "thunderstorm": T("Gök gürültülü sağanak"),
    "snow": T("Kar"), "cold": T("Soğuk hava / don"), "hot": T("Sıcak hava"), "fog": T("Sis"),
    "ice": T("Buzlanma"), "dust": T("Toz taşınımı"), "avalanche": T("Çığ"), "snowmelt": T("Kar erimesi"),
    "flood": T("Sel"), "agricultural": T("Zirai don"), "storm": T("Fırtına"),
}


def fetch_alerts():
    return api("/meteoalarm/today") or []


def alerts_for(loc, alerts, now=None):
    """Bu konumu kapsayan uyarılar, en ciddisi başta."""
    if loc.get("src") == "om":
        return []
    now = now or datetime.now(timezone.utc)
    mid, out = loc.get("merkezId"), []
    for a in alerts or []:
        try:
            if parse_utc(a["end"]) < now:
                continue
        except Exception:
            pass
        towns = a.get("towns") or {}
        for rank, color in enumerate(("red", "orange", "yellow")):
            if mid in (towns.get(color) or []):
                kinds = (a.get("weather") or {}).get(color) or []
                out.append({"rank": rank, "color": color, "no": a.get("alertNo"),
                            "kinds": [ALERT_KINDS.get(k, k) for k in kinds], "codes": kinds,
                            "text": (a.get("text") or {}).get(color) or "",
                            "begin": a.get("begin"), "end": a.get("end")})
                break
    out.sort(key=lambda x: x["rank"])
    return out


# ---- yurt dışı şehirler: Open-Meteo (MGM yalnızca Türkiye'yi kapsar)
WMO = {0: "A", 1: "AB", 2: "PB", 3: "CB", 45: "SIS", 48: "SIS", 51: "HY", 53: "HY", 55: "Y",
       56: "KKY", 57: "KKY", 61: "HY", 63: "Y", 65: "KY", 66: "KKY", 67: "KKY", 71: "HKY",
       73: "K", 75: "YKY", 77: "HKY", 80: "HSY", 81: "SY", 82: "KSY", 85: "HKY", 86: "YKY",
       95: "GSY", 96: "KGSY", 99: "KGSY"}


def om_search(name):
    url = "https://geocoding-api.open-meteo.com/v1/search?" + urllib.parse.urlencode(
        {"name": name, "count": 10, "language": LANG})
    out = []
    for r in (http_json(url).get("results") or []):
        lat, lon = r.get("latitude"), r.get("longitude")
        if lat is None or lon is None:
            continue
        out.append({"src": "om", "il": r.get("country") or "", "ilce": r.get("name") or "",
                    "admin": r.get("admin1") or "", "merkezId": f"om_{lat:.3f}_{lon:.3f}",
                    "lat": lat, "lon": lon})
    return out


def _iso(ts):
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def fetch_om(loc):
    """Open-Meteo yanıtını MGM veri biçimine çevirir; çizim kodu ikisini aynı görür."""
    url = "https://api.open-meteo.com/v1/forecast?" + urllib.parse.urlencode({
        "latitude": loc["lat"], "longitude": loc["lon"],
        "current": "temperature_2m,relative_humidity_2m,apparent_temperature,weather_code,"
                   "pressure_msl,wind_speed_10m,wind_direction_10m",
        "hourly": "temperature_2m,weather_code,visibility",
        "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_sum",
        "timezone": "auto", "forecast_days": 6, "timeformat": "unixtime"})
    j = http_json(url)
    c, h, dl = j.get("current") or {}, j.get("hourly") or {}, j.get("daily") or {}
    off = j.get("utc_offset_seconds") or 0
    now_ts = c.get("time") or time.time()
    ht = h.get("time") or []
    start = next((i for i, t in enumerate(ht) if t >= now_ts - 1800), len(ht))
    hourly = [{"tarih": _iso(ht[i]), "hadise": WMO.get(h["weather_code"][i], "CB"),
               "sicaklik": h["temperature_2m"][i]}
              for i in range(start, min(len(ht), start + 24), 3)
              if h["temperature_2m"][i] is not None]
    vis = (h.get("visibility") or [None] * len(ht))
    daily = {}
    for i, t in enumerate(dl.get("time") or []):
        daily[f"tarihGun{i}"] = _iso(t + off)[:10] + "T00:00:00.000Z"
        daily[f"enDusukGun{i}"] = dl["temperature_2m_min"][i]
        daily[f"enYuksekGun{i}"] = dl["temperature_2m_max"][i]
        daily[f"hadiseGun{i}"] = WMO.get(dl["weather_code"][i], "CB")
    rain = (dl.get("precipitation_sum") or [None])[0]
    cur = {
        "sicaklik": c.get("temperature_2m"), "hissedilenSicaklik": c.get("apparent_temperature"),
        "nem": c.get("relative_humidity_2m"), "ruzgarHiz": c.get("wind_speed_10m"),
        "ruzgarYon": c.get("wind_direction_10m"),
        "denizeIndirgenmisBasinc": c.get("pressure_msl"),
        "hadiseKodu": WMO.get(c.get("weather_code"), "CB"), "veriZamani": _iso(now_ts),
        "yagis24Saat": rain if rain is not None else -9999,
        "gorus": vis[start] if start < len(vis) and vis[start] is not None else -9999,
        "denizSicaklik": -9999, "karYukseklik": -9999, "kapalilik": -9999,
    }
    return {"merkezId": loc["merkezId"], "src": "om", "utc_offset": off, "cur": cur, "daily": daily,
            "hourly": hourly, "fetched": datetime.now(timezone.utc).isoformat()}


def _fetch_main(loc):
    if loc.get("src") == "om":
        return fetch_om(loc)
    cur = api("/sondurumlar", merkezid=loc["merkezId"])
    if not cur:
        cur = api("/sondurumlar", istno=loc["sondurum"])
    daily = api("/tahminler/gunluk", istno=loc["gunluk"])
    try:
        hourly = api("/tahminler/saatlik", istno=loc["saatlik"])
        hourly = (hourly[0].get("tahmin") or []) if hourly else []
        if not hourly:  # ilçenin saatlik istasyonu yoksa MGM'nin il merkezi tahmini
            ist = next((e["saatlikTahminIstNo"] for e in api("/merkezler", il=loc["il"]) or []
                        if e.get("saatlikTahminIstNo")), None)
            hourly = api("/tahminler/saatlik", istno=ist) if ist else []
            hourly = (hourly[0].get("tahmin") or []) if hourly else []
    except Exception as ex:
        log("saatlik tahmin alınamadı:", ex)
        hourly = []
    return {
        "merkezId": loc["merkezId"],
        "cur": cur[0] if cur else None,
        "daily": daily[0] if daily else None,
        "hourly": hourly,
        "fetched": datetime.now(timezone.utc).isoformat(),
    }


def fetch_weather(loc):
    data = _fetch_main(loc)
    # Türkiye'deki konumlar yalnızca MGM verisiyle gösterilir; Open-Meteo yurt dışı içindir
    data["extra"] = fetch_extras(loc) if loc.get("src") == "om" else mgm_extras(data)
    return fix_code(data)


def mgm_extras(data):
    """MGM saatlik (3 saatte bir) ve 5 günlük tahminini, Open-Meteo ek verisiyle aynı biçime
    (temps12, daily16) çevirir; saatlik şerit, grafik ve haftalık düzen MGM verisiyle çalışır."""
    out, now = {}, time.time()
    t12 = []
    for e in data.get("hourly") or []:
        try:
            ts = parse_utc(e["tarih"]).timestamp()
        except Exception:
            continue
        if ts >= now - 5400 and ok(e.get("sicaklik")):
            t12.append([ts, e["sicaklik"], e.get("hadise") or "CB", None])
    if t12:
        out["temps12"] = t12
    dd = data.get("daily") or {}
    d16 = {}
    for i in range(6):
        ts, lo, hi = dd.get(f"tarihGun{i}"), dd.get(f"enDusukGun{i}"), dd.get(f"enYuksekGun{i}")
        if ts and ok(lo) and ok(hi):
            d16[ts[:10]] = {"d": ts[:10], "code": dd.get(f"hadiseGun{i}") or "CB", "lo": lo, "hi": hi,
                            "prob": None}
    if d16:
        out["daily16"] = [d16[k] for k in sorted(d16)]
    return out


def fix_code(data):
    """MGM bazen hava durumu kodunu boş / -9999 gönderir; o zaman kartta "-9999" yazmasın:
    MGM saatlik tahmininin ilk kodu (yoksa "Çok Bulutlu") kullanılır."""
    cur = (data or {}).get("cur")
    if cur and cur.get("hadiseKodu") not in HADISE:
        h = [r.get("hadise") for r in data.get("hourly") or []]
        cur["hadiseKodu"] = h[0] if h and h[0] in HADISE else "CB"
    return data


POLLEN = {"alder": T("Kızılağaç"), "birch": T("Huş"), "grass": T("Çim"), "mugwort": T("Pelin"),
          "olive": T("Zeytin"), "ragweed": T("Ambrosia")}


def fetch_extras(loc):
    """Open-Meteo'dan ek bilgiler (yalnızca yurt dışı konumlar): dünkü sıcaklık farkı, yağış
    olasılığı, UV, hava kalitesi, polen. Hata olursa boş döner, ana veriyi etkilemez."""
    out = {}
    lat, lon = loc["lat"], loc["lon"]
    now = time.time()
    try:
        j = http_json("https://api.open-meteo.com/v1/forecast?" + urllib.parse.urlencode({
            "latitude": lat, "longitude": lon,
            "hourly": "temperature_2m,precipitation_probability,uv_index,weather_code",
            "minutely_15": "precipitation", "forecast_minutely_15": 16, "past_minutely_15": 1,
            "daily": "weather_code,temperature_2m_max,temperature_2m_min,"
                     "precipitation_probability_max,uv_index_max",
            "past_days": 1, "forecast_days": 16, "timezone": "auto", "timeformat": "unixtime"}))
        h, dl = j.get("hourly") or {}, j.get("daily") or {}
        ts = h.get("time") or []
        i = max((n for n, x in enumerate(ts) if x <= now), default=None)
        if i is not None:
            temps = h.get("temperature_2m") or []
            j24 = next((n for n, x in enumerate(ts) if x == ts[i] - 86400), None)
            if j24 is not None and temps[i] is not None and temps[j24] is not None:
                out["yesterday_diff"] = temps[i] - temps[j24]
            uv = h.get("uv_index") or []
            if i < len(uv) and uv[i] is not None:
                out["uv_now"] = uv[i]
            nxt = [p for p in (h.get("precipitation_probability") or [])[i:i + 12] if p is not None]
            if nxt:
                out["rain_prob_12h"] = max(nxt)
            # önümüzdeki 12 saat: [zaman, sıcaklık, hava kodu, yağış olasılığı] (minik grafik, sayfalar)
            codes_h = h.get("weather_code") or []
            probs_h = h.get("precipitation_probability") or []
            t12 = []
            for n in range(i, min(len(ts), i + 13)):
                if n < len(temps) and temps[n] is not None:
                    t12.append([ts[n], temps[n], WMO.get(codes_h[n] if n < len(codes_h) else None, "CB"),
                                probs_h[n] if n < len(probs_h) else None])
            if t12:
                out["temps12"] = t12
        m15 = j.get("minutely_15") or {}
        r15 = [[a, b] for a, b in zip(m15.get("time") or [], m15.get("precipitation") or [])]
        if r15:
            out["rain15"] = r15  # 15 dakikalık yağış (mm), önümüzdeki 4 saat
        dts = dl.get("time") or []
        ti = max((n for n, x in enumerate(dts) if x <= now), default=None)
        if ti is not None:
            pm = (dl.get("precipitation_probability_max") or [None] * len(dts))[ti]
            um = (dl.get("uv_index_max") or [None] * len(dts))[ti]
            if pm is not None:
                out["rain_prob"] = pm
            if um is not None:
                out["uv_max"] = um
            # bugünden başlayan 16 günlük liste (haftalık düzen ve eğilim için)
            off = j.get("utc_offset_seconds") or 0
            codes = dl.get("weather_code") or []
            his, los = dl.get("temperature_2m_max") or [], dl.get("temperature_2m_min") or []
            probs = dl.get("precipitation_probability_max") or []
            d16 = []
            for n in range(ti, len(dts)):
                if n >= len(his) or n >= len(los) or his[n] is None or los[n] is None:
                    continue
                d16.append({"d": _iso(dts[n] + off)[:10],
                            "code": WMO.get(codes[n] if n < len(codes) else None, "CB"),
                            "lo": los[n], "hi": his[n],
                            "prob": probs[n] if n < len(probs) else None})
            if d16:
                out["daily16"] = d16
    except Exception as ex:
        log("ek tahmin alınamadı:", repr(ex))
    try:
        aq = http_json("https://air-quality-api.open-meteo.com/v1/air-quality?" + urllib.parse.urlencode({
            "latitude": lat, "longitude": lon, "timezone": "auto",
            "current": "european_aqi,pm2_5,pm10,ozone," + ",".join(p + "_pollen" for p in POLLEN)}))
        c = aq.get("current") or {}
        for key in ("european_aqi", "pm2_5", "pm10", "ozone"):
            if c.get(key) is not None:
                out[key] = c[key]
        pol = {p: c[p + "_pollen"] for p in POLLEN if c.get(p + "_pollen") is not None}
        if pol:
            out["pollen"] = pol
    except Exception as ex:
        log("hava kalitesi alınamadı:", repr(ex))
    return out


def aqi_level(v):
    """Avrupa hava kalitesi indeksi -> (ad, renk)."""
    for lim, name, col in ((20, T("İyi"), (80, 200, 120)), (40, T("Makul"), (160, 210, 80)),
                           (60, T("Orta"), (240, 200, 60)), (80, T("Kötü"), (245, 140, 60)),
                           (100, T("Çok kötü"), (225, 70, 70))):
        if v <= lim:
            return name, col
    return T("Aşırı kötü"), (160, 70, 170)


def uv_level(v):
    for lim, name in ((2, T("Düşük")), (5, T("Orta")), (7, T("Yüksek")), (10, T("Çok yüksek"))):
        if v <= lim:
            return name
    return T("Aşırı")


def pollen_summary(pol):
    """En yoğun polen türü -> (tür, seviye) ya da None."""
    if not pol:
        return None
    kind, v = max(pol.items(), key=lambda kv: kv[1])
    if v < 1:
        return None
    level = T("düşük") if v < 10 else T("orta") if v < 50 else T("yüksek") if v < 200 else T("çok yüksek")
    return POLLEN[kind], level


def yesterday_text(diff):
    d = diff * 9 / 5 if UNITS["temp"] == "F" else diff
    r = round(abs(d))
    if r == 0:
        return T("dünle aynı")
    return T('dünden {}° daha {}').format(r, T('sıcak') if d > 0 else T('soğuk'))


def fmt_minutes(m):
    m = int(m)
    if m < 60:
        return T('{} dk').format(m)
    h, mm = divmod(m, 60)
    return T('{} sa {} dk').format(h, mm) if mm else T('{} sa').format(h)


def rain_eta(extra, code, now=None):
    """15 dakikalık yağış tahmininden kısa geri sayım (önümüzdeki 4 saat)."""
    now = now or time.time()
    steps = [(a, b or 0.0) for a, b in (extra.get("rain15") or []) if a + 900 > now]
    if not steps:
        return None
    TH = 0.1  # mm / 15 dk: bunun altı "yağış yok" sayılır
    # metinler mini kartın dar ikinci satırına sığacak kadar kısa
    if steps[0][1] >= TH:
        for a, b in steps[1:]:
            if b < TH:
                return T('☂ {} daha').format(fmt_minutes(max(15, round((a - now) / 300) * 5)))
        return T("☂ 4 sa+ daha")
    if code in WET:  # MGM yağış ölçüyor ama model kuru diyor
        return T("Yağış sürüyor")
    for a, b in steps[1:]:
        if b >= TH:
            return T('☂ {} sonra').format(fmt_minutes(max(5, round((a - now) / 300) * 5)))
    return T("Yağış yok")


def sun_eta(lat, lon, now=None, tz=None):
    """Bir sonraki gün doğumu / batımı: 2 saatten uzaksa saati, yakınsa kalan süre."""
    now = now or datetime.now(timezone.utc)
    ev = []
    for dd in (0, 1):
        day = now + timedelta(days=dd)
        s = _sun_hours(lat, lon, day)
        if isinstance(s, str):
            continue
        base = day.replace(hour=0, minute=0, second=0, microsecond=0)
        ev += [(base + timedelta(hours=s[0]), T("Doğuş")), (base + timedelta(hours=s[1]), T("Batış"))]
    nxt = min((e for e in ev if e[0] > now), default=None, key=lambda e: e[0])
    if not nxt:
        return None
    mins = max(1, round((nxt[0] - now).total_seconds() / 60))
    if mins > 120:  # uzaksa saati, yakınsa geri sayımı göster
        return f"{nxt[1]} {nxt[0].astimezone(tz):%H:%M}"
    return f"{nxt[1]} {fmt_minutes(mins)}"


# ---- ay evresi
MOON_STATE = {"q": 14}  # 0..27 (0 yeni ay, 14 dolunay); çizimden önce güncellenir
MOON_NAMES = [T("Yeni ay"), T("Büyüyen hilal"), T("İlk dördün"), T("Büyüyen şişkin ay"), T("Dolunay"),
              T("Küçülen şişkin ay"), T("Son dördün"), T("Küçülen hilal")]
MOON_SHORT = [T("Yeni ay"), T("Hilal"), T("İlk dördün"), T("Şişkin ay"), T("Dolunay"), T("Şişkin ay"),
              T("Son dördün"), T("Hilal")]


def moon_phase(dt=None):
    """0 = yeni ay, 0.5 = dolunay (ortalama sinodik ay ile; ±1 gün)."""
    dt = dt or datetime.now(timezone.utc)
    ref = datetime(2000, 1, 6, 18, 14, tzinfo=timezone.utc)
    return ((dt - ref).total_seconds() / 86400 / 29.530588853) % 1.0


def moon_info(dt=None):
    p = moon_phase(dt)
    i = int(p * 8 + 0.5) % 8
    return MOON_NAMES[i], MOON_SHORT[i], (1 - math.cos(2 * math.pi * p)) / 2 * 100


GUN_KISA = [T("Pzt"), T("Sal"), T("Çar"), T("Per"), T("Cum"), T("Cmt"), T("Paz")]
AY_KISA = [T("Oca"), T("Şub"), T("Mar"), T("Nis"), T("May"), T("Haz"), T("Tem"), T("Ağu"), T("Eyl"), T("Eki"), T("Kas"), T("Ara")]


def clock_text(tz=None):
    n = datetime.now(tz)
    return f"{GUN_KISA[n.weekday()]} {n.day} {AY_KISA[n.month - 1]} · {n:%H:%M}"


def parse_hhmm(s):
    m = re.fullmatch(r"\s*(\d{1,2})[:.](\d{2})\s*", s or "")
    if not m or int(m.group(1)) > 23 or int(m.group(2)) > 59:
        return None
    return int(m.group(1)), int(m.group(2))


HADISE = {
    "A": T("Açık"), "AB": T("Az Bulutlu"), "PB": T("Parçalı Bulutlu"), "CB": T("Çok Bulutlu"),
    "HY": T("Hafif Yağmurlu"), "Y": T("Yağmurlu"), "KY": T("Kuvvetli Yağmurlu"),
    "KKY": T("Karla Karışık Yağmurlu"), "HKY": T("Hafif Kar Yağışlı"), "K": T("Kar Yağışlı"),
    "YKY": T("Yoğun Kar Yağışlı"), "HSY": T("Hafif Sağanak Yağışlı"), "SY": T("Sağanak Yağışlı"),
    "KSY": T("Kuvvetli Sağanak Yağışlı"), "MSY": T("Mevzi Sağanak Yağışlı"), "DY": T("Dolu"),
    "GSY": T("Gökgürültülü Sağanak Yağışlı"), "KGSY": T("Kuvvetli Gökgürültülü Sağanak Yağışlı"),
    "SIS": T("Sisli"), "PUS": T("Puslu"), "DMN": T("Dumanlı"), "KF": T("Toz veya Kum Fırtınası"),
    "R": T("Rüzgarlı"), "GKR": T("Güneyli Kuvvetli Rüzgar"), "KKR": T("Kuzeyli Kuvvetli Rüzgar"),
    "SCK": T("Sıcak"), "SGK": T("Soğuk"), "HHY": T("Yağışlı"),
}
RAIN = {"HY": 2, "HSY": 2, "HHY": 2, "Y": 3, "SY": 3, "MSY": 3, "KY": 4, "KSY": 4}
SNOW = {"HKY": 2, "K": 3, "YKY": 4}
GUNLER = [T("Pazartesi"), T("Salı"), T("Çarşamba"), T("Perşembe"), T("Cuma"), T("Cumartesi"), T("Pazar")]


def ok(v):
    return v is not None and v != -9999


def parse_utc(s):
    return datetime.strptime(s[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)


def wind_dir(deg):
    if not ok(deg):
        return ""
    return T("K KD D GD G GB B KB").split()[int((deg % 360) / 45 + 0.5) % 8]


def _sun_hours(lat, lon, t_utc):
    """(doğuş, batış) UTC saat olarak; kutup gecesi/gündüzünde 'night' / 'day'."""
    n = t_utc.timetuple().tm_yday
    b = 2 * math.pi * (n - 81) / 364
    eot = 9.87 * math.sin(2 * b) - 7.53 * math.cos(b) - 1.5 * math.sin(b)  # dakika
    decl = math.radians(23.44) * math.sin(2 * math.pi * (n - 81) / 365)
    x = -math.tan(math.radians(lat)) * math.tan(decl)
    if x >= 1:
        return "night"
    if x <= -1:
        return "day"
    h0 = math.degrees(math.acos(x)) + 0.83
    noon = 12 - lon / 15 - eot / 60
    return (noon - h0 / 15) % 24, (noon + h0 / 15) % 24


def is_night(lat, lon, t_utc):
    s = _sun_hours(lat, lon, t_utc)
    if isinstance(s, str):
        return s == "night"
    rise, sett = s
    hour = t_utc.hour + t_utc.minute / 60
    if rise < sett:
        return not (rise <= hour < sett)
    return sett <= hour < rise


def day_factor(lat, lon, t_utc):
    """0 = gece, 1 = gündüz. Gün doğumu ve batımının çevresinde 80 dakika boyunca
    (40 dk önce - 40 dk sonra) 0 ile 1 arasında yumuşak geçiş yapar."""
    s = _sun_hours(lat, lon, t_utc)
    if isinstance(s, str):
        return 0.0 if s == "night" else 1.0
    rise, sett = s
    hour = t_utc.hour + t_utc.minute / 60 + t_utc.second / 3600

    def diff(a, b):  # a - b saat farkı, [-12, 12) aralığına sarılmış
        return (a - b + 12) % 24 - 12
    span = 80 / 60
    up = diff(hour, rise) / span + 0.5
    down = diff(sett, hour) / span + 0.5
    return max(0.0, min(1.0, min(up, down)))


def sun_times(lat, lon, t_utc, tz=None):
    """Bugünün gün doğumu ve batımı, sistem saat diliminde 'SS:DD' (yaklaşık ±3 dk)."""
    s = _sun_hours(lat, lon, t_utc)
    if isinstance(s, str):
        return None
    base = t_utc.replace(hour=0, minute=0, second=0, microsecond=0)
    return tuple((base + timedelta(hours=x)).astimezone(tz).strftime("%H:%M") for x in s)


# ---- birimler (ayarlardan gelir; MGM verisi °C ve km/saat)
UNITS = {"temp": "C", "wind": "kmh"}


def tconv(v):
    return v * 9 / 5 + 32 if UNITS["temp"] == "F" else v


def tint(v):
    """Tam sayı gösterilen sıcaklıklar (tahminler) için."""
    return round(tconv(v))


def fmt_wind(v):
    if UNITS["wind"] == "ms":
        return T('{:.1f} m/sn').format(v / 3.6)
    return T('{} km/sa').format(round(v))


TR_ALPHA = "abcçdefgğhıijklmnoöprsştuüvyz"


def tr_lower(s):
    return s.replace("I", "ı").replace("İ", "i").lower()


def tr_key(s):
    return [TR_ALPHA.index(c) if c in TR_ALPHA else 100 + ord(c) for c in tr_lower(s)]


# --------------------------------------------------------------------------- çizim

def _font_file(*names):
    for n in names:
        p = os.path.join(FONT_DIR, n)
        if os.path.exists(p):
            return p
    return "arial.ttf"


FONT_FILES = {
    "reg": _font_file("segoeui.ttf", "arial.ttf"),
    "sb": _font_file("seguisb.ttf", "segoeuib.ttf", "arialbd.ttf"),
    "light": _font_file("segoeuil.ttf", "segoeuisl.ttf", "segoeui.ttf"),
}
_font_cache = {}


def F(kind, size):
    key = (kind, size)
    if key not in _font_cache:
        f = ImageFont.truetype(FONT_FILES[kind], size)
        f.kind = kind  # yedek font seçimi için ağırlık bilgisi
        _font_cache[key] = f
    return _font_cache[key]


# ---- yedek fontlar: Segoe UI'da olmayan karakterler (Japonca, Çince, Korece, Hintçe,
# Tayca, semboller…) için harf harf uygun Windows fontu seçilir.
# ağırlık -> {yazı sistemi: font dosyası}
FALLBACK_FILES = {
    "reg": {"ja": "YuGothM.ttc", "zh": "msyh.ttc", "ko": "malgun.ttf"},
    "sb": {"ja": "YuGothB.ttc", "zh": "msyhbd.ttc", "ko": "malgunbd.ttf"},
    "light": {"ja": "YuGothL.ttc", "zh": "msyhl.ttc", "ko": "malgunsl.ttf"},
}
FALLBACK_COMMON = ["Nirmala.ttc", "leelawui.ttf", "seguisym.ttf", "simsun.ttc", "arialuni.ttf"]
_cmap_cache, _fb_font_cache, _fb_choice = {}, {}, {}


def _cmap(path):
    """Fontun içerdiği karakter kodları (fontTools ile, bir kez okunur)."""
    if path not in _cmap_cache:
        cps = frozenset()
        if path and os.path.exists(path):
            try:
                from fontTools.ttLib import TTFont
                kw = {"fontNumber": 0} if path.lower().endswith(".ttc") else {}
                tt = TTFont(path, lazy=True, **kw)
                cps = frozenset(tt.getBestCmap() or {})
                tt.close()
            except Exception as ex:
                log("font okunamadı:", path, ex)
        _cmap_cache[path] = cps
    return _cmap_cache[path]


def _script_hint(text):
    """Han karakterleri hem Japonca hem Çince fontta var; metne göre öncelik seç."""
    for c in text:
        o = ord(c)
        if 0x3040 <= o <= 0x30FF or 0x31F0 <= o <= 0x31FF or 0xFF66 <= o <= 0xFF9F:
            return ["ja", "zh", "ko"]  # kana -> Japonca
        if 0xAC00 <= o <= 0xD7AF or 0x1100 <= o <= 0x11FF or 0x3130 <= o <= 0x318F:
            return ["ko", "zh", "ja"]  # hangul -> Korece
    return ["zh", "ja", "ko"]


def _fallback_font(ch, kind, size, order):
    key = (ch, kind, tuple(order))
    if key not in _fb_choice:
        files = FALLBACK_FILES.get(kind, FALLBACK_FILES["reg"])
        cands = [files[s] for s in order] + FALLBACK_COMMON
        _fb_choice[key] = next((os.path.join(FONT_DIR, n) for n in cands
                                if ord(ch) in _cmap(os.path.join(FONT_DIR, n))), None)
    path = _fb_choice[key]
    if not path:
        return None
    fk = (path, size)
    if fk not in _fb_font_cache:
        _fb_font_cache[fk] = ImageFont.truetype(path, size, index=0)
    return _fb_font_cache[fk]


def text_runs(text, font):
    """Metni, her parçası tek bir fontla çizilebilecek parçalara böler."""
    kind = getattr(font, "kind", None)
    if not kind or not text:
        return [(text, font)]
    prim = _cmap(FONT_FILES[kind])
    if all(ord(c) in prim or c.isspace() for c in text):
        return [(text, font)]
    order = _script_hint(text)
    runs, buf, cur = [], "", font
    for ch in text:
        if ch.isspace() or unicodedata.combining(ch):
            f = cur
        elif ord(ch) in prim:
            f = font
        else:
            f = _fallback_font(ch, kind, font.size, order) or font
        if f is not cur and buf:
            runs.append((buf, cur))
            buf = ""
        cur = f
        buf += ch
    if buf:
        runs.append((buf, cur))
    return runs


def text_len(text, font=None):
    return sum(f.getlength(s) for s, f in text_runs(text, font))


def dtext(d, xy, text, font=None, fill=None, anchor="la"):
    """ImageDraw.text yerine: gerektiğinde yedek fontlarla parça parça çizer."""
    runs = text_runs(text, font)
    if len(runs) == 1:  # tek parça: ana font ya da tümüyle yedek font
        d.text(xy, text, font=runs[0][1], fill=fill, anchor=anchor)
        return
    x, y = xy
    total = sum(f.getlength(s) for s, f in runs)
    if anchor[0] == "m":
        x -= total / 2
    elif anchor[0] == "r":
        x -= total
    for s, f in runs:
        d.text((x, y), s, font=f, fill=fill, anchor="l" + anchor[1])
        x += f.getlength(s)


SUN = (255, 200, 61, 255)
SUN_HOT = (255, 140, 50, 255)
MOON_D = (236, 232, 210, 255)
CLOUD_D = (238, 243, 250, 255)
CLOUD_GREY_D = (176, 188, 204, 255)
CLOUD_DARK_D = (128, 136, 156, 255)
RAIN_C = (92, 176, 255, 255)
SNOW_D = (255, 255, 255, 255)
FOG_D = (214, 220, 228, 255)
_icon_cache = {}


ANIM_CYCLE = 2.25  # sn: bir ikon animasyonu döngüsü
ANIM_FRAMES = 135  # döngüdeki kare sayısı = ANIM_CYCLE x ekranın Hz değeri (set_hz günceller)


def icon_animated(code, night):
    """Bu ikonun hareketli bir parçası var mı? (yağmur, kar, güneş ışınları)"""
    c = code or ""
    if c in RAIN or c in SNOW or c == "KKY":
        return True
    return not night and c in ("A", "SCK", "AB", "PB", "PUS")


def make_icon(code, size, night, scheme="dark", frame=None):
    """scheme: 'dark' = koyu zemin için (açık bulutlar), 'light' = açık zemin için.
    frame: None = sabit ikon; 0..ANIM_FRAMES-1 = animasyon karesi."""
    if frame is not None and not icon_animated(code, night):
        frame = None
    key = (code, size, night, scheme, frame, MOON_STATE["q"] if night else 0)
    if key in _icon_cache:
        return _icon_cache[key]
    ph = (frame or 0) / ANIM_FRAMES  # 0..1 döngü fazı

    def fall(i, speed=2):
        """i. damlanın/tanenin (aşağı kayma, opaklık) değeri."""
        if frame is None:
            return 0.0, 255
        t = (ph * speed + i * 0.37) % 1.0
        a = min(1.0, t / 0.15, (1.0 - t) / 0.35)
        return t * 10 - 2, int(255 * max(0.0, a))
    if scheme == "light":
        CLOUD, CLOUD_GREY, CLOUD_DARK = (198, 209, 224, 255), (152, 165, 184, 255), (108, 116, 138, 255)
        SNOW_C, FOG_C, MOON = (140, 182, 232, 255), (132, 142, 160, 255), (214, 190, 110, 255)
    else:
        CLOUD, CLOUD_GREY, CLOUD_DARK = CLOUD_D, CLOUD_GREY_D, CLOUD_DARK_D
        SNOW_C, FOG_C, MOON = SNOW_D, FOG_D, MOON_D
    N = size * 4
    im = Image.new("RGBA", (N, N), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    k = N / 100

    def circ(x, y, r, fill):
        d.ellipse([(x - r) * k, (y - r) * k, (x + r) * k, (y + r) * k], fill=fill)

    def ln(x1, y1, x2, y2, w, fill):
        d.line([x1 * k, y1 * k, x2 * k, y2 * k], fill=fill, width=max(1, int(w * k)))
        circ(x1, y1, w / 2, fill)
        circ(x2, y2, w / 2, fill)

    def sun(x, y, r, col=SUN):
        for i in range(8):
            a = i * math.pi / 4 + ph * math.pi / 4  # 8 ışın: 45° dönünce döngü kusursuz
            ln(x + math.cos(a) * (r + 6), y + math.sin(a) * (r + 6),
               x + math.cos(a) * (r + 12), y + math.sin(a) * (r + 12), 4.5, col)
        circ(x, y, r, col)

    def moon(x, y, r):
        """Ayın gerçek evresi: aydınlık kısım parlak, karanlık kısım silik."""
        p = MOON_STATE["q"] / 28
        circ(x, y, r, MOON[:3] + (45,))
        m = Image.new("L", im.size, 0)
        md = ImageDraw.Draw(m)
        box = [(x - r) * k, (y - r) * k, (x + r) * k, (y + r) * k]
        md.pieslice(box, 270, 90, fill=255) if p < 0.5 else md.pieslice(box, 90, 270, fill=255)
        c_ = math.cos(2 * math.pi * p)  # 1: yeni ay, -1: dolunay
        # hilal küçük boyutta kaybolmasın: aydınlık kısım en az yarıçapın ~%45'i kadar kalın
        ew = (min(c_, 0.55) if c_ > 0 else -c_) * r
        md.ellipse([(x - ew) * k, (y - r) * k, (x + ew) * k, (y + r) * k],
                   fill=0 if c_ > 0 else 255)
        im.paste(Image.new("RGBA", im.size, MOON), (0, 0), m)

    def sky(x, y, r):
        moon(x, y, r * 1.15) if night else sun(x, y, r)

    def cloud(ox, oy, sc, fill):
        for x, y, r in ((-20, 4, 14), (2, -6, 20), (22, 6, 12)):
            circ(ox + x * sc, oy + y * sc, r * sc, fill)
        d.rounded_rectangle([(ox - 34 * sc) * k, (oy + 2 * sc) * k,
                             (ox + 34 * sc) * k, (oy + 18 * sc) * k],
                            radius=8 * sc * k, fill=fill)

    def drops(n, col=RAIN_C, y=74):
        xs = {1: [50], 2: [40, 60], 3: [32, 50, 68], 4: [28, 42, 56, 70]}[n]
        for i, x in enumerate(xs):
            dy, al = fall(i)
            yy = y + (6 if i % 2 else 0) + dy
            ln(x - dy * 0.33, yy, x - 4 - dy * 0.33, yy + 12, 5, col[:3] + (al,))

    def flake(x, y, r, col, rot=0.0):
        """Altı kollu kar tanesi: 3 çizgi + her kolun ucunda küçük V."""
        for j in range(6):
            a = rot + j * math.pi / 3
            ex, ey = x + math.cos(a) * r, y + math.sin(a) * r
            ln(x, y, ex, ey, 2.6, col)
            mx, my = x + math.cos(a) * r * 0.6, y + math.sin(a) * r * 0.6
            for s in (-0.7, 0.7):
                ln(mx, my, mx + math.cos(a + s) * r * 0.32, my + math.sin(a + s) * r * 0.32, 2.2, col)

    def flakes(n, y=78):
        xs = {1: [50], 2: [38, 62], 3: [30, 50, 70], 4: [27, 43, 59, 75]}[n]
        for i, x in enumerate(xs):
            dy, al = fall(i, speed=1)
            sway = math.sin((ph + i * 0.3) * 2 * math.pi) * 2 if frame is not None else 0
            flake(x + sway, y + (8 if i % 2 else 0) + dy, 7 if n < 4 else 6, SNOW_C[:3] + (al,),
                  ph * math.pi / 3 + i)

    c = code or "CB"
    if c in ("A", "SCK"):
        if night:
            moon(50, 50, 28)
        else:
            sun(50, 50, 20, SUN_HOT if c == "SCK" else SUN)
    elif c == "AB":
        sky(40, 38, 17)
        cloud(60, 64, 0.72, CLOUD)
    elif c == "PB":
        sky(36, 34, 17)
        cloud(56, 62, 0.95, CLOUD)
    elif c == "CB":
        cloud(38, 42, 0.72, CLOUD_GREY)
        cloud(54, 58, 1.0, CLOUD)
    elif c in ("HSY", "SY", "MSY", "KSY"):  # sağanak: koyu, alçak bulut + rüzgârla eğik sicim yağmur
        cloud(36, 40, 0.8, CLOUD_DARK)
        cloud(54, 46, 1.05, CLOUD_GREY)
        for i in range(RAIN[c] + 2):
            dy, al = fall(i, speed=3)
            x, y = 26 + i * 52 / (RAIN[c] + 1), 66 + (5 if i % 2 else 0) + dy
            ln(x - dy * 0.6, y, x - 8 - dy * 0.6, y + 18, 4, RAIN_C[:3] + (al,))
    elif c in RAIN:
        cloud(52, 46, 1.05, CLOUD_GREY if RAIN[c] >= 3 else CLOUD)
        drops(RAIN[c])
    elif c == "KKY":  # karla karışık: mavi damla ve beyaz kar tanesi sırayla
        cloud(50, 44, 1.05, CLOUD)
        for i, x in enumerate((28, 44, 60, 76)):
            dy, al = fall(i, speed=1.5)
            y = 72 + (8 if i % 2 else 0) + dy
            if i % 2:
                flake(x, y + 2, 7, SNOW_C[:3] + (al,), ph * math.pi / 3 + i)
            else:
                ln(x - dy * 0.33, y - 4, x - 4 - dy * 0.33, y + 9, 5, RAIN_C[:3] + (al,))
    elif c in SNOW:
        cloud(50, 46, 1.05, CLOUD)
        flakes(SNOW[c])
    elif c == "DY":
        cloud(50, 46, 1.05, CLOUD_GREY)
        for x, y in ((34, 80), (50, 88), (66, 80)):
            circ(x, y, 5.5, (220, 238, 255, 255))
    elif c in ("GSY", "KGSY"):
        cloud(50, 44, 1.05, CLOUD_DARK)
        d.polygon([(p[0] * k, p[1] * k) for p in
                   ((54, 58), (40, 80), (50, 80), (44, 98), (64, 72), (53, 72), (60, 58))],
                  fill=SUN)
        ln(30, 72, 26, 84, 5, RAIN_C)
        ln(72, 72, 68, 84, 5, RAIN_C)
    elif c in ("SIS", "PUS", "DMN", "KF"):
        col = (214, 180, 130, 255) if c == "KF" else FOG_C
        if c == "PUS":
            sky(50, 38, 16)
        elif c in ("SIS", "DMN"):
            cloud(50, 38, 0.9, CLOUD)
        for y, x1, x2 in ((66, 20, 80), (77, 28, 72), (88, 20, 80)):
            ln(x1, y, x2, y, 5, col)
    elif c in ("R", "GKR", "KKR"):
        for y, x2 in ((34, 66), (50, 80), (66, 60)):
            ln(18, y, x2, y, 6, FOG_C)
            d.arc([(x2 - 8) * k, (y - 16) * k, (x2 + 8) * k, y * k], -100, 110,
                  fill=FOG_C, width=int(6 * k))
    elif c == "SGK":
        for i in range(3):
            a = i * math.pi / 3
            ln(50 - math.cos(a) * 30, 50 - math.sin(a) * 30,
               50 + math.cos(a) * 30, 50 + math.sin(a) * 30, 7, (190, 225, 255, 255))
    else:
        cloud(50, 52, 1.05, CLOUD)

    out = im.resize((size, size), Image.LANCZOS)
    if len(_icon_cache) > 1500:  # yüksek Hz'de bir ikonun yüzlerce karesi olur; bellek sınırsız büyümesin
        _icon_cache.clear()
    _icon_cache[key] = out
    return out


PALETTES = {
    "day": ((70, 140, 228), (30, 74, 152)),
    "night": ((30, 40, 80), (10, 14, 34)),
    "cloudy": ((96, 110, 132), (46, 54, 72)),
    "rain": ((62, 82, 110), (26, 36, 54)),
    "snow": ((112, 134, 164), (56, 70, 96)),
    "storm": ((66, 52, 98), (22, 18, 40)),
    "fog": ((122, 126, 134), (62, 66, 76)),
}


def palette_for(code, night):
    c = code or ""
    if c in ("A", "AB", "PB", "SCK"):
        g = "night" if night else "day"
    elif c in RAIN or c in ("KKY", "DY"):
        g = "rain"
    elif c in SNOW or c == "SGK":
        g = "snow"
    elif c in ("GSY", "KGSY"):
        g = "storm"
    elif c in ("SIS", "PUS", "DMN", "KF"):
        g = "fog"
    else:
        g = "cloudy"
    top, bot = PALETTES[g]
    if night and g not in ("night",):
        top = tuple(int(v * 0.55) for v in top)
        bot = tuple(int(v * 0.6) for v in bot)
    return top, bot


def palette_at(code, night, dayf=None):
    """palette_for'un geçişli hali: gün doğumu/batımında gece ve gündüz renkleri karışır,
    alt kenara ufuk ışığını andıran sıcak bir ton eklenir."""
    if dayf is None or dayf <= 0 or dayf >= 1:
        return palette_for(code, night)
    (nt, nb), (dt_, db) = palette_for(code, True), palette_for(code, False)
    tw = 1 - abs(2 * dayf - 1)
    warm = 0.35 * tw * (1.0 if scene_group(code) in ("clear", "partly") else 0.4)
    return mix(nt, dt_, dayf), mix(mix(nb, db, dayf), (235, 130, 80), warm)


THEMES = [
    ("auto", T("Hava durumuna göre")),
    ("dark", T("Koyu")),
    ("light", T("Açık", "Light")),
    ("glass", T("Cam (yarı saydam)")),
    ("clear", T("Saydam (yalnızca yazı)")),
    ("system", T("Windows temasını izle (koyu / açık)")),
    ("scene", T("Çizimli sahne (havaya göre)")),
    ("custom", T("Özel renk…")),
    ("image", T("Kendi görselim…")),
]
RADII = [(0, T("Köşeli")), (10, T("Az yuvarlak")), (22, T("Yuvarlak")), (32, T("Çok yuvarlak"))]


def hex_rgb(s, default=(59, 110, 165)):
    m = re.fullmatch(r"#?([0-9a-fA-F]{6})", s or "")
    return tuple(int(m.group(1)[i:i + 2], 16) for i in (0, 2, 4)) if m else default


def luminance(c):
    return (0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]) / 255


def custom_colors(hexstr):
    c = hex_rgb(hexstr)
    top = tuple(min(255, int(v + (255 - v) * 0.12)) for v in c)
    bot = tuple(int(v * 0.55) for v in c)
    return top, bot


def scene_group(code):
    c = code or ""
    if c in ("A", "SCK"):
        return "clear"
    if c in ("AB", "PB"):
        return "partly"
    if c in ("GSY", "KGSY"):
        return "storm"
    if c in SNOW:
        return "snow"
    if c in RAIN or c in ("KKY", "DY"):
        return "rain"
    if c in ("SIS", "PUS", "DMN", "KF"):
        return "fog"
    return "cloudy"


_scene_cache = {}


def scene_bg(w, h, code, night, S, dayf=None):
    """Havaya göre kod içinde çizilen arka plan sahnesi (RGB). Aynı boyut/durum için önbellekli.
    dayf (0..1 gündüz oranı) verilirse gün doğumu/batımında gece ve gündüz sahneleri
    karıştırılır; ufka turuncu, üst kısma hafif mor bir alacakaranlık ışığı eklenir."""
    grp = scene_group(code)
    if dayf is not None:
        q = round(dayf * 8) / 8  # 8 kademe: önbellek küçük kalır, geçiş yine yumuşak
        if 0 < q < 1:
            key = (grp, "tw", q, w, h)
            if key not in _scene_cache:
                img = Image.blend(scene_bg(w, h, code, True, S), scene_bg(w, h, code, False, S), q)
                tw = 1 - abs(2 * q - 1)
                k_ = tw * (1.0 if grp in ("clear", "partly") else 0.45)
                g = Image.linear_gradient("L").resize((w, h))  # üst 0 -> alt 255
                rgba = img.convert("RGBA")
                warm = Image.new("RGBA", (w, h), (255, 150, 80, 255))
                warm.putalpha(g.point(lambda v: int((v / 255) ** 1.6 * 190 * k_)))
                rgba.alpha_composite(warm)
                dusk = Image.new("RGBA", (w, h), (190, 120, 190, 255))
                dusk.putalpha(ImageOps.invert(g).point(lambda v: int((v / 255) ** 2 * 70 * k_)))
                rgba.alpha_composite(dusk)
                if len(_scene_cache) > 40:
                    _scene_cache.clear()
                _scene_cache[key] = rgba.convert("RGB")
            return _scene_cache[key]
        night = q <= 0
    key = (grp, night, w, h)
    if key in _scene_cache:
        return _scene_cache[key]
    top, bot = palette_for(code, night)
    out = vgradient(w, h, top, bot).convert("RGBA")
    rnd = random.Random(w * 7919 + h * 104729 + len(grp) * 31 + (1 if night else 0))

    def glow(cx, cy, rad, col, alpha):
        g = ImageOps.invert(Image.radial_gradient("L")).resize((int(rad * 2), int(rad * 2)))
        # karenin kenarında ışık tam sıfıra insin (yoksa kartta dikey bir çizgi görünür)
        g = g.point(lambda v: max(0, (v - 24) * 255 // 231))
        lay = Image.new("L", (w, h), 0)
        lay.paste(g, (int(cx - rad), int(cy - rad)))
        layer = Image.new("RGBA", (w, h), col)
        layer.putalpha(lay.point(lambda v: v * alpha // 255))
        out.alpha_composite(layer)

    def blobs(n, col, alpha, y0, y1, blur):
        lay = Image.new("L", (w, h), 0)
        dd = ImageDraw.Draw(lay)
        for _ in range(n):
            cx, cy = rnd.uniform(-0.1, 1.1) * w, rnd.uniform(y0, y1) * h
            rx = rnd.uniform(0.18, 0.36) * w
            ry = rx * rnd.uniform(0.35, 0.55)
            dd.ellipse([cx - rx, cy - ry, cx + rx, cy + ry], fill=255)
        lay = lay.filter(ImageFilter.GaussianBlur(blur))
        layer = Image.new("RGBA", (w, h), col)
        layer.putalpha(lay.point(lambda v: v * alpha // 255))
        out.alpha_composite(layer)

    if grp in ("clear", "partly"):
        if night:
            stars = Image.new("RGBA", (w, h), (0, 0, 0, 0))
            sd = ImageDraw.Draw(stars)
            for _ in range(int(w * h / 1800)):
                x, y = rnd.random() * w, rnd.random() * h * 0.75
                r = rnd.choice((0.5, 0.8, 1.1, 1.5)) * S
                sd.ellipse([x - r, y - r, x + r, y + r],
                           fill=(255, 255, 255, rnd.randint(70, 230)))
            out.alpha_composite(stars)
            glow(w * 0.86, h * 0.1, w * 0.45, (210, 225, 255, 255), 55)
        else:
            glow(w * 0.88, h * 0.06, w * 0.75, (255, 225, 150, 255), 150)
    if grp in ("partly", "cloudy", "rain", "snow", "storm"):
        dark = grp in ("rain", "storm")
        col = (210, 220, 235, 255) if night else ((70, 78, 96, 255) if dark else (255, 255, 255, 255))
        blobs(9 if grp != "partly" else 5, col, 70 if night else (120 if dark else 85),
              -0.05, 0.38, w * 0.06)
        if dark:
            blobs(6, (230, 236, 245, 255), 35, 0.0, 0.3, w * 0.05)
    if grp in ("rain", "storm"):
        lay = Image.new("L", (w, h), 0)
        dd = ImageDraw.Draw(lay)
        for _ in range(int(w * h / 1400)):
            x, y = rnd.random() * w * 1.1, rnd.random() * h
            ln = rnd.uniform(10, 20) * S
            dd.line([x, y, x - ln * 0.25, y + ln], fill=255, width=max(1, int(S)))
        layer = Image.new("RGBA", (w, h), (200, 220, 255, 255))
        layer.putalpha(lay.point(lambda v: v * 60 // 255))
        out.alpha_composite(layer)
    if grp == "snow":
        lay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        dd = ImageDraw.Draw(lay)
        for _ in range(int(w * h / 1200)):
            x, y = rnd.random() * w, rnd.random() * h
            r = rnd.uniform(0.8, 2.4) * S
            dd.ellipse([x - r, y - r, x + r, y + r], fill=(255, 255, 255, rnd.randint(110, 220)))
        out.alpha_composite(lay.filter(ImageFilter.GaussianBlur(max(0.4, S * 0.5))))
    if grp == "fog":
        for i in range(6):
            lay = Image.new("L", (w, h), 0)
            y = rnd.uniform(0.15, 0.95) * h
            ImageDraw.Draw(lay).ellipse([-w * 0.2, y - h * 0.05, w * 1.2, y + h * 0.05], fill=255)
            lay = lay.filter(ImageFilter.GaussianBlur(h * 0.04))
            layer = Image.new("RGBA", (w, h), (235, 238, 242, 255))
            layer.putalpha(lay.point(lambda v: v * 60 // 255))
            out.alpha_composite(layer)
    if grp == "storm":
        pts, x, y = [], w * rnd.uniform(0.55, 0.8), 0.0
        while y < h * 0.55:
            pts.append((x, y))
            x += rnd.uniform(-14, 14) * S
            y += rnd.uniform(10, 20) * S
        lay = Image.new("L", (w, h), 0)
        ImageDraw.Draw(lay).line(pts, fill=255, width=max(2, int(2 * S)))
        g = lay.filter(ImageFilter.GaussianBlur(6 * S))
        for src, a in ((g, 120), (lay, 200)):
            layer = Image.new("RGBA", (w, h), (235, 225, 255, 255))
            layer.putalpha(src.point(lambda v, a=a: v * a // 255))
            out.alpha_composite(layer)
    img = out.convert("RGB")
    if len(_scene_cache) > 40:
        _scene_cache.clear()
    _scene_cache[key] = img
    return img


_image_bg_cache = {}


def image_bg(path, w, h, S):
    """Kullanıcının görseli: karta göre kırpılır, hafif bulanıklaştırılıp koyulaştırılır."""
    try:
        key = (path, os.path.getmtime(path), w, h)
    except OSError:
        return None
    if key not in _image_bg_cache:
        try:
            with Image.open(path) as src:
                img = ImageOps.fit(ImageOps.exif_transpose(src).convert("RGB"), (w, h),
                                   Image.LANCZOS)
        except Exception as ex:
            log("arka plan görseli açılamadı:", path, ex)
            return None
        img = img.filter(ImageFilter.GaussianBlur(max(1, S * 1.5)))
        img = Image.blend(img, Image.new("RGB", (w, h), (0, 0, 0)), 0.38)
        _image_bg_cache.clear()
        _image_bg_cache[key] = img
    return _image_bg_cache[key]
OPACITIES = [1.0, 0.9, 0.8, 0.7, 0.6, 0.5]

STYLE_DARK = {"text": (255, 255, 255, 255), "muted": (255, 255, 255, 175),
              "faint": (255, 255, 255, 45), "track": (255, 255, 255, 50),
              "warn": (255, 190, 170, 255), "icons": "dark"}
STYLE_LIGHT = {"text": (22, 28, 38, 255), "muted": (22, 28, 38, 160),
               "faint": (22, 28, 38, 40), "track": (22, 28, 38, 32),
               "warn": (190, 60, 40, 255), "icons": "light"}
# saydam tema: zemin yok, ikincil yazılar da neredeyse tam opak olmalı
STYLE_CLEAR = {"text": (255, 255, 255, 255), "muted": (255, 255, 255, 228),
               "faint": (255, 255, 255, 110), "track": (255, 255, 255, 110),
               "warn": (255, 200, 180, 255), "icons": "dark"}

# tema -> (üst renk, alt renk, temel alfa)
THEME_BG = {
    "dark": ((44, 48, 56), (22, 24, 29), 242),
    "light": ((250, 251, 253), (230, 235, 242), 246),
    "glass": ((34, 40, 54), (14, 18, 28), 140),
}


def vgradient(w, h, top, bot):
    mask = Image.linear_gradient("L").resize((w, h))
    return Image.composite(Image.new("RGB", (w, h), bot), Image.new("RGB", (w, h), top), mask)


def lerp(a, b, t):
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(len(a)))


TEMP_STOPS = [(-10, (130, 170, 255)), (0, (110, 205, 255)), (10, (120, 225, 170)),
              (20, (255, 214, 90)), (30, (255, 145, 70)), (40, (240, 80, 80))]


def temp_color(t):
    if t <= TEMP_STOPS[0][0]:
        return TEMP_STOPS[0][1]
    for (t1, c1), (t2, c2) in zip(TEMP_STOPS, TEMP_STOPS[1:]):
        if t <= t2:
            return lerp(c1, c2, (t - t1) / (t2 - t1))
    return TEMP_STOPS[-1][1]


def fmt_temp(v):
    """Anlık ölçümü MGM'deki gibi bir ondalıkla gösterir: 8.8°"""
    if not ok(v):
        return "–"
    v = round(tconv(float(v)), 1)
    return f"{0.0 if v == 0 else v:.1f}°"


_corner_cache = {}


def round_mask_fast(w, h, r):
    """Yuvarlak köşeli maske; köşe parçaları bir kez hesaplanıp yeniden kullanılır
    (animasyonda her karede 4x örneklemeli maske çizmek yerine)."""
    r = int(max(0, min(r, w // 2, h // 2)))
    m = Image.new("L", (w, h), 255)
    if r <= 0:
        return m
    cs = _corner_cache.get(r)
    if cs is None:
        big = Image.new("L", (r * 8, r * 8), 0)
        ImageDraw.Draw(big).ellipse([0, 0, r * 8 - 1, r * 8 - 1], fill=255)
        q = big.resize((r * 2, r * 2), Image.LANCZOS).crop((0, 0, r, r))
        cs = _corner_cache[r] = (q, q.transpose(Image.FLIP_LEFT_RIGHT),
                                 q.transpose(Image.FLIP_TOP_BOTTOM), q.transpose(Image.ROTATE_180))
    m.paste(cs[0], (0, 0))
    m.paste(cs[1], (w - r, 0))
    m.paste(cs[2], (0, h - r))
    m.paste(cs[3], (w - r, h - r))
    return m


def fit_font(d, text, kind, size, maxw):
    """Metin maxw'a sığana kadar fontu küçültür (en fazla %40)."""
    s = size
    while s > size * 0.6 and text_len(text, font=F(kind, s)) > maxw:
        s -= max(1, size // 30)
    return F(kind, s)


def fit(d, text, font, maxw):
    if text_len(text, font=font) <= maxw:
        return text
    while text and text_len(text + "…", font=font) > maxw:
        text = text[:-1]
    return text + "…"


# --------------------------------------------------------------------------- Win32

user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32

GWL_EXSTYLE = -20
GWLP_HWNDPARENT = -8
WS_EX_LAYERED = 0x00080000
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_NOACTIVATE = 0x08000000
WS_EX_APPWINDOW = 0x00040000
SWP_NOSIZE, SWP_NOMOVE, SWP_NOZORDER, SWP_NOACTIVATE = 0x1, 0x2, 0x4, 0x10
HWND_BOTTOM, HWND_TOPMOST, HWND_NOTOPMOST = 1, -1, -2
ULW_ALPHA = 2


class BLENDFUNCTION(ctypes.Structure):
    _fields_ = [("BlendOp", ctypes.c_ubyte), ("BlendFlags", ctypes.c_ubyte),
                ("SourceConstantAlpha", ctypes.c_ubyte), ("AlphaFormat", ctypes.c_ubyte)]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG),
                ("biHeight", wintypes.LONG), ("biPlanes", wintypes.WORD),
                ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG),
                ("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.DWORD),
                ("biClrImportant", wintypes.DWORD)]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", wintypes.DWORD * 3)]


class ULWINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("hdcDst", wintypes.HDC),
                ("pptDst", ctypes.POINTER(wintypes.POINT)),
                ("psize", ctypes.POINTER(wintypes.SIZE)), ("hdcSrc", wintypes.HDC),
                ("pptSrc", ctypes.POINTER(wintypes.POINT)), ("crKey", wintypes.DWORD),
                ("pblend", ctypes.POINTER(BLENDFUNCTION)), ("dwFlags", wintypes.DWORD),
                ("prcDirty", ctypes.POINTER(wintypes.RECT))]


user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
user32.GetAncestor.restype = wintypes.HWND
user32.GetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int]
user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
user32.SetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
user32.SetWindowLongPtrW.restype = ctypes.c_ssize_t
user32.SetWindowPos.argtypes = [wintypes.HWND, ctypes.c_ssize_t, ctypes.c_int,
                                ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.UINT]
user32.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
user32.FindWindowW.restype = wintypes.HWND
user32.GetDC.argtypes = [wintypes.HWND]
user32.GetDC.restype = wintypes.HDC
user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
user32.UpdateLayeredWindowIndirect.argtypes = [wintypes.HWND, ctypes.POINTER(ULWINFO)]
gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
gdi32.CreateCompatibleDC.restype = wintypes.HDC
gdi32.CreateDIBSection.argtypes = [wintypes.HDC, ctypes.c_void_p, wintypes.UINT,
                                   ctypes.POINTER(ctypes.c_void_p), wintypes.HANDLE,
                                   wintypes.DWORD]
gdi32.CreateDIBSection.restype = ctypes.c_void_p
gdi32.SelectObject.argtypes = [wintypes.HDC, ctypes.c_void_p]
gdi32.SelectObject.restype = ctypes.c_void_p
gdi32.DeleteObject.argtypes = [ctypes.c_void_p]
gdi32.DeleteDC.argtypes = [wintypes.HDC]


def set_dpi_awareness():
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass
    try:
        return user32.GetDpiForSystem() / 96.0
    except Exception:
        return 1.0


class LayeredSurface:
    """Pencere içeriğini per-pixel alfa ile (UpdateLayeredWindowIndirect) çizer."""

    def __init__(self, hwnd, w, h):
        self.hwnd, self.w, self.h = hwnd, w, h
        self.screen_dc = user32.GetDC(None)
        self.mem_dc = gdi32.CreateCompatibleDC(self.screen_dc)
        bmi = BITMAPINFO()
        bmi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        bmi.bmiHeader.biWidth = w
        bmi.bmiHeader.biHeight = -h
        bmi.bmiHeader.biPlanes = 1
        bmi.bmiHeader.biBitCount = 32
        self.bits = ctypes.c_void_p()
        self.hbmp = gdi32.CreateDIBSection(self.mem_dc, ctypes.byref(bmi), 0,
                                           ctypes.byref(self.bits), None, 0)
        self.old = gdi32.SelectObject(self.mem_dc, self.hbmp)

    def show(self, img):
        # RGBA -> ön-çarpımlı BGRA tek geçişte (eskiden: dönüştür + böl + birleştir = 4 geçiş)
        data = img.tobytes("raw", "BGRa")
        ctypes.memmove(self.bits, data, len(data))
        self._commit(None)

    def show_at(self, img, x, y):
        """Küçük bir görüntüyü pencerenin (x, y) konumuna yazar; yalnızca o alan güncellenir
        (animasyonlu ikon: tüm kart yerine ikonun alanı dönüştürülüp ekrana basılır)."""
        W, H = self.w, self.h
        x, y = int(x), int(y)
        sx, sy = max(0, -x), max(0, -y)
        x, y = max(0, x), max(0, y)
        w, h = min(img.width - sx, W - x), min(img.height - sy, H - y)
        if w <= 0 or h <= 0:
            return
        data = img.crop((sx, sy, sx + w, sy + h)).tobytes("raw", "BGRa")
        base, rw = self.bits.value, w * 4
        for r in range(h):
            ctypes.memmove(base + ((y + r) * W + x) * 4, data[r * rw:(r + 1) * rw], rw)
        self._commit(wintypes.RECT(x, y, x + w, y + h))

    def _commit(self, rect):
        size = wintypes.SIZE(self.w, self.h)
        src = wintypes.POINT(0, 0)
        blend = BLENDFUNCTION(0, 0, getattr(self, "alpha", 255), 1)
        info = ULWINFO()
        info.cbSize = ctypes.sizeof(ULWINFO)
        info.hdcDst = self.screen_dc
        info.psize = ctypes.pointer(size)
        info.hdcSrc = self.mem_dc
        info.pptSrc = ctypes.pointer(src)
        info.pblend = ctypes.pointer(blend)
        info.dwFlags = ULW_ALPHA
        info.prcDirty = ctypes.pointer(rect) if rect is not None else None
        if not user32.UpdateLayeredWindowIndirect(self.hwnd, ctypes.byref(info)):
            log("UpdateLayeredWindowIndirect başarısız:", ctypes.GetLastError())

    def close(self):
        gdi32.SelectObject(self.mem_dc, self.old)
        gdi32.DeleteObject(self.hbmp)
        gdi32.DeleteDC(self.mem_dc)
        user32.ReleaseDC(None, self.screen_dc)


def set_window_styles(hwnd):
    ex = user32.GetWindowLongPtrW(hwnd, GWL_EXSTYLE)
    ex = (ex | WS_EX_LAYERED | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE) & ~WS_EX_APPWINDOW
    user32.SetWindowLongPtrW(hwnd, GWL_EXSTYLE, ex)


def pin_to_desktop(hwnd):
    progman = user32.FindWindowW("Progman", None)
    user32.SetWindowLongPtrW(hwnd, GWLP_HWNDPARENT, progman or 0)
    user32.SetWindowPos(hwnd, HWND_NOTOPMOST, 0, 0, 0, 0,
                        SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE)
    keep_bottom(hwnd)


def keep_bottom(hwnd):
    user32.SetWindowPos(hwnd, HWND_BOTTOM, 0, 0, 0, 0,
                        SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE)


def pin_on_top(hwnd):
    user32.SetWindowLongPtrW(hwnd, GWLP_HWNDPARENT, 0)
    user32.SetWindowPos(hwnd, HWND_TOPMOST, 0, 0, 0, 0,
                        SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE)


def move_window(hwnd, x, y):
    user32.SetWindowPos(hwnd, 0, int(x), int(y), 0, 0,
                        SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE)


# --------------------------------------------------------------------------- sistem tepsisi

shell32 = ctypes.windll.shell32
kernel32 = ctypes.windll.kernel32
WS_EX_TRANSPARENT = 0x00000020
WM_APP_TRAY = 0x8000 + 77
WM_LBUTTONUP, WM_RBUTTONUP, WM_LBUTTONDBLCLK = 0x0202, 0x0205, 0x0203
NIN_BALLOONUSERCLICK = 0x0405
NIM_ADD, NIM_MODIFY, NIM_DELETE = 0, 1, 2
NIF_MESSAGE, NIF_ICON, NIF_TIP, NIF_INFO = 0x1, 0x2, 0x4, 0x10
NIIF_USER, NIIF_LARGE_ICON = 0x4, 0x20

WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, wintypes.HWND, wintypes.UINT,
                             wintypes.WPARAM, wintypes.LPARAM)


class WNDCLASSW(ctypes.Structure):
    _fields_ = [("style", wintypes.UINT), ("lpfnWndProc", WNDPROC),
                ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int),
                ("hInstance", wintypes.HINSTANCE), ("hIcon", wintypes.HICON),
                ("hCursor", wintypes.HANDLE), ("hbrBackground", wintypes.HANDLE),
                ("lpszMenuName", wintypes.LPCWSTR), ("lpszClassName", wintypes.LPCWSTR)]


class NOTIFYICONDATAW(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("hWnd", wintypes.HWND), ("uID", wintypes.UINT),
                ("uFlags", wintypes.UINT), ("uCallbackMessage", wintypes.UINT),
                ("hIcon", wintypes.HICON), ("szTip", wintypes.WCHAR * 128),
                ("dwState", wintypes.DWORD), ("dwStateMask", wintypes.DWORD),
                ("szInfo", wintypes.WCHAR * 256), ("uVersion", wintypes.UINT),
                ("szInfoTitle", wintypes.WCHAR * 64), ("dwInfoFlags", wintypes.DWORD),
                ("guidItem", ctypes.c_byte * 16), ("hBalloonIcon", wintypes.HICON)]


class ICONINFO(ctypes.Structure):
    _fields_ = [("fIcon", wintypes.BOOL), ("xHotspot", wintypes.DWORD),
                ("yHotspot", wintypes.DWORD), ("hbmMask", ctypes.c_void_p),
                ("hbmColor", ctypes.c_void_p)]


class MONITORINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT),
                ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD)]


class MONITORINFOEXW(MONITORINFO):
    _fields_ = [("szDevice", ctypes.c_wchar * 32)]


user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.DefWindowProcW.restype = ctypes.c_ssize_t
user32.CreateWindowExW.argtypes = [wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR,
                                   wintypes.DWORD, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                   ctypes.c_int, wintypes.HWND, wintypes.HMENU,
                                   wintypes.HINSTANCE, wintypes.LPVOID]
user32.CreateWindowExW.restype = wintypes.HWND
user32.RegisterClassW.argtypes = [ctypes.POINTER(WNDCLASSW)]
user32.DestroyWindow.argtypes = [wintypes.HWND]
user32.RegisterWindowMessageW.argtypes = [wintypes.LPCWSTR]
user32.RegisterWindowMessageW.restype = wintypes.UINT
user32.SetForegroundWindow.argtypes = [wintypes.HWND]
user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
user32.CreateIconIndirect.argtypes = [ctypes.POINTER(ICONINFO)]
user32.CreateIconIndirect.restype = wintypes.HICON
user32.DestroyIcon.argtypes = [wintypes.HICON]
user32.MonitorFromPoint.argtypes = [wintypes.POINT, wintypes.DWORD]
user32.MonitorFromPoint.restype = wintypes.HANDLE
user32.GetMonitorInfoW.argtypes = [wintypes.HANDLE, ctypes.POINTER(MONITORINFO)]
kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
kernel32.GetModuleHandleW.restype = wintypes.HINSTANCE
shell32.Shell_NotifyIconW.argtypes = [wintypes.DWORD, ctypes.POINTER(NOTIFYICONDATAW)]
gdi32.CreateBitmap.argtypes = [ctypes.c_int, ctypes.c_int, wintypes.UINT, wintypes.UINT,
                               ctypes.c_void_p]
gdi32.CreateBitmap.restype = ctypes.c_void_p


def pil_to_hicon(img):
    """RGBA PIL görüntüsünden alfa kanallı HICON üretir."""
    img = img.convert("RGBA")
    w, h = img.size
    bmi = BITMAPINFO()
    bmi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    bmi.bmiHeader.biWidth, bmi.bmiHeader.biHeight = w, -h
    bmi.bmiHeader.biPlanes, bmi.bmiHeader.biBitCount = 1, 32
    bits = ctypes.c_void_p()
    hbm = gdi32.CreateDIBSection(None, ctypes.byref(bmi), 0, ctypes.byref(bits), None, 0)
    r, g, b, a = img.split()
    data = Image.merge("RGBA", (b, g, r, a)).tobytes()
    ctypes.memmove(bits, data, len(data))
    mask = gdi32.CreateBitmap(w, h, 1, 1, None)
    ii = ICONINFO(True, 0, 0, mask, hbm)
    hicon = user32.CreateIconIndirect(ctypes.byref(ii))
    gdi32.DeleteObject(hbm)
    gdi32.DeleteObject(mask)
    return hicon


user32.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]
user32.MonitorFromWindow.restype = wintypes.HANDLE
gdi32.CreateDCW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.LPCWSTR, ctypes.c_void_p]
gdi32.CreateDCW.restype = wintypes.HDC
gdi32.GetDeviceCaps.argtypes = [wintypes.HDC, ctypes.c_int]


def refresh_hz(mon):
    """Ekranın yenileme hızı (Hz); okunamazsa 60."""
    mi = MONITORINFOEXW()
    mi.cbSize = ctypes.sizeof(MONITORINFOEXW)
    if mon and user32.GetMonitorInfoW(mon, ctypes.byref(mi)):
        hdc = gdi32.CreateDCW(mi.szDevice, mi.szDevice, None, None)
        if hdc:
            hz = gdi32.GetDeviceCaps(hdc, 116)  # VREFRESH
            gdi32.DeleteDC(hdc)
            if 20 <= hz <= 1000:  # 0 / 1 = "donanım varsayılanı"
                return hz
    return 60


def work_area(x, y):
    """(x, y) noktasının bulunduğu ekranın görev çubuğu hariç alanı: (sol, üst, sağ, alt)."""
    mon = user32.MonitorFromPoint(wintypes.POINT(int(x), int(y)), 2)
    mi = MONITORINFO()
    mi.cbSize = ctypes.sizeof(MONITORINFO)
    if mon and user32.GetMonitorInfoW(mon, ctypes.byref(mi)):
        r = mi.rcWork
        return r.left, r.top, r.right, r.bottom
    return 0, 0, user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)


def set_click_through(hwnd, on):
    ex = user32.GetWindowLongPtrW(hwnd, GWL_EXSTYLE)
    ex = (ex | WS_EX_TRANSPARENT) if on else (ex & ~WS_EX_TRANSPARENT)
    user32.SetWindowLongPtrW(hwnd, GWL_EXSTYLE, ex)


def windows_light_theme():
    """Windows uygulama modu açık tema mı?"""
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize") as k:
            return bool(winreg.QueryValueEx(k, "AppsUseLightTheme")[0])
    except Exception:
        return False


_tray_ids = itertools.count(1)


class Tray:
    """Bildirim alanı ikonu: sıcaklığı gösterir, tıklamaları ve bildirim balonlarını yönetir.
    Tk ana döngüsü bu iş parçacığının pencere mesajlarını dağıttığı için ayrı döngü gerekmez.

    ÖNEMLİ: _wndproc, Tk olay döngüsünün içinden (Tk Python kilidini bırakmışken) çağrılır.
    Buradan Tk'ye çağrı yapmak (root.after, menü açmak…) Python'u çökertir (0xc0000409).
    Bu yüzden tıklamalar yalnızca self.events kuyruğuna yazılır; widget'ın poll() döngüsü okur."""

    def __init__(self):
        self.events = deque()  # "left" / "right"
        self.hicon = None
        self.added = False
        self._proc = WNDPROC(self._wndproc)  # GC'den koru
        # pencere oluşturulurken de mesaj gelir: _wndproc'un kullandığı alanlar önce hazır olmalı
        self.msg_taskbar = user32.RegisterWindowMessageW("TaskbarCreated")
        self.hwnd = None
        hinst = kernel32.GetModuleHandleW(None)
        # her örneğe ayrı sınıf: kapatılmış bir örneğin pencere yordamı yeniden kullanılmasın
        self.cls = f"HavaDurumuTray{os.getpid()}_{next(_tray_ids)}"
        wc = WNDCLASSW()
        wc.lpfnWndProc, wc.hInstance, wc.lpszClassName = self._proc, hinst, self.cls
        user32.RegisterClassW(ctypes.byref(wc))
        self.hwnd = user32.CreateWindowExW(0, self.cls, "HavaDurumuTray", 0, 0, 0, 0, 0,
                                           None, None, hinst, None)
        self.tip = APP_NAME

    def _nid(self, flags=0):
        nid = NOTIFYICONDATAW()
        nid.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
        nid.hWnd, nid.uID, nid.uFlags = self.hwnd, 1, flags
        nid.uCallbackMessage = WM_APP_TRAY
        nid.hIcon = self.hicon
        nid.szTip = self.tip[:127]
        return nid

    def _wndproc(self, hwnd, msg, wp, lp):
        try:
            if msg == WM_APP_TRAY:
                ev = lp & 0xFFFF
                if ev in (WM_LBUTTONUP, NIN_BALLOONUSERCLICK):
                    self.events.append("left")
                elif ev == WM_RBUTTONUP:
                    self.events.append("right")
                return 0
            if msg == self.msg_taskbar and self.hicon:  # explorer yeniden başladı
                self.added = False
                self._push()
                return 0
        except Exception:
            log(traceback.format_exc())
        return user32.DefWindowProcW(hwnd, msg, wp, lp)

    def _push(self):
        nid = self._nid(NIF_MESSAGE | NIF_ICON | NIF_TIP)
        if not self.added:
            self.added = bool(shell32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(nid)))
        else:
            shell32.Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(nid))

    def set(self, img, tip):
        old = self.hicon
        self.hicon = pil_to_hicon(img)
        self.tip = tip
        self._push()
        if old:
            user32.DestroyIcon(old)

    def notify(self, title, text, img=None):
        """Windows bildirimi gösterir. img: bildirimdeki büyük ikon (RGBA, SM_CXICON boyutunda).
        Windows, NIIF_LARGE_ICON ile küçük ikon verilirse isteği reddeder (hata 1462)."""
        if not self.added:
            return False
        nid = self._nid(NIF_INFO)
        nid.szInfoTitle = title[:63]
        nid.szInfo = text[:255]
        old = getattr(self, "hballoon", None)
        self.hballoon = pil_to_hicon(img) if img is not None else None
        if self.hballoon:
            nid.dwInfoFlags = NIIF_USER | NIIF_LARGE_ICON
            nid.hBalloonIcon = self.hballoon
        else:
            nid.dwInfoFlags = 1  # NIIF_INFO: standart bilgi ikonu
        ok_ = bool(shell32.Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(nid)))
        if old:
            user32.DestroyIcon(old)
        return ok_

    def close(self):
        if self.added:
            shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(self._nid()))
            self.added = False
        if self.hwnd:
            user32.DestroyWindow(self.hwnd)
            self.hwnd = None
        if self.hicon:
            user32.DestroyIcon(self.hicon)
            self.hicon = None


def monitor_work_areas():
    """Bütün ekranların görev çubuğu hariç alanları."""
    out = []
    proc_t = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HANDLE, wintypes.HDC,
                                ctypes.POINTER(wintypes.RECT), wintypes.LPARAM)

    def cb(hmon, hdc, rc, lp):
        mi = MONITORINFO()
        mi.cbSize = ctypes.sizeof(MONITORINFO)
        if user32.GetMonitorInfoW(hmon, ctypes.byref(mi)):
            r = mi.rcWork
            out.append((r.left, r.top, r.right, r.bottom))
        return True
    user32.EnumDisplayMonitors(None, None, proc_t(cb), 0)
    return out


WM_HOTKEY = 0x0312
HOTKEYS = [("win_shift_w", "Win+Shift+W", 0x8 | 0x4, 0x57),
           ("ctrl_alt_w", "Ctrl+Alt+W", 0x2 | 0x1, 0x57),
           ("ctrl_shift_h", "Ctrl+Shift+H", 0x2 | 0x4, 0x48),
           ("off", T("Kapalı"), 0, 0)]
user32.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT]
user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
user32.GetAsyncKeyState.restype = ctypes.c_short
user32.WindowFromPoint.argtypes = [wintypes.POINT]
user32.WindowFromPoint.restype = wintypes.HWND
user32.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]


class HotkeyWindow:
    """Genel kısayol tuşu için gizli pencere. Tray ile aynı kural: pencere yordamında
    Tk'ye dokunulmaz, olay yalnızca kuyruğa yazılır (aksi halde Python çöker)."""

    def __init__(self):
        self.events = deque()
        self.active = False
        self._proc = WNDPROC(self._wndproc)
        self.hwnd = None
        hinst = kernel32.GetModuleHandleW(None)
        self.cls = f"HavaDurumuHotkey{os.getpid()}_{next(_tray_ids)}"
        wc = WNDCLASSW()
        wc.lpfnWndProc, wc.hInstance, wc.lpszClassName = self._proc, hinst, self.cls
        user32.RegisterClassW(ctypes.byref(wc))
        self.hwnd = user32.CreateWindowExW(0, self.cls, "HavaDurumuHotkey", 0, 0, 0, 0, 0,
                                           None, None, hinst, None)

    def _wndproc(self, hwnd, msg, wp, lp):
        if msg == WM_HOTKEY:
            self.events.append("hotkey")
            return 0
        return user32.DefWindowProcW(hwnd, msg, wp, lp)

    def register(self, mods, vk):
        user32.UnregisterHotKey(self.hwnd, 1)
        self.active = bool(mods) and bool(user32.RegisterHotKey(self.hwnd, 1, mods | 0x4000, vk))
        return self.active

    def close(self):
        if self.hwnd:
            user32.UnregisterHotKey(self.hwnd, 1)
            user32.DestroyWindow(self.hwnd)
            self.hwnd = None


class Popup:
    """Mini / kompakt kartın üzerine gelince açılan ayrıntı kartı (yalnızca görüntü).

    Pencere doğrudan gerçek boyut ve konumda oluşturulur: önce küçük oluşturulup sonra
    büyütülen katmanlı Tk penceresine yazma Windows'ta hata 87 ile reddediliyor."""

    def __init__(self, root):
        self.root = root
        self.win = self.hwnd = self.surface = None
        self.size, self.visible, self.idx = None, False, None

    def _create(self, w, h, x, y):
        self.close()
        win = tk.Toplevel(self.root)
        win.withdraw()
        win.overrideredirect(True)
        win.geometry(f"{w}x{h}+{int(x)}+{int(y)}")
        win.deiconify()
        win.update_idletasks()
        self.win = win
        self.hwnd = user32.GetAncestor(win.winfo_id(), 2)
        set_window_styles(self.hwnd)
        set_click_through(self.hwnd, True)  # fare olayları alttaki pencereye gider
        self.surface = LayeredSurface(self.hwnd, w, h)
        self.size, self.visible = (w, h), True

    def show(self, img, x, y):
        w, h = img.size
        if (w, h) != self.size or self.win is None:
            self._create(w, h, x, y)
        elif not self.visible:
            user32.ShowWindow(self.hwnd, 4)  # SW_SHOWNOACTIVATE
            self.visible = True
        user32.SetWindowPos(self.hwnd, HWND_TOPMOST, int(x), int(y), 0, 0,
                            SWP_NOSIZE | SWP_NOACTIVATE)
        self.surface.show(img)

    def hide(self):
        if self.visible and self.hwnd:
            user32.ShowWindow(self.hwnd, 0)
        self.visible = False
        self.idx = None

    def close(self):
        if self.surface:
            self.surface.close()
            self.surface = None
        if self.win is not None:
            self.win.destroy()
            self.win = None
        self.hwnd, self.size, self.visible = None, None, False


def tray_image(text, size):
    """Tepsi ikonu: saydam zeminde koyu konturlu beyaz sıcaklık yazısı."""
    n = size * 4
    im = Image.new("RGBA", (n, n), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    fs = int(n * 0.86)
    while fs > 8:
        f = ImageFont.truetype(FONT_FILES["sb"], fs)
        sw = max(2, n // 14)
        box = d.textbbox((0, 0), text, font=f, stroke_width=sw)
        if box[2] - box[0] <= n and box[3] - box[1] <= n:
            break
        fs -= 2
    d.text((n / 2, n / 2), text, font=f, fill=(255, 255, 255, 255), anchor="mm",
           stroke_width=sw, stroke_fill=(20, 26, 38, 255))
    return im.resize((size, size), Image.LANCZOS)


# --------------------------------------------------------------------------- bildirim kimliği
#
# Bildirim başlığında uygulama adının ("Hava Durumu") görünmesi için Windows, bildirimi gönderen
# uygulamayı Başlat menüsündeki bir kısayoldan tanır: kısayolun System.AppUserModel.ID özelliği
# bildirimin kimliğiyle aynı olmalıdır. Denenip İŞE YARAMAYANLAR: tepsi ikonuna sabit GUID,
# HKCU\Software\Classes\AppUserModelId kaydı, SetCurrentProcessExplicitAppUserModelID.
# Kısayol oluşturulduktan sonra Windows'un onu işlemesi birkaç saniye sürer (ölçülen: ~6 sn);
# bu süre dolmadan gönderilen bildirim yalnızca bildirim merkezine düşer, ekranda açılmaz.
# Bu yüzden kısayol 30 saniyeden yeniyse eski yöntem (tepsi balonu) kullanılır.
SHORTCUT_NAME = os.environ.get("HAVADURUMU_SHORTCUT", APP_NAME)  # testler başka ad verir
AUMID = "HavaDurumu.Widget" if SHORTCUT_NAME == APP_NAME else "HavaDurumu.Widget.Test"


def start_shortcut_path():
    return os.path.join(os.environ.get("APPDATA", ""),
                        r"Microsoft\Windows\Start Menu\Programs", SHORTCUT_NAME + ".lnk")


class _GUID(ctypes.Structure):
    _fields_ = [("b", ctypes.c_byte * 16)]

    @staticmethod
    def of(s):
        import uuid
        g = _GUID()
        ctypes.memmove(g.b, uuid.UUID(s).bytes_le, 16)
        return g


class _PROPERTYKEY(ctypes.Structure):
    _fields_ = [("fmtid", _GUID), ("pid", wintypes.DWORD)]


class _PROPVARIANT(ctypes.Structure):
    _fields_ = [("vt", ctypes.c_ushort), ("r1", ctypes.c_ushort), ("r2", ctypes.c_ushort),
                ("r3", ctypes.c_ushort), ("val", ctypes.c_void_p), ("pad", ctypes.c_void_p)]


def _com_call(obj, idx, *args, argtypes=()):
    """COM nesnesinin sanal tablosundaki idx. yöntemi çağırır (hata olursa OSError)."""
    vtbl = ctypes.cast(obj, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p)))[0]
    return ctypes.WINFUNCTYPE(ctypes.HRESULT, ctypes.c_void_p, *argtypes)(vtbl[idx])(obj, *args)


def create_start_shortcut(lnk, target, args, aumid, icon):
    """Başlat menüsünde, bildirim kimliğini (AUMID) taşıyan bir kısayol oluşturur."""
    ole32 = ctypes.OleDLL("ole32")
    try:
        ole32.CoInitializeEx(None, 2)
    except OSError:
        pass  # iş parçacığı zaten başka kipte başlatılmış
    link = ctypes.c_void_p()
    ole32.CoCreateInstance(ctypes.byref(_GUID.of("00021401-0000-0000-C000-000000000046")), None, 1,
                           ctypes.byref(_GUID.of("000214F9-0000-0000-C000-000000000046")),
                           ctypes.byref(link))

    def qi(iid):
        out = ctypes.c_void_p()
        _com_call(link, 0, ctypes.byref(_GUID.of(iid)), ctypes.byref(out),
                  argtypes=(ctypes.POINTER(_GUID), ctypes.POINTER(ctypes.c_void_p)))
        return out
    _com_call(link, 20, target, argtypes=(wintypes.LPCWSTR,))               # SetPath
    _com_call(link, 11, args, argtypes=(wintypes.LPCWSTR,))                 # SetArguments
    _com_call(link, 7, T("{} hava durumu widget'ı").format(APP_NAME), argtypes=(wintypes.LPCWSTR,))  # SetDescription
    if icon:
        _com_call(link, 17, icon, 0, argtypes=(wintypes.LPCWSTR, ctypes.c_int))  # SetIconLocation
    store = qi("886D8EEB-8CF2-4446-8D02-CDBA1DBDCF99")                      # IPropertyStore
    key = _PROPERTYKEY(_GUID.of("9F4C2855-9F79-4B39-A8D0-E1D42DE1D5F3"), 5)  # PKEY_AppUserModel_ID
    buf = ctypes.create_unicode_buffer(aumid)
    pv = _PROPVARIANT(vt=31, val=ctypes.cast(buf, ctypes.c_void_p))         # VT_LPWSTR
    _com_call(store, 6, ctypes.byref(key), ctypes.byref(pv),
              argtypes=(ctypes.POINTER(_PROPERTYKEY), ctypes.POINTER(_PROPVARIANT)))  # SetValue
    _com_call(store, 7)                                                      # Commit
    pf = qi("0000010B-0000-0000-C000-000000000046")                         # IPersistFile
    os.makedirs(os.path.dirname(lnk), exist_ok=True)
    _com_call(pf, 6, lnk, True, argtypes=(wintypes.LPCWSTR, wintypes.BOOL))  # Save
    for o in (pf, store, link):
        _com_call(o, 2)                                                      # Release


def send_toast(title, text, icon_png=None):
    """WinRT bildirimi (başlıkta kısayolun adı görünür). Başarısızsa istisna atar."""
    from winrt.windows.data.xml.dom import XmlDocument
    from winrt.windows.ui.notifications import ToastNotification, ToastNotificationManager
    xml = ('<toast><visual><binding template="ToastGeneric">'
           f"<text>{xml_escape(title)}</text><text>{xml_escape(text)}</text>")
    if icon_png:
        uri = xml_escape(pathlib.Path(icon_png).as_uri(), {'"': "&quot;"})
        xml += f'<image placement="appLogoOverride" src="{uri}"/>'
    xml += "</binding></visual></toast>"
    doc = XmlDocument()
    doc.load_xml(xml)
    import warnings
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")  # create_toast_notifier_with_id "eski" uyarısı
        notifier = ToastNotificationManager.create_toast_notifier_with_id(AUMID)
    notifier.show(ToastNotification(doc))


# --------------------------------------------------------------------------- ayarlar

def load_json(path, default):
    try:
        with open(path, encoding="utf-8-sig") as f:  # Not Defteri BOM'u da okunur
            return json.load(f)
    except Exception:
        return default


def save_json(path, data):
    try:
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
        os.replace(tmp, path)
    except Exception as ex:
        log("kaydedilemedi:", path, ex)


def startup_enabled():
    return os.path.exists(STARTUP_VBS)


def set_startup(on):
    if not on:
        try:
            os.remove(STARTUP_VBS)
        except FileNotFoundError:
            pass
        return
    if FROZEN:
        cmd = f'sh.Run """{sys.executable}""", 0, False'
    else:
        exe = sys.executable
        pyw = os.path.join(os.path.dirname(exe), "pythonw.exe")
        if not os.path.exists(pyw):
            pyw = exe
        script = os.path.abspath(__file__)
        cmd = f'sh.Run """{pyw}"" ""{script}""", 0, False'
    with open(STARTUP_VBS, "w", encoding="utf-16") as f:
        f.write('Set sh = CreateObject("WScript.Shell")\r\n' + cmd + "\r\n")


# --------------------------------------------------------------------------- düzen / seçenekler

MINI_W, MINI_H = 150, 78
MULTI_MAX, MULTI_GAP = 5, 6
# düzen -> (genişlik, yükseklik, menü etiketi)  [ölçek 1.0'daki mantıksal piksel]
# "row" / "col": tüm şehirler mini kartlar halinde yan yana / alt alta (boyut şehir sayısına bağlı)
LAYOUTS = {
    "micro": (100, 44, T("Mikro (ikon + sıcaklık)")),
    "mini": (MINI_W, MINI_H, T("Mini (yalnızca sıcaklık)")),
    "miniplus": (MINI_W, 112, T("Mini+ (+ 12 saatlik grafik)")),
    "vmini": (84, 150, T("Dikey mini")),
    "compact": (340, 190, T("Kompakt (anlık durum)")),
    "peek": (340, 368, T("Büyütülmüş mini (anlık + ayrıntılar + 12 saat)")),  # menüde yok
    "medium": (360, 426, T("Orta (+ ayrıntılar, saatlik)")),
    "full": (360, 584, T("Tam (+ 5 günlük tahmin)")),
    "weekly": (360, 404, T("Haftalık (7 gün + 16 günlük eğilim)")),
    "row": (MINI_W, MINI_H, T("Yan yana (tüm şehirler)")),
    "col": (MINI_W, MINI_H, T("Alt alta (tüm şehirler)")),
}
SCALES = [0.7, 0.85, 1.0, 1.15, 1.3, 1.5]
ROTATE_OPTIONS = [(5, T("5 saniye")), (10, T("10 saniye")), (15, T("15 saniye")), (30, T("30 saniye")),
                  (60, T("1 dakika")), (300, T("5 dakika"))]
REFRESH_OPTIONS = [5, 10, 15, 30, 60]
MINI_LINES = [("hl", T("En yüksek / en düşük")), ("feels", T("Hissedilen")), ("rain", T("Yağış olasılığı")),
              ("wind", T("Rüzgar")), ("humidity", T("Nem")), ("aqi", T("Hava kalitesi")),
              ("uv", T("UV indeksi")), ("clock", T("Saat")), ("rain_eta", T("Yağış geri sayımı")),
              ("sun", T("Gün doğumu / batımı"))]
SMALL_LAYOUTS = ("micro", "mini", "miniplus", "vmini")
MAX_DOTS = 12
# Animasyonlar widget'ın bulunduğu ekranın yenileme hızında (Hz) çizilir. Windows zamanlayıcısı
# 1 ms çözünürlüğe alınır (varsayılan 15,6 ms adımda 7 ms isteği 15,6 ms olur) ve çizim süresi
# bekleme süresinden düşülür.
WET = set(RAIN) | set(SNOW) | {"KKY", "DY", "GSY", "KGSY"}


# ---- arka plan efektleri
# Kartın arka planı ile yazılar arasında, havaya göre bir hareket. Her hava durumunun gündüz ve
# gece için ayrı bir görünümü var (ör. açık: gündüz güneş parıltısı, gece yıldızlar; bulutlu gece
# yıldızsız, ay ışığında bulutlar). Kartın sabit kısmı önbellekte durur; her karede yalnızca efekt
# katmanı çizilir. Widget başka pencerelerin altında kalınca hiç çizilmez.
FX_MS_HIDDEN = 400
FX_FPS = 60  # arka plan efektleri en fazla bu hızda: her karede bütün kart çizilir, 144 Hz'de bir
             # çekirdeği doldurur. Aradaki karelerde yalnızca hava ikonu ekranın hızında yenilenir.
FX_KINDS = {"A": "clear", "AB": "fewclouds", "PB": "partly", "CB": "overcast", "KKY": "sleet",
            "DY": "hail", "GSY": "storm", "KGSY": "storm", "SIS": "fog", "PUS": "haze",
            "DMN": "smoke", "R": "wind", "GKR": "wind", "KKR": "wind", "KF": "dust",
            "SCK": "heat", "SGK": "frost", "HHY": "rain",
            "HSY": "showers", "SY": "showers", "MSY": "showers", "KSY": "showers"}


def fx_kind(code, night=False):
    """Hava kodu -> efekt türü. Gündüz / gece farkı AmbientFX içinde çizilir."""
    if code in FX_KINDS:
        return FX_KINDS[code]
    if code in RAIN:
        return "rain"
    return "snow" if code in SNOW else "partly"


def _soft_sprite(w, h, color, alpha, blur, shapes):
    """Yumuşak kenarlı (bulanık) bir şekil: bulut, sis bandı, parıltı."""
    m = int(blur * 2.5) + 1
    a = Image.new("L", (max(1, w) + 2 * m, max(1, h) + 2 * m), 0)
    dr = ImageDraw.Draw(a)
    for x1, y1, x2, y2 in shapes:
        dr.ellipse([x1 + m, y1 + m, x2 + m, y2 + m], fill=alpha)
    a = a.filter(ImageFilter.GaussianBlur(blur))
    img = Image.new("RGBA", a.size, tuple(color) + (0,))
    img.putalpha(a)
    return img


def _paste(out, spr, x, y):
    """Saydam şekli kartın RGB arka planına karıştırır; kenardan taşan kısmı paste kırpar."""
    out.paste(spr, (x, y), spr)


def _clip_mask(A, x, y, w, h):
    """Maskeyi (w x h) kartın içinde kalan kısmına kırpar: her karede yalnızca görünen alan işlenir."""
    box = (max(0, -x), max(0, -y), min(A.width, w - x), min(A.height, h - y))
    return A.crop(box), (x + box[0], y + box[1])


def _paste_lit(out, col, A, pos, z):
    """Tek renkli ışığı (glow, hale, leke) z parlaklığıyla karıştırır; z her karede yeniden hesaplanır,
    hazır parlaklık basamakları arasında atlama olmaz."""
    out.paste(col, pos + (pos[0] + A.width, pos[1] + A.height), A.point([int(v * z) for v in range(256)]))


class AmbientFX:
    """Bir kartın efekt katmanı: üst üste çizilen katmanlar (parıltı, yıldız, bulut, yağış...).
    Parçacıkların başlangıç değerleri bir kez (sabit tohumla) üretilir; konumlar zamana göre
    hesaplanır, kare hızı düşse de hareket aynı hızda kalır."""

    SUN, MOON, WHITE = (255, 226, 150), (205, 220, 255), (255, 255, 255)
    NCLOUD = (125, 140, 172)  # gece bulutu: ay ışığında mavimsi gri

    def __init__(self, kind, w, h, S, light, level=3, sun_at=None, night=False):
        self.kind, self.w, self.h, self.S, self.light, self.night = kind, w, h, S, light, night
        self.rnd = random.Random(f"{kind}{night}{w}x{h}")
        self.area = max(0.4, (w * h) / (S * S) / (188 * 98)) ** 0.5  # mini kart = 1
        self.layers = []
        cx, cy, cr = sun_at or (w * 0.2, h * 0.35, h * 0.3)
        N = night
        n = lambda base, cap=90: max(1, int(min(cap, base * self.area)))
        SUN, MOON, WHITE, NCLOUD = self.SUN, self.MOON, self.WHITE, self.NCLOUD
        rainc = (175, 205, 255) if N else WHITE
        ram = 0.8 if N else 1.0
        if kind == "clear":
            if N:
                self.stars(n(14, 40))
                self.glow(cx, cy, cr, MOON, 0.10, 0.18)
            else:
                self.glow(cx, cy, cr, SUN, 0.26, 0.45)
                self.spots(cx, cy, cr)
                self.motes(n(6, 12), (255, 240, 200), 80)
        elif kind in ("fewclouds", "partly"):
            # güneş/ay açık kalsın: küçük, belirgin bulutlar arasında mavi gökyüzü görünsün
            few = kind == "fewclouds"
            if N:
                self.glow(cx, cy, cr, MOON, 0.10, 0.17)
            else:
                self.glow(cx, cy, cr, SUN, 0.24, 0.42)
                self.spots(cx, cy, cr, 0.8 if few else 0.6)
                self.motes(n(4 if few else 3, 8), (255, 240, 200), 75)
            self.clouds(1 if few else 2, 1 if few else 2, NCLOUD if N else WHITE, 135 if N else 150,
                        0.45, size=(0.22, 0.34) if few else (0.26, 0.4), blur=0.07, speed=1.3)
        elif kind == "overcast":
            self.clouds(3, 3, (100, 112, 140) if N else (228, 232, 240), 105, 0.6, size=(0.6, 0.95))
        elif kind == "showers":  # sağanak: hızlı koyu bulutlar, dalga dalga gelen eğik yoğun yağmur
            self.clouds(2, 2, (60, 68, 92) if N else (88, 98, 122), 130, 0.1, size=(0.55, 0.9), speed=2.4)
            self.rain(n({2: 22, 3: 32, 4: 44}.get(level, 32)), rainc, ram, slant=0.45, gust=True)
        elif kind in ("rain", "snow", "sleet", "hail", "storm"):
            dark = kind == "storm"
            cc = ((45, 50, 70) if N else (70, 78, 100)) if dark else (NCLOUD if N else (215, 222, 232))
            self.clouds(1, 2, cc, 115 if dark else 85, 0.12, size=(0.55, 0.9), speed=1.6 if dark else 1.0)
            nr = {2: 11, 3: 18, 4: 26}.get(4 if dark else level, 18)
            if kind in ("rain", "storm"):
                self.rain(n(nr), rainc, ram)
            elif kind == "sleet":
                self.rain(n(nr * 0.5), rainc, ram)
                self.snow(n(9, 40), (215, 228, 255) if N else WHITE)
            elif kind == "snow":
                self.snow(n(18, 80), (215, 228, 255) if N else WHITE)
            else:  # dolu
                self.rain(n(10), rainc, ram)
                self.pellets(n(8, 30))
            if dark:
                self.flash(95 if N else 70)
        elif kind in ("fog", "haze", "smoke"):
            col = {"fog": ((150, 165, 190), (225, 232, 240)), "haze": ((140, 150, 175), (240, 232, 210)),
                   "smoke": ((110, 100, 95), (175, 160, 140))}[kind][0 if N else 1]
            if kind == "haze":
                self.glow(cx, cy, cr, MOON if N else SUN, 0.12, 0.22)
                if not N:
                    self.spots(cx, cy, cr, 0.45)
            self.bands(4 if kind == "fog" else 3, col, 60 if kind == "fog" else 45)
        elif kind in ("wind", "dust"):
            if kind == "dust":
                self.streaks(n(9, 24), (205, 175, 125), 0.30 if N else 0.45)
                self.dust(n(26, 70), (210, 180, 130), 0.6 if N else 1.0)
            elif N:
                self.clouds(1, 2, NCLOUD, 65, 0.4, speed=3.0)
                self.streaks(n(9, 24), WHITE, 0.30)
            else:
                self.streaks(n(9, 24), WHITE, 0.45)
                self.leaves(n(4, 10))
        elif kind == "heat":
            if N:
                self.stars(n(8, 24))
                self.motes(n(5, 10), (255, 200, 150), 60)
            else:
                self.glow(cx, cy, cr, SUN, 0.3, 0.5)
                self.sunflare(cx, cy, cr, 1.1)
                self.spots(cx, cy, cr, 1.1)
                self.heatwaves(n(4, 8))
        elif kind == "frost":
            if N:
                self.stars(n(10, 30))
            else:
                self.glow(cx, cy, cr, (225, 240, 255), 0.10, 0.18)
            self.sparkles(n(8, 22))

    # -- yardımcılar
    def U(self, a, b):
        return self.rnd.uniform(a, b)

    def ink(self, col):
        """Açık temada açık renkler görünmez: efektler koyu mavi-gri çizilir."""
        return (35, 55, 85) if self.light else tuple(col)

    def draw_on(self, out, t):
        """Efektleri kartın RGB arka planına çizer ("RGBA" çizim kipi saydam şekilleri karıştırır).
        Ayrı bir saydam katmana çizip birleştirmekten ~2 ms hızlı."""
        d = ImageDraw.Draw(out, "RGBA")
        for fn, data in self.layers:
            fn(out, d, t, data)

    # -- katmanlar
    def clouds(self, far, near, col, alpha, y_hi, size=(0.5, 0.85), speed=1.0, blur=0.13):
        """Kümülüs biçimli yumuşak bulutlar, iki derinlikte (uzaktakiler soluk ve yavaş)."""
        w, h, S, U = self.w, self.h, self.S, self.U
        col = self.ink(col)
        puffs = [(0.0, 0.55, 1.0, 1.0), (0.08, 0.3, 0.45, 0.95), (0.28, 0.02, 0.72, 0.85),
                 (0.52, 0.18, 0.92, 0.92)]
        items = []
        for depth, cnt in ((0, far), (1, near)):
            for _ in range(cnt):
                cw_ = w * U(*size) * (0.8 if depth == 0 else 1.0)
                ch_ = cw_ * U(0.3, 0.4)
                a = int(alpha * (0.6 if depth == 0 else 1.0) * (0.45 if self.light else 1))
                spr = _soft_sprite(int(cw_), int(ch_), col, a, max(1.5, ch_ * blur),
                                   [(x1 * cw_, y1 * ch_, x2 * cw_, y2 * ch_) for x1, y1, x2, y2 in puffs])
                v = S * (U(4, 7) if depth == 0 else U(8, 14)) * speed
                items.append((spr, U(0, w + spr.width), U(-0.15, y_hi) * h, v))
        self.layers.append((self._d_clouds, items))

    def _d_clouds(self, out, d, t, items):
        w = self.w
        for spr, x0, y, v in items:
            _paste(out, spr, int((x0 + v * t) % (w + spr.width) - spr.width), int(y))

    def rain(self, n, col, am=1.0, slant=0.22, gust=False):
        w, h, S, U = self.w, self.h, self.S, self.U
        k = 1.5 if gust else 1.0  # sağanakta damlalar daha uzun ve hızlı
        items = [(U(0, w + h * slant), U(0, h), S * U(190, 280) * k, S * U(7, 13) * k,
                  int(U(55, 105) * am * (0.8 if self.light else 1))) for _ in range(int(n))]
        self.layers.append((self._d_rain, (items, self.ink(col), slant, gust)))

    def _d_rain(self, out, d, t, data):
        items, c, sl, gust = data
        w, h = self.w, self.h
        lw, span = max(1, round(self.S)), w + h * sl
        for x0, y0, v, L, a in items:
            y = (y0 + v * t) % (h + L)
            x = (x0 - sl * (y0 + v * t)) % span
            if gust:  # yağmur perdeleri: yoğunluk soldan sağa dalga halinde gelip geçer
                a = int(a * (0.35 + 0.65 * max(0.0, math.sin(t * 1.3 - x / w * 4))))
            d.line([(x + sl * L, y - L), (x, y)], fill=c + (a,), width=lw)

    def snow(self, n, col):
        w, h, S, U = self.w, self.h, self.S, self.U
        items = [(U(0, w), U(0, h), S * U(1.0, 2.2), S * U(16, 30), S * U(7, 14), U(0.6, 1.5),
                  U(0, 6.3), int(U(150, 220) * (0.7 if self.light else 1))) for _ in range(int(n))]
        self.layers.append((self._d_snow, (items, self.ink(col))))

    def _d_snow(self, out, d, t, data):
        items, c = data
        w, h = self.w, self.h
        for x0, y0, r, v, amp, f, ph, a in items:
            y = (y0 + v * t) % (h + 2 * r) - r
            x = (x0 + amp * math.sin(t * f + ph)) % w
            d.ellipse([x - r, y - r, x + r, y + r], fill=c + (a,))

    def pellets(self, n):
        """Dolu: hızlı düşen iri, parlak taneler."""
        w, h, S, U = self.w, self.h, self.S, self.U
        items = [(U(0, w), U(0, h), S * U(1.4, 2.4), S * U(280, 360), int(U(170, 230)))
                 for _ in range(int(n))]
        self.layers.append((self._d_pellets, (items, self.ink(self.WHITE))))

    def _d_pellets(self, out, d, t, data):
        items, c = data
        w, h = self.w, self.h
        for x0, y0, r, v, a in items:
            y = (y0 + v * t) % (h + 2 * r) - r
            x = (x0 - 0.1 * (y0 + v * t)) % w
            d.ellipse([x - r, y - r, x + r, y + r], fill=c + (a,))

    def stars(self, n):
        w, h, S, U = self.w, self.h, self.S, self.U
        items = [(U(0, w), U(0, min(h * 0.75, S * 150)), S * U(0.6, 1.3), U(0.6, 1.8), U(0, 6.3),
                  U(20, 50), U(150, 230)) for _ in range(int(n))]
        shoot = (U(0, 11), U(0.1, 0.5), U(0.05, 0.3))  # kayan yıldız: faz, başlangıç
        self.layers.append((self._d_stars, (items, shoot, self.ink(self.WHITE))))

    def _d_stars(self, out, d, t, data):
        items, (ph, sx, sy), c = data
        w, h, S = self.w, self.h, self.S
        for x, y, r, f, ph_, lo, hi in items:
            a = int(lo + (hi - lo) * (0.5 + 0.5 * math.sin(t * f + ph_)))
            d.ellipse([x - r, y - r, x + r, y + r], fill=c + (a,))
        p = (t + ph) % 11.0
        if p < 0.8:  # 11 saniyede bir kayan yıldız
            q = p / 0.8
            x1, y1 = w * (sx + 0.45 * q), h * (sy + 0.16 * q)
            for j in range(6):
                f_ = j / 6
                d.line([(x1 - w * 0.05 * (1 - f_) - w * 0.05, y1 - h * 0.02 * (1 - f_) - h * 0.02),
                        (x1 - w * 0.05 * (1 - f_), y1 - h * 0.02 * (1 - f_))],
                       fill=c + (int(220 * f_ * math.sin(q * math.pi)),), width=max(1, round(S)))

    def glow(self, cx, cy, r, col, lo, hi):
        """Güneşin / ayın arkasında nefes alır gibi parlayan ışık."""
        R = int(r * 1.9)
        col = (120, 90, 30) if self.light else tuple(col)
        A = _soft_sprite(2 * R, 2 * R, col, 255, R * 0.35,
                         [(R * 0.35, R * 0.35, R * 1.65, R * 1.65)]).getchannel("A")
        A, pos = _clip_mask(A, int(cx - A.width / 2), int(cy - A.height / 2), self.w, self.h)
        self.layers.append((self._d_glow, (A, col, lo, hi, pos)))

    def _d_glow(self, out, d, t, data):
        A, col, lo, hi, pos = data
        z = lo + (hi - lo) * (0.5 - 0.5 * math.cos(t / 2.8 * 2 * math.pi))  # 2,8 sn'de bir nefes
        _paste_lit(out, col, A, pos, z)

    def sunflare(self, cx, cy, r, s=1.0):
        """Güneşin çevresinde nabız gibi atan ince hale halkası (yalnızca çok sıcak günlerde)."""
        if self.light:  # açık temada sarı ışık zemini kirletir
            return
        col = (255, 244, 210)  # sarı mavi zeminde griye döner: neredeyse beyaz, sıcak bir ışık
        q = int(r * 0.95)
        th = max(2, int(r * 0.09))
        ring = Image.new("L", (2 * q + 8 * th, 2 * q + 8 * th), 0)
        o = 4 * th
        ImageDraw.Draw(ring).ellipse([o, o, o + 2 * q, o + 2 * q], outline=255, width=th)
        ring = ring.filter(ImageFilter.GaussianBlur(th * 0.8))
        ring, pos = _clip_mask(ring, int(cx) - ring.width // 2, int(cy) - ring.height // 2, self.w, self.h)
        self.layers.append((self._d_sunflare, (ring, col, s, pos)))

    def spots(self, cx, cy, r, s=1.0):
        """Güneşten kartın içine doğru dizilen, hafifçe süzülüp parlayan soluk ışık lekeleri."""
        if self.light:
            return
        dx, dy = self.w * 0.62 - cx, self.h * 0.62 - cy
        ghosts = []
        for f, rr, col, a in ((0.45, 0.16, (255, 225, 150), 0.55), (0.8, 0.3, (190, 225, 255), 0.30),
                              (1.15, 0.1, (255, 210, 120), 0.65), (1.5, 0.45, (255, 240, 205), 0.22)):
            q = int(r * rr) + 1
            A = _soft_sprite(2 * q, 2 * q, col, 255, q * 0.35, [(0, 0, 2 * q, 2 * q)]).getchannel("A")
            ghosts.append((f, A, col, a * s))
        self.layers.append((self._d_spots, (ghosts, (cx, cy, dx, dy))))

    def _d_spots(self, out, d, t, data):
        ghosts, (cx, cy, dx, dy) = data
        for i, (f, A, col, a) in enumerate(ghosts):
            g = f + 0.06 * math.sin(t * 0.45 + i * 1.3)
            z = a * (0.4 + 0.6 * (0.5 + 0.5 * math.sin(t * 0.8 + i * 1.7)))
            _paste_lit(out, col, A, (int(cx + dx * g - A.width / 2), int(cy + dy * g - A.height / 2)), z)

    def _d_sunflare(self, out, d, t, data):
        ring, col, s, pos = data
        z = s * (0.25 + 0.5 * (0.5 - 0.5 * math.cos(t / 3.2 * 2 * math.pi)))  # 3,2 sn'de bir nabız
        _paste_lit(out, col, ring, pos, z)

    def motes(self, n, col, amax):
        """Aşağıdan yukarı süzülen, sönerek kaybolan ışık zerreleri."""
        w, S, U = self.w, self.S, self.U
        items = [(U(0, w), U(0, 1), S * U(5, 10), S * U(1.2, 2.4), U(0.6, 1.4), U(0, 6.3))
                 for _ in range(int(n))]
        self.layers.append((self._d_motes, (items, (120, 90, 20) if self.light else col, amax)))

    def _d_motes(self, out, d, t, data):
        items, c, amax = data
        h, S = self.h, self.S
        for x0, p0, v, r, f, ph in items:
            life = (p0 + t * v / max(1, h)) % 1.0
            y = h * (1 - life)
            x = x0 + S * 6 * math.sin(t * f + ph)
            a = int(amax * math.sin(life * math.pi))
            d.ellipse([x - r, y - r, x + r, y + r], fill=c + (a,))

    def bands(self, n, col, alpha):
        """Sis / pus / duman: yavaşça dalgalanan yumuşak bantlar."""
        w, h, S, U = self.w, self.h, self.S, self.U
        items = []
        for i in range(n):
            bw, bh = w * 1.6, h * U(0.16, 0.26)
            spr = _soft_sprite(int(bw), int(bh), self.ink(col),
                               int(alpha * U(0.8, 1.15) * (0.4 if self.light else 1)),
                               max(2, S * 7), [(0, 0, bw, bh)])
            yc = h * (0.12 + 0.8 * i / max(1, n - 1))
            items.append((spr, yc - spr.height / 2, w * U(0.15, 0.3), U(0.18, 0.35), U(0, 6.3)))
        self.layers.append((self._d_bands, items))

    def _d_bands(self, out, d, t, items):
        w = self.w
        for spr, y, amp, f, ph in items:
            _paste(out, spr, int(-w * 0.3 + amp * math.sin(t * f + ph)), int(y))

    def streaks(self, n, col, am):
        """Rüzgar: hızla geçen, iki ucu solan ince çizgiler."""
        w, h, S, U = self.w, self.h, self.S, self.U
        c = self.ink(col)
        items = []
        for _ in range(int(n)):
            L = int(S * U(30, 60))
            a = Image.linear_gradient("L").rotate(90).resize((L, max(1, round(S)))).point(
                lambda v: int(min(v, 255 - v) * 2 * am * (0.8 if self.light else 1)))
            spr = Image.new("RGBA", a.size, c + (0,))
            spr.putalpha(a)
            items.append((spr, U(0, w + L), U(0.08, 0.92) * h, S * U(140, 210), U(0, 6.3)))
        self.layers.append((self._d_streaks, items))

    def _d_streaks(self, out, d, t, items):
        w, S = self.w, self.S
        for spr, x0, y0, v, ph in items:
            x = (x0 + v * t) % (w + spr.width) - spr.width
            _paste(out, spr, int(x), int(y0 + S * 3 * math.sin(t * 1.3 + ph)))

    def dust(self, n, col, am):
        """Toz fırtınası: hızla savrulan küçük taneler."""
        w, h, S, U = self.w, self.h, self.S, self.U
        items = [(U(0, w), U(0, h), S * U(0.6, 1.3), S * U(60, 120), S * U(2, 6), U(1, 3), U(0, 6.3),
                  int(U(60, 140) * am)) for _ in range(int(n))]
        self.layers.append((self._d_dust, (items, self.ink(col))))

    def _d_dust(self, out, d, t, data):
        items, c = data
        w = self.w
        for x0, y0, r, v, amp, f, ph, a in items:
            x = (x0 + v * t) % w
            y = y0 + amp * math.sin(t * f + ph)
            d.ellipse([x - r, y - r, x + r, y + r], fill=c + (a,))

    def leaves(self, n):
        """Rüzgarlı gündüz: dönerek uçuşan yapraklar."""
        w, h, S, U = self.w, self.h, self.S, self.U
        cols = [(196, 160, 72), (150, 170, 80), (205, 120, 60)]
        items = [(U(0, w), U(0.15, 0.85) * h, S * U(2.2, 3.4), S * U(50, 90), S * U(6, 14), U(0.8, 1.6),
                  U(0, 6.3), U(2, 4), self.ink(cols[i % 3])) for i in range(int(n))]
        self.layers.append((self._d_leaves, items))

    def _d_leaves(self, out, d, t, items):
        w, S = self.w, self.S
        for x0, y0, r, v, amp, f, ph, spin, c in items:
            x = (x0 + v * t) % (w + 10 * S) - 5 * S
            y = y0 + amp * math.sin(t * f + ph)
            rx = r * abs(math.cos(t * spin + ph)) + 0.5 * S
            d.ellipse([x - rx, y - r * 0.55, x + rx, y + r * 0.55], fill=c + (170,))

    def heatwaves(self, n):
        """Sıcak gündüz: yukarı doğru yükselen, dalgalı sıcak hava çizgileri."""
        U = self.U
        items = [(U(0, 1), U(0, 6.3)) for _ in range(int(n))]
        self.layers.append((self._d_heat, items))

    def _d_heat(self, out, d, t, items):
        w, h, S = self.w, self.h, self.S
        c = (120, 90, 30) if self.light else (255, 230, 190)
        step = max(3, int(6 * S))
        for p0, ph in items:
            life = (p0 + t * 0.12) % 1.0
            y = h * (0.95 - 0.7 * life)
            a = int(55 * math.sin(life * math.pi))
            pts = [(x, y + 2 * S * math.sin(x * 0.06 / S + t * 3 + ph)) for x in range(0, w + step, step)]
            d.line(pts, fill=c + (a,), width=max(1, round(S)))

    def sparkles(self, n):
        """Soğuk hava: ara sıra parlayıp sönen kırağı pırıltıları."""
        w, h, S, U = self.w, self.h, self.S, self.U
        items = [(U(0, w), U(0, h), S * U(2, 4), U(0.25, 0.6), U(0, 1)) for _ in range(int(n))]
        self.layers.append((self._d_sparkles, (items, self.ink((220, 235, 255)))))

    def _d_sparkles(self, out, d, t, data):
        items, c = data
        lw = max(1, round(self.S))
        for x, y, s, f, ph in items:
            life = (t * f + ph) % 1.0
            k = max(0.0, math.sin(life * math.pi)) ** 3
            if k < 0.05:
                continue
            L = s * k
            a = int(230 * k)
            d.line([(x - L, y), (x + L, y)], fill=c + (a,), width=lw)
            d.line([(x, y - L), (x, y + L)], fill=c + (a,), width=lw)

    def flash(self, alpha):
        self.layers.append((self._d_flash, Image.new("RGBA", (self.w, self.h), (255, 255, 255, alpha))))

    def _d_flash(self, out, d, t, img):
        p = t % 6.5  # şimşek: kısa, iki çakışlı
        if p < 0.07 or 0.16 < p < 0.22:
            out.alpha_composite(img)


def loc_title(loc):
    if loc.get("name"):  # kullanıcının verdiği özel ad
        return loc["name"]
    il, ilce = loc.get("il", ""), loc.get("ilce", "")
    return il if not ilce or ilce in (il, "Merkez") else f"{ilce}, {il}"


def loc_short(loc):
    il, ilce = loc.get("il", ""), loc.get("ilce", "")
    return loc.get("name") or (il if not ilce or ilce in (il, "Merkez") else ilce)


def data_tz(data):
    """Verinin saat dilimi: yurt dışı şehirlerde şehrin kendi saati, yoksa sistem saati."""
    off = (data or {}).get("utc_offset")
    return timezone(timedelta(seconds=off)) if off is not None else None


def loc_key(loc):
    return str(loc["merkezId"])


# --------------------------------------------------------------------------- olay günlüğü

EVENT_MAX = 500          # dosyada tutulan en fazla kayıt
EVENT_TEMP_DELTA = 5.0   # °C: en çok 3 saat içinde bu kadar değişim olay sayılır
EVENT_UV, EVENT_WIND = 8.0, 60.0  # UV indeksi, km/sa


def event_text(e):
    """Olayın kısa metni (sıcaklık ve rüzgar, seçili birimle)."""
    k = e.get("kind")
    if k == "rain_start":
        return T("Yağış başladı")
    if k == "rain_end":
        return T("Yağış bitti")
    if k == "temp":
        a, b = e.get("a", 0), e.get("b", 0)
        d = abs(tconv(b) - tconv(a))
        return T('{:.0f}° {} ({} sa)').format(d, T('yükseldi') if b > a else T('düştü'), e.get('h', 1))
    if k == "uv":
        return T('UV {:.0f} (çok yüksek)').format(e.get('a', 0))
    if k == "wind":
        return T("Rüzgar ") + fmt_wind(e.get("a", 0))
    if k == "alert":
        name = ALERT_COLORS.get(e.get("color"), ("",))[0]
        what = ", ".join(ALERT_KINDS.get(w, w) for w in e["what"].split(", ")) if e.get("what") else ""
        return T('{} uyarı').format(name) + (f": {what}" if what else "")
    return e.get("what") or k or ""


class EventLog:
    """Hava olaylarının günlüğü (events.json). Olay, iki ölçüm arasındaki DEĞİŞİMDİR; bu yüzden
    şehir başına son durum da dosyada saklanır (yeniden başlatınca aynı olay tekrar yazılmaz).
    Bir şehir ilk kez görüldüğünde yalnızca durumu kaydedilir, olay üretilmez."""

    def __init__(self, path):
        self.path = path
        d = load_json(path, {})
        d = d if isinstance(d, dict) else {}
        self.events = [e for e in (d.get("events") or []) if isinstance(e, dict) and e.get("t")]
        self.state = d.get("state") if isinstance(d.get("state"), dict) else {}

    def save(self):
        del self.events[:-EVENT_MAX]
        save_json(self.path, {"events": self.events, "state": self.state})  # atomik: tmp + replace

    def clear(self):
        self.events = []
        self.save()

    def _add(self, loc, kind, now, **kw):
        e = {"t": now.strftime("%Y-%m-%dT%H:%M:%S.000Z"), "key": loc_key(loc),
             "city": loc_short(loc), "kind": kind}
        e.update(kw)
        self.events.append(e)
        return e

    def recent(self, hours=24, now=None):
        """Son `hours` saatin olayları, en yeni başta."""
        cut = (now or datetime.now(timezone.utc)) - timedelta(hours=hours)
        out = []
        for e in reversed(self.events):
            try:
                if parse_utc(e["t"]) >= cut:
                    out.append(e)
            except Exception:
                continue
        return out

    def observe(self, loc, data, now=None):
        """Yeni ölçümü önceki durumla karşılaştırır; oluşan olayları döndürür."""
        cur = (data or {}).get("cur")
        if not cur:
            return []
        now = now or datetime.now(timezone.utc)
        ts = now.timestamp()
        k = loc_key(loc)
        st = self.state.get(k)
        first = not isinstance(st, dict)
        st = {} if first else st
        code = cur.get("hadiseKodu")
        wet = code in WET
        tv, rz = cur.get("sicaklik"), cur.get("ruzgarHiz")
        uv = ((data.get("extra") or {}).get("uv_now"))
        hi_uv = uv is not None and uv >= EVENT_UV
        hi_w = ok(rz) and rz >= EVENT_WIND
        new = []
        if not first:
            if wet and not st.get("wet"):
                new.append(self._add(loc, "rain_start", now, code=code))
            elif not wet and st.get("wet"):
                new.append(self._add(loc, "rain_end", now, code=code))
            ref, ref_ts = st.get("t_ref"), st.get("t_ref_ts", 0)
            if ok(tv) and ref is not None and abs(tv - ref) >= EVENT_TEMP_DELTA:
                new.append(self._add(loc, "temp", now, a=ref, b=tv,
                                     h=max(1, round((ts - ref_ts) / 3600)),
                                     code="SCK" if tv > ref else "SGK"))
                st["t_ref"] = None  # yeni ölçümden itibaren yeniden say
            if hi_uv and not st.get("uv"):
                new.append(self._add(loc, "uv", now, a=uv, code="SCK"))
            if hi_w and not st.get("wind"):
                new.append(self._add(loc, "wind", now, a=rz, code="R"))
        st["wet"], st["uv"], st["wind"] = wet, hi_uv, hi_w
        # sıcaklık referansı: 3 saatten eskiyse yenilenir (kayan pencere)
        if ok(tv) and (st.get("t_ref") is None or ts - st.get("t_ref_ts", 0) > 3 * 3600):
            st["t_ref"], st["t_ref_ts"] = tv, ts
        self.state[k] = st
        return new

    def observe_alerts(self, loc, alerts, now=None):
        """Bu şehir için ilk kez görülen MGM uyarılarını günlüğe yazar."""
        now = now or datetime.now(timezone.utc)
        k = loc_key(loc)
        st = self.state.setdefault(k, {})
        seen = list(st.get("alerts") or [])
        new = []
        for a in alerts_for(loc, alerts, now):
            sid = str(a.get("no"))
            if sid not in seen:
                seen.append(sid)
                new.append(self._add(loc, "alert", now, color=a["color"],
                                     what=", ".join(a["codes"])))  # kod: gösterirken o anki dile çevrilir
        st["alerts"] = seen[-30:]
        return new


# --------------------------------------------------------------------------- pencereler

class CityManager(tk.Toplevel):
    """Şehir listesi: ekle / kaldır / sırala / adlandır / göster."""

    def __init__(self, app):
        super().__init__(app.root)
        self.app = app
        self.title(T("Şehirler"))
        self.attributes("-topmost", True)
        self.resizable(False, False)
        self.iller, self.ilceler = [], []
        self.il_names, self.ilce_names = [], []
        self.om_results = []

        frm = ttk.Frame(self, padding=14)
        frm.pack(fill="both", expand=True)
        ttk.Label(frm, text=T("Şehirlerim")).grid(row=0, column=0, sticky="w")
        self.lb = tk.Listbox(frm, height=7, width=36, activestyle="none", exportselection=False)
        self.lb.grid(row=1, column=0, rowspan=5, sticky="nsew", pady=(2, 0))
        for r, (txt, cmd) in enumerate(((T("Göster"), self.show), (T("Ad ver…"), self.rename),
                                        (T("Yukarı"), lambda: self.move(-1)),
                                        (T("Aşağı"), lambda: self.move(1)), (T("Kaldır"), self.remove))):
            ttk.Button(frm, text=txt, command=cmd).grid(row=1 + r, column=1, sticky="ew",
                                                        padx=(8, 0), pady=(2, 0))
        ttk.Separator(frm).grid(row=6, column=0, columnspan=2, sticky="ew", pady=10)

        ttk.Label(frm, text=T("Türkiye'den ekle (MGM) — İl")).grid(row=7, column=0, sticky="w")
        self.il_cb = ttk.Combobox(frm, width=30)
        self.il_cb.grid(row=8, column=0, columnspan=2, sticky="we", pady=(2, 6))
        ttk.Label(frm, text=T("İlçe")).grid(row=9, column=0, sticky="w")
        self.ilce_cb = ttk.Combobox(frm, width=30)
        self.ilce_cb.grid(row=10, column=0, sticky="we", pady=(2, 0))
        self.add_btn = ttk.Button(frm, text=T("Ekle"), command=self.add, state="disabled")
        self.add_btn.grid(row=10, column=1, sticky="ew", padx=(8, 0))
        ttk.Separator(frm).grid(row=11, column=0, columnspan=2, sticky="ew", pady=10)

        ttk.Label(frm, text=T("Yurt dışından ekle (Open-Meteo) — şehir adı")).grid(
            row=12, column=0, columnspan=2, sticky="w")
        self.om_entry = ttk.Entry(frm)
        self.om_entry.grid(row=13, column=0, sticky="we", pady=(2, 6))
        ttk.Button(frm, text=T("Ara", "Search"), command=self.om_find).grid(row=13, column=1, sticky="ew",
                                                              padx=(8, 0), pady=(2, 6))
        self.om_cb = ttk.Combobox(frm, state="readonly")
        self.om_cb.grid(row=14, column=0, sticky="we")
        self.om_btn = ttk.Button(frm, text=T("Ekle"), command=self.om_add, state="disabled")
        self.om_btn.grid(row=14, column=1, sticky="ew", padx=(8, 0))

        self.status = ttk.Label(frm, text=T("İller yükleniyor…"), foreground="#666")
        self.status.grid(row=15, column=0, sticky="w", pady=(12, 0))
        ttk.Button(frm, text=T("Kapat"), command=self.destroy).grid(row=15, column=1, sticky="ew",
                                                                padx=(8, 0), pady=(12, 0))
        frm.columnconfigure(0, weight=1)

        self.lb.bind("<Double-Button-1>", lambda e: self.show())
        self.lb.bind("<Delete>", lambda e: self.remove())
        self.il_cb.bind("<<ComboboxSelected>>", lambda e: self.on_il())
        self.il_cb.bind("<Return>", lambda e: self.on_il())
        self.il_cb.bind("<KeyRelease>", lambda e: self.filter(e, self.il_cb, self.il_names))
        self.ilce_cb.bind("<KeyRelease>", lambda e: self.filter(e, self.ilce_cb, self.ilce_names))
        self.ilce_cb.bind("<Return>", lambda e: self.add())
        self.om_entry.bind("<Return>", lambda e: self.om_find())
        self.bind("<Escape>", lambda e: self.destroy())

        self.refresh_list()
        self.update_idletasks()
        x = min(max(0, app.x), self.winfo_screenwidth() - self.winfo_width() - 20)
        y = min(max(0, app.y + 40), self.winfo_screenheight() - self.winfo_height() - 80)
        self.geometry(f"+{x}+{y}")
        self.focus_force()
        app.run_bg(lambda: api("/merkezler/iller"), self.got_iller, self.failed)

    # ---- liste
    def refresh_list(self, sel=None):
        self.lb.delete(0, "end")
        for loc in self.app.locs:
            self.lb.insert("end", loc_title(loc) + (T("  (yurt dışı)") if loc.get("src") == "om" else ""))
        sel = self.app.idx if sel is None else sel
        if self.app.locs:
            sel = max(0, min(sel, len(self.app.locs) - 1))
            self.lb.selection_set(sel)
            self.lb.see(sel)

    def selected(self):
        s = self.lb.curselection()
        return s[0] if s else None

    def show(self):
        i = self.selected()
        if i is not None:
            self.app.switch_to(i, user=True)

    def rename(self):
        """Şehre widget'ta görünecek özel ad ver (her dil/alfabe olur; boş = özgün ad)."""
        i = self.selected()
        if i is None:
            return
        loc = self.app.locs[i]
        name = simpledialog.askstring(
            T("Ad ver"), T('{} için görünen ad\n(boş bırakırsan özgün adı kullanılır):').format(loc_title(dict(loc, name=''))),
            initialvalue=loc.get("name", ""), parent=self)
        if name is None:
            return
        self.app.rename_location(i, name.strip())
        self.refresh_list(i)
        self.say(T("Ad güncellendi."))

    def move(self, delta):
        i = self.selected()
        if i is None:
            return
        j = i + delta
        if 0 <= j < len(self.app.locs):
            self.app.move_location(i, j)
            self.refresh_list(j)

    def remove(self):
        i = self.selected()
        if i is None:
            return
        if len(self.app.locs) <= 1:
            self.say(T("En az bir şehir kalmalı."), err=True)
            return
        name = loc_title(self.app.locs[i])
        self.app.remove_location(i)
        self.refresh_list(min(i, len(self.app.locs) - 1))
        self.say(T('{} kaldırıldı.').format(name))

    def say(self, text, err=False):
        if self.winfo_exists():
            self.status.config(text=text, foreground="#b00" if err else "#666")

    def failed(self, ex):
        self.say(T('Bağlantı hatası: {}').format(ex)[:60], err=True)

    def _add(self, loc):
        if any(loc_key(l) == loc_key(loc) for l in self.app.locs):
            self.say(T('{} zaten listede.').format(loc_title(loc)), err=True)
            return
        self.app.add_location(loc)
        self.refresh_list(self.app.idx)
        self.say(T('{} eklendi.').format(loc_title(loc)))

    # ---- Türkiye: il / ilçe
    @staticmethod
    def filter(e, cb, names):
        if e.keysym in ("Return", "Up", "Down", "Escape", "Tab"):
            return
        q = tr_lower(cb.get().strip())
        cb["values"] = [n for n in names if tr_lower(n).startswith(q)] if q else names

    @staticmethod
    def match(text, names):
        q = tr_lower(text.strip())
        for n in names:
            if tr_lower(n) == q:
                return n
        hits = [n for n in names if tr_lower(n).startswith(q)]
        return hits[0] if hits else None

    def got_iller(self, data):
        if not self.winfo_exists():
            return
        self.iller = data
        self.il_names = sorted({e["il"] for e in data}, key=tr_key)
        self.il_cb["values"] = self.il_names
        self.say(T("Eklemek için il seçin ya da şehir arayın."))
        (self.il_cb if LANG == "tr" else self.om_entry).focus_set()

    def on_il(self):
        il = self.match(self.il_cb.get(), self.il_names)
        if not il:
            return
        self.il_cb.set(il)
        self.il_cb["values"] = self.il_names
        self.ilce_cb.set("")
        self.ilce_cb["values"] = []
        self.add_btn.config(state="disabled")
        self.say(T('{} ilçeleri yükleniyor…').format(il))

        def done(data):
            if not self.winfo_exists() or self.il_cb.get() != il:
                return
            self.ilceler = data
            self.ilce_names = sorted({e["ilce"] for e in data if e.get("ilce")}, key=tr_key)
            self.ilce_cb["values"] = self.ilce_names
            center = next((e for e in self.iller if e["il"] == il), None)
            pick = center["ilce"] if center and center.get("ilce") in self.ilce_names \
                else (self.ilce_names[0] if self.ilce_names else "")
            self.ilce_cb.set(pick)
            self.add_btn.config(state="normal")
            self.say(T("{} ilçe — seçip Ekle'ye basın.").format(len(self.ilce_names)))
            self.ilce_cb.focus_set()

        self.app.run_bg(lambda: api("/merkezler/ililcesi", il=il), done, self.failed)

    def add(self):
        ilce = self.match(self.ilce_cb.get(), self.ilce_names)
        entry = next((e for e in self.ilceler if e.get("ilce") == ilce), None)
        if not entry:
            self.say(T("Listeden bir ilçe seçin."), err=True)
            return
        self._add(loc_from_entry(entry))

    # ---- yurt dışı: Open-Meteo araması
    def om_find(self):
        q = self.om_entry.get().strip()
        if len(q) < 2:
            self.say(T("En az 2 harf yazın."), err=True)
            return
        self.say(T('“{}” aranıyor…').format(q))
        self.om_btn.config(state="disabled")

        def done(res):
            if not self.winfo_exists():
                return
            self.om_results = res
            labels = [", ".join(x for x in (r["ilce"], r.get("admin"), r["il"]) if x) for r in res]
            self.om_cb["values"] = labels
            if res:
                self.om_cb.current(0)
                self.om_btn.config(state="normal")
                self.say(T("{} sonuç — seçip Ekle'ye basın.").format(len(res)))
            else:
                self.om_cb.set("")
                self.say(T("Sonuç bulunamadı."), err=True)

        self.app.run_bg(lambda: om_search(q), done, self.failed)

    def om_add(self):
        i = self.om_cb.current()
        if 0 <= i < len(self.om_results):
            self._add(dict(self.om_results[i]))


class ThresholdDialog(tk.Toplevel):
    """Sıcaklık eşiği bildirimleri: alt ve üst eşik (boş = kapalı)."""

    def __init__(self, app):
        super().__init__(app.root)
        self.app = app
        self.title(T("Sıcaklık eşikleri"))
        self.attributes("-topmost", True)
        self.resizable(False, False)
        unit = "°F" if UNITS["temp"] == "F" else "°C"
        frm = ttk.Frame(self, padding=14)
        frm.pack()
        ttk.Label(frm, text=T("Sıcaklık bu değerlere ulaşınca bildirim gelir.\n"
                            "Kapatmak için kutuyu boş bırak.")).grid(row=0, column=0, columnspan=2,
                                                                  sticky="w", pady=(0, 10))
        self.vars = {}
        for r, (key, label) in enumerate((("alert_low", T('Alt eşik ({}) — şu değere düşünce').format(unit)),
                                          ("alert_high", T('Üst eşik ({}) — şu değere çıkınca').format(unit)))):
            ttk.Label(frm, text=label).grid(row=1 + r, column=0, sticky="w", pady=3)
            v = app.cfg.get(key)
            var = tk.StringVar(value="" if v is None else f"{tconv(v):g}")
            ttk.Entry(frm, textvariable=var, width=8).grid(row=1 + r, column=1, padx=(10, 0))
            self.vars[key] = var
        self.msg = ttk.Label(frm, text="", foreground="#b00")
        self.msg.grid(row=3, column=0, sticky="w", pady=(8, 0))
        ttk.Button(frm, text=T("Kaydet"), command=self.save).grid(row=3, column=1, sticky="e",
                                                               pady=(8, 0))
        self.bind("<Return>", lambda e: self.save())
        self.bind("<Escape>", lambda e: self.destroy())
        self.update_idletasks()
        self.geometry(f"+{max(0, app.x)}+{max(0, app.y + 40)}")
        self.focus_force()

    def save(self):
        out = {}
        for key, var in self.vars.items():
            s = var.get().strip().replace(",", ".")
            if not s:
                out[key] = None
                continue
            try:
                v = float(s)
            except ValueError:
                self.msg.config(text=T("Sayı girin (ör. 2 ya da 35.5)."))
                return
            out[key] = (v - 32) * 5 / 9 if UNITS["temp"] == "F" else v  # içeride hep °C
        self.app.cfg.update(out)
        self.app.thr_state.clear()
        self.app.save_cfg()
        self.app.check_notifications()
        self.destroy()


class TimesDialog(tk.Toplevel):
    """Sabah özeti saati ve sessiz saatler."""

    def __init__(self, app):
        super().__init__(app.root)
        self.app = app
        self.title(T("Bildirim saatleri"))
        self.attributes("-topmost", True)
        self.resizable(False, False)
        c = app.cfg
        frm = ttk.Frame(self, padding=14)
        frm.pack()
        ttk.Label(frm, text=T("Sabah özeti / hatırlatma saati (SS:DD)")).grid(row=0, column=0, sticky="w", pady=3)
        self.morning = tk.StringVar(value=c.get("morning_time", "07:30"))
        ttk.Entry(frm, textvariable=self.morning, width=7).grid(row=0, column=1, padx=(10, 0))
        ttk.Separator(frm).grid(row=1, column=0, columnspan=2, sticky="ew", pady=10)
        self.quiet_on = tk.BooleanVar(value=bool(c.get("quiet_on")))
        ttk.Checkbutton(frm, text=T("Sessiz saatler (bu aralıkta bildirim gelmez)"),
                        variable=self.quiet_on).grid(row=2, column=0, columnspan=2, sticky="w")
        ttk.Label(frm, text=T("Başlangıç (SS:DD)")).grid(row=3, column=0, sticky="w", pady=3)
        self.q1 = tk.StringVar(value=c.get("quiet_start", "23:00"))
        ttk.Entry(frm, textvariable=self.q1, width=7).grid(row=3, column=1, padx=(10, 0))
        ttk.Label(frm, text=T("Bitiş (SS:DD)")).grid(row=4, column=0, sticky="w", pady=3)
        self.q2 = tk.StringVar(value=c.get("quiet_end", "07:00"))
        ttk.Entry(frm, textvariable=self.q2, width=7).grid(row=4, column=1, padx=(10, 0))
        ttk.Label(frm, text=T("Sessiz saatlerde gelmeyen uyarılar, sessizlik bitince gösterilir."),
                  foreground="#666").grid(row=5, column=0, columnspan=2, sticky="w", pady=(6, 0))
        self.msg = ttk.Label(frm, text="", foreground="#b00")
        self.msg.grid(row=6, column=0, sticky="w", pady=(8, 0))
        ttk.Button(frm, text=T("Kaydet"), command=self.save).grid(row=6, column=1, sticky="e", pady=(8, 0))
        self.bind("<Return>", lambda e: self.save())
        self.bind("<Escape>", lambda e: self.destroy())
        self.update_idletasks()
        self.geometry(f"+{max(0, app.x)}+{max(0, app.y + 40)}")
        self.focus_force()

    def save(self):
        vals = {}
        for key, var in (("morning_time", self.morning), ("quiet_start", self.q1),
                         ("quiet_end", self.q2)):
            hm = parse_hhmm(var.get())
            if not hm:
                self.msg.config(text=T("Saatleri SS:DD biçiminde girin (ör. 07:30)."))
                return
            vals[key] = f"{hm[0]:02d}:{hm[1]:02d}"
        vals["quiet_on"] = self.quiet_on.get()
        if vals["morning_time"] != self.app.cfg.get("morning_time"):
            vals["last_morning"] = ""  # yeni saat bugün henüz geçmediyse bugün de gelsin
        self.app.cfg.update(vals)
        self.app.save_cfg()
        self.destroy()


RADAR_URL = "https://www.mgm.gov.tr/FTPDATA/uzal/radar/comp/compppi{}.jpg"
RADAR_FRAMES = 15  # 1 = en eski, 15 = en yeni (yaklaşık 6 dk arayla)


def fetch_radar_frame(n):
    req = urllib.request.Request(RADAR_URL.format(n), headers={
        "Referer": "https://www.mgm.gov.tr/", "User-Agent": HEADERS["User-Agent"]})
    with urllib.request.urlopen(req, timeout=20) as r:
        b = r.read()
    if b[:2] != b"\xff\xd8":
        raise ValueError(T("radar görüntüsü alınamadı"))
    return Image.open(io.BytesIO(b)).convert("RGB")


# Radar görüntüsünde (880x720) coğrafi konum -> piksel. 27 il işaretinin konumuna en küçük
# karelerle uydurulmuş 2. derece polinom (u = boylam-35, v = enlem-39); ortalama hata ~1 px,
# uydurmaya katılmayan illerde en fazla ~3 px.
RADAR_CX = [422.19135, 35.20408, -0.07062, -7e-05, 0.00192, 0.05927]
RADAR_CY = [292.43477, -0.03726, -45.45515, 0.00719, 0.01528, -0.44161]
RADAR_MAP = (0, 0, 880, 552)       # haritanın kapladığı alan (altı: renk cetveli ve saat)
RADAR_TIME_BOX = (572, 672, 732, 720)  # görüntünün sağ altındaki saat/tarih kutusu
RADAR_CROP = (264, 216)            # "Şehrim" görünümünde kırpılan alan (880:720 oranında)


def radar_xy(lat, lon):
    u, v = lon - 35.0, lat - 39.0
    f = (1.0, u, v, u * v, u * u, v * v)
    return (sum(a * c for a, c in zip(f, RADAR_CX)), sum(a * c for a, c in zip(f, RADAR_CY)))


def radar_city_box(lat, lon):
    """Şehrin çevresini kapsayan kırpma kutusu ve şehrin kutu içindeki konumu; şehir radar
    haritasının dışındaysa (yurt dışı) None."""
    x, y = radar_xy(lat, lon)
    x0, y0, x1, y1 = RADAR_MAP
    if not (x0 + 10 <= x <= x1 - 10 and y0 + 10 <= y <= y1 - 10):
        return None
    cw, ch = RADAR_CROP
    bx = min(max(x0, x - cw / 2), x1 - cw)
    by = min(max(y0, y - ch / 2), y1 - ch)
    return (int(bx), int(by), int(bx) + cw, int(by) + ch), (x - int(bx), y - int(by))


class RadarWindow(tk.Toplevel):
    """MGM Türkiye radar kompozitinin son 15 görüntüsünü oynatır. "Şehrim" görünümünde
    widget'ta seçili şehrin çevresi büyütülür; şehir değişince görünüm de değişir."""

    def __init__(self, app):
        super().__init__(app.root)
        self.app = app
        self.title(T("Radar — MGM"))
        self.attributes("-topmost", True)
        self.resizable(False, False)
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        f = min(1.0, sw * 0.6 / 880, sh * 0.72 / 720)
        self.size = (int(880 * f), int(720 * f))
        self.blank = tk.PhotoImage(width=self.size[0], height=self.size[1])
        self.view = tk.Label(self, image=self.blank, bg="#111", bd=0)
        self.view.pack()
        bar = ttk.Frame(self, padding=(10, 6))
        bar.pack(fill="x")
        self.play_btn = ttk.Button(bar, text=T("Duraklat"), width=10, command=self.toggle)
        self.play_btn.pack(side="left")
        self.mode = tk.StringVar(value=app.cfg.get("radar_mode", "tr"))
        ttk.Radiobutton(bar, text=T("Türkiye"), value="tr", variable=self.mode, style="Toolbutton",
                        command=self.mode_changed).pack(side="left", padx=(10, 0))
        self.city_btn = ttk.Radiobutton(bar, text=T("Şehrim"), value="city", variable=self.mode,
                                        style="Toolbutton", command=self.mode_changed)
        self.city_btn.pack(side="left")
        self.slider = ttk.Scale(bar, from_=1, to=RADAR_FRAMES, orient="horizontal",
                                command=self.on_slider)
        self.slider.pack(side="left", fill="x", expand=True, padx=10)
        ttk.Button(bar, text=T("Yenile"), command=self.load).pack(side="right")
        self.info = ttk.Label(bar, text="", width=22)
        self.info.pack(side="right", padx=(0, 10))
        self.note = ttk.Label(self, text="", foreground="#666", padding=(10, 0, 10, 8))
        self.note.pack(anchor="w")
        self.frames, self.photos, self.i, self.playing = [], [], 0, True
        self.job = self.reload_job = self.watch_job = None
        self._setting = False
        self._city_key = None
        self.protocol("WM_DELETE_WINDOW", self.close)
        self.bind("<space>", lambda e: self.toggle())
        self.bind("<Escape>", lambda e: self.close())
        self.focus_force()
        self.load()
        self.watch()

    def load(self):
        if self.reload_job:
            self.after_cancel(self.reload_job)
            self.reload_job = None
        self.info.config(text=T("İndiriliyor…"))

        def work():
            from concurrent.futures import ThreadPoolExecutor
            with ThreadPoolExecutor(4) as ex:
                return list(ex.map(fetch_radar_frame, range(1, RADAR_FRAMES + 1)))
        self.app.run_bg(work, self.got, self.failed)

    def failed(self, ex):
        if self.winfo_exists():
            self.info.config(text=T("Radar alınamadı"))
            self.reload_job = self.after(60_000, self.load)

    def got(self, frames):
        if not self.winfo_exists():
            return
        self.frames = frames
        self.i = len(frames) - 1
        self.rebuild()
        if self.playing and not self.job:
            self.job = self.after(1500, self.step)
        self.reload_job = self.after(10 * 60_000, self.load)

    def city_frame(self, im, box, pos, name):
        """Kareyi şehrin çevresine kırpıp büyütür; şehri işaretler, saat kutusunu köşeye koyar."""
        out = im.crop(box).resize(self.size, Image.BICUBIC)
        k = self.size[0] / (box[2] - box[0])
        cx, cy = pos[0] * k, pos[1] * k
        d = ImageDraw.Draw(out)
        r = max(7, int(self.size[0] * 0.012))
        d.ellipse([cx - r - 2, cy - r - 2, cx + r + 2, cy + r + 2], outline=(255, 255, 255), width=5)
        d.ellipse([cx - r, cy - r, cx + r, cy + r], outline=(210, 30, 30), width=3)
        fnt = F("sb", max(13, int(self.size[1] * 0.026)))
        tx = cx + r + 8
        if tx + text_len(name, font=fnt) > self.size[0] - 6:  # sağa sığmıyorsa sola yaz
            tx = cx - r - 8 - text_len(name, font=fnt)
        d.text((tx, cy), name, font=fnt, fill=(20, 20, 20), anchor="lm",
               stroke_width=3, stroke_fill=(255, 255, 255))
        tb = im.crop(RADAR_TIME_BOX)  # özgün görüntüdeki saat (UTC)
        out.paste(tb, (self.size[0] - tb.width - 6, self.size[1] - tb.height - 6))
        return out

    def rebuild(self):
        """Seçili görünüme göre (Türkiye / Şehrim) gösterilecek kareleri hazırlar."""
        if not self.frames:
            return
        loc = self.app.loc
        self._city_key = loc_key(loc)
        cb = radar_city_box(loc["lat"], loc["lon"])
        self.city_btn.config(text=T('Şehrim ({})').format(loc_short(loc)),
                             state="normal" if cb else "disabled")
        mode = self.mode.get()
        if mode == "city" and cb:
            frames = [self.city_frame(f, cb[0], cb[1], loc_short(loc)) for f in self.frames]
            # 1° boylam bu haritada ~35,2 px ve Türkiye enleminde ~86 km
            km = round(RADAR_CROP[0] / 35.2 * 86 / 50) * 50
            self.note.config(text=T('{} çevresi (yaklaşık {} km genişlik) · kaynak: MGM radar kompoziti · saat UTC').format(loc_title(loc), km))
        else:
            frames = [f.resize(self.size, Image.LANCZOS) for f in self.frames]
            self.note.config(text=T("Kaynak: MGM radar kompoziti · son 15 görüntü, yaklaşık 6 dakika "
                                  "arayla · saat görüntünün sağ altında (UTC)")
                                  + ("" if cb else T(" · bu şehir radar alanının dışında")))
        self.photos = [ImageTk.PhotoImage(f) for f in frames]
        self.i = min(self.i, len(self.photos) - 1)
        self.show()

    def mode_changed(self):
        self.app.cfg["radar_mode"] = self.mode.get()
        self.app.save_cfg()
        self.rebuild()

    def watch(self):
        """Widget'ta şehir değişince (elle ya da otomatik geçişle) görünümü güncelle."""
        if not self.winfo_exists():
            return
        try:
            if self.frames and loc_key(self.app.loc) != self._city_key:
                self.rebuild()
        except Exception:
            log(traceback.format_exc())
        self.watch_job = self.after(500, self.watch)

    def show(self):
        if not self.photos:
            return
        self.view.config(image=self.photos[self.i])
        self._setting = True
        self.slider.set(self.i + 1)
        self._setting = False
        n = len(self.photos)
        self.info.config(text=T('Görüntü {}/{}').format(self.i + 1, n) + (T("  (en yeni)") if self.i == n - 1 else ""))

    def step(self):
        self.job = None
        if not self.photos or not self.playing:
            return
        self.i = (self.i + 1) % len(self.photos)
        self.show()
        self.job = self.after(1500 if self.i == len(self.photos) - 1 else 320, self.step)

    def toggle(self):
        self.playing = not self.playing
        self.play_btn.config(text=T("Duraklat") if self.playing else T("Oynat"))
        if self.playing and not self.job:
            self.job = self.after(320, self.step)

    def on_slider(self, v):
        if self._setting or not self.photos:
            return
        self.playing = False
        self.play_btn.config(text=T("Oynat"))
        self.i = max(0, min(len(self.photos) - 1, int(round(float(v))) - 1))
        self.show()

    def close(self):
        for j in (self.job, self.reload_job, self.watch_job):
            if j:
                self.after_cancel(j)
        self.destroy()


class AlertDialog(tk.Toplevel):
    """MGM uyarısının tam metni."""

    def __init__(self, app, loc, alerts):
        super().__init__(app.root)
        self.title(T('MGM uyarısı — {}').format(loc_title(loc)))
        self.attributes("-topmost", True)
        self.resizable(False, False)
        frm = ttk.Frame(self, padding=14)
        frm.pack()
        for a in alerts:
            name, rgb = ALERT_COLORS[a["color"]]
            head = tk.Label(frm, text=T('  {} uyarı: {}  ').format(name, ', '.join(a['kinds']) or T('Meteorolojik uyarı')),
                            bg="#%02x%02x%02x" % rgb, fg="#1c1c1c", font=("Segoe UI", 10, "bold"))
            head.pack(anchor="w", pady=(0, 4))
            try:
                when = (f"{parse_utc(a['begin']).astimezone():%d.%m %H:%M} – "
                        f"{parse_utc(a['end']).astimezone():%d.%m %H:%M}")
                ttk.Label(frm, text=when, foreground="#555").pack(anchor="w")
            except Exception:
                pass
            ttk.Label(frm, text=a["text"], wraplength=420, justify="left").pack(anchor="w",
                                                                              pady=(4, 12))
        ttk.Button(frm, text=T("Kapat"), command=self.destroy).pack(anchor="e")
        self.bind("<Escape>", lambda e: self.destroy())
        self.update_idletasks()
        x = min(max(0, app.x), self.winfo_screenwidth() - self.winfo_width() - 20)
        y = min(max(0, app.y + 40), self.winfo_screenheight() - self.winfo_height() - 80)
        self.geometry(f"+{x}+{y}")
        self.focus_force()


# --------------------------------------------------------------------------- sağ tık menüsü

def build_scheme(seed, light):
    """Tek bir renk tonundan Material You benzeri menü paleti (Spotify widget'ı ile aynı)."""
    h, s = seed
    sp = min(0.85, max(0.40, s)) if s >= 0.15 else s * 0.6
    sn = sp * 0.28  # nötr yüzeyler için düşük doygunluk

    def T(tone, sat):
        return tuple(int(round(c * 255)) for c in colorsys.hls_to_rgb(h, tone / 100, sat))

    if light:
        return {
            "primary": T(40, sp), "on_primary": (255, 255, 255),
            "primary_container": T(88, sp), "on_primary_container": T(15, sp),
            "secondary_container": T(88, sp * 0.35), "on_secondary_container": T(16, sp * 0.35),
            "surface": T(95, sn), "surface_high": T(91, sn), "surface_highest": T(87, sn),
            "on_surface": T(10, sn), "on_surface_variant": T(32, sn), "outline": T(55, sn),
        }
    return {
        "primary": T(80, sp), "on_primary": T(18, sp),
        "primary_container": T(30, sp * 0.8), "on_primary_container": T(90, sp),
        "secondary_container": T(26, sp * 0.35), "on_secondary_container": T(90, sp * 0.35),
        "surface": T(11, sn), "surface_high": T(16, sn), "surface_highest": T(23, sn),
        "on_surface": T(92, sn), "on_surface_variant": T(76, sn), "outline": T(55, sn),
    }


def mix(a, b, t_):
    return tuple(int(round(a[i] + (b[i] - a[i]) * t_)) for i in range(3))


_mask_cache = {}


def _rr_mask(w, h, fx0, fy0, fx1, fy1, r, ellipse=False):
    key = (w, h, round(fx0, 2), round(fy0, 2), round(fx1, 2), round(fy1, 2), round(r, 2), ellipse)
    m = _mask_cache.get(key)
    if m is None:
        SS = 4
        big = Image.new("L", (w * SS, h * SS), 0)
        d = ImageDraw.Draw(big)
        box = (fx0 * SS, fy0 * SS, fx1 * SS - 1, fy1 * SS - 1)
        if ellipse:
            d.ellipse(box, fill=255)
        else:
            d.rounded_rectangle(box, radius=r * SS, fill=255)
        m = big.resize((w, h), Image.Resampling.BOX)
        if len(_mask_cache) > 400:
            _mask_cache.clear()
        _mask_cache[key] = m
    return m


def rr(img, x0, y0, x1, y1, r, color, alpha=255, ellipse=False):
    """Kenarları yumuşatılmış yuvarlak dikdörtgen (fiziksel piksel)."""
    if x1 - x0 < 0.5 or y1 - y0 < 0.5:
        return
    r = max(0.0, min(r, (x1 - x0) / 2, (y1 - y0) / 2))
    ix0, iy0 = max(0, int(math.floor(x0))), max(0, int(math.floor(y0)))
    ix1, iy1 = min(img.width, int(math.ceil(x1))), min(img.height, int(math.ceil(y1)))
    w, h = ix1 - ix0, iy1 - iy0
    if w <= 0 or h <= 0:
        return
    m = _rr_mask(w, h, x0 - ix0, y0 - iy0, x1 - ix0, y1 - iy0, r, ellipse)
    if alpha < 255:
        m = m.point(lambda v: v * alpha // 255)
    layer = Image.new("RGBA", (w, h), tuple(color) + (255,))
    layer.putalpha(m)
    img.alpha_composite(layer, (ix0, iy0))


ICON_FONT = _font_file("SegoeIcons.ttf", "segmdl2.ttf")
MI_BACK, MI_CHEV, MI_CHECK = "\uE72B", "\uE76C", "\uE73E"
MI = {"refresh": "\uE72C", "city": "\uE707", "radar": "\uE909", "lock": "\uE72E",
      "pin": "\uE718", "look": "\uE771", "units": "\uE9CA", "notif": "\uEA8F",
      "sync": "\uE895", "window": "\uE737", "widgets": "\uECA5", "update": "\uE896",
      "open": "\uE8A7", "close": "\uE711", "add": "\uE710", "edit": "\uE70F",
      "import": "\uE8B5", "export": "\uE74E", "clock": "\uE823", "photo": "\uE91B",
      "color": "\uE790", "info": "\uE946", "eye": "\uE890", "keyboard": "\uE765",
      "remove": "\uE711", "history": "\uE81C", "start": "\uE8FC"}
SWATCHES = [("#3b6ea5", T("Mavi")), ("#2e7d5b", T("Yeşil")), ("#1f7a8c", T("Turkuaz")),
            ("#7a3fa0", T("Mor")), ("#b23a48", T("Kırmızı")), ("#c46a1c", T("Turuncu")),
            ("#555c66", T("Gri"))]


class MenuPanel:
    """Widget'la aynı görünümde, sayfalı sağ tık menüsü (Spotify widget'ındaki gibi).
    Alt menüler açılır liste yerine aynı panelde sayfa olarak açılır; ayar değişince hem
    widget hem menü anında yeniden çizilir."""
    _inst = None
    PW = 300          # panel genişliği (mantıksal px)
    M = 14            # gölge payı
    HEIGHTS = {"quick": 80, "sep": 9, "nav": 40, "toggle": 40, "action": 40, "radio": 38,
               "seg": 66, "swatch": 66, "info": 24, "header": 30, "title": 50}

    @classmethod
    def toggle(cls, app, x, y):
        if cls._inst is not None:  # açıksa: yeni konumda yeniden aç
            cls._inst.close()
        cls._inst = MenuPanel(app, x, y)

    @classmethod
    def close_open(cls):
        if cls._inst is not None:
            cls._inst.close()

    def __init__(self, app, x, y):
        self.app = app
        self.S = S = app.dpi
        r = lambda v: max(6, round(v * S))  # noqa: E731
        self.F = {"label": F("reg", r(13)), "value": F("reg", r(12)), "head": F("sb", r(11)),
                  "title": F("sb", r(15)), "seg": F("reg", r(12)), "segb": F("sb", r(12)),
                  "tiny": F("reg", r(10))}
        self._ifonts = {}
        self.pages = ["main"]
        self.scroll = 0.0
        self.hover = None
        self.hits = []
        self.anchor = (x, y)
        l_, t_, r_, b_ = work_area(x, y)
        self.max_h = (b_ - t_) / S - 2 * self.M - 8
        self.WM = round((self.PW + 2 * self.M) * S)
        self.HM = round((self.max_h + 2 * self.M) * S) + 2
        tl = self.top = tk.Toplevel(app.root)
        tl.overrideredirect(True)
        tl.configure(bg="black")
        # tam boyutta oluştur: sonradan büyütülen katmanlı pencereye yazma hata 87 verir
        tl.geometry(f"{self.WM}x{self.HM}+-30000+-30000")
        tl.update_idletasks()
        self.hwnd = user32.GetAncestor(tl.winfo_id(), 2)
        ex = user32.GetWindowLongPtrW(self.hwnd, GWL_EXSTYLE)
        user32.SetWindowLongPtrW(self.hwnd, GWL_EXSTYLE,
                                 (ex | WS_EX_LAYERED | WS_EX_TOOLWINDOW) & ~WS_EX_APPWINDOW)
        user32.SetWindowLongPtrW(self.hwnd, GWLP_HWNDPARENT, 0)  # masaüstü widget'ına bağlı olmasın
        self.surface = LayeredSurface(self.hwnd, self.WM, self.HM)
        self.surface.alpha = 0
        tl.bind("<Motion>", self._on_motion)
        tl.bind("<Leave>", lambda _e: self._set_hover(None))
        tl.bind("<ButtonRelease-1>", self._on_click)
        tl.bind("<MouseWheel>", self._on_wheel)
        tl.bind("<Escape>", lambda _e: self._back_or_close())
        tl.bind("<BackSpace>", lambda _e: self._back_or_close())
        self._closing = False
        self._frame = None
        self.render(place=True)
        user32.SetWindowPos(self.hwnd, HWND_TOPMOST, 0, 0, 0, 0, SWP_NOSIZE | SWP_NOMOVE)
        tl.focus_force()
        user32.SetForegroundWindow(self.hwnd)
        self._fade(0)
        tl.after(250, lambda: tl.bind("<FocusOut>", self._on_focus_out))
        self._was_down = True
        self._watch()

    def _watch(self):
        """Odak alınamazsa da çalışsın: panelin dışına tıklanınca kapan."""
        if self._closing:
            return
        down = bool(user32.GetAsyncKeyState(0x01) & 0x8000 or user32.GetAsyncKeyState(0x02) & 0x8000)
        if down and not self._was_down:
            pt = wintypes.POINT()
            user32.GetCursorPos(ctypes.byref(pt))
            S, M = self.S, self.M
            x0, y0 = self.pos[0] + round(M * S), self.pos[1] + round(M * S)
            if not (x0 <= pt.x <= x0 + round(self.PW * S) and y0 <= pt.y <= y0 + round(self.vh * S)):
                self.close()
                return
        self._was_down = down
        self.top.after(40, self._watch)

    # -- yardımcılar
    def icon(self, px_):
        f = self._ifonts.get(px_)
        if f is None:
            f = self._ifonts[px_] = ImageFont.truetype(ICON_FONT, max(6, round(px_ * self.S)))
        return f

    def _fade(self, step):
        if self._closing:
            return
        n = max(3, round(78 / self.app.frame_ms))  # ~78 ms, ekranın her karesinde bir adım
        self.surface.alpha = min(255, round(255 * (step + 1) / n))
        if self._frame is not None:
            self.surface.show(self._frame)
        if step < n - 1:
            self.top.after(self.app.next_ms(), lambda: self._fade(step + 1))

    def close(self):
        if self._closing:
            return
        self._closing = True
        MenuPanel._inst = None
        try:
            self.top.destroy()
        except tk.TclError:
            pass

    def _on_focus_out(self, _e):
        # odak gerçekten başka bir pencereye geçtiyse kapan
        self.top.after(60, lambda: (None if self._closing or
                                    user32.GetForegroundWindow() == self.hwnd else self.close()))

    def _back_or_close(self):
        if len(self.pages) > 1:
            self._go_back()
        else:
            self.close()

    def _open_page(self, page):
        self.pages.append(page)
        self.scroll = 0.0
        self.hover = None
        self.render()

    def _go_back(self):
        if len(self.pages) > 1:
            self.pages.pop()
            self.scroll = 0.0
            self.hover = None
            self.render()

    def _then_close(self, fn):
        """İşlemi yap ve menüyü kapat (pencere açan / tek seferlik işlemler)."""
        def go():
            self.close()
            fn()
        return go

    def set(self, key, value, after=None):
        """Ayarı değiştir, kaydet, widget'ı güncelle."""
        self.app.cfg[key] = value
        self.app.save_cfg()
        (after or self.app.render)()

    def scheme(self):
        """Menü renkleri widget'ın o anki temasından türetilir."""
        app = self.app
        th = app.theme
        light = th == "light"
        if th == "custom":
            base = hex_rgb(app.cfg.get("custom_color"))
            light = luminance(custom_colors(app.cfg.get("custom_color"))[0]) > 0.62
        elif th in ("auto", "scene"):
            loc = app.loc
            cur = (app.datas.get(loc_key(loc)) or {}).get("cur") or {}
            night = is_night(loc["lat"], loc["lon"], datetime.now(timezone.utc))
            base = palette_for(cur.get("hadiseKodu", "CB"), night)[0]
        else:
            base = (59, 110, 165)
        hh, _, ss = colorsys.rgb_to_hls(*(v / 255 for v in base))
        return build_scheme((hh, ss), light)

    # -- sayfalar (her çizimde güncel durumdan yeniden kurulur)
    def items(self):
        app, c = self.app, self.app.cfg
        page = self.pages[-1]
        main = not PROFILE
        on = lambda k: bool(c.get(k))  # noqa: E731
        theme_name = dict(THEMES).get(c.get("theme", "auto"), "").rstrip("…").split(" (")[0]
        if page == "main":
            n = len(app.locs)
            cnt = sum(bool(c.get(k)) for k in ("notify_rain", "notify_alerts", "notify_uv",
                                               "morning_on", "reminders_on"))
            out = [
                {"t": "quick", "buttons": [
                    ("q_ref", MI["refresh"], T("Yenile"), app.loading, app.refresh),
                    ("q_city", MI["city"], T("Şehirler"), False, self._then_close(app.open_cities)),
                    ("q_radar", MI["radar"], "Radar", False, self._then_close(app.open_radar)),
                    ("q_lock", MI["lock"], T("Kilitle"), on("locked"),
                     lambda: self.set("locked", not on("locked"))),
                    ("q_top", MI["pin"], T("Üstte"), on("on_top"),
                     lambda: self.set("on_top", not on("on_top"), app.apply_layer))]},
                {"t": "sep"},
                {"t": "nav", "id": "p_cities", "icon": MI["city"], "label": T("Şehirler"),
                 "value": loc_short(app.loc) + (f"  ·  {n}" if n > 1 else ""), "page": "cities"},
                {"t": "nav", "id": "p_look", "icon": MI["look"], "label": T("Görünüm"),
                 "value": theme_name, "page": "look"},
                {"t": "nav", "id": "p_units", "icon": MI["units"], "label": T("Birimler"),
                 "value": ("°F" if c.get("units_temp") == "F" else "°C") + " · "
                          + (T("m/sn") if c.get("units_wind") == "ms" else T("km/sa")), "page": "units"},
                {"t": "sep"},
                {"t": "nav", "id": "p_notif", "icon": MI["notif"], "label": T("Bildirimler"),
                 "value": T('{} açık').format(cnt) if cnt else T("Kapalı"), "page": "notif"},
                {"t": "nav", "id": "p_refresh", "icon": MI["sync"], "label": T("Yenileme"),
                 "value": T('{} dk').format(c.get('refresh_min', 10)), "page": "refresh"},
                {"t": "nav", "id": "p_events", "icon": MI["history"], "label": T("Olay günlüğü"),
                 "value": (lambda n_: T('{} olay').format(n_) if n_ else T("Boş"))(len(app.events.recent(24))),
                 "page": "events"},
                {"t": "nav", "id": "p_window", "icon": MI["window"], "label": T("Pencere"),
                 "value": T("Üstte") if on("on_top") else T("Masaüstü"), "page": "window"},
                {"t": "nav", "id": "p_widgets", "icon": MI["widgets"], "label": T("Widget'lar ve ayarlar"),
                 "value": "", "page": "widgets"},
            ]
            if main:
                out.append({"t": "nav", "id": "p_update", "icon": MI["update"], "label": T("Güncelleme"),
                            "value": T("Yeni sürüm!") if app.update_info else T('Sürüm {}').format(APP_VERSION),
                            "page": "update"})
            out += [
                {"t": "sep"},
                {"t": "action", "id": "a_site", "icon": MI["open"], "label": T("Kaynak sitesini aç"),
                 "do": self._then_close(app.open_site)},
                {"t": "action", "id": "a_close", "icon": MI["close"], "label": T("Kapat"),
                 "do": self._then_close(app.quit)},
            ]
            return out

        titles = {"cities": T("Şehirler"), "look": T("Görünüm"), "layout": T("Düzen"), "theme": T("Tema"),
                  "miniline": T("Mini kartta ikinci satır"), "units": T("Birimler"),
                  "notif": T("Bildirimler"), "refresh": T("Yenileme"), "window": T("Pencere"),
                  "hotkey": T("Kısayol tuşu"), "widgets": T("Widget'lar ve ayarlar"),
                  "update": T("Güncelleme"), "events": T("Olay günlüğü (son 24 saat)"),
                  "minicard": T("Mini kart")}
        out = [{"t": "title", "label": titles[page]}]
        if page == "cities":
            n = len(app.locs)
            for i, l in enumerate(app.locs):
                cur = (app.datas.get(loc_key(l)) or {}).get("cur") or {}
                tv = cur.get("sicaklik")
                out.append({"t": "radio", "id": f"r_c{i}", "label": loc_title(l),
                            "value": f"{tint(tv)}°" if ok(tv) else "", "on": i == app.idx,
                            "do": (lambda i=i: app.switch_to(i, user=True))})
            out += [
                {"t": "sep"},
                {"t": "toggle", "id": "t_rot", "label": T("Otomatik geçiş"), "on": on("rotate"),
                 "set": lambda v: app.set_rotate(on=v), "disabled": n < 2},
                {"t": "seg", "id": "s_rot", "label": T("Geçiş süresi"),
                 "value": int(c.get("rotate_sec", 15)),
                 "options": [(5, T("5 sn")), (10, T("10 sn")), (15, T("15 sn")), (30, T("30 sn")),
                             (60, T("1 dk")), (300, T("5 dk"))],
                 "set": lambda v: app.set_rotate(sec=v), "disabled": n < 2},
            ]
            if n < 2:
                out.append({"t": "info", "label": T("Otomatik geçiş için en az 2 şehir ekleyin.")})
            out.append({"t": "action", "id": "a_manage", "icon": MI["edit"],
                        "label": T("Şehirleri yönet…"), "do": self._then_close(app.open_cities)})
        elif page == "look":
            scale = min(SCALES, key=lambda v: abs(v - float(c.get("scale", 1.0))))
            opac = min(OPACITIES, key=lambda v: abs(v - float(c.get("opacity", 1.0))))
            out += [
                {"t": "nav", "id": "p_layout", "label": T("Düzen"),
                 "value": LAYOUTS[app.base_layout][2].split(" (")[0], "page": "layout"},
                {"t": "nav", "id": "p_theme", "label": T("Tema"), "value": theme_name, "page": "theme"},
                {"t": "seg", "id": "s_scale", "label": T("Ölçek"), "value": scale,
                 "options": [(v, pct(v * 100)) for v in SCALES],
                 "set": lambda v: self.set("scale", v, app.relayout)},
                {"t": "seg", "id": "s_rad", "label": T("Köşeler"),
                 "value": min((r_[0] for r_ in RADII), key=lambda v: abs(v - float(c.get("radius", 22)))),
                 "options": [(0, T("Köşeli")), (10, T("Az")), (22, T("Normal")), (32, T("Çok"))],
                 "set": lambda v: self.set("radius", v)},
                {"t": "seg", "id": "s_op",
                 "label": T("Gölge yoğunluğu") if app.theme == "clear" else T("Arka plan opaklığı"),
                 "value": opac, "options": [(v, pct(v * 100)) for v in OPACITIES],
                 "set": lambda v: self.set("opacity", v)},
                {"t": "seg", "id": "s_hourly", "label": T("Saatlik tahmin"),
                 "value": c.get("hourly", "icons"),
                 "options": [("icons", T("İkonlar")), ("chart", T("Sıcaklık grafiği"))],
                 "set": lambda v: self.set("hourly", v)},
                {"t": "nav", "id": "p_mini", "label": T("Mini kart"),
                 "value": T("Üzerine gelince büyür") if c.get("mini_hover", "expand") == "expand" else
                          {"expand": T("Tıkla: büyüt"), "page": T("Tıkla: sayfa"),
                           "none": ""}.get(c.get("mini_click", "expand"), ""), "page": "minicard"},
                {"t": "sep"},
                {"t": "toggle", "id": "t_clock", "label": T("Saat ve tarihi göster"), "on": on("clock"),
                 "set": lambda v: self.set("clock", v)},
                {"t": "toggle", "id": "t_anim", "label": T("Animasyonlu ikonlar"), "on": on("animate"),
                 "set": lambda v: self.set("animate", v, app.anim_changed)},
                {"t": "toggle", "id": "t_fx", "label": T("Arka plan efektleri (yağmur, kar, yıldızlar…)"),
                 "on": c.get("effects", True), "set": lambda v: self.set("effects", v, app.anim_changed)},
                {"t": "toggle", "id": "t_hover", "label": T("Üzerine gelince ayrıntı kartı"),
                 "on": c.get("hover", True), "set": lambda v: self.set("hover", v, app.popup_hide)},
                {"t": "toggle", "id": "t_ctemp", "label": T("Renkli sıcaklık (soğuk mavi, sıcak turuncu)"),
                 "on": on("colored_temp"), "set": lambda v: self.set("colored_temp", v)},
                {"t": "toggle", "id": "t_aborder", "label": T("Uyarı varken kart kenarı renkli"),
                 "on": c.get("alert_border", True), "set": lambda v: self.set("alert_border", v)},
            ]
        elif page == "minicard":
            out += [
                {"t": "seg", "id": "s_hover", "label": T("Fareyle üzerine gelince"),
                 "value": c.get("mini_hover", "expand"),
                 "options": [("expand", T("Büyüt")), ("popup", T("Ayrıntı kartı")), ("none", T("Hiçbir şey"))],
                 "set": lambda v: self.set("mini_hover", v, app.popup_hide)},
                {"t": "seg", "id": "s_click", "label": T("Karta tıklayınca"),
                 "value": c.get("mini_click", "expand"),
                 "options": [("expand", T("Büyüt")), ("page", T("Sayfa değiştir")), ("none", T("Hiçbir şey"))],
                 "set": lambda v: self.set("mini_click", v)},
                {"t": "seg", "id": "s_expd", "label": T("Büyüyünce göster"),
                 "value": c.get("expand_detail", "rich"),
                 "options": [("rich", T("Ayrıntılı (+ 12 saat)")), ("compact", T("Sade (anlık)"))],
                 "set": lambda v: self.set("expand_detail", v, lambda: app.expand(False))},
                {"t": "seg", "id": "s_wheel", "label": T("Fare tekerleği"),
                 "value": c.get("wheel_action", "city"),
                 "options": [("city", T("Şehir değiştir")), ("page", T("Sayfa değiştir"))],
                 "set": lambda v: self.set("wheel_action", v)},
                {"t": "nav", "id": "p_mini2", "label": T("İkinci satır"),
                 "value": dict(MINI_LINES).get(c.get("mini_line", "hl"), ""), "page": "miniline"},
                {"t": "sep"},
                {"t": "info", "label": T("Sayfalar: şimdi → saatlik → yarın (Mini, Mini+).")},
                {"t": "info", "label": T("Üzerine gelince büyüt: fare çekilince küçülür.")},
                {"t": "info", "label": T("Tıklayınca büyüt: tekrar tıkla ya da 6 sn bekle.")},
                {"t": "info", "label": T("Yan yana / Alt alta: Shift + sürükle ile sırala.")},
            ]
        elif page == "layout":
            out += [{"t": "radio", "id": f"r_l{k}", "label": v[2].split(" (")[0],
                     "value": v[2].split(" (")[1].rstrip(")") if " (" in v[2] else "",
                     "on": app.base_layout == k, "do": (lambda k=k: self.set("layout", k, app.relayout))}
                    for k, v in LAYOUTS.items() if k != "peek"]
        elif page == "theme":
            cur_t = c.get("theme", "auto")
            for k, label in THEMES:
                if k == "image":
                    do = (lambda: self.set("theme", "image")) if c.get("bg_image") else \
                        self._then_close(app.choose_bg_image)
                else:
                    do = (lambda k=k: self.set("theme", k))
                out.append({"t": "radio", "id": f"r_t{k}", "label": label.rstrip("…"),
                            "on": cur_t == k, "do": do})
            out += [
                {"t": "swatch", "id": "s_col", "label": T("Özel renk"),
                 "value": (c.get("custom_color") or "").lower() if cur_t == "custom" else None,
                 "options": [(hx, hex_rgb(hx)) for hx, _ in SWATCHES] + [("pick", None)],
                 "set": self._pick_color},
                {"t": "action", "id": "a_img", "icon": MI["photo"],
                 "label": T("Arka plan görselini seç…"), "do": self._then_close(app.choose_bg_image)},
            ]
        elif page == "miniline":
            out += [{"t": "radio", "id": f"r_m{k}", "label": label,
                     "on": c.get("mini_line", "hl") == k,
                     "do": (lambda k=k: self.set("mini_line", k))} for k, label in MINI_LINES]
            if app.base_layout not in ("mini", "miniplus", "vmini", "row", "col"):
                out.append({"t": "info", "label": T("Mini, Mini+, Dikey mini ve çoklu düzenlerde görünür.")})
        elif page == "units":
            out += [
                {"t": "seg", "id": "s_ut", "label": T("Sıcaklık"), "value": c.get("units_temp", "C"),
                 "options": [("C", T("Santigrat (°C)")), ("F", T("Fahrenhayt (°F)"))],
                 "set": lambda v: self.set("units_temp", v, app.units_changed)},
                {"t": "seg", "id": "s_uw", "label": T("Rüzgar"), "value": c.get("units_wind", "kmh"),
                 "options": [("kmh", T("km/saat")), ("ms", T("m/sn"))],
                 "set": lambda v: self.set("units_wind", v, app.units_changed)},
            ]
        elif page == "notif":
            lo, hi = c.get("alert_low"), c.get("alert_high")
            parts = ([f"≤ {fmt_temp(lo)}"] if lo is not None else []) + \
                    ([f"≥ {fmt_temp(hi)}"] if hi is not None else [])
            noop = lambda: None  # noqa: E731
            out += [
                {"t": "toggle", "id": "t_rain", "label": T("Yağış yaklaşırken haber ver"),
                 "on": on("notify_rain"), "set": lambda v: self.set("notify_rain", v, noop)},
                {"t": "toggle", "id": "t_alert", "label": T("MGM uyarılarını bildir"),
                 "on": on("notify_alerts"), "set": lambda v: self.set("notify_alerts", v, noop)},
                {"t": "toggle", "id": "t_uv", "label": T("UV çok yüksek olunca haber ver"),
                 "on": on("notify_uv"), "set": lambda v: self.set("notify_uv", v, noop)},
                {"t": "sep"},
                {"t": "toggle", "id": "t_morning",
                 "label": T('Sabah özeti ({})').format(c.get('morning_time', '07:30')),
                 "on": on("morning_on"), "set": lambda v: self.set("morning_on", v, noop)},
                {"t": "toggle", "id": "t_remind", "label": T("Şemsiye / mont hatırlatması"),
                 "on": on("reminders_on"), "set": lambda v: self.set("reminders_on", v, noop)},
                {"t": "action", "id": "a_times", "icon": MI["clock"], "label": T("Sabah saati, sessiz saatler…"),
                 "value": T("Sessiz: açık") if on("quiet_on") else "",
                 "do": self._then_close(lambda: TimesDialog(app))},
                {"t": "action", "id": "a_thr", "icon": MI["units"], "label": T("Sıcaklık eşikleri…"),
                 "value": ", ".join(parts) or T("Kapalı"),
                 "do": self._then_close(lambda: ThresholdDialog(app))},
                {"t": "sep"},
                {"t": "toggle", "id": "t_lnk", "label": T('Başlıkta "{}" yazsın').format(APP_NAME),
                 "on": c.get("start_shortcut", True),
                 "set": lambda v: self.set("start_shortcut", v, app.apply_start_shortcut),
                 "disabled": not main},
                {"t": "action", "id": "a_test", "icon": MI["notif"], "label": T("Sabah özetini şimdi göster"),
                 "do": self._then_close(lambda: app.notify(*(app.morning_message(app.locs[0]) or (
                     T("Sabah özeti"), T("Veri henüz gelmedi."), None))))},
            ]
        elif page == "refresh":
            out += [{"t": "radio", "id": f"r_r{v}", "label": T('{} dakikada bir').format(v),
                     "on": int(c.get("refresh_min", 10)) == v,
                     "do": (lambda v=v: self.set("refresh_min", v, app.refresh))}
                    for v in REFRESH_OPTIONS]
            out += [{"t": "sep"},
                    {"t": "action", "id": "a_now", "icon": MI["refresh"], "label": T("Şimdi yenile"),
                     "value": T("Yenileniyor…") if app.loading else "", "do": app.refresh}]
        elif page == "window":
            noop = lambda: None  # noqa: E731
            out += [
                {"t": "seg", "id": "s_layer", "label": T("Konum"), "value": "top" if on("on_top") else "desktop",
                 "options": [("desktop", T("Masaüstünde")), ("top", T("Her zaman üstte"))],
                 "set": lambda v: self.set("on_top", v == "top", app.apply_layer)},
                {"t": "toggle", "id": "t_lock", "label": T("Konumu kilitle"), "on": on("locked"),
                 "set": lambda v: self.set("locked", v, noop)},
                {"t": "toggle", "id": "t_snap", "label": T("Ekran kenarına yapış"), "on": on("snap"),
                 "set": lambda v: self.set("snap", v, noop)},
                {"t": "toggle", "id": "t_ct", "label": T("Tıklamayı geçir (yalnızca görüntü)"),
                 "on": on("click_through"), "set": self._set_click_through,
                 "disabled": not app.tray},
                {"t": "toggle", "id": "t_tray", "label": T("Tepsi ikonunu göster"), "on": bool(app.tray),
                 "set": app.set_tray},
            ]
            if not app.tray:
                out.append({"t": "info", "label": T("Tıklamayı geçir için tepsi ikonu gerekir.")})
            out.append({"t": "action", "id": "a_hide", "icon": MI["eye"],
                        "label": T("Widget'ı göster") if app.hidden else T("Widget'ı gizle"),
                        "do": self._then_close(app.toggle_hidden),
                        "disabled": not (app.tray or app.hidden)})
            if main:
                hk = dict((h[0], h[1]) for h in HOTKEYS).get(c.get("hotkey", "win_shift_w"), "")
                out.append({"t": "nav", "id": "p_hotkey", "icon": MI["keyboard"],
                            "label": T("Kısayol tuşu (öne getir)"),
                            "value": hk + (T("  — kullanımda!") if app.hotkey_ok is False else ""),
                            "page": "hotkey"})
        elif page == "hotkey":
            out += [{"t": "radio", "id": f"r_h{h[0]}", "label": h[1],
                     "on": c.get("hotkey", "win_shift_w") == h[0],
                     "do": (lambda k=h[0]: self._set_hotkey(k))} for h in HOTKEYS]
            out.append({"t": "info", "label": T("Basınca widget öne gelir, tekrar basınca geri döner.")})
            if app.hotkey_ok is False:
                out.append({"t": "info", "label": T("Bu kısayolu başka bir uygulama kullanıyor.")})
        elif page == "widgets":
            if main:
                out += [{"t": "action", "id": "a_add", "icon": MI["add"],
                         "label": T("Yeni widget ekle (başka şehir / ekran)"),
                         "do": self._then_close(app.add_widget)},
                        {"t": "info", "label": T("Ek widget sayısı"), "value": str(len(list_profiles()))}]
            else:
                out.append({"t": "action", "id": "a_rm", "icon": MI["remove"],
                            "label": T("Bu widget'ı kaldır…"), "do": self._then_close(app.remove_widget)})
            out += [{"t": "sep"},
                    {"t": "action", "id": "a_exp", "icon": MI["export"], "label": T("Ayarları dışa aktar…"),
                     "do": self._then_close(app.export_settings)},
                    {"t": "action", "id": "a_imp", "icon": MI["import"], "label": T("Ayarları içe aktar…"),
                     "do": self._then_close(app.import_settings)}]
            if main:
                out += [{"t": "sep"},
                        {"t": "toggle", "id": "t_start", "label": T("Windows ile başlat"),
                         "on": startup_enabled(), "set": set_startup},
                        {"t": "seg", "id": "s_lang", "label": "Dil / Language", "value": c.get("lang", LANG),
                         "options": [("tr", "Türkçe"), ("en", "English")],
                         "set": lambda v: self.set("lang", v, app.restart_for_lang)}]
        elif page == "events":
            evs = app.events.recent(24)
            if not evs:
                out.append({"t": "info", "label": T("Son 24 saatte kayıtlı olay yok.")})
            today, last = datetime.now().date(), None
            for e in evs[:80]:
                lt = parse_utc(e["t"]).astimezone()
                day = T("Bugün") if lt.date() == today else T("Dün")
                if day != last:
                    out.append({"t": "header", "label": day})
                    last = day
                out.append({"t": "info", "label": f"{lt:%H:%M}   {e.get('city', '')}",
                            "value": event_text(e)})
            out += [{"t": "sep"},
                    {"t": "info", "label": T("Kaydedilenler: yağış başlangıcı ve bitişi,")},
                    {"t": "info", "label": T("3 saatte 5°+ sıcaklık değişimi, UV 8+,")},
                    {"t": "info", "label": T("rüzgar 60+ km/sa, yeni MGM uyarısı.")},
                    {"t": "action", "id": "a_clear", "icon": MI["remove"], "label": T("Günlüğü temizle"),
                     "do": app.clear_events, "disabled": not app.events.events}]
        elif page == "update":
            out.append({"t": "info", "label": T("Kurulu sürüm"), "value": APP_VERSION})
            if app.update_info:
                out.append({"t": "action", "id": "a_inst", "icon": MI["update"],
                            "label": T('Sürüm {} — şimdi güncelle').format(app.update_info['version']),
                            "do": self._then_close(app.prompt_update)})
            out += [{"t": "action", "id": "a_chk", "icon": MI["sync"], "label": T("Güncellemeleri denetle"),
                     "do": self._then_close(lambda: app.check_update(manual=True))},
                    {"t": "toggle", "id": "t_auto", "label": T("Otomatik denetle (günde bir)"),
                     "on": on("auto_update"), "set": lambda v: self.set("auto_update", v, lambda: None)}]
        return out

    # -- menüden çağrılan ayarlar
    def _pick_color(self, v):
        if v == "pick":
            self.close()
            self.app.choose_custom_color()
            return
        self.app.cfg["custom_color"] = v
        self.set("theme", "custom")

    def _set_click_through(self, v):
        if v and not self.app.tray:
            return
        self.set("click_through", v, lambda: set_click_through(self.app.hwnd, v))

    def _set_hotkey(self, k):
        self.app.cfg["hotkey_touched"] = True
        self.set("hotkey", k, self.app.apply_hotkey)

    # -- çizim
    def render(self, place=False):
        if self._closing:
            return
        S, Fn = self.S, self.F
        P = self.scheme()
        X = lambda v: v * S  # noqa: E731
        items = self.items()
        PW, M = self.PW, self.M
        total = 8 + sum(self.HEIGHTS[it["t"]] for it in items) + 8
        vh = min(total, self.max_h)
        self.scroll = max(0.0, min(self.scroll, total - vh))
        size = (round(X(PW)), round(X(total)))
        cb = Image.new("RGBA", size, (0, 0, 0, 0))   # zeminler, düğmeler
        cf = Image.new("RGBA", size, (0, 0, 0, 0))   # yazılar, simgeler
        d = ImageDraw.Draw(cf)
        hits = []
        hov = self.hover
        on_, var, pri = P["on_surface"] + (255,), P["on_surface_variant"] + (255,), P["primary"] + (255,)
        y = 8
        for it in items:
            h = self.HEIGHTS[it["t"]]
            t_ = it["t"]
            iid = it.get("id")
            dis = it.get("disabled")
            if t_ in ("nav", "toggle", "action", "radio") and hov == iid and not dis:
                rr(cb, X(6), X(y + 2), X(PW - 6), X(y + h - 2), X(12), P["on_surface"], alpha=22)
            if t_ == "quick":
                bw = (PW - 16) / len(it["buttons"])
                for i, (bid, glyph, lbl, active, fn) in enumerate(it["buttons"]):
                    cx = 8 + bw * (i + 0.5)
                    col = P["primary_container"] if active else P["surface_highest"]
                    if hov == bid:
                        col = mix(col, P["on_surface"], 0.10)
                    rr(cb, X(cx - 22), X(y + 8), X(cx + 22), X(y + 52), X(16), col)
                    d.text((X(cx), X(y + 30)), glyph, font=self.icon(17),
                           fill=(P["on_primary_container"] if active else P["on_surface"]) + (255,),
                           anchor="mm")
                    dtext(d, (X(cx), X(y + 66)), lbl, font=Fn["tiny"], fill=var, anchor="mm")
                    hits.append((bid, cx - bw / 2, y, cx + bw / 2, y + h, fn))
            elif t_ == "sep":
                rr(cb, X(16), X(y + 4), X(PW - 16), X(y + 5), 0, P["on_surface"], alpha=24)
            elif t_ == "title":
                if hov == "back":
                    rr(cb, X(8), X(y + 7), X(44), X(y + 43), 0, P["on_surface"], alpha=22, ellipse=True)
                d.text((X(26), X(y + 25)), MI_BACK, font=self.icon(15), fill=on_, anchor="mm")
                dtext(d, (X(52), X(y + 25)), fit(d, it["label"], Fn["title"], X(PW - 64)),
                      font=Fn["title"], fill=on_, anchor="lm")
                hits.append(("back", 0, y, PW, y + h, self._go_back))
            elif t_ == "header":
                dtext(d, (X(18), X(y + 18)), it["label"], font=Fn["head"], fill=pri, anchor="lm")
            elif t_ == "info":
                val = fit(d, it["value"], Fn["value"], X(PW - 36) * 0.62) if it.get("value") else ""
                vw = text_len(val, font=Fn["value"]) if val else 0
                dtext(d, (X(18), X(y + h / 2)), fit(d, it["label"], Fn["value"], X(PW - 36) - vw - X(8)),
                      font=Fn["value"], fill=var, anchor="lm")
                if val:
                    dtext(d, (X(PW - 18), X(y + h / 2)), val, font=Fn["value"], fill=on_, anchor="rm")
            elif t_ in ("nav", "action", "toggle", "radio"):
                fg = on_ if not dis else P["outline"] + (255,)
                lx = 18
                if it.get("icon"):
                    d.text((X(30), X(y + h / 2)), it["icon"], font=self.icon(16),
                           fill=var if not dis else fg, anchor="mm")
                    lx = 52
                right = 0
                if t_ == "nav":
                    d.text((X(PW - 22), X(y + h / 2)), MI_CHEV, font=self.icon(11), fill=var,
                           anchor="mm")
                    right = 34
                elif t_ == "toggle":
                    self._switch(cb, PW - 54, y + h / 2, it["on"], dis, P)
                    right = 64
                elif t_ == "radio":
                    if it["on"]:
                        d.text((X(PW - 24), X(y + h / 2)), MI_CHECK, font=self.icon(14), fill=pri,
                               anchor="mm")
                        fg = pri
                    right = 40
                elif t_ == "action":
                    right = 18
                if it.get("value") and t_ in ("nav", "radio", "action"):
                    # sağdaki değer: etikete en az ~%45 yer bırakacak kadar kısaltılır
                    room = X(PW - lx - right) * 0.55
                    val = fit(d, it["value"], Fn["value"], room)
                    dtext(d, (X(PW - right), X(y + h / 2)), val, font=Fn["value"], fill=var, anchor="rm")
                    right += text_len(val, font=Fn["value"]) / S + 8
                dtext(d, (X(lx), X(y + h / 2)), fit(d, it["label"], Fn["label"], X(PW - lx - right - 6)),
                      font=Fn["label"], fill=fg, anchor="lm")
                if not dis:
                    if t_ == "nav":
                        fn = (lambda p=it["page"]: self._open_page(p))
                    elif t_ == "toggle":
                        fn = (lambda it=it: it["set"](not it["on"]))
                    else:
                        fn = it["do"]
                    hits.append((iid, 6, y, PW - 6, y + h, fn))
            elif t_ == "seg":
                dtext(d, (X(18), X(y + 14)), it["label"], font=Fn["value"],
                      fill=var if not dis else P["outline"] + (255,), anchor="lm")
                opts = it["options"]
                x0, x1, sy0, sy1 = 14, PW - 14, y + 26, y + 58
                rr(cb, X(x0), X(sy0), X(x1), X(sy1), X(16), P["outline"], alpha=110)
                rr(cb, X(x0 + 1), X(sy0 + 1), X(x1 - 1), X(sy1 - 1), X(15), P["surface_high"])
                sw = (x1 - x0) / len(opts)
                for i, (val, lbl) in enumerate(opts):
                    sx0 = x0 + i * sw
                    sel = val == it["value"]
                    sid = f"{iid}:{i}"
                    if sel:
                        rr(cb, X(sx0 + 3), X(sy0 + 3), X(sx0 + sw - 3), X(sy1 - 3), X(13),
                           P["secondary_container"])
                    elif hov == sid and not dis:
                        rr(cb, X(sx0 + 3), X(sy0 + 3), X(sx0 + sw - 3), X(sy1 - 3), X(13),
                           P["on_surface"], alpha=20)
                    col = (P["on_secondary_container"] if sel else P["on_surface"]) \
                        if not dis else P["outline"]
                    fnt = Fn["segb" if sel else "seg"]
                    dtext(d, (X(sx0 + sw / 2), X((sy0 + sy1) / 2)), fit(d, lbl, fnt, X(sw - 8)),
                          font=fnt, fill=col + (255,), anchor="mm")
                    if not dis:
                        hits.append((sid, sx0, sy0, sx0 + sw, sy1, (lambda s=it["set"], v=val: s(v))))
            elif t_ == "swatch":
                dtext(d, (X(18), X(y + 14)), it["label"], font=Fn["value"], fill=var, anchor="lm")
                opts = it["options"]
                sw = (PW - 28) / len(opts)
                for i, (val, col) in enumerate(opts):
                    cx, cy = 14 + sw * (i + 0.5), y + 44
                    sid = f"{iid}:{i}"
                    sel = val == it["value"]
                    if sel or hov == sid:
                        rr(cb, X(cx - 15), X(cy - 15), X(cx + 15), X(cy + 15), 0,
                           P["primary"] if sel else P["outline"], ellipse=True)
                        rr(cb, X(cx - 13), X(cy - 13), X(cx + 13), X(cy + 13), 0,
                           P["surface_high"], ellipse=True)
                    if col is None:  # renk seçici
                        rr(cb, X(cx - 11), X(cy - 11), X(cx + 11), X(cy + 11), 0,
                           P["surface_highest"], ellipse=True)
                        d.text((X(cx), X(cy)), MI["add"], font=self.icon(12), fill=on_, anchor="mm")
                    else:
                        rr(cb, X(cx - 11), X(cy - 11), X(cx + 11), X(cy + 11), 0, col, ellipse=True)
                    hits.append((sid, cx - sw / 2, y + 26, cx + sw / 2, y + 62,
                                 (lambda s=it["set"], v=val: s(v))))
            y += h
        content = cb
        content.alpha_composite(cf)

        # pencereye yerleştir: gölge + kart + içerik
        sk = (vh, P["surface_high"])
        if getattr(self, "_bg_key", None) != sk:  # gölge + kart zemini (yükseklik değişince)
            bg = Image.new("RGBA", (self.WM, self.HM), (0, 0, 0, 0))
            shadow = Image.new("RGBA", (round(X(PW + 2 * M)), round(X(vh + 2 * M))), (0, 0, 0, 0))
            rr(shadow, X(M), X(M + 3), X(M + PW), X(M + vh + 3), X(18), (0, 0, 0), alpha=110)
            bg.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(X(7))))
            rr(bg, X(M), X(M), X(M + PW), X(M + vh), X(18), P["surface_high"])
            self._bg, self._bg_key = bg, sk
        canvas = self._bg.copy()
        part = content.crop((0, round(X(self.scroll)), content.width, round(X(self.scroll + vh))))
        mask = _rr_mask(part.width, part.height, 0, 0, part.width, part.height, X(18))
        a = part.getchannel("A")
        part.putalpha(Image.composite(a, Image.new("L", a.size, 0), mask))
        canvas.alpha_composite(part, (round(X(M)), round(X(M))))
        if total > vh:  # kaydırma çubuğu
            th_ = vh * vh / total
            ty = self.scroll / (total - vh) * (vh - th_)
            rr(canvas, X(M + PW - 6), X(M + ty + 4), X(M + PW - 3), X(M + ty + th_ - 4),
               X(1.5), P["on_surface"], alpha=70)
        self.hits = hits
        self.vh = vh
        self._frame = canvas
        self.surface.show(canvas)
        self._place(vh, place)

    def _switch(self, img, x, cy, on_, dis, P):
        X = lambda v: v * self.S  # noqa: E731
        w, h = 40, 24
        if on_:
            rr(img, X(x), X(cy - h / 2), X(x + w), X(cy + h / 2), X(h / 2),
               P["primary"] if not dis else P["outline"])
            r_ = 9
            cx = x + w - h / 2
            rr(img, X(cx - r_), X(cy - r_), X(cx + r_), X(cy + r_), 0, P["on_primary"], ellipse=True)
        else:
            rr(img, X(x), X(cy - h / 2), X(x + w), X(cy + h / 2), X(h / 2), P["outline"],
               alpha=200 if not dis else 90)
            rr(img, X(x + 2), X(cy - h / 2 + 2), X(x + w - 2), X(cy + h / 2 - 2), X(h / 2 - 2),
               P["surface_highest"])
            r_ = 6
            cx = x + h / 2
            rr(img, X(cx - r_), X(cy - r_), X(cx + r_), X(cy + r_), 0, P["outline"], ellipse=True)

    def _place(self, vh, first):
        """İlk açılışta imleç konumuna; sayfa değişince ekrandan taşmayacak şekilde."""
        S, M = self.S, self.M
        ax, ay = self.anchor
        l_, t_, r_, b_ = work_area(ax, ay)
        w, h = round((self.PW + 2 * M) * S), round((vh + 2 * M) * S)
        if first:
            x = ax - round(M * S) + 2
            y = ay - round(M * S) + 2
            if x + w > r_:
                x = ax - w + round(M * S) - 2
            if y + h > b_:  # aşağı sığmıyorsa yukarı aç
                y = ay - h + round(M * S) - 2
            self.pos = [x, y]
        x, y = self.pos
        x = max(l_ - round(M * S), min(x, r_ - w + round(M * S)))
        y = max(t_ - round(M * S), min(y, b_ - h + round(M * S)))
        self.pos = [x, y]
        move_window(self.hwnd, x, y)

    # -- olaylar
    def _hit(self, e):
        x = e.x / self.S - self.M
        y = e.y / self.S - self.M
        if not (0 <= x <= self.PW and 0 <= y <= self.vh):
            return None
        y += self.scroll
        for h in self.hits:
            if h[1] <= x <= h[3] and h[2] <= y <= h[4]:
                return h
        return None

    def _set_hover(self, hid):
        if hid != self.hover:
            self.hover = hid
            try:
                self.top.config(cursor="hand2" if hid else "")
            except tk.TclError:
                return
            self.render()

    def _on_motion(self, e):
        h = self._hit(e)
        self._set_hover(h[0] if h else None)

    def _on_click(self, e):
        h = self._hit(e)
        x, y = e.x / self.S - self.M, e.y / self.S - self.M
        if h is None:
            if not (0 <= x <= self.PW and 0 <= y <= self.vh):
                self.close()  # gölgeye / dışarıya tıklama
            return
        try:
            h[5]()
        except Exception:
            log(traceback.format_exc())
        if not self._closing:
            self.render()

    def _on_wheel(self, e):
        self.scroll -= e.delta / 120 * 40
        self.render()


# --------------------------------------------------------------------------- widget

class WeatherWidget:
    PAD = 20

    def __init__(self):
        self.dpi = set_dpi_awareness()
        cfg = load_json(CONFIG_PATH, {})
        self.first_run = "loc" not in cfg and "locs" not in cfg
        if "locs" not in cfg:  # eski tek-konum ayarını taşı
            cfg["locs"] = [cfg.pop("loc")] if "loc" in cfg else [dict(DEFAULT_LOC)]
        self.placeholder = self.first_run  # ilk eklenen şehir varsayılanın yerine geçer
        self.cfg = {"locs": [], "idx": 0, "layout": "full", "scale": 1.0, "on_top": False,
                    "x": None, "y": None, "theme": "auto", "opacity": 1.0,
                    "rotate": False, "rotate_sec": 15,
                    "hourly": "icons", "animate": True, "units_temp": "C", "units_wind": "kmh",
                    "refresh_min": 10, "locked": False, "snap": True, "click_through": False,
                    "notify_rain": True, "notify_alerts": True,
                    "alert_low": None, "alert_high": None, "seen_alerts": [],
                    "auto_update": True, "tray": True,
                    "morning_on": True, "morning_time": "07:30", "reminders_on": True,
                    "notify_uv": True, "quiet_on": False, "quiet_start": "23:00",
                    "quiet_end": "07:00", "last_morning": "", "clock": False, "mini_line": "hl",
                    "hover": True, "hotkey": "win_shift_w", "radius": 22,
                    "custom_color": "#3b6ea5", "bg_image": "",
                    "radar_mode": "tr", "start_shortcut": True, "shortcut_target": "",
                    "mini_click": "expand", "wheel_action": "city", "colored_temp": False,
                    "alert_border": True, "mini_hover": "expand", "expand_detail": "rich",
                    "effects": True}
        self.cfg.update(cfg)
        # birden çok şehir varsa ve kullanıcı ayarı hiç değiştirmediyse geçiş açık başlasın
        if len(self.cfg["locs"]) > 1 and not self.cfg.get("rotate_touched"):
            self.cfg["rotate"] = True
        self.apply_units()
        self.q = queue.Queue()
        self.datas, self.errors = {}, {}
        self.alerts = []
        self.notified, self.thr_state = {}, {}
        self.events = EventLog(EVENTS_PATH)
        self.loading = False
        self.hidden = False
        self.refresh_job = self.rotate_job = self.fade_job = self.anim_job = None
        self.last_img = None
        self.hits = []
        self.anim_frame = 0
        self.frame_ms = 1000 / 60
        self._anim_needed = False
        self._theme_cache = (0, False)
        self._tray_key = None
        self.update_info = None
        self.updating = False
        self.popup = None
        self._hover_idx, self._hover_t, self._pressed = None, 0.0, False
        self.expanded, self._collapsed_pos, self._exp_idle, self._exp_job = False, None, 0, None
        self.mini_page, self._click_job, self._dbl = 0, None, False
        self._reorder, self._reorder_key, self._reordered = None, None, False
        self._resize_job = None
        self._hover_exp, self._hover_armed, self._hover_since, self._leave_since = False, True, None, None
        self.hotkey_win, self.hotkey_ok = None, None
        self.peeking, self._peek_job = False, None
        self._no_save = False
        self._exe_mtime = os.path.getmtime(sys.executable) if FROZEN else None
        self.load_cache()

        self.root = tk.Tk()
        self.root.withdraw()
        self.root.title(APP_NAME)
        self.root.overrideredirect(True)
        try:
            self._app_icon = ImageTk.PhotoImage(make_icon("PB", 64, False))
            self.root.iconphoto(True, self._app_icon)
        except Exception:
            log(traceback.format_exc())
        self.surface = None
        self.hwnd = None
        self.tray = None
        self.setup_window()
        self.root.deiconify()
        self.root.update_idletasks()
        self.hwnd = user32.GetAncestor(self.root.winfo_id(), 2)
        set_window_styles(self.hwnd)
        self.apply_layer()
        if not self.cfg.get("tray", True):
            # tepsi yokken widget'a ulaşmanın tek yolu widget'ın kendisi: tıklamayı geçirmesin
            self.cfg["click_through"] = False
        if self.cfg.get("click_through"):
            set_click_through(self.hwnd, True)
        if self.cfg.get("tray", True):
            self.create_tray()
        self.render()

        r = self.root
        r.bind("<ButtonPress-1>", self.on_press)
        r.bind("<B1-Motion>", self.on_drag)
        r.bind("<ButtonRelease-1>", self.on_release)
        r.bind("<Double-Button-1>", self.on_double)
        r.bind("<Button-3>", self.show_menu)
        r.bind("<MouseWheel>", self.on_wheel)
        r.after(150, self.poll)
        r.after(200, self._hover_tick)
        r.after(60_000 - int(time.time() * 1000) % 60_000 + 200, self.tick)  # dakika başına hizalı
        self.refresh()
        self.schedule_rotate()
        self._timer_fine(True)  # animasyonlar ekranın Hz değerinde akabilsin
        self.schedule_anim()
        r.after(3000, self.cleanup_old_exe)
        if "--update" in sys.argv:  # HavaDurumu.exe --update : varsa yeni sürümü sormadan kur
            r.after(2000, lambda: self.check_update(install=True))
            r.after(24 * 3600 * 1000, self.auto_update_check)
        else:
            r.after(20_000, self.auto_update_check)
        self.apply_hotkey()
        if not PROFILE:
            r.after(1500, self.spawn_profiles)
            r.after(2500, self.apply_start_shortcut)
        if self.first_run:
            r.after(400, self.open_cities)

    # ---- durum
    @property
    def locs(self):
        return self.cfg["locs"]

    @property
    def idx(self):
        n = len(self.locs)
        return min(max(0, int(self.cfg.get("idx", 0))), n - 1) if n else 0

    @property
    def loc(self):
        return self.locs[self.idx]

    @property
    def base_layout(self):
        """Ayarlardaki düzen."""
        lay = self.cfg.get("layout")
        return lay if lay in LAYOUTS and lay != "peek" else "full"

    @property
    def layout(self):
        """Etkin düzen: küçük kart büyütüldüyse geçici olarak ayrıntılı kart (ya da Kompakt)."""
        if getattr(self, "expanded", False):
            return "compact" if self.cfg.get("expand_detail", "rich") == "compact" else "peek"
        return self.base_layout

    @property
    def multi(self):
        return self.layout in ("row", "col")

    @property
    def multi_n(self):
        return max(1, min(len(self.locs), MULTI_MAX))

    @property
    def theme(self):
        """Etkin tema ('system' ise Windows ayarına göre koyu/açık)."""
        t = self.cfg.get("theme", "auto")
        if t != "system":
            return t
        ts, val = self._theme_cache
        if time.time() - ts > 5:
            val = windows_light_theme()
            self._theme_cache = (time.time(), val)
        return "light" if val else "dark"

    @property
    def S(self):
        return self.dpi * float(self.cfg.get("scale", 1.0))

    def px(self, v):
        return int(round(v * self.S))

    def size_px(self):
        """Pencere boyutu (gerçek piksel)."""
        lay = self.layout
        cw, ch, gap, n = self.px(MINI_W), self.px(MINI_H), self.px(MULTI_GAP), self.multi_n
        if lay == "row":
            return n * cw + (n - 1) * gap, ch
        if lay == "col":
            return cw, n * ch + (n - 1) * gap
        return self.px(LAYOUTS[lay][0]), self.px(LAYOUTS[lay][1])

    def apply_units(self):
        UNITS["temp"] = "F" if self.cfg.get("units_temp") == "F" else "C"
        UNITS["wind"] = "ms" if self.cfg.get("units_wind") == "ms" else "kmh"

    def load_cache(self):
        cache = load_json(CACHE_PATH, {}) or {}
        if "merkezId" in cache:  # eski tek-konum önbelleği
            cache = {"by_id": {str(cache["merkezId"]): cache}}
        self.datas = {k: fix_code(v) for k, v in (cache.get("by_id") or {}).items()}
        self.alerts = list(cache.get("alerts") or [])

    def save_cache(self):
        keep = {loc_key(l) for l in self.locs}
        save_json(CACHE_PATH, {"by_id": {k: v for k, v in self.datas.items() if k in keep},
                               "alerts": self.alerts})

    # ---- pencere
    def setup_window(self):
        self.w, self.h = self.size_px()
        sw = user32.GetSystemMetrics(0)
        vx, vy = user32.GetSystemMetrics(76), user32.GetSystemMetrics(77)
        vw, vh = user32.GetSystemMetrics(78), user32.GetSystemMetrics(79)
        x, y = self.cfg.get("x"), self.cfg.get("y")
        if x is None or y is None or not (vx - 50 <= x <= vx + vw - 50 and vy <= y <= vy + vh - 50):
            x, y = sw - self.w - self.px(32), self.px(48)
        self.x, self.y = int(x), int(y)
        self.root.geometry(f"{self.w}x{self.h}+{self.x}+{self.y}")
        if self.hwnd:
            if self.surface:
                self.surface.close()
            self.surface = LayeredSurface(self.hwnd, self.w, self.h)
            self.last_img = None

    def relayout(self):
        if self.expanded:  # düzen değişirken büyütülmüş kart önce küçülür
            if self._exp_job:
                self.root.after_cancel(self._exp_job)
                self._exp_job = None
            self.expanded = False
            if self._collapsed_pos:
                self.x, self.y = self._collapsed_pos
        self.cfg["x"], self.cfg["y"] = self.x, self.y
        self.setup_window()
        self.save_cfg()
        self.render()
        self.schedule_rotate()

    def apply_layer(self):
        if self.surface is None:
            self.surface = LayeredSurface(self.hwnd, self.w, self.h)
        if self.cfg.get("on_top"):
            pin_on_top(self.hwnd)
        else:
            pin_to_desktop(self.hwnd)

    def save_cfg(self):
        # büyütülmüş kartın geçici konumu değil, küçük kartın konumu kaydedilir
        pos = self._collapsed_pos if getattr(self, "expanded", False) and self._collapsed_pos \
            else (self.x, self.y)
        self.cfg["x"], self.cfg["y"] = pos
        save_json(CONFIG_PATH, self.cfg)

    def toggle_hidden(self):
        self.hidden = not self.hidden
        user32.ShowWindow(self.hwnd, 0 if self.hidden else 4)  # SW_HIDE / SW_SHOWNOACTIVATE
        if not self.hidden:
            self.apply_layer()
            self.render()

    # ---- arka plan işleri
    def run_bg(self, fn, on_ok, on_err=None):
        def work():
            try:
                res = fn()
                self.q.put(lambda: on_ok(res))
            except Exception as ex:
                log("hata:", repr(ex))
                if on_err:
                    self.q.put(lambda ex=ex: on_err(ex))
        threading.Thread(target=work, daemon=True).start()

    def poll(self):
        while True:
            try:
                fn = self.q.get_nowait()
            except queue.Empty:
                break
            try:
                fn()
            except Exception:
                log(traceback.format_exc())
        tray = self.tray
        while tray is not None and tray.events:
            ev = tray.events.popleft()
            try:
                if ev == "left":
                    self.toggle_hidden()
                elif ev == "right":
                    self.show_menu()
            except Exception:
                log(traceback.format_exc())
            if self.tray is not tray:  # menüden tepsi kapatıldı
                break
        hk = self.hotkey_win
        while hk is not None and hk.events:
            hk.events.popleft()
            try:
                self.peek()
            except Exception:
                log(traceback.format_exc())
        try:
            self.hover_check()
        except Exception:
            log(traceback.format_exc())
        self.root.after(100, self.poll)

    # ---- tepsi ikonu
    def create_tray(self):
        try:
            self.tray = Tray()
            self._tray_key = None
            self.update_tray()
        except Exception:
            self.tray = None
            log(traceback.format_exc())

    def set_tray(self, on):
        self.cfg["tray"] = on
        if on:
            if not self.tray:
                self.create_tray()
        else:
            if self.tray:
                self.tray.close()
                self.tray = None
            # tepsi yokken widget görünür ve tıklanabilir olmalı (menüye tek yol)
            if self.hidden:
                self.toggle_hidden()
            if self.cfg.get("click_through"):
                self.cfg["click_through"] = False
                set_click_through(self.hwnd, False)
        self.save_cfg()

    def tick(self):
        """Dakika başında: saat / gece-gündüz / tema güncellenir, sabah özeti denetlenir."""
        self.render()
        try:
            self.morning_check()
        except Exception:
            log(traceback.format_exc())
        self.restart_if_exe_changed()
        self.root.after(60_000 - int(time.time() * 1000) % 60_000 + 200, self.tick)

    def refresh(self):
        """Tüm şehirleri (önce görüntüleneni) ve MGM uyarılarını günceller."""
        if self.refresh_job:
            self.root.after_cancel(self.refresh_job)
        ms = max(1, int(self.cfg.get("refresh_min", 10))) * 60_000
        self.refresh_job = self.root.after(ms, self.refresh)
        if self.loading:
            return
        self.loading = True
        i = self.idx
        locs = [dict(l) for l in self.locs[i:] + self.locs[:i]]
        self.render()

        def work():
            for loc in locs:
                try:
                    data = fetch_weather(loc)
                    self.q.put(lambda data=data: self._got(data))
                except Exception as ex:
                    log("hata:", loc_title(loc), repr(ex))
                    self.q.put(lambda loc=loc, ex=ex: self._failed(loc, ex))
            if any(l.get("src") != "om" for l in locs):
                try:
                    al = fetch_alerts()
                    self.q.put(lambda al=al: setattr(self, "alerts", al))
                except Exception as ex:
                    log("uyarılar alınamadı:", repr(ex))
            self.q.put(self._done_loading)
        threading.Thread(target=work, daemon=True).start()

    def refresh_one(self, loc):
        self.run_bg(lambda: fetch_weather(dict(loc)), self._got,
                    lambda ex: self._failed(loc, ex))

    def _got(self, data):
        k = str(data["merkezId"])
        self.datas[k] = data
        self.errors.pop(k, None)
        try:  # olay günlüğü: önceki ölçümle karşılaştır
            loc = next((l for l in self.locs if loc_key(l) == k), None)
            if loc is not None:
                self.events.observe(loc, data)
                self.events.save()  # yeni olay olmasa da durum (son ölçüm) saklanır
        except Exception:
            log(traceback.format_exc())
        if self.multi or k == loc_key(self.loc):
            self.render()

    def _failed(self, loc, ex):
        k = loc_key(loc)
        self.errors[k] = T("Bağlantı yok") if isinstance(ex, OSError) else T("Veri alınamadı")
        if self.multi or k == loc_key(self.loc):
            self.render()

    def _done_loading(self):
        self.loading = False
        self.save_cache()
        try:
            if any([self.events.observe_alerts(l, self.alerts) for l in self.locs]):
                self.events.save()
        except Exception:
            log(traceback.format_exc())
        if self.errors:  # başarısız şehir varsa 1 dk sonra yeniden dene
            if self.refresh_job:
                self.root.after_cancel(self.refresh_job)
            self.refresh_job = self.root.after(60_000, self.refresh)
        self.render()
        try:
            self.check_notifications()
        except Exception:
            log(traceback.format_exc())

    # ---- bildirimler
    def clear_events(self):
        self.events.clear()

    # ---- bildirimde uygulama adı: Başlat kısayolu + WinRT
    def shortcut_allowed(self):
        """Kısayol yalnızca derlenmiş exe için (ya da testte özel adla) oluşturulur."""
        return not PROFILE and (FROZEN or SHORTCUT_NAME != APP_NAME)

    def apply_start_shortcut(self):
        """Ayara göre Başlat menüsü kısayolunu oluşturur / günceller / kaldırır."""
        if not self.shortcut_allowed():
            return
        lnk = start_shortcut_path()
        old = os.path.join(os.path.dirname(lnk), "Hava Durumu.lnk")  # 1.4.1 ve öncesinin adı
        if FROZEN and os.path.exists(old):
            try:
                os.remove(old)
                self.cfg["shortcut_target"] = ""
            except OSError:
                pass
        try:
            if not self.cfg.get("start_shortcut", True):
                if self.cfg.get("shortcut_target") and os.path.exists(lnk):
                    os.remove(lnk)  # yalnızca bu uygulamanın oluşturduğu kısayol silinir
                self.cfg["shortcut_target"] = ""
                self.save_cfg()
                return
            cmd = launch_cmd()
            target = subprocess.list2cmdline(cmd)
            if os.path.exists(lnk) and self.cfg.get("shortcut_target") == target:
                return  # güncel
            icon = cmd[0] if FROZEN else os.path.join(APP_DIR, "icon.ico")
            create_start_shortcut(lnk, cmd[0], subprocess.list2cmdline(cmd[1:]), AUMID,
                                  icon if os.path.exists(icon) else None)
            self.cfg["shortcut_target"] = target
            self.save_cfg()
        except Exception:
            log(traceback.format_exc())

    def toast(self, title, text, code=None):
        """WinRT bildirimi dener; koşullar uygun değilse False (çağıran tepsi balonuna düşer)."""
        if not (self.shortcut_allowed() and self.cfg.get("start_shortcut", True)):
            return False
        lnk = start_shortcut_path()
        try:
            # Windows yeni kısayolu birkaç saniyede işler; o sürede bildirim ekranda açılmaz
            if not os.path.exists(lnk) or time.time() - os.path.getmtime(lnk) < 30:
                return False
            folder = os.path.join(tempfile.gettempdir(), "HavaDurumuWidget")
            os.makedirs(folder, exist_ok=True)
            png = os.path.join(folder, f"toast_{code or 'PB'}.png")
            if not os.path.exists(png):
                make_icon(code or "PB", 96, False).save(png)
            send_toast(title, text, png)
            return True
        except Exception as ex:
            log("WinRT bildirimi gönderilemedi, tepsi balonu kullanılacak:", repr(ex))
            return False

    def notify(self, title, text, code=None):
        """Windows bildirimi; code: bildirimde gösterilecek hava ikonu.
        Önce WinRT (başlıkta "Hava Durumu" yazar); olmazsa tepsi balonu. Balon bir tepsi
        ikonuna bağlıdır: tepsi kapalıysa geçici bir ikon eklenip sonra kaldırılır."""
        if self.toast(title, text, code):
            return True
        size = max(32, user32.GetSystemMetrics(11))  # SM_CXICON
        img = make_icon(code or "PB", size, False)
        if self.tray:
            return self.tray.notify(title, text, img)
        try:
            tmp = Tray()
            tmp.set(make_icon(code or "PB", max(16, user32.GetSystemMetrics(49)), False),
                    APP_NAME)
            res = tmp.notify(title, text, img)
        except Exception:
            log(traceback.format_exc())
            return False
        self._tmp_trays = getattr(self, "_tmp_trays", []) + [tmp]

        def drop():
            tmp.close()
            if tmp in self._tmp_trays:
                self._tmp_trays.remove(tmp)
        self.root.after(12_000, drop)
        return res

    def check_notifications(self):
        """Yağış yaklaşması, sıcaklık eşiği ve yeni MGM uyarıları için bildirim üretir."""
        if self.in_quiet():  # sessiz saatler: hiçbir şey "görüldü" sayılmaz, sonra gelir
            return []
        now = datetime.now(timezone.utc)
        msgs = []
        seen = list(self.cfg.get("seen_alerts") or [])
        seen_n = len(seen)
        lo, hi = self.cfg.get("alert_low"), self.cfg.get("alert_high")
        for loc in self.locs:
            k = loc_key(loc)
            cur = (self.datas.get(k) or {}).get("cur")
            if not cur:
                continue
            name = loc_title(loc)
            if self.cfg.get("notify_rain") and cur.get("hadiseKodu") not in WET:
                for e in self.datas[k].get("hourly") or []:
                    try:
                        t = parse_utc(e["tarih"])
                    except Exception:
                        continue
                    if now < t <= now + timedelta(hours=3) and e.get("hadise") in WET:
                        if time.time() - self.notified.get("rain:" + k, 0) > 6 * 3600:
                            self.notified["rain:" + k] = time.time()
                            msgs.append((T('{}: yağış yaklaşıyor').format(name),
                                         T('{:%H:%M} civarında yağış bekleniyor ({}).').format(t.astimezone(), HADISE.get(e['hadise'], T('Yağışlı'))), e["hadise"]))
                        break
            t = cur.get("sicaklik")
            state = None
            if ok(t):
                state = "low" if lo is not None and t <= lo else \
                    "high" if hi is not None and t >= hi else None
            if state != self.thr_state.get(k):
                self.thr_state[k] = state
                if state == "low":
                    msgs.append((f"{name}: {fmt_temp(t)}",
                                 T('Sıcaklık alt eşiğin ({}) altına düştü.').format(fmt_temp(lo)), "SGK"))
                elif state == "high":
                    msgs.append((f"{name}: {fmt_temp(t)}",
                                 T('Sıcaklık üst eşiğin ({}) üstüne çıktı.').format(fmt_temp(hi)), "SCK"))
            uvn = (self.datas[k].get("extra") or {}).get("uv_now")
            day = datetime.now().date().isoformat()
            if self.cfg.get("notify_uv") and uvn is not None and uvn >= 8 \
                    and self.notified.get("uv:" + k) != day:
                self.notified["uv:" + k] = day
                msgs.append((T('{}: UV çok yüksek').format(name),
                             T('UV indeksi {:.0f} ({}). Güneş kremi ve şapka önerilir.').format(uvn, uv_level(uvn)), "SCK"))
            if self.cfg.get("notify_alerts"):
                for a in alerts_for(loc, self.alerts, now):
                    sid = f"{a['no']}:{k}"
                    if sid not in seen:
                        seen.append(sid)
                        cname = ALERT_COLORS[a["color"]][0]
                        msgs.append((T('{}: {} uyarı').format(name, cname),
                                     ", ".join(a["kinds"]) or a["text"][:200],
                                     cur.get("hadiseKodu")))
        if len(seen) != seen_n:
            self.cfg["seen_alerts"] = seen[-200:]
            self.save_cfg()
        for i, (title, text, code) in enumerate(msgs[:4]):
            self.root.after(i * 5000, lambda a=title, b=text, c=code: self.notify(a, b, c))
        return msgs

    def in_quiet(self):
        c = self.cfg
        if not c.get("quiet_on"):
            return False
        a, b = parse_hhmm(c.get("quiet_start")), parse_hhmm(c.get("quiet_end"))
        if not a or not b:
            return False
        n = datetime.now()
        cur, s, e = n.hour * 60 + n.minute, a[0] * 60 + a[1], b[0] * 60 + b[1]
        return s <= cur < e if s < e else (cur >= s or cur < e)

    def morning_message(self, loc):
        """Sabah özeti ve/veya şemsiye-mont hatırlatması: (başlık, metin, ikon kodu) ya da None."""
        data = self.datas.get(loc_key(loc))
        if not data or not data.get("cur"):
            return None
        cur, extra = data["cur"], data.get("extra") or {}
        tz = data_tz(data)
        days = self.daily_rows(data)
        today = days[0] if days and days[0][0] == datetime.now(tz).date() else None
        code = today[1] if today else cur.get("hadiseKodu")
        lines, tips = [], []
        if self.cfg.get("morning_on"):
            s = HADISE.get(code, "")
            if today:
                s += f", {tint(today[2])}° / {tint(today[3])}°"
            lines.append(s)
            if extra.get("rain_prob") is not None:
                lines.append(T('Yağış ihtimali %{:.0f}').format(extra['rain_prob']))
            if extra.get("european_aqi") is not None:
                lines.append(T('Hava kalitesi: {}').format(aqi_level(extra['european_aqi'])[0]))
        if self.cfg.get("reminders_on"):
            now = datetime.now(timezone.utc)
            wet = (extra.get("rain_prob") or 0) >= 50 or code in WET
            for lt, hc, _, _ in self.hourly_rows(data, loc, now):
                if lt.date() == datetime.now(tz).date() and 7 <= lt.hour <= 22 and hc in WET:
                    wet = True
            if wet:
                tips.append(T("☂ Şemsiyeni al"))
            fl = cur.get("hissedilenSicaklik")
            if (today and today[2] <= 5) or (ok(fl) and fl <= 3):
                tips.append(T("🧥 Kalın giyin"))
            if today and today[3] >= 33:
                tips.append(T("Bol su iç, öğle güneşinden kaçın"))
            if (extra.get("uv_max") or 0) >= 6:
                tips.append(T("UV yüksek, güneş kremi sür"))
        if not lines and not tips:
            return None
        title = (T('Günaydın — {}').format(loc_title(loc)) if self.cfg.get("morning_on")
                 else T('Hatırlatma — {}').format(loc_title(loc)))
        return title, "\n".join(lines + tips), code

    def morning_check(self):
        """Ayarlanan saatte (3 saat içinde) günde bir kez sabah özeti gönderir."""
        c = self.cfg
        if not (c.get("morning_on") or c.get("reminders_on")):
            return
        hm = parse_hhmm(c.get("morning_time", "07:30"))
        if not hm:
            return
        now = datetime.now()
        start = now.replace(hour=hm[0], minute=hm[1], second=0, microsecond=0)
        today = now.date().isoformat()
        if c.get("last_morning") == today or not (start <= now < start + timedelta(hours=3)):
            return
        if self.in_quiet():
            return
        msg = self.morning_message(self.locs[0])  # listedeki ilk şehir: "ev"
        if not msg:
            return  # veri henüz gelmedi: bir sonraki dakikada yeniden dener
        c["last_morning"] = today
        self.save_cfg()
        self.notify(*msg)

    def update_tray(self):
        if not self.tray:
            return
        loc = self.loc
        cur = (self.datas.get(loc_key(loc)) or {}).get("cur") or {}
        t = cur.get("sicaklik")
        text = f"{tint(t)}°" if ok(t) else "–"
        tip = loc_title(loc)
        if ok(t):
            tip += f"  {fmt_temp(t)}  {HADISE.get(cur.get('hadiseKodu'), '')}"
        key = (text, tip)
        if key == self._tray_key:
            return
        self._tray_key = key
        try:
            size = max(16, user32.GetSystemMetrics(49))  # SM_CXSMICON
            self.tray.set(tray_image(text, size), tip)
        except Exception:
            log(traceback.format_exc())

    # ---- şehirler
    def switch_to(self, i, user=False):
        n = len(self.locs)
        if n == 0:
            return
        i %= n
        if user:
            self.schedule_rotate()  # elle geçişte sayaç baştan başlasın
        if i == self.idx:
            return
        self.cfg["idx"] = i
        if user:
            self.save_cfg()
        self.render(fade=True)
        if loc_key(self.loc) not in self.datas and not self.loading:
            self.refresh_one(self.loc)

    def _cities_changed(self, fade=False):
        self.save_cfg()
        if self.multi:
            self.relayout()  # kart sayısı değişti: pencere boyutu da değişir
        else:
            self.render(fade=fade)
        self.schedule_rotate()

    def add_location(self, loc):
        if self.placeholder:
            self.cfg["locs"] = [loc]
            self.placeholder = False
        else:
            self.locs.append(loc)
            if len(self.locs) == 2 and not self.cfg.get("rotate_touched"):
                self.cfg["rotate"] = True
        self.cfg["idx"] = len(self.locs) - 1
        self._cities_changed(fade=True)
        if loc_key(loc) not in self.datas:
            self.refresh_one(loc)

    def remove_location(self, i):
        if len(self.locs) <= 1:
            return
        cur = self.idx
        loc = self.locs.pop(i)
        self.datas.pop(loc_key(loc), None)
        self.errors.pop(loc_key(loc), None)
        if cur > i or cur >= len(self.locs):
            self.cfg["idx"] = max(0, cur - 1)
        self.placeholder = False
        self.save_cache()
        self._cities_changed(fade=(cur == i))

    def rename_location(self, i, name):
        if name:
            self.locs[i]["name"] = name
        else:
            self.locs[i].pop("name", None)
        self.save_cfg()
        self.render()

    def move_location(self, i, j):
        cur = self.idx
        self.locs[i], self.locs[j] = self.locs[j], self.locs[i]
        self.cfg["idx"] = j if cur == i else i if cur == j else cur
        self.placeholder = False
        self.save_cfg()
        self.render()

    # ---- otomatik geçiş
    def schedule_rotate(self):
        if self.rotate_job:
            self.root.after_cancel(self.rotate_job)
            self.rotate_job = None
        if self.cfg.get("rotate") and len(self.locs) > 1 and not self.multi:
            sec = max(3, int(self.cfg.get("rotate_sec", 15)))
            self.rotate_job = self.root.after(sec * 1000, self._rotate_tick)

    def _rotate_tick(self):
        self.rotate_job = None
        if not self.cursor_over() and not self.expanded:  # fare üzerindeyken / kart büyükken bekle
            self.switch_to(self.idx + 1)
        self.schedule_rotate()

    def cursor_over(self):
        """Fare imleci şu an widget'ın üzerinde mi? (Enter/Leave olaylarına güvenmeden)"""
        if self.hidden:
            return False
        pt = wintypes.POINT()
        if not user32.GetCursorPos(ctypes.byref(pt)):
            return False
        return self.x <= pt.x < self.x + self.w and self.y <= pt.y < self.y + self.h

    def set_rotate(self, on=None, sec=None):
        self.cfg["rotate_touched"] = True
        if on is not None:
            self.cfg["rotate"] = on
        if sec is not None:
            self.cfg["rotate_sec"] = sec
            if on is None:
                self.cfg["rotate"] = True  # süre seçmek özelliği de açar
        self.save_cfg()
        self.schedule_rotate()
        self.render()

    # ---- animasyon
    def schedule_anim(self):
        if self.anim_job:
            self.root.after_cancel(self.anim_job)
            self.anim_job = None
        if self.cfg.get("animate") or self.cfg.get("effects", True):
            if getattr(self, "_fx_on", False) and getattr(self, "_fx_hidden", False):
                self.anim_job = self.root.after(FX_MS_HIDDEN, self._anim_tick)
            elif not self._anim_needed:  # kartta hareket yok: ekran hızında boşuna uyanma
                self.anim_job = self.root.after(100, self._anim_tick)
            else:
                # sabit kare adımı: ms tam sayıya yuvarlansa da ortalama ekranın Hz değerinde kalır;
                # çizim kare süresini aşarsa hedef "şimdi"ye çekilir, gecikme birikmez
                self.next_ms()  # ekran değiştiyse Hz'i günceller
                now = time.perf_counter()
                ms = self.frame_ms
                if getattr(self, "_fx_on", False) and not getattr(self, "_fx_parts", None):
                    ms = max(ms, 1000 / FX_FPS)
                self._anim_t = max(getattr(self, "_anim_t", 0.0) + ms / 1000, now)
                self.anim_job = self.root.after(max(1, round((self._anim_t - now) * 1000)),
                                                self._anim_tick)

    def next_ms(self, spent=0.0):
        """Bir sonraki kareye kalan ms: ekranın bir kare süresi eksi bu karenin çizim süresi.
        Widget başka ekrana geçince o ekranın Hz değeri alınır."""
        mon = user32.MonitorFromWindow(self.hwnd, 2) if getattr(self, "hwnd", None) else None
        if mon != getattr(self, "_mon", 0):
            self._mon = mon
            self.set_hz(refresh_hz(mon))
        return max(1, round(self.frame_ms - spent * 1000))

    def set_hz(self, hz):
        global ANIM_FRAMES
        # shortcut: Hz yalnızca widget başka ekrana geçince okunur; ekranın hızı Windows ayarından
        # değiştirilirse uygulama yeniden açılana dek eski hız kalır, gerekirse WM_DISPLAYCHANGE dinlenmeli
        self.frame_ms = 1000 / hz
        if ANIM_FRAMES != round(ANIM_CYCLE * hz):
            ANIM_FRAMES = round(ANIM_CYCLE * hz)
            _icon_cache.clear()  # önbellekteki kare numaraları eski döngü uzunluğuna göre
            self.anim_frame = 0

    def _anim_tick(self):
        self.anim_job = None
        self._fx_hidden = getattr(self, "_fx_on", False) and not self.widget_seen()
        if (self._anim_needed and not self.fade_job and not self.hidden
                and not self._resize_job and not self._fx_hidden):  # büyüme sürerken / görünmezken bekle
            self.anim_frame = (self.anim_frame + 1) % ANIM_FRAMES
            now = time.perf_counter()
            fx_wait = self._fx_on and now < getattr(self, "_fx_next", 0.0)
            try:
                parts = self._fx_parts if fx_wait else getattr(self, "_anim_parts", None)
                if fx_wait and not parts:
                    pass  # efekt karesinin zamanı gelmedi, ikon da durağan
                elif parts and (self.surface.w, self.surface.h) == (self.w, self.h):
                    # hızlı yol: yalnızca ikonun alanı (kartın kalanı önbellekteki görüntüde aynı)
                    for base, code, isz, night, IS, theme, lx, ly, side, off in parts:
                        reg = base.crop((lx, ly, lx + side, ly + side))
                        m_ = (side - isz) // 2
                        reg = self.with_icon(reg, make_icon(code, isz, night, IS, self.anim_frame),
                                             (m_, m_), theme)
                        self.surface.show_at(reg, off[0] + lx, off[1] + ly)
                else:
                    img = self.draw()
                    self.last_img = img
                    self.surface.show(img)
                    # sabit adım: ekranın kare adımı 60'ın katı olmasa da ortalama FX_FPS
                    self._fx_next = max(getattr(self, "_fx_next", 0.0) + 1 / FX_FPS, now)
            except Exception:
                log(traceback.format_exc())
        self.schedule_anim()

    # ---- fare
    def on_press(self, e):
        self._pressed = True
        self.popup_hide()
        self._drag = (e.x_root - self.x, e.y_root - self.y)
        self._moved = False
        self._reorder = None
        # yan yana / alt alta: Shift + sürükle (ya da konum kilitliyken sürükle) kartları sıralar
        if self.multi and (e.state & 0x0001 or self.cfg.get("locked")):
            i = self.card_at(e.x, e.y)
            if i is not None:
                self._reorder, self._reordered = i, False
                self._reorder_key = loc_key(self.locs[i])
                self.render()

    def on_drag(self, e):
        if self._reorder is not None:
            i = self.card_at(e.x, e.y)
            if i is not None and i != self._reorder:
                cur_key = loc_key(self.loc)
                self.locs.insert(i, self.locs.pop(self._reorder))
                self.cfg["idx"] = next(n for n, l in enumerate(self.locs) if loc_key(l) == cur_key)
                self._reorder, self._reordered = i, True
                self.render()
            return
        if self.cfg.get("locked"):
            return
        nx, ny = e.x_root - self._drag[0], e.y_root - self._drag[1]
        if abs(nx - self.x) + abs(ny - self.y) > 2 or self._moved:
            self._moved = True
            if self.cfg.get("snap"):
                l, t, r, b = work_area(nx + self.w // 2, ny + self.h // 2)
                th = self.px(14)
                if abs(nx - l) < th:
                    nx = l
                elif abs(nx + self.w - r) < th:
                    nx = r - self.w
                if abs(ny - t) < th:
                    ny = t
                elif abs(ny + self.h - b) < th:
                    ny = b - self.h
            if self.expanded and self._collapsed_pos:  # küçülünce de yeni yerinde olsun
                cx, cy = self._collapsed_pos
                self._collapsed_pos = (cx + nx - self.x, cy + ny - self.y)
            self.x, self.y = nx, ny
            move_window(self.hwnd, nx, ny)

    def on_release(self, e):
        self._pressed = False
        if self._reorder is not None:
            done = self._reordered
            self._reorder = self._reorder_key = None
            self._reordered = False
            if done:
                self.save_cfg()
            self.render()
            return
        if self._dbl:  # çift tıklamanın ikinci bırakışı
            self._dbl = False
            return
        if getattr(self, "_moved", False):
            self.root.geometry(f"+{self.x}+{self.y}")
            self.save_cfg()
        else:
            for (x1, y1, x2, y2), action in self.hits:
                if x1 <= e.x <= x2 and y1 <= e.y <= y2:
                    self.do_action(action)
                    break
            else:  # boş alana tıklama: çift tıklama değilse büyüt / sayfa değiştir
                if self._click_job:
                    self.root.after_cancel(self._click_job)
                self._click_job = self.root.after(260, self.click_action)
        if not self.cfg.get("on_top"):
            keep_bottom(self.hwnd)

    def on_double(self, e):
        if self._click_job:
            self.root.after_cancel(self._click_job)
            self._click_job = None
        self._dbl = True
        self.refresh()

    def click_action(self):
        self._click_job = None
        if self.expanded:
            if self.hover_expand_on():  # bu kipte küçülme fareyle yönetilir
                return
            self.expand(False)
            return
        if self.hover_expand_on() and self.base_layout in SMALL_LAYOUTS:
            self._hover_exp = True  # beklemeden büyüt; fare çekilince küçülür
            self._hover_since = None
            self.expand(True)
            return
        base, act = self.base_layout, self.cfg.get("mini_click", "expand")
        if base not in SMALL_LAYOUTS:
            return
        if act == "expand":
            self.expand(True)
        elif act == "page" and base in ("mini", "miniplus"):
            self.next_page()

    def pages_on(self):
        return self.cfg.get("mini_click") == "page" or self.cfg.get("wheel_action") == "page"

    def next_page(self, delta=1):
        self.mini_page = (self.mini_page + delta) % 3
        self.render(fade=True)

    def do_action(self, action):
        kind = action[0]
        if kind == "refresh":
            self.refresh()
        elif kind == "city":
            self.switch_to(action[1], user=True)
        elif kind == "alert":
            al = alerts_for(action[1], self.alerts)
            if al:
                AlertDialog(self, action[1], al)

    def on_wheel(self, e):
        step = -1 if e.delta > 0 else 1
        if (self.cfg.get("wheel_action") == "page" and self.base_layout in ("mini", "miniplus")
                and not self.expanded):
            self.next_page(step)
            return
        if len(self.locs) > 1 and not self.multi:
            self.switch_to(self.idx + step, user=True)

    # ---- tıklayınca büyüme
    def _resize_window(self, x, y, w, h):
        self.x, self.y, self.w, self.h = int(x), int(y), int(w), int(h)
        self.root.geometry(f"{self.w}x{self.h}+{self.x}+{self.y}")
        move_window(self.hwnd, self.x, self.y)
        if self.surface:
            self.surface.close()
        self.surface = LayeredSurface(self.hwnd, self.w, self.h)
        self.last_img = None

    def expand(self, on):
        """Küçük kartı Kompakt boya büyütür / geri küçültür (yerinde, animasyonlu)."""
        if on == self.expanded or (on and (self.base_layout not in SMALL_LAYOUTS or self.hidden)):
            return
        if self._exp_job:
            self.root.after_cancel(self._exp_job)
            self._exp_job = None
        self.popup_hide()
        if self.fade_job:  # şehir geçişinin solması büyüme ile aynı pencereye çizmesin
            self.root.after_cancel(self.fade_job)
            self.fade_job = None
        old_img, old_rect = self.last_img, (self.x, self.y, self.w, self.h)
        if on:
            self._collapsed_pos = (self.x, self.y)
        self.expanded = on
        nw, nh = self.size_px()
        if on:  # ekrandan taşacaksa sola / yukarı doğru büyür
            l, t_, r, b = work_area(self.x + self.w // 2, self.y + self.h // 2)
            nx, ny = max(l, min(self.x, r - nw)), max(t_, min(self.y, b - nh))
        else:
            nx, ny = self._collapsed_pos or (self.x, self.y)
        self.__dict__.setdefault("_static", {}).clear()
        sw, sh = self.w, self.h
        self.w, self.h = nw, nh
        try:
            new_img = self.draw()
        finally:
            self.w, self.h = sw, sh
        if old_img is None or old_img.size != (sw, sh):
            self._resize_window(nx, ny, nw, nh)
            self.render()
        else:
            # küçülme daha kısa: fare çekilince kart hemen toparlansın
            self._animate_resize(old_img, old_rect, new_img, (nx, ny, nw, nh),
                                 duration=0.18 if on else 0.13)
        if not on:
            self._hover_exp = False
        if on and not self.hover_expand_on():  # tıklayarak büyüdüyse: 6 sn sonra kendiliğinden küçül
            self._exp_idle = 0
            self._exp_job = self.root.after(1000, self._exp_tick)

    def _exp_tick(self):
        """Tıklayarak büyütülmüş kart, fare üzerinde değilken 6 saniye sonra kendiliğinden küçülür."""
        self._exp_job = None
        if not self.expanded or self.hover_expand_on():
            return
        busy = self.cursor_over() or MenuPanel._inst is not None
        self._exp_idle = 0 if busy else self._exp_idle + 1
        if self._exp_idle >= 6:
            self.expand(False)
            return
        self._exp_job = self.root.after(1000, self._exp_tick)

    def _timer_fine(self, on):
        """Windows zamanlayıcısını animasyon boyunca 1 ms çözünürlüğe alır (varsayılan 15,6 ms:
        "16 ms sonra" istenen kare ~31 ms sonra gelir, animasyon ~30 kare/sn'de kalır)."""
        if on == getattr(self, "_fine_timer", False):
            return
        try:
            winmm = ctypes.windll.winmm
            (winmm.timeBeginPeriod if on else winmm.timeEndPeriod)(1)
            self._fine_timer = on
        except Exception:
            pass

    def _animate_resize(self, old_img, old_rect, new_img, new_rect, duration=0.2):
        """Büyük kart, küçük kartın yerinden açılarak (ya da kapanarak) görünür; küçük kart solar.
        Süreye bağlıdır: yavaş bir kare animasyonu uzatmaz, yalnızca bir kare atlanır."""
        if self._resize_job:
            self.root.after_cancel(self._resize_job)
            self._resize_job = None
        ox, oy, ow, oh = old_rect
        nx, ny, nw, nh = new_rect
        ux, uy = min(ox, nx), min(oy, ny)
        uw, uh = max(ox + ow, nx + nw) - ux, max(oy + oh, ny + nh) - uy
        self._resize_window(ux, uy, uw, uh)
        grow = nw * nh >= ow * oh
        big, small = (new_img, old_img) if grow else (old_img, new_img)
        spos = (ox - ux, oy - uy) if grow else (nx - ux, ny - uy)
        R = self.px(22 * float(self.cfg.get("radius", 22)) / 22)
        big_a, small_a = big.getchannel("A"), small.getchannel("A")
        empty = Image.new("RGBA", (uw, uh), (0, 0, 0, 0))
        t0 = tf = time.perf_counter()
        self._anim_frames = 0  # ölçüm için: gösterilen kare sayısı

        def frame():
            nonlocal tf
            self._resize_job = None
            tf = time.perf_counter()
            p = min(1.0, (time.perf_counter() - t0) / duration)
            e = 1 - (1 - p) ** 3  # hızlı başlayıp yavaşlayan geçiş
            rx, ry = ox + (nx - ox) * e, oy + (ny - oy) * e
            rw, rh = max(2, int(ow + (nw - ow) * e)), max(2, int(oh + (nh - oh) * e))
            canvas = empty.copy()
            box = (0, 0, min(rw, big.width), min(rh, big.height))
            part = big.crop(box)
            part.putalpha(ImageChops.multiply(big_a.crop(box),
                                              round_mask_fast(part.width, part.height, R)))
            canvas.alpha_composite(part, (int(rx - ux), int(ry - uy)))
            a = (1 - e) if grow else e
            if a > 0.02:
                sm = small.copy()
                sm.putalpha(small_a.point(lambda v: int(v * a)))
                canvas.alpha_composite(sm, spos)
            self.surface.show(canvas)
            self._anim_frames += 1
            if p < 1.0:
                self._resize_job = self.root.after(self.next_ms(time.perf_counter() - tf), frame)
            else:
                self._resize_window(nx, ny, nw, nh)
                self.render()
        frame()

    # ---- menü
    def show_menu(self, e=None):
        """Sağ tık menüsü (kendi çizdiğimiz sayfalı panel). Tepsiden açılınca imleç konumunda."""
        self.popup_hide()
        if e is not None:
            x, y = e.x_root, e.y_root
        else:
            pt = wintypes.POINT()
            user32.GetCursorPos(ctypes.byref(pt))
            x, y = pt.x, pt.y
        MenuPanel.toggle(self, x, y)

    def units_changed(self):
        self.apply_units()
        self._tray_key = None
        self.render()

    def anim_changed(self):
        self.schedule_anim()
        self.render()

    # ---- güncelleme
    def ask(self, kind, title, text):
        """Her zaman üstte açılan mesaj kutusu (widget penceresi masaüstüne sabit olduğu için)."""
        top = tk.Toplevel(self.root)
        top.withdraw()
        top.attributes("-topmost", True)
        try:
            return getattr(messagebox, kind)(title, text, parent=top)
        finally:
            top.destroy()

    def cleanup_old_exe(self, tries=12):
        """Güncellemeden kalan eski dosyaları siler (eski süreçler kapanana kadar birkaç kez dener):
        tek dosya: HavaDurumu.exe.old; klasör kipi: _internal.old, _update, _update.zip."""
        if not FROZEN or PROFILE:
            return
        left = []
        for p in (sys.executable + ".old", os.path.join(APP_DIR, "_internal.old"),
                  os.path.join(APP_DIR, "_update"), os.path.join(APP_DIR, "_update.zip")):
            if not os.path.exists(p):
                continue
            try:
                shutil.rmtree(p) if os.path.isdir(p) else os.remove(p)
            except OSError:
                left.append(p)
        if left and tries > 0:
            self.root.after(15_000, lambda: self.cleanup_old_exe(tries - 1))

    def auto_update_check(self):
        if PROFILE:  # güncellemeyi yalnızca ana widget yapar
            return
        if self.cfg.get("auto_update"):
            self.check_update()
        self.root.after(24 * 3600 * 1000, self.auto_update_check)

    def check_update(self, manual=False, install=False):
        """manual: sonucu pencereyle bildir; install: yeni sürüm varsa sormadan kur."""
        def got(rel):
            # kurulum dosyası varsa onunla; yoksa klasör kipi zip ile, tek dosya exe ile
            newer = parse_version(rel["version"]) > parse_version(APP_VERSION) and \
                (rel.get("setup_url") or (rel.get("zip_url") if is_onedir() else rel["url"]))
            self.update_info = rel if newer else None
            if newer:
                if install:
                    self.install_update()
                elif manual:
                    self.prompt_update()
                elif self.cfg.get("seen_update") != rel["version"]:  # sürüm başına bir bildirim
                    self.cfg["seen_update"] = rel["version"]
                    self.save_cfg()
                    self.notify(T("{}: yeni sürüm").format(APP_NAME),
                                T('Sürüm {} hazır. Kurmak için tepsi ikonuna sağ tıklayıp Güncelleme menüsünü aç.').format(rel['version']))
            elif manual:
                self.ask("showinfo", T("Güncelleme"),
                         T('En güncel sürümü kullanıyorsun (sürüm {}).').format(APP_VERSION))

        def failed(ex):
            if manual:
                self.ask("showwarning", T("Güncelleme"), T('Güncelleme denetlenemedi:\n{}').format(ex))

        self.run_bg(latest_release, got, failed)

    def prompt_update(self):
        rel = self.update_info
        if not rel or self.updating:
            return
        notes = rel["notes"][:500] + ("…" if len(rel["notes"]) > 500 else "")
        if self.ask("askyesno", T("Güncelleme"),
                    T('Yeni sürüm: {}  (kurulu sürüm: {})\n\n').format(rel['version'], APP_VERSION)
                    + (notes + "\n\n" if notes else "")
                    + T("Şimdi indirilip kurulsun mu? Widget yeniden başlayacak.")):
            self.install_update()

    def install_update(self):
        rel = self.update_info
        if not FROZEN:  # .py olarak çalışırken kendini değiştiremez: sürüm sayfasını aç
            webbrowser.open(rel.get("page") or f"https://github.com/{UPDATE_REPO}/releases")
            return
        self.updating = True
        if rel.get("setup_url"):
            self.install_update_setup(rel)
            return
        if is_onedir():
            self.install_update_onedir(rel)
            return
        exe = sys.executable
        new = exe + ".new"

        def done(_):
            try:
                swap_exe(new, exe)
            except OSError as ex:
                self.updating = False
                self.ask("showwarning", T("Güncelleme"), T('Dosya değiştirilemedi:\n{}').format(ex))
                return
            self.save_cfg()
            if self.tray:
                self.tray.close()
                self.tray = None
            if self.hotkey_win:
                self.hotkey_win.close()
            # yeni sürüm bu süreç kapanana kadar bekler (aynı anda iki kopya çalışmasın)
            spawn(launch_cmd(PROFILE, "--wait-pid", str(os.getpid())))
            self.root.destroy()

        def failed(ex):
            self.updating = False
            try:
                os.remove(new)
            except OSError:
                pass
            self.ask("showwarning", T("Güncelleme"), T('İndirme başarısız:\n{}').format(ex))

        self.run_bg(lambda: download_update(rel, new), done, failed)

    def install_update_setup(self, rel):
        """Kurulum dosyasını indirip sessiz kipte bu klasöre kurar. Kurulum, kapanan widget'ı
        bekler (Restart Manager), dosyaları değiştirir ve widget'ı yeniden başlatır."""
        setup = os.path.join(tempfile.gettempdir(), UPDATE_SETUP)

        def done(_):
            self.save_cfg()
            stop_other_instances()
            if self.tray:
                self.tray.close()
                self.tray = None
            if self.hotkey_win:
                self.hotkey_win.close()
            spawn([setup, "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/CLOSEAPPLICATIONS",
                   f"/DIR={APP_DIR}"])
            self.root.destroy()

        def failed(ex):
            self.updating = False
            try:
                os.remove(setup)
            except OSError:
                pass
            self.ask("showwarning", T("Güncelleme"), T('Güncelleme indirilemedi:\n{}').format(ex))

        self.run_bg(lambda: download_update(rel, setup, "setup"), done, failed)

    def install_update_onedir(self, rel):
        """Klasör kipi: zip indirilip _update'e açılır; yeni exe, bu süreç kapanınca dosyaları
        yerine taşır (çalışan _internal kilitli olduğu için değişimi kendisi yapamaz)."""
        zip_path, stage = os.path.join(APP_DIR, "_update.zip"), os.path.join(APP_DIR, "_update")

        def work():
            download_update(rel, zip_path, "zip")
            extract_update(zip_path, stage)
            os.remove(zip_path)
            return stage

        def done(stage_):
            self.save_cfg()
            stop_other_instances()
            if self.tray:
                self.tray.close()
                self.tray = None
            if self.hotkey_win:
                self.hotkey_win.close()
            spawn([os.path.join(stage_, "HavaDurumu.exe"), "--apply-update", APP_DIR,
                   "--wait-pid", str(os.getpid())])
            self.root.destroy()

        def failed(ex):
            self.updating = False
            for p in (zip_path, stage):
                try:
                    shutil.rmtree(p) if os.path.isdir(p) else os.remove(p)
                except OSError:
                    pass
            self.ask("showwarning", T("Güncelleme"), T('Güncelleme indirilemedi:\n{}').format(ex))

        self.run_bg(work, done, failed)

    # ---- üzerine gelince ayrıntı kartı
    def card_at(self, x, y):
        """Pencere içindeki noktanın hangi şehir kartına düştüğü."""
        if not self.multi:
            return self.idx
        cw, ch, gap = self.px(MINI_W), self.px(MINI_H), self.px(MULTI_GAP)
        i = int(x // (cw + gap)) if self.layout == "row" else int(y // (ch + gap))
        return i if 0 <= i < self.multi_n else None

    def hover_expand_check(self):
        """Küçük kartlarda "üzerine gelince büyüt": imleç 0,25 sn kalınca Kompakt boya açılır,
        0,3 sn dışarıda kalınca küçülür. Bu kip etkinse True döner (ayrıntı kartı açılmaz)."""
        if not self.hover_expand_on():
            return False
        if self._pressed and not (user32.GetAsyncKeyState(0x01) & 0x8000):
            # fare tuşu bırakılmış ama bırakma olayı gelmemiş (pencere boyutu değişirken olur):
            # yoksa üzerine gelme denetimi sonsuza kadar duraklar
            self._pressed = False
        if self._resize_job or self._pressed or self.hidden or self.updating \
                or MenuPanel._inst is not None:
            self._hover_since = self._leave_since = None
            return True
        self.popup_hide()
        over = self.cursor_on_widget()
        now = time.time()
        if not self.expanded:
            self._leave_since = None
            if not over:
                self._hover_armed, self._hover_since = True, None
            elif self._hover_armed:
                if self._hover_since is None:
                    self._hover_since = now
                elif now - self._hover_since >= 0.25:
                    self._hover_since = None
                    self._hover_exp = True
                    self.expand(True)
            return True
        # büyük kart (üzerine gelerek ya da tıklayarak büyümüş olsun): imleç çıkınca küçül
        self._hover_since = self._leave_since = None
        if not over:  # imleç çıktığı anda küçül (bekleme yok)
            # imleç küçük kartın üstündeyse (büyük kart ekran kenarında kaymışsa olabilir)
            # bir kez dışarı çıkana kadar yeniden büyüme; değilse hemen yeniden büyüyebilir
            self._hover_armed = not self.cursor_in_small()
            self.expand(False)
        return True

    def _hover_tick(self):
        """Üzerine gelince büyüt kipinde imleç 25 ms'de bir denetlenir (genel döngü 100 ms'de bir
        çalışır; çıkışı o kadar geç fark etmek küçülmeyi gecikmeli gösteriyordu)."""
        try:
            if self.hover_expand_on():
                self.hover_expand_check()
        except Exception:
            log(traceback.format_exc())
        self.root.after(25, self._hover_tick)

    def cursor_on_widget(self):
        """İmleç widget'ın üstünde mi? Yalnızca dikdörtgene bakmak yetmez: masaüstündeki widget
        büyüyünce bir kısmı başka pencerelerin altında kalabilir; imleç o pencerenin üstüne
        çekilince de "kartın içinde" sayılır ve kart büyük kalırdı. Bu yüzden o noktada en üstte
        görünen pencereye bakılır (kartın saydam köşeleri de dışarı sayılır)."""
        if self.hidden:
            return False
        pt = wintypes.POINT()
        if not user32.GetCursorPos(ctypes.byref(pt)):
            return False
        if not (self.x <= pt.x < self.x + self.w and self.y <= pt.y < self.y + self.h):
            return False
        h = user32.WindowFromPoint(pt)
        return bool(h) and user32.GetAncestor(h, 2) == self.hwnd

    def widget_seen(self):
        """Widget ekranda görünüyor mu? Kartın beş noktasında en üstteki pencereye bakılır;
        hiçbiri bu widget değilse (başka pencerelerin altında) efektler çizilmez."""
        if self.hidden:
            return False
        m = max(4, self.w // 6)
        for fx_, fy_ in ((0.5, 0.5), (0, 0), (1, 0), (0, 1), (1, 1)):
            x = self.x + m + int((self.w - 2 * m) * fx_)
            y = self.y + max(4, self.h // 6) + int((self.h - 2 * max(4, self.h // 6)) * fy_)
            h = user32.WindowFromPoint(wintypes.POINT(x, y))
            if h and user32.GetAncestor(h, 2) == self.hwnd:
                return True
        return False

    def hover_expand_on(self):
        return (self.base_layout in SMALL_LAYOUTS
                and self.cfg.get("mini_hover", "expand") == "expand")

    def cursor_in_small(self):
        """İmleç küçük kartın (büyütülmeden önceki) alanında mı?"""
        pt = wintypes.POINT()
        if not user32.GetCursorPos(ctypes.byref(pt)):
            return False
        x, y = self._collapsed_pos if (self.expanded and self._collapsed_pos) else (self.x, self.y)
        w, h = self.px(LAYOUTS[self.base_layout][0]), self.px(LAYOUTS[self.base_layout][1])
        return x <= pt.x < x + w and y <= pt.y < y + h

    def hover_check(self):
        if self.hover_expand_check():
            return
        if self.base_layout in SMALL_LAYOUTS and self.cfg.get("mini_hover", "expand") == "none":
            self.popup_hide()
            return
        lay = self.layout
        if (MenuPanel._inst is not None
                or not self.cfg.get("hover", True) or self.expanded
                or lay not in ("micro", "mini", "miniplus", "vmini", "row", "col", "compact")
                or self.hidden or self._pressed or self.updating):
            self.popup_hide()
            return
        pt = wintypes.POINT()
        if not user32.GetCursorPos(ctypes.byref(pt)):
            return
        if not (self.x <= pt.x < self.x + self.w and self.y <= pt.y < self.y + self.h):
            self._hover_idx = None
            self.popup_hide()
            return
        i = self.card_at(pt.x - self.x, pt.y - self.y)
        if i is None:
            return
        now = time.time()
        if i != self._hover_idx:
            self._hover_idx, self._hover_t = i, now
        showing = self.popup is not None and self.popup.visible
        if showing and self.popup.idx != i:
            self.popup_show(i)  # kartlar arasında gezinirken bekletme
        elif not showing and now - self._hover_t >= 0.6:
            self.popup_show(i)

    def popup_image(self, loc):
        """Orta düzende kart; widget'ın tıklama alanlarına karışmaz."""
        saved, anim, theme = self.hits, self._anim_needed, self.cfg.get("theme")
        parts = list(getattr(self, "_anim_parts", []))
        self.hits = []
        # kart başka pencerelerin üstünde açılır: saydam temada zemin olmadan okunmaz
        if self.theme == "clear":
            self.cfg["theme"] = "dark"
        try:
            w, h = self.px(LAYOUTS["medium"][0]), self.px(LAYOUTS["medium"][1])
            return self.draw_card(loc, "medium", w, h, (0, 0), multi=("pop", self.theme))
        finally:
            self.hits, self._anim_needed, self._anim_parts = saved, anim, parts
            self.cfg["theme"] = theme

    def popup_show(self, i):
        if i is None or i >= len(self.locs):
            return
        if self.popup is None:
            self.popup = Popup(self.root)
        img = self.popup_image(self.locs[i])
        pw, ph = img.size
        l, t_, r, b = work_area(self.x + self.w // 2, self.y + self.h // 2)
        gap = self.px(8)
        if self.multi:  # kartın hizasından aç
            cw, ch, g = self.px(MINI_W), self.px(MINI_H), self.px(MULTI_GAP)
            cx = self.x + ((cw + g) * i if self.layout == "row" else 0)
            cy = self.y + (0 if self.layout == "row" else (ch + g) * i)
            cwid = cw
        else:
            cx, cy, cwid = self.x, self.y, self.w
        if self.layout == "row":  # yatay şeritte kartın altına / üstüne
            x = min(max(l, cx), r - pw)
            y = self.y + self.h + gap if self.y + self.h + gap + ph <= b else self.y - gap - ph
        else:  # sağına, yer yoksa soluna
            x = cx + cwid + gap if cx + cwid + gap + pw <= r else cx - gap - pw
            y = min(max(t_, cy), b - ph)
        self.popup.show(img, x, y)
        self.popup.idx = i

    def popup_hide(self):
        if self.popup is not None:
            self.popup.hide()

    # ---- kısayol tuşu
    def apply_hotkey(self):
        if PROFILE:  # kısayol yalnızca ana widget'ta
            return
        key = self.cfg.get("hotkey", "win_shift_w")
        spec = next((h for h in HOTKEYS if h[0] == key), HOTKEYS[0])
        if spec[0] == "off":
            if self.hotkey_win:
                self.hotkey_win.register(0, 0)
            self.hotkey_ok = None
            return
        if self.hotkey_win is None:
            try:
                self.hotkey_win = HotkeyWindow()
            except Exception:
                log(traceback.format_exc())
                return
        self.hotkey_ok = self.hotkey_win.register(spec[2], spec[3])
        if not self.hotkey_ok and not self.cfg.get("hotkey_touched"):
            # kullanıcı seçmediyse: varsayılan doluysa sıradaki boş kısayola geç
            for alt in HOTKEYS:
                if alt[0] not in ("off", spec[0]) and self.hotkey_win.register(alt[2], alt[3]):
                    log(f"kısayol {spec[1]} kullanımda; {alt[1]} kullanılıyor")
                    self.cfg["hotkey"], self.hotkey_ok = alt[0], True
                    self.save_cfg()
                    return
        if not self.hotkey_ok:
            log("kısayol kaydedilemedi (başka bir uygulama kullanıyor olabilir):", spec[1])

    def peek(self):
        """Kısayol: widget'ı geçici olarak bütün pencerelerin üstüne getirir; tekrar basınca geri."""
        if self.hidden:
            self.toggle_hidden()
        if self.peeking:
            self.end_peek()
            return
        self.peeking = True
        pin_on_top(self.hwnd)
        self._peek_job = self.root.after(15_000, self._peek_timeout)

    def _peek_timeout(self):
        self._peek_job = None
        if self.cursor_over():
            self._peek_job = self.root.after(3000, self._peek_timeout)
        else:
            self.end_peek()

    def end_peek(self):
        if self._peek_job:
            self.root.after_cancel(self._peek_job)
            self._peek_job = None
        self.peeking = False
        self.apply_layer()

    # ---- ek widget'lar (her biri ayrı şehir / ekran, ayrı ayar dosyası)
    def spawn_profiles(self):
        for name in list_profiles():
            try:
                spawn(launch_cmd(name))
            except Exception:
                log(traceback.format_exc())

    def add_widget(self):
        names = set(list_profiles())
        i = 1
        while f"w{i}" in names:
            i += 1
        name = f"w{i}"
        cw = self.px(MINI_W)
        here = work_area(self.x + self.w // 2, self.y + self.h // 2)
        other = next((m for m in monitor_work_areas() if m != here), None)
        if other:  # başka bir ekran varsa onun sağ üstüne
            x, y = other[2] - cw - self.px(32), other[1] + self.px(48)
        else:      # yoksa bu widget'ın yanına
            x = self.x - cw - self.px(16) if self.x - cw - self.px(16) >= here[0] else self.x + self.w + self.px(16)
            y = self.y
        cfg = {"locs": [dict(self.loc)], "idx": 0, "layout": "mini", "x": x, "y": y,
               "scale": self.cfg.get("scale", 1.0), "theme": self.cfg.get("theme", "auto"),
               "opacity": self.cfg.get("opacity", 1.0), "radius": self.cfg.get("radius", 22),
               "custom_color": self.cfg.get("custom_color"), "bg_image": self.cfg.get("bg_image"),
               "units_temp": self.cfg.get("units_temp"), "units_wind": self.cfg.get("units_wind"),
               "tray": False, "notify_rain": False, "notify_alerts": False, "notify_uv": False,
               "morning_on": False, "reminders_on": False, "auto_update": False,
               "hotkey": "off", "rotate_touched": True}
        save_json(profile_paths(name)[0], cfg)
        spawn(launch_cmd(name))

    def remove_widget(self):
        if not PROFILE:
            return
        if not self.ask("askyesno", T("Widget'ı kaldır"),
                        T("Bu ek widget kapatılıp ayarları silinsin mi?\n"
                        "Ana widget ve diğer widget'lar etkilenmez.")):
            return
        for p in profile_paths(PROFILE) + (EVENTS_PATH,):
            try:
                os.remove(p)
            except OSError:
                pass
        self._no_save = True
        self.quit()

    def restart_if_exe_changed(self):
        """Ana widget güncellendiyse ek widget'lar da yeni sürümle yeniden başlar."""
        if not (FROZEN and PROFILE and self._exe_mtime):
            return
        try:
            m = os.path.getmtime(sys.executable)
        except OSError:
            return
        if m != self._exe_mtime:
            self.save_cfg()
            self._no_save = True
            spawn(launch_cmd(PROFILE, "--wait-pid", str(os.getpid())))
            self.quit()

    # ---- ayar aktarımı
    EXPORT_SKIP = {"x", "y", "idx", "seen_alerts", "last_morning", "seen_update"}

    def file_dialog(self, fn, **kw):
        top = tk.Toplevel(self.root)
        top.withdraw()
        top.attributes("-topmost", True)
        try:
            return fn(parent=top, **kw)
        finally:
            top.destroy()

    def export_settings(self):
        path = self.file_dialog(filedialog.asksaveasfilename, title=T("Ayarları dışa aktar"),
                                defaultextension=".json", initialfile="hava-durumu-ayarlar.json",
                                filetypes=[(T("Ayar dosyası"), "*.json")])
        if not path:
            return
        data = {k: v for k, v in self.cfg.items() if k not in self.EXPORT_SKIP}
        data["_app"], data["_version"] = "HavaDurumuWidget", APP_VERSION
        save_json(path, data)

    def import_settings(self):
        path = self.file_dialog(filedialog.askopenfilename, title=T("Ayarları içe aktar"),
                                filetypes=[(T("Ayar dosyası"), "*.json")])
        if not path:
            return
        data = load_json(path, None)
        locs = data.get("locs") if isinstance(data, dict) else None
        if not (isinstance(locs, list) and locs and all(
                isinstance(l, dict) and "merkezId" in l and "lat" in l and "lon" in l for l in locs)):
            self.ask("showwarning", T("Ayarları içe aktar"), T("Bu dosya geçerli bir ayar dosyası değil."))
            return
        names = ", ".join(loc_title(l) for l in locs[:6]) + (" …" if len(locs) > 6 else "")
        if not self.ask("askyesno", T("Ayarları içe aktar"),
                        T('Bütün ayarlar bu dosyadakilerle değiştirilsin mi?\n\nŞehirler: {}').format(names)):
            return
        for k, v in data.items():
            if k in self.cfg and k not in self.EXPORT_SKIP:
                self.cfg[k] = v
        self.cfg["idx"] = 0
        self.datas, self.errors = {}, {}
        self.apply_units()
        self.set_tray(bool(self.cfg.get("tray", True)))
        set_click_through(self.hwnd, bool(self.cfg.get("click_through")) and bool(self.tray))
        self.apply_hotkey()
        self.relayout()
        self.refresh()

    # ---- tema seçimleri
    def choose_custom_color(self):
        res = self.file_dialog(colorchooser.askcolor, title=T("Kart rengi"),
                               color=self.cfg.get("custom_color") or "#3b6ea5")
        if res and res[1]:
            self.cfg["custom_color"] = res[1]
            self.cfg["theme"] = "custom"
            self.save_cfg()
            self.render()

    def choose_bg_image(self):
        path = self.file_dialog(filedialog.askopenfilename, title=T("Arka plan görseli"),
                                filetypes=[(T("Görseller"), "*.jpg *.jpeg *.png *.bmp *.webp")])
        if path:
            self.cfg["bg_image"] = path
            self.cfg["theme"] = "image"
            self.save_cfg()
            self.render()
        elif not self.cfg.get("bg_image") and self.cfg.get("theme") == "image":
            self.cfg["theme"] = "auto"  # görsel seçilmediyse temayı geri al
            self.save_cfg()
            self.render()

    def theme_changed(self):
        t_ = self.cfg.get("theme")
        if t_ == "custom":
            self.choose_custom_color()
        elif t_ == "image":
            self.choose_bg_image()
        else:
            self.render()

    def open_radar(self):
        if getattr(self, "_radar", None) and self._radar.winfo_exists():
            self._radar.lift()
            return
        self._radar = RadarWindow(self)

    def open_cities(self):
        if getattr(self, "_dlg", None) and self._dlg.winfo_exists():
            self._dlg.lift()
            return
        self._dlg = CityManager(self)

    def open_site(self):
        loc = self.loc
        if loc.get("src") == "om":
            webbrowser.open("https://open-meteo.com/")
            return
        webbrowser.open("https://www.mgm.gov.tr/tahmin/il-ve-ilceler.aspx?"
                        + urllib.parse.urlencode({"il": loc["il"], "ilce": loc["ilce"]}))

    def restart_for_lang(self):
        """Dil değişti: tablolar içe aktarımda çevrildiği için bütün widget'lar yeniden başlar
        (ana widget açılınca ek widget'ları da açar)."""
        if self.cfg.get("lang") == LANG:
            return
        stop_other_instances()
        spawn(launch_cmd("", "--wait-pid", str(os.getpid())))
        self.quit()

    def quit(self):
        MenuPanel.close_open()
        if not self._no_save:
            self.save_cfg()
        if self.tray:
            self.tray.close()
        if self.hotkey_win:
            self.hotkey_win.close()
        if self.popup:
            self.popup.close()
        self.root.destroy()

    # ---- çizim
    def render(self, fade=False):
        if getattr(self, "_resize_job", None):  # büyüme / küçülme animasyonu sürüyor
            return
        self.__dict__.setdefault("_static", {}).clear()  # içerik değişmiş olabilir
        try:
            img = self.draw()
        except Exception:
            log(traceback.format_exc())
            return
        self.update_tray()
        if self.popup is not None and self.popup.visible and self.popup.idx is not None:
            self.popup_show(self.popup.idx)
        if self.fade_job:
            self.root.after_cancel(self.fade_job)
            self.fade_job = None
        old, self.last_img = self.last_img, img
        if self.hidden:
            return
        if fade and old is not None and old.size == img.size:
            self._fade(old, img, 1)
        else:
            self.surface.show(img)

    def _fade(self, old, new, i):
        steps = max(4, round(190 / self.frame_ms))  # ~190 ms, ekranın her karesinde bir adım
        if self._resize_job or new.size != (self.surface.w, self.surface.h):
            self.fade_job = None  # pencere boyutu değişti (büyüme / küçülme): solmayı bırak
            return
        self.surface.show(Image.blend(old, new, i / steps))
        self.fade_job = self.root.after(self.next_ms(), self._fade, old, new, i + 1) if i < steps else None

    def draw_refresh(self, d, cx, cy, r, st):
        """Yenile düğmesi (dairesel ok); tıklanabilir alanı döndürür.
        Yenileme sürerken tam renkli, boştayken soluk çizilir."""
        px = self.px
        col = st["text"] if self.loading else st["muted"]
        rp = px(r)
        d.arc([cx - rp, cy - rp, cx + rp, cy + rp], 40, 330, fill=col,
              width=max(1, px(r * 0.3)))
        a = max(px(3.2), px(r * 0.6))  # ok ucu (küçük boyutta da seçilsin)
        d.polygon([(cx + rp + a * 0.4, cy - a * 0.6), (cx + rp - a * 0.8, cy - a * 0.6),
                   (cx + rp, cy + a * 0.3)], fill=col)
        hit = max(px(9), rp + px(5))
        return (cx - hit, cy - hit, cx + hit, cy + hit)

    def draw_dots(self, d, x, y, st, center=False, left=None, right=None):
        """Şehir noktaları. (tıklanabilir alanlar, kapladığı genişlik) döndürür.
        left/right verilirse noktalar left'ten başlar ve right'ı aşmayacak kadar nokta çizilir
        (küçük kartlarda yenile simgesinin ve sayfa göstergesinin üstüne binmesin diye)."""
        rects = []
        N = len(self.locs)
        if N < 2:
            return rects, 0
        n = min(N, MAX_DOTS)
        sp, r = self.px(11), max(2, self.px(2.6))
        if left is not None:
            room = right - left - (sp if self.cfg.get("rotate") else 0)  # üçgene de yer bırak
            n = max(1, min(n, int(room // sp) + 1))
        total = (n - 1) * sp
        x0 = left if left is not None else x - total / 2 if center else x - total
        if left is not None:
            center = True  # otomatik geçiş üçgeni noktaların sağında
        cur = self.idx
        # şehir sayısı noktalardan fazlaysa görünen pencere seçili şehrin çevresine kayar
        start = max(0, min(cur - n // 2, N - n))
        c = st["text"]
        for j in range(n):
            i = start + j
            cx = x0 + j * sp
            edge = (j == 0 and start > 0) or (j == n - 1 and start + n < N)  # devamı var
            rr = (r + (1 if i == cur else 0)) * (0.65 if edge and i != cur else 1)
            d.ellipse([cx - rr, y - rr, cx + rr, y + rr],
                      fill=c if i == cur else (c[0], c[1], c[2], 90))
            rects.append(((cx - sp / 2, y - self.px(9), cx + sp / 2, y + self.px(9)), i))
        if self.cfg.get("rotate"):  # otomatik geçiş açık göstergesi: küçük üçgen
            tx = x0 + total + sp if center else x0 - sp
            s = self.px(3.2)
            d.polygon([(tx - s * 0.6, y - s), (tx - s * 0.6, y + s), (tx + s * 0.9, y)],
                      fill=(c[0], c[1], c[2], 150))
        return rects, total + sp * 2

    def draw(self):
        """Pencerenin tamamı: tek kart ya da (yan yana / alt alta) mini kartlar."""
        self.hits = []
        self._anim_needed = False
        self._anim_parts = []
        self._fx_on = False
        self._fx_parts = []
        MOON_STATE["q"] = int(moon_phase() * 28 + 0.5) % 28
        if not self.multi:
            img = self.draw_card(self.loc, self.layout, self.w, self.h, (0, 0))
            if self._fx_on:
                self._anim_parts = []  # efekt varken kartın tamamı yenilenir
            return img
        cw, ch, gap = self.px(MINI_W), self.px(MINI_H), self.px(MULTI_GAP)
        out = Image.new("RGBA", (self.w, self.h), (0, 0, 0, 0))
        for i in range(self.multi_n):
            off = ((cw + gap) * i, 0) if self.layout == "row" else (0, (ch + gap) * i)
            out.alpha_composite(self.draw_card(self.locs[i], "mini", cw, ch, off, multi=i), off)
        if self._fx_on:
            self._anim_parts = []
        return out

    def draw_card(self, loc, layout, w, h, off, multi=None):
        """Bir şehrin kartını çizer. off: kartın pencere içindeki konumu (tıklama alanları için);
        multi: çoklu düzende kartın sırası (None = tek kart)."""
        px, P = self.px, self.PAD
        W, H = LAYOUTS[layout][0], LAYOUTS[layout][1]
        k = loc_key(loc)
        now = datetime.now(timezone.utc)
        night = is_night(loc["lat"], loc["lon"], now)
        dayf = day_factor(loc["lat"], loc["lon"], now)
        data = self.datas.get(k)
        err = self.errors.get(k)
        cur = data.get("cur") if data else None
        code = cur.get("hadiseKodu") if cur else "CB"
        theme = self.theme
        st = self.style()
        big_font = "reg" if theme == "clear" else "light"  # ince font duvar kâğıdında kaybolur
        IS = st["icons"]
        WHITE, MUTED, FAINT = st["text"], st["muted"], st["faint"]
        layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        d = ImageDraw.Draw(layer)
        days = self.daily_rows(data)
        extra = (data or {}).get("extra") or {}
        tz = data_tz(data)  # yurt dışı şehirlerde saatler şehrin kendi saatiyle gösterilir
        local_today = datetime.now(tz).date()
        today = days[0] if days and days[0][0] == local_today else None
        alerts = alerts_for(loc, self.alerts, now)
        # Animasyon: kartın sabit kısmı (ana ikon hariç her şey) bir kez çizilip önbelleğe
        # alınır; her karede yalnızca hareketli ikon üstüne konur. Önbellek render() ile boşalır.
        page = getattr(self, "mini_page", 0) if (layout in ("mini", "miniplus") and multi is None
                                                 and self.pages_on()) else 0
        frame = self.anim_frame if self.cfg.get("animate") else None
        anim = frame is not None and bool(cur) and icon_animated(code, night) and page == 0
        isz, ipos = (px(58), (px(3), px(6))) if layout in ("mini", "miniplus") else \
            (px(36), (px(3), px(4))) if layout == "micro" else \
            (px(54), (px((W - 54) / 2), px(6))) if layout == "vmini" else \
            (px(44), (px(P - 2), px(50))) if layout == "weekly" else (px(88), (px(P - 8), px(42)))
        cache = self.__dict__.setdefault("_static", {})
        ck = (k, layout, w, h, multi, page)
        h0 = len(self.hits)
        def anim_part(base):
            """Animasyon karelerinde yalnızca bu alan yeniden hazırlanır (ikon + saydam temada
            halesi). Kartın geri kalanı değişmediği için ekrana yeniden yazılmaz."""
            m_ = px(10) if theme == "clear" else 0
            self.__dict__.setdefault("_anim_parts", []).append(
                (base, code, isz, night, IS, theme, ipos[0] - m_, ipos[1] - m_, isz + 2 * m_, off))

        fxk = fx_kind(code, night) if (cur and self.cfg.get("effects", True)
                                        and theme != "clear") else None
        if fxk:
            self._anim_needed = self._fx_on = True
            fx_obj = self.fx_object(fxk, w, h, st["icons"] == "light", code,
                                    (ipos[0] + isz / 2, ipos[1] + isz / 2, isz * 0.75), night)
            if ck + ("fx",) in cache:
                bg, lay_, bga, hits = cache[ck + ("fx",)]
                self.hits.extend(hits)
                return self.fx_frame(bg, lay_, bga, fx_obj, anim, code, isz, night, IS, frame, ipos,
                                     multi)
        if anim:
            self._anim_needed = True
            if ck in cache:
                base, hits = cache[ck]
                self.hits.extend(hits)
                anim_part(base)
                return self.with_icon(base, make_icon(code, isz, night, IS, frame), ipos, theme)

        accent = None  # kartın kenarı: sıralanan kart beyaz, uyarı varsa uyarı rengi
        if multi is not None and loc_key(loc) == getattr(self, "_reorder_key", None):
            accent = (255, 255, 255)
        elif alerts and self.cfg.get("alert_border", True):
            accent = ALERT_COLORS[alerts[0]["color"]][1]

        def finish(radius):
            if fxk:  # arka plan ayrı tutulur: efekt, arka plan ile yazılar arasına çizilir
                bg = self.compose_bg(w, h, code, night, radius, dayf, accent)
                bga, bg = bg.getchannel("A"), bg.convert("RGB")
                cache[ck + ("fx",)] = (bg, layer, bga, self.hits[h0:])
                return self.fx_frame(bg, layer, bga, fx_obj, anim, code, isz, night, IS, frame, ipos,
                                     multi)
            img = self.compose(layer, code, night, radius, dayf, accent)
            if not anim:
                return img
            cache[ck] = (img, self.hits[h0:])
            anim_part(img)
            return self.with_icon(img, make_icon(code, isz, night, IS, frame), ipos, theme)

        def hit(rect, *action):
            self.hits.append(((rect[0] + off[0], rect[1] + off[1],
                               rect[2] + off[0], rect[3] + off[1]), action))

        # ------------------------------------------------------------ küçük kartlar
        if layout in SMALL_LAYOUTS:
            self.draw_small(d, layer, loc, layout, W, H, cur, err, code, night, today, days, extra,
                            tz, alerts, anim, isz, ipos, st, IS, multi, hit, page, local_today)
            return finish(7 if layout == "micro" else 9)

        # ------------------------------------------------------------ başlık
        title_w = px(W - 2 * P - (30 if multi is None else 0))
        if self.cfg.get("clock"):
            ck_txt = clock_text(tz)
            cf = F("reg", px(13))
            cx_ = px(W - P - (22 if multi is None else 0))
            dtext(d, (cx_, px(30)), ck_txt, font=cf, fill=MUTED, anchor="rs")
            title_w -= int(text_len(ck_txt, font=cf)) + px(10)
        dtext(d, (px(P), px(30)), fit(d, loc_title(loc), F("sb", px(16)), title_w),
              font=F("sb", px(16)), fill=WHITE, anchor="ls")
        fy = px(H - 14)
        dots_w = 0
        if multi is None:
            hit(self.draw_refresh(d, px(W - P - 7), px(25), 6, st), "refresh")
            rects, dots_w = self.draw_dots(d, px(W - P), fy - px(4), st)
            for r, i in rects:
                hit(r, "city", i)

        if not cur:
            dtext(d, (w // 2, h // 2), err or T("Yükleniyor…"), font=F("reg", px(15)),
                  fill=st["warn"] if err else MUTED, anchor="mm")
            return finish(22)

        # ------------------------------------------------------------ haftalık
        if layout == "weekly":
            if not anim:
                layer.alpha_composite(make_icon(code, isz, night, IS), ipos)
            ts = fmt_temp(cur.get("sicaklik"))
            tf = F(big_font, px(30))
            dtext(d, (px(70), px(84)), ts, font=tf, fill=self.temp_fill(cur.get("sicaklik"), st),
                  anchor="ls")
            tw_ = int(text_len(ts, font=tf))
            dtext(d, (px(70) + tw_ + px(10), px(84)),
                  fit(d, HADISE.get(code, code), F("sb", px(14)), px(W - P - 80) - tw_),
                  font=F("sb", px(14)), fill=WHITE, anchor="ls")
            d.line([px(P), px(104), px(W - P), px(104)], fill=FAINT, width=max(1, px(1)))
            d16 = []
            for r_ in extra.get("daily16") or []:
                try:
                    day = datetime.strptime(r_["d"], "%Y-%m-%d").date()
                except Exception:
                    continue
                if day >= local_today:
                    d16.append((day, r_.get("code"), r_["lo"], r_["hi"], r_.get("prob")))
            week = d16[:7]
            if not week:
                dtext(d, (w // 2, px(215)), T("Haftalık tahmin alınamadı"), font=F("reg", px(13)),
                      fill=MUTED, anchor="mm")
            else:
                lo = min(r_[2] for r_ in week)
                span = max(1.0, max(r_[3] for r_ in week) - lo)
                bx1, bx2 = W - P - 116, W - P - 34
                tomorrow = local_today + timedelta(days=1)
                for i, (day, dc, tmin, tmax, prob) in enumerate(week):
                    yc = 110 + i * 30 + 15
                    name = T("Bugün") if day == local_today else \
                        T("Yarın") if day == tomorrow else GUNLER[day.weekday()]
                    dtext(d, (px(P), px(yc + 5)), name, font=F("reg", px(14)), fill=WHITE, anchor="ls")
                    ic = make_icon(dc, px(26), False, IS)
                    layer.alpha_composite(ic, (px(112), px(yc - 13)))
                    if prob is not None:  # yağış ihtimali: %50 ve üstü vurgulu
                        wet = prob >= 50
                        dtext(d, (px(186), px(yc + 5)), pct(prob),
                              font=F("sb" if wet else "reg", px(12)),
                              fill=((120, 190, 255, 255) if IS == "dark" else (30, 100, 190, 255))
                              if wet else MUTED, anchor="rs")
                    dtext(d, (px(bx1 - 8), px(yc + 5)), f"{tint(tmin)}°", font=F("reg", px(14)),
                          fill=MUTED, anchor="rs")
                    dtext(d, (px(W - P), px(yc + 5)), f"{tint(tmax)}°", font=F("sb", px(14)),
                          fill=WHITE, anchor="rs")
                    self.temp_bar(layer, d, st, bx1, bx2, yc, tmin, tmax, lo, span)
            if week and (data or {}).get("src") == "om":
                d.line([px(P), px(326), px(W - P), px(326)], fill=FAINT, width=max(1, px(1)))
                # 16 günlük EĞİLİM: tek tek günlerin tahmini değil, model ortalaması
                n16 = len(d16)
                wet_days = sum(1 for r_ in d16 if (r_[4] or 0) >= 50)
                avg_lo = sum(r_[2] for r_ in d16) / n16
                avg_hi = sum(r_[3] for r_ in d16) / n16
                dtext(d, (px(P), px(344)), T('{} günlük eğilim (model ortalaması, kesin tahmin değil)').format(n16),
                      font=F("reg", px(11)), fill=MUTED, anchor="ls")
                dtext(d, (px(P), px(364)),
                      fit(d, T('Ortalama {}° / {}°   ·   {} yağışlı gün').format(tint(avg_lo), tint(avg_hi), wet_days),
                          F("sb", px(13)), px(W - 2 * P)),
                      font=F("sb", px(13)), fill=WHITE, anchor="ls")
            if err:
                foot, fcol, ff = err, st["warn"], F("sb", px(11))
            else:
                om_ = (data or {}).get("src") == "om"
                foot = T("Kaynak: Open-Meteo") if om_ else T("Kaynak: MGM (5 günlük tahmin)")
                fcol, ff = MUTED, F("reg", px(11))
            dtext(d, (px(P), fy), fit(d, foot, ff, px(W - 2 * P) - dots_w), font=ff, fill=fcol,
                  anchor="ls")
            return finish(22)

        # ------------------------------------------------------------ anlık
        if not anim:
            layer.alpha_composite(make_icon(code, isz, night, IS), ipos)
        t = cur.get("sicaklik")
        rx = px(W - P)
        fl = cur.get("hissedilenSicaklik")
        right_w = 0
        if today:
            hl = f"↑ {tint(today[3])}°   ↓ {tint(today[2])}°"
            right_w = text_len(hl, font=F("sb", px(14)))
            dtext(d, (rx, px(76)), hl, font=F("sb", px(14)), fill=WHITE, anchor="rs")
        if ok(fl):
            hs = T('Hissedilen {}').format(fmt_temp(fl))
            right_w = max(right_w, text_len(hs, font=F("reg", px(13))))
            dtext(d, (rx, px(100)), hs, font=F("reg", px(13)), fill=MUTED, anchor="rs")
        ts = fmt_temp(t)
        # ondalıklı / eksi değerler sağ sütuna taşmasın: gerekirse font küçülür
        dtext(d, (px(112), px(112)), ts,
              font=fit_font(d, ts, big_font, px(62), rx - right_w - px(8) - px(112)),
              fill=self.temp_fill(t, st), anchor="ls")

        # durum satırı + (varsa) MGM uyarı etiketi
        cond_w = px(W - 2 * P)
        if alerts:
            a = alerts[0]
            cname, col = ALERT_COLORS[a["color"]]
            af = F("sb", px(11))
            label = fit(d, T('{} uyarı').format(cname) + (f" · {a['kinds'][0]}" if a["kinds"] else ""),
                        af, px(176))
            pw = int(text_len(label, font=af)) + px(16)
            x2, y1, y2 = px(W - P), px(135), px(156)
            d.rounded_rectangle([x2 - pw, y1, x2, y2], radius=px(10), fill=col + (255,))
            dtext(d, (x2 - pw / 2, (y1 + y2) / 2), label, font=af, fill=(30, 30, 30, 255),
                  anchor="mm")
            hit((x2 - pw, y1 - px(3), x2, y2 + px(3)), "alert", loc)
            cond_w -= pw + px(10)
        cond = fit(d, HADISE.get(code, code), F("sb", px(15)), cond_w)
        dtext(d, (px(P), px(150)), cond, font=F("sb", px(15)), fill=WHITE, anchor="ls")
        yd = extra.get("yesterday_diff")
        if yd is not None:
            ytxt = "  ·  " + yesterday_text(yd)
            yf = F("reg", px(13))
            used = int(text_len(cond, font=F("sb", px(15))))
            if used + text_len(ytxt, font=yf) <= cond_w:
                dtext(d, (px(P) + used, px(150)), ytxt, font=yf, fill=MUTED, anchor="ls")

        if layout == "peek":
            self.draw_peek(d, layer, loc, cur, code, extra, now, tz, st, IS, data)

        if layout in ("medium", "full"):
            # -------------------------------------------------------- ayrıntılar (2 satır)
            nem, rz = cur.get("nem"), cur.get("ruzgarHiz")
            bas = cur.get("denizeIndirgenmisBasinc")
            if not ok(bas):
                bas = cur.get("aktuelBasinc")
            yg, vis = cur.get("yagis24Saat"), cur.get("gorus")
            deniz, kar, kap = cur.get("denizSicaklik"), cur.get("karYukseklik"), cur.get("kapalilik")
            sun = sun_times(loc["lat"], loc["lon"], now, tz)
            pol = pollen_summary(extra.get("pollen"))
            if ok(deniz):
                slot4 = (T("Deniz suyu"), fmt_temp(deniz))
            elif ok(kar) and kar > 0:
                slot4 = (T("Kar kalınlığı"), T('{:g} cm').format(kar))
            elif pol:
                slot4 = (T("Polen"), f"{pol[0]} · {pol[1]}")
            elif ok(kap):
                slot4 = (T("Bulutluluk"), f"{round(kap)}/8")
            else:
                slot4 = (T("Bulutluluk"), "–")
            _, moon_s, illum = moon_info(now)
            aqi, uvn, rp = extra.get("european_aqi"), extra.get("uv_now"), extra.get("rain_prob")
            items = [
                (T("Nem"), pct(nem) if ok(nem) else "–"),
                (T("Rüzgar"), "–" if not ok(rz) else T("Sakin") if rz < 1 else
                 f"{fmt_wind(rz)} {wind_dir(cur.get('ruzgarYon'))}".strip()),
                (T("Basınç"), T('{} hPa').format(round(bas)) if ok(bas) else "–"),
                (T("Yağış 24s"), T('{:g} mm').format(yg) if ok(yg) else "–"),
                (T("Görüş"), "–" if not ok(vis) else T('{:.0f} km').format(vis / 1000) if vis >= 1000
                 else f"{vis:.0f} m"),
                (T("Gün doğumu"), sun[0] if sun else "–"),
                (T("Gün batımı"), sun[1] if sun else "–"),
                (T('Ay · %{:.0f}').format(illum), moon_s),
                (T("Hava kalitesi"), f"{aqi:.0f} · {aqi_level(aqi)[0]}" if aqi is not None else "–",
                 aqi_level(aqi)[1] if aqi is not None else None),
                (T("UV indeksi"), f"{uvn:.0f} · {uv_level(uvn)}" if uvn is not None else "–"),
                (T("Yağış ihtimali"), pct(rp) if rp is not None else "–"),
                slot4,
            ]
            # MGM konumlarında olmayan (Open-Meteo) değerler "–" yazmasın, hiç gösterilmesin
            items = [it for it in items if it[1] != "–" or it[0] not in
                     (T("Hava kalitesi"), T("UV indeksi"), T("Yağış ihtimali"), T("Bulutluluk"))]
            cw = (W - 2 * P) / 4
            for i, item in enumerate(items):
                lab, val = item[0], item[1]
                dot = item[2] if len(item) > 2 else None
                x = px(P + (i % 4) * cw)
                y = 178 + (i // 4) * 44
                dtext(d, (x, px(y)), lab, font=F("reg", px(11)), fill=MUTED, anchor="ls")
                if dot:  # renkli gösterge noktası (hava kalitesi)
                    r_ = px(4)
                    d.ellipse([x, px(y + 14) - r_, x + 2 * r_, px(y + 14) + r_], fill=dot + (255,))
                    x += 2 * r_ + px(5)
                dtext(d, (x, px(y + 19)), fit(d, val, F("sb", px(13)), px(cw - 4) - (x - px(P + (i % 4) * cw))),
                      font=F("sb", px(13)), fill=WHITE, anchor="ls")
            d.line([px(P), px(302), px(W - P), px(302)], fill=FAINT, width=max(1, px(1)))

            # -------------------------------------------------------- saatlik
            hours = self.hourly_rows(data, loc, now)
            if not hours:
                dtext(d, (w // 2, px(353)), T("Saatlik tahmin yok"), font=F("reg", px(12)),
                      fill=MUTED, anchor="mm")
            else:
                n = len(hours)
                colw = (W - 2 * P) / n
                xs = [px(P + colw * i + colw / 2) for i in range(n)]
                labels = [T("Şimdi") if i == 0 and lt <= now
                          else lt.strftime("%H:%M") for i, (lt, _, _, _) in enumerate(hours)]
                if self.cfg.get("hourly") == "chart":
                    temps = [r[2] for r in hours]
                    lo_t, hi_t = min(temps), max(temps)
                    span = max(1.0, hi_t - lo_t)
                    ys = [px(370 - (tv - lo_t) / span * 26) for tv in temps]
                    pts = list(zip(xs, ys))
                    d.polygon(pts + [(xs[-1], px(378)), (xs[0], px(378))],
                              fill=WHITE[:3] + (26,))
                    d.line(pts, fill=WHITE[:3] + (230,), width=max(2, px(2)), joint="curve")
                    for (x, y), tv, lab in zip(pts, temps, labels):
                        r = px(3)
                        d.ellipse([x - r, y - r, x + r, y + r], fill=temp_color(tv) + (255,),
                                  outline=WHITE, width=max(1, px(1)))
                        dtext(d, (x, y - px(8)), f"{tint(tv)}°", font=F("sb", px(12)),
                              fill=WHITE, anchor="ms")
                        dtext(d, (x, px(391)), lab, font=F("reg", px(10)), fill=MUTED,
                              anchor="ms")
                else:
                    for x, (lt, hc, ht, hn), lab in zip(xs, hours, labels):
                        dtext(d, (x, px(324)), lab, font=F("reg", px(11)), fill=MUTED,
                              anchor="ms")
                        ic = make_icon(code if lab == "Şimdi" else hc, px(30), hn, IS)
                        layer.alpha_composite(ic, (x - ic.width // 2, px(330)))
                        dtext(d, (x, px(378)), f"{tint(ht)}°", font=F("sb", px(13)),
                              fill=WHITE, anchor="ms")

        if layout == "full":
            d.line([px(P), px(398), px(W - P), px(398)], fill=FAINT, width=max(1, px(1)))
            # -------------------------------------------------------- günlük
            if days:
                lo = min(r[2] for r in days)
                hi = max(r[3] for r in days)
                span = max(1, hi - lo)
                y0 = 404
                bx1, bx2 = W - P - 130, W - P - 34
                tomorrow = local_today + timedelta(days=1)
                for i, (day, dc, tmin, tmax) in enumerate(days):
                    yc = y0 + i * 30 + 15
                    name = T("Bugün") if day == local_today else \
                        T("Yarın") if day == tomorrow else GUNLER[day.weekday()]
                    dtext(d, (px(P), px(yc + 5)), name, font=F("reg", px(14)), fill=WHITE,
                          anchor="ls")
                    ic = make_icon(dc, px(26), False, IS)
                    layer.alpha_composite(ic, (px(118), px(yc - 13)))
                    dtext(d, (px(bx1 - 10), px(yc + 5)), f"{tint(tmin)}°", font=F("reg", px(14)),
                          fill=MUTED, anchor="rs")
                    dtext(d, (px(W - P), px(yc + 5)), f"{tint(tmax)}°", font=F("sb", px(14)),
                          fill=WHITE, anchor="rs")
                    bh = 5
                    d.rounded_rectangle([px(bx1), px(yc - bh / 2), px(bx2), px(yc + bh / 2)],
                                        radius=px(bh / 2), fill=st["track"])
                    sx1 = bx1 + (bx2 - bx1) * (tmin - lo) / span
                    sx2 = max(sx1 + bh, bx1 + (bx2 - bx1) * (tmax - lo) / span)
                    gw = max(1, px(sx2) - px(sx1))
                    gh = max(1, px(yc + bh / 2) - px(yc - bh / 2))
                    grad = Image.linear_gradient("L").rotate(90).resize((gw, gh))
                    # grad: solda 0, sağda 255 -> maske 255 olan yerde ilk görüntü (en yüksek)
                    bar = Image.composite(Image.new("RGB", (gw, gh), temp_color(tmax)),
                                          Image.new("RGB", (gw, gh), temp_color(tmin)), grad)
                    m = Image.new("L", (gw, gh), 0)
                    ImageDraw.Draw(m).rounded_rectangle([0, 0, gw - 1, gh - 1],
                                                        radius=gh // 2, fill=255)
                    bar = bar.convert("RGBA")
                    bar.putalpha(m)
                    layer.alpha_composite(bar, (px(sx1), px(yc - bh / 2)))

        # ------------------------------------------------------------ alt bilgi
        if err:
            foot, fcol, ff = err, st["warn"], F("sb", px(11))
        else:
            foot = T("Kaynak: Open-Meteo") if (data or {}).get("src") == "om" else T("Kaynak: MGM")
            fcol, ff = MUTED, F("reg", px(11))
            vt = cur.get("veriZamani")
            if vt:
                foot += T(" · Ölçüm ") + parse_utc(vt).astimezone(tz).strftime("%H:%M")
        dtext(d, (px(P), fy), fit(d, foot, ff, px(W - 2 * P) - dots_w), font=ff, fill=fcol,
              anchor="ls")
        return finish(22)

    def draw_peek(self, d, layer, loc, cur, code, extra, now, tz, st, IS, data):
        """Büyütülmüş mini kartın alt kısmı: 8 ayrıntı + önümüzdeki 12 saat (2 saatte bir)."""
        px, P, W = self.px, self.PAD, LAYOUTS["peek"][0]
        WHITE, MUTED, FAINT = st["text"], st["muted"], st["faint"]
        blue = (120, 190, 255, 255) if IS == "dark" else (30, 100, 190, 255)
        d.line([px(P), px(166), px(W - P), px(166)], fill=FAINT, width=max(1, px(1)))
        nem, rz = cur.get("nem"), cur.get("ruzgarHiz")
        bas = cur.get("denizeIndirgenmisBasinc")
        if not ok(bas):
            bas = cur.get("aktuelBasinc")
        sun = sun_times(loc["lat"], loc["lon"], now, tz)
        aqi, uvn, rp = extra.get("european_aqi"), extra.get("uv_now"), extra.get("rain_prob")
        eta = rain_eta(extra, code) or ""
        if eta.startswith("☂"):  # "☂ 3 sa 20 dk sonra" -> başlık "Yağışa kalan", değer "3 sa 20 dk"
            v_ = eta[1:].strip()
            if v_.endswith(T(" sonra")):
                rain_item = (T("Yağışa kalan"), v_[:-len(T(" sonra"))], None, blue)
            else:
                rain_item = (T("Yağış sürüyor"), v_, None, blue)
        else:
            rain_item = (T("Yağış ihtimali"), pct(rp) if rp is not None else "–")
        items = [
            (T("Nem"), pct(nem) if ok(nem) else "–"),
            (T("Rüzgar"), "–" if not ok(rz) else T("Sakin") if rz < 1 else
             f"{fmt_wind(rz)} {wind_dir(cur.get('ruzgarYon'))}".strip()),
            rain_item,
            (T("UV indeksi"), f"{uvn:.0f} · {uv_level(uvn)}" if uvn is not None else "–"),
            (T("Gün doğumu"), sun[0] if sun else "–"),
            (T("Gün batımı"), sun[1] if sun else "–"),
            (T("Hava kalitesi"), f"{aqi:.0f} · {aqi_level(aqi)[0]}" if aqi is not None else "–",
             aqi_level(aqi)[1] if aqi is not None else None),
            (T("Basınç"), T('{} hPa').format(round(bas)) if ok(bas) else "–"),
        ]
        # MGM'de olmayan (Open-Meteo) değerler boşsa yerine MGM ölçümleri
        yg, vis, kap = cur.get("yagis24Saat"), cur.get("gorus"), cur.get("kapalilik")
        _, moon_s, illum = moon_info(now)
        spare = iter([(T("Yağış 24s"), T('{:g} mm').format(yg) if ok(yg) else "–"),
                      (T("Görüş"), "–" if not ok(vis) else T('{:.0f} km').format(vis / 1000) if vis >= 1000
                       else f"{vis:.0f} m"),
                      (T("Bulutluluk"), f"{round(kap)}/8") if ok(kap) else (T('Ay · %{:.0f}').format(illum), moon_s)])
        items = [next(spare) if it[1] == "–" and it[0] in (T("Yağış ihtimali"), T("UV indeksi"), T("Hava kalitesi"))
                 else it for it in items]
        cw = (W - 2 * P) / 4
        for i, item in enumerate(items):
            lab, val = item[0], item[1]
            dot = item[2] if len(item) > 2 else None
            col = item[3] if len(item) > 3 else WHITE
            x0 = px(P + (i % 4) * cw)
            x, y = x0, 186 + (i // 4) * 42
            dtext(d, (x, px(y)), lab, font=F("reg", px(11)), fill=MUTED, anchor="ls")
            if dot:
                r_ = px(4)
                d.ellipse([x, px(y + 14) - r_, x + 2 * r_, px(y + 14) + r_], fill=dot + (255,))
                x += 2 * r_ + px(5)
            avail = px(cw - 4) - (x - x0)
            if " · " in val and text_len(val, font=F("sb", px(13))) > avail:
                val = val.split(" · ", 1)[1]  # sığmazsa yalnızca seviye: "Makul"
            vf = fit_font(d, val, "sb", px(13), avail)  # sığmazsa önce küçült (11 px'e kadar)
            if vf.size < px(11):
                vf = F("sb", px(11))
            dtext(d, (x, px(y + 19)), fit(d, val, vf, avail), font=vf, fill=col, anchor="ls")
        d.line([px(P), px(256), px(W - P), px(256)], fill=FAINT, width=max(1, px(1)))

        # önümüzdeki 12 saat: Open-Meteo saatlik (2 saatte bir); yoksa MGM saatlik tahmini
        rows = []
        t12 = extra.get("temps12") or []
        step = 2 if len(t12) > 1 and t12[1][0] - t12[0][0] < 7200 else 1  # MGM zaten 3 saatlik
        for r_ in t12[::step][:7]:
            t_ = datetime.fromtimestamp(r_[0], timezone.utc)
            rows.append((t_.astimezone(tz), r_[2], r_[1], is_night(loc["lat"], loc["lon"], t_),
                         r_[3] if len(r_) > 3 else None))
        if not rows:
            rows = [(lt, hc, ht, hn, None) for lt, hc, ht, hn in self.hourly_rows(data, loc, now)]
        if not rows:
            dtext(d, (px(W / 2), px(305)), T("Saatlik tahmin yok"), font=F("reg", px(12)),
                  fill=MUTED, anchor="mm")
            return
        colw = (W - 2 * P) / len(rows)
        for i, (lt, hc, ht, hn, prob) in enumerate(rows):
            x = px(P + colw * i + colw / 2)
            lab = T("Şimdi") if i == 0 and lt <= now + timedelta(minutes=30) else lt.strftime("%H:%M")
            dtext(d, (x, px(276)), lab, font=F("reg", px(11)), fill=MUTED, anchor="ms")
            # "Şimdi" saatlik tahmin değil, üstteki anlık gözlemle aynı ikonu göstersin
            ic = make_icon(code if lab == "Şimdi" else hc, px(28), hn, IS)
            layer.alpha_composite(ic, (x - ic.width // 2, px(282)))
            dtext(d, (x, px(326)), f"{tint(ht)}°", font=F("sb", px(13)),
                  fill=self.temp_fill(ht, st), anchor="ms")
            if prob is not None and prob >= 20:
                dtext(d, (x, px(340)), pct(prob), font=F("reg", px(10)), fill=blue, anchor="ms")

    def temp_fill(self, t, st):
        """Sıcaklık yazısının rengi: "Renkli sıcaklık" açıksa soğuk mavi, sıcak turuncu."""
        if not (self.cfg.get("colored_temp") and ok(t)):
            return st["text"]
        c = temp_color(t)
        if st["icons"] == "light":  # açık zeminde okunsun diye koyulaştır
            c = mix(c, (0, 0, 0), 0.38)
        return tuple(c) + (255,)

    def line_font(self, d, text, size, maxw):
        """İkinci satır yazısı: sığmazsa 10 px'e kadar küçülür (sonra … ile kısaltılır)."""
        f = fit_font(d, text, "reg", self.px(size), maxw)
        return f if f.size >= self.px(10) else F("reg", self.px(10))

    def draw_small(self, d, layer, loc, layout, W, H, cur, err, code, night, today, days, extra,
                   tz, alerts, anim, isz, ipos, st, IS, multi, hit, page, local_today):
        """Mikro, Mini, Mini+ ve Dikey mini kartlar."""
        px = self.px
        WHITE, MUTED = st["text"], st["muted"]
        tv = cur.get("sicaklik") if cur else None
        loading = err or T("Yükleniyor…")

        def alert_tri():
            if not alerts:
                return
            col = ALERT_COLORS[alerts[0]["color"]][1]
            d.polygon([(px(5), px(18)), (px(12), px(5)), (px(19), px(18))], fill=col + (255,))
            d.line([px(12), px(9.5), px(12), px(13.5)], fill=(30, 30, 30, 255), width=max(1, px(1.6)))
            d.ellipse([px(11.2), px(15), px(12.8), px(16.6)], fill=(30, 30, 30, 255))
            hit((0, 0, px(24), px(24)), "alert", loc)

        def bottom_row(right_margin):
            # yenile simgesi solda (x 13, yarıçap 5,5 + ok ucu); noktalar onun sağından başlar
            if multi is None or multi == 0:
                hit(self.draw_refresh(d, px(13), px(H - 10), 5.5, st), "refresh")
            if multi is None:
                rects, _ = self.draw_dots(d, 0, px(H - 8), st, left=px(30),
                                          right=px(W - right_margin))
                for r_, i in rects:
                    hit(r_, "city", i)

        # ---- mikro: ikon + sıcaklık + şehir
        if layout == "micro":
            rx = px(W - 7)
            if cur:
                if not anim:
                    layer.alpha_composite(make_icon(code, isz, night, IS), ipos)
                ts = fmt_temp(tv)
                dtext(d, (rx, px(25)), ts, font=fit_font(d, ts, "reg", px(20), rx - px(42)),
                      fill=self.temp_fill(tv, st), anchor="rs")
            else:
                dtext(d, (rx, px(25)), "!" if err else "…", font=F("reg", px(16)),
                      fill=st["warn"] if err else MUTED, anchor="rs")
            dtext(d, (rx, px(38)), fit(d, loc_short(loc), F("reg", px(10)), px(W - 46)),
                  font=F("reg", px(10)), fill=WHITE, anchor="rs")
            return

        # ---- dikey mini
        if layout == "vmini":
            cx = px(W / 2)
            if cur:
                if not anim:
                    layer.alpha_composite(make_icon(code, isz, night, IS), ipos)
                ts = fmt_temp(tv)
                dtext(d, (cx, px(84)), ts, font=fit_font(d, ts, "reg", px(22), px(W - 8)),
                      fill=self.temp_fill(tv, st), anchor="ms")
                line2 = self.mini_line(cur, today, extra, tz, loc)
                if line2:
                    lf = self.line_font(d, line2, 11, px(W - 8))
                    dtext(d, (cx, px(102)), fit(d, line2, lf, px(W - 8)),
                          font=lf, fill=WHITE, anchor="ms")
            else:
                dtext(d, (cx, px(84)), fit(d, loading, F("reg", px(10)), px(W - 8)),
                      font=F("reg", px(10)), fill=st["warn"] if err else MUTED, anchor="ms")
            dtext(d, (cx, px(119)), fit(d, loc_short(loc), F("reg", px(11)), px(W - 8)),
                  font=F("reg", px(11)), fill=WHITE, anchor="ms")
            alert_tri()
            bottom_row(8)
            return

        # ---- mini / mini+ (sayfa 0: şimdi, 1: saatlik, 2: yarın)
        rx = px(W - 9)
        if not cur:
            dtext(d, (rx, px(40)), loading, font=F("reg", px(11)),
                  fill=st["warn"] if err else MUTED, anchor="rs")
            dtext(d, (rx, px(64)), fit(d, loc_short(loc), F("reg", px(12)), px(W - 66)),
                  font=F("reg", px(12)), fill=WHITE, anchor="rs")
        elif page == 1:
            rows = extra.get("temps12") or []
            # +1, +3, +6 saate en yakın satırlar (Open-Meteo saatlik, MGM 3 saatlik)
            picks = []
            for hh in (1, 3, 6):
                r_ = min(rows, key=lambda r_: abs(r_[0] - time.time() - hh * 3600), default=None)
                if r_ is not None and r_ not in picks:
                    picks.append(r_)
            if not picks:
                dtext(d, (px(W / 2), px(36)), T("Saatlik veri yok"), font=F("reg", px(11)),
                      fill=MUTED, anchor="mm")
            for i, (ts_, t_, hc, _p) in enumerate(picks):
                cx = px(25 + 50 * i)
                lt = datetime.fromtimestamp(ts_, timezone.utc)
                dtext(d, (cx, px(14)), lt.astimezone(tz).strftime("%H:%M"), font=F("reg", px(10)),
                      fill=MUTED, anchor="ms")
                ic = make_icon(hc, px(28), is_night(loc["lat"], loc["lon"], lt), IS)
                layer.alpha_composite(ic, (cx - ic.width // 2, px(18)))
                dtext(d, (cx, px(59)), f"{tint(t_)}°", font=F("sb", px(13)),
                      fill=self.temp_fill(t_, st), anchor="ms")
        elif page == 2:
            tomorrow = local_today + timedelta(days=1)
            row = next((r_ for r_ in days if r_[0] == tomorrow), None)
            d16 = next((r_ for r_ in (extra.get("daily16") or []) if r_.get("d") == tomorrow.isoformat()),
                       None)
            if row is None and d16:
                row = (tomorrow, d16["code"], d16["lo"], d16["hi"])
            if row:
                layer.alpha_composite(make_icon(row[1], px(52), False, IS), (px(4), px(8)))
                dtext(d, (rx, px(18)), T("Yarın"), font=F("sb", px(12)), fill=MUTED, anchor="rs")
                hl = f"{tint(row[3])}° / {tint(row[2])}°"
                dtext(d, (rx, px(40)), hl, font=fit_font(d, hl, "reg", px(19), rx - px(60)),
                      fill=self.temp_fill(row[3], st), anchor="rs")
                if d16 and d16.get("prob") is not None:
                    dtext(d, (rx, px(58)), T('Yağış %{:.0f}').format(d16['prob']), font=F("reg", px(11)),
                          fill=WHITE, anchor="rs")
            else:
                dtext(d, (px(W / 2), px(36)), T("Yarın verisi yok"), font=F("reg", px(11)),
                      fill=MUTED, anchor="mm")
        else:
            if not anim:
                layer.alpha_composite(make_icon(code, isz, night, IS), ipos)
            ts = fmt_temp(tv)
            dtext(d, (rx, px(29)), ts, font=fit_font(d, ts, "reg", px(24), rx - px(64)),
                  fill=self.temp_fill(tv, st), anchor="rs")
            line2 = self.mini_line(cur, today, extra, tz, loc)
            if line2:
                lf = self.line_font(d, line2, 12, px(W - 70))
                dtext(d, (rx, px(47)), fit(d, line2, lf, px(W - 70)), font=lf, fill=WHITE, anchor="rs")
            dtext(d, (rx, px(64)), fit(d, loc_short(loc), F("reg", px(12)), px(W - 66)),
                  font=F("reg", px(12)), fill=WHITE, anchor="rs")
        if layout == "miniplus":
            self.draw_sparkline(d, extra, st, px(12), px(70), px(W - 12), px(97))
        if page == 0:
            alert_tri()
        bottom_row(32 if (multi is None and self.pages_on()) else 8)
        if multi is None and self.pages_on():  # sayfa göstergesi (sağ alt)
            c = st["text"]
            for i in range(3):
                x = px(W - 24 + 7 * i)
                r_ = px(2.2 if i == page else 1.6)
                d.ellipse([x - r_, px(H - 8) - r_, x + r_, px(H - 8) + r_],
                          fill=c if i == page else c[:3] + (90,))

    def draw_sparkline(self, d, extra, st, x1, y1, x2, y2):
        """Önümüzdeki 12 saatin sıcaklık eğrisi; yağış olasılığı mavi çubuklarla."""
        rows = (extra.get("temps12") or [])[:13]
        f9 = F("reg", self.px(9))
        if len(rows) < 2:
            dtext(d, ((x1 + x2) // 2, (y1 + y2) // 2), T("Saatlik veri yok"), font=f9,
                  fill=st["muted"], anchor="mm")
            return
        temps = [r_[1] for r_ in rows]
        lo, hi = min(temps), max(temps)
        span = max(1.0, hi - lo)
        n = len(rows)
        top, bot = y1 + self.px(9), y2 - self.px(2)
        xs = [x1 + (x2 - x1) * i / (n - 1) for i in range(n)]
        ys = [bot - (bot - top) * (v - lo) / span for v in temps]
        blue = (110, 180, 255, 150) if st["icons"] == "dark" else (40, 110, 200, 150)
        for x, r_ in zip(xs, rows):
            p = r_[3]
            if p and p >= 20:
                bh = (y2 - y1) * 0.55 * p / 100
                d.rectangle([x - self.px(1.4), y2 - bh, x + self.px(1.4), y2], fill=blue)
        pts = list(zip(xs, ys))
        d.polygon(pts + [(xs[-1], y2), (xs[0], y2)], fill=st["text"][:3] + (26,))
        d.line(pts, fill=st["text"][:3] + (225,), width=max(1, self.px(1.6)), joint="curve")
        dtext(d, (xs[0], ys[0] - self.px(3)), f"{tint(temps[0])}°", font=f9, fill=st["muted"],
              anchor="ls")
        dtext(d, (xs[-1], ys[-1] - self.px(3)), T('+{} sa  {}°').format(n - 1, tint(temps[-1])), font=f9,
              fill=st["muted"], anchor="rs")

    def temp_bar(self, layer, d, st, bx1, bx2, yc, tmin, tmax, lo, span):
        """Günün sıcaklık aralığı çubuğu (soldan sağa soğuk -> sıcak renk geçişli)."""
        px, bh = self.px, 5
        d.rounded_rectangle([px(bx1), px(yc - bh / 2), px(bx2), px(yc + bh / 2)],
                            radius=px(bh / 2), fill=st["track"])
        sx1 = bx1 + (bx2 - bx1) * (tmin - lo) / span
        sx2 = max(sx1 + bh, bx1 + (bx2 - bx1) * (tmax - lo) / span)
        gw = max(1, px(sx2) - px(sx1))
        gh = max(1, px(yc + bh / 2) - px(yc - bh / 2))
        grad = Image.linear_gradient("L").rotate(90).resize((gw, gh))
        bar = Image.composite(Image.new("RGB", (gw, gh), temp_color(tmax)),
                              Image.new("RGB", (gw, gh), temp_color(tmin)), grad)
        m = Image.new("L", (gw, gh), 0)
        ImageDraw.Draw(m).rounded_rectangle([0, 0, gw - 1, gh - 1], radius=gh // 2, fill=255)
        bar = bar.convert("RGBA")
        bar.putalpha(m)
        layer.alpha_composite(bar, (px(sx1), px(yc - bh / 2)))

    def style(self):
        t = self.theme
        if t == "light":
            return STYLE_LIGHT
        if t == "clear":
            return STYLE_CLEAR
        if t == "custom" and luminance(custom_colors(self.cfg.get("custom_color"))[0]) > 0.62:
            return STYLE_LIGHT
        return STYLE_DARK

    def mini_line(self, cur, today, extra, tz, loc=None):
        """Mini kartın ikinci satırı (ayarlardan seçilir)."""
        kind = self.cfg.get("mini_line", "hl")
        if kind == "rain_eta":
            s = rain_eta(extra, cur.get("hadiseKodu"))
            if s:
                return s
        if kind == "sun" and loc is not None:
            s = sun_eta(loc["lat"], loc["lon"], tz=tz)
            if s:
                return s
        if kind == "clock":
            return datetime.now(tz).strftime("%H:%M")
        if kind == "feels" and ok(cur.get("hissedilenSicaklik")):
            return T('Hiss. {}').format(fmt_temp(cur['hissedilenSicaklik']))
        if kind == "wind" and ok(cur.get("ruzgarHiz")):
            rz = cur["ruzgarHiz"]
            return T("Sakin") if rz < 1 else f"{fmt_wind(rz)} {wind_dir(cur.get('ruzgarYon'))}".strip()
        if kind == "humidity" and ok(cur.get("nem")):
            return T('Nem %{}').format(round(cur['nem']))
        if kind == "aqi" and extra.get("european_aqi") is not None:
            v = extra["european_aqi"]
            return T('HK {:.0f} · {}').format(v, aqi_level(v)[0])
        if kind == "rain" and extra.get("rain_prob") is not None:
            return T('Yağış %{:.0f}').format(extra['rain_prob'])
        if kind == "uv" and extra.get("uv_now") is not None:
            return T('UV {:.0f} · {}').format(extra['uv_now'], uv_level(extra['uv_now']))
        return f"{tint(today[3])}° / {tint(today[2])}°" if today else None

    def fx_object(self, kind, w, h, light, code, sun_at, night=False):
        store = self.__dict__.setdefault("_fx_objs", {})
        key = (kind, night, w, h, light, RAIN.get(code, 3), self.S)
        if key not in store:
            if len(store) > 24:
                store.clear()
            store[key] = AmbientFX(kind, w, h, self.S, light, RAIN.get(code, 3), sun_at, night)
        return store[key]

    def fx_frame(self, bg, layer, bga, fx_obj, anim, code, isz, night, IS, frame, ipos, multi):
        img = bg.copy()  # RGB: efektler doğrudan üstüne karıştırılır
        fx_obj.draw_on(img, time.monotonic())
        img.putalpha(bga)  # kartın yuvarlak köşeleri (efekt köşelerden taşmasın)
        img.alpha_composite(layer)
        if anim:
            if multi is None:  # efekt kareleri arasında ikon bu ikonsuz kare üstünde yenilenir
                self._fx_parts = [(img.copy(), code, isz, night, IS, self.theme, ipos[0], ipos[1],
                                   isz, (0, 0))]
            img.alpha_composite(make_icon(code, isz, night, IS, frame), ipos)
        return img

    def with_icon(self, base, icon, pos, theme):
        """Önbellekteki kartın üstüne animasyon karesindeki ikonu koyar.
        Saydam temada ikonun kendi halesi/gölgesi de (kare başına bir kez) hazırlanır."""
        out = base.copy()
        if theme != "clear":
            out.alpha_composite(icon, pos)
            return out
        op = float(self.cfg.get("opacity", 1.0))
        bk = (id(icon), op, self.S)
        baked = self.__dict__.setdefault("_baked", {})
        if bk not in baked:
            m = self.px(10)
            pad = Image.new("RGBA", (icon.width + 2 * m, icon.height + 2 * m), (0, 0, 0, 0))
            pad.alpha_composite(icon, (m, m))
            sh = Image.new("RGBA", pad.size, (8, 12, 20, 0))
            sh.putalpha(self.shadow_alpha(pad.getchannel("A"), op))
            sh.alpha_composite(pad)
            if len(baked) > 400:
                baked.clear()
            baked[bk] = (sh, m, icon)  # ikonu tut: id() yeniden kullanılmasın
        sh, m, _ = baked[bk]
        dx, dy = pos[0] - m, pos[1] - m
        out.alpha_composite(sh, (max(0, dx), max(0, dy)), (max(0, -dx), max(0, -dy)))
        return out

    def shadow_alpha(self, A, op):
        """Saydam tema: sıkı koyu hale + geniş yumuşak gölge (alfa kanalı)."""
        grow = 5 if self.S >= 1.5 else 3
        halo = A.filter(ImageFilter.MaxFilter(grow)).filter(
            ImageFilter.GaussianBlur(max(0.8, self.px(0.9))))
        halo = halo.point(lambda v: min(255, int(v * 1.5 * (0.55 + 0.45 * op))))
        soft = A.filter(ImageFilter.MaxFilter(grow)).filter(
            ImageFilter.GaussianBlur(self.px(4)))
        soft = soft.point(lambda v: min(255, int(v * 1.3 * (0.4 + 0.6 * op))))
        return ImageChops.lighter(halo, soft)

    def compose(self, layer, code, night, radius, dayf=None, accent=None):
        """Temaya göre arka planı oluşturur ve içerik katmanını üstüne koyar."""
        w, h = layer.size
        theme = self.theme
        op = float(self.cfg.get("opacity", 1.0))

        if theme == "clear":
            # 1) sıkı koyu hale: her harfin/ikonun çevresinde ince kontur
            # 2) geniş yumuşak gölge: açık ve karışık duvar kâğıtlarında kontrast
            # "opaklık" seçeneği bu temada gölgenin koyuluğunu ayarlar.
            # alfa 0 olan pikseller tıklamayı geçirir; 1 bırakınca sürükleme çalışır.
            out = Image.new("RGBA", (w, h), (0, 0, 0, 1))
            shadow = Image.new("RGBA", (w, h), (8, 12, 20, 0))
            shadow.putalpha(self.shadow_alpha(layer.getchannel("A"), op))
            out.alpha_composite(shadow)
            if accent:
                self.draw_accent(out, accent, self.px(radius * float(self.cfg.get("radius", 22)) / 22))
            out.alpha_composite(layer)
            return out

        out = self.compose_bg(w, h, code, night, radius, dayf, accent)
        out.alpha_composite(layer)
        return out

    def compose_bg(self, w, h, code, night, radius, dayf=None, accent=None):
        """Kartın yazısız arka planı (saydam tema dışındaki temalar)."""
        theme = self.theme
        op = float(self.cfg.get("opacity", 1.0))
        R = self.px(radius * float(self.cfg.get("radius", 22)) / 22)
        if theme in THEME_BG:
            top, bot, base_a = THEME_BG[theme]
        elif theme == "custom":
            (top, bot), base_a = custom_colors(self.cfg.get("custom_color")), 242
        else:
            (top, bot), base_a = palette_at(code, night, dayf), 242
        alpha = max(1, int(base_a * op))
        k = 3
        mask = Image.new("L", (w * k, h * k), 0)
        ImageDraw.Draw(mask).rounded_rectangle([0, 0, w * k - 1, h * k - 1],
                                               radius=R * k, fill=alpha)
        mask = mask.resize((w, h), Image.LANCZOS)
        bg = None
        if theme == "scene":
            bg = scene_bg(w, h, code, night, self.S, dayf)
        elif theme == "image" and self.cfg.get("bg_image"):
            bg = image_bg(self.cfg["bg_image"], w, h, self.S)
        out = (bg or vgradient(w, h, top, bot)).convert("RGBA")
        out.putalpha(mask)
        if theme == "glass" or op < 1:
            edge = Image.new("L", (w * k, h * k), 0)
            ImageDraw.Draw(edge).rounded_rectangle(
                [0, 0, w * k - 1, h * k - 1], radius=R * k, outline=70,
                width=max(1, self.px(1)) * k)
            border = Image.new("RGBA", (w, h), (255, 255, 255, 0))
            border.putalpha(edge.resize((w, h), Image.LANCZOS))
            out.alpha_composite(border)
        if accent:
            self.draw_accent(out, accent, R)
        return out

    def draw_accent(self, out, color, R):
        """Kartın kenarına renkli çerçeve (uyarı rengi ya da sıralanan kart)."""
        w, h = out.size
        k = 3
        bw = max(2, self.px(2.2))
        edge = Image.new("L", (w * k, h * k), 0)
        ImageDraw.Draw(edge).rounded_rectangle([0, 0, w * k - 1, h * k - 1], radius=R * k,
                                               outline=235, width=bw * k)
        ring = Image.new("RGBA", (w, h), tuple(color) + (0,))
        ring.putalpha(edge.resize((w, h), Image.LANCZOS))
        out.alpha_composite(ring)

    @staticmethod
    def daily_rows(data):
        """(gün, hadise, en düşük °C, en yüksek °C) — ham değerler; gösterimde birime çevrilir."""
        dd = (data or {}).get("daily") or {}
        today = datetime.now(data_tz(data)).date()
        by_day = {}  # MGM bazen aynı günü iki kez gönderir: sonraki (güncel tahmin) geçerli
        for i in range(0, 6):
            ts = dd.get(f"tarihGun{i}")
            lo, hi = dd.get(f"enDusukGun{i}"), dd.get(f"enYuksekGun{i}")
            if not ts or not ok(lo) or not ok(hi):
                continue
            day = datetime.strptime(ts[:10], "%Y-%m-%d").date()
            if day < today:
                continue
            by_day[day] = (day, dd.get(f"hadiseGun{i}"), round(lo), round(hi))
        return [by_day[k] for k in sorted(by_day)][:5]

    @staticmethod
    def hourly_rows(data, loc, now):
        rows = []
        for e in (data or {}).get("hourly") or []:
            try:
                t = parse_utc(e["tarih"])
            except Exception:
                continue
            if t < now - timedelta(minutes=90) or not ok(e.get("sicaklik")):
                continue
            rows.append((t.astimezone(data_tz(data)), e.get("hadise"), e["sicaklik"],
                         is_night(loc["lat"], loc["lon"], t)))
        return rows[:7]


def main():
    args = sys.argv[1:]

    def arg(name):
        if name in args:
            i = args.index(name)
            return args[i + 1] if i + 1 < len(args) else None
        return None

    prof = arg("--profile")
    if prof and re.fullmatch(r"[A-Za-z0-9_-]{1,32}", prof):
        set_profile(prof)
    wp = arg("--wait-pid")
    if wp and wp.isdigit():
        wait_pid(int(wp))
    target = arg("--apply-update")
    if target and FROZEN and os.path.isdir(target):  # klasör kipi güncellemesi: dosyaları taşı, çık
        apply_update(target)
        return
    if not single_instance(PROFILE):
        return  # bu widget zaten açık
    try:
        app = WeatherWidget()
        app.root.mainloop()
    except Exception:
        log(traceback.format_exc())
        raise


if __name__ == "__main__":
    main()
