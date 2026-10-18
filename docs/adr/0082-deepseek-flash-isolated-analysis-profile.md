# DeepSeek Flash 隔离分析配置

状态：已接受（隔离实现范围），2026-10-07。关联 #6/#37。
用户指定将当前隔离评测中 Seed 2.1 Pro 的全部视觉和文本节点换成
`deepseek-flash`。这不是切换默认 Mini、ASR、GYMTI 或演示入口的授权。

## 决定

- 在现有 Provider 公开方法内注入显式请求 Adapter，共用原业务合同、
  字幕整理、来源绑定、参数冲突及候选融合校验，不另造 Agent 链路。
- 所有原 Pro 节点使用同一个 Flash 模型：视觉读取、语音文本理解、
  字幕参数/提醒整理、语义融合。既有提醒/片段选择方法若复用该客户端，
  也使用 Flash。ASR 仍为原语音 Provider。
- DeepSeek Responses 不声明原生视频输入，视觉块改为每秒一帧的 JPEG
  图片序列；每帧给出当前声明窗口内的时间，保持片内时间及一次偏移。
  短于一秒的尾部也采样，不减少原分块覆盖，不上传 Files API，不做外链。
  图片最长边至多960像素、原始细节模式；这不是独立OCR或连续动作识别证明。
- 限定官方 HTTPS 地址、两个模型 ID 均为 Flash、零自动重试。
  `reasoning.effort=none` 显式关闭思考，替代豆包专用 `thinking` 参数。
  JSON Schema 内容不变，DeepSeek 请求不使用未声明的 `strict` 开关；
  本地完整接收校验仍保留。JSON 对象读取模式不因此放宽字段或时间。
- 接收仍须 completed、store=false、实际模型为Flash、无拒绝/错误/未完成。
  DeepSeek 的返回格式元信息不完整回显请求，不能沿用豆包的schema回显比较。
  本地直接按原完整schema验证真实JSON内容；不伪造或改写返回元信息。
  超容量、抽帧不完整或模型格式错误拒绝，不截断或修补获得成功。
  原视频上限300秒、60秒块/10秒重叠与原输入容量不变。
- 帧仅驻内存，FFmpeg 完成或取消后退出；原字幕/ASR/模型正文不写日志。
  密钥运行时读取，只留本机，不能进入公开材料或代码。
- 历史 Pro 配置及回执保留原身份；新评测使用独立命名。
  正式配置、工厂、15176 与旧存档不修改，不推送、部署或合并。

## 验收

沿用已确认的 Provider 公开方法→外部 HTTP 测试接口，模拟外部网络，
视觉测试实际解码合成视频与每张输出图片。验证请求兼容、时间标记、
取消/错误清理、未知字段/越界/未完成拒绝和默认路径不变。

真实调用先做合成文本和图片两次兼容性检查，任一失败即停；通过后
按原参考记录做39秒一次完整分析。每次零重试，仅记录状态、计数、
耗时及固定原因码。通过接口不等于模型识别准确，也不满足整体 Goal。
68/208重复质量复评与完整旅程、真人语音/播报验收仍单独记录。

接口依据：
[DeepSeek Vision](https://api-docs.deepseek.com/guides/vision/)；
[Responses 参数](https://api-docs.deepseek.com/api/create-response/)；
[Responses 兼容说明](https://api-docs.deepseek.com/guides/responses_api/)。
