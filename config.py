"""
config.py — Конфигурация приложения SD Image Generator.

Все настройки хранятся в JSON-файле рядом с приложением.
При первом запуске создаётся config.json со значениями по умолчанию.
"""

import json
import os
from pathlib import Path
from typing import Any, Dict

APP_DIR = Path(__file__).parent.resolve()
CONFIG_PATH = APP_DIR / "config.json"
MODELS_DIR = APP_DIR / "models"
OUTPUT_DIR = APP_DIR / "output"

MODELS_DIR.mkdir(exist_ok=True)
OUTPUT_DIR.mkdir(exist_ok=True)

DEFAULT_CONFIG: Dict[str, Any] = {
    "model_id": "runwayml/stable-diffusion-v1-5",
    "device": "auto",
    "torch_dtype": "fp32",
    "enable_cpu_offload": False,
    "attention_slicing": True,
    "vae_slicing": True,
    "enable_xformers": True,
    "default_width": 512,
    "default_height": 512,
    "default_steps": 20,
    "default_guidance_scale": 7.5,
    "default_negative_prompt": (
        "lowres, bad anatomy, bad hands, text, error, missing fingers, "
        "extra digit, fewer digits, cropped, worst quality, low quality, "
        "normal quality, jpeg artifacts, signature, watermark, username, blurry"
    ),
    "default_seed": -1,
    "translator_model": "Helsinki-NLP/opus-mt-ru-en",
    "auto_translate": True,
    "save_history": True,
    "history_limit": 100,
}


def load_config() -> Dict[str, Any]:
    if not CONFIG_PATH.exists():
        save_config(DEFAULT_CONFIG)
        return dict(DEFAULT_CONFIG)
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            cfg = json.load(f)
    except (json.JSONDecodeError, OSError):
        cfg = dict(DEFAULT_CONFIG)
        save_config(cfg)
    changed = False
    for key, value in DEFAULT_CONFIG.items():
        if key not in cfg:
            cfg[key] = value
            changed = True
    if changed:
        save_config(cfg)
    return cfg


def save_config(cfg: Dict[str, Any]) -> None:
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


def detect_device() -> str:
    try:
        import torch
        if torch.cuda.is_available():
            return "cuda"
    except ImportError:
        pass
    return "cpu"


def resolve_device(cfg: Dict[str, Any]) -> str:
    requested = cfg.get("device", "auto")
    if requested == "auto":
        return detect_device()
    return requested
