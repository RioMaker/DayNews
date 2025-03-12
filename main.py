from pkg.plugin.context import register, handler, llm_func, BasePlugin, APIHost, EventContext
from pkg.plugin.events import GroupNormalMessageReceived
import requests
import json
import asyncio
import pkg.platform.types as platform_types
from datetime import datetime, time as dtime, timedelta

# 在此处填写你的 token
token = ""

def GetNews():
    """
    获取每日早报的图片链接
    """
    url = "https://v3.alapi.cn/api/zaobao"
    querystring = {"token": token, "format": "json"}
    headers = {"Content-Type": "application/json"}
    response = requests.get(url, headers=headers, params=querystring)
    data = response.json().get("data", {})
    img_url = data.get("image", "")
    return img_url

@register(
    name="DayNews",
    description="多群定时发送早报图片，可用/news相关指令控制",
    version="0.1",
    author="Rio"
)
class MyPlugin(BasePlugin):
    def __init__(self, host: APIHost):
        super().__init__(host)
        self.host = host

        # 唯一一个拥有“设置/stop”权限的管理员QQ
        self.admin_qq = 123456789

        # 记录每个群的定时发送配置:
        # group_schedules = {
        #   group_id: {
        #       "active": True/False,   # 是否启用定时
        #       "time": datetime.time,  # 定时发送的时间
        #       "task": asyncio.Task or None  # 该群定时任务
        #   },
        #   ...
        # }
        self.group_schedules = {}

        # 默认发送时间，可自行修改
        self.default_time = dtime(8, 0, 0)

    async def initialize(self):
        """
        插件加载后，会异步调用此方法
        如果需要在插件加载时就恢复之前的记录，可在这里加载
        """
        pass

    def start_or_restart_group_task(self, group_id: int):
        """
        启动(或重启)指定群的定时发送任务
        """
        # 如果群还没在 group_schedules 中，则先放进来
        if group_id not in self.group_schedules:
            self.group_schedules[group_id] = {
                "active": True,
                "time": self.default_time,
                "task": None
            }

        # 如果已有任务在跑，先取消掉
        schedule_info = self.group_schedules[group_id]
        if schedule_info["task"] and not schedule_info["task"].done():
            schedule_info["task"].cancel()

        schedule_info["active"] = True
        # 创建新的后台任务
        schedule_info["task"] = asyncio.create_task(
            self.daily_send_loop(group_id)
        )

    async def daily_send_loop(self, group_id: int):
        """
        针对某个群的定时发送循环
        """
        while True:
            # 若群已在表里，但active=False，则直接结束循环
            if group_id not in self.group_schedules or not self.group_schedules[group_id]["active"]:
                return

            now = datetime.now()
            send_time = self.group_schedules[group_id]["time"]

            # 今天指定时刻
            target_time = datetime.combine(now.date(), send_time)
            # 如果当前时间已经过了指定时刻，则推到明天
            if now > target_time:
                target_time += timedelta(days=1)

            wait_seconds = (target_time - now).total_seconds()

            try:
                await asyncio.sleep(wait_seconds)
            except asyncio.CancelledError:
                # 如果任务被取消，结束循环
                return

            # 再次检查active状态
            if not self.group_schedules.get(group_id, {}).get("active", False):
                return

            # 发送一次
            img_url = GetNews()
            if img_url:
                self.host.apis.message().send_group_message(
                    group_id,
                    [platform_types.MessageSegment.image(img_url)]
                )
            # 发送完毕后，等待24小时继续下一轮
            try:
                await asyncio.sleep(24 * 60 * 60)
            except asyncio.CancelledError:
                return

    def stop_group_task(self, group_id: int):
        """
        停止指定群的定时发送
        """
        if group_id not in self.group_schedules:
            return

        schedule_info = self.group_schedules[group_id]
        schedule_info["active"] = False
        # 取消后台任务
        if schedule_info["task"] and not schedule_info["task"].done():
            schedule_info["task"].cancel()
        schedule_info["task"] = None

    @handler(GroupNormalMessageReceived)
    async def handle_group_message(self, ctx: EventContext):
        """
        监听群消息，解析 /news 指令。
        支持：
          /news           -> 立即发送一次
          /news set       -> 将本群添加到定时发送(无权限要求)，如已停止，则重新启用
          /news stop      -> (管理员专用)停止本群定时发送
          /news HH:MM     -> (管理员专用)设定本群每日发送时间
          /news list      -> 列出所有已添加定时的群，以及各自时间和状态
        """
        msg = ctx.event.text_message.strip()
        if not msg.startswith("/news"):
            return

        # 只要匹配到了 /news，就阻止其默认处理
        ctx.prevent_default()

        sender_id = ctx.event.sender_id  # 发消息的QQ
        group_id = ctx.event.group_id    # 当前群号

        parts = msg.split()
        if len(parts) == 1:
            # "/news" 不带子命令 => 立即发送一次
            img_url = GetNews()
            if img_url:
                self.host.apis.message().send_group_message(
                    group_id,
                    [platform_types.MessageSegment.image(img_url)]
                )
                ctx.add_return("reply", ["已触发一次发送。"])
            else:
                ctx.add_return("reply", ["获取图片失败，请稍后再试。"])
            return

        sub_cmd = parts[1].lower()

        if sub_cmd == "set":
            """
            将当前群添加或重新启用定时发送。
            无权限要求。
            """
            # 如果当前群不在 group_schedules，则初始化并启动任务
            if group_id not in self.group_schedules:
                self.group_schedules[group_id] = {
                    "active": True,
                    "time": self.default_time,
                    "task": None
                }
                self.start_or_restart_group_task(group_id)
                ctx.add_return("reply", [
                    f"已添加本群到每日定时发送列表，默认时间为 {self.default_time.strftime('%H:%M')}"
                ])
            else:
                # 如果已有记录，但处于停止状态，则重启
                if not self.group_schedules[group_id]["active"]:
                    self.start_or_restart_group_task(group_id)
                    ctx.add_return("reply", ["已重新启用本群的定时发送。"])
                else:
                    ctx.add_return("reply", ["本群已经在定时发送列表中了。"])
            return

        if sub_cmd == "stop":
            """
            需要管理员权限。
            停止当前群的定时发送。
            """
            if sender_id != self.admin_qq:
                ctx.add_return("reply", ["无权执行此操作。"])
                return

            self.stop_group_task(group_id)
            ctx.add_return("reply", ["已停止本群的定时发送。"])
            return

        if sub_cmd == "list":
            """
            列出所有在定时列表中的群，以及各自时间和状态
            """
            if not self.group_schedules:
                ctx.add_return("reply", ["当前没有任何群启用定时发送。"])
                return

            lines = ["【定时发送群列表】"]
            for g_id, info in self.group_schedules.items():
                time_str = info["time"].strftime("%H:%M")
                status = "启用" if info["active"] else "停止"
                lines.append(f"- 群 {g_id}: 时间={time_str}, 状态={status}")

            ctx.add_return("reply", ["\n".join(lines)])
            return

        # 尝试解析 "/news HH:MM" => 需要管理员权限
        # 这里 sub_cmd 可能是 "8:00" 之类的
        if sender_id != self.admin_qq:
            ctx.add_return("reply", ["无权执行此操作。"])
            return

        # 管理员可修改当前群的发送时间
        try:
            hour_minute = sub_cmd.split(":")
            hour = int(hour_minute[0])
            minute = int(hour_minute[1])
            new_time = dtime(hour, minute)

            # 如果当前群不在列表中，先 set 一下
            if group_id not in self.group_schedules:
                self.group_schedules[group_id] = {
                    "active": True,
                    "time": new_time,
                    "task": None
                }

            # 更新时间
            self.group_schedules[group_id]["time"] = new_time
            self.group_schedules[group_id]["active"] = True
            # 重启任务
            self.start_or_restart_group_task(group_id)

            ctx.add_return("reply", [
                f"已设置本群每日发送时间为 {hour:02d}:{minute:02d}。"
            ])

        except (ValueError, IndexError):
            ctx.add_return("reply", ["时间格式错误，请使用 /news HH:MM，比如 /news 8:00"])

    def __del__(self):
        """
        插件卸载时，清理所有已启动的异步任务
        """
        for g_id, info in self.group_schedules.items():
            if info["task"] and not info["task"].done():
                info["task"].cancel()
