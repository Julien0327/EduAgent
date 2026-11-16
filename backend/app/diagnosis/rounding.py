"""确定性四舍五入（Phase 3 / S1，D4）。

掌握度/占比等数值统一用四舍五入（round half up），不依赖 Python 内置
``round()`` 的银行家舍入（banker's rounding）行为。
"""
from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP


def round_half_up(x, ndigits: int = 0) -> float | None:
    """四舍五入到 ndigits 位小数；None 原样返回。

    2.675 -> 2.68（Python round 会给 2.67）；0.5->1；1.5->2。
    """
    if x is None:
        return None
    if isinstance(x, bool):
        return float(x)
    q = Decimal(1).scaleb(-ndigits)
    return float(Decimal(str(x)).quantize(q, rounding=ROUND_HALF_UP))
