"""hava_widget.py'deki her T("...") metninin İngilizcesi lang_en.py'de var mı, yer tutucular tutuyor mu.
Çalıştır: python test_lang.py"""
import ast
import os
import string

from lang_en import EN

HERE = os.path.dirname(os.path.abspath(__file__))
tree = ast.parse(open(os.path.join(HERE, "hava_widget.py"), encoding="utf-8").read())
keys = {n.args[0].value for n in ast.walk(tree)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "T"
        and n.args and isinstance(n.args[0], ast.Constant) and len(n.args) == 1}
fields = lambda s: sorted((f or "") + (c and "!" + c or "") + (sp or "")
                          for _, f, sp, c in string.Formatter().parse(s) if f is not None)
missing = sorted(keys - EN.keys())
bad = [k for k in keys & EN.keys() if fields(k) != fields(EN[k])]
unused = sorted(EN.keys() - keys)
assert not missing, f"çevirisi yok: {missing}"
assert not bad, f"yer tutucular farklı: {bad}"
assert not unused, f"kullanılmayan çeviri: {unused}"

# olay günlüğü uyarı türlerini kod olarak saklar, gösterirken çevirir; eski kayıtlar (ad) olduğu gibi kalır
import sys  # noqa: E402
sys.argv = [sys.argv[0]]
sys.path.insert(0, HERE)
import hava_widget as hw  # noqa: E402
red = hw.ALERT_COLORS["red"][0]
assert hw.event_text({"kind": "alert", "color": "red", "what": "wind, rain"}) == \
    hw.T("{} uyarı").format(red) + ": " + hw.ALERT_KINDS["wind"] + ", " + hw.ALERT_KINDS["rain"]
assert hw.event_text({"kind": "alert", "color": "red", "what": "Kuvvetli rüzgar"}).endswith(": Kuvvetli rüzgar")
assert hw.event_text({"kind": "alert", "color": "red"}) == hw.T("{} uyarı").format(red)
print(f"tamam: {len(keys)} metin")
