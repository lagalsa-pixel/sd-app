"""
user_map.py — обработка пользовательской карты (генплана) населённого пункта.

Позволяет загрузить изображение карты (JPG/PNG) и выполнить геопривязку:
  1. Авто: через SIFT-подобное сопоставление со спутниковой мозаикой
  2. Ручная: пользователь кликает 2+ точки и задаёт их координаты

После геопривязки карта может использоваться как:
  - Базовый слой для отрисовки сети FTTH
  - Источник данных о границе (если на карте видна застройка)
  - Дополнение к спутниковой мозаике (если генплан точнее)

Поддерживаемые форматы: JPG, PNG, BMP, WEBP, TIFF
"""
from __future__ import annotations

import logging
import math
import os
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

import numpy as np
from PIL import Image, ImageDraw

logger = logging.getLogger(__name__)

M_PER_DEG_LAT = 111320.0


@dataclass
class UserMapTransform:
    """Геопривязка пользовательской карты.

    Содержит аффинное преобразование (матрицу 3×3), переводящее
    пиксели пользовательского изображения в географические координаты.
    """
    # Размер изображения
    width: int
    height: int

    # Матрица аффинного преобразования: [px, py] → [lat, lon]
    # M = [[a, b, c], [d, e, f], [0, 0, 1]]
    # lat = a*px + b*py + c
    # lon = d*px + e*py + f
    M: np.ndarray  # shape (3, 3), dtype float64

    # Путь к файлу
    image_path: str = ""

    # Метод геопривязки
    method: str = "manual"  # "manual" | "auto_sift" | "bbox"

    # Точность (inliers для SIFT, RMSE для ручной)
    rms_error_px: float = 0.0
    n_control_points: int = 0

    def px_to_geo(self, px: float, py: float) -> Tuple[float, float]:
        """Пиксели изображения → (lat, lon)."""
        v = self.M @ np.array([px, py, 1.0])
        return float(v[0]), float(v[1])

    def geo_to_px(self, lat: float, lon: float) -> Tuple[float, float]:
        """(lat, lon) → пиксели изображения."""
        M_inv = np.linalg.inv(self.M)
        v = M_inv @ np.array([lat, lon, 1.0])
        return float(v[0]), float(v[1])

    def get_bbox(self) -> Tuple[float, float, float, float]:
        """Вернуть bbox (lon_min, lat_min, lon_max, lat_max) изображения."""
        corners = [(0, 0), (self.width, 0), (self.width, self.height), (0, self.height)]
        lats = []
        lons = []
        for px, py in corners:
            lat, lon = self.px_to_geo(px, py)
            lats.append(lat)
            lons.append(lon)
        return (min(lons), min(lats), max(lons), max(lats))

    def to_dict(self) -> dict:
        return dict(
            width=self.width, height=self.height,
            M=self.M.tolist(), image_path=self.image_path,
            method=self.method, rms_error_px=self.rms_error_px,
            n_control_points=self.n_control_points,
            bbox=self.get_bbox(),
        )

    @classmethod
    def from_dict(cls, d: dict) -> "UserMapTransform":
        return cls(
            width=d["width"], height=d["height"],
            M=np.array(d["M"], dtype=np.float64),
            image_path=d.get("image_path", ""),
            method=d.get("method", "manual"),
            rms_error_px=d.get("rms_error_px", 0.0),
            n_control_points=d.get("n_control_points", 0),
        )


