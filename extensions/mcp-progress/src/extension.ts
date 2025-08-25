import { spawn } from 'child_process';
import * as fs from 'fs';
import * as path from 'path';
import * as vscode from 'vscode';

// Simple JSON-RPC 2.0 over stdio helper
type JsonRpcId = number | string;
type JsonRpcRequest = { jsonrpc: '2.0'; id: JsonRpcId; method: string; params?: any };
type JsonRpcResponse = { jsonrpc: '2.0'; id: JsonRpcId; result?: any; error?: any };
type JsonRpcNotification = { jsonrpc: '2.0'; method: string; params?: any };

class JsonRpcClient {
  private proc?: ReturnType<typeof spawn>;
  private nextId = 1;
  private pending = new Map<JsonRpcId, { resolve: (v: any) => void; reject: (e: any) => void }>();
  private buf = '';
  private listeners: Array<(n: JsonRpcNotification) => void> = [];

  constructor(private cmd: string, private args: string[] = [], private cwd?: string) {}

  start(): void {
    this.proc = spawn(this.cmd, this.args, {
      cwd: this.cwd,
      env: process.env,
      stdio: ['pipe', 'pipe', 'pipe']
    });
    this.proc.stdout?.setEncoding('utf8');
    this.proc.stdout?.on('data', (chunk: string) => this.onData(chunk));
  this.proc.stderr?.on('data', (chunk: string) => console.error('[mcp stderr]', chunk.toString()));
  this.proc.on('exit', (code: number | null) => {
      if (code !== 0) {
        console.error('MCP process exited with', code);
      }
    });
  }

  stop(): void {
    this.proc?.kill();
    this.proc = undefined;
  }

  onNotification(cb: (n: JsonRpcNotification) => void): void {
    this.listeners.push(cb);
  }

  private onData(chunk: string): void {
    this.buf += chunk;
    while (true) {
      // Content-Length framed messages (LSP/MCP)
      const headerEnd = this.buf.indexOf('\r\n\r\n');
      const contentLengthHeaderIdx = this.buf.toLowerCase().indexOf('content-length:');
      if (headerEnd > -1 && contentLengthHeaderIdx > -1 && contentLengthHeaderIdx < headerEnd) {
        const headers = this.buf.slice(0, headerEnd).split(/\r\n/);
        let length = -1;
        for (const h of headers) {
          const m = /content-length:\s*(\d+)/i.exec(h);
          if (m) { length = parseInt(m[1], 10); break; }
        }
        if (length < 0) {
          // Malformed header; drop it
          this.buf = this.buf.slice(headerEnd + 4);
          continue;
        }
        const totalLen = headerEnd + 4 + length;
        if (this.buf.length < totalLen) {
          // Wait for more bytes
          return;
        }
        const body = this.buf.slice(headerEnd + 4, totalLen);
        this.buf = this.buf.slice(totalLen);
        this.dispatchMessage(body);
        continue;
      }

      // Fallback: newline-delimited JSON
      const idx = this.buf.indexOf('\n');
      if (idx >= 0) {
        const line = this.buf.slice(0, idx).trim();
        this.buf = this.buf.slice(idx + 1);
        if (line) this.dispatchMessage(line);
        continue;
      }
      // Need more data
      break;
    }
  }

  private dispatchMessage(payload: string): void {
    try {
      const msg = JSON.parse(payload);
      if (msg.id !== undefined && (msg.result !== undefined || msg.error !== undefined)) {
        const resp = msg as JsonRpcResponse;
        const h = this.pending.get(resp.id);
        if (h) {
          this.pending.delete(resp.id);
          resp.error ? h.reject(resp.error) : h.resolve(resp.result);
        }
      } else if (msg.method) {
        const note = msg as JsonRpcNotification;
        for (const l of this.listeners) l(note);
      }
    } catch (e) {
      console.error('Invalid JSON from MCP server:', e, 'payload=', payload.slice(0, 200));
    }
  }

  private send(obj: any): void {
    if (!this.proc || !this.proc.stdin) return;
    const json = JSON.stringify(obj);
    const frame = `Content-Length: ${Buffer.byteLength(json, 'utf8')}\r\n\r\n${json}`;
    this.proc.stdin.write(frame);
  }

