import logging
import time

import discord
from discord import app_commands
from discord.ext import commands

logger = logging.getLogger("bot.vc_remote_recruit")

# vc_recruit.py と同じ値にしよ。　これ同期したりできないのかな、引っ張ったりしたり。
VC_ROLE_ID = 0000000000000000000 
VC_CHECK_CHANNEL_IDS = [0000000000000000000, 0000000000000000000]
COOLDOWN_SECONDS = 60

# {user_id(int): 最後に募集した時刻(time.monotonic())}
_last_recruit_time: dict[int, float] = {}


class VCRemoteRecruit(commands.Cog):
    """スラッシュコマンドからVC募集を行うCog"""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="recruit", description="VCの募集を投稿します")
    @app_commands.describe(
        reason="募集内容",
        notifications="True にすると募集ロールに通知します（省略時は通知なし）",
    )
    @app_commands.guild_only()
    async def recruit(
        self,
        interaction: discord.Interaction,
        reason: app_commands.Range[str, 1, 500],
        notifications: bool = False,
    ):
        channel = interaction.channel
        if channel is None or not isinstance(channel, discord.abc.Messageable):
            await interaction.response.send_message(
                "このチャンネルでは募集できません。", ephemeral=True
            )
            return

        reason = reason.strip()
        if not reason:
            await interaction.response.send_message(
                "募集内容を入力してください。", ephemeral=True
            )
            return

        # 通知する場合のみロールを確認
        role = None
        if notifications:
            role = interaction.guild.get_role(VC_ROLE_ID)
            if role is None:
                await interaction.response.send_message(
                    "VC募集ロールが正しく設定されていません。vc_remote_recruit.py の VC_ROLE_ID を確認してください。",
                    ephemeral=True,
                )
                return

        # 連打防止
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

        # 送信に時間がかかっても失敗扱いにならないよう先に defer する
        await interaction.response.defer(ephemeral=True)

        # 同時実行による抜けを防ぐため、送信前にクールダウンを記録する
        _last_recruit_time[interaction.user.id] = now

        embed = discord.Embed(
            description=reason,
            color=discord.Color.green(),
        )
        embed.set_author(
            name=interaction.user.display_name,
            icon_url=interaction.user.display_avatar.url,
        )

        try:
            await channel.send(
                content=role.mention if role else None,
                embed=embed,
                allowed_mentions=(
                    discord.AllowedMentions(roles=[role])
                    if role
                    else discord.AllowedMentions.none()
                ),
            )
        except discord.Forbidden:
            _last_recruit_time.pop(interaction.user.id, None)
            await interaction.followup.send(
                "このチャンネルへ送信する権限がBotにありません。", ephemeral=True
            )
            return

        await interaction.followup.send("募集を投稿しました。", ephemeral=True)

    @recruit.error
    async def recruit_error(
        self, interaction: discord.Interaction, error: app_commands.AppCommandError
    ):
        logger.exception("/recruit の実行中にエラーが発生しました", exc_info=error)
        message = "募集の送信中にエラーが発生しました。もう一度お試しください。"
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(VCRemoteRecruit(bot))
