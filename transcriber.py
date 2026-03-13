#!/usr/bin/env python3
"""DiaryDecoder — 手写日记图片批量转写（思源笔记集成）"""

import os
import json
import base64
import threading
import tkinter as tk
from tkinter import ttk, scrolledtext, messagebox, filedialog
from pathlib import Path

import requests
from PIL import Image, ImageTk

# ─── API Key 加载 ─────────────────────────────────────────────────────────────

def load_api_key(name: str) -> str:
    """env var → ~/.api_keys.json"""
    env_name = name.upper().replace("-", "_").replace(" ", "_")
    if val := os.environ.get(env_name, "").strip():
        return val
    global_keys = Path.home() / ".api_keys.json"
    if global_keys.exists():
        try:
            keys = json.loads(global_keys.read_text(encoding="utf-8"))
            short = name.lower().replace("_api_key", "").replace("_key", "")
            if val := keys.get(name, keys.get(env_name, keys.get(short, ""))):
                return val.strip()
        except Exception:
            pass
    return ""

# ─── 持久化配置 ───────────────────────────────────────────────────────────────

CONFIG_FILE = Path(__file__).parent / "config.json"

def load_config() -> dict:
    if CONFIG_FILE.exists():
        try:
            return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}

def save_config(cfg: dict):
    CONFIG_FILE.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8")

# ─── 常量 ─────────────────────────────────────────────────────────────────────

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".tiff"}

DEFAULT_MODELS = [
    "anthropic/claude-3.5-sonnet",
    "anthropic/claude-3-haiku",
    "google/gemini-2.0-flash-001",
    "google/gemini-2.5-pro-preview-03-25",
    "openai/gpt-4o",
    "openai/gpt-4o-mini",
]

TRANSCRIBE_PROMPT = (
    "请将图片中的手写文字完整转写为简体中文。"
    "只输出转写的文字内容，不要添加任何解释、说明或标题。"
    "保持原文的段落结构，使用换行区分段落。"
)

# ─── 核心函数 ─────────────────────────────────────────────────────────────────

def get_pending_images(directory: str) -> list[Path]:
    d = Path(directory)
    if not d.is_dir():
        return []
    return [f for f in sorted(d.iterdir())
            if f.suffix.lower() in IMAGE_EXTS and not f.with_suffix(".txt").exists()]

def transcribe_image(image_path: Path, api_key: str, model: str) -> str:
    raw  = image_path.read_bytes()
    b64  = base64.b64encode(raw).decode()
    mime = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
            ".gif": "image/gif",  ".webp": "image/webp"}.get(
                image_path.suffix.lower(), "image/jpeg")
    resp = requests.post(
        "https://openrouter.ai/api/v1/chat/completions",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json={"model": model, "messages": [{"role": "user", "content": [
            {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}},
            {"type": "text", "text": TRANSCRIBE_PROMPT},
        ]}]},
        timeout=90,
    )
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"].strip()

def siyuan_find_blocks(url: str, token: str, image_filename: str) -> list[dict]:
    sql = (f"SELECT id, hpath, markdown FROM blocks "
           f"WHERE markdown LIKE '%{image_filename}%' ORDER BY updated DESC")
    try:
        r = requests.post(f"{url}/api/query/sql",
                          headers={"Authorization": f"Token {token}",
                                   "Content-Type": "application/json"},
                          json={"stmt": sql}, timeout=10)
        r.raise_for_status()
        data = r.json()
        if data.get("code") == 0:
            return data.get("data") or []
    except Exception as e:
        print(f"[SiYuan SQL] {e}")
    return []

