"""Desktop GUI for the two-machine Jackson scheduling problem (J2 || C_max).

Connects to the compiled C++17 computation core (`jackson_solver`) via JSON
and blank-line-delimited .txt exchange, ensuring bit-exact reproducibility
with the CLI and Web versions.

Requirements:
    - Python 3.9+ (standard library only: tkinter, subprocess, json, etc.)
    - Compiled `jackson_solver` executable in the same directory or in PATH.
    - Optional: `pillow` (`pip install pillow`) to enable direct PNG export
      in addition to built-in vector SVG export.
"""

from __future__ import annotations

import json
import os
import random
import re
import shutil
import subprocess
import sys
import tkinter as tk
from dataclasses import dataclass
from html import escape as xml_escape
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Dict, List, Optional, Sequence, Tuple

try:
    from PIL import Image, ImageDraw, ImageFont

    HAS_PILLOW = True
except ImportError:
    HAS_PILLOW = False


# =============================================================================
# 1. Constants & Domain Data Models
# =============================================================================

PALETTE: Dict[int, Dict[str, str]] = {
    1: {"main": "#3b82f6", "tail": "#93c5fd", "label": "Тип 1 (M1 → M2)"},
    2: {"main": "#10b981", "tail": "#6ee7b7", "label": "Тип 2 (M2 → M1)"},
    3: {"main": "#f59e0b", "tail": "#fcd34d", "label": "Тип 3 (только M1)"},
    4: {"main": "#8b5cf6", "tail": "#c4b5fd", "label": "Тип 4 (только M2)"},
}

DURATION_MODES: List[Tuple[str, str]] = [
    ("pairs", "Простые пары чисел (детерм.)"),
    ("lower", "Нижняя граница L (детерм.)"),
    ("upper", "Верхняя граница U (детерм.)"),
    ("mid", "Середина (L+U)/2 (детерм.)"),
    ("random", "Случайная точка в [L, U]"),
]

INTERVAL_METHODS: List[Tuple[int, str]] = [
    (1, "Метод 1: Независимые границы"),
    (2, "Метод 2: Зависимый предел (β)"),
    (3, "Метод 3: Ширина ≤ 100 (β)"),
    (4, "Метод 4: Мультипликативный (β, α)"),
]

RANGE_PRESETS: List[Tuple[str, Tuple[float, float]]] = [
    ("[20, 1000]", (20.0, 1000.0)),
    ("[2, 100]", (2.0, 100.0)),
]

OP_PREFIX_MAP: Dict[Tuple[int, int], str] = {
    (1, 1): "a",
    (2, 1): "b",
    (2, 2): "c",
    (1, 2): "d",
    (1, 3): "p",
    (2, 4): "q",
}


class SolverError(RuntimeError):
    """Raised when the C++ computation core fails or cannot be located."""


@dataclass(frozen=True)
class TwoStageJob:
    """Two-operation job (a_j, b_j) for Type 1 or (c_j, d_j) for Type 2."""

    job_id: int
    first_stage: int
    second_stage: int


@dataclass(frozen=True)
class ProblemFields:
    """String representation of the six input fields in the GUI."""

    t1: str
    pi1: str
    t2: str
    pi2: str
    t3: str
    t4: str


@dataclass(frozen=True)
class GanttBlock:
    """Single operation interval on a machine track."""

    machine: int
    job_type: int
    job_id: int
    start: int
    end: int

    @property
    def duration(self) -> int:
        return self.end - self.start

    @property
    def op_name(self) -> str:
        prefix = OP_PREFIX_MAP[(self.machine, self.job_type)]
        return f"{prefix}_{self.job_id}"


@dataclass(frozen=True)
class MachineMetrics:
    """Aggregated timing metrics for a single machine."""

    initial_block_load: int
    total_work_time: int
    completion_time: int
    internal_idle_time: int
    total_idle_time: int


@dataclass(frozen=True)
class ScheduleResult:
    """Complete evaluation output returned by the C++ core."""

    c_max: int
    opt_c_max: int
    anti_c_max: int
    perm1: Tuple[int, ...]
    perm2: Tuple[int, ...]
    m1: MachineMetrics
    m2: MachineMetrics
    blocks: Tuple[GanttBlock, ...]


@dataclass(frozen=True)
class GeneratorParams:
    """Parameters forwarded to `jackson_solver --generate`."""

    n1: int
    n2: int
    n3: int
    n4: int
    mode: str
    method: int
    min_val: float
    max_val: float
    beta: float
    alpha: float
    seed: int


@dataclass(frozen=True)
class HitBox:
    """Screen-space bounding box used for interactive Gantt tooltips."""

    x1: float
    y1: float
    x2: float
    y2: float
    tooltip: str


# =============================================================================
# 2. Input Validation & Clean .txt Parser / Serializer
# =============================================================================


def parse_positive_int(token: str, context: str) -> int:
    """Parse a strictly positive integer token (>= 1)."""
    if not token.isdigit():
        raise ValueError(
            f"Все длительности должны быть положительными целыми числами (≥ 1). "
            f"Ошибка в {context}: '{token}'."
        )
    value = int(token)
    if value < 1:
        raise ValueError(
            f"Длительность должна быть ≥ 1 ({context}), получено: {value}."
        )
    return value


def parse_two_stage_entry(text: str, label: str) -> List[TwoStageJob]:
    """Parse comma-separated pairs 'op1 op2, op1 op2' from a GUI entry."""
    stripped = text.strip()
    if not stripped:
        return []
    jobs: List[TwoStageJob] = []
    for idx, raw_pair in enumerate(stripped.split(","), start=1):
        parts = raw_pair.strip().split()
        if len(parts) != 2:
            raise ValueError(
                f"В поле «{label}» ожидалась пара целых чисел (op1 op2), "
                f"получено: '{raw_pair.strip()}'."
            )
        op1 = parse_positive_int(parts[0], f"{label}, пара #{idx}")
        op2 = parse_positive_int(parts[1], f"{label}, пара #{idx}")
        jobs.append(TwoStageJob(job_id=idx, first_stage=op1, second_stage=op2))
    return jobs


def parse_int_list_entry(text: str, label: str) -> List[int]:
    """Parse whitespace-separated positive integers from a GUI entry."""
    stripped = text.strip()
    if not stripped:
        return []
    return [parse_positive_int(tok, label) for tok in stripped.split()]


def parse_permutation_entry(text: str, n: int, name: str) -> List[int]:
    """Validate and parse a 1-based permutation of {1, ..., n}."""
    if n == 0:
        if text.strip():
            raise ValueError(
                f"Задана перестановка {name}, но число работ данного типа равно 0."
            )
        return []
    tokens = text.strip().split()
    if not tokens:
        return list(range(1, n + 1))
    nums = [parse_positive_int(tok, f"перестановке {name}") for tok in tokens]
    if sorted(nums) != list(range(1, n + 1)):
        raise ValueError(
            f"Перестановка {name} должна содержать числа от 1 до {n} без повторений."
        )
    return nums


def serialize_to_txt(fields: ProblemFields) -> str:
    """Validate GUI fields and format them into the clean blank-line .txt layout."""
    t1 = parse_two_stage_entry(fields.t1, "Тип 1")
    t2 = parse_two_stage_entry(fields.t2, "Тип 2")
    t3 = parse_int_list_entry(fields.t3, "Тип 3")
    t4 = parse_int_list_entry(fields.t4, "Тип 4")

    if len(t1) + len(t2) + len(t3) + len(t4) == 0:
        raise ValueError("Введите хотя бы одно требование.")

    pi1 = parse_permutation_entry(fields.pi1, len(t1), "π₁")
    pi2 = parse_permutation_entry(fields.pi2, len(t2), "π₂")

    blocks: List[str] = [
        "\n".join(f"{j.first_stage} {j.second_stage}" for j in t1) if t1 else "-",
        " ".join(str(x) for x in pi1) if pi1 else "-",
        "\n".join(f"{j.first_stage} {j.second_stage}" for j in t2) if t2 else "-",
        " ".join(str(x) for x in pi2) if pi2 else "-",
    ]
    if t3 or t4:
        blocks.append(" ".join(str(x) for x in t3) if t3 else "-")
    if t4:
        blocks.append(" ".join(str(x) for x in t4))

    return "\n\n".join(blocks) + "\n"


