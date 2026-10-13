"""Runs Cutzo with Cutzo's own Python and no console window.

    desktop shortcut:  <Cutzo's pythonw.exe> run.pyw          (opens Cutzo in the browser)
    background task:   <Cutzo's pythonw.exe> run.pyw engine   (just the engine)

"Cutzo's pythonw.exe" is the real one in .python. The pythonw.exe inside .venv is a
small launcher that, with this Python build, opens a black console window, so it is
skipped and the .venv's packages are added here instead.
"""

import os
import runpy
import site
import sys

APP = os.path.dirname(os.path.abspath(__file__))
VENV = os.path.join(APP, ".venv")

if os.path.normcase(os.path.abspath(sys.prefix)) != os.path.normcase(VENV):
    site.addsitedir(os.path.join(VENV, "Lib", "site-packages"))
if APP not in sys.path:
    sys.path.insert(0, APP)

if sys.argv[1:2] == ["engine"]:
    target, rest = "cutzo.engine", sys.argv[2:]
else:
    target, rest = "cutzo", sys.argv[1:]
sys.argv = [target, *rest]
runpy.run_module(target, run_name="__main__", alter_sys=True)
