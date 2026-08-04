# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目性质

这是 `full-stack-fastapi-template`（FastAPI + React 全栈模板）本身的源码仓库，同时也是一个 **Copier 模板**：`copier.yml` 定义模板变量，`hooks/post_gen_project.py` 在生成项目后运行。修改文件时注意不要破坏模板占位符。

顶层是 uv workspace（成员为 `backend`）+ bun workspace（成员为 `frontend`）。

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
docker compose exec backend bash
alembic revision --autogenerate -m "描述"
alembic upgrade head
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

### E2E 测试

`frontend/tests/` 下 Playwright，`auth.setup.ts` 作为 setup project 先登录并复用 storage state。测试会调用后端 `private` 路由创建用户，因此必须以 `ENVIRONMENT=local` 起后端。
