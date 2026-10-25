"""Azure Web App（Windows）啟動入口：由 web.config 的 HttpPlatformHandler 呼叫。

HttpPlatformHandler 會把要監聽的連接埠放在 ``HTTP_PLATFORM_PORT``，
本機測試時未設定則使用 8501：
    python serve.py
"""

import os
import sys
from pathlib import Path

from streamlit.web import cli as stcli

APP_PATH = Path(__file__).with_name("app.py")


def main() -> None:
    port = os.environ.get("HTTP_PLATFORM_PORT", "8501")
    sys.argv = [
        "streamlit",
        "run",
        str(APP_PATH),
        "--server.port",
        port,
        "--server.headless",
        "true",
        "--server.fileWatcherType",
        "none",
        "--browser.gatherUsageStats",
        "false",
    ]
    sys.exit(stcli.main())


if __name__ == "__main__":
    main()
