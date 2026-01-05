from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    DATABASE_URL: str
    CELERY_BROKER_URL: str
    CELERY_RESULT_BACKEND: str
    MINIO_ENDPOINT: str
    MINIO_ACCESS_KEY: str
    MINIO_SECRET_KEY: str
    MINIO_BUCKET_NAME: str = "test-artifacts"
    OPENAI_API_KEY: str = ""
    EXECUTION_ENGINE_URL: str = "http://execution-engine:3000/run"
    BACKEND_CORS_ORIGINS: list[str] = ["*"]

    @property
    def cors_origins(self) -> list[str]:
        return self.BACKEND_CORS_ORIGINS


    class Config:
        env_file = ".env"

settings = Settings()
