"""実績の「解放」と「称号（ロール）」を、1 か所で扱う。

同じ決まりを Discord のコマンド・マイページのパネル・サイトからの依頼の 3 か所に
書くと、片方だけ直して食い違う。ここに 1 つだけ置いて、みんなここを通す。

- 解放済み …… Redis（database.grant_achievement / revoke_achievement）
- 装着中 …… Discord のロール。実績の称号は同時に 1 つだけ。

付与しても Discord のロールは付けない（着けるのは本人）。これは今までの動きと同じ。
"""

from config import ACHIEVEMENT_ROLE_IDS
from database import (
    get_user_achievements,
    grant_achievement,
    revoke_achievement,
)


def worn_role_id(member) -> int | None:
    """いま称号にしている実績のロール ID（着けていなければ None）。"""
    for role in member.roles:
        if role.id in ACHIEVEMENT_ROLE_IDS:
            return role.id
    return None


def unlocked_role_ids(user_id: int) -> list[int]:
    """解放済みの実績（順番は決めておく。毎回同じ並びになる）。"""
    return sorted(get_user_achievements(user_id))


async def set_title(member, role_id: int | None) -> None:
    """称号を 1 つだけにする（None なら外す）。

    実績のロールを全部外してから、選んだものだけを付ける。着け替えの途中で失敗しても
    「2 つ付いている」状態になりにくい。
    """
    roles_to_remove = [
        role for role in member.roles if role.id in ACHIEVEMENT_ROLE_IDS
    ]
    if roles_to_remove:
        await member.remove_roles(*roles_to_remove)

    if role_id is None:
        return

    role = member.guild.get_role(role_id)
    if role is None:
        raise LookupError("実績のロールが見つかりません")

    await member.add_roles(role)


def grant(user_id: int, role_id: int) -> None:
    """実績を解放する（Discord のロールは付けない）。"""
    grant_achievement(user_id, role_id)


async def revoke(user_id: int, role_id: int, member=None) -> None:
    """実績を剥奪する。着けていたら、そのロールも外す。

    member は分かっているときだけ渡す（サーバーに居ない人でも、記録だけは消せる）。
    """
    revoke_achievement(user_id, role_id)

    if member is not None and worn_role_id(member) == role_id:
        role = member.guild.get_role(role_id)
        if role is not None:
            await member.remove_roles(role)
