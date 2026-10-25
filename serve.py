"""Azure Web App（Windows）啟動入口：由 web.config 的 ASP.NET Core Module 呼叫。

ASP.NET Core Module 會把要監聽的連接埠放在 ``ASPNETCORE_PORT``
（舊的 HttpPlatformHandler 則是 ``HTTP_PLATFORM_PORT``），
本機測試時未設定則使用 8501：
    python serve.py
"""

import os
import sys
from pathlib import Path

from streamlit.web import cli as stcli

APP_PATH = Path(__file__).with_name("app.py")


def main() -> None:
    port = os.environ.get("ASPNETCORE_PORT") or os.environ.get("HTTP_PLATFORM_PORT", "8501")
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
