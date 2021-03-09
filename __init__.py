import regex
from textwrap import dedent

from opsdroid.database.matrix import memory_in_event_room
from opsdroid.events import (Event, JoinRoom, Message, OpsdroidStarted,
                             UserInvite)
from opsdroid.matchers import match_event, match_regex
from opsdroid.parsers.regex import match_regex as parse_regex
from opsdroid.const import REGEX_PARSE_SCORE_FACTOR

from nio.responses import JoinedMembersError
from random import randint

import logging

_LOGGER = logging.getLogger(__name__)

################################################################################
# Helper functions
################################################################################

async def get_stat_names(opsdroid, room):
    with opsdroid.get_database('matrix').memory_in_room(room):
        return await opsdroid.memory.get("pbta_stat_names", [])


def html_list(sequence):
    html_stats = ''.join([f"<li>{s.capitalize()}</li>" for s in sequence])
    return f"<ul>{html_stats}</ul>"


async def get_mxid(nick, room, connector):
    if nick is None:
        return

    members = await connector.connection.joined_members(
        connector.lookup_target(room)
    )
    if isinstance(members, JoinedMembersError):
        return None

    members = {member.display_name: member.user_id for member in members.members}
    return members.get(nick, None)


async def get_nick(config, message):
    nick = message.user
    mxid = message.user_id
    if message.user_id == config.get('keeper', None):
        message_nick = message.entities.get('nick', {'value': ''})['value']
        if message_nick:
            nick = message_nick.strip().strip('@')
            mxid = await get_mxid(nick, message.target, message.connector)
            if not mxid:
                await message.respond(
                    f"Could not find the user {nick} in the room."
                )
                return message.user, message.user_id
    return nick, mxid


def pretty_stats(stats):
    pretty_stats = []
    for name, stat in stats.items():
        stat_sign = "-" if stat < 0 else "+"
        pretty_stats.append(f"{name.capitalize()} {stat_sign}{abs(stat)}")
    return ", ".join(pretty_stats)


def two_d6():
    return randint(1, 6), randint(1, 6)


async def update_exp(opsdroid, mxid, room_id, set_exp=None):
    db = opsdroid.get_database("matrix")

    with db.memory_in_room(room_id):
        all_exp = await opsdroid.memory.get("pbta_experience") or {}

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
        await opsdroid.memory.put("pbta_experience", all_exp)

    return all_exp


################################################################################
# Core Parsing
################################################################################


class MarkExperience(Event):
    _no_register = True


class StatSetCommand(Event):
    _no_register = True


@match_event(OpsdroidStarted)
async def migrate_old_keys(opsdroid, config, event):
    db = opsdroid.get_database("matrix")
    for room in opsdroid.get_connector('matrix').connection.rooms.values():
        with db.memory_in_room(room.room_id):
            old_key = await opsdroid.memory.get("motw_stats")
            if old_key:
                await opsdroid.memory.put("pbta_stats", old_key)
                await opsdroid.memory.delete("motw_stats")
                await opsdroid.memory.put("pbta_stat_names", GAME_STATS["motw"])
            old_exp_key = await opsdroid.memory.get("motw_experience")
            if old_exp_key:
                await opsdroid.memory.put("pbta_experience", old_exp_key)
                await opsdroid.memory.delete("motw_experience")


@match_event(UserInvite)
async def respond_to_invites(opsdroid, config, invite):
    if config.get('autoinvite', False):
        return await invite.respond(JoinRoom())


MODIFIER_REGEX = "[+-]?[0,1,2,3]"
GAME_STATS = {"motw": ["cool", "tough", "sharp", "charm", "weird"],
              "pbtastartrek": ["aggressive", "bold", "talk", "tech", "morale", "shields"]}
STAT_REGEXES = {}


class CommandRegister:
    command_words = []
    dispatcher = {}

    def add(self, *words, regex=None, parse_nick=False):
        if regex is not None and parse_nick:
            raise ValueError("Can't handle generic secondary regex and nick extraction.")
        def decorator(func):
            func.second_regex = regex
            func.parse_nick = parse_nick
            for word in words:
                self.dispatcher[word] = func
            return func
        return decorator

    def __contains__(self, key):
        return key in self.dispatcher

    def __call__(self, word):
        return self.dispatcher[word]


