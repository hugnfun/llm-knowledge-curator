# URL Ingest — Step 1+2 of ContentOS 七步全链路 v2

统一采集入口:把微信公众号 / 抖音 / 小红书 / 通用网页的链接,
经过下载/转录/正文抽取,转成带 frontmatter 的 Markdown 落到 Obsidian
`00-Inbox/URL-Ingest/YYYY-MM/<slug>/<slug>.md`,并在 SQLite `items` 表
登记一行 `verdict=pending`,交给后续 parser 判别。

## 支持的来源与完整性保证

| Source | 文字全文 | 原始视频 | 音频/转录 | 图片资产 |
|---|---|---|---|---|
| mp.weixin.qq.com | ✓ md 全文 | — | — | ✓ 本地 images/ 复制到 assets/ |
| v.douyin.com / www.douyin.com | ✓ 文案+互动元数据+转录 | ✓ 页面原始 mp4 | ✓ qwen3tts 转录（可选） | — |
| xiaohongshu.com / xhslink.com 图文帖 | ✓ 帖子正文 | — | — | ✓ 无水印原图 |
| xiaohongshu.com / xhslink.com 视频帖 | ✓ 元数据+转录 | ✓ 原始 mp4 | ✓ qwen3tts 转录（可选） | — |
| 其他 https:// 网页 | ✓ 正文 markdown | — | — | — |

TikTok URL 仍会被识别，但 OpenCLI 当前没有单视频详情/下载命令，接口会返回明确的“不支持”错误，不会静默降级成残缺内容。

## 依赖装配

一次性初始化:

```bash
# 1) 项目 Python 包(在 llm-knowledge-curator 目录下)
pip3 install --user -r requirements-ingest.txt
# 如果 lxml / regex 报架构错(Apple Silicon):
ARCHFLAGS="-arch arm64" pip3 install --user --force-reinstall --no-cache-dir --no-binary=lxml,regex lxml regex

# 2) 微信文章 CLI(独立 uv tool 环境)
uv tool install wechat-article-to-markdown
uv tool run --from camoufox camoufox fetch    # 首次下载浏览器 runtime(~200MB)

# 3) OpenCLI（小红书、抖音）
#    安装/启动 OpenCLIApp，并在 Chrome 中登录小红书和抖音。
opencli doctor
# doctor 必须显示 daemon + extension + connectivity 正常。

# 4) qwen3tts 服务(视频转录后端)
#    在 ~/qwen3tts 项目里:python3 app/server.py --port 9999
#    或者用户已有的启动脚本
```

## 环境变量

| 变量 | 默认 | 说明 |
|---|---|---|
| `LLKC_WECHAT_CLI` | `wechat-article-to-markdown` | uv tool 装完后 PATH 里的 CLI 名 |
| `QWEN3TTS_URL` | `http://127.0.0.1:9999` | qwen3tts HTTP endpoint |
| `QWEN3TTS_TIMEOUT` | `600` | 单个视频最长转录等待秒数 |
| `LLKC_INGEST_TMP` | `/tmp/llkc_ingest` | 临时下载 workdir 根目录 |
| `LLKC_OPENCLI_BIN` | `opencli` | OpenCLI 可执行文件 |
| `LLKC_OPENCLI_TIMEOUT` | `180` | 单次 OpenCLI 命令超时秒数 |
| `LLKC_OPENCLI_RETRIES` | `3` | OpenCLI/浏览器桥瞬时失败重试次数 |

## API

### POST `/api/ingest/url`

Request:

```json
{ "url": "https://mp.weixin.qq.com/s/xxxxxxxx", "auto_classify": false }
```

Response(成功):

```json
{
  "ok": true,
  "source_type": "wechat",
  "unit_id": "url-20260720-...-wechat-...",
  "inbox_path": "00-Inbox/URL-Ingest/2026-07/wechat-...-title/wechat-...-title.md",
  "title": "文章标题",
  "has_video": false, "has_transcript": false, "has_images": true,
  "original_files": ["00-Inbox/URL-Ingest/2026-07/.../assets/img_001.png", ...]
}
```

### GET `/api/ingest/classify_url?url=...`

纯路由预览,不下载:

```json
{ "url": "...", "source_type": "wechat|douyin|tiktok|xhs|generic" }
```

## Web GUI

顶部导航栏右侧新增 **+ Ingest URL** 按钮,弹出输入框:

- 粘贴 URL,自动识别 source_type 并给提示
- 可选"完成后自动跑判别器"
- 提交后显示写入路径 + 资产清单 + unit_id

## iOS Shortcut 打通(用户侧)

在 iOS "捷径" App 中新建:

1. 接收类型:URL
2. Action: **获取 URL 的内容**
   - URL: `http://<你的Mac局域网IP>:8765/api/ingest/url`
   - 方法: POST
   - 请求体:JSON `{ "url": "<Shortcut input>" }`
3. 分享到"捷径",从抖音/小红书/微信右上角分享菜单直接触发。

> Mac 局域网 IP 可以在系统偏好设置 → 网络查看。首次使用需要允许"其他设备访问":
> `python3 -m uvicorn llkc.api.server:app --host 0.0.0.0 --port 8765`

## 常见问题

- **wechat CLI produced no .md file**:大概率是 `camoufox fetch` 没跑,浏览器 runtime 缺失
- **qwen3tts transcribe timed out**:确认 `http://127.0.0.1:9999/health` 可访问。ASR 是可选能力，失败时视频/图片和正文仍会正常入库，只是 `has_transcript=false`
- **OpenCLIApp is not running / extension not connected**:先启动 OpenCLIApp，再确认 Chrome 已安装 OpenCLI 扩展并运行 `opencli doctor`
- **Xiaohongshu security block / login required**:在 Chrome 登录小红书，尽量使用带 `xsec_token` 的完整笔记 URL
- **抖音页面未暴露可下载的视频地址**:确认链接是视频帖而非图文帖，并在登录后的 Chrome 中打开一次链接
- **lxml / regex 架构错**:见"依赖装配"第 1 步的 ARCHFLAGS 命令
