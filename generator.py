"""
Обёртка над Stable Diffusion через библиотеку diffusers.

Поддерживает:
  - txt2img (генерация из текстового промпта)
  - img2img (модификация загруженного изображения)

Полностью оффлайн после первой загрузки модели (~4 ГБ для SD 1.5).
Модель один раз скачивается с Hugging Face Hub и кэшируется локально.
"""

import logging
import os
import time
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Tuple

from PIL import Image

import config

logger = logging.getLogger(__name__)


class SDGenerator:
    """Stable Diffusion генератор с ленивой загрузкой модели."""

    def __init__(self, cfg: Dict[str, Any]):
        self.cfg = cfg
        self.model_id = cfg.get("model_id", "runwayml/stable-diffusion-v1-5")
        self.device = config.resolve_device(cfg)
        self.dtype_str = cfg.get("torch_dtype", "fp32")
        self._pipe = None
        self._loaded_model_id = None  # чтобы перезагружать при смене модели

    # ---------- загрузка модели ----------

    def _resolve_dtype(self):
        import torch
        # На CPU принудительно fp32 — fp16 на CPU либо не работает, либо медленнее
        if self.device == "cpu":
            return torch.float32
        if self.dtype_str == "fp16":
            return torch.float16
        return torch.float32

    def _ensure_loaded(self, progress_cb: Optional[Callable[[str, float], None]] = None):
        """Лениво загрузить pipeline, если ещё не загружен или сменилась модель."""
        if self._pipe is not None and self._loaded_model_id == self.model_id:
            return

        def _notify(msg: str, frac: float = 0.0):
            if progress_cb:
                try:
                    progress_cb(msg, frac)
                except Exception:
                    pass

        _notify(f"Загружаю модель {self.model_id}...", 0.05)

        # Импортируем diffusers динамически — если не установлен, упадёт понятной ошибкой
        try:
            from diffusers import StableDiffusionPipeline, StableDiffusionImg2ImgPipeline
            import torch
        except ImportError as e:
            raise RuntimeError(
                "Библиотека diffusers не установлена. Запустите install.bat "
                "или выполните: pip install diffusers transformers accelerate"
            ) from e

        # Если грузим новую модель — выгружаем старую
        if self._pipe is not None:
            _notify("Выгружаю старую модель...", 0.1)
            self.unload()

        dtype = self._resolve_dtype()

        _notify("Скачиваю/загружаю веса модели (это может занять несколько минут при первом запуске)...", 0.2)
        try:
            # Загружаем оба pipeline сразу (txt2img и img2img) — они разделяют
            # компоненты (UNet, VAE, tokenizer), поэтому память не удваивается
            self._pipe = StableDiffusionPipeline.from_pretrained(
                self.model_id,
                torch_dtype=dtype,
                safety_checker=None,   # отключаем safety checker для скорости
                requires_safety_checker=False,
            )
        except Exception as e:
            raise RuntimeError(
                f"Не удалось загрузить модель {self.model_id}. "
                f"Проверьте подключение к интернету (первая загрузка ~4 ГБ) "
                f"и что имя модели верное. Ошибка: {e}"
            ) from e

        # Применяем оптимизации
        _notify("Применяем оптимизации памяти...", 0.7)

        if self.cfg.get("enable_cpu_offload", False) and self.device == "cuda":
            try:
                self._pipe.enable_model_cpu_offload()
                logger.info("Включён model_cpu_offload")
            except Exception as e:
                logger.warning("cpu_offload недоступен: %s", e)

        if self.cfg.get("attention_slicing", True):
            try:
                self._pipe.enable_attention_slicing()
                logger.info("Включено attention_slicing")
            except Exception as e:
                logger.warning("attention_slicing недоступен: %s", e)

        if self.cfg.get("vae_slicing", True):
            try:
                self._pipe.enable_vae_slicing()
                logger.info("Включено vae_slicing")
            except Exception as e:
                logger.warning("vae_slicing недоступен: %s", e)

        if self.cfg.get("enable_xformers", True):
            try:
                self._pipe.enable_xformers_memory_efficient_attention()
                logger.info("Включён xformers")
            except Exception as e:
                # xformers может быть не установлен — это нормально
                logger.debug("xformers не активирован: %s", e)

        # Перенос на устройство — только если не используем cpu_offload
        # (он сам управляет переносом)
        if not self.cfg.get("enable_cpu_offload", False):
            try:
                self._pipe = self._pipe.to(self.device)
            except Exception as e:
                logger.warning("Не удалось перенести pipeline на %s: %s. Остаётся на CPU.",
                               self.device, e)
                self.device = "cpu"

        self._loaded_model_id = self.model_id
        _notify("Модель готова к генерации.", 1.0)

    def unload(self) -> None:
        """Выгрузить модель из памяти."""
        if self._pipe is None:
            return
        # Удаляем pipeline и принудительно собираем мусор
        del self._pipe
        self._pipe = None
        self._loaded_model_id = None
        try:
            import gc
            gc.collect()
            try:
                import torch
                if torch.cuda.is_available():
                    torch.cuda.empty_cache()
            except Exception:
                pass
        except Exception:
            pass
        logger.info("Модель SD выгружена.")

    # ---------- генерация ----------

    def txt2img(
        self,
        prompt: str,
        negative_prompt: str = "",
        width: int = 512,
        height: int = 512,
        steps: int = 20,
        guidance_scale: float = 7.5,
        seed: int = -1,
        progress_cb: Optional[Callable[[str, float], None]] = None,
    ) -> Tuple[Image.Image, int]:
        """
        Сгенерировать изображение из текстового промпта.

        Возвращает (PIL.Image, использованный seed).
        """
        import torch

        self._ensure_loaded(progress_cb)

        actual_seed = self._resolve_seed(seed)
        generator = torch.Generator(device=self.device if self.device != "cpu" else "cpu")
        # На CPU генератор должен быть на CPU
        gen_device = "cpu" if self.device == "cpu" else self.device
        generator = torch.Generator(device=gen_device).manual_seed(actual_seed)

        logger.info("txt2img: prompt=%r neg=%r steps=%d cfg=%.2f seed=%d (%dx%d)",
                    prompt, negative_prompt, steps, guidance_scale, actual_seed, width, height)

        def _cb(step: int, timestep: int, latents):
            if progress_cb:
                # Используем step +1 чтобы дойти до 1.0 в конце
                frac = min(1.0, (step + 1) / max(1, steps))
                try:
                    progress_cb(f"Шаг {step + 1}/{steps}", frac)
                except Exception:
                    pass

        t0 = time.time()
        with torch.inference_mode():
            result = self._pipe(
                prompt=prompt,
                negative_prompt=negative_prompt or None,
                width=width,
                height=height,
                num_inference_steps=steps,
                guidance_scale=guidance_scale,
                generator=generator,
                callback=_cb,
                callback_steps=1,
            )
        dt = time.time() - t0
        logger.info("Генерация завершена за %.1f сек (%.2f сек/шаг)", dt, dt / max(1, steps))

        image = result.images[0]
        return image, actual_seed

    def img2img(
        self,
        prompt: str,
        init_image: Image.Image,
        negative_prompt: str = "",
        strength: float = 0.75,
        steps: int = 20,
        guidance_scale: float = 7.5,
        seed: int = -1,
        progress_cb: Optional[Callable[[str, float], None]] = None,
    ) -> Tuple[Image.Image, int]:
        """
        Модифицировать существующее изображение по промпту.

        strength: 0.0 — почти не меняет, 1.0 — полностью перерисовывает.
        Рекомендуемый диапазон 0.3–0.8.
        """
        import torch
        from diffusers import StableDiffusionImg2ImgPipeline

        self._ensure_loaded(progress_cb)

        # Если pipeline — txt2img, нужно создать img2img с теми же компонентами
        # (экономия памяти, не требует повторной загрузки)
        pipe = self._pipe
        if not isinstance(pipe, StableDiffusionImg2ImgPipeline):
            pipe = StableDiffusionImg2ImgPipeline.from_pipe(pipe)
            # from_pipe разделяет компоненты — переносить заново не нужно

        # Приводим изображение к RGB и ресайз (SD 1.5 любит кратное 8)
        init_image = init_image.convert("RGB")
        # Округляем размер до кратного 8
        w, h = init_image.size
        w = (w // 8) * 8
        h = (h // 8) * 8
        if (w, h) != init_image.size:
            init_image = init_image.resize((w, h), Image.LANCZOS)
            logger.info("Изображение приведено к %dx%d", w, h)

        actual_seed = self._resolve_seed(seed)
        gen_device = "cpu" if self.device == "cpu" else self.device
        generator = torch.Generator(device=gen_device).manual_seed(actual_seed)

        logger.info("img2img: prompt=%r strength=%.2f steps=%d seed=%d",
                    prompt, strength, steps, actual_seed)

        def _cb(step: int, timestep: int, latents):
            if progress_cb:
                frac = min(1.0, (step + 1) / max(1, steps))
                try:
                    progress_cb(f"Шаг {step + 1}/{steps}", frac)
                except Exception:
                    pass

        t0 = time.time()
        with torch.inference_mode():
            result = pipe(
                prompt=prompt,
                image=init_image,
                negative_prompt=negative_prompt or None,
                strength=strength,
                num_inference_steps=steps,
                guidance_scale=guidance_scale,
                generator=generator,
                callback=_cb,
                callback_steps=1,
            )
        dt = time.time() - t0
        logger.info("img2img завершён за %.1f сек", dt)

        image = result.images[0]
        return image, actual_seed

    # ---------- утилиты ----------

    @staticmethod
    def _resolve_seed(seed: int) -> int:
        """Если seed=-1 — вернуть случайный."""
        if seed is None or seed < 0:
            import random
            return random.randint(0, 2**31 - 1)
        return int(seed)

    def get_status(self) -> Dict[str, Any]:
        """Вернуть словарь с информацией о текущем состоянии."""
        return {
            "model_id": self.model_id,
            "device": self.device,
            "dtype": self.dtype_str,
            "loaded": self._pipe is not None,
        }


def save_image(image: Image.Image, output_dir: Optional[Path] = None,
               prefix: str = "sd") -> Path:
    """Сохранить PIL.Image в output/ с уникальным именем."""
    out_dir = output_dir or config.OUTPUT_DIR
    out_dir.mkdir(exist_ok=True, parents=True)
    ts = time.strftime("%Y%m%d_%H%M%S")
    path = out_dir / f"{prefix}_{ts}.png"
    # Если уже есть — добавляем счётчик
    counter = 1
    while path.exists():
        path = out_dir / f"{prefix}_{ts}_{counter}.png"
        counter += 1
    image.save(path, format="PNG")
    logger.info("Изображение сохранено: %s", path)
    return path
