# 添加邮箱域名操作指南

每新增一个用于生成随机邮箱的域名（如 `example.com`），照本文从头走一遍。全程约 20 分钟，其中 DNS 生效等待占大头。

---

## 一、先理解整体架构

不理解这套机制的话，后面每一步都像在盲目照抄，出错也不知道从哪查。

### 邮件是怎么流动的

```
Steam 发验证码到 k8p4x2q9@example.com
        │
        │  ① 查 example.com 的 MX 记录 → mail.3dbody.top
        ↓
   连接 mail.3dbody.top:25（你的服务器）
        │
        │  ② Postfix 查收件人 k8p4x2q9@example.com
        │     - 是真实邮箱吗？→ 不是（还没保存）
        │     - 有 catch-all 别名吗？→ 有，@example.com → catchall@3dbody.top
        ↓
   投递到 catchall@3dbody.top（兜底邮箱）
        │
        │  ③ 读信接口用 IMAP 登录 catchall，按 To: 头精确过滤
        ↓
   只返回收件人正好是 k8p4x2q9@example.com 的邮件
```

保存账号后，Mailcow 里真实创建了 `k8p4x2q9@example.com` 这个邮箱，于是第 ② 步走另一条分支：

```
   ② Postfix 查收件人
      - 是真实邮箱吗？→ 是！
      ↓
   直接投进 k8p4x2q9@example.com 自己的邮箱
   （精确匹配优先于 catch-all，这是 Postfix 的既定行为）
```

买家从此可以用这个邮箱和密码独立登录，看不到任何其他人的邮件。

### 由此推出每一步的必要性

| 步骤 | 目的 | 不做会怎样 |
|---|---|---|
| MX 记录 | 告诉全世界「发给 `@example.com` 的信送到 `mail.3dbody.top`」 | 信根本到不了你的服务器，Steam 会收到退信 |
| Mailcow 添加域名 | 让 Postfix 认这个域名，愿意接收它的邮件 | Postfix 视其为「与我无关的域」，直接拒收（`554 relay access denied`） |
| catch-all 别名 | 让不存在的随机地址也能收信 | 只有已保存的邮箱能收信，`/generate` 出来的新地址收不到验证码 |
| DKIM | 给这个域名一份加密签名身份 | 主要影响外发（本项目已禁止外发），但**不配的话别人可以随意冒用你的域名发垃圾邮件**，域名声誉受损后连收信都可能被影响 |
| SPF / DMARC | 声明「只有我的服务器能代表这个域名发信」 | 同上，防冒用 |
| 更新 `.env` | 让 `/generate` 知道有这个域名可用 | 接口不会生成该域名的地址 |

### 一个关键简化

**新域名不需要自己的 A 记录。** 它的 MX 指向已有的 `mail.3dbody.top`，所有邮件流量都汇到那一台服务器。你只有在希望用 `mail.example.com` 这种地址访问 Webmail 时才需要额外的 A 记录和证书——本项目用不着。

同理，**所有域名共用一个 catch-all 兜底邮箱** `catchall@3dbody.top`。Mailcow 的别名可以跨域指向，五个域名配五条别名、全部指向同一个邮箱即可。读信时按完整地址过滤，`abc@a.com` 和 `abc@b.com` 不会混淆。不需要为每个域名单独建兜底邮箱和 IMAP 凭据。

---

## 二、前置检查

开始前确认这些已经就绪（首次部署时已完成，之后每次加域名不必重做）：

```bash
# 在服务器上执行
sudo docker compose -f /opt/mailcow-dockerized/docker-compose.yml ps | grep -c healthy   # 应 ≥ 15
dig +short mail.3dbody.top A                                                              # 应返回 111.231.55.54
```

从任意机器验证 25 端口可达：

```bash
(echo QUIT) | nc mail.3dbody.top 25 | head -1
# 期望：220 mail.3dbody.top ESMTP Postcow
```

准备好：
- 新域名已购买，且**能修改其 NS 或 DNS 记录**
- Mailcow 后台管理员账号
- 服务器 SSH 访问

---

## 三、第 1 步：域名接入 Cloudflare

