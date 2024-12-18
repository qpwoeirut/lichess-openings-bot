from __future__ import annotations

import logging
import random
import time
from enum import Enum
from typing import Union, Any, override, cast

import chess
import requests
import yaml
from chess.engine import PlayResult

from lib import lichess, model
from lib.config import load_config
from lib.engine_wrapper import MinimalEngine
from lib.timer import seconds
from lib.types import MOVE, InfoStrDict

# Use this logger variable to print messages to the console or log files.
# logger.info("message") will always print "message" to the console or log file.
# logger.debug("message") will only print "message" if verbose logging is enabled.
logger = logging.getLogger(__name__)

with open("lib/versioning.yml") as version_file:
    versioning_info = yaml.safe_load(version_file)

__version__ = versioning_info["lichess_bot_version"]


class ExampleEngine(MinimalEngine):
    """An example engine that all homemade engines inherit."""

    pass


class OpeningsBotModeEnum(Enum):
    PLAYER_OPENINGS = "Player Opening Explorer"
    GENERAL_OPENINGS = "General Opening Explorer"
    FAIRY_STOCKFISH = "Fairy Stockfish"


RATINGS = [0, 400, 1000, 1200, 1400, 1600, 1800, 2000, 2200, 2500]


class OpeningsBotEngine(ExampleEngine):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        config = load_config("./config.yml")
        max_retries = config.engine.online_moves.max_retries
        self.li = lichess.Lichess(config.token, config.url, __version__, logging.INFO, max_retries)

        self.engine = chess.engine.SimpleEngine.popen_uci(
            ["engines/fairy-stockfish"], timeout=15, debug=False, setpgrp=False)

        self.opening_book_player = None
        self.opening_book_player_rating = 0
        self.mode = OpeningsBotModeEnum.FAIRY_STOCKFISH

    @override
    def search(self, board: chess.Board, time_limit: chess.engine.Limit, ponder: bool, draw_offered: bool,
               root_moves: MOVE) -> chess.engine.PlayResult:
        """
        Tell the engine to search.
        :param board: The current position.
        :param time_limit: Conditions for how long the engine can search (e.g. we have 10 seconds and search up to depth 10).
        :param ponder: Whether the engine can ponder.
        :param draw_offered: Whether the bot was offered a draw.
        :param root_moves: If it is a list, the engine will only play a move that is in `root_moves`.
        :return: The move to play.
        """

        # Pause for a little so that any chat messages are processed before the start of the game.
        if board.halfmove_clock == 0:
            time.sleep(0.5)

        time_left, increment = (time_limit.white_clock, time_limit.white_inc) if board.turn == chess.WHITE else (
            time_limit.black_clock, time_limit.black_inc)
        if time_left is None or time_left > 10 or (increment is not None and increment >= 1):
            # check opening explorer if there are at least 10s left or increment
            move, source = self.pick_weighted_random_opening_explorer_move(board)
            if move is not None:
                self.mode = source
                return PlayResult(move, None)

        result = self.engine.play(board,
                                  self.add_go_commands(time_limit),
                                  info=chess.engine.INFO_ALL,
                                  ponder=ponder,
                                  draw_offered=draw_offered,
                                  root_moves=root_moves if isinstance(root_moves, list) else None)
        # Use null_score to have no effect on draw/resign decisions
        null_score = chess.engine.PovScore(chess.engine.Mate(1), board.turn)
        self.scores.append(result.info.get("score", null_score))
        result = self.offer_draw_or_resign(result, board)

        self.mode = OpeningsBotModeEnum.FAIRY_STOCKFISH
        return result

    @override
    def add_comment(self, move: chess.engine.PlayResult, board: chess.Board) -> None:
        """
        Store the move's comments.

        :param move: The move. Contains the comments in `move.info`.
        :param board: The current position.
        """
        if self.comment_start_index < 0:
            self.comment_start_index = len(board.move_stack)
        move_info: InfoStrDict = cast(InfoStrDict, dict(move.info.copy() if move.info else {}))
        if "pv" in move_info:
            move_info["ponderpv"] = board.variation_san(move.info["pv"])
        if "refutation" in move_info:
            move_info["refutation"] = board.variation_san(move.info["refutation"])
        if "currmove" in move_info:
            move_info["currmove"] = board.san(move.info["currmove"])

        move_info["Source"] = self.mode.value

        self.move_commentary.append(move_info)

    def pick_weighted_random_opening_explorer_move(
            self, board: chess.Board) -> tuple[Union[None, chess.Move], Union[None, OpeningsBotModeEnum]]:
        opening_explorer_move_list, source = self.get_opening_explorer_move_list(board)
        moves = []
        weights = []
        for possible_move in opening_explorer_move_list:
            games_played = possible_move["white"] + possible_move["black"] + possible_move["draws"]
            moves.append(possible_move["uci"])
            weights.append(games_played)

        if len(moves) == 0:
            return None, None

        move = random.choices(moves, weights, k=1)[0]
        return move, source

    def get_opening_explorer_move_list(self, board: chess.Board) -> tuple[list[dict[str, Any]], OpeningsBotModeEnum]:
        variant = "standard" if board.uci_variant == "chess" else str(board.uci_variant)

        if self.opening_book_player is not None:
            params = {"player": self.opening_book_player, "fen": board.fen(), "moves": 100, "variant": variant,
                      "recentGames": 0, "color": "white" if board.turn == chess.WHITE else "black"}
            response = self.li.online_book_get("https://explorer.lichess.ovh/player", params, stream=True)
            if response["moves"]:
                return response["moves"], OpeningsBotModeEnum.PLAYER_OPENINGS
            else:  # if there's no moves found, try the general opening explorer near the player's rating or higher
                # Passing in a list causes duplicate arguments instead of a comma-separated list
                ratings = ','.join([str(rating) for rating in RATINGS if rating + 200 >= self.opening_book_player_rating])
                params = {"fen": board.fen(), "moves": 100, "variant": variant, "topGames": 0, "recentGames": 0,
                          "ratings": ratings}
                response = self.li.online_book_get("https://explorer.lichess.ovh/lichess", params)
                return response["moves"], OpeningsBotModeEnum.GENERAL_OPENINGS
        else:
            # Increase the quality of openings played by the bot.
            # Low-rated players in standard chess usually know much more theory than low-rated players in variants, so
            # standard chess has a lower threshold.
            if variant == "standard":
                ratings = ','.join([str(rating) for rating in RATINGS if rating >= 1600])
            else:
                ratings = ','.join([str(rating) for rating in RATINGS if rating >= 1800])

            # Filter out ultrabullet openings
            speeds = ','.join(["bullet", "blitz", "rapid", "classical", "correspondence"])
            params = {"fen": board.fen(), "moves": 100, "variant": variant, "topGames": 0, "recentGames": 0, "ratings": ratings, "speeds": speeds}
            response = self.li.online_book_get("https://explorer.lichess.ovh/lichess", params)
            return response["moves"], OpeningsBotModeEnum.GENERAL_OPENINGS

    def chat_command(self, game: model.Game, cmd: str) -> str:
        if cmd == "setplayer" or cmd.startswith("setplayer "):
            if game.mode != "casual":
                return "setplayer is only allowed for casual games!"
            parts = cmd.strip().split()
            if len(parts) != 2:
                return "Invalid format! Use \"!setplayer <username>\" to set the opening explorer player."
            else:
                username = parts[1]
                already_indexed = self.set_opening_book_player(game, username)
                if already_indexed:
                    return f"Set opening explorer to \"{username}\"."
                else:
                    return (f"Set opening explorer to \"{username}\".\n"
                            f"It may take a while to index all games for this player.\n"
                            f"If the bot fails to move, ensure the player's openings are already fully indexed.")
        elif cmd == "unsetplayer":
            self.opening_book_player = None
            self.opening_book_player_rating = 0
            return "Using general Lichess opening explorer."
        elif cmd == "mode":
            return f"Currently using {self.mode.value}."
        else:
            return "Command not recognized!"

    def set_opening_book_player(self, game: model.Game, username: str) -> bool:
        self.opening_book_player = username
        user_data = self.li.get_public_data(username)
        variant = game.speed if game.variant_key == "standard" else game.variant_key
        if variant in user_data["perfs"]:
            self.opening_book_player_rating = user_data["perfs"][variant]["rating"]
        else:
            logger.info("%s not found in ratings, likely because user has played no games for this variant", variant)

        # send request to the lichess server to start indexing games for this player
        params = {"player": username, "moves": 100, "variant": game.variant_name, "recentGames": 0, "color": "white"}
        already_indexed = False
        try:
            requests.get("https://explorer.lichess.ovh/player", params=params, timeout=1, stream=True).close()
            already_indexed = True
        except requests.exceptions.Timeout:
            pass

        # extend abort time so that lichess servers have more time to index
        game.ping(seconds(60), seconds(120), seconds(120))

        return already_indexed
