"""
FM2024 4-2-3-1 squad depth analysis - GUI version.

Usage: python fm_analysis_gui.py
"""

import os
import threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from pathlib import Path

import fm_analysis as fa


class FmGui:
    def __init__(self, root):
        self.root = root
        root.title("FM2024 阵容厚度分析")
        root.geometry("720x520")
        root.minsize(640, 420)

        pad = {"padx": 6, "pady": 4}
        frm = ttk.Frame(root, padding=10)
        frm.pack(fill="both", expand=True)

        self._config = fa.load_config()
        self.rtf_var = tk.StringVar(value=self._config["rtf_path"])

        ttk.Label(frm, text="阵容文件 (RTF):").grid(row=0, column=0, sticky="w", **pad)
        ttk.Entry(frm, textvariable=self.rtf_var).grid(row=0, column=1, sticky="ew", **pad)
        ttk.Button(frm, text="浏览...", command=self.browse_rtf).grid(row=0, column=2, **pad)

        ea_row = ttk.Frame(frm)
        ea_row.grid(row=1, column=0, columnspan=3, sticky="ew", **pad)
        self.ea_age_var = tk.StringVar(value=str(self._config["growth_until_age"]))
        self.ea_growth_var = tk.StringVar(value=str(self._config["growth_per_year"]))
        ttk.Label(ea_row, text="EA成长至年龄:").pack(side="left")
        ttk.Entry(ea_row, textvariable=self.ea_age_var, width=6).pack(side="left", padx=(0, 10))
        ttk.Label(ea_row, text="EA每年成长:").pack(side="left")
        ttk.Entry(ea_row, textvariable=self.ea_growth_var, width=6).pack(side="left")

        self.formula_var = tk.StringVar()
        ttk.Label(frm, textvariable=self.formula_var, foreground="#555", justify="left").grid(
            row=2, column=0, columnspan=3, sticky="w", **pad
        )
        self._update_formula()
        self.ea_age_var.trace_add("write", lambda *_: self._update_formula())
        self.ea_growth_var.trace_add("write", lambda *_: self._update_formula())

        btn_row = ttk.Frame(frm)
        btn_row.grid(row=3, column=0, columnspan=3, sticky="ew", **pad)
        self.run_btn = ttk.Button(btn_row, text="开始分析", command=self.start_analysis)
        self.run_btn.pack(side="left")
        ttk.Button(btn_row, text="退出", command=root.destroy).pack(side="right")

        self.log = tk.Text(frm, height=14, state="disabled", wrap="word")
        self.log.grid(row=4, column=0, columnspan=3, sticky="nsew", **pad)
        scroll = ttk.Scrollbar(frm, command=self.log.yview)
        scroll.grid(row=4, column=3, sticky="ns")
        self.log.configure(yscrollcommand=scroll.set)

        frm.columnconfigure(1, weight=1)
        frm.rowconfigure(4, weight=1)

        self.status = tk.StringVar(value="就绪")
        ttk.Label(frm, textvariable=self.status).grid(
            row=5, column=0, columnspan=3, sticky="w", **pad
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

    def browse_rtf(self):
        current = Path(self.rtf_var.get())
        path = filedialog.askopenfilename(
            title="选择阵容文件",
            filetypes=[("RTF 文件", "*.rtf"), ("所有文件", "*.*")],
            initialdir=str(current.parent) if current.exists() else os.path.expanduser("~"),
        )
        if path:
            self.rtf_var.set(path)

    def open_result(self):
        path = fa.OUTPUT
        if path.exists():
            os.startfile(str(path.resolve()))
        else:
            messagebox.showwarning("提示", "结果文件不存在，请先运行分析。")

    def start_analysis(self):
        if not Path(self.rtf_var.get()).exists():
            messagebox.showerror("错误", "阵容文件不存在，请检查路径。")
            return
        try:
            growth_until_age = int(self.ea_age_var.get())
            growth_per_year = int(self.ea_growth_var.get())
            if growth_until_age <= 0 or growth_per_year < 0:
                raise ValueError
        except ValueError:
            messagebox.showerror("错误", "EA 参数必须是正整数（每年成长可为 0）。")
            return
        fa.save_config({"rtf_path": self.rtf_var.get(),
                        "growth_until_age": growth_until_age,
                        "growth_per_year": growth_per_year})
        self.run_btn.configure(state="disabled")
        self.status.set("分析中...")
        self.log_line("-" * 40)
        self.log_line(f"读取: {self.rtf_var.get()}")
        self.log_line(f"EA 参数: 成长至 {growth_until_age} 岁，每年 +{growth_per_year}")
        thread = threading.Thread(
            target=self._analyze,
            args=(growth_until_age, growth_per_year),
            daemon=True,
        )
        thread.start()

    def _analyze(self, growth_until_age, growth_per_year):
        try:
            rtf_path = Path(self.rtf_var.get())
            roster = fa.read_roster_from_rtf(rtf_path)
            html = fa.analyze(roster, output=fa.OUTPUT,
                              growth_until_age=growth_until_age,
                              growth_per_year=growth_per_year)
            if html:
                message = f"完成：共 {len(roster)} 名球员，结果已写入 {fa.OUTPUT}"
            else:
                message = "未找到阵容数据，请确认 RTF 文件格式正确。"
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
