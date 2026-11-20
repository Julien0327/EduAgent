"""确定性模板题库（Phase 6 / S2，KP 042–082）。

方案 C 的模板侧：算术、几何、单位换算、数论、统计与概率等有确定公式的
知识点，用参数化模板生成；每 KP 难度 1–5 各一题。模板答案由公式直接计算，
不依赖模型，天然可复现、可校验。

说明：模板题的语言相对规整，质量由 ``validation`` 的统一规则复核；
应用题类 KP（001–041）走 LLM 生成，见 ``generation.py``。
"""
from __future__ import annotations

import random

from backend.app.demo.models import DemoQuestion


def f(x: float) -> str:
    """数字转字符串：整数不带小数点，小数最多 4 位且去尾零。"""
    if abs(x - round(x)) < 1e-9:
        return str(int(round(x)))
    return f"{x:.4f}".rstrip("0").rstrip(".")


def _q(stem, answer, solution, *, value=None, unit=None, equation=None,
       qtype="解答题", options=None) -> dict:
    return {
        "stem": stem,
        "answer": answer,
        "solution": solution,
        "answer_value": value,
        "unit": unit,
        "equation": equation,
        "qtype": qtype,
        "options": list(options or []),
    }


# ---------------------------------------------------------------- 几何

def rect_perimeter(rng: random.Random, d: int) -> dict:
    if d <= 2:
        L, W = rng.randint(6, 12), rng.randint(2, 5)
        ans = 2 * (L + W)
        return _q(f"一个长方形长 {L} 厘米、宽 {W} 厘米，它的周长是多少厘米？",
                  f"{f(ans)} 厘米", f"（{L}+{W}）×2={f(ans)}（厘米）。",
                  value=ans, unit="厘米", equation=f"2*({L}+{W})")
    if d == 3:
        L, W = rng.randint(5, 9) + 0.5, rng.randint(2, 4) + 0.5
        ans = 2 * (L + W)
        return _q(f"一个长方形长 {f(L)} 厘米、宽 {f(W)} 厘米，它的周长是多少厘米？",
                  f"{f(ans)} 厘米", f"（{f(L)}+{f(W)}）×2={f(ans)}（厘米）。",
                  value=ans, unit="厘米", equation=f"2*({f(L)}+{f(W)})")
    if d == 4:
        W = rng.randint(3, 7)
        L = W + rng.randint(2, 5)
        P = 2 * (L + W)
        return _q(f"一个长方形长比宽多 {L - W} 厘米，周长是 {P} 厘米，长是多少厘米？",
                  f"{L} 厘米",
                  f"长+宽={P}÷2={P // 2}，长=({P // 2}+{L - W})÷2={L}（厘米）。",
                  value=L, unit="厘米", equation=f"({P}/2+{L - W})/2")
    W = rng.randint(3, 7)
    L = 2 * W
    P = 2 * (L + W)
    return _q(f"用一根长 {P} 厘米的铁丝围成一个长方形，长是宽的 2 倍。这个长方形的面积是多少平方厘米？",
              f"{L * W} 平方厘米",
              f"长+宽={P}÷2={P // 2}，宽={W}，长={L}，面积={L}×{W}={L * W}（平方厘米）。",
              value=L * W, unit="平方厘米", equation=f"{L}*{W}")


def square_perimeter(rng: random.Random, d: int) -> dict:
    if d <= 2:
        a = rng.randint(3, 12)
        return _q(f"一个正方形的边长是 {a} 厘米，它的周长是多少厘米？",
                  f"{4 * a} 厘米", f"正方形周长=边长×4={a}×4={4 * a}（厘米）。",
                  value=4 * a, unit="厘米", equation=f"4*{a}")
    if d == 3:
        a = rng.randint(4, 9) + 0.5
        return _q(f"一个正方形的边长是 {f(a)} 厘米，它的周长是多少厘米？",
                  f"{f(4 * a)} 厘米", f"{f(a)}×4={f(4 * a)}（厘米）。",
                  value=4 * a, unit="厘米", equation=f"4*{f(a)}")
    if d == 4:
        a = rng.randint(5, 10)
        P = 4 * a
        return _q(f"一个正方形的周长是 {P} 厘米，它的面积是多少平方厘米？",
                  f"{a * a} 平方厘米", f"边长={P}÷4={a}，面积={a}×{a}={a * a}（平方厘米）。",
                  value=a * a, unit="平方厘米", equation=f"{a}*{a}")
    a = rng.randint(3, 8)
    return _q(f"一个正方形的边长是 {a} 厘米，边长增加 2 厘米后，周长增加多少厘米？",
              "8 厘米", "边长增加 2 厘米，四条边共增加 2×4=8（厘米）。",
              value=8, unit="厘米", equation="2*4")


def rect_area(rng: random.Random, d: int) -> dict:
    if d <= 2:
        L, W = rng.randint(6, 14), rng.randint(2, 6)
        return _q(f"一个长方形长 {L} 厘米、宽 {W} 厘米，面积是多少平方厘米？",
                  f"{L * W} 平方厘米", f"{L}×{W}={L * W}（平方厘米）。",
                  value=L * W, unit="平方厘米", equation=f"{L}*{W}")
    if d == 3:
        L, W = rng.randint(5, 9) + 0.5, rng.randint(2, 4) + 0.5
        return _q(f"一个长方形长 {f(L)} 厘米、宽 {f(W)} 厘米，面积是多少平方厘米？",
                  f"{f(L * W)} 平方厘米", f"{f(L)}×{f(W)}={f(L * W)}（平方厘米）。",
                  value=L * W, unit="平方厘米", equation=f"{f(L)}*{f(W)}")
    if d == 4:
        W = rng.randint(4, 8)
        L = rng.randint(6, 12)
        A = L * W
        return _q(f"一个长方形的面积是 {A} 平方厘米，长是 {L} 厘米，宽是多少厘米？",
                  f"{W} 厘米", f"宽=面积÷长={A}÷{L}={W}（厘米）。",
                  value=W, unit="厘米", equation=f"{A}/{L}")
    L, W = rng.randint(8, 12), rng.randint(4, 7)
    return _q(f"一个长方形长 {L} 厘米、宽 {W} 厘米。如果长增加 2 厘米、宽不变，面积增加多少平方厘米？",
              f"{2 * W} 平方厘米",
              f"增加的面积=2×宽=2×{W}={2 * W}（平方厘米）。",
              value=2 * W, unit="平方厘米", equation=f"2*{W}")


def square_area(rng: random.Random, d: int) -> dict:
    if d <= 2:
        a = rng.randint(3, 12)
        return _q(f"一个正方形的边长是 {a} 厘米，面积是多少平方厘米？",
                  f"{a * a} 平方厘米", f"{a}×{a}={a * a}（平方厘米）。",
                  value=a * a, unit="平方厘米", equation=f"{a}*{a}")
    if d == 3:
        a = rng.randint(4, 9) + 0.5
        return _q(f"一个正方形的边长是 {f(a)} 厘米，面积是多少平方厘米？",
                  f"{f(a * a)} 平方厘米", f"{f(a)}×{f(a)}={f(a * a)}（平方厘米）。",
                  value=a * a, unit="平方厘米", equation=f"{f(a)}*{f(a)}")
    if d == 4:
        a = rng.randint(4, 9)
        P = 4 * a
        return _q(f"一个正方形的周长是 {P} 厘米，面积是多少平方厘米？",
                  f"{a * a} 平方厘米", f"边长={P}÷4={a}，面积={a}×{a}={a * a}（平方厘米）。",
                  value=a * a, unit="平方厘米", equation=f"{a}*{a}")
    a = rng.randint(3, 6)
    return _q(f"一个正方形的边长扩大到原来的 2 倍，面积扩大到原来的多少倍？",
              "4 倍", "边长扩大 2 倍，面积扩大 2×2=4 倍。",
              value=4, unit="倍", equation="2*2")


