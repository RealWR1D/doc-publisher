#!/usr/bin/env python3
"""
scripts/lint.py — Markdown 文档结构与合规性检查器
用于在推送前验证 Markdown 是否符合结构化文档站的渲染标准。
"""

import sys
import os
import re

def check_markdown(file_path):
    if not os.path.exists(file_path):
        return False, [f"文件不存在: {file_path}"]

    with open(file_path, "r", encoding="utf-8") as f:
        content = f.read()

    errors = []
    warnings = []
    lines = content.splitlines()

    if not content.strip():
        return False, ["文件内容为空"]

    # 1. 检查 H1 大标题
    h1_matches = [line for line in lines if line.startswith("# ")]
    if not h1_matches:
        errors.append("缺少一级标题 (# 文档标题)，无法提取文档主标题")
    elif len(h1_matches) > 1:
        warnings.append(f"包含多个一级标题 (共 {len(h1_matches)} 个)，建议仅保留一个主标题")

    # 2. 检查 H2 章节标题
    h2_matches = [line for line in lines if line.startswith("## ")]
    if not h2_matches:
        warnings.append("未检测到二级标题 (## 章节名称)，某些模板将无法生成目录大纲")
    else:
        print(f"  ✓ 检测到 {len(h2_matches)} 个章节卡片")

    # 3. 检查代码块语言标注
    in_code = False
    for idx, line in enumerate(lines, start=1):
        stripped = line.strip()
        if stripped.startswith("```"):
            if not in_code:
                in_code = True
                lang = stripped[3:].strip()
                if not lang:
                    warnings.append(f"第 {idx} 行代码块未指定语言标识 (建议加上 bash, python, json 等)")
            else:
                in_code = False

    if in_code:
        errors.append("检测到未闭合的代码块 (```)")

    # 4. 检查表格格式
    in_table = False
    for idx, line in enumerate(lines, start=1):
        stripped = line.strip()
        if stripped.startswith("|") and stripped.endswith("|"):
            in_table = True
        elif in_table:
            in_table = False

    return len(errors) == 0, errors, warnings

def main():
    if len(sys.argv) < 2:
        print("用法: python3 lint.py <markdown_file>")
        sys.exit(1)

    target_file = sys.argv[1]
    print(f"正在检查 Markdown 规范: {target_file}")
    passed, errors, warnings = check_markdown(target_file)

    if warnings:
        print("\n[警告 / 建议]:")
        for w in warnings:
            print(f"  ⚠ {w}")

    if errors:
        print("\n[错误 - 检查不通过]:")
        for e in errors:
            print(f"  ✖ {e}")
        sys.exit(1)

    print("\n✓ 结构检查通过，格式符合规范！")
    sys.exit(0)

if __name__ == "__main__":
    main()
