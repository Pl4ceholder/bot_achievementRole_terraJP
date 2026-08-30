import os
from threading import Thread
import discord
from discord import app_commands
from discord.ext import commands
from flask import Flask
import redis

# Renderのポート検知を通すための軽量Flaskアプリ
app = Flask("")


@app.route("/")
def home():
    return "Bot is alive!"


def run_http():
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)


# スレッドでHTTPサーバーを並行起動
Thread(target=run_http).start()

# --------------------------------------------------
# Redis & Bot 設定
# --------------------------------------------------
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")
r = redis.from_url(REDIS_URL, decode_responses=True)

# 監視対象の実績ロールIDリスト
ACHIEVEMENT_ROLE_IDS = [
    1543500048245981275,  # 実績AロールID
    1543500112456585267,  # 実績BロールID
    1543500149798473828,  # 実績CロールID
]

intents = discord.Intents.default()
intents.members = True
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)


# --------------------------------------------------
# DB操作用関数
# --------------------------------------------------
def get_user_achievements(user_id: int) -> list[int]:
    """ユーザーが解放済みの実績ロールIDリストを取得"""
    role_ids = r.smembers(f"user:{user_id}:achievements")
    return [int(rid) for rid in role_ids]


def grant_achievement(user_id: int, role_id: int):
    """ユーザーに実績を解放（DBに追加）"""
    r.sadd(f"user:{user_id}:achievements", role_id)


def revoke_achievement(user_id: int, role_id: int):
    """ユーザーの実績を剥奪（DBから削除）"""
    r.srem(f"user:{user_id}:achievements", role_id)


# --------------------------------------------------
# UIコンポーネント
# --------------------------------------------------
class TitleSelect(discord.ui.Select):

    def __init__(self, available_roles: list[discord.Role]):
        options = [
            discord.SelectOption(label=role.name, value=str(role.id))
            for role in available_roles
        ]
        options.append(
            discord.SelectOption(
                label="称号をつけない",
                value="none",
                description="すべての実績称号を外します",
            )
        )
        super().__init__(
            placeholder="装着する称号を選択してください", options=options
        )

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        user = interaction.user
        selected_value = self.values[0]

        # 1. 選択ドロップダウンのメッセージを削除
        await interaction.delete_original_response()

        # 2. 現在ついているすべての実績系ロールを解除
        roles_to_remove = [
            role for role in user.roles if role.id in ACHIEVEMENT_ROLE_IDS
        ]
        if roles_to_remove:
            await user.remove_roles(*roles_to_remove)

        # 3. 選択されたロールを1つだけ付与
        if selected_value != "none":
            selected_role = interaction.guild.get_role(int(selected_value))
            if selected_role:
                await user.add_roles(selected_role)
                await interaction.followup.send(
                    f"称号を **{selected_role.name}** に変更しました！",
                    ephemeral=True,
                )
                return

        await interaction.followup.send("称号を外しました。", ephemeral=True)


class TitleSelectView(discord.ui.View):

    def __init__(self, available_roles: list[discord.Role]):
        super().__init__(timeout=60)
        self.add_item(TitleSelect(available_roles))


class MyPageView(discord.ui.View):

    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="称号着脱",
        style=discord.ButtonStyle.primary,
        custom_id="mypage_title_button",
    )
    async def title_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        user = interaction.user
        guild = interaction.guild

        # DBから解放済みの実績ロールIDを取得
        unlocked_ids = get_user_achievements(user.id)

        # ロールオブジェクトに変換
        available_roles = []
        for rid in unlocked_ids:
            role = guild.get_role(rid)
            if role:
                available_roles.append(role)

        if not available_roles:
            await interaction.response.send_message(
                "装着できる実績称号を所持していません。", ephemeral=True
            )
            return

        view = TitleSelectView(available_roles)
        await interaction.response.send_message(
            "設定したい称号を選択してください：", view=view, ephemeral=True
        )


