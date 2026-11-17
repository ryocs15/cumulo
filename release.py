"""Yeni sürüm yayınlar: sürüm numarasını yazar, derler, GitHub Releases'e yükler.

Kullanım:
    python release.py 1.1.0 "Bu sürümde neler değişti"
    python release.py 1.1.0 --notes-file notlar.md
    python release.py 1.1.0 "..." --dry-run      (her şeyi denetler, yüklemez)

Yüklenen dosyalar:
    HavaDurumu-Kurulum.exe  kurulum (Inno Setup 6 gerekir, installer.iss). Yeni kullanıcılar bunu
                    indirir; 1.4.1 ve sonrası kendini bununla sessizce günceller.
    HavaDurumu.zip  klasör kipi. Yalnızca 1.3.0–1.4.0 kurulumlarının güncellenebilmesi için.
                    (Tek dosyalık HavaDurumu.exe artık yayınlanmıyor; 1.2.0 ve öncesi elle kurulmalı.)

Gerekenler: GitHub CLI (gh) kurulu ve `gh auth login` yapılmış olmalı. Çalışan widget kapatılmalı
(derleme klasöründeki dosyalar kilitli olur).
Aynı etiketle bir TASLAK sürüm varsa (yarıda kalmış yükleme) silinip yeniden oluşturulur;
yayınlanmış bir sürüm varsa betik durur, hiçbir şeyi silmez.
"""
import hashlib
import json
import os
import re
import subprocess
import sys
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "hava_widget.py")
ONEDIR = os.path.join(HERE, "build", "onedir", "HavaDurumu")   # build.bat çıktısı
OUT = os.path.join(HERE, "build", "release")
ZIP = os.path.join(OUT, "HavaDurumu.zip")
SETUP = os.path.join(OUT, "HavaDurumu-Kurulum.exe")
ISCC = os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "Inno Setup 6", "ISCC.exe")


def fail(msg):
    print("HATA:", msg)
    sys.exit(1)


def run(cmd, check=True, capture=False):
    print(">", " ".join(cmd))
    r = subprocess.run(cmd, cwd=HERE, text=True, capture_output=capture)
    if check and r.returncode != 0:
        if capture:
            print(r.stdout, r.stderr)
        fail(f"komut başarısız ({r.returncode})")
    return r


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def make_zip():
    """Klasör kipi paketini oluşturur: kökte HavaDurumu.exe ve _internal."""
    if os.path.exists(ZIP):
        os.remove(ZIP)
    with zipfile.ZipFile(ZIP, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for dp, _, files in os.walk(ONEDIR):
            for fn in files:
                full = os.path.join(dp, fn)
                z.write(full, os.path.relpath(full, ONEDIR))


def main():
    args = sys.argv[1:]
    dry = "--dry-run" in args
    args = [a for a in args if a != "--dry-run"]
    if not args or not re.fullmatch(r"\d+\.\d+\.\d+", args[0]):
        fail('kullanım: python release.py 1.2.3 "notlar"  [--notes-file dosya] [--dry-run]')
    version = args[0]
    if "--notes-file" in args:
        i = args.index("--notes-file")
        notes = open(args[i + 1], encoding="utf-8").read()
    else:
        notes = " ".join(args[1:]).strip() or f"Sürüm {version}"

    src = open(SRC, encoding="utf-8").read()
    m = re.search(r'^UPDATE_REPO = "([^"]+)"', src, re.M)
    if not m:
        fail("hava_widget.py içinde UPDATE_REPO bulunamadı")
    repo = m.group(1)
    cur = re.search(r'^APP_VERSION = "([^"]+)"', src, re.M).group(1)
    as_tuple = lambda s: tuple(int(x) for x in s.split("."))
    tag = f"v{version}"

    if not os.path.isfile(ISCC):
        fail(f"Inno Setup bulunamadı: {ISCC}")
    # gh oturumu ve var olan sürüm
    run(["gh", "auth", "status"], capture=True)
    r = run(["gh", "release", "view", tag, "--repo", repo, "--json", "isDraft,isPrerelease"],
            check=False, capture=True)
    existing = json.loads(r.stdout) if r.returncode == 0 else None
    if existing and not existing.get("isDraft"):
        fail(f"{tag} zaten yayınlanmış. Yeni bir sürüm numarası verin.")
    if as_tuple(version) < as_tuple(cur):
        fail(f"yeni sürüm ({version}) kurulu sürümden ({cur}) küçük olamaz")

    print(f"\nDepo: {repo}\nSürüm: {cur} -> {version}" + ("  (taslak yenilenecek)" if existing else ""))
    if dry:
        print("\n--dry-run: dosyalar değiştirilmedi, hiçbir şey yüklenmedi.")
        return

    # 1) sürüm numarasını yaz
    src = re.sub(r'^APP_VERSION = "[^"]+"', f'APP_VERSION = "{version}"', src, count=1, flags=re.M)
    with open(SRC, "w", encoding="utf-8", newline="\n") as f:
        f.write(src)
    os.makedirs(OUT, exist_ok=True)
    # 2) klasör kipi (dist'e de kopyalanır) + zip
    run(["cmd", "/c", os.path.join(HERE, "build.bat")])
    if not os.path.isfile(os.path.join(ONEDIR, "HavaDurumu.exe")):
        fail("klasör kipi derlemesi oluşmadı")
    make_zip()
    run([ISCC, "/Q", f"/DAppVer={version}", os.path.join(HERE, "installer.iss")])
    if not os.path.isfile(SETUP):
        fail("kurulum dosyası oluşmadı")
    ssha, zsha = sha256(SETUP), sha256(ZIP)
    print(f"kurulum: {os.path.getsize(SETUP) / 1e6:.1f} MB  sha256 {ssha}")
    print(f"zip (eski sürümlerin güncellemesi için): {os.path.getsize(ZIP) / 1e6:.1f} MB  sha256 {zsha}")

    # 3) yükle (yarım kalmış taslak varsa önce onu kaldır)
    if existing:
        run(["gh", "release", "delete", tag, "--repo", repo, "--yes"])
    notes_path = os.path.join(OUT, "release_notes.md")
    with open(notes_path, "w", encoding="utf-8") as f:
        f.write(notes + "\n\n**İndirme:** `HavaDurumu-Kurulum.exe` — çalıştırıp kurun. "
                "Başlat menüsüne eklenir, Ayarlar > Uygulamalar'dan kaldırılabilir; "
                "sonraki sürümler uygulama içinden kendiliğinden kurulur.\n\n"
                "**Download:** `HavaDurumu-Kurulum.exe` — run it to install (no admin rights needed). "
                "The app follows your Windows language (Turkish or English); change it under "
                "Widgets and settings > Language.\n\n"
                f"SHA-256: `{ssha}`\n")
    # shortcut: zip yalnızca 1.3.0–1.4.0 kurulumlarının bu sürüme geçmesi için yükleniyor (onlar
    # kurulum dosyasını tanımaz); kullanıcılar 1.4.1+ olunca ZIP'i bu listeden çıkar.
    run(["gh", "release", "create", tag, SETUP, ZIP, "--repo", repo,
         "--title", f"Sürüm {version}", "--notes-file", notes_path])
    print(f"\nYayınlandı: https://github.com/{repo}/releases/tag/{tag}")


if __name__ == "__main__":
    main()
