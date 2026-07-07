"""统一数据同步入口 — 按依赖拓扑顺序调用 5 个 sync_*.py，打印汇总表。

使用方式：
  python scripts/sync_runner.py                    # 增量（今天）
  python scripts/sync_runner.py --full             # 全量初始化
  python scripts/sync_runner.py --date 20260701    # 补拉指定日期
"""

import argparse
import subprocess
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = PROJECT_ROOT / "scripts"

# ═══════════════════════════════════════════════════════════════════
# 常量
# ═══════════════════════════════════════════════════════════════════

TOPOLOGY_ORDER = ["sync_trade_cal", "sync_stocks", "sync_st", "sync_indices", "sync_kline", "sync_delist"]
# sync_kline 始终依赖 sync_stocks；sync_delist 仅 --full 依赖 sync_st
DEPENDENCIES = {"sync_kline": ["sync_stocks"], "sync_delist": ["sync_st"]}


# ═══════════════════════════════════════════════════════════════════
# 数据结构
# ═══════════════════════════════════════════════════════════════════

class ScriptResult:
    def __init__(self, name: str):
        self.name = name
        self.status = "skipped"       # "success" | "failed" | "skipped"
        self.exit_code = -1
        self.elapsed = 0.0
        self.last_line = ""
        self.skip_reason = ""
        self.stdout = ""


# ═══════════════════════════════════════════════════════════════════
# CLI 参数解析
# ═══════════════════════════════════════════════════════════════════

def _validate_date_arg(s: str) -> str:
    if len(s) != 8 or not s.isdigit():
        raise argparse.ArgumentTypeError(f"日期格式必须为 YYYYMMDD，收到: {s!r}")
    return s


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="统一数据同步入口：自动处理依赖顺序")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--full", action="store_true")
    group.add_argument("--date", type=_validate_date_arg, default=None,
                       help="YYYYMMDD（增量模式，默认今天）")
    return parser.parse_args()


# ═══════════════════════════════════════════════════════════════════
# 依赖与调度
# ═══════════════════════════════════════════════════════════════════

def _build_args(name: str, full: bool, date_val: str | None) -> list[str]:
    """根据脚本名和运行模式构建 subprocess 参数列表。sync_stocks 无 --date。"""
    if full:
        return ["--full"]
    if name == "sync_stocks":
        return []
    if date_val is not None:
        return ["--date", date_val]
    return []


def _failed_dep(name: str, results: dict, full: bool) -> str | None:
    """检查上游依赖是否已失败/跳过/未执行，返回第一个问题依赖名或 None。"""
    deps = DEPENDENCIES.get(name, [])
    if name == "sync_delist" and not full:
        return None
    for d in deps:
        r = results.get(d)
        if r is None or r.status in ("failed", "skipped"):
            return d
    return None


def run_one(name: str, args: list[str], full: bool) -> ScriptResult:
    """通过 subprocess 运行单个 sync_*.py，返回 ScriptResult。

    流式输出：每行 stdout/stderr 实时打印到父进程 stdout，确保 nohup 日志即时可见。
    成功/失败/超时/异常均在此处理。
    """
    r = ScriptResult(name)
    cmd = [sys.executable, str(SCRIPTS_DIR / f"{name}.py")] + args
    # sync_kline 即使增量模式也需逐只轮询 5k+ 股票（~20-30分钟），单独给更长超时
    per_script_timeout = {"sync_kline": 3600}
    timeout = per_script_timeout.get(name, 7200 if full else 600)

    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    print(f"\n{'─' * 60}\n>>> [{name}] {ts} 开始执行\n    cmd: {' '.join(cmd)}\n{'─' * 60}", flush=True)

    try:
        t0 = time.perf_counter()
        proc = subprocess.Popen(
            cmd, cwd=str(PROJECT_ROOT),
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, bufsize=1,
        )

        stdout_lines: list[str] = []
        assert proc.stdout is not None
        for line in proc.stdout:
            print(line, end="", flush=True)
            stdout_lines.append(line)

        proc.wait(timeout=timeout)
        r.elapsed = time.perf_counter() - t0
        r.exit_code = proc.returncode
        r.stdout = "".join(stdout_lines)

        if proc.returncode == 0:
            r.status = "success"
            for line in reversed(stdout_lines):
                s = line.strip()
                if s.startswith("完成"):
                    r.last_line = s[:80]
                    break
            if not r.last_line and stdout_lines:
                r.last_line = stdout_lines[-1].strip()[:80]
        else:
            r.status = "failed"
            r.last_line = next(
                (l.strip()[:80] for l in reversed(stdout_lines) if l.strip()),
                f"exit_code={proc.returncode}",
            )

        ts_end = time.strftime("%Y-%m-%d %H:%M:%S")
        print(f"\n<<< [{name}] {ts_end} "
              f"{'✓ 成功' if r.status == 'success' else '✗ 失败'} "
              f"| 耗时 {r.elapsed:.1f}s | exit {r.exit_code} | "
              f"{r.last_line}", flush=True)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()
        r.elapsed, r.status, r.last_line = timeout, "failed", f"超时（>{timeout}s）"
        print(f"\n<<< [{name}] ✗ 超时（>{timeout}s）", flush=True)
    except KeyboardInterrupt:
        print("\n[runner] 收到中断信号", file=sys.stderr)
        raise
    except Exception as e:
        r.status, r.last_line = "failed", f"{type(e).__name__}: {e}"[:80]
        print(f"\n<<< [{name}] ✗ 异常: {e}", flush=True)

    return r


