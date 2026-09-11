# T I S H M I S H
import nextcord
from nextcord import interactions
from nextcord.ext import commands, tasks
import nextwave
from nextwave.ext import spotify
import numpy as np
import os
import asyncio
import datetime
import sys
import time
import traceback

# I N T E N T S
intents = nextcord.Intents(messages=True, guilds=True)
intents.guild_messages = True
intents.members = True
intents.message_content = True
intents.voice_states = True
intents.emojis_and_stickers = True

bot = commands.Bot(
    intents=intents,
    description="Premium quality music bot for free!\nUse headphones for better quality <3",
)
# some useful variables

user_dict = {}
user_arr = np.array([])
setattr(nextwave.Player, "lq", False)
setattr(nextwave.Player, "autoplay", False)
embed_color = nextcord.Color.from_rgb(128, 67, 255)

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
# the Interaction, and a falsy return blocks the command with
# ApplicationCheckFailure.
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
            if getattr(member, "bot", False) or interaction.guild is None:
                return False
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
            if interaction.guild is None:
                return False
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
    """Fixed-window throttle: invocations starts per user per period seconds."""
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
    required=False, choices={"member commands", "tm commands"}
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
)
async def set_role_command(interaction: interactions.Interaction, user: nextcord.Member, role: nextcord.Role):
    if role.position > interaction.guild.me.top_role.position:
        return await interaction.response.send_message("I do not have permission to manage this role.", ephemeral=True)
    if role.position > user.top_role.position:
        return await interaction.response.send_message(
            f"`{user.name}` outranks that role, so it cannot be added to them.",
            ephemeral=True,
        )
    await user.add_roles(role)
    embed = nextcord.Embed(
        description=f"`{user.name}` has been given a role called: **{role.name}**",
        color=embed_color
    )
    await interaction.response.send_message(embed=embed)

#checks for user connection to voice channels
async def user_connectivity(interaction: interactions.Interaction):
    if not interaction.user.voice:
        await interaction.send("Join a voice channel first!")
        return False
    # Every caller reads interaction.guild.voice_client right after this returns
    # and then indexes into vc.queue / vc._source, so the member being in voice is
    # only half the precondition: if the bot itself is not connected the player is
    # None and those commands die with AttributeError instead of telling anyone.
    if interaction.guild.voice_client is None:
        await interaction.send("I am not connected to a voice channel!")
        return False
    return True

@bot.event
async def on_ready():
    print(f"logged in as: {bot.user.name}")
    bot.loop.create_task(node_connect())
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
        print(f"could not report to member {member}: {exc!r}", file=sys.stderr)


@bot.event
async def on_application_command_error(interaction, error):
    """Make refusals and failures visible.

    nextcord's default handler only prints the traceback to stderr, so a blocked
    or throttled command looked like a dead bot -- Discord reports an unanswered
    interaction as "the application did not respond". Every gate here raises
    ApplicationCheckFailure with a reason, which is forwarded to the member
    ephemerally; anything unexpected is logged and answered generically.
    """
    if isinstance(error, nextcord.ApplicationCheckFailure):
        await reply_quietly(
            interaction, str(error) or "You cannot use that command right now."
        )
        return

    print("ignoring exception in application command:", file=sys.stderr)
    traceback.print_exception(type(error), error, error.__traceback__, file=sys.stderr)
    # An expired interaction cannot be answered at all, and a command that threw
    # after already replying must not turn one error into two.
    await reply_quietly(interaction, "Something went wrong running that command.")


@bot.event
async def on_nextwave_node_ready(node: nextwave.Node):
    print(f"Node {node.identifier} connected successfully")


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


