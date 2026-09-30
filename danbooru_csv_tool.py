"""danbooru CSV 加工脚本的共享实现。

被两个入口脚本复用：

- ``modify_danbooru_art.py``：``danbooru_art_full.csv`` -> ``danbooru_art_001.csv``
- ``modify_danbooru_character.py``：``danbooru_character.csv`` -> ``danbooru_character_001.csv``

共同逻辑：按 ``count`` 阈值筛选、只保留指定列、只对指定列做提示词转义、
临时文件 + 原子替换写回、写回前自动备份。

之所以抽成独立模块：**转义规则只有一份实现**。这条规则改过一次（从「处理整行」改成
「只处理 trigger 列」），两份拷贝迟早会漂移，而它直接决定提示词能不能用。

本模块不依赖 ``ra_core`` 与 ComfyUI，可单独导入与测试。
"""

import csv
import os
import re
import shutil
import time
from typing import Iterable, List, Optional, Sequence, Tuple

__all__ = [
    "BACKUP_SUFFIX",
    "backup_output",
    "backup_path_for",
    "convert",
    "escape_paren",
    "find_columns",
    "normalize_header",
    "read_rows",
    "run",
    "transform_text",
    "write_rows",
]

# 备份后缀：danbooru_art_001.csv -> danbooru_art_001.bak
BACKUP_SUFFIX = ".bak"
# 原子替换被占用时的重试次数与间隔（覆盖杀软实时扫描、索引器这类短暂占用）
REPLACE_RETRIES = 5
REPLACE_INTERVAL = 0.3


# ----------------------------------------------------------------------
# 文本处理
# ----------------------------------------------------------------------
def normalize_header(name: str) -> str:
    """归一化表头名：去空白/BOM、转小写、空格与短横线转下划线。"""
    return str(name or "").strip().lstrip("\ufeff").lower().replace("-", "_").replace(" ", "_")


def escape_paren(match: "re.Match") -> str:
    """转义未被转义的括号，已转义的（反斜杠个数为奇数）保持原样。"""
    backslashes, paren = match.group(1), match.group(2)
    if len(backslashes) % 2 == 0:
        return backslashes + "\\" + paren
    return match.group(0)


def transform_text(text: str) -> str:
    """提示词转义：把源文本整理成可直接使用的标签串。

    源数据的 trigger 列是**逗号分隔的多标签串**（``hakurei reimu, touhou``），
    因此必须逐段处理，不能对整串做替换——否则逗号后的空格会变成下划线，
    写出 ``hakurei_reimu,_touhou`` 这种脏标签。

    逐段规则：

    1. 去首尾空白与短横线（``-x-`` -> ``x``）
    2. 段内连续的空白 / 短横线折叠成**单个**下划线（``95---`` -> ``95``、``a  b`` -> ``a_b``）
    3. 折叠重复下划线（``kezune (i- -i)`` -> ``kezune_\\(i_i\\)``）
    4. 空段丢弃（``a,,b`` / 首尾逗号 / 纯空白都收敛掉）

    最后用 ``,`` 重新连接，并转义未转义的括号（已转义的保持原样）。
    """
    parts: List[str] = []
    for segment in str(text or "").split(","):
        segment = segment.strip().strip("-").strip()
        segment = re.sub(r"[\s\-]+", "_", segment)
        segment = re.sub(r"_+", "_", segment)
        if segment:
            parts.append(segment)
    return re.sub(r"(\\*)([\(\)])", escape_paren, ",".join(parts))


def find_columns(header: Sequence[str], columns: Sequence[str]) -> dict:
    """按列名定位各列的索引；缺列时抛出可读异常。"""
    normalized = [normalize_header(cell) for cell in header]
    indexes = {}
    for name in columns:
        if name not in normalized:
            raise ValueError(
                f"表头缺少 {name!r} 列（实际表头：{list(header)}）。"
                f"本工具需要包含 {' / '.join(columns)} 的表头"
            )
        indexes[name] = normalized.index(name)
    return indexes


# ----------------------------------------------------------------------
# 转换
# ----------------------------------------------------------------------
def convert(rows: Iterable[Sequence[str]], min_count: int,
            columns: Sequence[str],
            escape_columns: Sequence[str] = ()) -> Tuple[List[List[str]], dict]:
    """把源行转换为输出行。

    参数:
        rows: CSV 行迭代器（第一行非空者视为表头）
        min_count: 只保留 ``count`` 不小于该值的行
        columns: 输出的列名（顺序即输出顺序）
        escape_columns: 需要做提示词转义的列名（其余列原样保留）

    返回:
        ``(输出行(含表头), 统计信息)``；统计信息含 ``total`` / ``kept`` / ``skipped``。
        输出按 count 从大到小排序（源文件通常已是降序，这里是防御性排序）。
    """
    iterator = iter(rows)
    header: Optional[Sequence[str]] = None
    for row in iterator:
        if row and any(str(cell or "").strip() for cell in row):
            header = row
            break
    if header is None:
        raise ValueError("输入文件是空的，找不到表头")

    indexes = find_columns(header, columns)
    count_index = indexes["count"] if "count" in indexes else None
    if count_index is None:
        raise ValueError(f"输出列里必须包含 count，用于按热度筛选；当前为 {list(columns)}")
    escape_set = {normalize_header(name) for name in escape_columns}
    needs_full_row = max(indexes.values())

    kept: List[Tuple[int, List[str]]] = []
    stats = {"total": 0, "kept": 0, "skipped": 0}

    for row in iterator:
        if not row or not any(str(cell or "").strip() for cell in row):
            continue
        stats["total"] += 1
        if needs_full_row >= len(row):
            stats["skipped"] += 1
            continue

        raw_count = str(row[count_index]).strip()
        if not raw_count.isdigit():
            stats["skipped"] += 1
            continue
        count = int(raw_count)
        if count < min_count:
            continue

        values: List[str] = []
        empty_critical = False
        for position, name in enumerate(columns):
            key = normalize_header(name)
            if key == "count":
                # count 直接写回规范化后的整数值，不依赖它在第几列
                values.append(str(count))
                continue
            cell = str(row[indexes[name]]).strip()
            if key in escape_set:
                cell = transform_text(cell)
            values.append(cell)
            # 第一列（提示词用的那一列）与需要转义的列都不能为空，否则这行没法使用
            if position == 0 or key in escape_set:
                empty_critical = empty_critical or not cell
        if empty_critical:
            stats["skipped"] += 1
            continue

        kept.append((count, values))

    kept.sort(key=lambda item: item[0], reverse=True)
    stats["kept"] = len(kept)
    return [list(columns)] + [row for _, row in kept], stats


