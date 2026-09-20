from __future__ import annotations

import asyncio
import json
import logging
import time
from pathlib import Path

import discord
from discord.ext import commands

from utils.json_store import load_id_map, save_id_map

logger = logging.getLogger("bot.vc_recruit")

DATA_DIR = Path(__file__).parent.parent / "data"
CHANNELS_FILE = DATA_DIR / "vc_channels.json"  # {channel_id: message_id}

BUTTON_CUSTOM_ID = "vc:recruit_button"

# メンションするVC募集ロールのID（ここを書き換えてください）
VC_ROLE_ID = 1511613354987225208

# 募集する前に人がいないか確認するVCチャンネルのID（2つ、ここを書き換えてください）
VC_CHECK_CHANNEL_IDS = [1459083568339751089, 1459083437615878145]

# 連打防止のクールダウン秒数
COOLDOWN_SECONDS = 60

# {user_id(int): 最後に募集した時刻(time.monotonic())}
_last_recruit_time: dict[int, float] = {}


# ------------------------------------------------------------
# 募集内容入力用モーダル
# ------------------------------------------------------------
class RecruitModal(discord.ui.Modal, title="VC募集"):
    content = discord.ui.TextInput(
        label="募集内容を入力してください",
        style=discord.TextStyle.paragraph,
        placeholder="例: 19:00〜 メンバー募集！",
        max_length=500,
        required=True,
    )

    async def on_submit(self, interaction: discord.Interaction):
        role = interaction.guild.get_role(VC_ROLE_ID)

        if role is None:
            await interaction.response.send_message(
                "VC募集ロールが正しく設定されていません。vc_recruit.py の VC_ROLE_ID を確認してください。",
                ephemeral=True,
            )
            return

        # 連打防止（クールダウン中かチェック）
        now = time.monotonic()
        last_time = _last_recruit_time.get(interaction.user.id)
        if last_time is not None and (now - last_time) < COOLDOWN_SECONDS:
            remaining = int(COOLDOWN_SECONDS - (now - last_time))
            await interaction.response.send_message(
                f"連続して募集はできません。あと{remaining}秒お待ちください。",
                ephemeral=True,
            )
            return

        # 指定したVCに人がいないか確認
        for vc_id in VC_CHECK_CHANNEL_IDS:
            vc = interaction.guild.get_channel(vc_id)
            if isinstance(vc, discord.VoiceChannel) and len(vc.members) > 0:
                await interaction.response.send_message(
                    "現在VCに人がいるため募集できません。",
                    ephemeral=True,
                )
                return

        embed = discord.Embed(
            description=self.content.value,
            color=discord.Color.green(),
        )
        embed.set_author(
            name=interaction.user.display_name,
            icon_url=interaction.user.display_avatar.url,
        )

        await interaction.channel.send(
            content=role.mention,
            embed=embed,
            allowed_mentions=discord.AllowedMentions(roles=True),
        )
        _last_recruit_time[interaction.user.id] = now
        await interaction.response.defer()

    async def on_error(self, interaction: discord.Interaction, error: Exception):
        logger.exception("VC募集モーダルでエラーが発生しました", exc_info=error)
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
class VCRecruitView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="VC募集をする",
        style=discord.ButtonStyle.success,
        custom_id=BUTTON_CUSTOM_ID,
    )
    async def recruit_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        await interaction.response.send_modal(RecruitModal())


# ------------------------------------------------------------
# Cog 本体
# ------------------------------------------------------------
class VCRecruit(commands.Cog):
    """VC募集ボタンをチャンネル最下部に維持するCog"""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # {channel_id(int): message_id(int)}
        self.vc_channels: dict[int, int] = {}
        self._locks: dict[int, asyncio.Lock] = {}
        self._load_data()

    def _get_lock(self, channel_id: int) -> asyncio.Lock:
        if channel_id not in self._locks:
            self._locks[channel_id] = asyncio.Lock()
        return self._locks[channel_id]

    async def cog_load(self):
        # 再起動後もボタンを押せるように View を永続登録
        self.bot.add_view(VCRecruitView())

    # ---------------- データ永続化 ----------------
    def _load_data(self):
        self.vc_channels = load_id_map(CHANNELS_FILE)

    def _save_data(self):
        save_id_map(CHANNELS_FILE, self.vc_channels)

    # ---------------- ボタン再送信 ----------------
    async def _repost_button(self, channel: discord.TextChannel):
        lock = self._get_lock(channel.id)
        if lock.locked():
            return

        async with lock:
            old_message_id = self.vc_channels.get(channel.id)

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
                    "以下のボタンからVCを募集してください。",
                    view=VCRecruitView(),
                )
            except discord.Forbidden:
                logger.warning(f"チャンネル {channel.id} への送信権限がありません")
                return

            self.vc_channels[channel.id] = new_message.id
            self._save_data()

    # ---------------- コマンド ----------------
    @commands.command(name="vbchannel")
    @commands.has_permissions(administrator=True)
    @commands.guild_only()
    async def vbchannel(self, ctx: commands.Context):
        """このチャンネルをVC募集チャンネルに設定する"""
        await self._repost_button(ctx.channel)

    @vbchannel.error
    async def vbchannel_error(self, ctx: commands.Context, error):
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
        if message.channel.id not in self.vc_channels:
            return

        # 直前に送ったボタン自体のメッセージなら無視（無限ループ防止）
        if message.id == self.vc_channels.get(message.channel.id):
            return

        await self._repost_button(message.channel)


async def setup(bot: commands.Bot):
    await bot.add_cog(VCRecruit(bot))
