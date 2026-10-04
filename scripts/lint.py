#!/usr/bin/env python3
"""Small, dependency-free Markdown structure checker (not a full GFM parser)."""

import argparse
import json
import re
from pathlib import Path

FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")
HEADING = re.compile(r"^ {0,3}(#{1,6})[ \t]+(.+?)\s*$")
DELIMITER = re.compile(r"^:?-{3,}:?$")


def table_cells(line):
    """Split unescaped pipes outside inline code; return None for ordinary text."""
    cells, part, escaped, code_run, pipes = [], [], False, 0, 0
    i = 0
    while i < len(line):
        char = line[i]
        if escaped:
            part.append(char)
            escaped = False
        elif char == "\\":
            escaped = True
            part.append(char)
        elif char == "`":
            end = i
            while end < len(line) and line[end] == "`":
                end += 1
            run = end - i
            if not code_run:
                code_run = run
            elif code_run == run:
                code_run = 0
            part.append(line[i:end])
            i = end - 1
        elif char == "|" and not code_run:
            cells.append("".join(part).strip())
            part = []
            pipes += 1
        else:
            part.append(char)
        i += 1
    if not pipes:
        return None
    cells.append("".join(part).strip())
    if line.lstrip().startswith("|"):
        cells.pop(0)
    if line.rstrip().endswith("|") and cells and not cells[-1]:
        cells.pop()
    return cells


def check_text(content):
    errors, warnings = [], []
    lines = content.lstrip("\ufeff").splitlines()
    if not content.strip():
        return False, ["文件内容为空"], warnings

    start = 0
    if lines and lines[0].strip() == "---":
        end = next((i for i in range(1, len(lines)) if lines[i].strip() in ("---", "...")), None)
        if end is None:
            return False, ["YAML frontmatter 未闭合"], warnings
        start = end + 1

    fence = None
    visible = []
    headings = []
    first_content = None
    previous_level = None
    for index in range(start, len(lines)):
        line = lines[index]
        number = index + 1
        match = FENCE.match(line)
        if fence:
            if match and match[1][0] == fence[0] and len(match[1]) >= fence[1] and not match[2].strip():
                fence = None
            visible.append((number, ""))
            continue
        if first_content is None and line.strip():
            first_content = number
        if match:
            marker, info = match.groups()
            if marker[0] == "`" and "`" in info:
                errors.append(f"第 {number} 行反引号代码围栏的语言标注包含反引号")
            fence = (marker[0], len(marker), number)
            if not info.strip():
                warnings.append(f"第 {number} 行代码块未指定语言标识")
            visible.append((number, ""))
            continue
        # Four-space/tab-indented code is not document structure.
        if line.startswith("    ") or line.startswith("\t"):
            visible.append((number, ""))
            continue
        visible.append((number, line))
        match = HEADING.match(line)
        if match:
            level = len(match[1])
            headings.append((number, level))
            if previous_level is not None and level > previous_level + 1:
                errors.append(f"第 {number} 行标题层级从 H{previous_level} 跳到 H{level}")
            previous_level = level

    if fence:
        errors.append(f"第 {fence[2]} 行开始的代码块未闭合 ({fence[0] * fence[1]})")
    h1 = [number for number, level in headings if level == 1]
    if not h1:
        errors.append("缺少一级标题 (# 文档标题)")
    elif len(h1) > 1:
        errors.append(f"包含多个一级标题 (共 {len(h1)} 个)，应仅保留一个主标题")
    elif h1[0] != first_content:
        errors.append("一级标题必须是正文首个非空内容 (可位于 frontmatter 后)")
    if not any(level == 2 for _, level in headings):
        warnings.append("未检测到二级标题 (## 章节名称)，可能无法生成目录")

    index = 0
    while index < len(visible) - 1:
        number, line = visible[index]
        cells = table_cells(line)
        next_cells = table_cells(visible[index + 1][1])
        if cells and next_cells and all(DELIMITER.fullmatch(c) for c in next_cells):
            if len(cells) != len(next_cells):
                errors.append(f"第 {number} 行表头与分隔行列数不一致")
            index += 2
            while index < len(visible):
                row_number, row = visible[index]
                row_cells = table_cells(row)
                if not row_cells:
                    break
                if len(row_cells) != len(cells):
                    errors.append(f"第 {row_number} 行表格列数为 {len(row_cells)}，应为 {len(cells)}")
                index += 1
            continue
        if cells and next_cells and line.strip().startswith("|") and visible[index + 1][1].strip().startswith("|"):
            errors.append(f"第 {number} 行表格缺少有效分隔行 (例如 | --- | --- |)")
            index += 2
            while index < len(visible) and table_cells(visible[index][1]):
                index += 1
            continue
        index += 1
    return not errors, errors, warnings


def check_markdown(file_path):
    try:
        content = Path(file_path).read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        return False, [f"无法读取 UTF-8 Markdown: {exc}"], []
    return check_text(content)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("file")
    parser.add_argument("--strict", action="store_true", help="将警告也视为失败")
    parser.add_argument("--json", action="store_true", help="输出机器可读结果")
    args = parser.parse_args(argv)
    passed, errors, warnings = check_markdown(args.file)
    passed = passed and not (args.strict and warnings)
    if args.json:
        print(json.dumps({"ok": passed, "errors": errors, "warnings": warnings}, ensure_ascii=False))
    else:
        for message in errors:
            print(f"✖ {message}")
        for message in warnings:
            print(f"⚠ {message}")
        print("✓ 结构检查通过" if passed else "✖ 结构检查失败")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
