from __future__ import annotations
import logging
import os

from click import style

from src.epomakercontroller.configs.constants import LOG_FOLDER


class Logger:
    _G_LOGGER = logging.getLogger("EpomakerController")
    logging.basicConfig(
        level=logging.INFO,
        format="[%(asctime)s] %(levelname)8s: %(message)s",
        handlers=[
            logging.FileHandler(os.path.join(LOG_FOLDER, "epomakercontroller.log")),
            logging.StreamHandler()
        ]
    )

    @staticmethod
    def log(level: int, message: str):
        # TODO: Refactor to logging library w/ custom format + file writing
        Logger._G_LOGGER.log(level, message)

    @staticmethod
    def log_info(message: str):
        Logger.log(logging.INFO, message)

    @staticmethod
    def log_warning(message: str):
        Logger.log(logging.WARNING, message)

    @staticmethod
    def log_error(message: str):
        Logger.log(logging.ERROR, message)
