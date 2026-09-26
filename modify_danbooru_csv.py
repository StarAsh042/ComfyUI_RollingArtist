"""danbooru 艺术家 CSV 预处理工具。

把标签中的空格/短横线统一为下划线，并转义未转义的括号
（``hammer_(sunset_beach)`` -> ``hammer_\\(sunset_beach\\)``），
以便作为提示词中的艺术家标签使用。

用法::

    python modify_danbooru_csv.py              # 处理节点目录下的 danbooru_art_001.csv
    python modify_danbooru_csv.py a.csv b.csv  # 也可指定一个或多个文件

安全特性（相较旧版）：
- 写回前生成 ``<文件名>.bak`` 备份（若备份已存在则不覆盖）
- 使用临时文件 + ``os.replace`` 原子替换，中途失败不会损坏原文件
- 文件不存在 / 编码错误时给出明确提示并以非 0 退出码结束
"""

import os
import re
import shutil
import sys

DEFAULT_CSV = os.path.join(os.path.dirname(os.path.abspath(__file__)), "danbooru_art_001.csv")


def escape_paren(match: "re.Match") -> str:
    """转义未被转义的括号，已转义的（反斜杠个数为奇数）保持原样。"""
    backslashes = match.group(1)
    paren = match.group(2)
    if len(backslashes) % 2 == 0:
        return backslashes + "\\" + paren
    return match.group(0)


def transform_line(line: str) -> str:
    """单行转换：去空白 -> 空格/短横线转下划线 -> 转义括号。"""
    normalized = line.strip().replace(" ", "_").replace("-", "_")
    return re.sub(r"(\\*)([\(\)])", escape_paren, normalized)


def modify_csv(path: str, backup: bool = True) -> int:
    """就地规范化 CSV，返回处理的行数。"""
    if not os.path.isfile(path):
        raise FileNotFoundError(f"文件不存在: {path}")

    if backup:
        backup_path = f"{path}.bak"
        if not os.path.exists(backup_path):
            shutil.copy2(path, backup_path)

    with open(path, "r", encoding="utf-8") as handle:
        lines = handle.read().splitlines()

    modified = [transform_line(line) for line in lines]

    temp_path = f"{path}.tmp"
    try:
        with open(temp_path, "w", encoding="utf-8") as handle:
            handle.write("\n".join(modified))
        os.replace(temp_path, path)
    except OSError:
        if os.path.exists(temp_path):
            os.remove(temp_path)
        raise
    return len(modified)


def main(argv: list) -> int:
    targets = argv[1:] or [DEFAULT_CSV]
    failed = 0
    for path in targets:
        try:
            count = modify_csv(path)
        except FileNotFoundError as error:
            print(f"[modify_danbooru_csv] {error}")
            failed += 1
        except UnicodeDecodeError as error:
            print(f"[modify_danbooru_csv] 编码无法解析（请使用 UTF-8）: {path} -> {error}")
            failed += 1
        except OSError as error:
            print(f"[modify_danbooru_csv] 处理失败: {path} -> {error}")
            failed += 1
        else:
            print(f"[modify_danbooru_csv] 已处理 {count} 行: {path}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
