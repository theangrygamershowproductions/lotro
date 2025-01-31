import discord
import json
import logging
import requests

from discord import app_commands
from discord.ext import commands
from discord.ext import tasks

from database import create_table, select, select_one, upsert

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


class TwitterCog(commands.GroupCog, name=_("twitter"), description=_("Manage twitter settings.")):

    def __init__(self, bot):
        self.bot = bot
        self.conn = bot.conn
        self.twitter_id = bot.twitter_id
        create_table(self.conn, 'twitter')
        super().__init__()

    async def cog_load(self):
        self.twitter_task.start()

    async def cog_unload(self):
        self.twitter_task.cancel()

    def connect_to_endpoint(self, url, params):
        headers = {
            "Authorization": "Bearer {0}".format(self.bot.twitter_token)
        }
        response = requests.request("GET", url, headers=headers, params=params)
        if response.status_code != 200:
            logger.error("Twitter endpoint status: {0}.".format(response.status_code))
            logger.error(response.text)
            return None
        return response.json()

    async def get_new_tweets(self, user_id, last_tweet_id=None):
        url = "https://api.twitter.com/2/users/{}/tweets".format(user_id)
        params = {
                "exclude": "retweets,replies"
                }
        if last_tweet_id:
            params["since_id"] = last_tweet_id
        json_response = self.connect_to_endpoint(url, params)
        if not json_response:
            return
        count = json_response['meta']['result_count']
        if count:
            for i in range(count-1, -1, -1):
                tweet_id = json_response['data'][i]['id']
                upsert(self.conn, 'Twitter', ['user_id', 'tweet_id'], [self.twitter_id, tweet_id])
                await self.post_tweet_to_servers(tweet_id)
            self.conn.commit()

    async def post_tweet_to_servers(self, tweet_id):
        url = "https://twitter.com/lotro/status/{0}".format(tweet_id)
        res = select(self.conn, 'Settings', ['guild_id', 'twitter'])
        for row in res:
            if row[1]:
                await self.post_tweet(*row, url)

    async def post_tweet(self, guild_id, chn_id, url):
        chn = self.bot.get_channel(chn_id)
        if chn:
            try:
                await chn.send(url)
            except discord.Forbidden:
                logger.warning("Missing write access to Twitter channel for guild {0}.".format(guild_id))
                upsert(self.conn, 'Settings', ['twitter'], [None], ['guild_id'], [guild_id])

        else:
            logger.warning("Twitter channel not found for guild {0}.".format(guild_id))
            upsert(self.conn, 'Settings', ['twitter'], [None], ['guild_id'], [guild_id])

    @app_commands.guild_only()
    @app_commands.command(name=_("on"), description=_("Turn on tweets in this channel."))
    async def tweets_on(self, interaction: discord.Interaction):
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message(_("You must be an admin to set up tweets."), ephemeral=True)
            return
        channel = interaction.channel
        guild = interaction.guild
        perms = channel.permissions_for(guild.me)
        if not (perms.send_messages and perms.embed_links):
            await interaction.response.send_message(_("Missing permissions to access this channel."))
            return
        upsert(self.conn, 'Settings', ['twitter'], [channel.id], ['guild_id'], [guild.id])
        await interaction.response.send_message(_("@lotro tweets will be posted to this channel."))
        self.conn.commit()

    @app_commands.guild_only()
    @app_commands.command(name=_("off"), description=_("Turn off tweets in this channel."))
    async def tweets_off(self, interaction: discord.Interaction):
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message(_("You must be an admin to turn off tweets."), ephemeral=True)
            return
        upsert(self.conn, 'Settings', ['twitter'], [None], ['guild_id'], [interaction.guild_id])
        await interaction.response.send_message(_("Tweets will no longer be posted to this channel."))
        self.conn.commit()

    @tasks.loop(seconds=300)
    async def twitter_task(self):
        last_tweet_id = select_one(self.conn, 'Twitter', ['tweet_id'], ['user_id'], [self.twitter_id])
        await self.get_new_tweets(self.twitter_id, last_tweet_id)
        logger.debug("Completed twitter background task.")

    @twitter_task.before_loop
    async def before_twitter_task(self):
        await self.bot.wait_until_ready()

    @twitter_task.error
    async def handle_error(self, exception):
        logger.error("Twitter task failed.")
        logger.error(exception, exc_info=True)


async def setup(bot):
    await bot.add_cog(TwitterCog(bot))
    logger.info("Loaded Twitter Cog.")
