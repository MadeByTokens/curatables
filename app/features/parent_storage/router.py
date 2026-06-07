"""Parent storage report — disk usage and per-channel breakdown."""

from fastapi import APIRouter, Depends, Request
from starlette.responses import HTMLResponse

from app.dependencies import require_parent, get_storage_report_service
from app.services.storage_report import StorageReportService
from app.models import ViewerContext

router = APIRouter(prefix="/parent", tags=["parent-storage"])


@router.get("/storage", response_class=HTMLResponse)
def storage_page(request: Request,
                 viewer: ViewerContext = Depends(require_parent),
                 reports: StorageReportService = Depends(get_storage_report_service)):
    report = reports.get_report()
    channels = reports.get_size_by_channel()
    return request.app.state.templates.TemplateResponse(request, "parent/storage.html", {
        "request": request,
        "report": report,
        "channels": channels,
    })
