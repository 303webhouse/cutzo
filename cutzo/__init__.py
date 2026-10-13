"""Cutzo: a streamlined podcast and video editor for Nick's shows.

Layout:
  cutzo.core         every action as a plain Python method on `Cutzo`
  cutzo.engine       the local web server (browser screens + JSON routes)
  cutzo.mcp_server   the Claude connector (talks to the engine)
  cutzo.launcher     what the desktop shortcut runs
"""

__version__ = "0.1.0"
PHASE = 1
