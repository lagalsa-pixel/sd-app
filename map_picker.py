"""
map_picker.py — интерактивный выбор границы населённого пункта на карте.

Пользователь может:
  1. Указать координаты центра села
  2. Нажать "Загрузить карту" — скачивается мозаика Google Satellite z16
  3. Кликать на карте (ЛКМ) для добавления точек полигона границы
  4. ПКМ — отменить последнюю точку
  5. "Замкнуть полигон" — закрыть контур
  6. "Авто-граница" — автоматически определить по застройке OSM
  7. Нажать "OK" — выбранная граница возвращается как bbox + polygon

Используется из main.py для задания границы вручную.
"""
from __future__ import annotations

import math
import os
import threading
import tkinter as tk
from tkinter import ttk, messagebox
from typing import List, Optional, Tuple

from PIL import Image, ImageTk

M_PER_DEG_LAT = 111320.0


def lon2tx(lon, z):
    return (lon + 180.0) / 360.0 * (1 << z)


def lat2ty(lat, z):
    lr = math.radians(lat)
    return (1.0 - math.log(math.tan(lr) + 1.0 / math.cos(lr)) / math.pi) / 2.0 * (1 << z)


def tx2lon(x, z):
    return x / (1 << z) * 360.0 - 180.0


def ty2lat(y, z):
    n = math.pi * (1.0 - 2.0 * y / (1 << z))
    return math.degrees(math.atan(math.sinh(n)))


def m_per_px(lat, z):
    return 156543.03392 * math.cos(math.radians(lat)) / (1 << z)


def geo_to_px(lat, lon, west, north, mpp):
    return ((lon - west) * (M_PER_DEG_LAT * math.cos(math.radians(lat))) / mpp,
            (north - lat) * M_PER_DEG_LAT / mpp)


def px_to_geo(x, y, lat_ref, west, north, mpp):
    return (north - y * mpp / M_PER_DEG_LAT,
            west + x * mpp / (M_PER_DEG_LAT * math.cos(math.radians(lat_ref))))


