import requests
import asyncio
import datetime
import re
from pkg.plugin.context import (
    BasePlugin,
    EventContext,
    register,
    handler,
)
from pkg.plugin.events import (
    GroupMessageReceived,
    PersonMessageReceived
)
from pkg.platform.types import (
    MessageChain,
    Plain,
    Image
)

API_URL = "https://v3.alapi.cn/api/zaobao"
API_TOKEN = "在这里填入你的token"

def fetch_news_image_url() -> str:
    """
    调用新闻API，获取返回JSON并提取其中的`image`字段。
    """
    querystring = {
        "token": API_TOKEN,
        "format": "json"
    }
    headers = {
        "Content-Type": "application/json"
    }
    try:
        resp = requests.get(API_URL, headers=headers, params=querystring, timeout=8)
        data = resp.json()
        image_url = data["data"]["image"]
        return image_url
    except Exception as e:
        print(f"[DailyNewsPlugin] 请求或解析API出现异常: {e}")
        return ""

@register(
    name="DayNews", 
    description="发送每日新闻图片的插件(带定时功能)", 
    version="0.4", 
    author="Rio"
)
class DailyNewsPlugin(BasePlugin):
    """
    当用户输入 '/news' 时，获取API返回的新闻图片并发送。
    支持 '/news set hh:mm' 命令做每日定时推送。
    支持 '/news stop' 命令关闭定时推送。
    """

    def __init__(self, host):
        self.host = host
        # 用于存储当前会话(群/私聊)对应的定时任务
        # key 可以是 group_id 或者 person_id，value 是 asyncio.Task
        self.tasks = {}

    async def initialize(self):
        print("[DailyNewsPlugin] 插件初始化完成")

    # @handler(PersonMessageReceived)
    @handler(GroupMessageReceived)
    async def on_message(self, ctx: EventContext):
        """
        监听私聊和群聊消息，识别 '/news' 相关命令。
        """
        msg_str = str(ctx.event.message_chain).strip()
        if not msg_str:
            return  # 空消息则不处理

        # 将会话标识（私聊则用sender, 群聊则用group）提取为一个 key
        # 你也可以根据实际需求决定是否区分群/私聊。这里简单区分一下：
        if hasattr(ctx.event, "group_id") and ctx.event.group_id:
            context_id = f"group_{ctx.event.group_id}"
        else:
            context_id = f"user_{ctx.event.sender_id}"

        parts = msg_str.split(maxsplit=2)
        cmd = parts[0].lstrip("/").lower()  # 去掉开头的斜杠，转小写

        # 1) /news -> 立即发送一次新闻
        if cmd == "news" and len(parts) == 1:
            # 立即发送新闻图片
            await self.send_news_once(ctx)
            return

        # 2) /news set hh:mm -> 设置定时任务
        #   假设只需要解析出 "8:00" 这样的格式
        if cmd == "news" and len(parts) == 2 and parts[1].startswith("set"):
            # 示例: "/news set 8:00"
            # 提取 "8:00"
            time_str = parts[1].replace("set", "").strip()
            # 也可能是 "/news set 08:00" "/news set 9:05" 等等
            # 这里用正则或者简单 split 检查
            match = re.match(r'^(\d{1,2}):(\d{1,2})$', time_str)
            if not match:
                await ctx.reply(MessageChain([Plain("请使用正确的时间格式，如 /news set 8:00")]))
                return
            hour = int(match.group(1))
            minute = int(match.group(2))
            if not (0 <= hour < 24 and 0 <= minute < 60):
                await ctx.reply(MessageChain([Plain("请使用正确的时间格式(小时0-23，分钟0-59)")]))
                return

            # 先停止已有的定时任务（如果存在）
            if context_id in self.tasks:
                task = self.tasks[context_id]
                task.cancel()
                del self.tasks[context_id]

            # 创建新的定时任务
            task = asyncio.create_task(self.schedule_daily_news(ctx, hour, minute))
            self.tasks[context_id] = task

            await ctx.reply(MessageChain([Plain(f"已设置每日新闻定时发送时间为 {hour:02d}:{minute:02d}。")]))
            return

        # 3) /news stop -> 停止当前会话的定时任务
        if cmd == "news" and len(parts) == 2 and parts[1] == "stop":
            if context_id in self.tasks:
                task = self.tasks[context_id]
                task.cancel()
                del self.tasks[context_id]
                await ctx.reply(MessageChain([Plain("已停止每日新闻定时发送。")]))
            else:
                await ctx.reply(MessageChain([Plain("当前没有正在执行的定时任务。")]))
            return

    async def send_news_once(self, ctx: EventContext):
        """
        立即发送一次新闻。
        """
        image_url = fetch_news_image_url()
        if not image_url:
            await ctx.reply(MessageChain([Plain("获取新闻图片失败，请稍后重试。")]))
            return
        await ctx.reply(MessageChain([
            Image(url=image_url)
        ]))

    async def schedule_daily_news(self, ctx: EventContext, hour: int, minute: int):
        """
        每日定时发送新闻的循环任务。
        当用户使用 /news set hh:mm 命令后，会创建一个任务运行该函数。
        如果用户 /news stop，则会通过 self.tasks[ctx_id].cancel() 取消它。
        """
        while True:
            now = datetime.datetime.now()
            # 计算当天目标时间
            target_time = now.replace(hour=hour, minute=minute, second=0, microsecond=0)

            # 如果已经过了今天的目标时间，则改为明天
            if target_time <= now:
                target_time += datetime.timedelta(days=1)

            # 计算等待时间
            wait_seconds = (target_time - now).total_seconds()

            try:
                # 等待直到目标时间 (可能被 cancel() 打断)
                await asyncio.sleep(wait_seconds)
            except asyncio.CancelledError:
                print(f"[DailyNewsPlugin] 定时任务被取消: {hour}:{minute}")
                break

            # 到了设定时间 -> 发送新闻
            await self.send_news_once(ctx)

            # 发送完后，等待 24 小时再发(即第二天同一时间)
            # 或者可以直接进入下一次循环再重新计算目标时间
            # 下面这种写法更直观：再次计算下一个目标时间，然后进入下一循环
            # 这里为了简单，直接 sleep 24 小时：
            try:
                await asyncio.sleep(24 * 60 * 60)
            except asyncio.CancelledError:
                print(f"[DailyNewsPlugin] 定时任务被取消: {hour}:{minute}")
                break

    def __del__(self):
        print("[DailyNewsPlugin] 插件被卸载或程序退出")