def siyuan_insert_before(url: str, token: str, block_id: str, text: str) -> bool:
    hdrs = {"Authorization": f"Token {token}", "Content-Type": "application/json"}
    check_sql = (
        f"SELECT id, content FROM blocks WHERE "
        f"parent_id = (SELECT parent_id FROM blocks WHERE id='{block_id}') "
        f"AND sort < (SELECT sort FROM blocks WHERE id='{block_id}') "
        f"ORDER BY sort DESC LIMIT 1"
    )
    try:
        r = requests.post(f"{url}/api/query/sql", headers=hdrs,
                          json={"stmt": check_sql}, timeout=10)
        siblings = r.json().get("data") or []
        if siblings and siblings[0].get("content", "").strip() == text.strip():
            return True
    except Exception:
        pass
    try:
        r = requests.post(f"{url}/api/block/insertBlock", headers=hdrs,
                          json={"data": text, "dataType": "markdown", "nextID": block_id},
                          timeout=10)
        return r.json().get("code") == 0
    except Exception as e:
        print(f"[SiYuan insert] {e}")
    return False

# ─── 设置对话框 ───────────────────────────────────────────────────────────────

class SettingsDialog(tk.Toplevel):
    def __init__(self, parent, cfg: dict):
        super().__init__(parent)
        self.title("设置")
        self.resizable(False, False)
        self.grab_set()
        self.result = None

        frame = ttk.Frame(self, padding=16)
        frame.pack(fill=tk.BOTH)

        def folder_row(label, key, default=""):
            ttk.Label(frame, text=label).pack(anchor=tk.W)
            f = ttk.Frame(frame)
            f.pack(fill=tk.X, pady=(2, 10))
            var = tk.StringVar(value=cfg.get(key, default))
            ttk.Entry(f, textvariable=var, width=56).pack(side=tk.LEFT, fill=tk.X, expand=True)
            def browse(v=var):
                d = filedialog.askdirectory(title=label)
                if d:
                    v.set(d)
            ttk.Button(f, text="浏览…", command=browse, width=6).pack(side=tk.LEFT, padx=(4, 0))
            return var

        def text_row(label, key, default="", secret=False):
            ttk.Label(frame, text=label).pack(anchor=tk.W)
            var = tk.StringVar(value=cfg.get(key, default))
            ttk.Entry(frame, textvariable=var, show="*" if secret else "",
                      width=62).pack(fill=tk.X, pady=(2, 10))
            return var

        self._assets   = folder_row("图片资产目录（assets_dir）", "assets_dir")
        self._sy_url   = text_row("思源 API 地址（siyuan_url）", "siyuan_url",
                                  default="http://127.0.0.1:6806")
        self._sy_token = text_row("思源 Token（siyuan_token）", "siyuan_token", secret=True)

        bot = ttk.Frame(self, padding=(16, 0, 16, 16))
        bot.pack(fill=tk.X)
        ttk.Button(bot, text="取消", command=self.destroy).pack(side=tk.RIGHT, padx=4)
        ttk.Button(bot, text="保存", command=self._save).pack(side=tk.RIGHT)

        self.bind("<Return>", lambda _: self._save())
        self.bind("<Escape>", lambda _: self.destroy())
        self.transient(parent)
        self.update_idletasks()
        x = parent.winfo_rootx() + (parent.winfo_width()  - self.winfo_width())  // 2
        y = parent.winfo_rooty() + (parent.winfo_height() - self.winfo_height()) // 2
        self.geometry(f"+{x}+{y}")

    def _save(self):
        self.result = {
            "assets_dir":   self._assets.get().strip(),
            "siyuan_url":   self._sy_url.get().strip().rstrip("/"),
            "siyuan_token": self._sy_token.get().strip(),
        }
        self.destroy()

# ─── GUI ──────────────────────────────────────────────────────────────────────

