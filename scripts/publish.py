#!/usr/bin/env python3
"""Validate and publish one Markdown file via HTTP or atomic local copy."""

import argparse
import hashlib
import ipaddress
import json
import math
import os
import re
import stat
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

from lint import check_text

MAX_RESPONSE_BYTES = 2 * 1024 * 1024
MISSING = object()


class PublishError(Exception):
    def __init__(self, message, stage="validation", code=1):
        super().__init__(message)
        self.stage = stage
        self.code = code


def find_config(explicit_path=None):
    """Read the first existing file; an explicit/broken config never falls through."""
    candidates = [Path(explicit_path).expanduser()] if explicit_path else [
        Path.cwd() / ".doc-publisher.json",
        Path.cwd() / "targets.json",
        Path(__file__).resolve().parent.parent / "targets.json",
        Path.home() / ".config/doc-publisher/targets.json",
    ]
    for path in candidates:
        if not path.exists() and not explicit_path:
            continue
        try:
            config = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, ValueError) as exc:
            # Do not echo config values or parser snippets containing credentials.
            raise PublishError(f"配置无法读取或不是有效 JSON: {path} ({type(exc).__name__})") from exc
        if not isinstance(config, dict) or not isinstance(config.get("targets"), dict) or not config["targets"]:
            raise PublishError("配置必须包含非空 targets 对象")
        if any(not isinstance(name, str) or not name or not isinstance(value, dict) for name, value in config["targets"].items()):
            raise PublishError("每个 target 必须是命名对象")
        default = config.get("default")
        if default is not None and (not isinstance(default, str) or default not in config["targets"]):
            raise PublishError("配置的 default 必须指向已有 target")
        return config, str(path.resolve())
    return None, None


def resolve_target(config, target_name):
    if config is None:
        if target_name:
            raise PublishError("指定了 --target，但未找到目标配置")
        return None, None
    name = target_name or config.get("default")
    if not name or name not in config["targets"]:
        raise PublishError("目标不存在或没有 default；请明确指定已有 --target")
    return dict(config["targets"][name]), name


def validate_url(url, allow_http=False):
    if not isinstance(url, str) or not url or any(ord(c) <= 32 for c in url):
        raise PublishError("URL 必须是有效的 HTTP(S) 地址")
    try:
        parts = urllib.parse.urlsplit(url)
        port = parts.port
    except ValueError as exc:
        raise PublishError("URL 格式或端口无效") from exc
    if parts.scheme not in ("http", "https") or not parts.hostname or parts.username is not None or parts.password is not None or parts.fragment:
        raise PublishError("URL 必须是 HTTP(S)，且不能含用户名、密码或 fragment")
    if port is not None and not 1 <= port <= 65535:
        raise PublishError("URL 端口无效")
    loopback = parts.hostname == "localhost"
    try:
        loopback = loopback or ipaddress.ip_address(parts.hostname).is_loopback
    except ValueError:
        pass
    if parts.scheme == "http" and not (allow_http or loopback):
        raise PublishError("远端地址必须使用 HTTPS；仅明确允许时使用 --allow-http")
    return url


def safe_url(url):
    parts = urllib.parse.urlsplit(url)
    query = urllib.parse.urlencode([(key, "[REDACTED]") for key, _ in urllib.parse.parse_qsl(parts.query, keep_blank_values=True)])
    return urllib.parse.urlunsplit((parts.scheme, parts.netloc, parts.path, query, ""))


def number(value, name, minimum, maximum, integer=False):
    kind = int if integer else (int, float)
    if isinstance(value, bool) or not isinstance(value, kind) or not minimum <= value <= maximum or not math.isfinite(value):
        raise PublishError(f"{name} 必须在 {minimum}..{maximum} 范围内")
    return value


def string(value, name):
    if not isinstance(value, str) or not value:
        raise PublishError(f"{name} 必须是非空字符串")
    return value


