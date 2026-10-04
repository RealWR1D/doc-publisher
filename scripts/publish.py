#!/usr/bin/env python3
"""
scripts/publish.py — 通用文档推送与同步客户端
支持通过 REST API、本地拷贝等适配器将 Markdown 自动发布到目标文档站。
"""

import sys
import os
import json
import argparse
import urllib.request
import urllib.error
import urllib.parse
import shutil
import time

def find_config(explicit_path=None):
    """按优先级寻找配置文件"""
    candidates = []
    if explicit_path:
        candidates.append(explicit_path)

    # 1. 当前工作目录下的 .doc-publisher.json 或 targets.json
    candidates.append(os.path.join(os.getcwd(), ".doc-publisher.json"))
    candidates.append(os.path.join(os.getcwd(), "targets.json"))

    # 2. 脚本所在目录同级的 targets.json
    script_dir = os.path.dirname(os.path.abspath(__file__))
    candidates.append(os.path.join(script_dir, "..", "targets.json"))

    # 3. 用户全局配置目录 ~/.config/doc-publisher/targets.json
    home = os.path.expanduser("~")
    candidates.append(os.path.join(home, ".config", "doc-publisher", "targets.json"))

    for c in candidates:
        if os.path.isfile(c):
            try:
                with open(c, "r", encoding="utf-8") as f:
                    return json.load(f), c
            except Exception as e:
                print(f"WARN: 读取配置文件 {c} 失败: {e}", file=sys.stderr)

    return None, None

def resolve_target(config, target_name):
    """解析目标配置"""
    if not config or "targets" not in config:
        return None

    targets = config.get("targets", {})
    if not target_name:
        target_name = config.get("default")

    if target_name and target_name in targets:
        return targets[target_name], target_name

    return None, target_name

def publish_http_rest(file_path, url, token=None, timeout=30):
    """通过 HTTP REST 协议推送 Markdown"""
    with open(file_path, "rb") as f:
        data = f.read()

    headers = {
        "Content-Type": "text/markdown; charset=utf-8",
        "User-Agent": "doc-publisher-cli/1.0"
    }
    if token:
        headers["X-Upload-Token"] = token

    # 如果 URL 中没有 token 且提供了 token 参数，也追加到 query 中
    final_url = url
    if token and "token=" not in url:
        sep = "&" if "?" in url else "?"
        final_url += f"{sep}token={urllib.parse.quote(token)}"

    req = urllib.request.Request(final_url, data=data, headers=headers, method="POST")

    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            status = resp.status
            body = resp.read().decode("utf-8", errors="replace")
            dt = time.time() - t0
            try:
                res_json = json.loads(body)
                return True, status, res_json, dt
            except json.JSONDecodeError:
                return True, status, {"raw": body}, dt
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        try:
            res_json = json.loads(body)
        except Exception:
            res_json = {"error": body}
        return False, e.code, res_json, 0
    except Exception as e:
        return False, 0, {"error": str(e)}, 0

def publish_local_copy(file_path, dest_path):
    """本地拷贝模式"""
    os.makedirs(os.path.dirname(os.path.abspath(dest_path)), exist_ok=True)
    shutil.copy2(file_path, dest_path)
    return True, 200, {"msg": f"已成功拷贝至 {dest_path}"}, 0

def verify_url(url, timeout=10):
    """验证目标 URL 是否可访问"""
    try:
        req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": "doc-publisher-cli/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status == 200, resp.status
    except Exception:
        # 降级尝试 GET
        try:
            req = urllib.request.Request(url, method="GET", headers={"User-Agent": "doc-publisher-cli/1.0"})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.status == 200, resp.status
        except urllib.error.HTTPError as e:
            return False, e.code
        except Exception:
            return False, 0

def main():
    parser = argparse.ArgumentParser(description="通用文档推送与同步工具")
    parser.add_argument("file", help="待推送的 Markdown 文件路径")
    parser.add_argument("-t", "--target", help="目标别名 (定义在 targets.json 中)")
    parser.add_argument("-u", "--url", help="直接指定推送 URL (覆盖配置)")
    parser.add_argument("-k", "--token", help="直接指定上传令牌 (覆盖配置)")
    parser.add_argument("-c", "--config", help="显式指定 targets.json 路径")
    parser.add_argument("--verify", action="store_true", help="推送完成后验证目标可访问性")
    parser.add_argument("--dry-run", action="store_true", help="仅验证配置和文件，不实际推送")

    args = parser.parse_args()

    if not os.path.isfile(args.file):
        print(f"✖ 错误: 文件不存在: {args.file}", file=sys.stderr)
        sys.exit(1)

    # 1. 解析目标配置
    config, cfg_path = find_config(args.config)
    target_info = None
    target_name = args.target

    if config:
        target_info, target_name = resolve_target(config, args.target)

    # 命令行参数覆盖
    target_type = "http-rest"
    target_url = args.url or (target_info.get("url") if target_info else os.environ.get("DOC_PUBLISHER_URL"))
    target_token = args.token or (target_info.get("token") if target_info else os.environ.get("DOC_PUBLISHER_TOKEN"))
    verify_endpoint = (target_info.get("verify_url") if target_info else None) or os.environ.get("DOC_PUBLISHER_VERIFY_URL")

    if target_info and target_info.get("type") == "local-copy":
        target_type = "local-copy"
        target_dest = target_info.get("dest")

    if target_type == "http-rest" and not target_url:
        print("✖ 错误: 未指定目标 URL。请通过 --url、--target 或配置文件提供推送端点。", file=sys.stderr)
        if cfg_path:
            print(f"  当前读取的配置文件为: {cfg_path}")
        else:
            print("  未检测到任何配置文件 (可复制 targets.example.json 为 targets.json)")
        sys.exit(1)

    print(f"准备推送文档: {args.file}")
    if target_name:
        print(f"目标环境: [{target_name}]")
    if target_type == "http-rest":
        print(f"推送端点: {target_url}")

    if args.dry_run:
        print("✓ Dry-run 模式: 配置与文件校验通过，跳过实际推送。")
        sys.exit(0)

    # 2. 执行推送
    if target_type == "http-rest":
        ok, code, res, dt = publish_http_rest(args.file, target_url, target_token)
    elif target_type == "local-copy":
        ok, code, res, dt = publish_local_copy(args.file, target_dest)
    else:
        print(f"✖ 不支持的目标类型: {target_type}", file=sys.stderr)
        sys.exit(1)

    # 3. 输出结果
    if ok:
        print(f"\n✓ 发布成功！(HTTP {code}, 耗时 {dt:.2f}s)")
        if isinstance(res, dict):
            for k, v in res.items():
                print(f"  • {k}: {v}")
        if args.verify and verify_endpoint:
            print(f"\n正在验证线上渲染状态: {verify_endpoint} ...")
            v_ok, v_code = verify_url(verify_endpoint)
            if v_ok:
                print(f"  ✓ 线上验证通过 (HTTP {v_code})！")
            else:
                print(f"  ⚠ 线上验证未返回 200 (状态码: {v_code})")
    else:
        print(f"\n✖ 发布失败 (状态码 {code}):", file=sys.stderr)
        print(f"  {res}", file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    main()
