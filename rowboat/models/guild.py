import re
import yaml
import time
import requests
import urlparse

from peewee import (
    BigIntegerField, CharField, TextField, BooleanField, DateTimeField, CompositeKey
)
from datetime import datetime
from playhouse.postgres_ext import BinaryJSONField

from rowboat.sql import BaseModel
from rowboat.models.user import User

ALLOWED_DOMAINS = {
    'github.com',
    'githubusercontent.com',
    'pastebin.com',
    'hastebin.com',
    'gitlab.com',
    'bitbucket.org',
}

GIST_RE = re.compile('https://gist.githubusercontent.com/(.*)/(.*)/raw/.*/(.*)')
GIST_FMT = 'https://gist.githubusercontent.com/{}/{}/raw/{}'


def validate_config_url(url):
    parsed = urlparse.urlparse(url)
    if not any(parsed.netloc.endswith(i) for i in ALLOWED_DOMAINS):
        return None

    # Gists can have the revision in them, so lets strip those
    if parsed.netloc.startswith('gist'):
        match = GIST_RE.match(url)
        if match:
            return GIST_FMT.format(*match.groups())

    return url


@BaseModel.register
class Guild(BaseModel):
    guild_id = BigIntegerField(primary_key=True)
    owner_id = BigIntegerField(null=True)
    name = TextField(null=True)
    icon = TextField(null=True)
    splash = TextField(null=True)
    region = TextField(null=True)

    last_ban_sync = DateTimeField(null=True)

    # Rowboat specific data
    config = BinaryJSONField(null=True)
    config_url = CharField()

    enabled = BooleanField(default=True)
    whitelist = BinaryJSONField(default=[])

    added_at = DateTimeField(default=datetime.utcnow)

    class Meta:
        db_table = 'guilds'

    @staticmethod
    def load_from_url(url):
        from rowboat.types.guild import GuildConfig
        r = requests.get(url, timeout=15, params={'_t': time.time()})
        r.raise_for_status()

        obj = yaml.load(r.content)
        gc = GuildConfig(obj)
        gc.validate()
        return gc, obj

    @classmethod
    def with_id(cls, guild_id):
        return cls.get(guild_id=guild_id)

    @classmethod
    def create_from_url(cls, guild, url):
        url = validate_config_url(url)
        if not url:
            raise Exception('Invalid Configuration URL')

        _, raw = cls.load_from_url(url)

        return cls.create(
            guild_id=guild.id,
            owner_id=guild.owner_id,
            name=guild.name,
            icon=guild.icon,
            splash=guild.splash,
            region=guild.region,
            config=raw,
            config_url=url)

    def sync(self, guild):
        updates = {}

        for key in ['owner_id', 'name', 'icon', 'splash', 'region']:
            if getattr(guild, key) != getattr(self, key):
                updates[key] = getattr(guild, key)

        if updates:
            Guild.update(**updates).where(Guild.guild_id == self.guild_id).execute()

    def reload(self):
        _, raw = self.load_from_url(self.config_url)
        self.config = raw
        self.save()

        if hasattr(self, '_cached_config'):
            delattr(self, '_cached_config')

    def get_config(self):
        from rowboat.types.guild import GuildConfig
        if not self.config:
            self.reload()

        if not hasattr(self, '_cached_config'):
            self._cached_config = GuildConfig(self.config)
        return self._cached_config

    def sync_bans(self, guild):
        try:
            bans = guild.get_bans()
        except:
            return

        for ban in bans.values():
            GuildBan.ensure(guild, ban)

        # Update last synced time
        Guild.update(
            last_ban_sync=datetime.utcnow()).where(Guild.guild_id == self.guild_id).execute()


@BaseModel.register
class GuildEmoji(BaseModel):
    emoji_id = BigIntegerField(primary_key=True)
    guild_id = BigIntegerField()
    name = CharField(index=True)

    require_colons = BooleanField()
    managed = BooleanField()
    roles = BinaryJSONField()

    deleted = BooleanField(default=False)

    class Meta:
        db_table = 'guildemojis'

    @classmethod
    def from_disco_guild_emoji(cls, emoji, guild_id=None):
        try:
            ge = cls.get(emoji_id=emoji.id)
            new = False
        except cls.DoesNotExist:
            ge = cls(emoji_id=emoji.id)
            new = True

        ge.guild_id = guild_id or emoji.guild_id
        ge.name = emoji.name
        ge.require_colons = emoji.require_colons
        ge.managed = emoji.managed
        ge.roles = emoji.roles
        ge.save(force_insert=new)
        return ge


@BaseModel.register
class GuildBan(BaseModel):
    user_id = BigIntegerField()
    guild_id = BigIntegerField()
    reason = TextField(null=True)

    class Meta:
        db_table = 'guildbans'
        primary_key = CompositeKey('user_id', 'guild_id')

    @classmethod
    def ensure(cls, guild, ban):
        User.ensure(ban.user)
        obj, _ = cls.get_or_create(guild_id=guild.id, user_id=ban.user.id, defaults=dict(reason=ban.reason))
        return obj
