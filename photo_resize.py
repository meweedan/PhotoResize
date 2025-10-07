#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import io
import os
import sys
import threading
from math import sqrt
from pathlib import Path
from typing import List, Tuple, Optional

from PIL import Image, ImageOps, ImageFile
ImageFile.LOAD_TRUNCATED_IMAGES = True  # be tolerant to slightly broken files

# ---- GUI (Tkinter) ----
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

APP_NAME = "ImageShrinker"
DEFAULT_TARGET_MB = 1.0
SUPPORTED_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff", ".gif"}

def human(n: int) -> str:
    for unit in ["B", "KB", "MB"]:
        if n < 1024 or unit == "MB":
            return f"{n:.2f} {unit}" if unit != "B" else f"{n} {unit}"
        n /= 1024
    return f"{n:.2f} MB"

def list_images_in_folder(folder: Path) -> List[Path]:
    return [p for p in folder.rglob("*") if p.suffix.lower() in SUPPORTED_EXTS and p.is_file()]

def has_alpha(img: Image.Image) -> bool:
    return img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info)

def exif_safe(img: Image.Image) -> Image.Image:
    # auto-rotate per EXIF and drop icc/huge exif if problematic
    try:
        img = ImageOps.exif_transpose(img)
    except Exception:
        pass
    return img

def save_jpeg_to_bytes(img: Image.Image, quality: int, subsampling="keep", progressive=True, optimize=True) -> bytes:
    buf = io.BytesIO()
    params = dict(format="JPEG", quality=quality, progressive=progressive, optimize=optimize)
    if subsampling != "keep":
        params["subsampling"] = subsampling
    img.convert("RGB").save(buf, **params)
    return buf.getvalue()

def save_png_to_bytes(img: Image.Image, optimize=True, palette=True) -> bytes:
    buf = io.BytesIO()
    data = img
    # Quantize (palette) for big savings on many images while preserving transparency
    if palette and img.mode not in ("P",):
        try:
            data = img.convert("RGBA") if has_alpha(img) else img.convert("RGB")
            data = data.quantize(method=Image.Quantize.MEDIANCUT, kmeans=0)  # PIL picks a good palette
        except Exception:
            data = img
    data.save(buf, format="PNG", optimize=optimize)
    return buf.getvalue()

def save_webp_to_bytes(img: Image.Image, quality: int, lossless=False) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="WEBP", quality=quality, lossless=lossless, method=6)
    return buf.getvalue()

def try_lossless_then_lossy(img: Image.Image, fmt: str, target_bytes: int) -> Tuple[bytes, str]:
    """
    Returns (bytes, ext) meeting target if possible.
    Strategy:
      1) Keep original format try-optimized (PNG optimize / JPEG optimize / WEBP lossless)
      2) For JPEG/WEBP: descend quality
      3) As last resort, resize and iterate.
    """
    fmt = fmt.upper()
    # Start with original format best-effort
    try:
        if fmt in ("JPEG", "JPG"):
            b = save_jpeg_to_bytes(img, quality=95)
            if len(b) <= target_bytes:
                return b, ".jpg"
        elif fmt == "PNG":
            b = save_png_to_bytes(img, optimize=True, palette=True)
            if len(b) <= target_bytes:
                return b, ".png"
        elif fmt == "WEBP":
            # try lossless first (great for graphics); if too big we’ll try lossy path
            b = save_webp_to_bytes(img, quality=100, lossless=True)
            if len(b) <= target_bytes:
                return b, ".webp"
        else:
            # Fallback: try original save (often BMP/TIFF will be huge)
            buf = io.BytesIO()
            img.save(buf, format=fmt)
            b = buf.getvalue()
            if len(b) <= target_bytes:
                return b, f".{fmt.lower()}"
    except Exception:
        pass

    # If still too large, choose best lossy container:
    # - Keep PNG if transparency (or fallback to WEBP for better compression with alpha)
    # - Otherwise prefer JPEG (broad compatibility) and only resize if needed
    transparent = has_alpha(img)
    prefer_webp = transparent  # WEBP handles alpha + high savings
    if prefer_webp:
        # Descend WEBP quality first
        for q in range(95, 59, -5):
            try:
                b = save_webp_to_bytes(img, quality=q, lossless=False)
                if len(b) <= target_bytes:
                    return b, ".webp"
            except Exception:
                continue
    else:
        # Descend JPEG quality first
        for q in range(95, 59, -5):
            try:
                b = save_jpeg_to_bytes(img, quality=q, subsampling="keep")
                if len(b) <= target_bytes:
                    return b, ".jpg"
            except Exception:
                continue

    # If still too big, we’ll resize progressively + try again
    return b, (".webp" if prefer_webp else ".jpg")  # return last attempt (likely > target); caller will resize

