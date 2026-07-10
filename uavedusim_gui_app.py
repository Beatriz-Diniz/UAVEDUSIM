#!/usr/bin/env python3
import os
import re
import sys
import time
import math
import json
import random
import shutil
import signal
import subprocess
import threading
from pathlib import Path
import tkinter as tk
from tkinter import ttk

try:
    from PIL import Image, ImageDraw, ImageTk
    HAS_PIL = True
except ImportError:
    HAS_PIL = False

APP_DIR = Path(os.environ.get("APP_DIR") or os.environ.get("UAVEDUSIM_DIR") or Path(__file__).resolve().parent).resolve()
PX4_DIR = Path(os.environ.get("PX4_DIR") or APP_DIR / "PX4-Autopilot").resolve()
QGC_APP = Path(os.environ.get("QGC_APP") or APP_DIR / "QGroundControl-x86_64.AppImage").resolve()
QGC_EXTRACTED = Path(os.environ.get("QGC_EXTRACTED") or APP_DIR / "squashfs-root/usr/bin/QGroundControl").resolve()
LOG = Path(os.environ.get("LOG") or APP_DIR / "uavedusim_gui.log").resolve()
SESSION_ID = os.environ.get("UAVEDUSIM_SESSION_ID") or str(os.getpid())
RUNTIME_DIR = Path(os.environ.get("UAVEDUSIM_RUNTIME_DIR") or APP_DIR / ".uavedusim_runtime").resolve()
PID_FILE = Path(os.environ.get("UAVEDUSIM_PID_FILE") or RUNTIME_DIR / f"terminals_{SESSION_ID}.pids").resolve()
SCRIPT_DIR = Path(os.environ.get("UAVEDUSIM_SCRIPT_DIR") or RUNTIME_DIR / f"scripts_{SESSION_ID}").resolve()

# Registro local do progresso do quiz — não é por sessão, fica salvo
# entre execuções, pra não obrigar a refazer o quiz toda vez que o
# simulador for aberto de novo. O mesmo arquivo também guarda a
# preferência de idioma da interface (chave "lang"), pra não precisar
# de um segundo arquivo só pra isso.
QUIZ_PROGRESS_FILE = Path(os.environ.get("UAVEDUSIM_QUIZ_PROGRESS_FILE") or APP_DIR / ".uavedusim_quiz_progress.json").resolve()

RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
SCRIPT_DIR.mkdir(parents=True, exist_ok=True)
PID_FILE.touch(exist_ok=True)
LOG.touch(exist_ok=True)

BG          = "#1e1e2e"
SURFACE     = "#313244"
SURFACE2    = "#45475a"
BORDER      = "#585b70"
TEXT        = "#cdd6f4"
MUTED       = "#bac2de"
ACCENT      = "#cba6f7"
ACCENT_DIM  = "#776394"
LAVENDER    = "#b4befe"
DISABLED_BG = "#313244"
DISABLED_FG = "#6c7086"
TAG_BG      = "#293248"
TAG_FG      = "#89b4fa"
SEL_BG      = "#35433b"
SEL_FG      = "#a6e3a1"
ROW_EVEN    = "#313244"
ROW_ODD     = "#181825"
OK          = "#a6e3a1"
WARN        = "#f9e2af"
DANGER      = "#f38ba8"
BLUE        = "#89b4fa"

ICONS = {
    # Ícones das ações principais do menu.
    "executar": "\uf04b",
    "limpar": "↺",
    "voltar": "󰁎",
    "sair": "⏻",

    # Ícone usado no alerta de manutenção da tela inicial.
    "ataque": "⚠",
}

# Textos curtos exibidos no balão de informação (ícone "i") de cada ataque.
ATTACK_INFO_TEXT = {
    "noise": (
        "Jamming Noise: o GPS parece continuar funcionando, mas as medições de "
        "posição, velocidade e altitude chegam degradadas por ruído. Quanto maior "
        "a intensidade (ex: 80), maior a diferença entre a posição estimada e a "
        "posição real do VANT."
    ),
    "blackout": (
        "Jamming Blackout: bloqueia por completo o sinal de GPS/GNSS. O VANT "
        "deixa de receber novas atualizações de posição, podendo interromper a "
        "navegação autônoma ou acionar mecanismos de seguranca (failsafe)."
    ),
    "pulse": (
        "Jamming Pulse: ataque intermitente que alterna entre degradação, "
        "bloqueio e recuperação parcial do GPS. A intensidade (ex: 0.3) afeta o "
        "padrão do ciclo, causando oscilações na navegação do VANT."
    ),
}


def _uavedusim_blend(hex1, hex2, t):
    """Mistura duas cores hex (t=0 -> hex1, t=1 -> hex2)."""
    def to_rgb(h):
        h = h.lstrip("#")
        return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))

    r1, g1, b1 = to_rgb(hex1)
    r2, g2, b2 = to_rgb(hex2)
    r = round(r1 + (r2 - r1) * t)
    g = round(g1 + (g2 - g1) * t)
    b = round(b1 + (b2 - b1) * t)
    return f"#{r:02x}{g:02x}{b:02x}"


def _uavedusim_make_rounded_solid_photo(width, height, radius, color, outline=None, outline_width=0):
    """Gera (via Pillow) um retângulo sólido de cantos bem arredondados
    e com antialiasing (supersampling 4x), pronto para uso como
    tk.PhotoImage. Usado pelos painéis/botões arredondados da tela
    inicial quando o Pillow está disponível.
    """
    width, height = max(1, int(width)), max(1, int(height))
    scale = 4
    w, h, r = width * scale, height * scale, int(radius * scale)
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle(
        [0, 0, w - 1, h - 1], radius=r, fill=color,
        outline=outline, width=(outline_width * scale if outline else 0),
    )
    img = img.resize((width, height), Image.LANCZOS)
    return ImageTk.PhotoImage(img)


def _uavedusim_make_rounded_gradient_photo(width, height, radius, color1, color2):
    """Igual a `_uavedusim_make_rounded_solid_photo`, mas com um
    degradê horizontal suave entre `color1` e `color2` (usado no botão
    "Iniciar simulação")."""
    width, height = max(1, int(width)), max(1, int(height))
    scale = 4
    w, h, r = width * scale, height * scale, int(radius * scale)

    def to_rgb(hex_color):
        hex_color = hex_color.lstrip("#")
        return tuple(int(hex_color[i:i + 2], 16) for i in (0, 2, 4))

    c1, c2 = to_rgb(color1), to_rgb(color2)
    gradient = Image.new("RGB", (w, h))
    draw = ImageDraw.Draw(gradient)
    for x in range(w):
        t = x / max(w - 1, 1)
        col = tuple(round(c1[i] + (c2[i] - c1[i]) * t) for i in range(3))
        draw.line([(x, 0), (x, h)], fill=col)

    mask = Image.new("L", (w, h), 0)
    mdraw = ImageDraw.Draw(mask)
    mdraw.rounded_rectangle([0, 0, w - 1, h - 1], radius=r, fill=255)

    result = Image.new("RGBA", (w, h))
    result.paste(gradient, (0, 0), mask)
    result = result.resize((width, height), Image.LANCZOS)
    return ImageTk.PhotoImage(result)


# ============================================================================
# Internacionalização (PT-BR / EN) ------------------------------------------
#
# Estratégia: em vez de reescrever cada widget do app para buscar texto num
# dicionário, interceptamos (monkey-patch) os pontos onde texto chega até a
# tela — tk.Label/tk.Button (texto inicial e .configure), tk.Canvas.create_text
# (usado por todos os desenhos custom do app, incluindo o helper _uj_text) e
# StringVar.set (usado pelos rótulos de status). Cada string literal passa por
# _uavedusim_translate(); se o idioma atual for "en" e a string existir no
# dicionário, ela é trocada pela tradução — senão, a string original (PT)
# passa direto. Isso cobre a esmagadora maioria da interface sem precisar
# editar cada chamada de widget espalhada pelo arquivo.
#
# Mensagens que só vão para o terminal/arquivo de log (função log()) e
# scripts de shell/Python executados via subprocess (abertos em terminais
# separados) ficam de fora de propósito: não fazem parte da interface do
# simulador e traduzi-los não muda nada do que o usuário vê na janela do
# UAVEDUSIM.
# ============================================================================

_UAVEDUSIM_LANG = {"code": "en"}


def _uavedusim_load_lang_pref():
    try:
        if QUIZ_PROGRESS_FILE.exists():
            data = json.loads(QUIZ_PROGRESS_FILE.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                code = data.get("lang")
                if code in ("pt", "en"):
                    return code
    except Exception:
        pass
    return "en"


def _uavedusim_has_saved_lang_pref():
    try:
        if QUIZ_PROGRESS_FILE.exists():
            data = json.loads(QUIZ_PROGRESS_FILE.read_text(encoding="utf-8"))
            if isinstance(data, dict) and data.get("lang") in ("pt", "en"):
                return True
    except Exception:
        pass
    return False


def _uavedusim_save_lang_pref(code):
    try:
        QUIZ_PROGRESS_FILE.parent.mkdir(parents=True, exist_ok=True)
        data = {}
        try:
            if QUIZ_PROGRESS_FILE.exists():
                existing = json.loads(QUIZ_PROGRESS_FILE.read_text(encoding="utf-8"))
                if isinstance(existing, dict):
                    data = existing
        except Exception:
            data = {}
        data["lang"] = code
        QUIZ_PROGRESS_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except Exception:
        pass


def _uavedusim_set_lang(code):
    _UAVEDUSIM_LANG["code"] = code if code in ("pt", "en") else "pt"
    _uavedusim_save_lang_pref(_UAVEDUSIM_LANG["code"])


_UAVEDUSIM_LANG["code"] = _uavedusim_load_lang_pref()


# Dicionário PT -> EN. Preenchido logo abaixo da definição de todo o texto
# original do app (ver bloco UAVEDUSIM_I18N_EN mais adiante no arquivo).
_UAVEDUSIM_I18N_EN = {}


def _uavedusim_translate(text):
    if _UAVEDUSIM_LANG["code"] != "en" or not isinstance(text, str):
        return text
    return _UAVEDUSIM_I18N_EN.get(text, text)


def _uavedusim_translate_kwargs(kwargs):
    if "text" in kwargs and isinstance(kwargs["text"], str):
        kwargs["text"] = _uavedusim_translate(kwargs["text"])
    return kwargs


def _uavedusim_L(pt, en):
    """Escolhe entre um texto PT e um EN (para mensagens com valores
    interpolados, que não podem ser resolvidas só por dicionário)."""
    return en if _UAVEDUSIM_LANG["code"] == "en" else pt


_UAVEDUSIM_SCRIPT_I18N_EN = {
    "pymavlink não encontrado. Tentando instalar com pip --user...": "pymavlink not found. Trying to install it with pip --user...",
    "ERRO: pymavlink não está disponível.": "ERROR: pymavlink is not available.",
    "Instale com: python3 -m pip install --user pymavlink": "Install it with: python3 -m pip install --user pymavlink",
    "Tentando conexão MAVLink: ": "Trying MAVLink connection: ",
    "Heartbeat recebido: ": "Heartbeat received: ",
    "ERRO: não consegui conectar ao PX4 via MAVLink.": "ERROR: couldn't connect to PX4 via MAVLink.",
    "Último erro: ": "Last error: ",
    "Dica: tente UAVEDUSIM_MAVLINK_URL=udpin:0.0.0.0:14540 ./uavedusim_gui.sh": "Tip: try UAVEDUSIM_MAVLINK_URL=udpin:0.0.0.0:14540 ./uavedusim_gui.sh",
    "Parâmetro ": "Parameter ",
    " ajustado para ": " set to ",
    " enviado sem confirmação.": " sent without confirmation.",
    "Aviso: não foi possível ajustar ": "Warning: couldn't adjust ",
    " enviado sem ACK.": " sent without ACK.",
    "Aviso: não foi possível enviar DO_CHANGE_SPEED (": "Warning: couldn't send DO_CHANGE_SPEED (",
    "ERRO: não recebi GLOBAL_POSITION_INT do PX4.": "ERROR: didn't receive GLOBAL_POSITION_INT from PX4.",
    "ERRO: não recebi ATTITUDE do PX4 para calcular a frente do drone.": "ERROR: didn't receive ATTITUDE from PX4 to compute the drone's heading.",
    "Destino acima da altitude atual. ": "Destination is above the current altitude. ",
    "Ajustando subida para ": "Adjusting climb to ",
    "Enviando DO_REPOSITION (": "Sending DO_REPOSITION (",
    "destino final": "final destination",
    "subida vertical": "vertical climb",
    "Subida vertical: alt_rel=": "Vertical climb: alt_rel=",
    ", alvo=": ", target=",
    ", tolerância=": ", tolerance=",
    "Altitude alvo atingida: alt_rel=": "Target altitude reached: alt_rel=",
    "Aviso: não recebi altitude durante a subida vertical.": "Warning: didn't receive altitude during the vertical climb.",
    "Aviso: subida vertical não atingiu a altitude alvo dentro do tempo. ": "Warning: the vertical climb didn't reach the target altitude in time. ",
    "Última alt_rel=": "Last alt_rel=",
    "Destino com subida detectado. ": "Destination with a climb detected. ",
    "Executando primeiro uma subida vertical antes do deslocamento horizontal. ": "Doing a vertical climb first, before the horizontal move. ",
    "Aviso: ACK da subida vertical não foi recebido. Continuando a espera de altitude.": "Warning: the vertical-climb ACK wasn't received. Continuing to wait for altitude.",
    "Aviso: PX4 rejeitou a subida vertical separada. O destino final ainda sera enviado.": "Warning: PX4 rejected the separate vertical climb. The final destination will still be sent.",
    "Destino calculado: ": "Destination computed: ",
    "rotação_rel=": "rel_rotation=",
    "direção_destino=": "destination_heading=",
    "yaw_comando=ignorado, distância=": "yaw_command=ignored, distance=",
    "alt_relativa_alvo=": "target_rel_alt=",
    "vel_subida=": "climb_speed=",
    ", vel_horizontal=": ", horizontal_speed=",
    "Enviando 1a tentativa de DO_REPOSITION...": "Sending 1st DO_REPOSITION attempt...",
    "ERRO: destino enviado, mas ACK de DO_REPOSITION não foi recebido (1a tentativa).": "ERROR: destination sent, but the DO_REPOSITION ACK wasn't received (1st attempt).",
    "ERRO: PX4 rejeitou o primeiro DO_REPOSITION.": "ERROR: PX4 rejected the first DO_REPOSITION.",
    "Reenviando DO_REPOSITION para garantir a altitude configurada...": "Resending DO_REPOSITION to make sure the configured altitude is applied...",
    "ERRO: destino enviado, mas ACK de DO_REPOSITION não foi recebido (2a tentativa).": "ERROR: destination sent, but the DO_REPOSITION ACK wasn't received (2nd attempt).",
    "ERRO: PX4 rejeitou o DO_REPOSITION na 2a tentativa (altitude pode não ter sido aplicada).": "ERROR: PX4 rejected the DO_REPOSITION on the 2nd attempt (altitude may not have been applied).",
}


def _uavedusim_translate_script_text(text):
    """Traduz (por substituição de trechos fixos) as mensagens de
    diagnóstico impressas pelo script MAVLink gerado e executado como
    subprocesso — essas linhas só aparecem no arquivo de log/terminal,
    nunca na janela do simulador, mas ainda assim são traduzidas quando
    o idioma escolhido e inglês."""
    if _UAVEDUSIM_LANG["code"] != "en":
        return text
    for pt, en in _UAVEDUSIM_SCRIPT_I18N_EN.items():
        text = text.replace(pt, en)
    return text


# --- Monkey-patches ---------------------------------------------------------

_uavedusim_orig_label_init = tk.Label.__init__
def _uavedusim_patched_label_init(self, *args, **kwargs):
    _uavedusim_orig_label_init(self, *args, **_uavedusim_translate_kwargs(dict(kwargs)))
tk.Label.__init__ = _uavedusim_patched_label_init

_uavedusim_orig_button_init = tk.Button.__init__
def _uavedusim_patched_button_init(self, *args, **kwargs):
    _uavedusim_orig_button_init(self, *args, **_uavedusim_translate_kwargs(dict(kwargs)))
tk.Button.__init__ = _uavedusim_patched_button_init

_uavedusim_orig_ttk_label_init = ttk.Label.__init__
def _uavedusim_patched_ttk_label_init(self, *args, **kwargs):
    _uavedusim_orig_ttk_label_init(self, *args, **_uavedusim_translate_kwargs(dict(kwargs)))
ttk.Label.__init__ = _uavedusim_patched_ttk_label_init

_uavedusim_orig_ttk_button_init = ttk.Button.__init__
def _uavedusim_patched_ttk_button_init(self, *args, **kwargs):
    _uavedusim_orig_ttk_button_init(self, *args, **_uavedusim_translate_kwargs(dict(kwargs)))
ttk.Button.__init__ = _uavedusim_patched_ttk_button_init

_uavedusim_orig_misc_configure = tk.Misc.configure
def _uavedusim_patched_misc_configure(self, cnf=None, **kwargs):
    if cnf is not None and isinstance(cnf, dict):
        cnf = _uavedusim_translate_kwargs(dict(cnf))
    kwargs = _uavedusim_translate_kwargs(dict(kwargs))
    if cnf is None:
        return _uavedusim_orig_misc_configure(self, **kwargs)
    return _uavedusim_orig_misc_configure(self, cnf, **kwargs)
tk.Misc.configure = _uavedusim_patched_misc_configure
tk.Misc.config = _uavedusim_patched_misc_configure

_uavedusim_orig_create_text = tk.Canvas.create_text
def _uavedusim_patched_create_text(self, *args, **kwargs):
    return _uavedusim_orig_create_text(self, *args, **_uavedusim_translate_kwargs(dict(kwargs)))
tk.Canvas.create_text = _uavedusim_patched_create_text

_uavedusim_orig_itemconfigure = tk.Canvas.itemconfigure
def _uavedusim_patched_itemconfigure(self, tagOrId, cnf=None, **kwargs):
    kwargs = _uavedusim_translate_kwargs(dict(kwargs))
    if cnf is not None and isinstance(cnf, dict):
        cnf = _uavedusim_translate_kwargs(dict(cnf))
        return _uavedusim_orig_itemconfigure(self, tagOrId, cnf, **kwargs)
    return _uavedusim_orig_itemconfigure(self, tagOrId, **kwargs)
tk.Canvas.itemconfigure = _uavedusim_patched_itemconfigure
tk.Canvas.itemconfig = _uavedusim_patched_itemconfigure

_uavedusim_orig_stringvar_set = tk.StringVar.set
def _uavedusim_patched_stringvar_set(self, value):
    return _uavedusim_orig_stringvar_set(self, _uavedusim_translate(value))
tk.StringVar.set = _uavedusim_patched_stringvar_set

_uavedusim_orig_text_insert = tk.Text.insert
def _uavedusim_patched_text_insert(self, index, chars, *args, **kwargs):
    return _uavedusim_orig_text_insert(self, index, _uavedusim_translate(chars), *args, **kwargs)
tk.Text.insert = _uavedusim_patched_text_insert


class ModernScrollbar(tk.Frame):
    """Scrollbar customizada com trilho fino e miniatura ("thumb")
    arredondada e flutuante, para um visual mais moderno e fluido do
    que o scrollbar padrão do Tk (sempre reto, quadrado e com setas
    nas pontas).

    Compatível o suficiente com tk.Scrollbar/ttk.Scrollbar para ser
    usada como substituto direto nos pontos existentes do app:
    - aceita `command=` no construtor ou via `.config(command=...)`
    - expoe `.set(lo, hi)`, usado como `yscrollcommand`
    - suporta `.pack()` / `.grid()` / `.pack_forget()` normalmente
    """

    def __init__(self, parent, command=None, width=10, bg=SURFACE,
                 thumb=BORDER, thumb_active=MUTED, min_thumb=24, **kwargs):
        super().__init__(parent, bg=bg, width=width, highlightthickness=0, bd=0)
        self.pack_propagate(False)
        self.grid_propagate(False)

        self._command = command
        self._thumb_color = thumb
        self._thumb_active = thumb_active
        self._min_thumb = min_thumb
        self._lo, self._hi = 0.0, 1.0
        self._dragging = False
        self._drag_offset = 0
        self._hover = False
        self._base_w = width
        self._hover_w = width + 3
        self._thumb_bounds = (0, 0)

        self.canvas = tk.Canvas(self, bg=bg, highlightthickness=0, bd=0)
        self.canvas.pack(fill="both", expand=True)

        self.canvas.bind("<Configure>", lambda _e: self._redraw())
        self.canvas.bind("<Button-1>", self._on_press)
        self.canvas.bind("<B1-Motion>", self._on_drag)
        self.canvas.bind("<ButtonRelease-1>", self._on_release)
        self.canvas.bind("<Enter>", lambda _e: self._set_hover(True))
        self.canvas.bind("<Leave>", lambda _e: self._set_hover(False))

    # -- API compatível com Scrollbar ---------------------------------
    def config(self, **kw):
        if "command" in kw:
            self._command = kw.pop("command")
        if kw:
            super().config(**kw)

    configure = config

    def set(self, lo, hi):
        try:
            lo, hi = float(lo), float(hi)
        except (TypeError, ValueError):
            return
        self._lo, self._hi = max(0.0, lo), min(1.0, hi)
        self._redraw()

    # -- desenho -------------------------------------------------------
    def _redraw(self):
        c = self.canvas
        c.delete("thumb")
        w = c.winfo_width()
        h = c.winfo_height()
        if w <= 1 or h <= 1:
            return

        active = self._hover or self._dragging
        thumb_w = max(4, min(w - 2, (self._hover_w if active else self._base_w) - 4))
        x1 = (w - thumb_w) / 2
        x2 = x1 + thumb_w

        span = max(0.0, self._hi - self._lo)
        thumb_h = max(self._min_thumb, int(h * span)) if span < 1 else h
        y1 = int(h * self._lo)
        y2 = min(h, y1 + thumb_h)
        if y2 - y1 < self._min_thumb:
            y1 = max(0, y2 - self._min_thumb)

        color = self._thumb_active if active else self._thumb_color
        self._draw_round_rect(x1, y1, x2, y2, thumb_w / 2, fill=color, tags="thumb")
        self._thumb_bounds = (y1, y2)

    def _draw_round_rect(self, x1, y1, x2, y2, r, **kw):
        r = max(1, min(r, (x2 - x1) / 2, (y2 - y1) / 2))
        pts = [
            x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r,
            x2, y2 - r, x2, y2, x2 - r, y2, x1 + r, y2,
            x1, y2, x1, y2 - r, x1, y1 + r, x1, y1,
        ]
        return self.canvas.create_polygon(pts, smooth=True, splinesteps=12, outline="", **kw)

    # -- interação -------------------------------------------------------
    def _set_hover(self, state):
        if self._hover != state:
            self._hover = state
            self._redraw()

    def _on_press(self, event):
        y1, y2 = self._thumb_bounds
        if y1 <= event.y <= y2:
            self._dragging = True
            self._drag_offset = event.y - y1
        else:
            h = self.canvas.winfo_height()
            span = max(0.0, self._hi - self._lo)
            frac = event.y / max(h, 1)
            if self._command:
                self._command("moveto", max(0.0, min(1.0 - span, frac - span / 2)))
            self._dragging = True
            self._drag_offset = (y2 - y1) / 2 if y2 > y1 else 0
        self._redraw()

    def _on_drag(self, event):
        if not self._dragging or not self._command:
            return
        h = self.canvas.winfo_height()
        if h <= 1:
            return
        thumb_h = max(self._hi - self._lo, 0.0) * h
        new_y1 = event.y - self._drag_offset
        span_px = max(h - thumb_h, 1)
        frac = max(0.0, min(1.0, new_y1 / span_px))
        self._command("moveto", frac)

    def _on_release(self, _event):
        self._dragging = False
        self._redraw()


def _uavedusim_round_rect_points(x1, y1, x2, y2, r):
    """Gera os pontos de um polígono suavizado que, com smooth=True no
    Canvas, desenha um retângulo de cantos arredondados. Usado por
    RoundedPanel e RoundedButton para contornar a falta de
    border-radius nativo em Frame/Button do tkinter.
    """
    r = max(0, min(r, (x2 - x1) / 2, (y2 - y1) / 2))
    return [
        x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r,
        x2, y2 - r, x2, y2, x2 - r, y2, x1 + r, y2,
        x1, y2, x1, y2 - r, x1, y1 + r, x1, y1,
    ]


class RoundedPanel(tk.Frame):
    """Painel/card com cantos arredondados. Quando o Pillow está
    disponível, o fundo é renderizado como uma imagem com
    antialiasing (supersampling), igual ao protótipo de referência;
    sem Pillow, cai para um polígono arredondado simples do próprio
    Canvas (mais quadriculado nas bordas, mas ainda funcional).

    Os widgets filhos devem ser adicionados dentro de `self.body`, um
    Frame comum com a mesma cor de fundo do painel. A altura do painel
    acompanha automaticamente a altura de `self.body` (útil para caixas
    de texto que quebram linha de forma responsiva); a largura segue o
    espaço que o geometry manager (pack/grid) der ao painel.
    """

    def __init__(self, parent, outer_bg, fill, radius=10, border=None, border_width=1,
                 auto_height=True, **kwargs):
        super().__init__(parent, bg=outer_bg, highlightthickness=0, bd=0, **kwargs)
        self._radius = radius
        self._fill = fill
        self._border = border
        self._border_width = border_width if border else 0
        self._auto_height = auto_height
        self._bg_item = None
        self._photo = None

        self.canvas = tk.Canvas(self, bg=outer_bg, highlightthickness=0, bd=0)
        self.canvas.pack(fill="both", expand=True)

        self.body = tk.Frame(self.canvas, bg=fill)
        self._window = self.canvas.create_window(0, 0, window=self.body, anchor="nw")

        self.canvas.bind("<Configure>", self._redraw)
        if self._auto_height:
            self.body.bind("<Configure>", self._sync_height)

    def _sync_height(self, _event=None):
        try:
            inset = max(self._radius, self._border_width)
            req_h = self.body.winfo_reqheight()
            self.canvas.configure(height=req_h + inset * 2)
        except Exception:
            pass

    def _redraw(self, _event=None):
        c = self.canvas
        w = c.winfo_width()
        h = c.winfo_height()
        if w <= 1 or h <= 1:
            return
        r = min(self._radius, w / 2, h / 2)
        bw = self._border_width

        if HAS_PIL:
            self._photo = _uavedusim_make_rounded_solid_photo(
                w, h, r, self._fill, outline=self._border, outline_width=bw,
            )
            if self._bg_item is None:
                self._bg_item = c.create_image(0, 0, image=self._photo, anchor="nw")
            else:
                c.itemconfigure(self._bg_item, image=self._photo)
            c.tag_lower(self._bg_item)
        else:
            c.delete("bgshape")
            pts = _uavedusim_round_rect_points(bw / 2, bw / 2, w - bw / 2, h - bw / 2, r)
            c.create_polygon(
                pts, fill=self._fill, outline=(self._border or ""), width=bw,
                smooth=True, splinesteps=16, tags="bgshape",
            )
            c.tag_lower("bgshape")

        inset = max(r, bw)
        c.coords(self._window, inset, inset)
        c.itemconfigure(self._window, width=max(1, w - inset * 2))
        if not self._auto_height:
            # Painéis "não auto-altura" (ex.: o card principal da tela)
            # preenchem toda a altura que o próprio pai (pack/grid) já
            # deu ao painel, em vez de tentar se ajustar ao conteúdo.
            c.itemconfigure(self._window, height=max(1, h - inset * 2))


class RoundedButton(tk.Frame):
    """Botão com cantos arredondados. Igual ao RoundedPanel, usa
    imagem Pillow antialiased quando disponível (com fallback para
    polígono simples do Canvas). Suporta ícone opcional ao lado do
    rótulo, cores por estado (normal / hover / pressionado) e, opcionalmente,
    um preenchimento em degradê (usado no botão "Iniciar simulação").
    """

    def __init__(self, parent, text, command=None, outer_bg=None,
                 fill=ACCENT, fill_hover=None, fill_press=None,
                 text_color="#11111b", text_color_hover=None,
                 radius=8, height=40, width=120,
                 font=None, icon=None, icon_font=None, icon_color=None, gradient=None, **kwargs):
        outer_bg = outer_bg if outer_bg is not None else fill
        super().__init__(parent, bg=outer_bg, highlightthickness=0, bd=0,
                          height=height, width=width, **kwargs)
        self.pack_propagate(False)

        self._command = command
        self._radius = radius
        self._fill = fill
        self._fill_hover = fill_hover or fill
        self._fill_press = fill_press or fill
        self._gradient = gradient
        self._text = text
        self._text_color = text_color
        self._text_color_hover = text_color_hover or text_color
        self._icon = icon
        self._icon_color = icon_color
        self._font = font or ("TkDefaultFont", 10, "bold")
        self._icon_font = icon_font or self._font
        self._state = "normal"
        self._bg_item = None
        self._photo = None
        self.enabled = True

        self.canvas = tk.Canvas(self, bg=outer_bg, highlightthickness=0, bd=0, cursor="hand2")
        self.canvas.pack(fill="both", expand=True)

        self.canvas.bind("<Configure>", self._redraw)
        self.canvas.bind("<Enter>", lambda _e: self._set_state("hover"))
        self.canvas.bind("<Leave>", lambda _e: self._set_state("normal"))
        self.canvas.bind("<ButtonPress-1>", lambda _e: self._set_state("press"))
        self.canvas.bind("<ButtonRelease-1>", self._on_release)

    def _set_state(self, state):
        if not self.enabled:
            return
        if self._state != state:
            self._state = state
            self._redraw()

    def _on_release(self, _event):
        was_press = self._state == "press"
        self._set_state("hover")
        if was_press and self.enabled and self._command:
            self._command()

    def set_text(self, text, height=None):
        """Atualiza o texto (aceita \\n para múltiplas linhas) e,
        opcionalmente, a altura do botão — usado pelo reflow responsivo
        que quebra o texto em telas estreitas.
        """
        self._text = text
        if height is not None:
            self.configure(height=height)
        self._redraw()

    def set_enabled(self, enabled):
        """Habilita/desabilita o botão visualmente (cursor + cor
        esmaecida), equivalente ao .configure(state=...) do tk.Button.
        """
        self.enabled = bool(enabled)
        try:
            self.canvas.configure(cursor=("hand2" if self.enabled else "arrow"))
        except Exception:
            pass
        self._redraw()

    def _redraw(self, _event=None):
        c = self.canvas
        w = c.winfo_width()
        h = c.winfo_height()
        if w <= 1 or h <= 1:
            return

        active = self._state in ("hover", "press")
        text_color = self._text_color_hover if active else self._text_color
        if not self.enabled:
            text_color = DISABLED_FG
        r = min(self._radius, w / 2, h / 2)

        if HAS_PIL:
            if self._gradient and self.enabled:
                c1, c2 = self._gradient
                if active:
                    c1 = _uavedusim_blend(c1, "#ffffff", 0.10)
                    c2 = _uavedusim_blend(c2, "#ffffff", 0.10)
                self._photo = _uavedusim_make_rounded_gradient_photo(w, h, r, c1, c2)
            else:
                if not self.enabled:
                    fill = DISABLED_BG
                else:
                    fill = {
                        "normal": self._fill, "hover": self._fill_hover, "press": self._fill_press,
                    }.get(self._state, self._fill)
                self._photo = _uavedusim_make_rounded_solid_photo(w, h, r, fill)

            if self._bg_item is None:
                self._bg_item = c.create_image(0, 0, image=self._photo, anchor="nw")
            else:
                c.itemconfigure(self._bg_item, image=self._photo)
            c.tag_lower(self._bg_item)
        else:
            c.delete("bgshape")
            if self._gradient and self.enabled:
                c1, c2 = self._gradient
                if active:
                    c1 = _uavedusim_blend(c1, "#ffffff", 0.10)
                    c2 = _uavedusim_blend(c2, "#ffffff", 0.10)
                steps = max(int(w) // 3, 1)
                for i in range(steps):
                    t = i / max(steps - 1, 1)
                    color = _uavedusim_blend(c1, c2, t)
                    x0 = i * w / steps
                    x1 = (i + 1) * w / steps
                    c.create_rectangle(x0, 0, x1, h, fill=color, outline="", tags="bgshape")
            else:
                if not self.enabled:
                    fill = DISABLED_BG
                else:
                    fill = {
                        "normal": self._fill, "hover": self._fill_hover, "press": self._fill_press,
                    }.get(self._state, self._fill)
                pts = _uavedusim_round_rect_points(0, 0, w, h, r)
                c.create_polygon(pts, fill=fill, outline="", smooth=True, splinesteps=16, tags="bgshape")
            c.tag_lower("bgshape")

        c.delete("fgtext")
        cx, cy = w / 2, h / 2

        if self._icon:
            gap = max(4, int(h * 0.16))
            icon_color = self._icon_color if self._icon_color is not None else text_color
            icon_id = c.create_text(0, cy, text=self._icon, fill=icon_color, font=self._icon_font, anchor="w", tags="fgtext")
            text_id = c.create_text(0, cy, text=self._text, fill=text_color, font=self._font, anchor="w", tags="fgtext")
            icon_w = c.bbox(icon_id)[2] - c.bbox(icon_id)[0]
            text_w = c.bbox(text_id)[2] - c.bbox(text_id)[0]
            start_x = cx - (icon_w + gap + text_w) / 2
            c.coords(icon_id, start_x, cy)
            c.coords(text_id, start_x + icon_w + gap, cy)
        else:
            c.create_text(cx, cy, text=self._text, fill=text_color, font=self._font, anchor="center", tags="fgtext")


def _uavedusim_footer_close_button(app, parent, command, width=None):
    """Botão "Fechar" no formato de pílula redonda, em vermelho (Red da
    paleta Catppuccin). Usado no rodapé de todas as telas do app.
    """
    return RoundedButton(
        parent,
        text="Fechar",
        command=command,
        outer_bg=SURFACE,
        fill=DANGER,
        fill_hover=_uavedusim_blend(DANGER, "#ffffff", 0.18),
        fill_press=_uavedusim_blend(DANGER, "#000000", 0.18),
        text_color="#11111b",
        text_color_hover="#11111b",
        radius=app.px(11),
        height=app.px(37),
        width=width if width is not None else app.px(100),
        font=(app.FONT_FAMILY, app.fs(11), "bold"),
    )


def _uavedusim_footer_back_button(app, parent, command, text="Voltar", width=None):
    """Botão "Voltar" no formato de pílula redonda, em azul (Blue da
    paleta Catppuccin) — cor diferente do "Fechar" pra ficar evidente.
    Usado no rodapé de todas as telas do app.
    """
    return RoundedButton(
        parent,
        text=text,
        icon=ICONS["voltar"],
        command=command,
        outer_bg=SURFACE,
        fill=BLUE,
        fill_hover=_uavedusim_blend(BLUE, "#ffffff", 0.18),
        fill_press=_uavedusim_blend(BLUE, "#000000", 0.18),
        text_color="#11111b",
        text_color_hover="#11111b",
        radius=app.px(11),
        height=app.px(37),
        width=width if width is not None else app.px(110),
        font=(app.FONT_FAMILY, app.fs(11), "bold"),
    )


def _uavedusim_footer_primary_button(app, parent, command, text, width=None, icon=None):
    """Botão de ação principal (Selecionar/Aplicar) no mesmo formato de
    pílula redonda, em mauve sólido (mesma cor de destaque usada no
    resto do app, sem degradê).
    """
    return RoundedButton(
        parent,
        text=text,
        icon=icon,
        command=command,
        outer_bg=SURFACE,
        fill=ACCENT,
        fill_hover="#ddc5fb",
        fill_press=ACCENT_DIM,
        text_color="#11111b",
        text_color_hover="#11111b",
        radius=app.px(11),
        height=app.px(37),
        width=width if width is not None else app.px(140),
        font=(app.FONT_FAMILY, app.fs(11), "bold"),
    )


class _AttackInfoTooltip:
    """Balão de informação (hover ou clique) para o ícone 'i' de cada ataque.

    O balão é desenhado como um widget dentro da própria janela do app
    (via place()), e não como uma janela separada do sistema operacional.
    Isso garante que ele sempre aparece dentro dos limites da janela do
    UAVEDUSIM, próximo do ataque que o usuário esta consultando.
    """

    _active = None  # garante um único balão visível por vez

    def __init__(self, widget, text, app):
        self.widget = widget
        self.text = text
        self.app = app
        self._tip = None
        self._after_id = None
        widget.bind("<Enter>", self._schedule_show, add="+")
        widget.bind("<Leave>", self._on_leave, add="+")
        widget.bind("<Button-1>", self._on_click, add="+")
        widget.bind("<Destroy>", self._on_destroy, add="+")

    def _schedule_show(self, _event=None):
        self._cancel_schedule()
        self._after_id = self.widget.after(300, self._show)

    def _cancel_schedule(self):
        if self._after_id is not None:
            try:
                self.widget.after_cancel(self._after_id)
            except Exception:
                pass
            self._after_id = None

    def _on_leave(self, _event=None):
        self._cancel_schedule()
        self._hide()

    def _on_click(self, _event=None):
        self._cancel_schedule()
        if self._tip is not None:
            self._hide()
        else:
            self._show()
        return "break"

    def _on_destroy(self, _event=None):
        self._cancel_schedule()
        self._hide()

    def _show(self):
        self._after_id = None
        if self._tip is not None:
            return

        if _AttackInfoTooltip._active is not None and _AttackInfoTooltip._active is not self:
            _AttackInfoTooltip._active._hide()

        app = self.app
        root = getattr(app, "root", None)
        if root is None:
            return

        try:
            self.widget.update_idletasks()
            root.update_idletasks()
            icon_x = self.widget.winfo_rootx() - root.winfo_rootx()
            icon_y = self.widget.winfo_rooty() - root.winfo_rooty()
            icon_w = self.widget.winfo_width()
            icon_h = self.widget.winfo_height()
        except Exception:
            return

        # Balão construido como filho direto de "root" (a janela do app),
        # posicionado com place() em coordenadas relativas a própria janela.
        tip = tk.Frame(root, bg=BORDER, bd=0, highlightthickness=0)

        inner = tk.Frame(tip, bg="#233241", bd=0)
        inner.pack(fill="both", expand=True, padx=1, pady=1)

        tk.Label(
            inner,
            text=self.text,
            bg="#233241",
            fg=TEXT,
            font=(app.FONT_FAMILY, getattr(app, "FS_TINY", 8)),
            justify="left",
            anchor="w",
            wraplength=app.px(260),
            padx=app.px(10),
            pady=app.px(8),
        ).pack()

        tip.update_idletasks()
        tip_w = tip.winfo_reqwidth()
        tip_h = tip.winfo_reqheight()

        root_w = max(root.winfo_width(), 1)
        root_h = max(root.winfo_height(), 1)
        margin = app.px(8)

        # Centraliza horizontalmente sob o ícone, sem ultrapassar as bordas da janela.
        pos_x = icon_x + icon_w // 2 - tip_w // 2
        pos_x = max(margin, min(pos_x, root_w - tip_w - margin))

        # Prefere exibir abaixo do ícone; se não couber, exibe acima dele.
        pos_y = icon_y + icon_h + app.px(6)
        if pos_y + tip_h > root_h - margin:
            pos_y = icon_y - tip_h - app.px(6)
        pos_y = max(margin, min(pos_y, root_h - tip_h - margin))

        tip.place(x=pos_x, y=pos_y, width=tip_w, height=tip_h)
        tip.lift()

        # Fecha o balão automaticamente se a janela for redimensionada,
        # para evitar que ele fique posicionado incorretamente. Guarda o
        # tamanho atual para ignorar eventos <Configure> "espurios" que não
        # representam uma mudanca real de tamanho da janela.
        self._shown_size = (root_w, root_h)
        if not getattr(root, "_attack_tooltip_bound", False):
            root.bind("<Configure>", _uavedusim_hide_active_attack_tooltip, add="+")
            root._attack_tooltip_bound = True

        self._tip = tip
        _AttackInfoTooltip._active = self

    def _hide(self):
        if self._tip is not None:
            try:
                self._tip.destroy()
            except Exception:
                pass
            self._tip = None
        if _AttackInfoTooltip._active is self:
            _AttackInfoTooltip._active = None


def _uavedusim_hide_active_attack_tooltip(event=None):
    active = _AttackInfoTooltip._active
    if active is None:
        return
    if event is not None:
        # Em Tkinter, um bind de <Configure> feito diretamente na janela raiz
        # também é acionado por eventos <Configure> de QUALQUER widget filho
        # (o pathname da janela raiz faz parte das bindtags de todos os seus
        # descendentes, incluindo o próprio balão recem-criado). Por isso,
        # só tratamos o evento se ele vier realmente da janela raiz.
        root = getattr(active.app, "root", None)
        if root is not None and event.widget is not root:
            return
        new_size = (getattr(event, "width", None), getattr(event, "height", None))
        if new_size == getattr(active, "_shown_size", None):
            return  # tamanho não mudou de fato; ignora o evento espurio
    active._hide()


def _uavedusim_wrap_monospace_text(app, text, container, font_spec=None, fallback_width=None):
    """Quebra `text` em várias linhas (com \\n) pra caber na largura
    atual de `container`, medindo o texto uma única vez — não fica
    escutando eventos de redimensionamento, então não tem como entrar
    num ciclo de "quebra -> muda tamanho -> quebra de novo".

    Só funciona bem com fonte monoespaçada (é o caso do app inteiro,
    via UAVEDUSIM_FONT_FAMILY), já que o cálculo é feito em caracteres.
    """
    text = str(text or "")

    if not text.strip():
        return text

    try:
        app.root.update_idletasks()
        width_px = container.winfo_width()
    except Exception:
        width_px = 0

    if width_px <= 10:
        width_px = fallback_width if fallback_width is not None else app.px(420)

    font_spec = font_spec or (app.FONT_FAMILY, app.FS_SMALL)

    try:
        f = _uj_tkfont.Font(font=font_spec)
        char_w = max(1.0, f.measure("M" * 20) / 20)
    except Exception:
        char_w = 7.0

    usable_px = max(1, width_px - app.px(24))
    chars_per_line = max(20, int(usable_px / char_w))

    wrapped_paragraphs = []
    for paragraph in text.split("\n"):
        if paragraph.strip():
            wrapped_paragraphs.append(
                _uj_textwrap.fill(paragraph, width=chars_per_line, break_long_words=False)
            )
        else:
            wrapped_paragraphs.append("")

    return "\n".join(wrapped_paragraphs)


def _uavedusim_make_info_icon(app, parent, attack_key):
    """Cria o ícone 'i' (canto do card do ataque) com balão de informação."""
    size = app.px(18)
    icon = tk.Canvas(
        parent,
        width=size,
        height=size,
        bg=SURFACE2,
        highlightthickness=0,
        cursor="hand2",
    )
    pad = 1
    icon.create_oval(pad, pad, size - pad, size - pad, outline=TAG_FG, width=1)
    icon.create_text(
        size / 2,
        size / 2,
        text="i",
        fill=TAG_FG,
        font=(app.FONT_FAMILY, app.fs(9), "bold"),
    )
    text = ATTACK_INFO_TEXT.get(attack_key, "")
    _AttackInfoTooltip(icon, text, app)
    return icon


ACTION_ICON_PATTERNS = (
    ("executar", ICONS["executar"]),
    ("takeoff", ICONS["executar"]),
    ("limpar", ICONS["limpar"]),
    ("reiniciar", ICONS["limpar"]),
    ("voltar", ICONS["voltar"]),
    ("sair", ICONS["sair"]),
)

ATTACK_ICON_PATTERNS = (
    ("ataque", ICONS["ataque"]),
)


# Define o ícone exibido para cada opção da interface.
def icon_for(label):
    text = str(label or "")
    low = text.lower()
    first_line = low.splitlines()[0].strip()
    normalized = first_line.replace("-", "_").replace(" ", "_")

    for pattern, icon in ACTION_ICON_PATTERNS:
        if pattern in normalized:
            return icon

    for pattern, icon in ATTACK_ICON_PATTERNS:
        if pattern in normalized:
            return icon

    return "◦"

# Registra mensagens no terminal e no arquivo de log.
def log(msg: str) -> None:
    stamp = time.strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{stamp}] {msg}"
    print(line, flush=True)
    try:
        with LOG.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass

# Escapa strings usadas em comandos shell.

def _uavedusim_safe_yview_scroll(widget, number, what):
    """Executa yview_scroll sem quebrar se o Canvas já tiver sido destruído."""
    try:
        if widget is None:
            return "break"

        if not widget.winfo_exists():
            return "break"

        widget.yview_scroll(number, what)
    except tk.TclError:
        return "break"
    except Exception:
        return "break"

    return "break"


def _uavedusim_make_scrollable_canvas(app, parent):
    """Cria um Canvas com scroll vertical (ModernScrollbar), no padrão
    comum a todas as telas educativas/de seleção desenhadas em Canvas
    (O que é jamming, Ataques, Mitigações, Modelo, Mundo).

    Retorna (canvas, scrollbar). O scrollbar já sai configurado e
    ligado ao canvas, mas não é posicionado (pack/place) aqui — cada
    tela decide isso sozinha, já que umas mostram/escondem o scrollbar
    com pack() e outras com place() (visual ligeiramente diferente).
    """
    canvas = tk.Canvas(parent, bg=SURFACE, bd=0, highlightthickness=0)
    scrollbar = ModernScrollbar(
        parent,
        command=canvas.yview,
        width=app.SCROLLBAR_W,
        bg=SURFACE,
        thumb=BORDER,
        thumb_active=MUTED,
    )
    canvas.configure(yscrollcommand=scrollbar.set)
    canvas.pack(side="left", fill="both", expand=True)
    return canvas, scrollbar


def _uavedusim_bind_canvas_wheel_scroll(canvas, is_scroll_enabled):
    """Liga a roda do mouse (e Button-4/5, usado no Linux) a um Canvas
    com scroll, só quando `is_scroll_enabled()` retornar True. Mesmo
    padrão repetido em todas as telas em Canvas com scroll.
    """
    def wheel(event):
        if not is_scroll_enabled():
            return "break"

        if getattr(event, "num", None) == 4:
            _uavedusim_safe_yview_scroll(canvas, -3, "units")
        elif getattr(event, "num", None) == 5:
            _uavedusim_safe_yview_scroll(canvas, 3, "units")
        else:
            _uavedusim_safe_yview_scroll(canvas, int(-1 * (getattr(event, "delta", 0) / 120)), "units")

        return "break"

    canvas.bind(
        "<Enter>",
        lambda _e: (
            canvas.bind_all("<MouseWheel>", wheel),
            canvas.bind_all("<Button-4>", wheel),
            canvas.bind_all("<Button-5>", wheel),
        ),
    )
    canvas.bind(
        "<Leave>",
        lambda _e: (
            canvas.unbind_all("<MouseWheel>"),
            canvas.unbind_all("<Button-4>"),
            canvas.unbind_all("<Button-5>"),
        ),
    )

    return wheel


def shell_quote(value: str) -> str:
    return "'" + value.replace("'", "'\\''") + "'"

# Executa comandos externos e grava a saída no log.
def run_cmd(args, cwd=None, check=False, capture=True):
    log("Executando: " + " ".join(map(str, args)))
    proc = subprocess.run(
        list(map(str, args)),
        cwd=str(cwd) if cwd else None,
        text=True,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.STDOUT if capture else None,
    )
    if capture and proc.stdout:
        for line in proc.stdout.splitlines():
            log(line)
    log(f"Código de retorno: {proc.returncode}")
    if check and proc.returncode != 0:
        raise RuntimeError(f"Comando falhou: {' '.join(map(str,args))}")
    return proc

# Le o trecho final do log para diagnóstico rápido.
def read_recent_log(max_bytes=500000) -> str:
    try:
        size = LOG.stat().st_size
        with LOG.open("rb") as f:
            if size > max_bytes:
                f.seek(size - max_bytes)
            return f.read().decode("utf-8", errors="replace")
    except Exception:
        return ""

# Le o log a partir de um offset conhecido.
def read_log_since(offset=0, max_bytes=700000) -> str:
    try:
        size = LOG.stat().st_size
        offset = int(offset or 0)
        offset = max(0, min(offset, size))

        with LOG.open("rb") as f:
            if size - offset > max_bytes:
                f.seek(size - max_bytes)
            else:
                f.seek(offset)
            return f.read().decode("utf-8", errors="replace")
    except Exception:
        return ""

# Remove códigos de cor e controle dos logs do terminal.
def strip_ansi(text: str) -> str:
    if not text:
        return ""

    # Normaliza logs removendo códigos ANSI do terminal.
    text = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", text)
    text = re.sub(r"\x1b\][^\x07]*(?:\x07|\x1b\\)", "", text)
    text = text.replace("\r", "\n")
    return text

# Lista processos ativos ignorando a própria interface.
def process_lines():
    try:
        proc = subprocess.run(
            ["ps", "-eo", "pid=,comm=,args="],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=3,
        )
    except Exception:
        return []

    if proc.returncode != 0:
        return []

    lines = []

    for raw in proc.stdout.splitlines():
        line = raw.strip()
        low = line.lower()

        if not line:
            continue

        if "uavedusim_gui_app.py" in low:
            continue

        lines.append(line)

    return lines

# Verifica se o PX4 SITL está em execução.
def process_indicates_px4_running():
    for line in process_lines():
        low = line.lower()

        if "px4_sitl_default/bin/px4" in line:
            return True, "PX4 em execução"

        if str(PX4_DIR / "build/px4_sitl_default/bin/px4") in line:
            return True, "PX4 em execução"

        if re.search(r"(^|[/\s])px4(\s|$)", line) and "px4-autopilot" in low:
            return True, "PX4 em execução"

    try:
        proc = subprocess.run(
            ["pgrep", "-x", "px4"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=2,
        )
        if proc.returncode == 0 and proc.stdout.strip():
            return True, "PX4 em execução"
    except Exception:
        pass

    return False, "PX4 ainda não detectado"

# Verifica se o Gazebo já foi aberto.
def process_indicates_gazebo_ready():
    gazebo_patterns = [
        "gz sim",
        "gzserver",
        "gzclient",
        "gz-gui",
        "gz gui",
        "ign gazebo",
        "gazebo sim",
        "gazebo-gui",
    ]

    for line in process_lines():
        low = line.lower()

        if any(pattern in low for pattern in gazebo_patterns):
            return True, "Gazebo aberto"

    return False, "Gazebo ainda não detectado"

# Detecta o Ready for takeoff no log do PX4.
def log_has_commander_ready(data: str):
    clean = strip_ansi(data)

    if not clean:
        return False

    low = clean.lower()
    compact = re.sub(r"\s+", "", low)

    # O PX4 pode colorir "Ready for takeoff!" com ANSI e o terminal pode quebrar
    # visualmente a palavra takeoff. Por isso procuramos também sem espacos.
    if "readyfortakeoff!" not in compact:
        return False

    # Evita disparar por textos externos. O evento esperado vem do commander.
    if "commander" not in low:
        return False

    return True

# Válida se o PX4 chegou ao estado pronto para decolar.
def log_has_takeoff_detected(data: str):
    clean = strip_ansi(data)

    if not clean:
        return False

    low = clean.lower()
    compact = re.sub(r"\s+", "", low)

    # O PX4 confirma que a decolagem realmente iniciou com:
    # INFO  [commander] Takeoff detected
    if "takeoffdetected" not in compact:
        return False

    if "commander" not in low:
        return False

    return True

def log_indicates_takeoff_detected(log_offset=0):
    data = read_log_since(log_offset, max_bytes=900000)

    if log_has_takeoff_detected(data):
        return True, "PX4 confirmou Takeoff detected"

    return False, "PX4 ainda não confirmou Takeoff detected"

def log_indicates_commander_ready(log_offset=0):
    data = read_log_since(log_offset)

    if not data:
        return False, "PX4 aguardando Ready for takeoff"

    clean = strip_ansi(data)
    low = clean.lower()

    # Procura o Ready no trecho de log da execução atual.
    if log_has_commander_ready(data):
        ready_pos = re.sub(r"\s+", "", low).rfind("readyfortakeoff!")

        if ready_pos >= 0:
            return True, "PX4 Ready for takeoff"

    # Fallback para casos em que o offset foi salvo tarde demais.
    # Como o log é recriado na abertura, o trecho recente pertence a sessão atual.
    recent = read_recent_log(900000)

    if log_offset and log_has_commander_ready(recent):
        return True, "PX4 Ready for takeoff"

    px4_running, _reason = process_indicates_px4_running()

    if px4_running:
        return False, "PX4 iniciado; aguardando Ready for takeoff"

    return False, "PX4 ainda não detectado"

# Confirma readiness combinando PX4 pronto e Gazebo aberto.
def simulation_ready(log_offset=0):
    gazebo_ok, gazebo_reason = process_indicates_gazebo_ready()
    px4_ok, px4_reason = log_indicates_commander_ready(log_offset)

    if gazebo_ok and px4_ok:
        return True, f"{gazebo_reason}; {px4_reason}"

    return False, f"{px4_reason} | {gazebo_reason}"

# Gera o bloco shell que carrega o ambiente do Gazebo.
def load_gazebo_env_block() -> str:
    return r'''load_gazebo_env() {
    local setup_file=""
    local source_status=0

    if [ -f "$HOME/gazebo_workspace/install/setup.bash" ]; then
        setup_file="$HOME/gazebo_workspace/install/setup.bash"
        echo "[UAVEDUSIM] Carregando Gazebo: $setup_file"
        set +u
        . "$setup_file"
        source_status=$?
        set -u
        if [ "$source_status" -ne 0 ]; then
            echo "[UAVEDUSIM] ERRO: falha ao carregar $setup_file"
            exit "$source_status"
        fi
    elif [ -f "$HOME/gazebo_workspace/install/setup.sh" ]; then
        setup_file="$HOME/gazebo_workspace/install/setup.sh"
        echo "[UAVEDUSIM] Carregando Gazebo: $setup_file"
        set +u
        . "$setup_file"
        source_status=$?
        set -u
        if [ "$source_status" -ne 0 ]; then
            echo "[UAVEDUSIM] ERRO: falha ao carregar $setup_file"
            exit "$source_status"
        fi
    elif [ -f "$HOME/gazebo_workspace/install/setup.zsh" ]; then
        setup_file="$HOME/gazebo_workspace/install/setup.zsh"
        echo "[UAVEDUSIM] Carregando Gazebo via zsh: $setup_file"
        if command -v zsh >/dev/null 2>&1; then
            while IFS= read -r -d "" env_line; do
                case "$env_line" in
                    *=*) export "$env_line" ;;
                esac
            done < <(zsh -lc "source \"$setup_file\" >/dev/null && env -0")
        else
            echo "[UAVEDUSIM] ERRO: encontrei setup.zsh, mas zsh não esta instalado."
            exit 1
        fi
    else
        echo "[UAVEDUSIM] Aviso: setup do gazebo_workspace não encontrado. Continuando com ambiente atual."
    fi

    export GZ_IP="${GZ_IP:-127.0.0.1}"
}
load_gazebo_env
'''

# Canvas customizado usado nas listas de seleção.
class ItemCanvas(tk.Canvas):
    def __init__(self, app, parent, **kw):
        super().__init__(parent, bg=SURFACE2, highlightthickness=0, bd=0, **kw)
        self.app = app
        self.opts = []
        self.selected_idx = 0
        self.hover_idx = None
        self.bind("<Configure>", self._redraw)
        self.bind("<ButtonPress-1>", self._on_click)
        self.bind("<Double-Button-1>", self._on_dbl)
        self.bind("<Motion>", self._on_motion)
        self.bind("<Leave>", self._on_leave)
        self.bind("<Up>", self._on_up)
        self.bind("<Down>", self._on_down)
        self.bind("<Return>", lambda _e: self.app.choose_current())
        self.focus_set()

    # Atualiza os itens da lista e reinicia a seleção.
    def set_options(self, opts):
        self.opts = list(opts)
        self.selected_idx = 0
        self.hover_idx = None
        self._draw()

    # Desenha linhas, ícones e destaque do item selecionado.
    def _redraw(self, _e=None):
        self._draw()

    # Converte a posição do mouse no indice da lista.
    # Seleciona um item com clique simples.
    def _on_click(self, e):
        i = self._idx_at(e.y)
        if i is not None:
            self.selected_idx = i
            self._draw()

    # Confirma a opção selecionada com duplo clique.
    def _on_dbl(self, e):
        i = self._idx_at(e.y)
        if i is not None:
            self.selected_idx = i
            self.app.choose_current()

    def _on_motion(self, e):
        i = self._idx_at(e.y)
        if i != self.hover_idx:
            self.hover_idx = i
            self._draw()

    def _on_leave(self, _e):
        if self.hover_idx is not None:
            self.hover_idx = None
            self._draw()

    def _on_up(self, _e):
        if self.selected_idx > 0:
            self.selected_idx -= 1
            self._ensure_visible(self.selected_idx)
            self._draw()

    def _on_down(self, _e):
        if self.selected_idx < len(self.opts) - 1:
            self.selected_idx += 1
            self._ensure_visible(self.selected_idx)
            self._draw()

    # Ajusta o scroll para manter o item selecionado visível.
# Janela principal da interface UAVEDUSIM.
# Ícones (Private Use Area, Nerd Fonts) dos botões da janela.
# Minimizar: nf-md-window_minimize / Maximizar: nf-md-window_maximize
# Restaurar: nf-md-window_restore / Fechar: nf-md-close
UAVEDUSIM_WIN_ICON_MINIMIZE = "\U000F05B0"
UAVEDUSIM_WIN_ICON_MAXIMIZE = "\uf2d0"
UAVEDUSIM_WIN_ICON_RESTORE = "\uf2d2"
UAVEDUSIM_WIN_ICON_CLOSE = "\U000F0156"


class UAVEDUSIMApp:
    def __init__(self):
        self.root = tk.Tk(className="UAVEDUSIM")
        self.root.title("UAVEDUSIM")
        self.root.configure(bg=BG)
        self.root.resizable(True, True)
        self.root.overrideredirect(False)

        self.selected_callback = None
        self.back_callback = None
        self.page = "main"
        self.model = ""
        self.world = ""
        self.mitigation_state = "off"
        self.attack_type = "noise"
        self.attack_noise_value = "80"
        self.attack_pulse_value = "0.3"
        self.active_attack_key = None
        self.jamming_active = False
        self.status_box_enabled = True
        self.attack_status = ""
        self.status_log_entries = []
        self.attack_warning = ""
        self.takeoff_sent = False
        self.takeoff_waiting = False
        self.destination_release_pending = False
        self.destination_release_until = 0.0
        self.destination_release_after_id = None
        self.destination_sent = False
        self.destination_forward_value = os.environ.get("UAVEDUSIM_DEST_DISTANCE_M") or os.environ.get("UAVEDUSIM_DEST_FORWARD_M", "350")
        self.destination_right_value = os.environ.get("UAVEDUSIM_DEST_ROTATION_DEG") or os.environ.get("UAVEDUSIM_DEST_DIRECTION_DEG") or os.environ.get("UAVEDUSIM_DEST_RIGHT_M", "0")
        self.destination_altitude_value = os.environ.get("UAVEDUSIM_DEST_ALT_M", "2.5")
        self.px4_console_fifo = RUNTIME_DIR / f"px4_console_{SESSION_ID}.fifo"
        self.wait_start = 0.0
        self.wait_soft_timeout = int(os.environ.get("UAVEDUSIM_WAIT_PX4_TIMEOUT", "900"))
        self.wait_timeout_reached = False
        self.fullscreen = False
        self.drag = {"x": 0, "y": 0}

        self._setup_dimensions()
        self._setup_styles()
        self._build_shell()
        self.root.protocol("WM_DELETE_WINDOW", self.close_app)
        self.root.bind("<Return>", lambda _e: self.choose_current())
        self.root.bind("<Escape>", self.on_escape)
        self.root.lift()
        self.root.attributes("-topmost", False)
        self.show_language_picker()

    def _env_float(self, name, default):
        try:
            return float(os.environ.get(name, str(default)))
        except Exception:
            return float(default)

    def _clamp(self, value, low, high):
        return max(low, min(value, high))

    # Calcula escala, tamanho da janela e fontes responsivas.
    # Configura estilos visuais dos widgets ttk.
    # Monta a moldura da janela, barra superior e área de conteúdo.
    def _build_shell(self):
        self.outer = tk.Frame(self.root, bg=BG, highlightbackground=BORDER, highlightthickness=1, bd=0)
        self.outer.pack(fill="both", expand=True)
        self.titlebar = tk.Frame(self.outer, bg=BG, height=self.TITLEBAR_H)
        self.titlebar.pack(fill="x", side="top")
        self.titlebar.pack_propagate(False)
        self.logo_icon = tk.Label(self.titlebar, text="☰", bg=BG, fg=ACCENT, font=(self.FONT_FAMILY, self.fs(15), "bold"), cursor="fleur")
        self.logo_icon.pack(side="left", padx=(self.px(16), self.px(6)))
        self.logo_icon.bind("<ButtonPress-1>", self.start_drag)
        self.logo_icon.bind("<B1-Motion>", self.do_drag)
        self.logo_lbl = tk.Label(self.titlebar, text="UAVEDUSIM", bg=BG, fg=ACCENT, font=(self.FONT_FAMILY, self.FS_LOGO, "bold"), cursor="fleur")
        self.logo_lbl.pack(side="left")
        self.logo_lbl.bind("<ButtonPress-1>", self.start_drag)
        self.logo_lbl.bind("<B1-Motion>", self.do_drag)
        self.badge = tk.Label(self.titlebar, text="  MAIN MENU  ", bg=TAG_BG, fg=TAG_FG, font=(self.FONT_FAMILY, self.FS_TINY, "bold"), padx=self.px(6), pady=self.px(2))
        self.badge.pack(side="left", padx=(self.px(10), 0))
        self.drag_area = tk.Frame(self.titlebar, bg=BG, cursor="fleur")
        self.drag_area.pack(side="left", fill="both", expand=True)
        self.drag_area.bind("<ButtonPress-1>", self.start_drag)
        self.drag_area.bind("<B1-Motion>", self.do_drag)
        win_btns = tk.Frame(self.titlebar, bg=BG)
        win_btns.pack(side="right", padx=(0, self.px(8)))
        self.win_min_btn = ttk.Button(
            win_btns,
            text=UAVEDUSIM_WIN_ICON_MINIMIZE,
            width=3,
            style="WinBtn.TButton",
            command=self.minimize,
        )
        self.win_min_btn.pack(side="left", padx=1)

        self.win_full_btn = ttk.Button(
            win_btns,
            text=UAVEDUSIM_WIN_ICON_RESTORE if self.fullscreen else UAVEDUSIM_WIN_ICON_MAXIMIZE,
            width=3,
            style="WinBtn.TButton",
            command=self.toggle_fullscreen,
        )
        self.win_full_btn.pack(side="left", padx=1)

        self.win_close_btn = ttk.Button(
            win_btns,
            text=UAVEDUSIM_WIN_ICON_CLOSE,
            width=3,
            style="CloseBtn.TButton",
            command=self.close_app,
        )
        self.win_close_btn.pack(side="left", padx=(1, 0))
        tk.Frame(self.outer, bg=ACCENT, height=self.px(2)).pack(fill="x")
        self.body_frame = tk.Frame(self.outer, bg=SURFACE)
        self.body_frame.pack(fill="both", expand=True)
        self.sidebar = tk.Frame(self.body_frame, bg=BG, width=self.SIDEBAR_W)
        self.sidebar.pack(side="left", fill="y")
        self.content = tk.Frame(self.body_frame, bg=SURFACE)
        self.content.pack(side="left", fill="both", expand=True)

    # Remove widgets da página atual antes de renderizar outra tela.
    def clear_content(self):
        for child in self.content.winfo_children():
            child.destroy()

    def set_badge(self, text):
        self.badge.configure(text=f"  {_uavedusim_translate(text)}  ")

    # Renderiza uma tela de menu com lista, rodape e botões.
    # Executa a ação associada ao item selecionado.
    # Define o comportamento da tecla Esc em cada tela.
    def on_escape(self, _event=None):
        if self.fullscreen:
            self.fullscreen = False
            self.root.attributes("-fullscreen", False)
            self.root.overrideredirect(False)
            return

        if self.page == "attack" and hasattr(self, "leave_attack_screen"):
            self.leave_attack_screen()
            return

        if self.back_callback:
            self.back_callback()
        else:
            self.close_app()

    def start_drag(self, event):
        self.drag["x"] = event.x_root - self.root.winfo_x()
        self.drag["y"] = event.y_root - self.root.winfo_y()

    def do_drag(self, event):
        if not self.fullscreen:
            self.root.geometry(f"+{event.x_root - self.drag['x']}+{event.y_root - self.drag['y']}")

    def toggle_fullscreen(self):
        self.fullscreen = not self.fullscreen
        if self.fullscreen:
            self.root.overrideredirect(False)
            self.root.attributes("-fullscreen", True)
        else:
            self.root.attributes("-fullscreen", False)
            self.root.overrideredirect(False)

        if hasattr(self, "win_full_btn"):
            try:
                self.win_full_btn.configure(
                    text=UAVEDUSIM_WIN_ICON_RESTORE if self.fullscreen else UAVEDUSIM_WIN_ICON_MAXIMIZE,
                )
            except Exception:
                pass

    def minimize(self):
        self.root.overrideredirect(False)
        self.root.iconify()
        self.root.after(200, lambda: self.root.overrideredirect(False))

    # Fecha a interface; o shell externo faz o cleanup final.
    def close_app(self):
        log("Fechando interface UAVEDUSIM.")
        self.root.destroy()

    # Encerra todos os terminais registrados pela sessão.
    def kill_tracked_terminals(self):
        if not PID_FILE.exists():
            return

        entries = []

        try:
            for raw in PID_FILE.read_text(encoding="utf-8", errors="ignore").splitlines():
                parts = raw.split("\t", 1)
                if not parts or not parts[0].strip().isdigit():
                    continue

                pid = int(parts[0].strip())
                title = parts[1].strip() if len(parts) > 1 else "terminal"

                if pid == os.getpid():
                    continue

                entries.append((pid, title))
        except Exception as e:
            log(f"Aviso: falha ao ler arquivo de PIDs {PID_FILE}: {e}")
            return

        if not entries:
            return

        log("Encerrando terminais abertos pelo UAVEDUSIM.")

        for pid, title in entries:
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                continue
            except Exception:
                pass

            log(f"Enviando TERM para terminal: pid={pid} title={title}")

            try:
                os.killpg(pid, signal.SIGTERM)
            except Exception:
                pass

            try:
                os.kill(pid, signal.SIGTERM)
            except Exception:
                pass

        time.sleep(0.8)

        for pid, title in entries:
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                continue
            except Exception:
                pass

            log(f"Forcando KILL para terminal restante: pid={pid} title={title}")

            try:
                os.killpg(pid, signal.SIGKILL)
            except Exception:
                pass

            try:
                os.kill(pid, signal.SIGKILL)
            except Exception:
                pass

        try:
            PID_FILE.write_text("", encoding="utf-8")
        except Exception:
            pass

    # Finaliza PX4, Gazebo e QGroundControl relacionados ao UAVEDUSIM.
    def cleanup_uavedusim_processes(self):
        log("Encerrando processos PX4/Gazebo/QGroundControl do UAVEDUSIM.")

        cleanup_script = APP_DIR / "cleanup_px4_gazebo.sh"

        if cleanup_script.exists() and os.access(cleanup_script, os.X_OK):
            try:
                proc = subprocess.run(
                    [str(cleanup_script)],
                    cwd=str(APP_DIR),
                    text=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    timeout=25,
                )

                if proc.stdout:
                    for line in proc.stdout.splitlines():
                        log(line)

                log(f"Cleanup retornou código: {proc.returncode}")
                return
            except subprocess.TimeoutExpired:
                log("Aviso: cleanup_px4_gazebo.sh excedeu timeout. Aplicando pkill direto.")
            except Exception as e:
                log(f"Aviso: falha ao executar cleanup_px4_gazebo.sh: {e}")

        pattern = "|".join([
            r"px4_sitl_default/bin/px4",
            r"PX4-Autopilot/build/.*/bin/px4",
            r"gz sim",
            r"gzserver",
            r"gzclient",
            r"MicroXRCEAgent",
            r"QGroundControl",
        ])

        subprocess.run(["pkill", "-TERM", "-f", pattern], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(0.8)
        subprocess.run(["pkill", "-KILL", "-f", pattern], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    # Encerra apenas terminais cujo titulo corresponde ao filtro.
    def kill_tracked_terminals_matching(self, title_fragments):
        if not PID_FILE.exists():
            return

        fragments = tuple(str(item).lower() for item in title_fragments)
        kept_lines = []
        targets = []

        try:
            raw_lines = PID_FILE.read_text(encoding="utf-8", errors="ignore").splitlines()
        except Exception as e:
            log(f"Aviso: falha ao ler arquivo de PIDs {PID_FILE}: {e}")
            return

        for raw in raw_lines:
            parts = raw.split("\t", 1)

            if not parts or not parts[0].strip().isdigit():
                continue

            pid = int(parts[0].strip())
            title = parts[1].strip() if len(parts) > 1 else "terminal"
            title_low = title.lower()

            if any(fragment in title_low for fragment in fragments):
                targets.append((pid, title))
            else:
                kept_lines.append(raw)

        if not targets:
            try:
                PID_FILE.write_text("\n".join(kept_lines) + ("\n" if kept_lines else ""), encoding="utf-8")
            except Exception:
                pass
            return

        log("Encerrando terminais da simulação PX4/Gazebo.")

        for pid, title in targets:
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                continue
            except Exception:
                pass

            log(f"Enviando TERM para terminal da simulação: pid={pid} title={title}")

            try:
                os.killpg(pid, signal.SIGTERM)
            except Exception:
                pass

            try:
                os.kill(pid, signal.SIGTERM)
            except Exception:
                pass

        time.sleep(0.8)

        for pid, title in targets:
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                continue
            except Exception:
                pass

            log(f"Forcando KILL para terminal da simulação: pid={pid} title={title}")

            try:
                os.killpg(pid, signal.SIGKILL)
            except Exception:
                pass

            try:
                os.kill(pid, signal.SIGKILL)
            except Exception:
                pass

        try:
            PID_FILE.write_text("\n".join(kept_lines) + ("\n" if kept_lines else ""), encoding="utf-8")
        except Exception:
            pass

    # Retorna processos associados a PX4/Gazebo.
    def simulation_process_lines(self):
        patterns = [
            r"make\s+px4_sitl",
            r"px4_sitl_default/bin/px4",
            r"PX4-Autopilot/build/.*/bin/px4",
            r"(^|[/\s])px4(\s|$)",
            r"\bgz\s+sim\b",
            r"\bgzserver\b",
            r"\bgzclient\b",
            r"\bgz-gui\b",
            r"\bgz\s+gui\b",
            r"\bMicroXRCEAgent\b",
        ]

        matches = []

        for line in process_lines():
            low = line.lower()

            if "uavedusim_gui_app.py" in low:
                continue

            for pattern in patterns:
                if re.search(pattern, line, flags=re.IGNORECASE):
                    matches.append(line)
                    break

        return matches

    # Aguarda o encerramento completo da simulação.
    def wait_until_simulation_processes_closed(self, timeout=20):
        deadline = time.monotonic() + float(timeout)
        last_remaining = []

        while time.monotonic() < deadline:
            remaining = self.simulation_process_lines()

            if not remaining:
                log("Todos os processos PX4/Gazebo da simulação foram encerrados.")
                return True

            last_remaining = remaining
            time.sleep(0.4)

        if last_remaining:
            log("Aviso: ainda existem processos PX4/Gazebo após timeout:")
            for line in last_remaining[:20]:
                log("  restante: " + line)

        return False

    # Envia TERM ou KILL para processos da simulação.
    def pkill_simulation_processes(self, signal_name):
        patterns = [
            r"make px4_sitl",
            r"px4_sitl_default/bin/px4",
            r"PX4-Autopilot/build/.*/bin/px4",
            r"gz sim",
            r"gzserver",
            r"gzclient",
            r"gz-gui",
            r"gz gui",
            r"MicroXRCEAgent",
        ]

        for pattern in patterns:
            subprocess.run(
                ["pkill", f"-{signal_name}", "-f", pattern],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )

        subprocess.run(
            ["pkill", f"-{signal_name}", "-x", "px4"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    # Limpa somente PX4/Gazebo, mantendo a interface aberta.
    def cleanup_simulation_processes_only(self):
        log("Encerrando apenas PX4/Gazebo da simulação atual.")

        self.pkill_simulation_processes("TERM")

        if not self.wait_until_simulation_processes_closed(timeout=6):
            log("Processos ainda ativos. Forcando KILL em PX4/Gazebo.")
            self.pkill_simulation_processes("KILL")

        closed = self.wait_until_simulation_processes_closed(timeout=10)

        try:
            self.px4_console_fifo.unlink(missing_ok=True)
        except TypeError:
            try:
                if self.px4_console_fifo.exists():
                    self.px4_console_fifo.unlink()
            except Exception:
                pass
        except Exception:
            pass

        return closed

    # Reinicia PX4/Gazebo a partir da tela de ataques.
    def restart_simulation_from_attack(self):
        if not self.model or not self.world:
            msg = "Não ha modelo/mundo selecionado para reiniciar."
            self._set_attack_status_ui(msg, level="danger")
            return

        msg = f"Reiniciando simulação: {self.model} / {self.world}..."
        log(msg)
        self._log_status_event(
            "Reiniciando simulação",
            "PX4 e Gazebo estão sendo encerrados e vão subir novamente do zero. "
            "Takeoff, destino, ataque e mitigação vão precisar ser configurados de novo.",
            level="warn",
        )

        if hasattr(self, "restart_button"):
            self.restart_button.set_enabled(False)

        self.takeoff_sent = False
        self.takeoff_waiting = False
        self.destination_release_pending = False
        self.destination_release_until = 0.0
        self.destination_sent = False
        self.active_attack_key = None
        self.jamming_active = False
        self.mitigation_state = "off"
        self.restart_waiting_for_cleanup = True

        try:
            self.wait_log_offset = LOG.stat().st_size
        except Exception:
            self.wait_log_offset = 0

        self.show_wait_page()

        if hasattr(self, "status_lbl"):
            self.status_lbl.configure(
                text="Reiniciando simulação...\nEncerrando processos antigos do PX4/Gazebo antes de iniciar novamente.",
                fg=WARN,
            )

        def worker():
            cleanup_ok = False

            try:
                self.kill_tracked_terminals_matching(("PX4 SITL",))
            except Exception as e:
                log(f"Aviso: falha ao encerrar terminal PX4: {e}")

            try:
                cleanup_ok = self.cleanup_simulation_processes_only()
            except Exception as e:
                log(f"Aviso: falha ao encerrar PX4/Gazebo: {e}")
                cleanup_ok = False

            def finish():
                self.restart_waiting_for_cleanup = False
                self.takeoff_sent = False
                self.destination_sent = False
                self.active_attack_key = None
                self.jamming_active = False
                self.mitigation_state = "off"

                if not cleanup_ok:
                    msg_error = (
                        "Não foi possível garantir que todos os processos PX4/Gazebo foram encerrados.\n"
                        "A simulação não sera reiniciada automaticamente. Use Limpar processos UAVEDUSIM e tente novamente."
                    )
                    log(msg_error)

                    if hasattr(self, "progress"):
                        try:
                            self.progress.stop()
                        except Exception:
                            pass

                    if hasattr(self, "status_lbl"):
                        self.status_lbl.configure(text=msg_error, fg=DANGER)

                    return

                self._log_status_event(
                    "Simulação reiniciada",
                    "PX4/Gazebo foram encerrados e vão abrir novamente. Envie commander takeoff e "
                    "destino para liberar os ataques.",
                    level="ok",
                )

                try:
                    self.wait_log_offset = LOG.stat().st_size
                except Exception:
                    self.wait_log_offset = 0

                self.open_px4_and_wait()

            self.root.after(0, finish)

        threading.Thread(target=worker, daemon=True).start()

    # Sai da tela de ataques e encerra a simulação atual.
    def leave_attack_screen(self):
        log("Saindo da tela de ataques. Encerrando terminais e processos da simulação.")

        self.selected_callback = None
        self.back_callback = None

        try:
            self.kill_tracked_terminals()
        except Exception as e:
            log(f"Aviso: falha ao encerrar terminais rastreados: {e}")

        try:
            self.cleanup_uavedusim_processes()
        except Exception as e:
            log(f"Aviso: falha ao encerrar processos UAVEDUSIM: {e}")

        self.takeoff_sent = False
        self.takeoff_waiting = False
        self.destination_release_pending = False
        self.destination_release_until = 0.0
        self.destination_sent = False
        self.active_attack_key = None
        self.jamming_active = False
        self.attack_status = ""
        self.status_log_entries = []

        self.show_main_menu()

    # Mostra uma tela de erro e registra a falha.
    def show_error(self, msg):
        log("ERRO: " + msg)
        self.show_status_page("Erro", msg, ["Voltar ao menu principal"], lambda _x: self.show_main_menu(), badge="ERRO")

    # Exibe uma página simples de status com opções.
    def show_status_page(self, title, text, options, on_select, badge="STATUS", on_back=None):
        self.clear_content()
        self.set_badge(badge)
        self.selected_callback = on_select
        self.back_callback = on_back or self.show_main_menu
        header = tk.Frame(self.content, bg=SURFACE)
        header.pack(fill="x", padx=self.CONTENT_PAD, pady=(self.px(24), 0))
        tk.Label(header, text=title, bg=SURFACE, fg=TEXT, font=(self.FONT_FAMILY, self.fs(16), "bold")).pack(anchor="w")
        tk.Label(header, text=text, bg=SURFACE, fg=MUTED, font=(self.FONT_FAMILY, self.FS_SMALL), justify="left", wraplength=max(self.px(540), self.content.winfo_width() - self.px(120))).pack(anchor="w", pady=(self.px(8), 0))
        self.render_menu(title, options, on_select, badge=badge, on_back=self.back_callback, is_main=False)

    # Exibe o menu inicial da aplicacao.
    # Despacha a ação escolhida no menu inicial.
    def handle_main_choice(self, choice):
        if choice == "Executar UAVEDUSIM":
            self.start_uavedusim_flow()
        elif choice == "Limpar build (se necessário)":
            self.run_clean_build()
        elif choice == "Limpar processos UAVEDUSIM":
            self.run_cleanup()

    # Salva o PID do terminal para cleanup posterior.
    def register_terminal_pid(self, pid, title):
        if not pid:
            return
        try:
            with PID_FILE.open("a", encoding="utf-8") as f:
                f.write(f"{int(pid)}\t{title}\n")
            log(f"Terminal registrado: pid={pid} title={title}")
        except Exception as e:
            log(f"Aviso: falha ao registrar terminal {pid}: {e}")

    # Abre comandos longos em um terminal separado.
    def open_terminal(self, title, command_body, hold=True):
        tmp = SCRIPT_DIR / f"uavedusim_cmd_{int(time.time()*1000)}_{os.getpid()}.sh"
        script = f'''#!/usr/bin/env bash
set -u
finish() {{
    local status="$?"
    trap - EXIT
    echo
    echo "[UAVEDUSIM] Comando finalizado com código: $status"
    rm -f "$0" >/dev/null 2>&1 || true
    if [ "${{UAVEDUSIM_TERMINAL_HOLD:-1}}" = "1" ]; then
        echo "[UAVEDUSIM] Pressione ENTER para fechar este terminal."
        if [ -t 0 ]; then
            read -r _ || true
        else
            sleep 3600
        fi
    else
        echo "[UAVEDUSIM] Fechando terminal automaticamente."
    fi
    exit "$status"
}}
trap finish EXIT
trap 'exit 130' INT TERM HUP
exec > >(tee -a "${{UAVEDUSIM_LOG:-/tmp/uavedusim.log}}") 2>&1
cd "${{UAVEDUSIM_APP_DIR:?}}" || exit 1
echo "[UAVEDUSIM] ${{UAVEDUSIM_TITLE:-Terminal}}"
echo "[UAVEDUSIM] Log: ${{UAVEDUSIM_LOG:-}}"
echo "[UAVEDUSIM] Session: ${{UAVEDUSIM_SESSION_ID:-}}"
echo
set +e
{command_body}
status=$?
exit "$status"
'''
        tmp.write_text(script, encoding="utf-8")
        tmp.chmod(0o755)
        env = os.environ.copy()
        env.update({
            "UAVEDUSIM_APP_DIR": str(APP_DIR),
            "UAVEDUSIM_LOG": str(LOG),
            "UAVEDUSIM_TITLE": title,
            "UAVEDUSIM_SESSION_ID": SESSION_ID,
            "UAVEDUSIM_TERMINAL_HOLD": "1" if hold else "0",
        })
        terminals = []
        if shutil.which("kitty"):
            terminals.append((["kitty", "--title", title, "bash", str(tmp)] if not hold else ["kitty", "--hold", "--title", title, "bash", str(tmp)]))
        if shutil.which("foot"):
            terminals.append((["foot", "--title", title, "bash", str(tmp)] if not hold else ["foot", "--hold", "--title", title, "bash", str(tmp)]))
        if shutil.which("alacritty"):
            terminals.append((["alacritty", "--title", title, "-e", "bash", str(tmp)] if not hold else ["alacritty", "--hold", "--title", title, "-e", "bash", str(tmp)]))
        if shutil.which("gnome-terminal"):
            terminals.append(["gnome-terminal", f"--title={title}", "--", "bash", str(tmp)])
        if not terminals:
            self.show_error(_uavedusim_L("Nenhum terminal encontrado. Instale kitty: sudo dnf install kitty", "No terminal found. Install kitty: sudo dnf install kitty"))
            return False
        try:
            log(f"Abrindo terminal: {title}")
            log(f"Script temporário: {tmp}")
            proc = subprocess.Popen(terminals[0], env=env, cwd=str(APP_DIR), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
            self.register_terminal_pid(proc.pid, title)
            return True
        except Exception as e:
            self.show_error(_uavedusim_L(f"Falha ao abrir terminal: {e}", f"Failed to open terminal: {e}"))
            return False

    # Executa a limpeza manual de processos.
    def run_cleanup(self):
        if (APP_DIR / "cleanup_px4_gazebo.sh").exists() and os.access(APP_DIR / "cleanup_px4_gazebo.sh", os.X_OK):
            cmd = f"cd {shell_quote(str(APP_DIR))} && ./cleanup_px4_gazebo.sh"
        else:
            cmd = "pkill -f 'px4|gz sim|gzserver|gzclient|MicroXRCEAgent|QGroundControl' || true"
        self.open_terminal("Limpar processos UAVEDUSIM", cmd, hold=False)

    # Remove o build local do PX4 quando necessário.
    def run_clean_build(self):
        if not PX4_DIR.is_dir():
            self.show_error(_uavedusim_L(f"Não encontrei a pasta: {PX4_DIR}", f"Couldn't find the folder: {PX4_DIR}"))
            return
        cmd = f"""cd {shell_quote(str(PX4_DIR))}
echo '[UAVEDUSIM] Limpando build...'
rm -rf build
echo '[UAVEDUSIM] Rodando make clean...'
make clean || true
echo '[UAVEDUSIM] Build limpo. Terminal sera fechado automaticamente.'"""
        self.open_terminal("Limpar build (se necessário)", cmd, hold=False)

    # Executa comandos Git e retorna código e saída.
    def git_output(self, args):
        proc = run_cmd(args)
        return proc.returncode, (proc.stdout or "").strip()

    # Verifica se o PX4 possui tag semântica acessivel.
    def px4_semantic_tag_available(self):
        code, _out = self.git_output([
            "git", "-C", PX4_DIR,
            "describe", "--tags", "--match", "v[0-9]*", "--abbrev=0"
        ])
        return code == 0

    # Obtem a versão Git do PX4 usada no build.
    def px4_git_describe(self):
        _code, out = self.git_output([
            "git", "-C", PX4_DIR,
            "describe", "--tags", "--match", "v[0-9]*", "--dirty", "--always"
        ])
        return out.strip()

    # Detecta build PX4 configurado como v0.0.0.
    def px4_build_looks_like_v0(self):
        build_dir = PX4_DIR / "build/px4_sitl_default"

        if not build_dir.exists():
            return False

        recent_log = read_recent_log(800000)
        if "PX4_GIT_TAG: v0.0.0" in recent_log or "PX4_GIT_TAG: v0.0.0" in recent_log:
            return True

        candidates = [
            build_dir / "CMakeCache.txt",
            build_dir / "src/lib/version/build_git_version.h",
            build_dir / "src/lib/version/version.c",
            build_dir / "src/lib/version/version.cpp",
            build_dir / "src/lib/version/git_version.h",
        ]

        version_dir = build_dir / "src/lib/version"
        if version_dir.exists():
            try:
                candidates.extend([x for x in version_dir.rglob("*") if x.is_file()][:80])
            except Exception:
                pass

        for candidate in candidates:
            try:
                if not candidate.exists() or not candidate.is_file():
                    continue
                if candidate.stat().st_size > 2_000_000:
                    continue
                data = candidate.read_text(encoding="utf-8", errors="ignore")
                if "v0.0.0" in data or "PX4_GIT_TAG" in data and "0.0.0" in data:
                    log(f"Build PX4 parece ter sido configurado com versão 0.0.0: {candidate}")
                    return True
            except Exception:
                continue

        return False

    # Remove o build SITL quando a versão PX4 precisa ser regenerada.
    def invalidate_px4_sitl_build_for_version(self, reason):
        build_dir = PX4_DIR / "build/px4_sitl_default"

        if os.environ.get("UAVEDUSIM_DISABLE_AUTO_TAG_BUILD_CLEAN", "0") == "1":
            log(f"Auto limpeza do build SITL desativada. Motivo ignorado: {reason}")
            return

        if build_dir.exists():
            log(f"Removendo apenas build SITL para regenerar versão PX4. Motivo: {reason}")
            shutil.rmtree(build_dir, ignore_errors=True)
        else:
            log(f"Build SITL ainda não existe. Motivo registrado: {reason}")

    # Garante tags Git para evitar build PX4 com versão v0.0.0.
    def ensure_px4_git_metadata(self):
        if not PX4_DIR.is_dir():
            return

        if run_cmd(["git", "-C", PX4_DIR, "rev-parse", "--is-inside-work-tree"]).returncode != 0:
            log("Aviso: PX4 não parece ser repositório Git. Pulando verificação de tags.")
            return

        initial_desc = self.px4_git_describe()
        initial_has_tag = self.px4_semantic_tag_available()

        if initial_has_tag:
            log(f"PX4 Git version detectada: {initial_desc or 'indisponível'}")

            if self.px4_build_looks_like_v0():
                self.invalidate_px4_sitl_build_for_version(
                    "tags existem, mas o build anterior foi configurado como PX4_GIT_TAG v0.0.0"
                )

            return

        log("PX4 sem tags semânticas alcançáveis. Tentando buscar tags do remoto...")

        run_cmd(["git", "-C", PX4_DIR, "config", "remote.origin.tagOpt", "--tags"])

        _code, shallow = self.git_output([
            "git", "-C", PX4_DIR,
            "rev-parse", "--is-shallow-repository"
        ])

        if shallow == "true":
            run_cmd([
                "git", "-C", PX4_DIR,
                "fetch", "--unshallow", "--tags", "--force", "origin"
            ])

        run_cmd([
            "git", "-C", PX4_DIR,
            "fetch", "--tags", "--force", "--prune", "origin",
            "+refs/tags/*:refs/tags/*"
        ])

        if self.px4_semantic_tag_available():
            desc = self.px4_git_describe()
            log(f"PX4 Git version após fetch: {desc or 'indisponível'}")
            self.invalidate_px4_sitl_build_for_version(
                "tags PX4 foram recuperadas depois de o repositório estar sem versão semântica"
            )
            return

        # Fallback offline para repositórios sem tags PX4.
        # Cria tag local quando não for possível buscar tags do remoto.
        # Preserva a tag oficial v1.17.0 quando ela já existe.
        if run_cmd([
            "git", "-C", PX4_DIR,
            "show-ref", "--verify", "--quiet", "refs/tags/v1.17.0"
        ]).returncode == 0:
            fallback_tag = os.environ.get("UAVEDUSIM_PX4_FALLBACK_TAG", "v1.17.0-uavedusim")
        else:
            fallback_tag = os.environ.get("UAVEDUSIM_PX4_FALLBACK_TAG", "v1.17.0")

        log(f"Criando tag local fallback para evitar PX4_GIT_TAG v0.0.0: {fallback_tag}")
        run_cmd(["git", "-C", PX4_DIR, "tag", "-f", fallback_tag, "HEAD"])

        final_desc = self.px4_git_describe()
        log(f"PX4 Git version final: {final_desc or 'indisponível'}")

        if self.px4_semantic_tag_available():
            self.invalidate_px4_sitl_build_for_version(
                f"tag fallback {fallback_tag} criada para corrigir PX4_GIT_TAG v0.0.0"
            )
        else:
            log("Aviso: mesmo após fallback, git describe ainda não encontrou tag semântica.")


    # Inicializa submódulos do PX4 usados pela simulação Gazebo.
    def ensure_px4_submodules(self):
        if not PX4_DIR.is_dir():
            return
        self.ensure_px4_git_metadata()
        if not (PX4_DIR / "Tools/simulation/gz/models/x500").is_dir():
            log("Submódulo Tools/simulation/gz ausente. Inicializando submódulos do PX4...")
            run_cmd(["git", "-C", PX4_DIR, "submodule", "sync", "--recursive"])
            proc = run_cmd(["git", "-C", PX4_DIR, "submodule", "update", "--init", "--recursive", "Tools/simulation/gz"])
            if proc.returncode != 0:
                run_cmd(["git", "-C", PX4_DIR, "submodule", "update", "--init", "--recursive"])

    # Prepara modelo, mundo e airframe do UAVEDUSIM no PX4.
    def ensure_uavjamsim_links(self, strict=False):
        uav_dir = APP_DIR / "UAVJamSim"
        model_src = uav_dir / "x500_uavjamsim"
        model_dst_dir = PX4_DIR / "Tools/simulation/gz/models"
        model_dst = model_dst_dir / "x500_uavjamsim"
        world_src = uav_dir / "worlds/default.sdf"
        world_dst_dir = PX4_DIR / "Tools/simulation/gz/worlds"
        world_dst = world_dst_dir / "default.sdf"
        airframe_src_dir = uav_dir / "airframes"
        airframe_src = airframe_src_dir / "4020_gz_x500_uavjamsim"
        airframe_dst_dir = PX4_DIR / "ROMFS/px4fmu_common/init.d-posix/airframes"
        airframe_dst = airframe_dst_dir / "4020_gz_x500_uavjamsim"
        airframe_cmake = airframe_dst_dir / "CMakeLists.txt"
        px4_link = uav_dir / "px4"

        if not PX4_DIR.is_dir():
            if strict:
                raise RuntimeError(_uavedusim_L(f"Não encontrei a pasta: {PX4_DIR}", f"Couldn't find the folder: {PX4_DIR}"))
            log(f"Aviso: pasta PX4 não encontrada: {PX4_DIR}")
            return
        self.ensure_px4_submodules()
        if not uav_dir.is_dir():
            if strict:
                raise RuntimeError(_uavedusim_L(f"Não encontrei a pasta: {uav_dir}", f"Couldn't find the folder: {uav_dir}"))
            log(f"Aviso: pasta UAVJamSim não encontrada: {uav_dir}")
            return
        if model_src.is_dir():
            model_dst_dir.mkdir(parents=True, exist_ok=True)
            if model_dst.exists() or model_dst.is_symlink():
                if model_dst.is_dir() and not model_dst.is_symlink():
                    shutil.rmtree(model_dst)
                else:
                    model_dst.unlink(missing_ok=True)
            os.symlink("../../../../../UAVJamSim/x500_uavjamsim", model_dst)
            log(f"Link modelo: {model_dst} -> ../../../../../UAVJamSim/x500_uavjamsim")
        elif strict:
            raise RuntimeError(_uavedusim_L(f"Não encontrei o modelo: {model_src}", f"Couldn't find the model: {model_src}"))
        else:
            log(f"Aviso: modelo x500_uavjamsim não encontrado: {model_src}")

        if world_src.exists() or world_src.is_symlink():
            world_dst_dir.mkdir(parents=True, exist_ok=True)
            if world_dst.exists() or world_dst.is_symlink():
                if world_dst.is_dir() and not world_dst.is_symlink():
                    shutil.rmtree(world_dst)
                else:
                    world_dst.unlink(missing_ok=True)
            os.symlink("../../../../../UAVJamSim/worlds/default.sdf", world_dst)
            log(f"Link mundo default: {world_dst} -> ../../../../../UAVJamSim/worlds/default.sdf")
        else:
            log(f"Aviso: mundo customizado não encontrado: {world_src}. Mantendo default do PX4, se existir.")

        airframe_src_dir.mkdir(parents=True, exist_ok=True)
        airframe_dst_dir.mkdir(parents=True, exist_ok=True)
        airframe_text = """#!/bin/sh
#
# @name Gazebo x500 UAVJamSim
#
# @type Quadrotor
#

PX4_SIM_MODEL=${PX4_SIM_MODEL:=x500_uavjamsim}

. ${R}etc/init.d-posix/airframes/4001_gz_x500
"""
        airframe_src.write_text(airframe_text, encoding="utf-8")
        if airframe_dst.exists() or airframe_dst.is_symlink():
            if airframe_dst.is_dir() and not airframe_dst.is_symlink():
                shutil.rmtree(airframe_dst)
            else:
                airframe_dst.unlink(missing_ok=True)
        shutil.copyfile(airframe_src, airframe_dst)
        airframe_src.chmod(0o644)
        airframe_dst.chmod(0o644)
        log(f"Airframe instalado: {airframe_dst}")

        if airframe_cmake.exists():
            text = airframe_cmake.read_text(encoding="utf-8")
            entry = "\t4020_gz_x500_uavjamsim"
            if "4020_gz_x500_uavjamsim" not in text:
                if "\t4021_gz_x500_flow" in text:
                    text = text.replace("\t4021_gz_x500_flow", entry + "\n\t4021_gz_x500_flow", 1)
                elif "\t4019_gz_x500_gimbal" in text:
                    text = text.replace("\t4019_gz_x500_gimbal", "\t4019_gz_x500_gimbal\n" + entry, 1)
                else:
                    idx = text.rfind(")")
                    if idx == -1:
                        raise RuntimeError(_uavedusim_L(
                            "Não encontrei fechamento da lista de airframes no CMakeLists.txt",
                            "Couldn't find the closing bracket of the airframe list in CMakeLists.txt",
                        ))
                    text = text[:idx] + entry + "\n" + text[idx:]
                airframe_cmake.write_text(text, encoding="utf-8")
                log("CMakeLists atualizado com 4020_gz_x500_uavjamsim.")
            else:
                log("CMakeLists já contem 4020_gz_x500_uavjamsim.")
        elif strict:
            raise RuntimeError(_uavedusim_L(f"Não encontrei: {airframe_cmake}", f"Couldn't find: {airframe_cmake}"))
        else:
            log(f"Aviso: CMakeLists de airframes não encontrado: {airframe_cmake}")

        if px4_link.is_symlink() or not px4_link.exists():
            px4_link.unlink(missing_ok=True)
            os.symlink("../PX4-Autopilot", px4_link)
            log(f"Link px4: {px4_link} -> ../PX4-Autopilot")
        else:
            log(f"Aviso: {px4_link} já existe e não é link. Mantendo como esta.")

    # Lista modelos Gazebo executáveis pelo PX4.
    # Salva o modelo escolhido e abre a seleção de mundo.
    # Lista mundos .sdf disponíveis para a simulação.
    # Salva o mundo escolhido e inicia QGC/PX4.
    # Abre o QGroundControl sem terminal visível.
    def open_qgc(self):
        def qgc_already_running():
            for line in process_lines():
                low = line.lower()
                if "qgroundcontrol" in low:
                    return True
            return False

        def base_qgc_env():
            env = os.environ.copy()

            for key in [
                "PYTHONHOME",
                "PYTHONPATH",
                "VIRTUAL_ENV",
                "CONDA_PREFIX",
                "CONDA_DEFAULT_ENV",
            ]:
                env.pop(key, None)

            env["GDK_BACKEND"] = env.get("GDK_BACKEND", "wayland,x11")
            return env

        def launch_qgc_no_terminal(args, env, cwd):
            try:
                with LOG.open("ab", buffering=0) as log_file:
                    proc = subprocess.Popen(
                        args,
                        cwd=str(cwd),
                        env=env,
                        stdout=log_file,
                        stderr=subprocess.STDOUT,
                        start_new_session=True,
                    )

                self.register_terminal_pid(proc.pid, "QGroundControl")
                log(f"QGroundControl aberto sem terminal. pid={proc.pid}")
                return True

            except Exception as e:
                self.show_error(_uavedusim_L(f"Falha ao abrir QGroundControl sem terminal: {e}", f"Failed to open QGroundControl without a terminal: {e}"))
                return False

        if qgc_already_running():
            log("QGroundControl já está aberto. Não vou abrir outra instância.")
            return

        if QGC_EXTRACTED.exists() and os.access(QGC_EXTRACTED, os.X_OK):
            appdir = APP_DIR / "squashfs-root"
            scanner = appdir / "usr/lib/gstreamer1.0/gstreamer-1.0/gst-plugin-scanner"

            try:
                shutil.rmtree(Path.home() / ".cache/gstreamer-1.0", ignore_errors=True)
            except Exception:
                pass

            env = base_qgc_env()
            env.update({
                "APPDIR": str(appdir),
                "LD_LIBRARY_PATH": str(appdir / "usr/lib"),
                "GST_PLUGIN_PATH": str(appdir / "usr/lib/gstreamer-1.0"),
                "GST_PLUGIN_SCANNER": str(scanner),
                "GST_PLUGIN_SCANNER_1_0": str(scanner),
                "GST_REGISTRY_FORK": "no",
            })

            launch_qgc_no_terminal([str(appdir / "usr/bin/QGroundControl")], env, APP_DIR)
            return

        if QGC_APP.exists():
            try:
                QGC_APP.chmod(QGC_APP.stat().st_mode | 0o111)
            except Exception:
                pass

            env = base_qgc_env()
            launch_qgc_no_terminal([str(QGC_APP)], env, APP_DIR)
            return

        log(f"Aviso: QGroundControl não encontrado em {APP_DIR}")


    # Abre PX4/Gazebo e prepara a tela de espera.
    def open_px4_and_wait(self):
        cmd = f'''{load_gazebo_env_block()}
cd {shell_quote(str(PX4_DIR))}
if [ ! -d 'Tools/simulation/gz/models/x500' ]; then
    echo '[UAVEDUSIM] Submódulo Tools/simulation/gz ausente. Inicializando submódulos...'
    git submodule sync --recursive || true
    git submodule update --init --recursive Tools/simulation/gz || git submodule update --init --recursive || true
fi
cd {shell_quote(str(APP_DIR))}
echo '[UAVEDUSIM] Garantindo links simbólicos e airframe do UAVJamSim...'
ls -l {shell_quote(str(PX4_DIR / "Tools/simulation/gz/models/x500_uavjamsim"))} 2>/dev/null || true
ls -l {shell_quote(str(PX4_DIR / "Tools/simulation/gz/worlds/default.sdf"))} 2>/dev/null || true
ls -l {shell_quote(str(PX4_DIR / "ROMFS/px4fmu_common/init.d-posix/airframes/4020_gz_x500_uavjamsim"))} 2>/dev/null || true
if [ {shell_quote(self.model)} = 'gz_x500_uavjamsim' ]; then
    if [ ! -f {shell_quote(str(PX4_DIR / "ROMFS/px4fmu_common/init.d-posix/airframes/4020_gz_x500_uavjamsim"))} ]; then
        echo '[UAVEDUSIM] ERRO: airframe 4020_gz_x500_uavjamsim não foi instalado no ROMFS.'
        exit 1
    fi
    if [ -L {shell_quote(str(PX4_DIR / "ROMFS/px4fmu_common/init.d-posix/airframes/4020_gz_x500_uavjamsim"))} ]; then
        echo '[UAVEDUSIM] ERRO: airframe 4020_gz_x500_uavjamsim ainda é link simbólico. Ele precisa ser arquivo real.'
        exit 1
    fi
fi
cd {shell_quote(str(PX4_DIR))}
PX4_CONSOLE_FIFO={shell_quote(str(self.px4_console_fifo))}
rm -f "$PX4_CONSOLE_FIFO"
mkfifo "$PX4_CONSOLE_FIFO"
echo "[UAVEDUSIM] Console PX4 por FIFO: $PX4_CONSOLE_FIFO"
echo '[UAVEDUSIM] Executando sem limpar build: make px4_sitl {self.model} PX4_GZ_WORLD={self.world}'
( while true; do cat "$PX4_CONSOLE_FIFO"; done ) | make px4_sitl {shell_quote(self.model)} PX4_GZ_WORLD={shell_quote(self.world)}'''
        try:
            self.wait_log_offset = LOG.stat().st_size
        except Exception:
            self.wait_log_offset = 0

        self.open_terminal(f"PX4 SITL - {self.model} - {self.world}", cmd, hold=True)
        self.show_wait_page()

    # Mostra a tela de carregamento da simulação.
    def _wait_status_lines(self, reason):
        raw = str(reason or "")
        parts = [part.strip() for part in raw.replace(";", "|").split("|") if part.strip()]

        px4_status = _uavedusim_L("iniciando", "starting")
        gazebo_status = _uavedusim_L("abrindo", "opening")

        for part in parts:
            low = part.lower()

            if "gazebo" in low:
                if "aberto" in low:
                    gazebo_status = _uavedusim_L("aberto", "open")
                elif "ainda não detectado" in low or "não detectado" in low:
                    gazebo_status = _uavedusim_L("aguardando abrir", "waiting to open")
                else:
                    gazebo_status = part.replace("Gazebo ", "").strip()

            elif "px4" in low or "ready for takeoff" in low or "commander" in low:
                if "confirmou takeoff detected" in low:
                    px4_status = _uavedusim_L("confirmou Takeoff detected", "confirmed Takeoff detected")
                elif "ready for takeoff" in low:
                    px4_status = _uavedusim_L("pronto para decolar", "ready for takeoff")
                elif "iniciado" in low and "aguardando" in low:
                    px4_status = _uavedusim_L(
                        'iniciado; aguardando "Ready for takeoff"',
                        'started; waiting for "Ready for takeoff"',
                    )
                elif "ainda não detectado" in low or "não detectado" in low:
                    px4_status = _uavedusim_L("aguardando iniciar", "waiting to start")
                elif "em execução" in low:
                    px4_status = _uavedusim_L("em execução", "running")
                else:
                    px4_status = part.replace("PX4 ", "").strip()

        return px4_status, gazebo_status

    def _format_wait_status_text(self, elapsed, reason, *, ok=False, timed_out=False):
        px4_status, gazebo_status = self._wait_status_lines(reason)
        px4_label = _uavedusim_L("PX4", "PX4")
        gazebo_label = _uavedusim_L("Gazebo", "Gazebo")

        if ok:
            return (
                _uavedusim_L("Simulação pronta.\n", "Simulation ready.\n")
                + f"{px4_label}: {px4_status}\n"
                + f"{gazebo_label}: {gazebo_status}\n"
                + _uavedusim_L("Abrindo menu de ataques...", "Opening attack menu...")
            )

        if timed_out:
            return (
                _uavedusim_L(
                    f"A simulação ainda não ficou pronta após {self.wait_soft_timeout}s.\n",
                    f"The simulation still isn't ready after {self.wait_soft_timeout}s.\n",
                )
                + f"{px4_label}: {px4_status}\n"
                + f"{gazebo_label}: {gazebo_status}\n"
                + _uavedusim_L("Verifique o terminal do PX4/Gazebo.", "Check the PX4/Gazebo terminal.")
            )

        return (
            _uavedusim_L(f"Preparando simulação... {elapsed}s\n", f"Preparing simulation... {elapsed}s\n")
            + f"{px4_label}: {px4_status}\n"
            + f"{gazebo_label}: {gazebo_status}\n"
            + _uavedusim_L(
                "O menu de ataques abre automaticamente quando tudo estiver pronto.",
                "The attack menu opens automatically once everything is ready.",
            )
        )

    # Verifica periodicamente se PX4 e Gazebo estão prontos.
    def check_px4_ready(self):
        if self.page != "wait":
            return

        elapsed = int(time.monotonic() - self.wait_start)

        if getattr(self, "restart_waiting_for_cleanup", False):
            if hasattr(self, "status_lbl"):
                self.status_lbl.configure(
                    text=(
                        _uavedusim_L(
                            f"Reiniciando simulação... {elapsed}s\n",
                            f"Restarting simulation... {elapsed}s\n",
                        )
                        + _uavedusim_L(
                            "Fechando PX4/Gazebo antigos antes de abrir novamente.",
                            "Closing the old PX4/Gazebo before opening it again.",
                        )
                    ),
                    fg=WARN,
                )
                self._reflow_wait_status()

            self.root.after(600, self.check_px4_ready)
            return

        ok, reason = simulation_ready(getattr(self, "wait_log_offset", 0))

        if ok:
            try:
                self.progress.stop()
            except Exception:
                pass

            self.status_lbl.configure(
                text=self._format_wait_status_text(elapsed, reason, ok=True),
                fg=OK,
            )
            self._reflow_wait_status()
            log(f"Simulação pronta para ataques: {reason}. Abrindo menu de ataques.")
            self.root.after(700, self.show_attack_menu)
            return

        if elapsed >= self.wait_soft_timeout and not self.wait_timeout_reached:
            self.wait_timeout_reached = True
            self.status_lbl.configure(
                text=self._format_wait_status_text(elapsed, reason, timed_out=True),
                fg=DANGER,
            )
            self._reflow_wait_status()
            log(f"Aviso: simulação ainda não está pronta após {self.wait_soft_timeout}s. Estado: {reason}. Continuando a espera.")
        else:
            self.status_lbl.configure(
                text=self._format_wait_status_text(elapsed, reason),
                fg=WARN,
            )
            self._reflow_wait_status()

        self.root.after(1000, self.check_px4_ready)


    # Compila ferramentas de ataque e mitigação quando necessário.
    def ensure_spoofer_tools(self):
        spoofer_dir = APP_DIR / "UAVJamSim/spoofer"
        build_dir = spoofer_dir / "build"
        spoofer_bin = build_dir / "spoofer"
        mitigator_bin = build_dir / "attack_mitigator"
        if not spoofer_dir.is_dir():
            raise RuntimeError(_uavedusim_L(f"Não encontrei a pasta do spoofer: {spoofer_dir}", f"Couldn't find the spoofer folder: {spoofer_dir}"))
        if spoofer_bin.exists() and os.access(spoofer_bin, os.X_OK) and mitigator_bin.exists() and os.access(mitigator_bin, os.X_OK):
            return spoofer_bin, mitigator_bin
        log("Compilando ferramentas do UAVJamSim/spoofer...")
        if (spoofer_dir / "build.sh").exists() and os.access(spoofer_dir / "build.sh", os.X_OK):
            cmd = f"{load_gazebo_env_block()}\ncd {shell_quote(str(spoofer_dir))} && ./build.sh"
        else:
            jobs = os.cpu_count() or 2
            cmd = f"{load_gazebo_env_block()}\nmkdir -p {shell_quote(str(build_dir))}\ncd {shell_quote(str(build_dir))} && cmake .. && make -j{jobs}"
        proc = subprocess.run(["bash", "-lc", cmd], text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        if proc.stdout:
            for line in proc.stdout.splitlines():
                log(line)
        if proc.returncode != 0:
            raise RuntimeError(_uavedusim_L("Falha ao compilar ferramentas do spoofer. Veja o log.", "Failed to build the spoofer tools. See the log."))
        for f in [spoofer_bin, mitigator_bin]:
            try:
                f.chmod(f.stat().st_mode | 0o111)
            except Exception:
                pass
        if not spoofer_bin.exists():
            raise RuntimeError(_uavedusim_L(f"Não encontrei o executável: {spoofer_bin}", f"Couldn't find the executable: {spoofer_bin}"))
        if not mitigator_bin.exists():
            raise RuntimeError(_uavedusim_L(f"Não encontrei o executável: {mitigator_bin}", f"Couldn't find the executable: {mitigator_bin}"))
        return spoofer_bin, mitigator_bin


    # Renderiza a tela de ataques, mitigação e prerequisitos.
    def show_attack_menu(self):
        self.page = "attack"
        try:
            self.spoofer_bin, self.mitigator_bin = self.ensure_spoofer_tools()
        except Exception as e:
            self.show_error(str(e))
            return

        self.clear_content()
        self.set_badge("ATTACK CONTROL")
        self.back_callback = self.leave_attack_screen
        self.selected_callback = None
        self.root.update_idletasks()

        # Rodapé fixo (fora da área com scroll), igual ao padrão usado
        # nas outras telas do app — os botões ficam sempre visíveis,
        # mesmo que o conteúdo do meio precise rolar.
        footer = tk.Frame(self.content, bg=SURFACE)
        footer.pack(
            side="bottom",
            fill="x",
            padx=self.CONTENT_PAD,
            pady=(self.px(10), self.FOOTER_BOTTOM_PAD),
        )

        outer = tk.Frame(self.content, bg=SURFACE)
        outer.pack(fill="both", expand=True)

        canvas = tk.Canvas(outer, bg=SURFACE, highlightthickness=0, bd=0)
        scrollbar = ModernScrollbar(
            outer,
            command=canvas.yview,
            width=self.SCROLLBAR_W,
            bg=SURFACE,
            thumb=BORDER,
            thumb_active=MUTED,
        )
        canvas.configure(yscrollcommand=scrollbar.set)

        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        self.attack_scroll_canvas = canvas

        page = tk.Frame(canvas, bg=SURFACE)
        page_id = canvas.create_window((0, 0), window=page, anchor="nw")

        def _sync_scrollregion(_event=None):
            canvas.configure(scrollregion=canvas.bbox("all"))

        def _sync_width(event):
            canvas.itemconfigure(page_id, width=event.width)
            _sync_scrollregion()

        def _wheel(event):
            if getattr(event, "num", None) == 4:
                _uavedusim_safe_yview_scroll(canvas, -3, "units")
            elif getattr(event, "num", None) == 5:
                _uavedusim_safe_yview_scroll(canvas, 3, "units")
            elif getattr(event, "delta", 0):
                _uavedusim_safe_yview_scroll(canvas, int(-1 * (event.delta / 120)), "units")

        page.bind("<Configure>", _sync_scrollregion)
        canvas.bind("<Configure>", _sync_width)
        canvas.bind(
            "<Enter>",
            lambda _e: (
                canvas.bind_all("<MouseWheel>", _wheel),
                canvas.bind_all("<Button-4>", _wheel),
                canvas.bind_all("<Button-5>", _wheel),
            ),
        )
        canvas.bind(
            "<Leave>",
            lambda _e: (
                canvas.unbind_all("<MouseWheel>"),
                canvas.unbind_all("<Button-4>"),
                canvas.unbind_all("<Button-5>"),
            ),
        )

        header = tk.Frame(page, bg=SURFACE)
        header.pack(fill="x", padx=self.CONTENT_PAD, pady=(self.px(16), 0))

        tk.Label(
            header,
            text="Ataques GPS Jamming",
            bg=SURFACE,
            fg=TEXT,
            font=(self.FONT_FAMILY, self.fs(17), "bold"),
        ).pack(anchor="w")

        tk.Label(
            header,
            text="Envie takeoff e destino. Padrão: dist. 350 m, rot. 0 graus, alt. 2.5 m.",
            bg=SURFACE,
            fg=MUTED,
            font=(self.FONT_FAMILY, self.FS_SMALL),
        ).pack(anchor="w", pady=(self.px(6), 0))

        self.takeoff_status_var = tk.StringVar()
        self.destination_status_var = tk.StringVar()
        self.destination_forward_var = tk.StringVar(value=self.destination_forward_value)
        self.destination_right_var = tk.StringVar(value=self.destination_right_value)
        self.destination_altitude_var = tk.StringVar(value=self.destination_altitude_value)

        prereq_panel = tk.Frame(page, bg=SURFACE2, highlightbackground=BORDER, highlightthickness=1)
        self.prereq_panel = prereq_panel
        prereq_panel.pack(fill="x", padx=self.CONTENT_PAD, pady=(self.px(9), 0))
        prereq_panel.grid_columnconfigure(0, weight=1)
        prereq_panel.grid_columnconfigure(1, weight=1)
        prereq_panel.grid_columnconfigure(2, weight=0)

        takeoff_box = tk.Frame(prereq_panel, bg=SURFACE2)
        self.takeoff_box = takeoff_box
        takeoff_box.grid(row=0, column=0, sticky="nsew", padx=(self.px(14), self.px(8)), pady=self.px(8))

        tk.Label(
            takeoff_box,
            text="1. Decolagem",
            bg=SURFACE2,
            fg=TAG_FG,
            font=(self.FONT_FAMILY, self.fs(11), "bold"),
        ).pack(anchor="w")

        self.takeoff_status_label = tk.Label(
            takeoff_box,
            textvariable=self.takeoff_status_var,
            bg=SURFACE2,
            fg=(OK if self.takeoff_sent else WARN),
            font=(self.FONT_FAMILY, self.FS_TINY, "bold"),
            justify="left",
            anchor="w",
            wraplength=self.px(180),
        )
        self.takeoff_status_label.pack(anchor="w", fill="x", pady=(self.px(6), 0))

        destination_box = tk.Frame(prereq_panel, bg=SURFACE2)
        self.destination_box = destination_box
        destination_box.grid(row=0, column=1, sticky="nsew", padx=(self.px(8), self.px(8)), pady=self.px(8))

        tk.Label(
            destination_box,
            text="2. Destino",
            bg=SURFACE2,
            fg=TAG_FG,
            font=(self.FONT_FAMILY, self.fs(11), "bold"),
        ).pack(anchor="w")

        self.destination_status_label = tk.Label(
            destination_box,
            textvariable=self.destination_status_var,
            bg=SURFACE2,
            fg=(OK if self.destination_sent else WARN),
            font=(self.FONT_FAMILY, self.FS_TINY, "bold"),
            justify="left",
            anchor="w",
            wraplength=self.px(220),
        )
        self.destination_status_label.pack(anchor="w", fill="x", pady=(self.px(6), self.px(8)))

        destination_grid = tk.Frame(destination_box, bg=SURFACE2)
        destination_grid.pack(anchor="w", fill="x")

        self.destination_entries = []

        def _destination_field(parent, col, label, var, width=8):
            wrap = tk.Frame(parent, bg=SURFACE2)
            wrap.grid(row=0, column=col, sticky="w", padx=(0, self.px(10)))

            tk.Label(
                wrap,
                text=label,
                bg=SURFACE2,
                fg=MUTED,
                font=(self.FONT_FAMILY, self.FS_TINY),
            ).pack(anchor="w")

            entry = tk.Entry(
                wrap,
                textvariable=var,
                relief="flat",
                bd=0,
                justify="center",
                bg="#11111b",
                fg=TEXT,
                insertbackground=TEXT,
                width=width,
                font=(self.FONT_FAMILY, self.fs(10), "bold"),
            )
            entry.pack(fill="x", ipady=self.px(3))
            self.destination_entries.append(entry)
            return entry

        _destination_field(destination_grid, 0, "Distância (m)", self.destination_forward_var, 9)
        _destination_field(destination_grid, 1, "Rotação (graus)", self.destination_right_var, 9)
        _destination_field(destination_grid, 2, "Alt. rel. (m)", self.destination_altitude_var, 9)

        prereq_buttons = tk.Frame(prereq_panel, bg=SURFACE2)
        self.prereq_buttons = prereq_buttons
        prereq_buttons.grid(row=0, column=2, sticky="e", padx=(self.px(8), self.px(14)), pady=self.px(8))

        self.takeoff_button = tk.Button(
            prereq_buttons,
            text=("Takeoff enviado" if self.takeoff_sent else "Enviar commander takeoff"),
            command=self.send_takeoff_from_ui,
            bg=("#35433b" if self.takeoff_sent else ACCENT),
            fg=(SEL_FG if self.takeoff_sent else "#11111b"),
            activebackground=("#445849" if self.takeoff_sent else ACCENT_DIM),
            activeforeground="#11111b",
            disabledforeground=DISABLED_FG,
            relief="flat",
            bd=0,
            padx=self.px(12),
            pady=self.px(6),
            font=(self.FONT_FAMILY, self.FS_SMALL, "bold"),
            cursor="hand2",
        )
        self.takeoff_button.pack(anchor="e", fill="x")

        self.destination_button = tk.Button(
            prereq_buttons,
            text=("Reenviar destino" if self.destination_sent else "Enviar destino"),
            command=self.send_destination_from_ui,
            bg=("#35433b" if self.destination_sent else DISABLED_BG),
            fg=(SEL_FG if self.destination_sent else DISABLED_FG),
            activebackground=("#445849" if self.destination_sent else DISABLED_BG),
            activeforeground=DISABLED_FG,
            disabledforeground=DISABLED_FG,
            relief="flat",
            bd=0,
            padx=self.px(12),
            pady=self.px(6),
            font=(self.FONT_FAMILY, self.FS_SMALL, "bold"),
            cursor="hand2",
        )
        self.destination_button.pack(anchor="e", fill="x", pady=(self.px(6), 0))

        self.destination_timer_var = tk.StringVar(value="")
        self.destination_timer_label = tk.Label(
            prereq_buttons,
            textvariable=self.destination_timer_var,
            bg=SURFACE2,
            fg=WARN,
            font=(self.FONT_FAMILY, self.FS_TINY, "bold"),
            justify="center",
            anchor="center",
            wraplength=self.px(220),
        )
        self.destination_timer_label.pack(anchor="e", fill="x", pady=(self.px(4), 0))

        prereq_panel.bind("<Configure>", self._reflow_prereq_layout)
        takeoff_box.bind("<Configure>", self._reflow_prereq_layout)
        destination_box.bind("<Configure>", self._reflow_prereq_layout)
        self.root.after(80, self._reflow_prereq_layout)

        self.attack_body = tk.Frame(page, bg=SURFACE)
        self.attack_body.pack(
            fill="both",
            expand=True,
            padx=self.CONTENT_PAD,
            pady=(self.px(12), 0),
        )

        self.attack_left = tk.Frame(
            self.attack_body,
            bg=SURFACE2,
            highlightbackground=BORDER,
            highlightthickness=1,
        )

        self.attack_right = tk.Frame(
            self.attack_body,
            bg=SURFACE2,
            highlightbackground=BORDER,
            highlightthickness=1,
        )

        # Posicionamento inicial (fallback) — sem isso, os cards só
        # apareciam quando _reflow_attack_layout rodava de fato na tela
        # (o que, neste ambiente, só acontecia quando o mouse entrava na
        # janela). Colocamos um grid padrão aqui e deixamos o reflow
        # (chamado logo depois) apenas ajustar as proporções conforme a
        # largura real da janela.
        self.attack_body.grid_columnconfigure(0, weight=3, minsize=1)
        self.attack_body.grid_columnconfigure(1, weight=2, minsize=1)
        self.attack_body.grid_rowconfigure(0, weight=1)
        self.attack_left.grid(row=0, column=0, sticky="nsew", padx=(0, self.px(14)), pady=0)
        self.attack_right.grid(row=0, column=1, sticky="nsew", padx=0, pady=0)

        left_inner = tk.Frame(self.attack_left, bg=SURFACE2)
        left_inner.pack(fill="both", expand=True, padx=self.px(16), pady=self.px(14))

        tk.Label(
            left_inner,
            text="Tipo de ataque",
            bg=SURFACE2,
            fg=TAG_FG,
            font=(self.FONT_FAMILY, self.fs(12), "bold"),
        ).pack(anchor="w")

        self.attack_rows = {}
        self.attack_desc_var = tk.StringVar()
        self.noise_entry_var = tk.StringVar(value=self.attack_noise_value)
        self.pulse_entry_var = tk.StringVar(value=self.attack_pulse_value)

        self._make_attack_row(left_inner, "noise", "Jamming Noise", self.noise_entry_var, "ex: 80")
        self._make_attack_row(left_inner, "blackout", "Jamming Blackout", None, None)
        self._make_attack_row(left_inner, "pulse", "Jamming Pulse", self.pulse_entry_var, "ex: 0.3")

        self.attack_desc_label = tk.Label(
            left_inner,
            textvariable=self.attack_desc_var,
            bg=SURFACE2,
            fg=MUTED,
            font=(self.FONT_FAMILY, self.FS_SMALL),
            justify="left",
            wraplength=self.px(560),
        )
        self.attack_desc_label.pack(anchor="w", fill="x", pady=(self.px(16), 0))

        self.attack_warning_var = tk.StringVar(value="")
        self.attack_warning_label = tk.Label(
            left_inner,
            textvariable=self.attack_warning_var,
            bg=SURFACE2,
            fg=WARN,
            font=(self.FONT_FAMILY, self.FS_SMALL, "bold"),
            justify="left",
            anchor="w",
            wraplength=0,
        )
        self.attack_warning_label.pack(anchor="w", fill="x", pady=(self.px(10), 0))

        right_inner = tk.Frame(self.attack_right, bg=SURFACE2)
        right_inner.pack(fill="both", expand=True, padx=self.px(18), pady=self.px(14))

        tk.Label(
            right_inner,
            text="Mitigação",
            bg=SURFACE2,
            fg=TAG_FG,
            font=(self.FONT_FAMILY, self.fs(12), "bold"),
        ).pack(anchor="w")

        self.mitigation_desc_label = tk.Label(
            right_inner,
            text="Ativa/desativa a mitigação do simulador.",
            bg=SURFACE2,
            fg=MUTED,
            font=(self.FONT_FAMILY, self.FS_SMALL),
            justify="left",
            wraplength=self.px(320),
        )
        self.mitigation_desc_label.pack(anchor="w", fill="x", pady=(self.px(10), self.px(12)))

        toggle_wrap = tk.Frame(right_inner, bg=SURFACE2)
        toggle_wrap.pack(anchor="w", fill="x")

        slider_w = self.px(86)
        slider_h = self.px(34)

        self.mitigation_slider = tk.Canvas(
            toggle_wrap,
            width=slider_w,
            height=slider_h,
            bg=SURFACE2,
            highlightthickness=0,
            bd=0,
            cursor="hand2",
        )
        self.mitigation_slider.pack(side="left")
        self.mitigation_slider.bind("<Button-1>", lambda _e: self.toggle_mitigation_ui())

        self.mitigation_label = tk.Label(
            toggle_wrap,
            text="",
            bg=SURFACE2,
            fg=MUTED,
            font=(self.FONT_FAMILY, self.fs(12), "bold"),
        )
        self.mitigation_label.pack(side="left", padx=(self.px(12), 0))

        status_frame = tk.Frame(
            right_inner,
            bg=BG,
            highlightbackground=BORDER,
            highlightthickness=1,
        )
        status_frame.pack(fill="both", expand=True, pady=(self.px(14), 0))

        status_header = tk.Frame(status_frame, bg=BG)
        status_header.pack(fill="x", padx=self.px(14), pady=(self.px(10), self.px(5)))

        tk.Label(
            status_header,
            text="Status",
            bg=BG,
            fg=MUTED,
            font=(self.FONT_FAMILY, self.fs(11), "bold"),
        ).pack(side="left")

        clear_lbl = tk.Label(
            status_header,
            text="Limpar",
            bg=BG,
            fg=BORDER,
            font=(self.FONT_FAMILY, self.FS_TINY, "underline"),
            cursor="hand2",
        )
        clear_lbl.pack(side="right")
        clear_lbl.bind("<Button-1>", lambda _e: self.clear_status_log())

        status_text_wrap = tk.Frame(status_frame, bg="#11111b")
        status_text_wrap.pack(fill="both", expand=True, padx=self.px(14), pady=(0, self.px(10)))
        status_text_wrap.grid_columnconfigure(0, weight=1)
        status_text_wrap.grid_rowconfigure(0, weight=1)

        self.status_text = tk.Text(
            status_text_wrap,
            height=9,
            bg="#11111b",
            fg=TEXT,
            insertbackground=TEXT,
            relief="flat",
            bd=0,
            wrap="word",
            font=(self.FONT_FAMILY, self.FS_SMALL),
        )
        self.status_text.grid(row=0, column=0, sticky="nsew")

        status_scroll = ModernScrollbar(
            status_text_wrap,
            command=self.status_text.yview,
            width=self.SCROLLBAR_W,
            bg="#11111b",
            thumb=BORDER,
            thumb_active=MUTED,
        )
        status_scroll.grid(row=0, column=1, sticky="ns")
        self.status_text.configure(yscrollcommand=status_scroll.set)

        # Tags de estilo para diferenciar tipo de evento no log em tempo real.
        self.status_text.tag_configure("time", foreground=BORDER)
        self.status_text.tag_configure("title", foreground=TEXT, font=(self.FONT_FAMILY, self.FS_SMALL, "bold"))
        self.status_text.tag_configure("detail", foreground=MUTED)
        self.status_text.tag_configure("ok", foreground=OK, font=(self.FONT_FAMILY, self.FS_SMALL, "bold"))
        self.status_text.tag_configure("warn", foreground=WARN, font=(self.FONT_FAMILY, self.FS_SMALL, "bold"))
        self.status_text.tag_configure("danger", foreground=DANGER, font=(self.FONT_FAMILY, self.FS_SMALL, "bold"))

        self.status_box_enabled = True
        self.status_text.configure(state="disabled")

        if not hasattr(self, "status_log_entries"):
            self.status_log_entries = []

        self._render_status_log()

        footer.grid_columnconfigure(0, weight=1)
        footer.grid_columnconfigure(1, weight=0)

        self.attack_hint_label = tk.Label(
            footer,
            text="",
            bg=SURFACE,
            fg=BORDER,
            font=(self.FONT_FAMILY, self.FS_TINY),
            anchor="w",
            justify="left",
        )
        self.attack_hint_label.grid(row=0, column=0, sticky="ew", padx=(0, self.px(10)))

        self.attack_btn_frame = tk.Frame(footer, bg=SURFACE)
        self.attack_btn_frame.grid(row=0, column=1, sticky="e")

        self.restart_button = RoundedButton(
            self.attack_btn_frame,
            text="Reiniciar simulação",
            command=self.restart_simulation_from_attack,
            outer_bg=SURFACE,
            fill=SURFACE2,
            fill_hover=BORDER,
            fill_press=BORDER,
            text_color=TEXT,
            text_color_hover=TEXT,
            radius=self.px(11),
            height=self.px(37),
            width=self.px(160),
            font=(self.FONT_FAMILY, self.FS_SMALL, "bold"),
        )
        self.restart_button.pack(side="left", padx=(0, self.px(8)))

        _uavedusim_footer_close_button(
            self, self.attack_btn_frame, self.leave_attack_screen,
        ).pack(side="left", padx=(0, self.px(8)))

        self.stop_button = RoundedButton(
            self.attack_btn_frame,
            text="Desligar jamming",
            command=self.stop_jamming_from_ui,
            outer_bg=SURFACE,
            fill="#3e2937",
            fill_hover="#553645",
            fill_press="#553645",
            text_color="#f2cdcd",
            text_color_hover="#f2cdcd",
            radius=self.px(11),
            height=self.px(37),
            width=self.px(150),
            font=(self.FONT_FAMILY, self.FS_BUTTON, "bold"),
        )
        self.stop_button.pack(side="left", padx=(0, self.px(8)))

        self.apply_button = _uavedusim_footer_primary_button(
            self, self.attack_btn_frame, self.apply_selected_attack_from_ui, "Aplicar",
            width=self.px(100),
        )
        self.apply_button.pack(side="left")

        self.attack_body.bind("<Configure>", self._reflow_attack_layout)
        footer.bind("<Configure>", self._reflow_attack_footer)

        self.update_attack_selection_ui()
        self.update_mitigation_ui()
        self.update_attack_lock_ui()

        # Força a geometria/repaint imediatamente, em vez de depender só
        # do after(80) — que, neste ambiente, só era "pintado" na tela
        # quando o mouse entrava na janela.
        self._reflow_attack_layout()
        self.root.update_idletasks()
        self.root.after(80, self._reflow_attack_layout)

    # Reorganiza o painel de decolagem e destino conforme a largura.
    # Reorganiza os paineis de ataque e mitigação.
    # Retorna o nome exibido para o tipo de ataque.
    def attack_label(self, attack_key):
        return {
            "noise": "Jamming Noise",
            "blackout": "Jamming Blackout",
            "pulse": "Jamming Pulse",
        }.get(attack_key, str(attack_key))

    # Explica, em linguagem simples, o que cada tipo de ataque faz ao GPS do drone.
    def _attack_effect_description(self, attack_key):
        return _uavedusim_translate({
            "noise": (
                "adiciona ruído aleatorio as coordenadas de GPS lidas pelo drone, fazendo a "
                "posição estimada 'tremer' e se afastar aos poucos da posição real."
            ),
            "blackout": (
                "bloqueia completamente o sinal de GPS, deixando o drone sem nenhuma leitura "
                "de posição válida."
            ),
            "pulse": (
                "liga e desliga o jamming em pulsos, causando perda intermitente de posição "
                "GPS (o drone alterna entre 'ver' e 'não ver' sua posição)."
            ),
        }.get(attack_key, "interfere no sinal de GPS recebido pelo drone."))

    # Explica o que se espera observar no drone/telemetria enquanto o ataque está ativo.
    def _attack_expected_observation(self, attack_key):
        return _uavedusim_translate({
            "noise": (
                "Espere ver a posição estimada 'tremendo' na telemetria, com pequenos desvios "
                "da rota planejada, mesmo sem perder o GPS por completo."
            ),
            "blackout": (
                "Espere ver o GPS sem fix (sem posição válida); o drone pode parar de responder "
                "corretamente a comandos de posição enquanto o sinal não volta."
            ),
            "pulse": (
                "Espere ver o GPS alternando entre 'com posição' e 'sem posição' repetidamente, "
                "com saltos na posição estimada a cada vez que o sinal retorna. Esses saltos não "
                "são necessariamente pequenos: quanto mais tempo o GPS ficar fora, maior pode ser "
                "a diferença entre a posição estimada e a posição real quando o sinal volta."
            ),
        }.get(attack_key, "Acompanhe a telemetria para ver o efeito do ataque no GPS."))

    # Explica, em linguagem simples, o que a mitigação faz de forma geral (independente do ataque).
    def _mitigation_general_description(self):
        return _uavedusim_translate("proteção ativada contra ataques ao GPS.")

    # Explica, de forma didática, o que a mitigação faz especificamente contra cada ataque.
    def _mitigation_defense_description(self, attack_key):
        return _uavedusim_translate({
            "noise": (
                "o GPS ainda envia leituras, só que ruidosas, então a mitigação usa um filtro de "
                "Kalman que combina essas leituras com o barômetro (para a altitude) e com o "
                "movimento esperado do drone, dando menos peso as medições que fogem muito do "
                "previsto. Isso suaviza o 'tremor' e mantem a posição estimada próxima da real."
            ),
            "blackout": (
                "o GPS parou de enviar leituras válidas, então a mitigação usa dead reckoning: "
                "parte da última posição confiável e projeta a posição atual com a velocidade e "
                "direção do drone, apoiada pelo barômetro (altitude) e pela odometria (distância "
                "percorrida), ate o sinal de GPS voltar."
            ),
            "pulse": (
                "o sinal de GPS aparece e desaparece, então a mitigação alterna entre as duas "
                "estratégias: usa o filtro de Kalman (com apoio do barômetro) enquanto o sinal "
                "esta presente, mesmo que ruidoso, e passa para dead reckoning com odometria nos "
                "momentos em que o sinal some, evitando saltos bruscos quando o GPS retorna."
            ),
        }.get(attack_key, "a mitigação está atuando para reduzir o efeito desse ataque."))

    # Explica o risco específico de não ter mitigação ativa contra cada ataque.
    def _attack_no_mitigation_description(self, attack_key):
        return _uavedusim_translate({
            "noise": (
                "o GPS ruidoso chega direto ao PX4 sem nenhuma correção. O drone pode se desviar "
                "da rota, oscilar de posição e perder estabilidade ate não conseguir alcançar o "
                "destino configurado."
            ),
            "blackout": (
                "sem nenhuma leitura válida de GPS chegando ao PX4, o próprio piloto automático "
                "deve acionar failsafe e iniciar um pouso de emergência (RTL/land) usando apenas a "
                "posição estimada disponível. Como essa posição pode estar desatualizada ou incorreta, "
                "o drone corre o risco de pousar em terrenos inadequados, como água, telhados, "
                "estradas ou vegetação densa, em vez de um local seguro."
            ),
            "pulse": (
                "cada vez que o GPS some, o PX4 fica sem leitura válida de posição; dependendo de "
                "quanto tempo o sinal ficar fora, o piloto automático pode acionar failsafe e "
                "iniciar um pouso de emergência em terreno inapropriado (água, telhados, estradas "
                "ou vegetação densa), assim como no blackout. Quando o sinal retorna, ainda "
                "ruidoso, o drone pode sofrer saltos de posição que não são necessariamente "
                "pequenos. Sem mitigação, essa alternancia entre 'sem GPS' e 'GPS ruidoso' aumenta "
                "o risco de desvio de rota e de um pouso de emergência em local inadequado."
            ),
        }.get(
            attack_key,
            "o GPS corrompido chega direto ao PX4. O drone pode se desviar da rota, perder "
            "estabilidade ou não alcançar o destino configurado.",
        ))

    # Monta a mensagem quando outro ataque está bloqueado.
    def active_attack_locked_message(self, requested_attack=None):
        active_key = getattr(self, "active_attack_key", None)
        active_label = self.attack_label(active_key) if active_key else _uavedusim_L("nenhum", "none")

        if requested_attack and active_key and requested_attack != active_key:
            requested_label = self.attack_label(requested_attack)
            return (
                _uavedusim_L(
                    f"{requested_label} bloqueado nesta simulação. Ataque atual: {active_label}. ",
                    f"{requested_label} is locked for this simulation. Current attack: {active_label}. ",
                )
                + _uavedusim_L(
                    "Para trocar o tipo de jamming, reinicie a simulação. ",
                    "To change the jamming type, restart the simulation. ",
                )
                + _uavedusim_L(
                    "O ataque atual ainda pode ter intensidade alterada, ser desligado/reativado e usar mitigação ON/OFF.",
                    "The current attack can still have its intensity changed, be turned off/on again, and use mitigation ON/OFF.",
                )
            )

        if active_key:
            return (
                _uavedusim_L(
                    f"Para trocar o tipo de jamming, reinicie a simulação. Ataque atual: {active_label}. ",
                    f"To change the jamming type, restart the simulation. Current attack: {active_label}. ",
                )
                + _uavedusim_L(
                    "Você ainda pode alterar a intensidade, desligar/reativar o ataque e ativar/desativar a mitigação.",
                    "You can still change the intensity, turn the attack off/on again, and toggle mitigation.",
                )
            )

        return ""

    # Atualiza o texto de descrição do ataque e garante que a área de
    # rolagem seja recalculada — sem isso, quando o texto cresce para
    # mais linhas (após quebra automática do wraplength), a parte de
    # baixo da descrição fica cortada porque o scrollregion antigo
    # (mais baixo) continua valendo.
    def _set_attack_desc_ui(self, text):
        self.attack_desc_var.set(text)

        if hasattr(self, "attack_scroll_canvas"):
            self.root.after(
                40,
                lambda: self.attack_scroll_canvas.configure(
                    scrollregion=self.attack_scroll_canvas.bbox("all")
                ),
            )

    def _set_attack_warning_ui(self, text="", level="warn"):
        self.attack_warning = text or ""

        if not hasattr(self, "attack_warning_var"):
            return

        color = DANGER if level == "danger" else WARN
        self.attack_warning_label.configure(fg=color, wraplength=0)

        # Quebra o texto manualmente em caracteres, na hora, uma única
        # vez — como a fonte é monoespaçada isso funciona sem precisar
        # reagir a eventos de redimensionamento, e por isso não tem
        # como entrar num ciclo de "quebra -> muda tamanho -> quebra
        # de novo" (o problema das tentativas anteriores).
        wrapped = _uavedusim_wrap_monospace_text(
            self, self.attack_warning, self.attack_left,
            font_spec=(self.FONT_FAMILY, self.FS_SMALL, "bold"),
        )
        self.attack_warning_var.set(wrapped)

        if hasattr(self, "attack_scroll_canvas"):
            self.root.after(
                40,
                lambda: self.attack_scroll_canvas.configure(
                    scrollregion=self.attack_scroll_canvas.bbox("all")
                ),
            )

    # Retorna uma descrição curta do ataque selecionado.
    def _attack_description(self, attack_key):
        if not self.attack_unlocked():
            return self.prereq_status_message()

        base = _uavedusim_translate({
            "noise": (
                "Neste ataque, ruído é adicionado às leituras de GPS do drone, "
                "reduzindo a precisão da navegação. Valores maiores produzem "
                "interferências mais intensas. Exemplo de valor: 80."
            ),
            "blackout": (
                "Neste ataque, o sinal de GPS é completamente interrompido, "
                "impedindo que o drone receba informações de posicionamento. "
                "Este ataque não utiliza parâmetro de intensidade."
            ),
            "pulse": (
                "Neste ataque, a interferência é aplicada de forma intermitente, "
                "alternando entre períodos com e sem jamming. Isso simula falhas "
                "temporárias no sinal de GPS. Exemplo de valor: 0.3."
            ),
        }[attack_key])

        active_key = getattr(self, "active_attack_key", None)

        if active_key == attack_key:
            return base + _uavedusim_L(" Este e o ataque ativo desta simulação.", " This is the active attack in this simulation.")

        if active_key and attack_key != active_key:
            return _uavedusim_L(
                f"{self.attack_label(attack_key)} está bloqueado nesta simulação.",
                f"{self.attack_label(attack_key)} is locked in this simulation.",
            )

        return base

    def attack_unlocked(self):
        return bool(getattr(self, "takeoff_sent", False)) and bool(getattr(self, "destination_sent", False))

    def prereq_status_message(self):
        if not getattr(self, "takeoff_sent", False):
            return "Ataques bloqueados. Envie commander takeoff e depois o destino."
        if getattr(self, "destination_release_pending", False):
            return "Ataques bloqueados. Aguardando timer após o takeoff antes de liberar o destino."
        if not getattr(self, "destination_sent", False):
            return "Ataques bloqueados. Envie o destino padrão: distância + rotação."
        return "Takeoff e destino enviados. Ataques liberados."

    # Envia comandos para o console PX4 via FIFO.
    def send_px4_console_command(self, command):
        fifo = Path(getattr(self, "px4_console_fifo", RUNTIME_DIR / f"px4_console_{SESSION_ID}.fifo"))

        if not fifo.exists():
            log(f"FIFO do console PX4 não encontrado: {fifo}")
            return False, f"Console PX4 ainda não está pronto: {fifo}"

        try:
            fd = os.open(str(fifo), os.O_WRONLY | os.O_NONBLOCK)
        except OSError as e:
            log(f"Falha ao abrir FIFO do PX4: {fifo}: {e}")
            return False, "Não consegui enviar comando ao PX4. Verifique se o terminal PX4 ainda está aberto."

        try:
            os.write(fd, (command.rstrip() + "\n").encode("utf-8"))
        except OSError as e:
            log(f"Falha ao escrever no FIFO do PX4: {e}")
            return False, "Falha ao escrever comando no console PX4."
        finally:
            try:
                os.close(fd)
            except Exception:
                pass

        log(f"Comando enviado ao console PX4: {command}")
        return True, f"Comando enviado: {command}"

    def _set_takeoff_status_ui(self, text, ok=False, pending=False):
        if hasattr(self, "takeoff_status_var"):
            self.takeoff_status_var.set(text)

        if hasattr(self, "takeoff_status_label"):
            self.takeoff_status_label.configure(fg=(OK if ok else WARN))

        if hasattr(self, "takeoff_button"):
            if pending:
                button_text = "Aguardando Takeoff detected"
                button_bg = SURFACE2
                button_fg = WARN
                button_active_bg = BORDER
                button_state = "disabled"
                button_cursor = "watch"
            else:
                button_text = "Takeoff detectado" if ok else "Enviar commander takeoff"
                button_bg = "#35433b" if ok else ACCENT
                button_fg = SEL_FG if ok else "#11111b"
                button_active_bg = "#445849" if ok else ACCENT_DIM
                button_state = "normal"
                button_cursor = "hand2"

            self.takeoff_button.configure(
                text=button_text,
                bg=button_bg,
                fg=button_fg,
                activebackground=button_active_bg,
                activeforeground="#11111b",
                state=button_state,
                cursor=button_cursor,
            )

    def _set_destination_status_ui(self, text, ok=False):
        if hasattr(self, "destination_status_var"):
            self.destination_status_var.set(text)

        if hasattr(self, "destination_status_label"):
            self.destination_status_label.configure(fg=(OK if ok else WARN))

        # Durante o timer pos-takeoff, o contador fica em uma mensagem separada
        # abaixo do botão. Assim o botão não troca de texto/cor a cada segundo.
        if hasattr(self, "destination_button") and not getattr(self, "destination_release_pending", False):
            self.destination_button.configure(
                text=("Reenviar destino" if ok else "Enviar destino"),
                bg=("#35433b" if ok else ACCENT),
                fg=(SEL_FG if ok else "#11111b"),
                activebackground=("#445849" if ok else ACCENT_DIM),
                activeforeground="#11111b",
            )

    # Atualiza botões e mensagens de decolagem/destino.
    def update_prereq_ui(self):
        if self.page != "attack":
            return

        release_pending = bool(getattr(self, "destination_release_pending", False))
        remaining_s = 0

        if release_pending:
            remaining_s = int(
                max(0.0, getattr(self, "destination_release_until", 0.0) - time.monotonic()) + 0.999
            )

        if getattr(self, "takeoff_sent", False):
            if release_pending:
                self._set_takeoff_status_ui(
                    "Takeoff detectado pelo PX4.\nAguardando liberação do destino.",
                    ok=True,
                )
            else:
                self._set_takeoff_status_ui(
                    "Takeoff detectado pelo PX4.\nEnvio de destino liberado.",
                    ok=True,
                )
        elif getattr(self, "takeoff_waiting", False):
            self._set_takeoff_status_ui(
                "Aguardando confirmação do PX4:\nINFO  [commander] Takeoff detected",
                ok=False,
                pending=True,
            )
        else:
            self._set_takeoff_status_ui("Aguardando:\ncommander takeoff", ok=False)

        if getattr(self, "destination_sent", False):
            msg = (
                _uavedusim_L("Destino enviado:\n", "Destination sent:\n")
                + _uavedusim_L(
                    f"dist={self.destination_forward_value} m, rot={self.destination_right_value} graus,\n",
                    f"dist={self.destination_forward_value} m, rot={self.destination_right_value} deg,\n",
                )
                + f"alt={self.destination_altitude_value} m."
            )
            self._set_destination_status_ui(msg, ok=True)
        elif release_pending:
            # CORREÇÃO: Quebramos a linha manualmente e adicionamos uma terceira linha
            # com um espaço em branco. Assim, a altura fica idêntica às outras mensagens!
            self._set_destination_status_ui(
                _uavedusim_L(
                    "Destino aguardando liberação\napós o takeoff.\n ",
                    "Destination waiting to be released\nafter takeoff.\n ",
                ),
                ok=False,
            )
        else:
            msg = (
                _uavedusim_L("Destino pendente:\n", "Destination pending:\n")
                + _uavedusim_L(
                    f"dist={self.destination_forward_value} m, rot={self.destination_right_value} graus,\n",
                    f"dist={self.destination_forward_value} m, rot={self.destination_right_value} deg,\n",
                )
                + f"alt={self.destination_altitude_value} m."
            )
            self._set_destination_status_ui(msg, ok=False)

        if hasattr(self, "destination_timer_var"):
            # Garante que a Label mantenha sua altura com o espaço " "
            self.destination_timer_var.set(
                _uavedusim_L(f"Destino liberado em {remaining_s}s", f"Destination released in {remaining_s}s")
                if release_pending else " "
            )

        if hasattr(self, "destination_timer_label"):
            self.destination_timer_label.configure(
                fg=(WARN if release_pending else MUTED),
                wraplength=max(self.px(130), self.px(220)),
            )

        if hasattr(self, "destination_button"):
            can_send_destination = bool(getattr(self, "takeoff_sent", False)) and not release_pending

            if release_pending:
                self.destination_button.configure(
                    text="Enviar destino",
                    state="disabled",
                    cursor="watch",
                    bg=DISABLED_BG,
                    fg=DISABLED_FG,
                    activebackground=DISABLED_BG,
                    activeforeground=DISABLED_FG,
                    disabledforeground=DISABLED_FG,
                )
            elif can_send_destination:
                self.destination_button.configure(
                    state="normal",
                    cursor="hand2",
                )
            else:
                self.destination_button.configure(
                    state="disabled",
                    cursor="arrow",
                    bg=DISABLED_BG,
                    fg=DISABLED_FG,
                    activebackground=DISABLED_BG,
                    activeforeground=DISABLED_FG,
                    disabledforeground=DISABLED_FG,
                )

        self._reflow_prereq_layout()

    def _takeoff_detected_since(self, log_offset):
        ok, _reason = log_indicates_takeoff_detected(log_offset)
        return ok

    def _takeoff_ready_hint(self):
        return _uavedusim_L(
            "Verifique também no terminal PX4 se apareceu Ready for takeoff! "
            "antes de enviar novamente. Se ainda não apareceu, aguarde essa mensagem e tente de novo.",
            "Also check the PX4 terminal to see whether Ready for takeoff! appeared "
            "before sending again. If it hasn't appeared yet, wait for that message and try again.",
        )

    def _cancel_destination_release_timer(self):
        after_id = getattr(self, "destination_release_after_id", None)

        if after_id is not None:
            try:
                self.root.after_cancel(after_id)
            except Exception:
                pass

        self.destination_release_after_id = None

    def _set_destination_timer_message(self):
        if not getattr(self, "destination_release_pending", False):
            remaining_s = 0
        else:
            remaining_s = int(
                max(0.0, getattr(self, "destination_release_until", 0.0) - time.monotonic()) + 0.999
            )

        if hasattr(self, "destination_timer_var"):
            # Garante que a Label tenha altura usando um espaço " " em vez de ""
            self.destination_timer_var.set(
                _uavedusim_L(f"Destino liberado em {remaining_s}s", f"Destination released in {remaining_s}s")
                if remaining_s > 0 else " "
            )

        return remaining_s

    def _start_destination_release_timer(self):
        self._cancel_destination_release_timer()

        try:
            delay_s = int(float(os.environ.get("UAVEDUSIM_DEST_RELEASE_DELAY_S", "12")))
        except Exception:
            delay_s = 12

        delay_s = max(0, min(delay_s, 30))

        self.destination_release_pending = delay_s > 0
        self.destination_release_until = time.monotonic() + float(delay_s)

        if delay_s <= 0:
            self.destination_release_pending = False
            self.destination_release_until = 0.0
            self._set_destination_timer_message()
            self.update_prereq_ui()
            self.update_attack_selection_ui()
            self.update_attack_lock_ui()
            return

        # Atualiza o layout uma vez para exibir a mensagem abaixo do botão.
        # Depois disso, cada tick altera apenas o texto do contador.
        self.update_prereq_ui()
        self.destination_release_after_id = self.root.after(1000, self._tick_destination_release_timer)

    def _tick_destination_release_timer(self):
        self.destination_release_after_id = None

        if not getattr(self, "destination_release_pending", False):
            self._set_destination_timer_message()
            return

        remaining_s = self._set_destination_timer_message()

        if remaining_s <= 0:
            self.destination_release_pending = False
            self.destination_release_until = 0.0
            self._set_destination_timer_message()
            self._log_status_event("Destino liberado", "Envio de destino disponível.", level="ok")
            self.update_prereq_ui()
            self.update_attack_selection_ui()
            self.update_attack_lock_ui()
            return

        self.destination_release_after_id = self.root.after(1000, self._tick_destination_release_timer)

    def _finish_takeoff_wait(self, success, message):
        self.takeoff_waiting = False

        if success:
            self.takeoff_sent = True
            self.destination_sent = False
            status_msg = "Takeoff detectado pelo PX4."
            self._log_status_event("Takeoff detectado", "Destino sera liberado após o timer.", level="ok")
            self._start_destination_release_timer()
            return

        self.takeoff_sent = False
        self.destination_release_pending = False
        self.destination_release_until = 0.0
        self.update_prereq_ui()
        self._set_takeoff_status_ui(message, ok=False)
        self._log_status_event("Takeoff não enviado", message, level="danger")
        self.update_attack_lock_ui()

    # Envia commander takeoff e só libera destino após o PX4 confirmar Takeoff detected.
    def send_takeoff_from_ui(self):
        if getattr(self, "takeoff_waiting", False):
            return

        if getattr(self, "takeoff_sent", False):
            if getattr(self, "destination_release_pending", False):
                msg = "Takeoff detectado pelo PX4. Aguarde o timer do botão de destino terminar."
            else:
                msg = "Takeoff já foi detectado pelo PX4. O envio de destino esta liberado."
            self._set_takeoff_status_ui(msg, ok=True)
            return

        self.takeoff_waiting = True
        self.takeoff_sent = False
        self.destination_release_pending = False
        self.destination_release_until = 0.0
        self.destination_sent = False

        try:
            log_offset = LOG.stat().st_size
        except Exception:
            log_offset = 0

        max_attempts = max(1, int(float(os.environ.get("UAVEDUSIM_TAKEOFF_MAX_ATTEMPTS", "2"))))
        retry_interval = max(1.0, float(os.environ.get("UAVEDUSIM_TAKEOFF_RETRY_INTERVAL_S", "3.0")))
        detect_timeout = max(
            retry_interval * max_attempts + 3.0,
            float(os.environ.get("UAVEDUSIM_TAKEOFF_DETECT_TIMEOUT_S", "12.0")),
        )

        wait_msg = (
            "Enviando commander takeoff.\n"
            "Aguardando: INFO  [commander] Takeoff detected"
        )
        self._set_takeoff_status_ui(wait_msg, ok=False, pending=True)
        self._set_destination_status_ui("Destino bloqueado ate o PX4 confirmar Takeoff detected.", ok=False)
        self.update_prereq_ui()
        self._set_takeoff_status_ui(wait_msg, ok=False, pending=True)
        self.update_attack_lock_ui()

        def ui_waiting(text):
            def apply():
                if not getattr(self, "takeoff_waiting", False):
                    return
                self.update_prereq_ui()
                self._set_takeoff_status_ui(text, ok=False, pending=True)
            self.root.after(0, apply)

        def worker():
            attempts = 0
            next_send_time = 0.0
            start_time = time.monotonic()
            last_error = ""

            while time.monotonic() - start_time < detect_timeout:
                now = time.monotonic()

                if attempts < max_attempts and now >= next_send_time:
                    attempts += 1
                    ok, msg = self.send_px4_console_command("commander takeoff")

                    if not ok:
                        last_error = msg

                    ui_waiting(
                        "Aguardando PX4:\n"
                        "INFO  [commander] Takeoff detected"
                    )

                    next_send_time = now + retry_interval

                if self._takeoff_detected_since(log_offset):
                    self.root.after(
                        0,
                        lambda: self._finish_takeoff_wait(
                            True,
                            "Takeoff detectado pelo PX4. Aguardando timer para liberar destino.",
                        ),
                    )
                    return

                time.sleep(0.4)

            hint = self._takeoff_ready_hint()

            if last_error:
                message = (
                    _uavedusim_L(
                        "Não foi possível confirmar o takeoff no PX4 após duas tentativas. ",
                        "Couldn't confirm takeoff on PX4 after two attempts. ",
                    )
                    + _uavedusim_L(f"Último erro: {last_error}. ", f"Last error: {last_error}. ")
                    + hint
                )
            else:
                message = (
                    _uavedusim_L(
                        "O comando commander takeoff foi enviado, mas o PX4 não registrou "
                        "INFO  [commander] Takeoff detected dentro do tempo limite após duas tentativas. ",
                        "The commander takeoff command was sent, but PX4 didn't log "
                        "INFO  [commander] Takeoff detected within the time limit after two attempts. ",
                    )
                    + hint
                )

            self.root.after(0, lambda: self._finish_takeoff_wait(False, message))

        threading.Thread(target=worker, daemon=True).start()

    # Válida distância, rotação e altitude do destino.
    def _parse_destination_values(self):
        try:
            distance = float(self.destination_forward_var.get().strip().replace(",", "."))
            rotation_deg = float(self.destination_right_var.get().strip().replace(",", "."))
            rel_alt = float(self.destination_altitude_var.get().strip().replace(",", "."))
        except Exception:
            raise ValueError("Destino invalido. Use números em Distância, Rotação e Altitude. Exemplo: 350, 0, 2.5")

        if distance <= 0:
            raise ValueError("Distância precisa ser maior que zero.")

        if rel_alt <= 0:
            raise ValueError("Altitude relativa precisa ser maior que zero. Exemplo: 2.5")

        self.destination_forward_value = f"{distance:g}"
        self.destination_right_value = f"{rotation_deg:g}"
        self.destination_altitude_value = f"{rel_alt:g}"

        return distance, rotation_deg, rel_alt

    # Envia o destino configurado pela interface.
    def send_destination_from_ui(self):
        if not getattr(self, "takeoff_sent", False):
            msg = "Envie commander takeoff antes do destino."
            self._set_destination_status_ui(msg, ok=False)
            self._log_status_event("Destino bloqueado", msg, level="warn")
            self.update_ttck_lock_ui()
            return

        if getattr(self, "destination_release_pending", False):
            remaining_s = int(max(0.0, getattr(self, "destination_release_until", 0.0) - time.monotonic()) + 0.999)
            msg = _uavedusim_L(
                f"Aguarde o timer do botão de destino terminar antes de enviar o destino. Restam {remaining_s}s.",
                f"Wait for the destination button timer to finish before sending the destination. {remaining_s}s left.",
            )
            self._set_destination_status_ui(msg, ok=False)
            self._log_status_event("Destino aguardando timer\n\n\n", msg, level="warn")
            self.update_attack_lock_ui()
            return

        try:
            distance, rotation_deg, rel_alt = self._parse_destination_values()
        except ValueError as e:
            msg = str(e)
            self._set_destination_status_ui(msg, ok=False)
            self._log_status_event("Destino invalido", msg, level="danger")
            return

        self.destination_sent = False
        self._set_destination_status_ui("Enviando destino via MAVLink...", ok=False)
        self._log_status_event(
            "Enviando destino via MAVLink",
            _uavedusim_L(
                f"O drone vai andar {self.destination_forward_value} m a frente, girar "
                f"{self.destination_right_value} graus e ajustar a altitude para "
                f"{self.destination_altitude_value} m em relação ao ponto de decolagem.",
                f"The drone will move {self.destination_forward_value} m forward, rotate "
                f"{self.destination_right_value} degrees, and adjust altitude to "
                f"{self.destination_altitude_value} m relative to the takeoff point.",
            ),
            level="detail",
        )
        self.update_attack_lock_ui()

        if hasattr(self, "destination_button"):
            self.destination_button.configure(
                state="disabled",
                cursor="watch",
                bg=DISABLED_BG,
                fg=DISABLED_FG,
                activebackground=DISABLED_BG,
                activeforeground=DISABLED_FG,
                disabledforeground=DISABLED_FG,
            )

        def worker():
            ok, msg = self.send_destination_mavlink(distance, rotation_deg, rel_alt)

            def finish():
                if ok:
                    self.destination_sent = True
                    msg_ok = (
                        _uavedusim_L(
                            f"Destino enviado: dist={self.destination_forward_value} m, "
                            f"rot={self.destination_right_value} graus, alt={self.destination_altitude_value} m. ",
                            f"Destination sent: dist={self.destination_forward_value} m, "
                            f"rot={self.destination_right_value} deg, alt={self.destination_altitude_value} m. ",
                        )
                        + _uavedusim_L("Ataques liberados.", "Attacks unlocked.")
                    )
                    self._set_destination_status_ui(msg_ok, ok=True)
                    self._log_status_event(
                        _uavedusim_L("Drone em deslocamento", "Drone on the move"),
                        _uavedusim_L(
                            f"Indo {self.destination_forward_value} m a frente, girando "
                            f"{self.destination_right_value} graus e ajustando para "
                            f"{self.destination_altitude_value} m de altitude. Ataques liberados.",
                            f"Moving {self.destination_forward_value} m forward, rotating "
                            f"{self.destination_right_value} degrees, and adjusting to "
                            f"{self.destination_altitude_value} m of altitude. Attacks unlocked.",
                        ),
                        level="ok",
                    )
                else:
                    self.destination_sent = False
                    self._set_destination_status_ui(msg, ok=False)
                    self._log_status_event(_uavedusim_L("Falha ao enviar destino", "Failed to send destination"), msg, level="danger")

                self.update_prereq_ui()
                self.update_attack_selection_ui()

            self.root.after(0, finish)

        threading.Thread(target=worker, daemon=True).start()

    # Calcula o destino relativo e envia comando MAVLink.
    def send_destination_mavlink(self, distance_m, rotation_deg, rel_alt_m):
        script = r"""
import math
import os
import subprocess
import sys
import time

try:
    from pymavlink import mavutil
except Exception as first_error:
    try:
        print("[UAVEDUSIM] pymavlink não encontrado. Tentando instalar com pip --user...", flush=True)
        subprocess.check_call(
            [sys.executable, "-m", "pip", "install", "--user", "pymavlink"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.STDOUT,
        )
        from pymavlink import mavutil
    except Exception as install_error:
        print("[UAVEDUSIM] ERRO: pymavlink não está disponível.", flush=True)
        print("[UAVEDUSIM] Instale com: python3 -m pip install --user pymavlink", flush=True)
        print(str(install_error), flush=True)
        sys.exit(2)

distance_m = float(sys.argv[1])
rotation_deg = float(sys.argv[2])
target_rel_alt_m = float(sys.argv[3])

def env_float(name, default):
    try:
        return float(os.environ.get(name, str(default)))
    except Exception:
        return float(default)

# Velocidades usadas ao enviar o destino.
# A velocidade vertical evita subida muito lenta quando a altitude alvo
# e maior que a altitude atual do VANT.
dest_climb_speed_mps = env_float("UAVEDUSIM_DEST_CLIMB_SPEED_MPS", 1.5)
dest_climb_speed_mps = max(0.2, min(dest_climb_speed_mps, 5.0))

# Velocidade horizontal usada pelo DO_REPOSITION.
dest_horizontal_speed_mps = env_float("UAVEDUSIM_DEST_HORIZONTAL_SPEED_MPS", 5.0)
dest_horizontal_speed_mps = max(0.5, min(dest_horizontal_speed_mps, 20.0))

# Quando o destino esta longe e a altitude alvo e maior, o PX4 tende a
# subir seguindo uma rampa ate o ponto final. Isso deixa a velocidade
# vertical muito baixa. Esta opção faz primeiro uma subida quase vertical
# no ponto atual e só depois envia o deslocamento horizontal.
dest_alt_stage_enabled = os.environ.get("UAVEDUSIM_DEST_ALT_STAGE", "1") != "0"
dest_alt_stage_tolerance_m = env_float("UAVEDUSIM_DEST_ALT_TOL_M", 0.35)
dest_alt_stage_tolerance_m = max(0.10, min(dest_alt_stage_tolerance_m, 2.0))
dest_alt_stage_timeout_env = env_float("UAVEDUSIM_DEST_ALT_STAGE_TIMEOUT_S", 0.0)

urls_raw = (
    os.environ.get("UAVEDUSIM_MAVLINK_URLS")
    or os.environ.get("UAVEDUSIM_MAVLINK_URL")
    or "udpin:0.0.0.0:14540,udp:127.0.0.1:14540"
)
urls = [item.strip() for item in urls_raw.split(",") if item.strip()]

master = None
last_error = ""

for url in urls:
    try:
        print(f"[UAVEDUSIM] Tentando conexão MAVLink: {url}", flush=True)
        candidate = mavutil.mavlink_connection(
            url,
            source_system=246,
            source_component=190,
            autoreconnect=True,
        )
        hb = candidate.wait_heartbeat(timeout=10)
        if hb:
            master = candidate
            print(
                f"[UAVEDUSIM] Heartbeat recebido: system={master.target_system} component={master.target_component}",
                flush=True,
            )
            break
    except Exception as e:
        last_error = str(e)
        try:
            candidate.close()
        except Exception:
            pass

if master is None:
    print("[UAVEDUSIM] ERRO: não consegui conectar ao PX4 via MAVLink.", flush=True)
    if last_error:
        print("[UAVEDUSIM] Último erro: " + last_error, flush=True)
    print("[UAVEDUSIM] Dica: tente UAVEDUSIM_MAVLINK_URL=udpin:0.0.0.0:14540 ./uavedusim_gui.sh", flush=True)
    sys.exit(3)

def set_param_float(param_name, value):
    try:
        param_type = getattr(mavutil.mavlink, "MAV_PARAM_TYPE_REAL32", 9)

        master.mav.param_set_send(
            master.target_system,
            master.target_component,
            param_name.encode("ascii"),
            float(value),
            param_type,
        )

        deadline_param = time.time() + 1.2

        while time.time() < deadline_param:
            msg = master.recv_match(type="PARAM_VALUE", blocking=True, timeout=0.2)

            if msg is None:
                continue

            raw_id = getattr(msg, "param_id", "")
            if isinstance(raw_id, bytes):
                raw_id = raw_id.decode("ascii", errors="ignore")

            if str(raw_id).strip("\x00") == param_name:
                print(f"[UAVEDUSIM] Parâmetro {param_name} ajustado para {float(value):.2f}", flush=True)
                return True

        print(f"[UAVEDUSIM] Parâmetro {param_name} enviado sem confirmação.", flush=True)
        return True

    except Exception as e:
        print(f"[UAVEDUSIM] Aviso: não foi possível ajustar {param_name}: {e}", flush=True)
        return False

def send_change_speed(speed_type, speed_mps, label):
    try:
        master.mav.command_long_send(
            master.target_system,
            master.target_component,
            mavutil.mavlink.MAV_CMD_DO_CHANGE_SPEED,
            0,
            speed_type,
            float(speed_mps),
            -1,
            0,
            0,
            0,
            0,
        )

        deadline_speed = time.time() + 1.2

        while time.time() < deadline_speed:
            ack = master.recv_match(type="COMMAND_ACK", blocking=True, timeout=0.3)

            if ack is None:
                continue

            if getattr(ack, "command", None) != mavutil.mavlink.MAV_CMD_DO_CHANGE_SPEED:
                continue

            print(
                f"[UAVEDUSIM] COMMAND_ACK DO_CHANGE_SPEED ({label}) result={getattr(ack, 'result', None)}",
                flush=True,
            )
            return True

        print(f"[UAVEDUSIM] DO_CHANGE_SPEED ({label}) enviado sem ACK.", flush=True)
        return True

    except Exception as e:
        print(f"[UAVEDUSIM] Aviso: não foi possível enviar DO_CHANGE_SPEED ({label}): {e}", flush=True)
        return False

def request_message_interval(message_id, interval_us):
    try:
        master.mav.command_long_send(
            master.target_system,
            master.target_component,
            mavutil.mavlink.MAV_CMD_SET_MESSAGE_INTERVAL,
            0,
            message_id,
            interval_us,
            0,
            0,
            0,
            0,
            0,
        )
    except Exception:
        pass

request_message_interval(mavutil.mavlink.MAVLINK_MSG_ID_GLOBAL_POSITION_INT, 200000)
request_message_interval(mavutil.mavlink.MAVLINK_MSG_ID_ATTITUDE, 200000)

position = None
attitude = None
deadline = time.time() + 20

while time.time() < deadline:
    msg = master.recv_match(type=["GLOBAL_POSITION_INT", "ATTITUDE"], blocking=True, timeout=1)

    if msg is None:
        continue

    msg_type = msg.get_type()

    if msg_type == "GLOBAL_POSITION_INT" and getattr(msg, "lat", 0) != 0 and getattr(msg, "lon", 0) != 0:
        position = msg

    elif msg_type == "ATTITUDE":
        attitude = msg

    if position is not None and attitude is not None:
        break

if position is None:
    print("[UAVEDUSIM] ERRO: não recebi GLOBAL_POSITION_INT do PX4.", flush=True)
    sys.exit(4)

if attitude is None:
    print("[UAVEDUSIM] ERRO: não recebi ATTITUDE do PX4 para calcular a frente do drone.", flush=True)
    sys.exit(7)

lat = position.lat / 1e7
lon = position.lon / 1e7
alt_amsl_m = position.alt / 1000.0
current_rel_alt_m = position.relative_alt / 1000.0

if target_rel_alt_m > current_rel_alt_m + 0.2:
    print(
        "[UAVEDUSIM] Destino acima da altitude atual. "
        f"Ajustando subida para {dest_climb_speed_mps:.2f} m/s.",
        flush=True,
    )

    set_param_float("MPC_Z_V_AUTO_UP", dest_climb_speed_mps)
    set_param_float("MPC_Z_VEL_MAX_UP", max(dest_climb_speed_mps, 1.0))
    send_change_speed(2, dest_climb_speed_mps, "subida")

# Usa ATTITUDE.yaw como referência da frente real do drone.
# A rotação relativa afeta apenas a direção do destino, não o yaw comandado.
heading_deg = (math.degrees(float(attitude.yaw)) + 360.0) % 360.0

# Rotação relativa e uma direção em graus a partir da frente atual do drone.
# 0 = frente, 90 = direita, -90 = esquerda, 180 = voltar.
target_heading_deg = (heading_deg + rotation_deg) % 360.0
theta = math.radians(target_heading_deg)

north_m = distance_m * math.cos(theta)
east_m = distance_m * math.sin(theta)

earth_radius_m = 6378137.0
target_lat = lat + math.degrees(north_m / earth_radius_m)
target_lon = lon + math.degrees(east_m / (earth_radius_m * max(0.1, math.cos(math.radians(lat)))))

home_amsl_m = alt_amsl_m - current_rel_alt_m
target_alt_amsl_m = home_amsl_m + target_rel_alt_m

cmd = mavutil.mavlink.MAV_CMD_DO_REPOSITION
flags = getattr(mavutil.mavlink, "MAV_DO_REPOSITION_FLAGS_CHANGE_MODE", 1)

def send_reposition_to(lat_cmd, lon_cmd, alt_cmd_amsl, label):
    print(
        f"[UAVEDUSIM] Enviando DO_REPOSITION ({label}): "
        f"lat={lat_cmd:.7f}, lon={lon_cmd:.7f}, alt_amsl={alt_cmd_amsl:.2f}",
        flush=True,
    )
    master.mav.command_long_send(
        master.target_system,
        master.target_component,
        cmd,
        0,
        dest_horizontal_speed_mps,
        flags,
        0,
        float("nan"),
        lat_cmd,
        lon_cmd,
        alt_cmd_amsl,
    )

def send_reposition():
    send_reposition_to(target_lat, target_lon, target_alt_amsl_m, "destino final")

def wait_reposition_ack(timeout_s):
    accepted = {
        getattr(mavutil.mavlink, "MAV_RESULT_ACCEPTED", 0),
        getattr(mavutil.mavlink, "MAV_RESULT_IN_PROGRESS", 5),
    }

    deadline_local = time.time() + timeout_s

    while time.time() < deadline_local:
        ack = master.recv_match(type="COMMAND_ACK", blocking=True, timeout=1)
        if ack is None:
            continue

        if getattr(ack, "command", None) != cmd:
            continue

        result = getattr(ack, "result", None)
        print(f"[UAVEDUSIM] COMMAND_ACK DO_REPOSITION result={result}", flush=True)
        return result in accepted

    return None

def wait_until_relative_alt(target_rel_alt, tolerance_m, timeout_s):
    deadline_local = time.time() + timeout_s
    last_rel_alt = None
    next_log = 0.0

    while time.time() < deadline_local:
        msg = master.recv_match(type="GLOBAL_POSITION_INT", blocking=True, timeout=0.5)

        if msg is None:
            continue

        if getattr(msg, "lat", 0) == 0 or getattr(msg, "lon", 0) == 0:
            continue

        rel_alt = getattr(msg, "relative_alt", 0) / 1000.0
        last_rel_alt = rel_alt

        now = time.time()
        if now >= next_log:
            print(
                f"[UAVEDUSIM] Subida vertical: alt_rel={rel_alt:.2f} m, "
                f"alvo={target_rel_alt:.2f} m, tolerância={tolerance_m:.2f} m",
                flush=True,
            )
            next_log = now + 2.0

        if rel_alt >= target_rel_alt - tolerance_m:
            print(
                f"[UAVEDUSIM] Altitude alvo atingida: alt_rel={rel_alt:.2f} m.",
                flush=True,
            )
            return True

    if last_rel_alt is None:
        print("[UAVEDUSIM] Aviso: não recebi altitude durante a subida vertical.", flush=True)
    else:
        print(
            f"[UAVEDUSIM] Aviso: subida vertical não atingiu a altitude alvo dentro do tempo. "
            f"Última alt_rel={last_rel_alt:.2f} m, alvo={target_rel_alt:.2f} m.",
            flush=True,
        )

    return False

alt_delta_m = target_rel_alt_m - current_rel_alt_m
need_alt_stage = dest_alt_stage_enabled and alt_delta_m > dest_alt_stage_tolerance_m

if need_alt_stage:
    if dest_alt_stage_timeout_env > 0:
        alt_stage_timeout_s = dest_alt_stage_timeout_env
    else:
        alt_stage_timeout_s = max(12.0, min(120.0, (alt_delta_m / max(dest_climb_speed_mps, 0.2)) + 12.0))

    print(
        "[UAVEDUSIM] Destino com subida detectado. "
        "Executando primeiro uma subida vertical antes do deslocamento horizontal. "
        f"Delta_alt={alt_delta_m:.2f} m, timeout={alt_stage_timeout_s:.1f}s.",
        flush=True,
    )

    send_reposition_to(lat, lon, target_alt_amsl_m, "subida vertical")
    climb_ack = wait_reposition_ack(8)

    if climb_ack is None:
        print("[UAVEDUSIM] Aviso: ACK da subida vertical não foi recebido. Continuando a espera de altitude.", flush=True)
    elif not climb_ack:
        print("[UAVEDUSIM] Aviso: PX4 rejeitou a subida vertical separada. O destino final ainda sera enviado.", flush=True)

    wait_until_relative_alt(target_rel_alt_m, dest_alt_stage_tolerance_m, alt_stage_timeout_s)

print(
    "[UAVEDUSIM] Destino calculado: "
    f"lat={target_lat:.7f}, lon={target_lon:.7f}, alt_amsl={target_alt_amsl_m:.2f}, "
    f"yaw_base_attitude={heading_deg:.1f}, rotação_rel={rotation_deg:.1f}, direção_destino={target_heading_deg:.1f}, "
    f"yaw_comando=ignorado, distância={distance_m:.1f}, north={north_m:.1f}, east={east_m:.1f}, "
    f"alt_relativa_alvo={target_rel_alt_m:.2f}, "
    f"vel_subida={dest_climb_speed_mps:.2f}, vel_horizontal={dest_horizontal_speed_mps:.2f}",
    flush=True,
)

# O PX4 pode confirmar (ACK) o primeiro DO_REPOSITION enquanto ainda esta
# trocando para o modo Hold/Loiter, e nesse instante de transição ele pode
# ignorar a altitude enviada e manter a altitude atual. Por isso, enviamos
# o comando uma segunda vez pouco depois: nessa segunda chamada o drone já
# está em Hold/Loiter e a nova altitude e aplicada de forma confiável.
print("[UAVEDUSIM] Enviando 1a tentativa de DO_REPOSITION...", flush=True)
send_reposition()
first_ok = wait_reposition_ack(8)

if first_ok is None:
    print("[UAVEDUSIM] ERRO: destino enviado, mas ACK de DO_REPOSITION não foi recebido (1a tentativa).", flush=True)
    sys.exit(6)

if not first_ok:
    print("[UAVEDUSIM] ERRO: PX4 rejeitou o primeiro DO_REPOSITION.", flush=True)
    sys.exit(5)

time.sleep(1.5)

print("[UAVEDUSIM] Reenviando DO_REPOSITION para garantir a altitude configurada...", flush=True)
send_reposition()
second_ok = wait_reposition_ack(8)

if second_ok is None:
    print("[UAVEDUSIM] ERRO: destino enviado, mas ACK de DO_REPOSITION não foi recebido (2a tentativa).", flush=True)
    sys.exit(6)

if not second_ok:
    print("[UAVEDUSIM] ERRO: PX4 rejeitou o DO_REPOSITION na 2a tentativa (altitude pode não ter sido aplicada).", flush=True)
    sys.exit(5)

sys.exit(0)
"""
        script = _uavedusim_translate_script_text(script)
        try:
            timeout_s = int(float(os.environ.get("UAVEDUSIM_DEST_SEND_TIMEOUT_S", "180")))
            proc = subprocess.run(
                [sys.executable, "-c", script, str(distance_m), str(rotation_deg), str(rel_alt_m)],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                timeout=timeout_s,
            )
        except subprocess.TimeoutExpired:
            return False, _uavedusim_L("Timeout ao enviar destino via MAVLink.", "Timeout while sending destination via MAVLink.")

        if proc.stdout:
            for line in proc.stdout.splitlines():
                log(line)

        if proc.returncode == 0:
            return True, _uavedusim_L("Destino enviado via MAVLink.", "Destination sent via MAVLink.")

        return False, _uavedusim_L(
            f"Falha ao enviar destino via MAVLink. Código={proc.returncode}. Veja o log.",
            f"Failed to send destination via MAVLink. Code={proc.returncode}. See the log.",
        )

    # Bloqueia troca de ataque depois da primeira aplicacao.
    def update_attack_lock_ui(self):
        if self.page != "attack" or not hasattr(self, "attack_rows"):
            return

        self.update_prereq_ui()

        prereq_ok = self.attack_unlocked()
        active_key = getattr(self, "active_attack_key", None)

        if active_key and self.attack_type != active_key:
            self.attack_type = active_key

        apply_allowed = prereq_ok and (active_key is None or self.attack_type == active_key)

        if hasattr(self, "apply_button"):
            self.apply_button.set_enabled(apply_allowed)

        for key, widgets in self.attack_rows.items():
            blocked_by_active_attack = bool(active_key and key != active_key)
            row_enabled = prereq_ok and not blocked_by_active_attack
            selected = key == self.attack_type and row_enabled

            bg = SEL_BG if selected else SURFACE2
            label_fg = SEL_FG if selected else (TEXT if row_enabled else MUTED)
            border = "white" if selected else (TEXT if row_enabled else BORDER)
            cursor = "hand2" if row_enabled else "arrow"

            widgets["frame"].configure(bg=bg, highlightbackground=border, cursor=cursor)
            widgets["label"].configure(bg=bg, fg=label_fg, cursor=cursor)

            if widgets.get("entry_wrap") is not None:
                widgets["entry_wrap"].configure(bg=bg)
                for child in widgets["entry_wrap"].winfo_children():
                    if isinstance(child, tk.Label):
                        child.configure(bg=bg, fg=(SEL_FG if selected else MUTED))

            if widgets.get("entry") is not None:
                widgets["entry"].configure(
                    state=("normal" if row_enabled else "disabled"),
                    bg=("#181825" if selected else "#11111b"),
                    fg=TEXT,
                    disabledbackground="#11111b",
                    disabledforeground=MUTED,
                    insertbackground=TEXT,
                    cursor=("xterm" if row_enabled else "arrow"),
                )

        if hasattr(self, "attack_desc_var"):
            if not prereq_ok:
                self._set_attack_desc_ui(self.prereq_status_message())
                if hasattr(self, "attack_desc_label"):
                    self.attack_desc_label.configure(fg=MUTED)
                self._set_attack_warning_ui("")
            else:
                self._set_attack_desc_ui(self._attack_description(self.attack_type))
                if hasattr(self, "attack_desc_label"):
                    self.attack_desc_label.configure(fg=MUTED)

                if active_key:
                    self._set_attack_warning_ui(self.active_attack_locked_message(), level="warn")
                else:
                    self._set_attack_warning_ui("")

    # Cria a linha visual de cada ataque.
    # Seleciona o ataque quando ele ainda não está bloqueado.
    def select_attack_type(self, attack_key):
        if not self.attack_unlocked():
            msg = self.prereq_status_message()
            self._set_attack_status_ui(msg, level="warn")
            self.update_attack_lock_ui()
            return

        active_key = getattr(self, "active_attack_key", None)

        if active_key and attack_key != active_key:
            msg = self.active_attack_locked_message(attack_key)
            self.update_attack_lock_ui()
            self._set_attack_warning_ui(msg, level="danger")
            return

        self.attack_type = attack_key
        self.update_attack_selection_ui()

    # Atualiza destaque visual dos ataques.
    def update_attack_selection_ui(self):
        for key, widgets in self.attack_rows.items():
            selected = key == self.attack_type
            bg = SEL_BG if selected else SURFACE2
            fg = SEL_FG if selected else TEXT

            widgets["frame"].configure(bg=bg, highlightbackground=("white" if selected else TEXT))
            widgets["label"].configure(bg=bg, fg=fg)

            if widgets.get("entry_wrap") is not None:
                widgets["entry_wrap"].configure(bg=bg)
                for child in widgets["entry_wrap"].winfo_children():
                    if isinstance(child, tk.Label):
                        child.configure(bg=bg, fg=(SEL_FG if selected else MUTED))

            if widgets["entry"] is not None:
                widgets["entry"].configure(
                    bg="#181825" if selected else "#11111b",
                    fg=TEXT,
                    insertbackground=TEXT,
                )

        self._set_attack_desc_ui(self._attack_description(self.attack_type))
        self.update_attack_lock_ui()

    # Adiciona um evento didático ao log de status em tempo real (não apaga o histórico).
    def _log_status_event(self, title, detail="", level="detail"):
        entry = {
            "time": time.strftime("%H:%M:%S"),
            "title": (title or "").strip(),
            "detail": (detail or "").strip(),
            "level": level,
        }

        if not entry["title"]:
            return

        if not hasattr(self, "status_log_entries"):
            self.status_log_entries = []

        self.status_log_entries.append(entry)
        self.status_log_entries = self.status_log_entries[-200:]
        self.attack_status = f"{entry['title']}. {entry['detail']}".strip()

        self._append_status_entry(entry)

    # Desenha uma única entrada do log no final da caixa de Status.
    def _append_status_entry(self, entry):
        if not hasattr(self, "status_text") or not getattr(self, "status_box_enabled", True):
            return

        try:
            if not self.status_text.winfo_exists():
                return
        except Exception:
            return

        try:
            self.status_text.configure(state="normal")

            if self.status_text.index("end-1c") != "1.0":
                self.status_text.insert("end", "\n")

            title = _uavedusim_translate(entry["title"])
            self.status_text.insert("end", f"[{entry['time']}] ", "time")
            self.status_text.insert("end", f"{title}\n", entry.get("level", "title"))

            if entry.get("detail"):
                detail_lines = entry["detail"].split("\n")
                for line in detail_lines:
                    self.status_text.insert("end", f"    {_uavedusim_translate(line)}\n", "detail")

            self.status_text.configure(state="disabled")
            self.status_text.see("end")
        except Exception:
            # A tela pode ter sido trocada/destruída entre o agendamento
            # deste callback e sua execução (ex.: reinício de simulação
            # rodando em thread). Nesse caso, só ignoramos — o evento já
            # fica registrado em status_log_entries e aparece quando a
            # tela de ataques for reaberta.
            pass

    # Redesenha o log inteiro (usado ao reabrir a tela de ataques).
    def _render_status_log(self):
        if not hasattr(self, "status_text"):
            return

        try:
            if not self.status_text.winfo_exists():
                return
        except Exception:
            return

        try:
            self.status_text.configure(state="normal")
            self.status_text.delete("1.0", "end")
            self.status_text.configure(state="disabled")
        except Exception:
            return

        for entry in getattr(self, "status_log_entries", []):
            self._append_status_entry(entry)

    # Limpa o histórico do log de status.
    def clear_status_log(self):
        self.status_log_entries = []

        if hasattr(self, "status_text"):
            self.status_text.configure(state="normal")
            self.status_text.delete("1.0", "end")
            self.status_text.configure(state="disabled")

        self._log_status_event("Log limpo", "O histórico de eventos foi apagado.", level="detail")

    # Compatibilidade: mensagens antigas de status agora viram uma entrada do log em tempo real.
    def _set_attack_status_ui(self, text, level="detail"):
        text = text or ""
        self.attack_status = text

        if not text:
            return

        parts = text.split("\n", 1)
        title = parts[0].strip()
        detail = parts[1].strip() if len(parts) > 1 else ""
        self._log_status_event(title or text, detail, level=level)

    # Desenha o slider ON/OFF da mitigação.
    def update_mitigation_ui(self):
        if not hasattr(self, "mitigation_slider"):
            return

        on = self.mitigation_state == "on"
        self._draw_mitigation_slider()

        self.mitigation_label.configure(
            text=("ON" if on else "OFF"),
            fg=(SEL_FG if on else MUTED),
        )

    # Alterna o estado da mitigação pela interface.
    def toggle_mitigation_ui(self):
        self.mitigation_state = "off" if self.mitigation_state == "on" else "on"
        ok = self.send_mitigation_state() == 0

        if ok:
            if self.mitigation_state == "on":
                self._log_status_event(
                    _uavedusim_L("Mitigação ATIVADA", "Mitigation ENABLED"),
                    self._mitigation_general_description(),
                    level="ok",
                )
            else:
                self._log_status_event(
                    _uavedusim_L("Mitigação DESATIVADA", "Mitigation DISABLED"),
                    _uavedusim_L(
                        "proteção contra ataques ao GPS desativada.",
                        "protection against GPS attacks disabled.",
                    ),
                    level="warn",
                )

            active_key = getattr(self, "active_attack_key", None)
            if active_key and getattr(self, "jamming_active", False):
                label = self.attack_label(active_key)

                if self.mitigation_state == "on":
                    defense = self._mitigation_defense_description(active_key)
                    self._log_status_event(
                        _uavedusim_L(f"Mitigação em andamento: {label}", f"Mitigation in progress: {label}"),
                        _uavedusim_L(f"Para este ataque, {defense}", f"For this attack, {defense}"),
                        level="warn",
                    )
                else:
                    self._log_status_event(
                        _uavedusim_L(f"Sem mitigação: {label}", f"No mitigation: {label}"),
                        self._attack_no_mitigation_description(active_key),
                        level="danger",
                    )

            msg = _uavedusim_L(
                f"Mitigação {self.mitigation_state.upper()}.",
                f"Mitigation {self.mitigation_state.upper()}.",
            )
        else:
            self.mitigation_state = "off" if self.mitigation_state == "on" else "on"
            msg = _uavedusim_L("Falha ao alterar mitigação. Veja o log.", "Failed to change mitigation. See the log.")
            self._log_status_event(_uavedusim_L("Falha ao alterar mitigação", "Failed to change mitigation"), msg, level="danger")

        self.update_mitigation_ui()

    # Válida a intensidade informada para o ataque.
    def _parse_intensity_value(self, attack_key):
        if attack_key == "noise":
            raw = self.noise_entry_var.get().strip().replace(",", ".")
            if not raw:
                raise ValueError("Informe a intensidade do Jamming Noise. Exemplo: 80")
            float(raw)
            self.attack_noise_value = raw
            return raw

        if attack_key == "pulse":
            raw = self.pulse_entry_var.get().strip().replace(",", ".")
            if not raw:
                raise ValueError("Informe a intensidade do Jamming Pulse. Exemplo: 0.3")
            float(raw)
            self.attack_pulse_value = raw
            return raw

        return None

    # Aplica o ataque escolhido após decolagem e destino.
    def apply_selected_attack_from_ui(self):
        if not self.attack_unlocked():
            msg = self.prereq_status_message()
            self._set_attack_status_ui(msg, level="warn")
            self.update_attack_lock_ui()
            return

        active_key = getattr(self, "active_attack_key", None)

        if active_key and self.attack_type != active_key:
            msg = self.active_attack_locked_message(self.attack_type)
            self._set_attack_status_ui(msg, level="warn")
            self.update_attack_lock_ui()
            return

        try:
            intensity = self._parse_intensity_value(self.attack_type)
        except ValueError as e:
            self._set_attack_status_ui(str(e), level="danger")
            return

        self.execute_attack(self.attack_type, intensity)

    # Desativa o jamming mantendo a simulação aberta.
    def stop_jamming_from_ui(self):
        if self.stop_jamming_attack():
            self.jamming_active = False
            active_key = getattr(self, "active_attack_key", None)

            if active_key:
                self._log_status_event(
                    _uavedusim_L("Jamming desligado", "Jamming turned off"),
                    _uavedusim_L(
                        f"O ataque {self.attack_label(active_key)} parou de interferir no GPS. O sinal "
                        f"volta ao normal e o drone deve retomar a navegação normal em direção ao destino "
                        f"(mitigação={self.mitigation_state.upper()}). Para trocar o tipo de jamming, "
                        "reinicie a simulação.",
                        f"The {self.attack_label(active_key)} attack stopped interfering with GPS. The "
                        f"signal returns to normal and the drone should resume normal navigation toward "
                        f"the destination (mitigation={self.mitigation_state.upper()}). To change the "
                        "jamming type, restart the simulation.",
                    ),
                    level="ok",
                )
                msg = _uavedusim_L(
                    f"Jamming desligado | ataque da simulação={self.attack_label(active_key)} | mitigação={self.mitigation_state.upper()}",
                    f"Jamming turned off | simulation attack={self.attack_label(active_key)} | mitigation={self.mitigation_state.upper()}",
                )
            else:
                self._log_status_event(
                    _uavedusim_L("Jamming desligado", "Jamming turned off"),
                    _uavedusim_L(
                        f"Nenhum ataque estava ativo. Mitigação atual={self.mitigation_state.upper()}.",
                        f"No attack was active. Current mitigation={self.mitigation_state.upper()}.",
                    ),
                    level="ok",
                )
                msg = _uavedusim_L(
                    f"Jamming desligado | mitigação={self.mitigation_state.upper()}",
                    f"Jamming turned off | mitigation={self.mitigation_state.upper()}",
                )
        else:
            msg = _uavedusim_L("Falha ao desligar jamming. Veja o log.", "Failed to turn off jamming. See the log.")
            self._log_status_event(_uavedusim_L("Falha ao desligar jamming", "Failed to turn off jamming"), msg, level="danger")

        self.update_attack_lock_ui()

    # Executa comandos do spoofer/mitigator no ambiente Gazebo.
    def run_gazebo_shell(self, command):
        cmd = f"{load_gazebo_env_block()}\n{command}"
        log("Executando comando UAVJamSim")
        proc = subprocess.run(["bash", "-lc", cmd], text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)

        if proc.stdout:
            for line in proc.stdout.splitlines():
                log(line)

        log(f"Código de retorno: {proc.returncode}")
        return proc.returncode

    # Liga ou desliga a mitigação no simulador.
    def send_mitigation_state(self):
        state_arg = "--on" if self.mitigation_state == "on" else "--off"
        return self.run_gazebo_shell(f"{shell_quote(str(self.mitigator_bin))} {state_arg}")

    # Envia ao spoofer o tipo e a intensidade do jamming.
    def apply_jamming_attack(self, attack_key, intensity=None):
        jam_type = {"noise": "1", "blackout": "2", "pulse": "3"}[attack_key]

        if self.send_mitigation_state() != 0:
            return False

        if intensity is not None:
            return self.run_gazebo_shell(
                f"{shell_quote(str(self.spoofer_bin))} --jam {jam_type} {shell_quote(str(intensity))}"
            ) == 0

        return self.run_gazebo_shell(f"{shell_quote(str(self.spoofer_bin))} --jam {jam_type}") == 0

    # Envia comando para desligar o jamming.
    def stop_jamming_attack(self):
        self.send_mitigation_state()
        return self.run_gazebo_shell(f"{shell_quote(str(self.spoofer_bin))} --jam 0") == 0

    # Executa comandos auxiliares do menu de ataques.
    def execute_attack(self, attack_key, intensity):
        if not self.attack_unlocked():
            msg = self.prereq_status_message()
            self._set_attack_status_ui(msg, level="warn")
            self.update_attack_lock_ui()
            return

        active_key = getattr(self, "active_attack_key", None)

        if active_key and attack_key != active_key:
            msg = self.active_attack_locked_message(attack_key)
            self.update_attack_lock_ui()
            self._set_attack_warning_ui(msg, level="danger")
            return

        label = self.attack_label(attack_key)
        was_new_attack = active_key is None

        ok = self.apply_jamming_attack(attack_key, intensity)

        if ok:
            self.active_attack_key = attack_key
            self.jamming_active = True
            self.attack_type = attack_key

            effect = self._attack_effect_description(attack_key)
            expected = self._attack_expected_observation(attack_key)
            intensity_txt = _uavedusim_L(
                f" (intensidade={intensity})" if intensity is not None else "",
                f" (intensity={intensity})" if intensity is not None else "",
            )
            action_word = _uavedusim_L(
                "Aplicado" if was_new_attack else ("Atualizado" if intensity is not None else "Reativado"),
                "Applied" if was_new_attack else ("Updated" if intensity is not None else "Reactivated"),
            )

            title = f"{action_word}: {label}{intensity_txt}"
            attack_detail = _uavedusim_L(f"O ataque {label} {effect} {expected}", f"The {label} attack {effect} {expected}")
            self._log_status_event(title, attack_detail, level="warn")

            if self.mitigation_state == "on":
                defense = self._mitigation_defense_description(attack_key)
                self._log_status_event(
                    _uavedusim_L(f"Mitigação em andamento: {label}", f"Mitigation in progress: {label}"),
                    _uavedusim_L(f"Para este ataque, {defense}", f"For this attack, {defense}"),
                    level="ok",
                )
            else:
                self._log_status_event(
                    _uavedusim_L(f"Sem mitigação: {label}", f"No mitigation: {label}"),
                    self._attack_no_mitigation_description(attack_key),
                    level="danger",
                )

            if was_new_attack:
                self._log_status_event(
                    _uavedusim_L("Aviso", "Warning"),
                    _uavedusim_L(
                        "Para lancar outro tipo de jamming nesta simulação, reinicie a simulação. "
                        "A intensidade deste ataque ainda pode ser alterada, e a mitigação pode ser "
                        "ligada/desligada a qualquer momento durante a execução.",
                        "To launch a different jamming type in this simulation, restart the simulation. "
                        "This attack's intensity can still be changed, and mitigation can be turned "
                        "on/off at any point during the run.",
                    ),
                    level="detail",
                )

            msg = title
        else:
            msg = _uavedusim_L(f"Falha ao aplicar {label}. Veja o log.", f"Failed to apply {label}. See the log.")
            self._log_status_event(_uavedusim_L("Falha ao aplicar ataque", "Failed to apply attack"), msg, level="danger")

        self.update_attack_selection_ui()

    def run(self):
        log("Iniciando UAVEDUSIM GUI persistente")
        log(f"APP_DIR={APP_DIR}")
        log(f"Sessão: XDG_SESSION_TYPE={os.environ.get('XDG_SESSION_TYPE','vazio')}, WAYLAND_DISPLAY={os.environ.get('WAYLAND_DISPLAY','vazio')}, DISPLAY={os.environ.get('DISPLAY','vazio')}")
        self.root.mainloop()


# Ajustes responsivos da interface.

def _uavedusim_responsive_setup_dimensions(self):
    screen_w = max(self.root.winfo_screenwidth(), 800)
    screen_h = max(self.root.winfo_screenheight(), 600)

    base_scale = min(screen_w / 1920.0, screen_h / 1080.0)
    default_scale = self._clamp(base_scale, 1.0, 1.45)
    self.UI_SCALE = self._env_float("UAVEDUSIM_UI_SCALE", default_scale)
    self.UI_SCALE = self._clamp(self.UI_SCALE, 0.72, 1.65)

    self.px = lambda value: max(1, int(round(value * self.UI_SCALE)))
    self.fs = lambda value: max(7, int(round(value * self.UI_SCALE)))

    # Janela com teto de tamanho mais conservador: em monitores grandes
    # ou de resolução incomum, evita que a janela cresça demais e sobre
    # espaço vazio abaixo do conteúdo (o conteúdo já tem um tamanho
    # "de app utilitário", não deve esticar pra ocupar a tela toda).
    width_frac_default = 0.66 if screen_w >= 1800 else (0.78 if screen_w >= 1280 else 0.90)
    height_frac_default = 0.64 if screen_h >= 1000 else (0.74 if screen_h >= 760 else 0.86)

    width_frac = self._env_float("UAVEDUSIM_UI_WIDTH_FRAC", width_frac_default)
    height_frac = self._env_float("UAVEDUSIM_UI_HEIGHT_FRAC", height_frac_default)

    max_w_limit = int(self._env_float("UAVEDUSIM_UI_MAX_W", self.px(1200)))
    max_h_limit = int(self._env_float("UAVEDUSIM_UI_MAX_H", self.px(800)))

    max_w = min(max(520, screen_w - self.px(24)), max_w_limit)
    max_h = min(max(360, screen_h - self.px(44)), max_h_limit)

    win_w = int(self._clamp(screen_w * width_frac, self.px(620), max_w))
    win_h = int(self._clamp(screen_h * height_frac, self.px(440), max_h))

    min_w = min(win_w, max(420, self.px(460)))
    min_h = min(win_h, max(300, self.px(320)))

    pos_x = max(0, (screen_w - win_w) // 2)
    pos_y = max(0, (screen_h - win_h) // 2)

    self.root.geometry(f"{win_w}x{win_h}+{pos_x}+{pos_y}")
    self.root.minsize(min_w, min_h)

    self.win_w = win_w
    self.win_h = win_h

    self.FONT_FAMILY = os.environ.get("UAVEDUSIM_FONT_FAMILY", "monospace")

    self.FS_TITLE = self.fs(14)
    self.FS_LOGO = self.fs(11)
    self.FS_SMALL = self.fs(8.5)
    self.FS_TINY = self.fs(7.5)
    self.FS_BUTTON = self.fs(9)
    self.FS_WIN = self.fs(8)
    self.FS_ROW = self.fs(10)
    self.FS_ICON = self.fs(12)
    self.FS_ARROW = self.fs(14)

    self.TITLEBAR_H = self.px(38)
    self.CONTENT_PAD = self.px(16)
    self.HEADER_TOP_PAD = self.px(12)
    self.LIST_TOP_PAD = self.px(8)
    self.FOOTER_TOP_PAD = self.px(10)
    self.FOOTER_BOTTOM_PAD = self.px(14)
    self.SIDEBAR_W = self.px(5)
    self.ROW_H = self.px(40)
    self.PADX = self.px(14)
    self.SEL_BAR_PADY = self.px(5)
    self.SEL_BAR_W = self.px(3)
    self.ICON_X = self.PADX + self.px(30)
    self.LABEL_X = self.PADX + self.px(50)
    self.SCROLLBAR_W = self.px(11)


# Ajusta estilos para telas pequenas e grandes.
def _uavedusim_responsive_setup_styles(self):
    self.style = ttk.Style(self.root)

    try:
        self.style.theme_use("clam")
    except Exception:
        pass

    try:
        _winbtn_font_family = _uavedusim_preferred_nerd_font_family(self)
    except Exception:
        _winbtn_font_family = self.FONT_FAMILY

    self.style.configure(
        "WinBtn.TButton",
        font=(_winbtn_font_family, self.fs(10), "bold"),
        padding=(self.px(2), self.px(0)),
        background=SURFACE2,
        foreground=MUTED,
        relief="flat",
        borderwidth=0,
    )
    self.style.map(
        "WinBtn.TButton",
        background=[("disabled", DISABLED_BG), ("active", BORDER)],
        foreground=[("disabled", DISABLED_FG), ("active", TEXT)],
    )

    self.style.configure(
        "CloseBtn.TButton",
        font=(_winbtn_font_family, self.fs(10), "bold"),
        padding=(self.px(2), self.px(0)),
        background=SURFACE2,
        foreground=MUTED,
        relief="flat",
        borderwidth=0,
    )
    self.style.map(
        "CloseBtn.TButton",
        background=[("disabled", DISABLED_BG), ("active", "#4e3241")],
        foreground=[("disabled", DISABLED_FG), ("active", "#f38ba8")],
    )

    self.style.configure(
        "Back.TButton",
        font=(self.FONT_FAMILY, self.FS_BUTTON),
        padding=(self.px(8), self.px(5)),
        background=SURFACE2,
        foreground=MUTED,
        relief="flat",
        borderwidth=0,
    )
    self.style.map(
        "Back.TButton",
        background=[("disabled", DISABLED_BG), ("active", BORDER)],
        foreground=[("disabled", DISABLED_FG), ("active", TEXT)],
    )

    self.style.configure(
        "Cancel.TButton",
        font=(self.FONT_FAMILY, self.FS_BUTTON),
        padding=(self.px(11), self.px(7)),
        background=SURFACE2,
        foreground=MUTED,
        relief="flat",
        borderwidth=0,
    )
    self.style.map(
        "Cancel.TButton",
        background=[("disabled", DISABLED_BG), ("active", BORDER)],
        foreground=[("disabled", DISABLED_FG), ("active", TEXT)],
    )

    self.style.configure(
        "Primary.TButton",
        font=(self.FONT_FAMILY, self.FS_BUTTON, "bold"),
        padding=(self.px(14), self.px(7)),
        background=ACCENT,
        foreground="#11111b",
        relief="flat",
        borderwidth=0,
    )
    self.style.map(
        "Primary.TButton",
        background=[("disabled", DISABLED_BG), ("active", ACCENT_DIM)],
        foreground=[("disabled", DISABLED_FG)],
    )

    try:
        self.style.layout(
            "Dark.Vertical.TScrollbar",
            [
                (
                    "Vertical.Scrollbar.trough",
                    {
                        "sticky": "ns",
                        "children": [
                            ("Vertical.Scrollbar.thumb", {"expand": "1", "sticky": "nswe"})
                        ],
                    },
                )
            ],
        )
    except Exception:
        pass

    self.style.configure(
        "Dark.Vertical.TScrollbar",
        background=BORDER,
        troughcolor=SURFACE,
        bordercolor=SURFACE,
        lightcolor=BORDER,
        darkcolor=BORDER,
        arrowcolor=SURFACE,
        relief="flat",
        borderwidth=0,
        arrowsize=0,
        width=self.SCROLLBAR_W,
    )
    self.style.map("Dark.Vertical.TScrollbar", background=[("active", MUTED)])

    self.style.configure(
        "UAVEDUSIM.Horizontal.TProgressbar",
        troughcolor=BG,
        background=ACCENT,
        bordercolor=BORDER,
        lightcolor=ACCENT,
        darkcolor=ACCENT,
    )


def _uavedusim_schedule_global_reflow(self, event=None):
    if event is not None and event.widget is not self.root:
        return

    # Alguns gerenciadores de janela (e sessões remotas/VNC) disparam um
    # <Configure> na janela raiz quando o mouse simplesmente sai/entra na
    # janela (foco), sem que a largura/altura realmente mudem. Sem esse
    # filtro, cada reflow refaz pack/grid de botões e frames à toa,
    # causando o "piscar" da tela. Só agendamos o reflow se o tamanho
    # mudou de fato.
    if event is not None:
        size = (event.width, event.height)
        last_size = getattr(self, "_last_reflow_size", None)
        if size == last_size:
            return
        self._last_reflow_size = size

    if getattr(self, "_reflow_after_id", None):
        try:
            self.root.after_cancel(self._reflow_after_id)
        except Exception:
            pass

    self._reflow_after_id = self.root.after(80, self._global_reflow)


def _uavedusim_global_reflow(self):
    self._reflow_after_id = None
    self._reflow_titlebar()

    if self.page == "menu":
        self._reflow_menu_footer()
    elif self.page == "wait":
        self._reflow_wait_status()
        self._reflow_wait_footer()
    elif self.page == "attack":
        self._reflow_prereq_layout()
        self._reflow_attack_layout()
        self._reflow_attack_footer()


def _uavedusim_reflow_titlebar(self):
    if not hasattr(self, "logo_lbl"):
        return

    width = max(self.root.winfo_width(), 1)

    if width < self.px(520):
        self.logo_lbl.configure(text="UAV")

        if hasattr(self, "badge"):
            self.badge.pack_forget()
    else:
        self.logo_lbl.configure(text="UAVEDUSIM")

        if hasattr(self, "badge") and not self.badge.winfo_ismapped():
            self.badge.pack(side="left", padx=(self.px(8), 0))


def _uavedusim_wrap_labels_deep(self, widget, wrap):
    try:
        children = widget.winfo_children()
    except Exception:
        return

    for child in children:
        try:
            if isinstance(child, tk.Label):
                text = str(child.cget("text") or "")
                if len(text) > 24:
                    child.configure(wraplength=max(self.px(180), wrap), justify="left", anchor="w")
        except Exception:
            pass

        self._wrap_labels_deep(child, wrap)


# Renderiza menus responsivos usados pelos monkey patches.
def _uavedusim_render_menu(self, prompt, options, on_select, *, badge=None, on_back=None, is_main=False):
    self.page = "menu"
    self.selected_callback = on_select
    self.back_callback = on_back
    self.clear_content()
    self.set_badge("MAIN MENU" if is_main else (badge or _uavedusim_translate(prompt).upper()))

    header = tk.Frame(self.content, bg=SURFACE)
    header.pack(fill="x", padx=self.CONTENT_PAD, pady=(self.HEADER_TOP_PAD, 0))

    didactic_subtitles = {
        "Escolha o modelo de drone para a simulação": (
            "Selecione o VANT que será usado no experimento. "
            "O modelo define a estrutura do drone e os sensores disponíveis na simulação."
        ),
        "Escolha o mundo de simulação onde o drone vai voar": (
            "Selecione o ambiente do Gazebo onde o voo será executado. "
            "O mundo define o cenário físico usado durante a missão simulada."
        ),
    }

    is_didactic_menu = prompt in didactic_subtitles

    if not is_main and on_back is not None:
        ttk.Button(header, text="󰁎", style="Back.TButton", command=on_back).pack(
            side="left",
            pady=(0, self.px(8)),
        )

        prompt_frame = tk.Frame(header, bg=SURFACE)
        prompt_frame.pack(side="left", fill="x", expand=True, padx=(self.px(12), 0))
    else:
        prompt_frame = header

    self.menu_prompt_lbl = tk.Label(
        prompt_frame,
        text=prompt,
        bg=SURFACE,
        fg=TEXT if is_didactic_menu else MUTED,
        font=(
            self.FONT_FAMILY,
            self.fs(14) if is_didactic_menu else self.FS_SMALL,
            "bold" if is_didactic_menu else "normal",
        ),
        justify="left",
        anchor="w",
        wraplength=max(self.px(260), self.content.winfo_width() - self.CONTENT_PAD * 2),
    )
    self.menu_prompt_lbl.pack(anchor="w", fill="x")

    if is_didactic_menu:
        tk.Label(
            prompt_frame,
            text=didactic_subtitles[prompt],
            bg=SURFACE,
            fg=MUTED,
            font=(self.FONT_FAMILY, self.FS_TINY),
            justify="left",
            anchor="w",
            wraplength=max(self.px(260), self.content.winfo_width() - self.CONTENT_PAD * 2),
        ).pack(anchor="w", fill="x", pady=(self.px(4), 0))

    list_frame = tk.Frame(self.content, bg=SURFACE2, highlightbackground=BORDER, highlightthickness=1)
    list_frame.pack(fill="both", expand=True, padx=self.CONTENT_PAD, pady=(self.LIST_TOP_PAD, 0))

    scrollbar = ModernScrollbar(
        list_frame,
        width=self.SCROLLBAR_W,
        bg=SURFACE2,
        thumb=BORDER,
        thumb_active=MUTED,
    )
    scrollbar.pack(side="right", fill="y", padx=(self.px(3), self.px(3)), pady=self.px(4))

    self.item_canvas = ItemCanvas(self, list_frame, yscrollcommand=scrollbar.set)
    self.item_canvas.set_options(options)
    self.item_canvas.pack(side="left", fill="both", expand=True)
    scrollbar.config(command=self.item_canvas.yview)

    self.menu_footer = tk.Frame(self.content, bg=SURFACE)
    self.menu_footer.pack(fill="x", padx=self.CONTENT_PAD, pady=(self.FOOTER_TOP_PAD, self.FOOTER_BOTTOM_PAD))
    self.menu_footer.grid_columnconfigure(0, weight=0)
    self.menu_footer.grid_columnconfigure(1, weight=1)
    self.menu_footer.grid_columnconfigure(2, weight=0)

    self.menu_count_lbl = tk.Label(
        self.menu_footer,
        text=_uavedusim_L(
            f"{len(options)} {'opção' if len(options) == 1 else 'opções'}",
            f"{len(options)} {'option' if len(options) == 1 else 'options'}",
        ),
        bg=SURFACE,
        fg=MUTED,
        font=(self.FONT_FAMILY, self.FS_TINY),
    )
    self.menu_count_lbl.grid(row=0, column=0, sticky="w")

    self.menu_hint_lbl = tk.Label(
        self.menu_footer,
        text="↑↓ navegar · Enter confirmar · Esc fechar",
        bg=SURFACE,
        fg=BORDER,
        font=(self.FONT_FAMILY, self.FS_TINY),
        anchor="w",
    )
    self.menu_hint_lbl.grid(row=0, column=1, sticky="w", padx=(self.px(12), 0))

    self.menu_btn_frame = tk.Frame(self.menu_footer, bg=SURFACE)
    self.menu_btn_frame.grid(row=0, column=2, sticky="e")

    _uavedusim_footer_close_button(
        self, self.menu_btn_frame,
        (self.close_app if is_main else (on_back or self.show_main_menu)),
    ).pack(side="left", padx=(0, self.px(8)))

    _uavedusim_footer_primary_button(
        self, self.menu_btn_frame, self.choose_current, "Selecionar 󰁕",
    ).pack(side="left")

    self.menu_footer.bind("<Configure>", self._reflow_menu_footer)
    self.root.after(80, self._reflow_menu_footer)
    self.item_canvas.focus_set()


def _uavedusim_reflow_menu_footer(self, event=None):
    if self.page != "menu" or not hasattr(self, "menu_footer"):
        return

    width = getattr(event, "width", None) or self.menu_footer.winfo_width() or self.content.winfo_width()

    if hasattr(self, "menu_prompt_lbl"):
        self.menu_prompt_lbl.configure(wraplength=max(self.px(220), width - self.CONTENT_PAD * 2))

    if width < self.px(620):
        self.menu_hint_lbl.grid_remove()
        self.menu_btn_frame.grid(row=1, column=0, columnspan=3, sticky="e", pady=(self.px(8), 0))
    else:
        self.menu_hint_lbl.grid(row=0, column=1, sticky="w", padx=(self.px(12), 0))
        self.menu_btn_frame.grid(row=0, column=2, sticky="e")


# Versão responsiva da tela de espera.
def _uavedusim_show_wait_page(self):
    self.page = "wait"
    self.selected_callback = None
    self.back_callback = self.show_main_menu
    self.clear_content()
    self.set_badge("AGUARDANDO PX4/GAZEBO")
    self.wait_start = time.monotonic()

    if not hasattr(self, "wait_log_offset"):
        try:
            self.wait_log_offset = LOG.stat().st_size
        except Exception:
            self.wait_log_offset = 0

    self.wait_timeout_reached = False

    header = tk.Frame(self.content, bg=SURFACE)
    header.pack(fill="x", padx=self.CONTENT_PAD, pady=(self.px(18), 0))

    self.wait_title_lbl = tk.Label(
        header,
        text="Aguardando PX4/Gazebo abrir...",
        bg=SURFACE,
        fg=TEXT,
        font=(self.FONT_FAMILY, self.fs(15), "bold"),
        justify="left",
        anchor="w",
    )
    self.wait_title_lbl.pack(anchor="w", fill="x")

    self.wait_subtitle_lbl = tk.Label(
        header,
        text="Aguarde enquanto PX4 e Gazebo iniciam. O menu de ataques abre automaticamente quando a simulação estiver pronta para decolar.",
        bg=SURFACE,
        fg=MUTED,
        font=(self.FONT_FAMILY, self.FS_SMALL),
        justify="left",
        anchor="w",
        wraplength=max(self.px(320), self.content.winfo_width() - self.CONTENT_PAD * 2),
    )
    self.wait_subtitle_lbl.pack(anchor="w", fill="x", pady=(self.px(7), 0))

    panel = tk.Frame(self.content, bg=SURFACE2, highlightbackground=BORDER, highlightthickness=1)
    panel.pack(fill="both", expand=True, padx=self.CONTENT_PAD, pady=(self.px(16), 0))
    panel.grid_columnconfigure(0, weight=1)
    panel.grid_rowconfigure(0, weight=1)

    center = tk.Frame(panel, bg=SURFACE2)
    center.grid(row=0, column=0, sticky="nsew", padx=self.px(28), pady=self.px(28))
    center.grid_columnconfigure(0, weight=1)

    tk.Label(
        center,
        text="Inicializando simulação",
        bg=SURFACE2,
        fg=TAG_FG,
        font=(self.FONT_FAMILY, self.FS_SMALL, "bold"),
    ).grid(row=0, column=0, sticky="w")

    self.progress = ttk.Progressbar(center, mode="indeterminate", style="UAVEDUSIM.Horizontal.TProgressbar")
    self.progress.grid(row=1, column=0, sticky="ew", pady=(self.px(12), self.px(10)))
    self.progress.start(12)

    self.status_lbl = tk.Label(
        center,
        text="Preparando simulação...\nPX4: iniciando\nGazebo: abrindo",
        bg=SURFACE2,
        fg=WARN,
        font=(self.FONT_FAMILY, self.fs(9.5), "bold"),
        justify="left",
        anchor="w",
        wraplength=self.px(520),
    )
    self.status_lbl.grid(row=2, column=0, sticky="ew")

    self.wait_log_lbl = tk.Label(
        center,
        text=f"Log: {LOG}",
        bg=SURFACE2,
        fg=MUTED,
        font=(self.FONT_FAMILY, self.FS_TINY),
        justify="left",
        anchor="w",
        wraplength=self.px(520),
    )
    self.wait_log_lbl.grid(row=3, column=0, sticky="ew", pady=(self.px(6), 0))

    center.bind("<Configure>", self._reflow_wait_status)

    self.wait_footer = tk.Frame(self.content, bg=SURFACE)
    self.wait_footer.pack(fill="x", padx=self.CONTENT_PAD, pady=(self.px(12), self.FOOTER_BOTTOM_PAD))
    self.wait_footer.grid_columnconfigure(0, weight=1)
    self.wait_footer.grid_columnconfigure(1, weight=0)

    self.wait_hint_lbl = tk.Label(
        self.wait_footer,
        text="",
        bg=SURFACE,
        fg=BORDER,
        font=(self.FONT_FAMILY, self.FS_TINY),
        justify="left",
        anchor="w",
        wraplength=self.px(520),
    )
    self.wait_hint_lbl.grid(row=0, column=0, sticky="ew")

    self.wait_btn_frame = tk.Frame(self.wait_footer, bg=SURFACE)
    self.wait_btn_frame.grid(row=0, column=1, sticky="e")

    self.wait_cancel_button = RoundedButton(
        self.wait_btn_frame,
        text="Cancelar",
        command=self.show_main_menu,
        outer_bg=SURFACE,
        fill=DANGER,
        fill_hover=_uavedusim_blend(DANGER, "#ffffff", 0.18),
        fill_press=_uavedusim_blend(DANGER, "#000000", 0.18),
        text_color="#11111b",
        text_color_hover="#11111b",
        radius=self.px(11),
        height=self.px(37),
        width=self.px(130),
        font=(self.FONT_FAMILY, self.fs(11), "bold"),
    )
    self.wait_cancel_button.pack(side="left", padx=(0, self.px(8)))

    self.wait_open_attacks_button = _uavedusim_footer_primary_button(
        self, self.wait_btn_frame, self.show_attack_menu, "Abrir ataques",
        width=self.px(160),
    )
    self.wait_open_attacks_button.pack(side="left")

    self.wait_footer.bind("<Configure>", self._reflow_wait_footer)
    self.root.after(80, self._reflow_wait_status)
    self.root.after(80, self._reflow_wait_footer)
    self.root.after(800, self.check_px4_ready)


def _uavedusim_reflow_wait_status(self, event=None):
    if self.page != "wait":
        return

    width = getattr(event, "width", None) if event is not None else None

    if not width:
        try:
            width = self.content.winfo_width()
        except Exception:
            width = self.root.winfo_width()

    wrap = max(self.px(240), width - self.CONTENT_PAD * 2 - self.px(24))

    for attr in ("wait_title_lbl", "wait_subtitle_lbl", "status_lbl", "wait_log_lbl", "wait_hint_lbl"):
        if hasattr(self, attr):
            getattr(self, attr).configure(wraplength=wrap)


def _uavedusim_reflow_wait_footer(self, event=None):
    if self.page != "wait" or not hasattr(self, "wait_footer"):
        return

    width = getattr(event, "width", None) or self.wait_footer.winfo_width() or self.content.winfo_width()

    if width < self.px(760):
        self.wait_hint_lbl.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, self.px(8)))
        self.wait_btn_frame.grid(row=1, column=0, columnspan=2, sticky="e")
    else:
        self.wait_hint_lbl.grid(row=0, column=0, columnspan=1, sticky="ew", pady=0)
        self.wait_btn_frame.grid(row=0, column=1, columnspan=1, sticky="e")



def _uavedusim_reflow_attack_layout(self, event=None):
    if self.page != "attack" or not hasattr(self, "attack_body"):
        return

    width = getattr(event, "width", None) or self.attack_body.winfo_width()

    if width <= 1:
        width = max(self.root.winfo_width() - self.CONTENT_PAD * 2, self.px(420))

    gap = self.px(14)
    stack = width < self.px(780)

    # REMOVIDOS os grid_forget() que causavam o piscar da tela inteira!

    self.attack_body.grid_columnconfigure(0, weight=1, minsize=0)
    self.attack_body.grid_columnconfigure(1, weight=0, minsize=0)
    self.attack_body.grid_rowconfigure(0, weight=1)
    self.attack_body.grid_rowconfigure(1, weight=0)

    if stack:
        self.attack_left.grid(row=0, column=0, sticky="nsew", padx=0, pady=0)
        self.attack_right.grid(row=1, column=0, sticky="ew", padx=0, pady=(gap, 0))
        self.attack_left.configure(width=max(1, width))
        self.attack_right.configure(width=max(1, width))
        self.attack_right.grid_propagate(True)
        left_wrap = max(self.px(220), width - self.px(56))
        right_wrap = max(self.px(220), width - self.px(56))
    else:
        right_w = int(width * 0.38)
        right_w = max(self.px(300), min(right_w, self.px(520)))
        left_w = max(self.px(340), width - gap - right_w)

        self.attack_body.grid_columnconfigure(0, weight=3, minsize=left_w)
        self.attack_body.grid_columnconfigure(1, weight=2, minsize=right_w)
        
        self.attack_left.grid(row=0, column=0, sticky="nsew", padx=(0, gap), pady=0)
        self.attack_right.grid(row=0, column=1, sticky="nsew", padx=0, pady=0)
        self.attack_left.configure(width=left_w)
        self.attack_right.configure(width=right_w)
        self.attack_right.grid_propagate(False)
        left_wrap = max(self.px(240), left_w - self.px(56))
        right_wrap = max(self.px(220), right_w - self.px(56))

    self._wrap_labels_deep(self.attack_body, max(self.px(220), width - self.px(40)))

    if hasattr(self, "attack_desc_label"):
        self.attack_desc_label.configure(wraplength=left_wrap)

    if hasattr(self, "mitigation_desc_label"):
        self.mitigation_desc_label.configure(wraplength=right_wrap)

    if hasattr(self, "attack_scroll_canvas"):
        self.root.after(40, lambda: self.attack_scroll_canvas.configure(scrollregion=self.attack_scroll_canvas.bbox("all")))



# Versão responsiva das linhas de ataque.
def _uavedusim_make_attack_row(self, parent, attack_key, label, entry_var=None, placeholder=None):
    outer = tk.Frame(parent, bg=SURFACE2)
    outer.pack(fill="x", pady=(self.px(9), 0))

    row = tk.Frame(
        outer,
        bg=SURFACE2,
        highlightbackground=TEXT,
        highlightthickness=1,
        cursor="hand2",
    )
    row.pack(fill="x")
    row.grid_columnconfigure(0, weight=1)
    row.grid_columnconfigure(1, weight=0)
    row.grid_columnconfigure(2, weight=0)
    row.bind("<Button-1>", lambda _e, key=attack_key: self.select_attack_type(key))

    lbl = tk.Label(
        row,
        text=label,
        bg=SURFACE2,
        fg=TEXT,
        font=(self.FONT_FAMILY, self.fs(12), "bold"),
        cursor="hand2",
        anchor="w",
        justify="left",
    )
    lbl.grid(row=0, column=0, sticky="ew", padx=(self.px(14), self.px(6)), pady=self.px(12))
    lbl.bind("<Button-1>", lambda _e, key=attack_key: self.select_attack_type(key))

    # Ícone de informação (canto superior direito do card do ataque).
    info_icon = _uavedusim_make_info_icon(self, row, attack_key)
    info_icon.grid(
        row=0,
        column=(2 if entry_var is not None else 1),
        sticky="ne",
        padx=(self.px(4), self.px(10)),
        pady=self.px(8),
    )

    entry = None
    entry_wrap = None

    if entry_var is not None:
        entry_wrap = tk.Frame(row, bg=SURFACE2)
        entry_wrap.grid(row=0, column=1, sticky="e", padx=(self.px(6), self.px(12)), pady=self.px(6))

        tk.Label(
            entry_wrap,
            text="Valor",
            bg=SURFACE2,
            fg=MUTED,
            font=(self.FONT_FAMILY, self.FS_TINY),
        ).pack(anchor="w")

        entry = tk.Entry(
            entry_wrap,
            textvariable=entry_var,
            relief="flat",
            bd=0,
            justify="center",
            bg="#11111b",
            fg=TEXT,
            insertbackground=TEXT,
            width=7,
            font=(self.FONT_FAMILY, self.fs(10), "bold"),
        )
        entry.pack(fill="x", ipady=self.px(2))

        if placeholder and not entry_var.get():
            entry_var.set(placeholder)

        entry.bind("<FocusIn>", lambda _e, key=attack_key: self.select_attack_type(key))
        entry.bind("<Button-1>", lambda _e, key=attack_key: self.select_attack_type(key))

    self.attack_rows[attack_key] = {
        "frame": row,
        "label": lbl,
        "entry": entry,
        "entry_wrap": entry_wrap,
        "var": entry_var,
        "info_icon": info_icon,
    }


def _uavedusim_draw_mitigation_slider(self):
    if not hasattr(self, "mitigation_slider"):
        return

    on = self.mitigation_state == "on"
    c = self.mitigation_slider

    w = self.px(76)
    h = self.px(30)
    pad = self.px(3)
    radius = h // 2

    c.configure(width=w, height=h, bg=SURFACE2)
    c.delete("all")

    track = "#35433b" if on else "#585b70"
    knob = "#cdd6f4"
    outline = "#6c7086"

    c.create_rectangle(radius, pad, w - radius, h - pad, fill=track, outline=track)
    c.create_oval(pad, pad, pad + 2 * radius - pad, h - pad, fill=track, outline=track)
    c.create_oval(w - 2 * radius + pad, pad, w - pad, h - pad, fill=track, outline=track)

    knob_size = h - self.px(8)
    knob_y0 = (h - knob_size) // 2
    knob_y1 = knob_y0 + knob_size

    if on:
        knob_x1 = w - self.px(5)
        knob_x0 = knob_x1 - knob_size
    else:
        knob_x0 = self.px(5)
        knob_x1 = knob_x0 + knob_size

    c.create_oval(knob_x0, knob_y0, knob_x1, knob_y1, fill=knob, outline=outline)


def _uavedusim_item_on_wheel(self, event):
    if getattr(event, "num", None) == 4:
        _uavedusim_safe_yview_scroll(self, -3, "units")
    elif getattr(event, "num", None) == 5:
        _uavedusim_safe_yview_scroll(self, 3, "units")
    elif getattr(event, "delta", 0):
        step = -1 if event.delta > 0 else 1
        _uavedusim_safe_yview_scroll(self, step * 3, "units")

    return "break"


_original_item_canvas_init = ItemCanvas.__init__

def _uavedusim_item_canvas_init(self, *args, **kwargs):
    _original_item_canvas_init(self, *args, **kwargs)
    self.bind("<MouseWheel>", self._on_wheel)
    self.bind("<Button-4>", self._on_wheel)
    self.bind("<Button-5>", self._on_wheel)
    self.bind("<Enter>", lambda _e: self.focus_set())


_original_app_init = UAVEDUSIMApp.__init__

def _uavedusim_app_init(self, *args, **kwargs):
    self._reflow_after_id = None
    _original_app_init(self, *args, **kwargs)
    self._reflow_after_id = None
    self.root.bind("<Configure>", self._schedule_global_reflow, add="+")
    self.root.after(100, self._global_reflow)


UAVEDUSIMApp._setup_dimensions = _uavedusim_responsive_setup_dimensions
UAVEDUSIMApp._setup_styles = _uavedusim_responsive_setup_styles
UAVEDUSIMApp._schedule_global_reflow = _uavedusim_schedule_global_reflow
UAVEDUSIMApp._global_reflow = _uavedusim_global_reflow
UAVEDUSIMApp._reflow_titlebar = _uavedusim_reflow_titlebar
UAVEDUSIMApp._wrap_labels_deep = _uavedusim_wrap_labels_deep
UAVEDUSIMApp.render_menu = _uavedusim_render_menu
UAVEDUSIMApp._reflow_menu_footer = _uavedusim_reflow_menu_footer
UAVEDUSIMApp.show_wait_page = _uavedusim_show_wait_page
UAVEDUSIMApp._reflow_wait_status = _uavedusim_reflow_wait_status
UAVEDUSIMApp._reflow_wait_footer = _uavedusim_reflow_wait_footer
UAVEDUSIMApp._reflow_attack_layout = _uavedusim_reflow_attack_layout
UAVEDUSIMApp._make_attack_row = _uavedusim_make_attack_row
UAVEDUSIMApp._draw_mitigation_slider = _uavedusim_draw_mitigation_slider
UAVEDUSIMApp.__init__ = _uavedusim_app_init

ItemCanvas._on_wheel = _uavedusim_item_on_wheel
ItemCanvas.__init__ = _uavedusim_item_canvas_init


# Layout responsivo dos campos de destino.

def _uavedusim_pack_button_column(self, frame, *, vertical=True, fill_x=True):
    children = list(frame.winfo_children())

    # Evita repetir o pack_forget()+pack() quando o layout não mudou.
    # Um <Configure> "espúrio" (por exemplo, disparado pelo gerenciador de
    # janelas ao focar/desfocar a janela quando o mouse entra/sai) chamava
    # essa função com os MESMOS parâmetros, escondendo e recolocando os
    # botões sem necessidade — o que fazia a tela "piscar".
    cache_key = (id(frame), vertical, fill_x, tuple(children))
    if getattr(self, "_pack_button_column_cache", None) == cache_key:
        return
    self._pack_button_column_cache = cache_key

    for child in children:
        try:
            child.pack_forget()
        except Exception:
            pass

    # Agora empacotamos TODOS os itens (incluindo o timer), sem esconder nada
    for idx, child in enumerate(children):
        last = idx == len(children) - 1

        if vertical:
            child.pack(
                side="top",
                fill=("x" if fill_x else "none"),
                pady=(0, self.px(7) if not last else 0),
            )
        else:
            child.pack(
                side="left",
                padx=(0, self.px(8) if not last else 0),
            )


def _uavedusim_config_prereq_button_texts(self, *, compact=False, very_compact=False):
    takeoff_done = bool(getattr(self, "takeoff_sent", False))
    destination_done = bool(getattr(self, "destination_sent", False))

    takeoff_text = "Takeoff enviado" if takeoff_done else "Enviar commander takeoff"
    destination_text = "Reenviar destino" if destination_done else "Enviar destino"

    # Mantem o nome completo dos botões em telas estreitas.
    if compact and not takeoff_done:
        takeoff_text = "Enviar commander\ntakeoff"

    if very_compact and not destination_done:
        destination_text = "Enviar\ndestino"

    if very_compact and destination_done:
        destination_text = "Reenviar\ndestino"

    for attr, text in (
        ("takeoff_button", takeoff_text),
        ("destination_button", destination_text),
    ):
        if not hasattr(self, attr):
            continue

        btn = getattr(self, attr)

        # Define a largura travada em CARACTERES, baseando-se no texto original mais longo
        if attr == "takeoff_button":
            char_width = 16 if compact else 24
        else:
            char_width = 10 if very_compact else 16

        try:
            btn.configure(
                text=text,
                justify="center",
                wraplength=max(self.px(120), self.px(190 if not compact else 140)),
                width=char_width,  # <-- Agora medido em letras, sem estourar a tela
                padx=self.px(12),
                pady=self.px(7 if "\n" in text else 6),
            )
        except Exception:
            pass


def _uavedusim_layout_destination_fields(self, *, stack=False):
    if not hasattr(self, "destination_entries"):
        return

    entries = list(getattr(self, "destination_entries", []))
    if not entries:
        return

    fields = []

    for entry in entries:
        try:
            fields.append(entry.master)
        except Exception:
            pass

    if not fields:
        return

    destination_grid = fields[0].master

    for child in fields:
        try:
            child.grid_forget()
        except Exception:
            pass

    for col in range(3):
        try:
            destination_grid.grid_columnconfigure(col, weight=0, minsize=0)
        except Exception:
            pass

    # Campos compactos para distância, rotação e altitude.
    widths = [10, 9, 8]

    for idx, entry in enumerate(entries):
        try:
            entry.configure(width=widths[idx] if idx < len(widths) else 8)
        except Exception:
            pass

    if stack:
        for idx, field in enumerate(fields):
            field.grid(row=idx, column=0, sticky="w", padx=0, pady=(0, self.px(6)))

        try:
            destination_grid.grid_columnconfigure(0, weight=1)
        except Exception:
            pass

        return

    for idx, field in enumerate(fields):
        field.grid(row=0, column=idx, sticky="w", padx=(0, self.px(10)), pady=0)



def _uavedusim_reflow_attack_footer(self, event=None):
    if self.page != "attack" or not hasattr(self, "attack_btn_frame"):
        return

    width = getattr(event, "width", None) or self.content.winfo_width() or self.root.winfo_width()

    compact = width < self.px(980)
    vertical_buttons = width < self.px(560)

    # Quebra linhas dos botões sem abreviar os textos.
    if hasattr(self, "restart_button"):
        self.restart_button.set_text(
            "Reiniciar\nsimulação" if vertical_buttons else "Reiniciar simulação",
            height=self.px(50) if vertical_buttons else self.px(37),
        )

    if hasattr(self, "stop_button"):
        self.stop_button.set_text(
            "Desligar\njamming" if vertical_buttons else "Desligar jamming",
            height=self.px(50) if vertical_buttons else self.px(37),
        )

    if compact:
        self.attack_hint_label.grid(
            row=0,
            column=0,
            columnspan=2,
            sticky="ew",
            padx=0,
            pady=(0, self.px(8)),
        )

        self.attack_btn_frame.grid(
            row=1,
            column=0,
            columnspan=2,
            sticky=("ew" if vertical_buttons else "e"),
        )
    else:
        self.attack_hint_label.grid(
            row=0,
            column=0,
            columnspan=1,
            sticky="ew",
            padx=(0, self.px(10)),
            pady=0,
        )

        self.attack_btn_frame.grid(
            row=0,
            column=1,
            columnspan=1,
            sticky="e",
        )

    self._pack_button_column(
        self.attack_btn_frame,
        vertical=vertical_buttons,
        fill_x=True,
    )

    if hasattr(self, "attack_scroll_canvas"):
        self.root.after(
            40,
            lambda: self.attack_scroll_canvas.configure(
                scrollregion=self.attack_scroll_canvas.bbox("all")
            ),
        )


UAVEDUSIMApp._pack_button_column = _uavedusim_pack_button_column
UAVEDUSIMApp._config_prereq_button_texts = _uavedusim_config_prereq_button_texts
UAVEDUSIMApp._layout_destination_fields = _uavedusim_layout_destination_fields
UAVEDUSIMApp._reflow_attack_footer = _uavedusim_reflow_attack_footer


# Descrições didáticas dos modelos Gazebo.

PX4_MODEL_DOC_ORDER = [
    "gz_x500_uavjamsim",
    "gz_x500",
    "gz_x500_vision",
    "gz_x500_depth",
    "gz_x500_mono_cam",
    "gz_x500_mono_cam_down",
    "gz_x500_lidar_down",
    "gz_x500_lidar_front",
    "gz_x500_lidar_2d",
    "gz_x500_gimbal",
    "gz_rc_cessna",
    "gz_advanced_plane",
    "gz_standard_vtol",
    "gz_quadtailsitter",
    "gz_tiltrotor",
]

PX4_MODEL_DESCRIPTIONS = {
    "gz_x500_uavjamsim": "Versão do X500 com vários sensores ativos ao mesmo tempo para experimentos do UAVEDUSIM.",
    "gz_x500": "Quadricóptero X500 básico.",
    "gz_x500_vision": "Quadricóptero X500 com odometria visual.",
    "gz_x500_depth": "Quadricóptero X500 com câmera de profundidade frontal baseada na OAK-D.",
    "gz_x500_mono_cam": "Quadricóptero X500 com câmera monocular simples.",
    "gz_x500_mono_cam_down": "Quadricóptero X500 com câmera monocular voltada para baixo; útil para testes de pouso preciso no mundo Aruco.",
    "gz_x500_lidar_down": "Quadricóptero X500 com LiDAR 1D inferior Lightware LW20/C, alcance de 0,1 a 100 m.",
    "gz_x500_lidar_front": "Quadricóptero X500 com LiDAR 1D frontal Lightware LW20/C, alcance de 0,2 a 100 m.",
    "gz_x500_lidar_2d": "Quadricóptero X500 com LiDAR 2D Hokuyo UTM-30LX, alcance de 0,1 a 30 m e varredura de 270 graus.",
    "gz_x500_gimbal": "Quadricóptero X500 com gimbal frontal e controle de posição.",
    "gz_rc_cessna": "Avião Cessna RC padrão no Gazebo.",
    "gz_advanced_plane": "Avião de asa fixa avançado com plugin Advanced Lift Drag.",
    "gz_standard_vtol": "VTOL padrão com decolagem vertical e voo de asa fixa.",
    "gz_quadtailsitter": "VTOL quad tailsitter com empuxo diferencial para arfagem, rolagem e guinada.",
    "gz_tiltrotor": "VTOL tiltrotor com motores frontais inclináveis para transição e voo avançado.",
}

PX4_ROVER_MODEL_PREFIXES = (
    "gz_rover",
    "gz_r1_rover",
)


def _uavedusim_is_rover_model(model_id):
    low = str(model_id).lower()
    if any(low.startswith(prefix) for prefix in PX4_ROVER_MODEL_PREFIXES):
        return True
    return "rover" in low


def _uavedusim_model_id_from_choice(choice):
    text = str(choice or "").strip()
    if not text:
        return ""
    first_line = text.splitlines()[0].strip()
    return first_line.split()[0] if first_line else ""


def _uavedusim_model_description(model_id):
    if model_id in PX4_MODEL_DESCRIPTIONS:
        return _uavedusim_translate(PX4_MODEL_DESCRIPTIONS[model_id])

    raw = model_id[3:] if model_id.startswith("gz_") else model_id
    pretty = raw.replace("_", " ")
    return _uavedusim_L(f"Modelo {pretty} no Gazebo.", f"{pretty} model in Gazebo.")


PX4_MODEL_RECOMMENDATION_LABELS = {
    "gz_x500_uavjamsim": "modelo indicado",
    "gz_x500_vision": "segundo modelo mais indicado",
}


def _uavedusim_model_display_label(model_id):
    description = _uavedusim_model_description(model_id)
    recommendation = PX4_MODEL_RECOMMENDATION_LABELS.get(model_id, "")
    suffix = f" ({recommendation})" if recommendation else ""
    return f"{model_id}{suffix}\n{description}"

def _uavedusim_model_exists_and_is_executable(model_id):
    if not model_id.startswith("gz_"):
        return False

    models_dir = PX4_DIR / "Tools/simulation/gz/models"
    model_name = model_id[3:]
    model_path = models_dir / model_name

    if not (model_path.exists() or model_path.is_symlink()):
        return False

    if os.environ.get("UAVEDUSIM_SHOW_ALL_GZ_MODELS", "0") == "1":
        return True

    airframes_dir = PX4_DIR / "ROMFS/px4fmu_common/init.d-posix/airframes"
    airframes = list(airframes_dir.glob(f"*_{model_id}"))

    if airframes:
        return True

    log(f"Ignorando modelo sem airframe/target PX4: {model_name}")
    return False


# Lista modelos com descrição didática e remove rovers por padrão.
def _uavedusim_get_models_with_descriptions(self):
    models_dir = PX4_DIR / "Tools/simulation/gz/models"

    if not models_dir.is_dir():
        raise RuntimeError(_uavedusim_L(f"Não encontrei a pasta de modelos do Gazebo: {models_dir}", f"Couldn't find the Gazebo models folder: {models_dir}"))

    discovered = []

    for item in sorted(models_dir.iterdir(), key=lambda p: p.name):
        if item.name.startswith("."):
            continue

        if not (item.is_dir() or item.is_symlink()):
            continue

        model_id = f"gz_{item.name}"

        if _uavedusim_is_rover_model(model_id):
            log(f"Ignorando modelo rover na lista de veículos: {item.name}")
            continue

        if not _uavedusim_model_exists_and_is_executable(model_id):
            continue

        discovered.append(model_id)

    ordered = []

    for model_id in PX4_MODEL_DOC_ORDER:
        if model_id in discovered and model_id not in ordered:
            ordered.append(model_id)

    # Restrito à lista curada (X500, VTOL e planes) — modelos extras
    # descobertos no disco que não estejam nela (ex.: rovers ou algo
    # fora do catálogo esperado) não entram na lista exibida.

    if not ordered:
        raise RuntimeError(f"Nenhum modelo executável foi encontrado em: {models_dir}")

    return [_uavedusim_model_display_label(model_id) for model_id in ordered]


# Extrai o token real do modelo selecionado.
def _uavedusim_handle_model_choice_with_description(self, choice):
    self.model = _uavedusim_model_id_from_choice(choice)

    if not self.model:
        self.show_error("Modelo invalido selecionado.")
        return

    _uavedusim_show_world_selection_screen(self)


# BEGIN UAVEDUSIM_MODEL_SELECTION_SCREEN_PATCH
# Tela de escolha de modelo de drone: os dois modelos recomendados
# (uavjamsim e vision) ficam sempre visíveis, lado a lado; o resto dos
# modelos fica escondido atrás de um botão "Ver todos os N modelos".

def _uavedusim_model_card_metrics(app, w, s, model_id, desc, warning_text=None):
    pad = max(app.px(14), int(16 * s))
    badge_font = _uj_font(app, 9.0 * s, "bold")
    name_font = _uj_font(app, 13.0 * s, "bold")
    desc_font = _uj_font(app, 10.2 * s)
    warn_font = _uj_font(app, 9.4 * s)

    text_w = max(app.px(100), w - pad * 2)

    badge_h = _uj_linespace(app, badge_font)
    name_h = _uj_measure_text_height(app, model_id, name_font, text_w)
    desc_h = _uj_measure_text_height(app, desc, desc_font, text_w)

    gap_badge_name = max(app.px(6), int(7 * s))
    gap_name_desc = max(app.px(6), int(6 * s))

    h = pad + badge_h + gap_badge_name + name_h + gap_name_desc + desc_h

    warn_h = 0
    warn_icon_w = max(app.px(20), int(22 * s))
    warn_text_w = max(app.px(80), text_w - warn_icon_w)

    if warning_text:
        warn_pad = max(app.px(8), int(9 * s))
        warn_body_h = _uj_measure_text_height(app, warning_text, warn_font, warn_text_w)
        warn_h = warn_pad * 2 + warn_body_h
        h += max(app.px(10), int(11 * s)) + warn_h

    h += pad

    return {
        "pad": pad, "badge_font": badge_font, "name_font": name_font,
        "desc_font": desc_font, "warn_font": warn_font, "text_w": text_w,
        "warn_text_w": warn_text_w, "warn_icon_w": warn_icon_w,
        "badge_h": badge_h, "name_h": name_h, "desc_h": desc_h,
        "gap_badge_name": gap_badge_name, "gap_name_desc": gap_name_desc,
        "warn_h": warn_h, "height": max(app.px(96), h),
    }


def _uavedusim_draw_model_card(canvas, app, x, y, w, h, s, model_id, desc,
                                badge_text, badge_color, name_color, border_color,
                                selected, warning_text=None, tag=None):
    fill = _uavedusim_blend(BG, SURFACE, 0.22)
    border = ACCENT if selected else border_color
    border_w = 2 if selected else 1

    _uj_rr(canvas, x, y, x + w, y + h, app.px(10), fill=fill, outline=border, width=border_w, tags=tag)

    m = _uavedusim_model_card_metrics(app, w, s, model_id, desc, warning_text)
    pad = m["pad"]
    cx = x + pad
    cy = y + pad

    _uj_text(
        canvas, cx, cy, badge_text, m["badge_font"], badge_color,
        width=m["text_w"], tags=tag,
    )
    cy += m["badge_h"] + m["gap_badge_name"]

    _uj_text(canvas, cx, cy, model_id, m["name_font"], name_color, width=m["text_w"], tags=tag)
    cy += m["name_h"] + m["gap_name_desc"]

    _uj_text(canvas, cx, cy, desc, m["desc_font"], TEXT, width=m["text_w"], tags=tag)
    cy += m["desc_h"]

    if warning_text:
        cy += max(app.px(10), int(11 * s))
        warn_bg = _uavedusim_blend(BG, DANGER, 0.16)
        warn_border = _uavedusim_blend(BG, DANGER, 0.40)
        warn_pad = max(app.px(8), int(9 * s))
        warn_box_h = m["warn_h"]

        _uj_rr(
            canvas, cx, cy, x + w - pad, cy + warn_box_h, app.px(7),
            fill=warn_bg, outline=warn_border, width=1, tags=tag,
        )

        _uj_text(
            canvas, cx + warn_pad, cy + warn_pad, "⚠",
            _uj_font(app, 10.5 * s), DANGER, tags=tag,
        )

        _uj_text(
            canvas, cx + m["warn_icon_w"], cy + warn_pad, warning_text,
            m["warn_font"], DANGER, width=m["warn_text_w"], tags=tag,
        )


def _uavedusim_draw_model_row(canvas, app, x, y, w, s, index, model_id, desc, selected, tag=None):
    pad = max(app.px(12), int(13 * s))
    name_font = _uj_font(app, 11.0 * s, "bold")
    desc_font = _uj_font(app, 9.6 * s)
    num_font = _uj_font(app, 9.0 * s)

    num_w = max(app.px(34), int(36 * s))
    text_x = x + num_w
    text_w = max(app.px(100), w - num_w - pad)

    name_h = _uj_measure_text_height(app, model_id, name_font, text_w)
    desc_h = _uj_measure_text_height(app, desc, desc_font, text_w)
    gap = max(app.px(4), int(4 * s))

    h = pad + name_h + gap + desc_h + pad

    fill = _uavedusim_blend(BG, SURFACE, 0.30) if not selected else _uavedusim_blend(BG, ACCENT, 0.14)
    border = ACCENT if selected else BORDER

    _uj_rr(canvas, x, y, x + w, y + h, app.px(8), fill=fill, outline=border, width=(2 if selected else 1), tags=tag)

    _uj_text(
        canvas, x + max(app.px(10), 11 * s), y + pad,
        f"#{index:02d}", num_font, MUTED, tags=tag,
    )

    ty = y + pad
    _uj_text(canvas, text_x, ty, model_id, name_font, (ACCENT if selected else TEXT), width=text_w, tags=tag)
    ty += name_h + gap
    _uj_text(canvas, text_x, ty, desc, desc_font, MUTED, width=text_w, tags=tag)

    return h


def _uavedusim_show_model_selection_screen(self):
    self.page = "model_select"
    self.back_callback = self.show_main_menu
    self.selected_callback = None
    self.clear_content()
    self.set_badge("ESCOLHER DRONE")

    if not PX4_DIR.is_dir():
        self.show_error(_uavedusim_L(f"Não encontrei a pasta: {PX4_DIR}", f"Couldn't find the folder: {PX4_DIR}"))
        return

    try:
        self.ensure_uavjamsim_links(False)
        formatted = self.get_models()
    except Exception as e:
        self.show_error(str(e))
        return

    model_ids = [_uavedusim_model_id_from_choice(f) for f in formatted]

    if not model_ids:
        self.show_error("Nenhum modelo de drone foi encontrado.")
        return

    primary_id = "gz_x500_uavjamsim" if "gz_x500_uavjamsim" in model_ids else None
    secondary_id = "gz_x500_vision" if "gz_x500_vision" in model_ids else None
    other_ids = [m for m in model_ids if m not in (primary_id, secondary_id)]

    if not hasattr(self, "_model_selected_id") or self._model_selected_id not in model_ids:
        self._model_selected_id = primary_id or secondary_id or model_ids[0]

    if not hasattr(self, "_model_list_expanded"):
        self._model_list_expanded = False

    footer = tk.Frame(self.content, bg=SURFACE)
    footer.pack(
        side="bottom",
        fill="x",
        padx=self.CONTENT_PAD,
        pady=(self.px(10), self.FOOTER_BOTTOM_PAD),
    )

    outer = tk.Frame(self.content, bg=SURFACE)
    outer.pack(
        fill="both",
        expand=True,
        padx=self.CONTENT_PAD,
        pady=(self.HEADER_TOP_PAD, 0),
    )

    canvas, scrollbar = _uavedusim_make_scrollable_canvas(self, outer)

    self._model_screen_scroll_enabled = False

    warning_text = "Pode ser pesado computacionalmente. Se travar, use o vision ao lado."

    def draw(_event=None):
        canvas.delete("all")

        width = max(canvas.winfo_width(), 1)
        visible_h = max(canvas.winfo_height(), 1)

        s = _uj_clamp(width / self.px(940), .82, 1.04)

        x = max(self.px(4), int(width * .008))
        w = max(self.px(420), width - 2 * x)
        y = self.px(4)

        title = "Escolha o modelo de drone para a simulação"
        title_font = _uj_font(self, 17.5 * s, "bold")
        _uj_text(canvas, x, y, title, title_font, TEXT, width=w)
        y += _uj_measure_text_height(self, title, title_font, w) + max(self.px(5), int(5 * s))

        subtitle = (
            "Selecione o VANT que será usado no experimento. O modelo define a estrutura "
            "do drone e os sensores disponíveis na simulação."
        )
        subtitle_font = _uj_font(self, 11.2 * s)
        _uj_text(canvas, x, y, subtitle, subtitle_font, MUTED, width=w)
        y += _uj_measure_text_height(self, subtitle, subtitle_font, w) + max(self.px(16), int(18 * s))

        gap = max(self.px(10), int(11 * s))
        wide = w >= max(self.px(560), int(560 * s))

        if primary_id or secondary_id:
            if wide and primary_id and secondary_id:
                col_w = (w - gap) / 2
                primary_desc = _uavedusim_model_description(primary_id)
                secondary_desc = _uavedusim_model_description(secondary_id)

                h1 = _uavedusim_model_card_metrics(self, col_w, s, primary_id, primary_desc, warning_text)["height"]
                h2 = _uavedusim_model_card_metrics(self, col_w, s, secondary_id, secondary_desc)["height"]
                card_h = max(h1, h2)

                _uavedusim_draw_model_card(
                    canvas, self, x, y, col_w, card_h, s, primary_id, primary_desc,
                    "MODELO INDICADO", "#a6e3a1", "#a6e3a1", "#a6e3a1",
                    self._model_selected_id == primary_id, warning_text,
                    tag="model_card_primary",
                )
                _uavedusim_draw_model_card(
                    canvas, self, x + col_w + gap, y, col_w, card_h, s, secondary_id, secondary_desc,
                    "ALTERNATIVA MAIS LEVE", MUTED, TEXT, BORDER,
                    self._model_selected_id == secondary_id, None,
                    tag="model_card_secondary",
                )
                y += card_h + gap
            else:
                for mid, badge, badge_color, name_color, border_color, warn in (
                    (primary_id, "MODELO INDICADO", "#a6e3a1", "#a6e3a1", "#a6e3a1", warning_text),
                    (secondary_id, "ALTERNATIVA MAIS LEVE", MUTED, TEXT, BORDER, None),
                ):
                    if not mid:
                        continue
                    desc = _uavedusim_model_description(mid)
                    ch = _uavedusim_model_card_metrics(self, w, s, mid, desc, warn)["height"]
                    tag = "model_card_primary" if mid == primary_id else "model_card_secondary"
                    _uavedusim_draw_model_card(
                        canvas, self, x, y, w, ch, s, mid, desc,
                        badge, badge_color, name_color, border_color,
                        self._model_selected_id == mid, warn, tag=tag,
                    )
                    y += ch + gap

        if other_ids:
            toggle_h = max(self.px(38), int(40 * s))
            toggle_fill = _uavedusim_blend(BG, SURFACE, 0.22)

            pts = _uavedusim_round_rect_points(x, y, x + w, y + toggle_h, self.px(8))
            canvas.create_polygon(
                pts, fill=toggle_fill, outline=BORDER, width=1,
                smooth=True, splinesteps=16, dash=(4, 3), tags="model_toggle",
            )

            chevron = "⌃" if self._model_list_expanded else "⌄"
            label = (
                _uavedusim_L(f"Ocultar os outros {len(other_ids)} modelos", f"Hide the other {len(other_ids)} models")
                if self._model_list_expanded
                else _uavedusim_L(f"Ver mais {len(other_ids)} modelos", f"See {len(other_ids)} more models")
            )

            canvas.create_text(
                x + w / 2, y + toggle_h / 2,
                text=f"{label}  {chevron}",
                font=_uj_font(self, 10.6 * s),
                fill=MUTED, anchor="center", tags="model_toggle",
            )

            y += toggle_h + gap

            if self._model_list_expanded:
                for idx, mid in enumerate(other_ids, start=1):
                    desc = _uavedusim_model_description(mid)
                    selected = self._model_selected_id == mid
                    row_tag = f"model_row_{idx}"
                    row_h = _uavedusim_draw_model_row(canvas, self, x, y, w, s, idx, mid, desc, selected, tag=row_tag)
                    canvas.tag_bind(row_tag, "<Button-1>", lambda _e, m=mid: select_model(m))
                    canvas.tag_bind(row_tag, "<Enter>", lambda _e: canvas.configure(cursor="hand2"))
                    canvas.tag_bind(row_tag, "<Leave>", lambda _e: canvas.configure(cursor=""))
                    y += row_h + max(self.px(6), int(7 * s))

        scroll_h = y + self.px(10)
        canvas.configure(scrollregion=(0, 0, width, scroll_h))

        self._model_screen_scroll_enabled = scroll_h > visible_h + 2

        if self._model_screen_scroll_enabled:
            if not scrollbar.winfo_ismapped():
                scrollbar.pack(side="right", fill="y")
        else:
            if scrollbar.winfo_ismapped():
                scrollbar.pack_forget()
            canvas.yview_moveto(0)

        canvas.tag_bind("model_card_primary", "<Button-1>", lambda _e: select_model(primary_id))
        canvas.tag_bind("model_card_secondary", "<Button-1>", lambda _e: select_model(secondary_id))
        canvas.tag_bind("model_toggle", "<Button-1>", lambda _e: toggle_expanded())

        for t in ("model_card_primary", "model_card_secondary", "model_toggle"):
            canvas.tag_bind(t, "<Enter>", lambda _e: canvas.configure(cursor="hand2"))
            canvas.tag_bind(t, "<Leave>", lambda _e: canvas.configure(cursor=""))

    def select_model(model_id):
        if not model_id:
            return
        self._model_selected_id = model_id
        draw()

    def toggle_expanded():
        self._model_list_expanded = not self._model_list_expanded
        draw()

    canvas.bind("<Configure>", draw)
    _uavedusim_bind_canvas_wheel_scroll(canvas, lambda: getattr(self, "_model_screen_scroll_enabled", False))

    self.root.after_idle(draw)

    tk.Frame(footer, bg=BORDER, height=1).pack(fill="x", pady=(0, self.px(10)))

    left_btns = tk.Frame(footer, bg=SURFACE)
    left_btns.pack(side="left")

    _uavedusim_footer_back_button(
        self, left_btns, self.show_main_menu,
    ).pack(side="left")

    right_btns = tk.Frame(footer, bg=SURFACE)
    right_btns.pack(side="right")

    _uavedusim_footer_close_button(
        self, right_btns, self.close_app,
    ).pack(side="left", padx=(0, self.px(8)))

    _uavedusim_footer_primary_button(
        self, right_btns,
        lambda: _uavedusim_handle_model_choice_with_description(self, self._model_selected_id),
        "Selecionar 󰁕",
    ).pack(side="left")


UAVEDUSIMApp.start_uavedusim_flow = _uavedusim_show_model_selection_screen
# END UAVEDUSIM_MODEL_SELECTION_SCREEN_PATCH


def _uavedusim_canvas_option_parts(label):
    text = str(label or "")
    if "\n" not in text:
        return text, ""

    title, description = text.split("\n", 1)
    return title.strip(), " ".join(description.split())


def _uavedusim_canvas_row_height_for(self, label, width=None):
    _title, description = _uavedusim_canvas_option_parts(label)

    if not description:
        return self.app.ROW_H

    w = width or self.winfo_width() or 680
    text_width = max(self.app.px(220), w - self.app.LABEL_X - self.app.PADX - self.app.px(32))
    approx_char_px = max(6, self.app.px(6.3))
    chars_per_line = max(34, int(text_width / approx_char_px))
    lines = max(1, min(3, (len(description) + chars_per_line - 1) // chars_per_line))

    return max(self.app.px(72), self.app.px(42) + lines * self.app.px(17))


def _uavedusim_canvas_row_bounds(self, idx):
    w = self.winfo_width() or 680
    y = 0

    for i, label in enumerate(self.opts):
        row_h = self._row_height_for(label, w)
        if i == idx:
            return y, y + row_h
        y += row_h

    return 0, 0


def _uavedusim_canvas_total_height(self):
    w = self.winfo_width() or 680
    return sum(self._row_height_for(label, w) for label in self.opts)


# Desenha itens com titulo e descrição na lista.
def _uavedusim_canvas_draw_with_descriptions(self):
    self.delete("all")
    w = self.winfo_width() or 680
    y0 = 0

    for i, label in enumerate(self.opts):
        title, description = _uavedusim_canvas_option_parts(label)
        row_h = self._row_height_for(label, w)
        y1 = y0 + row_h
        is_sel = i == self.selected_idx
        is_hover = i == self.hover_idx and not is_sel
        has_description = bool(description)

        row_bg = SEL_BG if is_sel else (BORDER if is_hover else (ROW_ODD if i % 2 else ROW_EVEN))
        fg = SEL_FG if is_sel else TEXT
        desc_fg = TAG_FG if is_sel else MUTED

        self.create_rectangle(0, y0, w, y1, fill=row_bg, outline="")

        if is_sel:
            self.create_rectangle(
                0,
                y0 + self.app.SEL_BAR_PADY,
                self.app.SEL_BAR_W,
                y1 - self.app.SEL_BAR_PADY,
                fill=ACCENT,
                outline="",
            )

        center_y = y0 + row_h // 2
        title_y = y0 + (self.app.px(23) if has_description else row_h // 2)
        desc_y = y0 + self.app.px(45)

        self.create_text(
            self.app.PADX,
            center_y,
            text=f"#{i+1:02d}",
            anchor="w",
            fill=MUTED if not is_sel else SEL_FG,
            font=(self.app.FONT_FAMILY, self.app.FS_TINY),
        )

        self.create_text(
            self.app.ICON_X,
            center_y,
            text=icon_for(title),
            anchor="w",
            fill=ACCENT if is_sel else (TAG_FG if is_hover else MUTED),
            font=(self.app.FONT_FAMILY, self.app.FS_ICON),
        )

        self.create_text(
            self.app.LABEL_X,
            title_y,
            text=title,
            anchor="w",
            fill=fg,
            font=(self.app.FONT_FAMILY, self.app.FS_ROW, "bold" if is_sel else "normal"),
        )

        if description:
            desc_width = max(self.app.px(220), w - self.app.LABEL_X - self.app.PADX - self.app.px(42))
            self.create_text(
                self.app.LABEL_X,
                desc_y,
                text=description,
                anchor="nw",
                fill=desc_fg,
                width=desc_width,
                font=(self.app.FONT_FAMILY, self.app.FS_TINY),
            )

        if is_sel:
            self.create_text(
                w - self.app.PADX,
                center_y,
                text="›",
                anchor="e",
                fill=ACCENT,
                font=(self.app.FONT_FAMILY, self.app.FS_ARROW, "bold"),
            )

        if i < len(self.opts) - 1:
            self.create_line(self.app.PADX, y1, w - self.app.PADX, y1, fill=BORDER, width=1)

        y0 = y1

    self.configure(scrollregion=(0, 0, w, self._total_height()))


def _uavedusim_canvas_idx_at_with_descriptions(self, y):
    cy = self.canvasy(y)
    current = 0
    w = self.winfo_width() or 680

    for idx, label in enumerate(self.opts):
        row_h = self._row_height_for(label, w)
        if current <= cy < current + row_h:
            return idx
        current += row_h

    return None


def _uavedusim_canvas_ensure_visible_with_descriptions(self, idx):
    top, bottom = self._row_bounds(idx)
    h = self.winfo_height()
    cy_top = self.canvasy(0)
    total = max(self._total_height(), 1)

    if top < cy_top:
        self.yview_moveto(top / total)
    elif bottom > cy_top + h:
        self.yview_moveto((bottom - h) / total)


UAVEDUSIMApp.get_models = _uavedusim_get_models_with_descriptions

ItemCanvas._row_height_for = _uavedusim_canvas_row_height_for
ItemCanvas._row_bounds = _uavedusim_canvas_row_bounds
ItemCanvas._total_height = _uavedusim_canvas_total_height
ItemCanvas._draw = _uavedusim_canvas_draw_with_descriptions
ItemCanvas._idx_at = _uavedusim_canvas_idx_at_with_descriptions
ItemCanvas._ensure_visible = _uavedusim_canvas_ensure_visible_with_descriptions


# Cancelamento seguro da tela de carregamento.

def _uavedusim_cancel_wait_loading_screen(self):
    if getattr(self, "wait_cancel_requested", False):
        return

    self.wait_cancel_requested = True

    log("Cancelamento solicitado na tela de carregamento. Encerrando terminais e processos abertos.")

    self.selected_callback = None
    self.back_callback = None

    if hasattr(self, "progress"):
        try:
            self.progress.stop()
        except Exception:
            pass

    if hasattr(self, "status_lbl"):
        try:
            self.status_lbl.configure(
                text=(
                    "Cancelando carregamento...\n"
                    "Encerrando terminais e janelas abertas pelo UAVEDUSIM."
                ),
                fg=WARN,
            )
        except Exception:
            pass

    if hasattr(self, "wait_btn_frame"):
        for child in self.wait_btn_frame.winfo_children():
            try:
                if isinstance(child, RoundedButton):
                    child.set_enabled(False)
                else:
                    child.configure(state="disabled", cursor="watch")
            except Exception:
                pass

    def worker():
        try:
            self.kill_tracked_terminals()
        except Exception as e:
            log(f"Aviso: falha ao encerrar terminais rastreados durante cancelamento: {e}")

        try:
            self.cleanup_uavedusim_processes()
        except Exception as e:
            log(f"Aviso: falha ao encerrar processos durante cancelamento: {e}")

        def finish():
            self.wait_cancel_requested = False
            self.takeoff_sent = False
            self.destination_sent = False
            self.active_attack_key = None
            self.jamming_active = False
            self.mitigation_state = "off"
            self.attack_status = ""
            self.attack_warning = ""
            self.show_main_menu()

        try:
            self.root.after(0, finish)
        except Exception:
            pass

    threading.Thread(target=worker, daemon=True).start()


_original_show_wait_page_for_cancel_cleanup = UAVEDUSIMApp.show_wait_page

def _uavedusim_show_wait_page_with_cancel_cleanup(self):
    _original_show_wait_page_for_cancel_cleanup(self)

    self.back_callback = self.cancel_wait_loading_screen

    if hasattr(self, "wait_cancel_button"):
        try:
            self.wait_cancel_button._command = self.cancel_wait_loading_screen
        except Exception:
            pass
    elif hasattr(self, "wait_btn_frame"):
        # Fallback (não deveria ser necessário): procura por um widget
        # com opção "text" clássica (ttk.Button/tk.Button), caso o botão
        # "Cancelar" não tenha sido criado como RoundedButton por algum motivo.
        for child in self.wait_btn_frame.winfo_children():
            try:
                if str(child.cget("text")).strip().lower() == "cancelar":
                    child.configure(command=self.cancel_wait_loading_screen)
            except Exception:
                pass


_original_on_escape_for_wait_cancel_cleanup = UAVEDUSIMApp.on_escape

def _uavedusim_on_escape_with_wait_cancel_cleanup(self, event=None):
    if getattr(self, "page", "") == "wait":
        self.cancel_wait_loading_screen()
        return

    return _original_on_escape_for_wait_cancel_cleanup(self, event)


UAVEDUSIMApp.cancel_wait_loading_screen = _uavedusim_cancel_wait_loading_screen
UAVEDUSIMApp.show_wait_page = _uavedusim_show_wait_page_with_cancel_cleanup
UAVEDUSIMApp.on_escape = _uavedusim_on_escape_with_wait_cancel_cleanup


# Descrições didáticas dos mundos Gazebo.

PX4_WORLD_DOC_ORDER = [
    "default",
    "aruco",
    "baylands",
    "bayland",
    "ridge",
    "walls",
    "windy",
    "moving_platform",
]

PX4_WORLD_DESCRIPTIONS = {
    "default": "Empty (Default). Mundo vazio com plano cinza; usado por padrão.",
    "aruco": "Aruco. Mundo default com marcador ArUco; usado com x500_mono_cam_down para testar pouso preciso.",
    "baylands": "Baylands. Cenário com águas e árvores.",
    "bayland": "Baylands. Cenário com águas e árvores.",
    "lawn": "Lawn. Mundo plano e verde; alternativa menos otimizada ao rover world.",
    "ridge": "Ridge. Mundo com terreno inclinado para testar terrain following, especialmente com x500_lidar_down.",
    "walls": "Walls. Mundo com paredes para testes de collision prevention.",
    "windy": "Windy. Mundo vazio com vento habilitado.",
    "moving_platform": "Moving Platform. Plataforma plana em movimento; útil para simular pouso e decolagem de VTOL a partir de uma base móvel.",
}

PX4_ROVER_WORLD_NAMES = {
    "rover",
}


def _uavedusim_is_rover_world(world_id):
    if os.environ.get("UAVEDUSIM_SHOW_ROVER_WORLDS", "0") == "1":
        return False

    low = str(world_id or "").strip().lower()

    if low in PX4_ROVER_WORLD_NAMES:
        return True

    if low.startswith("rover_") or low.endswith("_rover") or "_rover_" in low:
        return True

    if low.startswith("r1_rover") or low.startswith("rover"):
        return True

    return False


# Idêntica a _uavedusim_model_id_from_choice (pega o primeiro token da
# primeira linha); mantida como alias por clareza semântica nos dois
# contextos (modelo x mundo) sem duplicar a implementação.
_uavedusim_world_id_from_choice = _uavedusim_model_id_from_choice


def _uavedusim_world_description(world_id):
    if world_id in PX4_WORLD_DESCRIPTIONS:
        return _uavedusim_translate(PX4_WORLD_DESCRIPTIONS[world_id])

    pretty = str(world_id or "").replace("_", " ").strip()

    if not pretty:
        return _uavedusim_L("Mundo Gazebo disponível no PX4.", "Gazebo world available in PX4.")

    return f"{pretty}. " + _uavedusim_L("Mundo Gazebo disponível no PX4.", "Gazebo world available in PX4.")


def _uavedusim_world_display_label(world_id):
    title = "default (mundo indicado)" if world_id == "default" else world_id
    description = _uavedusim_world_description(world_id)
    return f"{title}\n{description}"


# Lista mundos com descrição didática.
def _uavedusim_get_worlds_with_descriptions(self):
    worlds_dir = PX4_DIR / "Tools/simulation/gz/worlds"

    if not worlds_dir.is_dir():
        raise RuntimeError(_uavedusim_L(f"Não encontrei a pasta de mundos do Gazebo: {worlds_dir}", f"Couldn't find the Gazebo worlds folder: {worlds_dir}"))

    discovered = []

    for item in sorted(worlds_dir.glob("*.sdf"), key=lambda p: p.name):
        name = item.stem

        if _uavedusim_is_rover_world(name):
            log(f"Ignorando mundo rover na lista de mundos: {name}")
            continue

        if name not in discovered:
            discovered.append(name)

    ordered = []

    for world_id in PX4_WORLD_DOC_ORDER:
        if world_id in discovered and world_id not in ordered:
            ordered.append(world_id)

    # Restrito à lista curada de mundos pra drones/aviões — mundos
    # extras descobertos no disco (rover, lawn, moving_platform etc.)
    # não entram na lista exibida.

    if not ordered:
        raise RuntimeError(f"Nenhum mundo .sdf foi encontrado em: {worlds_dir}")

    return [_uavedusim_world_display_label(world_id) for world_id in ordered]


# Extrai o token real do mundo selecionado.
def _uavedusim_handle_world_choice_with_description(self, choice):
    self.world = _uavedusim_world_id_from_choice(choice)

    if not self.world:
        self.show_error("Mundo invalido selecionado.")
        return

    self.takeoff_sent = False
    self.destination_sent = False
    self.active_attack_key = None
    self.jamming_active = False

    log(f"PX4 selecionado: modelo={self.model} mundo={self.world}")

    try:
        self.ensure_uavjamsim_links(self.model == "gz_x500_uavjamsim")
    except Exception as e:
        self.show_error(str(e))
        return

    self.open_qgc()
    self.root.after(1000, self.open_px4_and_wait)


UAVEDUSIMApp.get_worlds = _uavedusim_get_worlds_with_descriptions


# BEGIN UAVEDUSIM_WORLD_SELECTION_SCREEN_PATCH
# Tela de escolha de mundo: "default" e "baylands" ficam sempre
# visíveis, lado a lado (mesma lógica da tela de modelo); o resto dos
# mundos fica escondido atrás de um botão "Ver todos os N mundos".
# Reaproveita os desenhos de card/linha já criados para a tela de
# modelos (_uavedusim_draw_model_card / _uavedusim_draw_model_row),
# que já são genéricos o suficiente (nome + descrição).

def _uavedusim_show_world_selection_screen(self):
    self.page = "world_select"
    self.back_callback = self.start_uavedusim_flow
    self.selected_callback = None
    self.clear_content()
    self.set_badge("ESCOLHER MUNDO")

    try:
        formatted = self.get_worlds()
    except Exception as e:
        self.show_error(str(e))
        return

    world_ids = [_uavedusim_world_id_from_choice(f) for f in formatted]

    if not world_ids:
        self.show_error("Nenhum mundo de simulação foi encontrado.")
        return

    primary_id = "default" if "default" in world_ids else None
    secondary_id = "baylands" if "baylands" in world_ids else ("bayland" if "bayland" in world_ids else None)
    other_ids = [w for w in world_ids if w not in (primary_id, secondary_id)]

    if not hasattr(self, "_world_selected_id") or self._world_selected_id not in world_ids:
        self._world_selected_id = primary_id or secondary_id or world_ids[0]

    if not hasattr(self, "_world_list_expanded"):
        self._world_list_expanded = False

    footer = tk.Frame(self.content, bg=SURFACE)
    footer.pack(
        side="bottom",
        fill="x",
        padx=self.CONTENT_PAD,
        pady=(self.px(10), self.FOOTER_BOTTOM_PAD),
    )

    outer = tk.Frame(self.content, bg=SURFACE)
    outer.pack(
        fill="both",
        expand=True,
        padx=self.CONTENT_PAD,
        pady=(self.HEADER_TOP_PAD, 0),
    )

    canvas, scrollbar = _uavedusim_make_scrollable_canvas(self, outer)

    self._world_screen_scroll_enabled = False

    WORLD_SKY = "#89dceb"

    def draw(_event=None):
        canvas.delete("all")

        width = max(canvas.winfo_width(), 1)
        visible_h = max(canvas.winfo_height(), 1)

        s = _uj_clamp(width / self.px(940), .82, 1.04)

        x = max(self.px(4), int(width * .008))
        w = max(self.px(420), width - 2 * x)
        y = self.px(4)

        title = "Escolha o mundo de simulação onde o drone vai voar"
        title_font = _uj_font(self, 17.5 * s, "bold")
        _uj_text(canvas, x, y, title, title_font, TEXT, width=w)
        y += _uj_measure_text_height(self, title, title_font, w) + max(self.px(5), int(5 * s))

        subtitle = (
            "Selecione o ambiente do Gazebo onde o voo será executado. O mundo define o "
            "cenário físico usado durante a missão simulada."
        )
        subtitle_font = _uj_font(self, 11.2 * s)
        _uj_text(canvas, x, y, subtitle, subtitle_font, MUTED, width=w)
        y += _uj_measure_text_height(self, subtitle, subtitle_font, w) + max(self.px(16), int(18 * s))

        gap = max(self.px(10), int(11 * s))
        wide = w >= max(self.px(560), int(560 * s))

        if primary_id or secondary_id:
            cards = [
                (
                    primary_id, "MUNDO INDICADO", "#a6e3a1", "#a6e3a1", "#a6e3a1",
                    "Mundo vazio, com plano cinza e sem obstáculos — o ambiente mais simples "
                    "para começar a testar os ataques de jamming.",
                ),
                (
                    secondary_id, "CENÁRIO COM ÁGUA E ÁRVORES", WORLD_SKY, WORLD_SKY, WORLD_SKY,
                    "Mundo cercado por água e árvores — útil para observar o que acontece quando "
                    "a mitigação aciona um pouso de emergência em terreno inadequado.",
                ),
            ]

            if wide and primary_id and secondary_id:
                col_w = (w - gap) / 2
                heights = [
                    _uavedusim_model_card_metrics(self, col_w, s, wid, desc)["height"]
                    for wid, _b, _bc, _nc, _brc, desc in cards
                ]
                card_h = max(heights)

                for idx, (wid, badge, badge_color, name_color, border_color, desc) in enumerate(cards):
                    if not wid:
                        continue
                    cx = x + idx * (col_w + gap)
                    tag = "world_card_primary" if wid == primary_id else "world_card_secondary"
                    _uavedusim_draw_model_card(
                        canvas, self, cx, y, col_w, card_h, s, wid, desc,
                        badge, badge_color, name_color, border_color,
                        self._world_selected_id == wid, None, tag=tag,
                    )
                y += card_h + gap
            else:
                for wid, badge, badge_color, name_color, border_color, desc in cards:
                    if not wid:
                        continue
                    ch = _uavedusim_model_card_metrics(self, w, s, wid, desc)["height"]
                    tag = "world_card_primary" if wid == primary_id else "world_card_secondary"
                    _uavedusim_draw_model_card(
                        canvas, self, x, y, w, ch, s, wid, desc,
                        badge, badge_color, name_color, border_color,
                        self._world_selected_id == wid, None, tag=tag,
                    )
                    y += ch + gap

        if other_ids:
            toggle_h = max(self.px(38), int(40 * s))
            toggle_fill = _uavedusim_blend(BG, SURFACE, 0.22)

            pts = _uavedusim_round_rect_points(x, y, x + w, y + toggle_h, self.px(8))
            canvas.create_polygon(
                pts, fill=toggle_fill, outline=BORDER, width=1,
                smooth=True, splinesteps=16, dash=(4, 3), tags="world_toggle",
            )

            chevron = "⌃" if self._world_list_expanded else "⌄"
            label = (
                _uavedusim_L(f"Ocultar os outros {len(other_ids)} mundos", f"Hide the other {len(other_ids)} worlds")
                if self._world_list_expanded
                else _uavedusim_L(f"Ver mais {len(other_ids)} mundos", f"See {len(other_ids)} more worlds")
            )

            canvas.create_text(
                x + w / 2, y + toggle_h / 2,
                text=f"{label}  {chevron}",
                font=_uj_font(self, 10.6 * s),
                fill=MUTED, anchor="center", tags="world_toggle",
            )

            y += toggle_h + gap

            if self._world_list_expanded:
                for idx, wid in enumerate(other_ids, start=1):
                    desc = _uavedusim_world_description(wid)
                    selected = self._world_selected_id == wid
                    row_tag = f"world_row_{idx}"
                    row_h = _uavedusim_draw_model_row(canvas, self, x, y, w, s, idx, wid, desc, selected, tag=row_tag)
                    canvas.tag_bind(row_tag, "<Button-1>", lambda _e, m=wid: select_world(m))
                    canvas.tag_bind(row_tag, "<Enter>", lambda _e: canvas.configure(cursor="hand2"))
                    canvas.tag_bind(row_tag, "<Leave>", lambda _e: canvas.configure(cursor=""))
                    y += row_h + max(self.px(6), int(7 * s))

        scroll_h = y + self.px(10)
        canvas.configure(scrollregion=(0, 0, width, scroll_h))

        self._world_screen_scroll_enabled = scroll_h > visible_h + 2

        if self._world_screen_scroll_enabled:
            if not scrollbar.winfo_ismapped():
                scrollbar.pack(side="right", fill="y")
        else:
            if scrollbar.winfo_ismapped():
                scrollbar.pack_forget()
            canvas.yview_moveto(0)

        canvas.tag_bind("world_card_primary", "<Button-1>", lambda _e: select_world(primary_id))
        canvas.tag_bind("world_card_secondary", "<Button-1>", lambda _e: select_world(secondary_id))
        canvas.tag_bind("world_toggle", "<Button-1>", lambda _e: toggle_expanded())

        for t in ("world_card_primary", "world_card_secondary", "world_toggle"):
            canvas.tag_bind(t, "<Enter>", lambda _e: canvas.configure(cursor="hand2"))
            canvas.tag_bind(t, "<Leave>", lambda _e: canvas.configure(cursor=""))

    def select_world(world_id):
        if not world_id:
            return
        self._world_selected_id = world_id
        draw()

    def toggle_expanded():
        self._world_list_expanded = not self._world_list_expanded
        draw()

    canvas.bind("<Configure>", draw)
    _uavedusim_bind_canvas_wheel_scroll(canvas, lambda: getattr(self, "_world_screen_scroll_enabled", False))

    self.root.after_idle(draw)

    tk.Frame(footer, bg=BORDER, height=1).pack(fill="x", pady=(0, self.px(10)))

    left_btns = tk.Frame(footer, bg=SURFACE)
    left_btns.pack(side="left")

    _uavedusim_footer_back_button(
        self, left_btns, self.start_uavedusim_flow,
    ).pack(side="left")

    right_btns = tk.Frame(footer, bg=SURFACE)
    right_btns.pack(side="right")

    _uavedusim_footer_close_button(
        self, right_btns, self.close_app,
    ).pack(side="left", padx=(0, self.px(8)))

    _uavedusim_footer_primary_button(
        self, right_btns,
        lambda: _uavedusim_handle_world_choice_with_description(self, self._world_selected_id),
        "Selecionar 󰁕",
    ).pack(side="left")
# END UAVEDUSIM_WORLD_SELECTION_SCREEN_PATCH


# Telas educativas da página inicial.

# Executa uma ação da tela inicial evitando duplo clique.
# Desenha um ícone de drone em vetor, direto no Canvas, inspirado no
# estilo dos ícones "sharp/light" de bibliotecas como o Font Awesome
# (moldura central + 4 "loops" nos rotores, ligados por hastes curtas).
# Evita depender de fontes de ícones externas, que precisariam ser
# baixadas/instaladas à parte e podem não estar disponíveis na máquina
# de quem for rodar o UAVEDUSIM. Por ser vetor, acompanha o
# dimensionamento responsivo (basta variar `size`).
def _uavedusim_draw_drone_icon(canvas, cx, cy, size, color=ACCENT):
    stroke = max(1, size * 0.052)

    # Moldura central.
    cs = size * 0.125
    cr = size * 0.045
    pts = _uavedusim_round_rect_points(cx - cs, cy - cs, cx + cs, cy + cs, cr)
    canvas.create_polygon(pts, fill="", outline=color, width=stroke, smooth=True, splinesteps=12)

    # Loops dos rotores + hastes de conexão.
    offset = size * 0.25
    loop_half = size * 0.078
    loop_r = size * 0.04

    for sx, sy in ((-1, -1), (1, -1), (-1, 1), (1, 1)):
        lx, ly = cx + sx * offset, cy + sy * offset
        loop_pts = _uavedusim_round_rect_points(
            lx - loop_half, ly - loop_half, lx + loop_half, ly + loop_half, loop_r,
        )
        canvas.create_polygon(loop_pts, fill="", outline=color, width=stroke, smooth=True, splinesteps=12)
        canvas.create_line(
            cx + sx * cs * 0.7, cy + sy * cs * 0.7,
            lx - sx * loop_half * 0.7, ly - sy * loop_half * 0.7,
            fill=color, width=stroke, capstyle="round",
        )


def _uavedusim_home_run_choice(self, choice):
    if getattr(self, "_home_choice_busy", False):
        return

    self._home_choice_busy = True

    try:
        self.handle_main_choice(choice)
    finally:
        try:
            self.root.after(700, lambda: setattr(self, "_home_choice_busy", False))
        except Exception:
            self._home_choice_busy = False


# Mantem os botões da tela inicial organizados.
def _uavedusim_home_reflow_buttons(self, _event=None):
    frame = getattr(self, "home_buttons_frame", None)
    buttons = getattr(self, "home_action_buttons", [])

    if frame is None or not frame.winfo_exists() or not buttons:
        return

    n = len(buttons)
    width = frame.winfo_width() or self.content.winfo_width() or self.px(900)
    compact = width < self.px(260) * n

    for btn in buttons:
        try:
            btn.grid_forget()
        except Exception:
            pass

    for col in range(n):
        frame.grid_columnconfigure(col, weight=0, uniform="")

    if compact:
        frame.grid_columnconfigure(0, weight=1)

        for row, btn in enumerate(buttons):
            btn.grid(row=row, column=0, sticky="ew", padx=0, pady=(0, self.px(8)))
    else:
        for col in range(n):
            frame.grid_columnconfigure(col, weight=1, uniform="home_actions")

        for col, btn in enumerate(buttons):
            btn.grid(
                row=0,
                column=col,
                sticky="ew",
                padx=(0 if col == 0 else self.px(10), 0),
                pady=0,
            )



# Renderiza a página inicial educativa.
def _uavedusim_home_show_main_menu(self):
    self.page = "main"
    self.selected_callback = None
    self.back_callback = None
    self.clear_content()
    self.set_badge("MAIN MENU")

    main_box = RoundedPanel(
        self.content,
        outer_bg=SURFACE,
        fill=SURFACE2,
        radius=self.px(14),
        border=BORDER,
        border_width=1,
        auto_height=False,
    )
    main_box.pack(
        fill="both",
        expand=True,
        padx=self.CONTENT_PAD,
        pady=(self.HEADER_TOP_PAD, 0),
    )

    scroll_outer = tk.Frame(main_box.body, bg=SURFACE2)
    scroll_outer.pack(fill="both", expand=True)

    text_canvas = tk.Canvas(scroll_outer, bg=SURFACE2, highlightthickness=0, bd=0)
    text_scrollbar = ModernScrollbar(
        scroll_outer,
        command=text_canvas.yview,
        width=self.SCROLLBAR_W,
        bg=SURFACE2,
        thumb=BORDER,
        thumb_active=MUTED,
    )
    text_canvas.configure(yscrollcommand=text_scrollbar.set)

    text_canvas.pack(side="left", fill="both", expand=True)

    self.home_text_canvas = text_canvas

    text_area = tk.Frame(text_canvas, bg=SURFACE2)
    text_area_id = text_canvas.create_window((0, 0), window=text_area, anchor="nw")

    inner_pad = self.px(10)

    self._home_scroll_enabled = False

    def _content_overflows():
        try:
            content_h = text_area.winfo_reqheight()
            visible_h = text_canvas.winfo_height()
            return content_h > visible_h
        except Exception:
            return False

    def _sync_text_scrollregion(_event=None):
        try:
            if not text_canvas.winfo_exists():
                return
            text_canvas.configure(scrollregion=text_canvas.bbox("all"))

            overflows = _content_overflows()
            self._home_scroll_enabled = overflows

            if overflows:
                if not text_scrollbar.winfo_ismapped():
                    text_scrollbar.pack(side="right", fill="y")
                text_canvas.coords(text_area_id, 0, 0)
            else:
                if text_scrollbar.winfo_ismapped():
                    text_scrollbar.pack_forget()
                text_canvas.yview_moveto(0)
                content_h = text_area.winfo_reqheight()
                visible_h = text_canvas.winfo_height()
                y_offset = max(0, (visible_h - content_h) // 2)
                text_canvas.coords(text_area_id, 0, y_offset)
        except Exception:
            pass

    def _sync_text_width(event):
        text_canvas.itemconfigure(text_area_id, width=event.width)
        _sync_text_scrollregion()

    def _text_wheel(event):
        if not self._home_scroll_enabled:
            return

        if getattr(event, "num", None) == 4:
            _uavedusim_safe_yview_scroll(text_canvas, -3, "units")
        elif getattr(event, "num", None) == 5:
            _uavedusim_safe_yview_scroll(text_canvas, 3, "units")
        elif getattr(event, "delta", 0):
            _uavedusim_safe_yview_scroll(text_canvas, int(-1 * (event.delta / 120)), "units")

    text_area.bind("<Configure>", _sync_text_scrollregion)
    text_canvas.bind(
        "<Enter>",
        lambda _e: (
            text_canvas.bind_all("<MouseWheel>", _text_wheel),
            text_canvas.bind_all("<Button-4>", _text_wheel),
            text_canvas.bind_all("<Button-5>", _text_wheel),
        ),
    )
    text_canvas.bind(
        "<Leave>",
        lambda _e: (
            text_canvas.unbind_all("<MouseWheel>"),
            text_canvas.unbind_all("<Button-4>"),
            text_canvas.unbind_all("<Button-5>"),
        ),
    )

    text_area_padded = tk.Frame(text_area, bg=SURFACE2)
    text_area_padded.pack(
        fill="both",
        expand=True,
        padx=inner_pad,
        pady=(inner_pad, self.px(10)),
    )

    self._home_text_labels = []

    # --- Cabeçalho: ícone + título + subtítulo curto ---------------------
    hero = tk.Frame(text_area_padded, bg=SURFACE2)
    hero.pack(fill="x", pady=(0, self.px(18)))

    icon_size = self.px(70)
    icon_canvas = tk.Canvas(
        hero, width=icon_size, height=icon_size, bg=SURFACE2, highlightthickness=0, bd=0,
    )
    icon_canvas.pack(side="left", padx=(0, self.px(16)))
    ring = self.px(2)
    icon_canvas.create_oval(
        ring, ring, icon_size - ring, icon_size - ring,
        fill=SURFACE, outline=ACCENT_DIM, width=ring,
    )
    _uavedusim_draw_drone_icon(icon_canvas, icon_size / 2, icon_size / 2, icon_size * 0.72)

    hero_text = tk.Frame(hero, bg=SURFACE2)
    hero_text.pack(side="left", fill="x", expand=True, anchor="w")

    tk.Label(
        hero_text,
        text="UAVEDUSIM",
        bg=SURFACE2,
        fg=TEXT,
        justify="left",
        anchor="w",
        font=(self.FONT_FAMILY, self.fs(30), "bold"),
    ).pack(anchor="w")

    tk.Label(
        hero_text,
        text="Simulador de ataques de jamming em VANTs",
        bg=SURFACE2,
        fg=MUTED,
        justify="left",
        anchor="w",
        font=(self.FONT_FAMILY, self.fs(13)),
    ).pack(anchor="w", pady=(self.px(3), 0))

    # --- Card explicativo curto, com link para a tela detalhada ----------
    explainer_card = RoundedPanel(
        text_area_padded, outer_bg=SURFACE2, fill=SURFACE, radius=self.px(14),
    )
    explainer_card.pack(fill="x", pady=(0, self.px(18)))

    explainer_lbl = tk.Label(
        explainer_card.body,
        text=(
            "Ambiente seguro para estudar como falhas de GPS/GNSS afetam a navegação de "
            "um VANT, usando PX4, Gazebo e QGroundControl — sem risco de interferência real."
        ),
        bg=SURFACE,
        fg=TEXT,
        justify="left",
        anchor="nw",
        wraplength=max(self.px(420), self.win_w - self.px(180)),
        font=(self.FONT_FAMILY, self.fs(11.5)),
    )
    explainer_lbl.pack(anchor="w", fill="x", padx=self.px(14), pady=(self.px(14), self.px(6)))
    self._home_text_labels.append(explainer_lbl)

    def _uavedusim_home_about_jamming_click(_event=None):
        _uavedusim_show_jamming_info_screen(self)

    # Chip preenchido (em vez de link de texto solto): o contorno
    # fechado já sinaliza "isso é um controle clicável", sem depender
    # só da seta. Fundo neutro (Surface2 da paleta) para não competir
    # com o texto/ícone em Mauve — só no hover ganha um leve tingido.
    about_link_fill = BORDER
    about_link_fill_hover = _uavedusim_blend(BORDER, ACCENT, 0.28)

    about_link_icon = "\ue66a"
    about_link_gap = "    "
    about_link_text = _uavedusim_translate("O que é jamming? Aprenda e desafie-se no quiz")
    about_link_arrow = "  󰁕"
    about_link_font = (self.FONT_FAMILY, self.fs(11.5), "bold")
    # Ícone e texto vão no mesmo bloco de texto (em vez do parâmetro
    # icon=) para controlar a distância entre eles com espaçamento real.
    about_link_full_text = about_link_icon + about_link_gap + about_link_text + about_link_arrow

    # O RoundedButton tem largura fixa (pack_propagate desligado), então
    # medimos o texto real com o mesmo font para garantir que o chip
    # nasce largo o suficiente pra não cortar a frase.
    import tkinter.font as _uavedusim_home_tkfont
    _about_measure_font = _uavedusim_home_tkfont.Font(font=about_link_font)
    _about_pad_x = self.px(22)
    about_link_width = _about_measure_font.measure(about_link_full_text) + _about_pad_x * 2
    # Não deixa o chip espremer o cartão em janelas estreitas.
    about_link_max_width = max(self.px(260), self.win_w - self.px(210))
    about_link_width = min(about_link_width, about_link_max_width)

    about_link = RoundedButton(
        explainer_card.body,
        text=about_link_full_text,
        command=_uavedusim_home_about_jamming_click,
        outer_bg=SURFACE,
        fill=about_link_fill,
        fill_hover=about_link_fill_hover,
        fill_press=about_link_fill_hover,
        text_color=ACCENT,
        text_color_hover=ACCENT,
        radius=self.px(16),
        height=self.px(34),
        width=about_link_width,
        font=about_link_font,
    )
    about_link.pack(anchor="w", padx=self.px(14), pady=(0, self.px(14)))

    self.home_about_button = about_link

    # --- Ação principal: única cor de destaque da tela --------------------
    sim_unlocked_home = _uavedusim_quiz_gate_ok(self)

    if sim_unlocked_home:
        cta_gradient = (ACCENT, LAVENDER)
        cta_fill_hover = "#ddc5fb"
        cta_fill_press = ACCENT_DIM
        cta_icon = ICONS["executar"]
        cta_icon_color = None
    else:
        cta_gradient = (_uavedusim_blend(ACCENT, BG, 0.45), _uavedusim_blend(LAVENDER, BG, 0.45))
        cta_fill_hover = _uavedusim_blend(ACCENT, BG, 0.35)
        cta_fill_press = _uavedusim_blend(ACCENT, BG, 0.45)
        cta_icon = "\uf456"
        cta_icon_color = DANGER

    self.home_cta_button = RoundedButton(
        text_area_padded,
        text="Iniciar simulação",
        icon=cta_icon,
        icon_color=cta_icon_color,
        command=lambda: _uavedusim_home_run_choice(self, "Executar UAVEDUSIM"),
        outer_bg=SURFACE2,
        fill=cta_gradient[0],
        fill_hover=cta_fill_hover,
        fill_press=cta_fill_press,
        gradient=cta_gradient,
        text_color="#11111b",
        radius=self.px(26),
        height=self.px(56),
        font=(self.FONT_FAMILY, self.fs(13), "bold"),
    )
    self.home_cta_button.pack(fill="x", pady=(0, self.px(18)))

    # --- Manutenção: peso visual menor, agrupada e rotulada ---------------
    tk.Label(
        text_area_padded,
        text="MANUTENÇÃO",
        bg=SURFACE2,
        fg=MUTED,
        anchor="w",
        font=(self.FONT_FAMILY, self.fs(9.5), "bold"),
    ).pack(anchor="w", pady=(0, self.px(8)))

    self.home_buttons_frame = tk.Frame(text_area_padded, bg=SURFACE2)
    self.home_buttons_frame.pack(fill="x", pady=(0, self.px(14)))

    self.home_action_buttons = []

    action_items = [
        (ICONS["limpar"], "Limpar builds", "Limpar build (se necessário)"),
        (ICONS["sair"], "Encerrar processos", "Limpar processos UAVEDUSIM"),
    ]

    for icon, label, choice in action_items:
        btn = RoundedButton(
            self.home_buttons_frame,
            text=label,
            icon=icon,
            command=lambda choice=choice: _uavedusim_home_run_choice(self, choice),
            outer_bg=SURFACE2,
            fill=SURFACE,
            fill_hover=BORDER,
            fill_press=BORDER,
            text_color=MUTED,
            text_color_hover=TEXT,
            radius=self.px(13),
            height=self.px(46),
            font=(self.FONT_FAMILY, self.fs(11.5)),
        )
        self.home_action_buttons.append(btn)

    self.home_buttons_frame.bind(
        "<Configure>",
        lambda event: _uavedusim_home_reflow_buttons(self, event),
    )

    # --- Alerta de manutenção, logo abaixo dos botões acima ----------------
    # Cor "Red" (#f38ba8) da paleta Catppuccin Mocha, misturada com o
    # fundo (BG) pra gerar um tom vermelho/rosado escuro, discreto.
    alert_accent = DANGER
    alert_bg = _uavedusim_blend(BG, alert_accent, 0.16)
    alert_border = _uavedusim_blend(BG, alert_accent, 0.40)
    alert_box = RoundedPanel(
        text_area_padded, outer_bg=SURFACE2, fill=alert_bg, radius=self.px(12),
        border=alert_border, border_width=1,
    )
    alert_box.pack(fill="x", pady=(0, self.px(10)))

    alert_inner = tk.Frame(alert_box.body, bg=alert_bg)
    alert_inner.pack(fill="x", padx=self.px(14), pady=self.px(12))

    tk.Label(
        alert_inner,
        text=ICONS["ataque"],
        bg=alert_bg,
        fg=alert_accent,
        font=(self.FONT_FAMILY, self.fs(13)),
    ).pack(side="left", anchor="n", padx=(0, self.px(8)))

    alert_lbl = tk.Label(
        alert_inner,
        text=(
            "Se a simulação não iniciar ou não iniciar corretamente, use os botões de "
            "manutenção acima e tente novamente."
        ),
        bg=alert_bg,
        fg=alert_accent,
        justify="left",
        anchor="nw",
        wraplength=max(self.px(380), self.win_w - self.px(220)),
        font=(self.FONT_FAMILY, self.fs(11.5)),
    )
    alert_lbl.pack(side="left", fill="x", expand=True)
    self._home_text_labels.append(alert_lbl)
    self.home_note_label = alert_lbl

    def _reflow_home_text(_event=None):
        try:
            w = text_canvas.winfo_width()
            wrap = max(self.px(380), w - self.px(70))

            for lbl in getattr(self, "_home_text_labels", []):
                lbl.configure(wraplength=wrap)
        except Exception:
            pass

    text_canvas.bind("<Configure>", lambda event: (_sync_text_width(event), _reflow_home_text()))

    # O redimensionamento do card externo (main_box) se propaga em
    # cascata até o text_canvas (main_box -> body -> scroll_outer ->
    # text_canvas). Reforça a resincronização da centralização vertical
    # nesse ponto também, para não depender só de o cascateamento
    # acontecer no mesmo ciclo de eventos.
    main_box.canvas.bind("<Configure>", lambda _e: self.root.after_idle(_sync_text_scrollregion), add="+")

    self.root.after_idle(_reflow_home_text)
    self.root.after_idle(_sync_text_scrollregion)
    self.root.after_idle(lambda: _uavedusim_home_reflow_buttons(self))
    # Verificações adicionais um pouco depois, cobrindo qualquer
    # acomodação tardia de geometria (ex.: gerenciadores de janela
    # mais lentos para aplicar o tamanho inicial da janela).
    self.root.after(60, _sync_text_scrollregion)
    self.root.after(200, _sync_text_scrollregion)

    # --- Rodapé: só a divisória e o botão de fechar ------------------------
    footer = tk.Frame(self.content, bg=SURFACE, highlightbackground=BORDER, highlightthickness=0)
    footer.pack(
        fill="x",
        padx=self.CONTENT_PAD,
        pady=(self.FOOTER_TOP_PAD, self.FOOTER_BOTTOM_PAD),
    )

    tk.Frame(footer, bg=BORDER, height=1).pack(fill="x", pady=(0, self.px(10)))

    btn_frame = tk.Frame(footer, bg=SURFACE)
    btn_frame.pack(fill="x")

    RoundedButton(
        btn_frame,
        text="Fechar",
        command=self.close_app,
        outer_bg=SURFACE,
        fill=DANGER,
        fill_hover=_uavedusim_blend(DANGER, "#ffffff", 0.18),
        fill_press=_uavedusim_blend(DANGER, "#000000", 0.18),
        text_color="#11111b",
        text_color_hover="#11111b",
        radius=self.px(11),
        height=self.px(37),
        width=self.px(100),
        font=(self.FONT_FAMILY, self.fs(11), "bold"),
    ).pack(side="right")


_UAVEDUSIM_HOME_OLD_CHOOSE_CURRENT = getattr(UAVEDUSIMApp, "choose_current", None)

if _UAVEDUSIM_HOME_OLD_CHOOSE_CURRENT is None:
    def _uavedusim_base_choose_current(self):
        if hasattr(self, "item_canvas") and self.selected_callback:
            idx = self.item_canvas.selected_idx
            if 0 <= idx < len(self.item_canvas.opts):
                self.selected_callback(self.item_canvas.opts[idx])

    _UAVEDUSIM_HOME_OLD_CHOOSE_CURRENT = _uavedusim_base_choose_current


def _uavedusim_home_choose_current(self):
    if getattr(self, "page", "") == "main":
        _uavedusim_home_run_choice(self, "Executar UAVEDUSIM")
        return

    return _UAVEDUSIM_HOME_OLD_CHOOSE_CURRENT(self)

UAVEDUSIMApp.show_main_menu = _uavedusim_home_show_main_menu
UAVEDUSIMApp.choose_current = _uavedusim_home_choose_current


# --- Tela de escolha de idioma (aparece a cada início do simulador) -------
def _uavedusim_reopen_current_screen(self):
    page = str(getattr(self, "page", "") or "")

    try:
        if page in ("main", "home"):
            self.show_main_menu()
        elif page == "jamming_info":
            _uavedusim_nav_safe_global(self, "_uavedusim_show_jamming_info_screen", self.show_main_menu)()
        elif page == "jamming_attacks_info" and callable(globals().get("_uavedusim_show_jamming_attacks_screen")):
            _uavedusim_show_jamming_attacks_screen(self)
        elif page == "mitigation_info" and callable(globals().get("_uavedusim_show_mitigation_screen")):
            _uavedusim_show_mitigation_screen(self)
        elif page == "quiz_info" and callable(globals().get("_uavedusim_show_quiz_screen")):
            _uavedusim_show_quiz_screen(self)
        elif page == "tutorial_info" and callable(globals().get("_uavedusim_show_tutorial_screen")):
            _uavedusim_show_tutorial_screen(self)
        elif page == "model_select":
            _uavedusim_show_model_selection_screen(self)
        elif page == "world_select":
            _uavedusim_show_world_selection_screen(self)
        elif page == "attack":
            self.show_attack_menu()
        elif page == "wait":
            # Não reinicia o cronômetro/checagem de prontidão do PX4/Gazebo —
            # só reconstrói os textos visíveis na tela atual.
            if hasattr(self, "_reflow_wait_status"):
                self._reflow_wait_status()
            if hasattr(self, "wait_title_lbl"):
                self.wait_title_lbl.configure(text=_uavedusim_translate("Aguardando PX4/Gazebo abrir..."))
            if hasattr(self, "wait_subtitle_lbl"):
                self.wait_subtitle_lbl.configure(text=_uavedusim_translate(
                    "Aguarde enquanto PX4 e Gazebo iniciam. O menu de ataques abre automaticamente quando a simulação estiver pronta para decolar."
                ))
        else:
            self.show_main_menu()
    except Exception:
        try:
            self.show_main_menu()
        except Exception:
            pass


def _uavedusim_show_language_picker(self):
    # Em vez de uma tela cheia dedicada, mostramos o menu principal
    # normalmente e, só se ainda não houver idioma salvo, um pop-up
    # centralizado por cima pedindo pra escolher. Se já tem preferência
    # salva, não pergunta de novo — o idioma já foi carregado no início
    # do módulo (_UAVEDUSIM_LANG["code"]) e a tela já nasce nesse idioma.
    self.show_main_menu()
    if not _uavedusim_has_saved_lang_pref():
        self.root.after_idle(lambda: _uavedusim_show_language_popup(self))


def _uavedusim_show_language_popup(self):
    old = getattr(self, "_language_popup", None)
    if old is not None:
        try:
            old.destroy()
        except Exception:
            pass
    self._language_popup = None

    try:
        self.root.update_idletasks()
    except Exception:
        pass

    # Sem backdrop cobrindo a tela: o Tkinter não faz transparência de
    # verdade, então um frame "escurecido" por cima acabava tampando a
    # tela inteira. O cartão flutua direto sobre a tela principal, que
    # continua totalmente visível ao redor dele.
    card_w = self.px(360)

    outer = tk.Frame(self.root, bg=BORDER, bd=0, highlightthickness=0)
    inner = tk.Frame(outer, bg=SURFACE2, bd=0)
    inner.pack(fill="both", expand=True, padx=1, pady=1)

    pad = self.px(26)
    content = tk.Frame(inner, bg=SURFACE2)
    content.pack(fill="both", expand=True, padx=pad, pady=pad)

    tk.Label(
        content,
        text="Escolha o idioma / Choose your language",
        bg=SURFACE2,
        fg=TEXT,
        font=(self.FONT_FAMILY, self.fs(14), "bold"),
        justify="center",
        wraplength=card_w - pad * 2,
    ).pack(pady=(0, self.px(6)))

    tk.Label(
        content,
        text="Isso define o idioma de toda a interface do simulador.",
        bg=SURFACE2,
        fg=MUTED,
        font=(self.FONT_FAMILY, self.fs(10.2)),
        justify="center",
        wraplength=card_w - pad * 2,
    ).pack(pady=(0, self.px(8)))

    tk.Frame(content, bg=BORDER, height=1).pack(fill="x", pady=(0, self.px(8)))

    tk.Label(
        content,
        text="This sets the language for the whole simulator interface.",
        bg=SURFACE2,
        fg=MUTED,
        font=(self.FONT_FAMILY, self.fs(10.2)),
        justify="center",
        wraplength=card_w - pad * 2,
    ).pack(pady=(0, self.px(20)))

    buttons_row = tk.Frame(content, bg=SURFACE2)
    buttons_row.pack()

    def _choose(code):
        _uavedusim_set_lang(code)
        try:
            outer.destroy()
        except Exception:
            pass
        self._language_popup = None
        # Re-renderiza a tela que estava aberta (não necessariamente a
        # inicial) pra aplicar o idioma escolhido em tudo que já tinha
        # sido desenhado antes da escolha.
        _uavedusim_reopen_current_screen(self)

    RoundedButton(
        buttons_row,
        text="Português",
        command=lambda: _choose("pt"),
        outer_bg=SURFACE2,
        fill=ACCENT,
        fill_hover=_uavedusim_blend(ACCENT, "#ffffff", 0.15),
        fill_press=_uavedusim_blend(ACCENT, "#000000", 0.10),
        text_color="#11111b",
        text_color_hover="#11111b",
        radius=self.px(13),
        height=self.px(44),
        width=self.px(150),
        font=(self.FONT_FAMILY, self.fs(11.5), "bold"),
    ).pack(side="left", padx=(0, self.px(10)))

    RoundedButton(
        buttons_row,
        text="English",
        command=lambda: _choose("en"),
        outer_bg=SURFACE2,
        fill=ACCENT,
        fill_hover=_uavedusim_blend(ACCENT, "#ffffff", 0.15),
        fill_press=_uavedusim_blend(ACCENT, "#000000", 0.10),
        text_color="#11111b",
        text_color_hover="#11111b",
        radius=self.px(13),
        height=self.px(44),
        width=self.px(150),
        font=(self.FONT_FAMILY, self.fs(11.5), "bold"),
    ).pack(side="left")

    outer.update_idletasks()
    card_h = outer.winfo_reqheight()
    outer.place(relx=0.5, rely=0.5, anchor="center", width=card_w, height=card_h)
    outer.lift()

    self._language_popup = outer


UAVEDUSIMApp.show_language_picker = _uavedusim_show_language_picker



# BEGIN UAVEDUSIM_TITLEBAR_NAV_MENU_PATCH
# Transforma "☰ UAVEDUSIM" da barra superior em um menu navegável entre páginas.

_UAVEDUSIM_ORIGINAL_BUILD_SHELL_FOR_NAV = UAVEDUSIMApp._build_shell
_UAVEDUSIM_ORIGINAL_ON_ESCAPE_FOR_NAV = UAVEDUSIMApp.on_escape


def _uavedusim_nav_close(self):
    menu = getattr(self, "_titlebar_nav_menu", None)

    if menu is not None:
        try:
            menu.destroy()
        except Exception:
            pass

    self._titlebar_nav_menu = None


def _uavedusim_nav_call(self, callback):
    _uavedusim_nav_close(self)

    try:
        self.root.after(1, callback)
    except Exception:
        callback()


def _uavedusim_nav_safe_global(self, name, fallback=None):
    func = globals().get(name)

    if callable(func):
        return lambda: func(self)

    if fallback is not None:
        return fallback

    return self.show_main_menu


def _uavedusim_bind_click_recursive(widget, command):
    try:
        widget.bind("<Button-1>", lambda _e: command(), add="+")
        widget.configure(cursor="hand2")
    except Exception:
        pass

    try:
        children = widget.winfo_children()
    except Exception:
        children = []

    for child in children:
        _uavedusim_bind_click_recursive(child, command)


def _uavedusim_titlebar_nav_toggle(self, event=None):
    existing = getattr(self, "_titlebar_nav_menu", None)

    if existing is not None:
        _uavedusim_nav_close(self)
        return "break"

    root = self.root
    root.update_idletasks()

    menu_w = max(self.px(300), 280)
    margin = self.px(8)

    try:
        anchor = getattr(self, "logo_icon", None) or getattr(self, "logo_lbl", None)
        x = anchor.winfo_rootx() - root.winfo_rootx()
        y = self.titlebar.winfo_rooty() - root.winfo_rooty() + self.titlebar.winfo_height() + self.px(6)
    except Exception:
        x = margin
        y = self.px(42)

    root_w = max(root.winfo_width(), 1)
    x = max(margin, min(x, root_w - menu_w - margin))

    outer = tk.Frame(
        root,
        bg=BORDER,
        bd=0,
        highlightthickness=0,
    )
    outer.place(x=x, y=y, width=menu_w)
    outer.lift()

    inner = tk.Frame(outer, bg=BG, bd=0)
    inner.pack(fill="both", expand=True, padx=1, pady=1)

    title = tk.Frame(inner, bg=BG)
    title.pack(fill="x", padx=self.px(10), pady=(self.px(10), self.px(6)))

    tk.Label(
        title,
        text="Navegação",
        bg=BG,
        fg=TEXT,
        anchor="w",
        font=(self.FONT_FAMILY, self.fs(10), "bold"),
    ).pack(side="left", fill="x", expand=True)

    tk.Label(
        title,
        text="×",
        bg=BG,
        fg=MUTED,
        cursor="hand2",
        font=(self.FONT_FAMILY, self.fs(12), "bold"),
    ).pack(side="right")

    close_label = title.winfo_children()[-1]
    close_label.bind("<Button-1>", lambda _e: _uavedusim_nav_close(self), add="+")

    current = _uavedusim_nav_current_key(self)
    quiz_unlocked = _uavedusim_quiz_gate_ok(self)
    locked_keys = {"tutorial_info", "simulation_flow"}

    for key, label, desc, command in _uavedusim_nav_items(self):
        active = key == current
        locked = key in locked_keys and not quiz_unlocked

        if locked:
            row_bg = _uavedusim_blend(BG, DANGER, 0.12)
            row_border = _uavedusim_blend(BG, DANGER, 0.35)
            row_fg = MUTED
            desc_fg = _uavedusim_blend(BG, DANGER, 0.75)
        else:
            row_bg = SURFACE2 if active else SURFACE
            row_border = ACCENT if active else BORDER
            row_fg = TEXT if active else MUTED
            desc_fg = LAVENDER if active else "#9399b2"

        row = tk.Frame(
            inner,
            bg=row_bg,
            highlightthickness=1,
            highlightbackground=row_border,
            bd=0,
        )
        row.pack(
            fill="x",
            padx=self.px(8),
            pady=(0, self.px(7)),
            ipady=self.px(5),
        )

        if locked:
            icon_text = "\uf456"
            icon_fg = DANGER
        else:
            icon_text = "●" if active else "○"
            icon_fg = ACCENT if active else BORDER

        tk.Label(
            row,
            text=icon_text,
            bg=row_bg,
            fg=icon_fg,
            font=(self.FONT_FAMILY, self.fs(9), "bold"),
            width=1,
        ).pack(side="left", padx=(self.px(8), self.px(4)))

        txt = tk.Frame(row, bg=row_bg)
        txt.pack(side="left", fill="x", expand=True, padx=(0, self.px(8)))

        tk.Label(
            txt,
            text=label,
            bg=row_bg,
            fg=row_fg,
            anchor="w",
            font=(self.FONT_FAMILY, self.fs(9), "bold"),
        ).pack(anchor="w", fill="x")

        tk.Label(
            txt,
            text=desc,
            bg=row_bg,
            fg=desc_fg,
            anchor="w",
            font=(self.FONT_FAMILY, self.fs(8)),
        ).pack(anchor="w", fill="x", pady=(self.px(2), 0))

        _uavedusim_bind_click_recursive(
            row,
            lambda cmd=command: _uavedusim_nav_call(self, cmd),
        )

    hint = tk.Label(
        inner,
        text="Clique em uma página para navegar",
        bg=BG,
        fg=BORDER,
        anchor="w",
        font=(self.FONT_FAMILY, self.fs(8)),
    )
    hint.pack(fill="x", padx=self.px(10), pady=(0, self.px(10)))

    self._titlebar_nav_menu = outer

    outer.update_idletasks()
    menu_h = outer.winfo_reqheight()
    outer.place_configure(height=menu_h)

    return "break"


def _uavedusim_install_titlebar_nav(self):
    widgets = [
        getattr(self, "logo_icon", None),
        getattr(self, "logo_lbl", None),
    ]

    for widget in widgets:
        if widget is None:
            continue

        try:
            widget.configure(cursor="hand2")
        except Exception:
            pass

        try:
            widget.bind("<Button-1>", lambda event, app=self: _uavedusim_titlebar_nav_toggle(app, event))
            widget.bind("<B1-Motion>", lambda _event: None)
        except Exception:
            pass

    try:
        if hasattr(self, "logo_icon"):
            self.logo_icon.configure(text="☰", fg=ACCENT)
        if hasattr(self, "logo_lbl"):
            self.logo_lbl.configure(text="UAVEDUSIM", fg=ACCENT)
    except Exception:
        pass


def _uavedusim_build_shell_with_nav(self):
    _UAVEDUSIM_ORIGINAL_BUILD_SHELL_FOR_NAV(self)
    _uavedusim_install_titlebar_nav(self)


def _uavedusim_on_escape_with_nav(self, event=None):
    if getattr(self, "_titlebar_nav_menu", None) is not None:
        _uavedusim_nav_close(self)
        return

    return _UAVEDUSIM_ORIGINAL_ON_ESCAPE_FOR_NAV(self, event)


UAVEDUSIMApp._build_shell = _uavedusim_build_shell_with_nav
UAVEDUSIMApp.on_escape = _uavedusim_on_escape_with_nav
# END UAVEDUSIM_TITLEBAR_NAV_MENU_PATCH






# BEGIN UAVEDUSIM_SIMULATION_START_GUARD_PATCH
# Bloqueia a inicialização de uma nova simulação quando já existe
# PX4/Gazebo em execução ou quando a simulação ainda está abrindo.
# O aviso é exibido dentro da própria janela, sem notificação do sistema.

_UAVEDUSIM_START_GUARD_ORIGINAL_START_FLOW = UAVEDUSIMApp.start_uavedusim_flow


def _uavedusim_load_quiz_progress():
    try:
        if not QUIZ_PROGRESS_FILE.exists():
            return {}
        data = json.loads(QUIZ_PROGRESS_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception as e:
        log(f"Aviso: falha ao ler progresso do quiz ({QUIZ_PROGRESS_FILE}): {e}")
        return {}


def _uavedusim_save_quiz_progress(passed, correct_count, total_q):
    try:
        QUIZ_PROGRESS_FILE.parent.mkdir(parents=True, exist_ok=True)
        data = {}
        try:
            if QUIZ_PROGRESS_FILE.exists():
                existing = json.loads(QUIZ_PROGRESS_FILE.read_text(encoding="utf-8"))
                if isinstance(existing, dict):
                    data = existing
        except Exception:
            data = {}
        data.update({
            "quiz_passed": bool(passed),
            "last_score": correct_count,
            "last_total": total_q,
            "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        })
        QUIZ_PROGRESS_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except Exception as e:
        log(f"Aviso: falha ao salvar progresso do quiz ({QUIZ_PROGRESS_FILE}): {e}")


def _uavedusim_quiz_gate_ok(self):
    if not getattr(self, "_quiz_passed", False):
        progress = _uavedusim_load_quiz_progress()
        if progress.get("quiz_passed"):
            self._quiz_passed = True

    return bool(getattr(self, "_quiz_passed", False))


def _uavedusim_quiz_gate_message():
    return _uavedusim_L(
        (
            f"Para começar a simular, precisamos garantir que você esteja preparado! "
            f"Clique em \"O que é jamming? Aprenda e desafie-se no quiz\", leia o "
            f"conteúdo explicativo e responda ao quiz. Acertando pelo menos "
            f"{QUIZ_MIN_CORRECT} de 5 perguntas, você desbloqueia a simulação. "
            f"Vamos lá?"
        ),
        (
            f"Before you start simulating, let's make sure you're ready! "
            f"Click \"What is jamming? Learn and test yourself in the quiz\", read the "
            f"explanatory content and take the quiz. Getting at least "
            f"{QUIZ_MIN_CORRECT} out of 5 questions right unlocks the simulation. "
            f"Ready?"
        ),
    )


def _uavedusim_simulation_start_block_reason(self):
    if not _uavedusim_quiz_gate_ok(self):
        return _uavedusim_quiz_gate_message()

    page = str(getattr(self, "page", "") or "")

    if page == "wait":
        return "A simulação já está iniciando. Aguarde o PX4/Gazebo ficar pronto ou encerre os processos antes de iniciar outra."

    if page == "attack":
        return "A simulação já está em andamento. Encerre a simulação atual antes de iniciar outra."

    if getattr(self, "restart_waiting_for_cleanup", False):
        return "A simulação está reiniciando. Aguarde a limpeza e a nova inicialização terminarem."

    if getattr(self, "_uavedusim_start_guard_busy", False):
        return "A inicialização da simulação já foi solicitada. Aguarde alguns instantes."

    try:
        remaining = self.simulation_process_lines()
    except Exception:
        remaining = []

    if remaining:
        return "Já existe uma simulação em andamento. Use Encerrar processos antes de iniciar outra."

    try:
        px4_ok, _px4_reason = process_indicates_px4_running()
    except Exception:
        px4_ok = False

    if px4_ok:
        return "PX4 já está em execução. Encerre a simulação atual antes de iniciar outra."

    try:
        gazebo_ok, _gazebo_reason = process_indicates_gazebo_ready()
    except Exception:
        gazebo_ok = False

    if gazebo_ok:
        return "Gazebo já está aberto. Encerre a simulação atual antes de iniciar outra."

    return ""


def _uavedusim_destroy_window_banner(self):
    banner = getattr(self, "_uavedusim_window_banner", None)

    if banner is not None:
        try:
            banner.destroy()
        except Exception:
            pass

    self._uavedusim_window_banner = None



def _uavedusim_show_start_block_feedback(self, reason):
    reason = str(reason or "Já existe uma simulação em andamento.")
    log("Inicialização bloqueada: " + reason)

    # Aviso aparece apenas dentro da janela (sem notificação do sistema).
    _uavedusim_show_window_banner(self, reason, level="warn")

    if hasattr(self, "_set_attack_status_ui"):
        try:
            self._set_attack_status_ui(reason, level="warn")
        except Exception:
            pass

    if hasattr(self, "status_lbl"):
        try:
            self.status_lbl.configure(text=reason, fg=WARN)
        except Exception:
            pass


def _uavedusim_start_uavedusim_flow_guarded(self):
    reason = _uavedusim_simulation_start_block_reason(self)

    if reason:
        _uavedusim_show_start_block_feedback(self, reason)
        return

    self._uavedusim_start_guard_busy = True

    try:
        return _UAVEDUSIM_START_GUARD_ORIGINAL_START_FLOW(self)
    finally:
        try:
            self.root.after(900, lambda: setattr(self, "_uavedusim_start_guard_busy", False))
        except Exception:
            self._uavedusim_start_guard_busy = False


UAVEDUSIMApp.start_uavedusim_flow = _uavedusim_start_uavedusim_flow_guarded
# END UAVEDUSIM_SIMULATION_START_GUARD_PATCH






# BEGIN UAVEDUSIM_SIM_ALERT_HOME_RED_STYLE_PATCH
# Deixa o aviso interno de simulação em andamento com as MESMAS cores
# e fonte do alerta de manutenção da tela inicial.

def _uavedusim_show_window_banner(self, message, level="warn"):
    _uavedusim_destroy_window_banner(self)

    try:
        self.root.update_idletasks()
    except Exception:
        pass

    root = self.root
    root_w = max(root.winfo_width(), self.px(420))

    # Exatamente o mesmo esquema do alerta da tela inicial.
    alert_accent = DANGER
    alert_bg = _uavedusim_blend(BG, alert_accent, 0.16)
    alert_border = _uavedusim_blend(BG, alert_accent, 0.40)

    banner_w = min(max(self.px(360), root_w - self.px(48)), self.px(760))

    try:
        titlebar_h = self.titlebar.winfo_height()
    except Exception:
        titlebar_h = self.px(42)

    outer = tk.Frame(
        root,
        bg=alert_border,
        bd=0,
        highlightthickness=1,
        highlightbackground=alert_border,
    )

    inner = tk.Frame(
        outer,
        bg=alert_bg,
        bd=0,
        highlightthickness=0,
    )
    inner.pack(fill="both", expand=True, padx=1, pady=1)

    tk.Label(
        inner,
        text=ICONS["ataque"],
        bg=alert_bg,
        fg=alert_accent,
        font=(self.FONT_FAMILY, self.fs(13)),
    ).pack(side="left", anchor="n", padx=(self.px(14), self.px(8)), pady=self.px(12))

    tk.Label(
        inner,
        text=message,
        bg=alert_bg,
        fg=alert_accent,
        justify="left",
        anchor="nw",
        wraplength=banner_w - self.px(90),
        font=(self.FONT_FAMILY, self.fs(11.5)),
    ).pack(side="left", fill="x", expand=True, pady=self.px(12))

    close = tk.Label(
        inner,
        text="×",
        bg=alert_bg,
        fg=alert_accent,
        cursor="hand2",
        font=(self.FONT_FAMILY, self.fs(13)),
    )
    close.pack(side="right", padx=(self.px(8), self.px(14)), pady=self.px(10), anchor="n")
    close.bind("<Button-1>", lambda _e: _uavedusim_destroy_window_banner(self))

    outer.update_idletasks()
    banner_h = outer.winfo_reqheight()

    x = max(self.px(12), (root_w - banner_w) // 2)
    y = titlebar_h + self.px(10)

    outer.place(x=x, y=y, width=banner_w, height=banner_h)
    outer.lift()

    self._uavedusim_window_banner = outer

    try:
        if getattr(self, "_uavedusim_window_banner_after_id", None):
            self.root.after_cancel(self._uavedusim_window_banner_after_id)
    except Exception:
        pass

    self._uavedusim_window_banner_after_id = self.root.after(
        4500,
        lambda: _uavedusim_destroy_window_banner(self),
    )
# END UAVEDUSIM_SIM_ALERT_HOME_RED_STYLE_PATCH



# BEGIN UAVEDUSIM_STRONG_HOME_CLEANUP_PATCH
# Faz o botão "Encerrar processos" realmente fechar PX4/Gazebo/QGroundControl
# e também os terminais abertos pelo UAVEDUSIM.


def _uavedusim_pkill_patterns(patterns, signal_name="TERM"):
    for pattern in patterns:
        try:
            subprocess.run(
                ["pkill", f"-{signal_name}", "-f", pattern],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except Exception:
            pass


def _uavedusim_force_cleanup_processes(self):
    patterns = [
        # PX4 / make / terminais de simulação
        r"make\s+px4_sitl",
        r"px4_sitl_default/bin/px4",
        r"PX4-Autopilot/build/.*/bin/px4",
        r"(^|[/\s])px4(\s|$)",
        r"PX4 SITL",
        r"uavedusim_cmd_.*\.sh",

        # Gazebo / Ignition / agentes
        r"\bgz\s+sim\b",
        r"\bgzserver\b",
        r"\bgzclient\b",
        r"\bgz-gui\b",
        r"\bgz\s+gui\b",
        r"\bign\s+gazebo\b",
        r"\bMicroXRCEAgent\b",

        # QGroundControl em AppImage, extraído ou processo interno
        r"QGroundControl",
        r"QGroundControl-x86_64\.AppImage",
        r"squashfs-root/usr/bin/QGroundControl",
        r"/tmp/\.mount_.*QGround",
        r"AppRun.*QGroundControl",
    ]

    log("Encerrando terminais rastreados pelo UAVEDUSIM.")
    try:
        self.kill_tracked_terminals()
    except Exception as e:
        log(f"Aviso: falha ao encerrar terminais rastreados: {e}")

    log("Enviando TERM para PX4/Gazebo/QGroundControl.")
    _uavedusim_pkill_patterns(patterns, "TERM")

    try:
        subprocess.run(
            ["pkill", "-TERM", "-x", "px4"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception:
        pass

    time.sleep(1.0)

    log("Enviando KILL para processos restantes de PX4/Gazebo/QGroundControl.")
    _uavedusim_pkill_patterns(patterns, "KILL")

    try:
        subprocess.run(
            ["pkill", "-KILL", "-x", "px4"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception:
        pass

    try:
        self.px4_console_fifo.unlink(missing_ok=True)
    except TypeError:
        try:
            if self.px4_console_fifo.exists():
                self.px4_console_fifo.unlink()
        except Exception:
            pass
    except Exception:
        pass

    try:
        PID_FILE.write_text("", encoding="utf-8")
    except Exception:
        pass

    self.takeoff_sent = False
    self.takeoff_waiting = False
    self.destination_release_pending = False
    self.destination_release_until = 0.0
    self.destination_sent = False
    self.active_attack_key = None
    self.jamming_active = False
    self.attack_status = ""
    self.status_log_entries = []


def _uavedusim_home_cleanup_feedback(self, message, ok=True):
    try:
        if callable(globals().get("_uavedusim_show_window_banner")):
            _uavedusim_show_window_banner(self, message, level=("warn" if ok else "danger"))
            return
    except Exception:
        pass

    try:
        old_badge = self.badge.cget("text")
        self.set_badge("PROCESSOS ENCERRADOS" if ok else "ERRO")
        self.root.after(2200, lambda: self.badge.configure(text=old_badge))
    except Exception:
        pass


def _uavedusim_run_cleanup_strong(self):
    log("Limpeza forte solicitada pela interface.")

    try:
        _uavedusim_home_cleanup_feedback(
            self,
            "Encerrando PX4, Gazebo, QGroundControl e terminais da simulação...",
            ok=True,
        )
    except Exception:
        pass

    def worker():
        success = True
        error_msg = ""

        try:
            _uavedusim_force_cleanup_processes(self)
        except Exception as e:
            success = False
            error_msg = str(e)
            log(f"Erro durante limpeza forte: {e}")

        def finish():
            if success:
                _uavedusim_home_cleanup_feedback(
                    self,
                    "Processos encerrados: PX4, Gazebo, QGroundControl e terminais da simulação.",
                    ok=True,
                )
            else:
                _uavedusim_home_cleanup_feedback(
                    self,
                    _uavedusim_L("Falha ao encerrar todos os processos. Verifique o terminal/log. ", "Failed to stop all processes. Check the terminal/log. ") + error_msg,
                    ok=False,
                )

        try:
            self.root.after(0, finish)
        except Exception:
            pass

    threading.Thread(target=worker, daemon=True).start()


UAVEDUSIMApp.run_cleanup = _uavedusim_run_cleanup_strong
# END UAVEDUSIM_STRONG_HOME_CLEANUP_PATCH






# BEGIN UAVEDUSIM_ATTACK_BUTTONS_AND_NAV_VISIBILITY_PATCH
# 1) Move os botões "Enviar commander takeoff" e "Enviar destino" um pouco
#    para a esquerda na tela de ataques, evitando corte em certas resoluções.
# 2) Oculta "Iniciar simulação" do menu quando PX4/Gazebo já estão iniciando
#    ou em execução.

def _uavedusim_simulation_active_for_nav(self):
    page = str(getattr(self, "page", "") or "")

    if page in ("wait", "attack"):
        return True

    if getattr(self, "_uavedusim_start_guard_busy", False):
        return True

    if getattr(self, "restart_waiting_for_cleanup", False):
        return True

    try:
        if self.simulation_process_lines():
            return True
    except Exception:
        pass

    try:
        px4_ok, _ = process_indicates_px4_running()
    except Exception:
        px4_ok = False

    try:
        gazebo_ok, _ = process_indicates_gazebo_ready()
    except Exception:
        gazebo_ok = False

    return bool(px4_ok or gazebo_ok)


def _uavedusim_runtime_attack_screen_available(self):
    if str(getattr(self, "page", "") or "") == "attack":
        return True

    try:
        px4_ok, _px4_reason = process_indicates_px4_running()
    except Exception:
        px4_ok = False

    try:
        gazebo_ok, _gazebo_reason = process_indicates_gazebo_ready()
    except Exception:
        gazebo_ok = False

    return bool(px4_ok and gazebo_ok)


def _uavedusim_open_runtime_attack_screen(self):
    if not _uavedusim_runtime_attack_screen_available(self):
        try:
            _uavedusim_show_window_banner(
                self,
                "O painel de ataques só fica disponível quando PX4 e Gazebo estão em execução.",
                level="warn",
            )
        except Exception:
            pass
        return

    try:
        self.show_attack_menu()
    except Exception as e:
        log(f"Erro ao abrir painel de ataques pelo menu: {e}")
        try:
            _uavedusim_show_window_banner(
                self,
                "Não foi possível abrir o painel de ataques. Verifique se a simulação ainda está ativa.",
                level="warn",
            )
        except Exception:
            pass


def _uavedusim_nav_items(self):
    items = [
        (
            "home",
            "Início",
            "Tela inicial do UAVEDUSIM",
            self.show_main_menu,
        ),
        (
            "jamming_info",
            "O que é jamming?",
            "Conceito geral de jamming",
            _uavedusim_nav_safe_global(self, "_uavedusim_show_jamming_info_screen", self.show_main_menu),
        ),
    ]

    if callable(globals().get("_uavedusim_show_jamming_attacks_screen")):
        items.append(
            (
                "jamming_attacks_info",
                "Ataques de Jamming Simulados",
                "Noise, Blackout e Pulse",
                lambda: _uavedusim_show_jamming_attacks_screen(self),
            )
        )

    if callable(globals().get("_uavedusim_show_mitigation_screen")):
        items.append(
            (
                "mitigation_info",
                "Mitigações do Simulador",
                "Estratégias contra os ataques",
                lambda: _uavedusim_show_mitigation_screen(self),
            )
        )

    if callable(globals().get("_uavedusim_show_quiz_screen")):
        items.append(
            (
                "quiz_info",
                "Quiz: jamming e mitigação",
                "5 perguntas de múltipla escolha",
                lambda: _uavedusim_show_quiz_screen(self),
            )
        )

    if callable(globals().get("_uavedusim_show_tutorial_screen")):
        tutorial_unlocked = _uavedusim_quiz_gate_ok(self)
        items.append(
            (
                "tutorial_info",
                "Como usar o simulador",
                "Passo a passo do painel de ataques",
                (
                    (lambda: _uavedusim_show_tutorial_screen(self)) if tutorial_unlocked
                    else (lambda: _uavedusim_show_window_banner(self, _uavedusim_quiz_gate_message(), level="warn"))
                ),
            )
        )

    if _uavedusim_runtime_attack_screen_available(self):
        items.append(
            (
                "attack_runtime",
                "Painel de Ataques",
                "Lançar ataques na simulação atual",
                lambda: _uavedusim_open_runtime_attack_screen(self),
            )
        )

    # Só mostra "Iniciar simulação" quando não existe simulação iniciando
    # nem PX4/Gazebo em execução.
    if not _uavedusim_simulation_active_for_nav(self):
        items.append(
            (
                "simulation_flow",
                "Iniciar simulação",
                "Selecionar modelo e mundo",
                self.start_uavedusim_flow,
            )
        )

    items.append(
        (
            "language",
            "Idioma / Language",
            "Português / English",
            lambda: _uavedusim_show_language_popup(self),
        )
    )

    return items


def _uavedusim_nav_current_key(self):
    page = str(getattr(self, "page", "") or "")

    if page in ("main", "home"):
        return "home"

    if page == "jamming_info":
        return "jamming_info"

    if page == "jamming_attacks_info":
        return "jamming_attacks_info"

    if page == "mitigation_info":
        return "mitigation_info"

    if page == "quiz_info":
        return "quiz_info"

    if page == "tutorial_info":
        return "tutorial_info"

    if page == "attack":
        return "attack_runtime"

    if page in ("menu", "wait"):
        return "simulation_flow"

    return ""


def _uavedusim_reflow_prereq_layout(self, event=None):
    if self.page != "attack" or not hasattr(self, "prereq_panel"):
        return

    width = self.prereq_panel.winfo_width() or self.content.winfo_width() or self.root.winfo_width()
    compact_panel = width < self.px(760)
    very_compact = width < self.px(520)

    # REMOVIDO o loop de grid_forget() do painel de destino/decolagem!

    for col in range(3):
        try:
            self.prereq_panel.grid_columnconfigure(col, weight=0, minsize=0)
        except Exception:
            pass

    if compact_panel:
        self.prereq_panel.grid_columnconfigure(0, weight=1)

        self.takeoff_box.grid(
            row=0, column=0, sticky="ew", padx=self.px(12), pady=(self.px(8), self.px(4)),
        )
        self.destination_box.grid(
            row=1, column=0, sticky="ew", padx=self.px(12), pady=(self.px(4), self.px(4)),
        )
        self.prereq_buttons.grid(
            row=2, column=0, sticky="ew", padx=self.px(12), pady=(self.px(4), self.px(8)),
        )
    else:
        self.prereq_panel.grid_columnconfigure(0, weight=1, minsize=self.px(205))
        self.prereq_panel.grid_columnconfigure(1, weight=3, minsize=self.px(395))
        self.prereq_panel.grid_columnconfigure(2, weight=0, minsize=self.px(205))

        self.takeoff_box.grid(
            row=0, column=0, sticky="nsew", padx=(self.px(12), self.px(8)), pady=self.px(8),
        )
        self.destination_box.grid(
            row=0, column=1, sticky="nsew", padx=(self.px(8), self.px(4)), pady=self.px(8),
        )
        self.prereq_buttons.grid(
            row=0, column=2, sticky="w", padx=(self.px(0), self.px(8)), pady=self.px(8),
        )

    # Empacota os botões na vertical apemas uma vez, eliminando o piscar lateral
    if not getattr(self, "_prereq_buttons_packed", False):
        self._pack_button_column(
            self.prereq_buttons,
            vertical=True,
            fill_x=True,
        )
        self._prereq_buttons_packed = True

    self._config_prereq_button_texts(
        compact=compact_panel,
        very_compact=very_compact,
    )

    for box_attr, label_attr in [
        ("takeoff_box", "takeoff_status_label"),
        ("destination_box", "destination_status_label"),
    ]:
        if not hasattr(self, box_attr) or not hasattr(self, label_attr):
            continue

        box = getattr(self, box_attr)
        label = getattr(self, label_attr)

        box_width = box.winfo_width()
        if box_width <= 1:
            box_width = max(self.px(180), width // (1 if compact_panel else 3))

        try:
            label.configure(wraplength=max(self.px(120), box_width - self.px(14)))
        except Exception:
            pass

    destination_width = 0
    if hasattr(self, "destination_box"):
        try:
            destination_width = self.destination_box.winfo_width()
        except Exception:
            destination_width = 0

    if destination_width <= 1:
        destination_width = width if compact_panel else max(self.px(390), int(width * 0.40))

    self._layout_destination_fields(
        stack=(compact_panel or destination_width < self.px(390)),
    )

    if hasattr(self, "attack_scroll_canvas"):
        self.root.after(
            40,
            lambda: self.attack_scroll_canvas.configure(
                scrollregion=self.attack_scroll_canvas.bbox("all")
            ),
        )


UAVEDUSIMApp._reflow_prereq_layout = _uavedusim_reflow_prereq_layout
# END UAVEDUSIM_ATTACK_BUTTONS_AND_NAV_VISIBILITY_PATCH









# BEGIN UAVEDUSIM_SELECT_HEIGHT_AND_PT_DESCRIPTIONS_PATCH
# Mantém o botão "Selecionar" preenchido em mauve/ACCENT, com texto preto,
# mas com a mesma altura visual do botão "Fechar".

_UAVEDUSIM_ORIGINAL_SETUP_STYLES_SELECT_HEIGHT_PT = UAVEDUSIMApp._setup_styles


def _uavedusim_setup_styles_select_height_pt(self):
    _UAVEDUSIM_ORIGINAL_SETUP_STYLES_SELECT_HEIGHT_PT(self)

    try:
        common_padding = (self.px(11), self.px(7))

        self.style.configure(
            "Cancel.TButton",
            font=(self.FONT_FAMILY, self.FS_BUTTON, "bold"),
            padding=common_padding,
            background=DANGER,
            foreground="#11111b",
            relief="flat",
            borderwidth=0,
        )
        self.style.map(
            "Cancel.TButton",
            background=[
                ("disabled", DISABLED_BG),
                ("active", _uavedusim_blend(DANGER, "#000000", 0.18)),
                ("pressed", _uavedusim_blend(DANGER, "#000000", 0.18)),
            ],
            foreground=[
                ("disabled", DISABLED_FG),
                ("active", "#11111b"),
                ("pressed", "#11111b"),
            ],
        )

        self.style.configure(
            "Primary.TButton",
            font=(self.FONT_FAMILY, self.FS_BUTTON, "bold"),
            padding=common_padding,
            background=ACCENT,
            foreground="#11111b",
            relief="flat",
            borderwidth=0,
        )

        self.style.map(
            "Primary.TButton",
            background=[
                ("disabled", DISABLED_BG),
                ("active", ACCENT_DIM),
                ("pressed", ACCENT_DIM),
            ],
            foreground=[
                ("disabled", DISABLED_FG),
                ("active", "#11111b"),
                ("pressed", "#11111b"),
            ],
        )
    except Exception:
        pass


UAVEDUSIMApp._setup_styles = _uavedusim_setup_styles_select_height_pt
# END UAVEDUSIM_SELECT_HEIGHT_AND_PT_DESCRIPTIONS_PATCH



# BEGIN UAVEDUSIM_BACK_FILLED_BUTTON_RESTORE_PATCH
# Restaura o botão "Voltar" como botão preenchido cinza, no estilo antigo:
# fundo SURFACE2, texto MUTED, hover BORDER/TEXT e seta menor "←".

_UAVEDUSIM_ORIGINAL_SETUP_STYLES_BACK_FILLED_RESTORE = UAVEDUSIMApp._setup_styles


def _uavedusim_setup_styles_back_filled_restore(self):
    _UAVEDUSIM_ORIGINAL_SETUP_STYLES_BACK_FILLED_RESTORE(self)

    try:
        self.style.configure(
            "Back.TButton",
            font=(self.FONT_FAMILY, self.FS_BUTTON),
            padding=(self.px(8), self.px(5)),
            background=SURFACE2,
            foreground=MUTED,
            relief="flat",
            borderwidth=0,
        )
        self.style.map(
            "Back.TButton",
            background=[
                ("disabled", DISABLED_BG),
                ("active", BORDER),
                ("pressed", BORDER),
            ],
            foreground=[
                ("disabled", DISABLED_FG),
                ("active", TEXT),
                ("pressed", TEXT),
            ],
        )
    except Exception:
        pass


UAVEDUSIMApp._setup_styles = _uavedusim_setup_styles_back_filled_restore
# END UAVEDUSIM_BACK_FILLED_BUTTON_RESTORE_PATCH









# BEGIN UAVEDUSIM_BACK_ICON_COMPACT_PATCH
# Botão Voltar compacto: apenas o ícone, fonte maior e largura menor.

_UAVEDUSIM_ORIGINAL_SETUP_STYLES_BACK_ICON_COMPACT = UAVEDUSIMApp._setup_styles


def _uavedusim_setup_styles_back_icon_compact(self):
    _UAVEDUSIM_ORIGINAL_SETUP_STYLES_BACK_ICON_COMPACT(self)

    try:
        self.style.configure(
            "Back.TButton",
            font=(self.FONT_FAMILY, self.fs(21), "bold"),
            padding=(self.px(4), self.px(1)),
            background=SURFACE2,
            foreground=MUTED,
            relief="flat",
            borderwidth=0,
            anchor="center",
        )
        self.style.map(
            "Back.TButton",
            background=[
                ("disabled", DISABLED_BG),
                ("active", BORDER),
                ("pressed", BORDER),
            ],
            foreground=[
                ("disabled", DISABLED_FG),
                ("active", TEXT),
                ("pressed", TEXT),
            ],
        )
    except Exception:
        pass


UAVEDUSIMApp._setup_styles = _uavedusim_setup_styles_back_icon_compact
# END UAVEDUSIM_BACK_ICON_COMPACT_PATCH






# BEGIN UAVEDUSIM_JAMMING_CANVAS_INFO_SCREEN_PATCH
# Tela "O que é jamming?" em Canvas responsivo, seguindo o layout de referência:
# coluna esquerda de conceitos, diagrama central e coluna direita de impactos.

import textwrap as _uj_textwrap
import tkinter.font as _uj_tkfont


def _uj_clamp(value, low, high):
    return max(low, min(value, high))


def _uj_font(app, size, weight="normal"):
    # Usa a mesma família e a mesma escala responsiva usada no restante da interface.
    try:
        resolved = app.fs(size)
    except Exception:
        resolved = int(round(size))
    return (app.FONT_FAMILY, max(10, int(round(resolved))), weight)

def _uj_rr(canvas, x1, y1, x2, y2, radius, fill, outline="", width=1, tags=None):
    radius = max(1, min(radius, (x2 - x1) / 2, (y2 - y1) / 2))
    pts = _uavedusim_round_rect_points(x1, y1, x2, y2, radius)
    return canvas.create_polygon(
        pts,
        fill=fill,
        outline=outline,
        width=width,
        smooth=True,
        splinesteps=16,
        tags=tags,
    )


def _uj_wrap_lines(text, font, width):
    # Quebra por largura real em pixels, próxima do comportamento nativo do Canvas.
    # Isso evita quebras artificiais por quantidade fixa de caracteres.
    try:
        tk_font = _uj_tkfont.Font(font=font)
        max_w = max(20, int(width))
    except Exception:
        return _uj_textwrap.wrap(
            str(text),
            width=max(20, int(width / 7)),
            break_long_words=False,
        )

    lines = []

    for paragraph in str(text).splitlines() or [""]:
        paragraph = paragraph.strip()

        if not paragraph:
            lines.append("")
            continue

        current = ""

        for word in paragraph.split():
            trial = word if not current else f"{current} {word}"

            if tk_font.measure(trial) <= max_w or not current:
                current = trial
                continue

            lines.append(current)
            current = word

            # Fallback para tokens muito longos sem espaço.
            if tk_font.measure(current) > max_w:
                piece = ""

                for ch in word:
                    trial_piece = piece + ch

                    if tk_font.measure(trial_piece) <= max_w or not piece:
                        piece = trial_piece
                    else:
                        lines.append(piece)
                        piece = ch

                current = piece

        if current:
            lines.append(current)

    return lines

def _uj_text(canvas, x, y, text, font, fill, width=None, anchor="nw", justify="left", tags=None):
    # O texto é passado cru para o Canvas; quem quebra visualmente é o Tk via width.
    clean = str(text).strip()

    if width is None:
        return canvas.create_text(
            x,
            y,
            text=clean,
            font=font,
            fill=fill,
            anchor=anchor,
            justify=justify,
            tags=tags,
        )

    return canvas.create_text(
        x,
        y,
        text=clean,
        font=font,
        fill=fill,
        width=max(1, int(width)),
        anchor=anchor,
        justify=justify,
        tags=tags,
    )

# Códigos (Private Use Area) de ícones do Nerd Fonts confirmados como
# disponíveis na fonte do sistema. Só entram aqui os que foram testados
# e realmente renderizam — os demais kinds continuam desenhados em
# vetor (fallback seguro, sem depender de nenhuma fonte instalada).
def _uavedusim_preferred_nerd_font_family(app=None):
    env_family = os.environ.get("UAVEDUSIM_NERD_FONT_FAMILY")

    if env_family:
        return env_family

    candidates = (
        "Symbols Nerd Font Mono",
        "Symbols Nerd Font",
        "JetBrainsMono Nerd Font",
        "JetBrainsMonoNL Nerd Font",
        "CaskaydiaCove Nerd Font",
        "CaskaydiaMono Nerd Font",
        "FiraCode Nerd Font",
        "Hack Nerd Font",
        "MesloLGS NF",
        "monospace",
    )

    try:
        import tkinter.font as _uavedusim_tkfont
        families = set(_uavedusim_tkfont.families())

        for family in candidates:
            if family in families:
                return family

        for family in sorted(families):
            if "Nerd Font" in family or family.endswith(" NF"):
                return family
    except Exception:
        pass

    return getattr(app, "FONT_FAMILY", "monospace")


_UAVEDUSIM_NERD_ICONS = {
    "target": "\U000F04FE",
    "antenna": "\U000F1119",
    "shield": "\U000F0499",
    "monitor": "\U000F0379",
    "blackout": "\U000F00AD",
    "info": "\ue66a",
    "pin": "\uf041",
    "person": "\uf415",
    "noise": "\ueb34",
    "pulse": "\ueb31",
}


def _uj_icon(canvas, kind, cx, cy, size, color, width=2, app=None):
    s = size
    width = max(1, int(width))

    glyph = _UAVEDUSIM_NERD_ICONS.get(kind)
    if glyph:
        font_family = _uavedusim_preferred_nerd_font_family(app)
        item = canvas.create_text(
            cx,
            cy,
            text=glyph,
            fill=color,
            font=(font_family, int(s * .82)),
            anchor="center",
        )
        return item

    if kind == "info":
        canvas.create_oval(cx - s / 2, cy - s / 2, cx + s / 2, cy + s / 2, outline=color, width=width)
        canvas.create_text(cx, cy + s * 0.03, text="i", fill=color, font=("TkDefaultFont", int(s * 0.70), "bold"))

    elif kind == "target":
        canvas.create_oval(cx - s * .34, cy - s * .34, cx + s * .34, cy + s * .34, outline=color, width=width)
        canvas.create_oval(cx - s * .06, cy - s * .06, cx + s * .06, cy + s * .06, outline=color, width=width)
        canvas.create_line(cx - s * .52, cy, cx - s * .20, cy, fill=color, width=width, capstyle="round", joinstyle="round")
        canvas.create_line(cx + s * .20, cy, cx + s * .52, cy, fill=color, width=width, capstyle="round", joinstyle="round")
        canvas.create_line(cx, cy - s * .52, cx, cy - s * .20, fill=color, width=width, capstyle="round", joinstyle="round")
        canvas.create_line(cx, cy + s * .20, cx, cy + s * .52, fill=color, width=width, capstyle="round", joinstyle="round")

    elif kind == "antenna":
        canvas.create_line(cx, cy + s * .42, cx, cy - s * .16, fill=color, width=width, capstyle="round", joinstyle="round")
        canvas.create_polygon(cx, cy - s * .24, cx - s * .18, cy + s * .42, cx + s * .18, cy + s * .42, outline=color, fill="", width=width, joinstyle="round")
        for r in (.28, .44):
            canvas.create_arc(cx - s * r, cy - s * r, cx + s * r, cy + s * r, start=35, extent=110, outline=color, width=width, style="arc")
            canvas.create_arc(cx - s * r, cy - s * r, cx + s * r, cy + s * r, start=215, extent=110, outline=color, width=width, style="arc")

    elif kind in ("shield", "shield_x"):
        pts = [
            cx, cy - s * .48, cx + s * .35, cy - s * .28, cx + s * .28, cy + s * .22,
            cx, cy + s * .48, cx - s * .28, cy + s * .22, cx - s * .35, cy - s * .28,
        ]
        canvas.create_polygon(pts, outline=color, fill="", width=width, smooth=True, joinstyle="round")

        if kind == "shield":
            canvas.create_line(cx - s * .14, cy, cx + s * .14, cy, fill=color, width=width, capstyle="round", joinstyle="round")
            canvas.create_line(cx, cy - s * .14, cx, cy + s * .14, fill=color, width=width, capstyle="round", joinstyle="round")
        else:
            canvas.create_line(cx - s * .13, cy - s * .13, cx + s * .13, cy + s * .13, fill=color, width=width, capstyle="round", joinstyle="round")
            canvas.create_line(cx + s * .13, cy - s * .13, cx - s * .13, cy + s * .13, fill=color, width=width, capstyle="round", joinstyle="round")

    elif kind == "pin":
        canvas.create_oval(cx - s * .34, cy - s * .46, cx + s * .34, cy + s * .22, outline=color, width=width)
        canvas.create_polygon(cx - s * .18, cy + s * .10, cx + s * .18, cy + s * .10, cx, cy + s * .52, outline=color, fill="", width=width, joinstyle="round")
        canvas.create_oval(cx - s * .08, cy - s * .24, cx + s * .08, cy - s * .08, outline=color, width=width)

    elif kind == "person":
        canvas.create_oval(cx - s * .18, cy - s * .45, cx + s * .18, cy - s * .10, outline=color, width=width)
        canvas.create_arc(cx - s * .42, cy - s * .02, cx + s * .42, cy + s * .62, start=0, extent=180, outline=color, width=width, style="arc")
        canvas.create_line(cx - s * .42, cy + s * .30, cx + s * .42, cy + s * .30, fill=color, width=width, capstyle="round", joinstyle="round")

    elif kind == "monitor":
        _uj_rr(canvas, cx - s * .42, cy - s * .30, cx + s * .42, cy + s * .20, s * .06, fill="", outline=color, width=width)
        canvas.create_line(cx, cy + s * .20, cx, cy + s * .42, fill=color, width=width, capstyle="round", joinstyle="round")
        canvas.create_line(cx - s * .22, cy + s * .42, cx + s * .22, cy + s * .42, fill=color, width=width, capstyle="round", joinstyle="round")

    elif kind == "noise":
        canvas.create_line(cx, cy + s * .43, cx, cy - s * .25, fill=color, width=width, capstyle="round", joinstyle="round")
        canvas.create_polygon(cx, cy - s * .42, cx - s * .17, cy + s * .48, cx + s * .17, cy + s * .48, outline=color, fill="", width=width, joinstyle="round")
        for r in (.22, .35, .48):
            canvas.create_arc(cx - s * r, cy - s * r, cx + s * r, cy + s * r, start=35, extent=110, outline=color, width=width, style="arc")

    elif kind == "blackout":
        canvas.create_oval(cx - s * .42, cy - s * .42, cx + s * .42, cy + s * .42, outline=color, width=width + 1)
        canvas.create_line(cx - s * .28, cy - s * .28, cx + s * .28, cy + s * .28, fill=color, width=width + 1, capstyle="round", joinstyle="round")

    elif kind == "pulse":
        pts = [
            cx - s * .45, cy + s * .20, cx - s * .10, cy + s * .20, cx - s * .10, cy - s * .35,
            cx + s * .13, cy - s * .35, cx + s * .13, cy + s * .20, cx + s * .45, cy + s * .20,
        ]
        canvas.create_line(*pts, fill=color, width=width + 1, capstyle="round", joinstyle="round")

    elif kind == "wave":
        pts = [
            cx - s * .45, cy, cx - s * .25, cy, cx - s * .18, cy - s * .18,
            cx - s * .08, cy + s * .18, cx, cy - s * .18, cx + s * .10, cy + s * .18,
            cx + s * .18, cy, cx + s * .45, cy,
        ]
        canvas.create_line(*pts, fill=color, width=width, capstyle="round", joinstyle="round")


def _uj_satellite(canvas, cx, cy, scale, color, angle_deg=-35):
    sw = max(1, int(1.4 * scale))
    thin = max(1, int(scale * .7))

    theta = math.radians(angle_deg)
    cos_t, sin_t = math.cos(theta), math.sin(theta)

    def rot(dx, dy):
        # Rotaciona um ponto (dx, dy) em torno do centro do satélite e
        # devolve a coordenada final na tela (cx, cy) + o ponto girado.
        return cx + dx * cos_t - dy * sin_t, cy + dx * sin_t + dy * cos_t

    def rect_pts(local_cx, local_cy, w, h):
        w2, h2 = w / 2, h / 2
        pts = []
        for dx, dy in ((-w2, -h2), (w2, -h2), (w2, h2), (-w2, h2)):
            pts.extend(rot(local_cx + dx, local_cy + dy))
        return pts

    body_w, body_h = 18 * scale, 38 * scale
    panel_w, panel_h = 40 * scale, 20 * scale
    gap = 10 * scale

    # Corpo central (retângulo rotacionado -> desenhado como polígono).
    canvas.create_polygon(rect_pts(0, 0, body_w, body_h), outline=color, fill="", width=sw)

    for side in (-1, 1):
        panel_cx = side * (body_w / 2 + gap + panel_w / 2)

        x1, y1 = rot(side * body_w / 2, 0)
        x2, y2 = rot(side * (body_w / 2 + gap), 0)
        canvas.create_line(x1, y1, x2, y2, fill=color, width=sw, capstyle="round")

        canvas.create_polygon(rect_pts(panel_cx, 0, panel_w, panel_h), outline=color, fill="", width=sw)

        for k in (-1, 1):
            lx = panel_cx + k * panel_w / 6
            x1, y1 = rot(lx, -panel_h / 2)
            x2, y2 = rot(lx, panel_h / 2)
            canvas.create_line(x1, y1, x2, y2, fill=color, width=thin, capstyle="round", joinstyle="round")

        x1, y1 = rot(panel_cx - panel_w / 2, 0)
        x2, y2 = rot(panel_cx + panel_w / 2, 0)
        canvas.create_line(x1, y1, x2, y2, fill=color, width=thin, capstyle="round", joinstyle="round")

    # Mastro.
    mast_len = 13 * scale
    x1, y1 = rot(0, body_h / 2)
    x2, y2 = rot(0, body_h / 2 + mast_len)
    canvas.create_line(x1, y1, x2, y2, fill=color, width=sw, capstyle="round", joinstyle="round")

    # "Prato" em meio-círculo com a ponta cortada (lado reto), como na
    # referência: o lado reto fica na ponta externa (longe do corpo) e
    # a curva encosta no mastro (voltada pra dentro). Um polígono
    # amostrado ao longo da curva fecha sozinho com uma linha reta
    # entre as duas pontas, formando o corte.
    dish_r = 16 * scale
    attach_y = body_h / 2 + mast_len
    dish_center_y = attach_y + dish_r
    dish_pts = []
    steps = 20
    for i in range(steps + 1):
        t = math.pi + math.pi * i / steps
        lx = dish_r * math.cos(t)
        ly = dish_center_y + dish_r * math.sin(t)
        dish_pts.extend(rot(lx, ly))
    canvas.create_polygon(dish_pts, outline=color, fill="", width=sw, smooth=False)


def _uj_drone(canvas, cx, cy, scale, color):
    sw = max(1, int(1.4 * scale))
    body, arm, rotor = 34 * scale, 36 * scale, 15 * scale
    canvas.create_rectangle(cx - body / 2, cy - body / 2, cx + body / 2, cy + body / 2, outline=color, fill="", width=sw)

    for sx, sy in [(-1, -1), (1, -1), (-1, 1), (1, 1)]:
        rx, ry = cx + sx * arm, cy + sy * arm
        canvas.create_line(cx + sx * body / 2, cy + sy * body / 2, rx, ry, fill=color, width=sw, capstyle="round", joinstyle="round")
        canvas.create_oval(rx - rotor, ry - rotor, rx + rotor, ry + rotor, outline=color, width=sw)


def _uj_jammer(canvas, cx, cy, scale, color):
    sw = max(1, int(1.4 * scale))
    bw, bh = 48 * scale, 42 * scale

    _uj_rr(
        canvas,
        cx - bw / 2,
        cy - bh / 2,
        cx + bw / 2,
        cy + bh / 2,
        6 * scale,
        fill="",
        outline=color,
        width=sw,
    )

    canvas.create_line(cx, cy - bh / 2, cx, cy - bh / 2 - 44 * scale, fill=color, width=sw, capstyle="round", joinstyle="round")
    canvas.create_oval(cx - 4 * scale, cy - 5 * scale, cx + 4 * scale, cy + 3 * scale, outline=color, width=sw)

    for dx in (-14, 0, 14):
        canvas.create_oval(
            cx + dx * scale - 2 * scale,
            cy + bh * .26,
            cx + dx * scale + 2 * scale,
            cy + bh * .26 + 4 * scale,
            fill=color,
            outline=color,
        )

    top = cy - bh / 2 - 44 * scale

    for rr in (20, 35, 52, 70):
        canvas.create_arc(
            cx - rr * scale,
            top - rr * scale,
            cx + rr * scale,
            top + rr * scale,
            start=45,
            extent=90,
            outline=color,
            width=sw,
            style="arc",
        )


# BEGIN UAVEDUSIM_DYNAMIC_JAMMING_HEIGHT_HELPERS
def _uj_linespace(app, font):
    try:
        return max(_uj_tkfont.Font(font=font).metrics("linespace"), app.fs(10))
    except Exception:
        return app.fs(10)


def _uj_measure_text_height(app, text, font, width, line_gap=None):
    lines = _uj_wrap_lines(text, font, width)

    if not lines:
        return _uj_linespace(app, font)

    line_h = _uj_linespace(app, font)
    gap = app.px(1) if line_gap is None else line_gap

    # Folga para compensar pequenas diferenças entre a medição e o wrap interno do Canvas.
    return len(lines) * line_h + max(0, len(lines) - 1) * gap + app.px(2)

def _uj_info_panel_metrics(app, w, s, items):
    title_font = _uj_font(app, 11.8 * s, "bold")
    body_font = _uj_font(app, 10.0 * s)

    top_pad = max(app.px(4), int(5 * s))
    body_gap = max(app.px(2), int(3 * s))
    bottom_pad = max(app.px(4), int(5 * s))

    icon_x_offset = max(app.px(26), int(28 * s))
    text_x_offset = max(app.px(48), int(50 * s))
    right_pad = max(app.px(6), int(8 * s))

    text_w = max(app.px(130), w - text_x_offset - right_pad)

    rows = []

    for icon, title, body in items:
        title_h = _uj_measure_text_height(app, title, title_font, text_w)
        body_h = _uj_measure_text_height(app, body, body_font, text_w)

        row_h = top_pad + title_h + body_gap + body_h + bottom_pad

        # Altura mínima com respiro para evitar texto colado nas divisórias.
        row_h = max(row_h, app.px(66))

        rows.append({
            "icon": icon,
            "title": title,
            "body": body,
            "title_font": title_font,
            "body_font": body_font,
            "title_h": title_h,
            "body_h": body_h,
            "row_h": row_h,
            "top_pad": top_pad,
            "body_gap": body_gap,
            "bottom_pad": bottom_pad,
            "icon_x_offset": icon_x_offset,
            "text_x_offset": text_x_offset,
            "text_w": text_w,
        })

    return rows

def _uj_info_panel_required_height(app, w, s, items):
    rows = _uj_info_panel_metrics(app, w, s, items)
    return sum(row["row_h"] for row in rows)


def _uj_attack_card_required_height(app, w, s, title, body):
    title_font = _uj_font(app, 11.0 * s, "bold")
    body_font = _uj_font(app, 9.8 * s)

    text_x_offset = max(app.px(78), int(82 * s))
    right_pad = max(app.px(12), int(14 * s))
    text_w = max(app.px(130), w - text_x_offset - right_pad)

    top_pad = max(app.px(9), int(10 * s))
    gap = max(app.px(4), int(5 * s))
    bottom_pad = max(app.px(9), int(10 * s))

    title_h = _uj_measure_text_height(app, title, title_font, text_w)
    body_h = _uj_measure_text_height(app, body, body_font, text_w)

    return max(app.px(70), top_pad + title_h + gap + body_h + bottom_pad)
# END UAVEDUSIM_DYNAMIC_JAMMING_HEIGHT_HELPERS

def _uj_info_panel(canvas, x, y, w, h, app, s, items):
    _uj_rr(canvas, x, y, x + w, y + h, app.px(10), fill=_uavedusim_blend(BG, SURFACE, .20), outline=BORDER, width=1)

    rows = _uj_info_panel_metrics(app, w, s, items)
    required_h = sum(row["row_h"] for row in rows)
    current_y = y

    for i, row in enumerate(rows):
        row_h = row["row_h"]
        yy = current_y

        if i > 0:
            canvas.create_line(
                x + 12 * s,
                yy,
                x + w - 12 * s,
                yy,
                fill=_uavedusim_blend(SURFACE2, BORDER, .25),
                width=1,
            )

        icon_x = x + row["icon_x_offset"]
        icon_y = yy + row_h / 2
        icon_size = min(30 * s, row_h * .30)
        _uj_icon(canvas, row["icon"], icon_x, icon_y, icon_size, ACCENT, width=max(1, int(2 * s)), app=app)

        tx = x + row["text_x_offset"]
        title_y = yy + row["top_pad"]
        body_y = title_y + row["title_h"] + row["body_gap"]

        _uj_text(
            canvas,
            tx,
            title_y,
            row["title"],
            row["title_font"],
            ACCENT,
            width=row["text_w"],
        )

        _uj_text(
            canvas,
            tx,
            body_y,
            row["body"],
            row["body_font"],
            TEXT,
            width=row["text_w"],
        )

        current_y += row_h

def _uj_attack_card(canvas, x, y, w, h, app, s, icon, color, title, body):
    _uj_rr(canvas, x, y, x + w, y + h, app.px(9), fill=_uavedusim_blend(BG, SURFACE, .20), outline=BORDER, width=1)

    icon_size = min(48 * s, h * .58)
    _uj_icon(canvas, icon, x + 42 * s, y + h / 2, icon_size, color, width=max(1, int(2 * s)), app=app)

    title_font = _uj_font(app, 10.0 * s, "bold")
    body_font = _uj_font(app, 8.7 * s)

    tx = x + max(app.px(78), int(82 * s))
    tw = max(app.px(130), w - (tx - x) - app.px(14))

    top_pad = max(app.px(11), int(12 * s))
    gap = max(app.px(4), int(5 * s))

    title_h = _uj_measure_text_height(app, title, title_font, tw)

    _uj_text(canvas, tx, y + top_pad, title, title_font, ACCENT, width=tw)
    _uj_text(canvas, tx, y + top_pad + title_h + gap, body, body_font, TEXT, width=tw)

def _uj_diagram(canvas, x, y, w, h, app, s):
    _uj_rr(canvas, x, y, x + w, y + h, app.px(10), fill=_uavedusim_blend(BG, SURFACE, .17), outline=BORDER, width=1)

    dot = _uavedusim_blend(BG, BORDER, .36)
    step = max(18, int(23 * s))

    for px in range(int(x + step), int(x + w - step), step):
        for py in range(int(y + step), int(y + h - step), step):
            canvas.create_oval(px, py, px + 1, py + 1, fill=dot, outline="")

    f = _uj_font(app, 9.6 * s, "bold")
    sw = max(1, int(1.4 * s))

    sat_x, sat_y = x + w * .24, y + h * .23
    drone_x, drone_y = x + w * .72, y + h * .23
    jammer_x, jammer_y = x + w * .50, y + h * .72

    canvas.create_text(sat_x, y + h * .10, text="Satélite GPS", fill=TEXT, font=f, anchor="center")
    _uj_satellite(canvas, sat_x, sat_y, s * .78, ACCENT)

    canvas.create_text(drone_x, y + h * .10, text="Drone / VANT", fill=TEXT, font=f, anchor="center")
    _uj_drone(canvas, drone_x, drone_y + h * .05, s * .70, ACCENT)

    canvas.create_line(
        sat_x + 62 * s,
        sat_y,
        drone_x - 60 * s,
        drone_y + h * .05,
        fill="#94e2d5",
        width=sw,
        dash=(6, 5),
        arrow=tk.LAST,
        arrowshape=(8 * s, 10 * s, 4 * s),
    )

    canvas.create_text(
        (sat_x + drone_x) / 2 + 12 * s,
        sat_y - 24 * s,
        text="sinal GPS",
        fill="#94e2d5",
        font=f,
        anchor="center",
    )

    _uj_jammer(canvas, jammer_x, jammer_y, s * .78, "#f38ba8")

    canvas.create_text(
        jammer_x,
        jammer_y + 55 * s,
        text="Fonte de jamming",
        fill=TEXT,
        font=f,
        anchor="center",
    )

    canvas.create_line(
        jammer_x + 24 * s,
        jammer_y - 42 * s,
        x + w * .56,
        y + h * .62,
        x + w * .66,
        y + h * .48,
        drone_x - 46 * s,
        drone_y + h * .20,
        fill="#f38ba8",
        width=sw,
        dash=(7, 5),
        smooth=True,
        arrow=tk.LAST,
        arrowshape=(8 * s, 10 * s, 4 * s),
    )

    canvas.create_text(
        x + w * .79,
        y + h * .57,
        text="interferência",
        fill="#f38ba8",
        font=f,
        anchor="center",
    )


def _uj_gradient_pill(canvas, x, y, w, h, r, c1, c2, tags=None):
    steps = max(36, int(w / 5))

    for i in range(steps):
        t = i / max(steps - 1, 1)
        color = _uavedusim_blend(c1, c2, t)
        x0 = x + r + (w - 2 * r) * i / steps
        x1 = x + r + (w - 2 * r) * (i + 1) / steps + 1
        canvas.create_rectangle(x0, y, x1, y + h, fill=color, outline="", tags=tags)

    canvas.create_oval(x, y, x + 2 * r, y + h, fill=c1, outline="", tags=tags)
    canvas.create_oval(x + w - 2 * r, y, x + w, y + h, fill=c2, outline="", tags=tags)


def _uavedusim_show_jamming_info_screen(self):
    self.page = "jamming_info"
    self.back_callback = self.show_main_menu
    self.selected_callback = None
    self.clear_content()
    self.set_badge("O QUE É JAMMING?")

    header = tk.Frame(self.content, bg=SURFACE)
    header.pack(fill="x", padx=self.CONTENT_PAD, pady=(self.HEADER_TOP_PAD, 0))

    ttk.Button(
        header,
        text="󰁎",
        style="Back.TButton",
        width=1,
        command=self.show_main_menu,
    ).pack(side="left", pady=(0, self.px(8)))

    outer = tk.Frame(self.content, bg=SURFACE)
    outer.pack(fill="both", expand=True, padx=self.CONTENT_PAD, pady=(self.px(4), 0))

    canvas, scrollbar = _uavedusim_make_scrollable_canvas(self, outer)

    self._jamming_info_scroll_enabled = False

    def draw(_event=None):
        canvas.delete("all")

        width = max(canvas.winfo_width(), 1)
        visible_h = max(canvas.winfo_height(), 1)

        margin = max(self.px(6), int(width * .012))
        card_x = margin
        card_y = self.px(4)
        card_w = max(self.px(520), width - margin * 2)
        usable_w = card_w - self.px(36)

        s = _uj_clamp(card_w / self.px(940), .82, 1.04)
        gap = max(self.px(6), int(8 * s))
        inner = max(self.px(10), int(12 * s))

        title_h = max(self.px(52), int(50 * s))
        attacks_title_h = max(self.px(26), int(26 * s))
        cta_h = max(self.px(42), int(42 * s))
        bottom_pad = max(self.px(10), int(12 * s))

        left_items = [
            (
                "target",
                "Interferência intencional",
                "Jamming é uma interferência intencional contra sinais utilizados por um sistema.",
            ),
            (
                "antenna",
                "Afeta múltiplos sinais",
                "Em VANTs, pode afetar sinais de GPS/GNSS, telemetria, comunicação de controle e outros canais sem fio usados durante a missão.",
            ),
            (
                "shield",
                "Degrada, bloqueia ou desestabiliza",
                "O objetivo é degradar, bloquear ou tornar instável a recepção desses sinais, prejudicando a capacidade do drone de navegar corretamente.",
            ),
        ]

        right_items = [
            (
                "pin",
                "Impactos no GPS/GNSS",
                "Pode causar erros de posição, falta de atualizações de localização ou alternância entre períodos de funcionamento normal e falha.",
            ),
            (
                "person",
                "Compromete a missão",
                "Mesmo sem invadir o software do drone, o atacante pode comprometer a missão ao explorar a dependência do VANT em relação aos sinais externos de navegação.",
            ),
            (
                "monitor",
                "Nesta interface",
                "Você pode escolher o modelo do drone, o mundo, iniciar PX4/Gazebo e executar cenários de jamming simulados.",
            ),
        ]

        attacks = [
            (
                "noise",
                "#fab387",
                "Noise jamming",
                "Ruído adicionado às medições de GPS/GNSS, fazendo o VANT calcular uma posição diferente da real.",
            ),
            (
                "blackout",
                "#f38ba8",
                "GPS blackout",
                "Sinal de GPS/GNSS interrompido: o VANT para de receber atualizações de posição.",
            ),
            (
                "pulse",
                "#94e2d5",
                "Pulsed jamming",
                "Alterna entre ruído, bloqueio e recuperação do sinal, causando falhas intermitentes.",
            ),
        ]

        layout_wide = usable_w >= self.px(760)
        x0 = card_x + inner

        if layout_wide:
            side_w = (usable_w - gap * 2) * .335
            diagram_w = usable_w - side_w * 2 - gap * 2

            left_h = _uj_info_panel_required_height(self, side_w, s, left_items)
            right_h = _uj_info_panel_required_height(self, side_w, s, right_items)

            # Altura dinâmica: cresce conforme o texto, com respiro mínimo visual.
            main_h = max(left_h, right_h, self.px(290), int(290 * s))
            diagram_h = main_h
        else:
            side_w = usable_w
            diagram_w = usable_w

            left_h = _uj_info_panel_required_height(self, side_w, s, left_items)
            right_h = _uj_info_panel_required_height(self, side_w, s, right_items)
            diagram_h = max(self.px(300), int(300 * s))
            main_h = left_h + gap + diagram_h + gap + right_h

        attack_cols = 3 if usable_w >= self.px(700) else 1
        attack_card_w = usable_w if attack_cols == 1 else (usable_w - gap * (attack_cols - 1)) / attack_cols
        attack_card_h = max(
            _uj_attack_card_required_height(self, attack_card_w, s, item[2], item[3])
            for item in attacks
        )

        if attack_cols == 1:
            attack_h = attack_card_h * 3 + gap * 2
        else:
            attack_h = attack_card_h

        card_h = (
            inner + title_h + gap + main_h + gap +
            attacks_title_h + attack_h + gap + cta_h + bottom_pad
        )

        # Mantém o fundo claro da página até o início do rodapé,
        # igual ao comportamento visual da tela inicial.
        card_h = max(card_h, visible_h - card_y - self.px(4))

        _uj_rr(
            canvas,
            card_x,
            card_y,
            card_x + card_w,
            card_y + card_h,
            self.px(14),
            fill=SURFACE2,
            outline=BORDER,
            width=1,
        )

        title_x = card_x + inner
        title_y = card_y + inner

        _uj_icon(
            canvas,
            "info",
            title_x + 25 * s,
            title_y + 25 * s,
            46 * s,
            ACCENT,
            width=max(2, int(3 * s)),
            app=self,
        )

        canvas.create_text(
            title_x + 64 * s,
            title_y + 4 * s,
            text="O que é jamming?",
            fill=TEXT,
            font=_uj_font(self, 19.5 * s, "bold"),
            anchor="nw",
        )

        jamming_subtitle_item = canvas.create_text(
            title_x + 64 * s,
            title_y + 54 * s,
            text="Interferência intencional que afeta sinais de navegação e comunicação.",
            fill=ACCENT,
            font=_uj_font(self, 12 * s),
            anchor="nw",
        )

        # Mantém o espaçamento atual em janela normal.
        # Em tela cheia/maximizada, garante respiro entre o subtítulo e os cards.
        y_base = title_y + title_h + gap

        is_fullscreen_layout = (
            getattr(self, "fullscreen", False)
            or self.root.winfo_height() >= self.root.winfo_screenheight() - self.px(80)
        )

        if is_fullscreen_layout:
            subtitle_bbox = canvas.bbox(jamming_subtitle_item)
            subtitle_bottom = subtitle_bbox[3] if subtitle_bbox else y_base

            y = max(
                y_base,
                subtitle_bottom + max(self.px(16), int(18 * s)),
            )
        else:
            y = y_base

        if layout_wide:
            left_x = x0
            diagram_x = left_x + side_w + gap
            right_x = diagram_x + diagram_w + gap

            _uj_info_panel(canvas, left_x, y, side_w, main_h, self, s, left_items)
            _uj_diagram(canvas, diagram_x, y, diagram_w, diagram_h, self, s)
            _uj_info_panel(canvas, right_x, y, side_w, main_h, self, s, right_items)
            y += main_h + gap
        else:
            _uj_info_panel(canvas, x0, y, side_w, left_h, self, s, left_items)
            y += left_h + gap

            _uj_diagram(canvas, x0, y, diagram_w, diagram_h, self, s)
            y += diagram_h + gap

            _uj_info_panel(canvas, x0, y, side_w, right_h, self, s, right_items)
            y += right_h + gap

        canvas.create_text(
            card_x + inner + 4 * s,
            y + attacks_title_h * .08,
            text="Ataques simulados",
            fill=ACCENT,
            font=_uj_font(self, 12.2 * s, "bold"),
            anchor="nw",
        )

        y += attacks_title_h

        for idx, item in enumerate(attacks):
            row = idx // attack_cols
            col = idx % attack_cols
            xx = x0 + col * (attack_card_w + gap)
            yy = y + row * (attack_card_h + gap)
            _uj_attack_card(canvas, xx, yy, attack_card_w, attack_card_h, self, s, *item)

        y += attack_h + gap

        cta_x = x0
        cta_w = usable_w

        _uj_gradient_pill(
            canvas,
            cta_x,
            y,
            cta_w,
            cta_h,
            cta_h / 2,
            ACCENT,
            LAVENDER,
            tags="jamming_cta",
        )

        canvas.create_text(
            cta_x + cta_w / 2,
            y + cta_h / 2,
            text="Entenda os Ataques de Jamming Simulados   ",
            fill="#11111b",
            font=_uj_font(self, 13.2 * s, "bold"),
            anchor="center",
            tags="jamming_cta",
        )

        scroll_h = max(card_y + card_h + self.px(4), visible_h)
        canvas.configure(scrollregion=(0, 0, width, scroll_h))
        self._jamming_info_scroll_enabled = scroll_h > visible_h + 2

        if self._jamming_info_scroll_enabled:
            if not scrollbar.winfo_ismapped():
                scrollbar.pack(side="right", fill="y")
        else:
            if scrollbar.winfo_ismapped():
                scrollbar.pack_forget()
            canvas.yview_moveto(0)
    canvas.bind("<Configure>", draw)
    _uavedusim_bind_canvas_wheel_scroll(canvas, lambda: getattr(self, "_jamming_info_scroll_enabled", False))

    canvas.tag_bind("jamming_cta", "<Button-1>", lambda _e: _uavedusim_show_jamming_attacks_screen(self))
    canvas.tag_bind("jamming_cta", "<Enter>", lambda _e: canvas.configure(cursor="hand2"))
    canvas.tag_bind("jamming_cta", "<Leave>", lambda _e: canvas.configure(cursor=""))

    self.root.after_idle(draw)

    footer = tk.Frame(self.content, bg=SURFACE)
    footer.pack(side="bottom", fill="x", padx=self.CONTENT_PAD, pady=(self.FOOTER_TOP_PAD, self.FOOTER_BOTTOM_PAD))

    tk.Frame(footer, bg=BORDER, height=1).pack(fill="x", pady=(0, self.px(10)))

    btn_frame = tk.Frame(footer, bg=SURFACE)
    btn_frame.pack(side="right")

    _uavedusim_footer_close_button(
        self, btn_frame, self.close_app,
    ).pack(side="left", padx=(0, self.px(8)))
# END UAVEDUSIM_JAMMING_CANVAS_INFO_SCREEN_PATCH



# BEGIN UAVEDUSIM_GLOBAL_SMALL_BACK_BUTTON_PATCH
# Reduz globalmente o botão "Voltar" em todas as telas que usam Back.TButton.

_UAVEDUSIM_ORIGINAL_SETUP_STYLES_GLOBAL_SMALL_BACK = UAVEDUSIMApp._setup_styles


def _uavedusim_setup_styles_global_small_back(self):
    _UAVEDUSIM_ORIGINAL_SETUP_STYLES_GLOBAL_SMALL_BACK(self)

    try:
        self.style.configure(
            "Back.TButton",
            font=(self.FONT_FAMILY, self.fs(18), "bold"),
            padding=(self.px(3), self.px(0)),
            background=BLUE,
            foreground="#11111b",
            relief="flat",
            borderwidth=0,
            anchor="center",
        )
        self.style.map(
            "Back.TButton",
            background=[
                ("disabled", DISABLED_BG),
                ("active", _uavedusim_blend(BLUE, "#000000", 0.18)),
                ("pressed", _uavedusim_blend(BLUE, "#000000", 0.18)),
            ],
            foreground=[
                ("disabled", DISABLED_FG),
                ("active", "#11111b"),
                ("pressed", "#11111b"),
            ],
        )
    except Exception:
        pass


UAVEDUSIMApp._setup_styles = _uavedusim_setup_styles_global_small_back
# END UAVEDUSIM_GLOBAL_SMALL_BACK_BUTTON_PATCH



# BEGIN UAVEDUSIM_GLOBAL_FOOTER_BACK_PATCH
# Move globalmente os botões de voltar para o rodapé, no canto esquerdo.

_UAVEDUSIM_ORIGINAL_SETUP_STYLES_FOOTER_BACK = UAVEDUSIMApp._setup_styles
_UAVEDUSIM_ORIGINAL_CLEAR_CONTENT_FOOTER_BACK = UAVEDUSIMApp.clear_content
_UAVEDUSIM_ORIGINAL_SET_BADGE_FOOTER_BACK = UAVEDUSIMApp.set_badge


def _uavedusim_setup_styles_footer_back(self):
    _UAVEDUSIM_ORIGINAL_SETUP_STYLES_FOOTER_BACK(self)

    try:
        self.style.configure(
            "FooterBack.TButton",
            font=(self.FONT_FAMILY, self.FS_BUTTON, "bold"),
            padding=(self.px(11), self.px(7)),
            background=BLUE,
            foreground="#11111b",
            relief="flat",
            borderwidth=0,
            anchor="center",
        )
        self.style.map(
            "FooterBack.TButton",
            background=[
                ("disabled", DISABLED_BG),
                ("active", _uavedusim_blend(BLUE, "#000000", 0.18)),
                ("pressed", _uavedusim_blend(BLUE, "#000000", 0.18)),
            ],
            foreground=[
                ("disabled", DISABLED_FG),
                ("active", "#11111b"),
                ("pressed", "#11111b"),
            ],
        )
    except Exception:
        pass


def _uavedusim_is_back_button_widget(widget):
    try:
        style = str(widget.cget("style"))
    except Exception:
        style = ""

    try:
        label = str(widget.cget("text") or "").strip().lower()
    except Exception:
        label = ""

    return (
        style in {"Back.TButton", "JammingBack.TButton"}
        or label in {"󰁎", "←", "← voltar", "voltar", "󰁎 voltar"}
        or label.startswith("←")
    )


def _uavedusim_remove_back_buttons_from(self, widget, keep=None):
    try:
        children = list(widget.winfo_children())
    except Exception:
        return

    for child in children:
        if child is keep:
            continue

        if _uavedusim_is_back_button_widget(child):
            parent = getattr(child, "master", None)

            try:
                child.destroy()
            except Exception:
                pass

            try:
                if parent is not None and parent is not self.content and parent.master is self.content:
                    if not parent.winfo_children():
                        parent.destroy()
            except Exception:
                pass

            continue

        _uavedusim_remove_back_buttons_from(self, child, keep=keep)


def _uavedusim_should_show_footer_back(self):
    if getattr(self, "page", "") == "main":
        return False

    # Na tela final de controle dos ataques PX4, não deve haver botão Voltar no rodapé.
    if getattr(self, "page", "") == "attack":
        return False

    # Essas telas já constroem o próprio botão Voltar no rodapé; se o
    # sistema global também injetasse um, os dois se sobreporiam.
    if getattr(self, "page", "") in (
        "model_select",
        "world_select",
        "jamming_attacks_info",
        "mitigation_info",
        "quiz_info",
        "tutorial_info",
    ):
        return False

    if getattr(self, "back_callback", None) is None:
        return False

    return True


def _uavedusim_footer_back_command(self):
    callback = getattr(self, "back_callback", None)

    if callback is not None:
        return callback

    return self.show_main_menu


def _uavedusim_place_footer_back_button(self):
    if not hasattr(self, "content"):
        return

    try:
        if not self.content.winfo_exists():
            return
    except Exception:
        return

    old_button = getattr(self, "_global_footer_back_button", None)

    if not _uavedusim_should_show_footer_back(self):
        _uavedusim_remove_back_buttons_from(self, self.content, keep=None)

        try:
            if old_button is not None and old_button.winfo_exists():
                old_button.destroy()
        except Exception:
            pass

        self._global_footer_back_button = None
        return

    _uavedusim_remove_back_buttons_from(self, self.content, keep=old_button)

    btn_w = max(self.px(90), 100)
    btn_h = self.px(37)

    try:
        if old_button is not None and old_button.winfo_exists():
            old_button._command = _uavedusim_footer_back_command(self)
            old_button.place_configure(
                x=self.CONTENT_PAD,
                rely=1.0,
                y=-(self.FOOTER_BOTTOM_PAD + self.px(2)),
                anchor="sw",
            )
            old_button.lift()
            return
    except Exception:
        pass

    try:
        btn = RoundedButton(
            self.content,
            text="Voltar",
            icon=ICONS["voltar"],
            command=_uavedusim_footer_back_command(self),
            outer_bg=SURFACE,
            fill=BLUE,
            fill_hover=_uavedusim_blend(BLUE, "#ffffff", 0.18),
            fill_press=_uavedusim_blend(BLUE, "#000000", 0.18),
            text_color="#11111b",
            text_color_hover="#11111b",
            radius=self.px(11),
            height=btn_h,
            width=btn_w,
            font=(self.FONT_FAMILY, self.fs(11), "bold"),
        )
        btn.place(
            x=self.CONTENT_PAD,
            rely=1.0,
            y=-(self.FOOTER_BOTTOM_PAD + self.px(2)),
            anchor="sw",
        )
        btn.lift()
        self._global_footer_back_button = btn
    except Exception:
        pass


def _uavedusim_clear_content_with_footer_back(self):
    try:
        old_button = getattr(self, "_global_footer_back_button", None)
        if old_button is not None and old_button.winfo_exists():
            old_button.destroy()
    except Exception:
        pass

    self._global_footer_back_button = None
    _UAVEDUSIM_ORIGINAL_CLEAR_CONTENT_FOOTER_BACK(self)


def _uavedusim_set_badge_with_footer_back(self, text):
    _UAVEDUSIM_ORIGINAL_SET_BADGE_FOOTER_BACK(self, text)

    try:
        self.root.after_idle(lambda: _uavedusim_place_footer_back_button(self))
        self.root.after(80, lambda: _uavedusim_place_footer_back_button(self))
        self.root.after(220, lambda: _uavedusim_place_footer_back_button(self))
        self.root.after(500, lambda: _uavedusim_place_footer_back_button(self))
    except Exception:
        pass


UAVEDUSIMApp._setup_styles = _uavedusim_setup_styles_footer_back
UAVEDUSIMApp.clear_content = _uavedusim_clear_content_with_footer_back
UAVEDUSIMApp.set_badge = _uavedusim_set_badge_with_footer_back
# END UAVEDUSIM_GLOBAL_FOOTER_BACK_PATCH



# BEGIN UAVEDUSIM_JAMMING_ATTACKS_CANVAS_SCREEN_PATCH
# Tela "Ataques de jamming simulados" em Canvas.
# Por padrão, somente o primeiro ataque fica expandido; os demais ficam recolhidos.

def _ujsa_nerd_font(app, size, weight="normal"):
    try:
        family = _uavedusim_preferred_nerd_font_family(app)
        resolved = app.fs(size)
    except Exception:
        family = "monospace"
        resolved = int(round(size))
    return (family, max(9, int(resolved)), weight)


def _ujsa_draw_attack_icon(canvas, kind, cx, cy, size, color, width=2, app=None):
    # Desenha o ícone e depois centraliza pelo bbox real.
    # Isso corrige deslocamentos visuais dentro dos círculos dos cards.
    before_items = set(canvas.find_all())

    try:
        _uj_icon(canvas, kind, cx, cy, size, color, width=width, app=app)
    except Exception:
        canvas.create_oval(
            cx - size / 2,
            cy - size / 2,
            cx + size / 2,
            cy + size / 2,
            outline=color,
            width=width,
        )
        fallback = {"noise": "≈", "blackout": "⊘", "pulse": "∿"}.get(kind, "◦")
        canvas.create_text(
            cx,
            cy,
            text=fallback,
            fill=color,
            font=("TkDefaultFont", int(size * 0.65), "bold"),
        )

    created_items = [item for item in canvas.find_all() if item not in before_items]

    if not created_items:
        return

    bbox = None

    for item in created_items:
        item_bbox = canvas.bbox(item)

        if not item_bbox:
            continue

        if bbox is None:
            bbox = list(item_bbox)
        else:
            bbox[0] = min(bbox[0], item_bbox[0])
            bbox[1] = min(bbox[1], item_bbox[1])
            bbox[2] = max(bbox[2], item_bbox[2])
            bbox[3] = max(bbox[3], item_bbox[3])

    if not bbox:
        return

    bbox_cx = (bbox[0] + bbox[2]) / 2
    bbox_cy = (bbox[1] + bbox[3]) / 2
    dx = cx - bbox_cx
    dy = cy - bbox_cy

    if abs(dx) < 0.01 and abs(dy) < 0.01:
        return

    for item in created_items:
        try:
            canvas.move(item, dx, dy)
        except Exception:
            pass


def _ujsa_draw_circle_attack_icon(canvas, kind, cx, cy, radius, color, bg, app=None):
    # Mantém somente o ícone Nerd Font do ataque.
    # O círculo/base ao redor foi removido.
    _ujsa_draw_attack_icon(
        canvas,
        kind,
        cx,
        cy,
        radius * 1.22,
        color,
        width=max(1, int(radius * 0.07)),
        app=app,
    )


def _ujsa_draw_nerd_icon(canvas, x, y, icon, color, app, size, tags=None, anchor="center"):
    return canvas.create_text(
        x,
        y,
        text=icon,
        fill=color,
        font=_ujsa_nerd_font(app, size, "bold"),
        anchor=anchor,
        tags=tags,
    )


def _ujsa_draw_gradient_pill(canvas, x, y, w, h, radius, color1, color2, tags=None):
    try:
        _uj_gradient_pill(canvas, x, y, w, h, radius, color1, color2, tags=tags)
        return
    except Exception:
        pass

    steps = max(28, int(w / 5))

    for i in range(steps):
        t = i / max(steps - 1, 1)
        color = _uavedusim_blend(color1, color2, t)
        x0 = x + radius + (w - 2 * radius) * i / steps
        x1 = x + radius + (w - 2 * radius) * (i + 1) / steps + 1
        canvas.create_rectangle(x0, y, x1, y + h, fill=color, outline="", tags=tags)

    canvas.create_oval(x, y, x + 2 * radius, y + h, fill=color1, outline="", tags=tags)
    canvas.create_oval(x + w - 2 * radius, y, x + w, y + h, fill=color2, outline="", tags=tags)


def _ujsa_intro_height(app, w, s, body=None):
    if body is None:
        body = (
            "Nesta tela, você pode comparar três cenários de jamming simulados no UAVEDUSIM. "
            "Cada ataque compromete a navegação de uma forma diferente, permitindo observar desde "
            "degradação contínua até perda total ou intermitente do sinal GPS/GNSS, tudo em ambiente "
            "seguro e sem interferência real."
        )
    body = _uavedusim_translate(body)

    text_w = max(app.px(120), w - max(app.px(90), 104 * s))
    text_h = _uj_measure_text_height(app, body, _uj_font(app, 9.6 * s), text_w)
    pad = max(app.px(13), 14 * s)

    return max(app.px(64), pad + text_h + pad)


def _ujsa_draw_intro(canvas, app, x, y, w, h, s, icon=None, body=None):
    _uj_rr(
        canvas,
        x,
        y,
        x + w,
        y + h,
        app.px(12),
        fill=_uavedusim_blend(BG, TAG_BG, 0.55),
        outline=_uavedusim_blend(BORDER, ACCENT, 0.24),
        width=1,
    )

    icon_x = x + max(app.px(34), 38 * s)
    icon_y = y + h / 2

    _ujsa_draw_nerd_icon(canvas, icon_x, icon_y, icon or "", ACCENT, app, 20 * s)

    if body is None:
        body = (
            "Nesta tela, você pode comparar três cenários de jamming simulados no UAVEDUSIM. "
            "Cada ataque compromete a navegação de uma forma diferente, permitindo observar desde "
            "degradação contínua até perda total ou intermitente do sinal GPS/GNSS, tudo em ambiente "
            "seguro e sem interferência real."
        )

    _uj_text(
        canvas,
        x + max(app.px(66), 76 * s),
        y + max(app.px(13), 14 * s),
        body,
        _uj_font(app, 9.6 * s),
        TEXT,
        width=w - max(app.px(90), 104 * s),
    )


def _ujsa_attack_detail_columns(canvas, app, x, y, w, h, color, details, s):
    # Em telas normais, os três tópicos ficam lado a lado.
    # Só empilha quando a área realmente estiver estreita.
    wide = w >= max(500, app.px(500))

    title_font = _uj_font(app, 10.0 * s, "bold")
    body_font = _uj_font(app, 9.5 * s)

    if wide:
        col_gap = max(app.px(10), int(11 * s))
        col_w = (w - col_gap * 2) / 3
        top_y = y + max(app.px(2), int(2 * s))

        for idx, (icon, title, body) in enumerate(details):
            col_x = x + idx * (col_w + col_gap)

            if idx > 0:
                sep_x = col_x - col_gap / 2
                canvas.create_line(
                    sep_x,
                    top_y,
                    sep_x,
                    y + h,
                    fill=BORDER,
                    width=1,
                )

            icon_x = col_x + max(app.px(8), int(8 * s))
            text_x = col_x + max(app.px(27), int(29 * s))
            text_w = max(app.px(126), col_w - (text_x - col_x) - max(app.px(5), int(5 * s)))

            _ujsa_draw_nerd_icon(
                canvas,
                icon_x,
                top_y + max(app.px(9), int(11 * s)),
                icon,
                color,
                app,
                10.5 * s,
            )

            _uj_text(
                canvas,
                text_x,
                top_y,
                title,
                title_font,
                color,
                width=text_w,
            )

            _uj_text(
                canvas,
                text_x,
                top_y + max(app.px(19), int(20 * s)),
                body,
                body_font,
                TEXT,
                width=text_w,
            )

        return

    # Fallback para janela muito estreita.
    row_gap = max(app.px(7), int(8 * s))
    row_h = max(app.px(76), (h - row_gap * 2) / 3)
    icon_x = x + max(app.px(8), int(9 * s))
    text_x = x + max(app.px(30), int(33 * s))
    text_w = max(app.px(160), w - (text_x - x) - max(app.px(8), int(8 * s)))

    current_y = y

    for idx, (icon, title, body) in enumerate(details):
        if idx > 0:
            sep_y = current_y - row_gap / 2
            canvas.create_line(x, sep_y, x + w, sep_y, fill=BORDER, width=1)

        _ujsa_draw_nerd_icon(
            canvas,
            icon_x,
            current_y + max(app.px(11), int(12 * s)),
            icon,
            color,
            app,
            10.5 * s,
        )

        _uj_text(
            canvas,
            text_x,
            current_y,
            title,
            title_font,
            color,
            width=text_w,
        )

        _uj_text(
            canvas,
            text_x,
            current_y + max(app.px(19), int(20 * s)),
            body,
            body_font,
            TEXT,
            width=text_w,
        )

        current_y += row_h + row_gap


def _ujsa_attack_card_height(app, w, expanded, s, attack=None):
    if not expanded or attack is None:
        return max(app.px(100), int(104 * s))

    title_x_offset = max(app.px(86), int(96 * s))
    right_pad = max(app.px(58), int(64 * s))
    text_w = max(app.px(120), w - title_x_offset - right_pad)

    # Título centralizado verticalmente em relação ao ícone (mesma
    # posição usada no desenho real do card). Medimos o texto já
    # traduzido, já que é isso que será efetivamente desenhado — medir
    # o PT original poderia calcular uma altura menor que a real quando
    # a versão em inglês quebra em mais linhas.
    title_text = _uavedusim_translate(attack["title"])
    summary_text = _uavedusim_translate(attack["summary"])

    icon_y_offset = max(app.px(43), int(46 * s))
    title_font = _uj_font(app, 14.8 * s, "bold")
    title_h = _uj_measure_text_height(app, title_text, title_font, text_w)
    title_top = icon_y_offset - title_h / 2
    small_gap = max(app.px(5), int(6 * s))
    summary_top = title_top + title_h + small_gap

    # Resumo (mesma fonte/posição usadas no desenho real do card).
    summary_font = _uj_font(app, 9.8 * s)
    summary_h = _uj_measure_text_height(app, summary_text, summary_font, text_w)
    summary_bottom = summary_top + summary_h

    divider_y_offset = max(
        max(app.px(86), int(90 * s)),
        summary_bottom + max(app.px(9), int(10 * s)),
    )
    details_y_offset = divider_y_offset + max(app.px(10), int(11 * s))

    # As 3 colunas de detalhe (ou empilhadas, em telas estreitas).
    wide = text_w >= max(500, app.px(500))
    detail_title_font = _uj_font(app, 10.0 * s, "bold")
    body_font = _uj_font(app, 9.5 * s)
    body_offset = max(app.px(19), int(20 * s))

    if wide:
        col_gap = max(app.px(10), int(11 * s))
        col_w = (text_w - col_gap * 2) / 3
        col_text_w = max(
            app.px(126),
            col_w - max(app.px(27), int(29 * s)) - max(app.px(5), int(5 * s)),
        )

        col_h = 0
        for _icon, _title, body in attack["details"]:
            body_h = _uj_measure_text_height(app, _uavedusim_translate(body), body_font, col_text_w)
            col_h = max(col_h, body_offset + body_h)

        details_h = max(app.px(2), int(2 * s)) + col_h
    else:
        row_gap = max(app.px(7), int(8 * s))
        icon_x_offset = max(app.px(30), int(33 * s))
        row_text_w = max(app.px(160), text_w - icon_x_offset - max(app.px(8), int(8 * s)))

        rows_h = 0
        for _icon, _title, body in attack["details"]:
            body_h = _uj_measure_text_height(app, _uavedusim_translate(body), body_font, row_text_w)
            rows_h += max(app.px(76), body_offset + body_h) + row_gap

        details_h = max(0, rows_h - row_gap)

    # Margem extra de segurança embaixo, pra absorver pequenas variações
    # de quebra de linha entre PT e EN sem cortar a última linha de texto.
    bottom_pad = max(app.px(10), int(12 * s))

    return max(app.px(120), details_y_offset + details_h + bottom_pad)


def _ujsa_draw_attack_card(canvas, app, x, y, w, attack, expanded, s):
    color = attack["color"]
    h = _ujsa_attack_card_height(app, w, expanded, s, attack=attack)
    tag = f"ujsa_attack_{attack['key']}"

    bg = _uavedusim_blend(BG, SURFACE, 0.22)
    border = color if expanded else BORDER

    _uj_rr(
        canvas,
        x,
        y,
        x + w,
        y + h,
        app.px(12),
        fill=bg,
        outline=border,
        width=(2 if expanded else 1),
        tags=tag,
    )

    icon_r = max(app.px(25), int(30 * s))
    icon_x = x + max(app.px(42), int(48 * s))
    icon_y = y + max(app.px(43), int(46 * s))

    _ujsa_draw_circle_attack_icon(canvas, attack["icon"], icon_x, icon_y, icon_r, color, bg)

    title_x = x + max(app.px(86), int(96 * s))
    right_pad = max(app.px(58), int(64 * s))
    text_w = w - (title_x - x) - right_pad

    canvas.create_text(
        x + w - right_pad / 2,
        y + max(app.px(35), int(38 * s)),
        text=("" if expanded else ""),
        fill=MUTED,
        font=_ujsa_nerd_font(app, 14 * s, "bold"),
        anchor="center",
        tags=tag,
    )

    title_font = _uj_font(app, 14.8 * s, "bold")
    title_item = _uj_text(
        canvas,
        title_x,
        icon_y,
        attack["title"],
        title_font,
        color,
        width=text_w,
        anchor="w",
        tags=tag,
    )

    title_bbox = canvas.bbox(title_item)
    title_h = _uj_measure_text_height(app, attack["title"], title_font, text_w)
    title_bottom = title_bbox[3] if title_bbox else icon_y + title_h / 2

    small_gap = max(app.px(5), int(6 * s))
    summary_y = title_bottom + small_gap

    summary_item = _uj_text(
        canvas,
        title_x,
        summary_y,
        attack["summary"],
        _uj_font(app, 9.8 * s),
        TEXT,
        width=text_w,
        tags=tag,
    )

    if not expanded:
        return h

    summary_bbox = canvas.bbox(summary_item)
    summary_bottom = summary_bbox[3] if summary_bbox else y + max(app.px(68), int(70 * s))
    divider_y = max(
        y + max(app.px(86), int(90 * s)),
        summary_bottom + max(app.px(9), int(10 * s)),
    )
    canvas.create_line(title_x, divider_y, x + w - right_pad, divider_y, fill=BORDER, width=1)

    details_y = divider_y + max(app.px(10), int(11 * s))
    details_h = h - (details_y - y) - max(app.px(6), int(7 * s))

    _ujsa_attack_detail_columns(
        canvas,
        app,
        title_x,
        details_y,
        w - (title_x - x) - right_pad,
        details_h,
        color,
        attack["details"],
        s,
    )

    return h


def _uavedusim_show_jamming_attacks_screen(self):
    self.page = "jamming_attacks_info"
    self.back_callback = lambda: _uavedusim_show_jamming_info_screen(self)
    self.selected_callback = None

    # Padrão da tela: somente o primeiro ataque aparece expandido.
    self._jamming_attack_open_key = "noise"

    self.clear_content()
    self.set_badge("ATAQUES DO SIMULADOR")

    outer = tk.Frame(self.content, bg=SURFACE)
    outer.pack(
        fill="both",
        expand=True,
        padx=self.CONTENT_PAD,
        pady=(self.HEADER_TOP_PAD, 0),
    )

    canvas, scrollbar = _uavedusim_make_scrollable_canvas(self, outer)

    self._jamming_attacks_scroll_enabled = False

    attacks = [
        {
            "key": "noise",
            "icon": "noise",
            "color": "#fab387",
            "title": "Jamming Noise",
            "summary": "O sinal continua chegando, mas as medições se tornam degradadas e menos confiáveis.",
            "details": [
                (
                    "",
                    "O que acontece",
                    "O GPS permanece ativo, mas a intensidade do ataque introduz perturbações na posição, na altitude, na velocidade e nos indicadores de qualidade do sinal.",
                ),
                (
                    "",
                    "Por que é perigoso",
                    "O VANT pode estimar posição incorreta, gerando desvios de trajetória e instabilidade na navegação.",
                ),
                (
                    "",
                    "Como é simulado",
                    "Perturbações são adicionadas às medições de GPS/GNSS para reproduzir degradação contínua do sinal.",
                ),
            ],
        },
        {
            "key": "blackout",
            "icon": "blackout",
            "color": "#f38ba8",
            "title": "Jamming Blackout",
            "summary": "O sinal de GPS deixa de ser recebido: enquanto o ataque está ativo, a publicação de atualizações é interrompida por completo.",
            "details": [
                (
                    "",
                    "O que acontece",
                    "O VANT fica sem novas medições válidas de GPS e perde sua principal referência externa de localização.",
                ),
                (
                    "",
                    "Por que é perigoso",
                    "Sem posição global válida, o PX4 pode acionar o failsafe padrão e iniciar pouso automático — que pode ocorrer em local inadequado, como sobre água, obstáculos ou áreas urbanas.",
                ),
                (
                    "",
                    "Como é simulado",
                    "As atualizações de GPS são interrompidas, reproduzindo um cenário de bloqueio total do sinal.",
                ),
            ],
        },
        {
            "key": "pulse",
            "icon": "pulse",
            "color": "#94e2d5",
            "title": "Jamming Pulse",
            "summary": "O ataque alterna entre degradação, bloqueio, recuperação parcial e retorno a um sinal aparentemente válido, num ciclo que se repete.",
            "details": [
                (
                    "",
                    "O que acontece",
                    "O comportamento do GPS oscila ao longo do tempo, criando falhas intermitentes e mudanças repentinas de qualidade.",
                ),
                (
                    "",
                    "Por que é perigoso",
                    "Esse padrão dificulta a estimativa de estado e pode confundir o controle por alternar entre falha e normalidade.",
                ),
                (
                    "",
                    "Como é simulado",
                    "O simulador alterna ciclicamente ruído, blackout e recuperação para reproduzir interferência pulsada.",
                ),
            ],
        },
    ]

    def draw(_event=None):
        canvas.delete("all")

        width = max(canvas.winfo_width(), 1)
        visible_h = max(canvas.winfo_height(), 1)

        margin = max(self.px(6), int(width * .012))
        x = margin
        y = self.px(4)
        w = max(self.px(520), width - 2 * margin)

        s = _uj_clamp(w / self.px(940), .82, 1.04)

        card_bg = _uavedusim_blend(BG, SURFACE2, 0.65)

        # O fundo claro da página é desenhado depois do conteúdo,
        # para acompanhar a altura real do scroll até o rodapé.

        inner = max(self.px(10), int(12 * s))
        content_x = x + inner
        content_w = w - 2 * inner
        yy = y + inner

        header_icon_x = content_x + 25 * s

        # Ícone semântico da tela: antena/interferência de jamming.
        _ujsa_draw_attack_icon(
            canvas,
            "noise",
            header_icon_x,
            yy + 25 * s,
            46 * s,
            ACCENT,
            width=max(2, int(3 * s)),
        )

        title_x = content_x + 64 * s

        _uj_text(
            canvas,
            title_x,
            yy + 4 * s,
            "Ataques de jamming simulados",
            _uj_font(self,  19.5 * s, "bold"),
            TEXT,
            width=content_w - (title_x - content_x),
        )

        subtitle_item = _uj_text(
            canvas,
            title_x,
            yy + 54 * s,
            "Entenda como cada ataque afeta a navegação GPS/GNSS do VANT em ambiente simulado.",
            _uj_font(self,  12 * s),
            ACCENT,
            width=content_w - (title_x - content_x),
        )

        # Mede a altura real do subtítulo (ele pode quebrar em 2 linhas
        # dependendo do idioma/largura), em vez de assumir sempre 1 linha —
        # senão a segunda linha fica coberta pelo card seguinte.
        yy_base = yy + max(self.px(52), int(50 * s)) + max(self.px(6), int(8 * s))

        subtitle_bbox = canvas.bbox(subtitle_item)
        subtitle_bottom = subtitle_bbox[3] if subtitle_bbox else yy_base

        yy = max(
            yy_base,
            subtitle_bottom + max(self.px(16), int(18 * s)),
        )

        intro_h = _ujsa_intro_height(self, content_w, s)
        _ujsa_draw_intro(canvas, self, content_x, yy, content_w, intro_h, s)

        yy += intro_h + max(self.px(7), int(8 * s))

        for attack in attacks:
            expanded = getattr(self, "_jamming_attack_open_key", "noise") == attack["key"]
            h = _ujsa_draw_attack_card(canvas, self, content_x, yy, content_w, attack, expanded, s)
            yy += h + max(self.px(7), int(8 * s))

        cta_h = max(self.px(48), int(50 * s))

        _ujsa_draw_gradient_pill(
            canvas,
            content_x,
            yy,
            content_w,
            cta_h,
            cta_h / 2,
            ACCENT,
            LAVENDER,
            tags="ujsa_cta",
        )

        canvas.create_text(
            content_x + content_w / 2,
            yy + cta_h / 2,
            text="Entenda as mitigações do simulador   ",
            fill="#11111b",
            font=_uj_font(self, 12.8 * s, "bold"),
            anchor="center",
            tags="ujsa_cta",
        )

        yy += cta_h + inner

        scroll_h = max(yy + self.px(6), visible_h)

        _uj_rr(
            canvas,
            x,
            y,
            x + w,
            max(y + self.px(520), scroll_h - self.px(4)),
            self.px(14),
            fill=card_bg,
            outline=BORDER,
            width=1,
            tags="ujsa_page_bg",
        )
        canvas.tag_lower("ujsa_page_bg")

        canvas.configure(scrollregion=(0, 0, width, scroll_h))

        self._jamming_attacks_scroll_enabled = scroll_h > visible_h + 2

        if self._jamming_attacks_scroll_enabled:
            # O scrollbar fica sobreposto na borda direita da área cinza,
            # sem usar pack(). Assim ele não reduz a largura do Canvas nem
            # muda o tamanho dos cards quando aparece.
            try:
                scrollbar.place(
                    relx=1.0,
                    rely=0.0,
                    x=-self.px(3),
                    y=0,
                    anchor="ne",
                    width=self.SCROLLBAR_W,
                    height=visible_h,
                )
                scrollbar.lift()
            except Exception:
                pass
        else:
            try:
                scrollbar.place_forget()
            except Exception:
                pass
            canvas.yview_moveto(0)

    def open_attack(key):
        # Clicar no card fechado abre esse card.
        # Clicar novamente no card já aberto fecha ele, permitindo deixar todos fechados.
        current = getattr(self, "_jamming_attack_open_key", "noise")

        if current == key:
            self._jamming_attack_open_key = None
        else:
            self._jamming_attack_open_key = key

        draw()

    canvas.bind("<Configure>", draw)
    _uavedusim_bind_canvas_wheel_scroll(canvas, lambda: getattr(self, "_jamming_attacks_scroll_enabled", False))

    for attack in attacks:
        key = attack["key"]
        canvas.tag_bind(f"ujsa_attack_{key}", "<Button-1>", lambda _e, k=key: open_attack(k))
        canvas.tag_bind(f"ujsa_attack_{key}", "<Enter>", lambda _e: canvas.configure(cursor="hand2"))
        canvas.tag_bind(f"ujsa_attack_{key}", "<Leave>", lambda _e: canvas.configure(cursor=""))

    canvas.tag_bind("ujsa_cta", "<Button-1>", lambda _e: _uavedusim_show_mitigation_screen(self))
    canvas.tag_bind("ujsa_cta", "<Enter>", lambda _e: canvas.configure(cursor="hand2"))
    canvas.tag_bind("ujsa_cta", "<Leave>", lambda _e: canvas.configure(cursor=""))

    self.root.after_idle(draw)

    footer = tk.Frame(self.content, bg=SURFACE)
    footer.pack(
        side="bottom",
        fill="x",
        padx=self.CONTENT_PAD,
        pady=(self.FOOTER_TOP_PAD, self.FOOTER_BOTTOM_PAD),
    )

    tk.Frame(footer, bg=BORDER, height=1).pack(fill="x", pady=(0, self.px(10)))

    left_btns = tk.Frame(footer, bg=SURFACE)
    left_btns.pack(side="left")

    _uavedusim_footer_back_button(
        self, left_btns, lambda: _uavedusim_show_jamming_info_screen(self),
    ).pack(side="left")

    right_btns = tk.Frame(footer, bg=SURFACE)
    right_btns.pack(side="right")

    _uavedusim_footer_close_button(
        self, right_btns, self.close_app,
    ).pack(side="left", padx=(0, self.px(8)))
# END UAVEDUSIM_JAMMING_ATTACKS_CANVAS_SCREEN_PATCH



# BEGIN UAVEDUSIM_MITIGATION_CANVAS_SCREEN_PATCH
# Tela "Mitigações de jamming GPS/GNSS" em Canvas, reaproveitando a
# mesma estrutura/estilo visual da tela de ataques (accordion, cores
# da paleta Catppuccin Mocha). O botão principal desta tela inicia a
# simulação (mesmo fluxo do botão "Iniciar simulação" da tela inicial).

def _ujsa_draw_safety_callout(canvas, app, x, y, w, h, s, icon_kind, accent, title, body, tags=None):
    bg = _uavedusim_blend(BG, accent, 0.16)
    border = _uavedusim_blend(BG, accent, 0.40)

    _uj_rr(
        canvas, x, y, x + w, y + h, app.px(12),
        fill=bg, outline=border, width=1, tags=tags,
    )

    icon_x = x + max(app.px(30), 34 * s)
    icon_y = y + max(app.px(24), 26 * s)

    _ujsa_draw_attack_icon(canvas, icon_kind, icon_x, icon_y, 20 * s, accent, width=max(1, int(2 * s)), app=app)

    text_x = x + max(app.px(58), 64 * s)
    text_w = w - (text_x - x) - max(app.px(20), 22 * s)

    _uj_text(
        canvas, text_x, y + max(app.px(12), 13 * s),
        title, _uj_font(app, 10.4 * s, "bold"), accent,
        width=text_w, tags=tags,
    )

    _uj_text(
        canvas, text_x, y + max(app.px(32), 34 * s),
        body, _uj_font(app, 9.6 * s), accent,
        width=text_w, tags=tags,
    )


def _ujsa_safety_callout_height(app, w, s, title, body):
    text_x_offset = max(app.px(58), 64 * s)
    right_pad = max(app.px(20), 22 * s)
    text_w = max(app.px(120), w - text_x_offset - right_pad)

    title_h = _uj_measure_text_height(app, title, _uj_font(app, 10.4 * s, "bold"), text_w)
    body_h = _uj_measure_text_height(app, body, _uj_font(app, 9.6 * s), text_w)

    top_pad = max(app.px(12), 13 * s)
    gap = max(app.px(32), 34 * s) - max(app.px(12), 13 * s) - title_h
    bottom_pad = max(app.px(12), 13 * s)

    return max(app.px(64), top_pad + title_h + max(gap, app.px(4)) + body_h + bottom_pad)


def _uavedusim_show_mitigation_screen(self):
    self.page = "mitigation_info"
    self.back_callback = lambda: _uavedusim_show_jamming_attacks_screen(self)
    self.selected_callback = None

    self._mitigation_open_key = "noise"

    self.clear_content()
    self.set_badge("MITIGAÇÕES")

    # Verde da paleta (Green #a6e3a1) como identidade da tela: reforça
    # "isso aqui é a solução", em contraste com as cores de risco
    # (laranja/vermelho/teal) usadas na tela de ataques.
    MITIG_TITLE_COLOR = "#a6e3a1"
    MITIG_CTA_COLOR = "#a6e3a1"
    MITIG_CTA_COLOR_2 = "#94e2d5"

    outer = tk.Frame(self.content, bg=SURFACE)
    outer.pack(
        fill="both",
        expand=True,
        padx=self.CONTENT_PAD,
        pady=(self.HEADER_TOP_PAD, 0),
    )

    canvas, scrollbar = _uavedusim_make_scrollable_canvas(self, outer)

    self._mitigation_scroll_enabled = False

    mitigations = [
        {
            "key": "noise",
            "icon": "noise",
            "color": "#a6e3a1",
            "title": "Mitigação do Jamming Noise",
            "summary": "Filtro de Kalman valida cada medição de GPS antes de usá-la, considerando também a altitude.",
            "details": [
                (
                    "",
                    "Como funciona",
                    "Cada medição de GPS é comparada com a posição prevista pelo filtro de Kalman; leituras muito divergentes têm sua influência reduzida.",
                ),
                (
                    "",
                    "Quando entra em ação",
                    "Sempre que uma leitura de GPS foge do comportamento esperado do VANT — mesmo com o sinal aparentemente disponível.",
                ),
                (
                    "",
                    "O que muda para o VANT",
                    "A altitude passa a priorizar a posição local do PX4 e o barômetro; a odometria visual ajuda quando disponível, evitando oscilações verticais falsas.",
                ),
            ],
        },
        {
            "key": "blackout",
            "icon": "blackout",
            "color": "#fab387",
            "title": "Mitigação do Jamming Blackout",
            "summary": "Âncora de posição e pseudo-medições substituem o GPS enquanto ele estiver ausente.",
            "details": [
                (
                    "",
                    "Como funciona",
                    "Um timeout detecta a ausência de atualizações de GPS e cria uma âncora: um ponto de referência fixado na última posição confiável do VANT.",
                ),
                (
                    "",
                    "Quando entra em ação",
                    "Assim que o tempo sem novas medições de GPS ultrapassa o limite configurado.",
                ),
                (
                    "",
                    "O que muda para o VANT",
                    "A partir da âncora, o sistema gera pseudo-medições — estimativas internas de posição, não um GPS real — usando dead reckoning e odometria visual quando disponível, até o sinal voltar.",
                ),
            ],
        },
        {
            "key": "pulse",
            "icon": "pulse",
            "color": "#94e2d5",
            "title": "Mitigação do Jamming Pulse",
            "summary": "Combina as estratégias do Noise e do Blackout, conforme a fase do ataque.",
            "details": [
                (
                    "",
                    "Como funciona",
                    "Usa o filtro de Kalman nas fases de degradação e recuperação, e a âncora com pseudo-medições durante os bloqueios.",
                ),
                (
                    "",
                    "Quando entra em ação",
                    "A cada transição de fase do ataque: degradação, bloqueio ou recuperação do sinal.",
                ),
                (
                    "",
                    "O que muda para o VANT",
                    "Evita reações bruscas quando o GPS retorna, já que o sinal pode voltar ainda instável ou com erro residual.",
                ),
            ],
        },
    ]

    safety_title = "Resposta de segurança — vale para os três ataques"
    safety_body = (
        "Nenhuma mitigação sustenta o voo indefinidamente. Se a interferência persistir após o "
        "VANT alcançar a região de destino, o sistema aciona pouso controlado e desarme dos motores."
    )

    def draw(_event=None):
        canvas.delete("all")

        width = max(canvas.winfo_width(), 1)
        visible_h = max(canvas.winfo_height(), 1)

        margin = max(self.px(6), int(width * .012))
        x = margin
        y = self.px(4)
        w = max(self.px(520), width - 2 * margin)

        s = _uj_clamp(w / self.px(940), .82, 1.04)

        card_bg = _uavedusim_blend(BG, SURFACE2, 0.65)

        inner = max(self.px(10), int(12 * s))
        content_x = x + inner
        content_w = w - 2 * inner
        yy = y + inner

        header_icon_x = content_x + 25 * s

        _ujsa_draw_attack_icon(
            canvas,
            "shield",
            header_icon_x,
            yy + 25 * s,
            46 * s,
            MITIG_TITLE_COLOR,
            width=max(2, int(3 * s)),
            app=self,
        )

        title_x = content_x + 64 * s

        _uj_text(
            canvas,
            title_x,
            yy + 4 * s,
            "Mitigações de jamming GPS/GNSS",
            _uj_font(self, 19.5 * s, "bold"),
            TEXT,
            width=content_w - (title_x - content_x),
        )

        subtitle_item = _uj_text(
            canvas,
            title_x,
            yy + 54 * s,
            "Como o UAVEDUSIM reduz o efeito de cada ataque antes que ele afete o estimador.",
            _uj_font(self, 12 * s),
            MITIG_TITLE_COLOR,
            width=content_w - (title_x - content_x),
        )

        yy_base = yy + max(self.px(52), int(50 * s)) + max(self.px(6), int(8 * s))

        is_fullscreen_layout = (
            getattr(self, "fullscreen", False)
            or self.root.winfo_height() >= self.root.winfo_screenheight() - self.px(80)
        )

        if is_fullscreen_layout:
            subtitle_bbox = canvas.bbox(subtitle_item)
            subtitle_bottom = subtitle_bbox[3] if subtitle_bbox else yy_base
            yy = max(yy_base, subtitle_bottom + max(self.px(16), int(18 * s)))
        else:
            yy = yy_base

        intro_body = (
            "O objetivo não é impedir o ataque, mas evitar que medições ruins, ausentes ou "
            "instáveis sejam usadas diretamente pela navegação sem tratamento. Cada tipo de "
            "ataque exige uma resposta diferente."
        )
        intro_h = _ujsa_intro_height(self, content_w, s, body=intro_body)
        _ujsa_draw_intro(canvas, self, content_x, yy, content_w, intro_h, s, body=intro_body)

        yy += intro_h + max(self.px(7), int(8 * s))

        for mitig in mitigations:
            expanded = getattr(self, "_mitigation_open_key", "noise") == mitig["key"]
            h = _ujsa_attack_card_height(self, content_w, expanded, s, attack=mitig)
            _ujsa_draw_attack_card(canvas, self, content_x, yy, content_w, mitig, expanded, s)
            yy += h + max(self.px(7), int(8 * s))

        safety_h = _ujsa_safety_callout_height(self, content_w, s, safety_title, safety_body)
        _ujsa_draw_safety_callout(
            canvas, self, content_x, yy, content_w, safety_h, s,
            "shield", "#f9e2af", safety_title, safety_body,
        )

        yy += safety_h + max(self.px(7), int(8 * s))

        quiz_note = "Antes de liberar o simulador, responda um quiz rápido de 5 perguntas sobre o que você acabou de ver — é preciso acertar pelo menos 2 pra desbloquear."
        quiz_note_font = _uj_font(self, 10.2 * s)
        _uj_text(canvas, content_x, yy, quiz_note, quiz_note_font, MUTED, width=content_w)
        yy += _uj_measure_text_height(self, quiz_note, quiz_note_font, content_w) + max(self.px(9), int(10 * s))

        cta_h = max(self.px(48), int(50 * s))

        _ujsa_draw_gradient_pill(
            canvas,
            content_x,
            yy,
            content_w,
            cta_h,
            cta_h / 2,
            MITIG_CTA_COLOR,
            MITIG_CTA_COLOR_2,
            tags="mitig_cta",
        )

        canvas.create_text(
            content_x + content_w / 2,
            yy + cta_h / 2,
            text="Faça o quiz para liberar o simulador   ",
            fill="#11111b",
            font=_uj_font(self, 12.8 * s, "bold"),
            anchor="center",
            tags="mitig_cta",
        )

        yy += cta_h + inner

        scroll_h = max(yy + self.px(6), visible_h)

        _uj_rr(
            canvas,
            x,
            y,
            x + w,
            max(y + self.px(520), scroll_h - self.px(4)),
            self.px(14),
            fill=card_bg,
            outline=BORDER,
            width=1,
            tags="mitig_page_bg",
        )
        canvas.tag_lower("mitig_page_bg")

        canvas.configure(scrollregion=(0, 0, width, scroll_h))

        self._mitigation_scroll_enabled = scroll_h > visible_h + 2

        if self._mitigation_scroll_enabled:
            try:
                scrollbar.place(
                    relx=1.0, rely=0.0, x=-self.px(3), y=0,
                    anchor="ne", width=self.SCROLLBAR_W, height=visible_h,
                )
                scrollbar.lift()
            except Exception:
                pass
        else:
            try:
                scrollbar.place_forget()
            except Exception:
                pass
            canvas.yview_moveto(0)

    def open_mitigation(key):
        current = getattr(self, "_mitigation_open_key", "noise")
        self._mitigation_open_key = None if current == key else key
        draw()

    canvas.bind("<Configure>", draw)
    _uavedusim_bind_canvas_wheel_scroll(canvas, lambda: getattr(self, "_mitigation_scroll_enabled", False))

    for mitig in mitigations:
        key = mitig["key"]
        canvas.tag_bind(f"ujsa_attack_{key}", "<Button-1>", lambda _e, k=key: open_mitigation(k))
        canvas.tag_bind(f"ujsa_attack_{key}", "<Enter>", lambda _e: canvas.configure(cursor="hand2"))
        canvas.tag_bind(f"ujsa_attack_{key}", "<Leave>", lambda _e: canvas.configure(cursor=""))

    canvas.tag_bind("mitig_cta", "<Button-1>", lambda _e: _uavedusim_show_quiz_screen(self))
    canvas.tag_bind("mitig_cta", "<Enter>", lambda _e: canvas.configure(cursor="hand2"))
    canvas.tag_bind("mitig_cta", "<Leave>", lambda _e: canvas.configure(cursor=""))

    self.root.after_idle(draw)

    footer = tk.Frame(self.content, bg=SURFACE)
    footer.pack(
        side="bottom",
        fill="x",
        padx=self.CONTENT_PAD,
        pady=(self.FOOTER_TOP_PAD, self.FOOTER_BOTTOM_PAD),
    )

    tk.Frame(footer, bg=BORDER, height=1).pack(fill="x", pady=(0, self.px(10)))

    left_btns = tk.Frame(footer, bg=SURFACE)
    left_btns.pack(side="left")

    _uavedusim_footer_back_button(
        self, left_btns, lambda: _uavedusim_show_jamming_attacks_screen(self),
    ).pack(side="left")

    right_btns = tk.Frame(footer, bg=SURFACE)
    right_btns.pack(side="right")

    _uavedusim_footer_close_button(
        self, right_btns, self.close_app,
    ).pack(side="left", padx=(0, self.px(8)))
# END UAVEDUSIM_MITIGATION_CANVAS_SCREEN_PATCH


# BEGIN UAVEDUSIM_QUIZ_SCREEN_PATCH
# Quiz de múltipla escolha (5 perguntas) sobre jamming, ataques e
# mitigação. Fica entre a tela de mitigações e o tutorial. Feedback é
# imediato por pergunta (certo/errado + explicação), com contador de
# acertos visível e uma tela de resultado no final.

# Nota mínima (de 5) pra liberar o tutorial e a simulação.
QUIZ_MIN_CORRECT = 2

QUIZ_QUESTIONS = [
    {
        "q": "O que é um ataque de jamming, segundo o simulador?",
        "options": [
            "Uma falha de hardware do GPS do drone",
            "Uma interferência intencional contra sinais usados por um sistema",
            "Um vírus que infecta o software de navegação",
            "Uma atualização mal aplicada no firmware do PX4",
        ],
        "correct": 1,
        "explain": "Jamming é uma interferência intencional contra sinais utilizados por um sistema — não é falha de hardware, vírus ou firmware.",
    },
    {
        "q": "No Jamming Noise, o que acontece com o sinal de GPS?",
        "options": [
            "O sinal desaparece completamente",
            "O sinal continua chegando, mas as medições ficam degradadas por ruído",
            "O sinal é substituído por coordenadas fixas",
            "O sinal passa a vir de um satélite falso",
        ],
        "correct": 1,
        "explain": "No Noise, o GPS continua aparentemente disponível, mas a intensidade do ataque introduz ruído na posição, na altitude, na velocidade e nos indicadores de qualidade do sinal.",
    },
    {
        "q": "Por que o Jamming Blackout não tem um campo de \"intensidade\" pra configurar?",
        "options": [
            "Porque a intensidade é sempre fixa em 100",
            "Porque o ataque é binário — o sinal simplesmente para de ser publicado por completo",
            "Porque o blackout usa a mesma intensidade do Noise",
            "Porque o blackout não afeta o GPS, só a câmera",
        ],
        "correct": 1,
        "explain": "O blackout é um ataque tudo-ou-nada: enquanto está ativo, a publicação de atualizações de GPS é interrompida por completo — não existe \"grau\" de blackout.",
    },
    {
        "q": "O que a intensidade do Jamming Pulse controla?",
        "options": [
            "Só a cor do ícone do ataque na interface",
            "O tempo de bloqueio do GPS a cada pulso e a distância do ruído nas fases de transição",
            "Apenas a velocidade de rotação do drone",
            "O número de satélites simulados",
        ],
        "correct": 1,
        "explain": "A intensidade do Pulse (entre 0.1 e 1.0) define tanto o tempo de bloqueio total do GPS a cada pulso quanto a distância do ruído nas fases de subida e decaimento do sinal.",
    },
    {
        "q": "Qual é a função do filtro de Kalman na mitigação do Jamming Noise?",
        "options": [
            "Bloquear completamente o GPS até o ataque acabar",
            "Comparar cada medição de GPS com uma previsão do estado esperado do VANT, reduzindo a influência de leituras muito divergentes",
            "Trocar o GPS por uma câmera automaticamente",
            "Aumentar a intensidade do ataque pra facilitar a detecção",
        ],
        "correct": 1,
        "explain": "O filtro de Kalman compara a medição recebida com uma previsão do comportamento esperado do VANT; leituras muito distantes do previsto têm sua influência reduzida.",
    },
]


def _uavedusim_build_shuffled_quiz():
    """Gera uma cópia das perguntas com as alternativas embaralhadas,
    pra resposta certa não ficar sempre na mesma posição entre uma
    tentativa e outra do quiz.
    """
    shuffled = []

    for question in QUIZ_QUESTIONS:
        pairs = list(enumerate(question["options"]))
        random.shuffle(pairs)

        new_options = [text for _old_idx, text in pairs]
        new_correct = next(
            i for i, (old_idx, _text) in enumerate(pairs) if old_idx == question["correct"]
        )

        shuffled.append({
            "q": question["q"],
            "options": new_options,
            "correct": new_correct,
            "explain": question["explain"],
        })

    return shuffled


def _uavedusim_quiz_option_metrics(app, w, s, text):
    pad_x = max(app.px(13), int(14 * s))
    pad_y = max(app.px(10), int(11 * s))
    icon_w = max(app.px(22), int(24 * s))
    text_w = max(app.px(120), w - pad_x * 2 - icon_w)
    font = _uj_font(app, 10.8 * s)
    text_h = _uj_measure_text_height(app, text, font, text_w)

    return {
        "pad_x": pad_x, "pad_y": pad_y, "icon_w": icon_w, "text_w": text_w,
        "font": font, "text_h": text_h,
        "height": max(app.px(40), pad_y * 2 + text_h),
    }


def _uavedusim_draw_quiz_option(canvas, app, x, y, w, s, text, state, tag=None):
    m = _uavedusim_quiz_option_metrics(app, w, s, text)
    h = m["height"]

    if state == "correct":
        border, bg, text_color, border_w = OK, _uavedusim_blend(BG, OK, 0.14), OK, 2
    elif state == "wrong":
        border, bg, text_color, border_w = DANGER, _uavedusim_blend(BG, DANGER, 0.14), DANGER, 2
    else:
        border, bg, text_color, border_w = BORDER, _uavedusim_blend(BG, SURFACE, 0.18), MUTED, 1

    _uj_rr(canvas, x, y, x + w, y + h, app.px(8), fill=bg, outline=border, width=border_w, tags=tag)

    tx = x + m["pad_x"]
    ty = y + m["pad_y"]
    _uj_text(canvas, tx, ty, text, m["font"], text_color, width=m["text_w"], tags=tag)

    if state == "correct":
        canvas.create_text(
            x + w - m["pad_x"], y + h / 2, text="✓", fill=OK,
            font=_uj_font(app, 13 * s, "bold"), anchor="e", tags=tag,
        )
    elif state == "wrong":
        canvas.create_text(
            x + w - m["pad_x"], y + h / 2, text="✕", fill=DANGER,
            font=_uj_font(app, 13 * s, "bold"), anchor="e", tags=tag,
        )

    return h


def _uavedusim_show_quiz_screen(self):
    self.page = "quiz_info"
    self.back_callback = lambda: _uavedusim_show_mitigation_screen(self)
    self.selected_callback = None
    self.clear_content()
    self.set_badge("QUIZ")

    total_q = len(QUIZ_QUESTIONS)

    if not hasattr(self, "_quiz_data") or len(getattr(self, "_quiz_data", [])) != total_q:
        self._quiz_data = _uavedusim_build_shuffled_quiz()
    if not hasattr(self, "_quiz_index"):
        self._quiz_index = 0
    if not hasattr(self, "_quiz_answers") or len(getattr(self, "_quiz_answers", [])) != total_q:
        self._quiz_answers = [None] * total_q
    if not hasattr(self, "_quiz_finished"):
        self._quiz_finished = False

    footer = tk.Frame(self.content, bg=SURFACE)
    footer.pack(
        side="bottom",
        fill="x",
        padx=self.CONTENT_PAD,
        pady=(self.FOOTER_TOP_PAD, self.FOOTER_BOTTOM_PAD),
    )

    outer = tk.Frame(self.content, bg=SURFACE)
    outer.pack(
        fill="both",
        expand=True,
        padx=self.CONTENT_PAD,
        pady=(self.HEADER_TOP_PAD, 0),
    )

    canvas, scrollbar = _uavedusim_make_scrollable_canvas(self, outer)
    self._quiz_scroll_enabled = False

    def draw(_event=None):
        canvas.delete("all")

        width = max(canvas.winfo_width(), 1)
        visible_h = max(canvas.winfo_height(), 1)
        s = _uj_clamp(width / self.px(940), .82, 1.04)

        x = max(self.px(4), int(width * .008))
        w = max(self.px(420), width - 2 * x)
        y = self.px(4)

        card_bg = _uavedusim_blend(BG, SURFACE2, 0.65)
        inner = max(self.px(12), int(14 * s))
        content_x = x + inner
        content_w = w - 2 * inner
        yy = y + inner

        header_icon_x = content_x + 18 * s
        _uj_icon(canvas, "info", header_icon_x, yy + 16 * s, 32 * s, ACCENT, width=max(2, int(2.5 * s)), app=self)

        title_x = content_x + 44 * s
        title_font = _uj_font(self, 15.5 * s, "bold")
        _uj_text(canvas, title_x, yy + 2 * s, "Quiz: jamming e mitigação", title_font, TEXT, width=content_w - (title_x - content_x) - self.px(120))

        progress_label = (
            _uavedusim_L("Resultado", "Result") if self._quiz_finished
            else _uavedusim_L(f"Pergunta {self._quiz_index + 1} de {total_q}", f"Question {self._quiz_index + 1} of {total_q}")
        )
        _uj_text(canvas, content_x + content_w, yy + 4 * s, progress_label, _uj_font(self, 10.5 * s), MUTED, anchor="ne")

        yy += max(self.px(34), int(36 * s))

        gap_seg = max(self.px(4), int(4 * s))
        seg_h = max(self.px(5), int(5 * s))
        seg_w = (content_w - gap_seg * (total_q - 1)) / total_q

        for i in range(total_q):
            if self._quiz_finished or i < self._quiz_index:
                seg_color = OK if self._quiz_answers[i] == self._quiz_data[i]["correct"] else DANGER
            elif i == self._quiz_index:
                seg_color = ACCENT
            else:
                seg_color = SURFACE2

            sx = content_x + i * (seg_w + gap_seg)
            canvas.create_rectangle(sx, yy, sx + seg_w, yy + seg_h, fill=seg_color, outline="")

        yy += seg_h + max(self.px(16), int(18 * s))

        if self._quiz_finished:
            correct_count = sum(
                1 for i in range(total_q) if self._quiz_answers[i] == self._quiz_data[i]["correct"]
            )
            passed = correct_count >= QUIZ_MIN_CORRECT
            self._quiz_passed = passed
            _uavedusim_save_quiz_progress(passed, correct_count, total_q)

            result_title = _uavedusim_L(f"Você acertou {correct_count} de {total_q}", f"You got {correct_count} out of {total_q} right")
            result_font = _uj_font(self, 18 * s, "bold")
            _uj_text(canvas, content_x, yy, result_title, result_font, TEXT, width=content_w)
            yy += _uj_measure_text_height(self, result_title, result_font, content_w) + max(self.px(8), int(9 * s))

            if not passed:
                msg = _uavedusim_L(
                    f"Você precisa acertar pelo menos {QUIZ_MIN_CORRECT} de {total_q} pra liberar o simulador. Reveja as telas anteriores e tente de novo.",
                    f"You need at least {QUIZ_MIN_CORRECT} out of {total_q} correct to unlock the simulator. Review the earlier screens and try again.",
                )
                msg_color = DANGER
            elif correct_count == total_q:
                msg = _uavedusim_translate("Mandou muito bem! Você entendeu os conceitos principais de jamming e mitigação. Simulador liberado.")
                msg_color = OK
            else:
                msg = _uavedusim_translate("Nota suficiente pra liberar o simulador! Vale revisar as telas anteriores nos pontos que você errou.")
                msg_color = OK

            msg_font = _uj_font(self, 11.5 * s)
            _uj_text(canvas, content_x, yy, msg, msg_font, msg_color, width=content_w)
            yy += _uj_measure_text_height(self, msg, msg_font, content_w) + max(self.px(18), int(20 * s))

            btn_h = max(self.px(44), int(46 * s))
            gap_btn = max(self.px(10), int(11 * s))
            btn_w = (content_w - gap_btn) / 2

            _uj_rr(
                canvas, content_x, yy, content_x + btn_w, yy + btn_h, self.px(10),
                fill=SURFACE2, outline=BORDER, width=1, tags="quiz_redo",
            )
            canvas.create_text(
                content_x + btn_w / 2, yy + btn_h / 2, text="↺ Refazer o quiz",
                fill=TEXT, font=_uj_font(self, 11 * s, "bold"), anchor="center", tags="quiz_redo",
            )

            if passed:
                _ujsa_draw_gradient_pill(
                    canvas, content_x + btn_w + gap_btn, yy, btn_w, btn_h, btn_h / 2,
                    OK, "#94e2d5", tags="quiz_continue",
                )
                canvas.create_text(
                    content_x + btn_w + gap_btn + btn_w / 2, yy + btn_h / 2, text="Ver como usar o simulador 󰁕",
                    fill="#11111b", font=_uj_font(self, 10.2 * s, "bold"), anchor="center", tags="quiz_continue",
                )
            else:
                _uj_rr(
                    canvas, content_x + btn_w + gap_btn, yy, content_x + content_w, yy + btn_h, btn_h / 2,
                    fill=DISABLED_BG, outline="",
                )
                canvas.create_text(
                    content_x + btn_w + gap_btn + btn_w / 2, yy + btn_h / 2,
                    text=_uavedusim_L(f"Acerte {QUIZ_MIN_CORRECT}/{total_q} pra liberar", f"Get {QUIZ_MIN_CORRECT}/{total_q} to unlock"),
                    fill=DISABLED_FG, font=_uj_font(self, 10.2 * s, "bold"), anchor="center",
                )

            yy += btn_h + inner
        else:
            q = self._quiz_data[self._quiz_index]
            q_font = _uj_font(self, 13.5 * s, "bold")
            _uj_text(canvas, content_x, yy, q["q"], q_font, TEXT, width=content_w)
            yy += _uj_measure_text_height(self, q["q"], q_font, content_w) + max(self.px(14), int(16 * s))

            answered = self._quiz_answers[self._quiz_index]
            gap_opt = max(self.px(8), int(9 * s))

            for idx, opt_text in enumerate(q["options"]):
                if answered is None:
                    state = "idle"
                elif idx == q["correct"]:
                    state = "correct"
                elif idx == answered:
                    state = "wrong"
                else:
                    state = "idle"

                tag = f"quiz_opt_{idx}"
                h = _uavedusim_draw_quiz_option(canvas, self, content_x, yy, content_w, s, opt_text, state, tag=tag)
                yy += h + gap_opt

            yy += max(self.px(2), int(2 * s))

            if answered is not None:
                is_correct = answered == q["correct"]
                box_color = OK if is_correct else DANGER
                box_bg = _uavedusim_blend(BG, box_color, 0.14)
                box_border = _uavedusim_blend(BG, box_color, 0.35)

                explain_text = (_uavedusim_L("Isso mesmo! ", "That's right! ") if is_correct else _uavedusim_L("Quase! ", "Almost! ")) + _uavedusim_translate(q["explain"])
                explain_font = _uj_font(self, 10.6 * s)
                explain_w = content_w - max(self.px(30), int(32 * s))
                explain_h = _uj_measure_text_height(self, explain_text, explain_font, explain_w)
                box_pad = max(self.px(10), int(11 * s))
                box_h = box_pad * 2 + explain_h

                _uj_rr(
                    canvas, content_x, yy, content_x + content_w, yy + box_h, self.px(8),
                    fill=box_bg, outline=box_border, width=1,
                )
                _uj_text(
                    canvas, content_x + max(self.px(14), int(15 * s)), yy + box_pad,
                    explain_text, explain_font, box_color, width=explain_w,
                )
                yy += box_h + max(self.px(14), int(16 * s))

            correct_so_far = sum(
                1 for i in range(self._quiz_index) if self._quiz_answers[i] == self._quiz_data[i]["correct"]
            )
            answered_so_far = self._quiz_index

            if answered is not None:
                answered_so_far += 1
                if answered == q["correct"]:
                    correct_so_far += 1

            counter_text = _uavedusim_L(
                f"{correct_so_far} de {answered_so_far} corretas até agora",
                f"{correct_so_far} of {answered_so_far} correct so far",
            )

            btn_h = max(self.px(40), int(42 * s))
            btn_w = max(self.px(170), int(180 * s))

            _uj_text(
                canvas, content_x, yy + btn_h / 2 - self.px(6), counter_text,
                _uj_font(self, 10 * s), BORDER, anchor="w",
            )

            is_last = self._quiz_index == total_q - 1
            next_label = "Ver resultado 󰁕" if is_last else "Próxima pergunta 󰁕"

            if answered is not None:
                _ujsa_draw_gradient_pill(
                    canvas, content_x + content_w - btn_w, yy, btn_w, btn_h, btn_h / 2,
                    ACCENT, LAVENDER, tags="quiz_next",
                )
                next_text_color = "#11111b"
                next_tags = "quiz_next"
            else:
                _uj_rr(
                    canvas, content_x + content_w - btn_w, yy, content_x + content_w, yy + btn_h,
                    btn_h / 2, fill=DISABLED_BG, outline="",
                )
                next_text_color = DISABLED_FG
                next_tags = None

            canvas.create_text(
                content_x + content_w - btn_w / 2, yy + btn_h / 2, text=next_label,
                fill=next_text_color, font=_uj_font(self, 10.8 * s, "bold"),
                anchor="center", tags=next_tags,
            )

            yy += btn_h + inner

        scroll_h = max(yy + self.px(6), visible_h)

        _uj_rr(
            canvas, x, y, x + w, max(y + self.px(420), scroll_h - self.px(4)), self.px(14),
            fill=card_bg, outline=BORDER, width=1, tags="quiz_page_bg",
        )
        canvas.tag_lower("quiz_page_bg")

        canvas.configure(scrollregion=(0, 0, width, scroll_h))
        self._quiz_scroll_enabled = scroll_h > visible_h + 2

        if self._quiz_scroll_enabled:
            try:
                scrollbar.place(
                    relx=1.0, rely=0.0, x=-self.px(3), y=0,
                    anchor="ne", width=self.SCROLLBAR_W, height=visible_h,
                )
                scrollbar.lift()
            except Exception:
                pass
        else:
            try:
                scrollbar.place_forget()
            except Exception:
                pass
            canvas.yview_moveto(0)

        if self._quiz_finished:
            for t in ("quiz_redo", "quiz_continue"):
                canvas.tag_unbind(t, "<Button-1>")
                canvas.tag_unbind(t, "<Enter>")
                canvas.tag_unbind(t, "<Leave>")

            canvas.tag_bind("quiz_redo", "<Button-1>", lambda _e: redo_quiz())
            canvas.tag_bind("quiz_redo", "<Enter>", lambda _e: canvas.configure(cursor="hand2"))
            canvas.tag_bind("quiz_redo", "<Leave>", lambda _e: canvas.configure(cursor=""))

            if passed:
                canvas.tag_bind("quiz_continue", "<Button-1>", lambda _e: _uavedusim_show_tutorial_screen(self))
                canvas.tag_bind("quiz_continue", "<Enter>", lambda _e: canvas.configure(cursor="hand2"))
                canvas.tag_bind("quiz_continue", "<Leave>", lambda _e: canvas.configure(cursor=""))
        else:
            q_now = self._quiz_data[self._quiz_index]

            # Desvincula sempre primeiro: como os binds ficam presos ao
            # nome da tag (não ao item desenhado), sem isso uma opção
            # já respondida continuaria clicável depois do redraw.
            for idx in range(len(q_now["options"])):
                t = f"quiz_opt_{idx}"
                canvas.tag_unbind(t, "<Button-1>")
                canvas.tag_unbind(t, "<Enter>")
                canvas.tag_unbind(t, "<Leave>")
            canvas.tag_unbind("quiz_next", "<Button-1>")
            canvas.tag_unbind("quiz_next", "<Enter>")
            canvas.tag_unbind("quiz_next", "<Leave>")

            if self._quiz_answers[self._quiz_index] is None:
                for idx in range(len(q_now["options"])):
                    t = f"quiz_opt_{idx}"
                    canvas.tag_bind(t, "<Button-1>", lambda _e, i=idx: select_option(i))
                    canvas.tag_bind(t, "<Enter>", lambda _e: canvas.configure(cursor="hand2"))
                    canvas.tag_bind(t, "<Leave>", lambda _e: canvas.configure(cursor=""))
            else:
                canvas.tag_bind("quiz_next", "<Button-1>", lambda _e: go_next())
                canvas.tag_bind("quiz_next", "<Enter>", lambda _e: canvas.configure(cursor="hand2"))
                canvas.tag_bind("quiz_next", "<Leave>", lambda _e: canvas.configure(cursor=""))

    def select_option(idx):
        self._quiz_answers[self._quiz_index] = idx
        draw()

    def go_next():
        if self._quiz_index < total_q - 1:
            self._quiz_index += 1
        else:
            self._quiz_finished = True
        draw()

    def redo_quiz():
        self._quiz_data = _uavedusim_build_shuffled_quiz()
        self._quiz_index = 0
        self._quiz_answers = [None] * total_q
        self._quiz_finished = False
        draw()

    canvas.bind("<Configure>", draw)
    _uavedusim_bind_canvas_wheel_scroll(canvas, lambda: getattr(self, "_quiz_scroll_enabled", False))
    self.root.after_idle(draw)
    tk.Frame(footer, bg=BORDER, height=1).pack(fill="x", pady=(0, self.px(10)))

    left_btns = tk.Frame(footer, bg=SURFACE)
    left_btns.pack(side="left")

    _uavedusim_footer_back_button(
        self, left_btns, lambda: _uavedusim_show_mitigation_screen(self),
    ).pack(side="left")

    right_btns = tk.Frame(footer, bg=SURFACE)
    right_btns.pack(side="right")

    _uavedusim_footer_close_button(
        self, right_btns, self.close_app,
    ).pack(side="left", padx=(0, self.px(8)))
# END UAVEDUSIM_QUIZ_SCREEN_PATCH


# BEGIN UAVEDUSIM_TUTORIAL_SCREEN_PATCH
# Tela "Como usar o painel de ataques": passo a passo de takeoff ->
# destino -> mitigação (opcional, antes do ataque) -> aplicar ataque,
# mais uma explicação técnica de como a intensidade de Noise/Pulse
# funciona (baseada na fórmula real do GZBridge). Fica entre a tela de
# mitigações e o início de fato da simulação.

def _uavedusim_tutorial_step_metrics(app, w, s, title, body):
    pad = max(app.px(12), int(13 * s))
    text_w = max(app.px(120), w - pad * 2)

    title_font = _uj_font(app, 11.8 * s, "bold")
    body_font = _uj_font(app, 10.2 * s)

    title_h = _uj_measure_text_height(app, title, title_font, text_w)
    body_h = _uj_measure_text_height(app, body, body_font, text_w)
    gap2 = max(app.px(4), int(4 * s))

    h = pad * 2 + title_h + gap2 + body_h

    return {
        "pad": pad, "text_w": text_w,
        "title_font": title_font, "body_font": body_font,
        "title_h": title_h, "body_h": body_h, "gap2": gap2,
        "height": max(app.px(50), h),
    }


def _uavedusim_draw_tutorial_step(canvas, app, x, y, w, s, number, title, body, accent=ACCENT, title_color=TEXT, tag=None):
    title = _uavedusim_translate(title)
    body = _uavedusim_translate(body)
    full_title = f"{number}. {title}"
    m = _uavedusim_tutorial_step_metrics(app, w, s, full_title, body)
    h = m["height"]

    _uj_rr(
        canvas, x, y, x + w, y + h, app.px(10),
        fill=_uavedusim_blend(BG, SURFACE, 0.22), outline=BORDER, width=1, tags=tag,
    )

    tx = x + m["pad"]
    ty = y + m["pad"]

    _uj_text(canvas, tx, ty, full_title, m["title_font"], title_color, width=m["text_w"], tags=tag)
    _uj_text(
        canvas, tx, ty + m["title_h"] + m["gap2"], body, m["body_font"], TEXT,
        width=m["text_w"], tags=tag,
    )

    return h


def _uavedusim_intensity_row_metrics(app, w, s, name, body):
    pad = max(app.px(11), int(12 * s))
    band_pad = max(app.px(4), int(4 * s))
    inset_x = max(app.px(9), int(10 * s))
    inset_top = max(app.px(7), int(8 * s))
    text_w = max(app.px(120), w - pad * 2)

    name_font = _uj_font(app, 12.5 * s, "bold")
    body_font = _uj_font(app, 10.2 * s)

    name_h = _uj_measure_text_height(app, name, name_font, text_w)
    body_h = _uj_measure_text_height(app, body, body_font, text_w)
    gap = max(app.px(6), int(6 * s))

    header_h = inset_top + band_pad * 2 + name_h

    return {
        "pad": pad, "band_pad": band_pad, "inset_x": inset_x, "inset_top": inset_top,
        "text_w": text_w, "name_font": name_font, "body_font": body_font,
        "name_h": name_h, "body_h": body_h, "gap": gap, "header_h": header_h,
        "height": max(app.px(46), header_h + gap + body_h + pad),
    }


def _uavedusim_draw_intensity_row(canvas, app, x, y, w, s, name, color, body, tag=None):
    body = _uavedusim_translate(body)
    m = _uavedusim_intensity_row_metrics(app, w, s, name, body)
    h = m["height"]
    radius = app.px(9)

    _uj_rr(
        canvas, x, y, x + w, y + h, radius,
        fill=_uavedusim_blend(BG, SURFACE, 0.20), outline=BORDER, width=1, tags=tag,
    )

    band_top = y + m["inset_top"]
    band_bottom = band_top + m["band_pad"] * 2 + m["name_h"]
    band_radius = (band_bottom - band_top) / 2

    _uj_rr(
        canvas, x + m["inset_x"], band_top, x + w - m["inset_x"], band_bottom, band_radius,
        fill=_uavedusim_blend(BG, color, 0.13), outline="", tags=tag,
    )

    tx = x + m["pad"]

    _uj_text(canvas, tx, band_top + m["band_pad"], name, m["name_font"], color, width=m["text_w"], tags=tag)
    _uj_text(
        canvas, tx, y + m["header_h"] + m["gap"], body, m["body_font"], TEXT,
        width=m["text_w"], tags=tag,
    )

    return h


def _uavedusim_show_tutorial_screen(self):
    self.page = "tutorial_info"
    self.back_callback = lambda: _uavedusim_show_quiz_screen(self)
    self.selected_callback = None
    self.clear_content()
    self.set_badge("TUTORIAL")

    TUT_TITLE_COLOR = ACCENT
    TUT_CTA_COLOR = "#94e2d5"
    TUT_CTA_COLOR_2 = "#89dceb"
    TUT_WARN_COLOR = "#f9e2af"

    footer = tk.Frame(self.content, bg=SURFACE)
    footer.pack(
        side="bottom",
        fill="x",
        padx=self.CONTENT_PAD,
        pady=(self.FOOTER_TOP_PAD, self.FOOTER_BOTTOM_PAD),
    )

    outer = tk.Frame(self.content, bg=SURFACE)
    outer.pack(
        fill="both",
        expand=True,
        padx=self.CONTENT_PAD,
        pady=(self.HEADER_TOP_PAD, 0),
    )

    canvas, scrollbar = _uavedusim_make_scrollable_canvas(self, outer)
    self._tutorial_scroll_enabled = False

    steps = [
        (
            "1", ACCENT, ACCENT,
            "Envie a decolagem",
            "Clique em \"Enviar commander takeoff\". O painel de ataques fica bloqueado até o PX4 confirmar que o VANT decolou de verdade.",
        ),
        (
            "2", "#74c7ec", "#74c7ec",
            "Envie o destino",
            "Distância, rotação e altitude já vêm preenchidos com valores padrão (350 m, 0°, 2.5 m) — mas são campos editáveis, dá pra ajustar antes de enviar.",
        ),
        (
            "3", TUT_WARN_COLOR, TUT_WARN_COLOR,
            "Quer comparar com e sem mitigação? Ative-a antes do ataque",
            "A chave \"Mitigação\" só passa a agir sobre as medições geradas depois de ser ligada. Ativando antes de lançar o ataque, a diferença entre com e sem fica clara desde o primeiro instante.",
        ),
        (
            "4", "#f5c2e7", "#f5c2e7",
            "Escolha o ataque, ajuste a intensidade e clique em \"Aplicar\"",
            "Jamming Noise e Jamming Pulse já vêm com um valor de intensidade padrão preenchido — mas esse valor é totalmente personalizável, edite antes de aplicar.",
        ),
    ]

    intensity_items = [
        (
            "Noise", "#fab387",
            "A intensidade entra direto na fórmula do ruído: o desvio horizontal (norte/leste) e de velocidade variam em torno de intensidade × 10 metros; o desvio de altitude, em torno de intensidade × 30 metros. Com a intensidade padrão (80), isso já significa desvios de até ~800 m na posição horizontal — um efeito bem visível.",
        ),
        (
            "Pulse", "#94e2d5",
            "A intensidade (limitada entre 0.1 e 1.0) tem duas funções: define o tempo de bloqueio total do GPS a cada pulso (intensidade × 10 segundos — 0.3 → 3 segundos) e também a distância do ruído nas fases de transição (subida e decaimento do sinal): intensidade × 1000 metros — 0.3 → até ~300 m de desvio. Use valores entre 0.1 e 1.0 — acima disso a diferença já não é mais visível, porque o PX4 aciona o failsafe de perda de GPS bem antes de o bloqueio terminar, e prolongar o bloqueio não muda o que se observa no comportamento do VANT.",
        ),
        (
            "Blackout", "#f38ba8",
            "Não tem campo de intensidade porque o ataque é binário: o sinal de GPS simplesmente para de ser publicado por completo. Não existe \"grau\" de blackout.",
        ),
    ]

    def draw(_event=None):
        canvas.delete("all")

        width = max(canvas.winfo_width(), 1)
        visible_h = max(canvas.winfo_height(), 1)
        s = _uj_clamp(width / self.px(940), .82, 1.04)

        x = max(self.px(4), int(width * .008))
        w = max(self.px(420), width - 2 * x)
        y = self.px(4)

        card_bg = _uavedusim_blend(BG, SURFACE2, 0.65)
        inner = max(self.px(10), int(12 * s))
        content_x = x + inner
        content_w = w - 2 * inner
        yy = y + inner

        header_icon_x = content_x + 25 * s
        _uj_icon(
            canvas, "monitor", header_icon_x, yy + 25 * s, 46 * s,
            TUT_TITLE_COLOR, width=max(2, int(3 * s)), app=self,
        )

        title_x = content_x + 64 * s
        title = "Como usar o painel de ataques"
        title_font = _uj_font(self, 19.5 * s, "bold")
        _uj_text(canvas, title_x, yy + 4 * s, title, title_font, TEXT, width=content_w - (title_x - content_x))

        subtitle = "Passo a passo pra lançar um ataque de jamming e, se quiser, comparar com a mitigação ativada."
        subtitle_font = _uj_font(self, 12 * s)
        subtitle_w = content_w - (title_x - content_x)
        subtitle_y = yy + 54 * s
        _uj_text(canvas, title_x, subtitle_y, subtitle, subtitle_font, TUT_TITLE_COLOR, width=subtitle_w)

        subtitle_h = _uj_measure_text_height(self, subtitle, subtitle_font, subtitle_w)
        yy = subtitle_y + subtitle_h + max(self.px(16), int(18 * s))

        gap = max(self.px(9), int(10 * s))

        for num, accent, title_color, title_txt, body_txt in steps:
            h = _uavedusim_draw_tutorial_step(
                canvas, self, content_x, yy, content_w, s, num, title_txt, body_txt,
                accent=accent, title_color=title_color,
            )
            yy += h + gap

        yy += max(self.px(2), int(2 * s))

        section_title = "Entendendo a intensidade de cada ataque"
        section_font = _uj_font(self, 13.5 * s, "bold")
        _uj_text(canvas, content_x, yy, section_title, section_font, OK, width=content_w)
        yy += _uj_measure_text_height(self, section_title, section_font, content_w) + max(self.px(9), int(10 * s))

        for name, color, body_txt in intensity_items:
            h = _uavedusim_draw_intensity_row(canvas, self, content_x, yy, content_w, s, name, color, body_txt)
            yy += h + gap

        cta_h = max(self.px(48), int(50 * s))
        _ujsa_draw_gradient_pill(
            canvas, content_x, yy, content_w, cta_h, cta_h / 2,
            TUT_CTA_COLOR, TUT_CTA_COLOR_2, tags="tutorial_cta",
        )
        canvas.create_text(
            content_x + content_w / 2, yy + cta_h / 2,
            text="\uf04b  Iniciar simulação do UAVEDUSIM",
            fill="#11111b", font=_uj_font(self, 12.8 * s, "bold"),
            anchor="center", tags="tutorial_cta",
        )
        yy += cta_h + inner

        scroll_h = max(yy + self.px(6), visible_h)

        _uj_rr(
            canvas, x, y, x + w, max(y + self.px(520), scroll_h - self.px(4)), self.px(14),
            fill=card_bg, outline=BORDER, width=1, tags="tutorial_page_bg",
        )
        canvas.tag_lower("tutorial_page_bg")

        canvas.configure(scrollregion=(0, 0, width, scroll_h))
        self._tutorial_scroll_enabled = scroll_h > visible_h + 2

        if self._tutorial_scroll_enabled:
            try:
                scrollbar.place(
                    relx=1.0, rely=0.0, x=-self.px(3), y=0,
                    anchor="ne", width=self.SCROLLBAR_W, height=visible_h,
                )
                scrollbar.lift()
            except Exception:
                pass
        else:
            try:
                scrollbar.place_forget()
            except Exception:
                pass
            canvas.yview_moveto(0)

        canvas.tag_bind("tutorial_cta", "<Button-1>", lambda _e: _uavedusim_home_run_choice(self, "Executar UAVEDUSIM"))
        canvas.tag_bind("tutorial_cta", "<Enter>", lambda _e: canvas.configure(cursor="hand2"))
        canvas.tag_bind("tutorial_cta", "<Leave>", lambda _e: canvas.configure(cursor=""))

    canvas.bind("<Configure>", draw)
    _uavedusim_bind_canvas_wheel_scroll(canvas, lambda: getattr(self, "_tutorial_scroll_enabled", False))
    self.root.after_idle(draw)

    tk.Frame(footer, bg=BORDER, height=1).pack(fill="x", pady=(0, self.px(10)))

    left_btns = tk.Frame(footer, bg=SURFACE)
    left_btns.pack(side="left")

    _uavedusim_footer_back_button(
        self, left_btns, lambda: _uavedusim_show_quiz_screen(self),
    ).pack(side="left")

    right_btns = tk.Frame(footer, bg=SURFACE)
    right_btns.pack(side="right")

    _uavedusim_footer_close_button(
        self, right_btns, self.close_app,
    ).pack(side="left", padx=(0, self.px(8)))
# END UAVEDUSIM_TUTORIAL_SCREEN_PATCH


# ============================================================================
# Dicionário de tradução PT -> EN -------------------------------------------
# Cobre a navegação/chrome do app, diálogos e as telas educativas (home,
# menus, tela de ataques, "O que é jamming?", ataques simulados,
# mitigações, quiz e tutorial). Mensagens dinâmicas com valores
# interpolados (contadores, distâncias, timers) são tratadas direto no
# código com _uavedusim_L(); o que está aqui é o texto estático.
# ============================================================================
_UAVEDUSIM_I18N_EN.update({
    # --- Navegação / chrome geral -------------------------------------
    "MAIN MENU": "MAIN MENU",
    "  MAIN MENU  ": "  MAIN MENU  ",
    "MANUTENÇÃO": "MAINTENANCE",
    "Voltar": "Back",
    "Fechar": "Close",
    "Cancelar": "Cancel",
    "Limpar": "Clear",
    "Valor": "Value",
    "Status": "Status",
    "Aviso": "Warning",
    "Erro": "Error",
    "ERRO": "ERROR",
    "STATUS": "STATUS",
    "ATAQUES": "ATTACKS",
    "ATAQUES DO SIMULADOR": "SIMULATOR ATTACKS",
    "PROCESSOS ENCERRADOS": "PROCESSES STOPPED",
    "ATTACK CONTROL": "ATTACK CONTROL",
    "ESCOLHER DRONE": "CHOOSE DRONE",
    "ESCOLHER MUNDO": "CHOOSE WORLD",
    "AGUARDANDO PX4/GAZEBO": "WAITING FOR PX4/GAZEBO",
    "MITIGAÇÕES": "MITIGATIONS",
    "QUIZ": "QUIZ",
    "TUTORIAL": "TUTORIAL",
    "O QUE É JAMMING?": "WHAT IS JAMMING?",
    "UAVEDUSIM": "UAVEDUSIM",
    "UAV": "UAV",
    "Drone / VANT": "Drone / UAV",
    "Clique em uma página para navegar": "Click a page to navigate",
    "↑↓ navegar · Enter confirmar · Esc fechar": "↑↓ navigate · Enter confirm · Esc close",

    # --- Tela inicial (home) -------------------------------------------
    "Simulador de ataques de jamming em VANTs": "GPS jamming attack simulator for UAVs",
    "Ambiente seguro para estudar como falhas de GPS/GNSS afetam a navegação de "
    "um VANT, usando PX4, Gazebo e QGroundControl — sem risco de interferência real.":
        "A safe environment to study how GPS/GNSS failures affect a UAV's navigation, "
        "using PX4, Gazebo and QGroundControl — with no risk of real interference.",
    "O que é jamming? Aprenda e desafie-se no quiz": "What is jamming? Learn and test yourself in the quiz",
    "Iniciar simulação": "Start simulation",
    "Limpar builds": "Clear builds",
    "Encerrar processos": "Stop processes",
    "Se a simulação não iniciar ou não iniciar corretamente, use os botões de "
    "manutenção acima e tente novamente.":
        "If the simulation doesn't start, or doesn't start correctly, use the "
        "maintenance buttons above and try again.",
    "Ver como usar o simulador \U000f0055": "See how to use the simulator \U000f0055",
    "Faça o quiz para liberar o simulador   ": "Take the quiz to unlock the simulator   ",

    # --- Seleção de drone / mundo (menus) -------------------------------
    "Escolha o modelo de drone para a simulação": "Choose the drone model for the simulation",
    "Escolha o mundo de simulação onde o drone vai voar": "Choose the simulation world where the drone will fly",
    "Selecione o VANT que será usado no experimento. "
    "O modelo define a estrutura do drone e os sensores disponíveis na simulação.":
        "Select the UAV that will be used in the experiment. "
        "The model defines the drone's structure and the sensors available in the simulation.",
    "Selecione o ambiente do Gazebo onde o voo será executado. "
    "O mundo define o cenário físico usado durante a missão simulada.":
        "Select the Gazebo environment where the flight will take place. "
        "The world defines the physical scenario used during the simulated mission.",
    "Nenhum modelo de drone foi encontrado.": "No drone model was found.",
    "Nenhum mundo de simulação foi encontrado.": "No simulation world was found.",
    "Modelo invalido selecionado.": "Invalid model selected.",
    "Mundo invalido selecionado.": "Invalid world selected.",

    # --- Tela de espera (PX4/Gazebo abrindo) ----------------------------
    "Aguardando PX4/Gazebo abrir...": "Waiting for PX4/Gazebo to open...",
    "Aguarde enquanto PX4 e Gazebo iniciam. O menu de ataques abre automaticamente "
    "quando a simulação estiver pronta para decolar.":
        "Wait while PX4 and Gazebo start up. The attack menu opens automatically "
        "once the simulation is ready for takeoff.",
    "Cancelando carregamento...\nEncerrando terminais e janelas abertas pelo UAVEDUSIM.":
        "Cancelling loading...\nClosing terminals and windows opened by UAVEDUSIM.",
    "Reiniciando simulação": "Restarting simulation",
    "Simulação reiniciada": "Simulation restarted",
    "Todos os processos PX4/Gazebo da simulação foram encerrados.": "All PX4/Gazebo processes from the simulation were stopped.",

    # --- Tela de ataques (formulário) -----------------------------------
    "Ataques GPS Jamming": "GPS jamming attacks",
    "Envie takeoff e destino. Padrão: dist. 350 m, rot. 0 graus, alt. 2.5 m.":
        "Send takeoff and destination. Default: dist. 350 m, rot. 0 deg, alt. 2.5 m.",
    "1. Decolagem": "1. Takeoff",
    "2. Destino": "2. Destination",
    "Distância (m)": "Distance (m)",
    "Rotação (graus)": "Rotation (deg)",
    "Alt. rel. (m)": "Rel. alt. (m)",
    "Distância pendente": "Distance pending",
    "Enviar commander takeoff": "Send commander takeoff",
    "Takeoff enviado": "Takeoff sent",
    "Takeoff detectado": "Takeoff detected",
    "Takeoff não enviado": "Takeoff not sent",
    "Enviar destino": "Send destination",
    "Reenviar destino": "Resend destination",
    "Destino bloqueado": "Destination locked",
    "Destino bloqueado ate o PX4 confirmar Takeoff detected.": "Destination locked until PX4 confirms Takeoff detected.",
    "Destino liberado": "Destination released",
    "Destino invalido": "Invalid destination",
    "Destino invalido. Use números em Distância, Rotação e Altitude. Exemplo: 350, 0, 2.5":
        "Invalid destination. Use numbers for Distance, Rotation and Altitude. Example: 350, 0, 2.5",
    "Distância precisa ser maior que zero.": "Distance must be greater than zero.",
    "Altitude relativa precisa ser maior que zero. Exemplo: 2.5": "Relative altitude must be greater than zero. Example: 2.5",
    "Envie commander takeoff antes do destino.": "Send commander takeoff before the destination.",
    "Tipo de ataque": "Attack type",
    "Fonte de jamming": "Jamming source",
    "Informe a intensidade do Jamming Noise. Exemplo: 80": "Enter the Jamming Noise intensity. Example: 80",
    "Informe a intensidade do Jamming Pulse. Exemplo: 0.3": "Enter the Jamming Pulse intensity. Example: 0.3",
    "Mitigação": "Mitigation",
    "Ativa/desativa a mitigação do simulador.": "Turns the simulator's mitigation on/off.",
    "Mitigação ATIVADA": "Mitigation ENABLED",
    "Mitigação DESATIVADA": "Mitigation DISABLED",
    "Falha ao alterar mitigação": "Failed to change mitigation",
    "Falha ao alterar mitigação. Veja o log.": "Failed to change mitigation. See the log.",
    "Falha ao aplicar ataque": "Failed to apply attack",
    "Falha ao desligar jamming": "Failed to turn off jamming",
    "Falha ao enviar destino": "Failed to send destination",
    "Jamming desligado": "Jamming turned off",
    "Desligar jamming": "Turn off jamming",
    "Reiniciar simulação": "Restart simulation",
    "Log limpo": "Log cleared",
    "O histórico de eventos foi apagado.": "The event history was cleared.",
    "Ataques simulados": "Simulated attacks",
    "Ataques bloqueados. Envie commander takeoff e depois o destino.":
        "Attacks locked. Send commander takeoff and then the destination.",
    "Ataques bloqueados. Aguardando timer após o takeoff antes de liberar o destino.":
        "Attacks locked. Waiting for the timer after takeoff before releasing the destination.",
    "Ataques bloqueados. Envie o destino padrão: distância + rotação.":
        "Attacks locked. Send the default destination: distance + rotation.",
    "Takeoff e destino enviados. Ataques liberados.": "Takeoff and destination sent. Attacks unlocked.",
    "Não consegui enviar comando ao PX4. Verifique se o terminal PX4 ainda está aberto.":
        "Couldn't send the command to PX4. Check whether the PX4 terminal is still open.",

    # --- Tooltip dos ataques (ícone "i") ---------------------------------
    "Jamming Noise: o GPS parece continuar funcionando, mas as medições de "
    "posição, velocidade e altitude chegam degradadas por ruído. Quanto maior "
    "a intensidade (ex: 80), maior a diferença entre a posição estimada e a "
    "posição real do VANT.":
        "Jamming Noise: GPS appears to keep working, but position, velocity and "
        "altitude readings arrive degraded by noise. The higher the intensity "
        "(e.g. 80), the bigger the gap between the estimated and the real position of the UAV.",
    "Jamming Blackout: bloqueia por completo o sinal de GPS/GNSS. O VANT "
    "deixa de receber novas atualizações de posição, podendo interromper a "
    "navegação autônoma ou acionar mecanismos de seguranca (failsafe).":
        "Jamming Blackout: completely blocks the GPS/GNSS signal. The UAV "
        "stops receiving new position updates, which can interrupt autonomous "
        "navigation or trigger safety mechanisms (failsafe).",
    "Jamming Pulse: ataque intermitente que alterna entre degradação, "
    "bloqueio e recuperação parcial do GPS. A intensidade (ex: 0.3) afeta o "
    "padrão do ciclo, causando oscilações na navegação do VANT.":
        "Jamming Pulse: intermittent attack that alternates between degradation, "
        "blocking and partial recovery of the GPS. Intensity (e.g. 0.3) affects "
        "the cycle pattern, causing oscillations in the UAV's navigation.",

    # --- Descrições dos ataques (base, sem interpolação) -----------------
    "Neste ataque, ruído é adicionado às leituras de GPS do drone, "
    "reduzindo a precisão da navegação. Valores maiores produzem "
    "interferências mais intensas. Exemplo de valor: 80.":
        "In this attack, noise is added to the drone's GPS readings, "
        "reducing navigation accuracy. Higher values produce stronger "
        "interference. Example value: 80.",
    "Neste ataque, o sinal de GPS é completamente interrompido, "
    "impedindo que o drone receba informações de posicionamento. "
    "Este ataque não utiliza parâmetro de intensidade.":
        "In this attack, the GPS signal is completely interrupted, "
        "preventing the drone from receiving positioning information. "
        "This attack doesn't use an intensity parameter.",
    "Neste ataque, a interferência é aplicada de forma intermitente, "
    "alternando entre períodos com e sem jamming. Isso simula falhas "
    "temporárias no sinal de GPS. Exemplo de valor: 0.3.":
        "In this attack, interference is applied intermittently, "
        "alternating between periods with and without jamming. This simulates "
        "temporary failures in the GPS signal. Example value: 0.3.",

    # --- Descrições de efeito / observação esperada ----------------------
    "adiciona ruído aleatorio as coordenadas de GPS lidas pelo drone, fazendo a "
    "posição estimada 'tremer' e se afastar aos poucos da posição real.":
        "adds random noise to the GPS coordinates read by the drone, making the "
        "estimated position 'shake' and gradually drift from the real position.",
    "bloqueia completamente o sinal de GPS, deixando o drone sem nenhuma leitura "
    "de posição válida.":
        "completely blocks the GPS signal, leaving the drone with no valid "
        "position reading at all.",
    "liga e desliga o jamming em pulsos, causando perda intermitente de posição "
    "GPS (o drone alterna entre 'ver' e 'não ver' sua posição).":
        "turns jamming on and off in pulses, causing intermittent loss of GPS "
        "position (the drone alternates between 'seeing' and 'not seeing' its position).",
    "interfere no sinal de GPS recebido pelo drone.": "interferes with the GPS signal received by the drone.",
    "Espere ver a posição estimada 'tremendo' na telemetria, com pequenos desvios "
    "da rota planejada, mesmo sem perder o GPS por completo.":
        "Expect to see the estimated position 'shaking' in telemetry, with small "
        "deviations from the planned route, even without fully losing GPS.",
    "Espere ver o GPS sem fix (sem posição válida); o drone pode parar de responder "
    "corretamente a comandos de posição enquanto o sinal não volta.":
        "Expect to see GPS without a fix (no valid position); the drone may stop "
        "responding correctly to position commands until the signal comes back.",
    "Espere ver o GPS alternando entre 'com posição' e 'sem posição' repetidamente, "
    "com saltos na posição estimada a cada vez que o sinal retorna. Esses saltos não "
    "são necessariamente pequenos: quanto mais tempo o GPS ficar fora, maior pode ser "
    "a diferença entre a posição estimada e a posição real quando o sinal volta.":
        "Expect to see GPS alternating between 'has a position' and 'no position' "
        "repeatedly, with jumps in the estimated position every time the signal "
        "returns. These jumps aren't necessarily small: the longer GPS stays out, "
        "the bigger the gap can be between the estimated and real position when the signal returns.",
    "Acompanhe a telemetria para ver o efeito do ataque no GPS.": "Watch the telemetry to see the attack's effect on GPS.",
    "proteção ativada contra ataques ao GPS.": "protection against GPS attacks enabled.",
    "o GPS ainda envia leituras, só que ruidosas, então a mitigação usa um filtro de "
    "Kalman que combina essas leituras com o barômetro (para a altitude) e com o "
    "movimento esperado do drone, dando menos peso as medições que fogem muito do "
    "previsto. Isso suaviza o 'tremor' e mantem a posição estimada próxima da real.":
        "GPS still sends readings, just noisy ones, so mitigation uses a Kalman "
        "filter that combines those readings with the barometer (for altitude) and "
        "with the drone's expected motion, giving less weight to readings that stray "
        "too far from expected. This smooths out the 'shaking' and keeps the estimated position close to the real one.",
    "o GPS parou de enviar leituras válidas, então a mitigação usa dead reckoning: "
    "parte da última posição confiável e projeta a posição atual com a velocidade e "
    "direção do drone, apoiada pelo barômetro (altitude) e pela odometria (distância "
    "percorrida), ate o sinal de GPS voltar.":
        "GPS stopped sending valid readings, so mitigation uses dead reckoning: it "
        "starts from the last reliable position and projects the current position "
        "using the drone's speed and heading, backed by the barometer (altitude) and "
        "odometry (distance travelled), until the GPS signal returns.",
    "o sinal de GPS aparece e desaparece, então a mitigação alterna entre as duas "
    "estratégias: usa o filtro de Kalman (com apoio do barômetro) enquanto o sinal "
    "esta presente, mesmo que ruidoso, e passa para dead reckoning com odometria nos "
    "momentos em que o sinal some, evitando saltos bruscos quando o GPS retorna.":
        "the GPS signal comes and goes, so mitigation alternates between the two "
        "strategies: it uses the Kalman filter (backed by the barometer) while the "
        "signal is present, even if noisy, and switches to dead reckoning with "
        "odometry whenever the signal drops, avoiding sudden jumps when GPS returns.",
    "a mitigação está atuando para reduzir o efeito desse ataque.": "mitigation is working to reduce the effect of this attack.",
    "o GPS ruidoso chega direto ao PX4 sem nenhuma correção. O drone pode se desviar "
    "da rota, oscilar de posição e perder estabilidade ate não conseguir alcançar o "
    "destino configurado.":
        "the noisy GPS reaches PX4 directly with no correction at all. The drone "
        "may drift off route, oscillate in position and lose stability to the point "
        "of failing to reach the configured destination.",
    "sem nenhuma leitura válida de GPS chegando ao PX4, o próprio piloto automático "
    "deve acionar failsafe e iniciar um pouso de emergência (RTL/land) usando apenas a "
    "posição estimada disponível. Como essa posição pode estar desatualizada ou incorreta, "
    "o drone corre o risco de pousar em terrenos inadequados, como água, telhados, "
    "estradas ou vegetação densa, em vez de um local seguro.":
        "with no valid GPS reading reaching PX4 at all, the autopilot itself must "
        "trigger failsafe and start an emergency landing (RTL/land) using only the "
        "available estimated position. Since that position may be outdated or "
        "wrong, the drone risks landing on unsuitable terrain, such as water, "
        "rooftops, roads or dense vegetation, instead of a safe spot.",
    "cada vez que o GPS some, o PX4 fica sem leitura válida de posição; dependendo de "
    "quanto tempo o sinal ficar fora, o piloto automático pode acionar failsafe e "
    "iniciar um pouso de emergência em terreno inapropriado (água, telhados, estradas "
    "ou vegetação densa), assim como no blackout. Quando o sinal retorna, ainda "
    "ruidoso, o drone pode sofrer saltos de posição que não são necessariamente "
    "pequenos. Sem mitigação, essa alternancia entre 'sem GPS' e 'GPS ruidoso' aumenta "
    "o risco de desvio de rota e de um pouso de emergência em local inadequado.":
        "every time GPS drops out, PX4 has no valid position reading; depending on "
        "how long the signal stays out, the autopilot may trigger failsafe and "
        "start an emergency landing on unsuitable terrain (water, rooftops, roads or "
        "dense vegetation), just like in a blackout. When the signal returns, still "
        "noisy, the drone may suffer position jumps that aren't necessarily small. "
        "Without mitigation, this back-and-forth between 'no GPS' and 'noisy GPS' "
        "increases the risk of route drift and of an emergency landing in an unsuitable spot.",
    "o GPS corrompido chega direto ao PX4. O drone pode se desviar da rota, perder "
    "estabilidade ou não alcançar o destino configurado.":
        "the corrupted GPS reaches PX4 directly. The drone may drift off route, "
        "lose stability or fail to reach the configured destination.",

    # --- "O que é jamming?" (tela educativa) -----------------------------
    "O que é jamming?": "What is jamming?",
    "Interferência intencional que afeta sinais de navegação e comunicação.":
        "Intentional interference that affects navigation and communication signals.",
    "Jamming é uma interferência intencional contra sinais utilizados por um sistema.":
        "Jamming is intentional interference against signals used by a system.",
    "Em VANTs, pode afetar sinais de GPS/GNSS, telemetria, comunicação de controle e outros canais sem fio usados durante a missão.":
        "In UAVs, it can affect GPS/GNSS signals, telemetry, control links and other wireless channels used during the mission.",
    "Degrada, bloqueia ou desestabiliza": "Degrades, blocks or destabilizes",
    "O objetivo é degradar, bloquear ou tornar instável a recepção desses sinais, prejudicando a capacidade do drone de navegar corretamente.":
        "The goal is to degrade, block or destabilize reception of those signals, harming the drone's ability to navigate correctly.",
    "Impactos no GPS/GNSS": "Impact on GPS/GNSS",
    "Pode causar erros de posição, falta de atualizações de localização ou alternância entre períodos de funcionamento normal e falha.":
        "It can cause position errors, missing location updates, or switching between periods of normal operation and failure.",
    "Compromete a missão": "Compromises the mission",
    "Mesmo sem invadir o software do drone, o atacante pode comprometer a missão ao explorar a dependência do VANT em relação aos sinais externos de navegação.":
        "Even without breaching the drone's software, an attacker can compromise the mission by exploiting the UAV's reliance on external navigation signals.",
    "Nesta interface": "In this interface",
    "Você pode escolher o modelo do drone, o mundo, iniciar PX4/Gazebo e executar cenários de jamming simulados.":
        "You can choose the drone model, the world, start PX4/Gazebo, and run simulated jamming scenarios.",
    "Ataques simulados": "Simulated attacks",
    "Ruído adicionado às medições de GPS/GNSS, fazendo o VANT calcular uma posição diferente da real.":
        "Noise added to GPS/GNSS measurements, making the UAV compute a position different from the real one.",
    "Sinal de GPS/GNSS interrompido: o VANT para de receber atualizações de posição.":
        "GPS/GNSS signal interrupted: the UAV stops receiving position updates.",
    "Alterna entre ruído, bloqueio e recuperação do sinal, causando falhas intermitentes.":
        "Alternates between noise, blocking and signal recovery, causing intermittent failures.",
    "Entenda os Ataques de Jamming Simulados   \uf054": "Learn about the simulated jamming attacks   \uf054",

    # --- Diagrama (satélite / drone / jammer) ----------------------------
    "Satélite GPS": "GPS satellite",
    "sinal GPS": "GPS signal",
    "interferência": "interference",

    # --- Ataques de jamming simulados (tela detalhada) -------------------
    "Ataques de jamming simulados": "Simulated jamming attacks",
    "Entenda como cada ataque afeta a navegação GPS/GNSS do VANT em ambiente simulado.":
        "Understand how each attack affects the UAV's GPS/GNSS navigation in the simulated environment.",
    "O sinal continua chegando, mas as medições se tornam degradadas e menos confiáveis.":
        "The signal keeps arriving, but the readings become degraded and less reliable.",
    "O que acontece": "What happens",
    "O GPS permanece ativo, mas a intensidade do ataque introduz perturbações na posição, na altitude, na velocidade e nos indicadores de qualidade do sinal.":
        "GPS stays active, but the attack's intensity introduces disturbances in position, altitude, velocity and signal-quality indicators.",
    "Por que é perigoso": "Why it's dangerous",
    "O VANT pode estimar posição incorreta, gerando desvios de trajetória e instabilidade na navegação.":
        "The UAV may compute an incorrect position, causing trajectory drift and navigation instability.",
    "Como é simulado": "How it's simulated",
    "Perturbações são adicionadas às medições de GPS/GNSS para reproduzir degradação contínua do sinal.":
        "Disturbances are added to the GPS/GNSS readings to reproduce continuous signal degradation.",
    "O sinal de GPS deixa de ser recebido: enquanto o ataque está ativo, a publicação de atualizações é interrompida por completo.":
        "The GPS signal stops being received: while the attack is active, updates stop being published entirely.",
    "O VANT fica sem novas medições válidas de GPS e perde sua principal referência externa de localização.":
        "The UAV is left with no new valid GPS readings and loses its main external location reference.",
    "Sem posição global válida, o PX4 pode acionar o failsafe padrão e iniciar pouso automático — que pode ocorrer em local inadequado, como sobre água, obstáculos ou áreas urbanas.":
        "Without a valid global position, PX4 may trigger the default failsafe and start an automatic landing — which may happen in an unsuitable spot, such as over water, obstacles or urban areas.",
    "As atualizações de GPS são interrompidas, reproduzindo um cenário de bloqueio total do sinal.":
        "GPS updates are interrupted, reproducing a scenario of total signal blockage.",
    "O ataque alterna entre degradação, bloqueio, recuperação parcial e retorno a um sinal aparentemente válido, num ciclo que se repete.":
        "The attack cycles through degradation, blocking, partial recovery and a return to an apparently valid signal, repeating over and over.",
    "O comportamento do GPS oscila ao longo do tempo, criando falhas intermitentes e mudanças repentinas de qualidade.":
        "GPS behavior oscillates over time, creating intermittent failures and sudden quality changes.",
    "Esse padrão dificulta a estimativa de estado e pode confundir o controle por alternar entre falha e normalidade.":
        "This pattern makes state estimation harder and can confuse the controller by switching between failure and normal operation.",
    "O simulador alterna ciclicamente ruído, blackout e recuperação para reproduzir interferência pulsada.":
        "The simulator cycles through noise, blackout and recovery to reproduce pulsed interference.",

    # --- Mitigações (tela detalhada) -------------------------------------
    "Mitigações de jamming GPS/GNSS": "GPS/GNSS jamming mitigations",
    "Como o UAVEDUSIM reduz o efeito de cada ataque antes que ele afete o estimador.":
        "How UAVEDUSIM reduces the effect of each attack before it reaches the estimator.",
    "O objetivo não é impedir o ataque, mas evitar que medições ruins, ausentes ou "
    "instáveis sejam usadas diretamente pela navegação sem tratamento. Cada tipo de "
    "ataque exige uma resposta diferente.":
        "The goal isn't to prevent the attack, but to keep bad, missing or unstable "
        "readings from being used directly by navigation without treatment. Each "
        "attack type calls for a different response.",
    "Mitigação do Jamming Noise": "Jamming Noise mitigation",
    "Filtro de Kalman valida cada medição de GPS antes de usá-la, considerando também a altitude.":
        "A Kalman filter validates every GPS reading before using it, also factoring in altitude.",
    "Como funciona": "How it works",
    "Cada medição de GPS é comparada com a posição prevista pelo filtro de Kalman; leituras muito divergentes têm sua influência reduzida.":
        "Each GPS reading is compared against the position predicted by the Kalman filter; readings that diverge too much have their influence reduced.",
    "Quando entra em ação": "When it kicks in",
    "Sempre que uma leitura de GPS foge do comportamento esperado do VANT — mesmo com o sinal aparentemente disponível.":
        "Whenever a GPS reading strays from the UAV's expected behavior — even while the signal looks available.",
    "O que muda para o VANT": "What changes for the UAV",
    "A altitude passa a priorizar a posição local do PX4 e o barômetro; a odometria visual ajuda quando disponível, evitando oscilações verticais falsas.":
        "Altitude starts prioritizing PX4's local position and the barometer; visual odometry helps when available, avoiding false vertical oscillations.",
    "Mitigação do Jamming Blackout": "Jamming Blackout mitigation",
    "Âncora de posição e pseudo-medições substituem o GPS enquanto ele estiver ausente.":
        "A position anchor and pseudo-measurements replace GPS while it's unavailable.",
    "Um timeout detecta a ausência de atualizações de GPS e cria uma âncora: um ponto de referência fixado na última posição confiável do VANT.":
        "A timeout detects the absence of GPS updates and creates an anchor: a reference point fixed at the UAV's last confirmed position.",
    "Assim que o tempo sem novas medições de GPS ultrapassa o limite configurado.":
        "As soon as the time without new GPS readings passes the configured limit.",
    "A partir da âncora, o sistema gera pseudo-medições — estimativas internas de posição, não um GPS real — usando dead reckoning e odometria visual quando disponível, até o sinal voltar.":
        "From the anchor, the system generates pseudo-measurements — internal position estimates, not a real GPS — using dead reckoning and visual odometry when available, until the signal returns.",
    "Mitigação do Jamming Pulse": "Jamming Pulse mitigation",
    "Combina as estratégias do Noise e do Blackout, conforme a fase do ataque.":
        "Combines the Noise and Blackout strategies, depending on the attack's phase.",
    "Usa o filtro de Kalman nas fases de degradação e recuperação, e a âncora com pseudo-medições durante os bloqueios.":
        "Uses the Kalman filter during degradation and recovery phases, and the anchor with pseudo-measurements during blockouts.",
    "A cada transição de fase do ataque: degradação, bloqueio ou recuperação do sinal.":
        "At every phase transition of the attack: degradation, blocking or signal recovery.",
    "Evita reações bruscas quando o GPS retorna, já que o sinal pode voltar ainda instável ou com erro residual.":
        "Avoids abrupt reactions when GPS returns, since the signal may come back still unstable or with residual error.",
    "Resposta de segurança — vale para os três ataques": "Safety response — applies to all three attacks",
    "Nenhuma mitigação sustenta o voo indefinidamente. Se a interferência persistir após o "
    "VANT alcançar a região de destino, o sistema aciona pouso controlado e desarme dos motores.":
        "No mitigation sustains flight indefinitely. If interference persists after the "
        "UAV reaches the destination area, the system triggers a controlled landing and disarms the motors.",
    "Antes de liberar o simulador, responda um quiz rápido de 5 perguntas sobre o que você acabou de ver — é preciso acertar pelo menos 2 pra desbloquear.":
        "Before unlocking the simulator, answer a quick 5-question quiz about what you just saw — you need at least 2 correct answers to unlock it.",

    # --- Quiz ------------------------------------------------------------
    "Quiz: jamming e mitigação": "Quiz: jamming and mitigation",
    "Resultado": "Result",
    "↺ Refazer o quiz": "↺ Retake the quiz",
    "Ver como usar o simulador 󰁕": "See how to use the simulator 󰁕",
    "Mandou muito bem! Você entendeu os conceitos principais de jamming e mitigação. Simulador liberado.":
        "Great job! You've grasped the main concepts of jamming and mitigation. Simulator unlocked.",
    "Nota suficiente pra liberar o simulador! Vale revisar as telas anteriores nos pontos que você errou.":
        "That's a passing score! It's worth reviewing the earlier screens on the points you missed.",
    "O que é um ataque de jamming, segundo o simulador?": "According to the simulator, what is a jamming attack?",
    "Uma falha de hardware do GPS do drone": "A hardware failure in the drone's GPS",
    "Uma interferência intencional contra sinais usados por um sistema": "Intentional interference against signals used by a system",
    "Um vírus que infecta o software de navegação": "A virus that infects the navigation software",
    "Uma atualização mal aplicada no firmware do PX4": "A badly applied firmware update on PX4",
    "Jamming é uma interferência intencional contra sinais utilizados por um sistema — não é falha de hardware, vírus ou firmware.":
        "Jamming is intentional interference against signals used by a system — it's not a hardware failure, a virus, or firmware.",
    "No Jamming Noise, o que acontece com o sinal de GPS?": "In Jamming Noise, what happens to the GPS signal?",
    "O sinal desaparece completamente": "The signal disappears completely",
    "O sinal continua chegando, mas as medições ficam degradadas por ruído": "The signal keeps arriving, but readings get degraded by noise",
    "O sinal é substituído por coordenadas fixas": "The signal is replaced by fixed coordinates",
    "O sinal passa a vir de um satélite falso": "The signal starts coming from a fake satellite",
    "No Noise, o GPS continua aparentemente disponível, mas a intensidade do ataque introduz ruído na posição, na altitude, na velocidade e nos indicadores de qualidade do sinal.":
        "In Noise, GPS still appears available, but the attack's intensity introduces noise into position, altitude, velocity and signal-quality indicators.",
    "Por que o Jamming Blackout não tem um campo de \"intensidade\" pra configurar?": "Why doesn't Jamming Blackout have an \"intensity\" field to configure?",
    "Porque a intensidade é sempre fixa em 100": "Because the intensity is always fixed at 100",
    "Porque o ataque é binário — o sinal simplesmente para de ser publicado por completo": "Because the attack is binary — the signal simply stops being published entirely",
    "Porque o blackout usa a mesma intensidade do Noise": "Because blackout uses the same intensity as Noise",
    "Porque o blackout não afeta o GPS, só a câmera": "Because blackout doesn't affect GPS, only the camera",
    "O blackout é um ataque tudo-ou-nada: enquanto está ativo, a publicação de atualizações de GPS é interrompida por completo — não existe \"grau\" de blackout.":
        "Blackout is an all-or-nothing attack: while it's active, GPS updates stop being published entirely — there's no \"degree\" of blackout.",
    "O que a intensidade do Jamming Pulse controla?": "What does the Jamming Pulse intensity control?",
    "Só a cor do ícone do ataque na interface": "Only the color of the attack icon in the interface",
    "O tempo de bloqueio do GPS a cada pulso e a distância do ruído nas fases de transição": "How long GPS is blocked per pulse and how far the noise reaches during transition phases",
    "Apenas a velocidade de rotação do drone": "Only the drone's rotation speed",
    "O número de satélites simulados": "The number of simulated satellites",
    "A intensidade do Pulse (entre 0.1 e 1.0) define tanto o tempo de bloqueio total do GPS a cada pulso quanto a distância do ruído nas fases de subida e decaimento do sinal.":
        "Pulse intensity (between 0.1 and 1.0) sets both how long GPS is fully blocked per pulse and how far the noise reaches during the signal's rise and decay phases.",
    "Qual é a função do filtro de Kalman na mitigação do Jamming Noise?": "What's the role of the Kalman filter in Jamming Noise mitigation?",
    "Bloquear completamente o GPS até o ataque acabar": "Completely block GPS until the attack ends",
    "Comparar cada medição de GPS com uma previsão do estado esperado do VANT, reduzindo a influência de leituras muito divergentes":
        "Compare each GPS reading against a prediction of the UAV's expected state, reducing the influence of readings that diverge too much",
    "Trocar o GPS por uma câmera automaticamente": "Automatically swap GPS for a camera",
    "Aumentar a intensidade do ataque pra facilitar a detecção": "Increase the attack's intensity to make it easier to detect",
    "O filtro de Kalman compara a medição recebida com uma previsão do comportamento esperado do VANT; leituras muito distantes do previsto têm sua influência reduzida.":
        "The Kalman filter compares the received reading against a prediction of the UAV's expected behavior; readings far from the prediction have their influence reduced.",
    "Isso mesmo! ": "That's right! ",
    "Quase! ": "Almost! ",

    # --- Tutorial ----------------------------------------------------------
    "Como usar o painel de ataques": "How to use the attack panel",
    "Passo a passo pra lançar um ataque de jamming e, se quiser, comparar com a mitigação ativada.":
        "A step-by-step guide to launching a jamming attack and, if you like, comparing it with mitigation enabled.",
    "Envie a decolagem": "Send takeoff",
    "Clique em \"Enviar commander takeoff\". O painel de ataques fica bloqueado até o PX4 confirmar que o VANT decolou de verdade.":
        "Click \"Send commander takeoff\". The attack panel stays locked until PX4 confirms the UAV has actually taken off.",
    "Envie o destino": "Send the destination",
    "Distância, rotação e altitude já vêm preenchidos com valores padrão (350 m, 0°, 2.5 m) — mas são campos editáveis, dá pra ajustar antes de enviar.":
        "Distance, rotation and altitude come pre-filled with default values (350 m, 0°, 2.5 m) — but they're editable, so you can adjust them before sending.",
    "Quer comparar com e sem mitigação? Ative-a antes do ataque": "Want to compare with and without mitigation? Enable it before the attack",
    "A chave \"Mitigação\" só passa a agir sobre as medições geradas depois de ser ligada. Ativando antes de lançar o ataque, a diferença entre com e sem fica clara desde o primeiro instante.":
        "The \"Mitigation\" switch only acts on readings generated after it's turned on. Enabling it before launching the attack makes the difference between with and without clear from the very first moment.",
    "Escolha o ataque, ajuste a intensidade e clique em \"Aplicar\"": "Choose the attack, adjust the intensity and click \"Apply\"",
    "Jamming Noise e Jamming Pulse já vêm com um valor de intensidade padrão preenchido — mas esse valor é totalmente personalizável, edite antes de aplicar.":
        "Jamming Noise and Jamming Pulse come with a default intensity value pre-filled — but that value is fully customizable, edit it before applying.",
    "Entendendo a intensidade de cada ataque": "Understanding each attack's intensity",
    "A intensidade entra direto na fórmula do ruído: o desvio horizontal (norte/leste) e de velocidade variam em torno de intensidade × 10 metros; o desvio de altitude, em torno de intensidade × 30 metros. Com a intensidade padrão (80), isso já significa desvios de até ~800 m na posição horizontal — um efeito bem visível.":
        "Intensity feeds directly into the noise formula: horizontal (north/east) and velocity deviation vary around intensity × 10 meters; altitude deviation, around intensity × 30 meters. With the default intensity (80), that already means deviations of up to ~800 m in horizontal position — a very visible effect.",
    "A intensidade (limitada entre 0.1 e 1.0) tem duas funções: define o tempo de bloqueio total do GPS a cada pulso (intensidade × 10 segundos — 0.3 → 3 segundos) e também a distância do ruído nas fases de transição (subida e decaimento do sinal): intensidade × 1000 metros — 0.3 → até ~300 m de desvio. Use valores entre 0.1 e 1.0 — acima disso a diferença já não é mais visível, porque o PX4 aciona o failsafe de perda de GPS bem antes de o bloqueio terminar, e prolongar o bloqueio não muda o que se observa no comportamento do VANT.":
        "Intensity (limited between 0.1 and 1.0) has two roles: it sets how long GPS is fully blocked per pulse (intensity × 10 seconds — 0.3 → 3 seconds) and also how far the noise reaches during the transition phases (signal rise and decay): intensity × 1000 meters — 0.3 → up to ~300 m of deviation. Use values between 0.1 and 1.0 — beyond that the difference is no longer visible, because PX4 triggers the GPS-loss failsafe well before the block ends, and extending the block doesn't change what you observe in the UAV's behavior.",
    "Não tem campo de intensidade porque o ataque é binário: o sinal de GPS simplesmente para de ser publicado por completo. Não existe \"grau\" de blackout.":
        "There's no intensity field because the attack is binary: the GPS signal simply stops being published entirely. There's no \"degree\" of blackout.",
    "\uf04b  Iniciar simulação do UAVEDUSIM": "\uf04b  Start the UAVEDUSIM simulation",

    # --- Mensagens de bloqueio/estado de simulação (segunda passada) -----
    "Não ha modelo/mundo selecionado para reiniciar.": "No model/world selected to restart.",
    "PX4 e Gazebo estão sendo encerrados e vão subir novamente do zero. "
    "Takeoff, destino, ataque e mitigação vão precisar ser configurados de novo.":
        "PX4 and Gazebo are being shut down and will come back up from scratch. "
        "Takeoff, destination, attack and mitigation will need to be set up again.",
    "A simulação já está iniciando. Aguarde o PX4/Gazebo ficar pronto ou encerre os processos antes de iniciar outra.":
        "The simulation is already starting. Wait for PX4/Gazebo to be ready, or stop the processes before starting another one.",
    "A simulação já está em andamento. Encerre a simulação atual antes de iniciar outra.":
        "The simulation is already running. Stop the current simulation before starting another one.",
    "A simulação está reiniciando. Aguarde a limpeza e a nova inicialização terminarem.":
        "The simulation is restarting. Wait for the cleanup and the new startup to finish.",
    "A inicialização da simulação já foi solicitada. Aguarde alguns instantes.":
        "Simulation startup has already been requested. Wait a few moments.",
    "Já existe uma simulação em andamento. Use Encerrar processos antes de iniciar outra.":
        "A simulation is already running. Use Stop processes before starting another one.",
    "PX4 já está em execução. Encerre a simulação atual antes de iniciar outra.":
        "PX4 is already running. Stop the current simulation before starting another one.",
    "Gazebo já está aberto. Encerre a simulação atual antes de iniciar outra.":
        "Gazebo is already open. Stop the current simulation before starting another one.",
    "Timeout ao enviar destino via MAVLink.": "Timeout while sending destination via MAVLink.",
    "Destino enviado via MAVLink.": "Destination sent via MAVLink.",
    "Enviando destino via MAVLink": "Sending destination via MAVLink",
    "Enviando destino via MAVLink...": "Sending destination via MAVLink...",
})

_UAVEDUSIM_I18N_EN.update({
    # --- Descrições dos modelos de drone (tela de seleção) ---------------
    "Versão do X500 com vários sensores ativos ao mesmo tempo para experimentos do UAVEDUSIM.":
        "X500 version with several sensors active at once, for UAVEDUSIM experiments.",
    "Quadricóptero X500 básico.": "Basic X500 quadcopter.",
    "Quadricóptero X500 com odometria visual.": "X500 quadcopter with visual odometry.",
    "Quadricóptero X500 com câmera de profundidade frontal baseada na OAK-D.":
        "X500 quadcopter with a front-facing depth camera based on the OAK-D.",
    "Quadricóptero X500 com câmera monocular simples.": "X500 quadcopter with a simple monocular camera.",
    "Quadricóptero X500 com câmera monocular voltada para baixo; útil para testes de pouso preciso no mundo Aruco.":
        "X500 quadcopter with a downward-facing monocular camera; useful for precision-landing tests in the Aruco world.",
    "Quadricóptero X500 com LiDAR 1D inferior Lightware LW20/C, alcance de 0,1 a 100 m.":
        "X500 quadcopter with a downward-facing Lightware LW20/C 1D LiDAR, range 0.1 to 100 m.",
    "Quadricóptero X500 com LiDAR 1D frontal Lightware LW20/C, alcance de 0,2 a 100 m.":
        "X500 quadcopter with a forward-facing Lightware LW20/C 1D LiDAR, range 0.2 to 100 m.",
    "Quadricóptero X500 com LiDAR 2D Hokuyo UTM-30LX, alcance de 0,1 a 30 m e varredura de 270 graus.":
        "X500 quadcopter with a Hokuyo UTM-30LX 2D LiDAR, range 0.1 to 30 m and a 270-degree scan.",
    "Quadricóptero X500 com gimbal frontal e controle de posição.": "X500 quadcopter with a front gimbal and position control.",
    "Avião Cessna RC padrão no Gazebo.": "Standard RC Cessna plane in Gazebo.",
    "Avião de asa fixa avançado com plugin Advanced Lift Drag.": "Advanced fixed-wing plane with the Advanced Lift Drag plugin.",
    "VTOL padrão com decolagem vertical e voo de asa fixa.": "Standard VTOL with vertical takeoff and fixed-wing flight.",
    "VTOL quad tailsitter com empuxo diferencial para arfagem, rolagem e guinada.":
        "Quad tailsitter VTOL with differential thrust for pitch, roll and yaw.",
    "VTOL tiltrotor com motores frontais inclináveis para transição e voo avançado.":
        "Tiltrotor VTOL with tiltable front motors for transition and advanced flight.",

    # --- Descrições dos mundos (tela de seleção) --------------------------
    "Empty (Default). Mundo vazio com plano cinza; usado por padrão.":
        "Empty (Default). Empty world with a gray plane; used by default.",
    "Aruco. Mundo default com marcador ArUco; usado com x500_mono_cam_down para testar pouso preciso.":
        "Aruco. Default world with an ArUco marker; used with x500_mono_cam_down to test precision landing.",
    "Baylands. Cenário com águas e árvores.": "Baylands. Scenario with water and trees.",
    "Lawn. Mundo plano e verde; alternativa menos otimizada ao rover world.":
        "Lawn. Flat, green world; a less optimized alternative to the rover world.",
    "Ridge. Mundo com terreno inclinado para testar terrain following, especialmente com x500_lidar_down.":
        "Ridge. World with sloped terrain to test terrain following, especially with x500_lidar_down.",
    "Walls. Mundo com paredes para testes de collision prevention.": "Walls. World with walls for collision-prevention tests.",
    "Windy. Mundo vazio com vento habilitado.": "Windy. Empty world with wind enabled.",
    "Moving Platform. Plataforma plana em movimento; útil para simular pouso e decolagem de VTOL a partir de uma base móvel.":
        "Moving Platform. A moving flat platform; useful for simulating VTOL landing and takeoff from a mobile base.",
    "Mundo Gazebo disponível no PX4.": "Gazebo world available in PX4.",

    # --- Cards de mundo recomendado (tela de seleção) --------------------
    "MUNDO INDICADO": "RECOMMENDED WORLD",
    "CENÁRIO COM ÁGUA E ÁRVORES": "SCENARIO WITH WATER AND TREES",
    "Mundo vazio, com plano cinza e sem obstáculos — o ambiente mais simples "
    "para começar a testar os ataques de jamming.":
        "Empty world, with a gray plane and no obstacles — the simplest environment "
        "to start testing jamming attacks.",
    "Mundo cercado por água e árvores — útil para observar o que acontece quando "
    "a mitigação aciona um pouso de emergência em terreno inadequado.":
        "World surrounded by water and trees — useful for observing what happens when "
        "mitigation triggers an emergency landing on unsuitable terrain.",

    # --- Intro da tela "Ataques de jamming simulados" --------------------
    "Nesta tela, você pode comparar três cenários de jamming simulados no UAVEDUSIM. "
    "Cada ataque compromete a navegação de uma forma diferente, permitindo observar desde "
    "degradação contínua até perda total ou intermitente do sinal GPS/GNSS, tudo em ambiente "
    "seguro e sem interferência real.":
        "On this screen, you can compare three simulated jamming scenarios in UAVEDUSIM. "
        "Each attack compromises navigation in a different way, letting you observe everything from "
        "continuous degradation to total or intermittent loss of the GPS/GNSS signal, all in a safe "
        "environment with no real interference.",

    # --- Botões / rótulos de estado (variantes com quebra de linha) -----
    "Enviar\ndestino": "Send\ndestination",
    "Reenviar\ndestino": "Resend\ndestination",
    "Reiniciar\nsimulação": "Restart\nsimulation",
    "Desligar\njamming": "Turn off\njamming",
    "Inicializando simulação": "Starting simulation",
    "Preparando simulação...\nPX4: iniciando\nGazebo: abrindo": "Preparing simulation...\nPX4: starting\nGazebo: opening",
    "Takeoff detectado pelo PX4.\nAguardando liberação do destino.": "Takeoff detected by PX4.\nWaiting for the destination to unlock.",
    "Takeoff detectado pelo PX4.\nEnvio de destino liberado.": "Takeoff detected by PX4.\nSending the destination is unlocked.",
    "Aguardando confirmação do PX4:\nINFO  [commander] Takeoff detected": "Waiting for PX4 confirmation:\nINFO  [commander] Takeoff detected",
    "Aguardando:\ncommander takeoff": "Waiting for:\ncommander takeoff",
    "Aguardando Takeoff detected": "Waiting for Takeoff detected",
    "Envio de destino disponível.": "Sending the destination is available.",
    "Destino sera liberado após o timer.": "The destination will unlock after the timer.",
    "Takeoff detectado pelo PX4. Aguarde o timer do botão de destino terminar.":
        "Takeoff detected by PX4. Wait for the destination button timer to finish.",
    "Takeoff já foi detectado pelo PX4. O envio de destino esta liberado.":
        "Takeoff has already been detected by PX4. Sending the destination is unlocked.",
    "Enviando commander takeoff.\nAguardando: INFO  [commander] Takeoff detected":
        "Sending commander takeoff.\nWaiting for: INFO  [commander] Takeoff detected",
    "Aguardando PX4:\nINFO  [commander] Takeoff detected": "Waiting for PX4:\nINFO  [commander] Takeoff detected",
    "Takeoff detectado pelo PX4. Aguardando timer para liberar destino.":
        "Takeoff detected by PX4. Waiting for the timer to unlock the destination.",
    "Destino aguardando timer\n\n\n": "Destination waiting for timer\n\n\n",

    # --- Menu de navegação (hambúrguer) -----------------------------------
    "Navegação": "Navigation",
    "Início": "Home",
    "Tela inicial do UAVEDUSIM": "UAVEDUSIM home screen",
    "Conceito geral de jamming": "General jamming concept",
    "Noise, Blackout e Pulse": "Noise, Blackout and Pulse",
    "Estratégias contra os ataques": "Strategies against the attacks",
    "Quiz: jamming e mitigação": "Quiz: jamming and mitigation",
    "5 perguntas de múltipla escolha": "5 multiple-choice questions",
    "Como usar o simulador": "How to use the simulator",
    "Passo a passo do painel de ataques": "Step-by-step guide to the attack panel",
    "Painel de Ataques": "Attack Panel",
    "Lançar ataques na simulação atual": "Launch attacks in the current simulation",
    "← voltar": "← back",
    "\U000f004e voltar": "\U000f004e back",

    # --- Painel de ataques via menu (fora de uma simulação já rodando) ----
    "O painel de ataques só fica disponível quando PX4 e Gazebo estão em execução.":
        "The attack panel is only available while PX4 and Gazebo are running.",
    "Não foi possível abrir o painel de ataques. Verifique se a simulação ainda está ativa.":
        "Couldn't open the attack panel. Check whether the simulation is still active.",

    # --- Manutenção / limpeza de processos --------------------------------
    "Limpar build (se necessário)": "Clear build (if needed)",
    "Limpar processos UAVEDUSIM": "Stop UAVEDUSIM processes",
    "Voltar ao menu principal": "Back to main menu",
    "Já existe uma simulação em andamento.": "A simulation is already running.",
    "Encerrando PX4, Gazebo, QGroundControl e terminais da simulação...":
        "Stopping PX4, Gazebo, QGroundControl and the simulation's terminals...",
    "Processos encerrados: PX4, Gazebo, QGroundControl e terminais da simulação.":
        "Processes stopped: PX4, Gazebo, QGroundControl and the simulation's terminals.",
    "modelo indicado": "recommended model",
    "segundo modelo mais indicado": "second most recommended model",

    # --- Gaps encontrados na auditoria final ------------------------------
    "Ver resultado 󰁕": "See result 󰁕",
    "Selecionar 󰁕": "Select 󰁕",
    "Abrir ataques": "Open attacks",
    "Aplicar": "Apply",
    "Selecionar modelo e mundo": "Select model and world",
    "Próxima pergunta 󰁕": "Next question 󰁕",
    "Reiniciando simulação...\nEncerrando processos antigos do PX4/Gazebo antes de iniciar novamente.":
        "Restarting simulation...\nClosing the old PX4/Gazebo before opening it again.",
    "Não foi possível garantir que todos os processos PX4/Gazebo foram encerrados.\n"
    "A simulação não sera reiniciada automaticamente. Use Limpar processos UAVEDUSIM e tente novamente.":
        "Couldn't guarantee that all PX4/Gazebo processes were stopped.\n"
        "The simulation won't restart automatically. Use Stop processes and try again.",
    "PX4/Gazebo foram encerrados e vão abrir novamente. Envie commander takeoff e "
    "destino para liberar os ataques.":
        "PX4/Gazebo were stopped and will open again. Send commander takeoff and "
        "the destination to unlock the attacks.",
    "Interferência intencional": "Intentional interference",
    "Afeta múltiplos sinais": "Affects multiple signals",
    "Mitigações do Simulador": "Simulator Mitigations",
    "Ataques de Jamming Simulados": "Simulated Jamming Attacks",
    "Entenda as mitigações do simulador   \uf054": "Learn about the simulator's mitigations   \uf054",
})


if __name__ == "__main__":
    try:
        UAVEDUSIMApp().run()
    except KeyboardInterrupt:
        sys.exit(130)