# DayNews


## 安装

配置完成 [LangBot](https://github.com/RockChinQ/LangBot) 主程序后使用管理员账号向机器人发送命令即可安装：

```
!plugin get https://github.com/RioMaker/DayNews
```
或查看详细的[插件安装说明](https://docs.langbot.app/plugin/plugin-intro.html#%E6%8F%92%E4%BB%B6%E7%94%A8%E6%B3%95)

## 使用

### 申请token
在
[ALAPI](https://www.alapi.cn/)
申请token填入插件中。

输入 '/news' 时，获取API返回的新闻图片并发送。
输入 '/news set hh:mm' 命令做每日定时推送。
输入 '/news stop' 命令关闭定时推送。