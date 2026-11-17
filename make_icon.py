"""Uygulama simgesi (Cumulo): mavi zeminde güneş ve bulut. icon.ico + icon.png üretir (build.bat çağırır)."""
import os

from PIL import Image, ImageDraw, ImageFilter

K = 4            # süper örnekleme
S = 256 * K


def gradient(c1, c2, angle):
    g = Image.linear_gradient("L").resize((S * 2, S * 2)).rotate(angle - 90, resample=Image.BICUBIC)
    g = g.crop((S // 2, S // 2, S * 3 // 2, S * 3 // 2))
    return Image.composite(Image.new("RGB", (S, S), c2), Image.new("RGB", (S, S), c1), g).convert("RGBA")


def cloud(d, cx, cy, w, fill):
    """Üç parçalı bulut; cx, cy taban ortası, w genişlik."""
    h = w * 0.42
    d.rounded_rectangle([cx - w / 2, cy - h * 0.55, cx + w / 2, cy], radius=h * 0.5, fill=fill)
    d.ellipse([cx - w * 0.38, cy - h * 1.15, cx + w * 0.04, cy - h * 1.15 + w * 0.42], fill=fill)
    d.ellipse([cx - w * 0.08, cy - h * 1.62, cx + w * 0.44, cy - h * 1.62 + w * 0.52], fill=fill)


def draw():
    bg = gradient((36, 86, 178), (88, 170, 240), 70)
    mask = Image.new("L", (S, S), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, S - 1, S - 1], radius=int(S * 0.22), fill=255)
    bg.putalpha(mask)
    # güneş ve hâlesi
    sx, sy, sr = S * 0.62, S * 0.36, S * 0.2
    sun = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    ImageDraw.Draw(sun).ellipse([sx - sr * 1.5, sy - sr * 1.5, sx + sr * 1.5, sy + sr * 1.5],
                                fill=(255, 200, 80, 90))
    sun = sun.filter(ImageFilter.GaussianBlur(S * 0.04))
    ImageDraw.Draw(sun).ellipse([sx - sr, sy - sr, sx + sr, sy + sr], fill=(255, 196, 60, 255))
    bg.alpha_composite(sun)
    # bulut ve gölgesi
    fg = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    cloud(ImageDraw.Draw(fg), S * 0.46, S * 0.76, S * 0.66, (255, 255, 255, 255))
    sh = Image.new("RGBA", (S, S), (10, 20, 60, 0))
    sh.putalpha(fg.getchannel("A").point(lambda v: v * 90 // 255).filter(ImageFilter.GaussianBlur(9 * K)))
    bg.alpha_composite(sh, (0, 5 * K))
    bg.alpha_composite(fg)
    return bg.resize((256, 256), Image.LANCZOS)


if __name__ == "__main__":
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "icon.ico")
    img = draw()
    img.save(out, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    img.save(out.replace(".ico", ".png"))
    print("yazıldı:", out)
