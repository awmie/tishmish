# T I S H M I S H
import nextcord
from nextcord import interactions
from nextcord.ext import commands, tasks
import mafic
from mafic import EndReason, Playlist, SearchType
import aiohttp
import base64
import logging
import math
import os
import asyncio
import datetime
import random
import re
import signal
import sys
import time
import traceback
from collections import deque, namedtuple

# Logging is configured before anything can fail, because the whole history of
# this bot's audio outages is "something went wrong and nobody was told": the
# previous backend reported a failed node connection by logging it and returning
# normally (nextwave websocket.py:76-88 swallowed the exception), so the one
# message saying "your audio node is down" went to an unconfigured logger and
# vanished. mafic does log and raise, but only where a handler can see it.
def _log_level():
    """DEBUG is genuinely useful here: the track-end handler logs the raw
    `reason` the backend sends, which is the only way to learn the exact
    spellings a given Lavalink version emits rather than guessing them. That is
    how this file learned 3.7.13 sends UPPERCASE reasons while mafic's EndReason
    is lowercase -- a gate that compares case-sensitively silently stops the
    queue."""
    name = (os.getenv("TISHMISH_LOG_LEVEL") or "INFO").strip().upper()
    return getattr(logging, name, logging.INFO)


logging.basicConfig(
    level=_log_level(),
    format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
    stream=sys.stdout,
)
log = logging.getLogger("tishmish")
logging.getLogger("mafic").setLevel(_log_level())

# I N T E N T S
intents = nextcord.Intents(messages=True, guilds=True)
intents.guild_messages = True
# members is load-bearing: on_voice_state_update counts VoiceChannel.members,
# and that list silently drops uncached ids without the members intent.
intents.members = True
# message_content is deliberately NOT enabled. Every command here is a slash
# command, nothing reads inbound content, and it is a privileged intent that has
# to be switched on in the developer portal -- it was a free grant with no
# reader.
intents.voice_states = True
intents.emojis_and_stickers = True

bot = commands.Bot(
    intents=intents,
    description="Premium quality music bot for free!\nUse headphones for better quality <3",
)
# Per-guild playback state (queue, requester, loop flags) is defined just below
# TrackQueue, because the Player subclass needs the queue class to exist first.
# The old `setattr(nextwave.Player, ...)` monkeypatching a third-party class is
# gone: mafic rebuilds Player objects in sync_players() after a reconnect, so
# state hung off a player -- a sixty-song queue included -- would vanish exactly
# when a member expects it to survive. See GuildState for the replacement.
embed_color = nextcord.Color.from_rgb(128, 67, 255)

# Spotify links, canonicalized.
#
# /spotifyplay takes free text from any member and hands it to the backend, which
# builds a URL by interpolation: the identifier is pasted straight into
# BASEURL.format(entity=..., identifier=query) and requested with the Spotify
# bearer token attached (nextwave ext/spotify/__init__.py:236). A value that is
# not URL-shaped skips the library's own regex, so it lands in the path verbatim
# -- and yarl normalizes dot segments, so `../../me` resolves to
# https://api.spotify.com/v1/me under our credentials. Any member could aim the
# bot's token at arbitrary api.spotify.com paths today.
#
# The fix is to accept only a canonical Spotify link and rebuild the string from
# the pieces we matched ourselves: host pinned, kind from a fixed set, id limited
# to base62 with no dots or slashes, and everything after the id discarded. The
# rebuilt form still matches the library's regex, so album and track links keep
# resolving as themselves rather than being forced through the playlist path.
SPOTIFY_URL_RE = re.compile(
    r"^https://open\.spotify\.com/(?:intl-[A-Za-z]{2}/)?"
    r"(?P<kind>playlist|album|track)/(?P<id>[0-9A-Za-z]{16,26})(?:\?.*)?$"
)


def normalize_spotify_url(search):
    """Return a canonical Spotify URL, or None if this is not one."""
    match = SPOTIFY_URL_RE.match((search or "").strip())
    if match is None:
        return None
    return f"https://open.spotify.com/{match.group('kind')}/{match.group('id')}"


# ---------------------------------------------------------------------------
# Playback queue
# ---------------------------------------------------------------------------
QUEUE_MAX = 100


class QueueFull(Exception):
    """Appending would exceed QUEUE_MAX. Enforced at accept time."""


class QueueEmpty(Exception):
    """get_next() on an exhausted queue. advance() returns None instead."""


class Row(namedtuple("Row", "position track title")):
    """One line of the /queue listing.

    `position` is 1-based and is exactly the number /del, /skipto and /move take.
    Producing both from one place is the point: the old code numbered the listing
    in one convention and indexed the queue in another.
    """

    __slots__ = ()


class TrackQueue:
    """A guild's pending tracks: ours to own, and therefore to test.

    Two things are deliberately absent, because they caused every historical
    queue bug in this bot:

    - No private-state access. The old code did `del vc.state.queue._queue[i]`,
      `vc.state.queue._queue[0] == vc._source`, `vc.state.queue._wakeup_next()` and
      `enumerate(vc.state.queue)`, all of which broke the moment the backend's shape
      changed.
    - No asyncio waiter. get_wait/put_wait existed so a player task could block
      for the next track; every command that edited the deque by hand could
      strand that waiter, which is how "the queue stops advancing and nobody is
      told" kept happening. The track-end handler pulls the next track inline via
      advance(), so there is one consumer and nothing to wake.

    Identity, not equality, wherever a track is located: Track defines no
    __eq__, so deque.remove() deletes the first EQUAL element and silently picked
    the wrong one once /loopqueue re-queued the same object.
    """

    def __init__(self, *tracks, max_size=QUEUE_MAX):
        max_size = int(max_size)
        if max_size < 1:
            raise ValueError("max_size must be at least 1")
        if len(tracks) > max_size:
            raise ValueError(f"{len(tracks)} tracks exceeds max_size={max_size}")
        self.max_size = max_size
        self._items = deque(tracks)

    # -- introspection ------------------------------------------------------
    def __len__(self):
        return len(self._items)

    def __iter__(self):
        return iter(self._items)

    def __contains__(self, track):
        return any(item is track for item in self._items)

    def __repr__(self):
        return f"<TrackQueue {len(self._items)}/{self.max_size}>"

    def __str__(self):
        # Human-readable on purpose: this object used to be interpolated into the
        # /predict prompt, where it contributed a repr instead of song titles.
        return ", ".join(self.titles())

    @property
    def count(self):
        return len(self._items)

    @property
    def is_empty(self):
        return not self._items

    @property
    def is_full(self):
        return len(self._items) >= self.max_size

    @property
    def remaining(self):
        return max(0, self.max_size - len(self._items))

    # -- internals ----------------------------------------------------------
    def _index_of(self, track):
        for index, item in enumerate(self._items):
            if item is track:
                return index
        return None

    @staticmethod
    def _title_of(track):
        title = getattr(track, "title", None)
        if title:
            return title
        info = getattr(track, "info", None) or {}
        return info.get("title") or "?"

    def _checked_index(self, position):
        if not isinstance(position, int) or isinstance(position, bool):
            raise IndexError(f"position must be an integer, got {position!r}")
        if position < 1 or position > len(self._items):
            raise IndexError(
                f"position {position} is out of range for {len(self._items)} track(s)"
            )
        return position - 1

    # -- producing ----------------------------------------------------------
    def append(self, track):
        """Queue one track. Raises QueueFull at the cap, so the caller decides
        what the member is told -- the only way a cap gets reported at all, since
        the backend accepted an unbounded queue silently."""
        if self.is_full:
            raise QueueFull(f"The queue is full at {self.max_size} tracks.")
        self._items.append(track)
        return True

    def extend(self, tracks):
        """Queue several atomically: either all fit or none are added, so a
        half-loaded playlist cannot leave the queue in a partial state."""
        incoming = list(tracks)
        if len(incoming) > self.remaining:
            raise QueueFull(
                f"{len(incoming)} tracks do not fit; {self.remaining} place(s) left."
            )
        self._items.extend(incoming)
        return len(incoming)

    # -- consuming ----------------------------------------------------------
    def peek(self):
        return self._items[0] if self._items else None

    def get_next(self):
        if not self._items:
            raise QueueEmpty("The queue is empty.")
        return self._items.popleft()

    def advance(self, *, finished=None, loop_queue=False):
        """Return the next track to play, or None when done.

        The single decision point for "what plays next", which used to be spread
        across the track-end handler, /skip and the loopqueue re-put. With
        loop_queue the finished track goes to the back exactly once -- removed
        from wherever it sits first, so the requeue cannot double it -- and the
        front is returned.
        """
        if loop_queue and finished is not None:
            index = self._index_of(finished)
            if index is not None:
                del self._items[index]
            if self.is_full:
                # At capacity the rotation is dropped rather than raising: the
                # alternative is that a full queue ends the set mid-loop.
                log.debug(
                    "loopqueue: queue at capacity, not re-adding the finished track"
                )
            else:
                self._items.append(finished)
        return self.get_next() if self._items else None

    # -- editing ------------------------------------------------------------
    def get_at(self, position):
        return self._items[self._checked_index(position)]

    def remove_at(self, position):
        index = self._checked_index(position)
        track = self._items[index]
        del self._items[index]
        return track

    def move(self, source, target):
        source_index = self._checked_index(source)
        target_index = self._checked_index(target)
        if source_index == target_index:
            return
        items = list(self._items)
        items.insert(target_index, items.pop(source_index))
        self._items = deque(items)

    def shuffle(self):
        items = list(self._items)
        random.shuffle(items)
        self._items = deque(items)

    def clear(self):
        self._items.clear()

    # -- rendering ----------------------------------------------------------
    def display_rows(self):
        return [
            Row(position, track, self._title_of(track))
            for position, track in enumerate(self._items, start=1)
        ]

    def titles(self, limit=None):
        titles = [self._title_of(track) for track in self._items]
        return titles if limit is None else titles[:limit]


# ---------------------------------------------------------------------------
# Per-guild player state
# ---------------------------------------------------------------------------
REQUESTERS_MAX = 256


class GuildState:
    """Queue, requester and loop flags that must outlive a Player object.

    Keyed by guild id deliberately: mafic calls Node.sync_players() after a
    reconnect and rebuilds each Player, so anything hung off the player -- a
    sixty-song queue, who requested what -- disappears at exactly the moment a
    member expects it to have survived. A reconnect then costs a voice handshake
    instead of the music.

    channel_id lives here too because mafic.Player exposes no `.channel`, and
    user_connectivity needs the channel the player actually sits in to refuse
    members who are elsewhere in the guild.
    """

    __slots__ = (
        "guild_id", "queue", "requesters", "loop_track", "loop_queue",
        "channel_id", "volume", "abandoned_id",
    )

    def __init__(self, guild_id, max_size=QUEUE_MAX):
        self.guild_id = guild_id
        self.queue = TrackQueue(max_size=max_size)
        self.requesters = {}
        self.loop_track = False
        self.loop_queue = False
        self.channel_id = None
        self.volume = 100
        # Set when a handler abandons a track (exception/stuck/skip) so the late
        # end event for that same track cannot advance the queue a second time.
        self.abandoned_id = None

    def remember(self, track, mention):
        # mafic.Track uses __slots__, so the requester cannot be stamped on the
        # track itself -- this side table is not laziness, it is the only shape
        # the backend permits. It is bounded and per-guild, fixing the old global
        # dict that grew forever and let two guilds overwrite each other.
        key = getattr(track, "id", None) or getattr(track, "identifier", None)
        if key is None:
            return
        self.requesters[key] = mention
        while len(self.requesters) > REQUESTERS_MAX:
            self.requesters.pop(next(iter(self.requesters)))

    def requester_for(self, track):
        key = getattr(track, "id", None) or getattr(track, "identifier", None)
        return self.requesters.get(key)


_GUILD_STATE = {}


def guild_state(guild_id):
    """The one GuildState for this guild, created on first use."""
    state = _GUILD_STATE.get(guild_id)
    if state is None:
        state = _GUILD_STATE[guild_id] = GuildState(guild_id)
    return state


class Player(mafic.Player):
    """The Lavalink player for one guild, carrying its GuildState.

    nextcord builds whatever is passed to VoiceChannel.connect as
    `cls(client, channel)` (abc.py:1790), which is the signature kept here, and
    mafic.Player already subclasses nextcord.VoiceProtocol, so the connection
    path is unchanged. Pass player_cls=Player to create_node as well: on
    reconnect mafic rebuilds players through sync_players() and would otherwise
    resurrect them as plain mafic.Players with no state attached.
    """

    def __init__(self, client, channel, *, node=None):
        super().__init__(client, channel, node=node)
        if channel is not None:
            # The state is keyed by GUILD id; channel.guild is how the voice
            # channel reaches it (channel.id here would key state per channel and
            # silently give every guild a fresh queue).
            guild = getattr(channel, "guild", None)
            gid = getattr(guild, "id", None)
            if gid is not None:
                guild_state(gid).channel_id = getattr(channel, "id", None)

    @property
    def state(self):
        guild = getattr(self, "guild", None)
        gid = getattr(guild, "id", None) or getattr(self, "_guild_id", None)
        return guild_state(gid)

    @property
    def queue(self):
        return self.state.queue

# ---------------------------------------------------------------------------
# Access control and rate limiting for slash (application) commands.
#
# nextcord's commands.has_role / is_owner / has_permissions / cooldown
# decorators do NOT apply to slash commands. They stash their predicate on
# __commands_checks__ / __commands_cooldown__, which application_command.py
# never reads -- only ext.commands.Command consumes those. On a
# @bot.slash_command the decorators are accepted silently and enforce nothing,
# so every gate in this file read as enforced while all 24 commands were wide
# open. ApplicationCommand.add_check() is the real hook: the predicate receives
# the Interaction.
#
# A predicate must RAISE ApplicationCheckFailure with its own sentence. It must
# not merely return False: nextcord then raises its own ApplicationCheckFailure
# whose text embeds the command's function repr and memory address
# ("The check functions for application command <function play_command at
# 0x...> failed."), and on_application_command_error forwards str(error) to the
# member. An earlier revision of this comment claimed a falsy return was the
# safe shape; that was unreachable as written and is why every gate below
# raises.
#
# These decorators therefore sit ABOVE @bot.slash_command so they receive the
# registered command object rather than the bare coroutine.
# ---------------------------------------------------------------------------
TM_ROLE = "tm"
_RATE_PRUNE_AT = 4096


