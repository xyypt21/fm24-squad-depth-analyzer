"""tmp 分支实验 GUI —— 读取游戏内存，自动检测用户俱乐部 ID。

重构第一阶段的入口：
    1. 附加运行中的 FM24 进程（只读）
    2. 读取游戏版本 / 游戏内日期，验证偏移可用
    3. 通过人控经理链自动检测用户俱乐部，回填 ID
    4. 可把检测结果保存为 config.json 的默认 club_uid

用法：python fm_club_gui.pyw   （游戏需已启动并载入存档）
"""

import json
import queue
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from session import attach_session  # noqa: E402
from user_club import DetectionError, detect_user_clubs  # noqa: E402

CONFIG_PATH = Path(__file__).parent / "config.json"
POLL_MS = 150


def load_config():
    try:
        return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_config(config):
    CONFIG_PATH.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")


class ClubGui:
    def __init__(self, root: tk.Tk):
        self.root = root
        root.title("FM24 俱乐部检测（tmp 重构）")
        root.geometry("680x460")
        root.minsize(560, 380)

        self._queue = queue.Queue()
        self._session = None
        self._clubs = []

        pad = {"padx": 8, "pady": 4}
        frm = ttk.Frame(root, padding=10)
        frm.pack(fill="both", expand=True)

        # ── 连接行 ───────────────────────────────────
        conn_row = ttk.Frame(frm)
        conn_row.grid(row=0, column=0, columnspan=2, sticky="ew", **pad)
        self.connect_btn = ttk.Button(
            conn_row, text="连接游戏并检测俱乐部", command=self.start_detect
        )
        self.connect_btn.pack(side="left")
        self.session_var = tk.StringVar(value="未连接")
        ttk.Label(conn_row, textvariable=self.session_var, foreground="#555").pack(
            side="left", padx=(10, 0)
        )

        # ── 检测结果 ─────────────────────────────────
        res_row = ttk.Frame(frm)
        res_row.grid(row=1, column=0, columnspan=2, sticky="ew", **pad)
        ttk.Label(res_row, text="检测到的俱乐部:").pack(side="left")
        self.club_combo = ttk.Combobox(res_row, state="disabled", width=42)
        self.club_combo.pack(side="left", padx=(6, 0))
        self.club_combo.bind("<<ComboboxSelected>>", self._on_pick_club)

        id_row = ttk.Frame(frm)
        id_row.grid(row=2, column=0, columnspan=2, sticky="ew", **pad)
        ttk.Label(id_row, text="俱乐部 ID:").pack(side="left")
        self.uid_var = tk.StringVar(value="")
        ttk.Entry(id_row, textvariable=self.uid_var, width=12).pack(side="left", padx=(6, 10))
        self.save_btn = ttk.Button(
            id_row, text="保存为默认俱乐部 ID", command=self.save_default, state="disabled"
        )
        self.save_btn.pack(side="left")

        saved = load_config().get("club_uid")
        hint = f"（config.json 当前默认: {saved}）" if saved else ""
        ttk.Label(
            frm, text=f"提示：先启动 FM24 并载入存档，再点连接。{hint}", foreground="#555"
        ).grid(row=3, column=0, columnspan=2, sticky="w", **pad)

        # ── 日志 ─────────────────────────────────────
        self.log_text = tk.Text(frm, height=14, state="disabled", wrap="word")
        self.log_text.grid(row=4, column=0, sticky="nsew", **pad)
        scroll = ttk.Scrollbar(frm, command=self.log_text.yview)
        scroll.grid(row=4, column=1, sticky="ns", **pad)
        self.log_text.configure(yscrollcommand=scroll.set)
        frm.rowconfigure(4, weight=1)

        self.status = tk.StringVar(value="就绪")
        ttk.Label(frm, textvariable=self.status).grid(row=5, column=0, columnspan=2, sticky="w")

        self.root.after(POLL_MS, self._poll_queue)

    # ── 日志与队列 ----------------------------------------
    def log_line(self, text):
        self.log_text.configure(state="normal")
        self.log_text.insert("end", text + "\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def _emit_log(self, text):
        self._queue.put(("log", text))

    def _poll_queue(self):
        try:
            while True:
                kind, payload = self._queue.get_nowait()
                if kind == "log":
                    self.log_line(payload)
                elif kind == "done":
                    self._on_detect_done(payload)
                elif kind == "status":
                    self.status.set(payload)
        except queue.Empty:
            pass
        self.root.after(POLL_MS, self._poll_queue)

    # ── 检测流程 ------------------------------------------
    def start_detect(self):
        self.connect_btn.configure(state="disabled")
        self.club_combo.configure(state="disabled")
        self.save_btn.configure(state="disabled")
        self.status.set("检测中...")
        self.log_line("-" * 48)
        threading.Thread(target=self._detect_worker, daemon=True).start()

    def _detect_worker(self):
        session = None
        try:
            session = attach_session()
            self._emit_log(f"[game] 附加成功 PID={session.mem.pid}")
            self._emit_log(
                f"[game] 文件版本={session.file_version or '?'} "
                f"产品版本={session.product_version or '?'}"
            )
            gd = session.game_date()
            self._emit_log(f"[game] 偏移表版本={session.offsets.fm_version}")
            self._emit_log(f"[game] 游戏内日期={gd.isoformat() if gd else '读取失败'}")
            clubs = detect_user_clubs(session, log=self._emit_log)
            self._queue.put(("done", ("ok", clubs)))
        except DetectionError as exc:
            self._queue.put(("done", ("fail", str(exc))))
        except Exception as exc:
            self._queue.put(("done", ("fail", f"附加失败:{exc}")))
        finally:
            if session is not None:
                session.close()

    def _on_detect_done(self, payload):
        kind, data = payload
        self.connect_btn.configure(state="normal")
        if kind != "ok":
            self.status.set("检测失败")
            messagebox.showerror("检测失败", data)
            return
        self._clubs = data
        labels = []
        for c in self._clubs:
            label = f"{c.uid} - {c.name}" if c.name else str(c.uid)
            labels.append(label)
        self.club_combo.configure(values=labels, state="readonly")
        self.club_combo.current(0)
        self._on_pick_club()
        msg = f"检测到 {len(self._clubs)} 家候选俱乐部:" + "、".join(
            str(c.uid) for c in self._clubs
        )
        self.status.set(msg)
        self.log_line(f"[done] {msg}")
        self.save_btn.configure(state="normal")

    def _on_pick_club(self, *_):
        idx = self.club_combo.current()
        if 0 <= idx < len(self._clubs):
            self.uid_var.set(str(self._clubs[idx].uid))

    # ── 配置 ----------------------------------------------
    def save_default(self):
        raw = self.uid_var.get().strip()
        try:
            uid = int(raw)
            if uid <= 0:
                raise ValueError
        except ValueError:
            messagebox.showerror("错误", "俱乐部 ID 必须是正整数。")
            return
        cfg = load_config()
        cfg["club_uid"] = uid
        save_config(cfg)
        self.log_line(f"[config] 已把默认俱乐部 ID 保存为 {uid}")
        self.status.set(f"已保存默认俱乐部 ID: {uid}")


def main():
    root = tk.Tk()
    ClubGui(root)
    root.mainloop()


if __name__ == "__main__":
    main()