def matches(actual, expected):
    """Keep missing fields and JSON booleans distinct from null and numbers."""
    if actual is MISSING:
        return False
    if isinstance(actual, bool) or isinstance(expected, bool):
        return type(actual) is type(expected) and actual == expected
    return actual == expected


def field_value(data, field):
    for key in field.split("."):
        if not isinstance(data, dict) or key not in data:
            return MISSING
        data = data[key]
    return data


def validate_profile(profile, args, cfg_path, env):
    selected = dict(profile or {})
    target_type = selected.get("type", "http-rest")
    if target_type not in ("http-rest", "local-copy"):
        raise PublishError("不支持的目标类型；可用 http-rest 或 local-copy")
    allow_http = args.allow_http or selected.get("allow_http", False)
    if not isinstance(selected.get("allow_http", False), bool):
        raise PublishError("allow_http 必须是布尔值")
    selected["allow_http"] = allow_http
    if target_type == "local-copy":
        if args.url or args.token or args.verify_url or args.verify_content is not None or args.verify_sha256:
            raise PublishError("local-copy 不能混用 HTTP 地址、凭据或远端验证参数")
        dest = Path(string(selected.get("dest"), "dest")).expanduser()
        # Paths in a config are relative to the config, never the caller's CWD.
        if not dest.is_absolute():
            dest = Path(cfg_path).parent / dest
        selected["dest"] = str(dest.absolute())
        selected["type"] = target_type
        return selected

    old_url = selected.get("url")
    overridden = args.url is not None and args.url != old_url
    if overridden:
        # Endpoint-bound credentials, headers and status/preview URLs cannot travel.
        for key in ("token", "token_env", "headers", "auth", "token_header", "token_param", "verify_url", "verify", "build"):
            selected.pop(key, None)
    url = args.url if args.url is not None else (selected.get("url") if profile is not None else env.get("DOC_PUBLISHER_URL"))
    selected["url"] = validate_url(url, allow_http)
    token_env = selected.get("token_env")
    if args.token is not None:
        token = args.token
    elif token_env:
        token = env.get(string(token_env, "token_env"))
        if not token:
            raise PublishError("token_env 指定的环境变量未设置或为空")
    else:
        token = selected.get("token") or env.get("DOC_PUBLISHER_TOKEN")
    if token is not None and (not isinstance(token, str) or any(c in token for c in "\r\n")):
        raise PublishError("Token 必须是不含换行的字符串")
    selected["token"] = token
    selected["type"] = target_type
    selected["auth"] = selected.get("auth", "header")
    if selected["auth"] not in ("header", "bearer", "query", "none"):
        raise PublishError("auth 可用 header、bearer、query 或 none")
    selected["token_header"] = string(selected.get("token_header", "X-Upload-Token"), "token_header")
    if not re.fullmatch(r"[A-Za-z0-9-]+", selected["token_header"]) or selected["token_header"].lower() in ("host", "content-length", "content-type"):
        raise PublishError("token_header 必须是有效请求头名称")
    selected["token_param"] = string(selected.get("token_param", "token"), "token_param")
    headers = selected.get("headers", {})
    if not isinstance(headers, dict):
        raise PublishError("headers 必须是对象")
    for key, value in headers.items():
        if not isinstance(key, str) or not re.fullmatch(r"[A-Za-z0-9-]+", key) or not isinstance(value, str) or any(c in value for c in "\r\n"):
            raise PublishError("headers 中含无效请求头")
        if key.lower() in ("host", "content-length", "content-type"):
            raise PublishError("headers 不能覆盖 Host、Content-Length 或 Content-Type")
    selected["headers"] = headers
    selected["method"] = selected.get("method", "POST")
    if selected["method"] not in ("POST", "PUT", "PATCH"):
        raise PublishError("method 可用 POST、PUT 或 PATCH")
    selected["format"] = selected.get("format", "raw")
    if selected["format"] not in ("raw", "json", "multipart"):
        raise PublishError("format 可用 raw、json 或 multipart")
    for key, default in (("content_field", "content"), ("file_field", "file")):
        selected[key] = string(selected.get(key, default), key)
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_-]*", selected[key]):
            raise PublishError(f"{key} 必须是简单字段名称")
    if selected["content_field"] == "filename":
        raise PublishError("content_field 不能与保留字段 filename 重名")
    selected["timeout"] = number(selected.get("timeout", 30), "timeout", 0.1, 120)
    response = selected.get("response", {})
    if not isinstance(response, dict):
        raise PublishError("response 必须是对象")
    if response:
        string(response.get("success_field"), "response.success_field")
        if "success_value" not in response:
            raise PublishError("response 必须包含 success_value")
    selected["response"] = response

    build = selected.get("build")
    if build is not None:
        if not isinstance(build, dict):
            raise PublishError("build 必须是对象")
        build = dict(build)
        validate_url(build.get("url"), allow_http)
        if origin(build["url"]) != origin(selected["url"]):
            raise PublishError("build.url 必须与上传地址同源，避免转发凭据")
        string(build.get("field", "status"), "build.field")
        build["field"] = build.get("field", "status")
        for key, default in (("success_values", ["published", "completed"]), ("failure_values", ["failed", "error"])):
            build[key] = build.get(key, default)
            if not isinstance(build[key], list) or not build[key] or any(not isinstance(v, str) for v in build[key]):
                raise PublishError(f"build.{key} 必须是非空字符串列表")
        if set(build["success_values"]) & set(build["failure_values"]):
            raise PublishError("构建成功和失败状态不能重叠")
        if build.get("job_field") is not None:
            string(build["job_field"], "build.job_field")
            build["job_param"] = string(build.get("job_param", "job_id"), "build.job_param")
        build["attempts"] = number(build.get("attempts", 5), "build.attempts", 1, 30, True)
        build["interval"] = number(build.get("interval", 1), "build.interval", 0, 10)
        selected["build"] = build

    verify = selected.get("verify", {})
    if not isinstance(verify, dict):
        raise PublishError("verify 必须是对象")
    verify = dict(verify)
    verify["url"] = args.verify_url or verify.get("url") or selected.get("verify_url") or env.get("DOC_PUBLISHER_VERIFY_URL")
    verify["mode"] = verify.get("mode", "available")
    if args.verify_content is not None:
        verify.update(mode="contains", expected=args.verify_content)
    elif args.verify_sha256:
        verify["mode"] = "sha256"
    if verify["mode"] not in ("available", "contains", "sha256", "json"):
        raise PublishError("verify.mode 可用 available、contains、sha256 或 json")
    if verify["mode"] == "contains":
        string(verify.get("expected"), "verify.expected")
    if verify["mode"] == "json":
        string(verify.get("field"), "verify.field")
        if "expected" not in verify:
            raise PublishError("JSON 验证必须指定 expected")
    if verify.get("field") is not None:
        string(verify["field"], "verify.field")
    verify["attempts"] = number(verify.get("attempts", 3), "verify.attempts", 1, 30, True)
    verify["interval"] = number(verify.get("interval", 1), "verify.interval", 0, 10)
    if verify.get("url") is not None or args.verify:
        validate_url(verify.get("url"), allow_http)
    if not args.verify and (args.verify_url or args.verify_content is not None or args.verify_sha256):
        raise PublishError("验证参数必须与 --verify 一起使用")
    selected["verify"] = verify
    return selected


