from __future__ import annotations

import json
import logging
import random
from pathlib import Path

import discord
from discord.ext import commands

from utils.json_store import load_id_map, save_id_map

logger = logging.getLogger("bot.ticket")

# チケットチャンネルを作成するカテゴリのID（ここを書き換えてください）
CATEGORY_ID = 1543475265131257866

OPEN_BUTTON_CUSTOM_ID = "ticket:open_button"
CLOSE_BUTTON_CUSTOM_ID = "ticket:close_button"
MAX_NUMBER_ATTEMPTS = 50  # 空き番号を探す最大試行回数

DATA_DIR = Path(__file__).parent.parent / "data"
DATA_FILE = DATA_DIR / "ticket_channels.json"  # {channel_id: creator_id}


# ------------------------------------------------------------
# チケットを開くボタン（永続化のため custom_id を固定・timeout=None）
# ------------------------------------------------------------
class TicketView(discord.ui.View):
    def __init__(self, cog: "Ticket", button_label: str = "チケットを開く"):
        super().__init__(timeout=None)
        self.cog = cog
        self.open_ticket_button.label = button_label

    @discord.ui.button(
        style=discord.ButtonStyle.primary, custom_id=OPEN_BUTTON_CUSTOM_ID
    )
    async def open_ticket_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        await self.cog.create_ticket_channel(interaction)


# ------------------------------------------------------------
# チケットを閉じるボタン（管理者 or 作成者のみ実行可）
# ------------------------------------------------------------
class CloseView(discord.ui.View):
    def __init__(self, cog: "Ticket"):
        super().__init__(timeout=None)
        self.cog = cog

    @discord.ui.button(
        label="チケットを閉じる",
        style=discord.ButtonStyle.danger,
        custom_id=CLOSE_BUTTON_CUSTOM_ID,
    )
    async def close_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        channel = interaction.channel
        creator_id = self.cog.ticket_creators.get(channel.id)

        is_admin = interaction.user.guild_permissions.administrator
        is_creator = creator_id is not None and interaction.user.id == creator_id

        if not (is_admin or is_creator):
            await interaction.response.send_message(
                "このチケットを閉じる権限がありません。", ephemeral=True
            )
            return

        await interaction.response.send_message("チケットを閉じます。", ephemeral=True)

        self.cog.ticket_creators.pop(channel.id, None)
        self.cog._save_data()

        try:
            await channel.delete(reason=f"{interaction.user} がチケットをクローズ")
        except discord.Forbidden:
            logger.warning(f"チャンネル {channel.id} を削除する権限がありません")


# ------------------------------------------------------------
# Cog 本体
# ------------------------------------------------------------
class Ticket(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # {channel_id(int): creator_id(int)}
        self.ticket_creators: dict[int, int] = {}
        self._load_data()

    async def cog_load(self):
        # 再起動後もボタンを押せるように View を永続登録
        self.bot.add_view(TicketView(self))
        self.bot.add_view(CloseView(self))

    # ---------------- データ永続化 ----------------
    def _load_data(self):
        self.ticket_creators = load_id_map(DATA_FILE)

    def _save_data(self):
        save_id_map(DATA_FILE, self.ticket_creators)

    # ---------------- チケット作成処理 ----------------
    async def create_ticket_channel(self, interaction: discord.Interaction):
        guild = interaction.guild
        category = guild.get_channel(CATEGORY_ID)

        if not isinstance(category, discord.CategoryChannel):
            await interaction.response.send_message(
                "チケット用カテゴリが正しく設定されていません。ticket.py の CATEGORY_ID を確認してください。",
                ephemeral=True,
            )
            return

        # 既に本人のチケットが開いていないか確認（1人1つまで）
        existing_channel_id = next(
            (
                cid
                for cid, uid in self.ticket_creators.items()
                if uid == interaction.user.id
            ),
            None,
        )
        if existing_channel_id is not None:
            existing_channel = guild.get_channel(existing_channel_id)
            if existing_channel is not None:
                await interaction.response.send_message(
                    f"既にチケットが開いています: {existing_channel.mention}",
                    ephemeral=True,
                )
                return
            # チャンネルが既に存在しない場合は古い記録として削除して続行
            self.ticket_creators.pop(existing_channel_id, None)
            self._save_data()

        existing_names = {ch.name for ch in category.channels}
        channel_name = None
        for _ in range(MAX_NUMBER_ATTEMPTS):
            number = random.randint(0, 999)
            candidate = f"Ticket{number:03d}"
            if candidate not in existing_names:
                channel_name = candidate
                break

        if channel_name is None:
            await interaction.response.send_message(
                "空いているチケット番号が見つかりませんでした。しばらくしてから再度お試しください。",
                ephemeral=True,
            )
            return

        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            interaction.user: discord.PermissionOverwrite(
                view_channel=True, send_messages=True, read_message_history=True
            ),
            guild.me: discord.PermissionOverwrite(
                view_channel=True, send_messages=True
            ),
        }

        try:
            channel = await guild.create_text_channel(
                name=channel_name,
                category=category,
                overwrites=overwrites,
                reason=f"{interaction.user} によるチケット作成",
            )
        except discord.Forbidden:
            await interaction.response.send_message(
                "チャンネルを作成する権限がありません。Botの権限を確認してください。",
                ephemeral=True,
            )
            return

        self.ticket_creators[channel.id] = interaction.user.id
        self._save_data()

        await channel.send(interaction.user.mention, view=CloseView(self))
        await interaction.response.send_message(
            f"{channel.mention} を作成しました。", ephemeral=True
        )

    # ---------------- コマンド ----------------
    @commands.command(name="ticket")
    @commands.has_permissions(administrator=True)
    @commands.guild_only()
    async def ticket(self, ctx: commands.Context, message: str, buttonmessage: str):
        """チケット作成パネルを設置する
        使い方: !ticket "パネルに表示するメッセージ" "ボタンのテキスト"
        （複数単語にする場合は " " で囲んでください）
        """
        view = TicketView(self, button_label=buttonmessage)
        await ctx.send(message, view=view)

    @ticket.error
    async def ticket_error(self, ctx: commands.Context, error):
        if isinstance(error, commands.MissingPermissions):
            await ctx.send("このコマンドを実行するには管理者権限が必要です。")
        elif isinstance(error, commands.NoPrivateMessage):
            await ctx.send("このコマンドはサーバー内でのみ使用できます。")
        elif isinstance(error, commands.MissingRequiredArgument):
            await ctx.send(
                '使い方: `!ticket "パネルに表示するメッセージ" "ボタンのテキスト"`'
            )
        else:
            raise error

    # ---------------- リスナー：チャンネルが手動削除された場合のデータ整理 ----------------
    @commands.Cog.listener()
    async def on_guild_channel_delete(self, channel: discord.abc.GuildChannel):
        if channel.id in self.ticket_creators:
            self.ticket_creators.pop(channel.id, None)
            self._save_data()


async def setup(bot: commands.Bot):
    await bot.add_cog(Ticket(bot))
