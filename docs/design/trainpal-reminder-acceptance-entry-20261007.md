# TrainPal 独立本地验收入口

日期：2026-10-07。关联 #23/#37、Goal v2、ADR0081。
固定审查起点：`2f2dc4d1e9d63cc01313da34d7d3e4612838982c`。
用户已确认本批只审查公开应用工厂→HTTP接口和启动配置，不重审声音/复制实现。
本文是本批运行合同与证据记录，不代替全 Goal 质量与真机验收。

## 本批合同

- 新入口 `hakimi_analysis.acceptance`；独立启动，监听 `127.0.0.1:15177`，不改
  `bootstrap.build_default_app`、`main.py`、`dev.py`、正式配置或演示15176。
- 默认服务本 checkout 的 `apps/web/dist`，同源前端/API；启动前必须存在构建产物。
  不沿用其他环境的 `WEB_STATIC_ROOT`。显式工厂输入可提供独立测试目录。
- 只接受 `APP_ENV=development`、`ANALYSIS_PROVIDER=cloud`；拒绝生产与测试假分析
  profile。启动/打开页面不调用模型，分析仍由明确点击触发，视频上限仍300秒。
- 复用既有分析编排、媒体处理、ASR/VLM、语义融合、文字/语音选段和 GYMTI 服务。
  提醒接口显式注入既有 `TrainingReminderPlanner`，不是新增 Agent 或训练状态机。
- ASR/VLM/文本、选段与可选 GYMTI 模型调用不自动重试。无提醒模型时明确规则安排，
  缺分析凭据时返回 `configuration_error`，不生成合成候选作为降级成功。
- 既有普通分析链路全部证据不可用时，可能终态为 `completed`，但覆盖必须明确
  `insufficient`、`insufficient_evidence` 和可重试缺口；这不是成功识别。
- 私密临时材料使用独立 `tmp/reminder-acceptance`；沿用终态清理和脱敏清理接口。
  服务关闭回收分析任务与 HTTP 客户端。没有新增永久任务、原文记录或模型日志。
- 保留安全 cookie、原有来源与版本校验。同源外的开发5173也不能调用本入口。
  评委槽位为0，公共容量沿用明确配置，不加入请求次数配额。

这里只接已有常规云分析链路与提醒编排。尚未把原句编号/视觉上下文实验路线启用
到应用：Goal 要求先依据真实质量决定接入，不能仅因入口工程通过就开启试点或
声称重复/提醒归属问题已解决。后续真实质量复评保留受控实验路线与独立配置。

## 本机运行

2026-10-11 本地恢复补充：开发环境的受控视频预览直接读取已经通过清单、路径、
哈希及时长校验的本机缓存，支持浏览器 Range 播放，不跳转到占位 CDN 地址。
生产环境仍使用受控 CDN 跳转；未知来源仍返回404。此调整不增加来源或分析调用。

从本 checkout 根目录使用现有、被忽略的 `.env.local` 凭据，不复制到前端或报告。

```powershell
pnpm install --frozen-lockfile
uv sync --locked --project services/analysis-api
pnpm acceptance
```

打开 `http://localhost:15177/`。启动命令先构建当前前端，再启动独立 API。
若15177已被使用，先核对占用进程，不更换成15176或杀死未知进程。
结束时在启动终端按 Ctrl+C，确认15177没有遗留监听者。

这是 loopback 本机入口，不是手机入口或部署。不能通过把 host 改为 `0.0.0.0`
绕过待确认的网络开放、访问保护与证书边界。小米15私密HTTPS入口仍需单独确认；
真实可听提醒与真人语音选段验收仍未通过。

## 测试证据与失败尝试

专项18项通过：明确请求才编排、绑定模型ID、300秒能力、配置拒绝、同源SPA/API、
无模型规则标识、真实合成媒体导入、缺凭据明确失败、终态后再次导入及清理、
ASR WebSocket/VLM HTTP各一次无重试、既有文字选段和本地GYMTI、启动预算及缺构建拒绝。
只替换外部 Provider 网络；媒体处理、应用、HTTP、模型适配器与编排器为实际代码。
测试前端使用临时合成文件，不依赖未提交的 `dist`，干净环境可运行专项。

红→绿：入口不存在；错误 profile 未拒绝；SPA未接通；公共分析默认0槽造成429；
正常分析适配器503自动重试3次；未接选段服务；缺构建未拒绝。先有公开边界失败，
再最小实现。没有放宽来源、确认、数量或训练执行校验。
测试错误预期也分开修正：普通链路证据失败是 `insufficient` 而非异常终态；
GYMTI计分版本与问卷版本相同，来源标记为 `local_fallback`。
HTTPX不会为HTTP发送安全cookie，导入API测试采用内存ASGI HTTPS，不关闭安全cookie。

实际 headless Chromium 使用无凭据的本入口工厂、本 checkout 构建前端和4秒合成
视频完成选片→明确提交→读取自己场次终态；安全cookie保留，公开GET不返回404，
终态 `configuration_error`、0候选、无页面脚本异常。不是收费模型、桌面人耳、真实
视频解码/质量或完整训练旅程验收。Python Playwright不可用，采用项目已有Node库。

Skill辅助启动器在Windows只停止shell，第一次遗留自建子进程；核对命令与PID后
关闭，不影响15176。后续使用可控终端，Ctrl+C观察正常关闭，15177已无监听。
未把辅助脚本“已停止”输出当作清理证据。

