# FastAPI Project - 后端

## 环境要求

* [Docker](https://www.docker.com/)。
* [uv](https://docs.astral.sh/uv/) 用于 Python 包和环境管理。

## Docker Compose

按照 [../development.md](../development.md) 的指南启动本地开发环境。

## 通用工作流

默认使用 [uv](https://docs.astral.sh/uv/) 管理依赖，先去安装它。

在 `./backend/` 目录下可以安装全部依赖：

```console
$ uv sync
```

然后激活虚拟环境：

```console
$ source .venv/bin/activate
```

确保你的编辑器使用了正确的 Python 虚拟环境，解释器路径为 `backend/.venv/bin/python`。

在 `./backend/app/models.py` 中修改或新增数据与 SQL 表的 SQLModel 模型，在 `./backend/app/api/` 中编写 API 端点，在 `./backend/app/crud.py` 中编写 CRUD（增删改查）工具函数。

## VS Code

项目已经配置好了通过 VS Code 调试器运行后端，你可以使用断点、暂停、查看变量等功能。

同时也配置好了通过 VS Code 的 Python 测试面板运行测试。

## Docker Compose Override

开发期间，你可以在 `compose.override.yml` 文件中修改只影响本地开发环境的 Docker Compose 配置。

对该文件的改动只影响本地开发环境，不影响生产环境。所以你可以往里加一些有助于开发流程的"临时"改动。

例如，后端代码目录会同步到 Docker 容器里，把你修改的代码实时复制进容器内的目录。这让你能立刻测试改动，无需重新构建 Docker 镜像。这只应在开发时这么做，生产环境应该用较新的后端代码构建 Docker 镜像。但在开发时，这能让你迭代得非常快。

另外还有一处 command 覆盖，它运行 `fastapi run --reload` 而不是默认的 `fastapi run`。它启动单个服务进程（而非生产环境那样的多进程），并在代码变化时重载进程。注意如果你有语法错误并保存了 Python 文件，它会中断退出，容器随之停止。之后你可以修复错误并重新运行来重启容器：

```console
$ docker compose watch
```

还有一处被注释掉的 `command` 覆盖，你可以取消注释并把默认那条注释掉。它会让后端容器运行一个"什么都不做"的进程，但保持容器存活。这样你就能进入运行中的容器执行命令，比如启动一个 Python 解释器测试已安装的依赖，或者启动一个检测到变化就重载的开发服务器。

要用 `bash` 会话进入容器，先启动整栈：

```console
$ docker compose watch
```

然后在另一个终端 `exec` 进入运行中的容器：

```console
$ docker compose exec backend bash
```

你应该会看到类似这样的输出：

```console
root@7f2607af31c3:/app#
```

这表示你在容器内的一个 `bash` 会话中，身份是 `root` 用户，位于 `/app` 目录。该目录下还有一个叫 "app" 的目录，那里就是你的代码在容器里的位置：`/app/app`。

在那里你可以用 `fastapi run --reload` 命令运行支持实时重载的调试服务器。

```console
$ fastapi run --reload app/main.py
```

看起来会是这样：

```console
root@7f2607af31c3:/app# fastapi run --reload app/main.py
```

然后回车。这就运行起了检测到代码变化即自动重载的服务器。

不过，如果它检测到的不是改动而是语法错误，就会直接报错停止。但因为容器仍然存活、你也还在 bash 会话里，修复错误后可以快速重启（"上方向键" + 回车）。

……前面提到的这个细节，正是"让容器空转存活、然后在 bash 会话里手动跑实时重载服务器"这种做法的价值所在。

## 后端测试

运行后端测试：

```console
$ bash ./scripts/test.sh
```

测试用 Pytest 运行，在 `./backend/tests/` 中修改和新增测试。

如果你使用 GitHub Actions，测试会自动运行。

### 在运行中的栈上跑测试

如果你的栈已经起来了，只想跑一下测试，可以用：

```bash
docker compose exec backend bash scripts/tests-start.sh
```

`/app/scripts/tests-start.sh` 脚本只是在确认其余部分已就绪后调用 `pytest`。如果你需要给 `pytest` 传额外参数，可以传给这条命令，它们会被转发过去。

例如，遇到第一个错误就停止：

```bash
docker compose exec backend bash scripts/tests-start.sh -x
```

### 测试覆盖率

测试运行后会生成 `htmlcov/index.html` 文件，用浏览器打开即可查看测试覆盖率。

## 数据库迁移

由于本地开发时应用目录以 volume 形式挂载在容器内，你也可以在容器里运行 `alembic` 命令，生成的迁移代码会出现在你的应用目录中（而不是只存在于容器内）。这样你就能把它加入 git 仓库。

每次修改模型后，务必创建一个 "revision" 并用该 revision "upgrade" 你的数据库。这才是真正更新数据库表的操作。否则你的应用会报错。

* 在后端容器中启动一个交互式会话：

```console
$ docker compose exec backend bash
```

* Alembic 已配置好，会从 `./backend/app/models.py` 导入你的 SQLModel 模型。

* 修改模型后（例如新增一列），在容器内创建一个 revision，例如：

```console
$ alembic revision --autogenerate -m "Add column last_name to User model"
```

* 把 alembic 目录下生成的文件提交到 git 仓库。

* 创建 revision 之后，在数据库上执行迁移（这一步才会真正改变数据库）：

```console
$ alembic upgrade head
```

如果你完全不想使用迁移，可以取消注释 `./backend/app/core/db.py` 文件中以这一行结尾的代码：

```python
SQLModel.metadata.create_all(engine)
```

并把 `scripts/prestart.sh` 文件中这一行注释掉：

```console
$ alembic upgrade head
```

如果你不想沿用默认模型、想从一开始就删除或修改它们且不保留任何历史 revision，可以删除 `./backend/app/alembic/versions/` 下的 revision 文件（`.py` Python 文件），然后按上面的方法创建第一个迁移。

## 邮件模板

邮件模板位于 `./backend/app/email-templates/`。这里有两个目录：`build` 和 `src`。`src` 目录存放用于构建最终邮件模板的源文件，`build` 目录存放应用实际使用的最终邮件模板。

继续之前，请确保你在 VS Code 中安装了 [MJML 扩展](https://github.com/mjmlio/vscode-mjml)。

装好 MJML 扩展后，你可以在 `src` 目录中创建新的邮件模板。创建完成并在编辑器中打开该 `.mjml` 文件后，用 `Ctrl+Shift+P` 打开命令面板，搜索 `MJML: Export to HTML`。这会把 `.mjml` 文件转换成 `.html` 文件，然后你就可以把它保存到 build 目录了。
