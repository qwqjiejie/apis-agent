from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    llm_api_key: str = ""
    llm_base_url: str = "https://api.openai.com/v1"
    llm_model: str = "gpt-4o"
    tavily_api_key: str = ""
    server_host: str = "0.0.0.0"
    server_port: int = 8080
    log_level: str = "INFO"
    max_agent_iterations: int = 10
    max_history_rounds: int = 20
    mysql_host: str = "127.0.0.1"
    mysql_port: int = 3306
    mysql_user: str = "root"
    mysql_pass: str = ""
    mysql_db: str = "dodo"

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8", "extra": "ignore"}


settings = Settings()
