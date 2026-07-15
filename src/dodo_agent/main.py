import logging
import uvicorn
from src.dodo_agent.config.settings import get_settings

logging.basicConfig(
    level=get_settings().log_level.upper(),
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%H:%M:%S",
)


def main():
    uvicorn.run(
        "src.dodo_agent.api.main:app",
        host=get_settings().server_host,
        port=get_settings().server_port,
        log_level=get_settings().log_level.lower(),
        access_log=False,
    )


if __name__ == "__main__":
    main()
