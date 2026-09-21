import logging
import re

import discord
from discord import app_commands
from discord.ext import commands

logger = logging.getLogger("bot.messege")

# https://discord.com/channels/{サーバーID}/{チャンネルID}[/{メッセージID}] の形式に対応
CHANNEL_URL_PATTERN = re.compile(
    r"discord(?:app)?\.com/channels/(\d+)/(\d+)", re.IGNORECASE
)


# ------------------------------------------------------------
# 入力フォーム（モーダル）
# ------------------------------------------------------------
class MessegeModal(discord.ui.Modal, title="メッセージ送信"):
    channel_url = discord.ui.TextInput(
        label="送信先チャンネルのURL",
        style=discord.TextStyle.short,
        placeholder="https://discord.com/channels/サーバーID/チャンネルID",
        required=True,
        max_length=200,
    )

    content = discord.ui.TextInput(
        label="送信するメッセージ",
        style=discord.TextStyle.paragraph,
        placeholder="Botが送信するメッセージを入力してください",
        required=True,
        max_length=2000,
    )

    async def on_submit(self, interaction: discord.Interaction):
        # 念のため、送信時にも管理者かどうかを再確認する
        if (
            interaction.guild is None
            or not interaction.user.guild_permissions.administrator
        ):
            await interaction.response.send_message(
                "このコマンドは管理者のみ使用できます。", ephemeral=True
            )
            return

        # 送信に時間がかかっても失敗扱いにならないよう、先に defer する
        await interaction.response.defer(ephemeral=True)

        text = self.content.value
        if not text.strip():
            await interaction.followup.send(
                "メッセージを入力してください。", ephemeral=True
            )
            return

        # URL からチャンネルIDを取り出す
        m = CHANNEL_URL_PATTERN.search(self.channel_url.value)
        if m is None:
            await interaction.followup.send(
                "チャンネルURLの形式が正しくありません。\n"
                "チャンネルを右クリック →「リンクをコピー」で取得できます。",
                ephemeral=True,
            )
            return

        guild_id, channel_id = int(m.group(1)), int(m.group(2))
        if guild_id != interaction.guild.id:
            await interaction.followup.send(
                "このサーバー以外のチャンネルには送信できません。", ephemeral=True
            )
            return

        # チャンネルを取得（キャッシュに無いスレッドなどは fetch で取得）
        channel = interaction.guild.get_channel_or_thread(channel_id)
        if channel is None:
            try:
                channel = await interaction.guild.fetch_channel(channel_id)
            except discord.HTTPException:
                await interaction.followup.send(
                    "そのチャンネルが見つかりません。", ephemeral=True
                )
                return

        # メッセージを送れる種類のチャンネルか確認（フォーラムやカテゴリは不可）
        if not isinstance(channel, discord.abc.Messageable):
            await interaction.followup.send(
                "そのチャンネルにはメッセージを送信できません。"
                "（フォーラムやカテゴリは指定できません）",
                ephemeral=True,
            )
            return

        # Bot の権限を確認
        perms = channel.permissions_for(interaction.guild.me)
        if not (perms.view_channel and perms.send_messages):
            await interaction.followup.send(
                "Botにそのチャンネルへ送信する権限がありません。", ephemeral=True
            )
            return

        try:
            sent = await channel.send(text)
        except discord.Forbidden:
            await interaction.followup.send(
                "Botにそのチャンネルへ送信する権限がありません。", ephemeral=True
            )
            return
        except discord.HTTPException:
            logger.exception("メッセージの送信に失敗しました")
            await interaction.followup.send(
                "メッセージの送信に失敗しました。", ephemeral=True
            )
            return

        # 誰がどこへ送ったかをログに残す（メッセージ本文は残さない）
        logger.info(
            "/messege: user=%s (ID: %s) -> channel=%s",
            interaction.user,
            interaction.user.id,
            channel_id,
        )

        await interaction.followup.send(
            f"メッセージを送信しました: {sent.jump_url}", ephemeral=True
        )

    async def on_error(self, interaction: discord.Interaction, error: Exception):
        logger.exception("メッセージ送信フォームでエラーが発生しました", exc_info=error)
        msg = "送信中にエラーが発生しました。もう一度お試しください。"
        if interaction.response.is_done():
            await interaction.followup.send(msg, ephemeral=True)
        else:
            await interaction.response.send_message(msg, ephemeral=True)


# ------------------------------------------------------------
# Cog 本体
# ------------------------------------------------------------
class Messege(commands.Cog):
    """管理者がフォームからBotにメッセージを送信させるCog"""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(
        name="messege",
        description="指定したチャンネルにBotとしてメッセージを送信します（管理者のみ）",
    )
    @app_commands.guild_only()
    # 管理者以外にはコマンド一覧に表示しない（サーバー設定で上書きされることがあるので、下の check も併用）
    @app_commands.default_permissions(administrator=True)
    @app_commands.checks.has_permissions(administrator=True)
    async def messege(self, interaction: discord.Interaction):
        await interaction.response.send_modal(MessegeModal())

    @messege.error
    async def messege_error(
        self, interaction: discord.Interaction, error: app_commands.AppCommandError
    ):
        if isinstance(error, app_commands.MissingPermissions):
            message = "このコマンドは管理者のみ使用できます。"
        else:
            logger.exception("/messege の実行中にエラーが発生しました", exc_info=error)
            message = "エラーが発生しました。もう一度お試しください。"

        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(Messege(bot))