> **目的**：把域名的 DNS 管理权交给 Cloudflare，后续所有记录都在这里配。
>
> 如果新域名不打算用 Cloudflare（比如留在阿里云/腾讯云 DNS），可跳过本步，直接到第 2 步在你的 DNS 服务商处添加记录——记录类型和值完全一样，只是界面不同。

### 1.1 添加站点

1. 登录 https://dash.cloudflare.com
2. 进入账户首页，点 **Add a site**（添加站点）
3. 输入域名，如 `example.com`，**不要带 www 或 http://**
4. 选择套餐：拉到底选 **Free**（免费版完全够用，本项目不需要任何付费功能）
5. Cloudflare 会自动扫描现有 DNS 记录，扫描完点 **Continue**

### 1.2 修改域名的 NS 服务器

这一步在**域名注册商**处操作（不是 Cloudflare），比如你在阿里云/腾讯云/GoDaddy 买的域名就去哪里改。

Cloudflare 会给你两个 NS 地址，形如：

```
armfazh.ns.cloudflare.com
izabella.ns.cloudflare.com
```

到注册商后台，找到「DNS 服务器」「域名服务器」「Nameservers」之类的设置项，把原有的 NS **全部替换**成 Cloudflare 给的这两个。

> **注意**：是替换不是追加。保留旧 NS 会导致解析结果不确定。

### 1.3 等待生效

NS 变更全球生效通常 5 分钟到 2 小时，偶尔到 24 小时。Cloudflare 后台会显示状态：

- **Pending Nameserver Update** — 还没生效，等
- **Active** — 已生效，可以继续

命令行验证：

```bash
dig +short example.com NS
# 期望返回 cloudflare 的两个 NS
```

---

## 四、第 2 步：配置 MX 记录

> **目的**：告诉全世界的邮件服务器，发给 `@example.com` 的邮件应该投递到 `mail.3dbody.top`。这是整条链路的入口，不配则一切免谈。

### 2.1 清理冲突记录

进入 Cloudflare → 选择 `example.com` → 左侧菜单 **DNS** → **Records**。

**先删掉所有已存在的 MX 记录**。域名注册商常会预置一些指向自家邮箱服务的 MX，不删会导致邮件被投递到别处。

同时确认没有启用 **Email Routing**（左侧菜单 **Email**）。如果启用了，它会锁定并接管 MX 记录，必须先在 `Email → Email Routing → Settings` 里 **Disable Email Routing**。

### 2.2 添加 MX 记录

点 **Add record**，按下表填写：

| 字段 | 填什么 | 说明 |
|---|---|---|
| **Type** | `MX` | |
| **Name** | `@` | `@` 代表域名本身（`example.com`）。有些界面显示为空或域名全称，都是一个意思 |
| **Mail server** | `mail.3dbody.top` | **不要带末尾的点**，Cloudflare 会自动处理 |
| **Priority** | `10` | 只有一台邮件服务器时填多少都行，10 是惯例 |
| **TTL** | `Auto` | |

点 **Save**。

> **MX 记录没有代理开关**（橙云/灰云），因为 Cloudflare 代理只处理 HTTP/HTTPS 流量。如果你看到代理开关，说明记录类型选错了。

### 2.3 验证

```bash
dig +short example.com MX
# 期望：10 mail.3dbody.top.
```

Cloudflare 的 DNS 传播很快，通常 1 分钟内。如果查不到，等几分钟再试，或用 `dig @1.1.1.1 example.com MX` 直查 Cloudflare 的解析器。

---

## 五、第 3 步：Mailcow 添加域名

> **目的**：让 Postfix 知道这个域名归它管。没有这一步，即使 MX 指过来了，Postfix 也会以「这不是我的域」为由拒收（`554 5.7.1 relay access denied`）。

1. 浏览器打开 `https://mail.3dbody.top`，用管理员账号登录
2. 顶部菜单 **邮件设置**（E-Mail / Mail Setup）→ **域名**（Domains）
3. 点 **添加域名**（Add domain）
4. 填写：

