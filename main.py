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
    description="发送每日新闻图片的插件(轮询定时版)", 
    version="0.5", 
    author="Rio"
)
class DailyNewsPlugin(BasePlugin):
    """
    命令说明：
      1) /news             -> 立即发送一次新闻
      2) /news set hh:mm   -> 设置每日定时任务
      3) /news stop        -> 停止当前会话的定时任务
    """

    def __init__(self, host):
        self.host = host
        # schedule_map 用来存储所有会话的定时设置：
        #  { context_id: {"hour": X, "minute": Y, "has_sent_today": False}, ...}
        self.schedule_map = {}

        # 用来标识后台轮询是否已经启动
        self._polling_task = None

    async def initialize(self):
        print("[DailyNewsPlugin] 插件初始化完成")
        # 启动后台轮询任务（只启动一次）
        if not self._polling_task:
            self._polling_task = asyncio.create_task(self._polling_loop())
            print("[DailyNewsPlugin] 后台轮询任务已启动")

    async def on_destroy(self):
        """
        当插件被卸载或机器人退出时，记得把后台任务结束
        （部分框架可能没有这个钩子，请自行处理）。
        """
        if self._polling_task:
            self._polling_task.cancel()
            self._polling_task = None
        print("[DailyNewsPlugin] 插件销毁，后台轮询任务已结束。")

    @handler(PersonMessageReceived)
    @handler(GroupMessageReceived)
    async def on_message(self, ctx: EventContext):
        """
        监听私聊和群聊消息，识别 '/news' 相关命令。
        """
        msg_str = str(ctx.event.message_chain).strip()
        if not msg_str:
            return  # 空消息则不处理

        # 确定当前的会话ID（群 or 私聊）
        if hasattr(ctx.event, "group_id") and ctx.event.group_id:
            context_id = f"group_{ctx.event.group_id}"
        else:
            context_id = f"user_{ctx.event.sender_id}"

        parts = msg_str.split(maxsplit=2)
        cmd = parts[0].lstrip("/").lower()

        # /news -> 立即发送一次新闻
        if cmd == "news" and len(parts) == 1:
            await self.send_news_once(ctx)
            return

        # /news set hh:mm -> 设置每日定时任务
        if cmd == "news" and len(parts) == 2 and parts[1].startswith("set"):
            time_str = parts[1].replace("set", "").strip()  # 取出 8:00
            match = re.match(r'^(\d{1,2}):(\d{1,2})$', time_str)
            if not match:
                await ctx.reply(MessageChain([Plain("请使用正确的时间格式，如 /news set 8:00")]))
                return
            hour = int(match.group(1))
            minute = int(match.group(2))
            if not (0 <= hour < 24 and 0 <= minute < 60):
                await ctx.reply(MessageChain([Plain("请使用正确的时间格式(小时0-23，分钟0-59)")]))
                return

            # 写入/更新 schedule_map
            self.schedule_map[context_id] = {
                "hour": hour,
                "minute": minute,
                "has_sent_today": False,  # 每天要重新置False，才能再次发送
            }
            await ctx.reply(MessageChain([Plain(f"已设置 {hour:02d}:{minute:02d} 的每日定时推送。")]))
            return

        # /news stop -> 停止当前会话的定时任务
        if cmd == "news" and len(parts) == 2 and parts[1] == "stop":
            if context_id in self.schedule_map:
                del self.schedule_map[context_id]
                await ctx.reply(MessageChain([Plain("已停止本会话的每日新闻定时发送。")]))
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

    async def _polling_loop(self):
        """
        后台轮询任务，每隔 60 秒检查一次是否到达了各会话的定时时间。
        """
        while True:
            now = datetime.datetime.now()
            current_h = now.hour
            current_m = now.minute

            # 遍历所有定时项
            for ctx_id, info in self.schedule_map.items():
                h = info["hour"]
                m = info["minute"]

                # 如果已经到了设定的小时和分钟，并且今日还没发过
                if (current_h == h) and (current_m == m) and (not info["has_sent_today"]):
                    # 这里我们无法直接调用 ctx.reply，因为每个会话可能是群或私聊
                    # 需要我们记录一下群号或用户ID，然后用对应的方式发送
                    # 由于本示例里，我们只有一个 ctx 时才能回复，这里只能做一个简单示例
                    # 你可以自己维护一个 Map {ctx_id: group_id or user_id} -> 发送函数
                    # 为了示例，咱就“假装”可以发送吧
                    print(f"[DailyNewsPlugin] 到点了，发送新闻: {ctx_id}")
                    await self.send_news_to(ctx_id)

                    # 标记今天已经发过
                    info["has_sent_today"] = True

                # 如果当前时间已经过了设定时间，等到明天再发
                # 当天自然要把 has_sent_today 设置为 True
                # 第二天凌晨再重置
                if (current_h > h) or (current_h == h and current_m > m):
                    info["has_sent_today"] = True

                # 如果现在比目标时间早，则确保 has_sent_today=False
                if (current_h < h) or (current_h == h and current_m < m):
                    info["has_sent_today"] = False

            await asyncio.sleep(60)  # 每分钟检查一次

    async def send_news_to(self, context_id: str):
        """
        给指定的上下文ID(群 or 私聊)发送新闻。
        这里需要根据你的框架API去实现：
         - 群发新闻
         - 私聊发新闻
        """
        # 1) 判断群聊还是私聊
        if context_id.startswith("group_"):
            group_id = context_id.replace("group_", "")
            # 你可能需要 something like self.host.send_group_message(...)
            # 示例:
            await self.send_group_news(group_id)
        else:
            user_id = context_id.replace("user_", "")
            # 你可能需要 something like self.host.send_private_message(...)
            # 示例:
            await self.send_user_news(user_id)

    async def send_group_news(self, group_id: str):
        """
        发送群聊新闻消息，示例
        """
        image_url = fetch_news_image_url()
        if not image_url:
            print("[DailyNewsPlugin] 群聊发送失败，没拿到新闻图片URL")
            return
        # 具体实现需要根据你的框架API，可能类似：
        await self.host.send_group_message(
            int(group_id),  # 群ID
            MessageChain([
                # Plain("每日新闻："),
                Image(url=image_url)
            ])
        )

    async def send_user_news(self, user_id: str):
        """
        发送私聊新闻消息，示例
        """
        image_url = fetch_news_image_url()
        if not image_url:
            print("[DailyNewsPlugin] 私聊发送失败，没拿到新闻图片URL")
            return
        # 具体实现需要根据你的框架API，可能类似：
        await self.host.send_private_message(
            int(user_id),  # 用户ID
            MessageChain([
                # Plain("每日新闻："),
                Image(url=image_url)
            ])
        )

    def __del__(self):
        # 如果你的框架在卸载插件时不调用 on_destroy，可以在这里补充一下
        if self._polling_task:
            self._polling_task.cancel()
            self._polling_task = None
        print("[DailyNewsPlugin] 插件被卸载或程序退出")