# --------------------------------------------------
# Bot起動時イベント（スラッシュコマンドの同期）
# --------------------------------------------------
@bot.event
async def on_ready():
    # MyPageViewの永続化
    bot.add_view(MyPageView())

    # スラッシュコマンドをDiscordサーバー側と同期
    try:
        synced = await bot.tree.sync()
        print(f"Synced {len(synced)} command(s)")
    except Exception as e:
        print(f"Failed to sync commands: {e}")

    print(f"Logged in as {bot.user}")


# --------------------------------------------------
# 管理者用スラッシュコマンド (/ コマンド)
# --------------------------------------------------
@bot.tree.command(
    name="help_admin", description="【管理者専用】ヘルプを表示します"
)
@app_commands.default_permissions(administrator=True)
async def help_admin(interaction: discord.Interaction):
    embed = discord.Embed(
        title="🛠️ 管理者専用コマンド一覧",
        description="スラッシュコマンド（ `/` ）を入力すると候補が表示されます。",
        color=discord.Color.gold(),
    )

    embed.add_field(
        name="📌 マイページパネル設置",
        value="`/setup_mypage`\nユーザーが称号を着脱するためのマイページパネルを設置します。",
        inline=False,
    )

    embed.add_field(
        name="🎁 実績の付与（DB登録）",
        value=(
            "`/give_achievement`\n"
            "指定したユーザーに実績の獲得権限（DB登録）を与えます。"
        ),
        inline=False,
    )

    embed.add_field(
        name="🗑️ 実績の剥奪（DB削除）",
        value=(
            "`/take_achievement`\n"
            "指定したユーザーから実績の獲得権限を剥奪（DB削除）します。"
        ),
        inline=False,
    )

    embed.set_footer(
        text="※これらのコマンドは「管理者権限」を持つユーザーのみ表示・実行可能です。"
    )
    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(
    name="give_achievement", description="【管理者専用】ユーザーに実績を付与"
)
@app_commands.describe(
    member="実績を付与するユーザーを選択", role="付与する実績ロールを選択"
)
@app_commands.default_permissions(administrator=True)
async def give_achievement(
    interaction: discord.Interaction,
    member: discord.Member,
    role: discord.Role,
):
    if role.id not in ACHIEVEMENT_ROLE_IDS:
        await interaction.response.send_message(
            f"⚠️ **{role.name}** は実績対象のロールリストに含まれていません。",
            ephemeral=True,
        )
        return

    grant_achievement(member.id, role.id)
    await interaction.response.send_message(
        f"✅ {member.mention} に実績 **{role.name}** を付与しました！（マイページから装着可能です）"
    )


@bot.tree.command(
    name="take_achievement",
    description="【管理者専用】ユーザーから実績を剥奪",
)
@app_commands.describe(
    member="実績を剥奪するユーザーを選択", role="剥奪する実績ロールを選択"
)
@app_commands.default_permissions(administrator=True)
async def take_achievement(
    interaction: discord.Interaction,
    member: discord.Member,
    role: discord.Role,
):
    if role.id not in ACHIEVEMENT_ROLE_IDS:
        await interaction.response.send_message(
            f"⚠️ **{role.name}** は実績対象のロールリストに含まれていません。",
            ephemeral=True,
        )
        return

    revoke_achievement(member.id, role.id)

    # 現在装着中であれば実際のロールも剥奪
    if role in member.roles:
        try:
            await member.remove_roles(role)
        except Exception:
            pass

    await interaction.response.send_message(
        f"🗑️ {member.mention} から実績 **{role.name}** の獲得権限を剥奪しました。"
    )


@bot.tree.command(
    name="setup_mypage",
    description="【管理者専用】マイページ（称号設定）パネルを設置",
)
@app_commands.default_permissions(administrator=True)
async def setup_mypage(interaction: discord.Interaction):
    embed = discord.Embed(
        title="👤 マイページ / 称号設定",
        description="下のボタンから獲得済みの実績称号（ロール）を着脱できます。\n※つけられる称号は同時に1つまでです。",
        color=discord.Color.blue(),
    )
    # パネルを送信
    await interaction.channel.send(embed=embed, view=MyPageView())
    # 実行者への完了通知（本人のみ表示）
    await interaction.response.send_message(
        "マイページパネルを設置しました！", ephemeral=True
    )


bot.run(os.getenv("DISCORD_TOKEN"))