"""Issue and check expiring invitations for the isolated trial."""

import argparse
from hashlib import sha256
from pathlib import Path
import re
import secrets
from time import time


def issue_invite(directory: Path, hours: int) -> tuple[str, str]:
    if not 1 <= hours <= 168:
        raise ValueError("有效期须为 1–168 小时")
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    token = secrets.token_urlsafe(32)
    invite_id = sha256(token.encode()).hexdigest()
    with (directory / invite_id).open("x", encoding="ascii") as entry:
        entry.write(str(int(time()) + hours * 3600))
    return token, invite_id


def valid_invite(directory: Path, token: str) -> str | None:
    if not re.fullmatch(r"[A-Za-z0-9_-]{43}", token):
        return None
    invite_id = sha256(token.encode()).hexdigest()
    try:
        with (directory / invite_id).open(encoding="ascii") as entry:
            expires_at = int(entry.read(32))
    except (OSError, ValueError):
        return None
    return invite_id if time() < expires_at else None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    actions = parser.add_subparsers(dest="action", required=True)
    create = actions.add_parser("issue")
    create.add_argument("--hours", type=int, default=48)
    create.add_argument("--origin", required=True)
    actions.add_parser("revoke").add_argument("invite_id")
    args = parser.parse_args()
    if args.action == "revoke":
        if not re.fullmatch(r"[a-f0-9]{64}", args.invite_id):
            parser.error("无效的邀请编号")
        (args.directory / args.invite_id).unlink(missing_ok=True)
        return
    token, invite_id = issue_invite(args.directory, args.hours)
    print(f"{args.origin.rstrip('/')}/trial/#invite={token}")
    print(f"撤销用编号 {invite_id}")


if __name__ == "__main__":
    main()
