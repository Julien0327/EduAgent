"""AIC（Agent Identity Code）解析 / 校验工具。

对官方 acps_sdk.aic 公开语义的等价实现，用于离线校验与单测。
算法与字段均来自已核实源码（ACPs-community/acps-sdk/acps_sdk/aic/__init__.py）：

- AIC 为 10 级点分结构：OID 前缀(1-4级) . 版本(5) . ARSP(6) . Vendor(7)
  . Ontology SN(8) . Instance SN(9) . CRC(10)。
- 前缀固定 `1.2.156.3088`；第 1-4 级纯数字；第 5-10 级 Base36(0-9A-Z)。
- CRC 使用 CRC-16/CCITT-FALSE(poly=0x1021, init=0xFFFF, refin/refout=False, xorout=0x0000)
  对「1-9 级本体码(ascii) + ARSP 内部 SALT」计算，再 Base36 编码为 4 位。
- 第 9 级（Instance SN）全 0 表示「本体 AIC」，否则为「实体 AIC」。

注意：CRC 校验需要 ARSP 内部 SALT，SALT 不对外公开；故 `AicValidator` 仅在有 SALT 时
才做 CRC 校验，否则仅做格式校验（与官方 `validate_aic_format` 一致）。
"""
from __future__ import annotations

import re
from dataclasses import dataclass

AIC_PREFIX = "1.2.156.3088"
BASE36_ALPHABET = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
_BASE36_INDEX = {ch: i for i, ch in enumerate(BASE36_ALPHABET)}

# 各级段长度约束（1-4 级为 OID 数字，5-10 级为 Base36）
_SEGMENT_CONSTRAINTS = {
    5: (1, 1),   # Version
    6: (1, 6),   # ARSP
    7: (1, 6),   # Vendor
    8: (1, 9),   # Ontology SN
    9: (1, 9),   # Instance SN
    10: (4, 4),  # CRC
}
_RE_DIGITS = re.compile(r"^[0-9]+$")
_RE_BASE36 = re.compile(r"^[0-9A-Z]+$")


@dataclass(frozen=True)
class AicInfo:
    """AIC 解析结果（字段语义与官方 AICInfo 一致）。"""

    raw: str
    normalized: str
    prefix: str
    version: str
    arsp: str
    vendor: str
    ontology_serial: str
    instance_serial: str
    checksum: str

    @property
    def is_ontology(self) -> bool:
        return set(self.instance_serial) == {"0"}

    @property
    def is_entity(self) -> bool:
        return not self.is_ontology

    @property
    def ontology_prefix(self) -> str:
        """本体前缀（1-8 级），用于匹配同一本体的所有实体。"""
        return ".".join(
            [self.prefix, self.version, self.arsp, self.vendor, self.ontology_serial]
        )


def _normalize(aic: str) -> str:
    return re.sub(r"\s+", "", str(aic)).upper()


def _split(aic: str) -> list[str]:
    normalized = _normalize(aic)
    if not normalized:
        return []
    parts = normalized.split(".")
    return [] if any(p == "" for p in parts) else parts


def base36_encode(num: int, length: int = 0) -> str:
    """非负整数 → 大写 Base36 字符串（length>0 时左侧补 0）。"""
    if num < 0:
        raise ValueError("num 必须是非负整数")
    if num == 0:
        encoded = "0"
    else:
        digits = []
        while num > 0:
            num, rem = divmod(num, 36)
            digits.append(BASE36_ALPHABET[rem])
        encoded = "".join(reversed(digits))
    return encoded.rjust(length, "0") if length > 0 else encoded


