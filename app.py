# T I S H M I S H
import nextcord
from nextcord import interactions
from nextcord.ext import commands, tasks
import nextwave
from nextwave.ext import spotify
import numpy as np
import os
import datetime

# I N T E N T S
intents = nextcord.Intents(messages=True, guilds=True)
intents.guild_messages = True
intents.members = True
intents.message_content = True
intents.voice_states = True
intents.emojis_and_stickers = True
all_intents = intents.all()
all_intents = True

bot = commands.Bot(
    intents=intents,
    description="Premium quality music bot for free!\nUse headphones for better quality <3",
)
# some useful variables

global user_arr, user_dict
user_dict = {}
user_arr = np.array([])
setattr(nextwave.Player, "lq", False)
setattr(nextwave.Player, "autoplay", False)
embed_color = nextcord.Color.from_rgb(128, 67, 255)

# # T I S M I S H help-command
@commands.cooldown(1, 1, commands.BucketType.user)
@bot.slash_command(name="help", description="All you need")
async def help(interaction: nextcord.Interaction, helpstr: str = nextcord.SlashOption(
    name='help_choices', description='Choose one of the help commands',
    required=False, choices={"member commands", "tm commands"}
)):
    # List of TM and Member commands
    commands_dict = {
        "tm commands": [
            skip_command, del_command, move_command, clear_command, seek_command, 
            volume_command, skipto_command, shuffle_command, loop_command, 
            disconnect_command, loopqueue_command, set_role_command, 
            spotifyplay_command, restart_command, predict_command
        ],
        "member commands": [
            ping_command, play_command, pause_command, resume_command, 
            nowplaying_command, queue_command, save_command
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
                  "[Help](https://github.com/awmie/tishmish/blob/main/readme.md)",
        )
        await interaction.response.send_message(embed=embed)



# T I S H M I S H commands

@commands.cooldown(1, 2, commands.BucketType.user)
@bot.slash_command(
    name="role",
    description="sets an existing role which are below tishmish(role) for a user",
)
@commands.has_permissions(manage_roles=True)
async def set_role_command(interaction: interactions.Interaction, user: nextcord.Member, role: nextcord.Role):
    if role.position > interaction.guild.me.top_role.position:
        return await interaction.response.send_message("I do not have permission to manage this role.", ephemeral=True)
    if role.position > user.top_role.position:
        return await interaction.response.send_message("You do not have permission to manage this role.", ephemeral=True)
    await user.add_roles(role)
    embed = nextcord.Embed(
        description=f"`{user.name}` has been given a role called: **{role.name}**",
        color=embed_color
    )
    await interaction.response.send_message(embed=embed)

#checks for user connection to voice channels
async def user_connectivity(interaction: interactions.Interaction):
    if not interaction.user.voice:
        await interaction.response.send_message("Join a voice channel first!")
        return False

@bot.event
async def on_ready():
    print(f"logged in as: {bot.user.name}")
    bot.loop.create_task(node_connect())
    await bot.change_presence(
        activity=nextcord.Activity(type=nextcord.ActivityType.listening, name="/play")
    )


@bot.event
async def on_nextwave_node_ready(node: nextwave.Node):
    print(f"Node {node.identifier} connected successfully")


async def node_connect():
    await bot.wait_until_ready()
    await nextwave.NodePool.create_node(
        bot=bot,
        host="v3.lavalink.rocks",
        port=443,
        password="horizxon.tech",
        https=True,
        spotify_client=spotify.SpotifyClient(
            client_id=os.environ['SPOTIFY_CLIENT_ID'],
            client_secret=os.environ['SPOTIFY_CLIENT_SECRET'],
        ),
    )

@commands.cooldown(1, 2, commands.BucketType.user)
@bot.slash_command(name="info", description="shows information about the bot")
@commands.is_owner()
@commands.has_role("tm")
async def info_command(interaction: interactions.Interaction):
    await interaction.response.send_message(
        embed=nextcord.Embed(
            description=f"**Info**\ntotal server count: `{len(bot.guilds)}`",
            color=embed_color,
        )
    )


@commands.cooldown(1, 2, commands.BucketType.user)
@bot.slash_command(
    name="loopqueue",
    description="loops the queue",
)
@commands.has_role("tm")
async def loopqueue_command(interaction: interactions.Interaction, type: str=nextcord.SlashOption(
    name="lq-options", description='options for loop queue', required=True, choices={"start","stop"}
)):
    vc: nextwave.Player = interaction.guild.voice_client
    if vc.queue.is_empty:
        return await interaction.response.send_message(
            embed=nextcord.Embed(
                description="Unable to loop `QUEUE`, try adding more songs..",
                color=embed_color,
            )
        )
    if vc.lq == False and type == "start":
        vc.lq = True
        await interaction.response.send_message(
            embed=nextcord.Embed(
                description="**loopqueue**: `enabled`", color=embed_color
            )
        )
        try:
            if vc._source not in vc.queue:
                vc.queue.put(vc._source)
            else:
                """"""
        except Exception:
            return ""
    if vc.lq == True and type == "stop":
        vc.lq = False
        await interaction.response.send_message(
            embed=nextcord.Embed(
                description="**loopqueue**: `disabled`", color=embed_color
            )
        )
        if vc.queue.count == 1 and vc.queue._queue[0] == vc._source:
            del vc.queue._queue[0]
        else:
            return ""

@commands.cooldown(1, 2, commands.BucketType.user)
@bot.slash_command(name="ping", description="displays bot's latency")
async def ping_command(interaction: interactions.Interaction):
    em = nextcord.Embed(
        description=f"**Pong!**\n\n`{round(bot.latency*1000)}`ms", color=embed_color
    )
    await interaction.response.send_message(embed=em, delete_after=5)


@commands.cooldown(1, 1, commands.BucketType.user)
@bot.slash_command(
    name="play", description="plays the given track provided by the user"
)
async def play_command(interaction: interactions.Interaction, *, search: str):
    if not interaction.user.voice:
        return await interaction.response.send_message("Join a voice channel first!")
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
        return await interaction.response.send_message(embed=nextcord.Embed(description="Invalid Spotify URL", color=embed_color))
        
    search_results = await nextwave.tracks.YouTubeTrack.search(search)
    first_track = search_results[0] # Get the first track from the list
    if vc.queue.is_empty and vc.is_playing() is False:
        
        playString = await interaction.response.send_message(
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
                delete_after=player.track.length
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


@commands.cooldown(1, 1, commands.BucketType.user)
@bot.slash_command(
    name="spotifyplay",
    description="plays the provided spotify playlist link up to the provided song number",
)
async def spotifyplay_command(
    interaction: interactions.Interaction, search: str, limit: int = 100
):
    if not interaction.user.voice:
        return await interaction.response.send_message("Join a voice channel first!")

    vc: nextwave.Player = (
        interaction.guild.voice_client
        or await interaction.user.voice.channel.connect(cls=nextwave.Player)
    )

    try:
        # Initialize the embed before the loop
        queue_embed = nextcord.Embed(
            description="initializing the **QUEUE**...", color=embed_color
        )
        queue_completion = await interaction.response.send_message(embed=queue_embed)
        
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
        await interaction.response.send_message(
            embed=nextcord.Embed(description=f"{e}", color=embed_color)
        )

@commands.cooldown(1, 2, commands.BucketType.user)
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


@commands.cooldown(1, 2, commands.BucketType.user)
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


@commands.cooldown(1, 2, commands.BucketType.user)
@bot.slash_command(name="skip", description="skips to the next track")
@commands.has_role("tm")
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

@commands.cooldown(1, 2, commands.BucketType.user)
@bot.slash_command(
    name="disconnect",
    description="disconnects the player from the vc",
)
@commands.has_role("tm")
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

@commands.cooldown(1, 2, commands.BucketType.user)
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
    """numpy array usertag indexing"""
    global user_list
    user_list = list(user_dict.items())
    user_arr = np.array(user_list)
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
        value=f"• Author: `{vc.track.author}`\n• Duration: `{str(datetime.timedelta(seconds=vc.track.length))}`",
    )
    em.add_field(
        name="**Player Info**",
        value=f"• Player Volume: `{vc.volume}`\n• Loop: `{loopstr}`\n• Current State: `{state}`",
        inline=False,
    )

    return await interaction.response.send_message(embed=em, delete_after=10)


@commands.cooldown(1, 2, commands.BucketType.user)
@bot.slash_command(
    name="loop",
    description="loop / exitloop",
)
@commands.has_role("tm")
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

@commands.cooldown(1, 2, commands.BucketType.user)
@bot.slash_command(
    name="queue",
    description="displays the current queue",
)
async def queue_command(interaction: interactions.Interaction):
    if await user_connectivity(interaction) == False:
        return
    vc: nextwave.Player = interaction.guild.voice_client

    if vc.queue.is_empty:
        return await interaction.response.send_message(
            embed=nextcord.Embed(description="**QUEUE**\n\n`empty`", color=embed_color)
        )
    
    lqstr = "`disabled`" if vc.lq == False else "`enabled`"
    
    song_array = np.array([(i+1, song.title if isinstance(song, nextwave.tracks.PartialTrack) else song.info["title"]) for i, song in enumerate(vc.queue, start=0)])

    await interaction.response.send_message(embed=nextcord.Embed(
        title=f"**QUEUE [total song count:{vc.queue.count}]**\n\n**loopqueue**: {lqstr}",
        description="\n".join([f"**{i}**. {song}" for i, song in song_array]),
        color=embed_color
    )
)

@commands.cooldown(1, 2, commands.BucketType.user)
@bot.slash_command(
    name="shuffle",
    description="shuffles the existing queue randomly",
)
@commands.has_role("tm")
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


@commands.cooldown(1, 2, commands.BucketType.user)
@bot.slash_command(
    name="del",
    description="deletes the specified track",
)
@commands.has_role("tm")
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


@commands.cooldown(1, 2, commands.BucketType.user)
@bot.slash_command(
    name="skipto",
    description="skips to the specified track",
)
@commands.has_role("tm")
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


@commands.cooldown(1, 2, commands.BucketType.user)
@bot.slash_command(
    name="move",
    description="moves the track to the specified position",
)
@commands.has_role("tm")
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

@commands.cooldown(1, 2, commands.BucketType.user)
@bot.slash_command(name="volume", description="sets the volume")
@commands.has_role("tm")
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



@commands.cooldown(1, 2, commands.BucketType.user)
@bot.slash_command(name="restart", description="restarts the song")
@commands.has_role("tm")
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
        


@commands.cooldown(1, 5, commands.BucketType.user)
@bot.slash_command(
    name="clear", description="clears the queue"
)
@commands.has_role("tm")
async def clear_command(interaction: interactions.Interaction):
    vc: nextwave.Player = interaction.guild.voice_client
    if await user_connectivity(interaction) == False:
        return
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


@commands.cooldown(1, 2, commands.BucketType.user)
@bot.slash_command(
    name="save",
    description="dms the current or specified song to the user",
)
async def save_command(interaction: interactions.Interaction):
    vc: nextwave.Player = interaction.guild.voice_client
    if await user_connectivity(interaction) == False:
        return
    user = await bot.fetch_user(interaction.user.id)
    if vc._source:
        await user.send(
            embed=nextcord.Embed(description=f"`{vc._source}`", color=embed_color)
        )
        song_saved = await interaction.response.send_message(
            embed=nextcord.Embed(description="**SONG** saved!", color=embed_color),delete_after=5
        )
        await song_saved.delete(delay=5)
        
    else:
        return await interaction.response.send_message(
            embed=nextcord.Embed(
                description="There is no `song` | `queue` available", color=embed_color
            ),delete_after=5
        )

@commands.cooldown(1,2,commands.BucketType.user)
@bot.slash_command(name="seek", description="seeks to the specified position for eg. 30sec")
@commands.has_role("tm")
async def seek_command(interaction:interactions.Interaction, seekpos: int):
    if await user_connectivity(interaction) == False:
        return 
    vc: nextwave.Player = interaction.guild.voice_client
    if not vc.is_playing():
        return await interaction.response.send_message(
            embed=nextcord.Embed(description="Player not playing!", color=embed_color),delete_after=5
        )    
    
    else:
        if seekpos < 0 or seekpos > vc.track.length:
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


@commands.cooldown(1, 5, commands.BucketType.user)
@bot.slash_command(name="predict", description="Predict and add songs to the queue")
@commands.has_role("tm")
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
    bot.run(os.environ["TOKEN"])