def shrink_to_target(img: Image.Image, target_bytes: int, min_size=(640, 640)) -> Tuple[bytes, str]:
    """
    Iteratively compress (quality) then resize until <= target_bytes or we hit min_size.
    Keeps aspect ratio, uses LANCZOS for downscale quality.
    Returns (bytes, ext)
    """
    img = exif_safe(img)
    attempt, ext = try_lossless_then_lossy(img, img.format or "JPEG", target_bytes)
    if len(attempt) <= target_bytes:
        return attempt, ext

    # Begin resizing loop. Estimate new size by sqrt rule of thumb.
    last = attempt
    cur = img
    while True:
        w, h = cur.size
        if w <= min_size[0] or h <= min_size[1]:
            # give one last encode (already did) and bail
            return last, ext

        # scale factor based on bytes ratio
        factor = sqrt(target_bytes / max(1, len(last))) * 0.98
        factor = min(factor, 0.95)  # avoid upscaling
        if factor <= 0.5:
            # if way too big, be more aggressive but not brutal
            factor = 0.6

        new_w, new_h = max(min_size[0], int(w * factor)), max(min_size[1], int(h * factor))
        if new_w >= w or new_h >= h:
            # Not getting smaller; force small decrement
            new_w, new_h = int(w * 0.9), int(h * 0.9)

        cur = cur.resize((new_w, new_h), Image.LANCZOS)
        # After resize, try again: choose container by transparency
        if has_alpha(cur):
            best = None
            for q in range(95, 59, -5):
                try:
                    cand = save_webp_to_bytes(cur, quality=q, lossless=False)
                    if best is None or len(cand) < len(best):
                        best = cand
                    if len(cand) <= target_bytes:
                        return cand, ".webp"
                except Exception:
                    continue
            last = best if best else last
            ext = ".webp"
        else:
            best = None
            for q in range(95, 59, -5):
                try:
                    cand = save_jpeg_to_bytes(cur, quality=q)
                    if best is None or len(cand) < len(best):
                        best = cand
                    if len(cand) <= target_bytes:
                        return cand, ".jpg"
                except Exception:
                    continue
            last = best if best else last
            ext = ".jpg"

        if last and len(last) <= target_bytes:
            return last, ext

def process_one(src: Path, dst_dir: Path, target_mb: float) -> Tuple[Optional[Path], str]:
    target_bytes = int(target_mb * 1024 * 1024)
    try:
        with Image.open(src) as im:
            out_bytes, ext = shrink_to_target(im, target_bytes)
        # Preserve base name, change extension if we changed container
        out_name = src.stem + ext
        out_path = dst_dir / out_name
        out_path.write_bytes(out_bytes)
        return out_path, f"OK: {src.name} → {out_name} ({human(len(out_bytes))})"
    except Exception as e:
        return None, f"ERR: {src.name} — {e}"