class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("DiaryDecoder 手写转写")
        self.geometry("1400x860")
        self.minsize(900, 600)

        self.cfg = load_config()
        self.images: list[Path] = []
        self.idx = 0
        self._photo = None
        self._transcribing = False
        self._auto = False

        self._build_ui()
        self.after(100, self._startup)

    # ── UI ────────────────────────────────────────────────────────────────────

    def _build_ui(self):
        top = ttk.Frame(self, padding=(8, 4))
        top.pack(fill=tk.X)

        ttk.Label(top, text="OpenRouter Key:").pack(side=tk.LEFT)
        self._api_key = tk.StringVar(
            value=self.cfg.get("api_key", "") or load_api_key("openrouter"))
        ttk.Entry(top, textvariable=self._api_key, width=48,
                  show="*").pack(side=tk.LEFT, padx=(2, 10))

        ttk.Label(top, text="模型:").pack(side=tk.LEFT)
        self._model = tk.StringVar(value=self.cfg.get("model", DEFAULT_MODELS[0]))
        cb = ttk.Combobox(top, textvariable=self._model, values=DEFAULT_MODELS, width=38)
        cb.pack(side=tk.LEFT, padx=(2, 10))

        ttk.Button(top, text="⚙ 设置", command=self._open_settings).pack(side=tk.LEFT, padx=4)

        self._progress = tk.StringVar(value="—")
        ttk.Label(top, textvariable=self._progress,
                  foreground="#555").pack(side=tk.RIGHT, padx=8)

        paned = ttk.PanedWindow(self, orient=tk.HORIZONTAL)
        paned.pack(fill=tk.BOTH, expand=True, padx=8, pady=4)

        lf = ttk.LabelFrame(paned, text="图片预览")
        paned.add(lf, weight=1)
        self._img_canvas = tk.Canvas(lf, bg="#2b2b2b", cursor="crosshair")
        self._img_canvas.pack(fill=tk.BOTH, expand=True)
        self._img_name = ttk.Label(lf, text="", foreground="#777", font=("Consolas", 9))
        self._img_name.pack(pady=(2, 4))

        rf = ttk.LabelFrame(paned, text="转写文字（可直接编辑）")
        paned.add(rf, weight=1)
        self._text = scrolledtext.ScrolledText(
            rf, font=("Microsoft YaHei", 13), wrap=tk.WORD, undo=True)
        self._text.pack(fill=tk.BOTH, expand=True)
        self._status = tk.StringVar(value="")
        ttk.Label(rf, textvariable=self._status, foreground="#0066cc",
                  wraplength=600, justify=tk.LEFT).pack(anchor=tk.W, padx=4, pady=2)

        bot = ttk.Frame(self, padding=(8, 4))
        bot.pack(fill=tk.X)

        self._btn_prev    = ttk.Button(bot, text="← 上一张",  command=self._prev)
        self._btn_prev.pack(side=tk.LEFT, padx=4)
        self._btn_retrans = ttk.Button(bot, text="开始转写",   command=self._start_transcription)
        self._btn_retrans.pack(side=tk.LEFT, padx=4)
        self._btn_skip    = ttk.Button(bot, text="跳过",       command=self._skip)
        self._btn_skip.pack(side=tk.LEFT, padx=4)
        self._btn_auto    = ttk.Button(bot, text="▶ 自动运行", command=self._toggle_auto,
                                       style="Auto.TButton")
        self._btn_auto.pack(side=tk.LEFT, padx=12)

        self._btn_approve = ttk.Button(bot, text="✓ 批准 → 保存 + 嵌入思源",
                                       command=self._approve, style="Approve.TButton")
        self._btn_approve.pack(side=tk.RIGHT, padx=4)
        self._btn_txt_only = ttk.Button(bot, text="✓ 仅保存 txt",
                                        command=self._approve_txt_only)
        self._btn_txt_only.pack(side=tk.RIGHT, padx=4)

        s = ttk.Style()
        s.configure("Approve.TButton", foreground="darkgreen", font=("", 10, "bold"))
        s.configure("Auto.TButton",    foreground="navy",      font=("", 10, "bold"))
        s.configure("AutoOn.TButton",  foreground="white",     background="#c0392b",
                    font=("", 10, "bold"))

        self.bind("<Control-Return>", lambda _: self._approve())
        self.bind("<Control-s>",      lambda _: self._approve_txt_only())
        self.bind("<Control-Right>",  lambda _: self._skip())
        self.bind("<Control-Left>",   lambda _: self._prev())
        self.bind("<Configure>",      self._on_resize)

    # ── 设置 ──────────────────────────────────────────────────────────────────

    def _open_settings(self):
        dlg = SettingsDialog(self, self.cfg)
        self.wait_window(dlg)
        if dlg.result:
            self.cfg.update(dlg.result)
            save_config(self.cfg)
            self._status.set("设置已保存。")

    def _check_config(self) -> bool:
        """若关键配置缺失则弹出设置对话框，返回是否已配置。"""
        if not self.cfg.get("assets_dir"):
            messagebox.showinfo("首次使用", "请先配置图片目录和思源笔记连接信息。")
            self._open_settings()
        return bool(self.cfg.get("assets_dir"))

    # ── 启动 ──────────────────────────────────────────────────────────────────

    def _startup(self):
        if not self._check_config():
            return
        assets_dir = self.cfg["assets_dir"]
        images = get_pending_images(assets_dir)
        total  = len(images)
        if total == 0:
            messagebox.showinfo("完成", "assets 目录中没有待转写的图片！")
            return
        if not messagebox.askokcancel(
            "确认开始",
            f"找到 {total} 张尚无转写文字的图片。\n\n目录：{assets_dir}\n\n"
            "点击「确定」加载第一张并转写测试。"
        ):
            self.destroy()
            return
        self.images = images
        self._update_progress()
        self._load_current()

    # ── 图片加载 ──────────────────────────────────────────────────────────────

    def _load_current(self):
        if self.idx >= len(self.images):
            self._status.set("🎉 所有图片已处理完毕！")
            self._btn_approve.config(state=tk.DISABLED)
            self._btn_txt_only.config(state=tk.DISABLED)
            return
        path = self.images[self.idx]
        self._img_name.config(text=path.name)
        self._update_progress()
        self._set_buttons_state(True)
        self._display_image(path)
        self._text.delete("1.0", tk.END)
        txt = path.with_suffix(".txt")
        if txt.exists():
            self._text.insert("1.0", txt.read_text(encoding="utf-8"))
            self._status.set(f"已有 {txt.name}，可直接修改后批准。")
            if self._auto:
                self.after(400, self._approve)
        elif self._auto:
            self._start_transcription()
        else:
            self._status.set("查看图片后，点击「开始转写」或「跳过」。")

    def _display_image(self, path: Path):
        try:
            self._orig_img = Image.open(path)
            self._fit_image()
        except Exception as e:
            self._img_canvas.delete("all")
            self._img_canvas.create_text(10, 10, text=f"无法显示: {e}",
                                         fill="red", anchor=tk.NW)

    def _fit_image(self):
        if not hasattr(self, "_orig_img") or self._orig_img is None:
            return
        cw, ch = self._img_canvas.winfo_width(), self._img_canvas.winfo_height()
        if cw < 10 or ch < 10:
            return
        img = self._orig_img.copy()
        img.thumbnail((cw, ch), Image.LANCZOS)
        self._photo = ImageTk.PhotoImage(img)
        self._img_canvas.delete("all")
        self._img_canvas.create_image(cw // 2, ch // 2, image=self._photo, anchor=tk.CENTER)

    def _on_resize(self, _event):
        self.after_idle(self._fit_image)

    # ── 转写 ──────────────────────────────────────────────────────────────────

    def _start_transcription(self):
        api_key = self._api_key.get().strip()
        if not api_key:
            messagebox.showerror("缺少 API Key", "请在顶部输入 OpenRouter API Key")
            return
        if self._transcribing:
            return
        self._transcribing = True
        self._set_buttons_state(False)
        self._status.set("调用 AI 转写中，请稍候……")
        self._text.delete("1.0", tk.END)
        path  = self.images[self.idx]
        model = self._model.get()

        def worker():
            try:
                text = transcribe_image(path, api_key, model)
                self.after(0, lambda t=text: self._on_done(t))
            except Exception as e:
                self.after(0, lambda err=str(e): self._on_error(err))

        threading.Thread(target=worker, daemon=True).start()

    def _on_done(self, text: str):
        self._transcribing = False
        self._text.delete("1.0", tk.END)
        self._text.insert("1.0", text)
        self._set_buttons_state(True)
        self.cfg["api_key"] = self._api_key.get().strip()
        self.cfg["model"]   = self._model.get()
        save_config(self.cfg)
        if self._auto:
            self._status.set("自动模式：转写完成，正在保存…")
            self.after(600, self._approve)
        else:
            self._status.set("转写完成，请检查后批准。")

    def _on_error(self, err: str):
        self._transcribing = False
        self._set_buttons_state(True)
        self._status.set(f"转写失败：{err}")
        messagebox.showerror("转写失败", err)

    # ── 审批 ──────────────────────────────────────────────────────────────────

    def _approve(self):
        text = self._text.get("1.0", tk.END).strip()
        if not text:
            messagebox.showwarning("空内容", "转写文字为空，请先转写或手动输入。")
            return
        path = self.images[self.idx]
        path.with_suffix(".txt").write_text(text, encoding="utf-8")

        url   = self.cfg.get("siyuan_url",   "")
        token = self.cfg.get("siyuan_token", "")
        blocks  = siyuan_find_blocks(url, token, path.name) if url and token else []
        sy_msgs = []
        if not url or not token:
            sy_msgs.append("思源未配置，已跳过")
        elif not blocks:
            sy_msgs.append("未在思源笔记中找到此图片的引用")
        else:
            for blk in blocks:
                ok = siyuan_insert_before(url, token, blk["id"], text)
                sy_msgs.append(("✓" if ok else "✗") + f" {blk['hpath']}")

        self._status.set(f"已保存 {path.stem}.txt  |  思源：{'；'.join(sy_msgs)}")
        self.after(1800, self._next)

    def _approve_txt_only(self):
        text = self._text.get("1.0", tk.END).strip()
        if not text:
            messagebox.showwarning("空内容", "转写文字为空。")
            return
        path = self.images[self.idx]
        path.with_suffix(".txt").write_text(text, encoding="utf-8")
        self._status.set(f"已保存 {path.stem}.txt（未嵌入思源）")
        self.after(1000, self._next)

    # ── 导航 ──────────────────────────────────────────────────────────────────

    def _next(self):
        self.idx += 1
        self._load_current()

    def _prev(self):
        if self.idx > 0:
            self.idx -= 1
            self._load_current()

    def _skip(self):
        self._next()

    # ── 自动模式 ──────────────────────────────────────────────────────────────

    def _toggle_auto(self):
        self._auto = not self._auto
        if self._auto:
            self._btn_auto.config(text="■ 停止自动", style="AutoOn.TButton")
            self._status.set("自动模式已开启，将依次转写并保存所有图片。")
            if not self._transcribing and not self._text.get("1.0", tk.END).strip():
                self._start_transcription()
        else:
            self._btn_auto.config(text="▶ 自动运行", style="Auto.TButton")
            self._status.set("自动模式已停止。")

    # ── 辅助 ──────────────────────────────────────────────────────────────────

    def _update_progress(self):
        total = len(self.images)
        cur   = self.idx + 1 if self.idx < total else total
        self._progress.set(f"{cur} / {total}  待转写")

    def _set_buttons_state(self, enabled: bool):
        state = tk.NORMAL if enabled else tk.DISABLED
        for btn in (self._btn_retrans, self._btn_skip, self._btn_approve,
                    self._btn_txt_only, self._btn_prev, self._btn_auto):
            btn.config(state=state)


# ─── 入口 ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    App().mainloop()
