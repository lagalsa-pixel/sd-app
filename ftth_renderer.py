"""
ftth_renderer.py — отрисовка карты сети FTTH (схема D) на спутниковой мозаике.

Реализует те же слои, что и оригинальный ftth_outputs.py, но через
Pillow (без OpenCV). Слои: зоны (выпуклые оболочки), дропы, ствол, фидеры
(пунктир), муфты, ДХ, зонные ОРШ, ЦУ + легенда с авто-размещением +
масштабная линейка + стрелка севера.
"""
from __future__ import annotations

import io
import logging
import math
import os
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Dict, List, Optional, Tuple

import numpy as np
from PIL import Image, ImageDraw, ImageFont

logger = logging.getLogger(__name__)

GOOGLE_TILE = 'https://mt{m}.google.com/vt/lyrs=s&x={x}&y={y}&z={z}'
GUA = {'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
                     '(KHTML, like Gecko) Chrome/120.0 Safari/537.36',
       'Referer': 'https://www.google.com/maps'}
M_PER_DEG_LAT = 111320.0

C_DROP = (255, 225, 0, 225)
C_COUP = (0, 255, 225, 255)
C_COUP_OUT = (0, 60, 60, 255)
C_TRUNK = (215, 225, 240, 235)
C_HH_OUT = (25, 25, 25, 255)
C_CU = (224, 49, 49)
PALETTE = [
    (25, 113, 194), (47, 158, 68), (240, 140, 0), (156, 54, 181),
    (12, 133, 153), (230, 73, 128), (102, 168, 15), (112, 72, 232),
    (160, 90, 44),
]
HH_HDR = 150
DASH, GAP = 30, 22

_FONT_PATHS_BOLD = [
    '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf',
    '/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf',
    'C:/Windows/Fonts/arialbd.ttf',
    'C:/Windows/Fonts/segoeuib.ttf',
]
_FONT_PATHS_REG = [
    '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',
    '/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf',
    'C:/Windows/Fonts/arial.ttf',
    'C:/Windows/Fonts/segoeui.ttf',
]


def _find_font(bold=True):
    paths = _FONT_PATHS_BOLD if bold else _FONT_PATHS_REG
    for p in paths:
        if os.path.exists(p):
            return p
    return None


_FB = _find_font(bold=True)
_FR = _find_font(bold=False)


def fnt(sz, bold=True):
    path = _FB if bold else (_FR or _FB)
    if path:
        try:
            return ImageFont.truetype(path, sz)
        except Exception:
            pass
    return ImageFont.load_default()


def lon2tx(lon, z): return (lon + 180.0) / 360.0 * (1 << z)


def lat2ty(lat, z):
    lr = math.radians(lat)
    return (1.0 - math.log(math.tan(lr) + 1.0 / math.cos(lr)) / math.pi) / 2.0 * (1 << z)


def tx2lon(x, z): return x / (1 << z) * 360.0 - 180.0


def ty2lat(y, z):
    n = math.pi * (1.0 - 2.0 * y / (1 << z))
    return math.degrees(math.atan(math.sinh(n)))


def m_per_px(lat, z):
    return 156543.03392 * math.cos(math.radians(lat)) / (1 << z)


def fetch_tile(z, x, y, cache_dir):
    p = os.path.join(cache_dir, f'g{z}', str(x), f'{y}.jpg')
    os.makedirs(os.path.dirname(p), exist_ok=True)
    if os.path.exists(p):
        if os.path.getsize(p) > 0:
            with open(p, 'rb') as f:
                return f.read()
        return None
    try:
        import requests
    except ImportError:
        logger.error("requests не установлен — не могу скачать тайлы")
        return None
    for attempt in range(3):
        try:
            r = requests.get(GOOGLE_TILE.format(m=(x + y) % 4, z=z, x=x, y=y),
                              headers=GUA, timeout=30)
            if r.status_code == 200 and len(r.content) > 500:
                with open(p, 'wb') as f:
                    f.write(r.content)
                return r.content
            if r.status_code == 404:
                open(p, 'wb').close()
                return None
        except Exception as e:
            logger.debug("tile fetch error: %s", e)
            time.sleep(1.5 * (attempt + 1))
    return None


