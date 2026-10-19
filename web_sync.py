"""サイト（Terraria JP 公式サイト）へ送る中身を組み立てる。

このファイルは discord を import しない。そのため Bot のトークンが無くても、
`tools/check_web_sync.py` で中身を組み立てて確かめられる。

いま送っているのは 2 つ。

- マルチ募集…… Discord では「メッセージ＋スレッド」で、24 時間で自動的に流れる。
  サイトはその写しを持ち、いま立っているものだけを出す。
- イベント…… Discord の「イベント」（サーバーの予定）。終わったものは
  Discord が返さなくなるので、終了の同期はしない。
"""

import logging
from datetime import timezone

logger = logging.getLogger(__name__)

# 募集の embed の見出し（cogs/recruitment.py）を探すためのキーワード。
# 絵文字や細かい文言が変わっても壊れないよう、絵文字を外した語で探す。
KEY_MODE = "種類"
KEY_PLATFORM = "機種"
KEY_VC = "VC"
KEY_CONNECTION = "SteamID"
KEY_MESSAGE = "メッセージ"


def find_field(fields: list[tuple[str, str]], keyword: str) -> str:
    """embed の見出しをキーワードで探して、中身を返す。無ければ空文字。"""
    for name, value in fields:
        if keyword in name:
            return value
    return ""


def to_iso(value) -> str:
    """Discord の時刻を ISO 8601（UTC）の文字列にする。読めなければ空文字。"""
    try:
        return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    except (AttributeError, ValueError):
        return ""


def vc_name(guild, value: str) -> str:
    """VC は `<#123...>` の形で入っていることがある。名前が引ければ名前にする。"""
    if value.startswith("<#") and value.endswith(">"):
        try:
            channel = guild.get_channel(int(value[2:-1]))
        except (TypeError, ValueError):
            channel = None
        if channel is not None:
            return channel.name
    return value


def build_item(*, thread, message, guild, created_at) -> dict:
    """スレッド 1 本を、サイトへ送る 1 件分にする。"""
    embed = message.embeds[0]
    fields = [(field.name, field.value) for field in getattr(embed, "fields", [])]

    author = getattr(embed, "author", None)
    author_name = getattr(author, "name", "") or getattr(
        message.author, "display_name", ""
    )
    author_icon = getattr(author, "icon_url", "") or ""

    return {
        "id": str(thread.id),
        "channelId": str(thread.parent_id or ""),
        "messageId": str(message.id),
        "mode": find_field(fields, KEY_MODE).strip(),
        "platform": find_field(fields, KEY_PLATFORM).strip(),
        "vc": vc_name(guild, find_field(fields, KEY_VC).strip()),
        "connection": find_field(fields, KEY_CONNECTION).strip(),
        "message": find_field(fields, KEY_MESSAGE).strip(),
        "authorId": str(getattr(message.author, "id", "") or ""),
        "authorName": str(author_name),
        "authorIcon": str(author_icon),
        "startedAt": to_iso(created_at),
    }


async def fetch_starter_message(thread):
    """スレッドの元になった募集メッセージを取る（スレッド ID ＝ 元メッセージ ID）。"""
    try:
        return await thread.fetch_message(thread.id)
    except Exception as error:  # 1 本読めなくても、他の募集は送る
        logger.warning("募集メッセージを読めませんでした: %s (%s)", thread.id, error)
        return None


async def collect_recruitments(guild, channel_ids) -> list[dict]:
    """いま立っている募集を集める（新しい順）。"""
    threads = [thread for thread in guild.active_threads() if thread.parent_id in channel_ids]
    threads.sort(key=lambda thread: thread.created_at, reverse=True)

    items = []
    for thread in threads:
        message = await fetch_starter_message(thread)
        if message is None or not message.embeds:
            continue
        items.append(
            build_item(
                thread=thread,
                message=message,
                guild=guild,
                created_at=thread.created_at,
            )
        )
    return items


def build_payload(guild_id, items: list[dict]) -> dict:
    """募集を、サイトへ送る形にする。"""
    return {"guildId": str(guild_id), "recruitments": items}


def event_location(event) -> str:
    """場所を決める。外部のイベントは場所の名前、ボイスやステージはチャンネル名。"""
    location = getattr(event, "location", None)
    if location:
        return str(location)

    channel = getattr(event, "channel", None)
    if channel is not None:
        return str(getattr(channel, "name", "") or "")

    return ""


def is_upcoming_event(event) -> bool:
    """これから、または開催中のイベントか。終わったもの・取り消されたものは送らない。"""
    return getattr(event.status, "name", "") not in {"completed", "canceled"}


def build_event(event) -> dict:
    """Discord のイベント 1 件を、サイトへ送る 1 件分にする。"""
    end_time = getattr(event, "end_time", None)
    cover = getattr(event, "cover_image", None)

    return {
        "id": str(event.id),
        "name": str(event.name or ""),
        "description": str(event.description or ""),
        "location": event_location(event),
        "coverUrl": str(getattr(cover, "url", "") or ""),
        "url": str(getattr(event, "url", "") or ""),
        "userCount": int(getattr(event, "user_count", 0) or 0),
        "startAt": to_iso(event.start_time),
        "endAt": to_iso(end_time) if end_time else "",
    }


async def collect_events(guild) -> list[dict]:
    """これからのイベントを集める（始まる順）。"""
    events = await guild.fetch_scheduled_events(with_counts=False)

    items = [build_event(event) for event in events if is_upcoming_event(event)]
    items.sort(key=lambda item: item["startAt"])
    return items


def build_events_payload(guild_id, items: list[dict]) -> dict:
    """イベントを、サイトへ送る形にする。"""
    return {"guildId": str(guild_id), "events": items}
