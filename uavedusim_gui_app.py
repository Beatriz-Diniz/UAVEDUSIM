#!/usr/bin/env python3
import os
import re
import sys
import time
import shutil
import signal
import subprocess
import threading
from pathlib import Path
import tkinter as tk
from tkinter import ttk

APP_DIR = Path(os.environ.get("APP_DIR") or os.environ.get("UAVEDUSIM_DIR") or Path(__file__).resolve().parent).resolve()
PX4_DIR = Path(os.environ.get("PX4_DIR") or APP_DIR / "PX4-Autopilot").resolve()
QGC_APP = Path(os.environ.get("QGC_APP") or APP_DIR / "QGroundControl-x86_64.AppImage").resolve()
QGC_EXTRACTED = Path(os.environ.get("QGC_EXTRACTED") or APP_DIR / "squashfs-root/usr/bin/QGroundControl").resolve()
LOG = Path(os.environ.get("LOG") or APP_DIR / "uavedusim_gui.log").resolve()
SESSION_ID = os.environ.get("UAVEDUSIM_SESSION_ID") or str(os.getpid())
RUNTIME_DIR = Path(os.environ.get("UAVEDUSIM_RUNTIME_DIR") or APP_DIR / ".uavedusim_runtime").resolve()
PID_FILE = Path(os.environ.get("UAVEDUSIM_PID_FILE") or RUNTIME_DIR / f"terminals_{SESSION_ID}.pids").resolve()
SCRIPT_DIR = Path(os.environ.get("UAVEDUSIM_SCRIPT_DIR") or RUNTIME_DIR / f"scripts_{SESSION_ID}").resolve()

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

ICONS = {
    # Icones das acoes principais do menu.
    "executar": "▶",
    "limpar": "↺",
    "voltar": "←",
    "fechar": "×",
    "sair": "⏻",

    # Icones de ferramentas e componentes externos.
    "uavedusim": "⬡",
    "px4": "⊡",
    "gazebo": "▣",
    "qground": "◈",

    # Icones dos tipos de veiculos simulados.
    "drone": "✛",
    "aircraft": "✈",
    "rover": "▥",
    "modelo": "✛",

    # Icones usados em ataques e mitigacao.
    "noise": "≈",
    "blackout": "⊘",
    "pulse": "∿",
    "mitig": "◆",
    "ataque": "⚠",
    "jamming": "≈",

    # Icones dos mundos disponiveis no Gazebo.
    "default": "○",
    "aruco": "▦",
    "baylands": "≋",
    "bayland": "≋",
    "lawn": "▤",
    "forest": "♣",
    "ridge": "▲",
    "walls": "▭",
    "windy": "↝",
    "moving_platform": "⇄",
    "frictionless": "◇",
    "underwater": "≈",
    "kth": "▣",
    "mundo": "◉",
}

# Textos curtos exibidos no balao de informacao (icone "i") de cada ataque.
ATTACK_INFO_TEXT = {
    "noise": (
        "Jamming Noise: o GPS parece continuar funcionando, mas as medicoes de "
        "posicao, velocidade e altitude chegam degradadas por ruido. Quanto maior "
        "a intensidade (ex: 80), maior a diferenca entre a posicao estimada e a "
        "posicao real do VANT."
    ),
    "blackout": (
        "Jamming Blackout: bloqueia por completo o sinal de GPS/GNSS. O VANT "
        "deixa de receber novas atualizacoes de posicao, podendo interromper a "
        "navegacao autonoma ou acionar mecanismos de seguranca (failsafe)."
    ),
    "pulse": (
        "Jamming Pulse: ataque intermitente que alterna entre degradacao, "
        "bloqueio e recuperacao parcial do GPS. A intensidade (ex: 0.3) afeta o "
        "padrao do ciclo, causando oscilacoes na navegacao do VANT."
    ),
}


class _AttackInfoTooltip:
    """Balao de informacao (hover ou clique) para o icone 'i' de cada ataque.

    O balao e desenhado como um widget dentro da propria janela do app
    (via place()), e nao como uma janela separada do sistema operacional.
    Isso garante que ele sempre aparece dentro dos limites da janela do
    UAVEDUSIM, proximo do ataque que o usuario esta consultando.
    """

    _active = None  # garante um unico balao visivel por vez

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

        # Balao construido como filho direto de "root" (a janela do app),
        # posicionado com place() em coordenadas relativas a propria janela.
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

        # Centraliza horizontalmente sob o icone, sem ultrapassar as bordas da janela.
        pos_x = icon_x + icon_w // 2 - tip_w // 2
        pos_x = max(margin, min(pos_x, root_w - tip_w - margin))

        # Prefere exibir abaixo do icone; se nao couber, exibe acima dele.
        pos_y = icon_y + icon_h + app.px(6)
        if pos_y + tip_h > root_h - margin:
            pos_y = icon_y - tip_h - app.px(6)
        pos_y = max(margin, min(pos_y, root_h - tip_h - margin))

        tip.place(x=pos_x, y=pos_y, width=tip_w, height=tip_h)
        tip.lift()

        # Fecha o balao automaticamente se a janela for redimensionada,
        # para evitar que ele fique posicionado incorretamente. Guarda o
        # tamanho atual para ignorar eventos <Configure> "espurios" que nao
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
        # tambem e acionado por eventos <Configure> de QUALQUER widget filho
        # (o pathname da janela raiz faz parte das bindtags de todos os seus
        # descendentes, incluindo o proprio balao recem-criado). Por isso,
        # so tratamos o evento se ele vier realmente da janela raiz.
        root = getattr(active.app, "root", None)
        if root is not None and event.widget is not root:
            return
        new_size = (getattr(event, "width", None), getattr(event, "height", None))
        if new_size == getattr(active, "_shown_size", None):
            return  # tamanho nao mudou de fato; ignora o evento espurio
    active._hide()


def _uavedusim_make_info_icon(app, parent, attack_key):
    """Cria o icone 'i' (canto do card do ataque) com balao de informacao."""
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


MODEL_AIRCRAFT_PATTERNS = (
    "standard_vtol",
    "vtol",
    "evtol",
    "tailsitter",
    "tail_sitter",
    "tiltrotor",
    "tilt_rotor",
    "plane",
    "cessna",
    "fixedwing",
    "fixed_wing",
)

MODEL_ROVER_PATTERNS = (
    "rover",
    "r1_rover",
    "ackermann",
    "mecanum",
    "differential",
)

MODEL_DRONE_PATTERNS = (
    "x500",
    "uavjamsim",
    "quad",
    "quadrotor",
    "multicopter",
    "multirotor",
    "hex",
    "iris",
    "typhoon",
    "lidar",
    "depth",
    "vision",
    "mono_cam",
    "camera",
    "cam",
    "gimbal",
    "flow",
)

ACTION_ICON_PATTERNS = (
    ("executar", ICONS["executar"]),
    ("takeoff", ICONS["executar"]),
    ("limpar", ICONS["limpar"]),
    ("reiniciar", ICONS["limpar"]),
    ("voltar", ICONS["voltar"]),
    ("fechar", ICONS["fechar"]),
    ("sair", ICONS["sair"]),
)

ATTACK_ICON_PATTERNS = (
    ("blackout", ICONS["blackout"]),
    ("noise", ICONS["noise"]),
    ("pulse", ICONS["pulse"]),
    ("pulsed", ICONS["pulse"]),
    ("mitig", ICONS["mitig"]),
    ("ataque", ICONS["ataque"]),
    ("jamming", ICONS["jamming"]),
)

WORLD_ICON_PATTERNS = (
    ("moving_platform", ICONS["moving_platform"]),
    ("default", ICONS["default"]),
    ("aruco", ICONS["aruco"]),
    ("baylands", ICONS["baylands"]),
    ("bayland", ICONS["bayland"]),
    ("lawn", ICONS["lawn"]),
    ("forest", ICONS["forest"]),
    ("ridge", ICONS["ridge"]),
    ("walls", ICONS["walls"]),
    ("windy", ICONS["windy"]),
    ("frictionless", ICONS["frictionless"]),
    ("underwater", ICONS["underwater"]),
    ("kth", ICONS["kth"]),
    ("mundo", ICONS["mundo"]),
)

TOOL_ICON_PATTERNS = (
    ("uavedusim", ICONS["uavedusim"]),
    ("qground", ICONS["qground"]),
    ("gazebo", ICONS["gazebo"]),
    ("px4", ICONS["px4"]),
)


# Define o icone exibido para cada opcao da interface.
def icon_for(label):
    text = str(label or "")
    low = text.lower()
    first_line = low.splitlines()[0].strip()
    normalized = first_line.replace("-", "_").replace(" ", "_")

    # Escolhe icones por tipo de modelo PX4/Gazebo.
    # Multirrotores usam drone; VTOL/avioes usam aviao; rovers usam solo.
    is_model = normalized.startswith("gz_") or "modelo" in normalized

    if is_model:
        if any(pattern in normalized for pattern in MODEL_AIRCRAFT_PATTERNS):
            return ICONS["aircraft"]

        if any(pattern in normalized for pattern in MODEL_ROVER_PATTERNS):
            return ICONS["rover"]

        if any(pattern in normalized for pattern in MODEL_DRONE_PATTERNS):
            return ICONS["drone"]

        if normalized.startswith("gz_"):
            return ICONS["drone"]

        return ICONS["modelo"]

    for pattern, icon in ACTION_ICON_PATTERNS:
        if pattern in normalized:
            return icon

    for pattern, icon in ATTACK_ICON_PATTERNS:
        if pattern in normalized:
            return icon

    for pattern, icon in WORLD_ICON_PATTERNS:
        if pattern in normalized:
            return icon

    for pattern, icon in TOOL_ICON_PATTERNS:
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

