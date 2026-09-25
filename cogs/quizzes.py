import random
import unicodedata

import discord
from discord import app_commands
from discord.ext import commands

from database import (
    create_quiz,
    delete_quiz,
    get_quiz_categories,
    get_quizzes,
)


def _split_lines(value: str) -> list[str]:
    return [line.strip() for line in value.splitlines() if line.strip()]


def _normalize_answer(value: str) -> str:
    return unicodedata.normalize("NFKC", value).strip().casefold()


class QuizCreateModal(discord.ui.Modal, title="クイズ登録"):
    question = discord.ui.TextInput(label="問題文", max_length=1000)
    category = discord.ui.TextInput(
        label="カテゴリ（任意）", required=False, max_length=100
    )
    entries = discord.ui.TextInput(
        label="選択肢 または 正解（1行に1つ）",
        style=discord.TextStyle.paragraph,
        placeholder="選択式: 選択肢を改行で入力\n記述式: 正解を改行で入力",
        max_length=4000,
    )
    correct = discord.ui.TextInput(
        label="選択式の正解（選択肢と完全一致）",
        required=False,
        max_length=100,
    )

    def __init__(self, kind: str):
        super().__init__(title="選択式クイズ登録" if kind == "choice" else "記述式クイズ登録")
        self.kind = kind
        if kind == "text":
            self.remove_item(self.correct)

    async def on_submit(self, interaction: discord.Interaction):
        entries = _split_lines(self.entries.value)
        if self.kind == "choice":
            choices = entries
            answer = self.correct.value.strip()
            if len(choices) < 2 or len(choices) > 25:
                await interaction.response.send_message(
                    "選択式は選択肢を2〜25個、1行に1つ入力してください。", ephemeral=True
                )
                return
            if not answer or answer not in choices:
                await interaction.response.send_message(
                    "正解は選択肢の中から、同じ表記で入力してください。", ephemeral=True
                )
                return
            answers = [answer]
        else:
            choices = []
            answers = entries
            if not answers:
                await interaction.response.send_message(
                    "記述式の正解を1行以上入力してください。", ephemeral=True
                )
                return

        quiz_id = create_quiz(
            self.question.value.strip(),
            self.kind,
            self.category.value.strip() or None,
            choices,
            answers,
        )
        await interaction.response.send_message(
            f"クイズを登録しました。ID: `{quiz_id}`", ephemeral=True
        )


class TextAnswerModal(discord.ui.Modal, title="クイズに回答"):
    answer = discord.ui.TextInput(label="回答", max_length=500)

    def __init__(self, quiz: dict):
        super().__init__()
        self.quiz = quiz

    async def on_submit(self, interaction: discord.Interaction):
        correct = any(
            _normalize_answer(self.answer.value) == _normalize_answer(answer)
            for answer in self.quiz["answers"]
        )
        result = "✅ 正解です！" if correct else "❌ 不正解です。"
        await interaction.response.send_message(
            f"{result}\n正解: {' / '.join(self.quiz['answers'])}", ephemeral=True
        )


class QuizSessionView(discord.ui.View):
    def __init__(self, quiz: dict):
        super().__init__(timeout=300)
        self.quiz = quiz
        if quiz["kind"] == "choice":
            self.add_item(QuizAnswerSelect(quiz))
        else:
            self.add_item(TextAnswerButton(quiz))


class QuizAnswerSelect(discord.ui.Select):
    def __init__(self, quiz: dict):
        self.quiz = quiz
        options = [discord.SelectOption(label=choice[:100], value=str(index)) for index, choice in enumerate(quiz["choices"])]
        super().__init__(placeholder="回答を選んでください", options=options)

    async def callback(self, interaction: discord.Interaction):
        answer = self.quiz["choices"][int(self.values[0])]
        correct = _normalize_answer(answer) == _normalize_answer(self.quiz["answers"][0])
        result = "✅ 正解です！" if correct else "❌ 不正解です。"
        await interaction.response.send_message(
            f"{result}\n正解: {self.quiz['answers'][0]}", ephemeral=True
        )


class TextAnswerButton(discord.ui.Button):
    def __init__(self, quiz: dict):
        super().__init__(label="回答する", style=discord.ButtonStyle.primary)
        self.quiz = quiz

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.send_modal(TextAnswerModal(self.quiz))


