"""AI 生成动作参数 / 表情的 Qt-free 契约。

职责边界：
- 只读配置、构造请求、解析并夹取提案；**不写盘、不碰 manifest**；
- 提案 → ChangeSet 在 ``ai_motion_adapter``；
- 真正落盘仍只走 ``upgrade_core.commit_changes()``。

默认零网络：只有用户点「AI 生成参数…」才构造请求；配置缺 base_url /
model / 环境变量时在发请求前直接失败。
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

from manifest_io import SWAY_DEFAULTS, SWAY_RANGES

DEFAULT_KEY_ENV = "DESKTOP_PET_AI_MOTION_KEY"
DEFAULT_TIMEOUT_MS = 30000
DEFAULT_TEMPERATURE = 0.6
DEFAULT_MAX_TOKENS = 1600

# 九项动作参数的唯一口径：与 ui_app 动作参数页 / anim.py 消费的键一一对应。
# plat 在 manifest 里存 0.1~20.0（界面显示 10%~2000%）。
ANIM_RANGES = {
    "hair_speed": (0.0, 5.0),
    "hair_amp": (0.0, 3.0),
    "hair_stiff": (0.0, 3.0),
    "hair_damp": (0.0, 3.0),
    "sway_deg": (0.0, 8.0),
    "sway_speed": (0.0, 3.0),
    "bounce": (0.0, 1.0),
    "fall": (0.0, 3.0),
    "plat": (0.1, 20.0),
}

ANIM_DEFAULTS = {
    "hair_speed": 1.0, "hair_amp": 1.0, "hair_stiff": 1.0, "hair_damp": 1.0,
    "sway_deg": 2.4, "sway_speed": 1.0, "bounce": 0.45, "fall": 1.0,
    "plat": 0.6,
}

EXPRESSION_STATES = ("joy", "sleepy")


class AIGenError(RuntimeError):
    """AI 生成失败：配置缺失 / 网络 / 协议 / 提案无效。入口应整体回手动。"""


@dataclass(frozen=True)
class AIMotionConfig:
    """独立配置源 ``ai_motion.json``；不写死 host / 模型 / key。

    ``api_key`` 是模型接口弹窗保存的本地密钥（文件已 gitignore，权限 0600）；
    环境变量优先于它，便于 CI / 临时覆盖。
    """

    base_url: str = ""
    model: str = ""
    key_env: str = DEFAULT_KEY_ENV
    timeout_ms: int = DEFAULT_TIMEOUT_MS
    json_mode: bool = True
    thinking: bool = False
    temperature: float = DEFAULT_TEMPERATURE
    max_tokens: int = DEFAULT_MAX_TOKENS
    api_key: str = ""


@dataclass
class AIMotionRequest:
    """一次请求的全部输入；只带标注几何与当前值，不带本地路径 / key。"""

    sku: str
    stem: str
    width: int = 0
    height: int = 0
    eyes: list[dict] = field(default_factory=list)
    hair_count: int = 0
    cloth_count: int = 0
    anim: dict = field(default_factory=dict)
    sway: dict = field(default_factory=dict)        # {"hair": [...], "cloth": [...]}
    expressions: list[str] = field(default_factory=list)


@dataclass
class AIMotionProposal:
    """已夹取的提案：九项 anim + 逐区域 sway + 封闭的 joy/sleepy 表情参数。"""

    anim: dict = field(default_factory=dict)
    sway: dict = field(default_factory=dict)
    expressions: dict = field(default_factory=dict)  # 状态 -> 渲染参数
    warnings: list[str] = field(default_factory=list)


def default_config_path() -> Path:
    return Path(__file__).with_name("ai_motion.json")


def load_config(path=None) -> AIMotionConfig:
    """读本地配置；文件不存在时返回空配置（发请求前会明确失败）。"""
    p = Path(path) if path else default_config_path()
    if not p.exists():
        return AIMotionConfig()
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception as exc:                 # noqa: BLE001
        raise AIGenError(f"ai_motion.json 解析失败：{type(exc).__name__}") from exc
    if isinstance(data, dict) and isinstance(data.get("ai_motion"), dict):
        data = data["ai_motion"]
    if not isinstance(data, dict):
        raise AIGenError("ai_motion.json 顶层必须是对象")

    def _int(key: str, default: int) -> int:
        val = data.get(key, default)
        return val if isinstance(val, int) and not isinstance(val, bool) else default

    def _float(key: str, default: float) -> float:
        val = data.get(key, default)
        return float(val) if isinstance(val, (int, float)) and not isinstance(
            val, bool) else default

    return AIMotionConfig(
        base_url=str(data.get("base_url") or "").strip(),
        model=str(data.get("model") or "").strip(),
        key_env=str(data.get("key_env") or DEFAULT_KEY_ENV),
        timeout_ms=max(1, _int("timeout_ms", DEFAULT_TIMEOUT_MS)),
        json_mode=bool(data.get("json_mode", True)),
        thinking=bool(data.get("thinking", False)),
        temperature=max(0.0, min(2.0, _float("temperature", DEFAULT_TEMPERATURE))),
        max_tokens=max(1, _int("max_tokens", DEFAULT_MAX_TOKENS)),
        api_key=str(data.get("api_key") or "").strip(),
    )


def save_config(cfg: AIMotionConfig, path=None) -> Path:
    """把模型接口写回本地 ``ai_motion.json``；文件权限收紧到 0600。

    只写这一份配置，不碰 manifest / assets；密钥不打印、不进日志。
    """
    p = Path(path) if path else default_config_path()
    data = {
        "base_url": cfg.base_url.strip(),
        "model": cfg.model.strip(),
        "key_env": cfg.key_env or DEFAULT_KEY_ENV,
        "api_key": cfg.api_key.strip(),
        "timeout_ms": int(cfg.timeout_ms),
        "json_mode": bool(cfg.json_mode),
        "thinking": bool(cfg.thinking),
        "temperature": float(cfg.temperature),
        "max_tokens": int(cfg.max_tokens),
    }
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(f".{p.name}.tmp")
    try:
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n",
                       encoding="utf-8")
        try:
            os.chmod(tmp, 0o600)
        except OSError:
            pass
        os.replace(tmp, p)
    except Exception:
        if tmp.exists():
            tmp.unlink()
        raise
    return p


def resolve_api_key(cfg: AIMotionConfig, env=None) -> str:
    """密钥解析：环境变量优先，其次模型接口弹窗保存的本地值。"""
    env = os.environ if env is None else env
    key_env = cfg.key_env or DEFAULT_KEY_ENV
    return (str(env.get(key_env, "")).strip()
            or str(cfg.api_key or "").strip())


def validate_config(cfg: AIMotionConfig, env=None) -> list[str]:
    """请求前检查；返回空列表表示可以发请求。"""
    issues: list[str] = []
    if not cfg.base_url.strip():
        issues.append("缺 base_url")
    if not cfg.model.strip():
        issues.append("缺 model")
    if not resolve_api_key(cfg, env=env):
        key_env = cfg.key_env or DEFAULT_KEY_ENV
        issues.append(f"未填 API 密钥（弹窗里填写，或设置环境变量 {key_env}）")
    return issues


def ensure_ready(cfg: AIMotionConfig, env=None) -> None:
    """发请求前的唯一闸门：未就绪直接抛错，保证零网络。"""
    issues = validate_config(cfg, env=env)
    if issues:
        raise AIGenError("；".join(issues))


def _fit(value, lo: float, hi: float, default: float) -> float:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return float(default)
    if v != v:                                # NaN
        return float(default)
    return max(lo, min(hi, v))


def _clamp_dir(value):
    if value is None:
        return None
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    if v != v:
        return None
    return round(v % 360.0, 1)


def _sway_list(raw, current, count: int) -> list[dict]:
    """把 AI 的逐区域参数夹到 SWAY_RANGES；缺项用当前值 / 默认值补齐。"""
    current = current if isinstance(current, list) else []
    raw = raw if isinstance(raw, list) else []
    amp_lo, amp_hi = SWAY_RANGES["amp"]
    bias_lo, bias_hi = SWAY_RANGES["bias"]
    out: list[dict] = []
    for i in range(max(0, int(count))):
        cur = current[i] if i < len(current) and isinstance(current[i], dict) else {}
        item = raw[i] if i < len(raw) and isinstance(raw[i], dict) else {}
        amp = _fit(item.get("amp"), amp_lo, amp_hi,
                   _fit(cur.get("amp"), amp_lo, amp_hi, SWAY_DEFAULTS["amp"]))
        bias = _fit(item.get("bias"), bias_lo, bias_hi,
                    _fit(cur.get("bias"), bias_lo, bias_hi, SWAY_DEFAULTS["bias"]))
        d = item.get("dir", cur.get("dir"))
        out.append({"amp": round(amp, 2), "dir": _clamp_dir(d),
                    "bias": round(bias, 1)})
    return out


def _clamp_expressions(raw) -> dict:
    """只接受封闭的 joy / sleepy；渲染参数复用 smile_geometry 的同一区间。"""
    if not isinstance(raw, dict):
        return {}
    from completers.smile_geometry import _sanitize

    out: dict = {}
    for state in EXPRESSION_STATES:
        params = raw.get(state)
        if isinstance(params, dict):
            out[state] = _sanitize(params)
    return out


def parse_proposal(raw, request: AIMotionRequest) -> AIMotionProposal:
    """严格 JSON 对象 → 已夹取提案；结构不对直接失败。"""
    if not isinstance(raw, dict):
        raise AIGenError("AI 返回不是 JSON 对象")

    anim_raw = raw.get("anim")
    anim_raw = anim_raw if isinstance(anim_raw, dict) else {}
    anim: dict = {}
    for key, (lo, hi) in ANIM_RANGES.items():
        cur = request.anim.get(key, ANIM_DEFAULTS[key])
        anim[key] = round(_fit(anim_raw.get(key, cur), lo, hi, ANIM_DEFAULTS[key]), 3)
    anim["plat"] = round(max(0.1, anim["plat"]), 2)

    sway_raw = raw.get("sway") if isinstance(raw.get("sway"), dict) else {}
    cur_sway = request.sway if isinstance(request.sway, dict) else {}
    sway = {
        "hair": _sway_list(sway_raw.get("hair"), cur_sway.get("hair"),
                           request.hair_count),
        "cloth": _sway_list(sway_raw.get("cloth"), cur_sway.get("cloth"),
                            request.cloth_count),
    }

    warnings: list[str] = []
    expr_raw = raw.get("expressions")
    if isinstance(expr_raw, dict):
        unknown_expr = [k for k in expr_raw if k not in EXPRESSION_STATES]
        if unknown_expr:
            warnings.append("已忽略未开放的表情状态：" + "、".join(map(str, unknown_expr)))
    expressions = _clamp_expressions(raw.get("expressions"))
    if not expressions:
        warnings.append("AI 未给出 joy / sleepy，表情部分保持原样")

    return AIMotionProposal(anim=anim, sway=sway, expressions=expressions,
                            warnings=warnings)


def parse_json_content(text: str) -> dict:
    """从模型输出里取严格 JSON：容忍 ```json 围栏，其余一律报错。"""
    if not isinstance(text, str) or not text.strip():
        raise AIGenError("模型响应为空")
    s = text.strip()
    if s.startswith("```"):
        s = s.split("\n", 1)[1] if "\n" in s else ""
        if s.rstrip().endswith("```"):
            s = s.rstrip()[:-3]
        elif "```" in s:
            s = s[:s.rindex("```")]
        s = s.strip()
    if not s.startswith("{"):
        i, j = s.find("{"), s.rfind("}")
        if i < 0 or j <= i:
            raise AIGenError("模型响应里没有 JSON 对象")
        s = s[i:j + 1]
    try:
        data = json.loads(s)
    except ValueError as exc:
        raise AIGenError("模型响应不是合法 JSON") from exc
    if not isinstance(data, dict):
        raise AIGenError("模型响应不是 JSON 对象")
    return data


