import discord
from discord.ext import commands
from achievements import set_title
from database import get_user_achievements


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

        await interaction.delete_original_response()

        role_id = None if selected_value == "none" else int(selected_value)

        try:
            await set_title(user, role_id)
        except (discord.HTTPException, LookupError):
            await interaction.followup.send(
                "称号を変更できませんでした。時間をおいてお試しください。",
                ephemeral=True,
            )
            return

        if role_id is None:
            await interaction.followup.send("称号を外しました。", ephemeral=True)
            return

        role = interaction.guild.get_role(role_id)
        await interaction.followup.send(
            f"称号を **{role.name}** に変更しました！",
            ephemeral=True,
        )


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

        unlocked_ids = get_user_achievements(user.id)

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


class MyPageCog(commands.Cog):

    def __init__(self, bot: commands.Bot):
        self.bot = bot


async def setup(bot: commands.Bot):
    await bot.add_cog(MyPageCog(bot))