import os
import sqlite3
import json
import asyncio
from datetime import datetime, timedelta, timezone, time as dtime

from pkg.plugin.context import register, handler, BasePlugin, APIHost, EventContext
from pkg.plugin.events import GroupMessageReceived, PersonMessageReceived
from pkg.platform.types import MessageChain, Plain, At, Image, MessageSegment

# ------- 以下是你现有打卡相关的常量与数据库文件路径 -------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, 'checkin.db')
china_tz = timezone(timedelta(hours=8))

# ========== 1. 初始化数据库：增加一张 news_schedules 用于记录“群定时早报”的信息 ==========
def init_db():
    """
    初始化数据库：如果没有 news_schedules 表则创建。
    也可顺便初始化其他打卡相关的表。
    """
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    # 你原先的打卡表初始化逻辑(简略)
    c.execute('''
        CREATE TABLE IF NOT EXISTS checkins (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            checkin_time DATETIME NOT NULL
        )
    ''')
    c.execute('''
        CREATE TABLE IF NOT EXISTS goals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            checkin_id INTEGER NOT NULL,
            goal TEXT NOT NULL,
            FOREIGN KEY (checkin_id) REFERENCES checkins(id)
        )
    ''')

    # 新增的表：news_schedules
    c.execute('''
        CREATE TABLE IF NOT EXISTS news_schedules (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            group_id TEXT NOT NULL,
            active INTEGER NOT NULL DEFAULT 1,  -- 1表示启用, 0表示停用
            time TEXT NOT NULL DEFAULT '08:00'  -- 每日发送时间(字符串)
        )
    ''')

    conn.commit()
    conn.close()

# ========== 2. 管理员 QQ 读取逻辑：依旧使用你现有的 read_admin_id 函数（或类似） ==========
def read_admin_id(current_user_id):
    """
    读取或创建管理员 ID。若文件中无管理员，则将当前用户设为管理员。
    """
    file_path = os.path.join(BASE_DIR, "admin_data.json")
    if os.path.exists(file_path):
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                admin_data = json.load(f)
                if "admin_id" in admin_data:
                    return ["存在", admin_data["admin_id"]]
                else:
                    admin_data["admin_id"] = current_user_id
                    with open(file_path, "w", encoding="utf-8") as f2:
                        json.dump(admin_data, f2)
                    return ["不存在", current_user_id]
        except json.JSONDecodeError:
            # 文件格式不对，则新建
            admin_data = {"admin_id": current_user_id}
            with open(file_path, "w", encoding="utf-8") as f:
                json.dump(admin_data, f)
            return ["不存在", current_user_id]
    else:
        # 不存在则直接创建空文件，但不写入任何管理员ID
        with open(file_path, "w", encoding="utf-8") as f:
            pass
        return ["不存在", current_user_id]


