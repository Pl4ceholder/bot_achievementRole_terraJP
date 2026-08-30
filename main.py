import os
import redis
import discord
from discord.ext import commands
from threading import Thread
from flask import Flask

# Renderのポート検知を通すための軽量Flaskアプリ
app = Flask("")

@app.route("/")
def home():
    return "Bot is alive!"

def run_http():
    # Renderが割り当てるPORT番号（デフォルト10000）で起動
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)


# スレッドでHTTPサーバーを並行起動
Thread(target=run_http).start()

# --- 以降、既存のdiscord.pyの処理 ---



# 1. 環境変数からRedis接続情報を取得（RenderのEnvironmentで設定）
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")
r = redis.from_url(REDIS_URL, decode_responses=True)

# 監視対象の実績ロールIDリスト（DiscordのロールID）
ACHIEVEMENT_ROLE_IDS = [
    1543500048245981275,  # 実績AロールID
    1543500112456585267,  # 実績BロールID
    1543500149798473828,  # 実績CロールID
]

intents = discord.Intents.default()
intents.members = True
bot = commands.Bot(command_prefix="!", intents=intents)

# --------------------------------------------------
# DB操作用関数
# --------------------------------------------------
def get_user_achievements(user_id: int) -> list[int]:
    """ユーザーが解放済みの実績ロールIDを取得"""
    role_ids = r.smembers(f"user:{user_id}:achievements")
    return [int(rid) for rid in role_ids]

def grant_achievement(user_id: int, role_id: int):
    """ユーザーに実績を解放（管理者が手動付与した際などに実行）"""
    r.sadd(f"user:{user_id}:achievements", role_id)


# --------------------------------------------------
# UIコンポーネント
# --------------------------------------------------
class TitleSelect(discord.ui.Select):
    def __init__(self, available_roles: list[discord.Role]):
        options = [
            discord.SelectOption(label=role.name, value=str(role.id))
            for role in available_roles
        ]
        options.append(discord.SelectOption(label="称号をつけない", value="none", description="すべての実績称号を外します"))
        super().__init__(placeholder="装着する称号を1つ選んでください", options=options)

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        user = interaction.user
        selected_value = self.values[0]

        # 現在ついているすべての実績系ロールを解除
        roles_to_remove = [role for role in user.roles if role.id in ACHIEVEMENT_ROLE_IDS]
        if roles_to_remove:
            await user.remove_roles(*roles_to_remove)

        # 選択されたロールを1つだけ付与
        if selected_value != "none":
            selected_role = interaction.guild.get_role(int(selected_value))
            if selected_role:
                await user.add_roles(selected_role)
                await interaction.followup.send(f"称号を **{selected_role.name}** に変更しました！", ephemeral=True)
                return

        await interaction.followup.send("称号を外しました。", ephemeral=True)


class TitleSelectView(discord.ui.View):
    def __init__(self, available_roles: list[discord.Role]):
        super().__init__(timeout=60)
        self.add_item(TitleSelect(available_roles))


class MyPageView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="称号着脱", style=discord.ButtonStyle.primary, custom_id="mypage_title_button")
    async def title_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        user = interaction.user
        guild = interaction.guild

        # DBからこのユーザーが獲得している実績ロールID一覧を取得
        unlocked_ids = get_user_achievements(user.id)

        # サーバー内に存在する該当ロールオブジェクトを取得
        available_roles = []
        for rid in unlocked_ids:
            role = guild.get_role(rid)
            if role:
                available_roles.append(role)

        if not available_roles:
            await interaction.response.send_message("装着できる実績称号を所持していません。", ephemeral=True)
            return

        view = TitleSelectView(available_roles)
        await interaction.response.send_message("設定したい称号を選択してください：", view=view, ephemeral=True)


# --------------------------------------------------
# 管理者用コマンド（実績の解放登録）
# --------------------------------------------------
@bot.command()
@commands.has_permissions(administrator=True)
async def give_achievement(ctx, member: discord.Member, role: discord.Role):
    """管理者がユーザーに「実績獲得権限」を与えるコマンド"""
    if role.id not in ACHIEVEMENT_ROLE_IDS:
        await ctx.send("指定されたロールは実績ロールリストに含まれていません。")
        return
    
    grant_achievement(member.id, role.id)
    await ctx.send(f"{member.mention} に実績 **{role.name}** を解放しました！（マイページから装着可能です）")


@bot.command()
@commands.has_permissions(administrator=True)
async def setup_mypage(ctx):
    """マイページパネル設置コマンド"""
    embed = discord.Embed(
        title="👤 マイページ / 称号設定",
        description="下のボタンから獲得済みの実績称号（ロール）を着脱できます。\n※つけられる称号は同時に1つまでです。",
        color=discord.Color.blue()
    )
    await ctx.send(embed=embed, view=MyPageView())
    await ctx.message.delete()

bot.run(os.getenv("DISCORD_TOKEN"))