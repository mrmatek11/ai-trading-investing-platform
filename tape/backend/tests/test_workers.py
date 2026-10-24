"""Workery startują tak jak w docker-compose: osobny proces, `python -m tape <worker>`."""

import os
import subprocess
import sys

import pytest


@pytest.mark.parametrize("worker", ["brief", "reports", "sync", "econ_calendar"])   # market --once idzie do sieci
def test_worker_starts_as_process(worker, tmp_path):
    env = {**os.environ, "DATABASE_URL": f"sqlite:///{tmp_path / 'w.db'}", "TAPE_PRICE_PROVIDER": ""}
    env = {k: v for k, v in env.items() if not k.startswith(("TAPE_TELEGRAM", "TAPE_SMTP", "ANTHROPIC", "DEEPSEEK"))}
    r = subprocess.run([sys.executable, "-m", "tape", worker, "--once"], env=env, capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr[-2000:]