def origin(url):
    parts = urllib.parse.urlsplit(url)
    return parts.scheme, parts.hostname, parts.port or (443 if parts.scheme == "https" else 80)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Never redirect a mutating request or forward credentials implicitly.
        return None


def request(url, method="GET", data=None, headers=None, timeout=30, stage="upload"):
    req = urllib.request.Request(url, data=data, headers=headers or {}, method=method)
    try:
        with urllib.request.build_opener(NoRedirect()).open(req, timeout=timeout) as response:
            body = response.read(MAX_RESPONSE_BYTES + 1)
            if len(body) > MAX_RESPONSE_BYTES:
                raise PublishError("响应超过 2 MiB 限制", stage, 2)
            if not 200 <= response.status < 300:
                raise PublishError(f"HTTP {response.status}", stage, 2)
            return response.status, body
    except urllib.error.HTTPError as exc:
        # Do not print server bodies, which may echo tokens or documents.
        raise PublishError(f"HTTP {exc.code}；重定向不会自动跟随" if 300 <= exc.code < 400 else f"HTTP {exc.code}", stage, 2) from exc
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise PublishError(f"请求失败 ({type(exc).__name__})", stage, 2) from exc


def auth_request(profile, url):
    headers = {"User-Agent": "doc-publisher-cli/2.0", **profile["headers"]}
    token = profile.get("token")
    if token:
        if profile["auth"] == "header":
            headers[profile["token_header"]] = token
        elif profile["auth"] == "bearer":
            headers["Authorization"] = f"Bearer {token}"
        elif profile["auth"] == "query":
            parts = urllib.parse.urlsplit(url)
            query = [(k, v) for k, v in urllib.parse.parse_qsl(parts.query, keep_blank_values=True) if k != profile["token_param"]]
            query.append((profile["token_param"], token))
            url = urllib.parse.urlunsplit((parts.scheme, parts.netloc, parts.path, urllib.parse.urlencode(query), ""))
    return url, headers


