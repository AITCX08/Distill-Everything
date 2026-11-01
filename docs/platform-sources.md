# 多平台数据获取方案：决策记录与开发规范

> 本文是多平台采集的**唯一决策依据**。新增平台 / 新增能力前先读这里；实现与文档不一致时，以本文档的契约章节为准，并同步更新本文。

## 0. 决策记录

| # | 决策点 | 结论 | 理由 |
| --- | --- | --- | --- |
| 1 | 抖音公开采集 | **Evil0ctal sidecar**（`DOUYIN_SOURCE=sidecar`），浏览器拦截保留为兜底 | 依赖隔离，不与主项目的 `pydantic/fastapi` 版本打架；sidecar 自带签名，升级互不影响 |
| 2 | B 站三方库 | **冻结 `bilibili-api==17.4.2` + 自研兜底** | 上游仓库已关停，不能再指望它跟进接口；先把收藏夹等新增能力做成自研 |
| 3 | 小红书 | **Spider_XHS**（PC 端 + 创作者平台签名） | 目前唯一覆盖创作者平台签名的活跃实现 |
| 4 | 创作者中心 | **统一用已登录浏览器抓 XHR** | 三平台的后台接口都是私密 + 弱签名，浏览器是唯一低成本高覆盖路径 |
| 5 | 落地节奏 | **先抖音收藏夹 + B 站收藏夹**，再小红书，最后创作者中心 | 前两项缺口最大、可离线验证、风险最低 |

## 1. 四层架构

每个平台实现同一组协议，流水线只认协议、不认平台：

```text
Auth     登录态：扫码 / cookie / 持久 profile        → AuthStatus
Acquire  解析目标 → 枚举作品（分享链接/收藏夹/创作者） → ResolvedTarget + EnumerationPage
Fetch    下载原始媒体（视频 / 音频 / 图文）           → DownloadedAssets
Insights 创作者中心自有数据（播放/互动/粉丝）          → InsightsSnapshot
```

约束：

- `Acquire` 不得下载，`Fetch` 不得枚举；两者都不得调用 ASR/LLM。
- `Insights` **只允许**返回自己账号的聚合数据，不得用于抓他人后台。
- 任何一层失败都抛出带平台前缀的错误，由 adapter 决定降级路径（见各平台章节）。

## 2. 目标模型

`ResolvedTarget.target_type` 取值：

| 取值 | 含义 | 例 |
| --- | --- | --- |
| `creator` | 创作者主页 | `space.bilibili.com/2267573`、`youtube.com/@handle` |
| `video` | 单个视频 | `BV1xx`、`douyin.com/video/123`、`youtu.be/xx` |
| `note` | 图文笔记（小红书） | `xiaohongshu.com/explore/xxx` |
| `collection` | 收藏夹 / 合集 / 收藏 | `.../favlist?fid=123`、`douyin.com/collection/123` |
| `insights` | 创作者中心（仅自己） | `insights:bilibili`、`insights:douyin`、`insights:xhs` |

解析优先级：显式前缀（`douyin:collection:123`）> URL 形态 > 短链跳转。

## 3. 平台实现

### 3.1 Bilibili

| 能力 | 实现 | 接口 / 方式 | 登录 | 降级 |
| --- | --- | --- | --- | --- |
| 分享链接、单视频 | 现有 adapter（b23/BV/av） | `x/web-interface/view` | 否 | — |
| 创作者作品 | `bilibili-api`（冻结版） | `x/space/wbi/arc/search`（WBI） | 是 | 自研 HTTP + WBI |
| **收藏夹** | **自研 3 个 GET** | 见下 | 公开夹否，私密夹是 | 无（报明确错误） |
| 视频下载 | `yt-dlp` | — | 跟随凭据 | `BBDown` 子进程（番剧/多P） |
| 创作者中心 | 浏览器 XHR | `member.bilibili.com/x/web/data/*` | 是（cookie + csrf） | — |

收藏夹接口（已对线上验证，**无 WBI、无登录**）：

```text
GET https://api.bilibili.com/x/v3/fav/folder/info?media_id={fid}                 → title / media_count / upper.name
GET https://api.bilibili.com/x/v3/fav/resource/ids?media_id={fid}                → 全部 bvid（一次拿完）
GET https://api.bilibili.com/x/v3/fav/resource/list?media_id={fid}&pn={pn}&ps=20&platform=web
                                                                                  → medias[] + has_more
```

