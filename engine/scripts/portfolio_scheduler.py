#!/usr/bin/env python3
"""Portfolio Scheduler — runs daily tasks on a timer.

For systems without cron (containers, shared compute, etc.).
Runs as a long-lived background process.

Usage:
    # Start scheduler (background)
    nohup python3 portfolio_scheduler.py &

    # Or with specific times
    python3 portfolio_scheduler.py --notify-time 18:00 --pipeline-time 19:00
"""

import os, sys, time, signal, argparse, json
from datetime import datetime, timedelta
from pathlib import Path
import subprocess
import threading

BASE_DIR = Path(__file__).parent
PID_FILE = BASE_DIR.parent / "logs" / "scheduler.pid"
LOG_DIR = BASE_DIR.parent / "logs"
import shutil as _shutil
CONDA = os.environ.get("CONDA_EXE") or _shutil.which("conda") or "conda"

# Track what we've done today to avoid re-running
daily_state = {
    "notified_date": None,
    "pipeline_date": None,
}

running = True


def log(msg):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    try:
        log_file = LOG_DIR / f"scheduler-{datetime.now().strftime('%Y%m%d')}.log"
        with open(log_file, "a") as f:
            f.write(line + "\n")
    except:
        pass


def is_trading_day():
    """Check if today is a trading day (weekday, not Chinese holiday)."""
    now = datetime.now()
    if now.weekday() >= 5:  # Saturday=5, Sunday=6
        return False
    
    # Chinese holidays 2026 (update annually)
    holidays = {
        "2026-01-01", "2026-01-26", "2026-01-27", "2026-01-28", "2026-01-29", "2026-01-30",
        "2026-02-16", "2026-02-17", "2026-05-01", "2026-05-02", "2026-05-05",
        "2026-06-19", "2026-10-01", "2026-10-02", "2026-10-05", "2026-10-06", "2026-10-07"
    }
    return now.strftime("%Y-%m-%d") not in holidays


def run_action(action, date_str=None):
    """Run a portfolio action via the update script."""
    if date_str is None:
        date_str = datetime.now().strftime("%Y-%m-%d")
    
    script = str(BASE_DIR / "portfolio_daily_update.py")
    cmd = [CONDA, "run", "-n", "quant", "python3", script, "--action", action, "--date", date_str]
    
    log(f"执行: {action} (日期: {date_str})")
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=180,
            cwd=str(BASE_DIR)
        )
        if result.stdout:
            for line in result.stdout.strip().split('\n'):
                log(f"  {line}")
        if result.returncode != 0 and result.stderr:
            log(f"  ⚠️ stderr: {result.stderr[:300]}")
        return result.returncode == 0
    except Exception as e:
        log(f"  ❌ 异常: {e}")
        return False


def check_and_run(notify_hour, notify_min, pipeline_hour, pipeline_min):
    """Check current time and run appropriate actions."""
    now = datetime.now()
    today = now.strftime("%Y-%m-%d")
    
    if not is_trading_day():
        return
    
    current_time = now.hour * 60 + now.minute
    notify_time = notify_hour * 60 + notify_min
    pipeline_time = pipeline_hour * 60 + pipeline_min
    
    # Window: run within 5 minutes of target time
    if abs(current_time - notify_time) <= 2 and daily_state["notified_date"] != today:
        log(f"⏰ 触发通知任务 (18:00)")
        daily_state["notified_date"] = today
        run_action("notify", today)
    
    if abs(current_time - pipeline_time) <= 2 and daily_state["pipeline_date"] != today:
        log(f"⏰ 触发自动管道 (19:00)")
        daily_state["pipeline_date"] = today
        run_action("auto-pipeline", today)


def signal_handler(sig, frame):
    global running
    log("收到停止信号，正在退出...")
    running = False


def main():
    parser = argparse.ArgumentParser(description="Portfolio daily scheduler")
    parser.add_argument("--notify-time", default="18:00", help="Notify time (HH:MM, Beijing time)")
    parser.add_argument("--pipeline-time", default="19:00", help="Auto-pipeline time (HH:MM, Beijing time)")
    parser.add_argument("--check-interval", type=int, default=60, help="Check interval in seconds")
    args = parser.parse_args()
    
    notify_h, notify_m = map(int, args.notify_time.split(":"))
    pipeline_h, pipeline_m = map(int, args.pipeline_time.split(":"))
    
    # Write PID file
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    PID_FILE.write_text(str(os.getpid()))
    
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    log(f"🚀 Portfolio Scheduler 启动")
    log(f"   通知时间: {args.notify_time} | 自动管道: {args.pipeline_time}")
    log(f"   检查间隔: {args.check_interval}s | PID: {os.getpid()}")
    
    while running:
        try:
            check_and_run(notify_h, notify_m, pipeline_h, pipeline_m)
        except Exception as e:
            log(f"❌ 调度异常: {e}")
        
        # Sleep in small intervals so we can respond to signals
        for _ in range(args.check_interval):
            if not running:
                break
            time.sleep(1)
    
    # Cleanup
    if PID_FILE.exists():
        PID_FILE.unlink()
    log("Scheduler 已停止")


if __name__ == "__main__":
    main()
