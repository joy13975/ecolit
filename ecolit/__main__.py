"""Main entry point for Ecolit application."""

import asyncio
import logging
from typing import NoReturn

from ecolit.config import load_config
from ecolit.core import EcoliteManager

class ConditionalFormatter(logging.Formatter):
    def format(self, record):
        if record.levelno >= logging.DEBUG and record.levelno < logging.INFO:
            # Debug level: show module name
            self._style._fmt = "%(asctime)s %(name)s %(message)s"
        else:
            # Info and above: hide module name
            self._style._fmt = "%(asctime)s %(message)s"
        return super().format(record)

logging.basicConfig(
    level=logging.INFO,
    datefmt="%m-%d %H:%M:%S",
    handlers=[
        logging.StreamHandler()
    ]
)

# Apply custom formatter to root logger
root_logger = logging.getLogger()
formatter = ConditionalFormatter(datefmt="%m-%d %H:%M:%S")
for handler in root_logger.handlers:
    handler.setFormatter(formatter)
logger = logging.getLogger(__name__)


async def main() -> NoReturn:
    """Main application loop."""
    logger.info("Starting Ecolit application")

    config = load_config()
    manager = EcoliteManager(config)

    try:
        await manager.start()
        await asyncio.Event().wait()
    except KeyboardInterrupt:
        logger.info("Shutting down Ecolit")
    finally:
        await manager.stop()


if __name__ == "__main__":
    asyncio.run(main())
