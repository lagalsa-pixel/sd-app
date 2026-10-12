"""
gpon_planner.py — оптимизированная для CPU версия алгоритма
проектирования FTTH (GPON) для сельских населённых пунктов.

Источник алгоритма: репозиторий github.com/lagalsa-pixel/gpon-ftth-planner
(см. ALGORITHM_FTTH.md v1.1). Реализация переписана с упором на:
  - работу на CPU (без GPU, без VLM, без скачивания спутниковых тайлов);
  - векторизацию через NumPy/SciPy;
  - sparse CSR-матрицы для дорожного графа;
  - KDTree для O(log n) поиска ближайших узлов;
  - один вызов Dijkstra (вместо K вызовов) — для всех терминалов сразу;
  - кэширование построения дорожного графа.

Алгоритм:
  1. fetch     — загрузка OSM-данных (дороги + здания) или ввод вручную
  2. households — детекция домохозяйств (OSM-здания + кластеризация)
  3. anchor    — выбор здания-якоря ЦУ (скоринг)
  4. network   — древовидная сеть: дорожный граф → MST → SPT → муфты → дропы
  5. boq       — расчёт материалов: схема A (централизованная) и схема D
                 (зонные ОРШ, жадное разбиение) + оптический бюджет

Запуск как модуля:
    from gpon_planner import GPONPlanner
    planner = GPONPlanner()
    planner.run(lat=50.1234, lon=82.5678, expected_hh=350)

Ключевые отличия от оригинала:
  - нет CV-детекции крыш (требует OpenCV + спутниковые снимки);
  - нет VLM-верификации (требует внешнего API);
  - нет SIFT-привязки пользовательского кадра;
  - упрощённая детекция домохозяйств (только OSM-здания);
  - все вычисления векторизованы (numpy/scipy).
Это снижает точность для сёл с дефицитом OSM-разметки, но даёт
сеть и BoQ, приемлемые для предварительной оценки и обучения.
"""

from __future__ import annotations

import logging
import math
import os
import time
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict, deque
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)


# Константы алгоритма (см. ALGORITHM_FTTH.md приложение)
M_PER_DEG_LAT = 111320.0
OSM_API = 'https://api.openstreetmap.org/api/0.6/map'
HDRS = {'User-Agent': 'FTTH-planner-cpu-optimized/1.0 (rural network design study)'}

ROAD_KEYS = {'motorway', 'trunk', 'primary', 'secondary', 'tertiary',
             'unclassified', 'residential', 'living_street', 'service',
             'track', 'road', 'pedestrian'}
MAIN_ROADS = {'primary', 'secondary', 'tertiary', 'trunk',
              'unclassified', 'residential'}
BAD_BUILDINGS = ('garage', 'garages', 'barn', 'shed', 'greenhouse',
                 'roof', 'kiosk', 'hut')


# ----------------------------------------------------------------------------
# Параметры модели (значения по умолчанию, можно переопределить)
# ----------------------------------------------------------------------------
DEFAULT_PARAMS: Dict[str, Any] = dict(
    # --- детекция границы села ---
    boundary_grid_m=50.0,
    boundary_margin_m=300.0,
    boundary_close_m=100.0,
    boundary_min_bld=15,
    boundary_keep_frac=0.65,
    probe_margin_m=900.0,
    # --- домохозяйства ---
    hh_eps_m=16.0,             # кластеризация усадеб
    hh_road_max_m=60.0,        # двор не дальше от дороги
    hh_main_area_m2=36.0,      # мин. размер главного здания
    # --- сеть ---
    net_densify_m=4.0,         # шаг денсификации дорожного графа
    net_snap_max_m=90.0,       # допуск привязки ДХ к дороге
    net_merge_m=50.0,          # слияние точек в муфту
    net_dedup_m=15.0,          # дедупликация муфт
    net_max_drop_m=120.0,      # предел длины дропа
    net_intermediate_m=100.0,  # шаг промежуточной муфты
    # --- оптический бюджет (§VII ALGORITHM_FTTH.md v1.1) ---
    ob_alpha_db_km=0.35,       # затухание G.652D @1310 нм (upstream — худший поток)
    ob_splitter_il_db=21.0,    # PLC 1:64
    ob_splice_db=0.10,         # сварка
    ob_connector_db=0.50,     # SC/APC (3 шт: ODF ЦУ, кросс ОРШ, ONT)
    ob_penalty_db=0.20,       # изгибы/наплывы
    ob_budget_db=28.0,        # Class B+
    ob_budget_db_c=32.0,      # Class C+ (для дальних зон)
    ob_margin_min_db=3.0,     # эксплуатационный запас
    # --- BoQ ---
    fiber_reserve=1.25,
    min_fibers=8,
    std_fibers=[8, 12, 16, 24, 32, 48, 64, 72, 96],
    cable_stock=1.10,
    drop_stock=1.05,
    suspend_per_km=30,
    drop_anchors_per_dh=2,
    drop_fix_per_dh=6,
    consum_stock=1.10,
    split_ratio=64,
    orsh_ports_row=[144, 288, 576, 864, 1152],
    orsh_ports_row_zone=[48, 96, 144, 288, 576],
    # --- схема D (зонные ОРШ) ---
    s_min_km=15.0,
    min_zone_dh=48,
)


# ----------------------------------------------------------------------------
# Утилиты
# ----------------------------------------------------------------------------
def lon2tx(lon: float, z: int) -> float:
    return (lon + 180.0) / 360.0 * (1 << z)


def lat2ty(lat: float, z: int) -> float:
    lr = math.radians(lat)
    return (1.0 - math.log(math.tan(lr) + 1.0 / math.cos(lr)) / math.pi) / 2.0 * (1 << z)


def m_per_px(lat: float, z: int) -> float:
    return 156543.03392 * math.cos(math.radians(lat)) / (1 << z)


def geo_to_px(lat: float, lon: float, west: float, north: float, mpp: float) -> Tuple[float, float]:
    """Географические координаты → пиксели мозаики (или локальной СК)."""
    return ((lon - west) * (M_PER_DEG_LAT * math.cos(math.radians(lat))) / mpp,
            (north - lat) * M_PER_DEG_LAT / mpp)


def px_to_geo(x: float, y: float, lat_ref: float, west: float, north: float, mpp: float) -> Tuple[float, float]:
    return (north - y * mpp / M_PER_DEG_LAT,
            west + x * mpp / (M_PER_DEG_LAT * math.cos(math.radians(lat_ref))))


def nkey(p) -> Tuple[float, float]:
    """Ключ узла (округление до 0.01 px)."""
    return (round(p[0], 2), round(p[1], 2))


def ceilr(x: float) -> int:
    """ceil без артефактов плавающей точки."""
    return math.ceil(round(x, 6))


def roundup01(x: float) -> float:
    return math.ceil(x * 10 - 1e-9) / 10


def decompose(F: int, std: List[int]) -> List[int]:
    """Число волокон → список параллельных кабелей стандартной ёмкости."""
    if F <= std[-1]:
        for s in std:
            if s >= F:
                return [s]
    n96, rem = divmod(F, std[-1])
    out = [std[-1]] * n96
    if rem > 0:
        for s in std:
            if s >= rem:
                out.append(s)
                break
    return out


def meters_to_deg(m: float, lat: float) -> Tuple[float, float]:
    return m / M_PER_DEG_LAT, m / (M_PER_DEG_LAT * math.cos(math.radians(lat)))


# ----------------------------------------------------------------------------
# Загрузка OSM
# ----------------------------------------------------------------------------
def fetch_osm_bbox(lon_min: float, lat_min: float, lon_max: float, lat_max: float,
                   cache_path: Optional[str] = None) -> Optional[bytes]:
    """Скачать OSM-данные через api.openstreetmap.org. Кэш на диске."""
    if cache_path and os.path.exists(cache_path) and os.path.getsize(cache_path) > 100:
        with open(cache_path, 'rb') as f:
            return f.read()
    try:
        import requests
    except ImportError as e:
        raise RuntimeError("Библиотека requests не установлена. Запустите install.bat") from e

    for attempt in range(4):
        try:
            r = requests.get(OSM_API, params={
                'bbox': f'{lon_min:.6f},{lat_min:.6f},{lon_max:.6f},{lat_max:.6f}'},
                headers=HDRS, timeout=120)
            if r.status_code == 200:
                if cache_path:
                    with open(cache_path, 'wb') as f:
                        f.write(r.content)
                return r.content
            if r.status_code == 400:
                logger.warning('bbox слишком велик для OSM API')
                return None
            logger.warning('HTTP %d, повтор...', r.status_code)
        except Exception as e:
            logger.warning('%s, повтор...', str(e)[:70])
        time.sleep(3 * (attempt + 1))
    return None


def parse_osm(xml_bytes: bytes) -> Tuple[Dict[int, Tuple[float, float, dict]],
                                         Dict[int, Tuple[List[int], dict]]]:
    """Парсинг XML OSM. -> (nodes, ways)."""
    root = ET.fromstring(xml_bytes)
    nodes, ways = {}, {}
    for n in root.iter('node'):
        tags = {t.get('k'): t.get('v') for t in n.findall('tag')}
        nodes[int(n.get('id'))] = (float(n.get('lat')), float(n.get('lon')), tags)
    for w in root.iter('way'):
        tags = {t.get('k'): t.get('v') for t in w.findall('tag')}
        nds = [int(nd.get('ref')) for nd in w.findall('nd')]
        ways[int(w.get('id'))] = (nds, tags)
    return nodes, ways


