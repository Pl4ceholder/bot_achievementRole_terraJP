import discord
from discord import app_commands
from discord.ext import commands
from cogs.mypage import MyPageView
from config import ACHIEVEMENT_ROLE_IDS
from database import grant_achievement, revoke_achievement


class AdminCog(commands.Cog):

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(
        name="help_admin", description="【管理者専用】ヘルプを表示します"
    )
    @app_commands.default_permissions(administrator=True)
    async def help_admin(self, interaction: discord.Interaction):
        embed = discord.Embed(
            title="🛠️ 管理者専用コマンド一覧",
            description="スラッシュコマンド（ `/` ）を入力すると候補が表示されます。",
            color=discord.Color.gold(),
        )
        embed.add_field(
            name="📌 マイページパネル設置",
            value=(
                "`/setup_mypage`\nユーザーが称号を着脱するためのマイページパネルを設置します。"
            ),
            inline=False,
        )
        embed.add_field(
            name="🎁 実績の付与（DB登録）",
            value=(
                "`/give_achievement`\n指定したユーザーに実績の獲得権限（DB登録）を与えます。"
            ),
            inline=False,
        )
        embed.add_field(
            name="🗑️ 実績の剥奪（DB削除）",
            value=(
                "`/take_achievement`\n指定したユーザーから実績の獲得権限を剥奪（DB削除）します。"
            ),
            inline=False,
        )
        embed.add_field(
            name="⚔️ 募集パネル設置",
            value=(
                "`/setup_recruitment`\nTerrariaマルチ募集パネルを設置します。"
            ),
            inline=False,
        )
        embed.set_footer(
            text="※これらのコマンドは「管理者権限」を持つユーザーのみ表示・実行可能です。"
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(
        name="give_achievement",
        description="【管理者専用】ユーザーに実績を付与",
    )
    @app_commands.describe(
        member="実績を付与するユーザーを選択",
        role="付与する実績ロールを選択",
    )
    @app_commands.default_permissions(administrator=True)
    async def give_achievement(
        self,
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

    @app_commands.command(
        name="take_achievement",
        description="【管理者専用】ユーザーから実績を剥奪",
    )
    @app_commands.describe(
        member="実績を剥奪するユーザーを選択",
        role="剥奪する実績ロールを選択",
    )
    @app_commands.default_permissions(administrator=True)
    async def take_achievement(
        self,
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

        if role in member.roles:
            try:
                await member.remove_roles(role)
            except Exception:
                pass

        await interaction.response.send_message(
            f"🗑️ {member.mention} から実績 **{role.name}** の獲得権限を剥奪しました。"
        )

    @app_commands.command(
        name="setup_mypage",
        description="【管理者専用】マイページ（称号設定）パネルを設置",
    )
    @app_commands.default_permissions(administrator=True)
    async def setup_mypage(self, interaction: discord.Interaction):
        embed = discord.Embed(
            title="👤 マイページ / 称号設定",
            description="下のボタンから獲得済みの実績称号（ロール）を着脱できます。\n※つけられる称号は同時に1つまでです。",
            color=discord.Color.blue(),
        )
        await interaction.channel.send(embed=embed, view=MyPageView())
        await interaction.response.send_message(
            "マイページパネルを設置しました！", ephemeral=True
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(AdminCog(bot))