# ========== 3. 关于早报的获取函数 ==========
def get_daily_news_image():
    """
    从接口获取每日早报图片链接。
    你可根据自己实际接口进行修改
    """
    import requests

    # 这里替换成你的真实 token 和接口
    token = "YOUR_TOKEN"
    url = "https://v3.alapi.cn/api/zaobao"
    params = {"token": token, "format": "json"}

    try:
        resp = requests.get(url, params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json().get("data", {})
        return data.get("image", "")
    except Exception as e:
        print(f"[!] get_daily_news_image error: {e}")
        return ""

# ========== 4. 关于多群定时发送的数据库操作辅助函数 ==========

def get_all_schedules():
    """
    返回所有群的定时发送设置: [(group_id, active, time), ...]
    """
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT group_id, active, time FROM news_schedules")
    rows = c.fetchall()
    conn.close()
    return rows

def get_schedule_by_group(group_id):
    """
    返回指定群的设置 (group_id, active, time) 或 None
    """
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT group_id, active, time FROM news_schedules WHERE group_id = ?", (str(group_id),))
    row = c.fetchone()
    conn.close()
    return row

def set_schedule(group_id, send_time="08:00", active=1):
    """
    设置/更新指定群的定时发送配置
    """
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    # 先看是否已有记录
    c.execute("SELECT id FROM news_schedules WHERE group_id = ?", (str(group_id),))
    row = c.fetchone()

    if row:
        # 更新
        c.execute("""
            UPDATE news_schedules
            SET active = ?, time = ?
            WHERE group_id = ?
        """, (active, send_time, str(group_id)))
    else:
        # 插入
        c.execute("""
            INSERT INTO news_schedules (group_id, active, time)
            VALUES (?, ?, ?)
        """, (str(group_id), active, send_time))

    conn.commit()
    conn.close()

def stop_schedule(group_id):
    """
    停止（停用）指定群的定时发送
    """
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
        UPDATE news_schedules
        SET active = 0
        WHERE group_id = ?
    """, (str(group_id),))
    conn.commit()
    conn.close()

# ========== 5. 插件主体，结合打卡功能与多群定时发送功能 ==========

@register(
    name="DailyGoalsTracker",
    description="多群定时发送早报功能。",
    version="0.2",
    author="Rio"
)
class MyPlugin(BasePlugin):
    def __init__(self, host: APIHost):
        super().__init__(host)
        self.host = host
        
        # 用于存储 “群号 -> asyncio.Task” 的映射
        self.news_tasks = {}

    async def initialize(self):
        # 初始化数据库(包括 news_schedules 表)
        init_db()
        # 插件加载时，遍历数据库中所有启用的群，创建定时任务
        schedules = get_all_schedules()
        for (group_id, active, time_str) in schedules:
            if active == 1:
                # 创建异步任务
                self.restart_daily_task_for_group(group_id, time_str)

    def restart_daily_task_for_group(self, group_id: str, time_str: str):
        """
        为某个群重启/启动定时任务:
          - 若之前有 task，先取消
          - 创建新的 task
        """
        # 如果之前有任务在跑，取消之
        if group_id in self.news_tasks:
            task = self.news_tasks[group_id]
            if not task.done():
                task.cancel()

        # 创建一个新的异步任务
        task = asyncio.create_task(self.daily_news_loop(group_id, time_str))
        self.news_tasks[group_id] = task

    async def daily_news_loop(self, group_id: str, time_str: str):
        """
        针对某个群的每日定时发送循环
        """
        while True:
            # 判断数据库里该群是否依旧active(可能被stop)
            row = get_schedule_by_group(group_id)
            if not row:
                # 该群配置已被删除 => 结束task
                return
            _, active, cur_time_str = row
            if active == 0:
                # 停止状态 => 结束task
                return

            # 若time_str 在数据库中被改了，这里也要更新
            time_str = cur_time_str

            # 计算今天的触发时间
            try:
                hour, minute = map(int, time_str.split(":"))
            except:
                hour, minute = (8, 0)  # 若解析失败，则默认08:00

            now = datetime.now(tz=china_tz)
            target_time = datetime(
                now.year, now.month, now.day, hour, minute, 0, tzinfo=china_tz
            )
            if now > target_time:
                # 如果此时已过了 today 的time, 那就顺延到明天
                target_time += timedelta(days=1)
            
            wait_seconds = (target_time - now).total_seconds()

            try:
                # 先等待到达发送时刻
                await asyncio.sleep(wait_seconds)
            except asyncio.CancelledError:
                return  # 任务被取消 => 退出循环

            # 再次查询数据库，确保还在active
            row2 = get_schedule_by_group(group_id)
            if not row2 or row2[1] == 0:
                return  # 已经停用/删除 => 结束

            # 发送
            img_url = get_daily_news_image()
            if img_url:
                # 发送到群
                self.host.send_group_message(
                    int(group_id),
                    MessageChain([Image(img_url)])
                )

            # 发送完后再等24小时
            try:
                await asyncio.sleep(24*3600)
            except asyncio.CancelledError:
                return

    def stop_daily_task_for_group(self, group_id: str):
        """
        停止某群的定时发送(并取消后台task)
        """
        if group_id in self.news_tasks:
            task = self.news_tasks[group_id]
            if not task.done():
                task.cancel()
            self.news_tasks.pop(group_id, None)

    # ========== 5.1 命令处理：只演示 /news 相关，其余打卡命令略  ==========
    @handler(GroupMessageReceived)
    async def handle_group_message(self, ctx: EventContext):
        message_str = str(ctx.event.message_chain).strip()
        user_id = ctx.event.sender_id  # 发送者QQ
        group_id = ctx.event.launcher_id  # 群号

        # 仅演示对 /news 指令的处理
        if not message_str.startswith("/news"):
            return

        # 阻止事件继续传给其他插件处理
        ctx.prevent_default()

        parts = message_str.split()
        if len(parts) == 1:
            # "/news" -> 立即发送一次
            img_url = get_daily_news_image()
            if img_url:
                self.host.send_group_message(
                    group_id,
                    MessageChain([Image(img_url)])
                )
                await ctx.reply(MessageChain([Plain("已触发一次早报发送。")]))
            else:
                await ctx.reply(MessageChain([Plain("获取早报失败，请稍后再试。")]))
            return

        sub_cmd = parts[1].lower()

        # ---- /news set ----
        if sub_cmd == "set":
            # 将当前群添加进定时发送（若无则建，有则设为 active=1）
            row = get_schedule_by_group(str(group_id))
            if not row:
                # 不存在 => 默认时间08:00
                set_schedule(group_id, "08:00", 1)
                self.restart_daily_task_for_group(str(group_id), "08:00")
                await ctx.reply(MessageChain([Plain("本群已加入定时发送列表，默认时间为 08:00")]))
            else:
                # 已经有记录
                if row[1] == 0:
                    # 之前是停止 => 重启
                    set_schedule(group_id, row[2], 1)
                    self.restart_daily_task_for_group(str(group_id), row[2])
                    await ctx.reply(MessageChain([Plain(f"已重新启用本群定时发送，时间保持为 {row[2]}")]))
                else:
                    await ctx.reply(MessageChain([Plain("本群已在定时发送列表中。")]))
            return

        # ---- /news list ----
        if sub_cmd == "list":
            # 列出所有群的定时发送状态
            schedules = get_all_schedules()
            if not schedules:
                await ctx.reply(MessageChain([Plain("当前没有任何群配置了定时发送。")]))
                return
            lines = ["【定时发送列表】"]
            for (g_id, active, t_str) in schedules:
                status = "启用" if active == 1 else "停用"
                lines.append(f"群 {g_id} => {t_str} [{status}]")
            await ctx.reply(MessageChain([Plain("\n".join(lines))]))
            return

        # 需要管理员权限的操作：/news stop, /news HH:MM
        # 先判断是否为管理员
        reAdmin_status, admin_id = read_admin_id(user_id)
        # 如果文件中已存在管理员，则 admin_id 是那个人；否则第一次调用会把当前人设成管理员
        # 但若 "存在" 返回的 admin_id 与 user_id 不同 => 没权限
        if reAdmin_status == "存在" and str(admin_id) != str(user_id):
            await ctx.reply(MessageChain([At(user_id), Plain(" 你无权执行此操作，仅管理员可用。")]))
            return

        # ---- /news stop ---- (仅管理员可用)
        if sub_cmd == "stop":
            stop_schedule(group_id)          # 更新数据库 active=0
            self.stop_daily_task_for_group(str(group_id))  # 取消后台任务
            await ctx.reply(MessageChain([Plain("已停止本群的定时发送。")]))
            return

        # ---- /news HH:MM ---- (仅管理员可用)
        # 解析子命令当作时间
        try:
            hour_minute = sub_cmd.split(":")
            hh = int(hour_minute[0])
            mm = int(hour_minute[1])
            if hh < 0 or hh > 23 or mm < 0 or mm > 59:
                raise ValueError()
            new_time_str = f"{hh:02d}:{mm:02d}"

            # 数据库里若无记录，就先插入
            row = get_schedule_by_group(str(group_id))
            if not row:
                set_schedule(group_id, new_time_str, 1)
            else:
                set_schedule(group_id, new_time_str, 1)

            # 重启任务
            self.restart_daily_task_for_group(str(group_id), new_time_str)
            await ctx.reply(MessageChain([Plain(f"已设置本群每日发送时间为 {new_time_str}。")]))
        except ValueError:
            await ctx.reply(MessageChain([Plain("时间格式错误，请使用 /news HH:MM，例如 /news 08:00")]))

    @handler(PersonMessageReceived)
    async def handle_person_message(self, ctx: EventContext):
        """
        如果你想在私聊里也可使用 /news 指令，可类似处理
        """
        pass

    def __del__(self):
        """
        插件卸载时，取消所有后台任务
        """
        for group_id, task in self.news_tasks.items():
            if not task.done():
                task.cancel()
        self.news_tasks.clear()
