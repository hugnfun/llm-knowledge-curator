# 飞书机器人 URL 自动捕获

监听飞书机器人收到的 `im.message.receive_v1` 事件，从 text/post 消息中提取 HTTP(S) URL，写入 SQLite `pending_urls` 队列。监听器只入队，不在事件回调里下载或转录；后续 cron 负责消费。

## 前置条件

```bash
lark-cli auth status --verify
lark-cli event schema im.message.receive_v1 --json
```

bot identity 必须为 `ready`，并在飞书开发者后台启用 `im.message.receive_v1` 事件及所需消息只读权限。

## 运行

```bash
# 长期监听（stdin 由父进程保持打开）
python3 scripts/cli.py lark-listen

# 30 秒 smoke test
python3 scripts/cli.py lark-listen --timeout 30s
```

不要用 `< /dev/null` 或 `nohup ... </dev/null` 启动：`lark-cli event consume` 把 stdin EOF 视为优雅退出信号。服务停止时应发送 SIGTERM；不要使用 `kill -9`，否则可能跳过服务端订阅清理。

## 可选范围限制

在运行环境设置逗号分隔的 allowlist：

```bash
LLKC_LARK_CHAT_IDS=oc_xxx,oc_yyy
LLKC_LARK_SENDER_IDS=ou_xxx
```

不设置时，接受 bot 能看到的所有 chat/sender；消息类型默认仅 text/post，interactive 卡片会忽略，避免抓到卡片资源 URL。

## 数据与幂等

- `event_id`、`message_id`、`chat_id`、`sender_id` 一并保存，便于追踪来源。
- `normalized_url` 全局唯一；相同 URL 重复投递只计为 duplicate，不产生第二条任务。
- URL fragment 会移除，签名 query（例如小红书 `xsec_token`）会保留。
- 每个新 URL 会记录 `PendingURL.Captured` 审计事件。
