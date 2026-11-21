"""Cumulo (WPF) yeni sürüm yayınlar: sürüm numarasını yazar, derler, kurulum dosyasını GitHub Releases'e yükler.
YALNIZCA kullanıcı "yayınla" deyince çalıştırılır.

Kullanım:
    python release.py 1.7.0 --notes-file notlar.md [--prerelease]
    python release.py 1.7.0 --notes-file notlar.md --dry-run      (denetler; hiçbir dosyayı değiştirmez)
    python release.py 1.7.0 --notes-file notlar.md --no-upload    (her şeyi yapar, yalnızca yüklemez: prova)

Yüklenen dosyalar (ikisi aynı dosya):
    Cumulo-Setup.exe        insanlar ve 1.7+ için (WPF önce bunu arar)
    HavaDurumu-Kurulum.exe  1.4.1–1.6.0 (Python) istemcilerinin otomatik güncellemesi bu adı arar
    (HavaDurumu.zip artık yok: onu bekleyen 1.3.0–1.4.0 depo adı değiştiğinden beri güncelleyemiyor.)

--prerelease: GitHub "en son sürüm" sorgusu ön sürümleri döndürmez -> otomatik güncelleme gitmez (kademeli yayın).
Gerekenler: gh (oturum açık), Inno Setup 6, .NET 10 SDK (%LOCALAPPDATA%\\dotnet10 ya da PATH), Python (çeviri betikleri).
Aynı etiketle TASLAK sürüm varsa silinip yeniden oluşturulur; yayınlanmış sürüm varsa betik durur.
"""
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
CONSTANTS = os.path.join(HERE, "src", "Core", "Constants.cs")
CSPROJ = os.path.join(HERE, "HavaDurumu.csproj")
OUT = os.path.join(HERE, "build", "release")
SETUP = os.path.join(OUT, "Cumulo-Setup.exe")
SETUP_OLD = os.path.join(OUT, "HavaDurumu-Kurulum.exe")
DLL = os.path.join(HERE, "build", "publish", "HavaDurumu.dll")
ISCC = os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "Inno Setup 6", "ISCC.exe")
REPO = "ryocs15/cumulo"
# yayın derlemesinde bulunmaması gereken test izleri (güncelleme testi derlemesi: -p:UpdateTest=1)
FORBIDDEN = ["cumulo-test"]
# sürüm satırı: test bloğundaki 99.0.0 değil, #else altındaki gerçek sürüm
VER_RE = re.compile(r'(#else\s*\n\s*public const string AppVersion = ")([^"]+)(";)')


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


def main():
    args = sys.argv[1:]
    dry, no_upload, pre = "--dry-run" in args, "--no-upload" in args, "--prerelease" in args
    args = [a for a in args if a not in ("--dry-run", "--no-upload", "--prerelease")]
    if not args or not re.fullmatch(r"\d+\.\d+\.\d+", args[0]) or "--notes-file" not in args:
        fail("kullanım: python release.py 1.2.3 --notes-file notlar.md [--prerelease] [--dry-run | --no-upload]")
    version = args[0]
    notes = open(args[args.index("--notes-file") + 1], encoding="utf-8").read().strip()

    cs = open(CONSTANTS, encoding="utf-8").read()
    m = VER_RE.search(cs)
    if not m:
        fail("Constants.cs içinde AppVersion (#else dalı) bulunamadı")
    cur = m.group(2)
    proj = open(CSPROJ, encoding="utf-8").read()
    pv = re.search(r"<Version>([^<]+)</Version>", proj)
    if not pv or pv.group(1) != cur:
        fail(f"csproj <Version> ({pv.group(1) if pv else '-'}) ile Constants.AppVersion ({cur}) farklı")
    as_tuple = lambda s: tuple(int(x) for x in s.split("."))
    if as_tuple(version) < as_tuple(cur):
        fail(f"yeni sürüm ({version}) mevcut sürümden ({cur}) küçük olamaz")
    if not os.path.isfile(ISCC):
        fail(f"Inno Setup bulunamadı: {ISCC}")
    tag = f"v{version}"
    run(["gh", "auth", "status"], capture=True)
    r = run(["gh", "release", "view", tag, "--repo", REPO, "--json", "isDraft"], check=False, capture=True)
    existing = json.loads(r.stdout) if r.returncode == 0 else None
    if existing and not existing.get("isDraft"):
        fail(f"{tag} zaten yayınlanmış. Yeni bir sürüm numarası verin.")

    print(f"\nDepo: {REPO}\nSürüm: {cur} -> {version}" + ("  (ön sürüm)" if pre else "")
          + ("  (taslak yenilenecek)" if existing else ""))
    if dry:
        print("\n--dry-run: dosyalar değiştirilmedi, hiçbir şey yüklenmedi.")
        return

    # 1) sürüm numarası (Constants.cs + csproj)
    if version != cur:
        open(CONSTANTS, "w", encoding="utf-8", newline="").write(VER_RE.sub(rf"\g<1>{version}\g<3>", cs, count=1))
        open(CSPROJ, "w", encoding="utf-8", newline="").write(proj.replace(f"<Version>{cur}</Version>", f"<Version>{version}</Version>", 1))
    # 2) derleme (çeviri denetimi + öz-test dahil) ve kurulum
    run(["cmd", "/c", os.path.join(HERE, "build.bat")])
    blob = open(DLL, "rb").read()
    for f in FORBIDDEN:
        if f.encode("utf-16-le") in blob:
            fail(f"yayın derlemesinde test izi var: {f} (UpdateTest ile derlenmiş olabilir)")
    run([ISCC, "/Q", f"/DAppVer={version}", os.path.join(HERE, "installer.iss")])
    if not os.path.isfile(SETUP):
        fail("kurulum dosyası oluşmadı")
    shutil.copyfile(SETUP, SETUP_OLD)
    s = sha256(SETUP)
    if sha256(SETUP_OLD) != s:
        fail("iki kurulum dosyası aynı değil")
    print(f"kurulum: {os.path.getsize(SETUP) / 1e6:.1f} MB  sha256 {s}")

    notes_path = os.path.join(OUT, "release_notes.md")
    with open(notes_path, "w", encoding="utf-8") as f:
        f.write(notes + "\n\n**İndirme:** `Cumulo-Setup.exe` — çalıştırıp kurun (yönetici izni gerekmez). "
                "Başlat menüsüne eklenir, Ayarlar > Uygulamalar'dan kaldırılabilir; sonraki sürümler uygulama içinden "
                "kendiliğinden kurulur. `HavaDurumu-Kurulum.exe` aynı dosyadır; eski sürümlerin otomatik güncellemesi içindir.\n\n"
                "**Download:** `Cumulo-Setup.exe` — run it to install (no admin rights needed). "
                "`HavaDurumu-Kurulum.exe` is the same file, kept for automatic updates from older versions.\n\n"
                f"SHA-256: `{s}`\n")
    if no_upload:
        print(f"\n--no-upload: derlendi, yüklenmedi.\n  {SETUP}\n  {SETUP_OLD}\n  {notes_path}")
        return

    # 3) yükle (yarım kalmış taslak varsa önce onu kaldır)
    if existing:
        run(["gh", "release", "delete", tag, "--repo", REPO, "--yes"])
    run(["gh", "release", "create", tag, SETUP, SETUP_OLD, "--repo", REPO, "--title", f"Sürüm {version}",
         "--notes-file", notes_path] + (["--prerelease"] if pre else []))
    print(f"\nYayınlandı: https://github.com/{REPO}/releases/tag/{tag}")


if __name__ == "__main__":
    main()
