# FM24 俱乐部 ID 检测器（tmp 分支）

只读访问运行中的 FM24 进程内存，自动检测玩家（人控经理）执教的俱乐部，在 GUI 中显示其 ID 与队名。

## 使用

```bash
pythonw fm_club_gui.pyw
```

1. 启动 FM24 并载入存档。
2. 点击"连接游戏并检测"。
3. 若有多家候选（网络球等），下拉选择；ID 会填入输入框。
4. "保存为默认俱乐部 ID"写回 `config.json`。

检测失败或游戏更新后偏移失效时，运行诊断：

```bash
python fm_probe_user.py --club 920
```

## 结构

```
fm_club_gui.pyw       GUI 入口
fmlib/memory.py       只读跨进程内存原语（ctypes）
fmlib/offsets.py      fm_offsets_info.json 加载与版本选择
fmlib/session.py      游戏会话：附加进程、exe 版本、游戏内日期
fmlib/clubs.py        俱乐部记录读取（uid、队名）
fmlib/user_club.py    检测：人控经理向量 ∩ 球队教练指针
fm_probe_user.py      诊断脚本
fm_offsets_info.json  偏移参考表（FM Scouting Tool 数据）
config.json           配置持久化（club_uid 等）
```

检测原理：`[exe+mgr_hnp_rva]` → 人控经理对象向量；球员记录签名扫描枚举全部球队；
球队 +0x80 的 `manager_ptr` 等于（或内部引用）向量元素的即为用户执教的球队 → 归一化到父俱乐部 UID 与队名。

> 阵容深度分析（EA 计算 / 匈牙利选阵 / HTML 报告）仍在 develop 分支，本分支只保留俱乐部检测功能。
