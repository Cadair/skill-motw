import re as regex
from textwrap import dedent
from random import randint
from functools import wraps

from opsdroid.matchers import match_regex, match_event
from opsdroid.events import UserInvite, JoinRoom, Event, Message


class MarkExperience(Event):
    _no_register = True
    pass


MODIFIER_REGEX = "[+-]?[0,1,2,3]"
STAT_REGEX = f"(?:(?:!cool|!tough|!sharp|!charm|!weird) {MODIFIER_REGEX})"


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


@match_event(UserInvite)
async def respond_to_invites(opsdroid, config, invite):
    if config.get('autoinvite', False):
        return await invite.respond(JoinRoom())


def memory_in_event_room(func):
    @wraps(func)
    async def _wrapper(opsdroid, config, message):
        db = opsdroid.get_database("matrix")
        if not db or not hasattr(db, "memory_in_room"):
            return await func(opsdroid, config, message)
        with db.memory_in_room(message.target):
            return await func(opsdroid, config, message)
    return _wrapper


@match_regex("!help")
async def help(opsdroid, config, message):
    await message.respond(dedent("""\
        <p>
        This bot makes checks against your stats, and tracks your experience.
        </p>
        <h1>
        Making Checks
        </h1>
        <ul>
            <li>Charm</li>
            <li>Cool</li>
            <li>Sharp</li>
            <li>Tough</li>
            <li>Weird</li>
        </ul>
        <p>
        You can roll against these stats by typing <code>+stat</code>, i.e. <code>+Weird</code>.
        You can append a single modifier on a roll by doing <code>+stat +x</code>, i.e. <code>+weird -1</code>.
        </p>
        <p>
        You can set your stats with <code>!stat number</code>, i.e. <code>!weird +1</code> you can
        set as many stats as you like in one command, i.e.
        <code>!weird +1 !charm +1 !sharp -1</code>.
        </p>
        <p>
        You can retrieve your characters stats with <code>!stats</code>.
        </p>
        <h1>
        Experience
        </h1>
        <p>
        When you roll a failure the bot will store and experience for you.
        You can manually mark experience by running <code>+experience</code>.
        To level up run <code>!levelup</code>.
        To check your current experience run <code>!experience</code>.
        </p>
        <p>
        Remember you can set your nick to your character name with
        <code>/myroomnick</code> in Riot if you desire.
        </p>\
    """))


@match_regex(f"(?P<nick>[^!]*){STAT_REGEX}", case_sensitive=False)
@memory_in_event_room
async def set_stats(opsdroid, config, message):
    nick, mxid = await get_nick(config, message)

    stats = message.text
    if nick != message.user:
        stats = message.text.split(nick)[1]

    stats = regex.findall(STAT_REGEX, stats, flags=regex.IGNORECASE)
    stats = tuple(s.split(' ') for s in stats)
    stats = dict((s[0].lower()[1:], int(s[1])) for s in stats)

    all_stats = await opsdroid.memory.get("motw_stats") or {}
    if not all_stats or mxid not in all_stats:
        existing_stats = {}
    else:
        existing_stats = all_stats[mxid]

    stats = {**existing_stats, **stats}

    await message.respond(f"Setting stats for {nick}: {pretty_stats(stats)}")

    new_stats = {**all_stats, **{mxid: stats}}
    await opsdroid.memory.put("motw_stats", new_stats)


@match_regex("!stats ?(?P<nick>.*)")
@memory_in_event_room
async def get_stats(opsdroid, config, message):
    nick, mxid = await get_nick(config, message)

    motw_stats = await opsdroid.memory.get("motw_stats")
    if not motw_stats or mxid not in motw_stats:
        await message.respond(rf"No stats found for {nick}, run '!<stat> +number'")
        return

    stats = motw_stats[mxid]
    await message.respond(f"Stats for {nick}: {pretty_stats(stats)}")


@match_regex(rf"\+(?P<stat>cool|tough|sharp|charm|weird) ?(?P<modifier>{MODIFIER_REGEX})?", case_sensitive=False)
@memory_in_event_room
async def roll(opsdroid, config, message):
    stat = message.regex.capturesdict()['stat'][0]
    modifier = message.regex.groupdict()['modifier'] or 0
    modifier = int(modifier)
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
    number_result = d1 + d2 + stat + modifier
    if number_result <= 6:
        result = "Failure"
    elif number_result < 10:
        result = "Mixed Success"
    else:
        result = "Full Success"

    stat_sign = "-" if stat < 0 else "+"
    mod_sign = "-" if modifier < 0 else "+"

    if modifier != 0:
        without_mod = d1 + d2 + stat
        equation = f"{d1} + {d2} {stat_sign} {abs(stat)} = {without_mod} {mod_sign} {abs(modifier)}"
    else:
        equation = f"{d1} + {d2} {stat_sign} {abs(stat)}"
    await message.respond(
        f'<a href="https://matrix.to/#/{message.user_id}">{message.user}</a> rolled {equation} = {number_result} ({result})'
    )

    if number_result <= 6:
        await opsdroid.parse(MarkExperience(user_id=message.user_id,
                                            user=message.user,
                                            target=message.target,
                                            connector=message.connector))


async def update_exp(opsdroid, mxid, room_id, set_exp=None):
    db = opsdroid.get_database("matrix")

    with db.memory_in_room(room_id):
        all_exp = await opsdroid.memory.get("motw_experience") or {}

    if not all_exp or mxid not in all_exp:
        exp = 0
    else:
        exp = all_exp[mxid]

    if set_exp is not None:
        exp = set_exp
    else:
        exp += 1

    all_exp[mxid] = exp

    with db.memory_in_room(room_id):
        await opsdroid.memory.put("motw_experience", all_exp)

    return all_exp


@match_event(MarkExperience)
@memory_in_event_room
async def add_experience(opsdroid, config, experience):
    nick, mxid = await get_nick(config, experience)

    all_exp = await update_exp(opsdroid, mxid, experience.target)
    exp = all_exp[mxid]

    await experience.respond(Message(f"{nick} now has {exp} experience."))

    if exp >= 5:
        await experience.respond(
            Message("You have 5 experience you can level up!")
        )


@match_regex(r"\+experience", case_sensitive=False)
async def mark_experience(opsdroid, config, message):
    exp = MarkExperience(user_id=message.user_id, user=message.user,
                         target=message.target, connector=message.connector)

    return await opsdroid.parse(exp)


@match_regex(r"\!experience", case_sensitive=False)
async def get_experience(opsdroid, config, message):
    nick, mxid = await get_nick(config, message)
    all_exp = await opsdroid.memory.get("motw_experience") or {}

    if not all_exp or mxid not in all_exp:
        await message.respond("You don't have any experience.")
    else:
        await message.respond(f"You have {all_exp[mxid]} experience.")


@match_regex(r"\!levelup", case_sensitive=False)
@memory_in_event_room
async def level_up(opsdroid, config, message):
    nick, mxid = await get_nick(config, message)

    all_exp = await opsdroid.memory.get("motw_experience") or {}

    if not all_exp or mxid not in all_exp:
        exp = 0
    else:
        exp = all_exp[mxid]

    if exp < 5:
        await message.respond("You don't have enough experience to level up.")
        return

    await update_exp(opsdroid, mxid, message.target, set_exp=0)

    await message.respond(Message("You have levelled up 🎉"))
