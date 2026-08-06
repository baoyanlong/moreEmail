# FastAPI Project - 前端

前端使用 [Vite](https://vitejs.dev/)、[React](https://reactjs.org/)、[TypeScript](https://www.typescriptlang.org/)、[TanStack Query](https://tanstack.com/query)、[TanStack Router](https://tanstack.com/router) 和 [Tailwind CSS](https://tailwindcss.com/) 构建。

## 环境要求

- [Bun](https://bun.sh/)（推荐）或 [Node.js](https://nodejs.org/)

## 快速开始

```bash
bun install
bun run dev
```

* 然后在浏览器打开 http://localhost:5173/。

注意这个实时开发服务器不在 Docker 里运行，它用于本地开发，也是推荐的工作流。前端调整满意之后，你可以构建后端 Docker 镜像并启动，测试类生产环境的形态——此时 FastAPI 会在 `http://localhost:8000` 提供构建好的前端。

查看 `package.json` 了解其他可用命令。

### 移除前端

如果你在开发纯 API 应用，想移除前端，可以很轻松地做到：

* 删除 `./frontend` 目录。

* 在 `backend/app/main.py` 中删除 `app.frontend()` 调用。

* 在 `backend/Dockerfile` 中删除前端构建阶段和 `COPY --from=frontend-build` 指令。

* 在 `compose.override.yml` 中删除 `playwright` 服务。

搞定，你得到了一个无前端（纯 API）的应用。🤓

---

如果你愿意，还可以从下面的位置移除 `FRONTEND_HOST` 环境变量：

* `.env`

但这只是清理而已，留着也不会有任何影响。

## 生成客户端

### 自动方式

* 激活后端虚拟环境。
* 在项目顶层目录运行脚本：

```bash
bash ./scripts/generate-client.sh
```

* 提交变更。

### 手动方式

* 启动 Docker Compose 栈。

* 从 `http://localhost/api/v1/openapi.json` 下载 OpenAPI JSON 文件，复制到 `frontend` 目录根下的新文件 `openapi.json`。

* 生成前端客户端：

```bash
bun run generate-client
```

* 提交变更。

注意每次后端发生变化（OpenAPI schema 改变）时，你都应该重新执行以上步骤来更新前端客户端。

## 使用远程 API

默认情况下，构建后的前端使用与 FastAPI 应用相同的源（origin）。如果你想在运行 Vite 开发服务器时使用远程 API，可以把环境变量 `VITE_API_URL` 设为远程 API 的地址。例如在 `frontend/.env` 文件中设置：

```env
VITE_API_URL=https://my-domain.example.com
```

这样运行前端时，它就会用这个地址作为 API 的基础 URL。

## 代码结构

前端代码结构如下：

* `frontend/src` - 前端主代码。
* `frontend/src/assets` - 静态资源。
* `frontend/src/client` - 生成的 OpenAPI 客户端。
* `frontend/src/components` - 前端的各个组件。
* `frontend/src/hooks` - 自定义 hooks。
* `frontend/src/routes` - 前端的各个路由，包含页面。

## 使用 Playwright 做端到端测试

前端包含了一套初始的 Playwright 端到端测试。要运行这些测试，你需要先让 Docker Compose 栈跑起来。用下面的命令启动：

```bash
docker compose up -d --wait backend
```

然后运行测试：

```bash
bunx playwright test
```

你也可以用 UI 模式运行测试，从而看到浏览器并与之交互：

```bash
bunx playwright test --ui
```

要停止并移除 Docker Compose 栈、清理测试产生的数据，使用：

```bash
docker compose down -v
```

要修改测试，进入 tests 目录编辑现有测试文件或按需新增。

关于编写和运行 Playwright 测试的更多信息，参见官方 [Playwright 文档](https://playwright.dev/docs/intro)。
