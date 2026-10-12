"""
Локальный переводчик промптов с русского на английский.

Использует модель Helsinki-NLP/opus-mt-ru-en (~300 МБ) через библиотеку
transformers. Полностью оффлайн после первой загрузки модели.

Если translate не нужен (пользователь выключил auto_translate), модель
остаётся незагруженной — экономим память.
"""

import logging
from typing import Optional

logger = logging.getLogger(__name__)


class RuEnTranslator:
    """Переводчик русский → английский на базе transformers MarianMT."""

    def __init__(self, model_name: str = "Helsinki-NLP/opus-mt-ru-en",
                 device: str = "cpu"):
        self.model_name = model_name
        self.device = device
        self._tokenizer = None
        self._model = None
        self._loaded = False

    def _ensure_loaded(self) -> None:
        """Лениво загрузить модель при первом использовании."""
        if self._loaded:
            return

        logger.info("Загружаю модель переводчика '%s' на %s ...", self.model_name, self.device)
        try:
            from transformers import AutoTokenizer, AutoModelForSeq2SeqLM
            import torch

            self._tokenizer = AutoTokenizer.from_pretrained(self.model_name)
            self._model = AutoModelForSeq2SeqLM.from_pretrained(self.model_name)

            # Переносим на GPU если есть
            if self.device != "cpu":
                try:
                    self._model = self._model.to(self.device)
                except Exception as e:
                    logger.warning("Не удалось перенести переводчик на %s: %s. "
                                   "Остаётся на CPU.", self.device, e)
                    self.device = "cpu"

            self._loaded = True
            logger.info("Переводчик готов.")
        except Exception as e:
            logger.error("Ошибка загрузки переводчика: %s", e)
            raise RuntimeError(
                "Не удалось загрузить модель переводчика. "
                "Проверьте интернет (для первого скачивания модели) и "
                "что библиотека transformers установлена."
            ) from e

    def translate(self, text: str) -> str:
        """Перевести текст с русского на английский."""
        text = (text or "").strip()
        if not text:
            return ""

        # Если текст уже в основном на английском — не переводим.
        # Простой эвристический тест: много латинских символов
        latin_ratio = self._latin_ratio(text)
        if latin_ratio > 0.7:
            logger.debug("Текст уже выглядит английским (latin=%.2f) — без перевода.", latin_ratio)
            return text

        self._ensure_loaded()

        try:
            import torch
            inputs = self._tokenizer(text, return_tensors="pt", truncation=True,
                                     max_length=512)
            if self.device != "cpu":
                inputs = {k: v.to(self.device) for k, v in inputs.items()}

            with torch.no_grad():
                outputs = self._model.generate(
                    **inputs,
                    max_length=512,
                    num_beams=4,
                    early_stopping=True,
                )
            result = self._tokenizer.decode(outputs[0], skip_special_tokens=True)
            return result.strip()
        except Exception as e:
            logger.error("Ошибка перевода: %s. Возвращаю оригинал.", e)
            return text

    @staticmethod
    def _latin_ratio(text: str) -> float:
        """Доля латинских букв среди всех букв в строке."""
        if not text:
            return 0.0
        letters = [ch for ch in text if ch.isalpha()]
        if not letters:
            return 0.0
        latin = sum(1 for ch in letters if ch.isascii() and ch.isalpha())
        return latin / len(letters)

    def unload(self) -> None:
        """Выгрузить модель из памяти (для освобождения VRAM/RAM)."""
        if not self._loaded:
            return
        self._tokenizer = None
        self._model = None
        self._loaded = False
        # Принудительная сборка мусора
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
        logger.info("Переводчик выгружен из памяти.")


# Глобальный синглтон (создаётся лениво)
_translator: Optional[RuEnTranslator] = None


def get_translator(model_name: str = "Helsinki-NLP/opus-mt-ru-en",
                   device: str = "cpu") -> RuEnTranslator:
    """Получить глобальный экземпляр переводчика."""
    global _translator
    if _translator is None:
        _translator = RuEnTranslator(model_name=model_name, device=device)
    return _translator


def translate_prompt(text: str, model_name: str = "Helsinki-NLP/opus-mt-ru-en",
                     device: str = "cpu") -> str:
    """Удобная функция-обёртка: перевести промпт (или вернуть как есть)."""
    if not text:
        return ""
    return get_translator(model_name=model_name, device=device).translate(text)
