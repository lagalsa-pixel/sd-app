"""
updater.py — модуль автоматического обновления зависимостей при запуске.

Проверяет, что все необходимые пакеты установлены и актуальны.
При отсутствии или устаревании — устанавливает/обновляет через pip.
"""
from __future__ import annotations

import logging
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Callable, List, Optional, Tuple

logger = logging.getLogger(__name__)

MIN_VERSIONS = {
    'torch': '2.1.0', 'torchvision': '0.16.0',
    'diffusers': '0.27.0', 'transformers': '4.38.0', 'accelerate': '0.25.0',
    'scipy': '1.10.0', 'numpy': '1.24.0', 'Pillow': '10.0.0', 'requests': '2.28.0',
}
CRITICAL_PACKAGES = ['Pillow', 'numpy', 'scipy', 'requests']
SD_PACKAGES = ['torch', 'torchvision', 'diffusers', 'transformers', 'accelerate']


def _parse_version(v: str) -> Tuple[int, ...]:
    parts = re.findall(r'\d+', v)
    return tuple(int(p) for p in parts[:4])


def _version_ge(a: str, b: str) -> bool:
    pa = _parse_version(a)
    pb = _parse_version(b)
    while len(pa) < len(pb):
        pa = pa + (0,)
    while len(pb) < len(pa):
        pb = pb + (0,)
    return pa >= pb


def _get_installed_version(package: str) -> Optional[str]:
    try:
        from importlib.metadata import version, PackageNotFoundError
        try:
            return version(package)
        except PackageNotFoundError:
            return None
    except ImportError:
        pass
    try:
        result = subprocess.run(
            [sys.executable, '-m', 'pip', 'show', package],
            capture_output=True, text=True, timeout=15)
        if result.returncode == 0:
            for line in result.stdout.splitlines():
                if line.startswith('Version:'):
                    return line.split(':', 1)[1].strip()
    except Exception:
        pass
    return None


def _pip_install(args: List[str], progress_cb: Optional[Callable[[str], None]] = None) -> bool:
    cmd = [sys.executable, '-m', 'pip', 'install'] + args
    logger.info("Запускаю: %s", ' '.join(cmd))
    if progress_cb:
        progress_cb(f"Запускаю: {' '.join(cmd)}")
    try:
        process = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding='utf-8', errors='replace')
        for line in process.stdout:
            line = line.rstrip()
            if line:
                logger.info("[pip] %s", line)
                if progress_cb:
                    progress_cb(line)
        process.wait()
        return process.returncode == 0
    except Exception as e:
        logger.error("Ошибка pip install: %s", e)
        if progress_cb:
            progress_cb(f"Ошибка: {e}")
        return False


def _pip_ok() -> bool:
    try:
        result = subprocess.run(
            [sys.executable, '-m', 'pip', '--version'],
            capture_output=True, text=True, timeout=10)
        return result.returncode == 0
    except Exception:
        return False


