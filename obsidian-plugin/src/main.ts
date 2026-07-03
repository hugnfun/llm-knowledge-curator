/**
 * main.ts — LLKC Obsidian Plugin 入口
 *
 * 右侧面板 + 6 个命令:
 *   - LLKC: New Daily Thinking
 *   - LLKC: Run Writer (生成 4 角度 draft)
 *   - LLKC: Run Parser (增量判别)
 *   - LLKC: Show Stats
 *   - LLKC: Health Check
 *   - LLKC: Open Dashboard
 */
import { App, Plugin, PluginSettingTab, Setting, Notice, ItemView, WorkspaceLeaf } from "obsidian";
import * as path from "path";
import * as fs from "fs";
import { spawn, ChildProcess } from "child_process";

/* === McpClient 合并自 mcp_client.ts ===
 * Obsidian 用 app:// 协议加载插件,跨文件 require 走不通(./ 相对路径会变 app://./)
 * 所以把 130 行 stdio 客户端合并到 main.ts,单文件,无跨文件依赖。
 */
interface McpContentItem { type: "text"; text: string; }
interface McpToolResult { isError?: boolean; content: McpContentItem[]; }
interface McpToolSpec { name: string; description: string; inputSchema: any; }
interface PendingRequest { resolve: (v: any) => void; reject: (e: Error) => void; }

class McpClient {
  private proc: ChildProcess | null = null;
  private pending: Map<number, PendingRequest> = new Map();
  private nextId = 1;
  private buf = "";
  private serverPath: string;
  private python: string;

  constructor(serverPath: string, python = "python3") {
    this.serverPath = serverPath;
    this.python = python;
  }

  async start(): Promise<void> {
    if (this.proc) return;
    this.proc = spawn(this.python, [this.serverPath], { stdio: ["pipe", "pipe", "pipe"] });
    this.proc.stdout?.on("data", (chunk) => this._onStdout(chunk.toString()));
    this.proc.stderr?.on("data", () => { /* ignore */ });
    this.proc.on("exit", (code) => {
      for (const p of this.pending.values()) {
        p.reject(new Error(`mcp server exited (code=${code})`));
      }
      this.pending.clear();
      this.proc = null;
    });
    await this._request("initialize", {
      protocolVersion: "2024-11-05",
      capabilities: {},
      clientInfo: { name: "obsidian-llkc", version: "0.1.0" },
    });
    this.proc.stdin?.write(
      JSON.stringify({ jsonrpc: "2.0", method: "notifications/initialized", params: {} }) + "\n"
    );
  }

  async stop(): Promise<void> {
    if (!this.proc) return;
    this.proc.stdin?.end();
    this.proc.kill();
    this.proc = null;
  }

  async listTools(): Promise<McpToolSpec[]> {
    const r = await this._request("tools/list", {});
    return r?.tools ?? [];
  }

  async callTool(name: string, args: Record<string, any> = {}): Promise<McpToolResult> {
    return await this._request("tools/call", { name, arguments: args });
  }

  private _onStdout(chunk: string): void {
    this.buf += chunk;
    let idx;
    while ((idx = this.buf.indexOf("\n")) >= 0) {
      const line = this.buf.slice(0, idx).trim();
      this.buf = this.buf.slice(idx + 1);
      if (!line) continue;
      try {
        const msg = JSON.parse(line);
        if (msg.id != null && this.pending.has(msg.id)) {
          const p = this.pending.get(msg.id)!;
          this.pending.delete(msg.id);
          if (msg.error) p.reject(new Error(msg.error.message ?? JSON.stringify(msg.error)));
          else p.resolve(msg.result);
        }
      } catch { /* ignore non-JSON */ }
    }
  }

  private _request(method: string, params: any): Promise<any> {
    if (!this.proc) return Promise.reject(new Error("mcp client not started"));
    const id = this.nextId++;
    const msg = { jsonrpc: "2.0", id, method, params };
    return new Promise((resolve, reject) => {
      this.pending.set(id, { resolve, reject });
      this.proc!.stdin?.write(JSON.stringify(msg) + "\n");
    });
  }
}

const VIEW_TYPE_LLKC = "llkc-panel-view";

