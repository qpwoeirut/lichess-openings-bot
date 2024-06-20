from __future__ import annotations

import logging
import random
from enum import Enum
from typing import Union, Any

import chess
import yaml
from chess.engine import PlayResult

from lib import lichess, model
from lib.config import load_config
from lib.engine_wrapper import MinimalEngine
from lib.timer import seconds
from lib.types import MOVE

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

        time_left, increment = (time_limit.white_clock, time_limit.white_inc) if board.turn == chess.WHITE else (
            time_limit.black_clock, time_limit.black_inc)
        if time_left is None or time_left > 10 * 1000 or (increment is not None and increment >= 1):
            # check opening explorer if there are at least 10s left or increment
            move = self.pick_weighted_random_opening_explorer_move(board)
            if move is not None:
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

    def pick_weighted_random_opening_explorer_move(self, board: chess.Board) -> Union[None, chess.Move]:
        opening_explorer_move_list = self.get_opening_explorer_move_list(board)
        moves = []
        weights = []
        for possible_move in opening_explorer_move_list:
            games_played = possible_move["white"] + possible_move["black"] + possible_move["draws"]
            moves.append(possible_move["uci"])
            weights.append(games_played)

        if len(moves) == 0:
            return None

        move = random.choices(moves, weights, k=1)[0]
        return move

    def get_opening_explorer_move_list(self, board: chess.Board) -> list[dict[str, Any]]:
        variant = "standard" if board.uci_variant == "chess" else str(board.uci_variant)

        if self.opening_book_player is not None:
            params = {"player": self.opening_book_player, "fen": board.fen(), "moves": 100, "variant": variant,
                      "recentGames": 0, "color": "white" if board.turn == chess.WHITE else "black"}
            response = self.li.online_book_get("https://explorer.lichess.ovh/player", params, stream=True)
            if response["moves"]:
                self.mode = OpeningsBotModeEnum.PLAYER_OPENINGS
                return response["moves"]
            else:  # if there's no moves found, try the general opening explorer next at the player's rating or higher
                params = {"fen": board.fen(), "moves": 100, "variant": variant, "topGames": 0, "recentGames": 0,
                          "ratings": [rating for rating in RATINGS if rating >= self.opening_book_player_rating]}
                response = self.li.online_book_get("https://explorer.lichess.ovh/lichess", params)
                self.mode = OpeningsBotModeEnum.GENERAL_OPENINGS
                return response["moves"]

        params = {"fen": board.fen(), "moves": 100, "variant": variant, "topGames": 0, "recentGames": 0}
        response = self.li.online_book_get("https://explorer.lichess.ovh/lichess", params)
        self.mode = OpeningsBotModeEnum.GENERAL_OPENINGS
        return response["moves"]

    def chat_command(self, game: model.Game, cmd: str) -> str:
        if cmd == "setplayer" or cmd.startswith("setplayer "):
            if game.mode != "casual":
                return "setplayer is only allowed for casual games!"
            parts = cmd.strip().split()
            if len(parts) != 2:
                return "Invalid format! Use \"!setplayer <username>\" to set the opening explorer player."
            else:
                username = parts[1]
                self.opening_book_player = username
                return f"Set opening explorer to \"{username}\". It may take a bit to index all games for this player."
        elif cmd == "unsetplayer":
            self.opening_book_player = None
            return "Using general Lichess opening explorer."
        elif cmd == "mode":
            return f"Currently using {self.mode.value}."
        else:
            return "Command not recognized!"

    def set_opening_book_player(self, game: model.Game, username: str) -> None:
        # send requests to the lichess server to start indexing games for this player
        params = {"player": username, "fen": chess.STARTING_FEN, "moves": 100, "variant": game.variant_name,
                  "recentGames": 0, "color": "white"}
        self.li.online_book_get("https://explorer.lichess.ovh/player", params)

        params["color"] = "black"
        self.li.online_book_get("https://explorer.lichess.ovh/player", params)

        user_data = self.li.get_public_data(username)
        if "perfs" in user_data and game.variant_name in user_data["perfs"] and "rating" in user_data["perfs"][
            game.variant_name]:
            self.opening_book_player_rating = user_data["perfs"][game.variant_name]["rating"]

        self.opening_book_player = username
        # extend abort time so that lichess servers have more time to index
        self.game.ping(seconds(60), seconds(120), seconds(120))
