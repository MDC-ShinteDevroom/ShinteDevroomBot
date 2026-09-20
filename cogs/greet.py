from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path

import discord
from discord.ext import commands

from utils.json_store import load_id_map, save_id_map

logger = logging.getLogger("bot.greet")

# 設定の保存先（channel_id: 現在のボタンメッセージID）
DATA_DIR = Path(__file__).parent.parent / "data"
DATA_FILE = DATA_DIR / "greet_channels.json"

BUTTON_CUSTOM_ID = "greet:intro_button"

# 自己紹介後に誘導するチャンネルのID（ここを書き換えてください）
GUIDE_CHANNEL_ID = 1466347269279191153


# ------------------------------------------------------------
# 自己紹介入力用モーダル
# ------------------------------------------------------------
class IntroModal(discord.ui.Modal, title="自己紹介"):
    intro = discord.ui.TextInput(
        label="自己紹介文を入力してください",
        style=discord.TextStyle.paragraph,
        placeholder="",
        max_length=1000,
        required=True,
    )

    async def on_submit(self, interaction: discord.Interaction):
        embed = discord.Embed(
            description=self.intro.value,
            color=discord.Color.blurple(),
        )
        embed.set_author(
            name=interaction.user.display_name,
            icon_url=interaction.user.display_avatar.url,
        )

        await interaction.channel.send(embed=embed)

        guide_channel = interaction.guild.get_channel(GUIDE_CHANNEL_ID)
        if guide_channel is not None:
            await interaction.response.send_message(
                f"ご記入ありがとうございます。次は {guide_channel.mention} でロール設定をしてください。",
                ephemeral=True,
            )
        else:
            logger.warning(
                f"GUIDE_CHANNEL_ID ({GUIDE_CHANNEL_ID}) に該当するチャンネルが見つかりません。"
                " greet.py の GUIDE_CHANNEL_ID を確認してください。"
            )
            await interaction.response.defer()

    async def on_error(self, interaction: discord.Interaction, error: Exception):
        logger.exception("自己紹介モーダルでエラーが発生しました", exc_info=error)
        if interaction.response.is_done():
            await interaction.followup.send(
                "送信中にエラーが発生しました。もう一度お試しください。", ephemeral=True
            )
        else:
            await interaction.response.send_message(
                "送信中にエラーが発生しました。もう一度お試しください。", ephemeral=True
            )


# ------------------------------------------------------------
# ボタンの View（永続化のため custom_id を固定・timeout=None）
# ------------------------------------------------------------
class GreetView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="自己紹介をする",
        style=discord.ButtonStyle.success,
        custom_id=BUTTON_CUSTOM_ID,
    )
    async def greet_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        await interaction.response.send_modal(IntroModal())


# ------------------------------------------------------------
# Cog 本体
# ------------------------------------------------------------
class Greet(commands.Cog):
    """自己紹介ボタンをチャンネル最下部に維持するCog"""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # {channel_id(int): message_id(int)}
        self.greet_channels: dict[int, int] = {}
        self._locks: dict[int, "asyncio.Lock"] = {}
        self._load_data()

    def _get_lock(self, channel_id: int) -> "asyncio.Lock":
        if channel_id not in self._locks:
            self._locks[channel_id] = asyncio.Lock()
        return self._locks[channel_id]

    async def cog_load(self):
        # 再起動後もボタンを押せるように View を永続登録
        self.bot.add_view(GreetView())

    # ---------------- データ永続化 ----------------
    def _load_data(self):
        self.greet_channels = load_id_map(DATA_FILE)

    def _save_data(self):
        save_id_map(DATA_FILE, self.greet_channels)

    # ---------------- ボタン再送信 ----------------
    async def _repost_button(self, channel: discord.TextChannel):
        lock = self._get_lock(channel.id)
        if lock.locked():
            # 既に再送信処理が進行中なら、二重実行しない
            return

        async with lock:
            old_message_id = self.greet_channels.get(channel.id)

            if old_message_id:
                try:
                    old_message = await channel.fetch_message(old_message_id)
                    await old_message.delete()
                except (discord.NotFound, discord.Forbidden):
                    pass
                except discord.HTTPException:
                    logger.exception("古いボタンメッセージの削除に失敗しました")

            try:
                new_message = await channel.send(
                    "以下のボタンから自己紹介をしてください。",
                    view=GreetView(),
                )
            except discord.Forbidden:
                logger.warning(f"チャンネル {channel.id} への送信権限がありません")
                return

            # send完了直後、他のイベント処理に制御が渡る前に同期的に更新
            self.greet_channels[channel.id] = new_message.id
            self._save_data()

    # ---------------- コマンド ----------------
    @commands.command(name="greetchannel")
    @commands.has_permissions(administrator=True)
    @commands.guild_only()
    async def greetchannel(self, ctx: commands.Context):
        await self._repost_button(ctx.channel)

    @greetchannel.error
    async def greetchannel_error(self, ctx: commands.Context, error):
        if isinstance(error, commands.MissingPermissions):
            await ctx.send("このコマンドを実行するには管理者権限が必要です。")
        elif isinstance(error, commands.NoPrivateMessage):
            await ctx.send("このコマンドはサーバー内でのみ使用できます。")
        else:
            raise error

    # ---------------- リスナー：ボタンを常に最下部へ ----------------
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.guild is None:
            return
        if message.channel.id not in self.greet_channels:
            return

        # 直前に送ったボタン自体のメッセージなら無視（無限ループ防止）
        if message.id == self.greet_channels.get(message.channel.id):
            return

        await self._repost_button(message.channel)


async def setup(bot: commands.Bot):
    await bot.add_cog(Greet(bot))