def require_role(role_name=TM_ROLE):
    """Block unless the invoking member holds role_name."""
    def decorator(command):
        async def predicate(interaction):
            member = interaction.user
            # Guild first: in a DM, interaction.user is a User with no .roles,
            # so the role scan below would raise AttributeError instead of
            # refusing.
            if interaction.guild is None:
                raise nextcord.ApplicationCheckFailure(
                    f"`/{command.name}` only works in a server."
                )
            if getattr(member, "bot", False):
                raise nextcord.ApplicationCheckFailure(
                    f"`/{command.name}` cannot be used by a bot account."
                )
            if not any(role.name == role_name for role in member.roles):
                raise nextcord.ApplicationCheckFailure(
                    f"You need the **{role_name}** role to use `/{command.name}`."
                )
            return True
        command.add_check(predicate)
        return command
    return decorator


def require_owner():
    """Block unless the invoker is this bot's application owner."""
    def decorator(command):
        async def predicate(interaction):
            if not await bot.is_owner(interaction.user):
                raise nextcord.ApplicationCheckFailure(
                    "Only the owner of this bot can use that command."
                )
            return True
        command.add_check(predicate)
        return command
    return decorator


def require_permission(**permissions):
    """Block unless the invoking member holds every named guild permission."""
    def decorator(command):
        async def predicate(interaction):
            # Raise, never return False -- see the block comment above. A falsy
            # return hands nextcord's own check-failure text, function repr and
            # all, to the member through on_application_command_error.
            if interaction.guild is None:
                raise nextcord.ApplicationCheckFailure(
                    f"`/{command.name}` only works in a server."
                )
            perms = interaction.user.guild_permissions
            if not all(getattr(perms, name, False) is True for name in permissions):
                wanted = " and ".join(p.replace("_", " ") for p in permissions)
                raise nextcord.ApplicationCheckFailure(
                    f"`/{command.name}` needs the **{wanted}** permission."
                )
            return True
        command.add_check(predicate)
        return command
    return decorator


def rate_limit(invocations, period):
    """Fixed-window throttle: invocations starts per user per period seconds.

    Scope and limits, all deliberate:
    - Keyed by user id only, with no guild partition: one member who spams a
      command in one server is throttled in every server. The process shares a
      single Lavalink node and a single g4f client across all guilds, so the
      global key is the conservative one.
    - Counts invocations, not completions. A member can therefore hold several
      in-flight runs of a slow command; /predict is the one that matters and it
      bounds its own fan-out.
    - A command body invoked *internally* by another command bypasses this check
      entirely, because __call__ on the command object goes straight to the
      callback and never runs can_run. That is why the cross-command paths call
      plain helpers rather than decorated commands.
    """
    hits = {}

    def decorator(command):
        async def predicate(interaction):
            now = time.monotonic()
            if len(hits) > _RATE_PRUNE_AT:
                for k in [k for k, v in hits.items() if now - v[-1] >= period]:
                    del hits[k]
            recent = [t for t in hits.get(interaction.user.id, ())
                      if now - t < period]
            if len(recent) >= invocations:
                raise nextcord.ApplicationCheckFailure(
                    f"Slow down — `/{command.name}` allows {invocations} use"
                    f"{'s' if invocations > 1 else ''} per {period}s."
                )
            recent.append(now)
            hits[interaction.user.id] = recent
            return True
        command.add_check(predicate)
        return command
    return decorator

# # T I S M I S H help-command
@rate_limit(1, 1)
@bot.slash_command(name="help", description="All you need")
async def help(interaction: nextcord.Interaction, helpstr: str = nextcord.SlashOption(
    name='help_choices', description='Choose one of the help commands',
    # A list, not a set: nextcord turns a non-dict choices value into
    # {v: v for v in choices}, so a set literal makes the emitted option order
    # depend on PYTHONHASHSEED. The payload then differs between restarts, and
    # nextcord's restart-time payload comparison responds by re-registering the
    # command with Discord for no reason.
    required=False, choices=["member commands", "tm commands"]
)):
    # List of TM and Member commands.
    # This is a hand-maintained inventory and it had drifted from what the
    # decorators actually enforce: /role is gated by the manage_roles permission
    # and /spotifyplay is not gated at all (readme documents it as a normal
    # command, and /play routes Spotify URLs into it). Neither belongs under
    # "tm commands".
    commands_dict = {
        "tm commands": [
            skip_command, del_command, move_command, clear_command, seek_command,
            volume_command, skipto_command, shuffle_command, loop_command,
            disconnect_command, loopqueue_command, restart_command,
            predict_command
        ],
        "member commands": [
            ping_command, play_command, pause_command, resume_command,
            nowplaying_command, queue_command, save_command, spotifyplay_command,
            visualizer_command
        ]
    }

    # Function to format the command list
    def format_command_list(cmd_list):
        return "\n\n".join([f"**/{cmd.name}** - `function: {cmd.description}`" for cmd in cmd_list])

    # Check the chosen help category and prepare the corresponding embed
    if helpstr in commands_dict:
        embed = nextcord.Embed(
            title=f"{helpstr.capitalize()} Help Commands",
            description=format_command_list(commands_dict[helpstr]),
            color=embed_color,
        )
        await interaction.response.send_message(embed=embed)
    else:
        # General help message if no specific category is chosen
        all_commands = {category: format_command_list(cmds) for category, cmds in commands_dict.items()}
        help_description = (
            f"{bot.description}\n\n**Member Commands**\n{all_commands['member commands']}\n\n"
            f"**TM Commands**\n{all_commands['tm commands']}\n\n"
        )
        embed = nextcord.Embed(
            title="Tishmish Help", description=help_description, color=embed_color
        )
        embed.add_field(
            name="View more options with `/help +1 options`",
            value="To use TM commands, server owner/admin can provide **tm** role to the member\n"
                  "Grant it with `/role` (requires the **Manage Roles** permission).\n"
                  "[Help](https://github.com/awmie/tishmish/blob/main/readme.md)",
        )
        await interaction.response.send_message(embed=embed)



# T I S H M I S H commands

@rate_limit(1, 2)
@require_permission(manage_roles=True)
@bot.slash_command(
    name="role",
    description="sets an existing role which are below tishmish(role) for a user",
    dm_permission=False,
)
async def set_role_command(interaction: interactions.Interaction, user: nextcord.Member, role: nextcord.Role):
    # is_assignable covers @everyone, managed roles and roles the bot cannot
    # touch; the old position test against guild.me mislabelled the managed case
    # as a generic permission problem.
    if not role.is_assignable():
        return await interaction.response.send_message(
            f"I cannot manage **{role.name}**.", ephemeral=True
        )
    # The INVOKER must outrank the role. The previous test compared the role
    # against the target member's top role, which refused the documented use case
    # every single time -- handing `tm` to an ordinary member, whose top role is
    # @everyone at position 0 -- while succeeding when aimed at someone who
    # already outranked it. 13 commands are gated on tm and /help sends admins
    # here to grant it.
    if not interaction.user.top_role > role:
        return await interaction.response.send_message(
            f"You cannot grant **{role.name}**: it is at or above your own top role.",
            ephemeral=True,
        )
    await user.add_roles(role)
    embed = nextcord.Embed(
        description=f"`{user.name}` has been given a role called: **{role.name}**",
        color=embed_color
    )
    await interaction.response.send_message(embed=embed)

async def user_connectivity(interaction: interactions.Interaction, *, same_channel=True):
    """Gate a player command. Raises for "cannot run here", returns False after
    already telling the member what to do.

    same_channel=False is for the read-only commands (nowplaying, queue, save),
    where a member in a different channel, or in none, is still allowed to look.
    """
    # A DM gives a User with no .voice and a guild of None. Raise rather than
    # return False, and rather than let the attribute read blow up.
    if interaction.guild is None:
        raise nextcord.ApplicationCheckFailure(
            "Music commands only work inside a server."
        )
    if not getattr(interaction.user, "voice", None):
        await interaction.send("Join a voice channel first!", ephemeral=True)
        return False
    # Every caller indexes into vc.state.queue and reads vc.current after this returns,
    # so the member being in voice is only half the precondition: if the bot is
    # not connected the player is None and those commands die with AttributeError
    # instead of telling anyone.
    vc = interaction.guild.voice_client
    if vc is None:
        await interaction.send("I am not connected to a voice channel!", ephemeral=True)
        return False
    # Guild.voice_client is keyed by guild id, so one player per guild. Without
    # this check, any member in any *other* voice channel could pause, skip,
    # clear or disconnect a player they cannot hear.
    if same_channel:
        # mafic.Player exposes no `.channel`, so the id is recorded on GuildState
        # at construction. Compared as ids, never objects: `int != VoiceChannel`
        # is always True and would refuse every legitimate call.
        member_channel = interaction.user.voice.channel
        player_channel_id = getattr(getattr(vc, "state", None), "channel_id", None)
        if player_channel_id is not None and member_channel.id != player_channel_id:
            player_channel = interaction.guild.get_channel(player_channel_id)
            where = getattr(player_channel, "mention", "the bot's voice channel")
            await interaction.send(
                f"You need to be in {where} to control the player.",
                ephemeral=True,
            )
            return False
    return True

# Held at module level on purpose. nextcord re-dispatches `ready` on every fresh
# IDENTIFY after a session invalidation, and `bot.loop.create_task(...)` kept no
# reference, so a routine reconnect started a second immortal node_connect loop
# beside the first -- each one calling create_node, each appending a new Node to
# the class-level pool.
_node_connect_task = None


@bot.event
async def on_ready():
    global _node_connect_task
    log.info("logged in as: %s", bot.user.name)
    if _node_connect_task is None:
        # Deliberately no `or _node_connect_task.done()` clause: node_connect's
        # only success exit is a plain return, so a HEALTHY task is permanently
        # done() and that clause would re-spawn the loop on every ready.
        _node_connect_task = bot.loop.create_task(node_connect())
    # Runs on every ready, so it is guarded internally; a reconnect re-dispatches
    # ready and re-installing a handler over the same loop would be harmless but
    # noisy.
    _install_signal_handlers()
    await bot.change_presence(
        activity=nextcord.Activity(type=nextcord.ActivityType.listening, name="/play")
    )


async def reply_quietly(interaction, description):
    """Reply in a way that cannot itself fail.

    An error handler that raises is worse than one that only logs: the second
    exception escapes into event dispatch. Replying fails for entirely ordinary
    reasons here -- the member dismissed the interaction, or the three second
    window closed while a long command was still working.
    """
    try:
        await interaction.send(
            embed=nextcord.Embed(description=description, color=embed_color),
            ephemeral=True,
        )
    except nextcord.DiscordException as exc:
        member = getattr(getattr(interaction, "user", None), "id", "?")
        log.warning("could not report to member %s: %r", member, exc)


@bot.event
async def on_application_command_error(interaction, error):
    """Make refusals and failures visible.

    nextcord's default handler only prints the traceback to stderr, so a blocked
    or throttled command looked like a dead bot -- Discord reports an unanswered
    interaction as "the application did not respond". Every gate here raises
    ApplicationCheckFailure with a reason, which is forwarded to the member
    ephemerally; anything unexpected is logged and answered generically.
    """
    # Unwrap first. A predicate that raises is dispatched to this event
    # UNWRAPPED (application_command.py:900-902), but an exception raised from
    # inside a command BODY is wrapped in ApplicationInvokeError
    # (application_command.py:920-924). user_connectivity raises from inside
    # bodies, so without this the authored refusal -- "Music commands only work
    # inside a server." -- is lost behind a generic "something went wrong".
    if isinstance(error, nextcord.ApplicationInvokeError):
        error = error.original

    command_name = getattr(getattr(error, "command", None), "name", None)
    if command_name is None:
        command_name = getattr(
            getattr(interaction, "application_command", None), "name", "?"
        )

    if isinstance(error, nextcord.ApplicationCheckFailure):
        await reply_quietly(
            interaction, str(error) or "You cannot use that command right now."
        )
        return

    if isinstance(error, (nextcord.NotFound, nextcord.InteractionResponded)):
        # NotFound 10062 means the three second interaction window closed while
        # the command was still working: expected when a slow command answers
        # late, and a traceback would be noise on top of it.
        log.warning("%s could not be answered: %s", command_name, error)
        return

    log.error("unhandled exception in /%s: %r", command_name, error)
    traceback.print_exception(type(error), error, error.__traceback__, file=sys.stderr)
    # An expired interaction cannot be answered at all, and a command that threw
    # after already replying must not turn one error into two.
    await reply_quietly(interaction, "Something went wrong running that command.")


@bot.event
async def on_application_command_completion(interaction):
    # The audit's "logging / observability" gap: nothing recorded that a command
    # ran at all, so "did the member's /skip work or was it refused?" was
    # unanswerable after the fact. Ids, not names: names need a fetch, and this
    # runs on every command.
    log.info(
        "ran /%s guild=%s user=%s",
        getattr(getattr(interaction, "application_command", None), "name", "?"),
        getattr(getattr(interaction, "guild", None), "id", "DM"),
        getattr(getattr(interaction, "user", None), "id", "?"),
    )


def _voice_channel(player):
    """The channel the player is in, for announcements.

    mafic.Player carries no `.channel`, so the id recorded on GuildState at
    construction is the only route back to it.
    """
    channel_id = getattr(getattr(player, "state", None), "channel_id", None)
    return bot.get_channel(channel_id) if channel_id is not None else None


async def _announce(player, description):
    """Tell the text feed of the voice channel what happened to playback.

    Event handlers have no interaction to answer with, and a handler that raises
    here would be strictly worse than one that only logs.
    """
    channel = _voice_channel(player)
    if channel is None:
        return
    try:
        await channel.send(
            embed=nextcord.Embed(description=description, color=embed_color),
            delete_after=10,
        )
    except nextcord.DiscordException as exc:
        log.warning("could not report a playback problem to the channel: %r", exc)