command_words = CommandRegister()


@match_regex(r"!(?<word>\w*).*", case_sensitive=False)
async def match_command_messages(opsdroid, config, message):
    """
    This is the main word of Command dispatcher.

    It matches all messages starting with `!word` and then calls the
    appropriate skill.
    """
    _LOGGER.debug("word of Command has been spoken")
    word = message.entities['word']['value']
    # Handle words of Command
    if word in command_words:
        skill_func = command_words(word)

        # We now pretend to be the match_regex matcher for the secondary regex
        opts = {"matching_condition": "match",
                "case_sensitive": False,
                "score_factor": REGEX_PARSE_SCORE_FACTOR}

        if skill_func.parse_nick:
            opts["expression"] = rf"!{word}" + r"(?:\s@\s?(?P<nick>.*))?"

        if skill_func.second_regex:
            opts["expression"] = rf"!{word}" + skill_func.second_regex

        if "expression" in opts:
            matched_regex = await parse_regex(message.text, opts)
            if matched_regex:
                message.regex = matched_regex
                for regroup, value in matched_regex.groupdict().items():
                    message.update_entity(regroup, value, None)

        if skill_func.parse_nick:
            nick, mxid = await get_nick(config, message)
            message.update_entity("nick", nick, None)
            message.update_entity("mxid", mxid, None)

        await skill_func(opsdroid, config, message)
        return

    # Handle stats
    if word in await get_stat_names(opsdroid, message.target):
        await opsdroid.parse(
            StatSetCommand(
                target=message.target,
                connector=message.connector,
                linked_event=message,
            )
        )
        return

    await message.respond(f"You have no power here - !{word}")


################################################################################
# bang command handles
################################################################################


@command_words.add("setgame", regex=r"\s(?P<gamename>.*)")
@memory_in_event_room
async def set_game(opsdroid, config, message):
    game = message.entities['gamename']["value"]
    if game not in GAME_STATS.keys():
        message.respond(f"I don't know how to play that. Available options are: {', '.join(GAME_STATS.keys())}")
        return

    if await opsdroid.memory.get("pbta_stat_names") and await opsdroid.memory.get("pbta_stats"):
        await message.respond(Message("You already have a game in progress, not setting a new one."))

    await opsdroid.memory.put("pbta_stat_names", GAME_STATS[game])

    await message.respond(f"Set the game to be: {game}")


@command_words.add("help")
async def help_message(opsdroid, config, message):
    stats_message = ""
    stats = await get_stat_names(opsdroid, message.target)
    if stats:
        stats_message = dedent(f"""\
            <h1>
            Making Checks
            </h1>
            {html_list(stats)}
            <p>
            You can roll against these stats by typing <code>+stat</code>, i.e. <code>+{stats[4]}</code>.
            You can append a single modifier on a roll by doing <code>+stat +x</code>, i.e. <code>+{stats[4]} -1</code>.
            </p>
            <p>
            You can set your stats with <code>!stat number</code>, i.e. <code>!{stats[3]} +1</code> you can
            set as many stats as you like in one command, i.e.
            <code>!{stats[1]} +1 !{stats[2]} +1 !{stats[3]} -1</code>.
            </p>
            <p>
            You can retrieve your characters stats with <code>!stats</code>.
            </p>\
        """)
    await message.respond(dedent(f"""\
        <p>
        This bot makes checks against your stats, and tracks your experience.
        </p>
        {stats_message}
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
        <code>/myroomnick</code> in Element if you desire.
        </p>\
    """))


@match_event(StatSetCommand)
@memory_in_event_room
async def set_stats(opsdroid, config, event):
    message = event.linked_event

    split = message.text.split('@')
    if len(split) == 1:
        split.append(None)

    stats, nick = split

    if nick is None:
        nick = message.user
        mxid = message.user_id
    else:
        nick = nick.strip()
        mxid = await get_mxid(nick, message.target, message.connector)

    stats = [s.strip() for s in stats.split("!") if s]
    stats = tuple(s.split(' ') for s in stats)
    stats = dict((s[0].lower(), int(s[1])) for s in stats)

    stat_names = set(await get_stat_names(opsdroid, message.target))
    unknown_stats = set(stats).difference(stat_names)
    if unknown_stats:
        await message.respond(f"Could not set stats: {', '.join(unknown_stats)}."
                              f" They are not valid for this game, valid stats are: {', '.join(stat_names)}")
        return

    all_stats = await opsdroid.memory.get("pbta_stats") or {}
    if not all_stats or mxid not in all_stats:
        existing_stats = {}
    else:
        existing_stats = all_stats[mxid]

    stats = {**existing_stats, **stats}

    await message.respond(f"Setting stats for {nick}: {pretty_stats(stats)}")

    new_stats = {**all_stats, **{mxid: stats}}
    await opsdroid.memory.put("pbta_stats", new_stats)


