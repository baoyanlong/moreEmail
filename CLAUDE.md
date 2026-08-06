# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目性质

这是 `full-stack-fastapi-template`（FastAPI + React 全栈模板）本身的源码仓库，同时也是一个 **Copier 模板**：`copier.yml` 定义模板变量，`hooks/post_gen_project.py` 在生成项目后运行。修改文件时注意不要破坏模板占位符。

顶层是 uv workspace（成员为 `backend`）+ bun workspace（成员为 `frontend`）。

## 其他要求
1.每次改完代码后不要立即提交,我审核后,再决定是否提交

## 常用命令

### 整栈（Docker Compose）

```bash
# 日常启动（本机推荐写法，见下方说明，务必显式列出服务名）
docker compose up -d db prestart backend adminer mailcatcher
docker compose up -d db prestart backend            # 只要 API
docker compose watch db prestart backend            # 前台运行 + 代码同步热重载

docker compose logs -f backend
docker compose exec backend bash
docker compose down           # 停止（保留 app-db-data 卷）
docker compose down -v        # 停止并清空数据库
bash ./scripts/test.sh        # 构建镜像 + 起栈 + 跑后端测试 + 清理（CI 用的完整流程）
```

**不要用不带服务名的 `docker compose up -d` / `docker compose watch`。** `compose.override.yml` 里的 `playwright` 服务会被一并构建，它要拉一个约 800MB 的 `mcr.microsoft.com/playwright` 基础镜像，在国内网络下可能只有几十 KB/s（实测卡了 2 小时以上）。E2E 测试建议在本机跑 `bunx playwright test`，或按需单独 `docker compose up -d playwright`。

首次启动前需要 Docker Desktop 正在运行（`open /Applications/Docker.app`）。`prestart` 是一次性任务，跑完迁移和初始超级用户后 `exited (0)` 是正常状态。默认账号来自 `.env` 的 `FIRST_SUPERUSER` / `FIRST_SUPERUSER_PASSWORD`（`admin@example.com` / `changethis`）。

### 后端（在 `backend/` 下）

```bash
uv sync
fastapi dev app/main.py                       # 需先 docker compose stop backend
bash scripts/lint.sh                          # mypy + ty check + ruff check + ruff format --check
bash scripts/format.sh                        # ruff check --fix + ruff format
bash scripts/tests-start.sh [pytest 参数]      # 在容器内：等 DB 就绪后跑测试
bash scripts/test.sh                          # coverage run -m pytest tests/ + coverage html

# 跑单个测试（需要数据库可达）
FASTAPI_ENV=development uv run pytest tests/api/routes/test_items.py::test_create_item -x
```

测试依赖真实 Postgres（`conftest.py` 里 session 级 fixture 会 `init_db` 并在结束时清空 `Item`/`User` 表），不是内存 DB。所以本地跑测试前要先起 `db` 服务。

### 前端（在 `frontend/` 下，或用根目录的 `bun run --filter frontend`）

```bash
bun install
bun run dev                   # Vite dev server，:5173
bun run build                 # tsc -p tsconfig.build.json && vite build
bun run lint                  # biome check --write --unsafe
bunx playwright test          # E2E，需要 docker compose up -d --wait backend
bunx playwright test --ui
bunx playwright test tests/items.spec.ts -g "test name"
```

### 数据库迁移

```bash
docker compose exec -T backend alembic revision --autogenerate -m "描述"
# 生成物在容器里，必须手动复制回宿主机才能提交：
docker compose cp backend:/app/backend/app/alembic/versions/<新文件>.py ./backend/app/alembic/versions/
docker compose exec -T backend alembic upgrade head
```

**`backend/` 不是 volume 挂载**，`compose.override.yml` 用的是 `develop.watch` 单向同步（宿主机 → 容器），且只在 `docker compose watch` 下生效。所以：容器内生成的文件（迁移、`ruff --fix` 的结果）不会自动回到宿主机，要 `docker compose cp` 取出来；用 `up -d` 起的栈，宿主机的改动也不会自动进容器，要 `docker compose cp backend/app backend:/app/backend/` 推进去。

Dockerfile 也没有 COPY `backend/tests`，容器里默认没有测试文件，同样需要 `docker compose cp backend/tests backend:/app/backend/`。

另注意 `backend/scripts/test.sh` 里 `pytest tests/` 是写死的，额外参数会被传给后面的 `coverage html --title`，**不会**传给 pytest。要跑单个测试直接调 pytest：

```bash
docker compose exec -T -e FASTAPI_ENV=development backend python -m pytest tests/api/routes/test_items.py -q
```

`scripts/prestart.sh`（容器启动时执行）会依次运行 `backend_pre_start.py`（等 DB）→ `alembic upgrade head` → `initial_data.py`（建初始超级用户）。改了 `app/models.py` 就必须生成并提交迁移。

### 代码检查（prek / pre-commit）

```bash
uv run prek install -f        # 安装 git hook
uv run prek run --all-files
```

hook 里包含 `generate-frontend-sdk`：**任何 `backend/` 下的改动都会自动重跑 `scripts/generate-client.sh`**，见下。

## 架构要点

### 后端与前端是一个应用

