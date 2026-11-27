"""把 GPT 画的一排连续动作帧切开，去白边、对齐，合成带透明背景的动态 WebP。
用法：cutvenv/bin/python anim_make.py <帧表.png> <输出.webp> <帧数> [每帧毫秒]
输出一行 JSON：{"ok": true, "frames": n, "w": 宽, "h": 高}
"""
import json, sys
import numpy as np
from PIL import Image
from scipy import ndimage

src, dst, n_want = sys.argv[1], sys.argv[2], int(sys.argv[3])
ms = int(sys.argv[4]) if len(sys.argv) > 4 else 130
sheet = Image.open(src).convert("RGBA")
arr = np.array(sheet).astype(np.float32)
a = arr[..., 3] / 255.0
# 去白边：半透明的边缘、发白的边缘像素直接去掉，再往里收 1 像素
rgb = arr[..., :3]
whiteish = (rgb.min(-1) > 225) & (a < 0.95)
a = np.where((a < 0.5) | whiteish, 0, a)
solid = ndimage.binary_erosion(a > 0, iterations=1)
a = np.where(solid, a, 0)
# 去掉零散碎点
lab, n = ndimage.label(a > 0)
if n:
    sz = ndimage.sum(a > 0, lab, index=range(1, n + 1))
    a = np.where(np.isin(lab, [i + 1 for i, z in enumerate(sz) if z > 400]), a, 0)
arr[..., 3] = a * 255
sheet = Image.fromarray(arr.astype(np.uint8), "RGBA")
# 按透明的竖条切帧
cols = (a > 0).any(0)
lab, n = ndimage.label(ndimage.binary_closing(cols, iterations=8))
spans = sorted([(np.where(lab == i)[0].min(), np.where(lab == i)[0].max()) for i in range(1, n + 1)], key=lambda s: -(s[1] - s[0]))
spans = sorted(s for s in spans[:n_want] if s[1] - s[0] > 40)
if len(spans) != n_want:
    print(json.dumps({"ok": False, "why": f"切出 {len(spans)} 帧，要 {n_want} 帧"})); sys.exit(0)
frames = []
for x0, x1 in spans:
    f = sheet.crop((x0, 0, x1 + 1, sheet.height))
    bb = f.getbbox()
    frames.append(f.crop((0, bb[1], f.width, bb[3])))
# 统一画布：脚底对齐、按身体重心水平对齐（避免左右抖）
W = max(f.width for f in frames) + 8
H = max(f.height for f in frames) + 8
seq = []
for f in frames:
    fa = np.array(f)[..., 3] > 0
    lower = fa[int(fa.shape[0] * .5):]  # 用下半身定水平位置，挥手不会把身体带偏
    cx = np.where(lower.any(0))[0].mean() if lower.any() else f.width / 2
    c = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    c.paste(f, (int(W / 2 - cx), H - f.height - 4), f)
    seq.append(c)
loop = seq + seq[-2:0:-1]  # 来回播，循环更顺
loop[0].save(dst, save_all=True, append_images=loop[1:], duration=ms, loop=0, quality=86, method=5, lossless=False)
seq[0].save(dst.replace(".webp", "_0.png"))
print(json.dumps({"ok": True, "frames": len(loop), "w": W, "h": H}))
