Monster of the Week
===================

A very simple bot to help you make MOTW checks.

When playing MOTW you make checks, there are 2d6 +stat rolls, i.e. 2d6+2 where
+2 is your "cool" modifier.

This skill lets you set your modifiers and then make rolls based on your stats,
i.e. you tell the bot `+Cool` and it will roll against your cool modifier.


Adding your Stats
-----------------

You can set your character stats by running sending commands like ``!cool +2`` or
``!tough -1``.
If your Keeper wants to set stats they can do this by a) being the configured
keeper in the config and b) sending ``!cool +2 @<nick>``.

These commands can be chained so sending ``!cool +2 !tough +1`` or as the
Keeper ``!cool +2 !tough +1 @<nick>``.


Viewing your Stats
------------------

Run ``!stats`` to see your current character stats.


Making checks
-------------

To make a check send ``+<stat>``, i.e. ``+Cool`` as the person assigned those
stats.
