import tkinter as tk
from tkinter import colorchooser, messagebox, simpledialog, ttk
import requests
import base64
import json
import io
import re
import sys
import os
import threading
import hashlib
import pyautogui
from embedded_config import get_icon_png_base64

def get_app_dir():
    if globals().get("__compiled__") or getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.argv[0]))
    return os.path.dirname(os.path.abspath(__file__))


APP_DIR = get_app_dir()
CONFIG_PATH = os.path.join(APP_DIR, "config.json")
WINDOW_ICON = None
WINDOW_ICON_DATA = None


def apply_window_icon(window):
    global WINDOW_ICON
    icon_data = _get_embedded_window_icon_data()
    if not icon_data:
        return
    try:
        if WINDOW_ICON is None:
            WINDOW_ICON = tk.PhotoImage(data=icon_data)
        window._fvnt_icon_photo = WINDOW_ICON
        window.iconphoto(False, WINDOW_ICON)
    except Exception:
        pass


def _get_embedded_window_icon_data():
    global WINDOW_ICON_DATA
    if WINDOW_ICON_DATA is not None:
        return WINDOW_ICON_DATA

    try:
        WINDOW_ICON_DATA = get_icon_png_base64()
        return WINDOW_ICON_DATA
    except Exception:
        return None


# ============ 配置加载 ============
def load_config():
    if not os.path.exists(CONFIG_PATH):
        raise FileNotFoundError(f"未找到配置文件: {CONFIG_PATH}")
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def save_config(config):
    temp_path = CONFIG_PATH + ".tmp"
    with open(temp_path, "w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False, indent=2)
        f.write("\n")
    os.replace(temp_path, CONFIG_PATH)


DEFAULT_APP_SETTINGS = {
    "default_language": "英语 → 中文",
    "default_interval": 2,
    "topmost": True,
    "ocr_font_size": 10,
    "highlight_foreground": "#ffffff",
    "highlight_background": "#8a5a00",
}


def get_app_settings(config=None):
    source = config if config is not None else CONFIG
    settings = dict(DEFAULT_APP_SETTINGS)
    raw_settings = source.get("app_settings", {})
    if isinstance(raw_settings, dict):
        settings.update(raw_settings)
    return settings


def _is_valid_hex_color(value):
    return bool(re.fullmatch(r'#[0-9a-fA-F]{6}', str(value).strip()))

CONFIG = load_config()
CONFIG["app_settings"] = get_app_settings(CONFIG)

# ============ 百度 Access Token 获取 ============
def get_access_token(api_key, secret_key):
    url = "https://aip.baidubce.com/oauth/2.0/token"
    params = {
        "grant_type": "client_credentials",
        "client_id": api_key,
        "client_secret": secret_key,
    }
    resp = requests.post(url, params=params, timeout=10)
    resp.raise_for_status()
    result = resp.json()
    if "access_token" not in result:
        raise RuntimeError(f"获取access_token失败: {result}")
    return result["access_token"]

# ============ 百度 OCR ============
def ocr_recognize(image_bytes):
    cfg = CONFIG["baidu_ocr"]
    token = get_access_token(cfg["api_key"], cfg["secret_key"])
    url = f"https://aip.baidubce.com/rest/2.0/ocr/v1/general_basic?access_token={token}"
    img_b64 = base64.b64encode(image_bytes).decode("utf-8")
    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    data = {"image": img_b64}
    resp = requests.post(url, headers=headers, data=data, timeout=15)
    resp.raise_for_status()
    result = resp.json()
    if "error_code" in result:
        raise RuntimeError(f"OCR识别失败: {result.get('error_msg', result)}")
    words_list = result.get("words_result", [])
    return _merge_ocr_lines(item["words"] for item in words_list)


def _merge_ocr_lines(lines):
    """合并 OCR 结果，默认跳过换行。
    中文/日文等 CJK 文本直接拼接；拉丁文本之间补空格，避免单词粘连。"""
    merged = []
    previous = ""
    for raw_line in lines:
        line = raw_line.strip()
        if not line:
            continue
        if not merged:
            merged.append(line)
            previous = line
            continue

        if _needs_space_between(previous, line):
            merged.append(" ")
        merged.append(line)
        previous = line
    return "".join(merged)


def _needs_space_between(left, right):
    left_char = left[-1]
    right_char = right[0]
    return left_char.isascii() and right_char.isascii() and (
        left_char.isalnum() and right_char.isalnum()
    )

# ============ OCR 文本清理与分词修复 ============
# CJK Unicode 范围
_CJK_RE = re.compile(
    r'[\u2E80-\u9FFF\uF900-\uFAFF\U00020000-\U0002FA1F]'
)
_DICT_NORMALIZE_SKIP_CHARS = {'-', '‐', '‑', '‒', '–', '—', '―'}

class CustomDictionaryState:
    def __init__(self):
        self._lock = threading.RLock()
        self._words = []
        self._regex = None
        self._config_stamp = None
        self.refresh(force=True)

    def _get_config_stamp(self):
        try:
            stat = os.stat(CONFIG_PATH)
        except FileNotFoundError:
            return None
        return (stat.st_mtime_ns, stat.st_size)

    def _normalize_words(self, words):
        normalized = []
        seen = set()
        for item in words:
            word = str(item).strip()
            if not word:
                continue
            key = word.casefold()
            if key in seen:
                continue
            seen.add(key)
            normalized.append(word)
        normalized.sort(key=len, reverse=True)
        return normalized

    def _compile_regex(self, words):
        if not words:
            return None
        escaped = [re.escape(word) for word in words]
        return re.compile('(' + '|'.join(escaped) + ')', re.IGNORECASE)

    def refresh(self, force=False):
        config_stamp = self._get_config_stamp()
        with self._lock:
            if not force and config_stamp == self._config_stamp:
                return False

        try:
            config = load_config()
        except (OSError, ValueError, json.JSONDecodeError):
            return False

        words = self._normalize_words(config.get("custom_dict", []))
        regex = self._compile_regex(words)
        with self._lock:
            self._words = words
            self._regex = regex
            self._config_stamp = config_stamp
        CONFIG["custom_dict"] = list(words)
        return True

    def get_regex(self):
        with self._lock:
            return self._regex

    def add_word(self, word):
        new_word = str(word).strip()
        if not new_word:
            raise ValueError("自定义词不能为空")

        with self._lock:
            config = load_config()
            words = self._normalize_words(config.get("custom_dict", []))
            if new_word.casefold() in {item.casefold() for item in words}:
                return False

            words.append(new_word)
            words = self._normalize_words(words)
            config["custom_dict"] = words
            save_config(config)

            self._words = words
            self._regex = self._compile_regex(words)
            self._config_stamp = self._get_config_stamp()

        CONFIG["custom_dict"] = list(words)
        return True


CUSTOM_DICT_STATE = CustomDictionaryState()

def _is_cjk(ch):
    return bool(_CJK_RE.match(ch))

def clean_ocr_text(text):
    """
    修复 OCR 常见的空格问题：
    1. CJK 字符之间的错误空格 → 移除
    2. 拉丁文本不再执行去空白和自动分词
    3. 仅保留基于自定义词典的匹配修正
    """
    cleaned_text, _ = clean_ocr_text_with_debug(text)
    return cleaned_text


def clean_ocr_text_with_debug(text):
    CUSTOM_DICT_STATE.refresh()
    debug_steps = [
        ("输入文本", text),
        (
            "当前自定义词典",
            "、".join(CONFIG.get("custom_dict", [])) or "(空)"
        )
    ]
    normalized_text = text.replace("\r\n", "\n").replace("\r", "\n")
    debug_steps.append(("统一换行符", normalized_text))
    flattened_text = normalized_text.replace("\n", " ")
    debug_steps.append(("换行转空格", flattened_text))
    cleaned_text, clean_line_steps = _clean_line_with_debug(flattened_text)
    debug_steps.extend(clean_line_steps)
    return cleaned_text, _format_debug_steps(debug_steps)

def _clean_line(line):
    cleaned_line, _ = _clean_line_with_debug(line)
    return cleaned_line


def _clean_line_with_debug(line):
    # 第1步: 移除 CJK 字符之间的空格
    cjk_space_cleaned = re.sub(
        r'(?<=[\u2E80-\u9FFF\uF900-\uFAFF])\s+(?=[\u2E80-\u9FFF\uF900-\uFAFF])',
        '', line
    )
    debug_steps = [("移除 CJK 字符之间的空格", cjk_space_cleaned)]

    # 第2步: 按 CJK / 非CJK 分段处理
    segments = re.split(r'([\u2E80-\u9FFF\uF900-\uFAFF]+)', cjk_space_cleaned)
    debug_steps.append(("CJK / 非 CJK 分段", _format_segments_for_debug(segments)))
    result_parts = []
    segment_debug_entries = []
    for seg in segments:
        if not seg:
            continue
        if _CJK_RE.search(seg):
            # CJK 段落，直接保留（空格已在第1步清除）
            result_parts.append(seg)
            segment_debug_entries.append(
                "CJK 片段直接保留\n" + _indent_debug_block(repr(seg))
            )
        else:
            # 拉丁/数字段落，修复空格问题
            fixed_segment, latin_debug = _fix_latin_segment_with_debug(seg)
            result_parts.append(fixed_segment)
            segment_debug_entries.append(
                "非 CJK 片段处理\n" + _indent_debug_block(latin_debug)
            )
    cleaned_line = ''.join(result_parts)
    debug_steps.append((
        "各分段处理明细",
        "\n\n".join(segment_debug_entries) if segment_debug_entries else "(无)"
    ))
    debug_steps.append(("最终清洗结果", cleaned_line))
    return cleaned_line, debug_steps

def _fix_latin_segment(seg):
    fixed_segment, _ = _fix_latin_segment_with_debug(seg)
    return fixed_segment


def _fix_latin_segment_with_debug(seg):
    """修复拉丁文段落：保留原始空白，仅执行自定义词典匹配修正。"""
    debug_lines = [
        f"原始片段: {seg!r}"
    ]
    fixed_segment, match_details = _apply_custom_dictionary_to_segment(seg)
    debug_lines.append(f"自定义词典命中: {match_details}")
    debug_lines.append(f"输出: {fixed_segment!r}")
    return fixed_segment, '\n'.join(debug_lines)

def _normalize_dict_lookup_text(text):
    normalized_chars = []
    index_map = []
    for index, char in enumerate(text):
        if char.isspace() or char in _DICT_NORMALIZE_SKIP_CHARS:
            continue
        normalized_chars.append(char)
        index_map.append(index)
    return ''.join(normalized_chars), index_map

def _is_hyphen_prefixed_word(text):
    stripped = str(text).strip()
    return bool(stripped) and stripped[0] in _DICT_NORMALIZE_SKIP_CHARS

def _is_valid_hyphen_custom_match(text, start, end, allow_prefix_start=False):
    if start < 0 or end > len(text) or start >= end:
        return False

    left_char = text[start - 1] if start > 0 else ''
    right_char = text[end] if end < len(text) else ''

    left_ok = not left_char or (not left_char.isalpha())
    right_ok = (
        not right_char or
        (not right_char.isalpha()) or
        (allow_prefix_start and start == 0)
    )
    return left_ok and right_ok


def _expand_hyphen_prefixed_match_start(text, start):
    cursor = start
    while cursor > 0 and text[cursor - 1].isspace():
        cursor -= 1
    if cursor > 0 and text[cursor - 1] in _DICT_NORMALIZE_SKIP_CHARS:
        return cursor - 1
    return start


def _needs_space_between_segments(left_text, right_text):
    if not left_text or not right_text:
        return False
    if left_text[-1].isspace() or right_text[0].isspace():
        return False
    return True

def _apply_custom_dictionary_to_segment(text):
    matches = _find_custom_dict_matches(text)
    if not matches:
        return text, "(无)"

    result_parts = []
    match_details = []
    cursor = 0
    for start, end, replacement in matches:
        original_text = text[start:end]
        prefix = text[cursor:start]
        if prefix:
            result_parts.append(prefix)

        adjusted_replacement = replacement
        if _needs_space_between_segments(''.join(result_parts), adjusted_replacement):
            adjusted_replacement = ' ' + adjusted_replacement

        suffix = text[end:]
        if _needs_space_between_segments(adjusted_replacement, suffix):
            adjusted_replacement = adjusted_replacement + ' '

        result_parts.append(adjusted_replacement)
        if adjusted_replacement == original_text:
            match_details.append(
                f"{original_text!r} 命中词典，但无需改写"
            )
        else:
            match_details.append(
                f"{original_text!r} -> {adjusted_replacement!r}"
            )
        cursor = end

    if cursor < len(text):
        result_parts.append(text[cursor:])

    return ''.join(result_parts), '\n'.join(match_details)


def _format_debug_steps(steps):
    lines = []
    for index, (title, value) in enumerate(steps, start=1):
        display_value = value if value else "(空)"
        lines.append(f"步骤 {index}: {title}")
        lines.append(display_value)
        lines.append("")
    return '\n'.join(lines).rstrip()


def _format_segments_for_debug(segments):
    formatted = []
    for index, segment in enumerate(seg for seg in segments if seg):
        segment_type = "CJK" if _CJK_RE.search(segment) else "非 CJK"
        formatted.append(f"[{index}] {segment_type}: {segment!r}")
    return '\n'.join(formatted) if formatted else "(无)"


def _indent_debug_block(text, prefix='    '):
    return '\n'.join(prefix + line for line in text.splitlines())


def _find_custom_dict_matches(text, allow_hyphen_prefix_start=False):
    words = [item for item in CONFIG.get('custom_dict', []) if str(item).strip()]
    if not words or not text:
        return []

    normalized_text, normalized_index_map = _normalize_dict_lookup_text(text)
    if not normalized_text:
        return []

    normalized_words = []
    seen = set()
    for word in words:
        raw_word = str(word).strip()
        normalized, _ = _normalize_dict_lookup_text(raw_word)
        if not normalized:
            continue
        key = (normalized.casefold(), _is_hyphen_prefixed_word(raw_word))
        if key in seen:
            continue
        seen.add(key)
        normalized_words.append((normalized, raw_word, _is_hyphen_prefixed_word(raw_word)))

    normalized_words.sort(key=lambda item: len(item[0]), reverse=True)
    folded_text = normalized_text.casefold()
    occupied = [False] * len(normalized_text)
    matches = []
    for word, raw_word, is_hyphen_prefixed in normalized_words:
        folded_word = word.casefold()
        start = 0
        while True:
            index = folded_text.find(folded_word, start)
            if index == -1:
                break
            end = index + len(word)
            if not any(occupied[index:end]):
                original_start = normalized_index_map[index]
                original_end = normalized_index_map[end - 1] + 1
                if (not is_hyphen_prefixed or
                        _is_valid_hyphen_custom_match(
                            text,
                            original_start,
                            original_end,
                            allow_prefix_start=allow_hyphen_prefix_start
                        )):
                    if is_hyphen_prefixed:
                        original_start = _expand_hyphen_prefixed_match_start(
                            text,
                            original_start
                        )
                    matches.append((original_start, original_end, raw_word))
                    for pos in range(index, end):
                        occupied[pos] = True
            start = index + 1

    matches.sort(key=lambda item: item[0])
    return matches

def _find_custom_dict_spans(text, allow_hyphen_prefix_start=False):
    return [(start, end) for start, end, _ in _find_custom_dict_matches(
        text,
        allow_hyphen_prefix_start=allow_hyphen_prefix_start
    )]

# ============ 百度翻译 ============
def translate_text(text, from_lang="auto", to_lang="zh"):
    if not text.strip():
        return ""
    cfg = CONFIG["baidu_translate"]
    token = get_access_token(cfg["api_key"], cfg["secret_key"])
    url = f"https://aip.baidubce.com/rpc/2.0/mt/texttrans/v1?access_token={token}"
    headers = {"Content-Type": "application/json;charset=utf-8"}
    body = {"q": text, "from": from_lang, "to": to_lang}
    term_ids = cfg.get("term_ids", [])
    if isinstance(term_ids, str):
        term_ids = [item.strip() for item in term_ids.split(",") if item.strip()]
    if term_ids:
        body["termIds"] = ",".join(term_ids)
    resp = requests.post(url, headers=headers, json=body, timeout=15)
    resp.raise_for_status()
    result = resp.json()
    if "error_code" in result:
        raise RuntimeError(f"翻译失败: {result.get('error_msg', result)}")
    trans_result = result.get("result", {}).get("trans_result", [])
    return "\n".join(item["dst"] for item in trans_result)

# ============ 屏幕区域框选（Toplevel，共享主循环）============
class ScreenSelector:
    """全屏半透明覆盖层，使用 Toplevel，不新建 mainloop"""

    def __init__(self, parent_root, on_select):
        """
        on_select(x1, y1, x2, y2): 用户框选完成后的回调，传入屏幕坐标
        """
        self.parent_root = parent_root
        self.on_select = on_select
        self.start_x = 0
        self.start_y = 0
        self.rect_id = None

        self.win = tk.Toplevel(parent_root)
        apply_window_icon(self.win)
        self.win.attributes("-fullscreen", True)
        self.win.attributes("-topmost", True)
        self.win.attributes("-alpha", 0.3)
        self.win.configure(cursor="cross", bg="black")
        self.win.focus_force()
        self.win.protocol("WM_DELETE_WINDOW", self._on_cancel)

        self.canvas = tk.Canvas(self.win, bg="black", highlightthickness=0)
        self.canvas.pack(fill=tk.BOTH, expand=True)

        self.canvas.bind("<ButtonPress-1>", self._on_press)
        self.canvas.bind("<B1-Motion>", self._on_drag)
        self.canvas.bind("<ButtonRelease-1>", self._on_release)
        self.win.bind("<Escape>", lambda e: self._on_cancel())

    def _on_cancel(self):
        self.win.destroy()
        self.parent_root.deiconify()

    def _on_press(self, event):
        self.start_x = event.x_root
        self.start_y = event.y_root
        if self.rect_id:
            self.canvas.delete(self.rect_id)
        self.rect_id = self.canvas.create_rectangle(
            event.x, event.y, event.x, event.y,
            outline="red", width=2, fill=""
        )

    def _on_drag(self, event):
        if self.rect_id:
            sx = self.start_x - self.win.winfo_rootx()
            sy = self.start_y - self.win.winfo_rooty()
            self.canvas.coords(self.rect_id, sx, sy, event.x, event.y)

    def _on_release(self, event):
        end_x = event.x_root
        end_y = event.y_root
        self.win.destroy()

        x1 = min(self.start_x, end_x)
        y1 = min(self.start_y, end_y)
        x2 = max(self.start_x, end_x)
        y2 = max(self.start_y, end_y)

        if x2 - x1 < 5 or y2 - y1 < 5:
            self.parent_root.deiconify()
            return

        # 等待覆盖层完全消失后再通知
        self.parent_root.after(150, lambda: self.on_select(x1, y1, x2, y2))

# ============ 悬浮译文窗 ============
class FloatingTranslation:
    """无边框、始终置顶的悬浮译文窗口"""

    MIN_W, MIN_H = 260, 60

    def __init__(self, root):
        self._root = root
        self.win = tk.Toplevel(root)
        apply_window_icon(self.win)
        self.win.overrideredirect(True)
        self.win.attributes("-topmost", True)
        self.win.geometry("500x140+600+820")
        self.win.configure(bg="#0d1117")
        self.win.withdraw()  # 初始隐藏，有译文时自动显示

        self._drag   = {"x": 0, "y": 0}
        self._resize = {}

        # 顶部拖动条
        bar = tk.Frame(self.win, bg="#161b22", height=24)
        bar.pack(fill=tk.X)
        bar.pack_propagate(False)

        self._bar_label = tk.Label(
            bar, text="译文", fg="#4ec9b0", bg="#161b22",
            font=("Microsoft YaHei UI", 8), anchor="w"
        )
        self._bar_label.pack(side=tk.LEFT, padx=8)

        tk.Button(
            bar, text="✕", fg="#666666", bg="#161b22",
            activebackground="#c0392b", activeforeground="white",
            bd=0, font=("Segoe UI", 9), padx=6,
            command=self.hide
        ).pack(side=tk.RIGHT)

        # 拖动绑定（标题栏及其子控件）
        for w in (bar, self._bar_label):
            w.bind("<ButtonPress-1>", self._start_drag)
            w.bind("<B1-Motion>",     self._do_drag)

        # 译文文本框
        self.text = tk.Text(
            self.win, wrap=tk.WORD, bg="#0d1117", fg="#e6e6e6",
            font=("Microsoft YaHei UI", 13), relief=tk.FLAT,
            padx=12, pady=8, state=tk.DISABLED
        )
        self.text.pack(fill=tk.BOTH, expand=True)

        # 右下角缩放手柄
        grip = tk.Label(
            self.win, text="⋱", fg="#30363d", bg="#0d1117",
            font=("Segoe UI", 10), cursor="size_nw_se"
        )
        grip.place(relx=1.0, rely=1.0, anchor="se")
        grip.bind("<ButtonPress-1>", self._start_resize)
        grip.bind("<B1-Motion>",     self._do_resize)

    def set_text(self, text):
        self.text.configure(state=tk.NORMAL)
        self.text.delete("1.0", tk.END)
        self.text.insert(tk.END, text)
        self.text.configure(state=tk.DISABLED)
        if not self.win.winfo_viewable():
            self.win.deiconify()

    def show(self):
        self.win.deiconify()
        self.win.lift()

    def hide(self):
        self.win.withdraw()

    def _start_drag(self, event):
        self._drag["x"] = event.x_root - self.win.winfo_x()
        self._drag["y"] = event.y_root - self.win.winfo_y()

    def _do_drag(self, event):
        self.win.geometry(
            f"+{event.x_root - self._drag['x']}+{event.y_root - self._drag['y']}"
        )

    def _start_resize(self, event):
        self._resize = {
            "x": event.x_root, "y": event.y_root,
            "w": self.win.winfo_width(), "h": self.win.winfo_height()
        }

    def _do_resize(self, event):
        w = max(self.MIN_W, self._resize["w"] + event.x_root - self._resize["x"])
        h = max(self.MIN_H, self._resize["h"] + event.y_root - self._resize["y"])
        self.win.geometry(f"{w}x{h}")


# ============ 主应用 ============
class TranslatorApp:
    """视觉小说实时翻译主窗口：持续监控指定屏幕区域，有文本变化时自动OCR+翻译"""

    LANG_OPTIONS = {
        "英语 → 中文":    ("en",   "zh"),
        "中文 → 英语":    ("zh",   "en"),
    }

    MIN_WIDTH  = 380
    MIN_HEIGHT = 300

    def __init__(self):
        self.app_settings = get_app_settings(CONFIG)
        self.root = tk.Tk()
        self.root.withdraw()
        apply_window_icon(self.root)
        self.root.title("1615's Translator")
        self.root.attributes("-topmost", bool(self.app_settings["topmost"]))
        self.root.geometry("600x450+150+150")
        self.root.minsize(self.MIN_WIDTH, self.MIN_HEIGHT)
        self.root.configure(bg="#2b2b2b")
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

        self.capture_region   = None   # (x1, y1, x2, y2)
        self.is_monitoring    = False
        self._stop_event      = threading.Event()
        self.last_image_hash  = None
        self.last_ocr_text    = ""
        default_language = self.app_settings["default_language"]
        if default_language not in self.LANG_OPTIONS:
            default_language = "英语 → 中文"
        self.interval_var     = tk.IntVar(value=int(self.app_settings["default_interval"]))
        self.lang_var         = tk.StringVar(value=default_language)
        self.topmost_var      = tk.BooleanVar(value=bool(self.app_settings["topmost"]))
        self._settings_win    = None

        self._drag = {"x": 0, "y": 0}
        self._build_ui()
        self.float_win = FloatingTranslation(self.root)
        self.root.update_idletasks()
        self.root.deiconify()

    # ------------------------------------------------------------------ UI --
    def _build_ui(self):
        # 自定义标题栏
        title_bar = tk.Frame(self.root, bg="#1e1e1e", height=32)
        title_bar.pack(fill=tk.X, side=tk.TOP)
        title_bar.pack_propagate(False)

        tk.Label(
            title_bar, text="视觉小说实时翻译",
            fg="white", bg="#1e1e1e",
            font=("Microsoft YaHei UI", 10, "bold"), anchor="w"
        ).pack(side=tk.LEFT, fill=tk.X, expand=True)

        tk.Checkbutton(
            title_bar, text="置顶", variable=self.topmost_var,
            fg="white", bg="#1e1e1e", selectcolor="#333333",
            activebackground="#1e1e1e", activeforeground="white",
            font=("Microsoft YaHei UI", 9),
            command=lambda: self.root.attributes("-topmost", self.topmost_var.get())
        ).pack(side=tk.RIGHT, padx=4)

        tk.Button(
            title_bar, text="✕", fg="white", bg="#1e1e1e",
            activebackground="#e81123", bd=0, font=("Segoe UI", 12),
            command=self._on_close
        ).pack(side=tk.RIGHT, padx=(0, 2))

        for w in (title_bar,):
            w.bind("<ButtonPress-1>", self._start_drag)
            w.bind("<B1-Motion>",     self._do_drag)

        # 控制区
        ctrl = tk.Frame(self.root, bg="#2b2b2b", pady=6)
        ctrl.pack(fill=tk.X, padx=8)

        # 行1：操作按钮
        row1 = tk.Frame(ctrl, bg="#2b2b2b")
        row1.pack(fill=tk.X)

        for column in range(3):
            row1.grid_columnconfigure(column, weight=1, uniform="main_actions")

        self.btn_select = tk.Button(
            row1, text="📷 选择区域", bg="#8f2d2d", fg="white",
            font=("Microsoft YaHei UI", 9), bd=0, padx=10, pady=4,
            activebackground="#a83a3a", command=self._select_region
        )
        self.btn_select.grid(row=0, column=0, sticky="ew", padx=(0, 6), pady=(0, 6))

        self.btn_toggle = tk.Button(
            row1, text="▶ 开始监控", bg="#9a5a00", fg="white",
            font=("Microsoft YaHei UI", 9), bd=0, padx=10, pady=4,
            activebackground="#b56a00", command=self._toggle_monitor
        )
        self.btn_toggle.grid(row=0, column=1, sticky="ew", padx=3, pady=(0, 6))

        tk.Button(
            row1, text="🔍 立即翻译", bg="#8b7a00", fg="white",
            font=("Microsoft YaHei UI", 9), bd=0, padx=10, pady=4,
            activebackground="#a09100", command=self._translate_once
        ).grid(row=0, column=2, sticky="ew", padx=(6, 0), pady=(0, 6))

        tk.Button(
            row1, text="➕ 添加词典", bg="#0b7285", fg="white",
            font=("Microsoft YaHei UI", 9), bd=0, padx=10, pady=4,
            activebackground="#1493a5", command=self._add_custom_word
        ).grid(row=1, column=0, sticky="ew", padx=(0, 6), pady=(0, 0))

        tk.Button(
            row1, text="📋 显示译文", bg="#2f5aa8", fg="white",
            font=("Microsoft YaHei UI", 9), bd=0, padx=10, pady=4,
            activebackground="#3b6cc4",
            command=lambda: self.float_win.show()
        ).grid(row=1, column=1, sticky="ew", padx=3, pady=(0, 0))

        tk.Button(
            row1, text="⚙ 其他设置", bg="#5b3f8c", fg="white",
            font=("Microsoft YaHei UI", 9), bd=0, padx=10, pady=4,
            activebackground="#7250b3", command=self._open_settings
        ).grid(row=1, column=2, sticky="ew", padx=(6, 0), pady=(0, 0))

        # 行2：语言 + 间隔
        row2 = tk.Frame(ctrl, bg="#2b2b2b", pady=4)
        row2.pack(fill=tk.X)

        tk.Label(row2, text="语言:", fg="#9cdcfe", bg="#2b2b2b",
                 font=("Microsoft YaHei UI", 9)).pack(side=tk.LEFT)

        om = tk.OptionMenu(row2, self.lang_var, *self.LANG_OPTIONS.keys())
        om.config(bg="#3c3c3c", fg="white", activebackground="#505050",
                  font=("Microsoft YaHei UI", 9), bd=0, highlightthickness=0)
        om["menu"].config(bg="#3c3c3c", fg="white",
                          font=("Microsoft YaHei UI", 9))
        om.pack(side=tk.LEFT, padx=(4, 16))

        tk.Label(row2, text="间隔:", fg="#9cdcfe", bg="#2b2b2b",
                 font=("Microsoft YaHei UI", 9)).pack(side=tk.LEFT)

        tk.Scale(
            row2, variable=self.interval_var, from_=1, to=10,
            orient=tk.HORIZONTAL, length=90, bg="#2b2b2b", fg="white",
            troughcolor="#1e1e1e", activebackground="#4a9d4a",
            highlightthickness=0, bd=0
        ).pack(side=tk.LEFT, padx=(4, 2))

        tk.Label(row2, text="秒", fg="#9cdcfe", bg="#2b2b2b",
                 font=("Microsoft YaHei UI", 9)).pack(side=tk.LEFT)

        # 状态栏
        status_bar = tk.Frame(self.root, bg="#1e1e1e", height=24)
        status_bar.pack(fill=tk.X)
        status_bar.pack_propagate(False)

        self.status_dot = tk.Label(
            status_bar, text="●", fg="#666666",
            bg="#1e1e1e", font=("Segoe UI", 10)
        )
        self.status_dot.pack(side=tk.LEFT, padx=(8, 2))

        self.status_label = tk.Label(
            status_bar, text="请先选择监控区域", fg="#999999", bg="#1e1e1e",
            font=("Microsoft YaHei UI", 9), anchor="w"
        )
        self.status_label.pack(side=tk.LEFT, fill=tk.X, expand=True)

        self.region_label = tk.Label(
            status_bar, text="未选择区域", fg="#666666", bg="#1e1e1e",
            font=("Microsoft YaHei UI", 8), anchor="e"
        )
        self.region_label.pack(side=tk.RIGHT, padx=8)

        tk.Frame(self.root, bg="#444444", height=1).pack(fill=tk.X)

        # 文本区域（显示清洗后的识别原文）
        content = tk.Frame(self.root, bg="#2b2b2b")
        content.pack(fill=tk.BOTH, expand=True, padx=8, pady=(6, 6))

        tk.Label(content, text="识别原文:", fg="#9cdcfe", bg="#2b2b2b",
                 font=("Microsoft YaHei UI", 9), anchor="w").pack(fill=tk.X)

        self.ocr_box = tk.Text(
            content, wrap=tk.WORD, bg="#1e1e1e", fg="#d4d4d4",
            font=("Microsoft YaHei UI", int(self.app_settings["ocr_font_size"])), relief=tk.FLAT,
            height=4, padx=6, pady=4, state=tk.DISABLED
        )
        self.ocr_box.tag_configure(
            "custom_dict_hit",
            foreground=self.app_settings["highlight_foreground"],
            background=self.app_settings["highlight_background"],
            font=("Microsoft YaHei UI", int(self.app_settings["ocr_font_size"]), "bold")
        )
        self.ocr_box.pack(fill=tk.BOTH, expand=True, pady=(2, 0))

        # 右下角拖拽调整大小
        grip = tk.Label(self.root, text="⋱", fg="#555555", bg="#2b2b2b",
                        font=("Segoe UI", 10), cursor="size_nw_se")
        grip.pack(side=tk.RIGHT, anchor=tk.SE)
        grip.bind("<ButtonPress-1>", self._start_resize)
        grip.bind("<B1-Motion>",     self._do_resize)
        self._resize_data = {}

    # ------------------------------------------------------- 拖动 / 缩放 --
    def _start_drag(self, event):
        self._drag["x"] = event.x_root - self.root.winfo_x()
        self._drag["y"] = event.y_root - self.root.winfo_y()

    def _do_drag(self, event):
        self.root.geometry(
            f"+{event.x_root - self._drag['x']}+{event.y_root - self._drag['y']}"
        )

    def _start_resize(self, event):
        self._resize_data = {
            "x": event.x_root, "y": event.y_root,
            "w": self.root.winfo_width(), "h": self.root.winfo_height()
        }

    def _do_resize(self, event):
        dx = event.x_root - self._resize_data["x"]
        dy = event.y_root - self._resize_data["y"]
        w  = max(self.MIN_WIDTH,  self._resize_data["w"] + dx)
        h  = max(self.MIN_HEIGHT, self._resize_data["h"] + dy)
        self.root.geometry(f"{w}x{h}")

    def _choose_color(self, variable, parent_window):
        color = colorchooser.askcolor(color=variable.get(), parent=parent_window)[1]
        if color:
            variable.set(color)

    def _apply_app_settings(self, settings):
        self.app_settings = dict(settings)
        default_language = settings["default_language"]
        if default_language not in self.LANG_OPTIONS:
            default_language = "英语 → 中文"
        self.lang_var.set(default_language)
        self.interval_var.set(int(settings["default_interval"]))
        self.topmost_var.set(bool(settings["topmost"]))
        self.root.attributes("-topmost", self.topmost_var.get())

        font_size = int(settings["ocr_font_size"])
        self.ocr_box.configure(font=("Microsoft YaHei UI", font_size))
        self.ocr_box.tag_configure(
            "custom_dict_hit",
            foreground=settings["highlight_foreground"],
            background=settings["highlight_background"],
            font=("Microsoft YaHei UI", font_size, "bold")
        )
        if self.last_ocr_text:
            self._update_ocr_text(self.last_ocr_text)

    def _open_settings(self):
        if self._settings_win and self._settings_win.winfo_exists():
            self._settings_win.deiconify()
            self._settings_win.lift()
            self._settings_win.focus_force()
            return

        settings = get_app_settings(CONFIG)
        win = tk.Toplevel(self.root)
        self._settings_win = win
        apply_window_icon(win)
        win.title("其他设置")
        win.transient(self.root)
        win.attributes("-topmost", True)
        win.configure(bg="#2b2b2b")
        win.geometry("560x460+220+180")

        language_var = tk.StringVar(value=settings["default_language"])
        interval_var = tk.IntVar(value=int(settings["default_interval"]))
        topmost_var = tk.BooleanVar(value=bool(settings["topmost"]))
        font_size_var = tk.IntVar(value=int(settings["ocr_font_size"]))
        highlight_fg_var = tk.StringVar(value=settings["highlight_foreground"])
        highlight_bg_var = tk.StringVar(value=settings["highlight_background"])
        new_word_var = tk.StringVar()

        notebook = ttk.Notebook(win)
        notebook.pack(fill=tk.BOTH, expand=True, padx=10, pady=(10, 8))

        general_tab = tk.Frame(notebook, bg="#2b2b2b")
        dictionary_tab = tk.Frame(notebook, bg="#2b2b2b")
        display_tab = tk.Frame(notebook, bg="#2b2b2b")
        notebook.add(general_tab, text="常规")
        notebook.add(dictionary_tab, text="词典")
        notebook.add(display_tab, text="界面")

        tk.Label(general_tab, text="默认语言", fg="#9cdcfe", bg="#2b2b2b",
                 font=("Microsoft YaHei UI", 9)).grid(row=0, column=0, sticky="w", padx=12, pady=(14, 6))
        general_language_menu = tk.OptionMenu(general_tab, language_var, *self.LANG_OPTIONS.keys())
        general_language_menu.config(bg="#3c3c3c", fg="white", activebackground="#505050",
                                     font=("Microsoft YaHei UI", 9), bd=0, highlightthickness=0)
        general_language_menu["menu"].config(bg="#3c3c3c", fg="white",
                                              font=("Microsoft YaHei UI", 9))
        general_language_menu.grid(row=0, column=1, sticky="w", padx=12, pady=(14, 6))

        tk.Label(general_tab, text="默认监控间隔（秒）", fg="#9cdcfe", bg="#2b2b2b",
                 font=("Microsoft YaHei UI", 9)).grid(row=1, column=0, sticky="w", padx=12, pady=6)
        tk.Spinbox(general_tab, from_=1, to=10, textvariable=interval_var, width=8,
                   font=("Microsoft YaHei UI", 9)).grid(row=1, column=1, sticky="w", padx=12, pady=6)

        tk.Checkbutton(
            general_tab, text="启动时窗口置顶", variable=topmost_var,
            fg="white", bg="#2b2b2b", selectcolor="#333333",
            activebackground="#2b2b2b", activeforeground="white",
            font=("Microsoft YaHei UI", 9)
        ).grid(row=2, column=0, columnspan=2, sticky="w", padx=12, pady=6)

        dictionary_list = tk.Listbox(
            dictionary_tab, bg="#1e1e1e", fg="#d4d4d4",
            font=("Consolas", 10), selectbackground="#264f78",
            relief=tk.FLAT, height=14
        )
        dictionary_list.pack(fill=tk.BOTH, expand=True, padx=12, pady=(14, 8))
        for word in CONFIG.get("custom_dict", []):
            dictionary_list.insert(tk.END, word)

        dictionary_actions = tk.Frame(dictionary_tab, bg="#2b2b2b")
        dictionary_actions.pack(fill=tk.X, padx=12, pady=(0, 12))
        tk.Entry(
            dictionary_actions, textvariable=new_word_var,
            bg="#1e1e1e", fg="#d4d4d4", insertbackground="white",
            relief=tk.FLAT, font=("Microsoft YaHei UI", 9)
        ).pack(side=tk.LEFT, fill=tk.X, expand=True)

        def add_dictionary_word():
            word = new_word_var.get().strip()
            if not word:
                messagebox.showwarning("提示", "自定义词不能为空", parent=win)
                return
            existing = {
                dictionary_list.get(index).casefold()
                for index in range(dictionary_list.size())
            }
            if word.casefold() in existing:
                messagebox.showinfo("提示", f"“{word}” 已存在", parent=win)
                return
            dictionary_list.insert(tk.END, word)
            new_word_var.set("")

        def remove_dictionary_word():
            selected = list(dictionary_list.curselection())
            if not selected:
                messagebox.showwarning("提示", "请先选择要删除的词", parent=win)
                return
            for index in reversed(selected):
                dictionary_list.delete(index)

        tk.Button(
            dictionary_actions, text="添加", bg="#0e639c", fg="white",
            font=("Microsoft YaHei UI", 9), bd=0, padx=10, pady=4,
            activebackground="#1177aa", command=add_dictionary_word
        ).pack(side=tk.LEFT, padx=(8, 6))
        tk.Button(
            dictionary_actions, text="删除选中", bg="#9d4a4a", fg="white",
            font=("Microsoft YaHei UI", 9), bd=0, padx=10, pady=4,
            activebackground="#b05a5a", command=remove_dictionary_word
        ).pack(side=tk.LEFT)

        tk.Label(display_tab, text="识别原文字号", fg="#9cdcfe", bg="#2b2b2b",
                 font=("Microsoft YaHei UI", 9)).grid(row=0, column=0, sticky="w", padx=12, pady=(14, 6))
        tk.Spinbox(display_tab, from_=8, to=20, textvariable=font_size_var, width=8,
                   font=("Microsoft YaHei UI", 9)).grid(row=0, column=1, sticky="w", padx=12, pady=(14, 6))

        tk.Label(display_tab, text="命中高亮文字颜色", fg="#9cdcfe", bg="#2b2b2b",
                 font=("Microsoft YaHei UI", 9)).grid(row=1, column=0, sticky="w", padx=12, pady=6)
        tk.Entry(display_tab, textvariable=highlight_fg_var, width=12,
                 bg="#1e1e1e", fg="#d4d4d4", insertbackground="white",
                 relief=tk.FLAT, font=("Consolas", 10)).grid(row=1, column=1, sticky="w", padx=(12, 6), pady=6)
        tk.Button(
            display_tab, text="选择", bg="#3c3c3c", fg="white",
            font=("Microsoft YaHei UI", 9), bd=0, padx=10, pady=4,
            activebackground="#505050",
            command=lambda: self._choose_color(highlight_fg_var, win)
        ).grid(row=1, column=2, sticky="w", padx=6, pady=6)

        tk.Label(display_tab, text="命中高亮背景颜色", fg="#9cdcfe", bg="#2b2b2b",
                 font=("Microsoft YaHei UI", 9)).grid(row=2, column=0, sticky="w", padx=12, pady=6)
        tk.Entry(display_tab, textvariable=highlight_bg_var, width=12,
                 bg="#1e1e1e", fg="#d4d4d4", insertbackground="white",
                 relief=tk.FLAT, font=("Consolas", 10)).grid(row=2, column=1, sticky="w", padx=(12, 6), pady=6)
        tk.Button(
            display_tab, text="选择", bg="#3c3c3c", fg="white",
            font=("Microsoft YaHei UI", 9), bd=0, padx=10, pady=4,
            activebackground="#505050",
            command=lambda: self._choose_color(highlight_bg_var, win)
        ).grid(row=2, column=2, sticky="w", padx=6, pady=6)

        bottom_bar = tk.Frame(win, bg="#2b2b2b")
        bottom_bar.pack(fill=tk.X, padx=10, pady=(0, 10))

        def save_settings():
            language = language_var.get()
            if language not in self.LANG_OPTIONS:
                messagebox.showerror("错误", "默认语言选项无效", parent=win)
                return

            try:
                interval = int(interval_var.get())
                font_size = int(font_size_var.get())
            except (TypeError, ValueError):
                messagebox.showerror("错误", "间隔和字号必须是整数", parent=win)
                return

            if interval < 1 or interval > 10:
                messagebox.showerror("错误", "默认监控间隔必须在 1 到 10 秒之间", parent=win)
                return
            if font_size < 8 or font_size > 20:
                messagebox.showerror("错误", "识别原文字号必须在 8 到 20 之间", parent=win)
                return

            highlight_fg = highlight_fg_var.get().strip()
            highlight_bg = highlight_bg_var.get().strip()
            if not _is_valid_hex_color(highlight_fg) or not _is_valid_hex_color(highlight_bg):
                messagebox.showerror("错误", "高亮颜色必须是 #RRGGBB 格式", parent=win)
                return

            updated_words = [
                dictionary_list.get(index)
                for index in range(dictionary_list.size())
            ]
            updated_config = load_config()
            updated_config["custom_dict"] = updated_words
            updated_config["app_settings"] = {
                "default_language": language,
                "default_interval": interval,
                "topmost": bool(topmost_var.get()),
                "ocr_font_size": font_size,
                "highlight_foreground": highlight_fg,
                "highlight_background": highlight_bg,
            }
            save_config(updated_config)

            CONFIG.clear()
            CONFIG.update(updated_config)
            CONFIG["app_settings"] = get_app_settings(CONFIG)
            CUSTOM_DICT_STATE.refresh(force=True)
            self._apply_app_settings(CONFIG["app_settings"])
            self._set_status("设置已保存", "#9cdcfe", "#4ec9b0")
            self._settings_win = None
            win.destroy()

        def on_close_settings():
            self._settings_win = None
            win.destroy()

        tk.Button(
            bottom_bar, text="保存", bg="#4a9d4a", fg="white",
            font=("Microsoft YaHei UI", 9), bd=0, padx=14, pady=5,
            activebackground="#5ab65a", command=save_settings
        ).pack(side=tk.RIGHT)
        tk.Button(
            bottom_bar, text="取消", bg="#3c3c3c", fg="white",
            font=("Microsoft YaHei UI", 9), bd=0, padx=14, pady=5,
            activebackground="#505050", command=on_close_settings
        ).pack(side=tk.RIGHT, padx=(0, 8))

        win.protocol("WM_DELETE_WINDOW", on_close_settings)
        win.focus_force()

    # ----------------------------------------------------- 区域选择 --
    def _select_region(self):
        self.root.withdraw()
        self.root.after(200, self._show_selector)

    def _show_selector(self):
        def on_select(x1, y1, x2, y2):
            self.capture_region = (x1, y1, x2, y2)
            w, h = x2 - x1, y2 - y1
            self.region_label.config(
                text=f"({x1},{y1})  {w}×{h}px", fg="#9cdcfe"
            )
            self.last_image_hash = None
            self.last_ocr_text   = ""
            self._update_ocr_text("")
            self._set_status("区域已选择，点击开始监控", "#9cdcfe", "#4a9d4a")
            self.root.deiconify()

        ScreenSelector(self.root, on_select)

    # ----------------------------------------------------- 监控控制 --
    def _toggle_monitor(self):
        if self.is_monitoring:
            self._stop_monitor()
        else:
            self._start_monitor()

    def _start_monitor(self):
        if not self.capture_region:
            messagebox.showwarning("提示", "请先选择监控区域", parent=self.root)
            return
        self.is_monitoring = True
        self._stop_event.clear()
        self.last_image_hash = None
        self.btn_toggle.config(
            text="⏹ 停止监控", bg="#b56a00", activebackground="#9a5a00"
        )
        self._set_status("监控中...", "#4ec9b0", "#4ec9b0")
        threading.Thread(target=self._monitor_loop, daemon=True).start()

    def _stop_monitor(self):
        self.is_monitoring = False
        self._stop_event.set()
        self.btn_toggle.config(
            text="▶ 开始监控", bg="#9a5a00", activebackground="#b56a00"
        )
        self._set_status("已停止监控", "#999999", "#666666")

    # ----------------------------------------------------- 后台监控线程 --
    def _monitor_loop(self):
        while not self._stop_event.is_set():
            try:
                self._capture_and_translate()
            except Exception as e:
                self.root.after(0, self._set_status,
                                f"错误: {e}", "#f48771", "#f48771")
            self._stop_event.wait(self.interval_var.get())

    def _capture_and_translate(self):
        x1, y1, x2, y2 = self.capture_region
        img = pyautogui.screenshot(region=(x1, y1, x2 - x1, y2 - y1))
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        image_bytes = buf.getvalue()

        # 图像哈希比对：未变化则跳过
        img_hash = hashlib.md5(image_bytes).hexdigest()
        if img_hash == self.last_image_hash:
            return
        self.last_image_hash = img_hash

        self.root.after(0, self._set_status, "识别中...", "#dcdcaa", "#dcdcaa")
        raw_text = ocr_recognize(image_bytes)
        if not raw_text.strip():
            self.root.after(0, self._update_ocr_text, "")
            self.root.after(0, self._set_status,
                            "未识别到文字，监控中...", "#999999", "#4ec9b0")
            return

        ocr_text = clean_ocr_text(raw_text)

        # OCR文本比对：内容未变化则跳过翻译
        if ocr_text.strip() == self.last_ocr_text.strip():
            self.root.after(0, self._set_status, "监控中...", "#4ec9b0", "#4ec9b0")
            return
        self.last_ocr_text = ocr_text
        self.root.after(0, self._update_ocr_text, ocr_text)

        self.root.after(0, self._set_status, "翻译中...", "#dcdcaa", "#dcdcaa")
        from_lang, to_lang = self.LANG_OPTIONS[self.lang_var.get()]
        translated = translate_text(ocr_text, from_lang=from_lang, to_lang=to_lang)

        self.root.after(0, self._update_translation_text, translated)
        self.root.after(0, self._set_status, "监控中...", "#4ec9b0", "#4ec9b0")

    # ----------------------------------------------------- 立即翻译 --
    def _translate_once(self):
        if not self.capture_region:
            messagebox.showwarning("提示", "请先选择监控区域", parent=self.root)
            return
        self._set_status("识别中...", "#dcdcaa", "#dcdcaa")

        def run():
            try:
                x1, y1, x2, y2 = self.capture_region
                img = pyautogui.screenshot(region=(x1, y1, x2 - x1, y2 - y1))
                buf = io.BytesIO()
                img.save(buf, format="PNG")
                image_bytes = buf.getvalue()

                raw_text = ocr_recognize(image_bytes)
                if not raw_text.strip():
                    self.root.after(0, self._update_ocr_text, "")
                    self.root.after(0, self._set_status,
                                    "未识别到文字", "#999999", "#666666")
                    return

                ocr_text = clean_ocr_text(raw_text)
                self.root.after(0, self._update_ocr_text, ocr_text)
                from_lang, to_lang = self.LANG_OPTIONS[self.lang_var.get()]
                translated = translate_text(ocr_text,
                                            from_lang=from_lang, to_lang=to_lang)
                self.root.after(0, self._update_translation_text, translated)
                if not self.is_monitoring:
                    self.root.after(0, self._set_status,
                                    "翻译完成", "#4ec9b0", "#4ec9b0")
                # 同步哈希，避免监控立刻重复翻译
                self.last_image_hash = hashlib.md5(image_bytes).hexdigest()
                self.last_ocr_text   = ocr_text
            except Exception as e:
                self.root.after(0, self._set_status,
                                f"错误: {e}", "#f48771", "#f48771")

        threading.Thread(target=run, daemon=True).start()

    def _add_custom_word(self):
        word = simpledialog.askstring(
            "添加自定义词",
            "输入要加入词典的单词：",
            parent=self.root
        )
        if word is None:
            return

        try:
            added = CUSTOM_DICT_STATE.add_word(word)
        except ValueError as exc:
            messagebox.showwarning("提示", str(exc), parent=self.root)
            return
        except Exception as exc:
            self._set_status(f"保存自定义词失败: {exc}", "#f48771", "#f48771")
            messagebox.showerror("错误", f"保存自定义词失败：\n{exc}", parent=self.root)
            return

        clean_word = word.strip()
        if not added:
            messagebox.showinfo("提示", f"“{clean_word}” 已在自定义词典中", parent=self.root)
            self._set_status("自定义词已存在", "#dcdcaa", "#dcdcaa")
            return

        self.last_image_hash = None
        self.last_ocr_text = ""
        self._set_status(f"已添加自定义词: {clean_word}", "#9cdcfe", "#4ec9b0")
        messagebox.showinfo("成功", f"已添加自定义词：\n{clean_word}\n\n后续识别会立即使用最新词典。", parent=self.root)

    # ----------------------------------------------------- UI 更新辅助 --
    def _update_ocr_text(self, ocr_text):
        custom_spans = _find_custom_dict_spans(ocr_text)

        self.ocr_box.configure(state=tk.NORMAL)
        self.ocr_box.delete("1.0", tk.END)
        self.ocr_box.insert(tk.END, ocr_text)
        self.ocr_box.tag_remove("custom_dict_hit", "1.0", tk.END)
        for start, end in custom_spans:
            self.ocr_box.tag_add(
                "custom_dict_hit",
                f"1.0 + {start} chars",
                f"1.0 + {end} chars"
            )
        self.ocr_box.configure(state=tk.DISABLED)

    def _update_translation_text(self, translated):
        self.float_win.set_text(translated)

    def _set_status(self, text, text_color="#999999", dot_color="#666666"):
        self.status_label.config(text=text, fg=text_color)
        self.status_dot.config(fg=dot_color)

    def _on_close(self):
        self._stop_monitor()
        self.root.destroy()

    def run(self):
        self.root.mainloop()


# ============ 入口 ============
def main():
    TranslatorApp().run()


if __name__ == "__main__":
    main()
