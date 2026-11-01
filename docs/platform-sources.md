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

`auto` 语义：探活 `GET /` 成功且版本兼容 → sidecar；否则 browser。sidecar 返回 4xx/5xx 时**不自动降级**（避免重复枚举），直接把错误抛给上层。

### 3.3 小红书

| 能力 | 实现 | 说明 | 登录 | 降级 |
| --- | --- | --- | --- | --- |
| 分享链接 | 自研解析 | `xhslink.com` → `note_id` / `user_id` | 否 | — |
| 笔记详情 | Spider_XHS 签名层 | `x-s / x-t / x-s-common` | 是（游客态亦可） | 浏览器拦截 |
| 用户主页 / 收藏夹 | Spider_XHS | 发布 / 喜欢 / 收藏 | 是 | 浏览器拦截 |
| 图文 / 视频下载 | 自研 | 图集按序落盘，视频走 CDN | — | — |
| 创作者中心 | 浏览器 XHR | `creator.xiaohongshu.com/api/*` | 是 | — |

### 3.4 创作者中心（Insights）

三平台统一实现：

```text
打开已登录的持久 profile → 落到后台首页 → 拦截目标 XHR → 归一化为 InsightsSnapshot
```

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
| `XHS_SOURCE` | `sidecar` | ⏳ 计划 | `sidecar` / `browser` |
| `XHS_SIDECAR_URL` | `http://127.0.0.1:8001` | ⏳ 计划 | 小红书 sidecar 地址 |
| `INSIGHTS_ENABLED` | `0` | ⏳ 计划 | 创作者中心采集开关（默认关，避免误触后台） |

## 6. 里程碑与验收

| 里程碑 | 内容 | 验收标准 |
| --- | --- | --- |
| **M1** | B 站收藏夹（自研 3 GET） | 离线 fixture 单测；对公开收藏夹真实拉取 ≥1 页；失效条目被过滤 |
| M2 | 抖音 sidecar（收藏夹/合集） | 假 sidecar 服务单测；`auto` 探测与降级各一条用例；真实抽检 |
| M3 | 小红书分享链接 + 收藏夹 | 短链解析单测；签名层 smoke |
| M4 | 创作者中心（三平台） | 拦截契约单测；`INSIGHTS_ENABLED=0` 时不发请求 |

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

