"""WebSocket account-origin authorization boundary."""

from channels.db import database_sync_to_async


class CTFAccountWebSocketBoundary:
    """Close every platform socket for temporary CTF accounts."""

    def __init__(self, application):
        self.application = application

    async def __call__(self, scope, receive, send):
        user = scope.get("user")
        if user is not None and await self._is_ctf_account(user):
            await send({"type": "websocket.close", "code": 4403})
            return
        await self.application(scope, receive, send)

    @database_sync_to_async
    def _is_ctf_account(self, user) -> bool:
        from management.services import is_temporary_ctf_account

        return is_temporary_ctf_account(user)
