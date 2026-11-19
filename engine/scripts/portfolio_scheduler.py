#!/usr/bin/env python3
"""Portfolio Scheduler — runs daily tasks on a timer.

For systems without cron (containers, shared compute, etc.).
Runs as a long-lived background process.

Usage:
    # Start scheduler (background)
    nohup python3 portfolio_scheduler.py &

    # Or with specific times (the machine's local time, like the dates it uses)
    python3 portfolio_scheduler.py --notify-time 18:00 --pipeline-time 19:00

    # Optional: run the daily scripts inside a conda environment
    CONDA_ENV=myenv python3 portfolio_scheduler.py

The daily scripts run with the interpreter that started the scheduler unless
CONDA_ENV is set (then ``conda run -n $CONDA_ENV python3``; CONDA_EXE selects
the conda executable).
"""

import os, sys, time, signal, argparse, json
from datetime import datetime, timedelta
from pathlib import Path
import subprocess
import threading

BASE_DIR = Path(__file__).parent
PID_FILE = BASE_DIR.parent / "logs" / "scheduler.pid"
LOG_DIR = BASE_DIR.parent / "logs"
from portfolio_daily_update import python_command

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


# Weekday closures of the Shanghai Stock Exchange in 2026, from its
# notice of 2025-12-22 (上证公告〔2025〕45号): 元旦 1/1-1/3, 春节 2/15-2/23,
# 清明 4/4-4/6, 劳动节 5/1-5/5, 端午 6/19-6/21, 中秋 9/25-9/27, 国庆 10/1-10/7.
# Update annually from the exchange notice, together with the same list in
# portfolio-cron.sh and portfolio-daily.sh. Dates outside this table are treated
# as trading days when they fall on a weekday.
HOLIDAYS = {
    "2026-01-01", "2026-01-02",
    "2026-02-16", "2026-02-17", "2026-02-18", "2026-02-19", "2026-02-20", "2026-02-23",
    "2026-04-06",
    "2026-05-01", "2026-05-04", "2026-05-05",
    "2026-06-19",
    "2026-09-25",
    "2026-10-01", "2026-10-02", "2026-10-05", "2026-10-06", "2026-10-07",
}


def is_trading_day(now=None):
    """Check if a day (default: today, local time) is a weekday and not an exchange holiday."""
    now = now or datetime.now()
    if now.weekday() >= 5:  # Saturday=5, Sunday=6
        return False
    return now.strftime("%Y-%m-%d") not in HOLIDAYS


def run_action(action, date_str=None):
    """Run a portfolio action via the update script."""
    if date_str is None:
        date_str = datetime.now().strftime("%Y-%m-%d")
    
    script = str(BASE_DIR / "portfolio_daily_update.py")
    # Current interpreter by default; ``conda run -n $CONDA_ENV`` when CONDA_ENV is set.
    cmd = [*python_command(), script, "--action", action, "--date", date_str]
    
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
    
    # Window: from 2 minutes before to 2 minutes after the target time, inclusive
    # (each task still runs at most once per day).
    if abs(current_time - notify_time) <= 2 and daily_state["notified_date"] != today:
        log(f"⏰ 触发通知任务 ({notify_hour:02d}:{notify_min:02d})")
        daily_state["notified_date"] = today
        run_action("notify", today)
    
    if abs(current_time - pipeline_time) <= 2 and daily_state["pipeline_date"] != today:
        log(f"⏰ 触发自动管道 ({pipeline_hour:02d}:{pipeline_min:02d})")
        daily_state["pipeline_date"] = today
        run_action("auto-pipeline", today)


def signal_handler(sig, frame):
    global running
    log("收到停止信号，正在退出...")
    running = False


def main():
    parser = argparse.ArgumentParser(description="Portfolio daily scheduler")
    parser.add_argument("--notify-time", default="18:00",
                        help="Notify time (HH:MM, local time of this machine)")
    parser.add_argument("--pipeline-time", default="19:00",
                        help="Auto-pipeline time (HH:MM, local time of this machine)")
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