def compute_affine_transform(src_points: List[Tuple[float, float]],
                              dst_points: List[Tuple[float, float]]
                              ) -> Tuple[np.ndarray, float]:
    """Вычислить аффинное преобразование по 3+ точкам.

    src_points: [(px, py), ...] — пиксели изображения
    dst_points: [(lat, lon), ...] — географические координаты

    Возвращает (M 3×3, rms_error).
    M переводит [px, py, 1] → [lat, lon, 1].
    """
    if len(src_points) < 3:
        raise ValueError(f"Нужно минимум 3 точки, получено {len(src_points)}")

    # Строим систему уравнений: A @ x = b
    # для lat = a*px + b*py + c
    # для lon = d*px + e*py + f
    A = []
    b_lat = []
    b_lon = []
    for (px, py), (lat, lon) in zip(src_points, dst_points):
        A.append([px, py, 1.0])
        b_lat.append(lat)
        b_lon.append(lon)

    A_arr = np.array(A, dtype=np.float64)
    b_lat_arr = np.array(b_lat, dtype=np.float64)
    b_lon_arr = np.array(b_lon, dtype=np.float64)

    # Решаем методом наименьших квадратов
    lat_coeffs, lat_resid, *_ = np.linalg.lstsq(A_arr, b_lat_arr, rcond=None)
    lon_coeffs, lon_resid, *_ = np.linalg.lstsq(A_arr, b_lon_arr, rcond=None)

    M = np.array([
        [lat_coeffs[0], lat_coeffs[1], lat_coeffs[2]],
        [lon_coeffs[0], lon_coeffs[1], lon_coeffs[2]],
        [0.0, 0.0, 1.0],
    ], dtype=np.float64)

    # Считаем RMSE
    rms = 0.0
    for (px, py), (lat, lon) in zip(src_points, dst_points):
        pred_lat, pred_lon = (M @ np.array([px, py, 1.0]))[:2]
        rms += (pred_lat - lat) ** 2 + (pred_lon - lon) ** 2
    rms = math.sqrt(rms / len(src_points))

    return M, rms


def load_user_image(path: str) -> Image.Image:
    """Загрузить изображение пользовательской карты."""
    img = Image.open(path)
    if img.mode != "RGB":
        img = img.convert("RGB")
    logger.info("Загружено изображение: %s (%dx%d)", path, img.size[0], img.size[1])
    return img


def georeference_by_bbox(width: int, height: int,
                          bbox: Tuple[float, float, float, float]
                          ) -> UserMapTransform:
    """Простая геопривязка по bbox (4 угла).

    bbox = (lon_min, lat_min, lon_max, lat_max)
    Предполагается, что изображение не повёрнуто (нормально ориентировано).
    """
    lon_min, lat_min, lon_max, lat_max = bbox

    # Линейная интерполяция: px→lon, py→lat
    # lon = lon_min + (px / width) * (lon_max - lon_min)
    # lat = lat_max - (py / height) * (lat_max - lat_min)

    dlon = (lon_max - lon_min) / width
    dlat = (lat_max - lat_min) / height

    M = np.array([
        [0.0, -dlat, lat_max],  # lat = -dlat * py + lat_max
        [dlon, 0.0, lon_min],   # lon = dlon * px + lon_min
        [0.0, 0.0, 1.0],
    ], dtype=np.float64)

    return UserMapTransform(
        width=width, height=height, M=M,
        method="bbox", rms_error_px=0.0, n_control_points=4,
    )


def georeference_by_points(src_points: List[Tuple[float, float]],
                            dst_points: List[Tuple[float, float]],
                            width: int, height: int,
                            image_path: str = ""
                            ) -> UserMapTransform:
    """Геопривязка по контрольным точкам (3+ точек).

    src_points: [(px, py), ...] — пиксели на изображении
    dst_points: [(lat, lon), ...] — географические координаты
    """
    M, rms = compute_affine_transform(src_points, dst_points)

    return UserMapTransform(
        width=width, height=height, M=M,
        image_path=image_path,
        method="manual", rms_error_px=rms,
        n_control_points=len(src_points),
    )


