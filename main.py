import requests
import asyncio
import os
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
        # 在实际使用中最好做异常处理，比如:
        # if not data.get("success"):
        #     raise Exception("请求API失败: " + data.get("message", ""))
        image_url = data["data"]["image"]
        return image_url
    except Exception as e:
        print(f"[DailyNewsPlugin] 请求或解析API出现异常: {e}")
        # 没能正常获取时，可返回空字符串以便后续判断
        return ""

@register(
    name="DayNews", 
    description="发送每日新闻图片的插件", 
    version="0.3", 
    author="Rio"
)
class DailyNewsPlugin(BasePlugin):
    """
    当用户输入 '/news' 时，获取API返回的新闻图片并发送。
    """

    def __init__(self, host):
        self.host = host
    
    async def initialize(self):
        # 插件初始化时可以做一些检查或准备工作
        print("[DailyNewsPlugin] 插件初始化完成")

    @handler(PersonMessageReceived)
    @handler(GroupMessageReceived)
    async def on_message(self, ctx: EventContext):
        """
        监听私聊和群聊消息，识别 '/news' 命令。
        """
        msg_str = str(ctx.event.message_chain).strip()
        if not msg_str:
            return  # 空消息则不处理
        
        # 提取命令 (类似 "/news")
        parts = msg_str.split(maxsplit=1)
        # 去掉命令前的斜杠，并转为小写
        cmd = parts[0].lstrip("/").lower()

        if cmd == "news":
            # 从 API 获取图片链接
            image_url = fetch_news_image_url()
            if not image_url:
                # 说明请求失败或解析异常
                await ctx.reply(MessageChain([Plain("获取新闻图片失败，请稍后重试。")]))
                return

            # 发送图片
            # 如果框架支持直接发送网络图片，可以这样：
            await ctx.reply(MessageChain([
                Plain("今日新闻图片："),
                Image(url=image_url)
            ]))

            # 如果框架不支持直接发送网络URL，需要先下载，再上传发送:
            # local_filename = "news_image.jpg"
            # download_image(image_url, local_filename)
            # await ctx.reply(MessageChain([
            #     Plain("今日新闻图片："),
            #     Image(path=local_filename)
            # ]))
            return

    def __del__(self):
        print("[DailyNewsPlugin] 插件被卸载或程序退出")

# 如果要先下载图片到本地，可加上这个函数:
# def download_image(url: str, save_path: str):
#     try:
#         r = requests.get(url, stream=True)
#         with open(save_path, "wb") as f:
#             for chunk in r.iter_content(chunk_size=8192):
#                 f.write(chunk)
#     except Exception as e:
#         print(f"下载图片失败: {e}")
