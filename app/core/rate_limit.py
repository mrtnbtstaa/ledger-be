from slowapi import Limiter
from slowapi.util import get_remote_address
from starlette.requests import Request


def client_ip(request: Request) -> str:
    # shortcut: trusts X-Forwarded-For (Render sets it). The global LLM budget is the backstop.
    forwarded = request.headers.get("x-forwarded-for")
    return forwarded.split(",")[0].strip() if forwarded else get_remote_address(request)


limiter = Limiter(key_func=client_ip)