interface LlkcSettings {
  mcpServerPath: string;
  python: string;
  defaultModel: string;
}

const DEFAULT_SETTINGS: LlkcSettings = {
  mcpServerPath: "/Users/aicer/Documents/Project/llm-knowledge-curator/scripts/mcp_server.py",
  python: "/usr/local/bin/python3",
  defaultModel: "deepseek-v4-pro",
};

export default class LlkcPlugin extends Plugin {
  settings: LlkcSettings = DEFAULT_SETTINGS;
  mcp: McpClient | null = null;  // 在 onload 里 new
  private mcpReady = false;

  async onload() {
    await this.loadSettings();

    this.mcp = new McpClient(this.settings.mcpServerPath, this.settings.python);

    // 注入 CSS
    try {
      const pluginDir = (this.manifest as any).dir ?? "";
      const cssPath = path.join(pluginDir, "styles.css");
      if (fs.existsSync(cssPath)) {
        const css = fs.readFileSync(cssPath, "utf8");
        document.head.createEl("style", { text: css, attr: { id: "llkc-styles" } });
      }
    } catch (e) {
      console.error("LLKC: CSS 注入失败", e);
    }

    // 注册右侧面板
    this.registerView(VIEW_TYPE_LLKC, (leaf) => new LlkcPanelView(leaf, this));

    // 注册 ribbon 图标(顶栏)
    this.addRibbonIcon("zap", "LLKC: Open Dashboard", () => this.activatePanelView());

    // 注册命令
    this.addCommand({
      id: "new-daily",
      name: "LLKC: New Daily Thinking",
      callback: () => this.cmdNewDaily(),
    });
    this.addCommand({
      id: "run-writer",
      name: "LLKC: Run Writer",
      callback: () => this.cmdRunWriter(),
    });
    this.addCommand({
      id: "run-parser",
      name: "LLKC: Run Parser (增量)",
      callback: () => this.cmdRunParser(),
    });
    this.addCommand({
      id: "show-stats",
      name: "LLKC: Show Stats",
      callback: () => this.cmdShowStats(),
    });
    this.addCommand({
      id: "health-check",
      name: "LLKC: Health Check",
      callback: () => this.cmdHealth(),
    });
    this.addCommand({
      id: "open-dashboard",
      name: "LLKC: Open Dashboard",
      callback: () => this.activatePanelView(),
    });

    // Settings tab
    this.addSettingTab(new LlkcSettingTab(this.app, this));
  }

  async onunload() {
    // 清理注入的 CSS
    document.getElementById("llkc-styles")?.remove();
    await this.mcp!.stop();
  }

  async loadSettings() {
    this.settings = Object.assign({}, DEFAULT_SETTINGS, await this.loadData());
  }

  async saveSettings() {
    await this.saveData(this.settings);
  }

  async ensureMcp(): Promise<boolean> {
    if (this.mcpReady) return true;
    try {
      await this.mcp!.start();
      this.mcpReady = true;
      return true;
    } catch (e) {
      new Notice(`LLKC: MCP 启动失败 — ${(e as Error).message}`);
      return false;
    }
  }

  async activatePanelView() {
    const { workspace } = this.app;
    let leaf = workspace.getLeavesOfType(VIEW_TYPE_LLKC)[0];
    if (!leaf) {
      const rightLeaf = workspace.getRightLeaf(false);
      if (!rightLeaf) {
        new Notice("LLKC: 找不到右侧面板");
        return;
      }
      await rightLeaf.setViewState({ type: VIEW_TYPE_LLKC, active: true });
      leaf = rightLeaf;
    }
    workspace.revealLeaf(leaf);
  }

  async cmdNewDaily() {
    if (!(await this.ensureMcp())) return;
    new Notice("LLKC: 生成今日 Daily Thinking…");
    const r = await this.mcp!.callTool("daily_thinking", {});
    if (r.isError) {
      new Notice(`LLKC: ✗ ${r.content[0]?.text?.slice(0, 200)}`);
      return;
    }
    new Notice(`LLKC: ✓ Daily Thinking 已生成`);
    // 重新激活面板以显示最新内容
    this.activatePanelView();
  }

