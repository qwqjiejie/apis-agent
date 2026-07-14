import logging
import uvicorn
from src.dodo_agent.config.settings import settings

logging.basicConfig(
    level=settings.log_level.upper(),
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%H:%M:%S",
)


def main():
    uvicorn.run(
        "src.dodo_agent.api.main:app",
        host=settings.server_host,
        port=settings.server_port,
        log_level=settings.log_level.lower(),
        access_log=False,
    )


if __name__ == "__main__":
    main()
