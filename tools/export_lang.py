"""lang_en.py (Python sürümü) -> src/Core/lang_en.json. İngilizce metinlerin tek kaynağı Python'daki sözlük;
bu dosya yalnızca oradan üretilir, elle düzenlenmez. Kullanım: python tools/export_lang.py"""
import json, os, sys

here = os.path.dirname(os.path.abspath(__file__))
py = os.path.normpath(os.path.join(here, "..", "..", "hava_durumu_widget"))
sys.path.insert(0, py)
from lang_en import EN  # noqa: E402

out = os.path.join(here, "..", "src", "Core", "lang_en.json")
with open(out, "w", encoding="utf-8", newline="\n") as f:
    json.dump(EN, f, ensure_ascii=False, indent=1, sort_keys=True)
print(f"{len(EN)} metin -> {os.path.normpath(out)}")