  async cmdRunWriter() {
    if (!(await this.ensureMcp())) return;
    new Notice("LLKC: 调 writer 生成 4 角度 draft…(可能要 1-2 分钟)");
    const r = await this.mcp!.callTool("write_drafts", { model: this.settings.defaultModel });
    if (r.isError) {
      new Notice(`LLKC: ✗ ${r.content[0]?.text?.slice(0, 200)}`);
      return;
    }
    new Notice(`LLKC: ✓ Writer 完成,看右侧面板`);
    this.activatePanelView();
  }

  async cmdRunParser() {
    if (!(await this.ensureMcp())) return;
    new Notice("LLKC: 跑 parser 增量…(可能要 1-2 分钟)");
    const r = await this.mcp!.callTool("run_parser", {});
    if (r.isError) {
      new Notice(`LLKC: ✗ ${r.content[0]?.text?.slice(0, 200)}`);
      return;
    }
    new Notice(`LLKC: ✓ Parser 完成`);
    this.activatePanelView();
  }

  async cmdShowStats() {
    if (!(await this.ensureMcp())) return;
    const r = await this.mcp!.callTool("get_stats", {});
    new Notice(r.content[0]?.text?.split("\n").slice(0, 5).join(" | ") ?? "无");
    this.activatePanelView();
  }

  async cmdHealth() {
    if (!(await this.ensureMcp())) return;
    const r = await this.mcp!.callTool("get_health", {});
    new Notice(r.content[0]?.text?.slice(0, 300) ?? "无");
    this.activatePanelView();
  }
}

// ============== 右侧面板 ==============
class LlkcPanelView extends ItemView {
  private plugin: LlkcPlugin;
  private refreshTimer: number | null = null;

  constructor(leaf: WorkspaceLeaf, plugin: LlkcPlugin) {
    super(leaf);
    this.plugin = plugin;
  }

  getViewType() { return VIEW_TYPE_LLKC; }
  getDisplayText() { return "LLKC 工作台"; }
  getIcon() { return "zap"; }

  async onOpen() {
    const container = this.containerEl.children[1] as HTMLElement;
    container.empty();
    container.addClass("llkc-panel");
    container.createEl("h2", { text: "⚡ LLM 知识库工作台" });
    container.createEl("p", { text: "本地 MCP 驱动的判别器 + Writer 控制台",
                              cls: "llkc-loading" });

    await this.refresh();

    // 每 60s 自动 refresh stats
    this.refreshTimer = window.setInterval(() => this.refresh(), 60000);
  }

  async onClose() {
    if (this.refreshTimer) {
      window.clearInterval(this.refreshTimer);
      this.refreshTimer = null;
    }
  }