实现要点：

1. **失效条目过滤**：`attr == 1` 表示已失效（标题被替换为「已失效视频」），必须过滤，`type` 字段不可靠。
2. `ps` 上限 20；`has_more` 驱动 checkpoint。
3. 私密收藏夹：`folder/info` 返回非 0 `code` 时，用 `data/.credentials.json` 的 SESSDATA 重试一次；仍失败则报「无权访问该收藏夹」。
4. 标题优先取收藏夹里的人工标题（`medias[].title`），而不是视频原标题。

### 3.2 抖音

| 能力 | 实现 | 说明 | 登录 | 降级 |
| --- | --- | --- | --- | --- |
| 分享链接 | 现有 adapter | `v.douyin.com` 短链跟随 | 否 | — |
| 创作者作品 | **sidecar 优先** | Evil0ctal 服务 `/api/douyin/web/fetch_user_post_videos` | 是 | 浏览器拦截 `/aweme/post/` |
| **收藏夹 / 合集** | **sidecar** | 收藏、收藏夹、合集走签名接口 | 是 | 浏览器拦截（端点见 `_COLLECTION_API_MARKERS`） |
| 视频 / 图文下载 | 现有 `DouyinDownloader` | `requests` 直连 CDN | — | — |
| 创作者中心 | 浏览器 XHR | `creator.douyin.com/aweme/v1/creator/*` | 是 | — |

配置：

```dotenv
DOUYIN_SOURCE=auto          # auto | sidecar | browser
DOUYIN_SIDECAR_URL=http://127.0.0.1:8000
DOUYIN_SIDECAR_TIMEOUT=30
```

**登录态是两套，必须桥接一次**：扫码只写进 Playwright profile，sidecar 是独立进程，读的是
`crawlers/douyin/web/config.yaml` 里的 `headers.Cookie`。扫码后执行：

```powershell
python main.py source login douyin          # 扫码
python main.py source sync-cookie           # 把浏览器 cookie 写进 sidecar 配置
.\scripts\start-douyin-sidecar.ps1          # 重启 sidecar 生效
```

没有 `sessionid` 时 `sync-cookie` 会直接报错，不会写入半截 cookie。

> 状态判定：`sidecar 可达` **不等于** `已登录`。平台卡片的「已登录」只在 sidecar 配置里
> 存在真实 `sessionid` 时才成立；仅端口通会显示「sidecar 已连接但尚未登录」。

`auto` 语义：探活 `GET /openapi.json` 成功 → sidecar；否则 browser。**不需要 Docker**：sidecar 是普通 Python 应用，可用独立 venv 或 WSL2 运行；本机已有 Chrome/Edge 时，浏览器模式无需下载 Playwright Chromium（`launch_persistent_context` 会按 `bundled → chrome → msedge` 顺序回退）。

#### 本机运行（无 Docker，已验证）

v5 需要 PostgreSQL + Redis，本机单用户不划算；用 **V4.1.2**（纯 Python 单进程）即可，本项目的客户端端点默认就是按它写的。

```powershell
# 1) 克隆（放到 .local-artifacts，已被 git 忽略）
git clone --depth 1 https://github.com/Evil0ctal/Douyin_TikTok_Download_API.git .local-artifacts/douyin-sidecar
cd .local-artifacts/douyin-sidecar
git fetch --depth 1 origin tag V4.1.2; git checkout V4.1.2

# 2) 依赖装进项目自己的 conda 环境（Distill-Everything，Python 3.12）
pip install aiofiles browser-cookie3 lz4 importlib_resources pyfiglet pypng `
            pywebio pywebio-battery tornado ua-parser user-agents gmssl tenacity

# 3) httpx>=0.28 已移除 proxies= 参数，V4 代码需要去掉该参数（3 个文件、9 处）
#    见 .local-artifacts/patch_sidecar_httpx.py 的做法

# 4) 启动
.\scripts\start-douyin-sidecar.ps1 -Port 8000
# 或：python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

验证：`GET http://127.0.0.1:8000/openapi.json` 返回 200，且包含
`/api/douyin/web/fetch_user_post_videos`、`fetch_user_collection_videos`、`fetch_user_mix_videos`、`fetch_one_video`。