def triangle_area(rng: random.Random, d: int) -> dict:
    if d <= 2:
        b, h = rng.randint(4, 12), rng.randint(3, 8)
        ans = b * h / 2
        return _q(f"一个三角形的底是 {b} 厘米，高是 {h} 厘米，面积是多少平方厘米？",
                  f"{f(ans)} 平方厘米", f"底×高÷2={b}×{h}÷2={f(ans)}（平方厘米）。",
                  value=ans, unit="平方厘米", equation=f"{b}*{h}/2")
    if d == 3:
        b, h = rng.randint(5, 9) + 0.5, rng.randint(3, 6)
        ans = b * h / 2
        return _q(f"一个三角形的底是 {f(b)} 厘米，高是 {h} 厘米，面积是多少平方厘米？",
                  f"{f(ans)} 平方厘米", f"{f(b)}×{h}÷2={f(ans)}（平方厘米）。",
                  value=ans, unit="平方厘米", equation=f"{f(b)}*{h}/2")
    if d == 4:
        b = rng.randint(4, 10)
        h = rng.randint(3, 8)
        A = b * h / 2
        return _q(f"一个三角形的面积是 {f(A)} 平方厘米，底是 {b} 厘米，高是多少厘米？",
                  f"{h} 厘米", f"高=面积×2÷底={f(A)}×2÷{b}={h}（厘米）。",
                  value=h, unit="厘米", equation=f"{f(A)}*2/{b}")
    base, height = rng.randint(4, 8), rng.randint(3, 7)
    return _q(f"一个三角形和一个平行四边形等底等高。平行四边形的面积是 {base * height} 平方厘米，三角形的面积是多少平方厘米？",
              f"{f(base * height / 2)} 平方厘米",
              f"等底等高时三角形面积是平行四边形的一半：{base * height}÷2={f(base * height / 2)}（平方厘米）。",
              value=base * height / 2, unit="平方厘米", equation=f"{base * height}/2")


def parallelogram_area(rng: random.Random, d: int) -> dict:
    if d <= 2:
        b, h = rng.randint(4, 12), rng.randint(3, 9)
        return _q(f"一个平行四边形的底是 {b} 厘米，高是 {h} 厘米，面积是多少平方厘米？",
                  f"{b * h} 平方厘米", f"{b}×{h}={b * h}（平方厘米）。",
                  value=b * h, unit="平方厘米", equation=f"{b}*{h}")
    if d == 3:
        b, h = rng.randint(5, 9) + 0.5, rng.randint(3, 6)
        return _q(f"一个平行四边形的底是 {f(b)} 厘米，高是 {h} 厘米，面积是多少平方厘米？",
                  f"{f(b * h)} 平方厘米", f"{f(b)}×{h}={f(b * h)}（平方厘米）。",
                  value=b * h, unit="平方厘米", equation=f"{f(b)}*{h}")
    if d == 4:
        b = rng.randint(4, 10)
        h = rng.randint(3, 8)
        A = b * h
        return _q(f"一个平行四边形的面积是 {A} 平方厘米，底是 {b} 厘米，高是多少厘米？",
                  f"{h} 厘米", f"高=面积÷底={A}÷{b}={h}（厘米）。",
                  value=h, unit="厘米", equation=f"{A}/{b}")
    base, height = rng.randint(4, 8), rng.randint(3, 7)
    return _q(f"一个三角形和一个平行四边形等底等高。三角形的面积是 {f(base * height / 2)} 平方厘米，平行四边形的面积是多少平方厘米？",
              f"{base * height} 平方厘米",
              f"平行四边形面积是等底等高三角形面积的 2 倍：{f(base * height / 2)}×2={base * height}（平方厘米）。",
              value=base * height, unit="平方厘米", equation=f"{f(base * height / 2)}*2")


def trapezoid_area(rng: random.Random, d: int) -> dict:
    if d <= 2:
        a, b, h = rng.randint(2, 6), rng.randint(7, 12), rng.randint(3, 8)
        ans = (a + b) * h / 2
        return _q(f"一个梯形的上底是 {a} 厘米，下底是 {b} 厘米，高是 {h} 厘米，面积是多少平方厘米？",
                  f"{f(ans)} 平方厘米", f"（{a}+{b}）×{h}÷2={f(ans)}（平方厘米）。",
                  value=ans, unit="平方厘米", equation=f"({a}+{b})*{h}/2")
    if d == 3:
        a, b, h = rng.randint(3, 6) + 0.5, rng.randint(7, 10) + 0.5, rng.randint(3, 6)
        ans = (a + b) * h / 2
        return _q(f"一个梯形的上底是 {f(a)} 厘米，下底是 {f(b)} 厘米，高是 {h} 厘米，面积是多少平方厘米？",
                  f"{f(ans)} 平方厘米", f"（{f(a)}+{f(b)}）×{h}÷2={f(ans)}（平方厘米）。",
                  value=ans, unit="平方厘米", equation=f"({f(a)}+{f(b)})*{h}/2")
    if d == 4:
        a, b, h = 4, 8, rng.randint(3, 7)
        A = (a + b) * h / 2
        return _q(f"一个梯形的面积是 {f(A)} 平方厘米，上底是 {a} 厘米，下底是 {b} 厘米，高是多少厘米？",
                  f"{h} 厘米", f"高=面积×2÷（上底+下底）={f(A)}×2÷（{a}+{b}）={h}（厘米）。",
                  value=h, unit="厘米", equation=f"{f(A)}*2/({a}+{b})")
    a, b, h = 4, 8, rng.randint(4, 8)
    return _q(f"一个梯形上底 {a} 厘米、下底 {b} 厘米、高 {h} 厘米。如果上底增加 2 厘米，下底和高不变，面积增加多少平方厘米？",
              f"{h} 平方厘米", f"增加的面积=2×高÷2={h}（平方厘米）。",
              value=h, unit="平方厘米", equation=f"2*{h}/2")


def circle_circumference(rng: random.Random, d: int) -> dict:
    pi = 3.14
    if d <= 2:
        r = rng.randint(2, 8)
        ans = 2 * pi * r
        return _q(f"一个圆的半径是 {r} 厘米，它的周长是多少厘米？（π 取 3.14）",
                  f"{f(ans)} 厘米", f"2×3.14×{r}={f(ans)}（厘米）。",
                  value=ans, unit="厘米", equation=f"2*3.14*{r}")
    if d == 3:
        dia = rng.choice([4, 6, 8, 10])
        ans = pi * dia
        return _q(f"一个圆的直径是 {dia} 厘米，它的周长是多少厘米？（π 取 3.14）",
                  f"{f(ans)} 厘米", f"3.14×{dia}={f(ans)}（厘米）。",
                  value=ans, unit="厘米", equation=f"3.14*{dia}")
    if d == 4:
        r = rng.randint(3, 8)
        C = 2 * pi * r
        return _q(f"一个圆的周长是 {f(C)} 厘米，它的半径是多少厘米？（π 取 3.14）",
                  f"{r} 厘米", f"半径=周长÷（2×3.14）={f(C)}÷6.28={r}（厘米）。",
                  value=r, unit="厘米", equation=f"{f(C)}/6.28")
    r = rng.randint(3, 8)
    ans = pi * r + 2 * r
    return _q(f"一个半圆的半径是 {r} 厘米，它的周长是多少厘米？（π 取 3.14）",
              f"{f(ans)} 厘米", f"半圆周长=πr+2r=3.14×{r}+2×{r}={f(ans)}（厘米）。",
              value=ans, unit="厘米", equation=f"3.14*{r}+2*{r}")


