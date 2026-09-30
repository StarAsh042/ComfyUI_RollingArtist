"""角色数据的解析、筛选与提示词组装（RollingCharacter 的核心逻辑）。

数据源是 ``danbooru_character_001.csv``（由 ``modify_danbooru_character.py`` 生成）：
``character,copyright,trigger,core_tags,count``，已按 count 降序。

``trigger`` 列的结构经 34,416 行全量校验是**恒定的两段**：

- 第 1 段 == 转义后的 ``character`` 列（34,416/34,416）
- 第 2 段 == 转义后的 ``copyright`` 列（34,416/34,416）

因此默认读 ``trigger`` 列：第 1 段当角色标签（已经转义，可直接使用），其余段就是作品标签。
输出不做权重分配，每个角色一行：``角色标签,作品标签,core_tags``，
多个角色之间用「逗号 + 换行」分隔。

纯逻辑模块，不依赖 ComfyUI，可单独测试。
"""

import re
import threading
from collections import OrderedDict
from typing import Any, Dict, Iterable, List, NamedTuple, Optional, Sequence, Set, Tuple

from .artists import (
    COLUMN_ALL,
    COLUMN_AUTO,
    ArtistRepository,
    is_header_row,
    normalize_cell,
    normalize_column_spec,
    resolve_layout,
)

__all__ = [
    "CharacterRecord",
    "build_character_prompt",
    "build_records",
    "build_payload",
    "clear_records_cache",
    "copyright_options",
    "escape_parens",
    "filter_by_copyright",
    "load_records",
    "parse_copyright_filter",
    "split_core_tags",
    "split_tags",
    "unique_tags",
    "COPYRIGHT_ANY",
]

# 名字列之外的辅助列（按表头名定位，缺失时该字段留空）
AUX_COLUMNS: Tuple[str, ...] = ("copyright", "core_tags", "count")

# 下拉菜单里代表「不限」的哨兵值。copyright 列实测没有空值，
# 与真实作品名不可能撞车；界面文案写在 tooltip 里
COPYRIGHT_ANY = "(不限)"


class CharacterRecord(NamedTuple):
    """一位角色及其附带信息（不可变、内存开销小，适合缓存几万条）。"""

    name: str          # 角色名（名字列的第 1 段，已转义）
    extra_tags: str    # 名字列的其余段（作品标签等，已转义，逗号连接）
    copyright: str     # copyright 列原值（用于筛选与 JSON，保留原始写法）
    core_tags: str     # core_tags 列原值（角色固定外观标签）
    count: int         # 热度（缺失或非法时为 0）


# ----------------------------------------------------------------------
# 文本解析
# ----------------------------------------------------------------------
def split_tags(value: str) -> List[str]:
    """按逗号拆分标签串，去掉首尾空白与空段。"""
    return [part.strip() for part in str(value or "").split(",") if part.strip()]


def _escape_paren(match: "re.Match") -> str:
    """给未被转义的括号补一个反斜杠；已转义的保持原样。"""
    backslashes, paren = match.group(1), match.group(2)
    if len(backslashes) % 2 == 0:
        return backslashes + "\\" + paren
    return match.group(0)


def escape_parens(value: str) -> str:
    """只转义括号（不动空格与连字符）。

    用于把 ``copyright`` 列原值安全地放进提示词：该列形如 ``fate_(series)``，
    括号不转义会被提示词解析器当成权重分组；而它本身不含空格，无需做下划线替换。
    """
    return re.sub(r"(\\*)([\(\)])", _escape_paren, str(value or "").strip())


def split_core_tags(value: str) -> List[str]:
    """拆分 core_tags（与 :func:`split_tags` 同规则，单独命名便于阅读）。"""
    return split_tags(value)


def _spec_is_name(spec: str) -> bool:
    """判断 csv_column 是否写的是「列名」而不是 auto / all / 列序号。"""
    return spec not in ("", COLUMN_AUTO, COLUMN_ALL) and not spec.isdigit()


def _locate(header_cells: Sequence[str]) -> Dict[str, int]:
    """按表头定位辅助列（copyright / core_tags / count）。"""
    indexes: Dict[str, int] = {}
    for name in AUX_COLUMNS:
        if name in header_cells:
            indexes[name] = header_cells.index(name)
    return indexes