def payload(data, file_path, profile):
    if profile["format"] == "raw":
        return data, "text/markdown; charset=utf-8"
    if profile["format"] == "json":
        return json.dumps({profile["content_field"]: data.decode("utf-8"), "filename": Path(file_path).name}, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8"
    boundary = "doc-publisher-" + uuid.uuid4().hex
    # Quotes, CRLF and non-ASCII filenames cannot inject multipart headers.
    filename = re.sub(r"[^A-Za-z0-9_.-]", "_", Path(file_path).name)
    prefix = f'--{boundary}\r\nContent-Disposition: form-data; name="{profile["file_field"]}"; filename="{filename}"\r\nContent-Type: text/markdown; charset=utf-8\r\n\r\n'.encode("ascii")
    return prefix + data + f"\r\n--{boundary}--\r\n".encode("ascii"), f"multipart/form-data; boundary={boundary}"


def parse_response(body):
    try:
        return json.loads(body)
    except (ValueError, UnicodeError):
        return None


def upload(data, file_path, profile):
    url, headers = auth_request(profile, profile["url"])
    body, content_type = payload(data, file_path, profile)
    headers["Content-Type"] = content_type
    started = time.monotonic()
    code, response_body = request(url, profile["method"], body, headers, profile["timeout"])
    response = parse_response(response_body)
    if isinstance(response, dict):
        if response.get("success") is False or response.get("ok") is False or response.get("error") or response.get("status") in ("failed", "error"):
            raise PublishError("服务端返回业务失败；未确认发布完成", "upload", 2)
    contract = profile["response"]
    if contract and (response is None or not matches(field_value(response, contract["success_field"]), contract["success_value"])):
        raise PublishError("响应未满足配置的成功条件", "upload", 2)
    return code, round(time.monotonic() - started, 3), response


def poll_build(profile, response):
    build = profile["build"]
    status_url = build["url"]
    if build.get("job_field"):
        job = field_value(response, build["job_field"])
        if isinstance(job, bool) or not isinstance(job, (str, int)) or job == "":
            raise PublishError("上传响应缺少有效构建任务 ID", "build", 3)
        parts = urllib.parse.urlsplit(status_url)
        query = [(k, v) for k, v in urllib.parse.parse_qsl(parts.query, keep_blank_values=True) if k != build["job_param"]]
        query.append((build["job_param"], str(job)))
        status_url = urllib.parse.urlunsplit((parts.scheme, parts.netloc, parts.path, urllib.parse.urlencode(query), ""))
    url, headers = auth_request(profile, status_url)
    for attempt in range(build["attempts"]):
        # Status requests may be repeated; uploads are never automatically retried.
        _, body = request(url, headers=headers, timeout=profile["timeout"], stage="build")
        state = field_value(parse_response(body), build["field"])
        if state in build["success_values"]:
            return
        if state in build["failure_values"]:
            raise PublishError("服务端报告构建失败", "build", 3)
        if attempt + 1 < build["attempts"]:
            time.sleep(build["interval"])
    raise PublishError("构建状态未在轮询次数内确认完成", "build", 3)


def verify(profile, digest):
    options = profile["verify"]
    mode = options["mode"]
    for attempt in range(options["attempts"]):
        try:
            # Preview URLs receive no upload credentials or private custom headers.
            _, body = request(options["url"], headers={"User-Agent": "doc-publisher-cli/2.0"}, timeout=profile["timeout"], stage="verification")
            if mode == "available":
                return "reachable"
            if mode == "contains" and options["expected"] in body.decode("utf-8", errors="replace"):
                return "content-match"
            if mode == "json" and matches(field_value(parse_response(body), options["field"]), options["expected"]):
                return "json-match"
            if mode == "sha256":
                remote = field_value(parse_response(body), options["field"]) if options.get("field") else hashlib.sha256(body).hexdigest()
                if remote == digest:
                    return "sha256-match"
        except PublishError:
            pass
        if attempt + 1 < options["attempts"]:
            time.sleep(options["interval"])
    raise PublishError("线上验证失败或仍未匹配预期内容", "verification", 4)


def validate_dest(source, dest):
    dest = Path(dest)
    if dest.is_dir() or dest.is_symlink():
        raise PublishError("本地目标不能是目录或符号链接")
    if dest.resolve() == Path(source).resolve():
        raise PublishError("本地目标不能与源文件相同")
    parent = dest.parent
    while not parent.exists():
        parent = parent.parent
    if not parent.is_dir() or not os.access(parent, os.W_OK):
        raise PublishError("本地目标的父路径不可写或不是目录")


def local_copy(data, source, dest):
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    mode = stat.S_IMODE(dest.stat().st_mode if dest.exists() else Path(source).stat().st_mode)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=dest.parent, prefix=".doc-publisher-", delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.chmod(mode)
        os.replace(temporary, dest)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def parser():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("file", help="UTF-8 Markdown 文件")
    cli.add_argument("-t", "--target")
    cli.add_argument("-u", "--url", help="覆盖 HTTP 地址，不继承原目标凭据或验证地址")
    cli.add_argument("-k", "--token", help="显式令牌；优先用环境变量避免命令历史泄露")
    cli.add_argument("-c", "--config")
    cli.add_argument("--verify", action="store_true")
    cli.add_argument("--verify-url")
    verify_modes = cli.add_mutually_exclusive_group()
    verify_modes.add_argument("--verify-content", help="GET 内容必须包含的字符串")
    verify_modes.add_argument("--verify-sha256", action="store_true", help="验证原始 Markdown 或配置的 JSON 哈希字段")
    cli.add_argument("--dry-run", action="store_true", help="校验并展示发布计划，不请求远端或写入目标")
    cli.add_argument("--strict", action="store_true", help="Markdown 警告也阻止发布")
    cli.add_argument("--skip-lint", action="store_true", help="明确跳过结构检查；仍检查 UTF-8 和空文件")
    cli.add_argument("--allow-http", action="store_true", help="明确允许非本地明文 HTTP")
    cli.add_argument("--json", action="store_true")
    return cli


def run(args, result):
    try:
        data = Path(args.file).read_bytes()
        text = data.decode("utf-8")
    except (OSError, UnicodeError) as exc:
        raise PublishError(f"无法读取 UTF-8 Markdown 文件 ({type(exc).__name__})") from exc
    if not text.strip():
        raise PublishError("文件内容为空")
    passed, errors, warnings = check_text(text)
    result["warnings"] = warnings if not args.skip_lint else []
    if not args.skip_lint and (not passed or (args.strict and warnings)):
        result["lint_errors"] = errors
        raise PublishError("Markdown 结构检查未通过" + (" (strict 将警告视为失败)" if args.strict and warnings else ""))
    config, config_path = find_config(args.config)
    if args.url and not args.target and config is not None and not config.get("default"):
        profile, name = None, None
    else:
        profile, name = resolve_target(config, args.target)
    profile = validate_profile(profile, args, config_path, os.environ)
    digest = hashlib.sha256(data).hexdigest()
    result.update(file=str(Path(args.file).resolve()), target=name, type=profile["type"], sha256=digest, bytes=len(data), lint="skipped" if args.skip_lint else "passed")
    if profile["type"] == "local-copy":
        validate_dest(args.file, profile["dest"])
        result.update(destination=profile["dest"], overwrite=Path(profile["dest"]).exists(), verification="sha256" if args.verify else "not-requested")
    else:
        result.update(endpoint=safe_url(profile["url"]), method=profile["method"], format=profile["format"], auth=profile["auth"], credential_present=bool(profile["token"] or profile["headers"]), verification=profile["verify"]["mode"] if args.verify else "not-requested", upload_retries=0)
        if args.verify:
            result["preview_url"] = safe_url(profile["verify"]["url"])
        if profile.get("build"):
            result["build_url"] = safe_url(profile["build"]["url"])
    if args.dry_run:
        result.update(ok=True, stage="dry-run", status="validated-plan")
        return
    result["stage"] = "upload"
    if profile["type"] == "local-copy":
        local_copy(data, args.file, profile["dest"])
        result["status"] = "copied"
        if args.verify:
            result["stage"] = "verification"
            if hashlib.sha256(Path(profile["dest"]).read_bytes()).hexdigest() != digest:
                raise PublishError("本地目标哈希不匹配", "verification", 4)
            result["verification"] = "sha256-match"
    else:
        code, elapsed, response = upload(data, args.file, profile)
        result.update(http_status=code, elapsed_seconds=elapsed, status="accepted" if code == 202 else "uploaded")
        if profile.get("build"):
            result["stage"] = "build"
            poll_build(profile, response)
            result["status"] = "build-completed"
        if args.verify:
            result["stage"] = "verification"
            result["verification"] = verify(profile, digest)
            # Accessibility alone must not upgrade 'accepted/uploaded' to 'published'.
    result.update(ok=True, stage="complete")


def main(argv=None):
    args = parser().parse_args(argv)
    result = {"ok": False, "stage": "validation"}
    code = 0
    try:
        run(args, result)
    except PublishError as exc:
        result.update(ok=False, stage=exc.stage, error=str(exc))
        code = {"validation": 1, "upload": 2, "build": 3, "verification": 4}.get(exc.stage, exc.code)
    except (OSError, ValueError, TypeError) as exc:
        result.update(ok=False, error=f"操作失败 ({type(exc).__name__})")
        code = {"upload": 2, "verification": 4}.get(result["stage"], 1)
    if args.json:
        print(json.dumps(result, ensure_ascii=False))
    else:
        print("✓ 操作完成" if result["ok"] else "✖ 操作失败")
        for key, value in result.items():
            if key != "ok":
                print(f"  {key}: {value}")
        if result.get("verification") == "reachable":
            print("  页面可访问；尚未验证新版本内容。")
        if result.get("status") in ("accepted", "uploaded"):
            print("  上传已被接受；未配置或未确认构建完成。")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