def circle_area(rng: random.Random, d: int) -> dict:
    pi = 3.14
    if d <= 2:
        r = rng.randint(2, 8)
        ans = pi * r * r
        return _q(f"一个圆的半径是 {r} 厘米，面积是多少平方厘米？（π 取 3.14）",
                  f"{f(ans)} 平方厘米", f"3.14×{r}²={f(ans)}（平方厘米）。",
                  value=ans, unit="平方厘米", equation=f"3.14*{r}*{r}")
    if d == 3:
        dia = rng.choice([4, 6, 8, 10])
        r = dia / 2
        ans = pi * r * r
        return _q(f"一个圆的直径是 {dia} 厘米，面积是多少平方厘米？（π 取 3.14）",
                  f"{f(ans)} 平方厘米", f"半径={dia}÷2={f(r)}，面积=3.14×{f(r)}²={f(ans)}（平方厘米）。",
                  value=ans, unit="平方厘米", equation=f"3.14*{f(r)}*{f(r)}")
    if d == 4:
        r = rng.randint(3, 8)
        C = 2 * pi * r
        return _q(f"一个圆的周长是 {f(C)} 厘米，面积是多少平方厘米？（π 取 3.14）",
                  f"{f(pi * r * r)} 平方厘米",
                  f"半径={f(C)}÷6.28={r}，面积=3.14×{r}²={f(pi * r * r)}（平方厘米）。",
                  value=pi * r * r, unit="平方厘米", equation=f"3.14*{r}*{r}")
    dia = rng.choice([6, 8, 10])
    r = dia / 2
    return _q(f"一个圆形花坛的直径是 {dia} 米，它的面积是多少平方米？（π 取 3.14）",
              f"{f(pi * r * r)} 平方米",
              f"半径={dia}÷2={f(r)}，面积=3.14×{f(r)}²={f(pi * r * r)}（平方米）。",
              value=pi * r * r, unit="平方米", equation=f"3.14*{f(r)}*{f(r)}")


def ring_area(rng: random.Random, d: int) -> dict:
    pi = 3.14
    if d <= 3:
        R, r = rng.randint(5, 10), rng.randint(2, 4)
        if d == 3:
            R, r = R + 0.5, r + 0.5
        ans = pi * (R * R - r * r)
        return _q(f"一个圆环的外圆半径是 {f(R)} 厘米，内圆半径是 {f(r)} 厘米，圆环面积是多少平方厘米？（π 取 3.14）",
                  f"{f(ans)} 平方厘米",
                  f"3.14×（{f(R)}²-{f(r)}²）={f(ans)}（平方厘米）。",
                  value=ans, unit="平方厘米", equation=f"3.14*({f(R)}*{f(R)}-{f(r)}*{f(r)})")
    if d == 4:
        R, r = 8, 5
        ans = pi * (R * R - r * r)
        return _q(f"一个圆环的外圆半径是 {R} 厘米，内圆直径是 {2 * r} 厘米，圆环面积是多少平方厘米？（π 取 3.14）",
                  f"{f(ans)} 平方厘米",
                  f"内半径={2 * r}÷2={r}，面积=3.14×（{R}²-{r}²）={f(ans)}（平方厘米）。",
                  value=ans, unit="平方厘米", equation=f"3.14*({R}*{R}-{r}*{r})")
    r = rng.randint(4, 7)
    width = 2
    R = r + width
    ans = pi * (R * R - r * r)
    return _q(f"一个圆环的内圆半径是 {r} 厘米，环宽 {width} 厘米，圆环面积是多少平方厘米？（π 取 3.14）",
              f"{f(ans)} 平方厘米",
              f"外半径={r}+{width}={R}，面积=3.14×（{R}²-{r}²）={f(ans)}（平方厘米）。",
              value=ans, unit="平方厘米", equation=f"3.14*({R}*{R}-{r}*{r})")


def sector_area(rng: random.Random, d: int) -> dict:
    pi = 3.14
    if d <= 3:
        r = rng.randint(3, 8)
        angle = rng.choice([90, 180, 270])
        ans = pi * r * r * angle / 360
        return _q(f"一个扇形的半径是 {r} 厘米，圆心角是 {angle}°，面积是多少平方厘米？（π 取 3.14）",
                  f"{f(ans)} 平方厘米",
                  f"3.14×{r}²×{angle}/360={f(ans)}（平方厘米）。",
                  value=ans, unit="平方厘米", equation=f"3.14*{r}*{r}*{angle}/360")
    if d == 4:
        r, angle = 8, 45
        ans = pi * r * r * angle / 360
        return _q(f"一个扇形的半径是 {r} 厘米，圆心角是 {angle}°，面积是多少平方厘米？（π 取 3.14）",
                  f"{f(ans)} 平方厘米",
                  f"3.14×{r}²×{angle}/360={f(ans)}（平方厘米）。",
                  value=ans, unit="平方厘米", equation=f"3.14*{r}*{r}*{angle}/360")
    r = rng.randint(4, 8)
    ans = pi * r * r / 2
    return _q(f"一个半圆的半径是 {r} 厘米，它的面积是多少平方厘米？（π 取 3.14）",
              f"{f(ans)} 平方厘米", f"3.14×{r}²÷2={f(ans)}（平方厘米）。",
              value=ans, unit="平方厘米", equation=f"3.14*{r}*{r}/2")


def cuboid(rng: random.Random, d: int) -> dict:
    if d <= 2:
        l, w, h = rng.randint(3, 6), rng.randint(2, 5), rng.randint(2, 4)
        return _q(f"一个长方体长 {l} 厘米、宽 {w} 厘米、高 {h} 厘米，它的体积是多少立方厘米？",
                  f"{l * w * h} 立方厘米", f"{l}×{w}×{h}={l * w * h}（立方厘米）。",
                  value=l * w * h, unit="立方厘米", equation=f"{l}*{w}*{h}")
    if d == 3:
        l, w, h = rng.randint(5, 8), rng.randint(3, 5), rng.randint(2, 4)
        surface = 2 * (l * w + l * h + w * h)
        return _q(f"一个长方体长 {l} 厘米、宽 {w} 厘米、高 {h} 厘米，它的表面积是多少平方厘米？",
                  f"{surface} 平方厘米",
                  f"2×（{l}×{w}+{l}×{h}+{w}×{h}）={surface}（平方厘米）。",
                  value=surface, unit="平方厘米", equation=f"2*({l}*{w}+{l}*{h}+{w}*{h})")
    if d == 4:
        l, w, h = rng.randint(6, 10), rng.randint(4, 6), rng.randint(3, 5)
        surface = 2 * (l * w + l * h + w * h)
        return _q(f"一个长方体长 {l} 厘米、宽 {w} 厘米、高 {h} 厘米，它的表面积是多少平方厘米？",
                  f"{surface} 平方厘米",
                  f"2×（{l}×{w}+{l}×{h}+{w}×{h}）={surface}（平方厘米）。",
                  value=surface, unit="平方厘米", equation=f"2*({l}*{w}+{l}*{h}+{w}*{h})")
    l, w, h = 10, 5, 4
    volume = l * w * h
    return _q(f"一个长方体木箱，长 {l} 分米、宽 {w} 分米、高 {h} 分米，它的容积是多少升？",
              f"{volume} 升", f"1 立方分米=1 升，容积={l}×{w}×{h}={volume}（升）。",
              value=volume, unit="升", equation=f"{l}*{w}*{h}")


def cube(rng: random.Random, d: int) -> dict:
    if d <= 2:
        a = rng.randint(2, 7)
        return _q(f"一个正方体的棱长是 {a} 厘米，它的表面积是多少平方厘米？",
                  f"{6 * a * a} 平方厘米", f"棱长×棱长×6={a}×{a}×6={6 * a * a}（平方厘米）。",
                  value=6 * a * a, unit="平方厘米", equation=f"6*{a}*{a}")
    if d == 3:
        a = rng.randint(2, 6)
        return _q(f"一个正方体的棱长是 {a} 厘米，它的体积是多少立方厘米？",
                  f"{a ** 3} 立方厘米", f"{a}×{a}×{a}={a ** 3}（立方厘米）。",
                  value=a ** 3, unit="立方厘米", equation=f"{a}*{a}*{a}")
    if d == 4:
        a = rng.randint(3, 6)
        return _q(f"一个正方体的棱长是 {a} 分米，它的体积是多少立方分米？",
                  f"{a ** 3} 立方分米", f"{a}³={a ** 3}（立方分米）。",
                  value=a ** 3, unit="立方分米", equation=f"{a}*{a}*{a}")
    a = 4
    return _q("一个正方体的表面积是 96 平方厘米，它的体积是多少立方厘米？",
              f"{a ** 3} 立方厘米",
              f"每个面面积=96÷6=16，棱长=4，体积=4³={a ** 3}（立方厘米）。",
              value=a ** 3, unit="立方厘米", equation="4*4*4")