def _reason_name(reason):
    """EndReason (or a raw string from an older node) normalised to lower case.

    Case-insensitive on purpose: mafic's EndReason values are lowercase
    ('cleanup'), but a Lavalink 3.7.13 node was observed sending UPPERCASE
    ('CLEANUP'). A gate that compared exactly would silently stop the queue on
    one server version and work on another.
    """
    return str(getattr(reason, "value", reason) or "").lower()


def _track_key(track):
    return getattr(track, "id", None) or getattr(track, "identifier", None)


@bot.event
async def on_track_start(event):
    # The absence of this line in a log is the proof that no audio ever started.
    log.info("track started: %s", getattr(event.track, "title", event.track))


@bot.event
async def on_track_exception(event):
    # Without a handler this was invisible: five consecutive failures produced
    # "Search found" and then silence, because the old backend dispatched the
    # event and nobody listened (and it read `error` where Lavalink 3.7 sends
    # `exception`, so even the log line said None).
    #
    # Advance HERE rather than waiting for track_end. Measured on a live node:
    # the follow-up end event arrived 64-65 seconds late, and once never at all,
    # leaving the player sitting on a dead track while the member was told it was
    # skipped. state.abandoned_id makes that safe: the late end event for this
    # same track is ignored instead of advancing twice.
    player, track = event.player, event.track
    title = getattr(track, "title", "that track")
    log.error("track exception for %r: %s", title, getattr(event, "error", None))
    player.state.abandoned_id = _track_key(track)
    await _announce(player, f"`{title}` could not be streamed. Skipping it.")
    await _advance_queue(player, finished=track)


@bot.event
async def on_track_end(event):
    player, track, reason = event.player, event.track, event.reason
    name = _reason_name(reason)
    log.debug("track end reason=%r (%s) track=%r", reason, name, getattr(track, "title", track))

    # A late end event for a track we already gave up on must not advance again:
    # that is how one bad stream silently costs two songs.
    if player.state.abandoned_id and player.state.abandoned_id == _track_key(track):
        player.state.abandoned_id = None
        log.info("ignoring late track_end for the already-skipped %r",
                 getattr(track, "title", track))
        return

    # stopped/replaced are produced by our own /skip and by a replacement play,
    # and those callers now advance themselves; cleanup arrives after the player
    # was torn down. Anything unrecognised falls through to advancing, because a
    # gate that silently blocks everything is the exact outage this file keeps
    # chasing.
    if name in ("stopped", "replaced", "cleanup"):
        return

    if not player.is_connected():
        return
    if player.state.loop_track:
        # start_time=0 is load-bearing, not decoration. `track` is the track from
        # the END event, and Lavalink encodes that one with the position it
        # stopped at: a 199s track arrived here encoded with position=198360.
        # Replaying it unchanged made every /loop restart 0.64s before the end,
        # finish instantly, and restart again -- an unthrottled replay storm that
        # hit the node ~72 times in 30s until the member disconnected. Starting
        # at 0 replays the song instead of its final tick.
        return await player.play(track, start_time=0)
    await _advance_queue(player, finished=track)


@bot.event
async def on_track_stuck(event):
    # A stuck track never produces track_end, so this one must advance or the
    # queue halts forever on a dead stream.
    player, track = event.player, event.track
    title = getattr(track, "title", "that track")
    log.error("track stuck: %r (threshold=%s)", title, getattr(event, "threshold", None))
    player.state.abandoned_id = _track_key(track)
    await _announce(player, f"`{title}` stalled. Moving on.")
    await _advance_queue(player, finished=track)


@bot.event
async def on_websocket_closed(event):
    # Discord closing the voice websocket is how audio vanishes while every
    # command still reports "playing" -- this is the handler that caught the
    # 4017 E2EE/DAVE refusal. The old backend dispatched it to nobody.
    player = event.player
    code = getattr(event, "code", None)
    reason = getattr(event, "reason", None)
    log.error(
        "voice websocket closed: code=%s reason=%r (node=%s)",
        code, reason, getattr(getattr(player, "node", None), "label", "?"),
    )
    await _announce(
        player,
        f"Lost the voice connection to Discord (code {code}). Try /play again.",
    )


@bot.event
async def on_node_ready(node):
    # Readiness is confirmed by node.available and by this event; the previous
    # backend swallowed connect failures into a logger nobody had configured and
    # returned a Node anyway, so "create_node succeeded" meant nothing.
    log.info("lavalink node %s ready (available=%s)", node.label, node.available)


@bot.event
async def on_node_unavailable(node):
    # The failure half of the same story: without this, a node that dies after
    # start-up is silent and the guilds keep reporting a healthy player.
    log.error("lavalink node %s is no longer available", getattr(node, "label", "?"))


# Without these four the process cannot do anything at all.
REQUIRED_ENV = (
    "TOKEN",
    "LAVALINK_HOST",
    "LAVALINK_PORT",
    "LAVALINK_PASSWORD",
)

# Without these two, only /spotifyplay is unavailable. They were required before,
# so a bot with no Spotify developer app could not start -- and therefore could
# not play a YouTube link either, which needs neither credential.
OPTIONAL_ENV = (
    "SPOTIFY_CLIENT_ID",
    "SPOTIFY_CLIENT_SECRET",
)


def missing_env():
    """Names in REQUIRED_ENV that are unset or empty.

    Each was read with a bare os.getenv and no default, so a missing or typo'd
    variable reached nextcord as None -- an opaque traceback, or a bot that
    logged in, looked alive, and had no audio backend.
    """
    return [name for name in REQUIRED_ENV if not os.getenv(name)]


def missing_optional_env():
    return [name for name in OPTIONAL_ENV if not os.getenv(name)]


def spotify_configured():
    """Whether both Spotify credentials are present. Partial config counts as
    absent: one of the two cannot authenticate, and failing at the command is
    better than constructing a client that 401s on every call."""
    return not missing_optional_env()


def env_flag(name):
    """Parse an env var as a bool.

    https= received the raw string before, so LAVALINK_SECURE="false" was
    truthy and asked for an encrypted node connection from someone who opted out.
    """
    return (os.getenv(name) or "").strip().lower() in {"1", "true", "yes", "on"}


NODE_LABEL = "tishmish"
# Created lazily in node_connect: mafic's NodePool needs the client, and building
# it at import time would also mean a NodePool exists for every `import app`
# (including the offline verification harness) that never connects.
pool = None


async def node_connect():
    """Bring the Lavalink node up, and do not treat a returned Node as success.

    Runs only after login: mafic sends a `User-Id` header built from
    client.user.id, so with an unlogged-in client (user is None) create_node
    hangs instead of failing. on_ready is therefore the only correct place to
    call this, which is also what keeps the Spotify credentials out of the
    import path.

    The `available` check is not ceremony. The previous backend swallowed every
    connect error and returned a Node anyway, so a returned node proved nothing
    and the retry loop exited on its first pass with Lavalink down -- the exact
    zombie the retry existed to prevent. mafic does raise (TimeoutError from its
    ready-wait), but it logs 'Connected to lavalink' and spawns a listener over a
    null socket on the way there, so the return value is still not proof.
    """
    global pool
    await bot.wait_until_ready()
    # Resolve the port once, outside the retry loop: it sat inside the try below,
    # so a typo'd value raised ValueError, got swallowed as a connection failure,
    # and retried forever with backoff instead of reporting a bad config.
    try:
        port = int(os.getenv('LAVALINK_PORT'))
    except (TypeError, ValueError):
        log.error(
            "LAVALINK_PORT is not a number (%r); not connecting to any node.",
            os.getenv('LAVALINK_PORT'),
        )
        return
    host = os.getenv('LAVALINK_HOST')
    if not spotify_configured():
        # Missing Spotify credentials disable one command. They used to stop the
        # whole process from starting, which also stopped YouTube playback.
        log.warning(
            "%s not set: /spotifyplay is disabled, everything else works.",
            ", ".join(missing_optional_env()),
        )
    if pool is None:
        pool = mafic.NodePool(bot)
    delay = 5
    while True:
        node = None
        try:
            # label is the pool key, so reusing NODE_LABEL means a retry replaces
            # this node instead of accumulating a second one (the old backend
            # generated a random identifier per attempt and never removed the
            # corpse). player_cls matters just as much: on reconnect mafic rebuilds
            # players through sync_players(), and without it they come back as bare
            # mafic.Players with no GuildState, no queue and no channel_id.
            node = await pool.create_node(
                host=host,
                port=port,
                label=NODE_LABEL,
                password=os.getenv('LAVALINK_PASSWORD'),
                secure=env_flag('LAVALINK_SECURE'),
                timeout=20.0,
                player_cls=Player,
            )
        except Exception as exc:
            log.error("lavalink create_node raised: %r (retrying in %ss)", exc, delay)
            node = None
        else:
            if getattr(node, "available", False):
                log.info(
                    "lavalink node %s connected at %s:%s (version %s)",
                    node.label, host, port, getattr(node, "version", "?"),
                )
                return
            log.error(
                "lavalink node %s came up unavailable -- %s:%s is unreachable; "
                "retrying in %ss", node.label, host, port, delay,
            )
        if node is not None:
            # Drop it before retrying so the pool never holds two nodes for one
            # label, and so its aiohttp session and listener task do not leak.
            try:
                await node.close()
            except Exception as exc:
                log.warning("could not close the dead node: %r", exc)
        await asyncio.sleep(delay)
        delay = min(delay * 2, 300)


def pick_node():
    """The node to search through, or None while none is available."""
    if pool is None:
        return None
    try:
        return pool.get_random_node()
    except Exception as exc:
        # mafic raises NoNodesAvailable with an empty pool; callers already have a
        # message for that case, so it is a return value rather than an exception
        # travelling to the generic error handler.
        log.warning("no lavalink node available: %r", exc)
        return None


NO_NODE_MESSAGE = "No audio node is connected yet — try again in a moment."


async def resolve_tracks(node, query):
    """Search or load `query`. Returns list[Track], Playlist, or None.

    mafic skips the search prefix when the query is a URL (node.py:1168), so a
    plain YouTube watch/playlist link is loaded exactly rather than text-searched
    -- which is what the readme has always promised and the previous client could
    not do.
    """
    # .value, not the enum. SearchType is NOT a str subclass (isinstance(..., str)
    # is False), and mafic builds the identifier with f"{search_type}:{query}"
    # (node.py:1169), so passing the enum sent the literal text
    # "SearchType.YOUTUBE:weeknd" to Lavalink as a search query. Every /play then
    # returned nothing while the node logged a perfectly formed request for junk.
    return await node.fetch_tracks(query, search_type=SearchType.YOUTUBE.value)


@rate_limit(1, 2)
@require_owner()
@require_role("tm")
@bot.slash_command(name="info", description="shows information about the bot")
async def info_command(interaction: interactions.Interaction):
    await interaction.response.send_message(
        embed=nextcord.Embed(
            description=f"**Info**\ntotal server count: `{len(bot.guilds)}`",
            color=embed_color,
        )
    )


@rate_limit(1, 2)
@require_role("tm")
@bot.slash_command(
    name="loopqueue",
    description="loops the queue",
    dm_permission=False,
)
async def loopqueue_command(interaction: interactions.Interaction, type: str=nextcord.SlashOption(
    name="lq-options", description='options for loop queue', required=True, choices=["start", "stop"]
)):
    if await user_connectivity(interaction) == False:
        return
    vc: Player = interaction.guild.voice_client
    state = vc.state
    # This command used to mutate the queue: start injected the currently
    # playing source into the backend's private deque and stop tried to remove
    # it again by comparing an AudioSource to a Track, a guard that could never
    # fire -- so switching loopqueue off left a duplicate that played after the
    # queue had drained. Looping is now a flag and nothing else: TrackQueue
    # .advance(finished=..., loop_queue=True) rotates the finished track to the
    # back exactly once, which is covered by tests/test_queue.py.
    if type == "start":
        if state.loop_queue:
            return await interaction.send(
                embed=nextcord.Embed(
                    description="**loopqueue** is already `enabled`", color=embed_color
                ),
                ephemeral=True,
            )
        state.loop_queue = True
        return await interaction.send(
            embed=nextcord.Embed(
                description="**loopqueue**: `enabled`", color=embed_color
            )
        )
    if not state.loop_queue:
        return await interaction.send(
            embed=nextcord.Embed(
                description="**loopqueue** is already `disabled`", color=embed_color
            ),
            ephemeral=True,
        )
    state.loop_queue = False
    return await interaction.send(
        embed=nextcord.Embed(
            description="**loopqueue**: `disabled`", color=embed_color
        )
    )

@rate_limit(1, 2)
@bot.slash_command(name="ping", description="displays bot's latency")
async def ping_command(interaction: interactions.Interaction):
    # bot.latency is infinity before the first HEARTBEAT_ACK and again while the
    # gateway is reconnecting (nextcord clears the keep-alive handler), and
    # round(inf * 1000) raises OverflowError -- so the command reliably died
    # during exactly the connection trouble it is used to check.
    latency_ms = "n/a" if not math.isfinite(bot.latency) else round(bot.latency * 1000)
    em = nextcord.Embed(
        description=f"**Pong!**\n\n`{latency_ms}`ms", color=embed_color
    )
    await interaction.response.send_message(embed=em, delete_after=5)