> 依赖不要按 V4 的 `requirements.txt` 钉版本安装：它会把 `fastapi` 降到 0.110、`pydantic` 降到 2.7，
> 与主项目的 `fastapi>=0.116,<0.117` 冲突。按上面的清单安装未钉版本的包即可。sidecar 返回 4xx/5xx 时**不自动降级**（避免重复枚举），直接把错误抛给上层。

### 3.3 小红书

| 能力 | 实现 | 说明 | 登录 | 降级 |
| --- | --- | --- | --- | --- |
| 分享链接 | 自研解析 | `xhslink.com` → `note_id` / `user_id` | 否 | — |
| 笔记详情 | Spider_XHS 签名层 | `x-s / x-t / x-s-common` | 是（游客态亦可） | 浏览器拦截 |
| 用户主页 / 收藏夹 | Spider_XHS | 发布 / 喜欢 / 收藏 | 是 | 浏览器拦截 |
| 图文 / 视频下载 | 自研 | 图集按序落盘，视频走 CDN | — | — |
| 创作者中心 | 浏览器 XHR | `creator.xiaohongshu.com/api/*` | 是 | — |

小红书 sidecar 契约（Spider_XHS 是库不是服务，需要一层薄封装；本项目的客户端只认这 3 个路径）：

```text
GET /openapi.json                     探活
GET /xhs/note?note_id={id}            → {note_id, title, desc, video_url, images[], cover, author{nickname}, liked, url}
GET /xhs/user/notes?user_id=&cursor=  → {items[], cursor, has_more, total}
GET /xhs/user/collect?user_id=&cursor= → 同上
```

对应 Spider_XHS 的真实端点：`/api/sns/web/v1/feed`、`/api/sns/web/v1/user_posted`、
`/api/sns/web/v2/note/collect/page`。路径可用 `paths=` 覆盖，v5 改版时只改 client 构造参数。

### 3.4 创作者中心（Insights）

三平台统一实现：

```text
打开已登录的持久 profile → 落到后台首页 → 拦截目标 XHR → 归一化为 InsightsSnapshot
```

实现落点：

| 平台 | 页面 | 拦截标记（可扩充） | 归一化函数 |
| --- | --- | --- | --- |
| Bilibili | `member.bilibili.com/platform/home` | `/x/web/data/`、`/x/web/archive/`、`member.bilibili.com/x/` | `normalize_bilibili` |
| 抖音 | `creator.douyin.com/creator-micro/home` | `/aweme/v1/creator/`、`/creator/data/` | `normalize_douyin` |
| 小红书 | `creator.xiaohongshu.com/creator/home` | `creator.xiaohongshu.com/api/`、`/api/galaxy/` | `normalize_xiaohongshu` |

入口：`INSIGHTS_ENABLED=1 python main.py insights --platform bilibili [--json out.json]`。
默认关闭；关闭时命令直接报错，不会启动浏览器。profile 复用各平台登录目录
（抖音用 `DOUYIN_PROFILE_DIR`，其余用 `data/browser/<platform>`）。

> 标记与字段名来自各平台后台的公开路由，**真机校准后**把结果追加到 §9 验证记录；
> 未命中的平台会明确报 `InsightsUnavailable`，不会产出半截数据。

`InsightsSnapshot` 契约：

```json
{
  "platform": "bilibili|douyin|xhs",
  "captured_at": "ISO8601",
  "account": {"id": "...", "name": "..."},
  "metrics": {"views": 0, "likes": 0, "comments": 0, "shares": 0, "followers": 0, "followers_delta": 0},
  "items": [{"item_id": "...", "title": "...", "views": 0, "likes": 0, "comments": 0, "published_at": "ISO8601"}],
  "source": {"endpoint": "...", "captured_by": "browser-xhr"}
}
```

约束：字段缺失填 `null` 而不是 0（避免把「没拿到」误报成「0 播放」）。

## 4. 数据契约映射

