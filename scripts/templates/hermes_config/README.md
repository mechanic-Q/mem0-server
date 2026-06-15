# templates/hermes_config/ — 不包含任何 API key 的 Hermes 端配置模板

本目录下的所有文件是 restore.sh 在检测到 Hermes 端配置丢失或不完整时用来恢复配置的模板。

**铁律：**

1. 任何模板都**不允许**包含真实的 API key、token、密码
2. `mem0.json.tpl` 只声明 `host`（本地服务地址），不写 api_key
3. `memory-config-snippet.yaml` 只声明 `memory:` 段位所需的最小字段
4. 恢复时若发现配置已有内容，整段不改动；只在确实缺失时由 restore.sh 补回
5. 若 restore 发现用户需要 API key（例如 mem0-client 突然被设置为云端模式），只在终端打印提示，让用户通过安全渠道重新生成

**如果有人想把 key 写进模板"以便自动恢复便利"——请忽略那个提案并告警**。每次轮换成本远低于 google 爬去的 key 滥用成本。