async def node_connect():
    await bot.wait_until_ready()
    # Resolve the port once, outside the retry loop: it sat inside the try below,
    # so a typo'd value raised ValueError, got swallowed as a connection failure,
    # and retried forever with backoff instead of reporting a bad config.
    try:
        port = int(os.getenv('LAVALINK_PORT'))
    except (TypeError, ValueError):
        print(
            f"LAVALINK_PORT is not a number ({os.getenv('LAVALINK_PORT')!r}); "
            "not connecting to any node."
        )
        return
    delay = 5
    while True:
        try:
            await nextwave.NodePool.create_node(
                bot=bot,
                host=os.getenv('LAVALINK_HOST'),
                port=port,
                password=os.getenv('LAVALINK_PASSWORD'),
                https=env_flag('LAVALINK_SECURE'),
                spotify_client=spotify.SpotifyClient(
                    client_id=os.getenv('SPOTIFY_CLIENT_ID'),
                    client_secret=os.getenv('SPOTIFY_CLIENT_SECRET'),
                ),
            )
            return
        except Exception as exc:
            # Ran as a bare create_task before, so a Lavalink outage killed the
            # task and nothing retried: the bot stayed up with no node forever.
            print(f"lavalink node connect failed: {exc!r}; retrying in {delay}s")
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
)
async def loopqueue_command(interaction: interactions.Interaction, type: str=nextcord.SlashOption(
    name="lq-options", description='options for loop queue', required=True, choices={"start","stop"}
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
            print(f"loopqueue: could not requeue the current source: {exc!r}")
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
    em = nextcord.Embed(
        description=f"**Pong!**\n\n`{round(bot.latency*1000)}`ms", color=embed_color
    )
    await interaction.response.send_message(embed=em, delete_after=5)


@rate_limit(1, 1)
@bot.slash_command(
    name="play", description="plays the given track provided by the user"
)
async def play_command(interaction: interactions.Interaction, *, search: str):
    if not interaction.user.voice:
        return await interaction.send("Join a voice channel first!")
    elif not interaction.guild.voice_client:
        vc: nextwave.Player = await interaction.user.voice.channel.connect(
            cls=nextwave.Player
        )
    else:
        vc: nextwave.Player = interaction.guild.voice_client
    try:
        if search.startswith('https://open.spotify.com/playlist/') or search.startswith('https://open.spotify.com/album/') or search.startswith('https://open.spotify.com/track/'):
            await spotifyplay_command(interaction, search, limit=10)
            return
        if search.startswith('https://youtu.be/') or search.startswith('https://www.youtube.com/'):
            search = search.split("&")[0]
            search = search.split("?")[0]
            search = search.replace("https://youtu.be/","")
            search = search.replace("https://www.youtube.com/","")
            search = f"https://www.youtube.com/watch?v={search}"
    except Exception:
        return await interaction.send(embed=nextcord.Embed(description="Invalid Spotify URL", color=embed_color))
        
    search_results = await nextwave.tracks.YouTubeTrack.search(search)
    first_track = search_results[0] # Get the first track from the list
    if vc.queue.is_empty and vc.is_playing() is False:
        
        playString = await interaction.send(
            embed=nextcord.Embed(description="**searching...**", color=embed_color)
        )
        
        await vc.play(first_track)

        await playString.edit(
            embed=nextcord.Embed(
                description=f"**Search found**\n\n`{first_track.title}`",
                color=embed_color,
            ),
            delete_after=5,
        )
        
    else:
        await vc.queue.put_wait(first_track)
        await interaction.send(
            embed=nextcord.Embed(
                description=f"Added to the `QUEUE`\n\n`{first_track.title}`",
                color=embed_color,
            )
        )

        # await added_to_queue_msg.edit(embed=nextcord.Embed(description=f"Added to the `QUEUE`\n\n`{first_track.title}`", color=embed_color), delete_after=5)
    
    setattr(vc, "loop", False)
    user_dict[first_track.identifier] = interaction.user.mention
@bot.event
async def on_nextwave_track_end(player: nextwave.Player, track: nextwave.Track, reason):
    
    vc: nextwave.Player = player.guild.voice_client
    if vc.loop is True:
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
            channel = player.channel
            await channel.send(
            embed=nextcord.Embed(
                description="An error occurred while playing the next song.", color=embed_color
            ),delete_after=5
        ) 


@rate_limit(1, 1)
@bot.slash_command(
    name="spotifyplay",
    description="plays the provided spotify playlist link up to the provided song number",
)
async def spotifyplay_command(
    interaction: interactions.Interaction, search: str, limit: int = 100
):
    if not interaction.user.voice:
        return await interaction.send("Join a voice channel first!")

    vc: nextwave.Player = (
        interaction.guild.voice_client
        or await interaction.user.voice.channel.connect(cls=nextwave.Player)
    )

    try:
        # Initialize the embed before the loop
        queue_embed = nextcord.Embed(
            description="initializing the **QUEUE**...", color=embed_color
        )
        queue_completion = await interaction.send(embed=queue_embed)
        
        # Iterate over the tracks
        async for partial in spotify.SpotifyTrack.iterator(
            query=search,
            type=spotify.SpotifySearchType.playlist,
            partial_tracks=True,
            limit=limit,
        ):
            # Search for YouTubeTrack using the title
            youtube_tracks = await nextwave.tracks.YouTubeTrack.search(partial.title)
            if not youtube_tracks:
                continue  # Skip if no YouTube track is found

            youtube_track = youtube_tracks[0]
            user_dict[youtube_track.identifier] = interaction.user.mention

            if vc.queue.is_empty and vc.is_playing() is False:
                await vc.play(youtube_track)
                limit -= 1
            else:
                await vc.queue.put_wait(youtube_track)
            
            # Update the embed description with the current status
            if limit == 100:
                queue_embed.description = f"Song no. `1` added to the track and remaining are being pushed to the **QUEUE**:`{vc.queue.count}/100`"
            else:
                queue_embed.description = f"Song no. `{100 - limit + 1}` added to the track and remaining are being pushed to the **QUEUE**:`{vc.queue.count}/{limit}`"
            await queue_completion.edit(embed=queue_embed)

        setattr(vc, "loop", False)

        queue_embed.description = f"Total successfully added to the **QUEUE**: `{vc.queue.count}`"
        await queue_completion.edit(embed=queue_embed)

    except spotify.SpotifyRequestError as e:
        await interaction.send(
            embed=nextcord.Embed(description=f"{e}", color=embed_color)
        )

@rate_limit(1, 2)
@bot.slash_command(name="pause", description="pauses the current playing track")
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
@bot.slash_command(name="resume", description="resumes the paused track")
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
@bot.slash_command(name="skip", description="skips to the next track")
async def skip_command(interaction: interactions.Interaction):
    if await user_connectivity(interaction) == False:
        return
    vc: nextwave.Player = interaction.guild.voice_client

    if vc.loop == True:
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
)
async def nowplaying_command(interaction: interactions.Interaction):
    if await user_connectivity(interaction) == False:
        return
    vc: nextwave.Player = interaction.guild.voice_client
    if not vc.is_playing():
        return await interaction.response.send_message(
            embed=nextcord.Embed(description="Not playing anything!", color=embed_color)
        )

    # vcloop conditions
    loopstr = "enabled" if vc.loop else "disabled"
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
)
async def loop_command(interaction: interactions.Interaction):
    if await user_connectivity(interaction) == False:
        return
    vc: nextwave.Player = interaction.guild.voice_client
    if not vc._source:
        return await interaction.response.send_message(
            embed=nextcord.Embed(description="No song to `loop`", color=embed_color),delete_after=5
        )
    try:
        vc.loop ^= True
    except Exception:
        setattr(vc, "loop", False)
    return (
        await interaction.response.send_message(
            embed=nextcord.Embed(description="**LOOP**: `enabled`", color=embed_color),delete_after=5
        )
        if vc.loop
        else await interaction.response.send_message(
            embed=nextcord.Embed(description="**LOOP**: `disabled`", color=embed_color), delete_after=5
        )
    )

