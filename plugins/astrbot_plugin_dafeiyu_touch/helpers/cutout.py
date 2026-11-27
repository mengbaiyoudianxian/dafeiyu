"""把场景图里的大肥鱼抠出来：原图和「去掉她的空场景」做差，差得多的那一大块就是她；
再在这块范围里用 rembg 动漫人物模型修边缘（只取两者都认可的部分，椅子、旁边的人不会被带进来）。
用法：cutvenv/bin/python cutout.py <原图> <空场景> <输出抠图.png> <输出底图.jpg>
输出一行 JSON：{"ok": true, "bbox": [x, y, w, h]（相对整图 0~1）, "W": 宽, "H": 高}
"""
import json, sys
import numpy as np
from PIL import Image, ImageFilter
from scipy import ndimage

src, plate_p, dst, plate_out = sys.argv[1:5]
im = Image.open(src).convert("RGB")
W, H = im.size
plate = Image.open(plate_p).convert("RGB").resize((W, H), Image.LANCZOS)
a = np.asarray(im.filter(ImageFilter.GaussianBlur(2)), dtype=np.float32)
b = np.asarray(plate.filter(ImageFilter.GaussianBlur(2)), dtype=np.float32)
diff = np.sqrt(((a - b) ** 2).sum(-1))
m = diff > 38
m = ndimage.binary_opening(m, iterations=2)
m = ndimage.binary_closing(m, iterations=6)
lab, n = ndimage.label(m)
if n == 0:
    print(json.dumps({"ok": False, "why": "原图和空场景几乎一样"})); sys.exit(0)
hsv = np.array(im.convert("HSV")).astype(np.float32)
h, s, v = hsv[..., 0] * 360 / 255, hsv[..., 1] / 255, hsv[..., 2] / 255
blue = (h > 185) & (h < 240) & (s > 0.22) & (v > 0.45)
sizes = ndimage.sum(m, lab, index=range(1, n + 1))
scores = ndimage.sum(blue, lab, index=range(1, n + 1)) + sizes * 0.05
best = int(np.argmax(scores)) + 1
keep = lab == best
ys, xs = np.where(keep)
x0, x1, y0, y1 = xs.min(), xs.max(), ys.min(), ys.max()
pad = int(0.05 * max(W, H))
for i in range(1, n + 1):  # 附近的碎块（头发丝、尾巴尖）并进来
    if i == best:
        continue
    yy, xx = np.where(lab == i)
    if xx.min() >= x0 - pad and xx.max() <= x1 + pad and yy.min() >= y0 - pad and yy.max() <= y1 + pad:
        keep |= lab == i
keep = ndimage.binary_fill_holes(keep)
ys, xs = np.where(keep)
x0, x1, y0, y1 = max(0, xs.min() - pad), min(W, xs.max() + pad), max(0, ys.min() - pad), min(H, ys.max() + pad)
alpha = ndimage.gaussian_filter(keep.astype(np.float32), 1.2)
try:  # rembg 修边缘：只在她这块范围里跑
    from rembg import new_session, remove
    crop = im.crop((x0, y0, x1, y1))
    rm = np.asarray(remove(crop, session=new_session("isnet-anime"), only_mask=True), dtype=np.float32) / 255.0
    region = ndimage.binary_dilation(keep[y0:y1, x0:x1], iterations=6)
    rm = np.where(rm < 0.35, 0, rm)
    # 只要 rembg 认为是人物、且在差分范围里的像素；再去掉小碎渣
    solid = ndimage.binary_opening((rm > 0.5) & region, iterations=2)
    lab2, n2 = ndimage.label(solid)
    if n2:
        sz = ndimage.sum(solid, lab2, index=range(1, n2 + 1))
        solid = np.isin(lab2, [i + 1 for i, z in enumerate(sz) if z > 0.02 * sz.max()])
    alpha[y0:y1, x0:x1] = np.where(ndimage.binary_dilation(solid, iterations=1), rm, 0)
except Exception as e:
    print(json.dumps({"warn": str(e)[:100]}), file=sys.stderr)
ys, xs = np.where(alpha > 0.05)
x0, x1, y0, y1 = xs.min(), xs.max() + 1, ys.min(), ys.max() + 1
rgba = np.dstack([np.asarray(im), (np.clip(alpha, 0, 1) * 255).astype(np.uint8)])[y0:y1, x0:x1]
Image.fromarray(rgba, "RGBA").save(dst, optimize=True)
plate.save(plate_out, "JPEG", quality=84, optimize=True, progressive=True)
print(json.dumps({"ok": True, "bbox": [x0 / W, y0 / H, (x1 - x0) / W, (y1 - y0) / H], "W": W, "H": H,
                  "area": round(float((alpha > 0.5).sum()) / (W * H), 3)}))
