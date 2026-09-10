from __future__ import annotations

import json
import logging
from pathlib import Path

import discord
from discord.ext import commands

logger = logging.getLogger("bot.welcome")

DATA_DIR = Path(__file__).parent.parent / "data"
DATA_FILE = DATA_DIR / "welcome_channels.json"


class Welcome(commands.Cog):
    """参加・脱退をシンプルなメッセージで通知するCog"""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # {guild_id(int): channel_id(int)}
        self.welcome_channels: dict[int, int] = {}
        self._load_data()

    # ---------------- データ永続化 ----------------
    def _load_data(self):
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        if DATA_FILE.exists():
            try:
                raw = json.loads(DATA_FILE.read_text(encoding="utf-8"))
                self.welcome_channels = {int(k): int(v) for k, v in raw.items()}
            except Exception:
                logger.exception("welcome_channels.json の読み込みに失敗しました")
                self.welcome_channels = {}

    def _save_data(self):
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        DATA_FILE.write_text(
            json.dumps(
                {str(k): v for k, v in self.welcome_channels.items()},
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

    def _get_channel(self, guild: discord.Guild) -> discord.abc.Messageable | None:
        channel_id = self.welcome_channels.get(guild.id)
        if channel_id is None:
            return None
        return guild.get_channel(channel_id)

    # ---------------- コマンド ----------------
    @commands.command(name="welcomechannel")
    @commands.has_permissions(administrator=True)
    @commands.guild_only()
    async def welcomechannel(self, ctx: commands.Context):
        """このチャンネルを入退室通知チャンネルに設定する"""
        self.welcome_channels[ctx.guild.id] = ctx.channel.id
        self._save_data()

    @welcomechannel.error
    async def welcomechannel_error(self, ctx: commands.Context, error):
        if isinstance(error, commands.MissingPermissions):
            await ctx.send("このコマンドを実行するには管理者権限が必要です。")
        elif isinstance(error, commands.NoPrivateMessage):
            await ctx.send("このコマンドはサーバー内でのみ使用できます。")
        else:
            raise error

    async def _send_log(self, guild: discord.Guild, message: str):
        channel = self._get_channel(guild)
        if channel is None:
            return

        try:
            await channel.send(message)
        except discord.Forbidden:
            logger.warning(f"チャンネル {channel.id} への送信権限がありません")

    # ---------------- リスナー ----------------
    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        await self._send_log(
            member.guild,
            f"{member.mention} が参加しました",
        )

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member):
        await self._send_log(
            member.guild,
            f"{member.mention} が脱退しました",
        )

    @commands.Cog.listener()
    async def on_member_update(
        self,
        before: discord.Member,
        after: discord.Member,
    ):
        # premium_since が None → 値ありに変化した場合、サーバーブーストを開始したと判定
        if before.premium_since is None and after.premium_since is not None:
            await self._send_log(
                after.guild,
                f"{after.mention} がサーバーをブーストしました",
            )


async def setup(bot: commands.Bot):
    await bot.add_cog(Welcome(bot))
