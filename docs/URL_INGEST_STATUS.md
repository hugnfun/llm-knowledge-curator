# URL Ingest 实现状态

## 已完成

- URL 路由：微信公众号 / 抖音 / 小红书 / 通用网页（TikTok 仅识别，暂不采集）
- 小红书：复用 `opencli xiaohongshu note` 与 `download`，保留正文、作者、互动量和本地图片/视频
- 抖音：因 OpenCLI 没有单视频详情命令，使用独立 browser session 读取页面 DOM，提取文案、作者、互动量和视频地址；无论成功失败都会释放会话
- 视频转录：qwen3tts 为可选增强，ASR 离线时仍保留视频和元数据
- Markdown：写入 `00-Inbox/URL-Ingest/YYYY-MM/`，frontmatter 保留来源元数据
- SQLite：登记 `source_path`、`abs_path` 与 `raw_content`，可直接进入 parser
- API/Web：`POST /api/ingest/url`、URL 来源预判和 Web ingest 弹窗

## 已验证

- OpenCLI 1.8.4 命令清单与小红书 adapter 源码契约
- OpenCLI adapter/browser 命令参数差异（browser 子命令不能附加 `-f json`）
- OpenCLI 输出前后混入升级提示时的 JSON 解析
- 抖音 owned session 的关闭语义
- 小红书媒体文件扫描与 schema 映射
- ASR 离线时保留抖音视频
- URL item 的绝对路径数据库登记
- `python3 -m unittest discover -s tests -v`
- Python 全量编译与前端 JavaScript 语法检查

## 真实端到端前置条件

1. 启动 OpenCLIApp，并确保 Chrome 中已安装、连接 OpenCLI 扩展。
2. 在 Chrome 登录抖音和小红书。
3. `opencli doctor` 的 daemon / extension / connectivity 全部通过。
4. 如需视频转录，启动 qwen3tts；否则采集仍可成功，只是不生成转录。

当前机器的 OpenCLIApp 进程可启动，但浏览器桥和扩展尚未连通，因此本轮没有把真实平台采集冒充成已验收；连接恢复后按 `docs/URL_INGEST.md` 的 API 示例即可跑真实 smoke test。
