# URL Ingest 实现状态

## 采集链路架构

```
[飞书 bot 消息]
    -> [lark-listener LaunchAgent]  (常驻，KeepAlive)
    -> [pending_urls SQLite 队列]
           |
    [pending-url-worker LaunchAgent]  (常驻，20s 轮询)
           |
    +-------------------+-------------------+
    |                   |                   |
  url_ingest       parser_stage      write_back_stage
  (抓取+写inbox)   (LLM分类)         (归档到vault)

[06:00 cron]  (补偿机制，兜底跑全量 incremental pipeline)
```

## 运行态服务

| 服务 | 端口 | LaunchAgent | 状态 |
|------|------|-------------|------|
| lark-url-listener | - | com.llkc.lark-url-listener | 运行中 |
| pending-url-worker | - | com.llkc.pending-url-worker | 运行中 |
| Qwen3TTS | :9999 | com.local.qwen3tts | 运行中 (venv Python 直调) |
| Qwen3TTS STT | :9998 | com.local.qwen3tts-stt | 运行中 (venv Python 直调) |
| API Server | :8765 | 手动 `llkc serve` | 按需启动 |

## 已完成

- URL 路由：微信公众号 / 抖音 / 小红书 / 通用网页（TikTok 仅识别，暂不采集）
- 小红书：复用 `opencli xiaohongshu note` 与 `download`，保留正文、作者、互动量和本地图片/视频
- 抖音：因 OpenCLI 没有单视频详情命令，使用独立 browser session 读取页面 DOM，提取文案、作者、互动量和视频地址；无论成功失败都会释放会话
- 视频转录：qwen3tts 为可选增强，ASR 离线时仍保留视频和元数据
- Markdown：写入 `00-Inbox/URL-Ingest/YYYY-MM/`，frontmatter 保留来源元数据
- SQLite：登记 `source_path`、`abs_path` 与 `raw_content`，可直接进入 parser
- API/Web：`POST /api/ingest/url`、URL 来源预判和 Web ingest 弹窗
- pending-url-worker：独立 daemon，20s 轮询消费队列，ingest 后触发后台分类+归档
- Qwen3TTS LaunchAgent 修复：改用 venv Python 直调，不再 `source activate`（避免权限问题）
- 健康检查：`GET /api/health` 报告队列状态和 dead URL 告警
- Dead URL 告警：重试耗尽的 URL 标记为 dead，写入 events 表并在 health API 暴露

## 运行机制

### 近实时处理 (worker)
- pending-url-worker 每 20 秒轮询 `pending_urls` 队列
- 每成功 ingest 一条 URL，后台线程触发 `parser_stage.run()` + `write_back_stage.run()`
- 分类与 ingest 解耦：ingest 快速完成，分类在后台异步执行
- 同一时间只有一个分类线程运行（`_classify_lock` 保护）
- 支持 SIGTERM 优雅退出

### 补偿机制 (cron)
- 每天 06:00 跑 `cron_incremental_v2.sh`，调用 `pipeline.run_incremental()`
- 全量流程：drain 队列 -> 扫描 inbox -> 分类 -> 归档
- 覆盖 worker 漏网场景（如 Mac 休眠期间 worker 未运行）

### Mac 休眠唤醒恢复
- LaunchAgent `KeepAlive=true` 确保进程在唤醒后自动重启
- `claim_pending_urls` 的 `stale_after_seconds` 参数自动回收卡住的 processing 行
- `fail_stale_runs` 在每次 CLI 启动时清理超时的 pipeline_runs
- 唤醒后 worker 会在 20s 内恢复消费

### 失败处理
- 单条 URL 最多重试 3 次（`max_attempts`）
- 重试期间状态为 `failed`，可被 worker 再次 claim
- 重试耗尽后标记为 `dead`，写入 `PendingURL.Dead` 事件
- dead URL 通过 `GET /api/health` 暴露，便于人工排查

## 已验证

- OpenCLI 1.8.4 命令清单与小红书 adapter 源码契约
- OpenCLI adapter/browser 命令参数差异（browser 子命令不能附加 `-f json`）
- OpenCLI 输出前后混入升级提示时的 JSON 解析
- 抖音 owned session 的关闭语义
- 小红书媒体文件扫描与 schema 映射
- ASR 离线时保留抖音视频
- URL item 的绝对路径数据库登记
- pending-worker daemon 单元测试（7 项全通过）
- 全量测试 34 项通过
- Python 全量编译与前端 JavaScript 语法检查

## 真实端到端前置条件

1. 启动 OpenCLIApp，并确保 Chrome 中已安装、连接 OpenCLI 扩展。
2. 在 Chrome 登录抖音和小红书。
3. `opencli doctor` 的 daemon / extension / connectivity 全部通过。
4. 如需视频转录，Qwen3TTS `:9999` 需在线；否则采集仍可成功，只是不生成转录。

## 部署

### LaunchAgent 安装
```bash
# pending-url-worker
cp deploy/com.llkc.pending-url-worker.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.llkc.pending-url-worker.plist

# Qwen3TTS (修复版，venv Python 直调)
cp deploy/com.local.qwen3tts.plist ~/Library/LaunchAgents/
launchctl unload ~/Library/LaunchAgents/com.local.qwen3tts.plist 2>/dev/null
launchctl load ~/Library/LaunchAgents/com.local.qwen3tts.plist

# Qwen3TTS STT (修复版)
cp deploy/com.local.qwen3tts-stt.plist ~/Library/LaunchAgents/
launchctl unload ~/Library/LaunchAgents/com.local.qwen3tts-stt.plist 2>/dev/null
launchctl load ~/Library/LaunchAgents/com.local.qwen3tts-stt.plist
```

### Cron
```bash
crontab deploy/llkc.crontab
# 每天早上 6 点跑全量 incremental pipeline 作为补偿机制
```

### 日志
- pending-url-worker: `output/pending_worker.out.log` / `output/pending_worker.err.log`
- lark-listener: `output/lark_listener.out.log` / `output/lark_listener.err.log`
- Qwen3TTS: `~/Library/Logs/qwen3tts.log` / `~/Library/Logs/qwen3tts.err`
- Cron: `output/cron_logs/YYYY-MM-DD_HHMMSS.log`
