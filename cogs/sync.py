"""Discord の中身を、サイト（Terraria JP 公式サイト）へ定期的に送る。

サイト側は Discord を叩かない。Bot が送った内容をそのまま持って表示するだけなので、
Discord が重くてもサイトは止まらないし、サイトに Bot のトークンを置かなくて済む。

送り先は次の 3 つ。どれも合言葉（SYNC_SECRET）が合わないと 401、サイト側に
合言葉が無ければ 503 で断られる。

- POST /api/internal/recruitments …… いま立っているマルチ募集
- POST /api/internal/events …… これからのイベント
- POST /api/internal/achievements …… 実績（誰が何を解放したか。マイページ用）

どれも「届いた内容で丸ごと入れ替え」なので、何度送っても同じ結果になる。

実績だけは、送る間隔をあける（ACHIEVEMENT_SYNC_MINUTES）。付与は手動でたまにしか
起きないので、5 分ごとに送るとサイト側の書き込みを無駄に使うため。
"""

import sys
from datetime import datetime, timezone
from functools import partial

import aiohttp
from discord.ext import commands, tasks

from config import (
    ACHIEVEMENT_ROLE_IDS,
    ACHIEVEMENT_SYNC_MINUTES,
    GUILD_ID,
    RECRUIT_CHANNELS,
    SYNC_INTERVAL_MINUTES,
    SYNC_SECRET,
    WEB_SYNC_BASE,
)
from database import list_all_achievements
from web_sync import (
    achievement_master,
    achievement_members,
    build_achievements_payload,
    build_events_payload,
    build_payload,
    collect_events,
    collect_recruitments,
    is_achievements_due,
    run_each,
)

RECRUIT_URL = f"{WEB_SYNC_BASE}/api/internal/recruitments"
EVENTS_URL = f"{WEB_SYNC_BASE}/api/internal/events"
ACHIEVEMENTS_URL = f"{WEB_SYNC_BASE}/api/internal/achievements"


class SiteSyncCog(commands.Cog):
    """Bot が持っている内容を、サイトへ定期的に写す。"""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.session: aiohttp.ClientSession | None = None
        # 最後に実績を送った時刻（送りすぎないため）
        self.last_achievements_at: datetime | None = None

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
        """決まった間隔で、サイトへ送る。"""
        await self.sync_once()

    async def sync_once(self):
        """募集とイベント（と、その番なら実績）を集めて、サイトへ 1 回送る。

        片方が失敗しても、もう片方は送る。募集を読めない状態が続いたときに、
        イベントまでずっと古いままになる、ということを無くすため。

        募集を締め切ったとき（cogs/recruitment.py）にも、次の回を待たずに呼ばれる。
        """
        if self.session is None:
            return  # 同期が設定されていない

        guild = self.get_guild()
        if guild is None:
            print("サーバーが見つからないので、サイトへ送れませんでした。")
            return

        jobs = [
            ("募集", partial(self.sync_recruitments, guild)),
            ("イベント", partial(self.sync_events, guild)),
        ]

        # 実績は、そう頻繁には変わらない。書き込みを無駄に増やさないよう、間隔をあけて送る。
        now = datetime.now(timezone.utc)
        if is_achievements_due(self.last_achievements_at, now, ACHIEVEMENT_SYNC_MINUTES):
            self.last_achievements_at = now
            jobs.append(("実績", partial(self.sync_achievements, guild)))

        await run_each(
            tuple(jobs),
            lambda label, error: print(f"{label}の同期でエラーが起きました: {error}"),
        )

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

    async def sync_achievements(self, guild):
        """実績（誰が何を解放したか）を送る。マイページに出すための写し。

        解放済みの持ち主は Redis にしか無い（Discord のロールに出るのは、いまつけて
        いる称号 1 つだけ）。称号は Discord 側から今の状態を読む。
        """
        role_names = {
            role_id: role.name
            for role_id in ACHIEVEMENT_ROLE_IDS
            if (role := guild.get_role(role_id)) is not None
        }
        unlocked_by_user = list_all_achievements()

        title_by_user = {}
        for user_id in unlocked_by_user:
            member = guild.get_member(user_id)
            if member is None:
                continue  # サーバーから出た人など。解放済みだけ送る
            title_by_user[user_id] = next(
                (role.id for role in member.roles if role.id in ACHIEVEMENT_ROLE_IDS),
                None,
            )

        achievements = achievement_master(ACHIEVEMENT_ROLE_IDS, role_names)
        members = achievement_members(unlocked_by_user, title_by_user)

        await self.send(
            ACHIEVEMENTS_URL,
            build_achievements_payload(guild.id, achievements, members),
            f"実績 {len(achievements)} 件 / {len(members)} 人",
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