def cylinder(rng: random.Random, d: int) -> dict:
    pi = 3.14
    if d <= 3:
        r, h = rng.randint(2, 5), rng.randint(3, 8)
        ans = pi * r * r * h
        return _q(f"一个圆柱的底面半径是 {r} 厘米，高是 {h} 厘米，体积是多少立方厘米？（π 取 3.14）",
                  f"{f(ans)} 立方厘米", f"3.14×{r}²×{h}={f(ans)}（立方厘米）。",
                  value=ans, unit="立方厘米", equation=f"3.14*{r}*{r}*{h}")
    if d == 4:
        r, h = 2, 5
        ans = 2 * pi * r * r + 2 * pi * r * h
        return _q(f"一个圆柱的底面半径是 {r} 分米，高是 {h} 分米，它的表面积是多少平方分米？（π 取 3.14）",
                  f"{f(ans)} 平方分米",
                  f"两个底面积 2×3.14×{r}²=25.12，侧面积 2×3.14×{r}×{h}=62.8，合计 {f(ans)}。",
                  value=ans, unit="平方分米", equation=f"2*3.14*{r}*{r}+2*3.14*{r}*{h}")
    r, h = 5, 2
    ans = pi * r * r * h
    return _q(f"一个圆柱形水桶的底面半径是 {r} 分米，高是 {h} 分米，它能装多少升水？（π 取 3.14）",
              f"{f(ans)} 升", f"体积=3.14×{r}²×{h}={f(ans)} 立方分米={f(ans)} 升。",
              value=ans, unit="升", equation=f"3.14*{r}*{r}*{h}")


def cone(rng: random.Random, d: int) -> dict:
    pi = 3.14
    if d <= 3:
        r, h = rng.randint(2, 6), rng.randint(3, 9)
        ans = pi * r * r * h / 3
        return _q(f"一个圆锥的底面半径是 {r} 厘米，高是 {h} 厘米，体积是多少立方厘米？（π 取 3.14）",
                  f"{f(ans)} 立方厘米", f"3.14×{r}²×{h}÷3={f(ans)}（立方厘米）。",
                  value=ans, unit="立方厘米", equation=f"3.14*{r}*{r}*{h}/3")
    if d == 4:
        r, h = 4, 3
        ans = pi * r * r * h / 3
        return _q(f"一个圆锥的底面半径是 {r} 分米，高是 {h} 分米，体积是多少立方分米？（π 取 3.14）",
                  f"{f(ans)} 立方分米", f"3.14×{r}²×{h}÷3={f(ans)}（立方分米）。",
                  value=ans, unit="立方分米", equation=f"3.14*{r}*{r}*{h}/3")
    r, h = 3, 6
    ans = pi * r * r * h / 3
    return _q(f"一个圆锥形沙堆的底面半径是 {r} 米，高是 {h} 米，它的体积是多少立方米？（π 取 3.14）",
              f"{f(ans)} 立方米", f"3.14×{r}²×{h}÷3={f(ans)}（立方米）。",
              value=ans, unit="立方米", equation=f"3.14*{r}*{r}*{h}/3")


def composite_area(rng: random.Random, d: int) -> dict:
    pi = 3.14
    if d == 1:
        l, w, a = 10, 6, 4
        ans = l * w - a * a
        return _q(f"一个长方形长 {l} 厘米、宽 {w} 厘米，从它的一个角剪去一个边长 {a} 厘米的正方形。剩下图形的面积是多少平方厘米？",
                  f"{ans} 平方厘米", f"{l}×{w}-{a}×{a}={ans}（平方厘米）。",
                  value=ans, unit="平方厘米", equation=f"{l}*{w}-{a}*{a}")
    if d == 2:
        l, w, r = 12, 8, 4
        ans = l * w - pi * r * r
        return _q(f"在一张长 {l} 厘米、宽 {w} 厘米的长方形纸板上剪下一个半径 {r} 厘米的圆，剩下部分的面积是多少平方厘米？（π 取 3.14）",
                  f"{f(ans)} 平方厘米", f"{l}×{w}-3.14×{r}²={f(ans)}（平方厘米）。",
                  value=ans, unit="平方厘米", equation=f"{l}*{w}-3.14*{r}*{r}")
    if d == 3:
        a, b, c, d2 = 10, 4, 6, 3
        ans = a * b + c * d2
        return _q(f"一个组合图形由两个长方形拼成：上面一个长 {a} 厘米、宽 {b} 厘米，下面一个长 {c} 厘米、宽 {d2} 厘米。组合图形的面积是多少平方厘米？",
                  f"{ans} 平方厘米", f"{a}×{b}+{c}×{d2}={ans}（平方厘米）。",
                  value=ans, unit="平方厘米", equation=f"{a}*{b}+{c}*{d2}")
    if d == 4:
        side, tri_h = 8, 4
        ans = side * side - side * tri_h / 2
        return _q(f"一个正方形边长 {side} 厘米，从中剪去一个底为 {side} 厘米、高为 {tri_h} 厘米的三角形。剩下图形的面积是多少平方厘米？",
                  f"{f(ans)} 平方厘米", f"{side}²-{side}×{tri_h}÷2={f(ans)}（平方厘米）。",
                  value=ans, unit="平方厘米", equation=f"{side}*{side}-{side}*{tri_h}/2")
    l, w, r = 20, 10, 5
    ans = l * w - pi * r * r / 2
    return _q(f"一个长方形长 {l} 分米、宽 {w} 分米，一端剪去一个半径 {r} 分米的半圆。剩下图形的面积是多少平方分米？（π 取 3.14）",
              f"{f(ans)} 平方分米", f"{l}×{w}-3.14×{r}²÷2={f(ans)}（平方分米）。",
              value=ans, unit="平方分米", equation=f"{l}*{w}-3.14*{r}*{r}/2")


def cut_combine(rng: random.Random, d: int) -> dict:
    if d <= 3:
        a = rng.randint(2, 4)
        return _q(f"把棱长为 {a} 厘米的正方体切成棱长为 1 厘米的小正方体，可以切成多少个小正方体？",
                  f"{a ** 3} 个", f"{a}×{a}×{a}={a ** 3}（个）。",
                  value=a ** 3, unit="个", equation=f"{a}*{a}*{a}")
    if d == 4:
        l, w, h = 6, 4, 2
        return _q(f"把长 {l} 厘米、宽 {w} 厘米、高 {h} 厘米的长方体切成棱长 1 厘米的小正方体，可以切成多少个？",
                  f"{l * w * h} 个", f"{l}×{w}×{h}={l * w * h}（个）。",
                  value=l * w * h, unit="个", equation=f"{l}*{w}*{h}")
    a = 3
    return _q(f"把两个棱长 {a} 厘米的正方体拼成一个长方体，表面积减少多少平方厘米？",
              f"{2 * a * a} 平方厘米", f"拼合面有 2 个，减少 2×{a}×{a}={2 * a * a}（平方厘米）。",
              value=2 * a * a, unit="平方厘米", equation=f"2*{a}*{a}")


# ---------------------------------------------------------------- 单位换算

