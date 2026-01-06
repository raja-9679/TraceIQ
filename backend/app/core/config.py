from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    DATABASE_URL: str
    CELERY_BROKER_URL: str
    CELERY_RESULT_BACKEND: str
    MINIO_ENDPOINT: str
    MINIO_PUBLIC_URL: str = "http://localhost:9000"
    MINIO_ACCESS_KEY: str
    MINIO_SECRET_KEY: str
    MINIO_BUCKET_NAME: str = "test-artifacts"
    OPENAI_API_KEY: str = ""
    EXECUTION_ENGINE_URL: str = "http://execution-engine:3000/run"
    BACKEND_CORS_ORIGINS: list[str] = ["*"]
    
    # Security
    SECRET_KEY: str
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30 # 30 minutes

    @property
    def cors_origins(self) -> list[str]:
        return self.BACKEND_CORS_ORIGINS


    class Config:
        env_file = ".env"

settings = Settings()
