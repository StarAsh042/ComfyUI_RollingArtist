import re
import os

def escape_paren(match):
    bs = match.group(1)
    paren = match.group(2)
    if len(bs) % 2 == 0:
        return bs + "\\" + paren
    return match.group(0)

def modify_csv(path):
    with open(path, "r", encoding="utf-8") as f:
        lines = f.read().splitlines()
    modified = []
    for line in lines:
        new_line = line.strip().replace(" ", "_").replace("-", "_")
        new_line = re.sub(r'(\\*)([\(\)])', escape_paren, new_line)
        modified.append(new_line)
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(modified))

if __name__ == "__main__":
    fp = os.path.join(os.path.dirname(__file__), "danbooru_art_001.csv")
    modify_csv(fp) 