def length_units(rng: random.Random, d: int) -> dict:
    if d == 1:
        x = rng.randint(2, 9)
        return _q(f"{x} 米等于多少厘米？", f"{x * 100} 厘米",
                  f"1 米=100 厘米，{x}×100={x * 100}（厘米）。",
                  value=x * 100, unit="厘米", equation=f"{x}*100", qtype="填空题")
    if d == 2:
        x = rng.randint(2, 9)
        return _q(f"{x} 千米等于多少米？", f"{x * 1000} 米",
                  f"1 千米=1000 米，{x}×1000={x * 1000}（米）。",
                  value=x * 1000, unit="米", equation=f"{x}*1000", qtype="填空题")
    if d == 3:
        x = rng.randint(2, 8) + 0.5
        return _q(f"{f(x)} 米等于多少厘米？", f"{f(x * 100)} 厘米",
                  f"{f(x)}×100={f(x * 100)}（厘米）。",
                  value=x * 100, unit="厘米", equation=f"{f(x)}*100", qtype="填空题")
    if d == 4:
        x = rng.choice([3800, 5200, 7500])
        return _q(f"{x} 米等于多少千米？", f"{f(x / 1000)} 千米",
                  f"{x}÷1000={f(x / 1000)}（千米）。",
                  value=x / 1000, unit="千米", equation=f"{x}/1000", qtype="填空题")
    return _q("1 千米减去 300 米，还剩多少米？", "700 米",
              "1 千米=1000 米，1000-300=700（米）。",
              value=700, unit="米", equation="1000-300", qtype="填空题")


def area_units(rng: random.Random, d: int) -> dict:
    if d == 1:
        x = rng.randint(2, 9)
        return _q(f"{x} 公顷等于多少平方米？", f"{x * 10000} 平方米",
                  f"1 公顷=10000 平方米，{x}×10000={x * 10000}（平方米）。",
                  value=x * 10000, unit="平方米", equation=f"{x}*10000", qtype="填空题")
    if d == 2:
        x = rng.randint(2, 8)
        return _q(f"{x} 平方千米等于多少公顷？", f"{x * 100} 公顷",
                  f"1 平方千米=100 公顷，{x}×100={x * 100}（公顷）。",
                  value=x * 100, unit="公顷", equation=f"{x}*100", qtype="填空题")
    if d == 3:
        x = rng.randint(2, 8)
        return _q(f"{x} 平方米等于多少平方分米？", f"{x * 100} 平方分米",
                  f"1 平方米=100 平方分米，{x}×100={x * 100}（平方分米）。",
                  value=x * 100, unit="平方分米", equation=f"{x}*100", qtype="填空题")
    if d == 4:
        x = rng.choice([4500, 6800, 12000])
        return _q(f"{x} 平方米等于多少公顷？", f"{f(x / 10000)} 公顷",
                  f"{x}÷10000={f(x / 10000)}（公顷）。",
                  value=x / 10000, unit="公顷", equation=f"{x}/10000", qtype="填空题")
    return _q("1 公顷减去 3000 平方米，还剩多少平方米？", "7000 平方米",
              "1 公顷=10000 平方米，10000-3000=7000（平方米）。",
              value=7000, unit="平方米", equation="10000-3000", qtype="填空题")


def volume_units(rng: random.Random, d: int) -> dict:
    if d == 1:
        x = rng.randint(2, 9)
        return _q(f"{x} 升等于多少毫升？", f"{x * 1000} 毫升",
                  f"1 升=1000 毫升，{x}×1000={x * 1000}（毫升）。",
                  value=x * 1000, unit="毫升", equation=f"{x}*1000", qtype="填空题")
    if d == 2:
        x = rng.randint(2, 8)
        return _q(f"{x} 立方米等于多少立方分米？", f"{x * 1000} 立方分米",
                  f"1 立方米=1000 立方分米，{x}×1000={x * 1000}（立方分米）。",
                  value=x * 1000, unit="立方分米", equation=f"{x}*1000", qtype="填空题")
    if d == 3:
        x = rng.randint(2, 8) + 0.5
        return _q(f"{f(x)} 升等于多少毫升？", f"{f(x * 1000)} 毫升",
                  f"{f(x)}×1000={f(x * 1000)}（毫升）。",
                  value=x * 1000, unit="毫升", equation=f"{f(x)}*1000", qtype="填空题")
    if d == 4:
        x = rng.choice([500, 750, 1200])
        return _q(f"{x} 毫升等于多少升？", f"{f(x / 1000)} 升",
                  f"{x}÷1000={f(x / 1000)}（升）。",
                  value=x / 1000, unit="升", equation=f"{x}/1000", qtype="填空题")
    x = rng.randint(2, 5)
    return _q(f"{x} 立方米等于多少升？", f"{x * 1000} 升",
              f"1 立方米=1000 升，{x}×1000={x * 1000}（升）。",
              value=x * 1000, unit="升", equation=f"{x}*1000", qtype="填空题")


def mass_units(rng: random.Random, d: int) -> dict:
    if d == 1:
        x = rng.randint(2, 9)
        return _q(f"{x} 吨等于多少千克？", f"{x * 1000} 千克",
                  f"1 吨=1000 千克，{x}×1000={x * 1000}（千克）。",
                  value=x * 1000, unit="千克", equation=f"{x}*1000", qtype="填空题")
    if d == 2:
        x = rng.randint(2, 9)
        return _q(f"{x} 千克等于多少克？", f"{x * 1000} 克",
                  f"1 千克=1000 克，{x}×1000={x * 1000}（克）。",
                  value=x * 1000, unit="克", equation=f"{x}*1000", qtype="填空题")
    if d == 3:
        x = rng.randint(2, 8) + 0.5
        return _q(f"{f(x)} 吨等于多少千克？", f"{f(x * 1000)} 千克",
                  f"{f(x)}×1000={f(x * 1000)}（千克）。",
                  value=x * 1000, unit="千克", equation=f"{f(x)}*1000", qtype="填空题")
    if d == 4:
        x = rng.choice([4500, 6800, 2500])
        return _q(f"{x} 克等于多少千克？", f"{f(x / 1000)} 千克",
                  f"{x}÷1000={f(x / 1000)}（千克）。",
                  value=x / 1000, unit="千克", equation=f"{x}/1000", qtype="填空题")
    return _q("1 吨减去 300 千克，还剩多少千克？", "700 千克",
              "1 吨=1000 千克，1000-300=700（千克）。",
              value=700, unit="千克", equation="1000-300", qtype="填空题")


def time_units(rng: random.Random, d: int) -> dict:
    if d == 1:
        x = rng.randint(2, 5)
        return _q(f"{x} 小时等于多少分钟？", f"{x * 60} 分钟",
                  f"1 小时=60 分钟，{x}×60={x * 60}（分钟）。",
                  value=x * 60, unit="分钟", equation=f"{x}*60", qtype="填空题")
    if d == 2:
        x = rng.randint(2, 6)
        return _q(f"{x} 分钟等于多少秒？", f"{x * 60} 秒",
                  f"1 分钟=60 秒，{x}×60={x * 60}（秒）。",
                  value=x * 60, unit="秒", equation=f"{x}*60", qtype="填空题")
    if d == 3:
        x = rng.randint(2, 4) + 0.5
        return _q(f"{f(x)} 小时等于多少分钟？", f"{f(x * 60)} 分钟",
                  f"{f(x)}×60={f(x * 60)}（分钟）。",
                  value=x * 60, unit="分钟", equation=f"{f(x)}*60", qtype="填空题")
    if d == 4:
        return _q("150 秒等于多少分多少秒？", "2 分 30 秒",
                  "150÷60=2 余 30，即 2 分 30 秒。",
                  value=150, unit="秒", equation=None, qtype="填空题")
    return _q("2 小时 15 分等于多少分钟？", "135 分钟",
              "2 小时=120 分钟，120+15=135（分钟）。",
              value=135, unit="分钟", equation="2*60+15", qtype="填空题")


def date_units(rng: random.Random, d: int) -> dict:
    if d == 1:
        return _q("2024 年是闰年，这一年的二月有多少天？", "29 天",
                  "闰年二月有 29 天。", value=29, unit="天", qtype="填空题")
    if d == 2:
        return _q("平年一年有多少天？", "365 天",
                  "平年一年有 365 天。", value=365, unit="天", qtype="填空题")
    if d == 3:
        return _q("一年中的第三季度共有多少天？", "92 天",
                  "第三季度是 7、8、9 月：31+31+30=92（天）。",
                  value=92, unit="天", equation="31+31+30", qtype="填空题")
    if d == 4:
        return _q("2025 年二月有多少天？", "28 天",
                  "2025 年是平年，二月有 28 天。", value=28, unit="天", qtype="填空题")
    return _q("六月有多少天？合几个星期零几天？", "30 天，4 个星期零 2 天",
              "六月有 30 天，30÷7=4 余 2，即 4 个星期零 2 天。",
              value=30, unit="天", qtype="填空题")


