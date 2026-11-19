"""Production routes for the settings smoke, with no external service and no background task started.

As in ``browser_app``, the scheduler, the alert monitor and the model are never started (例行任务 are
refused with the scheduler's own 409). Two services the settings page reads are started as the real app
starts them: the research workbench (模型 › 研究工作台的模型连接) and, outside demo mode, the assistant
service, built without a model (nothing is ever sent to one) and keeping its memory in
``SETTINGS_SMOKE_MEMORY``, a file in the smoke's temporary directory; 交易偏好 edits it through
``/api/memory`` and confirmed lesson proposals write through it. Demo mode has no assistant service, as in
the real app, so its memory is unavailable there.
"""
from contextlib import asynccontextmanager
import os

from backend import main
from backend.main import app


@asynccontextmanager
async def fixture_lifespan(app):
    from backend.services.agent_service import AgentService
    from core import lessons
    from core.lab_service import LabService
    from core.memory import MemoryManager

    demo = os.environ.get("TRACKER_DEMO_MODE") == "1"
    app.state.lab_service = LabService(demo=demo)
    await app.state.lab_service.start()
    if not demo:
        service = AgentService()
        service.memory_manager = MemoryManager(data_file=os.environ['SETTINGS_SMOKE_MEMORY'])
        lessons.use_memory_manager(lambda: service.memory_manager)
        main.agent_service = service
    try:
        yield
    finally:
        main.agent_service = None
        lessons.use_memory_manager(None)
        await app.state.lab_service.stop()


app.router.lifespan_context = fixture_lifespan
