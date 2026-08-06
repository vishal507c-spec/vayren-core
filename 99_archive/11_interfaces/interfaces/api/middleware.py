import time
from fastapi import Request


async def logging_middleware(request: Request, call_next):
    start = time.perf_counter()
    response = await call_next(request)
    elapsed = time.perf_counter() - start
    print(f"{request.method} {request.url.path} - {response.status_code} [{elapsed:.3f}s]")
    return response
