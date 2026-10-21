# ADR-0092: 个人网站完整体验与持久化 AI 预算

2026-10-22，已接受。用户批准按两次 PR 验收并发布 https://caohonglin.fun/trainpal/，部署到现有 ECS。

保留 competition 配置的全部检查，新增 portfolio 独立配置。主应用装配视频分析、模型个性化、片段查找、单次 ASR、GYMTI 和训练提醒。本地与公网使用相同服务构造函数。继续使用已验收的 Ark 文本 260915、视觉 260628、分析 json_object 及本地严格 schema 校验。GYMTI 保留已批准 Mini。

所有模型 HTTP 尝试和 ASR 尝试通过独立 SQLite 账本。累计上限 50 元、北京时间每日 5 元；调用前以官方标准价格和输入/输出上界预留，响应使用量可核实时结算。失败、取消、进程退出或缺失 usage 不释放预留；计量超出边界时停止后续付费调用。额度不属于访客，也不因服务重启、日期切换、更新或回滚重置累计额。

模型输入以 UTF-8 字节数加 framing 上界计，视频仅接收本服务 FFmpeg 生成并带帧数信息的内联 MP4，按官方 80K 视频 token 上限加 framing 预留；Responses 输出限制 16384 token，Chat 使用 max_completion_tokens 将思考与回答的总输出限制为 4096 token。未知模型、未知计费入口及图片实验路径停止付费调用。ASR 只接受已核价的标准时长资源，16kHz PCM16 按每次尝试向上取整秒数计价。账本仅存调用标识、日期、模型标识和整数金额，不保存媒体或模型内容。

匿名入口保持公开单分析并发，不引入账号、评委码、访客配额或冷却。预算不足返回既有错误结构及 ai_budget_exhausted，保留本地保存、数字编辑和训练。

前端构建支持 /trainpal/，Nginx 剥离入口前缀；cookie Path=/trainpal。应用只绑定 127.0.0.1:8788，单 worker、1GB 内存、host 网络。镜像在服务器外构建并审计，ECS Docker 不管理桥接与防火墙。预算、受控媒体和临时运行数据独立于网站及 HomyMate。密钥置于站点目录外。运行目录保留独立 host bind；启动前取得预算目录中的排他进程锁并清理旧临时媒体，锁跨 exec 保留到单 worker 退出。第二个实例不能清理活跃实例的数据，崩溃重启也不清空预算。

本次上传能力为 300 秒、64MiB。桌面真人免手与 Android Chrome 点按录音必须由实际设备验证，模拟语音仅证明状态机行为；手机免手和既有模型质量专项不由本次发布自动关闭。

价格核验依据：

- https://ark.volcengine.com/region:cn-beijing/docs/model-pricing.md
- https://ark.volcengine.com/region:cn-beijing/docs/video-understanding.md
- https://docs.volcengine.com/docs/DoubaoVoice/BillingOverview-15?lang=zh
- https://docs.volcengine.com/docs/ark/context-management?lang=zh

北京标准价 Pro 输入 6 元/百万 token，输出 30 元/百万；Mini 按输入长度阶梯采用 0.2/2、0.4/4、0.8/8 元/百万。ASR 标准时长 4.5 元/小时。不扣除缓存优惠或赠送额度，保守计入预算。