| 字段 | 建议值 | 说明 |
|---|---|---|
| **域名** | `example.com` | 不带 @ 不带 www |
| **描述** | 随意，如 `Steam 备用域名 2` | 纯备注，方便日后辨认 |
| **邮箱数量上限** | `100` 或更高 | 每保存一个 Steam 账号就占一个名额，按预期规模设 |
| **域名配额上限** | 按需，如 `10240` MB | 该域名下所有邮箱可用总空间 |
| **默认邮箱配额** | `512` MB | 新建单个邮箱的默认大小，够存几千封验证码 |
| **别名数量上限** | 保持默认（400 或 4000） | catch-all 只占 1 个 |
| **激活** | ✅ 勾选 | 不勾等于建了不用 |

5. 保存

> **配额说明**：40G 硬盘要同时装 Mailcow 自身、系统、以及所有邮件。域名配额之和不要超过磁盘可用空间，否则磁盘满会导致 Postfix 停止收信、Dovecot 报错，排查起来很痛苦。留 30% 余量。

---

## 六、第 4 步：创建 catch-all 别名

> **目的**：这是整个方案的核心。`/generate` 生成的随机地址在 Mailcow 里并不存在，没有这条别名，发给它们的信会被退回「用户不存在」，验证码永远收不到。
>
> 配了之后，所有「查无此人」的地址统统兜进 `catchall@3dbody.top`。

1. Mailcow 后台 → **邮件设置**（E-Mail）→ **别名**（Aliases）
2. 点 **添加别名**（Add alias）
3. 填写：

| 字段 | 填什么 | 说明 |
|---|---|---|
| **别名地址** | `@example.com` | **就是这个格式：@ 开头，前面没有本地部分**。这是 catch-all 的专用写法 |
| **收件人** | `catchall@3dbody.top` | 跨域指向已有的兜底邮箱，不需要为新域名新建 |
| **激活** | ✅ 勾选 | |
| **静默丢弃** | ❌ 不要勾 | 勾了信会被扔掉 |

4. 保存

> **为什么收件人可以是别的域名**：Mailcow 的别名本质是 Postfix 的 virtual alias 映射，只要目标是本机管理的有效邮箱即可，不要求同域。这正是「所有域名共用一个兜底邮箱」的实现基础。

### 验证 catch-all 生效

用任意外部邮箱（QQ / 163 / Gmail）发一封信到一个**肯定不存在**的地址，比如：

```
zzz-test-20260805@example.com
```

然后用 `catchall@3dbody.top` 登录 `https://mail.3dbody.top` 的网页版邮箱，能看到这封信就说明成功。

**关键检查点**：打开这封信看它的 `收件人（To:）`，应该显示 `zzz-test-20260805@example.com` 而**不是** `catchall@3dbody.top`。读信接口正是靠这个头来区分邮件属于哪个随机地址的，如果被重写了，过滤逻辑会失效。

---

## 七、第 5 步：生成并配置 DKIM

> **目的**：DKIM 是域名的加密签名身份。本项目禁止账号外发邮件，所以 DKIM 对「发信」没有实际用途，但它防止别人**冒用你的域名**发垃圾邮件——域名一旦被列入黑名单，连收信都会受影响（不少邮件服务商会拒绝与黑名单域名交互）。
>
> 五个域名如果都不配 DKIM，很容易被判定为垃圾邮件农场。这一步不要省。

### 5.1 在 Mailcow 生成密钥

1. Mailcow 后台 → **系统**（System）→ **配置**（Configuration）→ **选项**（Options）
2. 找到 **ARC/DKIM 密钥**（ARC/DKIM keys）面板
3. 在生成区域填写：
   - **选择域名**：`example.com`
   - **DKIM 选择器（Selector）**：`dkim`（保持默认）
   - **密钥长度**：`2048` 位（推荐；1024 位偏弱，部分服务商已不认）
4. 点 **添加 ARC/DKIM 密钥**（Add）
5. 生成后列表里会出现该域名的记录，点它可以看到完整的 TXT 值，形如：

```
v=DKIM1;k=rsa;t=s;s=email;p=MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEA...（很长一串）
```

全部复制下来。

### 5.2 在 Cloudflare 添加 TXT 记录

