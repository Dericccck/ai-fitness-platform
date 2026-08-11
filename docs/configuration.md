# 本地配置说明

环境相关配置可能包含数据库、微信和短信服务凭证，不应提交到 Git。

本地开发时，复制 `src/main/resources/application-dev.example.yml` 为
`src/main/resources/application-dev.yml`，再填写本机配置。集成测试配置同理，使用
`src/test/resources/application-local.example.yml`。

推荐通过环境变量或本地 Secret Manager 注入敏感值。提交前使用以下命令检查待提交内容：

阿里云点播、OSS、CMS 监控服务分别使用 `FITNESS_ALI_VOD_*`、
`FITNESS_ALI_OSS_*`、`FITNESS_ALI_CMS_*` 环境变量注入凭证；短信服务继续使用
`FITNESS_ALI_ACCESS_KEY_ID` 和 `FITNESS_ALI_ACCESS_KEY_SECRET`。

```bash
git diff --cached --check
git grep -n -i -E 'password|secret|token|access[-_]?key' -- ':!*.example.yml'
```