async def _play_one(interaction, vc, search, *, announce=True):
    """Resolve `search`, then start it or queue it. Returns the track, or None.

    Shared by /play and /predict, and deliberately a plain coroutine rather than a
    call to the decorated command. Invoking a SlashApplicationCommand object runs
    only its callback (application_command.py:621-629): can_run never executes, so
    every add_check gate this file installs is bypassed. Four sites used to do
    exactly that -- /play into /spotifyplay, /skip into /queue, /skipto into /skip,
    and /predict into /play up to ten times -- which read fine because nextcord
    permits it and was silently unthrottled. Calling a shared helper keeps the same
    (correct) behaviour while making it explicit, and `announce=False` is what
    stops one /predict from posting a dozen permanent channel messages.
    """
    node = pick_node()
    if node is None:
        if announce:
            await interaction.send(NO_NODE_MESSAGE, ephemeral=True)
        return None

    # mafic prefixes the search type only when the query is not a URL
    # (node.py:1168), so a plain https://www.youtube.com/watch?v=<id> is loaded
    # exactly instead of being text-searched, and a ?list= link comes back as a
    # Playlist. The previous client could not do either: it destroyed &list= while
    # normalizing and then text-searched whatever was left.
    try:
        results = await resolve_tracks(node, search)
    except mafic.TrackLoadException as exc:
        # loadType "error" raises this (node.py fetch_tracks). No-match does NOT:
        # it returns [] and is handled below.
        log.warning("track lookup failed for %r: %r", search, exc)
        if announce:
            await interaction.send(
                embed=nextcord.Embed(
                    description="No results for that search.", color=embed_color
                ),
                ephemeral=True,
            )
        return None

    # A Playlist and a list of search matches are DIFFERENT answers. Only a
    # playlist means "queue all of these"; a ytsearch: result is 20 alternative
    # matches for the same request, and queueing the tail of it meant /play
    # weeknd silently filled the queue with 19 other Weeknd videos.
    if isinstance(results, Playlist):
        tracks, queue_the_rest = list(results.tracks), True
    else:
        tracks, queue_the_rest = list(results or []), False

    if not tracks:
        # An empty list is a real answer (no matches), and indexing [0] on it used
        # to surface as an IndexError reported as "something went wrong".
        if announce:
            await interaction.send(
                embed=nextcord.Embed(
                    description="No results for that search.", color=embed_color
                ),
                ephemeral=True,
            )
        return None

    first_track, rest = tracks[0], (tracks[1:] if queue_the_rest else [])
    state = vc.state
    # Recorded before playback, not after. This used to sit at the very end of the
    # command -- a network round trip after the track actually started -- so
    # /nowplaying inside that window answered "Song not found" about a song that
    # was already audible.
    state.remember(first_track, interaction.user.mention)

    if state.queue.is_empty and not vc.current:
        await vc.play(first_track)
        # A playlist still has to fit the cap; the surplus is reported rather than
        # silently dropped mid-list.
        overflow = 0
        for track in rest:
            try:
                state.queue.append(track)
                state.remember(track, interaction.user.mention)
            except QueueFull:
                overflow += 1
        if announce:
            extra = (
                f"\n\n`{len(rest) - overflow}` more queued, `{overflow}` dropped at the "
                f"{QUEUE_MAX} limit" if rest else ""
            )
            await interaction.send(
                embed=nextcord.Embed(
                    description=f"**Search found**\n\n`{first_track.title}`{extra}",
                    color=embed_color,
                ),
                delete_after=5,
            )
    else:
        added = 0
        overflow = 0
        for track in [first_track] + rest:
            try:
                state.queue.append(track)
                state.remember(track, interaction.user.mention)
                added += 1
            except QueueFull:
                overflow += 1
                break
        if announce:
            if not added:
                return await interaction.send(
                    embed=nextcord.Embed(
                        description=f"The `QUEUE` is full at {QUEUE_MAX} songs.",
                        color=embed_color,
                    ),
                    ephemeral=True,
                )
            note = f" (+{overflow} dropped at the limit)" if overflow else ""
            await interaction.send(
                embed=nextcord.Embed(
                    description=f"Added to the `QUEUE`\n\n`{first_track.title}`"
                                f" and {added - 1} more{note}",
                    color=embed_color,
                )
            )

    state.loop_track = False
    return first_track


@rate_limit(1, 1)
@bot.slash_command(
    name="play", description="plays the given track provided by the user",
    dm_permission=False,
)
async def play_command(interaction: interactions.Interaction, *, search: str):
    # Answer the interaction before doing any network work. channel.connect
    # carries a 60s handshake budget (nextcord abc.py:1746) and the search is a
    # Lavalink round trip; both used to run before the first send, and past
    # Discord's three-second window the interaction token is invalidated so
    # nothing at all can be said (NotFound 10062, which reply_quietly then re-hit
    # and swallowed). A slow node therefore turned /play into a silent no-op.
    # The is_done() guard is load-bearing: /predict defers and then runs this
    # body, and a second defer raises InteractionResponded.
    if not interaction.response.is_done():
        await interaction.response.defer()

    if interaction.guild is None:
        raise nextcord.ApplicationCheckFailure("`/play` only works in a server.")
    if not getattr(interaction.user, "voice", None):
        return await interaction.send("Join a voice channel first!", ephemeral=True)

    try:
        vc: Player = interaction.guild.voice_client
        if vc is None:
            vc = await interaction.user.voice.channel.connect(cls=Player)
    except (mafic.MaficException, ValueError):
        # An empty pool raises ValueError from get_random_node and mafic's own
        # resolution raises MaficException, so a /play racing node start-up gets a
        # message instead of a traceback.
        return await interaction.send(NO_NODE_MESSAGE, ephemeral=True)

    if search.startswith(
        (
            "https://open.spotify.com/playlist/",
            "https://open.spotify.com/album/",
            "https://open.spotify.com/track/",
        )
    ):
        # Outside any try/except on purpose. This used to be wrapped in
        # `except Exception: reply("Invalid Spotify URL")`, so every failure
        # inside -- a dead node, a failed queue write, a bad YouTube lookup --
        # was reported as a malformed Spotify link, and because the exception was
        # caught the error event never fired, so the real cause was never logged.
        return await spotifyplay_command(interaction, search, limit=10)

    return await _play_one(interaction, vc, search)


async def _advance_queue(player, finished=None):
    """Play what is next, or announce that the queue is done.

    The ONLY queue-advance path, shared by track_end, track_exception and
    track_stuck. Extracted because those three now all need to do the same thing
    and an inline copy per handler is how they drift out of sync.
    """
    state = player.state
    channel = _voice_channel(player)
    try:
        # advance() owns the loop-queue rotation: the old code re-put
        # queue._queue[0] by hand here, which duplicated the head every track and
        # was the origin of the "loopqueue leaves a ghost song" bug.
        next_song = state.queue.advance(finished=finished, loop_queue=state.loop_queue)
        if next_song is None:
            if channel is not None:
                await channel.send(
                    embed=nextcord.Embed(
                        description="The queue is empty.", color=embed_color
                    ),
                    delete_after=5,
                )
            # False, not None-with-a-silent-caller: /skip needs to know that
            # nothing was left so it can say so instead of claiming a skip.
            return False
        # start_time=0 matters here for the same reason as the /loop replay:
        # /loopqueue rotates the FINISHED track -- encoded with its end position
        # -- back onto the queue, so a rotated track would otherwise start at its
        # last tick too.
        await player.play(next_song, start_time=0)
        if channel is not None:
            await channel.send(
                embed=nextcord.Embed(
                    description=f"**Now playing from the queue:**\n\n`{next_song.title}`",
                    color=embed_color,
                ),
                # Track.length is MILLISECONDS under mafic (the previous backend
                # divided by 1000 in its own model, which is why /seek and the
                # duration render were wrong there). Capped so a live stream does
                # not schedule an hour of pending deletion.
                delete_after=min(600, max(5, (getattr(next_song, "length", 5000) or 5000) / 1000)),
            )
    except Exception:
        # An error handler that raises is worse than one that only logs: the
        # second exception escapes into event dispatch, and this path used to
        # dereference player.channel unconditionally on top of that.
        log.exception("could not advance the queue after this track ended")
        if channel is None:
            return
        try:
            await channel.send(
                embed=nextcord.Embed(
                    description="An error occurred while playing the next song.",
                    color=embed_color,
                ),
                delete_after=5,
            )
        except nextcord.DiscordException as exc:
            log.warning("could not report the queue failure to the channel: %r", exc)


# ---------------------------------------------------------------------------
# Spotify, directly. nextwave shipped an ext.spotify; mafic ships nothing, so
# the client-credentials flow now lives here. Two rules the deleted extension
# got wrong and are kept deliberately:
#   - the client secret travels ONLY in the Basic auth header, never in a URL
#     (a secret in a query string lands in every proxy and access log between
#     here and api.spotify.com);
#   - the playlist identifier is normalize_spotify_url's canonical form, because
#     the old extension interpolated member-supplied text into the request path
#     and yarl resolves dot segments -- `../../me` reached
#     https://api.spotify.com/v1/me under our token.
# ---------------------------------------------------------------------------
SPOTIFY_TIMEOUT = 15.0
SPOTIFY_TOKEN_URL = "https://accounts.spotify.com/api/token"
SPOTIFY_API = "https://api.spotify.com/v1"
SPOTIFY_PAGE = 100  # the API's per-page maximum for playlist/album tracks


class SpotifyError(Exception):
    """Anything that means 'this Spotify link could not be read'."""


async def _spotify_token(session):
    auth = base64.b64encode(
        f"{os.getenv('SPOTIFY_CLIENT_ID')}:{os.getenv('SPOTIFY_CLIENT_SECRET')}".encode()
    ).decode()
    async with session.post(
        SPOTIFY_TOKEN_URL,
        data={"grant_type": "client_credentials"},
        headers={"Authorization": f"Basic {auth}"},
        timeout=aiohttp.ClientTimeout(total=SPOTIFY_TIMEOUT),
    ) as resp:
        if resp.status != 200:
            # status only: the body can contain a correlation id we do not want.
            raise SpotifyError(f"authentication failed (HTTP {resp.status})")
        return (await resp.json())["access_token"]


async def _spotify_get(session, token, path, params=None):
    async with session.get(
        f"{SPOTIFY_API}{path}",
        params=params,
        headers={"Authorization": f"Bearer {token}"},
        timeout=aiohttp.ClientTimeout(total=SPOTIFY_TIMEOUT),
    ) as resp:
        if resp.status == 404:
            raise SpotifyError("that playlist or album does not exist")
        if resp.status in (401, 403):
            raise SpotifyError("Spotify refused the request for this link")
        if resp.status != 200:
            raise SpotifyError(f"Spotify returned HTTP {resp.status}")
        return await resp.json()


async def spotify_titles(canonical_url, limit):
    """[(title, artist), ...] for a canonical Spotify playlist/album/track URL."""
    kind, ident = re.match(
        r"^https://open\.spotify\.com/(playlist|album|track)/([0-9A-Za-z]{16,26})$",
        canonical_url,
    ).groups()
    out = []
    async with aiohttp.ClientSession() as session:
        token = await _spotify_token(session)
        if kind == "track":
            item = await _spotify_get(session, token, f"/tracks/{ident}")
            artists = item.get("artists") or []
            out.append((item.get("name") or "", (artists[0].get("name") if artists else "")))
            return out

        # A playlist's items sit under .items (playlist) or .items[].track (album);
        # both page the same way and both can carry nulls for deleted tracks.
        path = f"/playlists/{ident}/tracks" if kind == "playlist" else f"/albums/{ident}/tracks"
        page = {"limit": min(SPOTIFY_PAGE, max(1, limit)), "offset": 0}
        while len(out) < limit:
            data = await _spotify_get(session, token, path, params=page)
            for entry in data.get("items") or []:
                item = entry.get("track") if kind == "playlist" else entry
                if item is None or item.get("is_local"):
                    continue
                artists = item.get("artists") or []
                out.append((
                    item.get("name") or "",
                    artists[0].get("name") if artists else "",
                ))
                if len(out) >= limit:
                    break
            nxt = data.get("next")
            if not nxt or len(out) >= limit:
                break
            # Follow only the cursor we built, never the URL Spotify hands back:
            # the old extension requested data["next"] verbatim with the bearer
            # header attached, so a hostile or compromised response could point
            # the next request at an arbitrary host.
            page["offset"] += page["limit"]
    return out


@rate_limit(1, 1)
@bot.slash_command(
    name="spotifyplay",
    description="plays the provided spotify playlist link up to the provided song number",
    dm_permission=False,
)
async def spotifyplay_command(
    interaction: interactions.Interaction, search: str, limit: int = 100
):
    # First thing, before the defer and before any network call: this is free
    # text from any member and the backend interpolates it into a URL it requests
    # with the Spotify bearer token attached.
    canonical = normalize_spotify_url(search)
    if canonical is None:
        return await interaction.response.send_message(
            embed=nextcord.Embed(
                description="That is not a Spotify link I can use. Paste the whole "
                            "link, like `https://open.spotify.com/playlist/<id>`.",
                color=embed_color,
            ),
            ephemeral=True,
        )
    search = canonical

    if not interaction.response.is_done():
        await interaction.response.defer()
    if interaction.guild is None:
        raise nextcord.ApplicationCheckFailure(
            "`/spotifyplay` only works in a server."
        )
    if not getattr(interaction.user, "voice", None):
        return await interaction.send("Join a voice channel first!", ephemeral=True)

    try:
        vc: Player = interaction.guild.voice_client
        if vc is None:
            vc = await interaction.user.voice.channel.connect(cls=Player)
    except (mafic.MaficException, ValueError):
        return await interaction.send(NO_NODE_MESSAGE, ephemeral=True)

    node = pick_node()
    if node is None:
        return await interaction.send(NO_NODE_MESSAGE, ephemeral=True)

    # `total` is the caller's request, captured once and never mutated. The old
    # code decremented `limit` inside only the play-now branch and then keyed both
    # progress strings to `limit == 100`, so /play's internal call (limit=10)
    # printed "Song no. 92" for every track, and a busy player froze at
    # "Song no. 1 ... /100" for the whole playlist.
    limit = max(1, min(int(limit or 1), QUEUE_MAX))
    total = limit
    state = vc.state
    added = 0
    unmatched = 0

    queue_embed = nextcord.Embed(
        description="initializing the **QUEUE**...", color=embed_color
    )
    queue_completion = None
    try:
        queue_completion = await interaction.send(embed=queue_embed)

        try:
            wanted = await spotify_titles(search, total)
        except SpotifyError as exc:
            # The one failure that genuinely means the Spotify link is bad.
            # Anything unexpected propagates to on_application_command_error,
            # which logs the real cause instead of mislabelling it.
            log.warning("spotify lookup failed for %r: %s", search, exc)
            return await interaction.send(
                embed=nextcord.Embed(
                    description="That Spotify link could not be loaded.",
                    color=embed_color,
                ),
                ephemeral=True,
            )

        for title, artist in wanted:
            # Per-track guard: previously one failed lookup propagated out of the
            # whole loop, so a single unmatched title aborted the rest of the
            # playlist and left "initializing the QUEUE..." on screen forever.
            query = f"{title} {artist}".strip()
            try:
                found = await resolve_tracks(node, query)
            except (mafic.TrackLoadException, mafic.MaficException) as exc:
                unmatched += 1
                log.warning("could not resolve spotify track %r: %r", query, exc)
                continue
            track = found[0] if isinstance(found, list) and found else None
            if track is None:
                unmatched += 1
                continue

            # Spotify playlists are resolved into YouTube searches, so an album
            # of 90 tracks can exceed the cap halfway through. Stop cleanly rather
            # than raising out of the middle of the loop.
            try:
                state.queue.append(track)
            except QueueFull:
                break
            state.remember(track, interaction.user.mention)
            if state.queue.count == 1 and not vc.current:
                await vc.play(state.queue.get_next())
            added += 1

            queue_embed.description = (
                f"Added `{added}/{total}` from the playlist; "
                f"the **QUEUE** holds `{state.queue.count}`"
            )
            await queue_completion.edit(embed=queue_embed)

        state.loop_track = False

    finally:
        # Terminal status on every exit -- clean, refused, or raising.
        if queue_completion is not None:
            queue_embed.description = (
                f"Total successfully added to the **QUEUE**: `{added}`"
                + (f" (`{unmatched}` could not be matched)." if unmatched else ".")
            )
            try:
                await queue_completion.edit(embed=queue_embed)
            except nextcord.DiscordException as exc:
                log.warning("could not post the final queue status: %r", exc)