Cloudflare → `example.com` → **DNS** → **Records** → **Add record**：

| 字段 | 填什么 |
|---|---|
| **Type** | `TXT` |
| **Name** | `dkim._domainkey` |
| **Content** | 粘贴上一步复制的完整字符串 |
| **TTL** | `Auto` |

> **Name 的坑**：只填 `dkim._domainkey`，**不要**写成 `dkim._domainkey.example.com`。Cloudflare 会自动补全域名后缀，手动加了会变成 `dkim._domainkey.example.com.example.com`，DKIM 验证必然失败。
>
> **Content 的坑**：那串值很长（2048 位密钥约 400 字符），复制时确保完整、首尾没有多余空格或换行。有些浏览器复制会插入换行符，粘贴后检查一下。

### 5.3 验证

```bash
dig +short TXT dkim._domainkey.example.com
```

应返回你刚才粘贴的内容。DNS 生效后回到 Mailcow 的 DKIM 面板，它会自动检测并显示状态（通常有个绿色标记表示 DNS 记录与本地密钥匹配）。

---

## 八、第 6 步：配置 SPF 和 DMARC

> **目的**：与 DKIM 同理，都是「防止域名被冒用」。SPF 声明哪些服务器有权代表这个域名发信，DMARC 告诉接收方「遇到验证失败的信该怎么办」。

### 6.1 SPF 记录

Cloudflare → **DNS** → **Add record**：

| 字段 | 填什么 |
|---|---|
| **Type** | `TXT` |
| **Name** | `@` |
| **Content** | `v=spf1 mx -all` |
| **TTL** | `Auto` |

含义拆解：
- `v=spf1` — SPF 版本标识
- `mx` — 授权该域名 MX 记录指向的服务器（即 `mail.3dbody.top`）发信
- `-all` — 其他任何服务器都**不允许**代表本域发信（硬失败）

> 用 `-all`（严格拒绝）而非 `~all`（软失败）。本项目的账号被禁止外发，正常情况下这个域名根本不会有合法外发邮件，越严格越好。

**注意**：一个域名只能有一条 SPF 记录。如果已存在 `v=spf1` 开头的 TXT 记录，是修改它而不是新增。

### 6.2 DMARC 记录

| 字段 | 填什么 |
|---|---|
| **Type** | `TXT` |
| **Name** | `_dmarc` |
| **Content** | `v=DMARC1; p=reject; sp=reject; adkim=s; aspf=s` |
| **TTL** | `Auto` |

含义拆解：
- `p=reject` — 验证失败的邮件直接拒收
- `sp=reject` — 子域名同样处理
- `adkim=s` / `aspf=s` — 严格对齐模式

同样，Name 只填 `_dmarc`，不要带域名后缀。

> 想收到验证失败报告的话可以加 `rua=mailto:catchall@3dbody.top`，但那会往兜底邮箱塞不少报告邮件，本项目场景建议不加。

### 6.3 验证

```bash
dig +short TXT example.com | grep spf
dig +short TXT _dmarc.example.com
```

---

## 九、第 7 步：更新后端配置

> **目的**：让 `/generate` 接口知道多了一个可用域名。前面所有步骤都是邮件基础设施，这一步才让应用真正用上它。

### 7.1 修改 `.env`

项目根目录的 `.env`（注意后端读的是 `../.env`，即项目根而非 `backend/` 下）：

```bash
# 单域名时
MAIL_DOMAIN=3dbody.top

# 改为多域名（逗号分隔，不要有空格）
MAIL_DOMAINS=3dbody.top,example.com,example2.com
```

> **前提**：多域名支持需要先改代码（`config.py` 的 `MAIL_DOMAIN` 单值改为 `MAIL_DOMAINS` 列表、`credentials.generate_email()` 随机选域名、保存接口增加域名白名单校验）。改动约 30 行。**如果尚未实施这项改造，本步骤无法生效**，届时 `.env` 里仍是单个 `MAIL_DOMAIN`。

### 7.2 重启后端

`.env` 通过 compose 的 `env_file` 注入，改完必须重建容器才生效：