def _cell(row: Sequence[str], index: Optional[int]) -> str:
    if index is None or index >= len(row):
        return ""
    return str(row[index]).strip()


def build_records(rows: Sequence[Sequence[str]],
                  column: Optional[str] = "trigger") -> List[CharacterRecord]:
    """把 CSV 行解析成角色记录列表。

    参数:
        column: 名字来源列，``auto`` / ``all`` / 列序号 / 列名（默认 ``trigger``）。
                ``all`` 对角色节点没有意义，会直接报错。

    行内保持原始顺序（源文件已按 count 降序），空行与名字为空的行会被跳过。
    """
    first: Optional[Sequence[str]] = None
    header_position = 0
    for position, row in enumerate(rows):
        if row and any(str(cell or "").strip() for cell in row):
            first, header_position = row, position
            break
    if first is None:
        return []

    spec = normalize_column_spec(column)
    if spec == COLUMN_ALL:
        raise ValueError(
            "[RollingCharacter] csv_column=all 对角色数据没有意义："
            "请指定具体列（默认 trigger，即「角色标签,作品标签」）"
        )

    cells = [normalize_cell(cell) for cell in first]
    # 名字写的是列名且能在首行找到时，首行就是表头（即使含未收录的额外列名）
    has_header = is_header_row(first) or (_spec_is_name(spec) and spec in cells)
    name_index = int(resolve_layout(first, spec, has_header))
    aux = _locate(cells) if has_header else {}
    start = header_position + 1 if has_header else 0

    records: List[CharacterRecord] = []
    for position, row in enumerate(rows):
        if position < start:
            continue
        if not row or not any(str(cell or "").strip() for cell in row):
            continue
        if name_index >= len(row):
            continue
        segments = split_tags(row[name_index])
        if not segments:
            continue
        raw_count = _cell(row, aux.get("count"))
        records.append(CharacterRecord(
            name=segments[0],
            extra_tags=",".join(segments[1:]),
            copyright=_cell(row, aux.get("copyright")),
            core_tags=_cell(row, aux.get("core_tags")),
            count=int(raw_count) if raw_count.isdigit() else 0,
        ))
    return records


# ----------------------------------------------------------------------
# 作品筛选
# ----------------------------------------------------------------------
def parse_copyright_filter(text: Optional[str]) -> List[str]:
    """解析作品过滤串：逗号分隔、去空白、保持顺序去重（比较时忽略大小写）。"""
    names: List[str] = []
    seen: Set[str] = set()
    for item in str(text or "").split(","):
        name = item.strip()
        key = name.lower()
        if name and key not in seen:
            seen.add(key)
            names.append(name)
    return names


def filter_by_copyright(records: Sequence[CharacterRecord],
                        text: Optional[str]) -> List[int]:
    """按作品名筛选，返回允许的记录下标（空过滤串返回全部）。

    同时接受 ``copyright`` 列的原始写法（``fate_(series)``）与
    ``trigger`` 里转义后的写法（``fate_\\(series\\)``），比较忽略大小写。
    """
    wanted = {name.lower() for name in parse_copyright_filter(text)}
    if not wanted:
        return list(range(len(records)))

    allowed: List[int] = []
    for index, record in enumerate(records):
        forms = {record.copyright.lower()}
        forms.update(tag.lower() for tag in split_tags(record.extra_tags))
        if forms & wanted:
            allowed.append(index)
    return allowed


def copyright_options(records: Sequence[CharacterRecord]) -> List[str]:
    """列出可进下拉菜单的全部作品名（按 **count 总和**降序，相同时按名字升序）。

    排序依据是「该作品下所有角色的 ``count`` 相加」——反映的是「这个作品整体有多热门」，
    而不是「这个作品里有多少个角色」（后者会让 ``original`` 这种大杂烩永远排第一）。
    并列时按名字升序，保证顺序稳定可复现。

    默认数据（34,416 行）共 3,460 个作品，全部列出：现代前端的下拉可以直接打字筛选，
    所以不再需要"只放常用作品 + 手输长尾"那套折中做法。
    """
    totals: Dict[str, int] = {}
    for record in records:
        name = record.copyright.strip()
        if name:
            totals[name] = totals.get(name, 0) + record.count
    ordered = sorted(totals.items(), key=lambda item: (-item[1], item[0]))
    return [name for name, _ in ordered]


