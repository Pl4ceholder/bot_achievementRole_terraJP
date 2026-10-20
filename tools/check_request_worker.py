"""サイトからの依頼の反映（request_worker.py）を、Discord にも Redis にも繋がずに確かめる。

    pip install -r requirements.txt        # discord.py が要ります
    python tools/check_request_worker.py

確かめること:

- 称号を着ける …… 実績ロールを全部外して、選んだ 1 つだけを付けるか
- 持っていない実績 …… 着けずに断るか（Discord も Redis も触らないか）
- 称号を外す …… 実績ロールが全部外れるか
- 実績の付与 …… Redis に足すだけで、Discord のロールは付けないか
- 実績の剥奪 …… Redis から消し、着けていたときだけロールも外すか
- 古すぎる依頼 …… 当てずに expired にするか
- サーバーに居ない人・権限不足・知らない依頼 …… 落ちずに、理由を返すか

合っていなければ終了コード 1 で終わる。
"""

import asyncio
import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import discord  # noqa: E402

import achievements  # noqa: E402
import request_worker  # noqa: E402
from config import ACHIEVEMENT_ROLE_IDS  # noqa: E402

A1, A2, A3 = ACHIEVEMENT_ROLE_IDS

NOW = datetime(2026, 10, 20, 12, 0, tzinfo=timezone.utc)


def at(minutes_ago: int = 0) -> str:
    """サイトが入れてくる形（ISO 8601、UTC）の時刻。"""
    return (NOW - timedelta(minutes=minutes_ago)).isoformat().replace("+00:00", "Z")


class FakeRole:
    def __init__(self, role_id, name):
        self.id = role_id
        self.name = name


class FakeMember:
    def __init__(self, member_id, guild, roles=(), name="だれか"):
        self.id = member_id
        self.guild = guild
        self.roles = list(roles)
        self.display_name = name
        self.added = []
        self.removed = []
        # True にすると、ロールを付けようとしたときに権限不足になる
        self.forbidden = False

    async def add_roles(self, *roles):
        if self.forbidden:
            raise discord.Forbidden.__new__(discord.Forbidden)

        self.added.extend(roles)
        for role in roles:
            if role not in self.roles:
                self.roles.append(role)

    async def remove_roles(self, *roles):
        self.removed.extend(roles)
        self.roles = [role for role in self.roles if role not in roles]

    def achievement_roles(self) -> list[int]:
        return [role.id for role in self.roles if role.id in ACHIEVEMENT_ROLE_IDS]


class FakeGuild:
    def __init__(self, roles, members):
        self._roles = {role.id: role for role in roles}
        self._members = members

    def get_role(self, role_id):
        return self._roles.get(role_id)

    def get_member(self, user_id):
        return self._members.get(user_id)

    async def fetch_member(self, user_id):
        member = self._members.get(user_id)
        if member is None:
            raise discord.NotFound.__new__(discord.NotFound)
        return member


class FakeRedis:
    """Redis の代わり。呼ばれた内容だけ覚える。"""

    def __init__(self, unlocked=None):
        self.unlocked = unlocked or {}
        self.granted = []
        self.revoked = []

    def install(self):
        achievements.get_user_achievements = lambda user_id: list(
            self.unlocked.get(user_id, [])
        )
        achievements.grant_achievement = lambda user_id, role_id: self.granted.append(
            (user_id, role_id)
        )
        achievements.revoke_achievement = lambda user_id, role_id: self.revoked.append(
            (user_id, role_id)
        )


def build_guild() -> FakeGuild:
    """実績ロール 2 つと、普通のロール 1 つ。

    - 1001（たろう）…… A1・A2 を解放していて、A2 を称号にしている
    - 1002（じろう）…… A1 だけ解放していて、称号は無し
    """
    normal = FakeRole(555, "ふつうのロール")
    roles = [FakeRole(A1, "ビギナー"), FakeRole(A2, "ビルダー"), normal]
    guild = FakeGuild(roles, {})
    guild._members = {
        1001: FakeMember(1001, guild, [roles[1], normal], "たろう"),
        1002: FakeMember(1002, guild, [normal], "じろう"),
    }

    return guild


async def apply(guild, kind, action, user_id, role_id="", minutes_ago=0):
    """サイトから届く形の依頼を、1 件反映する。"""
    return await request_worker.apply_request(
        guild,
        {
            "kind": kind,
            "action": action,
            "userId": str(user_id),
            "roleId": role_id,
            "requestedAt": at(minutes_ago),
        },
        NOW,
    )


async def check_set_title() -> bool:
    """称号を着けると、実績ロールが 1 つだけになるか。"""
    FakeRedis({1001: [A1, A2]}).install()

    guild = build_guild()
    member = guild.get_member(1001)

    status, message = await apply(guild, "title", "set", 1001, str(A1))

    return (
        status == "done"
        and member.achievement_roles() == [A1]
        and member.removed == [guild.get_role(A2)]
        and message == "称号を「ビギナー」に変更しました。"
    )


async def check_not_owned() -> bool:
    """持っていない実績は着けずに断るか（Discord も Redis も触らない）。"""
    redis = FakeRedis({1001: [A1, A2]})
    redis.install()

    guild = build_guild()
    member = guild.get_member(1001)

    status, message = await apply(guild, "title", "set", 1001, str(A3))

    return (
        status == "failed"
        and "持っていない" in message
        and member.achievement_roles() == [A2]
        and member.added == []
        and redis.granted == []
    )


