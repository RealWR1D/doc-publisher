# 📚 doc-publisher (Antigravity Agent Skill)

> **Universal Documentation Publisher & Hot-Sync Agent Skill**  
> 一键打通 AI Agent 编写文档到多端文档站热发布的最后一公里。零三方依赖，纯标准库构建。

---

## ✨ Features

- 🚀 **一键热更新发布**：支持通过标准 REST API 将 Markdown 秒级推送并实时编译上线。
- 🔍 **结构语义检查器**：内置 `lint.py`，自动检测 H1/H2 层次结构、未闭合代码块、缺少语言标注等常见缺陷。
- 🛡️ **严格安全脱敏**：彻底隔离配置与代码，杜绝私有 API URL 与 Token 意外提交到公共 Git 仓库。
- 🧩 **多端适配架构**：支持多目标环境切换（生产、预发、本地预览），纯 Python 标准库驱动，无需 `pip install` 任何依赖。
- 🤖 **全 Agent 生态兼容**：完美适配 Google Antigravity、Claude Code、Cursor、Gemini CLI 等支持 Agentic Skills 的编码工具。

---

## 📦 Installation

### 安装为 Antigravity 全局技能（推荐）
```bash
git clone https://github.com/<your-username>/doc-publisher.git ~/.gemini/config/skills/doc-publisher
```

### 安装为单个项目的本地技能
在目标项目根目录下克隆：
```bash
mkdir -p .agents/skills
git clone https://github.com/<your-username>/doc-publisher.git .agents/skills/doc-publisher
```

---

## ⚙️ Configuration (配置目标环境)

复制示例配置文件为私有配置（已被 `.gitignore` 自动忽略，绝不入库）：

```bash
cp targets.example.json targets.json
```

在 `targets.json` 中配置你的文档发布目标：

```json
{
  "default": "my-docs",
  "targets": {
    "my-docs": {
      "type": "http-rest",
      "url": "https://your-docs-site.com/api/upload/doc",
      "token": "your-secret-token",
      "verify_url": "https://your-docs-site.com/guide",
      "description": "线上文档主站"
    },
    "local-preview": {
      "type": "local-copy",
      "dest": "./dist/guide.md",
      "description": "本地静态预览"
    }
  }
}
```

> **配置查找顺序**：
> 1. 当前工作目录下的 `./.doc-publisher.json` 或 `./targets.json`；
> 2. Skill 目录下的 `targets.json`；
> 3. 用户主目录 `~/.config/doc-publisher/targets.json`；
> 4. 环境变量 `DOC_PUBLISHER_URL` / `DOC_PUBLISHER_TOKEN`。

---

## 🚀 Usage

### 1. 结构语法自检
```bash
python3 scripts/lint.py path/to/document.md
```

### 2. 发布推送
```bash
# 使用 targets.json 中配置的命名目标
python3 scripts/publish.py --target my-docs path/to/document.md --verify

# 或无需配置文件，直接通过命令行传参推送
python3 scripts/publish.py --url "https://api.example.com/upload/doc" --token "secret" path/to/document.md --verify

# 模拟演练 (Dry-run)
python3 scripts/publish.py --target my-docs path/to/document.md --dry-run
```

---

## 📝 Markdown 规范要求

为保证目标文档站的最佳渲染效果，推荐遵循以下排版规范：
- `# 主标题`：文档顶部唯一的单个 H1；
- `> 副标题/元信息`：紧随 H1 后的一行或多行引用块；
- `## 章节卡片`：作为独立章节，自动提取为目录索引（TOC）；
- `### 小节`：章节内次级层次；
- ````lang`：代码块必须标注语言标识；
- `> [!TIP]` / `> [!WARNING]`：语义化提示卡片。

---

## 📄 License
MIT License
