import logging

from fastapi import FastAPI

from l_space import __version__
from l_space.api.routes import router
from l_space.core.settings import get_settings


def create_app() -> FastAPI:
    logging.basicConfig(level=get_settings().log_level)
    app = FastAPI(title="l-space", version=__version__)
    app.include_router(router)
    return app


app = create_app()
