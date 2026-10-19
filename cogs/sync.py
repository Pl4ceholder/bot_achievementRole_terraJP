"""Discord の中身を、サイト（Terraria JP 公式サイト）へ定期的に送る。

サイト側は Discord を叩かない。Bot が送った内容をそのまま持って表示するだけなので、
Discord が重くてもサイトは止まらないし、サイトに Bot のトークンを置かなくて済む。

送り先は次の 2 つ。どちらも合言葉（SYNC_SECRET）が合わないと 401、サイト側に
合言葉が無ければ 503 で断られる。

- POST /api/internal/recruitments …… いま立っているマルチ募集
- POST /api/internal/events …… これからのイベント

どちらも「届いた内容で丸ごと入れ替え」なので、何度送っても同じ結果になる。
"""

import sys

import aiohttp
from discord.ext import commands, tasks

from config import (
    CHANNEL_CONSOLE,
    CHANNEL_TMOD,
    CHANNEL_VANILLA,
    GUILD_ID,
    SYNC_INTERVAL_MINUTES,
    SYNC_SECRET,
    WEB_SYNC_BASE,
)
from web_sync import (
    build_events_payload,
    build_payload,
    collect_events,
    collect_recruitments,
)

# 募集が立つチャンネル（cogs/recruitment.py と同じ）
RECRUIT_CHANNELS = {CHANNEL_TMOD, CHANNEL_CONSOLE, CHANNEL_VANILLA}

RECRUIT_URL = f"{WEB_SYNC_BASE}/api/internal/recruitments"
EVENTS_URL = f"{WEB_SYNC_BASE}/api/internal/events"


class SiteSyncCog(commands.Cog):
    """Bot が持っている内容を、サイトへ定期的に写す。"""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.session: aiohttp.ClientSession | None = None

    async def cog_load(self):
        # Render では print が溜められて見えなくなるので、行ごとに出すようにする
        # （これが無いと、動いているのにログが出ずに原因が分からなくなる）
        sys.stdout.reconfigure(line_buffering=True)

        if not SYNC_SECRET:
            print("サイトとの同期は設定されていないので動かしません（SYNC_SECRET）。")
            return
        self.session = aiohttp.ClientSession()
        self.sync_site.start()
        print(f"サイトへの同期を始めました（{SYNC_INTERVAL_MINUTES} 分ごと）。")

    async def cog_unload(self):
        self.sync_site.cancel()
        if self.session is not None:
            await self.session.close()
            self.session = None

    @tasks.loop(minutes=SYNC_INTERVAL_MINUTES)
    async def sync_site(self):
        """募集とイベントを集めて、サイトへ送る。"""
        guild = self.get_guild()
        if guild is None:
            print("サーバーが見つからないので、サイトへ送れませんでした。")
            return

        await self.sync_recruitments(guild)
        await self.sync_events(guild)

    def get_guild(self):
        """送り先のサーバー。まだ準備できていなければ None。"""
        if not GUILD_ID:
            return None
        return self.bot.get_guild(int(GUILD_ID))

    async def sync_recruitments(self, guild):
        """いま立っている募集を送る。"""
        items = await collect_recruitments(guild, RECRUIT_CHANNELS)
        await self.send(RECRUIT_URL, build_payload(guild.id, items), f"募集 {len(items)} 件")

    async def sync_events(self, guild):
        """これからのイベントを送る。"""
        items = await collect_events(guild)
        await self.send(
            EVENTS_URL, build_events_payload(guild.id, items), f"イベント {len(items)} 件"
        )

    async def send(self, url, payload, label):
        """サイトへ送る。送れなくても、次の回にまた送ればよい。"""
        try:
            async with self.session.post(
                url,
                json=payload,
                headers={"x-sync-secret": SYNC_SECRET},
                timeout=aiohttp.ClientTimeout(total=20),
            ) as response:
                status = response.status
                body = await response.text()
        except Exception as error:
            print(f"{label} を送れませんでした: {error}")
            return

        if status == 200:
            print(f"{label} を送りました。")
        else:
            print(f"{label} を送れませんでした（{status}）: {body[:200]}")

    @sync_site.before_loop
    async def before_sync_site(self):
        await self.bot.wait_until_ready()

    @sync_site.error
    async def on_sync_error(self, error: BaseException):
        print(f"サイトへの同期でエラーが起きました: {error}")


async def setup(bot: commands.Bot):
    await bot.add_cog(SiteSyncCog(bot))