# ----------------------------------------------------------------------
# 文件读写
# ----------------------------------------------------------------------
def read_rows(path: str) -> List[List[str]]:
    with open(path, "r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.reader(handle))


def write_rows(path: str, rows: Sequence[Sequence[str]]) -> str:
    """写回文件，返回实际使用的写入方式（供日志说明）。

    优先「先写临时文件 + 原子替换」，中途失败不会留下半截文件。
    如果目标文件被其他程序占用（编辑器打开、网盘同步、杀软实时扫描），
    先重试几次（覆盖短暂占用）；仍失败则退化为直接覆盖写入——
    此时不具备原子性，但避免了「文件被占用就完全用不了」。
    """
    temp_path = f"{path}.tmp"
    with open(temp_path, "w", encoding="utf-8", newline="") as handle:
        csv.writer(handle).writerows(rows)

    last_error: Optional[OSError] = None
    for attempt in range(REPLACE_RETRIES):
        try:
            os.replace(temp_path, path)
            return "原子替换"
        except OSError as error:
            last_error = error
            time.sleep(REPLACE_INTERVAL * (attempt + 1))

    try:
        with open(path, "w", encoding="utf-8", newline="") as handle:
            csv.writer(handle).writerows(rows)
    except OSError:
        raise last_error if last_error is not None else OSError("写入失败")
    finally:
        if os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except OSError:
                pass
    return "直接覆盖（非原子：目标文件被占用）"


def backup_path_for(path: str) -> str:
    """``danbooru_art_001.csv`` -> ``danbooru_art_001.bak``。"""
    return os.path.splitext(path)[0] + BACKUP_SUFFIX


def backup_output(path: str) -> Optional[str]:
    """把已有输出文件备份为 ``<输出名>.bak``，返回备份路径。

    - 输出文件不存在：无需备份，返回 None
    - 备份已存在：不覆盖（保留最初那份原始文件），返回 None

    本函数不打印任何内容，日志由 :func:`run` 统一输出。
    """
    if not os.path.isfile(path):
        return None
    target = backup_path_for(path)
    if os.path.exists(target):
        return None
    shutil.copy2(path, target)
    return target


# ----------------------------------------------------------------------
# 命令行执行
# ----------------------------------------------------------------------
def run(source: str, target: str, min_count: int, columns: Sequence[str],
        escape_columns: Sequence[str], prefix: str, backup: bool = True) -> int:
    """执行一次加工，返回退出码（0 成功）。所有日志都以 ``prefix`` 开头。"""
    source = os.path.abspath(source)
    target = os.path.abspath(target)

    if min_count < 0:
        print(f"{prefix} --min-count 不能为负数：{min_count}")
        return 1
    if source == target:
        print(f"{prefix} 输入与输出不能是同一个文件：{source}")
        return 1
    if not os.path.isfile(source):
        print(f"{prefix} 输入文件不存在：{source}")
        return 1

    try:
        rows = read_rows(source)
        output, stats = convert(rows, min_count, columns, escape_columns)
    except UnicodeDecodeError as error:
        print(f"{prefix} 编码无法解析（请使用 UTF-8）：{source} -> {error}")
        return 1
    except (OSError, csv.Error) as error:
        print(f"{prefix} 读取失败：{source} -> {error}")
        return 1
    except ValueError as error:
        print(f"{prefix} {error}")
        return 1

    try:
        if backup:
            backed = backup_output(target)
            if backed:
                print(f"{prefix} 原文件已备份：{backed}")
            elif os.path.exists(backup_path_for(target)):
                print(f"{prefix} 备份已存在，未覆盖：{backup_path_for(target)}")
        write_mode = write_rows(target, output)
    except OSError as error:
        if isinstance(error, PermissionError):
            print(f"{prefix} 目标文件被其他程序占用，无法写入：{target}")
            print("  请关闭正在打开该文件的编辑器（或暂停网盘同步 / 杀软实时扫描）后重试；"
                  "原文件未被修改。")
        print(f"{prefix} 写入失败：{target} -> {error}")
        return 1

    size = os.path.getsize(target)
    print(f"{prefix} 输入：{source}")
    print(f"{prefix} 输出：{target}（{len(output) - 1} 行 + 表头，{size / 1048576:.2f} MB）")
    print(f"{prefix} 输出列：{', '.join(columns)}；转义列：{', '.join(escape_columns) or '无'}")
    print(f"{prefix} 条件：count >= {min_count}"
          f"；源文件 {stats['total']} 行，保留 {stats['kept']} 行，跳过 {stats['skipped']} 行")
    print(f"{prefix} 写入方式：{write_mode}")
    return 0