_SYSTEM_PROMPT = """你是桌面宠物动作参数调参助手。只输出一个 JSON 对象，不要解释、不要 Markdown。
对象结构必须严格为：
{"anim": {九项}, "sway": {"hair": [逐区域], "cloth": [逐区域]},
 "expressions": {"joy": 参数, "sleepy": 参数}}
anim 九项与范围：
hair_speed 0~5, hair_amp 0~3, hair_stiff 0~3, hair_damp 0~3,
sway_deg 0~8, sway_speed 0~3, bounce 0~1, fall 0~3, plat 0.1~20。
sway 每项 {"amp": 0~3, "dir": 0~359 或 null, "bias": -45~45}。
expressions 只允许 joy / sleepy；参数为
{"lid_drop": 0~0.45, "lid_sag": 0~0.25, "arc_ratio": 0~1, "blush_alpha": 0~160}。
joy = 开心（眯眼 + 嘴角弧 + 腮红），sleepy = 困倦（上眼睑压低、嘴角弧接近 0、少腮红）。
不要发明新的状态名，不要改 pivot / tip / mask 等几何字段。"""


def build_messages(request: AIMotionRequest) -> list[dict]:
    """chat/completions 的 messages；只带调参需要的几何与当前值。"""
    payload = {
        "sku": request.sku,
        "stem": request.stem,
        "width": request.width,
        "height": request.height,
        "eyes": request.eyes,
        "hair_regions": request.hair_count,
        "cloth_regions": request.cloth_count,
        "current_anim": request.anim,
        "current_sway": request.sway,
        "existing_expression_overlays": request.expressions,
    }
    return [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
    ]


def propose_motion(provider, request: AIMotionRequest, config: AIMotionConfig,
                   env=None) -> AIMotionProposal:
    """统一入口：配置闸门 → provider.propose → 严格解析 / 夹取。"""
    ensure_ready(config, env=env)
    raw = provider.propose(request, config)
    return parse_proposal(raw, request)