首轮完整检查在lint停止：临时Node浏览器检查脚本虽被Git忽略，仍被前端ESLint扫描。
已仅移除本批自建的两个临时启动/浏览器脚本，再运行检查；未放宽lint规则。

独立 Standards 审查找到P2：空值/纯空白凭据被误认为已配置。公开HTTP测试先复现
缺 `configuration_error`，再由本入口在构造客户端前复制配置、将空凭据归一为None；
覆盖双项空值和单项缺失、清理、再次导入、提醒规则降级及零外部HTTP请求。
原配置对象与默认工厂没有修改。复审 Standards/Spec 均0项。

首轮完整E2E为49/50，失败来自动作编辑弹层的Axe颜色扫描时机；专项再跑复现。
最小真实CSS动画探针将200ms淡入固定在50ms（opacity 0.408511）时出现颜色违规，
等待实际动画完成（opacity 1）后零违规。只让原扫描等待真实animation.finished，
没有固定sleep、禁动画、修改配色或放宽标准；探针及DEBUG日志已移除。
随后三次重复旅程出现一次第二个测试时序缺口：点击新建后未等跳转，就检查
零卡片；训练首页原本也无该卡片，可能提前离开导致未完成新建，进入原方案复练。
改为先等待 `/plan` 再检查空方案，不改变请求次数及新ID断言。修复后二者合并的
完整五要点/复制/解码画面/训练记录复练旅程连续3次通过（1.2分钟）；全部内容仍为
合成HTTP fixture，不代表真实模型或真人听取通过。

修复后完整 `pnpm check` 通过：前端50文件/348项，后端1659项，lint/typecheck/build
均通过。`pnpm api:generate` 成功，OpenAPI与前端类型无内容差异，SHA256分别为
`C1E5F4284C91425B0C98DBA892F09D00A2BDD2557EF07777FFFC09DF9F5F135C`、
`BA5AA7C435E1FFBFDB87B4A0780BFE9DD03D1DFCED3BD5A68B09951CD6600128`。
完整E2E最终复跑50项全通过（3.1分钟）；最终独立Standards/Spec复审各0项，
审查包括空凭据修复和两处验收等待，不重审既有声音/复制实现。`git diff --check`
通过。本批所有测试进程已结束；15177没有遗留监听，演示15176未操作。
没有收费调用、真实媒体上传、推送/PR/合并/部署或网络开放。

## 已提交源码快照的复现（2026-10-07）

从 `eb9ef3c1e6b26d4623d2ba72a0d3393edb6dcfe6` 用 `git archive` 导出跟踪文件，
在本 checkout 的独立 `tmp/clean-acceptance-20261007/source` 安装和运行。开始时
没有 `node_modules`、前端 `dist`、Python `.venv` 或 `.env.local`，没有复制密钥、
用户视频或实验原文；依赖下载复用了本机包缓存，不是空缓存或新机器网络验收。
源码 ZIP 的 SHA256 为
`CE74F9030014878605796DA5410DEF45A2144520B0A7025409BAFEE14805B25A`。

实际使用 pnpm 11.19.0（符合仓库11.x范围，但不是CI固定的11.9.0）、Python 3.12.10、
uv 0.11.7。PATH中Node为24.14.1，pnpm启动包装器使用的独立Node为24.19.0；
两者均在仓库24.x范围，不能将本次记录说成唯一固定Node版本的复现。
`pnpm install --frozen-lockfile` 与 `uv sync --locked`
成功，前者337包复用缓存、无下载。pnpm更新元数据查询报告fetch失败，但锁定安装
退出成功；没有据此升级工具或改锁文件。锁文件SHA256：

- `pnpm-lock.yaml`：`E5F2F91D941D081E6797D0E7504789BF7CD6591BA8B1808C5C51EF3E89ABA928`。
- `services/analysis-api/uv.lock`：`26C57DFB152D6230888EB9AC66329AED131387CBCB1B960AECC938F31CCD40C8`。

在新依赖目录运行入口专项18项及 `pnpm build` 成功。以空分析凭据、关闭可选GYMTI
模型的进程环境，启动实际 `python -m hakimi_analysis.acceptance`；本机 `/`、
`/train`、health、capabilities 与合成提醒请求均收到HTTP200。没有提交真实视频或
调用收费Provider。Ctrl+C观察应用正常关闭，随后15177无监听。

准备浏览器与合成媒体后，五要点保存/复制/原生解码画面/记录复练的现有浏览器
旅程在源码快照通过1项（20.2秒）。复现命令为：

```powershell
pnpm --filter @hachimi/web exec playwright install chromium
pnpm e2e:prepare
pnpm --filter @hachimi/web exec playwright test tests/e2e/analysis-sources.spec.ts -g "five source tips"
```

浏览器安装使用已有本机浏览器缓存；未声称冷下载已验证。此旅程使用原测试专用
入口和合成HTTP fixture，不是独立验收入口的真实AI完整旅程，不是人耳听取。
测试结束后15173/18123均无监听，15176未操作。

源码快照 `pnpm api:generate` 成功。最初严格字节对比失败；与源码ZIP原始文本
逐文件比较后，OpenAPI完全一致，前端类型仅CRLF改为LF（1497处），归一化文本
完全一致。与本工作树合同的Git无内容差异，生成后散列仍为上文两项已记录值。
不把换行差异称为接口漂移，也没有修改合同、生成器或校验规则。

本节仅补上干净源码/新依赖目录的工程复现证据。真实分析质量、同版重复复评、
真实视频完整旅程、小米实际可听提醒与真人语音选段仍未通过全目标门槛。