@rate_limit(1, 2)
@bot.slash_command(name="pause", description="pauses the current playing track", dm_permission=False)
async def pause_command(interaction: interactions.Interaction):
    if await user_connectivity(interaction) == False:
        return
    vc: Player = interaction.guild.voice_client

    # `elif vc.paused` used to be an unreachable arm of `if not vc.paused`, and
    # /resume had the same shape; both are now a straight two-way test.
    if not vc.current:
        return await interaction.response.send_message(
            embed=nextcord.Embed(
                description="Player is not `playing`!", color=embed_color
            ), delete_after=5
        )
    if vc.paused:
        return await interaction.response.send_message(
            embed=nextcord.Embed(
                description="Already in `PAUSED State`", color=embed_color
            ), delete_after=5
        )
    await vc.pause()
    return await interaction.response.send_message(
        embed=nextcord.Embed(description="`PAUSED` the music!", color=embed_color),
        delete_after=5,
    )


@rate_limit(1, 2)
@bot.slash_command(name="resume", description="resumes the paused track", dm_permission=False)
async def resume_command(interaction: interactions.Interaction):
    if await user_connectivity(interaction) == False:
        return
    vc: Player = interaction.guild.voice_client

    if not vc.current:
        return await interaction.response.send_message(
            embed=nextcord.Embed(
                description="Player is not `playing`!", color=embed_color
            ), delete_after=5
        )
    if not vc.paused:
        return await interaction.response.send_message(
            embed=nextcord.Embed(
                description="Already in `RESUMED State`", color=embed_color
            ), delete_after=5
        )
    await vc.resume()
    return await interaction.response.send_message(
        embed=nextcord.Embed(description="Music `RESUMED`!", color=embed_color),
        delete_after=5,
    )


def _queue_rows_embed(interaction, state, title_extra=""):
    """One page of the queue, character-bounded.

    /queue used to render every entry into a single embed description and blew
    past Discord's 4096-character limit at about 85 songs -- inside the 100 the
    readme advertises -- so the command failed rather than truncated.
    """
    lines = []
    used = 0
    shown = 0
    for row in state.queue.display_rows():
        line = f"**{row.position}**. {row.title}"
        if used + 1 + len(line) > 4000:
            break
        lines.append(line)
        used += 1 + len(line)
        shown += 1
    hidden = state.queue.count - shown
    if not lines:
        # A single title longer than the budget still has to say something.
        first = state.queue.display_rows()
        lines = [f"**{first[0].position}**. {first[0].title[:380]}"] if first else ["(empty)"]
        hidden = max(0, state.queue.count - 1)
    if hidden:
        lines.append(f"_...and {hidden} more_")
    return nextcord.Embed(
        title=f"**QUEUE [total song count:{state.queue.count}]**{title_extra}",
        description="\n".join(lines),
        color=embed_color,
    )


# ---------------------------------------------------------------------------
# Music visualiser -- real audio, not a fake wave
# ---------------------------------------------------------------------------
# Lavalink never hands the client audio (no PCM stream, no FFT), so the bot
# fetches the track itself with yt-dlp, decodes it to raw mono PCM with ffmpeg
# and FFTs it -- then the panel is drawn from the ACTUAL spectrum at the current
# playback position. Analysis is cached per track id, so replays and loops are
# instant. If a source cannot be fetched the panel says so instead of pretending.
def _visualizer_interval():
    """Seconds between panel edits, TISHMISH_VIS_INTERVAL to override.

    Discord rate-limits message edits to roughly 5 per 5s per channel (about one
    per second sustainable), and that bucket is shared with the bot's other
    sends in the channel. Going faster does not produce more frames -- nextcord
    just sleeps on the 429 -- and it can starve command replies, so the floor is
    clamped rather than left to a bad env value.
    """
    try:
        value = float(os.getenv("TISHMISH_VIS_INTERVAL", "1.0"))
    except (TypeError, ValueError):
        value = 1.0
    return max(0.5, min(10.0, value))


VISUALIZER_INTERVAL = _visualizer_interval()
VIS_BANDS = 32                 # spectrum columns
VIS_PANEL_ROWS = 7             # 2 spectrum rows + 5 waveform rows
_VIS_RAMP = "▁▂▃▄▅▆▇█"
_ANALYSIS_RATE = 8000          # Hz, mono
_ANALYSIS_HOP = 400            # 50 ms between spectrum frames
_ANALYSIS_WIN = 1024           # FFT window
_ANALYSIS_WAVE_RATE = 2000     # Hz kept for the oscilloscope
_ANALYSIS_WAVE_CUTOFF = 300.0  # low-pass so the wave is musical, not cymbals
_ANALYSIS_MAX_SECONDS = 900
_ANALYSIS_TIMEOUT = 120.0
_ANALYSIS_CACHE_MAX = 6
_ANALYSIS_SEM = asyncio.Semaphore(2)
_SPINNER = "|/-\\"

# guild_id -> _Visualizer
_VISUALIZERS = {}
# track key -> _Analysis
_ANALYSIS_CACHE = {}


class _Analysis:
    """Decoded, precomputed features for one track."""

    __slots__ = ("bands", "wave", "rate", "hop", "wave_rate")

    def __init__(self, bands, wave, rate, hop, wave_rate):
        self.bands = bands        # (frames, VIS_BANDS) float32, normalised 0..1
        self.wave = wave          # int16 band-limited waveform for the scope
        self.rate = rate
        self.hop = hop
        self.wave_rate = wave_rate


class _Visualizer:
    """State for one guild's live panel."""

    __slots__ = ("task", "message", "channel_id", "track_key", "last_position",
                 "last_text", "paused", "analysis", "analysis_failed",
                 "analysis_task", "spinner")

    def __init__(self, task=None, message=None, channel_id=None):
        self.task = task
        # The Message object itself, not a (channel_id, message_id) pair: the
        # panel is posted in the voice channel's text chat, and nextcord models
        # that as a VoiceChannel, which has no get_partial_message -- the exact
        # AttributeError that killed the first version on its first edit.
        self.message = message
        # Where to re-post the panel when playback resumes after silence.
        self.channel_id = channel_id
        self.track_key = None
        self.last_position = 0.0
        self.last_text = None
        self.paused = False
        self.analysis = None
        self.analysis_failed = False
        self.analysis_task = None
        self.spinner = 0


def _vis_time(seconds):
    seconds = max(0, int(seconds))
    minutes, secs = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes}:{secs:02d}"


def _audio_url(track):
    """The URL yt-dlp should fetch: the track's own uri when it has one, else a
    YouTube watch URL rebuilt from the video id."""
    uri = str(getattr(track, "uri", "") or "")
    if uri.startswith("http"):
        return uri
    identifier = getattr(track, "identifier", None)
    source = str(getattr(track, "source", "") or "").lower()
    if identifier and "youtube" in source:
        return f"https://www.youtube.com/watch?v={identifier}"
    return None