def osm_features(nodes: Dict, ways: Dict) -> Tuple[List, List, List]:
    """Извлечение дорог, зданий, POI из OSM."""
    roads, buildings, pois = [], [], []
    for wid, (nds, tags) in ways.items():
        hw = tags.get('highway')
        bld = tags.get('building')
        if hw and hw in ROAD_KEYS and len(nds) >= 2:
            pts = [(nodes[r][0], nodes[r][1]) for r in nds if r in nodes]
            if len(pts) >= 2:
                roads.append(dict(id=wid, pts=pts, hw=hw, name=tags.get('name', '')))
        if bld and bld not in ('no',) and len(nds) >= 4:
            pts = [(nodes[r][0], nodes[r][1]) for r in nds if r in nodes]
            if len(pts) >= 4:
                cx = sum(p[0] for p in pts) / len(pts)
                cy = sum(p[1] for p in pts) / len(pts)
                # Площадь по формуле шнурков (плоская аппроксимация)
                a = 0.0
                mx = M_PER_DEG_LAT * math.cos(math.radians(cy))
                for k in range(len(pts)):
                    la1, lo1 = pts[k]
                    la2, lo2 = pts[(k + 1) % len(pts)]
                    x1, y1 = lo1 * mx, la1 * M_PER_DEG_LAT
                    x2, y2 = lo2 * mx, la2 * M_PER_DEG_LAT
                    a += x1 * y2 - x2 * y1
                buildings.append(dict(
                    id=wid, poly=pts, center=[cx, cy], area=round(abs(a) / 2, 1),
                    tags={k: tags[k] for k in ('building', 'name', 'amenity', 'shop',
                                               'office', 'height', 'building:levels')
                          if k in tags}))
        if (tags.get('amenity') or tags.get('office') or tags.get('shop')) and not bld:
            if nds and nds[0] in nodes:
                la, lo, _ = nodes[nds[0]]
                pois.append(dict(id=wid, lat=la, lon=lo,
                                 tags={k: tags[k] for k in ('amenity', 'office', 'shop', 'name')
                                       if k in tags}))
    return roads, buildings, pois


# ----------------------------------------------------------------------------
# ОПТИМИЗАЦИЯ: векторизованная детекция границы села
# ----------------------------------------------------------------------------
def detect_bbox_vectorized(v_lat: float, v_lon: float, buildings: List[dict],
                            probe: Tuple[float, float, float, float],
                            P: Callable) -> List[float]:
    """
    Векторизованная версия detect_bbox из оригинала.
    Использует numpy для операций с сеткой и scipy.ndimage для дилатации.

    Возвращает bbox [lon_min, lat_min, lon_max, lat_max].
    """
    if not buildings:
        return list(probe)

    grid_m = P('boundary_grid_m')
    margin = P('boundary_margin_m')
    close_cells = max(1, int(round(P('boundary_close_m') / grid_m)))
    min_bld = P('boundary_min_bld')
    keep_frac = P('boundary_keep_frac')

    # Векторизованный сбор центров зданий
    centers = np.array([b['center'] for b in buildings], dtype=np.float64)
    lats = centers[:, 0]
    lons = centers[:, 1]
    lat_min_arr, lon_min_arr = lats.min(), lons.min()
    dlat = grid_m / M_PER_DEG_LAT
    dlon = grid_m / (M_PER_DEG_LAT * math.cos(math.radians(v_lat)))

    # Индексы ячеек для каждого здания (векторизованно)
    row = np.floor((lats - lat_min_arr) / dlat).astype(np.int32)
    col = np.floor((lons - lon_min_arr) / dlon).astype(np.int32)
    # Гистограмма — сколько зданий в каждой ячейке
    rc_max = row.max() + 1 if len(row) else 1
    cc_max = col.max() + 1 if len(col) else 1
    grid = np.zeros((rc_max, cc_max), dtype=np.int32)
    np.add.at(grid, (row, col), 1)

    # Дилатация через scipy.ndimage (быстро, в C)
    try:
        from scipy import ndimage
        r = close_cells
        # Круглое ядро
        yy, xx = np.ogrid[-r:r + 1, -r:r + 1]
        kernel = (xx * xx + yy * yy <= r * r + 1).astype(np.uint8)
        dil = ndimage.binary_dilation(grid > 0, structure=kernel)
    except ImportError:
        # Fallback на чистый numpy (медленнее)
        dil = grid > 0
        for _ in range(close_cells):
            dil = dil | np.roll(dil, 1, 0) | np.roll(dil, -1, 0) | \
                  np.roll(dil, 1, 1) | np.roll(dil, -1, 1)

    # Связные компоненты
    try:
        from scipy import ndimage
        labeled, n_comp = ndimage.label(dil)
    except ImportError:
        n_comp = 0
        labeled = dil.astype(np.int32)

    if n_comp == 0:
        return list(probe)

    # Для каждой компоненты — подсчёт зданий и центроид
    # Векторизованно через ndimage.sum и ndimage.center_of_mass
    sizes = np.bincount(labeled.ravel())
    sizes[0] = 0  # фон не считаем

    # Центроиды компонент (в индексах ячеек)
    coms = ndimage.center_of_mass(np.ones_like(labeled), labeled,
                                   range(1, n_comp + 1)) if n_comp > 0 else []

    # Фильтр: >= min_bld зданий И центроид в пределах keep_frac зонда
    probe_half_w = (probe[2] - probe[0]) * M_PER_DEG_LAT * math.cos(math.radians(v_lat)) / 2
    probe_half_h = (probe[3] - probe[1]) * M_PER_DEG_LAT / 2
    r_max = keep_frac * math.hypot(probe_half_w, probe_half_h)

    kept_cells = []
    for i, com in enumerate(coms, start=1):
        if sizes[i] < min_bld:
            continue
        # Центроид в географических координатах
        cla = lat_min_arr + (com[0] + 0.5) * dlat
        clo = lon_min_arr + (com[1] + 0.5) * dlon
        distm = math.hypot((cla - v_lat) * M_PER_DEG_LAT,
                           (clo - v_lon) * M_PER_DEG_LAT * math.cos(math.radians(v_lat)))
        if distm <= r_max:
            kept_cells.append((i, com))

    if not kept_cells:
        # Берём самую большую компоненту
        biggest = sizes.argmax()
        com = coms[biggest - 1]
        kept_cells = [(biggest, com)]

    # Собираем маску из оставшихся компонент
    mask = np.zeros_like(labeled, dtype=bool)
    for i, _ in kept_cells:
        mask |= (labeled == i)

    # Границы маски в индексах
    rows = np.where(mask.any(axis=1))[0]
    cols = np.where(mask.any(axis=0))[0]
    if len(rows) == 0 or len(cols) == 0:
        return list(probe)

    rmin, rmax = int(rows[0]), int(rows[-1])
    cmin, cmax = int(cols[0]), int(cols[-1])

    lat_min = lat_min_arr + rmin * dlat - margin / M_PER_DEG_LAT
    lat_max = lat_min_arr + (rmax + 1) * dlat + margin / M_PER_DEG_LAT
    lon_min = lon_min_arr + cmin * dlon - margin / (M_PER_DEG_LAT * math.cos(math.radians(v_lat)))
    lon_max = lon_min_arr + (cmax + 1) * dlon + margin / (M_PER_DEG_LAT * math.cos(math.radians(v_lat)))

    # Клип по зонду
    return [max(lon_min, probe[0]), max(lat_min, probe[1]),
            min(lon_max, probe[2]), min(lat_max, probe[3])]


# ----------------------------------------------------------------------------
# ОПТИМИЗАЦИЯ: дорожный граф с KDTree и CSR-матрицей
# ----------------------------------------------------------------------------
class RoadGraphBuilder:
    """Строит дорожный граф: узлы + рёбра + KDTree + CSR-матрица расстояний.

    Оптимизация для CPU:
      - узлы в numpy-массиве (np.float32 для экономии памяти);
      - KDTree через scipy.spatial.cKDTree — O(log n) поиск ближайших;
      - CSR-матрица через scipy.sparse — быстрая Dijkstra.
    """

    def __init__(self, mpp: float, densify_m: float):
        self.mpp = mpp
        self.densify_m = densify_m
        self.pts: List[Tuple[float, float]] = []
        self.idx: Dict[Tuple[float, float], int] = {}
        self.edges: List[Tuple[int, int, float]] = []

    def add_node(self, x: float, y: float) -> int:
        k = (round(x, 1), round(y, 1))
        i = self.idx.get(k)
        if i is None:
            i = len(self.pts)
            self.pts.append((x, y))
            self.idx[k] = i
        return i

    def add_polyline(self, pts_px: List[Tuple[float, float]]) -> None:
        prev = None
        for p in pts_px:
            cur = self.add_node(p[0], p[1])
            if prev is not None and cur != prev:
                d = math.hypot(self.pts[cur][0] - self.pts[prev][0],
                               self.pts[cur][1] - self.pts[prev][1]) * self.mpp
                if d > 0.05:
                    self.edges.append((prev, cur, d))
            prev = cur

    def build(self, roads: List[dict], west: float, north: float,
              bounds: Optional[Tuple[float, float, float, float]] = None
              ) -> Dict[str, Any]:
        """Построить граф: densify дорог → узлы → CSR + KDTree."""
        from scipy.spatial import cKDTree
        from scipy.sparse import csr_matrix

        mstep = self.densify_m
        for r in roads:
            pts = [geo_to_px(la, lo, west, north, self.mpp) for la, lo in r['pts']]
            # Денсификация: вставить промежуточные точки с шагом mstep
            dens = [pts[0]] if pts else []
            for p in pts[1:]:
                q = dens[-1]
                seg = math.hypot(p[0] - q[0], p[1] - q[1]) * self.mpp
                n_sub = int(seg / mstep)
                for k in range(1, n_sub + 1):
                    dens.append((q[0] + (p[0] - q[0]) * k / max(n_sub, 1),
                                 q[1] + (p[1] - q[1]) * k / max(n_sub, 1)))
                if seg < mstep:
                    dens.append(p)
            self.add_polyline(dens)

        # Узлы в numpy-массиве (float32 для экономии памяти на больших графах)
        Pt = np.array(self.pts, dtype=np.float32) if self.pts else np.zeros((0, 2), dtype=np.float32)
        n = len(Pt)

        # Маска bounds
        allowed = np.ones(n, dtype=bool)
        if bounds is not None and n > 0:
            bx0, by0, bx1, by1 = bounds
            allowed = ((Pt[:, 0] >= bx0) & (Pt[:, 0] <= bx1) &
                       (Pt[:, 1] >= by0) & (Pt[:, 1] <= by1))

        # CSR-матрица расстояний
        rows, cols, vals = [], [], []
        for (u, w, l) in self.edges:
            if not (allowed[u] and allowed[w]):
                continue
            rows += [u, w]
            cols += [w, u]
            vals += [l, l]
        C = csr_matrix((vals, (rows, cols)), shape=(n, n), dtype=np.float32)

        return dict(P=Pt, kdt=cKDTree(Pt) if n > 0 else None,
                    C=C, allowed=allowed)


