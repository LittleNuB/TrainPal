# TrainPal

> 把“想练但还没开始”的健身视频，变成一场可以确认、调整并完成的训练。

TrainPal 是一个以本地视频为入口的 Web-first 训练伙伴。用户从当前设备选择自己有权使用的健身视频；系统提取可核对的动作候选与来源片段，将它们编排为训练方案，并由同一位猫教练陪伴完成训练与回顾。

它不把健身内容伪装成已接入的短视频信息流，也不把模型结果当作不可质疑的答案。每一项训练都保留人的确认权。

## 为什么做它

收藏健身视频很容易，真正开始训练很难。中间缺的不是更多内容，而是把内容转成“我今天能完成什么”的桥梁。

TrainPal 的设计重点是这条转化链路：

```text
本地健身视频 → 动作候选与来源证据 → 可编辑训练方案 → 训练执行 → 本机记录与回顾
```

| 体验原则 | 产品上的体现 |
| --- | --- |
| 用户带来内容 | 仅选择当前设备上的本地视频；不抓取任意链接、不复用平台登录态。 |
| AI 提供依据，不替人决定 | 动作候选保留出处，可在训练前编辑、删除或补充。 |
| 训练不是一次性输出 | 方案、训练进度与记录在同一设备上延续。 |
| 真实能力如实呈现 | 后端不可用时，首页仍展示完整产品路径；不会假装分析已经完成。 |

## 体验地图

1. **选择视频**：导入一条自己有权使用的健身视频，先预览、再明确点击分析。
2. **理解动作**：分析任务展示真实阶段、进度、覆盖缺口和可恢复状态；失败不会被说成“没有识别到动作”。
3. **确认方案**：按动作顺序组织训练，可调整参数、顺序或添加不依赖视频的动作。
4. **进入训练**：次数型与时长型动作都可执行；离开页面或刷新后可在同一设备继续。
5. **完成回顾**：实际完成量进入训练记录，并可生成分享海报。

## 当前可展示的内容

- **作品集首页**：静态产品叙事、三步体验路径与隐私／确认边界始终可见，不依赖 API 返回内容。
- **本地视频入口**：支持 MP4、MOV、WebM；视频由浏览器在当前设备保存，服务端只使用临时分析副本。
- **动作分析流程**：显式启动、可取消、可恢复；分析的完整来源时长以能力接口实际返回值为准。
- **训练工作流**：方案编辑、个性化问卷、训练计时、猫教练状态、结果与海报。
- **受控演示来源**：部署可配置少量授权视频，用于快速体验真实分析；它们不是模拟结果。

> **展示边界**：这是一个正在演进的竞赛／作品集原型。云端分析需要自行配置服务；没有配置时，产品不会伪造 AI 分析成功或部署可用性。

## 技术架构

| 层 | 主要技术 | 责任 |
| --- | --- | --- |
| Web | Vue 3 · TypeScript · Vite · Pinia · Dexie | 训练旅程、同设备数据、媒体选择、方案与训练 UI |
| API | Python 3.12 · FastAPI · Pydantic · httpx | 运行管理、媒体临时处理、来源证据与分析 Provider 编排 |
| 领域能力 | 版本化 contracts 与 skills | 训练编译、个性化调整、动作要点补充 |

核心产品数据优先保存在浏览器的当前设备。用户选择的源视频不进入仓库；后端收到的音视频、帧、转写和模型返回属于一次运行的临时数据，应在运行结束后清理。

## 本地运行

### 环境要求

- Node.js 24.x
- pnpm 11.x
- Python 3.12
- [uv](https://docs.astral.sh/uv/)

### 启动

```powershell
git clone https://github.com/<your-github-account>/Hachimi.git
Set-Location Hachimi
pnpm install
uv sync --project services/analysis-api
Copy-Item .env.example .env.local
pnpm dev
```

打开 `http://localhost:5173`。`.env.local` 只由后端读取，必须保持在 Git 忽略列表中；不要将任何密钥、令牌、Cookie 或真实用户视频提交到仓库。

默认配置中的本地视频限制与 Provider 可用性仅是开发起点。运行时以公共能力接口的返回结果为准。

## 验证

```powershell
pnpm check
pnpm test:e2e
pnpm api:generate
```

- `pnpm check`：Web lint、类型检查、API 静态检查、单元测试与生产构建。
- `pnpm test:e2e`：使用独立 loopback 端口、合成媒体和测试专用适配器，不复用正在运行的本地服务。
- `pnpm smoke:cloud`：仅在本地显式配置真实云端能力后使用；CI 不注入云端密钥。

## 目录导览

```text
apps/web/                  Vue 前端与产品体验
services/analysis-api/     FastAPI 分析服务
contracts/                 版本化接口与问卷合同
skills/                    训练编译、个性化与动作要点领域能力
docs/design/               体验与视觉设计说明
docs/adr/                  架构决策记录
```

`questionaire/` 是队友提交的独立参考原型，只用于设计与实现追溯；正式产品入口为 `apps/web` 与 `services/analysis-api`，参考原型不进入线上源码包或服务。

## 设计与产品文档

- [移动端体验 Brief](docs/design/trainpal-mobile-experience-brief.md)
- [竞赛 Web MVP 规格](docs/specs/competition-web-mvp.md)
- [领域词汇表](CONTEXT.md)
- [本地视频导入与可恢复分析 ADR](docs/adr/0013-local-video-import-and-recoverable-analysis.md)
- [GYMTI 主应用确认边界 ADR](docs/adr/0031-questionnaire-recommends-main-app-confirms-coach-style.md)
- [公开分析并发策略 ADR](docs/adr/0045-public-analysis-keeps-one-slot-without-session-or-ip-cooldown.md)

## License

[MIT](LICENSE) © 2026 Hachimi Contributors
