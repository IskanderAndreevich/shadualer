import os
import sys
import subprocess
import tkinter as tk
from tkinter import ttk, messagebox

def get_solver_path():
    """Находит скомпилированный C++ модуль (и при обычном запуске, и внутри .exe)"""
    base_dir = getattr(sys, '_MEIPASS', os.path.dirname(os.path.abspath(__file__)))
    exe_name = "solver.exe" if os.name == "nt" else "solver"
    return os.path.join(base_dir, exe_name)

class JacksonSchedulerApp:
    COLORS = {
        1: ("#3b82f6", "#93c5fd", "Тип 1 (M1 → M2)"),
        2: ("#10b981", "#6ee7b7", "Тип 2 (M2 → M1)"),
        3: ("#f59e0b", "#fcd34d", "Тип 3 (только M1)"),
        4: ("#8b5cf6", "#c4b5fd", "Тип 4 (только M2)"),
    }

    def __init__(self, root):
        self.root = root
        self.root.title("Расписание Джексона (J2 || C_max) — C++ Core & Диаграмма Гантта")
        self.root.geometry("1080x720")
        self.root.configure(bg="#f8fafc")

        self.shift_right_var = tk.BooleanVar(value=False)
        self._build_ui()
        self.load_preset_1()

    def _build_ui(self):
        # Верхняя панель с пресетами и кнопкой расчёта
        top = tk.Frame(self.root, bg="#1e293b", pady=8, padx=12)
        top.pack(fill=tk.X)

        tk.Label(top, text="Задача Джексона (2 прибора, 4 типа требований)", fg="white",
                 bg="#1e293b", font=("Segoe UI", 12, "bold")).pack(side=tk.LEFT)

        tk.Button(top, text="Пример 1: Все 4 типа", command=self.load_preset_1,
                  bg="#334155", fg="white", relief=tk.FLAT, padx=8).pack(side=tk.RIGHT, padx=4)
        tk.Button(top, text="Пример 2: Чистый Тип 1 (Окно слева)", command=self.load_preset_2,
                  bg="#334155", fg="white", relief=tk.FLAT, padx=8).pack(side=tk.RIGHT, padx=4)

        # Панель ввода данных
        input_frame = tk.LabelFrame(self.root, text=" Входные данные (длительности и перестановки) ",
                                    bg="#f8fafc", font=("Segoe UI", 10, "bold"), padx=10, pady=8)
        input_frame.pack(fill=tk.X, padx=12, pady=8)

        # Сетка полей ввода
        labels = [
            ("Требования Типа 1 (пары a_j b_j через запятую):", 0, 0),
            ("Перестановка π₁ (номера с 1 через пробел):", 1, 0),
            ("Требования Типа 2 (пары c_j d_j через запятую):", 0, 2),
            ("Перестановка π₂ (номера с 1 через пробел):", 1, 2),
            ("Требования Типа 3 (p_j на M1 через пробел):", 2, 0),
            ("Требования Типа 4 (q_j на M2 через пробел):", 2, 2),
        ]
        for text, r, c in labels:
            tk.Label(input_frame, text=text, bg="#f8fafc", font=("Segoe UI", 9)).grid(row=r, column=c, sticky="w", padx=4, pady=3)

        self.ent_t1 = tk.Entry(input_frame, width=32, font=("Consolas", 10))
        self.ent_pi1 = tk.Entry(input_frame, width=32, font=("Consolas", 10))
        self.ent_t2 = tk.Entry(input_frame, width=32, font=("Consolas", 10))
        self.ent_pi2 = tk.Entry(input_frame, width=32, font=("Consolas", 10))
        self.ent_t3 = tk.Entry(input_frame, width=32, font=("Consolas", 10))
        self.ent_t4 = tk.Entry(input_frame, width=32, font=("Consolas", 10))

        self.ent_t1.grid(row=0, column=1, padx=6, pady=3)
        self.ent_pi1.grid(row=1, column=1, padx=6, pady=3)
        self.ent_t2.grid(row=0, column=3, padx=6, pady=3)
        self.ent_pi2.grid(row=1, column=3, padx=6, pady=3)
        self.ent_t3.grid(row=2, column=1, padx=6, pady=3)
        self.ent_t4.grid(row=2, column=3, padx=6, pady=3)

        # Панель управления
        ctrl_frame = tk.Frame(input_frame, bg="#f8fafc")
        ctrl_frame.grid(row=3, column=0, columnspan=4, sticky="ew", pady=(8, 2))

        tk.Button(ctrl_frame, text="▶ Рассчитать в C++ и построить диаграмму Гантта",
                  command=self.solve_and_draw, bg="#2563eb", fg="white",
                  font=("Segoe UI", 10, "bold"), padx=14, pady=4, relief=tk.FLAT).pack(side=tk.LEFT)

        tk.Checkbutton(ctrl_frame, text="Сдвинуть вторые операции вправо (консолидировать окно простоя слева)",
                       variable=self.shift_right_var, command=self.solve_and_draw,
                       bg="#f8fafc", font=("Segoe UI", 9, "bold")).pack(side=tk.LEFT, padx=16)

        # Панель метрик
        self.stats_label = tk.Label(self.root, text="", bg="#e2e8f0", fg="#0f172a",
                                    font=("Consolas", 10), justify=tk.LEFT, padx=12, pady=8, anchor="w")
        self.stats_label.pack(fill=tk.X, padx=12, pady=4)

        # Холст для диаграммы Гантта
        canvas_frame = tk.LabelFrame(self.root, text=" Диаграмма Гантта (Г. Гантт, 1903) ",
                                     bg="white", font=("Segoe UI", 10, "bold"))
        canvas_frame.pack(fill=tk.BOTH, expand=True, padx=12, pady=8)

        self.canvas = tk.Canvas(canvas_frame, bg="white", highlightthickness=0)
        self.canvas.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)
        self.canvas.bind("<Configure>", lambda e: self.redraw_cached())
        self.last_result = None

    def load_preset_1(self):
        self._set_entries("3 6, 5 2, 4 5", "1 2 3", "4 5, 8 3, 2 6", "1 2 3", "3 2", "4")
        self.shift_right_var.set(False)
        self.solve_and_draw()

    def load_preset_2(self):
        self._set_entries("4 3, 6 2, 5 4", "1 2 3", "", "", "", "")
        self.shift_right_var.set(True)
        self.solve_and_draw()

    def _set_entries(self, t1, pi1, t2, pi2, t3, t4):
        for ent, val in [(self.ent_t1, t1), (self.ent_pi1, pi1),
                         (self.ent_t2, t2), (self.ent_pi2, pi2),
                         (self.ent_t3, t3), (self.ent_t4, t4)]:
            ent.delete(0, tk.END)
            ent.insert(0, val)

    def _parse_two_stage(self, text):
        if not text.strip():
            return []
        jobs = []
        for pair in text.split(","):
            parts = pair.strip().split()
            if len(parts) != 2:
                raise ValueError(f"Ожидалась пара чисел (op1 op2), получено: '{pair}'")
            jobs.append((float(parts[0]), float(parts[1])))
        return jobs

    def _parse_floats(self, text):
        return [float(x) for x in text.strip().split()] if text.strip() else []

    def _parse_ints(self, text, n, name):
        if n == 0:
            return []
        nums = [int(x) for x in text.strip().split()]
        if sorted(nums) != list(range(1, n + 1)):
            raise ValueError(f"Перестановка {name} должна содержать числа от 1 до {n} без повторений.")
        return nums

    def solve_and_draw(self):
        try:
            t1 = self._parse_two_stage(self.ent_t1.get())
            pi1 = self._parse_ints(self.ent_pi1.get(), len(t1), "π₁")
            t2 = self._parse_two_stage(self.ent_t2.get())
            pi2 = self._parse_ints(self.ent_pi2.get(), len(t2), "π₂")
            t3 = self._parse_floats(self.ent_t3.get())
            t4 = self._parse_floats(self.ent_t4.get())
        except Exception as e:
            messagebox.showerror("Ошибка ввода", str(e))
            return

        # Формируем входной поток для C++ ядра
        lines = [f"{len(t1)} {len(t2)} {len(t3)} {len(t4)}"]
        lines += [f"{a} {b}" for a, b in t1]
        if pi1: lines.append(" ".join(map(str, pi1)))
        lines += [f"{c} {d}" for c, d in t2]
        if pi2: lines.append(" ".join(map(str, pi2)))
        if t3: lines.append(" ".join(map(str, t3)))
        if t4: lines.append(" ".join(map(str, t4)))
        stdin_payload = "\n".join(lines) + "\n"

        solver_bin = get_solver_path()
        if not os.path.exists(solver_bin):
            messagebox.showerror(
                "Не найден модуль C++",
                f"Не найден файл:\n{solver_bin}\n\nСначала скомпилируйте solver.cpp:\ng++ -O3 solver.cpp -o solver.exe"
            )
            return

        try:
            # На Windows отключаем мигание консольного окна при вызове solver.exe из GUI (--noconsole)
            creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
            proc = subprocess.run(
                [solver_bin],
                input=stdin_payload,
                text=True,
                capture_output=True,
                check=True,
                creationflags=creationflags,
            )
            self.last_result = self._parse_solver_output(proc.stdout)
            self.redraw_cached()
        except Exception as e:
            messagebox.showerror("Ошибка выполнения C++ модуля", str(e))

    def _parse_solver_output(self, out_text):
        lines = [line.strip().split() for line in out_text.strip().splitlines() if line.strip()]
        c_max = float(lines[0][1])
        m1 = [float(x) for x in lines[1][1:]] # T1, work1, C1, idle_in1, idle_tot1
        m2 = [float(x) for x in lines[2][1:]] # T2, work2, C2, idle_in2, idle_tot2
        num_blocks = int(lines[3][1])
        blocks = []
        for i in range(4, 4 + num_blocks):
            mach, jtype, jid, st, en = lines[i]
            blocks.append({
                "machine": int(mach),
                "type": int(jtype),
                "id": int(jid),
                "start": float(st),
                "end": float(en),
            })
        return {"c_max": c_max, "m1": m1, "m2": m2, "blocks": blocks}

    def _apply_right_shift(self, blocks, m_idx, T_load, C_end):
        """Сдвигает операции хвоста вплотную к C_end, собирая внутренние простои в единое окно слева"""
        tail_type = 2 if m_idx == 1 else 1
        head = [b.copy() for b in blocks if b["machine"] == m_idx and b["type"] != tail_type]
        tail = [b.copy() for b in blocks if b["machine"] == m_idx and b["type"] == tail_type]
        cursor = C_end
        for b in reversed(tail):
            dur = b["end"] - b["start"]
            b["end"] = cursor
            b["start"] = cursor - dur
            cursor -= dur
        return head + tail

    def redraw_cached(self):
        if not self.last_result:
            return
        res = self.last_result
        c_max = res["c_max"]
        m1, m2 = res["m1"], res["m2"]

        # Обновляем текстовую сводку
        self.stats_label.config(text=(
            f"ОБЩЕЕ ВРЕМЯ ЗАВЕРШЕНИЯ (C_max): {c_max:g}\n"
            f"Прибор 1 (M1):  Стартовый блок T₁ = {m1[0]:g}  |  Полезная работа = {m1[1]:g}  |  "
            f"Завершение C¹ = {m1[2]:g}  |  Внутр. простой = {m1[3]:g}  |  Полный простой = {m1[4]:g}\n"
            f"Прибор 2 (M2):  Стартовый блок T₂ = {m2[0]:g}  |  Полезная работа = {m2[1]:g}  |  "
            f"Завершение C² = {m2[2]:g}  |  Внутр. простой = {m2[3]:g}  |  Полный простой = {m2[4]:g}"
        ))

        self.canvas.delete("all")
        w = max(self.canvas.winfo_width(), 600)
        h = max(self.canvas.winfo_height(), 300)

        left_pad, right_pad = 125, 45
        usable_w = w - left_pad - right_pad
        scale = usable_w / max(c_max, 1.0)

        # Готовим блоки с учётом опции «Сдвиг вправо»
        if self.shift_right_var.get():
            b_m1 = self._apply_right_shift(res["blocks"], 1, m1[0], m1[2])
            b_m2 = self._apply_right_shift(res["blocks"], 2, m2[0], m2[2])
        else:
            b_m1 = [b for b in res["blocks"] if b["machine"] == 1]
            b_m2 = [b for b in res["blocks"] if b["machine"] == 2]

        y_m1, y_m2 = 65, 195
        bar_h = 56

        # Сетка времени
        step = 1 if c_max <= 20 else (2 if c_max <= 50 else 5)
        t = 0
        while t <= int(c_max) + 1:
            x = left_pad + t * scale
            self.canvas.create_line(x, 35, x, y_m2 + bar_h + 25, fill="#e2e8f0", dash=(2, 2))
            self.canvas.create_text(x, 22, text=f"{t:g}", fill="#64748b", font=("Segoe UI", 8))
            t += step

        # Рисуем две линии приборов
        self._draw_machine_track("Прибор 1 (M1)", y_m1, bar_h, b_m1, m1[0], c_max, left_pad, scale, "T₁")
        self._draw_machine_track("Прибор 2 (M2)", y_m2, bar_h, b_m2, m2[0], c_max, left_pad, scale, "T₂")

        # Финальная отсечка C_max
        x_cmax = left_pad + c_max * scale
        self.canvas.create_line(x_cmax, 30, x_cmax, y_m2 + bar_h + 30, fill="#dc2626", width=2, dash=(5, 3))
        self.canvas.create_text(x_cmax, y_m2 + bar_h + 42, text=f"C_max = {c_max:g}",
                                fill="#dc2626", font=("Segoe UI", 10, "bold"))

    def _draw_machine_track(self, title, y, h, blocks, T_val, c_max, left_pad, scale, t_name):
        # Название прибора слева
        self.canvas.create_text(14, y + h / 2, text=title, anchor="w",
                                font=("Segoe UI", 10, "bold"), fill="#1e293b")

        # Фон дорожки
        self.canvas.create_rectangle(left_pad, y, left_pad + c_max * scale, y + h,
                                     fill="#f8fafc", outline="#cbd5e1")

        # Вычисляем и рисуем интервалы простоя
        cursor = 0.0
        for b in blocks:
            if b["start"] > cursor + 1e-6:
                self._draw_idle_box(left_pad + cursor * scale, y, left_pad + b["start"] * scale, y + h,
                                    b["start"] - cursor, "Окно простоя")
            cursor = b["end"]
        if c_max > cursor + 1e-6:
            self._draw_idle_box(left_pad + cursor * scale, y, left_pad + c_max * scale, y + h,
                                c_max - cursor, "Простой")

        # Рисуем блоки операций
        for b in blocks:
            x1 = left_pad + b["start"] * scale
            x2 = left_pad + b["end"] * scale
            main_col, tail_col, _ = self.COLORS[b["type"]]
            # На своём первом приборе операция рисуется ярким цветом, в хвосте — более светлым
            is_tail = (b["machine"] == 1 and b["type"] == 2) or (b["machine"] == 2 and b["type"] == 1)
            fill_col = tail_col if is_tail else main_col
            text_col = "#0f172a" if is_tail else "white"

            self.canvas.create_rectangle(x1, y, x2, y + h, fill=fill_col, outline="#1e293b", width=1)
            op_name = {
                (1, 1): f"a_{b['id']}", (2, 1): f"b_{b['id']}",
                (2, 2): f"c_{b['id']}", (1, 2): f"d_{b['id']}",
                (1, 3): f"p_{b['id']}", (2, 4): f"q_{b['id']}",
            }[(b["machine"], b["type"])]
            dur = b["end"] - b["start"]
            if x2 - x1 > 26:
                self.canvas.create_text((x1 + x2) / 2, y + h / 2 - 7, text=op_name,
                                        fill=text_col, font=("Segoe UI", 9, "bold"))
                self.canvas.create_text((x1 + x2) / 2, y + h / 2 + 9, text=f"({dur:g})",
                                        fill=text_col, font=("Segoe UI", 8))

            # Отметка времени завершения блока снизу дорожки
            self.canvas.create_text(x2, y + h + 11, text=f"{b['end']:g}",
                                    fill="#334155", font=("Segoe UI", 8))

        # Отметка рубежа T1 или T2
        if T_val > 0:
            x_t = left_pad + T_val * scale
            self.canvas.create_line(x_t, y - 10, x_t, y + h + 10, fill="#0f172a", width=2, dash=(4, 2))
            self.canvas.create_text(x_t, y - 16, text=f"{t_name}={T_val:g}",
                                    fill="#0f172a", font=("Segoe UI", 8, "bold"))

    def _draw_idle_box(self, x1, y1, x2, y2, dur, label):
        self.canvas.create_rectangle(x1, y1, x2, y2, fill="#fee2e2", outline="#fca5a5", stipple="gray25")
        if x2 - x1 > 34:
            self.canvas.create_text((x1 + x2) / 2, (y1 + y2) / 2,
                                    text=f"{label}\n{dur:g}", fill="#b91c1c",
                                    font=("Segoe UI", 8, "italic"), justify=tk.CENTER)

if __name__ == "__main__":
    root = tk.Tk()
    app = JacksonSchedulerApp(root)
    root.mainloop()