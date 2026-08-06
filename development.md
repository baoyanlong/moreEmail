# FastAPI Project - 开发

## Docker Compose

* 用 Docker Compose 启动本地整栈：

```bash
docker compose watch
```

* 现在你可以在浏览器中访问这些地址：

由 FastAPI 同时提供前端和 API 的应用：<http://localhost:8000>

自动生成的交互式 API 文档（Swagger UI）：<http://localhost:8000/docs>

Adminer，数据库网页管理界面：<http://localhost:8080>

Traefik UI，查看代理如何处理路由：<http://localhost:8090>

**注意**：首次启动整栈时，可能需要一分钟才能就绪。这段时间后端在等待数据库就绪并完成各项配置。你可以查看日志来观察进度。

查看日志（在另一个终端中运行）：

```bash
docker compose logs
```

查看某个具体服务的日志，加上服务名即可，例如：

```bash
docker compose logs backend
```

## Mailcatcher

Mailcatcher 是一个简易 SMTP 服务器，用于在本地开发期间捕获后端发出的所有邮件。这些邮件不会真的发出去，而是被捕获并展示在一个网页界面里。

它适用于：

* 开发期间测试邮件功能
* 验证邮件内容和排版
* 调试邮件相关功能而不必真的发信

用 Docker Compose 在本地运行时，后端会自动配置为使用 Mailcatcher（SMTP 端口 1025）。所有捕获到的邮件可在 <http://localhost:1080> 查看。

## 本地开发

Docker Compose 文件配置成让每个辅助服务在 `localhost` 的不同端口上提供服务。

FastAPI 把构建好的前端和 API 作为一个应用在 `http://localhost:8000` 提供服务。API 路由位于 `/api` 下。

如果想让前端支持实时重载，你仍然可以单独运行本地 Vite 开发服务器。

启动本地前端开发服务器：

```bash
bun run dev
```

或者你也可以停掉 `backend` 这个 Docker Compose 服务：

```bash
docker compose stop backend
```

然后在本地运行后端开发服务器：

```bash
cd backend
fastapi dev app/main.py
```

## 在 `localhost.tiangolo.com` 上使用 Docker Compose

启动 Docker Compose 栈时，默认使用 `localhost`，每个服务占用不同端口。

部署到生产（或预发布）环境时，应用使用单一域名。前端在 `/` 提供服务，API 位于 `/api` 下。

在[部署](deployment.md)指南中你可以了解 Traefik——项目配置的代理组件，它负责根据域名把流量转发到应用服务。

如果你想在本地验证这套机制是否正常，可以编辑本地 `.env` 文件，修改：

```dotenv
DOMAIN=localhost.tiangolo.com
```

Docker Compose 文件会用它来配置各服务的基础域名。

Traefik 会把 `localhost.tiangolo.com` 上的应用流量转发给 FastAPI，由后者同时提供前端和 API。

`localhost.tiangolo.com` 是一个特殊域名，它（连同所有子域名）被配置为指向 `127.0.0.1`。因此你可以用它做本地开发。

修改之后，重新运行：

```bash
docker compose watch
```

在生产等环境部署时，主 Traefik 是在 Docker Compose 文件之外配置的。`compose.override.yml` 里内置的 Traefik 仅供本地开发，用来测试域名是否按预期工作，例如 `localhost.tiangolo.com`。

## Docker Compose 文件与环境变量

有一个主 `compose.yml` 文件，包含适用于整栈的全部配置，`docker compose` 会自动使用它。

还有一个 `compose.override.yml`，包含开发环境的覆盖配置，例如把源代码挂载为 volume。`docker compose` 会自动用它来覆盖 `compose.yml` 中的配置。

这些 Docker Compose 文件会读取 `.env` 文件中的配置，并作为环境变量注入容器。

它们还会用到一些在调用 `docker compose` 命令之前由脚本设置的环境变量。

修改变量后，记得重启整栈：

```bash
docker compose watch
```

## `.env` 文件

`.env` 文件包含了你的全部配置、生成的密钥和密码等。

根据你的工作流，你可能想把它排除在 Git 之外，比如项目是公开的情况下。那样的话，你需要确保 CI 工具在构建或部署时能通过某种方式获取到它。

一种做法是把每个环境变量加到你的 CI/CD 系统中，并修改 `compose.yml` 文件让它读取对应的环境变量而不是读 `.env` 文件。

## Pre-commit 与代码检查

我们使用一个叫 [prek](https://prek.j178.dev/) 的工具（[Pre-commit](https://pre-commit.com/) 的现代替代品）来做代码检查和格式化。

安装之后，它会在 git 提交前自动运行。这样能确保代码在被提交之前就已经保持一致和格式规范。

项目根目录下有一个 `.pre-commit-config.yaml` 配置文件。

#### 安装 prek 使其自动运行

`prek` 已经是项目依赖的一部分。

安装好 `prek` 工具并确保可用后，你需要把它"安装"到本地仓库中，让它在每次提交前自动运行。

用 `uv` 可以这样做（确保你在 `backend` 目录下）：

```bash
❯ uv run prek install -f
prek installed at `../.git/hooks/pre-commit`
```

`-f` 参数用于强制安装，以防之前已经装过 `pre-commit` 钩子。

现在每当你尝试提交时，例如：

```bash
git commit
```

……prek 会运行，检查并格式化你即将提交的代码，然后要求你用 git 重新添加（暂存）这些代码。

之后你可以再次 `git add` 被修改/修复的文件，然后就能提交了。

#### 手动运行 prek 钩子

你也可以对所有文件手动运行 `prek`，用 `uv` 执行：

```bash
❯ uv run prek run --all-files
check for added large files..............................................Passed
check toml...............................................................Passed
check yaml...............................................................Passed
fix end of files.........................................................Passed
trim trailing whitespace.................................................Passed
ruff.....................................................................Passed
ruff-format..............................................................Passed
biome check..............................................................Passed
```

## URL 列表

生产或预发布环境的 URL 路径与下面相同，只是换成你自己的域名。

### 开发环境 URL

用于本地开发的 URL。

应用：<http://localhost:8000>

自动生成的交互式文档（Swagger UI）：<http://localhost:8000/docs>

自动生成的备用文档（ReDoc）：<http://localhost:8000/redoc>

Adminer：<http://localhost:8080>

Traefik UI：<http://localhost:8090>

MailCatcher：<http://localhost:1080>

### 配置了 `localhost.tiangolo.com` 的开发环境 URL

用于本地开发的 URL。

应用：<http://localhost.tiangolo.com>

自动生成的交互式文档（Swagger UI）：<http://localhost.tiangolo.com/docs>

自动生成的备用文档（ReDoc）：<http://localhost.tiangolo.com/redoc>

Adminer：<http://localhost.tiangolo.com:8080>

Traefik UI：<http://localhost.tiangolo.com:8090>

MailCatcher：<http://localhost.tiangolo.com:1080>
