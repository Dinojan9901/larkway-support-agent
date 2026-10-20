"""Launch the Larkway MCP server from any working directory.

MCP clients start servers from wherever they happen to be, so this puts the repo on
the import path first. Register it with the project's own Python:

    claude mcp add --transport stdio larkway -- <repo>/.venv/Scripts/python.exe <repo>/scripts/mcp_server.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.mcp_server import main  # noqa: E402

if __name__ == "__main__":
    main()