def rmb_units(rng: random.Random, d: int) -> dict:
    if d == 1:
        x = rng.randint(2, 9)
        return _q(f"{x} 元等于多少角？", f"{x * 10} 角",
                  f"1 元=10 角，{x}×10={x * 10}（角）。",
                  value=x * 10, unit="角", equation=f"{x}*10", qtype="填空题")
    if d == 2:
        x, y = rng.randint(2, 8), rng.randint(1, 9)
        return _q(f"{x} 元 {y} 角等于多少角？", f"{x * 10 + y} 角",
                  f"{x}×10+{y}={x * 10 + y}（角）。",
                  value=x * 10 + y, unit="角", equation=f"{x}*10+{y}", qtype="填空题")
    if d == 3:
        x, y = rng.randint(2, 9), rng.randint(1, 9)
        return _q(f"{f(x + y / 10)} 元等于多少元多少角？", f"{x} 元 {y} 角",
                  f"{f(x + y / 10)} 元 = {x} 元 {y} 角。",
                  value=x + y / 10, unit="元", qtype="填空题")
    if d == 4:
        y = rng.randint(2, 9)
        return _q(f"{y} 角等于多少元？", f"{f(y / 10)} 元",
                  f"{y}÷10={f(y / 10)}（元）。",
                  value=y / 10, unit="元", equation=f"{y}/10", qtype="填空题")
    return _q("10 元减去 3 元 5 角，还剩多少元多少角？", "6 元 5 角",
              "10 元-3 元 5 角=6 元 5 角。",
              value=6.5, unit="元", qtype="填空题")


# ---------------------------------------------------------------- 数论

def _factors(n: int) -> list[int]:
    return [i for i in range(1, n + 1) if n % i == 0]


def factors_multiples(rng: random.Random, d: int) -> dict:
    n = rng.choice([12, 18, 24, 36, 48]) if d >= 3 else rng.choice([6, 8, 12, 16])
    fs = _factors(n)
    if d <= 2:
        return _q(f"{n} 的因数一共有多少个？", f"{len(fs)} 个",
                  f"{n} 的因数有：{'、'.join(map(str, fs))}，共 {len(fs)} 个。",
                  value=len(fs), unit="个", qtype="填空题")
    if d == 3:
        return _q(f"{n} 的所有因数中，最大的因数是多少？", f"{n}",
                  f"一个数最大的因数是它本身，所以是 {n}。",
                  value=n, qtype="填空题")
    if d == 4:
        return _q(f"一个数的最小倍数是 {n}，这个数是多少？", f"{n}",
                  f"一个数的最小倍数是它本身，所以这个数是 {n}。",
                  value=n, qtype="填空题")
    return _q(f"{n} 的最小倍数是 {n}，它的最大因数是多少？", f"{n}",
              f"一个数的最大因数和最小倍数都是它本身，所以是 {n}。",
              value=n, qtype="填空题")


def _is_prime(n: int) -> bool:
    if n < 2:
        return False
    for i in range(2, int(n ** 0.5) + 1):
        if n % i == 0:
            return False
    return True


def prime_composite(rng: random.Random, d: int) -> dict:
    if d == 1:
        return _q("最小的质数是多少？", "2",
                  "质数是只有 1 和它本身两个因数的数，最小的是 2。",
                  value=2, qtype="填空题")
    if d == 2:
        n = rng.choice([29, 31, 37, 41, 43])
        return _q(f"{n} 是质数还是合数？", "质数",
                  f"{n} 只有 1 和它本身两个因数，所以是质数。", qtype="填空题")
    if d == 3:
        return _q("把 60 分解质因数，结果是多少？", "2²×3×5",
                  "60=2×2×3×5=2²×3×5。", qtype="填空题")
    if d == 4:
        return _q("两个质数的积是 35，这两个质数分别是多少？", "5 和 7",
                  "35=5×7，5 和 7 都是质数。", qtype="填空题")
    return _q("20 以内最大的质数是多少？", "19",
              "20 以内的质数有 2、3、5、7、11、13、17、19，最大的是 19。",
              value=19, qtype="填空题")


def _gcd(a: int, b: int) -> int:
    while b:
        a, b = b, a % b
    return a


def _lcm(a: int, b: int) -> int:
    return a // _gcd(a, b) * b


def common_factor(rng: random.Random, d: int) -> dict:
    pairs = [(12, 18), (24, 36), (15, 25), (42, 56), (48, 72)]
    a, b = pairs[d - 1]
    g = _gcd(a, b)
    return _q(f"{a} 和 {b} 的最大公因数是多少？", f"{g}",
              f"{a} 和 {b} 的公因数中最大的是 {g}。",
              value=g, qtype="填空题")


def common_multiple(rng: random.Random, d: int) -> dict:
    pairs = [(4, 6), (8, 12), (5, 7), (10, 15), (12, 18)]
    a, b = pairs[d - 1]
    l = _lcm(a, b)
    return _q(f"{a} 和 {b} 的最小公倍数是多少？", f"{l}",
              f"{a} 和 {b} 的公倍数中最小的是 {l}。",
              value=l, qtype="填空题")


def parity(rng: random.Random, d: int) -> dict:
    choices = [
        ("奇数加偶数，和是奇数还是偶数？", "奇数", "奇数+偶数=奇数。"),
        ("奇数乘偶数，积是奇数还是偶数？", "偶数", "奇数×偶数=偶数。"),
        ("偶数加偶数，和是奇数还是偶数？", "偶数", "偶数+偶数=偶数。"),
        ("奇数乘奇数，积是奇数还是偶数？", "奇数", "奇数×奇数=奇数。"),
        ("奇数个奇数相加，和是奇数还是偶数？", "奇数", "奇数个奇数相加，和是奇数。"),
    ]
    stem, ans, sol = choices[d - 1]
    return _q(stem, ans, sol, qtype="填空题")


def divisibility(rng: random.Random, d: int) -> dict:
    if d == 1:
        return _q("个位上是 0 或 5 的非零自然数，一定能被哪个数（1 除外）整除？", "5",
                  "个位是 0 或 5 的非零自然数能被 5 整除。", value=5, qtype="填空题")
    if d == 2:
        return _q("一个数各位数字之和是 9 的倍数，这个数一定能被哪个数整除？", "3",
                  "各位数字之和是 3 的倍数，这个数就能被 3 整除；是 9 的倍数时也能被 9 整除。",
                  value=3, qtype="填空题")
    if d == 3:
        return _q("在 42□ 中填一个数字，使它同时能被 2 和 3 整除，□里最小填几？", "0",
                  "能被 2 整除，□是偶数；4+2+0=6 能被 3 整除，所以最小填 0。",
                  value=0, qtype="填空题")
    if d == 4:
        return _q("在 3□5 中填一个数字，使它能被 3 整除，□里最小填几？", "1",
                  "3+□+5=8+□，要能被 3 整除，□最小是 1。",
                  value=1, qtype="填空题")
    return _q("1236 能被 4 整除吗？", "能",
              "一个数末两位能被 4 整除，这个数就能被 4 整除；36 能被 4 整除，所以能。",
              qtype="填空题")


# ---------------------------------------------------------------- 统计与概率

