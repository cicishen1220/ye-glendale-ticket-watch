# Ye Glendale 票价提醒

2026 年 11 月 21 日 State Farm Stadium 场次。每 15 分钟读取 StubHub 公开在售票单，票数不限。105–112 区及 126–133 区的含费单价低于 700 美元时，发一封中文邮件列出本次新增或降价票单。首次成功查票也会发确认邮件。当地时间 20:30 开演后停止。

## 启用

1. 在自己的 GitHub 账户中新建一个专用公开仓库。标准 GitHub 托管的 Linux runner 在公开仓库免费。把本包的 `watch.py` 和 `.github/workflows/watch.yml` 原样放在默认分支。用网页上传时可以分别使用 **Add file → Create new file**，在文件名框填入完整路径。
2. 在仓库 **Settings → Secrets and variables → Actions → New repository secret** 中添加两个 secret。把收件 Gmail 地址填入 `ALERT_EMAIL`，该邮箱同时是发件人。把同一 Google 账户为本任务创建的应用专用密码填入 `GMAIL_APP_PASSWORD`。只有开启两步验证并允许应用专用密码的 Google 账户才能使用此方式。凭据只填入加密设置，不要填入代码、聊天或 GitHub issue。演出后可撤销这个专用密码。
3. 在 **Actions → Ye Glendale ticket watch → Run workflow** 触发第一次检查。确认运行成功，并在收件箱或垃圾邮件中找到确认邮件或实际低价提醒，再把它当作已上线。定时运行只读取默认分支，GitHub 繁忙时可能延迟。
4. 如果 StubHub 对云端地址显示设备验证且没有返回实际票单，运行会失败，保留上次成功的提醒记录。连续三轮失败后会尝试发一次中断邮件。工作流失败详情在 Actions 的运行日志中。Google 拒绝发信时在那里排查凭据。

提醒列出区、排、票单标示的数量、含费美元单价及场次链接。公开仓库中的状态文件只保存已匹配票单的编号、价格和运行状态。邮件地址、专用密码不会写入仓库。部署前可阅读 [GitHub 计费规则](https://docs.github.com/en/billing/concepts/product-billing/github-actions) 和 [Google 应用专用密码说明](https://support.google.com/accounts/answer/185833)。
