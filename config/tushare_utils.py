"""
Tushare 公共工具：pro_api 工厂、代理清理、代码格式转换。

所有 sync_*.py 通过此模块获取 pro_api 实例，确保代理清理和 Token
读取逻辑只存在一处。
"""

import os
import tushare as ts
from config.local import TUSHARE_TOKEN


def clear_proxy():
    """清除 HTTP/HTTPS 代理环境变量（Tushare 直连不走代理）。"""
    for key in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY"):
        os.environ.pop(key, None)


def get_pro() -> ts.pro_api:
    """
    获取已认证的 Tushare Pro API 实例。

    每次调用返回新实例（线程安全），自动清理代理。
    """
    clear_proxy()
    return ts.pro_api(TUSHARE_TOKEN)


def code_to_ts_code(code: str) -> str:
    """
    6 位纯数字代码 → Tushare 格式（带交易所后缀）。

    规则:
      6xxxxx → .SH
      0xxxxx / 3xxxxx → .SZ
      9xxxxx → .BJ
      其他  → .SZ（兜底）

    示例: '000001' → '000001.SZ', '600000' → '600000.SH'
    """
    c = code.zfill(6)
    if c[0] == "6":
        return c + ".SH"
    if c[0] in ("0", "3"):
        return c + ".SZ"
    if c[0] == "9":
        return c + ".BJ"
    return c + ".SZ"