@command_words.add("stats", parse_nick=True)
@memory_in_event_room
async def print_stats(opsdroid, config, message):
    nick = message.entities["nick"]["value"]
    mxid = message.entities["mxid"]["value"]

    all_stats = await opsdroid.memory.get("pbta_stats")
    if not all_stats or mxid not in all_stats:
        await message.respond(rf"No stats found for {nick}, run '!<stat> +number'")
        return

    stats = all_stats[mxid]
    await message.respond(f"Stats for {nick}: {pretty_stats(stats)}")


@command_words.add("experience", parse_nick=True)
@memory_in_event_room
async def get_experience(opsdroid, config, message):
    nick = message.entities["nick"]["value"]
    mxid = message.entities["mxid"]["value"]
    all_exp = await opsdroid.memory.get("pbta_experience") or {}

    if not all_exp or mxid not in all_exp:
        all_exp[mxid] = 0

    await message.respond(f"{nick} has {all_exp[mxid]} experience.")


@command_words.add("levelup", parse_nick=True)
@memory_in_event_room
async def level_up(opsdroid, config, message):
    nick = message.entities["nick"]["value"]
    mxid = message.entities["mxid"]["value"]

    all_exp = await opsdroid.memory.get("pbta_experience") or {}

    if not all_exp or mxid not in all_exp:
        exp = 0
    else:
        exp = all_exp[mxid]

    if exp < 5:
        await message.respond(f"{nick} does not have enough experience to level up.")
        return

    await update_exp(opsdroid, mxid, message.target, set_exp=exp - 5)

    await message.respond(Message(f"{nick} has levelled up 🎉"))


################################################################################
# plus command handles
################################################################################


@match_event(MarkExperience)
@memory_in_event_room
async def add_experience(opsdroid, config, experience):
    all_exp = await update_exp(opsdroid, experience.user_id, experience.target)
    exp = all_exp[experience.user_id]

    await experience.respond(
        Message(f"{experience.user} now has {exp} experience."))

    if exp >= 5:
        await experience.respond(
            Message("You have 5 experience you can level up!")
        )


@match_regex(r"\+experience(\s@\s?(?P<nick>.*))?", case_sensitive=False)
async def mark_experience(opsdroid, config, message):
    nick, mxid = await get_nick(config, message)
    exp = MarkExperience(user_id=mxid, user=nick,
                         target=message.target, connector=message.connector)

    return await opsdroid.parse(exp)


@match_regex(rf"(?!\+experience)\+(?P<stat>\w*)(?P<modifier>\s?{MODIFIER_REGEX})?(\s@\s?(?P<nick>.*))?", case_sensitive=False)
@memory_in_event_room
async def roll(opsdroid, config, message):
    nick, mxid = await get_nick(config, message)

    stat = message.regex.capturesdict()['stat'][0]
    gamestats = await get_stat_names(opsdroid, message.target)
    if stat not in gamestats:
        return

    modifier = message.regex.groupdict()['modifier'] or 0
    modifier = int(modifier)

    all_stats = await opsdroid.memory.get("pbta_stats")

    if not all_stats or mxid not in all_stats:
        await message.respond(rf"No stats found for {nick}, run '!{stat} +number'")
        return

    stats = all_stats[mxid]

    if stat not in stats:
        await message.respond(
            rf"{nick} has not set {stat}, run '!{stat} +number'"
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
        f'<a href="https://matrix.to/#/{mxid}">{nick}</a> rolled {equation} = {number_result} ({result})'
    )

    if number_result <= 6:
        await opsdroid.parse(MarkExperience(user_id=mxid,
                                            user=nick,
                                            target=message.target,
                                            connector=message.connector))
