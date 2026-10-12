"""
Конфигурация приложения SD Image Generator.

Все настройки хранятся в JSON-файле рядом с приложением.
При первом запуске создаётся config.json со значениями по умолчанию.
"""

import json
import os
from pathlib import Path
from typing import Any, Dict

# Путь к директории приложения
APP_DIR = Path(__file__).parent.resolve()
CONFIG_PATH = APP_DIR / "config.json"
MODELS_DIR = APP_DIR / "models"
OUTPUT_DIR = APP_DIR / "output"

# Создаём директории при импорте, чтобы не падать при первом запуске
MODELS_DIR.mkdir(exist_ok=True)
OUTPUT_DIR.mkdir(exist_ok=True)


# Значения по умолчанию
DEFAULT_CONFIG: Dict[str, Any] = {
    # Модель Stable Diffusion (можно указать любую с HF Hub)
    "model_id": "runwayml/stable-diffusion-v1-5",

    # Альтернативные лёгкие модели для слабых ПК:
    #   "Linaqruf/anything-v3.0"           — аниме-стиль
    #   "SG161222/Realistic_Vision_V5.1_noVAE" — фотореализм
    #   "stabilityai/sd-turbo"             — быстрая модель (1-4 шага)
    #   "CompVis/stable-diffusion-v1-4"    — классика

    # Устройство: "auto" | "cuda" | "cpu"
    # "auto" — автоматически определит наличие GPU
    "device": "auto",

    # Точность: "fp16" (быстрее, нужно GPU) | "fp32" (медленнее, универсально)
    "torch_dtype": "fp32",

    # Оптимизация памяти (включить, если мало ОЗУ или VRAM)
    # "enable_model_cpu_offload" — держит модель на CPU, переносит на GPU по частям
    # "enable_sequential_cpu_offload" — ещё агрессивнее (для очень слабых GPU)
    "enable_cpu_offload": False,
    "attention_slicing": True,   # снижает потребление VRAM
    "vae_slicing": True,         # снижает пиковую VRAM при батчах
    "enable_xformers": True,     # ускорение, если установлен xformers

    # Параметры генерации по умолчанию
    "default_width": 512,
    "default_height": 512,
    "default_steps": 20,         # 20-30 для SD1.5, 1-4 для sd-turbo
    "default_guidance_scale": 7.5,
    "default_negative_prompt": (
        "lowres, bad anatomy, bad hands, text, error, missing fingers, "
        "extra digit, fewer digits, cropped, worst quality, low quality, "
        "normal quality, jpeg artifacts, signature, watermark, username, blurry"
    ),
    "default_seed": -1,          # -1 = случайно каждый раз

    # Переводчик RU -> EN (локальная модель)
    "translator_model": "Helsinki-NLP/opus-mt-ru-en",
    "auto_translate": True,      # автоматически переводить русский промпт

    # История
    "save_history": True,
    "history_limit": 100,
}


def load_config() -> Dict[str, Any]:
    """Загрузить конфигурацию из JSON-файла. Создать с дефолтами, если нет."""
    if not CONFIG_PATH.exists():
        save_config(DEFAULT_CONFIG)
        return dict(DEFAULT_CONFIG)

    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            cfg = json.load(f)
    except (json.JSONDecodeError, OSError):
        # Повреждённый конфиг — пересоздаём
        cfg = dict(DEFAULT_CONFIG)
        save_config(cfg)

    # Дополняем недостающие ключи значениями по умолчанию
    # (на случай если новая версия приложения добавила опции)
    changed = False
    for key, value in DEFAULT_CONFIG.items():
        if key not in cfg:
            cfg[key] = value
            changed = True
    if changed:
        save_config(cfg)

    return cfg


def save_config(cfg: Dict[str, Any]) -> None:
    """Сохранить конфигурацию в JSON-файл."""
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


def detect_device() -> str:
    """Определить лучшее доступное устройство: 'cuda' если есть GPU NVIDIA, иначе 'cpu'."""
    try:
        import torch
        if torch.cuda.is_available():
            return "cuda"
    except ImportError:
        pass
    return "cpu"


def resolve_device(cfg: Dict[str, Any]) -> str:
    """Вернуть реальное устройство на основе конфига."""
    requested = cfg.get("device", "auto")
    if requested == "auto":
        return detect_device()
    return requested