def auto_georeference(user_img: Image.Image,
                       mosaic_img: Image.Image,
                       mosaic_geo: dict,
                       progress_cb: Optional[Callable[[str, float], None]] = None
                       ) -> Optional[UserMapTransform]:
    """Авто-геопривязка пользовательского изображения по спутниковой мозаике.

    Использует простой подход:
    1. Даунскейл обоих изображений
    2. Поиск характерных точек через разность Гауссиана (DoG)
    3. Сопоставление через нормализованную взаимную корреляцию (NCC)
    4. RANSAC для отсеивания выбросов
    5. Аффинное преобразование

    Это упрощённая версия без SIFT (который требует OpenCV).
    Точность ниже, чем SIFT, но работает без OpenCV.
    """
    def _notify(msg, frac=0.0):
        logger.info("[user_map] %s (%.0f%%)", msg, frac * 100)
        if progress_cb:
            try:
                progress_cb(msg, frac)
            except Exception:
                pass

    _notify("Авто-геопривязка: подготовка изображений...", 0.0)

    # Даунскейл для скорости
    DS = 4
    user_small = user_img.resize(
        (user_img.size[0] // DS, user_img.size[1] // DS),
        Image.LANCZOS
    )
    mosaic_small = mosaic_img.resize(
        (mosaic_img.size[0] // DS, mosaic_img.size[1] // DS),
        Image.LANCZOS
    )

    user_arr = np.array(user_small, dtype=np.float64)
    mosaic_arr = np.array(mosaic_small, dtype=np.float64)

    # Перевод в grayscale
    user_gray = user_arr.mean(axis=2)
    mosaic_gray = mosaic_arr.mean(axis=2)

    _notify("Поиск характерных точек на пользовательском изображении...", 0.1)

    # Поиск "углов" через детектор Harris (упрощённый)
    def harris_corners(gray: np.ndarray, threshold: float = 0.01,
                        max_points: int = 200) -> List[Tuple[int, int]]:
        """Упрощённый детектор углов Harris."""
        h, w = gray.shape
        # Градиенты
        Ix = np.zeros_like(gray)
        Ix[:, 1:-1] = (gray[:, 2:] - gray[:, :-2]) / 2
        Iy = np.zeros_like(gray)
        Iy[1:-1, :] = (gray[2:, :] - gray[:-2, :]) / 2

        Ixx = Ix * Ix
        Iyy = Iy * Iy
        Ixy = Ix * Iy

        # Размытие (box filter)
        from scipy.ndimage import uniform_filter
        Sxx = uniform_filter(Ixx, size=5)
        Syy = uniform_filter(Iyy, size=5)
        Sxy = uniform_filter(Ixy, size=5)

        # Harris response
        det = Sxx * Syy - Sxy * Sxy
        trace = Sxx + Syy
        R = det - 0.04 * trace * trace

        # Non-maximum suppression (простой)
        corners = []
        R_max = R.max()
        if R_max <= 0:
            return corners
        threshold_val = threshold * R_max

        # Выбираем топ-N точек
        flat_R = R.flatten()
        indices = np.argsort(flat_R)[::-1][:max_points * 3]

        selected = []
        min_dist = 10
        for idx in indices:
            y, x = divmod(idx, w)
            if R[y, x] < threshold_val:
                break
            # Проверяем минимальное расстояние
            ok = True
            for sy, sx in selected:
                if abs(y - sy) < min_dist and abs(x - sx) < min_dist:
                    ok = False
                    break
            if ok:
                selected.append((y, x))
                if len(selected) >= max_points:
                    break

        return [(x, y) for y, x in selected]  # возвращаем (px, py)

    user_corners = harris_corners(user_gray, threshold=0.05, max_points=100)
    _notify(f"Найдено {len(user_corners)} точек на пользовательском изображении", 0.2)

    if len(user_corners) < 4:
        _notify("Недостаточно характерных точек для авто-привязки", 1.0)
        return None

    _notify("Поиск соответствий в мозаике...", 0.3)

    # Для каждой точки пользователя ищем лучший матч в мозаике
    # через нормализованную взаимную корреляцию (NCC)
    patch_size = 15  # окрестность для сравнения
    search_radius = 50  # радиус поиска в мозаике

    matches = []  # [(user_px, user_py, mosaic_px, mosaic_py, score)]

    for i, (ux, uy) in enumerate(user_corners):
        if i % 10 == 0:
            _notify(f"Сопоставление точек: {i}/{len(user_corners)}", 0.3 + 0.4 * i / len(user_corners))

        # Патч пользователя
        u_x0 = max(0, ux - patch_size)
        u_x1 = min(user_gray.shape[1], ux + patch_size + 1)
        u_y0 = max(0, uy - patch_size)
        u_y1 = min(user_gray.shape[0], uy + patch_size + 1)
        u_patch = user_gray[u_y0:u_y1, u_x0:u_x1]
        if u_patch.size < 10:
            continue
        u_patch_norm = u_patch - u_patch.mean()
        u_std = math.sqrt((u_patch_norm ** 2).sum())
        if u_std < 1e-6:
            continue

        # Поиск в мозаике
        best_score = -1
        best_mx, best_my = -1, -1

        m_h, m_w = mosaic_gray.shape
        for dy in range(-search_radius, search_radius + 1, 2):
            for dx in range(-search_radius, search_radius + 1, 2):
                mx = ux + dx
                my = uy + dy
                if mx < patch_size or mx >= m_w - patch_size:
                    continue
                if my < patch_size or my >= m_h - patch_size:
                    continue

                m_patch = mosaic_gray[my - patch_size:my + patch_size + 1,
                                       mx - patch_size:mx + patch_size + 1]
                if m_patch.shape != u_patch.shape:
                    continue
                m_patch_norm = m_patch - m_patch.mean()
                m_std = math.sqrt((m_patch_norm ** 2).sum())
                if m_std < 1e-6:
                    continue

                # NCC
                ncc = float((u_patch_norm * m_patch_norm).sum() / (u_std * m_std))
                if ncc > best_score:
                    best_score = ncc
                    best_mx, best_my = mx, my

        if best_score > 0.5:  # порог
            matches.append((ux * DS, uy * DS, best_mx * DS, best_my * DS, best_score))

    _notify(f"Найдено {len(matches)} соответствий (NCC > 0.5)", 0.7)

    if len(matches) < 3:
        _notify("Недостаточно соответствий для авто-привязки", 1.0)
        return None

    # RANSAC для отсеивания выбросов
    _notify("RANSAC: отсеивание выбросов...", 0.8)

    import random
    best_inliers = []
    best_M = None
    n_iterations = 100
    threshold = 5.0  # пиксели

    for _ in range(n_iterations):
        sample = random.sample(matches, min(3, len(matches)))
        src = [(m[0], m[1]) for m in sample]  # user px
        dst_mosaic = [(m[2], m[3]) for m in sample]  # mosaic px

        try:
            M_px, _ = compute_affine_transform(src, dst_mosaic)
        except Exception:
            continue

        inliers = []
        for m in matches:
            pred = M_px @ np.array([m[0], m[1], 1.0])
            dx = pred[0] - m[2]
            dy = pred[1] - m[3]
            dist = math.hypot(dx, dy)
            if dist < threshold:
                inliers.append(m)

        if len(inliers) > len(best_inliers):
            best_inliers = inliers
            best_M = M_px

    if len(best_inliers) < 3:
        _notify("RANSAC: недостаточно inliers", 1.0)
        return None

    _notify(f"RANSAC: {len(best_inliers)} inliers из {len(matches)}", 0.9)

    # Финальное преобразование с всеми inliers
    src = [(m[0], m[1]) for m in best_inliers]
    dst_mosaic_px = [(m[2], m[3]) for m in best_inliers]
    M_px, rms_px = compute_affine_transform(src, dst_mosaic_px)

    # Теперь нужно преобразование px_user → (lat, lon)
    # Сначала px_user → px_mosaic (через M_px)
    # Потом px_mosaic → (lat, lon) (через mosaic_geo)

    mpp = mosaic_geo["mpp"]
    west = mosaic_geo["west"]
    north = mosaic_geo["north"]

    def mosaic_px_to_geo(mx: float, my: float) -> Tuple[float, float]:
        lat = north - my * mpp / M_PER_DEG_LAT
        lon = west + mx * mpp / (M_PER_DEG_LAT * math.cos(math.radians(lat)))
        return lat, lon

    # Комбинируем: px_user → px_mosaic → (lat, lon)
    # Создаём контрольные точки
    src_final = [(m[0], m[1]) for m in best_inliers]
    dst_final = [mosaic_px_to_geo(m[2], m[3]) for m in best_inliers]

    M_geo, rms_geo = compute_affine_transform(src_final, dst_final)

    _notify(f"Геопривязка завершена: {len(best_inliers)} точек, RMSE={rms_geo:.6f}°", 1.0)

    return UserMapTransform(
        width=user_img.size[0],
        height=user_img.size[1],
        M=M_geo,
        image_path="",
        method="auto_ncc",
        rms_error_px=rms_px,
        n_control_points=len(best_inliers),
    )


def render_on_user_map(user_img: Image.Image,
                        transform: UserMapTransform,
                        network: dict,
                        boq: dict,
                        households: list,
                        village: dict,
                        output_dir: str,
                        progress_cb: Optional[Callable[[str, float], None]] = None
                        ) -> Tuple[str, str]:
    """Отрисовать сеть FTTH на пользовательской карте.

    Возвращает (jpg_path, preview_path).
    """
    import time
    from ftth_renderer import (hull, draw_dashed, draw_orsh, draw_olt_node,
                                Labels, legend_weight, fnt,
                                C_DROP, C_COUP, C_COUP_OUT, C_TRUNK, C_HH_OUT,
                                C_CU, PALETTE, DASH, GAP, HH_HDR)

    t0 = time.time()

    def _notify(msg, frac=0.0):
        logger.info("[user_map] %s (%.0f%%)", msg, frac * 100)
        if progress_cb:
            try:
                progress_cb(msg, frac)
            except Exception:
                pass

    _notify("Отрисовка сети на пользовательской карте...", 0.0)

    # Базовое изображение
    base = user_img.convert("RGB")
    W, H = base.size
    _notify(f"Размер карты: {W}x{H}px", 0.1)

    # Зоны
    zones_layout = boq.get("zones", [])
    cuts_count = len(zones_layout) - 1 if zones_layout else 0
    _notify(f"Зон: {cuts_count} + ЦУ", 0.15)

    zone_centers = []
    for i, z in enumerate(zones_layout):
        if z.get("px"):
            # Конвертируем px мозаики в px пользовательской карты
            # через geo координаты
            lat = z.get("lat")
            lon = z.get("lon")
            if lat is not None and lon is not None:
                px, py = transform.geo_to_px(lat, lon)
                zone_centers.append((i, px, py))
            else:
                zone_centers.append((i, network["anchor"]["x"], network["anchor"]["y"]))
        else:
            zone_centers.append((i, network["anchor"]["x"], network["anchor"]["y"]))

    def zone_of_drop(drop_last_px):
        if not zone_centers:
            return 0
        # drop_last_px в координатах мозаики — конвертируем в geo
        ax, ay = network["anchor"]["x"], network["anchor"]["y"]
        # Используем geo координаты ЦУ для привязки
        x, y = drop_last_px
        best_z, best_d = 0, float("inf")
        for zi, zx, zy in zone_centers:
            d = (x - zx) ** 2 + (y - zy) ** 2
            if d < best_d:
                best_d, best_z = d, zi
        return best_z

    zcolor = {0: C_CU}
    zname = {0: "ЦУ"}
    for i in range(1, len(zones_layout)):
        zcolor[i] = PALETTE[(i - 1) % len(PALETTE)]
        zname[i] = f"ОРШ-{i}"

    # Рисуем слои
    ov = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    dr = ImageDraw.Draw(ov)
    k = 1.0
    lw = max(2, round(2 * k))
    trunk_w = max(5, round(5 * k))

    # Конвертируем координаты сети из px мозаики в px пользовательской карты
    # Через geo: px_mosaic → geo → px_user

    # Сначала получим geo координаты из мозаики
    # Нужен mpp, west, north от мозаики — но у нас их нет здесь
    # Используем transform для конвертации

    # Упрощение: рисуем дропы, фидеры, муфты через geo координаты
    # Для этого нужны geo координаты каждой точки
    # Из network есть только px координаты мозаики
    # Конвертируем через geo привязку мозаики

    # Получаем geo привязку мозаики из network
    mosaic_geo = network.get("geo", {})
    if not mosaic_geo:
        _notify("Нет geo привязки мозаики — пропускаю", 1.0)
        return "", ""

    mpp = mosaic_geo.get("mpp", 0.4)
    west = mosaic_geo.get("west", 0)
    north = mosaic_geo.get("north", 0)

    def mosaic_px_to_user_px(mx: float, my: float) -> Tuple[float, float]:
        """Конвертировать px мозаики в px пользовательской карты."""
        lat = north - my * mpp / M_PER_DEG_LAT
        lon = west + mx * mpp / (M_PER_DEG_LAT * math.cos(math.radians(lat)))
        return transform.geo_to_px(lat, lon)

    # Зоны (выпуклые оболочки)
    zpts: Dict[int, List[Tuple[float, float]]] = {}
    for d in network.get("drops", []):
        if d.get("poly"):
            last = d["poly"][-1]
            ux, uy = mosaic_px_to_user_px(last[0], last[1])
            zi = 0  # упрощённо
            zpts.setdefault(zi, []).append((ux, uy))

    for zi in range(len(zones_layout)):
        col = zcolor[zi]
        hp = hull(zpts.get(zi, []))
        if len(hp) >= 3:
            dr.polygon(hp, fill=col + (38,), outline=col + (170,),
                         width=max(3, round(4 * k)))

    # Дропы
    for d in network.get("drops", []):
        poly = d.get("poly", [])
        if len(poly) >= 2:
            user_poly = [mosaic_px_to_user_px(p[0], p[1]) for p in poly]
            dr.line(user_poly, fill=C_DROP, width=lw)

    # Фидерные рёбра
    for e in network.get("feeder_edges", []):
        if len(e) >= 2:
            (x1, y1), (x2, y2) = e[0], e[1]
            ux1, uy1 = mosaic_px_to_user_px(x1, y1)
            ux2, uy2 = mosaic_px_to_user_px(x2, y2)
            dr.line([(ux1, uy1), (ux2, uy2)], fill=C_TRUNK, width=trunk_w)

    # Фидерные пути (пунктир)
    feeder_paths = network.get("feeder_paths", [])
    for i in range(1, len(zones_layout)):
        if i < len(feeder_paths) and feeder_paths[i]:
            path_pts = [mosaic_px_to_user_px(p[0], p[1]) for p in feeder_paths[i]]
            if len(path_pts) >= 2:
                draw_dashed(dr, path_pts, zcolor[i] + (215,),
                             max(5, round(6 * k)), phase=i * (DASH + GAP) / 2.0)

    # Муфты
    for c in network.get("couplers", []):
        ux, uy = mosaic_px_to_user_px(c["x"], c["y"])
        s = 6 * k
        dr.rectangle([ux - s, uy - s, ux + s, uy + s],
                      fill=C_COUP, outline=C_COUP_OUT, width=max(1, round(2 * k)))

    # Домохозяйства
    for d in network.get("drops", []):
        if not d.get("poly"):
            continue
        last = d["poly"][-1]
        ux, uy = mosaic_px_to_user_px(last[0], last[1])
        col = C_CU
        s = 4 * k
        dr.rectangle([ux - s, uy - s, ux + s, uy + s],
                      fill=col + (255,), outline=C_HH_OUT, width=max(1, round(k)))

    # ОРШ
    labels = Labels()
    for i in range(1, len(zones_layout)):
        if i < len(zone_centers):
            _, zx, zy = zone_centers[i]
            draw_orsh(dr, zx, zy, 16 * k, zcolor[i], i)
            labels.boxes.append((zx - 24, zy - 24, zx + 24, zy + 24))

    # ЦУ
    ax, ay = mosaic_px_to_user_px(network["anchor"]["x"], network["anchor"]["y"])
    draw_olt_node(dr, ax, ay, 30 * k)
    labels.boxes.append((ax - 40, ay - 40, ax + 40, ay + 40))

    _notify("Слои отрисованы", 0.9)

    # Композитим
    canvas = Image.new("RGB", (W, H + HH_HDR), (10, 18, 30))
    map_rgb = base.convert("RGBA")
    map_rgb.alpha_composite(ov)
    canvas.paste(map_rgb.convert("RGB"), (0, HH_HDR))
    dh2 = ImageDraw.Draw(canvas)

    # Шапка
    f1, f2, f3 = fnt(46), fnt(27, bold=False), fnt(27)
    dh2.text((26, 12), f"с. {village.get('name', '?')} — карта пользователя (схема D)",
              font=f1, fill=(240, 245, 250))
    dh2.text((26, 68), f"Пользовательская карта: {transform.method}, "
              f"точек привязки: {transform.n_control_points}",
              font=f2, fill=(170, 185, 200))
    st = (f"ДХ: {boq.get('dhx_served', 0)}   ·   зон ОРШ: {boq.get('n_zones', 0)} + ЦУ   ·   "
           f"волокно-км: {boq.get('fiber_km', 0):.1f}")
    dh2.text((26, 106), st, font=f3, fill=(255, 220, 120))

    # Сохранение
    os.makedirs(output_dir, exist_ok=True)
    fname = os.path.join(output_dir, f"{village.get('key', 'village')}_user_map.jpg")
    canvas.save(fname, quality=90, subsampling=0)

    prev = canvas.copy()
    prev.thumbnail((1800, 1800), Image.LANCZOS)
    prev_path = os.path.join(output_dir, f"{village.get('key', 'village')}_user_map_preview.png")
    prev.save(prev_path)

    sz = os.path.getsize(fname) / 1e6
    dt = time.time() - t0
    _notify(f"Карта сохранена: {fname} ({sz:.1f} МБ, {dt:.0f} сек)", 1.0)

    del base, ov, map_rgb, canvas, prev
    try:
        import gc
        gc.collect()
    except Exception:
        pass

    return fname, prev_path
