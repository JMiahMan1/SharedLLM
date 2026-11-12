"""No homehub module may share a name with a standard-library module.

When the hub ran as a script, services/homehub/ was first on sys.path, so a
statistics.py there replaced Python's statistics module for every library:
reolink-aio's ``from statistics import mean`` failed and the cameras' whole
integration stopped loading.
"""
import os
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_no_module_shadows_the_standard_library():
    clashes = []
    for root in (HERE, os.path.join(HERE, "drivers")):
        for name in os.listdir(root):
            if name.endswith(".py") and name[:-3] in sys.stdlib_module_names:
                clashes.append(os.path.join(os.path.basename(root), name))
    assert clashes == []
