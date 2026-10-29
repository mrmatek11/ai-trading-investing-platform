"""Workery:  python -m tape <brief|reports|sync|econ_calendar|market> [--once | --every N]

Nie `python -m tape.brief`: wtedy plik działa jako `__main__`, a import_models() ładuje go drugi raz
jako `tape.brief` i jego tabele rejestrują się w Base.metadata podwójnie.
"""
import importlib
import sys

WORKERS = ("brief", "reports", "sync", "econ_calendar", "market")

if len(sys.argv) < 2 or sys.argv[1] not in WORKERS:
    sys.exit(f"użycie: python -m tape {{{'|'.join(WORKERS)}}} [--once | --every N]")
importlib.import_module(f"tape.{sys.argv[1]}").main(sys.argv[2:])
