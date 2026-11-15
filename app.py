import os
import sys
import subprocess
import tkinter as tk
from tkinter import ttk, messagebox


def get_solver_path():
    """Находит скомпилированный C++ модуль (и при обычном запуске, и внутри .exe)"""
    base_dir = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    exe_name = "solver.exe" if os.name == "nt" else "solver"
    return os.path.join(base_dir, exe_name)


class JacksonSchedulerApp:
    COLORS = {
        1: ("#3b82f6", "#93c5fd", "Тип 1 (M1 → M2)"),
        2: ("#10b981", "#6ee7b7", "Тип 2 (M2 → M1)"),
        3: ("#f59e0b", "#fcd34d", "Тип 3 (только M1)"),
        4: ("#8b5cf6", "#c4b5fd", "Тип 4 (только M2)"),
    }

    METHODS = [
        "Метод 1: Независимые длительности",
        "Метод 2: Зависимый верхний предел (от β)",
        "Метод 3: Интервал ширины ≤ 100 (от β)",
        "Метод 4: Мультипликативный интервал (от β, α)",
    ]

    METHOD_FORMULAS = {
        1: "Формула Метода 1:  op₁, op₂ ~ round(U[20, 1000]) — две независимые случайные величины в целых числах (β и α не используются).",
        2: "Формула Метода 2:  lower ~ U[20, 1000],   upper ~ U[lower + β·(1000 − lower), 1000],   op = round(U[lower, upper]).",
        3: "Формула Метода 3:  lower ~ U[20, 1000],   upper ~ U[min(lower + 100·β, 1000), min(lower + 100, 1000)],   op = round(U[lower, upper]).",
        4: "Формула Метода 4:  lower ~ U[20, 1000],   upper ~ U[min(lower + β·lower·(α − 1), 1000), min(α·lower, 1000)],   op = round(U[lower, upper]).",
    }

    def __init__(self, root):
        self.root = root
        self.root.title("Расписание Джексона (J2 || C_max) — C++ mt19937_64 (Целые числа) & Диаграмма Гантта")
        self.root.geometry("1200x840")
        self.root.configure(bg="#f8fafc")

        self.shift_right_var = tk.BooleanVar(value=False)
        self.last_result = None
        self.last_seed = 0
        self.last_edited_n = "n1"

        self._build_ui()
        self.load_preset_1()

    def _build_ui(self):
        # Верхняя панель с пресетами
        top = tk.Frame(self.root, bg="#1e293b", pady=8, padx=12)
        top.pack(fill=tk.X)

        tk.Label(
            top,
            text="Задача Джексона (J2 || C_max) — Положительные целые числа, Вихрь Мерсенна (std::mt19937_64)",
            fg="white",
            bg="#1e293b",
            font=("Segoe UI", 11, "bold"),
        ).pack(side=tk.LEFT)

        tk.Button(
            top,
            text="Пример 1: Все 4 типа",
            command=self.load_preset_1,
            bg="#334155",
            fg="white",
            relief=tk.FLAT,
            padx=8,
        ).pack(side=tk.RIGHT, padx=4)
        tk.Button(
            top,
            text="Пример 2: Конвейер (Окно слева)",
            command=self.load_preset_2,
            bg="#334155",
            fg="white",
            relief=tk.FLAT,
            padx=8,
        ).pack(side=tk.RIGHT, padx=4)

        # 1. Панель генератора интервалов (Вихрь Мерсенна std::mt19937_64 в C++)
        gen_frame = tk.LabelFrame(
            self.root,
            text=" 1. Генератор интервалов и длительностей (C++ Вихрь Мерсенна std::mt19937_64, целые числа ≥ 1) ",
            bg="#f8fafc",
            font=("Segoe UI", 10, "bold"),
            padx=10,
            pady=6,
        )
        gen_frame.pack(fill=tk.X, padx=12, pady=(8, 4))

        row0 = tk.Frame(gen_frame, bg="#f8fafc")
        row0.pack(fill=tk.X, pady=2)

        tk.Label(row0, text="n (общее = n₁+n₂):", bg="#f8fafc", font=("Segoe UI", 9, "bold")).pack(side=tk.LEFT)
        self.ent_n_total = tk.Entry(row0, width=5, font=("Consolas", 10))
        self.ent_n_total.insert(0, "6")
        self.ent_n_total.pack(side=tk.LEFT, padx=(4, 10))
        self.ent_n_total.bind("<KeyRelease>", lambda e: self._on_n_edited("n"))

        tk.Label(row0, text="n₁ (Тип 1, пусто=авто):", bg="#f8fafc", font=("Segoe UI", 9)).pack(side=tk.LEFT)
        self.ent_n1 = tk.Entry(row0, width=5, font=("Consolas", 10))
        self.ent_n1.insert(0, "5")
        self.ent_n1.pack(side=tk.LEFT, padx=(4, 10))
        self.ent_n1.bind("<KeyRelease>", lambda e: self._on_n_edited("n1"))

        tk.Label(row0, text="n₂ (Тип 2, пусто=авто):", bg="#f8fafc", font=("Segoe UI", 9)).pack(side=tk.LEFT)
        self.ent_n2 = tk.Entry(row0, width=5, font=("Consolas", 10))
        self.ent_n2.insert(0, "1")
        self.ent_n2.pack(side=tk.LEFT, padx=(4, 12))
        self.ent_n2.bind("<KeyRelease>", lambda e: self._on_n_edited("n2"))

        tk.Label(row0, text="n₃ (Тип 3):", bg="#f8fafc", font=("Segoe UI", 9)).pack(side=tk.LEFT)
        self.ent_n3 = tk.Entry(row0, width=4, font=("Consolas", 10))
        self.ent_n3.insert(0, "0")
        self.ent_n3.pack(side=tk.LEFT, padx=(4, 8))

        tk.Label(row0, text="n₄ (Тип 4):", bg="#f8fafc", font=("Segoe UI", 9)).pack(side=tk.LEFT)
        self.ent_n4 = tk.Entry(row0, width=4, font=("Consolas", 10))
        self.ent_n4.insert(0, "0")
        self.ent_n4.pack(side=tk.LEFT, padx=(4, 12))

        tk.Label(row0, text="Метод:", bg="#f8fafc", font=("Segoe UI", 9, "bold")).pack(side=tk.LEFT)
        self.cmb_method = ttk.Combobox(row0, values=self.METHODS, state="readonly", width=34)
        self.cmb_method.current(0)
        self.cmb_method.pack(side=tk.LEFT, padx=(4, 10))
        self.cmb_method.bind("<<ComboboxSelected>>", lambda e: self._update_method_formula())

        tk.Label(row0, text="β:", bg="#f8fafc", font=("Segoe UI", 9)).pack(side=tk.LEFT)
        self.ent_beta = tk.Entry(row0, width=5, font=("Consolas", 10))
        self.ent_beta.insert(0, "0.3")
        self.ent_beta.pack(side=tk.LEFT, padx=(3, 8))

        tk.Label(row0, text="α:", bg="#f8fafc", font=("Segoe UI", 9)).pack(side=tk.LEFT)
        self.ent_alpha = tk.Entry(row0, width=5, font=("Consolas", 10))
        self.ent_alpha.insert(0, "1.5")
        self.ent_alpha.pack(side=tk.LEFT, padx=(3, 8))

        tk.Label(row0, text="Seed (0=случ.):", bg="#f8fafc", font=("Segoe UI", 9)).pack(side=tk.LEFT)
        self.ent_seed = tk.Entry(row0, width=10, font=("Consolas", 10))
        self.ent_seed.insert(0, "0")
        self.ent_seed.pack(side=tk.LEFT, padx=(3, 8))

        tk.Button(
            row0,
            text="🎲 Сгенерировать (случ. π)",
            command=self.generate_via_cpp,
            bg="#0d9488",
            fg="white",
            font=("Segoe UI", 9, "bold"),
            padx=10,
            pady=3,
            relief=tk.FLAT,
        ).pack(side=tk.RIGHT, padx=2)

        # Пометка с формулой выбранного метода
        self.lbl_formula = tk.Label(
            gen_frame,
            text=self.METHOD_FORMULAS[1],
            bg="#ecfdf5",
            fg="#065f46",
            font=("Consolas", 9, "bold"),
            anchor="w",
            padx=8,
            pady=4,
            relief=tk.SOLID,
            bd=1,
        )
        self.lbl_formula.pack(fill=tk.X, pady=(6, 2))

        # Пометка о том, что такое Seed и как работает авторасчет n = n1 + n2
        seed_note_text = (
            "ℹ️ Что такое Seed (зерно генератора): начальное число для алгоритма Вихря Мерсенна (std::mt19937_64). "
            "Одинаковый Seed > 0 гарантирует 100% воспроизводимость всех сгенерированных длительностей и случайных перестановок; "
            "при Seed = 0 каждый раз создаётся новый случайный набор. "
            "Для количества работ выполняется n = n₁ + n₂: достаточно указать (n и n₁) или (n и n₂) — второй параметр рассчитается автоматически."
        )
        tk.Label(
            gen_frame,
            text=seed_note_text,
            bg="#f8fafc",
            fg="#475569",
            font=("Segoe UI", 8),
            anchor="w",
            justify=tk.LEFT,
            wraplength=1140,
        ).pack(fill=tk.X, pady=(2, 0))

        # 2. Панель ввода данных
        input_frame = tk.LabelFrame(
            self.root,
            text=" 2. Входные данные (положительные целые длительности и перестановки) ",
            bg="#f8fafc",
            font=("Segoe UI", 10, "bold"),
            padx=10,
            pady=6,
        )
        input_frame.pack(fill=tk.X, padx=12, pady=4)

        labels = [
            ("Требования Типа 1 (целые пары a_j b_j через запятую):", 0, 0),
            ("Перестановка π₁ (номера с 1 через пробел):", 1, 0),
            ("Требования Типа 2 (целые пары c_j d_j через запятую):", 0, 2),
            ("Перестановка π₂ (номера с 1 через пробел):", 1, 2),
            ("Требования Типа 3 (целые p_j на M1 через пробел):", 2, 0),
            ("Требования Типа 4 (целые q_j на M2 через пробел):", 2, 2),
        ]
        for text, r, c in labels:
            tk.Label(input_frame, text=text, bg="#f8fafc", font=("Segoe UI", 9)).grid(
                row=r, column=c, sticky="w", padx=4, pady=3
            )

        self.ent_t1 = tk.Entry(input_frame, width=38, font=("Consolas", 10))
        self.ent_pi1 = tk.Entry(input_frame, width=38, font=("Consolas", 10))
        self.ent_t2 = tk.Entry(input_frame, width=38, font=("Consolas", 10))
        self.ent_pi2 = tk.Entry(input_frame, width=38, font=("Consolas", 10))
        self.ent_t3 = tk.Entry(input_frame, width=38, font=("Consolas", 10))
        self.ent_t4 = tk.Entry(input_frame, width=38, font=("Consolas", 10))

        self.ent_t1.grid(row=0, column=1, padx=6, pady=3)
        self.ent_pi1.grid(row=1, column=1, padx=6, pady=3)
        self.ent_t2.grid(row=0, column=3, padx=6, pady=3)
        self.ent_pi2.grid(row=1, column=3, padx=6, pady=3)
        self.ent_t3.grid(row=2, column=1, padx=6, pady=3)
        self.ent_t4.grid(row=2, column=3, padx=6, pady=3)

        # Панель управления (Случайная π / Оптимум / Антиоптимум / Ручной расчет)
        ctrl_frame = tk.Frame(input_frame, bg="#f8fafc")
        ctrl_frame.grid(row=3, column=0, columnspan=4, sticky="ew", pady=(8, 2))

        tk.Button(
            ctrl_frame,
            text="🔀 Случайная перестановка (π₁, π₂)",
            command=lambda: self.solve_and_draw(mode="RANDOM"),
            bg="#7c3aed",
            fg="white",
            font=("Segoe UI", 9, "bold"),
            padx=10,
            pady=4,
            relief=tk.FLAT,
        ).pack(side=tk.LEFT, padx=(0, 6))

        tk.Button(
            ctrl_frame,
            text="★ Оптимум (Беллман — Джонсон)",
            command=lambda: self.solve_and_draw(mode="OPT"),
            bg="#16a34a",
            fg="white",
            font=("Segoe UI", 9, "bold"),
            padx=10,
            pady=4,
            relief=tk.FLAT,
        ).pack(side=tk.LEFT, padx=6)

        tk.Button(
            ctrl_frame,
            text="⚡ Антиоптимум (Разворот)",
            command=lambda: self.solve_and_draw(mode="ANTI"),
            bg="#dc2626",
            fg="white",
            font=("Segoe UI", 9, "bold"),
            padx=10,
            pady=4,
            relief=tk.FLAT,
        ).pack(side=tk.LEFT, padx=6)

        tk.Button(
            ctrl_frame,
            text="▶ Рассчитать по заданным π₁, π₂",
            command=lambda: self.solve_and_draw(mode="MANUAL"),
            bg="#2563eb",
            fg="white",
            font=("Segoe UI", 9, "bold"),
            padx=10,
            pady=4,
            relief=tk.FLAT,
        ).pack(side=tk.LEFT, padx=6)

        tk.Checkbutton(
            ctrl_frame,
            text="Сдвинуть вторые операции вправо (окно простоя слева)",
            variable=self.shift_right_var,
            command=self.redraw_cached,
            bg="#f8fafc",
            font=("Segoe UI", 9, "bold"),
        ).pack(side=tk.LEFT, padx=10)

        # Панель метрик
        self.stats_label = tk.Label(
            self.root,
            text="",
            bg="#e2e8f0",
            fg="#0f172a",
            font=("Consolas", 10),
            justify=tk.LEFT,
            padx=12,
            pady=8,
            anchor="w",
        )
        self.stats_label.pack(fill=tk.X, padx=12, pady=4)

        # Холст для диаграммы Гантта
        canvas_frame = tk.LabelFrame(
            self.root,
            text=" Диаграмма Гантта (Г. Гантт, 1903) ",
            bg="white",
            font=("Segoe UI", 10, "bold"),
        )
        canvas_frame.pack(fill=tk.BOTH, expand=True, padx=12, pady=6)

        self.canvas = tk.Canvas(canvas_frame, bg="white", highlightthickness=0)
        self.canvas.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)
        self.canvas.bind("<Configure>", lambda e: self.redraw_cached())

    def _update_method_formula(self):
        m = self.cmb_method.current() + 1
        self.lbl_formula.config(text=self.METHOD_FORMULAS.get(m, ""))

    def _on_n_edited(self, which):
        if which in ("n1", "n2"):
            self.last_edited_n = which

    def _resolve_n_counts(self):
        """
        Разрешает соотношение n = n1 + n2.
        Допускается задание (n и только n1), (n и только n2), (только n1 и n2)
        или только одного из параметров — недостающий рассчитывается автоматически.
        """
        s_n = self.ent_n_total.get().strip()
        s_n1 = self.ent_n1.get().strip()
        s_n2 = self.ent_n2.get().strip()

        n = int(s_n) if s_n != "" else None
        n1 = int(s_n1) if s_n1 != "" else None
        n2 = int(s_n2) if s_n2 != "" else None

        if n is not None and n < 0:
            raise ValueError("Общее число требований n не может быть отрицательным.")
        if n1 is not None and n1 < 0:
            raise ValueError("Число требований n₁ не может быть отрицательным.")
        if n2 is not None and n2 < 0:
            raise ValueError("Число требований n₂ не может быть отрицательным.")

        if n is not None and n1 is not None and n2 is None:
            if n1 > n:
                raise ValueError(f"n₁ ({n1}) не может превышать общее число требований n ({n}).")
            n2 = n - n1
        elif n is not None and n2 is not None and n1 is None:
            if n2 > n:
                raise ValueError(f"n₂ ({n2}) не может превышать общее число требований n ({n}).")
            n1 = n - n2
        elif n is None and n1 is not None and n2 is not None:
            n = n1 + n2
        elif n is None and n1 is not None and n2 is None:
            n = n1
            n2 = 0
        elif n is None and n2 is not None and n1 is None:
            n = n2
            n1 = 0
        elif n is not None and n1 is None and n2 is None:
            n1 = (n + 1) // 2
            n2 = n - n1
        elif n is not None and n1 is not None and n2 is not None:
            if n1 + n2 != n:
                # Автоматический пересчет второго параметра по последнему отредактированному полю
                if self.last_edited_n == "n1" and n1 <= n:
                    n2 = n - n1
                elif self.last_edited_n == "n2" and n2 <= n:
                    n1 = n - n2
                else:
                    raise ValueError(
                        f"Сумма n₁ ({n1}) + n₂ ({n2}) должна равняться n ({n}). "
                        "Оставьте одно из полей пустым для автоматического расчёта."
                    )
        else:
            raise ValueError("Укажите количество требований n, n₁ или n₂.")

        # Обновляем поля ввода рассчитанными значениями
        self.ent_n_total.delete(0, tk.END)
        self.ent_n_total.insert(0, str(n))
        self.ent_n1.delete(0, tk.END)
        self.ent_n1.insert(0, str(n1))
        self.ent_n2.delete(0, tk.END)
        self.ent_n2.insert(0, str(n2))

        return n, n1, n2

    def load_preset_1(self):
        self._set_entries("3 6, 5 2, 4 5", "1 2 3", "4 5, 8 3, 2 6", "1 2 3", "3 2", "4")
        self.shift_right_var.set(False)
        self.solve_and_draw(mode="MANUAL")

    def load_preset_2(self):
        self._set_entries("4 3, 6 2, 5 4", "1 3 2", "", "", "", "")
        self.shift_right_var.set(True)
        self.solve_and_draw(mode="OPT")

    def _set_entries(self, t1, pi1, t2, pi2, t3, t4):
        for ent, val in [
            (self.ent_t1, t1),
            (self.ent_pi1, pi1),
            (self.ent_t2, t2),
            (self.ent_pi2, pi2),
            (self.ent_t3, t3),
            (self.ent_t4, t4),
        ]:
            ent.delete(0, tk.END)
            ent.insert(0, val)

    def _parse_pos_int(self, token, context_label):
        try:
            val = int(token)
        except ValueError:
            raise ValueError(
                f"Ожидалось положительное целое число в {context_label}, получено: '{token}'."
            )
        if val < 1:
            raise ValueError(
                f"Все длительности должны быть положительными целыми числами (≥ 1), получено: {val}."
            )
        return val

    def _parse_two_stage(self, text):
        if not text.strip():
            return []
        jobs = []
        for pair in text.split(","):
            parts = pair.strip().split()
            if len(parts) != 2:
                raise ValueError(f"Ожидалась пара целых чисел (op1 op2), получено: '{pair}'")
            a = self._parse_pos_int(parts[0], f"паре '{pair.strip()}'")
            b = self._parse_pos_int(parts[1], f"паре '{pair.strip()}'")
            jobs.append((a, b))
        return jobs

    def _parse_pos_int_list(self, text, label):
        if not text.strip():
            return []
        return [self._parse_pos_int(x, label) for x in text.strip().split()]

    def _parse_permutation(self, text, n, name):
        if n == 0:
            return []
        if not text.strip():
            return list(range(1, n + 1))
        nums = [int(x) for x in text.strip().split()]
        if sorted(nums) != list(range(1, n + 1)):
            raise ValueError(f"Перестановка {name} должна содержать числа от 1 до {n} без повторений.")
        return nums

    def _run_solver(self, stdin_payload):
        solver_bin = get_solver_path()
        if not os.path.exists(solver_bin):
            messagebox.showerror(
                "Не найден модуль C++",
                f"Не найден скомпилированный файл:\n{solver_bin}\n\n"
                "Скомпилируйте solver.cpp:\n"
                "g++ -std=c++17 -O3 solver.cpp -o solver",
            )
            return None

        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
        proc = subprocess.run(
            [solver_bin],
            input=stdin_payload,
            text=True,
            capture_output=True,
            check=True,
            creationflags=creationflags,
        )
        return self._parse_solver_output(proc.stdout)

    def generate_via_cpp(self):
        try:
            n, n1, n2 = self._resolve_n_counts()
            n3 = int(self.ent_n3.get().strip() or "0")
            n4 = int(self.ent_n4.get().strip() or "0")
            if n3 < 0 or n4 < 0 or (n1 + n2 + n3 + n4 == 0):
                raise ValueError("Общее количество работ должно быть больше нуля (все n_i ≥ 0).")

            method = self.cmb_method.current() + 1
            beta = float(self.ent_beta.get().strip() or "0.3")
            alpha = float(self.ent_alpha.get().strip() or "1.5")
            seed = int(self.ent_seed.get().strip() or "0")
            if seed < 0:
                raise ValueError("Seed должен быть неотрицательным целым числом.")
        except Exception as e:
            messagebox.showerror("Ошибка параметров генератора", str(e))
            return

        # При генерации вставляется случайная перестановка (режим RANDOM)
        payload = f"GEN {n1} {n2} {n3} {n4} {method} {beta} {alpha} {seed} RANDOM\n"
        try:
            res = self._run_solver(payload)
            if not res:
                return
            self.last_result = res
            self.last_seed = res.get("seed", 0)

            t1_str = ", ".join(f"{a} {b}" for a, b in res.get("jobs_t1", []))
            t2_str = ", ".join(f"{c} {d}" for c, d in res.get("jobs_t2", []))
            t3_str = " ".join(f"{p}" for p in res.get("jobs_t3", []))
            t4_str = " ".join(f"{q}" for q in res.get("jobs_t4", []))
            pi1_str = " ".join(map(str, res.get("used_pi1", [])))
            pi2_str = " ".join(map(str, res.get("used_pi2", [])))

            self._set_entries(t1_str, pi1_str, t2_str, pi2_str, t3_str, t4_str)
            self.redraw_cached()
        except Exception as e:
            messagebox.showerror("Ошибка выполнения C++ генератора", str(e))

    def solve_and_draw(self, mode="MANUAL"):
        try:
            t1 = self._parse_two_stage(self.ent_t1.get())
            pi1 = (
                list(range(1, len(t1) + 1))
                if mode in ("OPT", "ANTI", "RANDOM")
                else self._parse_permutation(self.ent_pi1.get(), len(t1), "π₁")
            )
            t2 = self._parse_two_stage(self.ent_t2.get())
            pi2 = (
                list(range(1, len(t2) + 1))
                if mode in ("OPT", "ANTI", "RANDOM")
                else self._parse_permutation(self.ent_pi2.get(), len(t2), "π₂")
            )
            t3 = self._parse_pos_int_list(self.ent_t3.get(), "Типе 3")
            t4 = self._parse_pos_int_list(self.ent_t4.get(), "Типе 4")
        except Exception as e:
            messagebox.showerror("Ошибка ввода", str(e))
            return

        lines = [f"EVAL {mode} {len(t1)} {len(t2)} {len(t3)} {len(t4)}"]
        lines += [f"{a} {b}" for a, b in t1]
        if pi1:
            lines.append(" ".join(map(str, pi1)))
        lines += [f"{c} {d}" for c, d in t2]
        if pi2:
            lines.append(" ".join(map(str, pi2)))
        if t3:
            lines.append(" ".join(map(str, t3)))
        if t4:
            lines.append(" ".join(map(str, t4)))
        stdin_payload = "\n".join(lines) + "\n"

        try:
            res = self._run_solver(stdin_payload)
            if not res:
                return
            self.last_result = res
            if res.get("seed", 0):
                self.last_seed = res["seed"]

            if mode in ("OPT", "ANTI", "RANDOM"):
                self.ent_pi1.delete(0, tk.END)
                self.ent_pi1.insert(0, " ".join(map(str, res.get("used_pi1", []))))
                self.ent_pi2.delete(0, tk.END)
                self.ent_pi2.insert(0, " ".join(map(str, res.get("used_pi2", []))))

            self.redraw_cached()
        except Exception as e:
            messagebox.showerror("Ошибка выполнения C++ модуля", str(e))

    def _parse_solver_output(self, out_text):
        lines = [line.strip().split() for line in out_text.strip().splitlines() if line.strip()]
        c_max = int(float(lines[0][1]))
        m1 = [int(float(x)) for x in lines[1][1:]]
        m2 = [int(float(x)) for x in lines[2][1:]]
        num_blocks = int(lines[3][1])
        blocks = []
        for i in range(4, 4 + num_blocks):
            mach, jtype, jid, st, en = lines[i]
            blocks.append(
                {
                    "machine": int(mach),
                    "type": int(jtype),
                    "id": int(jid),
                    "start": int(float(st)),
                    "end": int(float(en)),
                }
            )

        res = {
            "c_max": c_max,
            "m1": m1,
            "m2": m2,
            "blocks": blocks,
            "opt_cmax": c_max,
            "anti_cmax": c_max,
            "opt_pi1": [],
            "opt_pi2": [],
            "anti_pi1": [],
            "anti_pi2": [],
            "used_pi1": [],
            "used_pi2": [],
            "seed": 0,
            "jobs_t1": [],
            "jobs_t2": [],
            "jobs_t3": [],
            "jobs_t4": [],
        }

        for tokens in lines[4 + num_blocks :]:
            key = tokens[0]
            if key == "OPT_CMAX":
                res["opt_cmax"] = int(float(tokens[1]))
            elif key == "ANTI_CMAX":
                res["anti_cmax"] = int(float(tokens[1]))
            elif key in ("OPT_PI1", "OPT_PI2", "ANTI_PI1", "ANTI_PI2", "USED_PI1", "USED_PI2"):
                res[key.lower()] = [int(x) for x in tokens[1:]]
            elif key == "SEED":
                res["seed"] = int(tokens[1])
            elif key in ("JOBS_T1", "JOBS_T2"):
                cnt = int(tokens[1])
                vals = [int(float(x)) for x in tokens[2 : 2 + 2 * cnt]]
                res[key.lower()] = [(vals[2 * k], vals[2 * k + 1]) for k in range(cnt)]
            elif key in ("JOBS_T3", "JOBS_T4"):
                cnt = int(tokens[1])
                res[key.lower()] = [int(float(x)) for x in tokens[2 : 2 + cnt]]

        return res

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
        opt_cmax = res.get("opt_cmax", c_max)
        anti_cmax = res.get("anti_cmax", c_max)
        m1, m2 = res["m1"], res["m2"]
        seed_info = f"  |  Seed (mt19937_64): {self.last_seed}" if self.last_seed else ""

        self.stats_label.config(
            text=(
                f"ТЕКУЩЕЕ C_max: {c_max}  |  Оптимальное C_max (Беллман — Джонсон): {opt_cmax}  |  "
                f"Антиоптимальное C_max (Разворот): {anti_cmax}{seed_info}\n"
                f"Прибор 1 (M1):  Стартовый блок T₁ = {m1[0]}  |  Полезная работа = {m1[1]}  |  "
                f"Завершение C¹ = {m1[2]}  |  Внутр. простой = {m1[3]}  |  Полный простой = {m1[4]}\n"
                f"Прибор 2 (M2):  Стартовый блок T₂ = {m2[0]}  |  Полезная работа = {m2[1]}  |  "
                f"Завершение C² = {m2[2]}  |  Внутр. простой = {m2[3]}  |  Полный простой = {m2[4]}"
            )
        )

        self.canvas.delete("all")
        w = max(self.canvas.winfo_width(), 600)
        h = max(self.canvas.winfo_height(), 280)

        left_pad, right_pad = 125, 45
        usable_w = w - left_pad - right_pad
        scale = usable_w / max(c_max, 1)

        if self.shift_right_var.get():
            b_m1 = self._apply_right_shift(res["blocks"], 1, m1[0], m1[2])
            b_m2 = self._apply_right_shift(res["blocks"], 2, m2[0], m2[2])
        else:
            b_m1 = [b for b in res["blocks"] if b["machine"] == 1]
            b_m2 = [b for b in res["blocks"] if b["machine"] == 2]

        y_m1, y_m2 = 60, 180
        bar_h = 54

        if c_max <= 20:
            step = 1
        elif c_max <= 50:
            step = 2
        elif c_max <= 200:
            step = 10
        elif c_max <= 1000:
            step = 50
        elif c_max <= 5000:
            step = 250
        else:
            step = 500

        t = 0
        while t <= int(c_max) + 1:
            x = left_pad + t * scale
            self.canvas.create_line(x, 32, x, y_m2 + bar_h + 24, fill="#e2e8f0", dash=(2, 2))
            self.canvas.create_text(x, 20, text=f"{t}", fill="#64748b", font=("Segoe UI", 8))
            t += step

        self._draw_machine_track("Прибор 1 (M1)", y_m1, bar_h, b_m1, m1[0], c_max, left_pad, scale, "T₁")
        self._draw_machine_track("Прибор 2 (M2)", y_m2, bar_h, b_m2, m2[0], c_max, left_pad, scale, "T₂")

        x_cmax = left_pad + c_max * scale
        self.canvas.create_line(x_cmax, 28, x_cmax, y_m2 + bar_h + 28, fill="#dc2626", width=2, dash=(5, 3))
        self.canvas.create_text(
            x_cmax,
            y_m2 + bar_h + 40,
            text=f"C_max = {c_max}",
            fill="#dc2626",
            font=("Segoe UI", 10, "bold"),
        )

    def _draw_machine_track(self, title, y, h, blocks, T_val, c_max, left_pad, scale, t_name):
        self.canvas.create_text(
            14, y + h / 2, text=title, anchor="w", font=("Segoe UI", 10, "bold"), fill="#1e293b"
        )
        self.canvas.create_rectangle(
            left_pad, y, left_pad + c_max * scale, y + h, fill="#f8fafc", outline="#cbd5e1"
        )

        cursor = 0
        for b in blocks:
            if b["start"] > cursor:
                self._draw_idle_box(
                    left_pad + cursor * scale,
                    y,
                    left_pad + b["start"] * scale,
                    y + h,
                    b["start"] - cursor,
                    "Окно простоя",
                )
            cursor = b["end"]
        if c_max > cursor:
            self._draw_idle_box(
                left_pad + cursor * scale,
                y,
                left_pad + c_max * scale,
                y + h,
                c_max - cursor,
                "Простой",
            )

        for b in blocks:
            x1 = left_pad + b["start"] * scale
            x2 = left_pad + b["end"] * scale
            main_col, tail_col, _ = self.COLORS[b["type"]]
            is_tail = (b["machine"] == 1 and b["type"] == 2) or (b["machine"] == 2 and b["type"] == 1)
            fill_col = tail_col if is_tail else main_col
            text_col = "#0f172a" if is_tail else "white"

            self.canvas.create_rectangle(x1, y, x2, y + h, fill=fill_col, outline="#1e293b", width=1)
            op_name = {
                (1, 1): f"a_{b['id']}",
                (2, 1): f"b_{b['id']}",
                (2, 2): f"c_{b['id']}",
                (1, 2): f"d_{b['id']}",
                (1, 3): f"p_{b['id']}",
                (2, 4): f"q_{b['id']}",
            }[(b["machine"], b["type"])]
            dur = b["end"] - b["start"]
            if x2 - x1 > 28:
                self.canvas.create_text(
                    (x1 + x2) / 2,
                    y + h / 2 - 7,
                    text=op_name,
                    fill=text_col,
                    font=("Segoe UI", 9, "bold"),
                )
                self.canvas.create_text(
                    (x1 + x2) / 2,
                    y + h / 2 + 9,
                    text=f"({dur})",
                    fill=text_col,
                    font=("Segoe UI", 8),
                )

            if x2 - x1 > 18:
                self.canvas.create_text(
                    x2, y + h + 11, text=f"{b['end']}", fill="#334155", font=("Segoe UI", 8)
                )

        if T_val > 0:
            x_t = left_pad + T_val * scale
            self.canvas.create_line(x_t, y - 10, x_t, y + h + 10, fill="#0f172a", width=2, dash=(4, 2))
            self.canvas.create_text(
                x_t, y - 16, text=f"{t_name}={T_val}", fill="#0f172a", font=("Segoe UI", 8, "bold")
            )

    def _draw_idle_box(self, x1, y1, x2, y2, dur, label):
        self.canvas.create_rectangle(x1, y1, x2, y2, fill="#fee2e2", outline="#fca5a5", stipple="gray25")
        if x2 - x1 > 34:
            self.canvas.create_text(
                (x1 + x2) / 2,
                (y1 + y2) / 2,
                text=f"{label}\n{dur}",
                fill="#b91c1c",
                font=("Segoe UI", 8, "italic"),
                justify=tk.CENTER,
            )


if __name__ == "__main__":
    root = tk.Tk()
    app = JacksonSchedulerApp(root)
    root.mainloop()