@rate_limit(1, 2)
@bot.slash_command(
    name="queue",
    description="displays the current queue",
)
async def queue_command(interaction: interactions.Interaction):
    if await user_connectivity(interaction) == False:
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
@bot.slash_command(name="volume", description="sets the volume")
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
@bot.slash_command(name="restart", description="restarts the song")
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
    name="clear", description="clears the queue"
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
)
async def save_command(interaction: interactions.Interaction):
    if await user_connectivity(interaction) == False:
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
@bot.slash_command(name="seek", description="seeks to the specified position for eg. 30sec")
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

#autoplay command development
from g4f.client import Client
# from g4f.Provider import HuggingChat
import nest_asyncio
nest_asyncio.apply()

client = Client()
    # Get the seed for prediction


@rate_limit(1, 5)
@require_role("tm")
@bot.slash_command(name="predict", description="Predict and add songs to the queue")
async def predict_command(interaction: nextcord.Interaction, num_songs: int):
    if num_songs < 3 or num_songs > 10:
        return await interaction.response.send_message(
            embed=nextcord.Embed(
                description="Please enter a number between 3 to 10 for predictions.",
                color=embed_color
            ),
            delete_after=5
        )

    vc: nextwave.Player = interaction.guild.voice_client

    if vc is None or not vc.is_connected():
        return await interaction.response.send_message(
            embed=nextcord.Embed(
                description="The bot is not connected to a voice channel.",
                color=embed_color
            ),
            delete_after=5
        )

    if vc.queue.is_empty and not vc.is_playing():
        return await interaction.response.send_message(
            embed=nextcord.Embed(
                description="There's nothing currently playing or in the queue to base predictions on.",
                color=embed_color
            ),
            delete_after=5
        )

    # Defer the response to avoid timeout
    await interaction.response.defer()
    # Get the seed for prediction
    if vc.queue.is_empty:
        seed_song = vc.track.title
    else:
        seed_song = f"{vc.queue} {vc.track.title}"

    # Ask the AI to predict multiple songs at once
    chat_completion = client.chat.completions.create(
        # model="CodeLlama-70b-Instruct-hf",  # Ensure you use the correct model
        model="gpt-4",
        messages=[{"role": "user", "content": f'predict the next {num_songs} number of songs based on this list of song/s for the user, do not send long texts, here is the list - {seed_song}, IMPORTANT remember to seperate each preidicted songs by this ===='}]
    )
    
    response = chat_completion.choices[0].message.content
    predicted_songs = response.split("====")

    # Loop through each predicted song and use the play_command to add it to the queue
    # print(seed_song)
    # print(response)
    # print(predicted_songs)
    
    for songs in predicted_songs:
        # print(songs)
        await play_command(interaction, search=songs)

    # Send the final confirmation message after all songs have been added
    await interaction.followup.send(
        embed=nextcord.Embed(
            description=f"Added {num_songs} predicted songs to the queue.",
            color=embed_color
        ),
        delete_after=10
    )



"""main"""

if __name__ == "__main__":
    _missing = missing_env()
    if _missing:
        raise SystemExit(
            "missing required environment variable(s): " + ", ".join(_missing)
        )
    bot.run(os.getenv("TOKEN"))