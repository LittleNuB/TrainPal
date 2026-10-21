# TrainPal 个人网站部署

部署域名 `https://caohonglin.fun/trainpal/`，应用仅监听现有 ECS 的 `127.0.0.1:8788`。作品集配置与旧竞赛配置独立，旧配置检查继续运行。

## 产物与配置

1. 合并生产配置 PR 并等 main CI 成功。在 main 触发 `Portfolio image (verified main artifact)`；工作流执行完整检查、镜像层与 FFmpeg 审计，再生成绑定完整 SHA 的预发布产物。服务器不执行 Node/Python 安装或镜像编译。
2. 下载对应 `portfolio-build-<完整SHA>` 的 `trainpal-image.tar.gz`、`SHA256SUMS`、`RELEASE_SHA`，核对 SHA 和校验和，再 `docker load`。
3. 初始化 `/etc/trainpal`（0700）、`/var/lib/trainpal/budget` 与 `/var/lib/trainpal/runtime`（10001:10001、0700）、`/var/lib/trainpal/media`。仅受控媒体目录允许 Nginx 读取。
4. 将 `portfolio.env.example` 填入 `/etc/trainpal/production.env`（0600，root），不得把密钥放入命令参数、日志、仓库或站点目录。GYMTI 使用已批准 Mini 独立设置。
5. 通过受保护的传输方式准备五个 manifest 匹配的已授权受控文件，保留路径与 SHA-256。用户上传视频、模型文本及临时窗口不进入发布产物。
6. 停止本地付费验收服务后，以 SQLite backup 复制本轮真实验收账本至 `/var/lib/trainpal/budget/ai.sqlite`。部署前后核对使用额与未知尝试数；禁止从空账本启动线上，也禁止同时消费一份账本的两个副本。

Docker daemon 使用 `portfolio-docker-daemon.json`，先配置再启动。保留现有 HTTPS MSS 与防火墙规则。不要给已有 Docker daemon 覆盖配置；若未来已有其他容器，先核对兼容性。

## 启动与路由

```sh
export TRAINPAL_SHA=<已通过CI的完整main-SHA>
export TRAINPAL_IMAGE=trainpal-portfolio:$TRAINPAL_SHA
docker compose --file portfolio.compose.yml config --quiet
docker compose --file portfolio.compose.yml up -d
curl --fail http://127.0.0.1:8788/api/v1/health
curl --fail http://127.0.0.1:8788/api/v1/ready
curl --fail http://127.0.0.1:8788/api/v1/runtime-cleanup
```

启动先取得预算目录中的进程锁，然后清理专用临时运行目录。该锁跨 `exec` 保留；第二个实例会退出，避免清理活动文件。强制终止遗留的上传视频、音频和窗口在重启前清理，预算库保留。

保存原 Nginx 配置，仅在现有 HTTPS server 中 include `portfolio.nginx.conf`。代理剥离 `/trainpal/`，转发单一可信客户端地址，保留上传、SSE 与媒体 Range。运行 `nginx -t` 后平滑加载。作品集能力接口必须返回 300 秒、67108864 字节。

## 验收与回滚

先验回环，再验公网 HTTPS、子页直达/刷新、资源、SSE、上传、播放、录音和预算持久。真实桌面与 Android Chrome 分别跑视频、模型调整、手动编辑、训练与恢复。真人桌面唤醒及手机点按录音不能用模拟事件替代。

保留原配置、候选镜像与上一镜像。失败时移除本次 TrainPal include，`nginx -t` 后 reload，再停止 TrainPal 容器。仅已验证兼容当前预算账本、仍覆盖全部付费调用的旧镜像可以按确切 SHA 恢复；禁止退回未接入预算保护的镜像。首次部署没有上一线上版本时，只撤回 TrainPal 路由和服务。**不删除、不回滚、不用旧副本覆盖预算库**。旧版容器也须继续挂载同一预算目录。官网、HomyMate、证书、DNS 与现有网络兼容服务不参与回滚。

额度达到累计 50 元或北京时间每日 5 元时，新付费调用停止。未知费用继续占用预留，已存方案、手动数字编辑及训练仍可用。运维只能查看金额与状态，账本不存用户媒体或模型内容。