| 统一字段 | Bilibili | 抖音 | 小红书 |
| --- | --- | --- | --- |
| `item_id` | `bvid` | `aweme_id` | `note_id` |
| `title` | 收藏夹人工标题 / 视频标题 | `desc` | `title` / 首行 |
| `duration_seconds` | `duration` | `video.duration/1000` | 视频笔记才有 |
| `published_at` | `pubtime` | `create_time` | `time` |
| `item_type` | 恒 `video` | `video` / `gallery` | `video` / `note` |
| `assets` | 音频（yt-dlp 产出） | 视频/图集 CDN | 图集 / 视频 CDN |

## 5. 配置项

| 变量 | 默认 | 状态 | 作用 |
| --- | --- | --- | --- |
| `DOUYIN_SOURCE` | `auto` | ✅ 已实现 | `auto` / `sidecar` / `browser` |
| `DOUYIN_SIDECAR_URL` | `http://127.0.0.1:8000` | ✅ 已实现 | sidecar 地址 |
| `DOUYIN_SIDECAR_TIMEOUT` | `30` | ✅ 已实现 | 单请求超时（秒） |
| `BILIBILI_FETCH_FAV` | `1` | ⏳ 计划 | 关闭时收藏夹能力直接报未启用 |
| `XHS_SOURCE` | `sidecar` | ✅ 已实现 | `sidecar` / `off` |
| `XHS_SIDECAR_URL` | `http://127.0.0.1:8001` | ✅ 已实现 | 小红书 sidecar 地址 |
| `XHS_SIDECAR_TIMEOUT` | `30` | ✅ 已实现 | 小红书 sidecar 超时（秒） |
| `INSIGHTS_ENABLED` | `0` | ✅ 已实现 | 创作者中心采集开关（默认关，避免误触后台） |

## 6. 里程碑与验收

| 里程碑 | 内容 | 验收标准 |
| --- | --- | --- |
| **M1** | B 站收藏夹（自研 3 GET） | 离线 fixture 单测；对公开收藏夹真实拉取 ≥1 页；失效条目被过滤 |
| M2 | 抖音 sidecar（收藏夹/合集） | 假 sidecar 服务单测；`auto` 探测与降级各一条用例；真实抽检 |
| M3 | 小红书分享链接 + 收藏夹 | 短链解析单测；签名层 smoke |
| **M4** | 创作者中心（三平台） | ✅ 拦截契约单测 + CLI 开关用例；`INSIGHTS_ENABLED=0` 时不启动浏览器 |

**完成定义**：`ruff` 通过、全量 pytest 通过、CI 三平台绿、至少一条真实链路抽检记录写进本文档「验证记录」。

## 7. 测试策略

- 单元测试**不联网**：所有平台响应落成 `tests/fixtures/<platform>/<capability>_*.json`。
- 真实链路抽检用独立脚本（`.local-artifacts/check_*.py`），结果只记结论，不把用户数据写进仓库。
- 失效条目、空列表、`has_more=false`、403/404 必须各有一条用例。

## 8. 风险与观测

| 风险 | 观测手段 | 处置 |
| --- | --- | --- |
| 接口路径变更 | 每平台 `code != 0` 计数 + 首错日志 | 按日志里的 endpoint 改常量 |
| 签名失效（抖音/小红书） | sidecar 5xx 比例 | 升级 sidecar，不改进主进程 |
| 风控限流 | 429 / `-352` 计数 | 退避 + 降低并发 |
| 登录态过期 | `auth_status` 变化 | Dashboard 页内重扫码 |

## 9. 验证记录

| 日期 | 平台 | 能力 | 结论 | 证据 |
| --- | --- | --- | --- | --- |
| 2026-10-31 | Bilibili | 收藏夹枚举 | 公开收藏夹 307 条，过滤失效后 247 条，`complete=True` | `python main.py source creator "bilibili:fav:63692673" --dry-run` |
| 2026-10-31 | 抖音 | sidecar 路由 | 单元级：posts / collection / mix 三条路径与浏览器回退各有用例 | `tests/platforms/douyin/test_sidecar.py` |
| 2026-10-31 | 小红书 | 目标解析与采集契约 | 单元级：短链/笔记/用户/收藏 21 例；真实链路待 sidecar 启动 | `tests/platforms/xhs/` |
| 2026-10-31 | 三平台 | 创作者中心 | 单元级：归一化 + 拦截 + 开关 6 例；真机校准待补 | `tests/platforms/test_insights.py` |

