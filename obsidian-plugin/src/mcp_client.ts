/**
 * mcp_client.ts — 跟本地 Python MCP server (mcp_server.py) 通信的客户端
 * over stdio,JSON-RPC 2.0,line-delimited
 */
import { spawn, ChildProcess } from "child_process";

export interface McpContentItem {
  type: "text";
  text: string;
}
export interface McpToolResult {
  isError?: boolean;
  content: McpContentItem[];
}
export interface McpToolSpec {
  name: string;
  description: string;
  inputSchema: any;
}

interface PendingRequest {
  resolve: (v: any) => void;
  reject: (e: Error) => void;
}

export class McpClient {
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
    this.proc = spawn(this.python, [this.serverPath], {
      stdio: ["pipe", "pipe", "pipe"],
    });
    this.proc.stdout?.on("data", (chunk) => this._onStdout(chunk.toString()));
    this.proc.stderr?.on("data", (chunk) => {
      // ignore stderr noise unless needed
      // console.error("[mcp_server stderr]", chunk.toString());
    });
    this.proc.on("exit", (code) => {
      // reject all pending
      for (const p of this.pending.values()) {
        p.reject(new Error(`mcp server exited (code=${code})`));
      }
      this.pending.clear();
      this.proc = null;
    });
    // initialize
    await this._request("initialize", {
      protocolVersion: "2024-11-05",
      capabilities: {},
      clientInfo: { name: "obsidian-llkc", version: "0.1.0" },
    });
    // send notifications/initialized (no response expected)
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
          if (msg.error) {
            p.reject(new Error(msg.error.message ?? JSON.stringify(msg.error)));
          } else {
            p.resolve(msg.result);
          }
        }
      } catch {
        // ignore parse errors on non-JSON lines
      }
    }
  }

  private _request(method: string, params: any): Promise<any> {
    if (!this.proc) {
      return Promise.reject(new Error("mcp client not started"));
    }
    const id = this.nextId++;
    const msg = { jsonrpc: "2.0", id, method, params };
    return new Promise((resolve, reject) => {
      this.pending.set(id, { resolve, reject });
      this.proc!.stdin?.write(JSON.stringify(msg) + "\n");
    });
  }
}
