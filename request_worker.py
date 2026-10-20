"""サイトから届いた依頼を、Discord に反映する。

サイトは Discord を触らない。Web の操作（実績の付与・剥奪、称号の着脱）は、サイトの
D1 に「依頼」として溜まる。Bot が 30 秒ごとに拾って、ここで初めて Discord に届く。
そのため、サイトに Bot のトークンを置かなくて済む。

反映できなかったときは、理由を短い日本語で返す。サイトの画面にそのまま出るので、
利用者は「何が起きたか」を見て、やり直せる。

この処理は 1 件ずつ呼ばれ、例外を投げない（1 件の失敗で、ほかの依頼まで止めない）。
"""

from datetime import datetime, timezone

import discord

from achievements import grant, revoke, set_title, unlocked_role_ids
from config import ACHIEVEMENT_ROLE_IDS
from web_sync import parse_iso

# 依頼が古すぎるときは当てない（サイト側の REQUEST_TTL_MINUTES と同じにする）。
# Bot が止まっている間に溜まった「称号を着ける」を、あとから当てないため。
REQUEST_TTL_MINUTES = 10


class RequestError(Exception):
    """利用者にそのまま見せてよい理由。"""


class RequestExpired(RequestError):
    """依頼が古すぎた（反映しない）。"""


def is_expired(requested_at, now: datetime) -> bool:
    """置かれてから、決めた時間を過ぎているか（読めない日時も、古い扱い）。"""
    moment = parse_iso(requested_at)
    if moment is None:
        return True

    return (now - moment).total_seconds() > REQUEST_TTL_MINUTES * 60


async def apply_request(guild, request: dict, now: datetime | None = None) -> tuple[str, str]:
    """依頼を 1 件反映して、(状態, 画面に出す理由) を返す。

    状態は done / failed / expired のどれか（サイト側の決まりに合わせる）。
    """
    now = now or datetime.now(timezone.utc)

    try:
        return "done", await _apply(guild, request, now)
    except RequestExpired as error:
        return "expired", str(error)
    except RequestError as error:
        return "failed", str(error)
    except discord.Forbidden:
        return (
            "failed",
            "Bot の権限が足りません（Bot のロールを、実績のロールより上にしてください）。",
        )
    except discord.NotFound:
        return "failed", "相手が見つかりませんでした（サーバーから出たかもしれません）。"
    except Exception as error:  # 予想していないもの。ログに残して、理由だけ返す
        print(f"依頼の反映でエラーが起きました（{request}）: {error}")
        return "failed", "Discord 側で失敗しました。時間をおいて、もう一度お試しください。"


async def _apply(guild, request: dict, now: datetime) -> str:
    if is_expired(request.get("requestedAt"), now):
        raise RequestExpired(
            "時間がかかりすぎたため、取り消しました。もう一度お試しください。"
        )

    kind = request.get("kind")
    action = request.get("action")
    user_id = to_int(request.get("userId"))
    role_id = to_int(request.get("roleId"))

    if user_id is None:
        raise RequestError("依頼の中身が正しくありません。")

    if kind == "title":
        return await _apply_title(guild, user_id, action, role_id)

    if kind == "unlock":
        return await _apply_unlock(guild, user_id, action, role_id)

    raise RequestError("知らない種類の依頼でした。")


async def _apply_title(guild, user_id: int, action, role_id: int | None) -> str:
    """称号の着脱。着けられるのは、本人が解放している実績だけ。"""
    member = await find_member(guild, user_id)

    if action == "clear":
        await set_title(member, None)
        return "称号を外しました。"

    if action != "set" or role_id is None:
        raise RequestError("知らない操作でした。")

    if role_id not in ACHIEVEMENT_ROLE_IDS:
        raise RequestError("知らない実績でした。")

    if role_id not in unlocked_role_ids(user_id):
        raise RequestError("その実績を持っていないため、称号にできません。")

    role = guild.get_role(role_id)
    if role is None:
        raise RequestError("実績のロールが Discord にありません。")

    await set_title(member, role_id)
    return f"称号を「{role.name}」に変更しました。"


async def _apply_unlock(guild, user_id: int, action, role_id: int | None) -> str:
    """実績の付与・剥奪。剥奪したときは、着けていればロールも外す。"""
    if role_id is None or role_id not in ACHIEVEMENT_ROLE_IDS:
        raise RequestError("知らない実績でした。")

    role = guild.get_role(role_id)
    if role is None:
        raise RequestError("実績のロールが Discord にありません。")

    if action == "grant":
        grant(user_id, role_id)
        return f"実績「{role.name}」を付与しました。"

    if action == "revoke":
        # サーバーに居ない人でも、解放の記録だけは消せる
        member = guild.get_member(user_id)
        await revoke(user_id, role_id, member)
        return f"実績「{role.name}」を剥奪しました。"

    raise RequestError("知らない操作でした。")


async def find_member(guild, user_id: int):
    """サーバーのメンバーを引く（キャッシュに無ければ Discord に聞く）。"""
    member = guild.get_member(user_id)
    if member is not None:
        return member

    try:
        return await guild.fetch_member(user_id)
    except discord.NotFound:
        raise RequestError("相手がサーバーにいません。") from None


def to_int(value) -> int | None:
    """数字にして返す。数字でなければ None（空文字も None）。"""
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