def check_dependencies(requirements_path: Optional[str] = None,
                        check_sd: bool = True,
                        progress_cb: Optional[Callable[[str], None]] = None) -> dict:
    def _log(msg: str):
        logger.info("[updater] %s", msg)
        if progress_cb:
            progress_cb(msg)

    if requirements_path is None:
        requirements_path = str(Path(__file__).parent / 'requirements.txt')
    _log(f"Проверяю зависимости из {requirements_path}")

    packages_to_check = list(CRITICAL_PACKAGES)
    if check_sd:
        packages_to_check += SD_PACKAGES

    required_versions = {}
    if os.path.exists(requirements_path):
        with open(requirements_path, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith('#'):
                    continue
                m = re.match(r'^([A-Za-z0-9_-]+)\s*(>=|==|~=|>)\s*([\d.]+)', line)
                if m:
                    pkg, op, ver = m.group(1), m.group(2), m.group(3)
                    required_versions[pkg] = (op, ver)

    missing, outdated, ok = [], [], []
    for pkg in packages_to_check:
        installed = _get_installed_version(pkg)
        if installed is None:
            missing.append(pkg)
            _log(f"  ✗ {pkg}: НЕ установлен")
        else:
            min_ver = MIN_VERSIONS.get(pkg)
            req_ver = required_versions.get(pkg)
            target_ver = None
            if req_ver:
                target_ver = req_ver[1]
            if min_ver and (target_ver is None or _version_ge(min_ver, target_ver)):
                target_ver = min_ver
            if target_ver and not _version_ge(installed, target_ver):
                outdated.append((pkg, installed, target_ver))
                _log(f"  ⚠ {pkg}: {installed} (нужно >= {target_ver})")
            else:
                ok.append(pkg)
                _log(f"  ✓ {pkg}: {installed}")

    result = {
        'missing': missing, 'outdated': outdated, 'ok': ok,
        'need_install': bool(missing or outdated),
    }
    if result['need_install']:
        _log(f"⚠ Требуется установка/обновление: "
              f"{len(missing)} отсутствует, {len(outdated)} устарело")
    else:
        _log(f"✓ Все {len(ok)} пакетов актуальны")
    return result


def update_dependencies(requirements_path: Optional[str] = None,
                         check_sd: bool = True,
                         progress_cb: Optional[Callable[[str], None]] = None) -> bool:
    def _log(msg: str):
        logger.info("[updater] %s", msg)
        if progress_cb:
            progress_cb(msg)

    check = check_dependencies(requirements_path, check_sd, progress_cb)
    if not check['need_install']:
        _log("✓ Обновление не требуется")
        return True

    if not _pip_ok():
        _log("⚠ pip недоступен. Установите pip вручную.")
        return False

    if requirements_path is None:
        requirements_path = str(Path(__file__).parent / 'requirements.txt')

    if not os.path.exists(requirements_path):
        _log(f"✗ requirements.txt не найден: {requirements_path}")
        return False

    _log("Запускаю установку зависимостей...")
    success = _pip_install(['-r', requirements_path], progress_cb)
    if success:
        _log("✓ Установка завершена успешно")
        check2 = check_dependencies(requirements_path, check_sd, progress_cb)
        if check2['need_install']:
            _log(f"⚠ После установки всё ещё не хватает: "
                  f"missing={check2['missing']}, outdated={check2['outdated']}")
            return False
        return True
    else:
        _log("✗ Ошибка установки")
        return False


def ensure_dependencies(requirements_path: Optional[str] = None,
                         check_sd: bool = True,
                         progress_cb: Optional[Callable[[str], None]] = None,
                         interactive: bool = False) -> bool:
    def _log(msg: str):
        logger.info("[updater] %s", msg)
        if progress_cb:
            progress_cb(msg)

    _log("=" * 60)
    _log("Проверка зависимостей приложения")
    _log("=" * 60)

    check = check_dependencies(requirements_path, check_sd, progress_cb)
    if not check['need_install']:
        _log("✓ Все зависимости готовы")
        return True

    missing = check['missing']
    outdated = [(p, inst, tgt) for p, inst, tgt in check['outdated']]

    if interactive:
        try:
            import tkinter as tk
            from tkinter import messagebox
            root = tk.Tk()
            root.withdraw()
            msg_parts = []
            if missing:
                msg_parts.append(f"Отсутствуют ({len(missing)}):\n  " +
                                  "\n  ".join(missing))
            if outdated:
                msg_parts.append("Устарели (" + str(len(outdated)) + "):\n  " +
                                  "\n  ".join(f"{p}: {i} → {t}" for p, i, t in outdated))
            msg = ("Приложение требует обновления зависимостей.\n\n" +
                    "\n\n".join(msg_parts) +
                    "\n\nУстановить сейчас? (нужен интернет, ~2 ГБ для SD)")
            result = messagebox.askyesno("Обновление зависимостей", msg, icon='question')
            root.destroy()
            if not result:
                _log("✗ Пользователь отказался от обновления")
                return False
        except Exception as e:
            _log(f"Не удалось показать messagebox ({e}), продолжаю в неинтерактивном режиме")

    _log("Начинаю установку...")
    success = update_dependencies(requirements_path, check_sd, progress_cb)
    if success:
        _log("✓ Зависимости обновлены")
        return True
    else:
        _log("✗ Не удалось обновить зависимости")
        return False


if __name__ == '__main__':
    import argparse
    logging.basicConfig(level=logging.INFO,
                         format='%(asctime)s [%(levelname)s] %(message)s')
    ap = argparse.ArgumentParser(description='Проверка и обновление зависимостей')
    ap.add_argument('--requirements', default=None, help='путь к requirements.txt')
    ap.add_argument('--no-sd', action='store_true', help='не проверять SD-пакеты')
    ap.add_argument('--check-only', action='store_true', help='только проверить, не устанавливать')
    args = ap.parse_args()

    def progress(msg):
        print(f"  {msg}")

    if args.check_only:
        result = check_dependencies(args.requirements, check_sd=not args.no_sd, progress_cb=progress)
        print(f"\nИтог: missing={len(result['missing'])}, "
              f"outdated={len(result['outdated'])}, ok={len(result['ok'])}")
        sys.exit(0 if not result['need_install'] else 1)
    else:
        success = ensure_dependencies(args.requirements, check_sd=not args.no_sd,
                                       progress_cb=progress, interactive=False)
        sys.exit(0 if success else 1)
