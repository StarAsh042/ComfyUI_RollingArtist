"""把 danbooru 画师 CSV 加工成提示词友好的精简 CSV。

从 danbooru 导出的画师文件（默认 ``danbooru_art_full.csv``，格式
``artist,trigger,count,url``）生成节点可直接使用的精简文件（默认 ``danbooru_art_001.csv``）：

1. **按热度保留**：只保留 ``count`` 不小于阈值的行（默认 30），并按 count 从大到小排序
2. **只留三列**：``artist,trigger,count``，并保留首行表头
3. **转义只作用于 trigger 列**：按逗号分段逐段整理（段内空白 / 短横线折叠为单个下划线、
   去掉首尾空白与短横线、丢弃空段），再转义未转义的括号；
   ``artist`` 列原样保留（danbooru 原始标签，可能含连字符与反斜杠）
4. **默认就地覆盖 + 自动备份**：写回前把原有输出文件备份为 ``<输出名>.bak``
   （``danbooru_art_001.csv`` -> ``danbooru_art_001.bak``）

用法::

    python modify_danbooru_art.py                 # 默认 full -> 001，count >= 30
    python modify_danbooru_art.py -n 100          # 改成 count >= 100
    python modify_danbooru_art.py -i a.csv -o b.csv
    python modify_danbooru_art.py --no-backup     # 不生成备份

共同逻辑（筛选 / 列裁剪 / 转义 / 备份 / 原子写回）在 ``danbooru_csv_tool.py`` 里，
与 ``modify_danbooru_character.py`` 共用，避免转义规则出现两份实现。
"""

import argparse
import os
import sys
from typing import List, Optional, Sequence, Tuple

# 兼容两种运行方式：直接 python 运行（脚本目录在 sys.path 上）
# 与作为包的一部分导入（测试用）。
try:  # pragma: no cover - 取决于运行方式
    from .danbooru_csv_tool import (
        backup_output,
        convert as _convert,
        escape_paren,
        find_columns,
        normalize_header,
        read_rows,
        run as _run,
        transform_text,
        write_rows,
    )
except ImportError:  # pragma: no cover - 直接运行脚本时走这里
    from danbooru_csv_tool import (  # type: ignore[no-redef]
        backup_output,
        convert as _convert,
        escape_paren,
        find_columns,
        normalize_header,
        read_rows,
        run as _run,
        transform_text,
        write_rows,
    )

NODE_DIR = os.path.dirname(os.path.abspath(__file__))
PROG = "modify_danbooru_art"
DEFAULT_INPUT = os.path.join(NODE_DIR, "danbooru_art_full.csv")
DEFAULT_OUTPUT = os.path.join(NODE_DIR, "danbooru_art_001.csv")
DEFAULT_MIN_COUNT = 30

# 输出列（顺序即输出顺序）与需要转义的列
OUTPUT_COLUMNS: Tuple[str, ...] = ("artist", "trigger", "count")
ESCAPE_COLUMNS: Tuple[str, ...] = ("trigger",)

# 兼容旧导入名：转义规则本身在 danbooru_csv_tool 里
transform_trigger = transform_text

__all__ = [
    "DEFAULT_INPUT",
    "DEFAULT_MIN_COUNT",
    "DEFAULT_OUTPUT",
    "ESCAPE_COLUMNS",
    "OUTPUT_COLUMNS",
    "backup_output",
    "column_indexes",
    "convert",
    "escape_paren",
    "main",
    "normalize_header",
    "read_rows",
    "transform_trigger",
    "write_rows",
]


def column_indexes(header: Sequence[str]) -> dict:
    """按列名定位 artist / trigger / count；缺列时抛出可读异常。"""
    return find_columns(header, OUTPUT_COLUMNS)


def convert(rows: Sequence[Sequence[str]],
            min_count: int) -> Tuple[List[List[str]], dict]:
    """把源行转换为输出行（列裁剪、按 count 筛选、只转义 trigger）。"""
    return _convert(rows, min_count, OUTPUT_COLUMNS, ESCAPE_COLUMNS)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="modify_danbooru_art.py",
        description="按 count 筛选 danbooru 画师 CSV，只保留 artist/trigger/count 三列，"
                    "并只对 trigger 列做提示词转义。",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "示例：\n"
            "  python modify_danbooru_art.py                # full -> 001，count >= 30\n"
            "  python modify_danbooru_art.py -n 100         # count >= 100\n"
            "  python modify_danbooru_art.py -i a.csv -o b.csv\n"
        ),
    )
    parser.add_argument("-i", "--input", default=DEFAULT_INPUT,
                        help="输入 CSV（默认 danbooru_art_full.csv）")
    parser.add_argument("-o", "--output", default=DEFAULT_OUTPUT,
                        help="输出 CSV（默认 danbooru_art_001.csv，写回前自动备份为 .bak）")
    parser.add_argument("-n", "--min-count", type=int, default=DEFAULT_MIN_COUNT,
                        help=f"只保留 count 不小于该值的行（默认 {DEFAULT_MIN_COUNT}）")
    parser.add_argument("--no-backup", action="store_true",
                        help="写回前不生成 .bak 备份")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    return _run(args.input, args.output, args.min_count,
                OUTPUT_COLUMNS, ESCAPE_COLUMNS, f"[{PROG}]",
                backup=not args.no_backup)


if __name__ == "__main__":
    sys.exit(main())