class MapPickerDialog(tk.Toplevel):
    """Диалог выбора границы населённого пункта на спутниковой карте.

    Использование:
        dialog = MapPickerDialog(parent, lat=50.0, lon=82.0, radius=2500.0)
        parent.wait_window(dialog)
        bbox = dialog.result          # (lon_min, lat_min, lon_max, lat_max) или None
        polygon = dialog.polygon_result  # [(lat, lon), ...] или None
    """

    ZOOM = 16  # z16 — компромисс между детализацией и размером

    def __init__(self, parent, lat: float, lon: float, radius: float = 2500.0,
                 cache_dir: str = '.'):
        super().__init__(parent)
        self.title("Задание границы населённого пункта")
        self.geometry("1100x750")
        self.minsize(900, 600)

        self.center_lat = lat
        self.center_lon = lon
        self.radius_m = radius
        self.cache_dir = cache_dir
        os.makedirs(cache_dir, exist_ok=True)

        self.result: Optional[Tuple[float, float, float, float]] = None
        self.polygon_result: Optional[List[Tuple[float, float]]] = None

        self.mosaic_image = None
        self.mosaic_photo = None
        self.geo = None
        self.poly_points: List[Tuple[float, float]] = []
        self.poly_canvas_ids: List[int] = []

        self._build_ui()
        self.after(100, self._auto_load_map)

    def _build_ui(self):
        top = ttk.Frame(self)
        top.pack(fill=tk.X, padx=8, pady=4)

        ttk.Label(top, text=f"Центр: {self.center_lat:.4f}, {self.center_lon:.4f}").pack(side=tk.LEFT)
        ttk.Label(top, text=f"  Радиус: {self.radius_m:.0f} м").pack(side=tk.LEFT, padx=8)

        ttk.Button(top, text="🔄 Перезагрузить", command=self._auto_load_map).pack(side=tk.RIGHT, padx=4)
        ttk.Button(top, text="🤖 Авто-граница", command=self._auto_bbox).pack(side=tk.RIGHT, padx=4)

        info = ttk.Frame(self)
        info.pack(fill=tk.X, padx=8, pady=2)
        self.info_var = tk.StringVar(value="Загрузка карты...")
        ttk.Label(info, textvariable=self.info_var, foreground="blue").pack(side=tk.LEFT)

        map_frame = ttk.Frame(self)
        map_frame.pack(fill=tk.BOTH, expand=True, padx=8, pady=4)

        self.canvas = tk.Canvas(map_frame, bg="#2b2b2b", highlightthickness=0,
                                 cursor="crosshair")
        h_scroll = ttk.Scrollbar(map_frame, orient="horizontal", command=self.canvas.xview)
        v_scroll = ttk.Scrollbar(map_frame, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(xscrollcommand=h_scroll.set, yscrollcommand=v_scroll.set)

        self.canvas.grid(row=0, column=0, sticky="nsew")
        v_scroll.grid(row=0, column=1, sticky="ns")
        h_scroll.grid(row=1, column=0, sticky="ew")
        map_frame.rowconfigure(0, weight=1)
        map_frame.columnconfigure(0, weight=1)

        self.canvas.bind("<Button-1>", self._on_click)
        self.canvas.bind("<Button-3>", self._on_right_click)

        bottom = ttk.Frame(self)
        bottom.pack(fill=tk.X, padx=8, pady=4)

        ttk.Label(bottom, text="ЛКМ — добавить точку | ПКМ — отменить").pack(side=tk.LEFT)
        ttk.Button(bottom, text="🗑 Очистить", command=self._clear_points).pack(side=tk.LEFT, padx=8)
        ttk.Button(bottom, text="📐 Замкнуть", command=self._close_polygon).pack(side=tk.LEFT, padx=4)

        ttk.Button(bottom, text="Отмена", command=self._cancel).pack(side=tk.RIGHT, padx=4)
        ttk.Button(bottom, text="✓ OK", command=self._ok).pack(side=tk.RIGHT, padx=4)

    def _auto_load_map(self):
        dlat = self.radius_m / M_PER_DEG_LAT
        dlon = self.radius_m / (M_PER_DEG_LAT * math.cos(math.radians(self.center_lat)))
        bbox = (self.center_lon - dlon, self.center_lat - dlat,
                self.center_lon + dlon, self.center_lat + dlat)

        self.info_var.set("Скачиваю спутниковые тайлы Google z16...")

        def worker():
            try:
                from ftth_renderer import stitch_mosaic
                mosaic, geo = stitch_mosaic(bbox, self.ZOOM, self.cache_dir,
                                             self.center_lat,
                                             progress_cb=lambda m, f: self.after(0, lambda: self.info_var.set(m)))
                self.after(0, lambda: self._on_mosaic_loaded(mosaic, geo))
            except Exception as e:
                self.after(0, lambda: self._on_load_error(str(e)))

        threading.Thread(target=worker, daemon=True).start()

    def _on_mosaic_loaded(self, mosaic, geo):
        self.mosaic_image = mosaic
        self.geo = geo

        # Уменьшаем если слишком большая
        max_dim = 3000
        w, h = mosaic.size
        if max(w, h) > max_dim:
            ratio = max_dim / max(w, h)
            new_size = (int(w * ratio), int(h * ratio))
            mosaic_small = mosaic.resize(new_size, Image.LANCZOS)
            geo = dict(geo)
            geo['mpp'] = geo['mpp'] / ratio
            geo['W'], geo['H'] = new_size
            self.geo = geo
            self.mosaic_image = mosaic_small

        self.mosaic_photo = ImageTk.PhotoImage(self.mosaic_image)
        cw, ch = self.mosaic_image.size
        self.canvas.configure(scrollregion=(0, 0, cw, ch),
                               width=min(cw, 1000), height=min(ch, 600))
        self.canvas.create_image(0, 0, anchor="nw", image=self.mosaic_photo)

        # Центр крестиком
        cx, cy = geo_to_px(self.center_lat, self.center_lon,
                            self.geo['west'], self.geo['north'], self.geo['mpp'])
        s = 10
        self.canvas.create_line(cx - s, cy, cx + s, cy, fill="red", width=2)
        self.canvas.create_line(cx, cy - s, cx, cy + s, fill="red", width=2)
        self.canvas.create_text(cx + 12, cy - 12, text="центр села",
                                 fill="yellow", font=("Segoe UI", 9, "bold"))

        self.info_var.set(f"Карта: {geo['W']}×{geo['H']}px. Кликайте ЛКМ для точек границы.")

    def _on_load_error(self, error):
        self.info_var.set(f"Ошибка: {error}")
        messagebox.showerror("Ошибка", f"Не удалось загрузить карту:\n{error}")

    def _on_click(self, event):
        if self.mosaic_image is None:
            return
        canvas_x = self.canvas.canvasx(event.x)
        canvas_y = self.canvas.canvasy(event.y)
        self._add_point(canvas_x, canvas_y)

    def _on_right_click(self, event):
        self._undo_point()

    def _add_point(self, x, y):
        self.poly_points.append((x, y))
        r = 5
        dot_id = self.canvas.create_oval(x - r, y - r, x + r, y + r,
                                           fill="#00ff00", outline="white", width=2)
        self.poly_canvas_ids.append(dot_id)
        if len(self.poly_points) > 1:
            px, py = self.poly_points[-2]
            line_id = self.canvas.create_line(px, py, x, y,
                                                fill="#00ff00", width=2, dash=(4, 2))
            self.poly_canvas_ids.append(line_id)
        self.info_var.set(f"Точек: {len(self.poly_points)}. ПКМ — отменить, 'Замкнуть' — закрыть.")

    def _undo_point(self):
        if not self.poly_points:
            return
        self.poly_points.pop()
        if self.poly_canvas_ids:
            self.canvas.delete(self.poly_canvas_ids.pop())
        if self.poly_canvas_ids and len(self.poly_points) >= 1:
            self.canvas.delete(self.poly_canvas_ids.pop())
        self.info_var.set(f"Точек: {len(self.poly_points)}")

    def _clear_points(self):
        for cid in self.poly_canvas_ids:
            self.canvas.delete(cid)
        self.poly_points = []
        self.poly_canvas_ids = []
        self.info_var.set("Граница очищена. Кликайте ЛКМ для точек.")

    def _close_polygon(self):
        if len(self.poly_points) < 3:
            messagebox.showwarning("Мало точек", "Нужно минимум 3 точки.")
            return
        x1, y1 = self.poly_points[0]
        x2, y2 = self.poly_points[-1]
        line_id = self.canvas.create_line(x2, y2, x1, y1, fill="#00ff00", width=2, dash=(4, 2))
        self.poly_canvas_ids.append(line_id)
        self.info_var.set(f"Полигон замкнут: {len(self.poly_points)} точек. Нажмите OK.")

    def _auto_bbox(self):
        if self.mosaic_image is None:
            messagebox.showinfo("Нет карты", "Дождитесь загрузки карты.")
            return

        def worker():
            try:
                from gpon_planner import (fetch_osm_bbox, parse_osm, osm_features,
                                            detect_bbox_vectorized, DEFAULT_PARAMS)
                P = lambda name, default=None: DEFAULT_PARAMS.get(name, default)

                dlat = self.radius_m / M_PER_DEG_LAT
                dlon = self.radius_m / (M_PER_DEG_LAT * math.cos(math.radians(self.center_lat)))
                probe = (self.center_lon - dlon, self.center_lat - dlat,
                         self.center_lon + dlon, self.center_lat + dlat)

                self.after(0, lambda: self.info_var.set("Скачиваю OSM..."))

                n_split = max(1, int(math.ceil((probe[2] - probe[0]) * M_PER_DEG_LAT
                                                 * math.cos(math.radians(self.center_lat)) / 2100)))
                m_split = max(1, int(math.ceil((probe[3] - probe[1]) * M_PER_DEG_LAT / 2100)))
                all_nodes, all_ways = {}, {}
                osm_cache = os.path.join(self.cache_dir, 'osm_raw')
                os.makedirs(osm_cache, exist_ok=True)

                for i in range(n_split):
                    for j in range(m_split):
                        x0 = probe[0] + (probe[2] - probe[0]) * i / n_split
                        x1 = probe[0] + (probe[2] - probe[0]) * (i + 1) / n_split
                        y0 = probe[1] + (probe[3] - probe[1]) * j / m_split
                        y1 = probe[1] + (probe[3] - probe[1]) * (j + 1) / m_split
                        cache_path = os.path.join(osm_cache,
                                                    f"picker_{x0:.4f}_{y0:.4f}_{x1:.4f}_{y1:.4f}.xml")
                        data = fetch_osm_bbox(x0, y0, x1, y1, cache_path)
                        if not data:
                            continue
                        n2, w2 = parse_osm(data)
                        all_nodes.update(n2)
                        all_ways.update(w2)

                roads, buildings, pois = osm_features(all_nodes, all_ways)
                self.after(0, lambda: self.info_var.set(f"OSM: {len(buildings)} зданий. Считаю границу..."))

                bbox = detect_bbox_vectorized(self.center_lat, self.center_lon,
                                                buildings, probe, P)
                self.after(0, lambda: self._set_polygon_from_bbox(bbox))
            except Exception as e:
                self.after(0, lambda: self._on_load_error(f"Авто-граница: {e}"))

        threading.Thread(target=worker, daemon=True).start()

    def _set_polygon_from_bbox(self, bbox):
        if self.geo is None:
            return
        self._clear_points()
        lon_min, lat_min, lon_max, lat_max = bbox
        corners = [
            (lat_max, lon_min),
            (lat_max, lon_max),
            (lat_min, lon_max),
            (lat_min, lon_min),
        ]
        for la, lo in corners:
            x, y = geo_to_px(la, lo, self.geo['west'], self.geo['north'], self.geo['mpp'])
            self._add_point(x, y)
        self._close_polygon()
        self.info_var.set(f"Авто-граница: 4 точки (bbox по застройке). OK для подтверждения.")

    def _ok(self):
        if not self.poly_points:
            messagebox.showwarning("Нет точек", "Добавьте точки ЛКМ.")
            return
        if self.geo is None:
            messagebox.showerror("Нет карты", "Карта не загружена.")
            return

        geo_points = []
        for x, y in self.poly_points:
            la, lo = px_to_geo(x, y, self.center_lat, self.geo['west'],
                                self.geo['north'], self.geo['mpp'])
            geo_points.append((la, lo))

        lats = [p[0] for p in geo_points]
        lons = [p[1] for p in geo_points]
        self.result = (min(lons), min(lats), max(lons), max(lats))
        self.polygon_result = geo_points
        self.destroy()

    def _cancel(self):
        self.result = None
        self.polygon_result = None
        self.destroy()


def open_map_picker(parent, lat: float, lon: float, radius: float = 2500.0,
                     cache_dir: str = '.') -> Tuple[Optional[tuple], Optional[list]]:
    """Открыть диалог выбора границы.

    Возвращает (bbox, polygon) где:
      bbox = (lon_min, lat_min, lon_max, lat_max) или None
      polygon = [(lat, lon), ...] или None
    """
    dialog = MapPickerDialog(parent, lat=lat, lon=lon, radius=radius, cache_dir=cache_dir)
    parent.wait_window(dialog)
    return dialog.result, dialog.polygon_result
