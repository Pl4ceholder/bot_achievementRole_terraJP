"""Discord の中身を、サイト（Terraria JP 公式サイト）へ定期的に送る。

サイト側は Discord を叩かない。Bot が送った内容をそのまま持って表示するだけなので、
Discord が重くてもサイトは止まらないし、サイトに Bot のトークンを置かなくて済む。

送り先は `POST /api/internal/recruitments`。合言葉（SYNC_SECRET）が合わないと 401、
サイト側に合言葉が無ければ 503 で断られる。
"""

import aiohttp
from discord.ext import commands, tasks

from config import (
    CHANNEL_CONSOLE,
    CHANNEL_TMOD,
    CHANNEL_VANILLA,
    GUILD_ID,
    SYNC_INTERVAL_MINUTES,
    SYNC_SECRET,
    WEB_SYNC_URL,
)
from web_sync import build_payload, collect_recruitments

# 募集が立つチャンネル（cogs/recruitment.py と同じ）
RECRUIT_CHANNELS = {CHANNEL_TMOD, CHANNEL_CONSOLE, CHANNEL_VANILLA}


class SiteSyncCog(commands.Cog):
    """Bot が持っている内容を、サイトへ定期的に写す。"""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.session: aiohttp.ClientSession | None = None

    async def cog_load(self):
        if not WEB_SYNC_URL or not SYNC_SECRET:
            print("サイトとの同期は設定されていないので動かしません（WEB_SYNC_URL / SYNC_SECRET）。")
            return
        self.session = aiohttp.ClientSession()
        self.sync_recruitments.start()
        print(f"募集の同期を始めました（{SYNC_INTERVAL_MINUTES} 分ごと）。")

    async def cog_unload(self):
        self.sync_recruitments.cancel()
        if self.session is not None:
            await self.session.close()
            self.session = None

    @tasks.loop(minutes=SYNC_INTERVAL_MINUTES)
    async def sync_recruitments(self):
        """いま立っている募集を集めて、サイトへ送る（丸ごと入れ替え）。"""
        if not GUILD_ID:
            return

        guild = self.bot.get_guild(int(GUILD_ID))
        if guild is None:
            print("サーバーが見つからないので、募集を送れませんでした。")
            return

        items = await collect_recruitments(guild, RECRUIT_CHANNELS)

        try:
            async with self.session.post(
                WEB_SYNC_URL,
                json=build_payload(guild.id, items),
                headers={"x-sync-secret": SYNC_SECRET},
                timeout=aiohttp.ClientTimeout(total=20),
            ) as response:
                status = response.status
                body = await response.text()
        except Exception as error:  # 送れなくても、次の回にまた送ればよい
            print(f"募集を送れませんでした: {error}")
            return

        if status == 200:
            print(f"募集を {len(items)} 件送りました。")
        else:
            print(f"募集を送れませんでした（{status}）: {body[:200]}")

    @sync_recruitments.before_loop
    async def before_sync_recruitments(self):
        await self.bot.wait_until_ready()

    @sync_recruitments.error
    async def on_sync_error(self, error: BaseException):
        print(f"募集の同期でエラーが起きました: {error}")


async def setup(bot: commands.Bot):
    await bot.add_cog(SiteSyncCog(bot))