  request(method: string, params?: any): Promise<any> {
    const id = this.nextId++;
    const req: JsonRpcRequest = { jsonrpc: '2.0', id, method, params };
    this.send(req);
    return new Promise((resolve, reject) => this.pending.set(id, { resolve, reject }));
  }

  notify(method: string, params?: any): void {
    const note: JsonRpcNotification = { jsonrpc: '2.0', method, params };
    this.send(note);
  }
}

let statusItem: vscode.StatusBarItem | undefined;
let client: JsonRpcClient | undefined;
let currentProgressToken: string | number | undefined;

async function connect(ctx: vscode.ExtensionContext): Promise<void> {
  if (!statusItem) {
    statusItem = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Left, 100);
    statusItem.text = '$(sync) MCP 0%';
    statusItem.tooltip = 'MCP progress';
    statusItem.show();
    ctx.subscriptions.push(statusItem);
  }

  // Spawn the demo MCP server (stdio) as defined in .mcp.json
  // For local extension use, run directly from repo root
  const repoRoot = vscode.workspace.workspaceFolders?.[0]?.uri.fsPath;
  if (!repoRoot) {
    vscode.window.showErrorMessage('No workspace found to locate progress-demo server');
    return;
  }
  // Prefer project .venv Python if available
  const venvPython = path.join(repoRoot, '.venv', 'bin', process.platform === 'win32' ? 'python.exe' : 'python');
  const pythonCmd = fs.existsSync(venvPython) ? venvPython : 'python3';
  client = new JsonRpcClient(pythonCmd, ['tools/mcp_progress.py'], repoRoot);
  client.start();

  client.onNotification((note) => {
    if (note.method === 'notifications/progress') {
      const { progressToken, value, label } = note.params ?? {};
      currentProgressToken = progressToken;
      const pct = Math.round((value ?? 0) * 100);
      statusItem!.text = `$(sync) MCP ${pct}%`;
    }
  });

  // Initialize (no-op for demo server, but send a ping to ensure IO is flowing)
  try {
    await client.request('initialize', { clientInfo: { name: 'mcp-progress-ext', version: '0.0.1' } });
  } catch (_) {
    // Not all demo servers implement initialize; ignore
  }
}

async function startLongTask(): Promise<void> {
  if (!client) {
    vscode.window.showWarningMessage('Not connected to MCP server. Connecting now…');
    await connect({ subscriptions: [] } as any);
  }
  if (!client) return;

  let cancelled = false;
  const seconds = await vscode.window.showInputBox({ prompt: 'Seconds', value: '10' });
  if (!seconds) return;
  const steps = await vscode.window.showInputBox({ prompt: 'Steps', value: '10' });
  if (!steps) return;

  await vscode.window.withProgress(
    {
      location: vscode.ProgressLocation.Notification,
      cancellable: true,
      title: 'MCP: Long Task'
    },
    async (_progress: vscode.Progress<{ increment?: number; message?: string }>, token: vscode.CancellationToken) => {
      token.onCancellationRequested(() => {
        cancelled = true;
        if (currentProgressToken !== undefined && client) {
          client.request('tools/call', { name: 'cancel_task', arguments: { progress_token: currentProgressToken } }).catch(() => void 0);
        }
        vscode.window.showInformationMessage('Cancellation requested');
      });

      try {
  // Call the demo tool.
  if (!client) { return; }
  await client.request('tools/call', {
          name: 'long_task',
          arguments: {
            seconds: Number(seconds),
            steps: Number(steps),
            label: 'Working…'
          }
        });
      } catch (e: any) {
        if (!cancelled) {
          vscode.window.showErrorMessage('MCP long task failed: ' + (e?.message ?? String(e)));
        }
      }
    }
  );
}

export function activate(context: vscode.ExtensionContext) {
  context.subscriptions.push(
    vscode.commands.registerCommand('mcp-progress.connect', () => connect(context)),
    vscode.commands.registerCommand('mcp-progress.startLongTask', () => startLongTask())
  );
  // Optionally auto-connect
  connect(context).catch(() => void 0);
}

export function deactivate() {
  client?.stop();
}
