"""本机动作桥：把桌宠上的手势 / 菜单 / 对话 action 映射成本机操作。

只依赖标准库 + PyQt6，不依赖任何模型，也不调用渲染层：

    触发源（双击 / 右键菜单 / 对话 action 字段）
        └─ dispatch(action_id) ─┬─ kind=internal → 表情 / 回弹
                                ├─ kind=app      → 启动 / 唤起外部程序
                                └─ kind=widget   → 气泡 / 待办

安全底线（比功能重要）：
  * 只认 action_id，绝不接受外部传入的路径字符串；模型只能选 id
  * 启动用 argv 列表 + startDetached，从不拼 shell 字符串、不用 shell=True
  * 未知 id 静默忽略；关闭/删除这类必须二次确认
  * enabled 默认 False，由用户自己的配置显式打开
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import time
from pathlib import Path

from PyQt6.QtCore import QObject, QProcess, pyqtSignal

APP_NAME = "desktop_pet"

# 首次运行时写到 ~/.config/desktop_pet/actions.json 的模板：
# 真实路径由用户自己填（不进仓库），这里只给可直接改的候选名。
DEFAULT_USER_CFG = {
    "_说明": "桌宠动作配置。registry 里一条一个动作，模型/手势只能引用 id。"
             "path 填绝对路径（优先级最高），否则按平台用 which 找 cmd 里的名字。",
    "enabled": False,
    "require_confirm": True,
    "bindings": {"double_tap": ""},
    "registry": {
        "launch:wechat": {
            "kind": "app", "label": "打开微信", "confirm": True,
            "win": ["WeChat.exe"], "mac": ["WeChat"], "linux": ["wechat"],
            "args": [], "single_instance": True,
        },
        "launch:calculator": {
            "kind": "app", "label": "打开计算器", "confirm": False,
            "win": ["calc.exe"], "mac": ["Calculator"],
            "linux": ["gnome-calculator", "kcalc", "galculator"],
            "args": [], "single_instance": True,
        },
    },
}


def user_config_path() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return Path(base) / APP_NAME / "actions.json"


def ensure_user_config(path: Path | None = None) -> Path:
    """没有用户配置就写一份模板（放在用户目录，不进仓库）。"""
    p = path or user_config_path()
    if not p.exists():
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(DEFAULT_USER_CFG, ensure_ascii=False, indent=2),
                     encoding="utf-8")
    return p


def _read_json(path: Path) -> dict:
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


class ActionBridge(QObject):
    """一个桌宠实例一份。cfg 是「SKU 配置 + 用户配置」合并后的结果。"""

    feedback = pyqtSignal(str, str)      # (气泡文本, 表情状态)

    def __init__(self, cfg: dict, expr_state=None, parent=None,
                 confirm_fn=None):
        super().__init__(parent)
        self.cfg = dict(cfg or {})
        self.registry = dict(self.cfg.get("registry") or {})
        self.bindings = dict(self.cfg.get("bindings") or {})
        self.sm = expr_state
        self.confirm_fn = confirm_fn          # 测试可注入；默认弹 QMessageBox
        self.procs: dict[str, tuple[int, float]] = {}   # action_id -> (pid, 启动时刻)
        self.enabled = bool(self.cfg.get("enabled", False))

        # 显式配置的绝对路径（per-user），优先级最高
        self.paths = dict(self.cfg.get("paths") or {})

    # ---------------- 装配 ----------------
    @classmethod
    def for_asset(cls, asset, expr_state=None, parent=None, confirm_fn=None):
        """从 <素材目录>/actions.json + 用户配置合成一份。

        SKU 配置决定「这只桌宠有哪些动作」，用户配置补真实路径并可以整体开关。
        两个都没有 → 返回一个 disabled 的桥，菜单里不会多出任何东西。
        """
        cfg: dict = {}
        src = getattr(asset, "source", None)
        if src is not None:
            cfg.update(_read_json(Path(src).parent / "actions.json"))
        user = _read_json(user_config_path())
        if user:
            # 用户配置优先：开关、确认、绑定、路径；registry 逐条覆盖
            for k in ("enabled", "require_confirm", "bindings", "paths"):
                if k in user:
                    cfg[k] = user[k]
            reg = dict(cfg.get("registry") or {})
            reg.update(user.get("registry") or {})
            cfg["registry"] = reg
        return cls(cfg, expr_state, parent, confirm_fn)

    def menu_items(self) -> list[tuple[str, str]]:
        """右键菜单里要列出的 (显示名, action_id)。"""
        if not self.enabled:
            return []
        out = []
        for aid, spec in self.registry.items():
            if not isinstance(spec, dict) or spec.get("hidden"):
                continue
            if aid.startswith("internal:"):
                continue
            out.append((spec.get("label") or aid, aid))
        return out

    def binding(self, trigger: str) -> str | None:
        """手势绑定：double_tap / long_press … 没配返回 None。"""
        if not self.enabled:
            return None
        aid = (self.bindings or {}).get(trigger) or ""
        return aid or None

    # ---------------- 派发 ----------------
    def dispatch(self, action_id: str) -> bool:
        """唯一入口。未知 id / 未开启 → False，绝不猜、绝不报错。"""
        if not self.enabled or not action_id:
            return False
        spec = self.registry.get(action_id)
        if not isinstance(spec, dict):
            return False
        kind = spec.get("kind")
        if kind == "internal":
            return self._internal(spec)
        if kind == "app":
            return self._app(action_id, spec)
        if kind == "widget":
            self.feedback.emit(spec.get("text") or spec.get("label") or "", "normal")
            return True
        return False

    def _internal(self, spec: dict) -> bool:
        st = spec.get("emotion", "surprise")
        if self.sm is not None:
            try:
                self.sm.set(st, hold_ms=spec.get("hold_ms"))
            except Exception:
                pass
        self.feedback.emit(spec.get("text") or "", st)
        return True

    def _app(self, action_id: str, spec: dict) -> bool:
        label = spec.get("label") or action_id
        confirm = bool(spec.get("confirm", self.cfg.get("require_confirm", True)))
        if confirm and not self._confirm(label):
            self.feedback.emit("已取消 ~", "normal")
            return False
        if spec.get("single_instance") and self._running(action_id):
            self._activate(spec)
            self.feedback.emit(f"{label} 已经在运行啦", "normal")
            return True
        argv = self._resolve(action_id, spec)
        if not argv:
            self.feedback.emit(f"没找到「{label}」，检查一下 actions.json", "half")
            return False
        try:
            # detached：桌宠退出时不能把自己拉起的程序一起带走
            ok, pid = QProcess.startDetached(argv[0], argv[1:])
        except Exception:
            ok, pid = False, 0
        if not ok:
            self.feedback.emit(f"启动「{label}」失败", "half")
            return False
        self.procs[action_id] = (int(pid or 0), time.monotonic())
        self.feedback.emit(spec.get("ok_text") or f"帮你打开{label}啦 ~", "surprise")
        return True

    def _resolve(self, action_id: str, spec: dict) -> list[str] | None:
        """返回 argv 列表：显式绝对路径 > 平台候选名 which > 平台固定 argv。"""
        args = [str(a) for a in (spec.get("args") or [])]
        explicit = (self.paths.get(action_id) or {}).get("path") or spec.get("path")
        if explicit and os.path.isfile(str(explicit)):
            return [str(explicit), *args]
        key = {"win32": "win", "darwin": "mac"}.get(sys.platform, "linux")
        for cand in spec.get(key, []) or []:
            found = shutil.which(str(cand))
            if found:
                return [found, *args]
        fixed = spec.get("argv")          # 需要参数的程序（如 mac 的 open -a）
        if isinstance(fixed, list) and fixed:
            return [str(v) for v in fixed]
        return None

    def _running(self, action_id: str) -> bool:
        """单实例判定：只看我们自己拉起过的进程还在不在。"""
        info = self.procs.get(action_id)
        if not info:
            return False
        pid, started = info
        if pid <= 0:
            # 拿不到 pid 的平台：按「刚启动 60 秒内」近似，避免连点重复拉起
            return time.monotonic() - started < 60.0
        if sys.platform.startswith("win"):
            return time.monotonic() - started < 60.0
        try:
            os.kill(pid, 0)          # 只探测，不发送任何信号
            return True
        except OSError:
            self.procs.pop(action_id, None)
            return False

    def _activate(self, spec: dict):
        """已运行时的前置动作：配置了 argv 才执行，没配就只给气泡反馈。"""
        argv = spec.get("activate")
        if isinstance(argv, list) and argv:
            try:
                QProcess.startDetached(str(argv[0]), [str(v) for v in argv[1:]])
            except Exception:
                pass

    def _confirm(self, label: str) -> bool:
        if self.confirm_fn is not None:
            return bool(self.confirm_fn(label))
        from PyQt6.QtWidgets import QMessageBox
        r = QMessageBox.question(
            self.parent(), "桌宠动作",
            f"要{label}吗？", QMessageBox.StandardButton.Yes
            | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No)
        return r == QMessageBox.StandardButton.Yes
