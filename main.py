"""
Multi-tool Desktop App — настольное приложение с двумя ИИ-инструментами:

1. SD Image Generator — генерация изображений через Stable Diffusion
   (полностью офлайн, после первой загрузки моделей).

2. GPON FTTH Planner — планирование FTTH-сети для сельских населённых
   пунктов на основе алгоритма github.com/lagalsa-pixel/gpon-ftth-planner,
   оптимизированного для CPU, с отрисовкой карты сети.

Запуск:
    python main.py
"""

import json
import logging
import os
import queue
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Any, Dict, Optional

from PIL import Image, ImageTk

import config
from generator import SDGenerator, save_image
from translator import RuEnTranslator

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.FileHandler(config.APP_DIR / "app.log", encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger("app")

PREVIEW_MAX = 600


def resize_for_preview(image, max_size=PREVIEW_MAX):
    w, h = image.size
    if w <= max_size and h <= max_size:
        return image
    ratio = min(max_size / w, max_size / h)
    return image.resize((max(1, int(w * ratio)), max(1, int(h * ratio))), Image.LANCZOS)


# ===== SD Image Generator вкладка =====
class SDTab(ttk.Frame):
    def __init__(self, parent):
        super().__init__(parent)
        self.cfg = config.load_config()
        self.generator = SDGenerator(self.cfg)
        self.translator: Optional[RuEnTranslator] = None
        self.init_image = None
        self.current_image = None
        self.current_photo = None
        self.last_seed_var = tk.StringVar(value="-")
        self.msg_queue: queue.Queue = queue.Queue()
        self._generating = False
        self._worker_thread = None
        self._build_ui()
        self.after(100, self._poll_messages)

    def _build_ui(self):
        main_paned = ttk.PanedWindow(self, orient=tk.HORIZONTAL)
        main_paned.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)
        left = ttk.Frame(main_paned, width=420)
        main_paned.add(left, weight=0)
        self._build_control_panel(left).pack(fill=tk.BOTH, expand=True)
        right = ttk.Frame(main_paned)
        main_paned.add(right, weight=1)
        self._build_preview_panel(right)

    def _build_control_panel(self, parent):
        container = ttk.Frame(parent)
        canvas = tk.Canvas(container, highlightthickness=0, width=400)
        sb = ttk.Scrollbar(container, orient="vertical", command=canvas.yview)
        scrollable = ttk.Frame(canvas)
        scrollable.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=scrollable, anchor="nw")
        canvas.configure(yscrollcommand=sb.set)
        canvas.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

        def _on_wheel(event):
            canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
        canvas.bind_all("<MouseWheel>", _on_wheel)
        self._fill_control_form(scrollable)
        return container

    def _fill_control_form(self, frame):
        pad = {"padx": 8, "pady": 4}
        ttk.Label(frame, text="Параметры генерации", font=("Segoe UI", 12, "bold")).pack(anchor="w", **pad)
        ttk.Separator(frame).pack(fill="x", **pad)
        ttk.Label(frame, text="Модель Hugging Face:").pack(anchor="w", **pad)
        self.model_var = tk.StringVar(value=self.cfg["model_id"])
        ttk.Entry(frame, textvariable=self.model_var).pack(fill="x", **pad)
        ttk.Label(frame, text="Например: runwayml/stable-diffusion-v1-5, stabilityai/sd-turbo",
                  foreground="gray", font=("Segoe UI", 8)).pack(anchor="w", **pad)
        ttk.Label(frame, text="Режим:").pack(anchor="w", **pad)
        self.mode_var = tk.StringVar(value="txt2img")
        mf = ttk.Frame(frame)
        mf.pack(fill="x", **pad)
        ttk.Radiobutton(mf, text="Текст → изображение", variable=self.mode_var, value="txt2img",
                        command=self._on_mode_change).pack(side="left")
        ttk.Radiobutton(mf, text="Изображение → изображение", variable=self.mode_var, value="img2img",
                        command=self._on_mode_change).pack(side="left", padx=8)

        self.init_img_frame = ttk.LabelFrame(frame, text="Исходное изображение (img2img)")
        self.init_img_frame.pack(fill="x", **pad)
        self.init_img_label_var = tk.StringVar(value="Изображение не выбрано")
        ttk.Label(self.init_img_frame, textvariable=self.init_img_label_var,
                  foreground="gray").pack(anchor="w", padx=8, pady=4)
        bf = ttk.Frame(self.init_img_frame)
        bf.pack(fill="x", padx=8, pady=(0, 8))
        ttk.Button(bf, text="Загрузить...", command=self._load_init_image).pack(side="left")
        ttk.Button(bf, text="Очистить", command=self._clear_init_image).pack(side="left", padx=4)
        ttk.Label(self.init_img_frame, text="Strength (0.1–1.0):").pack(anchor="w", padx=8)
        self.strength_var = tk.DoubleVar(value=0.75)
        ttk.Scale(self.init_img_frame, from_=0.1, to=1.0, variable=self.strength_var,
                  command=lambda v: self.strength_var.set(round(float(v), 2))).pack(fill="x", padx=8)
        self.strength_value_label = ttk.Label(self.init_img_frame, text="0.75")
        self.strength_value_label.pack(anchor="e", padx=8)
        self.strength_var.trace_add("write", self._update_strength_label)
        self.init_img_frame.pack_forget()

        ttk.Label(frame, text="Промпт (русский или английский):").pack(anchor="w", **pad)
        self.prompt_text = tk.Text(frame, height=4, wrap="word", font=("Segoe UI", 10))
        self.prompt_text.pack(fill="x", **pad)
        self.prompt_text.insert("1.0", "космонавт верхом на лошади на Марсе, фотореалистично, 4k")
        self.auto_translate_var = tk.BooleanVar(value=self.cfg.get("auto_translate", True))
        ttk.Checkbutton(frame, text="Авто-перевод RU→EN (локальная модель)",
                        variable=self.auto_translate_var).pack(anchor="w", **pad)
        ttk.Label(frame, text="Негативный промпт:").pack(anchor="w", **pad)
        self.neg_prompt_text = tk.Text(frame, height=2, wrap="word", font=("Segoe UI", 9))
        self.neg_prompt_text.pack(fill="x", **pad)
        self.neg_prompt_text.insert("1.0", self.cfg.get("default_negative_prompt", ""))

        sf = ttk.Frame(frame)
        sf.pack(fill="x", **pad)
        ttk.Label(sf, text="Размер:").pack(side="left")
        sizes = ["512x512", "512x768", "768x512", "768x768", "1024x1024"]
        self.size_var = tk.StringVar(value=f"{self.cfg['default_width']}x{self.cfg['default_height']}")
        ttk.Combobox(sf, textvariable=self.size_var, values=sizes, width=10, state="readonly").pack(side="left", padx=8)

        ttk.Label(frame, text="Шаги (20-30 для SD1.5, 1-4 для sd-turbo):").pack(anchor="w", **pad)
        self.steps_var = tk.IntVar(value=self.cfg.get("default_steps", 20))
        ttk.Scale(frame, from_=1, to=100, variable=self.steps_var,
                  command=lambda v: self.steps_var.set(int(float(v)))).pack(fill="x", padx=8)
        self.steps_value_label = ttk.Label(frame, text="20")
        self.steps_value_label.pack(anchor="e", padx=8)
        self.steps_var.trace_add("write", self._update_steps_label)

        ttk.Label(frame, text="CFG (guidance scale):").pack(anchor="w", **pad)
        self.cfg_var = tk.DoubleVar(value=self.cfg.get("default_guidance_scale", 7.5))
        ttk.Scale(frame, from_=1.0, to=20.0, variable=self.cfg_var,
                  command=lambda v: self.cfg_var.set(round(float(v), 2))).pack(fill="x", padx=8)
        self.cfg_value_label = ttk.Label(frame, text="7.50")
        self.cfg_value_label.pack(anchor="e", padx=8)
        self.cfg_var.trace_add("write", self._update_cfg_label)

        seedf = ttk.Frame(frame)
        seedf.pack(fill="x", **pad)
        ttk.Label(seedf, text="Seed (-1 = случайно):").pack(side="left")
        self.seed_var = tk.IntVar(value=self.cfg.get("default_seed", -1))
        ttk.Entry(seedf, textvariable=self.seed_var, width=12).pack(side="left", padx=8)
        ttk.Button(seedf, text="🔁 Случайно", command=lambda: self.seed_var.set(-1)).pack(side="left")

        ttk.Separator(frame).pack(fill="x", **pad)
        self.gen_button = ttk.Button(frame, text="🎨 Сгенерировать", command=self._start_generation)
        self.gen_button.pack(fill="x", **pad)
        self.stop_button = ttk.Button(frame, text="⏹ Выгрузить модель", command=self._unload_model, state="disabled")
        self.stop_button.pack(fill="x", **pad)
        self.model_status_var = tk.StringVar(value="Модель не загружена")
        ttk.Label(frame, textvariable=self.model_status_var, foreground="blue",
                  font=("Segoe UI", 9)).pack(anchor="w", **pad)
        ttk.Label(frame, text=f"Устройство: {config.resolve_device(self.cfg)}",
                  foreground="gray", font=("Segoe UI", 9)).pack(anchor="w", **pad)
        ttk.Separator(frame).pack(fill="x", **pad)
        ttk.Button(frame, text="💾 Сохранить как...", command=self._save_as).pack(fill="x", **pad)
        ttk.Button(frame, text="📂 Открыть папку output", command=self._open_output_dir).pack(fill="x", **pad)
        ttk.Button(frame, text="⚙ Открыть config.json", command=self._open_config).pack(fill="x", **pad)

    def _build_preview_panel(self, parent):
        pf = ttk.LabelFrame(parent, text="Предпросмотр")
        pf.pack(fill="both", expand=True, padx=4, pady=4)
        self.preview_label = tk.Label(pf, bg="#2b2b2b",
                                      text="Здесь появится изображение\nпосле генерации",
                                      fg="white", font=("Segoe UI", 12), width=80, height=30)
        self.preview_label.pack(fill="both", expand=True, padx=4, pady=4)
        sf = ttk.LabelFrame(parent, text="Статус")
        sf.pack(fill="x", padx=4, pady=4)
        self.status_var = tk.StringVar(value="Готов к работе")
        ttk.Label(sf, textvariable=self.status_var, font=("Segoe UI", 10)).pack(anchor="w", padx=8, pady=4)
        self.progress = ttk.Progressbar(sf, mode="determinate", maximum=100)
        self.progress.pack(fill="x", padx=8, pady=(0, 8))
        self.last_prompt_var = tk.StringVar(value="")
        ttk.Label(sf, textvariable=self.last_prompt_var, foreground="gray",
                  font=("Segoe UI", 9), wraplength=700).pack(anchor="w", padx=8, pady=(0, 8))

    def _on_mode_change(self):
        if self.mode_var.get() == "img2img":
            self.init_img_frame.pack(fill="x", padx=8, pady=4, before=self.gen_button)
        else:
            self.init_img_frame.pack_forget()

    def _update_strength_label(self, *_):
        self.strength_value_label.config(text=f"{self.strength_var.get():.2f}")

    def _update_steps_label(self, *_):
        self.steps_value_label.config(text=str(self.steps_var.get()))

    def _update_cfg_label(self, *_):
        self.cfg_value_label.config(text=f"{self.cfg_var.get():.2f}")

    def _load_init_image(self):
        path = filedialog.askopenfilename(
            title="Выберите изображение",
            filetypes=[("Изображения", "*.png *.jpg *.jpeg *.bmp *.webp"), ("Все файлы", "*.*")])
        if not path:
            return
        try:
            self.init_image = Image.open(path)
            self.init_img_label_var.set(f"{Path(path).name}  ({self.init_image.size[0]}x{self.init_image.size[1]})")
        except Exception as e:
            messagebox.showerror("Ошибка", f"Не удалось открыть изображение:\n{e}")

    def _clear_init_image(self):
        self.init_image = None
        self.init_img_label_var.set("Изображение не выбрано")

    def _open_output_dir(self):
        import subprocess
        try:
            if sys.platform.startswith("win"):
                os.startfile(str(config.OUTPUT_DIR))
            else:
                subprocess.run(["xdg-open", str(config.OUTPUT_DIR)])
        except Exception as e:
            logger.error("Не удалось открыть папку: %s", e)

    def _open_config(self):
        import subprocess
        try:
            if sys.platform.startswith("win"):
                os.startfile(str(config.CONFIG_PATH))
            else:
                subprocess.run(["xdg-open", str(config.CONFIG_PATH)])
        except Exception as e:
            logger.error("Не удалось открыть конфиг: %s", e)

    def _start_generation(self):
        if self._generating:
            messagebox.showinfo("Занято", "Генерация уже идёт, подождите.")
            return
        prompt = self.prompt_text.get("1.0", "end").strip()
        if not prompt:
            messagebox.showwarning("Пусто", "Введите промпт.")
            return
        negative = self.neg_prompt_text.get("1.0", "end").strip()
        try:
            w, h = [int(x) for x in self.size_var.get().split("x")]
        except Exception:
            messagebox.showerror("Ошибка", "Неверный формат размера.")
            return
        w = (w // 8) * 8
        h = (h // 8) * 8
        steps = int(self.steps_var.get())
        cfg_scale = float(self.cfg_var.get())
        seed = int(self.seed_var.get())
        mode = self.mode_var.get()
        self.cfg["model_id"] = self.model_var.get()
        self.cfg["default_steps"] = steps
        self.cfg["default_guidance_scale"] = cfg_scale
        self.cfg["default_seed"] = seed
        self.cfg["default_width"] = w
        self.cfg["default_height"] = h
        self.cfg["auto_translate"] = bool(self.auto_translate_var.get())
        config.save_config(self.cfg)
        if self.generator.model_id != self.cfg["model_id"]:
            self.generator.unload()
            self.generator.model_id = self.cfg["model_id"]
        if mode == "img2img" and self.init_image is None:
            messagebox.showwarning("Нет изображения", "Загрузите исходное изображение для img2img.")
            return
        self._generating = True
        self.gen_button.config(state="disabled")
        self.stop_button.config(state="normal")
        self.progress["value"] = 0
        self.status_var.set("Подготовка...")
        self.last_prompt_var.set(f"Промпт: {prompt}")
        task = dict(mode=mode, prompt=prompt, negative=negative, width=w, height=h,
                    steps=steps, cfg_scale=cfg_scale, seed=seed,
                    auto_translate=bool(self.auto_translate_var.get()),
                    init_image=self.init_image if mode == "img2img" else None,
                    strength=float(self.strength_var.get()))
        self.generator.cfg = self.cfg
        self.generator.device = config.resolve_device(self.cfg)
        self._worker_thread = threading.Thread(target=self._worker, args=(task,), daemon=True)
        self._worker_thread.start()

    def _worker(self, task):
        try:
            self._put("status", "Перевод промпта..." if task["auto_translate"] else "Готовлю генерацию...")
            final_prompt = task["prompt"]
            final_negative = task["negative"]
            if task["auto_translate"]:
                from translator import get_translator
                tr = get_translator(self.cfg["translator_model"], device="cpu")
                self._put("status", "Перевод промпта на английский...")
                translated = tr.translate(task["prompt"])
                if translated and translated != task["prompt"]:
                    final_prompt = translated
                    self._put("info", f"Переведённый промпт: {translated}")
                if task["negative"]:
                    tn = tr.translate(task["negative"])
                    if tn:
                        final_negative = tn
            self._put("status", "Загрузка модели (если нужно)...")

            def pcb(msg, frac):
                self._put("progress", {"msg": msg, "frac": frac})

            if task["mode"] == "txt2img":
                image, used_seed = self.generator.txt2img(
                    prompt=final_prompt, negative_prompt=final_negative,
                    width=task["width"], height=task["height"], steps=task["steps"],
                    guidance_scale=task["cfg_scale"], seed=task["seed"], progress_cb=pcb)
            else:
                image, used_seed = self.generator.img2img(
                    prompt=final_prompt, init_image=task["init_image"],
                    negative_prompt=final_negative, strength=task["strength"],
                    steps=task["steps"], guidance_scale=task["cfg_scale"],
                    seed=task["seed"], progress_cb=pcb)
            saved = save_image(image)
            self._put("done", {"image": image, "seed": used_seed, "path": str(saved),
                                "prompt": final_prompt, "neg": final_negative})
        except Exception as e:
            logger.exception("Ошибка в потоке генерации")
            self._put("error", str(e))

    def _put(self, kind, payload):
        self.msg_queue.put((kind, payload))

    def _poll_messages(self):
        try:
            while True:
                kind, payload = self.msg_queue.get_nowait()
                self._handle_message(kind, payload)
        except queue.Empty:
            pass
        self.after(100, self._poll_messages)

    def _handle_message(self, kind, payload):
        if kind == "status":
            self.status_var.set(str(payload))
        elif kind == "info":
            self.last_prompt_var.set(str(payload))
        elif kind == "progress":
            self.status_var.set(payload.get("msg", ""))
            self.progress["value"] = float(payload.get("frac", 0.0)) * 100
        elif kind == "done":
            self._generating = False
            self.gen_button.config(state="normal")
            self.stop_button.config(state="normal")
            image = payload["image"]
            seed = payload["seed"]
            path = payload["path"]
            self.current_image = image
            self._show_image(image)
            self.last_seed_var.set(str(seed))
            self.status_var.set(f"Готово. Сохранено: {Path(path).name}  (seed={seed})")
            self.progress["value"] = 100
            self.model_status_var.set("Модель загружена в память")
        elif kind == "error":
            self._generating = False
            self.gen_button.config(state="normal")
            self.stop_button.config(state="disabled")
            self.status_var.set("Ошибка")
            messagebox.showerror("Ошибка генерации", str(payload))
            self.progress["value"] = 0

    def _show_image(self, image):
        preview = resize_for_preview(image, PREVIEW_MAX)
        self.current_photo = ImageTk.PhotoImage(preview)
        self.preview_label.config(image=self.current_photo, text="")

    def _unload_model(self):
        if self._generating:
            messagebox.showinfo("Занято", "Дождитесь окончания генерации.")
            return
        try:
            self.generator.unload()
            try:
                from translator import _translator
                if _translator is not None and _translator._loaded:
                    _translator.unload()
            except Exception:
                pass
            self.model_status_var.set("Модель выгружена")
            self.stop_button.config(state="disabled")
            self.status_var.set("Модель выгружена из памяти.")
        except Exception as e:
            logger.error("Не удалось выгрузить модель: %s", e)

    def _save_as(self):
        if self.current_image is None:
            messagebox.showinfo("Нет изображения", "Сначала сгенерируйте изображение.")
            return
        path = filedialog.asksaveasfilename(
            title="Сохранить как", defaultextension=".png",
            filetypes=[("PNG", "*.png"), ("JPEG", "*.jpg"), ("WEBP", "*.webp")],
            initialdir=str(config.OUTPUT_DIR))
        if not path:
            return
        try:
            ext = Path(path).suffix.lower().lstrip(".")
            fmt = {"png": "PNG", "jpg": "JPEG", "jpeg": "JPEG", "webp": "WEBP"}.get(ext, "PNG")
            self.current_image.save(path, format=fmt)
            self.status_var.set(f"Сохранено: {path}")
        except Exception as e:
            messagebox.showerror("Ошибка", f"Не удалось сохранить:\n{e}")


# ===== GPON FTTH Planner вкладка =====
class GPONTab(ttk.Frame):
    def __init__(self, parent):
        super().__init__(parent)
        self.cfg = config.load_config()
        self.msg_queue: queue.Queue = queue.Queue()
        self._planning = False
        self._worker_thread = None
        self._build_ui()
        self.after(100, self._poll_messages)

    def _build_ui(self):
        main_paned = ttk.PanedWindow(self, orient=tk.HORIZONTAL)
        main_paned.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)
        left = ttk.Frame(main_paned, width=420)
        main_paned.add(left, weight=0)
        self._build_control_panel(left).pack(fill=tk.BOTH, expand=True)
        right = ttk.Frame(main_paned)
        main_paned.add(right, weight=1)
        self._build_results_panel(right)

    def _build_control_panel(self, parent):
        container = ttk.Frame(parent)
        canvas = tk.Canvas(container, highlightthickness=0, width=400)
        sb = ttk.Scrollbar(container, orient="vertical", command=canvas.yview)
        scrollable = ttk.Frame(canvas)
        scrollable.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=scrollable, anchor="nw")
        canvas.configure(yscrollcommand=sb.set)
        canvas.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

        def _on_wheel(event):
            canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
        canvas.bind_all("<MouseWheel>", _on_wheel)
        self._fill_form(scrollable)
        return container

    def _fill_form(self, frame):
        pad = {"padx": 8, "pady": 4}
        ttk.Label(frame, text="GPON FTTH Планировщик", font=("Segoe UI", 12, "bold")).pack(anchor="w", **pad)
        ttk.Label(frame, text="Оптимизировано для CPU • алгоритм v1.1",
                  foreground="gray", font=("Segoe UI", 9)).pack(anchor="w", **pad)
        ttk.Separator(frame).pack(fill="x", **pad)
        ttk.Label(frame, text="Имя села (латиницей):").pack(anchor="w", **pad)
        self.village_name_var = tk.StringVar(value="test_village")
        ttk.Entry(frame, textvariable=self.village_name_var).pack(fill="x", **pad)
        cf = ttk.Frame(frame)
        cf.pack(fill="x", **pad)
        ttk.Label(cf, text="Широта:").grid(row=0, column=0, sticky="w", padx=(0, 4))
        self.lat_var = tk.DoubleVar(value=50.1234)
        ttk.Entry(cf, textvariable=self.lat_var, width=10).grid(row=0, column=1, padx=4)
        ttk.Label(cf, text="Долгота:").grid(row=0, column=2, sticky="w", padx=(8, 4))
        self.lon_var = tk.DoubleVar(value=82.5678)
        ttk.Entry(cf, textvariable=self.lon_var, width=10).grid(row=0, column=3, padx=4)
        ttk.Label(frame, text="Подсказка: координаты можно получить с OSM или Google Maps",
                  foreground="gray", font=("Segoe UI", 8), wraplength=380).pack(anchor="w", **pad)
        ttk.Label(frame, text="Ожидаемое число домохозяйств:").pack(anchor="w", **pad)
        self.hh_var = tk.IntVar(value=200)
        ttk.Entry(frame, textvariable=self.hh_var).pack(fill="x", **pad)
        ttk.Label(frame, text="Радиус поиска, м:").pack(anchor="w", **pad)
        self.radius_var = tk.DoubleVar(value=2500.0)
        ttk.Scale(frame, from_=500, to=5000, variable=self.radius_var,
                  command=lambda v: self.radius_var.set(round(float(v), 0))).pack(fill="x", padx=8)
        self.radius_label = ttk.Label(frame, text="2500 м")
        self.radius_label.pack(anchor="e", padx=8)
        self.radius_var.trace_add("write", self._update_radius_label)
        ttk.Label(frame, text="Тип зоны:").pack(anchor="w", **pad)
        self.zone_var = tk.StringVar(value="bbox")
        zf = ttk.Frame(frame)
        zf.pack(fill="x", **pad)
        ttk.Radiobutton(zf, text="Bbox (авто-граница по застройке)", variable=self.zone_var, value="bbox").pack(anchor="w")
        ttk.Radiobutton(zf, text="Radius (фиксированный радиус)", variable=self.zone_var, value="radius").pack(anchor="w")
        ttk.Label(frame, text="force_anchor (id OSM, необязательно):").pack(anchor="w", **pad)
        self.force_anchor_var = tk.StringVar(value="")
        ttk.Entry(frame, textvariable=self.force_anchor_var).pack(fill="x", **pad)
        ttk.Label(frame, text="Порог окупаемости зонного ОРШ, волокно-км:").pack(anchor="w", **pad)
        self.s_min_var = tk.DoubleVar(value=15.0)
        ttk.Scale(frame, from_=5, to=50, variable=self.s_min_var,
                  command=lambda v: self.s_min_var.set(round(float(v), 1))).pack(fill="x", padx=8)
        self.s_min_label = ttk.Label(frame, text="15.0")
        self.s_min_label.pack(anchor="e", padx=8)
        self.s_min_var.trace_add("write", lambda *_: self.s_min_label.config(text=f"{self.s_min_var.get():.1f}"))
        ttk.Label(frame, text="Мин. ДХ в зоне (заполнение ≥75%):").pack(anchor="w", **pad)
        self.min_zone_var = tk.IntVar(value=48)
        ttk.Scale(frame, from_=16, to=128, variable=self.min_zone_var,
                  command=lambda v: self.min_zone_var.set(int(float(v)))).pack(fill="x", padx=8)
        self.min_zone_label = ttk.Label(frame, text="48")
        self.min_zone_label.pack(anchor="e", padx=8)
        self.min_zone_var.trace_add("write", lambda *_: self.min_zone_label.config(text=str(self.min_zone_var.get())))
        ttk.Separator(frame).pack(fill="x", **pad)
        self.render_map_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(frame, text="🗺 Отрисовать карту сети (спутник + слои)",
                        variable=self.render_map_var).pack(anchor="w", **pad)
        self.download_tiles_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(frame, text="📥 Скачать спутниковые тайлы Google z18",
                        variable=self.download_tiles_var).pack(anchor="w", **pad)
        ttk.Label(frame, text="Первый запуск: ~30–60 сек на 1000 тайлов. Кэш сохраняется.",
                  foreground="gray", font=("Segoe UI", 8), wraplength=380).pack(anchor="w", **pad)
        self.plan_button = ttk.Button(frame, text="🚀 Запустить планирование",
                                       command=self._start_planning)
        self.plan_button.pack(fill="x", **pad)
        self.stop_button = ttk.Button(frame, text="⏹ Очистить кэш OSM",
                                       command=self._clear_cache, state="normal")
        self.stop_button.pack(fill="x", **pad)
        self.plan_status_var = tk.StringVar(value="Готов к работе")
        ttk.Label(frame, textvariable=self.plan_status_var, foreground="blue",
                  font=("Segoe UI", 9)).pack(anchor="w", **pad)
        ttk.Separator(frame).pack(fill="x", **pad)
        ttk.Button(frame, text="📂 Открыть папку GPON output", command=self._open_output_dir).pack(fill="x", **pad)
        ttk.Button(frame, text="💾 Сохранить отчёт (JSON)...", command=self._save_report).pack(fill="x", **pad)
        ttk.Separator(frame).pack(fill="x", **pad)
        info_text = ("Алгоритм:\n1. fetch: OSM-дороги и здания\n2. households: кластеризация (eps=16м)\n"
                     "3. anchor: скоринг ЦУ\n4. network: MST + SPT (Dijkstra)\n"
                     "5. boq: схема A + схема D\n6. optical_budget: затухание худшей линии\n\n"
                     "Оптимизации:\n• один вызов Dijkstra\n• CSR + KDTree (scipy)\n• float32 для памяти")
        ttk.Label(frame, text=info_text, foreground="gray", font=("Segoe UI", 8),
                  justify="left", wraplength=380).pack(anchor="w", **pad)

    def _build_results_panel(self, parent):
        pf = ttk.LabelFrame(parent, text="Результаты планирования")
        pf.pack(fill="both", expand=True, padx=4, pady=4)
        sf = ttk.Frame(pf)
        sf.pack(fill="x", padx=8, pady=4)
        self.plan_progress_var = tk.StringVar(value="Ожидание...")
        ttk.Label(sf, textvariable=self.plan_progress_var, font=("Segoe UI", 10)).pack(anchor="w")
        self.plan_progress = ttk.Progressbar(sf, mode="determinate", maximum=100)
        self.plan_progress.pack(fill="x", pady=4)
        self.results_notebook = ttk.Notebook(pf)
        self.results_notebook.pack(fill="both", expand=True, padx=4, pady=4)
        report_tab = ttk.Frame(self.results_notebook)
        self.results_notebook.add(report_tab, text="📋 Отчёт")
        self.report_text = tk.Text(report_tab, wrap="word", font=("Consolas", 10),
                                    state="disabled", bg="#1e1e1e", fg="#d4d4d4", insertbackground="white")
        sb = ttk.Scrollbar(report_tab, command=self.report_text.yview)
        self.report_text.configure(yscrollcommand=sb.set)
        self.report_text.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self.report_text.tag_configure("header", foreground="#569cd6", font=("Consolas", 11, "bold"))
        self.report_text.tag_configure("section", foreground="#4ec9b0", font=("Consolas", 10, "bold"))
        self.report_text.tag_configure("ok", foreground="#4af68c")
        self.report_text.tag_configure("warn", foreground="#ce9178")
        self.report_text.tag_configure("err", foreground="#f44747")
        self.report_text.tag_configure("num", foreground="#dcdcaa")
        map_tab = ttk.Frame(self.results_notebook)
        self.results_notebook.add(map_tab, text="🗺 Карта")
        map_btn_frame = ttk.Frame(map_tab)
        map_btn_frame.pack(fill="x", padx=4, pady=4)
        ttk.Button(map_btn_frame, text="📂 Открыть карту (JPG)", command=self._open_map_jpg).pack(side="left", padx=2)
        ttk.Button(map_btn_frame, text="🖼 Открыть превью", command=self._open_preview_png).pack(side="left", padx=2)
        ttk.Button(map_btn_frame, text="💾 Сохранить карту как...", command=self._save_map_as).pack(side="left", padx=2)
        map_preview_frame = ttk.LabelFrame(map_tab, text="Предпросмотр карты")
        map_preview_frame.pack(fill="both", expand=True, padx=4, pady=4)
        self.map_preview_label = tk.Label(map_preview_frame, bg="#2b2b2b",
                                           text="Карта появится здесь после планирования",
                                           fg="white", font=("Segoe UI", 12), width=80, height=30)
        self.map_preview_label.pack(fill="both", expand=True, padx=4, pady=4)
        self.map_photo = None
        self.map_path = None
        self.preview_path = None

    def _update_radius_label(self, *_):
        self.radius_label.config(text=f"{int(self.radius_var.get())} м")

    def _clear_cache(self):
        import shutil
        cache_dir = config.APP_DIR / "gpon_work" / "osm_raw"
        if cache_dir.exists():
            try:
                shutil.rmtree(cache_dir)
                cache_dir.mkdir(parents=True)
                messagebox.showinfo("Готово", f"Кэш очищен: {cache_dir}")
            except Exception as e:
                messagebox.showerror("Ошибка", str(e))
        else:
            messagebox.showinfo("Кэш", f"Папки нет: {cache_dir}")

    def _open_output_dir(self):
        import subprocess
        out_dir = config.APP_DIR / "gpon_work"
        out_dir.mkdir(exist_ok=True)
        try:
            if sys.platform.startswith("win"):
                os.startfile(str(out_dir))
            else:
                subprocess.run(["xdg-open", str(out_dir)])
        except Exception as e:
            logger.error("Не удалось открыть папку: %s", e)

    def _start_planning(self):
        if self._planning:
            messagebox.showinfo("Занято", "Планирование уже идёт.")
            return
        try:
            lat = float(self.lat_var.get())
            lon = float(self.lon_var.get())
            hh = int(self.hh_var.get())
            radius = float(self.radius_var.get())
            name = self.village_name_var.get().strip() or "test_village"
        except Exception as e:
            messagebox.showerror("Ошибка ввода", f"Проверьте значения: {e}")
            return
        if not (-90 <= lat <= 90) or not (-180 <= lon <= 180):
            messagebox.showerror("Ошибка", "Координаты вне диапазона.")
            return
        fa_raw = self.force_anchor_var.get().strip()
        force_anchor = int(fa_raw) if fa_raw else None
        task = dict(name=name, lat=lat, lon=lon, hh=hh, radius=radius,
                    zone_type=self.zone_var.get(), force_anchor=force_anchor,
                    s_min_km=float(self.s_min_var.get()),
                    min_zone_dh=int(self.min_zone_var.get()),
                    render_map=bool(self.render_map_var.get()),
                    download_tiles=bool(self.download_tiles_var.get()))
        self._planning = True
        self.plan_button.config(state="disabled")
        self.plan_progress["value"] = 0
        self.plan_progress_var.set("Запуск...")
        self._append_report("header", f"\n=== Запуск планирования: {name} ===\n")
        self._append_report("", f"Координаты: {lat:.4f}, {lon:.4f}\n")
        self._append_report("", f"Ожидаемое ДХ: {hh}, радиус поиска: {radius} м\n\n")
        self._worker_thread = threading.Thread(target=self._worker, args=(task,), daemon=True)
        self._worker_thread.start()

    def _worker(self, task):
        try:
            from gpon_planner import GPONPlanner, VillageSpec
            work_dir = str(config.APP_DIR / "gpon_work")
            planner = GPONPlanner(work_dir=work_dir)
            planner.params['s_min_km'] = task['s_min_km']
            planner.params['min_zone_dh'] = task['min_zone_dh']
            village = VillageSpec(key=task['name'], name=task['name'], lat=task['lat'], lon=task['lon'],
                                   hh=task['hh'], radius_m=task['radius'], zone_type=task['zone_type'],
                                   zone_radius=900.0, force_anchor=task['force_anchor'])

            def progress_cb(msg, frac):
                self._put("progress", {"msg": msg, "frac": frac})
                if any(kw in msg.lower() for kw in ['osm:', 'найдено', 'сеть:', 'boq:', 'бюджет', 'граница', 'цу:']):
                    self._put("log", msg)

            result = planner.run(village, progress_cb=progress_cb,
                                  render_map=task['render_map'], download_tiles=task['download_tiles'])
            self._put("done", result)
        except Exception as e:
            logger.exception("Ошибка в потоке GPON")
            self._put("error", str(e))

    def _put(self, kind, payload):
        self.msg_queue.put((kind, payload))

    def _poll_messages(self):
        try:
            while True:
                kind, payload = self.msg_queue.get_nowait()
                self._handle_message(kind, payload)
        except queue.Empty:
            pass
        self.after(100, self._poll_messages)

    def _handle_message(self, kind, payload):
        if kind == "progress":
            self.plan_progress_var.set(payload.get("msg", ""))
            self.plan_progress["value"] = float(payload.get("frac", 0.0)) * 100
        elif kind == "log":
            self._append_report("", f"  {payload}\n")
        elif kind == "done":
            self._planning = False
            self.plan_button.config(state="normal")
            self.plan_progress["value"] = 100
            self.plan_progress_var.set("Готово!")
            self._render_report(payload)
            self._show_map_preview(payload)
            self.last_result = payload
            if payload.get('map_path'):
                self.results_notebook.select(1)
        elif kind == "error":
            self._planning = False
            self.plan_button.config(state="normal")
            self.plan_progress_var.set("Ошибка")
            self.plan_progress["value"] = 0
            messagebox.showerror("Ошибка планирования", str(payload))

    def _append_report(self, tag, text):
        self.report_text.config(state="normal")
        self.report_text.insert("end", text, tag if tag else ())
        self.report_text.see("end")
        self.report_text.config(state="disabled")

    def _render_report(self, result):
        self.report_text.config(state="normal")
        self.report_text.delete("1.0", "end")
        hh = result.get('households', [])
        net = result.get('network', {})
        stats = net.get('stats', {})
        boq = result.get('boq', {})
        ob = result.get('optical_budget', {})
        self._append_report("header", f"{'=' * 60}\n")
        self._append_report("header", f" ОТЧЁТ ПО ПЛАНИРОВАНИЮ FTTH (GPON)\n")
        self._append_report("header", f"{'=' * 60}\n\n")
        v = result.get('village', {})
        self._append_report("section", "▼ Село\n")
        self._append_report("", f"  Имя:        {v.get('name', '?')}\n")
        self._append_report("", f"  Координаты: {v.get('lat', 0):.4f}, {v.get('lon', 0):.4f}\n")
        self._append_report("", f"  Радиус:     {v.get('radius_m', 0):.0f} м\n\n")
        self._append_report("section", "▼ Домохозяйства\n")
        dev = 100 * (len(hh) - v.get('hh', 0)) / max(1, v.get('hh', 1))
        dev_tag = "ok" if abs(dev) <= 25 else "warn"
        self._append_report("", f"  Найдено:    {len(hh)} ДХ\n")
        self._append_report("", f"  Заказ:      {v.get('hh', 0)} ДХ\n")
        self._append_report("", f"  Отклонение: ")
        self._append_report(dev_tag, f"{dev:+.1f}%\n\n")
        anchor = result.get('anchor_cands', [{}])[0]
        self._append_report("section", "▼ Центральный узел (ЦУ)\n")
        self._append_report("", f"  id OSM:     {anchor.get('id', '?')}\n")
        self._append_report("", f"  Площадь:    {anchor.get('area', 0):.0f} м²\n")
        self._append_report("", f"  Score:      {anchor.get('score', 0)}\n\n")
        self._append_report("section", "▼ Сеть\n")
        self._append_report("", f"  Обслужено:    {stats.get('served', 0)}/{stats.get('households', 0)} ДХ\n")
        self._append_report("", f"  Муфт:         {stats.get('couplers', 0)}\n")
        self._append_report("num", f"  Магистраль:   {stats.get('feeder_km', 0):.2f} км\n")
        self._append_report("num", f"  Дропы:        {stats.get('drop_km', 0):.2f} км\n")
        self._append_report("num", f"  Средний дроп: {stats.get('avg_drop_m', 0):.0f} м\n")
        self._append_report("num", f"  Макс. дроп:   {stats.get('max_drop_m', 0):.0f} м\n\n")
        a = boq.get('scheme_a', {})
        self._append_report("section", "▼ BoQ — схема A (централизованная)\n")
        self._append_report("num", f"  Волокно-км:     {a.get('fiber_km', 0):.1f}\n")
        self._append_report("num", f"  Кабель-км:      {a.get('cable_km_raw', 0):.2f}\n")
        self._append_report("num", f"  Дроп-км:        {a.get('drop_cable_km', 0):.2f}\n")
        self._append_report("num", f"  Сплиттеры 1:64: {a.get('splitters64', 0)}\n")
        self._append_report("num", f"  Сварки:         {a.get('splices', 0)}\n")
        self._append_report("num", f"  Муфты:          {a.get('mufty', 0)}\n\n")
        self._append_report("section", "▼ BoQ — схема D (зонные ОРШ) — рекомендуется\n")
        self._append_report("num", f"  Зон:            {boq.get('n_zones', 0)} + ЦУ\n")
        self._append_report("num", f"  Всего ОРШ:      {boq.get('orsh', 0)}\n")
        self._append_report("num", f"  Волокно-км:     {boq.get('fiber_km', 0):.1f}\n")
        self._append_report("num", f"  Кабель-км:      {boq.get('cable_km_raw', 0):.2f}\n")
        self._append_report("num", f"  Сплиттеры 1:64: {boq.get('splitters64', 0)}\n")
        self._append_report("num", f"  Сварки:         {boq.get('materials', {}).get('splices', 0)}\n")
        self._append_report("num", f"  Муфты:          {boq.get('mufty', 0)}\n")
        self._append_report("num", f"  Дроп-км:        {boq.get('drop_cable_km', 0):.2f}\n\n")
        fka = a.get('fiber_km', 0)
        fkd = boq.get('fiber_km', 0)
        if fka > 0:
            saving = 100 * (fka - fkd) / fka
            self._append_report("ok", f"  Экономия волокно-км: {saving:.0f}% ({fka:.0f} → {fkd:.0f})\n\n")
        self._append_report("section", "▼ Оптический бюджет (Class B+ = 28 дБ)\n")
        self._append_report("", f"  Худшая линия:   ")
        worst = ob.get('worst_attenuation_db', 0)
        worst_tag = "ok" if worst <= 25 else ("warn" if worst <= 28 else "err")
        self._append_report(worst_tag, f"{worst:.2f} дБ\n")
        margin = ob.get('worst_margin_db', 0)
        m_tag = "ok" if margin >= 3 else ("warn" if margin >= 0 else "err")
        self._append_report("", f"  Маржа:          ")
        self._append_report(m_tag, f"{margin:.2f} дБ\n")
        all_ok = ob.get('all_ok', False)
        self._append_report("", f"  Статус:         ")
        if all_ok:
            self._append_report("ok", "ВСЕ ЗОНЫ OK\n")
        else:
            self._append_report("err", "ЕСТЬ ПРЕВЫШЕНИЯ\n")
        self._append_report("", "\n")
        zones = ob.get('zones', [])
        if zones:
            self._append_report("section", "▼ Зоны (детально)\n")
            for i, z in enumerate(zones):
                st = z.get('status', '')
                tag = "ok" if st == 'ok' else ("warn" if st == 'ok-hard' else "err")
                self._append_report("", f"  Зона {i}: ")
                self._append_report("num", f"{z.get('houses', 0)} ДХ, ")
                self._append_report("", f"L={z.get('L_km', 0):.2f} км, A=")
                self._append_report(tag, f"{z.get('attenuation_db', 0):.2f} дБ, ")
                self._append_report("", f"маржа=")
                self._append_report(tag, f"{z.get('margin_db', 0):.2f} дБ ")
                self._append_report(tag, f"[{st}]\n")
                if z.get('recommendation'):
                    self._append_report("warn", f"         ↳ {z['recommendation']}\n")
        self._append_report("header", f"\n{'=' * 60}\n")
        if result.get('map_path'):
            self._append_report("section", "\n▼ Карта сети\n")
            self._append_report("", f"  Файл:        {result['map_path']}\n")
            if result.get('preview_path'):
                self._append_report("", f"  Превью:      {result['preview_path']}\n")
            self._append_report("ok", "  Карта построена на спутниковой мозаике\n")
        else:
            self._append_report("warn", "\n▼ Карта сети\n  Карта не построена\n")

    def _show_map_preview(self, result):
        preview_path = result.get('preview_path')
        self.map_path = result.get('map_path')
        self.preview_path = preview_path
        if not preview_path or not os.path.exists(preview_path):
            self.map_preview_label.config(image="", text="Карта не построена")
            self.map_photo = None
            return
        try:
            img = Image.open(preview_path)
            max_w, max_h = 1100, 700
            w, h = img.size
            ratio = min(max_w / w, max_h / h, 1.0)
            if ratio < 1.0:
                img = img.resize((int(w * ratio), int(h * ratio)), Image.LANCZOS)
            self.map_photo = ImageTk.PhotoImage(img)
            self.map_preview_label.config(image=self.map_photo, text="")
        except Exception as e:
            logger.error("Не удалось отобразить карту: %s", e)
            self.map_preview_label.config(image="", text=f"Ошибка: {e}")

    def _open_map_jpg(self):
        if not self.map_path or not os.path.exists(self.map_path):
            messagebox.showinfo("Нет карты", "Сначала запустите планирование с картой.")
            return
        import subprocess
        try:
            if sys.platform.startswith("win"):
                os.startfile(self.map_path)
            elif sys.platform == "darwin":
                subprocess.run(["open", self.map_path])
            else:
                subprocess.run(["xdg-open", self.map_path])
        except Exception as e:
            messagebox.showerror("Ошибка", f"Не удалось открыть: {e}")

    def _open_preview_png(self):
        if not self.preview_path or not os.path.exists(self.preview_path):
            messagebox.showinfo("Нет превью", "Сначала запустите планирование с картой.")
            return
        import subprocess
        try:
            if sys.platform.startswith("win"):
                os.startfile(self.preview_path)
            elif sys.platform == "darwin":
                subprocess.run(["open", self.preview_path])
            else:
                subprocess.run(["xdg-open", self.preview_path])
        except Exception as e:
            messagebox.showerror("Ошибка", f"Не удалось открыть: {e}")

    def _save_map_as(self):
        if not self.map_path or not os.path.exists(self.map_path):
            messagebox.showinfo("Нет карты", "Сначала запустите планирование с картой.")
            return
        src = self.map_path
        dst = filedialog.asksaveasfilename(
            title="Сохранить карту как", defaultextension=".jpg",
            filetypes=[("JPEG", "*.jpg"), ("PNG", "*.png"), ("Все файлы", "*.*")],
            initialfile=Path(src).name)
        if not dst:
            return
        try:
            import shutil
            shutil.copy2(src, dst)
            messagebox.showinfo("Сохранено", f"Карта сохранена: {dst}")
        except Exception as e:
            messagebox.showerror("Ошибка", f"Не удалось сохранить: {e}")

    def _save_report(self):
        if not hasattr(self, 'last_result'):
            messagebox.showinfo("Нет данных", "Сначала запустите планирование.")
            return
        path = filedialog.asksaveasfilename(
            title="Сохранить отчёт", defaultextension=".json",
            filetypes=[("JSON", "*.json"), ("Все файлы", "*.*")],
            initialdir=str(config.APP_DIR / "gpon_work"),
            initialfile=f"gpon_{self.village_name_var.get()}.json")
        if not path:
            return
        try:
            import numpy as np

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
                if isinstance(o, Path):
                    return str(o)
                raise TypeError(f'not serializable: {type(o)}')

            with open(path, 'w', encoding='utf-8') as f:
                json.dump(self.last_result, f, ensure_ascii=False, indent=2, default=_conv)
            self.plan_status_var.set(f"Сохранено: {path}")
        except Exception as e:
            messagebox.showerror("Ошибка", f"Не удалось сохранить:\n{e}")


