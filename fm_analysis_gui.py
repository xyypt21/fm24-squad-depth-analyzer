"""
FM2024 4-2-3-1 squad depth analysis - GUI version.

Reads the squad directly from the running FM24 game memory.

Usage: python fm_analysis_gui.py
"""

import os
import threading
import tkinter as tk
from tkinter import messagebox, ttk

from fm_analysis import OUTPUT, analyze, load_config, save_config
from fm_roster import (
    read_merged_squad_from_memory,
    read_squad_from_memory,
)


class FmGui:
    def __init__(self, root):
        self.root = root
        root.title("FM2024 阵容厚度分析")
        root.geometry("720x520")
        root.minsize(640, 420)

        pad = {"padx": 6, "pady": 4}
        frm = ttk.Frame(root, padding=10)
        frm.pack(fill="both", expand=True)

        self._config = load_config()
        self.club_var = tk.StringVar(value=str(self._config["club_uid"]))

        club_row = ttk.Frame(frm)
        club_row.grid(row=0, column=0, columnspan=3, sticky="ew", **pad)
        ttk.Label(club_row, text="俱乐部 ID:").pack(side="left")
        ttk.Entry(club_row, textvariable=self.club_var, width=10).pack(side="left")

        ea_row = ttk.Frame(frm)
        ea_row.grid(row=1, column=0, columnspan=3, sticky="ew", **pad)
        self.min_age_var = tk.StringVar(value=str(self._config["min_age"]))
        self.ea_age_var = tk.StringVar(value=str(self._config["growth_until_age"]))
        self.ea_growth_var = tk.StringVar(value=str(self._config["growth_per_year"]))
        ttk.Label(ea_row, text="最小年龄:").pack(side="left")
        ttk.Entry(ea_row, textvariable=self.min_age_var, width=6).pack(side="left", padx=(0, 10))
        ttk.Label(ea_row, text="EA成长至年龄:").pack(side="left")
        ttk.Entry(ea_row, textvariable=self.ea_age_var, width=6).pack(side="left", padx=(0, 10))
        ttk.Label(ea_row, text="EA每年成长:").pack(side="left")
        ttk.Entry(ea_row, textvariable=self.ea_growth_var, width=6).pack(side="left")

        opt_row = ttk.Frame(frm)
        opt_row.grid(row=2, column=0, columnspan=3, sticky="ew", **pad)
        self.translate_var = tk.BooleanVar(value=bool(self._config.get("translate_names", False)))
        ttk.Checkbutton(opt_row, text="翻译球员名字为中文", variable=self.translate_var).pack(
            side="left"
        )

        club2_row = ttk.Frame(frm)
        club2_row.grid(row=3, column=0, columnspan=3, sticky="ew", **pad)
        self.compare_var = tk.BooleanVar(value=bool(self._config.get("merge_club2", False)))
        self.club2_var = tk.StringVar(
            value=str(self._config.get("club2_uid", 0) or 0) if self._config.get("club2_uid") else ""
        )
        ttk.Checkbutton(
            club2_row, text="合并第二俱乐部", variable=self.compare_var, command=self._toggle_club2
        ).pack(side="left")
        self.club2_entry = ttk.Entry(club2_row, textvariable=self.club2_var, width=10)
        self.club2_entry.pack(side="left", padx=(4, 10))
        self._refresh_club2_state()

        self.formula_var = tk.StringVar()
        ttk.Label(frm, textvariable=self.formula_var, foreground="#555", justify="left").grid(
            row=4, column=0, columnspan=3, sticky="w", **pad
        )
        self._update_formula()
        self.ea_age_var.trace_add("write", lambda *_: self._update_formula())
        self.ea_growth_var.trace_add("write", lambda *_: self._update_formula())

        btn_row = ttk.Frame(frm)
        btn_row.grid(row=5, column=0, columnspan=3, sticky="ew", **pad)
        self.run_btn = ttk.Button(btn_row, text="开始分析", command=self.start_analysis)
        self.run_btn.pack(side="left")
        ttk.Button(btn_row, text="退出", command=root.destroy).pack(side="right")

        self.log = tk.Text(frm, height=14, state="disabled", wrap="word")
        self.log.grid(row=6, column=0, columnspan=3, sticky="nsew", **pad)
        scroll = ttk.Scrollbar(frm, command=self.log.yview)
        scroll.grid(row=6, column=3, sticky="ns")
        self.log.configure(yscrollcommand=scroll.set)

        frm.columnconfigure(1, weight=1)
        frm.rowconfigure(6, weight=1)

        self.status = tk.StringVar(value="就绪")
        ttk.Label(frm, textvariable=self.status).grid(
            row=7, column=0, columnspan=3, sticky="w", **pad
        )

    def log_line(self, text):
        self.log.configure(state="normal")
        self.log.insert("end", text + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def _update_formula(self):
        age = self.ea_age_var.get() or "?"
        growth = self.ea_growth_var.get() or "?"
        self.formula_var.set(
            f"公式:\n"
            f"年龄 < {age} → EA = CA + ({age} - 年龄) × {growth} (不超过 PA)\n"
            f"年龄 ≥ {age} → EA = CA"
        )

    def _get_club_uid(self):
        return int(self.club_var.get())

    def _refresh_club2_state(self):
        """按复选框启用/禁用第二俱乐部输入控件。"""
        self.club2_entry.configure(state="normal" if self.compare_var.get() else "disabled")

    def _toggle_club2(self):
        self._refresh_club2_state()

    def open_result(self):
        path = OUTPUT
        if path.exists():
            os.startfile(str(path.resolve()))
        else:
            messagebox.showwarning("提示", "结果文件不存在，请先运行分析。")

    def start_analysis(self):
        try:
            club_uid = self._get_club_uid()
            if club_uid <= 0:
                raise ValueError
            min_age = int(self.min_age_var.get())
            growth_until_age = int(self.ea_age_var.get())
            growth_per_year = int(self.ea_growth_var.get())
            if min_age <= 0 or growth_until_age <= 0 or growth_per_year < 0:
                raise ValueError
        except ValueError:
            messagebox.showerror("错误", "参数必须是正整数（每年成长可为 0）。")
            return

        club2_uid = None
        if self.compare_var.get():
            raw = self.club2_var.get().strip()
            if raw:
                try:
                    club2_uid = int(raw)
                    if club2_uid <= 0:
                        raise ValueError
                except ValueError:
                    messagebox.showerror("错误", "第二俱乐部 ID 必须是正整数。")
                    return

        save_config(
            {
                "club_uid": club_uid,
                "min_age": min_age,
                "growth_until_age": growth_until_age,
                "growth_per_year": growth_per_year,
                "translate_names": self.translate_var.get(),
                "merge_club2": bool(self.compare_var.get()),
                "club2_uid": club2_uid or 0,
            }
        )
        self.run_btn.configure(state="disabled")
        self.status.set("分析中...")
        self.log_line("-" * 40)
        self.log_line(
            f"俱乐部 ID: {club_uid}" + (f"，合并第二俱乐部 {club2_uid}" if club2_uid else "")
        )
        self.log_line(
            f"参数: 最小 {min_age} 岁，EA成长至 {growth_until_age} 岁，每年 +{growth_per_year}"
        )
        self.log_line(f"翻译球员名字: {'开启' if self.translate_var.get() else '关闭'}")
        thread = threading.Thread(
            target=self._analyze,
            args=(
                club_uid,
                club2_uid,
                min_age,
                growth_until_age,
                growth_per_year,
                self.translate_var.get(),
            ),
            daemon=True,
        )
        thread.start()

    def _analyze(self, club_uid, club2_uid, min_age, growth_until_age, growth_per_year, translate):
        try:
            name2 = None
            if club2_uid is None:
                name, roster = read_squad_from_memory(club_uid)
                club_text = f"{club_uid}{(' ' + name) if name else ''}"
                detail = f" 共 {len(roster)} 名球员"
            else:
                name1, name2, roster = read_merged_squad_from_memory(club_uid, club2_uid)
                club_text = (
                    f"{club_uid}{(' ' + name1) if name1 else ''} + "
                    f"{club2_uid}{(' ' + name2) if name2 else ''}"
                )
                detail = f" 合并后共 {len(roster)} 名球员"
            # 把第二俱乐部的设置记进 config，下次启动时回填
            cfg = load_config()
            cfg["merge_club2"] = club2_uid is not None
            cfg["club2_uid"] = club2_uid or 0
            save_config(cfg)
            html = analyze(
                roster,
                output=OUTPUT,
                min_age=min_age,
                growth_until_age=growth_until_age,
                growth_per_year=growth_per_year,
                translate=translate,
            )
            if html:
                message = f"完成：{club_text}{detail}，结果已写入 {OUTPUT}"
            else:
                message = "未读到阵容数据，请确认游戏已运行且俱乐部 ID 正确。"
        except Exception as exc:
            message = f"分析出错：{exc}"
        self.root.after(0, self._on_done, message)

    def _on_done(self, message):
        self.log_line(message)
        self.status.set(message)
        self.run_btn.configure(state="normal")
        if message.startswith("完成"):
            self.open_result()


def main():
    root = tk.Tk()
    FmGui(root)
    root.mainloop()


if __name__ == "__main__":
    main()
