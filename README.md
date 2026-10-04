# doc-publisher

> 将单个 Markdown 文件交付到自建 HTTP 接口或本地文件路径的 Agent Skill。

纯 Python 标准库实现，支持 Python 3.9+。客户端负责校验、上传和验证；文档构建、渲染、托管由目标服务负责。它不包含文件监听、多文件同步或现成的文档平台适配器。

## 功能

- 发布前自动检查 UTF-8、空文档、标题结构、代码围栏和基础表格格式。
- 命名目标配置；错误配置和未知目标立即失败，不静默回退。
- HTTP 支持 POST / PUT / PATCH，原始 Markdown / JSON / multipart 请求体。
- 请求头、Bearer 或明确配置的查询参数认证；默认不会将 Token 写入 URL。
- 本地目标使用临时文件与原子替换；相对路径基于配置文件所在目录。
- 可选构建任务轮询，以及页面可访问性、内容、JSON 字段或 SHA-256 验证。
- dry-run 展示实际目标、文件摘要、覆盖动作和验证方式；`--json` 适合 Agent 和 CI。

## 安装

将仓库克隆到你的 Agent 支持的 skills 目录，例如项目级安装：

```bash
mkdir -p .agents/skills
git clone https://github.com/RealWR1D/doc-publisher.git .agents/skills/doc-publisher
```

不同 Agent 的全局 skills 路径与发现机制不同，按对应工具的约定安装。运行脚本时使用技能目录的实际路径，避免依赖当前工作目录：

```bash
python3 /absolute/path/to/doc-publisher/scripts/publish.py ./guide.md --dry-run
```

## 配置

复制 [targets.example.json](targets.example.json) 为私有配置，设置实际端点，并通过环境变量提供 Token：

```bash
cp targets.example.json targets.json
export DOC_PUBLISHER_TOKEN='replace-with-your-token'
```

建议将私有配置放在 `~/.config/doc-publisher/targets.json`，避免进入项目仓库。若放在其他项目中，需要在那个项目的 `.gitignore` 中添加忽略规则；本 skill 的忽略规则不会保护其他目录。环境变量也需要由调用脚本的进程继承。不要把真实 Token 写入示例、命令历史或提交。

配置查找顺序：

1. 显式 `--config`（只读取该文件，失败即停止）。
2. 当前目录 `.doc-publisher.json`，然后 `targets.json`。
3. Skill 根目录 `targets.json`。
4. `~/.config/doc-publisher/targets.json`。
5. 没有配置文件时，使用 `DOC_PUBLISHER_URL` 与 `DOC_PUBLISHER_TOKEN`。

发现的首个配置如果损坏，不会继续寻找下一个。指定 `--target` 必须命中已有目标。配置中的默认目标必须存在。目标已选定时，Token 优先级为 `--token`、`token_env` 指向的变量、配置 `token`、`DOC_PUBLISHER_TOKEN`；指定但未设置的 `token_env` 会报错。

完整字段、响应约定及异步构建示例见 [配置与接口约定](references/configuration.md)。

## 使用

```bash
# 独立检查；strict 将警告也视为失败
python3 scripts/lint.py guide.md --strict

# 预演：校验 Markdown 和配置，无网络请求、无目标文件写入
python3 scripts/publish.py guide.md --target example-docs --dry-run --json

# 上传；使用配置中的验证 URL，默认只检查页面可访问
python3 scripts/publish.py guide.md --target example-docs --verify

# 配置服务提供原始 Markdown URL 时，验证内容与本次上传完全一致
python3 scripts/publish.py guide.md --target example-docs --verify --verify-sha256

# 本地原子复制，并比较源/目标 SHA-256
python3 scripts/publish.py guide.md --target local-preview --verify

# 无配置文件时也可以直接指定地址；凭据使用环境变量
python3 scripts/publish.py guide.md --url https://api.example.com/upload/doc --dry-run
```

