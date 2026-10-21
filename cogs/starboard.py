"""リアクションを集めた投稿を、スターボードのチャンネルへ載せる。

`STARBOARD_EMOJI` のリアクションが `STARBOARD_THRESHOLD` 個集まった投稿を、
`STARBOARD_CHANNEL_ID` のチャンネルへ 1 回だけコピーする。コピーしたあとも
リアクションの数を追いかけ、しきい値を下回ったらコピーを消す。

設定はすべて環境変数（Render の Environment）で行う。`STARBOARD_CHANNEL_ID` が
無いときは、この機能だけを止める（ほかの機能には影響しない）。

- 同じ投稿を二重に載せないように、Redis に「元の投稿 → スターボードの投稿」を覚える。
- Bot の投稿と、自分で自分の投稿につけたリアクションは数えない（設定で変更できる）。
- 元の投稿が消えたとき、リアクションがすべて消えたときも、スターボードから消す。
"""

import asyncio

import discord
from discord.ext import commands

from config import (
    STARBOARD_ALLOW_SELF_STAR,
    STARBOARD_CHANNEL_ID,
    STARBOARD_EMOJI,
    STARBOARD_IGNORE_BOTS,
    STARBOARD_IGNORE_CHANNEL_IDS,
    STARBOARD_THRESHOLD,
)
from database import (
    delete_starboard_entry,
    get_starboard_entry,
    get_starboard_source,
    save_starboard_entry,
)

IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".gif", ".webp")
MAX_ATTACHMENT_LINKS = 10


def _normalize_emoji_name(name) -> str:
    """絵文字の異体字セレクタ（️）の有無で取りこぼさないようにする。"""
    return (name or "").replace("\ufe0f", "").strip()


def _parse_emoji(value: str):
    try:
        return discord.PartialEmoji.from_str(value)
    except (TypeError, ValueError):
        return None


def same_emoji(left, configured: str) -> bool:
    """リアクションの絵文字が、設定した絵文字と同じか。

    `left` は `discord.Reaction.emoji`（Unicode なら str、カスタムなら Emoji）でも、
    リアクションイベントの `PartialEmoji` でもよい。
    """
    target = _parse_emoji(configured)
    if target is None:
        return False

    if target.id:
        return getattr(left, "id", None) == target.id

    if getattr(left, "id", None) is not None:
        return False

    name = left if isinstance(left, str) else getattr(left, "name", "")
    return _normalize_emoji_name(name) == _normalize_emoji_name(target.name)


def _is_image(attachment) -> bool:
    content_type = (getattr(attachment, "content_type", None) or "").lower()
    if content_type.startswith("image/"):
        return True

    filename = (getattr(attachment, "filename", "") or "").lower()
    return filename.endswith(IMAGE_EXTENSIONS)


