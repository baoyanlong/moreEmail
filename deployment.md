# FastAPI Project - 部署

你可以用 Docker Compose 把项目部署到远程服务器。

本项目预设你有一个 Traefik 代理来处理与外界的通信和 HTTPS 证书。

你可以用 CI/CD（持续集成与持续部署）系统实现自动部署，项目里已经准备好了用 GitHub Actions 实现的配置。

但你需要先配置几样东西。🤓

## 准备工作

* 准备好一台可用的远程服务器。
* 配置 DNS 记录，把应用域名以及你想暴露的各个辅助服务子域名指向该服务器，例如 `fastapi-project.example.com`、`traefik.fastapi-project.example.com` 和 `adminer.fastapi-project.example.com`。预发布环境的域名同理，例如 `staging.fastapi-project.example.com`。
* 在远程服务器上安装并配置 [Docker](https://docs.docker.com/engine/install/)（Docker Engine，不是 Docker Desktop）。

## 公共 Traefik

我们需要一个 Traefik 代理来处理入站连接和 HTTPS 证书。

下面这些步骤只需要做一次。

### Traefik Docker Compose

* 创建一个远程目录来存放 Traefik 的 Docker Compose 文件：

```bash
mkdir -p /root/code/traefik-public/
```

把 Traefik 的 Docker Compose 文件复制到服务器上。可以在本地终端运行 `rsync` 命令：

```bash
rsync -a compose.traefik.yml root@your-server.example.com:/root/code/traefik-public/
```

### Traefik 公共网络

这个 Traefik 需要一个名为 `traefik-public` 的 Docker "公共网络" 来与你的应用栈通信。

这样一来，就只有一个公共 Traefik 代理负责与外界的通信（HTTP 和 HTTPS），而在它背后，你可以运行一个或多个使用不同域名的应用栈，即使它们都在同一台服务器上。

在远程服务器上运行下面的命令，创建名为 `traefik-public` 的 Docker "公共网络"：

```bash
docker network create traefik-public
```

### Traefik 环境变量

Traefik 的 Docker Compose 文件需要你在启动前于终端中设置一些环境变量。在远程服务器上运行以下命令即可。

* 创建 HTTP Basic Auth 的用户名，例如：

```bash
export USERNAME=admin
```

* 创建一个环境变量存放 HTTP Basic Auth 的密码，例如：

```bash
export PASSWORD=changethis
```

* 用 openssl 生成该密码的"哈希"版本供 HTTP Basic Auth 使用，并存入环境变量：

```bash
export HASHED_PASSWORD=$(openssl passwd -apr1 $PASSWORD)
```

要确认哈希后的密码是否正确，可以打印出来：

```bash
echo $HASHED_PASSWORD
```

* 创建一个环境变量存放你服务器的域名，例如：

```bash
export DOMAIN=fastapi-project.example.com
```

* 创建一个环境变量存放 Let's Encrypt 使用的邮箱，例如：

```bash
export EMAIL=admin@example.com
```

**注意**：你需要换成一个真实的邮箱，`@example.com` 的邮箱不会生效。

### 启动 Traefik Docker Compose

进入你在远程服务器上存放 Traefik Docker Compose 文件的目录：

```bash
cd /root/code/traefik-public/
```

环境变量都设置好、`compose.traefik.yml` 也就位之后，运行下面的命令启动 Traefik Docker Compose：

```bash
docker compose -f compose.traefik.yml up -d
```

## 部署 FastAPI 项目

Traefik 就位之后，你就可以用 Docker Compose 部署你的 FastAPI 项目了。

**注意**：你可能想直接跳到后面关于 GitHub Actions 持续部署的章节。

## 复制代码

```bash
rsync -av --exclude=".git/" --filter=":- .gitignore" ./ root@your-server.example.com:/root/code/app/
```

注意：`--filter=":- .gitignore"` 告诉 `rsync` 使用与 git 相同的规则，忽略被 git 忽略的文件，比如 Python 虚拟环境。

## 环境变量

你需要先设置一些环境变量。

### 生成密钥

`.env` 文件里有些环境变量的默认值是 `changethis`。

你必须把它们改成密钥，可以运行下面的命令生成：

```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

复制输出内容作为密码 / 密钥。需要另一个密钥就再运行一次。

### 必需的环境变量

设置 `ENVIRONMENT`，默认是 `local`（用于开发），部署到服务器时应设为 `staging` 或 `production` 之类的值：

```bash
export ENVIRONMENT=production
```

设置 `DOMAIN`，默认是 `localhost`（用于开发），部署时应使用你自己的域名，例如：

```bash
export DOMAIN=fastapi-project.example.com
```

把 `POSTGRES_PASSWORD` 设为不同于 `changethis` 的值：

```bash
export POSTGRES_PASSWORD="changethis"
```

设置用于签发 token 的 `SECRET_KEY`：

```bash
export SECRET_KEY="changethis"
```

注意：你可以用上面的 Python 命令生成一个安全的密钥。

把 `FIRST_SUPER_USER_PASSWORD` 设为不同于 `changethis` 的值：

```bash
export FIRST_SUPERUSER_PASSWORD="changethis"
```

把 `FRONTEND_HOST` 设为应用地址。它用于生成邮件中的链接：

```bash
export FRONTEND_HOST="https://${DOMAIN?Variable not set}"
```

你还可以设置其他一些环境变量：

* `PROJECT_NAME`：项目名称，用于 API 文档和邮件中。
* `STACK_NAME`：用于 Docker Compose 标签和项目名的栈名称，`staging`、`production` 等环境应各不相同。你可以用同一个域名把点替换成短横线，例如 `fastapi-project-example-com` 和 `staging-fastapi-project-example-com`。
* `BACKEND_CORS_ORIGINS`：额外允许的 CORS 来源列表，用逗号分隔。由 FastAPI 提供服务的前端使用相同的源，不需要额外添加。
* `FIRST_SUPERUSER`：初始超级用户的邮箱，这个超级用户将是唯一能创建新用户的账号。
* `SMTP_HOST`：发送邮件的 SMTP 服务器地址，由你的邮件服务商提供（例如 Mailgun、Sparkpost、Sendgrid 等）。
* `SMTP_USER`：发送邮件的 SMTP 服务器用户名。
* `SMTP_PASSWORD`：发送邮件的 SMTP 服务器密码。
* `EMAILS_FROM_EMAIL`：发件邮箱账号。
* `POSTGRES_SERVER`：PostgreSQL 服务器的主机名。可以保留默认值 `db`，由同一个 Docker Compose 提供。除非你使用第三方服务商，否则通常不需要改。
* `POSTGRES_PORT`：PostgreSQL 服务器的端口。可以保留默认值。除非你使用第三方服务商，否则通常不需要改。
* `POSTGRES_USER`：Postgres 用户名，可以保留默认值。
* `POSTGRES_DB`：本应用使用的数据库名。可以保留默认值 `app`。
* `SENTRY_DSN`：Sentry 的 DSN，如果你使用 Sentry 的话。

## GitHub Actions 环境变量

有一些环境变量只被 GitHub Actions 使用，你可以配置它们：

* `LATEST_CHANGES`：被 GitHub Action [latest-changes](https://github.com/tiangolo/latest-changes) 用来根据合并的 PR 自动添加发布记录。它是一个个人访问令牌（personal access token），详见其文档。
* `SMOKESHOW_AUTH_KEY`：用于通过 [Smokeshow](https://github.com/samuelcolvin/smokeshow) 处理和发布代码覆盖率报告，按其说明创建一个（免费的）Smokeshow key。

### 用 Docker Compose 部署

环境变量都就位后，就可以用 Docker Compose 部署了：

```bash
cd /root/code/app/
docker compose -f compose.yml build
docker compose -f compose.yml up -d
```

生产环境你不会想带上 `compose.override.yml` 里的覆盖配置，所以这里显式指定了使用 `compose.yml`。

## 持续部署（CD）

你可以用 GitHub Actions 自动部署项目。😎

你可以配置多个环境的部署。

项目里已经配置好了两个环境：`staging` 和 `production`。🚀

### 安装 GitHub Actions Runner

* 在远程服务器上，为 GitHub Actions 创建一个用户：

```bash
sudo adduser github
```

* 给 `github` 用户添加 Docker 权限：

```bash
sudo usermod -aG docker github
```

* 临时切换到 `github` 用户：

```bash
sudo su - github
```

* 进入 `github` 用户的 home 目录：

```bash
cd
```

* [按照官方指南安装 GitHub Action 自托管 runner](https://docs.github.com/en/actions/hosting-your-own-runners/managing-self-hosted-runners/adding-self-hosted-runners#adding-a-self-hosted-runner-to-a-repository)。

* 当被问到标签（labels）时，添加一个环境标签，例如 `production`。标签之后也可以再添加。

安装完成后，指南会让你运行一条命令来启动 runner。不过一旦你终止那个进程、或者你与服务器的连接断开，它就会停止。

为了确保它开机自启并持续运行，你可以把它安装为一个服务。要这么做，先退出 `github` 用户，回到 `root` 用户：

```bash
exit
```

执行之后，你会回到之前的用户，也会回到属于该用户的之前那个目录。

在能进入 `github` 用户目录之前，你需要先成为 `root` 用户（你可能已经是了）：

```bash
sudo su
```

* 以 `root` 用户身份，进入 `github` 用户 home 目录下的 `actions-runner` 目录：

```bash
cd /home/github/actions-runner
```

* 以 `github` 用户身份把自托管 runner 安装为服务：

```bash
./svc.sh install github
```

* 启动服务：

```bash
./svc.sh start
```

* 查看服务状态：

```bash
./svc.sh status
```

更多内容参见官方指南：[将自托管 runner 应用配置为服务](https://docs.github.com/en/actions/hosting-your-own-runners/managing-self-hosted-runners/configuring-the-self-hosted-runner-application-as-a-service)。

### 配置 GitHub Environments

部署工作流使用 [GitHub Environments](https://docs.github.com/en/actions/how-tos/deploy/configure-and-manage-deployments/manage-environments) 来管理 `staging` 和 `production`。这使得你可以设置环境级别的密钥、部署保护规则（例如必需的审批人、等待计时器）以及部署状态追踪。

要配置它们，进入仓库的 **Settings** > **Environments**，创建 `staging` 和 `production` 两个环境。

### 设置 Secrets

为每个 GitHub Environment（`staging` 和 `production`）把所需的密钥配置为[环境级 secrets](https://docs.github.com/en/actions/how-tos/write-workflows/choose-what-workflows-do/use-secrets#creating-secrets-for-an-environment)。环境级 secrets 优于[仓库级 secrets](https://docs.github.com/en/actions/how-tos/write-workflows/choose-what-workflows-do/use-secrets#creating-secrets-for-a-repository)，因为它们的作用域被限定在特定环境内，减少了暴露面，也能与你配置的保护规则相配合。

当前的 GitHub Actions 工作流需要这些 secrets：

* `DOMAIN_PRODUCTION`
* `DOMAIN_STAGING`
* `STACK_NAME_PRODUCTION`
* `STACK_NAME_STAGING`
* `EMAILS_FROM_EMAIL`
* `FIRST_SUPERUSER`
* `FIRST_SUPERUSER_PASSWORD`
* `POSTGRES_PASSWORD`
* `SECRET_KEY`
* `LATEST_CHANGES`
* `SMOKESHOW_AUTH_KEY`

## GitHub Action 部署工作流

`.github/workflows` 目录下已经配置好了用于部署到各环境（带对应标签的 GitHub Actions runner）的工作流：

* `staging`：推送（或合并）到 `master` 分支后触发。
* `production`：发布 release 后触发。

两个工作流都关联到各自的 GitHub Environment，因此部署会显示在仓库的 **Environments** 区域，并遵守你配置的任何保护规则。

如果你需要添加额外的环境，可以以它们为起点。

## URL 列表

把 `fastapi-project.example.com` 替换成你自己的域名。

### Traefik 主控制台

Traefik UI：`https://traefik.fastapi-project.example.com`

### 生产环境

应用（前端和 API）：`https://fastapi-project.example.com`

交互式 API 文档：`https://fastapi-project.example.com/docs`

Adminer：`https://adminer.fastapi-project.example.com`

### 预发布环境

应用（前端和 API）：`https://staging.fastapi-project.example.com`

交互式 API 文档：`https://staging.fastapi-project.example.com/docs`

Adminer：`https://adminer.staging.fastapi-project.example.com`