# ═══════════════════════════════════════════════════════════════════
# 汇总报告
# ═══════════════════════════════════════════════════════════════════

STATUS_ICONS = {"success": "V 成功", "failed": "X 失败", "skipped": "- 跳过"}


def _fmt_row(i: int, r: ScriptResult) -> str:
    icon = STATUS_ICONS[r.status]
    icon_col = icon + " " * (8 - len(icon))
    elapsed = " ---" if r.status == "skipped" else f"{r.elapsed:.1f}s"
    desc = r.skip_reason if r.status == "skipped" else r.last_line
    return f"  {i}  {r.name.ljust(18)}{icon_col}{elapsed.rjust(7)}    {desc}"


def print_summary(results: dict, full: bool) -> None:
    """打印汇总表：表头 → 逐行 ScriptResult → 成功率统计 → 分隔线。"""
    sep = "=" * 70
    print(f"\n{sep}\n  sync_runner 执行汇总 | 模式: {'全量初始化' if full else '增量更新'}\n{sep}")
    print("  #  脚本              状态      耗时       说明")
    print("-" * 70)
    for i, name in enumerate(TOPOLOGY_ORDER, 1):
        print(_fmt_row(i, results[name]))
    print("-" * 70)
    ok = sum(1 for r in results.values() if r.status == "success")
    fail = sum(1 for r in results.values() if r.status == "failed")
    print(f"  结果: {ok}/{len(TOPOLOGY_ORDER)} 成功, {fail} 失败\n{sep}")


# ═══════════════════════════════════════════════════════════════════
# 主入口
# ═══════════════════════════════════════════════════════════════════

def main() -> None:
    # Windows GBK 终端兼容：强制 stdout 使用 utf-8
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")

    args_ns = parse_args()
    full, date_val = args_ns.full, args_ns.date
    mode_str = "全量初始化" if full else f"增量更新 (date={date_val or '今天'})"
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    print(f"{'=' * 60}")
    print(f"  sync_runner 启动 | 模式: {mode_str} | {ts}")
    print(f"  Python: {sys.executable}")
    print(f"{'=' * 60}", flush=True)
    results: dict[str, ScriptResult] = {}

    for name in TOPOLOGY_ORDER:
        dep = _failed_dep(name, results, full)
        if dep is not None:
            r = ScriptResult(name)
            r.skip_reason = f"依赖 {dep} 失败，自动跳过"
            results[name] = r
            ts = time.strftime("%Y-%m-%d %H:%M:%S")
            print(f"  · [{name}] {ts} 跳过 — {r.skip_reason}", flush=True)
            continue

        r = run_one(name, _build_args(name, full, date_val), full)
        results[name] = r

    print_summary(results, full)
    all_ok = all(r.status == "success" for r in results.values())
    sys.exit(0 if all_ok else 1)


if __name__ == "__main__":
    main()
