# Agent 协作规约(项目管理系统)

你在为我的项目开发。我有一套项目管理系统,你需要在合适的时机主动同步进度到系统里,不用等我要求。

## 你的身份

你是 Codex。所有 commit message 结尾必须加 `[codex]` 标签。

## 何时 commit + push

以下情况**立刻**执行完整的 git 操作序列(add + commit + push):

1. 完成了一个功能的独立环节(如接口写好、UI 组件完成、bug 修复通过测试)
2. 完成了一个可测试的最小单元(哪怕功能未完全,也应该 commit)
3. 完成了架构调整、重构、依赖升级等有明确边界的改动
4. 每完成一个 todo 后

不要等我说"你 commit 一下",你自己判断到达节点就做。

### commit 命令模板

```bash
git add -A
git commit -m "<type>: <描述做了什么> [codex]"
git push
```

type 用: feat / fix / refactor / docs / chore / test / style

例:
- `feat: 完成登录接口,含 JWT 校验 [codex]`
- `fix: 修复 dashboard 加载时数组为空报错 [codex]`
- `refactor: 抽离 API client 到 shared/api.ts [codex]`

## 何时添加 todo

以下情况**主动**通过 API 加 todo,不要只在对话里提:

1. 你在实现过程中发现了新的待办事项(如"应该加个测试"、"文档需要更新")
2. 我口头上说"下次做 X",你把它变成 todo
3. 发现的技术债 / 优化点 / 潜在 bug

### 添加 todo 的命令

先找到当前项目的 project_id:

```bash
curl -su $PM_USER:$PM_PASS $PM_URL/api/projects | python3 -c "import sys,json; [print(p['id'],p['name']) for p in json.load(sys.stdin)]"
```

然后创建 todo:

```bash
curl -su $PM_USER:$PM_PASS -X POST $PM_URL/api/todos \
  -H "Content-Type: application/json" \
  -d '{
    "project_id": "<项目ID>",
    "title": "<todo 标题,越具体越好>",
    "tags": ["backend"],
    "effort": "M",
    "priority": 2
  }'
```

参数说明:
- `tags`: backend / frontend / ui / react / api / docs / testing / architecture / debugging 等
- `effort`: S(半小时以内) / M(一两小时) / L(半天以上)
- `priority`: 0-5,越大越紧急,默认 2

## 环境变量

以下变量应在 shell 中配置好。如果执行 curl 时遇到 401,说明变量未设置,请先在项目根目录执行:

source .env

(`.env` 已 gitignore,包含实际凭据)

- `$PM_URL` = http://47.109.45.135
- `$PM_USER` = zhanghao
- `$PM_PASS` = 见 .env 文件

## 不要做

- 不要等我明确说"commit 一下"才动,达到节点就自主执行
- 不要一次 commit 涵盖多个不相关改动,一个节点一个 commit
- 不要在 commit message 里省略 `[codex]` 标签
- 不要跳过 push,只 commit 不 push 等于没做

## 你可以做

- 完成一个环节后,主动汇报"已 commit + push,message: xxx"
- 加了 todo 后告诉我"我加了个 todo: xxx",让我知道你在想什么
- 如果一个 todo 已经通过 commit 完成了,通过 API 标记它为 completed:

```bash
curl -su $PM_USER:$PM_PASS -X PUT $PM_URL/api/todos/<todo_id> \
  -H "Content-Type: application/json" \
  -d '{"status": "completed"}'
```
