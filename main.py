from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.core import settings, init_db
from app.core.security import ScopeViolation, audit_scope_violation
from app.api import api_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(
    title=settings.PROJECT_NAME,
    description="微专业就业成效追踪系统后端API",
    version="1.0.0",
    lifespan=lifespan
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(ScopeViolation)
async def scope_violation_handler(request: Request, exc: ScopeViolation):
    """越权统一出口：先记录安全审计，再返回不含任何统计数字的 403。"""
    audit_scope_violation(request, exc)
    return JSONResponse(
        status_code=403,
        content={"detail": "无权访问该范围的数据"},
    )


@app.middleware("http")
async def no_store_sensitive_data(request: Request, call_next):
    """所有业务数据响应禁止缓存，避免不同范围调用方共享缓存中的敏感数字。"""
    response = await call_next(request)
    if request.url.path.startswith(settings.API_V1_STR):
        response.headers["Cache-Control"] = "no-store"
        response.headers["Pragma"] = "no-cache"
    return response


app.include_router(api_router, prefix=settings.API_V1_STR)


@app.get("/", tags=["系统"])
def root():
    return {
        "name": settings.PROJECT_NAME,
        "version": "1.0.0",
        "docs": "/docs",
        "api_prefix": settings.API_V1_STR,
        "init_data_script": "python scripts/init_data.py"
    }


@app.get("/health", tags=["系统"])
def health_check():
    return {"status": "healthy"}