`--url` 指向不同地址时，不继承原目标的 Token、私有请求头、认证方式、构建 URL 和验证 URL。显式 `--token` 或 `DOC_PUBLISHER_TOKEN` 仍可提供新地址的凭据；验证地址需要重新指定。若已选择 `local-copy`，混用 HTTP 参数会报错。

远端默认要求 HTTPS；本机 loopback HTTP 可直接使用，其他明文地址需要明确 `--allow-http`。客户端不会自动跟随重定向，避免重复上传或转发凭据；请配置最终端点。上传不会自动重试，避免重复副作用。只有配置的构建/验证 GET 会按次数轮询。

默认 lint 阻止结构错误；语言标注缺失等警告不阻止发布，可用 `--strict` 收紧。只有明确 `--skip-lint` 才跳过结构检查，空文件和 UTF-8 检查仍然执行。

## 如何判断结果

HTTP 2xx 只表示接口接受请求。`success: false`、`ok: false`、非空 `error` 或 `status: failed/error` 会判为业务失败；其他响应格式应配置显式成功字段。客户端不直接输出服务端响应体，以免泄露 Token 或文档内容。

| 结果字段 | 含义 |
| --- | --- |
| `status: uploaded` | 上传返回 2xx，尚未确认构建完成 |
| `status: accepted` | 上传返回 202，尚未确认异步任务完成 |
| `status: build-completed` | 配置的构建状态接口报告完成 |
| `status: copied` | 已完成本地原子复制 |
| `verification: reachable` | 页面可访问，不能证明新版本上线 |
| `verification: content-match/json-match` | 页面内容或 JSON 字段匹配配置的预期值 |
| `verification: sha256-match` | 远端原始 Markdown 或哈希字段与本次文件一致 |

`--verify` 缺少验证 URL 会在上传前失败；本地模式直接验证目标文件。验证失败会返回非零状态。预览 GET 不携带上传 Token 或私有请求头；需要认证的预览服务目前应提供独立可访问的验证端点。

| 退出码 | 含义 |
| --- | --- |
| 0 | 请求的操作完成，具体证据见 `status` 和 `verification` |
| 1 | 文件、Markdown 或配置检查失败 |
| 2 | 上传/本地复制失败（argparse 参数用法错误也使用 2） |
| 3 | 构建轮询失败或超出次数 |
| 4 | 发布后的验证失败或超出次数 |

## Markdown 范围

检查器是轻量结构检查，不是完整 CommonMark/GFM 解析器，也不会做渲染截图检查。正文首个内容应为唯一 H1，可以有 YAML frontmatter；标题不能向下跳级。它识别三反引号和波浪号围栏、围栏长度，并排除围栏内或缩进代码中的标题。表格检查分隔行和列数，处理转义管道与行内代码中的管道。

复杂的列表/引用嵌套、Setext 标题、链接有效性及渲染平台特有语法不在完整支持范围内；确有需要时使用更完整的 Markdown 工具或明确跳过本检查。H2 是否生成目录、提示块是否成为卡片取决于服务端模板。

## 测试与迁移

```bash
python3 -m unittest discover -s tests -v
python3 scripts/lint.py README.md --strict
```

测试覆盖配置与凭据边界、检查器缺陷、失败退出码、异步构建、内容验证、本地原子复制及本机 HTTP 上传/重定向。GitHub Actions 在 Linux、macOS、Windows 上运行测试。

旧配置的 `url`、`token`、`verify_url` 和两种目标类型仍可使用，但有以下行为变化：

- 默认不再向 URL 自动追加 Token；必须使用旧服务的查询参数认证时，明确配置 `auth: query`。
- 相对 `dest` 改为相对配置文件目录，请检查现有本地目标。
- 空文档、多个 H1、标题跳级、无效配置及验证失败会阻止操作或返回非零退出码。
- dry-run 会执行真实的文件/配置检查，但不代表远端权限、接口或构建已验证。

## License

MIT，见 [LICENSE](LICENSE)。