def stitch_mosaic(bbox, z, cache_dir, lat_mid, progress_cb=None):
    lon_min, lat_min, lon_max, lat_max = bbox
    tx0f, tx1f = lon2tx(lon_min, z), lon2tx(lon_max, z)
    ty0f, ty1f = lat2ty(lat_max, z), lat2ty(lat_min, z)
    x0, x1 = int(math.floor(tx0f)), int(math.ceil(tx1f))
    y0, y1 = int(math.floor(ty0f)), int(math.ceil(ty1f))
    offx, offy = (tx0f - x0) * 256, (ty0f - y0) * 256
    Wpx, Hpx = int(round((tx1f - tx0f) * 256)), int(round((ty1f - ty0f) * 256))
    tiles = [(tx, ty) for tx in range(x0, x1) for ty in range(y0, y1)]
    if progress_cb:
        progress_cb(f"Скачиваю {len(tiles)} тайлов Google z{z}...", 0.0)
    tilemap = {}
    with ThreadPoolExecutor(max_workers=8) as ex:
        for (tx, ty), data in ex.map(lambda t: (t, fetch_tile(z, t[0], t[1], cache_dir)), tiles):
            tilemap[(tx, ty)] = data
    canvas = Image.new('RGB', ((x1 - x0) * 256, (y1 - y0) * 256), (40, 40, 40))
    missing = 0
    total = len(tiles)
    for i, ((tx, ty), data) in enumerate(tilemap.items()):
        if data:
            try:
                im = Image.open(io.BytesIO(data)).convert('RGB')
                canvas.paste(im, ((tx - x0) * 256, (ty - y0) * 256))
            except Exception as e:
                logger.debug("tile paste error: %s", e)
                missing += 1
        else:
            missing += 1
        if progress_cb and i % 10 == 0:
            progress_cb(f"Сборка мозаики: {i + 1}/{total} тайлов", 0.3 + 0.5 * (i + 1) / max(1, total))
    mos = canvas.crop((int(offx), int(offy), int(offx) + Wpx, int(offy) + Hpx))
    mpp = m_per_px(lat_mid, z)
    geo = dict(west=tx2lon(tx0f, z), north=ty2lat(ty0f, z), mpp=mpp, W=Wpx, H=Hpx,
                zoom=z, missing=missing, bbox=list(bbox))
    if progress_cb:
        progress_cb(f"Мозаика {Wpx}x{Hpx}px готова", 0.8)
    return mos, geo


def hull(points):
    pts = sorted(set((round(x, 1), round(y, 1)) for x, y in points))
    if len(pts) < 3:
        return [tuple(p) for p in pts]

    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    lower = []
    for p in pts:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], p) <= 0:
            lower.pop()
        lower.append(p)
    upper = []
    for p in reversed(pts):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], p) <= 0:
            upper.pop()
        upper.append(p)
    return lower[:-1] + upper[:-1]


def draw_dashed(dr, pts, fill, width, phase=0.0):
    seg = DASH + GAP
    g = 0.0
    for (x1, y1), (x2, y2) in zip(pts, pts[1:]):
        L = math.hypot(x2 - x1, y2 - y1)
        if L < 1e-9:
            continue
        g0, g1 = g, g + L
        for k in range(int(math.floor((g0 - phase) / seg)) - 1, int(math.ceil((g1 - phase) / seg)) + 1):
            a = max(g0, k * seg + phase)
            b = min(g1, k * seg + phase + DASH)
            if b > a:
                t0, t1 = (a - g0) / L, (b - g0) / L
                dr.line([(x1 + (x2 - x1) * t0, y1 + (y2 - y1) * t0),
                          (x1 + (x2 - x1) * t1, y1 + (y2 - y1) * t1)], fill=fill, width=width)
        g += L


def draw_orsh(dr, x, y, s, col, num):
    dr.rectangle([x - s, y - int(s * 1.5), x + s, y + int(s * 1.5)],
                  fill=(40, 50, 60, 255), outline=col, width=max(2, int(s / 6)))
    f = fnt(max(10, int(s * 0.9)))
    txt = str(num)
    tw = dr.textlength(txt, font=f)
    dr.text((x - tw / 2, y - f.size / 2), txt, font=f, fill=(255, 255, 255, 255))