def parse_txt_content(raw_text: str) -> ProblemFields:
    """Parse clean blank-line-delimited .txt text into GUI input strings.

    Supports column and inline formats, commas, UTF-8 BOM, '-' placeholders,
    and compact one-line-per-block fallback.
    """
    cleaned = raw_text.lstrip("\ufeff").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not cleaned:
        raise ValueError("Файл пуст.")

    blocks = [b.strip() for b in re.split(r"\n[ \t]*\n", cleaned)]
    if len(blocks) == 1 and "\n" in cleaned:
        blocks = [line.strip() for line in cleaned.split("\n") if line.strip()]

    def extract_nums(idx: int, label: str) -> List[int]:
        if idx >= len(blocks):
            return []
        block = blocks[idx].strip()
        if not block or block == "-":
            return []
        tokens = block.replace(",", " ").split()
        return [parse_positive_int(tok, label) for tok in tokens]

    def nums_to_pairs_str(nums: Sequence[int], label: str) -> str:
        if len(nums) % 2 != 0:
            raise ValueError(
                f"В блоке «{label}» нечётное количество чисел ({len(nums)}), "
                f"ожидались пары длительностей."
            )
        return ", ".join(f"{nums[i]} {nums[i + 1]}" for i in range(0, len(nums), 2))

    b1 = extract_nums(0, "Тип 1")
    b2 = extract_nums(1, "Перестановка π₁")
    b3 = extract_nums(2, "Тип 2")
    b4 = extract_nums(3, "Перестановка π₂")
    b5 = extract_nums(4, "Тип 3")
    b6 = extract_nums(5, "Тип 4")

    t1_str = nums_to_pairs_str(b1, "Тип 1")
    t2_str = nums_to_pairs_str(b3, "Тип 2")
    n1 = len(b1) // 2
    n2 = len(b3) // 2

    pi1_str = (
        " ".join(str(x) for x in b2)
        if b2
        else (" ".join(str(i) for i in range(1, n1 + 1)) if n1 > 0 else "")
    )
    pi2_str = (
        " ".join(str(x) for x in b4)
        if b4
        else (" ".join(str(i) for i in range(1, n2 + 1)) if n2 > 0 else "")
    )

    return ProblemFields(
        t1=t1_str,
        pi1=pi1_str,
        t2=t2_str,
        pi2=pi2_str,
        t3=" ".join(str(x) for x in b5),
        t4=" ".join(str(x) for x in b6),
    )


# =============================================================================
# 3. C++ Core Bridge (`jackson_solver`)
# =============================================================================


class SolverBridge:
    """Subprocess wrapper around the compiled C++17 `jackson_solver` binary."""

    def __init__(self, executable_path: Optional[Path] = None) -> None:
        self.executable: Optional[Path] = executable_path or self._discover_binary()

    @staticmethod
    def _discover_binary() -> Optional[Path]:
        names = (
            ["jackson_solver.exe", "jackson_solver"]
            if os.name == "nt"
            else ["jackson_solver", "jackson_solver.exe"]
        )
        script_dir = Path(__file__).resolve().parent
        for base in (script_dir, Path.cwd()):
            for name in names:
                candidate = base / name
                if candidate.is_file():
                    return candidate
        for name in names:
            found = shutil.which(name)
            if found:
                return Path(found)
        return None

    def _require_executable(self) -> Path:
        if self.executable is None or not self.executable.is_file():
            self.executable = self._discover_binary()
        if self.executable is None or not self.executable.is_file():
            raise SolverError(
                "Не найден исполняемый файл C++ ядра (jackson_solver).\n"
                "Скомпилируйте jackson_solver.cpp рядом с этим скриптом:\n"
                "g++ -std=c++17 -O2 jackson_solver.cpp -o jackson_solver"
            )
        return self.executable

    def _run_process(
        self, args: Sequence[str], stdin_text: Optional[str] = None
    ) -> subprocess.CompletedProcess[str]:
        exe = self._require_executable()
        kwargs: Dict[str, object] = {
            "input": stdin_text,
            "capture_output": True,
            "text": True,
            "encoding": "utf-8",
            "check": False,
        }
        if os.name == "nt":
            kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)

        proc = subprocess.run([str(exe), *args], **kwargs)  # type: ignore[arg-type]
        if proc.returncode != 0:
            err_msg = proc.stderr.strip() or proc.stdout.strip() or "Неизвестная ошибка."
            raise SolverError(err_msg)
        return proc

    def evaluate(self, txt_payload: str, perm_mode: str = "manual") -> ScheduleResult:
        """Evaluate a schedule via `jackson_solver --stdin --format json`."""
        proc = self._run_process(
            ["--stdin", "--perm", perm_mode, "--format", "json"],
            stdin_text=txt_payload,
        )
        try:
            data = json.loads(proc.stdout)
        except json.JSONDecodeError as exc:
            raise SolverError(f"Некорректный JSON от C++ ядра: {exc}") from exc

        def parse_metrics(raw: Dict[str, int]) -> MachineMetrics:
            return MachineMetrics(
                initial_block_load=int(raw["initial_block_load"]),
                total_work_time=int(raw["total_work_time"]),
                completion_time=int(raw["completion_time"]),
                internal_idle_time=int(raw["internal_idle_time"]),
                total_idle_time=int(raw["total_idle_time"]),
            )

        blocks = tuple(
            GanttBlock(
                machine=int(b["machine"]),
                job_type=int(b["type"]),
                job_id=int(b["id"]),
                start=int(b["start"]),
                end=int(b["end"]),
            )
            for b in data["blocks"]
        )

        return ScheduleResult(
            c_max=int(data["c_max"]),
            opt_c_max=int(data["opt_c_max"]),
            anti_c_max=int(data["anti_c_max"]),
            perm1=tuple(int(x) for x in data["perm1"]),
            perm2=tuple(int(x) for x in data["perm2"]),
            m1=parse_metrics(data["m1"]),
            m2=parse_metrics(data["m2"]),
            blocks=blocks,
        )

    def generate(self, params: GeneratorParams) -> Tuple[ProblemFields, int]:
        """Generate an instance via `jackson_solver --generate` and return (fields, seed)."""
        args = [
            "--generate",
            "--n1", str(params.n1),
            "--n2", str(params.n2),
            "--n3", str(params.n3),
            "--n4", str(params.n4),
            "--mode", params.mode,
            "--method", str(params.method),
            "--min", f"{params.min_val:g}",
            "--max", f"{params.max_val:g}",
            "--beta", f"{params.beta:g}",
            "--alpha", f"{params.alpha:g}",
            "--seed", str(params.seed),
        ]
        proc = self._run_process(args)
        match = re.search(r"Seed:\s*(\d+)", proc.stderr)
        actual_seed = int(match.group(1)) if match else params.seed
        fields = parse_txt_content(proc.stdout)
        return fields, actual_seed


# =============================================================================
# 4. Gantt Chart Layout, Rendering (Tkinter Canvas, SVG, PNG) & Tooltips
# =============================================================================


