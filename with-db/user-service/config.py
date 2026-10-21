import os

class Config:
    DB_HOST = os.getenv("DB_HOST", "127.0.0.1")
    # DB_HOST = os.getenv("DB_HOST", "media-mysql")
    DB_PORT = int(os.getenv("DB_PORT", "3306"))
    DB_NAME = os.getenv("DB_NAME", "media_tools")
    DB_USER = os.getenv("DB_USER", "media_user")
    DB_PASSWORD = os.getenv("DB_PASSWORD", "")

