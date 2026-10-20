"""募集の管理コマンド（`/recruit_list`・`/recruit_close`）を、Discord に繋がずに確かめる。

    pip install -r requirements.txt        # discord.py が要ります
    python tools/check_recruit_commands.py

確かめること:

- コマンドが、管理者専用として正しく組み立てられているか
- `/recruit_list` が、いま立っている募集を一覧にできるか（募集のチャンネル以外は出さない）
- `/recruit_close` が、スレッドを閉じられるか／閉じてはいけないものを断れるか
- 締め切ったあとに、サイトへの同期を 1 回呼ぶか

Discord には繋がない（ダミーを渡す）。合っていなければ終了コード 1 で終わる。
"""

import asyncio
import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import discord  # noqa: E402
from discord import app_commands  # noqa: E402

import cogs.recruitment as recruitment  # noqa: E402
from check_web_sync import sample_guild  # noqa: E402
from config import CHANNEL_VANILLA, WEB_SYNC_BASE  # noqa: E402

GUILD_ID = 999000111222333444


def http_error(cls, status=404, reason="Not Found", message="Unknown Channel"):
    """本物と同じ形の Discord のエラーを作る。"""
    response = types.SimpleNamespace(status=status, reason=reason)
    return cls(response, {"message": message, "code": 10003})


class FakeResponse:
    def __init__(self):
        self.deferred = None

    async def defer(self, ephemeral=False, thinking=False):
        self.deferred = ephemeral


class FakeFollowup:
    def __init__(self):
        self.messages = []

    async def send(self, content=None, embed=None, ephemeral=False):
        self.messages.append((content, embed))

    def texts(self):
        return [content or "" for content, _ in self.messages]

    def embeds(self):
        return [embed for _, embed in self.messages if embed is not None]


class FakeInteraction:
    def __init__(self, guild):
        self.guild = guild
        self.user = types.SimpleNamespace(mention="@管理者", display_name="管理者")
        self.response = FakeResponse()
        self.followup = FakeFollowup()


class FakeThread:
    """本物の `discord.Thread` の代わり（コマンドが使うところだけ）。"""

    def __init__(self, thread_id, parent_id, archived=False, name="【バニラマルチ/PC】テストの募集"):
        self.id = thread_id
        self.parent_id = parent_id
        self.archived = archived
        self.name = name
        self.mention = f"<#{thread_id}>"
        self.sent = []
        self.edits = []

    async def send(self, content):
        self.sent.append(content)

    async def edit(self, **kwargs):
        self.edits.append(kwargs)
        if kwargs.get("archived"):
            self.archived = True


class FakeDiscord:
    """discord モジュールの代わり。`Thread` だけダミーにして、isinstance を通す。"""

    def __init__(self, thread_class):
        self._thread_class = thread_class

    def __getattr__(self, name):
        if name == "Thread":
            return self._thread_class
        return getattr(discord, name)


class FakeCloseGuild:
    """`fetch_channel` だけを持つサーバー。"""

    def __init__(self, threads):
        self.id = GUILD_ID
        self._threads = threads

    async def fetch_channel(self, channel_id):
        target = self._threads.get(channel_id)
        if target is None:
            raise http_error(discord.NotFound)
        return target


class FakeSyncCog:
    """サイトへの同期（cogs/sync.py）の代わり。呼ばれた回数を数える。"""

    def __init__(self):
        self.calls = 0

    async def sync_once(self):
        self.calls += 1


class FakeBot:
    def __init__(self, sync_cog=None):
        self._sync_cog = sync_cog

    def get_cog(self, name):
        return self._sync_cog if name == "SiteSyncCog" else None


def make_cog(sync_cog=None) -> recruitment.RecruitmentCog:
    return recruitment.RecruitmentCog(FakeBot(sync_cog))


def embed_text(embed) -> str:
    parts = [embed.title or "", embed.description or ""]
    parts += [f"{field.name}\n{field.value}" for field in embed.fields]
    parts.append((embed.footer.text or "") if embed.footer else "")
    return "\n".join(parts)


async def check_registration() -> bool:
    """コマンドが、管理者専用として組み立てられているか。"""
    listed = recruitment.RecruitmentCog.recruit_list
    close = recruitment.RecruitmentCog.recruit_close

    return (
        isinstance(listed, app_commands.Command)
        and listed.name == "recruit_list"
        and listed.default_permissions.administrator
        and isinstance(close, app_commands.Command)
        and close.name == "recruit_close"
        and close.default_permissions.administrator
        and [parameter.display_name for parameter in close.parameters] == ["recruitment"]
        and bool(getattr(close.parameters[0], "autocomplete", None))
    )