def draw_olt_node(dr, x, y, s):
    dr.rectangle([x - s, y - s, x + s, y + s], fill=(140, 30, 30, 255),
                  outline=(255, 255, 255, 255), width=max(2, int(s / 6)))
    dr.line([(x, y - s), (x, y - s * 2.2)], fill=(255, 255, 255, 255), width=max(2, int(s / 5)))
    dr.line([(x - s * 0.6, y - s * 1.6), (x, y - s * 2.2)], fill=(255, 255, 255, 255), width=max(2, int(s / 6)))
    dr.line([(x + s * 0.6, y - s * 1.6), (x, y - s * 2.2)], fill=(255, 255, 255, 255), width=max(2, int(s / 6)))
    f = fnt(max(10, int(s * 0.55)))
    txt = 'OLT'
    tw = dr.textlength(txt, font=f)
    dr.rectangle([x - tw / 2 - 4, y - s * 2.6, x + tw / 2 + 4, y - s * 2.2],
                  fill=(180, 0, 0, 255), outline=(255, 255, 255, 255), width=1)
    dr.text((x - tw / 2, y - s * 2.55), txt, font=f, fill=(255, 255, 255, 255))


class Labels:
    def __init__(self):
        self.boxes: List[Tuple[float, float, float, float]] = []

    def place(self, x, y, tw, th, W, H, pad=14):
        for dx, dy in ((pad, -th - pad), (-tw - pad, -th - pad), (pad, pad),
                        (-tw - pad, pad), (pad, -th // 2), (-tw - pad, -th // 2),
                        (0, -th - 2 * pad), (0, pad + 4)):
            bx, by = x + dx, y + dy
            if bx < 8 or by < 8 or bx + tw > W - 8 or by + th > H - 8:
                continue
            box = (bx - 6, by - 6, bx + tw + 6, by + th + 6)
            if any(box[0] < b[2] and box[2] > b[0] and box[1] < b[3] and box[3] > b[1] for b in self.boxes):
                continue
            self.boxes.append(box)
            return bx, by
        return None


def legend_weight(rect, hh_pts, coup_pts, orsh_pts, cu_pt):
    x0, y0, x1, y1 = rect
    w = 0
    for (x, y), k in [(p, 1) for p in hh_pts] + [(p, 2) for p in coup_pts] + [(p, 20) for p in orsh_pts]:
        if x0 - 8 <= x <= x1 + 8 and y0 - 8 <= y <= y1 + 8:
            w += k
    if cu_pt and x0 - 20 <= cu_pt[0] <= x1 + 20 and y0 - 20 <= cu_pt[1] <= y1 + 20:
        w += 50
    return w


def render_network_map(village, bbox, geo, households, network, boq, optical_budget,
                       work_dir, output_dir, cache_tiles_dir,
                       progress_cb=None, download_tiles=True):
    """Отрисовать карту сети FTTH (схема D) на спутниковой мозаике.

    Возвращает (jpg_path, preview_path).
    """
    t0 = time.time()

    def _notify(msg, frac=0.0):
        logger.info("[render] %s (%.0f%%)", msg, frac * 100)
        if progress_cb:
            try:
                progress_cb(msg, frac)
            except Exception:
                pass

    if download_tiles:
        _notify("Скачиваю спутниковые тайлы Google z18...", 0.0)
        try:
            base, geo_tiles = stitch_mosaic(tuple(bbox), 18, cache_tiles_dir,
                                              (bbox[1] + bbox[3]) / 2,
                                              progress_cb=lambda m, f: _notify(m, 0.05 + 0.30 * f))
            geo.update(geo_tiles)
        except Exception as e:
            logger.error("Не удалось скачать мозаику: %s. Использую белый фон.", e)
            W, H = geo.get('W', 2000), geo.get('H', 2000)
            base = Image.new('RGB', (W, H), (240, 240, 240))
    else:
        W, H = geo.get('W', 2000), geo.get('H', 2000)
        base = Image.new('RGB', (W, H), (240, 240, 240))

    W, H = base.size
    mpp = geo['mpp']
    _notify(f"Базовое изображение: {W}x{H}px", 0.40)

    zones_layout = boq.get('zones', [])
    cuts_count = len(zones_layout) - 1 if zones_layout else 0
    _notify(f"Зон: {cuts_count} + ЦУ", 0.45)

    zone_centers = []
    for i, z in enumerate(zones_layout):
        if z.get('px'):
            zone_centers.append((i, z['px'][0], z['px'][1]))
        else:
            zone_centers.append((i, network['anchor']['x'], network['anchor']['y']))

    def zone_of_drop(drop_poly_last_pt):
        if not zone_centers:
            return 0
        x, y = drop_poly_last_pt
        best_z, best_d = 0, float('inf')
        for zi, zx, zy in zone_centers:
            d = (x - zx) ** 2 + (y - zy) ** 2
            if d < best_d:
                best_d, best_z = d, zi
        return best_z

    zcolor = {0: C_CU}
    zname = {0: 'ЦУ'}
    for i in range(1, len(zones_layout)):
        zcolor[i] = PALETTE[(i - 1) % len(PALETTE)]
        zname[i] = f'ОРШ-{i}'

    zpts: Dict[int, List[Tuple[float, float]]] = defaultdict(list)
    for d in network['drops']:
        if d['poly']:
            last = d['poly'][-1]
            zi = zone_of_drop(last)
            zpts[zi].append(last)
    for c in network['couplers']:
        zi = zone_of_drop((c['x'], c['y']))
        zpts[zi].append((c['x'], c['y']))
    for zi, _, zx, zy in [(zc[0], None, zc[1], zc[2]) for zc in zone_centers]:
        zpts[zi].append((zx, zy))

    _notify("Рисую слои сети...", 0.50)
    ov = Image.new('RGBA', (W, H), (0, 0, 0, 0))
    dr = ImageDraw.Draw(ov)
    k = 1.0
    lw = max(2, round(2 * k))
    trunk_w = max(5, round(5 * k))

    for zi in range(len(zones_layout)):
        col = zcolor[zi]
        hp = hull(zpts.get(zi, []))
        if len(hp) >= 3:
            dr.polygon(hp, fill=col + (38,), outline=col + (170,), width=max(3, round(4 * k)))

    for d in network['drops']:
        poly = d['poly']
        if len(poly) >= 2:
            dr.line([(p[0], p[1]) for p in poly], fill=C_DROP, width=lw)

    for e in network['feeder_edges']:
        if len(e) >= 2:
            (x1, y1), (x2, y2) = e[0], e[1]
            zi = zone_of_drop(((x1 + x2) / 2, (y1 + y2) / 2))
            col = C_TRUNK if zi == 0 else zcolor[zi] + (235,)
            dr.line([(x1, y1), (x2, y2)], fill=col, width=trunk_w)

    for i in range(1, len(zones_layout)):
        zxy = zones_layout[i].get('px')
        if not zxy:
            continue
        ax, ay = network['anchor']['x'], network['anchor']['y']
        draw_dashed(dr, [(ax, ay), (zxy[0], zxy[1])], zcolor[i] + (215,),
                     max(5, round(6 * k)), phase=i * (DASH + GAP) / 2.0)

    for c in network['couplers']:
        x, y = c['x'], c['y']
        s = 6 * k
        dr.rectangle([x - s, y - s, x + s, y + s], fill=C_COUP, outline=C_COUP_OUT,
                      width=max(1, round(2 * k)))

    for d in network['drops']:
        if not d['poly']:
            continue
        last = d['poly'][-1]
        zi = zone_of_drop(last)
        col = zcolor[zi]
        x, y = last
        s = 4 * k
        dr.rectangle([x - s, y - s, x + s, y + s], fill=col + (255,), outline=C_HH_OUT,
                      width=max(1, round(k)))

    f_z1 = fnt(max(26, round(34 * k)))
    f_z2 = fnt(max(20, round(26 * k)), bold=False)
    labels = Labels()

    for i in range(1, len(zones_layout)):
        zxy = zones_layout[i].get('px')
        if not zxy:
            continue
        mx_, my_ = zxy
        r_ = 24 * k
        labels.boxes.append((mx_ - r_, my_ - r_, mx_ + r_, my_ + r_))
    ax, ay = network['anchor']['x'], network['anchor']['y']
    R0 = 40 * k
    labels.boxes.append((ax - R0, ay - R0, ax + R0, ay + R0))

    for i in range(1, len(zones_layout)):
        zxy = zones_layout[i].get('px')
        if not zxy:
            continue
        col = zcolor[i]
        x, y = zxy
        draw_orsh(dr, x, y, 16 * k, col, i)
        name = zname[i]
        dh = zones_layout[i].get('houses', 0)
        tw = max(dr.textlength(name, font=f_z1), dr.textlength(f'{dh} ДХ', font=f_z2))
        hgt = f_z1.size + f_z2.size + 2
        pos = labels.place(x, y, int(tw), hgt, W, H)
        if pos:
            bx, by = pos
            dr.rectangle([bx - 8, by - 6, bx + tw + 8, by + hgt + 6], fill=(12, 14, 20, 130))
            dr.text((bx, by), name, font=f_z1, fill=(255, 255, 255, 255),
                     stroke_width=max(2, round(4 * k)), stroke_fill=(15, 15, 15, 255))
            dr.text((bx, by + f_z1.size + 2), f'{dh} ДХ', font=f_z2, fill=(255, 235, 180, 255),
                     stroke_width=max(2, round(3 * k)), stroke_fill=(15, 15, 15, 255))

    draw_olt_node(dr, ax, ay, 30 * k)
    f_cu = fnt(max(26, round(34 * k)))
    t1 = 'ЦУ · OLT — центральный узел села'
    zone_dh_cu = zones_layout[0].get('houses', 0) if zones_layout else 0
    t2 = f"корневая зона: {zone_dh_cu} ДХ"
    pos = labels.place(ax, ay, int(dr.textlength(t1, font=f_cu)), f_cu.size + f_z2.size, W, H)
    if pos:
        bx, by = pos
        tw1 = int(dr.textlength(t1, font=f_cu))
        dr.rectangle([bx - 8, by - 6, bx + tw1 + 8, by + f_cu.size + f_z2.size + 6],
                      fill=(12, 14, 20, 130))
        dr.text((bx, by), t1, font=f_cu, fill=(255, 255, 255, 255),
                 stroke_width=max(2, round(4 * k)), stroke_fill=(120, 0, 0, 255))
        dr.text((bx, by + f_cu.size + 2), t2, font=f_z2, fill=(255, 235, 180, 255),
                 stroke_width=max(2, round(3 * k)), stroke_fill=(120, 0, 0, 255))

    _notify("Слои сети отрисованы", 0.75)

    canvas = Image.new('RGB', (W, H + HH_HDR), (10, 18, 30))
    map_rgb = base.convert('RGBA')
    map_rgb.alpha_composite(ov)
    canvas.paste(map_rgb.convert('RGB'), (0, HH_HDR))
    dh2 = ImageDraw.Draw(canvas)
    f1, f2, f3 = fnt(46), fnt(27, bold=False), fnt(27)
    dh2.text((26, 12), f"с. {village.get('name', '?')} — схема D: зонные ОРШ (сплиттеры 1:64) "
              f"при едином узле OLT", font=f1, fill=(240, 245, 250))
    dh2.text((26, 68), f"спутник Google z{geo.get('zoom', 18)} ({mpp:.2f} м/px) · "
              f"топология сети, муфты и дропы — как в схеме A", font=f2, fill=(170, 185, 200))
    st = (f"ДХ: {boq.get('dhx_served', 0)}   ·   зонных ОРШ: {boq.get('n_zones', 0)} + ЦУ   ·   "
           f"волокно-км: {boq.get('fiber_km', 0):.1f}   ·   кабель-км: {boq.get('cable_km_raw', 0):.1f}   ·   "
           f"дроп-км: {boq.get('drop_cable_km', 0):.1f}   ·   сплиттеры 1×64: {boq.get('splitters64', 0)}")
    dh2.text((26, 106), st, font=f3, fill=(255, 220, 120))

    rows = [('ЦУ', C_CU, f"корневая зона: {zones_layout[0].get('houses', 0)} ДХ · "
                          f"{zones_layout[0].get('splitters', 0)}×1:64 · "
                          f"кросс {zones_layout[0].get('orsh_ports', 0)} портов")] if zones_layout else []
    for i in range(1, len(zones_layout)):
        z = zones_layout[i]
        rows.append((zname[i], zcolor[i], f"{z.get('houses', 0)} ДХ · {z.get('splitters', 0)}×1:64 · "
                                            f"фидер {z.get('feeder_fibers', 0)} вол. · "
                                            f"{z.get('root_dist_m', 0) / 1000:.2f} км от ЦУ"))
    if len(rows) <= 1:
        rows.append(('--', (120, 120, 120), 'зонные ОРШ не образуются: село компактное, экономия ниже S_MIN'))

    fl_t, fl_r = fnt(32), fnt(28, bold=False)
    LW = 900
    LHH = 58 + len(rows) * 52 + 36 + 50 + 7 * 52 + 28
    lg = Image.new('RGBA', (LW, LHH), (8, 12, 20, 218))
    ld = ImageDraw.Draw(lg)
    s_min_km = boq.get('s_min_km', 15.0)
    ld.text((20, 14), f"ЗОНЫ ОРШ (схема D, S_MIN = {s_min_km:g})", font=fl_t, fill=(240, 240, 240))
    yy = 58
    for name, col, txt in rows:
        ld.rectangle([20, yy - 15, 52, yy + 15], fill=col, outline=(255, 255, 255), width=2)
        ld.text((64, yy - 15), f'{name}: {txt}', font=fl_r, fill=(228, 232, 238))
        yy += 52
    yy += 14
    ld.line([(20, yy), (LW - 20, yy)], fill=(90, 100, 115), width=2)
    yy += 18
    ld.text((20, yy), 'УСЛОВНЫЕ ОБОЗНАЧЕНИЯ', font=fl_t, fill=(240, 240, 240))
    yy += 50
    ld.line([(20, yy), (52, yy)], fill=C_TRUNK[:3], width=6)
    ld.text((64, yy - 15), 'ствол сети: волокна зоны ЦУ + фидеры зон', font=fl_r, fill=(228, 232, 238))
    yy += 52
    ld.line([(20, yy), (52, yy)], fill=PALETTE[0], width=6)
    ld.text((64, yy - 15), 'распределительная сеть зоны (цвет зоны)', font=fl_r, fill=(228, 232, 238))
    yy += 52
    draw_dashed(ld, [(20, yy), (52, yy)], PALETTE[1] + (235,), 7)
    ld.text((64, yy - 15), 'фидер ЦУ → зонный ОРШ (ceil(1,25 × сплиттеры зоны))', font=fl_r, fill=(228, 232, 238))
    yy += 52
    ld.line([(20, yy), (52, yy)], fill=C_DROP[:3], width=4)
    ld.text((64, yy - 15), 'дроп-кабель к домохозяйству', font=fl_r, fill=(228, 232, 238))
    yy += 52
    ld.rectangle([28, yy - 12, 44, yy + 12], fill=C_COUP[:3], outline=(0, 60, 60), width=2)
    ld.text((64, yy - 15), 'муфта оптическая (ветвление)', font=fl_r, fill=(228, 232, 238))
    yy += 52
    ld.rectangle([30, yy - 9, 42, yy + 9], fill=PALETTE[2], outline=(25, 25, 25))
    ld.text((64, yy - 15), 'домохозяйство (цвет его зоны)', font=fl_r, fill=(228, 232, 238))
    yy += 52
    draw_orsh(ld, 36, yy, 14, PALETTE[3], 1)
    ld.text((64, yy - 15), 'зонный ОРШ-N — уличный шкаф (номер зоны на шкафе)', font=fl_r, fill=(228, 232, 238))
    yy += 52
    draw_olt_node(ld, 36, yy, 14)
    ld.text((64, yy - 15), 'ЦУ · OLT — центральный узел: станция OLT, вход магистрали',
             font=fl_r, fill=(228, 232, 238))

    hh_pts = [(d['poly'][-1][0], d['poly'][-1][1]) for d in network['drops'] if d['poly']]
    coup_pts = [(c['x'], c['y']) for c in network['couplers']]
    orsh_pts = [z['px'] for z in zones_layout[1:] if z.get('px')]
    positions = {
        'BL': (16, HH_HDR + H - LHH - 16),
        'BR': (W - LW - 16, HH_HDR + H - LHH - 16),
        'ML': (16, HH_HDR + (H - LHH) // 2),
        'MR': (W - LW - 16, HH_HDR + (H - LHH) // 2),
        'TL': (16, HH_HDR + 16),
    }
    best_pos, best_w, best_name = None, None, None
    for pname, (px, py) in positions.items():
        if px < 0 or py < HH_HDR:
            continue
        w = legend_weight((px, py, px + LW, py + LHH), hh_pts, coup_pts, orsh_pts, (ax, ay))
        if best_w is None or w < best_w:
            best_pos, best_w, best_name = (px, py), w, pname
    if best_pos:
        canvas.paste(lg, best_pos, lg)

    cand = [100, 200, 500, 1000, 2000]
    valid_cands = [m2 for m2 in cand if m2 / mpp <= 1400]
    if valid_cands:
        sb_m = max(valid_cands, key=lambda m2: m2 / mpp)
        sb_px = sb_m / mpp
        x0 = W - int(sb_px) - 60
        y0 = HH_HDR + H - 46
        txt = f'{sb_m} м' if sb_m < 1000 else f'{sb_m // 1000} км'
        dh2.line([(x0, y0), (x0 + sb_px, y0)], fill=(255, 255, 255), width=5)
        for xx in (x0, x0 + sb_px / 2, x0 + sb_px):
            dh2.line([(xx, y0 - 9), (xx, y0 + 9)], fill=(255, 255, 255), width=4)
        dh2.text((x0 + sb_px / 2 - 34, y0 - 44), txt, font=fnt(27), fill=(255, 255, 255))

    nx, ny = W - 70, HH_HDR + 64
    dh2.polygon([(nx, ny - 34), (nx - 13, ny + 16), (nx, ny + 6), (nx + 13, ny + 16)],
                 outline=(255, 255, 255), width=3)
    dh2.text((nx - 8, ny + 18), 'С', font=fnt(27), fill=(255, 255, 255))

    _notify("Сохраняю карту...", 0.92)
    os.makedirs(output_dir, exist_ok=True)
    fname = os.path.join(output_dir, f"{village.get('key', 'village')}_зоны_ОРШ.jpg")
    canvas.save(fname, quality=90, subsampling=0)
    prev = canvas.copy()
    prev.thumbnail((1800, 1800), Image.LANCZOS)
    prev_path = os.path.join(output_dir, f"{village.get('key', 'village')}_preview.png")
    prev.save(prev_path)
    sz = os.path.getsize(fname) / 1e6
    dt = time.time() - t0
    _notify(f"Карта сохранена: {fname} ({sz:.1f} МБ, {dt:.0f} сек)", 1.0)
    logger.info("Карта: %s (%.1f МБ), превью: %s, [%d сек]", fname, sz, prev_path, dt)

    del base, ov, map_rgb, canvas, prev
    try:
        import gc
        gc.collect()
    except Exception:
        pass
    return fname, prev_path


if __name__ == '__main__':
    import argparse
    import json
    import sys
    logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
    ap = argparse.ArgumentParser(description='FTTH карта сети')
    ap.add_argument('--result', required=True, help='путь к *_result.json из gpon_planner')
    ap.add_argument('--work-dir', default='./gpon_work')
    ap.add_argument('--output-dir', default='./gpon_work/maps')
    ap.add_argument('--no-tiles', action='store_true', help='не скачивать тайлы')
    args = ap.parse_args()
    with open(args.result, encoding='utf-8') as f:
        result = json.load(f)
    cache_dir = os.path.join(args.work_dir, 'tiles')
    os.makedirs(cache_dir, exist_ok=True)

    def progress(msg, frac):
        print(f"  [{frac * 100:5.1f}%] {msg}")

    jpg, prev = render_network_map(
        village=result['village'], bbox=result['bbox'], geo=result['geo'],
        households=result['households'], network=result['network'],
        boq=result['boq'], optical_budget=result.get('optical_budget', {}),
        work_dir=args.work_dir, output_dir=args.output_dir, cache_tiles_dir=cache_dir,
        progress_cb=progress, download_tiles=not args.no_tiles)
    print(f"\nКарта: {jpg}")
    print(f"Превью: {prev}")
