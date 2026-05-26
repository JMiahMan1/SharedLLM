# test/debug_import.py
import sys
import os

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

print("Importing settings...")
import app.settings  # pyright: ignore[reportUnusedImport]
print("Settings imported.")

print("Importing timer_ops...")
import app.logic.timer_ops  # pyright: ignore[reportUnusedImport,reportMissingImports]
print("timer_ops imported.")
