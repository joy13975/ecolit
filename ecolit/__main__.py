"""Main entry point for Ecolit application."""

import asyncio
import logging
from typing import NoReturn

from ecolit.config import load_config
from ecolit.core import EcoliteManager

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
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