def build_embed(message, count: int, guild_id: int) -> discord.Embed:
    """スターボードに出す埋め込みを作る。"""
    author = message.author
    display_name = getattr(author, "display_name", None) or getattr(
        author, "name", "名前不明"
    )

    embed = discord.Embed(
        title=f"{STARBOARD_EMOJI} 秀逸な投稿",
        color=discord.Color.gold(),
        timestamp=getattr(message, "created_at", None),
    )
    avatar = getattr(author, "display_avatar", None)
    avatar_url = getattr(avatar, "url", None)
    if avatar_url:
        embed.set_author(name=display_name, icon_url=avatar_url)
    else:
        embed.set_author(name=display_name)

    content = (getattr(message, "content", "") or "").strip()
    if content:
        embed.description = content[:4096]

    attachments = list(getattr(message, "attachments", []) or [])
    images = [attachment for attachment in attachments if _is_image(attachment)]
    if images:
        embed.set_image(url=images[0].url)

    linked = [attachment for attachment in attachments if attachment not in images[:1]]
    if linked:
        lines = [
            f"[{getattr(attachment, 'filename', 'ファイル')}]({attachment.url})"
            for attachment in linked[:MAX_ATTACHMENT_LINKS]
        ]
        if len(linked) > MAX_ATTACHMENT_LINKS:
            lines.append(f"ほか {len(linked) - MAX_ATTACHMENT_LINKS} 件")
        embed.add_field(name="添付ファイル", value="\n".join(lines)[:1024], inline=False)

    source_embeds = list(getattr(message, "embeds", []) or [])
    if source_embeds:
        source = source_embeds[0]
        parts = []
        if getattr(source, "title", None):
            parts.append(f"**{source.title}**")
        if getattr(source, "url", None):
            parts.append(source.url)
        if getattr(source, "description", None):
            parts.append(source.description[:500])
        if parts:
            embed.add_field(name="引用", value="\n".join(parts)[:1024], inline=False)

    if not embed.description and not images and not source_embeds:
        embed.description = "（本文なし）"

    channel = getattr(message, "channel", None)
    channel_id = getattr(channel, "id", None)
    if channel_id:
        link = (
            f"https://discord.com/channels/{guild_id}/{channel_id}/{message.id}"
        )
        embed.add_field(name="元の投稿", value=f"[投稿を開く]({link})", inline=False)

    embed.set_footer(
        text=f"{STARBOARD_EMOJI} {count} 件 / {STARBOARD_THRESHOLD} 件で登録"
    )
    return embed


async def count_stars(message, emoji: str) -> int:
    """設定したリアクションを付けた、重複しない人の数を返す。

    Bot と（既定では）投稿者本人は数えない。`STARBOARD_ALLOW_SELF_STAR` を
    有効にすると、投稿者本人も 1 人として数える。
    """
    reaction = next(
        (
            reaction
            for reaction in getattr(message, "reactions", []) or []
            if same_emoji(reaction.emoji, emoji)
        ),
        None,
    )
    if reaction is None:
        return 0

    # 総数がしきい値より少なければ、誰を除いても届かない。ユーザー一覧を読まずに済ませる。
    total = getattr(reaction, "count", None)
    if total is not None and total < STARBOARD_THRESHOLD:
        return total

    author_id = getattr(getattr(message, "author", None), "id", None)
    count = 0
    async for user in reaction.users():
        if getattr(user, "bot", False):
            continue
        if not STARBOARD_ALLOW_SELF_STAR and getattr(user, "id", None) == author_id:
            continue
        count += 1

    return count


