# BG Runner

一个轻量的 PySide6 图形化后台任务管理器。用于运行和维护后台命令（SSH 隧道、开发服务器、watch 脚本等）。

## 功能

- **快速运行按钮**：从同目录的 `commands.json` 读取配置的命令，一键启动（可自定义）
- **命令输入框**：输入任意命令（如 `ssh -N -L 8080:localhost:8080 user@host`），回车或点 Run 后台运行
- **保存为快速按钮**：命令框中输入命令后点 **Save as quick**，写入 `commands.json` 并立即生成一个快速按钮（可自定义按钮文字）
- **拖放运行**：把 `.py` / `.sh` / 可执行文件直接拖进窗口即可运行；其他文件会填入命令框
- **任务列表**：实时显示每个任务的运行状态、PID、退出码，颜色区分（绿=运行中、灰=成功、红=失败、橙=被终止）
- **双击查看输出**：双击列表项打开独立输出窗口，实时滚动显示控制台输出，支持复制全部 / 停止 / 重启
- **右键菜单**：打开输出、停止、重启（SSH 断了可一键重连）、复制命令、从列表移除
- **命令历史**：输入框按 ↑/↓ 浏览历史命令
- **干净的进程管理**：任务在独立进程组中运行，停止时整棵进程树（含 SSH 等子进程）一起终止，3 秒未退出自动 SIGKILL

## 安装与运行

```bash
pip install -r requirements.txt   # 或 pip install PySide6
python3 bg_runner.py
```

## 使用说明

| 操作 | 效果 |
| --- | --- |
| 输入命令后回车 / 点 Run | 后台启动任务 |
| 输入命令后点 Save as quick | 把该命令保存为快速启动按钮（可自定义按钮文字） |
| 点击顶部快速按钮 | 运行 `commands.json` 中配置的命令 |
| 拖入 `.py` / `.sh` / 可执行文件 | 自动以 `python3` / `bash` / 直接执行的方式运行 |
| 双击任务行 | 打开该任务的实时输出窗口 |
| 选中任务后点 Stop selected | 终止选中任务（整棵进程树） |
| 右键任务 → Restart | 重新运行相同命令（如重连断掉的 SSH 隧道） |
| 右键任务 → Remove / Clear finished | 清理已完成任务 |
| 关闭窗口 | 最小化到系统托盘（不退出程序） |
| 托盘图标右键 → Show | 从托盘恢复窗口 |
| 托盘图标右键 → Quit | 退出程序（询问是否终止运行中的任务） |
| 双击托盘图标 | 从托盘恢复窗口 |

## 快速命令配置

程序同目录下的 `commands.json`（首次运行自动生成示例，`"label"` 省略时用命令本身作为按钮文字）：

```json
{
  "buttons": [
    {"label": "SSH Tunnel", "command": "ssh -N -L 8080:localhost:8080 user@host"},
    {"label": "Ping", "command": "ping -c 5 8.8.8.8"},
    {"label": "HTTP Server", "command": "python3 -m http.server 8000"},
    {"label": "Watch Log", "command": "tail -f /var/log/syslog"}
  ]
}
```

修改文件后在按钮区域**右键 → Reload commands.json** 即可生效（无需重启程序）；也可以在界面命令框中输入命令后点 **Save as quick** 直接添加（重复的命令会被拒绝，避免生成重复按钮）。

## 命令解析规则

- 命令不含 shell 语法（管道、重定向、`&&`、通配符、`$` 等）时直接 exec，PID 干净、可精确终止
- 含 shell 语法时通过 `/bin/sh -c` 运行，支持管道组合等复杂命令
- 引号内的特殊字符会被正确识别（如 `echo "a | b"` 不需要 shell）

## 注意事项

- 涉及 `sudo` 的命令无法在无 TTY 的 GUI 环境下直接交互输入密码，建议使用 `pkexec` 或免密配置
- 任务输出在内存中完整保留，超长输出（如持续刷日志的进程）可能占用较多内存；输出窗口显示上限为 20 万行
- 已测试环境：Python 3.14 + PySide6 6.11（Linux）

## 测试

```bash
python3 tests/test_smoke.py    # offscreen 冒烟测试，无需显示器
python3 tests/test_cycle.py    # bug2 回归测试（窗口重开/输出增量）
```
