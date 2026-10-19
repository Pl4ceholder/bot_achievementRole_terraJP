"""サイトへの同期（cogs/sync.py）を、Discord にも Redis にも繋がずに確かめる。

    pip install -r requirements.txt        # discord.py と redis が要ります
    python tools/check_sync_cog.py

確かめること:

- 1 回の同期で、募集・イベント・実績の 3 つを送るか
- 実績は、間隔をあけるまで送り直さないか（サイト側の書き込みを無駄に使わない）
- 実績の中身が、サイトが受け取れる形になっているか
  （マスタは Discord のロール名、称号はメンバーのロールから読む、サーバーに居ない人は称号なし）
- 同期の設定が無いときは、何もしないか

合っていなければ終了コード 1 で終わる。
"""

import asyncio
import os
import sys
import types
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import cogs.sync as sync  # noqa: E402
from config import ACHIEVEMENT_ROLE_IDS  # noqa: E402

GUILD_ID = 999000111222333444

A1, A2, A3 = ACHIEVEMENT_ROLE_IDS


class FakeResponse:
    def __init__(self, status=200, body="{}"):
        self.status = status
        self._body = body

    async def text(self):
        return self._body


class FakePost:
    """`async with session.post(...)` で使える形にする。"""

    def __init__(self, response):
        self.response = response

    async def __aenter__(self):
        return self.response

    async def __aexit__(self, *args):
        return False


class FakeSession:
    def __init__(self):
        self.posts = []

    def post(self, url, json=None, headers=None, timeout=None):
        self.posts.append({"url": url, "payload": json, "headers": headers or {}})
        return FakePost(FakeResponse())

    def sent(self, from_index=0):
        return [post["url"] for post in self.posts[from_index:]]


class FakeRole:
    def __init__(self, role_id, name):
        self.id = role_id
        self.name = name


class FakeMember:
    def __init__(self, roles):
        self.roles = roles


class FakeGuild:
    """実績を集めるときに使うところだけを持つサーバー。"""

    def __init__(self, roles, members):
        self.id = GUILD_ID
        self._roles = {role.id: role for role in roles}
        self._members = members

    def get_role(self, role_id):
        return self._roles.get(role_id)

    def get_member(self, user_id):
        return self._members.get(user_id)

    async def active_threads(self):
        return []

    async def fetch_scheduled_events(self, with_counts=False):
        return []


def sample_guild() -> FakeGuild:
    """実績ロール 2 つと、3 人ぶんの状態。

    - 1001 …… 2 つ解放していて、称号は A2
    - 1002 …… 1 つ解放していて、称号は無し
    - 1003 …… 1 つ解放しているが、サーバーに居ない（称号は読めない）
    - A3 …… ロールが消えている（マスタに載せない）
    """
    roles = [FakeRole(A1, "ビギナー"), FakeRole(A2, "ビルダー")]
    members = {
        1001: FakeMember([FakeRole(A2, "ビルダー")]),
        1002: FakeMember([FakeRole(555, "ふつうのロール")]),
    }
    return FakeGuild(roles, members)


def make_cog(guild, unlocked):
    original = sync.list_all_achievements
    sync.list_all_achievements = lambda: unlocked

    cog = sync.SiteSyncCog(types.SimpleNamespace(get_guild=lambda guild_id: guild))
    # config の GUILD_ID は環境変数から来る。ここではダミーのサーバーを返す
    cog.get_guild = lambda: guild
    cog.session = FakeSession()

    return cog, original


async def check_wiring(original) -> bool:
    """1 回の同期で 3 つ送り、実績だけは間隔をあけるか。"""
    cog, _ = make_cog(sample_guild(), {1001: [A1]})
    session = cog.session

    await cog.sync_once()
    first = session.sent()

    await cog.sync_once()  # すぐもう一度（実績は送らない番）
    second = session.sent(len(first))

    cog.last_achievements_at = datetime.now(timezone.utc) - timedelta(minutes=31)
    await cog.sync_once()
    third = session.sent(len(first) + len(second))

    sync.list_all_achievements = original

    return (
        first == [sync.RECRUIT_URL, sync.EVENTS_URL, sync.ACHIEVEMENTS_URL]
        and second == [sync.RECRUIT_URL, sync.EVENTS_URL]
        and third == [sync.RECRUIT_URL, sync.EVENTS_URL, sync.ACHIEVEMENTS_URL]
    )


async def check_achievement_payload(original) -> bool:
    """実績の中身が、サイトが受け取れる形になっているか。"""
    guild = sample_guild()
    cog, _ = make_cog(guild, {1001: [A1, A2], 1002: [A1], 1003: [A2]})

    await cog.sync_achievements(guild)
    sync.list_all_achievements = original

    return cog.session.posts[0]["payload"] == {
        "guildId": str(GUILD_ID),
        "achievements": [
            {"id": str(A1), "name": "ビギナー"},
            {"id": str(A2), "name": "ビルダー"},
        ],
        "members": [
            {"userId": "1001", "unlocked": sorted([str(A1), str(A2)]), "title": str(A2)},
            {"userId": "1002", "unlocked": [str(A1)], "title": ""},
            {"userId": "1003", "unlocked": [str(A2)], "title": ""},
        ],
    }


async def check_without_settings() -> bool:
    """同期の設定が無いときは、何もしないか（例外も出さないか）。"""
    cog, original = make_cog(sample_guild(), {1001: [A1]})
    cog.session = None

    try:
        await cog.sync_once()
    except Exception:
        sync.list_all_achievements = original
        return False

    sync.list_all_achievements = original
    return True


async def main() -> None:
    original = sync.list_all_achievements

    try:
        checks = (
            ("1 回の同期で 3 つ送り、実績だけは間隔をあける", await check_wiring(original)),
            ("実績の中身が、サイトが受け取れる形になっている", await check_achievement_payload(original)),
            ("同期の設定が無いときは、何もしない", await check_without_settings()),
        )
    finally:
        sync.list_all_achievements = original

    for title, ok in checks:
        print(("OK   " if ok else "ダメ  ") + title)

    if not all(ok for _, ok in checks):
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())
