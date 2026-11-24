"""`python -m ai_council` でサーバーを起動する。"""

import uvicorn

from .config import load_settings


def main() -> None:
    settings = load_settings()
    print(f"AI合議室を起動します：http://{settings.host}:{settings.port}")
    uvicorn.run("ai_council.main:app", host=settings.host, port=settings.port)


if __name__ == "__main__":
    main()