class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(APP_NAME)
        self.geometry("720x540")
        self.minsize(680, 520)

        # State
        self.files: List[Path] = []
        self.output_dir: Optional[Path] = None
        self.worker: Optional[threading.Thread] = None
        self.stop_flag = threading.Event()

        # UI
        self._build_ui()

    def _build_ui(self):
        pad = 10

        # Top controls
        frm_top = ttk.Frame(self)
        frm_top.pack(fill="x", padx=pad, pady=pad)

        ttk.Button(frm_top, text="Add Images…", command=self.add_files).pack(side="left")
        ttk.Button(frm_top, text="Add Folder…", command=self.add_folder).pack(side="left", padx=6)
        ttk.Button(frm_top, text="Clear List", command=self.clear_list).pack(side="left", padx=6)

        ttk.Label(frm_top, text="Target size (MB):").pack(side="left", padx=(18, 6))
        self.var_target = tk.StringVar(value=str(DEFAULT_TARGET_MB))
        e_target = ttk.Entry(frm_top, width=6, textvariable=self.var_target)
        e_target.pack(side="left")

        ttk.Button(frm_top, text="Choose Output…", command=self.choose_output).pack(side="right")

        # Middle: listbox + log
        paned = ttk.Panedwindow(self, orient="vertical")
        paned.pack(fill="both", expand=True, padx=pad, pady=(0, pad))

        # Files list
        frm_files = ttk.Labelframe(paned, text="Files to process")
        self.lb = tk.Listbox(frm_files, selectmode="extended")
        self.lb.pack(fill="both", expand=True, padx=8, pady=6)
        paned.add(frm_files, weight=3)

        # Log
        frm_log = ttk.Labelframe(paned, text="Log")
        self.txt = tk.Text(frm_log, height=10)
        self.txt.pack(fill="both", expand=True, padx=8, pady=6)
        paned.add(frm_log, weight=2)

        # Bottom: progress + actions
        frm_bottom = ttk.Frame(self)
        frm_bottom.pack(fill="x", padx=pad, pady=(0, pad))

        self.pb = ttk.Progressbar(frm_bottom, mode="determinate")
        self.pb.pack(fill="x", expand=True, side="left")

        self.btn_start = ttk.Button(frm_bottom, text="Start", command=self.start)
        self.btn_start.pack(side="left", padx=8)
        self.btn_stop = ttk.Button(frm_bottom, text="Stop", command=self.stop, state="disabled")
        self.btn_stop.pack(side="left")

        ttk.Label(self, text="Tip: You can change the target size above (e.g., 0.8 for 800 KB).").pack(padx=pad, pady=(0, pad))

    def add_files(self):
        paths = filedialog.askopenfilenames(title="Select images", filetypes=[("Images", "*.jpg;*.jpeg;*.png;*.webp;*.bmp;*.tif;*.tiff;*.gif")])
        for p in paths:
            path = Path(p)
            if path.suffix.lower() in SUPPORTED_EXTS:
                self.files.append(path)
                self.lb.insert("end", str(path))
        self._status(f"Added {len(paths)} images.")

    def add_folder(self):
        folder = filedialog.askdirectory(title="Select folder")
        if not folder:
            return
        imgs = list_images_in_folder(Path(folder))
        for p in imgs:
            self.files.append(p)
            self.lb.insert("end", str(p))
        self._status(f"Added {len(imgs)} images from folder.")

    def clear_list(self):
        self.files.clear()
        self.lb.delete(0, "end")

    def choose_output(self):
        d = filedialog.askdirectory(title="Choose output directory")
        if d:
            self.output_dir = Path(d)
            self._status(f"Output: {self.output_dir}")

    def start(self):
        if self.worker and self.worker.is_alive():
            return
        if not self.files:
            messagebox.showwarning(APP_NAME, "No files to process.")
            return

        try:
            target = float(self.var_target.get())
            if target <= 0:
                raise ValueError
        except Exception:
            messagebox.showerror(APP_NAME, "Target size must be a positive number (MB).")
            return

        out = self.output_dir or Path.cwd() / "output"
        out.mkdir(parents=True, exist_ok=True)

        self.stop_flag.clear()
        self.pb.configure(maximum=len(self.files), value=0)
        self.btn_start.configure(state="disabled")
        self.btn_stop.configure(state="normal")
        self._status(f"Processing {len(self.files)} images → {out}")

        def work():
            done = 0
            for p in self.files:
                if self.stop_flag.is_set():
                    break
                out_path, msg = process_one(p, out, target)
                self._status(msg)
                done += 1
                self.pb.configure(value=done)
            self.btn_start.configure(state="normal")
            self.btn_stop.configure(state="disabled")
            self._status("Finished." if not self.stop_flag.is_set() else "Stopped by user.")

        self.worker = threading.Thread(target=work, daemon=True)
        self.worker.start()

    def stop(self):
        self.stop_flag.set()

    def _status(self, s: str):
        self.txt.insert("end", s + "\n")
        self.txt.see("end")

def main():
    # macOS app bundle fix: ensure relative paths work
    if getattr(sys, "frozen", False) and sys.platform == "darwin":
        os.chdir(Path(sys.executable).resolve().parent)

    app = App()
    app.mainloop()

if __name__ == "__main__":
    main()