def apply_right_shift(
    blocks: Sequence[GanttBlock], machine_id: int, completion_time: int
) -> List[GanttBlock]:
    """Shift second-stage (tail) operations on a machine rightwards to completion_time."""
    tail_type = 2 if machine_id == 1 else 1
    head = [b for b in blocks if b.machine == machine_id and b.job_type != tail_type]
    tail = [b for b in blocks if b.machine == machine_id and b.job_type == tail_type]

    shifted_tail: List[GanttBlock] = []
    cursor = completion_time
    for b in reversed(tail):
        dur = b.duration
        shifted_tail.append(
            GanttBlock(
                machine=b.machine,
                job_type=b.job_type,
                job_id=b.job_id,
                start=cursor - dur,
                end=cursor,
            )
        )
        cursor -= dur
    shifted_tail.reverse()
    return head + shifted_tail


def compute_grid_step(c_max: int) -> int:
    """Choose a clean time-axis tick interval based on C_max."""
    if c_max > 20000:
        return 2500
    if c_max > 5000:
        return 1000
    if c_max > 1000:
        return 250
    if c_max > 200:
        return 50
    if c_max > 50:
        return 10
    if c_max > 20:
        return 2
    return 1


def extract_idle_intervals(
    blocks: Sequence[GanttBlock], c_max: int
) -> List[Tuple[int, int, str]]:
    """Return list of (start, end, label) idle intervals along a machine track."""
    idles: List[Tuple[int, int, str]] = []
    cursor = 0
    for b in blocks:
        if b.start > cursor:
            idles.append((cursor, b.start, "Окно простоя"))
        cursor = b.end
    if c_max > cursor:
        idles.append((cursor, c_max, "Простой"))
    return idles