```bash
docker compose up -d --force-recreate backend
```

### 7.3 验证

```bash
KEY="<你的 STEAM_API_KEY>"
for i in 1 2 3 4 5 6; do
  curl -s -H "X-API-Key: $KEY" -X POST http://localhost:8000/api/v1/steam-accounts/generate \
    | python3 -c "import json,sys; print(json.load(sys.stdin)['email'])"
done
```

连续生成 6 个地址，应该能看到各个域名混合出现。

---

## 十、第 8 步：端到端验证

把整条链路完整跑一遍，确认新域名真正可用。

### 10.1 生成地址

```bash
KEY="<你的 STEAM_API_KEY>"
curl -s -H "X-API-Key: $KEY" -X POST \
  "http://localhost:8000/api/v1/steam-accounts/generate?domain=example.com" | python3 -m json.tool
```

记下返回的 `email` 和 `email_password`。

### 10.2 发测试邮件

用外部邮箱（QQ / 163 / Gmail）发一封信到上一步的地址，正文里写一句：

```
Your verification code is K7X2M
```

> 内容太短或主题过于简单可能触发 rspamd 反垃圾拦截（返回 `554 5.7.1`）。写得像正常邮件一些，或参考「故障排查」一节调整 rspamd 阈值。

### 10.3 读取并确认解析

```bash
curl -s -H "X-API-Key: $KEY" \
  "http://localhost:8000/api/v1/steam-accounts/messages?email=<刚才的地址>&minutes=10" \
  | python3 -m json.tool
```

检查三项：
- `count` 为 1
- `data[0].text` 是邮件正文
- `data[0].extracted_code` 是 `K7X2M` ← 验证码提取正常工作

### 10.4 保存并验证隔离

```bash
curl -s -H "X-API-Key: $KEY" -H "Content-Type: application/json" -X POST \
  http://localhost:8000/api/v1/steam-accounts/ -d '{
    "email": "<地址>",
    "steam_username": "<用户名>",
    "email_password": "<邮箱密码>",
    "steam_password": "<Steam 密码>"
  }'
```

然后：
1. 再发一封新信到同一地址
2. 用**该邮箱地址和密码**登录 `https://mail.3dbody.top` 网页版
3. 确认能看到：新发的信 + 保存前那封被搬运过来的历史信
4. 用 `catchall@3dbody.top` 登录，确认新信**没有**进兜底邮箱

第 4 项是关键——它证明「精确匹配优先于 catch-all」在新域名上同样成立，买家的邮箱是真正隔离的。

---

## 十一、故障排查

### 发信方收到退信 `550 5.1.1 User unknown`

catch-all 别名没配好。检查别名地址是不是写成了 `@example.com`（@ 开头无本地部分），以及是否勾选了「激活」。

### 发信方收到 `554 5.7.1 relay access denied`

Mailcow 里没添加这个域名（第 3 步），Postfix 认为该域与自己无关。

### 发信方收到 `554 5.7.1 This message does not meet our delivery requirements`

rspamd 反垃圾拦截。对「只收验证码」的场景，漏收比收到垃圾危险得多，可以放宽：

```bash
sudo tee /opt/mailcow-dockerized/data/conf/rspamd/local.d/actions.conf <<'EOF'
reject = 999;
add_header = 6;
greylist = 4;
EOF
sudo docker compose -f /opt/mailcow-dockerized/docker-compose.yml restart rspamd-mailcow
```

改完垃圾邮件仍会被打标记投递，但不再被拒收。代价是兜底邮箱堆积更多垃圾——`cleanup_catchall.py` 会定期清理，且读信接口按收件人精确过滤，垃圾信不会混进结果。

查看具体拦截原因：

```bash
sudo docker compose -f /opt/mailcow-dockerized/docker-compose.yml logs rspamd-mailcow --tail 100 | grep -i reject
```

### 信收到了，但读信接口返回空

优先检查邮件的 `To:` 头。用兜底邮箱登录网页版，打开那封信查看原始头部，确认 `To:` 是原始随机地址而非 `catchall@`。如果被重写了，说明别名配成了「转发（Forwarding）」而非「别名（Alias）」——两者在 Mailcow 里是不同功能，转发会重写信封收件人。

