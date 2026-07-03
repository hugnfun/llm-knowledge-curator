# LLKC Plugin 启用指南

## ⚠️ 首次启用必做(在 Obsidian GUI 里)

LLKC 插件已经部署到 `LLM知识库/.obsidian/plugins/llkc/`,但**默认未启用**。开启步骤:

### 1. 在 Obsidian 里打开新 vault

`Cmd + O` → 选 `~/Library/Mobile Documents/iCloud~md~obsidian/Documents/LLM知识库/`

(不是 Person Vault,是新独立 vault。)

### 2. 启用 Community plugins

1. 左下角 **⚙ Settings** → **Community plugins**
2. 右上角 **关闭 Restricted mode** (如果开着)
3. 列表里找 **"LLM Knowledge Curator"** 插件
4. 点旁边的开关 → **打开**

### 3. 配置插件(可选)

Settings → Community plugins → LLKC → ⚙ 图标:
- MCP server path: 默认值应该已经对(`mcp_server.py` 路径)
- Python: 默认 `/usr/local/bin/python3` 一般也对
- Default model: 默认 `deepseek-v4-pro`

### 4. 验证

三种方式之一:
- 左侧 ribbon 栏 (顶栏) 有 **⚡ 图标** → 点击打开右侧面板
- 命令面板 `Cmd+P` → 输入 `LLKC:` → 看到 6 个命令
- 右侧边栏自动多出 **LLKC 工作台** 选项卡

## 6 个命令

| 命令 | 作用 |
|---|---|
| `LLKC: New Daily Thinking` | 生成今日 Daily Thinking md |
| `LLKC: Run Writer` | 调 writer 生成 4 角度 draft |
| `LLKC: Run Parser (增量)` | 手动跑判别器(cron 失败时备用) |
| `LLKC: Show Stats` | vault 状态统计 |
| `LLKC: Health Check` | 自检(路径/脚本/API key) |
| `LLKC: Open Dashboard` | 打开右侧面板 |

## 故障排查

- **"MCP 启动失败"**:检查 Python 路径 + mcp_server.py 是否存在
- **"无响应"**:查看 Obsidian console(`Cmd+Option+I` → Console) 看 stderr
- **想换模型**:Settings → LLKC → Default model
- **想看 raw 通信**:LLKC 面板点 "🩺 健康自检" 能看到路径/脚本状态

## 技术架构(供参考)

```
┌─────────────────────┐
│  Obsidian 插件      │
│  (TS, right panel)  │
└──────────┬──────────┘
           │ stdio (JSON-RPC 2.0)
           ▼
┌─────────────────────┐
│  mcp_server.py      │
│  (zero-dep Python)  │
└──────────┬──────────┘
           │ subprocess
           ▼
┌─────────────────────┐
│  Python 脚本层      │
│  daily_thinking     │
│  writer_agent       │
│  parser_runner      │
│  build_index        │
│  write_back         │
└─────────────────────┘
```

**8 个 MCP tool**:
- `daily_thinking` — 生成 Daily Thinking
- `write_drafts` — 4 角度 draft
- `run_parser` — 判别器增量
- `list_seeds` — 列 seed(过滤)
- `get_stats` — 统计
- `get_health` — 自检
- `search_inbox` — 全文搜 inbox
- `read_seed` — 读单条 seed

**复用性**:MCP server 不只 Obsidian 能用,Claude Code / Hermes Agent 也能以同样协议调。

## 开发迭代

改 TS 后重新构建 + 复制:
```bash
cd ~/Documents/Project/llm-knowledge-curator/obsidian-plugin
npx tsc
cp src/main.js src/mcp_client.js src/styles.css \
  "/Users/aicer/Library/Mobile Documents/iCloud~md~obsidian/Documents/LLM知识库/.obsidian/plugins/llkc/"
```

Obsidian 会自动热重载插件(开发模式下)。
