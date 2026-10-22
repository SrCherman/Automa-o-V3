"""
Módulo de configuração do projeto.
Carrega variáveis de ambiente e valida usando Pydantic Settings.
"""

import os
from pydantic_settings import BaseSettings, SettingsConfigDict
from functools import lru_cache

ENV_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env")

class Settings(BaseSettings):
    # Mind7
    mind7_url: str
    mind7_username: str
    mind7_password: str

    # Itaú
    itau_url: str
    itau_username: str
    itau_password: str

    model_config = SettingsConfigDict(env_file=ENV_PATH, env_file_encoding="utf-8", extra="ignore")

@lru_cache()
def get_settings() -> Settings:
    """Retorna as configurações do projeto de forma cacheada."""
    return Settings()

# Instância global de settings
settings = get_settings()