`backend/app/main.py` 里 `app.frontend("/", directory=FRONTEND_DIR)` 让 FastAPI 直接托管构建好的前端；`frontend/vite.config.ts` 的 `build.outDir` 指向 `../backend/app/frontend`（该目录被 gitignore）。生产环境只有一个服务：`/` 是前端，`/api/v1` 是 API。本地开发时前端另跑 Vite（:5173），通过 CORS 访问后端。

### OpenAPI → 前端 SDK 是生成的，不要手改

`frontend/src/client/`（`sdk.gen.ts` / `types.gen.ts` / `schemas.gen.ts`）由 `bash ./scripts/generate-client.sh` 生成：后端导出 `openapi.json` → `@hey-api/openapi-ts` 生成 → 跑 biome。

生成的方法名依赖 `main.py` 中的 `custom_generate_unique_id`（`f"{route.tags[0]}-{route.name}"`），因此**每个路由必须带 tags，且函数名要唯一**，否则前端 SDK 的类名/方法名会错乱。`openapi-ts.config.ts` 里 `classNameBuilder: "{{name}}Service"` → 例如 tags 为 `items` 的路由生成 `ItemsService.readItems`。

改完后端 API 后必须重新生成客户端并提交。

### 后端分层

- `app/models.py` — 所有 SQLModel 模型集中于此，遵循 `XBase` / `XCreate` / `XUpdate` / `XPublic` / `XsPublic`（列表 + count）的命名约定；Alembic 从这里自动发现模型。
- `app/crud.py` — 数据库读写工具函数，签名用 keyword-only（`*, session, ...`）。
- `app/api/deps.py` — 依赖注入的核心，导出 `SessionDep`、`CurrentUser`、`get_current_active_superuser`（用 `Annotated[..., Depends(...)]` 别名，路由里直接用这些类型）。
- `app/api/routes/` — 每个模块一个 `APIRouter(prefix=..., tags=[...])`，在 `app/api/main.py` 注册。`private.py` 只在 `ENVIRONMENT == "local"` 时挂载（供 E2E 测试造数据）。
- `app/core/config.py` — pydantic-settings，读取**上一级目录的 `.env`**（`env_file="../.env"`），项目根的 `.env` 是唯一配置源。

Python 目标版本 3.14（ruff `target-version = "py314"`），代码里会用到 3.14 语法如 `except A, B:`。mypy 为 strict 模式，且同时跑 `ty check`。

### 前端结构

- TanStack Router 基于文件路由：`src/routes/`，`_layout.tsx` 是需要登录的布局，`routeTree.gen.ts` 由 `@tanstack/router-plugin` 自动生成（勿手改）。
- 认证：token 存 `localStorage.access_token`；`src/main.tsx` 中 `OpenAPI.TOKEN` 读取它，QueryCache/MutationCache 的 `onError` 统一处理 401/403 → 清 token 并跳 `/login`。
- 数据获取一律走 TanStack Query + 生成的 `*Service`，模式见 `src/hooks/useAuth.ts`。
- UI 为 Tailwind v4 + shadcn/ui（`src/components/ui/`，`components.json`）；`@/` 别名指向 `src/`。
- 格式化/lint 用 Biome（不是 ESLint/Prettier）。

### Steam 账号 / 临时邮箱模块

业务代码（非模板自带），实现「随机生成凭据 → 读验证码 → 保存账号」三步。

- `app/clients/imap.py` — 同步 IMAP，移植自 `~/Desktop/myProject/steamgenerateaccount`。含两阶段拉取、MIME 解码、字符集容错、资源边界。
- `app/clients/mailcow.py` — Mailcow HTTP API（同步 `httpx.Client`）。
- `app/services/mailbox.py` — catch-all 读信、真建邮箱、搬运历史邮件。
- `app/services/credentials.py` — `secrets` 随机凭据生成。
- `app/services/steam_parser.py` — 验证码与激活链接提取。
- `app/api/routes/steam_accounts.py` — 6 个接口，全部走 `ApiKeyDep`（`X-API-Key`），不接 User 体系。

**核心设计：catch-all 兜底 + 保存时才真建。** `/generate` 只吐随机字符串不落库，来信由 catch-all 兜住；保存时才调 Mailcow 真实创建邮箱，之后来信直接进独立邮箱（Postfix 精确匹配优先于 catch-all），账号可整套转让。这让「没保存的不入库」成为天然结果。

**保存接口的顺序不能反**：先建 Mailcow 邮箱，成功后才落库 —— 反过来会留下指向不存在邮箱、却占着唯一索引的记录。搬运历史邮件失败只记日志，不能让保存回滚。

`steam_parser.py` 的链接正则必须把 `steampowered.com` 锚在**主机名**位置并带结尾否定断言，否则 `https://evil.com/steampowered.com/x` 和 `https://steampowered.com.evil.com/x` 都会被当成官方链接 —— 有对应的测试守着，别放松。

本地开发时 `CATCHALL_*` / `MAILCOW_*` 留空，读信和保存接口返回 503，其余接口正常。

### E2E 测试

`frontend/tests/` 下 Playwright，`auth.setup.ts` 作为 setup project 先登录并复用 storage state。测试会调用后端 `private` 路由创建用户，因此必须以 `ENVIRONMENT=local` 起后端。