class StarboardCog(commands.Cog):
    """スターボードのリアクションを監視する。"""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._locks: dict[int, asyncio.Lock] = {}

    async def cog_load(self):
        if STARBOARD_CHANNEL_ID is None:
            print(
                "スターボードは設定されていないので動かしません"
                "（STARBOARD_CHANNEL_ID）。"
            )
            return

        print(
            f"スターボードを有効にしました（{STARBOARD_EMOJI} "
            f"{STARBOARD_THRESHOLD} 件 → <#{STARBOARD_CHANNEL_ID}>）。"
        )

    def _lock_for(self, message_id: int) -> asyncio.Lock:
        """同じ投稿へのリアクションを、1 つずつ順番に処理する。"""
        lock = self._locks.get(message_id)
        if lock is not None:
            return lock

        # リアクションを受けた投稿ぶんだけ増える。使い終わったものは、ときどき捨てる。
        if len(self._locks) >= 1000:
            self._locks = {
                key: value for key, value in self._locks.items() if value.locked()
            }

        lock = asyncio.Lock()
        self._locks[message_id] = lock
        return lock

    async def _fetch_message(self, guild, channel_id: int, message_id: int):
        channel = guild.get_channel(channel_id)
        if channel is None:
            try:
                channel = await guild.fetch_channel(channel_id)
            except (discord.Forbidden, discord.NotFound, discord.HTTPException):
                return None

        try:
            return await channel.fetch_message(message_id)
        except (discord.Forbidden, discord.NotFound, discord.HTTPException):
            return None

    async def _get_channel(self, channel_id: int):
        channel = self.bot.get_channel(channel_id)
        if channel is not None:
            return channel

        try:
            return await self.bot.fetch_channel(channel_id)
        except (discord.Forbidden, discord.NotFound, discord.HTTPException):
            return None

    async def _post_or_update(self, guild, message, count: int, entry=None) -> None:
        channel = await self._get_channel(STARBOARD_CHANNEL_ID)
        if channel is None:
            print(
                "スターボードのチャンネルを開けませんでした"
                f"（{STARBOARD_CHANNEL_ID}）。"
            )
            return

        embed = build_embed(message, count, guild.id)
        allowed_mentions = discord.AllowedMentions.none()

        if entry:
            try:
                posted_id = int(entry.get("starboard_message_id") or 0)
            except (TypeError, ValueError):
                posted_id = 0

            if posted_id:
                try:
                    posted = await channel.fetch_message(posted_id)
                except discord.NotFound:
                    posted = None
                except discord.HTTPException as error:
                    # 消えたのか、権限が無いのか分からないときは、重複を避けて何もしない。
                    print(f"スターボードの投稿を開けませんでした: {error}")
                    return

                if posted is not None:
                    try:
                        await posted.edit(
                            embed=embed, allowed_mentions=allowed_mentions
                        )
                    except discord.HTTPException as error:
                        print(f"スターボードの投稿を更新できませんでした: {error}")
                        return

                    save_starboard_entry(
                        message.id, posted.id, channel.id, count
                    )
                    return

        try:
            posted = await channel.send(
                embed=embed, allowed_mentions=allowed_mentions
            )
        except discord.HTTPException as error:
            print(f"スターボードへ投稿できませんでした: {error}")
            return

        save_starboard_entry(message.id, posted.id, channel.id, count)

    async def _remove(self, message_id: int, entry=None) -> None:
        entry = entry or get_starboard_entry(message_id)
        if not entry:
            return

        try:
            channel_id = int(entry.get("starboard_channel_id") or STARBOARD_CHANNEL_ID)
        except (TypeError, ValueError):
            channel_id = STARBOARD_CHANNEL_ID

        try:
            posted_id = int(entry.get("starboard_message_id") or 0)
        except (TypeError, ValueError):
            posted_id = 0

        channel = await self._get_channel(channel_id) if channel_id else None
        if channel is not None and posted_id:
            try:
                posted = await channel.fetch_message(posted_id)
            except discord.NotFound:
                posted = None
            except discord.HTTPException as error:
                # 消せないまま記録だけ消すと、次に載せたときに二重になる。記録は残す。
                print(f"スターボードの投稿を開けませんでした: {error}")
                return

            if posted is not None:
                try:
                    await posted.delete()
                except discord.NotFound:
                    pass
                except discord.HTTPException as error:
                    print(f"スターボードの投稿を消せませんでした: {error}")
                    return

        delete_starboard_entry(message_id)

    async def _handle_reaction(self, payload, added: bool) -> None:
        if STARBOARD_CHANNEL_ID is None or payload.guild_id is None:
            return
        if payload.channel_id in {STARBOARD_CHANNEL_ID, *STARBOARD_IGNORE_CHANNEL_IDS}:
            return
        if not same_emoji(payload.emoji, STARBOARD_EMOJI):
            return

        # Bot 自身やほかの Bot のリアクションは数えない（数える前に軽く弾く）。
        if getattr(payload, "member", None) is not None and payload.member.bot:
            return
        if self.bot.user is not None and payload.user_id == self.bot.user.id:
            return

        # リアクションが減っただけなら、まだ載せていない投稿を読みに行く必要はない。
        # ただし、追加の処理とすれ違わないように、判断はロックの中で行う。

        guild = self.bot.get_guild(payload.guild_id)
        if guild is None:
            return

        async with self._lock_for(payload.message_id):
            entry = get_starboard_entry(payload.message_id)
            if not added and entry is None:
                return

            message = await self._fetch_message(
                guild, payload.channel_id, payload.message_id
            )
            if message is None:
                return

            if STARBOARD_IGNORE_BOTS and getattr(message.author, "bot", False):
                if entry is not None:
                    await self._remove(message.id, entry)
                return

            count = await count_stars(message, STARBOARD_EMOJI)
            if count >= STARBOARD_THRESHOLD:
                await self._post_or_update(guild, message, count, entry)
            elif entry is not None:
                await self._remove(message.id, entry)

    async def _handle_edit(self, payload) -> None:
        if STARBOARD_CHANNEL_ID is None or payload.guild_id is None:
            return
        if payload.channel_id in {STARBOARD_CHANNEL_ID, *STARBOARD_IGNORE_CHANNEL_IDS}:
            return
        if get_starboard_entry(payload.message_id) is None:
            return

        guild = self.bot.get_guild(payload.guild_id)
        if guild is None:
            return

        async with self._lock_for(payload.message_id):
            entry = get_starboard_entry(payload.message_id)
            if entry is None:
                return

            message = await self._fetch_message(
                guild, payload.channel_id, payload.message_id
            )
            if message is None:
                return

            if STARBOARD_IGNORE_BOTS and getattr(message.author, "bot", False):
                await self._remove(message.id, entry)
                return

            count = await count_stars(message, STARBOARD_EMOJI)
            if count >= STARBOARD_THRESHOLD:
                await self._post_or_update(guild, message, count, entry)
            else:
                await self._remove(message.id, entry)

    async def _handle_deleted_message(self, message_id: int) -> None:
        if STARBOARD_CHANNEL_ID is None:
            return

        # スターボード側の投稿が消されたときは、記録だけ消す（勝手に載せ直さない）。
        source_id = get_starboard_source(message_id)
        if source_id is not None:
            async with self._lock_for(source_id):
                # 待っているあいだに、元の投稿の削除が先に片付けたかもしれない。
                source_id = get_starboard_source(message_id)
                if source_id is not None:
                    delete_starboard_entry(source_id)
            return

        async with self._lock_for(message_id):
            entry = get_starboard_entry(message_id)
            if entry is not None:
                await self._remove(message_id, entry)

    async def _handle_clear(self, message_id: int) -> None:
        if STARBOARD_CHANNEL_ID is None:
            return

        async with self._lock_for(message_id):
            entry = get_starboard_entry(message_id)
            if entry is not None:
                await self._remove(message_id, entry)

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload):
        try:
            await self._handle_reaction(payload, added=True)
        except Exception as error:
            print(f"スターボードのリアクション処理でエラーが起きました: {error}")

    @commands.Cog.listener()
    async def on_raw_reaction_remove(self, payload):
        try:
            await self._handle_reaction(payload, added=False)
        except Exception as error:
            print(f"スターボードのリアクション処理でエラーが起きました: {error}")

    @commands.Cog.listener()
    async def on_raw_reaction_clear(self, payload):
        try:
            await self._handle_clear(payload.message_id)
        except Exception as error:
            print(f"スターボードの後片付けでエラーが起きました: {error}")

    @commands.Cog.listener()
    async def on_raw_reaction_clear_emoji(self, payload):
        try:
            if same_emoji(payload.emoji, STARBOARD_EMOJI):
                await self._handle_clear(payload.message_id)
        except Exception as error:
            print(f"スターボードの後片付けでエラーが起きました: {error}")

    @commands.Cog.listener()
    async def on_raw_message_edit(self, payload):
        try:
            await self._handle_edit(payload)
        except Exception as error:
            print(f"スターボードの更新でエラーが起きました: {error}")

    @commands.Cog.listener()
    async def on_raw_message_delete(self, payload):
        try:
            await self._handle_deleted_message(payload.message_id)
        except Exception as error:
            print(f"スターボードの後片付けでエラーが起きました: {error}")

    @commands.Cog.listener()
    async def on_raw_bulk_message_delete(self, payload):
        try:
            for message_id in payload.message_ids:
                await self._handle_deleted_message(message_id)
        except Exception as error:
            print(f"スターボードの後片付けでエラーが起きました: {error}")


async def setup(bot: commands.Bot):
    await bot.add_cog(StarboardCog(bot))