def statistics_chart(rng: random.Random, d: int) -> dict:
    days = ["周一", "周二", "周三", "周四", "周五"]
    values = [rng.randint(10, 20) for _ in days]
    table = "、".join(f"{day}{v}本" for day, v in zip(days, values))
    total = sum(values)
    if d <= 2:
        return _q(f"某班一周借书数量如下：{table}。这五天一共借书多少本？",
                  f"{total} 本", f"{' + '.join(map(str, values))}={total}（本）。",
                  value=total, unit="本", equation="+".join(map(str, values)))
    if d == 3:
        avg = total / len(values)
        return _q(f"某班一周借书数量如下：{table}。这五天平均每天借书多少本？",
                  f"{f(avg)} 本", f"总数 {total}，{total}÷{len(values)}={f(avg)}（本）。",
                  value=avg, unit="本", equation=f"{total}/{len(values)}")
    if d == 4:
        return _q(f"某班一周借书数量如下：{table}。借书最多的一天比最少的一天多借多少本？",
                  f"{max(values) - min(values)} 本",
                  f"最多 {max(values)} 本，最少 {min(values)} 本，相差 {max(values) - min(values)} 本。",
                  value=max(values) - min(values), unit="本", equation=f"{max(values)}-{min(values)}")
    avg = total / len(values)
    return _q(f"某班一周借书数量如下：{table}。如果下周每天借书数量都比这周平均每天多 2 本，下周一共借书多少本？",
              f"{f((avg + 2) * len(values))} 本",
              f"本周平均每天 {f(avg)} 本，下周每天 {f(avg + 2)} 本，共 {f((avg + 2) * len(values))} 本。",
              value=(avg + 2) * len(values), unit="本",
              equation=f"({total}/{len(values)}+2)*{len(values)}")


def possibility(rng: random.Random, d: int) -> dict:
    if d == 1:
        return _q("一个袋子里有 3 个红球和 2 个蓝球，任意摸一个，摸到红球的可能性是多少？",
                  "3/5", "红球 3 个，共 5 个，可能性是 3/5。", value=0.6, qtype="填空题")
    if d == 2:
        return _q("一个袋子里有 5 个红球和 5 个蓝球，任意摸一个，摸到红球和蓝球的可能性相比怎样？",
                  "一样大", "红球和蓝球各 5 个，数量相同，可能性一样大。", qtype="填空题")
    if d == 3:
        return _q("一个袋子里有 1 个红球和 4 个蓝球，任意摸一个，摸到红球的可能性是多少？",
                  "1/5", "红球 1 个，共 5 个，可能性是 1/5。", value=0.2, qtype="填空题")
    if d == 4:
        return _q("一个袋子里有 6 个红球和 2 个蓝球，任意摸一个，摸到红球的可能性是多少？",
                  "3/4", "红球 6 个，共 8 个，6/8=3/4。", value=0.75, qtype="填空题")
    return _q("一个袋子里有 4 个红球、3 个蓝球和 1 个黄球，任意摸一个，摸到哪种颜色球的可能性最大？",
              "红球", "红球 4 个最多，所以摸到红球的可能性最大。", qtype="填空题")


# ---------------------------------------------------------------- 运算

def decimal_ops(rng: random.Random, d: int) -> dict:
    if d == 1:
        a, b = rng.randint(15, 45) / 10, rng.randint(10, 30) / 10
        ans = a + b
        return _q(f"计算：{f(a)} + {f(b)} = ______。", f(a + b),
                  f"{f(a)}+{f(b)}={f(ans)}。", value=ans, equation=f"{f(a)}+{f(b)}", qtype="填空题")
    if d == 2:
        a, b = rng.randint(30, 60) / 10, rng.randint(10, 25) / 10
        ans = a - b
        return _q(f"计算：{f(a)} - {f(b)} = ______。", f(ans),
                  f"{f(a)}-{f(b)}={f(ans)}。", value=ans, equation=f"{f(a)}-{f(b)}", qtype="填空题")
    if d == 3:
        a, b = rng.randint(15, 35) / 10, rng.randint(2, 6)
        ans = a * b
        return _q(f"计算：{f(a)} × {b} = ______。", f(ans),
                  f"{f(a)}×{b}={f(ans)}。", value=ans, equation=f"{f(a)}*{b}", qtype="填空题")
    if d == 4:
        a, b = rng.choice([(6.3, 0.9), (8.4, 1.2), (7.2, 0.8)])
        ans = a / b
        return _q(f"计算：{f(a)} ÷ {f(b)} = ______。", f(ans),
                  f"{f(a)}÷{f(b)}={f(ans)}。", value=ans, equation=f"{f(a)}/{f(b)}", qtype="填空题")
    a, b = 3.6, 2.4
    ans = a + b * 0.5
    return _q("计算：3.6 + 2.4 × 0.5 = ______。", f(ans),
              "先算乘法 2.4×0.5=1.2，再算 3.6+1.2=4.8。",
              value=ans, equation="3.6+2.4*0.5", qtype="填空题")


def fraction_ops(rng: random.Random, d: int) -> dict:
    if d == 1:
        return _q("计算：1/2 + 1/4 = ______。", "3/4",
                  "通分：2/4+1/4=3/4。", value=0.75, equation="1/2+1/4", qtype="填空题")
    if d == 2:
        return _q("计算：2/3 + 1/6 = ______。", "5/6",
                  "通分：4/6+1/6=5/6。", value=5 / 6, equation="2/3+1/6", qtype="填空题")
    if d == 3:
        return _q("计算：3/4 × 2/5 = ______。", "3/10",
                  "分子乘分子、分母乘分母：3/4×2/5=6/20=3/10。",
                  value=0.3, equation="3/4*2/5", qtype="填空题")
    if d == 4:
        return _q("计算：4/5 ÷ 2/3 = ______。", "6/5",
                  "除以一个分数等于乘它的倒数：4/5×3/2=12/10=6/5。",
                  value=1.2, equation="4/5/(2/3)", qtype="填空题")
    return _q("计算：1 - 3/8 - 1/4 = ______。", "3/8",
              "1-3/8-2/8=3/8。", value=0.375, equation="1-3/8-1/4", qtype="填空题")


def percent_ops(rng: random.Random, d: int) -> dict:
    if d == 1:
        return _q("50 的 20% 是多少？", "10", "50×20%=10。",
                  value=10, equation="50*0.2", qtype="填空题")
    if d == 2:
        return _q("80 的 25% 是多少？", "20", "80×25%=20。",
                  value=20, equation="80*0.25", qtype="填空题")
    if d == 3:
        return _q("把 25% 化成最简分数是多少？", "1/4", "25%=25/100=1/4。",
                  value=0.25, qtype="填空题")
    if d == 4:
        return _q("把 0.6 化成百分数是多少？", "60%", "0.6=60%。",
                  value=0.6, qtype="填空题")
    return _q("一个数的 25% 是 15，这个数是多少？", "60",
              "15÷25%=15÷0.25=60。", value=60, equation="15/0.25", qtype="填空题")


def add_sub(rng: random.Random, d: int) -> dict:
    if d == 1:
        a, b = rng.randint(10, 50), rng.randint(10, 40)
        return _q(f"计算：{a} + {b} = ______。", f"{a + b}",
                  f"{a}+{b}={a + b}。", value=a + b, equation=f"{a}+{b}", qtype="填空题")
    if d == 2:
        a, b = rng.randint(60, 99), rng.randint(10, 59)
        return _q(f"计算：{a} - {b} = ______。", f"{a - b}",
                  f"{a}-{b}={a - b}。", value=a - b, equation=f"{a}-{b}", qtype="填空题")
    if d == 3:
        a, b = rng.randint(1200, 4800), rng.randint(900, 3900)
        return _q(f"计算：{a} + {b} = ______。", f"{a + b}",
                  f"{a}+{b}={a + b}。", value=a + b, equation=f"{a}+{b}", qtype="填空题")
    if d == 4:
        a, b = rng.randint(400, 900), rng.randint(100, 399)
        return _q(f"计算：{a} - {b} = ______。", f"{a - b}",
                  f"{a}-{b}={a - b}。", value=a - b, equation=f"{a}-{b}", qtype="填空题")
    return _q("计算：1234 + 567 - 890 = ______。", "911",
              "1234+567=1801，1801-890=911。",
              value=911, equation="1234+567-890", qtype="填空题")