# ----------------------------------------------------------------------
# 标签去重
# ----------------------------------------------------------------------
def unique_tags(tags: Iterable[str]) -> List[str]:
    """按出现顺序去重（保留首次出现的位置）。"""
    merged: List[str] = []
    seen: Set[str] = set()
    for tag in tags:
        if tag and tag not in seen:
            seen.add(tag)
            merged.append(tag)
    return merged


def build_character_prompt(records: Sequence[CharacterRecord],
                           include_core_tags: bool = True) -> str:
    """组装角色输出：**每个角色一行**，格式为 ``角色标签,作品标签,core_tags``。

    - 不带权重：角色标签直接输出，不套 ``(名称:权重)``
    - 作品标签优先取名字列的其余段（``trigger`` 列的第 2 段，已转义）；
      名字列不是 ``trigger`` 时退回 ``copyright`` 列原值，并只转义其中的括号
      （该列不含空格与连字符，无需做下划线替换）
    - 外观标签取自 ``core_tags``，行内去重（源数据里存在同一角色重复列同一标签的情况）；
      ``include_core_tags=False`` 时每行只输出 ``角色标签,作品标签``
    - 空的部分自动省略，不会留下多余逗号（没有作品或外观标签时就只有角色标签）
    - 多个角色用「逗号 + 换行」分隔：每一行末尾都补一个逗号（最后一行不补），
      这样把多行拼成一行时标签之间不会粘在一起；单个角色时不带尾逗号
    """
    lines: List[str] = []
    for record in records:
        copyright_tag = record.extra_tags or escape_parens(record.copyright)
        parts = [record.name]
        parts.extend(split_tags(copyright_tag))
        if include_core_tags:
            parts.extend(unique_tags(split_core_tags(record.core_tags)))
        line = ",".join(part for part in parts if part)
        if line:
            lines.append(line)
    return ",\n".join(lines)


# ----------------------------------------------------------------------
# 输出组装
# ----------------------------------------------------------------------
def build_payload(records: Sequence[CharacterRecord],
                  top_names: Optional[Set[str]]) -> Dict[str, Any]:
    """组装 ``characters_json``：角色标签 / 作品 / 是否 Top / 热度 / 外观标签。"""
    return {
        "characters": [
            {
                "name": record.name,
                "copyright": record.copyright,
                "top": top_names is None or record.name in top_names,
                "count": record.count,
                "core_tags": unique_tags(split_core_tags(record.core_tags)),
            }
            for record in records
        ],
        "status": "OK",
    }


# ----------------------------------------------------------------------
# 记录缓存
# ----------------------------------------------------------------------
# 解析 34,416 行需要构造三万多条记录，按「文件签名 + 列」缓存，
# 多个节点实例共享，避免每次执行都重解析。
_RECORDS_CACHE: "OrderedDict[Tuple[str, str, int, int], List[CharacterRecord]]" = OrderedDict()
_RECORDS_LOCK = threading.RLock()
_MAX_RECORDS_CACHE = 4


def load_records(repository: ArtistRepository, custom_path: Optional[str] = None,
                 column: Optional[str] = "trigger") -> List[CharacterRecord]:
    """读取并缓存角色记录（内容未变化时直接命中缓存）。"""
    rows, stat = repository.load_rows(custom_path)
    if stat is None:
        return build_records(rows, column)

    key = (stat[0], normalize_column_spec(column), stat[1], stat[2])
    with _RECORDS_LOCK:
        cached = _RECORDS_CACHE.get(key)
        if cached is not None:
            _RECORDS_CACHE.move_to_end(key)
            return cached

    records = build_records(rows, column)

    if records:
        with _RECORDS_LOCK:
            _RECORDS_CACHE[key] = records
            _RECORDS_CACHE.move_to_end(key)
            while len(_RECORDS_CACHE) > _MAX_RECORDS_CACHE:
                _RECORDS_CACHE.popitem(last=False)
    return records


def clear_records_cache() -> None:
    """清空角色记录缓存（测试或手动热重载时使用）。"""
    with _RECORDS_LOCK:
        _RECORDS_CACHE.clear()