# ----------------------------------------------------------------------------
# ОПТИМИЗАЦИЯ: ядро построения сети — один вызов Dijkstra для всех терминалов
# ----------------------------------------------------------------------------
def build_network_optimized(osm_data: dict, hhs: List[dict], anchor: dict,
                             geo: dict, P: Callable,
                             bounds: Optional[Tuple] = None,
                             progress_cb: Optional[Callable[[str, float], None]] = None
                             ) -> dict:
    """
    Оптимизированная версия build_network.

    Ключевые оптимизации:
      - один вызов scipy.sparse.csgraph.dijkstra для всех терминалов сразу
        (в оригинале — K вызовов по отдельности, что в K раз медленнее);
      - numpy-массивы для расстояний вместо Python-списков;
      - float32 для экономии памяти;
      - множества и словари для O(1) операций.

    Возвращает dict в формате network.json (как в оригинале).
    """
    from scipy.sparse import csr_matrix
    from scipy.sparse.csgraph import dijkstra

    mpp = geo['mpp']
    west, north = geo['west'], geo['north']
    W, H = geo.get('W', 0), geo.get('H', 0)
    MERGE_M, DEDUP_M = P('net_merge_m'), P('net_dedup_m')
    MAX_DROP_M = P('net_max_drop_m')
    INTERM = P('net_intermediate_m')

    ax, ay = anchor['x'], anchor['y']

    def _notify(msg: str, frac: float = 0.0):
        if progress_cb:
            try:
                progress_cb(msg, frac)
            except Exception:
                pass

    _notify("Строю дорожный граф...", 0.05)
    builder = RoadGraphBuilder(mpp, P('net_densify_m'))
    graph = builder.build(osm_data['roads'], west, north, bounds)
    Pt, tree, C, allowed = graph['P'], graph['kdt'], graph['C'], graph['allowed']
    n = len(Pt)
    logger.info("Граф: %d узлов, %d рёбер", n, C.nnz // 2)

    _notify(f"Граф готов: {n} узлов. Привязка ДХ...", 0.15)
    # Привязка ЦУ
    root_id = int(tree.query([ax, ay])[1]) if tree is not None else 0
    # Привязка ДХ
    hh_nodes: List[Optional[int]] = []
    snap_d: List[float] = []
    if tree is not None:
        for h in hhs:
            d, i = tree.query([h['cx'], h['cy']])
            if d * mpp > P('net_snap_max_m') or not allowed[i]:
                hh_nodes.append(None)
            else:
                hh_nodes.append(int(i))
            snap_d.append(float(d * mpp))
    else:
        hh_nodes = [None] * len(hhs)
        snap_d = [0.0] * len(hhs)

    # Статистика
    snap_arr = np.array(snap_d, dtype=np.float32)
    sd = np.sort(snap_arr[snap_arr > 0])
    if len(sd) > 0:
        logger.info("Привязка ДХ: медиана %.0f м, p90 %.0f м, макс %.0f м;"
                    " вне допуска: %d",
                    float(sd[len(sd) // 2]),
                    float(sd[9 * len(sd) // 10]),
                    float(sd[-1]),
                    sum(x is None for x in hh_nodes))

    ok = [i for i in range(len(hhs)) if hh_nodes[i] is not None]
    terminals = list(dict.fromkeys([root_id] + [hh_nodes[i] for i in ok]))
    tpos = {t: i for i, t in enumerate(terminals)}

    _notify("Считаю кратчайшие пути (Dijkstra)...", 0.25)
    # ОПТИМИЗАЦИЯ: один вызов dijkstra для всех терминалов сразу
    # Это даёт ускорение в K раз по сравнению с отдельными вызовами
    Dm, pred_m = dijkstra(C, indices=terminals, return_predecessors=True,
                          directed=False)

    # MST по метрике кратчайших путей
    K = len(terminals)
    Dt = np.array([[Dm[i][t] for t in terminals] for i in range(K)],
                   dtype=np.float32)
    in_tree = [False] * K
    best_w = [np.inf] * K
    parent = [-1] * K
    best_w[0] = 0.0
    mst_pairs: List[Tuple[int, int]] = []
    for _ in range(K):
        b, bw = -1, np.inf
        for i in range(K):
            if not in_tree[i] and best_w[i] < bw:
                b, bw = i, best_w[i]
        if b < 0:
            break
        in_tree[b] = True
        if parent[b] >= 0:
            mst_pairs.append((terminals[parent[b]], terminals[b]))
        for j in range(K):
            if not in_tree[j] and Dt[b][j] < best_w[j]:
                best_w[j] = float(Dt[b][j])
                parent[j] = b

    _notify("Строю SPT-дерево от корня...", 0.45)
    # Объединение путей MST
    union = set()
    for (u, w) in mst_pairs:
        iu = tpos[u]
        cur = w
        while cur != u and cur >= 0:
            p = int(pred_m[iu][cur])
            if p < 0:
                break
            union.add((min(p, cur), max(p, cur)))
            cur = p

    # CSR-матрица объединённого подграфа
    ur, uc, uv = [], [], []
    elen: Dict[Tuple[int, int], float] = {}
    Pt64 = Pt.astype(np.float64) if Pt.dtype != np.float64 else Pt
    for (u, w) in union:
        l = float(math.hypot(Pt64[u][0] - Pt64[w][0],
                             Pt64[u][1] - Pt64[w][1]) * mpp)
        elen[(u, w)] = l
        ur += [u, w]
        uc += [w, u]
        uv += [l, l]
    U = csr_matrix((uv, (ur, uc)), shape=(n, n), dtype=np.float64)

    _notify("Считаю SPT от корня...", 0.55)
    DU, predU = dijkstra(U, indices=root_id, return_predecessors=True,
                          directed=False)
    predU = predU.astype(np.int64)

    def path_to_root(node: int) -> Optional[List[int]]:
        pth = [node]
        cur = node
        guard = 0
        while cur != root_id:
            p = int(predU[cur])
            if p < 0:
                return None
            pth.append(p)
            cur = p
            guard += 1
            if guard > n:
                return None
        return pth

    hh_paths = {i: path_to_root(hh_nodes[i]) for i in ok}

    # Муфты
    couplers: Dict[int, List[int]] = {}

    def add_coup(node: int, hi: int) -> None:
        couplers.setdefault(node, []).append(hi)

    # Слияние точек подключения в радиусе MERGE_M
    order = sorted([i for i in ok if hh_paths[i] is not None],
                   key=lambda i: float(DU[hh_nodes[i]]))
    for hi in order:
        pth = hh_paths[hi]
        acc = 0.0
        serve = None
        for i in range(len(pth) - 1):
            if pth[i] in couplers:
                serve = pth[i]
                break
            acc += elen.get((min(pth[i], pth[i + 1]),
                             max(pth[i], pth[i + 1])), 0.0)
            if acc > MERGE_M:
                break
        add_coup(serve if serve is not None else pth[0], hi)

    # Все узлы ветвления (степень >= 3) — тоже муфты
    tree_edges = set()
    for hi in order:
        pth = hh_paths[hi]
        for i in range(len(pth) - 1):
            tree_edges.add((min(pth[i], pth[i + 1]),
                            max(pth[i], pth[i + 1])))
    deg: Dict[int, int] = {}
    for (u, w) in tree_edges:
        deg[u] = deg.get(u, 0) + 1
        deg[w] = deg.get(w, 0) + 1
    for node, dg in deg.items():
        if dg >= 3:
            couplers.setdefault(node, [])

    _notify(f"Дедупликация муфт ({len(couplers)} шт.)...", 0.65)
    # Дедупликация муфт ближе DEDUP_M по дереву
    # ОРИГИНАЛ: O(n²) × O(n) path_dist_nodes в цикле while changed → O(n³)
    # ОПТИМИЗАЦИЯ: один проход по дереву — для каждой муфты проверяем
    # только её соседей на пути к корню (в пределах DEDUP_M), а не все пары

    def path_dist_nodes(a: int, b: int) -> float:
        if a == b:
            return 0.0
        pa = path_to_root(a)
        if pa is None or b not in pa:
            return 1e9
        ib = pa.index(b)
        return sum(elen.get((min(pa[i], pa[i + 1]),
                              max(pa[i], pa[i + 1])), 0.0)
                   for i in range(ib))

    # ОПТИМИЗИРОВАННАЯ дедупликация: для каждой муфты ищем только
    # ближайшую муфту на пути к корню (в пределах DEDUP_M), а не
    # сравниваем все пары. Это O(n × path_length) вместо O(n² × path_length)
    changed = True
    _dedup_iter = 0
    while changed and _dedup_iter < 5:  # лимит итераций для безопасности
        changed = False
        _dedup_iter += 1
        # Сортируем по расстоянию от корня (ближайшие к корню — первыми)
        nodes_c = sorted(couplers.keys(), key=lambda c: float(DU[c]))
        for a in nodes_c:
            if a not in couplers:
                continue
            # Идём от муфты 'a' к корню и ищем ближайшую другую муфту
            pth = path_to_root(a)
            if pth is None:
                continue
            acc = 0.0
            for i in range(len(pth) - 1):
                acc += elen.get((min(pth[i], pth[i + 1]),
                                max(pth[i], pth[i + 1])), 0.0)
                if acc > DEDUP_M:
                    break
                # Проверяем, есть ли здесь другая муфта
                candidate = pth[i + 1]
                if candidate in couplers and candidate != a:
                    # Нашли близкую муфту — сливаем
                    couplers[candidate].extend(couplers.pop(a))
                    changed = True
                    break
            if changed:
                break

    _notify(f"Дедупликация завершена: {len(couplers)} муфт (итераций: {_dedup_iter})", 0.70)

    def serving(hi: int) -> int:
        pth = hh_paths[hi]
        for i in range(len(pth)):
            if pth[i] in couplers:
                return pth[i]
        return root_id

    def drop_len(hi: int, sc: int) -> float:
        pth = hh_paths[hi]
        si = pth.index(sc)
        return sum(elen.get((min(pth[i], pth[i + 1]),
                              max(pth[i], pth[i + 1])), 0.0)
                   for i in range(si))

    _notify("Размещение промежуточных муфт...", 0.75)
    # Промежуточные муфты через INTERM при дропе > MAX_DROP_M
    for hi in order:
        pth = hh_paths[hi]
        hh = hhs[hi]
        a_h = pth[0]
        yard = float(math.hypot(hh['cx'] - Pt64[a_h][0],
                                  hh['cy'] - Pt64[a_h][1]) * mpp)
        sc = serving(hi)
        dl = drop_len(hi, sc) + yard
        while dl > MAX_DROP_M:
            acc = 0.0
            placed = None
            for i in range(len(pth) - 1):
                acc += elen.get((min(pth[i], pth[i + 1]),
                                  max(pth[i], pth[i + 1])), 0.0)
                if acc >= INTERM:
                    placed = pth[i + 1]
                    break
            if placed is None or placed in couplers or placed == sc:
                break
            couplers.setdefault(placed, [])
            sc = serving(hi)
            dl2 = drop_len(hi, sc)
            if dl2 >= dl:
                break
            dl = dl2 + yard

    _notify("Считаю дропы...", 0.85)
    # Дропы
    import hashlib
    drops = []
    for hi in range(len(hhs)):
        if hh_nodes[hi] is None or hh_paths.get(hi) is None:
            continue
        pth = hh_paths[hi]
        sc = serving(hi)
        si = pth.index(sc)
        poly = [list(Pt64[pth[i]]) for i in range(si, -1, -1)]
        a_h = pth[0]
        # Дворовая прокладка (как в оригинале)
        hx, hy = hhs[hi]['cx'], hhs[hi]['cy']
        sx, sy = float(Pt64[a_h][0]), float(Pt64[a_h][1])
        dx, dy = hx - sx, hy - sy
        dist = math.hypot(dx, dy)
        if dist < 1e-6:
            entry = [[sx, sy], [hx, hy]]
        else:
            hsh = int(hashlib.md5(str(hhs[hi].get('id', hi)).encode()).hexdigest()[:6], 16) / 0xFFFFFF
            f_along = 0.30 + 0.35 * hsh
            side = 1 if hsh > 0.5 else -1
            ux, uy = dx / dist, dy / dist
            px_, py_ = -uy, ux
            if dist * mpp < 10:
                m1 = [sx + dx * 0.55 + px_ * 2.5 * side, sy + dy * 0.55 + py_ * 2.5 * side]
                entry = [[sx, sy], m1, [hx, hy]]
            else:
                a1 = [sx + px_ * dist * f_along * 0.55 + ux * dist * 0.22,
                      sy + py_ * dist * f_along * 0.55 + uy * dist * 0.22]
                a2 = [a1[0] + ux * dist * 0.5 + px_ * dist * 0.12 * side,
                      a1[1] + uy * dist * 0.5 + py_ * dist * 0.12 * side]
                entry = [[sx, sy], a1, a2, [hx, hy]]
        poly += entry
        dl = (sum(elen.get((min(pth[i], pth[i + 1]),
                             max(pth[i], pth[i + 1])), 0.0)
                  for i in range(si))
              + sum(math.hypot(entry[k + 1][0] - entry[k][0],
                                entry[k + 1][1] - entry[k][1])
                    for k in range(len(entry) - 1)) * mpp)
        drops.append(dict(hh=hi, coupler=sc, poly=poly, length_m=round(dl, 1)))

    # Магистраль = объединение путей муфта → корень
    feeder_edges = set()
    for c in couplers:
        pth = path_to_root(c)
        if pth is None:
            continue
        for i in range(len(pth) - 1):
            feeder_edges.add((min(pth[i], pth[i + 1]),
                              max(pth[i], pth[i + 1])))
    feeder_m = sum(elen.get(e, float(math.hypot(Pt64[e[0]][0] - Pt64[e[1]][0],
                                                  Pt64[e[0]][1] - Pt64[e[1]][1]) * mpp))
                   for e in feeder_edges)
    drop_m = sum(dd['length_m'] for dd in drops)
    coup_list = sorted([c for c in couplers if c != root_id])
    stats = dict(households=len(hhs), served=len(drops),
                 couplers=len(coup_list),
                 feeder_km=round(feeder_m / 1000, 2),
                 drop_km=round(drop_m / 1000, 2),
                 avg_drop_m=round(drop_m / max(1, len(drops)), 1),
                 max_drop_m=round(max((dd['length_m'] for dd in drops), default=0), 1))

    _notify("Сеть построена.", 1.0)
    logger.info("ОРШ id=%s; обслужено %d/%d ДХ; муфт %d; магистраль %.2f км; "
                "дропы %.2f км, ср %.0f м, макс %.0f м",
                anchor.get('id'), stats['served'], len(hhs),
                stats['couplers'], stats['feeder_km'],
                stats['drop_km'], stats['avg_drop_m'], stats['max_drop_m'])

    return dict(anchor=dict(x=ax, y=ay, lat=anchor['lat'], lon=anchor['lon'],
                             bld_id=anchor.get('id'), area=anchor.get('area')),
                root_node=root_id,
                couplers=[dict(node=int(c), x=float(Pt64[c][0]), y=float(Pt64[c][1]),
                                label=f'М{i + 1}') for i, c in enumerate(coup_list)],
                feeder_edges=[[[float(Pt64[e[0]][0]), float(Pt64[e[0]][1])],
                                [float(Pt64[e[1]][0]), float(Pt64[e[1]][1])]]
                               for e in sorted(feeder_edges)],
                drops=drops, stats=stats)


# ----------------------------------------------------------------------------
# ОПТИМИЗАЦИЯ: выбор ЦУ через скоринг (векторизованный)
# ----------------------------------------------------------------------------
def anchor_candidates_vectorized(v_lat: float, v_lon: float,
                                  buildings: List[dict], roads: List[dict],
                                  hhs: List[dict], bbox: List[float],
                                  west: float, north: float, mpp: float,
                                  P: Callable) -> List[dict]:
    """
    Векторизованная версия anchor_candidates.
    Скоринг: размер × центричность × близость к дороге × компактность × бонус.

    Возвращает топ-N кандидатов (отсортированы по убыванию score).
    """
    if not hhs:
        clat, clon = v_lat, v_lon
    else:
        clat = sum(h['lat'] for h in hhs) / len(hhs)
        clon = sum(h['lon'] for h in hhs) / len(hhs)

    # Сбор точек главных дорог для расчёта road_dist
    main_pts = []
    for r in roads:
        if r['hw'] in MAIN_ROADS:
            prev = None
            for la, lo in r['pts']:
                p = geo_to_px(la, lo, west, north, mpp)
                if prev is not None:
                    seg = math.hypot(p[0] - prev[0], p[1] - prev[1]) * mpp
                    n_sub = int(seg / 10.0)
                    for k in range(n_sub + 1):
                        main_pts.append((prev[0] + (p[0] - prev[0]) * k / max(n_sub, 1),
                                         prev[1] + (p[1] - prev[1]) * k / max(n_sub, 1)))
                prev = p
    main_arr = np.array(main_pts) if main_pts else np.zeros((0, 2), dtype=np.float32)

    cands = []
    for b in buildings:
        la, lo = b['center']
        if not (bbox[0] <= lo <= bbox[2] and bbox[1] <= la <= bbox[3]):
            continue
        tags = b.get('tags', {})
        bt = tags.get('building', 'yes')
        if bt in BAD_BUILDINGS:
            continue
        x, y = geo_to_px(la, lo, west, north, mpp)
        pts = [geo_to_px(pla, plo, west, north, mpp) for pla, plo in b['poly']]
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        bw, bh = max(xs) - min(xs), max(ys) - min(ys)
        asp = max(bw, bh) / max(1e-6, min(bw, bh))
        compact = 1.0 if asp < 2.2 else (2.2 / asp)
        dcent_m = math.hypot((la - clat) * M_PER_DEG_LAT,
                              (lo - clon) * M_PER_DEG_LAT * math.cos(math.radians(la)))
        centr = max(0.0, 1.0 - dcent_m / 1200.0)
        area = b['area']
        sz = (1.0 if 150 <= area <= 500 else
              (0.75 if 90 <= area < 150 else
               (0.5 if 500 < area <= 900 else 0.25)))
        # road_dist через векторизованный расчёт
        if len(main_arr) > 0:
            d2 = ((main_arr[:, 0] - x) ** 2 + (main_arr[:, 1] - y) ** 2).min()
            rd = math.sqrt(float(d2)) * mpp
        else:
            rd = 999.0
        road_s = (1.0 if rd < 25 else
                  (0.6 if rd < 50 else
                   (0.3 if rd < 90 else 0.1)))
        tag_bonus = (1.4 if (tags.get('amenity') or
                              bt in ('public', 'civic', 'commercial', 'retail', 'office'))
                     else 1.0)
        cands.append(dict(id=b['id'], lat=la, lon=lo, x=x, y=y,
                           area=area, asp=round(asp, 1), rd=round(rd, 1),
                           dcent=round(dcent_m), bw=bw, bh=bh,
                           score=round(sz * centr * road_s * compact * tag_bonus, 4),
                           tags=tags))
    cands.sort(key=lambda c: -c['score'])
    return cands


# ----------------------------------------------------------------------------
# Детекция домохозяйств (упрощённая: только OSM-здания)
# ----------------------------------------------------------------------------
def detect_households(buildings: List[dict], roads: List[dict],
                       v: dict, bbox: List[float],
                       west: float, north: float, mpp: float,
                       W: int, H: int, P: Callable) -> List[dict]:
    """
    Упрощённая (CPU-only, без CV) детекция домохозяйств.

    Этапы (как в оригинале, но без CV-супплемента):
      1. Фильтр OSM-зданий по зоне
      2. Кластеризация усадеб (eps=16 м)
      3. Фильтр ДХ: главное здание ≥36 м², центр двора ≤60 м от дороги
    """
    # Сбор дорог как точек для расчёта near_road
    from scipy.spatial import cKDTree
    road_pts = []
    for r in roads:
        poly = [geo_to_px(la, lo, west, north, mpp) for la, lo in r['pts']]
        if len(poly) < 2:
            continue
        prev = poly[0]
        road_pts.append(prev)
        for p in poly[1:]:
            seg = math.hypot(p[0] - prev[0], p[1] - prev[1]) * mpp
            n_sub = int(seg / 15.0)
            for k in range(1, n_sub + 1):
                road_pts.append((prev[0] + (p[0] - prev[0]) * k / max(n_sub, 1),
                                  prev[1] + (p[1] - prev[1]) * k / max(n_sub, 1)))
            if seg < 15.0:
                road_pts.append(p)
            prev = p
    road_arr = np.array([(x, y) for x, y in road_pts
                          if -100 <= x <= W + 100 and -100 <= y <= H + 100],
                         dtype=np.float32) if road_pts else np.zeros((0, 2), dtype=np.float32)
    road_tree = cKDTree(road_arr) if len(road_arr) > 0 else None

    def near_road(x: float, y: float, max_m: float) -> bool:
        if road_tree is None:
            return False
        d, _ = road_tree.query([x, y])
        return float(d) * mpp < max_m

    # OSM-здания в зоне
    osm_blds = []
    for b in buildings:
        la, lo = b['center']
        # Проверка зоны
        zone = v.get('zone') or {'type': 'bbox'}
        if zone.get('type') == 'radius':
            r = zone.get('r', 900.0)
            if math.hypot((la - v['lat']) * M_PER_DEG_LAT,
                           (lo - v['lon']) * M_PER_DEG_LAT * math.cos(math.radians(v['lat']))) > r:
                continue
        else:
            if not (bbox[0] <= lo <= bbox[2] and bbox[1] <= la <= bbox[3]):
                continue
        pts = [geo_to_px(pla, plo, west, north, mpp) for pla, plo in b['poly']]
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        if min(xs) < -80 or min(ys) < -80 or max(xs) > W + 80 or max(ys) > H + 80:
            continue
        osm_blds.append(dict(cx=(min(xs) + max(xs)) / 2,
                              cy=(min(ys) + max(ys)) / 2,
                              w=max(xs) - min(xs),
                              h=max(ys) - min(ys),
                              src='osm', tags=b.get('tags', {}), poly=pts))

    # Кластеризация усадеб (eps=16 м)
    eps = P('hh_eps_m') / mpp
    used = [False] * len(osm_blds)
    yards: List[List[int]] = []
    order = sorted(range(len(osm_blds)),
                   key=lambda i: -(osm_blds[i]['w'] * osm_blds[i]['h']))
    for i in order:
        if used[i]:
            continue
        queue, yard = [i], []
        used[i] = True
        while queue:
            j = queue.pop()
            yard.append(j)
            for k in range(len(osm_blds)):
                if used[k]:
                    continue
                if math.hypot(osm_blds[j]['cx'] - osm_blds[k]['cx'],
                               osm_blds[j]['cy'] - osm_blds[k]['cy']) < eps:
                    used[k] = True
                    queue.append(k)
        yards.append(yard)

    # Фильтр ДХ
    households: List[dict] = []
    for yard in yards:
        bl = [osm_blds[j] for j in yard]
        main = max(bl, key=lambda b: b['w'] * b['h'])
        if main['w'] * main['h'] * mpp * mpp < P('hh_main_area_m2'):
            continue
        cx = sum(b['cx'] for b in bl) / len(bl)
        cy = sum(b['cy'] for b in bl) / len(bl)
        if not near_road(cx, cy, P('hh_road_max_m')):
            continue
        la, lo = px_to_geo(main['cx'], main['cy'], v['lat'], west, north, mpp)
        households.append(dict(id=len(households) + 1,
                                cx=main['cx'], cy=main['cy'],
                                lat=round(la, 6), lon=round(lo, 6),
                                n_bld=len(bl),
                                main_w=main['w'], main_h=main['h'],
                                main_src='osm'))
    return households


# ----------------------------------------------------------------------------
# ОПТИМИЗАЦИЯ: расчёт BoQ (схема A и схема D) с оптическим бюджетом
# ----------------------------------------------------------------------------
class TreeOptimized:
    """Дерево магистрали села (BFS-ориентация от ЦУ).

    Оптимизация: индексы в numpy, словари для быстрого доступа.
    Поддержка схемы D (зонные ОРШ) с жадным разбиением и оптическим бюджетом.
    """

    def __init__(self, net: dict, mpp: float, P: Callable):
        self.mpp = mpp
        self.P = P
        self.net = net
        adj: Dict[Tuple, set] = defaultdict(set)
        self.edge_len: Dict[Tuple, float] = {}
        for e in net['feeder_edges']:
            k1, k2 = nkey(e[0]), nkey(e[1])
            if k1 == k2:
                continue
            L = math.hypot(e[1][0] - e[0][0], e[1][1] - e[0][1]) * mpp
            adj[k1].add(k2)
            adj[k2].add(k1)
            self.edge_len[(min(k1, k2), max(k1, k2))] = L
        ax, ay = net['anchor']['x'], net['anchor']['y']
        self.root = min(adj.keys(),
                        key=lambda k: (k[0] - ax) ** 2 + (k[1] - ay) ** 2)

        self.parent: Dict[Tuple, Optional[Tuple]] = {self.root: None}
        self.children: Dict[Tuple, List[Tuple]] = defaultdict(list)
        order = deque([self.root])
        self.bfs: List[Tuple] = [self.root]
        while order:
            u = order.popleft()
            for w in adj[u]:
                if w not in self.parent:
                    self.parent[w] = u
                    self.children[u].append(w)
                    self.bfs.append(w)
                    order.append(w)

        self.edges = []
        for (k1, k2), L in self.edge_len.items():
            if self.parent.get(k1) == k2:
                self.edges.append((k2, k1, L))
            elif self.parent.get(k2) == k1:
                self.edges.append((k1, k2, L))

        ck = {c['node']: nkey([c['x'], c['y']]) for c in net['couplers']}
        self.coupler_nodes = {k for c in net['couplers']
                              if (k := nkey([c['x'], c['y']])) in adj}
        self.homes: Dict[Tuple, int] = defaultdict(int)
        for d in net['drops']:
            k = ck.get(d['coupler'])
            if k is None or k not in adj:
                k = self.root
            self.homes[k] += 1
        self.dh_total = len(net['drops'])
        self.drop_km = sum(d['length_m'] for d in net['drops']) / 1000.0
        self.n_couplers = len(net['couplers'])

        self.dist: Dict[Tuple, float] = {self.root: 0.0}
        for u in self.bfs[1:]:
            p = self.parent[u]
            self.dist[u] = self.dist[p] + self.edge_len[(min(u, p), max(u, p))]

    def flows(self, cuts: List[Tuple]):
        P = self.P
        cutset = set(cuts)
        zr = {self.root: self.root}
        for u in self.bfs:
            if u == self.root:
                continue
            zr[u] = u if u in cutset else zr[self.parent[u]]
        zone_dh = Counter()
        cnt = Counter()
        for node, dh in self.homes.items():
            z = zr[node]
            zone_dh[z] += dh
            cnt[node] += dh
            cnt[z] -= dh
        spl = {z: ceilr(dh / P('split_ratio')) for z, dh in zone_dh.items()}
        ffnode = Counter()
        for z in cutset:
            ffnode[z] += ceilr(P('fiber_reserve') * spl[z])
        df: Dict[Tuple, int] = {}
        ffw: Dict[Tuple, int] = {}
        for u in reversed(self.bfs):
            acc_d = cnt.get(u, 0)
            acc_f = ffnode.get(u, 0)
            for ch in self.children[u]:
                acc_d += df[ch]
                acc_f += ffw[ch]
            df[u] = acc_d
            ffw[u] = acc_f
        return zr, zone_dh, spl, df, ffw

    def _edge_accounting(self, cuts, flows=None):
        """Генератор (L_м, F_волокон, несёт_фидер). Без дорожного графа —
        фидеры по дереву (упрощённый режим, CPU-friendly)."""
        P = self.P
        if flows is None:
            flows = self.flows(cuts)
        _, _, spl, df, ffw_tree = flows
        for _, child, L in self.edges:
            F = max(P('min_fibers'),
                    ceilr(P('fiber_reserve') * df[child]) + ffw_tree[child])
            yield L, F, ffw_tree[child] > 0

    def raw_fkm(self, cuts):
        fl = self.flows(cuts)
        fkm = sum(F * L for L, F, _ in self._edge_accounting(cuts, fl))
        return fkm, fl[3]

    def partition_greedy(self, s_min_km: float):
        """Жадные врезы по максимальной маржинальной экономии волокно-км."""
        cuts = []
        cur_fkm, df = self.raw_fkm(cuts)
        log = []
        while True:
            best_u, best_sav = None, 0.0
            cands = [u for u in self.coupler_nodes
                     if u != self.root and u not in cuts
                     and df.get(u, 0) >= self.P('min_zone_dh')]
            for u in cands:
                fkm_new, _ = self.raw_fkm(cuts + [u])
                sav = (cur_fkm - fkm_new) / 1000.0
                if sav > best_sav:
                    best_u, best_sav = u, sav
            if best_u is None or best_sav < s_min_km:
                break
            cuts.append(best_u)
            log.append(dict(node=str(best_u), sav_km=round(best_sav, 1)))
            cur_fkm, df = self.raw_fkm(cuts)
        return cuts, log

    def layout(self, cuts):
        P = self.P
        fl = self.flows(cuts)
        zr, zone_dh, spl, df, _ = fl
        std = P('std_fibers')
        km_by_size: Dict[int, float] = defaultdict(float)
        fiber_km = cable_km = 0.0
        top_fibers = max_parallel = 0
        feeder_route_km = 0.0
        for L, F, has_ff in self._edge_accounting(cuts, fl):
            top_fibers = max(top_fibers, F)
            cables = decompose(F, std)
            max_parallel = max(max_parallel, len(cables))
            for s in cables:
                km_by_size[s] += L / 1000.0
                cable_km += L / 1000.0
                fiber_km += L / 1000.0 * s
            if has_ff:
                feeder_route_km += L / 1000.0

        routes = []
        for node, dh in self.homes.items():
            r = max(0.0, self.dist[node] - self.dist[zr[node]])
            routes.extend([r] * dh)
        routes.sort()

        zinfo = [dict(zone='ЦУ (корневая)', root_dist_m=0.0,
                       houses=zone_dh.get(self.root, 0),
                       splitters=spl.get(self.root, 0), feeder_fibers=0,
                       orsh_ports=next(p2 for p2 in P('orsh_ports_row')
                                        if p2 >= zone_dh.get(self.root, 0) * 1.1))]
        for z in cuts:
            zinfo.append(dict(zone='зонный ОРШ',
                              root_dist_m=round(self.dist[z], 1),
                              houses=zone_dh[z],
                              splitters=spl[z],
                              feeder_fibers=ceilr(P('fiber_reserve') * spl[z]),
                              orsh_ports=next(p2 for p2 in P('orsh_ports_row_zone')
                                               if p2 >= zone_dh[z] * 1.1)))
        mufty = self.n_couplers - sum(1 for z in cuts if z in self.coupler_nodes)
        dh = self.dh_total
        spl_total = sum(spl.values())

        def pct(sv, q):
            if not sv:
                return 0.0
            i = min(len(sv) - 1, max(0, int(math.ceil(q / 100.0 * len(sv))) - 1))
            return sv[i]

        return dict(cuts=[str(c) for c in cuts], zones=zinfo,
                    n_zones=len(cuts),
                    orsh=1 + len(cuts), fiber_km=round(fiber_km, 1),
                    cable_km_raw=round(cable_km, 3),
                    km_by_size={str(s): round(km_by_size.get(s, 0.0), 3)
                                 for s in std if km_by_size.get(s, 0.0) > 0},
                    top_fibers=top_fibers, max_parallel=max_parallel,
                    feeder_route_km=round(feeder_route_km, 2),
                    splitters=spl_total, olt_ports=spl_total,
                    orsh_ports=sum(z['orsh_ports'] for z in zinfo),
                    mufty=mufty,
                    splices=ceilr((2 * dh + 2 * spl_total) * P('consum_stock')),
                    pigtails=dh + 2 * spl_total,
                    suspend_kits=math.ceil(cable_km * P('suspend_per_km')),
                    routes=dict(avg_m=round(sum(routes) / max(1, len(routes)), 1),
                                 med_m=round(pct(routes, 50), 1),
                                 p90_m=round(pct(routes, 90), 1),
                                 max_m=round(routes[-1], 1) if routes else 0))


def boq_centralized(net: dict, mpp: float, P: Callable) -> dict:
    """Схема A: сплиттеры 1:64 в ОРШ, выделенное волокно каждому ДХ."""
    std = P('std_fibers')
    adj: Dict[Tuple, set] = defaultdict(set)
    edge_len: Dict[Tuple, float] = {}
    for e in net['feeder_edges']:
        k1, k2 = nkey(e[0]), nkey(e[1])
        if k1 == k2:
            continue
        L = math.hypot(e[1][0] - e[0][0], e[1][1] - e[0][1]) * mpp
        adj[k1].add(k2)
        adj[k2].add(k1)
        edge_len[(min(k1, k2), max(k1, k2))] = L
    ax, ay = net['anchor']['x'], net['anchor']['y']
    root = min(adj.keys(), key=lambda k: (k[0] - ax) ** 2 + (k[1] - ay) ** 2)

    ck = {c['node']: nkey([c['x'], c['y']]) for c in net['couplers']}
    homes_direct: Counter = Counter()
    unmapped = 0
    for d in net['drops']:
        k = ck.get(d['coupler'])
        if k is None or k not in adj:
            k = root
            unmapped += 1
        homes_direct[k] += 1

    parent: Dict[Tuple, Optional[Tuple]] = {root: None}
    bfs = [root]
    order = deque([root])
    while order:
        u = order.popleft()
        for w in adj[u]:
            if w not in parent:
                parent[w] = u
                bfs.append(w)
                order.append(w)

    sub = dict(homes_direct)
    for u in reversed(bfs):
        p2 = parent[u]
        if p2 is not None:
            sub[p2] = sub.get(p2, 0) + sub.get(u, 0)

    km_by_size: Dict[int, float] = defaultdict(float)
    fiber_km = total_cable_km = 0.0
    top_fibers = max_parallel = 0
    feeder_m = 0.0
    for (k1, k2), L in edge_len.items():
        feeder_m += L
        if parent.get(k1) == k2:
            child = k1
        elif parent.get(k2) == k1:
            child = k2
        else:
            continue
        F = max(P('min_fibers'), ceilr(P('fiber_reserve') * sub.get(child, 0)))
        top_fibers = max(top_fibers, F)
        cables = decompose(F, std)
        max_parallel = max(max_parallel, len(cables))
        for s in cables:
            km_by_size[s] += L / 1000.0
            total_cable_km += L / 1000.0
            fiber_km += L / 1000.0 * s

    drops = net['drops']
    dh = len(drops)
    drop_km = sum(d['length_m'] for d in drops) / 1000.0
    cable_km = {str(s): round(roundup01(km_by_size.get(s, 0.0) * P('cable_stock')), 1)
                for s in std if km_by_size.get(s, 0.0) > 0}
    m = dict(
        orsh=1,
        splitters=ceilr(dh / P('split_ratio')),
        olt_ports=ceilr(dh / P('split_ratio')),
        pigtails=dh, mufty=len(net['couplers']),
        drop_cable_km=roundup01(drop_km * P('drop_stock')),
        abonent_boxes=dh, fast_conn=ceilr(dh * P('consum_stock')),
        splices=ceilr(2 * dh * P('consum_stock')),
        kdzs=ceilr(2 * dh * P('consum_stock')),
        suspend_kits=math.ceil(total_cable_km * P('suspend_per_km')),
        drop_anchors=P('drop_anchors_per_dh') * dh,
        drop_fix=P('drop_fix_per_dh') * dh,
        orsh_ports=next(p2 for p2 in P('orsh_ports_row') if p2 >= dh * 1.1),
    )
    m.update({f'cable_{s}': cable_km.get(str(s), 0.0) for s in std})
    return dict(
        dhx_served=dh, couplers=len(net['couplers']),
        mufty=len(net['couplers']),
        feeder_km=round(feeder_m / 1000, 2), drop_km=round(drop_km, 2),
        avg_drop_m=round(sum(d['length_m'] for d in drops) / max(1, dh), 1),
        max_drop_m=round(max((d['length_m'] for d in drops), default=0), 1),
        top_fibers=top_fibers, max_parallel=max_parallel,
        total_cable_km=round(total_cable_km, 3), fiber_km=round(fiber_km, 1),
        cable_km=cable_km,
        cable_km_raw=round(total_cable_km * P('cable_stock'), 2),
        drop_cable_km=m['drop_cable_km'],
        orsh_ports=m['orsh_ports'], splitters64=m['splitters'],
        materials=m, checks=dict(unmapped_drop_couplers=unmapped),
    )


def optical_budget(t: TreeOptimized, cuts, zones_layout, max_drop_m: float,
                    P: Callable) -> dict:
    """Энергетический бюджет худшей линии каждой зоны (см. §VII ALGORITHM_FTTH.md)."""
    zr, zone_dh, spl, df, _ = t.flows(cuts)
    zworst: Dict[Tuple, Tuple[float, Tuple]] = {}
    for node in t.homes:
        z = zr[node]
        r = t.dist[node] - t.dist[z]
        if z not in zworst or r > zworst[z][0]:
            zworst[z] = (r, node)
    B, MC = P('ob_budget_db'), P('ob_margin_min_db')
    zones = []
    for zi, z in enumerate([t.root] + list(cuts)):
        dist_m, wnode = zworst.get(z, (0.0, z))
        n_tr = 0
        u = wnode
        while u != z:
            p = t.parent[u]
            if p is None:
                break
            if p != z and p in t.coupler_nodes:
                n_tr += 1
            u = p
        feeder_m = zones_layout[zi]['root_dist_m']
        L_km = (feeder_m + dist_m + max_drop_m) / 1000.0
        A = (P('ob_alpha_db_km') * L_km + P('ob_splitter_il_db')
             + 3 * P('ob_connector_db')
             + (2 + n_tr + 2) * P('ob_splice_db') + P('ob_penalty_db'))
        status = ('ok' if A + MC <= B else
                  'ok-hard' if A <= B else 'EXCEEDED')
        rec = ('' if status == 'ok' else
               'оптика Class C+ или сплиттер 1:32 для дальних PON-портов зоны'
               if status == 'ok-hard' else
               'РЕДИЗАЙН: сдвинуть ОРШ к центру зоны / 1:32')
        zones.append(dict(zone=zones_layout[zi].get('zone', ''),
                          houses=zones_layout[zi]['houses'],
                          feeder_m=round(feeder_m, 1),
                          distrib_m=round(dist_m, 1),
                          drop_max_m=max_drop_m, L_km=round(L_km, 3),
                          transit_splices=n_tr,
                          attenuation_db=round(A, 2),
                          margin_db=round(B - A, 2),
                          status=status, recommendation=rec))
    return dict(model=dict(alpha_db_km=P('ob_alpha_db_km'),
                            splitter_il_db=P('ob_splitter_il_db'),
                            splice_db=P('ob_splice_db'),
                            connector_db=P('ob_connector_db'),
                            penalty_db=P('ob_penalty_db'),
                            budget_class_b=B,
                            budget_class_c=P('ob_budget_db_c'),
                            margin_min_db=MC),
                worst_attenuation_db=max(z['attenuation_db'] for z in zones),
                worst_margin_db=min(z['margin_db'] for z in zones),
                all_ok=all(z['status'] != 'EXCEEDED' for z in zones),
                all_ok_with_margin=all(z['status'] == 'ok' for z in zones),
                zones=zones)


# ----------------------------------------------------------------------------
# Главный класс-планировщик
# ----------------------------------------------------------------------------
@dataclass
class VillageSpec:
    """Спецификация села (входные данные)."""
    key: str
    name: str
    lat: float
    lon: float
    hh: int = 0  # ожидаемое число домохозяйств (для контроля)
    radius_m: float = 2500.0
    zone_type: str = 'bbox'  # 'bbox' | 'radius'
    zone_radius: float = 900.0
    bbox_lock: Optional[List[float]] = None
    force_anchor: Optional[int] = None


class GPONPlanner:
    """Главный класс-планировщик FTTH (GPON) для одного села.

    Оптимизирован для CPU: всё через numpy/scipy, без CV/VLM.
    """

    def __init__(self, params: Optional[Dict[str, Any]] = None,
                 work_dir: str = '.'):
        self.params = dict(DEFAULT_PARAMS)
        if params:
            self.params.update(params)
        self.work_dir = work_dir
        os.makedirs(work_dir, exist_ok=True)
        self._cache_dir = os.path.join(work_dir, 'osm_raw')
        os.makedirs(self._cache_dir, exist_ok=True)

    def P(self, name: str, default=None):
        return self.params.get(name, default)

    def run(self, village: VillageSpec,
            progress_cb: Optional[Callable[[str, float], None]] = None,
            render_map: bool = False,
            download_tiles: bool = True
            ) -> Dict[str, Any]:
        """Полный цикл проектирования села.

        Возвращает dict с ключами:
          - village: спецификация села
          - bbox, geo: геометрия
          - households: список ДХ
          - anchor: выбранное здание ЦУ
          - network: сеть (муфты, дропы, фидер)
          - boq: материалы схем A и D
          - optical_budget: бюджет худших линий зон
        """
        def _notify(msg: str, frac: float = 0.0):
            logger.info("[GPON] %s (%.0f%%)", msg, frac * 100)
            if progress_cb:
                try:
                    progress_cb(msg, frac)
                except Exception:
                    pass

        # === 1. fetch ===
        _notify(f"Загружаю OSM-данные для '{village.name}'...", 0.02)
        if village.bbox_lock:
            probe = tuple(village.bbox_lock)
        else:
            radius = village.radius_m + self.P('probe_margin_m')
            dlat, dlon = meters_to_deg(radius, village.lat)
            probe = (village.lon - dlon, village.lat - dlat,
                     village.lon + dlon, village.lat + dlat)

        # Скачиваем чанками ~2.1 км
        n_split = max(1, int(math.ceil((probe[2] - probe[0]) * M_PER_DEG_LAT
                                         * math.cos(math.radians(village.lat)) / 2100)))
        m_split = max(1, int(math.ceil((probe[3] - probe[1]) * M_PER_DEG_LAT / 2100)))
        all_nodes, all_ways = {}, {}
        for i in range(n_split):
            for j in range(m_split):
                x0 = probe[0] + (probe[2] - probe[0]) * i / n_split
                x1 = probe[0] + (probe[2] - probe[0]) * (i + 1) / n_split
                y0 = probe[1] + (probe[3] - probe[1]) * j / m_split
                y1 = probe[1] + (probe[3] - probe[1]) * (j + 1) / m_split
                cache_path = os.path.join(self._cache_dir,
                                          f"{village.key}_{x0:.4f}_{y0:.4f}_{x1:.4f}_{y1:.4f}.xml")
                _notify(f"OSM чанк {i * m_split + j + 1}/{n_split * m_split}...",
                        0.02 + 0.18 * (i * m_split + j) / max(1, n_split * m_split))
                data = fetch_osm_bbox(x0, y0, x1, y1, cache_path)
                if not data:
                    continue
                n2, w2 = parse_osm(data)
                all_nodes.update(n2)
                all_ways.update(w2)
                time.sleep(0.4)
        roads, buildings, pois = osm_features(all_nodes, all_ways)
        _notify(f"OSM: {len(roads)} дорог, {len(buildings)} зданий, {len(pois)} POI", 0.22)

        # Граница села (векторизованная)
        bbox = (village.bbox_lock or
                detect_bbox_vectorized(village.lat, village.lon, buildings,
                                         probe, self.P))
        w_km = (bbox[2] - bbox[0]) * M_PER_DEG_LAT * math.cos(math.radians(village.lat)) / 1000
        h_km = (bbox[3] - bbox[1]) * M_PER_DEG_LAT / 1000
        _notify(f"Граница села: {w_km:.2f} x {h_km:.2f} км", 0.25)

        # Геопривязка (без скачивания тайлов — мы используем локальную СК)
        # м/пиксель: имитируем z18 (~0.38 м/px на 50° с.ш.)
        mpp = m_per_px(village.lat, 18)
        west = bbox[0]
        north = bbox[3]
        # Размер мозаики в пикселях (для согласованности с оригиналом)
        W = int(round((bbox[2] - bbox[0]) * M_PER_DEG_LAT
                       * math.cos(math.radians(village.lat)) / mpp))
        H = int(round((bbox[3] - bbox[1]) * M_PER_DEG_LAT / mpp))
        geo = dict(west=west, north=north, mpp=mpp, W=W, H=H, zoom=18, bbox=bbox)

        osm_data = dict(bbox=bbox, roads=roads, buildings=buildings,
                         pois=pois, center=[village.lat, village.lon])

        # === 2. households ===
        _notify("Детекция домохозяйств (OSM)...", 0.30)
        households = detect_households(buildings, roads, village.__dict__,
                                        bbox, west, north, mpp, W, H, self.P)
        dev = 100 * (len(households) - village.hh) / max(1, village.hh)
        _notify(f"Найдено {len(households)} ДХ (заказ {village.hh}, "
                f"отклонение {dev:+.1f}%)", 0.40)

        if not households:
            raise RuntimeError(
                f"Не найдено ни одного домохозяйства для '{village.name}'. "
                f"Возможно, в OSM нет разметки зданий в этом районе, "
                f"или координаты села указаны неверно."
            )

        # === 3. anchor ===
        _notify("Выбор здания-якоря (ЦУ)...", 0.45)
        cands = anchor_candidates_vectorized(village.lat, village.lon,
                                              buildings, roads, households,
                                              bbox, west, north, mpp, self.P)
        if not cands:
            raise RuntimeError("Нет кандидатов ЦУ — проверьте зону и OSM-данные")
        if village.force_anchor:
            cands.sort(key=lambda c: (c['id'] != village.force_anchor, -c['score']))
        anchor = cands[0]
        _notify(f"ЦУ: id={anchor['id']} ({anchor['area']:.0f} м², score={anchor['score']})", 0.50)

        # === 4. network ===
        _notify("Построение сети (MST + SPT)...", 0.55)
        net = build_network_optimized(osm_data, households, anchor, geo,
                                       self.P, progress_cb=lambda m, f: _notify(m, 0.55 + 0.30 * f))
        _notify(f"Сеть: муфт {net['stats']['couplers']}, "
                f"магистраль {net['stats']['feeder_km']} км, "
                f"дропы {net['stats']['drop_km']} км", 0.85)

        # === 5. boq ===
        _notify("Расчёт материалов (схемы A и D)...", 0.88)
        a = boq_centralized(net, mpp, self.P)
        t = TreeOptimized(net, mpp, self.P)
        # Контроль: A и D без врезов должны совпадать
        r0 = t.layout([])
        if abs(r0['fiber_km'] - a['fiber_km']) > 0.15:
            logger.warning("Контроль A/D: fiber %s vs %s",
                            r0['fiber_km'], a['fiber_km'])

        cuts, cut_log = t.partition_greedy(self.P('s_min_km'))
        r = t.layout(cuts)

        # Гео-координаты зонных ОРШ
        zones_geo = []
        ax, ay = net['anchor']['x'], net['anchor']['y']
        alat, alon = net['anchor']['lat'], net['anchor']['lon']
        mlat = -mpp / 110574.0
        mlon = mpp / (M_PER_DEG_LAT * math.cos(math.radians(alat)))
        cpos = {nkey([c['x'], c['y']]): (c['x'], c['y']) for c in net['couplers']}
        zlist = [(t.root, None)] + [(c, cpos.get(c)) for c in cuts]
        for z, zxy in zlist:
            zdata = r['zones'][len(zones_geo)]
            if zxy is not None:
                zx, zy = zxy
                zgeo = dict(px=[round(zx, 1), round(zy, 1)],
                            lat=round(alat + (zy - ay) * mlat, 6),
                            lon=round(alon + (zx - ax) * mlon, 6))
            else:
                zgeo = dict(px=None, lat=None, lon=None)
            zones_geo.append({**zdata, **zgeo})

        cable_km = {str(s): roundup01(r['km_by_size'].get(str(s), 0.0) * self.P('cable_stock'))
                    for s in self.P('std_fibers')
                    if r['km_by_size'].get(str(s), 0.0) > 0}
        dh = t.dh_total
        drops_sorted = sorted(d['length_m'] for d in net['drops'])
        m = dict(
            orsh=r['orsh'], orsh_cu=1, orsh_zone=r['n_zones'],
            splitters=r['splitters'], olt_ports=r['splitters'],
            pigtails=r['pigtails'], mufty=r['mufty'],
            drop_cable_km=roundup01(t.drop_km * self.P('drop_stock')),
            abonent_boxes=dh, fast_conn=ceilr(dh * self.P('consum_stock')),
            splices=r['splices'], kdzs=r['splices'],
            suspend_kits=r['suspend_kits'],
            drop_anchors=self.P('drop_anchors_per_dh') * dh,
            drop_fix=self.P('drop_fix_per_dh') * dh,
            orsh_ports=r['orsh_ports'],
        )
        m.update({f'cable_{s}': cable_km.get(str(s), 0.0)
                  for s in self.P('std_fibers')})

        vv = dict(
            num='', key=village.key, name=village.name,
            dhx_excel=village.hh, net='network.json',
            dhx_served=dh, couplers=t.n_couplers, mufty=r['mufty'],
            feeder_km=round(sum(L for _, _, L in t.edges) / 1000.0, 2),
            drop_km=round(t.drop_km, 2),
            avg_drop_m=round(sum(drops_sorted) / max(1, dh), 1),
            max_drop_m=round(drops_sorted[-1], 1) if drops_sorted else 0,
            n_zones=r['n_zones'], orsh=r['orsh'], zones=zones_geo,
            fiber_km=r['fiber_km'], total_cable_km=r['cable_km_raw'],
            cable_km=cable_km,
            cable_km_raw=round(r['cable_km_raw'] * self.P('cable_stock'), 2),
            drop_cable_km=m['drop_cable_km'],
            orsh_ports=r['orsh_ports'], splitters64=r['splitters'],
            top_fibers=r['top_fibers'], max_parallel=r['max_parallel'],
            feeder_route_km=r['feeder_route_km'], routes=r['routes'],
            cut_log=cut_log, materials=m,
            scheme_a=dict(fiber_km=a['fiber_km'],
                           cable_km_raw=a['cable_km_raw'],
                           drop_cable_km=a['drop_cable_km'],
                           mufty=a['mufty'], splitters64=a['splitters64'],
                           splices=a['materials']['splices'],
                           orsh_ports=a['orsh_ports'],
                           total_length_km=round(a['cable_km_raw'] + a['drop_cable_km'], 1)),
            optical_budget=optical_budget(t, cuts, r['zones'],
                                          vv_max_drop := (drops_sorted[-1] if drops_sorted else 0),
                                          self.P),
        )
        vv['total_length_km'] = round(vv['cable_km_raw'] + vv['drop_cable_km'], 1)
        vv['s_min_km'] = float(self.P('s_min_km'))

        _notify(f"BoQ: A вол-км {a['fiber_km']}, "
                f"D вол-км {r['fiber_km']}, "
                f"зон {r['n_zones']}", 0.95)
        ob = vv['optical_budget']
        _notify(f"Бюджет B+: худшая линия {ob['worst_attenuation_db']:.2f} дБ "
                f"(маржа {ob['worst_margin_db']:.2f} дБ)", 0.97)

        # === 6. map (опционально) ===
        map_path = None
        preview_path = None
        if render_map:
            try:
                _notify("Рендеринг карты сети...", 0.98)
                from ftth_renderer import render_network_map
                cache_dir = os.path.join(self.work_dir, 'tiles')
                os.makedirs(cache_dir, exist_ok=True)
                maps_dir = os.path.join(self.work_dir, 'maps')
                os.makedirs(maps_dir, exist_ok=True)

                map_path, preview_path = render_network_map(
                    village=village.__dict__,
                    bbox=bbox,
                    geo=geo,
                    households=households,
                    network=net,
                    boq=vv,
                    optical_budget=ob,
                    work_dir=self.work_dir,
                    output_dir=maps_dir,
                    cache_tiles_dir=cache_dir,
                    progress_cb=lambda m, f: _notify(m, 0.98 + 0.02 * f),
                    download_tiles=download_tiles,
                )
                _notify(f"Карта сохранена: {map_path}", 1.0)
            except Exception as e:
                logger.error("Не удалось отрисовать карту: %s", e)
                _notify(f"Карта не построена: {e}", 1.0)
        else:
            _notify("Готово (без карты)", 1.0)

        return dict(
            village=village.__dict__,
            bbox=bbox, geo=geo,
            households=households, anchor_cands=cands[:5],
            network=net, boq=vv,
            optical_budget=ob,
            map_path=map_path,
            preview_path=preview_path,
        )


# ----------------------------------------------------------------------------
# CLI для тестирования
# ----------------------------------------------------------------------------
if __name__ == '__main__':
    import argparse
    import json

    logging.basicConfig(level=logging.INFO,
                         format='%(asctime)s [%(levelname)s] %(message)s')

    ap = argparse.ArgumentParser(description='GPON FTTH planner (CPU-optimized)')
    ap.add_argument('--lat', type=float, required=True, help='широта центра села')
    ap.add_argument('--lon', type=float, required=True, help='долгота центра села')
    ap.add_argument('--name', default='test', help='имя села (латиницей)')
    ap.add_argument('--hh', type=int, default=100, help='ожидаемое число ДХ')
    ap.add_argument('--radius', type=float, default=2500.0, help='радиус поиска, м')
    ap.add_argument('--work-dir', default='./gpon_work',
                    help='директория для кэша и результатов')
    args = ap.parse_args()

    village = VillageSpec(key=args.name, name=args.name,
                           lat=args.lat, lon=args.lon,
                           hh=args.hh, radius_m=args.radius)
    planner = GPONPlanner(work_dir=args.work_dir)

    def progress(msg, frac):
        print(f"  [{frac * 100:5.1f}%] {msg}")

    result = planner.run(village, progress_cb=progress)
    out_path = os.path.join(args.work_dir, f"{args.name}_result.json")

    # Сериализация
    def _conv(o):
        if isinstance(o, (np.integer,)):
            return int(o)
        if isinstance(o, (np.floating,)):
            return float(o)
        if isinstance(o, np.ndarray):
            return o.tolist()
        if isinstance(o, tuple):
            return list(o)
        if isinstance(o, set):
            return list(o)
        raise TypeError(f'not serializable: {type(o)}')

    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump(result, f, ensure_ascii=False, indent=2, default=_conv)
    print(f"\nГотово! Результат: {out_path}")
    print(f"  ДХ: {len(result['households'])}")
    print(f"  муфт: {result['network']['stats']['couplers']}")
    print(f"  магистраль: {result['network']['stats']['feeder_km']} км")
    print(f"  дропы: {result['network']['stats']['drop_km']} км")
    print(f"  зоны (D): {result['boq']['n_zones']}")
    print(f"  волокно-км A/D: {result['boq']['scheme_a']['fiber_km']} / "
          f"{result['boq']['fiber_km']}")
    print(f"  худшая линия: {result['optical_budget']['worst_attenuation_db']:.2f} дБ "
          f"(маржа {result['optical_budget']['worst_margin_db']:.2f} дБ)")
