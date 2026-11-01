"""小红书扫码登录：终端里出二维码，扫完把 cookie 存下来供 sidecar 使用。

用法：
  python scripts/xhs_login.py

产物：.local-artifacts/xhs-sidecar/cookie.txt
之后启动 sidecar：
  python scripts/xhs_sidecar.py --port 8001
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
XHS_ROOT = ROOT / ".local-artifacts" / "xhs-sidecar"
if str(XHS_ROOT) not in sys.path:
    sys.path.insert(0, str(XHS_ROOT))


def main() -> None:
    if not XHS_ROOT.exists():
        raise SystemExit(f"未找到 Spider_XHS：{XHS_ROOT}")

    from xhs_utils.xhs_pc.auth import XHSPcAuth

    print("即将在终端打印二维码，请用小红书 App 扫码（Ctrl+C 取消）")
    auth = XHSPcAuth.from_qrcode_login(show_in_terminal=True)
    cookie = str(getattr(auth, "cookies", "") or "").strip()
    if "web_session=" not in cookie:
        raise SystemExit("登录未完成：cookie 里没有 web_session")

    target = XHS_ROOT / "cookie.txt"
    target.write_text(cookie, encoding="utf-8")
    print(f"已保存登录态：{target}（{len(cookie)} 字节）")
    print("下一步：python scripts/xhs_sidecar.py --port 8001")


if __name__ == "__main__":
    main()
