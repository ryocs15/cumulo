"""Çeviri denetimi: kaynakta T("...") ile sarılan her metnin İngilizcesi var mı, yer tutucular uyuyor mu,
lang_en_wpf.json'da kullanılmayan metin var mı. lang_en.json, Python'un lang_en.py'siyle aynı mı.
Kullanım: python tools/test_lang.py"""
import glob, json, os, re, sys

here = os.path.dirname(os.path.abspath(__file__))
src = os.path.join(here, "..", "src")
base = json.load(open(os.path.join(src, "Core", "lang_en.json"), encoding="utf-8"))
wpf = json.load(open(os.path.join(src, "Core", "lang_en_wpf.json"), encoding="utf-8"))
sys.path.insert(0, os.path.normpath(os.path.join(here, "..", "..", "hava_durumu_widget")))
fail = 0
try:
    from lang_en import EN
    if EN != base:
        print("HATA: lang_en.json eski; python tools/export_lang.py çalıştırın"); fail += 1
except ImportError:
    print("uyarı: Python lang_en.py bulunamadı, eşitlik denetlenmedi")

ESC = {"n": "\n", "t": "\t", '"': '"', "\\": "\\", "'": "'", "r": "\r", "0": "\0"}
lit = re.compile(r'(?<![\w$@])T\("((?:[^"\\\n]|\\.)*)"\)')
keys = {}
for f in glob.glob(os.path.join(src, "**", "*.cs"), recursive=True):
    for ln, line in enumerate(open(f, encoding="utf-8"), 1):
        for m in lit.finditer(line):
            k = re.sub(r"\\(.)", lambda e: ESC.get(e.group(1), e.group(1)), m.group(1))
            keys.setdefault(k, f"{os.path.relpath(f, src)}:{ln}")
ph = lambda s: re.findall(r"\{[^{}]*\}", s)
en = {**base, **wpf}
for k, where in sorted(keys.items(), key=lambda x: x[1]):
    if k not in en:
        print(f"EKSİK  {where}: {k!r}"); fail += 1
    elif len(ph(k)) != len(ph(en[k])):
        print(f"YER TUTUCU  {where}: {k!r} -> {en[k]!r}"); fail += 1
for k in wpf:
    if k not in keys:
        print(f"KULLANILMAYAN (lang_en_wpf.json): {k!r}"); fail += 1
    if k in base:
        print(f"GEREKSİZ (lang_en.json'da var): {k!r}"); fail += 1
print(f"{len(keys)} metin, {fail} sorun")
sys.exit(1 if fail else 0)