class GanttView(ttk.Frame):
    """Interactive Gantt chart widget with Macro-view, tooltips, and SVG/PNG export."""

    LEFT_PAD = 130
    RIGHT_PAD = 50
    Y_M1 = 60
    Y_M2 = 180
    BAR_H = 56
    MIN_H = 300

    def __init__(self, master: tk.Misc) -> None:
        super().__init__(master)
        self._result: Optional[ScheduleResult] = None
        self._shift_right: bool = False
        self._macro_view: bool = False
        self._hitboxes: List[HitBox] = []

        self.canvas = tk.Canvas(
            self,
            bg="#ffffff",
            highlightthickness=1,
            highlightbackground="#cbd5e1",
            height=self.MIN_H,
            cursor="crosshair",
        )
        self.canvas.pack(fill="both", expand=True)

        self._tooltip = tk.Label(
            self,
            bg="#0f172a",
            fg="#ffffff",
            font=("Consolas", 9),
            padx=8,
            pady=4,
            relief="flat",
        )

        self.canvas.bind("<Configure>", lambda _e: self.redraw())
        self.canvas.bind("<Motion>", self._on_mouse_move)
        self.canvas.bind("<Leave>", lambda _e: self._hide_tooltip())

    def set_data(
        self,
        result: Optional[ScheduleResult],
        shift_right: bool,
        macro_view: bool,
    ) -> None:
        """Update chart state and trigger a redraw."""
        self._result = result
        self._shift_right = shift_right
        self._macro_view = macro_view
        self.redraw()

    def clear(self) -> None:
        """Clear the canvas and remove cached hitboxes."""
        self._result = None
        self._hitboxes.clear()
        self._hide_tooltip()
        self.canvas.delete("all")

    def _active_tracks(
        self, res: ScheduleResult
    ) -> Tuple[List[GanttBlock], List[GanttBlock]]:
        if self._shift_right:
            b_m1 = apply_right_shift(res.blocks, 1, res.m1.completion_time)
            b_m2 = apply_right_shift(res.blocks, 2, res.m2.completion_time)
        else:
            b_m1 = [b for b in res.blocks if b.machine == 1]
            b_m2 = [b for b in res.blocks if b.machine == 2]
        return b_m1, b_m2

    def redraw(self) -> None:
        """Redraw the Gantt chart on the Tkinter Canvas."""
        self.canvas.delete("all")
        self._hitboxes.clear()
        self._hide_tooltip()

        if self._result is None:
            return

        res = self._result
        w = max(self.canvas.winfo_width(), 640)
        c_max = max(res.c_max, 1)
        scale = (w - self.LEFT_PAD - self.RIGHT_PAD) / c_max

        # Vertical grid lines
        step = compute_grid_step(res.c_max)
        for t in range(0, res.c_max + 1, step):
            x = self.LEFT_PAD + t * scale
            self.canvas.create_line(
                x, 30, x, self.Y_M2 + self.BAR_H + 25,
                fill="#e2e8f0", dash=(2, 2),
            )
            self.canvas.create_text(
                x, 18, text=str(t), fill="#64748b", font=("Segoe UI", 9)
            )

        b_m1, b_m2 = self._active_tracks(res)
        self._draw_track("Прибор 1 (M1)", self.Y_M1, b_m1, res.m1.initial_block_load, res.c_max, scale, "T₁")
        self._draw_track("Прибор 2 (M2)", self.Y_M2, b_m2, res.m2.initial_block_load, res.c_max, scale, "T₂")

        # Final C_max vertical line
        x_cmax = self.LEFT_PAD + res.c_max * scale
        self.canvas.create_line(
            x_cmax, 25, x_cmax, self.Y_M2 + self.BAR_H + 28,
            fill="#dc2626", width=2, dash=(5, 3),
        )
        self.canvas.create_text(
            x_cmax, self.Y_M2 + self.BAR_H + 44,
            text=f"C_max = {res.c_max}",
            fill="#dc2626",
            font=("Segoe UI", 10, "bold"),
        )

    def _draw_track(
        self,
        title: str,
        y: int,
        blocks: Sequence[GanttBlock],
        t_val: int,
        c_max: int,
        scale: float,
        t_name: str,
    ) -> None:
        h = self.BAR_H
        self.canvas.create_text(
            12, y + h / 2, text=title, anchor="w",
            fill="#1e293b", font=("Segoe UI", 10, "bold"),
        )
        self.canvas.create_rectangle(
            self.LEFT_PAD, y, self.LEFT_PAD + c_max * scale, y + h,
            fill="#f8fafc", outline="#cbd5e1",
        )

        # Idle windows
        for start, end, label in extract_idle_intervals(blocks, c_max):
            x1 = self.LEFT_PAD + start * scale
            x2 = self.LEFT_PAD + end * scale
            dur = end - start
            self.canvas.create_rectangle(
                x1, y, x2, y + h, fill="#fee2e2", outline="#fca5a5"
            )
            if x2 - x1 > 42:
                self.canvas.create_text(
                    (x1 + x2) / 2, y + h / 2 - 7, text=label,
                    fill="#b91c1c", font=("Segoe UI", 8, "italic"),
                )
                self.canvas.create_text(
                    (x1 + x2) / 2, y + h / 2 + 8, text=str(dur),
                    fill="#b91c1c", font=("Segoe UI", 8, "italic"),
                )
            self._hitboxes.append(
                HitBox(x1, y, x2, y + h, f"{label}: длит. {dur} [{start} — {end}]")
            )

        # Operation blocks
        for b in blocks:
            x1 = self.LEFT_PAD + b.start * scale
            x2 = self.LEFT_PAD + b.end * scale
            pal = PALETTE[b.job_type]
            is_tail = (b.machine == 1 and b.job_type == 2) or (
                b.machine == 2 and b.job_type == 1
            )
            fill_col = pal["tail"] if is_tail else pal["main"]
            text_col = "#0f172a" if is_tail else "#ffffff"

            if self._macro_view:
                self.canvas.create_rectangle(
                    x1, y, max(x2, x1 + 1.0), y + h,
                    fill=fill_col, outline="",
                )
            else:
                self.canvas.create_rectangle(
                    x1, y, x2, y + h,
                    fill=fill_col, outline="#1e293b", width=1,
                )
                if x2 - x1 > 30:
                    self.canvas.create_text(
                        (x1 + x2) / 2, y + h / 2 - 8,
                        text=b.op_name, fill=text_col,
                        font=("Segoe UI", 9, "bold"),
                    )
                    self.canvas.create_text(
                        (x1 + x2) / 2, y + h / 2 + 8,
                        text=f"({b.duration})", fill=text_col,
                        font=("Segoe UI", 8),
                    )
                if x2 - x1 > 20:
                    self.canvas.create_text(
                        x2, y + h + 12, text=str(b.end),
                        fill="#334155", font=("Segoe UI", 8),
                    )

            self._hitboxes.append(
                HitBox(
                    x1, y, x2, y + h,
                    f"{b.op_name} ({pal['label']}): длит. {b.duration} [{b.start} — {b.end}]",
                )
            )

        if self._macro_view:
            self.canvas.create_rectangle(
                self.LEFT_PAD, y, self.LEFT_PAD + c_max * scale, y + h,
                fill="", outline="#94a3b8", width=1,
            )

        # Boundary marker T1 / T2
        if t_val > 0:
            x_t = self.LEFT_PAD + t_val * scale
            self.canvas.create_line(
                x_t, y - 10, x_t, y + h + 10,
                fill="#0f172a", width=2, dash=(4, 2),
            )
            self.canvas.create_text(
                x_t, y - 16, text=f"{t_name}={t_val}",
                fill="#0f172a", font=("Segoe UI", 9, "bold"),
            )

    def _on_mouse_move(self, event: tk.Event[tk.Canvas]) -> None:
        mx, my = float(event.x), float(event.y)
        for box in reversed(self._hitboxes):
            if box.x1 <= mx <= box.x2 and box.y1 <= my <= box.y2:
                self._tooltip.config(text=box.tooltip)
                self._tooltip.update_idletasks()
                tip_w = self._tooltip.winfo_reqwidth()
                canvas_w = self.canvas.winfo_width()
                px = max(8, min(int(mx) + 12, canvas_w - tip_w - 8))
                py = max(8, int(my) - 32)
                self._tooltip.place(x=px, y=py)
                return
        self._hide_tooltip()

    def _hide_tooltip(self) -> None:
        self._tooltip.place_forget()

    def export_svg(self, filepath: Path, width: int = 1100, height: int = 310) -> None:
        """Export the current Gantt chart as publication-ready vector SVG."""
        if self._result is None:
            raise ValueError("Нет данных для экспорта диаграммы.")

        res = self._result
        c_max = max(res.c_max, 1)
        scale = (width - self.LEFT_PAD - self.RIGHT_PAD) / c_max
        b_m1, b_m2 = self._active_tracks(res)

        lines: List[str] = [
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
            f'viewBox="0 0 {width} {height}">',
            '<style>'
            'text { font-family: "Segoe UI", Arial, sans-serif; }'
            '</style>',
            f'<rect width="{width}" height="{height}" fill="#ffffff"/>',
        ]

        step = compute_grid_step(res.c_max)
        for t in range(0, res.c_max + 1, step):
            x = self.LEFT_PAD + t * scale
            lines.append(
                f'<line x1="{x:.2f}" y1="30" x2="{x:.2f}" y2="{self.Y_M2 + self.BAR_H + 25}" '
                f'stroke="#e2e8f0" stroke-dasharray="2,2"/>'
            )
            lines.append(
                f'<text x="{x:.2f}" y="20" text-anchor="middle" font-size="11" fill="#64748b">{t}</text>'
            )

        def append_track(title: str, y: int, blocks: Sequence[GanttBlock], t_val: int, t_name: str) -> None:
            h = self.BAR_H
            lines.append(
                f'<text x="12" y="{y + h / 2 + 4:.1f}" font-size="13" font-weight="bold" fill="#1e293b">'
                f'{xml_escape(title)}</text>'
            )
            lines.append(
                f'<rect x="{self.LEFT_PAD}" y="{y}" width="{res.c_max * scale:.2f}" height="{h}" '
                f'fill="#f8fafc" stroke="#cbd5e1"/>'
            )
            for start, end, label in extract_idle_intervals(blocks, res.c_max):
                x1 = self.LEFT_PAD + start * scale
                bw = (end - start) * scale
                lines.append(
                    f'<rect x="{x1:.2f}" y="{y}" width="{bw:.2f}" height="{h}" '
                    f'fill="#fee2e2" stroke="#fca5a5"/>'
                )
                if bw > 42:
                    cx = x1 + bw / 2
                    lines.append(
                        f'<text x="{cx:.2f}" y="{y + h / 2 - 2:.1f}" text-anchor="middle" '
                        f'font-size="10" font-style="italic" fill="#b91c1c">{xml_escape(label)}</text>'
                    )
                    lines.append(
                        f'<text x="{cx:.2f}" y="{y + h / 2 + 12:.1f}" text-anchor="middle" '
                        f'font-size="10" font-style="italic" fill="#b91c1c">{end - start}</text>'
                    )

            for b in blocks:
                x1 = self.LEFT_PAD + b.start * scale
                bw = b.duration * scale
                pal = PALETTE[b.job_type]
                is_tail = (b.machine == 1 and b.job_type == 2) or (
                    b.machine == 2 and b.job_type == 1
                )
                fill_col = pal["tail"] if is_tail else pal["main"]
                text_col = "#0f172a" if is_tail else "#ffffff"

                if self._macro_view:
                    lines.append(
                        f'<rect x="{x1:.2f}" y="{y}" width="{max(bw + 0.4, 1.0):.2f}" height="{h}" '
                        f'fill="{fill_col}"/>'
                    )
                else:
                    lines.append(
                        f'<rect x="{x1:.2f}" y="{y}" width="{bw:.2f}" height="{h}" '
                        f'fill="{fill_col}" stroke="#1e293b" stroke-width="1"/>'
                    )
                    if bw > 30:
                        cx = x1 + bw / 2
                        lines.append(
                            f'<text x="{cx:.2f}" y="{y + h / 2 - 2:.1f}" text-anchor="middle" '
                            f'font-size="11" font-weight="bold" fill="{text_col}">{xml_escape(b.op_name)}</text>'
                        )
                        lines.append(
                            f'<text x="{cx:.2f}" y="{y + h / 2 + 13:.1f}" text-anchor="middle" '
                            f'font-size="10" fill="{text_col}">({b.duration})</text>'
                        )
                    if bw > 20:
                        lines.append(
                            f'<text x="{x1 + bw:.2f}" y="{y + h + 14}" text-anchor="middle" '
                            f'font-size="10" fill="#334155">{b.end}</text>'
                        )

            if self._macro_view:
                lines.append(
                    f'<rect x="{self.LEFT_PAD}" y="{y}" width="{res.c_max * scale:.2f}" height="{h}" '
                    f'fill="none" stroke="#94a3b8" stroke-width="1"/>'
                )
            if t_val > 0:
                x_t = self.LEFT_PAD + t_val * scale
                lines.append(
                    f'<line x1="{x_t:.2f}" y1="{y - 10}" x2="{x_t:.2f}" y2="{y + h + 10}" '
                    f'stroke="#0f172a" stroke-width="2" stroke-dasharray="4,2"/>'
                )
                lines.append(
                    f'<text x="{x_t:.2f}" y="{y - 14}" text-anchor="middle" '
                    f'font-size="11" font-weight="bold" fill="#0f172a">{xml_escape(t_name)}={t_val}</text>'
                )

        append_track("Прибор 1 (M1)", self.Y_M1, b_m1, res.m1.initial_block_load, "T1")
        append_track("Прибор 2 (M2)", self.Y_M2, b_m2, res.m2.initial_block_load, "T2")

        x_cmax = self.LEFT_PAD + res.c_max * scale
        lines.append(
            f'<line x1="{x_cmax:.2f}" y1="25" x2="{x_cmax:.2f}" y2="{self.Y_M2 + self.BAR_H + 28}" '
            f'stroke="#dc2626" stroke-width="2" stroke-dasharray="5,3"/>'
        )
        lines.append(
            f'<text x="{x_cmax:.2f}" y="{self.Y_M2 + self.BAR_H + 46}" text-anchor="middle" '
            f'font-size="13" font-weight="bold" fill="#dc2626">C_max = {res.c_max}</text>'
        )
        lines.append("</svg>\n")

        filepath.write_text("\n".join(lines), encoding="utf-8")

    def export_png(self, filepath: Path, width: int = 1200, height: int = 320) -> None:
        """Export the current Gantt chart as a raster PNG image (requires Pillow)."""
        if not HAS_PILLOW:
            raise RuntimeError("Для сохранения в PNG установите библиотеку Pillow (pip install pillow).")
        if self._result is None:
            raise ValueError("Нет данных для экспорта диаграммы.")

        res = self._result
        c_max = max(res.c_max, 1)
        scale = (width - self.LEFT_PAD - self.RIGHT_PAD) / c_max
        b_m1, b_m2 = self._active_tracks(res)

        img = Image.new("RGB", (width, height), "#ffffff")
        draw = ImageDraw.Draw(img)
        font = ImageFont.load_default()

        step = compute_grid_step(res.c_max)
        for t in range(0, res.c_max + 1, step):
            x = int(self.LEFT_PAD + t * scale)
            draw.line([(x, 30), (x, self.Y_M2 + self.BAR_H + 25)], fill="#e2e8f0", width=1)
            draw.text((x, 14), str(t), fill="#64748b", font=font, anchor="mm")

        def draw_track(title: str, y: int, blocks: Sequence[GanttBlock], t_val: int, t_name: str) -> None:
            h = self.BAR_H
            draw.text((12, y + h // 2), title, fill="#1e293b", font=font, anchor="lm")
            draw.rectangle(
                [self.LEFT_PAD, y, int(self.LEFT_PAD + res.c_max * scale), y + h],
                fill="#f8fafc", outline="#cbd5e1",
            )
            for start, end, _label in extract_idle_intervals(blocks, res.c_max):
                x1 = int(self.LEFT_PAD + start * scale)
                x2 = int(self.LEFT_PAD + end * scale)
                draw.rectangle([x1, y, x2, y + h], fill="#fee2e2", outline="#fca5a5")
                if x2 - x1 > 42:
                    draw.text(((x1 + x2) // 2, y + h // 2), f"Idle ({end - start})", fill="#b91c1c", font=font, anchor="mm")

            for b in blocks:
                x1 = int(self.LEFT_PAD + b.start * scale)
                x2 = int(self.LEFT_PAD + b.end * scale)
                pal = PALETTE[b.job_type]
                is_tail = (b.machine == 1 and b.job_type == 2) or (
                    b.machine == 2 and b.job_type == 1
                )
                fill_col = pal["tail"] if is_tail else pal["main"]
                text_col = "#0f172a" if is_tail else "#ffffff"

                if self._macro_view:
                    draw.rectangle([x1, y, max(x2, x1 + 1), y + h], fill=fill_col)
                else:
                    draw.rectangle([x1, y, x2, y + h], fill=fill_col, outline="#1e293b")
                    if x2 - x1 > 34:
                        draw.text(((x1 + x2) // 2, y + h // 2 - 7), b.op_name, fill=text_col, font=font, anchor="mm")
                        draw.text(((x1 + x2) // 2, y + h // 2 + 8), f"({b.duration})", fill=text_col, font=font, anchor="mm")
                    if x2 - x1 > 22:
                        draw.text((x2, y + h + 11), str(b.end), fill="#334155", font=font, anchor="mm")

            if self._macro_view:
                draw.rectangle(
                    [self.LEFT_PAD, y, int(self.LEFT_PAD + res.c_max * scale), y + h],
                    outline="#94a3b8",
                )
            if t_val > 0:
                x_t = int(self.LEFT_PAD + t_val * scale)
                draw.line([(x_t, y - 10), (x_t, y + h + 10)], fill="#0f172a", width=2)
                draw.text((x_t, y - 16), f"{t_name}={t_val}", fill="#0f172a", font=font, anchor="mm")

        draw_track("Machine 1 (M1)", self.Y_M1, b_m1, res.m1.initial_block_load, "T1")
        draw_track("Machine 2 (M2)", self.Y_M2, b_m2, res.m2.initial_block_load, "T2")

        x_cmax = int(self.LEFT_PAD + res.c_max * scale)
        draw.line([(x_cmax, 25), (x_cmax, self.Y_M2 + self.BAR_H + 28)], fill="#dc2626", width=2)
        draw.text((x_cmax, self.Y_M2 + self.BAR_H + 42), f"C_max = {res.c_max}", fill="#dc2626", font=font, anchor="mm")

        img.save(filepath, format="PNG")


# =============================================================================
# 5. Main Desktop Application (Tkinter / ttk)
# =============================================================================


class JacksonSchedulerApp(tk.Tk):
    """Desktop interface for J2 || C_max scheduling experiments."""

    def __init__(self, solver: SolverBridge) -> None:
        super().__init__()
        self.solver = solver
        self.title("Задача Джексона (J2 || C_max) — C++17 (mt19937_64), Файлы и Диаграмма Гантта")
        self.geometry("1220x860")
        self.minsize(980, 720)
        self.configure(bg="#f8fafc")

        self._last_result: Optional[ScheduleResult] = None
        self._last_edited_n: str = "n1"
        self._help_visible: bool = False

        self._init_variables()
        self._build_ui()
        self._update_formula_note()
        self._load_preset_1()

    def _init_variables(self) -> None:
        self.var_n_total = tk.StringVar(value="6")
        self.var_n1 = tk.StringVar(value="5")
        self.var_n2 = tk.StringVar(value="1")
        self.var_n3 = tk.StringVar(value="0")
        self.var_n4 = tk.StringVar(value="0")
        self.var_det_mode = tk.StringVar(value=DURATION_MODES[4][1])
        self.var_method = tk.StringVar(value=INTERVAL_METHODS[0][1])
        self.var_range = tk.StringVar(value=RANGE_PRESETS[0][0])
        self.var_beta = tk.StringVar(value="0.3")
        self.var_alpha = tk.StringVar(value="1.5")
        self.var_seed = tk.StringVar(value="0")
        self.var_seed_badge = tk.StringVar(value="mt19937_64")
        self.var_formula_note = tk.StringVar(value="")

        self.var_t1 = tk.StringVar()
        self.var_pi1 = tk.StringVar()
        self.var_t2 = tk.StringVar()
        self.var_pi2 = tk.StringVar()
        self.var_t3 = tk.StringVar()
        self.var_t4 = tk.StringVar()

        self.var_shift_right = tk.BooleanVar(value=False)
        self.var_macro_view = tk.BooleanVar(value=False)
        self.var_stats = tk.StringVar(value="")

    def _build_ui(self) -> None:
        style = ttk.Style(self)
        if "clam" in style.theme_names():
            style.theme_use("clam")

        # Header bar
        header = tk.Frame(self, bg="#1e293b", padx=14, pady=10)
        header.pack(fill="x")

        title_frame = tk.Frame(header, bg="#1e293b")
        title_frame.pack(side="left")
        tk.Label(
            title_frame,
            text="Задача Джексона (J2 || C_max) — C++17 (mt19937_64)",
            bg="#1e293b", fg="#ffffff", font=("Segoe UI", 12, "bold"),
        ).pack(anchor="w")
        tk.Label(
            title_frame,
            text="Генератор детерминированных и интервальных примеров, работа с .txt, диаграмма Гантта",
            bg="#1e293b", fg="#cbd5e1", font=("Segoe UI", 9),
        ).pack(anchor="w")

        presets_frame = tk.Frame(header, bg="#1e293b")
        presets_frame.pack(side="right")
        ttk.Button(presets_frame, text="Пример 1: Все 4 типа", command=self._load_preset_1).pack(side="left", padx=4)
        ttk.Button(presets_frame, text="Пример 2: Конвейер", command=self._load_preset_2).pack(side="left", padx=4)

        main = ttk.Frame(self, padding=10)
        main.pack(fill="both", expand=True)

        self._build_generator_section(main)
        self._build_input_section(main)
        self._build_metrics_section(main)
        self._build_gantt_section(main)

    def _build_generator_section(self, parent: ttk.Frame) -> None:
        frame = ttk.LabelFrame(
            parent,
            text=" 1. Генератор примеров (C++ mt19937_64, целые числа ≥ 1) ",
            padding=8,
        )
        frame.pack(fill="x", pady=(0, 8))

        row1 = ttk.Frame(frame)
        row1.pack(fill="x")

        def add_num_field(container: ttk.Frame, label: str, var: tk.StringVar, width: int = 6, tag: Optional[str] = None) -> None:
            col = ttk.Frame(container)
            col.pack(side="left", padx=(0, 8))
            ttk.Label(col, text=label, font=("Segoe UI", 8, "bold")).pack(anchor="w")
            ent = ttk.Entry(col, textvariable=var, width=width, font=("Consolas", 9))
            ent.pack(anchor="w")
            if tag is not None:
                ent.bind("<KeyRelease>", lambda _e, t=tag: setattr(self, "_last_edited_n", t))

        add_num_field(row1, "n (n₁+n₂):", self.var_n_total, 7)
        add_num_field(row1, "n₁ (Тип 1):", self.var_n1, 6, tag="n1")
        add_num_field(row1, "n₂ (Тип 2):", self.var_n2, 6, tag="n2")
        add_num_field(row1, "n₃ (Тип 3):", self.var_n3, 6)
        add_num_field(row1, "n₄ (Тип 4):", self.var_n4, 6)

        col_mode = ttk.Frame(row1)
        col_mode.pack(side="left", padx=(0, 8))
        ttk.Label(col_mode, text="Режим длительности:", font=("Segoe UI", 8, "bold")).pack(anchor="w")
        cb_mode = ttk.Combobox(
            col_mode,
            textvariable=self.var_det_mode,
            values=[label for _, label in DURATION_MODES],
            state="readonly",
            width=26,
        )
        cb_mode.pack(anchor="w")
        cb_mode.bind("<<ComboboxSelected>>", lambda _e: self._update_formula_note())

        col_meth = ttk.Frame(row1)
        col_meth.pack(side="left", padx=(0, 8))
        ttk.Label(col_meth, text="Метод интервалов (1..4):", font=("Segoe UI", 8, "bold")).pack(anchor="w")
        self.cb_method = ttk.Combobox(
            col_meth,
            textvariable=self.var_method,
            values=[label for _, label in INTERVAL_METHODS],
            state="readonly",
            width=27,
        )
        self.cb_method.pack(anchor="w")
        self.cb_method.bind("<<ComboboxSelected>>", lambda _e: self._update_formula_note())

        col_rng = ttk.Frame(row1)
        col_rng.pack(side="left", padx=(0, 8))
        ttk.Label(col_rng, text="Диапазон:", font=("Segoe UI", 8, "bold")).pack(anchor="w")
        cb_rng = ttk.Combobox(
            col_rng,
            textvariable=self.var_range,
            values=[label for label, _ in RANGE_PRESETS],
            state="readonly",
            width=11,
        )
        cb_rng.pack(anchor="w")
        cb_rng.bind("<<ComboboxSelected>>", lambda _e: self._update_formula_note())

        add_num_field(row1, "β:", self.var_beta, 5)
        add_num_field(row1, "α:", self.var_alpha, 5)

        row2 = ttk.Frame(frame)
        row2.pack(fill="x", pady=(8, 0))

        ttk.Label(row2, text="Seed (0=случ.):", font=("Segoe UI", 8, "bold")).pack(side="left")
        ttk.Entry(row2, textvariable=self.var_seed, width=12, font=("Consolas", 9)).pack(side="left", padx=(4, 8))
        ttk.Label(
            row2, textvariable=self.var_seed_badge,
            font=("Consolas", 9, "bold"), foreground="#0f766e",
        ).pack(side="left", padx=(0, 10))

        ttk.Label(
            row2, textvariable=self.var_formula_note,
            font=("Consolas", 8), foreground="#065f46",
        ).pack(side="left", fill="x", expand=True)

        ttk.Button(
            row2, text="Сгенерировать работы + π", command=self._on_generate
        ).pack(side="right", padx=(8, 0))

        # Collapsible help bar
        help_bar = ttk.Frame(frame)
        help_bar.pack(fill="x", pady=(6, 0))
        self.btn_toggle_help = ttk.Button(
            help_bar, text="▶ Справка: Seed, авторасчёт n = n₁ + n₂ и формат .txt",
            command=self._toggle_help,
        )
        self.btn_toggle_help.pack(anchor="w")

        self.help_panel = tk.Label(
            frame,
            justify="left",
            anchor="w",
            bg="#f1f5f9",
            fg="#1e293b",
            font=("Segoe UI", 8),
            padx=10,
            pady=6,
            relief="solid",
            bd=1,
            text=(
                "• Seed: задаёт зерно 64-битного Вихря Мерсенна (mt19937_64). При одинаковом Seed > 0 "
                "генерация 100% воспроизводима в C++, Python и веб-версии (0 = случайное зерно).\n"
                "• Авторасчёт n = n₁ + n₂: укажите общее n и одно из полей (n₁ или n₂), оставив второе пустым.\n"
                "• Формат .txt: блоки разделяются пустой строкой (без комментариев и заголовков). "
                "Пары можно писать в столбик или в строчку.\n"
                "  Порядок блоков: 1) Тип 1 (a_j b_j); 2) π₁; 3) Тип 2 (c_j d_j); 4) π₂; "
                "5) Тип 3 (p_j, опц.); 6) Тип 4 (q_j, опц.). Пропуск блока — символ «-»."
            ),
        )

    def _build_input_section(self, parent: ttk.Frame) -> None:
        frame = ttk.LabelFrame(
            parent, text=" 2. Входные данные (длительности и перестановки) ", padding=8
        )
        frame.pack(fill="x", pady=(0, 8))

        toolbar = ttk.Frame(frame)
        toolbar.pack(fill="x", pady=(0, 6))
        ttk.Button(toolbar, text="Загрузить из .txt", command=self._on_load_txt).pack(side="left", padx=(0, 6))
        ttk.Button(toolbar, text="Сохранить в .txt", command=self._on_save_txt).pack(side="left", padx=(0, 6))
        ttk.Button(toolbar, text="Очистить поля", command=self._on_clear_fields).pack(side="left")

        grid = ttk.Frame(frame)
        grid.pack(fill="x")
        grid.columnconfigure(0, weight=1)
        grid.columnconfigure(1, weight=1)

        def add_entry(row: int, col: int, label: str, var: tk.StringVar) -> None:
            box = ttk.Frame(grid)
            box.grid(row=row, column=col, sticky="ew", padx=(0 if col == 0 else 8, 0), pady=2)
            ttk.Label(box, text=label, font=("Segoe UI", 8)).pack(anchor="w")
            ttk.Entry(box, textvariable=var, font=("Consolas", 9)).pack(fill="x")

        add_entry(0, 0, "Тип 1 (M1 → M2, пары a_j b_j через запятую):", self.var_t1)
        add_entry(1, 0, "Перестановка π₁ (номера с 1 через пробел):", self.var_pi1)
        add_entry(2, 0, "Тип 3 (длительности p_j только на M1):", self.var_t3)

        add_entry(0, 1, "Тип 2 (M2 → M1, пары c_j d_j через запятую):", self.var_t2)
        add_entry(1, 1, "Перестановка π₂ (номера с 1 через пробел):", self.var_pi2)
        add_entry(2, 1, "Тип 4 (длительности q_j только на M2):", self.var_t4)

        actions = ttk.Frame(frame)
        actions.pack(fill="x", pady=(8, 0))

        ttk.Button(actions, text="Случайная (π₁, π₂)", command=self._on_random_perm).pack(side="left", padx=(0, 6))
        ttk.Button(actions, text="Оптимум (Беллман — Джонсон)", command=lambda: self._solve("opt")).pack(side="left", padx=(0, 6))
        ttk.Button(actions, text="Антиоптимум (Разворот)", command=lambda: self._solve("anti")).pack(side="left", padx=(0, 6))
        ttk.Button(actions, text="Рассчитать по заданным π₁, π₂", command=lambda: self._solve("manual")).pack(side="left", padx=(0, 6))

        ttk.Checkbutton(
            actions, text="Макро-вид", variable=self.var_macro_view, command=self._refresh_gantt
        ).pack(side="right", padx=(8, 0))
        ttk.Checkbutton(
            actions, text="Сдвиг вторых операций вправо", variable=self.var_shift_right, command=self._refresh_gantt
        ).pack(side="right")

    def _build_metrics_section(self, parent: ttk.Frame) -> None:
        box = tk.Frame(parent, bg="#e2e8f0", bd=1, relief="solid", padx=10, pady=6)
        box.pack(fill="x", pady=(0, 8))
        tk.Label(
            box,
            textvariable=self.var_stats,
            bg="#e2e8f0",
            fg="#0f172a",
            font=("Consolas", 9),
            justify="left",
            anchor="w",
        ).pack(fill="x")

    def _build_gantt_section(self, parent: ttk.Frame) -> None:
        frame = ttk.LabelFrame(parent, text=" Диаграмма Гантта (Г. Гантт, 1903) ", padding=8)
        frame.pack(fill="both", expand=True)

        top = ttk.Frame(frame)
        top.pack(fill="x", pady=(0, 6))

        export_label = "Сохранить график (PNG / SVG)" if HAS_PILLOW else "Сохранить график (SVG)"
        ttk.Button(top, text=export_label, command=self._on_export_chart).pack(side="left")

        legend = ttk.Frame(top)
        legend.pack(side="right")
        legend_items = [
            ("#3b82f6", "Тип 1 (M1→M2)"),
            ("#10b981", "Тип 2 (M2→M1)"),
            ("#f59e0b", "Тип 3 (M1)"),
            ("#8b5cf6", "Тип 4 (M2)"),
            ("#fca5a5", "Простой"),
        ]
        for color, caption in legend_items:
            tk.Label(legend, bg=color, width=2, height=1).pack(side="left", padx=(8, 3))
            ttk.Label(legend, text=caption, font=("Segoe UI", 8)).pack(side="left")

        self.gantt = GanttView(frame)
        self.gantt.pack(fill="both", expand=True)

    # -------------------------------------------------------------------------
    # Event Handlers & Helpers
    # -------------------------------------------------------------------------

    def _toggle_help(self) -> None:
        self._help_visible = not self._help_visible
        if self._help_visible:
            self.help_panel.pack(fill="x", pady=(4, 0))
            self.btn_toggle_help.config(
                text="▼ Справка: Seed, авторасчёт n = n₁ + n₂ и формат .txt"
            )
        else:
            self.help_panel.pack_forget()
            self.btn_toggle_help.config(
                text="▶ Справка: Seed, авторасчёт n = n₁ + n₂ и формат .txt"
            )

    def _selected_mode_key(self) -> str:
        label = self.var_det_mode.get()
        for key, text in DURATION_MODES:
            if text == label:
                return key
        return "random"

    def _selected_method_id(self) -> int:
        label = self.var_method.get()
        for mid, text in INTERVAL_METHODS:
            if text == label:
                return mid
        return 1

    def _selected_range_bounds(self) -> Tuple[float, float]:
        label = self.var_range.get()
        for text, bounds in RANGE_PRESETS:
            if text == label:
                return bounds
        return (20.0, 1000.0)

    def _update_formula_note(self) -> None:
        mode = self._selected_mode_key()
        method = self._selected_method_id()
        min_v, max_v = self._selected_range_bounds()
        self.cb_method.config(state="disabled" if mode == "pairs" else "readonly")

        if mode == "pairs":
            self.var_formula_note.set(
                f"Простые пары: op1, op2 = round(U[{min_v:g}, {max_v:g}])"
            )
            return

        mode_expr = {
            "lower": "op = round(L)",
            "upper": "op = round(U)",
            "mid": "op = round((L + U) / 2)",
            "random": "op = round(U[L, U])",
        }[mode]
        iv_expr = {
            1: f"x1,x2 ~ U[{min_v:g},{max_v:g}], L=min, U=max",
            2: f"L ~ U[{min_v:g},{max_v:g}], U ~ U[L+β({max_v:g}-L), {max_v:g}]",
            3: f"L ~ U[{min_v:g},{max_v:g}], U ~ U[min(L+100β,{max_v:g}), min(L+100,{max_v:g})]",
            4: f"L ~ U[{min_v:g},{max_v:g}], U ~ U[min(L+βL(α-1),{max_v:g}), min(αL,{max_v:g})]",
        }[method]
        self.var_formula_note.set(f"Метод {method}: {iv_expr}; {mode_expr}")

    def _resolve_n_counts(self) -> Tuple[int, int]:
        def parse_opt(val: str, name: str) -> Optional[int]:
            s = val.strip()
            if not s:
                return None
            if not s.isdigit():
                raise ValueError(f"Поле {name} должно быть неотрицательным целым числом.")
            return int(s)

        n = parse_opt(self.var_n_total.get(), "n")
        n1 = parse_opt(self.var_n1.get(), "n₁")
        n2 = parse_opt(self.var_n2.get(), "n₂")

        if n is not None and n1 is not None and n2 is None:
            if n1 > n:
                raise ValueError(f"n₁ ({n1}) не может превышать n ({n}).")
            n2 = n - n1
        elif n is not None and n2 is not None and n1 is None:
            if n2 > n:
                raise ValueError(f"n₂ ({n2}) не может превышать n ({n}).")
            n1 = n - n2
        elif n is None and n1 is not None and n2 is not None:
            n = n1 + n2
        elif n is None and n1 is not None and n2 is None:
            n, n2 = n1, 0
        elif n is None and n2 is not None and n1 is None:
            n, n1 = n2, 0
        elif n is not None and n1 is None and n2 is None:
            n1 = (n + 1) // 2
            n2 = n - n1
        elif n is not None and n1 is not None and n2 is not None:
            if n1 + n2 != n:
                if self._last_edited_n == "n1" and n1 <= n:
                    n2 = n - n1
                elif self._last_edited_n == "n2" and n2 <= n:
                    n1 = n - n2
                else:
                    raise ValueError(f"Сумма n₁ ({n1}) + n₂ ({n2}) должна равняться n ({n}).")
        else:
            raise ValueError("Укажите количество требований n, n₁ или n₂.")

        self.var_n_total.set(str(n))
        self.var_n1.set(str(n1))
        self.var_n2.set(str(n2))
        return n1, n2

    def _get_fields(self) -> ProblemFields:
        return ProblemFields(
            t1=self.var_t1.get(),
            pi1=self.var_pi1.get(),
            t2=self.var_t2.get(),
            pi2=self.var_pi2.get(),
            t3=self.var_t3.get(),
            t4=self.var_t4.get(),
        )

    def _set_fields(self, fields: ProblemFields) -> None:
        self.var_t1.set(fields.t1)
        self.var_pi1.set(fields.pi1)
        self.var_t2.set(fields.t2)
        self.var_pi2.set(fields.pi2)
        self.var_t3.set(fields.t3)
        self.var_t4.set(fields.t4)

    def _on_generate(self) -> None:
        try:
            n1, n2 = self._resolve_n_counts()
            n3 = int(self.var_n3.get().strip() or "0")
            n4 = int(self.var_n4.get().strip() or "0")
            min_v, max_v = self._selected_range_bounds()
            beta = float(self.var_beta.get().strip() or "0.3")
            alpha = float(self.var_alpha.get().strip() or "1.5")
            seed = int(self.var_seed.get().strip() or "0")
            if n3 < 0 or n4 < 0 or seed < 0:
                raise ValueError("Параметры n₃, n₄ и Seed должны быть неотрицательными.")

            params = GeneratorParams(
                n1=n1, n2=n2, n3=n3, n4=n4,
                mode=self._selected_mode_key(),
                method=self._selected_method_id(),
                min_val=min_v, max_val=max_v,
                beta=beta, alpha=alpha, seed=seed,
            )
            fields, actual_seed = self.solver.generate(params)
            self.var_seed_badge.set(f"Seed: {actual_seed}")
            self._set_fields(fields)
            self._solve("manual")
        except (ValueError, SolverError) as exc:
            messagebox.showerror("Ошибка генерации", str(exc))

    def _on_random_perm(self) -> None:
        try:
            t1 = parse_two_stage_entry(self.var_t1.get(), "Тип 1")
            t2 = parse_two_stage_entry(self.var_t2.get(), "Тип 2")
            p1 = list(range(1, len(t1) + 1))
            p2 = list(range(1, len(t2) + 1))
            random.shuffle(p1)
            random.shuffle(p2)
            self.var_pi1.set(" ".join(str(x) for x in p1))
            self.var_pi2.set(" ".join(str(x) for x in p2))
            self._solve("manual")
        except ValueError as exc:
            messagebox.showerror("Ошибка ввода", str(exc))

    def _solve(self, perm_mode: str) -> None:
        try:
            payload = serialize_to_txt(self._get_fields())
            result = self.solver.evaluate(payload, perm_mode=perm_mode)
            if perm_mode in ("opt", "anti"):
                self.var_pi1.set(" ".join(str(x) for x in result.perm1))
                self.var_pi2.set(" ".join(str(x) for x in result.perm2))
            self._last_result = result
            self._update_stats(result)
            self._refresh_gantt()
        except (ValueError, SolverError) as exc:
            messagebox.showerror("Ошибка расчёта", str(exc))

    def _update_stats(self, res: ScheduleResult) -> None:
        m1, m2 = res.m1, res.m2
        self.var_stats.set(
            f"ТЕКУЩЕЕ C_max: {res.c_max}  |  Оптимальное C_max (Беллман — Джонсон): {res.opt_c_max}  |  "
            f"Антиоптимальное C_max (Разворот): {res.anti_c_max}\n"
            f"Прибор 1 (M1):  Стартовый блок T₁ = {m1.initial_block_load}  |  Полезная работа = {m1.total_work_time}  |  "
            f"Завершение C¹ = {m1.completion_time}  |  Внутр. простой = {m1.internal_idle_time}  |  Полный простой = {m1.total_idle_time}\n"
            f"Прибор 2 (M2):  Стартовый блок T₂ = {m2.initial_block_load}  |  Полезная работа = {m2.total_work_time}  |  "
            f"Завершение C² = {m2.completion_time}  |  Внутр. простой = {m2.internal_idle_time}  |  Полный простой = {m2.total_idle_time}"
        )

    def _refresh_gantt(self) -> None:
        self.gantt.set_data(
            self._last_result,
            shift_right=self.var_shift_right.get(),
            macro_view=self.var_macro_view.get(),
        )

    def _on_load_txt(self) -> None:
        path = filedialog.askopenfilename(
            title="Загрузить входные данные (.txt)",
            filetypes=[("Текстовые файлы", "*.txt"), ("Все файлы", "*.*")],
        )
        if not path:
            return
        try:
            raw = Path(path).read_text(encoding="utf-8")
            fields = parse_txt_content(raw)
            self._set_fields(fields)
            self._solve("manual")
        except (OSError, ValueError, SolverError) as exc:
            messagebox.showerror("Ошибка чтения файла", str(exc))

    def _on_save_txt(self) -> None:
        try:
            payload = serialize_to_txt(self._get_fields())
            t1 = parse_two_stage_entry(self.var_t1.get(), "Тип 1")
            t2 = parse_two_stage_entry(self.var_t2.get(), "Тип 2")
        except ValueError as exc:
            messagebox.showerror("Ошибка данных", str(exc))
            return

        path = filedialog.asksaveasfilename(
            title="Сохранить входные данные (.txt)",
            defaultextension=".txt",
            initialfile=f"jackson_n1-{len(t1)}_n2-{len(t2)}.txt",
            filetypes=[("Текстовые файлы", "*.txt")],
        )
        if not path:
            return
        try:
            Path(path).write_text(payload, encoding="utf-8")
        except OSError as exc:
            messagebox.showerror("Ошибка сохранения", str(exc))

    def _on_clear_fields(self) -> None:
        self._set_fields(ProblemFields("", "", "", "", "", ""))
        self._last_result = None
        self.var_stats.set(
            "Поля очищены. Введите данные вручную, сгенерируйте пример или загрузите .txt файл."
        )
        self.gantt.clear()

    def _on_export_chart(self) -> None:
        if self._last_result is None:
            messagebox.showinfo("Экспорт", "Сначала выполните расчёт расписания.")
            return

        filetypes = [("SVG векторная графика", "*.svg")]
        default_ext = ".svg"
        if HAS_PILLOW:
            filetypes.insert(0, ("PNG изображение", "*.png"))
            default_ext = ".png"

        path = filedialog.asksaveasfilename(
            title="Сохранить диаграмму Гантта",
            defaultextension=default_ext,
            initialfile=f"gantt_Cmax-{self._last_result.c_max}{default_ext}",
            filetypes=filetypes,
        )
        if not path:
            return

        target = Path(path)
        try:
            if target.suffix.lower() == ".png":
                self.gantt.export_png(target)
            else:
                self.gantt.export_svg(target)
        except (OSError, RuntimeError, ValueError) as exc:
            messagebox.showerror("Ошибка экспорта", str(exc))

    def _load_preset_1(self) -> None:
        self.var_shift_right.set(False)
        self._set_fields(
            ProblemFields("3 6, 5 2, 4 5", "1 2 3", "4 5, 8 3, 2 6", "1 2 3", "3 2", "4")
        )
        self._solve("manual")

    def _load_preset_2(self) -> None:
        self.var_shift_right.set(True)
        self._set_fields(ProblemFields("4 3, 6 2, 5 4", "1 3 2", "", "", "", ""))
        self._solve("opt")


def main() -> int:
    """Application entry point."""
    bridge = SolverBridge()
    app = JacksonSchedulerApp(bridge)
    app.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())