async def check_list() -> bool:
    """`/recruit_list` が、いま立っている募集だけを一覧にするか。"""
    cog = make_cog()
    interaction = FakeInteraction(sample_guild())

    await recruitment.RecruitmentCog.recruit_list.callback(cog, interaction)

    embeds = interaction.followup.embeds()
    if len(embeds) != 1:
        return False

    text = embed_text(embeds[0])

    return (
        "2 件" in text
        # 募集のチャンネルではないスレッド（雑談）は出さない
        and "これは送られないはず" not in text
        and "バニラマルチ / PC" in text
        and "モッドマルチ / Switch" in text
        and f"https://discord.com/channels/{GUILD_ID}/1111222233334444551" in text
        and "書き込み 6 件" in text
        # 残り時間は、確かめる時刻によって「あと…」にも「過ぎました」にもなる
        and ("あと" in text or "流れる時刻を過ぎました" in text)
        and f"{WEB_SYNC_BASE}/recruit" in text
        and interaction.response.deferred is True  # 先に「考え中」を返しているか
    )


async def check_autocomplete() -> bool:
    """候補が、いま立っている募集（募集のチャンネルだけ）から出るか。"""
    cog = make_cog()
    interaction = FakeInteraction(sample_guild())

    all_names = [choice.name for choice in await cog.recruit_autocomplete(interaction, "")]
    tmod_names = [choice.name for choice in await cog.recruit_autocomplete(interaction, "モッド")]
    none_names = [choice.name for choice in await cog.recruit_autocomplete(interaction, "ありえない")]
    values = [choice.value for choice in await cog.recruit_autocomplete(interaction, "")]

    return (
        all_names == ["【バニラマルチ/PC】テスト太郎の募集", "【モッドマルチ/Switch】テスト花子の募集"]
        and tmod_names == ["【モッドマルチ/Switch】テスト花子の募集"]
        and none_names == []
        and values == ["1111222233334444551", "1111222233334444552"]
    )


async def run_close(threads, value):
    """`/recruit_close` を 1 回動かして、やり取りを返す。"""
    sync_cog = FakeSyncCog()
    cog = make_cog(sync_cog)
    interaction = FakeInteraction(FakeCloseGuild(threads))

    await recruitment.RecruitmentCog.recruit_close.callback(cog, interaction, value)

    return interaction, sync_cog


async def check_close() -> bool:
    """`/recruit_close` が、締め切れるか／締め切ってはいけないものを断れるか。"""
    live = FakeThread(1111222233334444551, CHANNEL_VANILLA)
    other = FakeThread(1111222233334444559, 999999999999999999)  # 募集のチャンネルではない
    closed = FakeThread(1111222233334444558, CHANNEL_VANILLA, archived=True)

    threads = {live.id: live, other.id: other, closed.id: closed}

    # 1. 募集のスレッドは、書き込みを止めて閉じる
    interaction, sync_cog = await run_close(threads, str(live.id))
    ok_live = (
        len(live.sent) == 1
        and "締め切られました" in live.sent[0]
        and live.edits == [{"archived": True, "locked": True}]
        and sync_cog.calls == 1
        and any("締め切りました" in text for text in interaction.followup.texts())
    )

    # 2. 募集のチャンネルではないスレッドは断る（閉じない）
    interaction, sync_cog = await run_close(threads, str(other.id))
    ok_other = (
        other.edits == []
        and sync_cog.calls == 0
        and any("募集のスレッドではありません" in text for text in interaction.followup.texts())
    )

    # 3. すでに流れているものは、何もしない
    interaction, _ = await run_close(threads, str(closed.id))
    ok_closed = closed.edits == [] and any(
        "すでに締め切られています" in text for text in interaction.followup.texts()
    )

    # 4. 見つからない募集は、そう伝える
    interaction, _ = await run_close(threads, "1111222233334444550")
    ok_missing = any(
        "見つかりませんでした" in text for text in interaction.followup.texts()
    )

    # 5. 数字でない入力（手で打った文字）は断る
    interaction, _ = await run_close(threads, "よくわからない")
    ok_invalid = any(
        "候補から募集を選んでください" in text for text in interaction.followup.texts()
    )

    return ok_live and ok_other and ok_closed and ok_missing and ok_invalid


async def main() -> None:
    # `discord.Thread` をダミーに差し替えて、締め切りの流れも確かめられるようにする
    original = recruitment.discord
    recruitment.discord = FakeDiscord(FakeThread)

    try:
        checks = (
            ("コマンドが管理者専用として組み立てられている", await check_registration()),
            ("/recruit_list がいま立っている募集だけを並べる", await check_list()),
            ("候補が、いま立っている募集（募集のチャンネルだけ）から出る", await check_autocomplete()),
            ("/recruit_close が締め切れる／締め切ってはいけないものを断れる", await check_close()),
        )
    finally:
        recruitment.discord = original

    for title, ok in checks:
        print(("OK   " if ok else "ダメ  ") + title)

    if not all(ok for _, ok in checks):
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())