try:
    from gpon_planner import DEFAULT_PARAMS as DEFAULTS
except ImportError:
    DEFAULTS = {}


# ===== Главное окно =====
class App:
    def __init__(self, root):
        self.root = root
        self.root.title("Локальные ИИ-инструменты — SD Generator + GPON Planner")
        self.root.geometry("1400x900")
        self.root.minsize(1100, 750)
        self.notebook = ttk.Notebook(root)
        self.notebook.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)
        self.sd_tab = SDTab(self.notebook)
        self.notebook.add(self.sd_tab, text="🎨 SD Image Generator")
        self.gpon_tab = GPONTab(self.notebook)
        self.notebook.add(self.gpon_tab, text="📡 GPON FTTH Planner")

    @property
    def _generating(self):
        return self.sd_tab._generating


def _startup_check_dependencies():
    print("=" * 60)
    print("Проверка зависимостей приложения")
    print("=" * 60)
    try:
        from updater import check_dependencies, update_dependencies

        def progress(msg):
            print(f"  {msg}")

        check_crit = check_dependencies(check_sd=False, progress_cb=progress)
        if check_crit['need_install']:
            print("\n⚠ Не хватает критичных пакетов. Устанавливаю...")
            ok = update_dependencies(check_sd=False, progress_cb=progress)
            if not ok:
                print("✗ Не удалось установить критичные пакеты.")
                print("  Попробуйте вручную: pip install -r requirements.txt")
                return False
            print("\n✓ Критичные пакеты установлены. Перезапустите приложение.")
            try:
                import tkinter as tk
                from tkinter import messagebox
                root = tk.Tk()
                root.withdraw()
                messagebox.showinfo("Перезапуск",
                                    "Критичные пакеты установлены.\nПриложение будет закрыто. Запустите его снова.")
                root.destroy()
            except Exception:
                pass
            return False

        check_sd = check_dependencies(check_sd=True, progress_cb=progress)
        if check_sd['need_install']:
            missing = check_sd['missing']
            outdated = check_sd['outdated']
            print(f"\n⚠ SD-пакеты: {len(missing)} отсутствует, {len(outdated)} устарело")
            try:
                import tkinter as tk
                from tkinter import messagebox
                root = tk.Tk()
                root.withdraw()
                msg_parts = []
                if missing:
                    msg_parts.append(f"Отсутствуют ({len(missing)}):\n  " + "\n  ".join(missing))
                if outdated:
                    msg_parts.append("Устарели (" + str(len(outdated)) + "):\n  " +
                                      "\n  ".join(f"{p}: {i} → {t}" for p, i, t in outdated))
                msg = ("Для вкладки SD Image Generator нужны дополнительные пакеты.\n\n" +
                        "\n\n".join(msg_parts) +
                        "\n\nУстановить сейчас? (~2 ГБ для SD)\n"
                        "Можно пропустить — GPON-планировщик будет работать.\n"
                        "После установки приложение нужно перезапустить.")
                result = messagebox.askyesno("Обновление SD-пакетов", msg, icon='question')
                root.destroy()
                if result:
                    print("\nУстанавливаю SD-пакеты...")
                    ok = update_dependencies(check_sd=True, progress_cb=progress)
                    if ok:
                        print("✓ SD-пакеты установлены. Перезапустите приложение.")
                        try:
                            root = tk.Tk()
                            root.withdraw()
                            messagebox.showinfo("Перезапуск",
                                                "SD-пакеты установлены.\nПриложение будет закрыто. Запустите его снова.")
                            root.destroy()
                        except Exception:
                            pass
                        return False
                    else:
                        print("✗ Ошибка установки SD-пакетов. GPON продолжит работать.")
                else:
                    print("\nSD-пакеты пропущены. GPON-планировщик будет работать.")
            except Exception as e:
                print(f"Не удалось показать messagebox: {e}")
                print("Продолжаю без SD-пакетов.")
        else:
            print("✓ Все зависимости (включая SD) готовы")
        return True
    except Exception as e:
        print(f"✗ Ошибка проверки зависимостей: {e}")
        print("  Продолжаю запуск приложения.")
        return True


def main():
    should_continue = _startup_check_dependencies()
    if not should_continue:
        return
    try:
        from ctypes import windll
        windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass
    root = tk.Tk()
    style = ttk.Style()
    try:
        style.theme_use("vista")
    except Exception:
        pass
    app = App(root)

    def on_close():
        if app._generating:
            if not messagebox.askokcancel("Выйти?", "Генерация ещё идёт. Выйти принудительно?"):
                return
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", on_close)
    root.mainloop()


if __name__ == "__main__":
    main()
