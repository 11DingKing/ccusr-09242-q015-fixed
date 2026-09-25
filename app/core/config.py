from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    API_V1_STR: str = "/api/v1"
    PROJECT_NAME: str = "微专业就业成效追踪系统"
    DATABASE_URL: str = "sqlite:///./gradtrack.db"
    #: 报告链接签名密钥，部署时必须通过环境变量覆盖
    REPORT_LINK_SECRET: str = "dev-only-change-me"

    class Config:
        case_sensitive = True


settings = Settings()
