import os
import sys


SERVICES_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if SERVICES_DIR not in sys.path:
    sys.path.insert(0, SERVICES_DIR)