# Envia notificacao do sistema quando notify-send estiver disponivel.
def notify_msg(msg: str) -> None:
    if shutil.which("notify-send"):
        subprocess.Popen(["notify-send", "UAVEDUSIM", msg], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

# Escapa strings usadas em comandos shell.
def shell_quote(value: str) -> str:
    return "'" + value.replace("'", "'\\''") + "'"

# Executa comandos externos e grava a saida no log.
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
    log(f"Codigo de retorno: {proc.returncode}")
    if check and proc.returncode != 0:
        raise RuntimeError(f"Comando falhou: {' '.join(map(str,args))}")
    return proc

# Le o trecho final do log para diagnostico rapido.
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

# Remove codigos de cor e controle dos logs do terminal.
def strip_ansi(text: str) -> str:
    if not text:
        return ""

    # Normaliza logs removendo codigos ANSI do terminal.
    text = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", text)
    text = re.sub(r"\x1b\][^\x07]*(?:\x07|\x1b\\)", "", text)
    text = text.replace("\r", "\n")
    return text

# Lista processos ativos ignorando a propria interface.
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

# Verifica se o PX4 SITL esta em execucao.
def process_indicates_px4_running():
    for line in process_lines():
        low = line.lower()

        if "px4_sitl_default/bin/px4" in line:
            return True, "PX4 em execucao"

        if str(PX4_DIR / "build/px4_sitl_default/bin/px4") in line:
            return True, "PX4 em execucao"

        if re.search(r"(^|[/\s])px4(\s|$)", line) and "px4-autopilot" in low:
            return True, "PX4 em execucao"

    try:
        proc = subprocess.run(
            ["pgrep", "-x", "px4"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=2,
        )
        if proc.returncode == 0 and proc.stdout.strip():
            return True, "PX4 em execucao"
    except Exception:
        pass

    return False, "PX4 ainda nao detectado"

# Verifica se o Gazebo ja foi aberto.
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

    return False, "Gazebo ainda nao detectado"

# Detecta o Ready for takeoff no log do PX4.
def log_has_commander_ready(data: str):
    clean = strip_ansi(data)

    if not clean:
        return False

    low = clean.lower()
    compact = re.sub(r"\s+", "", low)

    # O PX4 pode colorir "Ready for takeoff!" com ANSI e o terminal pode quebrar
    # visualmente a palavra takeoff. Por isso procuramos tambem sem espacos.
    if "readyfortakeoff!" not in compact:
        return False

    # Evita disparar por textos externos. O evento esperado vem do commander.
    if "commander" not in low:
        return False

    return True

# Valida se o PX4 chegou ao estado pronto para decolar.
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

    return False, "PX4 ainda nao confirmou Takeoff detected"

def log_indicates_commander_ready(log_offset=0):
    data = read_log_since(log_offset)

    if not data:
        return False, "PX4 aguardando Ready for takeoff"

    clean = strip_ansi(data)
    low = clean.lower()

    # Procura o Ready no trecho de log da execucao atual.
    if log_has_commander_ready(data):
        ready_pos = re.sub(r"\s+", "", low).rfind("readyfortakeoff!")

        if ready_pos >= 0:
            return True, "PX4 Ready for takeoff"

    # Fallback para casos em que o offset foi salvo tarde demais.
    # Como o log e recriado na abertura, o trecho recente pertence a sessao atual.
    recent = read_recent_log(900000)

    if log_offset and log_has_commander_ready(recent):
        return True, "PX4 Ready for takeoff"

    px4_running, _reason = process_indicates_px4_running()

    if px4_running:
        return False, "PX4 iniciado; aguardando Ready for takeoff"

    return False, "PX4 ainda nao detectado"

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
            echo "[UAVEDUSIM] ERRO: encontrei setup.zsh, mas zsh nao esta instalado."
            exit 1
        fi
    else
        echo "[UAVEDUSIM] Aviso: setup do gazebo_workspace nao encontrado. Continuando com ambiente atual."
    fi

    export GZ_IP="${GZ_IP:-127.0.0.1}"
}
load_gazebo_env
'''

# Canvas customizado usado nas listas de selecao.
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

    # Atualiza os itens da lista e reinicia a selecao.
    def set_options(self, opts):
        self.opts = list(opts)
        self.selected_idx = 0
        self.hover_idx = None
        self._draw()

    # Desenha linhas, icones e destaque do item selecionado.
    def _redraw(self, _e=None):
        self._draw()

    # Converte a posicao do mouse no indice da lista.
    # Seleciona um item com clique simples.
    def _on_click(self, e):
        i = self._idx_at(e.y)
        if i is not None:
            self.selected_idx = i
            self._draw()

    # Confirma a opcao selecionada com duplo clique.
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

    # Ajusta o scroll para manter o item selecionado visivel.
# Janela principal da interface UAVEDUSIM.
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
        self.show_main_menu()

    def _env_float(self, name, default):
        try:
            return float(os.environ.get(name, str(default)))
        except Exception:
            return float(default)

    def _clamp(self, value, low, high):
        return max(low, min(value, high))

    # Calcula escala, tamanho da janela e fontes responsivas.
    # Configura estilos visuais dos widgets ttk.
    # Monta a moldura da janela, barra superior e area de conteudo.
    def _build_shell(self):
        self.outer = tk.Frame(self.root, bg=BG, highlightbackground=BORDER, highlightthickness=1, bd=0)
        self.outer.pack(fill="both", expand=True)
        self.titlebar = tk.Frame(self.outer, bg=BG, height=self.TITLEBAR_H)
        self.titlebar.pack(fill="x", side="top")
        self.titlebar.pack_propagate(False)
        self.logo_lbl = tk.Label(self.titlebar, text="⬡ UAVEDUSIM", bg=BG, fg=ACCENT, font=(self.FONT_FAMILY, self.FS_LOGO, "bold"), cursor="fleur")
        self.logo_lbl.pack(side="left", padx=(self.px(16), 0))
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
        ttk.Button(win_btns, text="−", width=3, style="WinBtn.TButton", command=self.minimize).pack(side="left", padx=1)
        ttk.Button(win_btns, text="□", width=3, style="WinBtn.TButton", command=self.toggle_fullscreen).pack(side="left", padx=1)
        ttk.Button(win_btns, text="×", width=3, style="CloseBtn.TButton", command=self.close_app).pack(side="left", padx=(1, 0))
        tk.Frame(self.outer, bg=ACCENT, height=self.px(2)).pack(fill="x")
        self.body_frame = tk.Frame(self.outer, bg=SURFACE)
        self.body_frame.pack(fill="both", expand=True)
        self.sidebar = tk.Frame(self.body_frame, bg=BG, width=self.SIDEBAR_W)
        self.sidebar.pack(side="left", fill="y")
        self.content = tk.Frame(self.body_frame, bg=SURFACE)
        self.content.pack(side="left", fill="both", expand=True)

    # Remove widgets da pagina atual antes de renderizar outra tela.
    def clear_content(self):
        for child in self.content.winfo_children():
            child.destroy()

    def set_badge(self, text):
        self.badge.configure(text=f"  {text}  ")

    # Renderiza uma tela de menu com lista, rodape e botoes.
    # Executa a acao associada ao item selecionado.
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

    def minimize(self):
        self.root.overrideredirect(False)
        self.root.iconify()
        self.root.after(200, lambda: self.root.overrideredirect(False))

    # Fecha a interface; o shell externo faz o cleanup final.
    def close_app(self):
        log("Fechando interface UAVEDUSIM.")
        self.root.destroy()

    # Encerra todos os terminais registrados pela sessao.
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

                log(f"Cleanup retornou codigo: {proc.returncode}")
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

        log("Encerrando terminais da simulacao PX4/Gazebo.")

        for pid, title in targets:
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                continue
            except Exception:
                pass

            log(f"Enviando TERM para terminal da simulacao: pid={pid} title={title}")

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

            log(f"Forcando KILL para terminal da simulacao: pid={pid} title={title}")

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

    # Aguarda o encerramento completo da simulacao.
    def wait_until_simulation_processes_closed(self, timeout=20):
        deadline = time.monotonic() + float(timeout)
        last_remaining = []

        while time.monotonic() < deadline:
            remaining = self.simulation_process_lines()

            if not remaining:
                log("Todos os processos PX4/Gazebo da simulacao foram encerrados.")
                return True

            last_remaining = remaining
            time.sleep(0.4)

        if last_remaining:
            log("Aviso: ainda existem processos PX4/Gazebo apos timeout:")
            for line in last_remaining[:20]:
                log("  restante: " + line)

        return False

    # Envia TERM ou KILL para processos da simulacao.
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
        log("Encerrando apenas PX4/Gazebo da simulacao atual.")

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
            msg = "Nao ha modelo/mundo selecionado para reiniciar."
            self._set_attack_status_ui(msg, level="danger")
            notify_msg(msg)
            return

        msg = f"Reiniciando simulacao: {self.model} / {self.world}..."
        log(msg)
        self._log_status_event(
            "Reiniciando simulacao",
            "PX4 e Gazebo estao sendo encerrados e vao subir novamente do zero. "
            "Takeoff, destino, ataque e mitigacao vao precisar ser configurados de novo.",
            level="warn",
        )
        notify_msg(msg)

        if hasattr(self, "restart_button"):
            self.restart_button.configure(state="disabled", cursor="watch")

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
                text="Reiniciando simulacao...\nEncerrando processos antigos do PX4/Gazebo antes de iniciar novamente.",
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
                        "Nao foi possivel garantir que todos os processos PX4/Gazebo foram encerrados.\n"
                        "A simulacao nao sera reiniciada automaticamente. Use Limpar processos UAVEDUSIM e tente novamente."
                    )
                    log(msg_error)
                    notify_msg("Falha ao reiniciar: processos antigos ainda ativos.")

                    if hasattr(self, "progress"):
                        try:
                            self.progress.stop()
                        except Exception:
                            pass

                    if hasattr(self, "status_lbl"):
                        self.status_lbl.configure(text=msg_error, fg=DANGER)

                    return

                self._log_status_event(
                    "Simulacao reiniciada",
                    "PX4/Gazebo foram encerrados e vao abrir novamente. Envie commander takeoff e "
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

    # Sai da tela de ataques e encerra a simulacao atual.
    def leave_attack_screen(self):
        log("Saindo da tela de ataques. Encerrando terminais e processos da simulacao.")
        notify_msg("Encerrando PX4/Gazebo/QGroundControl e voltando ao menu.")

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
        notify_msg(msg)
        self.show_status_page("Erro", msg, ["Voltar ao menu principal"], lambda _x: self.show_main_menu(), badge="ERRO")

    # Exibe uma pagina simples de status com opcoes.
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
    # Despacha a acao escolhida no menu inicial.
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
    echo "[UAVEDUSIM] Comando finalizado com codigo: $status"
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
            self.show_error("Nenhum terminal encontrado. Instale kitty: sudo dnf install kitty")
            return False
        try:
            log(f"Abrindo terminal: {title}")
            log(f"Script temporario: {tmp}")
            proc = subprocess.Popen(terminals[0], env=env, cwd=str(APP_DIR), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
            self.register_terminal_pid(proc.pid, title)
            return True
        except Exception as e:
            self.show_error(f"Falha ao abrir terminal: {e}")
            return False

    # Executa a limpeza manual de processos.
    def run_cleanup(self):
        if (APP_DIR / "cleanup_px4_gazebo.sh").exists() and os.access(APP_DIR / "cleanup_px4_gazebo.sh", os.X_OK):
            cmd = f"cd {shell_quote(str(APP_DIR))} && ./cleanup_px4_gazebo.sh"
        else:
            cmd = "pkill -f 'px4|gz sim|gzserver|gzclient|MicroXRCEAgent|QGroundControl' || true"
        self.open_terminal("Limpar processos UAVEDUSIM", cmd, hold=False)

    # Remove o build local do PX4 quando necessario.
    def run_clean_build(self):
        if not PX4_DIR.is_dir():
            self.show_error(f"Nao encontrei a pasta: {PX4_DIR}")
            return
        cmd = f"""cd {shell_quote(str(PX4_DIR))}
echo '[UAVEDUSIM] Limpando build...'
rm -rf build
echo '[UAVEDUSIM] Rodando make clean...'
make clean || true
echo '[UAVEDUSIM] Build limpo. Terminal sera fechado automaticamente.'"""
        self.open_terminal("Limpar build (se necessário)", cmd, hold=False)

    # Executa comandos Git e retorna codigo e saida.
    def git_output(self, args):
        proc = run_cmd(args)
        return proc.returncode, (proc.stdout or "").strip()

    # Verifica se o PX4 possui tag semantica acessivel.
    def px4_semantic_tag_available(self):
        code, _out = self.git_output([
            "git", "-C", PX4_DIR,
            "describe", "--tags", "--match", "v[0-9]*", "--abbrev=0"
        ])
        return code == 0

    # Obtem a versao Git do PX4 usada no build.
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
                    log(f"Build PX4 parece ter sido configurado com versao 0.0.0: {candidate}")
                    return True
            except Exception:
                continue

        return False

    # Remove o build SITL quando a versao PX4 precisa ser regenerada.
    def invalidate_px4_sitl_build_for_version(self, reason):
        build_dir = PX4_DIR / "build/px4_sitl_default"

        if os.environ.get("UAVEDUSIM_DISABLE_AUTO_TAG_BUILD_CLEAN", "0") == "1":
            log(f"Auto limpeza do build SITL desativada. Motivo ignorado: {reason}")
            return

        if build_dir.exists():
            log(f"Removendo apenas build SITL para regenerar versao PX4. Motivo: {reason}")
            shutil.rmtree(build_dir, ignore_errors=True)
        else:
            log(f"Build SITL ainda nao existe. Motivo registrado: {reason}")

    # Garante tags Git para evitar build PX4 com versao v0.0.0.
    def ensure_px4_git_metadata(self):
        if not PX4_DIR.is_dir():
            return

        if run_cmd(["git", "-C", PX4_DIR, "rev-parse", "--is-inside-work-tree"]).returncode != 0:
            log("Aviso: PX4 nao parece ser repositorio Git. Pulando verificacao de tags.")
            return

        initial_desc = self.px4_git_describe()
        initial_has_tag = self.px4_semantic_tag_available()

        if initial_has_tag:
            log(f"PX4 Git version detectada: {initial_desc or 'indisponivel'}")

            if self.px4_build_looks_like_v0():
                self.invalidate_px4_sitl_build_for_version(
                    "tags existem, mas o build anterior foi configurado como PX4_GIT_TAG v0.0.0"
                )

            return

        log("PX4 sem tags semanticas alcancaveis. Tentando buscar tags do remoto...")

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
            log(f"PX4 Git version apos fetch: {desc or 'indisponivel'}")
            self.invalidate_px4_sitl_build_for_version(
                "tags PX4 foram recuperadas depois de o repositorio estar sem versao semantica"
            )
            return

        # Fallback offline para repositorios sem tags PX4.
        # Cria tag local quando nao for possivel buscar tags do remoto.
        # Preserva a tag oficial v1.17.0 quando ela ja existe.
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
        log(f"PX4 Git version final: {final_desc or 'indisponivel'}")

        if self.px4_semantic_tag_available():
            self.invalidate_px4_sitl_build_for_version(
                f"tag fallback {fallback_tag} criada para corrigir PX4_GIT_TAG v0.0.0"
            )
        else:
            log("Aviso: mesmo apos fallback, git describe ainda nao encontrou tag semantica.")


    # Inicializa submodulos do PX4 usados pela simulacao Gazebo.
    def ensure_px4_submodules(self):
        if not PX4_DIR.is_dir():
            return
        self.ensure_px4_git_metadata()
        if not (PX4_DIR / "Tools/simulation/gz/models/x500").is_dir():
            log("Submodulo Tools/simulation/gz ausente. Inicializando submodulos do PX4...")
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
                raise RuntimeError(f"Nao encontrei a pasta: {PX4_DIR}")
            log(f"Aviso: pasta PX4 nao encontrada: {PX4_DIR}")
            return
        self.ensure_px4_submodules()
        if not uav_dir.is_dir():
            if strict:
                raise RuntimeError(f"Nao encontrei a pasta: {uav_dir}")
            log(f"Aviso: pasta UAVJamSim nao encontrada: {uav_dir}")
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
            raise RuntimeError(f"Nao encontrei o modelo: {model_src}")
        else:
            log(f"Aviso: modelo x500_uavjamsim nao encontrado: {model_src}")

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
            log(f"Aviso: mundo customizado nao encontrado: {world_src}. Mantendo default do PX4, se existir.")

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
                        raise RuntimeError("Nao encontrei fechamento da lista de airframes no CMakeLists.txt")
                    text = text[:idx] + entry + "\n" + text[idx:]
                airframe_cmake.write_text(text, encoding="utf-8")
                log("CMakeLists atualizado com 4020_gz_x500_uavjamsim.")
            else:
                log("CMakeLists ja contem 4020_gz_x500_uavjamsim.")
        elif strict:
            raise RuntimeError(f"Nao encontrei: {airframe_cmake}")
        else:
            log(f"Aviso: CMakeLists de airframes nao encontrado: {airframe_cmake}")

        if px4_link.is_symlink() or not px4_link.exists():
            px4_link.unlink(missing_ok=True)
            os.symlink("../PX4-Autopilot", px4_link)
            log(f"Link px4: {px4_link} -> ../PX4-Autopilot")
        else:
            log(f"Aviso: {px4_link} ja existe e nao e link. Mantendo como esta.")

    # Inicia o fluxo de selecao de modelo e mundo.
    def start_uavedusim_flow(self):
        if not PX4_DIR.is_dir():
            self.show_error(f"Nao encontrei a pasta: {PX4_DIR}")
            return
        try:
            self.ensure_uavjamsim_links(False)
            models = self.get_models()
        except Exception as e:
            self.show_error(str(e))
            return
        self.render_menu("Escolha o modelo de drone para a simulação", models, self.handle_model_choice, badge="ESCOLHER DRONE", on_back=self.show_main_menu)

    # Lista modelos Gazebo executaveis pelo PX4.
    # Salva o modelo escolhido e abre a selecao de mundo.
    # Lista mundos .sdf disponiveis para a simulacao.
    # Salva o mundo escolhido e inicia QGC/PX4.
    # Abre o QGroundControl sem terminal visivel.
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
                self.show_error(f"Falha ao abrir QGroundControl sem terminal: {e}")
                return False

        if qgc_already_running():
            log("QGroundControl ja esta aberto. Nao vou abrir outra instancia.")
            notify_msg("QGroundControl ja esta aberto.")
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

        log(f"Aviso: QGroundControl nao encontrado em {APP_DIR}")
        notify_msg("QGroundControl nao encontrado.")


    # Abre PX4/Gazebo e prepara a tela de espera.
    def open_px4_and_wait(self):
        cmd = f'''{load_gazebo_env_block()}
cd {shell_quote(str(PX4_DIR))}
if [ ! -d 'Tools/simulation/gz/models/x500' ]; then
    echo '[UAVEDUSIM] Submodulo Tools/simulation/gz ausente. Inicializando submodulos...'
    git submodule sync --recursive || true
    git submodule update --init --recursive Tools/simulation/gz || git submodule update --init --recursive || true
fi
cd {shell_quote(str(APP_DIR))}
echo '[UAVEDUSIM] Garantindo links simbolicos e airframe do UAVJamSim...'
ls -l {shell_quote(str(PX4_DIR / "Tools/simulation/gz/models/x500_uavjamsim"))} 2>/dev/null || true
ls -l {shell_quote(str(PX4_DIR / "Tools/simulation/gz/worlds/default.sdf"))} 2>/dev/null || true
ls -l {shell_quote(str(PX4_DIR / "ROMFS/px4fmu_common/init.d-posix/airframes/4020_gz_x500_uavjamsim"))} 2>/dev/null || true
if [ {shell_quote(self.model)} = 'gz_x500_uavjamsim' ]; then
    if [ ! -f {shell_quote(str(PX4_DIR / "ROMFS/px4fmu_common/init.d-posix/airframes/4020_gz_x500_uavjamsim"))} ]; then
        echo '[UAVEDUSIM] ERRO: airframe 4020_gz_x500_uavjamsim nao foi instalado no ROMFS.'
        exit 1
    fi
    if [ -L {shell_quote(str(PX4_DIR / "ROMFS/px4fmu_common/init.d-posix/airframes/4020_gz_x500_uavjamsim"))} ]; then
        echo '[UAVEDUSIM] ERRO: airframe 4020_gz_x500_uavjamsim ainda e link simbolico. Ele precisa ser arquivo real.'
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

    # Mostra a tela de carregamento da simulacao.
    def _wait_status_lines(self, reason):
        raw = str(reason or "")
        parts = [part.strip() for part in raw.replace(";", "|").split("|") if part.strip()]

        px4_status = "iniciando"
        gazebo_status = "abrindo"

        for part in parts:
            low = part.lower()

            if "gazebo" in low:
                if "aberto" in low:
                    gazebo_status = "aberto"
                elif "ainda nao detectado" in low or "nao detectado" in low:
                    gazebo_status = "aguardando abrir"
                else:
                    gazebo_status = part.replace("Gazebo ", "").strip()

            elif "px4" in low or "ready for takeoff" in low or "commander" in low:
                if "ready for takeoff" in low:
                    px4_status = 'pronto para decolar'
                elif "iniciado" in low and "aguardando" in low:
                    px4_status = 'iniciado; aguardando "Ready for takeoff"'
                elif "ainda nao detectado" in low or "nao detectado" in low:
                    px4_status = "aguardando iniciar"
                else:
                    px4_status = part.replace("PX4 ", "").strip()

        return px4_status, gazebo_status

    def _format_wait_status_text(self, elapsed, reason, *, ok=False, timed_out=False):
        px4_status, gazebo_status = self._wait_status_lines(reason)

        if ok:
            return (
                "Simulacao pronta.\n"
                f"PX4: {px4_status}\n"
                f"Gazebo: {gazebo_status}\n"
                "Abrindo menu de ataques..."
            )

        if timed_out:
            return (
                f"A simulacao ainda nao ficou pronta apos {self.wait_soft_timeout}s.\n"
                f"PX4: {px4_status}\n"
                f"Gazebo: {gazebo_status}\n"
                "Verifique o terminal do PX4/Gazebo."
            )

        return (
            f"Preparando simulacao... {elapsed}s\n"
            f"PX4: {px4_status}\n"
            f"Gazebo: {gazebo_status}\n"
            "O menu de ataques abre automaticamente quando tudo estiver pronto."
        )

    # Verifica periodicamente se PX4 e Gazebo estao prontos.
    def check_px4_ready(self):
        if self.page != "wait":
            return

        elapsed = int(time.monotonic() - self.wait_start)

        if getattr(self, "restart_waiting_for_cleanup", False):
            if hasattr(self, "status_lbl"):
                self.status_lbl.configure(
                    text=(
                        f"Reiniciando simulacao... {elapsed}s\n"
                        "Fechando PX4/Gazebo antigos antes de abrir novamente."
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
            log(f"Simulacao pronta para ataques: {reason}. Abrindo menu de ataques.")
            self.root.after(700, self.show_attack_menu)
            return

        if elapsed >= self.wait_soft_timeout and not self.wait_timeout_reached:
            self.wait_timeout_reached = True
            self.status_lbl.configure(
                text=self._format_wait_status_text(elapsed, reason, timed_out=True),
                fg=DANGER,
            )
            self._reflow_wait_status()
            log(f"Aviso: simulacao ainda nao esta pronta apos {self.wait_soft_timeout}s. Estado: {reason}. Continuando a espera.")
        else:
            self.status_lbl.configure(
                text=self._format_wait_status_text(elapsed, reason),
                fg=WARN,
            )
            self._reflow_wait_status()

        self.root.after(1000, self.check_px4_ready)


    # Compila ferramentas de ataque e mitigacao quando necessario.
    def ensure_spoofer_tools(self):
        spoofer_dir = APP_DIR / "UAVJamSim/spoofer"
        build_dir = spoofer_dir / "build"
        spoofer_bin = build_dir / "spoofer"
        mitigator_bin = build_dir / "attack_mitigator"
        if not spoofer_dir.is_dir():
            raise RuntimeError(f"Nao encontrei a pasta do spoofer: {spoofer_dir}")
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
            raise RuntimeError("Falha ao compilar ferramentas do spoofer. Veja o log.")
        for f in [spoofer_bin, mitigator_bin]:
            try:
                f.chmod(f.stat().st_mode | 0o111)
            except Exception:
                pass
        if not spoofer_bin.exists():
            raise RuntimeError(f"Nao encontrei o executavel: {spoofer_bin}")
        if not mitigator_bin.exists():
            raise RuntimeError(f"Nao encontrei o executavel: {mitigator_bin}")
        return spoofer_bin, mitigator_bin


    # Renderiza a tela de ataques, mitigacao e prerequisitos.
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

        outer = tk.Frame(self.content, bg=SURFACE)
        outer.pack(fill="both", expand=True)

        canvas = tk.Canvas(outer, bg=SURFACE, highlightthickness=0, bd=0)
        scrollbar = ttk.Scrollbar(
            outer,
            orient="vertical",
            style="Dark.Vertical.TScrollbar",
            command=canvas.yview,
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
                canvas.yview_scroll(-3, "units")
            elif getattr(event, "num", None) == 5:
                canvas.yview_scroll(3, "units")
            elif getattr(event, "delta", 0):
                canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

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
        header.pack(fill="x", padx=self.CONTENT_PAD, pady=(self.px(18), 0))

        tk.Label(
            header,
            text="Ataques GPS Jamming",
            bg=SURFACE,
            fg=TEXT,
            font=(self.FONT_FAMILY, self.fs(17), "bold"),
        ).pack(anchor="w")

        tk.Label(
            header,
            text="Envie takeoff e destino. Padrao: dist. 350 m, rot. 0 graus, alt. 2.5 m.",
            bg=SURFACE,
            fg=MUTED,
            font=(self.FONT_FAMILY, self.FS_SMALL),
        ).pack(anchor="w", pady=(self.px(7), 0))

        self.takeoff_status_var = tk.StringVar()
        self.destination_status_var = tk.StringVar()
        self.destination_forward_var = tk.StringVar(value=self.destination_forward_value)
        self.destination_right_var = tk.StringVar(value=self.destination_right_value)
        self.destination_altitude_var = tk.StringVar(value=self.destination_altitude_value)

        prereq_panel = tk.Frame(page, bg=SURFACE2, highlightbackground=BORDER, highlightthickness=1)
        self.prereq_panel = prereq_panel
        prereq_panel.pack(fill="x", padx=self.CONTENT_PAD, pady=(self.px(10), 0))
        prereq_panel.grid_columnconfigure(0, weight=1)
        prereq_panel.grid_columnconfigure(1, weight=1)
        prereq_panel.grid_columnconfigure(2, weight=0)

        takeoff_box = tk.Frame(prereq_panel, bg=SURFACE2)
        self.takeoff_box = takeoff_box
        takeoff_box.grid(row=0, column=0, sticky="nsew", padx=(self.px(14), self.px(8)), pady=self.px(9))

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
        destination_box.grid(row=0, column=1, sticky="nsew", padx=(self.px(8), self.px(8)), pady=self.px(9))

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

        _destination_field(destination_grid, 0, "Distancia (m)", self.destination_forward_var, 9)
        _destination_field(destination_grid, 1, "Rotacao (graus)", self.destination_right_var, 9)
        _destination_field(destination_grid, 2, "Alt. rel. (m)", self.destination_altitude_var, 9)

        prereq_buttons = tk.Frame(prereq_panel, bg=SURFACE2)
        self.prereq_buttons = prereq_buttons
        prereq_buttons.grid(row=0, column=2, sticky="e", padx=(self.px(8), self.px(14)), pady=self.px(9))

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
            pady=self.px(7),
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
            pady=self.px(7),
            font=(self.FONT_FAMILY, self.FS_SMALL, "bold"),
            cursor="hand2",
        )
        self.destination_button.pack(anchor="e", fill="x", pady=(self.px(7), 0))

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
            pady=(self.px(14), 0),
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

        left_inner = tk.Frame(self.attack_left, bg=SURFACE2)
        left_inner.pack(fill="both", expand=True, padx=self.px(16), pady=self.px(16))

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
            wraplength=self.px(560),
        )
        self.attack_warning_label.pack(anchor="w", fill="x", pady=(self.px(10), 0))

        right_inner = tk.Frame(self.attack_right, bg=SURFACE2)
        right_inner.pack(fill="both", expand=True, padx=self.px(18), pady=self.px(16))

        tk.Label(
            right_inner,
            text="Mitigacao",
            bg=SURFACE2,
            fg=TAG_FG,
            font=(self.FONT_FAMILY, self.fs(12), "bold"),
        ).pack(anchor="w")

        self.mitigation_desc_label = tk.Label(
            right_inner,
            text="Ativa/desativa a mitigacao do simulador.",
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
        status_frame.pack(fill="both", expand=True, pady=(self.px(16), 0))

        status_header = tk.Frame(status_frame, bg=BG)
        status_header.pack(fill="x", padx=self.px(14), pady=(self.px(12), self.px(6)))

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
        status_text_wrap.pack(fill="both", expand=True, padx=self.px(14), pady=(0, self.px(12)))
        status_text_wrap.grid_columnconfigure(0, weight=1)
        status_text_wrap.grid_rowconfigure(0, weight=1)

        self.status_text = tk.Text(
            status_text_wrap,
            height=10,
            bg="#11111b",
            fg=TEXT,
            insertbackground=TEXT,
            relief="flat",
            bd=0,
            wrap="word",
            font=(self.FONT_FAMILY, self.FS_SMALL),
        )
        self.status_text.grid(row=0, column=0, sticky="nsew")

        status_scroll = ttk.Scrollbar(
            status_text_wrap,
            orient="vertical",
            style="Dark.Vertical.TScrollbar",
            command=self.status_text.yview,
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

        footer = tk.Frame(page, bg=SURFACE)
        footer.pack(fill="x", padx=self.CONTENT_PAD, pady=(self.px(14), self.FOOTER_BOTTOM_PAD))
        footer.grid_columnconfigure(0, weight=1)
        footer.grid_columnconfigure(1, weight=0)

        self.attack_hint_label = tk.Label(
            footer,
            text="Noise/Pulse: digite a intensidade. Blackout nao usa intensidade.",
            bg=SURFACE,
            fg=BORDER,
            font=(self.FONT_FAMILY, self.FS_TINY),
            anchor="w",
            justify="left",
        )
        self.attack_hint_label.grid(row=0, column=0, sticky="ew", padx=(0, self.px(10)))

        self.attack_btn_frame = tk.Frame(footer, bg=SURFACE)
        self.attack_btn_frame.grid(row=0, column=1, sticky="e")

        self.restart_button = tk.Button(
            self.attack_btn_frame,
            text="Reiniciar simulação",
            command=self.restart_simulation_from_attack,
            bg=SURFACE2,
            fg=TEXT,
            activebackground=BORDER,
            activeforeground=TEXT,
            relief="flat",
            bd=0,
            padx=self.px(12),
            pady=self.px(8),
            font=(self.FONT_FAMILY, self.FS_SMALL, "bold"),
            cursor="hand2",
        )
        self.restart_button.pack(side="left", padx=(0, self.px(8)))

        ttk.Button(
            self.attack_btn_frame,
            text="Fechar",
            style="Cancel.TButton",
            command=self.leave_attack_screen,
        ).pack(side="left", padx=(0, self.px(8)))

        self.stop_button = tk.Button(
            self.attack_btn_frame,
            text="Desligar jamming",
            command=self.stop_jamming_from_ui,
            bg="#3e2937",
            fg="#f2cdcd",
            activebackground="#553645",
            activeforeground="#11111b",
            relief="flat",
            bd=0,
            padx=self.px(14),
            pady=self.px(9),
            font=(self.FONT_FAMILY, self.FS_BUTTON, "bold"),
            cursor="hand2",
        )
        self.stop_button.pack(side="left", padx=(0, self.px(8)))

        self.apply_button = ttk.Button(
            self.attack_btn_frame,
            text="Aplicar",
            style="Primary.TButton",
            command=self.apply_selected_attack_from_ui,
        )
        self.apply_button.pack(side="left")

        self.attack_body.bind("<Configure>", self._reflow_attack_layout)
        footer.bind("<Configure>", self._reflow_attack_footer)

        self.update_attack_selection_ui()
        self.update_mitigation_ui()
        self.update_attack_lock_ui()
        self.root.after(80, self._reflow_attack_layout)

    # Reorganiza o painel de decolagem e destino conforme a largura.
    # Reorganiza os paineis de ataque e mitigacao.
    # Retorna o nome exibido para o tipo de ataque.
    def attack_label(self, attack_key):
        return {
            "noise": "Jamming Noise",
            "blackout": "Jamming Blackout",
            "pulse": "Jamming Pulse",
        }.get(attack_key, str(attack_key))

    # Explica, em linguagem simples, o que cada tipo de ataque faz ao GPS do drone.
    def _attack_effect_description(self, attack_key):
        return {
            "noise": (
                "adiciona ruido aleatorio as coordenadas de GPS lidas pelo drone, fazendo a "
                "posicao estimada 'tremer' e se afastar aos poucos da posicao real."
            ),
            "blackout": (
                "bloqueia completamente o sinal de GPS, deixando o drone sem nenhuma leitura "
                "de posicao valida."
            ),
            "pulse": (
                "liga e desliga o jamming em pulsos, causando perda intermitente de posicao "
                "GPS (o drone alterna entre 'ver' e 'nao ver' sua posicao)."
            ),
        }.get(attack_key, "interfere no sinal de GPS recebido pelo drone.")

    # Explica o que se espera observar no drone/telemetria enquanto o ataque esta ativo.
    def _attack_expected_observation(self, attack_key):
        return {
            "noise": (
                "Espere ver a posicao estimada 'tremendo' na telemetria, com pequenos desvios "
                "da rota planejada, mesmo sem perder o GPS por completo."
            ),
            "blackout": (
                "Espere ver o GPS sem fix (sem posicao valida); o drone pode parar de responder "
                "corretamente a comandos de posicao enquanto o sinal nao volta."
            ),
            "pulse": (
                "Espere ver o GPS alternando entre 'com posicao' e 'sem posicao' repetidamente, "
                "com saltos na posicao estimada a cada vez que o sinal retorna. Esses saltos nao "
                "sao necessariamente pequenos: quanto mais tempo o GPS ficar fora, maior pode ser "
                "a diferenca entre a posicao estimada e a posicao real quando o sinal volta."
            ),
        }.get(attack_key, "Acompanhe a telemetria para ver o efeito do ataque no GPS.")

    # Explica, em linguagem simples, o que a mitigacao faz de forma geral (independente do ataque).
    def _mitigation_general_description(self):
        return "protecao ativada contra ataques ao GPS."

    # Explica, de forma didatica, o que a mitigacao faz especificamente contra cada ataque.
    def _mitigation_defense_description(self, attack_key):
        return {
            "noise": (
                "o GPS ainda envia leituras, so que ruidosas, entao a mitigacao usa um filtro de "
                "Kalman que combina essas leituras com o barometro (para a altitude) e com o "
                "movimento esperado do drone, dando menos peso as medicoes que fogem muito do "
                "previsto. Isso suaviza o 'tremor' e mantem a posicao estimada proxima da real."
            ),
            "blackout": (
                "o GPS parou de enviar leituras validas, entao a mitigacao usa dead reckoning: "
                "parte da ultima posicao confiavel e projeta a posicao atual com a velocidade e "
                "direcao do drone, apoiada pelo barometro (altitude) e pela odometria (distancia "
                "percorrida), ate o sinal de GPS voltar."
            ),
            "pulse": (
                "o sinal de GPS aparece e desaparece, entao a mitigacao alterna entre as duas "
                "estrategias: usa o filtro de Kalman (com apoio do barometro) enquanto o sinal "
                "esta presente, mesmo que ruidoso, e passa para dead reckoning com odometria nos "
                "momentos em que o sinal some, evitando saltos bruscos quando o GPS retorna."
            ),
        }.get(attack_key, "a mitigacao esta atuando para reduzir o efeito desse ataque.")

    # Explica o risco especifico de nao ter mitigacao ativa contra cada ataque.
    def _attack_no_mitigation_description(self, attack_key):
        return {
            "noise": (
                "o GPS ruidoso chega direto ao PX4 sem nenhuma correcao. O drone pode se desviar "
                "da rota, oscilar de posicao e perder estabilidade ate nao conseguir alcancar o "
                "destino configurado."
            ),
            "blackout": (
                "sem nenhuma leitura valida de GPS chegando ao PX4, o proprio piloto automatico "
                "deve acionar failsafe e iniciar um pouso de emergencia (RTL/land) usando apenas a "
                "posicao estimada disponivel. Como essa posicao pode estar desatualizada ou incorreta, "
                "o drone corre o risco de pousar em terrenos inadequados, como agua, telhados, "
                "estradas ou vegetacao densa, em vez de um local seguro."
            ),
            "pulse": (
                "cada vez que o GPS some, o PX4 fica sem leitura valida de posicao; dependendo de "
                "quanto tempo o sinal ficar fora, o piloto automatico pode acionar failsafe e "
                "iniciar um pouso de emergencia em terreno inapropriado (agua, telhados, estradas "
                "ou vegetacao densa), assim como no blackout. Quando o sinal retorna, ainda "
                "ruidoso, o drone pode sofrer saltos de posicao que nao sao necessariamente "
                "pequenos. Sem mitigacao, essa alternancia entre 'sem GPS' e 'GPS ruidoso' aumenta "
                "o risco de desvio de rota e de um pouso de emergencia em local inadequado."
            ),
        }.get(
            attack_key,
            "o GPS corrompido chega direto ao PX4. O drone pode se desviar da rota, perder "
            "estabilidade ou nao alcancar o destino configurado.",
        )

    # Monta a mensagem quando outro ataque esta bloqueado.
    def active_attack_locked_message(self, requested_attack=None):
        active_key = getattr(self, "active_attack_key", None)
        active_label = self.attack_label(active_key) if active_key else "nenhum"

        if requested_attack and active_key and requested_attack != active_key:
            requested_label = self.attack_label(requested_attack)
            return (
                f"{requested_label} bloqueado nesta simulacao. "
                f"Ataque atual: {active_label}. "
                "Para trocar o tipo de jamming, reinicie a simulacao. "
                "O ataque atual ainda pode ter intensidade alterada, ser desligado/reativado e usar mitigacao ON/OFF."
            )

        if active_key:
            return (
                f"Para trocar o tipo de jamming, reinicie a simulacao. "
                f"Ataque atual: {active_label}. "
                "Voce ainda pode alterar a intensidade, desligar/reativar o ataque e ativar/desativar a mitigacao."
            )

        return ""

    def _set_attack_warning_ui(self, text="", level="warn"):
        self.attack_warning = text or ""

        if not hasattr(self, "attack_warning_var"):
            return

        self.attack_warning_var.set(self.attack_warning)

        color = DANGER if level == "danger" else WARN
        self.attack_warning_label.configure(fg=color)

    # Retorna uma descricao curta do ataque selecionado.
    def _attack_description(self, attack_key):
        if not self.attack_unlocked():
            return self.prereq_status_message()

        base = {
            "noise": "Ruido no GPS com intensidade configuravel. Use um valor numerico, por exemplo: 80.",
            "blackout": "Blackout corta o sinal de GPS. Nao requer intensidade.",
            "pulse": "Pulse aplica jamming intermitente. Use um valor numerico, por exemplo: 0.3.",
        }[attack_key]

        active_key = getattr(self, "active_attack_key", None)

        if active_key == attack_key:
            return base + " Este e o ataque ativo desta simulacao."

        if active_key and attack_key != active_key:
            return f"{self.attack_label(attack_key)} esta bloqueado nesta simulacao."

        return base

    def attack_unlocked(self):
        return bool(getattr(self, "takeoff_sent", False)) and bool(getattr(self, "destination_sent", False))

    def prereq_status_message(self):
        if not getattr(self, "takeoff_sent", False):
            return "Ataques bloqueados. Envie commander takeoff e depois o destino."
        if getattr(self, "destination_release_pending", False):
            return "Ataques bloqueados. Aguardando timer apos o takeoff antes de liberar o destino."
        if not getattr(self, "destination_sent", False):
            return "Ataques bloqueados. Envie o destino padrao: distancia + rotacao."
        return "Takeoff e destino enviados. Ataques liberados."

    # Envia comandos para o console PX4 via FIFO.
    def send_px4_console_command(self, command):
        fifo = Path(getattr(self, "px4_console_fifo", RUNTIME_DIR / f"px4_console_{SESSION_ID}.fifo"))

        if not fifo.exists():
            log(f"FIFO do console PX4 nao encontrado: {fifo}")
            return False, f"Console PX4 ainda nao esta pronto: {fifo}"

        try:
            fd = os.open(str(fifo), os.O_WRONLY | os.O_NONBLOCK)
        except OSError as e:
            log(f"Falha ao abrir FIFO do PX4: {fifo}: {e}")
            return False, "Nao consegui enviar comando ao PX4. Verifique se o terminal PX4 ainda esta aberto."

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
        # abaixo do botao. Assim o botao nao troca de texto/cor a cada segundo.
        if hasattr(self, "destination_button") and not getattr(self, "destination_release_pending", False):
            self.destination_button.configure(
                text=("Reenviar destino" if ok else "Enviar destino"),
                bg=("#35433b" if ok else ACCENT),
                fg=(SEL_FG if ok else "#11111b"),
                activebackground=("#445849" if ok else ACCENT_DIM),
                activeforeground="#11111b",
            )

    # Atualiza botoes e mensagens de decolagem/destino.
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
                    "Takeoff detectado pelo PX4.\nAguardando liberacao do destino.",
                    ok=True,
                )
            else:
                self._set_takeoff_status_ui(
                    "Takeoff detectado pelo PX4.\nEnvio de destino liberado.",
                    ok=True,
                )
        elif getattr(self, "takeoff_waiting", False):
            self._set_takeoff_status_ui(
                "Aguardando confirmacao do PX4:\nINFO  [commander] Takeoff detected",
                ok=False,
                pending=True,
            )
        else:
            self._set_takeoff_status_ui("Aguardando:\ncommander takeoff", ok=False)

        if getattr(self, "destination_sent", False):
            msg = (
                f"Destino enviado:\n"
                f"dist={self.destination_forward_value} m, rot={self.destination_right_value} graus,\n"
                f"alt={self.destination_altitude_value} m."
            )
            self._set_destination_status_ui(msg, ok=True)
        elif release_pending:
            self._set_destination_status_ui(
                "Destino aguardando liberacao apos o takeoff.",
                ok=False,
            )
        else:
            msg = (
                f"Destino pendente:\n"
                f"dist={self.destination_forward_value} m, rot={self.destination_right_value} graus,\n"
                f"alt={self.destination_altitude_value} m."
            )
            self._set_destination_status_ui(msg, ok=False)

        if hasattr(self, "destination_timer_var"):
            self.destination_timer_var.set(f"Destino liberado em {remaining_s}s" if release_pending else "")

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
        return (
            "Verifique tambem no terminal PX4 se apareceu Ready for takeoff! "
            "antes de enviar novamente. Se ainda nao apareceu, aguarde essa mensagem e tente de novo."
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
            self.destination_timer_var.set(f"Destino liberado em {remaining_s}s" if remaining_s > 0 else "")

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

        # Atualiza o layout uma vez para exibir a mensagem abaixo do botao.
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
            self._log_status_event("Destino liberado", "Envio de destino disponivel.", level="ok")
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
            self._log_status_event("Takeoff detectado", "Destino sera liberado apos o timer.", level="ok")
            notify_msg(status_msg)
            self._start_destination_release_timer()
            return

        self.takeoff_sent = False
        self.destination_release_pending = False
        self.destination_release_until = 0.0
        self.update_prereq_ui()
        self._set_takeoff_status_ui(message, ok=False)
        self._log_status_event("Takeoff nao enviado", message, level="danger")
        notify_msg(message)
        self.update_attack_lock_ui()

    # Envia commander takeoff e so libera destino apos o PX4 confirmar Takeoff detected.
    def send_takeoff_from_ui(self):
        if getattr(self, "takeoff_waiting", False):
            return

        if getattr(self, "takeoff_sent", False):
            if getattr(self, "destination_release_pending", False):
                msg = "Takeoff detectado pelo PX4. Aguarde o timer do botao de destino terminar."
            else:
                msg = "Takeoff ja foi detectado pelo PX4. O envio de destino esta liberado."
            self._set_takeoff_status_ui(msg, ok=True)
            notify_msg(msg)
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
                    "Nao foi possivel confirmar o takeoff no PX4 apos duas tentativas. "
                    f"Ultimo erro: {last_error}. {hint}"
                )
            else:
                message = (
                    "O comando commander takeoff foi enviado, mas o PX4 nao registrou "
                    "INFO  [commander] Takeoff detected dentro do tempo limite apos duas tentativas. "
                    f"{hint}"
                )

            self.root.after(0, lambda: self._finish_takeoff_wait(False, message))

        threading.Thread(target=worker, daemon=True).start()

    # Valida distancia, rotacao e altitude do destino.
    def _parse_destination_values(self):
        try:
            distance = float(self.destination_forward_var.get().strip().replace(",", "."))
            rotation_deg = float(self.destination_right_var.get().strip().replace(",", "."))
            rel_alt = float(self.destination_altitude_var.get().strip().replace(",", "."))
        except Exception:
            raise ValueError("Destino invalido. Use numeros em Distancia, Rotacao e Altitude. Exemplo: 350, 0, 2.5")

        if distance <= 0:
            raise ValueError("Distancia precisa ser maior que zero.")

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
            notify_msg(msg)
            self.update_attack_lock_ui()
            return

        if getattr(self, "destination_release_pending", False):
            remaining_s = int(max(0.0, getattr(self, "destination_release_until", 0.0) - time.monotonic()) + 0.999)
            msg = f"Aguarde o timer do botao de destino terminar antes de enviar o destino. Restam {remaining_s}s."
            self._set_destination_status_ui(msg, ok=False)
            self._log_status_event("Destino aguardando timer", msg, level="warn")
            notify_msg(msg)
            self.update_attack_lock_ui()
            return

        try:
            distance, rotation_deg, rel_alt = self._parse_destination_values()
        except ValueError as e:
            msg = str(e)
            self._set_destination_status_ui(msg, ok=False)
            self._log_status_event("Destino invalido", msg, level="danger")
            notify_msg(msg)
            return

        self.destination_sent = False
        self._set_destination_status_ui("Enviando destino via MAVLink...", ok=False)
        self._log_status_event(
            "Enviando destino via MAVLink",
            f"O drone vai andar {self.destination_forward_value} m a frente, girar "
            f"{self.destination_right_value} graus e ajustar a altitude para "
            f"{self.destination_altitude_value} m em relacao ao ponto de decolagem.",
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
                        f"Destino enviado: dist={self.destination_forward_value} m, "
                        f"rot={self.destination_right_value} graus, alt={self.destination_altitude_value} m. "
                        "Ataques liberados."
                    )
                    self._set_destination_status_ui(msg_ok, ok=True)
                    self._log_status_event(
                        "Drone em deslocamento",
                        f"Indo {self.destination_forward_value} m a frente, girando "
                        f"{self.destination_right_value} graus e ajustando para "
                        f"{self.destination_altitude_value} m de altitude. Ataques liberados.",
                        level="ok",
                    )
                    notify_msg(msg_ok)
                else:
                    self.destination_sent = False
                    self._set_destination_status_ui(msg, ok=False)
                    self._log_status_event("Falha ao enviar destino", msg, level="danger")
                    notify_msg(msg)

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
        print("[UAVEDUSIM] pymavlink nao encontrado. Tentando instalar com pip --user...", flush=True)
        subprocess.check_call(
            [sys.executable, "-m", "pip", "install", "--user", "pymavlink"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.STDOUT,
        )
        from pymavlink import mavutil
    except Exception as install_error:
        print("[UAVEDUSIM] ERRO: pymavlink nao esta disponivel.", flush=True)
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
# vertical muito baixa. Esta opcao faz primeiro uma subida quase vertical
# no ponto atual e so depois envia o deslocamento horizontal.
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
        print(f"[UAVEDUSIM] Tentando conexao MAVLink: {url}", flush=True)
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
    print("[UAVEDUSIM] ERRO: nao consegui conectar ao PX4 via MAVLink.", flush=True)
    if last_error:
        print("[UAVEDUSIM] Ultimo erro: " + last_error, flush=True)
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
                print(f"[UAVEDUSIM] Parametro {param_name} ajustado para {float(value):.2f}", flush=True)
                return True

        print(f"[UAVEDUSIM] Parametro {param_name} enviado sem confirmacao.", flush=True)
        return True

    except Exception as e:
        print(f"[UAVEDUSIM] Aviso: nao foi possivel ajustar {param_name}: {e}", flush=True)
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
        print(f"[UAVEDUSIM] Aviso: nao foi possivel enviar DO_CHANGE_SPEED ({label}): {e}", flush=True)
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
    print("[UAVEDUSIM] ERRO: nao recebi GLOBAL_POSITION_INT do PX4.", flush=True)
    sys.exit(4)

if attitude is None:
    print("[UAVEDUSIM] ERRO: nao recebi ATTITUDE do PX4 para calcular a frente do drone.", flush=True)
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

# Usa ATTITUDE.yaw como referencia da frente real do drone.
# A rotacao relativa afeta apenas a direcao do destino, nao o yaw comandado.
heading_deg = (math.degrees(float(attitude.yaw)) + 360.0) % 360.0

# Rotacao relativa e uma direcao em graus a partir da frente atual do drone.
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
                f"alvo={target_rel_alt:.2f} m, tolerancia={tolerance_m:.2f} m",
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
        print("[UAVEDUSIM] Aviso: nao recebi altitude durante a subida vertical.", flush=True)
    else:
        print(
            f"[UAVEDUSIM] Aviso: subida vertical nao atingiu a altitude alvo dentro do tempo. "
            f"Ultima alt_rel={last_rel_alt:.2f} m, alvo={target_rel_alt:.2f} m.",
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
        print("[UAVEDUSIM] Aviso: ACK da subida vertical nao foi recebido. Continuando a espera de altitude.", flush=True)
    elif not climb_ack:
        print("[UAVEDUSIM] Aviso: PX4 rejeitou a subida vertical separada. O destino final ainda sera enviado.", flush=True)

    wait_until_relative_alt(target_rel_alt_m, dest_alt_stage_tolerance_m, alt_stage_timeout_s)

print(
    "[UAVEDUSIM] Destino calculado: "
    f"lat={target_lat:.7f}, lon={target_lon:.7f}, alt_amsl={target_alt_amsl_m:.2f}, "
    f"yaw_base_attitude={heading_deg:.1f}, rotacao_rel={rotation_deg:.1f}, direcao_destino={target_heading_deg:.1f}, "
    f"yaw_comando=ignorado, distancia={distance_m:.1f}, north={north_m:.1f}, east={east_m:.1f}, "
    f"alt_relativa_alvo={target_rel_alt_m:.2f}, "
    f"vel_subida={dest_climb_speed_mps:.2f}, vel_horizontal={dest_horizontal_speed_mps:.2f}",
    flush=True,
)

# O PX4 pode confirmar (ACK) o primeiro DO_REPOSITION enquanto ainda esta
# trocando para o modo Hold/Loiter, e nesse instante de transicao ele pode
# ignorar a altitude enviada e manter a altitude atual. Por isso, enviamos
# o comando uma segunda vez pouco depois: nessa segunda chamada o drone ja
# esta em Hold/Loiter e a nova altitude e aplicada de forma confiavel.
print("[UAVEDUSIM] Enviando 1a tentativa de DO_REPOSITION...", flush=True)
send_reposition()
first_ok = wait_reposition_ack(8)

if first_ok is None:
    print("[UAVEDUSIM] ERRO: destino enviado, mas ACK de DO_REPOSITION nao foi recebido (1a tentativa).", flush=True)
    sys.exit(6)

if not first_ok:
    print("[UAVEDUSIM] ERRO: PX4 rejeitou o primeiro DO_REPOSITION.", flush=True)
    sys.exit(5)

time.sleep(1.5)

print("[UAVEDUSIM] Reenviando DO_REPOSITION para garantir a altitude configurada...", flush=True)
send_reposition()
second_ok = wait_reposition_ack(8)

if second_ok is None:
    print("[UAVEDUSIM] ERRO: destino enviado, mas ACK de DO_REPOSITION nao foi recebido (2a tentativa).", flush=True)
    sys.exit(6)

if not second_ok:
    print("[UAVEDUSIM] ERRO: PX4 rejeitou o DO_REPOSITION na 2a tentativa (altitude pode nao ter sido aplicada).", flush=True)
    sys.exit(5)

sys.exit(0)
"""
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
            return False, "Timeout ao enviar destino via MAVLink."

        if proc.stdout:
            for line in proc.stdout.splitlines():
                log(line)

        if proc.returncode == 0:
            return True, "Destino enviado via MAVLink."

        return False, f"Falha ao enviar destino via MAVLink. Codigo={proc.returncode}. Veja o log."

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
            self.apply_button.configure(state=("normal" if apply_allowed else "disabled"))

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
                self.attack_desc_var.set(self.prereq_status_message())
                if hasattr(self, "attack_desc_label"):
                    self.attack_desc_label.configure(fg=MUTED)
                self._set_attack_warning_ui("")
            else:
                self.attack_desc_var.set(self._attack_description(self.attack_type))
                if hasattr(self, "attack_desc_label"):
                    self.attack_desc_label.configure(fg=MUTED)

                if active_key:
                    self._set_attack_warning_ui(self.active_attack_locked_message(), level="warn")
                else:
                    self._set_attack_warning_ui("")

    # Cria a linha visual de cada ataque.
    # Seleciona o ataque quando ele ainda nao esta bloqueado.
    def select_attack_type(self, attack_key):
        if not self.attack_unlocked():
            msg = self.prereq_status_message()
            self._set_attack_status_ui(msg, level="warn")
            notify_msg(msg)
            self.update_attack_lock_ui()
            return

        active_key = getattr(self, "active_attack_key", None)

        if active_key and attack_key != active_key:
            msg = self.active_attack_locked_message(attack_key)
            self.update_attack_lock_ui()
            self._set_attack_warning_ui(msg, level="danger")
            notify_msg("Novo ataque bloqueado. Reinicie a simulacao para trocar o tipo de jamming.")
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

        self.attack_desc_var.set(self._attack_description(self.attack_type))
        self.update_attack_lock_ui()

    # Adiciona um evento didatico ao log de status em tempo real (nao apaga o historico).
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

    # Desenha uma unica entrada do log no final da caixa de Status.
    def _append_status_entry(self, entry):
        if not hasattr(self, "status_text") or not getattr(self, "status_box_enabled", True):
            return

        self.status_text.configure(state="normal")

        if self.status_text.index("end-1c") != "1.0":
            self.status_text.insert("end", "\n")

        self.status_text.insert("end", f"[{entry['time']}] ", "time")
        self.status_text.insert("end", f"{entry['title']}\n", entry.get("level", "title"))

        if entry.get("detail"):
            detail_lines = entry["detail"].split("\n")
            for line in detail_lines:
                self.status_text.insert("end", f"    {line}\n", "detail")

        self.status_text.configure(state="disabled")
        self.status_text.see("end")

    # Redesenha o log inteiro (usado ao reabrir a tela de ataques).
    def _render_status_log(self):
        if not hasattr(self, "status_text"):
            return

        self.status_text.configure(state="normal")
        self.status_text.delete("1.0", "end")
        self.status_text.configure(state="disabled")

        for entry in getattr(self, "status_log_entries", []):
            self._append_status_entry(entry)

    # Limpa o historico do log de status.
    def clear_status_log(self):
        self.status_log_entries = []

        if hasattr(self, "status_text"):
            self.status_text.configure(state="normal")
            self.status_text.delete("1.0", "end")
            self.status_text.configure(state="disabled")

        self._log_status_event("Log limpo", "O historico de eventos foi apagado.", level="detail")

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

    # Desenha o slider ON/OFF da mitigacao.
    def update_mitigation_ui(self):
        if not hasattr(self, "mitigation_slider"):
            return

        on = self.mitigation_state == "on"
        self._draw_mitigation_slider()

        self.mitigation_label.configure(
            text=("ON" if on else "OFF"),
            fg=(SEL_FG if on else MUTED),
        )

    # Alterna o estado da mitigacao pela interface.
    def toggle_mitigation_ui(self):
        self.mitigation_state = "off" if self.mitigation_state == "on" else "on"
        ok = self.send_mitigation_state() == 0

        if ok:
            if self.mitigation_state == "on":
                self._log_status_event(
                    "Mitigacao ATIVADA", self._mitigation_general_description(), level="ok"
                )
            else:
                self._log_status_event(
                    "Mitigacao DESATIVADA",
                    "protecao contra ataques ao GPS desativada.",
                    level="warn",
                )

            active_key = getattr(self, "active_attack_key", None)
            if active_key and getattr(self, "jamming_active", False):
                label = self.attack_label(active_key)

                if self.mitigation_state == "on":
                    defense = self._mitigation_defense_description(active_key)
                    self._log_status_event(
                        f"Mitigacao em andamento: {label}",
                        f"Para este ataque, {defense}",
                        level="warn",
                    )
                else:
                    self._log_status_event(
                        f"Sem mitigacao: {label}",
                        self._attack_no_mitigation_description(active_key),
                        level="danger",
                    )

            msg = f"Mitigacao {self.mitigation_state.upper()}."
        else:
            self.mitigation_state = "off" if self.mitigation_state == "on" else "on"
            msg = "Falha ao alterar mitigacao. Veja o log."
            self._log_status_event("Falha ao alterar mitigacao", msg, level="danger")

        self.update_mitigation_ui()
        notify_msg(msg)

    # Valida a intensidade informada para o ataque.
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

    # Aplica o ataque escolhido apos decolagem e destino.
    def apply_selected_attack_from_ui(self):
        if not self.attack_unlocked():
            msg = self.prereq_status_message()
            self._set_attack_status_ui(msg, level="warn")
            notify_msg(msg)
            self.update_attack_lock_ui()
            return

        active_key = getattr(self, "active_attack_key", None)

        if active_key and self.attack_type != active_key:
            msg = self.active_attack_locked_message(self.attack_type)
            self._set_attack_status_ui(msg, level="warn")
            notify_msg("Novo ataque bloqueado. Reinicie a simulacao para trocar o tipo de jamming.")
            self.update_attack_lock_ui()
            return

        try:
            intensity = self._parse_intensity_value(self.attack_type)
        except ValueError as e:
            self._set_attack_status_ui(str(e), level="danger")
            notify_msg(str(e))
            return

        self.execute_attack(self.attack_type, intensity)

    # Desativa o jamming mantendo a simulacao aberta.
    def stop_jamming_from_ui(self):
        if self.stop_jamming_attack():
            self.jamming_active = False
            active_key = getattr(self, "active_attack_key", None)

            if active_key:
                self._log_status_event(
                    "Jamming desligado",
                    f"O ataque {self.attack_label(active_key)} parou de interferir no GPS. O sinal "
                    f"volta ao normal e o drone deve retomar a navegacao normal em direcao ao destino "
                    f"(mitigacao={self.mitigation_state.upper()}). Para trocar o tipo de jamming, "
                    "reinicie a simulacao.",
                    level="ok",
                )
                msg = f"Jamming desligado | ataque da simulacao={self.attack_label(active_key)} | mitigacao={self.mitigation_state.upper()}"
            else:
                self._log_status_event(
                    "Jamming desligado",
                    f"Nenhum ataque estava ativo. Mitigacao atual={self.mitigation_state.upper()}.",
                    level="ok",
                )
                msg = f"Jamming desligado | mitigacao={self.mitigation_state.upper()}"
        else:
            msg = "Falha ao desligar jamming. Veja o log."
            self._log_status_event("Falha ao desligar jamming", msg, level="danger")

        notify_msg(msg)
        self.update_attack_lock_ui()

    # Executa comandos do spoofer/mitigator no ambiente Gazebo.
    def run_gazebo_shell(self, command):
        cmd = f"{load_gazebo_env_block()}\n{command}"
        log("Executando comando UAVJamSim")
        proc = subprocess.run(["bash", "-lc", cmd], text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)

        if proc.stdout:
            for line in proc.stdout.splitlines():
                log(line)

        log(f"Codigo de retorno: {proc.returncode}")
        return proc.returncode

    # Liga ou desliga a mitigacao no simulador.
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
            notify_msg(msg)
            self.update_attack_lock_ui()
            return

        active_key = getattr(self, "active_attack_key", None)

        if active_key and attack_key != active_key:
            msg = self.active_attack_locked_message(attack_key)
            self.update_attack_lock_ui()
            self._set_attack_warning_ui(msg, level="danger")
            notify_msg("Novo ataque bloqueado. Reinicie a simulacao para trocar o tipo de jamming.")
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
            intensity_txt = f" (intensidade={intensity})" if intensity is not None else ""
            action_word = "Aplicado" if was_new_attack else ("Atualizado" if intensity is not None else "Reativado")

            title = f"{action_word}: {label}{intensity_txt}"
            attack_detail = f"O ataque {label} {effect} {expected}"
            self._log_status_event(title, attack_detail, level="warn")

            if self.mitigation_state == "on":
                defense = self._mitigation_defense_description(attack_key)
                self._log_status_event(
                    f"Mitigacao em andamento: {label}",
                    f"Para este ataque, {defense}",
                    level="ok",
                )
            else:
                self._log_status_event(
                    f"Sem mitigacao: {label}",
                    self._attack_no_mitigation_description(attack_key),
                    level="danger",
                )

            if was_new_attack:
                self._log_status_event(
                    "Aviso",
                    "Para lancar outro tipo de jamming nesta simulacao, reinicie a simulacao. "
                    "A intensidade deste ataque ainda pode ser alterada, e a mitigacao pode ser "
                    "ligada/desligada a qualquer momento durante a execucao.",
                    level="detail",
                )

            msg = title
        else:
            msg = f"Falha ao aplicar {label}. Veja o log."
            self._log_status_event("Falha ao aplicar ataque", msg, level="danger")

        notify_msg(msg)
        self.update_attack_selection_ui()

    def run(self):
        log("Iniciando UAVEDUSIM GUI persistente")
        log(f"APP_DIR={APP_DIR}")
        log(f"Sessao: XDG_SESSION_TYPE={os.environ.get('XDG_SESSION_TYPE','vazio')}, WAYLAND_DISPLAY={os.environ.get('WAYLAND_DISPLAY','vazio')}, DISPLAY={os.environ.get('DISPLAY','vazio')}")
        self.root.mainloop()


# Ajustes responsivos da interface.

def _uavedusim_responsive_setup_dimensions(self):
    screen_w = max(self.root.winfo_screenwidth(), 800)
    screen_h = max(self.root.winfo_screenheight(), 600)

    base_scale = min(screen_w / 1920.0, screen_h / 1080.0)
    default_scale = self._clamp(base_scale, 0.82, 1.30)
    self.UI_SCALE = self._env_float("UAVEDUSIM_UI_SCALE", default_scale)
    self.UI_SCALE = self._clamp(self.UI_SCALE, 0.72, 1.55)

    self.px = lambda value: max(1, int(round(value * self.UI_SCALE)))
    self.fs = lambda value: max(7, int(round(value * self.UI_SCALE)))

    width_frac_default = 0.78 if screen_w >= 1800 else (0.86 if screen_w >= 1280 else 0.94)
    height_frac_default = 0.78 if screen_h >= 1000 else (0.86 if screen_h >= 760 else 0.92)

    width_frac = self._env_float("UAVEDUSIM_UI_WIDTH_FRAC", width_frac_default)
    height_frac = self._env_float("UAVEDUSIM_UI_HEIGHT_FRAC", height_frac_default)

    max_w_limit = int(self._env_float("UAVEDUSIM_UI_MAX_W", 1800))
    max_h_limit = int(self._env_float("UAVEDUSIM_UI_MAX_H", 1200))

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

    self.style.configure(
        "WinBtn.TButton",
        font=(self.FONT_FAMILY, self.FS_WIN, "bold"),
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
        font=(self.FONT_FAMILY, self.FS_WIN, "bold"),
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


def _uavedusim_repack_children(self, frame, *, vertical=False, fill_x=False):
    children = list(frame.winfo_children())

    for child in children:
        try:
            child.pack_forget()
        except Exception:
            pass

    for idx, child in enumerate(children):
        last = idx == len(children) - 1

        if vertical:
            child.pack(
                side="top",
                fill=("x" if fill_x else "none"),
                pady=(0, self.px(6) if not last else 0),
            )
        else:
            child.pack(
                side="left",
                padx=(0, self.px(8) if not last else 0),
            )


def _uavedusim_schedule_global_reflow(self, event=None):
    if event is not None and event.widget is not self.root:
        return

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
        self.logo_lbl.configure(text="⬡ UAV")

        if hasattr(self, "badge"):
            self.badge.pack_forget()
    else:
        self.logo_lbl.configure(text="⬡ UAVEDUSIM")

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
    self.set_badge("MAIN MENU" if is_main else (badge or prompt.upper()))

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
        ttk.Button(header, text="← voltar", style="Back.TButton", command=on_back).pack(
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

    scrollbar = ttk.Scrollbar(list_frame, orient="vertical", style="Dark.Vertical.TScrollbar")
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
        text=f"{len(options)} {'opção' if len(options) == 1 else 'opções'}",
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

    ttk.Button(
        self.menu_btn_frame,
        text="Fechar",
        style="Cancel.TButton",
        command=(self.close_app if is_main else (on_back or self.show_main_menu)),
    ).pack(side="left", padx=(0, self.px(8)))

    ttk.Button(
        self.menu_btn_frame,
        text="Selecionar →",
        style="Primary.TButton",
        command=self.choose_current,
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


# Versao responsiva da tela de espera.
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
        text="Aguarde enquanto PX4 e Gazebo iniciam. O menu de ataques abre automaticamente quando a simulacao estiver pronta para decolar.",
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
        text="Inicializando simulacao",
        bg=SURFACE2,
        fg=TAG_FG,
        font=(self.FONT_FAMILY, self.FS_SMALL, "bold"),
    ).grid(row=0, column=0, sticky="w")

    self.progress = ttk.Progressbar(center, mode="indeterminate", style="UAVEDUSIM.Horizontal.TProgressbar")
    self.progress.grid(row=1, column=0, sticky="ew", pady=(self.px(12), self.px(10)))
    self.progress.start(12)

    self.status_lbl = tk.Label(
        center,
        text="Preparando simulacao...\nPX4: iniciando\nGazebo: abrindo",
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
        text="O menu de ataques sera aberto automaticamente quando PX4 e Gazebo estiverem prontos.",
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

    ttk.Button(self.wait_btn_frame, text="Cancelar", style="Cancel.TButton", command=self.show_main_menu).pack(side="left", padx=(0, self.px(8)))
    ttk.Button(self.wait_btn_frame, text="Abrir ataques", style="Primary.TButton", command=self.show_attack_menu).pack(side="left")

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

    self.attack_left.grid_forget()
    self.attack_right.grid_forget()

    self.attack_body.grid_columnconfigure(0, weight=1, minsize=0)
    self.attack_body.grid_columnconfigure(1, weight=0, minsize=0)
    self.attack_body.grid_rowconfigure(0, weight=1)
    self.attack_body.grid_rowconfigure(1, weight=0)

    if stack:
        self.attack_left.grid(row=0, column=0, sticky="nsew")
        self.attack_right.grid(row=1, column=0, sticky="ew", pady=(gap, 0))
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
        self.attack_left.grid(row=0, column=0, sticky="nsew", padx=(0, gap))
        self.attack_right.grid(row=0, column=1, sticky="nsew")
        self.attack_left.configure(width=left_w)
        self.attack_right.configure(width=right_w)
        self.attack_right.grid_propagate(False)
        left_wrap = max(self.px(240), left_w - self.px(56))
        right_wrap = max(self.px(220), right_w - self.px(56))

    if hasattr(self, "attack_desc_label"):
        self.attack_desc_label.configure(wraplength=left_wrap)

    if hasattr(self, "attack_warning_label"):
        self.attack_warning_label.configure(wraplength=left_wrap)

    if hasattr(self, "mitigation_desc_label"):
        self.mitigation_desc_label.configure(wraplength=right_wrap)

    self._wrap_labels_deep(self.attack_body, max(self.px(220), width - self.px(40)))

    if hasattr(self, "attack_scroll_canvas"):
        self.root.after(40, lambda: self.attack_scroll_canvas.configure(scrollregion=self.attack_scroll_canvas.bbox("all")))



# Versao responsiva das linhas de ataque.
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

    # Icone de informacao (canto superior direito do card do ataque).
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
        self.yview_scroll(-3, "units")
    elif getattr(event, "num", None) == 5:
        self.yview_scroll(3, "units")
    elif getattr(event, "delta", 0):
        step = -1 if event.delta > 0 else 1
        self.yview_scroll(step * 3, "units")

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
UAVEDUSIMApp._repack_children = _uavedusim_repack_children
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

def _uavedusim_pack_button_column_v2(self, frame, *, vertical=True, fill_x=True):
    children = list(frame.winfo_children())

    for child in children:
        try:
            child.pack_forget()
        except Exception:
            pass

    visible_children = []

    for child in children:
        if child is getattr(self, "destination_timer_label", None):
            try:
                timer_text = str(getattr(self, "destination_timer_var").get()).strip()
            except Exception:
                timer_text = ""

            if not timer_text:
                continue

        visible_children.append(child)

    for idx, child in enumerate(visible_children):
        last = idx == len(visible_children) - 1

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


def _uavedusim_config_prereq_button_texts_v2(self, *, compact=False, very_compact=False):
    takeoff_done = bool(getattr(self, "takeoff_sent", False))
    destination_done = bool(getattr(self, "destination_sent", False))

    takeoff_text = "Takeoff enviado" if takeoff_done else "Enviar commander takeoff"
    destination_text = "Reenviar destino" if destination_done else "Enviar destino"

    # Mantem o nome completo dos botoes em telas estreitas.
    # Evita abreviacoes nos botoes.
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

        try:
            btn.configure(
                text=text,
                justify="center",
                wraplength=max(self.px(120), self.px(190 if not compact else 140)),
                padx=self.px(12),
                pady=self.px(8 if "\n" in text else 7),
            )
        except Exception:
            pass


def _uavedusim_layout_destination_fields_v2(self, *, stack=False):
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

    # Campos compactos para distancia, rotacao e altitude.
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


def _uavedusim_reflow_prereq_layout_v2(self, event=None):
    if self.page != "attack" or not hasattr(self, "prereq_panel"):
        return

    width = self.prereq_panel.winfo_width() or self.content.winfo_width() or self.root.winfo_width()

    compact_panel = width < self.px(760)
    very_compact = width < self.px(520)

    widgets = [
        getattr(self, "takeoff_box", None),
        getattr(self, "destination_box", None),
        getattr(self, "prereq_buttons", None),
    ]

    for widget in widgets:
        if widget is not None:
            try:
                widget.grid_forget()
            except Exception:
                pass

    for col in range(3):
        try:
            self.prereq_panel.grid_columnconfigure(col, weight=0, minsize=0)
        except Exception:
            pass

    if compact_panel:
        self.prereq_panel.grid_columnconfigure(0, weight=1)

        self.takeoff_box.grid(
            row=0,
            column=0,
            sticky="ew",
            padx=self.px(12),
            pady=(self.px(8), self.px(4)),
        )

        self.destination_box.grid(
            row=1,
            column=0,
            sticky="ew",
            padx=self.px(12),
            pady=(self.px(4), self.px(4)),
        )

        self.prereq_buttons.grid(
            row=2,
            column=0,
            sticky="ew",
            padx=self.px(12),
            pady=(self.px(4), self.px(8)),
        )

        timer_visible = bool(getattr(self, "destination_release_pending", False))
        self._pack_button_column_v2(self.prereq_buttons, vertical=(very_compact or timer_visible), fill_x=True)

    else:
        self.prereq_panel.grid_columnconfigure(0, weight=1, minsize=self.px(210))
        self.prereq_panel.grid_columnconfigure(1, weight=3, minsize=self.px(430))
        self.prereq_panel.grid_columnconfigure(2, weight=0, minsize=self.px(250))

        self.takeoff_box.grid(
            row=0,
            column=0,
            sticky="nsew",
            padx=(self.px(12), self.px(8)),
            pady=self.px(8),
        )

        self.destination_box.grid(
            row=0,
            column=1,
            sticky="nsew",
            padx=(self.px(8), self.px(8)),
            pady=self.px(8),
        )

        self.prereq_buttons.grid(
            row=0,
            column=2,
            sticky="e",
            padx=(self.px(8), self.px(12)),
            pady=self.px(8),
        )

        self._pack_button_column_v2(self.prereq_buttons, vertical=True, fill_x=True)

    self._config_prereq_button_texts_v2(compact=compact_panel, very_compact=very_compact)

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
        destination_width = width if compact_panel else max(self.px(430), int(width * 0.42))

    # Usa layout horizontal quando houver largura suficiente.
    # Empilha os campos somente em telas estreitas.
    stack_destination_fields = very_compact or destination_width < self.px(330)
    self._layout_destination_fields_v2(stack=stack_destination_fields)

    if hasattr(self, "attack_scroll_canvas"):
        self.root.after(
            40,
            lambda: self.attack_scroll_canvas.configure(
                scrollregion=self.attack_scroll_canvas.bbox("all")
            ),
        )


def _uavedusim_reflow_attack_footer_v2(self, event=None):
    if self.page != "attack" or not hasattr(self, "attack_btn_frame"):
        return

    width = getattr(event, "width", None) or self.content.winfo_width() or self.root.winfo_width()

    compact = width < self.px(980)
    vertical_buttons = width < self.px(560)

    # Quebra linhas dos botoes sem abreviar os textos.
    if hasattr(self, "restart_button"):
        self.restart_button.configure(
            text=("Reiniciar\nsimulação" if vertical_buttons else "Reiniciar simulação"),
            justify="center",
            wraplength=max(self.px(130), self.px(190)),
            padx=self.px(12),
            pady=self.px(8 if vertical_buttons else 7),
        )

    if hasattr(self, "stop_button"):
        self.stop_button.configure(
            text=("Desligar\njamming" if vertical_buttons else "Desligar jamming"),
            justify="center",
            wraplength=max(self.px(120), self.px(180)),
            padx=self.px(12),
            pady=self.px(8 if vertical_buttons else 7),
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

    self._pack_button_column_v2(
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


UAVEDUSIMApp._pack_button_column_v2 = _uavedusim_pack_button_column_v2
UAVEDUSIMApp._config_prereq_button_texts_v2 = _uavedusim_config_prereq_button_texts_v2
UAVEDUSIMApp._layout_destination_fields_v2 = _uavedusim_layout_destination_fields_v2
UAVEDUSIMApp._reflow_prereq_layout = _uavedusim_reflow_prereq_layout_v2
UAVEDUSIMApp._reflow_attack_footer = _uavedusim_reflow_attack_footer_v2


# Descricoes didaticas dos modelos Gazebo.

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
    "gz_x500_uavjamsim": "Versao do X500 com varios sensores ativos ao mesmo tempo para experimentos do UAVEDUSIM.",
    "gz_x500": "X500 Quadrotor.",
    "gz_x500_vision": "X500 Quadrotor with Visual Odometry.",
    "gz_x500_depth": "X500 Quadrotor with Depth Camera (Front-facing). Camera de profundidade frontal baseada na OAK-D.",
    "gz_x500_mono_cam": "X500 Quadrotor with Monocular Camera. Sensor monocular simples acoplado ao modelo.",
    "gz_x500_mono_cam_down": "X500 Quadrotor with Monocular Camera (Down-facing). Util para testes de pouso preciso no mundo Aruco.",
    "gz_x500_lidar_down": "X500 Quadrotor with 1D LIDAR (Down-facing). LIDAR inferior Lightware LW20/C, alcance de 0.1 a 100 m.",
    "gz_x500_lidar_front": "X500 Quadrotor with 1D LIDAR (Front-facing). LIDAR frontal Lightware LW20/C, alcance de 0.2 a 100 m.",
    "gz_x500_lidar_2d": "X500 Quadrotor with 2D LIDAR. Sensor Hokuyo UTM-30LX, alcance de 0.1 a 30 m e varredura de 270 graus.",
    "gz_x500_gimbal": "X500 Quadrotor with Gimbal (Front-facing). Gimbal frontal com controle de posicao e cadeia cinematica ZXY.",
    "gz_rc_cessna": "Standard Plane. Plane in Gazebo.",
    "gz_advanced_plane": "Advanced Plane. Modelo de asa fixa com Advanced Lift Drag plugin.",
    "gz_standard_vtol": "Standard VTOL.",
    "gz_quadtailsitter": "Quad Tailsitter VTOL. Tailsitter com empuxo diferencial para pitch, roll e yaw.",
    "gz_tiltrotor": "Tiltrotor VTOL. VTOL Plane com motores frontais inclinaveis para transicao e voo avancado.",
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
        return PX4_MODEL_DESCRIPTIONS[model_id]

    raw = model_id[3:] if model_id.startswith("gz_") else model_id
    pretty = raw.replace("_", " ")
    return f"{pretty} in Gazebo."


def _uavedusim_model_display_label(model_id):
    description = _uavedusim_model_description(model_id)
    return f"{model_id}\n{description}"


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


# Lista modelos com descricao didatica e remove rovers por padrao.
def _uavedusim_get_models_with_descriptions(self):
    models_dir = PX4_DIR / "Tools/simulation/gz/models"

    if not models_dir.is_dir():
        raise RuntimeError(f"Nao encontrei a pasta de modelos do Gazebo: {models_dir}")

    discovered = []

    for item in sorted(models_dir.iterdir(), key=lambda p: p.name):
        if item.name.startswith("."):
            continue

        if not (item.is_dir() or item.is_symlink()):
            continue

        model_id = f"gz_{item.name}"

        if _uavedusim_is_rover_model(model_id):
            log(f"Ignorando modelo rover na lista de veiculos: {item.name}")
            continue

        if not _uavedusim_model_exists_and_is_executable(model_id):
            continue

        discovered.append(model_id)

    ordered = []

    for model_id in PX4_MODEL_DOC_ORDER:
        if model_id in discovered and model_id not in ordered:
            ordered.append(model_id)

    extras = sorted(model_id for model_id in discovered if model_id not in ordered)
    ordered.extend(extras)

    if not ordered:
        raise RuntimeError(f"Nenhum modelo executavel foi encontrado em: {models_dir}")

    return [_uavedusim_model_display_label(model_id) for model_id in ordered]


# Extrai o token real do modelo selecionado.
def _uavedusim_handle_model_choice_with_description(self, choice):
    self.model = _uavedusim_model_id_from_choice(choice)

    if not self.model:
        self.show_error("Modelo invalido selecionado.")
        return

    try:
        worlds = self.get_worlds()
    except Exception as e:
        self.show_error(str(e))
        return

    self.render_menu("Escolha o mundo de simulação onde o drone vai voar", worlds, self.handle_world_choice, badge="ESCOLHER MUNDO", on_back=self.start_uavedusim_flow)


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


# Desenha itens com titulo e descricao na lista.
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
UAVEDUSIMApp.handle_model_choice = _uavedusim_handle_model_choice_with_description

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
    notify_msg("Cancelando carregamento e encerrando PX4/Gazebo/QGroundControl.")

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

    if hasattr(self, "wait_btn_frame"):
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


# Descricoes didaticas dos mundos Gazebo.

PX4_WORLD_DOC_ORDER = [
    "default",
    "aruco",
    "baylands",
    "bayland",
    "lawn",
    "ridge",
    "walls",
    "windy",
    "moving_platform",
]

PX4_WORLD_DESCRIPTIONS = {
    "default": "Empty (Default). Mundo vazio com plano cinza; usado por padrao.",
    "aruco": "Aruco. Mundo default com marcador ArUco; usado com x500_mono_cam_down para testar pouso preciso.",
    "baylands": "Baylands. Mundo cercado por agua.",
    "bayland": "Baylands. Mundo cercado por agua.",
    "lawn": "Lawn. Mundo plano e verde; alternativa menos otimizada ao rover world.",
    "ridge": "Ridge. Mundo com terreno inclinado para testar terrain following, especialmente com x500_lidar_down.",
    "walls": "Walls. Mundo com paredes para testes de collision prevention.",
    "windy": "Windy. Mundo vazio com vento habilitado.",
    "moving_platform": "Moving Platform. Mundo com plataforma plana em movimento para simular operacoes a partir de navios ou caminhoes.",
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


def _uavedusim_world_id_from_choice(choice):
    text = str(choice or "").strip()

    if not text:
        return ""

    first_line = text.splitlines()[0].strip()
    return first_line.split()[0] if first_line else ""


def _uavedusim_world_description(world_id):
    if world_id in PX4_WORLD_DESCRIPTIONS:
        return PX4_WORLD_DESCRIPTIONS[world_id]

    pretty = str(world_id or "").replace("_", " ").strip()

    if not pretty:
        return "Mundo Gazebo disponivel no PX4."

    return f"{pretty}. Mundo Gazebo disponivel no PX4."


def _uavedusim_world_display_label(world_id):
    title = "default (mundo indicado)" if world_id == "default" else world_id
    description = _uavedusim_world_description(world_id)
    return f"{title}\n{description}"


# Lista mundos com descricao didatica.
def _uavedusim_get_worlds_with_descriptions(self):
    worlds_dir = PX4_DIR / "Tools/simulation/gz/worlds"

    if not worlds_dir.is_dir():
        raise RuntimeError(f"Nao encontrei a pasta de mundos do Gazebo: {worlds_dir}")

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

    extras = sorted(world_id for world_id in discovered if world_id not in ordered)
    ordered.extend(extras)

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
UAVEDUSIMApp.handle_world_choice = _uavedusim_handle_world_choice_with_description


# Telas educativas da pagina inicial.

UAVEDUSIM_JAMMING_ATTACKS_TEXT = """Neste simulador, foram implementados três tipos de ataques de jamming contra o sistema de navegação do VANT: Jamming Noise, Jamming Blackout e Jamming Pulse. Todos eles têm como objetivo degradar a navegação baseada em GPS/GNSS, mas cada um faz isso de uma maneira diferente.

Em um drone real, o GPS/GNSS é usado para estimar a posição, acompanhar a trajetória, manter a missão autônoma e apoiar decisões do piloto automático. Quando esse sinal é degradado, bloqueado ou se torna instável, o VANT pode receber informações incorretas de localização, perder atualizações de posicionamento ou alternar entre momentos de funcionamento normal e falha. Como consequência, o drone pode se desviar da rota planejada, corrigir sua trajetória de forma brusca, interromper a missão ou acionar mecanismos de segurança.

No UAVEDUSIM, esses ataques são reproduzidos em ambiente simulado, sem transmissão real de interferência eletromagnética. Isso permite estudar o comportamento do VANT de forma segura, repetível e controlada, observando como diferentes padrões de falha afetam a navegação, o estimador de estado e a continuidade da missão.

Jamming Noise

O Jamming Noise representa um cenário em que o GPS continua aparentemente funcionando, mas as medições recebidas pelo VANT passam a ser degradadas por ruído. Isso significa que o sistema ainda recebe dados de posição, velocidade ou altitude, porém esses dados deixam de representar corretamente o movimento real do drone.

Esse ataque é perigoso porque o GPS não desaparece completamente. Para o piloto automático, o sensor pode parecer disponível, mas as informações que chegam ao estimador estão corrompidas. Na prática, o VANT pode acreditar que está em uma posição diferente da posição real, interpretar deslocamentos falsos ou tentar corrigir uma trajetória com base em dados inconsistentes.

No simulador, esse ataque é aplicado adicionando perturbações às medições de GPS/GNSS. Quanto maior a intensidade configurada, maior pode ser a diferença entre a posição estimada e a posição real do drone. O efeito esperado é uma navegação instável, com desvios de rota, dificuldade para alcançar o destino e possíveis correções bruscas durante o voo.

Jamming Blackout

O Jamming Blackout representa um cenário mais severo: o sinal de GPS/GNSS deixa de chegar ao VANT. Nesse caso, não se trata apenas de medições ruins, mas da ausência completa de novas atualizações de posicionamento.

Esse ataque simula uma situação em que o receptor GPS fica indisponível durante o voo. Sem novas informações de posição, o piloto automático perde uma das principais referências usadas para estimar onde o drone está. Dependendo da configuração do sistema e do estágio da missão, isso pode levar à interrupção da navegação autônoma ou ao acionamento de mecanismos de segurança.

No simulador, o blackout é reproduzido interrompendo a publicação das medições GPS/GNSS. Assim, o PX4 deixa de receber atualizações recentes do sensor e precisa lidar com a perda da fonte de localização. O efeito observado pode incluir perda de continuidade na missão, falhas na estimativa de posição e acionamento de procedimentos de failsafe.

Jamming Pulse

O Jamming Pulse representa um ataque intermitente. Diferente do Jamming Noise, em que o sinal permanece degradado, e do Jamming Blackout, em que o sinal é bloqueado, o ataque pulsado alterna entre diferentes fases: degradação, bloqueio, recuperação parcial e funcionamento válido.

Esse tipo de ataque é especialmente desafiador porque o comportamento do GPS muda ao longo do tempo. Em alguns momentos, o VANT recebe medições corrompidas; em outros, não recebe nenhuma atualização; depois, o sinal pode retornar parcialmente ou voltar a parecer válido. Essa alternância força o sistema de navegação a lidar com uma sequência de condições instáveis.

No simulador, o Jamming Pulse é implementado como um ciclo de interferência. Durante uma fase, o ruído aumenta e as medições se tornam menos confiáveis. Em seguida, pode ocorrer bloqueio temporário do GPS. Depois, o sinal passa por uma etapa de recuperação, até voltar ao funcionamento válido por um período. Esse processo se repete, criando um cenário de falha intermitente.

O efeito esperado é uma navegação com oscilações: o drone pode tentar corrigir a trajetória quando o GPS retorna, perder referência durante os intervalos de bloqueio e reagir de forma diferente dependendo do momento da missão em que cada pulso ocorre. Por isso, mesmo usando os mesmos parâmetros de ataque, o comportamento pode variar entre execuções.

Resumo dos efeitos

Os três ataques afetam o VANT de formas diferentes. O Jamming Noise compromete a qualidade das medições, fazendo com que o drone receba dados incorretos. O Jamming Blackout bloqueia a disponibilidade do GPS, deixando o sistema sem novas atualizações de posição. O Jamming Pulse combina os dois problemas, alternando períodos de dados corrompidos, ausência de sinal e recuperação.

Com esses três cenários, o UAVEDUSIM permite observar como diferentes formas de interferência GPS/GNSS impactam a navegação autônoma. Assim, o usuário pode comparar o comportamento do VANT em situações de degradação contínua, perda total de sinal e falhas intermitentes, avaliando também a importância das estratégias de mitigação.""".strip()

UAVEDUSIM_JAMMING_SUBTITLES = {"Jamming Noise", "Jamming Blackout", "Jamming Pulse", "Resumo dos efeitos"}

UAVEDUSIM_MITIGATION_TEXT = """No UAVEDUSIM, as mitigações foram implementadas para reduzir os efeitos dos ataques de jamming GPS/GNSS sobre a navegação do VANT. O objetivo não é impedir que o ataque aconteça, mas evitar que medições ruins, ausentes ou instáveis sejam usadas diretamente pelo sistema de navegação sem nenhum tratamento.

Durante o voo, o piloto automático depende das informações de sensores para estimar a posição, a velocidade e o movimento do drone. Quando o GPS/GNSS é afetado por jamming, essas informações podem ficar incorretas, deixar de chegar ou alternar entre períodos válidos e inválidos. Por isso, a mitigação atua no fluxo de dados antes que essas medições sejam consumidas pelo estimador e pelo controle de voo.

Cada tipo de ataque exige uma resposta diferente. No Jamming Noise, o foco é filtrar medições corrompidas. No Jamming Blackout, o foco é manter temporariamente a navegação mesmo sem novas atualizações de GPS. No Jamming Pulse, o sistema precisa lidar com alternância entre ruído, perda de sinal e recuperação parcial.

Mitigação do Jamming Noise

No Jamming Noise, o GPS continua aparentemente disponível, mas as medições chegam degradadas por ruído. Isso afeta não apenas a posição horizontal, mas também a altitude e as velocidades estimadas pelo GPS.

A mitigação desse ataque utiliza filtro de Kalman combinado com fontes auxiliares de altitude. O filtro compara a medição GPS recebida com uma previsão do estado esperado do VANT. Quando a medição está muito distante do comportamento previsto, ela é tratada como suspeita e sua influência é reduzida.

Para a altitude, o sistema não depende apenas da altitude GPS ruidosa. Quando disponível, ele prioriza a altitude estimada pela posição local do PX4 e pode usar a altitude barométrica como referência auxiliar. A odometria visual também pode auxiliar quando está recente, fornecendo uma estimativa alternativa de deslocamento e altitude durante a rejeição de medições GPS inconsistentes.

Dessa forma, a mitigação do Noise Jamming não filtra apenas latitude e longitude. Ela também tenta estabilizar o canal vertical, usando o filtro de Kalman junto com referências auxiliares como posição local, barômetro e odometria visual. Isso reduz oscilações artificiais de altitude e evita que o VANT reaja diretamente a variações verticais falsas causadas pelo jamming.

Mitigação do Jamming Blackout

No Jamming Blackout, o problema é diferente: o GPS deixa de fornecer novas atualizações. Nesse caso, o VANT não recebe apenas medições ruins; ele fica sem novas informações de posicionamento global.

A mitigação começa detectando que o GPS parou de atualizar. Para isso, o sistema observa o tempo desde a última medição válida. Quando esse tempo ultrapassa um limite, a situação é tratada como perda de GPS. A partir desse momento, o UAVEDUSIM cria uma referência temporária baseada na última posição confiável.

Essa referência funciona como uma âncora. A partir dela, o sistema tenta estimar por um curto período onde o VANT deve estar, mesmo sem novas medições GPS. Para isso, são usadas pseudo-medições e dead reckoning.

As pseudo-medições são estimativas temporárias geradas pelo próprio sistema. Elas não representam um GPS real, mas fornecem ao estimador uma referência controlada para evitar uma perda imediata de continuidade na navegação. Essas medições podem ser baseadas na última posição válida, na estimativa filtrada e no movimento previsto do drone.

O dead reckoning é usado para propagar a posição a partir do estado anterior. Ou seja, se o sistema sabe onde o VANT estava e tem uma estimativa de como ele se moveu, ele calcula aproximadamente onde o drone deve estar após um curto intervalo de tempo. Essa técnica ajuda durante perdas temporárias de GPS, mas o erro cresce com o tempo. Por isso, ela não deve sustentar a navegação indefinidamente.

Quando disponível, a odometria visual também auxilia essa mitigação. A odometria visual estima deslocamentos do VANT a partir de informações visuais, funcionando como uma fonte auxiliar de movimento. Com isso, durante um blackout, o sistema não depende apenas da última coordenada GPS válida; ele também pode usar indícios do deslocamento observado para melhorar a estimativa temporária.

Mitigação do Jamming Pulse

No Jamming Pulse, o GPS alterna entre fases de degradação, bloqueio e recuperação. Em alguns momentos, o VANT recebe medições com ruído; em outros, deixa de receber atualizações; depois, o sinal pode voltar parcialmente ou retornar a um estado aparentemente válido.

Por isso, a mitigação do Jamming Pulse combina as estratégias usadas no Jamming Noise e no Jamming Blackout. Quando o sinal está degradado, o sistema usa filtro de Kalman para suavizar as medições e reduzir o impacto de leituras inconsistentes. Quando o sinal desaparece, o sistema usa âncora de posição, pseudo-medições, dead reckoning e, quando disponível, odometria visual.

O principal desafio desse ataque é a transição entre os estados. Quando o GPS volta depois de um período de bloqueio, o sistema não deve confiar imediatamente em qualquer nova medição. O sinal pode retornar ainda instável ou com erro. Por isso, a mitigação tenta evitar mudanças bruscas entre três situações: GPS ruidoso, GPS ausente e GPS aparentemente recuperado.

Essa abordagem ajuda o VANT a manter um comportamento mais estável durante falhas intermitentes. Em vez de alternar entre correções fortes, perda de referência e retomadas repentinas, o sistema tenta preservar uma estimativa mais contínua e coerente.

Resposta de segurança

Mesmo com mitigação, existe um limite para manter a navegação sob interferência. O filtro de Kalman reduz o impacto de medições ruins, o dead reckoning ajuda em perdas temporárias e a odometria visual pode fornecer uma referência auxiliar, mas nenhuma dessas estratégias deve manter o VANT voando indefinidamente se o GPS continuar indisponível ou muito degradado.

Por isso, quando a interferência persiste ou quando a navegação deixa de ser suficientemente confiável, o sistema prioriza a segurança da missão. No UAVEDUSIM, caso o VANT consiga chegar à região de destino mesmo sob jamming, a resposta segura é iniciar um pouso controlado. Após o pouso, os motores são desligados, encerrando o voo de forma segura.

Essa lógica evita que o drone continue tentando navegar com informações pouco confiáveis depois de atingir o objetivo da missão. Assim, a mitigação não busca apenas manter o voo ativo, mas também garantir que, ao chegar ao destino ou ao enfrentar interferência persistente, o VANT finalize a operação de maneira controlada.

Resumo das mitigações

No Jamming Noise, a mitigação usa principalmente filtro de Kalman para suavizar e validar medições GPS degradadas. No Jamming Blackout, a mitigação usa detecção de perda de GPS, âncora de posição, pseudo-medições, dead reckoning e odometria visual para manter temporariamente a navegação. No Jamming Pulse, as duas estratégias são combinadas, pois o ataque alterna entre ruído, ausência de sinal e recuperação parcial.

Com essas mitigações, o UAVEDUSIM permite comparar o comportamento do VANT com e sem defesa, mostrando como técnicas de filtragem, estimativa temporária e fontes auxiliares podem reduzir desvios de trajetória e preservar a continuidade da navegação por mais tempo.""".strip()

UAVEDUSIM_MITIGATION_SUBTITLES = {
    "Mitigação do Jamming Noise",
    "Mitigação do Jamming Blackout",
    "Mitigação do Jamming Pulse",
    "Resposta de segurança",
    "Resumo das mitigações",
}

UAVEDUSIM_HOME_TEXT = 'O UAVEDUSIM é um ambiente de simulação para estudar ataques de jamming em Veículos Aéreos Não Tripulados (VANTs) de forma segura, controlada e reprodutível. Ele utiliza PX4 SITL, Gazebo e QGroundControl para executar missões simuladas, observar o comportamento do drone e analisar como falhas ou ataques contra o sistema de navegação podem afetar o voo.\n\nEm um drone real, a navegação depende de diferentes fontes de informação, como GPS/GNSS, sensores inerciais, altitude, velocidade, telemetria e comandos de controle. Quando essas informações são degradadas, bloqueadas ou se tornam inconsistentes, o piloto automático pode ter dificuldade para estimar corretamente a posição do VANT. Isso pode causar desvios de trajetória, interrupção da missão, perda de continuidade na navegação ou acionamento de procedimentos de segurança.\n\nO UAVEDUSIM permite reproduzir esse tipo de situação dentro de um ambiente simulado, sem transmitir interferência eletromagnética real e sem colocar equipamentos, pessoas ou outros sistemas em risco. Assim, é possível repetir experimentos, comparar cenários, alterar parâmetros do ataque e avaliar mecanismos de mitigação de maneira mais segura do que em testes com rádio frequência no mundo real.\n\nO que é jamming?\n\nJamming é uma interferência intencional contra sinais utilizados por um sistema. No contexto de VANTs, esse ataque pode afetar sinais de GPS/GNSS, telemetria, comunicação de controle ou outros canais sem fio usados durante a missão. O objetivo do atacante é degradar, bloquear ou tornar instável a recepção desses sinais, prejudicando a capacidade do drone de navegar corretamente.\n\nNo caso do GPS/GNSS, o jamming pode fazer com que o VANT receba medições de posição com erro, fique sem atualizações de localização ou alterne entre períodos de funcionamento normal e falha. Por isso, mesmo sem invadir diretamente o software do drone, o atacante pode comprometer a missão ao explorar a dependência do VANT em relação aos sinais externos de navegação.\n\nNesta interface, você pode selecionar o modelo do VANT, escolher o mundo de simulação, iniciar o ambiente PX4/Gazebo e executar cenários de jamming GPS/GNSS. O UAVEDUSIM permite testar ataques como noise jamming, GPS blackout e pulsed jamming, além de avaliar estratégias de mitigação que buscam reduzir desvios de trajetória e preservar a continuidade da navegação.'


# Executa uma acao da tela inicial evitando duplo clique.
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


# Mantem os botoes da tela inicial organizados.
def _uavedusim_home_reflow_buttons(self, _event=None):
    frame = getattr(self, "home_buttons_frame", None)
    buttons = getattr(self, "home_action_buttons", [])

    if frame is None or not frame.winfo_exists() or not buttons:
        return

    width = frame.winfo_width() or self.content.winfo_width() or self.px(900)
    compact = width < self.px(620)

    for btn in buttons:
        try:
            btn.grid_forget()
        except Exception:
            pass

    for col in range(3):
        frame.grid_columnconfigure(col, weight=0, uniform="")

    if compact:
        frame.grid_columnconfigure(0, weight=1)

        for row, btn in enumerate(buttons):
            btn.grid(row=row, column=0, sticky="ew", padx=0, pady=(0, self.px(10)))
    else:
        for col in range(3):
            frame.grid_columnconfigure(col, weight=1, uniform="home_actions")

        for col, btn in enumerate(buttons):
            btn.grid(
                row=0,
                column=col,
                sticky="ew",
                padx=(0 if col == 0 else self.px(12), 0),
                pady=0,
            )


# Renderiza a pagina inicial educativa.
def _uavedusim_home_show_main_menu(self):
    self.page = "main"
    self.selected_callback = None
    self.back_callback = None
    self.clear_content()
    self.set_badge("MAIN MENU")

    options = [
        "Executar UAVEDUSIM",
        "Limpar build (se necessário)",
        "Limpar processos UAVEDUSIM",
    ]

    try:
        self.style.configure(
            "HomeAction.TButton",
            font=(self.FONT_FAMILY, self.FS_BUTTON, "bold"),
            padding=(self.px(18), self.px(9)),
            background="#74c7ec",
            foreground="#11111b",
            relief="flat",
            borderwidth=0,
        )
        self.style.map(
            "HomeAction.TButton",
            background=[("disabled", DISABLED_BG), ("active", "#89dceb"), ("pressed", "#89b4fa")],
            foreground=[("disabled", DISABLED_FG), ("active", "#11111b"), ("pressed", "#11111b")],
        )

        self.style.configure(
            "HomeAbout.TButton",
            font=(self.FONT_FAMILY, self.FS_BUTTON, "bold"),
            padding=(self.px(18), self.px(11)),
            background=ACCENT,
            foreground="#11111b",
            relief="flat",
            borderwidth=0,
        )
        self.style.map(
            "HomeAbout.TButton",
            background=[("disabled", DISABLED_BG), ("active", ACCENT_DIM), ("pressed", ACCENT_DIM)],
            foreground=[("disabled", DISABLED_FG), ("active", "#11111b"), ("pressed", "#11111b")],
        )
    except Exception:
        pass

    main_box = tk.Frame(
        self.content,
        bg=SURFACE2,
        highlightbackground=BORDER,
        highlightthickness=1,
    )
    main_box.pack(
        fill="both",
        expand=True,
        padx=self.CONTENT_PAD,
        pady=(self.HEADER_TOP_PAD, 0),
    )

    scroll_outer = tk.Frame(main_box, bg=SURFACE2)
    scroll_outer.pack(fill="both", expand=True)

    text_canvas = tk.Canvas(scroll_outer, bg=SURFACE2, highlightthickness=0, bd=0)
    text_scrollbar = tk.Scrollbar(
        scroll_outer,
        orient="vertical",
        command=text_canvas.yview,
        width=self.SCROLLBAR_W,
        bg=BORDER,
        troughcolor=SURFACE,
        activebackground=MUTED,
        bd=0,
        highlightthickness=0,
        relief="flat",
        elementborderwidth=0,
    )
    text_canvas.configure(yscrollcommand=text_scrollbar.set)

    text_canvas.pack(side="left", fill="both", expand=True)

    self.home_text_canvas = text_canvas

    text_area = tk.Frame(text_canvas, bg=SURFACE2)
    text_area_id = text_canvas.create_window((0, 0), window=text_area, anchor="nw")

    inner_pad = self.px(18)

    self._home_scroll_enabled = False

    def _content_overflows():
        try:
            content_h = text_area.winfo_reqheight()
            visible_h = text_canvas.winfo_height()
            return content_h > visible_h
        except Exception:
            return False

    def _sync_text_scrollregion(_event=None):
        text_canvas.configure(scrollregion=text_canvas.bbox("all"))

        overflows = _content_overflows()
        self._home_scroll_enabled = overflows

        if overflows:
            if not text_scrollbar.winfo_ismapped():
                text_scrollbar.pack(side="right", fill="y")
        else:
            if text_scrollbar.winfo_ismapped():
                text_scrollbar.pack_forget()
            text_canvas.yview_moveto(0)

    def _sync_text_width(event):
        text_canvas.itemconfigure(text_area_id, width=event.width)
        _sync_text_scrollregion()

    def _text_wheel(event):
        if not self._home_scroll_enabled:
            return

        if getattr(event, "num", None) == 4:
            text_canvas.yview_scroll(-3, "units")
        elif getattr(event, "num", None) == 5:
            text_canvas.yview_scroll(3, "units")
        elif getattr(event, "delta", 0):
            text_canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

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

    title = tk.Label(
        text_area_padded,
        text="UAVEDUSIM",
        bg=SURFACE2,
        fg=TEXT,
        justify="center",
        anchor="center",
        font=(self.FONT_FAMILY, self.fs(24), "bold"),
    )
    title.pack(fill="x", pady=(0, self.px(14)))

    blocks = UAVEDUSIM_HOME_TEXT.split("\n\n")
    self._home_text_labels = []

    for block in blocks:
        block = block.strip()

        if not block:
            continue

        is_subtitle = block == "O que é jamming?"

        lbl = tk.Label(
            text_area_padded,
            text=block,
            bg=SURFACE2,
            fg=TEXT if is_subtitle else MUTED,
            justify="left",
            anchor="nw",
            wraplength=max(self.px(500), self.win_w - self.px(140)),
            font=(
                self.FONT_FAMILY,
                self.fs(13 if is_subtitle else 10),
                "bold" if is_subtitle else "normal",
            ),
        )
        lbl.pack(anchor="w", fill="x", pady=(0, self.px(12 if is_subtitle else 10)))
        self._home_text_labels.append(lbl)

    def _uavedusim_home_about_jamming_click():
        _uavedusim_show_jamming_info_screen(self)

    self.home_about_button = ttk.Button(
        text_area_padded,
        text="Entenda os ataques de jamming simulados",
        style="HomeAbout.TButton",
        command=_uavedusim_home_about_jamming_click,
    )
    self.home_about_button.pack(fill="x", pady=(self.px(8), self.px(12)))

    self.home_buttons_frame = tk.Frame(text_area_padded, bg=SURFACE2)
    self.home_buttons_frame.pack(fill="x", pady=(0, self.px(8)))

    self.home_action_buttons = []

    action_items = [
        ("Iniciar simulação do UAVEDUSIM", "Executar UAVEDUSIM"),
        ("Apagar arquivos de build antigos", "Limpar build (se necessário)"),
        ("Encerrar processos do UAVEDUSIM em execução", "Limpar processos UAVEDUSIM"),
    ]

    for label, choice in action_items:
        btn = ttk.Button(
            self.home_buttons_frame,
            text=label,
            style="HomeAction.TButton",
            command=lambda choice=choice: _uavedusim_home_run_choice(self, choice),
        )
        self.home_action_buttons.append(btn)

    self.home_buttons_frame.bind(
        "<Configure>",
        lambda event: _uavedusim_home_reflow_buttons(self, event),
    )

    self.home_note_label = tk.Label(
        text_area_padded,
        text=(
            "Observação: em algumas situações, principalmente após fechar a simulação de forma inesperada "
            "ou alterar arquivos do PX4, pode ser necessário limpar a build ou encerrar manualmente processos "
            "antigos do PX4, Gazebo ou QGroundControl. Se a simulação não iniciar corretamente, use os botões "
            "de limpeza acima e tente executar o UAVEDUSIM novamente."
        ),
        bg=SURFACE2,
        fg=WARN,
        justify="left",
        anchor="nw",
        wraplength=max(self.px(500), self.win_w - self.px(140)),
        font=(self.FONT_FAMILY, self.FS_TINY),
    )
    self.home_note_label.pack(anchor="w", fill="x", pady=(self.px(4), self.px(8)))
    self._home_text_labels.append(self.home_note_label)

    def _reflow_home_text(_event=None):
        try:
            w = text_canvas.winfo_width()
            wrap = max(self.px(420), w - self.px(24))

            for lbl in getattr(self, "_home_text_labels", []):
                lbl.configure(wraplength=wrap)
        except Exception:
            pass

    text_canvas.bind("<Configure>", lambda event: (_sync_text_width(event), _reflow_home_text()))

    self.root.after_idle(_reflow_home_text)
    self.root.after_idle(_sync_text_scrollregion)
    self.root.after_idle(lambda: _uavedusim_home_reflow_buttons(self))

    footer = tk.Frame(self.content, bg=SURFACE)
    footer.pack(
        fill="x",
        padx=self.CONTENT_PAD,
        pady=(self.FOOTER_TOP_PAD, self.FOOTER_BOTTOM_PAD),
    )

    tk.Label(
        footer,
        text=f"{len(options)} opções",
        bg=SURFACE,
        fg=MUTED,
        font=(self.FONT_FAMILY, self.FS_TINY),
    ).pack(side="left")

    tk.Label(
        footer,
        text="Clique nos botões · Enter executar · Esc fechar",
        bg=SURFACE,
        fg=BORDER,
        font=(self.FONT_FAMILY, self.FS_TINY),
    ).pack(side="left", padx=(self.px(12), 0))

    btn_frame = tk.Frame(footer, bg=SURFACE)
    btn_frame.pack(side="right")

    ttk.Button(
        btn_frame,
        text="Fechar",
        style="Cancel.TButton",
        command=self.close_app,
    ).pack(side="left", padx=(0, self.px(8)))


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


# Mostra a pagina educativa sobre ataques de jamming.
def _uavedusim_show_jamming_info_screen(self):
    self.page = "jamming_info"
    self.back_callback = self.show_main_menu
    self.selected_callback = None
    self.clear_content()
    self.set_badge("ATAQUES DE JAMMING")

    # Cabecalho com botao de voltar.
    header = tk.Frame(self.content, bg=SURFACE)
    header.pack(fill="x", padx=self.CONTENT_PAD, pady=(self.HEADER_TOP_PAD, 0))

    ttk.Button(
        header,
        text="← voltar",
        style="Back.TButton",
        command=self.show_main_menu,
    ).pack(side="left", pady=(0, self.px(10)))

    tk.Frame(header, bg=SURFACE).pack(side="left", fill="x", expand=True)

    # Caixa principal do texto educativo.
    main_box = tk.Frame(
        self.content,
        bg=SURFACE2,
        highlightbackground=BORDER,
        highlightthickness=1,
    )
    main_box.pack(
        fill="both",
        expand=True,
        padx=self.CONTENT_PAD,
        pady=(self.px(4), 0),
    )

    scroll_outer = tk.Frame(main_box, bg=SURFACE2)
    scroll_outer.pack(fill="both", expand=True)

    text_canvas = tk.Canvas(scroll_outer, bg=SURFACE2, highlightthickness=0, bd=0)
    text_scrollbar = tk.Scrollbar(
        scroll_outer,
        orient="vertical",
        command=text_canvas.yview,
        width=self.SCROLLBAR_W,
        bg=BORDER,
        troughcolor=SURFACE,
        activebackground=MUTED,
        bd=0,
        highlightthickness=0,
        relief="flat",
        elementborderwidth=0,
    )
    text_canvas.configure(yscrollcommand=text_scrollbar.set)
    text_canvas.pack(side="left", fill="both", expand=True)

    text_area = tk.Frame(text_canvas, bg=SURFACE2)
    text_area_id = text_canvas.create_window((0, 0), window=text_area, anchor="nw")

    inner_pad = self.px(18)
    self._ji_scroll_enabled = False

    def _ji_content_overflows():
        try:
            return text_area.winfo_reqheight() > text_canvas.winfo_height()
        except Exception:
            return False

    def _ji_sync_scrollregion(_event=None):
        text_canvas.configure(scrollregion=text_canvas.bbox("all"))
        overflows = _ji_content_overflows()
        self._ji_scroll_enabled = overflows
        if overflows:
            if not text_scrollbar.winfo_ismapped():
                text_scrollbar.pack(side="right", fill="y")
        else:
            if text_scrollbar.winfo_ismapped():
                text_scrollbar.pack_forget()
            text_canvas.yview_moveto(0)

    def _ji_sync_width(event):
        text_canvas.itemconfigure(text_area_id, width=event.width)
        _ji_sync_scrollregion()

    def _ji_wheel(event):
        if not self._ji_scroll_enabled:
            return
        if getattr(event, "num", None) == 4:
            text_canvas.yview_scroll(-3, "units")
        elif getattr(event, "num", None) == 5:
            text_canvas.yview_scroll(3, "units")
        elif getattr(event, "delta", 0):
            text_canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

    text_area.bind("<Configure>", _ji_sync_scrollregion)
    text_canvas.bind("<Enter>", lambda _e: (
        text_canvas.bind_all("<MouseWheel>", _ji_wheel),
        text_canvas.bind_all("<Button-4>", _ji_wheel),
        text_canvas.bind_all("<Button-5>", _ji_wheel),
    ))
    text_canvas.bind("<Leave>", lambda _e: (
        text_canvas.unbind_all("<MouseWheel>"),
        text_canvas.unbind_all("<Button-4>"),
        text_canvas.unbind_all("<Button-5>"),
    ))

    # Area interna onde o texto e exibido.
    text_area_padded = tk.Frame(text_area, bg=SURFACE2)
    text_area_padded.pack(
        fill="both",
        expand=True,
        padx=inner_pad,
        pady=(inner_pad, self.px(10)),
    )

    # Titulo da pagina educativa.
    tk.Label(
        text_area_padded,
        text="Ataques de Jamming GPS/GNSS",
        bg=SURFACE2,
        fg=TEXT,
        justify="center",
        anchor="center",
        font=(self.FONT_FAMILY, self.fs(22), "bold"),
    ).pack(fill="x", pady=(0, self.px(14)))

    # Renderiza os paragrafos do texto educativo.
    self._ji_labels = []
    blocks = UAVEDUSIM_JAMMING_ATTACKS_TEXT.split("\n\n")

    for block in blocks:
        block = block.strip()
        if not block:
            continue

        is_subtitle = block in UAVEDUSIM_JAMMING_SUBTITLES

        lbl = tk.Label(
            text_area_padded,
            text=block,
            bg=SURFACE2,
            fg=TEXT if is_subtitle else MUTED,
            justify="left",
            anchor="nw",
            wraplength=max(self.px(500), self.win_w - self.px(140)),
            font=(
                self.FONT_FAMILY,
                self.fs(13 if is_subtitle else 10),
                "bold" if is_subtitle else "normal",
            ),
        )
        lbl.pack(anchor="w", fill="x", pady=(0, self.px(12 if is_subtitle else 10)))
        self._ji_labels.append(lbl)

    # Botao para abrir a tela de mitigacoes.
    try:
        self.style.configure(
            "JammingMitig.TButton",
            font=(self.FONT_FAMILY, self.FS_BUTTON, "bold"),
            padding=(self.px(18), self.px(11)),
            background=ACCENT,
            foreground="#11111b",
            relief="flat",
            borderwidth=0,
        )
        self.style.map(
            "JammingMitig.TButton",
            background=[("disabled", DISABLED_BG), ("active", ACCENT_DIM), ("pressed", ACCENT_DIM)],
            foreground=[("disabled", DISABLED_FG), ("active", "#11111b"), ("pressed", "#11111b")],
        )
    except Exception:
        pass

    ttk.Button(
        text_area_padded,
        text="Entenda as mitigações do simulador",
        style="JammingMitig.TButton",
        command=lambda: _uavedusim_show_mitigation_screen(self),
    ).pack(fill="x", pady=(0, self.px(14)))

    def _ji_reflow(_event=None):
        try:
            w = text_canvas.winfo_width()
            wrap = max(self.px(420), w - self.px(24))
            for lbl in getattr(self, "_ji_labels", []):
                lbl.configure(wraplength=wrap)
        except Exception:
            pass

    text_canvas.bind("<Configure>", lambda event: (_ji_sync_width(event), _ji_reflow()))

    self.root.after_idle(_ji_reflow)
    self.root.after_idle(_ji_sync_scrollregion)

    # Rodape com acoes da tela.
    footer = tk.Frame(self.content, bg=SURFACE)
    footer.pack(
        fill="x",
        padx=self.CONTENT_PAD,
        pady=(self.FOOTER_TOP_PAD, self.FOOTER_BOTTOM_PAD),
    )

    tk.Label(
        footer,
        text="Esc voltar",
        bg=SURFACE,
        fg=BORDER,
        font=(self.FONT_FAMILY, self.FS_TINY),
    ).pack(side="left", padx=(self.px(12), 0))

    btn_frame = tk.Frame(footer, bg=SURFACE)
    btn_frame.pack(side="right")

    ttk.Button(
        btn_frame,
        text="Fechar",
        style="Cancel.TButton",
        command=self.close_app,
    ).pack(side="left", padx=(0, self.px(8)))


# Mostra a pagina educativa sobre mitigacoes.
def _uavedusim_show_mitigation_screen(self):
    self.page = "mitigation_info"
    self.back_callback = lambda: _uavedusim_show_jamming_info_screen(self)
    self.selected_callback = None
    self.clear_content()
    self.set_badge("MITIGAÇÕES DO SIMULADOR")

    # Cor secundaria para o botao de iniciar simulacao.
    MITIG_COLOR     = "#94e2d5"
    MITIG_COLOR_DIM = "#32454a"

    # Cabecalho com botao de voltar.
    header = tk.Frame(self.content, bg=SURFACE)
    header.pack(fill="x", padx=self.CONTENT_PAD, pady=(self.HEADER_TOP_PAD, 0))

    ttk.Button(
        header,
        text="← voltar",
        style="Back.TButton",
        command=lambda: _uavedusim_show_jamming_info_screen(self),
    ).pack(side="left", pady=(0, self.px(10)))

    tk.Frame(header, bg=SURFACE).pack(side="left", fill="x", expand=True)

    # Caixa principal do texto educativo.
    main_box = tk.Frame(
        self.content,
        bg=SURFACE2,
        highlightbackground=BORDER,
        highlightthickness=1,
    )
    main_box.pack(
        fill="both",
        expand=True,
        padx=self.CONTENT_PAD,
        pady=(self.px(4), 0),
    )

    scroll_outer = tk.Frame(main_box, bg=SURFACE2)
    scroll_outer.pack(fill="both", expand=True)

    text_canvas = tk.Canvas(scroll_outer, bg=SURFACE2, highlightthickness=0, bd=0)
    text_scrollbar = tk.Scrollbar(
        scroll_outer,
        orient="vertical",
        command=text_canvas.yview,
        width=self.SCROLLBAR_W,
        bg=BORDER,
        troughcolor=SURFACE,
        activebackground=MUTED,
        bd=0,
        highlightthickness=0,
        relief="flat",
        elementborderwidth=0,
    )
    text_canvas.configure(yscrollcommand=text_scrollbar.set)
    text_canvas.pack(side="left", fill="both", expand=True)

    text_area = tk.Frame(text_canvas, bg=SURFACE2)
    text_area_id = text_canvas.create_window((0, 0), window=text_area, anchor="nw")

    inner_pad = self.px(18)
    self._mi_scroll_enabled = False

    def _mi_content_overflows():
        try:
            return text_area.winfo_reqheight() > text_canvas.winfo_height()
        except Exception:
            return False

    def _mi_sync_scrollregion(_event=None):
        text_canvas.configure(scrollregion=text_canvas.bbox("all"))
        overflows = _mi_content_overflows()
        self._mi_scroll_enabled = overflows
        if overflows:
            if not text_scrollbar.winfo_ismapped():
                text_scrollbar.pack(side="right", fill="y")
        else:
            if text_scrollbar.winfo_ismapped():
                text_scrollbar.pack_forget()
            text_canvas.yview_moveto(0)

    def _mi_sync_width(event):
        text_canvas.itemconfigure(text_area_id, width=event.width)
        _mi_sync_scrollregion()

    def _mi_wheel(event):
        if not self._mi_scroll_enabled:
            return
        if getattr(event, "num", None) == 4:
            text_canvas.yview_scroll(-3, "units")
        elif getattr(event, "num", None) == 5:
            text_canvas.yview_scroll(3, "units")
        elif getattr(event, "delta", 0):
            text_canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

    text_area.bind("<Configure>", _mi_sync_scrollregion)
    text_canvas.bind("<Enter>", lambda _e: (
        text_canvas.bind_all("<MouseWheel>", _mi_wheel),
        text_canvas.bind_all("<Button-4>", _mi_wheel),
        text_canvas.bind_all("<Button-5>", _mi_wheel),
    ))
    text_canvas.bind("<Leave>", lambda _e: (
        text_canvas.unbind_all("<MouseWheel>"),
        text_canvas.unbind_all("<Button-4>"),
        text_canvas.unbind_all("<Button-5>"),
    ))

    # Area interna onde o texto e exibido.
    text_area_padded = tk.Frame(text_area, bg=SURFACE2)
    text_area_padded.pack(
        fill="both",
        expand=True,
        padx=inner_pad,
        pady=(inner_pad, self.px(10)),
    )

    # Titulo da pagina educativa.
    tk.Label(
        text_area_padded,
        text="Mitigações do Simulador",
        bg=SURFACE2,
        fg=TEXT,
        justify="center",
        anchor="center",
        font=(self.FONT_FAMILY, self.fs(22), "bold"),
    ).pack(fill="x", pady=(0, self.px(14)))

    # Renderiza os paragrafos do texto educativo.
    self._mi_labels = []
    blocks = UAVEDUSIM_MITIGATION_TEXT.split("\n\n")

    for block in blocks:
        block = block.strip()
        if not block:
            continue

        is_subtitle = block in UAVEDUSIM_MITIGATION_SUBTITLES

        lbl = tk.Label(
            text_area_padded,
            text=block,
            bg=SURFACE2,
            fg=TEXT if is_subtitle else MUTED,
            justify="left",
            anchor="nw",
            wraplength=max(self.px(500), self.win_w - self.px(140)),
            font=(
                self.FONT_FAMILY,
                self.fs(13 if is_subtitle else 10),
                "bold" if is_subtitle else "normal",
            ),
        )
        lbl.pack(anchor="w", fill="x", pady=(0, self.px(12 if is_subtitle else 10)))
        self._mi_labels.append(lbl)

    # Botao para iniciar a simulacao.
    try:
        self.style.configure(
            "MitigStart.TButton",
            font=(self.FONT_FAMILY, self.FS_BUTTON, "bold"),
            padding=(self.px(18), self.px(11)),
            background=MITIG_COLOR,
            foreground="#11111b",
            relief="flat",
            borderwidth=0,
        )
        self.style.map(
            "MitigStart.TButton",
            background=[("disabled", DISABLED_BG), ("active", MITIG_COLOR_DIM), ("pressed", MITIG_COLOR_DIM)],
            foreground=[("disabled", DISABLED_FG), ("active", "#11111b"), ("pressed", "#11111b")],
        )
    except Exception:
        pass

    ttk.Button(
        text_area_padded,
        text="Iniciar simulação do UAVEDUSIM",
        style="MitigStart.TButton",
        command=lambda: _uavedusim_home_run_choice(self, "Executar UAVEDUSIM"),
    ).pack(fill="x", pady=(0, self.px(14)))

    def _mi_reflow(_event=None):
        try:
            w = text_canvas.winfo_width()
            wrap = max(self.px(420), w - self.px(24))
            for lbl in getattr(self, "_mi_labels", []):
                lbl.configure(wraplength=wrap)
        except Exception:
            pass

    text_canvas.bind("<Configure>", lambda event: (_mi_sync_width(event), _mi_reflow()))

    self.root.after_idle(_mi_reflow)
    self.root.after_idle(_mi_sync_scrollregion)

    # Rodape com acoes da tela.
    footer = tk.Frame(self.content, bg=SURFACE)
    footer.pack(
        fill="x",
        padx=self.CONTENT_PAD,
        pady=(self.FOOTER_TOP_PAD, self.FOOTER_BOTTOM_PAD),
    )

    tk.Label(
        footer,
        text="Esc voltar",
        bg=SURFACE,
        fg=BORDER,
        font=(self.FONT_FAMILY, self.FS_TINY),
    ).pack(side="left", padx=(self.px(12), 0))

    btn_frame = tk.Frame(footer, bg=SURFACE)
    btn_frame.pack(side="right")

    ttk.Button(
        btn_frame,
        text="Fechar",
        style="Cancel.TButton",
        command=self.close_app,
    ).pack(side="left", padx=(0, self.px(8)))


UAVEDUSIMApp.show_main_menu = _uavedusim_home_show_main_menu
UAVEDUSIMApp.choose_current = _uavedusim_home_choose_current


if __name__ == "__main__":
    try:
        UAVEDUSIMApp().run()
    except KeyboardInterrupt:
        sys.exit(130)