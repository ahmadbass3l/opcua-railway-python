from pydantic_settings import BaseSettings
from typing import List


class Settings(BaseSettings):
    opcua_endpoint: str = "opc.tcp://localhost:4840"
    opcua_node_ids: str = "ns=2;i=1001,ns=2;i=1002,ns=2;i=1003"
    opcua_interval_ms: int = 500
    db_dsn: str = "postgresql://railway:railway@localhost:5432/railway"
    port: int = 8080

    @property
    def node_id_list(self) -> List[str]:
        return [n.strip() for n in self.opcua_node_ids.split(",") if n.strip()]

    class Config:
        env_file = ".env"


settings = Settings()
