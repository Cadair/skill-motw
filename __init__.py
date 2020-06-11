import re as regex
from textwrap import dedent
from random import randint

from opsdroid.matchers import match_regex

STAT_REGEX = "(?:(?:!cool|!tough|!sharp|!charm|!weird) [+-]?[0,1,2,3])"


async def get_mxid(nick, room, connector):
    members = await connector.connection.get_room_members(
        connector.lookup_target(room)
    )
    members = {m['content']['displayname']: m['state_key'] for m in members['chunk']}
    return members.get(nick, None)


async def get_nick(config, message):
    nick = message.user
    mxid = message.user_id
    if message.user_id == config.get('keeper', None):
        message_nick = message.regex.groupdict()['nick'].strip()
        if message_nick:
            nick = message_nick
            mxid = await get_mxid(nick, message.target, message.connector)
            if not mxid:
                await message.respond(
                    f"Could not find the user {nick} in the room."
                )
    return nick, mxid


def pretty_stats(stats):
    pretty_stats = []
    for name, stat in stats.items():
        stat_sign = "-" if stat < 0 else "+"
        pretty_stats.append(f"{name.capitalize()} {stat_sign}{abs(stat)}")
    return ", ".join(pretty_stats)


def two_d6():
    return randint(1, 6), randint(1, 6)


@match_regex("!help")
async def help(opsdroid, config, message):
    await message.respond(dedent("""\
        <p>
        This bot can make checks against your stats. The possible stats are:
        </p>
        <ul>
            <li>Charm</li>
            <li>Cool</li>
            <li>Sharp</li>
            <li>Tough</li>
            <li>Weird</li>
        </ul>
        <p>
        You can roll against these stats by typing <code>+stat</code>, i.e. <code>+Weird</code>.
        </p>

        <p>
        You can set your stats with <code>!stat number</code>, i.e. <code>!weird +1</code> you can
        set as many stats as you like in one command, i.e.
        <code>!weird +1 !charm +1 !sharp -1</code>.
        </p>
        <p>
        You can retrieve your characters stats with <code>!stats</code>.
        </p>
        <p>
        Remember you can set your nick to your character name with
        <code>/myroomnick</code> in Riot if you desire.
        </p>\
    """))

@match_regex(f"(?P<nick>[^!]*){STAT_REGEX}", case_sensitive=False)
async def set_stats(opsdroid, config, message):
    nick, mxid = await get_nick(config, message)

    stats = message.text
    if nick != message.user:
        stats = message.text.split(nick)[1]

    stats = regex.findall(STAT_REGEX, stats, flags=regex.IGNORECASE)
    stats = tuple(s.split(' ') for s in stats)
    stats = dict((s[0].lower()[1:], int(s[1])) for s in stats)

    existing_stats = await opsdroid.memory.get("motw_stats")
    if not existing_stats or mxid not in existing_stats:
        existing_stats = {}
    else:
        existing_stats = existing_stats[mxid]

    stats = {**existing_stats, **stats}

    await message.respond(f"Setting stats for {nick}: {pretty_stats(stats)}")

    await opsdroid.memory.put("motw_stats", {mxid: stats})


@match_regex("!stats ?(?P<nick>.*)")
async def get_stats(opsdroid, config, message):
    nick, mxid = await get_nick(config, message)
    motw_stats = await opsdroid.memory.get("motw_stats")
    if not motw_stats or mxid not in motw_stats:
        await message.respond(f"No stats for {nick}.")
        return

    stats = motw_stats[mxid]
    await message.respond(f"Stats for {nick}: {pretty_stats(stats)}")


@match_regex(r"\+(?P<stat>cool|tough|sharp|charm|weird)", case_sensitive=False)
async def roll(opsdroid, config, message):
    stat = message.regex.string[1:].lower()
    mxid = message.user_id

    motw_stats = await opsdroid.memory.get("motw_stats")

    if not motw_stats or mxid not in motw_stats:
        await message.respond(rf"No stats found for {message.user}, run '!{stat} +number'")
        return

    stats = motw_stats[mxid]

    if stat not in stats:
        await message.respond(
            rf"You have not set {stat}, run '!{stat} +number'"
        )
        return

    stat = stats[stat]
    d1, d2 = two_d6()
    number_result = d1 + d2 + stat
    if number_result <= 6:
        result = "Failure"
    elif number_result <= 10:
        result = "Mixed Success"
    else:
        result = "Full Success"

    stat_sign = "+" if stat > 1 else "-"
    await message.respond(
        f"You rolled {d1} + {d2} {stat_sign} {abs(stat)} = {number_result} ({result})"
    )
