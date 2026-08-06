# 贡献指南

感谢你有兴趣为 Full Stack FastAPI Template 做贡献！🙇

## 先讨论

对于**较大的改动**（新功能、架构调整、大规模重构），请先开一个 [GitHub Discussion](https://github.com/fastapi/full-stack-fastapi-template/discussions)。这样社区和维护者可以在你投入大量时间实现之前，先对方案给出反馈。

对于小而直接的改动，可以不经讨论直接提 Pull Request。这类改动包括：

- 拼写和语法修正
- 可复现的小 bug 修复
- 修复 lint 警告或类型错误
- 细微的代码改进（例如删除无用代码）

注意：非团队成员的 PR 不允许修改 `pyproject.toml` 或 `uv.lock`，以防范供应链风险。
如果你想添加新依赖，请开一个 [Discussion](https://github.com/fastapi/full-stack-fastapi-template/discussions) 说明理由。

## 开发

关于搭建开发环境、运行整栈、代码检查、pre-commit 钩子等详细说明，请参阅[开发指南](development.md)。

## Pull Request

提交 pull request 时：

1. 提交前确保所有测试通过。
2. 让每个 PR 只聚焦于一处改动。
3. 如果改动了功能行为，同时更新测试。
4. 在 PR 描述中关联相关的 issue。

## 自动化代码与 AI

我们鼓励你使用任何能提升效率的工具来完成工作和贡献，包括 AI（LLM）工具等。但贡献本身应当包含有意义的人工介入、judgement 和上下文理解。

如果一个 PR 中投入的**人力**（例如撰写 LLM prompt）**少于**我们**审阅它所需投入的精力**，请**不要**提交这个 PR。

可以这样理解：我们自己也能写 LLM prompt 或运行自动化工具，而且那比审阅外部 PR 更快。

### 关闭自动化和 AI 生成的 PR

如果我们发现某个 PR 像是 AI 生成或以类似方式自动产出的，我们会标记并关闭它。

评论和描述同理，请不要直接粘贴 LLM 生成的内容。

### 对人力的拒绝服务攻击

用自动化工具和 AI 提交需要我们仔细审阅和处理的 PR 或评论，等同于对我们的人力发起一次[拒绝服务攻击](https://en.wikipedia.org/wiki/Denial-of-service_attack)。

提交者只需极小的成本（一条 LLM prompt），却在我们这边产生大量工作（仔细审阅代码）。

请不要这样做。

对于反复用自动化 PR 或评论刷屏的账号，我们将不得不予以封禁。

### 明智地使用工具

正如本叔叔所说：

> 能力越大，~~权力~~ **工具**越强，责任越大。

避免无意中造成伤害。

你手上有非常强大的工具，请明智地使用它们，真正带来帮助。

## 有疑问？

如果你对贡献流程有疑问，欢迎开一个 [GitHub Discussion](https://github.com/fastapi/full-stack-fastapi-template/discussions)。