async def check_clear() -> bool:
    """称号を外すと、実績ロールが全部外れるか。"""
    guild = build_guild()
    member = guild.get_member(1001)

    status, message = await apply(guild, "title", "clear", 1001)

    return (
        status == "done"
        and member.achievement_roles() == []
        and message == "称号を外しました。"
    )


async def check_grant() -> bool:
    """付与は Redis だけ。Discord のロールは付けないか。"""
    redis = FakeRedis({1002: []})
    redis.install()

    guild = build_guild()
    member = guild.get_member(1002)

    status, message = await apply(guild, "unlock", "grant", 1002, str(A1))

    return (
        status == "done"
        and redis.granted == [(1002, A1)]
        and member.added == []
        and message == "実績「ビギナー」を付与しました。"
    )


async def check_revoke_worn() -> bool:
    """剥奪すると、着けていたロールも外れるか。"""
    redis = FakeRedis({1001: [A1, A2]})
    redis.install()

    guild = build_guild()
    member = guild.get_member(1001)

    status, message = await apply(guild, "unlock", "revoke", 1001, str(A2))

    return (
        status == "done"
        and redis.revoked == [(1001, A2)]
        and member.achievement_roles() == []
        and message.startswith("実績「")
    )


async def check_revoke_unworn() -> bool:
    """着けていない実績を剥奪しても、ロールは触らないか。"""
    redis = FakeRedis({1002: [A1]})
    redis.install()

    guild = build_guild()
    member = guild.get_member(1002)

    status, _ = await apply(guild, "unlock", "revoke", 1002, str(A1))

    return status == "done" and redis.revoked == [(1002, A1)] and member.removed == []


async def check_expired() -> bool:
    """古すぎる依頼は、当てずに expired にするか。"""
    guild = build_guild()
    member = guild.get_member(1001)
    before = member.achievement_roles()

    status, message = await apply(guild, "title", "set", 1001, str(A1), minutes_ago=11)

    return (
        status == "expired"
        and member.achievement_roles() == before
        and member.added == []
        and "もう一度" in message
    )


async def check_missing_member() -> bool:
    """サーバーに居ない人・知らない依頼は、落ちずに理由を返すか。"""
    guild = build_guild()

    status, message = await apply(guild, "title", "set", 9999, str(A1))

    return status == "failed" and "サーバーにいません" in message


async def check_forbidden() -> bool:
    """Bot の権限が足りないときは、その旨を返すか。"""
    FakeRedis({1001: [A1, A2]}).install()

    guild = build_guild()
    member = guild.get_member(1001)
    member.forbidden = True

    status, message = await apply(guild, "title", "set", 1001, str(A1))

    return status == "failed" and "権限" in message


async def check_bad_requests() -> bool:
    """中身が変な依頼は、すべて failed で返すか（例外を投げないか）。"""
    guild = build_guild()

    cases = [
        await apply(guild, "role", "grant", 1001, str(A1)),
        await apply(guild, "title", "grant", 1001, str(A1)),
        await apply(guild, "unlock", "set", 1001, str(A1)),
        await apply(guild, "unlock", "grant", 1001, "555"),
        await apply(guild, "title", "set", 1001, "555"),
        await apply(guild, "unlock", "grant", "たろう", str(A1)),
    ]

    return all(status == "failed" and message for status, message in cases)


async def check_done_messages() -> bool:
    """うまくいったときの理由に、実績の名前が入っているか。"""
    guild = build_guild()

    status, message = await apply(guild, "unlock", "grant", 1001, str(A1))

    return status == "done" and "ビギナー" in message


async def main() -> None:
    originals = (
        achievements.get_user_achievements,
        achievements.grant_achievement,
        achievements.revoke_achievement,
    )

    # ふだんは、実績を持っている状態を用意しておく（個別の確認で差し替える）
    FakeRedis({1001: [A1, A2], 1002: [A1]}).install()

    try:
        checks = (
            ("称号を着けると、実績ロールが 1 つだけになる", await check_set_title()),
            ("持っていない実績は着けずに断る", await check_not_owned()),
            ("称号を外すと、実績ロールが全部外れる", await check_clear()),
            ("実績の付与は Redis だけ（ロールは付けない）", await check_grant()),
            ("実績の剥奪で、着けていたロールも外れる", await check_revoke_worn()),
            ("着けていない実績の剥奪では、ロールを触らない", await check_revoke_unworn()),
            ("古すぎる依頼は、当てずに expired にする", await check_expired()),
            ("サーバーに居ない人には、その旨を返す", await check_missing_member()),
            ("権限が足りないときは、その旨を返す", await check_forbidden()),
            ("変な依頼は、落ちずに failed で返す", await check_bad_requests()),
            ("うまくいった理由に、実績の名前が入っている", await check_done_messages()),
        )
    finally:
        (
            achievements.get_user_achievements,
            achievements.grant_achievement,
            achievements.revoke_achievement,
        ) = originals

    failed = 0
    for title, ok in checks:
        print(("OK   " if ok else "ダメ  ") + title)
        if not ok:
            failed += 1

    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())
