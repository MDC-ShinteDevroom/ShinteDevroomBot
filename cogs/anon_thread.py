import logging
import time

import discord
from discord import app_commands
from discord.ext import commands

logger = logging.getLogger("bot.anon_thread")

# 連続使用を防ぐクールダウン秒数
COOLDOWN_SECONDS = 30

_last_used: dict[int, float] = {}


class AnonThread(commands.Cog):

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(
        name="anon_thread", description="フォーラムに匿名でスレッドを建てます"
    )
    @app_commands.describe(
        channel="スレッドを建てるフォーラムチャンネル",
        title="スレッドのタイトル（100文字まで）",
        description="スレッドの概要（2000文字まで）",
    )
    @app_commands.guild_only()
    async def anon_thread(
        self,
        interaction: discord.Interaction,
        channel: discord.ForumChannel,
        title: app_commands.Range[str, 1, 100],
        description: app_commands.Range[str, 1, 2000],
    ):
        assert interaction.guild is not None
        assert isinstance(interaction.user, discord.Member)

        title = title.strip()
        description = description.strip()
        if not title or not description:
            await interaction.response.send_message(
                "タイトルと概要を入力してください。", ephemeral=True
            )
            return

        # 実行者が、そのフォーラムに投稿できる権限を持っているか確認
        user_perms = channel.permissions_for(interaction.user)
        if not (user_perms.view_channel and user_perms.send_messages):
            await interaction.response.send_message(
                "そのフォーラムには投稿する権限がありません。", ephemeral=True
            )
            return

        # Bot 側の権限確認
        bot_perms = channel.permissions_for(interaction.guild.me)
        if not (bot_perms.view_channel and bot_perms.send_messages):
            await interaction.response.send_message(
                "Botにそのフォーラムへ投稿する権限がありません。", ephemeral=True
            )
            return

        # タグ必須のフォーラムには投稿できない（タグを選ぶ手段がないため）
        if channel.flags.require_tag:
            await interaction.response.send_message(
                "そのフォーラムはタグが必須のため、この機能では投稿できません。",
                ephemeral=True,
            )
            return

        # クールダウン確認（確認と記録の間に await を挟まないので同時実行でも抜けない）
        now = time.monotonic()
        last = _last_used.get(interaction.user.id)
        if last is not None and (now - last) < COOLDOWN_SECONDS:
            remaining = int(COOLDOWN_SECONDS - (now - last)) + 1
            await interaction.response.send_message(
                f"連続して使用できません。あと{remaining}秒お待ちください。",
                ephemeral=True,
            )
            return
        _last_used[interaction.user.id] = now

        # 応答は ephemeral にして、実行者が他の人に分からないようにする
        await interaction.response.defer(ephemeral=True)

        try:
            created = await channel.create_thread(
                name=title,
                content=description,
                # メンション（@everyone やロール）が実際に通知されないようにする
                allowed_mentions=discord.AllowedMentions.none(),
                # 監査ログに実行者が残らないよう、理由にもユーザー情報は入れない
                reason="匿名スレッド",
            )
        except discord.Forbidden:
            _last_used.pop(interaction.user.id, None)
            await interaction.followup.send(
                "Botにスレッドを作成する権限がありません。", ephemeral=True
            )
            return
        except discord.HTTPException:
            _last_used.pop(interaction.user.id, None)
            logger.exception("匿名スレッドの作成に失敗しました")
            await interaction.followup.send(
                "スレッドの作成に失敗しました。もう一度お試しください。", ephemeral=True
            )
            return

        await interaction.followup.send(
            f"匿名でスレッドを作成しました: {created.thread.jump_url}",
            ephemeral=True,
        )

    @anon_thread.error
    async def anon_thread_error(
        self, interaction: discord.Interaction, error: app_commands.AppCommandError
    ):
        logger.exception("/anon_thread の実行中にエラーが発生しました", exc_info=error)
        message = "スレッドの作成中にエラーが発生しました。もう一度お試しください。"
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(AnonThread(bot))