### DKIM 状态一直不匹配

1. `dig +short TXT dkim._domainkey.example.com` 确认记录已生效
2. 检查 Name 是否误写成 `dkim._domainkey.example.com`（会变成双重域名）
3. 检查 Content 是否完整——2048 位密钥很长，容易复制不全
4. Cloudflare 对超长 TXT 会自动分片存储，这是正常的，不影响验证

### 邮件收得到但很慢（延迟几分钟）

可能触发了 greylisting（灰名单）——首次投递故意拒绝，要求发信方重试。这是正常的反垃圾机制。想关闭：

```bash
# 在 rspamd 的 actions.conf 里把 greylist 阈值抬高
greylist = 999;
```

---

## 十二、DNS 记录速查表

每个新域名需要在 DNS 添加的全部记录：

| Type | Name | Content | 目的 | 必需 |
|---|---|---|---|---|
| MX | `@` | `mail.3dbody.top`（优先级 10） | 邮件路由入口 | ✅ 必需 |
| TXT | `@` | `v=spf1 mx -all` | 防域名冒用 | 强烈建议 |
| TXT | `dkim._domainkey` | Mailcow 生成的长串 | 防域名冒用 | 强烈建议 |
| TXT | `_dmarc` | `v=DMARC1; p=reject; sp=reject; adkim=s; aspf=s` | 防域名冒用 | 强烈建议 |
| A | — | — | **不需要**，MX 指向已有主机 | ❌ |

一次性验证全部：

```bash
D=example.com
echo "MX:    $(dig +short $D MX)"
echo "SPF:   $(dig +short TXT $D | grep spf)"
echo "DKIM:  $(dig +short TXT dkim._domainkey.$D | head -c 60)..."
echo "DMARC: $(dig +short TXT _dmarc.$D)"
```

---

## 十三、Mailcow 后台操作速查

| 操作 | 路径 |
|---|---|
| 添加域名 | 邮件设置 → 域名 → 添加域名 |
| 添加 catch-all 别名 | 邮件设置 → 别名 → 添加别名（地址填 `@域名`） |
| 生成 DKIM | 系统 → 配置 → 选项 → ARC/DKIM 密钥 |
| 查看/删除邮箱 | 邮件设置 → 邮箱 |
| 查看被封 IP | 系统 → 配置 → Fail2ban |
| 生成 API Key | 系统 → 配置 → 访问权限 → API |

> 后台菜单名称随 Mailcow 版本可能微调，按层级和关键词找即可。

---

## 十四、多域名的成本与限制

**磁盘**：所有域名共用服务器的 40G。域名配额之和留 30% 余量，磁盘满会导致 Postfix 停止收信。定期检查：

```bash
df -h /
sudo docker compose -f /opt/mailcow-dockerized/docker-compose.yml exec -T mysql-mailcow \
  mysql -u mailcow -p"$(sudo grep '^DBPASS=' /opt/mailcow-dockerized/mailcow.conf | cut -d= -f2)" \
  mailcow -e "SELECT domain, SUM(quota)/1024/1024 AS quota_mb FROM mailbox GROUP BY domain;"
```

**内存**：加域名本身几乎不增加内存开销——Postfix/Dovecot 不为每个域名起独立进程。4G 机器加到十几个域名也无压力。

**兜底邮箱增长**：域名越多，未保存地址收到的信越多，全部堆在 `catchall@3dbody.top`。务必启用清理任务：

```cron
0 4 * * * cd /path/to/project && docker compose exec -T backend python app/cleanup_catchall.py
```

保留天数由 `.env` 的 `CATCHALL_RETENTION_DAYS` 控制（默认 14 天）。

**IP 声誉是共享的**：所有域名共用同一个出口 IP（`111.231.55.54`）。任何一个域名被滥用导致 IP 进黑名单，**所有域名一起受影响**。这是保持 `MAILBOX_DISABLE_OUTBOUND=True`（禁止账号外发）的根本原因——转让出去的账号一旦被拿去发垃圾邮件，整套基础设施都会遭殃。
