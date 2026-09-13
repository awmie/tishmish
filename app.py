# T I S H M I S H
import nextcord
from nextcord import interactions
from nextcord.ext import commands, tasks
import nextwave
from nextwave.ext import spotify
import numpy as np
import logging
import math
import os
import asyncio
import datetime
import re
import signal
import sys
import time
import traceback

# The audio backend reports a failed node connection by logging it and returning
# normally (nextwave websocket.py:76-88 swallows the exception), so on a stock
# deploy the one message that says "your audio node is down" went to an
# unconfigured logger at INFO and vanished. Configure logging before anything can
# fail, and surface the library's own logger.
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
    stream=sys.stdout,
)
log = logging.getLogger("tishmish")
logging.getLogger("nextwave").setLevel(logging.INFO)

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
# some useful variables

user_dict = {}
# App-owned flags live as class defaults so no code path can read an unset
# attribute. lq was given one; loop_track is the renamed former `vc.loop`, which
# had none -- on_nextwave_track_end dereferenced it outside its try, so any
# command that raised before its tail left the guild with a playing player whose
# next track end died with AttributeError and the queue never advanced.
# `autoplay` is gone: nothing in the backend or this file ever read it.
setattr(nextwave.Player, "lq", False)
setattr(nextwave.Player, "loop_track", False)
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
            nowplaying_command, queue_command, save_command, spotifyplay_command
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
    # Every caller reads interaction.guild.voice_client right after this returns
    # and then indexes into vc.queue / vc._source, so the member being in voice is
    # only half the precondition: if the bot itself is not connected the player is
    # None and those commands die with AttributeError instead of telling anyone.
    vc = interaction.guild.voice_client
    if vc is None:
        await interaction.send("I am not connected to a voice channel!", ephemeral=True)
        return False
    # Guild.voice_client is keyed by guild id, so one player per guild. Without
    # this check, any member in any *other* voice channel could pause, skip,
    # clear or disconnect a player they cannot hear.
    if same_channel:
        # Player.channel is a VoiceChannel object (nextwave player.py:73,85) that
        # can be None when the guild has evicted it from cache, so compare ids on
        # both sides -- `channel_id != channel_object` is always unequal and
        # would refuse every legitimate call.
        member_channel = interaction.user.voice.channel
        player_channel = getattr(vc, "channel", None)
        player_channel_id = getattr(player_channel, "id", player_channel)
        if player_channel_id is not None and member_channel.id != player_channel_id:
            await interaction.send(
                f"You need to be in {player_channel.mention} to control the player.",
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
async def on_nextwave_node_ready(node: nextwave.Node):
    # This is the ONLY true readiness signal in the system: nextwave's
    # Websocket.connect swallows a failed connection into its logger and returns,
    # so create_node "succeeds" against a dead node and this event is what
    # distinguishes the two. node_connect watches the socket for the same reason.
    log.info("lavalink node %s ready", node.identifier)


REQUIRED_ENV = (
    "TOKEN",
    "LAVALINK_HOST",
    "LAVALINK_PORT",
    "LAVALINK_PASSWORD",
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


def env_flag(name):
    """Parse an env var as a bool.

    https= received the raw string before, so LAVALINK_SECURE="false" was
    truthy and asked for an encrypted node connection from someone who opted out.
    """
    return (os.getenv(name) or "").strip().lower() in {"1", "true", "yes", "on"}


NODE_IDENTIFIER = "tishmish"


async def node_connect():
    """Bring the Lavalink node up, and do not call it connected until it is.

    create_node CANNOT fail. Node._connect -> Websocket.connect
    (websocket.py:76-88) catches any connect exception, logs it and returns
    normally, and create_node then hands back the Node unconditionally. The
    version of this loop before the rewrite treated a returned Node as success, so
    with Lavalink down the backoff exited on its FIRST pass and the bot ran
    forever with no audio -- precisely the zombie the retry existed to prevent. So
    trust the socket, not the return value.
    """
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
    # Built ONCE, outside the loop. SpotifyClient.__init__ opens its own aiohttp
    # ClientSession (ext/spotify/__init__.py:195) and nothing closes it, so
    # constructing it per attempt leaked one session per retry, forever.
    spotify_client = spotify.SpotifyClient(
        client_id=os.getenv('SPOTIFY_CLIENT_ID'),
        client_secret=os.getenv('SPOTIFY_CLIENT_SECRET'),
    )
    delay = 5
    while True:
        node = None
        try:
            # The explicit identifier is load-bearing: it otherwise defaults to
            # os.urandom(8).hex() (pool.py:399-400), so the NodeOccupied guard can
            # never fire and every retry appended a NEW Node to the class-level
            # _nodes dict -- new websocket, new immortal listen() task, and the
            # previous corpse never removed.
            node = await nextwave.NodePool.create_node(
                bot=bot,
                host=host,
                port=port,
                password=os.getenv('LAVALINK_PASSWORD'),
                https=env_flag('LAVALINK_SECURE'),
                identifier=NODE_IDENTIFIER,
                spotify_client=spotify_client,
            )
        except Exception as exc:
            # Kept for the genuinely-raising cases (a bad pool argument, an
            # authorization rejection) now that a dead node no longer arrives here.
            log.error("lavalink create_node raised: %r (retrying in %ss)", exc, delay)
        else:
            if node.is_connected():
                log.info(
                    "lavalink node %s connected at %s:%s", node.identifier, host, port
                )
                return
            log.error(
                "lavalink node %s was created but has no live websocket -- %s:%s is "
                "unreachable; retrying in %ss",
                node.identifier, host, port, delay,
            )
            # Remove this one before retrying. Node.cleanup cancels the listener,
            # closes the session and deletes the identifier from the pool
            # (pool.py:324-335), so the next attempt replaces it instead of piling
            # up beside it.
            try:
                await node.cleanup()
            except Exception as exc:
                log.warning("could not clean up the dead node: %r", exc)
        await asyncio.sleep(delay)
        delay = min(delay * 2, 300)

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
    vc: nextwave.Player = interaction.guild.voice_client
    if vc.queue.is_empty:
        return await interaction.send(
            embed=nextcord.Embed(
                description="Unable to loop `QUEUE`, try adding more songs..",
                color=embed_color,
            )
        )
    # The two original conditions were `lq is False and type == "start"` and
    # `lq is True and type == "stop"`, so asking to start an already-looping
    # queue matched neither and the command ended without ever replying.
    if type == "start":
        if vc.lq:
            return await interaction.send(
                embed=nextcord.Embed(
                    description="**loopqueue** is already `enabled`", color=embed_color
                )
            )
        vc.lq = True
        await interaction.send(
            embed=nextcord.Embed(
                description="**loopqueue**: `enabled`", color=embed_color
            )
        )
        try:
            if vc._source not in vc.queue:
                vc.queue.put(vc._source)
        except Exception as exc:
            log.warning("loopqueue: could not requeue the current source: %r", exc)
        return
    if not vc.lq:
        return await interaction.send(
            embed=nextcord.Embed(
                description="**loopqueue** is already `disabled`", color=embed_color
            )
        )
    vc.lq = False
    await interaction.send(
        embed=nextcord.Embed(
            description="**loopqueue**: `disabled`", color=embed_color
        )
    )
    if vc.queue.count == 1 and vc.queue._queue[0] == vc._source:
        del vc.queue._queue[0]

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


NO_NODE_MESSAGE = "No audio node is connected yet — try again in a moment."


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
    # YouTube URLs are passed through verbatim. The old normalization was
    #   split("&")[0] -> split("?")[0] -> strip host -> rebuild
    # which deleted the query string that HOLDS the video id before the host
    # strip ran, so every https://www.youtube.com/watch?v=<id> reached Lavalink
    # as the literal text "https://www.youtube.com/watch?v=watch". It also
    # destroyed &list=<playlist>, the one thing nextwave's playlist branch tests
    # for (tracks.py:186-191), so playlist links could never load. nextwave
    # inspects the URL itself: host www.youtube.com plus list= routes to
    # get_playlist, anything else to ytsearch.
    # Still open and intentionally not guessed at here: a single-video URL is
    # text-searched rather than loaded by id. That call is a backend API question
    # and belongs with the backend pass.
    try:
        search_results = await nextwave.tracks.YouTubeTrack.search(search)
    except nextwave.ZeroConnectedNodes:
        if announce:
            await interaction.send(NO_NODE_MESSAGE, ephemeral=True)
        return None

    if not search_results:
        # Node.get_tracks returns [] for LoadType.no_matches, so indexing [0]
        # below was a plain IndexError that surfaced as "something went wrong".
        if announce:
            await interaction.send(
                embed=nextcord.Embed(
                    description="No results for that search.", color=embed_color
                ),
                ephemeral=True,
            )
        return None

    first_track = search_results[0]
    # Recorded before playback, not after. This used to sit at the very end of the
    # command -- roughly 20 lines and a network round trip after the track
    # actually started -- so /nowplaying inside that window answered "Song not
    # found" about a song that was already audible.
    user_dict[first_track.identifier] = interaction.user.mention

    if vc.queue.is_empty and not vc.is_playing():
        await vc.play(first_track)
        if announce:
            await interaction.send(
                embed=nextcord.Embed(
                    description=f"**Search found**\n\n`{first_track.title}`",
                    color=embed_color,
                ),
                delete_after=5,
            )
    else:
        await vc.queue.put_wait(first_track)
        if announce:
            await interaction.send(
                embed=nextcord.Embed(
                    description=f"Added to the `QUEUE`\n\n`{first_track.title}`",
                    color=embed_color,
                )
            )

    setattr(vc, "loop_track", False)
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
        vc: nextwave.Player = interaction.guild.voice_client
        if vc is None:
            vc = await interaction.user.voice.channel.connect(cls=nextwave.Player)
    except nextwave.ZeroConnectedNodes:
        # Player.__init__ resolves a node via NodePool.get_node(), so a /play that
        # races the node coming up raises rather than handing back a player.
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


@bot.event
async def on_nextwave_track_end(player: nextwave.Player, track: nextwave.Track, reason):
    # This handler used to read player.guild.voice_client -- a different object
    # from the player that fired the event once a guild has reconnected -- and
    # read it OUTSIDE the try below. A missing `loop` default then raised
    # AttributeError into nextcord's default on_error, which only prints: the
    # queue stopped advancing and music died with no member-visible signal.
    #
    # The reason values are logged rather than gated on purpose. nextwave
    # dispatches this for every TrackEndEvent including the stopped/cleanup ones
    # that app.py's own vc.stop() calls cause, and /skip currently relies on that
    # event to advance. Blocking stop reasons here without first making /skip
    # self-advance would turn /skip into "stop and silence", so the exact
    # spellings have to be read off a live node before the gate is written.
    log.debug("track end on %s: %r reason=%r", player, getattr(track, "title", track), reason)
    if not player.is_connected():
        # A cleanup or force-disconnect event arrives after VoiceProtocol.cleanup
        # has already torn the client down; advancing or announcing there raises
        # for no effect.
        return
    if getattr(player, "loop_track", False):
        return await player.play(track)

    try:
        if not player.queue.is_empty:
            if player.lq:
                player.queue.put(player.queue._queue[0])  # Assuming lq is a custom property for loop queue
            next_song = player.queue.get()
            await player.play(next_song)
            channel = player.channel
            await channel.send(
                embed=nextcord.Embed(
                    description=f"**Now playing from the queue:**\n\n`{next_song.title}`",color=embed_color,
                    ),
                delete_after=max(5, player.track.length / 1000)
                )
        else:
            await player.stop()
            channel = player.channel
            await channel.send(
                embed=nextcord.Embed(
                    description="The queue is empty.", color=embed_color
                ),delete_after=5
            )
        
    except Exception:
        # An error handler that raises is worse than one that only logs: the
        # second exception escapes into event dispatch. This used to dereference
        # player.channel and send unconditionally, so a teardown racing the
        # failure produced two errors and never recorded the real cause.
        log.exception("could not advance the queue after this track ended")
        channel = getattr(player, "channel", None)
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
        vc: nextwave.Player = interaction.guild.voice_client
        if vc is None:
            vc = await interaction.user.voice.channel.connect(cls=nextwave.Player)
    except nextwave.ZeroConnectedNodes:
        return await interaction.send(NO_NODE_MESSAGE, ephemeral=True)

    # `total` is the caller's request, captured once and never mutated. The old
    # code decremented `limit` inside only the play-now branch and then keyed both
    # progress strings to `limit == 100`, so /play's internal call (limit=10)
    # printed "Song no. 92" for every track, and a busy player froze at
    # "Song no. 1 ... /100" for the whole playlist.
    total = limit
    added = 0
    unmatched = 0

    queue_embed = nextcord.Embed(
        description="initializing the **QUEUE**...", color=embed_color
    )
    queue_completion = None
    try:
        queue_completion = await interaction.send(embed=queue_embed)

        async for partial in spotify.SpotifyTrack.iterator(
            query=search,
            type=spotify.SpotifySearchType.playlist,
            partial_tracks=True,
            limit=limit,
        ):
            # Per-track guard: previously one failed lookup propagated out of the
            # whole loop, so a single unmatched title aborted the rest of the
            # playlist and left "initializing the QUEUE..." on screen forever.
            try:
                youtube_tracks = await nextwave.tracks.YouTubeTrack.search(partial.title)
            except nextwave.NextwaveError as exc:
                unmatched += 1
                log.warning("could not resolve spotify track %r: %r", partial.title, exc)
                continue
            if not youtube_tracks:
                unmatched += 1
                continue

            youtube_track = youtube_tracks[0]
            user_dict[youtube_track.identifier] = interaction.user.mention

            if vc.queue.is_empty and not vc.is_playing():
                await vc.play(youtube_track)
            else:
                await vc.queue.put_wait(youtube_track)
            added += 1

            queue_embed.description = (
                f"Added `{added}/{total}` from the playlist; "
                f"the **QUEUE** holds `{vc.queue.count}`"
            )
            await queue_completion.edit(embed=queue_embed)

        setattr(vc, "loop_track", False)

    except spotify.SpotifyRequestError as exc:
        # Left narrow on purpose: this is the one failure that genuinely means the
        # Spotify link is bad. Anything else now propagates to
        # on_application_command_error, which logs the real cause instead of
        # mislabelling it.
        log.warning("spotify request failed for %r: %r", search, exc)
        await interaction.send(
            embed=nextcord.Embed(
                description="That Spotify link could not be loaded.", color=embed_color
            ),
            ephemeral=True,
        )
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
    vc: nextwave.Player = interaction.guild.voice_client

    if vc._source:
        if not vc.is_paused():
            await vc.pause()
            return await interaction.response.send_message(
                embed=nextcord.Embed(
                    description="`PAUSED` the music!", color=embed_color
                ),delete_after=5
            )

        elif vc.is_paused():
            return await interaction.response.send_message(
                embed=nextcord.Embed(
                    description="Already in `PAUSED State`", color=embed_color
                ),delete_after=5
            )
    else:
        return await interaction.response.send_message(
            embed=nextcord.Embed(
                description="Player is not `playing`!", color=embed_color
            ),delete_after=5
        )


@rate_limit(1, 2)
@bot.slash_command(name="resume", description="resumes the paused track", dm_permission=False)
async def resume_command(interaction: interactions.Interaction):
    if await user_connectivity(interaction) == False:
        return
    vc: nextwave.Player = interaction.guild.voice_client

    if vc.is_playing():
        if vc.is_paused():
            await vc.resume()
            await interaction.response.send_message(
                embed=nextcord.Embed(description="Music `RESUMED`!", color=embed_color),delete_after=5
            )

        elif vc.is_playing():
            await interaction.response.send_message(
                embed=nextcord.Embed(
                    description="Already in `RESUMED State`", color=embed_color
                ),delete_after=5
            )
    else:
        await interaction.response.send_message(
            embed=nextcord.Embed(
                description="Player is not `playing`!", color=embed_color
            ),delete_after=5
        )


@rate_limit(1, 2)
@require_role("tm")
@bot.slash_command(name="skip", description="skips to the next track", dm_permission=False)
async def skip_command(interaction: interactions.Interaction):
    if await user_connectivity(interaction) == False:
        return
    vc: nextwave.Player = interaction.guild.voice_client

    if vc.loop_track:
        vclooptxt = "Disable the `LOOP` mode to skip\n**/loop** again to disable the `LOOP` mode\nAdding songs disables the `LOOP` mode"
        return await interaction.response.send_message(
            embed=nextcord.Embed(description=vclooptxt, color=embed_color),delete_after=5
        )

    elif vc.queue.is_empty:
        await vc.stop()
        await vc.resume()
        return await interaction.response.send_message(
            embed=nextcord.Embed(
                description="Song stopped! No songs in the `QUEUE`",
                color=embed_color,
            ),delete_after=5
        )

    else:
        await vc.stop()
        vc.queue._wakeup_next()
        await vc.resume()
        await interaction.response.send_message(
            embed=nextcord.Embed(description="`SKIPPED`!", color=embed_color),delete_after=5
        )
        await queue_command(interaction)

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
    vc: nextwave.Player = interaction.guild.voice_client
    try:
        await vc.stop()
        await vc.resume()
        vc.queue._queue.clear()
        await vc.disconnect(force=True)
        await interaction.response.send_message(
            embed=nextcord.Embed(
                description="**BYE!** Have a great time!", color=embed_color
            )
        )
    except Exception:
        await interaction.response.send_message(
            embed=nextcord.Embed(description="Failed to destroy!", color=embed_color),delete_after=5
        )


# Auto-disconnect if all participants leave the voice channel
@bot.event
async def on_voice_state_update(member, before, after):
    if (
        before.channel is not None
        and (bot.user in before.channel.members and len(before.channel.members) == 1)
        or (member.id == bot.user.id and after.channel is None)
    ):
        for vc in bot.voice_clients:
            if vc.channel == before.channel:
                await vc.stop()
                await vc.resume()
                await vc.disconnect(force=True)
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
    vc: nextwave.Player = interaction.guild.voice_client
    if not vc.is_playing():
        return await interaction.response.send_message(
            embed=nextcord.Embed(description="Not playing anything!", color=embed_color)
        )

    # vcloop conditions
    loopstr = "enabled" if vc.loop_track else "disabled"
    state = "paused" if vc.is_paused() else "playing"
    # numpy array usertag indexing
    user_arr = np.array(list(user_dict.items()))
    song_index = np.flatnonzero(
        np.char.find(user_arr, vc.track.identifier) == 0
    )

    if len(song_index) == 0:
        return await interaction.response.send_message(
            embed=nextcord.Embed(description="Song not found", color=embed_color)
        )

    # Extract the first index from song_index array
    arr_index = int(song_index[0] / 2)

    requester = user_arr[arr_index, 1]

    nowplaying_description = (
        f"[`{vc.track.title}`]({str(vc.track.uri)})\n\n**Requested by**: {requester}"
    )
    em = nextcord.Embed(
        description=f"**Now Playing**\n\n{nowplaying_description}", color=embed_color
    )
    em.add_field(
        name="**Song Info**",
        value=f"• Author: `{vc.track.author}`\n• Duration: `{str(datetime.timedelta(milliseconds=vc.track.length))}`",
    )
    em.add_field(
        name="**Player Info**",
        value=f"• Player Volume: `{vc.volume}`\n• Loop: `{loopstr}`\n• Current State: `{state}`",
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
    vc: nextwave.Player = interaction.guild.voice_client
    if not vc._source:
        return await interaction.response.send_message(
            embed=nextcord.Embed(description="No song to `loop`", color=embed_color),delete_after=5
        )
    # Explicit toggle. The old `vc.loop ^= True` inside a bare try/except
    # swallowed the AttributeError from a player whose flag had never been set
    # and then reported "`disabled`" -- i.e. the command silently did nothing and
    # said the opposite of what it meant. The class default makes the recovery
    # branch unnecessary, so it is gone rather than kept as a hidden failure.
    vc.loop_track = not vc.loop_track
    return await interaction.response.send_message(
        embed=nextcord.Embed(
            description="**LOOP**: `enabled`" if vc.loop_track else "**LOOP**: `disabled`",
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
    vc: nextwave.Player = interaction.guild.voice_client

    if vc.queue.is_empty:
        return await interaction.send(
            embed=nextcord.Embed(description="**QUEUE**\n\n`empty`", color=embed_color)
        )
    
    lqstr = "`disabled`" if vc.lq == False else "`enabled`"
    
    song_array = np.array([(i+1, song.title if isinstance(song, nextwave.tracks.PartialTrack) else song.info["title"]) for i, song in enumerate(vc.queue, start=0)])

    await interaction.send(embed=nextcord.Embed(
        title=f"**QUEUE [total song count:{vc.queue.count}]**\n\n**loopqueue**: {lqstr}",
        description="\n".join([f"**{i}**. {song}" for i, song in song_array]),
        color=embed_color
    )
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
    vc: nextwave.Player = interaction.guild.voice_client
    if vc.queue.count > 1:
        vc.queue.shuffle()
        return await interaction.response.send_message(
            embed=nextcord.Embed(description="Shuffled the `QUEUE`", color=embed_color),delete_after=5
        )
    elif vc.queue.is_empty:
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
    vc: nextwave.Player = interaction.guild.voice_client
    if vc.queue.is_empty:
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
    elif position > vc.queue.count:
        return await interaction.response.send_message(
            embed=nextcord.Embed(
                description=f"Position `{position}` is outta range", color=embed_color
            ),delete_after=5
        )
    else:
        SongToBeDeleted = vc.queue._queue[position - 1].title
        del vc.queue._queue[position - 1]
        return await interaction.response.send_message(
            embed=nextcord.Embed(
                description=f"`{SongToBeDeleted}` removed from the QUEUE",
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
    vc: nextwave.Player = interaction.guild.voice_client
    if vc.queue.is_empty:
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
    elif position > vc.queue.count:
        return await interaction.response.send_message(
            embed=nextcord.Embed(
                description=f"Position `{position}` is outta range", color=embed_color
            ),delete_after=5
        )
    elif position == vc.queue._queue[position - 1]:
        return await interaction.response.send_message(
            embed=nextcord.Embed(
                description="Already in that `Position`!", color=embed_color
            ),delete_after=5
        )
    else:
        vc.queue.put_at_front(vc.queue._queue[position - 1])
        del vc.queue._queue[position]
        return await skip_command(interaction)


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
    vc: nextwave.Player = interaction.guild.voice_client
    if vc.queue.is_empty:
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

    queue_length = len(vc.queue)
    if song_position > queue_length or move_position > queue_length:
        position = song_position if song_position > queue_length else move_position
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
        move_song = vc.queue._queue[song_position - 1]
        vc.queue._queue.remove(move_song)
        move_index = move_position - 1
        vc.queue.put_at_index(move_index, move_song)

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
    vc: nextwave.Player = interaction.guild.voice_client
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
            await interaction.response.send_message(
                embed=nextcord.Embed(
                    description=f"**VOLUME**\nSet to `{playervolume}%`",
                    color=embed_color,
                ),delete_after=5
            )
            return await vc.set_volume(playervolume)
    elif not vc.is_connected():
        return await interaction.response.send_message(
            embed=nextcord.Embed(description="Player not connected!", color=embed_color),delete_after=5
        )



@rate_limit(1, 2)
@require_role("tm")
@bot.slash_command(name="restart", description="restarts the song", dm_permission=False)
async def restart_command(interaction: interactions.Interaction):
    if await user_connectivity(interaction) == False:
        return
    vc: nextwave.Player = interaction.guild.voice_client
    if not vc.is_playing():
        return await interaction.response.send_message(
            embed=nextcord.Embed(description="Player not playing!", color=embed_color),delete_after=5
        )
    elif vc.is_playing():
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
    vc: nextwave.Player = interaction.guild.voice_client
    if vc.queue.is_empty:
        return await interaction.response.send_message(
            embed=nextcord.Embed(
                description="No `SONGS` are present", color=embed_color
            ),delete_after=5
        )
    vc.queue._queue.clear()
    vc.lq = False
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
    vc: nextwave.Player = interaction.guild.voice_client
    if not vc.track:
        return await interaction.send(
            embed=nextcord.Embed(
                description="There is no `song` | `queue` available", color=embed_color
            ),
            delete_after=5,
        )
    # vc._source is nextwave's private audio-source object; formatting it sent the
    # user a Python repr. vc.track is what /nowplaying already uses for title+uri.
    saved = nextcord.Embed(
        description=f"[`{vc.track.title}`]({str(vc.track.uri)})\n\n**Saved from** {interaction.guild.name}",
        color=embed_color,
    )
    # DM first, then answer once with what actually happened. The original DM'd
    # before responding at all, so a member with DMs closed raised out of the
    # handler and left the interaction unanswered.
    try:
        await interaction.user.send(embed=saved)
    except nextcord.Forbidden:
        return await interaction.send(
            embed=nextcord.Embed(
                description="I could not DM you — allow direct messages from "
                            "server members and try again.",
                color=embed_color,
            )
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
    vc: nextwave.Player = interaction.guild.voice_client
    if not vc.is_playing():
        return await interaction.response.send_message(
            embed=nextcord.Embed(description="Player not playing!", color=embed_color),delete_after=5
        )    
    
    else:
        if seekpos < 0 or seekpos * 1000 > vc.track.length:
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

    The old seed was f"{vc.queue} {vc.track.title}", which interpolated the queue
    OBJECT -- a memory address or a raw deque dump, never song titles -- so the
    model was prompted with junk and handed back junk. Bounded and read-only on
    purpose: an unbounded seed dumps a 100-song queue into every prompt.
    """
    titles = []
    current = getattr(vc, "track", None)
    if current is not None and getattr(current, "title", None):
        titles.append(current.title)
    try:
        pending = list(vc.queue)
    except Exception as exc:  # queue shape is the backend's business
        log.warning("could not read the queue for the prediction seed: %r", exc)
        pending = []
    for song in pending:
        title = getattr(song, "title", None)
        if title is None:
            info = getattr(song, "info", None) or {}
            title = info.get("title")
        if title:
            titles.append(title)
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
    vc: nextwave.Player = interaction.guild.voice_client

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

    if vc.queue.is_empty and not vc.is_playing():
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
        asyncio.ensure_future(_graceful_shutdown())

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
    for vc in list(bot.voice_clients):
        try:
            await vc.disconnect(force=True)
        except Exception as exc:
            log.warning("could not disconnect a player on shutdown: %r", exc)
    for node in list(nextwave.NodePool.nodes.values()):
        # Node.disconnect drops its players, closes the aiohttp session and
        # removes the identifier from the class-level pool (pool.py:315-335).
        try:
            await node.disconnect(force=True)
        except Exception as exc:
            log.warning(
                "could not close node %s on shutdown: %r",
                getattr(node, "identifier", "?"), exc,
            )
    if _node_connect_task is not None:
        _node_connect_task.cancel()
    await bot.close()


"""main"""

if __name__ == "__main__":
    _missing = missing_env()
    if _missing:
        raise SystemExit(
            "missing required environment variable(s): " + ", ".join(_missing)
        )
    bot.run(os.getenv("TOKEN"))