  async refresh() {
    const container = this.containerEl.children[1] as HTMLElement;
    container.empty();
    container.addClass("llkc-panel");
    container.createEl("h2", { text: "⚡ LLM 知识库工作台" });

    const ok = await this.plugin.ensureMcp();
    if (!ok) {
      container.createEl("div", {
        cls: "llkc-status error",
        text: "✗ MCP server 未启动。检查设置或重试。",
      });
      return;
    }

    // === 今日进度 ===
    const today = new Date().toISOString().slice(0, 10);
    const todaySection = container.createDiv({ cls: "llkc-section" });
    todaySection.createEl("h3", { text: `今日 ${today}` });
    const todayRow = todaySection.createDiv({ cls: "llkc-row" });
    todayRow.createSpan({ cls: "llkc-label", text: "Daily Thinking" });
    const todayStatus = todayRow.createSpan({ cls: "llkc-value" });
    todayStatus.setText("加载中…");

    const writeBtn = todaySection.createEl("button", { cls: "llkc-button" });
    writeBtn.setText("▶ 生成今日 Daily Thinking");
    writeBtn.onclick = async () => {
      writeBtn.disabled = true;
      writeBtn.setText("生成中…");
      await this.plugin.cmdNewDaily();
      writeBtn.disabled = false;
      writeBtn.setText("▶ 生成今日 Daily Thinking");
      this.refresh();
    };

    const writerBtn = todaySection.createEl("button", { cls: "llkc-button" });
    writerBtn.setText(`▶ 跑 Writer (${this.plugin.settings.defaultModel})`);
    writerBtn.onclick = async () => {
      writerBtn.disabled = true;
      writerBtn.setText("Writer 跑中…(1-2 分钟)");
      await this.plugin.cmdRunWriter();
      writerBtn.disabled = false;
      writerBtn.setText(`▶ 跑 Writer (${this.plugin.settings.defaultModel})`);
      this.refresh();
    };

    // === 触发 cron 类操作 ===
    const opsSection = container.createDiv({ cls: "llkc-section" });
    opsSection.createEl("h3", { text: "运维操作" });
    const parserBtn = opsSection.createEl("button", { cls: "llkc-button llkc-button-secondary" });
    parserBtn.setText("▶ 跑 Parser 增量(扫 inbox + 判别)");
    parserBtn.onclick = async () => {
      parserBtn.disabled = true;
      parserBtn.setText("Parser 跑中…");
      await this.plugin.cmdRunParser();
      parserBtn.disabled = false;
      parserBtn.setText("▶ 跑 Parser 增量(扫 inbox + 判别)");
      this.refresh();
    };

    const healthBtn = opsSection.createEl("button", { cls: "llkc-button llkc-button-secondary" });
    healthBtn.setText("🩺 健康自检");
    healthBtn.onclick = async () => {
      healthBtn.disabled = true;
      const r = await this.plugin.mcp!.callTool("get_health", {});
      healthBtn.disabled = false;
      // 在面板下方插入
      const statusDiv = container.createDiv({ cls: "llkc-status info" });
      statusDiv.createEl("pre", { cls: "llkc-pre", text: r.content[0]?.text ?? "" });
    };

    // === 统计 ===
    const statsSection = container.createDiv({ cls: "llkc-section" });
    statsSection.createEl("h3", { text: "📊 知识库统计" });
    try {
      const r = await this.plugin.mcp!.callTool("get_stats", {});
      const pre = statsSection.createEl("pre", { cls: "llkc-pre" });
      pre.setText(r.content[0]?.text ?? "无数据");
    } catch (e) {
      statsSection.createEl("div", { cls: "llkc-status error", text: (e as Error).message });
    }

    // === 最近 seed (high priority) ===
    const seedSection = container.createDiv({ cls: "llkc-section" });
    seedSection.createEl("h3", { text: "🔥 priority:high seed (待翻牌)" });
    try {
      const r = await this.plugin.mcp!.callTool("list_seeds", { priority: "high", limit: 10 });
      const text = r.content[0]?.text ?? "";
      if (text.includes("共 0")) {
        seedSection.createEl("div", {
          cls: "llkc-status warn",
          text: "暂无 high priority seed。",
        });
      } else {
        seedSection.createEl("pre", { cls: "llkc-pre", text });
      }
    } catch (e) {
      seedSection.createEl("div", { cls: "llkc-status error", text: (e as Error).message });
    }
  }
}

// ============== Settings tab ==============
class LlkcSettingTab extends PluginSettingTab {
  plugin: LlkcPlugin;
  constructor(app: App, plugin: LlkcPlugin) {
    super(app, plugin);
    this.plugin = plugin;
  }
  display(): void {
    const { containerEl } = this;
    containerEl.empty();
    containerEl.createEl("h2", { text: "LLKC 设置" });

    new Setting(containerEl)
      .setName("MCP server 路径")
      .setDesc("mcp_server.py 绝对路径")
      .addText(t => t
        .setValue(this.plugin.settings.mcpServerPath)
        .onChange(async (v) => {
          this.plugin.settings.mcpServerPath = v;
          await this.plugin.saveSettings();
        }));

    new Setting(containerEl)
      .setName("Python 解释器")
      .setDesc("绝对路径(/usr/local/bin/python3 或 venv)")
      .addText(t => t
        .setValue(this.plugin.settings.python)
        .onChange(async (v) => {
          this.plugin.settings.python = v;
          await this.plugin.saveSettings();
        }));

    new Setting(containerEl)
      .setName("默认模型")
      .setDesc("Writer 用的模型(ark-code-latest / deepseek-v4-pro)")
      .addText(t => t
        .setValue(this.plugin.settings.defaultModel)
        .onChange(async (v) => {
          this.plugin.settings.defaultModel = v;
          await this.plugin.saveSettings();
        }));
  }
}
