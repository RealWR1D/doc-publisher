# 配置与接口约定

## 基本配置

```json
{
  "default": "docs",
  "targets": {
    "docs": {
      "type": "http-rest",
      "url": "https://docs.example.com/api/upload/doc",
      "token_env": "DOC_PUBLISHER_TOKEN",
      "verify_url": "https://docs.example.com/guide"
    },
    "local": {
      "type": "local-copy",
      "dest": "./dist/guide.md"
    }
  }
}
```

多个目标是可选配置，不会同时发布。相对 `dest` 以配置文件目录为基准；本地复制不能覆盖源文件、目录或符号链接。原子替换保留已有目标的权限位，目标不存在时使用源文件权限位。

## HTTP 字段

| 字段 | 默认值/含义 |
| --- | --- |
| `type` | `http-rest`；也可为 `local-copy` |
| `url` | 上传最终地址，默认要求 HTTPS |
| `token_env` | 从命名环境变量获取 Token；未设置会报错 |
| `token` | 可兼容旧配置，优先使用环境变量避免明文凭据落盘 |
| `auth` | `header`、`bearer`、`query`、`none`；默认 `header` |
| `token_header` | `X-Upload-Token`；header 模式使用 |
| `token_param` | `token`；仅明确选用 query 模式时使用 |
| `headers` | 额外请求头对象；不能覆盖 Host、Content-Type、Content-Length |
| `method` | `POST`、`PUT`、`PATCH`；默认 `POST` |
| `format` | `raw`、`json`、`multipart`；默认 `raw` |
| `content_field` | JSON Markdown 字段名，默认 `content`；不能为 `filename` |
| `file_field` | multipart 文件字段名，默认 `file` |
| `timeout` | 单次网络操作超时秒数，0.1–120，默认 30；不是整个发布的总超时 |
| `allow_http` | 默认 false；对非本机 HTTP 的显式例外 |
| `response` | 可选上传响应的显式成功条件 |
| `build` | 可选构建状态轮询配置 |
| `verify` | 可选发布后 GET 验证配置，只有 `--verify` 时执行 |
| `verify_url` | 兼容旧配置；优先使用 `verify.url` |

`query` 认证会让 Token 进入服务端/代理 URL 日志，应只用于无法修改的旧接口。输出中会隐藏 URL 查询参数值；服务端响应体不输出。配置文件自身仍需妥善保存，不提供加密密钥库功能。

`raw` 发送 UTF-8 Markdown 字节，Content-Type 为 `text/markdown; charset=utf-8`。JSON 请求包含 Markdown 字段和 `filename`：

```json
{"content": "# Title\n## Section\nBody\n", "filename": "guide.md"}
```

multipart 发送一个文件字段，生成随机 boundary。文件名在 multipart 头中替换为安全 ASCII，正文内容保持原字节。这里的适配能力针对自建接口；未实现特定 SaaS 平台的完整 API 流程。

## 上传响应成功条件

只有 2xx 会被当作传输成功，202 单独报告 `accepted`。常见 JSON 业务失败字段会阻止后续步骤；对于其他响应协议，明确配置成功字段，例如：

```json
{
  "response": {
    "success_field": "result.accepted",
    "success_value": true
  }
}
```

字段支持点分隔的对象路径，字段缺失不会匹配 JSON null。它验证上传响应，不能单独证明站点构建完成。非 JSON 响应在没有成功条件时仅报告上传被接受。

## 异步构建

```json
{
  "build": {
    "url": "https://docs.example.com/api/build/status",
    "job_field": "job.id",
    "job_param": "job_id",
    "field": "status",
    "success_values": ["published", "completed"],
    "failure_values": ["failed", "error"],
    "attempts": 5,
    "interval": 1
  }
}
```

上传响应应包含 `job.id`。客户端将该值作为 `job_id` 查询参数发送给固定的状态接口；不会直接访问服务端响应提供的任意 URL。状态接口必须与上传地址同源，可使用同一认证。缺少任务 ID、HTTP 失败、业务失败或次数耗尽都会返回退出码 3。

若不配置 `job_field`，使用固定状态 URL；此时服务端必须保证该地址描述本次上传，避免把旧任务完成状态当成本次结果。对于并发发布，建议始终使用任务 ID。

`attempts` 为 1–30，`interval` 为 0–10 秒，均为有界轮询。不自动重试上传；构建 HTTP 错误直接失败，仅未完成状态继续轮询。

## 发布后验证

配置 `verify.url` 或兼容字段 `verify_url`，并传入 `--verify`。验证 GET 不发送上传认证；重定向不会自动跟随。默认最多尝试三次，间隔一秒；`attempts` 可为 1–30，`interval` 可为 0–10 秒。

### 页面可访问

```json
{"verify": {"url": "https://docs.example.com/guide", "mode": "available"}}
```

只检查 GET 返回 2xx，报告 `reachable`。即使页面是旧内容，也可能通过；不可把这项结果描述为新版本上线。

### 内容标识

```json
{"verify": {"url": "https://docs.example.com/guide", "mode": "contains", "expected": "release-2026-10-05"}}
```

用于检查本次版本的唯一标识。也可使用 `--verify-content` 覆盖预期字符串。选择旧页面已经包含的字符串无法验证更新。

### JSON 版本

```json
{"verify": {"url": "https://docs.example.com/api/version", "mode": "json", "field": "document.version", "expected": "v2"}}
```

用于 JSON 元数据端点，点分隔字段需匹配预期版本。客户端不会自动从任意上传响应推断版本。

### 精确内容哈希

```json
{"verify": {"url": "https://docs.example.com/raw/guide.md", "mode": "sha256"}}
```

计算 GET 原始字节的 SHA-256，与本次上传文件比较；不能用于渲染后的 HTML。也可用返回 JSON 哈希的端点：

```json
{"verify": {"url": "https://docs.example.com/api/document", "mode": "sha256", "field": "document.sha256"}}
```

服务端必须返回对应文档的原始 Markdown SHA-256。内容不匹配或 URL 请求失败会继续有界验证，最终失败返回退出码 4。

## Agent/CI 结果

`--json` 输出一个 JSON 对象，无前置进度文本。成功操作的 `ok` 为 true；在 HTTP 模式下，请进一步读取 `status`、`verification` 判断证据，不要只检查退出码。

```json
{
  "ok": true,
  "stage": "complete",
  "status": "uploaded",
  "http_status": 200,
  "verification": "sha256-match"
}
```

失败结果包含 `stage` 与脱敏错误概要，保留已完成上传的状态。上传后验证失败时不要盲目重新上传；先检查构建、缓存和目标内容。单个网络响应最多读取 2 MiB；更大文档的远端验证建议使用 JSON 哈希端点。