class QuizCategorySelect(discord.ui.Select):
    def __init__(self, categories: list[str]):
        options = [discord.SelectOption(label=category[:100], value=category) for category in categories[:25]]
        super().__init__(placeholder="出題するカテゴリを選んでください", options=options)

    async def callback(self, interaction: discord.Interaction):
        await send_random_quiz(interaction, self.values[0])


class QuizCategoryView(discord.ui.View):
    def __init__(self, categories: list[str]):
        super().__init__(timeout=60)
        self.add_item(QuizCategorySelect(categories))


class QuizKindSelect(discord.ui.Select):
    def __init__(self):
        super().__init__(
            placeholder="登録するクイズ形式を選んでください",
            options=[
                discord.SelectOption(label="選択式", value="choice"),
                discord.SelectOption(label="記述式", value="text"),
            ],
        )

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.send_modal(QuizCreateModal(self.values[0]))


class QuizKindView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=60)
        self.add_item(QuizKindSelect())


async def send_quiz_list(interaction: discord.Interaction):
    quizzes = get_quizzes()
    if not quizzes:
        await interaction.response.send_message("登録済みのクイズはありません。", ephemeral=True)
        return
    # DiscordのEmbed文字数上限に収まるよう、1メッセージ5問ずつ表示。
    for offset in range(0, len(quizzes), 5):
        embed = discord.Embed(title="登録済みクイズ", color=discord.Color.blurple())
        for quiz in quizzes[offset:offset + 5]:
            kind = "選択式" if quiz["kind"] == "choice" else "記述式"
            details = [f"形式: {kind}", f"カテゴリ: {quiz['category'] or 'なし'}"]
            if quiz["choices"]:
                details.append("選択肢: " + " / ".join(quiz["choices"]))
            details.append("回答: " + " / ".join(quiz["answers"]))
            embed.add_field(
                name=f"ID {quiz['id']} · {quiz['question'][:200]}",
                value="\n".join(details)[:1024], inline=False
            )
        if offset == 0:
            await interaction.response.send_message(embed=embed, ephemeral=True)
        else:
            await interaction.followup.send(embed=embed, ephemeral=True)


class QuizDeleteSelect(discord.ui.Select):
    def __init__(self, parent: "QuizDeleteView"):
        self.parent_view = parent
        quizzes = parent.quizzes[parent.page * 25:(parent.page + 1) * 25]
        options = [
            discord.SelectOption(
                label=f"{quiz['id']}: {quiz['question']}"[:100], value=quiz["id"]
            )
            for quiz in quizzes
        ]
        super().__init__(placeholder="削除するクイズを選択", options=options)

    async def callback(self, interaction: discord.Interaction):
        self.parent_view.selected_id = self.values[0]
        await interaction.response.edit_message(
            content=f"クイズ ID `{self.values[0]}` を選択しました。削除ボタンで確定してください。",
            view=self.parent_view,
        )


class QuizDeleteView(discord.ui.View):
    def __init__(self, quizzes: list[dict]):
        super().__init__(timeout=120)
        self.quizzes = quizzes
        self.page = 0
        self.selected_id: str | None = None
        self.refresh_items()

    def refresh_items(self):
        self.clear_items()
        self.add_item(QuizDeleteSelect(self))
        if self.page > 0:
            self.add_item(QuizDeletePageButton(-1))
        if (self.page + 1) * 25 < len(self.quizzes):
            self.add_item(QuizDeletePageButton(1))
        self.add_item(QuizDeleteConfirmButton())


class QuizDeletePageButton(discord.ui.Button):
    def __init__(self, direction: int):
        super().__init__(
            label="前へ" if direction < 0 else "次へ",
            style=discord.ButtonStyle.secondary,
            row=1,
        )
        self.direction = direction

    async def callback(self, interaction: discord.Interaction):
        view: QuizDeleteView = self.view
        view.page += self.direction
        view.selected_id = None
        view.refresh_items()
        await interaction.response.edit_message(
            content=f"削除するクイズを選択してください（{view.page + 1}/{(len(view.quizzes) + 24) // 25}ページ）",
            view=view,
        )


class QuizDeleteConfirmButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="選択したクイズを削除", style=discord.ButtonStyle.danger, row=1)

    async def callback(self, interaction: discord.Interaction):
        view: QuizDeleteView = self.view
        if not view.selected_id:
            await interaction.response.send_message("先に削除するクイズを選択してください。", ephemeral=True)
            return
        quiz_id = view.selected_id
        if not delete_quiz(quiz_id):
            await interaction.response.edit_message(content="選択したクイズは既に削除されています。", view=None)
            return
        await interaction.response.edit_message(content=f"クイズ ID `{quiz_id}` を削除しました。", view=None)


async def send_random_quiz(interaction: discord.Interaction, category: str | None = None):
    quizzes = get_quizzes(category)
    if not quizzes:
        message = "このカテゴリにはクイズがありません。" if category else "登録済みのクイズがありません。"
        await interaction.response.send_message(message, ephemeral=True)
        return

    quiz = random.choice(quizzes)
    embed = discord.Embed(
        title="🧠 クイズ",
        description=quiz["question"],
        color=discord.Color.blurple(),
    )
    if quiz["category"]:
        embed.add_field(name="カテゴリ", value=quiz["category"], inline=True)
    if quiz["kind"] == "choice":
        embed.add_field(name="形式", value="選択式", inline=True)
    else:
        embed.add_field(name="形式", value="記述式", inline=True)
    await interaction.response.send_message(embed=embed, view=QuizSessionView(quiz), ephemeral=True)


class QuizPanelView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="ランダム出題", style=discord.ButtonStyle.primary,
        custom_id="quiz_random_all", emoji="🎲"
    )
    async def random_all(self, interaction: discord.Interaction, button: discord.ui.Button):
        await send_random_quiz(interaction)

    @discord.ui.button(
        label="カテゴリから出題", style=discord.ButtonStyle.secondary,
        custom_id="quiz_random_category", emoji="📚"
    )
    async def random_category(self, interaction: discord.Interaction, button: discord.ui.Button):
        categories = get_quiz_categories()
        if not categories:
            await interaction.response.send_message("カテゴリ付きのクイズがありません。", ephemeral=True)
            return
        await interaction.response.send_message(
            "出題するカテゴリを選んでください：",
            view=QuizCategoryView(categories), ephemeral=True
        )

    @discord.ui.button(
        label="クイズ登録", style=discord.ButtonStyle.success,
        custom_id="quiz_manage_add", emoji="➕", row=1
    )
    async def manage_add(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("この操作は管理者専用です。", ephemeral=True)
            return
        await interaction.response.send_message(
            "登録するクイズ形式を選んでください：", view=QuizKindView(), ephemeral=True
        )

    @discord.ui.button(
        label="クイズ一覧", style=discord.ButtonStyle.secondary,
        custom_id="quiz_manage_list", emoji="📋", row=1
    )
    async def manage_list(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("この操作は管理者専用です。", ephemeral=True)
            return
        await send_quiz_list(interaction)

    @discord.ui.button(
        label="クイズ削除", style=discord.ButtonStyle.danger,
        custom_id="quiz_manage_delete", emoji="🗑️", row=1
    )
    async def manage_delete(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("この操作は管理者専用です。", ephemeral=True)
            return
        quizzes = get_quizzes()
        if not quizzes:
            await interaction.response.send_message("削除できるクイズがありません。", ephemeral=True)
            return
        view = QuizDeleteView(quizzes)
        await interaction.response.send_message(
            f"削除するクイズを選んでください（1/{(len(quizzes) + 24) // 25}ページ）",
            view=view, ephemeral=True
        )


class QuizCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="quiz_panel", description="クイズ操作パネルを設置します")
    @app_commands.default_permissions(administrator=True)
    async def quiz_panel(self, interaction: discord.Interaction):
        embed = discord.Embed(
            title="🧠 クイズパネル",
            description=(
                "出題ボタンからランダムクイズに挑戦できます。\n"
                "管理者はこのパネルからクイズの登録・一覧・削除を行えます。"
            ),
            color=discord.Color.blurple(),
        )
        await interaction.channel.send(embed=embed, view=QuizPanelView())
        await interaction.response.send_message("クイズパネルを設置しました。", ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(QuizCog(bot))
