import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from l_space import __version__
from l_space.api.routes import router
from l_space.core.settings import get_settings
from l_space.library.migrate import prepare_database


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    prepare_database(get_settings())
    yield


def create_app() -> FastAPI:
    logging.basicConfig(level=get_settings().log_level)
    app = FastAPI(title="l-space", version=__version__, lifespan=lifespan)
    app.include_router(router)
    return app


app = create_app()