def mul_div(rng: random.Random, d: int) -> dict:
    if d == 1:
        a, b = rng.randint(3, 9), rng.randint(3, 9)
        return _q(f"计算：{a} × {b} = ______。", f"{a * b}",
                  f"{a}×{b}={a * b}。", value=a * b, equation=f"{a}*{b}", qtype="填空题")
    if d == 2:
        b, q = rng.randint(3, 9), rng.randint(3, 12)
        a = b * q
        return _q(f"计算：{a} ÷ {b} = ______。", f"{q}",
                  f"{a}÷{b}={q}。", value=q, equation=f"{a}/{b}", qtype="填空题")
    if d == 3:
        return _q("计算：25 × 16 = ______。", "400",
                  "25×16=25×4×4=400。", value=400, equation="25*16", qtype="填空题")
    if d == 4:
        return _q("计算：504 ÷ 12 = ______。", "42",
                  "504÷12=42。", value=42, equation="504/12", qtype="填空题")
    return _q("计算：125 × 8 ÷ 4 = ______。", "250",
              "125×8=1000，1000÷4=250。",
              value=250, equation="125*8/4", qtype="填空题")


def mixed_ops(rng: random.Random, d: int) -> dict:
    if d == 1:
        return _q("计算：3 + 4 × 5 = ______。", "23",
                  "先算乘法 4×5=20，再算 3+20=23。",
                  value=23, equation="3+4*5", qtype="填空题")
    if d == 2:
        return _q("计算：（3 + 4）× 5 = ______。", "35",
                  "先算括号 3+4=7，再算 7×5=35。",
                  value=35, equation="(3+4)*5", qtype="填空题")
    if d == 3:
        return _q("计算：48 ÷（6 + 2）= ______。", "6",
                  "先算括号 6+2=8，再算 48÷8=6。",
                  value=6, equation="48/(6+2)", qtype="填空题")
    if d == 4:
        return _q("计算：12 ×（8 - 3）+ 5 = ______。", "65",
                  "先算括号 8-3=5，再算 12×5=60，最后 60+5=65。",
                  value=65, equation="12*(8-3)+5", qtype="填空题")
    return _q("计算：100 - 25 × 3 + 15 ÷ 5 = ______。", "28",
              "先算乘除：25×3=75，15÷5=3；再算 100-75+3=28。",
              value=28, equation="100-25*3+15/5", qtype="填空题")


def powers(rng: random.Random, d: int) -> dict:
    if d == 1:
        return _q("计算：5² = ______。", "25", "5×5=25。",
                  value=25, equation="5*5", qtype="填空题")
    if d == 2:
        return _q("计算：4³ = ______。", "64", "4×4×4=64。",
                  value=64, equation="4*4*4", qtype="填空题")
    if d == 3:
        return _q("计算：1.5² = ______。", "2.25", "1.5×1.5=2.25。",
                  value=2.25, equation="1.5*1.5", qtype="填空题")
    if d == 4:
        return _q("计算：2³ + 3² = ______。", "17",
                  "2³=8，3²=9，8+9=17。",
                  value=17, equation="2*2*2+3*3", qtype="填空题")
    return _q("计算：10³ = ______。", "1000", "10×10×10=1000。",
              value=1000, equation="10*10*10", qtype="填空题")


def equation_solve(rng: random.Random, d: int) -> dict:
    if d == 1:
        return _q("解方程：x + 5 = 12，x = ______。", "7",
                  "x=12-5=7。", value=7, equation="12-5", qtype="填空题")
    if d == 2:
        return _q("解方程：3x = 18，x = ______。", "6",
                  "x=18÷3=6。", value=6, equation="18/3", qtype="填空题")
    if d == 3:
        return _q("解方程：2x + 5 = 17，x = ______。", "6",
                  "2x=12，x=6。", value=6, equation="(17-5)/2", qtype="填空题")
    if d == 4:
        return _q("解方程：5x - 8 = 32，x = ______。", "8",
                  "5x=40，x=8。", value=8, equation="(32+8)/5", qtype="填空题")
    return _q("解方程：4（x + 2）= 36，x = ______。", "7",
              "x+2=9，x=7。", value=7, equation="36/4-2", qtype="填空题")


def simple_calc(rng: random.Random, d: int) -> dict:
    if d == 1:
        return _q("计算：25 × 4 = ______。", "100", "25×4=100。",
                  value=100, equation="25*4", qtype="填空题")
    if d == 2:
        return _q("用简便方法计算：99 × 5 = ______。", "495",
                  "99×5=（100-1）×5=500-5=495。",
                  value=495, equation="99*5", qtype="填空题")
    if d == 3:
        return _q("用简便方法计算：25 × 32 = ______。", "800",
                  "25×32=25×4×8=100×8=800。",
                  value=800, equation="25*32", qtype="填空题")
    if d == 4:
        return _q("用简便方法计算：101 × 36 = ______。", "3636",
                  "101×36=（100+1）×36=3600+36=3636。",
                  value=3636, equation="101*36", qtype="填空题")
    return _q("用简便方法计算：3.6 × 99 + 3.6 = ______。", "360",
              "3.6×99+3.6=3.6×（99+1）=3.6×100=360。",
              value=360, equation="3.6*99+3.6", qtype="填空题")


# ---------------------------------------------------------------- 注册表

TEMPLATE_GENERATORS = {
    "长方形周长": rect_perimeter,
    "正方形周长": square_perimeter,
    "长方形面积": rect_area,
    "正方形面积": square_area,
    "三角形面积": triangle_area,
    "平行四边形面积": parallelogram_area,
    "梯形面积": trapezoid_area,
    "圆的周长": circle_circumference,
    "圆的面积": circle_area,
    "环形面积": ring_area,
    "扇形面积": sector_area,
    "长方体表面积体积": cuboid,
    "正方体表面积体积": cube,
    "圆柱": cylinder,
    "圆锥": cone,
    "组合图形面积": composite_area,
    "图形切拼": cut_combine,
    "长度单位换算": length_units,
    "面积单位换算": area_units,
    "体积容积单位换算": volume_units,
    "质量单位换算": mass_units,
    "时间单位换算": time_units,
    "年月日": date_units,
    "人民币计算": rmb_units,
    "因数与倍数": factors_multiples,
    "质数合数": prime_composite,
    "公因数": common_factor,
    "公倍数": common_multiple,
    "奇偶性": parity,
    "整除特征": divisibility,
    "统计图表": statistics_chart,
    "可能性": possibility,
    "小数运算": decimal_ops,
    "分数运算": fraction_ops,
    "百分数运算": percent_ops,
    "加减运算": add_sub,
    "乘除运算": mul_div,
    "四则混合运算": mixed_ops,
    "平方与立方": powers,
    "方程求解": equation_solve,
    "简便计算": simple_calc,
}


def template_kp_names() -> list[str]:
    return sorted(TEMPLATE_GENERATORS)


def generate_template_questions(kp_name: str, kp_id: str, seed: int) -> list[DemoQuestion]:
    """为一个模板 KP 生成难度 1–5 各一题；同 seed 结果一致。"""
    gen = TEMPLATE_GENERATORS.get(kp_name)
    if gen is None:
        raise KeyError(f"没有模板生成器：{kp_name}")
    questions: list[DemoQuestion] = []
    for difficulty in range(1, 6):
        rng = random.Random(seed + difficulty * 1009)
        data = gen(rng, difficulty)
        q = DemoQuestion(
            q_id=f"q_demo_{kp_id.split('_')[-1]}_{difficulty}",
            kp_id=kp_id,
            kp_name=kp_name,
            difficulty=difficulty,
            qtype=data["qtype"],
            stem=data["stem"],
            answer=data["answer"],
            solution=data["solution"],
            generation_method="template",
            options=data.get("options") or [],
            answer_value=data.get("answer_value"),
            unit=data.get("unit"),
            equation=data.get("equation"),
            seed=seed + difficulty * 1009,
        )
        questions.append(q)
    return questions
