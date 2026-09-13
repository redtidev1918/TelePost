"""Telegram Mini App presentation adapter (auth, sessions, RBAC, API glue).

The Mini App is a UI adapter on top of the canonical TelePost HTTP API. It
never owns business state: every mutation still flows through the same
domain / application / repository layers the Telegram Bot uses.
"""