async def _decode_audio(url):
    """yt-dlp -> ffmpeg -> raw mono PCM, or None on any failure (missing binary,
    network error, unsupported source).

    The media is pulled into memory first and handed to ffmpeg on its stdin:
    asyncio subprocesses cannot take another subprocess's StreamReader as their
    stdin, so a direct yt-dlp|ffmpeg pipe is not available here.
    """
    try:
        ytdlp = await asyncio.create_subprocess_exec(
            "yt-dlp", "-f", "bestaudio/best", "-o", "-", "--no-playlist",
            "--no-warnings", "-q", "--no-progress",
            # The default web clients hand back media URLs that 403 today; the
            # mweb client still serves a playable stream. Verified on this node.
            "--extractor-args", "youtube:player_client=mweb",
            url,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
        )
    except (FileNotFoundError, NotImplementedError):
        return None
    try:
        media = await asyncio.wait_for(ytdlp.stdout.read(), _ANALYSIS_TIMEOUT)
    except asyncio.TimeoutError:
        media = b""
    finally:
        if ytdlp.returncode is None:
            ytdlp.kill()
        await ytdlp.wait()
    if not media:
        return None

    try:
        ffmpeg = await asyncio.create_subprocess_exec(
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-i", "pipe:0",
            "-f", "s16le", "-ac", "1", "-ar", str(_ANALYSIS_RATE), "pipe:1",
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
    except (FileNotFoundError, NotImplementedError):
        return None
    try:
        raw, _ = await asyncio.wait_for(ffmpeg.communicate(media), _ANALYSIS_TIMEOUT)
    except asyncio.TimeoutError:
        if ffmpeg.returncode is None:
            ffmpeg.kill()
        await ffmpeg.wait()
        return None
    if not raw:
        return None
    import numpy as np
    samples = np.frombuffer(raw, dtype=np.int16)
    if samples.size < _ANALYSIS_WIN * 2:
        return None
    return samples[: _ANALYSIS_MAX_SECONDS * _ANALYSIS_RATE]


def _analyse_samples(samples):
    """FFT one track into (normalised band energies, band-limited waveform).

    CPU-bound, so callers run it off the event loop.
    """
    import numpy as np

    window = np.hanning(_ANALYSIS_WIN).astype(np.float32)
    freqs = np.fft.rfftfreq(_ANALYSIS_WIN, 1.0 / _ANALYSIS_RATE)
    edges = np.geomspace(40.0, _ANALYSIS_RATE / 2.0, VIS_BANDS + 1)
    band_of = np.clip(np.searchsorted(edges, freqs) - 1, 0, VIS_BANDS - 1)
    band_matrix = np.zeros((freqs.size, VIS_BANDS), dtype=np.float32)
    for band in range(VIS_BANDS):
        mask = band_of == band
        count = int(mask.sum())
        if count:
            band_matrix[mask, band] = 1.0 / count

    frames = max(1, (samples.size - _ANALYSIS_WIN) // _ANALYSIS_HOP + 1)
    audio = samples.astype(np.float32)
    bands = np.empty((frames, VIS_BANDS), dtype=np.float32)
    offsets = np.arange(_ANALYSIS_WIN)
    for start in range(0, frames, 2048):          # chunked to bound memory
        stop = min(frames, start + 2048)
        index = (np.arange(start, stop) * _ANALYSIS_HOP)[:, None] + offsets
        spectrum = np.abs(np.fft.rfft(audio[index] * window, axis=1))
        bands[start:stop] = spectrum @ band_matrix
    # dB, then normalise against the loud end of this track so quiet tracks still
    # fill the bars instead of showing a flat line.
    db = 20.0 * np.log10(bands + 1e-6)
    ref = float(np.percentile(db, 99))
    floor = ref - 45.0
    bands = np.clip((db - floor) / max(1e-6, ref - floor), 0.0, 1.0).astype(np.float32)

    # Band-limited copy for the oscilloscope: windowed-sinc low-pass, then
    # decimate. Rolled off at 300 Hz so the wave follows bass/melody rather than
    # dissolving into a comb of cymbals and vocals.
    taps = 101
    n = np.arange(taps) - (taps - 1) / 2
    lowpass = np.sinc(2.0 * _ANALYSIS_WAVE_CUTOFF / _ANALYSIS_RATE * n) * np.hamming(taps)
    lowpass /= lowpass.sum()
    wave = np.convolve(audio, lowpass, mode="same")
    wave = wave[:: _ANALYSIS_RATE // _ANALYSIS_WAVE_RATE]
    wave = np.clip(wave, -32768.0, 32767.0).astype(np.int16)
    return _Analysis(bands, wave, _ANALYSIS_RATE, _ANALYSIS_HOP, _ANALYSIS_WAVE_RATE)


async def _analyse_track(track):
    """Return a cached _Analysis for this track, decoding it on first use."""
    key = _track_key(track)
    if not key:
        return None
    cached = _ANALYSIS_CACHE.get(key)
    if cached is not None:
        return cached
    try:
        import numpy  # noqa: F401
    except ImportError:
        log.warning("numpy is not installed; the visualiser needs it")
        return None
    url = _audio_url(track)
    if not url:
        return None
    async with _ANALYSIS_SEM:
        cached = _ANALYSIS_CACHE.get(key)
        if cached is not None:
            return cached
        try:
            samples = await _decode_audio(url)
            if samples is None:
                return None
            analysis = await asyncio.to_thread(_analyse_samples, samples)
        except Exception:
            log.exception("could not analyse %r for the visualiser", key)
            return None
        if analysis is None:
            return None
        while len(_ANALYSIS_CACHE) >= _ANALYSIS_CACHE_MAX:
            _ANALYSIS_CACHE.pop(next(iter(_ANALYSIS_CACHE)))
        _ANALYSIS_CACHE[key] = analysis
        return analysis


async def _analyse_for_handle(guild_id, handle, track):
    """Decode a track in the background and hand its analysis to the handle."""
    try:
        analysis = await _analyse_track(track)
    except asyncio.CancelledError:
        raise
    except Exception:
        log.exception("visualiser analysis failed")
        analysis = None
    if _VISUALIZERS.get(guild_id) is not handle:
        return  # the panel was stopped or replaced while we were decoding
    if analysis is None:
        handle.analysis_failed = True
    else:
        handle.analysis = analysis


def _render_spectrum(analysis, position_ms):
    """The panel: 2 rows of real FFT bands over a 5-row oscilloscope."""
    bands = analysis.bands
    frame = int(position_ms / 1000.0 * analysis.rate / analysis.hop)
    frame = max(0, min(bands.shape[0] - 1, frame))
    values = bands[frame]
    width = values.shape[0]

    rows = [[" "] * width for _ in range(2)]
    for col in range(width):
        level = float(values[col]) * 16.0
        if level >= 8.0:
            rows[1][col] = "█"
        elif level >= 1.0:
            rows[1][col] = _VIS_RAMP[min(7, int(level) - 1)]
        upper = level - 8.0
        if upper >= 8.0:
            rows[0][col] = "█"
        elif upper >= 1.0:
            rows[0][col] = _VIS_RAMP[min(7, int(upper) - 1)]
    lines = ["".join(rows[0]), "".join(rows[1])]

    wave = analysis.wave
    start = int(position_ms / 1000.0 * analysis.wave_rate)
    step = max(1, int(analysis.wave_rate * 0.05 / width))
    columns = []
    for col in range(width):
        index = start + col * step
        if index >= wave.size:
            break
        columns.append(float(wave[index:index + step].mean()) / 32768.0)
    wave_rows = [[" "] * width for _ in range(5)]
    if columns:
        # Per-window auto-gain: quiet passages still show a wave, but true
        # silence stays a flat line instead of an amplified noise floor.
        peak = max(abs(value) for value in columns)
        scale = peak if peak > 1e-3 else 1.0
        previous = None
        for col, value in enumerate(columns):
            row = max(0, min(4, int((1.0 - value / scale) * 0.5 * 4.999)))
            wave_rows[row][col] = "~"
            if previous is not None:
                for between in range(min(previous, row) + 1, max(previous, row)):
                    wave_rows[between][col] = "~"
            previous = row
    lines += ["".join(row) for row in wave_rows]
    return lines


def _placeholder_lines(text, spin=""):
    """A fixed-height stand-in while a track is being analysed (or cannot be)."""
    lines = [" " * VIS_BANDS for _ in range(VIS_PANEL_ROWS)]
    label = f"{spin} {text}".strip()
    lines[VIS_PANEL_ROWS // 2] = label[:VIS_BANDS]
    return lines


_VIS_TITLE = "🎵 Music Visualiser"
_SILENCE = "_Silence — nothing is playing._"


def _start_analysis(guild_id, handle, track):
    """Point the handle at a new track and decode it in the background."""
    handle.track_key = _track_key(track)
    handle.analysis = None
    handle.analysis_failed = False
    if handle.analysis_task is not None:
        handle.analysis_task.cancel()
    handle.analysis_task = bot.loop.create_task(
        _analyse_for_handle(guild_id, handle, track)
    )


def _visualizer_body(handle, player):
    track = player.current
    state = player.state
    total = int((getattr(track, "length", 0) or 0) / 1000)
    position = handle.last_position
    current = max(0, min(total, int(position / 1000)))
    width = 16
    filled = 0 if not total else int(width * current / total)
    progress = "=" * filled
    if filled < width:
        progress += ">" + "-" * (width - filled - 1)
    loop = "track" if state.loop_track else ("queue" if state.loop_queue else "off")

    if handle.analysis is not None:
        scene = "\n".join(_render_spectrum(handle.analysis, position))
    elif handle.analysis_failed:
        scene = "\n".join(_placeholder_lines("no readable audio for this track"))
    else:
        scene = "\n".join(
            _placeholder_lines("analysing the audio", _SPINNER[handle.spinner % 4])
        )

    return (
        f"**{track.title}**\n{getattr(track, 'author', '') or ''}\n\n"
        f"```\n{scene}\n```\n"
        f"`[{progress}]` `{_vis_time(current)} / {_vis_time(total)}`\n"
        f"loop `{loop}` • queue `{state.queue.count}` • vol `{state.volume}%`"
        + (" • ⏸ paused" if player.paused else "")
    )


def _visualizer_embed(body):
    return nextcord.Embed(title=_VIS_TITLE, description=body, color=embed_color)


def _visualizer_tick(handle, player):
    """Return the frame body to render, or None to leave the panel untouched.

    While playing the frame follows the live position; while paused the position
    is left frozen and, after one 'paused' frame, the panel is not touched at all
    until playback resumes.
    """
    if player.paused:
        if handle.paused:
            return None
        handle.paused = True
    else:
        handle.paused = False
        handle.last_position = float(player.position or 0)
    return _visualizer_body(handle, player)


async def _edit_visualizer(guild_id, embed):
    """Edit the live panel, returning False the moment it can no longer be edited."""
    handle = _VISUALIZERS.get(guild_id)
    if handle is None or handle.message is None:
        return False
    try:
        await handle.message.edit(embed=embed)
        return True
    except nextcord.NotFound:
        return False  # a moderator or cleanup deleted the message
    except nextcord.HTTPException as exc:
        if getattr(exc, "status", None) == 429:
            # nextcord normally absorbs 429s by sleeping; if one still surfaces,
            # keep the panel alive rather than tearing it down on a throttle.
            log.debug("visualiser edit throttled (guild %s)", guild_id)
            return True
        log.warning("visualiser update failed (guild %s): %r", guild_id, exc)
        return False
    except nextcord.DiscordException as exc:
        log.warning("visualiser update failed (guild %s): %r", guild_id, exc)
        return False


async def _visualizer_loop(guild_id):
    """Re-render the panel on a fixed cadence until playback stops.

    The wait is measured against a monotonic schedule instead of sleeping a flat
    interval each pass. Sleeping the interval and THEN rendering means the edit
    time adds to every period, so the loop drifts to ~1.5-2s and the on-screen
    timer appears to skip a second; scheduling `next = previous + interval`
    keeps the redraws exactly one second apart.
    """
    try:
        schedule = bot.loop.time()
        while True:
            schedule += VISUALIZER_INTERVAL
            delay = schedule - bot.loop.time()
            if delay > 0:
                await asyncio.sleep(delay)
            elif delay < -VISUALIZER_INTERVAL:
                # Fell a whole interval behind (a blocked/throttled edit): resync
                # instead of firing a burst of catch-up frames at the channel.
                schedule = bot.loop.time()
            handle = _VISUALIZERS.get(guild_id)
            if handle is None:
                return
            guild = bot.get_guild(guild_id)
            player = guild.voice_client if guild is not None else None
            if player is None or player.current is None:
                # Idle: say so once, but KEEP the subscription alive so the next
                # track lights the panel back up without /visualizer again.
                if handle.message is not None and handle.last_text != _SILENCE:
                    if await _edit_visualizer(guild_id, _visualizer_embed(_SILENCE)):
                        handle.last_text = _SILENCE
                    else:
                        handle.message = None
                continue

            if handle.message is None or handle.last_text == _SILENCE:
                # Music is back but the panel is gone or still the idle frame:
                # move it to the bottom by dropping the old message and posting
                # a fresh one, so the live panel is the newest message again.
                channel = bot.get_channel(handle.channel_id)
                if channel is None:
                    return
                if handle.message is not None:
                    try:
                        await handle.message.delete()
                    except nextcord.DiscordException:
                        pass
                    handle.message = None
                _start_analysis(guild_id, handle, player.current)
                handle.last_position = float(player.position or 0)
                handle.paused = bool(player.paused)
                posted = _visualizer_body(handle, player)
                try:
                    handle.message = await channel.send(embed=_visualizer_embed(posted))
                except nextcord.DiscordException as exc:
                    log.warning("could not repost the visualiser: %r", exc)
                    handle.message = None
                    return
                handle.last_text = posted
                continue

            key = _track_key(player.current)
            if key != handle.track_key:
                _start_analysis(guild_id, handle, player.current)
            if handle.analysis is None and not handle.analysis_failed:
                handle.spinner += 1
            body = _visualizer_tick(handle, player)
            if body is None or body == handle.last_text:
                continue  # paused, or nothing changed: leave the frame alone
            handle.last_text = body
            if not await _edit_visualizer(guild_id, _visualizer_embed(body)):
                return
    except asyncio.CancelledError:
        raise
    except Exception:
        log.exception("visualiser loop died (guild %s)", guild_id)
    finally:
        # Identity-guarded: a restart installs the new task's handle before this
        # task's finally runs, and a bare pop would delete the NEW handle.
        handle = _VISUALIZERS.get(guild_id)
        if handle is not None and handle.task is asyncio.current_task():
            _VISUALIZERS.pop(guild_id, None)
        if handle is not None and handle.analysis_task is not None:
            handle.analysis_task.cancel()


async def _stop_visualizer(guild_id, *, final_embed=None, delete=False):
    """Cancel a running visualiser and tidy its panel.

    delete=True removes the panel entirely -- used when the bot leaves the voice
    channel, where a leftover panel is just a stale message. Otherwise a
    final_embed replaces it with a closing frame.
    """
    handle = _VISUALIZERS.pop(guild_id, None)
    if handle is None:
        return False
    if handle.task is not None:
        handle.task.cancel()
    if handle.analysis_task is not None:
        handle.analysis_task.cancel()
    if handle.message is not None:
        try:
            if delete:
                await handle.message.delete()
            elif final_embed is not None:
                await handle.message.edit(embed=final_embed)
        except nextcord.DiscordException:
            pass
    return True


@rate_limit(1, 2)
@bot.slash_command(
    name="visualizer",
    description="real-audio music visualiser (spectrum + waveform)",
    dm_permission=False,
)
async def visualizer_command(interaction: interactions.Interaction, mode: str = nextcord.SlashOption(
    name="mode", description="start or stop the visualiser", required=True, choices=["start", "stop"]
)):
    if await user_connectivity(interaction) == False:
        return
    guild_id = interaction.guild.id

    if mode == "stop":
        stopped = await _stop_visualizer(guild_id, final_embed=nextcord.Embed(
            title="🎵 Music Visualiser", description="_Stopped._", color=embed_color))
        return await interaction.response.send_message(
            embed=nextcord.Embed(
                description="Visualiser `stopped`." if stopped else "No visualiser is running.",
                color=embed_color,
            ),
            ephemeral=True,
        )

    player = interaction.guild.voice_client
    if player.current is None:
        return await interaction.response.send_message(
            embed=nextcord.Embed(
                description="Nothing is playing to visualise.", color=embed_color
            ),
            ephemeral=True,
        )

    # Re-running start replaces the panel cleanly, so there is never more than
    # one live panel (and one render task) per guild.
    await _stop_visualizer(guild_id, delete=True)
    await interaction.response.defer()
    track = player.current
    handle = _Visualizer()
    handle.track_key = _track_key(track)
    handle.last_position = max(0.0, float(player.position or 0))
    handle.paused = bool(player.paused)
    body = _visualizer_body(handle, player)
    try:
        message = await interaction.channel.send(embed=_visualizer_embed(body))
    except nextcord.DiscordException as exc:
        log.warning("could not post the visualiser: %r", exc)
        return await interaction.followup.send(
            "I can't post here — I need **Send Messages** and **Embed Links** "
            "in this channel.",
            ephemeral=True,
        )
    handle.message = message
    handle.channel_id = message.channel.id
    handle.last_text = body
    _VISUALIZERS[guild_id] = handle
    handle.task = bot.loop.create_task(_visualizer_loop(guild_id))
    handle.analysis_task = bot.loop.create_task(
        _analyse_for_handle(guild_id, handle, track)
    )
    return await interaction.followup.send(
        f"Visualiser `started` — decoding the audio for a real waveform; it "
        f"redraws every {VISUALIZER_INTERVAL:g}s (Discord caps message edits at "
        f"about one per second). `/visualizer stop` to end it.",
        ephemeral=True,
    )


@rate_limit(1, 2)
@require_role("tm")
@bot.slash_command(name="skip", description="skips to the next track", dm_permission=False)
async def skip_command(interaction: interactions.Interaction):
    if await user_connectivity(interaction) == False:
        return
    vc: Player = interaction.guild.voice_client
    state = vc.state

    if state.loop_track:
        vclooptxt = "Disable the `LOOP` mode to skip\n**/loop** again to disable the `LOOP` mode\nAdding songs disables the `LOOP` mode"
        return await interaction.response.send_message(
            embed=nextcord.Embed(description=vclooptxt, color=embed_color), delete_after=5
        )

    current = vc.current
    # mark-then-stop-then-advance: the reason gate in on_track_end now ignores
    # `stopped`, so /skip drives its own advance instead of relying on the end
    # event to do it (the previous code called _wakeup_next on the backend's
    # private deque to achieve the same thing).
    state.abandoned_id = _track_key(current) if current is not None else None
    await vc.stop()
    if current is not None:
        state.abandoned_id = None
    advanced = await _advance_queue(vc, finished=current)
    if advanced is False:
        return await interaction.response.send_message(
            embed=nextcord.Embed(
                description="Song stopped! No songs in the `QUEUE`",
                color=embed_color,
            ),
            delete_after=5,
        )
    # One response, showing the new state. The old version sent "SKIPPED!" with
    # delete_after=5 and then called /queue, whose listing was permanent -- the
    # acknowledgment vanished while the listing stayed.
    return await interaction.response.send_message(
        embed=_queue_rows_embed(interaction, state, title_extra="  _(`SKIPPED`)_"),
        delete_after=10,
    )


@rate_limit(1, 2)
@require_role("tm")
@bot.slash_command(
    name="disconnect",
    description="disconnects the player from the vc",
    dm_permission=False,
)
async def disconnect_command(interaction: interactions.Interaction):
    if await user_connectivity(interaction) == False:
        return
    vc: Player = interaction.guild.voice_client
    state = vc.state
    # The success reply used to live inside the same bare `except Exception` as
    # the teardown, so a failure after the message was sent was reported as
    # "Failed to destroy!" and the real reason was never logged.
    try:
        await vc.stop()
    except mafic.MaficException as exc:
        log.warning("stop during /disconnect failed: %r", exc)
    state.queue.clear()
    state.loop_queue = False
    state.abandoned_id = None
    await _stop_visualizer(interaction.guild.id, delete=True)
    try:
        await vc.disconnect(force=True)
    except Exception as exc:
        log.exception("could not disconnect cleanly")
        return await interaction.response.send_message(
            embed=nextcord.Embed(description="Failed to destroy!", color=embed_color),
            delete_after=5,
        )
    return await interaction.response.send_message(
        embed=nextcord.Embed(
            description="**BYE!** Have a great time!", color=embed_color
        )
    )


# Auto-disconnect if all participants leave the voice channel
@bot.event
async def on_voice_state_update(member, before, after):
    # Two triggers, kept in this order and NOT hoisted above the None check: the
    # channel a member just left is None on every voice JOIN event, so reading
    # anything off it unguarded would raise on the commonest case.
    channel = before.channel
    if channel is not None:
        # Count voice states, not members. VoiceChannel.members is a cache view
        # and silently drops present-but-uncached ids in large guilds whose
        # member chunk never arrived, which could evict the bot from a channel
        # full of listeners. The bot counts itself, so 1 means "only the bot".
        alone = len(channel.voice_states) == 1 and bot.user.id in channel.voice_states
    else:
        alone = False
    kicked = member.id == bot.user.id and after.channel is None
    if not (alone or kicked):
        return
    for vc in bot.voice_clients:
        # mafic.Player exposes no `.channel`; the id lives on GuildState.
        if getattr(getattr(vc, "state", None), "channel_id", None) == getattr(channel, "id", None):
            try:
                await vc.stop()
            except mafic.MaficException as exc:
                log.debug("nothing to stop during auto-disconnect: %r", exc)
            vc.state.queue.clear()
            vc.state.loop_queue = False
            vc.state.loop_track = False
            vc.state.abandoned_id = None
            await _stop_visualizer(vc.guild.id, delete=True)
            await vc.disconnect(force=True)
            log.info("auto-disconnected from channel %s (nobody left / kicked)",
                     getattr(channel, "id", "?"))
            break

@rate_limit(1, 2)
@bot.slash_command(
    name="nowplaying",
    description="shows the current track information",
    dm_permission=False,
)
async def nowplaying_command(interaction: interactions.Interaction):
    # Read-only: a member in another channel, or in none, may still look.
    if await user_connectivity(interaction, same_channel=False) == False:
        return
    vc: Player = interaction.guild.voice_client
    if vc.current is None:
        return await interaction.response.send_message(
            embed=nextcord.Embed(description="Not playing anything!", color=embed_color)
        )

    loopstr = "enabled" if vc.state.loop_track else "disabled"
    pstate = "paused" if vc.paused else "playing"
    current = vc.current

    # This used to rebuild a numpy array from a process-global dict on every
    # call and prefix-search it with np.char.find, which (a) raised
    # UFuncTypeError whenever the dict was empty, (b) could match the *value*
    # column or a shorter key and credit the wrong member, and (c) let two
    # guilds overwrite each other's entries. It is now a per-guild dict lookup
    # keyed by the track id.
    requester = vc.state.requester_for(current)
    if requester is None:
        # The old code sent "Song not found" as a permanent public message while
        # a song was plainly playing; the track IS found, the requester record
        # just predates this session (or was lost to a node reconnect).
        requester = "_unknown_"

    nowplaying_description = (
        f"[`{current.title}`]({str(current.uri)})\n\n**Requested by**: {requester}"
    )
    em = nextcord.Embed(
        description=f"**Now Playing**\n\n{nowplaying_description}", color=embed_color
    )
    em.add_field(
        name="**Song Info**",
        # Track.length is milliseconds under mafic.
        value=f"• Author: `{current.author}`\n"
              f"• Duration: `{datetime.timedelta(seconds=int((current.length or 0) / 1000))}`",
    )
    em.add_field(
        name="**Player Info**",
        # mafic.Player exposes no volume getter, so the last value we successfully
        # set is tracked on GuildState by /volume.
        value=f"• Player Volume: `{vc.state.volume}`\n• Loop: `{loopstr}`\n"
              f"• Current State: `{pstate}`",
        inline=False,
    )

    return await interaction.response.send_message(embed=em, delete_after=10)


@rate_limit(1, 2)
@require_role("tm")
@bot.slash_command(
    name="loop",
    description="loop / exitloop",
    dm_permission=False,
)
async def loop_command(interaction: interactions.Interaction):
    if await user_connectivity(interaction) == False:
        return
    vc: Player = interaction.guild.voice_client
    if not vc.current:
        return await interaction.response.send_message(
            embed=nextcord.Embed(description="No song to `loop`", color=embed_color),delete_after=5
        )
    # Explicit toggle. The old `vc.loop ^= True` inside a bare try/except
    # swallowed the AttributeError from a player whose flag had never been set
    # and then reported "`disabled`" -- i.e. the command silently did nothing and
    # said the opposite of what it meant. The class default makes the recovery
    # branch unnecessary, so it is gone rather than kept as a hidden failure.
    vc.state.loop_track = not vc.state.loop_track
    return await interaction.response.send_message(
        embed=nextcord.Embed(
            description="**LOOP**: `enabled`" if vc.state.loop_track else "**LOOP**: `disabled`",
            color=embed_color,
        ),
        delete_after=5,
    )

@rate_limit(1, 2)
@bot.slash_command(
    name="queue",
    description="displays the current queue",
    dm_permission=False,
)
async def queue_command(interaction: interactions.Interaction):
    if await user_connectivity(interaction, same_channel=False) == False:
        return
    vc: Player = interaction.guild.voice_client

    if vc.state.queue.is_empty:
        return await interaction.send(
            embed=nextcord.Embed(description="**QUEUE**\n\n`empty`", color=embed_color)
        )
    
    lqstr = "`disabled`" if not vc.state.loop_queue else "`enabled`"
    # One bounded embed built by the same helper /skip uses, so the numbers shown
    # here are literally the numbers /del, /move and /skipto accept. The old
    # version built one embed for the whole queue with numpy and broke past
    # ~85 songs, inside the advertised cap of 100.
    return await interaction.send(
        embed=_queue_rows_embed(interaction, vc.state, title_extra=f"\n\n**loopqueue**: {lqstr}")
    )

@rate_limit(1, 2)
@require_role("tm")
@bot.slash_command(
    name="shuffle",
    description="shuffles the existing queue randomly",
    dm_permission=False,
)
async def shuffle_command(interaction: interactions.Interaction):
    if await user_connectivity(interaction) == False:
        return
    vc: Player = interaction.guild.voice_client
    if vc.state.queue.count > 1:
        vc.state.queue.shuffle()
        return await interaction.response.send_message(
            embed=nextcord.Embed(description="Shuffled the `QUEUE`", color=embed_color),delete_after=5
        )
    elif vc.state.queue.is_empty:
        return await interaction.response.send_message(
            embed=nextcord.Embed(description="`QUEUE` is empty", color=embed_color),delete_after=5
        )
    else:
        return await interaction.response.send_message(
            embed=nextcord.Embed(
                description="`QUEUE` has less than `3 songs`",
                color=embed_color,
            ),delete_after=5
        )


@rate_limit(1, 2)
@require_role("tm")
@bot.slash_command(
    name="del",
    description="deletes the specified track",
    dm_permission=False,
)
async def del_command(interaction: interactions.Interaction, position: int):
    if await user_connectivity(interaction) == False:
        return
    vc: Player = interaction.guild.voice_client
    if vc.state.queue.is_empty:
        return await interaction.response.send_message(
            embed=nextcord.Embed(
                description="No songs in the `QUEUE`", color=embed_color
            ),delete_after=5
        )
    try:
        removed = vc.state.queue.remove_at(position)
    except IndexError:
        # remove_at validates zero, negatives, python-style negative indexing and
        # overshoot in one place, so the three separate hand-written guards (and
        # the off-by-one they disagreed about) are gone.
        return await interaction.response.send_message(
            embed=nextcord.Embed(
                description=f"Position `{position}` is outta range "
                            f"(1-{vc.state.queue.count})",
                color=embed_color,
            ),delete_after=5
        )
    vc.state.requesters.pop(_track_key(removed), None)
    return await interaction.response.send_message(
        embed=nextcord.Embed(
            description=f"`{removed.title}` removed from the QUEUE",
            color=embed_color,
        ),delete_after=5
    )


@rate_limit(1, 2)
@require_role("tm")
@bot.slash_command(
    name="skipto",
    description="skips to the specified track",
    dm_permission=False,
)
async def skipto_command(interaction: interactions.Interaction, position: int):
    if await user_connectivity(interaction) == False:
        return
    vc: Player = interaction.guild.voice_client
    if vc.state.queue.is_empty:
        return await interaction.response.send_message(
            embed=nextcord.Embed(
                description="No songs in the `QUEUE`", color=embed_color
            ),delete_after=5
        )
    if position <= 0:
        return await interaction.response.send_message(
            embed=nextcord.Embed(
                description="Position can not be `ZERO`* or `LESSER`",
                color=embed_color,
            ),delete_after=5
        )
    elif position > vc.state.queue.count:
        return await interaction.response.send_message(
            embed=nextcord.Embed(
                description=f"Position `{position}` is outta range", color=embed_color
            ),delete_after=5
        )
    else:
        # The old `position == _queue[position-1]` branch compared an int to a
        # Track and was therefore dead code; and the reorder-then-delegate-to-
        # /skip left the mutation applied whenever /skip refused (loop mode),
        # permuting the queue once per attempt. Validate FIRST, move to front,
        # then drop the duplicate that the move created.
        target = vc.state.queue.get_at(position)
        if position == 1 and vc.state.queue.count == 1:
            return await interaction.response.send_message(
                embed=nextcord.Embed(
                    description="That is the only song in the `QUEUE`.",
                    color=embed_color,
                ),delete_after=5
            )
        current = vc.current
        vc.state.queue.move(position, 1)
        state = vc.state
        state.abandoned_id = _track_key(current) if current is not None else None
        await vc.stop()
        state.abandoned_id = None
        advanced = await _advance_queue(vc, finished=current)
        if advanced is False:
            return await interaction.response.send_message(
                embed=nextcord.Embed(
                    description=f"Could not start `{target.title}`.", color=embed_color
                ),delete_after=5
            )
        return await interaction.response.send_message(
            embed=nextcord.Embed(
                description=f"Now playing `{target.title}` (position `{position}`)",
                color=embed_color,
            ),delete_after=10
        )


@rate_limit(1, 2)
@require_role("tm")
@bot.slash_command(
    name="move",
    description="moves the track to the specified position",
    dm_permission=False,
)
async def move_command(
    interaction: interactions.Interaction, song_position: int, move_position: int
):
    if await user_connectivity(interaction) == False:
        return
    vc: Player = interaction.guild.voice_client
    if vc.state.queue.is_empty:
        return await interaction.response.send_message(
            embed=nextcord.Embed(
                description="No songs in the `QUEUE`!", color=embed_color
            ),delete_after=5
        )
    if song_position <= 0 or move_position <= 0:
        return await interaction.response.send_message(
            embed=nextcord.Embed(
                description="Position can not be `ZERO`* or `LESSER`",
                color=embed_color,
            ),delete_after=5
        )

    queue_length = vc.state.queue.count
    if song_position > queue_length or move_position > queue_length:
        # Report whichever one actually overshot; the old expression named the
        # larger of the two only when the SOURCE overshot, so a bad destination
        # printed the good number.
        position = (song_position if song_position > queue_length
                    else move_position)
        return await interaction.response.send_message(
            embed=nextcord.Embed(
                description=f"Position `{position}` is outta range!", color=embed_color
            ),delete_after=5
        )
    elif song_position == move_position:
        return await interaction.response.send_message(
            embed=nextcord.Embed(
                description=f"Already in that `Position`:{move_position}",
                color=embed_color,
            ),delete_after=5
        )
    else:
        # TrackQueue.move() pops then inserts on a copy and rebinds, so it cannot
        # do what `deque.remove(song)` did here: remove() deletes the FIRST equal
        # element, and because Track has no __eq__ equality is identity -- under
        # /loopqueue, which re-queued the same object, it removed a different
        # instance than the one read on the line above.
        move_song = vc.state.queue.get_at(song_position)
        vc.state.queue.move(song_position, move_position)

        moved_song_name = move_song.title
        return await interaction.response.send_message(
            embed=nextcord.Embed(
                description=f"**{moved_song_name}** moved at Position:`{move_position}`",
                color=embed_color,
            ),delete_after=5
        )

@rate_limit(1, 2)
@require_role("tm")
@bot.slash_command(name="volume", description="sets the volume", dm_permission=False)
async def volume_command(interaction: interactions.Interaction, playervolume: int):
    if await user_connectivity(interaction) == False:
        return
    vc: Player = interaction.guild.voice_client
    if vc.is_connected():
        if playervolume > 100:
            return await interaction.response.send_message(
                embed=nextcord.Embed(
                    description="**VOLUME** supported upto `100%`", color=embed_color
                ),delete_after=5
            )
        elif playervolume < 0:
            return await interaction.response.send_message(
                embed=nextcord.Embed(
                    description="**VOLUME** can not be `negative`", color=embed_color
                ),delete_after=5
            )
        else:
            # Set first, then confirm. The reply used to be sent BEFORE the
            # attempt, so it predicted success and a later failure stacked a
            # second, non-self-clearing message on top of a wrong one.
            try:
                await vc.set_volume(playervolume)
            except mafic.MaficException as exc:
                log.warning("set_volume failed: %r", exc)
                return await interaction.response.send_message(
                    embed=nextcord.Embed(
                        description="Could not change the volume.", color=embed_color
                    ),delete_after=5, ephemeral=True,
                )
            vc.state.volume = playervolume
            return await interaction.response.send_message(
                embed=nextcord.Embed(
                    description=f"**VOLUME**\nSet to `{playervolume}%`",
                    color=embed_color,
                ),delete_after=5
            )
    return await interaction.response.send_message(
        embed=nextcord.Embed(description="Player not connected!", color=embed_color),delete_after=5
    )



@rate_limit(1, 2)
@require_role("tm")
@bot.slash_command(name="restart", description="restarts the song", dm_permission=False)
async def restart_command(interaction: interactions.Interaction):
    if await user_connectivity(interaction) == False:
        return
    vc: Player = interaction.guild.voice_client
    if vc.current is None:
        return await interaction.response.send_message(
            embed=nextcord.Embed(description="Player not playing!", color=embed_color),delete_after=5
        )
    elif vc.current is not None:
        msg = await interaction.response.send_message(embed=nextcord.Embed(description="Restarting...", color=embed_color))
        await vc.seek(0)
        return await msg.edit(embed=nextcord.Embed(description="Player restarted!", color=embed_color),delete_after=5)
        


@rate_limit(1, 5)
@require_role("tm")
@bot.slash_command(
    name="clear", description="clears the queue",
    dm_permission=False,
)
async def clear_command(interaction: interactions.Interaction):
    if await user_connectivity(interaction) == False:
        return
    vc: Player = interaction.guild.voice_client
    if vc.state.queue.is_empty:
        return await interaction.response.send_message(
            embed=nextcord.Embed(
                description="No `SONGS` are present", color=embed_color
            ),delete_after=5
        )
    vc.state.queue.clear()
    vc.state.loop_queue = False
    # Without this a marker left by an exception survives the clear and the next
    # real end event is swallowed, which stalls the queue one song later.
    vc.state.abandoned_id = None
    clear_command_embed = nextcord.Embed(
        description="`QUEUE` cleared", color=embed_color
    )
    return await interaction.response.send_message(embed=clear_command_embed, delete_after=5)


@rate_limit(1, 2)
@bot.slash_command(
    name="save",
    description="dms the current or specified song to the user",
    dm_permission=False,
)
async def save_command(interaction: interactions.Interaction):
    # Read-only listing of what is playing; controlling the player is the other
    # gate's job.
    if await user_connectivity(interaction, same_channel=False) == False:
        return
    vc: Player = interaction.guild.voice_client
    if not vc.current:
        return await interaction.send(
            embed=nextcord.Embed(
                description="There is no `song` | `queue` available", color=embed_color
            ),
            delete_after=5,
        )
    # vc._source is nextwave's private audio-source object; formatting it sent the
    # user a Python repr. vc.current is what /nowplaying already uses for title+uri.
    saved = nextcord.Embed(
        description=f"[`{vc.current.title}`]({str(vc.current.uri)})\n\n**Saved from** {interaction.guild.name}",
        color=embed_color,
    )
    # DM first, then answer once with what actually happened. The original DM'd
    # before responding at all, so a member with DMs closed raised out of the
    # handler and left the interaction unanswered.
    try:
        await interaction.user.send(embed=saved)
    except nextcord.Forbidden:
        # Ephemeral: this names a member's privacy setting, and it used to be a
        # permanent message visible to the whole channel.
        return await interaction.send(
            embed=nextcord.Embed(
                description="I could not DM you — allow direct messages from "
                            "server members and try again.",
                color=embed_color,
            ),
            ephemeral=True,
        )
    await interaction.send(
        embed=nextcord.Embed(description="**SONG** saved!", color=embed_color),
        delete_after=5,
    )

@rate_limit(1, 2)
@require_role("tm")
@bot.slash_command(name="seek", description="seeks to the specified position for eg. 30sec", dm_permission=False)
async def seek_command(interaction:interactions.Interaction, seekpos: int):
    if await user_connectivity(interaction) == False:
        return 
    vc: Player = interaction.guild.voice_client
    if vc.current is None:
        return await interaction.response.send_message(
            embed=nextcord.Embed(description="Player not playing!", color=embed_color),delete_after=5
        )    
    
    else:
        # Compare like with like: seek takes milliseconds (mafic
        # Player.seek(position)), and mafic's Track.length is milliseconds
        # straight from Lavalink (track.py:150 assigns info["length"] with no
        # division). That is the OPPOSITE of the previous backend, whose model
        # divided length by 1000 -- which is why this guard was flipped during
        # the migration and why the same arithmetic is correct under one client
        # and broken under the other. Verified against a live node: a 214000 ms
        # track reports length 214000 here.
        if seekpos < 0 or seekpos * 1000 > vc.current.length:
            return await interaction.response.send_message(
                embed=nextcord.Embed(
                    description=f"SEEK length `{seekpos}` outta range",
                    color=embed_color
                ),delete_after=5
            )
        else:
            await vc.seek(seekpos*1000)
            return await interaction.response.send_message(
                embed=nextcord.Embed(
                    description=f"Player seeked to `{seekpos}` sec.",
                    color=embed_color
                ),delete_after=5
            )

# The /predict AI path. g4f's Client is a scraper over third-party free
# providers: with no api_key and no base_url, model="gpt-4" resolves through
# AnyProvider, which rotates free-first and shuffled across cookie-scraping and
# auth-gated hosts. So the model that answers is whoever happens to be free, the
# call can raise MissingAuthError / RetryNoProviderError, and whatever text is in
# the prompt -- here, song titles from a guild's queue -- is sent to that
# third party. That is a product decision, not a bug to fix silently; see
# PREDICT_TIMEOUT below for what this code does control.
from g4f.client import Client  # noqa: E402
import nest_asyncio  # noqa: E402

# nest_asyncio is what historically let the *synchronous* g4f call complete from
# inside nextcord's already-running loop: g4f drives its async providers with
# run_until_complete on the loop that is already running, which is exactly the
# situation nest_asyncio patches for. The completion is now offloaded to a
# worker thread (see _ask_g4f), which makes this patch dead weight for this
# file's purposes -- but g4f 7.2.2 hard-depends on nest_asyncio2 and applies it
# itself, and removing the legacy apply here changes which shim wins the race, so
# it gets its own commit rather than being folded into a bug fix.
nest_asyncio.apply()

client = Client()
PREDICT_TIMEOUT = 25.0
PREDICT_SEED_SONGS = 10


def _queue_titles(vc):
    """Up to PREDICT_SEED_SONGS titles: the current track, then the queue.

    The old seed was f"{vc.state.queue} {vc.current.title}", which interpolated the queue
    OBJECT -- a memory address or a raw deque dump, never song titles -- so the
    model was prompted with junk and handed back junk. Bounded and read-only on
    purpose: an unbounded seed dumps a 100-song queue into every prompt.
    """
    # TrackQueue already knows how to name its entries; hand it the job instead
    # of re-deriving "title or info['title']" here.
    current = getattr(vc, "current", None)
    titles = [current.title] if current is not None and getattr(current, "title", None) else []
    titles.extend(vc.state.queue.titles())
    return titles[: PREDICT_SEED_SONGS + 1]


async def _ask_g4f(prompt):
    """Run the g4f completion off the event loop, bounded.

    `client.chat.completions.create` is a plain synchronous method in g4f 7.2.2
    (g4f/client/__init__.py:277), so the missing await was never the bug -- the
    bug was calling it at all from inside the gateway loop. With nest_asyncio
    applied, that nested run pumped every other pending callback reentrantly: a
    /predict could starve heartbeats and other guilds' work for the whole
    provider round trip while the bot still looked alive. A thread plus
    wait_for makes the cost local to this command and cancellable.
    """
    completion = await asyncio.wait_for(
        asyncio.to_thread(
            client.chat.completions.create,
            model="gpt-4",
            messages=[{"role": "user", "content": prompt}],
        ),
        timeout=PREDICT_TIMEOUT,
    )
    return completion.choices[0].message.content


@rate_limit(1, 5)
@require_role("tm")
@bot.slash_command(name="predict", description="Predict and add songs to the queue", dm_permission=False)
async def predict_command(interaction: nextcord.Interaction, num_songs: int):
    if num_songs < 3 or num_songs > 10:
        return await interaction.response.send_message(
            embed=nextcord.Embed(
                description="Please enter a number between 3 to 10 for predictions.",
                color=embed_color
            ),
            ephemeral=True,
        )

    # Member gate BEFORE paying for a provider round trip. This command used to
    # look only at interaction.guild.voice_client, so a member who was not in
    # voice still triggered the scrape, then watched each internally invoked play
    # refuse, and was told the prediction succeeded regardless.
    if await user_connectivity(interaction) is False:
        return
    vc: Player = interaction.guild.voice_client

    # is_connected() here is Discord voice, not the Lavalink node -- the node has
    # its own check inside _play_one. Kept separate from user_connectivity, which
    # only tests for None: a voice connect that fails without timing out leaves a
    # registered-but-not-connected player behind.
    if not vc.is_connected():
        return await interaction.send(
            embed=nextcord.Embed(
                description="The bot is not connected to a voice channel.",
                color=embed_color
            ),
            ephemeral=True,
        )

    if vc.state.queue.is_empty and vc.current is None:
        return await interaction.send(
            embed=nextcord.Embed(
                description="There's nothing currently playing or in the queue to base "
                            "predictions on.",
                color=embed_color
            ),
            ephemeral=True,
        )

    await interaction.response.defer()

    seed_titles = _queue_titles(vc)
    if not seed_titles:
        return await interaction.send(
            embed=nextcord.Embed(
                description="There is nothing playing to base a prediction on.",
                color=embed_color
            ),
            ephemeral=True,
        )

    prompt = (
        f"Predict the next {num_songs} songs for a listener, continuing from this "
        f"list of song titles: {', '.join(seed_titles)}. "
        "Reply with song titles only, no commentary and no numbering, separated "
        "by ==== ."
    )
    try:
        response = await _ask_g4f(prompt)
    except asyncio.TimeoutError:
        log.error("prediction timed out after %ss", PREDICT_TIMEOUT)
        return await interaction.send(
            embed=nextcord.Embed(
                description=f"The prediction request timed out after "
                            f"{int(PREDICT_TIMEOUT)}s.",
                color=embed_color
            ),
            ephemeral=True,
        )
    except Exception as exc:
        # The provider is a third-party free scraper; failing is normal. Without
        # this the error escaped into the generic handler and the member saw
        # "something went wrong" with no indication it was the AI, not the bot.
        log.exception("prediction request failed")
        return await interaction.send(
            embed=nextcord.Embed(
                description=f"The prediction service did not answer ({type(exc).__name__}).",
                color=embed_color
            ),
            ephemeral=True,
        )

    # Normalize before trusting it. The old code fed every split() fragment
    # straight into a search, so model preamble, list numbering, markdown and the
    # trailing empty segment all became YouTube searches.
    titles = [t.strip() for t in (response or "").split("====") if t.strip()]
    titles = titles[:num_songs]
    if len(titles) < 2:
        return await interaction.send(
            embed=nextcord.Embed(
                description="The prediction did not come back in a usable form.",
                color=embed_color
            ),
            ephemeral=True,
        )

    added = 0
    for title in titles:
        try:
            if await _play_one(interaction, vc, title, announce=False) is not None:
                added += 1
        except Exception:
            log.exception("predicted title %r could not be queued", title)

    # Counted, not asserted. The old message reported num_songs -- what was
    # requested -- so a total failure still announced "Added 5 predicted songs".
    await interaction.followup.send(
        embed=nextcord.Embed(
            description=f"Added `{added}` of `{len(titles)}` predicted songs to the queue.",
            color=embed_color
        ),
        delete_after=10
    )



# ---------------------------------------------------------------------------
# Shutdown
# ---------------------------------------------------------------------------
# Procfile runs `python3 app.py` and the platform stops it with SIGTERM. Nothing
# handled that signal, so a deploy or a restart cut the process off mid-flight: no
# voice disconnects, no Lavalink session teardown, no node removal from the pool.
_signals_installed = False


def _install_signal_handlers():
    global _signals_installed
    if _signals_installed:
        return

    def fire(name):
        log.info("received %s, shutting down", name)
        task = asyncio.ensure_future(_graceful_shutdown())

        def report(finished):
            # Without this a failed teardown is a "Task exception was never
            # retrieved" line that appears only at interpreter exit, if at all --
            # which is exactly how the AttributeError above went unnoticed.
            exc = finished.exception()
            if exc is not None:
                log.error("shutdown raised %r", exc)

        task.add_done_callback(report)
        asyncio.get_running_loop().call_later(10, _hard_exit_if_stuck)

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    for name in ("SIGTERM", "SIGINT"):
        sig = getattr(signal, name, None)
        if sig is None:
            continue
        try:
            loop.add_signal_handler(sig, fire, name)
        except (NotImplementedError, RuntimeError):
            # Windows has no add_signal_handler; RuntimeError means the loop is
            # closing. bot.run still handles Ctrl-C on its own.
            pass
    _signals_installed = True


async def _graceful_shutdown():
    nodes = []
    for vc in list(bot.voice_clients):
        # Reach the node through the players that use it. NodePool.nodes is an
        # INSTANCE property, so `NodePool.nodes` on the class is a bare property
        # object and .values() on it raises AttributeError -- which aborted this
        # handler, left the process alive past SIGTERM, and needed SIGKILL. The
        # alternative, NodePool._nodes, is private state, which is the same
        # mistake the rest of this file is being repaired for.
        node = getattr(vc, "node", None)
        if node is not None and not any(node is seen for seen in nodes):
            nodes.append(node)
        try:
            await vc.disconnect(force=True)
        except Exception as exc:
            log.warning("could not disconnect a player on shutdown: %r", exc)
    for node in nodes:
        # Node.cleanup closes the aiohttp session, cancels the listener and
        # removes this node from the pool (pool.py:324-335).
        try:
            await node.cleanup()
        except Exception as exc:
            log.warning("could not close a node on shutdown: %r", exc)
    for handle in list(_VISUALIZERS.values()):
        if handle.task is not None:
            handle.task.cancel()
    _VISUALIZERS.clear()
    if _node_connect_task is not None:
        _node_connect_task.cancel()
    try:
        await bot.close()
    except asyncio.CancelledError:
        # Cancelling in-flight gateway work is how close() ends when the loop is
        # already tearing down. Expected, not a shutdown failure.
        pass


def _hard_exit_if_stuck():
    """Watchdog: if teardown wedges, exit anyway rather than ignore SIGTERM."""
    log.error("shutdown did not finish within 10s; exiting anyway")
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(1)


"""main"""

if __name__ == "__main__":
    _missing = missing_env()
    if _missing:
        raise SystemExit(
            "missing required environment variable(s): " + ", ".join(_missing)
        )
    bot.run(os.getenv("TOKEN"))
