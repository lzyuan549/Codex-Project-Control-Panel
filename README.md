# Docker Web Codex Plan Runner

一个单容器 Web 控制台：上传基础项目 ZIP 和项目题目后，先让 Codex 生成 `PLAN.md`、`HANDOFF.md`、`TEST_REPORT.md`，用户确认后再按 `PLAN.md` 每轮最多 10 个未完成项循环执行。

## 快速部署

```bash
export ADMIN_PASSWORD='your-login-password'
export SESSION_SECRET='replace-with-a-long-random-string'
export CODEX_GATEWAY_BASE_URL='https://your-gateway.example.com/v1'
export CODEX_GATEWAY_API_KEY='your-gateway-key'
export CODEX_MODEL='your-model-name'

docker compose up -d --build
```

默认 Docker 构建已使用国内镜像：

- 基础镜像：`m.daocloud.io/docker.io/library/node:22-bookworm-slim`
- apt：清华 Debian 镜像，首次安装 CA 证书前使用 `http`
- pip：阿里云 PyPI 镜像
- npm：npmmirror

如服务器上某个源不可用，可以通过环境变量替换：

```bash
export BASE_IMAGE='docker.1ms.run/node:22-bookworm-slim'
export APT_MIRROR='http://mirrors.aliyun.com/debian'
export APT_SECURITY_MIRROR='http://mirrors.aliyun.com/debian-security'
export PIP_INDEX_URL='https://mirrors.aliyun.com/pypi/simple/'
export NPM_REGISTRY='https://registry.npmmirror.com'

docker compose build --no-cache
```

## 使用流程

打开 `http://服务器IP:8000`，登录后：

1. 上传基础项目 ZIP。通常是 `auth-only.zip`，或一个包含 `auth-only/README.md` 的现有项目 ZIP。
2. 填写项目题目，例如“校园综合服务网页，课程表/失物/二手/公告等全整合，适配手机端”。
3. 可选上传 `constraints/*.md` 或 `constraints/*.txt`。
4. 点击“生成规划”，等待 `PLAN.md` 预览。
5. 如果规划不合适，在反馈框输入修改意见，点击“调整规划”。
6. 满意后点击“开始执行”，Codex 会按 `PLAN.md` 每轮最多推进 10 个未完成项。

`auth-only` 只作为基础权限模板或复用来源，最终开发仍应落到 `frontend/`、`backend/`、`db/`。

## 运行逻辑

- 每次上传都会创建一个新的 workspace。
- 原始 ZIP、项目题目和约束文件会保存到 `inputs/`。
- 程序会在 workspace 中执行 `git init`。
- 规划阶段只允许生成/修订：
  - `PLAN.md`
  - `HANDOFF.md`
  - `TEST_REPORT.md`
- 执行阶段每一轮运行：

```bash
codex exec --dangerously-bypass-approvals-and-sandbox -C <workspace> --json -o <final-message> -
```

Codex 配置在容器启动时写入 `~/.codex/config.toml`，使用 `CODEX_GATEWAY_BASE_URL`、`CODEX_GATEWAY_API_KEY` 和 `CODEX_MODEL`。

## 历史与下载

页面下方“历史记录”可查看过去任务的：

- `PLAN.md`
- `HANDOFF.md`
- `TEST_REPORT.md`
- 轮次日志
- 工作区文件
- 工作区 ZIP 下载

## 安全提示

该服务会在容器内触发 Codex 的危险全权限模式。请只部署在可信服务器上，并务必设置强 `ADMIN_PASSWORD`，不要裸露到公网。
