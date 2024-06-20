# lichess-openings-bot

A bot that plays based on the Lichess openings explorer and then uses Fairy-Stockfish.
For casual games, users can tell the bot to play the openings of a specific Lichess player.
Supports all Lichess variants.

## TODO
* Figure out how to let user start game w/o waiting for 8s
* Make sure the bot doesn't flag so easily

## Setup
Copy `config.yml.default` to `config.yml` and add a Lichess OAuth token to `config.yml`.

Build Fairy-Stockfish (or download a prebuilt binary), name it `fairy-stockfish`, and put it in the `./engines/` directory.

Install Python dependencies using pip or an IDE like PyCharm.

Run `python3 lichess-bot.py`. Add `-u` if you need to upgrade your account to a BOT account.


# Acknowledgements
This bot is forked from the [lichess-bot](https://github.com/lichess-bot-devs/lichess-bot) repository.

A new function called `chat_command` was added to `EngineWrapper` in `engine_wrapper.py` to facilitate chat command responses.

A 8-second `time.sleep` call was added to `lichess-bot.py` to give the user time to update bot settings.

`.gitignore`, `config.yml.default`, `conversation.py`, `README.md`, and `homemade.py` are the only other edited files.