def validate_aic_format(aic: str, expected_prefix: str = AIC_PREFIX) -> tuple[bool, str | None]:
    """校验 AIC 格式（不校验 CRC）。返回 (是否有效, 错误描述)。"""
    if not aic:
        return False, "AIC 不能为空"
    parts = _split(aic)
    if len(parts) != 10:
        return False, f"AIC 必须包含 10 段，当前为 {len(parts)} 段"
    prefix_parts = expected_prefix.split(".")
    if parts[: len(prefix_parts)] != prefix_parts:
        return False, f"前缀不匹配，期望 {expected_prefix}，实际 {'.'.join(parts[:len(prefix_parts)])}"
    for i in range(4):
        if not _RE_DIGITS.fullmatch(parts[i]):
            return False, f"第 {i + 1} 级必须为纯数字，实际为 '{parts[i]}'"
    for level in range(5, 11):
        seg = parts[level - 1]
        min_len, max_len = _SEGMENT_CONSTRAINTS[level]
        if not _RE_BASE36.fullmatch(seg):
            return False, f"第 {level} 级必须为 Base36 字符，实际为 '{seg}'"
        if not (min_len <= len(seg) <= max_len):
            return False, f"第 {level} 级长度必须在 {min_len}-{max_len} 之间，实际为 {len(seg)}"
    return True, None


def parse_aic(aic: str, expected_prefix: str = AIC_PREFIX) -> AicInfo | None:
    """解析 AIC，格式非法返回 None（不校验 CRC）。"""
    valid, _ = validate_aic_format(aic, expected_prefix)
    if not valid:
        return None
    parts = _split(aic)
    return AicInfo(
        raw=aic,
        normalized=".".join(parts),
        prefix=".".join(parts[:4]),
        version=parts[4],
        arsp=parts[5],
        vendor=parts[6],
        ontology_serial=parts[7],
        instance_serial=parts[8],
        checksum=parts[9],
    )


def is_ontology_aic(aic: str) -> bool:
    info = parse_aic(aic)
    return info is not None and info.is_ontology


def is_entity_aic(aic: str) -> bool:
    info = parse_aic(aic)
    return info is not None and info.is_entity


def _crc16_ccitt_false(data: bytes) -> int:
    """CRC-16/CCITT-FALSE：poly=0x1021, init=0xFFFF, refin/refout=False, xorout=0x0000。"""
    crc = 0xFFFF
    for b in data:
        crc ^= (b << 8) & 0xFFFF
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
    return crc & 0xFFFF


class AicValidator:
    """AIC 完整校验器（含 CRC，需 ARSP 内部 SALT）。"""

    def __init__(self, salt: bytes | str | None = None):
        if salt is None:
            self._salt = None
        elif isinstance(salt, bytes):
            self._salt = salt
        elif isinstance(salt, str):
            hex_str = salt.strip()
            if hex_str.lower().startswith("0x"):
                hex_str = hex_str[2:]
            if len(hex_str) % 2 != 0:
                hex_str = "0" + hex_str
            self._salt = bytes.fromhex(hex_str)
        else:
            raise TypeError("salt 必须是 bytes 或 str")
        self.last_error: str | None = None

    @property
    def has_salt(self) -> bool:
        return self._salt is not None

    def calculate_checksum(self, body_1_9: str) -> str | None:
        """对 1-9 级本体码计算 4 位 Base36 CRC；未配置 SALT 返回 None。"""
        if self._salt is None:
            return None
        data = _normalize(body_1_9).encode("ascii") + self._salt
        return base36_encode(_crc16_ccitt_false(data), 4)

    def validate(self, aic: str, require_crc: bool = False) -> bool:
        self.last_error = None
        valid, error = validate_aic_format(aic)
        if not valid:
            self.last_error = error
            return False
        if self._salt is None:
            if require_crc:
                self.last_error = "未配置 SALT，无法验证 CRC"
                return False
            return True
        info = parse_aic(aic)
        if info is None:
            self.last_error = "解析 AIC 失败"
            return False
        expected = self.calculate_checksum(
            ".".join(
                [info.prefix, info.version, info.arsp, info.vendor,
                 info.ontology_serial, info.instance_serial]
            )
        )
        if expected != info.checksum:
            self.last_error = f"CRC 校验失败，期望 {expected}，实际 {info.checksum}"
            return False
        return True
